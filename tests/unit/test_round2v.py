# START OF FILE tests/unit/test_round2v.py
"""
Round 2v: fixtures from the 2026-10-22 10:27-10:38 run (WiFi_Snake_Game, a new project, qwen3.5:9b).

* 10:29:42 - the kick-off tasks said "Create server.py ...", "Create index.html file ...", "Create README.md ...", but
  PROJECT_STRUCTURE.md planned only tests/test_game.py; W1 first wrote 'backend/server.py' (refused: not in the layout).
* 10:34-10:38 - W1's server.py (14-16k characters per reply) was rejected four times for one small syntax error each
  ('{' never closed, then a missing comma after `'tick_interval_ms': 67` - the report showed lines 12-16, never the next
  item). The file never existed; every retry resent the whole file. After three rejections W1 was forced to report.
* 10:36:56 - W1 asked for worker_work with task id 'c73067a-df34-...' (one character missing from 'c73067a6-df34-...'):
  Taskwarrior read it as a subtraction ("Cannot subtract strings") and the bad id stayed W1's active task.
* The tools' own help (tool_information / the schema text) still described the old behaviour.
"""
import asyncio
import shutil
import types

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")

# server.py lines 1-17 as W1 sent it at 10:35:04 (lines 13-15 of the rejection report)
SERVER_MISSING_COMMA = (
    '"""\nWiFi Snake Game Backend Server\n"""\n\n'
    "import sqlite3\nimport threading\nimport json\n\n\n"
    "# Global game configuration\n"
    "GAME_CONFIG = {\n"
    "    'board_size': {'width': 15, 'height': 20},   # Grid: x=15 cols (short for mobile), y=20 rows\n"
    "    'tick_interval_ms': 67                        # ~14.9 ticks/sec -> approx 1 sec/move arcade feel\n"
    "\n"
    "    'max_players': 8,\n"
    "}\n"
    "\n\n"
    "def board_cells():\n"
    "    return GAME_CONFIG['board_size']['width'] * GAME_CONFIG['board_size']['height']\n")
SERVER_FIXED = SERVER_MISSING_COMMA.replace("'tick_interval_ms': 67 ", "'tick_interval_ms': 67,")
KICKOFF_TASKS = [
    "Create server.py implementing Flask backend with SQLite database setup for players, high_scores tables; register "
    "player endpoint POST /register accepting name and returning unique ID",
    "Create index.html file with embedded CSS styling for retro snake aesthetic featuring green-on-black grid",
    "Create tests/test_game.py with pytest test cases covering collision detection logic",
    "Create README.md documenting usage instructions including running server.py command line instruction",
    "Run server.py in background process, open browser at localhost:5000 or LAN IP address for mobile device testing",
]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_kept_drafts():
    from src.tools import rejected_drafts
    rejected_drafts.clear()
    yield
    rejected_drafts.clear()


# ======================================================================================== 1. the missing comma
def test_the_missing_comma_is_named_with_the_next_item():
    from src.tools.python_static_check import syntax_error_report
    with pytest.raises(SyntaxError) as raised:
        compile(SERVER_MISSING_COMMA, "server.py", "exec")
    report = syntax_error_report(SERVER_MISSING_COMMA, raised.value)
    assert report.splitlines()[:7] == [
        "Code around the error:",
        "     10 | # Global game configuration",
        "     11 | GAME_CONFIG = {",
        "     12 |     'board_size': {'width': 15, 'height': 20},   # Grid: x=15 cols (short for mobile), y=20 rows",
        ">>   13 |     'tick_interval_ms': 67                        # ~14.9 ticks/sec -> approx 1 sec/move arcade feel",
        "     14 | ",
        "     15 |     'max_players': 8,"]                                       # the next item is in view now
    assert ("Hint: a comma is missing at the END of line 13 (after `'tick_interval_ms': 67`, before any # comment): the "
            "next item starts on line 15 (`'max_players': 8,`).") in report


def test_a_missing_comma_on_one_line():
    from src.tools.python_static_check import missing_comma_hint, syntax_error_report
    source = "players = register(name color)\n"
    with pytest.raises(SyntaxError) as raised:
        compile(source, "a.py", "exec")
    assert "Hint: two items on line 1 are not separated by a comma (`name color`). Add ',' between them." in \
        syntax_error_report(source, raised.value)
    assert missing_comma_hint(["x"], SyntaxError("invalid syntax. Perhaps you forgot a comma?")).startswith(
        "Hint: a comma is missing between two items")


def test_a_hash_inside_a_string_is_not_a_comment():
    from src.tools.python_static_check import _code_part
    assert _code_part("    'colour': '#00ff00'   # green") == "    'colour': '#00ff00'"
    assert _code_part("    x = 1  # note") == "    x = 1"
    assert _code_part("    'a': \"x # y\"") == "    'a': \"x # y\""


# ======================================================================================== 2. a rejected new file is kept
def _fs(tmp_path, agent="W1", **kwargs):
    from src.tools.file_system import FileSystemTool
    return run(FileSystemTool().execute(agent_id=agent, agent_sandbox_path=tmp_path, scope="private", **kwargs))


def test_find_replace_fixes_the_rejected_text_and_creates_the_file(tmp_path):
    rejected = _fs(tmp_path, action="write", filename="server.py", content=SERVER_MISSING_COMMA)
    assert rejected["status"] == "error" and not (tmp_path / "server.py").exists()
    first_line = rejected["message"].splitlines()[0]
    assert ("'server.py' was NOT created and still does not exist. You do NOT have to send the whole file again: your "
            "rejected text of 'server.py' is kept. Fix only the faulty line(s) with file_system 'find_replace' (filename "
            "'server.py', find_text = the wrong text, replace_text = the corrected text) or 'replace_lines' (the line "
            "numbers shown below), or add a missing end with 'append'; 'server.py' is created as soon as the text is "
            "valid Python.") in first_line  # Round 2x: + 'append'

    assert rejected["message"].endswith("Fix the faulty line(s) with find_replace or replace_lines (the rejected text is "
                                        "kept), or send the COMPLETE corrected file with 'write'. Do not resend the same text.")
    fixed = _fs(tmp_path, action="find_replace", filename="server.py", find_text="'tick_interval_ms': 67 ",
                replace_text="'tick_interval_ms': 67,")
    assert fixed["status"] == "success"
    assert fixed["message"].startswith("[Framework: 'server.py' did not exist; your change was applied to your rejected "
                                       "text of it, which is now valid Python, so 'server.py' was created.] Successfully wrote")
    assert (tmp_path / "server.py").read_text() == SERVER_FIXED
    again = _fs(tmp_path, action="find_replace", filename="other.py", find_text="a", replace_text="b")
    assert again["status"] == "error" and again["message"].startswith("File not found: 'other.py'")   # unchanged


def test_replace_lines_on_the_kept_text(tmp_path):
    _fs(tmp_path, action="write", filename="server.py", content=SERVER_MISSING_COMMA)
    fixed = _fs(tmp_path, action="replace_lines", filename="server.py", replace_start_line=13, replace_end_line=13,
                content="    'tick_interval_ms': 67,  # ~14.9 ticks/sec\n")
    assert fixed["status"] == "success" and "'server.py' was created" in fixed["message"]
    assert "    'tick_interval_ms': 67,  # ~14.9 ticks/sec\n\n    'max_players': 8," in (tmp_path / "server.py").read_text()


def test_a_fix_that_leaves_another_error_keeps_the_new_text(tmp_path):
    broken_twice = SERVER_MISSING_COMMA.replace("def board_cells():", "def board_cells(:")
    _fs(tmp_path, action="write", filename="server.py", content=broken_twice)
    step = _fs(tmp_path, action="find_replace", filename="server.py", find_text="'tick_interval_ms': 67 ",
               replace_text="'tick_interval_ms': 67,")
    assert step["status"] == "error" and not (tmp_path / "server.py").exists()
    assert step["message"].startswith("Your change was applied to your kept (rejected) text of 'server.py', but it is still "
                                      "not valid Python (")
    assert ">>   19 | def board_cells(:" in step["message"]                       # the NEXT error, in the fixed text
    done = _fs(tmp_path, action="find_replace", filename="server.py", find_text="def board_cells(:",
               replace_text="def board_cells():")
    assert done["status"] == "success" and (tmp_path / "server.py").read_text() == SERVER_FIXED


def test_text_that_is_not_in_the_kept_draft(tmp_path):
    _fs(tmp_path, action="write", filename="server.py", content=SERVER_MISSING_COMMA)
    missing = _fs(tmp_path, action="find_replace", filename="server.py", find_text="TICK = 67", replace_text="TICK = 67,")
    assert missing["status"] == "error" and missing["message"].startswith(
        "find_text was NOT found in your kept (rejected) text of 'server.py', so nothing was changed.")
    same = _fs(tmp_path, action="find_replace", filename="server.py", find_text="import json", replace_text="import json")
    assert same["status"] == "error" and same["message"].startswith("Your change does not change the kept (rejected) text")
    assert not (tmp_path / "server.py").exists()


def test_the_kept_text_belongs_to_its_writer_and_expires(tmp_path):
    from src.tools import rejected_drafts
    _fs(tmp_path, action="write", filename="server.py", content=SERVER_MISSING_COMMA)
    other = _fs(tmp_path, agent="W2", action="find_replace", filename="server.py", find_text="'tick_interval_ms': 67 ",
                replace_text="'tick_interval_ms': 67,")
    assert other["status"] == "error" and other["message"].startswith("File not found: 'server.py'")
    path = tmp_path / "server.py"
    assert rejected_drafts.get(path, "W1") == SERVER_MISSING_COMMA
    assert rejected_drafts.get(path, "W1", now=10**12) is None                     # expired after DRAFT_TTL_SECONDS
    rejected_drafts.keep(path, "W1", "x = (")
    assert _fs(tmp_path, action="write", filename="server.py", content=SERVER_FIXED)["status"] == "success"
    assert rejected_drafts.get(path, "W1") is None                                 # a written file needs no kept text


def test_a_rejected_change_of_an_existing_file_keeps_nothing(tmp_path):
    from src.tools import rejected_drafts
    (tmp_path / "server.py").write_text(SERVER_FIXED)
    result = _fs(tmp_path, action="write", filename="server.py", content=SERVER_MISSING_COMMA, force_overwrite=True)
    assert result["status"] == "error" and "The existing 'server.py' is unchanged." in result["message"]
    assert rejected_drafts.get(tmp_path / "server.py", "W1") is None
    assert result["message"].endswith("Send the COMPLETE corrected file. Keep the code simple (no deeply nested try/except) "
                                      "and do not resend the same text.")


def test_code_editor_points_to_the_kept_text(tmp_path):
    from src.tools.code_editor import CodeEditorTool
    _fs(tmp_path, action="write", filename="server.py", content=SERVER_MISSING_COMMA)
    result = run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks", filename="server.py",
                                          chunks=[{"start_line": 13, "end_line": 13, "replace": "    'tick_interval_ms': 67,\n"}]))
    assert result["status"] == "error" and result["message"] == (
        "File 'server.py' does not exist yet: your 'write' of it was rejected, so code_editor cannot edit it. Your rejected "
        "text is kept: fix the faulty line with file_system action 'find_replace' (filename 'server.py', find_text, "
        "replace_text) or 'replace_lines', or add a missing end with 'append'; 'server.py' is created as soon as the text "
        "is valid Python.")  # Round 2x: + 'append'


def test_the_kept_text_in_the_shared_workspace(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.file_system import FileSystemTool
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")

    def call(**kwargs):
        return run(FileSystemTool().execute(agent_id="W1", agent_sandbox_path=tmp_path / "sandbox", project_name="WiFi_Snake_Game",
                                            session_name="startup_1792636039", **kwargs))
    assert call(action="write", filename="server.py", content=SERVER_MISSING_COMMA)["status"] == "error"
    assert call(action="find_replace", filename="server.py", find_text="'tick_interval_ms': 67 ",
                replace_text="'tick_interval_ms': 67,")["status"] == "success"
    workspace = tmp_path / "projects" / "WiFi_Snake_Game" / "startup_1792636039" / "shared_workspace"
    assert (workspace / "server.py").read_text() == SERVER_FIXED


# ======================================================================================== 3. root files of the kick-off
def test_files_the_tasks_create_in_the_root():
    from src.utils.task_files import created_root_files
    assert [created_root_files(text) for text in KICKOFF_TASKS] == [["server.py"], ["index.html"], [], ["README.md"], []]
    assert created_root_files("Create socket handlers; write a concise README; add the file config.json") == ["config.json"]
    assert created_root_files("Create a new `app.js` and write .env") == ["app.js"]


def test_root_files_join_the_planned_files():
    from src.workflows.pm_kickoff_workflow import render_project_structure_md, with_root_files
    planned = with_root_files(["tests/test_game.py"], ["server.py", "index.html", "README.md"])
    assert planned == ["tests/test_game.py", "server.py", "index.html", "README.md"]
    assert with_root_files(["src/core/server.py"], ["server.py", "README.md"]) == ["src/core/server.py", "README.md"]
    text = render_project_structure_md(["tests"], planned, None, [], python_project=True)
    assert "- server.py\n- index.html\n- README.md" in text and "  server.py\n" in text


def test_a_file_outside_the_layout_is_pointed_to_its_planned_path(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.file_system import FileSystemTool
    from src.workflows.pm_kickoff_workflow import render_project_structure_md, with_root_files
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / "WiFi_Snake_Game" / "startup_1792636039" / "shared_workspace"
    (workspace / "tests").mkdir(parents=True)
    planned = with_root_files(["tests/test_game.py"], ["server.py", "index.html", "README.md"])
    (workspace / "PROJECT_STRUCTURE.md").write_text(render_project_structure_md(["tests"], planned, None, [], python_project=True))
    result = run(FileSystemTool().execute(agent_id="W1", agent_sandbox_path=tmp_path / "sandbox", project_name="WiFi_Snake_Game",
                                          session_name="startup_1792636039", action="write", filename="backend/server.py",
                                          content=SERVER_FIXED))
    assert result["status"] == "error" and not (workspace / "backend").exists()
    assert result["message"].startswith("Workspace Bloat Protection: 'backend/' is not a directory of this project's layout "
                                        "(PROJECT_STRUCTURE.md), so NOTHING was written. Use the workspace root or one of "
                                        "these directories: assets, build, components, config, css, dist, docs, html, images, "
                                        "img, js, pages, public, src, styles, tests, utils.")
    assert result["message"].endswith("PROJECT_STRUCTURE.md plans this file as 'server.py': write it at exactly that path.")


# ======================================================================================== 4. a mistyped task UUID
@pytest.mark.parametrize("a, b, limit, close", [
    ("c73067a6-df34-4121-bea4-3aa570a29611", "c73067a-df34-4121-bea4-3aa570a29611", 2, True),   # 10:36:56
    ("c73067a6-df34-4121-bea4-3aa570a29611", "c73067a66-df34-4121-bea4-3aa570a29611", 2, True),
    ("2b7e308d-1111-2222-3333-444455556666", "3b7e308d-1111-2222-3333-444455556667", 2, True),
    ("c73067a6-df34-4121-bea4-3aa570a29611", "37025fae-2da3-45df-9ee8-c54028031c00", 2, False),
    ("abc", "abcdef", 2, False),
])
def test_edit_distance(a, b, limit, close):
    from src.tools.project_management import edit_distance_at_most
    assert edit_distance_at_most(a, b, limit) is close


def test_a_uuid_with_a_missing_character_names_its_task():
    from src.tools.project_management import match_task_by_uuid_prefix
    tasks = [{"uuid": "c73067a6-df34-4121-bea4-3aa570a29611"}, {"uuid": "37025fae-2da3-45df-9ee8-c54028031c00"},
             {"uuid": "6fda995c-4099-4473-bb1b-036d227acb15"}]
    assert match_task_by_uuid_prefix(tasks, "c73067a-df34-4121-bea4-3aa570a29611") == [tasks[0]]
    assert match_task_by_uuid_prefix(tasks, "37025fae-2da3-45df-9ee8-c54028031c0") == [tasks[1]]
    assert match_task_by_uuid_prefix(tasks, "deadbeef-df34-4121-bea4-3aa570a29611") == []


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

    def call(agent, **kw):
        return run(pm_module.ProjectManagementTool().execute(agent_id=agent, agent_sandbox_path=tmp_path, project_name="P",
                                                             session_name="S", **kw))
    return call


@needs_tw
def test_modify_task_with_a_uuid_missing_a_character(tw_env):
    created = tw_env("PM1", action="add_task", description="Kick-off Task 1: Create server.py", assignee_agent_id="W1")
    uuid = created["task_uuid"]
    short = uuid[:7] + uuid[8:]                                                     # one character dropped
    result = tw_env("W1", action="modify_task", task_id=short, task_progress="doing")
    assert result["status"] == "success" and result["task_uuid"] == uuid
    unknown = tw_env("W1", action="modify_task", task_id="deadbee-df34-4121-bea4-3aa570a29611", task_progress="doing")
    assert unknown["status"] == "error" and "Cannot subtract" not in unknown["message"]


@needs_tw
def test_request_state_with_a_mistyped_uuid_uses_the_real_task(tw_env, monkeypatch):
    from src.agents.cycle_handler import AgentCycleHandler
    created = tw_env("PM1", action="add_task", description="Kick-off Task 1: Create server.py", assignee_agent_id="W1")
    uuid = created["task_uuid"]
    manager = types.SimpleNamespace(
        current_project="P", current_session="S",
        workflow_manager=types.SimpleNamespace(resolve_state_alias=lambda agent_type, state: state))
    handler = types.SimpleNamespace(_manager=manager, _infer_worker_task=lambda agent_id: None)
    worker = types.SimpleNamespace(agent_id="W1", agent_type="worker", active_task_id=None)
    AgentCycleHandler._handle_worker_task_tracking(handler, worker, "worker_work", uuid[:7] + uuid[8:])
    assert worker.active_task_id == uuid
    tw = __import__("src.tools.project_management", fromlist=["x"]).ProjectManagementTool()._get_taskwarrior_instance("P", "S")
    assert tw.tasks.get(uuid=uuid)["task_progress"] == "doing"
    with pytest.raises(ValueError) as refused:
        AgentCycleHandler._handle_worker_task_tracking(handler, worker, "worker_work", "deadbee-df34-4121-bea4-3aa570a29611")
    assert "could not be found in the task database" in str(refused.value) and uuid in str(refused.value)
    assert worker.active_task_id == uuid                                            # the bad id is not kept


# ======================================================================================== 5. the tools' own help
def test_the_file_system_help_describes_what_the_actions_do():
    from src.tools.file_system import FileSystemTool
    tool = FileSystemTool()
    write_help = tool.get_detailed_usage(sub_action="write")
    assert "your text is kept: fix only the faulty line with `find_replace`" in write_help
    assert "Part files such as `src/app.py.part1` are refused." in write_help
    assert "you wrote it yourself, or (Python) your new version keeps all its functions" in write_help
    assert "append whole functions or classes" in tool.get_detailed_usage(sub_action="append")
    assert "kept (rejected) text" in tool.get_detailed_usage(sub_action="find_replace")
    assert "kept (rejected) text" in tool.get_detailed_usage(sub_action="replace_lines")
    assert "When the 'write' of a NEW such file is rejected, its text is kept" in tool.description  # Round 2x wording
    action = next(p for p in tool.parameters if p.name == "action")
    for name in ("append", "insert_lines", "replace_lines", "find_replace"):
        assert f"'{name}'" in action.description
    content = next(p for p in tool.parameters if p.name == "content")
    assert "'append', 'insert_lines' and 'replace_lines'" in content.description
    every_help = "".join(tool.get_detailed_usage(sub_action=a) for a in [None, "read", "write", "append", "list", "git_init"])
    assert "Default: 'private'." not in every_help and "Default: 'shared' when you work on a project" in every_help


def test_the_code_editor_and_project_management_help():
    from src.tools.code_editor import CodeEditorTool
    from src.tools.project_management import ProjectManagementTool
    usage = CodeEditorTool().get_detailed_usage()
    assert "code_editor edits EXISTING files. To create a file use file_system 'write'." in usage
    assert "your rejected text is kept" in usage
    pm_tool = ProjectManagementTool()
    assignee = next(p for p in pm_tool.parameters if p.name == "assignee_agent_id")
    assert "a Project Manager, the Admin AI or the Guardian is refused" in assignee.description
    assert "The ID of the WORKER to assign this task to" in pm_tool.get_detailed_usage(sub_action="add_task")
    assert "Assign or reassign the task to a WORKER" in pm_tool.get_detailed_usage(sub_action="modify_task")  # Round 2w wording


def test_the_worker_prompt_knows_the_kept_text():
    import yaml
    from pathlib import Path
    prompt = yaml.safe_load((Path(__file__).resolve().parents[2] / "prompts.yaml").read_text())["worker_work_prompt"]
    assert "**REJECTED NEW FILE:**" in prompt and "do NOT send the whole file again" in prompt

# END OF FILE tests/unit/test_round2v.py
