# START OF FILE src/tools/file_system.py
import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging
import re # Import re for find_replace
import shutil # Added for copy and move
import datetime # Added for exists/stat
import difflib # Added for fuzzy filename suggestions
from diff_match_patch import diff_match_patch # Added for search_replace_block
import git # Added for git integration
from git.exc import InvalidGitRepositoryError, GitCommandError

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings # For PROJECTS_BASE_DIR

logger = logging.getLogger(__name__)

class _EditRefused(Exception):
    """Round 1y: raised inside search_replace_block when the result must not be saved (the message says why)."""


# Round 2t: 2026-10-21 20:55-21:00 W1 wrote src/app.py in pieces as 'src/app.py.part1', 'src/app.py.part2',
# 'src/app.py.part1.txt' ... (6 writes, 6 deletes), while src/app.py itself was never created. Nothing ever joins such files.
_PART_FILE_RE = re.compile(r"^(?P<real>.+\.[A-Za-z0-9]{1,10})\.(?:part|chunk|piece)[_-]?\d+(?:\.(?:txt|tmp|bak))?$",
                           re.IGNORECASE)


def part_file_target(filename: Optional[str]) -> Optional[str]:
    """'src/app.py' for 'src/app.py.part1' / 'src/app.py.part2.txt' / 'src/app.py.chunk_1'; None for ordinary names."""
    text = str(filename or "").strip().replace("\\", "/")
    if not text:
        return None
    directory, _, name = text.rpartition("/")
    match = _PART_FILE_RE.match(name)
    if not match:
        return None
    return f"{directory}/{match.group('real')}" if directory else match.group("real")


def rejected_write_state_note(filename: str, existed: bool) -> str:
    """
    Round 2t: what a rejected 'write' left behind. 2026-10-21 20:52:43 W1's first src/app.py was rejected (a 'try' without
    'except'); W1 then edited the file it believed existed ("rewrite this file using code_editor") and was told twice
    that src/app.py does not exist.
    """
    if existed:
        return f" The existing '{filename}' is unchanged."
    # Round 2v: the rejected text is kept, so only the faulty line has to be sent (src/tools/rejected_drafts.py)
    from src.tools.rejected_drafts import kept_note
    return (f" '{filename}' was NOT created and still does not exist." + kept_note(filename)
            + f" Or send the COMPLETE corrected file with 'write' (if it is too long for one reply: its first part, whole "
            f"functions or classes only, then the rest with 'append' to '{filename}').")


def python_append_error(filename: str, original: str, new_content: str) -> Optional[Dict[str, Any]]:
    """
    Round 2t: the refusal of an 'append' that turns valid Python into invalid Python, else None. A file that was already
    broken is never blocked (the append may be its repair).
    """
    from src.tools.python_static_check import parse_python, syntax_error_report
    try:
        parse_python(new_content)
        return None
    except SyntaxError as syntax_err:
        try:
            parse_python(original)
        except SyntaxError:
            return None
        try:
            report = syntax_error_report(new_content, syntax_err)
        except Exception:
            report = ""
        state = (f"'{filename}' is unchanged." if original else f"'{filename}' was NOT created.")
        return {"status": "error", "message": (
            f"Append rejected: '{filename}' would not be valid Python after it ({syntax_err.msg} at line {syntax_err.lineno}). "
            f"Nothing was appended; {state} Append whole functions or classes: every part must end where a function or "
            "class ends." + (f"\n{report}" if report else ""))}


def checked_append_error(filename: str, original: str, new_content: str) -> Optional[Dict[str, Any]]:
    """
    Round 2x: python_append_error for a JavaScript / JSON / shell file (src/tools/source_check.py): the refusal of an
    'append' that turns a valid file into an invalid one, else None. Runs a checker program (call it in a thread).
    """
    from src.tools.source_check import blocking_issue, check_source, issue_report
    issue = blocking_issue(new_content, filename)
    if issue is None:
        return None
    if original and original.strip() and check_source(original, filename) is not None:
        return None  # the file was already broken: never block an attempt to repair it
    state = (f"'{filename}' is unchanged." if original else f"'{filename}' was NOT created.")
    return {"status": "error", "message": (
        f"Append rejected: '{filename}' would not be valid {issue.kind} after it ({issue.summary()}). Nothing was appended; "
        f"{state} Append whole functions, classes or statements: every part must end where one ends."
        + "\n" + issue_report(issue, new_content))}


def append_text(original: str, content: str) -> str:
    """Round 2x: what an 'append' adds to original - content, on a new line if original does not end with one."""
    if original and not original.endswith("\n") and not content.startswith("\n"):
        return "\n" + content
    return content


def replace_line_range(text: str, start_line: int, end_line: int, content: str) -> str:
    """Round 2v: text with lines start_line..end_line (1-based, inclusive) replaced - exactly as 'replace_lines' does it."""
    lines = text.splitlines(True)
    start_idx = max(0, start_line - 1)
    end_idx = max(start_idx, min(end_line, len(lines)))
    new_lines = content.splitlines(True)
    if new_lines and not new_lines[-1].endswith('\n') and end_idx < len(lines):
        new_lines[-1] += '\n'
    return "".join(lines[:start_idx] + new_lines + lines[end_idx:])


def bloat_refusal(rel_target: Path, allowed_entries, structure_text: str) -> str:
    """
    Round 2v: the Workspace Bloat Protection refusal with the real directories and, when PROJECT_STRUCTURE.md plans a file
    of that name, its planned path. 2026-10-22 10:33:02 W1 wrote 'backend/server.py' and was given a list that mixed the
    project's files with a dozen generic folder names, but not that server.py belongs in the workspace root.
    """
    directories = sorted(entry for entry in allowed_entries if not _EXTENSION_LIKE_RE.search(entry))
    planned = []
    in_files = False
    for line in str(structure_text or "").splitlines():
        if line.startswith("## "):
            in_files = line.strip().lower() == "## files to create"
            continue
        if in_files and line.startswith("- "):
            planned.append(line[2:].strip())
    same_name = [path for path in planned if Path(path).name == rel_target.name]
    message = (f"Workspace Bloat Protection: '{rel_target.parts[0]}/' is not a directory of this project's layout "
               f"(PROJECT_STRUCTURE.md), so NOTHING was written. Use the workspace root or one of these directories: "
               f"{', '.join(directories)}.")
    if same_name:
        message += f" PROJECT_STRUCTURE.md plans this file as '{same_name[0]}': write it at exactly that path."
    return message


_EXTENSION_LIKE_RE = re.compile(r"\.[A-Za-z0-9]{1,10}$")


def part_file_refusal(filename: str, real: str, real_exists: bool) -> Dict[str, Any]:
    """Round 2t: the refusal of a write / append to a part file of `real`."""
    if real_exists:
        how = (f"'{real}' exists, so add your next part to it with file_system action 'append' and filename '{real}' "
               "(whole functions or classes per call).")
    else:
        how = (f"'{real}' does not exist yet: create it with file_system action 'write', filename '{real}' and the first "
               f"part (whole functions or classes only), then add every further part with 'append' to '{real}'.")
    return {"status": "error", "error_type": "part_file", "message": (
        f"Refused: '{filename}' would be a PART of '{real}'. Nothing ever joins part files, so nothing was written. Write "
        f"the code into '{real}' itself: {how}")}


class FileSystemTool(BaseTool):
    """
    Tool for reading, writing, listing files/directories, creating directories,
    deleting files/empty directories, and modifying files within different scopes:
    agent's private sandbox, a shared session workspace, or the main projects directory.
    Ensures operations are restricted to the designated scope and path.
    """
    name: str = "file_system"
    auth_level: str = "worker" # Accessible by all
    summary: Optional[str] = "Performs file system operations (read, write, append, insert_lines, replace_lines, list, tree, mkdir, delete, find_replace, regex_replace, copy, move, rename, exists) within allowed scopes. Use tool_information with sub_action for per-action help."
    description: str = ( # Round 2v: kept in line with what the actions really do
        "Reads, writes, appends, inserts, or replaces lines in files. Also lists, creates, moves, or renames files/directories. "
        "Use 'scope' ('private', 'shared', or 'projects') to specify the target area. Default: 'shared' when you work on a "
        "project, else 'private'. "
        "Actions: 'read', 'write' (for NEW files), 'append' (add to end), 'insert_lines', 'replace_lines', "
        "'list', 'tree' (recursive listing), 'mkdir', 'delete', 'find_replace', 'regex_replace', 'copy', 'move', 'rename', "
        "'exists' (check file/dir existence and metadata), 'search_replace_block', 'git_init', 'git_add', 'git_commit', "
        "'git_status', 'git_diff', 'git_log', 'git_branch', 'git_checkout', 'git_pull', 'git_push'. "
        "All paths are relative to the selected scope. Python, JavaScript (.js/.mjs/.cjs), JSON and shell (.sh/.bash) files "
        "must stay valid: a write or change that breaks one is rejected and nothing is saved. When the 'write' of a NEW "
        "such file is rejected, its text is kept: fix the faulty line with 'find_replace' or 'replace_lines' on that file, "
        "or add the rest with 'append', instead of sending the whole file again. Write code into the real file (never part "
        "files such as 'app.py.part1')."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation: 'read', 'write', 'append', 'insert_lines', 'replace_lines', 'find_replace', 'regex_replace', 'search_replace_block', 'list', 'tree', 'mkdir', 'delete', 'copy', 'move', 'rename', 'exists', 'git_init', 'git_add', 'git_commit', 'git_status', 'git_diff', 'git_log', 'git_branch', 'git_checkout', 'git_pull', 'git_push'.",
            required=True,
        ),
        ToolParameter(
            name="scope",
            type="string",
            description="Target scope: 'private' (agent's sandbox), 'shared' (session workspace), or 'projects' (top-level project list). Defaults to 'shared' for project workers, else 'private'.",
            required=False, # Dynamically defaulted
        ),
        ToolParameter(
            name="filename",
            type="string", 
            description="Relative path to the file within the scope. Required for 'read', 'write', 'append', 'insert_lines', 'replace_lines', 'find_replace', 'regex_replace' and 'search_replace_block'. Can also use 'filepath'.",
            required=False, # Dynamically required
            aliases=["filepath", "file", "file_path", "file_name", "target_file", "name"]
        ),
        ToolParameter(
            name="content",
            type="string",
            description="Text content. Required for 'write' (the COMPLETE file), 'append', 'insert_lines' and 'replace_lines'.",
            required=False, # Dynamically required
            aliases=["text", "data", "body", "code", "string", "content_str", "line_content", "file_contents"]
        ),
         ToolParameter(
            name="path",
            type="string",
            description="Relative path to a directory or file. Required for 'list', 'tree', 'mkdir', 'delete', 'copy' (as source), 'move' (as source), 'rename' (as source), 'exists'. For 'list'/'tree', defaults to '.' (scope root).",
            required=False, # Dynamically required by action
            aliases=["dir", "directory", "folder", "target_dir"]
        ),
        ToolParameter(
            name="destination_path",
            type="string",
            description="The target relative path. Required for 'copy', 'move', 'rename'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="find_text",
            type="string",
            description="The exact text string to find within the file. Required for 'find_replace'.",
            required=False, # Dynamically required
            aliases=["search", "find", "search_string", "search_text", "search_term"]
        ),
        ToolParameter(
            name="replace_text",
            type="string",
            description="The text to replace matches with. Required for 'find_replace', 'regex_replace'.",
            required=False, # Dynamically required
            aliases=["replace", "replacement", "replacement_text", "replace_string", "replace_term"]
        ),
        ToolParameter(
            name="regex_pattern",
            type="string",
            description="The regular expression pattern to find. Required for 'regex_replace'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="start_line",
            type="integer",
            description="Optional starting line number (1-indexed) for 'read'.",
            required=False,
        ),
        ToolParameter(
            name="show_line_numbers",
            type="boolean",
            description="Prefix each line with its line number for 'read'. Defaults to true.",
            required=False,
        ),
        ToolParameter(
            name="end_line",
            type="integer",
            description="Optional ending line number (1-indexed) for 'read'.",
            required=False,
        ),
        ToolParameter(
            name="insert_line",
            type="integer",
            description="Line number (1-indexed) where content should be inserted for 'insert_lines'. Alternatively, use '<search>' to find a text anchor and insert relative to it.",
            required=False,
            aliases=["line_number", "line", "at_line"]
        ),
        ToolParameter(
            name="replace_start_line",
            type="integer",
            description="Starting line number (1-indexed, inclusive) for 'replace_lines'. Also accepts 'start_line'.",
            required=False,
            aliases=["start_line_number", "from_line"]
        ),
        ToolParameter(
            name="replace_end_line",
            type="integer",
            description="Ending line number (1-indexed, inclusive) for 'replace_lines'. Also accepts 'end_line'.",
            required=False,
        ),
        ToolParameter(
            name="search_block",
            type="string",
            description="The exact (or slightly fuzzy) block of text to find. Required for 'search_replace_block'.",
            required=False,
            aliases=["search_string", "find"]
        ),
        ToolParameter(
            name="replace_block",
            type="string",
            description="The block of text to replace it with. Required for 'search_replace_block'.",
            required=False,
        ),
        ToolParameter(
            name="expected_replacements",
            type="integer",
            description="Optional. For 'search_replace_block': if the tool finds multiple matching blocks and reports the count N, you MUST re-send the call with this set to N to confirm replacing all N occurrences.",
            required=False,
        ),
        ToolParameter(
            name="start_marker",
            type="string",
            description="Optional for 'search_replace_block': a unique string marking the start of the block to replace. Can be used instead of 'search_block'.",
            required=False,
        ),
        ToolParameter(
            name="end_marker",
            type="string",
            description="Optional for 'search_replace_block': a unique string marking the end of the block to replace. Must be used with 'start_marker'.",
            required=False,
        ),
        ToolParameter(
            name="commit_message",
            type="string",
            description="The message for the git commit. Required for 'git_commit'.",
            required=False,
        ),
        ToolParameter(
            name="branch",
            type="string",
            description="Branch name. Used for 'git_checkout', and 'git_branch' (to create).",
            required=False,
        ),
        ToolParameter(
            name="files",
            type="string",
            description="Comma-separated files or pattern (e.g. '.'). Required for 'git_add' to stage files.",
            required=False,
        ),
        ToolParameter(
            name="remote",
            type="string",
            description="Git remote name (default: 'origin'). Used for 'git_push' and 'git_pull'.",
            required=False,
        ),
        ToolParameter(
            name="force_overwrite",
            type="boolean",
            description="Set to true ONLY when you intentionally want to replace the ENTIRE contents of an existing file via the 'write' action. Default is false. Use code_editor for partial edits.",
            required=False,
        ),
        ToolParameter(
            name="max_depth",
            type="integer",
            description="Maximum recursion depth for the 'tree' action. Default: 5. Set lower for large workspaces to reduce output size.",
            required=False,
        ),
    ]

    @staticmethod
    def _first_of(*values):
        """Return the first value that is not None. Preserves empty strings and other falsy values like 0."""
        for v in values:
            if v is not None:
                return v
        return None

    # --- Path hygiene (Round 1v) ----------------------------------------------------------------------------------------
    # 2026-10-16 20:39-21:31: PROJECT_STRUCTURE.md marked files as 'README.md  (planned)' and W3 copied the pattern: it
    # created a DIRECTORY 'docs/README.md (force)', wrote 'docs/README.md (new)' and ended with 'docs/README_final_v1.txt'.
    _ANNOTATION_RE = re.compile(r"^(?P<path>.*?[^\s_(])[\s_]+\((?P<note>[^()/]*)\)\s*$")
    # Extensions that make a name a FILE beyond doubt (used to refuse 'mkdir docs/README.md').
    FILE_LIKE_EXTENSIONS = frozenset({
        ".py", ".md", ".txt", ".rst", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".json",
        ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".bat", ".csv", ".xml", ".sql", ".java", ".c", ".cpp", ".h",
        ".go", ".rs", ".rb", ".php", ".lock",
    })

    @classmethod
    def _strip_path_annotation(cls, base_path: Path, value: Any):
        """(path, note): removes a trailing note in brackets such as ' (new)' / '_(force)' from a path that does not exist."""
        if not isinstance(value, str):
            return value, None
        match = cls._ANNOTATION_RE.match(value.strip())
        if not match:
            return value, None
        try:
            raw = value.strip().lstrip("/")
            if raw and ((base_path / raw).exists() or (base_path / raw.replace(" ", "_")).exists()):
                return value, None  # a real file/directory with that name: leave it alone
        except (OSError, ValueError):
            pass
        cleaned = match.group("path").strip()
        return cleaned, f"'{value.strip()}' was treated as '{cleaned}': a note in brackets is not part of a path."

    @staticmethod
    def _single_filename(value: Any) -> Optional[str]:
        """A single path from a 'filenames'-style list (list, JSON list string or plain string); None if not exactly one."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    import json as _json
                    value = _json.loads(text)
                except ValueError:
                    return None
            else:
                return text if text and "," not in text else None
        if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], str) and value[0].strip():
            return value[0].strip()
        return None

    @staticmethod
    def _python_edit_error(filename: str, original: str, new_content: str) -> Optional[Dict[str, Any]]:
        """
        Round 1v: an error result if an edit would turn a valid Python file into invalid Python, else None.
        Round 1y: also (for every file type) if the edit would leave a file that had content empty.
        Round 2x: also if it would turn a valid JavaScript / JSON / shell file into an invalid one (source_check.edit_rejection;
        runs a checker program, so async callers run this in a thread).
        """
        from src.tools.code_editor import emptied_file_error
        emptied = emptied_file_error(filename, original, new_content)
        if emptied:
            return {"status": "error", "message": emptied}
        if not str(filename).endswith(".py"):
            from src.tools.source_check import edit_rejection
            refused = edit_rejection(filename, original, new_content)
            return {"status": "error", "message": refused} if refused else None
        from src.tools.python_static_check import parse_python  # Round 2m: no SyntaxWarnings on the server's stderr
        try:
            parse_python(new_content)
            return None
        except SyntaxError as syntax_err:
            try:
                parse_python(original)
            except SyntaxError:
                return None  # the file was already broken: never block an attempt to repair it
            try:
                from src.tools.python_static_check import syntax_error_report
                report = syntax_error_report(new_content, syntax_err)
            except Exception:
                report = ""
            return {
                "status": "error",
                "message": f"Edit rejected: the result would not be valid Python ({syntax_err.msg} at line {syntax_err.lineno}). "
                           "No changes were saved. Check the indentation of the new text." + (f"\n{report}" if report else ""),
            }

    @staticmethod
    def _whitespace_tolerant_span(content: str, needle: str):
        """
        (start, end) character span of the whole lines in content that equal needle's lines when leading/trailing whitespace
        is ignored; 'ambiguous' if several places match; None if none does.
        """
        needle_lines = [line.strip() for line in needle.splitlines()]
        while needle_lines and not needle_lines[0]:
            needle_lines.pop(0)
        while needle_lines and not needle_lines[-1]:
            needle_lines.pop()
        if not needle_lines:
            return None
        file_lines = content.splitlines(keepends=True)
        stripped = [line.strip() for line in file_lines]
        size = len(needle_lines)
        starts = [i for i in range(len(file_lines) - size + 1) if stripped[i:i + size] == needle_lines]
        if not starts:
            return None
        if len(starts) > 1:
            return "ambiguous"
        start = sum(len(line) for line in file_lines[:starts[0]])
        end = start + sum(len(line) for line in file_lines[starts[0]:starts[0] + size])
        return start, end

    def _find_replace_text(self, text: str, find_text: str, replace_text: str, filename: str):
        """Round 2v: (new text, None) for find_replace on a text, or (None, error message) - same matching as find_replace."""
        if find_text in text:
            return text.replace(find_text, replace_text), None
        span = self._whitespace_tolerant_span(text, find_text)
        if span is None:
            return None, (f"find_text was NOT found in your kept (rejected) text of '{filename}', so nothing was changed. "
                          "It must match that text exactly, including indentation." + self._closest_lines_hint(text, find_text))
        if span == "ambiguous":
            return None, (f"find_text was not found exactly in your kept text of '{filename}', and ignoring indentation it "
                          "matches several places. Nothing was changed. Use a longer find_text or 'replace_lines'.")
        start, end = span
        replacement = replace_text
        if text[start:end].endswith("\n") and not replacement.endswith("\n"):
            replacement += "\n"
        return text[:start] + replacement + text[end:], None

    async def _edit_rejected_draft(self, base_path: Path, filename: str, validated_path: Path, agent_id: str,
                                   scope_description: str, edit, change: str = "change") -> Optional[Dict[str, Any]]:
        """
        Round 2v: apply an edit to the kept text of agent_id's rejected 'write' of the missing file (src/tools/rejected_drafts.py).
        None if there is no kept text. Valid Python is written with the normal 'write' checks; invalid text stays kept.
        Round 2x: JavaScript / JSON / shell files too (rejected_drafts.draft_problem); `change` names the edit.
        """
        from src.tools import rejected_drafts
        draft = rejected_drafts.get(validated_path, agent_id)
        if draft is None:
            return None
        new_text, problem = edit(draft)
        if problem:
            return {"status": "error", "message": problem}
        if new_text == draft:
            return {"status": "error", "message": (f"Your change does not change the kept (rejected) text of '{filename}', so "
                                                   f"'{filename}' still does not exist. Fix the line named in the error.")}
        language = rejected_drafts.language_of(filename)
        invalid = await asyncio.to_thread(rejected_drafts.draft_problem, new_text, filename)
        if invalid:
            summary, report, error_line = invalid
            rejected_drafts.keep(validated_path, agent_id, new_text)
            rejected_drafts.note_rejection(validated_path, agent_id, error_line)  # Round 2y: progress if it moved down
            logger.info(f"Agent {agent_id}: {change} applied to the kept text of '{filename}'; still invalid ({summary}).")
            return {"status": "error", "message": (
                f"Your {change} was applied to your kept (rejected) text of '{filename}', but it is still not valid "
                f"{language} ({summary}), so '{filename}' still does not exist. Fix that line the same way."
                + (f"\n{report}" if report else ""))}
        result = await self._write_file(base_path, filename, new_text, agent_id, scope_description)
        if isinstance(result, dict) and result.get("status") == "success":
            rejected_drafts.forget(validated_path)
            logger.info(f"Agent {agent_id}: '{filename}' created from its kept rejected text after a {change}.")
            result["message"] = (f"[Framework: '{filename}' did not exist; your {change} was applied to your rejected text of "
                                 f"it, which is now valid {language}, so '{filename}' was created.] "
                                 + str(result.get("message") or ""))
        return result

    @staticmethod
    def _closest_lines_hint(content: str, needle: str) -> str:
        """Numbered file lines around the line most similar to needle's first non-empty line ('' if nothing is close)."""
        first = next((line.strip() for line in needle.splitlines() if line.strip()), "")
        lines = content.splitlines()
        if not first or not lines:
            return ""
        scored = [(difflib.SequenceMatcher(None, first, line.strip()).ratio(), index) for index, line in enumerate(lines)]
        best_ratio, best_index = max(scored)
        if best_ratio < 0.6:
            return ""
        size = max(1, len([line for line in needle.splitlines() if line.strip()]))
        start, end = max(0, best_index - 1), min(len(lines), best_index + size + 1)
        shown = "\n".join(f"{number + 1}: {lines[number]}" for number in range(start, end))
        return f"\nThe most similar lines in the file are:\n{shown}"

    @staticmethod
    def _lines_as_text(value: Any) -> Optional[str]:
        """
        Round 2k: W1 sent the whole file as 'lines' with 'write_with_lines' (2026-10-20 10:31 and 11:12) and was told
        'content' is required. Text (or a list of text lines) given as 'lines' is the content; anything else is not.
        """
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value and all(isinstance(line, str) for line in value):
            return "\n".join(line.rstrip("\n") for line in value) + "\n"
        return None

    @staticmethod
    def _is_true(value: Any) -> bool:
        """Round 2n: a boolean flag as models send it (True, 'true', 'yes', 1); the string 'false' is False."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value == 1
        return isinstance(value, str) and value.strip().lower() in ("true", "yes", "1", "on")

    @staticmethod
    def _content_required_error(action: str, received: Dict[str, Any]) -> Dict[str, Any]:
        """Round 1n: say which parameters arrived, so a model that sent the text under another name can correct itself."""
        names = ", ".join(sorted(str(k) for k in received.keys())) or "(none)"
        return {
            "status": "error",
            "message": f"'content' parameter is required for '{action}'. Received parameters: {names}. Pass the text to write as 'content'.",
        }

    @staticmethod
    def _link_notes(base_path: Path, file_path: Path, content: str) -> str:
        """Round 3d: src/tools/project_links_check.link_notes_text for a saved file ('' if nothing to say). Never raises."""
        try:
            from src.tools.project_links_check import link_notes_text
            rel = Path(file_path).resolve().relative_to(Path(base_path).resolve()).as_posix()
            return link_notes_text(base_path, rel, content)
        except Exception as err:
            logger.debug(f"FileSystemTool: link notes for '{file_path}' failed: {err}")
            return ""

    @staticmethod
    def _line_edit_list(value: Any) -> Optional[List[Dict[str, Any]]]:
        """
        Round 3d: `value` as a list of code_editor chunks if it is a list of edit objects (or the JSON text of one, or of one
        object) - each with line numbers, a search text or a 'replace' object - else None.
        """
        parsed = value
        if isinstance(value, str):
            text = value.strip()
            if not text.startswith(("[", "{")):
                return None
            from src.tools.code_editor import _loads_chunks, _rebalance_brackets, _repair_json_escapes
            try:
                parsed = _loads_chunks(text)
            except Exception:
                try:
                    parsed = _loads_chunks(_rebalance_brackets(_repair_json_escapes(text)))
                except Exception:
                    return None
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list) or not parsed or not all(isinstance(item, dict) for item in parsed):
            return None
        edit_keys = ("start_line", "end_line", "search", "search_block", "find")
        if not all(any(key in item for key in edit_keys) or isinstance(item.get("replace"), dict) for item in parsed):
            return None
        # Round 3e: the new text under the name of the action ('[{"replace_lines": "...", ...}]', W3 2026-10-23 18:40:27)
        text_aliases = ("replace_lines", "new_lines", "lines", "line_content")
        flattened = []
        for item in parsed:
            item = dict(item)
            if not any(key in item for key in ("replace", "replace_text", "content", "text", "code", "new_text")):
                for alias in text_aliases:
                    value = item.get(alias)
                    if isinstance(value, list) and all(isinstance(line, str) for line in value):
                        value = "\n".join(value)
                    if isinstance(value, str):
                        item.pop(alias)
                        item["replace"] = value
                        break
            flattened.append(item)
        return flattened

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
        ) -> Dict[str, Any]:
        """
        Executes the file system operation based on the provided action and scope.
        """
        action = kwargs.get("action")
        if isinstance(action, str):
            # Round 1s: the 4B model sometimes emits 'read_with_lines>' (a stray tag character); clean it before validating.
            action = action.strip().strip("<>/\"' \t").lower()
        # Default to 'shared' if we have project context, else 'private'
        default_scope = "shared" if project_name and session_name else "private"
        scope = kwargs.get("scope", default_scope).lower()
        _fo = self._first_of # Shorthand — preserves empty strings unlike `or`
        if kwargs.get("filename") is None:
            # Round 1v: W2 sent filenames='["tests/test_temp_convert.py"]' (plus path='.') four times.
            single_name = self._single_filename(_fo(kwargs.get("filenames"), kwargs.get("file_names"), kwargs.get("paths")))
            if single_name:
                kwargs["filename"] = single_name
        filename = _fo(kwargs.get("filename"), kwargs.get("filepath"), kwargs.get("file"), kwargs.get("path"), kwargs.get("file_path"), kwargs.get("file_name"), kwargs.get("target_file"), kwargs.get("name")) # Used by read, write, find_replace, regex_replace
        if isinstance(filename, str): filename = filename.strip()
        content = _fo(kwargs.get("content"), kwargs.get("text"), kwargs.get("data"), kwargs.get("body"), kwargs.get("code"), kwargs.get("string"), kwargs.get("content_str"), kwargs.get("message_content"), kwargs.get("file_content"), kwargs.get("contents"), kwargs.get("line_content"), kwargs.get("file_contents"), self._lines_as_text(kwargs.get("lines"))) # Used by write, append, insert_lines (Round 2d: 'line_content', sent by W1 with 'write_with_lines>')
        relative_path = _fo(kwargs.get("path"), kwargs.get("filename"), kwargs.get("filepath"), kwargs.get("dir"), kwargs.get("directory"), kwargs.get("folder"), kwargs.get("target_dir")) # Used by list, mkdir, delete, copy, move
        if isinstance(relative_path, str): relative_path = relative_path.strip()
        destination_path = kwargs.get("destination_path") # Used by copy, move
        if isinstance(destination_path, str): destination_path = destination_path.strip()
        
        # --- BLOCK BRACE EXPANSION HALLUCINATIONS UNIVERSALLY ---
        for param_name, param_val in [("filename", filename), ("path", relative_path), ("destination_path", destination_path)]:
            if isinstance(param_val, str) and "{" in param_val and "}" in param_val:
                return {"status": "error", "message": f"Brace expansion (e.g. {{a,b}}) is NOT supported. Please provide exactly one valid path per tool call. Do not use {{ or }} in the '{param_name}': {param_val}"}

        find_text = _fo(kwargs.get("find_text"), kwargs.get("search"), kwargs.get("find"), kwargs.get("search_string"), kwargs.get("search_text"), kwargs.get("search_term")) # Used by find_replace
        replace_text = _fo(kwargs.get("replace_text"), kwargs.get("replace"), kwargs.get("replacement"), kwargs.get("replacement_text"), kwargs.get("replace_string"), kwargs.get("replace_term")) # Used by find_replace, regex_replace
        regex_pattern = kwargs.get("regex_pattern") # Used by regex_replace
        start_line = kwargs.get("start_line") # Used by read
        end_line = kwargs.get("end_line") # Used by read
        show_line_numbers = kwargs.get("show_line_numbers", True) # Used by read, default True
        insert_line = _fo(kwargs.get("insert_line"), kwargs.get("line_number"), kwargs.get("line"), kwargs.get("at_line")) # Used by insert_lines (NOTE: 'position' excluded — it's a semantic hint like 'after'/'before', not a line number)
        replace_start_line = _fo(kwargs.get("replace_start_line"), kwargs.get("start_line_number"), kwargs.get("from_line")) # Used by replace_lines
        replace_end_line = _fo(kwargs.get("replace_end_line"), kwargs.get("end_line_number"), kwargs.get("to_line")) # Used by replace_lines
        # Auto-cast all line-number params to int if they are numeric strings
        def _safe_int(val):
            if val is None: return None
            try: return int(val)
            except (ValueError, TypeError): return val
        start_line = _safe_int(start_line)
        end_line = _safe_int(end_line)
        insert_line = _safe_int(insert_line)  # May remain a string if it's a search hint — handled later
        replace_start_line = _safe_int(replace_start_line)
        replace_end_line = _safe_int(replace_end_line)
        search_block = _fo(kwargs.get("search_block"), kwargs.get("search"), kwargs.get("search_string"), kwargs.get("find"), kwargs.get("find_text"), kwargs.get("search_term"), kwargs.get("search_text")) # Used by search_replace_block
        replace_block_param = _fo(kwargs.get("replace_block"), kwargs.get("replace"), kwargs.get("replacement"), kwargs.get("replace_string"), kwargs.get("replace_text"), kwargs.get("replace_term")) # Used by search_replace_block
        expected_replacements = kwargs.get("expected_replacements")  # Used by search_replace_block for multi-match confirmation
        start_marker = _fo(kwargs.get("start_marker"), kwargs.get("start"), kwargs.get("start_string"), kwargs.get("start_line"), kwargs.get("start_pattern"))
        end_marker = _fo(kwargs.get("end_marker"), kwargs.get("end"), kwargs.get("end_string"), kwargs.get("end_line"), kwargs.get("end_pattern"))
        try:
            expected_replacements = int(expected_replacements) if expected_replacements is not None else None
        except (ValueError, TypeError):
            expected_replacements = None
        commit_message = kwargs.get("commit_message") # Used by git_commit
        branch = kwargs.get("branch") # Used by git_checkout, git_branch
        files_to_add = kwargs.get("files") # Used by git_add
        remote = kwargs.get("remote", "origin") # Used by git_push, git_pull

        if action == "write_file":
            action = "write"
        # Round 2n: 'force_overwrite_file_write', 'overwrite', 'rewrite_file' ... = 'write' with force_overwrite
        force_overwrite_requested = any(self._is_true(kwargs.get(key)) for key in ("force_overwrite", "overwrite", "force"))
        auto_corrected_from: Optional[str] = None
        from src.agents.tool_activity import FORCE_WRITE_ACTIONS
        if action in FORCE_WRITE_ACTIONS:
            logger.info(f"FileSystemTool: action '{action}' from '{agent_id}' is a 'write' with force_overwrite.")
            auto_corrected_from = action
            action = "write"
            force_overwrite_requested = True
        valid_actions = [
            "read", "write", "append", "insert_lines", "replace_lines", "search_replace_block", 
            "list", "tree", "mkdir", "delete", "find_replace", "regex_replace", "copy", "move", "rename", "exists",
            "git_commit", "git_status", "git_diff", "git_log", "git_branch", "git_checkout", 
            "git_pull", "git_push", "git_add", "git_init"
        ]
        
        # Check for common mistakes and provide helpful suggestions
        action_suggestions = {
            "add_lines": "insert_lines",
            "add_line": "insert_lines",
            "create_directory": "mkdir",
            "create_file": "write", 
            "create": "write",
            "create_folder": "mkdir",
            "make_directory": "mkdir",
            "make_dir": "mkdir",
            "new_file": "write",
            "save_file": "write",
            "save": "write",
            "read_file": "read",
            "read_with_lines": "read",
            # Round 1y: names W1 used on 2026-10-18 (each was rejected as an invalid action)
            "read_file_with_lines": "read",
            "read_with_content": "read",
            "read_content": "read",
            "write_with_content": "write",
            "write_with_lines": "write",
            "write_content": "write",
            "list_with_tree": "tree",
            "list_files": "list",
            "list_directory": "list",
            "delete_file": "delete",
            "remove_file": "delete",
            "remove": "delete",
            "insert_line": "insert_lines",
            "replace_line": "replace_lines",
            "move_file": "move",
            "rename_file": "rename",
            "file_exists": "exists",
            "check_exists": "exists",
            "stat": "exists",
            "file_info": "exists",
            "tree_view": "tree",
            "recursive_list": "tree",
            "find_files": "tree",
            "commit": "git_commit",
            "status": "git_status",
            "diff": "git_diff",
            "log": "git_log",
            "branch": "git_branch",
            "checkout": "git_checkout",
            "pull": "git_pull",
            "push": "git_push",
            "add": "git_add",
            "init": "git_init"
        }
        
        if not action:
            return {"status": "error", "message": f"Missing required 'action' parameter. Must be one of: {', '.join(valid_actions)}."}
        
        if action in ("replace_chunks", "code_editor"):
            # Round 1y: W1 called file_system with action 'code_editor' (2026-10-17 23:29, 23:37). With chunks, do the edit.
            chunks_arg = _fo(kwargs.get("chunks"), kwargs.get("replacements"), kwargs.get("edits"))
            if chunks_arg is not None and filename:
                from src.tools.code_editor import CodeEditorTool
                logger.info(f"FileSystemTool: passing action '{action}' from '{agent_id}' to the code_editor tool.")
                ce_result = await CodeEditorTool().execute(
                    agent_id, agent_sandbox_path, project_name=project_name, session_name=session_name,
                    action="replace_chunks", filename=filename, chunks=chunks_arg, scope=scope,
                )
                if isinstance(ce_result, dict) and ce_result.get("message"):
                    ce_result["message"] = ("[Note: 'replace_chunks' belongs to the 'code_editor' tool; the framework ran it "
                                            "there for you.] " + str(ce_result["message"]))
                return ce_result
            return {"status": "error", "message": (
                "'code_editor' is a separate TOOL and 'replace_chunks' belongs to the 'code_editor' tool, not 'file_system'. "
                "Call the tool named code_editor with action 'replace_chunks', 'filename' and 'chunks', or use file_system "
                "actions 'read', 'write' or 'replace_lines'.")}
        
        if action == "list_with_lines":
            # Round 2f: W1 sent 'list_with_lines' 4x on 2026-10-19 (for 'src/tests/', 'src/' and 'src/temp_convert.py'):
            # a directory is listed, a file is read
            target = str(filename or relative_path or "").strip().rstrip("/")
            auto_corrected_from = action
            action = "read" if Path(target).suffix else "list"
            logger.info(f"FileSystemTool: Auto-correcting action 'list_with_lines' to '{action}' for agent '{agent_id}'.")
        if action not in valid_actions:
            if action in action_suggestions:
                corrected_action = action_suggestions[action]
                logger.info(f"FileSystemTool: Auto-correcting action '{action}' to '{corrected_action}' for agent '{agent_id}'.")
                auto_corrected_from = action
                action = corrected_action  # Auto-correct and continue execution
            else:
                return {"status": "error", "message": f"Invalid action '{action}'. Valid actions are: {', '.join(valid_actions)}."}
        if scope not in ["private", "shared", "projects"]: # Added 'projects' scope
            return {"status": "error", "message": "Invalid 'scope'. Must be 'private', 'shared', or 'projects'."}

        # Determine base path
        base_path: Optional[Path] = None
        scope_description: str = ""
        if scope == "private":
            base_path = agent_sandbox_path; scope_description = f"agent {agent_id}'s private sandbox"
            if not base_path.is_dir(): return {"status": "error", "message": f"Agent's private sandbox directory does not exist at {base_path}"}
        elif scope == "shared":
            if not project_name or not session_name: return {"status": "error", "message": "Cannot use 'shared' scope - project/session context is missing."}
            safe_project_name = project_name.replace(" ", "_").strip()[:50].rstrip('_')
            base_path = settings.PROJECTS_BASE_DIR / safe_project_name / session_name / "shared_workspace"; scope_description = f"shared workspace for '{safe_project_name}/{session_name}'"
            try:
                # Ensure shared workspace base exists (don't need output subdir here)
                await asyncio.to_thread(base_path.mkdir, parents=True, exist_ok=True)
                logger.debug(f"Ensured shared workspace dir exists: {base_path}")
            except Exception as e: logger.error(f"Failed to create shared workspace dir for {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Could not create shared workspace directory: {e}"}
        elif scope == "projects": # Handle new 'projects' scope
            base_path = settings.PROJECTS_BASE_DIR
            scope_description = "main projects directory"
            if not base_path.is_dir(): return {"status": "error", "message": f"Main projects directory does not exist at {base_path}"}
        # --- End Scope Handling ---
        if base_path is None: return {"status": "error", "message": "Internal error determining workspace path."}

        # Round 1v: notes in brackets copied into paths ('docs/README.md (new)') are not part of the path
        path_notes: List[str] = []
        filename, note_f = self._strip_path_annotation(base_path, filename)
        relative_path, note_p = self._strip_path_annotation(base_path, relative_path)
        destination_path, note_d = self._strip_path_annotation(base_path, destination_path)
        for note in (note_f, note_p, note_d):
            if note and note not in path_notes:
                path_notes.append(note)

        # Default relative path for list action if not provided
        if action in ("list", "tree") and relative_path is None:
             relative_path = "."

        # Extract agent_type from manager if available
        agent_type = "worker"
        manager = kwargs.get("manager")
        if manager and hasattr(manager, "agents") and agent_id in manager.agents:
            agent_type = getattr(manager.agents[agent_id], "agent_type", "worker")

        # --- WORKSPACE BLOAT PROTECTION ---
        if action in ["write", "mkdir", "copy", "move", "rename"] and "shared workspace" in scope_description and agent_type != "pm":
            target_file_param = filename if action == "write" else (destination_path if action in ["copy", "move"] else relative_path)
            if target_file_param:
                validated_target = await self._resolve_and_validate_path(base_path, target_file_param, agent_id, scope_description)
                if validated_target:
                    project_structure_file = base_path / "PROJECT_STRUCTURE.md"
                    if project_structure_file.exists():
                        try:
                            rel_target_path = validated_target.relative_to(base_path)
                            if len(rel_target_path.parts) > 1:
                                target_top_dir = rel_target_path.parts[0].lower()
                                if not target_top_dir.startswith("."):
                                    structure_content = await asyncio.to_thread(project_structure_file.read_text, encoding="utf-8")
                                    allowed_top_dirs = set()
                                    in_block = False
                                    for line in structure_content.splitlines():
                                        if line.startswith("```"):
                                            in_block = not in_block
                                            continue
                                        if in_block and line.startswith("  ") and not line.startswith("    "):
                                            clean_dir = line.strip().rstrip("/")
                                            allowed_top_dirs.add(clean_dir.lower())
                                    
                                    # Whitelist standard web development directories
                                    allowed_top_dirs.update(["public", "src", "assets", "components", "pages", "styles", "utils", "tests", "dist", "build", "css", "js", "images", "img", "docs", "config", "html"])

                                    if allowed_top_dirs and target_top_dir not in allowed_top_dirs:
                                        logger.warning(f"Agent {agent_id} attempted to create unauthorized root folder '{target_top_dir}' in shared workspace.")
                                        return {"status": "error", "message": bloat_refusal(rel_target_path, allowed_top_dirs, structure_content)}
                        except Exception as e:
                            logger.error(f"Error checking Workspace Bloat Protection for agent {agent_id}: {e}", exc_info=True)

        # Execute action
        result: Optional[Dict[str, Any]] = None
        try:
            if action == "read":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'read'."}
                result = await self._read_file(base_path, filename, start_line, end_line, show_line_numbers, agent_id, scope_description)
            elif action == "write":
                if not filename: return {"status": "error", "message": "'filename' (or 'filepath') parameter is required for 'write'."}
                if filename.endswith('/'):
                    return {"status": "error", "message": f"The path '{filename}' appears to be a directory. Please use the 'mkdir' action to create directories."}
                if content is None: return self._content_required_error("write", kwargs)
                part_of = part_file_target(filename)  # Round 2t
                if part_of:
                    return part_file_refusal(filename, part_of, (base_path / part_of.lstrip("/")).is_file())
                result = await self._write_file(base_path, filename, content, agent_id, scope_description, force_overwrite=force_overwrite_requested)
            elif action == "list":
                # relative_path default handled above
                result = await self._list_directory(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description)
            elif action == "find_replace":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'find_replace'."}
                if find_text is None: return {"status": "error", "message": "'find_text' parameter is required for 'find_replace'."}
                if replace_text is None: return {"status": "error", "message": "'replace_text' parameter is required for 'find_replace'."}
                result = await self._find_replace_in_file(base_path, filename, find_text, replace_text, agent_id, scope_description)
            elif action == "regex_replace":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'regex_replace'."}
                if regex_pattern is None: return {"status": "error", "message": "'regex_pattern' parameter is required for 'regex_replace'."}
                if replace_text is None: return {"status": "error", "message": "'replace_text' parameter is required for 'regex_replace'."}
                result = await self._regex_replace_in_file(base_path, filename, regex_pattern, replace_text, agent_id, scope_description)
            elif action == "append":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'append'."}
                if content is None: return self._content_required_error("append", kwargs)
                part_of = part_file_target(filename)  # Round 2t
                if part_of:
                    return part_file_refusal(filename, part_of, (base_path / part_of.lstrip("/")).is_file())
                result = await self._append_to_file(base_path, filename, content, agent_id, scope_description)
            elif action == "insert_lines":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'insert_lines'."}
                if content is None: return self._content_required_error("insert_lines", kwargs)
                # Support search-based insertion: if insert_line is missing or non-numeric,
                # check if search/find_text was provided to locate the insertion point.
                position_kwarg = kwargs.get("position", "after")  # Semantic hint: 'before' or 'after'
                search_anchor = _fo(kwargs.get("search"), kwargs.get("after"), kwargs.get("before"))
                if insert_line is None or (isinstance(insert_line, str) and not insert_line.isdigit()):
                    if search_anchor:
                        # Resolve insertion point by finding the search anchor text in the file
                        resolved = await self._resolve_insert_line_from_search(
                            base_path, filename, search_anchor, agent_id, scope_description,
                            position_hint=insert_line if isinstance(insert_line, str) else position_kwarg
                        )
                        if resolved is None:
                            return {"status": "error", "message": f"Could not find search anchor '{search_anchor}' in file '{filename}' for insert_lines."}
                        insert_line = resolved
                    else:
                        return {"status": "error", "message": "'insert_line' parameter (integer line number) is required for 'insert_lines'. Alternatively, provide a '<search>' text to insert after."}
                result = await self._insert_lines_in_file(base_path, filename, int(insert_line), content, agent_id, scope_description)
            elif action == "replace_lines":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'replace_lines'."}
                # Fallback: LLMs frequently use start_line/end_line instead of replace_start_line/replace_end_line
                if replace_start_line is None and start_line is not None:
                    replace_start_line = _safe_int(start_line)
                if replace_end_line is None and end_line is not None:
                    replace_end_line = _safe_int(end_line)
                line_edits = self._line_edit_list(_fo(kwargs.get("replace_lines"), kwargs.get("chunks"),
                                                      kwargs.get("edits"), kwargs.get("replacements")))
                if line_edits is not None and (replace_start_line is None or replace_end_line is None):
                    # Round 3d: 2026-10-23 12:31-14:36 W1/W2 sent the edits as a LIST under 'replace_lines'
                    # ('[{"start_line": 73, "end_line": 110, "replace_text": "..."}]') 5 times: each one is a code_editor chunk
                    from src.tools.code_editor import CodeEditorTool
                    logger.info(f"FileSystemTool: replace_lines from '{agent_id}' with a list of line edits; passing it to code_editor.")
                    ce_result = await CodeEditorTool().execute(
                        agent_id, agent_sandbox_path, project_name=project_name, session_name=session_name,
                        action="replace_chunks", filename=filename, chunks=line_edits, scope=scope,
                    )
                    if isinstance(ce_result, dict) and ce_result.get("message"):
                        # Round 3e: "passed to" - the edit may still fail (2026-10-23 18:09:31 said "applied" on a rejection)
                        ce_result["message"] = ("[Note: your 'replace_lines' held a list of line edits, so the framework passed "
                                                "them to the code_editor tool. For ONE range use replace_start_line, "
                                                "replace_end_line and content.] " + str(ce_result["message"]))
                    return ce_result
                if replace_start_line is None or replace_end_line is None:
                    # Round 2d: W1 sent replace_lines with find_text + replace_text and no (or only an end) line number 3 times
                    # on 2026-10-18 23:29-23:38. Old text + new text is a search/replace: do that instead of refusing.
                    if find_text is not None and replace_text is not None:
                        result = await self._search_replace_block_in_file(base_path, filename, find_text, replace_text, agent_id, scope_description)
                        if isinstance(result, dict) and result.get("status") == "success":
                            result["message"] = (result.get("message") or "") + (
                                " [Note: 'replace_lines' had no start and end line numbers, so your find_text/replace_text "
                                "were applied as 'search_replace_block'.]")
                        return result
                    return {"status": "error", "message": "'replace_start_line' and 'replace_end_line' parameters are required for 'replace_lines'."}
                if content is None:
                    # Round 1v: W1 sent the new lines as 'replace_text' three times; that IS the content here.
                    content = _fo(replace_text, replace_block_param, kwargs.get("replacement_content"),
                                  kwargs.get("new_content"), kwargs.get("new_text"), kwargs.get("new_lines"))
                if content is None and isinstance(kwargs.get("replace_lines"), str) and line_edits is None:
                    # Round 3d: 2026-10-23 15:03:22 W1 sent the new lines as 'replace_lines' (with replace_start_line 94 and
                    # replace_end_line 144) and got "'content' parameter is required"
                    content = kwargs.get("replace_lines")
                if content is None: return self._content_required_error("replace_lines", kwargs)
                result = await self._replace_lines_in_file(base_path, filename, replace_start_line, replace_end_line, content, agent_id, scope_description)
            elif action == "search_replace_block":
                if not filename: return {"status": "error", "message": "'filename' parameter is required for 'search_replace_block'."}
                if search_block is None and (start_marker is None or end_marker is None): 
                    return {"status": "error", "message": "'search_block' OR both 'start_marker' and 'end_marker' are required for 'search_replace_block'."}
                if replace_block_param is None: return {"status": "error", "message": "'replace_block' parameter is required for 'search_replace_block'."}
                result = await self._search_replace_block_in_file(base_path, filename, search_block, replace_block_param, agent_id, scope_description, expected_replacements=expected_replacements, start_marker=start_marker, end_marker=end_marker)

            # --- Git integration ---
            elif action == "git_commit":
                if not commit_message: return {"status": "error", "message": "'commit_message' parameter is required for 'git_commit'."}
                result = await self._git_commit(base_path, relative_path if isinstance(relative_path, str) else ".", commit_message, agent_id, scope_description)
            elif action == "git_status":
                result = await self._git_status(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description)
            elif action == "git_diff":
                result = await self._git_diff(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description)
            elif action == "git_log":
                result = await self._git_log(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description)
            elif action == "git_branch":
                result = await self._git_branch(base_path, relative_path if isinstance(relative_path, str) else ".", branch, agent_id, scope_description)
            elif action == "git_checkout":
                if not branch: return {"status": "error", "message": "'branch' parameter is required for 'git_checkout'."}
                result = await self._git_checkout(base_path, relative_path if isinstance(relative_path, str) else ".", branch, agent_id, scope_description)
            elif action == "git_add":
                if not files_to_add: return {"status": "error", "message": "'files' parameter is required for 'git_add'."}
                result = await self._git_add(base_path, relative_path if isinstance(relative_path, str) else ".", files_to_add, agent_id, scope_description)
            elif action == "git_init":
                result = await self._git_init(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description)
            elif action == "git_pull":
                result = await self._git_pull(base_path, relative_path if isinstance(relative_path, str) else ".", remote, agent_id, scope_description)
            elif action == "git_push":
                result = await self._git_push(base_path, relative_path if isinstance(relative_path, str) else ".", remote, agent_id, scope_description)

            # --- NEW: Handle mkdir and delete ---
            elif action == "mkdir":
                 if not relative_path: return {"status": "error", "message": "'path' parameter (directory path) is required for 'mkdir'."}
                 result = await self._create_directory(base_path, relative_path, agent_id, scope_description)
            elif action == "delete":
                 if not relative_path: return {"status": "error", "message": "'path' parameter (file or directory path) is required for 'delete'."}
                 result = await self._delete_item(base_path, relative_path, agent_id, scope_description)
            elif action == "copy":
                 if not relative_path: return {"status": "error", "message": "'path' parameter (source) is required for 'copy'."}
                 if not destination_path: return {"status": "error", "message": "'destination_path' parameter is required for 'copy'."}
                 result = await self._copy_item(base_path, relative_path, destination_path, agent_id, scope_description)
            elif action == "move":
                 if not relative_path: return {"status": "error", "message": "'path' parameter (source) is required for 'move'."}
                 if not destination_path: return {"status": "error", "message": "'destination_path' parameter is required for 'move'."}
                 result = await self._move_item(base_path, relative_path, destination_path, agent_id, scope_description)
            elif action == "rename":
                 if not relative_path: return {"status": "error", "message": "'path' parameter (source) is required for 'rename'."}
                 if not destination_path: return {"status": "error", "message": "'destination_path' parameter (new name/path) is required for 'rename'."}
                 result = await self._move_item(base_path, relative_path, destination_path, agent_id, scope_description)
            elif action == "tree":
                 result = await self._tree_directory(base_path, relative_path if isinstance(relative_path, str) else ".", agent_id, scope_description, max_depth=kwargs.get("max_depth", 5))
            elif action == "exists":
                 if not relative_path and not filename:
                     return {"status": "error", "message": "'path' or 'filename' parameter is required for 'exists'."}
                 target = str(relative_path or filename)
                 result = await self._exists_stat(base_path, target, agent_id, scope_description)
            # --- END NEW ---

        except Exception as e:
            logger.error(f"Unexpected error executing file system tool (Action: {action}, Scope: {scope}) for agent {agent_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Error executing file system tool ({action} in {scope}): {type(e).__name__} - {e}"}

        if result is None:
            return {"status": "error", "message": "Unknown state in file system tool."} # Should not be reached

        if path_notes:
            result["message"] = (result.get("message") or "") + " [Note: " + " ".join(path_notes) + "]"

        # Append auto-correction feedback to successful results so the agent learns
        if auto_corrected_from and result.get("status") == "success":
            correction_note = (
                f" [Note: The action '{auto_corrected_from}' was auto-corrected to '{action}'. "
                f"Please use '{action}' directly in future calls.]"
            )
            if auto_corrected_from in FORCE_WRITE_ACTIONS:  # Round 2n
                correction_note = (
                    f" [Note: the action '{auto_corrected_from}' was run as 'write' with force_overwrite=true. Next time use "
                    "action 'write'; add \"force_overwrite\": true only to replace a file that another agent wrote.]"
                )
            result["message"] = result.get("message", "") + correction_note

        return result

    # --- Detailed Usage Method ---
    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """Returns detailed usage instructions for the FileSystemTool."""
        
        common_header = """**Tool Name:** file_system

**Description:** Performs operations on files and directories within different scopes. All paths MUST be relative to the scope root.

**CRITICAL - Valid Actions Only:** The following actions are the ONLY valid actions:
read, write, append, insert_lines, replace_lines, list, tree, mkdir, delete, find_replace, regex_replace, copy, move, rename, exists

**Scopes:**
*   `shared`: Current session's shared workspace (the default when you work on a project).
*   `private`: Agent's own sandbox directory (the default without a project).
*   `projects`: Top-level projects directory (ONLY use with `list` action).

**Python, JavaScript (.js/.mjs/.cjs), JSON and shell (.sh/.bash) files** are syntax-checked on every change: a write,
or an edit of a valid file, that would leave invalid code is rejected and NOTHING is saved (the error shows the faulty
lines and a hint). A file that is already invalid can still be edited to repair it.
"""
        
        if sub_action == "read":
            return common_header + """
**Action: read**
Reads the content of a file.
*   `<filename>` (string, required): Relative path to the file.
*   `<start_line>` (integer, optional): Line number to start reading from (1-indexed).
*   `<end_line>` (integer, optional): Line number to stop reading at (inclusive).
*   `<show_line_numbers>` (boolean, optional): Whether to prefix each line with its line number. Default: true.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "write":
            return common_header + """
**Action: write**
Writes content to a NEW file. For targeted edits on existing files, use `replace_lines`, `find_replace`, `regex_replace`, or `append`! DO NOT use `write` to create directories — use `mkdir` instead!
*   `<filename>` (string, required): Relative path to the file - its real path (e.g. `src/app.py`). Part files such as `src/app.py.part1` are refused.
*   `<content>` (string, required): The complete file content to write.
*   `<force_overwrite>` (boolean, optional): Set to `true` ONLY when you intentionally want to **replace the entire contents** of an existing file. Default: `false`. **Use this sparingly** — prefer `code_editor` or `find_replace` for partial edits.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.

Rules:
*   An existing file is replaced without `force_overwrite` only if it is empty, holds no code yet, you wrote it yourself, or (Python) your new version keeps all its functions, classes and top-level names.
*   Python, JavaScript, JSON and shell content must be valid. If the write of a NEW such file is rejected, the file is NOT created, but your text is kept: fix only the faulty line with `find_replace` (find_text = the wrong text) or `replace_lines` (the line numbers shown in the error) on that filename, or add its missing rest with `append`. The file is created as soon as the text is valid.
*   A file too long for one reply: `write` its first part (whole functions or classes only), then add each further part with `append` to the SAME file.
"""
        elif sub_action == "append":
            return common_header + """
**Action: append**
Appends text to the end of a file (a missing file is created).
*   `<filename>` (string, required): Relative path to the file - its real path, never a part file such as `app.py.part2`.
*   `<content>` (string, required): Text to append.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.

For a Python, JavaScript, JSON or shell file the result must be valid: append whole functions or classes (every part must end where a function or class ends). The appended text always starts on a new line.
If the `write` (or first `append`) of a NEW such file was rejected, the appended text is added to your kept (rejected) text instead; the file is created as soon as that text is valid.
"""
        elif sub_action == "insert_lines":
            return common_header + """
**Action: insert_lines**
Inserts a block of text at a specific line number, shifting subsequent lines down.
*   `<filename>` (string, required): Relative path to the file.
*   `<insert_line>` (integer): The line number (1-indexed) where the new `<content>` will be inserted. **Either `<insert_line>` or `<search>` is required.**
*   `<search>` (string): Instead of a line number, provide a text string to search for in the file. The new content will be inserted relative to the first matching line.
*   `<position>` (string, optional): 'after' (default) or 'before'. Controls whether content is inserted after or before the `<search>` match.
*   `<content>` (string, required): Text to insert.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
*   Example (by line number):
    ```json
    {
      "action": "insert_lines",
      "filename": "server.js",
      "insert_line": 10,
      "content": "const express = require('express');",
      "scope": "shared"
    }
    ```
*   Example (by search anchor):
    ```json
    {
      "action": "insert_lines",
      "filename": "package.json",
      "search": "\"dependencies\": {",
      "position": "before",
      "content": "  \"devDependencies\": { \"jest\": \"^29.0.0\" },",
      "scope": "shared"
    }
    ```
"""
        elif sub_action == "replace_lines":
            return common_header + """
**Action: replace_lines**
Replaces a specific block of existing lines with new content. STRONGLY RECOMMENDED for modifying existing files.
*   `<filename>` (string, required): Relative path to the file.
*   `<replace_start_line>` (integer, required): The first line number (1-indexed) to remove. Also accepts `<start_line>`.
*   `<replace_end_line>` (integer, required): The last line number (1-indexed) to remove (inclusive). Also accepts `<end_line>`.
*   `<content>` (string, required): The new text to replace the removed lines with.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.

On a NEW Python, JavaScript, JSON or shell file whose `write` was rejected, the lines of your kept (rejected) text are replaced; the file is created as soon as that text is valid.
"""
        elif sub_action == "list":
            return common_header + """
**Action: list**
Lists files and directories within a single directory (non-recursive). For a full recursive view, use `tree` instead.
*   `<path>` (string, optional): Relative directory path. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared', 'private' or 'projects'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "mkdir":
            return common_header + """
**Action: mkdir**
Creates a directory, including parent directories.
*   `<path>` (string, required): Relative directory path.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "delete":
            return common_header + """
**Action: delete**
Deletes a file or an *empty* directory.
*   `<path>` (string, required): Relative target path.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "find_replace":
            return common_header + """
**Action: find_replace**
Finds and replaces all occurrences of an EXACT string.
*   `<filename>` (string, required): Relative path.
*   `<find_text>` (string, required): Exact string to find. (Can also use `<find>` or `<search>`)
*   `<replace_text>` (string, required): Replacement string. (Can also use `<replace>`)
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.

If find_text is not found exactly, the same lines ignoring indentation are used (one place only); otherwise nothing is changed.
On a NEW Python, JavaScript, JSON or shell file whose `write` was rejected, the change is made in your kept (rejected) text; the file is created as soon as that text is valid.
"""
        elif sub_action == "regex_replace":
            return common_header + """
**Action: regex_replace**
Finds and replaces using regex.
*   `<filename>` (string, required): Relative path.
*   `<regex_pattern>` (string, required): Regex pattern.
*   `<replace_text>` (string, required): Replacement string.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "copy":
            return common_header + """
**Action: copy**
Copies a file/directory.
*   `<path>` (string, required): Source path.
*   `<destination_path>` (string, required): Target path.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "move":
            return common_header + """
**Action: move**
Moves/renames a file/directory. (Tip: use `rename` as a more readable alias.)
*   `<path>` (string, required): Source path.
*   `<destination_path>` (string, required): Target path.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "rename":
            return common_header + """
**Action: rename**
Renames a file or directory (alias for `move`).
*   `<path>` (string, required): Current path of the file/directory.
*   `<destination_path>` (string, required): New path/name for the file/directory.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "tree":
            return common_header + """
**Action: tree**
Recursively lists all files and directories in a tree format, showing the full workspace structure at a glance.
*   `<path>` (string, optional): Relative directory path. Defaults to root of scope.
*   `<max_depth>` (integer, optional): Maximum depth of recursion. Default: 5. Set lower for large workspaces.
*   `<scope>` (string, optional): 'shared', 'private' or 'projects'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "exists":
            return common_header + """
**Action: exists**
Checks if a file or directory exists and returns metadata (type, size, last modified). Much faster than `read` when you only need to check existence.
*   `<path>` (string, required): Relative path to check.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "search_replace_block":
            return common_header + """
**Action: search_replace_block**
Finds a specific block of code/text and replaces it with new content. Uses a 3-tier matching strategy:
1. **Exact match** — if the `search_block` appears exactly once, it is replaced.
2. **First/last line match** — if exact match fails, the first and last non-empty lines of `search_block` are used to locate the block in the file. Useful when indentation or internal whitespace may differ slightly.
3. **Fuzzy match** — `diff_match_patch` fallback for slight variations.

**Parameters:**
*   `<filename>` (string, required): Relative path to the file.
*   `<search_block>` (string, required): The block of text to find. Provide at minimum the **first and last unique lines** of the block. (Can also use `<search>` or `<find>`)
*   `<replace_block>` (string, required): The new content to replace the found block with. (Can also use `<replace_text>`)
*   `<expected_replacements>` (integer, optional): **Required for multi-match cases.** If the tool reports it found N matches, re-send with `<expected_replacements>N</expected_replacements>` to confirm replacing all N occurrences.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "git_init":
            return common_header + """
**Action: git_init**
Initializes a new git repository in the specified path.
*   `<path>` (string, optional): The directory to run git init in. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "git_commit":
            return common_header + """
**Action: git_commit**
Commits changes in the specified scope (if it is a git repository).
*   `<commit_message>` (string, required): The message for the commit.
*   `<path>` (string, optional): The directory to run the commit in. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "git_status":
            return common_header + """
**Action: git_status**
Gets the git status for the specified scope (if it is a git repository).
*   `<path>` (string, optional): The directory to run git status in. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "git_diff":
            return common_header + """
**Action: git_diff**
Gets the git diff for the specified scope (if it is a git repository).
*   `<path>` (string, optional): The directory to run git diff in. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""

        elif sub_action == "git_log":
            return common_header + """
**Action: git_log**
Gets the git commit history for the specified scope.
*   `<path>` (string, optional): The directory to run git log in. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "git_branch":
            return common_header + """
**Action: git_branch**
Lists branches or creates a new one if `<branch>` is provided.
*   `<branch>` (string, optional): Name of the new branch to create.
*   `<path>` (string, optional): The directory to run git branch in. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "git_checkout":
            return common_header + """
**Action: git_checkout**
Switches to a specified branch.
*   `<branch>` (string, required): Name of the branch to checkout.
*   `<path>` (string, optional): The directory to run git checkout in. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "git_add":
            return common_header + """
**Action: git_add**
Stages files for commit.
*   `<files>` (string, required): Comma-separated list of files, or '.' for all.
*   `<path>` (string, optional): The directory to run git add in. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "git_pull":
            return common_header + """
**Action: git_pull**
Pulls changes from a remote repository.
*   `<remote>` (string, optional): The remote to pull from (default: 'origin').
*   `<path>` (string, optional): The directory to run git pull in. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""
        elif sub_action == "git_push":
            return common_header + """
**Action: git_push**
Pushes changes to a remote repository.
*   `<remote>` (string, optional): The remote to push to (default: 'origin').
*   `<path>` (string, optional): The directory to run git push in. Defaults to root of scope.
*   `<scope>` (string, optional): 'shared' or 'private'. Default: 'shared' when you work on a project, else 'private'.
"""

        # Return directory of commands
        return common_header + """
**Available Actions Summary:**
1.  **read:** Reads file content. (Use start_line and end_line for large files).
2.  **write:** Creates a new file (use `force_overwrite=true` for existing files). A rejected new Python file is fixed with find_replace / replace_lines (its text is kept).
3.  **append:** Adds text to the end of a file (whole functions/classes for Python).
4.  **insert_lines:** Inserts a block of text at a specific line number, or relative to a search anchor.
5.  **replace_lines:** Replaces a block of lines with new text.
6.  **list:** Lists files in a single directory.
7.  **tree:** Recursively lists all files/directories in a tree format.
8.  **mkdir:** Creates a directory.
9.  **delete:** Deletes a file or empty directory.
10. **find_replace:** Exact string replacement.
11. **regex_replace:** Regex based string replacement.
12. **copy:** Copies files/directories.
13. **move:** Moves/renames files/directories.
14. **rename:** Alias for move — renames a file or directory.
15. **exists:** Checks if a file/directory exists and returns metadata (size, modified time).
16. **search_replace_block:** Fuzzy search and replace code blocks.
17. **git_commit:** Commits modifications via Git.
18. **git_status:** Gets repository status.
19. **git_diff:** Gets undiscarded modifications.
20. **git_log:** Gets repository commit history.
21. **git_branch:** Lists or creates branches.
22. **git_checkout:** Switches branches.
23. **git_add:** Stages files.
24. **git_pull:** Pulls from remote.
25. **git_push:** Pushes to remote.
26. **git_init:** Initializes a new git repository.

**To get detailed instructions and parameter lists for a specific action, call the 'tool_information' tool:**
    ```json
    {
      "action": "get_info",
      "tool_name": "file_system",
      "sub_action": "ACTION_NAME"
    }
    ```
"""

    async def _resolve_and_validate_path(self, base_path: Path, relative_file_path: str, agent_id: str, scope_description: str) -> Path | None:
        """ Resolves the relative path against the base path and validates it. """
        if not relative_file_path: logger.warning(f"Agent {agent_id} provided empty path/filename for {scope_description}."); return None
        
        # Sanitize the path: strip spaces and replace internal spaces with underscores
        if isinstance(relative_file_path, str):
            relative_file_path = relative_file_path.strip().replace(" ", "_")
            
            # Agent pathing workaround: LLMs often mistakenly prepend "shared/" or "shared/project_name/" 
            # to their paths when working in the shared workspace because of prompt wording.
            if "shared workspace" in scope_description:
                parts = tuple(Path(relative_file_path).parts)
                if parts and parts[0] == "shared":
                    parts = parts[1:] # Strip 'shared/'
                    
                # See if they also included the project name (which is the parent of the parent of the shared_workspace)
                # base_path is: .../projects/ProjectName/SessionName/shared_workspace
                if parts and len(base_path.parents) >= 2 and parts[0] == base_path.parent.parent.name:
                    parts = parts[1:] # Strip 'ProjectName/'

                # See if they included the session name (which is the parent of the shared_workspace)
                if parts and len(base_path.parents) >= 1 and parts[0] == base_path.parent.name:
                    parts = parts[1:] # Strip 'SessionName/'

                # Strip the workspace folder's own name ('shared_workspace/'): agents see it in
                # the workspace tree and prompts and often prepend it to otherwise correct paths.
                if parts and parts[0] == base_path.name:
                    parts = parts[1:] # Strip 'shared_workspace/'

                relative_file_path = str(Path(*parts)) if parts else "."
            
        try:
            # Strip leading slashes to tolerate absolute-looking paths within the scope
            relative_file_path = relative_file_path.lstrip('/') or '.'

            # Handle potential path normalization issues
            # Disallow paths containing '..'
            if '..' in Path(relative_file_path).parts:
                 logger.warning(f"Agent {agent_id} provided potentially unsafe path '{relative_file_path}' for {scope_description}.")
                 return None

            norm_relative_path = Path(relative_file_path)
            absolute_path = (base_path / norm_relative_path).resolve()
            base_path_resolved = base_path.resolve()

            # SECURITY CHECK: Ensure the resolved path is *within* or *is* the base directory
            if absolute_path == base_path_resolved or absolute_path.is_relative_to(base_path_resolved):
                 return absolute_path
            else:
                logger.warning(f"Agent {agent_id} path traversal attempt blocked for {scope_description}: Resolved path {absolute_path} vs Base path {base_path_resolved}")
                return None
        except ValueError as ve: # is_relative_to can raise ValueError on Windows if drives differ
             logger.warning(f"Agent {agent_id} path validation error for {scope_description} ({ve}): Path '{relative_file_path}'")
             return None
        except Exception as e:
            logger.error(f"Error resolving/validating path '{relative_file_path}' for agent {agent_id} within {scope_description} ('{base_path}'): {e}", exc_info=True)
            return None


    async def _read_file(self, base_path: Path, filename: str, start_line: Optional[int], end_line: Optional[int], show_line_numbers: bool, agent_id: str, scope_description: str) -> Dict[str, Any]:
        """Reads content from a file within the specified base path."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed file path '{filename}' in {scope_description}."}
        if validated_path.is_dir():
            # Round 1v: reading '.' used to answer "directory 'startup_.../' exists but contains no files", which was wrong.
            try:
                rel_dir = str(validated_path.relative_to(base_path.resolve())) or "."
            except ValueError:
                rel_dir = "."
            listing = await self._list_directory(base_path, rel_dir, agent_id, scope_description)
            return {"status": "error", "message": f"'{filename}' is a directory, not a file. Pass the path of a file as 'filename'.\n{listing.get('message', '')}"}
        if not validated_path.is_file():
            # Help the agent by listing what files DO exist in the parent dir
            parent = validated_path.parent
            existing_files = []
            if parent.is_dir():
                try:
                    existing_files = sorted([f.name for f in parent.iterdir() if f.is_file()][:20])
                except Exception:
                    pass
            hint = ""
            if existing_files:
                hint = f" Available files in '{parent.name}/': {', '.join(existing_files)}"
            elif parent.is_dir():
                hint = f" The directory '{parent.name}/' exists but contains no files."
            else:
                hint = f" The directory '{parent.name}/' does not exist. Use 'list' action first to discover the workspace structure."
            
            # Warn on extension mismatch (e.g. .js vs .py)
            if existing_files and validated_path.suffix:
                ext = validated_path.suffix.lower()
                alt_ext = '.py' if ext == '.js' else ('.js' if ext == '.py' else None)
                if alt_ext:
                    alt_name = validated_path.stem + alt_ext
                    if alt_name in existing_files:
                        hint += f"\n\n[CRITICAL WARNING]: You requested '{validated_path.name}' but only '{alt_name}' exists. Ensure you are using the correct programming language as defined in PROJECT_STRUCTURE.md!"
            
            # --- File-Not-Found Loop Escalation ---
            if not hasattr(self, '_failed_reads'): self._failed_reads = {}
            if agent_id not in self._failed_reads: self._failed_reads[agent_id] = {}
            fail_count = self._failed_reads[agent_id].get(filename, 0) + 1
            self._failed_reads[agent_id][filename] = fail_count
            
            parent_dir = str(parent.relative_to(base_path)) if parent.is_relative_to(base_path) else "."
            list_result = await self._list_directory(base_path, parent_dir, agent_id, scope_description)
            list_content = list_result.get("message", "(Could not list directory)")
            
            hint += f"\n\n[Framework Intervention]: You have failed to read '{filename}' {fail_count} times. The file does NOT exist. The framework has automatically run the 'list' command on the parent directory '{parent_dir}'. Please review these contents and use the correct filename:\n\n{list_content}"
            
            # After 2+ failures for the same filename, suggest closest match
            if fail_count >= 2 and existing_files:
                close = difflib.get_close_matches(validated_path.name, existing_files, n=3, cutoff=0.4)
                if close:
                    hint += f"\n\n[CRITICAL SUGGESTION]: You have tried '{filename}' {fail_count} times and it does NOT exist. Did you mean one of these? {', '.join(close)}. You MUST use one of the listed filenames instead of '{validated_path.name}'."
            # ----------------------------------------
            
            return {"status": "error", "message": f"File not found: '{filename}' in {scope_description}.{hint}"}
        try:
            content = await asyncio.to_thread(validated_path.read_text, encoding='utf-8')
            if not content.strip():
                # Round 1y: an empty file used to come back as "Tool execution was successful." (and ranged reads as
                # "Invalid start_line/end_line range for file with 0 lines"), so W1 never learned that it was empty.
                if hasattr(self, '_failed_reads') and agent_id in self._failed_reads and filename in self._failed_reads[agent_id]:
                    self._failed_reads[agent_id][filename] = 0
                logger.info(f"Agent {agent_id} read file '{filename}' from {scope_description}: the file is EMPTY.")
                return {"status": "success", "content": "", "message": (
                    f"File '{filename}' is EMPTY ({len(content)} bytes, no lines of text). There is nothing to read. To give "
                    "it content, use file_system action 'write' with the complete file as 'content' (no force_overwrite "
                    "needed for an empty file).")}
            lines = content.splitlines(True)

            if show_line_numbers:
                # Add line numbers preserving original newlines
                numbered_lines = []
                for i, line in enumerate(lines):
                    numbered_lines.append(f"{i + 1}: {line}")
                lines = numbered_lines
                
            if start_line is not None or end_line is not None:
                start_idx = max(0, start_line - 1) if start_line is not None else 0
                end_idx = min(len(lines), end_line) if end_line is not None else len(lines)
                if start_idx >= len(lines) or start_idx > end_idx:
                    return {"status": "error", "message": f"Invalid start_line/end_line range for file with {len(lines)} lines."}
                content = "".join(lines[start_idx:end_idx])
                if end_idx >= len(lines):
                    # Round 2e: 2026-10-19 04:26-04:56 W2 read lines 39-100 of a 40-line file six times, looking for the
                    # rest of a test that was never written. Say where the file ends.
                    if content and not content.endswith("\n"):
                        content += "\n"
                    content += f"[End of file: '{filename}' has {len(lines)} lines; there is nothing after line {len(lines)}.]"
            else:
                content = "".join(lines)
            
            
            # Reset fail count on success
            if hasattr(self, '_failed_reads') and agent_id in self._failed_reads and filename in self._failed_reads[agent_id]:
                self._failed_reads[agent_id][filename] = 0
                
            logger.info(f"Agent {agent_id} successfully read file: '{filename}' from {scope_description}")
            return {"status": "success", "content": content}
        except FileNotFoundError: 
            # Warn on extension mismatch (e.g. .js vs .py) before doing the list directory fallback
            ext_warning = ""
            try:
                parent_path = validated_path.parent if validated_path else base_path / Path(filename).parent
                if parent_path.is_dir():
                    dir_files = sorted([f.name for f in parent_path.iterdir() if f.is_file()][:20])
                    if dir_files and validated_path.suffix:
                        ext = validated_path.suffix.lower()
                        alt_ext = '.py' if ext == '.js' else ('.js' if ext == '.py' else None)
                        if alt_ext:
                            alt_name = validated_path.stem + alt_ext
                            if alt_name in dir_files:
                                ext_warning = f"\n\n[CRITICAL WARNING]: You requested '{validated_path.name}' but only '{alt_name}' exists. Ensure you are using the correct programming language as defined in PROJECT_STRUCTURE.md!"
            except Exception:
                pass

            # --- File-Not-Found Loop Escalation ---
            if not hasattr(self, '_failed_reads'): self._failed_reads = {}
            if agent_id not in self._failed_reads: self._failed_reads[agent_id] = {}
            fail_count = self._failed_reads[agent_id].get(filename, 0) + 1
            self._failed_reads[agent_id][filename] = fail_count
            
            parent = validated_path.parent
            parent_dir = str(parent.relative_to(base_path)) if parent.is_relative_to(base_path) else "."
            list_result = await self._list_directory(base_path, parent_dir, agent_id, scope_description)
            list_content = list_result.get("message", "(Could not list directory)")
            
            hint = f"{ext_warning}\n\n[Framework Intervention]: You have failed to read '{filename}' {fail_count} times. The file does NOT exist. The framework has automatically run the 'list' command on the parent directory '{parent_dir}'. Please review these contents and use the correct filename:\n\n{list_content}"
            
            # After 2+ failures, try fuzzy match on parent directory files
            if fail_count >= 2:
                try:
                    parent_path = validated_path.parent if validated_path else base_path / Path(filename).parent
                    if parent_path.is_dir():
                        dir_files = sorted([f.name for f in parent_path.iterdir() if f.is_file()][:20])
                        if dir_files:
                            close = difflib.get_close_matches(Path(filename).name, dir_files, n=3, cutoff=0.4)
                            if close:
                                hint += f"\n\n[CRITICAL SUGGESTION]: You have tried '{filename}' {fail_count} times and it does NOT exist. Did you mean one of these? {', '.join(close)}. You MUST use one of the listed filenames instead of '{Path(filename).name}'."
                except Exception:
                    pass
            # ----------------------------------------
            logger.warning(f"Agent {agent_id} file read error (FileNotFound): File not found at '{filename}' in {scope_description}."); return {"status": "error", "message": f"File not found at '{filename}' in {scope_description}.{hint}"}
        except PermissionError: logger.error(f"Agent {agent_id} file read error: Permission denied for '{filename}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when reading file '{filename}'."}
        except Exception as e: logger.error(f"Agent {agent_id} error reading file '{filename}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error reading file '{filename}': {type(e).__name__} - {e}"}


    async def _write_file(self, base_path: Path, filename: str, content: str, agent_id: str, scope_description: str, force_overwrite: bool = False) -> Dict[str, Any]:
        """Writes content to a file within the specified base path."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed file path '{filename}' in {scope_description}."}
        replace_empty_dir = False
        if validated_path.is_dir():
            # Self-heal: an EMPTY directory with the file's name (e.g. 'src/__init__.py/' created by a
            # bad scaffold) is replaced by the file. Non-empty directories are never removed.
            try:
                is_empty_dir = not any(validated_path.iterdir())
            except Exception:
                is_empty_dir = False
            if is_empty_dir and validated_path != base_path.resolve():
                replace_empty_dir = True
                logger.warning(f"Agent {agent_id} is writing '{filename}' where an EMPTY directory of that name exists in {scope_description}. The directory will be replaced by the file.")
            else:
                logger.warning(f"Agent {agent_id} attempted to write to directory: {filename} in {scope_description}")
                return {"status": "error", "message": f"Cannot write file. '{filename}' points to an existing directory that is not empty. To create a file inside it, use a path such as '{filename.rstrip('/')}/<file_name>'."}
        
        # Round 1u: the root conftest.py written by the framework must not be rewritten by agents (even with force_overwrite)
        if str(filename).replace("\\", "/").lstrip("./") == "conftest.py" and validated_path.exists():
            try:
                if "FRAMEWORK-OWNED conftest" in validated_path.read_text(encoding="utf-8")[:300]:
                    return {
                        "status": "error",
                        "message": "conftest.py is managed by the framework and must not be changed. Modules in src/ and in the project "
                                   "root are already importable from tests: write `from my_module import my_function`. Run tests with "
                                   "`python -m pytest -q` from the workspace root."
                    }
            except Exception:
                pass
        # Round 1y: an existing file that is EMPTY (only whitespace) holds nothing to lose. 'write' was refused 38 times on
        # the emptied src/temp_convert.py (2026-10-17 21:21 - 2026-10-18 05:37) because the agents never set force_overwrite.
        replace_blank_file = False
        replace_stub_file = False  # Round 2a: a .py file with only docstrings/imports/pass holds no code to lose either
        replace_keeping_code = False  # Round 2d: a complete new .py version that keeps every function and class
        defer_to_syntax_check = False  # Round 2d: new .py text that does not parse is rejected by the syntax check below
        replace_own_file = False  # Round 2n: the same agent wrote this file earlier in this server run (its own work)
        rewrite_problems: List[str] = []
        previous_writer: Optional[str] = None
        if validated_path.is_file() and not force_overwrite and not replace_empty_dir:
            try:
                from src.utils.file_authorship import last_writer
                previous_writer = last_writer(validated_path)
            except Exception:
                previous_writer = None
            try:
                existing_text = validated_path.read_text(encoding='utf-8')
                replace_blank_file = not existing_text.strip()
                if not replace_blank_file and previous_writer == agent_id and existing_text != content:
                    # Round 2n: W1's corrected versions of its OWN src/game_logic.py and src/app.py were refused 20+ times
                    # on 2026-10-20 23:25-23:55 because they renamed a class or dropped imports. The agent replaces its
                    # own work; the result says what the new version no longer has.
                    replace_own_file = True
                    if str(filename).endswith('.py'):
                        from src.tools.python_static_check import parses_as_python, python_rewrite_problems
                        if parses_as_python(existing_text) and parses_as_python(content):
                            rewrite_problems = python_rewrite_problems(existing_text, content)
                elif not replace_blank_file and str(filename).endswith('.py'):
                    from src.tools.python_static_check import (is_unimplemented_python, parses_as_python,
                                                               python_rewrite_problems)
                    replace_stub_file = is_unimplemented_python(existing_text)
                    if not replace_stub_file and existing_text != content and parses_as_python(existing_text):
                        if not parses_as_python(content):
                            defer_to_syntax_check = True  # nothing is written; the agent gets the syntax error report
                        else:
                            rewrite_problems = python_rewrite_problems(existing_text, content)
                            replace_keeping_code = not rewrite_problems
            except Exception:
                replace_blank_file = False
                replace_stub_file = False
                replace_keeping_code = False
                defer_to_syntax_check = False
                replace_own_file = False
        # Prevent overwriting existing files unless force_overwrite is explicitly set
        if (validated_path.exists() and not force_overwrite and not replace_empty_dir and not replace_blank_file
                and not replace_stub_file and not replace_keeping_code and not defer_to_syntax_check
                and not replace_own_file):
            # Check if the content is exactly identical, in which case just return success to avoid looping the agent
            try:
                if validated_path.read_text(encoding='utf-8') == content:
                    logger.info(f"Agent {agent_id} attempted to rewrite existing file '{filename}' with identical content. Marking as success.")
                    same_msg = (f"File '{filename}' already exists and contains the exact same content. No changes were made, "
                                "but your action is considered successful.")
                    if str(filename).endswith('.py'):
                        # Round 2n: W1 rewrote src/game_logic.py 4 times with the same broken text (undefined 'i' and
                        # 'half_grid') and was only told "success"
                        try:
                            from src.tools.python_static_check import python_code_notes_for
                            same_notes = python_code_notes_for(content, filename, base_path)
                        except Exception:
                            same_notes = []
                        if same_notes:
                            same_msg += ("\n[Framework check - the file is unchanged and STILL has these defects:]\n- "
                                         + "\n- ".join(same_notes)
                                         + "\nSending the same text again changes nothing: fix the lines named above.")
                    else:
                        # Round 2r: W1 rewrote game-server/game-engine.js 5 times with the same syntax error and was
                        # only told "considered successful" (JavaScript / JSON / shell files, src/tools/source_check.py)
                        from src.tools.source_check import unchanged_note
                        same_msg += await asyncio.to_thread(unchanged_note, content, filename)
                    return {"status": "success", "message": same_msg}
            except Exception:
                pass
            if not hasattr(self, '_failed_writes'): self._failed_writes = {}
            if agent_id not in self._failed_writes: self._failed_writes[agent_id] = {}
            fail_count = self._failed_writes[agent_id].get(filename, 0) + 1
            self._failed_writes[agent_id][filename] = fail_count
            
            hint = ""
            if previous_writer and previous_writer != agent_id:
                hint += f"\n\nThis file was last changed by agent '{previous_writer}', not by you."
            if rewrite_problems:
                # Round 2d: say exactly what the complete new version would lose
                hint += ("\n\nA complete new version of a Python file is saved without force_overwrite only if it keeps every "
                         "function, class and top-level name of the existing file. Here that is not the case:\n- "
                         + "\n- ".join(rewrite_problems))
            if fail_count >= 2:
                hint += f"\n\n[CRITICAL INTERVENTION]: You have failed to overwrite '{filename}' {fail_count} times in a row. You MUST stop trying to use 'write' on this file. EITHER use 'code_editor' (action='replace_chunks') to modify it, OR include '\"force_overwrite\": true' in your JSON if you intentionally want to delete the old content entirely."

            logger.warning(f"Agent {agent_id} attempted to overwrite existing file '{filename}'. Write rejected to strongly encourage find/replace targeted edits.")
            return {
                "status": "error", 
                "message": (
                    f"File '{filename}' already exists. Overwriting entire existing files using the file_system 'write' action is blocked by default to prevent accidental data loss.\n\n"
                    "HOW TO PROCEED:\n"
                    "1. BEST: Use the 'code_editor' tool with action 'replace_chunks' for partial updates.\n"
                    "2. IF YOU MUST OVERWRITE THE ENTIRE FILE: You MUST include the parameter `\"force_overwrite\": true` in your JSON tool call.\n"
                    "Do not attempt to delete and recreate the file."
                    f"{hint}"
                )
            }
            
        try:
            # Perform basic Python syntax validation if modifying a python file
            if filename.endswith('.py'):
                try:
                    from src.tools.python_static_check import parse_python  # Round 2m: quiet parse
                    parse_python(content)
                except SyntaxError as syntax_err:
                    file_existed = validated_path.is_file()
                    closing = None
                    repeat_note = ""
                    if not file_existed:
                        # Round 2v: keep the rejected text of a NEW file; find_replace / replace_lines fix it in place
                        from src.tools import rejected_drafts
                        rejected_drafts.keep(validated_path, agent_id, content)
                        closing = rejected_drafts.DRAFT_CLOSING
                        # Round 2y: a later error than last time is progress; a repeated complete rewrite is told to stop
                        repeat_note = rejected_drafts.repeated_write_note(
                            filename, rejected_drafts.note_rejection(validated_path, agent_id, syntax_err.lineno))
                    try:  # Round 1t: show the code around the error and a hint (never lets the report break the rejection)
                        from src.tools.python_static_check import syntax_error_report
                        syntax_report = syntax_error_report(content, syntax_err, closing=closing)
                    except Exception:
                        syntax_report = ""
                    return {
                        "status": "error",
                        "message": f"Write rejected due to Python SyntaxError in resulting code: {syntax_err.msg} at line {syntax_err.lineno}. No changes were saved. Please check your indentation and brackets."
                                   + rejected_write_state_note(filename, file_existed)  # Round 2t
                                   + repeat_note
                                   + (f"\n{syntax_report}" if syntax_report else "")
                    }
            else:
                # Round 2x: a JavaScript / JSON / shell syntax error rejects the write too (src/tools/source_check.py)
                from src.tools.source_check import blocking_issue, issue_report, write_rejection_text
                issue = await asyncio.to_thread(blocking_issue, content, filename)
                if issue is not None:
                    file_existed = validated_path.is_file()
                    closing = None
                    repeat_note = ""
                    if not file_existed:
                        from src.tools import rejected_drafts
                        rejected_drafts.keep(validated_path, agent_id, content)
                        closing = rejected_drafts.DRAFT_CLOSING
                        repeat_note = rejected_drafts.repeated_write_note(  # Round 2y
                            filename, rejected_drafts.note_rejection(validated_path, agent_id, issue.line))
                    logger.info(f"Agent {agent_id}: write of '{filename}' rejected - not valid {issue.kind} ({issue.summary()}).")
                    return {"status": "error",
                            "message": write_rejection_text(filename, issue)
                                       + rejected_write_state_note(filename, file_existed) + repeat_note
                                       + "\n" + issue_report(issue, content, closing)}
                    
            if replace_empty_dir:
                await asyncio.to_thread(validated_path.rmdir)  # still empty: rmdir fails safely otherwise
            await asyncio.to_thread(validated_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(validated_path.write_text, content, encoding='utf-8')
            from src.tools import rejected_drafts  # Round 2v: a written file needs no kept rejected text any more
            rejected_drafts.forget(validated_path)
            
            if hasattr(self, '_failed_writes') and agent_id in self._failed_writes and filename in self._failed_writes[agent_id]:
                self._failed_writes[agent_id][filename] = 0
                
            logger.info(f"Agent {agent_id} successfully wrote file: '{filename}' to {scope_description}")
            success_msg = f"Successfully wrote content to '{filename}' in {scope_description}."
            if replace_empty_dir:
                success_msg += " (Framework note: an empty directory with this name was in the way and has been replaced by your file.)"
            if replace_blank_file:
                logger.info(f"Agent {agent_id} wrote '{filename}', which existed but was EMPTY, without force_overwrite.")
                success_msg += " (Framework note: the file existed but was empty, so it was written without force_overwrite.)"
            if replace_stub_file:
                logger.info(f"Agent {agent_id} rewrote '{filename}', which held no code (only docstrings/imports/pass), without force_overwrite.")
                success_msg += " (Framework note: the old file held no code, only docstrings/imports, so it was replaced without force_overwrite.)"
            if replace_keeping_code:
                logger.info(f"Agent {agent_id} replaced '{filename}' with a complete new version that keeps all its functions and classes (no force_overwrite).")
                success_msg += (" (Framework note: the file already existed; your complete new version keeps all of its functions, "
                                "classes and top-level names, so it replaced the old version without force_overwrite.)")
            if replace_own_file:
                logger.info(f"Agent {agent_id} replaced its own file '{filename}' with a complete new version (no force_overwrite).")
                success_msg += (" (Framework note: you wrote this file yourself earlier, so your complete new version replaced "
                                "it without force_overwrite.")
                if rewrite_problems:
                    success_msg += (" The new version no longer has: " + "; ".join(rewrite_problems)
                                    + ". If that was not intended, add them back.")
                success_msg += ")"
            if filename.endswith('.py'):
                # Round 1o: non-blocking hints about defects the syntax check cannot see (missing imports, docstring-only functions).
                try:
                    from src.tools.python_static_check import python_code_notes_for
                    code_notes = python_code_notes_for(content, filename, base_path)  # Round 2e: + import suggestions
                except Exception as note_err:
                    logger.debug(f"Agent {agent_id}: python_code_notes failed for '{filename}': {note_err}")
                    code_notes = []
                if code_notes:
                    success_msg += (
                        "\n[Framework check - the file WAS saved, but please fix:]\n- " + "\n- ".join(code_notes)
                        + "\nUse 'find_replace' or 'insert_lines' for small fixes; do not resend the whole file."
                    )
            elif not content.strip():
                # Round 2r: syntax check of JavaScript / JSON / shell files (src/tools/source_check.py). Round 2x: text
                # with a syntax error was rejected above, so only a blank file (e.g. an empty .json) still gets a note.
                from src.tools.source_check import write_note
                success_msg += await asyncio.to_thread(write_note, content, filename)
            # Round 3d: a test of a copy, a loaded module that exports nothing, an npm package package.json lacks
            success_msg += await asyncio.to_thread(self._link_notes, base_path, validated_path, content)
            return {"status": "success", "message": success_msg}
        except FileExistsError:
            logger.warning(f"Agent {agent_id} attempted to write file '{filename}' but a parent path exists as a file."); return {"status": "error", "message": f"Cannot write to '{filename}': A parent path already exists but is a FILE, not a directory. Did you previously use 'write' instead of 'mkdir' to create a directory?"}
        except NotADirectoryError:
            logger.warning(f"Agent {agent_id} attempted to write file '{filename}' but a parent path is not a directory."); return {"status": "error", "message": f"Cannot write to '{filename}': A parent path already exists but is a FILE, not a directory. Did you previously use 'write' instead of 'mkdir' to create a directory?"}
        except PermissionError: logger.error(f"Agent {agent_id} file write error: Permission denied for '{filename}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when writing to file '{filename}'."}
        except Exception as e: logger.error(f"Agent {agent_id} error writing file '{filename}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error writing file '{filename}': {type(e).__name__} - {e}"}


    async def _list_directory(self, base_path: Path, relative_dir: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        """Lists files and directories within a specified sub-directory of the base path."""
        validated_path = await self._resolve_and_validate_path(base_path, relative_dir, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed path '{relative_dir}' in {scope_description}."}
        if not validated_path.is_dir():
            logger.warning(f"Agent {agent_id} attempted to list non-existent directory: {relative_dir} in {scope_description}")
            # Instead of erroring (which triggers retries), return helpful info with root listing
            try:
                root_items = await asyncio.to_thread(os.listdir, base_path)
                root_listing = "\n".join([f"- {item}" for item in sorted([str(i) for i in root_items])]) if root_items else "(empty workspace)"  # type: ignore
            except Exception:
                root_listing = "(could not list root)"
            return {
                "status": "success",
                "message": f"Path '{relative_dir}' does not exist yet in {scope_description}. You may need to create it first using 'mkdir'. Here are the current contents of the workspace root:\n{root_listing}",
                "items": []
            }
        try:
            items = await asyncio.to_thread(os.listdir, validated_path)
            if not items: logger.info(f"Agent {agent_id} listed empty directory: '{relative_dir}' in {scope_description}"); return {"status": "success", "message": f"Directory '{relative_dir}' in {scope_description} is empty.", "items": []}
            output_lines = []
            item_details = []
            for item in sorted([str(i) for i in items]):  # type: ignore
                 try:
                      item_path = validated_path / item
                      item_type = "unknown"
                      if item_path.is_symlink(): item_type = "link"
                      elif item_path.is_dir(): item_type = "dir"
                      elif item_path.is_file(): item_type = "file"
                      output_lines.append(f"- {item} ({item_type})")
                      item_details.append({"name": item, "type": item_type})
                 except OSError as list_item_err: logger.warning(f"Error accessing item '{item}' in directory '{relative_dir}' for agent {agent_id}: {list_item_err}"); output_lines.append(f"- {item} (error accessing)")
            logger.info(f"Agent {agent_id} successfully listed directory: '{relative_dir}' in {scope_description}")
            return {"status": "success", "message": f"Contents of '{relative_dir}' in {scope_description}:\n" + "\n".join(output_lines), "items": item_details}
        except PermissionError: logger.error(f"Agent {agent_id} directory list error: Permission denied for '{relative_dir}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when listing directory '{relative_dir}'."}
        except Exception as e: logger.error(f"Agent {agent_id} error listing directory '{relative_dir}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error listing directory '{relative_dir}': {type(e).__name__} - {e}"}


    async def _find_replace_in_file(self, base_path: Path, filename: str, find_text: str, replace_text: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        """Finds and replaces all occurrences of text in a file within the specified scope."""
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed file path '{filename}' in {scope_description}."}
        if not validated_path.is_file() and find_text != "":
            # Round 2v: the file's rejected 'write' text is kept - the fix is applied to it
            drafted = await self._edit_rejected_draft(base_path, filename, validated_path, agent_id, scope_description,
                                                      lambda text: self._find_replace_text(text, find_text, replace_text, filename))
            if drafted is not None:
                return drafted
        if not validated_path.is_file():
            parent = validated_path.parent
            existing_files = []
            if parent.is_dir():
                try:
                    existing_files = sorted([f.name for f in parent.iterdir() if f.is_file()][:20])
                except Exception:
                    pass
            hint = f" Available files in '{parent.name}/': {', '.join(existing_files)}" if existing_files else ""
            return {"status": "error", "message": f"File not found: '{filename}' in {scope_description}.{hint}"}

        if find_text == "":
            return {"status": "error", "message": "'find_text' is empty. Provide the exact text to find."}
        try:
            # Round 1v: 0 occurrences used to be reported as SUCCESS ("No changes made"), so W1 never learned that its edits
            # did nothing. Now: exact match first; else the same lines ignoring indentation (one place only); else an error.
            original_content = await asyncio.to_thread(validated_path.read_text, encoding='utf-8')
            num_replacements = original_content.count(find_text)
            match_note = ""
            if num_replacements > 0:
                new_content = original_content.replace(find_text, replace_text)
            else:
                span = self._whitespace_tolerant_span(original_content, find_text)
                if span is None:
                    logger.info(f"Agent {agent_id}: find_replace in '{filename}' ({scope_description}): text not found.")
                    return {"status": "error", "replacements_made": 0, "message": (
                        f"find_text was NOT found in '{filename}', so nothing was changed. It must match the file exactly, "
                        "including indentation." + self._closest_lines_hint(original_content, find_text)
                        + "\nRead the file with line numbers and use 'replace_lines' (replace_start_line, replace_end_line, "
                        "content), or copy the text exactly.")}
                if span == "ambiguous":
                    return {"status": "error", "replacements_made": 0, "message": (
                        f"find_text was not found exactly in '{filename}', and ignoring indentation it matches several places. "
                        "Nothing was changed. Use a longer find_text or 'replace_lines' with line numbers.")}
                start, end = span
                replacement = replace_text
                if original_content[start:end].endswith("\n") and not replacement.endswith("\n"):
                    replacement += "\n"
                new_content = original_content[:start] + replacement + original_content[end:]
                num_replacements = 1
                match_note = " (matched ignoring differences in indentation/whitespace)"
            syntax_problem = await asyncio.to_thread(self._python_edit_error, filename, original_content, new_content)
            if syntax_problem:
                return syntax_problem
            await asyncio.to_thread(validated_path.write_text, new_content, encoding='utf-8')
            logger.info(f"Agent {agent_id}: Successfully replaced {num_replacements} occurrence(s) in file '{filename}' in {scope_description}{match_note}.")
            from src.tools.source_check import edit_note  # Round 2r: JavaScript / JSON / shell syntax ('' for other files)
            check_note = await asyncio.to_thread(edit_note, new_content, filename)
            return {"status": "success", "message": f"Successfully replaced {num_replacements} occurrence(s) in '{filename}'{match_note}." + check_note, "replacements_made": num_replacements}
        except FileNotFoundError: logger.warning(f"Agent {agent_id} find_replace error (FileNotFound): File not found at '{filename}' in {scope_description}."); return {"status": "error", "message": f"File not found at '{filename}' in {scope_description}."}
        except PermissionError: logger.error(f"Agent {agent_id} find_replace error: Permission denied for '{filename}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when accessing file '{filename}'."}
        except Exception as e: logger.error(f"Agent {agent_id} error during find/replace in file '{filename}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error during find/replace in '{filename}': {type(e).__name__} - {e}"}

    # --- NEW: _create_directory method ---
    async def _create_directory(self, base_path: Path, relative_dir: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
         """Creates a directory within the specified base path."""
         if "{" in relative_dir and "}" in relative_dir:
             return {"status": "error", "message": f"Brace expansion (e.g. {{a,b}}) is NOT supported. Please provide exactly one valid directory path per tool call. Do not use {{ or }} in the path '{relative_dir}'."}
             
         validated_path = await self._resolve_and_validate_path(base_path, relative_dir, agent_id, scope_description)
         if not validated_path: return {"status": "error", "message": f"Invalid or disallowed directory path '{relative_dir}' in {scope_description}."}
         if not validated_path.exists() and validated_path.suffix.lower() in self.FILE_LIKE_EXTENSIONS:
             # Round 1v: 'mkdir docs/README.md (force)' created a directory named like the file that was supposed to be written.
             return {"status": "error", "message": f"'{relative_dir}' is a FILE name ({validated_path.suffix}), not a directory. Create files with action 'write' (path and content); missing parent directories are created automatically."}
         if validated_path.exists():
             if validated_path.is_dir(): return {"status": "success", "message": f"Directory '{relative_dir}' already exists in {scope_description}."}
             else: return {"status": "error", "message": f"Cannot create directory. '{relative_dir}' points to an existing file."}
         try:
             await asyncio.to_thread(validated_path.mkdir, parents=True, exist_ok=True) # exist_ok shouldn't be needed due to check, but safe
             logger.info(f"Agent {agent_id} successfully created directory: '{relative_dir}' in {scope_description}")
             return {"status": "success", "message": f"Successfully created directory '{relative_dir}' in {scope_description}."}
         except PermissionError: logger.error(f"Agent {agent_id} directory creation error: Permission denied for '{relative_dir}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when creating directory '{relative_dir}'."}
         except Exception as e: logger.error(f"Agent {agent_id} error creating directory '{relative_dir}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error creating directory '{relative_dir}': {type(e).__name__} - {e}"}

    # --- NEW: _delete_item method ---
    async def _delete_item(self, base_path: Path, relative_item_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
         """Deletes a file or an empty directory within the specified base path."""
         validated_path = await self._resolve_and_validate_path(base_path, relative_item_path, agent_id, scope_description)
         if not validated_path: return {"status": "error", "message": f"Invalid or disallowed path '{relative_item_path}' for deletion in {scope_description}."}
         if not validated_path.exists(): return {"status": "error", "message": f"Cannot delete. Path '{relative_item_path}' does not exist in {scope_description}."}
         # Prevent deleting the base path itself
         if validated_path == base_path.resolve(): return {"status": "error", "message": f"Cannot delete the root of the {scope_description}."}

         try:
             if validated_path.is_file():
                 await asyncio.to_thread(validated_path.unlink)
                 logger.info(f"Agent {agent_id} successfully deleted file: '{relative_item_path}' from {scope_description}")
                 return {"status": "success", "message": f"Successfully deleted file '{relative_item_path}' from {scope_description}."}
             elif validated_path.is_dir():
                 # Check if directory is empty
                 is_empty = not any(await asyncio.to_thread(validated_path.iterdir))
                 if is_empty:
                     await asyncio.to_thread(validated_path.rmdir)
                     logger.info(f"Agent {agent_id} successfully deleted empty directory: '{relative_item_path}' from {scope_description}")
                     return {"status": "success", "message": f"Successfully deleted empty directory '{relative_item_path}' from {scope_description}."}
                 else:
                     logger.warning(f"Agent {agent_id} attempted to delete non-empty directory: '{relative_item_path}' in {scope_description}")
                     return {"status": "error", "message": f"Directory '{relative_item_path}' is not empty. Cannot delete."}
             else:
                 # Handle symlinks or other types if necessary, for now, treat as error
                 logger.warning(f"Agent {agent_id} attempted to delete unsupported item type at '{relative_item_path}' in {scope_description}")
                 return {"status": "error", "message": f"Path '{relative_item_path}' is not a file or directory. Cannot delete."}
         except PermissionError: logger.error(f"Agent {agent_id} deletion error: Permission denied for '{relative_item_path}' in {scope_description}."); return {"status": "error", "message": f"Permission denied when deleting '{relative_item_path}'."}
         except Exception as e: logger.error(f"Agent {agent_id} error deleting '{relative_item_path}' in {scope_description}: {e}", exc_info=True); return {"status": "error", "message": f"Error deleting '{relative_item_path}': {type(e).__name__} - {e}"}

    async def _regex_replace_in_file(self, base_path: Path, filename: str, regex_pattern: str, replace_text: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        validated_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed file path '{filename}' in {scope_description}."}
        if not validated_path.is_file():
            parent = validated_path.parent
            existing_files = []
            if parent.is_dir():
                try:
                    existing_files = sorted([f.name for f in parent.iterdir() if f.is_file()][:20])
                except Exception:
                    pass
            hint = f" Available files in '{parent.name}/': {', '.join(existing_files)}" if existing_files else ""
            return {"status": "error", "message": f"File not found: '{filename}' in {scope_description}.{hint}"}
        try:
            original_content = await asyncio.to_thread(validated_path.read_text, encoding='utf-8')
            new_content, num_replacements = re.subn(regex_pattern, replace_text, original_content, flags=re.MULTILINE)
            if num_replacements == 0:
                # Round 1v: like find_replace, matching nothing is a failure the agent must see, not a success.
                logger.info(f"Agent {agent_id}: regex_replace in '{filename}' ({scope_description}): no matches.")
                return {"status": "error", "replacements_made": 0, "message": f"The regex '{regex_pattern}' matched nothing in '{filename}', so nothing was changed."}
            syntax_problem = await asyncio.to_thread(self._python_edit_error, filename, original_content, new_content)
            if syntax_problem:
                return syntax_problem
            await asyncio.to_thread(validated_path.write_text, new_content, encoding='utf-8')
            logger.info(f"Agent {agent_id}: Successfully replaced {num_replacements} match(es) in file '{filename}' in {scope_description}.")
            from src.tools.source_check import edit_note  # Round 2r: JavaScript / JSON / shell syntax ('' for other files)
            check_note = await asyncio.to_thread(edit_note, new_content, filename)
            return {"status": "success", "message": f"Successfully replaced {num_replacements} match(es) in '{filename}'." + check_note, "replacements_made": num_replacements}
        except FileNotFoundError: return {"status": "error", "message": f"File not found at '{filename}' in {scope_description}."}
        except PermissionError: return {"status": "error", "message": f"Permission denied when accessing file '{filename}'."}
        except re.error as e: return {"status": "error", "message": f"Invalid regex pattern '{regex_pattern}': {e}"}
        except Exception as e: return {"status": "error", "message": f"Error during regex replace in '{filename}': {type(e).__name__} - {e}"}

    async def _copy_item(self, base_path: Path, relative_src: str, relative_dst: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_src = await self._resolve_and_validate_path(base_path, relative_src, agent_id, scope_description)
        if not val_src: return {"status": "error", "message": f"Invalid source path '{relative_src}'."}
        if not val_src.exists(): return {"status": "error", "message": f"Source '{relative_src}' does not exist."}
        val_dst = await self._resolve_and_validate_path(base_path, relative_dst, agent_id, scope_description)
        if not val_dst: return {"status": "error", "message": f"Invalid destination path '{relative_dst}'."}
        
        try:
            await asyncio.to_thread(val_dst.parent.mkdir, parents=True, exist_ok=True)
            if val_src.is_file():
                await asyncio.to_thread(shutil.copy2, val_src, val_dst)
            else:
                if val_dst.exists(): return {"status": "error", "message": f"Destination '{relative_dst}' already exists."}
                await asyncio.to_thread(shutil.copytree, val_src, val_dst)
            logger.info(f"Agent {agent_id} copied '{relative_src}' to '{relative_dst}' in {scope_description}")
            return {"status": "success", "message": f"Successfully copied '{relative_src}' to '{relative_dst}'."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error copying '{relative_src}' to '{relative_dst}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error copying '{relative_src}' to '{relative_dst}': {e}"}

    async def _move_item(self, base_path: Path, relative_src: str, relative_dst: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_src = await self._resolve_and_validate_path(base_path, relative_src, agent_id, scope_description)
        if not val_src: return {"status": "error", "message": f"Invalid source path '{relative_src}'."}
        if not val_src.exists(): return {"status": "error", "message": f"Source '{relative_src}' does not exist."}
        val_dst = await self._resolve_and_validate_path(base_path, relative_dst, agent_id, scope_description)
        if not val_dst: return {"status": "error", "message": f"Invalid destination path '{relative_dst}'."}
        
        try:
            await asyncio.to_thread(val_dst.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.move, val_src, val_dst)
            logger.info(f"Agent {agent_id} moved '{relative_src}' to '{relative_dst}' in {scope_description}")
            return {"status": "success", "message": f"Successfully moved '{relative_src}' to '{relative_dst}'."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error moving '{relative_src}' to '{relative_dst}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error moving '{relative_src}' to '{relative_dst}': {e}"}

    # --- NEW: _tree_directory method ---
    async def _tree_directory(self, base_path: Path, relative_dir: str, agent_id: str, scope_description: str, max_depth: Any = 5) -> Dict[str, Any]:
        """Recursively lists all files and directories in a tree format."""
        validated_path = await self._resolve_and_validate_path(base_path, relative_dir, agent_id, scope_description)
        if not validated_path: return {"status": "error", "message": f"Invalid or disallowed path '{relative_dir}' in {scope_description}."}
        if not validated_path.is_dir():
            return {"status": "error", "message": f"Path '{relative_dir}' is not a directory in {scope_description}. Use 'exists' to check individual files."}

        # Ensure max_depth is reasonable
        try:
            max_depth = int(max_depth) if max_depth is not None else 5
        except (ValueError, TypeError):
            max_depth = 5
        max_depth = max(1, min(max_depth, 10))  # Clamp between 1 and 10

        # Hidden/ignored patterns to skip
        skip_names = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.env', '.idea', '.vscode', '.DS_Store'}
        
        tree_lines = []
        file_count = 0
        dir_count = 0
        MAX_ENTRIES = 500  # Safety limit to prevent enormous outputs

        def build_tree(current_path: Path, prefix: str, depth: int):
            nonlocal file_count, dir_count
            if depth > max_depth or (file_count + dir_count) >= MAX_ENTRIES:
                return
            try:
                entries = sorted(current_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except PermissionError:
                tree_lines.append(f"{prefix}[permission denied]")
                return
            except OSError as e:
                tree_lines.append(f"{prefix}[error: {e}]")
                return

            # Filter out hidden/ignored
            entries = [e for e in entries if e.name not in skip_names]

            for i, entry in enumerate(entries):
                if (file_count + dir_count) >= MAX_ENTRIES:
                    tree_lines.append(f"{prefix}... (truncated, {MAX_ENTRIES} entry limit reached)")
                    return
                is_last = (i == len(entries) - 1)
                connector = "└── " if is_last else "├── "
                extension = "    " if is_last else "│   "

                if entry.is_dir():
                    dir_count += 1
                    tree_lines.append(f"{prefix}{connector}{entry.name}/")
                    if depth < max_depth:
                        build_tree(entry, prefix + extension, depth + 1)
                elif entry.is_file():
                    file_count += 1
                    # Show file size for context
                    try:
                        size = entry.stat().st_size
                        if size < 1024:
                            size_str = f"{size}B"
                        elif size < 1024 * 1024:
                            size_str = f"{size / 1024:.1f}KB"
                        else:
                            size_str = f"{size / (1024 * 1024):.1f}MB"
                        tree_lines.append(f"{prefix}{connector}{entry.name} ({size_str})")
                    except OSError:
                        tree_lines.append(f"{prefix}{connector}{entry.name}")
                else:
                    tree_lines.append(f"{prefix}{connector}{entry.name} (symlink/other)")

        try:
            await asyncio.to_thread(build_tree, validated_path, "", 0)
        except Exception as e:
            logger.error(f"Agent {agent_id} error building tree for '{relative_dir}' in {scope_description}: {e}", exc_info=True)
            return {"status": "error", "message": f"Error building directory tree: {type(e).__name__} - {e}"}

        if not tree_lines:
            return {"status": "success", "message": f"Directory '{relative_dir}' in {scope_description} is empty.", "files": 0, "directories": 0}

        header = f"Tree of '{relative_dir}' in {scope_description} (depth={max_depth}):"
        summary = f"\n\n({file_count} file(s), {dir_count} directory/ies)"
        tree_output = header + "\n" + "\n".join(tree_lines) + summary
        
        logger.info(f"Agent {agent_id} successfully listed tree for '{relative_dir}' in {scope_description} ({file_count} files, {dir_count} dirs)")
        return {"status": "success", "message": tree_output, "files": file_count, "directories": dir_count}

    # --- NEW: _exists_stat method ---
    async def _exists_stat(self, base_path: Path, relative_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        """Checks if a file or directory exists and returns metadata."""
        validated_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not validated_path: 
            return {"status": "success", "exists": False, "message": f"Invalid path '{relative_path}' in {scope_description}.", "path": relative_path}

        if not validated_path.exists():
            # Provide helpful suggestions if the parent exists
            parent = validated_path.parent
            suggestions = ""
            if parent.is_dir():
                try:
                    siblings = sorted([f.name for f in parent.iterdir()][:20])
                    if siblings:
                        suggestions = f" Items in '{parent.name}/': {', '.join(siblings)}"
                except Exception:
                    pass
            logger.info(f"Agent {agent_id} checked existence of '{relative_path}' in {scope_description}: does not exist")
            return {
                "status": "success", 
                "exists": False, 
                "message": f"'{relative_path}' does not exist in {scope_description}.{suggestions}",
                "path": relative_path
            }

        try:
            stat_info = await asyncio.to_thread(validated_path.stat)
            is_file = validated_path.is_file()
            is_dir = validated_path.is_dir()
            item_type = "file" if is_file else ("directory" if is_dir else "other")
            
            # Format size
            size_bytes = stat_info.st_size
            if size_bytes < 1024:
                size_str = f"{size_bytes} bytes"
            elif size_bytes < 1024 * 1024:
                size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"

            # Format modification time
            mod_time = datetime.datetime.fromtimestamp(stat_info.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            result = {
                "status": "success",
                "exists": True,
                "path": relative_path,
                "type": item_type,
                "size_bytes": size_bytes,
                "size_human": size_str,
                "last_modified": mod_time,
                "message": f"'{relative_path}' exists in {scope_description}. Type: {item_type}, Size: {size_str}, Last modified: {mod_time}"
            }

            # For directories, also include item count
            if is_dir:
                try:
                    children = list(await asyncio.to_thread(validated_path.iterdir))
                    child_files = sum(1 for c in children if c.is_file())
                    child_dirs = sum(1 for c in children if c.is_dir())
                    result["child_files"] = child_files
                    result["child_directories"] = child_dirs
                    result["message"] += f", Contains: {child_files} file(s) and {child_dirs} subdirectory/ies"
                except Exception:
                    pass

            logger.info(f"Agent {agent_id} checked existence of '{relative_path}' in {scope_description}: exists ({item_type})")
            return result
        except Exception as e:
            logger.error(f"Agent {agent_id} error checking '{relative_path}' in {scope_description}: {e}", exc_info=True)
            return {"status": "error", "message": f"Error checking '{relative_path}': {type(e).__name__} - {e}"}

    async def _append_to_file(self, base_path: Path, filename: str, content: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{filename}'."}
        if val_path.is_dir(): return {"status": "error", "message": f"Cannot append. '{filename}' is a directory."}
        if not val_path.is_file() and content:
            # Round 2x: the file's rejected 'write' text is kept - the appended part is added to it ("write the first
            # part, then append the rest" works even when the first part alone is not valid yet)
            drafted = await self._edit_rejected_draft(base_path, filename, val_path, agent_id, scope_description,
                                                      lambda text: (text + append_text(text, content), None),
                                                      change="append")
            if drafted is not None:
                return drafted
        try:
            is_python = str(filename).endswith(".py")
            from src.tools.source_check import check_kind
            is_checked = not is_python and check_kind(filename) is not None
            if is_python or is_checked:
                # Round 2t: a Python file is checked like every other edit: appending must not turn valid code into
                # invalid code, and the appended text starts on a new line. Round 2x: JavaScript / JSON / shell too.
                original = await asyncio.to_thread(val_path.read_text, encoding='utf-8') if val_path.is_file() else ""
                content = append_text(original, content)
                if is_python:
                    refused = python_append_error(filename, original, original + content)
                else:
                    refused = await asyncio.to_thread(checked_append_error, filename, original, original + content)
                if refused:
                    if not original and not val_path.exists():
                        # Round 2x: the first part of a NEW file is kept like a rejected 'write'
                        from src.tools import rejected_drafts
                        rejected_drafts.keep(val_path, agent_id, content)
                        refused["message"] += ("\n" + rejected_drafts.kept_note(filename).strip())
                    return refused
            await asyncio.to_thread(val_path.parent.mkdir, parents=True, exist_ok=True)
            def append_sync():
                with open(val_path, 'a', encoding='utf-8') as f:
                    f.write(content)
            await asyncio.to_thread(append_sync)
            logger.info(f"Agent {agent_id} appended to '{filename}' in {scope_description}")
            check_note = ""
            from src.tools.source_check import edit_note  # Round 2r: JavaScript / JSON / shell syntax
            if is_python:
                from src.tools.python_static_check import saved_edit_notes  # Round 2t: same hints as the other edits
                try:
                    new_text = await asyncio.to_thread(val_path.read_text, encoding='utf-8')
                    check_note = await asyncio.to_thread(saved_edit_notes, new_text, filename, base_path)
                except (OSError, UnicodeDecodeError):
                    check_note = ""
            elif is_checked:
                try:
                    new_text = await asyncio.to_thread(val_path.read_text, encoding='utf-8')
                    check_note = await asyncio.to_thread(edit_note, new_text, filename)
                except (OSError, UnicodeDecodeError):
                    check_note = ""
            return {"status": "success", "message": f"Successfully appended content to '{filename}'." + check_note}
        except Exception as e:
            logger.error(f"Agent {agent_id} error appending to '{filename}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error appending to '{filename}': {e}"}

    async def _resolve_insert_line_from_search(self, base_path: Path, filename: str, search_text: str, agent_id: str, scope_description: str, position_hint: str = "after") -> Optional[int]:
        """Resolve a line number by searching for an anchor string in the file."""
        val_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not val_path or not val_path.is_file(): return None
        try:
            lines = val_path.read_text(encoding='utf-8').splitlines()
            for i, line in enumerate(lines):
                if search_text in line:
                    if position_hint and position_hint.lower() in ("before",):
                        return i + 1  # Insert before the matched line (1-indexed)
                    else:
                        return i + 2  # Insert after the matched line (1-indexed)
            return None
        except Exception:
            return None

    async def _insert_lines_in_file(self, base_path: Path, filename: str, insert_line: Any, content: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        # Defensive: ensure insert_line is always int even if caller passes a string
        try:
            insert_line = int(insert_line)
        except (ValueError, TypeError):
            return {"status": "error", "message": f"'insert_line' must be an integer line number, got '{insert_line}'."}
        val_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{filename}'."}
        if not val_path.is_file(): return {"status": "error", "message": f"File '{filename}' does not exist."}
        try:
            original_content = await asyncio.to_thread(val_path.read_text, encoding='utf-8')
            lines = original_content.splitlines(True)
            idx = max(0, insert_line - 1)
            new_lines = content.splitlines(True)
            if lines and idx >= len(lines) and not lines[-1].endswith('\n'):
                lines[-1] += '\n'  # appending after a last line without newline must not glue two lines together
            # Round 1v: this compared/appended the two characters backslash+n instead of a newline, gluing the inserted text
            # onto the next line with a literal "\n" in between.
            if new_lines and not new_lines[-1].endswith('\n') and idx < len(lines):
                new_lines[-1] += '\n'
            new_content = "".join(lines[:idx] + new_lines + lines[idx:])
            syntax_problem = await asyncio.to_thread(self._python_edit_error, filename, original_content, new_content)
            if syntax_problem:
                return syntax_problem
            await asyncio.to_thread(val_path.write_text, new_content, encoding='utf-8')
            logger.info(f"Agent {agent_id} inserted lines at {insert_line} in '{filename}' ({scope_description})")
            from src.tools.python_static_check import saved_edit_notes  # Round 2e: same hints as 'write' and code_editor
            edit_notes = await asyncio.to_thread(saved_edit_notes, new_content, filename, base_path)  # Round 2r: + JS/JSON/sh
            return {"status": "success", "message": f"Successfully inserted content at line {insert_line} in '{filename}'."
                                                    + edit_notes}
        except Exception as e:
            logger.error(f"Agent {agent_id} error inserting lines in '{filename}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error inserting lines in '{filename}': {e}"}

    async def _replace_lines_in_file(self, base_path: Path, filename: str, start_line: int, end_line: int, content: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{filename}'."}
        if not val_path.is_file() and not (start_line > end_line or start_line < 1):
            # Round 2v: the file's rejected 'write' text is kept - the line range is replaced in it
            drafted = await self._edit_rejected_draft(base_path, filename, val_path, agent_id, scope_description,
                                                      lambda text: (replace_line_range(text, start_line, end_line, content), None))
            if drafted is not None:
                return drafted
        if not val_path.is_file(): return {"status": "error", "message": f"File '{filename}' does not exist."}
        if start_line > end_line or start_line < 1:
            return {"status": "error", "message": f"Invalid line range: {start_line} to {end_line}."}
        try:
            original_content = await asyncio.to_thread(val_path.read_text, encoding='utf-8')
            lines = original_content.splitlines(True)
            start_idx = max(0, start_line - 1)
            end_idx = max(start_idx, min(end_line, len(lines)))
            new_lines = content.splitlines(True)
            # Round 1v: real newline (was the two characters backslash+n, see insert_lines)
            if new_lines and not new_lines[-1].endswith('\n') and end_idx < len(lines):
                new_lines[-1] += '\n'
            new_content = "".join(lines[:start_idx] + new_lines + lines[end_idx:])
            syntax_problem = await asyncio.to_thread(self._python_edit_error, filename, original_content, new_content)
            if syntax_problem:
                return syntax_problem
            await asyncio.to_thread(val_path.write_text, new_content, encoding='utf-8')
            logger.info(f"Agent {agent_id} replaced lines {start_line}-{end_line} in '{filename}' ({scope_description})")
            from src.tools.python_static_check import saved_edit_notes  # Round 2e: same hints as 'write' and code_editor
            from src.tools.code_editor import removed_statement_lines
            lost = removed_statement_lines(lines, start_idx, end_idx, content)
            lost_note = (f" WARNING: this edit removed {'; '.join(lost)} - these lines are NOT in your new text any more. "
                         "If they were still needed, add them back.") if lost else ""
            edit_notes = await asyncio.to_thread(saved_edit_notes, new_content, filename, base_path)  # Round 2r: + JS/JSON/sh
            return {"status": "success", "message": f"Successfully replaced lines {start_line} to {end_line} in '{filename}'."
                                                    + lost_note + edit_notes}
        except Exception as e:
            logger.error(f"Agent {agent_id} error replacing lines in '{filename}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error replacing lines in '{filename}': {e}"}

    async def _search_replace_block_in_file(self, base_path: Path, filename: str, search_block: Optional[str], replace_block: str, agent_id: str, scope_description: str, expected_replacements: Optional[int] = None, start_marker: Optional[str] = None, end_marker: Optional[str] = None) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, filename, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{filename}'."}
        if not val_path.is_file(): return {"status": "error", "message": f"File '{filename}' does not exist."}
        try:
            def search_replace_sync():
                original_content = val_path.read_text(encoding='utf-8')

                def write_checked(new_text: str) -> None:
                    # Round 1y: never save a result that leaves a file that had content empty
                    # Round 2d: nor one that turns a valid Python file into invalid Python (the other edit actions check this
                    # since Round 1v; search_replace_block did not)
                    problem = self._python_edit_error(filename, original_content, new_text)
                    if problem:
                        raise _EditRefused(problem["message"])
                    val_path.write_text(new_text, encoding='utf-8')

                if start_marker and end_marker:
                    start_idx = original_content.find(start_marker)
                    if start_idx == -1: return False, f"start_marker '{start_marker}' not found in the file."
                    end_idx = original_content.find(end_marker, start_idx + len(start_marker))
                    if end_idx == -1: return False, f"end_marker '{end_marker}' not found after the start_marker."
                    
                    old_block = original_content[start_idx:end_idx + len(end_marker)]
                    exact_count = original_content.count(old_block)
                    if exact_count == 1:
                        new_content = original_content.replace(old_block, replace_block, 1)
                        write_checked(new_content)
                        return True, "Marker block found and replaced successfully."
                    elif exact_count > 1:
                        if expected_replacements is not None and expected_replacements == exact_count:
                            new_content = original_content.replace(old_block, replace_block)
                            write_checked(new_content)
                            return True, f"Replaced all {exact_count} identical marker blocks."
                        return False, f"Found {exact_count} identical marker blocks. Re-send with expected_replacements={exact_count} to confirm."
                    return False, "Failed to extract old block using markers."

                if search_block is None:
                    return False, "Unexpected: search_block is None but markers were not used."

                # ── TIER 1: Exact substring match ─────────────────────────────────
                exact_count = original_content.count(search_block)
                if exact_count == 1:
                    new_content = original_content.replace(search_block, replace_block, 1)
                    write_checked(new_content)
                    return True, "Exact match found and replaced."

                if exact_count > 1:
                    if expected_replacements is not None and expected_replacements == exact_count:
                        new_content = original_content.replace(search_block, replace_block)
                        write_checked(new_content)
                        return True, f"Replaced all {exact_count} exact occurrences (confirmed by expected_replacements)."
                    return False, (
                        f"Found {exact_count} exact matches. To replace ALL {exact_count} occurrences, "
                        f"re-send this call with <expected_replacements>{exact_count}</expected_replacements>. "
                        f"To replace only ONE, provide a more specific search_block that is unique in the file."
                    )

                    # ── TIER 2: First/last line matching ──────────────────────────────
                # Extract first and last non-empty lines of the search block.
                # This lets the LLM provide just the anchor lines when the full block
                # is hard to reproduce exactly, saving tokens.
                search_lines = [l for l in search_block.splitlines() if l.strip()]
                if len(search_lines) >= 2:
                    first_line = search_lines[0]
                    last_line = search_lines[-1]
                    file_lines = original_content.splitlines(keepends=True)

                    # Find all start indices in file_lines where first_line occurs
                    start_indices = [
                        i for i, fl in enumerate(file_lines)
                        if fl.rstrip('\n\r') == first_line or fl.strip() == first_line.strip()
                    ]

                    matched_spans = []
                    for start_idx in start_indices:
                        # From start_idx, search forward for the last_line
                        for end_idx in range(start_idx, min(start_idx + len(search_lines) * 3, len(file_lines))):
                            if file_lines[end_idx].rstrip('\n\r') == last_line or file_lines[end_idx].strip() == last_line.strip():
                                matched_spans.append((start_idx, end_idx))
                                break  # Take the shortest matching span from this start

                    if len(matched_spans) == 1:
                        start_idx, end_idx = matched_spans[0]
                        before = "".join(file_lines[:start_idx])
                        after = "".join(file_lines[end_idx + 1:])
                        # Preserve trailing newline convention of the replaced block
                        replacement = replace_block
                        if not replacement.endswith('\n') and after:
                            replacement += '\n'
                        new_content = before + replacement + after
                        write_checked(new_content)
                        return True, (
                            f"First/last line match found (lines {start_idx+1}–{end_idx+1}) and replaced."
                        )

                    if len(matched_spans) > 1:
                        n = len(matched_spans)
                        if expected_replacements is not None and expected_replacements == n:
                            # Replace all spans in reverse order to keep indices valid
                            file_lines_mut = list(file_lines)
                            for start_idx, end_idx in reversed(matched_spans):
                                replacement = replace_block
                                if not replacement.endswith('\n'):
                                    replacement += '\n'
                                file_lines_mut[start_idx:end_idx + 1] = [replacement]
                            write_checked("".join(file_lines_mut))
                            return True, f"Replaced all {n} first/last-line matches (confirmed by expected_replacements)."
                        return False, (
                            f"First/last line anchor matched {n} distinct blocks. "
                            f"To replace ALL {n}, re-send with <expected_replacements>{n}</expected_replacements>. "
                            f"To replace only one, provide a more specific search_block with unique surrounding lines."
                        )

                # ── TIER 3: Fuzzy fallback ─────────────────────────────────────────
                # Round 1z: ONE block of whole lines that is >= 90 % similar. This was diff_match_patch with an unlimited
                # match distance that accepted PARTIAL patches: search "c = 3" (absent) + replace "" turned "a = 1" into "1".
                from src.tools.code_editor import fuzzy_line_window, replace_fuzzy_window
                window = fuzzy_line_window(original_content, search_block)
                new_content = None
                if window:
                    first_line, last_line = window[2], window[3]
                    new_content = replace_fuzzy_window(original_content, window, replace_block)

                if new_content is None:
                    # Provide useful context: show first/last line of search_block
                    preview_first = search_lines[0][:80] if search_lines else "(empty)"
                    preview_last  = search_lines[-1][:80] if len(search_lines) > 1 else ""
                    hint = (
                        f"Could not find a matching block. "
                        f"Searched for block starting with: {repr(preview_first)}"
                        + (f" and ending with: {repr(preview_last)}" if preview_last else "") +
                        ". Please read the file and verify the exact content before retrying. Consider using the 'replace_lines' action instead, which is much more reliable if you know the exact line numbers."
                    )
                    return False, hint

                write_checked(new_content)
                return True, (f"Fuzzy match found and replaced (lines {first_line}-{last_line} were about 90 % or more "
                              "similar to search_block; read them to check the result).")

            try:
                success, msg = await asyncio.to_thread(search_replace_sync)
            except _EditRefused as refused:
                logger.warning(f"Agent {agent_id} search/replace block in '{filename}' ({scope_description}) refused: {refused}")
                return {"status": "error", "message": str(refused)}

            if success:
                logger.info(f"Agent {agent_id} search/replace block in '{filename}' ({scope_description}): {msg}")
                check_note = ""
                from src.tools.source_check import check_kind, edit_note  # Round 2r: JavaScript / JSON / shell syntax
                if check_kind(filename):
                    try:
                        new_text = await asyncio.to_thread(val_path.read_text, encoding='utf-8')
                        check_note = await asyncio.to_thread(edit_note, new_text, filename)
                    except (OSError, UnicodeDecodeError):
                        check_note = ""
                return {"status": "success", "message": f"Successfully replaced block in '{filename}'. ({msg})" + check_note}
            else:
                logger.warning(f"Agent {agent_id} failed search/replace block in '{filename}' ({scope_description}): {msg}")
                return {"status": "error", "message": msg}
        except Exception as e:
            logger.error(f"Agent {agent_id} error replacing block in '{filename}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error replacing block in '{filename}': {e}"}

    async def _git_commit(self, base_path: Path, relative_path: str, commit_message: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_commit_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                repo.git.add(all=True)
                repo.index.commit(commit_message)
                return repo.head.commit.hexsha
            
            commit_sha = await asyncio.to_thread(git_commit_sync)
            logger.info(f"Agent {agent_id} created git commit {commit_sha[:7]} in {scope_description}")
            return {"status": "success", "message": f"Successfully created git commit: {commit_sha[:7]} - {commit_message}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git command failed: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error committing in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error committing: {e}"}

    async def _git_status(self, base_path: Path, relative_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_status_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                return repo.git.status()
            
            status_output = await asyncio.to_thread(git_status_sync)
            return {"status": "success", "message": f"Git status:\n{status_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error getting git status in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error getting git status: {e}"}

    async def _git_diff(self, base_path: Path, relative_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_diff_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                return repo.git.diff()
            
            diff_output = await asyncio.to_thread(git_diff_sync)
            if not diff_output:
                diff_output = "No current modifications."
            return {"status": "success", "message": f"Git diff:\n{diff_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except Exception as e:
            logger.error(f"Agent {agent_id} error getting git diff in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error getting git diff: {e}"}

    async def _git_log(self, base_path: Path, relative_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_log_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                return repo.git.log('-n', '10', '--oneline') # Limit to 10 for token safety
            
            log_output = await asyncio.to_thread(git_log_sync)
            return {"status": "success", "message": f"Git log (last 10):\n{log_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error getting log: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error getting git log in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error getting git log: {e}"}

    async def _git_branch(self, base_path: Path, relative_path: str, branch: Optional[str], agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_branch_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                if branch:
                    repo.git.branch(branch)
                    return f"Created branch '{branch}'"
                else:
                    return repo.git.branch()
            
            result_output = await asyncio.to_thread(git_branch_sync)
            return {"status": "success", "message": f"Git branch result:\n{result_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error on branch: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error executing git branch in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error executing git branch: {e}"}

    async def _git_checkout(self, base_path: Path, relative_path: str, branch: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_checkout_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                repo.git.checkout(branch)
                return f"Switched or checked out to '{branch}'"
            
            result_output = await asyncio.to_thread(git_checkout_sync)
            return {"status": "success", "message": result_output}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error on checkout: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error checking out branch in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error checking out branch: {e}"}

    async def _git_add(self, base_path: Path, relative_path: str, files_to_add: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_add_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                # Split comma separated files
                files_list = [f.strip() for f in files_to_add.split(',') if f.strip()]
                repo.git.add(*files_list)
                return f"Staged: {', '.join(files_list)}"
            
            result_output = await asyncio.to_thread(git_add_sync)
            return {"status": "success", "message": result_output}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error on add: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error staging files in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error staging files: {e}"}

    async def _git_init(self, base_path: Path, relative_path: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        
        # Prevent repeated git inits from consuming cycles (idempotency override)
        if (val_path / ".git").exists():
            logger.info(f"Agent {agent_id} attempted git init but repo already exists.")
            return {"status": "success", "message": f"Git repository is already initialized in {val_path}/.git/"}
            
        try:
            def git_init_sync():
                repo = git.Repo.init(val_path)
                return "Initialized empty Git repository"
            
            result_output = await asyncio.to_thread(git_init_sync)
            logger.info(f"Agent {agent_id} initialized git repo in '{relative_path}' ({scope_description})")
            return {"status": "success", "message": result_output}
        except Exception as e:
            logger.error(f"Agent {agent_id} error initializing git repo in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error initializing git repo: {e}"}

    async def _git_pull(self, base_path: Path, relative_path: str, remote: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_pull_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                return repo.git.pull(remote)
            
            result_output = await asyncio.to_thread(git_pull_sync)
            return {"status": "success", "message": f"Git pull successful:\n{result_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error pulling repository: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error pulling repo in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error pulling repo: {e}"}

    async def _git_push(self, base_path: Path, relative_path: str, remote: str, agent_id: str, scope_description: str) -> Dict[str, Any]:
        val_path = await self._resolve_and_validate_path(base_path, relative_path, agent_id, scope_description)
        if not val_path: return {"status": "error", "message": f"Invalid path '{relative_path}'."}
        try:
            def git_push_sync():
                repo = git.Repo(val_path, search_parent_directories=True)
                return repo.git.push(remote)
            
            result_output = await asyncio.to_thread(git_push_sync)
            return {"status": "success", "message": f"Git push successful:\n{result_output}"}
        except InvalidGitRepositoryError:
            return {"status": "error", "message": f"Path '{relative_path}' is not within a valid git repository."}
        except GitCommandError as e:
            return {"status": "error", "message": f"Git error pushing repository: {e}"}
        except Exception as e:
            logger.error(f"Agent {agent_id} error pushing repo in '{relative_path}': {e}", exc_info=True)
            return {"status": "error", "message": f"Error pushing repo: {e}"}
