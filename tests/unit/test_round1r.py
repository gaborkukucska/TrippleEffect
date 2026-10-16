"""Round 1r tests: a project request answered with chat text in startup left Admin without a plan (run of 2026-10-16 07:35)."""
import asyncio

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents.constants import (
    ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_STANDBY, ADMIN_STATE_STARTUP,
    AGENT_STATUS_IDLE, AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
)
from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
from src.agents.tool_activity import is_unhandled_project_request, last_user_text, looks_like_project_request

REQUEST = (
    'Hello :)\n\nPlease start a small project called "Temp_Convert_CLI": a command-line temperature converter in Python.\n\n'
    "Requirements:\n- A single file temp_convert.py with functions celsius_to_fahrenheit(c) and fahrenheit_to_celsius(f).\n"
    "- A test file test_temp_convert.py.\n- A short README.md."
)
CHAT_REPLY = 'I see you\'re kicking off "Temp_Convert_CLI". Since I\'ve got clear requirements here, let\'s get this thing built!'


# ------------------------------------------------------------------------------------------------------ helpers
def test_the_observed_request_is_recognised():
    assert looks_like_project_request(REQUEST) is True


@pytest.mark.parametrize("text", [
    "", None, "Hello :)", "approve project PM1", "How is the project going?",
    "[System: Backend initialized, begin proactive startup sequence. Introduce yourself briefly.]",
    "[MESSAGE_ID: msg_1] [From @PM1]: I noticed two identical tasks in the project and I will start to create a fix for them now, please advise me on how to proceed with the build.",
    "Thanks, that was great. Let's chat about something else for a moment, I would like to hear about your day and how things are going.",
])
def test_chat_approvals_and_framework_messages_are_not_project_requests(text):
    assert looks_like_project_request(text) is False


def test_last_user_text():
    history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "system", "content": "c"}, "junk"]
    assert last_user_text(history) == "a"
    assert last_user_text([]) == "" and last_user_text(None) == ""


def test_marker_prevents_a_second_handling_of_the_same_request():
    agent = FakeAgent()
    assert is_unhandled_project_request(agent) is True
    agent._auto_planning_for = REQUEST
    assert is_unhandled_project_request(agent) is False


# ------------------------------------------------------------------------------------------------------ scheduler
class Ctx:
    def __init__(self, agent, **flags):
        self.agent = agent
        self.all_tool_results = []
        self.retry_count = 0
        self.max_retries_for_cycle = 3
        self.cycle_completed_successfully = True
        self.__dict__.update(flags)

    def __getattr__(self, name):
        return False


class FakeAgent:
    def __init__(self, state=ADMIN_STATE_STARTUP, agent_type=AGENT_TYPE_ADMIN, history=None):
        self.agent_id = "admin_ai"
        self.agent_type = agent_type
        self.state = state
        self.status = "processing"
        self.message_history = history if history is not None else [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "[System: Backend initialized, begin proactive startup sequence.]"},
            {"role": "assistant", "content": "Greetings!"},
            {"role": "user", "content": REQUEST},
            {"role": "assistant", "content": CHAT_REPLY},
        ]

    def set_status(self, status):
        self.status = status


class FakeWorkflowManager:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def change_state(self, agent, state, task_description=None):
        self.calls.append(state)
        if self.ok:
            agent.state = state
        return self.ok


class Manager:
    def __init__(self, ok=True):
        self.scheduled, self.ui = [], []
        self.workflow_manager = FakeWorkflowManager(ok)

    async def schedule_cycle(self, agent, retry_count):
        self.scheduled.append((agent.agent_id, retry_count))

    async def send_to_ui(self, payload):
        self.ui.append(payload)


def run(agent, ok=True, **flags):
    manager = Manager(ok)
    asyncio.run(NextStepScheduler(manager).schedule_next_step(Ctx(agent, **flags)))
    return manager


@pytest.mark.parametrize("state", [ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_STANDBY])
def test_chat_reply_to_a_project_request_switches_admin_to_planning(state):
    agent = FakeAgent(state=state)
    manager = run(agent)
    assert manager.workflow_manager.calls == [ADMIN_STATE_PLANNING]
    assert agent.state == ADMIN_STATE_PLANNING
    assert manager.scheduled == [("admin_ai", 0)]
    assert agent.message_history[-1]["role"] == "user"
    assert "State changed to planning" in agent.message_history[-1]["content"]
    assert agent.status == AGENT_STATUS_IDLE
    assert agent._auto_planning_for == REQUEST
    assert any("planning" in u["content"] for u in manager.ui)


def test_a_plain_greeting_does_not_start_planning():
    agent = FakeAgent(history=[
        {"role": "system", "content": "sys"}, {"role": "user", "content": "Hello :)"}, {"role": "assistant", "content": "Hi!"},
    ])
    manager = run(agent)
    assert manager.workflow_manager.calls == [] and manager.scheduled == [] and agent.state == ADMIN_STATE_STARTUP


def test_the_initial_startup_greeting_cycle_is_untouched():
    agent = FakeAgent(history=[
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "[System: Backend initialized, begin proactive startup sequence. Introduce yourself briefly.]"},
        {"role": "assistant", "content": "Greetings!"},
    ])
    manager = run(agent)
    assert manager.workflow_manager.calls == [] and manager.scheduled == []


def test_no_switch_when_admin_already_requested_a_state_or_used_a_tool():
    for flag in ("state_change_requested_this_cycle", "executed_tool_successfully_this_cycle"):
        agent = FakeAgent()
        manager = run(agent, **{flag: True})
        assert manager.workflow_manager.calls == [], flag


@pytest.mark.parametrize("agent_type", [AGENT_TYPE_PM, AGENT_TYPE_WORKER])
def test_only_admin_is_affected(agent_type):
    agent = FakeAgent(agent_type=agent_type)
    assert run(agent).workflow_manager.calls == []


def test_the_same_request_is_only_handled_once():
    agent = FakeAgent()
    run(agent)
    agent.state = ADMIN_STATE_CONVERSATION
    agent.message_history.append({"role": "assistant", "content": "Planning started."})
    manager = run(agent)
    assert manager.workflow_manager.calls == []


def test_a_failed_switch_does_not_loop_or_crash():
    agent = FakeAgent()
    manager = run(agent, ok=False)
    assert manager.scheduled == [] and agent.state == ADMIN_STATE_STARTUP
    assert not any("State changed" in str(m.get("content")) for m in agent.message_history)
    manager = run(agent, ok=False)             # marker is set: no second attempt
    assert manager.workflow_manager.calls == []
