# START OF FILE tests/unit/test_round5o.py
"""
Round 5o: the run of 2026-11-20 11:14 - 11:45 (WiFi_Snake_Game) ended when PM1 restarted the game with
`pkill -f "python.*main" 2>/dev/null; sleep 1; cd /workspace && python -m src.main &`: the project's server was src/main.py,
TrippleEffect is `python -m src.main` - the pattern matched the framework and it shut down.
A stop command now reaches only the project's own processes (src/utils/kill_guard.py).

Before that, 11:31-11:44: the Round 5l run check refused W2's "Server entry point: src/main.py" task six times with
"ModuleNotFoundError: No module named 'src'" - it started `python src/main.py`, and src/main.py imports
`from src.multiplayer_server import ...`. W2's own `python -m src.main` had got past the imports to the real errors. A Python
file that imports its own folder as a package is now started as a module, and a README that says `python src/main.py`
for it is named.
"""
import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order

needs_proc = pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="Linux /proc needed")


@pytest.fixture
def procs(tmp_path):
    """A project process (in the workspace) and an outside one (another folder), both with 'main' in their command line."""
    tag = "te5o" + uuid.uuid4().hex[:8]
    workspace = tmp_path / "shared_workspace"
    outside = tmp_path / "framework"
    (workspace / "src").mkdir(parents=True)
    (outside / "src").mkdir(parents=True)
    script = "import time\ntime.sleep(60)\n"
    (workspace / "src" / "main.py").write_text(script, encoding="utf-8")
    (outside / "src" / "main.py").write_text(script, encoding="utf-8")
    started = []
    for folder in (workspace, outside):
        started.append(subprocess.Popen([sys.executable, "src/main.py", tag], cwd=folder,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(0.3)
    yield workspace, tag, started[0], started[1]
    for process in started:
        if process.poll() is None:
            process.kill()
        process.wait()


@needs_proc
def test_the_runs_pkill_is_not_run(procs):
    from src.utils.kill_guard import kill_guard_problem
    workspace, tag, own, other = procs
    command = f'pkill -f "python.*main.py {tag}" 2>/dev/null; sleep 1; cd /workspace && python -m src.main &'
    problem = kill_guard_problem(command, workspace)
    assert problem and problem.startswith(f'NOT run: `pkill -f "python.*main.py {tag}" 2>/dev/null` would also stop '
                                          "processes that are NOT the project's:"), problem
    assert f"- pid {other.pid} `" in problem and "not started in the project's workspace" in problem
    assert f"- pid {own.pid} `" in problem and f"Stop exactly those: `kill {own.pid}`" in problem
    assert f"pid {own.pid} `" not in problem.split("project's own processes")[0]


@needs_proc
def test_the_framework_itself_is_named(procs):
    from src.utils.kill_guard import kill_guard_problem
    workspace, _tag, own, _other = procs
    problem = kill_guard_problem(f"kill -9 {os.getpid()} {own.pid}", workspace)
    assert f"pid {os.getpid()} `" in problem and "TrippleEffect ITSELF, the framework every agent runs in" in problem
    assert "kill -1" not in problem
    assert "every process" in kill_guard_problem("kill -9 -1", workspace)


@needs_proc
def test_stopping_the_projects_own_processes_runs(procs):
    from src.utils.kill_guard import kill_guard_problem
    workspace, tag, own, other = procs
    assert kill_guard_problem(f"kill {own.pid}", workspace) is None
    assert kill_guard_problem(f"kill -TERM {own.pid}; python src/main.py &", workspace) is None
    # a pattern only the project's process matches - its folder is in the command line of neither, so use the tag + cwd
    assert kill_guard_problem(f"pgrep -f {tag}", workspace) is None  # a pgrep alone stops nothing
    assert kill_guard_problem("ls -la && echo killing nothing", workspace) is None
    assert kill_guard_problem(f"kill 999999999", workspace) is None  # no such process


@needs_proc
def test_the_other_ways_to_stop_by_name_or_port(procs):
    from src.utils.kill_guard import kill_guard_problem
    workspace, tag, own, other = procs
    for command in (f"kill $(pgrep -f {tag})", f"pgrep -f {tag} | xargs kill",
                    f"ps aux | grep {tag} | grep -v grep | awk '{{print $2}}' | xargs kill -9"):
        problem = kill_guard_problem(command, workspace)
        assert problem and f"- pid {other.pid} `" in problem, command
    assert kill_guard_problem(f"ps aux | grep {tag}", workspace) is None  # looking is fine
    # a port the framework holds
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        for command in (f"fuser -k {port}/tcp", f"lsof -ti:{port} | xargs kill -9", f"kill $(lsof -t -i:{port})"):
            problem = kill_guard_problem(command, workspace)
            assert problem and "TrippleEffect ITSELF" in problem, command


@needs_proc
def test_run_sandboxed_does_not_run_it(procs):
    from src.tools.sandbox_runner import run_sandboxed
    workspace, tag, own, other = procs
    result = asyncio.run(run_sandboxed(f'pkill -f "main.py {tag}"', workspace, 10, tool_name="command_executor",
                                       agent_id="PM1"))
    assert result.status == "blocked" and result.message.startswith("NOT run:")
    time.sleep(0.3)
    assert other.poll() is None and own.poll() is None
    result = asyncio.run(run_sandboxed(f"kill {own.pid}", workspace, 10, tool_name="command_executor", agent_id="PM1"))
    assert result.status == "completed"
    own.wait(timeout=5)
    assert other.poll() is None

# ============================================================================== python -m for a file in its own package
MAIN_PY = """import http.server
import socketserver

from src.helper import PORT


class Handler(http.server.SimpleHTTPRequestHandler):
    pass


if __name__ == '__main__':
    with socketserver.TCPServer(('127.0.0.1', PORT), Handler) as httpd:
        httpd.serve_forever()
"""


def _project(root, readme="Start: `python src/main.py`\n"):
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "helper.py").write_text("PORT = 0\n", encoding="utf-8")
    (root / "src" / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (root / "docs" / "README.md").write_text(readme, encoding="utf-8")
    return root


def test_a_file_that_imports_its_own_package_is_started_as_a_module(tmp_path):
    from src.utils.program_run_check import python_command, server_entry
    root = _project(tmp_path)
    assert python_command(root, "src/main.py") == "python -m src.main"
    assert server_entry(root) == ("python -m src.main", "src/main.py")
    (root / "docs" / "README.md").write_text("Start: `python -m src.main`\n", encoding="utf-8")
    assert server_entry(root) == ("python -m src.main", "src/main.py")
    (root / "docs" / "README.md").write_text("Nothing to see.\n", encoding="utf-8")
    assert server_entry(root) == ("python -m src.main", "src/main.py")
    # no package import: the file's own path; a file at the top level too
    (root / "src" / "main.py").write_text(MAIN_PY.replace("from src.helper import PORT", "PORT = 0"), encoding="utf-8")
    assert python_command(root, "src/main.py") == "python src/main.py"
    assert python_command(root, "server.py") == "python server.py"


def test_the_readme_command_that_cannot_import_is_named(tmp_path):
    from src.utils.program_run_check import readme_command_problems
    root = _project(tmp_path)
    problems = readme_command_problems(root)
    assert problems == ["docs/README.md tells the user to run `python src/main.py`, but src/main.py imports its own "
                        "package (`from src. ...`): started that way, Python puts src/ itself on the import path and it "
                        "stops with \"ModuleNotFoundError: No module named 'src'\". Write `python -m src.main` (run from "
                        "the project folder) - do not change the imports."]
    (root / "docs" / "README.md").write_text("Start: `python -m src.main`; tests: `python -m pytest -q`\n", encoding="utf-8")
    assert readme_command_problems(root) == []
    (root / "docs" / "README.md").write_text("Start: `python -m src.server`\n", encoding="utf-8")
    assert readme_command_problems(root) == ["docs/README.md tells the user to run `python -m src.server`, but there is "
                                             "no src/server.py."]


@pytest.mark.skipif(not Path("/proc/self").exists(), reason="Linux")
def test_the_run_check_gets_past_the_import(tmp_path, monkeypatch):
    from src.utils import program_run_check
    monkeypatch.setattr(program_run_check, "RUN_CHECK_SECONDS", 3.0)
    root = _project(tmp_path)
    assert asyncio.run(program_run_check.program_run_problem(root)) is None
    (root / "src" / "main.py").write_text(MAIN_PY.replace("    with socketserver", "    raise AttributeError(\"'MainServer' "
                                                          "object has no attribute 'multiplayer'\")\n    with socketserver"),
                                          encoding="utf-8")
    problem = asyncio.run(program_run_check.program_run_problem(root))
    assert problem.startswith("the program does not start: `python -m src.main` stopped with an error")
    assert "'MainServer' object has no attribute 'multiplayer'" in problem and "No module named" not in problem

# END OF FILE tests/unit/test_round5o.py
