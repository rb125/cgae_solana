"""
Temporal Dynamics (Section 3.3 of cgae.tex)

Implements:
- Temporal decay: delta(dt) = e^(-lambda * dt) (Eq. 8)
- Effective robustness: R_eff(A,t) = delta(t - t_cert) * R_hat(A) (Eq. 9)
- Stochastic re-auditing: p_audit(A,t) = 1 - e^(-mu_k * dt) (Eq. 10)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from cgae_engine.gate import RobustnessVector, Tier


@dataclass
class TemporalDecay:
    """
    Temporal decay function (Definition 7).

    delta(dt) = e^(-lambda * dt)

    Reduces effective robustness over time since last certification.
    lambda controls how fast certifications expire.
    """
    decay_rate: float = 0.01  # lambda: higher = faster decay

    def delta(self, dt: float) -> float:
        """Compute decay factor for time elapsed since certification."""
        if dt < 0:
            raise ValueError(f"Time delta must be non-negative, got {dt}")
        return math.exp(-self.decay_rate * dt)

    def effective_robustness(
        self,
        certified_robustness: RobustnessVector,
        time_since_cert: float,
    ) -> RobustnessVector:
        """
        Compute R_eff(A,t) = delta(t - t_cert) * R_hat(A) (Eq. 9).
        All robustness components decay uniformly.
        """
        d = self.delta(time_since_cert)
        return RobustnessVector(
            cc=certified_robustness.cc * d,
            er=certified_robustness.er * d,
            as_=certified_robustness.as_ * d,
            ih=certified_robustness.ih * d,
        )

    def time_to_tier_drop(
        self,
        current_score: float,
        threshold: float,
    ) -> Optional[float]:
        """
        Calculate time until a score decays below a threshold.
        Solves: threshold = current_score * e^(-lambda * t) for t.
        Returns None if current_score is already below threshold.
        """
        if current_score <= threshold:
            return 0.0
        if threshold <= 0:
            return None  # Never reaches 0 with exponential decay
        return -math.log(threshold / current_score) / self.decay_rate


@dataclass
class AuditEvent:
    """Record of a spot-audit event."""
    agent_id: str
    timestamp: float
    passed: bool
    old_tier: Tier
    new_tier: Tier
    robustness_before: Optional[RobustnessVector] = None
    robustness_after: Optional[RobustnessVector] = None


@dataclass
class StochasticAuditor:
    """
    Stochastic Re-Auditing (Definition 8 in paper).

    p_audit(A,t) = 1 - e^(-mu_k * (t - t_last_audit))

    Higher-tier agents face more frequent spot audits (mu_k increasing in k).
    Failing a spot-audit triggers immediate tier demotion.
    """
    # Tier-dependent audit intensity parameters (mu_k)
    audit_intensities: dict[Tier, float] = field(default_factory=lambda: {
        Tier.T0: 0.0,     # No audits for T0
        Tier.T1: 0.005,   # ~1 audit per 200 time steps
        Tier.T2: 0.010,   # ~1 audit per 100 time steps
        Tier.T3: 0.020,   # ~1 audit per 50 time steps
        Tier.T4: 0.040,   # ~1 audit per 25 time steps
        Tier.T5: 0.080,   # ~1 audit per 12.5 time steps
    })

    audit_log: list[AuditEvent] = field(default_factory=list)

    def audit_probability(self, tier: Tier, time_since_last_audit: float) -> float:
        """
        Compute spot-audit probability (Eq. 10).
        p_audit(A,t) = 1 - e^(-mu_k * dt)
        """
        mu = self.audit_intensities.get(tier, 0.0)
        if mu <= 0 or time_since_last_audit <= 0:
            return 0.0
        return 1.0 - math.exp(-mu * time_since_last_audit)

    def should_audit(self, tier: Tier, time_since_last_audit: float) -> bool:
        """Stochastically determine whether to trigger a spot audit."""
        prob = self.audit_probability(tier, time_since_last_audit)
        return random.random() < prob

    def expected_audits_per_period(self, tier: Tier, period: float) -> float:
        """Expected number of audits over a time period (for planning)."""
        mu = self.audit_intensities.get(tier, 0.0)
        return mu * period
