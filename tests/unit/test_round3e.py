# START OF FILE tests/unit/test_round3e.py
"""
Round 3e: the run of 2026-10-23 16:52-19:05 (qwen3.5:4b-q4_K_M for every agent, Multiplayer_Snake_Game).

* tests/test_api.js did `require('../src/server')` and src/server.js called `server.listen(config.port, ...)` at its top
  level: every `npm test` / `npx mocha tests/` (16 runs, 60-120 s each) timed out and nobody was told why.
* PM1 marked "Kick-off Task 9: Run all tests with npm test and perform end-to-end verification" finished at 18:41:23,
  90 seconds after its own `npm test` had timed out.
* W1 decomposed Kick-off Task 1 (src/server.js and src/routes/) into sub-tasks for src/game.js, public/index.html, tests/
  and docs/ - the files of Kick-off Tasks 3-8 - and wrote them all itself while W2 waited.
* W3 sent '[{"replace_lines": "...", "start_line": ..}]' under 'replace_lines'; the note said the edits were "applied"
  although code_editor rejected them.
* W3 was forced to report 3 times on Task 9 and nothing was escalated: the task ids were cleared when PM1 finished the task.
"""
import asyncio
import json
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]
PROJECT, SESSION = "Multiplayer_Snake_Game", "startup_1792745520"

HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture(autouse=True)
def _no_test_runs():
    from src.utils.test_runs import clear_test_runs
    clear_test_runs()
    yield
    clear_test_runs()


# The run's files (shortened)
SERVER_JS = """// src/server.js
const express = require('express');
const http = require('http');
/*
 * Multiplayer snake server
 */
const config = require('./config');
const app = express();
const server = http.createServer(app);

app.get('/api/health', (req, res) => res.json({ ok: true }));

server.listen(config.port, () => {
  console.log(`Server running on port ${config.port}`);
});

module.exports = { app, server };
"""
SERVER_JS_GUARDED = SERVER_JS.replace(
    "server.listen(config.port, () => {\n  console.log(`Server running on port ${config.port}`);\n});",
    "if (require.main === module) {\n  server.listen(config.port, () => {\n    console.log('Server running');\n  });\n}")
CONFIG_JS = "module.exports = { port: 3000 };\n"
TEST_API_JS = """const assert = require('assert');
const { app } = require('../src/server');

describe('API', () => {
  it('has an app', () => {
    assert.ok(app);
  });
});
"""
PACKAGE_JSON = json.dumps({"name": "snake", "scripts": {"test": "npx mocha tests/", "start": "node src/server.js"},
                           "dependencies": {"express": "^4.18.2"}, "devDependencies": {"mocha": "^10.2.0"}}, indent=2)


def _workspace(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _snake_project(root, server=SERVER_JS, package=PACKAGE_JSON):
    return _workspace(root, {"package.json": package, "src/server.js": server, "src/config.js": CONFIG_JS,
                             "tests/test_api.js": TEST_API_JS})


# ======================================================================================== 1. a server started on load
def test_starts_server_on_load():
    from src.tools.project_links_check import starts_server_on_load
    assert starts_server_on_load(SERVER_JS) == "`server.listen(` at line 13"   # the block comment keeps its lines
    assert starts_server_on_load("const app = express();\napp.listen(3000);\n") == "`app.listen(` at line 2"
    assert starts_server_on_load(SERVER_JS_GUARDED) is None                   # only when run directly
    assert starts_server_on_load("function start() {\n  server.listen(3000);\n}\nmodule.exports = start;\n") is None
    assert starts_server_on_load("// server.listen(3000);\nmodule.exports = {};\n") is None
    assert starts_server_on_load("if (!module.parent) {\n  app.listen(3000);\n}\napp.listen(1);\n") is None
    assert starts_server_on_load("") is None and starts_server_on_load(None) is None


def test_a_test_file_that_loads_a_server_started_on_load_is_found(tmp_path):
    from src.tools.project_links_check import server_on_load_problems, link_problems, link_notes_text
    ws = _snake_project(tmp_path)
    problems = server_on_load_problems(ws, "tests/test_api.js")
    assert len(problems) == 1
    assert problems[0].startswith("tests/test_api.js loads src/server.js, which STARTS THE SERVER as soon as it is loaded "
                                  "(`server.listen(` at line 13), so the test run never ends")
    assert "if (require.main === module)" in problems[0]
    assert problems[0] in link_problems(ws, ws / "tests" / "test_api.js")
    assert "STARTS THE SERVER" in link_notes_text(ws, "tests/test_api.js", TEST_API_JS)
    # not a test file, the guard, or mocha --exit: no problem
    assert server_on_load_problems(ws, "src/server.js") == []
    (ws / "src" / "server.js").write_text(SERVER_JS_GUARDED, encoding="utf-8")
    assert server_on_load_problems(ws, "tests/test_api.js") == []
    (ws / "src" / "server.js").write_text(SERVER_JS, encoding="utf-8")
    (ws / "package.json").write_text(PACKAGE_JSON.replace("npx mocha tests/", "npx mocha --exit tests/"), encoding="utf-8")
    assert server_on_load_problems(ws, "tests/test_api.js") == []
    (ws / "package.json").write_text(PACKAGE_JSON, encoding="utf-8")
    (ws / ".mocharc.json").write_text('{"exit": true}', encoding="utf-8")
    assert server_on_load_problems(ws, "tests/test_api.js") == []


def test_test_hang_hint(tmp_path):
    from src.tools.project_links_check import test_hang_hint, is_test_command
    for command in ("npm test", "npm run test", "npx mocha tests/", "node --test", "pytest -q", "python -m pytest",
                    "yarn test", "npx jest"):
        assert is_test_command(command), command
    for command in ("npm install", "node src/server.js", "ls tests", "npm start", "cat test_api.js"):
        assert not is_test_command(command), command
    ws = _snake_project(tmp_path)
    hint = test_hang_hint(ws, "npm test")
    assert hint.startswith("\n[Framework hint - why the TEST run did not end:]\n- tests/test_api.js loads src/server.js")
    assert "will time out again: fix this first" in hint
    assert test_hang_hint(ws, "npm install") == ""
    (ws / "src" / "server.js").write_text(SERVER_JS_GUARDED, encoding="utf-8")
    generic = test_hang_hint(ws, "npx mocha tests/")
    assert generic.startswith("\n[Framework hint - a TEST run that does not end keeps something open:]")
    assert "`--exit`" in generic and "will not help" in generic
    assert test_hang_hint(None, "npm test").startswith("\n[Framework hint - a TEST run that does not end")


# ======================================================================================== 2. test runs
def test_is_full_test_run():
    from src.utils.test_runs import is_full_test_run
    for command in ("npm test", "npx mocha tests/", "npm install && npm test", "npm test 2>&1", "python -m pytest -q",
                    "pytest tests"):
        assert is_full_test_run(command), command
    for command in ("npx mocha tests/test_api.js", "npm test | head -50", "npm test || true", "npm test; echo done",
                    "pytest tests/test_x.py::test_a", "pytest -k snake", "npx mocha --grep API", "npm install", ""):
        assert not is_full_test_run(command), command


def test_is_test_run_task_and_project_test_command(tmp_path):
    from src.utils.test_runs import is_test_run_task, project_test_command
    assert is_test_run_task("Kick-off Task 9: Run all tests with npm test and perform end-to-end verification of the game")
    assert is_test_run_task("Kick-off Task 8: Run the tests and the program to verify that everything works")
    assert is_test_run_task("Fix failing unit tests in tests/test_gameLogic.js")
    assert not is_test_run_task("Kick-off Task 7: Write unit tests for the game logic in tests/test_game.js")
    assert not is_test_run_task("Kick-off Task 1: Set up Express server with Socket.io in src/server.js")
    assert not is_test_run_task(None)
    assert project_test_command(tmp_path) is None
    (tmp_path / "package.json").write_text('{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}')
    assert project_test_command(tmp_path) is None
    (tmp_path / "package.json").write_text(PACKAGE_JSON)
    assert project_test_command(tmp_path) == "npm test"
    py = tmp_path / "py"
    _workspace(py, {"tests/test_converter.py": "def test_a():\n    assert True\n", "node_modules/x/test_y.py": ""})
    assert project_test_command(py) == "python -m pytest -q"
    only_modules = tmp_path / "mods"
    _workspace(only_modules, {"node_modules/x/test_y.py": ""})
    assert project_test_command(only_modules) is None


def test_verification_problem(tmp_path):
    from src.utils.test_runs import verification_problem, record_test_run, latest_test_run, PASSED, FAILED, TIMEOUT
    ws = _snake_project(tmp_path)
    task = "Kick-off Task 9: Run all tests with npm test and perform end-to-end verification"
    missing = verification_problem(ws, task)
    assert missing.startswith("no full test run of the CURRENT files has passed yet. Run the whole test suite (`npm test`)")
    assert verification_problem(ws, "Kick-off Task 6: Build the game UI in public/index.html") is None
    record_test_run(ws, "npx mocha tests/test_api.js", PASSED)          # one file: not a full run
    assert latest_test_run(ws) is None
    record_test_run(ws, "npm test", TIMEOUT, "API")
    timeout = verification_problem(ws, task)
    assert timeout.startswith("the tests do NOT pass: the last full test run (`npm test`) did not END - it timed out.")
    assert "tests/test_api.js loads src/server.js" in timeout
    record_test_run(ws, "npm test", FAILED, "1 passing 2 failing AssertionError")
    assert verification_problem(ws, task) == ("the tests do NOT pass: the last full test run (`npm test`) failed. Its output "
                                              "ended with: 1 passing 2 failing AssertionError")
    record_test_run(ws, "npm test", PASSED, "3 passing")
    assert verification_problem(ws, task) is None
    (ws / "src" / "server.js").write_text(SERVER_JS_GUARDED, encoding="utf-8")   # changed after the passing run
    assert verification_problem(ws, task).startswith("no full test run of the CURRENT files has passed yet")


def test_command_executor_records_runs_and_explains_a_test_timeout(tmp_path, monkeypatch):
    import src.tools.command_executor as ce
    from src.utils.test_runs import latest_test_run
    ws = _snake_project(tmp_path / "sandbox")
    results = []

    async def fake_run(command, cwd, timeout, tool_name=None, agent_id=None):
        return results.pop(0)

    def result(status, returncode=0, stdout="", stderr=""):
        return types.SimpleNamespace(status=status, returncode=returncode, stdout=stdout, stderr=stderr, message="",
                                     background_log=None, stdout_truncated=False, stderr_truncated=False)

    monkeypatch.setattr(ce, "run_sandboxed", fake_run)

    def execute(command, timeout=60):
        with quiet():
            return run(ce.CommandExecutionTool().execute(agent_id="W3", agent_sandbox_path=ws, scope="private",
                                                         action="run_command", command=command, timeout=timeout))

    results.append(result("timeout", stdout="\n> snake@1.0.0 test\n> npx mocha tests/\n\nServer running on port 3000\n"))
    answer = execute("npm test", timeout=120)
    assert answer["status"] == "error"
    assert answer["message"].startswith("The TEST command timed out after 120 seconds and its process group was terminated: "
                                        "the test run did not END. Output so far (end):\n> snake@1.0.0 test")
    assert "tests/test_api.js loads src/server.js, which STARTS THE SERVER" in answer["message"]
    assert latest_test_run(ws)["outcome"] == "timeout"
    results.append(result("success", returncode=1, stdout="1 passing\n1 failing"))
    execute("npx mocha tests/")
    assert latest_test_run(ws)["outcome"] == "failed" and latest_test_run(ws)["command"] == "npx mocha tests/"
    results.append(result("success", returncode=0, stdout="2 passing"))
    assert execute("npm test")["status"] == "success"
    assert latest_test_run(ws)["outcome"] == "passed"
    results.append(result("success", returncode=1, stdout="1 failing"))
    execute("npx mocha tests/test_api.js")                                   # one file: the full run stays
    assert latest_test_run(ws)["outcome"] == "passed"
    # a timeout of another command keeps the old message
    results.append(result("timeout", stdout="listening"))
    other = execute("node src/worker.js")
    assert "TEST command" not in other.get("message", "") and latest_test_run(ws)["outcome"] == "passed"


# ======================================================================================== 3. tasks
class Worker:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None


def _manager(*agents):
    return types.SimpleNamespace(
        agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION,
        state_manager=types.SimpleNamespace(get_agent_team=lambda agent_id: f"team_{PROJECT}",
                                            get_agents_in_team=lambda team_id: list(agents)))


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


TASK_9 = ("Kick-off Task 9: Run all tests with npm test and perform end-to-end verification of the multiplayer game "
          "(player join, movement, scoring and leaderboard)")


def _add(call, manager, text, assignee=None):
    args = {"assignee_agent_id": assignee} if assignee else {}
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert added["status"] == "success", added
    return added["task_uuid"]


@needs_tw
def test_a_test_run_task_is_finished_only_after_a_passing_full_run(tasks):
    from src.utils.test_runs import record_test_run, PASSED, TIMEOUT
    pm1, w3 = Worker("PM1", "pm", "pm_manage"), Worker("W3", state="worker_work")
    manager = _manager(pm1, w3)
    ws = _snake_project(tasks.workspace)
    t9 = _add(tasks, manager, TASK_9, "W3")
    # 18:41:23: PM1 marked it finished 90 s after its own `npm test` had timed out
    record_test_run(ws, "npm test", TIMEOUT, "Server running on port 3000")
    refused = tasks("PM1", manager, action="modify_task", task_id=t9, task_progress="finished")
    assert refused["status"] == "error" and refused["error_type"] == "tests_not_passing", refused
    assert refused["message"].startswith(f"Task '{t9}' was NOT marked finished: the tests do NOT pass: the last full test "
                                         "run (`npm test`) did not END - it timed out.")
    assert "src/server.js, which STARTS THE SERVER" in refused["message"]
    assert "send the task back to its worker with send_message" in refused["message"]
    assert tasks("PM1", manager, action="complete_task", task_id=t9)["error_type"] == "tests_not_passing"
    assert tasks("W3", manager, action="modify_task", task_id=t9, task_progress="finished")["status"] == "error"
    # the server is fixed, but the tests were not run again
    (ws / "src" / "server.js").write_text(SERVER_JS_GUARDED, encoding="utf-8")
    again = tasks("PM1", manager, action="modify_task", task_id=t9, task_progress="finished")
    assert "no full test run of the CURRENT files has passed yet" in again["message"]
    record_test_run(ws, "npm test", PASSED, "1 passing")
    done = tasks("PM1", manager, action="modify_task", task_id=t9, task_progress="finished")
    assert done["status"] == "success", done


@needs_tw
def test_the_test_check_spares_other_tasks_and_the_framework(tasks):
    pm1, w1 = Worker("PM1", "pm", "pm_manage"), Worker("W1", state="worker_work")
    manager = _manager(pm1, w1)
    ws = _snake_project(tasks.workspace)
    ui = _add(tasks, manager, "Kick-off Task 6: Build the game page in public/index.html with a canvas", "W1")
    _workspace(ws, {"public/index.html": "<!DOCTYPE html>\n<html><body><canvas id=\"game\"></canvas></body></html>\n"})
    assert tasks("PM1", manager, action="modify_task", task_id=ui, task_progress="finished")["status"] == "success"
    t9 = _add(tasks, manager, TASK_9, "W1")
    assert tasks("framework", manager, action="modify_task", task_id=t9, task_progress="finished")["status"] == "success"
    # no project test command: nothing to check
    (ws / "package.json").write_text(json.dumps({"name": "snake", "scripts": {"start": "node src/server.js"}}))
    t8 = _add(tasks, manager, "Kick-off Task 8: Run the tests and the program to verify that everything works", "W1")
    assert tasks("PM1", manager, action="modify_task", task_id=t8, task_progress="finished")["status"] == "success"


TASK_1 = ("Kick-off Task 1: Set up Express server with Socket.io, game rooms and the REST API in src/server.js and "
          "src/routes/")
TASK_5 = "Kick-off Task 5: Implement the snake movement, food spawning and collision rules in src/game.js"
TASK_6 = "Kick-off Task 6: Build the browser page with the canvas and the controls in public/index.html"


@needs_tw
def test_a_worker_sub_task_does_not_take_another_task_s_files(tasks):
    pm1, w1, w2 = Worker("PM1", "pm", "pm_manage"), Worker("W1", state="worker_decompose"), Worker("W2")
    manager = _manager(pm1, w1, w2)
    t1 = _add(tasks, manager, TASK_1, "W1")
    t5 = _add(tasks, manager, TASK_5)
    _add(tasks, manager, TASK_6)
    w1.current_task_id = t1
    # 16:59:13 W1: a sub-task of Task 1 for the file of Task 5
    refused = tasks("W1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W1",
                    description="Create src/game.js with core snake game logic (movement, food, collision)")
    assert refused["status"] == "error" and refused["error_type"] == "foreign_files", refused
    assert refused["message"].startswith(f"The task was NOT created: src/game.js belongs to another open task "
                                         f"('{TASK_5[:87]}...', {t5}), not to YOUR task ('Kick-off Task 1: Set up Express server")
    assert "leave the other tasks' files to the workers who get those tasks" in refused["message"]
    refused = tasks("W1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W1",
                    description="Create the static frontend public/index.html served by Express")
    assert refused.get("error_type") == "foreign_files", refused
    # the files of its own task, and files no task names, are fine
    own = tasks("W1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W1",
                description="Create src/server.js with the Express app and the Socket.io connection handler")
    assert own["status"] == "success", own
    new = tasks("W1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W1",
                description="Create src/routes/rooms.js with the REST routes for game rooms")
    assert new["status"] == "success", new
    # a PM may plan any task; a finished task's files are no longer taken
    pm_task = tasks("PM1", manager, action="add_task", project_filter=PROJECT,
                    description="Add wall wrapping to src/game.js as an option")
    assert pm_task.get("error_type") != "foreign_files", pm_task
    _workspace(tasks.workspace, {"src/game.js": "function step(s) { return s; }\nmodule.exports = { step };\n"})
    assert tasks("framework", manager, action="modify_task", task_id=t5, task_progress="finished")["status"] == "success"
    still = tasks("W1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W1",
                  description="Create src/rooms.js to keep one src/game.js state per game room")
    assert still.get("error_type") == "foreign_files" and pm_task["task_uuid"] in still["message"]   # the PM's task is open
    assert tasks("framework", manager, action="modify_task", task_id=pm_task["task_uuid"],
                 task_progress="finished")["status"] == "success"
    later = tasks("W1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W1",
                  description="Create src/rooms.js to keep one src/game.js state per game room")
    assert later.get("error_type") != "foreign_files", later


@needs_tw
def test_foreign_files_check_needs_a_known_worker_task(tasks):
    from src.tools.project_management import foreign_files_subtask_error
    pm1, w1 = Worker("PM1", "pm", "pm_manage"), Worker("W1", state="worker_decompose")
    manager = _manager(pm1, w1)
    _add(tasks, manager, TASK_5)
    import src.tools.project_management as pmt
    tw = pmt.ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION)
    description = "Create src/game.js with core snake game logic"
    assert foreign_files_subtask_error(tw, "W1", manager, description) is None      # W1 has no task: unknown parent
    assert foreign_files_subtask_error(tw, "PM1", manager, description) is None
    assert foreign_files_subtask_error(tw, "framework", manager, description) is None
    assert foreign_files_subtask_error(tw, None, manager, description) is None


# ======================================================================================== 4. file_system replace_lines
PY_FILE = "x = 1\ny = 2\nz = 3\n"


def _fs(tmp_path, **args):
    from src.tools.file_system import FileSystemTool
    with quiet():
        return run(FileSystemTool().execute("W3", tmp_path, scope="private", **args))


def test_replace_lines_edits_with_the_text_under_replace_lines(tmp_path):
    from src.tools.file_system import FileSystemTool
    (tmp_path / "m.py").write_text(PY_FILE, encoding="utf-8")
    # 18:40:27 W3: the new text of each edit under the name of the action
    result = _fs(tmp_path, action="replace_lines", filename="m.py",
                 replace_lines='[{"start_line": 2, "end_line": 2, "replace_lines": "y = 5"}]')
    assert result["status"] == "success", result
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "x = 1\ny = 5\nz = 3\n"
    assert result["message"].startswith("[Note: your 'replace_lines' held a list of line edits, so the framework passed "
                                        "them to the code_editor tool.")
    result = _fs(tmp_path, action="replace_lines", filename="m.py",
                 replace_lines=[{"start_line": 3, "end_line": 3, "new_lines": ["z = 7", "w = 8"]}])
    assert result["status"] == "success", result
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "x = 1\ny = 5\nz = 7\nw = 8\n"
    # an explicit text key wins over the alias
    edits = FileSystemTool._line_edit_list([{"start_line": 1, "end_line": 1, "replace": "a = 0", "lines": "ignored"}])
    assert edits == [{"start_line": 1, "end_line": 1, "replace": "a = 0", "lines": "ignored"}]


def test_a_rejected_line_edit_list_is_not_called_applied(tmp_path):
    (tmp_path / "m.py").write_text(PY_FILE, encoding="utf-8")
    result = _fs(tmp_path, action="replace_lines", filename="m.py",
                 replace_lines='[{"start_line": 2, "end_line": 2, "search": "not in the file", "replace": "q = 1"}]')
    assert result["status"] == "error", result
    assert result["message"].startswith("[Note: your 'replace_lines' held a list of line edits, so the framework passed "
                                        "them to the code_editor tool.")
    assert "applied them" not in result["message"]
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == PY_FILE


# ======================================================================================== 5. escalation
def test_escalation_survives_cleared_task_ids():
    from src.agents.task_escalation import record_forced_report, forced_report_count
    # 18:15-18:44 W3: forced 3x on Task 9; PM1 finished the task at 18:41 and W3's ids were cleared before the 3rd report
    agent = types.SimpleNamespace(agent_id="W3", active_task_id="e2fdc9e3", current_task_id="e2fdc9e3",
                                  _injected_task_description=TASK_9)
    assert record_forced_report(agent) is None and record_forced_report(agent) is None
    agent.active_task_id = agent.current_task_id = None
    assert record_forced_report(agent) == ("e2fdc9e3", 3)
    assert forced_report_count(agent) == 3
    # a worker whose id was never seen: counted by its injected description
    fresh = types.SimpleNamespace(agent_id="W2", active_task_id=None, current_task_id=None,
                                  _injected_task_description="Kick-off Task 6: Build the browser page")
    for _ in range(2):
        assert record_forced_report(fresh) is None
    assert record_forced_report(fresh) == ("task: Kick-off Task 6: Build the browser page", 3)


# ======================================================================================== 6. prompts
def test_decompose_prompt_keeps_sub_tasks_inside_the_own_task():
    import yaml
    prompts = yaml.safe_load((REPO / "prompts.yaml").read_text(encoding="utf-8"))
    text = prompts["worker_decompose_prompt"]
    assert ("Sub-tasks cover only YOUR task: never create a sub-task for a file or feature that another task of the project "
            "names (the framework refuses it) - other workers get those tasks.") in text

# END OF FILE tests/unit/test_round3e.py
