# START OF FILE src/agents/tool_activity.py
"""
Small, dependency-free helpers that read what tools actually did (Round 1n).

Two framework decisions used to be made by scanning raw text, and both produced false alarms in the 2026-10-15
Temp_Convert_CLI run:

* "Did this tool call fail?"  The cycle handler treated ANY non-JSON tool result that contained the word "error" as a
  failure. A successful read of PROJECT_STRUCTURE.md ("... Implement error handling ...") and a successful but empty
  web_search (whose suggestion list says "Check for spelling errors") were counted as failed cycles. Three of those in a
  row make the scheduler force the worker into the report state ("3 consecutive failed cycles").
  -> is_tool_result_failure(): framework failure markers always count; for tools whose successful output is arbitrary
     text (file contents, search hits, documentation) the bare word "error" no longer does.

* "Did the worker write code?"  The skipped-testing nudge searched every assistant/tool message for any file name with a
  code extension, so merely READING PROJECT_STRUCTURE.md (which lists temp_convert.py as planned) made the framework tell
  the worker it had "created/modified code files".
  -> written_code_files(): only successful write-type tool calls count.
"""
import json
import os
from typing import Any, Dict, Iterable, List, Optional

# Prefixes the framework puts in front of a failed tool result (lower-case, compared after lstrip()).
FRAMEWORK_FAILURE_PREFIXES = (
    "[tool execution failed]",
    "[toolexec error",
    "[tool execution error",
    "[tool error",
    "error:",
)

# Tools whose SUCCESSFUL output is arbitrary text (file contents, search results, documentation, issue text) and can
# therefore contain the word "error" without the call having failed. For these only a framework failure prefix or a JSON
# {"status": "error"} marks a failure. Every other tool keeps the legacy substring rule.
CONTENT_RETURNING_TOOLS = frozenset({
    "file_system", "web_search", "zim_search", "knowledge_base", "codebase_search",
    "tool_information", "asset_search", "github_tool", "system_help",
})


def is_tool_result_failure(tool_name: Optional[str], content: Any) -> bool:
    """True if a tool result (as stored in the message history) represents a failed tool call."""
    text = "" if content is None else str(content)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    else:
        # Structured result: only an explicit error status is a failure (unchanged behaviour).
        return isinstance(data, dict) and data.get("status") == "error"

    lowered = text.lower()
    if lowered.lstrip().startswith(FRAMEWORK_FAILURE_PREFIXES):
        return True
    if tool_name in CONTENT_RETURNING_TOOLS:
        return False
    # Legacy rule, unchanged for all other tools (command output, test runs, ...).
    return "error" in lowered or "[tool execution failed]" in lowered


CODE_EXTENSIONS = frozenset({
    ".js", ".py", ".html", ".css", ".ts", ".jsx", ".tsx",
    ".sh", ".go", ".rs", ".java", ".c", ".cpp", ".rb", ".php",
})

# file_system actions that create or change a file's content.
FILE_WRITE_ACTIONS = frozenset({
    "write", "append", "insert_lines", "replace_lines", "find_replace", "regex_replace", "search_replace_block",
})
# code_editor actions that only look at a file.
CODE_EDITOR_READ_ACTIONS = frozenset({"view", "read", "show", "list", "search", "find", "get", "open", "cat"})

_PATH_KEYS = ("filename", "path", "file_path", "filepath", "file", "file_name", "target_file")


def _call_arguments(call: Dict[str, Any]) -> Dict[str, Any]:
    args = call.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError, ValueError):
            args = {}
    return args if isinstance(args, dict) else {}


def _written_path(call: Dict[str, Any]) -> Optional[str]:
    """The file path a write-type tool call targets, or None if the call does not write a file."""
    name = call.get("name")
    args = _call_arguments(call)
    action = str(args.get("action", "")).strip().lower()
    if name == "file_system":
        if action not in FILE_WRITE_ACTIONS:
            return None
    elif name == "code_editor":
        if action in CODE_EDITOR_READ_ACTIONS:
            return None
    else:
        return None
    for key in _PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def written_code_files(history: Iterable[Dict[str, Any]], limit: int = 5) -> List[str]:
    """
    Code files (by extension) that the agent wrote or edited through a SUCCESSFUL file_system / code_editor call, in
    order of first appearance. Reading a file, listing a directory, or a failed write does not count. A call whose
    result is not in the history yet is not counted either.
    """
    messages = [m for m in history if isinstance(m, dict)]
    results: Dict[Any, Any] = {}
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id") is not None:
            results[msg["tool_call_id"]] = msg.get("content", "")

    found: List[str] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            path = _written_path(call)
            if not path or os.path.splitext(path.lower())[1] not in CODE_EXTENSIONS:
                continue
            call_id = call.get("id")
            if call_id not in results or is_tool_result_failure(call.get("name"), results[call_id]):
                continue
            if path not in found:
                found.append(path)
    return found[:limit]
# END OF FILE src/agents/tool_activity.py
