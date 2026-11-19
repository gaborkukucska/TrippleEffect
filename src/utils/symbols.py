# START OF FILE src/utils/symbols.py
"""
Round 5a: functions, classes and methods of a source file BY NAME, with the lines they occupy.

Until Round 5a an agent could change existing code only by position: line numbers from a read (out of date after every
edit that adds or removes a line) or an exact 'search' text (one changed space and it is "not found"), both wrapped in a
JSON array of chunk objects. Rounds 1v-4m added more than thirty repairs for the shapes small models send instead, and a 4B
worker that failed two or three such edits still rewrote the whole file (CURRENT_ISSUES +43: 15-18 KB, minutes per write).
What a small model does well is write ONE complete function. This module finds the lines a named function, class or method
occupies, so that

* `code_editor` action 'replace_function' (filename, function, code) replaces - or adds - exactly that definition, and
* `file_system` action 'read' with 'function' shows only that definition (a 400-line file costs 400 lines of context,
  one function 20).

Python is read with `ast` (exact). JavaScript / TypeScript is read with a brace scanner on the source with strings and
comments blanked: function declarations, `const f = (...) => {`, `const f = function (...) {`, classes and their methods,
object literals assigned at the top level and their methods, `exports.f = ...`, `X.prototype.m = ...`. The result of an
edit is still checked by the tools' own syntax checks, so a wrong range can never be saved as a broken file.
Nothing here raises.
"""
import ast
import difflib
import re
import warnings
from typing import Any, Dict, List, Optional, Tuple

from src.utils.code_map import _blank_strings_and_comments, _matching_brace, _matching_paren

PYTHON_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
MAX_LISTED = 40


class Symbol:
    """A named definition: lines are 1-based and inclusive. `start` is the definition line, `head_start` the first line of
    the decorators (Python) or of the comment block directly above it (JavaScript) - equal to `start` when there is none."""
    __slots__ = ("name", "kind", "start", "end", "head_start", "indent", "parent")

    def __init__(self, name: str, kind: str, start: int, end: int, head_start: Optional[int] = None, indent: str = "",
                 parent: Optional[str] = None):
        self.name, self.kind, self.start, self.end = name, kind, start, end
        self.head_start = head_start if head_start is not None else start
        self.indent, self.parent = indent, parent

    @property
    def short(self) -> str:
        return self.name if self.kind == "test" else self.name.rsplit(".", 1)[-1]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Symbol({self.name!r}, {self.kind}, {self.start}-{self.end})"


def language_of(filename: Any) -> Optional[str]:
    name = str(filename or "").lower()
    if name.endswith(PYTHON_SUFFIXES):
        return "python"
    if name.endswith(JS_SUFFIXES):
        return "javascript"
    return None


def _leading_indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip(" \t"))]


# --------------------------------------------------------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------------------------------------------------------
def python_symbols(source: str) -> Optional[List[Symbol]]:
    """The functions, classes and methods of a Python source (None if it does not parse)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    lines = source.splitlines()
    found: List[Symbol] = []

    def add(node: Any, prefix: str, depth: int) -> None:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        head = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        name = prefix + node.name
        indent = _leading_indent(lines[node.lineno - 1]) if 0 < node.lineno <= len(lines) else ""
        kind = "class" if isinstance(node, ast.ClassDef) else ("method" if prefix else "function")
        found.append(Symbol(name, kind, node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno, head, indent,
                            prefix[:-1] or None))
        if isinstance(node, ast.ClassDef) and depth < 2:
            for child in node.body:
                add(child, name + ".", depth + 1)

    for top in tree.body:
        add(top, "", 0)
    return found


# --------------------------------------------------------------------------------------------------------------------------
# JavaScript / TypeScript
# --------------------------------------------------------------------------------------------------------------------------
_ID = r"[A-Za-z_$][\w$]*"
_JS_FUNCTION_RE = re.compile(rf"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*({_ID})\s*(?=[(<])")
_JS_CLASS_RE = re.compile(rf"^(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+({_ID})\b")
_JS_VAR_RE = re.compile(rf"^(?:export\s+)?(?:const|let|var)\s+({_ID})\s*(?::[^=]+)?=\s*(.*)$")
_JS_ASSIGN_RE = re.compile(rf"^(?:module\.exports|exports|({_ID})\.prototype|({_ID}))\.({_ID})\s*=\s*(.*)$")
_JS_FUNCTION_VALUE_RE = re.compile(rf"^(?:async\s+)?(?:function\b|\(|{_ID}\s*=>|<)")
_JS_MEMBER_RE = re.compile(
    rf"^(?:(?:public|private|protected|static|readonly|override|async|get|set)\s+)*\*?\s*(#?{_ID})\s*(?=[(<])")
_JS_PROPERTY_RE = re.compile(rf"^(?:static\s+)?(#?{_ID})\s*[:=]\s*(.*)$")
_JS_TEST_BLOCK_RE = re.compile(r"(?<![\w.$])(?:test|it|describe|suite)(?:\.(?:only|skip|todo))?\s*\(\s*['\"`]")
_JS_NOT_MEMBERS = frozenset({"if", "for", "while", "switch", "catch", "function", "return", "with", "else", "do", "try",
                             "new", "typeof", "await", "yield", "throw", "delete", "super"})


def _line_starts(text: str) -> List[int]:
    starts, position = [0], text.find("\n")
    while position != -1:
        starts.append(position + 1)
        position = text.find("\n", position + 1)
    return starts


def _line_of(starts: List[int], offset: int) -> int:
    """1-based line of a character offset."""
    low, high = 0, len(starts) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if starts[middle] <= offset:
            low = middle
        else:
            high = middle - 1
    return low + 1


def _body_brace(clean: str, start: int, limit: int) -> int:
    """The index of the '{' that opens the body of the function whose text starts at `start` (-1 if none before limit):
    after the parameter list (which may itself hold `{}` defaults) and an arrow or a return type."""
    index = start
    while index < limit and clean[index] not in "({=":
        index += 1
    if index < limit and clean[index] == "(":
        index = _matching_paren(clean, index) + 1
    arrow = clean.find("=>", index, limit)
    brace = clean.find("{", index, limit)
    if arrow != -1 and (brace == -1 or arrow < brace):
        after = arrow + 2
        while after < limit and clean[after] in " \t\r\n":
            after += 1
        return after if after < limit and clean[after] == "{" else -1
    return brace


def _method_body_brace(clean: str, start: int, limit: int) -> int:
    """
    Round 5f: the '{' that opens a METHOD body (`name(params) {`, `name(params): Type {`), else -1. A call statement
    (`resetGame();`) is no method: 2026-11-15 21:54 W3's replace_function code began with `resetGame();` and was taken for a
    definition of 'resetGame' - the '{' of an object literal three lines further down was read as its body.
    """
    index = start
    while index < limit and clean[index] in " \t":
        index += 1
    if index < limit and clean[index] == "<":  # a type parameter list
        close = clean.find(">", index)
        if close == -1 or close >= limit:
            return -1
        index = close + 1
    if index >= limit or clean[index] != "(":
        return -1
    after = _matching_paren(clean, index) + 1
    while after < limit and clean[after] in " \t\r\n":
        after += 1
    if after < limit and clean[after] == "{":
        return after
    if after < limit and clean[after] == ":":  # a TypeScript return type
        brace = clean.find("{", after, limit)
        if brace != -1 and ";" not in clean[after:brace] and "=" not in clean[after:brace].replace("=>", ""):
            return brace
    return -1


def _statement_end(clean: str, start: int) -> int:
    """The offset of the end of the statement that starts at `start` and has no body block: its ';' at bracket depth 0, or
    the end of the line on which its brackets are balanced."""
    depth = 0
    for index in range(start, len(clean)):
        ch = clean[index]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return index - 1
        elif ch == ";" and depth == 0:
            return index
        elif ch == "\n" and depth == 0:
            following = clean[index + 1:index + 200].lstrip()
            before = clean[max(start, index - 40):index].rstrip()
            if not following.startswith((".", "?", ":", "+", "-", "*", "/", "&", "|", ",", "=")) \
                    and not before.endswith(("=>", "=", "+", "-", "*", "/", "&", "|", ",", "?", ":", "(")):
                return index - 1
    return len(clean) - 1


def _comment_head(lines: List[str], start: int) -> int:
    """The first line of the comment block that ends directly above line `start` (1-based), else `start`."""
    index = start - 2
    if index < 0:
        return start
    text = lines[index].strip()
    if text.startswith("//"):
        while index >= 0 and lines[index].strip().startswith("//"):
            index -= 1
        return index + 2
    if text.endswith("*/"):
        while index >= 0 and "/*" not in lines[index]:
            index -= 1
        return index + 1 if index >= 0 else start
    return start


def javascript_symbols(source: str) -> List[Symbol]:
    """The functions, classes, class methods, top-level object literals and their methods of a JavaScript source."""
    try:
        clean = _blank_strings_and_comments(source)
    except Exception:
        return []
    starts = _line_starts(clean)
    lines = source.split("\n")
    clean_lines = clean.split("\n")
    found: List[Symbol] = []

    def end_line_of(close: int) -> int:
        return _line_of(starts, min(close, len(clean) - 1))

    def symbol(name: str, kind: str, number: int, close: int, parent: Optional[str] = None) -> Symbol:
        item = Symbol(name, kind, number, max(number, end_line_of(close)), _comment_head(lines, number),
                      _leading_indent(lines[number - 1]) if number <= len(lines) else "", parent)
        found.append(item)
        return item

    def members(owner: str, open_at: int, close_at: int) -> None:
        """Methods and function-valued properties directly inside the block clean[open_at:close_at]."""
        depth = 0
        first = _line_of(starts, open_at)
        last = _line_of(starts, close_at)
        for number in range(first, last + 1):
            begin = starts[number - 1]
            text = clean_lines[number - 1]
            if number == first:
                text = " " * (open_at + 1 - begin) + text[open_at + 1 - begin:]
            if number == last:
                text = text[:max(0, close_at - begin)]
            stripped = text.strip()
            if depth == 0 and stripped:
                at = begin + len(text) - len(text.lstrip())
                match = _JS_MEMBER_RE.match(stripped)
                name = match.group(1) if match else None
                if name and name not in _JS_NOT_MEMBERS:
                    brace = _method_body_brace(clean, at + match.end(), close_at)  # Round 5f: not _body_brace
                    if brace != -1 and _line_of(starts, brace) <= number + 6:
                        symbol(f"{owner}.{name}", "method", number, _matching_brace(clean, brace), owner)
                else:
                    prop = _JS_PROPERTY_RE.match(stripped)
                    if prop and prop.group(1) not in _JS_NOT_MEMBERS and _JS_FUNCTION_VALUE_RE.match(prop.group(2).strip()):
                        value_at = at + prop.start(2)
                        brace = _body_brace(clean, value_at, close_at)
                        if brace != -1 and _line_of(starts, brace) <= number + 6:
                            symbol(f"{owner}.{prop.group(1)}", "method", number, _matching_brace(clean, brace), owner)
                        else:
                            symbol(f"{owner}.{prop.group(1)}", "method", number,
                                   min(_statement_end(clean, value_at), close_at - 1), owner)
            depth = max(0, depth + text.count("{") - text.count("}"))

    # Round 5c: test blocks by their title - test('wraps around the grid', () => { ... }) - at any depth
    for match in _JS_TEST_BLOCK_RE.finditer(clean):
        quote_at = match.end() - 1
        closing = clean.find(clean[quote_at], quote_at + 1)
        open_paren = clean.find("(", match.start())
        if closing == -1 or open_paren == -1:
            continue
        title = source[quote_at + 1:closing].strip()
        close_paren = _matching_paren(clean, open_paren)
        if not title or close_paren <= open_paren:
            continue
        tail = close_paren + 1 if clean[close_paren + 1:close_paren + 2] == ";" else close_paren
        number = _line_of(starts, match.start())
        if clean_lines[number - 1][:match.start() - starts[number - 1]].strip():
            continue  # not the start of a statement
        found.append(Symbol(title, "test", number, max(number, end_line_of(tail)), _comment_head(lines, number),
                            _leading_indent(lines[number - 1])))

    depth = 0
    paren = 0
    for number, text in enumerate(clean_lines, start=1):
        stripped = text.strip()
        if depth == 0 and paren == 0 and stripped:
            at = starts[number - 1] + len(text) - len(text.lstrip())
            function = _JS_FUNCTION_RE.match(stripped)
            klass = _JS_CLASS_RE.match(stripped)
            variable = _JS_VAR_RE.match(stripped)
            assigned = _JS_ASSIGN_RE.match(stripped)
            if function:
                brace = _body_brace(clean, at + function.end(), len(clean))
                if brace != -1:
                    symbol(function.group(1), "function", number, _matching_brace(clean, brace))
            elif klass:
                brace = clean.find("{", at + klass.end())
                if brace != -1:
                    close = _matching_brace(clean, brace)
                    symbol(klass.group(1), "class", number, close)
                    members(klass.group(1), brace, close)
            elif variable or assigned:
                if variable:
                    name, value, value_at = variable.group(1), variable.group(2).strip(), at + variable.start(2)
                    owner = None
                else:
                    owner = assigned.group(1) or assigned.group(2)
                    name, value, value_at = assigned.group(3), assigned.group(4).strip(), at + assigned.start(4)
                    if assigned.group(2) and not _JS_FUNCTION_VALUE_RE.match(value):
                        name = None  # `state.score = 0`: a plain assignment, not a definition
                full = f"{owner}.{name}" if owner and name else name
                if not name:
                    pass
                elif _JS_FUNCTION_VALUE_RE.match(value):
                    brace = _body_brace(clean, value_at, len(clean))
                    if brace != -1:
                        close = _matching_brace(clean, brace)
                        tail = _statement_end(clean, close + 1) if clean[close + 1:close + 2] in (";", ")") else close
                        symbol(full, "method" if owner else "function", number, tail, owner)
                    else:
                        symbol(full, "method" if owner else "function", number, _statement_end(clean, value_at), owner)
                elif value.startswith("{") and variable:
                    close = _matching_brace(clean, value_at)
                    tail = close + 1 if clean[close + 1:close + 2] == ";" else close
                    symbol(name, "object", number, tail)
                    members(name, value_at, close)
                elif value.startswith("class") and variable:
                    brace = clean.find("{", value_at)
                    if brace != -1:
                        close = _matching_brace(clean, brace)
                        symbol(name, "class", number, close)
                        members(name, brace, close)
        depth = max(0, depth + text.count("{") - text.count("}"))
        paren = max(0, paren + text.count("(") - text.count(")"))
    return found


def find_symbols(source: str, filename: Any) -> Optional[List[Symbol]]:
    """The named definitions of a source file: None for a language this module does not read or a Python file that does
    not parse."""
    language = language_of(filename)
    try:
        if language == "python":
            return python_symbols(source)
        if language == "javascript":
            return javascript_symbols(source)
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------------------------------------------------------
# lookup by the name an agent sends
# --------------------------------------------------------------------------------------------------------------------------
def clean_name(name: Any) -> str:
    """'def update(self):' / 'Game.update()' / 'function update' / 'Game::update' / 'Game#update' -> 'update' / 'Game.update'."""
    text = str(name or "").strip().strip("`'\"")
    text = re.sub(r"^(?:async\s+)?(?:def|function|class|method|const|let|var)\s+", "", text)
    text = re.sub(r"\(.*$", "", text, flags=re.S).rstrip(":{ \t")
    text = text.replace("::", ".").replace("#", ".").replace(" ", "")
    text = re.sub(r"\.prototype\.", ".", text)
    return text.strip(".")


def _loose(name: str) -> str:
    return re.sub(r"[_$]", "", name).lower()


# Round 5g: a test block named by its call head. 2026-11-16 21:26:00 W2 (qwen3.5:4b) sent replace_function with
# function="describe('Client-side Game Rendering and Input Handling'" and the whole suite as code: no block had that
# "title", so the suite was ADDED after the last test - inside the last describe() - and three more calls nested the file
# into itself four times (tests/snake/snake.test.js grew to 2,479 lines / 99 KB).
_TEST_CALL_HEAD_RE = re.compile(r"^\s*(?:test|it|describe|suite)(?:\.(?:only|skip|todo))?\s*\(\s*(['\"`])(.*?)(?:\1|$)", re.S)


def block_title(name: Any) -> str:
    """The title of a test block an agent names: "describe('Snake class'" / 'it("moves", () =>' / "'moves'" -> the title."""
    text = str(name or "").strip()
    head = _TEST_CALL_HEAD_RE.match(text)
    if head and head.group(2).strip():
        return head.group(2).strip()
    return text.strip("`'\"").strip()


def lookup(symbols: List[Symbol], name: Any) -> Tuple[Optional[Symbol], List[Symbol]]:
    """
    (the one symbol the name means, the candidates when it is ambiguous). A full name ('Game.update') must match exactly; a
    bare name ('update') matches a top-level definition first, else the only method of that name.
    """
    title = block_title(name)  # Round 5g: also "describe('title'"
    titled = [s for s in symbols if s.kind == "test" and s.name == title]
    if titled:
        return (titled[0], []) if len(titled) == 1 else (None, titled)
    wanted = clean_name(name)
    if not wanted:
        return None, []
    exact = [s for s in symbols if s.name == wanted]
    if exact:
        return exact[0], ([] if len(exact) == 1 else exact)
    if "." not in wanted:
        methods = [s for s in symbols if s.short == wanted]
        if len(methods) == 1:
            return methods[0], []
        if methods:
            return None, methods
    else:
        # 'module.update' / 'self.update' / 'this.update': an owner that is no class of the file
        owner, short = wanted.rsplit(".", 1)
        if not any(s.name == owner for s in symbols):
            return lookup(symbols, short)
    return None, []


def close_names(symbols: List[Symbol], name: Any, limit: int = 3) -> List[str]:
    """Names of the file that the given name probably means (another spelling, case, or underscores)."""
    wanted = clean_name(name)
    short = wanted.rsplit(".", 1)[-1]
    loose = [s.name for s in symbols if _loose(s.short) == _loose(short)]
    if loose:
        return loose[:limit]
    names = sorted({s.short for s in symbols})
    near = difflib.get_close_matches(short, names, n=limit, cutoff=0.8)
    return [s.name for s in symbols if s.short in near][:limit]


def names_text(symbols: List[Symbol], limit: int = MAX_LISTED) -> str:
    """'Game (class, lines 3-80), Game.update (lines 10-24), helper (lines 82-90)'."""
    shown = [f"{s.name} ({'class, ' if s.kind in ('class', 'object') else ''}lines {s.start}-{s.end})" for s in symbols[:limit]]
    return ", ".join(shown) + (f", ... ({len(symbols) - limit} more)" if len(symbols) > limit else "")


# --------------------------------------------------------------------------------------------------------------------------
# the new text of a definition
# --------------------------------------------------------------------------------------------------------------------------
def strip_code_fence(code: str) -> str:
    """The text without a surrounding ``` fence."""
    text = str(code or "")
    match = re.match(r"^\s*```[\w+-]*[ \t]*\n(.*?)\n?```\s*$", text, re.S)
    return match.group(1) if match else text


_HEAD_LINE_RE = re.compile(r"^\s*(?://|/\*|\*|#|@)")


def reindent(code: str, indent: str) -> str:
    """
    The code with its DEFINITION line at `indent` and every other line moved by the same amount (blank lines stay empty).
    The definition line is the first line that is no comment and no decorator: 2026-11-11 a model sent the comment above
    `move() {` at column 0 and the method at column 4 - measured from the comment, every replacement pushed the method
    4 columns further right (26 after six rounds).
    """
    lines = code.expandtabs(4).split("\n") if "\t" in code and " " in indent else code.split("\n")
    first = next((line for line in lines if line.strip() and not _HEAD_LINE_RE.match(line)),
                 next((line for line in lines if line.strip()), ""))
    current = _leading_indent(first)
    if current == indent:
        return "\n".join(lines)
    out = []
    for line in lines:
        if not line.strip():
            out.append("")
        elif line.startswith(current):
            out.append(indent + line[len(current):])
        else:
            out.append(indent + line.lstrip(" \t"))  # less indented than the first line: keep it inside the block
    return "\n".join(out)


def home_indent(symbols: List[Symbol], symbol: Symbol) -> str:
    """The indentation the definition belongs at: that of most of its siblings (other members of its class or object, other
    top-level definitions), else its own. Repairs a definition that an earlier edit left at a wrong column."""
    if symbol.kind == "test":
        return symbol.indent
    siblings = [s.indent for s in symbols if s is not symbol and s.parent == symbol.parent and s.kind != "test"]
    if not siblings:
        return symbol.indent
    return max(set(siblings), key=lambda value: (siblings.count(value), -len(value)))


def defined_names(code: str, filename: Any) -> Optional[List[Symbol]]:
    """The definitions the new text itself holds (it is parsed alone, at indentation 0): None when it cannot be read."""
    language = language_of(filename)
    text = reindent(strip_code_fence(code), "")
    if language == "python":
        return python_symbols(text)
    if language == "javascript":
        symbols = javascript_symbols(text)
        if symbols:
            return symbols
        # a class method or an object property sent alone ('update(dt) { ... }') is no statement of its own
        wrapped = javascript_symbols("class __owner__ {\n" + text + "\n}")
        return [Symbol(s.short, s.kind, s.start - 1, s.end - 1, s.head_start - 1, s.indent) for s in wrapped
                if s.parent == "__owner__"]
    return None


def replacement_range(symbol: Symbol, code: str, filename: Any) -> Tuple[int, int]:
    """
    The lines (1-based, inclusive) the new text replaces. The decorators of a Python definition and the comment block
    above a JavaScript definition are replaced only when the new text brings its own; otherwise they stay.
    """
    first = next((line.strip() for line in strip_code_fence(code).split("\n") if line.strip()), "")
    if symbol.head_start < symbol.start:
        brings_head = first.startswith("@") if language_of(filename) == "python" else first.startswith(("//", "/*"))
        if brings_head:
            return symbol.head_start, symbol.end
    return symbol.start, symbol.end


def insertion_line(symbols: List[Symbol], name: Any, total_lines: int, python: bool = False,
                   as_test: bool = False, group: bool = False) -> Tuple[int, str, str]:
    """
    Where a definition that does not exist yet goes: (the line AFTER which it is inserted, its indentation, what that
    place is). 'Game.reset' goes after the last method of Game; a top-level name after the last top-level definition, else
    at the end of the file.
    Round 5g: a new test GROUP (a describe() that holds tests of its own) goes after the last outermost test block, at its
    column - never into the innermost block, where a whole suite was nested into itself.
    """
    wanted = clean_name(name)
    if as_test:
        tests = [s for s in symbols if s.kind == "test"]
        if group:
            roots = [s for s in tests if not any(o is not s and o.start <= s.start and o.end >= s.end for o in tests)]
            if roots:
                last = max(roots, key=lambda s: s.end)
                return last.end, last.indent, f"after the test block '{last.name}'"
            return total_lines, "", "at the end of the file"
        leaves = [s for s in tests if not any(o is not s and o.start >= s.start and o.end <= s.end for o in tests)]
        if leaves:
            last = max(leaves, key=lambda s: s.end)
            return last.end, last.indent, f"after the test '{last.name}'"
        return total_lines, "", "at the end of the file"
    if "." in wanted:
        owner = wanted.rsplit(".", 1)[0]
        own = [s for s in symbols if s.parent == owner]
        if own:
            last = max(own, key=lambda s: s.end)
            return last.end, last.indent, f"after '{last.name}'"
        holder = next((s for s in symbols if s.name == owner and s.kind in ("class", "object")), None)
        if holder is not None:
            inner = holder.indent + "    "
            if python:
                return holder.end, inner, f"at the end of class '{owner}'"
            if holder.end > holder.start:
                return holder.end - 1, inner, f"inside '{owner}'"
    top = [s for s in symbols if s.parent is None and s.kind != "test"]
    if top:
        last = max(top, key=lambda s: s.end)
        return last.end, "", f"after '{last.name}'"
    return total_lines, "", "at the end of the file"


# ---------------------------------------------------------------------------------------------------- Round 5e
# The parts of a file that have no name: its imports and its start block. A 4B model asked for them by the names it
# gave them (2026-11-11 17:31/17:34: function='__main__' and 'main' with an `if __name__ == '__main__':` block as code;
# 2026-11-11 09:20: function='imports'), and its line edits for them failed 17 times of 28.
IMPORT_BLOCK_NAMES = frozenset({"imports", "import", "theimports", "importstatements", "importblock", "requires", "header"})
START_BLOCK_NAMES = frozenset({"main", "__main__", "mainblock", "ifmain", "if__name__", "startblock", "entrypoint",
                               "if__name__==__main__", "startserver", "runserver"})
_PY_IMPORT_LINE_RE = re.compile(r"^(?:import\s+[\w.]+|from\s+[\w.]+\s+import\s+.+)")
_PY_FROM_RE = re.compile(r"^from\s+([\w.]+)\s+import\s+(.+)$")
_JS_IMPORT_LINE_RE = re.compile(
    r"^(?:(?:const|let|var)\s+[\w${}\s,:]+=\s*require\s*\(\s*['\"][^'\"]+['\"]\s*\)(?:\.\w+)?\s*;?"
    r"|import\s.+?from\s*['\"][^'\"]+['\"]\s*;?|import\s*['\"][^'\"]+['\"]\s*;?)$")
_JS_SOURCE_RE = re.compile(r"(?:require\s*\(|from)\s*['\"]([^'\"]+)['\"]")
_PY_START_RE = re.compile(r"^if\s+__name__\s*==\s*['\"]__main__['\"]\s*:")
_JS_START_RE = re.compile(r"^if\s*\(\s*require\.main\s*===?\s*module\s*\)")
_COMMENT_LINE_RE = re.compile(r"^\s*(?:#|//)")


def _code_lines(code: str) -> List[str]:
    return [line for line in code.split("\n") if line.strip() and not _COMMENT_LINE_RE.match(line)]


def _block_name(name: Any) -> str:
    return re.sub(r"[\s`'\"():]", "", str(name or "")).lower()


def _joined_statements(lines: List[str]) -> List[Tuple[int, int, str]]:
    """Top-level statements that may span lines in brackets: (first line, last line, text on one line), 1-based."""
    out, index = [], 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line[:1] in " \t":
            index += 1
            continue
        first, text = index, line.rstrip()
        depth = text.count("(") - text.count(")") + text.count("{") - text.count("}")
        while depth > 0 and index + 1 < len(lines) and index - first < 40:
            index += 1
            text += " " + lines[index].strip()
            depth += lines[index].count("(") - lines[index].count(")") + lines[index].count("{") - lines[index].count("}")
        out.append((first + 1, index + 1, re.sub(r"\(\s+", "(", re.sub(r",?\s+\)", ")", text))))
        index += 1
    return out


_PY_FROM_NAMES_RE = re.compile(r"^from\s+([\w.]+)\s+import\s+\(?\s*(.+?)\s*,?\s*\)?\s*$")
_JS_DESTRUCTURED_RE = re.compile(r"^(const|let|var)\s*\{\s*([^}]*)\}\s*=\s*(require\s*\(.+\))\s*;?\s*$")
_JS_NAMED_IMPORT_RE = re.compile(r"^import\s*\{\s*([^}]*)\}\s*from\s*(['\"][^'\"]+['\"])\s*;?\s*$")


def _name_set(text: str) -> set:
    return {" ".join(part.split()) for part in text.split(",") if part.strip()}


def _union_names(old: str, new: str) -> Optional[str]:
    """Both lines' names, or None when the new line only leaves names out (a correction: 2026-11-11 the model dropped the
    `WebSocket` that flask_socketio does not have)."""
    if _name_set(new) <= _name_set(old):
        return None
    names: List[str] = []
    for part in old.split(",") + new.split(","):
        part = " ".join(part.split())
        if part and part not in names:
            names.append(part)
    return ", ".join(names)


def _union_import(old: str, new: str, python: bool) -> Optional[str]:
    """
    Round 5f: one import line with the names of both lines of the same module (`from m import a` + `from m import b` ->
    `from m import a, b`; `const { a } = require('m')` + `const { b } = require('m')` -> `const { a, b } = require('m')`),
    else None. The file's names were lost when a line of 'code' that named fewer of them replaced its line.
    """
    if python:
        a, b = _PY_FROM_NAMES_RE.match(old.strip()), _PY_FROM_NAMES_RE.match(new.strip())
        if a and b and a.group(1) == b.group(1) and "*" not in a.group(2) + b.group(2):
            names = _union_names(a.group(2), b.group(2))
            return f"from {b.group(1)} import {names}" if names else None
        return None
    a, b = _JS_DESTRUCTURED_RE.match(old.strip()), _JS_DESTRUCTURED_RE.match(new.strip())
    if a and b:
        names = _union_names(a.group(2), b.group(2))
        return f"{b.group(1)} {{ {names} }} = {b.group(3).rstrip(';')};" if names else None
    a, b = _JS_NAMED_IMPORT_RE.match(old.strip()), _JS_NAMED_IMPORT_RE.match(new.strip())
    if a and b:
        names = _union_names(a.group(1), b.group(1))
        return f"import {{ {names} }} from {b.group(2)};" if names else None
    return None


def _import_key(statement: str, python: bool) -> Optional[str]:
    if python:
        match = _PY_FROM_RE.match(statement)
        if match:
            return "from " + match.group(1)
        return re.sub(r"\s+", " ", statement.split(" as ")[0]).strip() if statement.startswith("import ") else None
    match = _JS_SOURCE_RE.search(statement)
    if not match:
        return None
    module = match.group(1)
    return module[5:] if module.startswith("node:") and module[5:] not in ("test",) else module  # Round 5f: 'node:assert' = 'assert'


def _without_unguarded_calls(lines: List[str], filename: Any, block_text: str, op: Tuple[int, int, str],
                             what: str) -> Tuple[List[Dict[str, Any]], str]:
    """Round 5m: the start block's chunk - and when the file also makes the block's calls unguarded at its top level, ONE
    chunk from the first such call to the end of the file without them (see unguarded_start_calls)."""
    first_line, last_line, replace = op
    chunk = [{"start_line": first_line, "end_line": last_line, "replace": replace}]
    try:
        found = [(a, b, call) for a, b, call in unguarded_start_calls("\n".join(lines), filename, block_text)
                 if b < first_line or a > last_line]
        if not found:
            return chunk, what
        ops = [(first_line, last_line, replace.split("\n"))]
        notes = []
        for a, b, call in found:
            shown = f"line {b}" if a == b else f"lines {a}-{b}"
            if b < len(lines) and not lines[b].strip() and (a == 1 or not lines[a - 2].strip()):
                b += 1
            ops.append((a, b, []))
            notes.append(f"the bare `{call}` ({shown}) was REMOVED: it ran whenever the file was loaded (also by the "
                         "tests); the start block makes that call now")
        new_lines = _apply_line_ops(lines, ops)
        if new_lines is None:
            return chunk, what
        start = min(op_[0] for op_ in ops)
        return ([{"start_line": start, "end_line": len(lines), "replace": "\n".join(new_lines[start - 1:])}],
                what + "; " + "; ".join(notes))
    except Exception:
        return chunk, what


def file_block_edit(original: str, filename: Any, name: Any, code: str,
                    known_names: Optional[List[str]] = None) -> Optional[Tuple[List[Dict[str, Any]], str]]:
    """
    Round 5e: 'code' that is the file's start block or import lines -> (line-range chunks, what was done), else None.

    * start block (`if __name__ == '__main__':` / `if (require.main === module) {`): replaces the file's block, or is added
      at the end of the file.
    * import lines: MERGED into the file's imports - a line the file has stays, a line for a module the file imports
      already replaces that line (`from flask import Flask` -> `from flask import Flask, jsonify`), a new one is added
      after the last import. Imports that are not in 'code' are kept, so a partial list loses nothing.
    A name that IS a definition of the file (a function called main) is left to replace_function.
    """
    language = language_of(filename)
    body = _code_lines(code or "")
    if language is None or not body:
        return None
    python = language == "python"
    lines = original.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    wanted = _block_name(name)
    start_re = _PY_START_RE if python else _JS_START_RE
    import_re = _PY_IMPORT_LINE_RE if python else _JS_IMPORT_LINE_RE
    statements = _joined_statements(code.split("\n"))
    code_statements = [text for _first, _last, text in statements if not _COMMENT_LINE_RE.match(text)]

    if start_re.match(body[0]) and not body[0][:1].isspace():
        first_code = next(i for i, line in enumerate(code.split("\n")) if line.strip() and start_re.match(line))
        new_text = "\n".join(code.split("\n")[first_code:]).rstrip()
        start = next((i for i, line in enumerate(lines) if start_re.match(line)), None)
        if start is None:
            if not lines:
                return [{"start_line": 1, "end_line": 0, "replace": new_text + "\n"}], "the start block was ADDED"
            last = len(lines)
            while last > 1 and not lines[last - 1].strip():
                last -= 1
            return _without_unguarded_calls(lines, filename, new_text,
                                            (last, len(lines), lines[last - 1] + "\n\n\n" + new_text),
                                            "the file had no start block, so yours was ADDED at its end")
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][:1] in " \t" or (not python and lines[end].startswith("}"))):
            end += 1
            if not python and lines[end - 1].startswith("}"):
                break
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        return _without_unguarded_calls(lines, filename, new_text, (start + 1, end, new_text),
                                        f"the start block (was lines {start + 1}-{end}) now holds your code")

    if not code_statements or not all(import_re.match(text) for text in code_statements):
        return None
    if wanted not in IMPORT_BLOCK_NAMES and wanted in {re.sub(r"[_$]", "", n).lower() for n in (known_names or [])}:
        return None
    file_statements = [(first, last, text) for first, last, text in _joined_statements(lines) if import_re.match(text)]
    by_key: Dict[str, Tuple[int, int, str]] = {}
    for first, last, text in file_statements:
        key = _import_key(text, python)
        if key and key not in by_key:
            by_key[key] = (first, last, text)
    chunks, added, changed, kept = [], [], [], 0
    present = {re.sub(r"\s+", " ", text).rstrip(";") for _f, _l, text in file_statements}
    for text in code_statements:
        normal = re.sub(r"\s+", " ", text).rstrip(";")
        if normal in present:
            kept += 1
            continue
        key = _import_key(text, python)
        if key and key in by_key and not any(c["start_line"] == by_key[key][0] for c in chunks):
            first, last, old_text = by_key[key]
            text = _union_import(old_text, text, python) or text  # Round 5f: no name of the file's line is lost
            if re.sub(r"\s+", " ", text).rstrip(";") == re.sub(r"\s+", " ", old_text).rstrip(";"):
                kept += 1
                continue
            chunks.append({"start_line": first, "end_line": last, "replace": text})
            changed.append(text)
        else:
            added.append(text)
        present.add(normal)
    if added:
        if file_statements:
            anchor = max(last for _first, last, _text in file_statements)
            existing = next((c for c in chunks if c["end_line"] == anchor), None)
            if existing:
                existing["replace"] += "\n" + "\n".join(added)
            else:
                chunks.append({"start_line": anchor, "end_line": anchor,
                               "replace": "\n".join(lines[anchor - 1:anchor] + added)})
        else:
            anchor = 0
            if python and lines:
                try:
                    tree = ast.parse(original)
                    if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(getattr(tree.body[0], "value", None), ast.Constant):
                        anchor = tree.body[0].end_lineno or 0
                except SyntaxError:
                    anchor = 0
            elif lines and lines[0].startswith("#!"):
                anchor = 1
            if anchor:
                chunks.append({"start_line": anchor, "end_line": anchor,
                               "replace": lines[anchor - 1] + "\n\n" + "\n".join(added)})
            elif lines:
                chunks.append({"start_line": 1, "end_line": 1, "replace": "\n".join(added) + "\n\n" + lines[0]})
            else:
                chunks.append({"start_line": 1, "end_line": 0, "replace": "\n".join(added) + "\n"})
    if not chunks:
        return [], f"every one of these {kept} import line(s) is in the file already"
    parts = ([f"{len(changed)} import line(s) replaced ({'; '.join(changed)[:160]})"] if changed else []) + \
            ([f"{len(added)} added ({'; '.join(added)[:160]})"] if added else []) + \
            ([f"{kept} already there"] if kept else [])
    return sorted(chunks, key=lambda c: c["start_line"]), "imports: " + ", ".join(parts) + "; every other import of the file is kept"


# ---------------------------------------------------------------------------------------------------- Round 5g
# The exports of a JavaScript file by their name. 2026-11-16 (qwen3.5:4b, WiFi_Snake_Multiplayer_Game) W2 sent
# replace_function function='module.exports' with a complete `module.exports = { ... };` 16 times (public/client.js,
# src/server.js) and was told each time that its code "defines nothing" and to use the line tools; the line edits that
# followed left src/server.js with FIVE module.exports blocks, and PM1 made three fix tasks to remove them.
EXPORT_BLOCK_NAMES = frozenset({"module.exports", "moduleexports", "exports", "export", "theexports", "exportsblock",
                                "moduleexportsblock", "exportblock"})
_EXPORT_GUARD_RE = re.compile(r"^if\s*\(\s*typeof\s+(?:module|exports)\s*!==?\s*['\"]undefined['\"][^)]*\)\s*")
_EXPORT_OBJECT_START_RE = re.compile(r"^module\.exports\s*=\s*\{")
_EXPORT_MEMBER_RE = re.compile(rf"^(?:module\.)?exports\.({_ID})\s*=\s*(.+?)\s*;?\s*$", re.S)


def _exports_statements(lines: List[str]) -> Optional[List[Tuple[int, int, str, bool]]]:
    """
    The top-level exports statements of JavaScript lines: [(first, last (1-based), the statement without a guard, guarded)]
    for `module.exports = ...`, `module.exports.x = ...` / `exports.x = ...` and the same inside an
    `if (typeof module !== 'undefined') { ... }` guard. None when the lines cannot be read.
    """
    items = _javascript_items("\n".join(lines))
    if items is None:
        return None
    found: List[Tuple[int, int, str, bool]] = []
    for item in items:
        text = "\n".join(lines[item.start - 1:item.end]).strip()
        if item.kind == "exports":
            found.append((item.start, item.end, text, False))
            continue
        guard = _EXPORT_GUARD_RE.match(text)
        if not guard:
            continue
        inner = text[guard.end():].strip()
        if inner.startswith("{") and inner.rstrip(";").endswith("}"):
            inner = inner[1:inner.rstrip(";").rfind("}")].strip()
        if inner and _JS_EXPORTS_RE.match(inner):
            found.append((item.start, item.end, inner, True))
    return found


def _object_entries(statement: str) -> Optional[List[Tuple[str, str]]]:
    """(key, entry) of `module.exports = { ... }` - the entries' own line breaks and inner indentation kept - else None."""
    match = _EXPORTS_OBJECT_RE.match(statement.strip())
    if not match:
        return None
    # whole-line comments between the entries ('// Export getters for tests') are no entries
    body_lines, in_block = [], False
    for line in match.group(2).split("\n"):
        stripped = line.strip()
        if in_block:
            in_block = "*/" not in stripped
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped
            continue
        if not stripped.startswith("//"):
            body_lines.append(line)
    body = "\n".join(body_lines)
    entries = _export_entries(body)
    if entries is None:
        return None
    return [(key, _dedent_entry(text)) for key, text in entries]


def _dedent_entry(text: str) -> str:
    """An entry whose first line was stripped: its later lines moved left by the indentation of its last line ('}')."""
    lines = text.strip().split("\n")
    if len(lines) == 1:
        return lines[0]
    tail = _leading_indent(lines[-1]) if lines[-1].strip() else ""
    out = [lines[0]]
    for line in lines[1:]:
        out.append(line[len(tail):] if line.startswith(tail) else line.lstrip())
    return "\n".join(out)


def exports_block_edit(original: str, filename: Any, name: Any, code: str, known_names: Optional[List[str]] = None,
                       browser_script: bool = False) -> Optional[Tuple[List[Dict[str, Any]], str]]:
    """
    Round 5g: 'code' that is only the exports of a JavaScript file (`module.exports = { ... }`, `module.exports.x = y`,
    optionally inside `if (typeof module !== 'undefined') { ... }`) -> (line-range chunks, what was done), else None.

    * every `module.exports = { ... }` of the file (a copy that is there twice goes too) becomes ONE object: the file's
      names first, the names of 'code' replacing / added to them, in place of the LAST of them;
      `module.exports.x = ...` lines of the file whose name the object now holds are removed;
    * a file without exports gets them at its end (before its start block `if (require.main === module)`);
    * a file the page loads in the browser (browser_script) - or whose exports were guarded already - gets them inside
      `if (typeof module !== 'undefined') { ... }`: a browser has no `module` and would stop at that line.
    A name that IS a definition of the file is left to replace_function. A file whose exports object is not a plain list of
    names gets the object of 'code' in its place.
    """
    if language_of(filename) != "javascript":
        return None
    wanted = _block_name(name)
    if wanted and wanted not in EXPORT_BLOCK_NAMES and wanted in {re.sub(r"[_$]", "", n).lower() for n in (known_names or [])}:
        return None
    text = reindent(strip_code_fence(code or ""), "").strip("\n")
    code_lines = text.split("\n")
    code_statements = _exports_statements(code_lines)
    code_items = _javascript_items(text)
    if not code_statements or code_items is None:
        return None
    lines = original.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    # other statements of 'code' only when the file holds them as they are (`process.on('exit', cleanup);` sent along)
    exports_spans = {(first, last) for first, last, _s, _g in code_statements}
    file_texts = {_normal("\n".join(lines[i.start - 1:i.end])) for i in (_javascript_items("\n".join(lines)) or [])}
    dropped = 0
    for item in code_items:
        if (item.start, item.end) in exports_spans:
            continue
        if _normal("\n".join(code_lines[item.start - 1:item.end])) in file_texts:
            dropped += 1
            continue
        return None  # 'code' holds more than the exports
    objects = [s for s in code_statements if _EXPORT_OBJECT_START_RE.match(s[2])]
    members = [s for s in code_statements if not _EXPORT_OBJECT_START_RE.match(s[2])]
    if len(objects) > 1:
        return None
    new_entries: List[Tuple[str, str]] = []
    if objects:
        parsed = _object_entries(objects[0][2])
        if parsed is None:
            return None
        new_entries = parsed
    for _first, _last, statement, _guarded in members:
        member = _EXPORT_MEMBER_RE.match(statement)
        if not member:
            return None
        key, value = member.group(1), member.group(2).strip()
        entry = key if value == key else f"{key}: {value}"
        if any(k == key for k, _t in new_entries):
            new_entries = [(k, entry if k == key else t) for k, t in new_entries]
        else:
            new_entries.append((key, entry))
    if not new_entries:
        return None
    code_indent = "    "
    if objects:
        second = objects[0][2].split("\n")[1:2]
        if second and second[0].strip() and _leading_indent(second[0]):
            code_indent = _leading_indent(second[0])

    file_statements = _exports_statements(lines) if lines else []
    if file_statements is None:
        return None
    file_objects = [s for s in file_statements if _EXPORT_OBJECT_START_RE.match(s[2])]
    merged: List[Tuple[str, str]] = []
    replaced_whole = False
    for _first, _last, statement, _guarded in file_objects:
        entries = _object_entries(statement)
        if entries is None:
            merged, replaced_whole = [], True
            break
        for key, entry in entries:  # first place of a name, its last text
            if any(k == key for k, _t in merged):
                merged = [(k, entry if k == key else t) for k, t in merged]
            else:
                merged.append((key, entry))
    new_by_key = dict(new_entries)
    merged = [(key, new_by_key.pop(key, entry)) for key, entry in merged]
    merged += [(key, entry) for key, entry in new_entries if key in new_by_key]
    keys = {key for key, _entry in merged}
    guarded = browser_script or any(s[3] for s in code_statements) or any(s[3] for s in file_statements)

    body = ["module.exports = {"]
    for number, (_key, entry) in enumerate(merged):
        entry_lines = entry.split("\n")
        comma = "," if number < len(merged) - 1 else ""
        body += [code_indent + line if line.strip() else "" for line in entry_lines[:-1]]
        body.append(code_indent + entry_lines[-1] + comma)
    body.append("};")
    if guarded:
        body = ["if (typeof module !== 'undefined') {"] + [("    " + line) if line else "" for line in body] + ["}"]

    # the file's lines that go: every exports object and the `module.exports.x = ...` lines the object now holds
    removed: List[Tuple[int, int]] = []
    for first, last, statement, _guarded in file_statements:
        member = _EXPORT_MEMBER_RE.match(statement)
        if _EXPORT_OBJECT_START_RE.match(statement) or (member and member.group(1) in keys):
            removed.append((first, last))
    ops: List[Tuple[int, int, List[str]]] = []
    if removed:
        object_places = [(first, last) for first, last, statement, _g in file_statements
                         if _EXPORT_OBJECT_START_RE.match(statement)]
        place = max(object_places) if object_places else max(removed)
        ops.append((place[0], place[1], body))
        for first, last in removed:
            if (first, last) == place:
                continue
            if last < len(lines) and not lines[last].strip() and (first == 1 or not lines[first - 2].strip()):
                last += 1
            ops.append((first, last, []))
        where = f"in place of line {place[0]}" if place[0] == place[1] else f"in place of lines {place[0]}-{place[1]}"
    else:
        start_re = _JS_START_RE
        start_at = next((i + 1 for i, line in enumerate(lines) if start_re.match(line)), None)
        if start_at is not None:
            head = _comment_head(lines, start_at)
            ops.append((head, head - 1, body + [""]))
            where = f"before the start block (line {head})"
        elif lines:
            last = len(lines)
            while last > 1 and not lines[last - 1].strip():
                last -= 1
            ops.append((last, len(lines), [lines[last - 1], ""] + body))
            where = "at the end of the file"
        else:
            ops.append((1, 0, body))
            where = "as the file's first lines"
    new_lines = _apply_line_ops(lines, ops)
    if new_lines is None:
        return None
    if new_lines == lines:
        return [], "the exports hold your names already"
    first = 0
    while first < min(len(lines), len(new_lines)) and lines[first] == new_lines[first]:
        first += 1
    old_last, new_last = len(lines) - 1, len(new_lines) - 1
    while old_last >= first and new_last >= first and lines[old_last] == new_lines[new_last]:
        old_last -= 1
        new_last -= 1
    if first > old_last:  # only an insertion: take the line before along
        first = max(0, first - 1)
    chunk = {"start_line": first + 1, "end_line": old_last + 1, "replace": "\n".join(new_lines[first:new_last + 1])}
    copies = len(file_objects)
    parts = [f"module.exports now holds {len(merged)} name(s), {where}"]
    if copies > 1:
        parts.append(f"the file's {copies} module.exports objects were made ONE")
    elif copies == 1 and not replaced_whole:
        parts.append("the file's names were kept and yours replaced / added")
    if replaced_whole:
        parts.append("the file's exports were not a plain list of names, so yours replaced them")
    gone = [s for s in file_statements if not _EXPORT_OBJECT_START_RE.match(s[2]) and (s[0], s[1]) in removed]
    if gone:
        parts.append(f"{len(gone)} `module.exports.x = ...` line(s) the object holds now were removed")
    if dropped:
        parts.append(f"{dropped} other statement(s) of your code the file has already were not added again")
    if guarded and browser_script:
        parts.append("the file is loaded by a page in the BROWSER, which has no `module`: they are inside "
                     "`if (typeof module !== 'undefined') { ... }`")
    return [chunk], "; ".join(parts)

# ---------------------------------------------------------------------------------------------------- Round 5f
# 'code' that holds SEVERAL top-level things. 2026-11-15 (qwen3.5:4b, WiFi_Snake_Multiplayer_Game): W1 sent replace_function
# function='NETWORK_CONFIG' with the file's header comment, NETWORK_CONFIG, eight `let` state variables and five functions -
# ten times; each time the result declared `socket` twice ("Identifier 'socket' has already been declared") because only
# the lines of NETWORK_CONFIG were replaced. W4 sent `const assert = require('node:assert')` into a file that had
# `const assert = require('assert')`. 30 of 50 replace_function calls failed in that run.
# Every top-level definition in 'code' now replaces the file's definition(s) of that name (a copy that is in the file twice
# goes too), the import lines are merged, a header comment the file already has is not added again, and the new code is
# placed where the first replaced definition was.
DECLARATION_KINDS = ("function", "class", "object", "variable")
_JS_PLAIN_VAR_RE = re.compile(rf"^(?:export\s+)?(?:const|let|var)\s+({_ID})\s*(?::[^=;]+)?(?:=|;|$)")
_JS_EXPORTS_RE = re.compile(rf"^(?:module\.exports|exports)(?:\.({_ID}))?\s*=(?!=)")
_JS_ES_EXPORT_LIST_RE = re.compile(r"^export\s*(?:\{|default\s+(?!function\b|class\b|async\b))")


def _py_comment_head(lines: List[str], start: int) -> int:
    """The first line of the '#' comment block directly above line `start` (1-based), else `start`."""
    index = start - 2
    while index >= 0 and lines[index].strip().startswith("#"):
        index -= 1
    return index + 2 if index + 2 < start else start


def _python_items(source: str) -> Optional[List[Symbol]]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    lines = source.split("\n")
    items: List[Symbol] = []
    for index, node in enumerate(tree.body):
        start = node.lineno
        end = getattr(node, "end_lineno", start) or start
        head = min([start] + [d.lineno for d in getattr(node, "decorator_list", [])])
        head = _py_comment_head(lines, head)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name, kind = node.name, "function"
        elif isinstance(node, ast.ClassDef):
            name, kind = node.name, "class"
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            name, kind = "", "import"
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, kind = node.targets[0].id, "variable"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, kind = node.target.id, "variable"
        elif index == 0 and isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
                and isinstance(node.value.value, str):
            name, kind = "", "docstring"
        elif isinstance(node, ast.If) and _PY_START_RE.match(lines[start - 1].strip()):
            name, kind = "", "start"
        else:
            name, kind = "", "statement"
        items.append(Symbol(name, kind, start, end, head, ""))
    return items


def _javascript_items(source: str) -> Optional[List[Symbol]]:
    try:
        clean = _blank_strings_and_comments(source)
    except Exception:
        return None
    lines = source.split("\n")
    clean_lines = clean.split("\n")
    starts = _line_starts(clean)
    by_start = {s.start: s for s in javascript_symbols(source) if s.parent is None and s.kind != "test"}
    items: List[Symbol] = []
    number = 1
    while number <= len(clean_lines):
        text = clean_lines[number - 1]
        stripped = text.strip()
        if not stripped or stripped in (";", "}", "};"):
            number += 1
            continue
        if text[:1] in " \t" and items:
            return None  # an indented top-level line between statements: not a file this reads safely
        at = starts[number - 1] + len(text) - len(text.lstrip())
        head = _comment_head(lines, number)
        known = by_start.get(number)
        if known is not None:
            end = known.end
            kind, name = ("function" if known.kind == "method" else known.kind), known.name
            if kind not in DECLARATION_KINDS:
                kind = "function"
        else:
            end_offset = _statement_end(clean, at)
            close = clean.find("{", at, end_offset + 1)
            if _JS_START_RE.match(stripped) or _JS_TEST_BLOCK_RE.match(stripped) or (
                    close != -1 and re.match(r"^(?:if|for|while|switch|try|do)\b", stripped)):
                # a block statement: to the brace that closes it (else ... / catch ... included)
                end_offset = _matching_brace(clean, close) if close != -1 else end_offset
                follow = clean[end_offset + 1:end_offset + 400]
                while True:
                    more = re.match(r"\s*(?:else|catch|finally)\b[^{]*\{", follow)
                    if not more:
                        break
                    end_offset = _matching_brace(clean, end_offset + 1 + more.end() - 1)
                    follow = clean[end_offset + 1:end_offset + 400]
                if _JS_TEST_BLOCK_RE.match(stripped):
                    end_offset = _statement_end(clean, at)
            end = max(number, _line_of(starts, min(end_offset, len(clean) - 1)))
            statement = " ".join(line.strip() for line in lines[number - 1:end])
            plain = _JS_PLAIN_VAR_RE.match(stripped)
            if _JS_IMPORT_LINE_RE.match(statement.strip()):
                kind, name = "import", ""
            elif _JS_START_RE.match(stripped):
                kind, name = "start", ""
            elif _JS_TEST_BLOCK_RE.match(stripped):
                kind, name = "test", ""
            elif _JS_EXPORTS_RE.match(stripped):
                target = _JS_EXPORTS_RE.match(stripped).group(1)
                kind, name = "exports", ("exports." + target) if target else "module.exports"
            elif _JS_ES_EXPORT_LIST_RE.match(stripped):
                kind, name = "exports", "export"
            elif plain:
                kind, name = "variable", plain.group(1)
            else:
                kind, name = "statement", ""
        items.append(Symbol(name, kind, number, end, head, ""))
        number = end + 1
    return items


def top_level_items(source: str, filename: Any) -> Optional[List[Symbol]]:
    """
    Round 5f: every top-level statement of a Python or JavaScript source, in order, as a Symbol whose kind is one of
    DECLARATION_KINDS, 'import', 'exports', 'start' (the start block), 'test', 'docstring' or 'statement'. Declarations carry
    their name; head_start is the first line of the comment (or decorators) directly above. None when it cannot be read.
    """
    language = language_of(filename)
    try:
        if language == "python":
            return _python_items(source)
        if language == "javascript":
            return _javascript_items(source)
    except Exception:
        return None
    return None


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(";").strip()


def _js_import_names(statement: str) -> List[str]:
    """The names a JavaScript import line declares (`const {a, b: c} = require(...)` -> a, c)."""
    match = re.match(r"^(?:const|let|var)\s+(\{[^}]*\}|[A-Za-z_$][\w$]*)\s*=", statement.strip())
    if match:
        target = match.group(1)
    else:
        match = re.match(r"^import\s+(.+?)\s+from\s", statement.strip())
        if not match:
            return []
        target = match.group(1)
    names = []
    for part in re.split(r"[{},]", target):
        part = part.strip()
        if not part:
            continue
        part = re.split(r"\s+as\s+|\s*:\s*", part)[-1].strip()
        part = part.replace("* ", "").strip("* ")
        if re.fullmatch(r"[A-Za-z_$][\w$]*", part):
            names.append(part)
    return names


def _apply_line_ops(lines: List[str], ops: List[Tuple[int, int, List[str]]]) -> Optional[List[str]]:
    """Applies (first, last, new lines) replacements (1-based, inclusive; last = first - 1 inserts before `first`). None
    when two of them overlap."""
    ordered = sorted(ops, key=lambda op: (op[0], op[1]))
    for (a_first, a_last, _), (b_first, _b_last, _) in zip(ordered, ordered[1:]):
        if b_first <= a_last:
            return None
    out = list(lines)
    for first, last, new in reversed(ordered):
        out[first - 1:last] = new
    return out


def _squeeze_blank_runs(lines: List[str], limit: int) -> List[str]:
    out: List[str] = []
    run = 0
    for line in lines:
        run = run + 1 if not line.strip() else 0
        if run <= limit:
            out.append(line)
    return out


# Round 5m: a statement that only calls something (`startGameLoop();`, `main()`, `await start();`)
_CALL_STATEMENT_RE = re.compile(r"^(?:await\s+)?[A-Za-z_$][\w$.]*\s*\(.*\)\s*;?$")


def unguarded_start_calls(original: str, filename: Any, start_block: str) -> List[Tuple[int, int, str]]:
    """
    Round 5m: (first line, last line, call) of the file's top-level calls OUTSIDE its start block that the start block
    'start_block' makes too. 2026-11-19 17:40-17:46 W1 sent `startGameLoop` with `if (require.main === module) {
    startGameLoop(); }`: the block was added at the end of server.js, the bare `startGameLoop();` above it (line 316) stayed,
    the tests that load the module still never ended, and W1 sent the same edit again for six minutes. The first line
    includes a comment directly above the call.
    """
    items = top_level_items(original, filename)
    if not items:
        return []
    lines = original.split("\n")
    calls = set()
    for line in str(start_block or "").split("\n")[1:]:
        text = _normal(line)
        if _CALL_STATEMENT_RE.match(text):
            calls.add(text)
    found = []
    for item in items:
        if item.kind != "statement":
            continue
        text = _normal(" ".join(lines[item.start - 1:item.end]))
        if text in calls:
            found.append((item.head_start, item.end, text))
    return found


def multi_definition_edit(original: str, filename: Any, code: str,
                          wanted: Any = None) -> Optional[Tuple[Dict[str, Any], str]]:
    """
    Round 5f: replace_function for 'code' that holds several top-level things -> (ONE line-range chunk, what was done), or
    None when this does not apply (then the one-definition rules of replace_function do).

    Applies when 'code' holds two or more top-level definitions (functions, classes, objects, variables), or one together
    with import lines, the exports or the start block - and nothing else except statements the file already has (they are
    dropped). Then:
    * every definition of 'code' replaces ALL top-level definitions of that name in the file (a copy that is there twice
      goes too); the new code is placed where the first of them was (new names only: after the file's last definition);
    * import lines are merged into the file's imports (src/utils/symbols.file_block_edit);
    * `module.exports = ...` / the start block replace the file's own (or are added at its end);
    * a header comment at the top of 'code' that the file already holds is not added again.
    A 'wanted' name that is no top-level name of 'code' (e.g. 'Game.update', or another name) -> None.
    """
    language = language_of(filename)
    if language not in ("python", "javascript"):
        return None
    python = language == "python"
    text = reindent(strip_code_fence(code or ""), "").strip("\n")
    code_items = top_level_items(text, filename)
    if not code_items:
        return None
    declarations = [item for item in code_items if item.kind in DECLARATION_KINDS]
    extras = [item for item in code_items if item.kind in ("import", "exports", "start")]
    if not declarations or (len(declarations) < 2 and not extras):
        return None
    wanted_name = clean_name(wanted) if wanted else ""
    if wanted_name and _block_name(wanted_name) not in IMPORT_BLOCK_NAMES | START_BLOCK_NAMES \
            and wanted_name not in {item.name for item in declarations}:
        return None
    file_lines = original.split("\n")
    trailing_newline = bool(file_lines) and file_lines[-1] == ""
    if trailing_newline:
        file_lines = file_lines[:-1]
    file_items = top_level_items(original, filename)
    if file_items is None:
        return None
    file_statements = {_normal(" ".join(file_lines[i.start - 1:i.end])) for i in file_items
                       if i.kind in ("statement", "test", "docstring")}
    code_lines = text.split("\n")
    dropped_statements = 0
    for item in code_items:
        if item.kind in ("statement", "test", "docstring"):
            if _normal(" ".join(code_lines[item.start - 1:item.end])) in file_statements:
                dropped_statements += 1
                continue
            return None  # code with other statements: not for this
    notes: List[str] = []
    ops: List[Tuple[int, int, List[str]]] = []
    remove_from_code: List[Tuple[int, int]] = []

    # a header comment that the file already holds
    first_head = min(item.head_start for item in code_items)
    if first_head > 1:
        preamble = [line for line in code_lines[:first_head - 1] if line.strip()]
        if preamble and _normal("\n".join(preamble)) in _normal("\n".join(file_lines)):
            remove_from_code.append((1, first_head - 1))
            notes.append("the header comment at the top of your code is in the file already, so it was not added again")

    # imports: merged into the file's imports
    imports = [item for item in code_items if item.kind == "import"]
    if imports:
        import_text = "\n".join("\n".join(code_lines[i.start - 1:i.end]) for i in imports)
        if not python:
            import_text = _merge_js_imports_by_name(import_text, file_lines, file_items)
        merged = file_block_edit("\n".join(file_lines) + ("\n" if trailing_newline else ""), filename, "imports",
                                 import_text, None) if import_text.strip() else ([], "imports: already there")
        if merged is None:
            return None
        merge_chunks, merge_note = merged
        for chunk in merge_chunks:
            ops.append((chunk["start_line"], chunk["end_line"], chunk["replace"].split("\n")))
        notes.append(merge_note)
        for item in imports:
            remove_from_code.append((item.head_start, item.end))

    # exports / start block: in place of the file's own, or at the end of the file
    tail_additions: List[str] = []
    for item in [i for i in code_items if i.kind in ("exports", "start")]:
        new = code_lines[item.head_start - 1:item.end]
        same = [f for f in file_items if f.kind == item.kind and (item.kind == "start" or f.name == item.name)]
        if same:
            target = same[-1]
            replacement = code_lines[item.start - 1:item.end]
            if item.kind == "exports":
                merged_exports = _union_exports("\n".join(file_lines[target.start - 1:target.end]), "\n".join(replacement))
                if merged_exports is None:
                    # not two lists of names: the file's exports stay (a partial list would drop the others)
                    notes.append(f"the file's {item.name} (line {target.start}) was KEPT - your {item.name} is not a "
                                 "list of names to add; change it with file_system 'replace_lines' if it must change")
                    remove_from_code.append((item.head_start, item.end))
                    continue
                replacement = merged_exports.split("\n")
            where = f"line {target.start}" if target.start == target.end else f"lines {target.start}-{target.end}"
            label = 'the start block' if item.kind == 'start' else item.name
            if _normal("\n".join(replacement)) == _normal("\n".join(file_lines[target.start - 1:target.end])):
                notes.append(f"{label} ({where}) has your names already")
            else:
                ops.append((target.start, target.end, replacement))
                notes.append(f"{label} (was {where}) " + ("merged with your names" if item.kind == "exports" else "replaced"))
        else:
            tail_additions.extend([""] + new)
            notes.append(f"{'the start block' if item.kind == 'start' else item.name} added at the end of the file")
        remove_from_code.append((item.head_start, item.end))

    # the definitions
    for item in code_items:
        if item.kind in ("statement", "test", "docstring"):
            remove_from_code.append((item.head_start, item.end))
    kept = [line for number, line in enumerate(code_lines, start=1)
            if not any(first <= number <= last for first, last in remove_from_code)]
    block = _squeeze_blank_runs(kept, 2 if python else 1)
    while block and not block[0].strip():
        block.pop(0)
    while block and not block[-1].strip():
        block.pop()
    if python:
        block = _python_top_level_spacing(block)
    replaced: List[str] = []
    places: List[Tuple[int, int]] = []
    seen_names = set()
    for item in declarations:
        if item.name in seen_names:
            continue
        seen_names.add(item.name)
        old = [f for f in file_items if f.kind in DECLARATION_KINDS and f.name == item.name]
        brings_head = item.head_start < item.start
        for number, f in enumerate(old):
            first = f.head_start if brings_head else f.start
            places.append((first, f.end))
            where = f"line {first}" if first == f.end else f"lines {first}-{f.end}"
            replaced.append(f"'{item.name}' (was {where})" if number == 0 else f"a second '{item.name}' ({where}, removed)")
    added = [item.name for item in declarations if not any(f.kind in DECLARATION_KINDS and f.name == item.name
                                                           for f in file_items)]
    places = sorted(set(places))
    for (a_first, a_last), (b_first, b_last) in zip(places, places[1:]):
        if b_first <= a_last:
            return None  # nested or overlapping definitions: not for this
    if places:
        first_place = places[0]
        ops.append((first_place[0], first_place[1], block))
        for first, last in places[1:]:
            # a removed definition takes one blank line next to it along
            if last < len(file_lines) and not file_lines[last].strip() and (first == 1 or not file_lines[first - 2].strip()):
                last += 1
            ops.append((first, last, []))
    else:
        symbols_now = find_symbols("\n".join(file_lines), filename) or []
        after, _indent, where = insertion_line(symbols_now, declarations[0].name, len(file_lines), python=python)
        exports_at = [f for f in file_items if f.kind == "exports"]
        if not python and exports_at and (after >= exports_at[0].start or not symbols_now):
            after = exports_at[0].head_start - 1
            where = "before the exports"
        gap = [""] * (2 if python else 1)
        if after <= 0:
            ops.append((1, 0, block + gap))
        else:
            ops.append((after, after, [file_lines[after - 1]] + gap + block))
        notes.insert(0, f"your code was ADDED {where}")
    # Round 5m: the calls the start block makes are not made again, unguarded, when the file is loaded
    for item in [i for i in code_items if i.kind == "start"]:
        block_text = "\n".join(code_lines[item.start - 1:item.end])
        for first, last, call in unguarded_start_calls("\n".join(file_lines), filename, block_text):
            shown = f"line {last}" if first == last else f"lines {first}-{last}"
            if last < len(file_lines) and not file_lines[last].strip() and (first == 1 or not file_lines[first - 2].strip()):
                last += 1  # and one blank line next to it
            if tail_additions and last >= len(file_lines):
                blank_before = first > 1 and not file_lines[first - 2].strip()
                ops.append((first, len(file_lines), tail_additions[1:] if blank_before else tail_additions))
                tail_additions = []
            else:
                ops.append((first, last, []))
            notes.append(f"the bare `{call}` ({shown}) was REMOVED: it ran "
                         "whenever the file was loaded (also by the tests); the start block makes that call now")
    if tail_additions:
        last = len(file_lines)
        ops.append((last, last, [file_lines[last - 1]] + tail_additions if last else tail_additions))
    new_lines = _apply_line_ops(file_lines, ops)
    if new_lines is None:
        return None
    while len(new_lines) > 1 and not new_lines[-1].strip() and file_lines and file_lines[-1].strip():
        new_lines.pop()  # a definition removed at the end leaves no blank lines behind
    longest, run = 0, 0
    for line in file_lines:
        run = run + 1 if not line.strip() else 0
        longest = max(longest, run)
    blank_limit = max(2 if python else 1, min(longest, 2))
    # one chunk from the first to the last changed line
    first = 0
    while first < min(len(file_lines), len(new_lines)) and file_lines[first] == new_lines[first]:
        first += 1
    old_last, new_last = len(file_lines) - 1, len(new_lines) - 1
    while old_last >= first and new_last >= first and file_lines[old_last] == new_lines[new_last]:
        old_last -= 1
        new_last -= 1
    if first > old_last and first > new_last:
        return {"start_line": 1, "end_line": 0, "replace": ""}, "nothing had to change: the file holds this code already"
    if first > old_last:  # only an insertion: take the line before (or after) along
        if first > 0:
            first -= 1
        else:
            old_last += 1
            new_last += 1
    span = new_lines[first:new_last + 1]
    if old_last + 1 < len(file_lines) or len(span) > 1:
        squeezed = _squeeze_blank_runs(span, blank_limit)
        # a blank run that continues past the span's ends is not doubled
        while squeezed and not squeezed[-1].strip() and old_last + 1 < len(file_lines) and not file_lines[old_last + 1].strip():
            squeezed.pop()
        while squeezed and not squeezed[0].strip() and first > 0 and not file_lines[first - 1].strip():
            squeezed.pop(0)
        span = squeezed
    chunk = {"start_line": first + 1, "end_line": old_last + 1, "replace": "\n".join(span)}
    parts = []
    if replaced:
        parts.append("replaced " + ", ".join(replaced))
    if added:
        parts.append("added " + ", ".join(repr(name) for name in added))
    if dropped_statements:
        parts.append(f"{dropped_statements} statement(s) the file has already were not added again")
    summary = "; ".join(parts + notes)
    return chunk, f"your code holds {len(seen_names)} definition(s): {summary}"


_EXPORTS_OBJECT_RE = re.compile(r"^(module\.exports\s*=\s*)\{(.*)\}\s*;?\s*$", re.DOTALL)


def _export_entries(body: str) -> Optional[List[Tuple[str, str]]]:
    """(key, entry text) of an object literal body at depth 0, or None when it is not a plain list."""
    entries, depth, current = [], 0, ""
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            entries.append(current)
            current = ""
        else:
            current += ch
    entries.append(current)
    out = []
    for entry in entries:
        text = entry.strip()
        if not text:
            continue
        key = re.match(r"^(?:\.\.\.)?\s*['\"]?([A-Za-z_$][\w$]*)['\"]?", text)
        if not key:
            return None
        out.append((key.group(1), text))
    return out


def _union_exports(old: str, new: str) -> Optional[str]:
    """
    `module.exports = { a, b }` + `module.exports = { b, c }` -> `module.exports = { a, b, c }` (the new entry wins for a
    name in both), else None. A model sends the exports with only the names it just wrote; the file's other names stay.
    """
    a, b = _EXPORTS_OBJECT_RE.match(old.strip()), _EXPORTS_OBJECT_RE.match(new.strip())
    if not a or not b:
        return None
    old_entries, new_entries = _export_entries(a.group(2)), _export_entries(b.group(2))
    if old_entries is None or new_entries is None:
        return None
    new_by_key = dict(new_entries)
    merged = [new_by_key.pop(key, text) for key, text in old_entries]
    merged += [text for key, text in new_entries if key in new_by_key]
    multiline = "\n" in old.strip() or len(merged) > 6
    if multiline:
        return "module.exports = {\n" + ",\n".join("  " + text for text in merged) + "\n};"
    return "module.exports = { " + ", ".join(merged) + " };"


def _python_top_level_spacing(block: List[str]) -> List[str]:
    """Two blank lines before every top-level def / class (with its decorators and comments) of a Python block."""
    starts = []
    index = 0
    while index < len(block):
        line = block[index]
        if line[:1] in ("@", "#") or line.startswith(("def ", "class ", "async def ")):
            first = index
            while index < len(block) and block[index][:1] in ("@", "#"):
                index += 1
            if index < len(block) and block[index].startswith(("def ", "class ", "async def ")) and first > 0:
                starts.append(first)
        index += 1
    out: List[str] = []
    for number, line in enumerate(block):
        if number in starts:
            while out and not out[-1].strip():
                out.pop()
            out.extend(["", ""])
        out.append(line)
    return out


def _merge_js_imports_by_name(import_text: str, file_lines: List[str], file_items: List[Symbol]) -> str:
    """
    JavaScript import lines of 'code' whose names the file imports already from the same module under another spelling
    ('node:assert' / 'assert') are dropped; the other lines are returned for file_block_edit.
    """
    file_imports = {}
    for item in file_items:
        if item.kind == "import":
            statement = " ".join(line.strip() for line in file_lines[item.start - 1:item.end])
            source = _JS_SOURCE_RE.search(statement)
            module = source.group(1).replace("node:", "", 1) if source else ""
            for name in _js_import_names(statement):
                file_imports[name] = module
    kept = []
    for line in import_text.split("\n"):
        source = _JS_SOURCE_RE.search(line)
        names = _js_import_names(line)
        module = source.group(1).replace("node:", "", 1) if source else None
        if names and module is not None and all(file_imports.get(name) == module for name in names):
            continue
        kept.append(line)
    return "\n".join(kept)


# END OF FILE src/utils/symbols.py
