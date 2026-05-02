"""
Autonomous Agent v2 — CGAE Economic Actor
==========================================

Implements the v2 Autonomous Agent Architecture specification.

Separation of Cognition from Economy
-------------------------------------
The LLM handles task *execution*.  Everything else — contract evaluation,
bidding strategy, robustness tracking, financial management — is deterministic
code.  This makes the agent's economic behaviour inspectable without LLM
introspection, and keeps gas costs low.

Layers
------
PerceptionLayer  — constraint / domain pass-rate learning
AccountingLayer  — balance, exposure, reserves, burn-rate
PlanningLayer    — EV / RAEV contract scoring + strategy delegation
ExecutionLayer   — LLM call with constraint-aware prompts, self-verify, retry

Strategies (pluggable via StrategyInterface)
--------------------------------------------
GrowthStrategy        — robustness-investment growth; the Theorem 2 agent
ConservativeStrategy  — low-risk, low-utilisation; survives longest
OpportunisticStrategy — high-risk, max-reward; highest variance
SpecialistStrategy    — domain-focused; improves pass rate in chosen domains
AdversarialStrategy   — probes system limits; validates Proposition 2

Migration (Phase 1)
-------------------
Drop-in replacement for the bare LLMAgent + manual logic in live_runner.py.
The runner still handles contract posting, acceptance and Economy settlement.
AutonomousAgent.plan_task()       — replaces random.choice(available_tasks)
AutonomousAgent.execute_task()    — replaces llm_agent.execute_task() + retry
AutonomousAgent.update_state()    — replaces inline robustness update logic
"""

from __future__ import annotations

import logging
import math
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from cgae_engine.gate import GateFunction, RobustnessVector, Tier, TierThresholds

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentState:
    """Complete agent state snapshot passed to strategies each planning cycle."""
    # Identity
    agent_id: str
    model_name: str

    # Robustness
    certified_robustness: RobustnessVector
    effective_robustness: RobustnessVector   # after temporal decay
    certified_tier: Tier
    effective_tier: Tier
    binding_dimension: Optional[str]          # "cc", "er", or "as"
    gap_to_next_tier: dict                    # dim -> gap float

    # Financial
    balance: float
    available_for_contracts: float
    active_exposure: float
    remaining_ceiling: float
    burn_rate: float
    rounds_until_insolvency: float
    roi: float

    # Performance history
    constraint_pass_rates: dict    # constraint_name -> float
    domain_pass_rates: dict        # domain -> float
    total_contracts_completed: int
    total_contracts_failed: int
    win_rate: float

    # Temporal
    time_since_certification: float
    spot_audit_probability: float


@dataclass(frozen=True)
class ScoredContract:
    """A contract that has been pre-evaluated by the Planning Layer."""
    contract_id: str
    task_id: str
    min_tier: Tier
    domain: str
    constraint_types: list            # list[str]
    reward: float
    penalty: float
    deadline: float
    difficulty: float

    # Computed by PlanningLayer
    estimated_pass_probability: float
    estimated_token_cost: float
    expected_value: float             # p*R - (1-p)*P - cost
    risk_premium: float               # penalty² / (2 * balance)
    risk_adjusted_ev: float           # EV - risk_premium


@dataclass
class ExecutionResult:
    """Result of executing a task through the ExecutionLayer."""
    output: str
    token_usage: dict                  # input_tokens, output_tokens
    token_cost_sol: float
    latency_ms: float
    retries_used: int
    self_check_passed: bool
    self_check_failures: list          # constraint names that failed self-check
    self_check_diagnostics: dict       # name -> diagnostic string


@dataclass
class RobustnessInvestment:
    """An instruction to invest in a robustness dimension."""
    dimension: str    # "cc", "er", or "as"
    budget: float     # SOL to spend


# ---------------------------------------------------------------------------
# Strategy interface and concrete implementations
# ---------------------------------------------------------------------------

class StrategyInterface(ABC):
    """Pluggable decision policy for the Planning Layer."""

    @abstractmethod
    def rank_contracts(
        self,
        eligible: list,           # list[ScoredContract]
        state: AgentState,
    ) -> list:                    # ordered list[ScoredContract]
        ...

    @abstractmethod
    def should_invest_robustness(
        self, state: AgentState
    ) -> Optional[RobustnessInvestment]:
        ...

    @abstractmethod
    def max_utilization(self) -> float:
        """Fraction of budget ceiling willing to commit."""
        ...


class GrowthStrategy(StrategyInterface):
    """
    Invests in robustness to unlock higher tiers.
    The Theorem 2 agent: robustness investment as profit strategy.

    Accepts any positive RAEV contract, prefers higher tiers.
    Invests when binding dimension is within ``invest_threshold`` of next tier
    and projected ROI is positive within 20 rounds.
    """
    RAEV_MIN = 0.0
    INVEST_THRESHOLD = 0.07   # Invest when < 0.07 from next tier threshold
    MAX_INVEST_FRACTION = 0.20  # Max 20% of balance per invest action

    def rank_contracts(self, eligible, state):
        # Sort by RAEV; break ties by preferring higher tiers
        return sorted(
            eligible,
            key=lambda c: (c.risk_adjusted_ev + c.min_tier.value * 0.005),
            reverse=True,
        )

    def should_invest_robustness(self, state: AgentState) -> Optional[RobustnessInvestment]:
        if state.binding_dimension is None:
            return None
        gap = state.gap_to_next_tier.get(state.binding_dimension, 1.0)
        if gap > self.INVEST_THRESHOLD:
            return None
        # Can we afford it?
        budget = min(
            state.available_for_contracts * self.MAX_INVEST_FRACTION,
            state.balance * 0.10,
        )
        if budget < 0.005:
            return None
        # Rough ROI check: does the tier upgrade pay back within 20 rounds?
        tier_idx = state.certified_tier.value
        next_tier_reward_uplift = (tier_idx + 1) * 0.01  # Rough per-round uplift
        if next_tier_reward_uplift * 20 > budget:
            return RobustnessInvestment(dimension=state.binding_dimension, budget=budget)
        return None

    def max_utilization(self) -> float:
        return 0.70


class ConservativeStrategy(StrategyInterface):
    """
    Only high-certainty, low-penalty contracts.
    Never invests in robustness. Survives the longest.
    """
    RAEV_MIN = 0.002
    MAX_DIFFICULTY = 0.5

    def rank_contracts(self, eligible, state):
        # Prefer lowest-penalty contracts above the RAEV minimum
        passing = [c for c in eligible if c.risk_adjusted_ev >= self.RAEV_MIN
                   and c.difficulty <= self.MAX_DIFFICULTY]
        return sorted(passing, key=lambda c: c.penalty)

    def should_invest_robustness(self, state: AgentState) -> Optional[RobustnessInvestment]:
        return None  # Never invests

    def max_utilization(self) -> float:
        return 0.30


class OpportunisticStrategy(StrategyInterface):
    """
    Max-reward, high-risk. Uses raw EV (not risk-adjusted).
    Most likely to go insolvent; highest upside in good rounds.
    """
    def rank_contracts(self, eligible, state):
        return sorted(eligible, key=lambda c: c.expected_value, reverse=True)

    def should_invest_robustness(self, state: AgentState) -> Optional[RobustnessInvestment]:
        # Only if stuck at T0 — must reach T1 to earn anything
        if state.certified_tier == Tier.T0 and state.available_for_contracts > 0.02:
            dim = state.binding_dimension or "as"
            return RobustnessInvestment(dimension=dim, budget=state.available_for_contracts * 0.30)
        return None

    def max_utilization(self) -> float:
        return 0.90


class SpecialistStrategy(StrategyInterface):
    """
    Domain-focused: only accepts contracts in its two best domains.
    Higher RAEV threshold for unfamiliar territory.
    Invests in constraint types where failure rate exceeds 30%.
    """
    SPECIALIST_RAEV_MIN = 0.001
    GENERALIST_RAEV_MIN = 0.010
    NUM_SPECIALTY_DOMAINS = 2
    FAILURE_INVEST_THRESHOLD = 0.30

    def rank_contracts(self, eligible, state):
        top_domains = sorted(
            state.domain_pass_rates,
            key=state.domain_pass_rates.get,
            reverse=True,
        )[:self.NUM_SPECIALTY_DOMAINS]

        def score(c: ScoredContract) -> float:
            in_specialty = c.domain in top_domains
            thresh = self.SPECIALIST_RAEV_MIN if in_specialty else self.GENERALIST_RAEV_MIN
            return c.risk_adjusted_ev if (in_specialty or c.risk_adjusted_ev > thresh) else -999

        ranked = [c for c in eligible if score(c) > -998]
        return sorted(ranked, key=score, reverse=True)

    def should_invest_robustness(self, state: AgentState) -> Optional[RobustnessInvestment]:
        # Find the constraint type with worst pass rate
        if not state.constraint_pass_rates:
            return None
        worst_dim_name, worst_rate = min(
            state.constraint_pass_rates.items(), key=lambda kv: kv[1]
        )
        if worst_rate > (1.0 - self.FAILURE_INVEST_THRESHOLD):
            return None
        # Map constraint family → robustness dimension
        dim = "cc"
        if any(k in worst_dim_name for k in ("keyword", "factual", "accuracy")):
            dim = "er"
        elif any(k in worst_dim_name for k in ("caveat", "harm", "refusal", "ethical")):
            dim = "as"
        budget = state.available_for_contracts * 0.15
        if budget < 0.003:
            return None
        return RobustnessInvestment(dimension=dim, budget=budget)

    def max_utilization(self) -> float:
        return 0.50


class AdversarialStrategy(StrategyInterface):
    """
    Probes system limits: selects borderline contracts and minimally invests
    in AS (trying to game the ethical gate).  Validates Proposition 2.
    """
    def rank_contracts(self, eligible, state):
        # Prefer contracts where self-check shows near-borderline probability
        return sorted(
            eligible,
            key=lambda c: abs(c.estimated_pass_probability - 0.55),
        )

    def should_invest_robustness(self, state: AgentState) -> Optional[RobustnessInvestment]:
        if state.binding_dimension == "as" and state.available_for_contracts > 0.01:
            return RobustnessInvestment(dimension="as", budget=0.005)
        return None

    def max_utilization(self) -> float:
        return 0.95


STRATEGY_MAP: dict[str, StrategyInterface] = {
    "growth": GrowthStrategy(),
    "conservative": ConservativeStrategy(),
    "opportunistic": OpportunisticStrategy(),
    "specialist": SpecialistStrategy(),
    "adversarial": AdversarialStrategy(),
}


# ---------------------------------------------------------------------------
# Perception Layer
# ---------------------------------------------------------------------------

class PerceptionLayer:
    """
    Tracks per-constraint and per-domain pass rates from task history.
    Updated after every contract settlement via update_from_result().
    """

    def __init__(self):
        # Running history: name -> list[bool]
        self._constraint_history: dict[str, list] = {}
        self._domain_history: dict[str, list] = {}

    @property
    def constraint_pass_rates(self) -> dict:
        return {
            name: (sum(hist) / len(hist))
            for name, hist in self._constraint_history.items()
            if hist
        }

    @property
    def domain_pass_rates(self) -> dict:
        return {
            domain: (sum(hist) / len(hist))
            for domain, hist in self._domain_history.items()
            if hist
        }

    def update_from_result(self, task: Any, verification: Any):
        """Call after each verification to update running pass rates."""
        domain = getattr(task, "domain", "unknown")
        self._domain_history.setdefault(domain, []).append(
            bool(getattr(verification, "overall_pass", False))
        )
        for c in getattr(task, "constraints", []):
            passed = c.name in getattr(verification, "constraints_passed", [])
            self._domain_history.setdefault(f"constraint:{c.name}", [])
            self._constraint_history.setdefault(c.name, []).append(passed)

    def estimated_pass_prob(self, task: Any) -> float:
        """
        Estimate pass probability for a task based on constraint and domain history.
        Falls back to 0.65 when no history is available — modern LLMs pass
        straightforward tasks at well above chance, so 0.5 systematically
        underestimates EV and suppresses all task selection at startup.
        """
        domain = getattr(task, "domain", "unknown")
        domain_rate = self.domain_pass_rates.get(domain, 0.65)
        constraints = getattr(task, "constraints", [])
        if not constraints:
            return domain_rate
        rates = [self.constraint_pass_rates.get(c.name, 0.65) for c in constraints]
        constraint_rate = math.prod(rates) if rates else 0.65
        return (constraint_rate + domain_rate) / 2.0


# ---------------------------------------------------------------------------
# Accounting Layer
# ---------------------------------------------------------------------------

class AccountingLayer:
    """
    Financial management with layered reserves.

    Reserves (in priority order, all deducted before contract funds):
      MINIMUM_RESERVE  — hard floor; triggers SelfSuspend if breached
      AUDIT_RESERVE    — 1 full 4-dim audit cycle
      (gas reserve is implicit in MINIMUM_RESERVE for off-chain simulation)

    available_for_contracts = balance - active_exposure
                              - MINIMUM_RESERVE - AUDIT_RESERVE
    """

    MINIMUM_RESERVE: float = 0.05    # SOL hard floor
    AUDIT_RESERVE: float = 0.02      # ~4 dims × 0.005 SOL
    MAX_UTILIZATION: float = 0.70    # Max fraction of ceiling to commit

    def __init__(self, initial_balance: float):
        self.balance: float = initial_balance
        self.active_exposure: float = 0.0
        self.cumulative_earned: float = 0.0
        self.cumulative_spent: float = 0.0
        self.cumulative_penalties: float = 0.0
        self._burn_samples: list = []   # Recent SOL-per-round costs

    @property
    def available_for_contracts(self) -> float:
        return max(
            0.0,
            self.balance
            - self.active_exposure
            - self.MINIMUM_RESERVE
            - self.AUDIT_RESERVE,
        )

    @property
    def roi(self) -> float:
        spent = self.cumulative_spent + self.cumulative_penalties
        if spent == 0:
            return 0.0
        return (self.cumulative_earned - spent) / spent

    @property
    def burn_rate(self) -> float:
        if not self._burn_samples:
            return 0.001   # Assume small storage cost until we have data
        return sum(self._burn_samples[-10:]) / len(self._burn_samples[-10:])

    @property
    def rounds_until_insolvency(self) -> float:
        br = self.burn_rate
        if br <= 0:
            return float("inf")
        return max(0.0, (self.balance - self.MINIMUM_RESERVE) / br)

    def can_afford(self, penalty: float, token_cost: float) -> bool:
        """Check whether accepting a contract keeps us solvent."""
        new_exposure = self.active_exposure + penalty
        headroom = self.balance - new_exposure - self.MINIMUM_RESERVE - self.AUDIT_RESERVE
        return headroom >= token_cost

    def record_round_cost(self, cost: float):
        self._burn_samples.append(cost)

    def sync_from_record(self, record: Any):
        """Sync from Economy AgentRecord (source of truth for balance)."""
        self.balance = record.balance
        self.cumulative_earned = record.total_earned
        self.cumulative_spent = record.total_spent
        self.cumulative_penalties = record.total_penalties


# ---------------------------------------------------------------------------
# Execution Layer
# ---------------------------------------------------------------------------

class ExecutionLayer:
    """
    Executes tasks with:
    1. Constraint-aware system prompt injection
    2. Self-verification using the same checks the verifier will run
    3. Retry loop (up to max_retries) when self-check detects failures

    Self-check only covers algorithmic constraints (format, keywords, JSON).
    Jury evaluation cannot be pre-checked — this is by design.
    """

    def __init__(self, llm_agent: Any, self_verify: bool = True, max_retries: int = 2):
        self.llm = llm_agent
        self.self_verify = self_verify
        self.max_retries = max_retries

    def execute(self, task: Any, token_cost_fn) -> ExecutionResult:
        """
        Execute a task end-to-end and return a structured result.
        ``token_cost_fn()`` is called with (model_name, in_tok, out_tok) to
        compute SOL cost; the caller owns cost accounting.
        """
        system_prompt = self._build_system_prompt(task)
        user_prompt = task.prompt

        tokens_in_before = self.llm.total_input_tokens
        tokens_out_before = self.llm.total_output_tokens
        start = time.time()

        output = self.llm.execute_task(user_prompt, system_prompt)
        retries = 0
        self_check_result: dict = {"passed": True, "failures": [], "diagnostics": {}}

        if self.self_verify:
            self_check_result = self._self_check(task, output)

            for attempt in range(self.max_retries):
                if self_check_result["passed"]:
                    break
                retries += 1
                retry_prompt = self._build_retry_prompt(
                    user_prompt, self_check_result["failures"],
                    self_check_result["diagnostics"],
                )
                output = self.llm.execute_task(retry_prompt, system_prompt)
                self_check_result = self._self_check(task, output)

        latency_ms = (time.time() - start) * 1000
        in_tok = self.llm.total_input_tokens - tokens_in_before
        out_tok = self.llm.total_output_tokens - tokens_out_before
        token_cost = token_cost_fn(self.llm.model_name, in_tok, out_tok)

        return ExecutionResult(
            output=output,
            token_usage={"input": in_tok, "output": out_tok},
            token_cost_sol=token_cost,
            latency_ms=latency_ms,
            retries_used=retries,
            self_check_passed=self_check_result["passed"],
            self_check_failures=self_check_result["failures"],
            self_check_diagnostics=self_check_result["diagnostics"],
        )

    def _build_system_prompt(self, task: Any) -> str:
        base = task.system_prompt or ""
        if not task.constraints:
            return base
        lines = [
            base,
            "\n\n[CONSTRAINT REQUIREMENTS — you MUST satisfy ALL of the following]",
        ]
        for c in task.constraints:
            lines.append(f"  • {c.name}: {c.description}")
        return "\n".join(lines)

    def _self_check(self, task: Any, output: str) -> dict:
        """Run algorithmic constraint checks identical to what the verifier will do."""
        failures: list = []
        diagnostics: dict = {}
        for c in task.constraints:
            try:
                passed = c.check(output)
            except Exception:
                passed = True   # Don't penalise unknown constraint types
            if not passed:
                failures.append(c.name)
                diagnostics[c.name] = self._diagnose(c, output)
        return {
            "passed": len(failures) == 0,
            "failures": failures,
            "diagnostics": diagnostics,
        }

    @staticmethod
    def _diagnose(constraint: Any, output: str) -> str:
        name = constraint.name
        if "word_count" in name:
            count = len(output.split())
            return f"Word count is {count}"
        if "valid_json" in name:
            return "Output is not valid JSON"
        if "keyword" in name or "contain" in name:
            desc = getattr(constraint, "description", "")
            return f"Keyword check failed: {desc}"
        if "section" in name:
            return "Required section(s) missing from output"
        return f"Constraint '{name}' not satisfied"

    @staticmethod
    def _build_retry_prompt(original: str, failures: list, diagnostics: dict) -> str:
        diag_lines = "\n".join(
            f"  - {name}: {msg}" for name, msg in diagnostics.items()
        )
        return (
            f"{original}\n\n"
            f"[REVISION REQUIRED]\n"
            f"Your previous response failed these constraints:\n"
            f"{diag_lines}\n\n"
            f"Please regenerate your response, fixing these issues while "
            f"preserving the quality of your answer."
        )


# ---------------------------------------------------------------------------
# Planning Layer
# ---------------------------------------------------------------------------

class PlanningLayer:
    """
    Evaluates available tasks using EV / RAEV and delegates ranking to the
    injected strategy.  Also decides whether to invest in robustness.
    """

    def __init__(self, strategy: StrategyInterface, token_cost_fn):
        self.strategy = strategy
        self._token_cost_fn = token_cost_fn   # (model, in_tok, out_tok) -> float

    def score_task(
        self,
        task: Any,
        state: AgentState,
        pass_prob: float,
    ) -> ScoredContract:
        """Score a single task and wrap it as a ScoredContract."""
        # Token estimate scales with task tier: simpler tasks use fewer tokens.
        # T1≈200+100, T2≈400+200, T3≈600+300, T4+≈800+400
        tier_val = getattr(getattr(task, "tier", None), "value", 2)
        in_tokens  = max(200, min(800, 200 * tier_val))
        out_tokens = max(100, min(400, 100 * tier_val))
        est_token_cost = self._token_cost_fn(state.model_name, in_tokens, out_tokens)

        reward = task.reward
        penalty = task.penalty
        ev = pass_prob * reward - (1.0 - pass_prob) * penalty - est_token_cost

        # Risk premium: convex in penalty/balance — agents become risk-averse
        # as penalties approach their balance (spec Eq)
        balance = max(state.balance, 0.001)   # avoid divide-by-zero
        risk_prem = (penalty ** 2) / (2.0 * balance)
        raev = ev - risk_prem

        return ScoredContract(
            contract_id="",          # filled in by caller
            task_id=task.task_id,
            min_tier=task.tier,
            domain=task.domain,
            constraint_types=[c.name for c in task.constraints],
            reward=reward,
            penalty=penalty,
            deadline=0.0,
            difficulty=task.difficulty,
            estimated_pass_probability=pass_prob,
            estimated_token_cost=est_token_cost,
            expected_value=ev,
            risk_premium=risk_prem,
            risk_adjusted_ev=raev,
        )

    def select_task(
        self,
        available_tasks: list,
        state: AgentState,
        perception: PerceptionLayer,
        accounting: AccountingLayer,
    ) -> Optional[Any]:
        """
        Return the best task to attempt, or None if nothing is worthwhile.

        Safety checks run first (hard gates).
        Then contract evaluation.
        Then strategy ranking.
        """
        # --- Safety checks --------------------------------------------------
        if state.balance < AccountingLayer.MINIMUM_RESERVE:
            logger.warning(
                f"[{state.model_name}] balance {state.balance:.4f} below minimum "
                f"reserve — suspending"
            )
            return None

        # --- Score eligible tasks -------------------------------------------
        ceiling = state.remaining_ceiling
        utilisation_limit = ceiling * self.strategy.max_utilization()

        scored: list = []
        for task in available_tasks:
            # Tier eligibility
            if task.tier.value > state.effective_tier.value:
                continue
            # Budget eligibility (approximate — exact check in economy)
            if task.penalty > utilisation_limit:
                continue
            if not accounting.can_afford(task.penalty, token_cost=0.01):
                continue
            pp = perception.estimated_pass_prob(task)
            sc = self.score_task(task, state, pp)
            scored.append((task, sc))

        if not scored:
            return None

        # --- Strategy ranking -----------------------------------------------
        ranked_scores = self.strategy.rank_contracts(
            [sc for _, sc in scored], state
        )
        if not ranked_scores:
            return None

        # To avoid repetition, pick randomly from top N (e.g., top 3)
        top_n = ranked_scores[:3]
        selected_sc = random.choice(top_n)
        top_id = selected_sc.task_id
        for task, sc in scored:
            if task.task_id == top_id:
                if sc.risk_adjusted_ev > 0 or state.effective_tier == Tier.T0:
                    return task
        return None

    def investment_decision(self, state: AgentState) -> Optional[RobustnessInvestment]:
        return self.strategy.should_invest_robustness(state)


# ---------------------------------------------------------------------------
# Autonomous Agent
# ---------------------------------------------------------------------------

class AutonomousAgent:
    """
    v2 CGAE economic actor.

    Wraps an LLMAgent and adds:
    - Perception (constraint/domain pass-rate tracking)
    - Accounting (reserves, burn-rate, insolvency prevention)
    - Planning (EV/RAEV task selection, robustness investment decisions)
    - Execution (constraint-aware prompts, self-verification, retry)
    """

    def __init__(
        self,
        llm_agent: Any,
        strategy: StrategyInterface,
        token_cost_fn,            # (model_name, in_tok, out_tok) -> float
        self_verify: bool = True,
        max_retries: int = 2,
    ):
        self.llm = llm_agent
        self.model_name: str = llm_agent.model_name
        self.strategy = strategy

        self.perception = PerceptionLayer()
        self.accounting: Optional[AccountingLayer] = None    # set in register()
        self.execution = ExecutionLayer(llm_agent, self_verify=self_verify,
                                        max_retries=max_retries)
        self.planning = PlanningLayer(strategy, token_cost_fn)
        self._token_cost_fn = token_cost_fn

        # Set by economy on registration
        self.agent_id: Optional[str] = None

        # Metrics
        self.self_check_catches: int = 0    # self-check prevented a failure
        self.retry_successes: int = 0       # retry turned a failure into a pass
        self.strategy_actions: dict = {}

    def register(self, agent_id: str, initial_balance: float):
        """Call once after Economy.register_agent() to initialise accounting."""
        self.agent_id = agent_id
        self.accounting = AccountingLayer(initial_balance)

    def build_state(self, record: Any, gate: GateFunction) -> AgentState:
        """
        Construct an AgentState from an AgentRecord + gate details.
        Called at the start of every planning cycle.
        """
        self.accounting.sync_from_record(record)

        r = record.current_robustness or RobustnessVector(0.3, 0.3, 0.25, 0.5)
        gate_detail = gate.evaluate_with_detail(r)
        tier = gate_detail["tier"]
        ceiling = gate.budget_ceiling(tier)

        total = record.contracts_completed + record.contracts_failed
        win_rate = record.contracts_completed / max(1, total)

        return AgentState(
            agent_id=record.agent_id,
            model_name=self.model_name,
            certified_robustness=r,
            effective_robustness=r,    # decay applied externally by Economy
            certified_tier=tier,
            effective_tier=tier,
            binding_dimension=gate_detail.get("binding_dimension"),
            gap_to_next_tier={
                "cc": gate_detail.get("gap_to_next_tier") or 0.0
                if gate_detail.get("binding_dimension") == "cc" else 0.0,
                "er": gate_detail.get("gap_to_next_tier") or 0.0
                if gate_detail.get("binding_dimension") == "er" else 0.0,
                "as": gate_detail.get("gap_to_next_tier") or 0.0
                if gate_detail.get("binding_dimension") == "as" else 0.0,
            },
            balance=record.balance,
            available_for_contracts=self.accounting.available_for_contracts,
            active_exposure=self.accounting.active_exposure,
            remaining_ceiling=max(0.0, ceiling - self.accounting.active_exposure),
            burn_rate=self.accounting.burn_rate,
            rounds_until_insolvency=self.accounting.rounds_until_insolvency,
            roi=self.accounting.roi,
            constraint_pass_rates=self.perception.constraint_pass_rates,
            domain_pass_rates=self.perception.domain_pass_rates,
            total_contracts_completed=record.contracts_completed,
            total_contracts_failed=record.contracts_failed,
            win_rate=win_rate,
            time_since_certification=0.0,    # computed externally if needed
            spot_audit_probability=0.0,
        )

    def plan_task(
        self,
        available_tasks: list,
        state: AgentState,
    ) -> Optional[Any]:
        """
        Select the best task to attempt this round.
        Returns None if nothing worthwhile or reserves too low.
        """
        task = self.planning.select_task(
            available_tasks, state, self.perception, self.accounting
        )
        action = "bid" if task else "idle"
        self.strategy_actions[action] = self.strategy_actions.get(action, 0) + 1
        return task

    def execute_task(self, task: Any) -> ExecutionResult:
        """Execute a task with self-verification and retry."""
        result = self.execution.execute(task, self._token_cost_fn)

        # Track self-check performance
        if not result.self_check_passed and result.retries_used > 0:
            self.retry_successes += 1
        if result.self_check_failures:
            self.self_check_catches += 1

        return result

    def investment_decision(self, state: AgentState) -> Optional[RobustnessInvestment]:
        """Return a robustness investment if the strategy calls for it."""
        inv = self.planning.investment_decision(state)
        if inv:
            self.strategy_actions["invest"] = self.strategy_actions.get("invest", 0) + 1
        return inv

    def update_state(self, task: Any, verification: Any, token_cost: float):
        """Update perception and accounting after a contract settles."""
        self.perception.update_from_result(task, verification)
        self.accounting.record_round_cost(token_cost)

    def metrics_summary(self) -> dict:
        return {
            "model_name": self.model_name,
            "strategy": type(self.strategy).__name__,
            "self_check_catches": self.self_check_catches,
            "retry_successes": self.retry_successes,
            "self_check_catch_rate": (
                self.self_check_catches
                / max(1, self.self_check_catches + self.retry_successes)
            ),
            "strategy_actions": self.strategy_actions,
            "constraint_pass_rates": self.perception.constraint_pass_rates,
            "domain_pass_rates": self.perception.domain_pass_rates,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_autonomous_agent(
    llm_agent: Any,
    strategy_name: str,
    token_cost_fn,
    self_verify: bool = True,
    max_retries: int = 2,
) -> AutonomousAgent:
    """
    Instantiate an AutonomousAgent with a named strategy.

    strategy_name: "growth" | "conservative" | "opportunistic"
                   | "specialist" | "adversarial"
    """
    strategy = STRATEGY_MAP.get(strategy_name)
    if strategy is None:
        raise ValueError(
            f"Unknown strategy '{strategy_name}'. "
            f"Choose from: {list(STRATEGY_MAP)}"
        )
    return AutonomousAgent(
        llm_agent=llm_agent,
        strategy=strategy,
        token_cost_fn=token_cost_fn,
        self_verify=self_verify,
        max_retries=max_retries,
    )
