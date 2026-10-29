# START OF FILE tests/unit/test_round4d.py
"""
Round 4d: the run of 2026-10-28 20:03 - 2026-10-29 09:10 (qwen3.5:4b-q4_K_M, WiFi_Snake_Game). All 9 kick-off tasks and two
fixes were finished by 23:38 - then nothing happened for 9.5 hours, the user was never told, and the game did not run.

* 23:36:18 PM1 went to pm_standby (W1 still fixed a test); 23:38:11 W1 went to worker_wait without a message. The standby
  watchdog wakes a PM for a NEW waiting worker, but W1 counted as seen since the wake of 21:03 (it had worked twice since),
  and only pm_manage moved a PM with a finished project to pm_audit. (_check_pm_completion_status used PM_STATE_AUDIT,
  which manager.py never imported.)
* "Kick-off Task 9: Run the server and test on multiple devices ..." was finished on passing tests (they mocked the
  server): src/server.js defined startServers() and nothing called it - `node src/server.js` ended at once. Called, it
  stopped on `startGame is not defined`; running, it answered GET / with JSON instead of the page.
* src/index/index.html loaded "game.js" and "auth.js" from src/index/ - the files were src/game.js and src/auth.js.
* docs/README.md said `npm install`, `npm start`, `npm test`; there was no package.json (the user's npm: ENOENT).
* the kick-off made 'Node.js' a planned file ("Implement Node.js HTTP server ...") and kept src/index/index.html next to the
  task's src/index.html.
"""
import asyncio
import json
import logging
import os
import shutil
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Game", "startup_1793188989"
HAVE_NODE = shutil.which("node") is not None
needs_node = pytest.mark.skipif(not HAVE_NODE, reason="node is not installed")
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def short_run_checks(monkeypatch):
    """A running server is seen after 3 s here (6 s in a run); the page is asked for after 1 s."""
    from src.utils import program_run_check
    monkeypatch.setattr(program_run_check, "RUN_CHECK_SECONDS", 3.0)
    monkeypatch.setattr(program_run_check, "HTTP_PROBE_DELAY_SECONDS", 1.0)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def _workspace(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Path(root)


# the shape of the run's src/server.js (shortened): the start function nobody calls
SERVER_JS = """const http = require('http');
const { getState, resetGame } = require('./game');
const PORT = 3000;

const server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ message: 'WiFi Snake Game Server' }));
});

const startGameAndServers = () => {
    startGame();
    server.listen(PORT, () => {
        console.log(`[Server] HTTP server running on port ${PORT}`);
    });
};

module.exports = { server, PORT };

// Start servers when module is required
const startServers = () => {
    console.log('[Server] Starting WiFi Snake Game server...');
    startGameAndServers();
};
"""
GAME_JS = "function getState() { return {}; }\nfunction resetGame() {}\nfunction startGame() {}\n" \
          "module.exports = { getState, resetGame, startGame };\n"
INDEX_HTML = """<!DOCTYPE html>
<html><head><link rel="stylesheet" href="styles.css"></head>
<body><canvas id="game"></canvas>
<script src="game.js"></script>
<script src="auth.js"></script>
<script src="index.js"></script>
<script src="https://cdn.example.com/lib.js"></script>
</body></html>
"""
README_MD = """# WiFi Snake Game

## Setup
```bash
npm install
```
## Run
```bash
npm start
```
Open `http://localhost:3000`. Run the tests with `npm test`.
"""
STRUCTURE_MD = """# Project Directory Structure

## Files To Create

These files are planned but do NOT exist yet:

- src/index/index.html
- src/game.js
- src/leaderboard.js
"""


def _snake(root, server=SERVER_JS):
    return _workspace(root, {"src/server.js": server, "src/game.js": GAME_JS, "src/auth.js": "// auth\n",
                             "src/index/index.html": INDEX_HTML, "src/index/index.js": "// ui\n",
                             "src/index/styles.css": "body {}\n", "docs/README.md": README_MD,
                             "PROJECT_STRUCTURE.md": STRUCTURE_MD})


# ======================================================================================== 1. the program run check
def test_run_tasks_are_recognized():
    from src.utils.program_run_check import is_program_run_task
    for text in ("Kick-off Task 9: Run the server and test on multiple devices to verify all features work correctly",
                 "Start the app and check it works", "Launch the game on the laptop", "Run the program and fix errors"):
        assert is_program_run_task(text), text
    for text in ("Implement Node.js HTTP server in src/server.js", "Write automated tests for server functionality",
                 "Run the tests with npm test", None):
        assert not is_program_run_task(text), text


def test_the_server_entry_and_its_port(tmp_path):
    from src.utils.program_run_check import server_entry, server_port
    ws = _snake(tmp_path)
    assert server_entry(ws) == ("node src/server.js", "src/server.js") and server_port(ws, "src/server.js") == 3000
    # the "start" script and a README command win; a project without server code has no entry
    _workspace(ws, {"package.json": json.dumps({"scripts": {"start": "node src/server.js"}})})
    assert server_entry(ws) == ("npm start", "src/server.js")
    assert server_entry(_workspace(tmp_path / "cli", {"src/main.js": "console.log(1);\n"})) is None
    assert server_port(_workspace(tmp_path / "p", {"a.js": "app.listen(process.env.PORT || 8080);\n"}), "a.js") == 8080
    assert server_port(_workspace(tmp_path / "q", {"a.js": "const PORT = process.env.PORT || 4000;\n"}), "a.js") == 4000


def test_the_start_function_nobody_calls_is_named():
    from src.utils.program_run_check import uncalled_start_functions
    assert uncalled_start_functions(SERVER_JS) == [("startServers", 20)]
    assert uncalled_start_functions(SERVER_JS + "startServers();\n") == []
    assert uncalled_start_functions(SERVER_JS + "module.exports.startServers = startServers;\n") == []
    assert uncalled_start_functions("function main() {\n  app.listen(3000);\n}\nif (require.main === module) main();\n") == []
    assert uncalled_start_functions("const x = 1;\n") == []


def _problem(ws):
    from src.utils.program_run_check import program_run_problem
    with quiet():
        return run(program_run_problem(ws))


@needs_node
def test_a_server_that_ends_at_once_is_found(tmp_path):
    problem = _problem(_snake(tmp_path))
    assert problem.startswith("the program does not keep running: `node src/server.js` ended at once (exit code 0, no "
                              "output), so the server never listens and nobody can open it.")
    assert ("src/server.js defines `startServers()` (line 20), which starts the server, but nothing calls it. Call it at the "
            "end of the file: `if (require.main === module) { startServers(); }`") in problem


@needs_node
def test_a_server_that_crashes_shows_its_error(tmp_path):
    problem = _problem(_snake(tmp_path, SERVER_JS + "startServers();\n"))
    assert problem.startswith("the program does not start: `node src/server.js` stopped with an error (exit code 1):\n"
                              "src/server.js:11\n")
    assert "ReferenceError: startGame is not defined" in problem and "at startGameAndServers (src/server.js:11" in problem
    assert str(tmp_path) not in problem


def _free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@needs_node
def test_a_running_server_must_send_the_page(tmp_path):
    port = _free_port()
    json_server = (f"const http = require('http');\nconst PORT = {port};\n"
                   "http.createServer((req, res) => { res.writeHead(200, {'Content-Type': 'application/json'}); "
                   "res.end(JSON.stringify({ message: 'WiFi Snake Game Server' })); }).listen(PORT);\n")
    problem = _problem(_snake(tmp_path, json_server))
    assert problem.startswith(f"the server runs, but http://localhost:{port}/ answers 200 (application/json: "
                              "'{\"message\":\"WiFi Snake Game Server\"}') instead of the page src/index/index.html")
    page_server = json_server.replace("'application/json'", "'text/html'").replace(
        "JSON.stringify({ message: 'WiFi Snake Game Server' })", "'<html></html>'")
    assert _problem(_snake(tmp_path / "ok", page_server)) is None


@needs_node
def test_a_port_in_use_or_a_missing_node_judges_nothing(tmp_path, monkeypatch):
    import socket
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        server = f"const http = require('http');\nhttp.createServer(() => {{}}).listen({port}, '127.0.0.1');\n"
        assert _problem(_snake(tmp_path, server)) is None                       # EADDRINUSE: another copy runs
    monkeypatch.setenv("PATH", str(tmp_path / "no_bin"))
    assert _problem(_snake(tmp_path / "b")) is None                             # node not installed: exit code 127


def test_a_project_without_server_code_is_not_judged(tmp_path):
    assert _problem(_workspace(tmp_path, {"src/cli.js": "console.log('hi');\n"})) is None


# ======================================================================================== 2. the README and the page
def test_readme_commands_that_cannot_work(tmp_path):
    from src.utils.program_run_check import readme_command_problems
    ws = _snake(tmp_path)
    assert readme_command_problems(ws) == [
        "docs/README.md tells the user to run `npm install`, `npm start`, `npm test`, but the project has NO package.json, "
        "so npm fails (\"ENOENT: no such file or directory, open 'package.json'\"). Write the commands that work in this "
        "project (`node src/server.js`) - e.g. `node --test tests/*.test.js` for the tests - or add a package.json with those "
        "scripts."]
    _workspace(ws, {"package.json": json.dumps({"scripts": {"test": "node --test tests/"}})})
    assert readme_command_problems(ws) == ["docs/README.md tells the user to run `npm start`, but package.json has no "
                                           "\"start\" script: add it, or write the command that works."]
    _workspace(ws, {"package.json": json.dumps({"scripts": {"test": "node --test", "start": "node src/server.js"}}),
                    "docs/README.md": "Run `node server.js`, then `node src/leaderboard.js`.\n"})
    assert readme_command_problems(ws) == ["docs/README.md tells the user to run `node server.js`, but there is no server.js "
                                           "(the project has src/server.js: `node src/server.js`)."]   # leaderboard: planned


def test_page_files_that_do_not_exist(tmp_path):
    from src.tools.project_links_check import html_reference_problems, link_problems, link_notes_text
    ws = _snake(tmp_path)
    problems = html_reference_problems(ws, "src/index/index.html")
    assert problems == [
        "src/index/index.html loads `game.js`, which is src/index/game.js - that file does NOT exist, so the page cannot "
        "load it. The project's game.js is src/game.js: use `../game.js`.",
        "src/index/index.html loads `auth.js`, which is src/index/auth.js - that file does NOT exist, so the page cannot "
        "load it. The project's auth.js is src/auth.js: use `../auth.js`."]
    assert link_problems(ws, ws / "src/index/index.html") == problems
    note = link_notes_text(ws, "src/index/index.html", INDEX_HTML)
    assert note.startswith("\n[Framework check - the file WAS saved, but it does not work with the project yet") and \
        problems[0] in note
    # planned files, absolute and remote references and a missing icon are fine
    fine = '<script src="../leaderboard.js"></script><script src="/app.js"></script><link rel="icon" href="x.png">'
    _workspace(ws, {"src/index/ok.html": fine})
    assert html_reference_problems(ws, "src/index/ok.html") == []
    _workspace(ws, {"src/index/bad.html": '<link rel="stylesheet" href="theme.css">'})
    assert html_reference_problems(ws, "src/index/bad.html") == [
        "src/index/bad.html loads `theme.css`, which is src/index/theme.css - that file does NOT exist, so the page cannot "
        "load it. Create that file, or point the reference at the file that exists."]


def test_the_task_check_reads_pages_and_readmes(tmp_path):
    from src.utils.task_files import task_completion_problems
    ws = _snake(tmp_path)
    page = task_completion_problems("Kick-off Task 2: Create HTML/CSS UI with player controls in src/index.html", ws,
                                    [str(ws / "src/index/index.html")])
    assert len(page) == 2 and all("does NOT exist" in p for p in page)
    readme = task_completion_problems("Kick-off Task 8: Write README.md with setup and usage instructions", ws,
                                      [str(ws / "docs/README.md")])
    assert len(readme) == 1 and "NO package.json" in readme[0]


# ======================================================================================== 3. finishing a run task
class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait", status="idle"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, status
        self.persona = agent_id
        self.message_history = []
        self.agent_config = {"config": {"project_name_context": PROJECT}}


def _manager(*agents):
    return types.SimpleNamespace(
        agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION,
        state_manager=types.SimpleNamespace(
            get_agent_team=lambda agent_id: f"team_{PROJECT}",
            get_agents_in_team=lambda team_id: [a for a in agents]))


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    from src.config.settings import settings
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    workspace.mkdir(parents=True)

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    call.workspace = workspace
    return call


def _add(call, manager, text, assignee=None):
    extra = {"assignee_agent_id": assignee} if assignee else {}
    result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **extra)
    assert result["status"] == "success", result
    return result["task_uuid"]


TASK_9 = "Kick-off Task 9: Run the server and test on multiple devices to verify all features work correctly"


@needs_tw
@needs_node
def test_a_run_task_is_not_finished_while_the_program_does_not_start(tasks):
    from src.utils.test_runs import PASSED, record_test_run
    pm1, w3 = Agent("PM1", "pm", "pm_report_check"), Agent("W3", state="worker_work", status="processing")
    manager = _manager(pm1, w3)
    ws = _snake(tasks.workspace)
    _workspace(ws, {"tests/test_server.js": "const assert = require('assert');\nassert.ok(true);\n"})
    t9 = _add(tasks, manager, TASK_9, "W3")
    record_test_run(ws, "node --test tests/test_server.js", PASSED, "12 passed")       # 23:34:54: the tests pass
    refused = tasks("W3", manager, action="modify_task", task_id=t9, task_progress="finished")
    assert refused["status"] == "error" and refused["error_type"] == "program_does_not_run", refused
    assert refused["message"].startswith(f"Task '{t9}' was NOT marked finished: the program does not keep running: "
                                         "`node src/server.js` ended at once")
    assert "nothing calls it" in refused["message"] and "NO package.json" in refused["message"]
    assert "Passing tests are not enough for this task" in refused["message"]
    # fixed: started when run directly, the README names the commands that work
    _workspace(ws, {"src/server.js": SERVER_JS.replace("    startGame();\n", "") .replace("const PORT = 3000;", "const PORT = 0;")
                    + "if (require.main === module) { startServers(); }\n",
                    "docs/README.md": "Run `node src/server.js` and open the address it prints.\n"})
    record_test_run(ws, "node --test tests/test_server.js", PASSED, "12 passed")
    done = tasks("W3", manager, action="modify_task", task_id=t9, task_progress="finished")
    assert done["status"] == "success", done
    # other tasks and the framework are not run-checked
    t4 = _add(tasks, manager, "Kick-off Task 4: Implement Node.js HTTP server in src/server.js", "W3")
    _workspace(ws, {"src/server.js": SERVER_JS})
    assert tasks("W3", manager, action="modify_task", task_id=t4, task_progress="finished")["status"] == "success"
    t10 = _add(tasks, manager, "Run the server again", "W3")
    assert tasks("framework", manager, action="modify_task", task_id=t10, task_progress="finished")["status"] == "success"


# ======================================================================================== 4. the finished project is audited
def test_a_worker_back_in_worker_wait_is_new_again():
    from src.agents.assignable_work import newly_waiting_workers
    pm1 = Agent("PM1", "pm", "pm_standby")
    pm1._seen_waiting_workers = {"W1", "W2", "W3"}                              # the wake of 21:03
    pm1._state_entered_at = 1000.0                                              # 23:36:18 standby
    w1, w2 = Agent("W1"), Agent("W2")
    w1._state_entered_at, w2._state_entered_at = 1110.0, 500.0                 # 23:38:11 W1 waits again
    assert newly_waiting_workers(pm1, [w1, w2], last_wake=200.0) == {"W1"}
    assert newly_waiting_workers(pm1, [w1, w2], last_wake=1200.0) == set()     # woken since
    assert newly_waiting_workers(pm1, [Agent("W4")], last_wake=0) == {"W4"}    # never seen


@needs_tw
def test_a_finished_project_needs_an_audit_once(tasks):
    from src.agents.assignable_work import finished_project_end, project_needs_audit
    pm1, w1 = Agent("PM1", "pm", "pm_standby"), Agent("W1")
    manager = _manager(pm1, w1)
    t1 = _add(tasks, manager, "Kick-off Task 1: Implement core game logic in src/game.js", "W1")
    with quiet():
        assert not project_needs_audit(manager, pm1)                            # Task 1 is open
        assert tasks("framework", manager, action="modify_task", task_id=t1, task_progress="finished")["status"] == "success"
        assert finished_project_end(manager, pm1) is not None and project_needs_audit(manager, pm1)
        w1.status = "processing"
        assert not project_needs_audit(manager, pm1)                            # a worker works
        w1.status = "idle"
        pm1._audited_at = time.time() + 5                                       # audited after the last task finished
        assert not project_needs_audit(manager, pm1)


@needs_tw
def test_the_standby_watchdog_moves_a_finished_project_to_the_audit(tasks, monkeypatch):
    """One pass of the real watchdog loop: PM1 in standby, every task finished, W1 seen since 21:03 - 23:38 situation."""
    from src.agents import manager as manager_module
    from src.agents.manager import AgentManager
    from src.agents.workflow_manager import AgentWorkflowManager
    pm1, w1, w2 = Agent("PM1", "pm", "pm_standby"), Agent("W1"), Agent("W2")
    pm1.clear_history = lambda: pm1.message_history.clear()
    pm1._pm_audit_attempt_count = 0                                            # as Agent.__init__ sets it
    pm1._seen_waiting_workers, pm1._last_standby_wake_time = {"W1", "W2"}, time.time() - 3600
    pm1.set_status = lambda status: setattr(pm1, "status", status)
    store = _manager(pm1, w1, w2)
    t1 = _add(tasks, store, "Kick-off Task 1: Implement core game logic in src/game.js", "W1")
    assert tasks("framework", store, action="modify_task", task_id=t1, task_progress="finished")["status"] == "success"
    _snake(tasks.workspace)
    scheduled = []

    async def schedule_cycle(agent, retry_count=0):
        scheduled.append((agent.agent_id, agent.state))
    am = AgentManager.__new__(AgentManager)
    am.agents, am.current_project, am.current_session = store.agents, PROJECT, SESSION
    am.state_manager, am.workflow_manager = store.state_manager, AgentWorkflowManager()
    am.schedule_cycle = schedule_cycle
    am.loop_coordinator = types.SimpleNamespace(should_intervene=lambda *a: True, record_intervention=lambda *a: None)
    sleeps = {"n": 0}
    real_sleep = asyncio.sleep
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PM_MANAGE_CHECK_INTERVAL_SECONDS", 4321.0, raising=False)

    async def one_pass(seconds, *args, **kwargs):
        if seconds != 4321.0:
            return await real_sleep(seconds, *args, **kwargs)                  # other sleeps (the run check) run as usual
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise asyncio.CancelledError
    monkeypatch.setattr(manager_module.asyncio, "sleep", one_pass)
    with quiet():
        with pytest.raises(asyncio.CancelledError):
            run(am._periodic_pm_manage_check())
    assert pm1.state == "pm_audit" and scheduled == [("PM1", "pm_audit")]
    notes = [m["content"] for m in pm1.message_history]
    assert notes[0].startswith("[Framework System Message]: Every task of your project is finished and no worker is "
                               "working, so the framework moved you from standby to 'pm_audit'")
    if HAVE_NODE:
        assert notes[1].startswith("[Framework Run Check]: before this audit the framework started the project the way a "
                                   "user would. It does NOT work yet:\n- the program does not keep running")
        assert "src/index/index.html loads `game.js`" in notes[1] and "NO package.json" in notes[1]
    assert pm1._audited_at >= pm1._state_entered_at - 1
    # a second pass: audited, nothing new - not moved again
    pm1.state, sleeps["n"] = "pm_standby", 0
    pm1._last_standby_wake_time = time.time() - 3600
    with quiet():
        with pytest.raises(asyncio.CancelledError):
            run(am._periodic_pm_manage_check())
    assert pm1.state == "pm_standby" and len(scheduled) == 1


def test_the_completion_check_has_its_state():
    from src.agents import manager as manager_module
    assert manager_module.PM_STATE_AUDIT == "pm_audit"


@needs_node
def test_the_audit_note_on_a_working_project(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.utils.program_run_check import audit_run_note
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    ws = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    port = _free_port()
    _workspace(ws, {"src/server.js": f"const http = require('http');\nconst PORT = {port};\nhttp.createServer((q, r) => "
                                     "{ r.writeHead(200, {'Content-Type': 'text/html'}); r.end('<html></html>'); })"
                                     ".listen(PORT);\n",
                    "src/index.html": "<html><script src=\"game.js\"></script></html>", "src/game.js": "// game\n",
                    "README.md": "Run `node src/server.js`.\n"})
    with quiet():
        note = run(audit_run_note(types.SimpleNamespace(current_project=PROJECT, current_session=SESSION)))
    assert note == ("[Framework Run Check]: before this audit the framework started the project: `node src/server.js` "
                    "keeps running and sends the page for GET /. Go on with the audit.")
    with quiet():
        assert run(audit_run_note(types.SimpleNamespace(current_project=None, current_session=None))) is None


def test_every_audit_entry_gets_the_run_check():
    import inspect
    from src.agents import cycle_handler
    from src.agents.cycle_components import next_step_scheduler
    assert "if agent.agent_type == AGENT_TYPE_PM and resolved_requested == PM_STATE_AUDIT:" in inspect.getsource(cycle_handler)
    assert "run_note = await audit_run_note(self._manager)" in inspect.getsource(cycle_handler)
    assert "run_note = await audit_run_note(self._manager)" in inspect.getsource(next_step_scheduler)


# ======================================================================================== 5. the kick-off's planned files
def test_technology_names_are_no_files():
    from src.utils.task_files import created_root_files, file_paths_in_text
    assert file_paths_in_text("Implement Node.js HTTP server to broadcast game state in src/server.js") == ["src/server.js"]
    assert file_paths_in_text("Draw with Three.js in src/three.js and Chart.js") == ["src/three.js"]
    assert created_root_files("Implement Node.js HTTP server") == []
    assert created_root_files("Write README.md and create server.js") == ["README.md", "server.js"]


def test_a_shared_name_in_a_folder_named_after_it_is_the_tasks_file():
    from src.workflows.pm_kickoff_workflow import duplicate_structure_files
    drop, notes = duplicate_structure_files(["src/index/index.html", "src/game/game.js", "tests/test_auth/test_auth.js"],
                                            ["src/index.html", "src/game.js"])
    assert drop == ["src/index/index.html", "src/game/game.js"]
    assert notes[0] == ("Planned file 'src/index/index.html' dropped: the kick-off task names this file 'src/index.html', "
                        "so that is the one path to create.")
    # a page in another folder (public/index.html) and other shared names stay
    assert duplicate_structure_files(["public/index.html", "src/utils/__init__.py"], ["src/index.html", "src/__init__.py"]) \
        == ([], [])

# END OF FILE tests/unit/test_round4d.py
