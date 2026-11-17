# START OF FILE tests/unit/test_round5h.py
"""
Round 5h: the known issues left after Round 5g, from the run of 2026-11-16 21:02 .. 2026-11-17 07:39
(WiFi_Snake_Multiplayer_Game, startup_1794834131, qwen3.5:4b, 49,152-token window).

1. 03:23:44 PM1 replied with its tool call as TEXT in qwen's own format (`<function=manage_team><parameter=action>
   request_state ...`, the opening <tool_call> missing): Ollama passed it on as text and nothing ran. (64 more such calls
   Ollama could not parse at all - "XML syntax error ... unexpected EOF" - and returned no text for; those are retried.)
2. src/server.js answered every request with "Server running" and never sent public/index.html; only the run task and the
   audit look for that, and the run never reached them.
3. W1 needed 136 context summaries in one task (W2 51): one read of tests/snake/snake.test.js (99 KB, ~28,000 tokens) passed
   the summary threshold (24,576 tokens) by itself, and the next read after a summary did again.
4. Five replies thought 69,686-79,614 characters until a 16,384-token budget ran out: the budget had been doubled after
   cut-offs, and a cut-off of pure thinking doubled it again.
5. 147 work calls were refused in worker_report / worker_wait - mostly after a worker's OWN request to report, when the
   finish of its task was refused and it went on fixing. The refusal named request_state; the models did not call it.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Multiplayer_Game", "startup_1794834131"
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="Taskwarrior (task + tasklib) missing")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def _workspace(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


# ======================================================================================== 1. a qwen tool call written as text
PM1_REPLY = ("<function=manage_team>\n<parameter=action>\nrequest_state\n</parameter>\n<parameter=state>\npm_standby\n"
             "</parameter>\n</function>\n</tool_call>")


def test_the_qwen_call_format_in_text_is_read():
    from src.llm_providers.tool_call_format import function_tag_tool_calls
    calls, rest = function_tag_tool_calls(PM1_REPLY, ["manage_team", "project_management"])
    assert [c["function"] for c in calls] == [{"name": "manage_team",
                                               "arguments": {"action": "request_state", "state": "pm_standby"}}]
    assert rest == ""
    # wrapped, two calls, a JSON value, a missing </parameter>, text around them
    text = ("I will read it.\n<tool_call>\n<function=file_system>\n<parameter=action>\nread\n</parameter>\n"
            "<parameter=filename>\nsrc/a.js\n</function>\n</tool_call>\n<tool_call>\n<function=code_editor>\n"
            "<parameter=chunks>\n[{\"search\": \"a < b\", \"replace\": \"a <= b\"}]\n</parameter>\n</function>\n</tool_call>")
    calls, rest = function_tag_tool_calls(text, ["file_system", "code_editor"])
    assert [c["function"]["arguments"] for c in calls] == [
        {"action": "read", "filename": "src/a.js"}, {"chunks": [{"search": "a < b", "replace": "a <= b"}]}]
    assert rest == "I will read it."
    # a value keeps its inner lines and indentation; only one line break at each end goes
    code = "<function=code_editor>\n<parameter=code>\nfunction f() {\n    return 1;\n}\n</parameter>\n</function>"
    assert function_tag_tool_calls(code, ["code_editor"])[0][0]["function"]["arguments"]["code"] == \
        "function f() {\n    return 1;\n}"
    # an unknown tool name, or no call at all: the reply stays text
    assert function_tag_tool_calls("<function=rm_rf>\n</function>", ["file_system"]) == ([], "<function=rm_rf>\n</function>")
    assert function_tag_tool_calls("a plain answer", ["file_system"]) == ([], "a plain answer")


def test_a_state_request_inside_any_tool():
    from src.agents.core import misplaced_state_request
    assert misplaced_state_request("manage_team", {"action": "request_state", "state": "pm_standby"})
    assert not misplaced_state_request("manage_team", {"action": "list_agents"})
    assert not misplaced_state_request("request_state", {"action": "request_state", "state": "x"})


class _FakeProvider:
    def __init__(self, events):
        self.events, self.max_tokens_seen, self.kwargs_seen = events, [], []

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        self.max_tokens_seen.append(max_tokens)
        self.kwargs_seen.append(kwargs)
        for event in self.events:
            yield event


def _core_agent(provider, monkeypatch, agent_type="worker", state="worker_work", tools=None):
    from src.agents.core import Agent
    import src.agents.core as core_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", tools is not None)
    monkeypatch.setattr(core_module.settings, "WORKER_WORK_STATE_MAX_TOKENS", 4096)

    async def no_workflow(*_args, **_kwargs):
        return None
    tool_objects = {name: types.SimpleNamespace(auth_level="worker", get_json_schema=lambda n=name: {"name": n})
                    for name in (tools or [])}
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools=tool_objects),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow,
                                               resolve_state_alias=lambda t, s: s, is_valid_state=lambda t, s: True,
                                               _valid_states={}),
        cycle_handler=None)
    agent = Agent({"agent_id": "PM1" if agent_type == "pm" else "W1",
                   "config": {"agent_type": agent_type, "provider": "ollama", "model": "m"}}, provider, manager)
    agent.state = state
    agent.ensure_sandbox_exists = lambda: True
    return agent


def _consume(agent):
    async def go():
        return [event async for event in agent.process_message()]
    return run(go())


def test_the_text_call_runs_as_a_tool_call(monkeypatch):
    provider = _FakeProvider([{"type": "response_chunk", "content": PM1_REPLY.replace("manage_team", "file_system").replace(
        "request_state", "read").replace("<parameter=state>\npm_standby", "<parameter=filename>\nsrc/server.js")},
        {"type": "finish_info", "done_reason": "stop", "output_tokens": 60}])
    agent = _core_agent(provider, monkeypatch, tools=["file_system", "manage_team"])
    requests = [e for e in _consume(agent) if e.get("type") == "tool_requests"]
    assert requests and requests[0]["calls"][0]["name"] == "file_system"
    assert requests[0]["calls"][0]["arguments"] == {"action": "read", "filename": "src/server.js"}


# ======================================================================================== 2. a page no server sends
INDEX_HTML = '<!DOCTYPE html>\n<html><body><canvas></canvas><script src="client.js"></script></body></html>\n'
CLIENT_JS = "const ws = new WebSocket('ws://' + location.host);\nws.onmessage = (e) => console.log(e.data);\n"
SERVER_TEXT_JS = """const http = require('http');
const WebSocket = require('ws');
const server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('Server running');
});
const wss = new WebSocket.Server({ server });
if (require.main === module) {
    server.listen(3000);
}
"""
SERVER_PAGE_JS = SERVER_TEXT_JS.replace(
    "    res.writeHead(200, { 'Content-Type': 'text/plain' });\n    res.end('Server running');\n",
    "    const file = path.join(__dirname, '..', 'public', req.url === '/' ? 'index.html' : req.url);\n"
    "    fs.readFile(file, (err, data) => res.end(data));\n").replace(
    "const http = require('http');\n", "const http = require('http');\nconst fs = require('fs');\nconst path = require('path');\n")


def test_a_page_no_server_sends_is_named_when_its_files_are_saved(tmp_path):
    from src.tools.project_links_check import link_notes_text
    root = _workspace(tmp_path, {"public/index.html": INDEX_HTML, "public/client.js": CLIENT_JS,
                                 "src/server.js": SERVER_TEXT_JS, "src/snake.js": "function move() {}\n"})
    for rel in ("src/server.js", "public/index.html", "public/client.js"):
        note = link_notes_text(root, rel, (root / rel).read_text())
        assert "[Framework note - the file WAS saved:]" in note, rel
        assert ("`public/index.html` needs a server (it uses a WebSocket), but NO server file of the project sends this "
                "page (server code is in: src/server.js)") in note, rel
    assert "needs a server" not in link_notes_text(root, "src/snake.js", "function move() {}\n")
    # the server sends the page now: no note
    _workspace(root, {"src/server.js": SERVER_PAGE_JS})
    for rel in ("src/server.js", "public/index.html", "public/client.js"):
        assert "needs a server" not in link_notes_text(root, rel, (root / rel).read_text()), rel


# ======================================================================================== 3. a read that fills the window
def _big_test_file(blocks: int = 60) -> str:
    parts = ["const { describe, it } = require('node:test');", "const assert = require('node:assert');", "",
             "describe('Client-side Game Rendering and Input Handling', () => {"]
    for number in range(blocks):
        parts += [f"    describe('group {number}', () => {{",
                  f"        it('checks value {number}', () => {{"]
        parts += [f"            assert.strictEqual({number} + {line}, {number + line});" for line in range(8)]
        parts += ["        });", "    });", ""]
    parts += ["});", ""]
    return "\n".join(parts)


def test_a_long_read_comes_in_parts_with_the_outline(tmp_path):
    from src.tools.file_system import FileSystemTool
    text = _big_test_file()
    _workspace(tmp_path, {"tests/snake.test.js": text})
    total = text.count("\n")

    def read(**kwargs):
        with quiet():
            return run(FileSystemTool().execute(agent_id="W1", agent_sandbox_path=tmp_path, action="read",
                                                filename="tests/snake.test.js", scope="private", **kwargs))
    from src.config.settings import settings
    limit = settings.FILE_READ_MAX_CHARS
    assert limit == 24000
    first = read(show_line_numbers=False)
    assert first["status"] == "success"
    content = first["content"]
    shown_until = int(content.split("These are lines 1-")[1].split(" ")[0])
    assert len(content) < limit + 2500 and 1 < shown_until < total
    assert (f"[Framework note: 'tests/snake.test.js' is too long to read at once (lines 1-{total} are {len(text):,} "
            f"characters; one read shows at most {limit:,}). These are lines 1-{shown_until} of {total}. Read the next part "
            f"with start_line={shown_until + 1}, or ONE function, class or test block") in content
    assert "Its top-level definitions: Client-side Game Rendering and Input Handling (lines 4-" in content
    assert "group 0 (lines 5-16)" in content and "checks value 0" not in content.split("Its top-level definitions:")[1]
    assert content.startswith(text[:200])
    # the next part from there; a short range is read whole, as before
    second = read(start_line=shown_until + 1, show_line_numbers=False)
    assert second["content"].split("\n")[0] == text.split("\n")[shown_until]
    assert read(start_line=5, end_line=16, show_line_numbers=False)["content"] == "\n".join(text.split("\n")[4:16]) + "\n"
    # with line numbers (the default) the numbered lines count
    numbered = read()["content"]
    assert numbered.startswith("1: const { describe, it }") and "These are lines 1-" in numbered


def test_the_read_limit_can_be_switched_off(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.file_system import FileSystemTool, bounded_read
    monkeypatch.setattr(settings, "FILE_READ_MAX_CHARS", 0)
    text = _big_test_file()
    _workspace(tmp_path, {"tests/snake.test.js": text})
    with quiet():
        result = run(FileSystemTool().execute(agent_id="W1", agent_sandbox_path=tmp_path, action="read",
                                              filename="tests/snake.test.js", scope="private", show_line_numbers=False))
    assert result["content"] == text
    assert bounded_read(["a\n", "b\n"], 1, 2, "x.js", "a\nb\n", limit=100) is None
    assert bounded_read(["aaaa\n", "bbbb\n", "cccc\n"], 7, 20, "x.txt", "", limit=8) == (
        "aaaa\n[Framework note: 'x.txt' is too long to read at once (lines 7-9 are 15 characters; one read shows at most 8). "
        "These are lines 7-7 of 20. Read the next part with start_line=8, or ONE function, class or test block with "
        "'function' (its name or title).]")


# ======================================================================================== 4. a cut-off that only thought
def test_a_cut_off_that_only_thought_gets_no_larger_budget(monkeypatch):
    thought = [{"type": "finish_info", "done_reason": "length", "output_tokens": 4096, "thinking_chars": 18159,
                "prompt_tokens": 24233, "num_ctx": 49152, "num_predict": 4096}]
    provider = _FakeProvider(thought)
    agent = _core_agent(provider, monkeypatch)
    _consume(agent)
    assert agent._last_reply_cut_off is True and agent._last_cut_off_was_thinking is True
    assert agent._consecutive_cut_off_replies == 0 and agent._think_off_next_call is True
    from src.agents.cycle_handler import cut_off_reply_notice
    agent._last_cut_off_max_tokens = 4096
    assert cut_off_reply_notice(agent).startswith(
        "[Framework Notice]: Your last reply THOUGHT until it reached 4096 tokens and never made its tool call")
    _consume(agent)
    assert provider.max_tokens_seen == [4096, 4096]
    # a reply cut off while it WROTE a tool call still gets the larger budget (Round 2n)
    provider.events = [{"type": "finish_info", "done_reason": "length", "output_tokens": 4096, "thinking_chars": 300,
                        "prompt_tokens": 11095, "num_ctx": 49152, "num_predict": 4096}]
    _consume(agent)
    assert agent._last_cut_off_was_thinking is False and agent._consecutive_cut_off_replies == 1
    _consume(agent)
    assert provider.max_tokens_seen[-1] == 8192


# ======================================================================================== 5. back to work from an own report
@pytest.fixture
def tasks(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    from src.config.settings import settings
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    (tmp_path / "projects" / PROJECT / SESSION / "shared_workspace").mkdir(parents=True)

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=None, **tool_args))
    return call


class _Workflow:
    def __init__(self):
        self.changes = []

    def change_state(self, agent, state, task_description=None):
        self.changes.append(state)
        agent.state = state
        agent._report_by_own_request = False
        return True

    def resolve_state_alias(self, agent_type, state):
        return state


@needs_tw
def test_a_worker_in_its_own_report_goes_back_to_work_for_its_open_task(tasks):
    from src.tools.executor import MAX_RESUMES_PER_TASK, ToolExecutor
    added = tasks("framework", action="add_task", description="Kick-off Task 3: Build client-side game rendering in "
                  "public/client.js", project_filter=PROJECT, assignee_agent_id="W2")
    task_id = added["task_uuid"]
    workflow = _Workflow()
    registry = {"file_system": types.SimpleNamespace(auth_level="worker"),
                "manage_team": types.SimpleNamespace(auth_level="pm"),
                "send_message": types.SimpleNamespace(auth_level="worker")}
    manager = types.SimpleNamespace(workflow_manager=workflow, tool_executor=types.SimpleNamespace(tools=registry))
    agent = types.SimpleNamespace(agent_id="W2", agent_type="worker", state="worker_report", current_task_id=task_id,
                                  active_task_id=task_id, _report_by_own_request=True)
    resume = ToolExecutor._resume_work_from_own_report
    args = {"action": "read", "filename": "public/client.js"}
    with quiet():
        note = resume(agent, "file_system", args, PROJECT, SESSION, manager)
    assert note.startswith(f"[Framework note: you were in worker_report, but your task {task_id} is not finished, so you "
                           "were moved back to worker_work and this call ran.")
    assert agent.state == "worker_work" and workflow.changes == ["worker_work"]
    # a report the framework FORCED (no mark), a report-only tool, a finished task: the call stays refused
    agent.state, agent._report_by_own_request = "worker_report", False
    with quiet():
        assert resume(agent, "file_system", args, PROJECT, SESSION, manager) == ""
    agent._report_by_own_request = True
    with quiet():
        assert resume(agent, "manage_team", {"action": "create_agent"}, PROJECT, SESSION, manager) == ""
        assert resume(agent, "file_system", args, PROJECT, SESSION, types.SimpleNamespace(workflow_manager=workflow)) == ""
    assert tasks("framework", action="modify_task", task_id=task_id, task_progress="finished")["status"] == "success"
    with quiet():
        assert resume(agent, "file_system", args, PROJECT, SESSION, manager) == ""
    # at most MAX_RESUMES_PER_TASK times per task
    other = tasks("framework", action="add_task", description="Fix public/client.js exports", project_filter=PROJECT,
                  assignee_agent_id="W2")["task_uuid"]
    agent.current_task_id = agent.active_task_id = other
    for _ in range(MAX_RESUMES_PER_TASK):
        agent.state, agent._report_by_own_request = "worker_report", True
        with quiet():
            assert resume(agent, "file_system", args, PROJECT, SESSION, manager)
    agent.state, agent._report_by_own_request = "worker_report", True
    with quiet():
        assert resume(agent, "file_system", args, PROJECT, SESSION, manager) == ""


def test_only_the_workers_own_report_request_is_marked():
    from src.agents.cycle_handler import mark_own_report
    manager = types.SimpleNamespace(workflow_manager=_Workflow())
    worker = types.SimpleNamespace(agent_type="worker", state="worker_report", _report_by_own_request=False)
    mark_own_report(manager, worker, "worker_report")
    assert worker._report_by_own_request is True
    pm = types.SimpleNamespace(agent_type="pm", state="pm_standby", _report_by_own_request=False)
    mark_own_report(manager, pm, "pm_standby")
    assert pm._report_by_own_request is False
    waiting = types.SimpleNamespace(agent_type="worker", state="worker_wait", _report_by_own_request=False)
    mark_own_report(manager, waiting, "worker_wait")
    assert waiting._report_by_own_request is False


def test_a_state_change_clears_the_mark():
    from src.agents.workflow_manager import AgentWorkflowManager
    source = Path(AgentWorkflowManager.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).resolve().parents[2] / source).read_text(encoding="utf-8")
    assert "agent._report_by_own_request = False  # Round 5h" in text

# END OF FILE tests/unit/test_round5h.py
