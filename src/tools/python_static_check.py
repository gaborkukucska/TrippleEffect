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
