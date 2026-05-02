"""
Base Agent - Abstract interface for CGAE economic agents.

Each agent has:
- A model identity (simulated or real)
- A robustness profile (true underlying robustness)
- An economic strategy (how it decides what to do each step)
- A wallet (balance, income, expenses)
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from cgae_engine.gate import RobustnessVector, Tier
from cgae_engine.contracts import CGAEContract


class AgentStrategy(Enum):
    CONSERVATIVE = "conservative"
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    ADAPTIVE = "adaptive"
    CHEATER = "cheater"


@dataclass
class AgentDecision:
    """What the agent decides to do in a given step."""
    action: str  # "bid", "execute", "invest_robustness", "idle", "delegate"
    contract_id: Optional[str] = None
    investment_dimension: Optional[str] = None  # "cc", "er", "as"
    investment_amount: float = 0.0
    output: Any = None
    details: dict = field(default_factory=dict)


class BaseAgent(ABC):
    """
    Abstract base class for CGAE economic agents.

    Subclasses implement the strategy: how the agent decides which contracts
    to bid on, whether to invest in robustness, and how to execute tasks.
    """

    def __init__(
        self,
        name: str,
        strategy: AgentStrategy,
        true_robustness: RobustnessVector,
        capability: float = 0.5,
        model_config: Optional[dict] = None,
    ):
        self.name = name
        self.strategy = strategy
        self.true_robustness = true_robustness
        self.capability = capability  # Task success probability baseline
        self.model_config = model_config or {"model": name, "strategy": strategy.value}

        # Set by the economy on registration
        self.agent_id: Optional[str] = None

        # Internal tracking
        self._decisions: list[AgentDecision] = []
        self._step_count: int = 0

    @abstractmethod
    def decide(
        self,
        available_contracts: list[CGAEContract],
        current_tier: Tier,
        balance: float,
        current_exposure: float,
        budget_ceiling: float,
    ) -> AgentDecision:
        """
        Make a decision for this time step.

        Args:
            available_contracts: Contracts the agent is eligible to bid on
            current_tier: Agent's current tier
            balance: Current token balance
            current_exposure: Current economic exposure
            budget_ceiling: Maximum exposure for current tier
        """
        ...

    @abstractmethod
    def execute_task(self, contract: CGAEContract) -> Any:
        """
        Execute a task and produce output.
        The output will be verified against the contract's constraints.
        """
        ...

    def task_success_probability(self, contract: CGAEContract) -> float:
        """
        Probability of successfully completing a contract.
        Depends on capability and the robustness dimension most
        relevant to the contract.
        """
        base = self.capability
        difficulty = contract.difficulty
        # Higher difficulty reduces success probability
        return max(0.05, min(0.95, base * (1.0 - difficulty * 0.5)))

    def robustness_investment_cost(self, dimension: str, amount: float) -> float:
        """Cost to improve a robustness dimension by `amount`."""
        # Quadratic cost: harder to improve as you get higher
        current = getattr(self.true_robustness, dimension if dimension != "as" else "as_")
        return amount * (1.0 + current * 2.0)

    def invest_robustness(self, dimension: str, amount: float) -> RobustnessVector:
        """
        Invest in improving a robustness dimension.
        Returns the new robustness vector.
        """
        cc = self.true_robustness.cc
        er = self.true_robustness.er
        as_ = self.true_robustness.as_
        ih = self.true_robustness.ih

        if dimension == "cc":
            cc = min(1.0, cc + amount)
        elif dimension == "er":
            er = min(1.0, er + amount)
        elif dimension == "as":
            as_ = min(1.0, as_ + amount)

        self.true_robustness = RobustnessVector(cc=cc, er=er, as_=as_, ih=ih)
        return self.true_robustness

    def record_decision(self, decision: AgentDecision):
        self._decisions.append(decision)
        self._step_count += 1

    @property
    def decisions(self) -> list[AgentDecision]:
        return list(self._decisions)

    def to_config(self) -> dict:
        return {
            "name": self.name,
            "strategy": self.strategy.value,
            "capability": self.capability,
            "true_robustness": {
                "cc": self.true_robustness.cc,
                "er": self.true_robustness.er,
                "as": self.true_robustness.as_,
                "ih": self.true_robustness.ih,
            },
        }
