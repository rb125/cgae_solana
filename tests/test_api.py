"""Tests for live dashboard state publishing."""

from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
from types import SimpleNamespace

import pytest

from cgae_engine.gate import RobustnessVector, Tier
from cgae_engine.registry import AgentStatus
from server import api


class FakeRecord:
    def __init__(self):
        self.agent_id = "agent_1"
        self.current_tier = Tier.T2
        self.balance = 1.23
        self.total_earned = 0.45
        self.total_penalties = 0.05
        self.contracts_completed = 3
        self.contracts_failed = 1
        self.status = AgentStatus.ACTIVE
        self.current_robustness = RobustnessVector(cc=0.7, er=0.65, as_=0.6, ih=0.8)


class FakeRegistry:
    def __init__(self, record: FakeRecord):
        self._record = record

    @property
    def active_agents(self):
        return [self._record]

    def get_agent(self, agent_id: str):
        if agent_id == self._record.agent_id:
            return self._record
        return None


class FakeEconomy:
    def __init__(self, record: FakeRecord):
        self.registry = FakeRegistry(record)

    def aggregate_safety(self) -> float:
        return 0.77


class GrowthStrategy:
    pass


@pytest.fixture(autouse=True)
def reset_api_globals():
    original_state = deepcopy(api._state)
    original_loop = api._broadcast_loop
    with api._state_lock:
        api._state.clear()
        api._state.update({
            "status": "idle",
            "round": 0,
            "total_rounds": 0,
            "economy": None,
            "agents": {},
            "trades": [],
            "events": [],
            "time_series": {"safety": [], "balance": [], "rewards": [], "penalties": []},
        })
    api._broadcast_loop = None
    yield
    with api._state_lock:
        api._state.clear()
        api._state.update(original_state)
    api._broadcast_loop = original_loop


def test_broadcast_sync_uses_registered_loop(monkeypatch):
    class FakeLoop:
        def is_running(self):
            return True

    loop = FakeLoop()
    api._broadcast_loop = loop
    seen = {}

    def fake_run_coroutine_threadsafe(coro, target_loop):
        seen["loop"] = target_loop
        coro.close()
        future = Future()
        future.set_result(None)
        return future

    monkeypatch.setattr(api.asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)

    api._broadcast_sync()

    assert seen["loop"] is loop


def test_publish_trade_update_updates_dashboard_state_immediately(monkeypatch):
    record = FakeRecord()
    runner = SimpleNamespace(
        agent_model_map={record.agent_id: "gpt-5.4"},
        autonomous_agents={"gpt-5.4": SimpleNamespace(strategy=GrowthStrategy())},
        economy=FakeEconomy(record),
    )
    task_result = {
        "agent": "gpt-5.4",
        "task_id": "t2_eval",
        "task_prompt": "Evaluate the result",
        "tier": "T2",
        "domain": "analysis",
        "verification": {
            "overall_pass": True,
            "constraints_passed": ["valid_json"],
            "constraints_failed": [],
        },
        "settlement": {"reward": 0.12, "penalty": 0.0},
        "token_cost_sol": 0.01,
        "latency_ms": 123.0,
        "output_preview": "ok",
    }
    broadcasts = []
    monkeypatch.setattr(api, "_broadcast_sync", lambda: broadcasts.append(True))

    api._publish_trade_update(runner, 0, task_result)

    with api._state_lock:
        assert api._state["round"] == 1
        assert api._state["economy"]["aggregate_safety"] == pytest.approx(0.77)
        assert api._state["economy"]["contracts_completed"] == 3
        assert api._state["agents"][record.agent_id]["strategy"] == "growth"
        assert api._state["trades"] == [{
            "round": 0,
            "agent": "gpt-5.4",
            "task_id": "t2_eval",
            "task_prompt": "Evaluate the result",
            "tier": "T2",
            "domain": "analysis",
            "passed": True,
            "reward": 0.12,
            "penalty": 0.0,
            "token_cost": 0.01,
            "latency_ms": 123.0,
            "output_preview": "ok",
            "constraints_passed": ["valid_json"],
            "constraints_failed": [],
        }]

    assert broadcasts == [True]
