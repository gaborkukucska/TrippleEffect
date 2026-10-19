# START OF FILE src/tools/python_static_check.py
"""
Tiny, dependency-free static hints for Python files an agent has just written (Round 1o).

Observed 2026-10-15 (Temp_Convert_CLI): the 4B worker wrote src/core/temp_convert.py that called logging.error(...) without
ever importing logging, and whose main() consisted of a (rambling) docstring and no code. Both are syntactically valid, so the
write succeeded silently and the worker reported the task as done. These checks are deliberately narrow so that they (almost)
never produce a false alarm; they are hints appended to a successful write, never a reason to reject it.
"""
import ast
import builtins
import re
import sys
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Set, Tuple

MAX_NOTES = 6  # Round 2e: was 4; a file can have several undefined names AND an unimplemented function

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


# --- Round 1v --------------------------------------------------------------------------------------------------------------
# 2026-10-16 21:04: W2 saved tests/test_temp_convert.py with `def test_celsius_to_fahrenheit_basic()` twice (the second
# silently replaces the first) and neither test contained an assert, so pytest could never fail. The write said "Successfully".
_TEST_FILE_RE = re.compile(r"^(test_.+|.+_test)\.py$", re.IGNORECASE)


def _duplicate_definitions(tree: ast.AST) -> List[str]:
    """Functions/classes defined twice in the same module or class body (decorated definitions are skipped)."""
    notes: List[str] = []
    bodies = [tree] + [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    for owner in bodies:
        first_line = {}
        for node in getattr(owner, "body", []):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or node.decorator_list:
                continue
            if node.name in first_line:
                notes.append(f"'{node.name}' is defined twice (lines {first_line[node.name]} and {node.lineno}): the second "
                             "definition silently replaces the first. Give each one a unique name or delete one.")
            else:
                first_line[node.name] = node.lineno
    return notes


def _checks_nothing(func: ast.AST) -> bool:
    """True if a test function contains no assert and calls nothing, so it can never fail."""
    for node in ast.walk(func):
        if node is not func and isinstance(node, (ast.Assert, ast.Call, ast.Raise, ast.Await)):
            return False
    return True


# --- Round 2e: undefined names, functions that never return their declared value, import-only bodies ------------------------
# 2026-10-19 (Temp_Converter_CLI, qwen3.5:4b): PM1 marked task 1 finished although src/temp_convert.py called Decimal(...) without
# importing it, fahrenheit_to_celsius() used a variable 'f' that does not exist, celsius_to_fahrenheit() (declared '-> float')
# returned nothing, and main() held only a docstring and 'import sys'. W2's tests called celsius_to_fahrenheit() without
# importing it. Every one of these is a certain runtime failure that none of the checks above saw.
MODULE_DUNDER_NAMES = frozenset({
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__", "__builtins__", "__path__", "__cached__",
    "__annotations__", "__dict__", "__debug__", "__class__", "__qualname__", "__module__", "__all__", "__version__",
})
# Code that can create names the AST cannot see. A file containing any of these is not checked for undefined names.
_DYNAMIC_NAMES_RE = re.compile(r"\b(?:globals|locals|vars|exec)\s*\(|\bimport\s+\*|__builtins__|\bbuiltins\.")
_KNOWN_IMPORTS = {
    "Decimal": "from decimal import Decimal", "ROUND_HALF_UP": "from decimal import ROUND_HALF_UP",
    "Fraction": "from fractions import Fraction", "Path": "from pathlib import Path",
    "dataclass": "from dataclasses import dataclass", "field": "from dataclasses import field",
    "Enum": "from enum import Enum", "namedtuple": "from collections import namedtuple",
    "defaultdict": "from collections import defaultdict", "Counter": "from collections import Counter",
    "OrderedDict": "from collections import OrderedDict", "deque": "from collections import deque",
    "partial": "from functools import partial", "wraps": "from functools import wraps", "reduce": "from functools import reduce",
    "lru_cache": "from functools import lru_cache", "ArgumentParser": "from argparse import ArgumentParser",
    "List": "from typing import List", "Dict": "from typing import Dict", "Optional": "from typing import Optional",
    "Any": "from typing import Any", "Tuple": "from typing import Tuple", "Union": "from typing import Union",
    "Callable": "from typing import Callable", "Iterable": "from typing import Iterable", "pytest": "import pytest",
}


def _missing_name_hint(name: str, import_hints: Optional[Dict[str, str]] = None, bound: Optional[Set[str]] = None,
                       local_names: Optional[Set[str]] = None) -> str:
    if import_hints and name in import_hints:
        return f"missing 'from {import_hints[name]} import {name}'?"
    if name in _KNOWN_IMPORTS:
        return f"missing '{_KNOWN_IMPORTS[name]}'?"
    if name in STDLIB_MODULES:
        return f"missing 'import {name}'?"
    similar = _similar_bound_name(name, local_names or set()) or _similar_bound_name(name, bound or set())
    if similar:
        return f"did you mean '{similar}'?"
    return "missing import, or a typo in the name?"


def _enclosing_arguments(tree: ast.AST, line: int) -> Set[str]:
    """Argument names of the innermost function that contains the given line."""
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= (node.end_lineno or node.lineno):
            if best is None or node.lineno > best.lineno:
                best = node
    if best is None:
        return set()
    return {arg.arg for arg in ast.walk(best.args) if isinstance(arg, ast.arg)}


def _similar_bound_name(name: str, bound: Set[str]) -> Optional[str]:
    """A defined name the undefined one is probably a typo or an abbreviation of ('f' -> 'fahrenheit'), else None."""
    import difflib
    # a longer name that CONTAINS the undefined one is another thing ('test_celsius_to_fahrenheit_zero' for
    # 'celsius_to_fahrenheit'), not a typo
    candidates = sorted(b for b in bound if b != name and not b.startswith("_") and (len(name) <= 2 or name not in b))
    if len(name) <= 2:
        prefixed = [b for b in candidates if b.startswith(name)]
        return prefixed[0] if len(prefixed) == 1 else None
    close = difflib.get_close_matches(name, candidates, n=1, cutoff=0.8)
    return close[0] if close else None


def undefined_names(tree: ast.AST, source: str = "") -> List[Tuple[str, int]]:
    """
    (name, first line) of every name that is READ but bound nowhere in the file (no assignment, import, def, class, argument,
    loop/with/except target ...) and is not a builtin. A binding anywhere counts, so a name is only reported when it cannot
    exist when the file runs. Files that create names dynamically (globals(), exec, star imports, builtins) are skipped.
    """
    if _DYNAMIC_NAMES_RE.search(source or ""):
        return []
    bound = _bound_names(tree)
    for node in ast.walk(tree):
        for param in getattr(node, "type_params", None) or []:  # Python 3.12 'def f[T]()'
            if getattr(param, "name", None):
                bound.add(param.name)
    first_use: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = node.id
            if name in bound or name in MODULE_DUNDER_NAMES or name in ("self", "cls") or hasattr(builtins, name):
                continue
            if name not in first_use or node.lineno < first_use[name]:
                first_use[name] = node.lineno
    return sorted(first_use.items(), key=lambda item: (item[1], item[0]))


def _statements_without_docstring(func: ast.AST) -> list:
    body = list(getattr(func, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


def _only_imports(func: ast.AST) -> bool:
    """True if a function body holds nothing but (a docstring and) import statements."""
    body = _statements_without_docstring(func)
    return bool(body) and all(isinstance(node, (ast.Import, ast.ImportFrom)) for node in body)


_NO_VALUE_RETURNS = ("None", "Optional", "NoReturn", "Never", "Iterator", "Iterable", "Generator", "AsyncIterator", "AsyncIterable",
                     "AsyncGenerator", "Any", "object")


def _own_nodes(func: ast.AST):
    """Every node inside a function, without descending into nested functions, lambdas and classes."""
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    stack = [node for node in getattr(func, "body", []) if not isinstance(node, nested)]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, nested):
                stack.append(child)


def _declared_return_without_return(func: ast.AST) -> Optional[str]:
    """
    The declared return type (as text) of a function that is annotated to return a value ('-> float') but contains no
    'return <value>', no yield, does not end with 'raise' and has no endless 'while True' loop; else None.
    """
    returns = getattr(func, "returns", None)
    if returns is None:
        return None
    try:
        declared = ast.unparse(returns)
    except Exception:
        return None
    if any(word in declared for word in _NO_VALUE_RETURNS):
        return None
    body = _statements_without_docstring(func)
    if not body or _holds_no_code(body):
        return None  # reported as 'not implemented' by the other checks
    if isinstance(body[-1], ast.Raise):
        return None
    for node in _own_nodes(func):
        if isinstance(node, ast.Return) and node.value is not None:
            return None
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return None
        if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value:
            return None
    return declared


def workspace_import_hints(workspace, skip_file=None, max_files: int = 200) -> Dict[str, str]:
    """
    {name: module} for the top-level functions and classes of the Python files in a workspace, as a test would import them
    with the framework conftest.py (src/ and the root are on sys.path): src/temp_convert.py -> 'temp_convert'. Test files,
    conftest.py and names defined in more than one module are left out. Never raises.
    """
    from pathlib import Path
    hints: Dict[str, str] = {}
    ambiguous: Set[str] = set()
    try:
        root = Path(workspace).resolve()
        skip = Path(skip_file).resolve() if skip_file else None
        checked = 0
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root)
            parts = relative.parts
            if set(parts[:-1]) & {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", "tests", "test"}:
                continue
            if path.name == "conftest.py" or _TEST_FILE_RE.match(path.name) or (skip and path == skip):
                continue
            checked += 1
            if checked > max_files:
                break
            module_parts = list(parts[1:] if parts[0] == "src" and len(parts) > 1 else parts)
            module_parts[-1] = module_parts[-1][:-3]
            if module_parts[-1] == "__init__":
                module_parts = module_parts[:-1]
            if not module_parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
                continue
            module = ".".join(module_parts)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                    if node.name in hints and hints[node.name] != module:
                        ambiguous.add(node.name)
                    hints.setdefault(node.name, module)
    except Exception:
        return {}
    return {name: module for name, module in hints.items() if name not in ambiguous}


def python_code_notes(source: str, filename: Optional[str] = None, import_hints: Optional[Dict[str, str]] = None) -> List[str]:
    """
    Hints about a Python source text; [] if it does not parse (the syntax check reports that separately).
    import_hints (Round 2e): {name: module} of functions/classes defined in OTHER files of the workspace, used to suggest the
    missing import of an undefined name ('from temp_convert import celsius_to_fahrenheit').
    """
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

    # Round 2e: every other undefined name (Decimal(...), a variable 'f' that does not exist, a test calling a function it never
    # imported). 2026-10-19 04:03: src/temp_convert.py with both was marked finished because no check saw them.
    for name, line in undefined_names(tree, source):
        if name in reported:
            continue
        reported.add(name)
        notes.append(f"'{name}' is used on line {line} but is never imported or defined in this file, so it raises NameError "
                     f"({_missing_name_hint(name, import_hints, bound, _enclosing_arguments(tree, line))}).")

    for func in sorted(_functions_outside_classes(tree), key=lambda n: n.lineno):
        skip = {"abstractmethod", "overload"} & set(_decorator_names(func))
        if _is_docstring_only(func) and not skip:
            notes.append(f"function '{func.name}' (line {func.lineno}) contains only a docstring and no code: its body is not implemented yet.")
        elif _only_imports(func) and not skip:
            # Round 2e: main() of 2026-10-19 held a docstring and 'import sys' only
            notes.append(f"function '{func.name}' (line {func.lineno}) contains only a docstring and imports, no code: its body "
                         "is not implemented yet.")
        elif not skip:
            declared = _declared_return_without_return(func)
            if declared:
                notes.append(f"function '{func.name}' (line {func.lineno}) is declared to return '{declared}' but has no "
                             "'return <value>' statement, so it returns None.")

    notes.extend(_duplicate_definitions(tree))

    is_test_file = filename is None or bool(_TEST_FILE_RE.match(PurePosixPath(str(filename).replace("\\", "/")).name))
    if is_test_file:
        for func in sorted(_functions_outside_classes(tree), key=lambda n: n.lineno):
            if func.name.startswith("test") and not _is_docstring_only(func) and _checks_nothing(func):
                notes.append(f"test '{func.name}' (line {func.lineno}) has no assert and calls nothing, so it can never fail. "
                             "Call the function under test and assert the expected result.")

    return notes[:MAX_NOTES]


def python_code_notes_for(source: str, filename: Optional[str], workspace=None) -> List[str]:
    """
    Round 2e: python_code_notes() of a file in a workspace. Only when a name is undefined are the workspace's other modules
    scanned, to suggest the import ('from temp_convert import celsius_to_fahrenheit'). Never raises.
    """
    try:
        notes = python_code_notes(source, filename)
        if workspace is not None and filename and any("NameError" in note for note in notes):
            from pathlib import Path
            hints = workspace_import_hints(workspace, skip_file=Path(workspace) / str(filename))
            if hints:
                notes = python_code_notes(source, filename, hints)
        return notes
    except Exception:
        return []


def saved_edit_notes(source: str, filename: Optional[str], workspace=None) -> str:
    """Round 2e: the '[Framework check ...]' text appended to a successful edit of a Python file ('' if nothing to say)."""
    if not str(filename or "").endswith(".py"):
        return ""
    notes = python_code_notes_for(source, filename, workspace)
    if not notes:
        return ""
    return "\n[Framework check - the edit WAS saved, but please fix:]\n- " + "\n- ".join(notes)


# --- Round 2a: Python files that hold no code yet ----------------------------------------------------------------------------
# 2026-10-18 10:19-11:30: W1's complete rewrites of src/main.py were refused 8 times ("already exists") although the file held
# only docstrings. Overwriting such a file loses nothing.
def _is_main_guard(node: ast.AST) -> bool:
    test = getattr(node, "test", None)
    return (isinstance(node, ast.If) and isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
            and test.left.id == "__name__")


def _holds_no_code(statements) -> bool:
    for node in statements:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstring, '...'
        if isinstance(node, (ast.Pass, ast.Import, ast.ImportFrom)) or _is_main_guard(node):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and _holds_no_code(node.body):
            continue
        return False
    return True


def is_unimplemented_python(source: str) -> bool:
    """
    True if source parses, defines at least one function or class, and nothing in it does anything: only docstrings,
    imports, pass/..., empty functions/classes and an `if __name__ == "__main__":` block. Imports-only files are False.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    has_definition = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in tree.body)
    return has_definition and _holds_no_code(tree.body)


# --- Round 2d: complete rewrites of an existing Python file ------------------------------------------------------------------
# 2026-10-18 22:41-23:40: W1 sent the complete, corrected src/temp_convert.py six times with file_system 'write'; every time it
# was refused ("already exists") although the new version kept both conversion functions and only replaced the broken main().
# It took 60 minutes until the model added force_overwrite. A rewrite that keeps every function and class loses nothing.
MAX_REWRITE_PROBLEMS = 6


def parses_as_python(source: str) -> bool:
    """True if source is syntactically valid Python."""
    try:
        ast.parse(source)
        return True
    except (SyntaxError, ValueError):
        return False


def _definitions(body) -> dict:
    return {node.name: node for node in body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _top_level_names(body) -> dict:
    """Names bound by top-level assignments and imports -> line number (definitions are handled separately)."""
    names: dict = {}
    for node in body:
        targets: list = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    names.setdefault((alias.asname or alias.name).split(".")[0], node.lineno)
            continue
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    names.setdefault(sub.id, node.lineno)
    return names


def python_rewrite_problems(old_source: str, new_source: str) -> List[str]:
    """
    Why a complete rewrite of a Python file cannot be saved without force_overwrite: the old file defines no function or
    class (nothing to compare with), or the new version would lose a top-level function, class, method of a kept class,
    assigned or imported name, or the code of a definition. [] means nothing is lost. Both texts must parse.
    """
    try:
        old_tree, new_tree = ast.parse(old_source), ast.parse(new_source)
    except (SyntaxError, ValueError):
        return ["the old or the new version is not valid Python, so the framework cannot check what would be lost"]
    problems: List[str] = []
    old_defs, new_defs = _definitions(old_tree.body), _definitions(new_tree.body)
    if not old_defs:
        return ["the existing file defines no function or class, so the framework cannot check what a new version would lose"]
    new_names = _top_level_names(new_tree.body)
    for name, line in _top_level_names(old_tree.body).items():
        if name not in new_names and name not in new_defs:
            problems.append(f"top-level name '{name}' (line {line}) would be removed")
    for name, old_node in old_defs.items():
        new_node = new_defs.get(name)
        kind = "class" if isinstance(old_node, ast.ClassDef) else "function"
        if new_node is None:
            problems.append(f"{kind} '{name}' (line {old_node.lineno}) would be removed")
            continue
        if not _holds_no_code(old_node.body) and _holds_no_code(new_node.body):
            problems.append(f"{kind} '{name}' has code now but would hold no code in the new version")
            continue
        if isinstance(old_node, ast.ClassDef) and isinstance(new_node, ast.ClassDef):
            new_methods = _definitions(new_node.body)
            for method in _definitions(old_node.body):
                if method not in new_methods:
                    problems.append(f"method '{name}.{method}' would be removed")
    return problems[:MAX_REWRITE_PROBLEMS]
# --- Syntax error report (Round 1t) -----------------------------------------------------------------------------------------
# 2026-10-16 10:45-12:00: W1 sent a whole file with the same fault ("expected 'except' or 'finally' block") 7 times. Every
# rejection only said "... at line N", so the 4B model never found the fault. The report below shows the code around the
# error and a concrete hint for the common mistakes.
def _show_lines(lines: List[str], center: int, before: int = 3, after: int = 1) -> str:
    first, last = max(1, center - before), min(len(lines), center + after)
    out = []
    for number in range(first, last + 1):
        marker = ">>" if number == center else "  "
        out.append(f"{marker} {number:>4} | {lines[number - 1].rstrip()[:110]}")
    return "\n".join(out)


def _find_open_try(lines: List[str], error_line: int) -> int:
    """1-based line of the closest 'try:' above error_line that has no except/finally at its own indentation, or 0."""
    for index in range(min(error_line, len(lines)) - 1, -1, -1):
        stripped = lines[index].strip()
        if not stripped.startswith("try:"):
            continue
        indent = len(lines[index]) - len(lines[index].lstrip())
        has_handler = False
        for follower in lines[index + 1:error_line]:
            body = follower.strip()
            if body and (len(follower) - len(follower.lstrip())) == indent and body.startswith(("except", "finally")):
                has_handler = True
                break
        if not has_handler:
            return index + 1
    return 0


def syntax_error_report(source: str, err: SyntaxError) -> str:
    """
    Human/LLM-friendly description of a SyntaxError: the message, the code around the faulty line and a hint.
    Never raises; returns '' if nothing useful can be said.
    """
    try:
        lines = str(source).splitlines()
        lineno = err.lineno if isinstance(err.lineno, int) and 1 <= err.lineno <= max(1, len(lines)) else 0
        message = str(err.msg or "")
        parts: List[str] = []
        if lineno and lines:
            parts.append("Code around the error:\n" + _show_lines(lines, lineno))
        low = message.lower()
        if "expected 'except' or 'finally'" in low and lines:
            try_line = _find_open_try(lines, lineno or len(lines))
            where = f"The `try:` on line {try_line}" if try_line else "A `try:` above"
            parts.append(f"Hint: {where} has no `except` or `finally` at the same indentation as the `try`. Add "
                         "`except Exception as exc:` (same indentation as `try`) with a body, or remove the `try`. "
                         "Do not leave code after the try body unindented.")
        elif "f-string" in low and ("single '}'" in low or "single '{'" in low):
            parts.append("Hint: inside an f-string write `{{` and `}}` for literal braces, and keep each `{expression}` short.")
        elif "unterminated triple-quoted" in low:
            parts.append('Hint: a triple-quoted string is never closed; end it with the same three quotes.')
        elif "unterminated string" in low:
            parts.append("Hint: a string is missing its closing quote on that line.")
        elif "indent" in low:
            parts.append("Hint: use 4 spaces per indentation level everywhere and no tabs; a block needs at least one indented line.")
        elif "was never closed" in low:
            parts.append("Hint: that bracket or parenthesis is never closed.")
        elif "invalid character" in low or "invalid decimal literal" in low:
            # Round 2e: 2026-10-19 05:21 "invalid character '°'" - docstring text had ended up outside its string
            parts.append('Hint: that line is plain text (from a docstring, comment or message) that ended up OUTSIDE a string. '
                         'Usually a """ was closed too early, or the edit cut a docstring in half. Replace whole functions '
                         '(from their def line to their last line) and make sure every """ has its closing """.')
        if parts:
            parts.append("Send the COMPLETE corrected file. Keep the code simple (no deeply nested try/except) and do not resend the same text.")
        return "\n".join(parts)
    except Exception:
        return ""
# END OF FILE src/tools/python_static_check.py
