"""
CGAE Engine - Comprehension-Gated Agent Economy

Core implementation of the CGAE protocol:
- Gate function (weakest-link, tier mapping)
- Temporal decay and stochastic re-auditing
- Agent registration and lifecycle
- Contract system with escrow
- Audit orchestration (CDCT, DDFT, EECT/AGT)
"""

from cgae_engine.gate import GateFunction, TierThresholds
from cgae_engine.temporal import TemporalDecay, StochasticAuditor
from cgae_engine.registry import AgentRegistry, AgentRecord
from cgae_engine.contracts import CGAEContract, ContractManager
from cgae_engine.economy import Economy

__all__ = [
    "GateFunction",
    "TierThresholds",
    "TemporalDecay",
    "StochasticAuditor",
    "AgentRegistry",
    "AgentRecord",
    "CGAEContract",
    "ContractManager",
    "Economy",
]
