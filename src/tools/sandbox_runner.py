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
    underscores. Prefer whichever directory already exists (raw first, so
    existing sessions keep working); otherwise use the underscore form, which
    matches InteractionHandler and the other tools.
    """
    raw = Path(projects_base) / project_name / session_name / "shared_workspace"
    safe = Path(projects_base) / project_name.replace(" ", "_").strip() / session_name / "shared_workspace"
    if raw.is_dir():
        return raw
    if safe.is_dir():
        return safe
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
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            status = "timeout"
            logger.warning(f"sandbox_runner: '{tool_name}' command for agent {agent_id} timed out after {timeout}s; terminating process group {process.pid}.")
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
    )
# END OF FILE src/tools/sandbox_runner.py
