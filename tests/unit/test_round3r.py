# START OF FILE tests/unit/test_round3r.py
"""
Round 3r: the run of 2026-10-26 07:06-09:05 (WiFi_Snake_Game, qwen3.5:4b-q4_K_M on a remote Ollama) - stuck on Kick-off
Task 6 "Write pytest tests for game logic, player management, and API endpoints in tests/" from 07:27 to 09:05.

* 07:28:56-07:51 W1 finished Task 7; its other task (Task 8, "Run the tests and start the server") waited for Task 6. W1
  stayed in worker_work and changed src/snake_game.py 12 times to fit W2's half-written tests.
* tests/test_snake_game.py imported `from src.player_manager import PlayerManager` and `from src.api import ...`, while
  src/api.py imported `from player_manager import PlayerManager`: src/player_manager.py was loaded twice, as two modules.
* src/api.py creates `player_manager = PlayerManager()` and `leaderboard = Leaderboard()` at import; PlayerManager kept its
  players in a CLASS attribute `_players: Dict[int, Player] = {}`. Tests saw each other's players; the diagnosis said only
  "1 of 5 failed test(s) passed when run alone".
* 08:58 `def test_can_move_same_direction(self):` lost its class indentation; two tests below it were nested inside it.
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

PROJECT, SESSION = "WiFi_Snake_Game", "startup_1792969618"

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


PLAYER_MANAGER_PY = '''"""Player Manager Module for WiFi Snake Game."""
import json
import os
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class Player:
    id: int
    name: str
    score: int = 0


class PlayerManager:
    """Manages player registration."""

    # In-memory storage for players
    _players: Dict[int, Player] = {}
    _next_id: int = 1

    def __init__(self):
        self._load_players()

    def _load_players(self):
        if os.path.exists('config/players.json'):
            with open('config/players.json') as f:
                for item in json.load(f).get('players', []):
                    self._players[item['id']] = Player(item['id'], item['name'], item.get('score', 0))

    def register_player(self, name: str) -> Optional[int]:
        player_id = self._next_id
        self._next_id += 1
        self._players[player_id] = Player(player_id, name)
        return player_id

    def get_all_players(self):
        return list(self._players.values())
'''
API_PY = '''"""REST API Module for WiFi Snake Game."""
from player_manager import PlayerManager

# Initialize managers
player_manager = PlayerManager()


def register_player(name: str) -> int:
    return player_manager.register_player(name)


def count_players() -> int:
    return len(player_manager.get_all_players())


def version() -> str:
    return "1.0"
'''
TEST_PY = '''import pytest
from src.player_manager import PlayerManager
from src.api import register_player, count_players


class TestPlayers:
    def test_register(self):
        register_player("Alice")
        assert count_players() == 1

    def test_register_two(self):
        register_player("Alice")
        register_player("Bob")
        assert count_players() == 2
'''
PYTEST_OUTPUT = """
=================================== FAILURES ===================================
________________________ TestPlayers.test_register_two _________________________
tests/test_players.py:13: in test_register_two
    assert count_players() == 2
E   assert 3 == 2
=========================== short test summary info ============================
FAILED tests/test_players.py::TestPlayers::test_register_two - assert 3 == 2
1 failed, 1 passed in 0.05s
"""


def _project(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "player_manager.py").write_text(PLAYER_MANAGER_PY, encoding="utf-8")
    (root / "src" / "api.py").write_text(API_PY, encoding="utf-8")
    (root / "tests" / "test_players.py").write_text(TEST_PY, encoding="utf-8")
    return root


def _pytest(root: Path):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly"], cwd=root,
                          capture_output=True, text=True, timeout=120)


# ======================================================================================== 1. a worker whose task waits
class Worker:
    def __init__(self, agent_id, agent_type="worker", state="worker_work"):
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


def _add(call, manager, text, assignee=None, depends=None):
    args = {"assignee_agent_id": assignee} if assignee else {}
    if depends:
        args["depends"] = depends
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert added["status"] == "success", added
    return added["task_uuid"]


TASK_6 = "Kick-off Task 6: Write pytest tests for game logic, player management, and API endpoints in tests/"
TASK_7 = "Kick-off Task 7: Write README.md with installation instructions, usage guide, and mobile hosting details"
TASK_8 = "Kick-off Task 8: Run the tests and start the server to verify the game works correctly on localhost"


@needs_tw
def test_a_worker_whose_tasks_all_wait_changes_no_files(tasks):
    from src.tools.project_management import blocked_worker_change_error
    from src.tools.executor import ToolExecutor
    w1, w2 = Worker("W1"), Worker("W2")
    manager = _manager(w1, w2)
    t6 = _add(tasks, manager, TASK_6, "W2")
    t7 = _add(tasks, manager, TASK_7, "W1")
    t8 = _add(tasks, manager, TASK_8, "W1", depends=t6)
    # 07:27: Task 7 can be worked on - nothing is refused
    assert blocked_worker_change_error(manager, "W1", PROJECT, SESSION, "src/snake_game.py") is None
    tasks("W1", manager, action="modify_task", task_id=t7, task_progress="finished")
    # 07:29:32: only Task 8 is left, and it waits for Task 6 (W2)
    message = blocked_worker_change_error(manager, "W1", PROJECT, SESSION, "./src/snake_game.py")
    assert message == (f"'src/snake_game.py' was NOT changed: you have NO task you can work on now. Your task 'Kick-off Task "
                       f"8: Run the tests and start the server to verify the game works correctly ...' ({t8}) "
                       f"waits for 'Kick-off Task 6: Write pytest tests for game logic, player management, and API "
                       f"endpoint...' ({t6}), which W2 is working on, to be finished - until then its files are still "
                       "changing. Do not change files meanwhile: call the 'request_state' tool with state='worker_wait'. You "
                       "are started automatically as soon as that task is finished.")
    # the executor applies it to file changes in the shared workspace only
    edit = {"action": "replace_chunks", "filename": "src/snake_game.py", "chunks": [{"search": "a", "replace": "b"}]}
    with quiet():
        assert ToolExecutor._blocked_worker_message("code_editor", edit, "W1", PROJECT, SESSION, manager) == message
        assert ToolExecutor._blocked_worker_message("file_system", {"action": "read", "filename": "src/snake_game.py"},
                                                    "W1", PROJECT, SESSION, manager) is None
        assert ToolExecutor._blocked_worker_message("file_system", {"action": "write", "filename": "notes.md",
                                                                    "scope": "private"}, "W1", PROJECT, SESSION,
                                                    manager) is None
        assert ToolExecutor._blocked_worker_message("command_executor", {"command": "python -m pytest -q"}, "W1", PROJECT,
                                                    SESSION, manager) is None
    # W2 works on Task 6; PMs and the framework are not judged
    assert blocked_worker_change_error(manager, "W2", PROJECT, SESSION, "tests/test_snake_game.py") is None
    pm_manager = _manager(w1, w2, Worker("PM1", "pm", "pm_manage"))
    assert blocked_worker_change_error(pm_manager, "PM1", PROJECT, SESSION, "src/snake_game.py") is None
    assert blocked_worker_change_error(manager, "framework", PROJECT, SESSION, "src/snake_game.py") is None
    # when Task 6 is finished, Task 8 can start
    tasks("W2", manager, action="modify_task", task_id=t6, task_progress="finished")
    assert blocked_worker_change_error(manager, "W1", PROJECT, SESSION, "src/snake_game.py") is None


@needs_tw
def test_a_worker_without_open_tasks_is_not_judged_here(tasks):
    from src.tools.project_management import blocked_worker_change_error
    w1 = Worker("W1")
    manager = _manager(w1)
    assert blocked_worker_change_error(manager, "W1", PROJECT, SESSION, "src/game.py") is None
    t1 = _add(tasks, manager, "Kick-off Task 1: Implement src/game.py", "W1")
    tasks("W1", manager, action="modify_task", task_id=t1, task_progress="finished")
    assert blocked_worker_change_error(manager, "W1", PROJECT, SESSION, "src/game.py") is None


# ======================================================================================== 2. one module per file
def test_the_framework_conftest_loads_each_file_once(tmp_path):
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST, FRAMEWORK_CONFTEST_1U
    root = _project(tmp_path / "game")
    (root / "tests" / "test_identity.py").write_text(
        "import src.api\nimport api\nimport player_manager\nfrom src.player_manager import PlayerManager\n"
        "from unittest import mock\n\n\n"
        "def test_one_module():\n    assert src.api is api and src.api.__name__ == 'api'\n"
        "    assert src.api.__spec__.name == 'api'\n"
        "    assert PlayerManager is player_manager.PlayerManager is api.PlayerManager\n\n\n"
        "def test_patch_by_name():\n    with mock.patch('src.api.version', return_value='x'):\n"
        "        assert api.version() == 'x'\n", encoding="utf-8")
    (root / "tests" / "test_players.py").unlink()
    # Round 1u - 3q: two modules for one file
    (root / "conftest.py").write_text(FRAMEWORK_CONFTEST_1U, encoding="utf-8")
    old = _pytest(root)
    assert old.returncode == 1 and "2 failed" in old.stdout, old.stdout   # two classes; the patch missed api.version
    # Round 3r: one module
    (root / "conftest.py").write_text(FRAMEWORK_CONFTEST, encoding="utf-8")
    new = _pytest(root)
    assert new.returncode == 0, new.stdout + new.stderr
    assert "2 passed" in new.stdout


def test_packages_and_relative_imports_still_load(tmp_path):
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST
    root = tmp_path / "game"
    (root / "src" / "engine").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "conftest.py").write_text(FRAMEWORK_CONFTEST, encoding="utf-8")
    (root / "src" / "engine" / "__init__.py").write_text("from .board import Board\n", encoding="utf-8")
    (root / "src" / "engine" / "board.py").write_text("from . import rules\n\n\nclass Board:\n    size = rules.SIZE\n",
                                                      encoding="utf-8")
    (root / "src" / "engine" / "rules.py").write_text("SIZE = 20\n", encoding="utf-8")
    (root / "src" / "stats.py").write_text("from .engine import Board\n", encoding="utf-8")  # only works as src.stats
    (root / "tests" / "test_engine.py").write_text(
        "import src.engine.board\nimport engine.board\nfrom src.engine import Board\nimport src.stats\n\n\n"
        "def test_engine():\n    assert src.engine.board is engine.board and Board.size == 20\n"
        "    assert src.stats.Board is Board and src.stats.__name__ == 'src.stats'\n", encoding="utf-8")
    result = _pytest(root)
    assert result.returncode == 0, result.stdout + result.stderr
    # a project whose src/ is a package of its own is left as it is
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_engine.py").write_text(
        "import src.engine.board\n\n\ndef test_engine():\n    assert src.engine.board.__name__ == 'src.engine.board'\n",
        encoding="utf-8")
    result = _pytest(root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_earlier_framework_conftest_is_updated(tmp_path):
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST, FRAMEWORK_CONFTEST_1U, refresh_framework_conftest
    conftest = tmp_path / "conftest.py"
    assert refresh_framework_conftest(tmp_path) is False                     # none
    conftest.write_text(FRAMEWORK_CONFTEST_1U, encoding="utf-8")
    with quiet():
        assert refresh_framework_conftest(tmp_path) is True
    assert conftest.read_text(encoding="utf-8") == FRAMEWORK_CONFTEST
    assert refresh_framework_conftest(tmp_path) is False                     # already current
    own = FRAMEWORK_CONFTEST_1U + "\nimport os\n"                            # changed by the project: not touched
    conftest.write_text(own, encoding="utf-8")
    assert refresh_framework_conftest(tmp_path) is False and conftest.read_text(encoding="utf-8") == own
    assert refresh_framework_conftest(None) is False


def test_the_kickoff_and_the_test_runner_update_it(tmp_path, monkeypatch):
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST, FRAMEWORK_CONFTEST_1U
    from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow
    (tmp_path / "conftest.py").write_text(FRAMEWORK_CONFTEST_1U, encoding="utf-8")
    with quiet():
        PMKickoffWorkflow()._write_test_scaffold(tmp_path, ["src/game.py"])
    assert (tmp_path / "conftest.py").read_text(encoding="utf-8") == FRAMEWORK_CONFTEST
    # the test runner, before a pytest run (here in a private sandbox)
    from src.tools.test_runner import TestRunnerTool
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "conftest.py").write_text(FRAMEWORK_CONFTEST_1U, encoding="utf-8")
    (sandbox / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    with quiet():
        result = run(TestRunnerTool().execute("W1", sandbox, command=f"{sys.executable} -m pytest -q -p no:cacheprovider",
                                              scope="private"))
    assert (sandbox / "conftest.py").read_text(encoding="utf-8") == FRAMEWORK_CONFTEST
    assert result["status"] == "success", result


def test_the_kickoff_module_still_offers_the_names():
    from src.workflows import pm_kickoff_workflow
    from src.utils import framework_conftest
    assert pm_kickoff_workflow.FRAMEWORK_CONFTEST is framework_conftest.FRAMEWORK_CONFTEST
    assert framework_conftest.FRAMEWORK_CONFTEST.startswith('"""' + framework_conftest.FRAMEWORK_CONFTEST_MARKER)


# ======================================================================================== 3. state the tests share
def test_a_mutable_class_attribute_is_named_after_a_write(tmp_path):
    from src.tools.python_static_check import python_code_notes
    from src.utils.task_files import python_file_problems
    notes = python_code_notes(PLAYER_MANAGER_PY, "src/player_manager.py")
    assert notes == ["class 'PlayerManager' keeps '_players' as a CLASS attribute (line 19) and its methods change it "
                     "through self._players (line 29): every PlayerManager instance shares that one dict, so a new "
                     "PlayerManager() still holds what earlier instances added (tests see each other's data). Create it per "
                     "instance in __init__: `self._players = {}`. If all instances are MEANT to share it, write "
                     "PlayerManager._players instead of self._players."]
    # a note after the write, not a reason to refuse finishing a task
    assert python_code_notes(PLAYER_MANAGER_PY, "src/player_manager.py", advisory=False) == []
    path = tmp_path / "player_manager.py"
    path.write_text(PLAYER_MANAGER_PY, encoding="utf-8")
    assert python_file_problems(path, "src/player_manager.py") == []


def test_other_class_attributes_are_not_named():
    from src.tools.python_static_check import shared_mutable_class_attributes, parse_python
    per_instance = PLAYER_MANAGER_PY.replace("        self._load_players()",
                                             "        self._players = {}\n        self._load_players()")
    assert shared_mutable_class_attributes(parse_python(per_instance)) == []
    by_class = PLAYER_MANAGER_PY.replace("self._players[", "PlayerManager._players[")
    assert shared_mutable_class_attributes(parse_python(by_class)) == []          # sharing on purpose
    read_only = "class Config:\n    DEFAULTS = {'a': 1}\n\n    def __init__(self):\n        self.a = self.DEFAULTS['a']\n"
    assert shared_mutable_class_attributes(parse_python(read_only)) == []
    no_init = "class Registry:\n    items = []\n\n    def add(self, x):\n        self.items.append(x)\n"
    assert shared_mutable_class_attributes(parse_python(no_init)) == []
    appended = ("class Log:\n    lines = list()\n\n    def __init__(self, name):\n        self.name = name\n\n"
                "    def write(self, text):\n        self.lines.append(text)\n")
    found = shared_mutable_class_attributes(parse_python(appended))
    assert found == [{"class": "Log", "name": "lines", "line": 2, "kind": "list", "changed_line": 8}]


def test_the_failed_run_names_the_objects_the_tests_share(tmp_path):
    from src.utils.test_diagnosis import shared_module_state_hints, diagnose_failed_test_run
    root = _project(tmp_path)
    hints = shared_module_state_hints(root, PYTEST_OUTPUT)
    assert hints == [
        "src/api.py creates `player_manager = PlayerManager()` (line 5) ONCE, when it is imported, so every test shares "
        "these objects - register_player, count_players use them. What one test adds (e.g. a registered player or a score) "
        "is still there in the next test, and a fixture that deletes a data file does not reset them: they read it at "
        "import, before any fixture ran. They also read and write the project's own data file(s) config/players.json, so "
        "what earlier test runs saved there comes back. Give each test fresh objects in an autouse fixture: delete the data "
        "files first, then e.g. `import src.api` and `src.api.player_manager = PlayerManager()`. A new instance alone does "
        "not help here: PlayerManager._players (src/player_manager.py line 19) is a CLASS attribute that all instances share "
        "- it belongs in __init__ (`self._players = ...`); until that is fixed the fixture must empty it too "
        "(`PlayerManager._players.clear()`). Do not change the assertions for this."]
    with quiet():
        text = run(diagnose_failed_test_run(root, "python -m pytest -q", PYTEST_OUTPUT))
    assert "[Framework check - state the tests share:]\n- src/api.py creates `player_manager = PlayerManager()`" in text
    # tests that import only functions which use no shared object, or a run without failed pytest tests: nothing
    (root / "tests" / "test_players.py").write_text(TEST_PY.replace("register_player, count_players", "version"),
                                                    encoding="utf-8")
    assert shared_module_state_hints(root, PYTEST_OUTPUT) == []
    (root / "tests" / "test_players.py").write_text(TEST_PY, encoding="utf-8")
    assert shared_module_state_hints(root, "1 passed in 0.01s") == []
    assert shared_module_state_hints(None, PYTEST_OUTPUT) == []


def test_the_advice_works(tmp_path):
    """The fixture the hint describes makes the run's failing tests pass (with the Round 3r conftest)."""
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST
    root = _project(tmp_path / "game")
    (root / "conftest.py").write_text(FRAMEWORK_CONFTEST, encoding="utf-8")
    assert "1 failed, 1 passed" in _pytest(root).stdout
    fixture = ("import src.api\n\n\n@pytest.fixture(autouse=True)\ndef fresh_players():\n"
               "    PlayerManager._players.clear()\n    src.api.player_manager = PlayerManager()\n\n\n")
    (root / "tests" / "test_players.py").write_text(TEST_PY.replace("class TestPlayers:", fixture + "class TestPlayers:"),
                                                    encoding="utf-8")
    result = _pytest(root)
    assert result.returncode == 0, result.stdout


# ======================================================================================== 4. a test outside its class
def test_a_test_method_that_lost_its_class_indentation():
    from src.tools.python_static_check import python_code_notes
    source = ("class TestSnake:\n    def test_move(self):\n        assert True\n\n"
              "def test_can_move_same_direction(self):\n    assert True\n\n"
              "    def test_food_not_on_snake_body(self):\n        assert True\n\n"
              "    def test_food_generates_valid_position(self):\n        assert True\n")
    notes = python_code_notes(source, "tests/test_snake_game.py")
    assert notes == ["test 'test_can_move_same_direction' (line 5) is a module-level function with a 'self' parameter: it "
                     "lost the indentation of its class, so pytest fails it with \"fixture 'self' not found\", and the 2 "
                     "test(s) indented below it (test_food_not_on_snake_body, test_food_generates_valid_position) are now "
                     "nested INSIDE it, so they never run. Indent it (and the tests after it) back into the class."]
    assert python_code_notes(source, "tests/test_snake_game.py", advisory=False) == notes   # a real defect
    assert python_code_notes("def test_a(tmp_path):\n    assert tmp_path\n", "tests/test_a.py") == []

# END OF FILE tests/unit/test_round3r.py
