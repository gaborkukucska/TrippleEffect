# START OF FILE tests/unit/test_round5e.py
"""
Round 5e: the first Round 5d run (2026-11-11 15:38-17:35, WiFi_Multiplayer_Snake_Game, qwen3.5:4b-q4_K_M, TOOL_SURFACE=focused,
a Python/Flask project, still running when its data was collected).

The audit of 16:26 named what Round 5d looks for (`python app/server.py` stops with an ImportError, no server sends
public/index.html) and the project was not reported complete. The next 70 minutes went into three lines of app/server.py:

* the import line. `from flask import ..., websocket`, then `from flask import SocketIO`, then `from flask_socketio import
  SocketIO, WebSocket`: the model guesses the names of a library. The framework can read them.
* edits of lines that are no definition: 17 of 28 line edits failed (`lines: "16,17"` + `replace_with`, `line` + `code`,
  find_replace without a filename 10 times of 10), and replace_function was asked for '__main__', 'main' and the imports.
* W1 had two ready tasks and the watchdog sent it from one to the other, through worker_decompose, every three minutes.
"""
import asyncio
import logging
import shutil
from contextlib import contextmanager

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

needs_python = pytest.mark.skipif(shutil.which("python") is None, reason="no `python` on the PATH")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


SERVER_PY = '''"""
Flask server
"""

import logging
import json
from typing import Dict, Optional
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, WebSocket, emit

logger = logging.getLogger(__name__)

app = Flask(__name__)


def get_game(game_id: str) -> Optional[Dict]:
    return None


@app.route('/')
def index():
    return 'Flask WebSocket Server'
'''


def edit(tmp_path, **kwargs):
    from src.tools.code_editor import CodeEditorTool
    with quiet():
        return run(CodeEditorTool().execute("W1", tmp_path, scope="private", **kwargs))


# ============================================================================================ 1. blocks without a name
def test_the_start_block_is_added_and_replaced_by_replace_function(tmp_path):
    path = tmp_path / "server.py"
    path.write_text(SERVER_PY)
    block = ("# ==================== START SERVER ====================\n\nif __name__ == '__main__':\n"
             "    logger.info(\"Starting\")\n    app.run(host='0.0.0.0', port=5000, debug=False)")
    result = edit(tmp_path, action="replace_function", filename="server.py", function="__main__", code=block)   # 17:31:18
    assert result["status"] == "success", result
    assert "the file had no start block, so yours was ADDED at its end" in result["message"]
    assert path.read_text().endswith("    return 'Flask WebSocket Server'\n\n\nif __name__ == '__main__':\n"
                                     "    logger.info(\"Starting\")\n    app.run(host='0.0.0.0', port=5000, debug=False)\n")
    result = edit(tmp_path, action="replace_function", filename="server.py", function="main",                  # 17:34:38
                  code="if __name__ == '__main__':\n    socketio.run(app, host='0.0.0.0', port=5000)\n")
    assert result["status"] == "success" and "the start block (was lines 25-27) now holds your code" in result["message"]
    text = path.read_text()
    assert text.count("__main__") == 1 and text.endswith("if __name__ == '__main__':\n    socketio.run(app, host='0.0.0.0', port=5000)\n")
    assert "def index():" in text and "app.run(" not in text


def test_a_function_called_main_is_still_a_function(tmp_path):
    path = tmp_path / "cli.py"
    path.write_text("import sys\n\n\ndef main():\n    return 1\n\n\nif __name__ == '__main__':\n    sys.exit(main())\n")
    result = edit(tmp_path, action="replace_function", filename="cli.py", function="main", code="def main():\n    return 2")
    assert result["status"] == "success" and "'main' (was lines 4-5)" in result["message"]
    assert path.read_text() == "import sys\n\n\ndef main():\n    return 2\n\n\nif __name__ == '__main__':\n    sys.exit(main())\n"


def test_import_lines_are_merged_into_the_imports(tmp_path):
    path = tmp_path / "server.py"
    path.write_text(SERVER_PY)
    result = edit(tmp_path, action="replace_function", filename="server.py", function="imports",
                  code="from flask import Flask, request, jsonify, send_from_directory\n"
                       "from flask_socketio import SocketIO, emit\nimport os\nimport json")
    assert result["status"] == "success", result
    assert "2 import line(s) replaced" in result["message"] and "1 added (import os)" in result["message"]
    assert "1 already there; every other import of the file is kept" in result["message"]
    assert path.read_text().split("\n")[4:11] == [
        "import logging", "import json", "from typing import Dict, Optional",
        "from flask import Flask, request, jsonify, send_from_directory", "from flask_socketio import SocketIO, emit",
        "import os", ""]
    # no name at all, and nothing to do
    result = edit(tmp_path, action="replace_function", filename="server.py", code="import os\nimport json")
    assert result["status"] == "success" and "Nothing had to change" in result["message"]
    # a file without imports: after its docstring
    (tmp_path / "plain.py").write_text('"""Doc."""\n\nX = 1\n')
    assert edit(tmp_path, action="replace_function", filename="plain.py", function="imports", code="import os")["status"] == "success"
    assert (tmp_path / "plain.py").read_text() == '"""Doc."""\n\nimport os\n\nX = 1\n'


def test_javascript_requires_and_start_block(tmp_path):
    from src.utils.symbols import file_block_edit
    source = "const http = require('http');\nconst { a } = require('./a');\n\nfunction start() {}\n\nmodule.exports = { start };\n"
    chunks, what = file_block_edit(source, "server.js", "imports", "const { a, b } = require('./a');\nconst fs = require('fs');")
    assert chunks == [{"start_line": 2, "end_line": 2, "replace": "const { a, b } = require('./a');\nconst fs = require('fs');"}]
    assert what.startswith("imports: 1 import line(s) replaced")
    chunks, what = file_block_edit(source, "server.js", "main", "if (require.main === module) {\n  start();\n}")
    assert what == "the file had no start block, so yours was ADDED at its end"
    assert chunks[0]["replace"].endswith("module.exports = { start };\n\n\nif (require.main === module) {\n  start();\n}")
    # code that is neither is left to the other rules
    assert file_block_edit(source, "server.js", "imports", "const PORT = 3000;") is None
    assert file_block_edit(source, "notes.md", "imports", "import os") is None


# ============================================================================================ 2. the shapes of a line edit
def test_line_edits_in_the_shapes_the_model_sent(tmp_path):
    from src.tools.code_editor import parse_line_numbers
    assert parse_line_numbers([16, 17]) == (16, 17) and parse_line_numbers(["3"]) == (3, None)
    assert parse_line_numbers(["a", "b"]) == (None, None)
    path = tmp_path / "server.py"
    path.write_text(SERVER_PY)
    result = edit(tmp_path, action="replace_lines", filename="server.py", lines="8,9",                          # 17:14:30
                  replace_with="from flask import Flask, request, jsonify\nfrom flask_socketio import SocketIO, emit")
    assert result["status"] == "success", result
    assert path.read_text().split("\n")[8] == "from flask_socketio import SocketIO, emit"
    result = edit(tmp_path, action="replace_line", filename="server.py", line="6", code="import json  # used by the routes")  # 16:42:55
    assert result["status"] == "success", result
    assert path.read_text().split("\n")[5] == "import json  # used by the routes" and path.read_text().count("\n") == SERVER_PY.count("\n")


def test_find_replace_without_a_filename_names_the_file(tmp_path):
    from src.tools.file_system import FileSystemTool, files_holding_text_hint
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "server.py").write_text(SERVER_PY)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_text(SERVER_PY)
    with quiet():
        result = run(FileSystemTool().execute("W1", tmp_path, scope="private", action="find_replace",
                                              find_text="from flask import Flask, request, jsonify",
                                              replace_text="from flask import Flask, request, jsonify, send_from_directory"))
    assert result["status"] == "error"
    assert result["message"] == ("'filename' parameter is required for 'find_replace'. Nothing was changed. Your find_text is "
                                 "in 'app/server.py' (line 8): send the SAME call again with filename='app/server.py'.")
    assert "send_from_directory" not in (tmp_path / "app" / "server.py").read_text()
    assert "no file of the workspace holds that find_text" in files_holding_text_hint(tmp_path, "from flask import Sanic, nothing")
    (tmp_path / "app" / "other.py").write_text("from flask import Flask, request, jsonify\n")
    assert "in 'app/other.py' (line 1), 'app/server.py' (line 8)" in files_holding_text_hint(tmp_path, "from flask import Flask, request, jsonify")


# ============================================================================================ 3. the names of a library
@needs_python
def test_a_wrong_library_name_gets_the_names_the_library_has(tmp_path):
    from src.utils.program_run_check import library_names_hint
    with quiet():
        hint = run(library_names_hint("ImportError: cannot import name 'loadz' from 'json' (/usr/lib/python3/json/__init__.py)", tmp_path))
        assert hint.startswith("\n[Framework]: 'loadz' does not exist in the installed 'json'. Do not guess another name - the "
                               "names it really has: load, loads, ")
        assert hint.endswith("Closest: load, loads.")
        assert "'parse' does not exist in the installed 'json'" in run(
            library_names_hint("AttributeError: module 'json' has no attribute 'parse'", tmp_path))
        (tmp_path / "x.py").write_text("from json import JSONDecoder\n")
        hint = run(library_names_hint("AttributeError: 'JSONDecoder' object has no attribute 'decode_raw'", tmp_path))
        assert "'decode_raw' does not exist in the installed 'json.JSONDecoder'" in hint and "raw_decode" in hint
        hint = run(library_names_hint("ModuleNotFoundError: No module named 'te_no_such_library'", tmp_path))
        assert "the library 'te_no_such_library' is NOT installed here" in hint and "pip install te-no-such-library" in hint
        # a module of the project is not a library, and a name that exists is no finding
        (tmp_path / "app").mkdir()
        assert run(library_names_hint("ModuleNotFoundError: No module named 'app.database'", tmp_path)) == ""
        assert run(library_names_hint("ImportError: cannot import name 'x' from 'app.game'", tmp_path)) == ""
        assert run(library_names_hint("ImportError: cannot import name 'loads' from 'json'", tmp_path)) == ""
        assert run(library_names_hint("AssertionError: 1 != 2", tmp_path)) == ""
    assert not (tmp_path / ".te_background" / "te_library_probe.py").exists()


FLASK_STUB = '''class Flask:
    def __init__(self, *args, **kwargs):
        pass

    def route(self, *args, **kwargs):
        return lambda function: function
'''


@needs_python
def test_a_flask_app_that_nothing_starts(tmp_path):
    from src.utils.product_check import page_server_problems
    from src.utils.program_run_check import program_run_problem, server_entry
    (tmp_path / "app").mkdir()
    (tmp_path / "public").mkdir()
    (tmp_path / "flask.py").write_text(FLASK_STUB)
    (tmp_path / "app" / "server.py").write_text("import sys\nsys.path.insert(0, '.')\nfrom flask import Flask\n\napp = Flask(__name__)\n\n\n"
                                                "@app.route('/')\ndef index():\n    return 'x'\n")
    (tmp_path / "public" / "index.html").write_text("<html><body><script>const ws = new WebSocket('ws://' + location.host + '/ws');</script></body></html>")
    assert server_entry(tmp_path) == ("python app/server.py", "app/server.py")
    with quiet():
        problem = run(program_run_problem(tmp_path))
    assert problem.startswith("the program does not keep running: `python app/server.py` ended at once (exit code 0")
    assert ("app/server.py creates the Flask app, but nothing STARTS it. Add at the end of the file:\n"
            "if __name__ == '__main__':\n    app.run(host='0.0.0.0', port=5000)") in problem
    # the page problem names the project's own start command and shows how Flask sends a page
    (tmp_path / "app" / "server.py").write_text("from flask import Flask\napp = Flask(__name__)\n\nif __name__ == '__main__':\n    app.run(port=5000)\n")
    problems = page_server_problems(tmp_path)
    assert len(problems) == 1 and "the start command (`python app/server.py`) must run that file." in problems[0]
    assert "With Flask: `app = Flask(__name__, static_folder='../public', static_url_path='')`" in problems[0]
    assert "`return app.send_static_file('index.html')`" in problems[0] and "npm start" not in problems[0]
    # a project without a page: a module that creates an app is not started
    (tmp_path / "public" / "index.html").unlink()
    (tmp_path / "app" / "server.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
    assert server_entry(tmp_path) is None


# ============================================================================================ 4. one task at a time
def test_the_watchdog_takes_the_task_the_worker_is_on():
    from src.agents.manager import AgentManager, prefer_started_tasks
    started = AgentManager.STARTED_TASK_PROGRESS
    integration = {"uuid": "b4b1dfe3", "task_progress": "todo", "description": "Integration and Testing"}
    fix = {"uuid": "e85ae301", "task_progress": "doing", "description": "Fix missing SocketIO import"}
    assert [t["uuid"] for t in prefer_started_tasks([integration, fix], None, started)] == ["e85ae301", "b4b1dfe3"]
    assert [t["uuid"] for t in prefer_started_tasks([integration, fix], "b4b1dfe3", started)] == ["b4b1dfe3", "e85ae301"]
    both = [dict(integration, task_progress="doing"), fix]
    assert [t["uuid"] for t in prefer_started_tasks(both, None, started)] == ["b4b1dfe3", "e85ae301"]   # stable
    assert prefer_started_tasks([], None, started) == []

# END OF FILE tests/unit/test_round5e.py
