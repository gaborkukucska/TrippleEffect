# START OF FILE src/utils/product_check.py
"""
Round 5b: is what was built the thing that was asked for? Facts the framework reads from the finished workspace.

2026-11-10 (Local_Snake_Multiplayer): all five kick-off tasks were finished, the tests "passed", the program started, the
PM's audit answered its three questions (no open task, tests exist, tests pass) and reported "Project Audit Complete" -
20 minutes after the start. What was built: a page with a name form and a leaderboard. There was NO game:

* `src/snake.js` (the snake logic of Task 1) was loaded by nothing but its test. The page the server sends never used it,
  and no file drew a snake or read a key. The plan had no task for the part of the program a player sees.
* `tests/snake.test.js` defined two exported functions full of checks and never called them, and registered no test.
  `node --test` reports such a file as "tests 1, pass 1" (the file itself), so every check of a test run was green.

Nothing looked at the result as a whole. This module gives the audit three facts no model has to guess:

* unused_modules(): code files of the project that no file of the program loads - only tests, or nothing. Reported only
  when the project HAS a program to start (a server, a page, a package.json "main"/"start", a Python __main__); in a
  library every module is used by tests only, and that is fine.
* tests_that_test_nothing(): test files in which nothing runs - no test()/it()/describe() and no statement that calls
  code (JavaScript), no test function or TestCase (Python).
* objective(): the objective of PROJECT_PLAN.md, which the audit prompt never showed (the PM's history is cleared when it
  enters the audit), so that the PM can answer whether a user can DO what it promises.

Reads files only. Nothing here raises.
"""
import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

PRODUCT_CHECK_TAG = "[Framework Product Check]"
MAX_SOURCE_BYTES = 400_000
_SKIP_DIRS = frozenset({"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", "coverage",
                        ".te_background", ".pytest_cache", "research", "docs"})
_JS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
_PY = (".py",)
_REFERRERS = _JS + _PY + (".html", ".htm", ".json")
_NOT_MODULES = frozenset({"conftest.py", "setup.py", "__init__.py", "__main__.py", "manage.py", "wsgi.py", "asgi.py"})
_CONFIG_NAME_RE = re.compile(r"(?:^|[./-])(?:config|rc)\.[cm]?[jt]s$|\.config\.[cm]?[jt]s$|\.d\.ts$", re.IGNORECASE)
_PY_MAIN_RE = re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]\s*:""", re.MULTILINE)
_STATIC_SERVE_RE = re.compile(r"\breadFile(?:Sync)?\s*\(|\bcreateReadStream\s*\(|\bexpress\.static\s*\(|\bsendFile\s*\(|"
                              r"SimpleHTTPRequestHandler|StaticFiles\s*\(|send_from_directory\s*\(")


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _files(root: Path, suffixes: Tuple[str, ...]) -> List[str]:
    found: List[str] = []
    try:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if set(rel.parts[:-1]) & _SKIP_DIRS or any(part.startswith(".") for part in rel.parts[:-1]):
                continue
            if path.suffix.lower() in suffixes and path.is_file():
                found.append(rel.as_posix())
            if len(found) >= 400:
                break
    except (OSError, ValueError):
        pass
    return found


def _is_test(rel: str) -> bool:
    try:
        from src.tools.project_links_check import is_test_file
        return bool(is_test_file(rel))
    except Exception:
        name = PurePosixPath(rel).name
        return name.startswith("test_") or ".test." in name or ".spec." in name or "tests" in PurePosixPath(rel).parts


def objective(workspace: Any, limit: int = 600) -> Optional[str]:
    """The objective of the project's PROJECT_PLAN.md ('**Objective:** ...'), or None."""
    try:
        text = _read(Path(workspace) / "PROJECT_PLAN.md")
        match = re.search(r"^\W*objective\W*[:\-]\s*(.+?)(?=\n\s*\n|\n\W*(?:tech stack|tasks?|plan)\b|\Z)", text,
                          re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if not match:
            return None
        found = " ".join(match.group(1).replace("*", "").split())
        return found[:limit] + ("..." if len(found) > limit else "") if found else None
    except Exception:
        return None


# --------------------------------------------------------------------------------------------------------------------------
# code that nothing of the program uses
# --------------------------------------------------------------------------------------------------------------------------
def program_entries(root: Path) -> List[str]:
    """What a user starts or opens: the server file, HTML pages, package.json main / start script, Python __main__ files."""
    entries: List[str] = []
    try:
        from src.utils.program_run_check import html_pages, server_entry
        entry = server_entry(root)
        if entry:
            entries.append(entry[1])
        entries.extend(html_pages(root)[:20])
    except Exception:
        pass
    try:
        package = json.loads(_read(root / "package.json") or "{}")
        if isinstance(package, dict):
            main = package.get("main")
            if isinstance(main, str) and (root / main).is_file():
                entries.append(PurePosixPath(main).as_posix().lstrip("./"))
            for name in ("start", "dev", "serve"):
                script = (package.get("scripts") or {}).get(name) if isinstance(package.get("scripts"), dict) else None
                for found in re.findall(r"[\w./-]+\.(?:m?js|cjs|ts|py)\b", str(script or "")):
                    if (root / found).is_file():
                        entries.append(found.lstrip("./"))
    except (ValueError, TypeError):
        pass
    for rel in _files(root, _PY):
        if not _is_test(rel) and (PurePosixPath(rel).name in ("main.py", "app.py", "__main__.py", "manage.py")
                                  or _PY_MAIN_RE.search(_read(root / rel))):
            entries.append(rel)
    return list(dict.fromkeys(entries))


def _mentions(source: str, rel: str) -> bool:
    """True when a file's text names the module: its file name in a string, or an import of its module name."""
    path = PurePosixPath(rel)
    stem = path.stem
    if re.search(r"""['"`/]""" + re.escape(stem) + r"""(?:\.[cm]?[jt]sx?|\.py)?['"`?#]""", source):
        return True
    if path.suffix == ".py":
        return bool(re.search(rf"^\s*(?:from\s+[\w.]*\b{re.escape(stem)}\b[\w.]*\s+import|import\s+[\w., ]*\b{re.escape(stem)}\b|"
                              rf"from\s+[\w.]+\s+import\s+[\w, ()]*\b{re.escape(stem)}\b)", source, re.MULTILINE))
    return False


def unused_modules(workspace: Any, limit: int = 4) -> List[Tuple[str, List[str], str]]:
    """
    (file, the names it defines, who uses it) for every code file of a project WITH a program that no file of the
    program loads: 'only its tests' or 'no file at all'. Empty for a project without anything to start.
    """
    try:
        root = Path(workspace).resolve()
        entries = program_entries(root)
        if not entries:
            return []
        code = [rel for rel in _files(root, _JS + _PY)
                if not _is_test(rel) and PurePosixPath(rel).name not in _NOT_MODULES and not _CONFIG_NAME_RE.search(rel)]
        referrers = {rel: _read(root / rel) for rel in _files(root, _REFERRERS) if PurePosixPath(rel).name != "package-lock.json"}
        program = {rel: text for rel, text in referrers.items() if not _is_test(rel)}
        tests = {rel: text for rel, text in referrers.items() if _is_test(rel)}
        found: List[Tuple[str, List[str], str]] = []
        for rel in code:
            if rel in entries:
                continue
            if any(other != rel and _mentions(text, rel) for other, text in program.items()):
                continue
            # a server that sends the files of a folder uses every file in it
            folder = PurePosixPath(rel).parts[0] if len(PurePosixPath(rel).parts) > 1 else ""
            if folder and any(_STATIC_SERVE_RE.search(text) and re.search(r"""['"`/]""" + re.escape(folder) + r"""['"`/]""", text)
                              for other, text in program.items() if other != rel and other.endswith(_JS + _PY)):
                continue
            names: List[str] = []
            try:
                from src.utils.symbols import find_symbols
                names = [s.name for s in find_symbols(_read(root / rel), rel) or [] if s.parent is None][:6]
            except Exception:
                names = []
            users = [other for other, text in tests.items() if _mentions(text, rel)]
            found.append((rel, names, f"only by its tests ({', '.join(users[:2])})" if users else "by no file at all"))
            if len(found) >= limit:
                break
        return found
    except Exception as err:
        logger.debug(f"product_check: unused_modules failed: {err}")
        return []


# --------------------------------------------------------------------------------------------------------------------------
# test files in which nothing runs
# --------------------------------------------------------------------------------------------------------------------------
_JS_REGISTER_RE = re.compile(r"(?<![\w.$])(?:test|it|describe|suite)(?:\.\w+)?\s*\(")
_JS_DECLARATION_RE = re.compile(
    r"^(?:import\b|export\b|const\b|let\b|var\b|function\b|async\s+function\b|class\b|module\.exports\b|exports\.|"
    r"['\"]use strict['\"]|[})\];,]|$)")
_JS_VALUE_CALL_RE = re.compile(r"^(?:export\s+)?(?:const|let|var)\s+[\w${},\s:\[\]]+=\s*(?:await\s+)?(?!require\b|async\b|function\b)"
                               r"[\w$.]+\s*\((?![^)]*\)\s*=>)")
_PY_TEST_RE = re.compile(r"^\s*(?:async\s+)?def\s+test\w*\s*\(|^class\s+\w+\(.*TestCase.*\)\s*:", re.MULTILINE)


def js_test_runs_nothing(source: str) -> bool:
    """
    True for a JavaScript test file in which nothing runs when it is loaded: it registers no test (test / it / describe)
    and has no top-level statement that calls code - only imports, declarations and exports.
    """
    try:
        from src.utils.code_map import _blank_strings_and_comments
        clean = _blank_strings_and_comments(source)
    except Exception:
        return False
    if not clean.strip() or _JS_REGISTER_RE.search(clean):
        return False
    depth = 0
    for line in clean.split("\n"):
        text = line.strip()
        if depth == 0 and text:
            if _JS_VALUE_CALL_RE.match(text):
                return False  # `const result = run();`
            if not _JS_DECLARATION_RE.match(text):
                return False  # a statement: `movementTests();`, `assert.equal(...)`, `if (...)`
        depth = max(0, depth + text.count("{") + text.count("(") - text.count("}") - text.count(")"))
    return True


def tests_that_test_nothing(workspace: Any, files: Optional[List[str]] = None, limit: int = 4) -> List[Tuple[str, str]]:
    """(test file, why nothing in it is tested) for the workspace's test files - or the given ones."""
    found: List[Tuple[str, str]] = []
    try:
        root = Path(workspace).resolve()
        candidates = files if files is not None else [rel for rel in _files(root, _JS + _PY) if _is_test(rel)]
        for rel in candidates:
            name = PurePosixPath(rel).name
            source = _read(root / rel)
            if not source.strip() or name in _NOT_MODULES:
                continue
            if rel.endswith(_JS) and js_test_runs_nothing(source):
                defined: List[str] = []
                try:
                    from src.utils.symbols import find_symbols
                    defined = [s.name for s in find_symbols(source, rel) or [] if s.parent is None][:3]
                except Exception:
                    defined = []
                found.append((rel, (
                    "it registers NO test and calls nothing" + (f" (it only defines {', '.join(defined)})" if defined else "")
                    + ": a test runner reports such a file as passed without checking anything. Put every check inside "
                    "`test('what it checks', () => { ... });` with `const { test } = require('node:test');` (or `import "
                    "{ test } from 'node:test';`) and `assert` from 'node:assert'.")))
            elif rel.endswith(_PY) and (name.startswith("test_") or name.endswith("_test.py")) and not _PY_TEST_RE.search(source):
                found.append((rel, "it has no test function (`def test_...():`) and no TestCase class, so pytest collects "
                                   "nothing from it."))
            if len(found) >= limit:
                break
    except Exception as err:
        logger.debug(f"product_check: tests_that_test_nothing failed: {err}")
    return found


# --------------------------------------------------------------------------------------------------------------------------
# the note for the audit
# --------------------------------------------------------------------------------------------------------------------------
def product_problems(workspace: Any) -> List[str]:
    """The problems above as sentences for the PM (each one is work for a task)."""
    problems: List[str] = []
    try:
        root = Path(workspace).resolve()
        entries = program_entries(root)
        for rel, names, users in unused_modules(root):
            problems.append(
                f"`{rel}`" + (f" ({', '.join(names)})" if names else "") + f" is used {users}: nothing a user starts or opens"
                + (f" ({', '.join(f'`{e}`' for e in entries[:3])})" if entries else "") + " loads it, so its code does nothing "
                "for the user. Either the part of the program that must use it is MISSING (for a game or app: the page and "
                "the script that show it and take the input) or it is not connected - create a task that names the file to "
                "write or change and what it must do with this code.")
        for rel, why in tests_that_test_nothing(root):
            problems.append(f"`{rel}` tests nothing: {why}")
    except Exception as err:
        logger.debug(f"product_check: product_problems failed: {err}")
    return problems


MAX_REPORT_REFUSALS = 2
_MESSAGE_KEYS = ("message_content", "content", "message", "text")


def audit_report_refusal(agent: Any, manager: Any, args: dict, project_name: Optional[str],
                         session_name: Optional[str]) -> Optional[str]:
    """
    Why a PM's completion report from the audit is NOT sent to the Admin AI yet (the product problems above), or None.
    After MAX_REPORT_REFUSALS refusals the report goes out - with the open findings added to it, so the Admin AI and the
    user are told the truth either way (a wrong finding must never lock a project in its audit). Mutates `args` then.
    """
    try:
        if getattr(agent, "agent_type", None) != "pm" or getattr(agent, "state", None) != "pm_audit":
            return None
        target = str(args.get("target_agent_id") or args.get("target") or args.get("to") or args.get("recipient") or "")
        if target != "admin_ai" or not project_name or not session_name:
            return None
        from src.config.settings import settings
        from src.tools.sandbox_runner import resolve_shared_workspace
        workspace = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, str(project_name), str(session_name))
        problems = product_problems(workspace)
        if not problems:
            return None
        # counted per set of findings, for the PM's whole life: the same findings stop a report twice, not for ever
        counts = getattr(agent, "_product_report_refusals", None)
        counts = counts if isinstance(counts, dict) else {}
        signature = "|".join(sorted(problem[:60] for problem in problems))
        refused = int(counts.get(signature, 0))
        if refused >= MAX_REPORT_REFUSALS:
            key = next((k for k in _MESSAGE_KEYS if isinstance(args.get(k), str)), None)
            if key:
                args[key] = (args[key].rstrip() + "\n[Framework note for the Admin AI and the user: the framework's check of "
                             "the finished project found problems that are still OPEN:\n- " + "\n- ".join(problems) + "]")
            logger.warning(f"product_check: the audit report of '{getattr(agent, 'agent_id', '?')}' goes out with "
                           f"{len(problems)} open finding(s) after {refused} refusals.")
            return None
        counts[signature] = refused + 1
        agent._product_report_refusals = counts
        logger.warning(f"product_check: audit report of '{getattr(agent, 'agent_id', '?')}' NOT sent: {len(problems)} "
                       f"product problem(s): {problems[0][:160]}")
        return ("Your report was NOT sent: the project is not complete. The framework looked at the finished workspace and "
                "found:\n- " + "\n- ".join(problems) + "\nFor each finding create ONE task with project_management "
                "(action 'add_task': the file to write or change and what it must do), then request the state 'pm_manage' "
                "and assign the tasks. The audit runs again when they are finished.")
    except Exception as err:
        logger.debug(f"product_check: audit_report_refusal failed: {err}")
        return None


def product_note(workspace: Any) -> Optional[str]:
    """
    The note a PM gets when it enters the audit: the project's objective, the files of the program, and the question the
    three test questions never asked - can a user DO what the objective says? None for a workspace without a plan and files.
    """
    try:
        root = Path(workspace).resolve()
        goal = objective(root)
        files = [rel for rel in _files(root, _JS + _PY + (".html", ".htm", ".css")) if not _is_test(rel)]
        if not goal or not files:
            return None
        shown = []
        for rel in files[:14]:
            try:
                shown.append(f"{rel} ({(root / rel).stat().st_size:,} bytes)")
            except OSError:
                shown.append(rel)
        return (f"{PRODUCT_CHECK_TAG}: passing tests do not show that the product exists. The objective the user approved:\n"
                f"  \"{goal}\"\n"
                f"The program's files: {', '.join(shown)}" + (f", ... ({len(files) - 14} more)" if len(files) > 14 else "") + ".\n"
                "Before you report, go through the objective part by part: for EVERY thing it says a user can see or do, "
                "name the file that does it (look with file_system 'read' and its 'function' parameter - do not guess from "
                "file names). A part no file does is MISSING WORK, even when every task is finished: create a task for it "
                "(project_management add_task, naming the file to write and what it must do), request 'pm_manage' and "
                "assign it. Report the project as complete only when no part is missing.")
    except Exception as err:
        logger.debug(f"product_check: product_note failed: {err}")
        return None

# END OF FILE src/utils/product_check.py
