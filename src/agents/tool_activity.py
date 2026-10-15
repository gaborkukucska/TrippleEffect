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



# --- Admin planning retry (Round 1p) -------------------------------------------------------------------------------------
# 2026-10-16 07:06: Admin entered 'planning' and answered with a one-sentence announcement ("Here's the complete workflow
# we're going to follow, followed by a JSON block ...", 199 chars) but never wrote the plan or the JSON title block. No
# workflow trigger matched, the scheduler let Admin idle ("plan output expected"), and the user never got a plan.
MAX_PLANNING_RETRIES = 2

PLANNING_RETRY_MESSAGE = (
    "[Framework System Message]: Your last reply did NOT contain the project plan, so no project was created. "
    "Do not describe or announce the plan: write it now. Reply with (1) the complete plan as markdown (a short objective, "
    "then the numbered tasks), and (2) at the very end a JSON block that contains only the project title, exactly like:\n"
    "```json\n{\"title\": \"<ProjectName>\"}\n```"
)


def last_assistant_text(history) -> str:
    """Text of the most recent assistant message in a message history ('' if there is none)."""
    for msg in reversed(list(history or [])):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if content:
                return str(content)
    return ""


def planning_reply_needs_retry(text: Optional[str]) -> bool:
    """
    True if an Admin reply produced in the planning state (that did not trigger project creation) should be re-requested.
    A reply that ends by asking the user a question is a legitimate clarification and is left alone.
    """
    stripped = (text or "").strip()
    if not stripped:
        return True
    tail = stripped[-300:]
    if "?" in tail and "```" not in tail:
        return False
    return True


# END OF FILE src/agents/tool_activity.py
