# START OF FILE tests/unit/test_round2b.py
"""Round 2b tests (2026-10-18 17:39-19:46): a Constitutional Guardian concern about an Admin AI reply could not be resolved.

The Admin-conversation end-of-cycle path set the status back to 'idle' after the concern was raised, and 'Approve' was
refused: "Agent 'admin_ai' is not awaiting user decision on a CG concern. Status: idle, Flag: True".
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents.constants import (ADMIN_STATE_CONVERSATION, AGENT_STATUS_AWAITING_USER_REVIEW_CG, AGENT_STATUS_ERROR,
                                  AGENT_STATUS_IDLE, AGENT_TYPE_ADMIN)
from src.agents.cycle_components.next_step_scheduler import NextStepScheduler


def admin(status, flag):
    agent = SimpleNamespace(agent_id="admin_ai", agent_type=AGENT_TYPE_ADMIN, state=ADMIN_STATE_CONVERSATION, status=status,
                            cg_awaiting_user_decision=flag, message_history=[{"role": "system", "content": "s"}],
                            cg_original_text="All tests pass.", cg_concern_details="<CONCERN>x</CONCERN>",
                            cg_original_event_data={"type": "response_chunk", "agent_id": "admin_ai", "content": "All tests pass."})
    agent.set_status = lambda s, **k: setattr(agent, "status", s)
    return agent


def ctx(agent):
    return SimpleNamespace(
        agent=agent, all_tool_results=[], trigger_failover=False, needs_reactivation_after_cycle=False,
        is_retryable_error_type=False, retry_count=0, max_retries_for_cycle=3, cycle_completed_successfully=True,
        executed_tool_successfully_this_cycle=False, action_taken_this_cycle=True,
        state_change_requested_this_cycle=False, current_model_key_for_tracking="m",
        last_error_content="", last_error_obj=None, plan_submitted_this_cycle=False, thought_produced_this_cycle=False,
    )


def scheduler():
    manager = MagicMock()
    manager.schedule_cycle = AsyncMock()
    manager.send_to_ui = AsyncMock()
    manager.current_session_db_id = None
    return NextStepScheduler(manager), manager


def test_admin_turn_end_keeps_a_pending_cg_concern():
    sched, manager = scheduler()
    agent = admin(AGENT_STATUS_AWAITING_USER_REVIEW_CG, True)
    asyncio.run(sched.schedule_next_step(ctx(agent)))
    assert agent.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG
    manager.schedule_cycle.assert_not_awaited()


@pytest.mark.parametrize("status", ["processing", AGENT_STATUS_IDLE])
def test_admin_turn_end_still_goes_idle_otherwise(status):
    sched, _ = scheduler()
    agent = admin(status, False)
    asyncio.run(sched.schedule_next_step(ctx(agent)))
    assert agent.status == AGENT_STATUS_IDLE


def make_manager(agent):
    from src.agents.manager import AgentManager
    manager = object.__new__(AgentManager)
    manager.agents = {agent.agent_id: agent}
    manager.current_session_db_id = None
    manager.send_to_ui = AsyncMock()
    manager.schedule_cycle = AsyncMock()
    manager.workflow_manager = SimpleNamespace(process_agent_output_for_workflow=AsyncMock())
    return manager


def run_approve(manager, agent_id):
    async def go():
        result = await manager.resolve_cg_concern_approve(agent_id)
        await asyncio.sleep(0)   # let the scheduled workflow task run
        return result
    return asyncio.run(go())


def test_approve_works_when_the_status_was_overwritten():
    agent = admin(AGENT_STATUS_IDLE, True)               # the observed state: "Status: idle, Flag: True"
    manager = make_manager(agent)
    ok, _ = run_approve(manager, "admin_ai")
    assert ok and agent.status == AGENT_STATUS_IDLE and agent.cg_awaiting_user_decision is False
    manager.send_to_ui.assert_awaited_once()             # the approved reply is shown
    assert agent.message_history[-1] == {"role": "assistant", "content": "All tests pass."}


def test_approve_without_a_pending_concern_is_still_refused():
    agent = admin(AGENT_STATUS_AWAITING_USER_REVIEW_CG, False)
    ok, message = run_approve(make_manager(agent), "admin_ai")
    assert not ok and "not in the correct state" in message


def test_stop_and_retry_go_by_the_flag():
    agent = admin(AGENT_STATUS_IDLE, True)
    manager = make_manager(agent)
    ok, _ = asyncio.run(manager.resolve_cg_concern_stop("admin_ai"))
    assert ok and agent.status == AGENT_STATUS_ERROR and agent.cg_awaiting_user_decision is False

    agent = admin("processing", True)
    manager = make_manager(agent)
    ok, _ = asyncio.run(manager.resolve_cg_concern_retry("admin_ai", "say what you checked"))
    assert ok and agent.cg_awaiting_user_decision is False
    assert "say what you checked" in agent.message_history[-1]["content"]
    manager.schedule_cycle.assert_awaited_once()

    agent = admin(AGENT_STATUS_IDLE, False)
    ok, _ = asyncio.run(make_manager(agent).resolve_cg_concern_retry("admin_ai", "x"))
    assert not ok

# END OF FILE tests/unit/test_round2b.py
