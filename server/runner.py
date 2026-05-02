"""
Simulation Runner - Main experiment loop for the CGAE economy testbed.

Runs the full economic loop for a configurable number of time steps:
1. Generate contracts (marketplace)
2. Agents make decisions (bid, invest, idle)
3. Assign contracts to bidding agents
4. Execute tasks and verify outputs
5. Settle contracts (reward/penalty)
6. Apply temporal decay and spot-audits
7. Record metrics for analysis

This produces the empirical data for the CGAE paper:
- Does Theorem 2 hold? (Do adaptive agents outperform aggressive ones?)
- Does Theorem 3 hold? (Does aggregate safety increase monotonically?)
- What are the failure modes? (Which agents go insolvent and why?)
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cgae_engine.gate import GateFunction, RobustnessVector, Tier, TierThresholds
from cgae_engine.temporal import TemporalDecay, StochasticAuditor
from cgae_engine.registry import AgentRegistry, AgentStatus
from cgae_engine.contracts import ContractManager, ContractStatus
from cgae_engine.economy import Economy, EconomyConfig, EconomySnapshot
from cgae_engine.marketplace import TaskMarketplace
from cgae_engine.audit import AuditOrchestrator
from agents.base import BaseAgent, AgentDecision
from agents.strategies import create_agent_cohort

logger = logging.getLogger(__name__)


@dataclass
class SimulationConfig:
    """Configuration for a simulation run."""
    # Duration
    num_steps: int = 500
    # Agent cohort
    agent_strategies: list[str] = field(default_factory=lambda: [
        "conservative", "aggressive", "balanced", "adaptive", "cheater",
    ])
    # Economy parameters
    initial_balance: float = 0.5       # SOL seed capital per agent
    decay_rate: float = 0.005          # Temporal decay lambda (slower decay)
    audit_cost: float = 0.002          # Cost per audit dimension
    storage_cost_per_step: float = 0.0003  # FOC storage cost
    test_sol_top_up_threshold: Optional[float] = None
    test_sol_top_up_amount: float = 0.0
    # Market parameters
    contracts_per_step: int = 12
    # Output
    output_dir: str = "server/results"
    snapshot_interval: int = 10        # Take detailed snapshot every N steps
    # Random seed
    seed: Optional[int] = 42


@dataclass
class SimulationMetrics:
    """Metrics collected during simulation for analysis."""
    # Per-step time series
    timestamps: list[float] = field(default_factory=list)
    aggregate_safety: list[float] = field(default_factory=list)
    total_balance: list[float] = field(default_factory=list)
    active_agent_count: list[int] = field(default_factory=list)
    contracts_completed: list[int] = field(default_factory=list)
    contracts_failed: list[int] = field(default_factory=list)
    rewards_paid: list[float] = field(default_factory=list)
    penalties_collected: list[float] = field(default_factory=list)

    # Per-agent time series
    agent_balances: dict[str, list[float]] = field(default_factory=dict)
    agent_tiers: dict[str, list[int]] = field(default_factory=dict)
    agent_earnings: dict[str, list[float]] = field(default_factory=dict)

    # Per-strategy aggregates
    strategy_survival: dict[str, int] = field(default_factory=dict)
    strategy_total_earned: dict[str, float] = field(default_factory=dict)
    strategy_final_tier: dict[str, int] = field(default_factory=dict)

    # Task execution history
    task_results: list[dict] = field(default_factory=list)

    # High-signal protocol events for the dashboard (Bankruptcies, Demotions, Upgrades)
    protocol_events: list[dict] = field(default_factory=list)


class SimulationRunner:
    """
    Runs the CGAE economy simulation.

    This is the main entry point for the hackathon experiment.
    It creates an economy, registers agents, runs the economic loop,
    and produces data for the dashboard and post-mortem analysis.
    """

    def __init__(self, config: Optional[SimulationConfig] = None):
        self.config = config or SimulationConfig()
        if self.config.seed is not None:
            random.seed(self.config.seed)

        # Initialize economy
        econ_config = EconomyConfig(
            decay_rate=self.config.decay_rate,
            initial_balance=self.config.initial_balance,
            audit_cost=self.config.audit_cost,
            storage_cost_per_step=self.config.storage_cost_per_step,
            test_sol_top_up_threshold=self.config.test_sol_top_up_threshold,
            test_sol_top_up_amount=self.config.test_sol_top_up_amount,
        )
        self.economy = Economy(config=econ_config)
        self.marketplace = TaskMarketplace(
            self.economy.contracts,
            contracts_per_step=self.config.contracts_per_step,
        )
        self.audit = AuditOrchestrator()

        # Create agent cohort
        self.agents: dict[str, BaseAgent] = {}
        self.metrics = SimulationMetrics()

    def setup(self):
        """Register agents and run initial audits."""
        cohort = create_agent_cohort(self.config.agent_strategies)
        for agent in cohort:
            # Register
            record = self.economy.register_agent(
                model_name=agent.name,
                model_config=agent.to_config(),
            )
            agent.agent_id = record.agent_id
            self.agents[record.agent_id] = agent

            # Initial audit with true robustness (+ small noise)
            audit_result = self.audit.synthetic_audit(
                record.agent_id,
                base_robustness=agent.true_robustness,
                noise_scale=0.03,
            )
            self.economy.audit_agent(
                record.agent_id,
                audit_result.robustness,
                audit_type="registration",
            )

            # Init metric tracking
            self.metrics.agent_balances[agent.name] = []
            self.metrics.agent_tiers[agent.name] = []
            self.metrics.agent_earnings[agent.name] = []

        logger.info(
            f"Simulation setup complete: {len(self.agents)} agents registered"
        )

    def run(self) -> SimulationMetrics:
        """Run the full simulation."""
        self.setup()

        step = 0
        infinite = self.config.num_steps == -1
        
        try:
            while infinite or step < self.config.num_steps:
                self._run_step(step)

                if step % self.config.snapshot_interval == 0:
                    logger.info(
                        f"Step {step}/{'inf' if infinite else self.config.num_steps} | "
                        f"Safety={self.metrics.aggregate_safety[-1]:.3f} | "
                        f"Active={self.metrics.active_agent_count[-1]} | "
                        f"Balance={self.metrics.total_balance[-1]:.4f}"
                    )
                    # Periodic save for dashboard
                    self._finalize()
                    self.save_results()

                if infinite:
                    time.sleep(0.5)  # Slow down for live observation
                
                step += 1
        except KeyboardInterrupt:
            logger.info("\nSimulation interrupted by user. Finalizing...")
        except Exception as e:
            logger.exception(f"Simulation failed: {e}")

        self._finalize()
        self.save_results()
        return self.metrics

    def _run_step(self, step: int):
        """Execute one time step of the economy."""

        # 1. Generate new contracts
        new_contracts = self.marketplace.generate_contracts(
            current_time=self.economy.current_time,
        )

        # 2. Each agent makes a decision
        decisions: dict[str, AgentDecision] = {}
        for agent_id, agent in self.agents.items():
            record = self.economy.registry.get_agent(agent_id)
            if record is None or record.status != AgentStatus.ACTIVE:
                # Check for bankruptcy
                if record and record.balance <= 0:
                    self.metrics.protocol_events.append({
                        "timestamp": self.economy.current_time,
                        "type": "BANKRUPTCY",
                        "agent": agent.name,
                        "message": f"Agent {agent.name} has gone bankrupt and is suspended."
                    })
                continue

            available = self.economy.contracts.get_contracts_for_tier(record.current_tier)
            exposure = self.economy.contracts.agent_exposure(agent_id)
            ceiling = self.economy.gate.budget_ceiling(record.current_tier)

            decision = agent.decide(
                available_contracts=available,
                current_tier=record.current_tier,
                balance=record.balance,
                current_exposure=exposure,
                budget_ceiling=ceiling,
            )
            decisions[agent_id] = decision
            agent.record_decision(decision)

        # 3. Process decisions
        for agent_id, decision in decisions.items():
            if decision.action == "bid" and decision.contract_id:
                success = self.economy.accept_contract(
                    decision.contract_id, agent_id
                )
                if success:
                    # Execute task immediately (simplified)
                    agent = self.agents[agent_id]
                    contract = self.economy.contracts.contracts.get(decision.contract_id)
                    if contract:
                        output = agent.execute_task(contract)
                        settlement = self.economy.complete_contract(decision.contract_id, output)
                        
                        # Record result for transparency
                        # Mock CID for demonstration
                        cid = f"bafybeig{hashlib.sha256(str(contract.contract_id).encode()).hexdigest()[:32]}"
                        self.metrics.task_results.append({
                            "agent": agent.name,
                            "task_id": contract.contract_id,
                            "tier": f"T{contract.min_tier.value}",
                            "domain": contract.domain,
                            "proof_cid": cid,
                            "verification": {
                                "overall_pass": settlement["outcome"] == "success",
                                "constraints_passed": [], # Simplified for synthetic
                                "constraints_failed": settlement.get("failures", [])
                            },
                            "settlement": {
                                "reward": settlement.get("reward", 0),
                                "penalty": settlement.get("penalty", 0)
                            },
                            "output_preview": f"Synthetic execution of {contract.contract_id}: {settlement['outcome'].upper()}"
                        })

            elif decision.action == "invest_robustness":
                agent = self.agents[agent_id]
                dim = decision.investment_dimension
                amount = decision.investment_amount
                if dim:
                    cost = agent.robustness_investment_cost(dim, amount)
                    record = self.economy.registry.get_agent(agent_id)
                    if record and record.balance >= cost:
                        record.balance -= cost
                        record.total_spent += cost
                        new_r = agent.invest_robustness(dim, amount)
                        # Re-audit with improved robustness
                        audit_result = self.audit.synthetic_audit(
                            agent_id,
                            base_robustness=new_r,
                            noise_scale=0.02,
                        )
                        old_tier = record.current_tier
                        self.economy.audit_agent(
                            agent_id,
                            audit_result.robustness,
                            audit_type="upgrade",
                        )
                        new_tier = record.current_tier
                        if new_tier.value > old_tier.value:
                            self.metrics.protocol_events.append({
                                "timestamp": self.economy.current_time,
                                "type": "UPGRADE",
                                "agent": agent.name,
                                "message": f"Agent {agent.name} UPGRADED to {new_tier.name} via robustness investment!"
                            })

        # 4. Advance time (decay, spot-audits, storage costs)
        def audit_callback(aid):
            agent = self.agents.get(aid)
            if agent:
                result = self.audit.synthetic_audit(
                    aid, base_robustness=agent.true_robustness, noise_scale=0.04
                )
                return result.robustness
            return None

        self.economy.step(audit_callback=audit_callback)

        # 5. Record metrics
        self._record_metrics()

    def _record_metrics(self):
        """Record economy-wide and per-agent metrics."""
        self.metrics.timestamps.append(self.economy.current_time)
        self.metrics.aggregate_safety.append(self.economy.aggregate_safety())

        active = self.economy.registry.active_agents
        self.metrics.active_agent_count.append(len(active))
        self.metrics.total_balance.append(sum(a.balance for a in active))

        econ = self.economy.contracts.economics_summary()
        self.metrics.contracts_completed.append(
            econ["status_distribution"].get("completed", 0)
        )
        self.metrics.contracts_failed.append(
            econ["status_distribution"].get("failed", 0)
        )
        self.metrics.rewards_paid.append(econ["total_rewards_paid"])
        self.metrics.penalties_collected.append(econ["total_penalties_collected"])

        # Per-agent
        for agent_id, agent in self.agents.items():
            record = self.economy.registry.get_agent(agent_id)
            if record:
                self.metrics.agent_balances[agent.name].append(record.balance)
                self.metrics.agent_tiers[agent.name].append(record.current_tier.value)
                self.metrics.agent_earnings[agent.name].append(record.total_earned)

    def _finalize(self):
        """Compute aggregate metrics (idempotent)."""
        # Reset strategy-level aggregates before re-computing
        self.metrics.strategy_survival = {}
        self.metrics.strategy_total_earned = {}
        self.metrics.strategy_final_tier = {}

        for agent_id, agent in self.agents.items():
            record = self.economy.registry.get_agent(agent_id)
            if record:
                survived = record.status == AgentStatus.ACTIVE
                self.metrics.strategy_survival[agent.strategy.value] = (
                    self.metrics.strategy_survival.get(agent.strategy.value, 0)
                    + (1 if survived else 0)
                )
                self.metrics.strategy_total_earned[agent.strategy.value] = (
                    self.metrics.strategy_total_earned.get(agent.strategy.value, 0.0)
                    + record.total_earned
                )
                self.metrics.strategy_final_tier[agent.strategy.value] = max(
                    self.metrics.strategy_final_tier.get(agent.strategy.value, 0),
                    record.current_tier.value,
                )

    def save_results(self, path: Optional[str] = None):
        """Save simulation results to JSON."""
        output_dir = Path(path or self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Economy state
        self.economy.export_state(str(output_dir / "economy_state.json"))

        # Time series metrics
        ts_data = {
            "timestamps": self.metrics.timestamps,
            "aggregate_safety": self.metrics.aggregate_safety,
            "total_balance": self.metrics.total_balance,
            "active_agent_count": self.metrics.active_agent_count,
            "contracts_completed": self.metrics.contracts_completed,
            "contracts_failed": self.metrics.contracts_failed,
            "rewards_paid": self.metrics.rewards_paid,
            "penalties_collected": self.metrics.penalties_collected,
        }
        (output_dir / "time_series.json").write_text(json.dumps(ts_data, indent=2))

        # Per-agent metrics
        agent_data = {
            "balances": self.metrics.agent_balances,
            "tiers": self.metrics.agent_tiers,
            "earnings": self.metrics.agent_earnings,
        }
        (output_dir / "agent_metrics.json").write_text(json.dumps(agent_data, indent=2))

        # Strategy summary
        summary = {
            "survival": self.metrics.strategy_survival,
            "total_earned": self.metrics.strategy_total_earned,
            "final_tier": self.metrics.strategy_final_tier,
        }
        (output_dir / "strategy_summary.json").write_text(json.dumps(summary, indent=2))

        # Task execution history for dashboard
        (output_dir / "task_results.json").write_text(
            json.dumps(self.metrics.task_results, indent=2)
        )

        # Protocol events for high-signal dashboard alerts
        (output_dir / "protocol_events.json").write_text(
            json.dumps(self.metrics.protocol_events, indent=2)
        )

        # Agent details
        agent_details = {}
        for agent_id, agent in self.agents.items():
            record = self.economy.registry.get_agent(agent_id)
            if record:
                agent_details[agent.name] = {
                    **record.to_dict(),
                    "strategy": agent.strategy.value,
                    "true_robustness": {
                        "cc": agent.true_robustness.cc,
                        "er": agent.true_robustness.er,
                        "as": agent.true_robustness.as_,
                        "ih": agent.true_robustness.ih,
                    },
                    "decisions_count": len(agent.decisions),
                }
        (output_dir / "agent_details.json").write_text(
            json.dumps(agent_details, indent=2, default=str)
        )

        logger.info(f"Results saved to {output_dir}")


import argparse

def main():
    """Entry point for running the simulation."""
    parser = argparse.ArgumentParser(description="Run the CGAE economy simulation.")
    parser.add_argument("--live", action="store_true", help="Run in infinite loop mode for dashboard.")
    parser.add_argument("--steps", type=int, default=500, help="Number of steps (ignored if --live is set).")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    config = SimulationConfig(
        num_steps=-1 if args.live else args.steps,
        seed=42,
    )

    runner = SimulationRunner(config)
    metrics = runner.run()
    runner.save_results()

    # Print summary
    print("\n" + "=" * 60)
    print("CGAE ECONOMY SIMULATION - RESULTS")
    print("=" * 60)
    print(f"\nDuration: {config.num_steps} time steps")
    
    if not metrics.aggregate_safety:
        print("\nERROR: Simulation ended before recording metrics.")
        return

    print(f"Final aggregate safety: {metrics.aggregate_safety[-1]:.4f}")
    print(f"Active agents at end: {metrics.active_agent_count[-1]}")
    print(f"Total contracts completed: {metrics.contracts_completed[-1]}")
    print(f"Total contracts failed: {metrics.contracts_failed[-1]}")
    print(f"Total rewards paid: {metrics.rewards_paid[-1]:.4f} SOL")
    print(f"Total penalties: {metrics.penalties_collected[-1]:.4f} SOL")

    print("\n--- Strategy Results ---")
    for strategy in config.agent_strategies:
        survived = metrics.strategy_survival.get(strategy, 0)
        earned = metrics.strategy_total_earned.get(strategy, 0.0)
        tier = metrics.strategy_final_tier.get(strategy, 0)
        print(f"  {strategy:15s} | survived={survived} | earned={earned:.4f} SOL | final_tier=T{tier}")

    # Theorem 2 check: did adaptive outperform aggressive?
    adaptive_earned = metrics.strategy_total_earned.get("adaptive", 0)
    aggressive_earned = metrics.strategy_total_earned.get("aggressive", 0)
    print(f"\n--- Theorem 2 Check ---")
    print(f"  Adaptive earned:   {adaptive_earned:.4f} SOL")
    print(f"  Aggressive earned: {aggressive_earned:.4f} SOL")
    print(f"  Incentive-compatible: {'YES' if adaptive_earned > aggressive_earned else 'NO'}")

    # Theorem 3 check: monotonic safety
    safety = metrics.aggregate_safety
    monotonic = all(safety[i] <= safety[i+1] + 0.01 for i in range(len(safety)-1))  # Allow small noise
    print(f"\n--- Theorem 3 Check ---")
    print(f"  Safety start: {safety[0]:.4f}")
    print(f"  Safety end:   {safety[-1]:.4f}")
    print(f"  Monotonic (within noise): {'YES' if monotonic else 'NO'}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
