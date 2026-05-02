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
