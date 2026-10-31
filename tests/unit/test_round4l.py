# START OF FILE tests/unit/test_round4l.py
"""
Round 4l: the run of 2026-10-31 01:44-04:23 (qwen3.5:4b-q4_K_M, WiFi_Snake_Multiplayer_Game: Node.js, plan "Testing
framework: node --test for all tests. No additional frameworks or libraries").

The code tasks were finished by 02:31; the rest of the run went into Kick-off Task 6 (the tests), W3:
* 02:34:55 W3 sent file_system {'action': 'replace_lines', 'replace_lines': '[1, 5]'} (a line RANGE, no new text) and got
  "'replace_start_line' and 'replace_end_line' parameters are required" - 19 times by 02:42.
* Moved to worker_report by the loop override, it sent the same (refused) call every 4-5 s: each new override was blocked
  by the LoopCoordinator cooldown and the cycle restarted at once with the same context (20 cycles).
* Back in worker_work, the copies worker_report had refused counted as repeats: it was moved back to worker_report 10-35 s
  later (02:37:22, 02:38:24, 02:42:25).
* The framework said "take describe/it from node:test ... do not install jest"; PM1 told W3 to "configure Jest" and W3 ran
  `npm install --save-dev jest` (later jest-environment-jsdom) and spent 1 h 45 min on jest runs.
* The last failures had causes the diagnosis did not name: src/game.js exports `isGameOver` BY VALUE, tests/game.test.js
  set its own `testNextDirection` for the module's `nextDirection`, and "Player already registered" is thrown on purpose by
  src/player.js line 66 because the test registered the same id twice.
"""
import asyncio
import logging
import time
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Multiplayer_Game", "startup_1793382257"


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


REFUSED = ("[Tool Error: file_system] 'file_system' cannot be used in the 'worker_report' state, so NOTHING was done. Here "
           "you can only use: mark_message_read, project_management, send_message.")
RANGE_CALL = {"action": "replace_lines", "filename": "tests/game.test.js", "replace_lines": "[1, 5]"}


def _calls(n, result, start_id=0):
    history = []
    for i in range(n):
        call_id = f"c{start_id + i}"
        history.append({"role": "assistant", "content": "",
                        "tool_calls": [{"id": call_id, "name": "file_system", "arguments": dict(RANGE_CALL)}]})
        history.append({"role": "tool", "tool_call_id": call_id, "name": "file_system", "content": result})
    return history


# ================================================================== 1. calls another state refused are no repeats here
def test_a_call_refused_in_another_state_is_no_repeat():
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler, refused_in_other_state
    assert refused_in_other_state(REFUSED, "worker_work")
    assert not refused_in_other_state(REFUSED, "worker_report")
    assert not refused_in_other_state("[Tool Execution Failed] 'replace_start_line' and ...", "worker_work")
    scheduler = NextStepScheduler(MagicMock())
    failed = "[Tool Execution Failed] 'replace_start_line' and 'replace_end_line' parameters are required"
    history = [{"role": "system", "content": "prompt"}] + _calls(3, REFUSED) + _calls(1, failed, start_id=10)
    back_at_work = types.SimpleNamespace(agent_id="W3", state="worker_work", message_history=history)
    with quiet():
        assert not scheduler._detect_tool_execution_loops(back_at_work)          # 02:37:22: bounced back to report
    still_in_report = types.SimpleNamespace(agent_id="W3", state="worker_report",
                                            message_history=[{"role": "system", "content": "prompt"}] + _calls(4, REFUSED))
    with quiet():
        assert scheduler._detect_tool_execution_loops(still_in_report)         # a loop in worker_report still is one
    really_stuck = types.SimpleNamespace(agent_id="W3", state="worker_work",
                                         message_history=[{"role": "system", "content": "prompt"}] + _calls(4, failed))
    with quiet():
        assert scheduler._detect_tool_execution_loops(really_stuck)


# ================================================================== 2. a blocked loop override waits, then happens
def test_the_cooldown_left():
    from src.agents.cycle_components.next_step_scheduler import LOOP_COOLDOWN_WAIT_MAX, loop_cooldown_wait
    from src.agents.loop_coordinator import LoopCoordinator
    coordinator = LoopCoordinator()
    assert coordinator.remaining_cooldown("W3", "stuck_loop_nudge") == 0.0
    coordinator.record_intervention("W3", "stuck_loop_nudge")
    assert 40 < coordinator.remaining_cooldown("W3", "stuck_loop_nudge") <= 45
    coordinator.record_intervention("W3", "emergency_override", custom_cooldown=500)
    assert loop_cooldown_wait(coordinator, "W3", "emergency_override") == LOOP_COOLDOWN_WAIT_MAX
    assert loop_cooldown_wait(object(), "W3", "stuck_loop_nudge") == 0.0      # never raises


def _ctx(agent):
    return types.SimpleNamespace(
        agent=agent, all_tool_results=[], trigger_failover=False, needs_reactivation_after_cycle=False,
        is_retryable_error_type=False, retry_count=0, max_retries_for_cycle=3, cycle_completed_successfully=True,
        executed_tool_successfully_this_cycle=False, action_taken_this_cycle=True,
        state_change_requested_this_cycle=False, current_model_key_for_tracking="m",
        last_error_content="", last_error_obj=None, plan_submitted_this_cycle=False, thought_produced_this_cycle=False)


def _report_agent():
    agent = types.SimpleNamespace(agent_id="W3", agent_type="worker", state="worker_report", status="processing",
                                  message_history=[{"role": "system", "content": "prompt"}] + _calls(4, REFUSED),
                                  _consecutive_failed_cycles=0, _duplicate_tool_call_count=0)
    agent.set_status = lambda status, **kw: setattr(agent, "status", status)
    return agent


def _scheduler(coordinator):
    from src.agents.cycle_components.next_step_scheduler import NextStepScheduler
    manager = MagicMock()
    manager.schedule_cycle = AsyncMock(return_value="task")
    manager.send_to_ui = AsyncMock()
    manager.current_session_db_id = None
    manager.agents = {}
    manager.loop_coordinator = coordinator
    manager.workflow_manager.change_state.side_effect = lambda agent, state, **kw: setattr(agent, "state", state) or True
    return NextStepScheduler(manager), manager


def test_a_blocked_loop_override_waits_out_the_cooldown_instead_of_a_new_cycle():
    from src.agents.loop_coordinator import LoopCoordinator
    coordinator = LoopCoordinator()
    coordinator.record_intervention("W3", "stuck_loop_nudge", custom_cooldown=0.4)   # the work->report override
    scheduler, manager = _scheduler(coordinator)
    agent = _report_agent()
    started = time.monotonic()
    with quiet():
        run(scheduler.schedule_next_step(_ctx(agent)))
    assert time.monotonic() - started >= 0.35                    # no model call while the cooldown ran
    assert agent.state == "worker_wait"                         # then the override: report -> wait
    assert any("EMERGENCY SYSTEM OVERRIDE" in str(m.get("content")) for m in agent.message_history)
    assert manager.schedule_cycle.await_count == 1


def test_no_override_when_the_state_changed_while_waiting():
    from src.agents.loop_coordinator import LoopCoordinator
    coordinator = LoopCoordinator()
    coordinator.record_intervention("W3", "stuck_loop_nudge", custom_cooldown=0.3)
    scheduler, manager = _scheduler(coordinator)
    agent = _report_agent()

    async def go():
        async def stop_meanwhile():
            await asyncio.sleep(0.1)
            agent.state = "worker_wait"          # e.g. the user's Stop
        await asyncio.gather(scheduler.schedule_next_step(_ctx(agent)), stop_meanwhile())
    with quiet():
        run(go())
    manager.workflow_manager.change_state.assert_not_called()
    assert manager.schedule_cycle.await_count == 0


# ================================================================== 3. 'replace_lines': '[1, 5]' is a line range
def _fs(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    with quiet():
        return run(FileSystemTool().execute(agent_id="W3", agent_sandbox_path=tmp_path, scope="private", **kwargs))


def test_a_line_range_in_replace_lines(tmp_path):
    from src.tools.file_system import line_range_pair
    assert line_range_pair("[1, 5]") == (1, 5) and line_range_pair([21, 25]) == (21, 25)
    assert line_range_pair("3-7") == (3, 7) and line_range_pair("2..4") == (2, 4) and line_range_pair(("1", "2")) == (1, 2)
    for not_a_range in ("[5, 1]", "[0, 3]", "x = [1, 5]", "[1, 5, 9]", [True, 2], '[{"start_line": 1}]', None, "1"):
        assert line_range_pair(not_a_range) is None, not_a_range
    _write(tmp_path, {"tests/game.test.js": "".join(f"line {n}\n" for n in range(1, 9))})
    result = _fs(tmp_path, **RANGE_CALL)                                           # 02:34:55, no new text
    assert result["status"] == "error"
    assert result["message"] == (
        "Your 'replace_lines' '[1, 5]' was read as the line range 1-5, but the NEW text for those lines is missing, so "
        "NOTHING was changed. Send the complete new text of lines 1-5 as 'content': {\"action\": \"replace_lines\", "
        "\"filename\": \"tests/game.test.js\", \"replace_start_line\": 1, \"replace_end_line\": 5, \"content\": \"<the new "
        "lines 1-5>\"}. To replace the whole file, use 'write' with the complete new content.")
    assert (tmp_path / "tests/game.test.js").read_text().startswith("line 1\n")     # unchanged, not "[1, 5]"
    done = _fs(tmp_path, action="replace_lines", filename="tests/game.test.js", replace_lines=[2, 3], content="new\n")
    assert done["status"] == "success", done
    assert (tmp_path / "tests/game.test.js").read_text().splitlines()[:3] == ["line 1", "new", "line 4"]
    # Round 3d unchanged: with the line numbers given, 'replace_lines' text IS the new content
    kept = _fs(tmp_path, action="replace_lines", filename="tests/game.test.js", replace_start_line=1, replace_end_line=1,
               replace_lines="first\n")
    assert kept["status"] == "success" and (tmp_path / "tests/game.test.js").read_text().startswith("first\n")
    no_range = _fs(tmp_path, action="replace_lines", filename="tests/game.test.js", content="x\n")
    assert no_range["message"].startswith("'replace_start_line' and 'replace_end_line' parameters are required for "
                                          "'replace_lines' (the first and last line to replace), with the NEW text")
    assert '"filename": "tests/game.test.js"' in no_range["message"]


# ================================================================== 4. the planned test runner
STRUCTURE = ("# Project Directory Structure\n\n## Code Base Definitions & Standards\n\nTech stack: Node.js (standard library "
             "only). Testing framework: node --test for all tests. No additional frameworks or libraries.\n")


def test_no_other_test_runner_where_the_plan_says_node_test(tmp_path):
    from src.utils.js_test_checks import unplanned_runner_install_message
    _write(tmp_path, {"PROJECT_STRUCTURE.md": STRUCTURE, "src/game.js": "module.exports = {};\n"})
    refused = unplanned_runner_install_message(tmp_path, "cd /workspace && npm install --save-dev jest")   # 02:39:52
    assert refused.startswith("NOTHING was installed or run: PROJECT_STRUCTURE.md plans Node's BUILT-IN test runner "
                              "(`node --test`) for this project, which needs no install, and jest is another test framework.")
    assert "require('node:test')" in refused and "tell your PM" in refused
    for command in ("npm install --save-dev jest-environment-jsdom", "npm i -D mocha@10", "yarn add --dev vitest",
                    "npx jest tests/", "pnpm add -D @jest/globals", "npm install ts-jest @types/jest"):
        assert unplanned_runner_install_message(tmp_path, command), command
    for command in ("npm install", "npm install ws", "npm install --save-dev jsdom", "node --test tests/*.test.js",
                    "npm test", "npx http-server"):
        assert unplanned_runner_install_message(tmp_path, command) is None, command
    # a runner the project already lists, or a plan with that runner, is not refused
    _write(tmp_path, {"package.json": '{"devDependencies": {"jest": "^29.7.0"}}'})
    assert unplanned_runner_install_message(tmp_path, "npm install --save-dev jest") is None
    (tmp_path / "package.json").unlink()
    _write(tmp_path, {"PROJECT_STRUCTURE.md": "Testing framework: jest.\n"})
    assert unplanned_runner_install_message(tmp_path, "npm install --save-dev jest") is None


def test_the_executor_refuses_the_install(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.executor import ToolExecutor
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    _write(workspace, {"PROJECT_STRUCTURE.md": STRUCTURE})
    manager = types.SimpleNamespace(agents={"W3": types.SimpleNamespace(agent_id="W3", agent_type="worker",
                                                                      state="worker_work")})
    with quiet():
        result = run(ToolExecutor().execute_tool(
            "W3", tmp_path, "command_executor",
            {"action": "run_command", "command": "npm install --save-dev jest", "timeout": 120},
            project_name=PROJECT, session_name=SESSION, manager=manager))
    assert isinstance(result, str) and result.startswith("Error from tool 'command_executor': NOTHING was installed or run:")
    assert not (workspace / "node_modules").exists()


# ================================================================== 5. the diagnosis of the last failures
GAME_JS = """const GRID_SIZE = 20;
let snake = [];
let direction = 'UP';
let nextDirection = 'UP';
let score = 0;
let isGameOver = false;

function initGame() {
    snake = [{ x: 10, y: 10 }];
    nextDirection = 'UP';
    score = 0;
    isGameOver = false;
}

function moveSnake() {
    const head = { ...snake[0] };
    if (nextDirection === 'DOWN') head.y += 1; else head.y -= 1;
    snake.unshift(head);
    snake.pop();
}

function changeDirection(newDir) {
    nextDirection = newDir;
}

function gameOver() {
    isGameOver = true;
}

module.exports = {
    initGame,
    moveSnake,
    changeDirection,
    gameOver,
    getScore: () => score,
    getSnake: () => snake,
    isGameOver
};
"""
GAME_TEST_JS = """const assert = require('assert');
const {
  initGame,
  moveSnake,
  changeDirection,
  gameOver,
  getSnake,
  isGameOver
} = require('../src/game');

let testSnake = [];
let testNextDirection = 'UP';

describe('game', () => {
  it('moves down', () => {
    initGame();
    testNextDirection = 'DOWN';
    moveSnake();
    assert.strictEqual(getSnake()[0].y, 11);
  });

  it('ends the game', () => {
    initGame();
    gameOver();
    assert.strictEqual(isGameOver, true);
  });
});
"""
PLAYER_JS = """const players = [];

function registerPlayer(playerId, name) {
  if (players.find(p => p.id === playerId)) {
    throw new Error('Player already registered');
  }
  players.push({ id: playerId, name });
  return { playerId, isActive: true };
}

module.exports = { registerPlayer };
"""
PLAYER_TEST_JS = """const assert = require('assert');
const { registerPlayer } = require('../src/player');

describe('cleanup', () => {
  it('marks expired sessions', () => {
    registerPlayer('p1', 'TestPlayer');
    const session = registerPlayer('p1', 'TestPlayer');
    assert.ok(session);
  });
});
"""
JEST_OUTPUT = """FAIL tests/player.test.js
  cleanup
    ✕ marks expired sessions (2 ms)

  ● cleanup › marks expired sessions

    Player already registered

      3 | function registerPlayer(playerId, name) {
      4 |   if (players.find(p => p.id === playerId)) {
    > 5 |     throw new Error('Player already registered');
        |           ^
      6 |   }

      at registerPlayer (src/player.js:5:11)
      at Object.registerPlayer (tests/player.test.js:7:21)

FAIL tests/game.test.js
  game
    ✕ moves down (1 ms)
    ✕ ends the game

  ● game › moves down

    assert.strictEqual(received, expected)

      at Object.strictEqual (tests/game.test.js:19:12)

  ● game › ends the game

      at Object.strictEqual (tests/game.test.js:25:12)

Test Suites: 2 failed, 2 total
Tests:       3 failed, 3 total
"""


@pytest.fixture
def snake_project(tmp_path):
    _write(tmp_path, {"src/game.js": GAME_JS, "tests/game.test.js": GAME_TEST_JS, "src/player.js": PLAYER_JS,
                      "tests/player.test.js": PLAYER_TEST_JS})
    return tmp_path


def test_a_value_the_module_exports_as_a_copy(snake_project):
    from src.utils.js_test_checks import value_export_hints
    hints = value_export_hints(snake_project, ["tests/game.test.js"])
    assert hints == [
        "src/game.js exports `isGameOver` as a VALUE: `module.exports` copied `let isGameOver` (line 6) once, when the "
        "file was loaded, and initGame() sets the module's own variable later (line 12) - the exported `isGameOver` never "
        "changes, so tests/game.test.js (line 25) always reads the first value. Export a function that returns the "
        "current value instead (`isGameOver: () => isGameOver` in module.exports, or a `get...()` function) and call it - "
        "in the tests and in every file that uses `isGameOver`. This is a mistake in src/game.js, not in the test."]
    # a module that exports a getter, or a constant, is fine
    fixed = GAME_JS.replace("    isGameOver\n};", "    isGameOver: () => isGameOver\n};")
    _write(snake_project, {"src/game.js": fixed})
    assert value_export_hints(snake_project, ["tests/game.test.js"]) == []


def test_the_tests_own_variables_standing_in_for_the_modules(snake_project):
    from src.utils.js_test_checks import _exported_functions, standin_variable_hints
    assert _exported_functions(GAME_JS) == ["initGame", "moveSnake", "changeDirection", "gameOver", "getScore", "getSnake"]
    hints = standin_variable_hints(snake_project, ["tests/game.test.js"])
    assert hints == [   # testSnake is only declared, never set: no stand-in
        "tests/game.test.js keeps its OWN variable `testNextDirection` (line 12) as if it were src/game.js's inner "
        "variable `nextDirection` (line 4): the module's functions only use THEIR variables, so setting `testNextDirection` "
        "(line 17) changes nothing for them, and an assertion on `testNextDirection` only checks the test's own value. Set "
        "the state through the module's own functions (initGame(), moveSnake(), changeDirection()) and read it through what "
        "it exports (getScore(), getSnake()). The code under test is not wrong for this."]
    _write(snake_project, {"tests/game.test.js": GAME_TEST_JS.replace("    initGame();\n    testNextDirection",
                                                                        "    initGame();\n    testSnake = [];\n    testNextDirection")})
    assert standin_variable_hints(snake_project, ["tests/game.test.js"])[0].startswith(
        "tests/game.test.js keeps its OWN variables `testSnake`, `testNextDirection` (e.g. `testSnake`, line 11) as if they "
        "were src/game.js's inner variables (`snake`, line 2)")
    # variables that are never set are no stand-ins
    _write(snake_project, {"tests/game.test.js": "const g = require('../src/game');\nlet testSnake = [];\n"})
    assert standin_variable_hints(snake_project, ["tests/game.test.js"]) == []


def test_an_error_the_code_raises_on_purpose(snake_project):
    from src.utils.test_diagnosis import thrown_on_purpose_hints
    hints = thrown_on_purpose_hints(snake_project, JEST_OUTPUT)
    assert hints == [
        "The error `Player already registered` is raised ON PURPOSE by src/player.js line 5 (`throw new Error('Player "
        "already registered');`), called from tests/player.test.js line 7: the code REFUSES what the test does there (e.g. "
        "registering the same id twice, a value it rejects, a state left by an earlier step of the same test). Read that "
        "test line first: change the test's data or steps, or - if the test means to check this refusal - expect it with "
        "`assert.throws(() => ..., /...)`. Change the code only if its check itself is wrong; do not remove it to make a "
        "test pass."]
    # node --test prints absolute paths
    absolute = (f"    at registerPlayer ({snake_project}/src/player.js:5:11)\n"
                f"    at TestContext.<anonymous> ({snake_project}/tests/player.test.js:7:21)\n")
    assert "src/player.js line 5" in thrown_on_purpose_hints(snake_project, absolute)[0]
    # an error raised by a TEST file, or a line that does not throw, is not "on purpose"
    assert thrown_on_purpose_hints(snake_project, "      at Object.strictEqual (tests/game.test.js:25:12)\n") == []
    assert thrown_on_purpose_hints(snake_project, "      at moveSnake (src/game.js:17:5)\n"
                                                  "      at Object.<anonymous> (tests/game.test.js:18:5)\n") == []


def test_an_error_python_code_raises_on_purpose(tmp_path):
    from src.utils.test_diagnosis import thrown_on_purpose_hints
    _write(tmp_path, {
        "src/auth.py": "def register(players, name):\n    if name in players:\n"
                       "        raise ValueError(f'Player {name} already registered')\n    players.add(name)\n",
        "tests/test_auth.py": "from auth import register\n\ndef test_twice():\n    p = set()\n    register(p, 'a')\n"
                              "    register(p, 'a')\n"})
    output = """_________________________________ test_twice __________________________________

    def test_twice():
        p = set()
        register(p, 'a')
>       register(p, 'a')

tests/test_auth.py:6:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def register(players, name):
        if name in players:
>           raise ValueError(f'Player {name} already registered')
E           ValueError: Player a already registered

src/auth.py:3: ValueError
"""
    hints = thrown_on_purpose_hints(tmp_path, output)
    assert len(hints) == 1 and hints[0].startswith(
        "The error `Player {name} already registered` is raised ON PURPOSE by src/auth.py line 3 (`raise ValueError(f'Player "
        "{name} already registered')`), called from tests/test_auth.py line 6: the code REFUSES what the test does there")
    assert "`with pytest.raises(...):`" in hints[0]


def test_the_failed_run_diagnosis_names_all_three(snake_project):
    from src.utils.test_diagnosis import clear_cache, diagnose_failed_test_run
    clear_cache()
    with quiet():
        text = run(diagnose_failed_test_run(snake_project, "node --test tests/", JEST_OUTPUT))
    assert "[Framework check - errors the project code raises ON PURPOSE:]" in text
    assert "[Framework check - values a module exports as a COPY:]\n- src/game.js exports `isGameOver` as a VALUE" in text
    assert "[Framework check - mistakes in the test file itself:]" in text and "keeps its OWN variable `testNextDirection`" in text
    clear_cache()

# END OF FILE tests/unit/test_round4l.py
