# START OF FILE tests/unit/test_round4n.py
"""
Round 4n: a sub-task never waits for its own parent, and sibling sub-tasks do not wait for each other.

The run of 2026-10-31 (WiFi_Multiplayer_Snake_Game, startup_1793408847): at 09:30:52 W3, in 'worker_decompose' on
Kick-off Task 7 (1fce9a96, tests), added five sub-tasks, each with depends=<Task 7>. Two things followed:
  - every sub-task waited for Task 7, which is finished only through its sub-tasks, so none could ever start
    (09:31:23 W3's 'worker_work' on its first sub-task was refused: "it waits for unfinished task(s)");
  - the decompose dependency transfer gave each new sub-task to every pending task that depended on Task 7 - which
    included the sub-tasks already made - so the first sub-task also waited for all four later ones.
W2's two sub-tasks stayed deferred and Task 9 (run and verify) never started.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Multiplayer_Snake_Game", "startup_1793408847"
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")

TASK_7 = ("Kick-off Task 7: Write comprehensive automated tests for game logic, networking, player management, scoring, "
          "and leaderboard in tests/")
TASK_9 = "Kick-off Task 9: Run all tests and verify the game works correctly on multiple devices connected to the same WiFi"
SUB_TASKS = (  # 09:30:52, in the order W3 made them
    ("Write unit tests for SnakeGame class in tests/game/test.js - test movement, collision detection, food spawning, "
     "and game loop", "W3"),
    ("Write unit tests for Player and PlayerManager classes in tests/player/test.js - test player registration, "
     "validation, and stats tracking", "W2"),
    ("Write unit tests for ScoreManager class in tests/score/test.js - test score calculation, persistence, and data "
     "retrieval", "W2"),
    ("Write unit tests for Leaderboard class in tests/leaderboard/test.js - test ranking, sorting, and leaderboard data "
     "retrieval", "W3"),
    ("Write integration tests for NetworkManager class in tests/network/test.js - test Socket.io connections, event "
     "handling, and peer communication", "W3"),
)


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


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
    yield call
    file_authorship.reset()
    clear_test_runs()


def _add(call, manager, text, assignee=None, caller="framework", **extra):
    args = {"assignee_agent_id": assignee} if assignee else {}
    result = call(caller, manager, action="add_task", description=text, project_filter=PROJECT, **args, **extra)
    assert result["status"] == "success", result
    return result


def _depends(call, manager):
    """uuid -> set of the uuids it depends on, for every pending task."""
    listed = call("framework", manager, action="list_tasks", project_filter=PROJECT)
    assert listed["status"] == "success", listed
    return {t["uuid"]: set(t.get("depends") or []) for t in listed["tasks"]}


def _the_run(call):
    """The task store at 09:30:51: Task 7 is W3's (being decomposed), Task 9 waits for it."""
    pm1, w1, w2, w3 = Agent("PM1", "pm", "pm_manage"), Agent("W1"), Agent("W2"), Agent("W3", state="worker_decompose")
    manager = _manager(pm1, w1, w2, w3)
    t7 = _add(call, manager, TASK_7, "W3")["task_uuid"]
    t9 = _add(call, manager, TASK_9, "W1", depends=t7)["task_uuid"]
    w3.current_task_id = w3.active_task_id = t7
    return manager, t7, t9


@needs_tw
def test_a_sub_task_does_not_wait_for_its_parent_nor_for_its_siblings(tasks):
    manager, t7, t9 = _the_run(tasks)
    subs = []
    for text, assignee in SUB_TASKS:
        result = _add(tasks, manager, text, assignee, caller="W3", depends=t7)
        assert t7 not in result["depends"], result
        assert "was NOT added" in result["message"] and t7 in result["message"], result["message"]
        subs.append(result["task_uuid"])

    deps = _depends(tasks, manager)
    for uuid in subs:
        assert deps[uuid] == set(), (uuid, deps[uuid])   # neither Task 7 nor a sibling
    # the transfer still makes the verification task wait for every sub-task (and for Task 7 itself)
    assert deps[t9] == {t7, *subs}

    # nothing blocks the sub-tasks now
    from src.utils.task_utils import unfinished_dependencies
    import src.tools.project_management as pmt
    tw = pmt.ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION)
    for uuid in subs:
        assert unfinished_dependencies(tw.tasks.get(uuid=uuid)) == []


@needs_tw
def test_a_parent_tag_alone_also_drops_that_dependency(tasks):
    manager, t7, _t9 = _the_run(tasks)
    w1 = manager.agents["W1"]
    w1.state = "worker_work"                       # not decomposing: only its tag names the parent
    result = _add(tasks, manager, "Fix the leaderboard test in tests/leaderboard/test.js", "W1", caller="W1",
                  depends=t7, tags=f"parent:{t7}")
    assert t7 not in result["depends"] and "was NOT added" in result["message"]


@needs_tw
def test_other_dependencies_of_a_sub_task_are_kept(tasks):
    manager, t7, _t9 = _the_run(tasks)
    other = _add(tasks, manager, "Kick-off Task 4: Implement score calculation in src/score.js", "W1")["task_uuid"]
    result = _add(tasks, manager, SUB_TASKS[2][0], "W2", caller="W3", depends=f"{t7},{other}")
    assert result["depends"] == [other], result
    plain = _add(tasks, manager, SUB_TASKS[3][0], "W3", caller="W3", depends=other)
    assert plain["depends"] == [other] and "was NOT added" not in plain["message"]


@needs_tw
def test_the_dependency_transfer_skips_the_parents_own_sub_tasks(tasks):
    manager, t7, t9 = _the_run(tasks)
    first = _add(tasks, manager, SUB_TASKS[0][0], "W3", caller="W3")["task_uuid"]
    # a sub-task that already waits for its parent (made before this round, or changed by modify_task)
    assert tasks("framework", manager, action="modify_task", task_id=first, depends=t7)["status"] == "success"
    second = _add(tasks, manager, SUB_TASKS[1][0], "W2", caller="W3")["task_uuid"]
    deps = _depends(tasks, manager)
    assert second not in deps[first]                 # no chain between siblings
    assert {first, second} <= deps[t9]               # the real dependent still gets both


@needs_tw
def test_outside_a_decomposition_a_dependency_on_any_task_is_kept(tasks):
    manager, t7, _t9 = _the_run(tasks)
    manager.agents["W3"].state = "worker_work"
    result = _add(tasks, manager, "Write a README.md for the tests in tests/README.md", "W1", caller="W3", depends=t7)
    assert result["depends"] == [t7] and "was NOT added" not in result["message"]

# END OF FILE tests/unit/test_round4n.py
