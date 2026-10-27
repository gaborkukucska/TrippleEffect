# START OF FILE tests/unit/test_round3f.py
"""
Round 3f: the run of 2026-10-23 19:55-21:27 (qwen3.5:4b-q4_K_M for every agent, WiFi_Snake_Multiplayer_Game).

* 20:53:51-21:24:02: the Ollama stream of W2's 2nd context summary stopped after 2,608 characters; the provider waited its
  30-minute per-chunk timeout and the whole team stood still.
* 20:47-21:26: `node test/game.test.js` and `npm test` timed out 4 times. The game's spawnFood() looped forever (a 0x0 board
  has no free cell: the CPU was busy), but the agent was told about interactive prompts, servers and open sockets.
  `node test/game.test.js` was not even recognised as a test run.
* test/game.test.js did `global.process = { exit: ... }`: a failing run would have ended with exit code 0.
* 21:26 src/server/server.js had an unguarded `server.listen(` AND a guarded one: the guard hid both.
* The plan put package.json in config/; `npm test` needed a second one at the root.
* 20:01:55 W1 (Kick-off Task 2, script.js) created test/game.test.js, the file of Kick-off Task 6 assigned to W2.
* 20:12:24 W2's sub-task for index.html was refused although index.html belonged to W2's own next task.
"""
import asyncio
import json
import logging
import shutil
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]
PROJECT, SESSION = "WiFi_Snake_Multiplayer_Game", "startup_1792756544"
HAVE_PROC = Path("/proc/self/stat").is_file()
needs_proc = pytest.mark.skipif(not HAVE_PROC, reason="needs Linux /proc")

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
def _clean_state():
    from src.utils.test_runs import clear_test_runs
    from src.llm_providers.ollama_provider import reset_output_rates
    clear_test_runs()
    reset_output_rates()
    yield
    clear_test_runs()
    reset_output_rates()


def _workspace(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# The run's files (shortened)
SCRIPT_JS = """class SnakeGame {
  constructor(canvasId, socket) {
    this.tileCountX = 0;
    this.tileCountY = 0;
    this.snake = [];
    this.resetSnake();
  }
  resetSnake() {
    this.snake = [{ x: Math.floor(this.tileCountX / 2), y: Math.floor(this.tileCountY / 2) }];
    this.spawnFood();
  }
  spawnFood() {
    let validPosition = false;
    while (!validPosition) {
      this.food = { x: Math.floor(Math.random() * this.tileCountX), y: Math.floor(Math.random() * this.tileCountY) };
      validPosition = !this.snake.some(s => s.x === this.food.x && s.y === this.food.y);
    }
  }
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = SnakeGame;
}
"""
GAME_TEST_JS = """/**
 * WiFi Snake Multiplayer Game - Test Suite
 */
let processExitCode = null;
global.process = {
  exit: function(code) {
    processExitCode = code;
  }
};
const SnakeGame = require('../src/game/script.js');
console.log('Test 1: should initialize snake in the center');
let game1 = new SnakeGame('game-canvas', {});
"""
PACKAGE_JSON = json.dumps({"name": "wifi-snake-multiplayer-game", "scripts": {"test": "node --test test/game.test.js",
                                                                              "start": "node src/server/server.js"}})
SERVER_JS_BOTH = """const express = require('express');
const http = require('http');
const app = express();
const server = http.createServer(app);
const PORT = process.env.PORT || 3001;

server.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});

// Start server only if run directly
if (require.main === module) {
  server.listen(PORT, () => {
    console.log('Server running');
  });
}

module.exports = { app, server };
"""


# ======================================================================================== 1. busy or idle
@needs_proc
def test_a_timed_out_command_reports_whether_it_was_busy(tmp_path):
    from src.tools.sandbox_runner import run_sandboxed
    busy = run(run_sandboxed(f"{sys.executable} -c 'while True: pass'", tmp_path, 1.5, kill_switch=False))
    assert busy.status == "timeout" and busy.cpu_busy is not None and busy.cpu_busy >= 0.5, busy
    idle = run(run_sandboxed("sleep 30", tmp_path, 1.5, kill_switch=False))
    assert idle.status == "timeout" and idle.cpu_busy is not None and idle.cpu_busy <= 0.05, idle
    done = run(run_sandboxed("echo ok", tmp_path, 5, kill_switch=False))
    assert done.status == "completed" and done.cpu_busy is None


def test_group_cpu_rate_without_the_group():
    from src.tools.sandbox_runner import group_cpu_rate
    assert run(group_cpu_rate(2 ** 22 + 12345, interval=0.01)) is None      # no such process group


def test_cpu_state_and_last_output_line():
    from src.tools.project_links_check import cpu_state, last_output_line
    assert cpu_state(1.0) == "busy" and cpu_state(0.5) == "busy" and cpu_state(0.0) == "idle"
    assert cpu_state(0.2) is None and cpu_state(None) is None and cpu_state(True) is None
    output = "> node --test test/game.test.js\n\nTAP version 13\n# === WiFi Snake Game Tests ===\n" \
             "# Test 1: should initialize snake in the center\n\n"
    assert last_output_line(output) == "# Test 1: should initialize snake in the center"
    assert last_output_line("") == "" and last_output_line(None) == ""
    assert len(last_output_line("x" * 500)) == 200


# ======================================================================================== 2. test commands
def test_a_test_file_run_directly_is_a_test_command():
    from src.tools.project_links_check import is_test_command, test_command_end
    from src.utils.test_runs import is_full_test_run
    for command in ("node test/game.test.js", "timeout 10 node test/game.test.js 2>&1", "node test/game.test.js &",
                    "python tests/test_converter.py", "python3 -u tests/test_x.py", "npx tsx tests/app.spec.ts",
                    "node tests/server.js"):
        assert is_test_command(command), command
    for command in ("node src/server/server.js", "python src/main.py", "node -e 'console.log(1)'", "npm start"):
        assert not is_test_command(command), command
    assert not is_full_test_run("node test/game.test.js")            # one named file
    assert test_command_end("npm install && npm test || true") == len("npm install && npm test")
    assert test_command_end("node test/game.test.js; echo done") == len("node test/game.test.js")
    assert test_command_end("ls") is None
    assert not is_full_test_run("npm test || true") and is_full_test_run("npm install && npm test")


def test_the_hang_hint_of_a_busy_idle_or_unknown_test_run(tmp_path):
    from src.tools.project_links_check import test_hang_hint
    ws = _workspace(tmp_path, {"package.json": PACKAGE_JSON, "src/game/script.js": SCRIPT_JS,
                               "test/game.test.js": GAME_TEST_JS})
    output = "TAP version 13\n# Test 1: should initialize snake in the center\n"
    busy = test_hang_hint(ws, "npm test", 1.0, output)
    assert busy.startswith("\n[Framework hint - why the TEST run did not end:] the process was BUSY when it was stopped "
                           "(it used 100% of a CPU core), so it was not waiting for anything: the code is stuck in a loop")
    assert "It printed last: '# Test 1: should initialize snake in the center' - the code that runs right AFTER" in busy
    assert "open" not in busy.split("loop that never ends")[0]
    assert "It printed nothing" in test_hang_hint(ws, "node test/game.test.js", 0.97, "")
    idle = test_hang_hint(ws, "npm test", 0.0, output)
    assert idle.startswith("\n[Framework hint - the TEST process was IDLE (0% CPU) when it was stopped")
    assert "It printed last: '# Test 1: should initialize snake in the center'." in idle
    unknown = test_hang_hint(ws, "npm test", None, output)
    assert unknown.startswith("\n[Framework hint - a TEST run that does not end either loops forever or keeps something")
    assert test_hang_hint(ws, "node src/server/server.js", 1.0, output) == ""
    # an idle run that loads a server started on load still names the file
    _workspace(ws, {"src/server.js": "const http = require('http');\nconst s = http.createServer();\ns.listen(3000);\n",
                    "test/server.test.js": "const assert = require('assert');\nrequire('../src/server');\n"
                                           "describe('s', () => { it('ok', () => assert.ok(1)); });\n"})
    assert "test/server.test.js loads src/server.js, which STARTS THE SERVER" in test_hang_hint(ws, "npm test", 0.0, "")


def _executor_result(status, returncode=0, stdout="", stderr="", cpu_busy=None):
    return types.SimpleNamespace(status=status, returncode=returncode, stdout=stdout, stderr=stderr, message="",
                                 background_log=None, stdout_truncated=False, stderr_truncated=False, cpu_busy=cpu_busy)


def test_command_executor_explains_a_busy_timeout(tmp_path, monkeypatch):
    import src.tools.command_executor as ce
    from src.utils.test_runs import latest_test_run, verification_problem
    ws = _workspace(tmp_path / "sandbox", {"package.json": PACKAGE_JSON, "src/game/script.js": SCRIPT_JS,
                                           "test/game.test.js": GAME_TEST_JS})
    results = []

    async def fake_run(command, cwd, timeout, tool_name=None, agent_id=None):
        return results.pop(0)
    monkeypatch.setattr(ce, "run_sandboxed", fake_run)

    def execute(command, timeout=30):
        with quiet():
            return run(ce.CommandExecutionTool().execute(agent_id="W2", agent_sandbox_path=ws, scope="private",
                                                         action="run_command", command=command, timeout=timeout))

    # 21:24:28 W2: `node test/game.test.js` - before: "Interactive commands ... are not supported ... end the command with ' &'"
    results.append(_executor_result("timeout", stdout="Test 1: should initialize snake in the center\n", cpu_busy=1.0))
    answer = execute("node test/game.test.js")
    assert answer["message"].startswith("The TEST command timed out after 30 seconds and its process group was "
                                        "terminated: the test run did not END. Output so far (end):\nTest 1: should")
    assert "the process was BUSY" in answer["message"] and "Interactive" not in answer["message"]
    assert latest_test_run(ws) is None                                       # one named file: not a full run
    # 21:25:17 W2: `npm test`
    results.append(_executor_result("timeout", stdout="TAP version 13\n# Test 1: should initialize snake in the center\n",
                                    cpu_busy=1.0))
    answer = execute("npm test", timeout=60)
    assert "the process was BUSY" in answer["message"] and "keeps something open" not in answer["message"]
    run_record = latest_test_run(ws)
    assert run_record["outcome"] == "timeout" and run_record["cpu_busy"] == 1.0
    problem = verification_problem(ws, "Kick-off Task 9: Run all tests with npm test")
    assert "did not END - it timed out." in problem and "the process was BUSY" in problem
    # a busy command that is not a test: a loop, not a prompt or a server
    results.append(_executor_result("timeout", stdout="computing\n", cpu_busy=0.99))
    other = execute("node src/tools/build.js")
    assert other["message"].startswith("Command execution timed out after 30 seconds and its process group was terminated: "
                                       "the process was BUSY when it was stopped (it used 99% of a CPU core)")
    assert "Interactive" not in other["message"] and "with ' &', will not help" in other["message"]
    # an idle one keeps the old advice
    results.append(_executor_result("timeout", stdout="listening on 3001\n", cpu_busy=0.0))
    idle = execute("node src/server/server.js")
    assert "Interactive commands (nano, vim, input prompts) are not supported" in idle["message"]
    results.append(_executor_result("timeout", stdout="", cpu_busy=None))
    assert "Interactive commands" in execute("node src/server/server.js")["message"]


# ======================================================================================== 3. file checks
def test_an_unguarded_listen_next_to_a_guarded_one_is_found(tmp_path):
    from src.tools.project_links_check import starts_server_on_load, server_on_load_problems
    assert starts_server_on_load(SERVER_JS_BOTH) == "`server.listen(` at line 7"
    guarded_only = SERVER_JS_BOTH.replace(
        "server.listen(PORT, () => {\n  console.log(`Server running on port ${PORT}`);\n});\n\n", "")
    assert starts_server_on_load(guarded_only) is None
    assert starts_server_on_load("if (require.main === module) server.listen(3000);\n") is None
    assert starts_server_on_load("if (require.main === module) {\n  run();\n}\nserver.listen(3000);\n") \
        == "`server.listen(` at line 4"
    ws = _workspace(tmp_path, {"package.json": PACKAGE_JSON, "src/server/server.js": SERVER_JS_BOTH,
                               "test/server.test.js": "const assert = require('assert');\n"
                                                      "const { app } = require('../src/server/server');\n"
                                                      "describe('api', () => { it('app', () => assert.ok(app)); });\n"})
    assert server_on_load_problems(ws, "test/server.test.js")[0].startswith(
        "test/server.test.js loads src/server/server.js, which STARTS THE SERVER as soon as it is loaded "
        "(`server.listen(` at line 7)")


def test_a_test_file_that_replaces_process_is_found(tmp_path):
    from src.tools.project_links_check import process_override_problems, link_notes_text, link_problems
    problems = process_override_problems("test/game.test.js", GAME_TEST_JS)
    assert problems == ["test/game.test.js replaces Node's `process` object (`global.process =` at line 5): "
                        "process.exit() and the exit code no longer work, so a FAILING test run ends with exit code 0 (it "
                        "looks passed) or never ends. Do not assign global.process; to test code that calls process.exit, "
                        "pass a function in or stub only `process.exit` and restore it afterwards."]
    assert process_override_problems("src/game/script.js", GAME_TEST_JS) == []           # not a test file
    assert process_override_problems("test/a.test.js", "if (global.process == null) {}\n") == []
    assert process_override_problems("test/a.test.js", "// global.process = {};\n") == []
    assert process_override_problems("test/a.test.js", "process.exit = () => {};\n") == []  # a stub of exit only
    ws = _workspace(tmp_path, {"package.json": PACKAGE_JSON, "src/game/script.js": SCRIPT_JS,
                               "test/game.test.js": GAME_TEST_JS})
    assert "replaces Node's `process` object" in link_notes_text(ws, "test/game.test.js", GAME_TEST_JS)
    assert any("replaces Node's `process` object" in p for p in link_problems(ws, ws / "test" / "game.test.js"))


# ======================================================================================== 4. planned layout
def test_a_root_only_file_planned_in_a_directory_moves_to_the_root():
    from src.workflows.pm_kickoff_workflow import normalize_planned_layout, root_only_files
    dirs, files, notes = normalize_planned_layout(
        ["config", "src", "src/game", "test"], ["config/package.json", "src/game/script.js", "test/game.test.js"])
    assert files == ["package.json", "src/game/script.js", "test/game.test.js"]
    assert notes == ["Planned file 'config/package.json' moved to the workspace root ('package.json'): the project's tools "
                     "read it only there."]
    # two package.json files (client and server) or one at the root stay
    assert root_only_files(["client/package.json", "server/package.json"]) == (
        ["client/package.json", "server/package.json"], [])
    assert root_only_files(["package.json", "src/a.js"]) == (["package.json", "src/a.js"], [])
    assert root_only_files(["backend/requirements.txt", "backend/app.py"])[0] == ["requirements.txt", "backend/app.py"]


# ======================================================================================== 5. other workers' files
class Worker:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history, self.read_message_ids = [], {}


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


def _add(call, manager, text, assignee=None):
    args = {"assignee_agent_id": assignee} if assignee else {}
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert added["status"] == "success", added
    return added["task_uuid"]


TASK_2 = ("Kick-off Task 2: Implement core game logic including snake movement, food spawning, and collision detection "
          "in script.js")
TASK_3 = "Kick-off Task 3: Add player registration, socket connection handling, and multiplayer sync using Socket.IO in script.js"
TASK_4 = ("Kick-off Task 4: Implement score persistence in LocalStorage and real-time leaderboard display in index.html")
TASK_6 = "Kick-off Task 6: Write unit tests for game logic and snake movement in test/game.test.js"


@needs_tw
def test_a_worker_does_not_create_the_file_of_another_worker_s_task(tasks):
    from src.tools.project_management import foreign_file_creation_error
    from src.tools.executor import ToolExecutor
    w1, w2, pm1 = Worker("W1", state="worker_work"), Worker("W2"), Worker("PM1", "pm", "pm_manage")
    manager = _manager(pm1, w1, w2)
    t2 = _add(tasks, manager, TASK_2, "W1")
    t6 = _add(tasks, manager, TASK_6, "W2")
    w1.current_task_id = t2
    # 20:01:55 W1 wrote test/game.test.js
    refusal = foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "test/game.test.js")
    assert refusal.startswith(f"The file was NOT created: 'test/game.test.js' is the file of the task '{TASK_6}' ({t6}), "
                              "which is assigned to W2. Your task is 'Kick-off Task 2: Implement core game logic")
    assert "tell your PM with send_message" in refusal
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "./test/game.test.js") == refusal
    # its own task's file (named without a directory), files no task names, the PM and the framework: allowed
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "src/game/script.js") is None
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "src/game/helpers.js") is None
    assert foreign_file_creation_error(manager, "PM1", PROJECT, SESSION, "test/game.test.js") is None
    assert foreign_file_creation_error(manager, "framework", PROJECT, SESSION, "test/game.test.js") is None
    # the executor's gate: only a NEW file, only 'write' in the shared workspace
    gate = ToolExecutor._foreign_file_creation_message
    args = {"action": "write", "filename": "test/game.test.js", "content": "x"}
    assert gate(args, "W1", PROJECT, SESSION, manager) == refusal
    assert gate(dict(args, action="write_file>"), "W1", PROJECT, SESSION, manager) == refusal
    assert gate(dict(args, scope="private"), "W1", PROJECT, SESSION, manager) is None
    assert gate(dict(args, action="read"), "W1", PROJECT, SESSION, manager) is None
    # through the tool executor: refused before the file system tool runs
    with quiet():
        answer = run(ToolExecutor().execute_tool("W1", tasks.workspace.parent, "file_system", dict(args),
                                                 project_name=PROJECT, session_name=SESSION, manager=manager))
    assert str(answer).startswith("Error from tool 'file_system': The file was NOT created: 'test/game.test.js'"), answer
    assert not (tasks.workspace / "test" / "game.test.js").exists()
    with quiet():
        answer = run(ToolExecutor().execute_tool("W1", tasks.workspace.parent, "file_system",
                                                 {"action": "write", "filename": "src/game/script.js", "content": "x = 1;\n"},
                                                 project_name=PROJECT, session_name=SESSION, manager=manager))
    assert (tasks.workspace / "src" / "game" / "script.js").is_file(), answer
    _workspace(tasks.workspace, {"test/game.test.js": "// W2's test\n"})
    assert gate(args, "W1", PROJECT, SESSION, manager) is None              # changing an existing file stays allowed
    # an unassigned task that is not part of the plan does not own its file
    unassigned = _add(tasks, manager, "Write README.md in docs/README.md")
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "docs/README.md") is None
    assert unassigned
    # Round 3y: an unassigned PLANNED task does (2026-10-27: W1 wrote tests/test_game.js of the unassigned Kick-off Task 6)
    _add(tasks, manager, "Kick-off Task 7: Write the guide in docs/GUIDE.md")
    refusal = foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "docs/GUIDE.md")
    assert refusal and "not assigned yet" in refusal


@needs_tw
def test_a_worker_without_a_known_task_or_a_worker_s_own_next_task_is_not_refused(tasks):
    from src.tools.project_management import foreign_file_creation_error
    w1, w2 = Worker("W1", state="worker_work"), Worker("W2")
    manager = _manager(w1, w2)
    _add(tasks, manager, TASK_2, "W1")
    _add(tasks, manager, TASK_4, "W1")
    _add(tasks, manager, TASK_6, "W2")
    # W1 has two open tasks and no current task id: its task is unknown
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "test/game.test.js") is None


@needs_tw
def test_a_sub_task_for_the_worker_s_own_next_task_is_allowed(tasks):
    # 20:12:24 W2 decomposed Kick-off Task 3 (script.js); index.html belonged to Kick-off Task 4, also W2's
    w1, w2, pm1 = Worker("W1"), Worker("W2", state="worker_decompose"), Worker("PM1", "pm", "pm_manage")
    manager = _manager(pm1, w1, w2)
    t3 = _add(tasks, manager, TASK_3, "W2")
    _add(tasks, manager, TASK_4, "W2")
    w2.current_task_id = t3
    sub = tasks("W2", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W2",
                description="Add player registration form to index.html with name input field and registration button")
    assert sub.get("error_type") != "foreign_files", sub
    # a file of ANOTHER worker's task is still refused
    _add(tasks, manager, TASK_6, "W1")
    refused = tasks("W2", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W2",
                    description="Write registration tests in test/game.test.js")
    assert refused.get("error_type") == "foreign_files", refused


# ======================================================================================== 6. a stream that stops
def test_stream_stall_timeout():
    from src.llm_providers import ollama_provider as op
    key = "http://ollama|qwen3.5:4b-q4_K_M"
    assert op.stream_stall_timeout(key, 1000) == op.DEFAULT_READ_TIMEOUT          # no rate known yet
    assert op.note_output_rate(key, {"eval_count": 948, "eval_duration": 11.2e9}) == pytest.approx(84.64, abs=0.01)
    assert op.note_output_rate(key, {"eval_count": 5, "eval_duration": 1e9}) is None   # too short to tell
    assert op.note_output_rate(key, {"eval_count": 100}) is None
    # the 2nd summary (num_predict 1000) at 85 tok/s: 1000 / 84.6 x 1.5 + 60 = 78 s -> the minimum, 180 s (was 1800 s)
    assert op.stream_stall_timeout(key, 1000) == 180.0
    # a full 8192-token reply at the SLOWEST rate seen
    op.note_output_rate(key, {"eval_count": 100, "eval_duration": 25e9})       # 4 tok/s
    assert op.stream_stall_timeout(key, 8192) == op.DEFAULT_READ_TIMEOUT       # 8192/4*1.5+60 > 1800
    assert op.stream_stall_timeout(key, 600) == 285.0                           # 600/4*1.5+60
    assert op.stream_stall_timeout(key, None) == op.DEFAULT_READ_TIMEOUT


def test_a_stream_that_stops_after_it_started_ends_after_the_stall_timeout(monkeypatch):
    from aiohttp import web
    from src.llm_providers import ollama_provider as op
    from src.llm_providers.ollama_provider import OllamaProvider
    monkeypatch.setattr(op, "STREAM_STALL_MIN_SECONDS", 0.5)
    monkeypatch.setattr(op, "STREAM_STALL_MARGIN_SECONDS", 0.0)

    async def chat(request):
        await request.json()
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await response.prepare(request)
        for word in ("W2 rewrote ", "src/game/script.js "):
            await response.write((json.dumps({"message": {"role": "assistant", "content": word}, "done": False})
                                  + "\n").encode())
        await asyncio.sleep(3)   # the server stops sending (20:53:51)
        return response

    registry = types.SimpleNamespace(get_model_info=lambda model: {"model_capabilities": ["completion"]})

    async def go():
        app = web.Application()
        app.router.add_route("POST", "/api/chat", chat)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        try:
            provider = OllamaProvider(base_url=url, model_registry=registry)
            op.note_output_rate(f"{url}|m", {"eval_count": 1000, "eval_duration": 1e9})
            started = time.monotonic()
            events = [e async for e in provider.stream_completion(
                messages=[{"role": "user", "content": "summarize"}], model="m", temperature=0.3, max_tokens=64)]
            return events, time.monotonic() - started
        finally:
            await runner.cleanup()

    with quiet():
        events, seconds = run(go())
    text = "".join(e["content"] for e in events if e.get("type") == "response_chunk")
    errors = [e["content"] for e in events if e.get("type") == "error"]
    assert text == "W2 rewrote src/game/script.js "
    assert errors and errors[0].startswith("[Ollama Error]: Stream stalled - no data for 0.5s")
    assert seconds < 2.5, seconds


# ======================================================================================== 7. the summarizer
def _summarizer(provider):
    from src.agents.cycle_components.context_summarizer import ContextSummarizer
    from src.agents.constants import CONSTITUTIONAL_GUARDIAN_AGENT_ID
    cg = types.SimpleNamespace(llm_provider=provider, model="m", message_history=["the CG's own history"])
    return ContextSummarizer(types.SimpleNamespace(agents={CONSTITUTIONAL_GUARDIAN_AGENT_ID: cg})), cg


def test_a_summary_call_that_hangs_is_given_up(monkeypatch):
    import src.agents.cycle_components.context_summarizer as cs

    class Hanging:
        async def stream_completion(self, model, messages, temperature, max_tokens=None, **kwargs):
            yield {"type": "response_chunk", "content": "W2 rewrote"}
            await asyncio.sleep(30)
            yield {"type": "response_chunk", "content": " never"}
    monkeypatch.setattr(cs, "SUMMARY_TIMEOUT_SECONDS", 0.3)
    summarizer, cg = _summarizer(Hanging())
    chunk = [{"role": "user", "content": "run the tests"}, {"role": "assistant", "content": "ok"}]
    started = time.monotonic()
    with quiet():
        assert run(summarizer._summarize_chunk(chunk, 2, "W2")) is None
    assert time.monotonic() - started < 3
    assert cg.message_history == ["the CG's own history"]                     # restored


def test_a_summary_that_was_cut_off_is_marked():
    from src.agents.cycle_components.context_summarizer import SUMMARY_CUT_OFF_NOTE

    class Stalled:
        async def stream_completion(self, model, messages, temperature, max_tokens=None, **kwargs):
            yield {"type": "response_chunk", "content": "W2 rewrote src/game/script.js and ran the tests"}
            yield {"type": "error", "content": "[Ollama Error]: Stream stalled - no data for 180.0s."}
    summarizer, cg = _summarizer(Stalled())
    with quiet():
        summary = run(summarizer._summarize_chunk([{"role": "user", "content": "x"}], 2, "W2"))
    assert summary == "W2 rewrote src/game/script.js and ran the tests" + SUMMARY_CUT_OFF_NOTE
    assert cg.message_history == ["the CG's own history"]



def test_the_worker_prompt_names_the_refusal():
    import yaml
    prompts = yaml.safe_load((REPO / "prompts.yaml").read_text(encoding="utf-8"))
    assert ("Only create/modify files directly related to YOUR assigned role and task. Creating a file that a task of "
            "ANOTHER worker names is refused by the framework.") in prompts["worker_work_prompt"]

# END OF FILE tests/unit/test_round3f.py
