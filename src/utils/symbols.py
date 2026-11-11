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
                    brace = _body_brace(clean, at + match.end(), close_at)
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


def lookup(symbols: List[Symbol], name: Any) -> Tuple[Optional[Symbol], List[Symbol]]:
    """
    (the one symbol the name means, the candidates when it is ambiguous). A full name ('Game.update') must match exactly; a
    bare name ('update') matches a top-level definition first, else the only method of that name.
    """
    title = str(name or "").strip().strip("`'\"").strip()
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
                   as_test: bool = False) -> Tuple[int, str, str]:
    """
    Where a definition that does not exist yet goes: (the line AFTER which it is inserted, its indentation, what that
    place is). 'Game.reset' goes after the last method of Game; a top-level name after the last top-level definition, else
    at the end of the file.
    """
    wanted = clean_name(name)
    if as_test:
        tests = [s for s in symbols if s.kind == "test"]
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


def _import_key(statement: str, python: bool) -> Optional[str]:
    if python:
        match = _PY_FROM_RE.match(statement)
        if match:
            return "from " + match.group(1)
        return re.sub(r"\s+", " ", statement.split(" as ")[0]).strip() if statement.startswith("import ") else None
    match = _JS_SOURCE_RE.search(statement)
    return match.group(1) if match else None


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
            return ([{"start_line": last, "end_line": len(lines), "replace": lines[last - 1] + "\n\n\n" + new_text}],
                    "the file had no start block, so yours was ADDED at its end")
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][:1] in " \t" or (not python and lines[end].startswith("}"))):
            end += 1
            if not python and lines[end - 1].startswith("}"):
                break
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        return ([{"start_line": start + 1, "end_line": end, "replace": new_text}],
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
            first, last, _old = by_key[key]
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

# END OF FILE src/utils/symbols.py
