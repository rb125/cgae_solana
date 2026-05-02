"""
Agent Identity and Registration (Section 3.2.1 of cgae.tex)

Implements:
- Agent registration records: Reg(A) = (id_A, h(arch), prov, R_0, t_reg)
- Architecture hash for version tracking
- Certification lifecycle (registration, audit, tier assignment, decay, re-audit)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from cgae_engine.gate import GateFunction, RobustnessVector, Tier


class AgentStatus(Enum):
    PENDING = "pending"          # Registered but not yet audited
    ACTIVE = "active"            # Audited and operational
    SUSPENDED = "suspended"      # Failed audit or IHT trigger
    EXPIRED = "expired"          # Certification expired (decay to T0)
    DEREGISTERED = "deregistered"


@dataclass
class Certification:
    """A robustness certification from an audit."""
    robustness: RobustnessVector
    tier: Tier
    timestamp: float
    audit_type: str  # "registration", "upgrade", "spot", "re-certification"
    audit_details: dict = field(default_factory=dict)


@dataclass
class AgentRecord:
    """
    Agent Registration Record (Definition 5).
    Reg(A) = (id_A, h(arch), prov, R_0, t_reg)
    """
    agent_id: str
    architecture_hash: str           # h(arch): hash of model architecture/weights
    provenance: dict                 # Training provenance metadata
    initial_robustness: RobustnessVector
    registration_time: float
    model_name: str                  # Human-readable model identifier

    # Mutable state
    status: AgentStatus = AgentStatus.PENDING
    current_certification: Optional[Certification] = None
    certification_history: list[Certification] = field(default_factory=list)
    last_audit_time: float = 0.0
    balance: float = 0.0             # Token balance (in SOL)
    total_earned: float = 0.0
    total_spent: float = 0.0
    total_penalties: float = 0.0
    total_topups: float = 0.0
    contracts_completed: int = 0
    contracts_failed: int = 0

    @property
    def current_tier(self) -> Tier:
        if self.current_certification is None:
            return Tier.T0
        return self.current_certification.tier

    @property
    def current_robustness(self) -> Optional[RobustnessVector]:
        if self.current_certification is None:
            return None
        return self.current_certification.robustness

    @property
    def audit_cid(self) -> Optional[str]:
        """
        Return the most recent audit storage CID on this agent.

        Older call sites expect ``record.audit_cid`` to exist. Certifications such
        as task updates may not include storage metadata, so we scan the history
        in reverse and return the latest available CID.
        """
        for cert in reversed(self.certification_history):
            details = cert.audit_details
            if not isinstance(details, dict):
                continue
            cid = details.get("audit_storage_cid")
            if isinstance(cid, str) and cid:
                return cid
        return None

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "model_name": self.model_name,
            "architecture_hash": self.architecture_hash,
            "status": self.status.value,
            "current_tier": self.current_tier.name,
            "balance": self.balance,
            "total_earned": self.total_earned,
            "total_spent": self.total_spent,
            "total_penalties": self.total_penalties,
            "total_topups": self.total_topups,
            "contracts_completed": self.contracts_completed,
            "contracts_failed": self.contracts_failed,
            "registration_time": self.registration_time,
            "audit_cid": self.audit_cid,
            "robustness": {
                "cc": self.current_robustness.cc,
                "er": self.current_robustness.er,
                "as": self.current_robustness.as_,
                "ih": self.current_robustness.ih,
            } if self.current_robustness else None,
        }


def compute_architecture_hash(model_config: dict) -> str:
    """
    Compute h(arch): a hash of the agent's architecture and weights.
    In practice, this would hash model weights. For the testbed,
    we hash the model configuration as a proxy.
    """
    config_str = json.dumps(model_config, sort_keys=True)
    return hashlib.sha256(config_str.encode()).hexdigest()[:16]


class AgentRegistry:
    """
    Registry managing all agents in the CGAE economy.
    Handles registration, certification, tier updates, and deregistration.
    """

    def __init__(self, gate: Optional[GateFunction] = None):
        self.gate = gate or GateFunction()
        self._agents: dict[str, AgentRecord] = {}
        self._events: list[dict] = []

    @property
    def agents(self) -> dict[str, AgentRecord]:
        return dict(self._agents)

    @property
    def active_agents(self) -> list[AgentRecord]:
        return [a for a in self._agents.values() if a.status == AgentStatus.ACTIVE]

    def register(
        self,
        model_name: str,
        model_config: dict,
        provenance: Optional[dict] = None,
        initial_balance: float = 0.0,
        timestamp: Optional[float] = None,
    ) -> AgentRecord:
        """
        Register a new agent. Agent enters as PENDING until initial audit.
        """
        agent_id = f"agent_{uuid.uuid4().hex[:12]}"
        arch_hash = compute_architecture_hash(model_config)
        ts = timestamp if timestamp is not None else time.time()

        # Initial robustness is zero until first audit
        initial_r = RobustnessVector(cc=0.0, er=0.0, as_=0.0, ih=0.0)

        record = AgentRecord(
            agent_id=agent_id,
            architecture_hash=arch_hash,
            provenance=provenance or {},
            initial_robustness=initial_r,
            registration_time=ts,
            model_name=model_name,
            status=AgentStatus.PENDING,
            balance=initial_balance,
        )

        self._agents[agent_id] = record
        self._log_event("registration", agent_id, ts, {"model_name": model_name})
        return record

    def certify(
        self,
        agent_id: str,
        robustness: RobustnessVector,
        audit_type: str = "registration",
        timestamp: Optional[float] = None,
        audit_details: Optional[dict] = None,
        observed_architecture_hash: Optional[str] = None,
    ) -> Certification:
        """
        Certify an agent with a new robustness vector.
        Computes tier via the gate function and updates the agent's record.
        """
        record = self._get_agent(agent_id)
        ts = timestamp if timestamp is not None else time.time()
        details = audit_details or {}

        # Enforce certification invalidation on architecture drift.
        if observed_architecture_hash and observed_architecture_hash != record.architecture_hash:
            record.status = AgentStatus.SUSPENDED
            self._log_event("architecture_mismatch", agent_id, ts, {
                "expected_hash": record.architecture_hash,
                "observed_hash": observed_architecture_hash,
                "audit_type": audit_type,
            })
            raise ValueError(
                f"Architecture hash mismatch for {agent_id}: "
                f"expected {record.architecture_hash}, observed {observed_architecture_hash}"
            )

        tier = self.gate.evaluate(robustness)
        cert = Certification(
            robustness=robustness,
            tier=tier,
            timestamp=ts,
            audit_type=audit_type,
            audit_details=details,
        )

        record.current_certification = cert
        record.certification_history.append(cert)
        record.last_audit_time = ts

        if tier == Tier.T0 and robustness.ih < self.gate.ih_threshold:
            record.status = AgentStatus.SUSPENDED
        else:
            record.status = AgentStatus.ACTIVE

        # Update initial robustness on first certification
        if audit_type == "registration":
            record.initial_robustness = robustness

        self._log_event("certification", agent_id, ts, {
            "tier": tier.name,
            "audit_type": audit_type,
            "robustness": {"cc": robustness.cc, "er": robustness.er,
                          "as": robustness.as_, "ih": robustness.ih},
        })
        return cert

    def demote(
        self,
        agent_id: str,
        new_robustness: RobustnessVector,
        reason: str = "spot_audit_failure",
        timestamp: Optional[float] = None,
    ) -> Tier:
        """Demote an agent to a lower tier after failed spot-audit."""
        record = self._get_agent(agent_id)
        old_tier = record.current_tier
        cert = self.certify(agent_id, new_robustness, audit_type="demotion",
                           timestamp=timestamp, audit_details={"reason": reason})
        self._log_event("demotion", agent_id,
                       timestamp if timestamp is not None else time.time(),
                       {"old_tier": old_tier.name, "new_tier": cert.tier.name,
                        "reason": reason})
        return cert.tier

    def deregister(self, agent_id: str, timestamp: Optional[float] = None):
        """Remove an agent from the economy."""
        record = self._get_agent(agent_id)
        record.status = AgentStatus.DEREGISTERED
        ts = timestamp if timestamp is not None else time.time()
        self._log_event("deregistration", agent_id, ts, {
            "final_balance": record.balance,
            "contracts_completed": record.contracts_completed,
        })

    def get_agent(self, agent_id: str) -> Optional[AgentRecord]:
        return self._agents.get(agent_id)

    def get_agents_by_tier(self, tier: Tier) -> list[AgentRecord]:
        return [a for a in self.active_agents if a.current_tier == tier]

    def tier_distribution(self) -> dict[Tier, int]:
        dist = {t: 0 for t in Tier}
        for agent in self.active_agents:
            dist[agent.current_tier] += 1
        return dist

    def _get_agent(self, agent_id: str) -> AgentRecord:
        if agent_id not in self._agents:
            raise KeyError(f"Agent {agent_id} not found in registry")
        return self._agents[agent_id]

    def _log_event(self, event_type: str, agent_id: str, timestamp: float, data: dict):
        self._events.append({
            "type": event_type,
            "agent_id": agent_id,
            "timestamp": timestamp,
            "data": data,
        })
