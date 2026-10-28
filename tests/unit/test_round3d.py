# START OF FILE tests/unit/test_round3d.py
"""
Round 3d: the run of 2026-10-23 11:37-15:12 (qwen3.5:4b-q4_K_M for every agent, Multiplayer_Snake_Game). The project never
finished: W1 spent 2.5 hours on "Fix failing unit tests in tests/test_gameLogic.js".

* 47 of the 79 failed code_editor calls of W1 and W2 wrote the location INSIDE 'replace'
  ([{"replace": {"start_line": 46, "end_line": 182}, "replace_text": "..."}]); as JSON text (25x) every one had one '}' too
  many or too few. 5 file_system 'replace_lines' calls sent a list of such edits.
* tests/test_gameLogic.js tested its own copy of the game logic; src/game/gameLogic.js exported nothing; server.js required
  'sqlite3', which package.json did not list.
* PM1 gave the finished Kick-off Task 1 to W2 again (12:46), rewrote it into "Create project documentation" (13:11),
  re-created Tasks 1 and 2 (12:17, duplicate ratio 0.79 < 0.80) and made three "Update Project Documentation" tasks for the
  idle W2; the pm_audit prompt told it to run pytest on a JavaScript project.
* After the first summary every condensation was a hard truncation (139 skipped summaries, 32 truncations).
* A re-run of the tests after an edit got the cached earlier output.
* W1 was forced into worker_report 18 times on the same task; nobody changed the task.
"""
import asyncio
import json
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

REPO = Path(__file__).resolve().parents[2]
PROJECT, SESSION = "Multiplayer_Snake_Game", "startup_1792726646"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

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


PY_FILE = "x = 1\ny = 2\nz = 3\n"


def _edit(tmp_path, chunks, filename="src/m.py", content=PY_FILE):
    from src.tools.code_editor import CodeEditorTool
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    with quiet():
        result = run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_chunks", filename=filename,
                                              chunks=chunks))
    return result, path.read_text(encoding="utf-8")


# ======================================================================================== 1. code_editor chunk shapes
def test_rebalance_brackets_repairs_the_run_s_strings_and_keeps_valid_json():
    from src.tools.code_editor import _rebalance_brackets
    # 11:45:52 W1: one '}' too many at the end
    extra = '[{"replace": {"start_line": 125, "end_line": 131}, "replace_text": "  }\\n  });"}}]'
    assert json.loads(_rebalance_brackets(extra)) == [
        {"replace": {"start_line": 125, "end_line": 131}, "replace_text": "  }\n  });"}]
    # 12:19:07 W2: the '}' that closes a chunk is missing before the next one
    missing = ('[{"replace": {"start_line": 58, "end_line": 60, "search": "function a() {}"}, '
               '{"replace": {"start_line": 149, "end_line": 156, "search": "b"}}]')
    assert json.loads(_rebalance_brackets(missing)) == [
        {"replace": {"start_line": 58, "end_line": 60, "search": "function a() {}"}},
        {"replace": {"start_line": 149, "end_line": 156, "search": "b"}}]
    # 12:28:31 W2: the chunk was closed too early, a member name follows
    early = '[{"replace": {"start_line": 133, "end_line": 148}, "search": "return false;"}, "replace_text": "return true;"}]'
    assert json.loads(_rebalance_brackets(early)) == [
        {"replace": {"start_line": 133, "end_line": 148}, "search": "return false;", "replace_text": "return true;"}]
    for valid in ('[{"search": "a}", "replace": "b]"}]', '{"a": [1, 2, {"b": "}"}]}', "[]", '[{"replace": "x"}]'):
        assert json.loads(_rebalance_brackets(valid)) == json.loads(valid)
    assert _rebalance_brackets('[{"search": "never closed') == '[{"search": "never closed'   # an open string: unchanged


def test_unnest_replace_object():
    from src.tools.code_editor import unnest_replace_object
    assert unnest_replace_object({"search": "a", "replace": "b"}) is None
    assert unnest_replace_object({"replace": {"end_line": 182, "start_line": 46}, "replace_text": "new"}) == {
        "start_line": 46, "end_line": 182, "replace": "new"}
    assert unnest_replace_object({"replace": {"replace_text": "new", "search": "old"}}) == {"search": "old", "replace": "new"}
    # keys beside the object win
    assert unnest_replace_object({"replace": {"start_line": 1, "end_line": 2}, "start_line": 5, "end_line": 6,
                                  "replace_text": "n"}) == {"start_line": 5, "end_line": 6, "replace": "n"}
    assert unnest_replace_object({"replace": {"start_line": 1, "end_line": 2}, "search": "x"}) == {
        "search": "x", "start_line": 1, "end_line": 2}


def test_location_inside_replace_is_applied(tmp_path):
    # 11:45:30 W1: "Chunk 0 is malformed: Provide either 'start_line'/'end_line' OR 'search'."
    result, text = _edit(tmp_path, [{"replace": {"end_line": 2, "start_line": 2}, "replace_text": "y = 20"}])
    assert result["status"] == "success", result
    assert text == "x = 1\ny = 20\nz = 3\n"
    assert "had the line numbers / search text INSIDE a 'replace' object" in result["message"]


def test_location_inside_replace_with_search_no_longer_crashes(tmp_path):
    # 12:24:28 W2: "Unhandled error during edit: replace() argument 2 must be str, not dict"
    result, text = _edit(tmp_path, [{"replace": {"end_line": 3, "start_line": 3}, "search": "z = 3", "replace_text": "z = 30"}])
    assert result["status"] == "success", result
    assert text == "x = 1\ny = 2\nz = 30\n"


def test_chunks_string_with_a_bracket_too_many_is_applied(tmp_path):
    chunks = '[{"replace": {"start_line": 1, "end_line": 1}, "replace_text": "x = 10"}}]'
    result, text = _edit(tmp_path, chunks)
    assert result["status"] == "success", result
    assert text == "x = 10\ny = 2\nz = 3\n"
    assert "had a '}' too many or too few" in result["message"]


def test_chunks_string_with_a_missing_bracket_between_chunks_is_applied(tmp_path):
    chunks = ('[{"replace": {"search": "x = 1", "replace_text": "x = 11"}, '
              '{"replace": {"search": "z = 3", "replace_text": "z = 33"}}]')
    result, text = _edit(tmp_path, chunks)
    assert result["status"] == "success", result
    assert text == "x = 11\ny = 2\nz = 33\n"


def test_a_chunk_without_new_text_and_a_non_text_replace_are_explained(tmp_path):
    # 14:49:05 W1: lines and a search text inside / beside 'replace', but no new text
    result, text = _edit(tmp_path, '[{"replace": {"start_line": 2, "end_line": 2}, "search": "y = 2"}}]')
    assert result["status"] == "error" and "gives lines 2-2 but NO new text" in result["message"]
    assert text == PY_FILE
    result, text = _edit(tmp_path, [{"search": "y = 2", "replace": ["y = 3"]}])
    assert result["status"] == "error" and "'replace' must be the new text as ONE string, not a list" in result["message"]
    assert text == PY_FILE


def test_normal_chunks_are_unchanged(tmp_path):
    result, text = _edit(tmp_path, [{"search": "y = 2", "replace": "y = 22"}, {"start_line": 1, "end_line": 1, "replace": "x = 0"}])
    assert result["status"] == "success" and text == "x = 0\ny = 22\nz = 3\n"
    assert "INSIDE a 'replace' object" not in result["message"] and "too many or too few" not in result["message"]


# ======================================================================================== 2. file_system replace_lines
def _fs(tmp_path, **args):
    from src.tools.file_system import FileSystemTool
    with quiet():
        return run(FileSystemTool().execute("W2", tmp_path, scope="private", **args))


def test_replace_lines_with_a_list_of_line_edits(tmp_path):
    # 12:31:37 W2: 'replace_lines': '[{"start_line": 73, "end_line": 110, "replace_text": "..."}]'
    (tmp_path / "m.py").write_text(PY_FILE, encoding="utf-8")
    result = _fs(tmp_path, action="replace_lines>", filename="m.py",
                 replace_lines='[{"start_line": 2, "end_line": 2, "replace_text": "y = 5"}]')
    assert result["status"] == "success", result
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "x = 1\ny = 5\nz = 3\n"
    assert result["message"].startswith("[Note: your 'replace_lines' held a list of line edits")
    # 14:19:44 W1: the same with 'replace' as the text
    result = _fs(tmp_path, action="replace_lines", filename="m.py",
                 replace_lines='[{"end_line": 3, "replace": "z = 9", "start_line": 3}]')
    assert result["status"] == "success" and (tmp_path / "m.py").read_text(encoding="utf-8") == "x = 1\ny = 5\nz = 9\n"


def test_replace_lines_text_given_as_replace_lines(tmp_path):
    # 15:03:22 W1: "'content' parameter is required for 'replace_lines'"
    (tmp_path / "m.py").write_text(PY_FILE, encoding="utf-8")
    result = _fs(tmp_path, action="replace_lines", filename="m.py", replace_lines="y = 7", replace_start_line=2,
                 replace_end_line=2)
    assert result["status"] == "success", result
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "x = 1\ny = 7\nz = 3\n"


def test_replace_lines_without_numbers_or_edits_is_still_refused(tmp_path):
    (tmp_path / "m.py").write_text(PY_FILE, encoding="utf-8")
    result = _fs(tmp_path, action="replace_lines", filename="m.py", content="y = 7")
    assert result["status"] == "error" and "'replace_start_line' and 'replace_end_line'" in result["message"]


# ======================================================================================== 3. finished tasks
class Worker:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id


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


TASK_1 = ("Kick-off Task 1: Set up Express server with Socket.io for real-time multiplayer communication, implement player "
          "registration/login, score persistence, and leaderboard logic in src/server/server.js")
TASK_2 = ("Kick-off Task 2: Build HTML/CSS/JavaScript game interface with player selection, controls, game board rendering, "
          "and score display in src/frontend/index.html")


def _finished(call, manager, text):
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT)
    assert added["status"] == "success", added
    done = call("framework", manager, action="modify_task", task_id=added["task_uuid"], task_progress="finished")
    assert done["status"] == "success", done
    return added["task_uuid"]


@needs_tw
def test_a_finished_task_is_not_given_out_again_or_rewritten(tasks):
    pm1, w1, w2 = Worker("PM1", "pm", "pm_manage"), Worker("W1"), Worker("W2")
    manager = _manager(pm1, w1, w2)
    t1 = _finished(tasks, manager, TASK_1)
    # 12:46:36 PM1 gave the finished Task 1 to W2
    again = tasks("PM1", manager, action="modify_task", task_id=t1, assignee_agent_id="W2")
    assert again["status"] == "error" and again["error_type"] == "finished_task"
    assert "is FINISHED ('Kick-off Task 1: Set up Express server" in again["message"]
    assert "not given to a worker again" in again["message"] and "action 'add_task'" in again["message"]
    # 13:11:24 PM1 rewrote it into documentation work
    rewrite = tasks("PM1", manager, action="modify_task", task_id=t1, assignee_agent_id="W2", description=(
        "Create project documentation: README.md updates, PROJECT_STRUCTURE.md, and initial docs/ folder content"))
    assert rewrite["status"] == "error" and "not rewritten into other work" in rewrite["message"]
    rewrite = tasks("PM1", manager, action="modify_task", task_id=t1, task_progress="todo", description="Other work")
    assert rewrite["status"] == "error" and "not rewritten into other work" in rewrite["message"]
    # reopening the same work for a worker is allowed
    reopened = tasks("PM1", manager, action="modify_task", task_id=t1, task_progress="todo", assignee_agent_id="W2")
    assert reopened["status"] == "success", reopened
    assert reopened["assignee"] == "W2" and reopened["task_progress"] == "todo"


@needs_tw
def test_finished_task_changes_that_stay_allowed(tasks):
    pm1, w1 = Worker("PM1", "pm", "pm_manage"), Worker("W1")
    manager = _manager(pm1, w1)
    t1 = _finished(tasks, manager, TASK_1)
    assert tasks("PM1", manager, action="modify_task", task_id=t1, tags="verified")["status"] == "success"
    assert tasks("PM1", manager, action="modify_task", task_id=t1, description=TASK_1)["status"] == "success"  # unchanged text
    assert tasks("framework", manager, action="modify_task", task_id=t1, assignee_agent_id="W1")["status"] == "success"


@needs_tw
def test_a_finished_task_with_a_new_title_is_still_a_duplicate(tasks):
    pm1, w1, w2 = Worker("PM1", "pm", "pm_manage"), Worker("W1"), Worker("W2")
    manager = _manager(pm1, w1, w2)
    _finished(tasks, manager, TASK_1)
    _finished(tasks, manager, TASK_2)
    # 12:17:01 and 12:18:06 (0.79 with the title)
    server = tasks("PM1", manager, action="add_task", project_filter=PROJECT, title="Backend server setup with Socket.io",
                   description=("Implement Express server with Socket.io for real-time multiplayer communication, player "
                                "registration/login, score persistence, and leaderboard logic"))
    assert server["status"] == "error" and server["error_type"] == "finished_duplicate", server
    ui = tasks("PM1", manager, action="add_task", project_filter=PROJECT, title="Frontend game UI implementation",
               description=("Build HTML/CSS/JavaScript game interface with player selection, controls, game board rendering, "
                            "and score display"))
    assert ui["status"] == "error" and ui["error_type"] == "finished_duplicate"
    # a task that names a problem with finished work is new work
    fix = tasks("PM1", manager, action="add_task", project_filter=PROJECT, title="Fix the server start",
                description="Add sqlite3 to the dependencies in package.json so src/server/server.js starts")
    assert fix["status"] == "success", fix


def test_task_is_finished():
    from src.tools.project_management import task_is_finished
    assert task_is_finished({"status": "completed"}) and task_is_finished({"status": "pending", "task_progress": "finished"})
    assert not task_is_finished({"status": "pending", "task_progress": "doing"})
    assert not task_is_finished({"status": "pending", "task_progress": "decomposed"})


# ======================================================================================== 4. project links
GAME_LOGIC = """// Multiplayer Snake Game - Game Logic
let snake = [];
const socket = io('ws://localhost:3001');
function initGame() { snake = [{ x: 10, y: 10 }]; }
function updateGame() { return 0; }
document.getElementById('start-btn').addEventListener('click', initGame);
"""
COPY_TEST = """const assert = require('assert');
const gameLogic = {
  snake: [],
  initGame() { this.snake = [{ x: 10, y: 10 }]; },
};
function test(name, fn) { fn(); }
test('initGame places the snake', () => { gameLogic.initGame(); assert.strictEqual(gameLogic.snake.length, 1); });
"""
SERVER = """const express = require('express');
const { Server } = require('socket.io');
const sqlite3 = require('sqlite3').verbose();
const path = require('path');
const fs = require('node:fs');
module.exports = { express };
"""
PACKAGE = '{"name": "multiplayer-snake-game", "dependencies": {"socket.io": "^4.7.2", "express": "^4.18.2"}}'


def _workspace(tmp_path, files):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_a_javascript_test_of_a_copy_is_found(tmp_path):
    from src.tools.project_links_check import test_target_problem
    ws = _workspace(tmp_path, {"src/game/gameLogic.js": GAME_LOGIC, "tests/test_gameLogic.js": COPY_TEST})
    problem = test_target_problem(ws, "tests/test_gameLogic.js")
    assert problem.startswith("tests/test_gameLogic.js loads NO project file")
    assert "`const gameLogic = require('../src/game/gameLogic.js');`" in problem
    # loading the project file (even one that does not exist yet) is not a copy
    assert test_target_problem(ws, "tests/test_gameLogic.js",
                               "const g = require('../src/game/gameLogic.js');\ntest('x', () => {});") is None
    assert test_target_problem(ws, "tests/test_gameLogic.js", "import { initGame } from '../src/game/new.js';\nit('x');") is None
    # an integration test that starts the program by path
    assert test_target_problem(ws, "tests/test_gameLogic.js",
                               "const { spawn } = require('child_process');\nspawn('node', ['src/game/gameLogic.js']);\n"
                               "test('runs', () => {});") is None
    # not a test file / no test code / nothing to test yet
    assert test_target_problem(ws, "src/game/gameLogic.js") is None
    assert test_target_problem(ws, "tests/helpers.js", "const x = 1;") is None
    assert test_target_problem(_workspace(tmp_path / "empty", {"tests/test_a.js": COPY_TEST}), "tests/test_a.js") is None


def test_a_python_test_of_a_copy_is_found(tmp_path):
    from src.tools.project_links_check import test_target_problem
    ws = _workspace(tmp_path, {"src/converter.py": "def c(x):\n    return x\n",
                               "tests/test_converter.py": "import math\n\ndef c(x):\n    return x\n\ndef test_c():\n    assert c(1) == 1\n"})
    problem = test_target_problem(ws, "tests/test_converter.py")
    assert problem.startswith("tests/test_converter.py imports NO project module") and "`from converter import ...`" in problem
    assert test_target_problem(ws, "tests/test_converter.py", "from converter import c\n\ndef test_c():\n    assert c(1) == 1\n") is None
    # a package import may be the project or a library: not judged
    assert test_target_problem(ws, "tests/test_converter.py", "import requests\n\ndef test_c():\n    assert 1\n") is None


def test_exports_and_dependencies(tmp_path):
    from src.tools.project_links_check import export_problems, missing_dependency_problems
    ws = _workspace(tmp_path, {"src/game/gameLogic.js": GAME_LOGIC, "src/server/server.js": SERVER, "package.json": PACKAGE})
    problems = export_problems(ws, "tests/test_gameLogic.js", "const gameLogic = require('../src/game/gameLogic.js');\n")
    assert len(problems) == 1 and problems[0].startswith("src/game/gameLogic.js exports NOTHING")
    assert export_problems(ws, "tests/test_server.js", "const { express } = require('../src/server/server.js');\n") == []
    assert export_problems(ws, "src/main.js", "require('./game/gameLogic.js');\n") == []   # loaded for its side effects
    missing = missing_dependency_problems(ws, "src/server/server.js")
    assert len(missing) == 1 and missing[0].startswith("src/server/server.js requires the package 'sqlite3', which package.json "
                                                       "does NOT list")
    # Round 4a: without a package.json, the npm packages a file REQUIRES and that are not installed are named too
    plain = _workspace(tmp_path / "plain", {"a.js": SERVER})
    unlisted = missing_dependency_problems(plain, "a.js")
    assert [p.split("'")[1] for p in unlisted] == ["express", "socket.io", "sqlite3"]
    assert all("the project has NO package.json" in p for p in unlisted)
    (plain / "node_modules" / "express").mkdir(parents=True)                    # installed: fine
    assert [p.split("'")[1] for p in missing_dependency_problems(plain, "a.js")] == ["socket.io", "sqlite3"]
    assert missing_dependency_problems(_workspace(tmp_path / "page", {"b.js": "import x from 'three';\n"}), "b.js") == []


def test_completion_check_and_write_note(tmp_path):
    from src.utils.task_files import task_completion_problems
    ws = _workspace(tmp_path, {"src/game/gameLogic.js": GAME_LOGIC, "tests/test_gameLogic.js": COPY_TEST,
                               "src/server/server.js": SERVER, "package.json": PACKAGE})
    problems = task_completion_problems("Kick-off Task 6: Write automated tests in tests/test_server.js and "
                                        "tests/test_gameLogic.js", ws)
    assert any(p.startswith("tests/test_gameLogic.js loads NO project file") for p in problems)
    problems = task_completion_problems("Kick-off Task 1: Set up Express in src/server/server.js", ws)
    assert any("requires the package 'sqlite3'" in p for p in problems)
    written = _fs(ws, action="write", filename="tests/test_copy.js", content=COPY_TEST)
    assert written["status"] == "success" and "loads NO project file" in written["message"]
    assert "a task with this problem cannot be marked finished" in written["message"]


# ======================================================================================== 5. rolling summaries
def _history(extra_after_summary):
    history = [{"role": "system", "content": "worker_work prompt"},
               {"role": "system", "content": "[CONTEXT SUMMARY 1/2 - t]\n\nold 1"},
               {"role": "system", "content": "[CONTEXT SUMMARY 2/2 - t]\n\nold 2"}]
    for n in range(extra_after_summary):
        history.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{n}", "name": "file_system", "arguments": {"action": "read", "path": "tests/test_gameLogic.js"}}]}
            if n % 2 == 0 else {"role": "tool", "tool_call_id": f"c{n - 1}", "name": "file_system", "content": "1: x"})
    return history


def test_a_new_summary_is_made_once_enough_new_messages_came_in():
    from src.agents.cycle_components.context_summarizer import ContextSummarizer, new_summary_would_help
    assert new_summary_would_help([{"role": "user", "content": "x"}])
    assert not new_summary_would_help(_history(6)) and not new_summary_would_help(_history(11))
    assert new_summary_would_help(_history(12))
    summarizer = ContextSummarizer(types.SimpleNamespace(agents={}))
    with quiet():
        assert run(summarizer.should_summarize_context("W1", 12000, 4096, _history(12), model_num_ctx=32768,
                                                       real_prompt_tokens=26000))
        assert not run(summarizer.should_summarize_context("W1", 12000, 4096, _history(6), model_num_ctx=32768,
                                                           real_prompt_tokens=26000))
        assert run(summarizer.should_summarize_context("W1", 12000, 4096, _history(6), model_num_ctx=32768,
                                                       real_prompt_tokens=30000))


def _summarizer_with(reply):
    from src.agents.cycle_components.context_summarizer import ContextSummarizer
    from src.agents.constants import CONSTITUTIONAL_GUARDIAN_AGENT_ID
    seen = []

    class Provider:
        async def stream_completion(self, model, messages, temperature, max_tokens=None, **kwargs):
            seen.append(messages[-1]["content"])
            yield {"type": "response_chunk", "content": reply}
    cg = types.SimpleNamespace(llm_provider=Provider(), model="m", message_history=[])
    return ContextSummarizer(types.SimpleNamespace(agents={CONSTITUTIONAL_GUARDIAN_AGENT_ID: cg})), seen


def test_rolling_summary_replaces_the_old_one_and_sees_the_tool_calls():
    summarizer, seen = _summarizer_with("W1 read the test file again.")
    with quiet():
        ok, condensed = run(summarizer.summarize_agent_context("W1", _history(14)))
    assert ok and len(seen) == 2
    assert "[called file_system(action=read, path=tests/test_gameLogic.js)]" in seen[0] + seen[1]
    assert "old 1" in seen[0] + seen[1]                                      # the older summary is carried on
    summaries = [m for m in condensed if "[CONTEXT SUMMARY" in str(m.get("content"))]
    assert len(summaries) == 2 and all("W1 read the test file again." in m["content"] for m in summaries)


def test_hard_truncation_right_after_a_summary_keeps_only_the_newest_summary():
    summarizer, seen = _summarizer_with("unused")
    history = _history(0) + [{"role": "system", "content": "[CONTEXT SUMMARY 1/2 - u]\n\nnew 1"},
                             {"role": "system", "content": "[CONTEXT SUMMARY 2/2 - u]\n\nnew 2"},
                             {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    with quiet():
        ok, condensed = run(summarizer.summarize_agent_context("W1", history))
    assert ok and seen == []
    assert [m["content"] for m in condensed] == ["worker_work prompt", "[CONTEXT SUMMARY 1/2 - u]\n\nnew 1",
                                                 "[CONTEXT SUMMARY 2/2 - u]\n\nnew 2", "a", "b"]


# ======================================================================================== 6. commands run again after changes
def test_workspace_changed_since_last_run(tmp_path):
    from src.agents.tool_activity import workspace_changed_since_last_run, workspace_fingerprint, is_command_run_call
    assert is_command_run_call("command_executor") and is_command_run_call("test_runner")
    assert not is_command_run_call("file_system")
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "run_integration_tests.sh"
    test_file.write_text("echo 1\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    agent = types.SimpleNamespace()
    sig = ("command_executor", '{"command": "bash tests/run_integration_tests.sh"}')
    assert workspace_changed_since_last_run(agent, sig, tmp_path)            # first run: no record
    assert not workspace_changed_since_last_run(agent, sig, tmp_path)        # nothing changed: a real duplicate
    (tmp_path / "node_modules" / "x.js").write_text("x", encoding="utf-8")  # dependency folders do not count
    assert not workspace_changed_since_last_run(agent, sig, tmp_path)
    test_file.write_text("echo 2 # fixed\n", encoding="utf-8")              # 12:07:31 W2 fixed the script
    assert workspace_changed_since_last_run(agent, sig, tmp_path)            # 12:07:52: runs again
    assert workspace_fingerprint(tmp_path / "missing") is None


def test_a_test_run_after_an_edit_is_not_answered_from_the_history(tmp_path):
    from src.agents.cycle_handler import AgentCycleHandler
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    workspace.mkdir(parents=True)
    (workspace / "t.sh").write_text("echo 1\n", encoding="utf-8")
    handler = AgentCycleHandler.__new__(AgentCycleHandler)
    handler._manager = types.SimpleNamespace(current_project=PROJECT, current_session=SESSION)
    args = {"action": "run_command", "command": "bash t.sh"}
    run_call = {"role": "assistant", "content": "", "tool_calls": [{"id": "r1", "name": "command_executor", "arguments": args}]}
    agent = types.SimpleNamespace(message_history=[
        {"role": "system", "content": "s"}, run_call,
        {"role": "tool", "tool_call_id": "r1", "name": "command_executor", "content": "Command execution completed with return code 1."}])
    from src.config.settings import settings
    original = settings.PROJECTS_BASE_DIR
    settings.PROJECTS_BASE_DIR = tmp_path / "projects"
    try:
        assert handler._detect_cross_cycle_duplicate_tool_call(agent, "command_executor", dict(args)) is None   # no record yet
        assert handler._detect_cross_cycle_duplicate_tool_call(agent, "command_executor", dict(args)) is not None  # unchanged
        (workspace / "t.sh").write_text("echo 22\n", encoding="utf-8")
        assert handler._detect_cross_cycle_duplicate_tool_call(agent, "command_executor", dict(args)) is None   # changed
    finally:
        settings.PROJECTS_BASE_DIR = original


# ======================================================================================== 7. repeated forced reports
def test_every_third_forced_report_on_a_task_is_escalated():
    from src.agents.task_escalation import record_forced_report, escalation_message, forced_report_count
    agent = types.SimpleNamespace(agent_id="W1", active_task_id="898a8c25", _injected_task_description=(
        "Fix failing unit tests in tests/test_gameLogic.js - the test file has a mock gameLogic"))
    assert record_forced_report(agent) is None and record_forced_report(agent) is None
    assert record_forced_report(agent) == ("898a8c25", 3)
    agent.active_task_id = "other"
    assert record_forced_report(agent) is None and forced_report_count(agent) == 1
    agent.active_task_id = "898a8c25"
    assert record_forced_report(agent) is None and forced_report_count(agent) == 4
    text = escalation_message(agent, "898a8c25", 3, "Code edit failed: 0 chunks succeeded")
    assert text.startswith("[Framework Escalation] W1 has been FORCED to stop and report 3 times on task 898a8c25 "
                           "('Fix failing unit tests in tests/test_gameLogic.js")
    assert "do NOT just tell it to keep going" in text and "a test that tests a copy of the code" in text
    assert record_forced_report(types.SimpleNamespace(agent_id="W2")) is None   # no task: not counted


def test_the_scheduler_tells_the_pm_on_the_third_forced_report():
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    sent = []

    class Handler:
        async def route_and_activate_agent_message(self, sender_id, target_identifier, message_content):
            sent.append((sender_id, target_identifier, message_content))
            return {"status": "queued"}, None
    pm = types.SimpleNamespace(agent_id="PM1", agent_type="pm")
    worker = types.SimpleNamespace(agent_id="W1", agent_type="worker", active_task_id="t1", message_history=[
        {"role": "tool", "name": "code_editor", "content": "[Tool Execution Failed] Code edit failed: 0 chunks succeeded"}])
    manager = types.SimpleNamespace(interaction_handler=Handler(), state_manager=types.SimpleNamespace(
        get_agent_team=lambda agent_id: "team_x", get_agents_in_team=lambda team_id: [pm, worker]))
    scheduler = NextStepScheduler(manager)
    with quiet():
        for _ in range(3):
            run(scheduler._note_forced_report(worker))
    assert len(sent) == 1
    sender, target, text = sent[0]
    assert sender == "constitutional_guardian_ai" and target == "PM1"
    assert "FORCED to stop and report 3 times on task t1" in text


# ======================================================================================== 8. prompts and the wake-up message
def test_prompts_name_the_project_s_test_command_and_forbid_busywork():
    import yaml
    prompts = yaml.safe_load((REPO / "prompts.yaml").read_text(encoding="utf-8"))
    audit = prompts["pm_audit_prompt"]
    assert "`npm test`" in audit and "Never run pytest on a JavaScript project." in audit and "python -m pytest -q" in audit
    manage = prompts["pm_manage_prompt"]
    assert "never invent a task just to keep a worker busy" in manage and "never give a FINISHED task out again" in manage
    work = prompts["worker_work_prompt"]
    assert "never test a copy of the code written inside the test file" in work and "`npm test` or `node --test`" in work
    for key in ("pm_audit_prompt", "pm_manage_prompt", "worker_work_prompt"):
        prompts[key].format_map(_Defaults())   # still formats (no stray braces)


class _Defaults(dict):
    def __missing__(self, key):
        return ""


def _wake_pm_for_waiting_worker(monkeypatch, hint):
    import src.agents.manager as manager_module
    from src.agents.manager import AgentManager
    sleeps = []

    async def fake_sleep(_seconds):
        sleeps.append(1)
        if len(sleeps) > 1:
            raise asyncio.CancelledError
    monkeypatch.setattr(manager_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(manager_module, "assignment_hint_for", lambda manager, agent: hint)
    pm = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_standby", status="idle", message_history=[],
                               message_inbox=[], _awaiting_project_approval=False)
    w2 = types.SimpleNamespace(agent_id="W2", agent_type="worker", state="worker_wait", status="idle", message_history=[])
    manager = object.__new__(AgentManager)
    manager.agents = {"PM1": pm}
    manager.state_manager = types.SimpleNamespace(get_agent_team=lambda agent_id: "team_x",
                                                  get_agents_in_team=lambda team_id: [pm, w2])
    manager.loop_coordinator = types.SimpleNamespace(should_intervene=lambda *a: True, record_intervention=lambda *a: None)
    states = []
    manager.workflow_manager = types.SimpleNamespace(change_state=lambda agent, state: states.append(state))

    async def schedule_cycle(agent, retry_count=0):
        return None
    manager.schedule_cycle = schedule_cycle

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await manager._periodic_pm_manage_check()
    with quiet():
        run(scenario())
    assert states == ["pm_manage"]
    return pm.message_history[-1]["content"]


def test_the_standby_wake_up_for_a_waiting_worker_asks_for_no_busywork(monkeypatch):
    text = _wake_pm_for_waiting_worker(monkeypatch, None)        # 12:45:28: W2 waits, no task is open
    assert "<project_management>" not in text
    assert 'call `project_management` with action="list_tasks" and assignee_filter="W2"' in text
    assert "do NOT create a task just to keep them busy and do NOT give out a finished task again" in text
    text = _wake_pm_for_waiting_worker(monkeypatch, "[Assignment Check] Task t5 is ready: assign it to W2.")
    assert text.endswith("[Assignment Check] Task t5 is ready: assign it to W2.")
    assert "just to keep them busy" not in text


def test_a_code_editor_edit_of_a_test_of_a_copy_gets_the_note(tmp_path):
    _workspace(tmp_path, {"src/game/gameLogic.js": GAME_LOGIC})
    chunks = '[{"replace": {"start_line": 1, "end_line": 1}, "replace_text": "const assert = require(\'assert\');"}}]'
    result, text = _edit(tmp_path, chunks, filename="tests/test_gameLogic.js", content=COPY_TEST)
    assert result["status"] == "success", result
    assert "tests/test_gameLogic.js loads NO project file" in result["message"]
