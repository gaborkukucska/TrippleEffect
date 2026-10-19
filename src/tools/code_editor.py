import logging
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import traceback
import json
import re

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings
from diff_match_patch import diff_match_patch

logger = logging.getLogger(__name__)

# --- Round 1y helpers ---------------------------------------------------------------------------------------------------
# 2026-10-17 18:09:13: a chunk with start/end_line AND 'search_block' deleted lines ..86 (the whole of src/temp_convert.py)
# because the line numbers silently won over the search text. The file stayed empty for the rest of the 14-hour run.
_TRAILING_JUNK_RE = re.compile(r"[\s\]\}>`]*")
CODE_EDITOR_READ_ALIASES = frozenset({
    "read", "read_file", "read_with_lines", "read_file_with_lines", "view", "view_file", "show", "cat", "open",
})


def _loads_chunks(text: str):
    """json.loads(strict=False); a valid array/object followed only by stray brackets/backticks ('Extra data') is accepted."""
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError as err:
        if not str(err.msg).startswith("Extra data"):
            raise
        stripped = text.strip()
        value, end = json.JSONDecoder(strict=False).raw_decode(stripped)
        if isinstance(value, (list, dict)) and _TRAILING_JUNK_RE.fullmatch(stripped[end:]):
            logger.info(f"CodeEditorTool: ignored trailing characters {stripped[end:][:20]!r} after the 'chunks' JSON.")
            return value
        raise


_JSON_NAMES = {"null": "None", "true": "True", "false": "False"}


def _literal_eval_chunks(text: str):
    """
    Round 2c: ast.literal_eval for a 'chunks' string that mixes JSON and Python (W1 sent
    [{"end_line": null, "search": 'if __name__ == "__main__":', ...}] 7 times on 2026-10-18). The JSON names null/true/false
    are replaced only where they are NAMES (never inside strings), so the strings stay exactly as sent.
    """
    import ast
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        pass
    import io
    import tokenize
    tokens = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.NAME and tok.string in _JSON_NAMES:
                tok = tok._replace(string=_JSON_NAMES[tok.string])   # same length: positions stay valid
            tokens.append(tok)
    except tokenize.TokenError as err:   # unclosed bracket or string
        raise ValueError(f"not a complete literal: {err}") from err
    return ast.literal_eval(tokenize.untokenize(tokens))


WRITE_ACTION_PREFIXES = ("write", "create", "save", "overwrite")
WRITE_CONTENT_KEYS = ("file_content", "content", "file_contents", "contents", "code", "new_content")


def is_write_action(action: Any) -> bool:
    """Round 2c: 'write', 'write_file', 'write_file_content', 'create_file', 'save_file', ... (whole-file writes)."""
    return isinstance(action, str) and action.startswith(WRITE_ACTION_PREFIXES)


# --- Round 2d helpers -----------------------------------------------------------------------------------------------------
# 2026-10-18 22:17-23:59 (Temp_Converter_CLI, qwen3.5:4b): W1 needed 70 minutes and ~30 failed calls to replace one broken
# main() function. The calls failed on the framework's side, not on the model's: 'chunks' with an invalid JSON escape (\C),
# one edit given as top-level search_block/replace_block (7x "Missing required parameters: chunks"), start_line given as
# '43, 52', and line numbers that were one line off from a search text that matched exactly once (6x "DIFFERENT code").
def _repair_json_escapes(text: str) -> str:
    r"""Doubles every backslash that does not start a valid JSON escape (\C -> \\C), so json.loads() accepts the text."""
    out: List[str] = []
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        following = text[index + 1] if index + 1 < size else ""
        if following and following in '"\\/bfnrt':
            out.append(text[index:index + 2])
            index += 2
        elif following == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", text[index + 2:index + 6] or ""):
            out.append(text[index:index + 6])
            index += 6
        else:
            out.append("\\\\")  # a literal backslash; the following character is kept as it is
            index += 1
    return "".join(out)


def _close_open_brackets(text: str) -> str:
    """
    Round 2d: appends the closing brackets a JSON text is missing at its END ('[{"search": ...}' -> '[{"search": ...}]', W1
    on 2026-10-18 22:33 and 22:35). The text is returned unchanged if a string is still open or the brackets do not match.
    """
    stack: List[str] = []
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack or {"[": "]", "{": "}"}[stack.pop()] != char:
                return text
    if in_string or not stack:
        return text
    return text.rstrip() + "".join("]" if opener == "[" else "}" for opener in reversed(stack))


_LINE_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:,|-|–|:|\.\.|to)\s*(\d+)\s*$", re.IGNORECASE)


def parse_line_numbers(value: Any):
    """
    (first, last) from a line-number value: 43 -> (43, None); '43' -> (43, None); '43, 52' / '43-52' / '43 to 52' -> (43, 52).
    (None, None) if the value holds no line number. Booleans are not line numbers.
    """
    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, int):
        return value, None
    if isinstance(value, float) and value.is_integer():
        return int(value), None
    text = str(value).strip()
    if text.isdigit():
        return int(text), None
    match = _LINE_RANGE_RE.match(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


TOP_LEVEL_SEARCH_KEYS = ("search", "search_block", "search_text", "find", "find_text", "old", "old_code", "old_text")
TOP_LEVEL_REPLACE_KEYS = ("replace", "replace_block", "replace_text", "replacement", "new", "new_code", "new_text",
                          "new_content", "line_content", "content",
                          # Round 2e: W2 sent {path, start_line, end_line, text} five times on 2026-10-19 05:00-05:13
                          "text", "code", "replace_with", "replacement_text")
# Keys of the new text inside a chunk; the first six are the historical ones, in their historical order.
CHUNK_REPLACE_KEYS = ("replace", "replace_block", "replace_text", "replacement", "new", "new_code", "new_text", "new_content",
                      "content", "line_content", "text", "code", "replace_with", "replacement_text")
TOP_LEVEL_START_KEYS = ("start_line", "from_line", "line_start", "replace_start_line")
TOP_LEVEL_END_KEYS = ("end_line", "to_line", "line_end", "replace_end_line")
EDIT_ACTION_ALIASES = frozenset({
    "replace", "edit", "modify", "replace_chunk", "replace_lines", "replace_line", "search_replace", "search_replace_block",
    "find_replace", "find_and_replace", "replace_block", "replace_text", "edit_file", "modify_file", "update_file",
    "apply_chunks", "apply_edits",
})


def _first_present(args: Dict[str, Any], keys) -> Any:
    for key in keys:
        if args.get(key) is not None:
            return args[key]
    return None


def single_chunk_from_args(args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Round 2d: ONE edit given as top-level arguments (search_block/replace_block, search_text/replace_text, start_line/end_line
    with replace/content ...) as a chunk dict, or None if the arguments do not describe an edit.
    """
    if not isinstance(args, dict):
        return None
    replace = _first_present(args, TOP_LEVEL_REPLACE_KEYS)
    if replace is None or isinstance(replace, (list, dict)):
        return None
    chunk: Dict[str, Any] = {"replace": str(replace)}
    search = _first_present(args, TOP_LEVEL_SEARCH_KEYS)
    if isinstance(search, str) and search.strip():
        chunk["search"] = search
    start, range_end = parse_line_numbers(_first_present(args, TOP_LEVEL_START_KEYS))
    end, _ = parse_line_numbers(_first_present(args, TOP_LEVEL_END_KEYS))
    if end is None:
        end = range_end
    if start is not None:
        chunk["start_line"] = start
    if end is not None:
        chunk["end_line"] = end
    if "search" not in chunk and start is None and end is None:
        return None
    return chunk


LINE_HINT_TOLERANCE_BEFORE = 1
LOCAL_EDIT_EXTRA_LINES = 2


def line_hint_edit_allowed(content: str, match_start: int, found_line: int, start_line: int, search_text: str,
                           replace_text: str) -> bool:
    """
    Round 2d: may a search text found by unique_search_near_lines() replace ONLY its match? Yes if it begins a block of whole
    lines at start_line +-1 (the line numbers were just off by one). A match deeper inside the range, or a fragment of a
    line, is only a LOCAL fix: the replacement may have at most 2 lines more than the search text. Otherwise a model that
    meant "replace lines 10-20 with this new block" but searched for one line in the middle would get duplicated code.
    """
    line_begin = content.rfind("\n", 0, match_start) + 1
    starts_a_line = not content[line_begin:match_start].strip()
    if starts_a_line and abs(found_line - start_line) <= 1:
        return True
    search_lines = len(str(search_text).strip("\n").splitlines()) or 1
    replace_lines = len(str(replace_text).strip("\n").splitlines())
    return replace_lines <= search_lines + LOCAL_EDIT_EXTRA_LINES


def unique_search_near_lines(content: str, search_text: str, start_line: int, end_line: int):
    """
    Round 2d: (start_char, end_char, line, exact) of the ONE place where search_text occurs between start_line - 1 and
    end_line: first exactly, else ignoring whitespace. None if there is no such place or more than one.
    """
    if not isinstance(search_text, str) or not search_text.strip():
        return None
    first_allowed = max(1, start_line - LINE_HINT_TOLERANCE_BEFORE)
    last_allowed = max(start_line, end_line)

    def in_window(position: int) -> bool:
        return first_allowed <= content.count("\n", 0, position) + 1 <= last_allowed

    exact_hits = []
    position = content.find(search_text)
    while position != -1:
        exact_hits.append(position)
        position = content.find(search_text, position + 1)
    near = [hit for hit in exact_hits if in_window(hit)]
    if len(near) == 1:
        return near[0], near[0] + len(search_text), content.count("\n", 0, near[0]) + 1, True
    if exact_hits:
        return None
    pieces = [re.escape(piece) for piece in search_text.split() if piece]
    if not pieces:
        return None
    matches = [m for m in re.finditer(r"\s*".join(pieces), content) if in_window(m.start())]
    if len(matches) != 1:
        return None
    match = matches[0]
    return match.start(), match.end(), content.count("\n", 0, match.start()) + 1, False


def _search_text_in(lines: List[str], wanted: List[str]) -> bool:
    """True if the non-blank search lines `wanted` occur as consecutive non-blank lines of the file (whitespace ignored)."""
    actual = [line.strip() for line in lines if line.strip()]
    size = len(wanted)
    return any(actual[index:index + size] == wanted for index in range(len(actual) - size + 1))


def _line_range_conflict(lines: List[str], start_idx: int, end_idx: int, search_text: str) -> Optional[str]:
    """
    None if 'search' agrees with lines start_idx..end_idx: the range starts with the search lines (whitespace at line ends
    and blank lines ignored). Otherwise the reason, showing the lines the range really holds and where the search text is.
    """
    wanted = [line.strip() for line in search_text.splitlines() if line.strip()]
    if not wanted:
        return None
    actual = [line.strip() for line in lines[start_idx:end_idx] if line.strip()]
    if actual[:len(wanted)] == wanted:
        return None
    shown_end = min(end_idx, start_idx + 6)
    shown = "".join(f"{n + 1}: {lines[n]}" for n in range(start_idx, shown_end)).rstrip("\n") or "(no lines)"
    more = f"\n... ({end_idx - shown_end} more line(s) up to line {end_idx})" if end_idx > shown_end else ""
    found_at = [str(n + 1) for n, line in enumerate(lines) if line.strip() == wanted[0]]
    where = (f"The first line of your search text is at line(s) {', '.join(found_at[:5])}." if found_at
             else "Your search text is not in the file.")
    if found_at and len(wanted) > 1 and not _search_text_in(lines, wanted):
        # Round 2e: 2026-10-19 03:54 - W1 searched for 5 lines it believed its failed 03:48 edit had written; only the first
        # existed. "The first line ... is at line 50" made it look like a line-number problem.
        where = (f"Only the FIRST line of your search text is in the file (line(s) {', '.join(found_at[:5])}); the rest of it is "
                 "NOT in the file, so the code you want to replace does not exist (an earlier edit may not have been saved). "
                 "Read the file again and edit what is really there.")
    return (f"start_line/end_line ({start_idx + 1}-{end_idx}) and the 'search' text point to DIFFERENT code, so nothing was "
            f"changed. Lines {start_idx + 1}-{end_idx} are:\n{shown}{more}\n{where} Send EITHER line numbers (read the "
            "file with line numbers first) OR a 'search' text that matches exactly - not both.")


# --- Round 2e: replacement text that repeats the lines next to its range ------------------------------------------------------
# 2026-10-19 03:56 and 03:58: W1 replaced line 1 (and 1-3) of src/temp_convert.py with new imports FOLLOWED by
# 'def celsius_to_fahrenheit(...):' and the start of its docstring - lines that come right after the range and were kept. The
# result had the def line twice ("expected an indented block"), was rejected twice, and W1 was forced into worker_report.
def _same_lines(first: List[str], second: List[str]) -> bool:
    return [line.rstrip() for line in first] == [line.rstrip() for line in second]


def boundary_overlap(lines: List[str], start_idx: int, end_idx: int, replacement: str):
    """
    (lines_before, lines_after): how many lines at the START of the replacement repeat the lines right before the range, and
    how many at its END repeat the lines right after it (whitespace at line ends ignored; the repeated block must contain a
    non-blank line). (0, 0) if there is no such overlap.
    """
    new_lines = str(replacement or "").splitlines(True)
    after, before = lines[end_idx:], lines[:start_idx]
    overlap_after = 0
    for size in range(min(len(new_lines), len(after)), 0, -1):
        if any(line.strip() for line in after[:size]) and _same_lines(new_lines[-size:], after[:size]):
            overlap_after = size
            break
    overlap_before = 0
    for size in range(min(len(new_lines) - overlap_after, len(before)), 0, -1):
        if any(line.strip() for line in before[-size:]) and _same_lines(new_lines[:size], before[-size:]):
            overlap_before = size
            break
    return overlap_before, overlap_after


def whole_line_span(content: str, start: int, end: int):
    """(first_line_index, end_line_index) if content[start:end] covers whole lines (it may stop just before a line break),
    else None."""
    if start < 0 or end <= start or end > len(content):
        return None
    if start > 0 and content[start - 1] != "\n":
        return None
    first = content.count("\n", 0, start)
    if content[end - 1] == "\n":
        return first, content.count("\n", 0, end)
    if end == len(content) or content[end] == "\n":
        return first, content.count("\n", 0, end) + 1
    return None


def repair_boundary_overlap(current: str, lines: List[str], start_idx: int, end_idx: int, replacement: str, naive: str):
    """
    (new_content, note) when replacing lines start_idx..end_idx of a VALID Python text with `replacement` gives invalid Python
    (`naive`) only because the replacement repeats the lines right before/after the range, and the replacement without the
    repeated lines gives valid Python. None in every other case, so a correct edit is never changed.
    """
    import ast
    try:
        ast.parse(naive)
        return None  # the edit as sent is valid
    except (SyntaxError, ValueError):
        pass
    try:
        ast.parse(current)
    except (SyntaxError, ValueError):
        return None  # the file is already broken: do not guess
    overlap_before, overlap_after = boundary_overlap(lines, start_idx, end_idx, replacement)
    if not overlap_before and not overlap_after:
        return None
    new_lines = str(replacement).splitlines(True)
    # the repeated lines are kept ONCE: the copy in the file before/after the range stays, the copy in the text is dropped
    text = "".join(new_lines[overlap_before:len(new_lines) - overlap_after])
    if text and not text.endswith("\n") and end_idx < len(lines):
        text += "\n"
    candidate = "".join(lines[:start_idx]) + text + "".join(lines[end_idx:])
    try:
        ast.parse(candidate)
    except (SyntaxError, ValueError):
        return None
    parts = []
    if overlap_before:
        parts.append(f"its first {overlap_before} line(s) repeated the line(s) before line {start_idx + 1}")
    if overlap_after:
        parts.append(f"its last {overlap_after} line(s) repeated the line(s) after line {end_idx}")
    return candidate, (" and ".join(parts) + ", which are already in the file, so they were not inserted a second time")


def replacement_already_in_place(lines: List[str], start_idx: int, end_idx: int, replacement) -> bool:
    """
    Round 2e: True if the lines starting at start_idx already ARE the replacement text and the replacement is longer than the
    range: the same chunk was applied before (its line numbers are the ones from before that edit).
    """
    new_lines = str(replacement or "").splitlines()
    if not any(line.strip() for line in new_lines) or len(new_lines) <= end_idx - start_idx:
        return False
    return _same_lines(lines[start_idx:start_idx + len(new_lines)], new_lines)


IMPORTANT_STATEMENT_PREFIXES = ("return", "def ", "async def ", "class ", "import ", "from ", "raise", "yield", "assert ",
                                "if __name__")
MAX_REMOVED_STATEMENTS = 4


def removed_statement_lines(lines: List[str], start_idx: int, end_idx: int, replacement) -> List[str]:
    """
    Round 2e: 'line: text' of the return/def/class/import/raise/yield/assert lines inside the replaced range that do not occur
    in the replacement text (compared without surrounding whitespace). These are the losses a small model does not notice.
    """
    kept = {line.strip() for line in str(replacement or "").splitlines() if line.strip()}
    lost: List[str] = []
    for number in range(start_idx, min(end_idx, len(lines))):
        text = lines[number].strip()
        if text and text not in kept and text.startswith(IMPORTANT_STATEMENT_PREFIXES):
            lost.append(f"line {number + 1} '{text[:80]}'")
    return lost[:MAX_REMOVED_STATEMENTS]


FUZZY_MIN_RATIO = 0.9
FUZZY_MIN_CHARS = 20
FUZZY_MAX_LINES = 5000


def fuzzy_line_window(content: str, search_text: str):
    """
    Round 1z: (start, end, first_line, last_line) of the ONE block of whole lines that is at least 90 % similar to
    search_text (leading/trailing whitespace and blank lines ignored), or None. Replaces diff_match_patch fallbacks that
    patched wherever a search text half-fitted and corrupted files. Searches shorter than 20 characters never match fuzzily.
    """
    import difflib
    wanted = [line.strip() for line in str(search_text or "").splitlines() if line.strip()]
    wanted_text = "\n".join(wanted)
    if not wanted or len(wanted_text) < FUZZY_MIN_CHARS:
        return None
    file_lines = content.splitlines(True)
    if len(file_lines) > FUZZY_MAX_LINES:
        return None
    non_blank = [(index, line.strip()) for index, line in enumerate(file_lines) if line.strip()]
    size = len(wanted)
    hits = []
    for start in range(len(non_blank) - size + 1):
        window = non_blank[start:start + size]
        ratio = difflib.SequenceMatcher(None, wanted_text, "\n".join(text for _, text in window)).ratio()
        if ratio >= FUZZY_MIN_RATIO:
            hits.append((window[0][0], window[-1][0]))
    if len(hits) != 1:
        return None
    first, last = hits[0]
    start_char = sum(len(line) for line in file_lines[:first])
    end_char = start_char + sum(len(line) for line in file_lines[first:last + 1])
    return start_char, end_char, first + 1, last + 1


def replace_fuzzy_window(content: str, window, replacement: str) -> str:
    """
    content with the block found by fuzzy_line_window() replaced. Keeps the block's line break at the end, and its
    indentation when the replacement starts without any (the block is matched ignoring indentation).
    """
    start_char, end_char = window[0], window[1]
    block = content[start_char:end_char]
    text = replacement or ""
    block_indent = block[:len(block) - len(block.lstrip(" \t"))]
    first_text_line = next((line for line in text.splitlines() if line.strip()), "")
    if block_indent and first_text_line and not first_text_line[:1].isspace():
        text = "".join(block_indent + line if line.strip() else line for line in text.splitlines(True))
    if text and block.endswith("\n") and not text.endswith("\n"):
        text += "\n"
    return content[:start_char] + text + content[end_char:]


def emptied_file_error(filename: str, original: str, new_content: str) -> Optional[str]:
    """The refusal message if an edit would leave a file that had content empty (only whitespace), else None."""
    if not str(original).strip() or str(new_content).strip():
        return None
    line_count = len(str(original).splitlines())
    return (f"Edit refused: it would delete ALL content of '{filename}' ({line_count} lines), leaving the file empty. No "
            "changes were saved. Replace or delete only the lines you mean (read the file with line numbers first). To "
            "really replace the whole file, use file_system action 'write' with the complete new content and "
            "\"force_overwrite\": true.")


class CodeEditorTool(BaseTool):
    """
    Tool for safe, diff-based file editing using standard search/replace or patch functionality.
    Restricted to sandbox and project directories.
    """
    name: str = "code_editor"
    auth_level: str = "worker"
    summary: Optional[str] = "Safe code editing using multiple replace chunks or diffs."
    description: str = (
        "Advanced code editing capabilities for precise modifications. "
        "Allows modifying multiple non-contiguous segments of code in a single file safely. "
        "Actions: 'replace_chunks'. Note: Each chunk's 'search' + 'replace' text is strictly limited to a combined 16384 characters. If editing massive files, split into multiple chunks or calls."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation: 'replace_chunks'.",
            required=True,
        ),
        ToolParameter(
            name="scope",
            type="string",
            description="Target scope: 'private', 'shared', or 'projects'. Defaults to 'shared'.",
            required=False,
        ),
        ToolParameter(
            name="filename",
            type="string",
            description="Relative path to the file within the specified scope.",
            required=True,
            aliases=["filepath", "file", "path", "file_path"]
        ),
        ToolParameter(
            name="chunks",
            type="array",
            description="An array of objects. Each object MUST have a 'replace' string. To target lines, provide 'start_line' and 'end_line' integers. To target text, provide a 'search' string.",
            required=True,
            aliases=["replacements", "replace_chunks", "edits", "replacements_json", "modifications"]
        )
    ]

    WRITE_ALIASES = frozenset({"write_file", "write", "create_file"})

    def params_not_required_for(self, args: Dict[str, Any]) -> set:
        """Round 1z: required parameters that an action does not use (asked by the executor before it rejects a call)."""
        action = str((args or {}).get("action", "")).strip().strip("<>/\"' \t").lower()
        if action in self.WRITE_ALIASES or is_write_action(action) or action in CODE_EDITOR_READ_ALIASES:
            return {"chunks"}
        # Round 2d: one edit given as top-level arguments is turned into 'chunks' by execute(); without an action the tool
        # can only mean its one edit action, 'replace_chunks'.
        has_chunks = any((args or {}).get(key) is not None for key in ("chunks", "replacements", "edits"))
        has_single_chunk = single_chunk_from_args(args or {}) is not None
        not_needed = set()
        if not has_chunks and has_single_chunk:
            not_needed.add("chunks")
        if not action and (has_chunks or has_single_chunk):
            not_needed.add("action")
        return not_needed

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:

        action = kwargs.get("action")
        if isinstance(action, str):
            # Round 1y: W1 sent 'replace_chunks>' (a stray tag character, 2026-10-17 23:59); file_system cleans this since 1s.
            action = action.strip().strip("<>/\"' \t").lower()
        # Handle action aliases
        if action in EDIT_ACTION_ALIASES:
            action = "replace_chunks"
        if not action and (kwargs.get("chunks") is not None or single_chunk_from_args(kwargs) is not None):
            action = "replace_chunks"  # Round 2d: W1 called code_editor without an action (2026-10-18 23:14, 23:16)
            
        default_scope = "shared" if project_name and session_name else "private"
        scope = kwargs.get("scope", default_scope).lower()
        filename = kwargs.get("filename") or kwargs.get("filepath") or kwargs.get("file") or kwargs.get("path") or kwargs.get("file_path")
        if isinstance(filename, str):
            filename = filename.strip()

        # Round 1y: code_editor only edits. W1 asked it for 'read_file_with_lines' (2026-10-17 22:18) and got "not supported".
        if action in CODE_EDITOR_READ_ALIASES and filename:
            from src.tools.file_system import FileSystemTool
            read_args = {"action": "read", "path": filename, "scope": scope}
            for key in ("start_line", "end_line"):
                if kwargs.get(key) is not None:
                    read_args[key] = kwargs[key]
            read_result = await FileSystemTool().execute(
                agent_id=agent_id, agent_sandbox_path=agent_sandbox_path,
                project_name=project_name, session_name=session_name, **read_args
            )
            if isinstance(read_result, dict) and read_result.get("status") == "success":
                note = "[Note: code_editor only edits files; this was read with file_system action 'read'.]"
                read_result["message"] = f"{note} {read_result.get('message') or ''}".strip()
            return read_result

        # Gracefully handle write_file called on code_editor
        if action in self.WRITE_ALIASES or is_write_action(action):
            # Round 2c: also 'write_file_content' with 'file_contents' (2026-10-18 22:01: "Missing required parameters: chunks")
            file_content = next((kwargs[key] for key in WRITE_CONTENT_KEYS if kwargs.get(key) is not None), None)
            if file_content is not None and filename:
                logger.info(f"CodeEditorTool: Handled '{action}' alias for '{filename}' directly.")
                from src.tools.file_system import FileSystemTool
                return await FileSystemTool().execute(
                    agent_id=agent_id, agent_sandbox_path=agent_sandbox_path,
                    project_name=project_name, session_name=session_name,
                    action="write", path=filename, content=str(file_content)
                )

        if action != "replace_chunks":
            return {"status": "error", "message": f"Action '{action}' is not supported. Use 'replace_chunks'."}

        chunks = kwargs.get("chunks") or kwargs.get("replacements") or kwargs.get("edits")
        single_chunk_note = ""
        if not chunks:
            # Round 2d: ONE edit sent as top-level arguments (search_block/replace_block + start_line/end_line ...)
            single_chunk = single_chunk_from_args(kwargs)
            if single_chunk is not None:
                chunks = [single_chunk]
                single_chunk_note = (" [Note: your edit was given as top-level arguments; it was applied as one chunk. "
                                     "Next time put it in 'chunks': [{\"search\": ..., \"replace\": ...}].]")
        if not filename or not chunks:
            return {"status": "error", "message": "Missing 'filename' or 'chunks' parameter."}
            
        if isinstance(chunks, str):
            try:
                # Round 1v: strict=False accepts raw line breaks/tabs inside the JSON strings (the 4B model sends code that
                # way: 6 rejections "Invalid control character" on 2026-10-16). Valid JSON parses exactly as before.
                chunks = _loads_chunks(chunks)  # Round 1y: also accepts stray ']' / '}' after the array ("Extra data")
            except Exception as e1:
                # Round 2d: a backslash that starts no JSON escape ('\\"\\"\\"\\CLI', 2026-10-18 22:33 and 22:35) is a literal
                # backslash. Only those backslashes change; everything else is parsed exactly as sent.
                try:
                    chunks = _loads_chunks(_close_open_brackets(_repair_json_escapes(chunks)))
                    logger.info("CodeEditorTool: 'chunks' parsed after repairing invalid backslash escapes / missing closing brackets.")
                except Exception:
                    # JSON Resilience Layer
                    try:
                        # Try to fix common JSON errors (single quotes, trailing commas, missing closing brackets)
                        fixed_str = chunks.strip()
                        # Remove markdown formatting if present
                        if fixed_str.startswith("```json"):
                            fixed_str = fixed_str[7:]
                        elif fixed_str.startswith("```"):
                            fixed_str = fixed_str[3:]
                        if fixed_str.endswith("```"):
                            fixed_str = fixed_str[:-3]

                        # Fix single quotes to double quotes, being careful around escaped quotes
                        # This is a naive regex but helps with simple LLM errors
                        fixed_str = re.sub(r"(?<!\\)'", '"', fixed_str)
                        # Fix trailing commas
                        fixed_str = re.sub(r',\s*([}\]])', r'\1', fixed_str)

                        chunks = json.loads(fixed_str)
                    except Exception as e2:
                        try:
                            # AST fallback for single-quote JSON
                            parsed = _literal_eval_chunks(chunks.strip())  # Round 2c: also JSON null/true/false
                            if isinstance(parsed, list):
                                chunks = parsed
                            else:
                                raise ValueError("AST evaluated to non-list")
                        except Exception as e3:
                            return {"status": "error", "message": f"'chunks' was provided as a string but is not valid JSON. Ensure you use an array of objects. Parse error: {e1}. Repair attempt failed: {e3}"}

        if isinstance(chunks, dict) and any(key in chunks for key in CHUNK_REPLACE_KEYS):
            chunks = [chunks]  # Round 2d: one chunk object without the surrounding list
        if not isinstance(chunks, list):
            return {"status": "error", "message": "'chunks' must be a list of dicts with 'search' and 'replace' keys."}

        for idx, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                return {"status": "error", "message": f"Chunk at index {idx} is invalid (got {type(chunk).__name__}). 'chunks' must be a list of JSON objects containing 'search' and 'replace' keys."}

        # --- FIX +42: Guard against oversized chunks that cause LLM JSON overflow ---
        MAX_CHUNKS = 20
        MAX_CHUNK_SIZE_CHARS = 32768  # 32KB per individual chunk (increased from 16384)
        
        if len(chunks) > MAX_CHUNKS:
            return {"status": "error", "message": f"Too many chunks ({len(chunks)}). Maximum is {MAX_CHUNKS}. Please split your edits into multiple smaller tool calls."}
        
        for idx, chunk in enumerate(chunks):
            if isinstance(chunk, dict):
                search_len = len(str(chunk.get('search', '')))
                replace_len = len(str(chunk.get('replace', '')))
                if search_len + replace_len > MAX_CHUNK_SIZE_CHARS:
                    # Auto-split oversized chunk logic is too complex for this tool's exact search match, 
                    # so we instruct the LLM to do it instead.
                    return {"status": "error", "message": f"Chunk {idx} is too large ({search_len + replace_len} chars, max {MAX_CHUNK_SIZE_CHARS}). Split this edit into multiple smaller chunks by separating your changes into distinct search/replace blocks."}

        # --- Base path logic identical to file_system ---
        base_path: Optional[Path] = None
        scope_description = ""
        if scope == "private":
            base_path = agent_sandbox_path; scope_description = f"agent {agent_id}'s private sandbox"
            if not base_path.is_dir(): return {"status": "error", "message": f"Sandbox does not exist: {base_path}"}
        elif scope == "shared":
            if not project_name or not session_name: return {"status": "error", "message": "Missing project/session context."}
            safe_project_name = project_name.replace(" ", "_").strip()
            base_path = settings.PROJECTS_BASE_DIR / safe_project_name / session_name / "shared_workspace"
            scope_description = f"shared workspace"
            if not base_path.is_dir():
                try: 
                    await asyncio.to_thread(base_path.mkdir, parents=True, exist_ok=True)
                except Exception as e: return {"status": "error", "message": f"Could not create shared workspace: {e}"}
        elif scope == "projects":
            base_path = settings.PROJECTS_BASE_DIR
            scope_description = "main projects directory"
            if not base_path.is_dir(): return {"status": "error", "message": "Projects dir does not exist."}
        else:
            return {"status": "error", "message": "Invalid scope. Use 'private', 'shared', or 'projects'."}

        try:
            rel_file_path = filename.lstrip('/') or '.'
            if '..' in Path(rel_file_path).parts:
                return {"status": "error", "message": "Unsafe path containing '..'"}
            abs_path = (base_path / Path(rel_file_path)).resolve()
            
            # Additional code_editor constraint: Can only edit files in the allowed workspaces.
            try:
                abs_path.relative_to(base_path.resolve())
            except ValueError:
                return {"status": "error", "message": f"Security restriction: The file at {abs_path} is outside the allowed {scope} path."}

            if not abs_path.is_file():
                return {"status": "error", "message": f"File '{filename}' does not exist or is a directory."}
                
            original_content = await asyncio.to_thread(abs_path.read_text, encoding="utf-8")
            
            # Apply all chunks
            content = original_content
            successful = 0
            approximate_matches: List[str] = []  # Round 1z: chunks matched by fuzzy_line_window()
            line_hint_corrections: List[str] = []  # Round 2d: chunks whose line numbers were off but whose search text was not
            boundary_overlaps: List[str] = []  # Round 2e: chunks whose new text repeated the lines next to their range
            already_applied: List[str] = []  # Round 2e: line chunks whose exact text is already at their start line
            removed_statements: List[str] = []  # Round 2e: return/def/import/... lines a line chunk replaced without keeping
            used_line_numbers = False  # Round 2e: for the stale-line-number hint of a syntax rejection
            errors = []
            
            # Sort chunks to prevent line shifting collisions
            # Process line-based edits from bottom to top
            def get_start_line(c):
                sl = c.get('start_line') or c.get('from_line') or c.get('line_start')
                first, _ = parse_line_numbers(sl)  # Round 2d: also '43, 52'
                return first if first is not None else -1
            
            # Use stable sort: line-based chunks descending, search chunks stay in relative order
            chunks = sorted(chunks, key=get_start_line, reverse=True)
            
            for idx, chunk in enumerate(chunks):
                if not isinstance(chunk, dict):
                    errors.append(f"Chunk {idx} is malformed (expected a JSON object).")
                    continue
                    
                # Support semantic aliases for replace text
                replace_key = next((key for key in CHUNK_REPLACE_KEYS if key in chunk), None)  # Round 2e: + text, content ...
                replace_text = chunk.get(replace_key) if replace_key else None
                if replace_text is None:
                    # Round 1x: say how to delete lines; W1 sent {'start_line': 31} chunks without 'replace' four times.
                    errors.append(f"Chunk {idx} is malformed (missing 'replace' key). Every chunk needs 'replace' with the new "
                                  "text; to DELETE lines give start_line, end_line and \"replace\": \"\".")
                    continue
                    
                start_line = chunk.get('start_line') or chunk.get('from_line') or chunk.get('line_start') or chunk.get('start')
                end_line = chunk.get('end_line') or chunk.get('to_line') or chunk.get('line_end') or chunk.get('end')
                if isinstance(start_line, str):
                    # Round 2d: W1 sent start_line '43, 52' (with end_line '52'): the first number is the start, the second
                    # the end when no end_line is given
                    first, range_last = parse_line_numbers(start_line)
                    if first is not None:
                        start_line = first
                        if end_line is None and range_last is not None:
                            end_line = range_last
                search_text = (
                    chunk.get('search') if 'search' in chunk else
                    chunk.get('search_block') if 'search_block' in chunk else
                    chunk.get('search_text') if 'search_text' in chunk else
                    chunk.get('find') if 'find' in chunk else
                    chunk.get('old') if 'old' in chunk else
                    chunk.get('old_code')
                )
                if isinstance(search_text, str) and not search_text.strip():
                    # Round 1x: an empty 'search' matched "987 times" and was rejected as ambiguous. It means "no search".
                    search_text = None

                # Single-line inference if only one bound is provided
                if start_line is not None and end_line is None and search_text is None:
                    end_line = start_line
                elif end_line is not None and start_line is None and search_text is None:
                    start_line = end_line
                
                if start_line is not None and end_line is not None:
                    try:
                        start_line = int(start_line)
                        end_line = int(end_line)
                    except ValueError:
                        errors.append(f"Chunk {idx}: start_line and end_line must be integers.")
                        continue
                        
                    used_line_numbers = True
                    lines = content.splitlines(True)
                    start_idx = max(0, start_line - 1)
                    end_idx = min(len(lines), end_line)
                    # Round 1y: an EMPTY file has no lines; start_line 1 (end_line 0 or 1) inserts the text into it. This used
                    # to fail with "Invalid range for file with 0 lines" for 11 hours on the emptied src/temp_convert.py.
                    empty_file_insert = not lines and start_idx == 0 and end_idx == 0 and end_line >= 0

                    if not empty_file_insert and (start_idx >= len(lines) or start_idx > end_idx):
                        if not lines:
                            errors.append(f"Chunk {idx}: Invalid start_line/end_line range: the file is EMPTY (0 lines). "
                                          "Use start_line 1 and end_line 1 to insert text, or file_system 'write'.")
                        else:
                            errors.append(f"Chunk {idx}: Invalid start_line/end_line range ({start_line}-{end_line}) for file "
                                          f"with {len(lines)} lines. Lines are numbered 1 to {len(lines)} and end_line must "
                                          "not be smaller than start_line.")
                        continue
                    if search_text is not None:
                        # Round 1y: line numbers AND search text: they must point to the same code, or nothing is changed.
                        conflict = _line_range_conflict(lines, start_idx, end_idx, search_text)
                        if conflict:
                            # Round 2d: the line numbers are a hint. If the search text occurs ONCE from one line before
                            # start_line up to end_line (off by one, or a fragment inside the range), the search text decides.
                            near = unique_search_near_lines(content, search_text, start_line, end_line)
                            if near is None or not line_hint_edit_allowed(content, near[0], near[2], start_line,
                                                                          search_text, replace_text):
                                errors.append(f"Chunk {idx}: {conflict}")
                                continue
                            match_start, match_end, found_line, exact = near
                            replacement = replace_text
                            after = content[match_end:]
                            if not exact and replacement and not replacement.endswith('\n') and after and not after.startswith('\n'):
                                replacement += '\n'
                            content = content[:match_start] + replacement + after
                            successful += 1
                            line_hint_corrections.append(f"chunk {idx}: lines {start_line}-{end_line} did not start with your "
                                                         f"search text; it was found at line {found_line} and replaced there")
                            continue
                        
                    if search_text is None and replacement_already_in_place(lines, start_idx, end_idx, replace_text):
                        # Round 2e: 03:48 W1 re-sent its 03:47 chunk (lines 24-26 -> 5 lines) inside a new call; applied
                        # again it would have replaced 3 of the 5 lines it had already written with the same 5 lines.
                        successful += 1
                        already_applied.append(f"chunk {idx}: lines {start_line}-{start_idx + len(str(replace_text).splitlines())} "
                                               "already hold exactly this text (it was applied before), so it was not applied again")
                        continue
                    replacement = replace_text
                    # Round 1x: keep the line break the replaced lines had. "replace": "" now really deletes the lines (it
                    # used to leave an empty line), and replacing the last line no longer drops the file's final newline.
                    replaced_ends_with_newline = lines[end_idx - 1].endswith('\n') if end_idx > start_idx else end_idx < len(lines)
                    if replacement and not replacement.endswith('\n') and replaced_ends_with_newline:
                        replacement += '\n'
                        
                    before = "".join(lines[:start_idx])
                    after = "".join(lines[end_idx:])
                    new_content = before + replacement + after
                    lost = removed_statement_lines(lines, start_idx, end_idx, replacement)
                    if lost:
                        # Round 2e: 03:47 W1's chunk for lines 24-26 silently removed 'return c * Decimal('1.8') + 32'
                        removed_statements.append(f"chunk {idx} removed " + "; ".join(lost))
                    if filename.endswith('.py'):
                        # Round 2e: the new text repeats the line(s) right after (or before) the range ('def f(...):' twice)
                        repaired = repair_boundary_overlap(content, lines, start_idx, end_idx, replacement, new_content)
                        if repaired is not None:
                            new_content, overlap_note = repaired
                            boundary_overlaps.append(f"chunk {idx}: {overlap_note}")
                    content = new_content
                    successful += 1
                    continue
                elif search_text is not None:
                    occurrences = content.count(search_text)
                    if occurrences == 0:
                        # Fallback to AST literal_eval if json.loads fails
                        # Try regex match ignoring all whitespace
                        pieces = [re.escape(p) for p in search_text.split() if p]
                        if not pieces:
                            errors.append(f"Chunk {idx}: Search text is empty or only whitespace.")
                            continue
                            
                        regex_pattern = r'\s*'.join(pieces)
                        # allow agents to use ... to skip lines
                        regex_pattern = regex_pattern.replace(re.escape('...'), r'[\s\S]*?')
                        matches = list(re.finditer(regex_pattern, content))
                        
                        if len(matches) == 1:
                            match = matches[0]
                            before = content[:match.start()]
                            after = content[match.end():]
                            
                            replacement = replace_text
                            if not replacement.endswith('\n') and after and not after.startswith('\n'):
                                replacement += '\n'
                                
                            content = before + replacement + after
                            successful += 1
                            continue
                        elif len(matches) > 1:
                            errors.append(f"Chunk {idx}: Search text is ambiguous (found {len(matches)} times ignoring whitespace).")
                            continue
                        else:
                            # Round 1z: last resort = ONE block of whole lines that is >= 90 % similar (was diff_match_patch,
                            # which patched wherever the text half-fitted and corrupted files).
                            window = fuzzy_line_window(content, search_text)
                            if window:
                                first_line, last_line = window[2], window[3]
                                content = replace_fuzzy_window(content, window, replace_text)
                                successful += 1
                                approximate_matches.append(f"chunk {idx}: lines {first_line}-{last_line}")
                                continue
                                
                        # Provide fuzzy match attempt context
                        import difflib
                        search_lines = search_text.splitlines()
                        content_lines = content.splitlines()
                        if search_lines and content_lines:
                            first_line = next((line for line in search_lines if line.strip()), None)
                            if first_line:
                                close_matches = difflib.get_close_matches(first_line, content_lines, n=1, cutoff=0.6)
                                if close_matches:
                                    best_line = close_matches[0]
                                    line_idx = content_lines.index(best_line)
                                    start_idx = max(0, line_idx - 2)
                                    end_idx = min(len(content_lines), line_idx + len(search_lines) + 2)
                                    context = '\n'.join(content_lines[start_idx:end_idx])
                                    errors.append(f"Chunk {idx}: Search text not found exactly. Found a close match here:\n```\n{context}\n```\nCRITICAL: If the block above is the section you want to edit, you MUST copy it EXACTLY including all indentation and spacing. If it is NOT the correct section, you MUST use the `file_system` tool with `action='read'` to review the file contents to find the correct exact text before trying to edit again.")
                                    continue
                                    
                        errors.append(f"Chunk {idx}: Search text not found in file. Ensure exact matching. Use the `file_system` tool with `action='read'` to review the file contents to find the correct exact text before trying again.")
                    elif occurrences > 1:
                        errors.append(f"Chunk {idx}: Search text is ambiguous (found {occurrences} times). Please make the search block larger/more unique, or use start_line and end_line.")
                    else:
                        new_content = content.replace(search_text, replace_text)
                        if filename.endswith('.py'):
                            # Round 2e: same repair for a search that covers whole lines (W1 03:56/03:58 searched for the
                            # module docstring and replaced it with imports + the 'def' line that follows it)
                            match_at = content.find(search_text)
                            span = whole_line_span(content, match_at, match_at + len(search_text))
                            if span is not None:
                                repl = replace_text if str(replace_text).endswith("\n") else str(replace_text) + "\n"
                                repaired = repair_boundary_overlap(content, content.splitlines(True), span[0], span[1], repl,
                                                                   new_content)
                                if repaired is not None:
                                    new_content, overlap_note = repaired
                                    boundary_overlaps.append(f"chunk {idx}: {overlap_note}")
                        content = new_content
                        successful += 1
                else:
                    errors.append(f"Chunk {idx} is malformed: Provide either 'start_line'/'end_line' OR 'search'.")
                    continue
            
            if errors:
                # Round 1z: chunks are applied one after another in memory, so text shown for a later chunk already contains
                # the earlier chunks' changes - which were NOT saved (2026-10-18 06:54:09 showed lines that were not in the file).
                earlier_note = ("\nNote: chunks are applied in order (line-number chunks from the bottom up). Text shown above "
                                "for a failed chunk already includes the changes of the chunks before it in this call, but "
                                "NOTHING was saved: read the file again before retrying.") if successful else ""
                return {
                    "status": "error",
                    "message": f"Code edit failed: {successful} chunks succeeded, but {len(errors)} failed issues occurred.\n" + "\n".join(errors) + "\nNo changes were saved." + earlier_note
                }
                
            emptied = emptied_file_error(filename, original_content, content)
            if emptied:
                logger.warning(f"CodeEditorTool: refused edit by '{agent_id}' that would empty '{filename}'.")
                return {"status": "error", "message": emptied}

            # Perform basic Python syntax validation if modifying a python file
            if filename.endswith('.py'):
                try:
                    import ast
                    ast.parse(content)
                except SyntaxError as syntax_err:
                    try:  # Round 1v: same code-around-the-error report as file_system writes
                        from src.tools.python_static_check import syntax_error_report
                        syntax_report = syntax_error_report(content, syntax_err)
                    except Exception:
                        syntax_report = ""
                    return {
                        "status": "error",
                        "message": f"Code edit rejected due to Python SyntaxError in resulting code: {syntax_err.msg} at line {syntax_err.lineno}. No changes were saved. Please check your indentation and brackets."
                                   + (f"\n{syntax_report}" if syntax_report else "")
                                   # Round 2e: 03:48 W1's second chunk used line numbers from before its own 03:47 edit
                                   + ("\nIf you used line numbers: they may be OUT OF DATE - every earlier edit that added or "
                                      "removed lines moved the lines below it. Read the file again and use its current line "
                                      "numbers." if used_line_numbers else "")
                    }
            
            # Save if all success
            await asyncio.to_thread(abs_path.write_text, content, encoding="utf-8")
            code_notes_text = ""
            if filename.endswith('.py'):
                # Round 2d: the same non-blocking hints as a file_system 'write' (a docstring-only function, a missing import)
                try:
                    from src.tools.python_static_check import python_code_notes_for
                    code_notes = python_code_notes_for(content, filename, base_path)  # Round 2e: + import suggestions
                except Exception:
                    code_notes = []
                if code_notes:
                    code_notes_text = ("\n[Framework check - the edit WAS saved, but please fix:]\n- " + "\n- ".join(code_notes))
            return {
                "status": "success",
                "message": f"Successfully applied {successful} edits to '{filename}'."
                           + (f" Note: the search text was not found exactly; it was matched approximately "
                              f"({'; '.join(approximate_matches)}). Read those lines to check the result."
                              if approximate_matches else "")
                           + (f" Note: {'; '.join(line_hint_corrections)}. Line numbers may have changed: read the file "
                              "again before the next line-based edit." if line_hint_corrections else "")
                           + (f" Note: {'; '.join(boundary_overlaps)}. Read the file again before the next line-based edit."
                              if boundary_overlaps else "")
                           + (f" Note: {'; '.join(already_applied)}." if already_applied else "")
                           + (f" WARNING: {' | '.join(removed_statements)} - these lines are NOT in your new text any more. "
                              "If they were still needed, add them back." if removed_statements else "")
                           + single_chunk_note
                           + code_notes_text
            }

        except Exception as e:
            logger.error(f"Error in code_editor tool: {e}\n{traceback.format_exc()}")
            return {"status": "error", "message": f"Unhandled error during edit: {str(e)}"}
            
    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        usage = """
        **Tool Name:** code_editor

        **Description:** Safely applies multiple targeted substring replacements in a single operation. This avoids having to call `file_system` multiple times for different blocks.

        **Parameters:**
        *   **action:** (string, required) - Must be 'replace_chunks'.
        *   **filename:** (string, required) - Relative path to the file.
        *   **chunks:** (array, required) - Array of objects containing 'replace' and either 'start_line' & 'end_line' (preferred) or 'search' (fallback).
        *   **scope:** (string, optional) - Target scope ('private', 'shared', or 'projects').

        **Example:**
        ```json
        {
            "action": "replace_chunks",
            "filename": "src/main.py",
            "chunks": [
                {
                    "search": "def old_function():\n    print('A')",
                    "replace": "def new_function():\n    print('B')"
                },
                {
                    "search": "x = 10",
                    "replace": "x = 20"
                }
            ]
        }
        ```
        Important Notes:
        - The `search` block must match existing content EXACTLY ONCE (including all whitespace, newlines, and indentation).
        - If you encounter a 'Search text not found exactly' error, DO NOT guess the whitespace. Use the `file_system` tool with `action='read'` to view the file contents and copy the EXACT text before retrying.
        - If a search string appears multiple times or zero times, the entire tool call is rejected.
        - Use EITHER 'start_line'/'end_line' OR 'search' in a chunk. If you give both, the lines must start with the search
          text, or the search text must occur exactly once inside those lines (or on the line just before them); otherwise
          nothing is changed.
        - One edit can also be sent without 'chunks': "search" (or "search_block") and "replace" (or "replace_block") as
          top-level parameters, optionally with "start_line" and "end_line".
        - To delete lines use "replace": "". An edit that would leave the file empty is refused.
        """
        return usage.strip()
