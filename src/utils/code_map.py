# START OF FILE src/utils/code_map.py
"""
Round 3y: the CODE MAP - the interface of a worker's task files, read from disk by the framework at every step.

2026-10-27 11:26-14:08 (WiFi_Snake_Game, qwen3.5:4b-q4_K_M, 32k window): the tests of every module disagreed with the module.
The model was not the only cause - the framework never let a worker keep what it knew about the code:
* every task starts with a wiped history: activation keeps the system prompt and the last 4 messages, and the step from
  worker_decompose to worker_work keeps only the system prompt. W1 wrote src/game.js in worker_decompose at 11:29:53 and
  its first call in worker_work was to read src/game.js again; W1 wrote tests/test_player_manager.js at 11:48:55 and read
  src/player_manager.js again 38 s later;
* inside a task the history is summarized every ~10 cycles (W3 4x, W4 6x on one task) and compacted: the text of the
  agent's own writes is replaced by a placeholder and reads of changed files are removed;
* 3 of the 5 test tasks went to a worker who never wrote the code (Task 6: W3 tested W1's src/game.js, Task 7: W1 tested
  W3's src/player_manager.js, Task 8: W5 tested W1's src/leaderboard.js); for the other two (Task 9: W2's src/server.py,
  Task 10: W4's src/api.py) the author's history had been cleared twice before it wrote the first test.
The tests then used what the module keeps INSIDE (tests/test_game.js set and read src/game.js's `snake`, `food`, `score`:
"Initial snake length should be 3" failed for 2h20m), names the module never had, and module state that is never reset.

code_map_block() is a per-call block for a worker in worker_decompose / worker_work / worker_test: for the files of its
current task (named in the task text or changed for it), the code a test file of the task is about, and the project files
those load, it shows what each file EXPORTS (with parameters and what it returns), what stays inside the file (module
state, internal functions), the globals a browser script needs and, for a test file, the names it takes from each module and
which of them the module does not export. It is rebuilt from the files at every call, so no summary, compaction or
cleared history can make it stale. Deterministic, no LLM call, a few KB at most. carried_files_note() names the files a
worker read and changed before its history is cleared. Nothing here raises.
"""
import ast
import logging
import os
import re
import warnings
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_FILES = 6
MAX_FILE_CHARS = 950
MAX_BLOCK_CHARS = 4200
MAX_SOURCE_BYTES = 400_000
MAX_ITEMS = 14          # functions / methods / names listed per line
MAX_RETURN_CHARS = 48

PYTHON_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
CODE_SUFFIXES = PYTHON_SUFFIXES + JS_SUFFIXES
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv", "dist", "build",
                        "coverage", ".te_background", ".mypy_cache"})
CODE_MAP_HEADER = "[CODE MAP - the files of your task, read from disk by the framework just now]"

_cache: Dict[Tuple[str, float, int], List[str]] = {}
_CACHE_LIMIT = 256


# --------------------------------------------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------------------------------------------
def _short(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:max(0, limit - 3)] + "..."


def _language(rel: str) -> Optional[str]:
    suffix = PurePosixPath(str(rel)).suffix.lower()
    if suffix in PYTHON_SUFFIXES:
        return "python"
    if suffix in JS_SUFFIXES:
        return "javascript"
    return None


def is_test_path(rel: str) -> bool:
    """A test file by its folder or name (tests/x.js, test_x.py, x_test.py, x.test.js, x.spec.ts)."""
    path = PurePosixPath(str(rel).replace("\\", "/"))
    name = path.name.lower()
    if set(part.lower() for part in path.parts[:-1]) & _TEST_DIRS:
        return _language(str(path)) is not None
    return bool(re.match(r"^(test_.+|.+_test)\.py$", name) or re.match(r"^.+[._-](test|spec)\.[a-z]+$", name))


def _read(path: Path) -> Optional[str]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _rel(root: Path, path: Path) -> Optional[str]:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _join_limited(items: List[str], limit: int = MAX_ITEMS) -> str:
    shown = items[:limit]
    more = f", ... (+{len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(shown) + more


# --------------------------------------------------------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------------------------------------------------------
def _py_returns(node: ast.AST) -> List[str]:
    """The distinct `return` expressions of a function body (not of nested functions / classes)."""
    found: List[str] = []

    def visit(item: ast.AST) -> None:
        for child in ast.iter_child_nodes(item):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return):
                text = "None" if child.value is None else _short(ast.unparse(child.value), MAX_RETURN_CHARS)
                if text not in found:
                    found.append(text)
            visit(child)

    visit(node)
    return found


def _py_signature(node: Any, drop_self: bool = False) -> str:
    try:
        args = node.args
        if drop_self and args.args and args.args[0].arg in ("self", "cls"):
            rest = list(args.args[1:])
            defaults = list(args.defaults)[-len(rest):] if rest else []
            args = ast.arguments(posonlyargs=list(args.posonlyargs), args=rest, vararg=args.vararg,
                                 kwonlyargs=list(args.kwonlyargs), kw_defaults=list(args.kw_defaults), kwarg=args.kwarg,
                                 defaults=defaults)
        text = f"{node.name}({_short(ast.unparse(args), 70)})"
    except Exception:
        text = f"{getattr(node, 'name', '?')}(...)"
    if getattr(node, "returns", None) is not None:
        try:
            return f"{text} -> {_short(ast.unparse(node.returns), MAX_RETURN_CHARS)}"
        except Exception:
            return text
    all_returns = _py_returns(node)
    returns = [r for r in all_returns if r != "None"]
    if returns:
        has_none = "None" in all_returns
        shown = " | ".join(returns[:3]) + (" | None" if has_none else "") + (" | ..." if len(returns) > 3 else "")
        return f"{text} -> {shown}"
    return text


def python_outline(source: str) -> Optional[List[str]]:
    """The outline lines of a Python file, or None if it does not parse."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    functions: List[str] = []
    private_functions: List[str] = []
    classes: List[str] = []
    constants: List[str] = []
    state: List[str] = []
    tests: List[str] = []
    imports: List[str] = []
    runs_main = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                tests.append(node.name)
            elif node.name.startswith("_"):
                private_functions.append(_py_signature(node))
            else:
                functions.append(_py_signature(node))
        elif isinstance(node, ast.ClassDef):
            methods = [item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if node.name.startswith("Test"):
                tests.extend(f"{node.name}.{m.name}" for m in methods if m.name.startswith("test"))
                continue
            init = next((m for m in methods if m.name == "__init__"), None)
            bases = ", ".join(_short(ast.unparse(b), 30) for b in node.bases) if node.bases else ""
            head = f"class {node.name}" + (f"({bases})" if bases else "")
            if init is not None:
                head += f" - create with {_py_signature(init, drop_self=True).replace('__init__', node.name, 1)}"
            shown = [_py_signature(m, drop_self=True) for m in methods if m.name != "__init__"
                     and (not m.name.startswith("__") or m.name in ("__call__", "__iter__", "__len__"))]
            attributes = [t.id for item in node.body if isinstance(item, ast.Assign)
                          for t in item.targets if isinstance(t, ast.Name)]
            line = head
            if shown:
                line += f"; methods: {_join_limited(shown, 10)}"
            if attributes:
                line += f"; class attributes (shared by ALL instances): {_join_limited(attributes, 6)}"
            classes.append(line)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("__"):
                    value = getattr(node, "value", None)
                    shown = _short(ast.unparse(value), 28) if value is not None else "..."
                    (constants if target.id.isupper() else state).append(f"{target.id} = {shown}")
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imports.append(f"{node.module}: {', '.join(a.name for a in node.names)[:80]}")
        elif isinstance(node, ast.ImportFrom) and node.level:
            imports.append(f"{'.' * node.level}{node.module or ''}: {', '.join(a.name for a in node.names)[:80]}")
        elif isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.If):
            try:
                if "__name__" in ast.unparse(node.test) and "__main__" in ast.unparse(node.test):
                    runs_main = True
            except Exception:
                pass
        elif isinstance(node, ast.Try):
            for item in node.body:  # try: from x import ... except ImportError: ...
                if isinstance(item, ast.ImportFrom) and item.module:
                    imports.append(f"{item.module} (optional): {', '.join(a.name for a in item.names)[:60]}")
    lines: List[str] = []
    if tests:
        lines.append(f"tests ({len(tests)}): {_join_limited(tests, 8)}")
    if functions:
        lines.append(f"functions: {_join_limited(functions, 10)}")
    lines.extend(classes[:6])
    if private_functions:
        lines.append(f"internal helpers (leading '_'): {_join_limited(private_functions, 6)}")
    if constants:
        lines.append(f"constants: {_join_limited(constants, 8)}")
    if state:
        lines.append(f"module-level variables (one copy, shared by every importer and test): {_join_limited(state, 8)}")
    if imports:
        lines.append(f"imports: {_join_limited(imports, 8)}")
    if runs_main:
        lines.append("runs code only when started directly (if __name__ == '__main__')")
    return lines


# --------------------------------------------------------------------------------------------------------------------------
# JavaScript
# --------------------------------------------------------------------------------------------------------------------------
_JS_KEYWORDS = frozenset({"if", "for", "while", "switch", "catch", "function", "return", "else", "do", "try", "with",
                          "typeof", "new", "await", "yield", "constructor", "super", "import", "require"})
_BROWSER_GLOBALS = ("document", "window", "localStorage", "sessionStorage", "navigator", "fetch", "WebSocket",
                    "requestAnimationFrame", "alert", "location", "Image", "Audio")
_FUNCTION_DECL_RE = re.compile(r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(([^)]*)\)")
_FUNCTION_VAR_RE = re.compile(
    r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
    r"(?:function\b[^(]*\(([^)]*)\)|\(([^)]*)\)\s*=>|([A-Za-z_$][\w$]*)\s*=>)")
_CLASS_RE = re.compile(r"^(?:export\s+(?:default\s+)?)?class\s+([A-Za-z_$][\w$]*)(?:\s+extends\s+([\w$.]+))?")
_METHOD_RE = re.compile(r"^\s*(?:static\s+|async\s+|get\s+|set\s+)*(#?[A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{")
_VAR_RE = re.compile(r"^(?:export\s+)?(let|var|const)\s+([A-Za-z_$][\w$]*)\s*(?:=\s*(.*))?$")
_DESTRUCTURE_VAR_RE = re.compile(r"^(?:const|let|var)\s+\{([^}]*)\}\s*=\s*require\(\s*['\"`]([^'\"`]+)['\"`]\s*\)")
_ALIAS_VAR_RE = re.compile(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\(\s*['\"`]([^'\"`]+)['\"`]\s*\)")
_IMPORT_RE = re.compile(r"^import\s+(.+?)\s+from\s+['\"]([^'\"]+)['\"]")
_TEST_CALL_RE = re.compile(r"\b(?:it|test)\s*\(\s*(['\"`])((?:(?!\1).){1,80})\1")
_JSDOC_RETURNS_RE = re.compile(r"@returns?\s*\{([^}]{1,60})\}")


def _blank_strings_and_comments(source: str) -> str:
    """The source with comments and the CONTENT of string literals replaced by spaces (line breaks kept)."""
    out: List[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            end = source.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
        elif ch == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append("".join("\n" if c == "\n" else " " for c in source[i:end]))
            i = end
        elif ch in ("'", '"', "`"):
            j = i + 1
            while j < n and source[j] != ch:
                if source[j] == "\\":
                    j += 1
                elif source[j] == "\n" and ch != "`":
                    break
                j += 1
            j = min(j, n - 1)
            out.append(ch + "".join("\n" if c == "\n" else " " for c in source[i + 1:j]) + (source[j] if j > i else ""))
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _line_depths(clean: str) -> List[int]:
    """The brace depth at the start of every line of a blanked source."""
    depths: List[int] = []
    depth = 0
    for line in clean.split("\n"):
        depths.append(depth)
        depth = max(0, depth + line.count("{") - line.count("}"))
    return depths


def _matching_brace(clean: str, start: int) -> int:
    """The index of the '}' that closes the '{' at or after start (len(clean) if none)."""
    open_at = clean.find("{", start)
    if open_at < 0:
        return len(clean)
    depth = 0
    for index in range(open_at, len(clean)):
        if clean[index] == "{":
            depth += 1
        elif clean[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(clean)


def _object_keys(clean: str, open_at: int, limit: int = 8) -> str:
    """'{a, b, c, ...}': the top-level keys of the object literal whose '{' is at open_at (a blanked source).
    Round 3z: a multi-line `return {` or `let gameState = {` was shown as a bare '{' (2026-10-27 15:01: getGameState())."""
    end = _matching_brace(clean, open_at)
    body = clean[open_at + 1:end]
    keys: List[str] = []
    depth, item = 0, ""
    for ch in body + ",":
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        if ch == "," and depth == 0:
            key = re.match(r"\s*(?:\.\.\.\s*)?([A-Za-z_$][\w$]*|\[[^\]]*\])", item)
            if key and key.group(1) not in keys:
                keys.append(("..." if item.strip().startswith("...") else "") + key.group(1))
            item = ""
        else:
            item += ch
    if not keys:
        return "{}"
    return "{" + ", ".join(keys[:limit]) + (", ..." if len(keys) > limit else "") + "}"


def _export_items(source: str, clean: str) -> List[Tuple[str, str]]:
    """(name, shown text) of the entries of every `module.exports = { ... }` object: `a` -> 'a', `b: x` -> 'b',
    `getScore: () => score` -> 'getScore() -> score', `reset() { ... }` -> 'reset()'."""
    items: List[Tuple[str, str]] = []
    for match in re.finditer(r"\bmodule\.exports\s*=\s*\{", clean):
        end = _matching_brace(clean, match.end() - 1)
        start = match.end()
        depth, item_start = 0, start
        for index in range(start, end + 1):
            ch = clean[index] if index < end else ","
            if ch in "{([":
                depth += 1
            elif ch in "})]":
                depth -= 1
            if ch == "," and depth == 0:
                segment_start = item_start
                raw_clean = clean[segment_start:index]
                item_start = index + 1
                if raw_clean.strip().startswith("..."):
                    continue
                key = re.match(r"\s*(?:async\s+)?(?:get\s+|set\s+)?\*?\s*([A-Za-z_$][\w$]*)", raw_clean)
                if not key:
                    continue
                name = key.group(1)
                rest = raw_clean[key.end():]
                shown = name
                arrow = re.match(r"\s*:\s*(?:async\s*)?(?:\(([^)]*)\)|([A-Za-z_$][\w$]*))\s*=>\s*(.*)$", rest, re.DOTALL)
                function = re.match(r"\s*:\s*(?:async\s*)?function\b[^(]*\(([^)]*)\)", rest)
                method = re.match(r"\s*\(([^)]*)\)\s*\{", rest)
                if arrow:
                    params = arrow.group(1) if arrow.group(1) is not None else arrow.group(2)
                    body_offset = segment_start + key.end() + arrow.start(3)
                    body = source[body_offset:index].strip() if body_offset < index else ""
                    shown = f"{name}({_short(params, 50)})"
                    if body and not body.startswith("{"):
                        shown += f" -> {_short(body, MAX_RETURN_CHARS)}"
                elif function:
                    shown = f"{name}({_short(function.group(1), 50)})"
                elif method:
                    shown = f"{name}({_short(method.group(1), 50)})"
                items.append((name, shown))
    return items


def js_exported_names(source: str) -> List[str]:
    """Names a JavaScript module exports: module.exports = {a, b: x, c() {}}, exports.x =, export function/const/class x,
    export { a, b as c }. 'default' for a default export; 'X (the whole export)' for `module.exports = X`."""
    clean = _blank_strings_and_comments(source)
    names: List[str] = []

    def add(name: str) -> None:
        if name and name not in names:
            names.append(name)

    for name, _shown in _export_items(source, clean):
        add(name)
    single = re.search(r"\bmodule\.exports\s*=\s*([A-Za-z_$][\w$]*)\s*;?\s*$", clean, re.MULTILINE)
    if single and not names:
        add(f"{single.group(1)} (the whole export)")
    for match in re.finditer(r"\b(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=(?!=)", clean):
        add(match.group(1))
    for match in re.finditer(r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function\s*\*?|class|const|let|var)\s+"
                             r"([A-Za-z_$][\w$]*)", clean, re.MULTILINE):
        add(match.group(1))
    for match in re.finditer(r"^\s*export\s*\{([^}]*)\}", clean, re.MULTILINE):
        for part in match.group(1).split(","):
            add(part.split(" as ")[-1].strip())
    if re.search(r"^\s*export\s+default\b", clean, re.MULTILINE):
        add("default")
    return [name for name in names if re.match(r"^[A-Za-z_$][\w$]*( \(the whole export\))?$", name)]


_BLOCK_WORDS = frozenset({"if", "for", "while", "switch", "catch", "with"})


def _opens_function(clean: str, brace: int) -> bool:
    """True if the '{' at index brace starts a function body (after '=>' or after the parameters of a function/method)."""
    before = clean[:brace].rstrip()
    if before.endswith("=>"):
        return True
    if not before.endswith(")"):
        return False
    depth = 0
    for index in range(len(before) - 1, -1, -1):
        if before[index] == ")":
            depth += 1
        elif before[index] == "(":
            depth -= 1
            if depth == 0:
                word = re.search(r"([A-Za-z_$][\w$]*)\s*$", before[:index])
                return not word or word.group(1) not in _BLOCK_WORDS
    return False


def _js_returns(source: str, clean: str, body_start: int) -> List[str]:
    """Distinct `return` expressions of the function whose body starts at the first '{' from body_start (returns of
    nested functions and callbacks are left out)."""
    open_at = clean.find("{", body_start)
    found: List[str] = []
    if open_at < 0:
        return found
    end = _matching_brace(clean, open_at)
    stack: List[bool] = []   # True for a nested function body
    i = open_at
    while i < end:
        ch = clean[i]
        if ch == "{":
            stack.append(i != open_at and _opens_function(clean, i))
        elif ch == "}":
            if stack:
                stack.pop()
        elif clean.startswith("return", i) and not any(stack) and not re.match(r"[\w$]", clean[i - 1:i] or " ") \
                and not re.match(r"[\w$]", clean[i + 6:i + 7] or " "):
            stop = i + 6
            while stop < end and clean[stop] not in ";\n}":
                stop += 1
            text = _short(source[i + 6:stop], MAX_RETURN_CHARS) or "undefined"
            if text.startswith("{") and clean.find("{", i + 6) >= 0:
                brace_at = clean.find("{", i + 6)
                text = _object_keys(clean, brace_at)  # Round 3z: the shape of a returned object
                stop = max(stop, _matching_brace(clean, brace_at))
            if text not in found:
                found.append(text)
            i = stop
            continue
        i += 1
    return found


def _jsdoc_returns(source: str, line_start: int) -> Optional[str]:
    """The @returns type of the JSDoc block right above the line that starts at line_start."""
    before = source[:line_start].rstrip()
    if not before.endswith("*/"):
        return None
    block_start = before.rfind("/**")
    if block_start < 0:
        return None
    match = _JSDOC_RETURNS_RE.search(before[block_start:])
    return _short(match.group(1), MAX_RETURN_CHARS) if match else None


def _js_function_text(name: str, params: str, returns: List[str], doc_type: Optional[str]) -> str:
    text = f"{name}({_short(params, 60)})"
    real = [r for r in returns if r != "undefined"]
    if doc_type:
        if len(real) > 1 or (real and "null" in real and "null" not in doc_type):
            return f"{text} -> {{{doc_type}}} (the code returns: " + " | ".join(real[:3]) + \
                (" | ..." if len(real) > 3 else "") + ")"
        return f"{text} -> {{{doc_type}}}"
    if real:
        return f"{text} -> " + " | ".join(real[:3]) + (" | ..." if len(real) > 3 else "")
    return text


def _shown_value(original_line: str) -> str:
    match = re.search(r"=\s*(.*)$", original_line)
    if not match:
        return ""
    value = re.split(r"\s//", match.group(1))[0].rstrip().rstrip(";")
    return _short(value, 26)


def javascript_outline(source: str, test_file: bool = False) -> List[str]:
    """The outline lines of a JavaScript file (best effort; a file that does not parse still gets one)."""
    clean = _blank_strings_and_comments(source)
    source_lines = source.split("\n")
    clean_lines = clean.split("\n")
    depths = _line_depths(clean)
    offsets: List[int] = []
    total = 0
    for line in clean_lines:
        offsets.append(total)
        total += len(line) + 1
    functions: Dict[str, str] = {}
    classes: List[Tuple[str, str]] = []
    variables: List[str] = []
    constants: List[str] = []
    loads: List[str] = []
    state = {"name": None, "head": "", "methods": []}

    def close_class() -> None:
        if state["name"]:
            line = state["head"] + (f"; methods: {_join_limited(state['methods'], 10)}" if state["methods"] else "")
            classes.append((state["name"], line))
        state.update(name=None, head="", methods=[])

    for number, line in enumerate(clean_lines):
        depth = depths[number]
        original = source_lines[number] if number < len(source_lines) else line
        stripped = line.strip()
        if state["name"] is not None and depth == 1:
            method = _METHOD_RE.match(line)
            if method and method.group(1) not in _JS_KEYWORDS - {"constructor"}:
                params = re.search(r"\(([^)]*)\)", original)
                brace = offsets[number] + line.find("{", method.start())
                if method.group(1) == "constructor":
                    state["head"] += f" - create with new {state['name']}({_short(params.group(1) if params else '', 50)})"
                else:
                    state["methods"].append(_js_function_text(
                        method.group(1), params.group(1) if params else "", _js_returns(source, clean, brace),
                        _jsdoc_returns(source, offsets[number])))
            continue
        if depth != 0:
            continue
        if state["name"] is not None:
            close_class()
        original_stripped = original.strip()
        match = _FUNCTION_DECL_RE.match(stripped)
        if match:
            params = re.search(r"\(([^)]*)\)", original)
            brace = line.find("{")
            returns = _js_returns(source, clean, offsets[number] + brace) if brace >= 0 else []
            functions[match.group(1)] = _js_function_text(match.group(1), params.group(1) if params else "", returns,
                                                          _jsdoc_returns(source, offsets[number]))
            continue
        match = _FUNCTION_VAR_RE.match(stripped)
        if match:
            single = match.group(4)
            params = re.search(r"\(([^)]*)\)", original)
            shown_params = single if single is not None else (params.group(1) if params else "")
            brace = line.find("{", match.end() - 1)
            returns = _js_returns(source, clean, offsets[number] + brace) if brace >= 0 else []
            functions[match.group(1)] = _js_function_text(match.group(1), shown_params, returns,
                                                          _jsdoc_returns(source, offsets[number]))
            continue
        match = _CLASS_RE.match(stripped)
        if match:
            state.update(name=match.group(1), methods=[],
                         head=f"class {match.group(1)}" + (f" extends {match.group(2)}" if match.group(2) else ""))
            continue
        match = _DESTRUCTURE_VAR_RE.match(original_stripped)
        if match:
            loads.append(f"{match.group(2)} -> {{{_short(' '.join(match.group(1).split()), 90)}}}")
            continue
        match = _ALIAS_VAR_RE.match(original_stripped)
        if match:
            loads.append(f"{match.group(2)} -> {match.group(1)}")
            continue
        match = _IMPORT_RE.match(original_stripped)
        if match:
            loads.append(f"{match.group(2)} -> {_short(match.group(1), 90)}")
            continue
        match = _VAR_RE.match(stripped)
        if match:
            kind, name, value = match.group(1), match.group(2), (match.group(3) or "").strip()
            if value.startswith("require("):
                continue
            shown = _shown_value(original)
            if value.startswith("{"):
                shown = _object_keys(clean, offsets[number] + line.find("{", match.start(3) if match.start(3) >= 0 else 0))
            entry = f"{name} = {shown}" if shown else name
            # Round 4a: a const Map/Set/object/array is module STATE (its content changes), unless it is an UPPER_CASE config
            container = value.startswith(("{", "[", "new "))
            if kind == "const" and ((name.isupper() and not value.startswith("new ")) or not container):
                constants.append(entry)
            else:
                variables.append(entry)
    close_class()
    if test_file:
        lines: List[str] = []
        if loads:
            lines.append(f"loads: {_join_limited(loads, 6)}")
        tests = [_short(m.group(2), 50) for m in _TEST_CALL_RE.finditer(source)]
        if not tests:
            tests = [name for name in functions if name.lower().startswith("test")]
        lines.append(f"tests ({len(tests)}): {_join_limited(['%r' % t for t in tests], 6)}" if tests else
                     "tests: none found (no it()/test() calls and no test functions)")
        helpers = [text for name, text in functions.items() if not name.lower().startswith("test")]
        if helpers:
            lines.append(f"helpers defined in the test: {_join_limited(helpers, 6)}")
        return lines
    exported = js_exported_names(source)
    exported_plain = [name for name in exported if " " not in name]
    for match in re.finditer(r"^\s*export\s+default\s+(?:class\s+|(?:async\s+)?function\s*\*?\s*)?([A-Za-z_$][\w$]*)",
                             clean, re.MULTILINE):
        exported_plain.append(match.group(1))  # `export default Game`: Game is what the module hands out
    whole = re.search(r"\bmodule\.exports\s*=\s*([A-Za-z_$][\w$]*)\s*;?\s*$", clean, re.MULTILINE)
    if whole:
        exported_plain.append(whole.group(1))
    export_texts = dict(_export_items(source, clean))
    lines = []
    if exported:
        shown = []
        for name in exported:
            text = export_texts.get(name, name)
            if text == name and name in functions:
                text = functions[name]
            shown.append(text)
        lines.append(f"EXPORTS (all that a test or another file can use): {_join_limited(shown)}")
    elif functions or classes:
        lines.append("EXPORTS NOTHING (no module.exports / export): a test or another file cannot use it with require/import")
    internal = [text for name, text in functions.items() if name not in exported_plain]
    if internal:
        lines.append(f"internal functions (NOT exported, a test cannot call them): {_join_limited(internal, 8)}")
    for name, line in classes[:4]:
        lines.append(line + ("" if name in exported_plain or not exported else " (NOT exported)"))
    hidden_state = [v for v in variables if v.split(" =")[0] not in exported_plain]
    if hidden_state:
        lines.append(f"module state (inside the file - a test can NOT read or set it, only the exported functions "
                     f"change it): {_join_limited(hidden_state, 8)}")
    if constants:
        lines.append(f"constants: {_join_limited(constants, 6)}")
    if loads:
        lines.append(f"loads: {_join_limited(loads, 5)}")
    browser = [name for name in _BROWSER_GLOBALS if re.search(rf"(?<![\w$.]){re.escape(name)}\b", clean)
               and not re.search(rf"(?:const|let|var|function)\s+{re.escape(name)}\b", clean)]
    if browser:
        lines.append(f"uses browser globals (a Node.js test must provide them before it loads the file): "
                     f"{', '.join(browser)}")
    return lines


# --------------------------------------------------------------------------------------------------------------------------
# links between files
# --------------------------------------------------------------------------------------------------------------------------
_JS_LOAD_RE = re.compile(r"""\brequire\s*\(\s*(['"`])([^'"`\n]+)\1\s*\)|\bfrom\s+(['"])([^'"\n]+)\3|\bimport\s+(['"])([^'"\n]+)\5""")


def _resolve_js(root: Path, from_rel: str, spec: str) -> Optional[str]:
    if not spec.startswith("."):
        return None
    base = (root / from_rel).parent / spec
    for candidate in [base] + [Path(str(base) + s) for s in JS_SUFFIXES] + [base / "index.js"]:
        try:
            if candidate.is_file():
                rel = _rel(root, candidate)
                if rel:
                    return rel
        except OSError:
            continue
    return None


def _resolve_python(root: Path, module: str) -> Optional[str]:
    parts = [p for p in module.split(".") if p]
    if not parts:
        return None
    for base in (root, root / "src"):
        for candidate in (base.joinpath(*parts).with_suffix(".py"), base.joinpath(*parts) / "__init__.py"):
            try:
                if candidate.is_file():
                    rel = _rel(root, candidate)
                    if rel:
                        return rel
            except OSError:
                continue
    return None


def project_loads(root: Path, rel: str, source: str) -> List[str]:
    """The project files a file loads (require/import of relative JS paths; Python imports of project modules)."""
    found: List[str] = []
    language = _language(rel)
    if language == "javascript":
        clean_comments = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
        clean_comments = "\n".join(l for l in clean_comments.splitlines() if not l.lstrip().startswith("//"))
        for match in _JS_LOAD_RE.finditer(clean_comments):
            spec = match.group(2) or match.group(4) or match.group(6)
            target = _resolve_js(root, rel, spec) if spec else None
            if target and target != rel and target not in found:
                found.append(target)
    elif language == "python":
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return found
        for node in ast.walk(tree):
            modules: List[str] = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                modules = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for module in modules:
                for name in (module, module[4:] if module.startswith("src.") else ""):
                    target = _resolve_python(root, name) if name else None
                    if target and target != rel and target not in found:
                        found.append(target)
                        break
    return found


def _code_files(root: Path, limit: int = 400) -> List[str]:
    files: List[str] = []
    for walk_root, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            if PurePosixPath(name).suffix.lower() in CODE_SUFFIXES:
                rel = _rel(root, Path(walk_root) / name)
                if rel and not is_test_path(rel) and PurePosixPath(rel).name not in ("conftest.py", "__init__.py"):
                    files.append(rel)
                    if len(files) >= limit:
                        return files
    return files


def _subject_by_name(test_rel: str, code_files: List[str]) -> Optional[str]:
    stem = PurePosixPath(test_rel).name.lower()
    for token in ("test_", "_test", ".test", "-test", ".spec", "_spec", "test-"):
        stem = stem.replace(token, "")
    stem = stem.split(".")[0]
    if len(stem) < 3:
        return None
    wanted_language = _language(test_rel)
    same = [f for f in code_files if PurePosixPath(f).stem.lower() == stem]
    same.sort(key=lambda f: (_language(f) != wanted_language, f))
    return same[0] if same else None


def test_subjects(root: Path, test_rel: str, source: Optional[str], code_files: Optional[List[str]] = None) -> List[str]:
    """The project code files a test file is about: the files it loads, else the code file of the same name."""
    subjects = [f for f in project_loads(root, test_rel, source or "") if not is_test_path(f)] if source else []
    if subjects:
        return subjects
    by_name = _subject_by_name(test_rel, code_files if code_files is not None else _code_files(root))
    return [by_name] if by_name else []


# --------------------------------------------------------------------------------------------------------------------------
# a test against the exports of the modules it loads
# --------------------------------------------------------------------------------------------------------------------------
def _taken_names(source: str) -> List[Tuple[str, str]]:
    """(spec, name) for every name a JS file takes by destructuring: const {a, b: c} = require('x'); import {a} from 'x'."""
    taken: List[Tuple[str, str]] = []
    clean = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    for match in re.finditer(r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\(\s*['\"`]([^'\"`]+)['\"`]\s*\)", clean):
        for part in match.group(1).split(","):
            name = part.split(":")[0].strip()
            if re.match(r"^[A-Za-z_$][\w$]*$", name):
                taken.append((match.group(2), name))
    for match in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]", clean):
        for part in match.group(1).split(","):
            name = part.split(" as ")[0].strip()
            if re.match(r"^[A-Za-z_$][\w$]*$", name):
                taken.append((match.group(2), name))
    # Round 4a: `const player = require('../src/player.js')` and then `player.players.clear()` - a name read from the module
    # object (2026-10-27 18:56 tests/test_player/test_player.js: src/player.js keeps `players` inside, so every test failed
    # with "Cannot read properties of undefined (reading 'clear')")
    blanked = _blank_strings_and_comments(source)
    for match in re.finditer(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\(\s*['\"`]([^'\"`]+)['\"`]\s*\)", clean):
        alias, spec = match.group(1), match.group(2)
        for use in re.finditer(rf"(?<![\w$.]){re.escape(alias)}\.([A-Za-z_$][\w$]*)", blanked):
            if (spec, use.group(1)) not in taken:
                taken.append((spec, use.group(1)))
    return taken


def missing_imported_names(root: Path, rel: str, source: str) -> List[Tuple[str, List[str], List[str]]]:
    """(module rel, names the file takes that the module does not export, what it exports) for JS files."""
    problems: List[Tuple[str, List[str], List[str]]] = []
    if _language(rel) != "javascript":
        return problems
    by_spec: Dict[str, List[str]] = {}
    for spec, name in _taken_names(source):
        by_spec.setdefault(spec, []).append(name)
    for spec, names in by_spec.items():
        target = _resolve_js(root, rel, spec)
        if not target:
            continue
        module_source = _read(root / target)
        if module_source is None:
            continue
        exported = js_exported_names(module_source)
        if not exported or any(" " in name for name in exported):
            continue  # exports nothing (another check names that) or one whole value: cannot judge
        missing = [name for name in names if name not in exported]
        if missing:
            problems.append((target, missing, exported))
    return problems


def _declared_in(clean: str, name: str) -> bool:
    """True if a (blanked) JS source declares `name` itself: a variable, function or class, a destructured variable, or a
    parameter of a function or an arrow function."""
    word = re.escape(name)
    if re.search(rf"\b(?:const|let|var|function|class)\s+{word}\b", clean):
        return True
    if re.search(rf"\b(?:const|let|var)\s*[{{\[][^}}\]=]*(?<![\w$.]){word}\b[^}}\]=]*[}}\]]\s*=", clean):
        return True
    for params in re.findall(r"\bfunction\b[^(]*\(([^)]*)\)", clean) + re.findall(r"\(([^()]*)\)\s*=>", clean):
        if re.search(rf"(?<![\w$.]){word}\b", params):
            return True
    return bool(re.search(rf"(?<![\w$.]){word}\s*=>", clean))


def module_internal_uses(root: Path, test_rel: str, source: str, limit: int = 6) -> List[Tuple[str, List[Tuple[str, int]], List[str]]]:
    """
    For a JS test file: (module rel, [(name, line)], exports) for the TOP-LEVEL variables of a loaded project module that the
    test uses by their bare name although the module does not export them and the test never declares them itself.
    tests/test_game.js (2026-10-27) did `snake = [...]` and `assert(snake.length === 3)` for src/game.js's `let snake`: the
    assignment makes a NEW global variable, the module keeps its own, so the test never sees the module's values.
    """
    found: List[Tuple[str, List[Tuple[str, int]], List[str]]] = []
    if _language(test_rel) != "javascript":
        return found
    clean_test = _blank_strings_and_comments(source)
    for target in project_loads(root, test_rel, source):
        module_source = _read(root / target)
        if module_source is None or is_test_path(target):
            continue
        module_clean = _blank_strings_and_comments(module_source)
        depths = _line_depths(module_clean)
        top_vars: List[str] = []
        for number, line in enumerate(module_clean.split("\n")):
            if depths[number] != 0:
                continue
            match = re.match(r"^\s*(?:let|var)\s+([A-Za-z_$][\w$]*)", line) or re.match(
                r"^\s*const\s+([a-z_$][\w$]*)\s*=\s*(?:new\s|\{|\[)", line)  # Round 4a: + a const Map/Set/object/array
            if match:
                top_vars.append(match.group(1))
        exported = js_exported_names(module_source)
        candidates = [name for name in top_vars if name not in exported and len(name) >= 3]
        uses: List[Tuple[str, int]] = []
        for name in candidates:
            if _declared_in(clean_test, name):
                continue
            for match in re.finditer(rf"(?<![\w$.]){re.escape(name)}\b(?!\s*:)", clean_test):
                uses.append((name, clean_test.count("\n", 0, match.start()) + 1))
                break
            if len(uses) >= limit:
                break
        if uses:
            found.append((target, uses, exported))
    return found


def module_internal_hint(root: Any, test_rel: str, source: Optional[str] = None) -> Optional[str]:
    """The note for module_internal_uses() (None if there is nothing to say). Never raises."""
    try:
        root = Path(root)
        if source is None:
            source = _read(root / test_rel)
        if not source:
            return None
        parts = []
        for target, uses, exported in module_internal_uses(root, test_rel, source):
            names = ", ".join(f"`{name}` (line {line})" for name, line in uses)
            getters = [e for e in exported if re.match(r"^(get|is|has)[A-Z_]", e)]
            reach = (f" Use what {target} exports instead (e.g. {', '.join(g + '()' for g in getters[:4])})"
                     if getters else f" Use the functions {target} exports (or add an exported getter/reset function there)")
            parts.append(
                f"{test_rel} uses {names}: variable(s) INSIDE {target} that it does not export. A test cannot see them: "
                f"assigning one makes a NEW global variable, and reading it never shows the module's value, so these "
                f"tests fail although the code may be right.{reach}.")
        return " ".join(parts) or None
    except Exception as err:
        logger.debug(f"module_internal_hint({test_rel}) failed: {err}")
        return None


def missing_names_hint(root: Any, rel: str, source: Optional[str] = None) -> Optional[str]:
    """The note for missing_imported_names() (None if there is nothing to say). Never raises."""
    try:
        root = Path(root)
        if source is None:
            source = _read(root / rel)
        if not source:
            return None
        parts = [f"{rel} takes {', '.join('`%s`' % n for n in missing)} from {target}, which does not export "
                 f"{'it' if len(missing) == 1 else 'them'} (it exports: {_join_limited(exported, 12)}), so "
                 f"{'it is' if len(missing) == 1 else 'they are'} undefined there."
                 for target, missing, exported in missing_imported_names(root, rel, source)]
        return " ".join(parts) or None
    except Exception as err:
        logger.debug(f"missing_names_hint({rel}) failed: {err}")
        return None


# --------------------------------------------------------------------------------------------------------------------------
# Round 4a: a timer a test starts and never stops
# --------------------------------------------------------------------------------------------------------------------------
# 2026-10-27 18:45-19:10 (WiFiSnake_Game) `node --test tests/test_game/test_game.js` timed out SIX times (W3, then W1): the
# test called game.startGameLoop(), which runs `setInterval(moveSnake, SPEED_MS)` in src/game.js, and never called
# game.stopGameLoop(). The open timer keeps Node running, so the run never ends; the framework only said "a timer/setInterval
# that is never cleared" in general.
_TIMER_START_RE = re.compile(r"(?<![\w$.])(?:setInterval|setTimeout)\s*\(")
_TIMER_STOP_RE = re.compile(r"(?<![\w$.])(?:clearInterval|clearTimeout)\s*\(")


def _matching_paren(clean: str, open_at: int) -> int:
    """The index of the ')' that closes the '(' at open_at (len(clean) if none)."""
    depth = 0
    for index in range(open_at, len(clean)):
        if clean[index] == "(":
            depth += 1
        elif clean[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return len(clean)


def _top_level_function_bodies(source: str) -> Dict[str, Tuple[int, str]]:
    """{name: (line, blanked body)} of the top-level functions (declarations and `const f = (...) => {`) of a JS source."""
    clean = _blank_strings_and_comments(source)
    depths = _line_depths(clean)
    bodies: Dict[str, Tuple[int, str]] = {}
    offset = 0
    for number, line in enumerate(clean.split("\n")):
        if depths[number] == 0:
            match = _FUNCTION_DECL_RE.match(line.strip()) or _FUNCTION_VAR_RE.match(line.strip())
            brace = line.find("{")
            if match and brace >= 0:
                start = offset + brace
                bodies[match.group(1)] = (number + 1, clean[start:_matching_brace(clean, start) + 1])
        offset += len(line) + 1
    return bodies


def open_timer_problems(root: Any, test_rel: str, source: Optional[str] = None) -> List[str]:
    """The timers a JS test starts through a loaded project module and never stops (see above). Never raises."""
    problems: List[str] = []
    try:
        root = Path(root)
        if _language(test_rel) != "javascript":
            return problems
        if source is None:
            source = _read(root / test_rel)
        if not source:
            return problems
        clean_test = _blank_strings_and_comments(source)
        for target in project_loads(root, test_rel, source):
            module_source = _read(root / target)
            if not module_source or is_test_path(target):
                continue
            module_clean = _blank_strings_and_comments(module_source)
            depths = _line_depths(module_clean)
            for number, line in enumerate(module_clean.split("\n")):
                if depths[number] == 0 and _TIMER_START_RE.search(line) and not _FUNCTION_DECL_RE.match(line.strip()):
                    problems.append(f"{target} starts a timer when it is LOADED (line {number + 1}): every test file that "
                                    f"loads it ({test_rel}) never ends. Start the timer in a function instead, or call "
                                    "`.unref()` on it.")
                    break
            bodies = _top_level_function_bodies(module_source)
            starters = {name for name, (_l, body) in bodies.items() if _TIMER_START_RE.search(body)
                        and not _TIMER_STOP_RE.search(body)}
            for _round in range(2):  # a function that calls a starter starts the timer too
                starters |= {name for name, (_l, body) in bodies.items() if name not in starters and any(
                    re.search(rf"(?<![\w$.]){re.escape(s)}\s*\(", body) for s in starters)}
            stoppers = [name for name, (_l, body) in bodies.items() if _TIMER_STOP_RE.search(body)]
            exported = set(js_exported_names(module_source))

            def called(name: str) -> Optional[int]:
                match = re.search(rf"(?<![\w$])(?:[A-Za-z_$][\w$]*\.)?{re.escape(name)}\s*\(", clean_test)
                return clean_test.count("\n", 0, match.start()) + 1 if match else None

            def count(name: str) -> int:
                return len(re.findall(rf"(?<![\w$])(?:[A-Za-z_$][\w$]*\.)?{re.escape(name)}\s*\(", clean_test))

            started = [(name, called(name)) for name in sorted(starters) if name in exported and called(name)]
            if not started:
                continue
            # Round 4g: a hook counts only where it runs - not one called INSIDE a test (it registers a hook for that
            # test's sub-tests, which never runs), nor one nothing provides (2026-10-30 tests/game.test.js called
            # `afterEach(() => { stopGameLoop(); })` inside its tests without taking afterEach from node:test: the run of
            # that file never ended from 01:27 to 02:47, and the framework only gave its general hint)
            block_spans = [(m.end(), _matching_paren(clean_test, m.end() - 1))
                           for m in re.finditer(r"(?<![\w$.])(?:it|test)\s*\(", clean_test)]
            from src.utils.js_test_checks import missing_runner_globals
            unprovided = set(missing_runner_globals(root, test_rel))
            hook_spans = [(m.start(), _matching_paren(clean_test, m.end() - 1))
                          for m in re.finditer(r"(?<![\w$.])(afterEach|after|afterAll)\s*\(", clean_test)]
            hooks = "".join(clean_test[start:end] for start, end in hook_spans
                            if not any(b_start <= start < b_end for b_start, b_end in block_spans)
                            and re.match(r"\w+", clean_test[start:]).group(0) not in unprovided)
            if any(re.search(rf"(?<![\w$])(?:[A-Za-z_$][\w$]*\.)?{re.escape(stop)}\s*\(", hooks) for stop in stoppers):
                continue  # an after hook stops it

            def without_hooks(start: int, end: int) -> str:
                """The text of a test block without the hooks called inside it (they never run)."""
                text, cut = clean_test[start:end], 0
                for h_start, h_end in hook_spans:
                    if start <= h_start < end:
                        text = text[:h_start - start - cut] + text[min(h_end, end) - start - cut:]
                        cut += min(h_end, end) - h_start
                return text

            blocks = [(clean_test.count("\n", 0, b_start) + 1, without_hooks(b_start, b_end)) for b_start, b_end in block_spans]
            if blocks:
                # a test (it/test block) that starts the timer and does not stop it
                open_starts = []
                for block_line, body in blocks:
                    starts = [name for name, _l in started
                              if re.search(rf"(?<![\w$])(?:[A-Za-z_$][\w$]*\.)?{re.escape(name)}\s*\(", body)]
                    stops = [stop for stop in stoppers
                             if re.search(rf"(?<![\w$])(?:[A-Za-z_$][\w$]*\.)?{re.escape(stop)}\s*\(", body)]
                    if starts and not stops:
                        open_starts.append((starts[0], block_line))
                if not open_starts:
                    continue
                started = open_starts
            elif sum(count(stop) for stop in stoppers) >= sum(count(name) for name, _l in started):
                continue  # every start has its stop
            started.sort(key=lambda item: item[1])
            name, line = started[0]
            if stoppers:
                usable = [s for s in stoppers if s in exported]
                fix = (f"call `{usable[0]}()` in an afterEach()/after() hook (take afterEach from require('node:test'))"
                       if usable else
                       f"export `{stoppers[0]}` from {target} and call it in an afterEach()/after() hook")
            else:
                fix = (f"give {target} a function that clears the timer (clearInterval), export it and call it in an "
                       "afterEach()/after() hook")
            # Round 4g: say where a hook does NOT run (inside a test, or nothing provides it)
            misplaced = [clean_test.count("\n", 0, start) + 1 for start, _end in hook_spans
                         if any(b_start <= start < b_end for b_start, b_end in block_spans)]
            missing_hooks = sorted({re.match(r"\w+", clean_test[start:]).group(0) for start, _end in hook_spans} & unprovided)
            why = ""
            if misplaced:
                why += (f" {'Line' if len(misplaced) == 1 else 'Lines'} {', '.join(str(n) for n in misplaced[:3])} "
                        f"{'calls a hook' if len(misplaced) == 1 else 'call hooks'} INSIDE a test: there it never runs - "
                        "call afterEach() once, at the top of the describe() block, beside the tests.")
            if missing_hooks:
                why += (f" {', '.join('`%s`' % h for h in missing_hooks)} is not taken from anywhere: add it to "
                        "`require('node:test')` at the top of the file.")
            problems.append(f"{test_rel} calls `{name}()` (the test at line {line}), which starts a setInterval/setTimeout in {target} "
                            f"(line {bodies[name][0]}), and does not stop it every time: an open timer keeps Node running, so "
                            f"the test run NEVER ends. To fix: {fix}.{why}")
        # Round 4o: a timer a CLASS starts when a test makes one (`new ScoreManager()`)
        for finding in class_timer_findings(root, test_rel, source):
            text = class_timer_text(finding, test_rel)
            if text not in problems:
                problems.append(text)
    except Exception as err:
        logger.debug(f"open_timer_problems({test_rel}) failed: {err}")
    return problems


# --------------------------------------------------------------------------------------------------------------------------
# Round 4o: a timer a class starts in its constructor
# --------------------------------------------------------------------------------------------------------------------------
# 2026-10-31 09:53-10:48 (WiFi_Multiplayer_Snake_Game, qwen3.5:4b) the tests of "Kick-off Task 7" never ended: the
# constructors of ScoreManager (src/score.js, W1) and Leaderboard (src/leaderboard.js, W2) call setupAutoSave() /
# setupAutoRefresh(), which run `setInterval(...)` and keep no way to clear it, and every test makes one in beforeEach().
# The framework refused the finish 14 times with the general hint (Round 4a only looks at top-level functions), and W3
# spent an hour rewriting its tests - the fix belongs in the classes, which are other workers' files.
_CLASS_DECL_RE = re.compile(r"^(?:export\s+(?:default\s+)?)?class\s+([A-Za-z_$][\w$]*)(?:\s+extends\s+[\w$.]+)?\s*\{")
_METHOD_DECL_RE = re.compile(r"^(?:static\s+)?(?:async\s+)?\*?\s*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")
_NOT_METHODS = frozenset({"if", "for", "while", "switch", "catch", "function", "return", "with", "else"})
_TIMER_UNREF_RE = re.compile(r"\.unref\s*\(")


def _class_methods(clean: str, open_at: int, close_at: int) -> Dict[str, Tuple[int, str]]:
    """{method: (line, blanked body)} of the class whose body is clean[open_at:close_at] (line counted in clean)."""
    methods: Dict[str, Tuple[int, str]] = {}
    depth, offset = 0, open_at + 1
    for line in clean[open_at + 1:close_at].split("\n"):
        if depth == 0:
            match = _METHOD_DECL_RE.match(line.strip())
            if match and match.group(1) not in _NOT_METHODS:
                start = offset + line.find("{", line.find(match.group(1)))
                methods.setdefault(match.group(1), (clean.count("\n", 0, offset) + 1,
                                                    clean[start:_matching_brace(clean, start) + 1]))
        depth = max(0, depth + line.count("{") - line.count("}"))
        offset += len(line) + 1
    return methods


def _test_hook_text(root: Path, test_rel: str, clean_test: str) -> str:
    """The text of the after/afterEach/afterAll hooks of a test file that run (beside the tests, and provided)."""
    from src.utils.js_test_checks import missing_runner_globals
    unprovided = set(missing_runner_globals(root, test_rel))
    blocks = [(m.end(), _matching_paren(clean_test, m.end() - 1))
              for m in re.finditer(r"(?<![\w$.])(?:it|test)\s*\(", clean_test)]
    hooks = [(m.start(), _matching_paren(clean_test, m.end() - 1), m.group(1))
             for m in re.finditer(r"(?<![\w$.])(afterEach|after|afterAll)\s*\(", clean_test)]
    return "".join(clean_test[start:end] for start, end, name in hooks
                   if name not in unprovided and not any(b_start <= start < b_end for b_start, b_end in blocks))


def class_timer_findings(root: Any, test_rel: str, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    The exported classes a JS test makes (`new X(`) whose constructor - itself or through `this.method()` - starts a
    setInterval/setTimeout it neither clears nor `.unref()`s, in the project modules the test loads: [{"module", "cls",
    "line" (of the timer), "method" (that starts it), "stoppers" (methods that clear a timer), "test_line" (the first
    `new X(`)}]. A class whose stopper an after/afterEach hook of the test calls is left out. Never raises.
    """
    findings: List[Dict[str, Any]] = []
    try:
        root = Path(root)
        if _language(test_rel) != "javascript":
            return findings
        if source is None:
            source = _read(root / test_rel)
        if not source:
            return findings
        clean_test = _blank_strings_and_comments(source)
        hooks: Optional[str] = None
        for target in project_loads(root, test_rel, source):
            module_source = _read(root / target)
            if not module_source or is_test_path(target):
                continue
            clean = _blank_strings_and_comments(module_source)
            depths = _line_depths(clean)
            exported = set(js_exported_names(module_source))
            offset = 0
            for number, line in enumerate(clean.split("\n")):
                match = _CLASS_DECL_RE.match(line.strip()) if depths[number] == 0 else None
                made = None
                if match and match.group(1) in exported:
                    made = re.search(rf"(?<![\w$])new\s+(?:[A-Za-z_$][\w$]*\.)?{re.escape(match.group(1))}\s*\(", clean_test)
                if made:
                    cls = match.group(1)
                    open_at = offset + line.find("{", line.find(cls))
                    methods = _class_methods(clean, open_at, _matching_brace(clean, open_at))
                    direct = {name for name, (_l, body) in methods.items() if _TIMER_START_RE.search(body)
                              and not _TIMER_STOP_RE.search(body) and not _TIMER_UNREF_RE.search(body)}
                    reached: set = set()
                    todo = ["constructor"] if "constructor" in methods else []
                    while todo:
                        name = todo.pop()
                        if name in reached:
                            continue
                        reached.add(name)
                        todo += [m for m in methods if m not in reached
                                 and re.search(rf"(?<![\w$])this\.{re.escape(m)}\s*\(", methods[name][1])]
                    starters = sorted((methods[name][0], name) for name in direct & reached)
                    stoppers = [name for name, (_l, body) in methods.items() if _TIMER_STOP_RE.search(body)]
                    if starters and hooks is None:
                        hooks = _test_hook_text(root, test_rel, clean_test)
                    if starters and not any(re.search(rf"\.{re.escape(stop)}\s*\(", hooks or "") for stop in stoppers):
                        method_line, method = starters[0]
                        body = methods[method][1]
                        timer = _TIMER_START_RE.search(body)
                        findings.append({"module": target, "cls": cls, "method": method, "stoppers": stoppers,
                                         "line": method_line + (body.count("\n", 0, timer.start()) if timer else 0),
                                         "test_line": clean_test.count("\n", 0, made.start()) + 1})
                offset += len(line) + 1
    except Exception as err:
        logger.debug(f"class_timer_findings({test_rel}) failed: {err}")
    return findings


def class_timer_text(finding: Dict[str, Any], test_rel: Optional[str] = None) -> str:
    """The hint for one finding of class_timer_findings."""
    cls, module, method = finding["cls"], finding["module"], finding["method"]
    where = "its constructor" if method == "constructor" else f"its constructor (through `{method}()`)"
    made = (f"{test_rel} makes a `{cls}` (line {finding['test_line']}), and" if test_rel else f"A test makes a `{cls}`, and")
    if finding.get("stoppers"):
        fix = (f"call `.{finding['stoppers'][0]}()` on every `{cls}` the tests make, in an afterEach() hook (take "
               "afterEach from require('node:test'))")
    else:
        fix = (f"change {module}: keep the timer's id (`this.timer = setInterval(...)`) and give `{cls}` a method that "
               "clears it (clearInterval) for the tests to call in afterEach() - or call `.unref()` on the timer so it does "
               f"not keep Node running. A test alone cannot stop this timer, and the same command will time out again")
    return (f"{made} {where} starts a setInterval/setTimeout in {module} (line {finding['line']}) that is never cleared: "
            f"an open timer keeps Node running, so the test run NEVER ends. To fix: {fix}.")


# --------------------------------------------------------------------------------------------------------------------------
# one file / the block
# --------------------------------------------------------------------------------------------------------------------------
def _outline_lines(path: Path, rel: str, source: str) -> List[str]:
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime, stat.st_size)
    except OSError:
        key = None
    if key and key in _cache:
        return list(_cache[key])
    language = _language(rel)
    if language == "python":
        lines = python_outline(source)
        if lines is None:
            lines = ["does NOT parse as Python right now (read it and fix the syntax first)"]
    elif language == "javascript":
        lines = javascript_outline(source, test_file=is_test_path(rel))
    else:
        lines = []
    if key:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[key] = list(lines)
    return lines


def file_outline(root: Any, rel: str, role: str = "", max_chars: int = MAX_FILE_CHARS) -> str:
    """The code map entry of one workspace file ('' if it is not a code file)."""
    try:
        root = Path(root)
        path = root / rel
        if _language(rel) is None:
            return ""
        source = _read(path)
        if source is None:
            return f"{rel} - {role + ', ' if role else ''}does NOT exist yet" if not path.exists() else ""
        from src.utils.file_authorship import last_writer
        writer = last_writer(path)
        count = source.count("\n") + (0 if source.endswith("\n") else 1)
        head = f"{rel} ({count} lines" + (f", last changed by {writer}" if writer else "") + ")"
        if role:
            head += f" - {role}"
        lines = list(_outline_lines(path, rel, source))
        if is_test_path(rel) and _language(rel) == "javascript":
            problems = [f"PROBLEM: {hint}" for hint in (missing_names_hint(root, rel, source),
                                                        module_internal_hint(root, rel, source)) if hint]
            lines = problems + lines  # first: the outline is cut at max_chars
        text = head + "".join(f"\n  {line}" for line in lines)
        return text if len(text) <= max_chars else text[:max_chars - 4].rstrip() + " ..."
    except Exception as err:
        logger.debug(f"file_outline({rel}) failed: {err}")
        return ""


def task_code_files(root: Any, description: Optional[str], changed: Optional[Iterable[Any]] = None,
                    limit: int = MAX_FILES) -> List[Tuple[str, str]]:
    """
    (rel path, role) of the code files a worker's task is about, most important first: the code files the task names (and
    those changed for it), the code their test files are about, then the project files they load.
    """
    try:
        root = Path(root)
        from src.utils.task_files import file_paths_in_text, find_in_workspace
        chosen: List[Tuple[str, str]] = []

        def add(rel: Optional[str], role: str) -> None:
            if rel and all(rel != existing for existing, _ in chosen) and len(chosen) < limit:
                chosen.append((rel, role))

        own: List[str] = []
        for path in file_paths_in_text(description, keep_framework_notes=True):  # Round 4g: the code a test task checks
            if _language(path) is None:
                continue
            found = find_in_workspace(root, path)
            own.append((_rel(root, found) if found else path) or path)
        for item in changed or []:
            rel = _rel(root, Path(item)) if Path(str(item)).is_absolute() else str(item)
            if rel and _language(rel) and rel not in own and PurePosixPath(rel).name != "conftest.py":
                own.append(rel)
        code_files = _code_files(root)
        subjects: List[str] = []
        loads: List[str] = []
        for rel in own:
            source = _read(root / rel)
            if is_test_path(rel):
                for subject in test_subjects(root, rel, source, code_files):
                    if subject not in subjects:
                        subjects.append(subject)
            if source:
                for target in project_loads(root, rel, source):
                    if target not in loads:
                        loads.append(target)
        for rel in own:
            add(rel, "YOUR task's file")
        for rel in subjects:
            add(rel, "the code your tests check")
        for rel in loads:
            add(rel, "loaded by your task's files")
        return chosen
    except Exception as err:
        logger.debug(f"task_code_files failed: {err}")
        return []


def code_map_text(root: Any, description: Optional[str], changed: Optional[Iterable[Any]] = None,
                  max_chars: int = MAX_BLOCK_CHARS) -> str:
    """The code map for a task in a workspace ('' when the task has no code files)."""
    try:
        entries = [file_outline(root, rel, role) for rel, role in task_code_files(root, description, changed)]
        entries = [entry for entry in entries if entry]
        if not entries:
            return ""
        intro = (f"{CODE_MAP_HEADER}\nYour history may no longer hold these files (it is cleared between tasks and "
                 "summarized while you work). This is their CURRENT interface. A test can use only what a module "
                 "EXPORTS - never its inner variables or internal functions. Before you change a file or write tests for "
                 "it, read it with file_system 'read'; never guess function names, arguments or return values.")
        text = intro
        per_file = max(200, min(MAX_FILE_CHARS, max_chars - len(intro) - 3))
        for number, entry in enumerate(entries):
            if len(entry) > per_file:
                entry = entry[:per_file - 4].rstrip() + " ..."
            if number and len(text) + len(entry) + 3 > max_chars:
                text += "\n- ... (more files: read them with file_system 'read')"
                break
            text += "\n- " + entry
        return text
    except Exception as err:
        logger.debug(f"code_map_text failed: {err}")
        return ""


def code_map_block(manager: Any, agent: Any) -> str:
    """The CODE MAP for a worker in worker_decompose / worker_work / worker_test with a current task ('' otherwise)."""
    try:
        from src.agents.constants import AGENT_TYPE_WORKER, WORKER_STATE_DECOMPOSE, WORKER_STATE_TEST, WORKER_STATE_WORK
        if getattr(agent, "agent_type", None) != AGENT_TYPE_WORKER or getattr(agent, "state", None) not in (
                WORKER_STATE_DECOMPOSE, WORKER_STATE_WORK, WORKER_STATE_TEST):
            return ""
        project_name = getattr(manager, "current_project", None)
        session_name = getattr(manager, "current_session", None)
        if not project_name or not session_name:
            return ""
        from src.config.settings import settings
        from src.tools.sandbox_runner import resolve_shared_workspace
        workspace = resolve_shared_workspace(settings.PROJECTS_BASE_DIR, str(project_name), str(session_name))
        if not Path(workspace).is_dir():
            return ""
        description, task_uuid = _current_task_text(project_name, session_name, agent)
        if not description:
            return ""
        from src.utils.file_authorship import files_changed_for_task
        changed = files_changed_for_task(task_uuid, workspace) if task_uuid else []
        limit = int(getattr(settings, "CODE_MAP_MAX_CHARS", MAX_BLOCK_CHARS))
        if limit <= 0:
            return ""
        return code_map_text(workspace, description, changed, max_chars=max(600, limit))
    except Exception as err:
        logger.debug(f"code_map_block failed: {err}")
        return ""


def _current_task_text(project_name: Any, session_name: Any, agent: Any) -> Tuple[str, Optional[str]]:
    """(description, uuid) of the worker's current task; the injected task text without a task store."""
    try:
        from src.tools.project_management import ProjectManagementTool, TASKLIB_AVAILABLE
        from src.utils.task_utils import get_task_field
        if TASKLIB_AVAILABLE:
            tw = ProjectManagementTool()._get_taskwarrior_instance(str(project_name), str(session_name))
            if tw is not None:
                pending = list(tw.tasks.pending())
                for attr in ("active_task_id", "current_task_id"):
                    wanted = str(getattr(agent, attr, "") or "").strip()
                    if not wanted:
                        continue
                    for task in pending:
                        if str(get_task_field(task, "uuid", "")) == wanted:
                            return str(get_task_field(task, "description", "") or ""), wanted
    except Exception as err:
        logger.debug(f"code map: task lookup failed: {err}")
    return str(getattr(agent, "_injected_task_description", "") or ""), getattr(agent, "current_task_id", None)


CARRIED_DOC_SUFFIXES = frozenset({".md", ".txt", ".rst"})
CARRIED_DOC_MAX_CHARS = 5000
CARRIED_DOCS_MAX_CHARS = 7000


def carried_files_note(history: Optional[Iterable[Dict[str, Any]]]) -> str:
    """
    The note kept when a worker's history is cleared (worker_decompose -> worker_work): the files it read and the files it
    changed successfully before ('' if none). The CODE MAP shows their interface at the next step.
    """
    try:
        from src.agents.tool_activity import _call_arguments, _written_path, is_file_read_call, is_tool_result_failure, \
            read_call_target
        messages = [m for m in history or [] if isinstance(m, dict)]
        results = {m.get("tool_call_id"): (m.get("name"), m.get("content")) for m in messages
                   if m.get("role") == "tool" and m.get("tool_call_id") is not None}
        read: List[str] = []
        written: List[str] = []
        read_text: Dict[str, str] = {}
        for message in messages:
            if message.get("role") != "assistant":
                continue
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict) or call.get("id") not in results:
                    continue
                name, content = results[call["id"]]
                if is_tool_result_failure(name or call.get("name"), content):
                    continue
                path = _written_path(call)
                if path:
                    if path not in written:
                        written.append(path)
                    continue
                if is_file_read_call(call.get("name"), _call_arguments(call)):
                    target = read_call_target(_call_arguments(call))
                    if target not in (".", "") and "." in PurePosixPath(target).name and target not in read:
                        read.append(target)
                    if target and PurePosixPath(target).suffix.lower() in CARRIED_DOC_SUFFIXES and isinstance(content, str):
                        read_text[target] = content  # the last read counts
        if not read and not written:
            return ""
        parts = []
        if written:
            parts.append(f"you WROTE {', '.join(written[:8])} (saved - the file holds that text now)")
        rest = [path for path in read if path not in written]
        if rest:
            parts.append(f"you read {', '.join(rest[:8])}")
        note = ("[Framework Note] Your history before this step was cleared, but nothing you did is lost: " + "; ".join(parts)
                + ". The CODE MAP shows the interface of your task's files; read a file again before you change it.")
        # Round 4b: the plan documents it read come along (2026-10-28 18:30:07 W1's first calls in worker_work read
        # PROJECT_STRUCTURE.md and PROJECT_PLAN.md again - 3 minutes on a slow server - after reading them in worker_decompose)
        kept, budget = [], CARRIED_DOCS_MAX_CHARS
        for path in rest:
            text = read_text.get(path, "").strip()
            if not text or path in written or len(text) > min(budget, CARRIED_DOC_MAX_CHARS):
                continue
            kept.append(f"--- {path} (as you read it) ---\n{text}")
            budget -= len(text)
        if kept:
            note += (" The text of the document(s) you read is below - you do not need to read them again:\n"
                     + "\n".join(kept))
        return note
    except Exception as err:
        logger.debug(f"carried_files_note failed: {err}")
        return ""

# END OF FILE src/utils/code_map.py
