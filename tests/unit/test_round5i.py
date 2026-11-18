# START OF FILE tests/unit/test_round5i.py
"""
Round 5i: the run of 2026-11-17 18:58 .. 20:06 (WiFi_Snake_Multiplayer, startup_1794913125, qwen3.5:4b). Every task was
finished, 30 tests passed, the audit reported the project complete - and the user found nothing at http://0.0.0.0:8080.

1. server/server.py (socketserver.ThreadingTCPServer + a raw socket with `.listen(10)`) was no "server code" for the run
   check: the audit never started it. It crashed on EVERY start: it bound port 8080 twice.
2. "Address already in use" was always read as "another copy runs" - not judged; an old copy (W3's raw socket server of
   19:24) held the port, and its answer (JSON, not HTTP) counted as "nothing answered".
3. W3 started the server with ' &' four times; each crashed at once and nobody read the logs ("ps aux" six times instead).
4. admin_ai sent its messages to the user with send_message(target_agent_id='user'): "agent not found", four times. In
   admin_standby it read server/server.py for the user's "there is nothing there" - and its turn ended without an answer.
5. public/client.js opened `new WebSocket(...)` - no server accepted a WebSocket - at `ws://${window.location.host}:8080`.
6. pytest printed "1 failed, 29 passed in 0.36s" and ran on for 60 s, nine times: a test's non-daemon thread looped.
7. PM1 sent file_system read with function='render_leaderboard' and no filename three times.
"""
import asyncio
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

HAVE_PYTHON = shutil.which("python") is not None
needs_python = pytest.mark.skipif(not HAVE_PYTHON, reason="no `python` on PATH")
needs_proc = pytest.mark.skipif(not Path("/proc/net/tcp").exists(), reason="needs Linux /proc")


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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int, up: bool = True, seconds: float = 8.0) -> bool:
    from src.utils.program_run_check import port_open
    end = time.time() + seconds
    while time.time() < end:
        if port_open(port) == up:
            return True
        time.sleep(0.1)
    return False


PAGE = "<html><body><canvas></canvas><script src='client.js'></script></body></html>\n"

# the run's server/server.py, reduced: an HTTP server AND a raw socket server on the same port
SELF_BIND_SERVER = '''import socket
import socketserver
import threading
import http.server

PORT = {port}


def main():
    httpd = socketserver.ThreadingTCPServer(('127.0.0.1', PORT), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', PORT))
    server.listen(10)
    while True:
        server.accept()


if __name__ == '__main__':
    main()
'''

# W3's copy of 19:24: a raw socket server that writes JSON to whoever connects
RAW_JSON_SERVER = '''import json
import socket

PORT = {port}

if __name__ == '__main__':
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', PORT))
    server.listen(10)
    while True:
        client, _ = server.accept()
        client.sendall(json.dumps({{'type': 'welcome', 'data': {{'client_id': 'client_1'}}}}).encode() + b'\\n')
        client.close()
'''

PAGE_SERVER = '''import http.server
import os

PORT = {port}
PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'public')


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLIC, **kwargs)


if __name__ == '__main__':
    with http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler) as httpd:
        httpd.serve_forever()
'''


# ============================================================================== 1. a Python server is server code
def test_python_socket_and_socketserver_servers_are_server_code(tmp_path):
    from src.utils.program_run_check import is_server_source, server_entry, server_port
    assert is_server_source("server/server.py", SELF_BIND_SERVER.format(port=8080))
    assert is_server_source("s.py", "import socketserver\nsocketserver.ThreadingTCPServer(('', 1), H)\n")
    assert is_server_source("s.py", "t = threading.Thread(target=httpd.serve_forever)\n")  # referenced, not called
    assert is_server_source("s.py", "from http.server import ThreadingHTTPServer\nThreadingHTTPServer(('', 8000), H)\n")
    assert is_server_source("s.py", "import asyncio\nasyncio.start_server(handle, '0.0.0.0', 8765)\n")
    assert is_server_source("s.py", "import websockets\nwebsockets.serve(handler, '0.0.0.0', 8765)\n")
    assert is_server_source("s.py", "from aiohttp import web\nweb.run_app(app, port=8080)\n")
    assert is_server_source("s.py", "import socket\ns = socket.socket()\ns.bind(('', 9000))\ns.listen(5)\n")
    # a turtle game's screen.listen() reads keys: no server
    assert not is_server_source("game.py", "import turtle\nscreen = turtle.Screen()\nscreen.listen()\n")
    assert not is_server_source("tool.py", "print('hello')\n")
    ws = _workspace(tmp_path, {"server/server.py": SELF_BIND_SERVER.format(port=8080), "public/index.html": PAGE,
                               "docs/README.md": "Start it: `python server/server.py`\n"})
    assert server_entry(ws) == ("python server/server.py", "server/server.py")


def test_the_port_of_a_python_server(tmp_path):
    from src.utils.program_run_check import server_port
    ws = _workspace(tmp_path, {
        # the run's file: `.listen(10)` is a backlog, the port is a typed default and a keyword argument
        "a.py": "class Server:\n    def __init__(self, host: str = '0.0.0.0', port: int = 8080):\n        pass\n"
                "    def start(self):\n        s.listen(10)\n\ndef main():\n    Server(host='0.0.0.0', port=8080)\n",
        "b.py": "import socket\ns = socket.socket()\ns.bind(('0.0.0.0', 9123))\ns.listen(5)\n",
        "c.py": "from http.server import HTTPServer\nHTTPServer(('', 8000), Handler).serve_forever()\n",
        "d.py": "import os\nPORT = int(os.environ.get('PORT', 5050))\n",
        "e.js": "server.listen(3000);\n"})
    assert server_port(ws, "a.py") == 8080
    assert server_port(ws, "b.py") == 9123
    assert server_port(ws, "c.py") == 8000
    assert server_port(ws, "d.py") == 5050
    assert server_port(ws, "e.js") == 3000  # JavaScript unchanged


# ============================================================================== 2. the run check and the port
@needs_python
def test_a_program_that_binds_its_port_twice_is_named(tmp_path, monkeypatch):
    from src.utils import program_run_check
    monkeypatch.setattr(program_run_check, "RUN_CHECK_SECONDS", 4.0)
    monkeypatch.setattr(program_run_check, "HTTP_PROBE_DELAY_SECONDS", 1.0)
    port = _free_port()
    ws = _workspace(tmp_path, {"server/server.py": SELF_BIND_SERVER.format(port=port), "public/index.html": PAGE,
                               "public/client.js": "document.title = 'snake';\n"})
    with quiet():
        problem = run(program_run_check.program_run_problem(ws))
    assert problem.startswith(f"the program does not start: `python server/server.py` stopped with \"Address already in "
                              f"use\" although port {port} was FREE when it started - the program itself opens port {port} "
                              "TWICE")
    assert "OSError: [Errno 98] Address already in use" in problem and "server.bind(('127.0.0.1', PORT))" in problem
    assert "ONE server must listen" in problem and str(tmp_path) not in problem


@needs_python
@needs_proc
def test_a_copy_that_answers_no_http_is_named_and_not_stopped(tmp_path, monkeypatch):
    """A copy the framework did NOT start is never stopped; its answer - JSON, not HTTP - is a problem now."""
    from src.utils import program_run_check
    monkeypatch.setattr(program_run_check, "RUN_CHECK_SECONDS", 3.0)
    monkeypatch.setattr(program_run_check, "HTTP_PROBE_DELAY_SECONDS", 1.0)
    port = _free_port()
    ws = _workspace(tmp_path, {"server/server.py": RAW_JSON_SERVER.format(port=port), "public/index.html": PAGE})
    env = {k: v for k, v in os.environ.items() if k != "TE_SANDBOX"}
    copy = subprocess.Popen([sys.executable, "server/server.py"], cwd=ws, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _wait_for_port(port)
        probe = run(program_run_check._http_probe(port))
        assert probe[0] == 0 and probe[1] == "" and probe[2].startswith('{"type": "welcome"')
        with quiet():
            problem = run(program_run_check.program_run_problem(ws))
        assert problem.startswith(f"the server runs, but http://localhost:{port}/ does not answer HTTP - it sent "
                                  "'{\"type\": \"welcome\"")
        assert "A raw socket server that writes JSON lines cannot be opened in a browser." in problem
        assert copy.poll() is None  # not the framework's copy: still running
    finally:
        copy.terminate()
        copy.wait(timeout=10)


@needs_python
@needs_proc
def test_a_stale_copy_the_framework_started_is_stopped_and_the_current_code_checked(tmp_path, monkeypatch):
    from src.tools.sandbox_runner import group_alive, run_sandboxed
    from src.utils import program_run_check
    monkeypatch.setattr(program_run_check, "RUN_CHECK_SECONDS", 3.0)
    monkeypatch.setattr(program_run_check, "HTTP_PROBE_DELAY_SECONDS", 1.5)
    port = _free_port()
    ws = _workspace(tmp_path, {"server/server.py": RAW_JSON_SERVER.format(port=port), "public/index.html": PAGE})
    started = run(run_sandboxed("python server/server.py &", ws, 20, tool_name="test", agent_id="W3"))
    try:
        assert started.background_log and started.pid and _wait_for_port(port)
        owner = program_run_check.port_owner_pid(port, ws)
        assert owner and program_run_check.started_by_framework(owner)
        # not changed since it started: it is not stopped (its answer is judged)
        with quiet():
            problem = run(program_run_check.program_run_problem(ws))
        assert "does not answer HTTP" in problem and group_alive(started.pid)
        # the server file is rewritten after the copy started: the copy runs OLD code - stopped, the new code is checked
        (ws / "server" / "server.py").write_text(PAGE_SERVER.format(port=port), encoding="utf-8")
        later = time.time() + 5
        os.utime(ws / "server" / "server.py", (later, later))
        with quiet():
            assert run(program_run_check.program_run_problem(ws)) is None
        assert not group_alive(started.pid)
    finally:
        try:
            os.killpg(started.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, TypeError):
            pass


def test_only_the_frameworks_shells_are_marked():
    from src.tools.sandbox_runner import SANDBOX_MARKER_VAR, build_sandbox_env
    assert build_sandbox_env(home_dir=None, base_env={"PATH": "/bin"})[SANDBOX_MARKER_VAR] == "1"
    from src.utils.program_run_check import started_by_framework
    assert not started_by_framework(os.getpid()) or os.environ.get("TE_SANDBOX") == "1"


def test_a_port_held_by_another_program_still_judges_nothing(tmp_path):
    """Round 4d's rule stays: a port another program holds before the start is not the project's problem."""
    from src.utils.program_run_check import program_run_problem
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        ws = _workspace(tmp_path, {"server.py": RAW_JSON_SERVER.format(port=port), "index.html": PAGE})
        if HAVE_PYTHON:
            with quiet():
                assert run(program_run_problem(ws)) is None


# ============================================================================== 3. a background start, a moment later
CRASHING = "import sys\nprint('starting', flush=True)\nraise SystemExit('Error: config.json not found')\n"


@needs_python
def test_the_background_start_note(tmp_path):
    from src.tools.command_hints import background_start_note
    from src.tools.sandbox_runner import run_sandboxed
    port = _free_port()
    ws = _workspace(tmp_path, {"twice.py": SELF_BIND_SERVER.format(port=port), "ok.py": RAW_JSON_SERVER.format(port=port),
                               "quiet.py": "x = 1\n", "crash.py": "import os\nopen('missing.json')\n"})

    async def start(command):
        result = await run_sandboxed(command, ws, 20, tool_name="test", agent_id="W3")
        return result, await background_start_note(ws, result.background_log, result.pid, command, wait=1.5)

    result, note = run(start("python twice.py &"))
    assert note.startswith("[Framework check 1.5 s after the start:] the background process has already ENDED - it is "
                           f"NOT running. Its output ({result.background_log}):")
    assert "OSError: [Errno 98] Address already in use" in note
    assert f"Nothing holds port {port} now, so the program itself opened port {port} TWICE" in note
    result, note = run(start("python crash.py &"))
    assert "has already ENDED" in note and "FileNotFoundError" in note and "Fix that error and start it again." in note
    result, note = run(start("python quiet.py &"))
    assert "has already ENDED, without any output - it does NOT keep running" in note
    running, note = run(start("python ok.py &"))
    try:
        assert note.startswith("[Framework check 1.5 s after the start:] the background process is still RUNNING.")
        if Path("/proc/net/tcp").exists():
            assert _wait_for_port(port)
            _result, note = run(start("python twice.py &"))
            assert f"Port {port} is held by a copy of THIS project's program that still runs (pid " in note
            assert "`kill " in note
    finally:
        os.killpg(running.pid, signal.SIGKILL)


@needs_python
def test_command_executor_reports_the_background_check(tmp_path, monkeypatch):
    from src.tools import command_hints
    from src.tools.command_executor import CommandExecutionTool
    monkeypatch.setattr(command_hints, "BACKGROUND_PEEK_SECONDS", 1.0)
    _workspace(tmp_path, {"crash.py": "open('missing.json')\n"})
    with quiet():
        result = run(CommandExecutionTool().execute(agent_id="W3", agent_sandbox_path=tmp_path, action="run_command",
                                                     command="python crash.py &"))
    assert result["status"] == "success" and "Command started in the background" in result["message"]
    assert "[Framework check 1.0 s after the start:] the background process has already ENDED" in result["content"]


def test_group_alive_and_the_shell_pid():
    from src.tools.sandbox_runner import group_alive, run_sandboxed
    assert not group_alive(None) and not group_alive(0)
    result = run(run_sandboxed("true", Path("."), 10, tool_name="test", agent_id="t"))
    assert isinstance(result.pid, int) and not group_alive(result.pid)


# ============================================================================== 4. the Admin AI and the user
class FakeAgent:
    def __init__(self, agent_id="admin_ai", agent_type="admin", state="admin_standby"):
        self.agent_id, self.agent_type, self.state = agent_id, agent_type, state
        self.status, self.current_tool_info, self.message_history = "processing", None, []

    def set_status(self, status, tool_info=None):
        self.status, self.current_tool_info = status, tool_info


class FakeManager:
    def __init__(self, guardian=False, verdict="<OK/>"):
        from src.agents.constants import CONSTITUTIONAL_GUARDIAN_AGENT_ID
        self.ui, self.routed, self.reviewed, self.concerns, self.ok_noted = [], [], [], [], []
        self.agents = {"admin_ai": FakeAgent(), "PM1": FakeAgent("PM1", "pm", "pm_manage")}
        self.current_session_db_id = None
        self.tool_executor = object()  # send_message never reaches it
        self.cg_escalation = None
        self.cycle_handler = None
        if guardian:
            self.agents[CONSTITUTIONAL_GUARDIAN_AGENT_ID] = FakeAgent(CONSTITUTIONAL_GUARDIAN_AGENT_ID, "worker", "x")
            manager = self

            async def get_verdict(agent, text, review_context=None):
                manager.reviewed.append((agent.agent_id, text, review_context))
                return verdict

            async def raise_concern(agent, kind, text, verdict_text, target_id=None, event_data=None):
                manager.concerns.append((kind, text, event_data))
                return types.SimpleNamespace(concern_id="cg_1", concern="GP003 tone")

            async def note_outgoing_ok(agent):
                manager.ok_noted.append(agent.agent_id)
            self.cycle_handler = types.SimpleNamespace(_get_cg_verdict=get_verdict)
            self.cg_escalation = types.SimpleNamespace(raise_concern=raise_concern, note_outgoing_ok=note_outgoing_ok)

    async def send_to_ui(self, payload):
        self.ui.append(payload)


def _handler(manager):
    from src.agents.interaction_handler import AgentInteractionHandler
    handler = AgentInteractionHandler(manager)

    async def route(sender_id, target_identifier, message_content):
        manager.routed.append((sender_id, target_identifier, message_content))
        return {"status": "success", "message": "routed"}, None
    handler.route_and_activate_agent_message = route
    return handler


URL_ANSWER = "Game URL: http://localhost:8080 - open it in a browser on any device of the same WiFi."


@pytest.mark.parametrize("target", ["user", "User", "human", "the user"])
def test_the_admins_message_to_the_user_reaches_the_main_chat(target):
    manager = FakeManager()
    with quiet():
        result = run(_handler(manager).execute_single_tool(manager.agents["admin_ai"], "call_1", "send_message",
                                                           {"target_agent_id": target, "message_content": URL_ANSWER},
                                                           "WiFi_Snake_Multiplayer", "s"))
    assert manager.ui == [{"type": "final_response", "agent_id": "admin_ai", "content": URL_ANSWER}]
    assert manager.routed == []
    assert result["content"].startswith("[Delivered to the user]: your message is shown to the user in the main chat.")
    assert result["_raw_result"]["delivered_to_user"] is True
    from src.agents.tool_activity import is_tool_result_failure
    assert not is_tool_result_failure("send_message", result["content"])


def test_a_message_to_the_user_is_reviewed_as_a_reply():
    manager = FakeManager(guardian=True)
    with quiet():
        run(_handler(manager).execute_single_tool(manager.agents["admin_ai"], "c", "send_message",
                                                  {"target_agent_id": "user", "message_content": URL_ANSWER}, "P", "s"))
    assert manager.reviewed[0][2].endswith("This is the author's reply to the human user.")
    assert manager.ok_noted == ["admin_ai"] and len(manager.ui) == 1
    held = FakeManager(guardian=True, verdict="<CONCERN>GP003</CONCERN>")
    with quiet():
        result = run(_handler(held).execute_single_tool(held.agents["admin_ai"], "c", "send_message",
                                                        {"target_agent_id": "user", "message_content": URL_ANSWER}, "P", "s"))
    assert held.ui == [] and held.concerns[0][0] == "reply" and held.concerns[0][2]["type"] == "final_response"
    assert "was HELD and NOT shown" in result["content"] and result["_raw_result"]["status"] == "held"


def test_other_senders_and_agent_targets_are_routed_as_before():
    manager = FakeManager()
    with quiet():
        run(_handler(manager).execute_single_tool(manager.agents["PM1"], "c", "send_message",
                                                  {"target_agent_id": "user", "message_content": "hi"}, "P", "s"))
        run(_handler(manager).execute_single_tool(manager.agents["admin_ai"], "c", "send_message",
                                                  {"target_agent_id": "PM1", "message_content": "status?"}, "P", "s"))
    assert manager.routed == [("PM1", "user", "hi"), ("admin_ai", "PM1", "status?")] and manager.ui == []


def test_user_delivery_helpers_and_the_silent_standby_rule():
    from src.agents.tool_activity import (ADMIN_SILENT_STANDBY_NOTE, combined_state_change_refusal, delivered_to_user,
                                          is_user_target, only_user_reply)
    assert is_user_target("user") and is_user_target(" Human ") and not is_user_target("PM1") and not is_user_target(None)
    delivered = {"role": "tool", "name": "send_message", "content": "[Delivered to the user]: your message is shown."}
    read = {"role": "tool", "name": "mark_message_read", "content": "Message 'm1' marked as read."}
    file_read = {"role": "tool", "name": "file_system", "content": "1: import socket"}
    assert delivered_to_user([read, delivered]) and not delivered_to_user([read])
    assert only_user_reply([read, delivered]) and not only_user_reply([delivered, file_read]) and not only_user_reply([read])
    # Round 4k's rule: a request for admin_standby without text is refused - unless the user got a message
    assert combined_state_change_refusal("admin_standby", [read], "admin", "") == ADMIN_SILENT_STANDBY_NOTE
    assert combined_state_change_refusal("admin_standby", [read, delivered], "admin", "") is None


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


class SchedulerManager:
    def __init__(self):
        self.scheduled, self.ui = [], []
        self.workflow_manager = types.SimpleNamespace(change_state=lambda *a, **k: True)

    async def schedule_cycle(self, agent, retry_count):
        self.scheduled.append((agent.agent_id, retry_count))

    async def send_to_ui(self, payload):
        self.ui.append(payload)


def _schedule(state, results):
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    agent = FakeAgent(state=state)
    agent.message_history = [{"role": "user", "content": "I've tried `http://0.0.0.0:8080` but there is nothing there."}]
    manager = SchedulerManager()
    with quiet():
        run(NextStepScheduler(manager).schedule_next_step(Ctx(agent, executed_tool_successfully_this_cycle=True,
                                                              all_tool_results=results)))
    return manager.scheduled


@pytest.mark.parametrize("state", ["admin_standby", "admin_conversation"])
def test_the_admin_processes_a_tool_result_in_standby_and_conversation(state):
    file_read = {"role": "tool", "name": "file_system", "content": "1: import socket"}
    assert _schedule(state, [file_read]) == [("admin_ai", 0)]
    delivered = {"role": "tool", "name": "send_message", "content": "[Delivered to the user]: your message is shown."}
    read = {"role": "tool", "name": "mark_message_read", "content": "Message 'm1' marked as read."}
    assert _schedule(state, [read, delivered]) == []  # that turn WAS the answer


# ============================================================================== 5. the page, its WebSocket and its port
CLIENT_JS = ("function connectToServer() {\n    const wsUrl = `ws://${window.location.host}:8080`;\n"
             "    const ws = new WebSocket(wsUrl);\n}\n")
RUN_SERVER = '''import socket
import http.server
# serves '/', '/index.html', '/client.js'
class Server:
    def start(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(('0.0.0.0', 8080))
        server.listen(10)
'''


def test_a_page_whose_websocket_no_server_accepts(tmp_path):
    from src.utils.product_check import page_server_problems, product_problems
    ws = _workspace(tmp_path, {"server/server.py": RUN_SERVER, "public/index.html": PAGE, "public/client.js": CLIENT_JS})
    problems = page_server_problems(ws)
    assert len(problems) == 1
    assert problems[0].startswith("`public/index.html` opens a WebSocket (`new WebSocket(...)` in the page or its script), "
                                  "but NO server file of the project ACCEPTS WebSocket connections (server code is in: "
                                  "server/server.py).")
    assert "fetch() requests to HTTP routes" in problems[0]
    assert any("ACCEPTS WebSocket" in p for p in product_problems(ws))
    for accepting in ("import websockets\n", "from flask_sock import Sock\n", "ACCEPT = 'Sec-WebSocket-Accept'\n",
                      "from aiohttp import web\nws = web.WebSocketResponse()\n"):
        (ws / "server" / "server.py").write_text(RUN_SERVER + accepting, encoding="utf-8")
        assert page_server_problems(ws) == [], accepting
    js = _workspace(tmp_path / "js", {"src/server.js": "const http = require('http');\nconst server = http.createServer(h);\n"
                                                       "server.listen(3000); // index.html\n",
                                      "public/index.html": PAGE, "public/client.js": CLIENT_JS})
    assert "ACCEPTS WebSocket" in page_server_problems(js)[0]
    (js / "src" / "server.js").write_text("const http = require('http');\nconst WebSocket = require('ws');\nconst server = "
                                          "http.createServer(h);\nnew WebSocket.Server({ server });\nserver.listen(3000); "
                                          "// index.html\n", encoding="utf-8")
    assert page_server_problems(js) == []


def test_a_port_added_to_location_host(tmp_path):
    from src.utils.product_check import browser_script_problems, host_port_problems
    ws = _workspace(tmp_path, {"public/index.html": PAGE, "public/client.js": CLIENT_JS})
    problems = browser_script_problems(ws)
    assert len(problems) == 1 and problems[0].startswith(
        "`public/client.js` (loaded by `public/index.html`) adds a port to `location.host` (line 2: `const wsUrl = "
        "`ws://${window.location.host}:8080`;`)")
    assert "'ws://localhost:8080:8080'" in problems[0] and "location.hostname" in problems[0]
    assert host_port_problems("c.js", "p.html", "const u = 'ws://' + location.host + ':8080';\n")
    for fine in ("const u = `ws://${location.host}`;\n", "const u = `ws://${location.hostname}:8080`;\n",
                 "// const u = `ws://${location.host}:8080`;\n"):
        assert host_port_problems("c.js", "p.html", fine) == [], fine


# ============================================================================== 6. pytest ended, its process did not
HUNG_OUTPUT = ("FAILED tests/test_server.py::TestClient::test_client_read - assert None is no...\n"
               "========================= 1 failed, 29 passed in 0.36s =========================")
THREAD_TEST = '''import threading


class TestHandleClient:
    def test_handle_client_disconnect(self, mock_server, mock_socket):
        client = Client("client_1", mock_socket)
        thread = threading.Thread(target=mock_server.handle_client, args=(client,))
        thread.start()
        thread.join(timeout=1)

    def test_daemon(self, server):
        threading.Thread(target=server.run, daemon=True).start()
        t = threading.Thread(target=server.run)
        t.daemon = True
        t.start()
'''


def test_a_pytest_run_that_printed_its_summary_names_the_non_daemon_thread(tmp_path):
    from src.tools.project_links_check import non_daemon_threads, pytest_ended_note, test_hang_hint
    ws = _workspace(tmp_path, {"tests/test_server.py": THREAD_TEST})
    assert non_daemon_threads(THREAD_TEST) == [
        (7, "thread = threading.Thread(target=mock_server.handle_client, args=(client,))", "mock_server.handle_client")]
    hint = test_hang_hint(ws, "python -m pytest tests/test_server.py -v", 1.01, HUNG_OUTPUT)
    assert hint.startswith("\n[Framework hint - why the TEST run did not end:] pytest printed its summary ('1 failed, 29 "
                           "passed in 0.36s'), so every test had ENDED.")
    assert ("- tests/test_server.py line 7: `thread = threading.Thread(target=mock_server.handle_client, args=(client,))` - "
            "its target `mock_server.handle_client` must RETURN") in hint
    assert "the code that runs right AFTER that line" not in hint  # the busy-loop text is wrong after the summary
    # without a thread in the tests: the other keepers of a Python process
    other = _workspace(tmp_path / "o", {"tests/test_x.py": "def test_x():\n    assert True\n"})
    assert "a child process (subprocess / multiprocessing) or a server" in pytest_ended_note(other, "pytest -q", HUNG_OUTPUT)
    # no summary, or no Python test run: nothing new
    assert pytest_ended_note(ws, "python -m pytest -q", "tests/test_server.py ....") == ""
    assert pytest_ended_note(ws, "node --test tests/a.test.js", "# pass 3 ... 1 passed in 0.3s") == ""
    assert "the code that runs right AFTER that line" in test_hang_hint(ws, "python -m pytest -q", 1.01, "....")


# ============================================================================== 7. read a function without its file
def test_reading_a_function_without_its_file(tmp_path):
    from src.tools.file_system import FileSystemTool, function_without_filename_result
    ws = _workspace(tmp_path, {
        "server/server.py": "class ServerState:\n    def get_leaderboard(self):\n        return []\n",
        "public/client.js": "function displayLeaderboard(players) {\n  return players;\n}\n",
        "tests/test_server.py": "def test_get_leaderboard():\n    assert True\n"})
    assert function_without_filename_result(ws, "get_leaderboard") == ("server/server.py", None)
    assert function_without_filename_result(ws, "displayLeaderboard") == ("public/client.js", None)
    _none, refusal = function_without_filename_result(ws, "render_leaderboard")
    assert refusal["message"].startswith("'filename' parameter is required for 'read', and NO file of the workspace "
                                         "defines 'render_leaderboard'. Close names:")
    assert "'displayLeaderboard' in 'public/client.js'" in refusal["message"]
    with quiet():
        result = run(FileSystemTool().execute(agent_id="PM1", agent_sandbox_path=ws, action="read",
                                              function="displayLeaderboard"))
    assert result["status"] == "success" and "function displayLeaderboard(players)" in result["content"]


def test_the_audit_prompt_says_where_the_note_is():
    text = Path(__file__).resolve().parents[2].joinpath("prompts.yaml").read_text(encoding="utf-8")
    assert "it is a message in your history, NOT a file: never search files, logs or the knowledge base for it." in text

# END OF FILE tests/unit/test_round5i.py
