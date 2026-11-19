# START OF FILE tests/unit/test_round5k.py
"""
Round 5k: the run of 2026-11-19 13:22 .. 16:00 (WiFi_Snake_Game, qwen3.5:4b; the laptop slept 14:31-15:51 and lost DNS).

1. 14:10-14:16 server/server.py ran `socketio.run(app, debug=True, ...)` after `os.chdir(...)`: Werkzeug's reloader
   ("Restarting with stat") started the script again by its relative path - "can't open file 'server/server/server.py'".
   W1 and W2 sent five other changes (and 26 repeated edits) before the cause was found.
2. Every pytest run printed "rootdir: /home/tom/TrippleEffect / configfile: pyproject.toml" (the FRAMEWORK's) and
   ".venv/bin/python" paths: W2 ran `tail /home/tom/TrippleEffect/.te_background/...` and `cd /home/tom/TrippleEffect && ...`.
3. 14:31:17 PM1: `kill 19753 && cd shared_workspace && python server/server.py &` - "can't cd to shared_workspace".
"""
import asyncio
import logging
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

needs_python = pytest.mark.skipif(shutil.which("python") is None, reason="no `python` on PATH")


def run(coro):
    return asyncio.run(coro)


@contextmanager
def quiet():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


RELOADER_LOG = """ * Serving Flask app 'server'
 * Debug mode: on
WARNING: This is a development server. Do not use it in a production deployment.
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit
 * Restarting with stat
python: can't open file 'server/server/server.py': [Errno 2] No such file or directory
"""


# ============================================================================== 1. Flask's debug reloader
def test_the_reloader_cause_is_named():
    from src.utils.program_run_check import known_cause_hint
    hint = known_cause_hint(RELOADER_LOG)
    assert hint.startswith("\nCause: the server started, then Flask's debug RELOADER (debug=True: \"Restarting with stat\") "
                           "started the script again by its relative path - from a different working folder, so "
                           "'server/server/server.py' is not found.")
    assert "use_reloader=False" in hint and "os.path.dirname(os.path.abspath(__file__))" in hint
    assert known_cause_hint(" * Restarting with stat\n * Debugger is active!\n") == ""
    assert known_cause_hint("python: can't open file 'x.py': [Errno 2]") == ""
    assert known_cause_hint("") == ""


def test_the_background_check_names_the_reloader_cause(tmp_path):
    from src.tools.command_hints import background_start_note
    log = tmp_path / ".te_background" / "bg.log"
    log.parent.mkdir()
    log.write_text(RELOADER_LOG, encoding="utf-8")
    note = run(background_start_note(tmp_path, ".te_background/bg.log", None, "python server/server.py &", wait=0))
    assert "the background process has already ENDED" in note and "Cause: the server started, then Flask's debug" in note
    assert "Fix that error and start it again." not in note


# ============================================================================== 2. pytest's rootdir is the workspace
def _workspace(root: Path) -> Path:
    (root / "pyproject.toml").write_text("[project]\nname = 'framework'\n", encoding="utf-8")  # the framework's, above
    ws = root / "projects" / "P" / "s" / "shared_workspace"
    (ws / "tests" / "sub").mkdir(parents=True)
    (ws / "src").mkdir()
    (ws / "src" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    from src.utils.framework_conftest import FRAMEWORK_CONFTEST
    (ws / "conftest.py").write_text(FRAMEWORK_CONFTEST, encoding="utf-8")
    (ws / "tests" / "conftest.py").write_text("import pytest\n\n\n@pytest.fixture\ndef val():\n    return 1\n",
                                               encoding="utf-8")
    (ws / "tests" / "test_a.py").write_text("from mod import f\n\n\ndef test_a():\n    assert f() == 1\n", encoding="utf-8")
    (ws / "tests" / "sub" / "test_b.py").write_text("def test_b(val):\n    assert val == 1\n", encoding="utf-8")
    return ws


def test_the_pytest_options_for_a_workspace(tmp_path):
    from src.tools.sandbox_runner import with_workspace_pytest_root
    ws = _workspace(tmp_path)
    env = with_workspace_pytest_root({"PYTEST_ADDOPTS": "-o faulthandler_timeout=50"}, "python -m pytest -q", ws)
    assert env["PYTEST_ADDOPTS"] == (f"-c {ws / '.te_background' / 'pytest.ini'} --rootdir={ws} --confcutdir={ws} "
                                     "-o faulthandler_timeout=50")
    assert (ws / ".te_background" / "pytest.ini").read_text(encoding="utf-8").startswith("[pytest]\n")
    assert with_workspace_pytest_root({}, "node --test", ws) == {}
    assert with_workspace_pytest_root({}, "python -m pytest --rootdir=. -q", ws) == {}
    (ws / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")  # the project's own config wins
    assert with_workspace_pytest_root({}, "python -m pytest", ws) == {}


@needs_python
def test_pytest_runs_with_the_workspace_as_its_root(tmp_path):
    from src.tools.sandbox_runner import run_sandboxed
    ws = _workspace(tmp_path)
    for command, passed in (("python -m pytest tests", "2 passed"), ("python -m pytest tests/sub/test_b.py", "1 passed"),
                            ("cd tests && python -m pytest", "2 passed")):
        with quiet():
            result = run(run_sandboxed(command, ws, 120, tool_name="test", agent_id="W1"))
        assert passed in result.stdout, (command, result.stdout[-800:])
        if command == "python -m pytest tests":
            assert f"rootdir: {ws}" in result.stdout and "configfile: .te_background/pytest.ini" in result.stdout
    assert not (tmp_path / ".pytest_cache").exists()


# ============================================================================== 2b. the framework's paths in tool results
def test_the_frameworks_paths_are_not_shown(monkeypatch):
    import src.tools.executor as executor
    monkeypatch.setattr(executor, "_framework_dirs", lambda: ("/home/tom/TrippleEffect", "/home/tom/TrippleEffect/.venv"))
    text = ("platform linux -- Python 3.14.4, pytest-9.1.1 -- /home/tom/TrippleEffect/.venv/bin/python\n"
            "rootdir: /home/tom/TrippleEffect\n"
            '  File "/home/tom/TrippleEffect/.venv/lib/python3.14/site-packages/flask_socketio/__init__.py", line 664\n'
            "/home/tom/TrippleEffect/.venv/bin/python: can't open file 'server.py'\n"
            "tests/test_x.py /home/tom/TrippleEffectOther/a /home/tom/TrippleEffect/projects/P/s/shared_workspace/app.py")
    shown = executor.workspace_relative_paths(text, "/home/tom/TrippleEffect/projects/P/s/shared_workspace",
                                              "/home/tom/TrippleEffect/projects", "/home/tom/TrippleEffect/sandboxes/home")
    assert shown == ("platform linux -- Python 3.14.4, pytest-9.1.1 -- python\n"
                     "rootdir: <framework>\n"
                     '  File "site-packages/flask_socketio/__init__.py", line 664\n'
                     "python: can't open file 'server.py'\n"
                     "tests/test_x.py /home/tom/TrippleEffectOther/a app.py")


def test_a_system_python_is_never_masked(monkeypatch):
    import src.tools.executor as executor
    monkeypatch.setattr(executor, "_framework_dirs", lambda: ("/home/tom/TrippleEffect", ""))
    assert executor.framework_paths_hidden("/usr/bin/python3 /usr/lib/python3/x.py") == "/usr/bin/python3 /usr/lib/python3/x.py"


# ============================================================================== 3. `cd shared_workspace` inside a chain
def test_a_workspace_cd_inside_a_chain_is_dropped():
    from src.tools.command_executor import strip_workspace_cd
    assert strip_workspace_cd("kill 19753 && cd shared_workspace && python server/server.py &") == \
        "kill 19753 && python server/server.py &"
    assert strip_workspace_cd("ls; cd /home/tom/x/shared_workspace/ ; pytest -q") == "ls; pytest -q"
    assert strip_workspace_cd("cd server && python server.py") == "cd server && python server.py"
    assert strip_workspace_cd("python a.py && cd src && ls") == "python a.py && cd src && ls"

# END OF FILE tests/unit/test_round5k.py
