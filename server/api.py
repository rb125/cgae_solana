"""
CGAE Live Economy Server

Runs the LiveSimulationRunner in a background thread and exposes
real-time state via WebSocket + REST endpoints for the dashboard.

Usage:
    python -m server.api                     # default 20 rounds
    python -m server.api --rounds 50
    python -m server.api --rounds -1         # infinite
"""

import argparse
import asyncio
import json
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

app = FastAPI(title="CGAE Live Economy")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

logger = logging.getLogger("cgae.api")

# Shared state — written by runner thread, read by API handlers
_state: dict = {
    "status": "idle",       # idle | setup | running | done
    "round": 0,
    "total_rounds": 0,
    "economy": None,        # snapshot per round
    "agents": {},           # agent_id -> details
    "trades": [],           # last N trade results
    "events": [],           # protocol events
    "time_series": {"safety": [], "balance": [], "rewards": [], "penalties": []},
}
_state_lock = threading.Lock()
_ws_clients: set[WebSocket] = set()
_broadcast_loop: asyncio.AbstractEventLoop | None = None

MAX_TRADES = 500  # keep last N trades in memory
MAX_WS_ITEMS = 200


# ---------------------------------------------------------------------------
# Background runner
# ---------------------------------------------------------------------------

def _run_economy(num_rounds: int, initial_balance: float):
    """Run the live simulation in a background thread."""
    import sys, os
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

    from server.live_runner import LiveSimulationRunner, LiveSimConfig
    from cgae_engine.gate import RobustnessVector

    config = LiveSimConfig(
        num_rounds=num_rounds,
        initial_balance=initial_balance,
        run_live_audit=False,
        self_verify=True,
        max_retries=1,
        test_sol_top_up_threshold=0.05,
        test_sol_top_up_amount=0.3,
    )

    runner = LiveSimulationRunner(config)

    with _state_lock:
        _state["status"] = "setup"
        _state["total_rounds"] = num_rounds

    runner.setup()

    with _state_lock:
        _state["status"] = "running"

    # Monkey-patch _emit_protocol_event to push events to our state
    original_emit = runner._emit_protocol_event

    def patched_emit(event_type, agent, message, **extra):
        original_emit(event_type, agent, message, **extra)
        evt = {
            "timestamp": runner.economy.current_time,
            "type": event_type,
            "agent": agent,
            "message": message,
            **extra,
        }
        with _state_lock:
            _state["events"].append(evt)
            if len(_state["events"]) > 1000:
                _state["events"] = _state["events"][-500:]

    runner._emit_protocol_event = patched_emit

    # Run rounds manually so we can push state after each
    round_num = 0
    infinite = num_rounds == -1

    try:
        while infinite or round_num < num_rounds:
            runner._reactivate_suspended_agents()
            round_results = runner._run_round(
                round_num,
                trade_callback=lambda task_result, _round_data: _publish_trade_update(
                    runner, round_num, task_result
                ),
            )
            runner._round_summaries.append(round_results)
            step_events = runner.economy.step()

            # Build snapshot
            safety = runner.economy.aggregate_safety()
            agents_snapshot = _build_agents_snapshot(runner)

            with _state_lock:
                _state["round"] = round_num + 1
                _state["economy"] = _build_economy_snapshot(runner, agents_snapshot, safety=safety)
                _state["agents"] = agents_snapshot
                _state["time_series"]["safety"].append(safety)
                _state["time_series"]["balance"].append(_state["economy"]["total_balance"])
                _state["time_series"]["rewards"].append(round_results.get("total_reward", 0))
                _state["time_series"]["penalties"].append(round_results.get("total_penalty", 0))

            # Notify WebSocket clients
            _broadcast_sync()

            round_num += 1

    except Exception as e:
        logger.exception(f"Economy runner failed: {e}")
    finally:
        with _state_lock:
            _state["status"] = "done"
        _broadcast_sync()


def _get_strategy(runner, model_name: str) -> str:
    auto = runner.autonomous_agents.get(model_name)
    if auto is None:
        return "unknown"
    cls = type(auto.strategy).__name__
    return cls.replace("Strategy", "").lower()


def _build_agents_snapshot(runner) -> dict[str, dict]:
    agents_snapshot = {}
    for aid, mname in runner.agent_model_map.items():
        rec = runner.economy.registry.get_agent(aid)
        if not rec:
            continue
        r = rec.current_robustness
        agents_snapshot[aid] = {
            "agent_id": aid,
            "model_name": mname,
            "strategy": _get_strategy(runner, mname),
            "current_tier": rec.current_tier.value,
            "balance": rec.balance,
            "total_earned": rec.total_earned,
            "total_penalties": rec.total_penalties,
            "contracts_completed": rec.contracts_completed,
            "contracts_failed": rec.contracts_failed,
            "status": rec.status.value,
            "robustness": {
                "cc": r.cc, "er": r.er, "as_": r.as_, "ih": r.ih,
            } if r else None,
        }
    return agents_snapshot


def _build_economy_snapshot(runner, agents_snapshot: dict[str, dict], *, safety: float | None = None) -> dict:
    return {
        "aggregate_safety": runner.economy.aggregate_safety() if safety is None else safety,
        "active_agents": len(runner.economy.registry.active_agents),
        "total_balance": sum(a["balance"] for a in agents_snapshot.values()),
        "total_earned": sum(a["total_earned"] for a in agents_snapshot.values()),
        "contracts_completed": sum(a["contracts_completed"] for a in agents_snapshot.values()),
        "contracts_failed": sum(a["contracts_failed"] for a in agents_snapshot.values()),
    }


def _serialize_trade(round_num: int, task_result: dict) -> dict:
    verification = task_result.get("verification") or {}
    settlement = task_result.get("settlement") or {}
    return {
        "round": round_num,
        "agent": task_result["agent"],
        "task_id": task_result["task_id"],
        "task_prompt": task_result.get("task_prompt", ""),
        "tier": task_result["tier"],
        "domain": task_result["domain"],
        "passed": verification.get("overall_pass", False),
        "reward": settlement.get("reward", 0),
        "penalty": settlement.get("penalty", 0),
        "token_cost": task_result["token_cost_sol"],
        "latency_ms": task_result["latency_ms"],
        "output_preview": task_result["output_preview"],
        "constraints_passed": verification.get("constraints_passed", []),
        "constraints_failed": verification.get("constraints_failed", []),
    }


def _publish_trade_update(runner, round_num: int, task_result: dict):
    agents_snapshot = _build_agents_snapshot(runner)
    with _state_lock:
        _state["round"] = round_num + 1
        _state["economy"] = _build_economy_snapshot(runner, agents_snapshot)
        _state["agents"] = agents_snapshot
        _state["trades"] = (_state["trades"] + [_serialize_trade(round_num, task_result)])[-MAX_TRADES:]
    _broadcast_sync()


def _current_broadcast_payload() -> dict:
    with _state_lock:
        return {
            "status": _state["status"],
            "round": _state["round"],
            "total_rounds": _state["total_rounds"],
            "economy": _state["economy"],
            "agents": list(_state["agents"].values()),
            "trades": _state["trades"][-MAX_WS_ITEMS:],
            "events": _state["events"][-MAX_WS_ITEMS:],
        }


def register_broadcast_loop(loop: asyncio.AbstractEventLoop | None = None):
    """Capture uvicorn's event loop so other threads can publish WS updates."""
    global _broadcast_loop
    _broadcast_loop = loop or asyncio.get_running_loop()


def _broadcast_sync():
    """Schedule WS broadcast from the runner thread."""
    try:
        loop = _broadcast_loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(_broadcast(), loop)
    except RuntimeError:
        pass


def broadcast_sync():
    """Public helper for manual demo runners to trigger WS push."""
    _broadcast_sync()


async def _broadcast():
    """Push current state to all connected WebSocket clients."""
    msg = json.dumps(_current_broadcast_payload())
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    _ws_clients -= dead


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/state")
def get_state():
    with _state_lock:
        return {
            "status": _state["status"],
            "round": _state["round"],
            "total_rounds": _state["total_rounds"],
            "economy": _state["economy"],
        }


@app.get("/api/agents")
def get_agents():
    with _state_lock:
        return {"agents": list(_state["agents"].values())}


@app.get("/api/trades")
def get_trades(limit: int = 100):
    with _state_lock:
        return {"trades": _state["trades"][-limit:]}


@app.get("/api/events")
def get_events(limit: int = 100):
    with _state_lock:
        return {"events": _state["events"][-limit:]}


@app.get("/api/timeseries")
def get_timeseries():
    with _state_lock:
        return _state["time_series"]


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    register_broadcast_loop()
    await ws.accept()
    _ws_clients.add(ws)
    try:
        # Send current state immediately
        await ws.send_text(json.dumps(_current_broadcast_payload()))
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

_runner_thread: threading.Thread | None = None


def start_economy(rounds: int = 20, balance: float = 0.5):
    global _runner_thread
    if _runner_thread and _runner_thread.is_alive():
        return
    _runner_thread = threading.Thread(
        target=_run_economy, args=(rounds, balance), daemon=True
    )
    _runner_thread.start()


@app.on_event("startup")
async def on_startup():
    register_broadcast_loop()
    import sys
    # Parse CLI args for rounds
    rounds = 20
    for i, arg in enumerate(sys.argv):
        if arg == "--rounds" and i + 1 < len(sys.argv):
            rounds = int(sys.argv[i + 1])
    start_economy(rounds=rounds)


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=args.port)
