# START OF FILE tests/unit/test_sandbox_runner.py
"""
Characterization tests for src/tools/sandbox_runner.py
(Review items S1, S3, R4, R10). Tool-level tests are in test_shell_tools.py.

Run from the repo root inside the venv:
    python -m pytest tests/unit/test_sandbox_runner.py -q
No pytest-asyncio needed: coroutines are driven with asyncio.run().
"""
import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tools import sandbox_runner as sr  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell semantics")


def run(coro):
    return asyncio.run(coro)


def _alive(pid: int) -> bool:
    """True if pid is running. Zombies (killed, not yet reaped by init) count as dead."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            return stat.read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except Exception:
            return True
    return True


# --- environment scrubbing (S1) ----------------------------------------------

def test_env_drops_secrets_and_unknown_vars(tmp_path):
    base = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": "/home/real",
        "OPENAI_API_KEY": "sk-should-not-leak",
        "OPENROUTER_API_KEY_1": "sk-or-leak",
        "SECRET_KEY": "jwt-secret",
        "GITHUB_ACCESS_TOKEN": "ghp_leak",
        "RANDOM_APP_SETTING": "x",
        "LC_ALL": "C.UTF-8",
        "TERMUX_VERSION": "0.118",
        "HTTPS_PROXY": "http://user:pass@proxy:3128",
        "HTTP_PROXY": "http://proxy:3128",
    }
    env = sr.build_sandbox_env(home_dir=tmp_path / "home", base_env=base)
    for leaked in ("OPENAI_API_KEY", "OPENROUTER_API_KEY_1", "SECRET_KEY", "GITHUB_ACCESS_TOKEN", "RANDOM_APP_SETTING"):
        assert leaked not in env
    assert "HTTPS_PROXY" not in env, "proxy URL with credentials must be dropped"
    assert env["HTTP_PROXY"] == "http://proxy:3128"
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["LC_ALL"] == "C.UTF-8"
    assert env["TERMUX_VERSION"] == "0.118"
    assert env["HOME"] == str(tmp_path / "home")
    assert (tmp_path / "home").is_dir()
    assert env["PIP_NO_INPUT"] == "1"


def test_env_passthrough_is_explicit_opt_in(tmp_path):
    base = {"PATH": "/bin", "MY_SERVICE_TOKEN": "t"}
    assert "MY_SERVICE_TOKEN" not in sr.build_sandbox_env(home_dir=None, base_env=base)
    env = sr.build_sandbox_env(home_dir=None, base_env=base, passthrough=["MY_SERVICE_TOKEN"])
    assert env["MY_SERVICE_TOKEN"] == "t"


def test_env_keeps_real_home_when_isolation_disabled():
    env = sr.build_sandbox_env(home_dir=None, base_env={"PATH": "/bin", "HOME": "/home/real"})
    assert env["HOME"] == "/home/real"


def test_child_process_cannot_print_parent_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PROVIDER_API_KEY", "leaked-value-123")
    env = sr.build_sandbox_env(home_dir=tmp_path / "h")
    res = run(sr.run_sandboxed("printenv FAKE_PROVIDER_API_KEY; env", tmp_path, 10, env=env, kill_switch=False))
    assert res.status == "completed"
    assert "leaked-value-123" not in res.stdout


# --- kill switch (S3 / D4) --------------------------------------------------------

def test_kill_switch_blocks_before_spawning(tmp_path):
    marker = tmp_path / "ran"
    res = run(sr.run_sandboxed(f"touch '{marker}'", tmp_path, 10, env={"PATH": os.defpath}, kill_switch=True))
    assert res.status == "blocked"
    assert "DISABLE_COMMAND_EXECUTION" in res.message
    assert not marker.exists()


# --- basic execution ---------------------------------------------------------------

def test_captures_stdout_stderr_and_returncode(tmp_path):
    res = run(sr.run_sandboxed("echo out; echo err >&2; exit 3", tmp_path, 10, env={"PATH": os.defpath}, kill_switch=False))
    assert res.status == "completed"
    assert res.returncode == 3
    assert res.stdout.strip() == "out"
    assert res.stderr.strip() == "err"


def test_stdin_is_closed_so_prompts_do_not_hang(tmp_path):
    start = time.monotonic()
    res = run(sr.run_sandboxed("read answer; echo got:$answer", tmp_path, 10, env={"PATH": os.defpath}, kill_switch=False))
    assert time.monotonic() - start < 5
    assert res.status == "completed"
    assert "got:" in res.stdout


def test_missing_cwd_is_an_error(tmp_path):
    res = run(sr.run_sandboxed("true", tmp_path / "nope", 5, env={"PATH": os.defpath}, kill_switch=False))
    assert res.status == "error"


def test_large_output_is_capped_head_and_tail(tmp_path):
    cmd = "python3 -c \"print('A'*5000 + 'B'*5000)\""
    res = run(sr.run_sandboxed(cmd, tmp_path, 10, env=dict(os.environ), kill_switch=False, max_capture_bytes=1000))
    assert res.stdout_truncated
    assert res.stdout_total_bytes > 1000
    assert res.stdout.startswith("A") and res.stdout.rstrip().endswith("B")
    assert "bytes omitted" in res.stdout


# --- timeouts and process groups (R4, R10) ---------------------------------------

def test_timeout_kills_whole_process_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    cmd = f"sleep 60 & echo $! > '{pidfile}'; wait"
    start = time.monotonic()
    res = run(sr.run_sandboxed(cmd, tmp_path, 1.5, env={"PATH": os.defpath}, kill_switch=False))
    assert res.status == "timeout"
    assert time.monotonic() - start < 10
    child = int(pidfile.read_text().strip())
    time.sleep(0.2)
    assert not _alive(child), "background child survived the timeout"


def test_trailing_ampersand_returns_immediately_and_logs(tmp_path):
    start = time.monotonic()
    res = run(sr.run_sandboxed("echo started; sleep 30 &", tmp_path, 20, env={"PATH": os.defpath}, kill_switch=False))
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"background command blocked for {elapsed:.1f}s"
    assert res.status == "completed"
    assert res.background_log and res.background_log.startswith(".te_background/")
    log = tmp_path / res.background_log
    for _ in range(20):
        if log.exists() and "started" in log.read_text():
            break
        time.sleep(0.1)
    assert "started" in log.read_text()


def test_mid_command_background_does_not_block(tmp_path):
    # Not a trailing '&': the background child inherits stdout. With pipes this
    # used to block until timeout; with file capture it returns at once.
    start = time.monotonic()
    res = run(sr.run_sandboxed("sleep 30 & echo done", tmp_path, 20, env={"PATH": os.defpath}, kill_switch=False))
    assert time.monotonic() - start < 5
    assert res.status == "completed"
    assert "done" in res.stdout


def test_detach_rewrite_only_for_single_trailing_ampersand(tmp_path):
    assert sr.detach_trailing_background("a && b", tmp_path) == ("a && b", None)
    assert sr.detach_trailing_background("a |& b", tmp_path)[1] is None
    cmd, log = sr.detach_trailing_background("python server.py &", tmp_path)
    assert log is not None and "python server.py" in cmd and cmd.rstrip().endswith("&")


# --- shared workspace resolution ---------------------------------------------------

def test_resolve_shared_workspace_prefers_existing(tmp_path):
    raw = tmp_path / "My Proj" / "s1" / "shared_workspace"
    safe = tmp_path / "My_Proj" / "s1" / "shared_workspace"
    assert sr.resolve_shared_workspace(tmp_path, "My Proj", "s1") == safe   # neither exists
    raw.mkdir(parents=True)
    assert sr.resolve_shared_workspace(tmp_path, "My Proj", "s1") == raw    # only raw exists
    safe.mkdir(parents=True)
    assert sr.resolve_shared_workspace(tmp_path, "My Proj", "s1") == safe   # both: match file_system
# END OF FILE tests/unit/test_sandbox_runner.py
