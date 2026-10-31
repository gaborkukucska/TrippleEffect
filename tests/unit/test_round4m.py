# START OF FILE tests/unit/test_round4m.py
"""
Round 4m: the run of 2026-10-31 04:58-07:40 (qwen3.5:4b-q4_K_M, WiFi_Multiplayer_Snake_Game, node --test).

* About 50 edits of the JavaScript tests were rejected ("Unexpected token '}'", "Unexpected end of input", "Identifier
  'mockSocket' has already been declared"). The new texts were whole, balanced blocks - the replaced line RANGES were not:
  05:43:01 W1 replaced lines 25-31 of tests/test_player/test_player.js with a complete `it(...)` block, but line 25 opened
  an `it(..., () => {` that line 32 closed; 07:24:58 W1 rewrote `const mockSocket = ...` in lines 137-142 while the old
  declaration (line 147) stayed. The rejection only showed the end of the file; W1 re-sent the same ranges.
* 05:21:58-05:22:58 W1 re-sent the same tests/test_game/test_game.js 4 times and was told "considered successful" - a
  normal write of that text gets the project checks (names the module does not export, ...), an identical one did not.
* 05:31-06:56 W1 (the tests) tried 7 times to export clearTimer() from W2's FINISHED src/game.js and never told its PM;
  07:25:10 PM1 created "Fix clearTimer() export in src/game.js" a second time although src/game.js exported it since 06:55.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Multiplayer_Snake_Game", "startup_1793393700"
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")
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


def _write(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


PLAYER_TEST = """const assert = require('node:assert');
const { describe, it } = require('node:test');
const Player = require('../../src/player');

describe('Player', () => {
  describe('register', () => {
    it('keeps the name', () => {
      const re = /[}{]/;
      const text = `a ${'}'} b ${ `{` } c`;  // a '{' in a comment
      assert.ok(re && text);
    });

    it('should register player with valid name', () => {
      const player = Player.register('ValidName');
      assert.strictEqual(player.name, 'ValidName');
    });
  });
});
"""
NEW_IT = ("    it('should register player with valid name', () => {\n"
          "      const player = Player.register('ValidName');\n"
          "      assert.strictEqual(player.isRegistered, true);\n"
          "    });")

SOCKET_TEST = """const assert = require('node:assert');
const { describe, it } = require('node:test');

describe('socket', () => {
  it('connects', () => {
    const io = { emit: () => {} };
    io.emit('connection', 1);
    const mockSocket = { id: 'test-socket-1' };
    io.emit('connection', mockSocket);
    assert.ok(mockSocket);
  });
});
"""


# ======================================================================================== 1. the bracket scanner
def test_brackets_in_strings_comments_templates_and_regexes_are_not_counted():
    from src.tools.source_check import bracket_events
    events = "".join(char for char, _offset in bracket_events(PLAYER_TEST))
    # only the code's own brackets: require(...) x3, describe/it calls and arrow bodies, Player.register(...), assert...
    assert events.count("{") == events.count("}") and events.count("(") == events.count(")")
    assert [char for char, _o in bracket_events("const re = /[}{]/; x = a / b; y = (c) / 2;")] == ["(", ")"]
    assert [char for char, _o in bracket_events("s = `a ${ f({b: 1}) } c`;")] == ["(", "{", "}", ")"]
    assert [char for char, _o in bracket_events("// }\n/* { */ x = '}' + \"{\";")] == []
    assert [char for char, _o in bracket_events("return /}/.test(x)")] == ["(", ")"]


# ======================================================================================== 2. the range notes
def test_a_range_that_opens_a_block_closed_after_it_names_the_closing_line():
    from src.tools.source_check import range_balance_note, replaced_lines
    note = range_balance_note(replaced_lines(PLAYER_TEST, 13, 15, NEW_IT))
    assert note == ("\n- The text you replace (lines 13-15) opens '(' '{' (line 13: `it('should register player with valid "
                    "name', () => {`) that are closed only AFTER it, by line 16 (`});`), which you do not replace. Your new "
                    "text closes everything it opens, so that line would be left closing nothing. Include line 16 in what "
                    "you replace (replace lines 13-16), or end your new text WITHOUT that closing text.")
    # the whole block replaced by a whole block: nothing to say
    assert range_balance_note(replaced_lines(PLAYER_TEST, 13, 16, NEW_IT)) == ""
    # a range inside the block, new text inside the block: nothing to say
    assert range_balance_note(replaced_lines(PLAYER_TEST, 14, 15, "      assert.ok(1);")) == ""


def test_a_range_that_closes_an_outer_block_names_where_it_opened():
    from src.tools.source_check import range_balance_note, replaced_lines
    note = range_balance_note(replaced_lines(PLAYER_TEST, 13, 17, NEW_IT, "Chunk 0: "))
    assert note == ("\n- Chunk 0: the text you replace (lines 13-17) closes '}' ')' (line 17: `});`) that opened BEFORE "
                    "it, at line 6 (`describe('register', () => {`). Your new text does not close it, so that block would "
                    "never be closed. End your new text with the same closing text, or stop what you replace before "
                    "line 17.")


def test_new_text_that_opens_or_closes_too_much():
    from src.tools.source_check import range_balance_note, replaced_lines
    unclosed = range_balance_note(replaced_lines(PLAYER_TEST, 14, 15, "      if (x) {\n        y();"))
    assert unclosed == ("\n- Your new text leaves '{' open (it never closes it), but the text you replace (lines 14-15) "
                        "closes everything it opens: add the closing text at the end of your new text.")
    extra = range_balance_note(replaced_lines(PLAYER_TEST, 14, 15, "      y();\n    });"))
    assert extra.startswith("\n- Your new text closes '}' ')' that it never opens, but the text you replace (lines 14-15) "
                            "does not: the file would get a closing bracket too many.")


def test_a_name_declared_twice_names_both_lines():
    from src.tools.source_check import SyntaxIssue, JAVASCRIPT, duplicate_declaration_note, replaced_lines
    new_text = "    const mockSocket = { id: 'test-socket-2' };\n    io.emit('connection', mockSocket);"
    item = replaced_lines(SOCKET_TEST, 6, 7, new_text)
    lines = SOCKET_TEST.splitlines(True)
    result = "".join(lines[:5] + [new_text + "\n"] + lines[7:])
    issue = SyntaxIssue(JAVASCRIPT, "SyntaxError: Identifier 'mockSocket' has already been declared", 8)
    note = duplicate_declaration_note(issue, result, [item])
    assert note == ("\n- 'mockSocket' is declared 2 times in the result (line 6: `const mockSocket = { id: 'test-socket-2' "
                    "};`; line 8: `const mockSocket = { id: 'test-socket-1' };`). Your new text declares it, and a line "
                    "you did NOT replace declares it too. If that line is the OLD version of the code you are rewriting, "
                    "include it in what you replace (a longer line range or search text); if it is different code, give "
                    "the name in your new text another name.")
    # destructuring counts as a declaration; another error says nothing
    destructured = "const { a, isGameOver } = require('x');\nfunction isGameOver() {}\n"
    issue = SyntaxIssue(JAVASCRIPT, "SyntaxError: Identifier 'isGameOver' has already been declared", 2)
    assert "line 1: `const { a, isGameOver } = require('x');`; line 2" in duplicate_declaration_note(
        issue, destructured, [replaced_lines(destructured, 1, 1, "const { a, isGameOver } = require('x');")])
    assert duplicate_declaration_note(SyntaxIssue(JAVASCRIPT, "SyntaxError: Unexpected token '}'", 3), result, [item]) == ""


@needs_node
def test_edit_rejection_adds_the_notes_only_for_replacements():
    from src.tools.source_check import edit_rejection, replaced_lines
    lines = PLAYER_TEST.splitlines(True)
    result = "".join(lines[:12] + [NEW_IT + "\n"] + lines[15:])
    plain = edit_rejection("tests/test_player.js", PLAYER_TEST, result)
    assert plain.startswith("Edit rejected: the result would not be valid JavaScript (SyntaxError: Unexpected token '}'")
    assert "why the edit broke the file" not in plain
    noted = edit_rejection("tests/test_player.js", PLAYER_TEST, result, [replaced_lines(PLAYER_TEST, 13, 15, NEW_IT)])
    assert noted.startswith(plain + "\n[Framework check - why the edit broke the file:]\n- The text you replace (lines "
                                    "13-15) opens")
    # a JSON or shell file: the bracket note is for JavaScript / JSON only
    assert edit_rejection("a.json", '{"a": 1}', '{"a": 1}}', [replaced_lines('{"a": 1}', 1, 1, '{"a": 1}}')]).endswith(
        "\n- Your new text closes '}' that it never opens, but the text you replace (line 1) does not: the file would get "
        "a closing bracket too many. Remove it from the end of your new text, or include the line that opens that block in "
        "what you replace.")
    assert "why the edit broke" not in (edit_rejection("run.sh", "#!/bin/bash\nif true; then\n  echo\nfi\n",
                                                       "#!/bin/bash\nif true; then\n  echo\n",
                                                       [replaced_lines("x\n", 1, 1, "y {")]) or "")


# ======================================================================================== 3. through the tools
def fs(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    with quiet():
        return run(FileSystemTool().execute("W1", tmp_path, scope="private", **kwargs))


def editor(tmp_path, **kwargs):
    from src.tools.code_editor import CodeEditorTool
    with quiet():
        return run(CodeEditorTool().execute("W1", tmp_path, scope="private", **kwargs))


@needs_node
def test_replace_lines_rejection_names_the_cut_block(tmp_path):
    _write(tmp_path, {"tests/test_player.js": PLAYER_TEST})
    answer = fs(tmp_path, action="replace_lines", filename="tests/test_player.js", replace_start_line=13,
                replace_end_line=15, content=NEW_IT)
    assert answer["status"] == "error"
    assert "Include line 16 in what you replace (replace lines 13-16)" in answer["message"], answer
    assert (tmp_path / "tests/test_player.js").read_text() == PLAYER_TEST
    # the corrected range is saved
    answer = fs(tmp_path, action="replace_lines", filename="tests/test_player.js", replace_start_line=13,
                replace_end_line=16, content=NEW_IT)
    assert answer["status"] == "success", answer
    # find_replace of one place
    _write(tmp_path, {"tests/test_socket.js": SOCKET_TEST})
    answer = fs(tmp_path, action="find_replace", filename="tests/test_socket.js",
                find_text="    io.emit('connection', 1);", replace_text="    const mockSocket = 2;")
    assert answer["status"] == "error" and "'mockSocket' is declared 2 times in the result" in answer["message"], answer


@needs_node
def test_code_editor_rejection_names_the_cut_block_per_chunk(tmp_path):
    _write(tmp_path, {"tests/test_player.js": PLAYER_TEST, "tests/test_socket.js": SOCKET_TEST})
    answer = editor(tmp_path, action="replace_chunks", filename="tests/test_player.js",
                    chunks=[{"start_line": 13, "end_line": 15, "replace": NEW_IT}])
    assert answer["status"] == "error" and answer["message"].startswith("Code edit rejected:"), answer
    assert ("- Chunk 0: the text you replace (lines 13-15) opens '(' '{' (line 13: `it('should register player with valid "
            "name', () => {`) that are closed only AFTER it, by line 16") in answer["message"]
    search = ("    it('should register player with valid name', () => {\n"
              "      const player = Player.register('ValidName');\n"
              "      assert.strictEqual(player.name, 'ValidName');\n")
    answer = editor(tmp_path, action="replace_chunks", filename="tests/test_player.js",
                    chunks=[{"search": search, "replace": NEW_IT + "\n"}])
    assert answer["status"] == "error" and "- Chunk 0: the text you replace (lines 13-15) opens" in answer["message"]
    answer = editor(tmp_path, action="replace_chunks", filename="tests/test_socket.js",
                    chunks=[{"search": "    io.emit('connection', 1);", "replace": "    const mockSocket = 2;"}])
    assert answer["status"] == "error" and "Your new text declares it, and a line you did NOT replace" in answer["message"]
    assert (tmp_path / "tests/test_player.js").read_text() == PLAYER_TEST


def test_an_identical_rewrite_gets_the_project_checks(tmp_path):
    _write(tmp_path, {"src/game.js": "let score = 0;\nfunction add(n) { score += n; }\nmodule.exports = { add };\n"})
    test_text = ("const { describe, it } = require('node:test');\nconst assert = require('node:assert');\n"
                 "const { add, reset } = require('../src/game');\n\ndescribe('g', () => {\n  it('adds', () => {\n"
                 "    add(1);\n    assert.ok(true);\n  });\n});\n")
    first = fs(tmp_path, action="write", filename="tests/game.test.js", content=test_text)
    assert first["status"] == "success" and "[Framework note - the file WAS saved:]" in first["message"], first
    again = fs(tmp_path, action="write", filename="tests/game.test.js", content=test_text)
    assert again["status"] == "success"
    assert again["message"].startswith("File 'tests/game.test.js' already exists and contains the exact same content. "
                                       "NOTHING was changed: you sent exactly the text the file already holds, so the "
                                       "problems named below are STILL in it."), again
    assert ("[Framework note - the file is UNCHANGED (it already held exactly this text):]\n- tests/game.test.js takes "
            "`reset` from src/game.js, which does not export it") in again["message"]
    # nothing to say: the old wording
    plain = fs(tmp_path, action="write", filename="src/game.js",
               content="let score = 0;\nfunction add(n) { score += n; }\nmodule.exports = { add };\n")
    assert plain["message"] == ("File 'src/game.js' already exists and contains the exact same content. No changes were "
                                "made, but your action is considered successful.")


# ======================================================================================== 4. another worker's file
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
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert added["status"] == "success", added
    return added["task_uuid"]


GAME_JS = ("let timer = null;\nfunction initGame() { timer = setInterval(() => {}, 100); }\n"
           "function clearTimer() { clearInterval(timer); }\nmodule.exports = {\n    initGame,\n    clearTimer\n};\n")
TASK_2 = "Kick-off Task 2: Implement the core Snake game mechanics (movement, collision detection, scoring) in src/game.js"
TASK_7 = "Kick-off Task 7: Write automated tests for game logic in tests/test_game/test_game.js using node --test"
EXPORT_EDIT = {"action": "replace_chunks", "filename": "src/game.js",
               "chunks": [{"start_line": 4, "end_line": 7, "replace": "module.exports = {\n    initGame,\n    clearTimer,\n"
                                                                       "    getTimer: () => timer\n};"}]}


def test_the_attempted_change_is_described_short():
    from src.tools.project_management import attempted_change_text
    assert attempted_change_text("code_editor", EXPORT_EDIT) == (
        "code_editor 'replace_chunks' lines 4-7 with:\n```\nmodule.exports = {\n    initGame,\n    clearTimer,\n"
        "    getTimer: () => timer\n};\n```")
    assert attempted_change_text("file_system", {"action": "replace_lines", "filename": "a.js", "replace_start_line": 3,
                                                 "replace_end_line": 4, "content": "x" * 600}).endswith("x" * 10 + "\n...(cut)\n```")
    assert attempted_change_text("file_system", {"action": "delete", "filename": "a.js"}) == "file_system 'delete'"
    chunks_text = '[{"search": "a", "replace": "b"}, {"search": "c", "replace": "d"}]'
    assert attempted_change_text("code_editor", {"action": "replace_chunks", "chunks": chunks_text}) == (
        "code_editor 'replace_chunks' (first of 2 changes) with:\n```\nb\n```")


@needs_tw
def test_repeated_refusals_for_another_workers_file_reach_the_pm(tasks):
    from src.tools.executor import ToolExecutor
    from src.tools.project_management import FOREIGN_CHANGE_RELAY_EVERY
    w1, w2, pm1 = Worker("W1"), Worker("W2", state="worker_wait"), Worker("PM1", "pm", "pm_manage")
    manager = _manager(pm1, w1, w2)
    handler = types.SimpleNamespace(route_and_activate_agent_message=AsyncMock(
        return_value=({"status": "delivered", "message": "ok"}, None)))
    manager.interaction_handler = handler
    _write(tasks.workspace, {"src/game.js": GAME_JS})
    t2 = _add(tasks, manager, TASK_2, "W2")
    t7 = _add(tasks, manager, TASK_7, "W1")
    assert tasks("framework", manager, action="modify_task", task_id=t2, task_progress="finished")["status"] == "success"
    w1.current_task_id = t7

    def attempt():
        with quiet():
            return str(run(ToolExecutor().execute_tool("W1", tasks.workspace.parent, "code_editor", dict(EXPORT_EDIT),
                                                       project_name=PROJECT, session_name=SESSION, manager=manager)))

    first = attempt()
    assert first.startswith("Error from tool 'code_editor': 'src/game.js' was NOT changed") and "your PM" in first
    assert "The framework has told your PM" not in first and handler.route_and_activate_agent_message.await_count == 0
    second = attempt()
    assert second.endswith("The framework has told your PM PM1 that you want to change 'src/game.js' (with your last "
                           "attempted change). Do NOT try to change that file again: continue with the parts of YOUR task "
                           "you can do, or wait for your PM's answer."), second
    call = handler.route_and_activate_agent_message.await_args.kwargs
    assert call["sender_id"] == "W1" and call["target_identifier"] == "PM1"
    assert call["message_content"].startswith(
        "[Framework notice for W1] W1 has tried 2 times to change 'src/game.js' and was refused: it is the file of the "
        f"FINISHED task '{TASK_2[:87]}...' ({t2}) of W2, and W1's task does not name that file.\nThe change it tried last: "
        "code_editor 'replace_chunks' lines 4-7 with:\n```\nmodule.exports = {"), call["message_content"]
    assert call["message_content"].endswith(
        "Decide now: if 'src/game.js' really needs that change (read the file), create a task for the worker who owns it "
        "that names the problem and the file; if W1's own code or test is what is wrong (it must use 'src/game.js' the way "
        "it is written), tell W1 that with send_message.")
    third = attempt()
    assert third.endswith("Your PM PM1 already KNOWS that you want to change 'src/game.js' (the framework told it). Do NOT "
                          "try to change that file again: continue with the parts of YOUR task you can do, or wait for "
                          "your PM's answer.")
    assert handler.route_and_activate_agent_message.await_count == 1
    for _ in range(FOREIGN_CHANGE_RELAY_EVERY - 2):
        attempt()
    assert handler.route_and_activate_agent_message.await_count == 1
    attempt()   # FOREIGN_CHANGE_RELAY_EVERY refusals after the first message: told again
    assert handler.route_and_activate_agent_message.await_count == 2
    assert tasks.workspace.joinpath("src/game.js").read_text() == GAME_JS


@needs_tw
def test_an_undelivered_relay_is_tried_again(tasks):
    from src.tools.executor import ToolExecutor
    w1, w2, pm1 = Worker("W1"), Worker("W2", state="worker_wait"), Worker("PM1", "pm", "pm_manage")
    manager = _manager(pm1, w1, w2)
    handler = types.SimpleNamespace(route_and_activate_agent_message=AsyncMock(
        return_value=({"status": "error", "message": "blocked"}, None)))
    manager.interaction_handler = handler
    _write(tasks.workspace, {"src/game.js": GAME_JS})
    t2 = _add(tasks, manager, TASK_2, "W2")
    w1.current_task_id = _add(tasks, manager, TASK_7, "W1")
    assert tasks("framework", manager, action="modify_task", task_id=t2, task_progress="finished")["status"] == "success"
    answers = []
    for _ in range(3):
        with quiet():
            answers.append(str(run(ToolExecutor().execute_tool("W1", tasks.workspace.parent, "code_editor",
                                                               dict(EXPORT_EDIT), project_name=PROJECT,
                                                               session_name=SESSION, manager=manager))))
    assert handler.route_and_activate_agent_message.await_count == 2      # the 2nd and the 3rd refusal
    assert not any("your PM PM1" in answer for answer in answers)          # never claimed it was told


# ======================================================================================== 5. an export that exists
def test_the_exports_a_task_asks_for():
    from src.tools.project_management import requested_js_exports
    assert requested_js_exports("Fix clearTimer() export in src/game.js: Export clearTimer function from src/game.js "
                                "module. The function is defined but not included in the module.exports object.") == [
        ("clearTimer", "src/game.js")]
    assert requested_js_exports("Export `getState` from 'src/game.js' for the tests") == [("getState", "src/game.js")]
    assert requested_js_exports("Implement the export feature in src/exporter.js") == []
    assert requested_js_exports("Fix the score bug in src/game.js") == []


@needs_tw
def test_a_task_to_export_what_is_exported_is_not_created(tasks):
    pm1, w2 = Worker("PM1", "pm", "pm_manage"), Worker("W2", state="worker_wait")
    manager = _manager(pm1, w2)
    _write(tasks.workspace, {"src/game.js": GAME_JS})
    refused = tasks("PM1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W2",   # 07:25:10
                    title="Fix clearTimer() export in src/game.js",
                    description="Export clearTimer function from src/game.js module. The function is defined but not "
                                "included in the module.exports object.")
    assert refused["status"] == "error" and refused["error_type"] == "already_done", refused
    assert refused["message"].startswith("The task was NOT created: src/game.js ALREADY exports clearTimer (read its "
                                         "module.exports / export lines) - there is nothing to export.")
    # a name it does not export yet: created
    added = tasks("PM1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W2",
                  title="Fix getTimer() export in src/game.js",
                  description="Export getTimer() function from src/game.js so the tests can read the timer")
    assert added["status"] == "success", added
    # the framework is never refused
    _add(tasks, manager, "Export clearTimer() function from src/game.js for the tests")
