# START OF FILE tests/unit/test_round5g.py
"""
Round 5g: the run of 2026-11-16 21:02 .. 2026-11-17 07:39 (WiFi_Snake_Multiplayer_Game, startup_1794834131, qwen3.5:4b):
10.5 hours, 3,263 model calls, and Task 6 (tests) and Task 8 (verification) never finished.

1. W2 named a test block by its call head (function="describe('Client-side Game Rendering and Input Handling'") with the
   whole suite as code: no block had that "title", so the suite was ADDED after the last test - inside the last describe() -
   and three more calls nested tests/snake/snake.test.js into itself four times (2,479 lines, 99 KB).
2. replace_function with function='module.exports' and a complete exports object was refused 16 times ("defines nothing");
   the line edits that followed left src/server.js with five module.exports blocks and PM1 made three fix tasks for them.
3. `npm test` timed out 64 times and Task 6 was refused 17 times with "its tests do not END". The hanging file was
   tests/snake/snake.test.js (W2's): it loads public/client.js, the page's browser script, whose last lines run initClient()
   -> connectToServer() (a WebSocket) -> on close reconnectToServer() (a setTimeout whose id is never kept). The hint said
   "usually a server that starts listening when its file is loaded", the test's afterEach(() => stopGame()) counted as a
   stop although stopGame() clears another timer, and W1 rewrote ITS src/server.js for five hours.
4. public/client.js ended with an unguarded `module.exports = {...}` - "ReferenceError: module is not defined" in the browser.
5. W2 wrote tests/snake/snake.test.js while working on Task 3 (public/client.js) because Task 6 (tests/) was ALSO its own
   at that moment; PM1 then gave Task 6 to W1, and Task 3's finish was refused 44 times (5 h 10 min) for that test file.
"""
import asyncio
import logging
import shutil
import subprocess
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

PROJECT, SESSION = "WiFi_Snake_Multiplayer_Game", "startup_1794834131"
HAVE_NODE = shutil.which("node") is not None
HAVE_TW = shutil.which("task") is not None
try:
    import tasklib  # noqa: F401
except ImportError:  # pragma: no cover
    HAVE_TW = False
needs_node = pytest.mark.skipif(not HAVE_NODE, reason="node is not installed")
needs_tw_and_node = pytest.mark.skipif(not (HAVE_TW and HAVE_NODE), reason="Taskwarrior (task + tasklib) or node missing")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def _workspace(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _edit(sandbox, **kwargs):
    from src.tools.code_editor import CodeEditorTool
    with quiet():
        return run(CodeEditorTool().execute("W2", sandbox, scope="private", **kwargs))


# public/client.js of the run (shortened; the same structure: a WebSocket that reconnects with a setTimeout whose id is not
# kept, a game loop whose id is kept, the start at the end inside if/else)
CLIENT_JS = """let ws = null;
let gameLoopId = null;
let isGameRunning = false;

function connectToServer() {
    ws = new WebSocket('ws://' + window.location.host);
    ws.onclose = () => {
        console.log('Disconnected from server');
        reconnectToServer();
    };
}

function reconnectToServer() {
    setTimeout(() => {
        connectToServer();
    }, 1000);
}

function gameLoop() {
    gameLoopId = setTimeout(gameLoop, 100);
}

function startGame() {
    isGameRunning = true;
    gameLoop();
}

function stopGame() {
    isGameRunning = false;
    if (gameLoopId) {
        clearTimeout(gameLoopId);
        gameLoopId = null;
    }
}

function initClient() {
    connectToServer();
    document.addEventListener('keydown', () => startGame());
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initClient);
} else {
    initClient();
}
"""
CLIENT_EXPORTS = """
if (typeof module !== 'undefined') {
    module.exports = { connectToServer, reconnectToServer, startGame, stopGame };
}
"""
INDEX_HTML = """<!DOCTYPE html>
<html><body><canvas id="gameCanvas"></canvas><script src="client.js"></script></body></html>
"""
# tests/snake/snake.test.js of the run (shortened): browser globals mocked, an afterEach that stops the GAME loop
SNAKE_TEST_JS = """const { describe, it, afterEach } = require('node:test');
const assert = require('node:assert');

global.document = { readyState: 'complete', addEventListener: () => {} };
global.window = { location: { protocol: 'http:', host: 'localhost' } };
// as on the run's machine: the connection fails and closes (Node's own WebSocket does that, or not, by its network)
global.WebSocket = class { constructor() { setTimeout(() => this.onclose && this.onclose(), 5); } send() {} close() {} };

const { startGame, stopGame } = require('../../public/client.js');

describe('Client-side Game Rendering and Input Handling', () => {
    afterEach(() => {
        stopGame();
    });

    it('starts the game', () => {
        startGame();
        assert.ok(true);
    });
});
"""
SERVER_JS = """function createServer() {
    return { started: true };
}

module.exports = { createServer };

if (require.main === module) {
    createServer();
}
"""
SERVER_TEST_JS = """const { describe, it } = require('node:test');
const assert = require('node:assert');
const { createServer } = require('../../src/server.js');

describe('server', () => {
    it('creates', () => {
        assert.ok(createServer().started);
    });
});
"""
TASK_3 = "Kick-off Task 3: Build client-side game rendering and input handling in public/client.js"
TASK_6 = "Kick-off Task 6: Write automated tests for game logic and server functionality in tests/"


def _the_project(root: Path) -> Path:
    return _workspace(root, {"public/index.html": INDEX_HTML, "public/client.js": CLIENT_JS + CLIENT_EXPORTS,
                             "tests/snake/snake.test.js": SNAKE_TEST_JS, "src/server.js": SERVER_JS,
                             "tests/server/server.test.js": SERVER_TEST_JS})


# ======================================================================================== 1. a test block named by its call head
SUITE = """const { describe, it } = require('node:test');
const assert = require('node:assert');

describe('Client-side Game Rendering and Input Handling', () => {
    describe('CONFIG', () => {
        it('has a size', () => {
            assert.ok(true);
        });
    });

    describe('showGameOverModal', () => {
        it('shows', () => {
            assert.ok(true);
        });
    });
});
"""
NEW_SUITE = """describe('Client-side Game Rendering and Input Handling', () => {
    describe('CONFIG', () => {
        it('has a size', () => {
            assert.strictEqual(1, 1);
        });
    });
});"""


def test_the_title_of_a_call_head():
    from src.utils.symbols import block_title
    assert block_title("describe('Client-side Game Rendering and Input Handling'") == \
        "Client-side Game Rendering and Input Handling"
    assert block_title('it("moves right", () =>') == "moves right"
    assert block_title("test.only(`wraps`, async () => {") == "wraps"
    assert block_title("describe('cut off") == "cut off"
    assert block_title("'moves right'") == "moves right" and block_title("update") == "update"


@pytest.mark.parametrize("name", ["describe('Client-side Game Rendering and Input Handling'",
                                  "describe('Client-side Game Rendering and Input Handling', () =>",
                                  "Client-side Game Rendering and Input Handling"])
def test_the_suite_is_replaced_not_nested_into_itself(tmp_path, name):
    _workspace(tmp_path, {"t.test.js": SUITE})
    result = _edit(tmp_path, action="replace_function", filename="t.test.js", function=name, code=NEW_SUITE)
    assert result["status"] == "success", result
    assert "'Client-side Game Rendering and Input Handling' (was lines 4-16) now holds your new code" in result["message"]
    text = (tmp_path / "t.test.js").read_text()
    assert text.count("Client-side Game Rendering and Input Handling") == 1
    assert "assert.strictEqual(1, 1)" in text and "showGameOverModal" not in text


def test_a_name_of_no_block_takes_the_title_of_the_code(tmp_path):
    _workspace(tmp_path, {"t.test.js": SUITE})
    result = _edit(tmp_path, action="replace_function", filename="t.test.js", function="describe", code=NEW_SUITE)
    assert result["status"] == "success", result
    assert ("'function' was 'describe', which names no test block of 't.test.js'; your code is the block "
            "'Client-side Game Rendering and Input Handling', so that block was replaced.") in result["message"]
    assert (tmp_path / "t.test.js").read_text().count("describe('Client-side") == 1


def test_a_new_group_goes_beside_the_outer_suites(tmp_path):
    _workspace(tmp_path, {"t.test.js": SUITE})
    code = "describe('Server suite', () => {\n    it('starts', () => {\n        assert.ok(true);\n    });\n});"
    result = _edit(tmp_path, action="replace_function", filename="t.test.js", function="describe('Server suite'", code=code)
    assert result["status"] == "success", result
    assert "had no 'Server suite', so your code was ADDED after the test block 'Client-side Game Rendering" in result["message"]
    assert (tmp_path / "t.test.js").read_text().endswith("});\n\ndescribe('Server suite', () => {\n    it('starts', () => {\n"
                                                          "        assert.ok(true);\n    });\n});\n")
    # a single new test still goes after the last test, inside its group (Round 5c)
    single = _edit(tmp_path, action="replace_function", filename="t.test.js", function="hides",
                   code="it('hides', () => {\n    assert.ok(true);\n});")
    assert "ADDED after the test 'starts'" in single["message"]
    assert "    it('starts', () => {\n        assert.ok(true);\n    });\n\n    it('hides', () => {" in \
        (tmp_path / "t.test.js").read_text()


@needs_node
def test_the_replaced_suite_still_runs(tmp_path):
    _workspace(tmp_path, {"t.test.js": SUITE})
    _edit(tmp_path, action="replace_function", filename="t.test.js",
          function="describe('Client-side Game Rendering and Input Handling'", code=NEW_SUITE)
    done = subprocess.run(["node", "--test", "t.test.js"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "# tests 1" in done.stdout, done.stdout + done.stderr


# ======================================================================================== 2. the exports by their name
RUN_EXPORTS = """// Export functions for testing
module.exports = {
    connectToServer,
    reconnectToServer,
    // Exported getters for testing module state
    getIsGameRunning: () => isGameRunning,
    // Export reset function for tests
    resetGame: () => {
        stopGame();
        isGameRunning = false;
    }
};
"""


def test_the_exports_of_a_page_script_are_added_inside_a_guard(tmp_path):
    _workspace(tmp_path, {"public/index.html": INDEX_HTML, "public/client.js": CLIENT_JS})
    result = _edit(tmp_path, action="replace_function", filename="public/client.js", function="module.exports",
                   code=RUN_EXPORTS)
    assert result["status"] == "success", result
    assert "module.exports now holds 4 name(s), at the end of the file" in result["message"]
    assert "loaded by a page in the BROWSER, which has no `module`" in result["message"]
    text = (tmp_path / "public/client.js").read_text()
    assert text.endswith("""    initClient();
}

if (typeof module !== 'undefined') {
    module.exports = {
        connectToServer,
        reconnectToServer,
        getIsGameRunning: () => isGameRunning,
        resetGame: () => {
            stopGame();
            isGameRunning = false;
        }
    };
}
""")
    # sent again with one more name (as the run's `module.exports.ctx = ctx;`): ONE exports object, the names merged
    again = _edit(tmp_path, action="replace_function", filename="public/client.js", function="module.exports",
                  code=RUN_EXPORTS + "\nmodule.exports.ctx = ctx;\n")
    assert again["status"] == "success", again
    text = (tmp_path / "public/client.js").read_text()
    assert text.count("module.exports") == 1 and "        },\n        ctx\n    };\n}\n" in text


@needs_node
def test_the_guarded_page_script_loads_in_node_and_in_a_browser_like_scope(tmp_path):
    _workspace(tmp_path, {"public/index.html": INDEX_HTML, "public/client.js": CLIENT_JS.split("if (document.readyState")[0]})
    _edit(tmp_path, action="replace_function", filename="public/client.js", function="module.exports", code=RUN_EXPORTS)
    check = subprocess.run(["node", "--check", "public/client.js"], cwd=tmp_path, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    script = ("const vm = require('vm'); const fs = require('fs');"
              "vm.runInNewContext(fs.readFileSync('public/client.js', 'utf8'), {console});"
              "console.log(typeof require('./public/client.js').connectToServer);")
    done = subprocess.run(["node", "-e", script], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0 and done.stdout.strip() == "function", done.stdout + done.stderr


def test_several_exports_objects_become_one_and_statements_the_file_has_are_not_added(tmp_path):
    server = SERVER_JS.replace("module.exports = { createServer };\n",
                               "module.exports = {\n    createServer,\n    stopServer: () => null\n};\n\n"
                               "module.exports = { createServer };\n\nprocess.on('exit', () => {});\n")
    _workspace(tmp_path, {"src/server.js": server})
    code = "module.exports = {\n    createServer,\n    getGameState\n};\n\nprocess.on('exit', () => {});\n"
    result = _edit(tmp_path, action="replace_function", filename="src/server.js", function="module.exports", code=code)
    assert result["status"] == "success", result
    assert "the file's 2 module.exports objects were made ONE" in result["message"]
    assert "1 other statement(s) of your code the file has already were not added again" in result["message"]
    text = (tmp_path / "src/server.js").read_text()
    assert text.count("module.exports") == 1 and text.count("process.on('exit'") == 1
    assert "module.exports = {\n    createServer,\n    stopServer: () => null,\n    getGameState\n};" in text
    assert "if (typeof module" not in text  # a server file is no page script


def test_the_exports_without_a_function_name_and_what_is_left_alone():
    from src.utils.symbols import exports_block_edit
    source = "function draw() {}\n\nif (require.main === module) {\n    draw();\n}\n"
    chunks, what = exports_block_edit(source, "src/draw.js", None, "module.exports = { draw };")
    assert what == "module.exports now holds 1 name(s), before the start block (line 3)"
    assert chunks == [{"start_line": 2, "end_line": 2, "replace": "\nmodule.exports = {\n    draw\n};\n"}]
    # a name that IS a definition of the file is replace_function's; code that holds more than exports; other languages
    assert exports_block_edit(source, "src/draw.js", "draw", "module.exports = { draw };", ["draw"]) is None
    assert exports_block_edit(source, "src/draw.js", "module.exports", "function x() {}\nmodule.exports = { x };") is None
    assert exports_block_edit("def f():\n    pass\n", "x.py", "module.exports", "module.exports = { f };") is None
    # the file holds these names already
    assert exports_block_edit("function draw() {}\n\nmodule.exports = {\n    draw\n};\n", "src/draw.js", "exports",
                              "module.exports = { draw };") == ([], "the exports hold your names already")


# ======================================================================================== 3. what keeps a test run from ending
def test_load_time_text_keeps_only_what_runs_when_the_file_is_loaded():
    from src.utils.code_map import _blank_strings_and_comments, load_time_text
    loaded = load_time_text(_blank_strings_and_comments(CLIENT_JS))
    assert "initClient();" in loaded and "connectToServer();" not in loaded and "setTimeout" not in loaded
    assert "function initClient" not in loaded and "startGame()" not in loaded
    assert loaded.count("\n") == CLIENT_JS.count("\n")


def test_the_hanging_page_script_is_named_with_its_chain(tmp_path):
    from src.utils.code_map import open_timer_problems
    root = _the_project(tmp_path)
    problems = open_timer_problems(root, "tests/snake/snake.test.js")
    assert problems[0] == (
        "tests/snake/snake.test.js loads public/client.js, which runs `initClient()` as soon as it is LOADED (line 44) - "
        "initClient() -> connectToServer(): `connectToServer()` opens a WebSocket (line 6) (kept in `ws`); initClient() -> "
        "connectToServer() -> reconnectToServer(): `reconnectToServer()` starts a setTimeout/setInterval (line 14) whose id "
        "it does not keep, so nothing can stop it. An open timer or connection keeps Node running, so this test file NEVER "
        "ends, whatever its tests do (an afterEach() that stops another timer does not help). public/client.js is the "
        "BROWSER script of a page: loading it in a Node test runs the page itself. Run that start only in a browser - wrap "
        "it in `if (typeof module === 'undefined') { ... }` (a browser has no `module`, Node has) - or test only code that "
        "does not start anything when it is loaded (the game logic in its own module).")
    assert len(problems) == 1  # startGame() is stopped by the afterEach (stopGame clears gameLoopId)
    # a module that is no page script gets the server advice
    (root / "public/index.html").unlink()
    assert open_timer_problems(root, "tests/snake/snake.test.js")[0].endswith(
        "Start it from a function the program calls (in a server: under `if (require.main === module)`), not when "
        "public/client.js is loaded, and keep the timer's id so a stop function can clear it.")
    assert open_timer_problems(root, "tests/server/server.test.js") == []


def test_a_stop_function_counts_only_for_the_timer_it_clears(tmp_path):
    from src.utils.code_map import open_timer_problems
    started = CLIENT_JS.replace("if (document.readyState === 'loading') {\n    document.addEventListener("
                                "'DOMContentLoaded', initClient);\n} else {\n    initClient();\n}\n", "")
    test = SNAKE_TEST_JS.replace("const { startGame, stopGame }", "const { connectToServer, startGame, stopGame }").replace(
        "        startGame();\n", "        startGame();\n        connectToServer();\n")
    root = _workspace(tmp_path, {"public/client.js": started + CLIENT_EXPORTS, "tests/snake/snake.test.js": test})
    [problem] = open_timer_problems(root, "tests/snake/snake.test.js")
    assert problem.startswith("tests/snake/snake.test.js calls `connectToServer()` (the test at line 16), which starts a "
                              "setInterval/setTimeout in public/client.js (line 5)")
    assert ("To fix: `reconnectToServer()` starts a setTimeout/setInterval (line 14) whose id it does not keep, so nothing "
            "can stop it - keep its id in public/client.js") in problem
    # the Round 4a/4g rule stays: a hook that calls the stop function of the started timer is enough
    root = _workspace(tmp_path, {"tests/snake/snake.test.js": SNAKE_TEST_JS})
    assert open_timer_problems(root, "tests/snake/snake.test.js") == []


def test_the_hang_hint_reads_the_files_the_command_names(tmp_path):
    from src.tools.project_links_check import command_test_paths, test_hang_hint
    root = _the_project(tmp_path)
    named = lambda command: [p.relative_to(root.resolve()).as_posix() for p in command_test_paths(root.resolve(), command)]
    assert named("node --test tests/server/server.test.js") == ["tests/server/server.test.js"]
    assert named("npm test -- tests/snake/snake.test.js --timeout 5000") == ["tests/snake/snake.test.js"]
    assert named("node --test tests/") == ["tests/server/server.test.js", "tests/snake/snake.test.js"]
    assert named("cd /workspace && npm test") == [] and named("node --test tests/*.test.js") == []
    assert "public/client.js" not in test_hang_hint(root, "node --test tests/server/server.test.js", 0.0, "x")
    everything = test_hang_hint(root, "npm test", 0.0, "# Disconnected from server")
    assert "[Framework hint - why the TEST run did not end:]" in everything and "runs `initClient()`" in everything


@needs_node
def test_node_really_hangs_on_the_page_script_and_ends_with_the_advised_guard(tmp_path):
    root = _the_project(tmp_path)
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(["node", "--test", "tests/snake/snake.test.js"], cwd=root, capture_output=True, timeout=8)
    guarded = (CLIENT_JS + CLIENT_EXPORTS).replace(
        "if (document.readyState === 'loading') {", "if (typeof module === 'undefined') {\nif (document.readyState === "
        "'loading') {").replace("    initClient();\n}\n", "    initClient();\n}\n}\n", 1)
    _workspace(root, {"public/client.js": guarded})
    done = subprocess.run(["node", "--test", "tests/snake/snake.test.js"], cwd=root, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    from src.utils.code_map import open_timer_problems
    assert open_timer_problems(root, "tests/snake/snake.test.js") == []


@needs_node
def test_the_file_that_hangs_is_found_by_running_each_alone(tmp_path, monkeypatch):
    from src.utils import test_task_check
    monkeypatch.setattr(test_task_check, "ISOLATED_FILE_TIMEOUT", 5.0)
    monkeypatch.setattr(test_task_check, "COMPLETION_TEST_TIMEOUT", 6.0)
    root = _the_project(tmp_path)
    files = ["tests/server/server.test.js", "tests/snake/snake.test.js"]
    with quiet():
        problem = run(test_task_check.javascript_run_problem(root, files, TASK_6))
    assert problem.startswith("its tests do not END: `node --test tests/server/server.test.js tests/snake/snake.test.js` "
                              "was still running after 6 s. Run ALONE, `tests/snake/snake.test.js` does not end, and "
                              "`tests/server/server.test.js` ends: the cause is in that file and the code it loads.")
    assert "runs `initClient()` as soon as it is LOADED" in problem
    assert test_task_check.last_hanging_files(root) == ["tests/snake/snake.test.js"]


# ======================================================================================== 4. module.exports in a page script
def test_unguarded_exports_of_a_page_script_are_named(tmp_path):
    from src.tools.project_links_check import link_notes_text
    from src.utils.product_check import browser_script_problems, is_page_script
    unguarded = CLIENT_JS + "\nmodule.exports = { startGame };\n"
    root = _workspace(tmp_path, {"public/index.html": INDEX_HTML, "public/client.js": unguarded, "src/server.js": SERVER_JS})
    assert is_page_script(root, "public/client.js") and is_page_script(root, "./public/client.js")
    assert not is_page_script(root, "src/server.js")
    [problem] = browser_script_problems(root)
    assert problem.startswith("`public/client.js` is loaded by `public/index.html` in the BROWSER, but it sets "
                              "`module.exports` (line 47) without `if (typeof module !== 'undefined')`")
    assert "[Framework note - the file WAS saved:]" in link_notes_text(root, "public/client.js", unguarded)
    _workspace(root, {"public/client.js": CLIENT_JS + CLIENT_EXPORTS})
    assert browser_script_problems(root) == []
    assert "module is not defined" not in link_notes_text(root, "public/client.js", CLIENT_JS + CLIENT_EXPORTS)
    # Node's own files are no page scripts: a server's exports are fine
    assert "module is not defined" not in link_notes_text(root, "src/server.js", SERVER_JS)


# ======================================================================================== 5. on a real task store
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
    from src.utils import file_authorship, test_task_check
    from src.utils.test_runs import clear_test_runs
    monkeypatch.setattr(pmt, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path / "projects")
    monkeypatch.setattr(test_task_check, "COMPLETION_TEST_TIMEOUT", 6.0)
    monkeypatch.setattr(test_task_check, "ISOLATED_FILE_TIMEOUT", 5.0)
    workspace = tmp_path / "projects" / PROJECT / SESSION / "shared_workspace"
    workspace.mkdir(parents=True)
    file_authorship.reset()
    clear_test_runs()

    async def no_broadcast(_msg):
        return None
    monkeypatch.setattr(pmt, "broadcast", no_broadcast)

    def call(agent_id, manager, **tool_args):
        with quiet():
            return run(pmt.ProjectManagementTool().execute(agent_id, tmp_path, project_name=PROJECT, session_name=SESSION,
                                                           manager=manager, **tool_args))
    call.workspace = workspace
    yield call
    file_authorship.reset()
    clear_test_runs()


def _add(call, manager, text, assignee=None):
    args = {"assignee_agent_id": assignee} if assignee else {}
    result = call("framework", manager, action="add_task", description=text, project_filter=PROJECT, **args)
    assert result["status"] == "success", result
    return result["task_uuid"]


@needs_tw_and_node
def test_the_hanging_test_file_of_another_worker_is_routed_to_it(tasks):
    from src.utils.file_authorship import record_file_change
    pm1, w1, w2 = Agent("PM1", "pm", "pm_manage"), Agent("W1", state="worker_work"), Agent("W2")
    manager = _manager(pm1, w1, w2)
    ws = _the_project(tasks.workspace)
    t3 = _add(tasks, manager, TASK_3, "W2")
    for rel in ("public/client.js", "tests/snake/snake.test.js"):
        record_file_change(ws / rel, "W2", t3)
    assert tasks("framework", manager, action="modify_task", task_id=t3, task_progress="finished")["status"] == "success"
    t6 = _add(tasks, manager, TASK_6, "W1")
    record_file_change(ws / "src/server.js", "W1", t6)
    record_file_change(ws / "tests/server/server.test.js", "W1", t6)
    w1.current_task_id = w1.active_task_id = t6
    refused = tasks("W1", manager, action="modify_task", task_id=t6, task_progress="finished")
    assert refused["status"] == "error" and refused["error_type"] == "tests_routed", refused
    assert refused["message"].startswith(f"Task '{t6}' was NOT marked finished: its tests do not END, and what keeps them "
                                         "running is in files OTHER workers made.")
    [routed] = refused["routed"]
    assert routed["file"] == "tests/snake/snake.test.js" and routed["assignee"] == "W2"
    listed = {t["uuid"]: t for t in tasks("framework", manager, action="list_tasks", project_filter=PROJECT)["tasks"]}
    fix = listed[routed["task_uuid"]]
    assert fix["description"].startswith("Fix: `node --test` of tests/snake/snake.test.js never ENDS (run alone too), so "
                                         "'Kick-off Task 6: Write automated tests for game logic and server funct' cannot be finished "
                                         "(it waits for this).")
    assert "runs `initClient()` as soon as it is LOADED" in fix["description"]
    assert routed["task_uuid"] in listed[t6]["depends"]
    # asked again while the fix is open: the same fix task, no second one
    again = tasks("W1", manager, action="modify_task", task_id=t6, task_progress="finished")
    assert again["status"] == "error", again
    fixes = [t for t in tasks("framework", manager, action="list_tasks", project_filter=PROJECT)["tasks"]
             if t["description"].startswith("Fix: `node --test` of tests/snake/snake.test.js")]
    assert len(fixes) == 1


@needs_tw_and_node
def test_a_worker_does_not_write_the_tests_of_its_own_other_task(tasks):
    from src.tools.project_management import foreign_file_creation_error
    w1, w2 = Agent("W1"), Agent("W2", state="worker_work")
    manager = _manager(Agent("PM1", "pm", "pm_manage"), w1, w2)
    t3 = _add(tasks, manager, TASK_3, "W2")
    t6 = _add(tasks, manager, TASK_6, "W2")   # 2026-11-16 21:11:22 .. 21:18:54 Task 6 was W2's too
    w2.current_task_id = w2.active_task_id = t3
    with quiet():
        refusal = foreign_file_creation_error(manager, "W2", PROJECT, SESSION, "tests/snake/snake.test.js")
    assert refusal.startswith(f"The file was NOT created: 'tests/snake/snake.test.js' is a test file of your OTHER task "
                              f"'{TASK_6}' ({t6}), which you have not started. Your current task is '{TASK_3}'")
    with quiet():  # files of the current task, and non-test files, stay free
        assert foreign_file_creation_error(manager, "W2", PROJECT, SESSION, "public/client.js") is None
        assert foreign_file_creation_error(manager, "W2", PROJECT, SESSION, "public/helpers.js") is None
    # working ON the test task: its files are the task's own
    w2.current_task_id = w2.active_task_id = t6
    with quiet():
        assert foreign_file_creation_error(manager, "W2", PROJECT, SESSION, "tests/snake/snake.test.js") is None
    # a current task that is about tests may write tests
    t9 = _add(tasks, manager, "Add unit tests for the input handling of public/client.js", "W2")
    w2.current_task_id = w2.active_task_id = t9
    with quiet():
        assert foreign_file_creation_error(manager, "W2", PROJECT, SESSION, "tests/snake/snake.test.js") is None
    # the task given to W1: the Round 3g+ refusal names W1
    moved = tasks("PM1", manager, action="modify_task", task_id=t6, assignee_agent_id="W1")
    assert moved["status"] == "success", moved
    w2.current_task_id = w2.active_task_id = t3
    with quiet():
        other = foreign_file_creation_error(manager, "W2", PROJECT, SESSION, "tests/snake/snake.test.js")
    assert other and "which is assigned to W1" in other

# END OF FILE tests/unit/test_round5g.py
