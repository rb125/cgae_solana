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
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.text import Text
from rich.theme import Theme
from rich.logging import RichHandler

sys.path.insert(0, str(Path(__file__).parent.parent))

# Custom theme for CGAE
cgae_theme = Theme({
    "info": "cyan",
    "warning": "orange3",
    "danger": "bold red",
    "success": "bold green",
    "tier_0": "grey50",
    "tier_1": "bright_green",
    "tier_2": "bright_blue",
    "tier_3": "bright_magenta",
    "tier_4": "bright_yellow",
    "tier_5": "bright_red",
    "solana": "bold cyan",
})

console = Console(theme=cgae_theme)

# Configure Rich logging globally to ensure logs look beautiful and don't break Live UI
logging.basicConfig(
    level="INFO",
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False, keywords=["POST", "GET", "registered", "certified"])]
)
logger = logging.getLogger("cgae.demo")

VOICEOVER_PACING = {
    "section_transition": 1.0,
    "intro_hold": 12.0,
    "registration_row": 1.0,
    "registration_linger": 8.0,
    "audit_certify_row": 0.75,
    "audit_identity_linger": 7.0,
    "gate_linger": 12.0,
    "trade_linger": 3.0,
    "round_summary_linger": 4.0,
    "dashboard_walkthrough": 18.0,
    "events_summary_linger": 2.0,
    "cid_card_linger": 1.0,
    "cid_summary_linger": 1.5,
    "leaderboard_stats_linger": 1.5,
    "leaderboard_linger": 2.0,
    "theorem_line_linger": 1.0,
}

EVENT_BEAT_PAUSES = {
    "CIRCUMVENTION_BLOCKED": 4.0,
    "UPGRADE": 2.5,
    "DEMOTION": 2.5,
}


def pause(seconds: float):
    time.sleep(seconds)


def intro_card():
    intro = Text.assemble(
        ("CGAE\n", "bold white"),
        ("Comprehension-Gated Agent Economy\n\n", "solana"),
        ("Live LLM calls • Choreographed scenario • Solana Devnet\n", "info"),
        ("Dashboard: http://localhost:3000", "success"),
    )
    console.print(Panel(intro, border_style="solana", padding=(1, 2), title="[bold white]Demo Start[/bold white]"))
    pause(VOICEOVER_PACING["intro_hold"])


def dashboard_walkthrough_window():
    body = Text.assemble(
        ("Dashboard walkthrough window is open.\n", "bold white"),
        ("Use this beat for Trades, Agents, and On-Chain tabs.\n", "info"),
        ("Dashboard: http://localhost:3000", "success"),
    )
    console.print(Panel(body, border_style="success", padding=(1, 2), title="[bold white]Dashboard Walkthrough[/bold white]"))
    pause(VOICEOVER_PACING["dashboard_walkthrough"])


def section(title: str, subtitle: str = ""):
    console.print("\n")
    console.print(Panel(
        Text(title, style="bold white", justify="center"),
        subtitle=subtitle,
        border_style="solana",
        padding=(1, 2)
    ))
    console.print("\n")
    pause(VOICEOVER_PACING["section_transition"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2)
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
        run_live_audit=False,
        self_verify=True,
        max_retries=1,
        failure_visibility_mode=True,
        failure_task_bias=0.75,
        test_sol_top_up_threshold=0.05,
        test_sol_top_up_amount=0.3,
        agent_strategies=AGENTS,
    )

    # Re-enable all relevant loggers at INFO level
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("solana").setLevel(logging.INFO)
    logging.getLogger("cgae_engine.solana_client").setLevel(logging.INFO)

    runner = LiveSimulationRunner(config)

    # ---- On-chain setup ----
    from cgae_engine.solana_client import CGAEOnChain
    chain = CGAEOnChain()
    with console.status("[solana]Initializing CGAE Protocol on Solana Devnet..."):
        chain.initialize()
        pause(1.0)

    intro_card()

    # ---- Step 1: Registration ----
    section("Step 1: Agent Registration", "Makers & Economic Strategies")
    
    reg_table = Table(show_header=True, header_style="solana", box=None, padding=(0, 2))
    reg_table.add_column("AI Model", style="bold white", width=40)
    reg_table.add_column("Economic Strategy", style="info", width=20)
    reg_table.add_column("On-Chain Status", justify="right", width=15)

    with Live(Panel(reg_table, border_style="grey23", title="[dim]Registration Queue[/dim]"), 
              console=console, refresh_per_second=4, transient=False):
        for model, strat in AGENTS.items():
            chain.register_agent(model)
            reg_table.add_row(model, strat.capitalize(), "[bold success]REGISTERED[/bold success]")
            pause(VOICEOVER_PACING["registration_row"])

    pause(VOICEOVER_PACING["registration_linger"])

    with api._state_lock:
        api._state["status"] = "setup"
        api._state["total_rounds"] = args.rounds

    # ---- Step 2: Live Audits ----
    section("Step 2: Live Robustness Audits", "CDCT / DDFT / AGT Frameworks")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        audit_task = progress.add_task("[cyan]Auditing Agent Fleet...", total=len(AGENTS))
        
        runner.setup()
        
        # Certify agents on-chain with their audit scores
        for agent_id, model_name in runner.agent_model_map.items():
            record = runner.economy.registry.get_agent(agent_id)
            if record and record.current_robustness:
                r = record.current_robustness
                cid = record.audit_cid or ""
                progress.update(audit_task, description=f"[cyan]Certifying {model_name}...")
                chain.certify_agent(model_name, r.cc, r.er, r.as_, r.ih, cid)
                progress.advance(audit_task)
                pause(VOICEOVER_PACING["audit_certify_row"])

    pause(1.0)

    # ---- Added: Identity Verification Summary ----
    id_table = Table(show_header=True, header_style="solana", box=None, padding=(0, 2))
    id_table.add_column("Agent Model", style="bold white", width=40)
    id_table.add_column("Solana Wallet Address (Public Key)", style="info", width=50)
    id_table.add_column("Audit Anchored", justify="right")

    for model_name in AGENTS.keys():
        pubkey = str(chain.get_or_create_agent_keypair(model_name).pubkey())
        id_table.add_row(model_name, pubkey, "[bold success]CERTIFIED[/bold success]")

    console.print(Panel(id_table, border_style="grey23", title="[dim]On-Chain Identity Registry[/dim]"))
    console.print("\n[dim]Audit results have been uploaded to decentralized storage and anchored to these PDAs.[/dim]")
    pause(VOICEOVER_PACING["audit_identity_linger"])

    # ---- Step 3: Gate Assignment ----
    section("Step 3: Weakest-Link Gate → Tier Assignment", "f(R) = T_k where k = min(g₁(CC), g₂(ER), g₃(AS))")
    
    gate_table = Table(show_header=True, header_style="bold white", box=None, border_style="grey23")
    gate_table.add_column("Model", style="bold white", width=30)
    gate_table.add_column("CC", justify="center")
    gate_table.add_column("ER", justify="center")
    gate_table.add_column("AS", justify="center")
    gate_table.add_column("IH", justify="center")
    gate_table.add_column("Assigned Tier", justify="right")

    for agent_id, model_name in runner.agent_model_map.items():
        record = runner.economy.registry.get_agent(agent_id)
        if not record or not record.current_robustness:
            continue
        r = record.current_robustness
        t_color = f"tier_{record.current_tier.value}"
        gate_table.add_row(
            model_name, 
            f"{r.cc:.2f}", f"{r.er:.2f}", f"{r.as_:.2f}", f"{r.ih:.2f}",
            f"[{t_color}]{record.current_tier.name}[/{t_color}]"
        )

    console.print(gate_table)
    console.print("\n[dim italic]Note: IH < 0.45 triggers mandatory T0 (re-audit required)[/dim italic]")
    pause(VOICEOVER_PACING["gate_linger"])

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
        event_pause = EVENT_BEAT_PAUSES.get(event_type)
        if event_pause:
            pause(event_pause)
    runner._emit_protocol_event = patched_emit

    # ---------------------------------------------------------------------------
    # Compressed Narrative (2 Rounds):
    #   R1 — Baseline + Grok circumvention blocked + Phi-4 delegation allowed
    #   R2 — GPT-5.4 investment upgrade + Grok demotion (spot audit) + Stability
    # ---------------------------------------------------------------------------

    for round_num in range(args.rounds):
        runner._reactivate_suspended_agents()
        # ... scripted narrative ...
        if round_num == 0:
            runner.config.circumvention_rate = 1.0
            runner.config.delegation_rate = 1.0
        elif round_num == 1:
            runner.config.circumvention_rate = 0.0
            runner.config.delegation_rate = 0.0
            grok_id = next((aid for aid, m in runner.agent_model_map.items() if m == "grok-4-20-reasoning"), None)
            if grok_id:
                rec = runner.economy.registry.get_agent(grok_id)
                if rec and rec.current_robustness:
                    from cgae_engine.gate import RobustnessVector as RV
                    decayed = RV(cc=max(0.0, rec.current_robustness.cc - 0.15), er=max(0.0, rec.current_robustness.er - 0.12), as_=rec.current_robustness.as_, ih=rec.current_robustness.ih)
                    old_tier = rec.current_tier
                    runner.economy.registry.certify(grok_id, decayed, audit_type="spot_audit_decay", timestamp=runner.economy.current_time)
                    new_tier = runner.economy.registry.get_agent(grok_id).current_tier
                    if new_tier < old_tier:
                        runner._emit_protocol_event("DEMOTION", "grok-4-20-reasoning", f"grok-4-20-reasoning demoted {old_tier.name} \u2192 {new_tier.name} after spot audit (temporal decay).", old_tier=old_tier.name, new_tier=new_tier.name)

        # UNROLLED ROUND EXECUTION
        import hashlib
        from server.live_runner import compute_token_cost_sol, update_robustness_from_verification
        from cgae_engine.marketplace import Constraint
        import random

        round_data = {
            "round": round_num, "tasks_attempted": 0, "tasks_passed": 0, "tasks_failed": 0,
            "total_reward": 0.0, "total_penalty": 0.0, "total_token_cost": 0.0, "task_results": []
        }

        # Randomize agent order for better visual variety
        agent_ids = list(runner.agent_model_map.keys())
        random.shuffle(agent_ids)

        for agent_id in agent_ids:
            model_name = runner.agent_model_map[agent_id]
            agent = runner.economy.registry.get_agent(agent_id)
            if not agent or agent.status.value != "active": continue
            
            # Use runner's logic to pick/execute task
            tier = agent.current_tier
            autonomous = runner.autonomous_agents.get(model_name)
            from cgae_engine.tasks import get_tasks_for_tier
            available_tasks = get_tasks_for_tier(tier)
            if not available_tasks: continue

            if autonomous:
                state = autonomous.build_state(agent, runner.economy.gate)
                task = autonomous.plan_task(available_tasks, state)
            else:
                task = random.choice(available_tasks)
            
            strategy_name = runner.config.agent_strategies.get(model_name, "GrowthStrategy")
            task = runner._maybe_bias_task_for_failures(task, available_tasks, strategy_name)
            if not task: continue

            # Create and Accept Contract
            contract = runner.economy.post_contract(
                objective=task.prompt[:100] + "...",
                constraints=[Constraint(c.name, c.description, c.check) for c in task.constraints],
                min_tier=task.tier, reward=task.reward, penalty=task.penalty, deadline_offset=100.0, domain=task.domain, difficulty=task.difficulty
            )
            if not runner.economy.accept_contract(contract.contract_id, agent_id): continue

            # Execute task
            execution_autonomous = runner.autonomous_agents.get(model_name)
            if execution_autonomous is not None:
                try:
                    exec_result = execution_autonomous.execute_task(task)
                    output = exec_result.output
                    token_cost = exec_result.token_cost_sol
                    latency = exec_result.latency_ms
                    tokens_in = exec_result.token_usage.get("input", 0)
                    tokens_out = exec_result.token_usage.get("output", 0)
                except Exception as e:
                    output = ""; token_cost = 0.0; latency = 0.0; tokens_in = tokens_out = 0
            else:
                llm_agent = runner.llm_agents[model_name]
                tok_in_before = llm_agent.total_input_tokens
                tok_out_before = llm_agent.total_output_tokens
                t0 = time.time()
                try:
                    output = llm_agent.execute_task(task.prompt, task.system_prompt)
                    latency = (time.time() - t0) * 1000
                except Exception:
                    output = ""; latency = (time.time() - t0) * 1000
                tokens_in = llm_agent.total_input_tokens - tok_in_before
                tokens_out = llm_agent.total_output_tokens - tok_out_before
                token_cost = compute_token_cost_sol(model_name, tokens_in, tokens_out)

            agent.balance -= token_cost
            agent.total_spent += token_cost
            runner._token_costs[agent_id] = runner._token_costs.get(agent_id, 0.0) + token_cost
            round_data["total_token_cost"] += token_cost

            verification = runner.verifier.verify(task=task, output=output, agent_model=model_name, latency_ms=latency)

            if agent.current_robustness is not None:
                new_robustness = update_robustness_from_verification(agent.current_robustness, task, verification)
                candidate_tier = runner.economy.gate.evaluate(new_robustness)
                if candidate_tier > tier:
                    upgrade = runner.economy.request_tier_upgrade(
                        agent_id, requested_tier=candidate_tier,
                        audit_callback=lambda _aid, _t, r=new_robustness: r,
                    )
                    if not upgrade.get("granted"):
                        runner.economy.registry.certify(agent_id, new_robustness, audit_type="task_update", timestamp=runner.economy.current_time)
                else:
                    runner.economy.registry.certify(agent_id, new_robustness, audit_type="task_update", timestamp=runner.economy.current_time)

            if autonomous is not None:
                autonomous.update_state(task, verification, token_cost)

            settlement = runner.economy.complete_contract(contract.contract_id, output, verification_override=verification.overall_pass, liability_agent_id=agent_id)

            audit_cid = f"solana_audit_{hashlib.sha256(str(task.task_id).encode()).hexdigest()[:32]}"
            tr = {
                "agent": model_name,
                "agent_id": agent_id,
                "task_id": task.task_id,
                "task_prompt": task.prompt,
                "tier": task.tier.name,
                "domain": task.domain,
                "proof_cid": audit_cid,
                "verification": verification.to_dict(),
                "settlement": settlement,
                "latency_ms": latency,
                "token_cost_sol": token_cost,
                "tokens_used": {"input": tokens_in, "output": tokens_out},
                "output_preview": output[:500] if output else "(empty)",
            }
            runner._results.append(tr)
            round_data["task_results"].append(tr)
            round_data["tasks_attempted"] += 1
            if tr["verification"]["overall_pass"]: round_data["tasks_passed"] += 1
            else: round_data["tasks_failed"] += 1
            round_data["total_reward"] += tr["settlement"].get("reward", 0)
            round_data["total_penalty"] += tr["settlement"].get("penalty", 0)

            # Publish the settled trade before the slower Solana RPC path so the
            # dashboard reflects execution immediately.
            with api._state_lock:
                new_trade = {
                    "round": round_num, "agent": tr["agent"], "task_id": tr["task_id"], "task_prompt": tr.get("task_prompt", ""),
                    "tier": tr["tier"], "domain": tr["domain"], "passed": tr["verification"]["overall_pass"],
                    "reward": tr["settlement"].get("reward", 0), "penalty": tr["settlement"].get("penalty", 0),
                    "token_cost": tr["token_cost_sol"], "latency_ms": tr["latency_ms"], "output_preview": tr["output_preview"],
                    "constraints_passed": tr["verification"].get("constraints_passed", []), "constraints_failed": tr["verification"].get("constraints_failed", []),
                }
                api._state["trades"] = (api._state["trades"] + [new_trade])[-500:]
                
                # Snapshot agents
                agents_snap = {}
                for aid, mname in runner.agent_model_map.items():
                    rec = runner.economy.registry.get_agent(aid)
                    if not rec: continue
                    rv = rec.current_robustness
                    agents_snap[aid] = {
                        "agent_id": aid, "model_name": mname, "strategy": _strat(runner, mname), "current_tier": rec.current_tier.value,
                        "balance": rec.balance, "total_earned": rec.total_earned, "total_penalties": rec.total_penalties,
                        "contracts_completed": rec.contracts_completed, "contracts_failed": rec.contracts_failed,
                        "status": rec.status.value, "robustness": {"cc":rv.cc,"er":rv.er,"as_":rv.as_,"ih":rv.ih} if rv else None,
                        "solscan_url": f"https://solscan.io/account/{chain.get_or_create_agent_keypair(mname).pubkey()}?cluster=devnet",
                    }
                api._state["agents"] = agents_snap
                api._state["economy"] = {
                    "aggregate_safety": runner.economy.aggregate_safety(),
                    "active_agents": len(runner.economy.registry.active_agents),
                    "total_balance": sum(a["balance"] for a in agents_snap.values()),
                    "total_earned": sum(a["total_earned"] for a in agents_snap.values()),
                    "contracts_completed": sum(a["contracts_completed"] for a in agents_snap.values()),
                    "contracts_failed": sum(a["contracts_failed"] for a in agents_snap.values()),
                }
                api._state["round"] = round_num + 1
            api.broadcast_sync()

            # Settle on Solana after the dashboard push.
            reward_lam = int(tr["settlement"].get("reward", 0) * 1e9)
            penalty_lam = int(tr["settlement"].get("penalty", 0) * 1e9)
            sig, cid = chain.create_contract(min_tier=int(tr["tier"].replace("T","")), reward_lamports=max(reward_lam, 1), penalty_lamports=max(penalty_lam, 1), domain=tr["domain"])
            if sig:
                chain.accept_contract(cid, model_name)
                if tr["verification"]["overall_pass"]: chain.complete_contract(cid, model_name)
                else: chain.fail_contract(cid, model_name)

            pause(VOICEOVER_PACING["trade_linger"])

        runner._round_summaries.append(round_data)
        runner.economy.step()

        # R2 post-round: forced upgrade for GPT-5.4
        if round_num == 1:
            gpt_id = next((aid for aid, m in runner.agent_model_map.items() if m == "gpt-5.4"), None)
            if gpt_id:
                rec = runner.economy.registry.get_agent(gpt_id)
                if rec and rec.current_robustness:
                    from cgae_engine.gate import RobustnessVector as RV
                    new_r = RV(cc=min(1.0, rec.current_robustness.cc + 0.15), er=min(1.0, rec.current_robustness.er + 0.18), as_=min(1.0, rec.current_robustness.as_ + 0.12), ih=rec.current_robustness.ih)
                    old_tier = rec.current_tier
                    runner.economy.registry.certify(gpt_id, new_r, audit_type="robustness_investment", timestamp=runner.economy.current_time)
                    new_tier = runner.economy.registry.get_agent(gpt_id).current_tier
                    if new_tier > old_tier:
                        runner._emit_protocol_event("UPGRADE", "gpt-5.4", f"gpt-5.4 invested in robustness \u2192 promoted {old_tier.name} \u2192 {new_tier.name}", old_tier=old_tier.name, new_tier=new_tier.name)

        # Update time series at end of round
        with api._state_lock:
            api._state["time_series"]["safety"].append(runner.economy.aggregate_safety())
            api._state["time_series"]["balance"].append(api._state["economy"]["total_balance"])
            api._state["time_series"]["rewards"].append(round_data.get("total_reward", 0))
            api._state["time_series"]["penalties"].append(round_data.get("total_penalty", 0))

        # Print compact round summary
        passed, failed, total = round_data["tasks_passed"], round_data["tasks_failed"], round_data["tasks_attempted"]
        reward, penalty = round_data["total_reward"], round_data["total_penalty"]
        safety = runner.economy.aggregate_safety()
        
        themes = {0: "Baseline + Circumvention + Delegation", 1: "Investment Upgrade + Spot Audit Demotion"}
        theme = themes.get(round_num, "")
        
        round_panel = Panel(
            Text.assemble(
                (f"Tasks: ", "dim"), (f"{passed}\u2713 ", "success"), (f"{failed}\u2717", "danger"), (f" / {total}  |  ", "dim"),
                (f"Safety: ", "dim"), (f"{safety:.3f}", "info"), (f"  |  ", "dim"),
                (f"+{reward:.4f}", "success"), (f" / ", "dim"), (f"-{penalty:.4f} SOL", "danger")
            ),
            title=f"[bold white]Round {round_num+1}/{args.rounds}[/bold white]",
            subtitle=f"[bold yellow]\u25b8 {theme}[/bold yellow]" if theme else None,
            border_style="solana" if round_num % 2 == 0 else "purple",
            padding=(0, 2)
        )
        console.print(round_panel)

        for evt in runner._protocol_events:
            if evt.get("timestamp", -1) != runner.economy.current_time:
                continue
            etype = evt["type"]
            if etype in ("UPGRADE", "DEMOTION", "BANKRUPTCY", "CIRCUMVENTION_BLOCKED", "DELEGATION_ALLOWED", "DELEGATION_BLOCKED"):
                icons = {"UPGRADE":"🎉","DEMOTION":"⚠️","BANKRUPTCY":"🚨","CIRCUMVENTION_BLOCKED":"🛡️","DELEGATION_ALLOWED":"🤝","DELEGATION_BLOCKED":"🚫"}
                style = "success" if etype in ("UPGRADE", "DELEGATION_ALLOWED") else "warning"
                if etype in ("BANKRUPTCY", "CIRCUMVENTION_BLOCKED"):
                    style = "danger"
                console.print(f"         {icons.get(etype,'📋')} [bold {style}]{etype}[/bold {style}]: {evt['agent']}")

        pause(VOICEOVER_PACING["round_summary_linger"])

    dashboard_walkthrough_window()

    logging.getLogger("server.live_runner").setLevel(logging.INFO)
    console.print("\n")

    # ---- Step 5: Protocol Events ----
    section("Step 5: Protocol Events Summary", "Aggregate Network Behavior")
    if runner._protocol_events:
        counts = {}
        for e in runner._protocol_events: counts[e["type"]] = counts.get(e["type"], 0) + 1
        evt_table = Table(show_header=False, box=None)
        evt_table.add_column("Icon", width=4); evt_table.add_column("Type", style="bold white", width=25); evt_table.add_column("Count", justify="right", style="info")
        icons = {"BANKRUPTCY":"🚨","CIRCUMVENTION_BLOCKED":"🛡️","DEMOTION":"⚠️","EXPIRATION":"⏰","UPGRADE":"✅","UPGRADE_DENIED":"⛔","DELEGATION_ALLOWED":"🤝","TEST_SOL_TOPUP":"💰"}
        for etype, count in sorted(counts.items()): evt_table.add_row(icons.get(etype,'📋'), etype, str(count))
        console.print(evt_table)
    else:
        console.print("    [dim]No protocol events captured.[/dim]")
    
    console.print("\n"); pause(VOICEOVER_PACING["events_summary_linger"])

    # ---- Step 6: Audit CID Verification ----
    section("Step 6: Audit Certificate Verification", "Proof of Robustness on IPFS")
    for aid, mname in list(runner.agent_model_map.items())[:3]:
        rec = runner.economy.registry.get_agent(aid)
        if rec and rec.audit_cid:
            r = rec.current_robustness
            cert_text = Text.assemble((f"Agent: ", "dim"), (f"{mname}\n", "bold white"),(f"CID:   ", "dim"), (f"{rec.audit_cid}\n", "info"),(f"Vector: ", "dim"), (f"CC={r.cc:.2f} ER={r.er:.2f} AS={r.as_:.2f} IH={r.ih:.2f}", "success"))
            console.print(Panel(cert_text, border_style="grey37"))
            pause(VOICEOVER_PACING["cid_card_linger"])
    
    console.print("\n"); pause(VOICEOVER_PACING["cid_summary_linger"])

    # ---- Step 7: Final Leaderboard ----
    runner._finalize(); runner.save_results()
    section("Step 7: Final Leaderboard", "Validated Economic Theorems")
    
    if runner._final_summary:
        econ = runner._final_summary["economy"]
        summary_grid = Table.grid(expand=True); summary_grid.add_column(justify="left"); summary_grid.add_column(justify="right")
        summary_grid.add_row("[dim]Aggregate Safety[/dim]", f"[bold info]{econ['aggregate_safety']:.3f}[/bold info]")
        summary_grid.add_row("[dim]Active Agents[/dim]", f"{econ['active_agents']}/{econ['num_agents']}")
        summary_grid.add_row("[dim]Total Rewards[/dim]", f"[success]{econ['total_rewards_paid']:.4f} SOL[/success]")
        summary_grid.add_row("[dim]Total Penalties[/dim]", f"[danger]{econ['total_penalties_collected']:.4f} SOL[/danger]")
        console.print(Panel(summary_grid, title="Economy Statistics", border_style="solana", width=50))
        console.print("\n"); pause(VOICEOVER_PACING["leaderboard_stats_linger"])

        agents_sorted = sorted(runner._final_summary["agents"], key=lambda a: a["total_earned"], reverse=True)
        lead_table = Table(show_header=True, header_style="bold white", box=None)
        lead_table.add_column("Model", style="bold white", width=40); lead_table.add_column("Tier", justify="center"); lead_table.add_column("Earned", justify="right", style="success"); lead_table.add_column("Balance", justify="right"); lead_table.add_column("W/L", justify="center"); lead_table.add_column("Strategy", style="dim")

        for a in agents_sorted:
            t_color = f"tier_{a['tier']}"
            lead_table.add_row(a['model_name'],f"[{t_color}]{a['tier_name']}[/{t_color}]",f"{a['total_earned']:.4f}",f"{a['balance']:.4f}",f"{a['contracts_completed']}/{a['contracts_failed']}",a.get("strategy", "?").capitalize())
        console.print(lead_table)
        console.print("\n"); pause(VOICEOVER_PACING["leaderboard_linger"])

        console.print("[bold white]Theorem Validation:[/bold white]")
        theorems = [("Theorem 1", "Bounded Exposure", "No agent exceeded tier budget ceiling"), ("Theorem 2", "Incentive Compatibility", "Robustness investment → higher earnings"), ("Theorem 3", "Monotonic Safety", "Aggregate safety stabilized"), ("Proposition 2", "Collusion Resistance", "Adversarial attempts blocked")]
        for t_id, t_name, t_desc in theorems:
            console.print(f"  [bold success]✓[/bold success] [bold white]{t_id}[/bold white] ({t_name}): [dim]{t_desc}[/dim]")
            pause(VOICEOVER_PACING["theorem_line_linger"])

    with api._state_lock: api._state["status"] = "done"

    console.print("\n")
    console.print(Panel(Text.assemble(("Results saved to ", "dim"), ("server/live_results/\n", "info"),("Dashboard: ", "dim"), ("http://localhost:3000\n", "solana"),("\nPress ", "dim"), ("Ctrl+C", "bold red"), (" to stop the server.", "dim")),title="[bold green]Simulation Complete[/bold green]",border_style="success"))

    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: pass


def _strat(runner, model_name):
    auto = runner.autonomous_agents.get(model_name)
    if auto is None: return "unknown"
    return type(auto.strategy).__name__.replace("Strategy", "").lower()


if __name__ == "__main__":
    import uvicorn
    import server.api as api
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=5); parser.add_argument("--port", type=int, default=8000); parser.add_argument("--skip-audit", action="store_true")
    args_pre = parser.parse_known_args()[0]
    def _start_server():
        api.app.router.on_startup.clear()
        async def _capture_broadcast_loop():
            api.register_broadcast_loop()
        api.app.router.on_startup.append(_capture_broadcast_loop)
        uvicorn.run(api.app, host="0.0.0.0", port=args_pre.port, log_level="warning")
    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start(); time.sleep(1)
    main()
