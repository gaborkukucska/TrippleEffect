# START OF FILE src/utils/program_run_check.py
"""
Does the project's program START? (Round 4d)

2026-10-28 20:03-23:38 (WiFi_Snake_Game, qwen3.5:4b) every task was finished, the last one "Kick-off Task 9: Run the server
and test on multiple devices to verify all features work correctly" with "All tests are now passing" - and the game could not
be played:
* src/server.js defined startServers() (it calls server.listen()) but nothing called it: `node src/server.js` ended at once
  with no output. W3 had run it at 21:56:32 (`node src/server.js &`), read the empty log and went back to the tests.
* the server answered every request with JSON - GET / never sent the page src/index/index.html.
* the README told the user to run `npm install` and `npm start`, but the project had no package.json (npm: ENOENT).
The tests passed because they mocked the server. A task that says to run / start the server or the program is finished only
when the program starts and keeps running - and, for a project with a web page, when the server sends a page for GET /.

* is_program_run_task(): the task text says to run / start / launch the server, app, program or game.
* server_entry(): the project's server entry (the "start" script of package.json, a README command, or the JS / Python file
  that starts a server) - None for a project without server code (a CLI program is not judged).
* program_run_problem(): runs the entry for RUN_CHECK_SECONDS; the reason it does not work, or None.
* readme_command_problems(): README commands that cannot work (npm without package.json, a script package.json lacks, a file
  that does not exist and is not planned).
Never raises; a check that cannot run (commands disabled, the port in use) blocks nothing.
"""
import asyncio
import json
import logging
import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

RUN_CHECK_SECONDS = 6.0
HTTP_PROBE_DELAY_SECONDS = 2.5
HTTP_PROBE_TIMEOUT_SECONDS = 2.0
MAX_SOURCE_BYTES = 400_000
_SKIP_DIRS = frozenset({"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", "coverage",
                        ".te_background", "tests", "test", "__tests__", "spec"})

_RUN_TASK_RE = re.compile(
    r"\b(?:run|runs|running|start|starts|starting|launch|launches|host|hosts|serve|serves|deploy|deploys)\b[^.\n]{0,40}?"
    r"\b(?:server|app|application|program|game|website|web ?site|project)\b", re.IGNORECASE)
_JS_SERVER_RE = re.compile(r"\.listen\s*\(|\bcreateServer\s*\(")
_PY_SERVER_RE = re.compile(r"\bserve_forever\s*\(|\bapp\.run\s*\(|\buvicorn\.run\s*\(|\bHTTPServer\s*\(|\bsocketio\.run\s*\(")
_ENTRY_NAMES = ("server.js", "app.js", "index.js", "main.js", "server.mjs", "app.mjs", "server.py", "app.py", "main.py")
_PORT_RES = (
    re.compile(r"\b(?:const|let|var)\s+(?:PORT|port|HTTP_PORT|SERVER_PORT)\s*=\s*(?:process\.env\.\w+\s*(?:\|\||\?\?)\s*)?"
               r"(?:Number\(|parseInt\()?['\"]?(\d{2,5})"),
    re.compile(r"\.listen\s*\(\s*(?:process\.env\.\w+\s*(?:\|\||\?\?)\s*)?(\d{2,5})\b"),
    re.compile(r"\b(?:PORT|port)\s*=\s*(?:int\()?(?:os\.environ\.get\([^)]*?,\s*)?['\"]?(\d{2,5})"),
    re.compile(r"\bport\s*=\s*(\d{2,5})\b"),
)
_README_NAMES = ("README.md", "readme.md", "Readme.md", "docs/README.md", "docs/readme.md")
_NPM_COMMAND_RE = re.compile(r"(?<![\w-])npm\s+(install|i|ci|start|test|run\s+[\w:.-]+)\b")
_FILE_COMMAND_RE = re.compile(r"(?<![\w-])(node|python3?|nodemon)\s+((?:\./)?[\w./-]+\.(?:m?js|cjs|py))\b")
_MISSING_TOOL_RE = re.compile(r"^(?:/bin/)?(?:ba|da|z)?sh: (?:\d+: )?(?:node|npm|python3?|nodemon): (?:command )?not found",
                              re.MULTILINE)
_ADDRESS_IN_USE_RE = re.compile(r"EADDRINUSE|address already in use|Address already in use", re.IGNORECASE)


def is_program_run_task(description: Optional[str]) -> bool:
    """True for a task that says to run / start / launch the server, app, program or game."""
    return bool(description) and bool(_RUN_TASK_RE.search(str(description)))


def _read(path: Path) -> str:
    try:
        if path.is_file() and path.stat().st_size <= MAX_SOURCE_BYTES:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _project_files(root: Path, suffixes: Tuple[str, ...]) -> List[str]:
    found = []
    try:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if set(part.lower() for part in rel.parts[:-1]) & _SKIP_DIRS or not path.is_file():
                continue
            if path.suffix.lower() in suffixes:
                found.append(rel.as_posix())
            if len(found) >= 300:
                break
    except (OSError, ValueError):
        pass
    return found


def _package(root: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def readme_files(root: Path) -> List[str]:
    return [name for name in _README_NAMES if (Path(root) / name).is_file()]


def _readme_commands(root: Path) -> List[Tuple[str, str]]:
    """(readme, command) pairs: npm commands and `node|python <file>` commands the READMEs tell the user to run."""
    commands: List[Tuple[str, str]] = []
    for readme in readme_files(root):
        text = _read(Path(root) / readme)
        for match in _NPM_COMMAND_RE.finditer(text):
            command = "npm " + " ".join(match.group(1).split())
            if (readme, command) not in commands:
                commands.append((readme, command))
        for match in _FILE_COMMAND_RE.finditer(text):
            command = f"{match.group(1)} {match.group(2)}"
            if (readme, command) not in commands:
                commands.append((readme, command))
    return commands


def _is_server_source(rel: str, source: str) -> bool:
    if rel.lower().endswith(".py"):
        return bool(_PY_SERVER_RE.search(source))
    return bool(_JS_SERVER_RE.search(source))


def _server_files(root: Path) -> List[str]:
    files = []
    for rel in _project_files(root, (".js", ".mjs", ".cjs", ".py")):
        if PurePosixPath(rel).name.lower().startswith("test") or ".test." in rel or ".spec." in rel:
            continue
        if _is_server_source(rel, _read(root / rel)):
            files.append(rel)
    return files


def server_entry(workspace: Any) -> Optional[Tuple[str, str]]:
    """
    (command, entry file) that starts the project's server, or None (no server code). The order: the "start" script of
    package.json (when it runs a file with server code), a README command `node|python <file>` for a server file, then a
    server file named server.js / app.js / index.js / main.js / ... (the shortest path first).
    """
    try:
        root = Path(workspace).resolve()
        servers = _server_files(root)
        if not servers:
            return None
        package = _package(root)
        script = str(((package or {}).get("scripts") or {}).get("start") or "").strip()
        if script:
            named = _FILE_COMMAND_RE.search(script)
            if named and named.group(2).lstrip("./") in servers:
                return "npm start", named.group(2).lstrip("./")
        for _readme, command in _readme_commands(root):
            parts = command.split()
            if parts[0] in ("node", "python", "python3") and parts[1].lstrip("./") in servers:
                return f"{'node' if parts[0] == 'node' else 'python'} {parts[1].lstrip('./')}", parts[1].lstrip("./")
        named = [rel for rel in servers if PurePosixPath(rel).name in _ENTRY_NAMES]
        candidates = sorted(named or servers, key=lambda rel: (rel.count("/"), _ENTRY_NAMES.index(PurePosixPath(rel).name)
                                                               if PurePosixPath(rel).name in _ENTRY_NAMES else 99, rel))
        entry = candidates[0]
        return f"{'python' if entry.endswith('.py') else 'node'} {shlex.quote(entry)}", entry
    except Exception as err:
        logger.debug(f"server_entry failed: {err}")
        return None


def server_port(workspace: Any, entry: str) -> Optional[int]:
    """The port the entry file listens on, read from its source (PORT = 3000, .listen(3000), port=8000), or None."""
    source = _read(Path(workspace) / entry)
    for pattern in _PORT_RES:
        match = pattern.search(source)
        if match:
            port = int(match.group(1))
            if 1 <= port <= 65535:
                return port
    return None


def uncalled_start_functions(source: str) -> List[Tuple[str, int]]:
    """
    (name, line) of the top-level JavaScript functions that start the server (.listen(), directly or by calling such a
    function) and that nothing in the file uses - no call, no export, no reference outside their own body.
    """
    try:
        from src.utils.code_map import _blank_strings_and_comments, _top_level_function_bodies
        bodies = _top_level_function_bodies(source)
        starters = {name for name, (_line, body) in bodies.items() if re.search(r"\.listen\s*\(", body)}
        for _round in range(3):
            more = {name for name, (_line, body) in bodies.items() if name not in starters
                    and any(re.search(r"(?<![\w.$])" + re.escape(s) + r"\s*\(", body) for s in starters)}
            if not more:
                break
            starters |= more
        clean = _blank_strings_and_comments(source)
        uncalled = []
        for name in starters:
            line, body = bodies[name]
            body_at = clean.find(body)
            span = (body_at, body_at + len(body)) if body_at >= 0 else (-1, -1)
            used = False
            for match in re.finditer(r"(?<![\w.$])" + re.escape(name) + r"(?![\w$])", clean):
                if span[0] <= match.start() < span[1]:
                    continue  # inside its own body
                before = clean[max(0, match.start() - 12):match.start()]
                if re.search(r"(?:\bfunction|\bconst|\blet|\bvar)\s+$", before):
                    continue  # its definition
                used = True
                break
            if not used:
                uncalled.append((name, line))
        return sorted(uncalled, key=lambda item: item[1])
    except Exception as err:
        logger.debug(f"uncalled_start_functions failed: {err}")
        return []


def html_pages(workspace: Any) -> List[str]:
    return _project_files(Path(workspace).resolve(), (".html", ".htm"))


def _dechunked(body: str) -> str:
    """The data of a chunked HTTP body (as far as it was read)."""
    data, rest = [], body
    while rest:
        size_line, sep, rest = rest.partition("\r\n")
        try:
            size = int(size_line.split(";")[0].strip() or "0", 16)
        except ValueError:
            break
        if not sep or size == 0:
            break
        data.append(rest[:size])
        rest = rest[size + 2:]
    return "".join(data)


async def _http_probe(port: int) -> Optional[Tuple[int, str, str]]:
    """(status, content type, first characters of the body) of GET http://127.0.0.1:port/, or None if nothing answered."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), HTTP_PROBE_TIMEOUT_SECONDS)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        writer.write(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        data = b""
        while len(data) < 4096:
            chunk = await asyncio.wait_for(reader.read(4096), HTTP_PROBE_TIMEOUT_SECONDS)
            if not chunk:
                break
            data += chunk
        text = data.decode("utf-8", errors="replace")
        head, _, body = text.partition("\r\n\r\n")
        lines = head.split("\r\n")
        if any(line.lower().replace(" ", "") == "transfer-encoding:chunked" for line in lines[1:]):
            body = _dechunked(body)
        status_match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d{3})", lines[0] if lines else "")
        if not status_match:
            return None
        content_type = next((line.split(":", 1)[1].strip() for line in lines[1:]
                             if line.lower().startswith("content-type:")), "")
        return int(status_match.group(1)), content_type, " ".join(body.split())[:80]
    except (OSError, asyncio.TimeoutError, ValueError):
        return None
    finally:
        try:
            writer.close()
        except Exception:
            pass


def _tail(text: str, lines: int = 8) -> str:
    kept = [line.rstrip() for line in str(text or "").strip().splitlines() if line.strip()][-lines:]
    return "\n".join(kept)


_ERROR_LINE_RE = re.compile(r"^\s*(?:[A-Z]\w*(?:Error|Exception)|Error)\b[^\n]*")
_LOCATION_LINE_RE = re.compile(r"^(?:\S*/)?[\w./-]+\.(?:m?js|cjs|py):\d+\s*$")


def error_excerpt(output: str, root: Any = None) -> str:
    """The error of a crashed program: Node's 'file:line / code / ^' block and the error line (or Python's last traceback
    lines), with workspace paths made relative; the output's last lines when no error line is found."""
    lines = [line.rstrip() for line in str(output or "").splitlines()]
    prefix = (str(Path(root).resolve()) + "/") if root else None
    shown: List[str] = []
    for index, line in enumerate(lines):
        if _ERROR_LINE_RE.match(line):
            start = index
            for back in range(index - 1, max(-1, index - 6), -1):
                if _LOCATION_LINE_RE.match(lines[back].strip()) or lines[back].lstrip().startswith("File \""):
                    start = back
                    break
            shown = [l for l in lines[start:index + 1] if l.strip()]
            following = next((l for l in lines[index + 1:index + 4] if l.strip().startswith("at ")), None)
            if following:
                shown.append(following)
            break
    text = "\n".join(shown) if shown else _tail(output)
    return text.replace(prefix, "") if prefix else text


async def program_run_problem(workspace: Any, agent_id: str = "framework") -> Optional[str]:
    """
    Why the project's server does not start (or does not send a page), or None (it runs, there is no server code, or the
    check cannot run). Runs its start command for RUN_CHECK_SECONDS. Never raises.
    """
    try:
        root = Path(workspace).resolve()
        found = server_entry(root)
        if not found:
            return None
        command, entry = found
        from src.tools.sandbox_runner import run_sandboxed
        port = server_port(root, entry)
        pages = html_pages(root)
        probe_task = None

        async def probe_later():
            await asyncio.sleep(HTTP_PROBE_DELAY_SECONDS)
            return await _http_probe(port)
        if port and pages:
            probe_task = asyncio.ensure_future(probe_later())
        result = await run_sandboxed(command, root, RUN_CHECK_SECONDS, tool_name="program_run_check", agent_id=agent_id)
        probe = None
        if probe_task is not None:
            try:
                probe = await asyncio.wait_for(probe_task, HTTP_PROBE_TIMEOUT_SECONDS * 3)
            except Exception:
                probe = None
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        if result.status in ("blocked", "error"):
            return None
        if _ADDRESS_IN_USE_RE.search(output):
            logger.info(f"program_run_check: `{command}` found its port in use (another copy runs?); not judged.")
            return None
        if result.status == "completed" and (result.returncode == 127 or _MISSING_TOOL_RE.search(output)):
            logger.info(f"program_run_check: `{command}` could not start its interpreter (not installed?); not judged.")
            return None
        if result.status == "completed":
            if result.returncode not in (0, None):
                return (f"the program does not start: `{command}` stopped with an error (exit code {result.returncode}):\n"
                        f"{error_excerpt(output, root)}\nRun it with command_executor, fix the error, and run it again until it keeps "
                        "running.")
            hint = ""
            if entry.lower().endswith((".js", ".mjs", ".cjs")):
                uncalled = uncalled_start_functions(_read(root / entry))
                if uncalled:
                    name, line = uncalled[0]
                    hint = (f" {entry} defines `{name}()` (line {line}), which starts the server, but nothing calls it. "
                            f"Call it at the end of the file: `if (require.main === module) {{ {name}(); }}` (the tests "
                            "that load the module do not start it then).")
            shown = _tail(output, 3)
            return (f"the program does not keep running: `{command}` ended at once (exit code 0"
                    + (f", output: {shown}" if shown else ", no output") + "), so the server never listens and nobody can "
                    f"open it.{hint}")
        # still running when the time was up: it started
        if probe is not None and pages:
            status, content_type, body = probe
            if status != 200 or "html" not in content_type.lower():
                return (f"the server runs, but http://localhost:{port}/ answers {status} "
                        f"({content_type or 'no content type'}: '{body}') instead of the page "
                        f"{', '.join(pages[:2])}: a player who opens the address sees no game. Make the server send the "
                        "page for GET / (and the .js / .css files the page loads, with their content types).")
        return None
    except Exception as err:
        logger.warning(f"program_run_check: the check failed ({err}); not blocking.")
        return None


def planned_files(workspace: Any) -> List[str]:
    """The files PROJECT_STRUCTURE.md lists under 'Files To Create' (planned at the kick-off)."""
    text = _read(Path(workspace) / "PROJECT_STRUCTURE.md")
    section = text.split("## Files To Create", 1)
    if len(section) < 2:
        return []
    files = []
    for line in section[1].splitlines():
        if line.startswith("## "):
            break
        match = re.match(r"^\s*-\s+`?([^`\s]+)`?\s*$", line)
        if match:
            files.append(match.group(1))
    return files


def readme_command_problems(workspace: Any, readme: Optional[str] = None) -> List[str]:
    """The commands a README tells the user to run that cannot work in this project (see the module docstring)."""
    problems: List[str] = []
    try:
        root = Path(workspace).resolve()
        commands = [(name, command) for name, command in _readme_commands(root) if readme is None or name == readme]
        if not commands:
            return problems
        package = _package(root)
        has_package = (root / "package.json").is_file()
        scripts = (package or {}).get("scripts") or {}
        entry = server_entry(root)
        planned = set(planned_files(root))
        npm = [(name, command) for name, command in commands if command.startswith("npm ")]
        if npm and not has_package:
            names = sorted({name for name, _ in npm})
            shown = ", ".join(f"`{command}`" for _, command in npm[:4])
            alternative = f" (`{entry[0]}`)" if entry else ""
            problems.append(
                f"{', '.join(names)} tells the user to run {shown}, but the project has NO package.json, so npm fails "
                "(\"ENOENT: no such file or directory, open 'package.json'\"). Write the commands that work in this project"
                f"{alternative} - e.g. `node --test tests/` for the tests - or add a package.json with those scripts.")
        elif npm and isinstance(scripts, dict):
            for name, command in npm:
                wanted = command.split(None, 2)[1:]
                script = wanted[1] if wanted and wanted[0] == "run" and len(wanted) > 1 else (
                    wanted[0] if wanted and wanted[0] in ("start", "test") else None)
                if script and script not in scripts and not (script == "start" and (root / "server.js").is_file()):
                    problems.append(f"{name} tells the user to run `{command}`, but package.json has no \"{script}\" "
                                    "script: add it, or write the command that works.")
        for name, command in commands:
            parts = command.split()
            if parts[0] == "npm":
                continue
            target = parts[1].lstrip("./")
            if (root / target).exists() or target in planned:
                continue
            same = [rel for rel in _project_files(root, (PurePosixPath(target).suffix,))
                    if PurePosixPath(rel).name == PurePosixPath(target).name]
            problems.append(f"{name} tells the user to run `{command}`, but there is no {target}"
                            + (f" (the project has {same[0]}: `{parts[0]} {same[0]}`)." if same else "."))
    except Exception as err:
        logger.debug(f"readme_command_problems failed: {err}")
    return problems

RUN_CHECK_TAG = "[Framework Run Check]"


async def audit_run_note(manager: Any) -> Optional[str]:
    """
    Round 4d: the note a PM gets when it enters pm_audit - what the framework found when it started the project's program
    the way a user would (server start, the page for GET /, the pages' script / style files, the README's commands), or
    None when the project has nothing of that to check. Never raises.
    """
    try:
        project = getattr(manager, "current_project", None)
        session = getattr(manager, "current_session", None)
        if not project or not session:
            return None
        from src.config.settings import settings
        from src.tools.sandbox_runner import resolve_shared_workspace
        workspace = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, str(project), str(session))
        root = Path(workspace).resolve()
        if not root.is_dir():
            return None
        entry = server_entry(root)
        problems: List[str] = []
        if entry:
            problem = await program_run_problem(root)
            if problem:
                problems.append(problem)
        from src.tools.project_links_check import html_reference_problems
        for page in html_pages(root)[:10]:
            problems.extend(html_reference_problems(root, page))
        problems.extend(readme_command_problems(root))
        if problems:
            return (f"{RUN_CHECK_TAG}: before this audit the framework started the project the way a user would. It does "
                    "NOT work yet:\n- " + "\n- ".join(problems[:6]) + "\nThe project is NOT finished, even if every task is "
                    "and the tests pass: create ONE task that names these problems and their files (project_management "
                    "add_task), assign it in pm_manage, and do not report the project as complete.")
        if entry:
            return (f"{RUN_CHECK_TAG}: before this audit the framework started the project: `{entry[0]}` keeps running"
                    + (" and sends the page for GET /" if html_pages(root) and server_port(root, entry[1]) else "")
                    + ". Go on with the audit.")
        return None
    except Exception as err:
        logger.warning(f"program_run_check: the audit check failed ({err}); no note.")
        return None

# END OF FILE src/utils/program_run_check.py
