# START OF FILE tests/unit/test_round3w.py
"""
Round 3w (run 2026-10-26 23:37 - 2026-10-27 08:08, WiFi_Snake_Game, qwen3.5:4b on Ollama behind a Cloudflare proxy):

1. "Fix SyntaxError in tests/test_server.py ..." was taken for a "make the test suite pass" task (the PATH holds "tests"):
   W3's finished one-line fix was refused 26 times until the whole suite passed (00:37-08:04).
2. A reply the model server could not parse ("XML syntax error ... element <parameter> closed by </function>"), stopped as
   repetitive ("token repeat limit reached") or stopped by the framework as a runaway was handled as a provider error: nine
   failovers moved agents between qwen3.5:4b, gemma4:12b and qwen3.5:9b in the middle of their tasks.
3. 93 requests were answered "524" by the proxy: they waited in the server's queue; the per-server limiter adapts now.
4. Tool results went to qwen as user messages ("--- Tool Response ---") also with native tool calling.
5. PM1 was stopped for "3 consecutive tool execution failures" - three runs of the failing test suite.
6. collect.py v2.6 found no tool result (they were user messages) - v2.7 does.
"""
import asyncio
import importlib.util
import json
import logging
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
XML_ERROR = "[Ollama Error]: XML syntax error on line 37: element <parameter> closed by </function>"
REPEAT_ERROR = "[Ollama Error]: prediction aborted, token repeat limit reached"


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


# =========================================================================================== 1. a file in the tests folder
def test_a_file_in_the_tests_folder_is_not_the_test_suite(tmp_path):
    from src.utils.test_runs import FAILED, clear_test_runs, is_test_run_task, record_test_run, verification_problem
    fix = ("Fix SyntaxError in tests/test_server.py: Fix SyntaxError in tests/test_server.py: remove repeated 'fromlist' "
           "keyword argument in __import__ call at line 383")
    assert not is_test_run_task(fix)
    assert not is_test_run_task("Fix the import in tests\\test_game.py")
    assert not is_test_run_task("Fix the helper in test/helpers.js")
    # the test suite named as such still is one
    assert is_test_run_task("Fix failing unit tests in tests/test_gameLogic.js")
    assert is_test_run_task("Kick-off Task 8: Run the server, test the game on mobile device, and verify all features work")
    assert is_test_run_task("Make the tests pass")
    assert is_test_run_task("Kick-off Task 9: Run all tests with npm test and perform end-to-end verification of the game")
    # a failing suite does not keep the one-file fix open; it keeps "make the tests pass" open
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_server.py").write_text("def test_x():\n    assert False\n", encoding="utf-8")
    clear_test_runs()
    record_test_run(tmp_path, "python -m pytest -q", FAILED, "5 failed, 16 passed")
    assert verification_problem(tmp_path, fix) is None
    assert verification_problem(tmp_path, "Make the tests pass").startswith("the tests do NOT pass")
    clear_test_runs()


# =========================================================================================== 2. unusable replies
class _FakeProvider:
    def __init__(self, events):
        self.events, self.calls = events, 0

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        self.calls += 1
        for event in self.events:
            yield event


def _core_worker(provider, monkeypatch):
    from src.agents.core import Agent
    import src.agents.core as core_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", True)

    async def no_workflow(*_args, **_kwargs):
        return None
    tools = {name: types.SimpleNamespace(auth_level="worker", get_json_schema=lambda n=name: {"name": n})
             for name in ("file_system", "code_editor")}
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools=tools),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow,
                                               resolve_state_alias=lambda t, s: s, is_valid_state=lambda t, s: True,
                                               _valid_states={}),
        cycle_handler=None)
    agent = Agent({"agent_id": "W3", "config": {"agent_type": "worker", "provider": "ollama", "model": "m"}}, provider, manager)
    agent.state = "worker_work"
    agent.ensure_sandbox_exists = lambda: True
    return agent


def _consume(agent):
    async def go():
        return [event async for event in agent.process_message()]
    with quiet():
        return run(go())


def test_malformed_reply_kinds():
    from src.agents.core import malformed_reply_kind
    assert malformed_reply_kind(XML_ERROR) == "tool_call"
    assert malformed_reply_kind(REPEAT_ERROR) == "repeat"
    assert malformed_reply_kind("[Ollama Error]: Max retries. Last: Status 524") is None
    assert malformed_reply_kind("Stream stalled - no data for 120.0s") is None
    assert malformed_reply_kind(None) is None


def test_a_reply_the_server_cannot_parse_is_retried_on_the_same_model(monkeypatch):
    from src.agents.core import UNUSABLE_REPLY_RETRY_CAP, excused_unusable_reply, unusable_reply_kind
    agent = _core_worker(_FakeProvider([{"type": "error", "content": XML_ERROR, "_exception_obj": ValueError(XML_ERROR)}]),
                         monkeypatch)
    events = _consume(agent)
    assert not [e for e in events if e.get("type") == "error"]                # no failover
    assert agent._last_reply_malformed["kind"] == "tool_call" and "XML syntax error" in agent._last_reply_malformed["detail"]
    assert unusable_reply_kind(agent) == "malformed" and excused_unusable_reply(agent)
    assert agent._consecutive_unusable_replies == 1
    # a normal reply resets it ...
    agent.llm_provider.events = [{"type": "response_chunk", "content": "Done."}, {"type": "finish_info", "done_reason": "stop",
                                                                                  "output_tokens": 3}]
    _consume(agent)
    assert agent._last_reply_malformed is None and agent._consecutive_unusable_replies == 0
    # ... and after UNUSABLE_REPLY_RETRY_CAP unusable replies in a row the error goes its old way (failover)
    agent.llm_provider.events = [{"type": "error", "content": XML_ERROR, "_exception_obj": ValueError(XML_ERROR)}]
    agent._consecutive_unusable_replies = UNUSABLE_REPLY_RETRY_CAP
    events = _consume(agent)
    assert [e for e in events if e.get("type") == "error"]


def test_a_repeating_reply_is_retried_without_thinking(monkeypatch):
    agent = _core_worker(_FakeProvider([{"type": "error", "content": REPEAT_ERROR}]), monkeypatch)
    events = _consume(agent)
    assert not [e for e in events if e.get("type") == "error"]
    assert agent._last_reply_malformed["kind"] == "repeat" and agent._think_off_next_call is True


def test_a_runaway_reply_is_discarded_and_retried(monkeypatch):
    agent = _core_worker(_FakeProvider([{"type": "response_chunk", "content": "x" * 33000}]), monkeypatch)
    events = _consume(agent)
    assert not [e for e in events if e.get("type") == "error"]
    assert agent._last_reply_malformed["kind"] == "runaway" and agent.text_buffer == ""


def test_the_malformed_reply_notice():
    from src.agents.cycle_handler import malformed_reply_notice
    agent = types.SimpleNamespace(_last_reply_malformed={"kind": "tool_call", "detail": XML_ERROR},
                                  _consecutive_malformed_replies=2)
    text = malformed_reply_notice(agent)
    assert text.startswith("[Framework Notice]: Your last reply was unusable (2 time(s) in a row), so NOTHING was executed")
    assert "could not read its tool call ([Ollama Error]: XML syntax error" in text and "ONE native tool call" in text
    agent._last_reply_malformed = {"kind": "repeat", "detail": REPEAT_ERROR}
    assert "repeated the same text over and over" in malformed_reply_notice(agent)


def _cycle_setup():
    from src.agents.constants import AGENT_STATUS_IDLE, AGENT_TYPE_WORKER
    from src.agents.core import Agent
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager
    manager = MagicMock(spec=AgentManager)
    manager.db_manager, manager.performance_tracker, manager.send_to_ui = AsyncMock(), AsyncMock(), AsyncMock()
    manager.schedule_cycle = AsyncMock(return_value="task")
    manager.current_project, manager.current_session, manager.current_session_db_id = None, "s", None
    manager._agent_cycle_locks, manager.agents = {}, {}
    manager.state_manager = MagicMock()
    manager.state_manager.get_agent_team.return_value = None
    manager.workflow_manager = MagicMock()
    manager.workflow_manager.get_system_prompt.return_value = "worker prompt"
    agent = MagicMock(spec=Agent)
    agent.agent_id, agent.agent_type, agent.state, agent.status = "W3", AGENT_TYPE_WORKER, "worker_work", AGENT_STATUS_IDLE
    agent.llm_provider, agent.provider_kwargs, agent.max_tokens = None, {}, None
    agent.provider_name, agent.model, agent.persona = "ollama-local", "qwen3.5:4b-q4_K_M", "Junior Coder"
    agent.message_history = [{"role": "system", "content": "worker prompt"}]
    agent._failover_state, agent._failed_models_this_cycle = {}, set()
    agent._last_reply_cut_off = agent._think_off_next_call = agent._context_window_full = False
    agent._last_reply_output_lost = False
    agent._empty_response_retry_count = 0
    agent.text_buffer = ""
    agent.needs_priority_recheck = False
    agent.active_task_id = None
    agent.set_state = MagicMock(side_effect=lambda new_state: setattr(agent, "state", new_state))
    return AgentCycleHandler(manager, MagicMock()), agent


def test_the_cycle_retries_a_malformed_reply_with_its_notice():
    from src.agents.cycle_handler import malformed_reply_notice
    handler, agent = _cycle_setup()

    async def events(*_args, **_kwargs):
        agent._last_reply_malformed = {"kind": "tool_call", "detail": XML_ERROR}
        agent._consecutive_unusable_replies = agent._consecutive_malformed_replies = 1
        agent._last_reply_thinking_chars, agent._last_reply_thinking_tail = 0, ""
        if False:
            yield {}
    agent.process_message = MagicMock(side_effect=events)
    with quiet():
        run(handler.run_cycle(agent, 0))
    notices = [m["content"] for m in agent.message_history if "Framework Notice" in str(m.get("content"))]
    assert notices and notices[-1] == malformed_reply_notice(agent)
    assert agent._empty_response_retry_count == 0 and agent.state == "worker_work" and agent.model == "qwen3.5:4b-q4_K_M"


# =========================================================================================== 3. the request limiter
def test_the_limiter_adapts_to_proxy_timeouts():
    from src.llm_providers.ollama_provider import LIMIT_RAISE_AFTER_CLEAN_REPLIES, OllamaRequestLimiter
    limiter = OllamaRequestLimiter("https://oli.example", 3)
    with quiet():
        limiter.proxy_timed_out(524)
        assert limiter.limit == 2
        limiter.proxy_timed_out(504)
        limiter.proxy_timed_out(524)
        assert limiter.limit == 1                                              # never below one
        for _ in range(LIMIT_RAISE_AFTER_CLEAN_REPLIES - 1):
            limiter.reply_started()
        assert limiter.limit == 1
        limiter.reply_started()
        assert limiter.limit == 2
        for _ in range(5 * LIMIT_RAISE_AFTER_CLEAN_REPLIES):
            limiter.reply_started()
        assert limiter.limit == 3                                              # never above the configured limit


def test_the_limiter_lets_only_limit_requests_in():
    from src.llm_providers.ollama_provider import OllamaRequestLimiter

    async def go():
        limiter = OllamaRequestLimiter("u", 1)
        order = []

        async def request(name, hold):
            async with limiter:
                order.append(f"{name} in")
                await asyncio.sleep(hold)
                order.append(f"{name} out")
        await asyncio.gather(request("a", 0.05), request("b", 0))
        return order
    assert run(go()) == ["a in", "a out", "b in", "b out"]


def _serve(statuses, payloads):
    from aiohttp import web

    async def chat(request):
        payloads.append(await request.json())
        status = statuses.pop(0) if statuses else 200
        if status != 200:
            return web.Response(status=status, text="<!DOCTYPE html><title>A timeout occurred</title>")
        body = {"model": "m", "done": True, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 2,
                "message": {"role": "assistant", "content": "ok"}}
        return web.Response(text=json.dumps(body) + "\n", content_type="application/x-ndjson")

    async def start():
        app = web.Application()
        app.router.add_post("/api/chat", chat)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    return start


def test_a_proxy_timeout_lowers_the_servers_limit(monkeypatch):
    import src.llm_providers.ollama_provider as ollama
    from src.config.settings import settings
    monkeypatch.setattr(ollama, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(settings, "OLLAMA_CONCURRENCY_LIMIT", 3, raising=False)
    monkeypatch.setattr(settings, "NATIVE_TOOL_CALLING_ENABLED", True)
    payloads = []
    registry = types.SimpleNamespace(get_model_info=lambda model: {"model_capabilities": ["completion", "tools"]})

    async def go():
        runner, url = await _serve([524], payloads)()
        try:
            provider = ollama.OllamaProvider(base_url=url, model_registry=registry)
            events = [e async for e in provider.stream_completion(
                messages=[{"role": "user", "content": "hi"}], model="qwen3.5:4b", temperature=0.7, max_tokens=64,
                tools=[{"type": "function", "function": {"name": "file_system"}}])]
            return events, ollama.get_ollama_semaphore(url)
        finally:
            await runner.cleanup()
    with quiet():
        events, limiter = run(go())
    assert len(payloads) == 2 and not [e for e in events if e.get("type") == "error"]
    assert limiter.limit == 2 and limiter.configured == 3 and limiter.active == 0


# =========================================================================================== 4. tool results keep their role
def test_ollama_chat_messages():
    from src.llm_providers.ollama_provider import ollama_chat_messages
    history = [{"role": "system", "content": "prompt"}, {"role": "user", "content": "go"},
               {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "file_system",
                                                                    "arguments": {"action": "read"}}]},
               {"role": "tool", "tool_call_id": "c1", "name": "file_system", "content": "1: x"},
               {"role": "system", "content": "Note without a tag"},
               {"role": "tool", "tool_call_id": "c9", "name": "code_editor", "content": "orphan"}]
    native = ollama_chat_messages(history, "qwen3.5:4b-q4_K_M", True)
    assert native[2] == {"role": "assistant", "content": "",
                         "tool_calls": [{"function": {"name": "file_system", "arguments": {"action": "read"}}}]}
    assert native[3] == {"role": "tool", "content": "1: x", "tool_name": "file_system"}
    assert native[4] == {"role": "user", "content": "[System Notification]:\nNote without a tag"}
    assert native[5] == {"role": "user", "content": "--- Tool Response (code_editor) ---\norphan\n-----------------------"}
    plain = ollama_chat_messages(history, "qwen3.5:4b-q4_K_M", False)
    assert plain[2] == {"role": "assistant", "content": ""}
    assert plain[3]["role"] == "user" and plain[3]["content"].startswith("--- Tool Response (file_system) ---")
    # a result without an id answers the pending call of its tool; a model outside the families keeps the tool role
    by_name = ollama_chat_messages(history[:3] + [{"role": "tool", "name": "file_system", "content": "1: x"}], "m", True)
    assert by_name[3] == {"role": "tool", "content": "1: x", "tool_name": "file_system"}
    assert ollama_chat_messages(history, "other-model", True)[5] == {"role": "tool", "content": "orphan"}


# =========================================================================================== 5. a command that ran
def test_a_command_that_ran_is_not_a_tool_failure():
    from src.agents.tool_activity import command_ran
    failing_tests = ("Command execution completed with return code 1. (Output was truncated due to length limits).\n"
                     "Command Execution Failed (Exit Code: 1)\n\n--- STDOUT ---\n.FFF..FF")
    assert command_ran("command_executor", failing_tests)
    assert command_ran("test_runner", "Test command failed with exit code 1.")
    assert not command_ran("command_executor", "Error: command timed out after 60 s")
    assert not command_ran("file_system", failing_tests)
    source = (ROOT / "src" / "agents" / "cycle_handler.py").read_text(encoding="utf-8")
    assert "if not any_tool_success and not commands_ran:" in source


# =========================================================================================== 6. collect.py v2.7
def _collect():
    spec = importlib.util.spec_from_file_location("te_collect_3w", ROOT / "collect.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_finds_tool_results_sent_as_user_messages():
    collect = _collect()
    prompt = ("- Your Agent ID: `W3`\n- Your Agent Type: `worker`\n"
              "- **Tools you can call in this state ('worker_work'):** code_editor, request_state.")
    messages = [
        {"role": "system", "content": prompt}, {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "command_executor", "arguments": {
            "action": "run_command", "command": "python -m pytest -q"}}}]},
        {"role": "user", "content": "--- Tool Response (command_executor) ---\n[Tool Execution Failed] Command not found: "
                                    "5 failed\n-----------------------"},
        {"role": "user", "content": "[Framework Notice]: something"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "file_system", "arguments": {
            "action": "read", "filename": "a.py"}}}]},
        {"role": "tool", "content": "1: x", "tool_name": "file_system"},
    ]
    guardian = [{"role": "system", "content": "--- Constitutional Guardian Agent ---\n[YOUR GOAL] review"},
                {"role": "user", "content": "text"}]
    header = ("2026-10-27 0{}:00:00,001 - src.llm_providers.ollama_provider - DEBUG - OllamaProvider 'qwen': EXACT JSON "
              "payload being sent to http://h/api/chat (before request):")
    log = "\n".join([header.format(1)] + json.dumps({"messages": messages}, indent=2).splitlines()
                    + [header.format(2)] + json.dumps({"messages": guardian}, indent=2).splitlines()
                    + ["2026-10-27 03:00:00,001 - src.agents.core - INFO - end"])
    data = collect.analyse_payloads(collect.parse_entries(log))
    assert data["counts"][("command_executor", "run_command")] == [1, 1]     # its result was found, and it failed
    assert data["counts"][("file_system", "read")] == [1, 0]
    texts = [entry["text"] for entry in data["injections"].values()]
    assert texts == ["[Framework Notice]: something"]                         # a tool result is no injected notice
    assert ("guardian", "review") in data["prompts"]
    report = collect.build_tool_calls_report(data)
    assert "5 failed" in report


def test_the_no_tools_text_names_the_state():
    from src.agents.tool_guidance import tool_use_instructions
    assert "No tools are offered in this state ('pm_startup')" in tool_use_instructions("pm", "pm_startup", [])

# END OF FILE tests/unit/test_round3w.py
