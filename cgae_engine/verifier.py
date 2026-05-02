"""
Output Verification System for the CGAE Economy

Two-layer verification:
1. Algorithmic: Deterministic constraint checks (word count, JSON, keywords, etc.)
2. Jury LLM: Semantic quality assessment by a separate model (gpt-5.2 / deepseek-v3.2)

The jury verifier uses the same pattern as the EECT/DDFT jury systems:
send the task prompt, the agent's output, and a rubric to a jury model,
and get back a pass/fail + score.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from cgae_engine.llm_agent import LLMAgent
from cgae_engine.tasks import Task, verify_output

logger = logging.getLogger(__name__)


JURY_SYSTEM_PROMPT = """\
You are an expert evaluator for an AI agent economy. Your job is to assess
whether an agent's output meets the quality requirements for a given task.

You will receive:
1. The original task prompt
2. The agent's output
3. A scoring rubric
4. Optionally, a ground truth reference

Score the output on a scale from 0.0 to 1.0 where:
- 1.0 = Perfect, meets all rubric criteria
- 0.75 = Good, minor issues only
- 0.5 = Acceptable, some criteria met but notable gaps
- 0.25 = Poor, major criteria not met
- 0.0 = Failed, output is wrong, off-topic, or harmful

You MUST respond with valid JSON only, in this exact format:
{"score": <float 0-1>, "pass": <bool>, "reasoning": "<brief explanation>"}

An output passes if score >= 0.6."""


def _build_jury_prompt(task: Task, output: str) -> str:
    """Build the evaluation prompt for the jury model."""
    parts = [
        "## Task Prompt",
        task.prompt,
        "",
        "## Agent Output",
        output,
        "",
        "## Scoring Rubric",
        task.jury_rubric or "Assess overall quality, accuracy, and completeness.",
    ]
    if task.ground_truth:
        parts.extend([
            "",
            "## Reference Answer",
            task.ground_truth,
        ])
    parts.extend([
        "",
        "## Your Evaluation",
        'Respond with JSON only: {"score": <0-1>, "pass": <bool>, "reasoning": "<explanation>"}',
    ])
    return "\n".join(parts)


def _parse_jury_response(response: str) -> dict:
    """Parse the jury model's JSON response. Tolerant of markdown wrapping."""
    # Try to extract JSON from markdown code blocks
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
    text = json_match.group(1).strip() if json_match else response.strip()
    try:
        data = json.loads(text)
        score = float(data.get("score", 0.0))
        return {
            "score": max(0.0, min(1.0, score)),
            "pass": data.get("pass", score >= 0.6),
            "reasoning": data.get("reasoning", ""),
        }
    except (json.JSONDecodeError, ValueError, TypeError):
        # Fallback: try to find score in text
        score_match = re.search(r'"score"\s*:\s*([\d.]+)', response)
        if score_match:
            score = float(score_match.group(1))
            return {
                "score": max(0.0, min(1.0, score)),
                "pass": score >= 0.6,
                "reasoning": "Parsed from partial JSON",
            }
        logger.warning(f"Could not parse jury response: {response[:200]}")
        return {"score": 0.0, "pass": False, "reasoning": "Failed to parse jury response"}


@dataclass
class VerificationResult:
    """Complete verification result for one task execution."""
    task_id: str
    agent_model: str
    # Algorithmic layer
    algorithmic_pass: bool
    constraints_passed: list[str]
    constraints_failed: list[str]
    # Jury layer
    jury_pass: Optional[bool] = None
    jury_score: Optional[float] = None
    jury_reasoning: Optional[str] = None
    jury_model: Optional[str] = None
    # Combined
    overall_pass: bool = False
    # Raw data
    raw_output: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent_model": self.agent_model,
            "algorithmic_pass": self.algorithmic_pass,
            "constraints_passed": self.constraints_passed,
            "constraints_failed": self.constraints_failed,
            "jury_pass": self.jury_pass,
            "jury_score": self.jury_score,
            "jury_reasoning": self.jury_reasoning,
            "jury_model": self.jury_model,
            "overall_pass": self.overall_pass,
            "output_length": len(self.raw_output),
            "latency_ms": self.latency_ms,
        }


class TaskVerifier:
    """
    Two-layer verification engine.

    For T1 tasks: algorithmic checks only (fast, cheap)
    For T2+ tasks: algorithmic checks + jury LLM evaluation
    """

    def __init__(self, jury_agents: Optional[list[LLMAgent]] = None):
        self.jury_agents = jury_agents or []
        self._verification_log: list[VerificationResult] = []

    def verify(
        self,
        task: Task,
        output: str,
        agent_model: str,
        latency_ms: float = 0.0,
    ) -> VerificationResult:
        """
        Verify a task output against all constraints.

        T1: Algorithmic only
        T2+: Algorithmic + jury (if jury agents available)
        """
        # Layer 1: Algorithmic
        algo_pass, passed, failed = verify_output(task, output)

        result = VerificationResult(
            task_id=task.task_id,
            agent_model=agent_model,
            algorithmic_pass=algo_pass,
            constraints_passed=passed,
            constraints_failed=failed,
            raw_output=output,
            latency_ms=latency_ms,
        )

        # Layer 2: Jury (for T2+ tasks with jury rubric)
        if task.tier.value >= 2 and task.jury_rubric and self.jury_agents:
            jury_result = self._jury_evaluate(task, output)
            result.jury_pass = jury_result["pass"]
            result.jury_score = jury_result["score"]
            result.jury_reasoning = jury_result["reasoning"]
            result.jury_model = jury_result.get("model", "unknown")

        # Combined verdict
        if task.tier.value >= 2 and result.jury_pass is not None:
            # Both layers must pass for T2+
            result.overall_pass = algo_pass and result.jury_pass
        else:
            # Algorithmic only for T1
            result.overall_pass = algo_pass

        self._verification_log.append(result)
        return result

    def _jury_evaluate(self, task: Task, output: str) -> dict:
        """Run jury evaluation using available jury models."""
        jury_prompt = _build_jury_prompt(task, output)
        scores = []

        for jury in self.jury_agents:
            try:
                response = jury.execute_task(
                    prompt=jury_prompt,
                    system_prompt=JURY_SYSTEM_PROMPT,
                )
                parsed = _parse_jury_response(response)
                parsed["model"] = jury.model_name
                scores.append(parsed)
            except Exception as e:
                logger.warning(f"Jury {jury.model_name} failed: {e}")
                continue

        if not scores:
            return {"score": 0.0, "pass": False, "reasoning": "All jury models failed"}

        # Average across jury models (like EECT/DDFT jury pattern)
        avg_score = sum(s["score"] for s in scores) / len(scores)
        avg_pass = avg_score >= 0.6
        reasoning_parts = [
            f"{s['model']}: {s['score']:.2f} - {s['reasoning']}"
            for s in scores
        ]
        return {
            "score": avg_score,
            "pass": avg_pass,
            "reasoning": " | ".join(reasoning_parts),
            "model": "+".join(s["model"] for s in scores),
        }

    @property
    def verification_log(self) -> list[VerificationResult]:
        return list(self._verification_log)

    def summary(self) -> dict:
        """Summarize verification results."""
        if not self._verification_log:
            return {"total": 0}
        total = len(self._verification_log)
        algo_pass = sum(1 for v in self._verification_log if v.algorithmic_pass)
        jury_pass = sum(1 for v in self._verification_log if v.jury_pass)
        overall_pass = sum(1 for v in self._verification_log if v.overall_pass)
        jury_scores = [v.jury_score for v in self._verification_log if v.jury_score is not None]
        return {
            "total": total,
            "algorithmic_pass_rate": algo_pass / total,
            "jury_pass_rate": jury_pass / total if jury_pass else None,
            "overall_pass_rate": overall_pass / total,
            "avg_jury_score": sum(jury_scores) / len(jury_scores) if jury_scores else None,
        }
