# START OF FILE tests/unit/test_round3g.py
"""
Round 3g: the run of 2026-10-23 21:53 - 2026-10-24 09:37 (qwen3.5:4b-q4_K_M, WiFi_Snake_Game, 12 hours, never finished).

* 01:18-09:37: "should increase score when food is eaten" and three more tests of tests/test_game/test_game.js failed; four
  workers rewrote src/game.js for 8 hours. The tests PASS when run alone: Game.initGame() loads the state an earlier test
  saved in localStorage (isGameOver: true).
* jest's testMatch ('**/tests/**/*.js') matched the setup file tests/setup.js -> "Add at least one test to tests/setup.js".
* `JSON.stringify({ timestamp: expect.any(Number) })` -> {"inverse":false}; jsdom's canvas.getContext() -> null.
* 'npm test -- tests/test_player' and 'npm test 2>&1 || python -m pytest' were recorded as full test runs.
* 07:02:50 PM1 copied W1's open task into a new task for W4; 22:37-23:47 PM1 invented "Polish UI", "Code review and
  optimization" and "performance review" tasks for idle workers.
* 19 command_executor calls had the command inside 'action' ('run_command>\\nnpm test -- tests/setup.js').
"""
import asyncio
import json
import logging
import shutil
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]
PROJECT, SESSION = "WiFi_Snake_Game", "startup_1792763590"

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


def _workspace(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# The run's jest output (shortened; jest writes it to stderr)
JEST_OUTPUT = """FAIL tests/test_sync/test_sync.js
  Sync Module
    sendMessage
      ✕ should send message to server when connected (3 ms)

  ● Sync Module › sendMessage › should send message to server when connected

    expect(jest.fn()).toHaveBeenCalledWith(...expected)

    Expected: "{\\"type\\":\\"test-type\\",\\"timestamp\\":{\\"inverse\\":false},\\"data\\":{\\"test\\":\\"data\\"}}"
    Received: "{\\"type\\":\\"test-type\\",\\"timestamp\\":1792806122002,\\"data\\":{\\"test\\":\\"data\\"}}"

PASS tests/test_ui.js
FAIL tests/test_game/test_game.js
  Game Module
    update
      ✓ should move snake in the correct direction (1 ms)
      ✕ should increase score when food is eaten (1 ms)
      ✕ should detect collision with self

  ● Game Module › update › should increase score when food is eaten

    expect(received).toBe(expected) // Object.is equality

    Expected: 10
    Received: 0

  ● Game Module › update › should detect collision with self

    Expected: true
    Received: false

Test Suites: 2 failed, 5 passed, 7 total
Tests:       3 failed, 100 passed, 103 total
"""
PACKAGE_JSON = json.dumps({"name": "wifi-snake-game", "scripts": {"test": "jest"},
                           "devDependencies": {"jest": "^29.7.0", "jest-environment-jsdom": "^30.5.2"}})
JEST_CONFIG = """module.exports = {
  testEnvironment: 'jsdom',
  testMatch: ['**/tests/**/*.js'],
  testPathIgnorePatterns: ['/node_modules/'],
  verbose: true,
  setupFilesAfterEnv: ['<rootDir>/tests/setup.js'],
};
"""
GAME_JS = """const STORAGE_KEY = 'snakeGameState';
class Game {
  initGame() {
    const savedState = localStorage.getItem(STORAGE_KEY);
    if (savedState) { Object.assign(this, JSON.parse(savedState)); }
  }
}
module.exports = { Game };
"""


# ======================================================================================== 1. which tests failed
def test_failed_jest_and_pytest_tests_are_read_from_the_output():
    from src.utils.test_diagnosis import jest_failed_tests, pytest_failed_tests
    assert jest_failed_tests(JEST_OUTPUT) == [
        ("tests/test_sync/test_sync.js", "Sync Module sendMessage should send message to server when connected"),
        ("tests/test_game/test_game.js", "Game Module update should increase score when food is eaten"),
        ("tests/test_game/test_game.js", "Game Module update should detect collision with self")]
    assert jest_failed_tests("  ● Test suite failed to run\n") == []
    assert jest_failed_tests("") == [] and jest_failed_tests(None) == []
    py = ("....F.F\n=========================== short test summary info ============================\n"
          "FAILED tests/test_game.py::test_score - assert 0 == 10\nFAILED tests/test_game.py::TestWall::test_hit\n"
          "2 failed, 5 passed in 0.12s\n")
    assert pytest_failed_tests(py) == ["tests/test_game.py::test_score", "tests/test_game.py::TestWall::test_hit"]


def test_the_isolated_commands():
    from src.utils.test_diagnosis import _isolated_command
    ws = Path("/nonexistent")
    assert _isolated_command("pytest", ws, "", "tests/test_x.py::test_a", ".venv/bin/python -m pytest -q tests") \
        == ".venv/bin/python -m pytest -q -p no:cacheprovider tests/test_x.py::test_a"
    assert _isolated_command("pytest", ws, "", "tests/test_x.py::test_a", "pytest") \
        == "pytest -q -p no:cacheprovider tests/test_x.py::test_a"
    assert _isolated_command("jest", ws, "tests/test_game/test_game.js", "Game Module update should increase score",
                             "npx jest") == ("npx jest tests/test_game/test_game.js -t "
                                             "'^Game\\ Module\\ update\\ should\\ increase\\ score$'")


# ======================================================================================== 2. tests that pass alone
ORDER_DEPENDENT_TESTS = """STATE = []


def test_first_saves_state():
    STATE.append("game over")
    assert STATE


def test_second_needs_a_fresh_state():
    assert STATE == []


def test_third_is_just_wrong():
    assert 1 == 2
"""


def test_a_pytest_test_that_passes_alone_is_reported(tmp_path):
    from src.utils.test_diagnosis import diagnose_failed_test_run
    from src.tools.sandbox_runner import run_sandboxed
    ws = _workspace(tmp_path, {"tests/test_order.py": ORDER_DEPENDENT_TESTS})
    command = f"{sys.executable} -m pytest -q -p no:cacheprovider tests"
    with quiet():
        full = run(run_sandboxed(command, ws, 120, kill_switch=False))
        output = full.stdout + "\n" + full.stderr
        text = run(diagnose_failed_test_run(ws, command, output))
    assert full.returncode != 0 and "FAILED tests/test_order.py::test_second_needs_a_fresh_state" in output
    assert text.startswith("\n\n[Framework check - these failed tests PASS when run ALONE (1 of 2 checked):]\n"
                           "  - tests/test_order.py::test_second_needs_a_fresh_state\n"
                           "So the code they test is NOT the problem")
    assert "test_third_is_just_wrong" not in text            # it fails alone too
    assert "Reset that state in a fixture (or setup_method)" in text and "This project uses localStorage" not in text


def test_jest_tests_that_pass_alone_are_reported(tmp_path, monkeypatch):
    import src.tools.sandbox_runner as sr
    from src.utils.test_diagnosis import diagnose_failed_test_run
    ws = _workspace(tmp_path, {"package.json": PACKAGE_JSON, "jest.config.js": JEST_CONFIG, "src/game.js": GAME_JS,
                               "tests/test_game/test_game.js": "const { Game } = require('../../src/game');\n",
                               "tests/test_sync/test_sync.js": "// sync tests\n"})
    commands = []

    async def fake_run(command, cwd, timeout, tool_name=None, agent_id=None, **kwargs):
        commands.append(command)
        alone_ok = "increase" in command
        return types.SimpleNamespace(
            status="completed", returncode=0 if alone_ok else 1, stdout="",
            stderr=("Tests:       16 skipped, 1 passed, 17 total\n" if alone_ok else "Tests:       1 failed, 16 skipped\n"))
    monkeypatch.setattr(sr, "run_sandboxed", fake_run)
    with quiet():
        text = run(diagnose_failed_test_run(ws, "npm test", JEST_OUTPUT))
    assert commands == [
        "npm test -- tests/test_sync/test_sync.js -t "
        "'^Sync\\ Module\\ sendMessage\\ should\\ send\\ message\\ to\\ server\\ when\\ connected$'",
        "npm test -- tests/test_game/test_game.js -t "
        "'^Game\\ Module\\ update\\ should\\ increase\\ score\\ when\\ food\\ is\\ eaten$'",
        "npm test -- tests/test_game/test_game.js -t "
        "'^Game\\ Module\\ update\\ should\\ detect\\ collision\\ with\\ self$'"]
    assert ("[Framework check - these failed tests PASS when run ALONE (1 of 3 checked):]\n"
            "  - tests/test_game/test_game.js: Game Module update should increase score when food is eaten") in text
    assert "This project uses localStorage" in text and "`beforeEach(() => { localStorage.clear(); })`" in text
    assert "Do NOT rewrite the source file for these tests." in text
    assert "{\"inverse\":false}` in an expected value" in text          # the hint for the sync test
    # the same failing run of the same files is not checked again
    with quiet():
        assert run(diagnose_failed_test_run(ws, "npm test", JEST_OUTPUT)) == text
    assert len(commands) == 3
    # a test that matched nothing (all skipped) did not pass
    from src.utils.test_diagnosis import _passed_alone
    assert not _passed_alone("jest", 0, "Tests:       17 skipped, 17 total")
    assert _passed_alone("pytest", 0, "1 passed in 0.01s") and not _passed_alone("pytest", 0, "no tests ran")


def test_other_runners_are_not_isolated(tmp_path, monkeypatch):
    import src.tools.sandbox_runner as sr
    from src.utils.test_diagnosis import isolation_check

    async def fail(*args, **kwargs):
        raise AssertionError("must not run")
    monkeypatch.setattr(sr, "run_sandboxed", fail)
    ws = _workspace(tmp_path, {"package.json": json.dumps({"scripts": {"test": "mocha tests/"}})})
    assert run(isolation_check(ws, "npm test", "  1) Game\n       score:\n     AssertionError")) is None
    assert run(isolation_check(ws, "npm test", JEST_OUTPUT)) is None       # the project's test script is not jest


# ======================================================================================== 3. known traps in the output
def test_output_hints(tmp_path):
    from src.utils.test_diagnosis import output_hints
    ws = _workspace(tmp_path, {"package.json": PACKAGE_JSON, "jest.config.js": JEST_CONFIG})
    empty_suite = ("FAIL tests/setup.js\n  ● Test suite failed to run\n\n    Your test suite must contain at least one "
                   "test.\n\n      at onResult (node_modules/@jest/core/build/TestScheduler.js:133:18)\n")
    hints = output_hints(ws, empty_suite)
    assert len(hints) == 1 and hints[0].startswith("jest runs tests/setup.js as a TEST file (its testMatch pattern matches "
                                                   "it), but it is a setup/helper file with no tests")
    assert "Do NOT add tests to it" in hints[0]
    assert output_hints(ws, 'Expected: "{\\"timestamp\\":{\\"inverse\\":false}}"')[0].startswith(
        "`{\"inverse\":false}` in an expected value means an asymmetric matcher")
    canvas = output_hints(ws, "TypeError: Cannot read properties of null (reading 'fillRect')\n  at Game.draw")
    assert canvas[0].startswith("jsdom has NO canvas: `canvas.getContext('2d')` returns null in jest.")
    assert output_hints(ws, "Error: Not implemented: HTMLCanvasElement.prototype.getContext (without installing the canvas "
                            "npm package)")[0].startswith("jsdom has NO canvas")
    assert output_hints(ws, "ReferenceError: describe is not defined\n    at Object.<anonymous> (test/game.test.js:3:1)")[0] \
        .startswith("`describe` is not defined: this test file uses the globals of a test runner")
    assert output_hints(ws, "Tests: 3 failed, 2 passed") == [] and output_hints(ws, "") == []


def test_command_executor_appends_the_diagnosis(tmp_path, monkeypatch):
    import src.tools.command_executor as ce
    import src.tools.sandbox_runner as sr
    ws = _workspace(tmp_path / "sandbox", {"package.json": PACKAGE_JSON, "jest.config.js": JEST_CONFIG,
                                           "src/game.js": GAME_JS})

    async def fake_run(command, cwd, timeout, tool_name=None, agent_id=None, **kwargs):
        if command.startswith("npm test -- "):    # the isolated runs
            return types.SimpleNamespace(status="completed", returncode=0, stdout="",
                                         stderr="Tests:       2 skipped, 1 passed, 3 total\n")
        return types.SimpleNamespace(status="completed", returncode=returncode, stdout="", stderr=output,
                                     message="", background_log=None, stdout_truncated=False, stderr_truncated=False,
                                     cpu_busy=None)
    monkeypatch.setattr(ce, "run_sandboxed", fake_run)
    monkeypatch.setattr(sr, "run_sandboxed", fake_run)

    def execute(command):
        with quiet():
            return run(ce.CommandExecutionTool().execute(agent_id="W1", agent_sandbox_path=ws, scope="private",
                                                         action="run_command", command=command, timeout=60))
    returncode, output = 1, JEST_OUTPUT
    answer = execute("npm test")
    assert answer["content"].endswith("Do NOT rewrite the source file for these tests.\n\n[Framework hints for this test "
                                      "output:]\n- `{\"inverse\":false}` in an expected value means an asymmetric matcher "
                                      "such as `expect.any(Number)` was put INSIDE `JSON.stringify(...)` - JSON.stringify "
                                      "turns it into {\"inverse\":false}, so that test can never pass. Compare the parsed "
                                      "object instead: `expect(JSON.parse(send.mock.calls[0][0])).toEqual({ type: 'x', "
                                      "timestamp: expect.any(Number) })`. The code under test is fine.")
    assert "PASS when run ALONE (3 of 3 checked)" in answer["content"]
    # a piped run ends with the pipe's exit code 0: the output tells it failed
    returncode = 0
    assert "PASS when run ALONE" in execute("npm test 2>&1 | head -100")["content"]
    # a passing run and other commands get nothing
    output = "Tests:       103 passed, 103 total\n"
    assert "Framework" not in execute("npm test")["content"]
    returncode, output = 1, "ls: cannot access 'x'"
    assert "Framework" not in execute("ls x")["content"]


# ======================================================================================== 4. full test runs
def test_partial_and_chained_runs_are_not_full_runs():
    from src.utils.test_runs import is_full_test_run
    for command in ("npm test -- tests/test_player", "npx jest tests/test_game", "npx jest -t score",
                    "npx jest --testPathPattern=game", "npm test || true",
                    "cd /shared && npm test 2>&1 || python -m pytest tests/ -v 2>&1", "pytest -m slow"):
        assert not is_full_test_run(command), command
    for command in ("npm test", "npm test -- --verbose", "npm test -- tests/", "npx mocha tests/", "pytest tests",
                    "npm test 2>&1", "npm install && npm test", "npx jest --coverage", "python -m pytest -q"):
        assert is_full_test_run(command), command


# ======================================================================================== 5. jest setup file as a test
def test_a_jest_setup_file_matched_by_test_match_is_found(tmp_path):
    from src.tools.project_links_check import jest_setup_match_problems, link_notes_text
    ws = _workspace(tmp_path, {"package.json": PACKAGE_JSON, "jest.config.js": JEST_CONFIG,
                               "tests/setup.js": "global.localStorage = {};\n"})
    problems = jest_setup_match_problems(ws, "jest.config.js")
    assert problems == [
        "jest runs the setup file tests/setup.js as a TEST file: testMatch in the jest config matches it, so jest reports "
        "'Your test suite must contain at least one test' (and any test written into it runs before every test file). "
        "Exclude it - add '<rootDir>/tests/setup.js' to testPathIgnorePatterns - or move it out of the tests folder (e.g. "
        "jest.setup.js) and point setupFilesAfterEnv there. Never add tests to a setup file."]
    assert jest_setup_match_problems(ws, "tests/setup.js") == problems
    assert jest_setup_match_problems(ws, "src/game.js") == []
    assert "jest runs the setup file tests/setup.js" in link_notes_text(ws, "jest.config.js", JEST_CONFIG)
    # excluded, or a testMatch that does not match it, or a setup file outside tests/: fine
    (ws / "jest.config.js").write_text(JEST_CONFIG.replace("['/node_modules/']", "['/node_modules/', '<rootDir>/tests/setup.js']"))
    assert jest_setup_match_problems(ws, "jest.config.js") == []
    (ws / "jest.config.js").write_text(JEST_CONFIG.replace("'**/tests/**/*.js'", "'**/tests/**/*.test.js'"))
    assert jest_setup_match_problems(ws, "jest.config.js") == []
    (ws / "jest.config.js").write_text(JEST_CONFIG.replace("<rootDir>/tests/setup.js", "<rootDir>/jest.setup.js"))
    assert jest_setup_match_problems(ws, "jest.config.js") == []
    (ws / "jest.config.js").write_text("module.exports = { testEnvironment: 'jsdom' };\n")   # jest's default testMatch
    assert jest_setup_match_problems(ws, "jest.config.js") == []


# ======================================================================================== 6. PM tasks
class Agent:
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


W1_TASK = ("Fix failing tests: 18 tests failed across game.js, network.js, and sync.js test suites. Issues include: score "
           "not incrementing when food eaten, collision detection not working, and network module functions not properly "
           "defined.")
KICKOFF = ("PROJECT KICK-OFF: WiFi_Snake_Game Full Plan Overview: **Objective:** Browser-based WiFi multiplayer snake game "
           "with player names, scores, and leaderboard. 6. **UI components:** Build game board, controls, player list, and "
           "leaderboard UI in `src/ui.js`. 8. **Testing:** Write tests for game logic.")


@needs_tw
def test_a_pm_does_not_copy_a_worker_s_open_task(tasks):
    pm1, w1, w4 = Agent("PM1", "pm", "pm_manage"), Agent("W1", state="worker_work"), Agent("W4")
    manager = _manager(pm1, w1, w4)
    added = tasks("framework", manager, action="add_task", description=W1_TASK, project_filter=PROJECT,
                  assignee_agent_id="W1")
    assert added["status"] == "success", added
    assert tasks("framework", manager, action="modify_task", task_id=added["task_uuid"],
                 task_progress="doing")["status"] == "success"
    # 07:02:50 PM1
    copy = tasks("PM1", manager, action="add_task", project_filter=PROJECT, title="Fix failing tests for game.js module",
                 description=W1_TASK)
    assert copy["status"] == "error" and copy["error_type"] == "open_duplicate", copy
    assert copy["message"].startswith(f"The task was NOT created: the same work is already OPEN in task {added['task_uuid']} "
                                      "('Fix failing tests: 18 tests failed across game.js")
    assert "assigned to W1 (task_progress: doing)" in copy["message"] and "send W1 what to do differently" in copy["message"]
    # different work is fine
    other = tasks("PM1", manager, action="add_task", project_filter=PROJECT,
                  title="Write README troubleshooting section", description="Add a troubleshooting section to docs/README.md")
    assert other["status"] == "success", other
    # a worker's own sub-tasks and the framework are not checked here
    assert tasks("framework", manager, action="add_task", project_filter=PROJECT, description=W1_TASK + " (again)",
                 assignee_agent_id="W4").get("error_type") != "open_duplicate"


@needs_tw
def test_a_pm_does_not_invent_improvement_work(tasks):
    pm1, w1 = Agent("PM1", "pm", "pm_manage"), Agent("W1")
    manager = _manager(pm1, w1)
    kickoff = tasks("framework", manager, action="add_task", description=KICKOFF, project_filter=PROJECT,
                    tags=["project_kickoff", "auto_created_by_framework"])
    assert kickoff["status"] == "success", kickoff
    # 22:37:50 and 23:16 PM1
    for title, description in (
            ("Polish UI components and add animations for enhanced gameplay",
             "Enhance the snake game UI with smooth animations, visual feedback for player movement"),
            ("Code review and optimization for game logic modules",
             "Review and optimize the core game modules (game.js, player.js, network.js) for performance and best practices"),
            ("Review game.js for performance issues and best practices", "create optimization recommendations")):
        refused = tasks("PM1", manager, action="add_task", project_filter=PROJECT, title=title, description=description)
        assert refused["status"] == "error" and refused["error_type"] == "unplanned_improvement", (title, refused)
        assert "An idle worker with nothing to do is NORMAL" in refused["message"]
    # a problem that was found, planned work, and the framework are fine
    fix = tasks("PM1", manager, action="add_task", project_filter=PROJECT, title="Fix the slow game loop",
                description="Optimize the game loop in src/game.js: the game lags with 4 players")
    assert fix["status"] == "success", fix
    planned = tasks("PM1", manager, action="add_task", project_filter=PROJECT,
                    description="Build the player list UI in src/ui.js")
    assert planned.get("error_type") != "unplanned_improvement", planned
    assert tasks("framework", manager, action="add_task", project_filter=PROJECT,
                 description="Polish the README wording")["status"] == "success"


@needs_tw
def test_improvement_work_the_user_asked_for_is_allowed(tasks):
    pm1 = Agent("PM1", "pm", "pm_manage")
    manager = _manager(pm1, Agent("W1"))
    tasks("framework", manager, action="add_task", project_filter=PROJECT, tags=["project_kickoff", "update_1"],
          description="PROJECT UPDATE 1: the user asks: add animations when the snake eats food")
    allowed = tasks("PM1", manager, action="add_task", project_filter=PROJECT,
                    description="Add CSS animations for eating food in src/ui.js")
    assert allowed.get("error_type") != "unplanned_improvement", allowed


# ======================================================================================== 7. the command in 'action'
def test_a_command_inside_action_is_run_as_the_command(tmp_path, monkeypatch):
    import src.tools.command_executor as ce
    from src.tools.executor import ToolExecutor
    seen = []

    async def fake_run(command, cwd, timeout, tool_name=None, agent_id=None, **kwargs):
        seen.append(command)
        return types.SimpleNamespace(status="completed", returncode=0, stdout="ok\n", stderr="", message="",
                                     background_log=None, stdout_truncated=False, stderr_truncated=False, cpu_busy=None)
    monkeypatch.setattr(ce, "run_sandboxed", fake_run)
    w2 = Agent("W2", state="worker_work")
    manager = types.SimpleNamespace(agents={"W2": w2})
    with quiet():
        answer = run(ToolExecutor().execute_tool("W2", tmp_path, "command_executor",
                                                 {"action": "run_command>\nnpm test -- tests/setup.js", "timeout": 60,
                                                  "scope": "private"}, manager=manager))
    assert seen == ["npm test -- tests/setup.js"], answer
    assert str(answer).startswith("[Framework note: your command was inside 'action', so it was run as 'command'.")
    with quiet():
        run(ToolExecutor().execute_tool("W2", tmp_path, "command_executor",
                                        {"action": "run_command", "command": "ls", "scope": "private"}, manager=manager))
    assert seen[-1] == "ls"



def test_the_worker_prompt_asks_for_independent_tests():
    import yaml
    prompts = yaml.safe_load((REPO / "prompts.yaml").read_text(encoding="utf-8"))
    assert ("Each test must work on its own: reset shared state (e.g. `localStorage.clear()`, module variables) in "
            "`beforeEach` / a fixture.") in prompts["worker_work_prompt"]

# END OF FILE tests/unit/test_round3g.py
