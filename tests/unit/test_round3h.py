# START OF FILE tests/unit/test_round3h.py
"""
Round 3h: the run of 2026-10-24 10:00-11:43 (qwen3.5:4b-q4_K_M, WiFi_Snake_Multiplayer_Game, Python + a browser UI).

* W1 (Kick-off Task 1: core game engine in src/game.py) wrote tests/test_game/test_game.py itself at 10:07:57 - the job of
  "Kick-off Task 7: Write pytest tests ... in tests/", assigned to W2 - and then changed src/game.py 45 times in 1.5 hours,
  because the same 8 tests kept failing. The TESTS were wrong: they expected the snake to eat food placed at random, a
  collision for a straight snake, `body[1] == (-2, 0)` after a turn.
* W2 changed tests/test_network/test_network.py 32 times; its mocks set `send.return_value` while the code reads `recv()`.
* The Round 3g isolation check ran 53 times (5 extra pytest runs each) and never found an order-dependent test.
"""
import asyncio
import logging
import shutil
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Multiplayer_Game", "startup_1792807214"

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
    clear_cache()
    yield
    clear_cache()


def _workspace(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# The run's files (shortened): the code is right, the tests are wrong
GAME_PY = '''import random


class Snake:
    def __init__(self, body=None):
        self.body = body or [(0, 0), (-1, 0), (-2, 0)]

    def move(self, direction):
        head = (self.body[0][0] + direction[0], self.body[0][1] + direction[1])
        return Snake([head] + self.body[:-1])

    def is_colliding_with_body(self):
        return self.body[0] in self.body[1:]


class Game:
    def __init__(self):
        self.snake = Snake()
        self.food = (random.randint(5, 20), random.randint(5, 20))
        self.score = 0
'''
TEST_GAME_PY = '''from game import Game, Snake


def test_snake_move():
    snake = Snake(body=[(0, 0), (-1, 0), (-2, 0)])
    assert snake.move((-1, 0)).body[1] == (-2, 0)


def test_snake_is_colliding_with_body():
    assert Snake(body=[(0, 0), (0, -1), (0, -2)]).is_colliding_with_body()


def test_snake_length():
    assert len(Snake().body) == 3
'''
CONFTEST = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
'''


def _pytest(ws):
    command = f"{sys.executable} -m pytest -q -p no:cacheprovider tests/test_game/test_game.py"
    out = subprocess.run(command, shell=True, cwd=ws, capture_output=True, text=True)
    return command, out.stdout + out.stderr


def _touch(path, text):
    path.write_text(path.read_text() + text, encoding="utf-8")


# ======================================================================================== 1. the same tests keep failing
def test_the_same_failures_after_code_changes_point_at_the_test(tmp_path):
    from src.utils.test_diagnosis import diagnose_failed_test_run, STUCK_RUNS
    ws = _workspace(tmp_path, {"conftest.py": CONFTEST, "src/game.py": GAME_PY,
                               "tests/test_game/test_game.py": TEST_GAME_PY})
    notes = []
    for number in range(1, STUCK_RUNS + 4):
        _touch(ws / "src" / "game.py", f"\n# edit {number}\n")
        command, output = _pytest(ws)
        with quiet():
            notes.append(run(diagnose_failed_test_run(ws, command, output)))
    assert "FAILED tests/test_game/test_game.py::test_snake_move" in output
    assert all("have failed in each of" not in note for note in notes[:STUCK_RUNS - 1])
    note = notes[STUCK_RUNS - 1]
    assert ("[Framework check - the same 2 test(s) have failed in each of the last 4 runs, although src/game.py changed "
            "in between:]\nOnly the code changed - the test file(s) did not. So the TEST may be what is wrong.") in note
    assert "food placed at random" in note and "`send.return_value`" in note
    assert "Do not bend the code to an impossible assertion" in note
    assert all("have failed in each of" not in note for note in notes[STUCK_RUNS:STUCK_RUNS + 2])   # every 3rd run again
    assert "in each of the last 7 runs" in notes[STUCK_RUNS + 2]


def test_test_changes_that_do_not_help_and_resets(tmp_path):
    from src.utils.test_diagnosis import stuck_note, STUCK_RUNS
    ws = _workspace(tmp_path, {"conftest.py": CONFTEST, "src/game.py": GAME_PY,
                               "tests/test_game/test_game.py": TEST_GAME_PY})
    test_file = ws / "tests" / "test_game" / "test_game.py"
    command, output = _pytest(ws)
    assert stuck_note(ws, output) is None
    # a run without any change between does not count
    assert stuck_note(ws, output) is None
    for number in range(2, STUCK_RUNS):
        _touch(test_file, f"\n# edit {number}\n")
        assert stuck_note(ws, _pytest(ws)[1]) is None
    _touch(test_file, "\n# last edit\n")
    note = stuck_note(ws, _pytest(ws)[1])
    assert note.startswith("[Framework check - the same 2 test(s) have failed in each of the last 4 runs, although "
                           "tests/test_game/test_game.py changed in between:]\nThese changes do not reach the cause.")
    # other failing tests start a new count
    other = "FAILED tests/test_game/test_game.py::test_snake_length - assert 2 == 3\n1 failed, 2 passed"
    _touch(test_file, "\n# x\n")
    assert stuck_note(ws, other) is None
    assert stuck_note(ws, "3 passed") is None


def test_the_isolation_check_is_not_repeated_for_unchanged_tests(tmp_path, monkeypatch):
    import src.utils.test_diagnosis as td
    ws = _workspace(tmp_path, {"conftest.py": CONFTEST, "src/game.py": GAME_PY,
                               "tests/test_game/test_game.py": TEST_GAME_PY})
    calls = []

    async def counting(workspace, command, output, budget=td.ISOLATION_BUDGET_SECONDS):
        calls.append(command)
        return None
    monkeypatch.setattr(td, "isolation_check", counting)
    command, output = _pytest(ws)
    with quiet():
        run(td.diagnose_failed_test_run(ws, command, output))
        _touch(ws / "src" / "game.py", "\n# code edit\n")
        run(td.diagnose_failed_test_run(ws, command, _pytest(ws)[1]))
    assert len(calls) == 1                                                   # only the code changed
    _touch(ws / "tests" / "test_game" / "test_game.py", "\n# test edit\n")
    with quiet():
        run(td.diagnose_failed_test_run(ws, command, _pytest(ws)[1]))
    assert len(calls) == 2                                                   # the tests changed: check again


# ======================================================================================== 2. another worker's test folder
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
    (tmp_path / "projects" / PROJECT / SESSION / "shared_workspace").mkdir(parents=True)

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    return call


def _add(call, manager, text, assignee):
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT,
                 assignee_agent_id=assignee)
    assert added["status"] == "success", added
    return added["task_uuid"]


TASK_1 = ("Kick-off Task 1: Implement core game engine with snake movement, food generation, collision detection, and "
          "scoring in src/game.py")
TASK_7 = "Kick-off Task 7: Write pytest tests for game mechanics, networking, player management, and leaderboard in tests/"


@needs_tw
def test_a_worker_does_not_create_tests_in_another_worker_s_test_folder(tasks):
    from src.tools.project_management import foreign_file_creation_error
    w1, w2 = Agent("W1", state="worker_work"), Agent("W2")
    manager = _manager(w1, w2)
    t1 = _add(tasks, manager, TASK_1, "W1")
    t7 = _add(tasks, manager, TASK_7, "W2")
    w1.current_task_id = t1
    refusal = foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "tests/test_game/test_game.py")
    assert refusal.startswith(f"The file was NOT created: 'tests/test_game/test_game.py' is the file of the task '{TASK_7[:87]}"
                              f"...' ({t7}), which is assigned to W2.")
    # its own file, a non-test file in tests/, a test file elsewhere: allowed
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "src/game.py") is None
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "tests/fixtures.json") is None
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "tests/conftest.py") is None
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "src/test_helpers.py") is None
    # W2 (the test task's worker) may, and a worker whose own task is about tests may
    w2.current_task_id = t7
    assert foreign_file_creation_error(manager, "W2", PROJECT, SESSION, "tests/test_game/test_game.py") is None


@needs_tw
def test_a_worker_whose_task_is_about_tests_is_not_refused(tasks):
    from src.tools.project_management import foreign_file_creation_error
    w1, w2 = Agent("W1", state="worker_work"), Agent("W2")
    manager = _manager(w1, w2)
    t_fix = _add(tasks, manager, "Fix the failing tests of the game engine in src/game.py", "W1")
    _add(tasks, manager, TASK_7, "W2")
    w1.current_task_id = t_fix
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "tests/test_game/test_regression.py") is None

# END OF FILE tests/unit/test_round3h.py
