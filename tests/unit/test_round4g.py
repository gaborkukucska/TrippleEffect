# START OF FILE tests/unit/test_round4g.py
"""
Round 4g: the run of 2026-10-30 01:03-02:47 (qwen3.5:4b-q4_K_M, WiFi_Snake_Game: JavaScript game, Python server). Tasks 1-5
were finished by 02:09; the three test tasks (6, 7, 8) were still open at the end, and Tasks 9 and 10 waited for them.

* The framework's own note in "Kick-off Task 6: Write pytest tests for game logic in tests/game.test.js [Framework note: the
  code these tests check is JavaScript (src/game.js), so they are JavaScript tests (tests/game.test.js, run with `node --test
  tests/*.test.js`) ...]" made the test-WRITING tasks tasks that RUN the tests ("run with `node --test") and tasks that name
  src/game.js: W3 and W4 changed the finished src/game.js and src/leaderboard.js of W1 and W2 to fit their tests (W4 eight
  times), and the run-all Task 10 counted as a one-file task (the note's `tests/*.test.js`).
* 02:01 and 02:18 PM1's finish of the player and leaderboard test tasks was refused with "the last test run (`node --test
  tests/game.test.js 2>&1`) did not END" - a run of ANOTHER worker's file.
* tests/game.test.js called `afterEach(() => { stopGameLoop(); })` INSIDE its tests, without taking afterEach from node:test:
  the timer of startGameLoop() kept every run open from 01:27 to the end, and the framework only gave its general hint.
* tests/player.test.js called `after(...)` without taking it from node:test; the hint said "Define it in the test file".
* tests/leaderboard.test.js declared `const leaderboard = [...]` inside five tests, hiding the module `leaderboard`:
  "leaderboard.formatLeaderboard is not a function".
* The structure planned tests/test_game/test_game.py and the task tests/test_game.py: both were renamed to JavaScript tests
  (tests/test_game/game.test.js and tests/game.test.js) and PROJECT_STRUCTURE.md listed both.
* 01:19:46 `cd /shared && node --test tests/game.test.js` failed in its `cd` and counted as a failed test run.
"""
import asyncio
import logging
import shutil
import subprocess
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Game", "startup_1793293435"
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
    root = Path(root)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


NOTE_6 = ("[Framework note: the code these tests check is JavaScript (src/game.js), so they are JavaScript tests "
          "(tests/game.test.js, run with `node --test tests/*.test.js`), not pytest tests; pytest tests only the Python files "
          "(scripts/server.py). Never write a Python copy of the JavaScript code.]")
TASK_1 = ("Kick-off Task 1: Implement core game logic (snake movement, food generation, collision detection, score tracking) "
          "in src/game.js")
TASK_6 = f"Kick-off Task 6: Write pytest tests for game logic in tests/game.test.js {NOTE_6}"
TASK_7 = ("Kick-off Task 7: Write pytest tests for player management in tests/player.test.js "
          + NOTE_6.replace("src/game.js", "src/player.js").replace("tests/game.test.js", "tests/player.test.js"))
TASK_8 = ("Kick-off Task 8: Write pytest tests for leaderboard functionality in tests/leaderboard.test.js "
          + NOTE_6.replace("src/game.js", "src/leaderboard.js").replace("tests/game.test.js", "tests/leaderboard.test.js"))
TASK_10 = ("Kick-off Task 10: Run pytest tests and start the server to verify that everything works correctly [Framework "
           "note: the tests of this project are JavaScript: run them with `node --test tests/*.test.js`, not pytest.]")

GAME_JS = """let gameLoopInterval = null;
let score = 0;
function updateGame() { score += 1; }
function startGameLoop() {
    gameLoopInterval = setInterval(() => {
        updateGame();
    }, 100);
}
function stopGameLoop() {
    if (gameLoopInterval) {
        clearInterval(gameLoopInterval);
        gameLoopInterval = null;
    }
}
function getScore() { return score; }
module.exports = { startGameLoop, stopGameLoop, updateGame, getScore };
"""
# the run's tests/game.test.js lines 1, 362-405 (shortened)
GAME_TEST_JS = """const { startGameLoop, stopGameLoop, getScore } = require('../src/game.js');
const { describe, test } = require('node:test');
const assert = require('node:assert');

describe('Game Logic Tests', () => {
    describe('startGameLoop', () => {
        test('should start game loop with 10 FPS', () => {
            startGameLoop();
            assert.strictEqual(getScore(), 0);

            afterEach(() => {
                stopGameLoop();
            });
        });
    });
});
"""
PLAYER_JS = """const players = new Map();
function createPlayer(name, score = 0) {
    if (players.has(name)) { return players.get(name); }
    const player = { name, score };
    players.set(name, player);
    return player;
}
module.exports = { createPlayer };
"""
# the run's tests/player.test.js at 01:26:47: `after` taken from nowhere
PLAYER_TEST_JS = """const { describe, it } = require('node:test');
const assert = require('node:assert');
const playerModule = require('../src/player.js');

after(() => {
    delete global.localStorage;
});

describe('Player Management', () => {
    it('creates a player', () => {
        assert.strictEqual(playerModule.createPlayer('Alice').name, 'Alice');
    });
});
"""
LEADERBOARD_JS = """function formatLeaderboard(leaderboard) {
    return leaderboard.map((entry, i) => `${i + 1}. ${entry.name}`);
}
function getLeaderboardJSON(leaderboard) {
    return JSON.stringify(leaderboard);
}
module.exports = { formatLeaderboard, getLeaderboardJSON };
"""
# the run's tests/leaderboard.test.js lines 37, 225-232 (shortened)
LEADERBOARD_TEST_JS = """const { describe, it } = require('node:test');
const assert = require('assert');
const leaderboard = require('../src/leaderboard');

describe('Leaderboard Tests', function() {
    describe('formatLeaderboard', function() {
        it('should format leaderboard with correct rank emojis', function() {
            const leaderboard = [
                { name: 'Alice', score: 100 },
                { name: 'Bob', score: 50 }
            ];
            const result = leaderboard.formatLeaderboard(leaderboard);
            assert.strictEqual(result[0], '1. Alice');
        });
    });
    describe('getLeaderboardJSON', function() {
        it('should return valid JSON string', function() {
            assert.strictEqual(leaderboard.getLeaderboardJSON([]), '[]');
        });
    });
});
"""


# ======================================================================================== 1. the framework's notes in task texts
def test_the_framework_notes_are_not_the_task():
    from src.utils.program_run_check import is_program_run_task
    from src.utils.task_files import file_paths_in_text, strip_framework_notes
    from src.utils.test_failure_routing import is_verification_only_task, says_to_fix
    from src.utils.test_runs import is_test_run_task
    from src.utils.test_task_check import is_test_writing_task, named_directories
    assert strip_framework_notes(TASK_6) == "Kick-off Task 6: Write pytest tests for game logic in tests/game.test.js"
    assert file_paths_in_text(TASK_6) == ["tests/game.test.js"]
    assert file_paths_in_text(TASK_6, keep_framework_notes=True) == ["tests/game.test.js", "src/game.js", "scripts/server.py"]
    for text in (TASK_6, TASK_7, TASK_8):
        assert is_test_writing_task(text) and not is_test_run_task(text) and not says_to_fix(text), text
        assert not is_program_run_task(text) and named_directories(text) == []
    # the run-all task: the note's `tests/*.test.js` is no test file of the task
    assert is_test_run_task(TASK_10) and is_verification_only_task(TASK_10) and is_program_run_task(TASK_10)
    # the task's own words still count
    assert is_test_run_task("Run `node --test tests/leaderboard.test.js` and fix any test failures")
    assert file_paths_in_text("Fix src/game.js [Framework note - the file WAS saved:] tests/x.test.js") == [
        "src/game.js", "tests/x.test.js"]


def test_the_code_map_still_shows_the_code_a_test_task_checks(tmp_path):
    from src.utils.code_map import code_map_text
    root = _workspace(tmp_path, {"src/game.js": GAME_JS})
    text = code_map_text(root, TASK_6)
    assert "src/game.js" in text


# ======================================================================================== 2. tasks (real Taskwarrior)
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
    from src.utils import file_authorship
    from src.utils.test_runs import clear_test_runs
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    workspace.mkdir(parents=True)
    file_authorship.reset()
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
    file_authorship.reset()


def _add(call, manager, text, assignee=None):
    args = {"assignee_agent_id": assignee} if assignee else {}
    result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert result["status"] == "success", result
    return result["task_uuid"]


@needs_tw
def test_a_test_writer_does_not_change_the_code_it_tests(tasks):
    from src.tools.project_management import foreign_file_change_error
    w1, w3 = Agent("W1"), Agent("W3", state="worker_work")
    manager = _manager(w1, w3)
    _workspace(tasks.workspace, {"src/game.js": GAME_JS, "tests/game.test.js": GAME_TEST_JS})
    task1 = _add(tasks, manager, TASK_1, "W1")
    assert tasks("framework", manager, action="modify_task", task_id=task1, task_progress="finished")["status"] == "success"
    w3.active_task_id = _add(tasks, manager, TASK_6, "W3")
    with quiet():
        refused = foreign_file_change_error(manager, "W3", PROJECT, SESSION, "src/game.js")       # 01:16:51 and 02:03:04
        assert refused and refused.startswith("'src/game.js' was NOT changed: it is the file of the FINISHED task "
                                              "'Kick-off Task 1: Implement core game logic")
        assert foreign_file_change_error(manager, "W3", PROJECT, SESSION, "tests/game.test.js") is None
        assert foreign_file_change_error(manager, "W1", PROJECT, SESSION, "src/game.js") is None


# ======================================================================================== 3. the run of the task's OWN files
NODE_TIMEOUT_TAIL = """    ok 10 - gameController
not ok 1 - Game Logic Tests
  failureType: 'subtestsFailed'
  error: '6 subtests failed'
"""


def test_a_task_that_names_its_test_files_is_judged_by_their_runs(tmp_path):
    from src.utils.test_runs import (FAILED, PASSED, TIMEOUT, clear_test_runs, latest_file_run, record_test_run,
                                     verification_problem)
    ws = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/game.test.js": GAME_TEST_JS, "src/player.js": PLAYER_JS,
                               "tests/player.test.js": PLAYER_TEST_JS, "src/leaderboard.js": LEADERBOARD_JS,
                               "tests/leaderboard.test.js": LEADERBOARD_TEST_JS})
    clear_test_runs()
    fix_players = "Run `node --test tests/player.test.js` and fix the failing player tests"
    with quiet():
        record_test_run(ws, "node --test tests/player.test.js", PASSED, "# pass 19", output="1..1\n# tests 19\n# pass 19\n")
        record_test_run(ws, "node --test tests/game.test.js 2>&1", TIMEOUT, NODE_TIMEOUT_TAIL)                 # 02:01:42
    assert latest_file_run(ws, "tests/player.test.js")["outcome"] == PASSED
    assert verification_problem(ws, fix_players) is None
    problem = verification_problem(ws, "Fix the hanging tests of tests/game.test.js")
    assert problem.startswith("the tests do NOT pass: the last run of tests/game.test.js (`node --test tests/game.test.js "
                              "2>&1`) did not END - it timed out.")
    assert "calls `startGameLoop()`" in problem  # the timer hint (section 4)
    assert verification_problem(ws, "Fix the failing tests in tests/leaderboard.test.js") == (
        "no run of tests/leaderboard.test.js with the CURRENT test and code files has passed yet. Run `node --test "
        "tests/leaderboard.test.js` with command_executor, fix what fails, and mark the task finished when that run passes.")
    # a change of the code the test loads asks for a new run; a change of another worker's file does not
    (ws / "src" / "game.js").write_text(GAME_JS + "\n// changed\n", encoding="utf-8")
    assert verification_problem(ws, fix_players) is None
    (ws / "src" / "player.js").write_text(PLAYER_JS + "\n// changed\n", encoding="utf-8")
    assert verification_problem(ws, fix_players).startswith("no run of tests/player.test.js with the CURRENT")
    # a run of several files whose output names the failing one: the others passed
    node_output = ("not ok 1 - tests/player.test.js\n  ---\n  location: 'tests/player.test.js:1:1'\n  failureType: "
                   "'testCodeFailure'\n  ...\nok 2 - leaderboard\n# tests 3\n# pass 2\n# fail 1\n")
    with quiet():
        record_test_run(ws, "node --test tests/player.test.js tests/leaderboard.test.js", FAILED, "# fail 1",
                        output=node_output)
    assert latest_file_run(ws, "tests/leaderboard.test.js")["outcome"] == PASSED
    assert latest_file_run(ws, "tests/player.test.js")["outcome"] == FAILED
    assert verification_problem(ws, "Fix the failing tests in tests/leaderboard.test.js") is None
    clear_test_runs()


def test_a_failed_cd_is_no_test_run(tmp_path):
    from src.utils.test_runs import FAILED, clear_test_runs, latest_any_test_run, record_test_run
    ws = _workspace(tmp_path, {"tests/game.test.js": GAME_TEST_JS})
    clear_test_runs()
    with quiet():
        record_test_run(ws, "cd /shared && node --test tests/game.test.js", FAILED, "cd: can't cd",           # 01:19:46
                        output="/bin/sh: 1: cd: can't cd to /shared\n")
    assert latest_any_test_run(ws) is None
    with quiet():
        record_test_run(ws, "cd tests && node --test game.test.js", FAILED, "# fail 1", output="# tests 1\n# fail 1\n")
    assert latest_any_test_run(ws)["outcome"] == FAILED
    clear_test_runs()


# ======================================================================================== 4. a hook inside a test never runs
def test_a_hook_inside_a_test_does_not_stop_the_timer(tmp_path):
    from src.utils.code_map import open_timer_problems
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/game.test.js": GAME_TEST_JS})
    assert open_timer_problems(root, "tests/game.test.js") == [
        "tests/game.test.js calls `startGameLoop()` (the test at line 7), which starts a setInterval/setTimeout in "
        "src/game.js (line 4), and does not stop it every time: an open timer keeps Node running, so the test run NEVER ends. "
        "To fix: call `stopGameLoop()` in an afterEach()/after() hook (take afterEach from require('node:test')). Line 11 "
        "calls a hook INSIDE a test: there it never runs - call afterEach() once, at the top of the describe() block, beside "
        "the tests. `afterEach` is not taken from anywhere: add it to `require('node:test')` at the top of the file."]
    # the hook at the top of the describe block, taken from node:test: no problem
    fixed = (GAME_TEST_JS.replace("const { describe, test }", "const { describe, test, afterEach }")
             .replace("            afterEach(() => {\n                stopGameLoop();\n            });\n", "")
             .replace("describe('Game Logic Tests', () => {\n",
                      "describe('Game Logic Tests', () => {\n    afterEach(() => { stopGameLoop(); });\n"))
    _workspace(root, {"tests/game.test.js": fixed})
    assert open_timer_problems(root, "tests/game.test.js") == []
    # a hook in the right place that nothing provides does not run either
    _workspace(root, {"tests/game.test.js": fixed.replace("const { describe, test, afterEach }", "const { describe, test }")})
    assert open_timer_problems(root, "tests/game.test.js")[0].endswith(
        "`afterEach` is not taken from anywhere: add it to `require('node:test')` at the top of the file.")


@needs_node
def test_node_really_hangs_and_ends_once_the_hook_is_in_place(tmp_path):
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/game.test.js": GAME_TEST_JS.replace(
        "const { describe, test }", "const { describe, test, afterEach }")})
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(["node", "--test", "tests/game.test.js"], cwd=root, capture_output=True, timeout=8)
    fixed = (GAME_TEST_JS.replace("const { describe, test }", "const { describe, test, afterEach }")
             .replace("            afterEach(() => {\n                stopGameLoop();\n            });\n", "")
             .replace("describe('Game Logic Tests', () => {\n",
                      "describe('Game Logic Tests', () => {\n    afterEach(() => { stopGameLoop(); });\n"))
    _workspace(root, {"tests/game.test.js": fixed})
    done = subprocess.run(["node", "--test", "tests/game.test.js"], cwd=root, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0


# ======================================================================================== 5. node:test's before / after
def test_after_and_after_all_come_from_node_test(tmp_path):
    from src.utils.js_test_checks import missing_runner_globals, runner_globals_advice
    from src.utils.test_diagnosis import output_hints
    root = _workspace(tmp_path, {"src/player.js": PLAYER_JS, "tests/player.test.js": PLAYER_TEST_JS})
    assert missing_runner_globals(root, "tests/player.test.js") == ["after"]
    hint = next(h for h in output_hints(root, "# ReferenceError: after is not defined\n#     at Object.<anonymous> "
                                              "(tests/player.test.js:5:1)") if "after" in h)
    assert "add `const { after } = require('node:test');` at the top of the file" in hint
    jest_style = PLAYER_TEST_JS.replace("after(() =>", "afterAll(() =>")
    _workspace(root, {"tests/player.test.js": jest_style})
    assert missing_runner_globals(root, "tests/player.test.js") == ["afterAll"]
    advice = runner_globals_advice(root, "tests/player.test.js", ["afterAll"])
    assert "add `const { after } = require('node:test');`" in advice
    assert "node:test has no `afterAll`: write `after` instead" in advice


# ======================================================================================== 6. a local that hides the module
def test_a_local_variable_that_hides_the_module_is_named(tmp_path):
    from src.utils.js_test_checks import shadowed_module_hints
    root = _workspace(tmp_path, {"src/leaderboard.js": LEADERBOARD_JS, "tests/leaderboard.test.js": LEADERBOARD_TEST_JS})
    assert shadowed_module_hints(root, "tests/leaderboard.test.js") == [
        "tests/leaderboard.test.js line 8 declares a local `leaderboard` (`const leaderboard = ...`) that HIDES the module "
        "`leaderboard` (line 3, src/leaderboard.js): in that test `leaderboard.formatLeaderboard(...)` is looked up on the "
        "local value, so it \"is not a function\" although src/leaderboard.js may export it. Give the local value another "
        "name (e.g. `leaderboardData`). The code under test is not wrong for this."]
    renamed = LEADERBOARD_TEST_JS.replace("const leaderboard = [", "const entries = [").replace(
        "leaderboard.formatLeaderboard(leaderboard)", "leaderboard.formatLeaderboard(entries)")
    _workspace(root, {"tests/leaderboard.test.js": renamed})
    assert shadowed_module_hints(root, "tests/leaderboard.test.js") == []


@needs_node
def test_the_diagnosis_of_the_real_run_names_the_hidden_module(tmp_path):
    from src.utils.test_diagnosis import js_test_mistake_hints
    root = _workspace(tmp_path, {"src/leaderboard.js": LEADERBOARD_JS, "tests/leaderboard.test.js": LEADERBOARD_TEST_JS})
    out = subprocess.run(["node", "--test", "tests/leaderboard.test.js"], cwd=root, capture_output=True, text=True,
                         timeout=60)
    assert "leaderboard.formatLeaderboard is not a function" in out.stdout
    assert any("HIDES the module `leaderboard`" in hint for hint in js_test_mistake_hints(root, out.stdout))


# ======================================================================================== 7. a JavaScript test task is RUN
@needs_node
def test_a_javascript_test_task_must_load_end_and_call_real_code(tmp_path, monkeypatch):
    from src.utils import test_task_check
    from src.utils.test_task_check import test_task_problem
    monkeypatch.setattr(test_task_check, "COMPLETION_TEST_TIMEOUT", 10.0)
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "src/player.js": PLAYER_JS, "src/leaderboard.js": LEADERBOARD_JS,
                                 "tests/game.test.js": GAME_TEST_JS.replace("const { describe, test }",
                                                                            "const { describe, test, afterEach }"),
                                 "tests/player.test.js": PLAYER_TEST_JS.replace("const { describe, it }",
                                                                                "const { describe, it, after }"),
                                 "tests/leaderboard.test.js": LEADERBOARD_TEST_JS})
    with quiet():
        hang = run(test_task_problem(root, TASK_6))
        assert hang.startswith("its tests do not END: `node --test tests/game.test.js` was still running after 10 s.")
        assert "calls `startGameLoop()`" in hang and "INSIDE a test" in hang
        wrong = run(test_task_problem(root, TASK_8))
        assert wrong.startswith("its tests call code that does NOT exist where they call it, so they fail before they test "
                                "anything (`node --test tests/leaderboard.test.js`):\n- tests/leaderboard.test.js line 12: "
                                "TypeError: leaderboard.formatLeaderboard is not a function\nWhy:\n- tests/leaderboard."
                                "test.js line 8 declares a local `leaderboard`")
        assert wrong.endswith("A test that fails because the code computes a WRONG result is fine here - name that bug in "
                              "your report to the PM.")
        # loads, ends and calls real code: a failing RESULT may stay (the run-all task routes it to the code's author)
        _workspace(root, {"tests/player.test.js": PLAYER_TEST_JS.replace("const { describe, it }", "const { describe, it, "
                                                                         "after }").replace("'Alice').name, 'Alice'",
                                                                                            "'Alice').name, 'Bob'")})
        assert run(test_task_problem(root, TASK_7)) is None
        # a file that does not even load
        _workspace(root, {"tests/player.test.js": PLAYER_TEST_JS.replace("after(() => {", "before(() => {")})
        load = run(test_task_problem(root, TASK_7))
    assert load.startswith("its tests cannot run: tests/player.test.js calls `before` but nothing provides it")


@needs_node
def test_a_file_that_does_not_load_is_named_from_the_node_output(tmp_path, monkeypatch):
    from src.utils import test_task_check
    monkeypatch.setattr(test_task_check, "COMPLETION_TEST_TIMEOUT", 20.0)
    root = _workspace(tmp_path, {"src/player.js": PLAYER_JS,
                                 "tests/player.test.js": "const { test } = require('node:test');\n"
                                                         "const player = require('../src/players.js');\n"
                                                         "test('x', () => {});\n"})
    with quiet():
        problem = run(test_task_check.javascript_run_problem(root, ["tests/player.test.js"], TASK_7))
    assert problem.startswith("its test file does not even LOAD (`node --test tests/player.test.js`), so no test in it "
                              "runs:\n- tests/player.test.js: Error: Cannot find module '../src/players.js'")
    assert problem.endswith("Fix the test file; the code under test is not wrong for this.")


@needs_tw
@needs_node
def test_the_pm_finishes_the_player_tests_while_the_game_tests_hang(tasks, monkeypatch):
    from src.utils import test_task_check
    from src.utils.test_runs import TIMEOUT, record_test_run
    monkeypatch.setattr(test_task_check, "COMPLETION_TEST_TIMEOUT", 20.0)
    pm1, w1 = Agent("PM1", "pm", "pm_report_check"), Agent("W1", state="worker_wait")
    manager = _manager(pm1, w1)
    _workspace(tasks.workspace, {"src/game.js": GAME_JS, "tests/game.test.js": GAME_TEST_JS, "src/player.js": PLAYER_JS,
                                 "tests/player.test.js": PLAYER_TEST_JS.replace("const { describe, it }",
                                                                                "const { describe, it, after }")})
    task7 = _add(tasks, manager, TASK_7, "W1")
    with quiet():
        record_test_run(tasks.workspace, "node --test tests/game.test.js 2>&1", TIMEOUT, NODE_TIMEOUT_TAIL)
    result = tasks("PM1", manager, action="modify_task", task_id=task7, task_progress="finished")             # 02:01:42
    assert result["status"] == "success", result


# ======================================================================================== 8. the kick-off of 01:05:59
STRUCTURE = [
    {"dir": "src", "children": [{"dir": "game", "files": ["game.js"]}, {"dir": "player", "files": ["player.js"]},
                                {"dir": "leaderboard", "files": ["leaderboard.js"]}]},
    {"dir": "public", "files": ["index.html", "styles.css"]},
    {"dir": "scripts", "files": ["server.py"]},
    {"dir": "tests", "children": [{"dir": "test_game", "files": ["test_game.py"]},
                                  {"dir": "test_player", "files": ["test_player.py"]},
                                  {"dir": "test_leaderboard", "files": ["test_leaderboard.py"]}]},
    {"dir": "docs", "files": ["README.md"]},
    {"dir": "config"},
]
PLAN_TASKS = [
    {"id": "task_1", "depends_on": [], "description": TASK_1.split(": ", 1)[1]},
    {"id": "task_2", "depends_on": ["task_1"], "description": "Implement player management with naming and score storage "
                                                              "using localStorage in src/player.js"},
    {"id": "task_3", "depends_on": ["task_1", "task_2"], "description": "Implement leaderboard display and ranking logic in "
                                                                        "src/leaderboard.js"},
    {"id": "task_5", "depends_on": [], "description": "Write Python HTTP server script in scripts/server.py to serve the game "
                                                      "on localhost for mobile devices"},
    {"id": "task_6", "depends_on": ["task_1"], "description": "Write pytest tests for game logic in tests/test_game.py"},
    {"id": "task_7", "depends_on": ["task_2"], "description": "Write pytest tests for player management in "
                                                              "tests/test_player.py"},
    {"id": "task_8", "depends_on": ["task_3"], "description": "Write pytest tests for leaderboard functionality in "
                                                              "tests/test_leaderboard.py"},
    {"id": "task_10", "depends_on": ["task_6", "task_7", "task_8"],
     "description": "Run pytest tests and start the server to verify that everything works correctly"},
]


def test_the_kickoff_plans_each_javascript_test_once(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    import src.workflows.pm_kickoff_workflow as pkw
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    monkeypatch.setattr(pmt, "TASKLIB_AVAILABLE", False)
    ws = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    ws.mkdir(parents=True)
    calls = []

    class Executor:
        async def execute_tool(self, **kwargs):
            calls.append(kwargs["tool_args"])
            return {"status": "success", "task_id": len(calls), "task_uuid": f"00000000-0000-0000-0000-{len(calls):012d}"}

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)

    class PM:
        agent_id, sandbox_path, message_history = "PM1", tmp_path, []
        agent_config = {"config": {"project_name_context": PROJECT}}

        def clear_history(self):
            self.message_history = []
    plan = {"roles": ["Coder"], "tasks": PLAN_TASKS, "project_structure": STRUCTURE,
            "code_base_definitions": "Tech stack: JavaScript (ES6+), Node.js, Python, pytest."}
    manager = types.SimpleNamespace(current_project=PROJECT, current_session=SESSION, tool_executor=Executor())
    with quiet():
        result = run(pkw.PMKickoffWorkflow().execute(manager, PM(), plan))
    assert result.success
    task_6 = next(c for c in calls if c.get("description", "").startswith("Kick-off Task 5:"))
    assert task_6["description"].startswith("Kick-off Task 5: Write pytest tests for game logic in tests/game.test.js")
    md = (ws / "PROJECT_STRUCTURE.md").read_text(encoding="utf-8")
    files_to_create = md.split("## Files To Create", 1)[1]
    for name in ("game", "player", "leaderboard"):
        assert f"- tests/{name}.test.js" in files_to_create
        assert f"tests/test_{name}/" not in md and f"test_{name}/" not in md
        assert not (ws / "tests" / f"test_{name}").exists()
    assert (ws / "tests").is_dir() and (ws / "conftest.py").exists()  # scripts/server.py is Python

# END OF FILE tests/unit/test_round4g.py
