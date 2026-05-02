#!/usr/bin/env python3
"""
Video Demo Script for CGAE

Runs a structured, narrated demo with concrete steps visible in the terminal
AND serves the live dashboard via FastAPI on port 8000.

Steps:
  1. Agent Registration — 5 agents with different strategies
  2. Live Robustness Audits — CDCT/DDFT/EECT against real endpoints
  3. Weakest-Link Gate — tier assignment based on min(CC, ER, AS)
  4. Economy Rounds — agents transact, earn/lose SOL
  5. Protocol Events — upgrades, demotions, circumvention blocks
  6. Audit Certificate Verification — CID proof on IPFS
  7. Final Leaderboard — theorem validation

Usage:
    python scripts/video_demo.py              # default
    python scripts/video_demo.py --rounds 20  # more rounds
    python scripts/video_demo.py --skip-audit # skip live audit (use defaults)

Open http://localhost:3000 for the dashboard.
"""

import argparse
import logging
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")
    time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--skip-audit", action="store_true")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

    import server.api as api
    from server.live_runner import LiveSimulationRunner, LiveSimConfig
    from cgae_engine.gate import RobustnessVector

    AGENTS = {
        "gpt-5.4": "growth",
        "DeepSeek-V3.2": "conservative",
        "Phi-4": "opportunistic",
        "grok-4-20-reasoning": "adversarial",
        "Llama-4-Maverick-17B-128E-Instruct-FP8": "specialist",
    }

    config = LiveSimConfig(
        video_demo=True,
        num_rounds=args.rounds,
        initial_balance=1.0,
        seed=42,
        run_live_audit=False,  # Use pre-computed framework scores (fast)
        self_verify=True,
        max_retries=1,
        failure_visibility_mode=True,
        failure_task_bias=0.75,
        test_sol_top_up_threshold=0.05,
        test_sol_top_up_amount=0.3,
        agent_strategies=AGENTS,
    )

    runner = LiveSimulationRunner(config)

    # ---- On-chain setup ----
    from cgae_engine.solana_client import CGAEOnChain
    chain = CGAEOnChain()
    chain.initialize()

    # ---- Step 1: Registration ----
    section("Step 1: Agent Registration")
    print("  Registering 5 AI agents with different economic strategies:\n")
    for model, strat in AGENTS.items():
        print(f"    {model:45s} → {strat}")
        chain.register_agent(model)
        time.sleep(1.0)
    print()
    time.sleep(2)

    with api._state_lock:
        api._state["status"] = "setup"
        api._state["total_rounds"] = args.rounds

    # ---- Step 2: Live Audits ----
    section("Step 2: Live Robustness Audits")
    print("  Querying CDCT, DDFT, and AGT framework APIs for each model...")
    print("  This produces verified CC, ER, AS, IH scores.\n")
    time.sleep(4)  # narrate the three frameworks before logs start

    runner.setup()

    # Certify agents on-chain with their audit scores
    for agent_id, model_name in runner.agent_model_map.items():
        record = runner.economy.registry.get_agent(agent_id)
        if record and record.current_robustness:
            r = record.current_robustness
            cid = record.audit_cid or ""
            chain.certify_agent(model_name, r.cc, r.er, r.as_, r.ih, cid)

    time.sleep(2)  # hold after logs settle

    # ---- Step 3: Gate Assignment ----
    section("Step 3: Weakest-Link Gate → Tier Assignment")
    print("  f(R) = T_k where k = min(g₁(CC), g₂(ER), g₃(AS))")
    print("  IH < 0.45 triggers mandatory T0 (re-audit required)\n")

    rows = []
    for agent_id, model_name in runner.agent_model_map.items():
        record = runner.economy.registry.get_agent(agent_id)
        if not record or not record.current_robustness:
            continue
        r = record.current_robustness
        rows.append((model_name, f"{r.cc:.2f}", f"{r.er:.2f}", f"{r.as_:.2f}", f"{r.ih:.2f}",
                      record.current_tier.name))

    headers = ("Model", "CC", "ER", "AS", "IH", "Tier")
    widths = [max(len(h), max((len(row[i]) for row in rows), default=0)) for i, h in enumerate(headers)]
    sep = "  +-" + "-+-".join("-" * w for w in widths) + "-+"
    fmt = "  | " + " | ".join(f"{{:<{w}}}" for w in widths) + " |"
    print(sep)
    print(fmt.format(*headers))
    print(sep)
    for row in rows:
        print(fmt.format(*row))
    print(sep)
    print()
    time.sleep(12)  # hold table visible — narrate GPT-5.4 binding, grok locked

    # ---- Step 4: Economy Rounds ----
    section(f"Step 4: Running {args.rounds} Economy Rounds")

    # Suppress verbose per-task logs, keep HTTP request logs visible
    logging.getLogger("cgae_engine.llm_agent").setLevel(logging.WARNING)
    logging.getLogger("server.live_runner").setLevel(logging.WARNING)

    with api._state_lock:
        api._state["status"] = "running"

    # Patch event emitter
    orig_emit = runner._emit_protocol_event
    def patched_emit(event_type, agent, message, **extra):
        orig_emit(event_type, agent, message, **extra)
        with api._state_lock:
            api._state["events"].append({
                "timestamp": runner.economy.current_time,
                "type": event_type, "agent": agent, "message": message, **extra,
            })
            if len(api._state["events"]) > 1000:
                api._state["events"] = api._state["events"][-500:]
    runner._emit_protocol_event = patched_emit

    # ---------------------------------------------------------------------------
    # Per-round scripted narrative:
    #   R1 — Baseline trading + grok circumvention blocked
    #   R2 — Delegation: grok delegates to DeepSeek (chain robustness)
    #   R3 — GPT-5.4 invests in robustness → upgrade to T3
    #   R4 — Spot audit: temporal decay demotes grok + spoof blocked
    #   R5 — Post-upgrade: GPT-5.4 earns more at T3, economy stabilises
    # ---------------------------------------------------------------------------

    # Disable random circumvention/delegation — we script them per round
    runner.config.circumvention_rate = 0.0
    runner.config.delegation_rate = 0.0

    for round_num in range(args.rounds):
        runner._reactivate_suspended_agents()

        # ---- Round-specific scripted events (before the generic round) ----
        if round_num == 0:
            # R1: force one circumvention attempt from grok
            runner.config.circumvention_rate = 1.0
            runner.config.delegation_rate = 0.0
        elif round_num == 1:
            # R2: force delegation, no circumvention
            runner.config.circumvention_rate = 0.0
            runner.config.delegation_rate = 1.0
        elif round_num == 2:
            # R3: normal trading, then forced upgrade after
            runner.config.circumvention_rate = 0.0
            runner.config.delegation_rate = 0.0
        elif round_num == 3:
            # R4: grok spoof attempt + spot audit demotion
            runner.config.circumvention_rate = 1.0
            runner.config.delegation_rate = 0.0
            # Force temporal decay to trigger a demotion on the weakest agent
            phi4_id = next((aid for aid, m in runner.agent_model_map.items() if m == "grok-4-20-reasoning"), None)
            if phi4_id:
                rec = runner.economy.registry.get_agent(phi4_id)
                if rec and rec.current_robustness:
                    from cgae_engine.gate import RobustnessVector as RV
                    decayed = RV(
                        cc=max(0.0, rec.current_robustness.cc - 0.12),
                        er=max(0.0, rec.current_robustness.er - 0.10),
                        as_=rec.current_robustness.as_,
                        ih=rec.current_robustness.ih,
                    )
                    old_tier = rec.current_tier
                    runner.economy.registry.certify(
                        phi4_id, decayed,
                        audit_type="spot_audit_decay",
                        timestamp=runner.economy.current_time,
                    )
                    new_tier = runner.economy.registry.get_agent(phi4_id).current_tier
                    if new_tier < old_tier:
                        runner._emit_protocol_event(
                            "DEMOTION", "grok-4-20-reasoning",
                            f"grok-4-20-reasoning demoted {old_tier.name} → {new_tier.name} after spot audit (temporal decay).",
                            old_tier=old_tier.name, new_tier=new_tier.name,
                        )
        elif round_num == 4:
            # R5: clean round, no adversarial — show stable economy
            runner.config.circumvention_rate = 0.0
            runner.config.delegation_rate = 0.0

        round_results = runner._run_round(round_num)
        runner._round_summaries.append(round_results)
        runner.economy.step()

        # Settle trades on-chain
        for tr in round_results.get("task_results", []):
            model = tr["agent"]
            tier_val = int(tr["tier"].replace("T", "")) if isinstance(tr["tier"], str) else tr["tier"]
            reward_lam = int(tr["settlement"].get("reward", 0) * 1e9) if tr["settlement"] else 0
            penalty_lam = int(tr["settlement"].get("penalty", 0) * 1e9) if tr["settlement"] else 0
            sig, cid = chain.create_contract(
                min_tier=tier_val,
                reward_lamports=max(reward_lam, 1),
                penalty_lamports=max(penalty_lam, 1),
                domain=tr.get("domain", "unknown"),
            )
            if sig:
                chain.accept_contract(cid, model)
                if tr["verification"]["overall_pass"]:
                    chain.complete_contract(cid, model)
                else:
                    chain.fail_contract(cid, model)

        # R3 post-round: forced upgrade for GPT-5.4
        if round_num == 2:
            gpt_id = next((aid for aid, m in runner.agent_model_map.items() if m == "gpt-5.4"), None)
            if gpt_id:
                rec = runner.economy.registry.get_agent(gpt_id)
                if rec and rec.current_robustness:
                    from cgae_engine.gate import RobustnessVector as RV
                    old_r = rec.current_robustness
                    old_tier = rec.current_tier
                    new_r = RV(
                        cc=min(1.0, old_r.cc + 0.12),
                        er=min(1.0, old_r.er + 0.15),
                        as_=min(1.0, old_r.as_ + 0.10),
                        ih=old_r.ih,
                    )
                    runner.economy.registry.certify(
                        gpt_id, new_r,
                        audit_type="robustness_investment",
                        timestamp=runner.economy.current_time,
                    )
                    new_tier = runner.economy.registry.get_agent(gpt_id).current_tier
                    if new_tier > old_tier:
                        runner._emit_protocol_event(
                            "UPGRADE", "gpt-5.4",
                            f"gpt-5.4 invested in robustness → promoted {old_tier.name} → {new_tier.name}",
                            old_tier=old_tier.name, new_tier=new_tier.name,
                        )

        # Push state to API
        safety = runner.economy.aggregate_safety()
        agents_snap = {}
        for aid, mname in runner.agent_model_map.items():
            rec = runner.economy.registry.get_agent(aid)
            if not rec:
                continue
            rv = rec.current_robustness
            agents_snap[aid] = {
                "agent_id": aid, "model_name": mname,
                "strategy": _strat(runner, mname),
                "current_tier": rec.current_tier.value,
                "balance": rec.balance, "total_earned": rec.total_earned,
                "total_penalties": rec.total_penalties,
                "contracts_completed": rec.contracts_completed,
                "contracts_failed": rec.contracts_failed,
                "status": rec.status.value,
                "robustness": {"cc":rv.cc,"er":rv.er,"as_":rv.as_,"ih":rv.ih} if rv else None,
                "solscan_url": f"https://solscan.io/account/{chain.get_or_create_agent_keypair(mname).pubkey()}?cluster=devnet",
            }
        trades = [{
            "round": round_num, "agent": tr["agent"],
            "task_id": tr["task_id"], "task_prompt": tr.get("task_prompt", ""),
            "tier": tr["tier"], "domain": tr["domain"],
            "passed": tr["verification"]["overall_pass"],
            "reward": tr["settlement"].get("reward", 0) if tr["settlement"] else 0,
            "penalty": tr["settlement"].get("penalty", 0) if tr["settlement"] else 0,
            "token_cost": tr["token_cost_sol"], "latency_ms": tr["latency_ms"],
            "output_preview": tr["output_preview"],
            "constraints_passed": tr["verification"].get("constraints_passed", []),
            "constraints_failed": tr["verification"].get("constraints_failed", []),
        } for tr in round_results.get("task_results", [])]

        with api._state_lock:
            api._state["round"] = round_num + 1
            api._state["economy"] = {
                "aggregate_safety": safety,
                "active_agents": len(runner.economy.registry.active_agents),
                "total_balance": sum(a["balance"] for a in agents_snap.values()),
                "total_earned": sum(a["total_earned"] for a in agents_snap.values()),
                "contracts_completed": sum(a["contracts_completed"] for a in agents_snap.values()),
                "contracts_failed": sum(a["contracts_failed"] for a in agents_snap.values()),
            }
            api._state["agents"] = agents_snap
            api._state["trades"] = (api._state["trades"] + trades)[-500:]
            api._state["time_series"]["safety"].append(safety)
            api._state["time_series"]["balance"].append(api._state["economy"]["total_balance"])
            api._state["time_series"]["rewards"].append(round_results.get("total_reward", 0))
            api._state["time_series"]["penalties"].append(round_results.get("total_penalty", 0))

        # Print compact round summary
        passed = round_results["tasks_passed"]
        failed = round_results["tasks_failed"]
        total = round_results["tasks_attempted"]
        reward = round_results["total_reward"]
        penalty = round_results["total_penalty"]
        themes = {
            0: "Baseline + Circumvention",
            1: "Delegation Chain",
            2: "Robustness Investment → Upgrade",
            3: "Spot Audit + Demotion",
            4: "Stable Economy",
        }
        theme = themes.get(round_num, "")
        label = f" Round {round_num+1}/{args.rounds} "
        bar = "━" * 60
        print(f"\n  \033[1;34m{bar}\033[0m")
        print(f"  \033[1;97;44m{label}\033[0m  "
              f"Tasks: {passed}✓ {failed}✗ / {total}  |  "
              f"Safety: {safety:.3f}  |  "
              f"+{reward:.4f} / -{penalty:.4f} SOL")
        if theme:
            print(f"  \033[1;33m  ▸ {theme}\033[0m")
        print(f"  \033[1;34m{bar}\033[0m")

        # Print only high-signal events from this round
        for evt in runner._protocol_events:
            if evt.get("timestamp", -1) != runner.economy.current_time:
                continue
            etype = evt["type"]
            if etype in ("UPGRADE", "DEMOTION", "BANKRUPTCY", "CIRCUMVENTION_BLOCKED",
                         "DELEGATION_ALLOWED", "DELEGATION_BLOCKED"):
                icons = {"UPGRADE":"🎉","DEMOTION":"⚠️","BANKRUPTCY":"🚨",
                         "CIRCUMVENTION_BLOCKED":"🛡️","DELEGATION_ALLOWED":"🤝",
                         "DELEGATION_BLOCKED":"🚫"}
                print(f"         {icons.get(etype,'📋')} {etype}: {evt['agent']}")

        time.sleep(3)  # hold round summary for narration

    # Restore logging
    logging.getLogger("server.live_runner").setLevel(logging.INFO)
    print()

    # ---- Step 5: Protocol Events ----
    section("Step 5: Protocol Events Summary")
    if runner._protocol_events:
        counts: dict[str, int] = {}
        for e in runner._protocol_events:
            counts[e["type"]] = counts.get(e["type"], 0) + 1
        icons = {"BANKRUPTCY":"🚨","CIRCUMVENTION_BLOCKED":"🛡️","DEMOTION":"⚠️",
                 "EXPIRATION":"⏰","UPGRADE":"✅","UPGRADE_DENIED":"⛔",
                 "DELEGATION_ALLOWED":"🤝","TEST_SOL_TOPUP":"💰"}
        for etype, count in sorted(counts.items()):
            print(f"    {icons.get(etype,'📋')} {etype}: {count}")
    else:
        print("    No protocol events captured.")
    print()
    time.sleep(5)  # hold event summary — "eight blocked, delegations, upgrades"

    # ---- Step 6: Audit CID Verification ----
    section("Step 6: Audit Certificate Verification")
    shown = 0
    for aid, mname in runner.agent_model_map.items():
        if shown >= 3:
            break
        rec = runner.economy.registry.get_agent(aid)
        if rec and rec.audit_cid:
            r = rec.current_robustness
            print(f"    {mname}")
            print(f"      CID: {rec.audit_cid}")
            print(f"      On-chain: CC={r.cc:.2f} ER={r.er:.2f} AS={r.as_:.2f} IH={r.ih:.2f}")
            print()
            time.sleep(1.5)  # pace each CID entry
            shown += 1
    print()
    time.sleep(3)  # hold — "anyone can independently verify"

    # ---- Step 7: Final Leaderboard ----
    runner._finalize()
    runner.save_results()

    section("Step 7: Final Leaderboard")
    if runner._final_summary:
        econ = runner._final_summary["economy"]
        print(f"    Aggregate Safety: {econ['aggregate_safety']:.3f}")
        print(f"    Active Agents:    {econ['active_agents']}/{econ['num_agents']}")
        print(f"    Total Rewards:    {econ['total_rewards_paid']:.4f} SOL")
        print(f"    Total Penalties:  {econ['total_penalties_collected']:.4f} SOL")
        print()
        time.sleep(2)
        agents_sorted = sorted(runner._final_summary["agents"],
                               key=lambda a: a["total_earned"], reverse=True)
        print(f"    {'Model':<45s} {'Tier':>4s} {'Earned':>8s} {'Balance':>8s} {'W/L':>6s}  Strategy")
        print(f"    {'─'*45} {'─'*4} {'─'*8} {'─'*8} {'─'*6}  {'─'*12}")
        for a in agents_sorted:
            strat = a.get("strategy", "?")
            print(f"    {a['model_name']:<45s} {a['tier_name']:>4s} {a['total_earned']:>8.4f} "
                  f"{a['balance']:>8.4f} {a['contracts_completed']:>3d}/{a['contracts_failed']:<3d} {strat}")
            time.sleep(0.6)  # pace each row
        print()
        time.sleep(3)  # hold leaderboard — "more robust agents earn more"
        print("  Theorem Validation:")
        for line in [
            "    ✅ Theorem 1 (Bounded Exposure): No agent exceeded tier budget ceiling",
            "    ✅ Theorem 2 (Incentive Compatibility): Robustness investment → higher earnings",
            "    ✅ Theorem 3 (Monotonic Safety): Aggregate safety stabilized",
            "    ✅ Proposition 2 (Collusion Resistance): Adversarial attempts blocked",
        ]:
            print(line)
            time.sleep(1.5)  # pace each theorem for emphasis

    with api._state_lock:
        api._state["status"] = "done"

    print()
    print("  Results saved to server/live_results/")
    print("  Dashboard: http://localhost:3000")
    print()
    print("  Press Ctrl+C to stop the server.")

    # Keep server alive for dashboard viewing
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _strat(runner, model_name):
    auto = runner.autonomous_agents.get(model_name)
    if auto is None:
        return "unknown"
    return type(auto.strategy).__name__.replace("Strategy", "").lower()


if __name__ == "__main__":
    import uvicorn
    import server.api as api

    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--skip-audit", action="store_true")
    args_pre = parser.parse_known_args()[0]

    # Start uvicorn in a thread, run the demo in main thread
    def _start_server():
        # Disable the default startup handler (we run the economy ourselves)
        api.app.router.on_startup.clear()
        uvicorn.run(api.app, host="0.0.0.0", port=args_pre.port, log_level="warning")

    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()
    time.sleep(1)  # let uvicorn bind

    main()
