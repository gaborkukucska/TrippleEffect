# START OF FILE tests/unit/test_round2g.py
"""
Round 2g: fixtures from the 2026-10-19 18:10-18:24 run with qwen3.5:9b (session DefaultProject/startup_1792404609).

* 18:16:03 - Admin (state 'startup') got the Temp Converter CLI project request, thought "This is definitely a project
  creation request" and still called request_state(state='work') with no task. The project request now goes to planning.
* 18:19:38 - Admin called code_editor with {} and got "missing required parameters" plus a placeholder example; it then
  returned an empty reply. An empty call is now named as such and gets the tool's full usage.
* 18:22:57 - codebase_search on a session that had no files yet answered "Shared workspace does not exist."
"""
import asyncio
import logging
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.constants import (
    ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_STANDBY, ADMIN_STATE_STARTUP, ADMIN_STATE_WORK,
    AGENT_STATUS_IDLE, AGENT_TYPE_ADMIN,
)
from src.agents.tool_activity import ADMIN_WORK_TO_PLANNING_NOTE, project_request_needs_planning
from src.tools.error_handler import ErrorType, tool_error_handler

REQUEST = (
    "Hello :)\n\nPlease start a small project called \"Temp Converter CLI\".\n\nBuild a Python command-line temperature "
    "converter using only the standard library (argparse) and pytest:\n1. A module with two functions, "
    "celsius_to_fahrenheit(c) and fahrenheit_to_celsius(f), plus a main() that uses argparse so that "
    "\"python temp_convert.py 100 C\" prints the converted value.\n2. Pytest tests in tests/test_temp_convert.py that "
    "check 0 C = 32 F, 100 C = 212 F, and that -40 is the same in both directions.\n3. A README.md with usage examples "
    "and how to run the tests (python -m pytest -q)."
)

# Admin's history at 18:16:03, when it requested 'work'
STARTUP_HISTORY = [
    {"role": "system", "content": "Today the <assistant> is acting in a way that best utilises the capabilities of The TrippleEffect framework."},
    {"role": "user", "content": "[System: Backend initialized, begin proactive startup sequence. Introduce yourself briefly.]"},
    {"role": "assistant", "content": "Greetings! Welcome to TrippleEffect, where I'm able to orchestrate groups of AI agents following your command!"},
    {"role": "user", "content": REQUEST},
]


def _history(*extra):
    return [dict(m) for m in STARTUP_HISTORY] + [dict(m) for m in extra]


# --- project_request_needs_planning ---------------------------------------------------------------------------------------
def test_the_observed_work_request_needs_planning():
    assert project_request_needs_planning(_history(), None) is True
    assert project_request_needs_planning(_history(), "") is True
    assert project_request_needs_planning(_history(), "   ") is True


def test_a_task_description_that_is_the_project_request_still_needs_planning():
    assert project_request_needs_planning(_history(), REQUEST) is True


def test_an_own_work_task_keeps_work():
    # admin_ai_conversation_prompt: stalled agent alert -> work with this task_description
    task = "Analyze stalled agent report and send guidance to break loop."
    assert project_request_needs_planning(_history(), task) is False


def test_a_request_that_was_already_planned_keeps_work():
    planned = {"role": "assistant", "content": "[Action Taken: Transitioned to state 'planning']"}
    assert project_request_needs_planning(_history(planned), None) is False
    auto = {"role": "user", "content": "[System State Change]: State changed to planning. Your instructions have been updated. Please proceed."}
    # the auto-planning message is itself the newest user message: it is a framework message, not a request
    assert project_request_needs_planning(_history(auto), None) is False
    assert project_request_needs_planning(_history(), None, auto_planned_for=REQUEST) is False


def test_messages_that_are_not_project_requests_keep_work():
    pm_message = {"role": "user", "content": "[From @pm_temp_converter_cli]: " + REQUEST}
    assert project_request_needs_planning(_history(pm_message), None) is False
    search = {"role": "user", "content": "Please search the web for the three most used Python libraries for command line "
                                         "parsing and tell me how they differ from each other."}
    assert project_request_needs_planning(_history(search), None) is False
    status = {"role": "user", "content": "What is the status of the Temp Converter CLI project that I asked you to build "
                                         "earlier today? Are the tests written yet?"}
    assert project_request_needs_planning(_history(status), None) is False
    assert project_request_needs_planning([], None) is False
    assert project_request_needs_planning(None, None) is False


# --- the cycle handler switches the observed request to planning ----------------------------------------------------------
def _run_state_change(agent_state, requested_state, task_description=None, history=None):
    from src.agents.core import Agent
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager

    logging.disable(logging.CRITICAL)
    try:
        manager = MagicMock(spec=AgentManager)
        manager.db_manager = AsyncMock()
        manager.performance_tracker = AsyncMock()
        manager.send_to_ui = AsyncMock()
        manager.schedule_cycle = AsyncMock(return_value=None)
        manager.current_project = "DefaultProject"
        manager.current_session = "startup_1792404609"
        manager.current_session_db_id = None
        manager._agent_cycle_locks = {}
        workflow_manager = MagicMock()
        workflow_manager.resolve_state_alias.side_effect = lambda agent_type, state: state
        workflow_manager.is_valid_state.return_value = True
        changes = []

        def change_state(agent, state, task_description=None):
            changes.append((state, task_description))
            agent.state = state
            return True

        workflow_manager.change_state.side_effect = change_state
        manager.workflow_manager = workflow_manager

        agent = MagicMock(spec=Agent)
        agent.agent_id = "admin_ai"
        agent.agent_type = AGENT_TYPE_ADMIN
        agent.state = agent_state
        agent.status = AGENT_STATUS_IDLE
        agent.provider_name = "ollama-local"
        agent.model = "qwen3.5:9b-q4_K_M"
        agent.message_history = history if history is not None else _history()
        agent._failover_state = {}
        agent._failed_models_this_cycle = set()
        agent._auto_planning_for = None
        event = {"type": "agent_state_change_requested", "requested_state": requested_state, "agent_id": "admin_ai",
                 "task_description": task_description, "task_id": None}

        async def events(*_args, **_kwargs):
            yield event

        agent.process_message = MagicMock(side_effect=events)
        handler = AgentCycleHandler(manager, MagicMock())
        asyncio.run(handler.run_cycle(agent, 0))
        return changes, agent, manager
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.parametrize("state", [ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_STANDBY])
def test_the_observed_work_request_goes_to_planning(state):
    changes, agent, manager = _run_state_change(state, "work")
    assert changes and changes[0] == (ADMIN_STATE_PLANNING, None)
    contents = [m.get("content") for m in agent.message_history]
    assert "[Action Taken: Transitioned to state 'planning']" in contents
    assert ADMIN_WORK_TO_PLANNING_NOTE in contents
    assert agent._auto_planning_for == REQUEST
    assert any("now planning" in str(c) for c in manager.send_to_ui.call_args_list)


def test_an_own_work_task_still_goes_to_work():
    task = "Analyze stalled agent report and send guidance to break loop."
    changes, agent, _ = _run_state_change(ADMIN_STATE_CONVERSATION, "work", task_description=task)
    assert changes and changes[0] == (ADMIN_STATE_WORK, task)
    assert ADMIN_WORK_TO_PLANNING_NOTE not in [m.get("content") for m in agent.message_history]


def test_a_tool_question_still_goes_to_work():
    question = {"role": "user", "content": "Please search the web for the three most used Python libraries for command "
                                           "line parsing and tell me how they differ from each other."}
    changes, _, _ = _run_state_change(ADMIN_STATE_STARTUP, "work", history=_history(question))
    assert changes and changes[0][0] == ADMIN_STATE_WORK


def test_a_planning_request_is_unchanged():
    changes, agent, _ = _run_state_change(ADMIN_STATE_STARTUP, "planning")
    assert changes and changes[0] == (ADMIN_STATE_PLANNING, None)
    assert ADMIN_WORK_TO_PLANNING_NOTE not in [m.get("content") for m in agent.message_history]


# --- empty tool call ------------------------------------------------------------------------------------------------------
def _code_editor_schema():
    from src.tools.code_editor import CodeEditorTool
    return CodeEditorTool().get_schema()


def test_an_empty_call_is_named_and_has_no_placeholder_example():
    response = tool_error_handler.generate_enhanced_error_response(
        ErrorType.MISSING_PARAMETER, "code_editor", tool_args={}, tool_schema=_code_editor_schema())
    text = tool_error_handler.format_error_for_agent(response)
    assert text.startswith("[Tool Error: code_editor]\nTool 'code_editor' missing required parameters: ")
    assert "with NO arguments at all" in text
    assert "your_value_here" not in text
    assert "`tool_information` tool" in text


def test_a_partial_call_keeps_its_example():
    response = tool_error_handler.generate_enhanced_error_response(
        ErrorType.MISSING_PARAMETER, "code_editor", tool_args={"action": "edit"}, tool_schema=_code_editor_schema())
    text = tool_error_handler.format_error_for_agent(response)
    assert "with NO arguments at all" not in text
    assert "[Correct Usage Example]" in text and '"action": "edit"' in text


def test_the_executor_attaches_the_full_usage_to_an_empty_call():
    from src.tools.code_editor import CodeEditorTool
    from src.tools.executor import ToolExecutor

    executor = ToolExecutor.__new__(ToolExecutor)
    executor.tools = {"code_editor": CodeEditorTool()}
    text = executor._generate_enhanced_error_response(ErrorType.MISSING_PARAMETER, "code_editor", {}, "admin_ai")
    assert "with NO arguments at all" in text
    assert "[Action Usage Context]" in text
    assert CodeEditorTool().get_detailed_usage().strip()[:80] in text
    partial = executor._generate_enhanced_error_response(ErrorType.MISSING_PARAMETER, "code_editor", {"filename": "a.py"}, "admin_ai")
    assert "with NO arguments at all" not in partial


# --- a session without files ----------------------------------------------------------------------------------------------
def test_codebase_search_explains_a_session_without_files(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.codebase_search import CodebaseSearchTool

    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path)
    result = asyncio.run(CodebaseSearchTool().execute(
        agent_id="admin_ai", agent_sandbox_path=tmp_path / "sandbox", project_name="DefaultProject",
        session_name="startup_1792404609", query="temp_convert.py"))
    assert result["status"] == "error"
    assert result["message"].startswith("Shared workspace does not exist yet")
    assert "'DefaultProject'" in result["message"] and "nothing to search" in result["message"]


def test_prompts_route_every_build_request_to_planning():
    import yaml
    from pathlib import Path

    prompts = yaml.safe_load((Path(__file__).resolve().parents[2] / "prompts.yaml").read_text(encoding="utf-8"))
    startup = prompts["admin_ai_startup_prompt"]
    assert "anything that has to be BUILT or WRITTEN as files" in startup
    assert "The 'work' state can NOT build projects" in startup
    assert "Do NOT build projects in this state" in prompts["admin_work_prompt"]
