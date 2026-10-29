# START OF FILE tests/unit/test_round4f.py
"""
Round 4f: the run of 2026-10-29 22:11 - 2026-10-30 00:27 (qwen3.5:4b-q4_K_M, five workers, WiFi_Snake_Game, JavaScript).

* package.json said "test": "node --test" and the tests were tests/*.spec.js. Node's runner without file names finds only
  *.test.js, *-test.js, *_test.js, test-*.js, test.js and test/ folders: `npm test` ran 0 tests and exited 0. It counted as a
  passing full test run, and W5's "Integration and Testing: Run ... integration tests and verify all components work" (23:47)
  and "Integration Testing: Run the game ... to verify end-to-end functionality" (00:10) were finished on it.
* the framework's own advice `node --test tests/` does not work in Node 22: it loads the folder as ONE module ("tests 1,
  fail 1"); `node --test tests/*.spec.js` ran the 73 tests.
* 23:01 and 23:26 PM1 added "Integration and Testing ..." and "Run Unit Tests: ... after W2 completes writing the test
  files" while W2 still wrote them (Kick-off Task 7): both started at once.
* 23:49 PM1 tried to turn Kick-off Task 7 into "Fix PlayerManager.getPlayerById() bug: ..." (refused: Round 3t keeps the
  plan's tasks), and 00:17 turned "Run Unit Tests" (W1), a task it had added, into "Add missing methods to SnakeGame class
  ..." by rewriting its description.
* 23:47 and 00:10 the run check found port 3000 in use - W5's own `npm start &` - and judged nothing.
"""
import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Game", "startup_1793283053"
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


# the output of the run's `npm test` (23:14:17, 23:40:29)
NPM_TEST_NOTHING = """
> wifi-snake-game@1.0.0 test
> node --test

1..0
# tests 0
# suites 0
# pass 0
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 59.941078
"""
SPEC = "const { test } = require('node:test');\nconst assert = require('node:assert');\ntest('a', () => assert.ok(true));\n"
PROJECT_FILES = {"package.json": json.dumps({"name": "wifi-snake-game", "scripts": {"start": "node src/server/index.js",
                                                                                      "test": "node --test"}}),
                 "tests/player.spec.js": SPEC, "tests/leaderboard.spec.js": SPEC, "tests/snake.spec.js": SPEC}


# ======================================================================================== 1. a run of no tests
def test_a_run_of_no_tests_is_recognized():
    from src.utils.test_runs import ran_no_tests
    assert ran_no_tests(NPM_TEST_NOTHING)
    assert ran_no_tests("ℹ tests 0\nℹ suites 0\nℹ pass 0\n") and ran_no_tests("\n  0 passing (2ms)\n")
    for output in ("# tests 73\n# pass 55\n# fail 18\n", "1..0 # SKIP\n# tests 3\n", "3 passed in 0.1s", "  12 passing\n",
                   "", None):
        assert not ran_no_tests(output), output


def test_a_run_of_no_tests_is_no_pass(tmp_path):
    from src.utils.test_runs import FAILED, PASSED, clear_test_runs, latest_test_run, record_test_run, verification_problem
    ws = _workspace(tmp_path, PROJECT_FILES)
    clear_test_runs()
    with quiet():
        record_test_run(ws, "npm test", PASSED, NPM_TEST_NOTHING[-400:], output=NPM_TEST_NOTHING)
    run_record = latest_test_run(ws)
    assert run_record["outcome"] == FAILED and run_record["summary"].startswith("it ran NO tests")
    problem = verification_problem(ws, "Integration and Testing: Run the WiFi Snake Game integration tests and verify all "
                                       "components work correctly together")
    assert problem == (
        "the last full test run (`npm test`) ran NO tests, so it proves nothing. Node's runner without file names only finds "
        "*.test.js, *-test.js, *_test.js, test-*.js, test.js or files in a test/ folder; this project's tests are "
        "tests/leaderboard.spec.js, tests/player.spec.js, tests/snake.spec.js. Run `node --test tests/*.spec.js` and put it "
        "in package.json: \"test\": \"node --test tests/*.spec.js\", fix what fails, and finish the task when that run "
        "passes.")
    # a run that did run tests stays as it was
    with quiet():
        record_test_run(ws, "npm test", PASSED, "# tests 73", output="1..73\n# tests 73\n# pass 73\n")
    assert latest_test_run(ws)["outcome"] == PASSED
    clear_test_runs()


def test_the_worker_hears_it_at_once(tmp_path):
    from src.tools.command_hints import command_notes
    ws = _workspace(tmp_path, PROJECT_FILES)
    notes = command_notes(ws, "npm test", 0, NPM_TEST_NOTHING)
    assert len(notes) == 1 and notes[0].startswith("This run passed only because the command (`npm test`) ran NO tests")
    assert "`node --test tests/*.spec.js`" in notes[0]
    assert command_notes(ws, "npm test", 0, "1..3\n# tests 3\n# pass 3\n") == []


@needs_node
def test_node_really_runs_nothing_and_the_named_files(tmp_path):
    ws = _workspace(tmp_path, PROJECT_FILES)
    nothing = subprocess.run("node --test", shell=True, cwd=ws, capture_output=True, text=True, timeout=60)
    from src.utils.test_runs import ran_no_tests
    assert nothing.returncode == 0 and ran_no_tests(nothing.stdout)
    from src.utils.js_test_checks import node_test_command
    named = subprocess.run(node_test_command(ws), shell=True, cwd=ws, capture_output=True, text=True, timeout=60)
    assert named.returncode == 0 and "# tests 3" in named.stdout


# ======================================================================================== 2. the right `node --test`
def test_the_node_test_command_names_the_files(tmp_path):
    from src.utils.js_test_checks import node_test_command
    assert node_test_command(_workspace(tmp_path / "a", PROJECT_FILES)) == "node --test tests/*.spec.js"
    assert node_test_command(files=["tests/test_game.js", "tests/test_player.js"]) == "node --test tests/test_*.js"
    assert node_test_command(files=["tests/game.test.js"]) == "node --test tests/*.test.js"
    assert node_test_command(files=["game.test.js", "x.test.js"]) == "node --test *.test.js"
    assert node_test_command(files=["tests/a.test.js", "tests/unit/b.test.js"]) == \
        "node --test tests/a.test.js tests/unit/b.test.js"
    many = [f"tests/m{i}/t{i}.spec.js" for i in range(8)]
    assert node_test_command(files=many) == 'node --test "tests/**/*.spec.js"'
    assert node_test_command(tmp_path / "empty") == "node --test tests/*.test.js"


def test_no_advice_says_node_test_with_a_folder():
    root = Path(__file__).resolve().parents[2] / "src"
    offenders = [str(path.relative_to(root)) for path in root.rglob("*.py")
                 if "`node --test tests/`" in path.read_text(encoding="utf-8")
                 and "does not work in Node 22" not in path.read_text(encoding="utf-8")]
    assert offenders == []


# ======================================================================================== 3. tasks
class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history = []


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
    from src.utils.test_runs import clear_test_runs
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    workspace.mkdir(parents=True)
    clear_test_runs()

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    call.workspace = workspace
    yield call
    clear_test_runs()


def _add(call, manager, text, assignee=None, caller="framework", **extra):
    args = {"assignee_agent_id": assignee} if assignee else {}
    result = call(caller, manager, action="add_task", description=text, project_filter=PROJECT, **args, **extra)
    assert result["status"] == "success", result
    return result


TASK_7 = "Kick-off Task 7: Write automated tests for game logic, player management, and leaderboard in tests/*.spec.js"
INTEGRATION = ("Integration and Testing: Run the WiFi Snake Game integration tests and verify all components work correctly "
               "together")


@needs_tw
def test_a_verification_task_finished_on_no_tests_is_refused(tasks):
    from src.utils.test_runs import PASSED, record_test_run
    pm1, w5 = Agent("PM1", "pm", "pm_manage"), Agent("W5", state="worker_work")
    manager = _manager(pm1, w5)
    _workspace(tasks.workspace, PROJECT_FILES)
    integration = _add(tasks, manager, INTEGRATION, "W5")["task_uuid"]
    with quiet():
        record_test_run(tasks.workspace, "npm test", PASSED, NPM_TEST_NOTHING[-400:], output=NPM_TEST_NOTHING)  # 23:40:29
    refused = tasks("W5", manager, action="modify_task", task_id=integration, task_progress="finished")       # 23:47:00
    assert refused["status"] == "error" and refused["error_type"] == "tests_not_passing", refused
    assert "ran NO tests" in refused["message"] and "`node --test tests/*.spec.js`" in refused["message"]


@needs_tw
def test_a_new_verification_task_waits_for_the_tests_being_written(tasks):
    from src.tools.project_management import ProjectManagementTool
    pm1, w2, w5 = Agent("PM1", "pm", "pm_manage"), Agent("W2", state="worker_work"), Agent("W5")
    manager = _manager(pm1, w2, w5)
    task7 = _add(tasks, manager, TASK_7, "W2")["task_uuid"]
    tasks("W2", manager, action="modify_task", task_id=task7, task_progress="doing")
    added = _add(tasks, manager, INTEGRATION, caller="PM1")                                                   # 23:01
    assert added["depends"] == [task7]
    assert added["message"].startswith("Task added successfully. It WAITS for the open task(s) that still write the "
                                       f"tests, so it runs a complete test suite: '{TASK_7[:67]}...' ({task7}).")
    unit = _add(tasks, manager, "Run Unit Tests: Run the unit tests for the WiFi Snake Game after W2 completes writing the "
                                "test files. Execute npm test", caller="PM1")                               # 23:26
    assert unit["depends"] == [task7]
    # not for: framework calls, a worker's task, a fix task, a task with its own depends, or when no test is written
    assert _add(tasks, manager, "Run all the tests and verify the game works")["depends"] == []
    assert _add(tasks, manager, "Run the tests of my parser and verify it", caller="W5")["depends"] == []
    assert _add(tasks, manager, "Run the tests and fix the failures in tests/player.spec.js", caller="PM1")["depends"] == []
    explicit = _add(tasks, manager, "Run all tests to verify the release", caller="PM1", depends=added["task_uuid"])
    assert explicit["depends"] == [added["task_uuid"]]
    tw = ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION)
    task = tw.tasks.get(uuid=task7)
    task["task_progress"], task["status"] = "finished", "completed"
    task.save()
    assert _add(tasks, manager, "Run all the tests again and verify the game", caller="PM1")["depends"] == []


@needs_tw
def test_a_pm_does_not_turn_an_open_task_into_other_work(tasks):
    pm1, w1, w2 = Agent("PM1", "pm", "pm_manage"), Agent("W1"), Agent("W2", state="worker_work")
    manager = _manager(pm1, w1, w2)
    task7 = _add(tasks, manager, TASK_7, "W2")["task_uuid"]
    planned = tasks("PM1", manager, action="modify_task", task_id=task7,                                     # 23:49:05
                    description="Fix PlayerManager.getPlayerById() bug: The method tries to retrieve players by id")
    assert planned["error_type"] == "planned_task"  # Round 3t, before this check
    unit = _add(tasks, manager, "Run Unit Tests: Run the unit tests for the WiFi Snake Game after W2 completes writing the "
                                "test files. Execute npm test", "W1")["task_uuid"]
    refused = tasks("PM1", manager, action="modify_task", task_id=unit,                                      # 00:17:19
                    description="Add missing methods to SnakeGame class (getSnake, getFood, getCurrentScore, getHighScore, "
                                "getPlayers)")
    assert refused["status"] == "error" and refused["error_type"] == "repurposed_task", refused
    assert refused["message"].startswith(f"Task '{unit}' was NOT changed: its new description is DIFFERENT work ('Add "
                                         "missing methods to SnakeGame class (getSnake, getFood, getCurrentScore, g...') "
                                         "instead of 'Run Unit Tests: Run the unit tests for the WiFi Snake Game after W2 "
                                         "completes...' (assigned to W1). That work would be lost.")
    assert "Create a NEW task for the new work" in refused["message"]
    # clarifying stays allowed: the same title with details, the old text extended, the same fix said more precisely
    fix = _add(tasks, manager, "Fix SnakeGame class bugs: Fix bugs in src/game/snake.js: 1) move() should return true on "
                               "successful movement, 2) gameOver", "W1")["task_uuid"]
    for text in ("Fix SnakeGame class bugs: Fix bugs in src/game/snake.js: 1) move() should return true on successful "
                 "movement, 2) gameOver should stop the loop, 3) reset() clears the score",
                 "Fix SnakeGame move() function: Verify move() returns true on successful movement (not eating food) and "
                 "false on collision"):
        assert tasks("PM1", manager, action="modify_task", task_id=fix, description=text)["status"] == "success", text
    # the framework and finished tasks are not judged
    assert tasks("framework", manager, action="modify_task", task_id=unit,
                 description="Fix PlayerManager.getPlayerById() bug: retrieve players by id")["status"] == "success"


# ======================================================================================== 4. the project's own running server
def test_the_port_in_use_and_the_ports_of_config_files(tmp_path):
    from src.utils.program_run_check import _port_from_output, server_port
    assert _port_from_output("Error: listen EADDRINUSE: address already in use 0.0.0.0:3000\n    at Server") == 3000
    assert _port_from_output("OSError: [Errno 98] Address already in use: ('0.0.0.0', 8000)") is None
    ws = _workspace(tmp_path, {"src/server/index.js": "const config = { port: 3000 };\nserver.listen(config.port);\n",
                               "src/app.js": "const config = require('../config/server.config');\napp.listen(config.port);\n",
                               "config/server.config.js": "module.exports = {\n  port: 4321\n};\n"})
    assert server_port(ws, "src/server/index.js") == 3000 and server_port(ws, "src/app.js") == 4321


def _free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@needs_node
@pytest.mark.skipif(not Path("/proc/net/tcp").exists(), reason="needs Linux /proc")
def test_the_running_copy_of_the_project_is_checked(tmp_path, monkeypatch):
    from src.utils import program_run_check
    monkeypatch.setattr(program_run_check, "RUN_CHECK_SECONDS", 3.0)
    monkeypatch.setattr(program_run_check, "HTTP_PROBE_DELAY_SECONDS", 1.0)
    port = _free_port()
    server = (f"const http = require('http');\nconst PORT = {port};\nhttp.createServer((q, r) => {{ r.writeHead(200, "
              "{'Content-Type': 'application/json'}); r.end('{\"ok\":true}'); }).listen(PORT);\n")
    ws = _workspace(tmp_path / "ws", {"src/server.js": server, "public/index.html": "<html></html>"})
    copy = subprocess.Popen(["node", "src/server.js"], cwd=ws, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        assert program_run_check.port_owner_in_workspace(port, ws)
        assert not program_run_check.port_owner_in_workspace(port, tmp_path / "other")
        with quiet():
            problem = run(program_run_check.program_run_problem(ws))
        assert problem and problem.startswith(f"the server runs, but http://localhost:{port}/ answers 200 "
                                              "(application/json: '{\"ok\":true}') instead of the page public/index.html")
        # the same port held by a program of ANOTHER folder: not judged
        other = _workspace(tmp_path / "other", {"src/server.js": server, "public/index.html": "<html></html>"})
        with quiet():
            assert run(program_run_check.program_run_problem(other)) is None
    finally:
        copy.terminate()
        copy.wait(timeout=10)

# END OF FILE tests/unit/test_round4f.py
