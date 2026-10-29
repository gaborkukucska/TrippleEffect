# START OF FILE tests/unit/test_round3s.py
"""
Round 3s: the run of 2026-10-26 09:28-11:17 (WiFi_Snake_Game, qwen3.5:4b-q4_K_M on a remote Ollama).

* 09:29:08 "Kick-off Task 7: Write pytest tests for game logic (movement, collision, score calculation) in
  tests/test_game_logic.py" - the game was JavaScript (src/core/snake_game.js, ...), its only Python file src/server/server.py.
  W3 wrote Python "mocks" of the game (tests/mock_game_logic.py) and a "Python bridge module" (src/core/snake_game.py,
  src/utils/score_manager.py) and worked on them until the end of the run.
* 09:33:18-09:36:35 the Admin AI, which had handed the project to PM1, wrote game.js and server.py into the team's workspace.
* 10:22-10:25 W3 in worker_report repeated a file_system call 15 times: the duplicate check gave back its earlier result.
* 11:13:31 a code_editor chunk with a 'search' OBJECT crashed: "'dict' object has no attribute 'splitlines'".
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Game", "startup_1792978084"

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


SNAKE_GAME_JS = ("// Snake Game Core\nconst CONFIG = { gridSize: 20 };\nfunction movePlayer(player) {\n"
                 "    player.body.unshift({ x: player.body[0].x + 1, y: player.body[0].y });\n    player.body.pop();\n}\n")
STRUCTURE = [{"dir": "src", "children": [{"dir": "core", "files": ["snake_game.js"]},
                                         {"dir": "utils", "files": ["input_handler.js", "score_manager.js"]},
                                         {"dir": "mobile", "files": ["touch_controls.js"]},
                                         {"dir": "server", "files": ["server.py"]}]},
             {"dir": "tests", "files": ["test_game_logic.py", "test_snake_game.js"]},
             {"dir": "docs", "files": ["README.md"]}]
TASK_7 = "Write pytest tests for game logic (movement, collision, score calculation) in tests/test_game_logic.py"


def _workspace(root: Path) -> Path:
    for rel, text in {"src/core/snake_game.js": SNAKE_GAME_JS, "src/utils/score_manager.js": "localStorage.x = 1;\n",
                      "src/server/server.py": "import http.server\n", "index.html": "<script src='game.js'></script>\n"
                      }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    return root


# ======================================================================================== 1. a 'search' object
def test_a_search_object_is_answered_not_crashed(tmp_path):
    from src.tools.code_editor import CodeEditorTool
    (tmp_path / "t.js").write_text("\n".join(f"line {i}" for i in range(1, 40)) + "\n", encoding="utf-8")
    for chunks in ([{"replace": "x", "search": {"start_line": 14}}],
                   [{"replace": "x", "start_line": 14, "end_line": 20, "search": {"text": "line 14"}}],
                   [{"replace": {"end_line": 20, "start_line": 14}, "replace_text": "new", "search": {"x": 1}}]):
        with quiet():
            result = run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks", filename="t.js",
                                                  chunks=chunks))
        assert result["status"] == "error"
        assert result["message"] == ("Chunk 0: 'search' must be the exact text to find, as ONE string, not a dict. For a "
                                     "line range use start_line and end_line next to 'replace' instead. Nothing was "
                                     "changed.")
    assert (tmp_path / "t.js").read_text(encoding="utf-8").startswith("line 1\nline 2\n")


# ======================================================================================== 2. the Admin AI and the PM's project
class Agent:
    def __init__(self, agent_id, agent_type, state, project=None):
        self.agent_id, self.agent_type, self.state = agent_id, agent_type, state
        self.agent_config = {"config": {"project_name_context": project}} if project else {"config": {}}


def test_the_admin_ai_does_not_change_the_files_of_a_pm_project(tmp_path):
    from src.tools.project_management import admin_project_change_error, project_pm_of
    from src.tools.executor import ToolExecutor
    admin, pm1, w1 = Agent("admin_ai", "admin", "work"), Agent("PM1", "pm", "pm_manage", PROJECT), Agent("W1", "worker",
                                                                                                       "worker_work")
    manager = types.SimpleNamespace(agents={"admin_ai": admin, "PM1": pm1, "W1": w1})
    assert project_pm_of(manager, PROJECT) == "PM1" and project_pm_of(manager, "Other_Project") is None
    message = admin_project_change_error(manager, "admin_ai", PROJECT, "WiFi_Snake_Game/server.py")
    assert message == ("'WiFi_Snake_Game/server.py' was NOT changed: the project 'WiFi_Snake_Game' is built by PM1 and its "
                       "team, so the Admin AI does not change its files - a second version beside the team's files breaks "
                       "the project. Tell PM1 what should change with send_message (the PM makes it a task for a worker), "
                       "or tell the user what you found.")
    write = {"action": "write", "scope": "shared", "filename": "WiFi_Snake_Game/game.js", "content": "x"}
    with quiet():
        assert ToolExecutor._admin_project_change_message("file_system", write, "admin_ai", PROJECT, manager) is not None
        assert ToolExecutor._admin_project_change_message("file_system", {"action": "read", "filename": "index.html"},
                                                          "admin_ai", PROJECT, manager) is None
        assert ToolExecutor._admin_project_change_message("file_system", dict(write, scope="private"), "admin_ai",
                                                          PROJECT, manager) is None
    # workers, other projects and a project without a PM are not judged
    assert admin_project_change_error(manager, "W1", PROJECT, "game.js") is None
    assert admin_project_change_error(manager, "admin_ai", "Other_Project", "game.js") is None
    assert admin_project_change_error(types.SimpleNamespace(agents={"admin_ai": admin}), "admin_ai", PROJECT,
                                      "game.js") is None


# ======================================================================================== 3. no Python copy of JavaScript
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


@needs_tw
def test_a_python_copy_of_a_javascript_module_is_not_created(tasks):
    from src.tools.project_management import javascript_twin_creation_error
    from src.tools.executor import ToolExecutor
    workspace = _workspace(tasks.workspace)
    manager = types.SimpleNamespace(agents={"W3": Agent("W3", "worker", "worker_work")}, current_project=PROJECT,
                                    current_session=SESSION)
    message = javascript_twin_creation_error(manager, "W3", PROJECT, SESSION, "src/core/snake_game.py", workspace)
    assert message.startswith("'src/core/snake_game.py' was NOT created: src/core/snake_game.js already holds this module "
                              "in JavaScript. A Python copy is a second implementation that the project never runs - "
                              "testing it proves nothing about the real code, and the two copies drift apart. JavaScript is "
                              "tested with a JavaScript test run by Node.js (e.g. tests/snake_game.test.js with `const "
                              "snake_game = require('../src/core/snake_game.js');`, run with `node --test tests/snake_game.test.js`)")
    assert message.endswith("pytest tests only the project's own Python files. If a task really needs this Python file, the "
                            "PM names it in a task.")
    write = {"action": "write", "filename": "src/core/snake_game.py", "content": "def move_player(p):\n    return p\n"}
    with quiet():
        assert ToolExecutor._javascript_twin_message("file_system", write, "W3", PROJECT, SESSION, manager) == message
        # changing an existing file is not creating a copy
        (workspace / "src" / "utils" / "score_manager.py").write_text("x = 1\n", encoding="utf-8")
        assert ToolExecutor._javascript_twin_message("file_system", dict(write, filename="src/utils/score_manager.py"),
                                                     "W3", PROJECT, SESSION, manager) is None
    # no JavaScript twin, a test file, the framework: not judged
    assert javascript_twin_creation_error(manager, "W3", PROJECT, SESSION, "src/core/rules.py", workspace) is None
    (workspace / "tests" / "test_snake_game.js").write_text("test('x', () => {});\n", encoding="utf-8")
    assert javascript_twin_creation_error(manager, "W3", PROJECT, SESSION, "tests/test_snake_game.py", workspace) is None
    assert javascript_twin_creation_error(manager, "framework", PROJECT, SESSION, "src/core/snake_game.py",
                                          workspace) is None
    # a task that names the Python file: planned work
    added = tasks("framework", manager, action="add_task", project_filter=PROJECT,
                  description="Port the scoring rules to src/core/snake_game.py for the server")
    assert added["status"] == "success"
    assert javascript_twin_creation_error(manager, "W3", PROJECT, SESSION, "src/core/snake_game.py", workspace) is None


def test_a_python_test_of_helpers_in_the_tests_folder_tests_a_copy(tmp_path):
    from src.tools.project_links_check import link_notes_text, test_target_problem
    root = _workspace(tmp_path)
    (root / "tests" / "mock_game_logic.py").write_text(
        '"""Mock implementations for JavaScript functions - Python tests cannot import JS files"""\n\n\n'
        "def movePlayer(player):\n    return player\n", encoding="utf-8")
    test = "import pytest\nfrom mock_game_logic import movePlayer\n\n\ndef test_move():\n    assert movePlayer({}) == {}\n"
    problem = test_target_problem(root, "tests/test_game_logic.py", test)
    assert problem == ("tests/test_game_logic.py imports only modules inside the tests folder (tests/mock_game_logic.py), "
                       "not the project's code, so its tests run against a copy (e.g. mock versions of the real "
                       "functions) and prove nothing about the project. JavaScript is tested with a JavaScript test run "
                       "by Node.js (e.g. tests/snake_game.test.js with `const snake_game = "
                       "require('../src/core/snake_game.js');`, run with `node --test tests/snake_game.test.js`); src/core/snake_game.js "
                       "exports nothing yet, so it needs e.g. `if (typeof module !== 'undefined') module.exports = { ... "
                       "};` at its end (the browser ignores that line); delete the copied code.")
    assert problem in link_notes_text(root, "tests/test_game_logic.py", test)
    # a test of the project's Python code, or an import that is a package, is not judged
    assert test_target_problem(root, "tests/test_server.py",
                               "from src.server import server\n\n\ndef test_x():\n    assert server\n") is None
    assert test_target_problem(root, "tests/test_game_logic.py",
                               "import requests\nfrom mock_game_logic import movePlayer\n\n\ndef test_x():\n    assert 1\n"
                               ) is None
    # a Python project: the advice is to import the code under test
    py_root = tmp_path / "py"
    (py_root / "src").mkdir(parents=True)
    (py_root / "tests").mkdir()
    (py_root / "src" / "parser.py").write_text("def parse(x):\n    return x\n", encoding="utf-8")
    (py_root / "tests" / "fakes.py").write_text("def parse(x):\n    return x\n", encoding="utf-8")
    assert test_target_problem(py_root, "tests/test_parser.py",
                               "from fakes import parse\n\n\ndef test_parse():\n    assert parse(1) == 1\n").endswith(
        "Import the code under test from the project (e.g. `from my_module import my_function`); delete the copied code.")


def test_what_a_test_is_about():
    from src.tools.project_links_check import name_tokens, subject_in_javascript
    assert name_tokens("tests/test_game_logic.py") == {"game", "logic"}
    assert name_tokens("src/core/snakeGame.js") == {"snake", "game"}
    assert name_tokens("tests/game_logic.test.js") == {"game", "logic"}
    files = ["src/core/snake_game.js", "src/utils/score_manager.js", "src/server/server.py", "tests/mock_game_logic.py"]
    assert subject_in_javascript("tests/test_game_logic.py", files) == "src/core/snake_game.js"
    assert subject_in_javascript("tests/test_score_manager.py", files) == "src/utils/score_manager.js"
    assert subject_in_javascript("tests/test_server.py", files) is None                    # the Python code
    assert subject_in_javascript("tests/test_misc.py", files) is None                      # no word in common
    assert subject_in_javascript("tests/test_app.py", ["src/game.js"]) == "src/game.js"    # JavaScript only
    assert subject_in_javascript("tests/test_app.py", ["src/app.py"]) is None


# ======================================================================================== 4. the kick-off plan
def test_python_tests_of_javascript_code_are_planned_as_javascript_tests():
    from src.workflows.pm_kickoff_workflow import PMKickoffWorkflow, javascript_test_renames, renamed_task_text
    renames, planned = PMKickoffWorkflow._javascript_test_plan(STRUCTURE, [TASK_7])
    assert renames == {"tests/test_game_logic.py": "tests/game_logic.test.js"}
    assert renamed_task_text(TASK_7, renames, planned) == (
        "Write pytest tests for game logic (movement, collision, score calculation) in tests/game_logic.test.js [Framework "
        "note: the code these tests check is JavaScript (src/core/snake_game.js), so they are JavaScript tests "
        "(tests/game_logic.test.js, run with `node --test tests/*.test.js`), not pytest tests; pytest tests only the Python files "
        "(src/server/server.py). Never write a Python copy of the JavaScript code.]")
    assert renamed_task_text("Write docs/README.md", renames, planned) == "Write docs/README.md"
    # Python tests of Python code stay; a JavaScript-only project's Python tests all become JavaScript tests
    assert javascript_test_renames(["src/server/server.py", "src/game.js", "tests/test_server.py"]) == {}
    assert javascript_test_renames(["src/game.js", "src/player.js", "tests/test_game.py", "tests/conftest.py"]) == {
        "tests/test_game.py": "tests/game.test.js"}


def test_the_kickoff_of_09_29_08_writes_javascript_tests(tmp_path, monkeypatch):
    import src.tools.project_management as pmt
    import src.workflows.pm_kickoff_workflow as pkw
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    monkeypatch.setattr(pmt, "TASKLIB_AVAILABLE", False)
    ws = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    ws.mkdir(parents=True)
    calls = []

    class Executor:
        async def execute_tool(self, **kwargs):
            calls.append(kwargs["tool_args"])
            return {"status": "success", "task_id": len(calls), "task_uuid": f"00000000-0000-0000-0000-{len(calls):012d}"}

    class PlainResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    monkeypatch.setattr(pkw, "WorkflowResult", PlainResult)

    class PM:
        agent_id, sandbox_path, message_history = "PM1", tmp_path, []
        agent_config = {"config": {"project_name_context": PROJECT}}

        def clear_history(self):
            self.message_history = []
    plan = {"roles": ["Coder", "QA_Testing"],
            "tasks": [{"id": "task_1", "description": "Implement the core game engine in src/core/snake_game.js"},
                      {"id": "task_6", "description": "Create simple Python HTTP server script in src/server/server.py"},
                      {"id": "task_7", "depends_on": ["task_1"], "description": TASK_7}],
            "code_base_definitions": "Tech Stack: HTML5, JavaScript (vanilla), Python (http.server), pytest for testing.",
            "project_structure": STRUCTURE}
    manager = types.SimpleNamespace(current_project=PROJECT, current_session=SESSION, tool_executor=Executor())
    with quiet():
        result = run(pkw.PMKickoffWorkflow().execute(manager, PM(), plan))
    assert result.success
    task_7 = next(c for c in calls if c.get("description", "").startswith("Kick-off Task 3:"))
    assert "in tests/game_logic.test.js [Framework note: the code these tests check is JavaScript" in task_7["description"]
    assert "tests/test_game_logic.py" not in task_7["description"]
    md = (ws / "PROJECT_STRUCTURE.md").read_text(encoding="utf-8")
    assert "test_game_logic.py" not in md and "game_logic.test.js" in md and pkw.MIXED_TEST_LANGUAGE_NOTE in md
    assert (ws / "conftest.py").exists()                                       # the server is Python


# ======================================================================================== 5. a forbidden call in report state
def _cycle(state):
    """The real AgentCycleHandler.run_cycle for a worker whose history holds a successful read of tests/test_game_logic.py,
    replying with the same read again (2026-10-26 10:24:26-10:25:13)."""
    from unittest.mock import AsyncMock, MagicMock
    from src.agents.constants import AGENT_TYPE_WORKER, AGENT_STATUS_IDLE
    from src.agents.core import Agent as CoreAgent
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager
    manager = MagicMock(spec=AgentManager)
    manager.db_manager, manager.performance_tracker, manager.send_to_ui = AsyncMock(), AsyncMock(), AsyncMock()
    manager.schedule_cycle = AsyncMock(return_value="task")
    manager.current_project, manager.current_session, manager.current_session_db_id = None, "s", None
    manager._agent_cycle_locks, manager.agents = {}, {}
    manager.state_manager = MagicMock()
    manager.state_manager.get_agent_team.return_value = None
    manager.workflow_manager = MagicMock()
    manager.workflow_manager.get_system_prompt.return_value = "worker prompt"
    agent = MagicMock(spec=CoreAgent)
    agent.agent_id, agent.agent_type, agent.state, agent.status = "W3", AGENT_TYPE_WORKER, state, AGENT_STATUS_IDLE
    agent.llm_provider, agent.provider_kwargs, agent.max_tokens = None, {}, None
    agent.provider_name, agent.model, agent.persona = "ollama-local", "qwen3.5:4b-q4_K_M", "QA Engineer"
    read = {"action": "read", "filename": "tests/test_game_logic.py"}
    agent.message_history = [{"role": "system", "content": "worker prompt"},
                             {"role": "assistant", "content": "", "tool_calls": [{"id": "r1", "name": "file_system",
                                                                                  "arguments": dict(read)}]},
                             {"role": "tool", "tool_call_id": "r1", "name": "file_system",
                              "content": "1: import pytest\n2: from mock_game_logic import movePlayer"}]
    agent._failover_state, agent._failed_models_this_cycle = {}, set()
    agent._last_reply_cut_off = agent._think_off_next_call = agent._context_window_full = False
    agent._empty_response_retry_count = 0
    agent.needs_priority_recheck = False
    agent.active_task_id = None
    agent.set_state = MagicMock(side_effect=lambda new_state: setattr(agent, "state", new_state))
    interaction_handler = MagicMock()
    interaction_handler.execute_single_tool = AsyncMock(return_value=None)

    async def events(*_args, **_kwargs):
        yield {"type": "tool_requests", "content_for_history": "",
               "calls": [{"id": "r2", "name": "file_system", "arguments": dict(read)}]}
    agent.process_message = MagicMock(side_effect=events)
    with quiet():
        asyncio.run(AgentCycleHandler(manager, interaction_handler).run_cycle(agent, 0))
    return interaction_handler.execute_single_tool


def test_a_call_the_state_forbids_is_not_answered_from_the_duplicate_cache():
    # worker_work: the same read again is answered from the earlier result (unchanged)
    assert _cycle("worker_work").call_count == 0
    # worker_report: file_system is not allowed there - the call reaches the executor, which refuses it
    executed = _cycle("worker_report")
    assert executed.call_count == 1 and "file_system" in str(executed.call_args)


# END OF FILE tests/unit/test_round3s.py
