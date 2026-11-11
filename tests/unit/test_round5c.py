# START OF FILE tests/unit/test_round5c.py
"""
Round 5c: the first run of Round 5b (2026-11-11 08:28-09:39, Laptop_Multiplayer_Snake, nemotron-3.5-lightning on
OpenRouter). The plan now had the page and its script, seven of nine tasks were finished after 45 minutes - and then W1
(a fix task for public/snake.js) and W2 (the final verification) rewrote the same Snake.move() against each other for
half an hour.

* Two workers changed one file at the same time: both were allowed to (W1's task names the file; W2 once wrote it and
  its task says to fix failures).
* A repeated call was answered from the agent's own history although the OTHER worker had changed the file: W1's reads
  of public/snake.js returned its earlier, cut copy; 17 identical code_editor calls were answered "Successfully applied".
* replace_function (59 calls, 29% failed - line edits 53%, whole-file writes 49%): nine failures asked for a test block
  by its title, three for 'imports'; and every replacement of a method whose code came with a comment at column 0
  pushed the method further right (26 columns after six rounds).
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

PROJECT, SESSION = "Laptop_Multiplayer_Snake", "startup_1794356884"
needs_tw = pytest.mark.skipif(shutil.which("task") is None, reason="real Taskwarrior (task + tasklib) not installed")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


# ============================================================================================ 1. stale repeats
def test_a_file_another_agent_changed_is_not_what_the_agent_saw(tmp_path):
    from src.agents.tool_activity import file_call_path, file_changed_since_seen, remember_file_call
    (tmp_path / "public").mkdir()
    target = tmp_path / "public" / "snake.js"
    target.write_text("export const a = 1;\n")
    w1 = types.SimpleNamespace(agent_id="W1")
    read = {"action": "read", "filename": "public/snake.js"}
    assert file_call_path("file_system", read) == "public/snake.js"
    assert file_call_path("code_editor", {"action": "replace_function", "filename": "public/snake.js"}) == "public/snake.js"
    assert file_call_path("file_system", {"action": "read", "filename": "../x"}) is None
    assert file_call_path("file_system", {"action": "read", "filename": "a.js", "scope": "private"}) is None
    assert file_call_path("command_executor", {"command": "ls"}) is None
    assert not file_changed_since_seen(w1, "file_system", read, tmp_path)  # nothing known yet
    remember_file_call(w1, "file_system", read, tmp_path)
    assert not file_changed_since_seen(w1, "file_system", read, tmp_path)
    target.write_text("export const a = 2; // changed by W2\n")
    assert file_changed_since_seen(w1, "file_system", read, tmp_path)
    assert file_changed_since_seen(w1, "code_editor", {"action": "replace_function", "filename": "public/snake.js"}, tmp_path)
    remember_file_call(w1, "file_system", read, tmp_path)
    assert not file_changed_since_seen(w1, "file_system", read, tmp_path)


def _handler(tmp_path, monkeypatch):
    from src.agents.cycle_handler import AgentCycleHandler
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path)
    handler = AgentCycleHandler.__new__(AgentCycleHandler)
    handler._manager = types.SimpleNamespace(current_project=PROJECT, current_session=SESSION)
    workspace = tmp_path / PROJECT / SESSION / "shared_workspace"
    (workspace / "public").mkdir(parents=True)
    (workspace / "public" / "snake.js").write_text("export const a = 1;\n")
    return handler, workspace


def test_a_repeated_call_runs_again_when_the_file_was_changed_by_someone_else(tmp_path, monkeypatch):
    from src.agents.tool_activity import remember_file_call
    handler, workspace = _handler(tmp_path, monkeypatch)
    edit = {"action": "replace_function", "filename": "public/snake.js", "function": "move", "code": "move() {}"}
    read = {"action": "read", "filename": "public/snake.js"}
    history = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "file_system", "arguments": read}]},
        {"role": "tool", "tool_call_id": "c1", "name": "file_system", "content": "1: export const a = 1;"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "name": "code_editor", "arguments": edit}]},
        {"role": "tool", "tool_call_id": "c2", "name": "code_editor", "content": "Successfully applied 1 edits to 'public/snake.js'."},
    ]
    w1 = types.SimpleNamespace(agent_id="W1", message_history=history)
    remember_file_call(w1, "code_editor", edit, workspace)
    with quiet():
        # nobody touched the file: the same edit again is a repeat, as before
        assert handler._detect_cross_cycle_duplicate_tool_call(w1, "code_editor", edit) is not None
        (workspace / "public" / "snake.js").write_text("export const a = 2; // W2 was here\n")
        assert handler._detect_cross_cycle_duplicate_tool_call(w1, "code_editor", edit) is None
        assert handler._detect_cross_cycle_duplicate_tool_call(w1, "file_system", read) is None


def test_the_executor_remembers_what_a_file_looked_like(tmp_path, monkeypatch):
    from src.agents.tool_activity import file_changed_since_seen
    from src.config.settings import settings
    from src.tools.executor import ToolExecutor
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path)
    workspace = tmp_path / PROJECT / SESSION / "shared_workspace"
    workspace.mkdir(parents=True)
    (workspace / "a.txt").write_text("one\n")
    read = {"action": "read", "filename": "a.txt"}
    with quiet():
        executor = ToolExecutor()
        w1 = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_work", message_history=[],
                                   current_task_id=None, active_task_id=None)
        manager = types.SimpleNamespace(agents={"W1": w1}, tool_executor=executor, current_project=PROJECT,
                                        current_session=SESSION)
        run(executor.execute_tool("W1", tmp_path / "sandbox", "file_system", dict(read), project_name=PROJECT,
                                  session_name=SESSION, manager=manager))
    assert getattr(w1, "_file_seen", {}).get("a.txt") is not None
    assert not file_changed_since_seen(w1, "file_system", read, workspace)
    (workspace / "a.txt").write_text("one\ntwo\n")
    assert file_changed_since_seen(w1, "file_system", read, workspace)


# ============================================================================================ 2. one writer per file
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
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    (workspace / "public").mkdir(parents=True)
    (workspace / "public" / "snake.js").write_text("export class Snake {}\n")
    file_authorship.reset()

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    call.tmp = tmp_path
    yield call
    file_authorship.reset()


def _add(call, manager, text, assignee, progress=None):
    result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, assignee_agent_id=assignee)
    assert result["status"] == "success", result
    if progress:
        assert call("framework", manager, action="modify_task", task_id=result["task_uuid"],
                    task_progress=progress)["status"] == "success"
    return result["task_uuid"]


@needs_tw
def test_a_file_another_worker_is_changing_is_left_alone(tasks):
    import src.tools.project_management as pmt
    w1, w2 = Agent("W1", state="worker_work"), Agent("W2", state="worker_work")
    manager = _manager(Agent("PM1", "pm", "pm_manage"), w1, w2)
    task2 = _add(tasks, manager, "Kick-off Task 2: Implement core game logic in public/snake.js", "W2", "finished")
    verify = _add(tasks, manager, "Kick-off Task 7: Start the server, test the game and run the test suite to confirm all "
                                  "tests pass. Fix any failures.", "W2", "doing")
    fix = _add(tasks, manager, "Fix two bugs in public/snake.js identified by W3: the tail is not removed in move()", "W1", "doing")
    w1.current_task_id, w2.current_task_id = fix, verify
    assert task2
    with quiet():
        # W1's task names the file: it may change it
        assert pmt.foreign_file_change_error(manager, "W1", PROJECT, SESSION, "public/snake.js") is None
        # W2 wrote the file once and its task says to fix failures - but W1 is on it right now
        refusal = pmt.foreign_file_change_error(manager, "W2", PROJECT, SESSION, "public/snake.js")
    assert refusal.startswith("'public/snake.js' was NOT changed: W1 is changing it RIGHT NOW for the task 'Fix two bugs in "
                              "public/snake.js")
    assert f"Your task ({verify}) now WAITS for that task" in refusal and "request the state 'worker_wait' now" in refusal
    tw = pmt.ProjectManagementTool()._get_taskwarrior_instance(PROJECT, SESSION)
    waiting = tw.tasks.get(uuid=verify)
    assert waiting["task_progress"] == "waiting" and {str(t["uuid"]) for t in waiting["depends"]} == {fix}
    # a worker that waits is no longer "ready": it may go to worker_wait and is started again when the fix is finished
    from src.agents.assignable_work import own_open_ready_tasks
    assert own_open_ready_tasks(list(tw.tasks.pending()), "W2") == []
    # other files are not touched by this rule
    with quiet():
        assert pmt.foreign_file_change_error(manager, "W2", PROJECT, SESSION, "README.md") is None


@needs_tw
def test_the_rule_needs_a_worker_who_is_really_working(tasks):
    import src.tools.project_management as pmt
    w1, w2 = Agent("W1", state="worker_wait"), Agent("W2", state="worker_work")
    manager = _manager(Agent("PM1", "pm", "pm_manage"), w1, w2)
    _add(tasks, manager, "Kick-off Task 2: Implement core game logic in public/snake.js", "W2", "finished")
    verify = _add(tasks, manager, "Kick-off Task 7: Run the test suite to confirm all tests pass. Fix any failures.", "W2", "doing")
    fix = _add(tasks, manager, "Fix two bugs in public/snake.js", "W1")  # assigned, not started
    w2.current_task_id = verify
    with quiet():
        assert pmt.foreign_file_change_error(manager, "W2", PROJECT, SESSION, "public/snake.js") is None
        # started, but W1 waits (e.g. for a rate limit): nobody is changing the file
        tasks("framework", manager, action="modify_task", task_id=fix, task_progress="doing")
        assert pmt.foreign_file_change_error(manager, "W2", PROJECT, SESSION, "public/snake.js") is None
        w1.state = "worker_work"
        assert "RIGHT NOW" in pmt.foreign_file_change_error(manager, "W2", PROJECT, SESSION, "public/snake.js")
        # two tasks that both name the file: no rule decides between them
        both = _add(tasks, manager, "Rewrite the collision check in public/snake.js", "W2", "doing")
        w2.current_task_id = both
        assert pmt.foreign_file_change_error(manager, "W2", PROJECT, SESSION, "public/snake.js") is None


# ============================================================================================ 3. replace_function
SNAKE_JS = '''export class Snake {
  constructor() {
    this.body = [];
  }

                        /** Move the snake one step */
                          move() {
                            this.body.pop();
                            return true;
                          }

  grow() {
    this.score++;
  }
}
'''
TEST_MJS = '''import { describe, test } from 'node:test';
import { strictEqual } from 'node:assert';
import { Snake } from '../public/snake.js';

describe('Snake class', () => {
  describe('move', () => {
    test('moves right by default', () => {
      strictEqual(new Snake().move(), true);
    });

    test('wraps around grid edges horizontally', () => {
      strictEqual(1, 2);
    });
  });
});
'''


def _edit(sandbox, **kwargs):
    from src.tools.code_editor import CodeEditorTool
    with quiet():
        return run(CodeEditorTool().execute("W1", sandbox, scope="private", **kwargs))


@needs_node
def test_a_method_goes_back_to_the_column_of_its_siblings(tmp_path):
    (tmp_path / "snake.js").write_text(SNAKE_JS)
    # as the models of the run sent it: the comment at column 0, the method at column 4
    code = "/** Move the snake one step in its current direction */\n    move() {\n      this.body.pop();\n      return false;\n    }"
    for _ in range(3):
        result = _edit(tmp_path, action="replace_function", filename="snake.js", function="move", code=code)
        assert result["status"] == "success", result
        code = code.replace("return false", "return true") if "false" in code else code.replace("return true", "return false")
    text = (tmp_path / "snake.js").read_text()
    assert "  }\n\n  /** Move the snake one step in its current direction */\n  move() {\n    this.body.pop();\n" in text
    assert "    return false;\n  }\n\n  grow() {" in text


def test_reindent_measures_from_the_definition_line():
    from src.utils.symbols import home_indent, find_symbols, lookup, reindent
    assert reindent("/** doc */\n    move() {\n      x();\n    }", "  ") == "  /** doc */\n  move() {\n    x();\n  }"
    assert reindent("@property\ndef score(self):\n    return 1", "    ") == "    @property\n    def score(self):\n        return 1"
    assert reindent("# note\n        def f():\n            pass", "") == "# note\ndef f():\n    pass"
    symbols = find_symbols(SNAKE_JS, "snake.js")
    move = lookup(symbols, "move")[0]
    assert move.indent == " " * 26 and home_indent(symbols, move) == "  "


@needs_node
def test_a_test_block_is_replaced_and_added_by_its_title(tmp_path):
    from src.utils.symbols import find_symbols, lookup
    found = {s.name: (s.kind, s.start, s.end) for s in find_symbols(TEST_MJS, "t.test.mjs")}
    assert found["wraps around grid edges horizontally"] == ("test", 11, 13)
    assert found["move"] == ("test", 6, 14) and found["Snake class"] == ("test", 5, 15)
    assert lookup(find_symbols(TEST_MJS, "t.test.mjs"), "'wraps around grid edges horizontally'")[0].start == 11
    (tmp_path / "t.test.mjs").write_text(TEST_MJS)
    result = _edit(tmp_path, action="replace_function", filename="t.test.mjs", function="wraps around grid edges horizontally",
                   code="test('wraps around grid edges horizontally', () => {\n  strictEqual(2, 2);\n});")
    assert result["status"] == "success", result
    assert "'wraps around grid edges horizontally' (was lines 11-13) now holds your new code" in result["message"]
    added = _edit(tmp_path, action="replace_function", filename="t.test.mjs", function="does not grow on its own",
                  code="test('does not grow on its own', () => {\n  strictEqual(3, 3);\n});")
    assert added["status"] == "success" and "ADDED after the test 'wraps around grid edges horizontally'" in added["message"]
    text = (tmp_path / "t.test.mjs").read_text()
    assert ("    test('wraps around grid edges horizontally', () => {\n      strictEqual(2, 2);\n    });\n\n"
            "    test('does not grow on its own', () => {\n      strictEqual(3, 3);\n    });\n  });\n});\n") in text
    assert "strictEqual(1, 2)" not in text


def test_code_that_defines_nothing_is_sent_to_the_line_tools(tmp_path):
    (tmp_path / "server.py").write_text("import os\nimport json\n\n\ndef main():\n    pass\n")
    result = _edit(tmp_path, action="replace_function", filename="server.py", function="constants", code="PORT = 3000\nHOST = 'x'")
    assert result["status"] == "error"   # (Round 5e: import lines and the start block are merged / replaced)
    assert "has no function, class or method named 'constants', and your 'code' defines none either" in result["message"]
    assert "file_system action 'replace_lines'" in result["message"] and "main (lines 5-6)" in result["message"]
    assert (tmp_path / "server.py").read_text().startswith("import os\nimport json\n")
    # a real function of the file with a body that is no definition keeps its own message
    part = _edit(tmp_path, action="replace_function", filename="server.py", function="main", code="x = 1")
    assert part["status"] == "error" and "does not define 'main'" in part["message"]


def test_the_overwrite_refusal_names_replace_function(tmp_path):
    from src.tools.file_system import FileSystemTool
    source = Path(FileSystemTool.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).resolve().parents[2] / source).read_text(encoding="utf-8")
    assert "action 'replace_function' (filename, function, code) for one function, class or method" in text
    assert json.dumps({"ok": True})

# END OF FILE tests/unit/test_round5c.py
