# START OF FILE tests/unit/test_round2i.py
"""
Round 2i: fixtures from the 2026-10-19 21:02-22:17 run with qwen3.5:9b (project Temp_Converter_CLI, session
startup_1792414918).

* 21:10-21:25 - PM1 thought 15,737 characters in pm_startup and wrote nothing; the kick-off retry ran with thinking again.
* 21:38:45 - PM1 called manage_team {'action': 'create_team'} without team_id. It was rejected, yet the framework said
  "Team creation complete. Step 1 is DONE".
* 21:41-21:47 - PM1 passed team_id 'None' to create_agent and list_agents; the framework created a team named 'None'.
"""
import asyncio
import logging
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.constants import AGENT_STATUS_IDLE, AGENT_TYPE_PM, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_STARTUP
from src.tools.manage_team import ManageTeamTool, is_placeholder_team_id, normalize_team_id

PROJECT, SESSION = "Temp_Converter_CLI", "startup_1792414918"


def _manage_team(**args):
    return asyncio.run(ManageTeamTool().execute("PM1", None, project_name=PROJECT, session_name=SESSION, **args))


# --- team ids -------------------------------------------------------------------------------------------------------------
def test_create_team_without_team_id_creates_the_project_team():
    result = _manage_team(action="create_team")  # the call of 21:38:45
    assert result["status"] == "success_signal_to_handler"
    assert result["action_params"]["team_id"] == "team_Temp_Converter_CLI"


@pytest.mark.parametrize("placeholder", ["None", "none", "null", "N/A", "", "  "])
def test_placeholder_team_ids_become_the_project_team(placeholder):
    result = _manage_team(action="list_agents", team_id=placeholder)  # the call of 21:47:29 had 'None'
    assert result["action_params"]["team_id"] == "team_Temp_Converter_CLI"
    result = _manage_team(action="create_agent", role="Python_Developer", persona="Senior Python Developer",
                          system_prompt="You are a Senior Python Developer.", team_id=placeholder)
    assert result["status"] == "success_signal_to_handler"
    assert result["action_params"]["team_id"] == "team_Temp_Converter_CLI"


def test_real_and_absent_team_ids_are_kept():
    result = _manage_team(action="list_agents", team_id="team_Other")
    assert result["action_params"]["team_id"] == "team_Other"
    result = _manage_team(action="list_agents")
    assert "team_id" not in result["action_params"]  # lists all agents, as before
    result = _manage_team(action="create_agent", role="Coder", persona="Coder", system_prompt="You code.")
    assert "team_id" not in result["action_params"]  # the creator's team is used, as before
    params = {"action": "create_team"}
    assert normalize_team_id("create_team", params, None) is None and "team_id" not in params
    assert normalize_team_id("delete_team", {"team_id": "None"}, PROJECT) is None


def test_is_placeholder_team_id():
    assert is_placeholder_team_id(None) and is_placeholder_team_id("None") and is_placeholder_team_id(" null ")
    assert not is_placeholder_team_id("team_Temp_Converter_CLI") and not is_placeholder_team_id("Nonesuch")


# --- a failed create_team is not reported as done -------------------------------------------------------------------------
def _run_build_team_cycle(tool_content, team_of_pm):
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
        manager.current_project = PROJECT
        manager.current_session = SESSION
        manager.current_session_db_id = None
        manager._agent_cycle_locks = {}
        manager.state_manager = types.SimpleNamespace(get_agent_team=lambda agent_id: team_of_pm,
                                                      get_agents_in_team=lambda team_id: [])
        manager.tool_executor = MagicMock()
        manager.tool_executor.execute_tool = AsyncMock(return_value={"status": "success", "message": "create_agent usage"})
        manager.workflow_manager = MagicMock()
        manager.agents = {}

        agent = MagicMock(spec=Agent)
        agent.agent_id = "PM1"
        agent.agent_type = AGENT_TYPE_PM
        agent.state = PM_STATE_BUILD_TEAM_TASKS
        agent.status = AGENT_STATUS_IDLE
        agent.provider_name = "ollama-local"
        agent.model = "qwen3.5:9b-q4_K_M"
        agent.persona = "Project Manager"
        agent.sandbox_path = None
        agent.message_history = [{"role": "system", "content": "pm prompt"}]
        agent._failover_state = {}
        agent._failed_models_this_cycle = set()
        agent.needs_priority_recheck = False

        call = {"id": "call_n6224qlo", "name": "manage_team", "arguments": {"action": "create_team"}}

        async def events(*_args, **_kwargs):
            yield {"type": "tool_requests", "calls": [call], "raw_assistant_response": ""}

        agent.process_message = MagicMock(side_effect=events)
        interaction = MagicMock()
        interaction.execute_single_tool = AsyncMock(return_value={"call_id": "call_n6224qlo", "name": "manage_team",
                                                                  "content": tool_content})
        handler = AgentCycleHandler(manager, interaction)
        asyncio.run(handler.run_cycle(agent, 0))
        return [m.get("content") for m in agent.message_history if isinstance(m.get("content"), str)]
    finally:
        logging.disable(logging.NOTSET)


def test_a_failed_create_team_is_not_reported_as_done():
    texts = _run_build_team_cycle("Error: Missing required 'team_id' parameter for 'create_team'.", None)
    assert not any("Team creation complete" in t for t in texts)
    assert any("The team was NOT created" in t and "team_id='team_Temp_Converter_CLI'" in t for t in texts)


def test_a_created_team_still_gets_the_next_step():
    texts = _run_build_team_cycle('{"status": "success", "message": "Team created", "data": {"created_team_id": "team_Temp_Converter_CLI"}}',
                                  "team_Temp_Converter_CLI")
    assert any("Team creation complete" in t for t in texts)
    assert not any("The team was NOT created" in t for t in texts)


# --- every reply that only thought is retried without thinking ------------------------------------------------------------
class _FakeProvider:
    def __init__(self, events):
        self.events, self.kwargs_seen = events, []

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        self.kwargs_seen.append(dict(kwargs))
        for event in self.events:
            yield event


def _pm(provider, monkeypatch, state):
    from src.agents.core import Agent
    import src.agents.core as core_module
    import src.llm_providers.ollama_provider as ollama_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", False)
    monkeypatch.setattr(ollama_module, "OllamaProvider", _FakeProvider)  # the fake counts as an Ollama provider

    async def no_workflow(*_args, **_kwargs):
        return None
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools={}),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow),
        cycle_handler=None)
    agent = Agent({"agent_id": "PM1", "config": {"agent_type": "pm", "provider": "ollama", "model": "m"}}, provider, manager)
    agent.state = state
    agent.ensure_sandbox_exists = lambda: True
    return agent


def _consume(agent):
    async def go():
        return [event async for event in agent.process_message()]
    return asyncio.run(go())


THINKING_ONLY = [{"type": "finish_info", "done_reason": "stop", "output_tokens": 3263, "thinking_chars": 15737,
                  "thinking_tail": "Let me now write the JSON kickoff plan."}]


def test_a_reply_that_only_thought_switches_thinking_off_for_the_next_reply(monkeypatch):
    provider = _FakeProvider(THINKING_ONLY)
    agent = _pm(provider, monkeypatch, PM_STATE_STARTUP)
    _consume(agent)
    assert agent._think_off_next_call is True
    provider.events = [{"type": "response_chunk", "content": '{"roles": ["Coder"], "tasks": []}'},
                       {"type": "finish_info", "done_reason": "stop", "thinking_chars": 0, "thinking_tail": ""}]
    _consume(agent)
    assert provider.kwargs_seen[-1].get("think") is False
    assert agent._think_off_next_call is False
    _consume(agent)
    assert "think" not in provider.kwargs_seen[-1]


def test_replies_with_text_or_without_thinking_keep_thinking_on(monkeypatch):
    provider = _FakeProvider([{"type": "response_chunk", "content": "plan"},
                              {"type": "finish_info", "done_reason": "stop", "thinking_chars": 900, "thinking_tail": "x"}])
    agent = _pm(provider, monkeypatch, PM_STATE_STARTUP)
    _consume(agent)
    assert agent._think_off_next_call is False
    provider.events = [{"type": "finish_info", "done_reason": "stop", "thinking_chars": 0, "thinking_tail": ""}]
    _consume(agent)
    assert agent._think_off_next_call is False
