# START OF FILE tests/unit/test_round5l.py
"""
Round 5l: how workers work together (run of 2026-11-19 13:22 - 14:31, WiFi_Snake_Game).

1. W1's "Implement server.py with Flask server ..." was finished at 13:35 while `python server/server.py` crashed: the run
   check only judged tasks that SAY to run the program. The task that makes the program's start file is now checked too
   (only the program and its server - and a module another worker has not written yet does not hold it up).
2. W2's verify task found four problems in W1's server/server.py one after the other (13:47-14:24); W2 may not change it,
   so each went W2 -> PM1 -> a new task -> W1 -> watchdog -> W2. The framework now gives the finding to the author of the
   file as a fix task, the verify task waits for it, and the PM is told (it relays nothing).
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Game", "startup_1795065749"
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")
needs_python = pytest.mark.skipif(shutil.which("python") is None, reason="no `python` on PATH")


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
def short_run_checks(monkeypatch):
    from src.utils import program_run_check
    monkeypatch.setattr(program_run_check, "RUN_CHECK_SECONDS", 3.0)
    monkeypatch.setattr(program_run_check, "HTTP_PROBE_DELAY_SECONDS", 1.0)


def _workspace(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Path(root)


class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait", status="idle"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, status
        self.persona = agent_id
        self.message_history = []
        self.agent_config = {"config": {"project_name_context": PROJECT}}


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
    extra = {"assignee_agent_id": assignee} if assignee else {}
    result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **extra)
    assert result["status"] == "success", result
    return result["task_uuid"]


CRASHING_SERVER = '''import http.server


def run_server():
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), http.server.SimpleHTTPRequestHandler)
    raise RuntimeError("The Werkzeug web server is not designed to run in production. Pass allow_unsafe_werkzeug=True "
                       "to the run() method to disable this error.")
    httpd.serve_forever()


if __name__ == '__main__':
    run_server()
'''
GOOD_SERVER = '''import http.server


def run_server():
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), http.server.SimpleHTTPRequestHandler)
    httpd.serve_forever()


if __name__ == '__main__':
    run_server()
'''
NEEDS_GAME_LOGIC = "from game_logic import Game\n" + GOOD_SERVER
README = "Start the game: `python server/server.py`\n"
SERVER_TASK = "Kick-off Task 2: Implement server/server.py with the HTTP server for the game"
VERIFY_TASK = "Kick-off Task 7: Run the tests and the program to verify that everything works"


# ============================================================================== 1. the start file's own task
@needs_tw
@needs_python
def test_the_task_that_makes_the_start_file_is_run_checked(tasks):
    w1 = Agent("W1", state="worker_work", status="processing")
    manager = _manager(Agent("PM1", "pm", "pm_manage"), w1)
    ws = _workspace(tasks.workspace, {"server/server.py": CRASHING_SERVER, "docs/README.md": README})
    t2 = _add(tasks, manager, SERVER_TASK, "W1")
    refused = tasks("W1", manager, action="modify_task", task_id=t2, task_progress="finished")
    assert refused["status"] == "error" and refused["error_type"] == "program_does_not_run", refused
    assert refused["message"].startswith(f"Task '{t2}' was NOT marked finished: it makes `server/server.py`, the file that "
                                         "starts the program, and the program does not start: `python server/server.py` "
                                         "stopped with an error")
    assert "allow_unsafe_werkzeug" in refused["message"]
    assert "A task that makes the program's start file is finished when the program starts (`python server/server.py`)" \
        in refused["message"]
    assert refused["run_problems"][0][0] == "server/server.py"
    _workspace(ws, {"server/server.py": GOOD_SERVER})
    assert tasks("W1", manager, action="modify_task", task_id=t2, task_progress="finished")["status"] == "success"


@needs_tw
@needs_python
def test_a_module_another_worker_has_not_written_yet_does_not_hold_the_author_up(tasks):
    from src.tools.project_management import missing_planned_module
    manager = _manager(Agent("PM1", "pm", "pm_manage"), Agent("W1", state="worker_work"), Agent("W2", state="worker_work"))
    ws = _workspace(tasks.workspace, {"server/server.py": NEEDS_GAME_LOGIC, "docs/README.md": README})
    t2 = _add(tasks, manager, SERVER_TASK, "W1")
    _add(tasks, manager, "Kick-off Task 3: Implement the snake game logic in src/game_logic.py", "W2")
    assert missing_planned_module("ModuleNotFoundError: No module named 'game_logic'", ws, PROJECT, SESSION, t2)
    assert not missing_planned_module("ModuleNotFoundError: No module named 'flask_socketio'", ws, PROJECT, SESSION, t2)
    assert tasks("W1", manager, action="modify_task", task_id=t2, task_progress="finished")["status"] == "success"


@needs_tw
def test_other_tasks_are_still_not_run_checked(tasks):
    manager = _manager(Agent("PM1", "pm", "pm_manage"), Agent("W3", state="worker_work"))
    _workspace(tasks.workspace, {"server/server.py": CRASHING_SERVER, "docs/README.md": README,
                                 "public/index.html": "<html><body>Snake</body></html>\n"})
    t5 = _add(tasks, manager, "Kick-off Task 5: Build the game page in public/index.html", "W3")
    assert tasks("W3", manager, action="modify_task", task_id=t5, task_progress="finished")["status"] == "success"


# ============================================================================== 2. a verify task's finding goes to the author
@needs_tw
@needs_python
def test_the_program_failure_of_a_verify_task_goes_to_the_files_author(tasks):
    from src.utils.test_runs import PASSED, record_test_run
    pm1 = Agent("PM1", "pm", "pm_standby")
    w1, w2 = Agent("W1", state="worker_wait"), Agent("W2", state="worker_work", status="processing")
    manager = _manager(pm1, w1, w2)
    ws = _workspace(tasks.workspace, {"server/server.py": GOOD_SERVER, "docs/README.md": README,
                                      "tests/test_server.py": "def test_ok():\n    assert True\n"})
    t2 = _add(tasks, manager, SERVER_TASK, "W1")
    assert tasks("W1", manager, action="modify_task", task_id=t2, task_progress="finished")["status"] == "success"
    _workspace(ws, {"server/server.py": CRASHING_SERVER})  # a later change broke the start
    t7 = _add(tasks, manager, VERIFY_TASK, "W2")
    record_test_run(ws, "python -m pytest -q", PASSED, "1 passed")
    refused = tasks("W2", manager, action="modify_task", task_id=t7, task_progress="finished")
    assert refused["status"] == "error" and refused["error_type"] == "run_routed", refused
    assert refused["message"].startswith(f"Task '{t7}' was NOT marked finished: the program does not start")
    assert "The framework gave each problem to the author of its file:\n- server/server.py -> W1 (task " in refused["message"]
    assert "This task now WAITS for those tasks" in refused["message"]
    assert "do not ask your PM to relay the problem" in refused["message"]
    fix_uuid = refused["routed"][0]["task_uuid"]
    listed = tasks("framework", manager, action="list_tasks", project_filter=PROJECT)
    fix = next(t for t in listed["tasks"] if t["uuid"] == fix_uuid)
    assert fix["description"].startswith("Fix server/server.py: the program check of 'Kick-off Task 7: Run the tests and "
                                         "the program to verify that everything works' failed because of this file:")
    assert "allow_unsafe_werkzeug" in fix["description"] and fix["assignee"] == "W1"
    verify = next(t for t in listed["tasks"] if t["uuid"] == t7)
    assert verify.get("task_progress") == "waiting", verify
    assert any("the framework gave each problem to the author of its file: server/server.py -> W1" in m["content"]
               for m in pm1.message_history)
    # asked again while the fix is open: the same fix task, no second one
    again = tasks("W2", manager, action="modify_task", task_id=t7, task_progress="finished")
    assert again["status"] == "error", again
    listed = tasks("framework", manager, action="list_tasks", project_filter=PROJECT)
    assert [t["uuid"] for t in listed["tasks"] if t["description"].startswith("Fix server/server.py")] == [fix_uuid]


@needs_tw
@needs_python
def test_the_verify_workers_own_file_stays_its_own(tasks):
    from src.utils.test_runs import PASSED, record_test_run
    manager = _manager(Agent("PM1", "pm", "pm_standby"), Agent("W2", state="worker_work", status="processing"))
    ws = _workspace(tasks.workspace, {"server/server.py": CRASHING_SERVER, "docs/README.md": README,
                                      "tests/test_server.py": "def test_ok():\n    assert True\n"})
    t7 = _add(tasks, manager, "Kick-off Task 7: Write server/server.py, then run the program to verify that it works", "W2")
    record_test_run(ws, "python -m pytest -q", PASSED, "1 passed")
    refused = tasks("W2", manager, action="modify_task", task_id=t7, task_progress="finished")
    assert refused["error_type"] == "program_does_not_run" and "routed" not in refused


def test_the_fix_text():
    from src.utils.test_failure_routing import run_fix_text
    text = run_fix_text("server/server.py", ["the program does not start: `python server/server.py` stopped"],
                        VERIFY_TASK, "python server/server.py")
    assert text.startswith("Fix server/server.py: the program check of 'Kick-off Task 7: Run the tests and the program to "
                           "verify that everything works' failed because of this file:\n- the program does not start")
    assert "Run `python server/server.py` with command_executor" in text

# END OF FILE tests/unit/test_round5l.py
