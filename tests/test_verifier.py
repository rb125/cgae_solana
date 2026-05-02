"""Tests for the two-layer verification system."""

import json
import pytest

from cgae_engine.gate import Tier
from cgae_engine.tasks import ALL_TASKS, Task, verify_output
from cgae_engine.verifier import (
    TaskVerifier,
    VerificationResult,
    _build_jury_prompt,
    _parse_jury_response,
)


class TestParseJuryResponse:
    def test_valid_json(self):
        resp = '{"score": 0.85, "pass": true, "reasoning": "Well done"}'
        result = _parse_jury_response(resp)
        assert result["score"] == 0.85
        assert result["pass"] is True
        assert result["reasoning"] == "Well done"

    def test_json_in_code_block(self):
        resp = '```json\n{"score": 0.7, "pass": true, "reasoning": "Good"}\n```'
        result = _parse_jury_response(resp)
        assert result["score"] == 0.7
        assert result["pass"] is True

    def test_score_clamped(self):
        resp = '{"score": 1.5, "pass": true, "reasoning": "Over"}'
        result = _parse_jury_response(resp)
        assert result["score"] == 1.0  # Clamped to max

    def test_fallback_regex(self):
        resp = 'Some text... "score": 0.6 ... blah'
        result = _parse_jury_response(resp)
        assert result["score"] == 0.6
        assert result["pass"] is True

    def test_unparseable(self):
        resp = "completely unparseable garbage"
        result = _parse_jury_response(resp)
        assert result["score"] == 0.0
        assert result["pass"] is False

    def test_auto_pass_threshold(self):
        resp = '{"score": 0.3, "reasoning": "Poor"}'
        result = _parse_jury_response(resp)
        assert result["pass"] is False  # 0.3 < 0.6 threshold


class TestBuildJuryPrompt:
    def test_includes_task_prompt(self):
        task = ALL_TASKS["t1_summarize_climate"]
        prompt = _build_jury_prompt(task, "Some output")
        assert "## Task Prompt" in prompt
        assert "Arctic sea ice" in prompt

    def test_includes_output(self):
        task = ALL_TASKS["t1_summarize_climate"]
        prompt = _build_jury_prompt(task, "My summary output")
        assert "## Agent Output" in prompt
        assert "My summary output" in prompt

    def test_includes_rubric(self):
        task = ALL_TASKS["t1_summarize_climate"]
        prompt = _build_jury_prompt(task, "output")
        assert "## Scoring Rubric" in prompt

    def test_includes_ground_truth_when_available(self):
        task = ALL_TASKS["t1_extract_entities"]
        prompt = _build_jury_prompt(task, "output")
        assert "## Reference Answer" in prompt
        assert "Sarah Chen" in prompt


class TestTaskVerifierAlgorithmic:
    """Test TaskVerifier without jury agents (algorithmic-only mode)."""

    def setup_method(self):
        self.verifier = TaskVerifier(jury_agents=[])

    def test_t1_pass(self):
        task = ALL_TASKS["t1_classify_sentiment"]
        output = json.dumps({
            "reviews": [
                {"text": "Great", "sentiment": "positive"},
                {"text": "Bad", "sentiment": "negative"},
                {"text": "Ok", "sentiment": "neutral"},
                {"text": "Amazing", "sentiment": "positive"},
            ]
        })
        result = self.verifier.verify(task, output, "test-model")
        assert result.algorithmic_pass is True
        assert result.overall_pass is True  # T1 = algorithmic only
        assert result.jury_pass is None  # No jury for T1

    def test_t1_fail(self):
        task = ALL_TASKS["t1_classify_sentiment"]
        result = self.verifier.verify(task, "not json", "test-model")
        assert result.algorithmic_pass is False
        assert result.overall_pass is False

    def test_t2_no_jury_algorithmic_only(self):
        """T2 task without jury agents should still use algorithmic checks."""
        task = ALL_TASKS["t2_legal_extract"]
        output = json.dumps({
            "royalty_rate": "4.5%",
            "payment_frequency": "quarterly",
            "minimum_annual": "$50,000",
            "initial_term": "5 years",
            "renewal_term": "2 years",
        })
        result = self.verifier.verify(task, output, "test-model")
        assert result.algorithmic_pass is True
        # Without jury, T2 still passes on algorithmic alone
        assert result.jury_pass is None

    def test_verification_log(self):
        task = ALL_TASKS["t1_classify_sentiment"]
        self.verifier.verify(task, "not json", "model-a")
        self.verifier.verify(task, "not json", "model-b")
        assert len(self.verifier.verification_log) == 2

    def test_summary(self):
        task = ALL_TASKS["t1_classify_sentiment"]
        output = json.dumps({
            "reviews": [
                {"text": "x", "sentiment": "positive"},
                {"text": "y", "sentiment": "negative"},
                {"text": "z", "sentiment": "neutral"},
            ]
        })
        self.verifier.verify(task, output, "model-a")
        self.verifier.verify(task, "bad", "model-b")
        summary = self.verifier.summary()
        assert summary["total"] == 2
        assert summary["algorithmic_pass_rate"] == 0.5

    def test_result_to_dict(self):
        task = ALL_TASKS["t1_summarize_climate"]
        result = self.verifier.verify(task, "short", "test-model", latency_ms=42.0)
        d = result.to_dict()
        assert d["task_id"] == "t1_summarize_climate"
        assert d["agent_model"] == "test-model"
        assert d["latency_ms"] == 42.0
        assert isinstance(d["constraints_passed"], list)
        assert isinstance(d["constraints_failed"], list)
