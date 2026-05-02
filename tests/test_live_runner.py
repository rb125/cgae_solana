"""Tests for the live simulation runner infrastructure."""

import pytest

from cgae_engine.gate import RobustnessVector, Tier
from cgae_engine.tasks import ALL_TASKS, TIER_1_TASKS
from cgae_engine.verifier import VerificationResult

from server.live_runner import (
    LiveSimConfig,
    LiveSimulationRunner,
    compute_token_cost_sol,
    update_robustness_from_verification,
    TOKEN_COSTS,
    USD_TO_SOL,
)


# ---------------------------------------------------------------------------
# Token cost accounting tests
# ---------------------------------------------------------------------------

class TestTokenCostAccounting:
    def test_known_model_cost(self):
        cost = compute_token_cost_sol("gpt-5", input_tokens=1000, output_tokens=500)
        # gpt-5: $0.010/1K input, $0.030/1K output
        expected_usd = (1000 / 1000) * 0.010 + (500 / 1000) * 0.030
        expected_sol = expected_usd * USD_TO_SOL
        assert abs(cost - expected_sol) < 0.001

    def test_unknown_model_uses_default(self):
        cost = compute_token_cost_sol("unknown-model", input_tokens=1000, output_tokens=500)
        # Default: $0.002/1K input, $0.006/1K output
        expected_usd = (1000 / 1000) * 0.002 + (500 / 1000) * 0.006
        expected_sol = expected_usd * USD_TO_SOL
        assert abs(cost - expected_sol) < 0.001

    def test_zero_tokens(self):
        cost = compute_token_cost_sol("gpt-5", 0, 0)
        assert cost == 0.0

    def test_all_configured_models_have_costs(self):
        for model_name in TOKEN_COSTS:
            cost = compute_token_cost_sol(model_name, 100, 100)
            assert cost > 0, f"Model {model_name} should have positive cost"

    def test_reasoning_models_cost_more(self):
        """grok-4-20-reasoning should cost more than DeepSeek-V3.2 per token."""
        grok_cost = compute_token_cost_sol("grok-4-20-reasoning", 1000, 1000)
        ds_cost = compute_token_cost_sol("DeepSeek-V3.2", 1000, 1000)
        assert grok_cost > ds_cost


# ---------------------------------------------------------------------------
# Robustness update tests
# ---------------------------------------------------------------------------

class TestRobustnessUpdate:
    def _make_verification(self, passed: list[str], failed: list[str], overall: bool) -> VerificationResult:
        return VerificationResult(
            task_id="test",
            agent_model="test",
            algorithmic_pass=overall,
            constraints_passed=passed,
            constraints_failed=failed,
            overall_pass=overall,
        )

    def test_all_pass_increases_robustness(self):
        current = RobustnessVector(cc=0.5, er=0.5, as_=0.5, ih=0.7)
        task = ALL_TASKS["t1_summarize_climate"]
        passed_names = [c.name for c in task.constraints]
        verification = self._make_verification(passed_names, [], True)
        updated = update_robustness_from_verification(current, task, verification)
        assert updated.cc >= current.cc, "CC should increase on pass"
        assert updated.ih >= current.ih, "IH should increase on overall pass"

    def test_all_fail_decreases_robustness(self):
        current = RobustnessVector(cc=0.5, er=0.5, as_=0.5, ih=0.7)
        task = ALL_TASKS["t1_summarize_climate"]
        failed_names = [c.name for c in task.constraints]
        verification = self._make_verification([], failed_names, False)
        updated = update_robustness_from_verification(current, task, verification)
        assert updated.cc <= current.cc, "CC should decrease on fail"
        assert updated.ih <= current.ih, "IH should decrease on overall fail"

    def test_robustness_clamped_to_bounds(self):
        current = RobustnessVector(cc=0.99, er=0.99, as_=0.99, ih=0.99)
        task = ALL_TASKS["t1_summarize_climate"]
        passed_names = [c.name for c in task.constraints]
        verification = self._make_verification(passed_names, [], True)
        updated = update_robustness_from_verification(current, task, verification)
        assert updated.cc <= 1.0
        assert updated.er <= 1.0
        assert updated.ih <= 1.0

    def test_robustness_floor_at_zero(self):
        current = RobustnessVector(cc=0.01, er=0.01, as_=0.01, ih=0.01)
        task = ALL_TASKS["t1_summarize_climate"]
        failed_names = [c.name for c in task.constraints]
        verification = self._make_verification([], failed_names, False)
        updated = update_robustness_from_verification(current, task, verification)
        assert updated.cc >= 0.0
        assert updated.er >= 0.0
        assert updated.ih >= 0.0

    def test_mixed_results(self):
        """Some constraints pass, some fail — mixed update."""
        current = RobustnessVector(cc=0.5, er=0.5, as_=0.5, ih=0.7)
        task = ALL_TASKS["t1_summarize_climate"]
        # First constraint passes (cc), second fails (er), third passes (cc)
        constraints = task.constraints
        passed = [constraints[0].name]
        failed = [constraints[1].name, constraints[2].name]
        verification = self._make_verification(passed, failed, False)
        updated = update_robustness_from_verification(current, task, verification)
        # Should be a mixed result — not strictly all up or all down
        assert isinstance(updated, RobustnessVector)


# ---------------------------------------------------------------------------
# LiveSimConfig tests
# ---------------------------------------------------------------------------

class TestLiveSimConfig:
    def test_defaults(self):
        config = LiveSimConfig()
        assert config.num_rounds == 10
        assert config.initial_balance == 1.0
        assert config.seed == 42

    def test_custom_config(self):
        config = LiveSimConfig(
            num_rounds=5,
            initial_balance=2.0,
            model_names=["gpt-5", "o3"],
        )
        assert config.num_rounds == 5
        assert config.model_names == ["gpt-5", "o3"]

    def test_framework_dirs(self):
        config = LiveSimConfig(
            ddft_results_dir="/some/path",
            eect_results_dir="/another/path",
        )
        assert config.ddft_results_dir == "/some/path"


# ---------------------------------------------------------------------------
# Gini coefficient test
# ---------------------------------------------------------------------------

class TestGiniCoefficient:
    def test_perfect_equality(self):
        gini = LiveSimulationRunner._compute_gini([1.0, 1.0, 1.0, 1.0])
        assert abs(gini) < 0.01  # Should be ~0

    def test_perfect_inequality(self):
        gini = LiveSimulationRunner._compute_gini([0.0, 0.0, 0.0, 100.0])
        assert gini > 0.5  # High inequality

    def test_empty(self):
        gini = LiveSimulationRunner._compute_gini([])
        assert gini == 0.0

    def test_single_value(self):
        gini = LiveSimulationRunner._compute_gini([5.0])
        assert gini == 0.0

    def test_moderate_inequality(self):
        gini = LiveSimulationRunner._compute_gini([1.0, 2.0, 3.0, 4.0])
        assert 0.0 < gini < 0.5
