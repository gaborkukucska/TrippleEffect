# START OF FILE tests/unit/test_round3q.py
"""
Round 3q: the run of 2026-10-25 22:38-23:50 (WiFi_Snake_Game, qwen3.5:4b-q4_K_M on a remote Ollama, ~80 output tokens/s).

* 22:53:46 W2 wrote tests/test_players.py with `from src.frontend.players import (...)` for src/frontend/players.js. Every
  run failed with "ModuleNotFoundError: No module named 'src.frontend.players'" and the diagnosis said nothing; PM1 made a
  task to "fix the test imports" and W1 spent 23:21-23:50 on src/frontend/players.py, a Python copy of players.js.
* 23:28:02-23:46:50 the Round 3p hint told W1 four times that this task was DONE, while each of its 8 modify_task calls was
  refused with "no full test run of the CURRENT files has passed yet".
* 23:17:00-23:33:29 project_management(action='request_state', state=...) was refused four times.
* 22:48:18 a code_editor chunk replaced lines 10-54 of src/frontend/index.html with line 10 alone (44 lines deleted).
* The finished game's index.html loaded only game.js: players.js and leaderboard.js never ran.
* PROJECT_STRUCTURE.md told this Python + JavaScript project only "Run tests ... with `python -m pytest -q`".
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Game", "startup_1792939138"

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


PLAYERS_JS = """// WiFi Snake Game - Player Management Module
let players = {};
function registerPlayer(name) {
    players[name] = { name: name, score: 0 };
    localStorage.setItem('players', JSON.stringify(players));
    return players[name];
}
"""
TEST_PLAYERS_PY = '''"""Tests for player management module."""
import pytest
from src.frontend.players import (
    Player,
    register_player,
)


def test_register_player():
    assert register_player("Ann").name == "Ann"
'''
INDEX_HTML = """<!DOCTYPE html>
<html>
<head><link rel="stylesheet" href="styles.css"></head>
<body>
    <canvas id="gameCanvas"></canvas>
    <script src="game.js"></script>
</body>
</html>
"""
GAME_JS = "const canvas = document.getElementById('gameCanvas');\nfunction loop() { requestAnimationFrame(loop); }\n"
# the output of `python -m pytest -q` for the files above (22:53:59)
PYTEST_OUTPUT = """
==================================== ERRORS ====================================
____________________ ERROR collecting tests/test_players.py ____________________
ImportError while importing test module '/ws/tests/test_players.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_players.py:3: in <module>
    from src.frontend.players import (
E   ModuleNotFoundError: No module named 'src.frontend.players'
=========================== short test summary info ============================
ERROR tests/test_players.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.11s
"""


def _game_workspace(root: Path) -> Path:
    (root / "src" / "frontend").mkdir(parents=True)
    (root / "src" / "backend").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "frontend" / "players.js").write_text(PLAYERS_JS, encoding="utf-8")
    (root / "src" / "frontend" / "game.js").write_text(GAME_JS, encoding="utf-8")
    (root / "src" / "frontend" / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "src" / "backend" / "server.py").write_text("import http.server\n", encoding="utf-8")
    (root / "tests" / "test_players.py").write_text(TEST_PLAYERS_PY, encoding="utf-8")
    return root


# ======================================================================================== 1. a Python import of JavaScript
def test_the_javascript_file_behind_a_python_import(tmp_path):
    from src.tools.project_links_check import other_language_module_file
    root = _game_workspace(tmp_path)
    assert other_language_module_file(root, "src.frontend.players") == "src/frontend/players.js"
    assert other_language_module_file(root, "frontend.players") == "src/frontend/players.js"   # via src/ (conftest)
    assert other_language_module_file(root, "players") == "src/frontend/players.js"            # the only file of that name
    assert other_language_module_file(root, "src.frontend.game") == "src/frontend/game.js"
    assert other_language_module_file(root, "src.backend.server") is None                      # Python
    assert other_language_module_file(root, "src.frontend") is None                            # a (namespace) package
    assert other_language_module_file(root, "json") is None and other_language_module_file(root, "pytest") is None
    assert other_language_module_file(root, "src.frontend.missing") is None                    # nothing to point at
    (root / "src" / "players.py").write_text("def register_player(name):\n    return name\n", encoding="utf-8")
    assert other_language_module_file(root, "players") is None                                 # a Python players.py exists
    (root / "tasklib.js").write_text("window.x = 1;\n", encoding="utf-8")
    assert other_language_module_file(root, "tasklib") is None                                 # an installed package
    assert other_language_module_file(root, "not-a.module") is None and other_language_module_file(root, "") is None


def test_a_python_test_that_imports_javascript_is_named_on_write(tmp_path):
    from src.tools.project_links_check import link_notes_text, link_problems
    root = _game_workspace(tmp_path)
    note = link_notes_text(root, "tests/test_players.py", TEST_PLAYERS_PY)
    assert note.startswith("\n[Framework check - the file WAS saved, but it does not work with the project yet (a task "
                           "with this problem cannot be marked finished):]\n- tests/test_players.py imports "
                           "'src.frontend.players' (line 3), but that module is JavaScript code: src/frontend/players.js. "
                           "Python cannot import a JavaScript file, so this fails with \"ModuleNotFoundError: No module "
                           "named 'src.frontend.players'\" and no test in the file runs.")
    assert ("JavaScript is tested with a JavaScript test run by Node.js (e.g. tests/players.test.js with `const players = "
            "require('../src/frontend/players.js');`, run with `node --test tests/players.test.js`); src/frontend/players.js exports "
            "nothing yet, so it needs e.g. `if (typeof module !== 'undefined') module.exports = { ... };`") in note
    assert note.endswith("pytest tests only Python code. Do NOT write a Python copy of src/frontend/players.js to make "
                         "the import work.")
    assert note.count("src/frontend/players.js. Python") == 1     # 'src.frontend.players.Player' is the same file
    # the completion check sees it too: a task naming the test file cannot be marked finished
    problems = link_problems(root, root / "tests" / "test_players.py")
    assert len(problems) == 1 and "that module is JavaScript code: src/frontend/players.js" in problems[0]
    # an exporting JavaScript file gets no export advice; a Python module of that name ends the problem
    (root / "src" / "frontend" / "players.js").write_text(PLAYERS_JS + "module.exports = { registerPlayer };\n",
                                                          encoding="utf-8")
    assert "exports nothing yet" not in link_notes_text(root, "tests/test_players.py", TEST_PLAYERS_PY)
    (root / "src" / "frontend" / "players.py").write_text("class Player:\n    pass\n", encoding="utf-8")
    assert link_notes_text(root, "tests/test_players.py", TEST_PLAYERS_PY) == ""


def test_imports_of_python_code_are_not_judged(tmp_path):
    from src.tools.project_links_check import python_import_language_problems
    root = _game_workspace(tmp_path)
    source = "import os\nfrom src.backend import server\nfrom src.backend.server import handler\nimport requests\n"
    assert python_import_language_problems(root, "tests/test_server.py", source) == []
    assert python_import_language_problems(root, "src/backend/server.py", "from src.frontend import game\n")[0].startswith(
        "src/backend/server.py imports 'src.frontend.game' (line 1), but that module is JavaScript code: "
        "src/frontend/game.js. Python cannot import a JavaScript file, so this fails with \"ModuleNotFoundError: No module "
        "named 'src.frontend.game'\". ")                                           # not a test file: no "no test runs"
    assert python_import_language_problems(root, "tests/test_players.py", "def broken(:\n") == []
    assert python_import_language_problems(root, "src/frontend/game.js", GAME_JS) == []


def test_the_failed_test_run_names_the_javascript_file(tmp_path):
    from src.utils.test_diagnosis import diagnose_failed_test_run
    root = _game_workspace(tmp_path)
    with quiet():
        text = run(diagnose_failed_test_run(root, "python -m pytest -q", PYTEST_OUTPUT))
    assert text.startswith("\n\n[Framework hints for this test output:]\n- The tests import 'src.frontend.players', but "
                           "that module is JavaScript (src/frontend/players.js): Python cannot import a JavaScript file - "
                           "that is the ModuleNotFoundError. JavaScript is tested with a JavaScript test run by Node.js")
    assert text.endswith("pytest tests only Python code. Do NOT write a Python copy of src/frontend/players.js.")
    with quiet():
        other = run(diagnose_failed_test_run(root, "python -m pytest -q", PYTEST_OUTPUT.replace(
            "src.frontend.players", "src.frontend.nothing")))
    assert "JavaScript" not in other


def test_the_project_structure_says_which_tool_tests_which_language():
    from src.workflows.pm_kickoff_workflow import (render_project_structure_md, PYTHON_TEST_LINES,
                                                   MIXED_STACK_TEST_LINES, OTHER_STACK_TEST_LINES)
    mixed = render_project_structure_md(["src/frontend", "src/backend", "tests"],
                                        ["src/frontend/game.js", "src/backend/server.py", "tests/test_server.py"])
    assert "\n".join(PYTHON_TEST_LINES + MIXED_STACK_TEST_LINES) in mixed
    assert "A Python test can NOT import a JavaScript file" in mixed
    python_only = render_project_structure_md(["src", "tests"], ["src/main.py", "tests/test_main.py"])
    assert "\n".join(PYTHON_TEST_LINES) in python_only and "JavaScript" not in python_only
    js_only = render_project_structure_md(["src"], ["src/game.js", "index.html"])
    assert "\n".join(OTHER_STACK_TEST_LINES) in js_only and "pytest" not in js_only


# ======================================================================================== 2. "done" only when it can finish
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
    from src.utils.test_runs import clear_test_runs
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
    clear_test_runs()
    yield call
    clear_test_runs()


@needs_tw
def test_a_task_whose_tests_have_not_passed_is_not_called_done(tasks):
    from src.agents.task_focus import task_done_hint
    from src.utils.test_runs import record_test_run, PASSED, FAILED
    w1 = Worker("W1")
    manager = _manager(w1)
    description = "Fix the failing tests in tests/test_players.py so that they test src/players.py"
    added = tasks("PM1", manager, action="add_task", description=description, project_filter=PROJECT,
                  assignee_agent_id="W1")
    assert added["status"] == "success", added
    w1.active_task_id = added["task_uuid"]
    (tasks.workspace / "src").mkdir()
    (tasks.workspace / "tests").mkdir()
    (tasks.workspace / "src" / "players.py").write_text("def register_player(name):\n    return name\n", encoding="utf-8")
    (tasks.workspace / "tests" / "test_players.py").write_text(
        "from players import register_player\n\n\ndef test_register():\n    assert register_player('a') == 'a'\n",
        encoding="utf-8")
    # 23:28: no full run of the current files has passed - modify_task would refuse, so there is no "DONE" hint
    assert task_done_hint(manager, w1) == ""
    refused = tasks("W1", manager, action="modify_task", task_id=w1.active_task_id, task_progress="finished")
    assert refused["status"] == "error" and "no full test run of the CURRENT files has passed yet" in refused["message"]
    record_test_run(tasks.workspace, "python -m pytest -q", FAILED, "1 failed")
    assert task_done_hint(manager, w1) == ""
    record_test_run(tasks.workspace, "python -m pytest -q", PASSED, "1 passed")
    hint = task_done_hint(manager, w1)
    assert hint.startswith("[Framework check]: The file(s) your task names (tests/test_players.py, src/players.py) exist "
                           "and pass the framework's checks.")
    # a change after the passing run asks for a new run first
    (tasks.workspace / "src" / "players.py").write_text("def register_player(name):\n    return name.strip()\n",
                                                        encoding="utf-8")
    assert task_done_hint(manager, w1) == ""


# ======================================================================================== 3. a state request in the wrong tool
def test_a_state_request_inside_project_management():
    from src.agents.core import misplaced_state_request
    assert misplaced_state_request("project_management", {"action": "request_state", "state": "pm_audit"})
    assert misplaced_state_request("project_management", {"action": " Change_State ", "state": "worker_work",
                                                          "task_id": "31d18413"})
    assert not misplaced_state_request("project_management", {"action": "request_state"})        # no state: refused
    assert not misplaced_state_request("project_management", {"action": "modify_task", "state": "x"})
    assert not misplaced_state_request("file_system", {"action": "request_state", "state": "worker_work"})
    assert not misplaced_state_request("project_management", "not a dict")


class _FakeProvider:
    def __init__(self, events):
        self.events = events

    async def stream_completion(self, messages, model, temperature, max_tokens=None, tools=None, **kwargs):
        for event in self.events:
            yield event


def _core_agent(provider, monkeypatch, agent_type="pm", state="pm_report_check"):
    from src.agents.core import Agent
    import src.agents.core as core_module
    monkeypatch.setattr(core_module.settings, "NATIVE_TOOL_CALLING_ENABLED", True)

    async def no_workflow(*_args, **_kwargs):
        return None
    tools = {name: types.SimpleNamespace(auth_level="pm", get_json_schema=lambda n=name: {"name": n})
             for name in ("project_management", "file_system", "send_message")}
    manager = types.SimpleNamespace(
        tool_executor=types.SimpleNamespace(tools=tools),
        workflow_manager=types.SimpleNamespace(process_agent_output_for_workflow=no_workflow,
                                               resolve_state_alias=lambda t, s: s,
                                               is_valid_state=lambda t, s: s in ("pm_audit", "pm_manage", "worker_work"),
                                               _valid_states={}),
        cycle_handler=None)
    agent = Agent({"agent_id": "PM1", "config": {"agent_type": agent_type, "provider": "ollama", "model": "m"}}, provider,
                  manager)
    agent.state = state
    agent.ensure_sandbox_exists = lambda: True
    return agent


def _consume(agent):
    async def go():
        return [event async for event in agent.process_message()]
    return run(go())


def test_the_core_runs_it_as_a_state_request(monkeypatch):
    call = {"id": "call_1", "function": {"name": "project_management",
                                         "arguments": {"action": "request_state", "state": "pm_audit"}}}
    agent = _core_agent(_FakeProvider([{"type": "native_tool_calls", "tool_calls": [call]},
                                       {"type": "finish_info", "done_reason": "stop"}]), monkeypatch)
    with quiet():
        events = _consume(agent)
    changes = [e for e in events if e.get("type") == "agent_state_change_requested"]
    assert len(changes) == 1 and changes[0]["requested_state"] == "pm_audit"
    assert not [e for e in events if e.get("type") == "tool_requests"]
    # W1 23:33:29: with its task id
    call["function"]["arguments"] = {"action": "request_state", "state": "worker_work", "task_id": "31d18413"}
    agent = _core_agent(_FakeProvider([{"type": "native_tool_calls", "tool_calls": [call]},
                                       {"type": "finish_info", "done_reason": "stop"}]), monkeypatch, "worker",
                        "worker_decompose")
    with quiet():
        changes = [e for e in _consume(agent) if e.get("type") == "agent_state_change_requested"]
    assert changes[0]["requested_state"] == "worker_work" and changes[0]["task_id"] == "31d18413"
    # any other project_management call is still a tool call
    call["function"]["arguments"] = {"action": "list_tasks"}
    agent = _core_agent(_FakeProvider([{"type": "native_tool_calls", "tool_calls": [call]},
                                       {"type": "finish_info", "done_reason": "stop"}]), monkeypatch)
    with quiet():
        requests = [e for e in _consume(agent) if e.get("type") == "tool_requests"]
    assert requests and requests[0]["calls"][0]["name"] == "project_management"


# ======================================================================================== 4. an edit cut short
INDEX_LINES = ['<!DOCTYPE html>', '<html lang="en">', '<head>', '    <meta charset="UTF-8">', '    <title>Snake</title>',
               '    <link rel="stylesheet" href="styles.css">', '</head>', '<body>', '    <!-- page -->',
               '    <div class="game-container">', '        <h1 class="title">WiFi Snake</h1>',
               '        <canvas id="gameCanvas" width="400" height="400"></canvas>', '        <div id="score">0</div>',
               '        <input id="playerName" type="text">', '        <button id="startBtn">Start</button>',
               '        <ol id="leaderboard"></ol>', '    </div>', '    <script src="game.js"></script>', '</body>',
               '</html>']


def test_a_replacement_that_only_repeats_the_first_line_is_refused(tmp_path):
    from src.tools.code_editor import CodeEditorTool
    path = tmp_path / "index.html"
    original = "\n".join(INDEX_LINES) + "\n"
    path.write_text(original, encoding="utf-8")

    def edit(chunks):
        with quiet():
            return run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks",
                                                filename="index.html", chunks=chunks))
    # 22:48:18: lines 10-17 -> line 10 alone
    result = edit([{"start_line": 10, "end_line": 17, "replace": '    <div class="game-container">'}])
    assert result["status"] == "error"
    assert ("Chunk 0: lines 10-17 would be replaced by their own first line (unchanged), so lines 11-17 (7 lines of code) "
            "would simply be DELETED - your 'replace' text looks cut short. NOTHING was changed. To change these lines, "
            "send ALL their new text as 'replace' (read the file first if needed); to delete lines 11-17 on purpose, send "
            "start_line 11, end_line 17 and 'replace': ''.") in result["message"]
    assert path.read_text(encoding="utf-8") == original
    # the explicit deletion it suggests works
    result = edit([{"start_line": 11, "end_line": 17, "replace": ""}])
    assert result["status"] == "success"
    assert path.read_text(encoding="utf-8").splitlines()[9:11] == ['    <div class="game-container">',
                                                                    '    <script src="game.js"></script>']


def test_other_line_range_edits_are_not_affected(tmp_path):
    from src.tools.code_editor import truncated_range_replacement
    lines = [line + "\n" for line in INDEX_LINES]
    assert truncated_range_replacement(lines, 9, 17, '    <div class="game-container">\n        <h1>New</h1>') is None
    assert truncated_range_replacement(lines, 9, 13, '    <div class="game-container">') is None   # 3 lines lost: < 5
    assert truncated_range_replacement(lines, 9, 17, "") is None                                  # an intended deletion
    assert truncated_range_replacement(lines, 9, 17, '    <section class="game">') is None         # changed text
    assert truncated_range_replacement(lines, 9, 10, '    <div class="game-container">') is None   # one line, unchanged
    two = truncated_range_replacement(lines, 9, 17, '    <div class="game-container">\n        <h1 class="title">WiFi '
                                                    'Snake</h1>\n')
    assert two.startswith("lines 10-17 would be replaced by their own first 2 lines (unchanged), so lines 12-17 (6 lines")


# ======================================================================================== 5. a script no page loads
def test_a_browser_script_that_no_page_loads_gets_a_note(tmp_path):
    from src.tools.project_links_check import link_notes_text, link_problems, unloaded_browser_script_problem
    root = _game_workspace(tmp_path)
    note = link_notes_text(root, "src/frontend/players.js", PLAYERS_JS)
    assert note == ("\n[Framework note - the file WAS saved:]\n- src/frontend/players.js is browser code, but no page loads "
                    "it: src/frontend/index.html has no `<script src=\"players.js\">`, so its code never runs. Add "
                    "`<script src=\"players.js\"></script>` to the page that uses it, before the script that calls its "
                    "functions.")
    assert link_problems(root, root / "src" / "frontend" / "players.js") == []   # a note, not a completion problem
    assert unloaded_browser_script_problem(root, "src/frontend/game.js", GAME_JS) is None           # loaded by the page
    # loaded by a page, with a query string, or named by another script: no note
    page = root / "src" / "frontend" / "index.html"
    page.write_text(INDEX_HTML.replace('<script src="game.js">', '<script src="players.js?v=2"></script>\n    '
                                       '<script src="game.js">'), encoding="utf-8")
    assert unloaded_browser_script_problem(root, "src/frontend/players.js", PLAYERS_JS) is None
    page.write_text(INDEX_HTML, encoding="utf-8")
    (root / "src" / "frontend" / "game.js").write_text("import { registerPlayer } from './players.js';\n" + GAME_JS,
                                                       encoding="utf-8")
    assert unloaded_browser_script_problem(root, "src/frontend/players.js", PLAYERS_JS) is None
    # Node.js code, tests, and projects whose pages load no local script are not judged
    assert unloaded_browser_script_problem(root, "src/server.js", "const fs = require('fs');\nwindow.x = 1;\n") is None
    assert unloaded_browser_script_problem(root, "tests/players.test.js", "document.body;\ntest('x', () => {});\n") is None
    page.write_text("<html><body><script src=\"https://cdn.example.com/lib.js\"></script></body></html>",
                    encoding="utf-8")
    assert unloaded_browser_script_problem(root, "src/frontend/leaderboard.js", "localStorage.getItem('a');\n") is None

# END OF FILE tests/unit/test_round3q.py
