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


# --- Unhandled project request (Round 1r) ---------------------------------------------------------------------------------
# 2026-10-16 07:36: the user sent "Hello :) Please start a small project called ..." while Admin was still in its 'startup'
# state. Admin replied with chat text ("let's get this thing built!") instead of calling request_state(planning), the cycle
# ended cleanly, and nothing ever started: no plan, no project. The framework now performs the switch itself.
_PROJECT_WORD_RE = _re.compile(r"\bproject\b", _re.IGNORECASE)
_PROJECT_VERB_RE = _re.compile(r"\b(start|create|build|make|develop|write|implement|set\s*up|begin|kick\s*off)\b", _re.IGNORECASE)


def last_user_text(history) -> str:
    """Text of the most recent user-role message in a message history ('' if there is none)."""
    for msg in reversed(list(history or [])):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            return content if isinstance(content, str) else str(content or "")
    return ""


def looks_like_project_request(text: Optional[str]) -> bool:
    """
    Conservative check for a human message that asks to start a project: long enough to contain requirements, mentions
    'project' and a start/build verb. Framework/PM messages (they start with '[') and short chat or approvals never match.
    """
    stripped = (text or "").strip()
    if len(stripped) < 100 or stripped.startswith("["):
        return False
    return bool(_PROJECT_WORD_RE.search(stripped) and _PROJECT_VERB_RE.search(stripped))


def is_unhandled_project_request(agent) -> bool:
    """True if the newest user message is a project request that this agent has not yet been moved to planning for."""
    text = last_user_text(getattr(agent, "message_history", None))
    if not looks_like_project_request(text):
        return False
    return getattr(agent, "_auto_planning_for", None) != text


# --- PM standby guard (Round 1s) -------------------------------------------------------------------------------------------
# 2026-10-16 09:45: all worker tasks were finished, but PM1 (history just cleared on entering pm_manage) requested pm_standby
# without listing the tasks. No audit ran, Admin was never told and the user never got a completion report.
PM_STANDBY_GUARD_TAG = "[Framework Standby Check]"

_PM_STANDBY_NO_LISTING = (
    PM_STANDBY_GUARD_TAG + " You requested standby without checking the task list. First call `project_management` with "
    "action=\"list_tasks\". If any task is still 'todo' or 'in_progress', request pm_standby (or assign work). If every task "
    "is finished, request pm_audit instead."
)
_PM_STANDBY_ALL_DONE = (
    PM_STANDBY_GUARD_TAG + " Your last task list shows NO open tasks, so the project work is finished. Do NOT go to standby: "
    "request the pm_audit state now so the results are verified and the Admin AI is told the project is complete."
)
_DONE_PROGRESS = {"finished", "completed", "complete", "done", "closed", "decomposed", "deleted"}


def _last_task_listing(history):
    """The parsed JSON of the most recent list_tasks-style tool result in a message history, else None."""
    for msg in reversed(list(history or [])):
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or '"tasks"' not in content:
            continue
        start = content.find("{")
        if start < 0:
            continue
        try:
            data = json.loads(content[start:])
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            return data
    return None


def pm_standby_block_message(agent_type, agent_state, requested_state, history, agent_id) -> Optional[str]:
    """
    A framework message if a PM in pm_manage should NOT be allowed to go to standby right now, else None.
    Blocks (once per manage visit - the history is cleared on entering pm_manage) when the PM has not looked at the task
    list, or when its latest task list shows no open task. Tasks assigned to the PM itself are ignored.
    """
    if (agent_type, agent_state, requested_state) != ("pm", "pm_manage", "pm_standby"):
        return None
    for msg in history or []:
        if isinstance(msg, dict) and isinstance(msg.get("content"), str) and msg["content"].startswith(PM_STANDBY_GUARD_TAG):
            return None
    listing = _last_task_listing(history)
    if listing is None:
        return _PM_STANDBY_NO_LISTING
    for task in listing["tasks"]:
        if not isinstance(task, dict) or task.get("assignee") == agent_id:
            continue
        progress = str(task.get("task_progress") or task.get("status") or "").strip().lower()
        if progress and progress not in _DONE_PROGRESS:
            return None
    return _PM_STANDBY_ALL_DONE


# --- Markdown plan without a JSON title block (Round 1w) --------------------------------------------------------------------
# 2026-10-17 00:58-01:34: in 'planning' Admin wrote a complete numbered plan three times ("# Temp Converter CLI Project Plan",
# then "**Objective:** ...") but never the ```json {"title": ...}``` block. Only that block triggered project creation, so
# every plan was thrown away (each after a 7-14 minute Guardian review) and the user was told no plan could be produced.
MIN_MARKDOWN_PLAN_ITEMS = 3
_PLAN_ITEM_RE = _re.compile(r"^[ \t]*(?:\d{1,2}[.)][ \t]+\S|#{2,4}[ \t]*(?:task|step)[ \t]*\d{1,2}\b)", _re.IGNORECASE | _re.MULTILINE)
_NAMED_PROJECT_RE = _re.compile(r"\b(?:called|named|titled)[ \t]*[:\-]?[ \t]*[\"\u201c'\u2018`]([^\"\u201d'\u2019`\n]{2,60})[\"\u201d'\u2019`]", _re.IGNORECASE)
_HEADING_RE = _re.compile(r"^[ ]{0,3}#{1,3}[ \t]+(.+?)[ \t#]*$", _re.MULTILINE)
_TITLE_NOISE_RE = _re.compile(r"(?:[\s:\-\u2013\u2014]*\b(?:project[ \t]+plan|implementation[ \t]+plan|plan|project|overview))+[ \t]*$", _re.IGNORECASE)
_GENERIC_HEADINGS = frozenset({"objective", "overview", "tasks", "plan", "project", "summary", "tech stack", "introduction", "goal"})


def looks_like_markdown_plan(text: Optional[str]) -> bool:
    """True for a real plan written as Markdown: long enough, at least 3 numbered items, and not ending with a question."""
    stripped = (text or "").strip()
    if len(stripped) < 200 or not planning_reply_needs_retry(stripped):
        return False
    return len(_PLAN_ITEM_RE.findall(stripped)) >= MIN_MARKDOWN_PLAN_ITEMS


def _clean_title(raw: Any) -> str:
    text = _re.sub(r"[*_`\[\]]", "", str(raw or "")).strip()
    text = _TITLE_NOISE_RE.sub("", text).strip(" :-\u2013\u2014")
    words = text.split()
    if not words or len(words) > 8:
        return ""
    return " ".join(words)[:60].strip()


def plan_title_from_text(plan_text: Optional[str], history=None) -> Optional[str]:
    """
    A project title for a plan that has no JSON title block: the name the user gave ('called "Temp Converter CLI"') in the
    most recent user message that names one, else the plan's first non-generic Markdown heading (without 'Project Plan').
    None if neither exists.
    """
    for msg in reversed(list(history or [])):
        if not (isinstance(msg, dict) and msg.get("role") == "user" and isinstance(msg.get("content"), str)):
            continue
        if msg["content"].lstrip().startswith("["):
            continue  # framework / system notices, not the human
        match = _NAMED_PROJECT_RE.search(msg["content"])
        if match:
            title = _clean_title(match.group(1))
            if title:
                return title
    for match in _HEADING_RE.finditer(plan_text or ""):
        title = _clean_title(match.group(1))
        if title and title.lower() not in _GENERIC_HEADINGS:
            return title
    return None


# --- Work done in worker_decompose (Round 1x) -------------------------------------------------------------------------------
# 2026-10-17 12:33-13:39: W1 wrote and edited tests/test_temp_convert.py for 66 minutes while still in worker_decompose, where
# its replies are capped at WORKER_DECOMPOSE_STATE_MAX_TOKENS (1024): incomplete files, empty replies, failed edits.
def files_written_in_last_turn(history) -> List[str]:
    """Paths written or edited SUCCESSFULLY by the tool calls of the most recent assistant message that made tool calls."""
    messages = [m for m in (history or []) if isinstance(m, dict)]
    for index in range(len(messages) - 1, -1, -1):
        msg = messages[index]
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            continue
        results = {m.get("tool_call_id"): m.get("content", "") for m in messages[index + 1:]
                   if m.get("role") == "tool" and m.get("tool_call_id") is not None}
        written: List[str] = []
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            path = _written_path(call)
            call_id = call.get("id")
            if path and call_id in results and not is_tool_result_failure(call.get("name"), results[call_id]) and path not in written:
                written.append(path)
        return written
    return []


# --- Duplicate file reads (Round 1y) -----------------------------------------------------------------------------------------
# A repeated read of the same file was answered from the cached FIRST result, even when the file had been edited in between
# (stale content), and every blocked read cost a Guardian LLM call (59 calls of ~35 s on 2026-10-17/18, none gave a verdict).
FILE_READ_ACTIONS = frozenset({"read", "read_file", "read_with_lines", "read_file_with_lines", "list", "list_directory",
                               "list_files", "tree", "exists"})


def _clean_action(args: Dict[str, Any]) -> str:
    return str(args.get("action", "")).strip().strip("<>/\"' \t").lower()


def is_file_read_call(tool_name: Optional[str], arguments: Any) -> bool:
    """True for a file_system / code_editor call that only looks at files (read, list, tree, exists)."""
    args = arguments if isinstance(arguments, dict) else _call_arguments({"arguments": arguments})
    action = _clean_action(args)
    if tool_name == "file_system":
        return action in FILE_READ_ACTIONS
    if tool_name == "code_editor":
        return action in CODE_EDITOR_READ_ACTIONS or action in FILE_READ_ACTIONS
    return False


def read_call_target(arguments: Any) -> str:
    """The path a read call looks at ('.' if none is given)."""
    args = arguments if isinstance(arguments, dict) else _call_arguments({"arguments": arguments})
    for key in _PATH_KEYS + ("dir", "directory", "folder"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "."


def files_changed_after(history, index: int) -> bool:
    """True if a message after position `index` of history made a SUCCESSFUL file_system / code_editor write or edit."""
    messages = list(history or [])
    later = [m for m in messages[index + 1:] if isinstance(m, dict)]
    results = {m.get("tool_call_id"): m.get("content", "") for m in later
               if m.get("role") == "tool" and m.get("tool_call_id") is not None}
    for msg in later:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict) or not _written_path(call):
                continue
            call_id = call.get("id")
            if call_id in results and not is_tool_result_failure(call.get("name"), results[call_id]):
                return True
    return False


# END OF FILE src/agents/tool_activity.py
