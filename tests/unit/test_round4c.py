# START OF FILE tests/unit/test_round4c.py
"""
Round 4c: the run of 2026-10-28 19:18-19:27 (granite4.2:8b-q4_K_M, OLLAMA_THINK=off, Ollama behind a Cloudflare proxy).
The Admin AI never wrote the plan.

* 19:19:24-19:20:50 admin_ai in 'planning' called tool_information twice, made the directory snake_game/ and wrote
  snake_game/index.html into DefaultProject - building the game instead of writing the plan.
* 19:20:50-19:27:26 its next reply (a longer file) got "524" three times, 125 s each: Ollama sends a reply that is ONE tool
  call only when the call is complete (the 498-token write before it got its first byte after 52 s, with the end). With
  thinking on, the thinking streams first; with OLLAMA_THINK=off nothing reached the proxy before its limit, and every retry
  of the same request was the same.
"""
import asyncio
import json
import logging
import types
from contextlib import contextmanager

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "DefaultProject", "startup_1793186306"


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


# ======================================================================================== 1. planning writes a plan
def test_the_admin_changes_nothing_in_planning():
    from src.agents.tool_activity import state_tool_block_message
    refused = [("file_system", {"action": "mkdir", "scope": "shared", "path": "snake_game"}),                     # 19:19:57
               ("file_system", {"action": "write", "filename": "snake_game/index.html", "content": "<!DOCTYPE html>"}),
               ("file_system", {"action": "create_directory", "path": "src"}),
               ("file_system", {"action": "git_init"}),
               ("code_editor", {"action": "replace_chunks", "filename": "a.py"}),
               ("command_executor", {"action": "run_command", "command": "echo x > a.txt"}),
               ("tool_information", {"action": "get_info", "tool_name": "project_management"}),                  # 19:19:38
               ("project_management", {"action": "add_task", "description": "x"})]
    for tool, args in refused:
        message = state_tool_block_message("admin", "planning", tool, args)
        assert message and message.startswith("In 'planning' your reply IS the plan, so "), (tool, args)
        assert message.endswith("Write the plan NOW as your reply: Markdown starting with '**Objective:**', the numbered "
                                "tasks, and at the end the ```json block with the 'title'.")
    assert "this 'mkdir' with file_system was NOT done" in state_tool_block_message("admin", "planning", *refused[0])
    allowed = [("file_system", {"action": "read", "filename": "README.md"}), ("file_system", {"action": "list", "path": "."}),
               ("file_system", {"action": "tree"}), ("knowledge_base", {"action": "search_knowledge", "query": "snake"}),
               ("web_search", {"query": "canvas snake"}), ("zim_search", {"query": "snake"}),
               ("codebase_search", {"query": "x"}), ("mark_message_read", {"message_id": "m1"}),
               ("request_state", {"state": "admin_conversation"})]
    for tool, args in allowed:
        assert state_tool_block_message("admin", "planning", tool, args) is None, (tool, args)
    # other admin states keep their rules (the Round 3s project check is the executor's)
    assert state_tool_block_message("admin", "admin_work", *refused[1]) is None
    assert state_tool_block_message("admin", "admin_conversation", *refused[0]) is None


def test_planning_offers_only_look_ups_and_reads():
    from src.agents.tool_guidance import offered_tool_names
    from src.agents.tool_activity import ADMIN_PLANNING_TOOLS
    names = ["asset_search", "code_editor", "codebase_search", "command_executor", "file_system", "github_tool",
             "knowledge_base", "manage_team", "mark_message_read", "project_management", "send_message", "system_help",
             "test_runner", "tool_information", "web_search", "zim_search"]
    tools = {name: types.SimpleNamespace(auth_level="admin") for name in names}
    offered = offered_tool_names("admin", "planning", tools)
    assert offered == ["codebase_search", "file_system", "knowledge_base", "mark_message_read", "web_search", "zim_search"]
    assert set(offered) == ADMIN_PLANNING_TOOLS
    assert offered_tool_names("admin", "admin_conversation", tools) == names


def test_the_executor_refuses_the_admins_mkdir_in_planning(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.executor import ToolExecutor
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    workspace.mkdir(parents=True)
    admin = types.SimpleNamespace(agent_id="admin_ai", agent_type="admin", state="planning", status="processing",
                                  message_history=[])
    manager = types.SimpleNamespace(agents={"admin_ai": admin}, current_project=PROJECT, current_session=SESSION)
    for args in ({"action": "mkdir", "scope": "shared", "path": "snake_game"},
                 {"action": "write", "scope": "shared", "filename": "snake_game/index.html", "content": "<html></html>"}):
        with quiet():
            answer = run(ToolExecutor().execute_tool("admin_ai", workspace.parent, "file_system", args,
                                                     project_name=PROJECT, session_name=SESSION, manager=manager))
        assert "In 'planning' your reply IS the plan" in str(answer), answer
    assert list(workspace.iterdir()) == []
    with quiet():
        listed = run(ToolExecutor().execute_tool("admin_ai", workspace.parent, "file_system", {"action": "list"},
                                                 project_name=PROJECT, session_name=SESSION, manager=manager))
    assert "IS the plan" not in str(listed)


# ======================================================================================== 2. OLLAMA_THINK=off behind a proxy
async def _serve(statuses, payloads):
    from aiohttp import web

    async def chat(request):
        payloads.append(await request.json())
        status = statuses.pop(0) if statuses else 200
        if status != 200:
            return web.Response(status=status, text="<!DOCTYPE html><title>A timeout occurred</title>")
        body = {"model": "m", "done": True, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 2,
                "message": {"role": "assistant", "content": "ok"}}
        return web.Response(text=json.dumps(body) + "\n", content_type="application/x-ndjson")
    app = web.Application()
    app.router.add_post("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


TOOLS = [{"type": "function", "function": {"name": "file_system"}}]


def _requests(monkeypatch, think_mode, statuses, calls):
    """Runs the calls (stream_completion kwargs) against one fake server; returns (payloads, events per call, limiter)."""
    import src.llm_providers.ollama_provider as ollama
    from src.config.settings import settings
    monkeypatch.setattr(ollama, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(settings, "OLLAMA_CONCURRENCY_LIMIT", 3, raising=False)
    monkeypatch.setattr(settings, "NATIVE_TOOL_CALLING_ENABLED", True)
    monkeypatch.setattr(settings, "OLLAMA_THINK", think_mode, raising=False)
    payloads = []
    registry = types.SimpleNamespace(get_model_info=lambda model: {"model_capabilities": ["completion", "tools"]})

    async def go():
        runner, url = await _serve(list(statuses), payloads)
        try:
            provider = ollama.OllamaProvider(base_url=url, model_registry=registry)
            results = []
            for kwargs in calls:
                results.append([e async for e in provider.stream_completion(
                    messages=[{"role": "user", "content": "write snake_game/game.js"}], model="granite4.2:8b-q4_K_M",
                    temperature=0.1, max_tokens=64, **kwargs)])
            return results, ollama.get_ollama_semaphore(url), url
        finally:
            await runner.cleanup()
    with quiet():
        results, limiter, url = run(go())
    return payloads, results, limiter, url


def test_a_proxy_timeout_without_thinking_retries_with_thinking(monkeypatch):
    from src.llm_providers.ollama_provider import thinking_keeps_reply_alive
    payloads, results, limiter, url = _requests(monkeypatch, "off", [524], [
        {"tools": TOOLS},            # 19:20:50: 524, then the retry thinks
        {"tools": TOOLS},            # later requests with tools to that server think from the start
        {}])                         # a request without tools stays without thinking
    assert [p.get("think") for p in payloads] == [False, True, True, False]
    assert not [e for events in results for e in events if e.get("type") == "error"]
    assert limiter.limit == 3                                                  # the cause is known: not the server's queue
    assert thinking_keeps_reply_alive(url, "granite4.2:8b-q4_K_M")
    assert not thinking_keeps_reply_alive(url, "qwen3.5:4b-q4_K_M")


def test_other_proxy_timeouts_are_handled_as_before(monkeypatch):
    # thinking left to the model (auto): the Round 3w rule - fewer requests at once, the same request again
    payloads, results, limiter, _url = _requests(monkeypatch, "auto", [524], [{"tools": TOOLS}])
    assert ["think" in p for p in payloads] == [False, False] and limiter.limit == 2
    # a call that switches thinking off itself (the Guardian's verdict) keeps it off
    payloads, results, limiter, _url = _requests(monkeypatch, "off", [524], [{"tools": TOOLS, "think": False}])
    assert [p.get("think") for p in payloads] == [False, False] and limiter.limit == 2
    # without tools the reply streams its text: no thinking is added
    payloads, results, limiter, _url = _requests(monkeypatch, "off", [524], [{}])
    assert [p.get("think") for p in payloads] == [False, False] and limiter.limit == 2

# END OF FILE tests/unit/test_round4c.py
