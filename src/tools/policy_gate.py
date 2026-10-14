# START OF FILE src/tools/policy_gate.py
"""
Deterministic pre-execution policy gate for tool calls (Review item S2).

``ToolExecutor`` calls :func:`evaluate_tool_call` after authorization and
argument validation and before a tool runs. Unlike the Constitutional
Guardian, which only reviews final text responses, this gate sees every
native and legacy tool call, costs no LLM tokens and always gives the same
answer for the same input.

Currently gated: the shell-running tools (``command_executor``,
``test_runner``). All other tools pass straight through.

Rules (rule ids appear in logs and in the message shown to the agent):

* P001 privilege_escalation  sudo / doas / pkexec / su
* P002 remote_script_pipe    curl|wget ... | sh, bash <(curl ...), sh -c "$(curl ...)"
* P003 secret_access         /proc/*/environ, ~/.ssh, private keys, .git-credentials,
                             .netrc, cloud credentials, /etc/shadow, .env files outside
                             the current session
* P004 framework_internals   any path that resolves inside the TrippleEffect install
                             but outside the projects and sandboxes folders
* P005 destructive_outside   rm / mv / chmod / chown / shred / truncate / unlink with a
                             target outside the session, the agent sandbox and temp
                             dirs; mkfs, dd to /dev, shutdown/reboot, fork bombs
* P006 framework_kill        killing python / uvicorn / the framework process

Modes (``COMMAND_POLICY_MODE``): ``enforce`` (default) blocks, ``warn`` logs and
allows, ``off`` skips evaluation.

This is defence in depth, not a sandbox: a determined model can still obfuscate
a command. It reliably stops the common accidental and prompt-injected cases.
"""
import logging
import os
import re
import shlex
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# tool name -> argument that holds the shell command
GATED_TOOLS: Dict[str, str] = {
    "command_executor": "command",
    "test_runner": "command",
}

VALID_MODES = ("enforce", "warn", "off")


@dataclass
class PolicyDecision:
    allowed: bool
    rule_id: str = ""
    rule_name: str = ""
    reason: str = ""
    suggestion: str = ""
    mode: str = "enforce"

    def format_for_agent(self) -> str:
        parts = [f"[Policy Gate] Command blocked by rule {self.rule_id} ({self.rule_name}): {self.reason}"]
        if self.suggestion:
            parts.append(self.suggestion)
        parts.append(
            "This check is deterministic: rewording or re-sending the same command will be blocked again. "
            "Choose a different approach that stays inside your workspace."
        )
        return " ".join(parts)


_ALLOW = PolicyDecision(allowed=True)

# --- Regex rules on the raw command ------------------------------------------------
_CMD_START = r"(?:^|[;&|(`]|\$\()\s*"

_P001_RE = re.compile(_CMD_START + r"(?:sudo|doas|pkexec)\b|" + _CMD_START + r"su(?:\s+(?:-\w*|root)\b|\s*$|\s*[;&|])", re.IGNORECASE)
_P002_RES = [
    re.compile(r"\b(?:curl|wget|fetch)\b[^|;&]*\|\s*(?:sudo\s+)?(?:env\s+)?(?:ba|z|da|k|c|tc)?sh\b", re.IGNORECASE),
    re.compile(r"\b(?:curl|wget|fetch)\b[^|;&]*\|\s*(?:sudo\s+)?python[0-9.]*\b(?!\s+-m\s+json\.tool)", re.IGNORECASE),
    re.compile(r"\b(?:ba|z|da|k)?sh\s+<\(\s*(?:curl|wget)\b", re.IGNORECASE),
    re.compile(r"\b(?:ba|z|da|k)?sh\s+-c\s+[\"']?\$\(\s*(?:curl|wget)\b", re.IGNORECASE),
]
_P003_RES = [
    (re.compile(r"/proc/[^\s/]+/environ\b"), "reading another process's environment"),
    (re.compile(r"(?:^|[\s'\"=:/])~?/?\.ssh(?:/|\b)"), "accessing SSH keys/configuration"),
    (re.compile(r"\bid_(?:rsa|dsa|ecdsa|ed25519)\b"), "accessing a private SSH key"),
    (re.compile(r"\.git-credentials\b"), "accessing stored git credentials"),
    (re.compile(r"(?:^|[\s'\"=/])\.netrc\b"), "accessing .netrc credentials"),
    (re.compile(r"\.aws/credentials\b|\.config/gcloud\b|\.azure/\b|\.kube/config\b"), "accessing cloud credentials"),
    (re.compile(r"\.config/gh/hosts\.yml\b|\.docker/config\.json\b"), "accessing CLI credentials"),
    (re.compile(r"/etc/(?:shadow|gshadow|sudoers)\b"), "accessing system credential files"),
]
_P005_RES = [
    (re.compile(_CMD_START + r"mkfs(?:\.\w+)?\b", re.IGNORECASE), "formatting a filesystem"),
    (re.compile(r"\bdd\b[^;&|]*\bof=/dev/", re.IGNORECASE), "writing directly to a device"),
    (re.compile(r">\s*/dev/(?:sd|nvme|mmcblk|hd|vd)", re.IGNORECASE), "writing directly to a device"),
    (re.compile(_CMD_START + r"(?:shutdown|reboot|poweroff|halt|init\s+[06])\b", re.IGNORECASE), "shutting down or rebooting the host"),
    (re.compile(r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:"), "fork bomb"),
]
_P006_KILL_RE = re.compile(r"\bkill\s+(?:-\S+\s+)*-1\b")

_DESTRUCTIVE_COMMANDS = frozenset({"rm", "rmdir", "mv", "chmod", "chown", "chgrp", "shred", "truncate", "unlink"})
_KILL_COMMANDS = frozenset({"pkill", "killall"})
_FRAMEWORK_PROCESS_NAMES = frozenset({"python", "python3", "uvicorn", "src.main", "trippleeffect"})
_SHELL_OPERATORS = frozenset({";", "&&", "||", "|", "&", "(", ")", "|&", ";;", "\n"})
_ENV_FILE_RE = re.compile(r"^\.env(?:\.(?!example$|sample$|template$)[\w.-]+)?$")


# --- Settings ---------------------------------------------------------------------

def _get_settings():
    try:
        from src.config.settings import settings
        return settings
    except Exception as e:  # pragma: no cover
        logger.error(f"policy_gate: could not load settings: {e}")
        return None


def _get_base_dir() -> Optional[Path]:
    try:
        from src.config.settings import BASE_DIR
        return Path(BASE_DIR)
    except Exception:
        return None


def get_policy_mode() -> str:
    s = _get_settings()
    mode = str(getattr(s, "COMMAND_POLICY_MODE", "enforce") or "enforce").lower() if s is not None else "enforce"
    return mode if mode in VALID_MODES else "enforce"


# --- Path helpers -------------------------------------------------------------------

def _safe_resolve(p: Path) -> Path:
    try:
        return p.resolve(strict=False)
    except Exception:
        return Path(os.path.normpath(str(p)))


def _is_under(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            if path == root or path.is_relative_to(root):
                return True
        except Exception:
            continue
    return False


def _tokenize(command: str) -> List[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return command.split()


def _path_candidates(token: str) -> List[str]:
    """A token and, for --opt=value / VAR=value tokens, the value part."""
    token = token.lstrip("<>0123456789&")  # redirection prefixes such as 2>, >>, <
    if not token:
        return []
    out = [token]
    if "=" in token:
        value = token.split("=", 1)[1]
        if value:
            out.append(value)
    return out


_HOME_VAR_RE = re.compile(r"^\$(?:HOME|\{HOME\})")


def _looks_like_path(token: str) -> bool:
    return "/" in token or token.startswith(("~", ".")) or bool(_HOME_VAR_RE.match(token))


def _resolve_token(token: str, cwd: Path, real_home: Optional[str]) -> Optional[Path]:
    if not _looks_like_path(token):
        return None
    if "://" in token:
        return None  # URL
    if real_home:
        if token.startswith("~"):
            token = real_home + token[1:]
        else:
            token = _HOME_VAR_RE.sub(lambda _m: real_home, token)
    p = Path(token)
    if not p.is_absolute():
        p = cwd / p
    return _safe_resolve(p)


def _split_simple_commands(tokens: List[str]) -> List[List[str]]:
    commands: List[List[str]] = []
    current: List[str] = []
    for tok in tokens:
        if tok in _SHELL_OPERATORS:
            if current:
                commands.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        commands.append(current)
    return commands


def _command_name(words: List[str]) -> Optional[str]:
    """First word that is not a VAR=value assignment or a wrapper such as env/nohup/time."""
    wrappers = {"env", "nohup", "time", "nice", "ionice", "command", "exec", "builtin"}
    for w in words:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            continue
        base = os.path.basename(w)
        if base in wrappers:
            continue
        return base
    return None


def _temp_roots() -> List[Path]:
    roots = {_safe_resolve(Path(tempfile.gettempdir()))}
    for var in ("TMPDIR", "TMP", "TEMP"):
        if os.environ.get(var):
            roots.add(_safe_resolve(Path(os.environ[var])))
    roots.add(Path("/tmp"))
    roots.add(Path("/var/tmp"))
    return list(roots)


# --- Core evaluation ----------------------------------------------------------------

def evaluate_command(
    command: str,
    *,
    cwd: Path,
    write_roots: List[Path],
    read_roots: List[Path],
    framework_root: Optional[Path],
    real_home: Optional[str] = None,
    framework_pid: Optional[int] = None,
) -> PolicyDecision:
    """
    Pure function: evaluates one shell command string.

    ``write_roots``  locations destructive commands may target (session dir,
                     agent sandbox); temp dirs are always added.
    ``read_roots``   locations inside ``framework_root`` that are not framework
                     internals (projects base dir, sandboxes dir).
    """
    if not command or not command.strip():
        return _ALLOW

    cwd = _safe_resolve(Path(cwd))
    explicit_write_roots = [_safe_resolve(Path(r)) for r in write_roots]
    temp_roots = _temp_roots()
    read_roots = [_safe_resolve(Path(r)) for r in read_roots]
    fw_root = _safe_resolve(Path(framework_root)) if framework_root else None

    def in_framework(p: Path) -> bool:
        return fw_root is not None and _is_under(p, [fw_root])

    def writable(p: Path) -> bool:
        # Temp dirs never override framework protection (e.g. an install under /tmp).
        if in_framework(p):
            return _is_under(p, explicit_write_roots)
        return _is_under(p, explicit_write_roots + temp_roots)

    # P002 remote script piping
    for rx in _P002_RES:
        if rx.search(command):
            return PolicyDecision(False, "P002", "remote_script_pipe",
                                  "piping a downloaded script straight into an interpreter is not allowed.",
                                  "Download the file into the workspace first, read it, then run it explicitly if it is safe.")
    # P001 privilege escalation
    if _P001_RE.search(command):
        return PolicyDecision(False, "P001", "privilege_escalation",
                              "privilege escalation (sudo/su/doas/pkexec) is not allowed for agents.",
                              "Install packages without sudo (e.g. 'pip install --user', a virtualenv, or 'npm install' in the workspace).")
    # P003 well-known secret locations
    for rx, what in _P003_RES:
        if rx.search(command):
            return PolicyDecision(False, "P003", "secret_access", f"{what} is not allowed.",
                                  "Agents must not read credentials. Ask your supervisor or the user if a credential is genuinely required.")
    # P005 regex-level destructive operations
    for rx, what in _P005_RES:
        if rx.search(command):
            return PolicyDecision(False, "P005", "destructive_outside", f"{what} is not allowed.")
    # P006 kill -1
    if _P006_KILL_RE.search(command):
        return PolicyDecision(False, "P006", "framework_kill",
                              "'kill -1' would terminate every process of the server user, including the framework.")

    tokens = _tokenize(command)

    # Token-level path checks (P003 .env, P004 framework internals)
    for tok in tokens:
        if tok in _SHELL_OPERATORS:
            continue
        for cand in _path_candidates(tok):
            resolved = _resolve_token(cand, cwd, real_home)
            if resolved is None:
                continue
            is_env_file = bool(_ENV_FILE_RE.match(resolved.name))
            if in_framework(resolved) and not _is_under(resolved, read_roots):
                if is_env_file:
                    return PolicyDecision(False, "P003", "secret_access",
                                          f"'{cand}' is the framework's secrets file.",
                                          "Agents must not read framework credentials.")
                return PolicyDecision(False, "P004", "framework_internals",
                                      f"'{cand}' points into the TrippleEffect framework installation, which agents must not access.",
                                      "Use paths inside your workspace (relative paths are resolved from it).")
            if is_env_file and not writable(resolved):
                return PolicyDecision(False, "P003", "secret_access",
                                      f"'{cand}' is an environment/secrets file outside your session workspace.",
                                      "Only .env files inside your own project workspace may be used.")

    # Per simple-command checks (P005 destructive targets, P006 process kills)
    for words in _split_simple_commands(tokens):
        name = _command_name(words)
        if not name:
            continue
        idx = next((i for i, w in enumerate(words) if os.path.basename(w) == name), 0)
        args = words[idx + 1:]
        if name in _DESTRUCTIVE_COMMANDS:
            operands = [a for a in args if not (a.startswith("-") and a != "-") and a not in (">", ">>", "<", ">&", "&>")]
            if name in ("chmod", "chown", "chgrp") and operands:
                operands = operands[1:]  # first operand is the mode / owner
            for arg in operands:
                target = _resolve_token(arg if _looks_like_path(arg) else "./" + arg, cwd, real_home)
                if target is None:
                    continue
                if arg in ("/", "~", "~/") or not writable(target):
                    return PolicyDecision(False, "P005", "destructive_outside",
                                          f"'{name}' targets '{arg}', which is outside your session workspace and sandbox.",
                                          "Only modify or delete files inside your workspace.")
        elif name in _KILL_COMMANDS:
            for arg in args:
                if arg.startswith("-"):
                    continue
                if arg.lower() in _FRAMEWORK_PROCESS_NAMES or "src.main" in arg or "trippleeffect" in arg.lower():
                    return PolicyDecision(False, "P006", "framework_kill",
                                          f"'{name} {arg}' could terminate the TrippleEffect server itself.",
                                          "Stop only your own process, e.g. \"pkill -f 'http.server 8080'\" or kill the PID you started.")
        elif name == "kill" and framework_pid is not None:
            if str(framework_pid) in args:
                return PolicyDecision(False, "P006", "framework_kill",
                                      "this would terminate the TrippleEffect server process.")
    return _ALLOW


def _infer_cwd(tool_name: str, args: Dict[str, Any], agent_sandbox_path: Path,
               session_workspace: Optional[Path], projects_base: Optional[Path]) -> Path:
    scope = str(args.get("scope") or ("shared" if session_workspace else "private")).lower()
    if scope == "shared" and session_workspace is not None:
        base = session_workspace
    elif scope == "projects" and projects_base is not None:
        base = projects_base
    else:
        base = Path(agent_sandbox_path)
    if tool_name == "test_runner" and args.get("working_dir"):
        base = base / str(args.get("working_dir"))
    return base


def evaluate_tool_call(
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    agent_id: str,
    agent_type: str,
    agent_sandbox_path: Path,
    project_name: Optional[str],
    session_name: Optional[str],
) -> PolicyDecision:
    """Entry point used by ToolExecutor."""
    arg_name = GATED_TOOLS.get(tool_name)
    if not arg_name:
        return _ALLOW
    mode = get_policy_mode()
    if mode == "off":
        return PolicyDecision(True, mode="off")

    command = tool_args.get(arg_name)
    if not isinstance(command, str) or not command.strip():
        return _ALLOW  # the tool itself reports the missing/invalid argument

    s = _get_settings()
    base_dir = _get_base_dir()
    projects_base = Path(getattr(s, "PROJECTS_BASE_DIR")) if s is not None and getattr(s, "PROJECTS_BASE_DIR", None) else None

    session_workspace: Optional[Path] = None
    write_roots: List[Path] = [Path(agent_sandbox_path)]
    if projects_base is not None and project_name and session_name:
        from src.tools.sandbox_runner import resolve_shared_workspace
        session_workspace = resolve_shared_workspace(projects_base, project_name, session_name)
        write_roots.append(session_workspace.parent)  # whole session dir

    read_roots: List[Path] = []
    if projects_base is not None:
        read_roots.append(projects_base)
    if base_dir is not None:
        read_roots.append(base_dir / "sandboxes")

    cwd = _infer_cwd(tool_name, tool_args, agent_sandbox_path, session_workspace, projects_base)

    decision = evaluate_command(
        command,
        cwd=cwd,
        write_roots=write_roots,
        read_roots=read_roots,
        framework_root=base_dir,
        real_home=os.environ.get("HOME"),
        framework_pid=os.getpid(),
    )
    decision = replace(decision, mode=mode)  # copy: never mutate the shared _ALLOW instance
    if not decision.allowed:
        log_msg = (f"PolicyGate: {decision.rule_id} ({decision.rule_name}) matched for agent '{agent_id}' "
                   f"(type {agent_type}) tool '{tool_name}': {command[:300]!r} -> {decision.reason}")
        if mode == "warn":
            logger.warning(log_msg + " [mode=warn: ALLOWED]")
            return PolicyDecision(True, decision.rule_id, decision.rule_name, decision.reason, decision.suggestion, mode)
        logger.warning(log_msg + " [mode=enforce: BLOCKED]")
    return decision
# END OF FILE src/tools/policy_gate.py
