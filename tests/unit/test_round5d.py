# START OF FILE tests/unit/test_round5d.py
"""
Round 5d: the first complete run on a local 4B model (2026-11-11 10:54-14:59, WiFi_Multiplayer_Snake, qwen3.5:4b-q4_K_M,
TOOL_SURFACE=full). All nine tasks were finished, the tests passed, the second audit reported the project complete - and
the user's `npm start` did nothing and the page got 404.

* `npm start` ran src/network_server.js, a file that defines a class and exports it. The run check never started it:
  `new WebSocket.Server({ port })` was no "server code", so the project had no entry to check.
* No file sent public/index.html, nothing answered its fetch('/api/leaderboard/...'), and public/client.js - loaded by the
  page - began with require().
* Asked about it, the Admin AI wrote two more servers into the project itself (scope 'projects' went past the Round 3s
  rule), gave its answer in the state 'work' - and stayed there with the status 'processing' for good.
* replace_function: 126 calls, 7% failed (line edits 49%, whole-file writes 35%). One early call sent the code as
  'function'.
"""
import asyncio
import logging
import shutil
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

needs_node = pytest.mark.skipif(shutil.which("node") is None or shutil.which("npm") is None, reason="node / npm not installed")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


NETWORK_SERVER = '''const WebSocket = require('ws');

class NetworkServer {
  constructor(config = {}) {
    this.port = config.port || 3000;
  }

  async initialize() {
    this.wsServer = new WebSocket.Server({ port: this.port });
    return { success: true };
  }
}

module.exports = { NetworkServer };
'''
INDEX_HTML = '''<!DOCTYPE html>
<html>
<head><script src="/client.js"></script></head>
<body>
<canvas id="board"></canvas>
<script>
async function loadLeaderboard() {
  const response = await fetch('/api/leaderboard/top?limit=10');
  return response.json();
}
</script>
</body>
</html>
'''
CLIENT_JS = '''/**
 * Client-side game
 */
const WebSocket = require('ws');
const { GameLogic } = require('./game_logic');

class ClientGame {
  connect() { this.socket = new WebSocket('ws://' + location.host); }
}
'''
PACKAGE = '{"name": "snake", "version": "1.0.0", "scripts": {"start": "node src/network_server.js", "test": "node --test"}}'
GOOD_SERVER = '''const http = require('http');
const fs = require('fs');
const path = require('path');
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/leaderboard/top')) { res.end('[]'); return; }
  const file = req.url === '/' ? 'index.html' : req.url.slice(1);
  fs.readFile(path.join(__dirname, '..', 'public', file), (err, data) => { res.end(data); });
});
server.listen(3000);
'''


def _workspace(root, server=NETWORK_SERVER, client=CLIENT_JS, package=PACKAGE):
    for rel, text in (("src/network_server.js", server), ("public/index.html", INDEX_HTML), ("public/client.js", client),
                      ("package.json", package), ("src/game_logic.js", "class GameLogic {}\nmodule.exports = { GameLogic };\n")):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# ============================================================================================ 1. the start command
def test_the_start_command_of_a_project_with_a_page_is_its_entry(tmp_path):
    from src.utils.program_run_check import _server_files, server_entry
    root = _workspace(tmp_path)
    assert _server_files(root) == ["src/network_server.js"]  # a WebSocket server is server code
    assert server_entry(root) == ("npm start", "src/network_server.js")
    # also when the file has no server code at all
    (root / "src" / "network_server.js").write_text("class NetworkServer {}\nmodule.exports = { NetworkServer };\n")
    assert _server_files(root) == [] and server_entry(root) == ("npm start", "src/network_server.js")
    # a command-line project without a page: a start script that prints and ends is not judged
    (root / "public" / "index.html").unlink()
    assert server_entry(root) is None


@needs_node
def test_a_start_command_that_ends_at_once_is_named(tmp_path):
    from src.utils.program_run_check import program_run_problem
    root = _workspace(tmp_path)
    (root / "node_modules" / "ws").mkdir(parents=True)
    (root / "node_modules" / "ws" / "index.js").write_text("module.exports = { Server: class { constructor() {} on() {} } };\n")
    with quiet():
        problem = run(program_run_problem(root))
    assert problem.startswith("the program does not keep running: `npm start` ended at once (exit code 0")
    assert "src/network_server.js only DEFINES and exports its code - nothing in it runs when it is started" in problem
    assert "if (require.main === module)" in problem


# ============================================================================================ 2. the page and its server
def test_a_page_that_no_server_sends(tmp_path):
    from src.utils.product_check import page_server_problems, product_problems
    root = _workspace(tmp_path)
    problems = page_server_problems(root)
    assert len(problems) == 1
    assert problems[0].startswith("`public/index.html` needs a server (it uses fetch('/api/leaderboard/top'), a WebSocket), "
                                  "but NO server file of the project sends this page (server code is in: src/network_server.js)")
    assert "ONE port" in problems[0] and "`npm start`" in problems[0]
    assert any("needs a server" in p for p in product_problems(root))
    # a server that sends the page and answers its request: nothing to say
    (root / "src" / "network_server.js").write_text(GOOD_SERVER)
    assert page_server_problems(root) == []
    # it sends the page, but has no route for what the page asks
    (root / "src" / "network_server.js").write_text(GOOD_SERVER.replace("/api/leaderboard/top", "/api/scores"))
    problems = page_server_problems(root)
    assert len(problems) == 1 and "asks the server for `/api/leaderboard/top`, but no server file" in problems[0]
    # a page that asks nobody for anything needs no server
    (root / "public" / "index.html").write_text("<html><body><script src='client.js'></script></body></html>")
    (root / "public" / "client.js").write_text("document.title = 'x';\n")
    (root / "src" / "network_server.js").write_text("class A {}\nmodule.exports = { A };\n")
    assert page_server_problems(root) == []


def test_a_browser_script_that_calls_require(tmp_path):
    from src.utils.product_check import browser_script_problems
    root = _workspace(tmp_path)
    problems = browser_script_problems(root)
    assert len(problems) == 1
    assert problems[0].startswith("`public/client.js` is loaded by `public/index.html` in the BROWSER, but it calls "
                                  "`require(...)` (line 4: `const WebSocket = require('ws');`)")
    assert "ReferenceError: require is not defined" in problems[0]
    # guarded for both worlds, or no require at all
    (root / "public" / "client.js").write_text(
        "class ClientGame {}\nif (typeof module !== 'undefined') { module.exports = { ClientGame }; }\n"
        "// const x = require('y') in a comment\n")
    assert browser_script_problems(root) == []
    # import/export in a script that is not loaded as a module
    (root / "public" / "client.js").write_text("import { GameLogic } from './game_logic.js';\nexport class ClientGame {}\n")
    problems = browser_script_problems(root)
    assert len(problems) == 1 and '<script type="module" src=...>' in problems[0]
    (root / "public" / "index.html").write_text(INDEX_HTML.replace('<script src="/client.js">', '<script type="module" src="/client.js">'))
    assert browser_script_problems(root) == []


def test_the_audit_note_holds_all_of_it(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.utils import program_run_check
    root = _workspace(tmp_path / "P" / "S" / "shared_workspace")
    (root / "PROJECT_PLAN.md").write_text("**Objective:** Browser-based multiplayer Snake game with a leaderboard.\n\n**Tasks:**\n1. x\n")
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", tmp_path)

    async def ended_at_once(_root, agent_id="framework"):
        return "the program does not keep running: `npm start` ended at once (exit code 0, no output)"
    monkeypatch.setattr(program_run_check, "program_run_problem", ended_at_once)
    with quiet():
        note = run(program_run_check.audit_run_note(types.SimpleNamespace(current_project="P", current_session="S")))
    assert "It does NOT work yet:\n- the program does not keep running: `npm start` ended at once" in note
    assert "- `public/index.html` needs a server" in note and "- `public/client.js` is loaded by `public/index.html`" in note
    assert "[Framework Product Check]" in note


# ============================================================================================ 3. the Admin AI
class Agent:
    def __init__(self, agent_id, agent_type, state, project=None):
        self.agent_id, self.agent_type, self.state, self.status = agent_id, agent_type, state, "processing"
        self.agent_config = {"config": {"project_name_context": project}} if project else {"config": {}}
        self.message_history = []

    def set_status(self, status, *args, **kwargs):
        self.status = status


def test_the_admin_ai_does_not_change_project_files_through_the_projects_folder():
    from src.tools.executor import ToolExecutor
    admin, pm = Agent("admin_ai", "admin", "work"), Agent("PM1", "pm", "pm_standby", "WiFi_Multiplayer_Snake")
    manager = types.SimpleNamespace(agents={"admin_ai": admin, "PM1": pm})
    base = "WiFi_Multiplayer_Snake/startup_1794365663/shared_workspace/"
    write = {"action": "write", "scope": "projects", "filename": base + "src/express_server.js", "content": "x"}
    with quiet():
        refusal = ToolExecutor._admin_project_change_message("file_system", write, "admin_ai", None, manager)
        assert refusal and refusal.startswith("'src/express_server.js' was NOT changed: the project 'WiFi_Multiplayer_Snake' "
                                              "is built by PM1 and its team")
        assert "send_message" in refusal
        edit = {"action": "replace_function", "scope": "projects", "filename": base + "src/leaderboard.js",
                "function": "f", "code": "function f() {}"}
        assert ToolExecutor._admin_project_change_message("code_editor", edit, "admin_ai", None, manager)
        # reading stays allowed, and so do files outside a project workspace
        read = {"action": "read", "scope": "projects", "filename": base + "src/leaderboard.js"}
        assert ToolExecutor._admin_project_change_message("file_system", read, "admin_ai", None, manager) is None
        note = {"action": "write", "scope": "projects", "filename": "notes.md", "content": "x"}
        assert ToolExecutor._admin_project_change_message("file_system", note, "admin_ai", None, manager) is None
        private = {"action": "write", "scope": "private", "filename": "shared_workspace/x.js", "content": "x"}
        assert ToolExecutor._admin_project_change_message("file_system", private, "admin_ai", None, manager) is None


def test_the_admin_ai_is_idle_and_back_in_the_conversation_after_its_answer():
    from src.agents.constants import ADMIN_STATE_CONVERSATION, ADMIN_STATE_WORK, AGENT_STATUS_IDLE
    from src.agents.cycle_components import next_step_scheduler
    source = Path(next_step_scheduler.__file__).read_text(encoding="utf-8")
    branch = source.split("work appears complete", 1)[1].split("elif (agent.agent_type, agent.state) in persistent_states", 1)[0]
    assert "workflow_manager.change_state(agent, ADMIN_STATE_CONVERSATION)" in branch
    assert "agent.set_status(AGENT_STATUS_IDLE)" in branch
    assert ADMIN_STATE_WORK == "work" and ADMIN_STATE_CONVERSATION and AGENT_STATUS_IDLE == "idle"
    import yaml
    prompts = yaml.safe_load((Path(__file__).resolve().parents[2] / "prompts.yaml").read_text(encoding="utf-8"))
    assert "send the project's PM a bug report with `send_message`" in prompts["admin_work_prompt"]


# ============================================================================================ 4. replace_function
def test_code_sent_as_the_function_name_is_used_as_the_code(tmp_path):
    from src.tools.code_editor import CodeEditorTool
    (tmp_path / "g.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    with quiet():
        result = run(CodeEditorTool().execute("W1", tmp_path, scope="private", action="replace_function", filename="g.py",
                                              function="def b():\n    return 3"))
    assert result["status"] == "success", result
    assert "'function' was not given; 'b' is what your code defines." in result["message"]
    assert (tmp_path / "g.py").read_text().endswith("def b():\n    return 3\n")

# END OF FILE tests/unit/test_round5d.py
