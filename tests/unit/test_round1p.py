"""Round 1p tests: Admin planning produced only an announcement and then idled forever (run of 2026-10-16 07:04-07:08)."""
import asyncio
import types

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents.constants import (
    ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_STATUS_IDLE,
)
from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
from src.agents.tool_activity import (
    MAX_PLANNING_RETRIES, PLANNING_RETRY_MESSAGE, last_assistant_text, planning_reply_needs_retry,
)

OBSERVED = (
    "I'll help you get started with **Temp_Convert_CLI**! Here's the complete workflow we're going to follow, followed by "
    "a JSON block containing only your requested title at the very end of the response."
)

# ---------------------------------------------------------------------------------------------------------- helpers


def test_the_observed_reply_needs_a_retry():
    assert planning_reply_needs_retry(OBSERVED) is True


@pytest.mark.parametrize("text", ["", "   ", None, "Sure!", "Plan:\n1. a\n2. b"])
def test_empty_or_planless_replies_need_a_retry(text):
    assert planning_reply_needs_retry(text) is True


@pytest.mark.parametrize("text", [
    "Which programming language should the converter use?",
    "Before I plan this: do you want a GUI or only a CLI? Tell me and I will continue.",
])
def test_clarifying_questions_are_left_alone(text):
    assert planning_reply_needs_retry(text) is False


def test_a_question_inside_a_json_block_does_not_count_as_clarification():
    assert planning_reply_needs_retry('Plan ready?\n```json\n{"title": "X?"}\n```') is True


def test_last_assistant_text_finds_the_most_recent_assistant_message():
    history = [
        {"role": "assistant", "content": "old"},
        {"role": "tool", "content": "t"},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "newest"},
        {"role": "system", "content": "s"},
        "junk",
    ]
    assert last_assistant_text(history) == "newest"
    assert last_assistant_text([]) == "" and last_assistant_text(None) == ""


def test_the_retry_message_names_the_required_format():
    assert "complete plan" in PLANNING_RETRY_MESSAGE and '{"title"' in PLANNING_RETRY_MESSAGE
    assert "```json" in PLANNING_RETRY_MESSAGE

# ---------------------------------------------------------------------------------------------------------- scheduler


class Ctx:
    """CycleContext stand-in: every flag the scheduler might read is False unless set."""

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
    def __init__(self, state=ADMIN_STATE_PLANNING, agent_type=AGENT_TYPE_ADMIN, history=None):
        self.agent_id = "admin_ai"
        self.agent_type = agent_type
        self.state = state
        self.status = "processing"
        self.message_history = history if history is not None else [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "start Temp_Convert_CLI"},
            {"role": "assistant", "content": OBSERVED},
        ]

    def set_status(self, status):
        self.status = status


class Manager:
    def __init__(self):
        self.scheduled, self.ui = [], []

    async def schedule_cycle(self, agent, retry_count):
        self.scheduled.append((agent.agent_id, retry_count))
        return None

    async def send_to_ui(self, payload):
        self.ui.append(payload)


def run(agent, **flags):
    manager = Manager()
    asyncio.run(NextStepScheduler(manager).schedule_next_step(Ctx(agent, **flags)))
    return manager


def system_messages(agent):
    return [m for m in agent.message_history if m.get("role") == "system" and m is not agent.message_history[0]]


def test_planning_without_a_plan_is_re_requested_with_feedback():
    agent = FakeAgent()
    manager = run(agent)
    assert manager.scheduled == [("admin_ai", 0)]
    assert agent.message_history[-1] == {"role": "system", "content": PLANNING_RETRY_MESSAGE}
    assert agent._planning_retry_count == 1
    assert agent.status == AGENT_STATUS_IDLE
    assert any("asking again (1/2)" in u["content"] for u in manager.ui)


def test_retries_are_bounded_then_the_user_is_told():
    agent = FakeAgent()
    for expected in (1, 2):
        manager = run(agent)
        assert manager.scheduled and agent._planning_retry_count == expected
        agent.message_history.append({"role": "assistant", "content": OBSERVED})
    manager = run(agent)                       # third time: budget spent
    assert manager.scheduled == []
    assert agent._planning_retry_count == MAX_PLANNING_RETRIES
    assert any(u["type"] == "system_event" and "could not produce a project plan" in u["content"] for u in manager.ui)


def test_a_clarifying_question_is_not_retried():
    agent = FakeAgent(history=[{"role": "assistant", "content": "Which language should I use?"}])
    manager = run(agent)
    assert manager.scheduled == [] and getattr(agent, "_planning_retry_count", 0) == 0
    assert manager.ui == []


def test_a_submitted_plan_is_never_retried():
    agent = FakeAgent()
    manager = run(agent, plan_submitted_this_cycle=True)
    assert getattr(agent, "_planning_retry_count", 0) == 0
    assert not any(m.get("content") == PLANNING_RETRY_MESSAGE for m in agent.message_history)


def test_tool_use_in_planning_keeps_its_existing_path():
    agent = FakeAgent()
    manager = run(agent, executed_tool_successfully_this_cycle=True)
    assert manager.scheduled == [("admin_ai", 0)]
    assert getattr(agent, "_planning_retry_count", 0) == 0
    assert not any(m.get("content") == PLANNING_RETRY_MESSAGE for m in agent.message_history)


def test_other_states_and_agents_are_untouched():
    for agent in (FakeAgent(state=ADMIN_STATE_CONVERSATION), FakeAgent(agent_type=AGENT_TYPE_PM, state="pm_standby")):
        manager = run(agent)
        assert getattr(agent, "_planning_retry_count", 0) == 0
        assert not any(m.get("content") == PLANNING_RETRY_MESSAGE for m in agent.message_history)


def test_budget_resets_once_admin_leaves_planning():
    agent = FakeAgent()
    run(agent)
    assert agent._planning_retry_count == 1
    agent.state = ADMIN_STATE_CONVERSATION       # project_creation workflow succeeded
    run(agent)
    assert agent._planning_retry_count == 0
