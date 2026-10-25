# START OF FILE tests/unit/test_round3o.py
"""
Round 3o: the run of 2026-10-25 13:22-13:41 (Laptop_Snake_Multiplayer, Python, OpenRouter nvidia/nemotron-3.5-lightning:free).

* 13:35-13:40 W2 ("Kick-off Task 3: Write pytest tests ... in tests/test_snake.py") edited src/snake_game.py - the file of W1's
  FINISHED Kick-off Task 1 - four times to make its own wrong tests pass ('length: int = 1' -> 3 -> 1).
* Its tests called GameState.spawn_food(position=...) (no such argument) and used HTTPServer / BaseHTTPRequestHandler without
  importing them; nothing told W2 that the TEST was wrong.
* 13:28:56 W1 ran `pytest -q` before any test existed: "Test command failed with exit code 5. - Please check your
  parameters" - the output and the framework's diagnosis of a failed test_runner run never reached any agent.
* 13:30:00 W1's REFUSED write of tests/test_snake.py counted as a successful file change ("skipping test state after
  producing code files: ['tests/test_snake.py']", "files were changed in between - not a loop").
* 13:39:24 PM1 sent W1 (waiting, no open task) a list of fixes; W1 marked it read and nothing happened.
* 13:39:57 PM1's `pytest tests/test_snake.py -v 2>&1 | head -100` (8 failed, 2 errors) came back as "completed successfully".
* 13:27:20 src/game/ and src/server/ were created for two DROPPED planned files.
* 13:36:39 W2's 'chunks' string had unescaped docstring quotes; every repair failed.
* 13:32:24-13:35:16 W2 wrote its test file in worker_decompose: cut off at 2048, 4096 and 8192 tokens.
* 13:40:47 the user stopped the project; PM1's and W2's replies still ran tools at 13:40:57 and 13:41:05.
"""
import asyncio
import logging
import shutil
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "Laptop_Snake_Multiplayer", "startup_1792905759"

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
    from src.utils.test_diagnosis import clear_cache
    clear_test_runs()
    clear_cache()
    yield
    clear_test_runs()
    clear_cache()


def _workspace(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# The run's code (shortened, the same definitions)
SNAKE_GAME = '''"""Core snake game logic."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Direction(Enum):
    UP = 1
    DOWN = 2
    LEFT = 3
    RIGHT = 4


@dataclass
class Point:
    x: int
    y: int


@dataclass
class Snake:
    id: str
    name: str
    head: Point
    body: List[Point] = field(default_factory=list)
    direction: Direction = Direction.RIGHT
    score: int = 0
    length: int = 1

    def set_direction(self, direction: Direction) -> None:
        self.direction = direction

    def grow(self, segments: int = 1) -> None:
        self.length += segments


class GameState:
    def __init__(self, grid_width: int = 20, grid_height: int = 20) -> None:
        self.grid_width = grid_width
        self.grid_height = grid_height
        self.snakes: Dict[str, Snake] = {}

    def add_player(self, player_id: str, name: str, x: int, y: int) -> Snake:
        snake = Snake(id=player_id, name=name, head=Point(x, y))
        self.snakes[player_id] = snake
        return snake

    def spawn_food(self, avoid_positions: Optional[List[Point]] = None) -> None:
        self.food = Point(0, 0)


def create_default_game() -> GameState:
    return GameState()
'''

def _line_of(text):
    return SNAKE_GAME.splitlines().index(text) + 1


SPAWN_FOOD_LINE = _line_of("    def spawn_food(self, avoid_positions: Optional[List[Point]] = None) -> None:")
SNAKE_LINE = _line_of("class Snake:")
GROW_LINE = _line_of("    def grow(self, segments: int = 1) -> None:")

# 13:38:54 W2's run (shortened): an ERROR at fixture setup and a FAILED test
PYTEST_RUN = """F...FFFF........F..........EEFF                                          [100%]
==================================== ERRORS ====================================
____________________ ERROR at setup of test_health_endpoint ____________________

    @pytest.fixture
    def mock_server():
>       server = HTTPServer(("127.0.0.1", 0), MockRequestHandler)
                 ^^^^^^^^^^
E       NameError: name 'HTTPServer' is not defined

tests/test_snake.py:349: NameError
=================================== FAILURES ===================================
__________________________ test_snake_set_direction_valid ___________________________

    def test_snake_set_direction_valid():
>       assert snake.direction == Direction.DOWN
E       AssertionError: assert <Direction.UP: 1> == <Direction.DOWN: 2>

tests/test_snake.py:104: AssertionError
__________________________ test_check_food_collision ___________________________

    def test_check_food_collision():
        gs = GameState(grid_width=20, grid_height=20)
        gs.add_player("p1", "Player1", 5, 5)
>       gs.spawn_food(position=Point(5, 5))  # Food at head position
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       TypeError: GameState.spawn_food() got an unexpected keyword argument 'position'

tests/test_snake.py:194: TypeError
=========================== short test summary info ============================
FAILED tests/test_snake.py::test_snake_set_direction_valid - AssertionError: assert <Direction.UP: 1> == <Direction.DOWN: 2>
FAILED tests/test_snake.py::test_check_food_collision - TypeError: GameState....
ERROR tests/test_snake.py::test_health_endpoint - NameError: name 'HTTPServer...
2 failed, 21 passed, 1 error in 0.16s
"""

# 13:35:31 W2's first run: the test file did not even load
PYTEST_COLLECTION_ERROR = """==================================== ERRORS ====================================
_ ERROR collecting projects/Laptop_Snake_Multiplayer/startup_1792905759/shared_workspace/tests/test_snake.py _
tests/test_snake.py:315: in <module>
    class MockRequestHandler(BaseHTTPRequestHandler):
                             ^^^^^^^^^^^^^^^^^^^^^^
E   NameError: name 'BaseHTTPRequestHandler' is not defined
=========================== short test summary info ============================
ERROR tests/test_snake.py - NameError: name 'BaseHTTPRequestHandler' is not defined
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
1 error in 0.11s
"""

# 13:28:56 W1's `pytest -q` before any test file existed
PYTEST_NO_TESTS = "\nno tests ran in 0.01s\n"


# ======================================================================================== 1. calls that do not match the code
def test_the_failures_are_found_with_where_they_were_raised():
    from src.utils.test_diagnosis import failure_locations
    found = failure_locations(PYTEST_RUN)
    assert ("NameError", "name 'HTTPServer' is not defined", ("tests/test_snake.py", 349)) in found
    assert ("TypeError", "GameState.spawn_food() got an unexpected keyword argument 'position'",
            ("tests/test_snake.py", 194)) in found
    assert ("AssertionError", "assert <Direction.UP: 1> == <Direction.DOWN: 2>", ("tests/test_snake.py", 104)) in found
    # the short summary lines cut to the terminal width add nothing
    assert not any(message.endswith("...") for _kind, message, _loc in found)
    # a collection error: the location is the 'tests/x.py:315: in <module>' line BEFORE the error
    assert failure_locations(PYTEST_COLLECTION_ERROR)[0] == (
        "NameError", "name 'BaseHTTPRequestHandler' is not defined", ("tests/test_snake.py", 315))


def test_a_test_that_calls_the_code_wrongly_is_named_with_the_real_definition(tmp_path):
    from src.utils.test_diagnosis import api_mismatch_hints
    _workspace(tmp_path, {"src/snake_game.py": SNAKE_GAME, "tests/test_snake.py": "def test_x():\n    assert True\n",
                          "conftest.py": "import sys\n"})
    hints = api_mismatch_hints(tmp_path, PYTEST_RUN)
    assert len(hints) == 2, hints
    assert hints[0] == ("tests/test_snake.py line 349 uses `HTTPServer`, but the test file never imports or defines it - "
                        "add `from http.server import HTTPServer` at the top. Fix the test file - the code is not the "
                        "problem.")
    assert hints[1] == ("tests/test_snake.py line 194 calls `GameState.spawn_food()` with the argument 'position', but "
                        f"src/snake_game.py line {SPAWN_FOOD_LINE} defines `def spawn_food(self, avoid_positions: Optional[List[Point]]="
                        "None) -> None` - it has no 'position'. So the TEST is wrong here: change the test to match the "
                        "code - do not change the code to fit the test.")
    collection = api_mismatch_hints(tmp_path, PYTEST_COLLECTION_ERROR)
    assert collection == ["tests/test_snake.py line 315 uses `BaseHTTPRequestHandler`, but the test file never imports or "
                          "defines it - add `from http.server import BaseHTTPRequestHandler` at the top. Fix the test "
                          "file - the code is not the problem."]


def test_other_calls_that_do_not_match_the_code(tmp_path):
    from src.utils.test_diagnosis import api_mismatch_hints, project_definitions
    _workspace(tmp_path, {"src/snake_game.py": SNAKE_GAME})
    defs = project_definitions(tmp_path)
    assert defs["classes"]["Snake"][0]["attributes"][:4] == ["id", "name", "head", "body"]
    assert "snake_game" in defs["modules"] and "src.snake_game" in defs["modules"]

    def section(error, location):
        return f"____ test_x ____\n\n>       call()\nE       {error}\n\n{location}\n"
    # a missing attribute (raised in the test), with what the class has
    output = section("AttributeError: 'Snake' object has no attribute 'grow_by'", "tests/test_snake.py:20: AttributeError")
    assert api_mismatch_hints(tmp_path, output) == [
        f"tests/test_snake.py line 20 uses `.grow_by` of a Snake object, but class Snake (src/snake_game.py line {SNAKE_LINE}) has "
        "no 'grow_by'. It has: id, name, head, body, direction, score, length, set_direction(), grow(). So the TEST is "
        "wrong here: change the test to match the code - do not change the code to fit the test."]
    # an import of a name the module does not define
    output = section("ImportError: cannot import name 'Food' from 'snake_game' (/x/src/snake_game.py)",
                     "tests/test_snake.py:3: ImportError")
    hint = api_mismatch_hints(tmp_path, output)[0]
    assert hint.startswith("tests/test_snake.py line 3 needs 'Food' from 'snake_game', but src/snake_game.py defines no "
                           "'Food'. It defines: ")
    assert "GameState" in hint and "create_default_game" in hint and "So the TEST is wrong here" in hint
    # too many positional arguments - raised in the CODE: the code is the caller to fix
    output = section("TypeError: Snake.grow() takes from 1 to 2 positional arguments but 3 were given",
                     "src/snake_game.py:40: TypeError")
    assert api_mismatch_hints(tmp_path, output) == [
        "src/snake_game.py line 40 calls `Snake.grow()` in a way it does not take (Snake.grow() takes from 1 to 2 "
        f"positional arguments but 3 were given): src/snake_game.py line {GROW_LINE} defines `def grow(self, segments: int=1) -> "
        "None`. So the code itself makes this wrong call: fix line 40 of src/snake_game.py."]
    # unknown names, assertion errors and tracebacks without a known definition: nothing is guessed
    assert api_mismatch_hints(tmp_path, section("TypeError: Other.go() got an unexpected keyword argument 'x'",
                                                "tests/test_snake.py:9: TypeError")) == []
    assert api_mismatch_hints(tmp_path, section("AssertionError: assert 3 == 5", "tests/test_snake.py:9: AssertionError")) == []
    assert api_mismatch_hints(None, PYTEST_RUN) == [] and api_mismatch_hints(tmp_path, "") == []


def test_a_run_in_which_no_test_ran(tmp_path):
    from src.utils.test_diagnosis import no_tests_note
    _workspace(tmp_path, {"src/snake_game.py": SNAKE_GAME, "conftest.py": "import sys\n"})
    note = no_tests_note(tmp_path, PYTEST_NO_TESTS)
    assert note.startswith("[Framework check - NO test ran: the project has no test file yet (this is pytest's exit code "
                           "5 / jest's 'No tests found'), so this says nothing about your code.]")
    assert "leave it to that task and do NOT write them yourself" in note
    assert "(e.g. `python -c \"import src.snake_game\"`)" in note
    assert no_tests_note(tmp_path, "No tests found, exiting with code 1\n") == note
    # test files that were not collected
    _workspace(tmp_path, {"tests/test_snake.py": "def check_x():\n    pass\n"})
    note = no_tests_note(tmp_path, PYTEST_NO_TESTS)
    assert note.startswith("[Framework check - NO test ran although the project has test file(s) (tests/test_snake.py):]")
    assert "`python -m pytest -q tests/test_snake.py`" in note
    # a run in which tests ran is not such a run
    assert no_tests_note(tmp_path, "no tests ran ... 3 passed in 0.1s") is None
    assert no_tests_note(tmp_path, PYTEST_RUN) is None


def test_the_diagnosis_of_a_failed_run_carries_the_new_checks(tmp_path, monkeypatch):
    import src.utils.test_diagnosis as td
    _workspace(tmp_path, {"src/snake_game.py": SNAKE_GAME, "tests/test_snake.py": "def test_x():\n    assert True\n"})

    async def no_isolation(*_args, **_kwargs):
        return None
    monkeypatch.setattr(td, "isolation_check", no_isolation)
    with quiet():
        text = run(td.diagnose_failed_test_run(tmp_path, "python -m pytest -q", PYTEST_RUN))
    assert "[Framework check - calls that do not match the project's code:]" in text
    assert "- tests/test_snake.py line 194 calls `GameState.spawn_food()`" in text
    empty = tmp_path / "empty"
    _workspace(empty, {"src/game.py": "x = 1\n"})
    with quiet():
        text = run(td.diagnose_failed_test_run(empty, "pytest -q", PYTEST_NO_TESTS))
    assert "[Framework check - NO test ran: the project has no test file yet" in text


# ======================================================================================== 2. failed test runs reach the agent
class _Result:
    def __init__(self, returncode, stdout, stderr=""):
        self.status, self.returncode, self.stdout, self.stderr = "completed", returncode, stdout, stderr
        self.duration_seconds, self.message, self.cpu_busy = 0.2, "", None
        self.background_log = None
        self.stdout_truncated = self.stderr_truncated = False


@pytest.fixture
def shared(tmp_path, monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    _workspace(workspace, {"src/snake_game.py": SNAKE_GAME})
    return workspace


def _fake_run(monkeypatch, module, result):
    async def fake(command, cwd, timeout, tool_name=None, agent_id=None):
        return result
    monkeypatch.setattr(module, "run_sandboxed", fake)


def test_a_failed_test_runner_run_shows_its_output_and_the_framework_check(shared, monkeypatch):
    import src.tools.test_runner as tr
    from src.tools.executor import ToolExecutor
    _fake_run(monkeypatch, tr, _Result(5, PYTEST_NO_TESTS))
    w1 = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_work", status="idle",
                               message_history=[])
    manager = types.SimpleNamespace(agents={"W1": w1})
    with quiet():
        answer = run(ToolExecutor().execute_tool("W1", shared.parent, "test_runner",
                                                 {"command": "pytest -q", "scope": "shared"},
                                                 project_name=PROJECT, session_name=SESSION, manager=manager))
    text = str(answer)
    assert text.startswith("[Tool Execution Failed] Test command failed with exit code 5. [ID:"), text
    assert "Read the output below and fix what it shows." in text and "Please check your parameters" not in text
    assert "[exit_code]: 5" in text and "no tests ran in 0.01s" in text
    assert "--- FRAMEWORK_CHECK ---\n[Framework check - NO test ran: the project has no test file yet" in text
    # still a FAILED tool result for the framework
    from src.agents.tool_activity import is_tool_result_failure
    assert is_tool_result_failure("test_runner", text)


def test_a_piped_test_run_that_hides_failures_is_a_failure(shared, monkeypatch):
    import src.tools.test_runner as tr
    piped = ("tests/test_snake.py::test_check_food_collision FAILED                   [ 40%]\n"
             "tests/test_snake.py::test_health_endpoint ERROR                         [ 90%]\n")
    _fake_run(monkeypatch, tr, _Result(0, piped))
    with quiet():
        result = run(tr.TestRunnerTool().execute("PM1", shared.parent, PROJECT, SESSION,
                                                 command="pytest tests/test_snake.py -v 2>&1 | head -100"))
    assert result["status"] == "error"
    assert result["message"].startswith("Tests FAILED: the command ended with exit code 0 only because of the pipe")
    # the same output without a pipe and exit code 0 cannot happen; a passing run stays a success
    _fake_run(monkeypatch, tr, _Result(0, "tests/test_snake.py::test_a PASSED [100%]\n1 passed in 0.01s\n"))
    with quiet():
        result = run(tr.TestRunnerTool().execute("PM1", shared.parent, PROJECT, SESSION,
                                                 command="pytest tests/test_snake.py -v 2>&1 | head -100"))
    assert result["status"] == "success" and result["message"] == "Test command completed successfully."
    _fake_run(monkeypatch, tr, _Result(0, "3 passed in 0.1s\n"))
    with quiet():
        result = run(tr.TestRunnerTool().execute("W1", shared.parent, PROJECT, SESSION, command="pytest -q"))
    assert result["status"] == "success"


# ======================================================================================== 3. a refused write is a failure
def test_a_refusal_by_the_executor_is_a_failed_tool_call():
    from src.agents.tool_activity import is_tool_result_failure, written_code_files
    refusal = ("Error from tool 'file_system': The file was NOT created: 'tests/test_snake.py' is the file of the task "
               "'Kick-off Task 3: ...', which is assigned to W2.")
    assert is_tool_result_failure("file_system", refusal)
    assert is_tool_result_failure("file_system", "[System Warning: there is no tool named 'fs'.]\n\n" + refusal)
    assert is_tool_result_failure("code_editor", refusal.replace("file_system", "code_editor"))
    # a file whose text contains such a line is still a successful read (its lines are numbered)
    assert not is_tool_result_failure("file_system", "1: x = 1\n2: Error from tool 'file_system': demo\n")
    history = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "file_system", "arguments": {
            "action": "write", "filename": "tests/test_snake.py", "content": "x"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "file_system", "content": refusal},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "name": "file_system", "arguments": {
            "action": "write", "filename": "src/snake_game.py", "content": "x"}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "file_system",
         "content": "Successfully wrote content to 'src/snake_game.py' in shared workspace."},
    ]
    assert written_code_files(history) == ["src/snake_game.py"]


def test_the_pm_file_refusal_says_how_to_give_a_waiting_worker_the_change():
    from src.agents.tool_activity import state_tool_block_message
    message = state_tool_block_message("pm", "pm_report_check", "code_editor",
                                       {"action": "replace_chunks", "filename": "src/snake_game.py", "chunks": []})
    assert message.startswith("A Project Manager does not change project files, so this 'replace_chunks' on "
                              "'src/snake_game.py' was NOT done.")
    assert ("A worker WITHOUT an open task (waiting in worker_wait) does not act on a message: for such a worker create a "
            "task for the change (project_management add_task") in message


def test_the_worker_prompt_says_to_fix_the_test_not_other_workers_code():
    import string
    import yaml
    repo = Path(__file__).resolve().parents[2]
    text = yaml.safe_load((repo / "prompts.yaml").read_text(encoding="utf-8"))["worker_work_prompt"]
    assert "**YOUR TEST FAILS ON OTHER CODE:** first check the TEST" in text
    assert "Changing a file that a task of ANOTHER worker names (open or finished) is refused by the framework too." in text
    # still a valid format string with the same fields as before
    fields = {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name}
    assert "YOUR TEST" not in " ".join(fields)


# ======================================================================================== 4. other workers' files
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


TASK_1 = ("Kick-off Task 1: Implement the core snake game logic, state management, movement, growth, scoring, and player "
          "management in src/snake_game.py using Python standard library types only.")
TASK_3 = ("Kick-off Task 3: Write pytest tests for the snake game logic and API interactions in tests/test_snake.py, "
          "covering state changes, movement, growth, scoring, and basic endpoint response validation.")
TASK_5 = ("Kick-off Task 5: Start the server, run pytest to verify all tests pass, and demonstrate a complete round of "
          "gameplay (naming players, moving, scoring, leaderboard update) via browser on two connections, fixing any "
          "failures.")


@needs_tw
def test_a_test_writer_does_not_change_the_finished_code_of_another_worker(tasks):
    from src.tools.project_management import foreign_file_change_error
    from src.tools.executor import ToolExecutor
    w1, w2, pm1 = Worker("W1"), Worker("W2", state="worker_work"), Worker("PM1", "pm", "pm_manage")
    manager = _manager(pm1, w1, w2)
    _workspace(tasks.workspace, {"src/snake_game.py": SNAKE_GAME, "tests/test_snake.py": "def test_x():\n    pass\n"})
    t1 = _add(tasks, manager, TASK_1, "W1")
    t3 = _add(tasks, manager, TASK_3, "W2")
    finished = tasks("framework", manager, action="modify_task", task_id=t1, task_progress="finished")
    assert finished["status"] == "success", finished
    w2.current_task_id = t3
    refusal = foreign_file_change_error(manager, "W2", PROJECT, SESSION, "src/snake_game.py")
    assert refusal.startswith(f"'src/snake_game.py' was NOT changed: it is the file of the FINISHED task '{TASK_1[:87]}...' "
                              f"({t1}) of W1. Your task is 'Kick-off Task 3: Write pytest tests"), refusal
    assert "first check the TEST" in refusal and "the PM gives the fix to W1" in refusal
    assert foreign_file_change_error(manager, "W2", PROJECT, SESSION, "./src/snake_game.py") == refusal
    # its own file, W1's own file, files no task names, the PM and the framework: allowed
    assert foreign_file_change_error(manager, "W2", PROJECT, SESSION, "tests/test_snake.py") is None
    assert foreign_file_change_error(manager, "W1", PROJECT, SESSION, "src/snake_game.py") is None
    assert foreign_file_change_error(manager, "W2", PROJECT, SESSION, "src/helpers.py") is None
    assert foreign_file_change_error(manager, "PM1", PROJECT, SESSION, "src/snake_game.py") is None
    assert foreign_file_change_error(manager, "framework", PROJECT, SESSION, "src/snake_game.py") is None
    # the executor's gate: code_editor and file_system edits of the EXISTING file; reads and copies pass
    gate = ToolExecutor._foreign_file_change_message
    edit = {"action": "replace_chunks", "filename": "src/snake_game.py",
            "chunks": [{"search": "length: int = 1", "replace": "length: int = 3"}]}
    assert gate("code_editor", edit, "W2", PROJECT, SESSION, manager) == refusal
    assert gate("file_system", {"action": "find_replace", "filename": "src/snake_game.py", "find_text": "a",
                                "replace_text": "b"}, "W2", PROJECT, SESSION, manager) == refusal
    assert gate("file_system", {"action": "write", "filename": "src/snake_game.py", "content": "x = 1\n"},
                "W2", PROJECT, SESSION, manager) == refusal
    assert gate("file_system", {"action": "read", "filename": "src/snake_game.py"}, "W2", PROJECT, SESSION, manager) is None
    assert gate("file_system", {"action": "copy", "filename": "src/snake_game.py", "destination": "tmp.py"},
                "W2", PROJECT, SESSION, manager) is None
    assert gate("code_editor", dict(edit, scope="private"), "W2", PROJECT, SESSION, manager) is None
    # through the tool executor: refused before code_editor runs, the file is unchanged
    with quiet():
        answer = run(ToolExecutor().execute_tool("W2", tasks.workspace.parent, "code_editor", dict(edit),
                                                 project_name=PROJECT, session_name=SESSION, manager=manager))
    assert str(answer).startswith("Error from tool 'code_editor': 'src/snake_game.py' was NOT changed"), answer
    assert "length: int = 1" in (tasks.workspace / "src" / "snake_game.py").read_text()
    # a worker's own edit still runs
    w1.state = "worker_work"
    with quiet():
        answer = run(ToolExecutor().execute_tool("W1", tasks.workspace.parent, "code_editor", dict(edit),
                                                 project_name=PROJECT, session_name=SESSION, manager=manager))
    assert "length: int = 3" in (tasks.workspace / "src" / "snake_game.py").read_text(), answer


@needs_tw
def test_verification_and_follow_up_tasks_may_change_finished_work(tasks):
    from src.tools.project_management import foreign_file_change_error
    w1, w2, w3 = Worker("W1"), Worker("W2", state="worker_work"), Worker("W3", state="worker_work")
    manager = _manager(w1, w2, w3)
    _workspace(tasks.workspace, {"src/snake_game.py": SNAKE_GAME})
    t1 = _add(tasks, manager, TASK_1, "W1")
    t5 = _add(tasks, manager, TASK_5, "W3")
    assert tasks("framework", manager, action="modify_task", task_id=t1, task_progress="finished")["status"] == "success"
    # 'run pytest ... fixing any failures' may fix any file
    w3.current_task_id = t5
    assert foreign_file_change_error(manager, "W3", PROJECT, SESSION, "src/snake_game.py") is None
    # a task created AFTER Kick-off Task 1 was finished is follow-up work on it
    time.sleep(1.1)  # Taskwarrior keeps whole seconds
    fix = _add(tasks, manager, "Fix the score bug in the game logic", "W2")
    w2.current_task_id = fix
    assert foreign_file_change_error(manager, "W2", PROJECT, SESSION, "src/snake_game.py") is None
    # ... but not of an OPEN task of another worker
    t3 = _add(tasks, manager, "Kick-off Task 6: Add pause support in src/pause.py", "W1")
    _workspace(tasks.workspace, {"src/pause.py": "PAUSED = False\n"})
    assert foreign_file_change_error(manager, "W2", PROJECT, SESSION, "src/pause.py").startswith(
        f"'src/pause.py' was NOT changed: it is the file of the open task 'Kick-off Task 6: Add pause support in "
        f"src/pause.py' ({t3}) of W1.")


@needs_tw
def test_a_pm_is_told_that_a_waiting_worker_without_a_task_needs_a_task(tasks):
    from src.tools.project_management import idle_worker_message_note
    w1, w2, pm1 = Worker("W1"), Worker("W2", state="worker_work"), Worker("PM1", "pm", "pm_report_check")
    manager = _manager(pm1, w1, w2)
    t1 = _add(tasks, manager, TASK_1, "W1")
    assert tasks("framework", manager, action="modify_task", task_id=t1, task_progress="finished")["status"] == "success"
    note = idle_worker_message_note(manager, "PM1", "W1")
    assert note == (" NOTE: W1 is waiting and has NO open task, so this message gives it no work - it can only read it. "
                    "If W1 must change files, create a task for it: project_management action 'add_task' with a title, a "
                    "description that names the file(s) and the exact change, and assignee_agent_id='W1'.")
    # a worker's message, a worker that works, and a waiting worker with an open (even blocked) task: no note
    assert idle_worker_message_note(manager, "W2", "W1") == ""
    assert idle_worker_message_note(manager, "PM1", "W2") == ""
    _add(tasks, manager, TASK_3, "W1")
    assert idle_worker_message_note(manager, "PM1", "W1") == ""


@needs_tw
def test_the_note_is_added_to_the_pm_s_delivery_result(tasks):
    from unittest.mock import AsyncMock
    from src.agents.interaction_handler import AgentInteractionHandler
    w1, pm1 = Worker("W1"), Worker("PM1", "pm", "pm_report_check")
    pm1.status = "processing"
    manager = _manager(pm1, w1)
    manager.workflow_manager = types.SimpleNamespace(_get_agent_project_name=lambda agent, mgr: PROJECT)
    manager.schedule_cycle = AsyncMock()
    manager.send_to_ui = AsyncMock()
    handler = AgentInteractionHandler(manager)

    async def deliver():
        status, task = await handler.route_and_activate_agent_message("PM1", "W1", "Hi W1, fix spawn_food() please.")
        if task:
            await task
        return status
    with quiet():
        status = run(deliver())
    assert status["status"] == "delivered"
    assert status["message"].startswith("Message successfully delivered to 'W1'.")
    assert status["message"].endswith("and assignee_agent_id='W1'.")
    assert w1.message_history[-1]["content"].endswith("Hi W1, fix spawn_food() please.")


# ======================================================================================== 5. kick-off directories
def test_no_directories_for_dropped_planned_files():
    from src.workflows.pm_kickoff_workflow import (plan_project_structure, normalize_planned_layout,
                                                   duplicate_structure_files, prune_dirs_of_dropped_files)
    # 13:27:20 PM1's project_structure and the paths of its kick-off tasks
    structure = [{"dir": "src", "children": [{"dir": "game", "files": ["snake_game.py"]},
                                             {"dir": "server", "files": ["server.py"]}]},
                 {"dir": "tests", "files": ["test_snake.py"]}, {"dir": "docs", "files": ["README.md"]}]
    task_files = ["src/snake_game.py", "src/server.py", "tests/test_snake.py"]
    dirs, files, _notes = plan_project_structure(structure + task_files)
    dirs, files, _layout = normalize_planned_layout(dirs, files)
    structure_only = normalize_planned_layout(*plan_project_structure(structure)[:2])[1]
    task_only = normalize_planned_layout(*plan_project_structure(task_files)[:2])[1]
    dropped, _dup_notes = duplicate_structure_files(structure_only, task_only)
    assert dropped == ["src/game/snake_game.py", "src/server/server.py"]
    files = [f for f in files if f not in dropped]
    kept, notes = prune_dirs_of_dropped_files(dirs, files, dropped)
    assert kept == ["src", "tests", "docs"]
    assert notes == ["Directory 'src/game/' not created: it was planned only for file(s) that were dropped.",
                     "Directory 'src/server/' not created: it was planned only for file(s) that were dropped."]
    # a directory planned without files, and one that keeps another file, stay
    assert prune_dirs_of_dropped_files(["assets", "src", "src/game"], ["src/game/board.py"], ["src/game/snake.py"]) == \
        (["assets", "src", "src/game"], [])
    # nested: the parent goes too when nothing else is under it
    assert prune_dirs_of_dropped_files(["lib", "lib/core"], ["main.py"], ["lib/core/x.py"]) == \
        ([], ["Directory 'lib/' not created: it was planned only for file(s) that were dropped.",
              "Directory 'lib/core/' not created: it was planned only for file(s) that were dropped."])


# ======================================================================================== 6. chunks with unescaped quotes
# 13:36:39 W2's 'chunks' (the docstring quotes of the search text not escaped, those of the replace text escaped)
W2_CHUNKS = ('[{"search": "def __post_init__(self) -> None:\\n        """Initialize the snake body if not already set."""'
             '\\n        if not self.body:\\n            self.body = [Point(self.head.x - i, self.head.y) for i in '
             'range(max(0, self.length - 1), -1, -1)]\\n", "replace": "def __post_init__(self) -> None:\\n        '
             '\\"\\"\\"Initialize the snake body if not already set.\\"\\"\\"\\n        if not self.body:\\n            '
             'self.body = [Point(self.head.x - i, self.head.y) for i in range(self.length)]\\n"}]')
POST_INIT = ('class S:\n    def __post_init__(self) -> None:\n        """Initialize the snake body if not already set."""\n'
             '        if not self.body:\n            self.body = [Point(self.head.x - i, self.head.y) for i in '
             'range(max(0, self.length - 1), -1, -1)]\n')


def test_chunks_with_unescaped_quotes_are_read_by_their_keys(tmp_path):
    import json
    from src.tools.code_editor import parse_chunks_by_keys, CodeEditorTool
    with pytest.raises(json.JSONDecodeError):
        json.loads(W2_CHUNKS)
    chunks = parse_chunks_by_keys(W2_CHUNKS)
    assert chunks[0]["search"].startswith('def __post_init__(self) -> None:\n        """Initialize the snake body')
    assert chunks[0]["replace"].endswith("for i in range(self.length)]\n")
    assert '"""Initialize the snake body if not already set."""' in chunks[0]["replace"]
    # numbers, null, several chunks
    assert parse_chunks_by_keys('[{"start_line": 3, "end_line": null, "replace": "a = "b""}, {"search": "x", '
                                '"replace": "y"}]') == [{"start_line": 3, "end_line": None, "replace": 'a = "b"'},
                                                        {"search": "x", "replace": "y"}]
    # not that shape: refused (a key-like text inside a value would give a key twice)
    for bad in ('{"search": "a", "replace": "b"}', '[{"search": "a", "replace": "b", "search": "c"}]',
                '[{"search": "a"}]', '[{"other": "a", "replace": "b"}]', '[{"search": "a" "replace": "b"}]'):
        with pytest.raises(ValueError):
            parse_chunks_by_keys(bad)
    # through the tool: the edit is applied with a note
    (tmp_path / "snake.py").write_text(POST_INIT, encoding="utf-8")
    with quiet():
        result = run(CodeEditorTool().execute("W2", tmp_path, action="replace_chunks", filename="snake.py",
                                              scope="private", chunks=W2_CHUNKS))
    assert result["status"] == "success", result
    assert "quotes (\") that were not escaped; the framework read it by its keys" in result["message"]
    assert "for i in range(self.length)]" in (tmp_path / "snake.py").read_text()


# ======================================================================================== 7. a file written in decompose
def test_a_cut_off_in_decompose_gets_the_work_budget():
    from src.agents.core import budget_after_cut_off
    assert budget_after_cut_off(2048, 0, 8192) == 2048          # no cut-off: the decompose budget
    assert budget_after_cut_off(2048, 1, 8192) == 8192          # was 4096
    assert budget_after_cut_off(2048, 3, 8192) == 8192
    assert budget_after_cut_off(2048, 1, None) == 4096          # other states: unchanged doubling
    assert budget_after_cut_off(4096, 2, 8192) == 16384
    assert budget_after_cut_off(None, 1, 8192) is None


def test_the_worker_s_next_call_after_a_cut_off_in_decompose(monkeypatch):
    from src.agents.core import Agent
    import src.agents.core as core_module

    class Provider:
        def __init__(self):
            self.max_tokens_seen = []

        async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
            self.max_tokens_seen.append(max_tokens)
            yield {"type": "finish_info", "done_reason": "length", "output_tokens": max_tokens}

    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", False)
    monkeypatch.setattr(core_module.settings, "WORKER_DECOMPOSE_STATE_MAX_TOKENS", 2048)
    monkeypatch.setattr(core_module.settings, "WORKER_WORK_STATE_MAX_TOKENS", 8192)

    async def no_workflow(*_args, **_kwargs):
        return None
    manager = types.SimpleNamespace(tool_executor=types.SimpleNamespace(tools={}), cycle_handler=None,
                                    workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow))
    provider = Provider()
    agent = Agent({"agent_id": "W2", "config": {"agent_type": "worker", "provider": "openrouter", "model": "m"}},
                  provider, manager)
    agent.state = "worker_decompose"
    agent.ensure_sandbox_exists = lambda: True

    async def consume():
        return [event async for event in agent.process_message()]
    with quiet():
        run(consume())
        run(consume())
    assert provider.max_tokens_seen == [2048, 8192]


# ======================================================================================== 8. the user's stop
def test_the_stop_stamps():
    from src.agents.user_stop import (mark_stopped, mark_resumed, stopped_during_cycle, resumed_since_stop,
                                      discarded_reply_note)
    agent = types.SimpleNamespace()
    assert not stopped_during_cycle(agent, 100.0) and not resumed_since_stop(agent)
    mark_stopped(agent, 150.0)
    assert stopped_during_cycle(agent, 100.0) and not stopped_during_cycle(agent, 200.0)
    assert not resumed_since_stop(agent)
    mark_resumed(agent, 160.0)
    assert resumed_since_stop(agent)
    mark_stopped(agent, 170.0)
    assert not resumed_since_stop(agent)
    assert discarded_reply_note({"type": "tool_requests", "calls": [{"name": "code_editor"}, {"name": "file_system"}]}) == (
        "[Framework Note]: The user STOPPED the project while your last reply was being written, so its tool call(s) "
        "(code_editor, file_system) were NOT run. Nothing changed. When the project is resumed, check the current state "
        "before you act again.")
    assert "its request to change to the state 'worker_work' was NOT applied" in discarded_reply_note(
        {"type": "agent_state_change_requested", "requested_state": "worker_work"})


def _cycle_setup(state="worker_work"):
    from unittest.mock import AsyncMock, MagicMock
    from src.agents.constants import AGENT_TYPE_WORKER, AGENT_STATUS_IDLE
    from src.agents.core import Agent
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager
    manager = MagicMock(spec=AgentManager)
    manager.db_manager = AsyncMock()
    manager.performance_tracker = AsyncMock()
    manager.send_to_ui = AsyncMock()
    manager.schedule_cycle = AsyncMock(return_value="task")
    manager.current_project, manager.current_session, manager.current_session_db_id = None, "s", None
    manager._agent_cycle_locks = {}
    manager.state_manager = MagicMock()
    manager.state_manager.get_agent_team.return_value = None
    manager.workflow_manager = MagicMock()
    manager.workflow_manager.get_system_prompt.return_value = "worker prompt"
    manager.agents = {}
    agent = MagicMock(spec=Agent)
    agent.agent_id, agent.agent_type, agent.state, agent.status = "W2", AGENT_TYPE_WORKER, state, AGENT_STATUS_IDLE
    agent.llm_provider, agent.provider_kwargs, agent.max_tokens = None, {}, None
    agent.provider_name, agent.model, agent.persona = "openrouter", "nvidia/nemotron-3.5-lightning:free", "Writer"
    agent.message_history = [{"role": "system", "content": "worker prompt"}]
    agent._failover_state, agent._failed_models_this_cycle = {}, set()
    agent._last_reply_cut_off = agent._think_off_next_call = agent._context_window_full = False
    agent._empty_response_retry_count = 0
    agent.needs_priority_recheck = False
    agent.active_task_id = None
    agent.set_state = MagicMock(side_effect=lambda new_state: setattr(agent, "state", new_state))
    interaction_handler = MagicMock()
    interaction_handler.execute_single_tool = AsyncMock(return_value=None)
    handler = AgentCycleHandler(manager, interaction_handler)
    return handler, agent, manager, interaction_handler


def _stopped_reply_cycle(handler, agent, resume=False, event=None):
    from unittest.mock import MagicMock
    from src.agents.user_stop import mark_stopped, mark_resumed

    async def events(*_args, **_kwargs):
        # 13:40:47 the stop route moved the agent to worker_wait while its reply was being written
        mark_stopped(agent)
        agent.state = "worker_wait"
        if resume:
            mark_resumed(agent, time.time() + 0.001)
            agent.state = "worker_work"
        yield event or {"type": "tool_requests", "content_for_history": "",
                        "calls": [{"id": "c1", "name": "code_editor",
                                   "arguments": {"action": "replace_chunks", "filename": "src/snake_game.py",
                                                 "chunks": [{"search": "length: int = 3", "replace": "length: int = 1"}]}}]}
    agent.process_message = MagicMock(side_effect=events)
    with quiet():
        asyncio.run(handler.run_cycle(agent, 0))


def test_a_reply_that_arrives_after_the_stop_runs_no_tool():
    from unittest.mock import MagicMock
    # control: without a stop the same reply's tool call runs
    handler, agent, manager, interaction_handler = _cycle_setup()

    async def events(*_args, **_kwargs):
        yield {"type": "tool_requests", "content_for_history": "",
               "calls": [{"id": "c1", "name": "code_editor", "arguments": {"action": "replace_chunks",
                                                                           "filename": "x.py", "chunks": []}}]}
    agent.process_message = MagicMock(side_effect=events)
    with quiet():
        asyncio.run(handler.run_cycle(agent, 0))
    assert interaction_handler.execute_single_tool.call_count == 1
    # a stop that came BEFORE the cycle began does not drop anything either
    from src.agents.user_stop import mark_stopped
    handler, agent, manager, interaction_handler = _cycle_setup()
    mark_stopped(agent, time.time() - 60)
    agent.process_message = MagicMock(side_effect=events)
    with quiet():
        asyncio.run(handler.run_cycle(agent, 0))
    assert interaction_handler.execute_single_tool.call_count == 1
    # the stop during the cycle
    handler, agent, manager, interaction_handler = _cycle_setup()
    _stopped_reply_cycle(handler, agent)
    interaction_handler.execute_single_tool.assert_not_called()
    notes = [m["content"] for m in agent.message_history if "The user STOPPED the project" in str(m.get("content"))]
    assert notes == ["[Framework Note]: The user STOPPED the project while your last reply was being written, so its tool "
                     "call(s) (code_editor) were NOT run. Nothing changed. When the project is resumed, check the current "
                     "state before you act again."]
    manager.schedule_cycle.assert_not_called()
    assert agent.state == "worker_wait"
    # a state change requested in such a reply is not applied either
    handler, agent, manager, _ih = _cycle_setup()
    _stopped_reply_cycle(handler, agent, event={"type": "agent_state_change_requested", "requested_state": "worker_work"})
    assert agent.state == "worker_wait"
    manager.workflow_manager.change_state.assert_not_called()


def test_a_reply_of_a_project_that_was_resumed_meanwhile_is_dropped_and_the_agent_runs_again():
    handler, agent, manager, interaction_handler = _cycle_setup()
    _stopped_reply_cycle(handler, agent, resume=True)
    interaction_handler.execute_single_tool.assert_not_called()
    assert manager.schedule_cycle.await_count >= 1 or manager.schedule_cycle.call_count >= 1

# END OF FILE tests/unit/test_round3o.py
