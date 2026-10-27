# START OF FILE tests/unit/test_round3m.py
"""
Round 3m: the run of 2026-10-24 21:53-23:09 (WiFi_Multiplayer_Snake, JavaScript, no package.json).

* tests/game.test.js was its own small test runner, run with `node tests/game.test.js`. Line 88,
  `if (!gameState.isRunning) { throw new Error('Expected isRunning to be false initially'); }`, throws exactly when isRunning
  IS false. From 22:15 to 23:09 W1 (and PM1, who rewrote the task as "set isRunning to false (line 53 in game.js)") changed
  src/game.js; the "same tests keep failing" check never counted a run - it read only jest and pytest output.
* 22:57:09 W1 was forced to report as "stuck in a loop": `node tests/game.test.js` 3 times - with a successful edit before
  each run.
* 22:12:41 W1 marked "Kick-off Task 8: Run automated tests ..." finished 14 seconds after its test run FAILED: the project had
  no test command the framework knew, so nothing was checked.
* 22:08 and 23:01 PM1 added "Create comprehensive documentation" and "Create User Guide and API Documentation" - not in
  the plan; W2 rewrote docs/API.md.
* 22:57-22:59 W1 sent {"search": X, "replace": X} five times; 22:58 it retried a search found twice four times.
"""
import asyncio
import json
import logging
import shutil
import types
from contextlib import contextmanager

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Multiplayer_Snake", "startup_1792850009"
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
def _clean():
    from src.utils.test_diagnosis import clear_cache
    from src.utils.test_runs import clear_test_runs
    clear_cache()
    clear_test_runs()
    yield
    clear_cache()
    clear_test_runs()


TEST_FILE = """// Mock localStorage for Node.js environment
let mockLocalStorage = {};
const game = require('../src/game');
const gameState = game.gameState;
let testResults = { passed: 0, failed: 0, tests: [] };

function runTest(name, testFn) {
  try {
    game.initializeGameState();
    testFn();
    testResults.passed++;
    console.log(`✓ ${name}`);
  } catch (e) {
    testResults.failed++;
    testResults.tests.push({ name, status: 'failed', error: e.message });
    console.error(`✗ ${name}: ${e.message}`);
  }
}

runTest('initializeGameState creates valid snake', () => {
  if (gameState.score !== 0) {
    throw new Error('Expected initial score to be 0');
  }
  if (!gameState.isRunning) {
    throw new Error('Expected isRunning to be false initially');
  }
});

runTest('handleCollision sets isGameOver', () => {
  game.handleCollision('wall');
  if (!gameState.isGameOver) {
    throw new Error('Expected isGameOver to be true after a collision');
  }
});
"""

OUTPUT = """✓ CONSTANTS.GRID_SIZE is 20
✓ handleCollision sets isGameOver
✗ initializeGameState creates valid snake: Expected isRunning to be false initially
✗ moveSnake moves snake LEFT: Snake did not move LEFT correctly

=== Running Game Engine Tests ===

Total tests: 19
Passed: 17
Failed: 2

Failed tests:
  - initializeGameState creates valid snake: Expected isRunning to be false initially
  - moveSnake moves snake LEFT: Snake did not move LEFT correctly
"""


def _workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "src").mkdir()
    (ws / "tests" / "game.test.js").write_text(TEST_FILE, encoding="utf-8")
    (ws / "src" / "game.js").write_text("const gameState = { isRunning: false };\nmodule.exports = { gameState };\n")
    return ws


# ======================================================================================== 1. the failed tests of other runners
def test_the_failed_tests_of_other_runners():
    from src.utils.test_diagnosis import other_failed_tests, _all_failures
    assert other_failed_tests(OUTPUT) == ["initializeGameState creates valid snake", "moveSnake moves snake LEFT"]
    assert _all_failures(OUTPUT) == ("initializeGameState creates valid snake", "moveSnake moves snake LEFT")
    tap = ("TAP version 13\nok 1 - adds numbers\n  ---\n  duration_ms: 0.5\n  ...\nnot ok 2 - moves left\n  ---\n"
           "    not ok 1 - starts stopped\nnot ok 3 - Game\n1..3\n# fail 2\n")
    assert other_failed_tests(tap) == ["moves left", "starts stopped", "Game"]
    spec = "✔ adds numbers (0.4ms)\n✖ moves left (0.9ms)\n✖ failing tests:\n\ntest at a.test.js:3:1\n✖ moves left (0.9ms)\n"
    assert other_failed_tests(spec) == ["moves left"]
    mocha = ("  Game\n    ✓ adds\n    1) moves left\n\n  1 passing (5ms)\n  2 failing\n\n  1) Game\n       moves left:\n"
             "     AssertionError: expected 1 to equal 2\n\n  2) starts stopped:\n     Error: x\n")
    assert other_failed_tests(mocha) == ["Game moves left", "starts stopped"]
    unittest_out = "FAIL: test_move (tests.test_game.TestGame.test_move)\nERROR: test_x (tests.t.T)\nRan 2 tests\n"
    assert other_failed_tests(unittest_out) == ["test_move (tests.test_game.TestGame.test_move)", "test_x (tests.t.T)"]
    assert other_failed_tests("FAIL: should start stopped\nPASS: adds\n") == ["should start stopped"]
    assert other_failed_tests(" × moves left 12ms\n × moves left 7ms\n") == ["moves left"]          # vitest
    assert other_failed_tests("All tests passed!\nPassed: 19\nFailed: 0\n") == []
    # jest and pytest output keeps its own names
    jest = "FAIL tests/game.test.js\n  ● Game › moves left\n\n    ✕ moves left (5 ms)\nTests: 1 failed, 1 total\n"
    assert _all_failures(jest) == ("Game moves left",)


def test_the_same_failing_tests_of_a_hand_written_runner_are_counted(tmp_path):
    from src.utils.test_diagnosis import stuck_note, STUCK_RUNS
    ws = _workspace(tmp_path)
    game = ws / "src" / "game.js"
    notes = []
    for number in range(STUCK_RUNS):
        game.write_text(game.read_text() + f"// edit {number}\n")
        notes.append(stuck_note(ws, OUTPUT))
    assert notes[:-1] == [None] * (STUCK_RUNS - 1)
    assert notes[-1].startswith("[Framework check - the same 2 test(s) have failed in each of the last 4 runs, although "
                                "src/game.js changed in between:]\nOnly the code changed - the test file(s) did not.")
    assert "an inverted check (`if (!x) throw new Error('Expected x to be false')` throws exactly when x IS false)" in notes[-1]


# ======================================================================================== 2. an inverted check
def test_an_inverted_check_is_named(tmp_path):
    from src.utils.test_diagnosis import inverted_check_hints, output_hints
    ws = _workspace(tmp_path)
    hint = ("tests/game.test.js line 24: `if (!gameState.isRunning)` throws 'Expected isRunning to be false initially' exactly "
            "when isRunning IS false - the check is INVERTED, so this test fails when the code is RIGHT. Fix the condition in "
            "the test (`if (gameState.isRunning)`); do not change the code for it.")
    assert inverted_check_hints(ws, OUTPUT) == [hint]
    assert hint in output_hints(ws, OUTPUT)
    # the correct check of line 31 is not named, and nothing is named for a message the run did not show
    assert inverted_check_hints(ws, OUTPUT.replace("Expected isRunning to be false initially", "boom")) == []
    # comparisons with true/false, and Python
    (ws / "tests" / "game.test.js").write_text(
        "if (state.isGameOver === false) {\n  throw new Error(`Expected isGameOver to be false at the start`);\n}\n")
    assert inverted_check_hints(ws, "✗ start: Expected isGameOver to be false at the start")[0].endswith(
        "Fix the condition in the test (`if (state.isGameOver !== false)`); do not change the code for it.")
    (ws / "tests" / "game.test.js").unlink()
    (ws / "tests" / "test_game.py").write_text(
        "def test_start():\n    game = Game()\n    if not game.running:\n        raise AssertionError('Expected running to be "
        "False after start()')\n")
    assert inverted_check_hints(ws, "AssertionError: Expected running to be False after start()") == [
        "tests/test_game.py line 3: `if not game.running:` raises 'Expected running to be False after start()' exactly when "
        "running IS false - the check is INVERTED, so this test fails when the code is RIGHT. Fix the condition in the test "
        "(`if game.running:`); do not change the code for it."]
    (ws / "tests" / "test_game.py").write_text(
        "if game.running:\n    raise AssertionError('Expected running to be False after start()')\n")
    assert inverted_check_hints(ws, "AssertionError: Expected running to be False after start()") == []


def test_a_piped_run_of_a_hand_written_runner_is_diagnosed(tmp_path, monkeypatch):
    import src.tools.command_executor as ce
    ws = _workspace(tmp_path)

    async def fake_run(command, cwd, timeout, tool_name=None, agent_id=None, **kwargs):
        return types.SimpleNamespace(status="completed", returncode=0, stdout=OUTPUT, stderr="", message="",
                                     background_log=None, stdout_truncated=False, stderr_truncated=False, cpu_busy=None)
    monkeypatch.setattr(ce, "run_sandboxed", fake_run)
    with quiet():
        answer = run(ce.CommandExecutionTool().execute(agent_id="W1", agent_sandbox_path=ws, scope="private",
                                                       action="run_command", command="node tests/game.test.js 2>&1 | head -50"))
    assert "[Framework hints for this test output:]" in answer["content"]
    assert "the check is INVERTED" in answer["content"]


# ======================================================================================== 3. edit, then run the tests again
def _history(*calls):
    """calls: (tool name, arguments, result text) in order."""
    history = [{"role": "system", "content": "prompt"}, {"role": "user", "content": "task"}]
    for number, (name, arguments, result) in enumerate(calls):
        history.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{number}", "name": name, "arguments": arguments}]})
        history.append({"role": "tool", "tool_call_id": f"c{number}", "name": name, "content": result})
    return history


RUN = ("command_executor", {"action": "run_command", "command": "node tests/game.test.js", "timeout": 30},
       "Command Execution Failed (Exit Code: 1)\n--- STDOUT ---\nFailed: 2")


def _edit(text, ok=True):
    return ("code_editor", {"action": "replace_chunks", "filename": "src/game.js",
                            "chunks": [{"search": "a", "replace": text}]},
            "Successfully applied 1 edits to 'src/game.js'." if ok else
            "[Tool Execution Failed] Code edit failed: 0 chunks succeeded, but 1 failed issues occurred.")


def _loop(history):
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    scheduler = object.__new__(NextStepScheduler)
    agent = types.SimpleNamespace(agent_id="W1", message_history=history)
    with quiet():
        return scheduler._detect_tool_execution_loops(agent)


def test_running_the_tests_again_after_each_edit_is_not_a_loop():
    assert _loop(_history(RUN, _edit("b"), RUN, _edit("c"), RUN)) is False          # 22:57:09
    assert _loop(_history(RUN, RUN, RUN, _edit("b"))) is False                       # the newest call is the edit
    # nothing changed in between: still a loop
    assert _loop(_history(_edit("b"), RUN, RUN, RUN)) is True
    assert _loop(_history(RUN, _edit("b", ok=False), RUN, _edit("c", ok=False), RUN)) is True
    failing = _edit("b", ok=False)
    assert _loop(_history(RUN, failing, failing, failing)) is True                   # 22:29:54


# ======================================================================================== 4. a test task without a test command
def test_a_test_task_needs_a_passing_run_when_no_test_command_is_known(tmp_path):
    from src.utils.test_runs import verification_problem, record_test_run, PASSED, FAILED, is_full_test_run
    ws = _workspace(tmp_path)
    task = "Kick-off Task 8: Run automated tests and the program to verify that all game functionality works correctly"
    assert verification_problem(ws, task) == (
        "no test run of the CURRENT files has passed yet. This project has no test command the framework knows (no \"test\" "
        "script in package.json), so run its test file(s) (tests/game.test.js) directly with command_executor (e.g. `node --test "
        "tests/game.test.js`), fix what fails, and mark the task finished when that run passes.")
    assert not is_full_test_run("node tests/game.test.js")
    record_test_run(ws, "node tests/game.test.js", FAILED, "Failed tests: - moveSnake moves snake LEFT")
    assert verification_problem(ws, task).startswith("the tests do NOT pass: the last test run (`node tests/game.test.js`) "
                                                     "failed. Its output ended with: Failed tests:")
    record_test_run(ws, "node tests/game.test.js 2>&1 | head -40", PASSED, "")       # the pipe's exit code: not counted
    assert verification_problem(ws, task).startswith("the tests do NOT pass")
    record_test_run(ws, "node tests/game.test.js", PASSED, "All tests passed!")
    assert verification_problem(ws, task) is None
    (ws / "src" / "game.js").write_text("// changed after the run\n")
    assert verification_problem(ws, task).startswith("no test run of the CURRENT files has passed yet.")
    # no test files: nothing to demand; other tasks are not checked
    (ws / "tests" / "game.test.js").unlink()
    assert verification_problem(ws, task) is None
    assert verification_problem(_workspace(tmp_path / "b"), "Write README.md") is None


class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history, self.read_message_ids, self.message_inbox = [], {}, []
        self.agent_config = {}


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


@needs_tw
def test_the_test_task_is_not_finished_after_a_failed_run(tasks):
    from src.utils.test_runs import record_test_run, FAILED, PASSED
    w1 = Agent("W1", state="worker_test")
    manager = _manager(Agent("PM1", "pm", "pm_manage"), w1)
    (tasks.workspace / "tests").mkdir()
    (tasks.workspace / "tests" / "game.test.js").write_text(TEST_FILE, encoding="utf-8")
    added = tasks("framework", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W1", tags=["kickoff"],
                  description="Kick-off Task 8: Run automated tests and the program to verify that all game functionality "
                              "works correctly")
    assert added["status"] == "success", added
    record_test_run(tasks.workspace, "node tests/game.test.js", FAILED, "Failed: 2")
    refused = tasks("W1", manager, action="modify_task", task_id=added["task_uuid"], task_progress="finished")
    assert refused["status"] == "error" and refused["error_type"] == "tests_not_passing", refused
    assert "the last test run (`node tests/game.test.js`) failed" in refused["message"]
    record_test_run(tasks.workspace, "node tests/game.test.js", PASSED, "All tests passed!")
    assert tasks("W1", manager, action="modify_task", task_id=added["task_uuid"],
                 task_progress="finished")["status"] == "success"


# ======================================================================================== 5. documentation nobody planned
KICKOFF = ("PROJECT KICK-OFF: WiFi_Multiplayer_Snake Full Plan Overview: **Objective:** Build a browser-based multiplayer Snake "
           "game. 7. **Server setup:** Create a simple HTTP server in `server.js`. 8. **Documentation:** Write `README.md` "
           "with setup instructions (npm install, start server), game controls, and how to join from mobile devices.")


@needs_tw
def test_a_pm_does_not_invent_documentation_work(tasks):
    pm1 = Agent("PM1", "pm", "pm_manage")
    manager = _manager(pm1, Agent("W2"))
    tasks("framework", manager, action="add_task", description=KICKOFF, project_filter=PROJECT,
          tags=["project_kickoff", "auto_created_by_framework"])
    for title, description in (
            ("Create User Guide and API Documentation",
             "Create comprehensive user guide and API documentation for the WiFi Multiplayer Snake game, including setup "
             "instructions, game controls, and peer-to-peer connection details."),
            ("Documentation: Create project docs",
             "Kick-off Task 7: Create comprehensive documentation for the multiplayer snake game project")):
        refused = tasks("PM1", manager, action="add_task", project_filter=PROJECT, title=title, description=description)
        assert refused["status"] == "error" and refused["error_type"] == "unplanned_improvement", (title, refused)
    # the planned README, a found problem in the docs, and a guide the plan asks for are fine
    readme = tasks("PM1", manager, action="add_task", project_filter=PROJECT,
                   description="Write README.md with setup instructions and game controls")
    assert readme.get("error_type") != "unplanned_improvement", readme
    wrong = tasks("PM1", manager, action="add_task", project_filter=PROJECT,
                  description="Fix the API documentation in docs/API.md: the port is wrong")
    assert wrong.get("error_type") != "unplanned_improvement", wrong
    tasks("framework", manager, action="add_task", project_filter=PROJECT, tags=["project_kickoff", "update_1"],
          description="PROJECT UPDATE 1: the user asks: write a user guide for players")
    guide = tasks("PM1", manager, action="add_task", project_filter=PROJECT, description="Write docs/USER_GUIDE.md user guide")
    assert guide.get("error_type") != "unplanned_improvement", guide


# ======================================================================================== 6. code_editor
GAME = ("function initializeGameState() {\n  gameState.isRunning = true;\n}\n\nfunction startGame() {\n"
        "  gameState.isRunning = true;\n}\n")


def _edit_file(tmp_path, chunks):
    from src.tools.code_editor import CodeEditorTool
    (tmp_path / "game.js").write_text(GAME, encoding="utf-8")
    with quiet():
        return run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks", filename="game.js",
                                            chunks=chunks))


def test_an_edit_that_changes_nothing_and_an_ambiguous_search(tmp_path):
    same = _edit_file(tmp_path, json.dumps([{"replace": "  gameState.isRunning = true;",
                                             "search": "  gameState.isRunning = true;"}]))
    assert same["status"] == "error"
    assert "Chunk 0 changes NOTHING: its 'replace' text is exactly its 'search' text. Put the NEW text in 'replace'." \
        in same["message"]
    ambiguous = _edit_file(tmp_path, [{"search": "  gameState.isRunning = true;", "replace": "  gameState.isRunning = false;"}])
    assert "Search text is ambiguous (found 2 times, at lines 2, 6). Please make the search block larger/more unique, or " \
           "use start_line and end_line." in ambiguous["message"]
    assert (tmp_path / "game.js").read_text() == GAME
    fixed = _edit_file(tmp_path, [{"start_line": 2, "end_line": 2, "replace": "  gameState.isRunning = false;"}])
    assert fixed["status"] == "success", fixed
    assert (tmp_path / "game.js").read_text().splitlines()[1] == "  gameState.isRunning = false;"

# END OF FILE tests/unit/test_round3m.py
