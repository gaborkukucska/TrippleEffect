# START OF FILE tests/unit/test_round3l.py
"""
Round 3l: the run of 2026-10-24 17:04-21:27 (WiFi_Snake_Game).

* 20:31:20 the Guardian held PM1's message to W4 ("run `sudo apt-get install libsdl2-dev`"); Admin AI approved it, marked
  the Guardian's message read, and the critical concern went to the user. The system paused until 21:25:35.
* During the pause the watchdog wrote "[Framework Watchdog]: You were detected as IDLE ... Resuming cycle..." into W1, W3
  and W4 every minute (54 times each); every reschedule was refused.
* After the user's feedback the system resumed. The newest user message in Admin AI's history was the ORIGINAL project
  request of 17:04: it "planned the new project" again, which became "PROJECT UPDATE 1" while PM1 and 4 workers went on.
"""
import asyncio
import logging
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REQUEST = ("Build a browser-based snake game that can be played on mobile devices over WiFi, with player names, a score "
           "system and a leaderboard. Create the project for it.")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def _agent(agent_id, agent_type, state, history=None, status="idle", config=None):
    return types.SimpleNamespace(agent_id=agent_id, agent_type=agent_type, state=state, status=status,
                                 message_history=list(history or []), agent_config={"config": config or {}},
                                 set_status=lambda value: None)


# ======================================================================================== 1. the deciders learn the outcome
def test_the_admin_ai_learns_what_the_user_decided():
    from src.agents.cg_escalation import CGEscalation, CGConcern, KIND_MESSAGE
    admin = _agent("admin_ai", "admin", "admin_conversation")
    pm1 = _agent("PM1", "pm", "pm_report_check")
    manager = types.SimpleNamespace(agents={"admin_ai": admin, "PM1": pm1}, send_to_ui=AsyncMock(),
                                    schedule_cycle=AsyncMock())
    escalation = CGEscalation(manager)
    concern = CGConcern(concern_id="cg_1", author_id="PM1", author_type="pm", kind=KIND_MESSAGE,
                        text="Run sudo apt-get install libsdl2-dev", concern="GP008: sudo", principles=["GP008"],
                        critical=True, chain=["admin_ai", "user"], target_id="W4", level_index=1)
    escalation._concerns["cg_1"] = concern
    escalation._paused_for = "cg_1"
    with quiet():
        run(escalation.finish_user_review("PM1", "revise", feedback="Use pip only; no system packages."))
    note = admin.message_history[-1]["content"]
    assert note == ("[Constitutional Guardian]: the USER decided concern 'cg_1' on PM1's message: the user asked the author "
                    "to REVISE it. The user's feedback: Use pip only; no system packages. PM1 handles it - there is "
                    "nothing for you to do. This is NOT a new request from the user: do not plan or start anything "
                    "because of it.")
    assert pm1.message_history == []                     # the author gets the revision request through request_revision
    manager.schedule_cycle.assert_not_called()           # a note, nobody is woken up


def test_the_manager_passes_the_user_s_feedback():
    import inspect
    from src.agents import manager
    assert 'await escalation.finish_user_review(agent_id, "revise", feedback=user_feedback)' in inspect.getsource(manager)


# ======================================================================================== 2. no second plan of one request
def test_last_human_text_skips_framework_and_agent_messages():
    from src.agents.replanning_guard import last_human_text
    history = [{"role": "user", "content": REQUEST},
               {"role": "assistant", "content": "planning"},
               {"role": "user", "content": "[System State Change]: State changed to planning."},
               {"role": "user", "content": "[MESSAGE_ID: m1] [From @PM1]: kick-off done"},
               {"role": "system", "content": "[Constitutional Guardian]: the USER decided concern 'cg_1'"}]
    assert last_human_text(history) == REQUEST
    assert last_human_text([]) == "" and last_human_text(None) == ""


def test_the_admin_ai_does_not_plan_the_same_request_twice():
    from src.agents.replanning_guard import replanning_block_message, note_planning_started
    admin = _agent("admin_ai", "admin", "admin_conversation", [{"role": "user", "content": REQUEST}])
    pm1 = _agent("PM1", "pm", "pm_manage", config={"project_name_context": "WiFi_Snake_Game"})
    manager = types.SimpleNamespace(agents={"admin_ai": admin, "PM1": pm1}, current_project="WiFi_Snake_Game")
    assert replanning_block_message(manager, admin) is None          # nothing planned yet
    note_planning_started(admin)
    # 21:25:44: the system resumed; the newest human message is still the 17:04 request
    admin.message_history += [{"role": "system", "content": "[Framework] tool results"},
                              {"role": "user", "content": "[System State Change]: State changed to admin_conversation."}]
    block = replanning_block_message(manager, admin)
    assert block.startswith("[Framework Note]: You were NOT moved to 'planning': the user has sent nothing new since you "
                            "planned their request ('Build a browser-based snake game")
    assert "is being built right now by PM1 and its workers" in block
    # a PM still waiting for approval, or no PM: planning again is allowed (e.g. the plan was rejected)
    pm1._awaiting_project_approval = True
    assert replanning_block_message(manager, admin) is None
    pm1._awaiting_project_approval = False
    # a NEW message from the user (feedback after testing) may be planned as an update
    admin.message_history.append({"role": "user", "content": "The snake is too fast on phones, please slow it down."})
    assert replanning_block_message(manager, admin) is None


def test_both_ways_into_planning_use_the_guard():
    import inspect
    from src.agents import cycle_handler
    from src.agents.cycle_components import next_step_scheduler
    handler_source = inspect.getsource(cycle_handler)
    assert "replan_block = replanning_block_message(self._manager, agent)" in handler_source
    assert "note_planning_started(agent)" in handler_source
    scheduler_source = inspect.getsource(next_step_scheduler)
    assert "switched = bool(not replan_block and workflow_manager" in scheduler_source


# ======================================================================================== 3. the watchdog during a pause
def test_the_watchdog_leaves_paused_agents_alone(monkeypatch):
    import src.agents.manager as manager_module
    from src.agents.manager import AgentManager
    passes = []

    async def fake_sleep(_seconds):
        passes.append(1)
        if len(passes) > 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(manager_module.asyncio, "sleep", fake_sleep)
    w3 = _agent("W3", "worker", "worker_work")
    manager = object.__new__(AgentManager)
    manager.agents = {"W3": w3}
    manager.send_to_ui = AsyncMock()
    scheduled = []

    async def schedule_cycle(agent, retry_count=0):
        scheduled.append(agent.agent_id)
    manager.schedule_cycle = schedule_cycle
    paused = {"value": True}
    manager.cg_escalation = types.SimpleNamespace(
        is_system_paused=lambda: paused["value"],
        cycle_block_reason=lambda agent: "the system is paused" if paused["value"] else None)
    manager.error_recovery = types.SimpleNamespace(consider=AsyncMock(return_value=False))
    with quiet(), pytest.raises(asyncio.CancelledError):
        run(manager._periodic_pm_manage_check())
    assert w3.message_history == [] and scheduled == []
    # without a pause the stalled worker is nudged as before
    paused["value"] = False
    passes.clear()
    with quiet(), pytest.raises(asyncio.CancelledError):
        run(manager._periodic_pm_manage_check())
    assert scheduled and "[Framework Watchdog]" in w3.message_history[-1]["content"]

# END OF FILE tests/unit/test_round3l.py
