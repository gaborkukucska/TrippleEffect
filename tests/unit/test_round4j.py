# START OF FILE tests/unit/test_round4j.py
"""
Round 4j: the run of 2026-10-30 11:46-15:49 (qwen3.5:4b-q4_K_M, WiFi_Snake_Multiplayer: Python / Flask).

* 11:55:14 W1 marked "Kick-off Task 2: Implement snake movement, collision detection, food spawning, and turn-based gameplay
  in src/game_logic.py" finished 33 seconds after its Task 1 without writing src/game_logic.py: the completion check looked
  only at files that EXIST. The project never got its game logic; W2 and PM1 looked for the file five times later.
* The structure put the README into docs/README/README.md although Task 8 said "Write README.md ...": W3 wrote it there.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Multiplayer", "startup_1793332012"
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")

TASK_1 = ("Kick-off Task 1: Implement Flask API endpoints for player registration, authentication, and score management in "
          "src/api.py")
TASK_2 = ("Kick-off Task 2: Implement snake movement, collision detection, food spawning, and turn-based gameplay in "
          "src/game_logic.py")
TASK_6 = ("Kick-off Task 6: Create leaderboard functionality that ranks players by score and displays top players in "
          "src/leaderboard.py")
TASK_8 = "Kick-off Task 8: Write README.md with installation instructions, game rules, and hosting setup"


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def _write(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


# ======================================================================================== 1. the file a task makes
def test_the_files_a_task_makes_and_that_are_missing(tmp_path):
    from src.utils.task_files import missing_task_files
    _write(tmp_path, {"src/api.py": "app = None\n"})
    assert missing_task_files(TASK_2, tmp_path, [TASK_1, TASK_6, TASK_8]) == ["src/game_logic.py"]
    assert missing_task_files(TASK_1, tmp_path, [TASK_2]) == []
    assert missing_task_files(TASK_8, tmp_path, [TASK_1]) == ["README.md"]
    _write(tmp_path, {"docs/README/README.md": "# Snake\n"})
    assert missing_task_files(TASK_8, tmp_path, [TASK_1]) == []          # a README somewhere counts
    # a file another task names is not this task's to make; a task that makes nothing is not judged
    assert missing_task_files("Kick-off Task 7: Implement the score API in src/scores.py using src/game_logic.py",
                              tmp_path, [TASK_2]) == ["src/scores.py"]
    assert missing_task_files("Run the tests in tests/test_api.py and report the result", tmp_path, []) == []
    assert missing_task_files(TASK_2 + " [Framework note: tests in tests/test_x.py]", tmp_path, []) == ["src/game_logic.py"]
    assert missing_task_files(TASK_2, None, []) == []


class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_work"):
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
    result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert result["status"] == "success", result
    return result["task_uuid"]


@needs_tw
def test_a_task_whose_file_was_never_written_is_not_finished(tasks):
    pm1, w1 = Agent("PM1", "pm", "pm_report_check"), Agent("W1")
    manager = _manager(pm1, w1)
    _write(tasks.workspace, {"src/api.py": "from flask import Flask\napp = Flask(__name__)\n"})
    task1 = _add(tasks, manager, TASK_1, "W1")
    task2 = _add(tasks, manager, TASK_2, "W1")
    _add(tasks, manager, TASK_6, "W1")
    assert tasks("W1", manager, action="modify_task", task_id=task1, task_progress="finished")["status"] == "success"
    refused = tasks("W1", manager, action="modify_task", task_id=task2, task_progress="finished")             # 11:55:14
    assert refused["status"] == "error"
    assert refused["message"].startswith(f"Task '{task2}' was NOT marked finished: its files still have these defects:\n"
                                         "- src/game_logic.py does NOT exist: the task says to make it. Write it with "
                                         "file_system 'write' at exactly that path (the whole content the task asks for), "
                                         "then mark the task finished.")
    assert tasks("PM1", manager, action="modify_task", task_id=task2, task_progress="finished")["status"] == "error"
    _write(tasks.workspace, {"src/game_logic.py": "def move_snake(snake, direction):\n    return snake\n"})
    assert tasks("W1", manager, action="modify_task", task_id=task2, task_progress="finished")["status"] == "success"
    # the framework is not judged
    task8 = _add(tasks, manager, TASK_8, "W1")
    assert tasks("framework", manager, action="modify_task", task_id=task8, task_progress="finished")["status"] == "success"


# ======================================================================================== 2. the README of the kick-off
def test_a_readme_in_a_folder_of_its_own_name_is_the_root_readme(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    import src.workflows.pm_kickoff_workflow as pkw
    from src.config.settings import settings
    from src.workflows.pm_kickoff_workflow import root_docs_over_doubled_folders
    assert root_docs_over_doubled_folders(["docs/README/README.md", "src/api.py"], ["README.md"])[0] == [
        "README.md", "src/api.py"]
    assert root_docs_over_doubled_folders(["docs/README.md"], ["README.md"])[0] == ["docs/README.md"]
    assert root_docs_over_doubled_folders(["src/server/server.py"], ["server.py"])[0] == ["src/server/server.py"]
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    monkeypatch.setattr(pmt, "TASKLIB_AVAILABLE", False)
    ws = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    ws.mkdir(parents=True)

    class Executor:
        calls = []

        async def execute_tool(self, **kwargs):
            self.calls.append(kwargs["tool_args"])
            n = len(self.calls)
            return {"status": "success", "task_id": n, "task_uuid": f"00000000-0000-0000-0000-{n:012d}"}

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)

    class PM:
        agent_id, sandbox_path, message_history = "PM1", tmp_path, []
        agent_config = {"config": {"project_name_context": PROJECT}}

        def clear_history(self):
            self.message_history = []
    plan = {"roles": ["Coder"],
            "tasks": [{"id": "task_1", "description": TASK_1.split(": ", 1)[1]},
                      {"id": "task_8", "depends_on": ["task_1"], "description": TASK_8.split(": ", 1)[1]}],
            "project_structure": [{"dir": "src", "children": [{"dir": "api", "files": ["api.py"]}]},
                                  {"dir": "docs", "children": [{"dir": "README", "files": ["README.md"]}]}]}
    manager = types.SimpleNamespace(current_project=PROJECT, current_session=SESSION, tool_executor=Executor())
    with quiet():
        assert run(pkw.PMKickoffWorkflow().execute(manager, PM(), plan)).success
    md = (ws / "PROJECT_STRUCTURE.md").read_text(encoding="utf-8")
    files_to_create = md.split("## Files To Create", 1)[1]
    assert "- README.md" in files_to_create and "docs/README" not in md
    assert not (ws / "docs" / "README").exists()

# END OF FILE tests/unit/test_round4j.py
