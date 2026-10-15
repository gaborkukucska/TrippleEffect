"""Round 1k tests: a worker in worker_decompose that replies with prose only gets a framework nudge (max 2 in a row).

Observed 2026-10-15 11:03: W1 answered in prose instead of calling request_state in worker_decompose; the scheduler
only re-activated it with an unchanged history, costing a full extra LLM cycle.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
from src.agents.constants import AGENT_TYPE_WORKER, WORKER_STATE_DECOMPOSE, WORKER_STATE_WORK


def _make(history=None):
    manager = MagicMock()
    manager.schedule_cycle = AsyncMock(return_value=MagicMock())
    manager.send_to_ui = AsyncMock()
    manager.current_session_db_id = None
    agent = SimpleNamespace(
        agent_id="W1", agent_type=AGENT_TYPE_WORKER, state=WORKER_STATE_DECOMPOSE, status="processing",
        message_history=list(history or []), set_status=lambda s, **k: None,
    )
    return NextStepScheduler(manager), manager, agent


def _ctx(agent, tool_ok=False, action=False, state_change=False):
    return SimpleNamespace(
        agent=agent, all_tool_results=[], trigger_failover=False, needs_reactivation_after_cycle=False,
        is_retryable_error_type=False, retry_count=0, max_retries_for_cycle=3, cycle_completed_successfully=True,
        executed_tool_successfully_this_cycle=tool_ok, action_taken_this_cycle=action,
        state_change_requested_this_cycle=state_change, current_model_key_for_tracking="m",
        last_error_content="", last_error_obj=None, plan_submitted_this_cycle=False, thought_produced_this_cycle=False,
    )


def _nudges(agent):
    return [m for m in agent.message_history if m.get("role") == "system" and "worker_decompose" in m.get("content", "")]


def test_prose_only_cycle_adds_nudge_and_reactivates():
    sched, manager, agent = _make([{"role": "assistant", "content": "I will plan the tasks."}])
    asyncio.run(sched.schedule_next_step(_ctx(agent)))
    n = _nudges(agent)
    assert len(n) == 1
    assert "request_state" in n[0]["content"] and WORKER_STATE_WORK in n[0]["content"] and "add_task" in n[0]["content"]
    manager.schedule_cycle.assert_awaited_once()


def test_nudges_are_capped_at_two_in_a_row_but_still_reactivates():
    sched, manager, agent = _make()
    for _ in range(5):
        asyncio.run(sched.schedule_next_step(_ctx(agent)))
    assert len(_nudges(agent)) == 2
    assert manager.schedule_cycle.await_count == 5


def test_tool_call_resets_counter_and_adds_no_nudge():
    sched, manager, agent = _make()
    asyncio.run(sched.schedule_next_step(_ctx(agent)))
    asyncio.run(sched.schedule_next_step(_ctx(agent, tool_ok=True, action=True)))
    assert len(_nudges(agent)) == 1
    assert agent._decompose_prose_nudges == 0
    asyncio.run(sched.schedule_next_step(_ctx(agent)))
    assert len(_nudges(agent)) == 2


def test_failed_tool_call_is_not_treated_as_prose():
    sched, manager, agent = _make()
    asyncio.run(sched.schedule_next_step(_ctx(agent, tool_ok=False, action=True)))
    assert _nudges(agent) == []
    manager.schedule_cycle.assert_awaited_once()


def test_state_change_cycle_untouched():
    sched, manager, agent = _make()
    asyncio.run(sched.schedule_next_step(_ctx(agent, state_change=True)))
    assert _nudges(agent) == []
