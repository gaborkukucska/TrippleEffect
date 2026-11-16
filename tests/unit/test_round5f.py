# START OF FILE tests/unit/test_round5f.py
"""
Round 5f: the run of 2026-11-15 19:40-23:04 (WiFi_Snake_Multiplayer_Game, qwen3.5:4b-q4_K_M, 1,373 model calls).

* Two framework files with uncommitted edits did not compile (project_links_check.py, test_diagnosis.py): the task
  completion check, the kick-off test-language check and the diagnosis of failed test runs were off for the whole run.
* replace_function failed 30 of 50 times: code with several top-level things (NETWORK_CONFIG + `let` state + functions,
  `const assert = require('node:assert')` next to the file's `require('assert')`), a call statement read as a method, a
  'function' name the file does not have.
* find_replace without a filename was refused although the framework had found the one file holding the text.
* 105 calls were mark_message_read alone; answering a message now acknowledges it.
* The plan for a WiFi multiplayer browser game had no server.
"""
import asyncio
import importlib.util
import logging
import shutil
import subprocess
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

ROOT = Path(__file__).resolve().parents[2]
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="no `node` on the PATH")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def edit(tmp_path, **kwargs):
    from src.tools.code_editor import CodeEditorTool
    with quiet():
        return run(CodeEditorTool().execute("W1", tmp_path, scope="private", **kwargs))


def node_check(path: Path) -> str:
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
    return result.stderr.strip()


# ============================================================================================ 1. the source check
def test_every_framework_file_compiles():
    from src.utils.source_health import broken_sources
    assert broken_sources(ROOT) == []


def test_a_file_that_does_not_compile_stops_the_start(tmp_path, monkeypatch):
    from src.utils.source_health import broken_sources, check_sources_or_raise
    (tmp_path / "src" / "tools").mkdir(parents=True)
    (tmp_path / "src" / "tools" / "good.py").write_text("X = 1\n")
    (tmp_path / "src" / "tools" / "bad.py").write_text('x = f"sets `"type": "module"` "\n')
    problems = broken_sources(tmp_path)
    assert len(problems) == 1 and problems[0].startswith("src/tools/bad.py:1: SyntaxError")
    monkeypatch.delenv("ALLOW_BROKEN_SOURCES", raising=False)
    with quiet(), pytest.raises(RuntimeError, match="do not compile"):
        check_sources_or_raise(tmp_path)
    monkeypatch.setenv("ALLOW_BROKEN_SOURCES", "true")
    with quiet():
        assert check_sources_or_raise(tmp_path) == problems


# ============================================================================================ 2. replace_function
NETWORK_JS = """/**
 * Network Communication Module
 * Handles peer-to-peer communication
 */

const NETWORK_CONFIG = {
  WS_HOST: 'localhost',
  WS_PORT: 8080
};

// Network state
let socket = null;
let isConnecting = false;

/**
 * Initialize network connection
 */
function initNetwork() {
  socket = new WebSocket('ws://x');
}

function send(msg) {
  socket.send(msg);
}

module.exports = {
  initNetwork,
  send
};

// Network state
let socket = null;
let isConnecting = false;
"""

NETWORK_CODE = """/**
 * Network Communication Module
 * Handles peer-to-peer communication
 */

const NETWORK_CONFIG = {
  WS_HOST: 'localhost',
  WS_PORT: 9090
};

// Network state
let socket = null;
let isConnecting = false;
let peer = null;

/**
 * Initialize network connection
 */
function initNetwork() {
  socket = new WebSocket('ws://' + location.host);
  peer = 1;
}
"""


def test_a_call_statement_is_no_method_definition():
    from src.utils.symbols import defined_names, javascript_symbols
    code = "  // Test self collision\n  resetGame();\n  game.gameState.snake = [\n    { x: 5, y: 5 },\n  ];\n"
    assert defined_names(code, "tests/game/test_game.js") == []
    found = {(s.name, s.kind) for s in javascript_symbols(
        "class A {\n  constructor(x) { this.x = x; }\n  async load(a): Promise<void> {\n    return;\n  }\n  get y() {\n"
        "    return 1;\n  }\n}\nconst o = {\n  move(d) {\n    return d;\n  },\n};\n")}
    assert {("A.constructor", "method"), ("A.load", "method"), ("A.y", "method"), ("o.move", "method")} <= found


def test_top_level_items_of_javascript_and_python():
    from src.utils.symbols import top_level_items
    kinds = [(i.kind, i.name) for i in top_level_items(NETWORK_JS, "src/network.js")]
    assert kinds == [("object", "NETWORK_CONFIG"), ("variable", "socket"), ("variable", "isConnecting"),
                     ("function", "initNetwork"), ("function", "send"), ("exports", "module.exports"),
                     ("variable", "socket"), ("variable", "isConnecting")]
    py = '"""Doc."""\nimport os\n\nMAX = 5\n\n\n@dec\ndef a():\n    return 1\n\n\nif __name__ == "__main__":\n    a()\n'
    assert [(i.kind, i.name) for i in top_level_items(py, "a.py")] == [
        ("docstring", ""), ("import", ""), ("variable", "MAX"), ("function", "a"), ("start", "")]


@needs_node
def test_code_with_several_definitions_replaces_each_of_them(tmp_path):
    path = tmp_path / "src" / "network.js"
    path.parent.mkdir()
    path.write_text(NETWORK_JS)
    result = edit(tmp_path, action="replace_function", filename="src/network.js", function="NETWORK_CONFIG", code=NETWORK_CODE)
    assert result["status"] == "success", result
    message = result["message"]
    assert "your code holds 5 definition(s)" in message and "a second 'socket'" in message and "added 'peer'" in message
    assert "header comment" in message
    text = path.read_text()
    assert text.count("let socket = null;") == 1 and text.count("let isConnecting") == 1
    assert text.count("Network Communication Module") == 1 and "WS_PORT: 9090" in text and "peer = 1;" in text
    assert "function send(msg)" in text and text.rstrip().endswith("};")  # untouched code and the exports stay
    assert node_check(path) == ""


@needs_node
def test_import_lines_in_code_are_merged_and_not_declared_twice(tmp_path):
    path = tmp_path / "t.js"
    path.write_text("const assert = require('assert');\nconst { describe, it } = require('node:test');\n\n"
                    "function helper() { return 1; }\n\ndescribe('x', () => {\n  it('y', () => { assert.ok(helper()); });\n});\n")
    code = ("const assert = require('node:assert');\nconst { describe, it, beforeEach } = require('node:test');\n\n"
            "function helper() { return 2; }\nfunction other() { return 3; }\n")
    result = edit(tmp_path, action="replace_function", filename="t.js", function="helper", code=code)
    assert result["status"] == "success", result
    text = path.read_text()
    assert text.count("const assert") == 1 and "const { describe, it, beforeEach } = require('node:test');" in text
    assert "return 2;" in text and "function other()" in text and text.count("describe('x'") == 1
    assert node_check(path) == ""


def test_python_definitions_and_imports_in_one_call(tmp_path):
    path = tmp_path / "app.py"
    path.write_text('"""Doc."""\nimport os\nfrom flask import Flask\n\nMAX = 5\n\n\ndef a():\n    return 1\n\n\n'
                    'def b():\n    return 2\n\n\nif __name__ == "__main__":\n    a()\n')
    code = "from flask import jsonify\nimport json\n\nMAX = 10\n\ndef a():\n    return json.dumps(1)\n\ndef c():\n    return 3\n"
    result = edit(tmp_path, action="replace_function", filename="app.py", function="a", code=code)
    assert result["status"] == "success", result
    text = path.read_text()
    assert "from flask import Flask, jsonify" in text and "import json" in text and "MAX = 10" in text
    assert "\n\n\ndef c():\n    return 3\n\n\ndef b():" in text and text.count("def a():") == 1
    compile(text, "app.py", "exec")


def test_import_union_keeps_names_and_a_correction_still_removes_one():
    from src.utils.symbols import _union_import
    assert _union_import("from m import a", "from m import b", True) == "from m import a, b"
    assert _union_import("from m import a, Bad", "from m import a", True) is None  # a correction: the code's line wins
    assert _union_import("const { a } = require('m');", "const { b } = require('m')", False) == "const { a, b } = require('m');"
    assert _union_import("import { a } from 'm';", "import { a, b } from 'm';", False) == "import { a, b } from 'm';"


def test_code_with_new_statements_is_still_one_definition_per_call(tmp_path):
    from src.utils.symbols import multi_definition_edit
    source = "function a() {}\nfunction b() {}\n"
    assert multi_definition_edit(source, "m.js", "function a() {}\nfunction c() {}\nconsole.log(1);", "a") is None
    assert multi_definition_edit(source, "m.js", "function a() { return 1; }", "a") is None  # one definition: the old way
    assert multi_definition_edit(source, "m.js", "function a() {}\nfunction c() {}", "Game.a") is None


def test_new_definitions_go_before_the_exports(tmp_path):
    path = tmp_path / "m.js"
    path.write_text("function a() {}\n\nmodule.exports = { a };\n")
    result = edit(tmp_path, action="replace_function", filename="m.js", function="b",
                  code="function b() {}\nfunction d() {}\nmodule.exports = { a, b, d };")
    assert result["status"] == "success", result
    assert path.read_text() == "function a() {}\n\nfunction b() {}\nfunction d() {}\n\nmodule.exports = { a, b, d };\n"


def test_partial_exports_in_code_keep_the_files_other_exports(tmp_path):
    from src.utils.symbols import _union_exports
    path = tmp_path / "m.js"
    path.write_text("function a() {}\nfunction b() {}\n\nmodule.exports = {\n  a,\n  b\n};\n")
    result = edit(tmp_path, action="replace_function", filename="m.js", function="c",
                  code="function c() {}\nmodule.exports = { c };")
    assert result["status"] == "success", result
    assert path.read_text().endswith("module.exports = {\n  a,\n  b,\n  c\n};\n") and "merged with your names" in result["message"]
    assert _union_exports("module.exports = { a, b };", "module.exports = { b: bb, c };") == "module.exports = { a, b: bb, c };"
    assert _union_exports("module.exports = Game;", "module.exports = { c };") is None


def test_a_function_name_the_file_lacks_goes_to_the_one_name_the_code_defines(tmp_path):
    path = tmp_path / "g.js"
    path.write_text("function resetGame() {\n  return 0;\n}\n\nfunction other() {}\n")
    result = edit(tmp_path, action="replace_function", filename="g.js", function="runTests",
                  code="function resetGame() {\n  return 1;\n}")
    assert result["status"] == "success", result
    assert "'function' was 'runTests', which 'g.js' does not have" in result["message"]
    assert path.read_text() == "function resetGame() {\n  return 1;\n}\n\nfunction other() {}\n"
    # both names in the file: still refused
    path.write_text("function resetGame() {}\n\nfunction runTests() {}\n")
    result = edit(tmp_path, action="replace_function", filename="g.js", function="runTests", code="function resetGame() {}")
    assert result["status"] == "error" and "your 'code' defines 'resetGame'" in result["message"]


# ============================================================================================ 3. find_replace without filename
def test_find_replace_without_filename_uses_the_one_file_with_the_text(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.executor import ToolExecutor
    workspace = tmp_path / "projects" / "P" / "S" / "shared_workspace"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "tests" / "a.js").write_text("const x = require('../src/game');\n")
    (workspace / "tests" / "b.js").write_text("const y = 1;\nconst shared = 2;\n")
    (workspace / "c.js").write_text("const shared = 2;\n")
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    infer = ToolExecutor._infer_find_replace_filename
    args = {"action": "find_replace", "find_text": "require('../src/game')", "replace_text": "require('../../src/game.js')"}
    assert infer(dict(args), "P", "S") == "tests/a.js"
    assert infer({**args, "find_text": "const shared = 2;"}, "P", "S") is None      # two files hold it
    assert infer({**args, "filename": "tests/a.js"}, "P", "S") is None             # named
    assert infer({**args, "action": "read"}, "P", "S") is None
    assert infer(dict(args), None, None) is None


# ============================================================================================ 4. an answer acknowledges
def test_an_answer_marks_the_senders_messages_read():
    from src.agents.message_delivery import mark_answered_messages_read, unread_messages
    agent = types.SimpleNamespace(read_message_ids={}, message_history=[
        {"role": "user", "message_id": "m1", "content": "[MESSAGE_ID: m1] [From @W1 (Coder)]: done with task 1"},
        {"role": "user", "message_id": "m2", "content": "[MESSAGE_ID: m2] [From @W2 (Tester)]: question"},
        {"role": "user", "message_id": "m3", "content": "[MESSAGE_ID: m3] [From @W1 (Coder)]: and task 2"}])
    agent.mark_message_read = lambda message_id: agent.read_message_ids.__setitem__(message_id, 1.0)
    assert mark_answered_messages_read(agent, {"target_agent_id": "W1", "message_content": "Thanks"}) == ["m1", "m3"]
    assert [m["message_id"] for m in unread_messages(agent, agent.message_history)] == ["m2"]
    assert mark_answered_messages_read(agent, {"message_content": "no target"}) == []


# ============================================================================================ 5. the plan
RUN_PLAN = """**Objective:** Create a browser-based multiplayer Snake game playable on mobile devices connected to the same WiFi network.

**Tech stack:** HTML5, JavaScript (vanilla), peer-to-peer communication via WebRTC for WiFi multiplayer.

**Tasks:**
1. **Core game engine:** snake movement in `src/game.js`.
2. **Network communication:** peer-to-peer WebSocket communication in `src/network.js`.
3. **HTML structure:** the page in `public/index.html`.
4. **Testing & documentation:** tests and `README.md`.
"""


def test_a_browser_game_across_devices_gets_a_server_item():
    from src.utils.plan_checks import with_network_server_item
    text, item = with_network_server_item(RUN_PLAN)
    assert item and "`server.js`" in item and "`ws` package" in item
    assert "\n3. **HTML structure:**" in text and "\n4. **Server (added by the framework" in text
    assert "\n5. **Testing & documentation:**" in text


def test_plans_that_need_no_server_item():
    from src.utils.plan_checks import network_server_gap
    assert network_server_gap("**Objective:** a snake game in the browser for one player.\n1. Game in `src/game.js`") is None
    assert network_server_gap(RUN_PLAN.replace("src/network.js", "src/network.js and the Node server in `server.js`")) is None
    python_plan = RUN_PLAN.replace("HTML5, JavaScript (vanilla)", "Python with Flask, HTML5")
    assert network_server_gap(python_plan) is None  # "Flask" is the server
    item = network_server_gap(RUN_PLAN.replace("JavaScript (vanilla)", "Python 3 for the logic, pytest"))
    assert item and "`server.py`" in item


# ============================================================================================ 6. collect.py
def _collect():
    spec = importlib.util.spec_from_file_location("collect_round5f", ROOT / "collect.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_does_not_redact_code_and_names_broken_sources(tmp_path):
    collect = _collect()
    assert collect.redact_text_line("+    prev_key = None")[1] == 0
    assert collect.redact_text_line("+        prev_key = key")[1] == 0
    assert collect.redact_text_line('+  api_key = os.getenv("X")')[1] == 0
    assert collect.redact_text_line("PASSWORD=hunter2")[1] == 1
    assert collect.redact_text_line('password = "hunter2"')[1] == 1
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bad.py").write_text("def f(:\n")
    assert collect.broken_framework_sources(tmp_path)[0].startswith("src/bad.py:1: SyntaxError")
    entries = collect.parse_entries(
        "2026-11-15 19:58:19,910 - src.utils.task_files - WARNING - task_completion_problems: check failed (invalid syntax. "
        "Perhaps you forgot a comma? (project_links_check.py, line 1142)); not blocking.\n"
        "2026-11-15 20:00:26,631 - src.utils.task_files - WARNING - task_completion_problems: check failed (invalid syntax. "
        "Perhaps you forgot a comma? (project_links_check.py, line 1142)); not blocking.\n")
    lines = collect.failed_framework_checks(entries)
    assert len(lines) == 1 and lines[0].startswith("x2")

# ============================================================================================ 7. a loop is answered at work
def _looping_worker(tool, args, result, state="worker_work", task="T1"):
    return types.SimpleNamespace(agent_id="W4", agent_type="worker", state=state, active_task_id=task, message_history=[
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": tool, "arguments": args}]},
        {"role": "tool", "tool_call_id": "c1", "name": tool, "content": result}])


def test_a_loop_is_answered_twice_per_task_in_the_work_state():
    from src.agents.loop_answer import answer_loop_in_place
    agent = _looping_worker("code_editor", {"action": "replace_function", "filename": "src/network.js",
                                            "function": "NETWORK_CONFIG"},
                            "[Tool Execution Failed] replace_function: 'NETWORK_CONFIG' in 'src/network.js' was NOT changed.")
    with quiet():
        first = answer_loop_in_place(agent, "repeat")
        second = answer_loop_in_place(agent, "failed_cycles")
        third = answer_loop_in_place(agent, "repeat")
    assert first.startswith("[FRAMEWORK - LOOP 1/2]: you keep making the same tool call")
    assert "code_editor(action='replace_function', filename='src/network.js', function='NETWORK_CONFIG')" in first
    assert "was NOT changed" in first and "Read the CURRENT text first" in first
    assert second.startswith("[FRAMEWORK - LOOP 2/2]") and "moved to worker_report" in second
    assert third is None                                  # the old move to worker_report applies
    agent.active_task_id = "T2"                           # a new task starts counting again
    with quiet():
        assert answer_loop_in_place(agent, "repeat").startswith("[FRAMEWORK - LOOP 1/2]")
    agent.state = "worker_report"
    with quiet():
        assert answer_loop_in_place(agent, "repeat") is None


def test_the_loop_notice_fits_the_call():
    from src.agents.loop_answer import loop_notice
    owned = _looping_worker("code_editor", {"action": "replace_function", "filename": "src/score.js"},
                            "Error from tool 'code_editor': 'src/score.js' was NOT changed: it is the file of the FINISHED task")
    assert "belongs to another task" in loop_notice(owned, "repeat", 1)
    run_again = _looping_worker("command_executor", {"action": "run_command", "command": "node --test tests/"},
                                "Command execution completed with return code 1.")
    assert "Nothing changed since the last run" in loop_notice(run_again, "repeat", 1)
    reread = _looping_worker("file_system", {"action": "read", "filename": "src/game.js"}, "1 | const x = 1;")
    assert "You have this content already" in loop_notice(reread, "repeat", 1)
    assert "given result" in loop_notice(reread, "repeat", 1, "given result")

# END OF FILE tests/unit/test_round5f.py
