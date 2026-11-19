# START OF FILE tests/unit/test_round5m.py
"""
Round 5m: the run of 2026-11-19 17:23 - 17:46 (WiFi_Snake_Game, qwen3.5:4b; stuck when it was collected).

1. 17:40-17:46 W1 sent `startGameLoop` with `if (require.main === module) { startGameLoop(); }` (replace_function) to stop
   its tests from hanging: the start block was added at the end of src/server/server.js and the bare `startGameLoop();`
   above it (line 316) stayed - the tests still never ended and W1 sent the same edit for six minutes.
2. 17:29 W1's Kick-off Task 1 passed the run check (Round 5l) although src/server/server.js never called `.listen(...)`:
   its game timers kept Node running, and "still running" counted as "started".
"""
import asyncio
import shutil
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

SERVER = """const http = require('http');

const server = http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/html' });
  res.end('<html></html>');
});

function startGameLoop() {
  const id = setInterval(() => {}, 100);
  return id;
}

function helper() {
  startGameLoop();
}

// Initialize game
startGameLoop();

module.exports = { startGameLoop, server };
"""
CODE = """function startGameLoop(start = true) {
  const id = setInterval(() => {}, 100);
  return id;
}

if (require.main === module) {
  startGameLoop();
}
"""


def _apply(original, chunk):
    lines = original.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines[:chunk["start_line"] - 1] + chunk["replace"].split("\n") + lines[chunk["end_line"]:]) + "\n"


# ============================================================================== 1. the bare call goes with the start block
def test_a_definition_with_a_start_block_removes_the_bare_call():
    from src.utils.symbols import multi_definition_edit
    chunk, what = multi_definition_edit(SERVER, "src/server/server.js", CODE, "startGameLoop")
    assert "the bare `startGameLoop()` (lines 17-18) was REMOVED" in what, what
    out = _apply(SERVER, chunk)
    assert "// Initialize game" not in out
    assert out.count("startGameLoop();") == 2  # in helper() and in the start block
    assert out.endswith("module.exports = { startGameLoop, server };\n\nif (require.main === module) {\n  startGameLoop();\n}\n")
    # sent again on the result: nothing more to remove
    again, what = multi_definition_edit(out, "src/server/server.js", CODE, "startGameLoop")
    assert "REMOVED" not in what


def test_the_start_block_alone_removes_the_bare_call_too():
    from src.utils.symbols import file_block_edit
    chunks, what = file_block_edit(SERVER, "src/server/server.js", "main", "if (require.main === module) {\n  startGameLoop();\n}\n")
    assert what.startswith("the file had no start block, so yours was ADDED at its end; the bare `startGameLoop()`")
    out = _apply(SERVER, chunks[0])
    assert out.count("startGameLoop();") == 2 and "// Initialize game" not in out
    # the file has both (what W1's edits left): the start block replaced, the bare call removed
    both = SERVER + "\nif (require.main === module) {\n  startGameLoop();\n}\n"
    chunks, what = file_block_edit(both, "src/server/server.js", "main", "if (require.main === module) {\n  startGameLoop();\n}\n")
    assert "now holds your code; the bare `startGameLoop()`" in what
    assert _apply(both, chunks[0]).count("startGameLoop();") == 2


def test_other_top_level_calls_stay():
    from src.utils.symbols import file_block_edit, unguarded_start_calls
    source = SERVER.replace("// Initialize game\nstartGameLoop();\n", "console.log('ready');\nhelper();\n")
    assert unguarded_start_calls(source, "src/server/server.js", "if (require.main === module) {\n  startGameLoop();\n}") == []
    chunks, what = file_block_edit(source, "src/server/server.js", "main", "if (require.main === module) {\n  startGameLoop();\n}\n")
    assert "REMOVED" not in what and len(chunks) == 1


def test_a_python_main_call_goes_too():
    from src.utils.symbols import multi_definition_edit
    source = "def main():\n    print('x')\n\n\nmain()\n"
    code = "def main():\n    print('y')\n\n\nif __name__ == '__main__':\n    main()\n"
    chunk, what = multi_definition_edit(source, "app.py", code, "main")
    assert "the bare `main()` (line 5) was REMOVED" in what, what
    assert _apply(source, chunk) == "def main():\n    print('y')\n\n\nif __name__ == '__main__':\n    main()\n"


def test_the_hang_hint_names_the_call_to_remove(tmp_path):
    from src.utils.code_map import open_timer_problems
    (tmp_path / "src" / "server").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "server" / "server.js").write_text(SERVER, encoding="utf-8")
    (tmp_path / "tests" / "server.test.js").write_text(
        "const { test } = require('node:test');\nconst s = require('../src/server/server.js');\ntest('x', () => {});\n",
        encoding="utf-8")
    problems = open_timer_problems(tmp_path, "tests/server.test.js")
    assert problems and "runs `startGameLoop()` as soon as it is LOADED (line 18)" in problems[0]
    assert "the call at line 18 has to GO - rewriting `startGameLoop()` itself does not stop the file from calling it" \
        in problems[0]


# ============================================================================== 2. a server that never listens
def test_a_server_that_never_listens_is_named(tmp_path):
    from src.utils.program_run_check import never_listens_problem
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "server.js").write_text(SERVER, encoding="utf-8")
    problem = never_listens_problem(tmp_path, "src/server.js", "node src/server.js")
    assert problem.startswith("the program does not listen: `node src/server.js` keeps running, but src/server.js creates "
                              "its server (`http.createServer(...)`, line 3) and nothing calls `.listen(...)`")
    assert "`if (require.main === module) { server.listen(PORT, '0.0.0.0'" in problem
    (tmp_path / "src" / "server.js").write_text(SERVER + "if (require.main === module) { server.listen(3000); }\n",
                                                encoding="utf-8")
    assert never_listens_problem(tmp_path, "src/server.js", "node src/server.js") is None
    # listening in a required local file, or a WebSocket server with its own port, counts
    (tmp_path / "src" / "server.js").write_text("const app = require('./app');\nconst http = require('http');\n"
                                                "const s = http.createServer(app);\nrequire('./start')(s);\n", encoding="utf-8")
    (tmp_path / "src" / "start.js").write_text("module.exports = (s) => s.listen(3000);\n", encoding="utf-8")
    assert never_listens_problem(tmp_path, "src/server.js", "node src/server.js") is None
    (tmp_path / "src" / "server.js").write_text("const http = require('http');\nhttp.createServer();\n"
                                                "const WebSocket = require('ws');\nnew WebSocket.Server({ port: 8080 });\n",
                                                encoding="utf-8")
    assert never_listens_problem(tmp_path, "src/server.js", "node src/server.js") is None
    assert never_listens_problem(tmp_path, "app.py", "python app.py") is None


@needs_node
def test_the_run_check_refuses_a_server_kept_alive_by_its_timers(tmp_path, monkeypatch):
    from src.utils import program_run_check
    monkeypatch.setattr(program_run_check, "RUN_CHECK_SECONDS", 3.0)
    monkeypatch.setattr(program_run_check, "HTTP_PROBE_DELAY_SECONDS", 1.0)
    (tmp_path / "src").mkdir()
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text("<html><body>Snake</body></html>\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "x", "scripts": {"start": "node src/server.js"}}\n', encoding="utf-8")
    (tmp_path / "src" / "server.js").write_text(SERVER, encoding="utf-8")
    problem = asyncio.run(program_run_check.program_run_problem(tmp_path))
    assert problem and problem.startswith("the program does not listen: `npm start` keeps running"), problem

# END OF FILE tests/unit/test_round5m.py
