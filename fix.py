#!/usr/bin/env python3
"""
TrippleEffect: Round 1o Surgical Fix
  (src/agents/cycle_handler.py, src/agents/tool_activity.py, src/tools/file_system.py, NEW src/tools/python_static_check.py)

OBSERVED (2026-10-15, Temp_Convert_CLI run 18:42-20:24, after Round 1n):
 A. Admin's message "Great news: Kick-off complete! ... Proceed with Phase 1 from PROJECT_PLAN.md" was blocked by the
    final_response HARD GATE (it matched "complete" + the word "project", the latter only because of "PROJECT_PLAN.md").
    The gate told Admin to verify task status; Admin switched to a self-invented "work" task and spent 8 LLM cycles
    (~17 minutes of the 2 shared LLM slots) listing tasks. Admin + PM used 44% of all 87 LLM calls in this run.
 B. W1 wrote src/core/temp_convert.py that calls logging.error() without importing logging, and whose main() is only a
    (rambling) docstring. Both are valid Python, so the write succeeded silently and W1 reported the task as done.

FIXES
 1. tool_activity.claims_project_complete(): the strict completion-claim pattern the Admin raw-response gate already used.
    The final_response gate now uses it, so status updates ("Kick-off complete", "Task 2 is done") are delivered, and a
    real claim ("The project is complete", "all tasks are done") with tasks still pending is still blocked.
 2. NEW src/tools/python_static_check.py (stdlib only) + file_system 'write': after a successful write of a .py file the
    result carries a NON-BLOCKING hint when (a) a standard-library module is used (e.g. logging.x) but never imported or
    defined anywhere in the file, or (b) a function outside a class contains only a docstring. The file is saved exactly as
    before; syntax errors are still rejected as before; a failing checker can never break a write.
 3. tests/unit/test_round1o.py (new, 38 tests).
 Unchanged: everything else, including the raw-response gate and all Round 1l-1n behaviour.

Usage:
    python3 fix.py --dry-run
    python3 fix.py

All-or-nothing: if ANY find-string is missing (or matches the wrong number of times) nothing is written.
"""
import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path.cwd()

PATCHES = [
    {
        "file": 'src/agents/tool_activity.py',
        "find": r'''# END OF FILE src/agents/tool_activity.py
''',
        "replace": r'''
# Strong assertions that the WHOLE project is finished. Same pattern the Admin raw-response gate has always used.
# The final_response gate used to block any text containing "complete"/"finished"/"done" AND the word "project": Admin's
# "Kick-off complete! ... Proceed with Phase 1 from PROJECT_PLAN.md" (2026-10-15 18:54:35) matched, was blocked, and sent
# Admin into 8 pointless "work" cycles (about 17 minutes of the 2 shared LLM slots).
import re as _re

_PROJECT_COMPLETE_CLAIM_RE = _re.compile(
    r"\b(project\s+is\s+(complete|finished|done)|all\s+tasks\s+(are\s+)?(complete|finished|done)"
    r"|project\s+completed|finished\s+(the\s+)?project)\b"
)


def claims_project_complete(text: Optional[str]) -> bool:
    """True if the text strongly asserts that the whole project is complete."""
    return bool(text) and _PROJECT_COMPLETE_CLAIM_RE.search(str(text).lower()) is not None


# END OF FILE src/agents/tool_activity.py
''',
    },
    {
        "file": 'src/agents/cycle_handler.py',
        "find": r'''from src.agents.tool_activity import is_tool_result_failure  # type: ignore[import]
''',
        "replace": r'''from src.agents.tool_activity import is_tool_result_failure, claims_project_complete  # type: ignore[import]
''',
    },
    {
        "file": 'src/agents/cycle_handler.py',
        "find": r'''                                if ("complete" in lower_fc or "finished" in lower_fc or "done" in lower_fc) and "project" in lower_fc:
''',
        "replace": r'''                                # Round 1o: only a strong claim that the WHOLE project is complete is gated (see tool_activity).
                                if claims_project_complete(lower_fc):
''',
    },
    {
        "file": 'src/tools/file_system.py',
        "find": r'''            success_msg = f"Successfully wrote content to '{filename}' in {scope_description}."
            if replace_empty_dir:
                success_msg += " (Framework note: an empty directory with this name was in the way and has been replaced by your file.)"
''',
        "replace": r'''            success_msg = f"Successfully wrote content to '{filename}' in {scope_description}."
            if replace_empty_dir:
                success_msg += " (Framework note: an empty directory with this name was in the way and has been replaced by your file.)"
            if filename.endswith('.py'):
                # Round 1o: non-blocking hints about defects the syntax check cannot see (missing imports, docstring-only functions).
                try:
                    from src.tools.python_static_check import python_code_notes
                    code_notes = python_code_notes(content)
                except Exception as note_err:
                    logger.debug(f"Agent {agent_id}: python_code_notes failed for '{filename}': {note_err}")
                    code_notes = []
                if code_notes:
                    success_msg += (
                        "\n[Framework check - the file WAS saved, but please fix:]\n- " + "\n- ".join(code_notes)
                        + "\nUse 'find_replace' or 'insert_lines' for small fixes; do not resend the whole file."
                    )
''',
    },
]

NEW_FILES = {
    "src/tools/python_static_check.py": r'''# START OF FILE src/tools/python_static_check.py
"""
Tiny, dependency-free static hints for Python files an agent has just written (Round 1o).

Observed 2026-10-15 (Temp_Convert_CLI): the 4B worker wrote src/core/temp_convert.py that called logging.error(...) without
ever importing logging, and whose main() consisted of a (rambling) docstring and no code. Both are syntactically valid, so the
write succeeded silently and the worker reported the task as done. These checks are deliberately narrow so that they (almost)
never produce a false alarm; they are hints appended to a successful write, never a reason to reject it.
"""
import ast
import builtins
import sys
from typing import List, Set

MAX_NOTES = 4

_FALLBACK_STDLIB = frozenset({
    "argparse", "collections", "datetime", "functools", "itertools", "json", "logging", "math", "os", "pathlib", "random",
    "re", "shutil", "string", "subprocess", "sys", "tempfile", "time", "typing", "unittest",
})
STDLIB_MODULES = frozenset(getattr(sys, "stdlib_module_names", _FALLBACK_STDLIB))


def _bound_names(tree: ast.AST) -> Set[str]:
    """Every name that is bound anywhere in the file (a binding anywhere counts as 'defined': precision over recall)."""
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and getattr(node, "name", None):
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def _decorator_names(node: ast.AST) -> List[str]:
    out: List[str] = []
    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute):
            out.append(target.attr)
        elif isinstance(target, ast.Name):
            out.append(target.id)
    return out


def _is_docstring_only(func: ast.AST) -> bool:
    body = getattr(func, "body", [])
    return (
        len(body) == 1
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )


def _functions_outside_classes(tree: ast.AST):
    """Yield function definitions that are not (directly or indirectly) inside a class body."""
    stack = [(tree, False)]
    while stack:
        node, in_class = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                stack.append((child, True))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not in_class:
                    yield child
                stack.append((child, in_class))
            else:
                stack.append((child, in_class))


def python_code_notes(source: str) -> List[str]:
    """Hints about a Python source text; [] if it does not parse (the syntax check reports that separately)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []

    notes: List[str] = []
    bound = _bound_names(tree)

    reported: Set[str] = set()
    for node in sorted((n for n in ast.walk(tree) if isinstance(n, ast.Attribute)), key=lambda n: (n.lineno, n.col_offset)):
        root = node.value
        if not (isinstance(root, ast.Name) and isinstance(root.ctx, ast.Load)):
            continue
        name = root.id
        if name in reported or name in bound or hasattr(builtins, name) or name not in STDLIB_MODULES:
            continue
        reported.add(name)
        notes.append(f"'{name}' is used on line {root.lineno} but is never imported or defined in this file (missing 'import {name}'?).")

    for func in sorted(_functions_outside_classes(tree), key=lambda n: n.lineno):
        if _is_docstring_only(func) and not {"abstractmethod", "overload"} & set(_decorator_names(func)):
            notes.append(f"function '{func.name}' (line {func.lineno}) contains only a docstring and no code: its body is not implemented yet.")

    return notes[:MAX_NOTES]
# END OF FILE src/tools/python_static_check.py
''',
    "tests/unit/test_round1o.py": r'''"""Round 1o tests (Temp_Convert_CLI run 2026-10-15 18:42-20:24).

1. Admin's final_response gate blocked "Kick-off complete! ... PROJECT_PLAN.md" as a false completion claim and sent Admin
   into 8 wasted LLM cycles.
2. W1 wrote src/core/temp_convert.py that used `logging` without importing it and whose main() was only a docstring; the
   write succeeded silently and W1 reported the task as done.
"""
import asyncio
from pathlib import Path

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order
from src.agents.tool_activity import claims_project_complete
from src.tools.python_static_check import python_code_notes

ROOT = Path(__file__).resolve().parents[2]

# ------------------------------------------------------------------------------------------------ completion claims

ADMIN_MESSAGE_18_54 = (
    "Great news: Kick-off complete! Your team has already started their work - W2 and W1 are now handling Tasks 1 & 2 "
    "respectively!\n\nProceed autonomously with Phase 1 from PROJECT_PLAN.md. Create the implementation and test tasks, "
    "assign them to your workers (Coder for code/tests + Technical Writer for README).\n\nI'll keep you updated as things progress"
)


def legacy_gate(text: str) -> bool:
    lower = text.lower()
    return ("complete" in lower or "finished" in lower or "done" in lower) and "project" in lower


def test_the_observed_admin_message_is_not_a_completion_claim():
    assert legacy_gate(ADMIN_MESSAGE_18_54) is True  # documents the bug
    assert claims_project_complete(ADMIN_MESSAGE_18_54) is False


@pytest.mark.parametrize("text", [
    "The project is complete.", "Project is finished and all deliverables are ready.", "All tasks are done.",
    "all tasks complete", "Project completed successfully!", "I have finished the project.", "PROJECT IS DONE",
])
def test_real_completion_claims_are_still_detected(text):
    assert claims_project_complete(text) is True


@pytest.mark.parametrize("text", [
    "Kick-off complete.", "Planning is done, the project plan is ready.", "Task 2 is complete; project continues.",
    "", None, "The project will be finished when the tests pass.",
])
def test_status_updates_are_not_completion_claims(text):
    assert claims_project_complete(text) is False


def test_cycle_handler_final_response_gate_uses_the_strict_detector():
    src = (ROOT / "src/agents/cycle_handler.py").read_text(encoding="utf-8")
    assert "claims_project_complete" in src
    assert 'if ("complete" in lower_fc or "finished" in lower_fc or "done" in lower_fc) and "project" in lower_fc:' not in src
    assert "if claims_project_complete(lower_fc):" in src

# ------------------------------------------------------------------------------------------------ static notes

W1_FILE = '"""Temperature conversion module."""\n\nimport sys\nfrom typing import Union\n\n\ndef celsius_to_fahrenheit(c: float) -> float:\n    """Convert."""\n    if not isinstance(c, (int, float)):\n        raise TypeError("bad")\n    try:\n        return round((c * 9 / 5) + 32, 2)\n    except Exception as e:\n        logging.error("Error converting Celsius to Fahrenheit", exc_info=True)\n        raise ValueError("Failed") from e\n\n\ndef main():\n    """Main entry point that parses command-line arguments and performs temperature conversion.\n\n    Example Usage from Command Line:\n        python temp_convert.py 100 C\n    """\n'


def test_the_real_w1_defects_are_reported():
    notes = python_code_notes(W1_FILE)
    assert len(notes) == 2
    assert "'logging'" in notes[0] and "line 14" in notes[0] and "import logging" in notes[0]
    assert "function 'main'" in notes[1] and "only a docstring" in notes[1]


def test_clean_file_has_no_notes():
    src = 'import argparse\nimport logging\nimport sys\nfrom pathlib import Path\n\nlog = logging.getLogger(__name__)\n\n\ndef main(argv=None):\n    """Entry."""\n    parser = argparse.ArgumentParser()\n    parser.add_argument("value")\n    args = parser.parse_args(argv)\n    log.info("%s", args.value)\n    return Path(sys.argv[0]).name and 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
    assert python_code_notes(src) == []


@pytest.mark.parametrize("src", [
    "import os.path\nprint(os.path.join('a'))\n",                  # dotted import binds 'os'
    "import logging as lg\nlg.info(1)\n",                          # alias
    "from os import path\npath.join('a')\n",                       # from-import
    "def f(logging):\n    logging.info(1)\n",                       # parameter
    "class A:\n    def m(self):\n        json = 1\n        return json.real\n",   # local assignment
    "try:\n    pass\nexcept Exception as sys:\n    sys.args\n",     # except-as
    "for time in [1]:\n    time.real\n",                           # loop target
    "with open('x') as string:\n    string.read()\n",              # with-as
    "def f():\n    global re\n    re = 1\n",                       # global
    "x = 'abc'.upper()\nprint(len(x).bit_length())\n",              # builtins / non-modules
    "import typing\nx: typing.List[int] = []\n",
])
def test_no_false_alarms_when_the_name_is_bound(src):
    assert python_code_notes(src) == []


def test_only_standard_library_module_names_are_flagged():
    assert python_code_notes("requests.get('x')\n") == []          # third-party: could be a legit import elsewhere
    assert python_code_notes("self.x = 1\n") == []
    assert len(python_code_notes("math.sqrt(4)\n")) == 1


def test_each_missing_module_is_reported_once():
    notes = python_code_notes("os.getcwd()\nos.listdir('.')\nsys.exit(1)\n")
    assert len(notes) == 2 and "'os'" in notes[0] and "'sys'" in notes[1]


def test_stub_detection_is_narrow():
    assert python_code_notes('def a():\n    """doc"""\n') != []
    assert python_code_notes('async def a():\n    """doc"""\n') != []
    assert python_code_notes('def a():\n    """doc"""\n    pass\n') == []
    assert python_code_notes('def a():\n    """doc"""\n    return 1\n') == []
    assert python_code_notes('def a():\n    pass\n') == []
    assert python_code_notes('class P:\n    def m(self):\n        """interface"""\n') == []
    assert python_code_notes('import abc\n@abc.abstractmethod\ndef a():\n    """doc"""\n') == []
    assert python_code_notes('from typing import overload\n@overload\ndef a():\n    """doc"""\n') == []


def test_unparseable_and_odd_input_gives_no_notes():
    assert python_code_notes("def f(:\n") == []
    assert python_code_notes("") == []
    assert python_code_notes("x = 1\0") == []


def test_notes_are_capped():
    src = "\n".join(f"def f{i}():\n    \"\"\"d\"\"\"\n" for i in range(10))
    assert len(python_code_notes(src)) == 4

# ------------------------------------------------------------------------------------------------ file_system integration


def fs_execute(tmp_path, **kwargs):
    from src.tools.file_system import FileSystemTool
    return asyncio.run(FileSystemTool().execute("W1", tmp_path, **kwargs))


def test_write_succeeds_and_appends_the_hints(tmp_path):
    r = fs_execute(tmp_path, action="write", path="src/core/temp_convert.py", content=W1_FILE)
    assert r["status"] == "success", r
    assert (tmp_path / "src/core/temp_convert.py").read_text() == W1_FILE          # saved unchanged
    assert "Successfully wrote content to 'src/core/temp_convert.py'" in r["message"]
    assert "[Framework check - the file WAS saved, but please fix:]" in r["message"]
    assert "'logging'" in r["message"] and "function 'main'" in r["message"]
    assert "do not resend the whole file" in r["message"]


def test_clean_write_message_is_unchanged(tmp_path):
    r = fs_execute(tmp_path, action="write", path="ok.py", content="import sys\n\n\ndef main():\n    return sys.argv\n")
    assert r["status"] == "success"
    assert "Framework check" not in r["message"]


def test_non_python_files_get_no_hints(tmp_path):
    r = fs_execute(tmp_path, action="write", path="notes.md", content="os.getcwd()\n")
    assert r["status"] == "success" and "Framework check" not in r["message"]


def test_syntax_errors_are_still_rejected_without_saving(tmp_path):
    r = fs_execute(tmp_path, action="write", path="bad.py", content="def f(:\n    pass\n")
    assert r["status"] == "error" and "SyntaxError" in r["message"]
    assert not (tmp_path / "bad.py").exists()


def test_a_failing_checker_never_breaks_the_write(tmp_path, monkeypatch):
    import src.tools.python_static_check as psc

    def boom(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(psc, "python_code_notes", boom)
    r = fs_execute(tmp_path, action="write", path="x.py", content="os.getcwd()\n")
    assert r["status"] == "success" and "Framework check" not in r["message"]
    assert (tmp_path / "x.py").exists()
''',
}

def apply_patches(dry_run: bool) -> int:
    originals = {}
    updated = {}
    errors = []

    for idx, p in enumerate(PATCHES, 1):
        rel = p["file"]
        path = ROOT / rel
        expected = p.get("count", 1)
        if rel not in updated:
            if not path.is_file():
                errors.append(f"[patch {idx}] File not found: {rel}")
                continue
            originals[rel] = path.read_text(encoding="utf-8")
            updated[rel] = originals[rel]
        count = updated[rel].count(p["find"])
        if count == 0:
            errors.append(f"[patch {idx}] NO MATCH in {rel} for find-string starting: {p['find'][:80]!r}")
        elif count != expected:
            errors.append(f"[patch {idx}] {count} MATCHES (expected exactly {expected}) in {rel}: {p['find'][:80]!r}")
        else:
            updated[rel] = updated[rel].replace(p["find"], p["replace"])

    for rel in NEW_FILES:
        if (ROOT / rel).exists():
            errors.append(f"New file already exists, refusing to overwrite: {rel}")

    if errors:
        print("ERROR: patch aborted, NOTHING was written:\n", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1

    for rel, new_text in updated.items():
        if new_text == originals[rel]:
            continue
        if dry_run:
            diff = difflib.unified_diff(
                originals[rel].splitlines(keepends=True), new_text.splitlines(keepends=True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}", n=2,
            )
            sys.stdout.writelines(diff)
            print()
        else:
            (ROOT / rel).write_text(new_text, encoding="utf-8")
            print(f"PATCHED  {rel}")

    for rel, content in NEW_FILES.items():
        if dry_run:
            print(f"[dry-run] would create {rel} ({len(content.splitlines())} lines)")
        else:
            target = ROOT / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            print(f"CREATED  {rel}")

    print("\nDry run complete: no files changed." if dry_run else "\nAll patches applied successfully.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply TrippleEffect Round 1o fixes.")
    ap.add_argument("--dry-run", action="store_true", help="Show diffs only; do not write any files.")
    args = ap.parse_args()
    if not (ROOT / "src").is_dir():
        print("ERROR: run this from the TrippleEffect repository root.", file=sys.stderr)
        return 1
    return apply_patches(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
