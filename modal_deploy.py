"""
Modal deployment for CGAE Live Economy Backend.

Runs the live_runner continuously and persists results to Modal Volume.
Dashboard (Streamlit Cloud) reads from this volume via Modal's web endpoint.
"""

import modal

# Create Modal app
app = modal.App("cgae-economy")

# Create persistent volume for results
volume = modal.Volume.from_name("cgae-results", create_if_missing=True)

# Define container image with dependencies and cached audits
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_requirements("requirements.txt")
    .pip_install("fastapi>=0.110,<1", "openai>=1.30.0")
    .env({
        "PYTHONUNBUFFERED": "1",
    })
    .add_local_python_source("server", "cgae_engine", "agents", "storage")
    .add_local_file("contracts/deployed.json", remote_path="/app/contracts/deployed.json")
    .add_local_dir("server/live_results/audit_cache", remote_path="/app/audit_cache")  # Keep add_local_* last
)


@app.function(
    image=image,
    volumes={"/results": volume},
    secrets=[modal.Secret.from_name("azure_credentials")],  # All credentials in one secret
    timeout=86400,  # 24 hours
    cpu=2.0,
    memory=4096,
    min_containers=1,  # Keep one instance always running
)
def run_live_economy():
    """Run the CGAE live economy continuously."""
    import json
    import os
    import sys
    import threading
    import time
    from pathlib import Path

    # Ensure local project sources bundled into the image are importable.
    for source_root in ("/root", "/app"):
        if source_root not in sys.path:
            sys.path.insert(0, source_root)

    # Set output directory to mounted volume
    os.environ["CGAE_OUTPUT_DIR"] = "/results"
    results_dir = Path("/results")
    results_dir.mkdir(parents=True, exist_ok=True)

    # Write heartbeat metadata so scheduler can detect healthy/stale workers.
    lock_path = Path("/results/.live_runner.lock")
    stop_heartbeat = threading.Event()

    def heartbeat():
        while not stop_heartbeat.is_set():
            payload = {
                "status": "running",
                "pid": os.getpid(),
                "last_heartbeat": time.time(),
            }
            lock_path.write_text(json.dumps(payload), encoding="utf-8")
            volume.commit()
            stop_heartbeat.wait(30)

    heartbeat_thread = threading.Thread(target=heartbeat, name="live-runner-heartbeat", daemon=True)
    heartbeat_thread.start()

    # Publish bootstrap files immediately so dashboard endpoints have data
    # even while the first live round is still initializing.
    bootstrap_files = {
        "economy_state.json": {},
        "agent_details.json": {},
        "task_results.json": [],
        "protocol_events.json": [],
        "round_summaries.json": [],
        "final_summary.json": {
            "economy": {},
            "agents": [],
            "safety_trajectory": [],
        },
    }
    for filename, payload in bootstrap_files.items():
        (results_dir / filename).write_text(json.dumps(payload), encoding="utf-8")
    volume.commit()

    # Import and run
    from server.live_runner import LiveSimulationRunner, LiveSimConfig

    config = LiveSimConfig(
        num_rounds=-1,  # Infinite
        output_dir="/results",
        live_audit_cache_dir="/app/audit_cache",  # Use pre-computed audits
        run_live_audit=False,  # Avoid slow startup dependencies on external framework APIs
        seed=42,
        video_demo=True,
        failure_visibility_mode=True,
        failure_task_bias=1.0,
        initial_balance=5.0,           # 5 SOL per agent (5 agents = 25 SOL total)
        test_sol_top_up_threshold=1.0, # Top up when balance drops below 1 SOL
        test_sol_top_up_amount=5.0,    # Inject 5 SOL at a time (testnet SOL available)
        ih_threshold=0.45,             # Empirical ih scores land ~0.49; 0.5 suspends everyone
    )

    runner = LiveSimulationRunner(config)
    try:
        runner.run()
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2)
        if lock_path.exists():
            lock_path.unlink()
        volume.commit()


@app.function(
    image=image,
    volumes={"/results": volume},
    secrets=[modal.Secret.from_name("azure_credentials")],
    schedule=modal.Period(minutes=5),
    timeout=120,
)
def ensure_live_economy_running():
    """
    Scheduled keeper that starts the runner when no fresh heartbeat exists.

    This runs automatically after `modal deploy` and then every 5 minutes.
    """
    import json
    import time
    from pathlib import Path

    volume.reload()
    lock_path = Path("/results/.live_runner.lock")
    results_dir = Path("/results")
    now = time.time()
    stale_after_seconds = 15 * 60
    required_outputs = [
        "final_summary.json",
        "round_summaries.json",
        "task_results.json",
        "economy_state.json",
        "agent_details.json",
        "protocol_events.json",
    ]

    if lock_path.exists():
        try:
            lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
            last_heartbeat = float(lock_data.get("last_heartbeat", 0))
            missing_outputs = [
                name for name in required_outputs if not (results_dir / name).exists()
            ]
            if now - last_heartbeat < stale_after_seconds and not missing_outputs:
                return {
                    "status": "runner_healthy",
                    "last_heartbeat": last_heartbeat,
                }
            if now - last_heartbeat < stale_after_seconds and missing_outputs:
                # Runner appears alive but has not produced output files.
                # Restart to recover from startup/import deadlocks.
                lock_path.write_text(
                    json.dumps(
                        {
                            "status": "restarting_missing_outputs",
                            "last_heartbeat": now,
                            "missing_outputs": missing_outputs,
                        }
                    ),
                    encoding="utf-8",
                )
                volume.commit()
                run_live_economy.spawn()
                return {
                    "status": "runner_restarted_missing_outputs",
                    "missing_outputs": missing_outputs,
                    "restarted_at": now,
                }
        except Exception:
            # Fall through and restart if lock file is malformed.
            pass

    # Write a startup heartbeat immediately to avoid duplicate starts.
    startup_payload = {
        "status": "starting",
        "last_heartbeat": now,
    }
    lock_path.write_text(json.dumps(startup_payload), encoding="utf-8")
    volume.commit()
    run_live_economy.spawn()
    return {"status": "runner_started", "started_at": now}


@app.function(
    image=image,
    volumes={"/results": volume},
    secrets=[modal.Secret.from_name("azure_credentials")],
    timeout=300,
)
@modal.fastapi_endpoint(method="GET")
def get_results(path: str = "final_summary.json"):
    """
    Web endpoint to serve result files to Streamlit dashboard.

    Usage: https://your-modal-app.modal.run/get_results?path=final_summary.json
    """
    import json
    from pathlib import Path

    from fastapi import HTTPException

    volume.reload()
    results_root = Path("/results").resolve()
    requested_path = Path(path)

    # Block absolute paths and parent traversal.
    if requested_path.is_absolute() or ".." in requested_path.parts:
        raise HTTPException(status_code=400, detail="Invalid file path")

    file_path = (results_root / requested_path).resolve()
    if results_root not in file_path.parents and file_path != results_root:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.function(
    image=image,
    volumes={"/results": volume},
    secrets=[modal.Secret.from_name("azure_credentials")],
    timeout=60,
)
@modal.fastapi_endpoint(method="GET")
def list_results():
    """
    List all available result files.

    Usage: https://your-modal-app.modal.run/list_results
    """
    from pathlib import Path

    volume.reload()
    results_dir = Path("/results")
    if not results_dir.exists():
        return {"files": []}

    files = [
        {
            "name": f.name,
            "size": f.stat().st_size,
            "modified": f.stat().st_mtime,
        }
        for f in results_dir.glob("*.json")
    ]
    
    return {"files": files}


@app.function(
    image=image,
    volumes={"/results": volume},
    secrets=[modal.Secret.from_name("azure_credentials")],
    timeout=60,
)
@modal.fastapi_endpoint(method="GET")
def health():
    """
    Report live runner health based on lock-file heartbeat.

    Usage: https://your-modal-app.modal.run/health
    """
    import json
    import time
    from pathlib import Path

    from fastapi import HTTPException

    volume.reload()
    lock_path = Path("/results/.live_runner.lock")
    results_dir = Path("/results")
    now = time.time()
    stale_after_seconds = 15 * 60
    required_outputs = [
        "final_summary.json",
        "round_summaries.json",
        "task_results.json",
        "economy_state.json",
        "agent_details.json",
        "protocol_events.json",
    ]
    missing_outputs = [name for name in required_outputs if not (results_dir / name).exists()]

    if not lock_path.exists():
        run_live_economy.spawn()
        return {
            "status": "starting",
            "reason": "heartbeat_lock_missing_spawned_runner",
            "stale_after_seconds": stale_after_seconds,
            "missing_outputs": missing_outputs,
            "timestamp": now,
        }

    try:
        lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Malformed lock file: {e}") from e

    last_heartbeat = float(lock_data.get("last_heartbeat", 0))
    age_seconds = max(0.0, now - last_heartbeat)
    if age_seconds >= stale_after_seconds:
        run_live_economy.spawn()
        return {
            "status": "restarting",
            "reason": "heartbeat_stale_spawned_runner",
            "age_seconds": age_seconds,
            "last_heartbeat": last_heartbeat,
            "stale_after_seconds": stale_after_seconds,
            "missing_outputs": missing_outputs,
            "lock": lock_data,
        }

    if missing_outputs:
        run_live_economy.spawn()
        return {
            "status": "restarting",
            "reason": "missing_outputs_spawned_runner",
            "age_seconds": age_seconds,
            "last_heartbeat": last_heartbeat,
            "stale_after_seconds": stale_after_seconds,
            "missing_outputs": missing_outputs,
            "lock": lock_data,
        }

    return {
        "status": "running",
        "age_seconds": age_seconds,
        "last_heartbeat": last_heartbeat,
        "stale_after_seconds": stale_after_seconds,
        "missing_outputs": missing_outputs,
        "lock": lock_data,
    }


@app.local_entrypoint()
def main():
    """Manual helper for `modal run modal_deploy.py`."""
    print("Triggering CGAE live economy run once...")
    run_live_economy.remote()
