# START OF FILE tests/unit/test_round3a.py
"""
Round 3a: the 2026-10-22 16:22-16:24 run with OpenRouter (nvidia/nemotron-3.5-lightning:free for Admin AI and the
Constitutional Guardian).

The OpenRouter provider never sent the tool schemas ("OpenRouterProvider received tools/tool_choice arguments, but they
will be ignored"), although every prompt tells the agents to use native tool calls. Asked to start a project, Admin AI
replied with the text '{"name": "request_state", "arguments": {"state": "planning"}}' (16:23:35): it was reviewed,
shown to the user as a reply, and the project never started.

* The OpenRouter provider sends the tools, converts the history to the OpenAI format, joins streamed tool calls into
  'native_tool_calls' and reports 'finish_info' (cut-off and lost-output detection work as with Ollama). A model that
  OpenRouter cannot use with tools is retried - and from then on sent - without them.
* The OpenAI (and vLLM) provider converts the history too, and passes tool-call arguments that are not valid JSON on as
  text instead of dropping the call.
* A reply that is ONLY a tool call written as JSON text runs as that call (any provider).
"""
import asyncio
import json
import logging
import types

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

ADMIN_16_23_35 = '{"name": "request_state", "arguments": {"state": "planning"}}'


def run(coro):
    return asyncio.run(coro)


# ======================================================================================== 1. the history in OpenAI format
HISTORY = [
    {"role": "system", "content": "You are PM1."},
    {"role": "user", "content": "assign Kick-off Task 1"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_563qwm5l", "name": "project_management",
         "arguments": {"action": "modify_task", "task_id": "c32b8c0e", "assignee_agent_id": "W1"}}]},
    {"role": "tool", "tool_call_id": "call_563qwm5l", "name": "project_management", "content": "Task modified."},
    {"role": "system", "content": "[Framework Notification]: Worker 'W1' has been activated."},
]


def test_the_history_becomes_valid_openai_messages():
    from src.llm_providers.tool_call_format import CONTINUE_NOTE, openai_chat_messages
    converted = openai_chat_messages(HISTORY)
    assert converted[2] == {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_563qwm5l", "type": "function", "function": {
            "name": "project_management",
            "arguments": '{"action": "modify_task", "task_id": "c32b8c0e", "assignee_agent_id": "W1"}'}}]}
    assert converted[3] == {"role": "tool", "tool_call_id": "call_563qwm5l", "content": "Task modified."}
    assert converted[4]["role"] == "system" and len(converted) == 5
    # a history that ends with the agent's own reply gets a note, so a new reply starts
    ended = openai_chat_messages(HISTORY[:2] + [{"role": "assistant", "content": "I will assign it now."}])
    assert ended[-1] == {"role": "user", "content": CONTINUE_NOTE}


def test_a_condensed_history_stays_valid():
    from src.llm_providers.tool_call_format import openai_chat_messages
    # the call's result was condensed away: the call is left out; the empty assistant message with it
    unanswered = openai_chat_messages([{"role": "user", "content": "go"},
                                       {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "x"}]},
                                       {"role": "user", "content": "next"}])
    assert unanswered == [{"role": "user", "content": "go"}, {"role": "user", "content": "next"}]
    # a result whose call is gone becomes a user message
    orphan = openai_chat_messages([{"role": "user", "content": "go"},
                                   {"role": "tool", "tool_call_id": "c9", "name": "file_system", "content": "1: x"}])
    assert orphan[1] == {"role": "user", "content": "--- Tool Response (file_system) ---\n1: x\n-----------------------"}
    # two calls, one answered: only the answered one is kept, text stays
    partial = openai_chat_messages([
        {"role": "assistant", "content": "Reading both.", "tool_calls": [{"id": "a", "name": "file_system"},
                                                                         {"id": "b", "name": "file_system"}]},
        {"role": "tool", "tool_call_id": "a", "content": "A"}, {"role": "user", "content": "ok"}])
    assert [c["id"] for c in partial[0]["tool_calls"]] == ["a"] and partial[0]["content"] == "Reading both."
    # already OpenAI-shaped calls and non-text content are accepted
    shaped = openai_chat_messages([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "z", "type": "function", "function": {"name": "send_message", "arguments": '{"a": 1}'}}]},
        {"role": "tool", "tool_call_id": "z", "content": {"status": "success"}}, {"role": "user", "content": "x"}])
    assert shaped[0]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'
    assert shaped[1]["content"] == '{"status": "success"}'


# ======================================================================================== 2. streamed calls and text calls
def test_streamed_tool_call_pieces_are_joined():
    from src.llm_providers.tool_call_format import add_tool_call_delta, finished_tool_calls
    pieces = {}
    add_tool_call_delta(pieces, {"index": 0, "id": "call_1", "function": {"name": "request_state", "arguments": '{"sta'}})
    add_tool_call_delta(pieces, {"index": 0, "function": {"arguments": 'te": "planning"}'}})
    add_tool_call_delta(pieces, {"index": 1, "id": "call_2", "function": {"name": "send_message", "arguments": '{"x": '}})
    calls = finished_tool_calls(pieces)
    assert calls[0] == {"id": "call_1", "type": "function", "function": {"name": "request_state",
                                                                         "arguments": {"state": "planning"}}}
    assert calls[1]["function"]["arguments"] == '{"x": '        # not valid JSON: passed on as text, not dropped


@pytest.mark.parametrize("text, expected", [
    (ADMIN_16_23_35, [("request_state", {"state": "planning"})]),
    ("```json\n" + ADMIN_16_23_35 + "\n```", [("request_state", {"state": "planning"})]),
    ('[{"name": "file_system", "parameters": {"action": "read", "path": "a.py"}}]', [("file_system", {"action": "read", "path": "a.py"})]),
    ('{"name": "unknown_tool", "arguments": {}}', []),                                  # unknown name
    ('{"name": "request_state", "arguments": {"state": "planning"}, "note": "x"}', []),  # not only a call
    ('I will call {"name": "request_state", "arguments": {}} now.', []),                # text around it
    ('{"roles": ["Coder"], "tasks": []}', []),                                           # a kick-off plan
    ("", []), (None, []),
])
def test_tool_calls_written_as_json_text(text, expected):
    from src.llm_providers.tool_call_format import json_text_tool_calls
    calls = json_text_tool_calls(text, ["request_state", "file_system", "send_message"])
    assert [(c["function"]["name"], c["function"]["arguments"]) for c in calls] == expected


def _core_admin(provider, monkeypatch):
    from src.agents.core import Agent
    import src.agents.core as core_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", False)

    async def no_workflow(*_args, **_kwargs):
        return None
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools={"file_system": object(), "send_message": object()}),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow,
                                               resolve_state_alias=lambda t, s: s, is_valid_state=lambda t, s: True,
                                               _valid_states={}),
        cycle_handler=None)
    agent = Agent({"agent_id": "admin_ai", "config": {"agent_type": "admin", "provider": "openrouter",
                                                      "model": "nvidia/nemotron-3.5-lightning:free"}}, provider, manager)
    agent.state = "startup"
    agent.ensure_sandbox_exists = lambda: True
    return agent


class _FakeProvider:
    def __init__(self, events):
        self.events = events

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        for event in self.events:
            yield event


def test_admins_json_text_reply_changes_the_state(monkeypatch):
    agent = _core_admin(_FakeProvider([{"type": "response_chunk", "content": ADMIN_16_23_35}]), monkeypatch)

    async def go():
        return [event async for event in agent.process_message()]
    logging.disable(logging.CRITICAL)
    try:
        events = run(go())
    finally:
        logging.disable(logging.NOTSET)
    changes = [e for e in events if e.get("type") == "agent_state_change_requested"]
    assert changes and changes[0]["requested_state"] == "planning"
    assert not [e for e in events if e.get("type") == "final_response"]       # not shown to the user as a reply


# ======================================================================================== 3. the OpenRouter provider
def _sse(chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _chunk(delta=None, finish=None, usage=None):
    chunk = {"id": "gen-1", "object": "chat.completion.chunk", "created": 1, "model": "m",
             "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage is not None:
        chunk["usage"] = usage
    return chunk


SCHEMA = [{"type": "function", "function": {"name": "request_state", "description": "Change state.",
                                            "parameters": {"type": "object", "properties": {"state": {"type": "string"}},
                                                           "required": ["state"]}}}]


def _openrouter(responses, monkeypatch, tools=SCHEMA, model="nvidia/nemotron-3.5-lightning:free", history=None,
                native=True, **kw):
    """Runs one stream_completion against a local OpenAI-compatible server; returns (events, request bodies)."""
    from aiohttp import web
    from src.config.settings import settings
    from src.llm_providers import openrouter_provider
    monkeypatch.setattr(settings, "NATIVE_TOOL_CALLING_ENABLED", native)
    monkeypatch.setattr(openrouter_provider, "_NO_TOOL_MODELS", set())
    monkeypatch.setattr(openrouter_provider, "RETRY_DELAY_SECONDS", 0)
    bodies, queue = [], list(responses)

    async def chat(request):
        bodies.append(await request.json())
        status, payload = queue.pop(0)
        if status != 200:
            return web.json_response(payload, status=status)
        return web.Response(text=payload, content_type="text/event-stream")

    async def go():
        app = web.Application()
        app.router.add_post("/v1/chat/completions", chat)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
        try:
            provider = openrouter_provider.OpenRouterProvider(api_key="sk-test", base_url=url)
            try:
                return [e async for e in provider.stream_completion(messages=history or HISTORY[:2], model=model,
                                                                     temperature=0.6, tools=tools, **kw)]
            finally:
                await provider._openai_client.close()
        finally:
            await runner.cleanup()
    logging.disable(logging.CRITICAL)
    try:
        return run(go()), bodies
    finally:
        logging.disable(logging.NOTSET)


def test_openrouter_sends_the_tools_and_returns_native_calls(monkeypatch):
    stream = _sse([
        _chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_a", "type": "function",
                                                     "function": {"name": "request_state", "arguments": '{"state"'}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": ': "planning"}'}}]}),
        _chunk({}, finish="tool_calls", usage={"prompt_tokens": 8015, "completion_tokens": 21, "total_tokens": 8036}),
    ])
    events, bodies = _openrouter([(200, stream)], monkeypatch, history=HISTORY)
    assert bodies[0]["tools"] == SCHEMA
    assert bodies[0]["messages"][2]["tool_calls"][0]["type"] == "function"            # history converted
    assert bodies[0]["messages"][3] == {"role": "tool", "tool_call_id": "call_563qwm5l", "content": "Task modified."}
    calls = [e for e in events if e["type"] == "native_tool_calls"]
    assert calls[0]["tool_calls"] == [{"id": "call_a", "type": "function",
                                      "function": {"name": "request_state", "arguments": {"state": "planning"}}}]
    finish = [e for e in events if e["type"] == "finish_info"][0]
    assert finish["done_reason"] == "stop" and finish["output_tokens"] == 21 and finish["prompt_tokens"] == 8015


def test_openrouter_reports_a_cut_off_and_reasoning(monkeypatch):
    stream = _sse([_chunk({"role": "assistant", "reasoning": "I need to plan the project first. "}),
                   _chunk({"content": "**Objective:** a snake"}),
                   _chunk({}, finish="length", usage={"prompt_tokens": 10, "completion_tokens": 4096,
                                                      "completion_tokens_details": {"reasoning_tokens": 9}})])
    events, _ = _openrouter([(200, stream)], monkeypatch, max_tokens=4096)
    finish = [e for e in events if e["type"] == "finish_info"][0]
    assert finish["done_reason"] == "length" and finish["num_predict"] == 4096
    assert finish["thinking_chars"] == len("I need to plan the project first. ") and finish["output_tokens"] == 4096
    assert [e["content"] for e in events if e["type"] == "response_chunk"] == ["**Objective:** a snake"]


def test_hidden_reasoning_tokens_are_not_lost_output():
    from src.llm_providers.ollama_provider import is_output_lost
    from src.llm_providers.openrouter_provider import openrouter_finish_info
    logging.disable(logging.CRITICAL)
    try:
        info = openrouter_finish_info("stop", {"completion_tokens": 900, "completion_tokens_details": {"reasoning_tokens": 880}},
                                      "", None, 12, 0)
    finally:
        logging.disable(logging.NOTSET)
    assert info["output_tokens"] == 20
    assert not is_output_lost(info["done_reason"], info["output_tokens"], info["thinking_chars"], 0, 0)
    assert openrouter_finish_info(None, None, "", None, 0, 0) is None


def test_a_model_without_tool_support_is_retried_without_tools(monkeypatch):
    from src.llm_providers import openrouter_provider
    refusal = {"error": {"code": 404, "message": "No endpoints found that support tool use. Try disabling \"tools\"."}}
    text = _sse([_chunk({"role": "assistant", "content": ADMIN_16_23_35}), _chunk({}, finish="stop")])
    events, bodies = _openrouter([(404, refusal), (200, text)], monkeypatch)
    assert "tools" in bodies[0] and "tools" not in bodies[1]
    assert "nvidia/nemotron-3.5-lightning:free" in openrouter_provider._NO_TOOL_MODELS
    assert [e["content"] for e in events if e["type"] == "response_chunk"] == [ADMIN_16_23_35]


def test_tool_support_refusals_are_recognised():
    from src.llm_providers.openrouter_provider import tool_support_refused
    error = types.SimpleNamespace(status_code=404, body={"message": "No endpoints found that support tool use."})
    assert tool_support_refused(error)
    assert not tool_support_refused(types.SimpleNamespace(status_code=429, body={"message": "tool use rate limited"}))
    assert not tool_support_refused(types.SimpleNamespace(status_code=400, body={"message": "invalid model"}))


def test_no_tools_when_native_tool_calling_is_off(monkeypatch):
    stream = _sse([_chunk({"role": "assistant", "content": "hi"}), _chunk({}, finish="stop")])
    events, bodies = _openrouter([(200, stream)], monkeypatch, native=False)
    assert "tools" not in bodies[0] and [e["content"] for e in events if e["type"] == "response_chunk"] == ["hi"]


# ======================================================================================== 4. the OpenAI provider
def test_openai_provider_converts_history_and_keeps_bad_arguments(monkeypatch):
    from aiohttp import web
    from src.config.settings import settings
    from src.llm_providers.openai_provider import OpenAIProvider
    monkeypatch.setattr(settings, "NATIVE_TOOL_CALLING_ENABLED", True)
    bodies = []
    stream = _sse([_chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                                                 "function": {"name": "request_state", "arguments": "{state: planning}"}}]}),
                   _chunk({}, finish="tool_calls")])

    async def chat(request):
        bodies.append(await request.json())
        return web.Response(text=stream, content_type="text/event-stream")

    async def go():
        app = web.Application()
        app.router.add_post("/v1/chat/completions", chat)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
        try:
            provider = OpenAIProvider(api_key="sk-test", base_url=url)
            try:
                return [e async for e in provider.stream_completion(messages=HISTORY, model="gpt-x", temperature=0.6,
                                                                     tools=SCHEMA)]
            finally:
                await provider._openai_client.close()
        finally:
            await runner.cleanup()
    logging.disable(logging.CRITICAL)
    try:
        events = run(go())
    finally:
        logging.disable(logging.NOTSET)
    assert bodies[0]["messages"][2]["tool_calls"][0]["function"]["name"] == "project_management"
    calls = [e for e in events if e["type"] == "native_tool_calls"]
    assert calls and calls[0]["tool_calls"][0]["function"]["arguments"] == "{state: planning}"   # was dropped silently

# END OF FILE tests/unit/test_round3a.py
