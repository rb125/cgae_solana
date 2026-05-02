"""
Concrete Agent Strategies for the CGAE Economy Testbed.

Five agent archetypes designed to test different aspects of the CGAE theorems:

1. Conservative: High robustness, low capability -> tests Theorem 1 (bounded exposure)
2. Aggressive: High capability, low robustness -> tests incentive structure (stuck at low tiers)
3. Balanced: Moderate both -> baseline reference
4. Adaptive: Invests in weakest dimension -> tests Theorem 2 (incentive compatibility)
5. Cheater: Attempts tier-laundering -> tests Proposition 2 (collusion resistance)
"""

from __future__ import annotations

import random
from typing import Any, Optional

from cgae_engine.gate import RobustnessVector, Tier
from cgae_engine.contracts import CGAEContract
from agents.base import BaseAgent, AgentStrategy, AgentDecision


class ConservativeAgent(BaseAgent):
    """
    High robustness, low capability. Plays it safe.

    Strategy:
    - Only bids on contracts well within its tier
    - Prefers low-risk, low-reward contracts
    - Maintains high balance by avoiding risky contracts
    - Never invests in capability, focuses on maintaining robustness

    Tests: Theorem 1 (bounded exposure) - should have low, stable exposure
    Expected: Survives long but earns less than optimal
    """

    def __init__(self, name: str = "conservative", **kwargs):
        robustness = kwargs.pop("true_robustness", RobustnessVector(
            cc=0.85, er=0.80, as_=0.75, ih=0.90
        ))
        super().__init__(
            name=name,
            strategy=AgentStrategy.CONSERVATIVE,
            true_robustness=robustness,
            capability=kwargs.pop("capability", 0.65),
            **kwargs,
        )

    def decide(self, available_contracts, current_tier, balance, current_exposure, budget_ceiling):
        # Only bid if we have plenty of budget headroom
        safe_exposure_limit = budget_ceiling * 0.5
        eligible = [
            c for c in available_contracts
            if c.penalty + current_exposure <= safe_exposure_limit
            and c.difficulty < 0.5  # Only easy tasks
            and c.reward > 0  # Must have positive reward
        ]

        if not eligible:
            return AgentDecision(action="idle")

        # Pick the safest (lowest penalty) contract
        best = min(eligible, key=lambda c: c.penalty)
        return AgentDecision(action="bid", contract_id=best.contract_id)

    def execute_task(self, contract):
        # Conservative agents are careful but slow. Success depends on capability + low difficulty.
        success_prob = self.task_success_probability(contract)
        return random.random() < success_prob


class AggressiveAgent(BaseAgent):
    """
    High capability, low robustness. Pushes boundaries.

    Strategy:
    - Bids on the highest-reward contracts available
    - Accepts more risk (higher exposure ratio)
    - Doesn't invest in robustness (capability-focused)
    - Gets stuck at low tiers due to robustness gating

    Tests: Theorem 2 (incentive compatibility) - demonstrates that capability
    alone doesn't unlock higher tiers. Should underperform Adaptive agent.
    Expected: High short-term earnings at T1, but can't access T2+ contracts
    """

    def __init__(self, name: str = "aggressive", **kwargs):
        robustness = kwargs.pop("true_robustness", RobustnessVector(
            cc=0.35, er=0.40, as_=0.30, ih=0.70
        ))
        super().__init__(
            name=name,
            strategy=AgentStrategy.AGGRESSIVE,
            true_robustness=robustness,
            capability=kwargs.pop("capability", 0.85),
            **kwargs,
        )

    def decide(self, available_contracts, current_tier, balance, current_exposure, budget_ceiling):
        eligible = [
            c for c in available_contracts
            if c.penalty + current_exposure <= budget_ceiling
        ]

        if not eligible:
            return AgentDecision(action="idle")

        # Pick the highest-reward contract
        best = max(eligible, key=lambda c: c.reward)
        return AgentDecision(action="bid", contract_id=best.contract_id)

    def execute_task(self, contract):
        # Aggressive agents have high capability, so they succeed more often
        success_prob = self.task_success_probability(contract)
        # Bonus for high capability
        success_prob = min(0.95, success_prob * 1.3)
        return random.random() < success_prob


class BalancedAgent(BaseAgent):
    """
    Moderate robustness and capability. The baseline.

    Strategy:
    - Evaluates contracts by expected value (reward * success_prob - penalty * fail_prob)
    - Maintains moderate exposure
    - Occasionally invests in robustness when near a tier threshold

    Tests: Provides baseline for comparing other strategies
    Expected: Moderate performance across all metrics
    """

    def __init__(self, name: str = "balanced", **kwargs):
        robustness = kwargs.pop("true_robustness", RobustnessVector(
            cc=0.60, er=0.55, as_=0.50, ih=0.80
        ))
        super().__init__(
            name=name,
            strategy=AgentStrategy.BALANCED,
            true_robustness=robustness,
            capability=kwargs.pop("capability", 0.6),
            **kwargs,
        )

    def decide(self, available_contracts, current_tier, balance, current_exposure, budget_ceiling):
        eligible = [
            c for c in available_contracts
            if c.penalty + current_exposure <= budget_ceiling * 0.8
        ]

        if not eligible:
            return AgentDecision(action="idle")

        # Pick by expected value
        def ev(c):
            p = self.task_success_probability(c)
            return c.reward * p - c.penalty * (1 - p)

        best = max(eligible, key=ev)
        if ev(best) > 0:
            return AgentDecision(action="bid", contract_id=best.contract_id)
        return AgentDecision(action="idle")

    def execute_task(self, contract):
        success_prob = self.task_success_probability(contract)
        return random.random() < success_prob


class AdaptiveAgent(BaseAgent):
    """
    Strategically invests in its weakest robustness dimension.

    Strategy:
    - Identifies binding dimension (what's keeping it at current tier)
    - Allocates a fraction of earnings to robustness investment
    - Targets the weakest dimension specifically (Theorem 2 behavior)
    - Gradually unlocks higher tiers over time

    Tests: Theorem 2 (incentive compatibility) - this agent should demonstrate
    the predicted behavior where rational agents invest in robustness.
    Expected: Starts slow, accelerates as it unlocks higher tiers.
    This is the agent that should win long-run.
    """

    def __init__(self, name: str = "adaptive", **kwargs):
        robustness = kwargs.pop("true_robustness", RobustnessVector(
            cc=0.55, er=0.50, as_=0.45, ih=0.80
        ))
        super().__init__(
            name=name,
            strategy=AgentStrategy.ADAPTIVE,
            true_robustness=robustness,
            capability=kwargs.pop("capability", 0.6),
            **kwargs,
        )
        self.investment_fraction = 0.15  # Spend 15% of earnings on robustness
        self._accumulated_investment = 0.0

    def decide(self, available_contracts, current_tier, balance, current_exposure, budget_ceiling):
        # Should we invest in robustness this step?
        # Only invest when we have sufficient capital buffer
        if self._accumulated_investment >= 0.03 and balance > 0.15:
            weakest_dim = self._weakest_dimension()
            investment = min(self._accumulated_investment, 0.03)
            self._accumulated_investment -= investment
            return AgentDecision(
                action="invest_robustness",
                investment_dimension=weakest_dim,
                investment_amount=investment * 0.5,  # Diminishing returns
            )

        # Otherwise, bid on contracts
        eligible = [
            c for c in available_contracts
            if c.penalty + current_exposure <= budget_ceiling * 0.7
        ]

        if not eligible:
            return AgentDecision(action="idle")

        # Pick by expected value, with preference for higher-tier contracts
        def ev(c):
            p = self.task_success_probability(c)
            return (c.reward * p - c.penalty * (1 - p)) * (1 + c.min_tier.value * 0.1)

        best = max(eligible, key=ev)
        if ev(best) > 0:
            return AgentDecision(action="bid", contract_id=best.contract_id)
        return AgentDecision(action="idle")

    def execute_task(self, contract):
        success_prob = self.task_success_probability(contract)
        result = random.random() < success_prob
        if result:
            # Accumulate investment fund from earnings
            self._accumulated_investment += contract.reward * self.investment_fraction
        return result

    def _weakest_dimension(self) -> str:
        r = self.true_robustness
        dims = {"cc": r.cc, "er": r.er, "as": r.as_}
        return min(dims, key=dims.get)


class CheaterAgent(BaseAgent):
    """
    Attempts to game the system via delegation chain exploitation.

    Strategy:
    - Tries to accept contracts above its actual tier
    - Attempts to form delegation chains to bypass gate
    - May misrepresent capabilities

    Tests: Proposition 2 (collusion resistance) - the CGAE system should
    prevent this agent from accessing higher tiers than its robustness warrants.
    Expected: Should be caught and penalized. Demonstrates that the
    weakest-link gate + chain robustness constraint works.
    """

    def __init__(self, name: str = "cheater", **kwargs):
        robustness = kwargs.pop("true_robustness", RobustnessVector(
            cc=0.70, er=0.25, as_=0.65, ih=0.60
        ))
        super().__init__(
            name=name,
            strategy=AgentStrategy.CHEATER,
            true_robustness=robustness,
            capability=kwargs.pop("capability", 0.7),
            **kwargs,
        )
        self._collusion_partners: list[str] = []

    def decide(self, available_contracts, current_tier, balance, current_exposure, budget_ceiling):
        # Greedily bid on highest-reward contracts, even risky ones
        eligible = [
            c for c in available_contracts
            if c.penalty + current_exposure <= budget_ceiling
        ]

        if not eligible:
            return AgentDecision(action="idle")

        # Always go for max reward, regardless of risk
        best = max(eligible, key=lambda c: c.reward)
        return AgentDecision(
            action="bid",
            contract_id=best.contract_id,
            details={"attempted_tier_bypass": best.min_tier.value > current_tier.value},
        )

    def execute_task(self, contract):
        # Cheater has moderate capability but weak epistemic robustness (ER=0.25)
        # This manifests as higher failure rate on tasks requiring epistemic integrity
        success_prob = self.task_success_probability(contract)
        # Penalty for epistemic weakness: more failures on complex tasks
        if contract.difficulty > 0.5:
            success_prob *= 0.6  # Epistemic weakness hurts on hard tasks
        return random.random() < success_prob


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

AGENT_PRESETS: dict[str, type[BaseAgent]] = {
    "conservative": ConservativeAgent,
    "aggressive": AggressiveAgent,
    "balanced": BalancedAgent,
    "adaptive": AdaptiveAgent,
    "cheater": CheaterAgent,
}


def create_agent_cohort(
    strategies: Optional[list[str]] = None,
    custom_robustness: Optional[dict[str, RobustnessVector]] = None,
) -> list[BaseAgent]:
    """
    Create a cohort of agents with diverse strategies.
    Default: one of each strategy type.
    """
    if strategies is None:
        strategies = list(AGENT_PRESETS.keys())

    agents = []
    for i, strategy_name in enumerate(strategies):
        cls = AGENT_PRESETS.get(strategy_name)
        if cls is None:
            raise ValueError(f"Unknown strategy: {strategy_name}")
        kwargs = {}
        if custom_robustness and strategy_name in custom_robustness:
            kwargs["true_robustness"] = custom_robustness[strategy_name]
        agent = cls(name=f"{strategy_name}_{i}", **kwargs)
        agents.append(agent)

    return agents
