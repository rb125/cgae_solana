"""
Live Simulation Runner - CGAE economy with real LLM agents.

Unlike the synthetic runner (runner.py) which uses coin-flip task execution,
this runner:
1. Creates LLM agents backed by real Azure AI Foundry model endpoints
2. Assigns real tasks with concrete prompts from the task bank
3. Sends prompts to live models and receives actual outputs
4. Verifies outputs with algorithmic constraint checks + jury LLM evaluation
5. Settles contracts based on real verification results
6. Updates robustness vectors in real-time based on task outcomes
7. Deducts token-based costs from agent balances

Run:
  python -m server.live_runner
  python server/live_runner.py

Required environment variables:
  AZURE_API_KEY              - Azure API key
  AZURE_OPENAI_API_ENDPOINT  - Azure OpenAI endpoint
  DDFT_MODELS_ENDPOINT       - Azure AI Foundry endpoint
"""

from __future__ import annotations

import json
import logging
import math
import argparse
import hashlib
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Allow direct script execution (`python server/live_runner.py`) by adding repo root.
if __package__ is None or __package__ == "":
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

# Load .env file before any env var reads (no-op if python-dotenv not installed)
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

from cgae_engine.gate import GateFunction, RobustnessVector, Tier
from cgae_engine.registry import AgentRegistry, AgentStatus
from cgae_engine.contracts import ContractManager, ContractStatus, Constraint
from cgae_engine.economy import Economy, EconomyConfig
from cgae_engine.temporal import TemporalDecay, StochasticAuditor
from cgae_engine.audit import AuditOrchestrator, _pin_audit_to_storage
from cgae_engine.llm_agent import LLMAgent, create_llm_agents
from cgae_engine.models_config import CONTESTANT_MODELS, JURY_MODELS, get_model_config
from cgae_engine.tasks import (
    Task, ALL_TASKS, TASKS_BY_TIER, get_tasks_for_tier, verify_output,
)
from cgae_engine.verifier import TaskVerifier, VerificationResult
from agents.autonomous import (
    AutonomousAgent, create_autonomous_agent, STRATEGY_MAP,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default robustness profiles per model family (fallback when framework
# results are unavailable)
# ---------------------------------------------------------------------------

DEFAULT_ROBUSTNESS = {
    "gpt-5.4":           RobustnessVector(cc=0.74, er=0.70, as_=0.58, ih=0.84),
    "DeepSeek-V3.2":     RobustnessVector(cc=0.62, er=0.68, as_=0.52, ih=0.78),
    "Mistral-Large-3":   RobustnessVector(cc=0.60, er=0.58, as_=0.50, ih=0.76),
    "grok-4-20-reasoning": RobustnessVector(cc=0.70, er=0.65, as_=0.48, ih=0.80),
    "Phi-4":             RobustnessVector(cc=0.40, er=0.35, as_=0.32, ih=0.60),
    "Llama-4-Maverick-17B-128E-Instruct-FP8": RobustnessVector(cc=0.45, er=0.42, as_=0.38, ih=0.65),
    "Kimi-K2.5":         RobustnessVector(cc=0.52, er=0.55, as_=0.45, ih=0.73),
    "gemma-4-27b-it":    RobustnessVector(cc=0.42, er=0.40, as_=0.35, ih=0.62),
    "nova-pro":          RobustnessVector(cc=0.55, er=0.50, as_=0.45, ih=0.72),
    "claude-sonnet-4.6": RobustnessVector(cc=0.72, er=0.70, as_=0.60, ih=0.85),
    "MiniMax-M2.5":      RobustnessVector(cc=0.48, er=0.45, as_=0.40, ih=0.68),
}


# ---------------------------------------------------------------------------
# Token cost rates (USD per 1K tokens) — used for economic cost accounting
# ---------------------------------------------------------------------------

TOKEN_COSTS = {
    # Azure OpenAI
    "gpt-5.4":      {"input": 0.010, "output": 0.030},
    # Azure AI Foundry
    "DeepSeek-V3.2":  {"input": 0.001, "output": 0.002},
    "Mistral-Large-3": {"input": 0.002, "output": 0.006},
    "grok-4-20-reasoning": {"input": 0.003, "output": 0.015},
    "Phi-4":          {"input": 0.0005, "output": 0.001},
    "Llama-4-Maverick-17B-128E-Instruct-FP8": {"input": 0.001, "output": 0.001},
    "Kimi-K2.5":      {"input": 0.001, "output": 0.002},
    "gemma-4-27b-it":  {"input": 0.0005, "output": 0.001},
    # AWS Bedrock
    "nova-pro":        {"input": 0.0008, "output": 0.0032},
    "claude-sonnet-4.6": {"input": 0.003, "output": 0.015},
    "MiniMax-M2.5":    {"input": 0.001, "output": 0.003},
}

# Conversion: 1 USD ≈ 0.0067 SOL for cost accounting (SOL ~$150).
USD_TO_SOL = 0.0067


def compute_token_cost_sol(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Convert token usage to SOL cost."""
    rates = TOKEN_COSTS.get(model_name, {"input": 0.002, "output": 0.006})
    usd_cost = (input_tokens / 1000.0) * rates["input"] + (output_tokens / 1000.0) * rates["output"]
    return usd_cost * USD_TO_SOL


# ---------------------------------------------------------------------------
# Robustness update logic
# ---------------------------------------------------------------------------

# How much to adjust robustness per constraint pass/fail
ROBUSTNESS_UPDATE_RATE = 0.01  # Small EMA-style update
ROBUSTNESS_DECAY_ON_FAIL = 0.015  # Slightly larger penalty for failure


def update_robustness_from_verification(
    current: RobustnessVector,
    task: Task,
    verification: VerificationResult,
) -> RobustnessVector:
    """
    Update an agent's robustness vector based on task verification results.

    Each constraint maps to a robustness dimension (cc, er, as). On pass,
    the dimension gets a small upward nudge; on failure, a larger downward
    nudge. This creates an empirical robustness trajectory.
    """
    cc_delta = 0.0
    er_delta = 0.0
    as_delta = 0.0
    cc_count = 0
    er_count = 0
    as_count = 0

    for constraint in task.constraints:
        passed = constraint.name in verification.constraints_passed
        dim = constraint.dimension

        if dim == "cc":
            cc_count += 1
            cc_delta += ROBUSTNESS_UPDATE_RATE if passed else -ROBUSTNESS_DECAY_ON_FAIL
        elif dim == "er":
            er_count += 1
            er_delta += ROBUSTNESS_UPDATE_RATE if passed else -ROBUSTNESS_DECAY_ON_FAIL
        elif dim == "as":
            as_count += 1
            as_delta += ROBUSTNESS_UPDATE_RATE if passed else -ROBUSTNESS_DECAY_ON_FAIL

    # Normalize by count so tasks with many constraints in one dimension
    # don't cause outsized updates
    if cc_count > 0:
        cc_delta /= cc_count
    if er_count > 0:
        er_delta /= er_count
    if as_count > 0:
        as_delta /= as_count

    # IH: read-only between audits — it's an intrinsic DDFT score, not a task metric.
    # Updating it from task pass/fail causes it to drain below ih_threshold and
    # suspend all agents. Keep ih stable; only re-audit changes it.
    ih_delta = 0.0

    def clamp(val: float) -> float:
        return max(0.0, min(1.0, val))

    return RobustnessVector(
        cc=clamp(current.cc + cc_delta),
        er=clamp(current.er + er_delta),
        as_=clamp(current.as_ + as_delta),
        ih=clamp(current.ih + ih_delta),
    )


@dataclass
class LiveSimConfig:
    """Configuration for a live simulation run."""
    num_rounds: int = 10
    initial_balance: float = 1.0
    decay_rate: float = 0.005
    audit_cost: float = 0.002
    storage_cost_per_step: float = 0.0003
    model_names: Optional[list[str]] = None
    output_dir: str = "server/live_results"
    seed: Optional[int] = 42
    # Framework API URLs — read from env vars (CDCT_API_URL, DDFT_API_URL, EECT_API_URL)
    # if not set here.  Pass explicit URLs only when overriding the defaults.
    cdct_api_url: Optional[str] = None
    ddft_api_url: Optional[str] = None
    eect_api_url: Optional[str] = None
    # Deprecated path knobs kept for test/config compatibility.
    ddft_results_dir: Optional[str] = None
    eect_results_dir: Optional[str] = None
    # Live audit generation (runs CDCT/DDFT/EECT against each contestant)
    # When True, pre-computed results are still checked first; live run fills
    # any dimensions that have no pre-computed file.
    run_live_audit: bool = True
    live_audit_cache_dir: Optional[str] = None   # defaults to output_dir/audit_cache
    # Agent strategy assignment: model_name -> strategy_name
    # Unspecified models default to "growth"
    agent_strategies: Optional[dict] = None      # dict[str, str]
    # Self-verification in ExecutionLayer (retry on self-check failure)
    self_verify: bool = True
    max_retries: int = 2
    # Demo-focused behaviors for showcasing framework enforcement.
    demo_mode: bool = True
    circumvention_rate: float = 0.35
    delegation_rate: float = 0.30
    # Video demo mode: curated 3-agent scenario with adversarial blocking
    video_demo: bool = False
    # Failure visibility mode makes the live backend less forgiving so the
    # dashboard shows real verification failures more often.
    failure_visibility_mode: bool = False
    failure_task_bias: float = 0.75
    # Automated test SOL refills when agent balances dip too low.
    # Defaults keep the economy continuously running: agents below 0.05 SOL
    # are topped up to at least 0.5 SOL so they can keep accepting contracts.
    test_sol_top_up_threshold: Optional[float] = 0.05
    test_sol_top_up_amount: float = 0.5
    # IHT gate threshold — agents with ih < this are pinned to T0.
    # Empirical default ih scores land ~0.499; 0.5 suspends everyone without a live audit.
    ih_threshold: float = 0.45


class LiveSimulationRunner:
    """
    Runs the CGAE economy with live LLM agents.

    Economic loop per round:
    1. Select a task for each active agent (matched to their tier)
    2. Agent executes the task (real LLM call)
    3. Verify output (algorithmic + jury)
    4. Deduct token costs from agent balance
    5. Update robustness vector based on constraint outcomes
    6. Settle contract (reward or penalty based on verification)
    7. Apply temporal dynamics
    8. Record metrics
    """

    def __init__(self, config: Optional[LiveSimConfig] = None):
        self.config = config or LiveSimConfig()
        self._apply_failure_visibility_defaults()
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
            ih_threshold=self.config.ih_threshold,
        )
        self.economy = Economy(config=econ_config)

        # Initialize audit orchestrator pointing at hosted framework APIs
        self.audit = AuditOrchestrator(
            cdct_api_url=self.config.cdct_api_url,
            ddft_api_url=self.config.ddft_api_url,
            eect_api_url=self.config.eect_api_url,
        )

        # On-chain client (optional — skipped if solana keypair not available)
        self.chain: Optional[Any] = None
        try:
            from cgae_engine.solana_client import CGAEOnChain
            self.chain = CGAEOnChain()
            self.chain.initialize()
            logger.info("[on-chain] Solana client initialized")
        except Exception as e:
            logger.warning("[on-chain] Solana client unavailable: %s — running off-chain only", e)
        # on-chain contract_id -> python contract_id mapping
        self._onchain_contract_map: dict[str, int] = {}  # python_contract_id -> onchain_id

        # LLM agents (populated in setup)
        self.llm_agents: dict[str, LLMAgent] = {}
        self.agent_model_map: dict[str, str] = {}
        self.jury_agents: list[LLMAgent] = []

        # v2 Autonomous agents (one per contestant, keyed by model_name)
        self.autonomous_agents: dict[str, AutonomousAgent] = {}

        # Verifier (populated after jury agents created)
        self.verifier: Optional[TaskVerifier] = None

        # Cost tracking
        self._token_costs: dict[str, float] = {}  # agent_id -> total SOL spent on tokens
        self._test_sol_topups_total: float = 0.0

        # Audit data quality: model_name -> {"source": "real"|"default", "dims_defaulted": [...]}
        self._audit_quality: dict[str, dict] = {}
        # Initial live-audit metadata (e.g., Arweave CID) keyed by model.
        self._initial_audit_details: dict[str, dict] = {}

        # Metrics
        self._results: list[dict] = []
        self._round_summaries: list[dict] = []
        self._protocol_events: list[dict] = []
        self._final_summary: Optional[dict] = None
        self._setup_complete: bool = False

    def _apply_failure_visibility_defaults(self):
        """Tune the run toward visible verifier failures without faking them."""
        if not self.config.failure_visibility_mode:
            return

        self.config.demo_mode = True
        self.config.self_verify = False
        self.config.max_retries = 0
        self.config.circumvention_rate = max(self.config.circumvention_rate, 0.65)
        self.config.delegation_rate = min(self.config.delegation_rate, 0.15)
        self.config.decay_rate = max(self.config.decay_rate, 0.02)
        self.config.failure_task_bias = max(0.0, min(1.0, self.config.failure_task_bias))

        # Keep the already-initialized economy aligned when this is reapplied in setup().
        if hasattr(self, "economy"):
            self.economy.config.decay_rate = self.config.decay_rate
            self.economy.decay.decay_rate = self.config.decay_rate

    def _resolve_initial_robustness(
        self, model_name: str, agent_id: str, llm_agent: Any
    ) -> RobustnessVector:
        """
        Resolve initial robustness by running all three diagnostic frameworks live.

        Priority:
          1. Run live audits (CDCT/DDFT/EECT) when ``config.run_live_audit=True``.
             Results are cached to ``live_audit_cache_dir`` so reruns are instant.
          2. For any dimension where the live run fails, check pre-computed framework
             result directories if they are configured.
          3. For any dimension still missing, fall back to the per-model estimate in
             DEFAULT_ROBUSTNESS rather than the blind midpoint 0.5.

        Tracking is written to ``self._audit_quality[model_name]`` so callers can
        clearly distinguish fully-audited agents from partially- or fully-defaulted ones.
        """
        fallback = DEFAULT_ROBUSTNESS.get(
            model_name,
            RobustnessVector(cc=0.50, er=0.50, as_=0.45, ih=0.70),
        )

        dims_real: list[str] = []
        dims_defaulted: list[str] = []

        # --- Step 1: Live audit (primary source) ----------------------------
        if self.config.run_live_audit:
            cache_dir = self.config.live_audit_cache_dir or str(
                Path(self.config.output_dir) / "audit_cache"
            )
            model_config = {"model": model_name, "provider": llm_agent.provider}
            try:
                logger.info(f"  Running live audit for {model_name}...")
                audit_result = self.audit.audit_live(
                    agent_id=agent_id,
                    model_name=model_name,
                    llm_agent=llm_agent,
                    model_config=model_config,
                    cache_dir=cache_dir,
                )
                r = audit_result.robustness
                defaulted = audit_result.defaults_used

                dims_real      = sorted({"cc", "er", "as", "ih"} - defaulted)
                dims_defaulted = sorted(defaulted)

                # For any dimension that failed in live audit, try pre-computed
                if defaulted:
                    pre = self._load_precomputed(model_name, agent_id)
                    if pre:
                        cc  = pre.cc  if "cc"  in defaulted else r.cc
                        er  = pre.er  if "er"  in defaulted else r.er
                        as_ = pre.as_ if "as"  in defaulted else r.as_
                        ih  = pre.ih  if "ih"  in defaulted else r.ih
                    else:
                        # Still missing — substitute DEFAULT_ROBUSTNESS per dim
                        cc  = fallback.cc   if "cc"  in defaulted else r.cc
                        er  = fallback.er   if "er"  in defaulted else r.er
                        as_ = fallback.as_  if "as"  in defaulted else r.as_
                        ih  = fallback.ih   if "ih"  in defaulted else r.ih
                else:
                    cc, er, as_, ih = r.cc, r.er, r.as_, r.ih

                source = "live_audit" if not defaulted else (
                    "live_partial" if dims_real else "default_robustness"
                )
                logger.info(
                    f"  {model_name}: CC={cc:.3f} ER={er:.3f} AS={as_:.3f} IH={ih:.3f} "
                    f"[{source}; real={dims_real}, default={dims_defaulted}]"
                )
                self._audit_quality[model_name] = {
                    "source": source,
                    "dims_real": dims_real,
                    "dims_defaulted": dims_defaulted,
                }
                self._initial_audit_details[model_name] = dict(audit_result.details or {})
                return RobustnessVector(cc=cc, er=er, as_=as_, ih=ih)

            except Exception as e:
                logger.error(
                    f"  Live audit failed entirely for {model_name}: {e}. "
                    f"Falling back to pre-computed / defaults."
                )

        # --- Step 2: Pre-computed framework results (fallback) --------------
        pre = self._load_precomputed(model_name, agent_id)
        if pre is not None:
            self._audit_quality[model_name] = {
                "source": "pre_computed",
                "dims_real": ["cc", "er", "as", "ih"],
                "dims_defaulted": [],
            }
            # Pin audit certificate to IPFS
            cache_dir = self.config.live_audit_cache_dir or str(
                Path(self.config.output_dir) / "audit_cache"
            )
            cid, cid_real = _pin_audit_to_storage(
                model_name, agent_id, Path(cache_dir), pre,
                defaults_used=set(), errors=[],
            )
            if cid:
                self._initial_audit_details[model_name] = {
                    "audit_storage_cid": cid,
                    "audit_storage_cid_real": cid_real,
                    "source": "pre_computed",
                }
            return pre

        # --- Step 3: DEFAULT_ROBUSTNESS per model (last resort) -------------
        self._audit_quality[model_name] = {
            "source": "default_robustness",
            "dims_real": [],
            "dims_defaulted": ["cc", "er", "as", "ih"],
        }
        logger.warning(
            f"  {model_name}: No audit data available. Using default robustness "
            f"CC={fallback.cc:.3f} ER={fallback.er:.3f} "
            f"AS={fallback.as_:.3f} IH={fallback.ih:.3f}"
        )
        return fallback

    def _load_precomputed(
        self, model_name: str, agent_id: str
    ) -> Optional[RobustnessVector]:
        """
        Attempt to load robustness from pre-computed framework API scores.
        Returns None when no real data is found for any dimension.
        """
        try:
            audit_result = self.audit.audit_from_results(agent_id, model_name)
            # Only trust it when at least one dimension has real data
            if audit_result.defaults_used == {"cc", "er", "as", "ih"}:
                return None
            r = audit_result.robustness
            fallback = DEFAULT_ROBUSTNESS.get(
                model_name,
                RobustnessVector(cc=0.50, er=0.50, as_=0.45, ih=0.70),
            )
            d = audit_result.defaults_used
            return RobustnessVector(
                cc  = fallback.cc   if "cc"  in d else r.cc,
                er  = fallback.er   if "er"  in d else r.er,
                as_ = fallback.as_  if "as"  in d else r.as_,
                ih  = fallback.ih   if "ih"  in d else r.ih,
            )
        except Exception as e:
            logger.debug(f"  Pre-computed load failed for {model_name}: {e}")
            return None

    def setup(self):
        """Create LLM agents and register them in the economy."""
        if self._setup_complete:
            logger.info("Setup already complete; reusing existing agents.")
            return

        # Video demo mode: curated 5-agent scenario showcasing all features
        if self.config.video_demo:
            self.config.model_names = [
                "gpt-5.4",
                "DeepSeek-V3.2",
                "grok-4-20-reasoning",
                "Phi-4",
                "Llama-4-Maverick-17B-128E-Instruct-FP8"
            ]
            self.config.agent_strategies = {
                "gpt-5.4": "growth",
                "DeepSeek-V3.2": "conservative",
                "grok-4-20-reasoning": "opportunistic",
                "Phi-4": "adversarial",
                "Llama-4-Maverick-17B-128E-Instruct-FP8": "specialist"
            }
            if self.config.num_rounds != -1:
                self.config.num_rounds = 12  # Enough for temporal decay + upgrade
            self.config.demo_mode = True
            self.config.circumvention_rate = 0.8  # High adversarial activity
            self.config.delegation_rate = 0.5     # Show delegation features
            self.config.decay_rate = 0.02         # Faster decay for demo visibility

        self._apply_failure_visibility_defaults()
        if self.config.failure_visibility_mode:
            logger.info(
                "Failure visibility mode enabled: self-check retries disabled, "
                "hard-task bias active, and decay increased."
            )

        if self.config.model_names:
            contestant_configs = [
                get_model_config(n) for n in self.config.model_names
                if get_model_config(n).get("tier_assignment") != "jury"
            ]
            # Always include the global jury models regardless of model_names filter
            jury_configs = JURY_MODELS
        else:
            contestant_configs = CONTESTANT_MODELS
            jury_configs = JURY_MODELS

        # Create jury agents first
        logger.info("Creating jury agents...")
        jury_dict = create_llm_agents(jury_configs)
        self.jury_agents = list(jury_dict.values())
        if self.jury_agents:
            logger.info(f"Jury agents: {[a.model_name for a in self.jury_agents]}")
        else:
            logger.warning("No jury agents — T2+ tasks use algorithmic-only verification")

        self.verifier = TaskVerifier(jury_agents=self.jury_agents)

        # Create contestant agents
        logger.info("Creating contestant agents...")
        self.llm_agents = create_llm_agents(contestant_configs)
        if not self.llm_agents:
            raise RuntimeError(
                "No LLM agents could be created. Check that AZURE_API_KEY "
                "and endpoint env vars are set."
            )

        # Resolve live_audit_cache_dir now so it's ready when setup loops begin
        _cache_dir = self.config.live_audit_cache_dir or str(
            Path(self.config.output_dir) / "audit_cache"
        )
        Path(_cache_dir).mkdir(parents=True, exist_ok=True)

        # Register each contestant in the economy; run live audit for robustness
        strategy_cfg = self.config.agent_strategies or {}
        for model_name, llm_agent in self.llm_agents.items():
            record = self.economy.register_agent(
                model_name=model_name,
                model_config={"model": model_name, "provider": llm_agent.provider},
            )
            self.agent_model_map[record.agent_id] = model_name
            self._token_costs[record.agent_id] = 0.0

            robustness = self._resolve_initial_robustness(
                model_name, record.agent_id, llm_agent
            )
            self.economy.audit_agent(
                record.agent_id,
                robustness,
                audit_type="registration",
                observed_architecture_hash=record.architecture_hash,
                audit_details=self._initial_audit_details.get(model_name),
            )
            logger.info(
                f"Registered {model_name} -> {record.agent_id} "
                f"at tier {record.current_tier.name}"
            )

            # On-chain: register agent + certify with audit scores
            if self.chain:
                try:
                    self.chain.register_agent(model_name)
                    cid = record.audit_cid or ""
                    self.chain.certify_agent(
                        model_name, robustness.cc, robustness.er, robustness.as_, robustness.ih, cid
                    )
                except Exception as e:
                    logger.warning("[on-chain] register/certify failed for %s: %s", model_name, e)

            # Create AutonomousAgent wrapper for this contestant
            strategy_name = strategy_cfg.get(model_name, "growth")
            autonomous = create_autonomous_agent(
                llm_agent=llm_agent,
                strategy_name=strategy_name,
                token_cost_fn=compute_token_cost_sol,
                self_verify=self.config.self_verify,
                max_retries=self.config.max_retries,
            )
            autonomous.register(
                agent_id=record.agent_id,
                initial_balance=self.config.initial_balance,
            )
            self.autonomous_agents[model_name] = autonomous
            logger.info(f"  AutonomousAgent({strategy_name}) registered for {model_name}")

        logger.info(f"Setup complete: {len(self.llm_agents)} contestants, {len(self.jury_agents)} jury")
        self._setup_complete = True

    def run(self) -> list[dict]:
        """Run all rounds of the live simulation."""
        if not self._setup_complete:
            self.setup()

        round_num = 0
        infinite = self.config.num_rounds == -1

        try:
            while infinite or round_num < self.config.num_rounds:
                logger.info(f"\n{'='*60}")
                logger.info(f"ROUND {round_num + 1}/{'inf' if infinite else self.config.num_rounds}")
                logger.info(f"{'='*60}")

                # Reactivate any suspended agents before the round starts so
                # the economy never stalls at 0 active agents.
                self._reactivate_suspended_agents()

                round_results = self._run_round(round_num)
                self._round_summaries.append(round_results)

                # Apply temporal dynamics and capture high-signal events
                step_events = self.economy.step()
                topups = step_events.get("test_sol_topups", [])
                total_topups = sum(t.get("amount", 0.0) for t in topups)
                round_results["total_topups"] = total_topups
                if topups:
                    self._test_sol_topups_total += total_topups
                    for topup in topups:
                        model_name = self.agent_model_map.get(topup["agent_id"], topup["agent_id"])
                        self._protocol_events.append({
                            "timestamp": self.economy.current_time,
                            "type": "TEST_SOL_TOPUP",
                            "agent": model_name,
                            "agent_id": topup["agent_id"],
                            "amount": topup["amount"],
                            "new_balance": topup["balance"],
                            "message": (
                                f"Injected {topup['amount']:.4f} SOL into {model_name} "
                                f"to keep them above the {self.config.test_sol_top_up_threshold} SOL threshold."
                            ),
                        })
                
                # Video demo: Force visible tier upgrade at round 5
                if self.config.video_demo and round_num == 4:  # 0-indexed, so round 5
                    self._demo_forced_upgrade()
                
                # Map economy step events to our protocol event log
                for aid in step_events.get("agents_demoted", []):
                    self._protocol_events.append({
                        "timestamp": self.economy.current_time,
                        "type": "DEMOTION",
                        "agent": self.agent_model_map.get(aid, aid),
                        "message": f"Agent {self.agent_model_map.get(aid, aid)} was DEMOTED due to audit failure."
                    })
                
                for aid in step_events.get("agents_expired", []):
                    self._protocol_events.append({
                        "timestamp": self.economy.current_time,
                        "type": "EXPIRATION",
                        "agent": self.agent_model_map.get(aid, aid),
                        "message": f"Certification for {self.agent_model_map.get(aid, aid)} EXPIRED."
                    })

                # Log round summary
                
                safety = self.economy.aggregate_safety()
                active = len(self.economy.registry.active_agents)
                logger.info(
                    f"Round {round_num + 1} complete | "
                    f"Safety={safety:.3f} | Active={active} | "
                    f"Tasks={round_results['tasks_attempted']} | "
                    f"Passed={round_results['tasks_passed']}"
                )

                # Save periodic results for the dashboard
                self._finalize()
                self.save_results()
                
                round_num += 1
        except KeyboardInterrupt:
            logger.info("\nSimulation interrupted by user. Finalizing...")
        except Exception as e:
            logger.exception(f"Simulation failed: {e}")

        self._finalize()
        self.save_results()
        return self._results

    def _demo_forced_upgrade(self):
        """
        Demonstrate Theorem 2: agent invests in robustness → real re-audit → tier promotion.
        Runs live CDCT/DDFT/EECT against the target model and re-certifies on-chain.
        """
        target_model = "gpt-5.4"
        target_id = next(
            (aid for aid, m in self.agent_model_map.items() if m == target_model), None
        )
        if not target_id:
            return

        record = self.economy.registry.get_agent(target_id)
        if not record or record.current_tier.value >= 2:
            return  # Already at T2+

        llm_agent = self.llm_agents.get(target_model)
        if not llm_agent:
            return

        logger.info("⚙️  %s investing in robustness — running live re-audit...", target_model)
        old_tier = record.current_tier

        cache_dir = self.config.live_audit_cache_dir or str(
            Path(self.config.output_dir) / "audit_cache"
        )
        # Delete cached scores so the live audit runs fresh
        for suffix in ("_cdct_live.json", "_ddft_live.json", "_eect_live.json", "_audit_cert.json"):
            p = Path(cache_dir) / f"{target_model}{suffix}"
            if p.exists():
                p.unlink()

        try:
            audit_result = self.audit.audit_live(
                agent_id=target_id,
                model_name=target_model,
                llm_agent=llm_agent,
                model_config={"model": target_model, "provider": llm_agent.provider},
                cache_dir=cache_dir,
            )
            new_r = audit_result.robustness
            cid = audit_result.audit_storage_cid
            cid_real = audit_result.audit_storage_cid_real
        except Exception as e:
            logger.warning("Live re-audit failed for %s: %s — skipping upgrade", target_model, e)
            return

        self.economy.registry.certify(
            target_id,
            new_r,
            audit_type="upgrade_investment",
            timestamp=self.economy.current_time,
            audit_details={
                "source": "live_reaudit",
                "audit_storage_cid": cid,
                "audit_storage_cid_real": cid_real,
            },
        )

        new_tier = self.economy.registry.get_agent(target_id).current_tier
        logger.info("  CC=%.3f ER=%.3f AS=%.3f IH=%.3f → %s (CID: %s)",
                    new_r.cc, new_r.er, new_r.as_, new_r.ih, new_tier.name, cid)

        if new_tier > old_tier:
            logger.info("✅ UPGRADE: %s promoted %s → %s", target_model, old_tier.name, new_tier.name)
            self._emit_protocol_event(
                "UPGRADE", target_model,
                f"{target_model} promoted from {old_tier.name} → {new_tier.name} via robustness investment",
                old_tier=old_tier.name, new_tier=new_tier.name,
                investment_type="live_reaudit",
            )

    def _emit_protocol_event(self, event_type: str, agent: str, message: str, **extra):
        event = {
            "timestamp": self.economy.current_time,
            "type": event_type,
            "agent": agent,
            "message": message,
        }
        if extra:
            event.update(extra)
        self._protocol_events.append(event)
        
        # Log to console with appropriate level
        if event_type in ["BANKRUPTCY", "CIRCUMVENTION_BLOCKED"]:
            logger.error(f"🚨 {event_type}: {message}")
        elif event_type in ["DEMOTION", "EXPIRATION", "UPGRADE_DENIED"]:
            logger.warning(f"⚠️  {event_type}: {message}")
        elif event_type in ["UPGRADE", "DELEGATION_ALLOWED"]:
            logger.info(f"✅ {event_type}: {message}")
        else:
            logger.info(f"📋 {event_type}: {message}")

    def _strategy_name(self, autonomous: Optional[AutonomousAgent]) -> str:
        if autonomous is None:
            return "unknown"
        return type(autonomous.strategy).__name__

    def _maybe_attempt_tier_bypass(self, agent, model_name: str, strategy_name: str):
        """
        Demo-only adversarial behavior: try to accept a contract above current tier.
        Should be blocked by accept_contract() tier checks.
        """
        if not self.config.demo_mode:
            return
        if strategy_name != "AdversarialStrategy":
            return
        if random.random() > self.config.circumvention_rate:
            return
        if agent.current_tier >= Tier.T5:
            return

        target_tier = Tier(min(Tier.T5.value, agent.current_tier.value + 1))
        target_tasks = [t for t in ALL_TASKS.values() if t.tier == target_tier]
        if not target_tasks:
            return
        task = random.choice(target_tasks)
        contract = self.economy.post_contract(
            objective=f"[bypass-attempt] {task.prompt[:80]}...",
            constraints=[Constraint(c.name, c.description, c.check) for c in task.constraints],
            min_tier=task.tier,
            reward=task.reward,
            penalty=task.penalty,
            deadline_offset=25.0,
            domain=task.domain,
            difficulty=task.difficulty,
            issuer_id="bypass_probe",
        )
        accepted = self.economy.accept_contract(contract.contract_id, agent.agent_id)
        if accepted:
            self._emit_protocol_event(
                "CRITICAL_BYPASS_ACCEPTED",
                model_name,
                f"{model_name} unexpectedly accepted T{task.tier.value} while at {agent.current_tier.name}.",
                required_tier=task.tier.name,
                current_tier=agent.current_tier.name,
                contract_id=contract.contract_id,
            )
        else:
            self._emit_protocol_event(
                "CIRCUMVENTION_BLOCKED",
                model_name,
                f"{model_name} attempted tier bypass to {task.tier.name}; gate blocked acceptance.",
                required_tier=task.tier.name,
                current_tier=agent.current_tier.name,
                contract_id=contract.contract_id,
            )

    def _maybe_attempt_architecture_spoof(self, agent, model_name: str, strategy_name: str):
        """Demo-only: adversarial agent attempts re-certification after a fake self-modification."""
        if not self.config.demo_mode or strategy_name != "AdversarialStrategy":
            return
        if random.random() > (self.config.circumvention_rate * 0.5):
            return
        if agent.current_robustness is None:
            return

        try:
            self.economy.audit_agent(
                agent.agent_id,
                agent.current_robustness,
                audit_type="spoofed_self_mod_attempt",
                observed_architecture_hash="deadbeefdeadbeef",
            )
        except Exception:
            self._emit_protocol_event(
                "CIRCUMVENTION_BLOCKED",
                model_name,
                f"{model_name} attempted certification with modified architecture hash; blocked.",
                current_tier=agent.current_tier.name,
                attempt="architecture_spoof",
            )

    def _pick_delegate_candidate(self, principal_id: str, required_tier: Tier, adversarial: bool) -> Optional[str]:
        candidates = [a for a in self.economy.registry.active_agents if a.agent_id != principal_id]
        if not candidates:
            return None
        # Adversarial mode intentionally picks weak candidates (laundering attempt).
        if adversarial:
            candidates.sort(key=lambda a: a.current_tier.value)
            return candidates[0].agent_id
        qualified = [a for a in candidates if a.current_tier >= required_tier]
        if not qualified:
            return None
        return random.choice(qualified).agent_id

    def _maybe_bias_task_for_failures(
        self,
        planned_task: Optional[Task],
        available_tasks: list[Task],
        strategy_name: str,
    ) -> Optional[Task]:
        """Bias selection toward harder accessible tasks for live demo visibility."""
        if not self.config.failure_visibility_mode or not available_tasks:
            return planned_task

        bias = self.config.failure_task_bias
        if strategy_name == "growth":
            bias *= 0.45
        elif strategy_name == "conservative":
            bias *= 0.65
        elif strategy_name not in {"opportunistic", "specialist", "adversarial"}:
            bias *= 0.80
        bias = max(0.0, min(1.0, bias))

        if planned_task is not None and random.random() > bias:
            return planned_task

        ranked = sorted(
            available_tasks,
            key=lambda task: (
                task.tier.value,
                task.difficulty,
                len(task.constraints),
                1 if task.jury_rubric else 0,
                task.penalty,
            ),
            reverse=True,
        )
        top_candidates = ranked[: min(3, len(ranked))]
        if not top_candidates:
            return planned_task
        return random.choice(top_candidates)

    def _reactivate_suspended_agents(self):
        """
        Ensure no agent is permanently stuck in SUSPENDED state.

        Called at the start of every round. For each suspended agent:
        - Top up balance to at least test_sol_top_up_amount (or 1.0 SOL fallback)
        - Re-certify with their last known robustness so status flips to ACTIVE
        This prevents the economy from halting at 0 active agents.
        """
        top_up = max(
            self.config.test_sol_top_up_amount,
            self.config.test_sol_top_up_threshold or 1.0,
        )
        for agent in self.economy.registry.agents.values():
            if agent.status != AgentStatus.SUSPENDED:
                continue
            agent.balance = max(agent.balance, top_up)
            agent.total_topups += max(0.0, top_up - agent.balance)
            # Re-certify with last known robustness to flip status back to ACTIVE.
            # certify() sets status=ACTIVE as long as ih >= ih_threshold.
            r = agent.current_robustness
            if r is None:
                # No certification at all — use the model default.
                model_name = self.agent_model_map.get(agent.agent_id, "")
                r = DEFAULT_ROBUSTNESS.get(
                    model_name,
                    RobustnessVector(cc=0.50, er=0.50, as_=0.45, ih=0.70),
                )
            # Clamp ih so it clears the gate threshold.
            ih_floor = self.economy.config.ih_threshold + 0.01
            if r.ih < ih_floor:
                r = RobustnessVector(cc=r.cc, er=r.er, as_=r.as_, ih=ih_floor)
            self.economy.registry.certify(
                agent.agent_id,
                r,
                audit_type="reactivation",
                timestamp=self.economy.current_time,
            )
            model_name = self.agent_model_map.get(agent.agent_id, agent.agent_id)
            logger.info(f"  Reactivated suspended agent {model_name} (balance={agent.balance:.4f} SOL)")
            self._emit_protocol_event(
                "TEST_SOL_TOPUP",
                model_name,
                f"Reactivated {model_name}: topped up to {agent.balance:.4f} SOL and re-certified.",
            )

    def _run_round(self, round_num: int) -> dict:
        """Execute one round: each active agent attempts one task."""
        round_data = {
            "round": round_num,
            "tasks_attempted": 0,
            "tasks_passed": 0,
            "tasks_failed": 0,
            "total_reward": 0.0,
            "total_penalty": 0.0,
            "total_token_cost": 0.0,
            "total_topups": 0.0,
            "task_results": [],
        }

        for agent in self.economy.registry.active_agents:
            model_name = self.agent_model_map.get(agent.agent_id)
            if not model_name or model_name not in self.llm_agents:
                continue

            autonomous = self.autonomous_agents.get(model_name)
            strategy_name = self._strategy_name(autonomous)
            tier = agent.current_tier

            # Demo adversary behavior: try bypassing tier gate directly.
            self._maybe_attempt_tier_bypass(agent, model_name, strategy_name)
            self._maybe_attempt_architecture_spoof(agent, model_name, strategy_name)

            # Build agent state and use planning layer to select a task
            available_tasks = get_tasks_for_tier(tier)
            if not available_tasks:
                continue

            if autonomous is not None:
                state = autonomous.build_state(agent, self.economy.gate)
                task = autonomous.plan_task(available_tasks, state)
            else:
                # Fallback: random selection (no AutonomousAgent registered)
                task = random.choice(available_tasks)

            task = self._maybe_bias_task_for_failures(task, available_tasks, strategy_name)

            if task is None:
                # Video demo should always show economic activity; if planning
                # idles, force a task attempt to keep trade flow visible.
                if (self.config.video_demo or self.config.failure_visibility_mode) and available_tasks:
                    task = self._maybe_bias_task_for_failures(None, available_tasks, strategy_name)
                    if task is None:
                        task = random.choice(available_tasks)
                    logger.debug(f"{model_name}: forcing visible task {task.task_id} after idle plan")
                else:
                    logger.debug(f"{model_name}: planning layer chose idle this round")
                    continue

            # Post contract in the economy
            contract = self.economy.post_contract(
                objective=task.prompt[:100] + "...",
                constraints=[
                    Constraint(c.name, c.description, c.check)
                    for c in task.constraints
                ],
                min_tier=task.tier,
                reward=task.reward,
                penalty=task.penalty,
                deadline_offset=100.0,
                domain=task.domain,
                difficulty=task.difficulty,
            )

            # Accept contract
            accepted = self.economy.accept_contract(contract.contract_id, agent.agent_id)
            if not accepted:
                logger.debug(f"{model_name}: Could not accept {task.task_id} (tier/budget)")
                continue

            round_data["tasks_attempted"] += 1
            liability_agent_id = agent.agent_id
            execution_agent_id = agent.agent_id
            execution_model_name = model_name
            delegation_info = None

            # Demo delegation behavior: principal may "hire" another agent to execute.
            if self.config.demo_mode and random.random() <= self.config.delegation_rate:
                delegate_id = self._pick_delegate_candidate(
                    principal_id=agent.agent_id,
                    required_tier=task.tier,
                    adversarial=(strategy_name == "AdversarialStrategy"),
                )
                if delegate_id:
                    delegate_model = self.agent_model_map.get(delegate_id, delegate_id)
                    check = self.economy.can_delegate(agent.agent_id, delegate_id, task.tier)
                    self.economy.record_delegation(
                        contract.contract_id,
                        principal_id=agent.agent_id,
                        delegate_id=delegate_id,
                        required_tier=task.tier,
                        allowed=check["allowed"],
                        reason=check["reason"],
                    )
                    delegation_info = {
                        "principal_agent_id": agent.agent_id,
                        "principal_model": model_name,
                        "delegate_agent_id": delegate_id,
                        "delegate_model": delegate_model,
                        **check,
                    }
                    if check["allowed"]:
                        execution_agent_id = delegate_id
                        execution_model_name = delegate_model
                        liability_agent_id = agent.agent_id  # principal remains liable
                        self._emit_protocol_event(
                            "DELEGATION_ALLOWED",
                            model_name,
                            f"{model_name} hired {delegate_model} for {task.task_id}; principal retains liability.",
                            contract_id=contract.contract_id,
                            delegate=delegate_model,
                            required_tier=task.tier.name,
                            chain_tier=check["chain_tier"],
                        )
                    else:
                        self._emit_protocol_event(
                            "CIRCUMVENTION_BLOCKED",
                            model_name,
                            f"{model_name} attempted delegation/laundering via {delegate_model}; blocked ({check['reason']}).",
                            contract_id=contract.contract_id,
                            delegate=delegate_model,
                            required_tier=task.tier.name,
                            principal_tier=check.get("principal_tier"),
                            delegate_tier=check.get("delegate_tier"),
                            chain_tier=check.get("chain_tier"),
                        )

            # Execute task — delegate to AutonomousAgent (self-verify + retry)
            logger.info(
                f"  {model_name} executing {task.task_id} (T{task.tier.value})"
                f"{' via ' + execution_model_name if execution_model_name != model_name else ''}..."
            )
            execution_autonomous = self.autonomous_agents.get(execution_model_name)
            if execution_autonomous is not None:
                try:
                    exec_result = execution_autonomous.execute_task(task)
                    output = exec_result.output
                    token_cost = exec_result.token_cost_sol
                    latency = exec_result.latency_ms
                    tokens_in = exec_result.token_usage.get("input", 0)
                    tokens_out = exec_result.token_usage.get("output", 0)
                    if exec_result.self_check_failures:
                        logger.debug(
                            f"    Self-check caught {exec_result.self_check_failures}; "
                            f"retries={exec_result.retries_used}"
                        )
                except Exception as e:
                    logger.error(f"  {execution_model_name} AutonomousAgent.execute_task FAILED: {e}")
                    output = ""
                    token_cost = 0.0
                    latency = 0.0
                    tokens_in = tokens_out = 0
            else:
                llm_agent = self.llm_agents[execution_model_name]
                tok_in_before = llm_agent.total_input_tokens
                tok_out_before = llm_agent.total_output_tokens
                start_time = time.time()
                try:
                    output = llm_agent.execute_task(task.prompt, task.system_prompt)
                    latency = (time.time() - start_time) * 1000
                except Exception as e:
                    logger.error(f"  {execution_model_name} FAILED to execute: {e}")
                    output = ""
                    latency = (time.time() - start_time) * 1000
                tokens_in  = llm_agent.total_input_tokens  - tok_in_before
                tokens_out = llm_agent.total_output_tokens - tok_out_before
                token_cost = compute_token_cost_sol(execution_model_name, tokens_in, tokens_out)

            # Cost accounting: deduct token costs from agent balance
            agent.balance    -= token_cost
            agent.total_spent += token_cost
            self._token_costs[agent.agent_id] = (
                self._token_costs.get(agent.agent_id, 0.0) + token_cost
            )
            round_data["total_token_cost"] += token_cost

            # Verify output
            verification = self.verifier.verify(
                task=task,
                output=output,
                agent_model=execution_model_name,
                latency_ms=latency,
            )

            # Real-time robustness update based on constraint outcomes
            new_robustness = None
            if agent.current_robustness is not None:
                new_robustness = update_robustness_from_verification(
                    agent.current_robustness, task, verification,
                )
                candidate_tier = self.economy.gate.evaluate(new_robustness)
                if candidate_tier > tier:
                    upgrade = self.economy.request_tier_upgrade(
                        agent.agent_id,
                        requested_tier=candidate_tier,
                        audit_callback=lambda _aid, _tier, r=new_robustness: r,
                    )
                    if upgrade.get("granted"):
                        self._emit_protocol_event(
                            "UPGRADE",
                            model_name,
                            f"{model_name} upgraded to {candidate_tier.name} via scaling-gate audit.",
                            requested_tier=candidate_tier.name,
                            path=upgrade.get("path"),
                        )
                    else:
                        # Persist robustness updates even when higher-tier request fails.
                        self.economy.registry.certify(
                            agent.agent_id,
                            new_robustness,
                            audit_type="task_update",
                            timestamp=self.economy.current_time,
                        )
                        self._emit_protocol_event(
                            "UPGRADE_DENIED",
                            model_name,
                            f"{model_name} tier request to {candidate_tier.name} denied ({upgrade.get('reason')}).",
                            requested_tier=candidate_tier.name,
                            reason=upgrade.get("reason"),
                            gaps=upgrade.get("gaps"),
                        )
                else:
                    self.economy.registry.certify(
                        agent.agent_id,
                        new_robustness,
                        audit_type="task_update",
                        timestamp=self.economy.current_time,
                    )

            # Let AutonomousAgent update its internal perception + accounting
            if autonomous is not None:
                autonomous.update_state(task, verification, token_cost)

            # Settle contract based on verification
            settlement = self.economy.complete_contract(
                contract.contract_id,
                output,
                verification_override=verification.overall_pass,
                liability_agent_id=liability_agent_id,
            )

            # On-chain: create + accept + complete/fail contract
            if self.chain:
                try:
                    reward_lam = max(1, int(settlement.get("reward", 0) * 1e9))
                    penalty_lam = max(1, int(settlement.get("penalty", 0) * 1e9))
                    sig, onchain_id = self.chain.create_contract(
                        min_tier=task.tier.value,
                        reward_lamports=reward_lam,
                        penalty_lamports=penalty_lam,
                        domain=task.domain,
                    )
                    if sig:
                        self.chain.accept_contract(onchain_id, execution_model_name)
                        if verification.overall_pass:
                            self.chain.complete_contract(onchain_id, execution_model_name)
                        else:
                            self.chain.fail_contract(onchain_id, execution_model_name)
                except Exception as e:
                    logger.warning("[on-chain] contract settlement failed: %s", e)

            # Log result
            cid = f"solana_audit_{hashlib.sha256(str(task.task_id).encode()).hexdigest()[:32]}"
            task_result = {
                "agent": model_name,
                "agent_id": agent.agent_id,
                "executed_by_agent_id": execution_agent_id,
                "executed_by_model": execution_model_name,
                "task_id": task.task_id,
                "task_prompt": task.prompt,
                "tier": task.tier.name,
                "domain": task.domain,
                "proof_cid": cid,
                "verification": verification.to_dict(),
                "settlement": settlement,
                "latency_ms": latency,
                "token_cost_sol": token_cost,
                "tokens_used": {"input": tokens_in, "output": tokens_out},
                "output_preview": output[:500] if output else "(empty)",
            }
            if autonomous is not None:
                task_result["agent_strategy"] = type(autonomous.strategy).__name__
            if delegation_info is not None:
                task_result["delegation"] = delegation_info
            round_data["task_results"].append(task_result)
            self._results.append(task_result)

            if verification.overall_pass:
                round_data["tasks_passed"] += 1
                round_data["total_reward"] += task.reward
                status_str = "PASS"
            else:
                round_data["tasks_failed"] += 1
                round_data["total_penalty"] += task.penalty
                status_str = "FAIL"

            jury_str = f"{verification.jury_score:.2f}" if verification.jury_score is not None else "N/A"
            logger.info(
                f"  {model_name}: {task.task_id} -> {status_str} "
                f"(algo={'PASS' if verification.algorithmic_pass else 'FAIL'}, "
                f"jury={jury_str}, cost={token_cost:.4f} SOL) "
                f"[{latency:.0f}ms]"
            )
            if verification.constraints_failed:
                logger.info(f"    Failed constraints: {verification.constraints_failed}")

        return round_data

    def _finalize(self):
        """Compute final summary statistics."""
        agents_data = []
        for agent_id, model_name in self.agent_model_map.items():
            record = self.economy.registry.get_agent(agent_id)
            if not record:
                continue
            llm = self.llm_agents.get(model_name)
            usage = llm.usage_summary() if llm else {}
            aq = self._audit_quality.get(model_name, {
                "source": "unknown",
                "dims_real": [],
                "dims_defaulted": ["cc", "er", "as", "ih"],
            })
            autonomous = self.autonomous_agents.get(model_name)
            strategy_name = "unknown"
            if self.config.agent_strategies:
                strategy_name = self.config.agent_strategies.get(model_name, strategy_name)
            if strategy_name == "unknown" and autonomous is not None:
                class_name = type(autonomous.strategy).__name__
                strategy_name = class_name[:-8].lower() if class_name.endswith("Strategy") else class_name.lower()
            agents_data.append({
                "model_name": model_name,
                "agent_id": agent_id,
                "tier": record.current_tier.value,
                "tier_name": record.current_tier.name,
                "balance": record.balance,
                "total_earned": record.total_earned,
                "total_penalties": record.total_penalties,
                "total_spent": record.total_spent,
                "token_cost_sol": self._token_costs.get(agent_id, 0.0),
                "net_profit": record.total_earned - record.total_penalties - record.total_spent,
                "contracts_completed": record.contracts_completed,
                "contracts_failed": record.contracts_failed,
                "success_rate": (
                    record.contracts_completed / max(1, record.contracts_completed + record.contracts_failed)
                ),
                "robustness": {
                    "cc": record.current_robustness.cc,
                    "er": record.current_robustness.er,
                    "as": record.current_robustness.as_,
                    "ih": record.current_robustness.ih,
                } if record.current_robustness else None,
                # Audit data provenance — critical for paper claims
                "audit_data_source": aq["source"],
                "audit_dims_real": aq["dims_real"],
                "audit_dims_defaulted": aq["dims_defaulted"],
                "llm_usage": usage,
                "strategy": strategy_name,
                # v2 AutonomousAgent metrics
                "autonomous_metrics": autonomous.metrics_summary() if autonomous else None,
            })

        # Gini coefficient of balances
        balances = sorted([a["balance"] for a in agents_data])
        gini = self._compute_gini(balances)

        # Tier distribution
        tier_dist = self.economy.registry.tier_distribution()

        # Per-round trajectory
        safety_trajectory = []
        for snap in self.economy.snapshots:
            safety_trajectory.append({
                "time": snap.timestamp,
                "safety": snap.aggregate_safety,
                "active_agents": snap.num_agents,
                "total_balance": snap.total_balance,
            })

        # Verification stats
        v_summary = self.verifier.summary() if self.verifier else {}

        # Total token costs
        total_token_cost = sum(self._token_costs.values())
        event_counts = {}
        for e in self._protocol_events:
            t = e.get("type", "UNKNOWN")
            event_counts[t] = event_counts.get(t, 0) + 1
        delegation_attempts = sum(1 for r in self._results if r.get("delegation") is not None)
        delegation_allowed = sum(
            1 for r in self._results
            if (r.get("delegation") or {}).get("allowed") is True
        )
        circumvention_blocked = event_counts.get("CIRCUMVENTION_BLOCKED", 0)

        # Data quality audit — list agents with unverified robustness dimensions
        unaudited_agents = [
            {
                "model_name": a["model_name"],
                "audit_source": a["audit_data_source"],
                "dims_defaulted": a["audit_dims_defaulted"],
                "tier_name": a["tier_name"],
            }
            for a in agents_data
            if a["audit_dims_defaulted"]
        ]

        self._final_summary = {
            "economy": {
                "aggregate_safety": self.economy.aggregate_safety(),
                "total_rewards_paid": sum(r["total_reward"] for r in self._round_summaries),
                "total_penalties_collected": sum(r["total_penalty"] for r in self._round_summaries),
                "total_token_cost_sol": total_token_cost,
                "usd_to_sol_rate": USD_TO_SOL,
                "gini_coefficient": gini,
                "num_rounds": self.config.num_rounds,
                "num_agents": len(agents_data),
                "active_agents": len(self.economy.registry.active_agents),
                "test_sol_topups_total": self._test_sol_topups_total,
            },
            "demo_highlights": {
                "protocol_event_counts": event_counts,
                "delegation_attempts": delegation_attempts,
                "delegation_allowed": delegation_allowed,
                "delegation_blocked": max(0, delegation_attempts - delegation_allowed),
                "circumvention_blocked": circumvention_blocked,
            },
            "tier_distribution": {t.name: c for t, c in tier_dist.items()},
            "verification": v_summary,
            "agents": sorted(agents_data, key=lambda a: a["balance"], reverse=True),
            "safety_trajectory": safety_trajectory,
            # ---------------------------------------------------------------
            # Paper note: agents listed here have one or more robustness
            # dimensions drawn from DEFAULT_ROBUSTNESS rather than verified
            # framework results.  Their tier assignments are estimates, not
            # certified values.  They should be reported separately from
            # fully-audited agents in any empirical claim about CGAE gating.
            # ---------------------------------------------------------------
            "data_quality_warnings": {
                "num_partially_or_fully_defaulted": len(unaudited_agents),
                "unaudited_agents": unaudited_agents,
            },
        }

    @staticmethod
    def _compute_gini(values: list[float]) -> float:
        """Compute Gini coefficient for a sorted list of values."""
        n = len(values)
        if n == 0:
            return 0.0
        total = sum(values)
        if total == 0:
            return 0.0
        cumulative = 0.0
        weighted_sum = 0.0
        for i, v in enumerate(values):
            cumulative += v
            weighted_sum += (2 * (i + 1) - n - 1) * v
        return weighted_sum / (n * total)

    def save_results(self, path: Optional[str] = None):
        """Save all results to disk."""
        output_dir = Path(path or self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Economy state
        self.economy.export_state(str(output_dir / "economy_state.json"))

        # Full task results
        (output_dir / "task_results.json").write_text(
            json.dumps(self._results, indent=2, default=str)
        )

        # Round summaries
        (output_dir / "round_summaries.json").write_text(
            json.dumps(self._round_summaries, indent=2, default=str)
        )

        # Protocol events for high-signal dashboard alerts
        (output_dir / "protocol_events.json").write_text(
            json.dumps(self._protocol_events, indent=2, default=str)
        )

        # Final summary
        if self._final_summary:
            (output_dir / "final_summary.json").write_text(
                json.dumps(self._final_summary, indent=2, default=str)
            )

        # Verification summary
        if self.verifier:
            (output_dir / "verification_summary.json").write_text(
                json.dumps(self.verifier.summary(), indent=2)
            )

        # Per-agent details
        agent_details = {}
        for agent_id, model_name in self.agent_model_map.items():
            record = self.economy.registry.get_agent(agent_id)
            if record:
                llm = self.llm_agents.get(model_name)
                agent_details[model_name] = {
                    **record.to_dict(),
                    "llm_usage": llm.usage_summary() if llm else {},
                    "token_cost_sol": self._token_costs.get(agent_id, 0.0),
                }
        (output_dir / "agent_details.json").write_text(
            json.dumps(agent_details, indent=2, default=str)
        )

        # Verification log
        if self.verifier:
            log_data = [v.to_dict() for v in self.verifier.verification_log]
            (output_dir / "verification_log.json").write_text(
                json.dumps(log_data, indent=2, default=str)
            )

        logger.info(f"Results saved to {output_dir}")


def main():
    """Entry point for running the live simulation."""
    parser = argparse.ArgumentParser(description="Run the CGAE live economy simulation.")
    parser.add_argument("--live", action="store_true", help="Run in infinite loop mode for dashboard.")
    parser.add_argument("--rounds", type=int, default=10, help="Number of rounds (ignored if --live is set).")
    parser.add_argument("--video-demo", action="store_true", help="Run curated 5-min video demo (3 agents, adversarial blocking).")
    parser.add_argument(
        "--show-failures",
        action="store_true",
        help="Bias live execution toward harder tasks and disable self-check retries.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Check env vars
    required_vars = ["AZURE_API_KEY"]
    optional_vars = ["AZURE_OPENAI_API_ENDPOINT", "DDFT_MODELS_ENDPOINT"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        print(f"ERROR: Missing required environment variables: {missing}")
        print(f"Optional (for more models): {optional_vars}")
        print("\nSet them with:")
        print("  export AZURE_API_KEY=your-key")
        print("  export AZURE_OPENAI_API_ENDPOINT=https://your-endpoint.openai.azure.com/")
        print("  export DDFT_MODELS_ENDPOINT=https://your-foundry-endpoint/v1")
        return

    available = [v for v in optional_vars if os.environ.get(v)]
    print(f"Endpoints available: {available}")

    # Framework API URLs are read from CDCT_API_URL / DDFT_API_URL / EECT_API_URL
    # env vars by the clients.  Override here if needed.
    config = LiveSimConfig(
        num_rounds=-1 if args.live else args.rounds,
        seed=42,
        video_demo=args.video_demo,
        failure_visibility_mode=args.show_failures,
    )

    runner = LiveSimulationRunner(config)
    results = runner.run()
    runner.save_results()

    # Print summary
    print("\n" + "=" * 60)
    print("CGAE LIVE ECONOMY - RESULTS")
    print("=" * 60)

    if runner._final_summary:
        econ = runner._final_summary["economy"]
        print(f"\nRounds: {econ['num_rounds']}")
        print(f"Agents: {econ['num_agents']} ({econ['active_agents']} active)")
        print(f"Aggregate safety: {econ['aggregate_safety']:.4f}")
        print(f"Gini coefficient: {econ['gini_coefficient']:.4f}")
        print(f"Total rewards: {econ['total_rewards_paid']:.4f} SOL")
        print(f"Total penalties: {econ['total_penalties_collected']:.4f} SOL")
        print(f"Total token costs: {econ['total_token_cost_sol']:.4f} SOL")
        highlights = runner._final_summary.get("demo_highlights", {})
        if highlights:
            print("\nDemo highlights:")
            print(f"  Circumvention blocked: {highlights.get('circumvention_blocked', 0)}")
            print(
                f"  Delegation attempts: {highlights.get('delegation_attempts', 0)} "
                f"(allowed={highlights.get('delegation_allowed', 0)}, "
                f"blocked={highlights.get('delegation_blocked', 0)})"
            )

    if runner.verifier:
        vs = runner.verifier.summary()
        print(f"\nVerification: {vs.get('total', 0)} tasks")
        print(f"  Algorithmic pass rate: {vs.get('algorithmic_pass_rate', 0):.1%}")
        if vs.get("jury_pass_rate") is not None:
            print(f"  Jury pass rate: {vs['jury_pass_rate']:.1%}")
        print(f"  Overall pass rate: {vs.get('overall_pass_rate', 0):.1%}")
        if vs.get("avg_jury_score") is not None:
            print(f"  Avg jury score: {vs['avg_jury_score']:.3f}")

    print("\n--- Agent Leaderboard ---")
    print(f"  {'Model':40s}  {'Tier':3s}  {'Bal':>8}  {'Earned':>8}  "
          f"{'Pen':>7}  {'Cost':>7}  W/L    CC    ER    AS   AuditSrc")
    if runner._final_summary:
        for a in runner._final_summary["agents"]:
            r = a.get("robustness") or {}
            # Show a short audit source tag; highlight defaulted dimensions
            src = a.get("audit_data_source", "?")
            defaulted = a.get("audit_dims_defaulted", [])
            src_tag = src if not defaulted else f"{src}[def:{','.join(defaulted)}]"
            print(
                f"  {a['model_name']:40s} | {a['tier_name']:3s} | "
                f"bal={a['balance']:8.4f} | earned={a['total_earned']:8.4f} | "
                f"pen={a['total_penalties']:7.4f} | cost={a['token_cost_sol']:7.4f} | "
                f"W/L={a['contracts_completed']}/{a['contracts_failed']} | "
                f"CC={r.get('cc', 0):.2f} ER={r.get('er', 0):.2f} AS={r.get('as', 0):.2f} | "
                f"{src_tag}"
            )

        dqw = runner._final_summary.get("data_quality_warnings", {})
        if dqw.get("num_partially_or_fully_defaulted", 0) > 0:
            print(f"\n  *** DATA QUALITY NOTE ***")
            print(f"  {dqw['num_partially_or_fully_defaulted']} agent(s) used assumed (not verified) "
                  f"robustness for one or more dimensions.")
            print(f"  These agents' tier assignments are estimates. See 'data_quality_warnings' "
                  f"in final_summary.json for details.")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
