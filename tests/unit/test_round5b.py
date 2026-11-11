# START OF FILE tests/unit/test_round5b.py
"""
Round 5b: the first run of Round 5a (2026-11-10 18:05-18:27, Local_Snake_Multiplayer, nvidia/nemotron-3.5-lightning:free on
OpenRouter). The run did not stall: 20 minutes after its start every task was finished, the tests "passed", the PM's audit
reported "Project Audit Complete" - and the user was told "the project is now in the planning phase".

* The Admin AI answered the PM's completion report with one call, mark_message_read. The message was removed from its
  history before its next step, which then told the user the project was being planned.
* What was built was no game: a page with a name form and a leaderboard. `src/snake.js` was loaded only by its test, and
  that test file defined functions it never called and registered no test (`node --test`: "tests 1, pass 1"). The audit
  asked three questions about tasks and tests and none about the product.
* `replace_function` was called with the whole file as 'code' and no 'function'; `zim_search` was offered to the Coder in
  45 of 50 calls ("Write automated tests" matched the research words); collect.py read no OpenRouter reply line and hid
  "PASS: ..." lines of test output as secrets.
"""
import asyncio
import importlib.util
import json
import logging
import types
from contextlib import contextmanager
from pathlib import Path


import src.agents  # noqa: F401 - resolves pre-existing circular import order


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


SNAKE_JS = '''"use strict";
class Snake {
  constructor(x, y) { this.positions = [{ x, y }]; }
  move() { return this.positions[0]; }
}
function spawnFood(w, h) { return { x: 1, y: 1 }; }
module.exports = { Snake, spawnFood };
'''
SERVER_JS = '''"use strict";
const http = require("http");
var page = "<!DOCTYPE html><html><body><form id=\\"name-form\\"></form><ul id=\\"leaderboard\\"></ul></body></html>";
const server = http.createServer(function (req, res) { res.end(page); });
server.listen(3000);
'''
# the test file of the run: checks inside exported functions that nothing calls
EMPTY_TEST_JS = '''import { Snake } from '../src/snake.js';

export const movementTests = () => {
  const snake = new Snake(0, 0);
  if (snake.move().x !== 0) {
    throw new Error('wrong');
  }
};

export const growthTests = () => {
  const snake = new Snake(0, 0);
};
'''
REAL_TEST_JS = '''const { test } = require('node:test');
const assert = require('node:assert');
const { Snake } = require('../src/snake.js');

test('the snake starts where it is put', () => {
  assert.deepStrictEqual(new Snake(0, 0).move(), { x: 0, y: 0 });
});
'''
PLAN = '''# Project Plan: Local_Snake_Multiplayer

**Objective:** A browser-based multiplayer snake game hosted on a laptop, playable by mobile devices on the same WiFi, with player naming, scoring, and a local leaderboard.

**Tech stack:** Vanilla JavaScript, HTML5, CSS.

**Tasks:**
1. **Core logic:** Implement snake movement in `src/snake.js`.
'''


def _workspace(tmp_path, test_source=EMPTY_TEST_JS, server=SERVER_JS):
    for rel, text in (("src/snake.js", SNAKE_JS), ("server/server.js", server), ("tests/snake.test.js", test_source),
                      ("PROJECT_PLAN.md", PLAN), ("docs/README.md", "# Snake\nRun `node server/server.js`.\n")):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


# ============================================================================================ 1. a message is not lost
def _history_with_report():
    return [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "Build a snake game"},
        {"role": "assistant", "content": "Your project has been approved and started."},
        {"role": "user", "message_id": "msg_1_PM1",
         "content": "[MESSAGE_ID: msg_1_PM1] [From @PM1]: Project Audit Complete. Please ask the user to test."},
    ]


def test_a_message_that_was_only_marked_read_stays_for_the_next_step():
    from src.agents.tool_activity import read_messages_to_hide
    history = _history_with_report() + [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "mark_message_read", "arguments": {"message_id": "msg_1_PM1"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "Message 'msg_1_PM1' marked as read."},
    ]
    agent = types.SimpleNamespace(read_message_ids={"msg_1_PM1": 1.0}, message_history=history)
    assert read_messages_to_hide(agent) == set()  # the agent has not answered it yet
    history.append({"role": "assistant", "content": "The project is finished - please test it."})
    assert read_messages_to_hide(agent) == {"msg_1_PM1"}  # answered: it may go


def test_a_message_marked_read_along_with_other_work_goes_at_once():
    from src.agents.tool_activity import read_messages_to_hide
    history = _history_with_report() + [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "mark_message_read", "arguments": '{"message_id": "msg_1_PM1"}'},
            {"id": "c2", "name": "send_message", "arguments": {"target_agent_id": "PM1", "message_content": "ok"}}]},
    ]
    agent = types.SimpleNamespace(read_message_ids={"msg_1_PM1": 1.0}, message_history=history)
    assert read_messages_to_hide(agent) == {"msg_1_PM1"}
    assert read_messages_to_hide(types.SimpleNamespace(read_message_ids={}, message_history=history)) == set()
    old = types.SimpleNamespace(read_message_ids={"msg_1_PM1": 1.0}, message_history=_history_with_report())
    assert read_messages_to_hide(old) == {"msg_1_PM1"}  # the call that marked it is no longer in the history


def test_the_prompt_assembler_keeps_the_report_until_it_is_answered():
    from src.agents.cycle_components import prompt_assembler
    source = Path(prompt_assembler.__file__).read_text(encoding="utf-8")
    assert "read_messages_to_hide(agent)" in source
    assert 'msg.get("message_id") in hide_ids' in source
    from src.tools.mark_message_read import MarkMessageReadTool
    result = run(MarkMessageReadTool().execute("admin_ai", Path("."), message_id="msg_1_PM1"))
    assert "do that NOW, in your next step" in result["message"] and result["message_id"] == "msg_1_PM1"


# ============================================================================================ 2. tests that test nothing
def test_a_javascript_test_file_in_which_nothing_runs():
    from src.utils.product_check import js_test_runs_nothing
    assert js_test_runs_nothing(EMPTY_TEST_JS)
    assert not js_test_runs_nothing(REAL_TEST_JS)
    assert not js_test_runs_nothing("describe('snake', () => {\n  it('moves', () => {});\n});\n")
    # a script-style test runs its checks when it is loaded: that is a test
    assert not js_test_runs_nothing("const assert = require('assert');\nconst a = require('../a');\nassert.equal(a.f(), 1);\n")
    assert not js_test_runs_nothing("function check() { run(); }\ncheck();\n")
    assert not js_test_runs_nothing("const result = run();\n")
    assert not js_test_runs_nothing("// test('in a comment')\ntest('real', () => {});\n")
    assert js_test_runs_nothing("// test('only in a comment', () => {});\nconst x = 1;\n")
    assert not js_test_runs_nothing("")


def test_test_files_that_test_nothing_are_named(tmp_path):
    from src.utils.product_check import tests_that_test_nothing
    root = _workspace(tmp_path)
    (root / "tests" / "test_empty.py").write_text("import os\n\ndef helper():\n    return 1\n")
    (root / "tests" / "test_real.py").write_text("def test_one():\n    assert True\n")
    found = dict(tests_that_test_nothing(root))
    assert set(found) == {"tests/snake.test.js", "tests/test_empty.py"}
    assert "registers NO test and calls nothing (it only defines movementTests, growthTests)" in found["tests/snake.test.js"]
    assert "test('what it checks', () => { ... });" in found["tests/snake.test.js"]
    assert "no test function" in found["tests/test_empty.py"]


def test_a_test_writing_task_is_not_finished_with_a_file_that_tests_nothing(tmp_path):
    from src.utils.test_task_check import javascript_test_problem
    root = _workspace(tmp_path)
    task = "Kick-off Task 3: Write automated tests for snake logic using node --test in tests/snake.test.js"
    with quiet():
        problem = javascript_test_problem(root, task, ["tests/snake.test.js"])
    assert problem and problem.startswith("its test file `tests/snake.test.js` tests nothing: it registers NO test")
    (root / "tests" / "snake.test.js").write_text(REAL_TEST_JS)
    with quiet():
        assert javascript_test_problem(root, task, ["tests/snake.test.js"]) is None


# ============================================================================================ 3. the product
def test_code_that_only_its_tests_use_is_named(tmp_path):
    from src.utils.product_check import objective, product_problems, program_entries, unused_modules
    root = _workspace(tmp_path)
    assert program_entries(root) == ["server/server.js"]
    assert unused_modules(root) == [("src/snake.js", ["Snake", "spawnFood"], "only by its tests (tests/snake.test.js)")]
    problems = product_problems(root)
    assert len(problems) == 2
    assert problems[0].startswith("`src/snake.js` (Snake, spawnFood) is used only by its tests (tests/snake.test.js): "
                                  "nothing a user starts or opens (`server/server.js`) loads it")
    assert "MISSING" in problems[0] and problems[1].startswith("`tests/snake.test.js` tests nothing")
    assert objective(root).startswith("A browser-based multiplayer snake game hosted on a laptop")


def test_code_the_program_loads_is_not_named(tmp_path):
    from src.utils.product_check import unused_modules
    # the server requires it
    root = _workspace(tmp_path / "a", server=SERVER_JS.replace('const http', 'const game = require("../src/snake.js");\nconst http'))
    assert unused_modules(root) == []
    # a page loads it
    root = _workspace(tmp_path / "b")
    (root / "public").mkdir()
    (root / "public" / "index.html").write_text('<html><body><script src="/src/snake.js"></script></body></html>')
    assert unused_modules(root) == []
    # the server sends every file of its folder
    root = _workspace(tmp_path / "c", server=SERVER_JS.replace(
        "res.end(page);", "require('fs').readFile(require('path').join(__dirname, '..', 'src', req.url), (e, d) => res.end(d));"))
    assert unused_modules(root) == []
    # a library without anything to start: its modules are used by tests only, and that is fine
    root = _workspace(tmp_path / "d")
    (root / "server" / "server.js").unlink()
    assert unused_modules(root) == []


def test_python_modules_of_a_program(tmp_path):
    from src.utils.product_check import unused_modules
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("from game import Game\n\nif __name__ == '__main__':\n    Game().run()\n")
    (tmp_path / "src" / "game.py").write_text("class Game:\n    def run(self):\n        pass\n")
    (tmp_path / "src" / "scores.py").write_text("def best(scores):\n    return max(scores)\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_scores.py").write_text("from scores import best\n\ndef test_best():\n    assert best([1, 2]) == 2\n")
    assert unused_modules(tmp_path) == [("src/scores.py", ["best"], "only by its tests (tests/test_scores.py)")]
    (tmp_path / "src" / "game.py").write_text("from .scores import best\n\nclass Game:\n    def run(self):\n        best([1])\n")
    assert unused_modules(tmp_path) == []


def test_the_audit_note_names_the_findings_and_the_objective(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.utils import program_run_check
    root = _workspace(tmp_path / "P" / "S" / "shared_workspace")
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path)

    async def no_run_problem(_root, agent_id="framework"):
        return None
    monkeypatch.setattr(program_run_check, "program_run_problem", no_run_problem)
    manager = types.SimpleNamespace(current_project="P", current_session="S")
    with quiet():
        note = run(program_run_check.audit_run_note(manager))
    assert note.startswith("[Framework Run Check]: before this audit the framework started the project the way a user would. "
                           "It does NOT work yet:\n- `src/snake.js` (Snake, spawnFood) is used only by its tests")
    assert "- `tests/snake.test.js` tests nothing" in note
    assert "[Framework Product Check]: passing tests do not show that the product exists." in note
    assert '"A browser-based multiplayer snake game hosted on a laptop' in note
    assert "server/server.js (" in note and "src/snake.js (" in note and "tests/snake.test.js (" not in note
    # nothing wrong: the objective and its question are still asked
    (root / "tests" / "snake.test.js").write_text(REAL_TEST_JS)
    (root / "server" / "server.js").write_text(SERVER_JS.replace('const http', 'const game = require("../src/snake.js");\nconst http'))
    with quiet():
        note = run(program_run_check.audit_run_note(manager))
    assert "It does NOT work yet" not in note and "Go on with the audit." in note
    assert "for EVERY thing it says a user can see or do, name the file that does it" in note
    assert root.is_dir()


def test_the_completion_report_waits_for_the_findings(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.utils.product_check import MAX_REPORT_REFUSALS, audit_report_refusal
    root = _workspace(tmp_path / "P" / "S" / "shared_workspace")
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path)
    pm = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_audit")
    report = "Project Audit Complete. The tests pass. Please ask the user to test the latest version."
    with quiet():
        for _ in range(MAX_REPORT_REFUSALS):
            args = {"target_agent_id": "admin_ai", "message_content": report}
            refusal = audit_report_refusal(pm, None, args, "P", "S")
            assert refusal.startswith("Your report was NOT sent: the project is not complete.")
            assert "`src/snake.js`" in refusal and "action 'add_task'" in refusal and args["message_content"] == report
        # the same findings do not hold a report for ever: it goes out, and says what is open
        args = {"target_agent_id": "admin_ai", "message_content": report}
        assert audit_report_refusal(pm, None, args, "P", "S") is None
        assert args["message_content"].startswith(report + "\n[Framework note for the Admin AI and the user")
        assert "`src/snake.js`" in args["message_content"]
        # other messages, other states and other agents are not touched
        assert audit_report_refusal(pm, None, {"target_agent_id": "W1", "message_content": "x"}, "P", "S") is None
        manage = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_manage")
        assert audit_report_refusal(manage, None, {"target_agent_id": "admin_ai", "message_content": "x"}, "P", "S") is None
        # a project without findings reports at once
        (root / "tests" / "snake.test.js").write_text(REAL_TEST_JS)
        (root / "server" / "server.js").write_text(SERVER_JS.replace('const http', 'const g = require("../src/snake.js");\nconst http'))
        fresh = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_audit")
        args = {"target_agent_id": "admin_ai", "message_content": report}
        assert audit_report_refusal(fresh, None, args, "P", "S") is None and args["message_content"] == report


def test_the_executor_holds_the_report(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.executor import ToolExecutor
    _workspace(tmp_path / "P" / "S" / "shared_workspace")
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path)
    with quiet():
        executor = ToolExecutor()
        pm = types.SimpleNamespace(agent_id="PM1", agent_type="pm", state="pm_audit", message_history=[])
        manager = types.SimpleNamespace(agents={"PM1": pm}, tool_executor=executor, current_project="P", current_session="S")
        result = run(executor.execute_tool(
            "PM1", tmp_path / "sandbox", "send_message",
            {"target_agent_id": "admin_ai", "message_content": "Project Audit Complete. The tests pass."},
            project_name="P", session_name="S", manager=manager))
    assert isinstance(result, str) and result.startswith("Error from tool 'send_message': Your report was NOT sent")


def test_the_prompts_ask_for_the_product():
    import yaml
    prompts = yaml.safe_load((Path(__file__).resolve().parents[2] / "prompts.yaml").read_text(encoding="utf-8"))
    audit = prompts["pm_audit_prompt"]
    assert "**Question 4 - Can a user DO what the objective says?**" in audit and "4-QUESTION CHECKLIST" in audit
    assert audit.index("Question 4") < audit.index("Project Audit Complete. The tests pass.")
    planning = prompts["admin_ai_planning_prompt"]
    assert "MUST BUILD every part of the objective that a user sees or uses" in planning and "`public/index.html`" in planning


# ============================================================================================ 4. replace_function
GAME_PY = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"


def _edit(sandbox, **kwargs):
    from src.tools.code_editor import CodeEditorTool
    with quiet():
        return run(CodeEditorTool().execute("W1", sandbox, scope="private", **kwargs))


def test_the_name_is_taken_from_code_that_defines_one_thing(tmp_path):
    (tmp_path / "g.py").write_text(GAME_PY)
    result = _edit(tmp_path, action="replace_function", filename="g.py", code="def b():\n    return 3")
    assert result["status"] == "success", result
    assert "'function' was not given; 'b' is what your code defines." in result["message"]
    assert (tmp_path / "g.py").read_text() == "def a():\n    return 1\n\n\ndef b():\n    return 3\n"


def test_a_whole_file_sent_as_one_function_is_explained(tmp_path):
    (tmp_path / "g.py").write_text(GAME_PY)
    result = _edit(tmp_path, action="replace_function", filename="g.py", code=GAME_PY.replace("return 2", "return 5"))
    assert result["status"] == "error"
    assert "'function' is missing and your 'code' holds 2 definitions (a, b) - it looks like the whole file" in result["message"]
    assert "function='a'" in result["message"] and "file_system action 'write'" in result["message"]
    assert (tmp_path / "g.py").read_text() == GAME_PY


# ============================================================================================ 5. the tool surface
def test_a_coders_role_text_does_not_ask_for_the_offline_library(monkeypatch):
    from src.agents.tool_surface import hidden_tools
    from src.config.settings import settings
    monkeypatch.setattr(settings, "TOOL_SURFACE", "focused", raising=False)
    monkeypatch.setenv("ZIM_FILES_PATH", "/home/tom/zim")
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "x")
    role_text = ("You are a Coder for the Local Snake Multiplayer project. Your goal is to implement the core game logic in "
                 "src/snake.js. Write automated tests for snake logic using node --test. Create and maintain README.md, "
                 "report to the PM and follow the project plan from its source.")
    coder = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_work", role="Coder", persona="Coder",
                                  _config_system_prompt=role_text, current_task_description="Kick-off Task 1: src/snake.js")
    names = ["asset_search", "github_tool", "zim_search", "web_search", "file_system"]
    assert set(hidden_tools(coder, names)) == {"asset_search", "github_tool", "zim_search"}
    coder.current_task_description = "Research the history of Snake and look up its rules on Wikipedia"
    assert "zim_search" not in hidden_tools(coder, names)


# ============================================================================================ 6. collect.py v2.10
def _collect():
    spec = importlib.util.spec_from_file_location("collect_for_test_5b", Path(__file__).resolve().parents[2] / "collect.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request(second, agent, history):
    system = (f"[SYSTEM CONTEXT]\n- Your Agent ID: `{agent}`\n- Your Agent Type: `worker`\n"
              "- **Tools you can call in this state ('worker_work'):** file_system." + "x" * 2000)
    payload = {"model": "m", "messages": [{"role": "system", "content": system}, {"role": "user", "content": "u" * history}]}
    return (f"2026-11-10 18:00:{second:02d},000 - src.llm_providers.openrouter_provider - DEBUG - Provider 'm': FULL JSON "
            f"equivalent of api_params being sent:\n{json.dumps(payload, indent=2)}")


def _reply(second, prompt_tokens):
    return (f"2026-11-10 18:00:{second:02d},500 - src.llm_providers.openrouter_provider - INFO - OpenRouter finished: "
            f"finish_reason=tool_calls, prompt_tokens={prompt_tokens}, output_tokens=45, content_chars=0, reasoning_chars=74, "
            "tool_calls=1")


def test_collect_reads_openrouter_replies_and_pairs_them_by_size():
    collect = _collect()
    # W1 (small request) and W2 (large request) call at the same time; W2's reply comes first
    log = "\n".join([_request(1, "W1", 2000), _request(2, "W2", 30000), _reply(10, 8900), _reply(20, 1150)]) + "\n"
    calls = collect.collect_budget(collect.parse_entries(log))["calls"]
    assert [(c["agent"], c["prompt_tokens"], c["reason"]) for c in calls] == [("W1", 1150, "tool_calls"), ("W2", 8900, "tool_calls")]
    assert calls[0]["total_s"] == 19.5 and calls[1]["total_s"] == 8.5  # from the request to its reply line
    report = collect.build_budget_report({"calls": calls, "summaries": {}})
    assert "requests 2 (0 without a reply line" in report and "tool_calls: 2" in report
    assert "from each request to its reply" in report


def test_test_output_is_not_redacted_as_a_secret():
    collect = _collect()
    assert collect.redact_text_line("PASS: move right increases x")[0] == "PASS: move right increases x"
    assert collect.redact_text_line("FAIL: setDirection(2) sets down - Expected 2, got 1")[1] == 0
    assert "REDACTED" in collect.redact_text_line("PASSWORD=hunter2hunter2")[0]
    assert "REDACTED" in collect.redact_text_line("api_key: sk-abcdefghijklmnopqrstuvwxyz0123")[0]

# END OF FILE tests/unit/test_round5b.py
