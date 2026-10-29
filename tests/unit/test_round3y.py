"""
Round 3y: the run of 2026-10-27 11:26-14:08 (WiFi_Snake_Game, qwen3.5:4b-q4_K_M for every agent on a remote Ollama).

* Tests that did not match the code: every task started with a wiped history, 3 of the 5 test tasks went to a worker who
  never wrote the code, and tests/test_game.js set and read src/game.js's inner `snake`, `food`, `score` (2h20m on
  "Initial snake length should be 3") -> the CODE MAP (src/utils/code_map.py), rebuilt from disk at every worker call.
* "Kick-off Task 6: Write pytest tests for game logic in tests/test_game.js": W3 wrote Python into the .js file.
* 13:44:44 W4 changed the framework conftest.py with code_editor (`__name__` -> `__name`): every pytest run failed after.
* 11:37:33 a SAVED code_editor edit whose note quoted "ModuleNotFoundError" counted as a failed call.
* 11:56:46 W4 started Kick-off Task 10 (tests of src/api.py) while it waited for Task 5 (src/api.py); 13:20:54 finished it.
* 11:34:19 W1 (Task 1, src/game.js) created tests/test_game.js, the file of the unassigned Kick-off Task 6.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Game", "startup_1793071586"

HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_tw = pytest.mark.skipif(not HAVE_TW, reason="real Taskwarrior (task + tasklib) not installed")
HAVE_NODE = shutil.which("node") is not None


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture(autouse=True)
def _clean_state():
    from src.utils import file_authorship, code_map
    file_authorship.reset()
    code_map._cache.clear()
    yield
    file_authorship.reset()
    code_map._cache.clear()


def _workspace(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# --- the run's files (shortened) ----------------------------------------------------------------------------------------
GAME_JS = """/**
 * WiFi Snake Game - Core Game Logic
 */
const GRID_SIZE = 20;
const GAME_SPEED = 150; // ms per frame
let snake = [];
let food = {};
let direction = 'UP';
let score = 0;
let gameRunning = false;
let gameCanvas = null;

function initCanvas() {
    gameCanvas = document.getElementById('gameCanvas');
}

function initSnake() {
    snake = [{ x: 10, y: 10 }, { x: 10, y: 11 }, { x: 10, y: 12 }];
}

function checkWallCollision() {
    const head = snake[0];
    if (head.x < 0 || head.x >= GRID_SIZE) {
        return true;
    }
    [1, 2].forEach(v => { return v; });
    return false;
}

function initGame() {
    initCanvas();
    initSnake();
}

module.exports = {
    initGame,
    getScore: () => score,
    getSnake: () => snake,
    getDirection: () => direction
};
"""

PLAYER_MANAGER_JS = """const STORAGE_KEY = 'wifi_snake_players';
let players = [];
let nextPlayerId = 1;

class Player {
    constructor(id, name) {
        this.id = id;
        this.name = name;
    }
}

function init() {
    const storedPlayers = localStorage.getItem(STORAGE_KEY);
}

/**
 * Register a new player with a name
 * @param {string} name - Player's name
 * @returns {Player} - The created player object
 */
function registerPlayer(name) {
    if (!name || name.trim() === '') {
        return null;
    }
    const existingPlayer = players.find(p => p.name === name);
    if (existingPlayer) {
        return existingPlayer;
    }
    const newPlayer = new Player(nextPlayerId, name.trim());
    players.push(newPlayer);
    return newPlayer;
}

function initPlayerManager() {
    init();
}

module.exports = { initPlayerManager, registerPlayer };
"""

# tests/test_game.js as W1 wrote it (11:35:16): it sets and reads the module's inner variables by name
TEST_GAME_JS = """const { initGame, getSnake, getScore } = require('../src/game.js');
function assert(condition, message) {
    if (!condition) { throw new Error(message); }
}
function testInitGame() {
    // Reset state
    snake = [];
    score = 0;
    initGame();
    assert(snake.length === 3, 'Initial snake length should be 3 - snake has 3 parts');
}
testInitGame();
"""

TEST_GAME_JS_GOOD = """const { describe, it } = require('node:test');
const assert = require('node:assert');
global.document = { getElementById: () => ({}) };
const game = require('../src/game.js');
describe('game', () => {
    it('starts with a snake of 3', () => {
        const snake = game.getSnake();
        game.initGame();
        assert.strictEqual(game.getSnake().length, 3);
        const data = { snake: 1, score: 2 };
        assert.ok(data.snake);
    });
});
"""


# ======================================================================================== 1. the code map: outlines
def test_a_javascript_module_outline_shows_its_exports_and_what_stays_inside():
    from src.utils.code_map import javascript_outline
    lines = "\n".join(javascript_outline(GAME_JS))
    assert "EXPORTS (all that a test or another file can use): initGame(), getScore() -> score, getSnake() -> snake" in lines
    assert "internal functions (NOT exported, a test cannot call them): initCanvas(), initSnake()" in lines
    assert "checkWallCollision() -> true | false" in lines                     # the callback's return is not its own
    assert "module state (inside the file - a test can NOT read or set it" in lines and "snake = []" in lines
    assert "constants: GRID_SIZE = 20, GAME_SPEED = 150" in lines and "ms per frame" not in lines
    assert "uses browser globals (a Node.js test must provide them before it loads the file): document" in lines


def test_jsdoc_return_types_and_the_returns_of_the_code_are_both_shown():
    from src.utils.code_map import javascript_outline
    lines = "\n".join(javascript_outline(PLAYER_MANAGER_JS))
    assert ("registerPlayer(name) -> {Player} (the code returns: null | existingPlayer | newPlayer)" in lines), lines
    assert "class Player - create with new Player(id, name) (NOT exported)" in lines
    assert "uses browser globals" in lines and "localStorage" in lines
    assert "internal functions (NOT exported, a test cannot call them): init()" in lines


def test_exported_names_of_every_export_style():
    from src.utils.code_map import js_exported_names
    assert js_exported_names("module.exports = { a, b: x, c() { return 1; }, d: () => 2, ...rest };") == ["a", "b", "c", "d"]
    assert js_exported_names("exports.one = 1;\nmodule.exports.two = function () {};") == ["one", "two"]
    assert js_exported_names("export function f() {}\nexport const g = 1;\nexport { h, i as j };\nexport default K;") == \
        ["f", "g", "h", "j", "default"]
    assert js_exported_names("class Game {}\nmodule.exports = Game;") == ["Game (the whole export)"]
    assert js_exported_names("// module.exports = { fake }\nconst s = 'module.exports = { fake2 }';") == []


def test_a_python_outline():
    from src.utils.code_map import python_outline
    source = ("import os\nfrom src.store import Store\nMAX = 3\ncache = {}\n\n"
              "class Snake(Base):\n    speed = 1\n    def __init__(self, x, y=0):\n        self.x = x\n"
              "    def move(self, d):\n        if d:\n            return self.x\n        return None\n\n"
              "def make(a, *b, c=2) -> int:\n    return a\n\ndef find(x):\n    if x:\n        return {'x': x}\n    return None\n\n"
              "def _helper():\n    pass\n\nif __name__ == '__main__':\n    make(1)\n")
    lines = "\n".join(python_outline(source))
    assert "functions: make(a, *b, c=2) -> int, find(x) -> {'x': x} | None" in lines
    assert ("class Snake(Base) - create with Snake(x, y=0); methods: move(d) -> self.x | None; class attributes (shared "
            "by ALL instances): speed") in lines
    assert "internal helpers (leading '_'): _helper()" in lines
    assert "constants: MAX = 3" in lines and "module-level variables (one copy, shared by every importer and test): cache" in lines
    assert "runs code only when started directly" in lines
    assert python_outline("def broken(:\n") is None


def test_a_test_file_outline_names_what_it_loads_and_its_tests(tmp_path):
    from src.utils.code_map import file_outline
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/test_game.js": TEST_GAME_JS_GOOD,
                                 "tests/test_api.py": "from api import register\n\ndef test_one():\n    assert register('a')\n"})
    text = file_outline(root, "tests/test_game.js", "YOUR task's file")
    assert text.startswith("tests/test_game.js (") and "YOUR task's file" in text
    assert "loads: node:test -> {describe, it}, node:assert -> assert, ../src/game.js -> game" in text
    assert "tests (1): 'starts with a snake of 3'" in text and "PROBLEM" not in text
    assert "tests (1): test_one" in file_outline(root, "tests/test_api.py")
    assert file_outline(root, "src/missing.js", "YOUR task's file") == "src/missing.js - YOUR task's file, does NOT exist yet"
    assert file_outline(root, "README.md") == ""


# ======================================================================================== 2. tests against the module
def test_a_test_that_uses_the_modules_inner_variables_is_named(tmp_path):
    from src.utils.code_map import module_internal_hint, module_internal_uses
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/test_game.js": TEST_GAME_JS,
                                 "tests/good.test.js": TEST_GAME_JS_GOOD})
    uses = module_internal_uses(root, "tests/test_game.js", TEST_GAME_JS)
    assert uses == [("src/game.js", [("snake", 7), ("score", 8)], ["initGame", "getScore", "getSnake", "getDirection"])]
    hint = module_internal_hint(root, "tests/test_game.js")
    assert "tests/test_game.js uses `snake` (line 7), `score` (line 8): variable(s) INSIDE src/game.js" in hint
    assert "Use what src/game.js exports instead (e.g. getScore(), getSnake(), getDirection())" in hint
    # a local `const snake`, an object key, a property and a word in a string are not the module's variable
    assert module_internal_uses(root, "tests/good.test.js", TEST_GAME_JS_GOOD) == []
    assert module_internal_hint(root, "tests/good.test.js") is None


def test_names_a_test_takes_that_the_module_does_not_export(tmp_path):
    from src.utils.code_map import missing_names_hint
    test = "const { initGame, resetGame, getSnake } = require('../src/game');\n"
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/test_game.js": test})
    hint = missing_names_hint(root, "tests/test_game.js")
    assert hint.startswith("tests/test_game.js takes `resetGame` from src/game.js, which does not export it (it exports: "
                           "initGame, getScore, getSnake, getDirection)")
    _workspace(root, {"tests/test_game.js": "const { initGame } = require('../src/game');\n"})
    assert missing_names_hint(root, "tests/test_game.js") is None


@pytest.mark.skipif(not HAVE_NODE, reason="node not installed")
def test_the_hint_is_right_node_really_fails_the_runs_test(tmp_path):
    import subprocess
    good_game = GAME_JS.replace("function initCanvas() {\n    gameCanvas = document.getElementById('gameCanvas');\n}",
                                "function initCanvas() {}")
    root = _workspace(tmp_path, {"src/game.js": good_game, "tests/test_game.js": TEST_GAME_JS,
                                 "tests/good.test.js": TEST_GAME_JS_GOOD})
    bad = subprocess.run(["node", "tests/test_game.js"], cwd=root, capture_output=True, text=True, timeout=60)
    assert bad.returncode != 0 and "Initial snake length should be 3" in bad.stderr     # the code is right, the test is not
    good = subprocess.run(["node", "--test", "tests/good.test.js"], cwd=root, capture_output=True, text=True, timeout=60)
    assert good.returncode == 0, good.stdout + good.stderr


def test_the_hints_reach_the_write_note_and_the_failed_run_diagnosis(tmp_path):
    from src.tools.project_links_check import link_notes_text
    from src.utils.test_diagnosis import js_test_mistake_hints, node_test_failed_files
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/test_game.js": TEST_GAME_JS})
    note = link_notes_text(root, "tests/test_game.js", TEST_GAME_JS)
    assert "[Framework note - the file WAS saved:]" in note and "variable(s) INSIDE src/game.js" in note
    assert "error" not in note.lower()                                  # a saved write is not a failed call
    # a `node --test` (TAP) output: the file failed as a whole, and a test inside a file (its location)
    whole = ("# Subtest: tests/test_game.js\nnot ok 1 - tests/test_game.js\n  ---\n  duration_ms: 38.6\n"
             f"  location: '{root / 'tests' / 'test_game.js'}:1:1'\n  failureType: 'testCodeFailure'\n  ...\n1..1\n")
    hints = js_test_mistake_hints(root, whole)
    assert any("variable(s) INSIDE src/game.js" in hint for hint in hints), hints
    inner = ("    # Subtest: starts\n    not ok 1 - starts with 3\n      ---\n      duration_ms: 1.1\n"
             f"      location: '{root / 'tests' / 'test_game.js'}:9:5'\n      ...\n")
    assert node_test_failed_files(root, inner) == ["tests/test_game.js"]
    assert node_test_failed_files(root, "ok 1 - passes\n") == []
    assert node_test_failed_files(root, "not ok 1 - /elsewhere/x.js\n") == []


# ======================================================================================== 3. which files, the block
def test_the_files_of_a_test_task_and_the_code_its_tests_check(tmp_path):
    from src.utils.code_map import task_code_files, code_map_text
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "src/player_manager.js": PLAYER_MANAGER_JS,
                                 "src/leaderboard.js": "const playerManager = require('./player_manager.js');\n"
                                                       "function initLeaderboard() {}\nmodule.exports = { initLeaderboard };\n"})
    task = "Kick-off Task 7: Write pytest tests for player management in tests/test_player_manager.js"
    assert task_code_files(root, task) == [("tests/test_player_manager.js", "YOUR task's file"),
                                           ("src/player_manager.js", "the code your tests check")]
    text = code_map_text(root, task)
    assert text.startswith("[CODE MAP - the files of your task, read from disk by the framework just now]")
    assert "A test can use only what a module EXPORTS" in text
    assert "- tests/test_player_manager.js - YOUR task's file, does NOT exist yet" in text
    assert "- src/player_manager.js (" in text and "EXPORTS (all that a test or another file can use): " \
                                                    "initPlayerManager(), registerPlayer(name)" in text
    # a code task: its file and the project files it loads
    assert task_code_files(root, "Kick-off Task 3: score tracking in src/leaderboard.js") == [
        ("src/leaderboard.js", "YOUR task's file"), ("src/player_manager.js", "loaded by your task's files")]
    # the files changed for the task count too; a task without code files has no map
    assert task_code_files(root, "Write docs/README.md", changed=[root / "src" / "game.js"]) == [
        ("src/game.js", "YOUR task's file")]
    assert code_map_text(root, "Write docs/README.md") == ""


def test_a_test_file_that_loads_its_module_finds_it_by_the_load(tmp_path):
    from src.utils.code_map import task_code_files
    root = _workspace(tmp_path, {"src/engine.js": GAME_JS, "tests/game.test.js": TEST_GAME_JS.replace("../src/game.js",
                                                                                                      "../src/engine.js")})
    assert ("src/engine.js", "the code your tests check") in task_code_files(root, "Fix the tests in tests/game.test.js")


def test_the_block_has_a_size_limit(tmp_path):
    from src.utils.code_map import code_map_text
    files = {f"src/m{i}.js": GAME_JS for i in range(6)}
    root = _workspace(tmp_path, files)
    task = "Task: " + ", ".join(files)
    full = code_map_text(root, task)
    small = code_map_text(root, task, max_chars=1500)
    assert len(small) <= 1500 + 80 and len(small) < len(full)
    assert small.count("\n- src/m") >= 1 and "(more files: read them with file_system 'read')" in small


def test_the_problems_of_a_test_file_come_first(tmp_path):
    from src.utils.code_map import file_outline
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/test_game.js": TEST_GAME_JS})
    lines = file_outline(root, "tests/test_game.js", "YOUR task's file").split("\n")
    assert lines[1].startswith("  PROBLEM: tests/test_game.js uses `snake` (line 7)")


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


class Worker:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.persona = agent_id
        self.current_task_id = self.active_task_id = None
        self.message_history, self.read_message_ids = [], {}


def _manager(*agents):
    return types.SimpleNamespace(
        agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION,
        state_manager=types.SimpleNamespace(get_agent_team=lambda agent_id: f"team_{PROJECT}",
                                            get_agents_in_team=lambda team_id: list(agents)),
        workflow_manager=types.SimpleNamespace(resolve_state_alias=lambda agent_type, state: state))


def _add(call, manager, text, assignee=None, depends=None):
    args = {"assignee_agent_id": assignee} if assignee else {}
    if depends:
        args["depends"] = depends
    added = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert added["status"] == "success", added
    return added["task_uuid"]


TASK_2 = "Kick-off Task 2: Implement player management with naming and local storage persistence in src/player_manager.js"
TASK_7 = "Kick-off Task 7: Write JavaScript tests for player management in tests/test_player_manager.js"


@needs_tw
def test_the_code_map_block_for_a_worker_in_its_working_states(tasks, monkeypatch):
    from src.config.settings import settings
    from src.utils.code_map import code_map_block
    _workspace(tasks.workspace, {"src/player_manager.js": PLAYER_MANAGER_JS})
    w1, pm1 = Worker("W1", state="worker_work"), Worker("PM1", "pm", "pm_manage")
    manager = _manager(pm1, w1)
    w1.active_task_id = _add(tasks, manager, TASK_7, "W1")
    block = code_map_block(manager, w1)
    assert "src/player_manager.js (" in block and "the code your tests check" in block
    for state in ("worker_decompose", "worker_test"):
        w1.state = state
        assert code_map_block(manager, w1) == block
    w1.state = "worker_wait"
    assert code_map_block(manager, w1) == ""
    assert code_map_block(manager, pm1) == ""
    w1.state = "worker_work"
    monkeypatch.setattr(settings, "CODE_MAP_MAX_CHARS", 0)
    assert code_map_block(manager, w1) == ""                                    # switched off
    monkeypatch.setattr(settings, "CODE_MAP_MAX_CHARS", 4200)
    w1.active_task_id = None                                                    # no task: the injected text
    w1._injected_task_description = TASK_2
    assert "src/player_manager.js (" in code_map_block(manager, w1) and "YOUR task's file" in code_map_block(manager, w1)


def test_the_code_map_setting_has_a_default():
    from src.config.settings import settings
    assert isinstance(settings.CODE_MAP_MAX_CHARS, int) and settings.CODE_MAP_MAX_CHARS >= 0


@needs_tw
def test_the_prompt_assembler_adds_the_code_map_for_a_worker(tasks, monkeypatch):
    from src.agents.cycle_components.prompt_assembler import PromptAssembler
    _workspace(tasks.workspace, {"src/player_manager.js": PLAYER_MANAGER_JS})
    w1 = Worker("W1", state="worker_work")
    manager = _manager(w1)
    w1.active_task_id = _add(tasks, manager, TASK_7, "W1")
    w1.message_history = [{"role": "system", "content": "You are W1."}, {"role": "user", "content": "Go."}]
    w1._last_system_prompt_state = ("worker", "worker_work")
    w1.final_system_prompt = "You are W1."
    context = types.SimpleNamespace(agent=w1, final_system_prompt=None, history_for_call=None)
    with quiet():
        run(PromptAssembler(manager).prepare_llm_call_data(context))
    maps = [m for m in context.history_for_call if str(m.get("content", "")).startswith("[CODE MAP")]
    assert len(maps) == 1 and maps[0]["role"] == "system"
    assert context.history_for_call.index(maps[0]) > 1                      # after the stored history (prompt cache)
    assert all("[CODE MAP" not in str(m.get("content")) for m in w1.message_history)   # never stored


# ======================================================================================== 4. a cleared history
def test_the_decompose_to_work_clear_keeps_a_note_of_the_files():
    from src.agents.workflow_manager import AgentWorkflowManager
    history = [
        {"role": "system", "content": "prompt"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "file_system", "arguments": {"action": "read", "filename": "PROJECT_PLAN.md"}},
            {"id": "c2", "name": "file_system", "arguments": {"action": "write", "filename": "src/game.js", "content": "x"}},
            {"id": "c3", "name": "file_system", "arguments": {"action": "write", "filename": "src/bad.js", "content": "("}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "file_system", "content": "# Plan"},
        {"role": "tool", "tool_call_id": "c2", "name": "file_system", "content": "File 'src/game.js' written."},
        {"role": "tool", "tool_call_id": "c3", "name": "file_system",
         "content": "[Tool Execution Failed] Write rejected: not valid JavaScript"},
    ]
    agent = types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_decompose",
                                  message_history=list(history), current_task_id=None)
    with quiet():
        assert AgentWorkflowManager().change_state(agent, "worker_work") is True
    assert agent.message_history[0] == {"role": "system", "content": "prompt"}
    note = agent.message_history[-1]["content"]
    assert note.startswith("[Framework Note] Your history before this step was cleared, but nothing you did is lost: "
                           "you WROTE src/game.js (saved - the file holds that text now); you read PROJECT_PLAN.md.")
    assert "src/bad.js" not in note and len(agent.message_history) == 2


def test_the_carried_note_is_empty_without_file_calls():
    from src.utils.code_map import carried_files_note
    assert carried_files_note([{"role": "system", "content": "x"}, {"role": "user", "content": "go"}]) == ""
    assert carried_files_note(None) == ""


def test_the_activation_condense_note_no_longer_claims_the_worker_remembers():
    source = (Path(__file__).resolve().parents[2] / "src" / "agents" / "manager.py").read_text(encoding="utf-8")
    assert "You retain your knowledge and capabilities." not in source
    assert "you do NOT remember the code you or others wrote" in source


# ======================================================================================== 5. "pytest" for a .js test
FILES = ["src/game.js", "src/player_manager.js", "src/leaderboard.js", "src/server.py", "src/api.py", "tests/test_game.js",
         "tests/test_player_manager.js", "tests/test_leaderboard.js", "tests/test_server.py", "tests/test_api.py",
         "docs/README.md"]


def test_a_pytest_task_for_a_javascript_test_file_says_javascript():
    from src.workflows.pm_kickoff_workflow import test_language_task_text
    fixed = test_language_task_text("Write pytest tests for game logic in tests/test_game.js", FILES)
    assert fixed.startswith("Write JavaScript tests for game logic in tests/test_game.js [Framework note: "
                            "tests/test_game.js is a JavaScript test of src/game.js: write it in JavaScript with Node's "
                            "built-in runner")
    assert "Never write Python into a .js file" in fixed and "pytest" not in fixed.split("[Framework note")[0]
    assert test_language_task_text(fixed, FILES) == fixed                       # once
    # Python tests of Python code, code tasks and tests without a language word stay as they are
    for text in ("Write pytest tests for HTTP server in tests/test_server.py", "Implement core game logic in src/game.js",
                 "Write tests for game logic in tests/test_game.js"):
        assert test_language_task_text(text, FILES) == text


def test_a_run_all_tests_task_of_a_mixed_project_names_both_runners():
    from src.workflows.pm_kickoff_workflow import test_language_task_text
    text = "Run all pytest tests and verify the server can be accessed from mobile devices on the same WiFi network"
    fixed = test_language_task_text(text, FILES)
    assert fixed.startswith(text) and "Python tests (run with `python -m pytest -q`) AND JavaScript tests (run with " \
                                      "`node --test tests/test_*.js`): run BOTH" in fixed  # Round 4f: the files named
    js_only = [f for f in FILES if not f.endswith(".py")]
    assert "the tests of this project are JavaScript: run them with `node --test tests/test_*.js`, not pytest" in \
        test_language_task_text(text, js_only)
    assert test_language_task_text(text, [f for f in FILES if not f.endswith(".js")]) == text


@needs_tw
def test_the_kickoff_writes_the_corrected_task_text(tasks, monkeypatch):
    from src.workflows import pm_kickoff_workflow as kickoff
    renames, files = kickoff.PMKickoffWorkflow._javascript_test_plan(
        [], ["Implement core game logic in src/game.js", "Write pytest tests for game logic in tests/test_game.js"])
    assert renames == {} and "tests/test_game.js" in files                   # nothing to rename (Round 3s) ...
    assert "JavaScript tests" in kickoff.test_language_task_text(           # ... but the words are fixed (Round 3y)
        "Write pytest tests for game logic in tests/test_game.js", files)


# ======================================================================================== 6. the framework conftest
def test_any_change_of_the_framework_conftest_is_refused(tmp_path):
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST, framework_conftest_change_error
    (tmp_path / "conftest.py").write_text(FRAMEWORK_CONFTEST, encoding="utf-8")
    assert "conftest.py is managed by the framework" in framework_conftest_change_error(tmp_path, "./conftest.py")
    assert framework_conftest_change_error(tmp_path, "tests/conftest.py") is None
    (tmp_path / "conftest.py").write_text("import sys\n", encoding="utf-8")       # the project's own
    assert framework_conftest_change_error(tmp_path, "conftest.py") is None
    assert framework_conftest_change_error(None, "conftest.py") is None


def test_the_executor_refuses_code_editor_on_the_framework_conftest(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.executor import ToolExecutor
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    _workspace(workspace, {"conftest.py": FRAMEWORK_CONFTEST})
    # 13:44:44 W4: `type(finder).__name__` -> `type(finder).__name`
    args = {"action": "replace_chunks", "filename": "conftest.py",
            "chunks": [{"search": "type(finder).__name__", "replace": "type(finder).__name"}]}
    manager = _manager(Worker("W4", state="worker_work"))
    with quiet():
        answer = run(ToolExecutor().execute_tool("W4", workspace.parent, "code_editor", args, project_name=PROJECT,
                                                 session_name=SESSION, manager=manager))
    assert "Error from tool 'code_editor': conftest.py is managed by the framework" in str(answer), answer
    assert (workspace / "conftest.py").read_text(encoding="utf-8") == FRAMEWORK_CONFTEST
    with quiet():
        answer = run(ToolExecutor().execute_tool("W4", workspace.parent, "file_system",
                                                 {"action": "delete", "filename": "conftest.py"}, project_name=PROJECT,
                                                 session_name=SESSION, manager=manager))
    assert "conftest.py is managed by the framework" in str(answer) and (workspace / "conftest.py").is_file()


def test_a_framework_conftest_an_agent_changed_is_restored(tmp_path):
    from src.utils.file_authorship import record_file_change, last_writer
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST, refresh_framework_conftest
    conftest = tmp_path / "conftest.py"
    damaged = FRAMEWORK_CONFTEST.replace("type(finder).__name__", "type(finder).__name")
    assert damaged != FRAMEWORK_CONFTEST
    conftest.write_text(damaged, encoding="utf-8")
    assert refresh_framework_conftest(tmp_path) is False                      # nobody known changed it: left alone
    record_file_change(conftest, "W4", "task-1", "replace_chunks")
    with quiet():
        assert refresh_framework_conftest(tmp_path) is True
    assert conftest.read_text(encoding="utf-8") == FRAMEWORK_CONFTEST and last_writer(conftest) is None
    own = "import sys\n"                                                        # a project's own conftest: never
    conftest.write_text(own, encoding="utf-8")
    record_file_change(conftest, "W4", "task-1", "write")
    assert refresh_framework_conftest(tmp_path) is False and conftest.read_text(encoding="utf-8") == own


# ======================================================================================== 7. a saved edit is a success
def test_a_saved_code_editor_edit_with_a_note_is_not_a_failed_call():
    from src.agents.tool_activity import is_tool_result_failure
    saved = ("Successfully applied 1 edits to 'src/server.py'.\n[Framework check - the file WAS saved, but it does not work "
             "with the project yet:]\n- src/server.py imports 'game' (line 25), but that module is JavaScript code: this "
             "fails with \"ModuleNotFoundError: No module named 'game'\".")
    assert is_tool_result_failure("code_editor", saved) is False
    assert is_tool_result_failure("code_editor", "[Tool Execution Failed] Code edit failed: 0 chunks succeeded") is True
    assert is_tool_result_failure("code_editor", "Error from tool 'code_editor': 'src/game.js' was NOT changed") is True
    assert is_tool_result_failure("command_executor", "Successfully applied: error 1") is True   # other tools unchanged


def test_collect_py_counts_the_same_way():
    import importlib.util
    spec = importlib.util.spec_from_file_location("collect_for_test", Path(__file__).resolve().parents[2] / "collect.py")
    collect = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collect)
    assert collect.result_failed("code_editor", "Successfully applied 1 edits.\n... ModuleNotFoundError ...") is False
    assert collect.result_failed("code_editor", "[Tool Execution Failed] Code edit failed") is True


# ======================================================================================== 8. tasks that wait
TASK_5 = "Kick-off Task 5: Implement REST API endpoints in src/api.py"
TASK_10 = "Kick-off Task 10: Write pytest tests for API endpoints in tests/test_api.py"


@needs_tw
def test_a_worker_cannot_start_a_task_that_waits_for_an_unfinished_task(tasks):
    from src.agents.cycle_handler import AgentCycleHandler
    w4 = Worker("W4", state="worker_report")
    manager = _manager(w4)
    t5 = _add(tasks, manager, TASK_5, "W4")
    t10 = _add(tasks, manager, TASK_10, "W4", depends=t5)
    handler = types.SimpleNamespace(_manager=manager, _infer_worker_task=lambda agent_id: None)
    w4.active_task_id = t5
    with quiet(), pytest.raises(ValueError) as refused:
        AgentCycleHandler._handle_worker_task_tracking(handler, w4, "worker_work", t10)
    message = str(refused.value)
    assert f"Task '{t10}' ('{TASK_10}') can NOT be worked on yet: it waits for unfinished task(s) '{TASK_5}' ({t5})" in message
    assert f"Work on your task '{TASK_5}' instead: call the 'request_state' tool with state='worker_work' and task_id='{t5}'" in message
    assert w4.active_task_id == t5                                               # unchanged
    tw = __import__("src.tools.project_management", fromlist=["x"]).ProjectManagementTool()._get_taskwarrior_instance(
        PROJECT, SESSION)
    assert str(tw.tasks.get(uuid=t10)["task_progress"] or "todo") != "doing"
    # its own ready task can be started
    with quiet():
        AgentCycleHandler._handle_worker_task_tracking(handler, w4, "worker_work", t5)
    assert w4.active_task_id == t5 and tw.tasks.get(uuid=t5)["task_progress"] == "doing"


@needs_tw
def test_with_only_a_waiting_task_the_worker_is_told_to_wait(tasks):
    from src.agents.cycle_handler import AgentCycleHandler
    w1, w4 = Worker("W1", state="worker_work"), Worker("W4", state="worker_report")
    manager = _manager(w1, w4)
    t5 = _add(tasks, manager, TASK_5, "W1")
    t10 = _add(tasks, manager, TASK_10, "W4", depends=t5)
    handler = types.SimpleNamespace(_manager=manager, _infer_worker_task=lambda agent_id: None)
    with quiet(), pytest.raises(ValueError) as refused:
        AgentCycleHandler._handle_worker_task_tracking(handler, w4, "worker_work", t10)
    assert "You have no task you can work on now: call the 'request_state' tool with state='worker_wait'" in str(refused.value)


@needs_tw
def test_the_inferred_task_is_one_the_worker_can_work_on(tasks):
    from src.agents.cycle_handler import AgentCycleHandler
    w4 = Worker("W4")
    manager = _manager(w4)
    t5 = _add(tasks, manager, TASK_5)                                            # not W4's
    t10 = _add(tasks, manager, TASK_10, "W4", depends=t5)                        # W4's, waiting
    t11 = _add(tasks, manager, "Kick-off Task 11: Write docs/README.md", "W4")   # W4's, ready
    handler = types.SimpleNamespace(_manager=manager)
    assert AgentCycleHandler._infer_worker_task(handler, "W4") == t11
    tasks("framework", manager, action="modify_task", task_id=t11, task_progress="finished")
    assert AgentCycleHandler._infer_worker_task(handler, "W4") == t10           # only a waiting one left: as before


@needs_tw
def test_a_task_that_waits_cannot_be_marked_finished(tasks):
    w4, pm1 = Worker("W4", state="worker_work"), Worker("PM1", "pm", "pm_manage")
    manager = _manager(pm1, w4)
    t5 = _add(tasks, manager, TASK_5, "W1")
    t10 = _add(tasks, manager, TASK_10, "W4", depends=t5)
    w4.active_task_id = t10
    for caller in ("W4", "PM1"):
        result = tasks(caller, manager, action="modify_task", task_id=t10, task_progress="finished")
        assert result["status"] == "error" and result.get("error_type") == "waiting_task", result
        assert f"was NOT marked finished: it depends on unfinished task(s) '{TASK_5}' ({t5})" in result["message"]
        result = tasks(caller, manager, action="complete_task", task_id=t10)
        assert result["status"] == "error" and "it depends on unfinished task(s)" in result["message"], result
    assert tasks("framework", manager, action="modify_task", task_id=t10, task_progress="finished")["status"] == "success"


# ======================================================================================== 9. a planned task's file
@needs_tw
def test_a_worker_does_not_create_the_file_of_an_unassigned_planned_task(tasks):
    from src.tools.project_management import foreign_file_creation_error
    w1 = Worker("W1", state="worker_work")
    manager = _manager(w1)
    w1.current_task_id = _add(tasks, manager, "Kick-off Task 1: Implement core game logic in src/game.js", "W1")
    _add(tasks, manager, "Kick-off Task 6: Write JavaScript tests for game logic in tests/test_game.js")   # nobody's yet
    refusal = foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "tests/test_game.js")
    assert refusal.startswith("The file was NOT created: 'tests/test_game.js' is the file of the planned task 'Kick-off "
                              "Task 6: Write JavaScript tests for game logic in tests/test_game.js'")
    assert "which is not assigned yet - the worker who gets that task writes it" in refusal
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "src/game.js") is None       # its own file
    assert foreign_file_creation_error(manager, "W1", PROJECT, SESSION, "src/helpers.js") is None    # nobody's file


def test_the_worker_work_prompt_explains_the_code_map():
    import yaml
    prompts = yaml.safe_load((Path(__file__).resolve().parents[2] / "prompts.yaml").read_text(encoding="utf-8"))
    text = prompts["worker_work_prompt"]
    assert "**CODE MAP:** the [CODE MAP] block at the end of every step is read from disk just then" in text
    assert "{" not in text.split("**CODE MAP:**")[1].split("\n")[0]             # no format placeholders in the new line
