"""
Round 4a: the run of 2026-10-27 18:11-19:26 (WiFiSnake_Game, qwen3.5:4b-q4_K_M; 7 of 8 kick-off tasks finished).

* PM1 re-created FINISHED work in other words 4 times (index.html, README.md twice, player.js) - one of them under the label
  of the existing "Kick-off Task 5".
* W3 (gemma4:12b after a failover) wrote 'tests/test_game/test_game.js`,force_overwrite:true': a file of that name.
* tests/test_player/test_player.js used `player.players` / `player.nextPlayerId`, which src/player.js keeps inside.
* src/server.js required 'ws' without a package.json - "Cannot find module 'ws'".
* `node --test tests/test_game/test_game.js` timed out 6 times: a test called game.startGameLoop() and never stopped it.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFiSnake_Game", "startup_1793095902"
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


def _workspace(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# ======================================================================================== 1. finished work again
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


class Agent:
    def __init__(self, agent_id, agent_type="worker", state="worker_wait"):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "idle"
        self.current_task_id = self.active_task_id = None
        self.message_history, self.read_message_ids = [], {}


def _manager(*agents):
    return types.SimpleNamespace(agents={a.agent_id: a for a in agents}, current_project=PROJECT, current_session=SESSION)


def _add(call, manager, text, assignee=None, caller="framework", **extra):
    args = {"assignee_agent_id": assignee} if assignee else {}
    return call(caller, manager, action="add_task", description=text, project_filter=PROJECT, **args, **extra)


def _finish(call, manager, uuid):
    assert call("framework", manager, action="complete_task", task_id=uuid)["status"] == "success"


FINISHED = [
    "Kick-off Task 2: Implement the player system in src/player.js including player registration, naming, joining/leaving "
    "functionality, and persistent score storage",
    "Kick-off Task 5: Implement the mobile responsiveness and game UI in src/index.html with touch controls and responsive "
    "layout",
    "Kick-off Task 7: Write docs/README.md with instructions for hosting the game on the local network and playing on mobile "
    "devices",
]


@needs_tw
def test_a_pm_does_not_recreate_finished_work_in_other_words(tasks):
    pm1 = Agent("PM1", "pm", "pm_manage")
    manager = _manager(pm1, Agent("W2"), Agent("W3"))
    for text in FINISHED:
        _finish(tasks, manager, _add(tasks, manager, text, "W2")["task_uuid"])
    copies = [  # what PM1 created at 18:29:37, 19:04:44, 19:07:11 and 19:07:21
        dict(title="Kick-off Task 5: Implement mobile responsiveness and touch controls",
             description="Implement responsive design for mobile devices in src/index.html with touch controls."),
        dict(title="Write README.md documentation",
             description="Write comprehensive README.md in docs/ with instructions for hosting the WiFiSnake_Game."),
        dict(title="Implement player module functionality",
             description="Implement the player.js module with player state management, movement logic and score tracking."),
    ]
    for copy in copies:
        result = tasks("PM1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W2", **copy)
        assert result["status"] == "error" and result.get("error_type") == "finished_duplicate", (copy, result)
        assert "already made by the FINISHED task(s)" in result["message"]
    # a problem in a finished file, or a task with a new file of its own, may be created
    fixed = tasks("PM1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W2",
                  description="Fix: the touch controls do not move the snake in src/index.html")
    assert fixed["status"] == "success", fixed
    new_file = tasks("PM1", manager, action="add_task", project_filter=PROJECT, assignee_agent_id="W3",
                     description="Implement the sound effects in src/sound.js and load it from src/index.html")
    assert new_file["status"] == "success", new_file


def test_the_files_check_is_for_pms_only_and_not_for_the_framework():
    from src.tools.project_management import finished_files_duplicate_error
    manager = _manager(Agent("PM1", "pm", "pm_manage"), Agent("W2"))
    tw = types.SimpleNamespace()   # never reached: the caller decides first
    assert finished_files_duplicate_error(tw, "framework", manager, None, "Write docs/README.md", PROJECT) is None
    assert finished_files_duplicate_error(tw, "W2", manager, None, "Write docs/README.md", PROJECT) is None


@needs_tw
def test_a_new_task_does_not_take_the_label_of_an_existing_planned_task(tasks):
    pm1 = Agent("PM1", "pm", "pm_manage")
    manager = _manager(pm1, Agent("W2"))
    _add(tasks, manager, FINISHED[1], "W2")
    result = tasks("PM1", manager, action="add_task", project_filter=PROJECT,
                   title="Kick-off Task 5: Add a pause button", description="Add a pause button to src/pause.js")
    assert result["status"] == "success", result
    assert not str(result.get("description", "")).startswith("Kick-off Task 5")
    tw = __import__("src.tools.project_management", fromlist=["x"]).ProjectManagementTool()._get_taskwarrior_instance(
        PROJECT, SESSION)
    assert tw.tasks.get(uuid=result["task_uuid"])["description"] == "Add a pause button: Add a pause button to src/pause.js"
    # a label no task has yet stays (the kick-off workflow writes its own labels)
    labelled = tasks("PM1", manager, action="add_task", project_filter=PROJECT, description="Kick-off Task 9: Write src/x.js")
    assert labelled["status"] == "success"
    assert tw.tasks.get(uuid=labelled["task_uuid"])["description"] == "Kick-off Task 9: Write src/x.js"


def test_the_label_helpers():
    from src.tools.project_management import strip_planned_label
    assert strip_planned_label("Kick-off Task 5: Implement X") == "Implement X"
    assert strip_planned_label("Update 2 Task 1: Fix Y") == "Fix Y"
    assert strip_planned_label("Implement Z") == "Implement Z" and strip_planned_label(None) is None


# ======================================================================================== 2. parameters in a file path
def test_parameters_written_into_a_path_are_split_off():
    from src.tools.executor import split_parameters_from_path
    args = {"action": "write", "filename": "tests/test_game/test_game.js`,force_overwrite:true", "content": "x"}
    note = split_parameters_from_path(args)
    assert args == {"action": "write", "filename": "tests/test_game/test_game.js", "content": "x", "force_overwrite": True}
    assert "the path is 'tests/test_game/test_game.js'" in note and "force_overwrite=True was set" in note
    args = {"action": "read", "filename": 'src/a.js", "start_line": 3'}
    split_parameters_from_path(args)
    assert args == {"action": "read", "filename": "src/a.js", "start_line": 3}
    args = {"filename": "x.js,force_overwrite=false", "force_overwrite": True}   # the call's own value wins
    split_parameters_from_path(args)
    assert args == {"filename": "x.js", "force_overwrite": True}
    for plain in ("src/ok.js", "docs/my file.md", "a,b.txt"):
        args = {"filename": plain}
        assert split_parameters_from_path(args) is None and args == {"filename": plain}


def test_the_executor_writes_the_real_file(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.tools.executor import ToolExecutor
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    _workspace(workspace, {"tests/test_game/test_game.js": "// old\n"})
    with quiet():
        answer = run(ToolExecutor().execute_tool(
            "W3", workspace.parent, "file_system",
            {"action": "write", "filename": "tests/test_game/test_game.js`,force_overwrite:true", "content": "// new\n"},
            project_name=PROJECT, session_name=SESSION, manager=_manager(Agent("W3", state="worker_work"))))
    assert (workspace / "tests" / "test_game" / "test_game.js").read_text(encoding="utf-8") == "// new\n", answer
    assert sorted(p.name for p in (workspace / "tests" / "test_game").iterdir()) == ["test_game.js"]


# ======================================================================================== 3. the code map
PLAYER_JS = """const players = new Map();
let nextPlayerId = 1;
const MAX_PLAYERS = 4;
const DEFAULTS = { score: 0 };
function registerPlayer(name) {
    if (!name) { return null; }
    const player = { id: nextPlayerId++, name };
    players.set(name, player);
    return player;
}
module.exports = { registerPlayer };
"""
TEST_PLAYER_JS = """const { describe, it, beforeEach } = require('node:test');
const assert = require('assert');
const player = require('../../src/player.js');
describe('Player System', () => {
    beforeEach(() => {
        player.players.clear();
        player.nextPlayerId = 1;
    });
    it('should register a new player', () => {
        assert.ok(player.registerPlayer('TestPlayer'));
    });
});
"""


def test_a_const_map_is_module_state_and_not_a_constant():
    from src.utils.code_map import javascript_outline
    lines = "\n".join(javascript_outline(PLAYER_JS))
    assert "module state (inside the file - a test can NOT read or set it, only the exported functions change it): " \
           "players = new Map(), nextPlayerId = 1" in lines
    assert "constants: MAX_PLAYERS = 4, DEFAULTS = {score}" in lines


def test_names_read_from_the_module_object_that_it_does_not_export(tmp_path):
    from src.utils.code_map import missing_names_hint, file_outline
    root = _workspace(tmp_path, {"src/player.js": PLAYER_JS, "tests/test_player/test_player.js": TEST_PLAYER_JS})
    hint = missing_names_hint(root, "tests/test_player/test_player.js")
    assert hint.startswith("tests/test_player/test_player.js takes `players`, `nextPlayerId` from src/player.js, which "
                           "does not export them (it exports: registerPlayer)")
    assert "PROBLEM: tests/test_player/test_player.js takes `players`" in file_outline(root, "tests/test_player/test_player.js")
    good = TEST_PLAYER_JS.replace("player.players.clear();\n        player.nextPlayerId = 1;\n", "")
    _workspace(root, {"tests/test_player/test_player.js": good})
    assert missing_names_hint(root, "tests/test_player/test_player.js") is None


# ======================================================================================== 4. a package without package.json
def test_a_required_npm_package_without_package_json_is_a_link_problem(tmp_path):
    from src.tools.project_links_check import link_problems
    server = "const WebSocket = require('ws');\nconst http = require('http');\nmodule.exports = { start() {} };\n"
    root = _workspace(tmp_path, {"src/server.js": server})
    problems = link_problems(root, root / "src" / "server.js")
    assert problems == ["src/server.js requires the npm package 'ws', but the project has NO package.json and 'ws' is not "
                        "installed, so the file fails to load with \"Cannot find module 'ws'\". Create package.json in the "
                        "workspace root with \"dependencies\": {\"ws\": \"*\"} and run `npm install`, or use only Node's "
                        "built-in modules (http, fs, path, events, ...)."]


# ======================================================================================== 5. a timer the test never stops
GAME_JS = """let gameLoopId = null;
function moveSnake() {}
function startGameLoop() {
    gameLoopId = setInterval(moveSnake, 100);
}
function stopGameLoop() {
    if (gameLoopId) {
        clearInterval(gameLoopId);
        gameLoopId = null;
    }
}
function startGame() {
    startGameLoop();
}
module.exports = { startGameLoop, stopGameLoop, startGame };
"""
TEST_GAME_JS = """const { describe, it } = require('node:test');
const assert = require('assert');
const game = require('../../src/game.js');
describe('loop', () => {
    it('should start game loop with setInterval', () => {
        game.startGameLoop();
        assert.ok(true);
    });
    it('should stop the loop', () => {
        game.startGameLoop();
        game.stopGameLoop();
        game.stopGameLoop();
    });
});
"""


def test_a_test_that_starts_a_timer_and_never_stops_it_is_named(tmp_path):
    from src.utils.code_map import open_timer_problems
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/test_game/test_game.js": TEST_GAME_JS})
    problems = open_timer_problems(root, "tests/test_game/test_game.js")
    assert problems == ["tests/test_game/test_game.js calls `startGameLoop()` (the test at line 5), which starts a "
                        "setInterval/setTimeout in src/game.js (line 3), and does not stop it every time: an open timer keeps "
                        "Node running, so the test run NEVER ends. To fix: call `stopGameLoop()` in an afterEach()/after() "
                        "hook (take afterEach from require('node:test'))."]  # Round 4g: no 'of the test' (put inside it)
    # a function that calls the starter starts it too; an afterEach that stops it ends the problem
    _workspace(root, {"tests/test_game/test_game.js": TEST_GAME_JS.replace("game.startGameLoop();\n        assert",
                                                                           "game.startGame();\n        assert")})
    assert "calls `startGame()`" in open_timer_problems(root, "tests/test_game/test_game.js")[0]
    hooked = TEST_GAME_JS.replace("const { describe, it }", "const { describe, it, afterEach }").replace(
        "describe('loop', () => {", "describe('loop', () => {\n    afterEach(() => game.stopGameLoop());")
    _workspace(root, {"tests/test_game/test_game.js": hooked})
    assert open_timer_problems(root, "tests/test_game/test_game.js") == []


def test_a_timer_started_when_the_module_is_loaded(tmp_path):
    from src.utils.code_map import open_timer_problems
    root = _workspace(tmp_path, {"src/tick.js": "setInterval(() => {}, 1000);\nmodule.exports = {};\n",
                                 "tests/tick.test.js": "const tick = require('../src/tick.js');\n"})
    assert open_timer_problems(root, "tests/tick.test.js")[0].startswith(
        "src/tick.js starts a timer when it is LOADED (line 1)")


def test_the_timeout_hint_and_the_write_note_name_the_timer(tmp_path):
    from src.tools.project_links_check import link_notes_text, test_hang_hint
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/test_game/test_game.js": TEST_GAME_JS})
    hint = test_hang_hint(root, "node --test tests/test_game/test_game.js", cpu_busy=0.0, output="# Game Over! Score: 0")
    assert "[Framework hint - why the TEST run did not end:]" in hint and "calls `startGameLoop()`" in hint
    note = link_notes_text(root, "tests/test_game/test_game.js", TEST_GAME_JS)
    assert "[Framework note - the file WAS saved:]" in note and "calls `startGameLoop()`" in note


@pytest.mark.skipif(not HAVE_NODE, reason="node not installed")
def test_the_timer_hint_is_right_node_ends_once_the_hook_stops_it(tmp_path):
    import subprocess
    hooked = TEST_GAME_JS.replace("const { describe, it }", "const { describe, it, afterEach }").replace(
        "describe('loop', () => {", "describe('loop', () => {\n    afterEach(() => game.stopGameLoop());")
    root = _workspace(tmp_path, {"src/game.js": GAME_JS, "tests/test_game/test_game.js": hooked})
    done = subprocess.run(["node", "--test", "tests/test_game/test_game.js"], cwd=root, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    _workspace(root, {"tests/test_game/test_game.js": TEST_GAME_JS})
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(["node", "--test", "tests/test_game/test_game.js"], cwd=root, capture_output=True, timeout=8)
