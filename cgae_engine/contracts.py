"""
CGAE Contract System (Section 3.2.2 of cgae.tex)

Implements:
- CGAE Contracts: C = (O, Phi, V, T_min, r, p)
- Contract lifecycle: creation, acceptance, execution, verification, settlement
- Budget ceiling enforcement per tier
- Escrow mechanism for rewards and penalties
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from cgae_engine.gate import Tier, DEFAULT_BUDGET_CEILINGS


class ContractStatus(Enum):
    OPEN = "open"              # Available for bidding
    ASSIGNED = "assigned"      # Accepted by an agent
    EXECUTING = "executing"    # Agent is working on it
    VERIFYING = "verifying"    # Output submitted, verification pending
    COMPLETED = "completed"    # Verified and settled
    FAILED = "failed"          # Constraint violation or timeout
    CANCELLED = "cancelled"    # Cancelled by issuer
    EXPIRED = "expired"        # No agent accepted in time


@dataclass
class Constraint:
    """A machine-verifiable constraint (element of Phi)."""
    name: str
    description: str
    verify: Callable[[Any], bool]  # V: Output -> {0, 1}

    def check(self, output: Any) -> bool:
        return self.verify(output)


@dataclass
class CGAEContract:
    """
    A valid CGAE contract (Definition 5 in paper).
    C = (O, Phi, V, T_min, r, p)
    """
    contract_id: str
    objective: str                     # O: task description
    constraints: list[Constraint]      # Phi: machine-verifiable constraints
    min_tier: Tier                     # T_min: minimum required tier
    reward: float                      # r: reward for successful completion
    penalty: float                     # p: penalty for constraint violation
    issuer_id: str                     # Who posted the contract
    deadline: float                    # Time limit for completion

    # Mutable state
    status: ContractStatus = ContractStatus.OPEN
    assigned_agent_id: Optional[str] = None
    assigned_time: Optional[float] = None
    output: Any = None
    verification_result: Optional[bool] = None
    settlement_time: Optional[float] = None

    # Metadata
    domain: str = "general"
    difficulty: float = 0.5            # 0-1 scale, used for simulation
    created_time: float = 0.0

    def verify_output(self, output: Any) -> tuple[bool, list[str]]:
        """
        Run all constraints against the output.
        Returns (passed, list_of_failed_constraint_names).
        """
        failures = []
        for constraint in self.constraints:
            if not constraint.check(output):
                failures.append(constraint.name)
        return len(failures) == 0, failures

    def to_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "objective": self.objective,
            "min_tier": self.min_tier.name,
            "reward": self.reward,
            "penalty": self.penalty,
            "status": self.status.value,
            "domain": self.domain,
            "difficulty": self.difficulty,
            "assigned_agent_id": self.assigned_agent_id,
            "issuer_id": self.issuer_id,
            "deadline": self.deadline,
        }


class ContractManager:
    """
    Manages the lifecycle of CGAE contracts.
    Enforces budget ceilings, handles escrow, and tracks economic flow.
    """

    def __init__(self, budget_ceilings: Optional[dict[Tier, float]] = None):
        self.budget_ceilings = budget_ceilings or DEFAULT_BUDGET_CEILINGS
        self._contracts: dict[str, CGAEContract] = {}
        self._agent_active_exposure: dict[str, float] = {}  # agent_id -> sum of penalties
        self._escrow: dict[str, float] = {}  # contract_id -> escrowed amount
        self._events: list[dict] = []
        self._total_rewards_paid: float = 0.0
        self._total_penalties_collected: float = 0.0

    @property
    def contracts(self) -> dict[str, CGAEContract]:
        return dict(self._contracts)

    @property
    def open_contracts(self) -> list[CGAEContract]:
        return [c for c in self._contracts.values() if c.status == ContractStatus.OPEN]

    def create_contract(
        self,
        objective: str,
        constraints: list[Constraint],
        min_tier: Tier,
        reward: float,
        penalty: float,
        issuer_id: str,
        deadline: float,
        domain: str = "general",
        difficulty: float = 0.5,
        timestamp: float = 0.0,
    ) -> CGAEContract:
        """Create a new contract and add it to the marketplace."""
        contract_id = f"contract_{uuid.uuid4().hex[:12]}"
        contract = CGAEContract(
            contract_id=contract_id,
            objective=objective,
            constraints=constraints,
            min_tier=min_tier,
            reward=reward,
            penalty=penalty,
            issuer_id=issuer_id,
            deadline=deadline,
            domain=domain,
            difficulty=difficulty,
            created_time=timestamp,
        )
        self._contracts[contract_id] = contract
        # Escrow the reward
        self._escrow[contract_id] = reward
        self._log_event("contract_created", timestamp, {
            "contract_id": contract_id, "min_tier": min_tier.name,
            "reward": reward, "penalty": penalty, "domain": domain,
        })
        return contract

    def assign_contract(
        self,
        contract_id: str,
        agent_id: str,
        agent_tier: Tier,
        timestamp: float = 0.0,
    ) -> bool:
        """
        Assign a contract to an agent. Enforces:
        1. Agent tier >= contract min_tier
        2. Agent's total exposure + this penalty <= budget ceiling
        """
        contract = self._get_contract(contract_id)
        if contract.status != ContractStatus.OPEN:
            return False

        # Tier check
        if agent_tier < contract.min_tier:
            return False

        # Budget ceiling check (Theorem 1: Bounded Economic Exposure)
        current_exposure = self._agent_active_exposure.get(agent_id, 0.0)
        ceiling = self.budget_ceilings[agent_tier]
        if current_exposure + contract.penalty > ceiling:
            return False

        # Assign
        contract.status = ContractStatus.ASSIGNED
        contract.assigned_agent_id = agent_id
        contract.assigned_time = timestamp
        self._agent_active_exposure[agent_id] = current_exposure + contract.penalty

        self._log_event("contract_assigned", timestamp, {
            "contract_id": contract_id, "agent_id": agent_id,
            "exposure_after": self._agent_active_exposure[agent_id],
            "ceiling": ceiling,
        })
        return True

    def submit_output(
        self,
        contract_id: str,
        output: Any,
        timestamp: float = 0.0,
    ) -> tuple[bool, list[str]]:
        """
        Submit output for a contract. Runs verification against constraints.
        Returns (passed, failed_constraints).
        """
        contract = self._get_contract(contract_id)
        if contract.status not in (ContractStatus.ASSIGNED, ContractStatus.EXECUTING):
            raise ValueError(f"Contract {contract_id} is not in assignable state: {contract.status}")

        contract.output = output
        contract.status = ContractStatus.VERIFYING
        passed, failures = contract.verify_output(output)
        contract.verification_result = passed

        return passed, failures

    def settle_contract(
        self,
        contract_id: str,
        timestamp: float = 0.0,
    ) -> dict:
        """
        Settle a verified contract. Distributes reward or penalty.
        Returns settlement details.
        """
        contract = self._get_contract(contract_id)
        if contract.status != ContractStatus.VERIFYING:
            raise ValueError(f"Contract {contract_id} not in verifying state")

        agent_id = contract.assigned_agent_id
        settlement = {"contract_id": contract_id, "agent_id": agent_id}

        if contract.verification_result:
            # Success: release escrow to agent
            contract.status = ContractStatus.COMPLETED
            settlement["outcome"] = "success"
            settlement["reward"] = contract.reward
            settlement["penalty"] = 0.0
            self._total_rewards_paid += contract.reward
        else:
            # Failure: agent pays penalty
            contract.status = ContractStatus.FAILED
            settlement["outcome"] = "failure"
            settlement["reward"] = 0.0
            settlement["penalty"] = contract.penalty
            self._total_penalties_collected += contract.penalty

        # Release exposure
        current_exposure = self._agent_active_exposure.get(agent_id, 0.0)
        self._agent_active_exposure[agent_id] = max(0, current_exposure - contract.penalty)

        # Clean up escrow
        self._escrow.pop(contract_id, None)
        contract.settlement_time = timestamp

        self._log_event("contract_settled", timestamp, settlement)
        return settlement

    def expire_contracts(self, current_time: float) -> list[str]:
        """Expire contracts past their deadline."""
        expired = []
        for contract in self._contracts.values():
            if contract.status == ContractStatus.OPEN and current_time > contract.deadline:
                contract.status = ContractStatus.EXPIRED
                self._escrow.pop(contract.contract_id, None)
                expired.append(contract.contract_id)
            elif contract.status in (ContractStatus.ASSIGNED, ContractStatus.EXECUTING):
                if current_time > contract.deadline:
                    contract.status = ContractStatus.FAILED
                    contract.verification_result = False
                    agent_id = contract.assigned_agent_id
                    if agent_id:
                        exposure = self._agent_active_exposure.get(agent_id, 0.0)
                        self._agent_active_exposure[agent_id] = max(
                            0, exposure - contract.penalty
                        )
                        self._total_penalties_collected += contract.penalty
                    self._escrow.pop(contract.contract_id, None)
                    expired.append(contract.contract_id)
        return expired

    def agent_exposure(self, agent_id: str) -> float:
        """Current economic exposure for an agent (Theorem 1)."""
        return self._agent_active_exposure.get(agent_id, 0.0)

    def get_contracts_for_tier(self, tier: Tier) -> list[CGAEContract]:
        """Get open contracts accessible to an agent at the given tier."""
        return [
            c for c in self._contracts.values()
            if c.status == ContractStatus.OPEN and c.min_tier <= tier
        ]

    def economics_summary(self) -> dict:
        status_counts = {}
        for c in self._contracts.values():
            status_counts[c.status.value] = status_counts.get(c.status.value, 0) + 1
        return {
            "total_contracts": len(self._contracts),
            "status_distribution": status_counts,
            "total_rewards_paid": self._total_rewards_paid,
            "total_penalties_collected": self._total_penalties_collected,
            "total_escrowed": sum(self._escrow.values()),
            "active_exposures": dict(self._agent_active_exposure),
        }

    def _get_contract(self, contract_id: str) -> CGAEContract:
        if contract_id not in self._contracts:
            raise KeyError(f"Contract {contract_id} not found")
        return self._contracts[contract_id]

    def _log_event(self, event_type: str, timestamp: float, data: dict):
        self._events.append({
            "type": event_type, "timestamp": timestamp, "data": data,
        })
