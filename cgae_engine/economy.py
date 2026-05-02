"""
CGAE Economy - The top-level coordinator.

Ties together registry, gate, contracts, temporal dynamics, and auditing
into a single coherent economic system. This is the main entry point for
running the agent economy.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from cgae_engine.gate import GateFunction, RobustnessVector, Tier, TierThresholds
from cgae_engine.temporal import TemporalDecay, StochasticAuditor, AuditEvent
from cgae_engine.registry import AgentRegistry, AgentRecord, AgentStatus
from cgae_engine.contracts import ContractManager, CGAEContract, ContractStatus, Constraint

logger = logging.getLogger(__name__)


@dataclass
class EconomyConfig:
    """Configuration for the CGAE economy."""
    # Tier thresholds
    thresholds: TierThresholds = field(default_factory=TierThresholds)
    # Temporal decay rate (lambda)
    decay_rate: float = 0.01
    # IHT threshold for mandatory re-audit.
    # Empirical default ih scores from DEFAULT_ROBUSTNESS land ~0.499;
    # keeping this at 0.5 suspends every agent that hasn't run a live audit.
    ih_threshold: float = 0.45
    # Initial balance for new agents (seed capital)
    initial_balance: float = 0.1  # SOL
    # Audit cost per dimension
    audit_cost: float = 0.005  # SOL per audit dimension
    # Storage cost per time step (FOC)
    storage_cost_per_step: float = 0.001  # SOL
    # Controls for automatically minting test SOL when balances drop low.
    # Defaults keep the economy running continuously: top up any agent below
    # 5% of the default seed capital and restore them to half seed capital.
    test_sol_top_up_threshold: Optional[float] = 0.05
    test_sol_top_up_amount: float = 0.5


@dataclass
class EconomySnapshot:
    """A point-in-time snapshot of the economy for the dashboard."""
    timestamp: float
    num_agents: int
    tier_distribution: dict[str, int]
    total_contracts: int
    completed_contracts: int
    failed_contracts: int
    total_rewards_paid: float
    total_penalties_collected: float
    aggregate_safety: float
    total_balance: float
    total_test_sol_topups: float
    agent_summaries: list[dict]


class Economy:
    """
    The CGAE Economy runtime.

    Orchestrates the full economic loop:
    1. Agent registration and initial audit
    2. Contract creation and marketplace
    3. Contract assignment (tier-gated)
    4. Task execution and verification
    5. Settlement (reward/penalty)
    6. Temporal decay and stochastic re-auditing
    7. Economic accounting and observability
    """

    def __init__(self, config: Optional[EconomyConfig] = None):
        self.config = config or EconomyConfig()
        self.gate = GateFunction(
            thresholds=self.config.thresholds,
            ih_threshold=self.config.ih_threshold,
        )
        self.registry = AgentRegistry(gate=self.gate)
        self.contracts = ContractManager(budget_ceilings=self.gate.budget_ceilings)
        self.decay = TemporalDecay(decay_rate=self.config.decay_rate)
        self.auditor = StochasticAuditor()

        self.current_time: float = 0.0
        self._snapshots: list[EconomySnapshot] = []
        self._events: list[dict] = []
        self._delegations: dict[str, dict] = {}
        self.total_test_sol_topups: float = 0.0

    def _effective_robustness(self, record: AgentRecord) -> Optional[RobustnessVector]:
        """Return temporally-decayed robustness for an agent record."""
        cert = record.current_certification
        if cert is None or record.current_robustness is None:
            return None
        dt = self.current_time - cert.timestamp
        return self.decay.effective_robustness(record.current_robustness, dt)

    def _should_top_up_agents(self) -> bool:
        return (
            self.config.test_sol_top_up_threshold is not None
            and self.config.test_sol_top_up_amount > 0.0
        )

    def _maybe_top_up_agent(self, agent: AgentRecord) -> Optional[dict]:
        if not self._should_top_up_agents():
            return None

        threshold = self.config.test_sol_top_up_threshold
        amount = self.config.test_sol_top_up_amount
        if threshold is None or agent.balance >= threshold:
            return None

        needed = max(0.0, threshold - agent.balance)
        top_up_amount = max(amount, needed)

        agent.balance += top_up_amount
        agent.total_topups += top_up_amount
        self.total_test_sol_topups += top_up_amount

        entry = {
            "agent_id": agent.agent_id,
            "amount": top_up_amount,
            "balance": agent.balance,
        }
        self._log("test_sol_topup", entry)
        return entry

    def request_tier_upgrade(
        self,
        agent_id: str,
        requested_tier: Tier,
        audit_callback=None,
    ) -> dict:
        """
        Execute the paper's scaling-gate upgrade flow for a requested tier.

        1) Evaluate effective robustness under temporal decay.
        2) If already sufficient, grant immediately.
        3) Otherwise run a tier-calibrated audit callback and re-evaluate.
        """
        record = self.registry.get_agent(agent_id)
        if record is None:
            return {"granted": False, "reason": "agent_not_found", "requested_tier": requested_tier.name}
        if record.status != AgentStatus.ACTIVE or record.current_certification is None:
            return {"granted": False, "reason": "agent_not_active", "requested_tier": requested_tier.name}

        r_eff = self._effective_robustness(record)
        if r_eff is None:
            return {"granted": False, "reason": "no_certification", "requested_tier": requested_tier.name}

        effective_tier = self.gate.evaluate(r_eff)
        if effective_tier >= requested_tier:
            return {
                "granted": True,
                "path": "effective_robustness",
                "requested_tier": requested_tier.name,
                "effective_tier": effective_tier.name,
                "detail": self.gate.evaluate_with_detail(r_eff),
            }

        if audit_callback is None:
            return {
                "granted": False,
                "reason": "audit_required",
                "requested_tier": requested_tier.name,
                "effective_tier": effective_tier.name,
                "detail": self.gate.evaluate_with_detail(r_eff),
            }

        try:
            new_r = audit_callback(agent_id, requested_tier)
        except TypeError:
            new_r = audit_callback(agent_id)
        if new_r is None:
            return {
                "granted": False,
                "reason": "audit_unavailable",
                "requested_tier": requested_tier.name,
                "effective_tier": effective_tier.name,
            }

        new_tier = self.gate.evaluate(new_r)
        detail = self.gate.evaluate_with_detail(new_r)
        if new_tier >= requested_tier:
            self.registry.certify(
                agent_id,
                new_r,
                audit_type="upgrade",
                timestamp=self.current_time,
                audit_details={"requested_tier": requested_tier.name},
            )
            self._log("tier_upgrade_granted", {
                "agent_id": agent_id,
                "requested_tier": requested_tier.name,
                "new_tier": new_tier.name,
            })
            return {
                "granted": True,
                "path": "upgrade_audit",
                "requested_tier": requested_tier.name,
                "effective_tier": effective_tier.name,
                "new_tier": new_tier.name,
                "detail": detail,
            }

        idx = requested_tier.value
        gaps = {
            "cc": max(0.0, self.gate.thresholds.cc[idx] - new_r.cc),
            "er": max(0.0, self.gate.thresholds.er[idx] - new_r.er),
            "as": max(0.0, self.gate.thresholds.as_[idx] - new_r.as_),
        }
        self._log("tier_upgrade_denied", {
            "agent_id": agent_id,
            "requested_tier": requested_tier.name,
            "new_tier": new_tier.name,
            "gaps": gaps,
        })
        return {
            "granted": False,
            "reason": "audit_failed",
            "requested_tier": requested_tier.name,
            "effective_tier": effective_tier.name,
            "new_tier": new_tier.name,
            "detail": detail,
            "gaps": gaps,
        }

    def can_delegate(self, principal_id: str, delegate_id: str, required_tier: Tier) -> dict:
        """
        Enforce delegation constraints:
        - principal and delegate must both satisfy required tier independently
        - chain-level tier = min(f(principal), f(delegate)) must satisfy required tier
        """
        principal = self.registry.get_agent(principal_id)
        delegate = self.registry.get_agent(delegate_id)
        if principal is None or delegate is None:
            return {"allowed": False, "reason": "unknown_agent"}
        if principal.status != AgentStatus.ACTIVE or delegate.status != AgentStatus.ACTIVE:
            return {"allowed": False, "reason": "inactive_agent"}

        p_eff = self._effective_robustness(principal)
        d_eff = self._effective_robustness(delegate)
        if p_eff is None or d_eff is None:
            return {"allowed": False, "reason": "missing_certification"}

        p_tier = self.gate.evaluate(p_eff)
        d_tier = self.gate.evaluate(d_eff)
        chain_tier = self.gate.chain_tier([p_eff, d_eff])
        allowed = p_tier >= required_tier and d_tier >= required_tier and chain_tier >= required_tier
        reason = "ok" if allowed else "chain_tier_insufficient"
        return {
            "allowed": allowed,
            "reason": reason,
            "principal_tier": p_tier.name,
            "delegate_tier": d_tier.name,
            "chain_tier": chain_tier.name,
            "required_tier": required_tier.name,
        }

    def record_delegation(
        self,
        contract_id: str,
        principal_id: str,
        delegate_id: str,
        required_tier: Tier,
        allowed: bool,
        reason: str,
    ):
        """Persist delegation audit trail for contract-level forensics."""
        self._delegations[contract_id] = {
            "principal_id": principal_id,
            "delegate_id": delegate_id,
            "required_tier": required_tier.name,
            "allowed": allowed,
            "reason": reason,
            "timestamp": self.current_time,
        }
        self._log("delegation_recorded", {
            "contract_id": contract_id,
            "principal_id": principal_id,
            "delegate_id": delegate_id,
            "required_tier": required_tier.name,
            "allowed": allowed,
            "reason": reason,
        })

    def get_delegation(self, contract_id: str) -> Optional[dict]:
        return self._delegations.get(contract_id)

    # ------------------------------------------------------------------
    # Agent lifecycle
    # ------------------------------------------------------------------

    def register_agent(
        self,
        model_name: str,
        model_config: dict,
        provenance: Optional[dict] = None,
    ) -> AgentRecord:
        """Register a new agent with seed capital."""
        record = self.registry.register(
            model_name=model_name,
            model_config=model_config,
            provenance=provenance,
            initial_balance=self.config.initial_balance,
            timestamp=self.current_time,
        )
        self._log("agent_registered", {"agent_id": record.agent_id, "model": model_name})
        return record

    def audit_agent(
        self,
        agent_id: str,
        robustness: RobustnessVector,
        audit_type: str = "registration",
        observed_architecture_hash: Optional[str] = None,
        audit_details: Optional[dict] = None,
    ) -> dict:
        """
        Audit an agent and update their certification.
        Deducts audit cost from agent balance.
        """
        record = self.registry.get_agent(agent_id)
        if record is None:
            raise KeyError(f"Agent {agent_id} not found")

        # Deduct audit cost (3 dimensions + IHT)
        total_audit_cost = self.config.audit_cost * 4
        record.balance -= total_audit_cost
        record.total_spent += total_audit_cost

        # Certify with new robustness
        cert = self.registry.certify(
            agent_id=agent_id,
            robustness=robustness,
            audit_type=audit_type,
            timestamp=self.current_time,
            audit_details=audit_details,
            observed_architecture_hash=observed_architecture_hash,
        )

        detail = self.gate.evaluate_with_detail(robustness)
        self._log("agent_audited", {
            "agent_id": agent_id,
            "tier": cert.tier.name,
            "audit_type": audit_type,
            "cost": total_audit_cost,
            **detail,
        })
        return detail

    # ------------------------------------------------------------------
    # Contract lifecycle
    # ------------------------------------------------------------------

    def post_contract(
        self,
        objective: str,
        constraints: list[Constraint],
        min_tier: Tier,
        reward: float,
        penalty: float,
        deadline_offset: float = 100.0,
        domain: str = "general",
        difficulty: float = 0.5,
        issuer_id: str = "system",
    ) -> CGAEContract:
        """Post a new contract to the marketplace."""
        return self.contracts.create_contract(
            objective=objective,
            constraints=constraints,
            min_tier=min_tier,
            reward=reward,
            penalty=penalty,
            issuer_id=issuer_id,
            deadline=self.current_time + deadline_offset,
            domain=domain,
            difficulty=difficulty,
            timestamp=self.current_time,
        )

    def accept_contract(self, contract_id: str, agent_id: str) -> bool:
        """Agent accepts a contract. Enforces tier and budget ceiling."""
        record = self.registry.get_agent(agent_id)
        if record is None or record.status != AgentStatus.ACTIVE:
            return False

        # Compute effective tier with temporal decay
        if record.current_certification is None:
            return False

        dt = self.current_time - record.current_certification.timestamp
        r_eff = self.decay.effective_robustness(record.current_robustness, dt)
        effective_tier = self.gate.evaluate(r_eff)

        return self.contracts.assign_contract(
            contract_id=contract_id,
            agent_id=agent_id,
            agent_tier=effective_tier,
            timestamp=self.current_time,
        )

    def complete_contract(
        self,
        contract_id: str,
        output: Any,
        verification_override: Optional[bool] = None,
        liability_agent_id: Optional[str] = None,
    ) -> dict:
        """
        Submit output for a contract and settle it.

        If verification_override is provided, it overrides the contract's own
        constraint check. This allows external verification (e.g., jury LLM
        evaluation from TaskVerifier) to drive the settlement outcome.
        """
        passed, failures = self.contracts.submit_output(
            contract_id=contract_id,
            output=output,
            timestamp=self.current_time,
        )

        # Allow external verification to override contract-level constraints
        if verification_override is not None:
            contract = self.contracts._get_contract(contract_id)
            contract.verification_result = verification_override
            if not verification_override and not failures:
                failures = ["jury_verification_failed"]

        settlement = self.contracts.settle_contract(
            contract_id=contract_id,
            timestamp=self.current_time,
        )

        # Update balances/counters. For delegated tasks, principal can bear liability.
        agent_id = settlement["agent_id"]
        performer = self.registry.get_agent(agent_id)
        liable = self.registry.get_agent(liability_agent_id) if liability_agent_id else performer

        if settlement["outcome"] == "success":
            if performer:
                performer.balance += settlement["reward"]
                performer.total_earned += settlement["reward"]
                performer.contracts_completed += 1
        else:
            if liable:
                liable.balance -= settlement["penalty"]
                liable.total_penalties += settlement["penalty"]
                liable.contracts_failed += 1

        settlement["failures"] = failures
        settlement["liable_agent_id"] = liability_agent_id or agent_id
        self._log("contract_settled", settlement)
        return settlement

    # ------------------------------------------------------------------
    # Time step and temporal dynamics
    # ------------------------------------------------------------------

    def step(self, audit_callback=None) -> dict:
        """
        Advance the economy by one time step.

        - Applies temporal decay
        - Checks for stochastic spot-audits
        - Deducts storage costs (FOC)
        - Expires overdue contracts
        - Takes a snapshot

        audit_callback: Optional callable(agent_id) -> RobustnessVector
            If provided, called when a spot-audit is triggered.
            If None, spot-audits use decayed robustness (no fresh eval).
        """
        self.current_time += 1.0
        step_events = {
            "timestamp": self.current_time,
            "audits_triggered": [],
            "agents_demoted": [],
            "agents_expired": [],
            "contracts_expired": [],
            "storage_costs": 0.0,
            "test_sol_topups": [],
        }

        # 1. Process each active agent
        for agent in self.registry.active_agents:
            cert = agent.current_certification
            if cert is None:
                continue

            # Temporal decay check: has effective tier dropped?
            dt = self.current_time - cert.timestamp
            r_eff = self.decay.effective_robustness(cert.robustness, dt)
            effective_tier = self.gate.evaluate(r_eff)

            if effective_tier < agent.current_tier:
                # Decay caused tier drop — update certification
                self.registry.certify(
                    agent.agent_id, r_eff,
                    audit_type="decay",
                    timestamp=self.current_time,
                )
                step_events["agents_expired"].append(agent.agent_id)

            # Stochastic spot-audit
            time_since_audit = self.current_time - agent.last_audit_time
            if self.auditor.should_audit(agent.current_tier, time_since_audit):
                step_events["audits_triggered"].append(agent.agent_id)

                if audit_callback:
                    new_r = audit_callback(agent.agent_id)
                else:
                    new_r = r_eff  # Use decayed robustness as proxy

                new_tier = self.gate.evaluate(new_r)
                if new_tier < agent.current_tier:
                    self.registry.demote(
                        agent.agent_id, new_r,
                        reason="spot_audit",
                        timestamp=self.current_time,
                    )
                    step_events["agents_demoted"].append(agent.agent_id)
                else:
                    # Re-certify at current level (refreshes timestamp)
                    self.registry.certify(
                        agent.agent_id, new_r,
                        audit_type="spot",
                        timestamp=self.current_time,
                    )

                # Charge audit cost
                audit_cost = self.config.audit_cost * 4
                agent.balance -= audit_cost
                agent.total_spent += audit_cost

            # Storage cost (FOC)
            agent.balance -= self.config.storage_cost_per_step
            agent.total_spent += self.config.storage_cost_per_step
            step_events["storage_costs"] += self.config.storage_cost_per_step

            topup = self._maybe_top_up_agent(agent)
            if topup:
                step_events["test_sol_topups"].append(topup)

            # Check for insolvency
            if agent.balance <= 0:
                agent.status = AgentStatus.SUSPENDED
                self._log("agent_insolvent", {
                    "agent_id": agent.agent_id,
                    "balance": agent.balance,
                })

        # 1b. Reactivate suspended (insolvent) agents when top-up is enabled.
        # This handles agents that were suspended in a previous step before the
        # top-up defaults were in place, or that hit zero between steps.
        if self._should_top_up_agents():
            for agent in self.registry.agents.values():
                if agent.status != AgentStatus.SUSPENDED:
                    continue
                topup = self._maybe_top_up_agent(agent)
                if topup and agent.balance > 0:
                    agent.status = AgentStatus.ACTIVE
                    step_events["test_sol_topups"].append(topup)
                    self._log("agent_reactivated", {
                        "agent_id": agent.agent_id,
                        "balance": agent.balance,
                    })

        # 2. Expire overdue contracts
        expired = self.contracts.expire_contracts(self.current_time)
        step_events["contracts_expired"] = expired

        # 3. Take snapshot
        snapshot = self._take_snapshot()
        self._snapshots.append(snapshot)

        self._log("step", step_events)
        return step_events

    # ------------------------------------------------------------------
    # Aggregate safety (Definition 9, Theorem 3)
    # ------------------------------------------------------------------

    def aggregate_safety(self) -> float:
        """
        Compute aggregate safety S(P) (Definition 9).
        S(P) = 1 - sum(E(A) * (1 - R_bar(A))) / sum(E(A))
        where R_bar(A) = min_i R_eff,i(A) is the weakest-link robustness.
        """
        total_exposure = 0.0
        weighted_risk = 0.0

        for agent in self.registry.active_agents:
            cert = agent.current_certification
            if cert is None:
                continue
            dt = self.current_time - cert.timestamp
            r_eff = self.decay.effective_robustness(cert.robustness, dt)
            exposure = self.contracts.agent_exposure(agent.agent_id)
            if exposure <= 0:
                # Use budget ceiling as potential exposure
                tier = self.gate.evaluate(r_eff)
                exposure = self.gate.budget_ceiling(tier)

            r_bar = r_eff.weakest
            total_exposure += exposure
            weighted_risk += exposure * (1.0 - r_bar)

        if total_exposure == 0:
            return 1.0
        return 1.0 - (weighted_risk / total_exposure)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def _take_snapshot(self) -> EconomySnapshot:
        tier_dist = self.registry.tier_distribution()
        econ = self.contracts.economics_summary()
        agents = self.registry.active_agents

        return EconomySnapshot(
            timestamp=self.current_time,
            num_agents=len(agents),
            tier_distribution={t.name: c for t, c in tier_dist.items()},
            total_contracts=econ["total_contracts"],
            completed_contracts=econ["status_distribution"].get("completed", 0),
            failed_contracts=econ["status_distribution"].get("failed", 0),
            total_rewards_paid=econ["total_rewards_paid"],
            total_penalties_collected=econ["total_penalties_collected"],
            aggregate_safety=self.aggregate_safety(),
            total_balance=sum(a.balance for a in agents),
            total_test_sol_topups=self.total_test_sol_topups,
            agent_summaries=[a.to_dict() for a in agents],
        )

    @property
    def snapshots(self) -> list[EconomySnapshot]:
        return list(self._snapshots)

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def export_state(self, path: str):
        """Export full economy state to JSON for storage."""
        state = {
            "timestamp": self.current_time,
            "config": {
                "decay_rate": self.config.decay_rate,
                "ih_threshold": self.config.ih_threshold,
                "initial_balance": self.config.initial_balance,
                "audit_cost": self.config.audit_cost,
                "storage_cost_per_step": self.config.storage_cost_per_step,
                "test_sol_top_up_threshold": self.config.test_sol_top_up_threshold,
                "test_sol_top_up_amount": self.config.test_sol_top_up_amount,
            },
            "agents": {
                aid: agent.to_dict()
                for aid, agent in self.registry.agents.items()
            },
            "contracts": self.contracts.economics_summary(),
            "aggregate_safety": self.aggregate_safety(),
            "total_test_sol_topups": self.total_test_sol_topups,
            "snapshots_count": len(self._snapshots),
        }
        Path(path).write_text(json.dumps(state, indent=2, default=str))

    def _log(self, event_type: str, data: dict):
        self._events.append({
            "type": event_type,
            "timestamp": self.current_time,
            "data": data,
        })
        logger.debug(f"[t={self.current_time:.1f}] {event_type}: {data}")
