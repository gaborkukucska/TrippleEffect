# START OF FILE tests/unit/test_round3x.py
"""
Round 3x (run 2026-10-27 08:55-10:04, WiFi_Snake_Game, qwen3.5:4b; plan: "Testing: node --test", no package.json):

1. tests/game.test.js called describe() / test() without taking them from anywhere. The framework's hint said "run it with
   npm test, or npx jest" - and two workers spent 35 minutes on npm (exit 254), npx jest, jest.config.js and package.json.
2. Tool results showed the workspace as 'shared_workspace/...' and npm's output the shells' HOME (sandboxes/_shell_home):
   W1 wrote 'shared_workspace/package.json' and ran find / ls / cd in _shell_home ten times.
3. 26 of 35 tests failed for mistakes in the TEST file the diagnosis did not name: `game.gameState = {...}` replaced the
   module's export (src/game.js kept its own `gameState`), assertEqual compared objects with `!==`, and names the test never
   defined (`gameState`, `assertNotEqual`).
4. "Write automated tests ..." was marked finished while its test file could not even load.
"""
import asyncio
import json
import logging
from pathlib import Path

import pytest

GAME_JS = """\
let gameState = {
  snake: [],
  direction: null,
  score: 0
};

function processMove() {
  const head = { x: gameState.snake[0].x + gameState.direction.x, y: gameState.snake[0].y };
  gameState.snake.unshift(head);
}

function getScore() {
  return gameState.score;
}

module.exports = {
  processMove,
  getScore,
  gameState
};
"""

TEST_JS = """\
const game = require('../src/game.js');

function resetGameState() {
  game.gameState = { snake: [{ x: 10, y: 10 }], direction: { x: 1, y: 0 }, score: 0 };
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${expected}, got ${actual}`);
  }
}

function assertEqualObjects(actual, expected, message) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(message);
  }
}

describe('Game', () => {
  beforeEach(() => {
    resetGameState();
  });

  test('moves', () => {
    game.processMove();
    assertEqual(game.gameState.direction, { x: 1, y: 0 }, 'Direction');
    assertEqual(gameState.score, 0, 'Score');
  });
});
"""

JEST_OUTPUT = """\
FAIL tests/game.test.js
  Game
    ✕ moves (2 ms)

  ● Game › moves

    Direction: expected [object Object], got [object Object]

      at assertEqual (tests/game.test.js:9:11)

  ● Game › score

    ReferenceError: gameState is not defined

      > 28 |     assertEqual(gameState.score, 0, 'Score');
      at Object.<anonymous> (tests/game.test.js:28:17)

Tests:       2 failed, 0 passed, 2 total
"""


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "game.js").write_text(GAME_JS, encoding="utf-8")
    (tmp_path / "tests" / "game.test.js").write_text(TEST_JS, encoding="utf-8")
    (tmp_path / "PROJECT_STRUCTURE.md").write_text("Testing: node --test for JavaScript tests.\n", encoding="utf-8")
    return tmp_path


# =========================================================================================== 1. what provides describe()
def test_missing_runner_globals(ws):
    from src.utils.js_test_checks import missing_runner_globals, package_test_runner, runner_globals_advice
    assert package_test_runner(ws) is None
    missing = missing_runner_globals(ws, "tests/game.test.js")
    assert missing == ["describe", "test", "beforeEach"]
    advice = runner_globals_advice(ws, "tests/game.test.js", missing)
    assert "add `const { describe, test, beforeEach } = require('node:test');` at the top of the file" in advice
    assert "run the tests with `node --test tests/`" in advice and "do not install jest" in advice
    # taken from node:test: nothing is missing
    test_file = ws / "tests" / "game.test.js"
    test_file.write_text("const { describe, test, beforeEach } = require('node:test');\n" + TEST_JS, encoding="utf-8")
    assert missing_runner_globals(ws, "tests/game.test.js") == []
    # a package.json that runs jest provides them
    test_file.write_text(TEST_JS, encoding="utf-8")
    (ws / "package.json").write_text(json.dumps({"scripts": {"test": "jest --config jest.config.js"}}), encoding="utf-8")
    assert package_test_runner(ws) == "jest" and missing_runner_globals(ws, "tests/game.test.js") == []
    # a script that runs node --test does not; expect() has no provider either
    (ws / "package.json").write_text(json.dumps({"scripts": {"test": "node --test tests/"}}), encoding="utf-8")
    test_file.write_text("test('x', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    assert missing_runner_globals(ws, "tests/game.test.js") == ["test", "expect"]
    assert "Node has no `expect`" in runner_globals_advice(ws, "tests/game.test.js", ["test", "expect"])
    # a hand-written runner defines its own test()
    test_file.write_text("function test(name, fn) { fn(); }\ntest('x', () => {});\n", encoding="utf-8")
    assert missing_runner_globals(ws, "tests/game.test.js") == []


def test_the_advice_follows_the_planned_runner(ws):
    from src.utils.js_test_checks import planned_js_runner, runner_globals_advice
    assert planned_js_runner(ws) == "node --test"
    (ws / "PROJECT_STRUCTURE.md").write_text("Testing: Jest for the frontend.\n", encoding="utf-8")
    assert planned_js_runner(ws) == "jest"
    advice = runner_globals_advice(ws, "tests/game.test.js", ["describe"])
    assert "runs the JavaScript tests with jest" in advice and "npm install --save-dev jest" in advice


def test_the_describe_hint_does_not_send_a_project_without_package_json_to_npm(ws):
    from src.utils.test_diagnosis import output_hints
    output = "ReferenceError: describe is not defined\n    at Object.<anonymous> (tests/game.test.js:19:1)"
    hint = next(h for h in output_hints(ws, output) if "describe" in h)
    assert "require('node:test')" in hint and "npm test" not in hint and "npx jest" not in hint
    (ws / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8")
    hint = next(h for h in output_hints(ws, output) if "describe" in h)
    assert hint.startswith("`describe` is not defined: this test file uses the globals of jest") and "`npm test`" in hint


# =========================================================================================== 2. paths in tool results
def test_workspace_relative_paths(tmp_path):
    import src.agents  # noqa: F401 - the executor imports src.agents; importing it first is circular
    from src.tools.executor import workspace_relative_paths
    workspace = tmp_path / "projects" / "Snake" / "s1" / "shared_workspace"
    home = tmp_path / "sandboxes" / "_shell_home"
    text = (f"npm error path {workspace}/package.json\n"
            f"cwd: \"{workspace}\"\n"
            f"npm error A complete log of this run can be found in: {home}/.npm/_logs/2026-debug-0.log\n"
            f"other project: {tmp_path / 'projects'}/Other/s2")
    shown = workspace_relative_paths(text, workspace, tmp_path / "projects", home)
    assert "npm error path package.json\n" in shown and 'cwd: "."' in shown
    assert "found in: ~/.npm/_logs/2026-debug-0.log" in shown and "projects_base/Other/s2" in shown
    assert "shared_workspace" not in shown and "_shell_home" not in shown
    assert workspace_relative_paths(None, workspace) is None
    source = (Path(__file__).resolve().parents[2] / "src" / "tools" / "executor.py").read_text(encoding="utf-8")
    assert "workspace_relative_paths(s, workspace_dir, settings.PROJECTS_BASE_DIR, default_sandbox_home())" in source


def test_npm_without_package_json_note(ws):
    from src.tools.command_hints import command_notes
    output = ("npm error code ENOENT\nnpm error path package.json\nnpm error enoent Could not read package.json: "
              "Error: ENOENT: no such file or directory, open 'package.json'")
    notes = command_notes(ws, "npm test", 254, output)
    assert len(notes) == 1 and notes[0].startswith("`npm test` needs a package.json, and this project has none")
    assert "`node --test tests/`" in notes[0] and "do not install jest" in notes[0]
    assert command_notes(ws, "npm test", 1, "Tests: 1 failed") == []


def test_the_unknown_command_example_uses_node_test(ws):
    from src.utils.test_runs import clear_test_runs, verification_problem
    clear_test_runs()
    problem = verification_problem(ws, "Kick-off Task 4: Run the tests and verify everything works")
    assert "(e.g. `node --test tests/game.test.js`)" in problem


# =========================================================================================== 3. mistakes in the test file
def test_reassigned_export_hint(ws):
    from src.utils.js_test_checks import reassigned_export_hints
    hints = reassigned_export_hints(ws, ["tests/game.test.js"])
    assert len(hints) == 1
    assert hints[0].startswith("tests/game.test.js line 4 REPLACES `game.gameState` (`game.gameState = ...`), but "
                               "src/game.js keeps using its OWN `gameState` (line 1)")
    assert "`Object.assign(game.gameState, { ... })`" in hints[0]
    # setting a property of the object is fine
    (ws / "tests" / "game.test.js").write_text(TEST_JS.replace("game.gameState = {", "game.gameState.snake = {"),
                                               encoding="utf-8")
    assert reassigned_export_hints(ws, ["tests/game.test.js"]) == []


def test_object_identity_and_undefined_name_hints(ws):
    from src.utils.js_test_checks import object_identity_hints, undefined_name_hints
    identity = object_identity_hints(ws, JEST_OUTPUT, ["tests/game.test.js"])
    assert identity and identity[0].startswith("`expected [object Object], got [object Object]`: tests/game.test.js's "
                                               "`assertEqual` (line 7) compares with `!==`")
    assert "use `assertEqualObjects` (line 13)" in identity[0]
    assert object_identity_hints(ws, "expected 3, got 4", ["tests/game.test.js"]) == []
    undefined = undefined_name_hints(ws, JEST_OUTPUT, ["tests/game.test.js"])
    assert undefined == ["tests/game.test.js line 28 uses `gameState`, which the test file never defines or imports, so "
                         "that test stops with \"ReferenceError: gameState is not defined\" before it checks anything. "
                         "src/game.js exports it: write `game.gameState`. The code under test is not wrong for this."]


def test_the_diagnosis_names_the_test_mistakes(ws):
    from src.utils.test_diagnosis import js_test_mistake_hints
    hints = js_test_mistake_hints(ws, JEST_OUTPUT)
    assert len(hints) == 3 and "REPLACES `game.gameState`" in hints[0] and "[object Object]" in hints[1]
    source = (Path(__file__).resolve().parents[2] / "src" / "utils" / "test_diagnosis.py").read_text(encoding="utf-8")
    assert "[Framework check - mistakes in the test file itself:]" in source


# =========================================================================================== 4. a test file that cannot load
def test_a_test_task_whose_js_tests_cannot_load_is_not_finished(ws):
    from src.utils.test_task_check import javascript_test_problem, test_task_problem
    task = ("Kick-off Task 3: Write automated tests for game movement, collision detection and scoring logic in "
            "tests/game.test.js")
    problem = javascript_test_problem(ws, task)
    assert problem.startswith("its tests cannot run: tests/game.test.js calls `describe`, `test`, `beforeEach`")
    logging.disable(logging.CRITICAL)
    try:
        assert asyncio.run(test_task_problem(ws, task)) == problem
    finally:
        logging.disable(logging.NOTSET)
    (ws / "tests" / "game.test.js").write_text("const { describe, test, beforeEach } = require('node:test');\n" + TEST_JS,
                                               encoding="utf-8")
    assert javascript_test_problem(ws, task) is None
    # a task that does not write or name JavaScript tests is not checked
    (ws / "tests" / "game.test.js").write_text(TEST_JS, encoding="utf-8")
    assert javascript_test_problem(ws, "Implement the game loop in src/game.js") is None

# END OF FILE tests/unit/test_round3x.py
