# START OF FILE src/tools/sandbox_runner.py
"""
Single choke point for every shell command an agent can trigger
(``command_executor`` and ``test_runner``).

What it guarantees (Review items S1, S3, R4, R10):

* ``DISABLE_COMMAND_EXECUTION`` is enforced here, so no tool can bypass it.
* The child gets a **scrubbed environment** built from an allowlist. API keys,
  tokens, ``SECRET_KEY`` and every other framework secret loaded from ``.env``
  are never inherited. ``HOME`` points at a dedicated sandbox home by default,
  so ``~/.npmrc``, ``~/.git-credentials`` and similar files are not picked up.
* stdin is ``/dev/null``: interactive prompts fail fast instead of hanging.
* The child runs in its **own session / process group**
  (``start_new_session=True``, thread-safe, unlike ``preexec_fn=os.setsid``).
  On timeout or cancellation the whole group is terminated, then killed.
* Output is captured to temporary files instead of pipes. A backgrounded child
  that keeps the inherited stdout open can therefore no longer block the call
  until timeout. Commands ending in a single ``&`` are detached explicitly and
  their output goes to a log file the agent can read later.
* Captured output is bounded (head + tail) before it is decoded.
* Round 3f: before a timed-out group is stopped, its CPU use over one second is measured (``SandboxResult.cpu_busy``,
  Linux /proc only), so the agent can be told whether the program was stuck in a loop (busy) or waiting (idle).
* Round 3u: a pytest command gets ``-o faulthandler_timeout=<half the timeout>`` in ``PYTEST_ADDOPTS``, so a test that
  does not end prints where it is stuck (``with_pytest_hang_report``).

This is NOT an OS-level sandbox: the command still runs as the server user and
can reach the network and any path that user can. Deterministic command rules
live in ``src/tools/policy_gate.py`` and are applied by ``ToolExecutor``.
"""
import asyncio
import logging
import os
import re
import signal
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# Bytes read back per stream (half from the head, half from the tail).
DEFAULT_MAX_CAPTURE_BYTES = 256 * 1024
# Seconds between SIGTERM and SIGKILL when stopping a process group.
KILL_GRACE_SECONDS = 2.0
# Folder (relative to the command's cwd) that receives detached-process logs.
BACKGROUND_LOG_DIR = ".te_background"

KILL_SWITCH_MESSAGE = (
    "Command execution is globally disabled by the administrator "
    "(DISABLE_COMMAND_EXECUTION is set)."
)

# --- Environment allowlist -------------------------------------------------
_ENV_ALLOW_EXACT = frozenset({
    "PATH", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TZ", "TERM",
    "TMPDIR", "TMP", "TEMP",
    # Termux / Android essentials (shebangs and linking break without them)
    "PREFIX", "LD_PRELOAD", "LD_LIBRARY_PATH",
    # Python / Node / toolchain discovery
    "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "PYTHONIOENCODING", "PYTHONUTF8",
    "NODE_PATH", "NVM_DIR", "NVM_BIN", "NVM_INC",
    "GOPATH", "GOROOT", "CARGO_HOME", "RUSTUP_HOME", "JAVA_HOME",
    # TLS trust stores (needed behind corporate / custom CAs)
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "CURL_CA_BUNDLE",
    # Proxies (dropped below if they embed credentials)
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
})
_ENV_ALLOW_PREFIXES = ("LC_", "ANDROID_", "TERMUX_", "XDG_")
_PROXY_VARS = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"})
# Names that look like secrets are dropped even if they match the allowlist.
_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|PRIVATE)", re.IGNORECASE)

# Non-interactive defaults applied to every sandboxed command.
_NON_INTERACTIVE_DEFAULTS: Dict[str, str] = {
    "PYTHONUNBUFFERED": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_NO_INPUT": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "DEBIAN_FRONTEND": "noninteractive",
    "NPM_CONFIG_YES": "true",
    "NPM_CONFIG_UPDATE_NOTIFIER": "false",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
}

# A single trailing '&' (not '&&', not '|&').
_TRAILING_BACKGROUND_RE = re.compile(r"(?<![&|>])&\s*$")


@dataclass
class SandboxResult:
    """Outcome of a sandboxed shell command."""
    status: str  # "completed" | "timeout" | "blocked" | "error"
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    duration_seconds: float = 0.0
    background_log: Optional[str] = None  # path relative to cwd, when detached
    message: str = ""
    command_executed: str = ""
    # Round 3f: on timeout, the CPU cores the process group used in its last second (1.0 = one core busy), None if unknown
    cpu_busy: Optional[float] = None


# --- Settings helpers (lazy, so the module stays importable in unit tests) ---

def _get_settings():
    try:
        from src.config.settings import settings
        return settings
    except Exception as e:  # pragma: no cover - only when settings cannot load
        logger.error(f"sandbox_runner: could not load settings: {e}")
        return None


def is_command_execution_disabled() -> bool:
    s = _get_settings()
    return bool(getattr(s, "DISABLE_COMMAND_EXECUTION", False)) if s is not None else False


def default_sandbox_home() -> Optional[Path]:
    """Dedicated HOME for agent shells, or None to keep the real HOME."""
    s = _get_settings()
    if s is None or not getattr(s, "SANDBOX_ISOLATE_HOME", True):
        return None
    home = getattr(s, "SANDBOX_HOME_DIR", None)
    if home:
        return Path(home)
    try:
        from src.config.settings import BASE_DIR
        return BASE_DIR / "sandboxes" / "_shell_home"
    except Exception:
        return None


def _configured_passthrough() -> Tuple[str, ...]:
    s = _get_settings()
    return tuple(getattr(s, "SANDBOX_ENV_PASSTHROUGH", []) or []) if s is not None else ()


def resolve_shared_workspace(projects_base: Path, project_name: str, session_name: str) -> Path:
    """
    Returns the session's shared_workspace path.

    Some code paths used the raw project name and others replaced spaces with
    underscores. FileSystemTool (where agents actually write files) and
    InteractionHandler always use the underscore form, so it wins whenever it
    exists; the raw name is only used if it is the sole existing folder.
    """
    safe_name = project_name.replace(" ", "_").strip()[:50].rstrip('_')
    raw = Path(projects_base) / project_name[:50].rstrip('_') / session_name / "shared_workspace"
    safe = Path(projects_base) / safe_name / session_name / "shared_workspace"
    try:
        if safe.is_dir():
            return safe
    except OSError:
        pass
    try:
        if raw.is_dir():
            return raw
    except OSError:
        pass
    return safe


# --- Environment -------------------------------------------------------------

def build_sandbox_env(
    home_dir: Optional[Path] = None,
    passthrough: Iterable[str] = (),
    base_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """
    Builds a minimal environment for agent shells from an allowlist.

    ``passthrough`` names are copied verbatim from ``base_env`` even when they
    look like secrets: this is the administrator's explicit opt-in
    (``SANDBOX_ENV_PASSTHROUGH``).
    """
    source = dict(os.environ if base_env is None else base_env)
    env: Dict[str, str] = {}
    for name, value in source.items():
        if name not in _ENV_ALLOW_EXACT and not name.startswith(_ENV_ALLOW_PREFIXES):
            continue
        if _SECRET_NAME_RE.search(name):
            continue
        if name in _PROXY_VARS and "@" in value:
            continue  # proxy URL with embedded credentials
        env[name] = value

    real_home = source.get("HOME")
    if home_dir is not None:
        try:
            Path(home_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"sandbox_runner: could not create sandbox HOME {home_dir}: {e}")
        env["HOME"] = str(home_dir)
        # Keep rustup/cargo toolchains working when HOME is isolated.
        if real_home:
            for var, sub in (("CARGO_HOME", ".cargo"), ("RUSTUP_HOME", ".rustup")):
                if var not in env and (Path(real_home) / sub).is_dir():
                    env[var] = str(Path(real_home) / sub)
    elif real_home:
        env["HOME"] = real_home

    env.setdefault("PATH", os.defpath)
    env.setdefault("TERM", "dumb")
    for name, value in _NON_INTERACTIVE_DEFAULTS.items():
        env.setdefault(name, value)

    for name in passthrough:
        if name in source:
            env[name] = source[name]
    return env


# --- Round 3u: where a pytest run that does not end is stuck ------------------------
# 2026-10-26 19:01:24 W2's `python -m pytest -q` timed out after 30 s with "the test run did not END" and nothing else; W2
# guessed that public/game.js (a browser file pytest never loads) looped and edited it. pytest's faulthandler prints the
# stack of a test that runs longer than faulthandler_timeout seconds - the test and the line of the code it is stuck in -
# and goes on; that output reaches the captured stderr even when the run is then terminated
# (src/utils/test_diagnosis.hung_test_note() reads it).
PYTEST_HANG_REPORT_FRACTION = 0.5
PYTEST_HANG_REPORT_MIN_SECONDS = 5


def pytest_hang_report_seconds(timeout: float) -> int:
    """Round 3u: after how many seconds a single test's stack is printed (half the run's timeout, at least 5 s)."""
    try:
        return max(PYTEST_HANG_REPORT_MIN_SECONDS, int(float(timeout) * PYTEST_HANG_REPORT_FRACTION))
    except (TypeError, ValueError):
        return PYTEST_HANG_REPORT_MIN_SECONDS


def with_pytest_hang_report(env: Dict[str, str], command: str, timeout: float) -> Dict[str, str]:
    """
    Round 3u: for a command that runs pytest, env with '-o faulthandler_timeout=<seconds>' added to PYTEST_ADDOPTS (an
    option the agent or the project set is kept: it comes later and wins). Other commands: env unchanged.
    """
    if "pytest" not in str(command or ""):
        return env
    option = f"-o faulthandler_timeout={pytest_hang_report_seconds(timeout)}"
    current = str(env.get("PYTEST_ADDOPTS") or "").strip()
    if "faulthandler_timeout" in current:
        return env
    updated = dict(env)
    updated["PYTEST_ADDOPTS"] = f"{option} {current}".strip()
    return updated


# --- Background handling -------------------------------------------------------

def detach_trailing_background(command: str, cwd: Path) -> Tuple[str, Optional[str]]:
    """
    If ``command`` ends with a single '&', rewrites it so the background job's
    output goes to a log file and stdin is /dev/null.

    Returns ``(command_to_run, log_path_relative_to_cwd_or_None)``.
    """
    stripped = command.rstrip()
    if not _TRAILING_BACKGROUND_RE.search(stripped):
        return command, None
    body = _TRAILING_BACKGROUND_RE.sub("", stripped).rstrip()
    if not body:
        return command, None
    log_dir = Path(cwd) / BACKGROUND_LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"sandbox_runner: cannot create {log_dir}: {e}. Background output goes to /dev/null.")
        return f"( {body}\n) > /dev/null 2>&1 < /dev/null &", None
    log_rel = f"{BACKGROUND_LOG_DIR}/bg_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid() % 1000:03d}{int(time.time() * 1000) % 1000:03d}.log"
    # Newline before ')' keeps a trailing '# comment' in the body from swallowing it.
    return f"( {body}\n) > '{log_rel}' 2>&1 < /dev/null &", log_rel


# --- Process control -------------------------------------------------------------

def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)  # start_new_session=True => pgid == pid
    except (ProcessLookupError, PermissionError):
        pass
    except Exception as e:
        logger.debug(f"sandbox_runner: killpg({pid}, {sig}) failed: {e}")


async def _terminate_group(process: "asyncio.subprocess.Process") -> None:
    if process.returncode is not None:
        # Shell already gone; still clean up anything left in its group.
        _signal_group(process.pid, signal.SIGKILL)
        return
    _signal_group(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=KILL_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass
    _signal_group(process.pid, signal.SIGKILL)
    try:
        await asyncio.wait_for(process.wait(), timeout=KILL_GRACE_SECONDS)
    except asyncio.TimeoutError:
        logger.error(f"sandbox_runner: process {process.pid} did not exit after SIGKILL.")


# --- Round 3f: was a timed-out command busy or waiting? ---------------------------------------------------------------
# 2026-10-23 20:47-21:26 (WiFi_Snake_Multiplayer_Game) `node test/game.test.js` and `npm test` timed out 4 times: the game's
# spawnFood() looped forever (a 0x0 board has no free cell). The agent was told about interactive prompts and servers left
# open, and kept looking for a socket that was never opened. A loop keeps a CPU core busy; a process that waits does not.
CPU_SAMPLE_SECONDS = 1.0


def _group_cpu_seconds(pgid: int) -> Optional[float]:
    """CPU seconds (user + system) used so far by the live processes of process group pgid; None without /proc."""
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    try:
        ticks_per_second = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):
        return None
    total = 0
    found = False
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 2:].split()
        try:
            if int(fields[2]) != pgid:  # fields after the name: state, ppid, pgrp, ...
                continue
            total += int(fields[11]) + int(fields[12])  # utime, stime
            found = True
        except (IndexError, ValueError):
            continue
    return total / ticks_per_second if found else None


async def group_cpu_rate(pgid: int, interval: float = CPU_SAMPLE_SECONDS) -> Optional[float]:
    """CPU cores process group pgid used over the next `interval` seconds (1.0 = one core fully busy), or None."""
    try:
        first = _group_cpu_seconds(pgid)
        if first is None:
            return None
        await asyncio.sleep(interval)
        second = _group_cpu_seconds(pgid)
        if second is None:
            return None
        return round(max(0.0, second - first) / interval, 2)
    except Exception as e:
        logger.debug(f"sandbox_runner: CPU sample of group {pgid} failed: {e}")
        return None


def _read_capped(fileobj, max_bytes: int) -> Tuple[str, bool, int]:
    """Returns (text, truncated, total_bytes); keeps head and tail when large."""
    try:
        fileobj.flush()
        fileobj.seek(0, os.SEEK_END)
        total = fileobj.tell()
        fileobj.seek(0)
        if total <= max_bytes:
            data = fileobj.read(total)
            return data.decode("utf-8", errors="replace"), False, total
        half = max(1, max_bytes // 2)
        head = fileobj.read(half)
        fileobj.seek(total - half)
        tail = fileobj.read(half)
        text = (
            head.decode("utf-8", errors="replace")
            + f"\n...[{total - 2 * half} bytes omitted]...\n"
            + tail.decode("utf-8", errors="replace")
        )
        return text, True, total
    except Exception as e:
        logger.error(f"sandbox_runner: failed to read captured output: {e}")
        return "", False, 0


async def run_sandboxed(
    command: str,
    cwd: Path,
    timeout: float,
    *,
    tool_name: str = "command_executor",
    agent_id: str = "unknown",
    max_capture_bytes: int = DEFAULT_MAX_CAPTURE_BYTES,
    env: Optional[Dict[str, str]] = None,
    kill_switch: Optional[bool] = None,
) -> SandboxResult:
    """
    Runs ``command`` through the shell under the guarantees listed in the
    module docstring. ``env`` and ``kill_switch`` exist for tests; production
    callers leave them as None so settings are used.
    """
    disabled = is_command_execution_disabled() if kill_switch is None else kill_switch
    if disabled:
        logger.warning(f"sandbox_runner: blocked '{tool_name}' for agent {agent_id}: DISABLE_COMMAND_EXECUTION is set.")
        return SandboxResult(status="blocked", message=KILL_SWITCH_MESSAGE, command_executed=command)

    cwd = Path(cwd)
    if not cwd.is_dir():
        return SandboxResult(status="error", message=f"Working directory does not exist: {cwd}", command_executed=command)

    if env is None:
        env = build_sandbox_env(home_dir=default_sandbox_home(), passthrough=_configured_passthrough())
        env = with_pytest_hang_report(env, command, timeout)  # Round 3u

    command_to_run, background_log = detach_trailing_background(command, cwd)
    if background_log:
        logger.info(f"sandbox_runner: detached background command for agent {agent_id}; log -> {background_log}")

    start = time.monotonic()
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        try:
            process = await asyncio.create_subprocess_shell(
                command_to_run,
                cwd=str(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                env=env,
                start_new_session=True,
            )
        except Exception as e:
            logger.error(f"sandbox_runner: failed to start '{tool_name}' command for agent {agent_id}: {e}", exc_info=True)
            return SandboxResult(status="error", message=f"Error starting command: {type(e).__name__} - {e}",
                                 command_executed=command_to_run)

        status = "completed"
        cpu_busy: Optional[float] = None
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            status = "timeout"
            cpu_busy = await group_cpu_rate(process.pid)  # Round 3f: busy (a loop) or waiting?
            logger.warning(f"sandbox_runner: '{tool_name}' command for agent {agent_id} timed out after {timeout}s "
                           f"(CPU in its last second: {cpu_busy if cpu_busy is not None else 'unknown'}); "
                           f"terminating process group {process.pid}.")
            await _terminate_group(process)
        except asyncio.CancelledError:
            logger.warning(f"sandbox_runner: '{tool_name}' command for agent {agent_id} cancelled; terminating process group {process.pid}.")
            await _terminate_group(process)
            raise

        duration = time.monotonic() - start
        stdout, out_trunc, out_total = _read_capped(out_f, max_capture_bytes)
        stderr, err_trunc, err_total = _read_capped(err_f, max_capture_bytes)

    return SandboxResult(
        status=status,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=out_trunc,
        stderr_truncated=err_trunc,
        stdout_total_bytes=out_total,
        stderr_total_bytes=err_total,
        duration_seconds=round(duration, 2),
        background_log=background_log,
        command_executed=command_to_run,
        cpu_busy=cpu_busy,
    )
# END OF FILE src/tools/sandbox_runner.py
