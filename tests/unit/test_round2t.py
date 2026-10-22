# START OF FILE tests/unit/test_round2t.py
"""
Round 2t: fixtures from the 2026-10-21 20:45-21:03 run (WiFi_Snake_Game again; the user chose 'continue').

* 20:49 - 'continue' carried 0 files: the latest session of the project (startup_1792572946, a run that never got past its
  kickoff) had no work files, while the sessions before it held the project's code.
* 20:52:43 - W1's first src/app.py was rejected (a 'try' without 'except' at line 165), so src/app.py was never created. W1
  then "edited" it (code_editor, 20:54:34 / 20:56:54 / 20:58:22: "File 'src/app.py' does not exist yet") and wrote it in
  pieces as src/app.py.part1, src/app.py.part2, src/app.py.part1.txt, src/app.py.part2.txt (6 writes, 6 deletes).
* 20:54:22 - PM1 assigned "Kick-off Task 9: Write docs/SETUP.md" to ITSELF.
"""
import asyncio
import json
import os
import re
import shutil
import types
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")

# src/app.py lines 160-175 of W1's rejected write (20:52:43): the try on line 165 has no except
APP_WITH_OPEN_TRY = (
    "from flask_socketio import SocketIO\n"
    "socketio = SocketIO()\n"
    "\n"
    "@socketio.on('register_player')\n"
    "def handle_register(data):\n"
    "    try:\n"
    "        player_data = {'name': data['name']}\n"
    "        socketio.emit('player_registered', {'data': player_data}, room='all')\n"
    "\n"
    "\n"
    "@socketio.on('movement_update')\n"
    "def handle_movement(data):\n"
    "    return data\n")
APP_PART_1 = (
    '"""\nWiFi Snake Game - Flask Application Entry Point\n"""\n\n'
    "import os\n\nDATABASE_PATH = 'data/scores.db'\n\n\n"
    "def get_db_connection():\n    import sqlite3\n    return sqlite3.connect(DATABASE_PATH)\n")
APP_PART_2 = "\n\ndef init_database():\n    os.makedirs('data', exist_ok=True)\n    return get_db_connection()\n"


def run(coro):
    return asyncio.run(coro)


# ======================================================================================== 1. 'continue' carries real work
def _touch(path: Path, text: str, mtime: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))


@pytest.fixture
def snake_projects(tmp_path):
    """WiFi_Snake_Game as it was at 20:47: three sessions with code, then startup_1792572946 with only its kickoff."""
    base = tmp_path / "projects"
    project = base / "WiFi_Snake_Game"
    for number, (session, files) in enumerate([
            ("startup_1792509061", {"src/app.py": "app = 1\n"}),
            ("startup_1792568609", {"src/app.py": "app = 2\n", "src/game_logic.py": "class Game: pass\n"}),
            ("startup_1792570417", {"src/app.py": "app = 3\n", "src/game_logic.py": "class Game: pass\n",
                                    "public/index.html": "<html></html>\n", "tests/test_app.py": "def test(): pass\n"})]):
        mtime = 1_792_500_000 + number * 10_000
        for rel, text in files.items():
            _touch(project / session / "shared_workspace" / rel, text, mtime)
        for rel in ("PROJECT_PLAN.md", "PROJECT_STRUCTURE.md", "conftest.py"):
            _touch(project / session / "shared_workspace" / rel, f"{rel} of {session}\n", mtime)
        _touch(project / session / "agent_session_data.json", "{}", mtime)
    empty = project / "startup_1792572946"
    _touch(empty / "shared_workspace" / "PROJECT_PLAN.md", "# plan of the kickoff-only run\n", 1_792_572_946)
    _touch(empty / "shared_workspace" / "PROJECT_STRUCTURE.md", "structure\n", 1_792_572_946)
    _touch(empty / "task_data" / "pending.data", "", 1_792_572_946)
    return base


def test_the_latest_session_with_work_files_is_carried(snake_projects):
    from src.utils.project_folders import carried_files_text, existing_project_info
    info = existing_project_info(snake_projects, "WiFi_Snake_Game", "startup_1792586749")
    assert info["latest_session"] == "startup_1792572946"                          # what 20:49 carried over (0 files)
    assert info["carry_session"] == "startup_1792570417"
    assert info["carry_work_files"] == ["public/index.html", "src/app.py", "src/game_logic.py", "tests/test_app.py"]
    assert info["carry_work_file_count"] == 4
    assert [s["work_files"] for s in info["sessions"]] == [1, 2, 4, 0]
    assert [s["files"] for s in info["sessions"]] == [4, 5, 7, 2]                   # unchanged meaning: all files
    assert carried_files_text(info) == ("the 4 work file(s) of its session 'startup_1792570417' are carried over; its "
                                        "latest session 'startup_1792572946' has no work files")


def test_the_carry_over_takes_the_session_with_work_files(snake_projects):
    from src.utils.project_folders import carry_over_note, carry_over_workspace
    target = snake_projects / "WiFi_Snake_Game" / "startup_1792586749" / "shared_workspace"
    target.mkdir(parents=True)
    (target / "PROJECT_PLAN.md").write_text("# new plan\n")
    session, copied = carry_over_workspace(snake_projects, "WiFi_Snake_Game", None, "startup_1792586749")
    assert session == "startup_1792570417"
    assert sorted(copied) == ["PREVIOUS_PROJECT_PLAN.md", "public/index.html", "src/app.py", "src/game_logic.py",
                              "tests/test_app.py"]
    assert (target / "src" / "app.py").read_text() == "app = 3\n"
    assert (target / "PROJECT_PLAN.md").read_text() == "# new plan\n"
    note = carry_over_note("WiFi_Snake_Game", session, copied)
    assert "The files of its session 'startup_1792570417' were copied into your workspace: " in note


def test_without_work_files_the_latest_session_is_kept(tmp_path):
    from src.utils.project_folders import carried_files_text, carry_over_workspace, existing_project_info
    base = tmp_path / "projects"
    _touch(base / "Temp_CLI" / "startup_1" / "shared_workspace" / "PROJECT_PLAN.md", "# plan 1\n", 1_000)
    _touch(base / "Temp_CLI" / "startup_2" / "shared_workspace" / "PROJECT_PLAN.md", "# plan 2\n", 2_000)
    info = existing_project_info(base, "Temp_CLI", "startup_3")
    assert info["carry_session"] == "startup_2" == info["latest_session"]
    assert info["carry_work_files"] == [] and info["carry_work_file_count"] == 0
    assert carried_files_text(info) == "no earlier session has work files, so only its earlier plan is carried over"
    assert carry_over_workspace(base, "Temp_CLI", None, "startup_3") == ("startup_2", ["PREVIOUS_PROJECT_PLAN.md"])


def test_the_carried_files_text_of_older_records():
    from src.utils.project_folders import carried_files_text
    assert carried_files_text({"latest_session": "s1"}) == "its latest files are carried over"     # no Round 2t keys
    assert carried_files_text(None) == "its latest files are carried over"
    assert carried_files_text({"latest_session": "s1", "carry_session": "s1", "carry_work_file_count": 2}) == (
        "the 2 work file(s) of its session 's1' are carried over")


def test_many_work_files_are_shortened_on_the_card(tmp_path):
    from src.utils.project_folders import MAX_SHOWN_WORK_FILES, existing_project_info
    base = tmp_path / "projects"
    for index in range(12):
        _touch(base / "Big" / "startup_1" / "shared_workspace" / "src" / f"m{index:02d}.py", "x = 1\n", 1_000)
    info = existing_project_info(base, "Big", "startup_2")
    assert len(info["carry_work_files"]) == MAX_SHOWN_WORK_FILES == 8 and info["carry_work_file_count"] == 12


def _approval_manager(tmp_path, monkeypatch, existing):
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    card = {"type": "project_pending_approval", "project_title": "WiFi_Snake_Game", "pm_agent_id": "PM1",
            "plan_content": "Build it.", "existing_project": existing}
    pm = types.SimpleNamespace(
        agent_id="PM1", status="idle", state="pm_startup", _awaiting_project_approval=True, _existing_project_info=existing,
        _approval_card=card, message_history=[], persona="Project Manager (WiFi_Snake_Game)",
        agent_config={"config": {"project_name_context": "WiFi_Snake_Game", "session_name": "startup_1792586749",
                                 "persona": "Project Manager (WiFi_Snake_Game)", "initial_plan_description": "Build it."}})
    admin = types.SimpleNamespace(agent_id="admin_ai", message_history=[])
    ui, scheduled = [], []

    async def set_context(project, session, loading=False):
        return None

    async def send_to_ui(payload):
        ui.append(payload)

    async def schedule_cycle(agent, retry_count=0):
        scheduled.append(agent.agent_id)
    manager = types.SimpleNamespace(agents={"PM1": pm, "admin_ai": admin}, current_session="startup_1792586749",
                                    set_project_session_context=set_context, send_to_ui=send_to_ui, schedule_cycle=schedule_cycle)
    return manager, pm, scheduled


def test_approving_continue_carries_the_work(tmp_path, monkeypatch, snake_projects):
    from src.api.http_routes import approve_project_start
    from src.utils.project_folders import existing_project_info
    existing = existing_project_info(snake_projects, "WiFi_Snake_Game", "startup_1792586749")
    manager, pm, scheduled = _approval_manager(tmp_path, monkeypatch, existing)

    async def go():
        await approve_project_start("PM1", "continue", manager)
        await asyncio.sleep(0)
    run(go())
    workspace = snake_projects / "WiFi_Snake_Game" / "startup_1792586749" / "shared_workspace"
    assert (workspace / "src" / "app.py").read_text() == "app = 3\n" and (workspace / "tests" / "test_app.py").exists()
    assert (workspace / "PROJECT_PLAN.md").read_text().startswith("# Project Plan: WiFi_Snake_Game")
    assert (workspace / "PREVIOUS_PROJECT_PLAN.md").read_text() == "PROJECT_PLAN.md of startup_1792570417\n"
    assert scheduled == ["PM1"]
    assert "The files of its session 'startup_1792570417' were copied" in pm.message_history[-1]["content"]


def test_an_older_record_without_carry_session_still_continues(tmp_path, monkeypatch, snake_projects):
    from src.api.http_routes import approve_project_start
    existing = {"name": "WiFi_Snake_Game", "latest_session": "startup_1792568609", "suggested_new_name": "WiFi_Snake_Game_2",
                "sessions": [{"name": "startup_1792568609", "files": 5, "last_changed": "2026-10-21T15:45"}]}
    manager, pm, scheduled = _approval_manager(tmp_path, monkeypatch, existing)

    async def go():
        await approve_project_start("PM1", "continue", manager)
        await asyncio.sleep(0)
    run(go())
    workspace = snake_projects / "WiFi_Snake_Game" / "startup_1792586749" / "shared_workspace"
    assert (workspace / "src" / "app.py").read_text() == "app = 2\n"            # the session the record named


def test_the_creation_workflow_says_what_continue_carries(tmp_path, monkeypatch, snake_projects):
    from src.config.settings import settings
    from src.workflows.project_creation_workflow import ProjectCreationWorkflow
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", snake_projects)
    admin = types.SimpleNamespace(agent_id="admin_ai", agent_type="admin", state="planning", message_history=[])

    async def create_agent_instance(agent_id_requested, **kwargs):
        pm = types.SimpleNamespace(agent_id=agent_id_requested, status="idle", state=None, message_history=[],
                                   sandbox_path=tmp_path / "sandbox", **kwargs)
        manager.agents[agent_id_requested] = pm
        return True, "created", agent_id_requested

    async def execute_tool(**kwargs):
        return {"status": "success", "task_id": 1, "task_uuid": "a90386ed-79be-4de7-9dcb-b0c6928088e1"}
    manager = types.SimpleNamespace(
        agents={"admin_ai": admin}, current_project="DefaultProject", current_session="startup_1792586749",
        current_session_db_id=None, create_agent_instance=create_agent_instance,
        workflow_manager=types.SimpleNamespace(change_state=lambda agent, state: setattr(agent, "state", state)),
        tool_executor=types.SimpleNamespace(execute_tool=execute_tool))
    result = run(ProjectCreationWorkflow().execute(manager, admin, {"title": "WiFi Snake Game",
                                                                     "_raw_plan_body_": "**Objective:** a WiFi snake game"}))
    carried = ("the 4 work file(s) of its session 'startup_1792570417' are carried over; its latest session "
               "'startup_1792572946' has no work files")
    assert f"CONTINUE that project ({carried})" in result.message
    assert f"CONTINUE it ({carried})" in admin.message_history[-1]["content"]
    assert result.ui_message_data["existing_project"]["carry_session"] == "startup_1792570417"


def test_the_card_shows_what_continue_carries():
    handlers = (REPO / "static" / "js" / "handlers.js").read_text()
    assert "existingProject.carry_session" in handlers and "existingProject.carry_work_file_count" in handlers
    assert "Continue carries over the ${escapeHTML(String(carryCount))} work file(s) of session" in handlers
    assert "No earlier session has work files: Continue carries over only the earlier plan." in handlers


def test_the_page_scripts_got_a_new_version():
    html = (REPO / "templates" / "index.html").read_text()
    versions = set(re.findall(r'/static/js/[A-Za-z]+\.js\?v=(\d+)', html))
    assert len(versions) == 1 and int(versions.pop()) >= 5                        # handlers.js changed in Round 2t


@pytest.mark.skipif(shutil.which("node") is None and not Path("/opt/node22/bin/node").exists(), reason="node is not installed")
def test_handlers_js_is_a_valid_module(tmp_path):
    import subprocess
    node = shutil.which("node") or "/opt/node22/bin/node"
    module = tmp_path / "handlers.mjs"
    module.write_text((REPO / "static" / "js" / "handlers.js").read_text())
    checked = subprocess.run([node, "--check", str(module)], capture_output=True, text=True, timeout=60)
    assert checked.returncode == 0, checked.stderr


# ======================================================================================== 2. tasks go to workers only
@pytest.mark.parametrize("agent_id, role", [
    ("PM1", "a Project Manager"), ("pm2", "a Project Manager"), ("PM_WiFi_Snake_Game", "a Project Manager"),
    ("pm", "a Project Manager"), ("admin_ai", "the Admin AI"), ("constitutional_guardian_ai", "the Constitutional Guardian"),
    ("W1", None), ("worker_3", None), ("pmx", None), ("", None), (None, None),
])
def test_non_worker_roles_by_name(agent_id, role):
    from src.tools.project_management import non_worker_role
    assert non_worker_role(agent_id) == role


def _agents(**types_by_id):
    return {agent_id: types.SimpleNamespace(agent_id=agent_id, agent_type=agent_type) for agent_id, agent_type in types_by_id.items()}


def test_known_agents_are_judged_by_their_type():
    from src.tools.project_management import non_worker_role, team_worker_ids
    manager = types.SimpleNamespace(agents=_agents(PM1="pm", W1="worker", W2="worker", admin_ai="admin", Lead="pm",
                                                   PM_helper="worker"), state_manager=None)
    assert non_worker_role("Lead", manager) == "a Project Manager"
    assert non_worker_role("PM_helper", manager) is None                            # a worker, whatever its name
    assert non_worker_role("admin_ai", manager) == "the Admin AI"
    assert team_worker_ids(manager, "PM1") == ["W1", "W2", "PM_helper"]
    teams = types.SimpleNamespace(get_agent_team=lambda agent_id: "team_WiFi_Snake_Game",
                                  get_agents_in_team=lambda team: [manager.agents["PM1"], manager.agents["W2"]])
    assert team_worker_ids(types.SimpleNamespace(agents=manager.agents, state_manager=teams), "PM1") == ["W2"]


def test_the_assignment_directive_names_the_workers():
    from src.agents.cycle_handler import worker_choice_text
    manager = types.SimpleNamespace(agents=_agents(PM1="pm", W1="worker", W2="worker", W3="worker"), state_manager=None)
    assert worker_choice_text(manager, "PM1") == "one of your workers (W1, W2, W3; never to yourself)"
    assert worker_choice_text(types.SimpleNamespace(agents={}, state_manager=None), "PM1") == "one of your workers (never to yourself)"
    source = (REPO / "src" / "agents" / "cycle_handler.py").read_text()
    assert "from this list to a suitable agent" not in source                       # both PM directives name workers


def test_the_activation_prompt_says_workers_only():
    import yaml
    prompt = yaml.safe_load((REPO / "prompts.yaml").read_text())["pm_activate_workers_prompt"]
    assert "Assign tasks ONLY to WORKER agents - never to yourself, the Admin AI or the Guardian" in prompt


@pytest.fixture
def tw_env(tmp_path, monkeypatch):
    pytest.importorskip("tasklib")
    import src.tools.project_management as pm_module
    from src.config.settings import settings
    monkeypatch.setattr(pm_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pm_module, "broadcast", no_broadcast)
    manager = types.SimpleNamespace(agents=_agents(PM1="pm", W1="worker", W2="worker", W3="worker", admin_ai="admin"),
                                    state_manager=None)

    def call(agent, **kw):
        return run(pm_module.ProjectManagementTool().execute(agent_id=agent, agent_sandbox_path=tmp_path, project_name="P",
                                                             session_name="S", manager=manager, **kw))
    return call


@needs_tw
def test_pm1_cannot_assign_a_task_to_itself(tw_env):
    call = tw_env
    kickoff = call("framework", action="add_task", description="PROJECT KICK-OFF: P", assignee_agent_id="PM1",
                   tags=["project_kickoff"])
    assert kickoff["status"] == "success"                                          # the framework's kickoff task
    docs = call("PM1", action="add_task", description="Kick-off Task 9: Write docs/SETUP.md")
    assert docs["status"] == "success"
    refused = call("PM1", action="modify_task", task_id=docs["task_uuid"], assignee_agent_id="PM1")
    assert refused["status"] == "error" and refused["error_type"] == "non_worker_assignee"
    assert refused["message"] == (
        f"Task '{docs['task_uuid']}' was NOT changed: 'PM1' is a Project Manager, and only workers carry out tasks - they "
        "write the files, which Project Managers, the Admin AI and the Guardian cannot do. Assign it to one of your "
        "workers: W1, W2, W3.")
    for other in ("admin_ai", "constitutional_guardian_ai"):
        assert call("PM1", action="modify_task", task_id=docs["task_uuid"], assignee_agent_id=other)["status"] == "error"
    listed = call("PM1", action="list_tasks")
    assert "PM1" not in json.dumps([t for t in listed.get("tasks", []) if t.get("uuid") == docs["task_uuid"]])
    assigned = call("PM1", action="modify_task", task_id=docs["task_uuid"], assignee_agent_id="W2")
    assert assigned["status"] == "success" and assigned["assignee"] == "W2"
    # the kickoff task stays PM1's: changing its progress (the assignee repeated) is not a new assignment
    same = call("PM1", action="modify_task", task_id=kickoff["task_uuid"], assignee_agent_id="PM1", task_progress="decomposed")
    assert same["status"] == "success"


@needs_tw
def test_a_new_task_for_a_pm_is_not_created(tw_env):
    call = tw_env
    refused = call("PM1", action="add_task", description="Write docs/SETUP.md", assignee_agent_id="PM1")
    assert refused["status"] == "error" and refused["message"].startswith(
        "The task was NOT created: 'PM1' is a Project Manager, and only workers carry out tasks")
    assert call("PM1", action="list_tasks").get("tasks", []) == []
    assert call("PM1", action="add_task", description="Write docs/SETUP.md", assignee_agent_id="W3")["status"] == "success"


# ======================================================================================== 3. creating a new file
@pytest.mark.parametrize("name, real", [
    ("src/app.py.part1", "src/app.py"), ("src/app.py.part2", "src/app.py"), ("src/app.py.part1.txt", "src/app.py"),
    ("src/app.py.part_2", "src/app.py"), ("app.js.chunk3", "app.js"), ("public/index.html.PART1", "public/index.html"),
    ("src/app.py", None), ("notes.part1.md", None), ("chapter_part1.md", None), ("src/part1.py", None),
    ("data/part.csv", None), ("", None),
])
def test_part_file_names(name, real):
    from src.tools.file_system import part_file_target
    assert part_file_target(name) == real


def _fs(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    return run(FileSystemTool().execute(agent_id="W1", agent_sandbox_path=tmp_path, scope="private", **kwargs))


def test_part_files_are_refused(tmp_path):
    result = _fs(tmp_path, action="write", filename="src/app.py.part1", content=APP_PART_1)
    assert result["status"] == "error" and result["error_type"] == "part_file"
    assert result["message"] == (
        "Refused: 'src/app.py.part1' would be a PART of 'src/app.py'. Nothing ever joins part files, so nothing was "
        "written. Write the code into 'src/app.py' itself: 'src/app.py' does not exist yet: create it with file_system "
        "action 'write', filename 'src/app.py' and the first part (whole functions or classes only), then add every "
        "further part with 'append' to 'src/app.py'.")
    assert not list(tmp_path.rglob("*.part1"))
    assert _fs(tmp_path, action="write", filename="src/app.py", content=APP_PART_1)["status"] == "success"
    result = _fs(tmp_path, action="append", filename="src/app.py.part2.txt", content=APP_PART_2)
    assert result["status"] == "error" and "'src/app.py' exists, so add your next part to it with file_system action " \
                                           "'append' and filename 'src/app.py'" in result["message"]
    assert not (tmp_path / "src" / "app.py.part2.txt").exists()


def test_a_rejected_write_says_the_file_was_not_created(tmp_path):
    result = _fs(tmp_path, action="write", filename="src/app.py", content=APP_WITH_OPEN_TRY)
    assert result["status"] == "error" and not (tmp_path / "src" / "app.py").exists()
    first_line = result["message"].splitlines()[0]
    assert first_line == (
        "Write rejected due to Python SyntaxError in resulting code: expected 'except' or 'finally' block at line 11. No "
        "changes were saved. Please check your indentation and brackets. 'src/app.py' was NOT created and still does not "
        "exist: send the COMPLETE corrected file with 'write' again. If it is too long for one reply, write its first part "
        "to 'src/app.py' (whole functions or classes only) and add the rest with 'append' to 'src/app.py'.")
    assert "Code around the error:" in result["message"]                              # the Round 1t report stays
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "app.py").write_text(APP_PART_1)
    result = _fs(tmp_path, action="write", filename="src/app.py", content=APP_WITH_OPEN_TRY, force_overwrite=True)
    assert result["status"] == "error" and " The existing 'src/app.py' is unchanged." in result["message"].splitlines()[0]
    assert (tmp_path / "src" / "app.py").read_text() == APP_PART_1


def test_a_python_file_is_built_with_append(tmp_path):
    assert _fs(tmp_path, action="append", filename="src/app.py", content=APP_PART_1)["status"] == "success"
    assert _fs(tmp_path, action="append", filename="src/app.py", content=APP_PART_2)["status"] == "success"
    assert (tmp_path / "src" / "app.py").read_text() == APP_PART_1 + APP_PART_2
    # a part that ends inside a function is refused and leaves the file as it was
    result = _fs(tmp_path, action="append", filename="src/app.py", content="\n\ndef handle_register(data):\n    try:\n        x = 1\n")
    assert result["status"] == "error" and result["message"].startswith(
        "Append rejected: 'src/app.py' would not be valid Python after it (expected 'except' or 'finally' block at line ")
    assert "Nothing was appended; 'src/app.py' is unchanged. Append whole functions or classes" in result["message"]
    assert (tmp_path / "src" / "app.py").read_text() == APP_PART_1 + APP_PART_2
    # the 21:00:31 file: a function that is only its docstring is named (the append WAS saved)
    result = _fs(tmp_path, action="append", filename="src/app.py",
                 content='\n\ndef get_or_create_player(name):\n    """Get existing player or create new one."""\n')
    assert result["status"] == "success" and "[Framework check - the edit WAS saved, but please fix:]" in result["message"]
    assert "function 'get_or_create_player'" in result["message"] and "contains only a docstring" in result["message"]


def test_append_starts_on_a_new_line_and_never_blocks_a_repair(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")                                          # no newline at the end
    assert _fs(tmp_path, action="append", filename="a.py", content="y = 2\n")["status"] == "success"
    assert (tmp_path / "a.py").read_text() == "x = 1\ny = 2\n"
    (tmp_path / "broken.py").write_text("def f(:\n")
    assert _fs(tmp_path, action="append", filename="broken.py", content="    pass\n")["status"] == "success"
    result = _fs(tmp_path, action="append", filename="new.py", content="def f(:\n")
    assert result["status"] == "error" and "'new.py' was NOT created." in result["message"]
    assert not (tmp_path / "new.py").exists()
    (tmp_path / "notes.txt").write_text("abc")                                       # other files: unchanged behaviour
    assert _fs(tmp_path, action="append", filename="notes.txt", content="def")["status"] == "success"
    assert (tmp_path / "notes.txt").read_text() == "abcdef"


def _edit(tmp_path, chunks, filename="src/app.py"):
    from src.tools.code_editor import CodeEditorTool
    return run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks", filename=filename,
                                        chunks=chunks))


def test_a_whole_file_chunk_creates_the_missing_file(tmp_path):
    (tmp_path / "src").mkdir()
    result = _edit(tmp_path, [{"start_line": 1, "end_line": -1, "search": "", "replace": APP_PART_1 + APP_PART_2}])
    assert result["status"] == "success"
    assert result["message"].startswith("[Note: 'src/app.py' did not exist, so your one whole-file chunk was saved as the "
                                        "new file. To create a file, use file_system 'write'.] Successfully wrote content to")
    assert (tmp_path / "src" / "app.py").read_text() == APP_PART_1 + APP_PART_2
    # 20:56:54 W1 sent the chunks as a JSON string
    chunks = json.dumps([{"start_line": 1, "end_line": -1, "search": "", "replace": "VALUE = 1\n"}])
    assert _edit(tmp_path, chunks, filename="src/config_values.py")["status"] == "success"
    assert (tmp_path / "src" / "config_values.py").read_text() == "VALUE = 1\n"
    assert _edit(tmp_path, [{"replace": "MAX_PLAYERS = 4\n"}], filename="src/limits.py")["status"] == "success"


def test_a_whole_file_chunk_is_checked_like_a_write(tmp_path):
    (tmp_path / "src").mkdir()
    result = _edit(tmp_path, [{"start_line": 1, "end_line": -1, "search": "", "replace": APP_WITH_OPEN_TRY}])
    assert result["status"] == "error" and "'src/app.py' was NOT created and still does not exist" in result["message"]
    assert not (tmp_path / "src" / "app.py").exists()


@pytest.mark.parametrize("chunks", [
    [{"start_line": 165, "end_line": 203, "search": "", "replace": "x = 1\n"}],             # 20:54:34: lines 165-203
    [{"search": "def handle_register(data):", "replace": "def handle_register(payload):"}],
    [{"start_line": 1, "end_line": 3, "replace": "a = 1\n"}, {"start_line": 5, "end_line": 6, "replace": "b = 2\n"}],
    [{"start_line": 1, "end_line": -1, "search": "", "replace": "   "}],
])
def test_edits_of_a_missing_file_are_still_refused(tmp_path, chunks):
    (tmp_path / "src").mkdir()
    result = _edit(tmp_path, chunks)
    assert result["status"] == "error" and result["message"].startswith("File 'src/app.py' does not exist yet")
    assert not (tmp_path / "src" / "app.py").exists()


def test_the_cut_off_notice_names_the_real_path():
    from src.agents.cycle_handler import cut_off_reply_notice
    agent = types.SimpleNamespace(_last_cut_off_max_tokens=4096, _consecutive_cut_off_replies=1, _last_base_max_tokens=4096,
                                  _cut_off_boost_level=1, _cut_off_boost_state="worker_work", state="worker_work")
    notice = cut_off_reply_notice(agent)
    assert "write its first part with file_system 'write', then add the rest with 'append' (one function or class per " \
           "call), always to the file's own path (e.g. 'src/app.py', never 'src/app.py.part1')." in notice
    assert notice.endswith("A file that does not exist yet has no lines to change: create it first.")

# END OF FILE tests/unit/test_round2t.py
