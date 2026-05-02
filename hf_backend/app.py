"""
HuggingFace Space backend for CGAE.
Runs the live economy runner and serves results via FastAPI.
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

RESULTS_DIR = Path(os.environ.get("CGAE_OUTPUT_DIR", "/app/results"))
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="CGAE Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

_runner_started = False
_runner_lock = threading.Lock()


def _start_runner():
    global _runner_started
    with _runner_lock:
        if _runner_started:
            return
        _runner_started = True

    from server.live_runner import LiveSimulationRunner, LiveSimConfig

    config = LiveSimConfig(
        num_rounds=-1,
        output_dir=str(RESULTS_DIR),
        live_audit_cache_dir=str(Path(__file__).parent.parent / "server/live_results/audit_cache"),
        run_live_audit=False,
        seed=42,
        video_demo=True,
        failure_visibility_mode=True,
        failure_task_bias=1.0,
        initial_balance=5.0,
        test_sol_top_up_threshold=2.0,  # Top up earlier (was 1.0) to prevent insolvency spirals
        test_sol_top_up_amount=5.0,
        ih_threshold=0.35,              # Lowered from 0.45 — default ih scores ~0.49, need margin
    )
    runner = LiveSimulationRunner(config)
    runner.run()


@app.on_event("startup")
def startup():
    # Write bootstrap files so dashboard has something to show immediately
    bootstrap = {
        "economy_state.json": {},
        "agent_details.json": {},
        "task_results.json": [],
        "protocol_events.json": [],
        "round_summaries.json": [],
        "final_summary.json": {"economy": {}, "agents": [], "safety_trajectory": []},
    }
    for name, payload in bootstrap.items():
        p = RESULTS_DIR / name
        if not p.exists():
            p.write_text(json.dumps(payload))

    t = threading.Thread(target=_start_runner, daemon=True, name="cgae-runner")
    t.start()


@app.get("/api/state")
def get_api_state():
    """Endpoint for dashboard-ui state."""
    path = RESULTS_DIR / "economy_state.json"
    if not path.exists():
        return {"status": "starting", "round": 0, "total_rounds": 0, "economy": None}
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "status": "running",
            "round": data.get("current_time", 0),
            "total_rounds": -1,
            "economy": data
        }
    except Exception:
        return {"status": "error", "round": 0, "total_rounds": 0, "economy": None}


@app.get("/api/agents")
def get_api_agents():
    """Endpoint for dashboard-ui agents."""
    path = RESULTS_DIR / "agent_details.json"
    if not path.exists():
        return {"agents": []}
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        agents = []
        for model_name, details in data.items():
            agents.append({
                "agent_id": details.get("agent_id", "unknown"),
                "model_name": model_name,
                "strategy": details.get("strategy", "unknown"),
                "current_tier": details.get("current_tier", 0),
                "balance": details.get("balance", 0.0),
                "total_earned": details.get("total_earned", 0.0),
                "total_penalties": details.get("total_penalties", 0.0),
                "contracts_completed": details.get("contracts_completed", 0),
                "contracts_failed": details.get("contracts_failed", 0),
                "status": details.get("status", "active"),
                "robustness": details.get("robustness"),
            })
        return {"agents": agents}
    except Exception:
        return {"agents": []}


@app.get("/api/trades")
def get_api_trades(limit: int = 100):
    """Endpoint for dashboard-ui trades."""
    path = RESULTS_DIR / "task_results.json"
    if not path.exists():
        return {"trades": []}
    
    try:
        results = json.loads(path.read_text(encoding="utf-8"))
        trades = []
        for r in results[-limit:]:
            v = r.get("verification", {})
            s = r.get("settlement", {})
            trades.append({
                "round": r.get("round", 0),
                "agent": r.get("agent", "unknown"),
                "task_id": r.get("task_id", "unknown"),
                "task_prompt": r.get("task_prompt", ""),
                "tier": r.get("tier", "T0"),
                "domain": r.get("domain", "unknown"),
                "passed": v.get("overall_pass", False),
                "reward": s.get("reward", 0.0),
                "penalty": s.get("penalty", 0.0),
                "token_cost": r.get("token_cost_sol", 0.0),
                "latency_ms": r.get("latency_ms", 0.0),
                "output_preview": r.get("output_preview", ""),
                "constraints_passed": v.get("constraints_passed", []),
                "constraints_failed": v.get("constraints_failed", []),
            })
        return {"trades": trades[::-1]}
    except Exception:
        return {"trades": []}


@app.get("/api/events")
def get_api_events(limit: int = 100):
    """Endpoint for dashboard-ui events."""
    path = RESULTS_DIR / "protocol_events.json"
    if not path.exists():
        return {"events": []}
    
    try:
        events = json.loads(path.read_text(encoding="utf-8"))
        return {"events": events[-limit:]}
    except Exception:
        return {"events": []}


@app.get("/api/timeseries")
def get_api_timeseries():
    """Endpoint for dashboard-ui timeseries."""
    path = RESULTS_DIR / "final_summary.json"
    if not path.exists():
        return {"safety": [], "balance": [], "rewards": [], "penalties": []}
    
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
        return {
            "safety": summary.get("safety_trajectory", []),
            "balance": [],
            "rewards": [],
            "penalties": []
        }
    except Exception:
        return {"safety": [], "balance": [], "rewards": [], "penalties": []}


@app.get("/")

def dashboard():
    html = (Path(__file__).parent / "dashboard.html").read_text()
    return HTMLResponse(html)


@app.get("/results/{filename}")
def get_result(filename: str):
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Invalid filename")
    path = RESULTS_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"Not found: {filename}")
    return json.loads(path.read_text())


@app.get("/list")
def list_results():
    files = [
        {"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime}
        for f in RESULTS_DIR.glob("*.json")
    ]
    return {"files": files}


@app.get("/health")
def health():
    lock = RESULTS_DIR / ".live_runner.lock"
    if lock.exists():
        try:
            data = json.loads(lock.read_text())
            age = time.time() - float(data.get("last_heartbeat", 0))
            return {"status": "running" if age < 900 else "stale", "age_seconds": age, **data}
        except Exception:
            pass
    return {"status": "starting"}
