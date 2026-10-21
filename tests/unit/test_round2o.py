# START OF FILE tests/unit/test_round2o.py
"""
Round 2o: fixtures from the 2026-10-21 15:43-16:06 run (WiFi_Snake_Game, session startup_1792568609, qwen3.5:9b on one
remote Ollama server).

* 27 of 153 replies were ONLY text like "[Action Taken: called file_system(action='write', filename='models/score_manager.py')]"
  (tool_calls=0): Round 2n wrote such lines into every assistant message that made calls, and the model copied them. Nothing
  ran - W1 "requested" worker_work 13 times (15:53-15:59) and stayed in worker_report. Ollama's /api/chat has no 'template'
  field, so the ChatML override was always ignored; the built-in qwen3.5 renderer already shows earlier calls as <tool_call>.
* The Constitutional Guardian reviewed every plain-text reply (50 reviews, ~269 s) and raised 14 concerns, all false alarms
  (writing models/score_manager.py "violates GP008"; "let me list all tasks" "may expose PII"). Each went to the user, but the
  agent was not paused: the scheduler set it 'idle' and started its next cycle in the same millisecond (16:02:56.736 -> .737).
* The plan "WiFi Snake Game" became one more session of the project of the night before (same folder, empty workspace).
"""
import asyncio
import json
import logging
import shutil
import types
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents.cg_escalation import (
    CGEscalation, KIND_MESSAGE, KIND_REPLY, USER, cited_principles, concern_text, critical_principle_ids, is_reviewed_reply,
    review_context,
)
from src.agents.constants import (
    AGENT_STATUS_AWAITING_CG_REVIEW, AGENT_STATUS_AWAITING_USER_REVIEW_CG, AGENT_STATUS_ERROR, AGENT_STATUS_IDLE,
    BOOTSTRAP_AGENT_ID, CONSTITUTIONAL_GUARDIAN_AGENT_ID,
)
from src.agents.tool_activity import call_imitation_notice, is_call_imitation_text

W1_WRITE_TEXT = "[Action Taken: called file_system(action='write', filename='models/score_manager.py')]"          # 15:50:30
W1_STATE_TEXT = "[Action Taken: called request_state(state='worker_work', task_id='8dc58a27-fb54-4e44-8830-8fdeb37e9c7b')]"
PM1_STATE_TEXT = "[Action Taken: Transitioned back to 'pm_manage' state]"                                         # 15:51:38
W1_REPORT = ("**Progress Report - Backend Developer (W1)**: \n\n✅ **Task 1 Progress**: \n- Created src/app.py with Flask "
             "routes /api/register, /api/game-state, /api/leaderboard\n- Next: models/score_manager.py (SQLite leaderboard)")
GP008_VERDICT = ("<CONCERN>The action involves writing to a specific system path ('models/'). While the code itself may be "
                 "benign, Principle GP008 (Safe Command Execution) requires extreme caution.</CONCERN>")          # 15:50:36
GP003_VERDICT = "<CONCERN>GP003: the report does not say which files were changed.</CONCERN>"
PRINCIPLES = [
    {"id": "GP001", "name": "Privacy", "text": "t", "enabled": True, "critical": True},
    {"id": "GP003", "name": "Transparency", "text": "t", "enabled": True},
    {"id": "GP008", "name": "Safe Command Execution", "text": "t", "enabled": True, "critical": True},
    {"id": "GP009", "name": "Disabled", "text": "t", "enabled": False, "critical": True},
]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def quiet():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


# ======================================================================================== 1. calls written as text
def test_a_reply_that_only_imitates_a_call_is_recognised():
    assert is_call_imitation_text(W1_WRITE_TEXT) and is_call_imitation_text(f"  {W1_STATE_TEXT}\n")
    assert is_call_imitation_text(PM1_STATE_TEXT)
    assert not is_call_imitation_text("I'll write models/score_manager.py next. [Action Taken: called x()]")
    assert not is_call_imitation_text("") and not is_call_imitation_text(None)


def test_the_notice_says_nothing_ran_and_how_to_call():
    text = call_imitation_notice(W1_WRITE_TEXT)
    assert "NOTHING was executed or saved" in text and "real call of the 'file_system' tool" in text
    assert "'filename' and the complete 'content'" in text
    assert "call the 'request_state' tool" in call_imitation_notice(W1_STATE_TEXT)
    assert "call the 'request_state' tool" in call_imitation_notice(PM1_STATE_TEXT)
    long_text = "[Action Taken: called command_executor(action='run_command', command=\"" + "x" * 400 + "\")]"
    assert "..." in call_imitation_notice(long_text) and len(call_imitation_notice(long_text)) < 700


class _FakeProvider:
    def __init__(self, events):
        self.events, self.tools_seen = events, []

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        self.tools_seen.append([t.get("name") for t in (tools or [])])
        for event in self.events:
            yield event


def _core_agent(provider, monkeypatch, agent_id="W1", agent_type="worker", state="worker_work", tools=None, escalation=None):
    from src.agents.core import Agent
    import src.agents.core as core_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", tools is not None)

    async def no_workflow(*_args, **_kwargs):
        return None
    tool_objects = {name: types.SimpleNamespace(auth_level=level, get_json_schema=lambda n=name: {"name": n})
                    for name, level in (tools or {}).items()}
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools=tool_objects),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow,
                                               resolve_state_alias=lambda t, s: s, is_valid_state=lambda t, s: True,
                                               _valid_states={}),
        cycle_handler=None, cg_escalation=escalation)
    agent = Agent({"agent_id": agent_id, "config": {"agent_type": agent_type, "provider": "ollama", "model": "m"}}, provider, manager)
    agent.state = state
    agent.ensure_sandbox_exists = lambda: True
    return agent


def _consume(agent):
    async def go():
        return [event async for event in agent.process_message()]
    return run(go())


def test_core_does_not_yield_an_imitated_call_as_a_reply(monkeypatch):
    provider = _FakeProvider([{"type": "response_chunk", "content": W1_WRITE_TEXT},
                              {"type": "finish_info", "done_reason": "stop", "output_tokens": 93}])
    agent = _core_agent(provider, monkeypatch)
    events = _consume(agent)
    assert not [e for e in events if e.get("type") == "final_response"]
    assert agent._call_imitation_text == W1_WRITE_TEXT
    provider.events = [{"type": "response_chunk", "content": "Task 1 is done."}]
    events = _consume(agent)
    assert [e["content"] for e in events if e.get("type") == "final_response"] == ["Task 1 is done."]
    assert agent._call_imitation_text is None


def test_the_cycle_tells_the_agent_that_nothing_ran():
    from unittest.mock import AsyncMock, MagicMock
    from src.agents.constants import AGENT_TYPE_PM, PM_STATE_ACTIVATE_WORKERS
    from src.agents.core import Agent
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager
    manager = MagicMock(spec=AgentManager)
    manager.db_manager = AsyncMock()
    manager.performance_tracker = AsyncMock()
    manager.send_to_ui = AsyncMock()
    manager.schedule_cycle = AsyncMock(return_value=None)
    manager.current_project, manager.current_session, manager.current_session_db_id = None, "s", None
    manager._agent_cycle_locks = {}
    manager.state_manager = MagicMock()
    manager.state_manager.get_agent_team.return_value = None
    manager.workflow_manager = MagicMock()
    manager.agents = {}
    agent = MagicMock(spec=Agent)
    agent.agent_id, agent.agent_type, agent.state, agent.status = "PM1", AGENT_TYPE_PM, PM_STATE_ACTIVATE_WORKERS, AGENT_STATUS_IDLE
    agent.provider_name, agent.model, agent.persona = "ollama-local", "qwen3.5:9b-q4_K_M", "Project Manager"
    agent.message_history = [{"role": "system", "content": "pm prompt"}]
    agent._failover_state, agent._failed_models_this_cycle = {}, set()
    agent._last_reply_cut_off = agent._think_off_next_call = agent._context_window_full = False
    agent._empty_response_retry_count = agent._manage_unproductive_cycles = agent._manage_cycle_cooldown_until = 0
    agent.needs_priority_recheck = False
    agent.set_state = MagicMock(side_effect=lambda new_state: setattr(agent, "state", new_state))

    async def events(*_args, **_kwargs):
        agent._call_imitation_text = PM1_STATE_TEXT        # what core.py records for an imitated call
        agent._last_reply_thinking_chars, agent._last_reply_thinking_tail = 0, ""
        if False:
            yield {}
    agent.process_message = MagicMock(side_effect=events)
    asyncio.run(AgentCycleHandler(manager, MagicMock()).run_cycle(agent, 0))
    notices = [m["content"] for m in agent.message_history if m.get("role") == "system" and "Framework Notice" in m["content"]]
    assert notices == [call_imitation_notice(PM1_STATE_TEXT)] and agent._call_imitation_text is None
    assert not any("Your last response was empty" in m["content"] for m in agent.message_history)


# ======================================================================================== 2. Ollama formats the chat
def test_uses_builtin_renderer():
    from src.llm_providers.ollama_provider import uses_builtin_renderer
    assert uses_builtin_renderer({"model_template": "{{ .Prompt }}", "model_capabilities": ["completion", "tools", "thinking"]})
    assert uses_builtin_renderer({"model_template": "{{ .Prompt }}", "model_renderer": "qwen3.5"})
    assert not uses_builtin_renderer({"model_template": "{{ .Prompt }}"})
    assert not uses_builtin_renderer({"model_capabilities": ["embedding"]}) and not uses_builtin_renderer(None)


def _serve(routes):
    from aiohttp import web

    async def start():
        app = web.Application()
        for method, path, handler in routes:
            app.router.add_route(method, path, handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    return start


def test_the_payload_has_no_template_and_raw_models_warn_once(monkeypatch, caplog):
    from aiohttp import web
    from src.config.settings import settings
    from src.llm_providers import ollama_provider
    from src.llm_providers.ollama_provider import OllamaProvider
    monkeypatch.setattr(settings, "NATIVE_TOOL_CALLING_ENABLED", True)
    monkeypatch.setattr(ollama_provider, "_RAW_TEMPLATE_WARNED", set())
    payloads = []

    async def chat(request):
        payloads.append(await request.json())
        return web.json_response({"model": "m", "done": True, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 2,
                                  "message": {"role": "assistant", "content": "ok"}})
    infos = {"qwen3.5:9b-q4_K_M": {"model_template": "{{ .Prompt }}", "model_capabilities": ["completion", "tools"]},
             "old-raw:latest": {"model_template": "{{ .Prompt }}"}}
    registry = types.SimpleNamespace(get_model_info=lambda model: infos.get(model))
    history = [{"role": "system", "content": "You are W1."}, {"role": "user", "content": "go"},
               {"role": "assistant", "content": "", "tool_calls": [
                   {"id": "c1", "name": "file_system", "arguments": {"action": "read", "filename": "src/app.py"}}]},
               {"role": "tool", "tool_call_id": "c1", "name": "file_system", "content": "1: x"}]

    async def go():
        runner, url = await _serve([("POST", "/api/chat", chat)])()
        try:
            provider = OllamaProvider(base_url=url, model_registry=registry)
            for model in ("qwen3.5:9b-q4_K_M", "old-raw:latest", "old-raw:latest"):
                [e async for e in provider.stream_completion(messages=history, model=model, temperature=0.7, max_tokens=64,
                                                               tools=[{"type": "function", "function": {"name": "file_system"}}])]
        finally:
            await runner.cleanup()
    logging.disable(logging.NOTSET)
    with caplog.at_level(logging.WARNING, logger="src.llm_providers.ollama_provider"):
        run(go())
    assert all("template" not in p for p in payloads)
    assert payloads[0]["messages"][2]["content"] == "" and payloads[0]["messages"][2]["tool_calls"][0]["function"]["name"] == "file_system"
    raw_warnings = [r for r in caplog.records if "RAW template" in r.getMessage()]
    assert len(raw_warnings) == 1 and "old-raw:latest" in raw_warnings[0].getMessage()


def test_discovery_keeps_renderer_and_parser(caplog):
    from aiohttp import web
    from unittest.mock import MagicMock
    from src.config.model_registry import ModelRegistry
    from src.config.settings import Settings

    async def tags(request):
        return web.json_response({"models": [{"name": "qwen3.5:9b-q4_K_M"}]})

    async def show(request):
        return web.json_response({"details": {"parameter_size": "9B"}, "template": "{{ .Prompt }}", "renderer": "qwen3.5",
                                  "parser": "qwen3.5", "capabilities": ["completion", "vision", "tools", "thinking"]})

    async def go():
        runner, url = await _serve([("GET", "/api/tags", tags), ("POST", "/api/show", show)])()
        try:
            settings_obj = MagicMock(spec=Settings)
            settings_obj.MODEL_TIER = "LOCAL"
            return await ModelRegistry(settings_obj=settings_obj)._verify_and_fetch_models(url)
        finally:
            await runner.cleanup()
    logging.disable(logging.NOTSET)
    with caplog.at_level(logging.WARNING, logger="src.config.model_registry"):
        _provider, _url, models = run(go())
    assert models[0]["model_renderer"] == "qwen3.5" and models[0]["model_parser"] == "qwen3.5"
    assert not [r for r in caplog.records if "RAW template" in r.getMessage()]   # 15:43:31 warned "Has RAW template"


# ======================================================================================== 3. what the Guardian reviews
def test_only_admin_replies_and_messages_are_reviewed():
    assert is_reviewed_reply(types.SimpleNamespace(agent_id=BOOTSTRAP_AGENT_ID, agent_type="admin"))
    assert not is_reviewed_reply(types.SimpleNamespace(agent_id="PM1", agent_type="pm"))
    assert not is_reviewed_reply(types.SimpleNamespace(agent_id="W1", agent_type="worker"))
    w1 = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_report")
    assert review_context(w1, KIND_MESSAGE, "PM1") == ("Author: agent 'W1' (type 'worker', state 'worker_report'). "
                                                       "This is a message the author is sending to agent 'PM1'.")
    assert review_context(types.SimpleNamespace(agent_id="admin_ai", agent_type="admin", state="admin_conversation"),
                          KIND_REPLY).endswith("This is the author's reply to the human user.")


def _run_text_reply(agent_id, agent_type, state, text, verdict):
    from unittest.mock import AsyncMock, MagicMock
    from src.agents.core import Agent
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager
    manager = MagicMock(spec=AgentManager)
    manager.db_manager = AsyncMock()
    manager.performance_tracker = AsyncMock()
    manager.send_to_ui = AsyncMock()
    manager.schedule_cycle = AsyncMock(return_value=None)
    manager.current_project, manager.current_session, manager.current_session_db_id = None, "s", None
    manager._agent_cycle_locks = {}
    manager.state_manager = MagicMock()
    manager.state_manager.get_agent_team.return_value = None
    manager.workflow_manager = MagicMock()
    manager.agents = {}
    manager.cg_escalation = MagicMock()
    manager.cg_escalation.cycle_block_reason.return_value = None
    manager.cg_escalation.raise_concern = AsyncMock()
    manager.cg_escalation.note_outgoing_ok = AsyncMock()
    agent = MagicMock(spec=Agent)
    agent.agent_id, agent.agent_type, agent.state, agent.status = agent_id, agent_type, state, AGENT_STATUS_IDLE
    agent.provider_name, agent.model, agent.persona = "ollama-local", "qwen3.5:9b-q4_K_M", agent_id
    agent.message_history = [{"role": "system", "content": "prompt"}, {"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}, {"role": "user", "content": "status?"}]
    agent._failover_state, agent._failed_models_this_cycle = {}, set()
    agent._last_reply_cut_off = agent._think_off_next_call = agent._context_window_full = False
    agent._call_imitation_text = None
    agent._empty_response_retry_count = agent._manage_unproductive_cycles = agent._manage_cycle_cooldown_until = 0
    agent.needs_priority_recheck = False
    agent.set_state = MagicMock(side_effect=lambda new_state: setattr(agent, "state", new_state))

    async def events(*_args, **_kwargs):
        agent._last_reply_thinking_chars, agent._last_reply_thinking_tail = 0, ""
        yield {"type": "final_response", "content": text, "agent_id": agent_id}
    agent.process_message = MagicMock(side_effect=events)
    handler = AgentCycleHandler(manager, MagicMock())
    handler._get_cg_verdict = AsyncMock(return_value=verdict)
    asyncio.run(handler.run_cycle(agent, 0))
    return agent, handler, manager


def test_a_pm_text_reply_is_not_reviewed():
    """15:53:33 'In pm_manage, I will autonomously inspect the project plan...' raised a GP001 concern."""
    text = "In pm_manage, I will autonomously inspect the project plan and create/assign the next phase of tasks."
    agent, handler, manager = _run_text_reply("PM1", "pm", "pm_report_check", text, GP003_VERDICT)
    handler._get_cg_verdict.assert_not_awaited()
    manager.cg_escalation.raise_concern.assert_not_awaited()
    assert {"role": "assistant", "content": text} in agent.message_history


def test_an_admin_reply_is_reviewed_and_a_concern_goes_to_the_ladder():
    agent, handler, manager = _run_text_reply(BOOTSTRAP_AGENT_ID, "admin", "admin_conversation", "All tests pass.", GP003_VERDICT)
    handler._get_cg_verdict.assert_awaited_once()
    assert handler._get_cg_verdict.await_args.kwargs["review_context"].endswith("This is the author's reply to the human user.")
    args = manager.cg_escalation.raise_concern.await_args.args
    assert args[1:4] == (KIND_REPLY, "All tests pass.", GP003_VERDICT)
    assert {"role": "assistant", "content": "All tests pass."} not in agent.message_history   # held, not delivered
    agent, handler, manager = _run_text_reply(BOOTSTRAP_AGENT_ID, "admin", "admin_conversation", "All tests pass.", "<OK/>")
    manager.cg_escalation.note_outgoing_ok.assert_awaited_once()
    assert {"role": "assistant", "content": "All tests pass."} in agent.message_history


def test_concern_parsing_and_critical_principles(monkeypatch):
    from src.config.settings import settings
    assert concern_text(GP008_VERDICT).startswith("The action involves writing")
    assert cited_principles(GP008_VERDICT) == ["GP008"]
    assert cited_principles("<CONCERN>GP001 and GP004; again GP001</CONCERN>") == ["GP001", "GP004"]
    monkeypatch.setattr(settings, "GOVERNANCE_PRINCIPLES", PRINCIPLES)
    assert critical_principle_ids() == ["GP001", "GP008"]


def test_governance_yaml_marks_privacy_harm_and_commands_critical():
    import yaml
    data = yaml.safe_load((Path(__file__).resolve().parents[2] / "governance.yaml").read_text())
    critical = sorted(p["id"] for p in data["principles"] if p.get("critical"))
    assert critical == ["GP001", "GP004", "GP008"]


def test_the_guardian_prompt_asks_for_concrete_violations_only():
    import yaml
    prompt = yaml.safe_load((Path(__file__).resolve().parents[2] / "prompts.yaml").read_text())["cg_system_prompt"]
    assert "If in doubt, err on the side of caution" not in prompt
    assert "If you are not sure that a principle is actually violated, answer `<OK/>`" in prompt
    assert "is NOT a violation in itself" in prompt
    text = prompt.format(governance_principles_text="P", team_wip_updates="W")
    assert "<CONCERN>" in text and "<OK/>" in text


def test_the_verdict_request_says_who_wrote_the_text(monkeypatch):
    from src.agents import cycle_handler as ch
    seen = []

    class Provider:
        async def stream_completion(self, messages, **kwargs):
            seen.append(messages)
            yield {"type": "response_chunk", "content": "<OK/>"}
    cg = types.SimpleNamespace(status=AGENT_STATUS_IDLE, llm_provider=Provider(), model="m", temperature=0.1)
    cg.set_status = lambda status: setattr(cg, "status", status)
    monkeypatch.setattr(ch.settings, "GOVERNANCE_PRINCIPLES", [], raising=False)
    monkeypatch.setattr(ch.settings, "PROMPTS", {"cg_system_prompt": "{governance_principles_text}{team_wip_updates}"}, raising=False)
    manager = types.SimpleNamespace(agents={CONSTITUTIONAL_GUARDIAN_AGENT_ID: cg},
                                    workflow_manager=types.SimpleNamespace(_build_team_wip_updates=lambda agent, manager: ""))

    async def push(agent_id):
        return None
    manager.push_agent_status_update = push
    handler = object.__new__(ch.AgentCycleHandler)
    handler._manager = manager
    w1 = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_report")
    assert run(handler._get_cg_verdict(w1, W1_REPORT, review_context=review_context(w1, KIND_MESSAGE, "PM1"))) == "<OK/>"
    assert seen[0][1]["content"].startswith("Author: agent 'W1' (type 'worker', state 'worker_report'). This is a message")
    assert run(handler._get_cg_verdict(w1, "hello")) == "<OK/>"       # older callers: no context line
    assert seen[1][1]["content"].startswith("---\nText for Constitutional Review:")


# ======================================================================================== 4. the escalation ladder
def _agent(agent_id, agent_type, state="idle_state"):
    agent = types.SimpleNamespace(agent_id=agent_id, agent_type=agent_type, state=state, status=AGENT_STATUS_IDLE,
                                  message_history=[], persona=agent_id)
    agent.set_status = lambda status, **kw: setattr(agent, "status", status)
    return agent


class FakeManager:
    def __init__(self):
        self.agents = {a.agent_id: a for a in (
            _agent(BOOTSTRAP_AGENT_ID, "admin", "admin_conversation"), _agent("PM1", "pm", "pm_manage"),
            _agent("W1", "worker", "worker_report"), _agent("W2", "worker", "worker_work"),
            _agent(CONSTITUTIONAL_GUARDIAN_AGENT_ID, "constitutional_guardian"))}
        self.ui, self.scheduled, self.routed = [], [], []
        self.interaction_handler = types.SimpleNamespace(route_and_activate_agent_message=self._route)
        self.state_manager = types.SimpleNamespace(
            get_agent_team=lambda agent_id: "team_WiFi_Snake_Game" if agent_id in ("PM1", "W1", "W2") else None,
            get_agents_in_team=lambda team: [self.agents["PM1"], self.agents["W1"], self.agents["W2"]])
        self.cg_escalation = CGEscalation(self)
        self.current_session_db_id = None
        self.tool_executor = types.SimpleNamespace(tools={})

    def _awaiting_cg_decision(self, agent, action):
        from src.agents.manager import AgentManager
        return AgentManager._awaiting_cg_decision(agent, action)

    async def send_to_ui(self, payload):
        self.ui.append(payload)

    async def schedule_cycle(self, agent, retry_count=0):
        reason = self.cg_escalation.cycle_block_reason(agent)
        if reason:
            self.cg_escalation.defer(agent, retry_count)
            return None
        self.scheduled.append(agent.agent_id)
        return "task"

    async def _route(self, sender_id, target_identifier, message_content):
        self.routed.append((sender_id, target_identifier, message_content))
        return {"status": "delivered", "message": "ok"}, None


@pytest.fixture
def ladder(monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings, "GOVERNANCE_PRINCIPLES", PRINCIPLES)
    monkeypatch.setattr(settings, "CG_DECISION_TIMEOUT_SECONDS", 300.0, raising=False)
    monkeypatch.setattr(settings, "CG_REVISION_TIMEOUT_SECONDS", 900.0, raising=False)
    monkeypatch.setattr(settings, "CG_MAX_REVISIONS_PER_LEVEL", 2, raising=False)
    return FakeManager()


def test_the_chain_of_deciders(ladder):
    esc = ladder.cg_escalation
    assert esc.decider_chain(ladder.agents["W1"]) == ["PM1", BOOTSTRAP_AGENT_ID, USER]
    assert esc.decider_chain(ladder.agents["PM1"]) == [BOOTSTRAP_AGENT_ID, USER]
    assert esc.decider_chain(ladder.agents[BOOTSTRAP_AGENT_ID]) == [USER]


def test_a_workers_message_is_held_and_its_pm_decides(ladder):
    esc, w1 = ladder.cg_escalation, ladder.agents["W1"]
    concern = run(esc.raise_concern(w1, KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))
    assert concern.concern_id == "cg_1" and concern.decider == "PM1" and not concern.critical
    assert w1.status == AGENT_STATUS_AWAITING_CG_REVIEW
    sender, target, text = ladder.routed[0]
    assert (sender, target) == (CONSTITUTIONAL_GUARDIAN_AGENT_ID, "PM1")
    assert "concern_id='cg_1'" in text and "W1's message to 'PM1' was HELD" in text and W1_REPORT in text
    assert "decision='escalate': 'admin_ai' decides instead" in text
    # only W1 waits; nobody else is blocked and no system pause
    assert "held by Constitutional Guardian concern 'cg_1'" in esc.cycle_block_reason(w1)
    assert esc.cycle_block_reason(ladder.agents["W2"]) is None and esc.cycle_block_reason(ladder.agents["PM1"]) is None
    assert not esc.is_system_paused()
    run(ladder.schedule_cycle(w1, 0))
    assert "W1" not in ladder.scheduled
    # the PM approves: the message is delivered from W1, W1 is told and runs again
    ok, text = run(esc.decide("PM1", "cg_1", "approve"))
    assert ok and "delivered" in text
    assert ladder.routed[-1] == ("W1", "PM1", W1_REPORT)
    assert "approved your held message to 'PM1'" in w1.message_history[-1]["content"]
    assert w1.status == AGENT_STATUS_IDLE and ladder.scheduled == ["W1"] and esc.cycle_block_reason(w1) is None


def test_a_revision_is_checked_again_and_settles_the_concern(ladder):
    esc, w1 = ladder.cg_escalation, ladder.agents["W1"]
    run(esc.raise_concern(w1, KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))
    ok, _ = run(esc.decide("PM1", "1", "revise", "List the files you changed."))       # a bare number works too
    assert ok and esc.get("cg_1").status == "revising" and esc.cycle_block_reason(w1) is None
    assert "asks you to REVISE it. Feedback: List the files you changed." in w1.message_history[-1]["content"]
    run(esc.note_outgoing_ok(w1))
    assert esc.get("cg_1").status == "resolved"


def test_revisions_that_keep_failing_climb(ladder):
    esc, w1 = ladder.cg_escalation, ladder.agents["W1"]
    run(esc.raise_concern(w1, KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))
    for revision in (1, 2):
        run(esc.decide("PM1", "cg_1", "revise", "again"))
        concern = run(esc.raise_concern(w1, KIND_MESSAGE, W1_REPORT + f" v{revision}", GP003_VERDICT, target_id="PM1"))
        assert concern.concern_id == "cg_1" and concern.revisions == revision and concern.decider == "PM1"
    run(esc.decide("PM1", "cg_1", "revise", "again"))
    concern = run(esc.raise_concern(w1, KIND_MESSAGE, W1_REPORT + " v3", GP003_VERDICT, target_id="PM1"))
    assert concern.revisions == 3 and concern.decider == BOOTSTRAP_AGENT_ID
    assert ladder.routed[-1][1] == BOOTSTRAP_AGENT_ID and "This is revision 3 of it." in ladder.routed[-1][2]


def test_a_critical_concern_climbs_to_the_user_and_pauses_everything(ladder):
    esc, w1, w2 = ladder.cg_escalation, ladder.agents["W1"], ladder.agents["W2"]
    concern = run(esc.raise_concern(w1, KIND_MESSAGE, W1_REPORT, GP008_VERDICT, target_id="PM1"))
    assert concern.critical and "CRITICAL principle (GP008): approving does NOT settle it" in ladder.routed[-1][2]
    ok, text = run(esc.decide("PM1", "cg_1", "approve"))
    assert ok and "does not settle it" in text and concern.decider == BOOTSTRAP_AGENT_ID
    assert "was passed to 'admin_ai'" in ladder.agents["PM1"].message_history[-1]["content"]
    assert ("W1", "PM1", W1_REPORT) not in ladder.routed                       # not delivered
    ok, _ = run(esc.decide(BOOTSTRAP_AGENT_ID, "cg_1", "approve"))
    assert ok and concern.decider == USER and esc.is_system_paused()
    card = [u for u in ladder.ui if u.get("type") == "cg_concern"][-1]
    assert card["agent_id"] == "W1" and card["kind"] == "message" and card["target_id"] == "PM1" and card["system_paused"]
    assert card["critical"] and card["principles"] == ["GP008"] and len(card["trail"]) >= 3
    assert w1.cg_awaiting_user_decision and w1.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG
    for agent in (w2, ladder.agents["PM1"], ladder.agents[BOOTSTRAP_AGENT_ID]):
        assert "the system is paused" in esc.cycle_block_reason(agent)
    run(ladder.schedule_cycle(w2, 0))
    assert ladder.scheduled == []


def test_the_user_approves_a_held_message_and_everything_resumes(ladder):
    from src.agents.manager import AgentManager
    esc, w1, w2 = ladder.cg_escalation, ladder.agents["W1"], ladder.agents["W2"]
    run(esc.raise_concern(w1, KIND_MESSAGE, W1_REPORT, GP008_VERDICT, target_id="PM1"))
    run(esc.decide("PM1", "cg_1", "escalate", "not sure"))
    run(esc.decide(BOOTSTRAP_AGENT_ID, "cg_1", "escalate"))
    run(ladder.schedule_cycle(w2, 0))                                           # deferred while paused
    ok, _ = run(AgentManager.resolve_cg_concern_approve(ladder, "W1"))
    assert ok and ladder.routed[-1] == ("W1", "PM1", W1_REPORT)
    assert not esc.is_system_paused() and esc.get("cg_1").status == "resolved"
    assert sorted(ladder.scheduled) == ["W1", "W2"] and w1.cg_awaiting_user_decision is False


def test_the_user_asks_for_a_revision_or_stops_the_agent(ladder):
    from src.agents.manager import AgentManager
    esc, w1 = ladder.cg_escalation, ladder.agents["W1"]
    run(esc.raise_concern(w1, KIND_MESSAGE, W1_REPORT, GP008_VERDICT, target_id="PM1"))
    run(esc.decide("PM1", "cg_1", "escalate"))
    run(esc.decide(BOOTSTRAP_AGENT_ID, "cg_1", "escalate"))
    ok, _ = run(AgentManager.resolve_cg_concern_retry(ladder, "W1", None))
    assert ok and esc.get("cg_1").status == "revising" and not esc.is_system_paused()
    assert "Rewrite it so that it no longer raises the Guardian's concern" in w1.message_history[-1]["content"]
    assert ladder.scheduled == ["W1"]
    # a second concern on W2, stopped by the user
    w2 = ladder.agents["W2"]
    run(esc.raise_concern(w2, KIND_MESSAGE, "rm -rf ~/ please", GP008_VERDICT, target_id="PM1"))
    run(esc.decide("PM1", "cg_2", "escalate"))
    run(esc.decide(BOOTSTRAP_AGENT_ID, "cg_2", "escalate"))
    ok, _ = run(AgentManager.resolve_cg_concern_stop(ladder, "W2"))
    assert ok and w2.status == AGENT_STATUS_ERROR and not esc.is_system_paused() and esc.get("cg_2").status == "resolved"


def test_admin_ai_replies_go_straight_to_the_user(ladder):
    from src.agents.manager import AgentManager
    esc, admin = ladder.cg_escalation, ladder.agents[BOOTSTRAP_AGENT_ID]
    event = {"type": "final_response", "agent_id": BOOTSTRAP_AGENT_ID, "content": "All tests pass."}
    concern = run(esc.raise_concern(admin, KIND_REPLY, "All tests pass.", GP003_VERDICT, event_data=event))
    assert concern.decider == USER and esc.is_system_paused() and admin.cg_original_event_data == event
    ladder.workflow_manager = types.SimpleNamespace(process_agent_output_for_workflow=lambda **kw: asyncio.sleep(0))
    ok, _ = run(AgentManager.resolve_cg_concern_approve(ladder, BOOTSTRAP_AGENT_ID))
    assert ok and event in ladder.ui                                             # the approved reply is shown
    assert admin.message_history[-1] == {"role": "assistant", "content": "All tests pass."}
    assert not esc.is_system_paused() and concern.status == "resolved"


def test_wrong_deciders_and_decisions_are_refused(ladder):
    esc = ladder.cg_escalation
    run(esc.raise_concern(ladder.agents["W1"], KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))
    assert run(esc.decide("W2", "cg_1", "approve")) == (False, "Concern 'cg_1' is for 'PM1' to decide, not for you.")
    ok, text = run(esc.decide("PM1", "cg_9", "approve"))
    assert not ok and "Concerns waiting for your decision: cg_1." in text
    ok, text = run(esc.decide("PM1", "cg_1", "ignore"))
    assert not ok and "approve, revise, escalate" in text
    assert run(esc.decide(BOOTSTRAP_AGENT_ID, "cg_1", "approve"))[0]                   # Admin AI outranks the PM
    assert run(esc.decide("PM1", "cg_1", "approve")) == (False, "Concern 'cg_1' is already resolved; nothing to decide.")


def test_the_guardian_moves_undecided_concerns_and_closes_abandoned_revisions(ladder):
    esc = ladder.cg_escalation
    concern = run(esc.raise_concern(ladder.agents["W1"], KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))
    assert run(esc.check_timeouts(now=concern.level_since + 100)) == []
    assert run(esc.check_timeouts(now=concern.level_since + 301)) == ["cg_1"] and concern.decider == BOOTSTRAP_AGENT_ID
    assert "no decision from 'PM1' within 300 s" in concern.trail[-1]
    run(esc.decide(BOOTSTRAP_AGENT_ID, "cg_1", "revise", "shorter"))
    assert run(esc.check_timeouts(now=concern.level_since + 901)) == ["cg_1"] and concern.status == "resolved"


def test_a_held_decider_is_skipped(ladder):
    esc = ladder.cg_escalation
    run(esc.raise_concern(ladder.agents["PM1"], KIND_MESSAGE, "W1, store the players' e-mails", "<CONCERN>GP001</CONCERN>",
                          target_id="W1"))
    concern = run(esc.raise_concern(ladder.agents["W1"], KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))
    assert concern.decider == BOOTSTRAP_AGENT_ID and "'PM1' cannot decide now" in concern.trail[1]


# ======================================================================================== 5. where the ladder plugs in
def test_resolve_concern_is_offered_only_to_a_waiting_decider(monkeypatch, ladder):
    esc = ladder.cg_escalation
    tools = {"send_message": "worker", "resolve_concern": "pm"}
    provider = _FakeProvider([{"type": "response_chunk", "content": "ok"}])
    pm = _core_agent(provider, monkeypatch, agent_id="PM1", agent_type="pm", state="pm_manage", tools=tools, escalation=esc)
    _consume(pm)
    run(esc.raise_concern(ladder.agents["W1"], KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))
    _consume(pm)
    assert "resolve_concern" not in provider.tools_seen[0] and "resolve_concern" in provider.tools_seen[1]


def test_the_tool_calls_the_ladder(ladder):
    from src.tools.resolve_concern import ResolveConcernTool
    run(ladder.cg_escalation.raise_concern(ladder.agents["W1"], KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))
    result = run(ResolveConcernTool().execute("PM1", Path("."), manager=ladder, concern_id="cg_1", decision="approve"))
    assert result == {"status": "success", "message": "Approved: W1's message was delivered."}
    assert run(ResolveConcernTool().execute("PM1", Path("."), concern_id="cg_1", decision="approve"))["status"] == "error"
    assert ResolveConcernTool().auth_level == "pm"


def _handler(ladder, verdict):
    from src.agents.interaction_handler import AgentInteractionHandler

    async def get_verdict(agent, text, review_context=None):
        ladder.reviewed = (agent.agent_id, text, review_context)
        return verdict
    ladder.cycle_handler = types.SimpleNamespace(_get_cg_verdict=get_verdict)
    handler = AgentInteractionHandler(ladder)
    handler.route_and_activate_agent_message = ladder._route
    return handler


def test_send_message_is_reviewed_and_held(ladder):
    w1 = ladder.agents["W1"]
    handler = _handler(ladder, GP003_VERDICT)
    result = run(handler.execute_single_tool(w1, "call_1", "send_message",
                                             {"target_agent_id": "PM1", "message_content": W1_REPORT}, "WiFi_Snake_Game", "s"))
    assert ladder.reviewed[2] == "Author: agent 'W1' (type 'worker', state 'worker_report'). This is a message the author is sending to agent 'PM1'."
    assert result["_raw_result"]["status"] == "held" and "was HELD and NOT delivered (concern 'cg_1'" in result["content"]
    assert "'PM1' decides" in result["content"] and "error" not in result["content"].lower()
    assert [r for r in ladder.routed if r[0] == "W1"] == []                       # not delivered
    from src.agents.tool_activity import is_tool_result_failure
    assert not is_tool_result_failure("send_message", result["content"])


def test_send_message_passes_and_settles_a_revision(ladder):
    w1 = ladder.agents["W1"]
    run(ladder.cg_escalation.raise_concern(w1, KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))
    run(ladder.cg_escalation.decide("PM1", "cg_1", "revise", "list the files"))
    handler = _handler(ladder, "<OK/>")
    result = run(handler.execute_single_tool(w1, "call_2", "send_message",
                                             {"target_agent_id": "PM1", "message_content": W1_REPORT + "\nFiles: src/app.py"},
                                             "WiFi_Snake_Game", "s"))
    assert ladder.routed[-1] == ("W1", "PM1", W1_REPORT + "\nFiles: src/app.py") and result["content"] == "ok"
    assert ladder.cg_escalation.get("cg_1").status == "resolved"


def test_the_manager_does_not_start_a_held_agents_cycle(ladder):
    from src.agents.manager import AgentManager
    manager = object.__new__(AgentManager)
    manager.cg_escalation = ladder.cg_escalation
    started = []

    async def run_cycle(agent, retry_count=0):
        started.append(agent.agent_id)
    manager.cycle_handler = types.SimpleNamespace(run_cycle=run_cycle)
    run(ladder.cg_escalation.raise_concern(ladder.agents["W1"], KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))

    async def go():
        assert await manager.schedule_cycle(ladder.agents["W1"], 1) is None
        task = await manager.schedule_cycle(ladder.agents["W2"], 0)
        await task
    run(go())
    assert started == ["W2"] and ladder.cg_escalation._deferred == {"W1": 1}


def test_run_cycle_does_not_start_while_paused(ladder):
    from src.agents import cycle_handler as ch
    handler = object.__new__(ch.AgentCycleHandler)
    handler._manager = ladder
    ladder._agent_cycle_locks = {}
    ladder.cg_escalation._paused_for = "cg_7"
    handler._run_cycle_internal = None                    # would raise if the cycle really started
    run(handler.run_cycle(ladder.agents["W2"], 3))       # returns before taking the lock or calling the LLM
    assert ladder.cg_escalation._deferred == {"W2": 3} and not ladder._agent_cycle_locks["W2"].locked()
    ladder.cg_escalation._paused_for = None
    run(ladder.cg_escalation.resume())
    assert ladder.scheduled == ["W2"] and ladder.cg_escalation._deferred == {}


def test_a_held_cycle_is_logged_as_waiting_not_as_an_error(ladder, caplog):
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    run(ladder.cg_escalation.raise_concern(ladder.agents["W1"], KIND_MESSAGE, W1_REPORT, GP003_VERDICT, target_id="PM1"))
    logging.disable(logging.NOTSET)
    with caplog.at_level(logging.INFO, logger="src.agents.cycle_components.next_step_scheduler"):
        run(NextStepScheduler(ladder)._schedule_new_cycle(ladder.agents["W1"], 0))
    assert any("waits: its message is held" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ======================================================================================== 6. an existing project
@pytest.fixture
def projects(tmp_path):
    base = tmp_path / "projects"
    old = base / "WiFi_Snake_Game" / "startup_1792509061"
    (old / "shared_workspace" / "src").mkdir(parents=True)
    (old / "shared_workspace" / "src" / "app.py").write_text("app = 1\n")
    (old / "shared_workspace" / "src" / "game_logic.py").write_text("class GameStateManager: pass\n")
    (old / "shared_workspace" / "PROJECT_PLAN.md").write_text("# Project Plan: WiFi_Snake_Game (old)\n")
    (old / "shared_workspace" / "PROJECT_STRUCTURE.md").write_text("old structure\n")
    (old / "shared_workspace" / "conftest.py").write_text("# framework\n")
    (old / "shared_workspace" / "src" / "__pycache__").mkdir()
    (old / "shared_workspace" / "src" / "__pycache__" / "app.cpython-313.pyc").write_bytes(b"x")
    (old / "task_data").mkdir()
    (old / "agent_session_data.json").write_text("{}")
    (base / "Temp_Converter_CLI" / "startup_1792451114" / "shared_workspace").mkdir(parents=True)
    return base


def test_an_existing_project_is_recognised(projects):
    from src.utils.project_folders import existing_project_info
    info = existing_project_info(projects, "WiFi_Snake_Game", "startup_1792568609")
    assert info["name"] == "WiFi_Snake_Game" and info["latest_session"] == "startup_1792509061"
    assert info["suggested_new_name"] == "WiFi_Snake_Game_2"
    assert info["sessions"][0]["name"] == "startup_1792509061" and info["sessions"][0]["files"] == 5
    # the current session with only the kickoff task data (created before approval) is not an earlier project
    (projects / "WiFi_Snake_Game" / "startup_1792568609" / "task_data").mkdir(parents=True)
    assert [s["name"] for s in existing_project_info(projects, "WiFi_Snake_Game", "startup_1792568609")["sessions"]] == ["startup_1792509061"]
    assert existing_project_info(projects, "Snake_Arena", "startup_1792568609") is None
    assert existing_project_info(projects, "Temp_Converter_CLI", "startup_1792568609")["sessions"][0]["files"] == 0


def test_unique_project_names(projects):
    from src.utils.project_folders import unique_project_name
    assert unique_project_name(projects, "WiFi_Snake_Game") == "WiFi_Snake_Game_2"
    (projects / "WiFi_Snake_Game_2").mkdir()
    assert unique_project_name(projects, "WiFi_Snake_Game") == "WiFi_Snake_Game_3"
    assert unique_project_name(projects, "WiFi_Snake_Game_2") == "WiFi_Snake_Game_3"


def test_continuing_carries_the_latest_files_over(projects):
    from src.utils.project_folders import carry_over_note, carry_over_workspace
    new_ws = projects / "WiFi_Snake_Game" / "startup_1792568609" / "shared_workspace"
    new_ws.mkdir(parents=True)
    (new_ws / "PROJECT_PLAN.md").write_text("# Project Plan: WiFi_Snake_Game (new)\n")
    (new_ws / "src").mkdir()
    (new_ws / "src" / "app.py").write_text("app = 2\n")
    session, copied = carry_over_workspace(projects, "WiFi_Snake_Game", None, "startup_1792568609")
    assert session == "startup_1792509061" and sorted(copied) == ["PREVIOUS_PROJECT_PLAN.md", "src/game_logic.py"]
    assert (new_ws / "src" / "app.py").read_text() == "app = 2\n"                  # never overwritten
    assert (new_ws / "PROJECT_PLAN.md").read_text().endswith("(new)\n")
    assert not (new_ws / "PROJECT_STRUCTURE.md").exists() and not (new_ws / "conftest.py").exists()
    assert not (new_ws / "src" / "__pycache__").exists()
    note = carry_over_note("WiFi_Snake_Game", session, copied)
    assert "CONTINUE the existing project 'WiFi_Snake_Game'" in note and "PREVIOUS_PROJECT_PLAN.md" in note
    assert carry_over_workspace(projects, "WiFi_Snake_Game", "startup_1792568609", "startup_1792568609") == (None, [])


def test_a_new_project_moves_the_pending_task_data(projects):
    from src.utils.project_folders import move_pending_project
    pending = projects / "WiFi_Snake_Game" / "startup_1792568609" / "task_data"
    pending.mkdir(parents=True)
    (pending / "pending.data").write_text("")
    (pending / ".taskrc").write_text("uda.assignee.type=string\n")
    assert move_pending_project(projects, "WiFi_Snake_Game", "WiFi_Snake_Game_2", "startup_1792568609")
    assert (projects / "WiFi_Snake_Game_2" / "startup_1792568609" / "task_data" / "pending.data").exists()
    assert not (projects / "WiFi_Snake_Game" / "startup_1792568609").exists()
    assert (projects / "WiFi_Snake_Game" / "startup_1792509061").exists()          # the old project is untouched


@pytest.mark.skipif(shutil.which("task") is None, reason="Taskwarrior is not installed")
def test_a_new_project_renames_its_tasks(projects):
    from tasklib import Task, TaskWarrior
    from src.utils.project_folders import move_pending_project
    data = projects / "WiFi_Snake_Game" / "startup_1792568609" / "task_data"
    data.mkdir(parents=True)
    (data / ".taskrc").write_text("uda.assignee.type=string\nuda.task_progress.type=string\n")
    tw = TaskWarrior(data_location=str(data), taskrc_location=str(data / ".taskrc"))
    Task(tw, description="PROJECT KICK-OFF: WiFi_Snake_Game\n\nFull Plan Overview: ...", project="WiFi_Snake_Game").save()
    move_pending_project(projects, "WiFi_Snake_Game", "WiFi_Snake_Game_2", "startup_1792568609")
    moved = projects / "WiFi_Snake_Game_2" / "startup_1792568609" / "task_data"
    tasks = TaskWarrior(data_location=str(moved), taskrc_location=str(moved / ".taskrc")).tasks.filter(project="WiFi_Snake_Game_2")
    assert len(tasks) == 1 and tasks[0]["description"].startswith("PROJECT KICK-OFF: WiFi_Snake_Game_2")


def test_project_structure_lists_existing_files_separately():
    from src.workflows.pm_kickoff_workflow import render_project_structure_md
    md = render_project_structure_md(["src", "models"], ["src/app.py", "models/score_manager.py"], None, ["src/app.py"])
    create = md.split("## Files To Create")[1].split("## Existing Files")[0]
    assert "- models/score_manager.py" in create and "src/app.py" not in create
    assert "## Existing Files" in md and md.rstrip().endswith("- src/app.py")
    assert "## Existing Files" not in render_project_structure_md(["src"], ["src/app.py"], None)


def _pm_for_approval(info):
    pm = types.SimpleNamespace(
        agent_id="PM1", status=AGENT_STATUS_IDLE, state="pm_startup", _awaiting_project_approval=True,
        _existing_project_info=info, message_history=[], persona="Project Manager (WiFi_Snake_Game)",
        agent_config={"config": {"project_name_context": "WiFi_Snake_Game", "session_name": "startup_1792568609",
                                 "persona": "Project Manager (WiFi_Snake_Game)", "initial_plan_description": "Build it."}})
    return pm


def _approval_manager(pm):
    contexts, ui, scheduled = [], [], []

    async def set_context(project, session, loading=False):
        contexts.append((project, session))

    async def send_to_ui(payload):
        ui.append(payload)

    async def schedule_cycle(agent, retry_count=0):
        scheduled.append(agent.agent_id)
    manager = types.SimpleNamespace(agents={"PM1": pm}, current_session="startup_1792568609",
                                    set_project_session_context=set_context, send_to_ui=send_to_ui, schedule_cycle=schedule_cycle)
    return manager, contexts, ui, scheduled


def _approve(manager, mode):
    from src.api.http_routes import approve_project_start

    async def go():
        result = await approve_project_start("PM1", mode, manager)
        await asyncio.sleep(0)
        return result
    return run(go())


def test_approval_asks_and_continues_the_existing_project(projects, monkeypatch):
    from fastapi import HTTPException
    from src.config.settings import settings
    from src.utils.project_folders import existing_project_info
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", projects)
    pm = _pm_for_approval(existing_project_info(projects, "WiFi_Snake_Game", "startup_1792568609"))
    manager, contexts, ui, scheduled = _approval_manager(pm)
    with pytest.raises(HTTPException) as refused:
        _approve(manager, None)
    assert refused.value.status_code == 409 and "choose 'continue'" in refused.value.detail
    assert pm._awaiting_project_approval is True                                 # the card can still be answered
    _approve(manager, "continue")
    workspace = projects / "WiFi_Snake_Game" / "startup_1792568609" / "shared_workspace"
    assert contexts == [("WiFi_Snake_Game", "startup_1792568609")] and scheduled == ["PM1"]
    assert (workspace / "PROJECT_PLAN.md").read_text().startswith("# Project Plan: WiFi_Snake_Game\n\nBuild it.")
    assert (workspace / "src" / "game_logic.py").exists() and (workspace / "PREVIOUS_PROJECT_PLAN.md").exists()
    assert "CONTINUE the existing project" in pm.message_history[-1]["content"]
    assert "(continuing the existing project)" in ui[-1]["message"]


def test_approval_starts_a_new_project(projects, monkeypatch):
    from src.config.settings import settings
    from src.utils.project_folders import existing_project_info
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", projects)
    pending = projects / "WiFi_Snake_Game" / "startup_1792568609" / "task_data"
    pending.mkdir(parents=True)
    pm = _pm_for_approval(existing_project_info(projects, "WiFi_Snake_Game", "startup_1792568609"))
    manager, contexts, ui, scheduled = _approval_manager(pm)
    _approve(manager, "new")
    assert contexts == [("WiFi_Snake_Game_2", "startup_1792568609")]
    assert pm.agent_config["config"]["project_name_context"] == "WiFi_Snake_Game_2"
    assert pm.persona == "Project Manager (WiFi_Snake_Game_2)" == pm.agent_config["config"]["persona"]
    assert (projects / "WiFi_Snake_Game_2" / "startup_1792568609" / "task_data").is_dir()
    assert (projects / "WiFi_Snake_Game_2" / "startup_1792568609" / "shared_workspace" / "PROJECT_PLAN.md").exists()
    assert not (projects / "WiFi_Snake_Game" / "startup_1792568609").exists()
    assert "(as a new project)" in ui[-1]["message"] and pm.message_history == []


def test_approval_without_an_existing_project_is_unchanged(projects, monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", projects)
    pm = _pm_for_approval(None)
    pm.agent_config["config"]["project_name_context"] = "Snake_Arena"
    manager, contexts, ui, scheduled = _approval_manager(pm)
    _approve(manager, None)
    assert contexts == [("Snake_Arena", "startup_1792568609")] and scheduled == ["PM1"]
    assert ui[-1]["message"] == "Project 'Snake_Arena' managed by 'PM1' approved and started."

# END OF FILE tests/unit/test_round2o.py
