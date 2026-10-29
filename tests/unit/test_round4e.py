# START OF FILE tests/unit/test_round4e.py
"""
Round 4e: the run of 2026-10-29 10:06-13:06 (qwen3.5:4b-q4_K_M, WiFi_Multiplayer_Snake_Game). Kick-off Tasks 1-7 and 9 were
finished by 12:22; W2 worked on "Kick-off Task 8: Run all tests and the game server to verify the complete system works
end-to-end" from 11:08 to the end of the run.

* The full pytest run failed in FOUR test files (14 + 6 + 1 + 21 failing tests) written by W1 and W3. Task 8 could not be
  finished before the suite passed, and a "run the tests" task could change any file - so W2 (Technical Writer) rewrote other
  workers' tests and src/websocket_server.py for two hours, was forced to report 9 times and sent back to work each time.
  PM1 told it "STOP trying file operations. Task 8 is about RUNNING TESTS"; its own finish was refused at 12:53.
* 12:12:15 W1, forced to report twice on "Fix test failures in test_snake_logic.py: ...", set the task to 'decomposed'
  (it had no sub-tasks) and went to worker_wait - the task left every open list without a finish check.
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

PROJECT, SESSION = "WiFi_Multiplayer_Snake_Game", "startup_1793239585"
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


# the short summary of the run's `python -m pytest -q` (shortened: 3 of 14, 2 of 6, 1, 3 of 21)
PYTEST_OUTPUT = """.....FFFF..EE
=========================== short test summary info ============================
FAILED tests/test_leaderboard/test_leaderboard.py::TestLeaderboard::test_initialization
FAILED tests/test_leaderboard/test_leaderboard.py::TestLeaderboard::test_generate_leaderboard - AttributeError: 'FixtureFunctionDefinition' object has no attribute 'add_score'
FAILED tests/test_leaderboard/test_leaderboard.py::TestLeaderboard::test_get_top_player
FAILED tests/test_player_manager/test_player_manager.py::TestPlayerManager::test_register_player_success - assert None is not None
FAILED tests/test_player_manager/test_player_manager.py::TestPlayerManager::test_count_players
FAILED tests/test_snake_logic/test_snake_logic.py::TestGameState::test_game_state_reset - assert [(10, 10), (9, 10), (8, 10)] == [(0, 0)]
FAILED tests/test_websocket_server/test_websocket_server.py::TestHandleJoinGame::test_handle_join_game_without_data
ERROR tests/test_websocket_server/test_websocket_server.py::TestGetOrCreateGameState::test_initial_game_state_creation - fixture 'mock_game_state' not found
ERROR tests/test_websocket_server/test_websocket_server.py::TestGetOrCreateGameState::test_existing_game_state_return - fixture 'mock_game_state' not found
32 failed, 75 passed, 66 errors in 1.01s
"""
TASK_8 = "Kick-off Task 8: Run all tests and the game server to verify the complete system works end-to-end"


# ======================================================================================== 1. what a verification run says
def test_the_failing_tests_per_file():
    from src.utils.test_runs import failing_tests
    found = failing_tests(PYTEST_OUTPUT)
    assert list(found) == ["tests/test_leaderboard/test_leaderboard.py", "tests/test_player_manager/test_player_manager.py",
                           "tests/test_snake_logic/test_snake_logic.py",
                           "tests/test_websocket_server/test_websocket_server.py"]
    assert found["tests/test_leaderboard/test_leaderboard.py"][:2] == [
        "TestLeaderboard::test_initialization",
        "TestLeaderboard::test_generate_leaderboard: AttributeError: 'FixtureFunctionDefinition' object has no attribute "
        "'add_score'"]
    assert found["tests/test_websocket_server/test_websocket_server.py"][1] == (
        "TestGetOrCreateGameState::test_initial_game_state_creation (error): fixture 'mock_game_state' not found")
    assert failing_tests("ok\n3 passed in 0.1s\n") == {} and failing_tests(None) == {}
    assert failing_tests("ERROR tests/test_a.py - ModuleNotFoundError: No module named 'flask'") == {
        "tests/test_a.py": ["(the file) (error): ModuleNotFoundError: No module named 'flask'"]}


def test_verification_only_tasks():
    from src.utils.test_failure_routing import is_verification_only_task, says_to_fix
    assert is_verification_only_task(TASK_8)
    assert is_verification_only_task("Kick-off Task 9: Run the test suite with pytest and verify the game works")
    for text in ("Kick-off Task 5: Run pytest on the whole project, fixing any failures",
                 "Fix test failures in test_snake_logic.py: test_snake_move_with_food",
                 "Run tests/test_leaderboard/test_leaderboard.py", "Implement the leaderboard in src/leaderboard.py"):
        assert not is_verification_only_task(text), text
    assert says_to_fix("Run the tests and make them pass") and not says_to_fix(TASK_8)


def test_a_test_file_remembers_who_made_it(tmp_path):
    from src.utils import file_authorship as fa
    fa.reset()
    path = tmp_path / "tests" / "test_x.py"
    fa.record_file_change(path, "W3", "b710a558")
    fa.record_file_change(path, "W2", "ef88e88a")                              # W2 rewrote it later
    assert fa.first_change(path)["agent_id"] == "W3" and fa.last_writer(path) == "W2"
    fa.forget_file(path)
    assert fa.first_change(path) is None
    fa.reset()


# ======================================================================================== 2. the run, on a real task store
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
    file_authorship.reset()
    clear_test_runs()


def _add(call, manager, text, assignee=None, **extra):
    args = {"assignee_agent_id": assignee} if assignee else {}
    result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args, **extra)
    assert result["status"] == "success", result
    return result["task_uuid"]


def _finish(call, manager, uuid):
    assert call("framework", manager, action="modify_task", task_id=uuid, task_progress="finished")["status"] == "success"


FILES = {
    "src/snake_logic.py": "def move():\n    return 1\n",
    "src/leaderboard.py": "class Leaderboard:\n    pass\n",
    "src/player_manager.py": "class PlayerManager:\n    pass\n",
    "src/websocket_server.py": "def handle():\n    return 1\n",
    "tests/test_snake_logic/test_snake_logic.py": "from snake_logic import move\n\ndef test_a():\n    assert move()\n",
    "tests/test_player_manager/test_player_manager.py": "from player_manager import PlayerManager\n\n"
                                                        "def test_a():\n    assert PlayerManager()\n",
    "tests/test_leaderboard/test_leaderboard.py": "from leaderboard import Leaderboard\n\n"
                                                  "def test_a():\n    assert Leaderboard()\n",
    "tests/test_websocket_server/test_websocket_server.py": "from websocket_server import handle\n\n"
                                                            "def test_a():\n    assert handle()\n",
}


def _the_run(call):
    """The run's tasks and files at 11:08 (W1: Tasks 1, 3, 6 and the missing tests; W3: Tasks 4, 7; W2: Tasks 2, 5, 8)."""
    from src.utils.file_authorship import record_file_change
    pm1, w1, w2, w3 = Agent("PM1", "pm", "pm_manage"), Agent("W1"), Agent("W2", state="worker_work"), Agent("W3")
    manager = _manager(pm1, w1, w2, w3)
    ws = call.workspace
    for rel, text in FILES.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text, encoding="utf-8")
    t1 = _add(call, manager, "Kick-off Task 1: Implement core game logic in src/snake_logic.py", "W1")
    t4 = _add(call, manager, "Kick-off Task 4: Implement WebSocket communication in src/websocket_server.py", "W3")
    t7 = _add(call, manager, "Kick-off Task 7: Write pytest tests for game logic, player management, and WebSocket "
                             "functionality in tests/", "W3")
    missing = _add(call, manager, "Complete missing test files: Write pytest tests for leaderboard and WebSocket server "
                                  "components in tests/test_leaderboard/ and tests/test_websocket_server/", "W1")
    t8 = _add(call, manager, TASK_8, "W2")
    for uuid in (t1, t4, t7, missing):
        _finish(call, manager, uuid)
    record_file_change(ws / "tests/test_snake_logic/test_snake_logic.py", "W3", t7)
    record_file_change(ws / "tests/test_player_manager/test_player_manager.py", "W3", t7)
    record_file_change(ws / "tests/test_leaderboard/test_leaderboard.py", "W1", missing)
    record_file_change(ws / "tests/test_websocket_server/test_websocket_server.py", "W1", missing)
    record_file_change(ws / "tests/test_websocket_server/test_websocket_server.py", "W2", t8)   # 11:21 W2's edit
    w2.current_task_id = w2.active_task_id = t8
    return manager, {"t1": t1, "t4": t4, "t7": t7, "missing": missing, "t8": t8}


def _failed_run(ws, output=PYTEST_OUTPUT):
    from src.utils.test_runs import FAILED, record_test_run
    record_test_run(ws, "python -m pytest -q", FAILED, output[-400:], output=output)


@needs_tw
def test_the_failures_go_to_the_workers_whose_tests_fail(tasks):
    from src.tools.project_management import ProjectManagementTool
    manager, ids = _the_run(tasks)
    _failed_run(tasks.workspace)
    refused = tasks("W2", manager, action="modify_task", task_id=ids["t8"], task_progress="finished")      # 12:52:14
    assert refused["status"] == "error" and refused["error_type"] == "tests_routed", refused
    routed = {item["file"]: item["assignee"] for item in refused["routed"]}
    assert routed == {"tests/test_leaderboard/test_leaderboard.py": "W1",
                      "tests/test_player_manager/test_player_manager.py": "W3",
                      "tests/test_snake_logic/test_snake_logic.py": "W3",
                      "tests/test_websocket_server/test_websocket_server.py": "W1"}
    assert refused["message"].startswith(f"Task '{ids['t8']}' was NOT marked finished: the full test run (`python -m pytest "
                                         "-q`) fails, and the failures are in test files OTHER workers made.")
    assert "This task now WAITS for those tasks" in refused["message"] and "request 'worker_wait' now" in refused["message"]
    tw = ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION)
    fix = tw.tasks.get(uuid=refused["routed"][0]["task_uuid"])
    assert fix["assignee"] == "W1" and {"test_fix", f"verifies:{ids['t8'][:8]}"} <= set(fix["tags"])
    assert fix["description"].startswith(
        "Fix the failing tests in tests/test_leaderboard/test_leaderboard.py (3 failing): "
        "TestLeaderboard::test_initialization; TestLeaderboard::test_generate_leaderboard: AttributeError: "
        "'FixtureFunctionDefinition' object has no attribute 'add_score'; TestLeaderboard::test_get_top_player. The full "
        f"test run of '{TASK_8[:70]}' fails there. Run `python -m pytest -q tests/test_leaderboard/test_leaderboard.py`")
    task8 = tw.tasks.get(uuid=ids["t8"])
    assert {d["uuid"] for d in task8["depends"]} == {item["task_uuid"] for item in refused["routed"]}
    assert task8["task_progress"] == "waiting"
    # asked again (12:53:24 PM1): no second copy - the open fix tasks are named again
    again = tasks("PM1", manager, action="modify_task", task_id=ids["t8"], task_progress="finished")
    assert again["status"] == "error"
    assert len([t for t in tw.tasks.pending() if "test_fix" in (t["tags"] or [])]) == 4


@needs_tw
def test_routing_stops_after_two_rounds_per_file(tasks):
    from src.tools.project_management import ProjectManagementTool
    manager, ids = _the_run(tasks)
    tw = ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION)
    only_snake = PYTEST_OUTPUT.split("\n")
    only_snake = "\n".join(line for line in only_snake if "test_snake_logic" in line or not line.startswith(("FAILED", "ERROR")))
    for round_number in (1, 2):
        _failed_run(tasks.workspace, only_snake)
        refused = tasks("W2", manager, action="modify_task", task_id=ids["t8"], task_progress="finished")
        assert refused["error_type"] == "tests_routed", (round_number, refused)
        for task in tw.tasks.pending():
            if "test_fix" in (task["tags"] or []):
                _finish(tasks, manager, task["uuid"])                            # "fixed" - and the run fails again
        (tasks.workspace / "src/snake_logic.py").write_text(f"def move():\n    return {round_number + 1}\n")
    _failed_run(tasks.workspace, only_snake)
    third = tasks("W2", manager, action="modify_task", task_id=ids["t8"], task_progress="finished")
    assert third["error_type"] == "tests_not_passing", third                     # the verification worker's own now


@needs_tw
def test_own_files_unknown_owners_and_fix_tasks_are_not_routed(tasks):
    manager, ids = _the_run(tasks)
    from src.utils.file_authorship import record_file_change
    ws = tasks.workspace
    # a test file the verification worker made itself, and one nobody's task names
    (ws / "tests/test_e2e.py").write_text("from snake_logic import move\n\ndef test_a():\n    assert not move()\n")
    record_file_change(ws / "tests/test_e2e.py", "W2", ids["t8"])
    (ws / "test_misc.py").write_text("from leaderboard import Leaderboard\n\ndef test_a():\n    assert not Leaderboard\n")
    _failed_run(ws, "FAILED tests/test_e2e.py::test_a\nFAILED test_misc.py::test_a\n2 failed\n")
    refused = tasks("W2", manager, action="modify_task", task_id=ids["t8"], task_progress="finished")
    assert refused["error_type"] == "tests_not_passing", refused
    # a task that says to FIX the failures keeps them
    fixer = _add(tasks, manager, "Kick-off Task 10: Run pytest on the whole project, fixing any failures", "W2")
    _failed_run(ws)
    assert tasks("W2", manager, action="modify_task", task_id=fixer, task_progress="finished")["error_type"] == \
        "tests_not_passing"
    # a run of older files is not routed either: the task has to run the suite again
    _failed_run(ws)
    (ws / "src/leaderboard.py").write_text("class Leaderboard:\n    size = 1\n")
    stale = tasks("W2", manager, action="modify_task", task_id=ids["t8"], task_progress="finished")
    assert stale["error_type"] == "tests_not_passing" and "no full test run of the CURRENT files" in stale["message"]


@needs_tw
def test_a_verification_task_does_not_change_other_workers_files(tasks):
    from src.tools.project_management import foreign_file_change_error
    manager, ids = _the_run(tasks)
    refusal = foreign_file_change_error(manager, "W2", PROJECT, SESSION, "src/websocket_server.py")      # 11:13:02
    assert refusal.startswith(f"'src/websocket_server.py' was NOT changed: it is the file of the FINISHED task "
                              f"'Kick-off Task 4: Implement WebSocket communication in src/websocket_server.py' "
                              f"({ids['t4']}) of W3.")
    # a task that says to fix the failures may (Round 3o)
    fixer = _add(tasks, manager, "Kick-off Task 10: Run pytest on the whole project, fixing any failures", "W2")
    manager.agents["W2"].current_task_id = fixer
    assert foreign_file_change_error(manager, "W2", PROJECT, SESSION, "src/websocket_server.py") is None


@needs_tw
def test_decomposed_needs_sub_tasks(tasks):
    pm1, w1 = Agent("PM1", "pm", "pm_manage"), Agent("W1", state="worker_work")
    manager = _manager(pm1, w1)
    fix = _add(tasks, manager, "Fix test failures in test_snake_logic.py: test_snake_move_with_food", "W1")
    refused = tasks("W1", manager, action="modify_task", task_id=fix, task_progress="decomposed")       # 12:12:15
    assert refused["status"] == "error" and refused["error_type"] == "no_subtasks"
    assert refused["message"].startswith(f"Task '{fix}' was NOT changed: 'decomposed' means its work moved to sub-tasks, "
                                         "and it has none.")
    assert tasks("W1", manager, action="add_task", description="Fix test_snake_move_with_food in test_snake_logic.py",
                 tags=f"parent:{fix}")["status"] == "success"
    assert tasks("W1", manager, action="modify_task", task_id=fix, task_progress="decomposed")["status"] == "success"
    other = _add(tasks, manager, "Fix the leaderboard sort in src/leaderboard.py", "W1")
    assert tasks("PM1", manager, action="modify_task", task_id=other, task_progress="decomposed")["status"] == "success"

# END OF FILE tests/unit/test_round4e.py
