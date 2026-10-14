# START OF FILE tests/unit/test_shell_tools.py
"""
Tool-level tests for command_executor, test_runner and codebase_search after
they were routed through src/tools/sandbox_runner.py (Review S1, S3, S7, R3, R4, R11).

Settings attributes are monkeypatched, so the real .env is never relied upon.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import settings  # noqa: E402
from src.tools.command_executor import CommandExecutionTool  # noqa: E402
from src.tools.test_runner import TestRunnerTool as _TestRunnerTool  # noqa: E402
from src.tools.codebase_search import CodebaseSearchTool  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell semantics")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    ws = projects / "Proj" / "sess" / "shared_workspace"
    sb = tmp_path / "sandboxes" / "agent_W1"
    ws.mkdir(parents=True)
    sb.mkdir(parents=True)
    monkeypatch.setattr(settings, "PROJECTS_BASE_DIR", projects, raising=False)
    monkeypatch.setattr(settings, "DISABLE_COMMAND_EXECUTION", False, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_ISOLATE_HOME", True, raising=False)
    monkeypatch.setattr(settings, "SANDBOX_HOME_DIR", tmp_path / "shell_home", raising=False)
    monkeypatch.setattr(settings, "SANDBOX_ENV_PASSTHROUGH", [], raising=False)
    monkeypatch.setenv("FAKE_PROVIDER_API_KEY", "leaked-value-123")
    return {"ws": ws, "sb": sb, "projects": projects}


def ce(env, command, **extra):
    args = {"action": "run_command", "command": command, **extra}
    return run(CommandExecutionTool().execute(agent_id="W1", agent_sandbox_path=env["sb"],
                                              project_name="Proj", session_name="sess", **args))


# --- command_executor ----------------------------------------------------------------

def test_command_runs_in_shared_workspace_by_default(env):
    r = ce(env, "pwd")
    assert r["status"] == "success"
    assert str(env["ws"].resolve()) in r["content"]


def test_scope_default_private_without_project(env):
    r = run(CommandExecutionTool().execute(agent_id="A", agent_sandbox_path=env["sb"],
                                           action="run_command", command="pwd"))
    assert r["status"] == "success"
    assert str(env["sb"].resolve()) in r["content"]


def test_command_env_has_no_secrets(env):
    r = ce(env, "printenv FAKE_PROVIDER_API_KEY; env")
    assert "leaked-value-123" not in r["content"]


def test_kill_switch(env, monkeypatch):
    monkeypatch.setattr(settings, "DISABLE_COMMAND_EXECUTION", True, raising=False)
    r = ce(env, "echo hi")
    assert r["status"] == "error" and "DISABLE_COMMAND_EXECUTION" in r["message"]


def test_nonzero_exit_reports_stderr(env):
    r = ce(env, "echo boom >&2; exit 2")
    assert r["status"] == "success"  # executor keeps context; failure is in the content
    assert "return code 2" in r["message"] and "boom" in r["message"]
    assert "Failed (Exit Code: 2)" in r["content"]


def test_background_command_returns_quickly(env):
    start = time.monotonic()
    r = ce(env, "sleep 30 &")
    assert time.monotonic() - start < 5
    assert r["status"] == "success" and "background" in r["message"]
    assert ".te_background/" in r["message"]


def test_auto_backgrounded_server_points_to_server_output_log(env):
    (env["ws"] / "server.py").write_text("import time\nprint('up', flush=True)\ntime.sleep(30)\n")
    start = time.monotonic()
    r = ce(env, "python server.py")
    assert time.monotonic() - start < 5
    assert "server_output.log" in r["message"]


def test_cd_prefix_still_stripped(env):
    r = ce(env, "cd /workspace && pwd")
    assert str(env["ws"].resolve()) in r["content"]


# --- npm skip truthfulness (R3) ---------------------------------------------------------

def _npm_layout(ws, marker_newer: bool):
    (ws / "node_modules").mkdir()
    pkg = ws / "package.json"
    pkg.write_text("{}")
    marker = ws / "node_modules" / ".package-lock.json"
    marker.write_text("{}")
    now = time.time()
    if marker_newer:
        os.utime(pkg, (now - 100, now - 100)); os.utime(marker, (now, now))
    else:
        os.utime(marker, (now - 100, now - 100)); os.utime(pkg, (now, now))


def test_npm_install_skipped_only_when_current(env):
    _npm_layout(env["ws"], marker_newer=True)
    r = ce(env, "npm install")
    assert "skipped" in r["message"].lower()
    assert "up to date, audited" not in str(r)  # no fabricated npm output


def test_npm_install_runs_when_package_json_changed(env):
    _npm_layout(env["ws"], marker_newer=False)
    r = ce(env, "npm install")
    assert "skipped" not in r["message"].lower()


def test_npm_install_with_package_never_skipped(env):
    _npm_layout(env["ws"], marker_newer=True)
    r = ce(env, "npm install left-pad")
    assert "skipped" not in r["message"].lower()


# --- test_runner (S3) -----------------------------------------------------------------------

def tr(env, **args):
    return run(_TestRunnerTool().execute(agent_id="W1", agent_sandbox_path=env["sb"],
                                        project_name="Proj", session_name="sess", **args))


def test_test_runner_respects_kill_switch(env, monkeypatch):
    monkeypatch.setattr(settings, "DISABLE_COMMAND_EXECUTION", True, raising=False)
    marker = env["ws"] / "ran"
    r = tr(env, command="touch ran")
    assert r["status"] == "error" and "DISABLE_COMMAND_EXECUTION" in r["message"]
    assert not marker.exists()


def test_test_runner_env_has_no_secrets(env):
    r = tr(env, command="env")
    assert r["status"] == "success"
    assert "leaked-value-123" not in r.get("stdout", "")


def test_test_runner_exit_codes(env):
    assert tr(env, command="true")["status"] == "success"
    r = tr(env, command="exit 1")
    assert r["status"] == "error" and r["exit_code"] == 1


def test_test_runner_working_dir_traversal_rejected(env):
    (env["ws"] / "a").mkdir()
    r = tr(env, command="pwd", working_dir="a/../../..")
    assert r["status"] == "error" and "Invalid" in r["message"]


def test_test_runner_working_dir_ok(env):
    (env["ws"] / "pkg").mkdir()
    r = tr(env, command="pwd", working_dir="pkg")
    assert r["status"] == "success" and r["stdout"].endswith("pkg")


# --- codebase_search (S7) ---------------------------------------------------------------------

def cs(env, **args):
    return run(CodebaseSearchTool().execute(agent_id="W1", agent_sandbox_path=env["sb"],
                                            project_name="Proj", session_name="sess", **args))


def test_search_query_starting_with_dash(env):
    (env["ws"] / "a.txt").write_text("value --verbose here\n")
    r = cs(env, query="--verbose")
    assert r["status"] == "success" and "a.txt" in r["results"]


def test_search_results_are_relative_and_skip_node_modules(env):
    (env["ws"] / "src").mkdir()
    (env["ws"] / "src" / "m.py").write_text("def target_fn():\n    pass\n")
    (env["ws"] / "node_modules").mkdir()
    (env["ws"] / "node_modules" / "x.js").write_text("target_fn\n")
    r = cs(env, query="target_fn")
    assert "src/m.py:1:" in r["results"]
    assert "node_modules" not in r["results"]
    assert str(env["ws"]) not in r["results"]


def test_search_path_traversal_rejected(env):
    (env["ws"] / "a").mkdir()
    r = cs(env, query="x", path="a/../../..")
    assert r["status"] == "error"


def test_search_long_lines_capped(env):
    (env["ws"] / "min.js").write_text("needle" + "x" * 5000 + "\n")
    r = cs(env, query="needle")
    assert "line truncated" in r["results"] and len(r["results"]) < 1000


def test_search_no_matches(env):
    (env["ws"] / "a.txt").write_text("nothing\n")
    r = cs(env, query="zzz_not_there")
    assert r["status"] == "success" and r["message"] == "No matches found."
# END OF FILE tests/unit/test_shell_tools.py
