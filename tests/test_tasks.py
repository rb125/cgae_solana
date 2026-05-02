"""Tests for the task bank and constraint verification system."""

import json
import pytest

from cgae_engine.gate import Tier
from cgae_engine.tasks import (
    ALL_TASKS,
    TASKS_BY_TIER,
    TIER_1_TASKS,
    TIER_2_TASKS,
    TIER_3_TASKS,
    TIER_4_TASKS,
    TIER_5_TASKS,
    Task,
    get_tasks_for_tier,
    verify_output,
    word_count_between,
    must_be_valid_json,
    json_has_fields,
    must_contain_keywords,
    must_not_contain,
    must_contain_number_in_range,
    must_refuse_or_caveat,
    min_sentence_count,
    must_have_sections,
)


# ---------------------------------------------------------------------------
# Constraint builder unit tests
# ---------------------------------------------------------------------------

class TestWordCountBetween:
    def test_within_range(self):
        c = word_count_between(5, 10)
        assert c.check("one two three four five six seven") is True

    def test_too_few(self):
        c = word_count_between(5, 10)
        assert c.check("one two three") is False

    def test_too_many(self):
        c = word_count_between(2, 4)
        assert c.check("one two three four five six") is False

    def test_dimension(self):
        c = word_count_between(1, 100)
        assert c.dimension == "cc"


class TestMustBeValidJson:
    def test_valid_json(self):
        c = must_be_valid_json()
        assert c.check('{"key": "value"}') is True

    def test_invalid_json(self):
        c = must_be_valid_json()
        assert c.check("not json at all") is False

    def test_json_in_code_block(self):
        c = must_be_valid_json()
        assert c.check('```json\n{"key": "value"}\n```') is True

    def test_json_array(self):
        c = must_be_valid_json()
        assert c.check('[1, 2, 3]') is True


class TestJsonHasFields:
    def test_has_all_fields(self):
        c = json_has_fields("name", "age")
        assert c.check('{"name": "Alice", "age": 30}') is True

    def test_missing_field(self):
        c = json_has_fields("name", "age")
        assert c.check('{"name": "Alice"}') is False

    def test_not_json(self):
        c = json_has_fields("name")
        assert c.check("plain text") is False

    def test_json_in_code_block(self):
        c = json_has_fields("x")
        assert c.check('```json\n{"x": 1}\n```') is True


class TestMustContainKeywords:
    def test_all_present(self):
        c = must_contain_keywords("ice", "temperature")
        assert c.check("The ice melts when temperature rises.") is True

    def test_case_insensitive(self):
        c = must_contain_keywords("ICE")
        assert c.check("ice cream") is True

    def test_missing_keyword(self):
        c = must_contain_keywords("ice", "volcano")
        assert c.check("The ice melts.") is False

    def test_dimension(self):
        c = must_contain_keywords("x")
        assert c.dimension == "er"


class TestMustNotContain:
    def test_clean(self):
        c = must_not_contain("guaranteed", "foolproof")
        assert c.check("This approach has trade-offs.") is True

    def test_forbidden(self):
        c = must_not_contain("guaranteed")
        assert c.check("This is guaranteed to work.") is False

    def test_dimension(self):
        c = must_not_contain("x")
        assert c.dimension == "as"


class TestMustContainNumberInRange:
    def test_in_range(self):
        c = must_contain_number_in_range("score", 1, 10)
        assert c.check("The risk score is 7 out of 10.") is True

    def test_out_of_range(self):
        c = must_contain_number_in_range("score", 1, 5)
        assert c.check("Score: 99") is False

    def test_float(self):
        c = must_contain_number_in_range("pct", 70, 90)
        assert c.check("Accuracy is 78.5%") is True


class TestMustRefuseOrCaveat:
    def test_has_caveat(self):
        c = must_refuse_or_caveat()
        assert c.check("However, there are limitations to this approach.") is True

    def test_no_caveat(self):
        c = must_refuse_or_caveat()
        assert c.check("The answer is 42.") is False

    def test_uncertainty(self):
        c = must_refuse_or_caveat()
        assert c.check("This depends on the context and situation.") is True


class TestMinSentenceCount:
    def test_enough(self):
        c = min_sentence_count(3)
        assert c.check("First. Second. Third.") is True

    def test_not_enough(self):
        c = min_sentence_count(3)
        assert c.check("Only one sentence.") is False


class TestMustHaveSections:
    def test_markdown_headings(self):
        c = must_have_sections("Summary", "Conclusion")
        text = "## Summary\nSome text.\n## Conclusion\nDone."
        assert c.check(text) is True

    def test_bold_headings(self):
        c = must_have_sections("Summary")
        assert c.check("**Summary**\ntext here") is True

    def test_colon_headings(self):
        c = must_have_sections("Summary")
        assert c.check("Summary: here is text") is True

    def test_missing_heading(self):
        c = must_have_sections("Summary", "Missing")
        assert c.check("## Summary\ntext") is False


# ---------------------------------------------------------------------------
# Task bank structure tests
# ---------------------------------------------------------------------------

class TestTaskBank:
    def test_all_tasks_have_unique_ids(self):
        ids = [t.task_id for t in TIER_1_TASKS + TIER_2_TASKS + TIER_3_TASKS + TIER_4_TASKS]
        assert len(ids) == len(set(ids)), f"Duplicate task IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_all_tasks_indexed(self):
        total_tasks = len(TIER_1_TASKS) + len(TIER_2_TASKS) + len(TIER_3_TASKS) + len(TIER_4_TASKS) + len(TIER_5_TASKS)
        assert len(ALL_TASKS) == total_tasks

    def test_task_bank_has_minimum_tasks(self):
        assert len(ALL_TASKS) >= 15, f"Expected >= 15 tasks, got {len(ALL_TASKS)}"

    def test_tier_1_tasks_are_tier_1(self):
        for task in TIER_1_TASKS:
            assert task.tier == Tier.T1

    def test_tier_2_tasks_are_tier_2(self):
        for task in TIER_2_TASKS:
            assert task.tier == Tier.T2

    def test_all_tasks_have_constraints(self):
        for task_id, task in ALL_TASKS.items():
            assert len(task.constraints) > 0, f"Task {task_id} has no constraints"

    def test_all_tasks_have_prompts(self):
        for task_id, task in ALL_TASKS.items():
            assert len(task.prompt) > 10, f"Task {task_id} has empty prompt"
            assert len(task.system_prompt) > 5, f"Task {task_id} has empty system_prompt"

    def test_all_tasks_have_positive_reward(self):
        for task_id, task in ALL_TASKS.items():
            assert task.reward > 0, f"Task {task_id} has non-positive reward"
            assert task.penalty > 0, f"Task {task_id} has non-positive penalty"

    def test_reward_scales_with_tier(self):
        """Higher tiers should have higher average rewards."""
        for tier in [Tier.T1, Tier.T2, Tier.T3]:
            lower_tasks = TASKS_BY_TIER.get(tier, [])
            upper_tasks = TASKS_BY_TIER.get(Tier(tier.value + 1), [])
            if lower_tasks and upper_tasks:
                avg_lower = sum(t.reward for t in lower_tasks) / len(lower_tasks)
                avg_upper = sum(t.reward for t in upper_tasks) / len(upper_tasks)
                assert avg_upper > avg_lower, (
                    f"T{tier.value + 1} avg reward ({avg_upper:.4f}) "
                    f"should > T{tier.value} ({avg_lower:.4f})"
                )

    def test_get_tasks_for_tier_includes_lower(self):
        t2_tasks = get_tasks_for_tier(Tier.T2)
        t1_ids = {t.task_id for t in TIER_1_TASKS}
        t2_ids = {t.task_id for t in TIER_2_TASKS}
        result_ids = {t.task_id for t in t2_tasks}
        assert t1_ids.issubset(result_ids), "T2 should include T1 tasks"
        assert t2_ids.issubset(result_ids), "T2 should include T2 tasks"

    def test_constraint_dimensions_valid(self):
        for task_id, task in ALL_TASKS.items():
            for c in task.constraints:
                assert c.dimension in ("cc", "er", "as"), (
                    f"Task {task_id} constraint {c.name} has invalid dimension: {c.dimension}"
                )


# ---------------------------------------------------------------------------
# verify_output integration tests
# ---------------------------------------------------------------------------

class TestVerifyOutput:
    def test_passing_output(self):
        """Test a synthetic output that passes T1 sentiment classification."""
        task = ALL_TASKS["t1_classify_sentiment"]
        output = json.dumps({
            "reviews": [
                {"text": "Great battery", "sentiment": "positive"},
                {"text": "Bad service", "sentiment": "negative"},
                {"text": "Fine", "sentiment": "neutral"},
                {"text": "Best camera", "sentiment": "positive"},
            ]
        })
        passed, passed_names, failed_names = verify_output(task, output)
        assert passed is True, f"Expected pass, failed: {failed_names}"

    def test_failing_output_bad_json(self):
        """Invalid JSON should fail the valid_json constraint."""
        task = ALL_TASKS["t1_extract_entities"]
        passed, _, failed = verify_output(task, "not json at all")
        assert passed is False
        assert "valid_json" in failed

    def test_partial_failure(self):
        """Output that passes some constraints but not others."""
        task = ALL_TASKS["t1_summarize_climate"]
        output = "Ice temperature."  # Too short, too few sentences
        passed, passed_names, failed_names = verify_output(task, output)
        assert passed is False
        # Should pass keywords but fail word count
        assert "required_keywords" in passed_names
        assert any("word_count" in f for f in failed_names)
