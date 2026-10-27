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
import re
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


# Round 3o: the executor's refusals BEFORE a tool runs ("Error from tool 'file_system': The file was NOT created: ...", the
# policy gate, the compaction placeholder), also after a framework note line ("[System Warning: ...]"). 2026-10-25 13:30:00
# W1's refused write of W2's tests/test_snake.py counted as a SUCCESSFUL file_system call: the framework then told W1 it had
# "created/modified code files (tests/test_snake.py)" and took W1's three identical failed test runs for "edit, then run
# again - not a loop". Compared on the lower-cased text, at the start of a line (a file's content is shown as 'N: ...').
_EXECUTOR_REFUSAL_RE = re.compile(r"^error from tool '[\w.-]+': ", re.MULTILINE)


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
    if _EXECUTOR_REFUSAL_RE.search(lowered[:2000]):  # Round 3o
        return True
    if tool_name in CONTENT_RETURNING_TOOLS:
        return False
    # Legacy rule, unchanged for all other tools (command output, test runs, ...).
    return "error" in lowered or "[tool execution failed]" in lowered


# Round 3w: a command that RAN and reported its exit code. 2026-10-27 01:23:13 PM1 was stopped ("3 consecutive tool execution
# failures ... to prevent infinite loop") for running the project's failing test suite three times - the runs worked; their
# result was that tests fail. Such a result is information for the agent, not a failed tool call.
_COMMAND_RAN_RE = re.compile(
    r"command execution completed with return code -?\d+|command execution (?:succeeded|failed) \(exit code: ?-?\d+\)|"
    r"test command failed with exit code -?\d+|tests failed: the command ended with exit code", re.IGNORECASE)


def command_ran(tool_name: Optional[str], content: Any) -> bool:
    """True for a command_executor / test_runner result of a command that ran and ended with an exit code (0 or not)."""
    if tool_name not in ("command_executor", "test_runner"):
        return False
    return bool(_COMMAND_RAN_RE.search(str(content or "")[:4000]))


CODE_EXTENSIONS = frozenset({
    ".js", ".py", ".html", ".css", ".ts", ".jsx", ".tsx",
    ".sh", ".go", ".rs", ".java", ".c", ".cpp", ".rb", ".php",
})

# file_system actions that create or change a file's content.
FILE_WRITE_ACTIONS = frozenset({
    "write", "append", "insert_lines", "replace_lines", "find_replace", "regex_replace", "search_replace_block",
})
# Round 2n: file_system actions that mean "write this complete file, replacing the old one" (force_overwrite). W1 sent
# 'force_overwrite_file_write' 5 times on 2026-10-20 23:39-23:45 and got "Invalid action" each time.
FORCE_WRITE_ACTIONS = frozenset({
    "force_overwrite_file_write", "force_overwrite_write", "force_write", "force_write_file", "force_overwrite",
    "overwrite", "overwrite_file", "overwrite_write", "write_force", "write_overwrite", "rewrite", "rewrite_file",
    "replace_file", "write_file_force", "force_rewrite",
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
        if action not in FILE_WRITE_ACTIONS and action not in FORCE_WRITE_ACTIONS:  # Round 2n: force writes are writes
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


# --- Project request sent to 'work' instead of 'planning' (Round 2g) ------------------------------------------------------
# 2026-10-19 18:16:03 (qwen3.5:9b): the user sent the Temp Converter CLI project request while Admin was in 'startup'. Admin
# thought "This is definitely a project creation request" and still called request_state(state='work') with no task. The
# admin 'work' state is for investigating with tools; it has no project workspace (codebase_search: "Shared workspace does
# not exist."), so Admin spent 8 minutes on an empty code_editor call, an empty reply and wrong paths. A project request
# that the framework would otherwise hand to 'work' as the work task now goes to 'planning', like Admin's own choice would.
ADMIN_WORK_TO_PLANNING_NOTE = (
    "[Framework Note]: The user asked for a project to be built, so your request for the 'work' state was changed to "
    "'planning'. Projects are always planned first and then built by a Project Manager and workers; the 'work' state is "
    "only for answering questions with tools and has no project workspace. Write the project plan now."
)
_PLANNED_MARKERS = (
    "transitioned to state 'planning'",  # the agent-history note before Round 2x
    "transitioning to state 'planning'",  # Round 2x: state_change_record
    "state changed to planning",
)
# A status question about an existing project ("what is the status/progress of the project I asked you to build?") is a
# legitimate reason for 'work'.
_STATUS_QUESTION_RE = _re.compile(r"\b(status|progress)\b", _re.IGNORECASE)


def project_request_needs_planning(history, task_description: Optional[str], auto_planned_for: Optional[str] = None) -> bool:
    """
    True if an Admin request for the 'work' state is really the newest user message's project request, which has not
    been planned yet. A request that names its own task (e.g. 'Analyze stalled agent report') keeps 'work', unless that
    task text itself is a project request.
    """
    messages = list(history or [])
    own_task = (task_description or "").strip() if isinstance(task_description, str) else ""
    if own_task and not looks_like_project_request(own_task):
        return False
    request_index = None
    for index in range(len(messages) - 1, -1, -1):
        msg = messages[index]
        if isinstance(msg, dict) and msg.get("role") == "user":
            request_index = index
            break
    if request_index is None:
        return False
    content = messages[request_index].get("content")
    text = content if isinstance(content, str) else str(content or "")
    if not looks_like_project_request(text) or _STATUS_QUESTION_RE.search(text):
        return False
    if auto_planned_for is not None and auto_planned_for == text:
        return False
    for msg in messages[request_index + 1:]:
        later = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(later, str) and any(marker in later.lower() for marker in _PLANNED_MARKERS):
            return False
    return True


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
_PM_STANDBY_FILTERED = (
    PM_STANDBY_GUARD_TAG + " Your last task list was FILTERED ({filters}), so it does not show every task. Call "
    "`project_management` with action=\"list_tasks\" and no filters. If any task is still open, request pm_standby (or "
    "assign work). If every task is finished, request pm_audit instead."
)
_DONE_PROGRESS = {"finished", "completed", "complete", "done", "closed", "decomposed", "deleted"}


def _listing_filters(history, tool_message) -> List[str]:
    """
    Round 2c: the filters (other than project_filter) of the list_tasks call that produced tool_message. On 2026-10-18 21:53 a
    listing with task_progress_filter='todo' and tags_filter excluding 'assigned' came back empty although 5 tasks were open.
    """
    call_id = tool_message.get("tool_call_id")
    for msg in reversed(list(history or [])):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("id") == call_id:
                args = _call_arguments(call)
                return sorted(f"{key}={value!r}" for key, value in args.items()
                              if (key.endswith("_filter") or key in ("assignee", "status", "task_progress", "tags"))
                              and key != "project_filter" and value not in (None, "", [], {}))
    return []


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
            data["_filters"] = _listing_filters(history, msg)  # Round 2c
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
    if listing.get("_filters"):
        return _PM_STANDBY_FILTERED.format(filters=", ".join(listing["_filters"]))  # Round 2c: not proof of "finished"
    return _PM_STANDBY_ALL_DONE


# --- Markdown plan without a JSON title block (Round 1w) --------------------------------------------------------------------
# 2026-10-17 00:58-01:34: in 'planning' Admin wrote a complete numbered plan three times ("# Temp Converter CLI Project Plan",
# then "**Objective:** ...") but never the ```json {"title": ...}``` block. Only that block triggered project creation, so
# every plan was thrown away (each after a 7-14 minute Guardian review) and the user was told no plan could be produced.
MIN_MARKDOWN_PLAN_ITEMS = 3
_PLAN_ITEM_RE = _re.compile(r"^[ \t]*(?:\d{1,2}[.)][ \t]+\S|#{2,4}[ \t]*(?:task|step)[ \t]*\d{1,2}\b)", _re.IGNORECASE | _re.MULTILINE)
_NAMED_PROJECT_RE = _re.compile(r"\b(?:called|named|titled)[ \t]*[:\-]?[ \t]*[\"\u201c'\u2018`]([^\"\u201d'\u2019`\n]{2,60})[\"\u201d'\u2019`]", _re.IGNORECASE)
_HEADING_RE = _re.compile(r"^[ ]{0,3}#{1,3}[ \t]+(.+?)[ \t#]*$", _re.MULTILINE)
# Round 2r: 2026-10-21 19:16:04 Admin's plan ended with "**Title:** WiFi Snake Multiplayer Game" (no JSON block, no heading):
# "no title could be derived", and planning was requested again.
_TITLE_LINE_RE = _re.compile(r"^[ \t]*(?:[-*>][ \t]+)?\**[ \t]*(?:project[ \t]+)?(?:title|name)[ \t]*\**[ \t]*:[ \t]*\**[ \t]*(\S.*?)[ \t]*$",
                             _re.IGNORECASE | _re.MULTILINE)
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
    most recent user message that names one, else (Round 2r) a 'Title: ...' / '**Project Name:** ...' line of the plan, else
    the plan's first non-generic Markdown heading (without 'Project Plan'). None if none exists.
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
    for match in _TITLE_LINE_RE.finditer(plan_text or ""):
        title = _clean_title(match.group(1))
        if title and title.lower() not in _GENERIC_HEADINGS:
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
                               "list_files", "tree", "exists", "list_with_lines"})  # Round 2f: + list_with_lines


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


def last_file_target(history) -> Optional[str]:
    """
    Round 2n: the FILE (a path with an extension) of the agent's most recent file_system / code_editor call in history, or
    None. Used when a 'read' names no file at all (W1, 2026-10-20 23:50-23:55: 9 reads with only start_line/end_line).
    """
    for msg in reversed(list(history or [])):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for call in reversed(msg.get("tool_calls") or []):
            if not isinstance(call, dict) or call.get("name") not in ("file_system", "code_editor"):
                continue
            args = _call_arguments(call)
            for key in _PATH_KEYS:
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    candidate = value.strip()
                    name = candidate.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
                    if "." in name.strip(".") and not candidate.endswith("/"):
                        return candidate
                    break
    return None


def unread_message_ids(agent, history=None) -> List[str]:
    """Round 2n: ids of the messages in the agent's history that it has not marked read, in order."""
    read_ids = getattr(agent, "read_message_ids", None) or {}
    found: List[str] = []
    for msg in (history if history is not None else getattr(agent, "message_history", None)) or []:
        msg_id = msg.get("message_id") if isinstance(msg, dict) else None
        if msg_id and msg_id not in read_ids and msg_id not in found:
            found.append(msg_id)
    return found


def mark_read_repeat_notice(agent, tool_args: Any) -> Optional[str]:
    """
    Round 2n: the answer to a mark_message_read call for a message the agent ALREADY marked read, or None for a new id.
    2026-10-20 23:32-23:37 W1 (worker_report) marked 'msg_1792509985381_9d8ee4' read 6 times while two OTHER messages stayed
    unread; every repeat was "successful", went through the duplicate check and a Guardian LLM call, and ended in a forced
    worker_wait.
    """
    args = tool_args if isinstance(tool_args, dict) else _call_arguments({"arguments": tool_args})
    msg_id = args.get("message_id")
    if not isinstance(msg_id, str) or not msg_id.strip():
        return None
    read_ids = getattr(agent, "read_message_ids", None) or {}
    if msg_id.strip() not in read_ids:
        return None
    unread = unread_message_ids(agent)
    text = f"Message '{msg_id.strip()}' was ALREADY marked as read earlier, so nothing changed. Do not mark it again."
    if unread:
        text += (f" Messages still unread: {', '.join(unread[:5])}. Mark one of THOSE after you have acted on it, or "
                 "continue with your task.")
    else:
        text += " You have no unread messages: continue with your task."
    return text


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


# --- Commands run again after files changed (Round 3d) ------------------------------------------------------------------------
# 2026-10-23 12:07:31 W2 fixed tests/run_integration_tests.sh; its re-run of `bash tests/run_integration_tests.sh` at 12:07:52 was
# a "duplicate" of the 12:06:39 run and got the OLD output back. W1 told PM1 at 13:41: "The command_executor tool keeps returning
# cached results when I run the same command multiple times" while it edited the tests between the runs. A command (a test run
# above all) is run again when any file of the workspace changed since its last run - also a file another agent changed, which
# the agent's own history cannot show.
COMMAND_RUN_TOOLS = frozenset({"command_executor", "test_runner"})
_FINGERPRINT_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv", ".te_background",
                                    ".mypy_cache", "coverage"})
MAX_FINGERPRINT_FILES = 5000


def is_command_run_call(tool_name: Optional[str], arguments: Any = None) -> bool:
    """True for a call that runs a command or tests (its result depends on the files at the time it runs)."""
    return str(tool_name or "") in COMMAND_RUN_TOOLS


def workspace_fingerprint(workspace: Any) -> Optional[tuple]:
    """
    (file count, newest modification time in ns, total size) of the workspace's files (dependency/cache folders left out),
    or None if it cannot be computed (missing folder, too many files). None means "unknown": treat it as changed.
    """
    import os
    from pathlib import Path
    try:
        root = Path(workspace)
        if not root.is_dir():
            return None
        count = newest = total = 0
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _FINGERPRINT_SKIP_DIRS]
            for name in files:
                count += 1
                if count > MAX_FINGERPRINT_FILES:
                    return None
                try:
                    stat = os.stat(os.path.join(current, name))
                except OSError:
                    continue
                newest = max(newest, stat.st_mtime_ns)
                total += stat.st_size
        return count, newest, total
    except Exception:
        return None


def workspace_changed_since_last_run(agent: Any, signature: Any, workspace: Any) -> bool:
    """
    True if the workspace differs from what it was when this agent last made this call (or there is no record of that, or
    the state is unknown): then the call must really run instead of being answered from the history. Records the current
    state for the next time.
    """
    fingerprint = workspace_fingerprint(workspace) if workspace is not None else None
    marks = getattr(agent, "_command_workspace_marks", None)
    if not isinstance(marks, dict):
        marks = {}
        try:
            agent._command_workspace_marks = marks
        except Exception:
            return True
    previous = marks.get(signature)
    marks[signature] = fingerprint
    return previous is None or fingerprint is None or previous != fingerprint


# --- Repeated file writes and edits (Round 2e) -------------------------------------------------------------------------------
# 2026-10-19 05:20:38 W2 replaced lines 48-50 of tests/test_temp_convert.py; at 05:21:50 it sent exactly the same call again and
# the Guardian (an LLM call) answered ALLOW. Applied twice, an edit by line numbers or an insert changes lines that have moved
# in the meantime. Writing the same complete content again changes nothing (the tool says so), so that may simply run.
def _is_whole_file_write(tool_name: Optional[str], args: Dict[str, Any]) -> bool:
    action = _clean_action(args)
    if tool_name == "file_system":
        return action == "write" or action in FORCE_WRITE_ACTIONS  # Round 2n
    if tool_name == "code_editor":
        return action.startswith(("write", "create", "save", "overwrite"))
    return False


def duplicate_file_change_verdict(tool_name: Optional[str], arguments: Any):
    """
    (verdict, feedback) for a call that repeats an earlier SUCCESSFUL call with identical arguments, when it writes or edits a
    file: a complete write may run again ('ALLOW'), any other edit must not ('BLOCK_AND_GUIDE'). None for other calls.
    """
    args = arguments if isinstance(arguments, dict) else _call_arguments({"arguments": arguments})
    path = _written_path({"name": tool_name, "arguments": args})
    if not path:
        return None
    if _is_whole_file_write(tool_name, args):
        return "ALLOW", ""
    return "BLOCK_AND_GUIDE", (
        f"You already made exactly this edit to '{path}' and it succeeded (its result is above), so the change is already in "
        "the file. It was NOT applied a second time: the line numbers have moved since. Do not send it again. Read "
        f"'{path}' to see its current content, then make the NEXT change, or move on to your next step if the file is complete."
    )


# --- PM polling in pm_manage (Round 2e) ---------------------------------------------------------------------------------------
# 2026-10-19 04:08-05:05: while W2 worked on the tests, PM1 spent 57 minutes in pm_manage listing tasks and agents (about 17
# LLM calls of 1-4 minutes) without assigning, messaging or changing anything. Ollama answers one request at a time, so every
# poll made the working agent wait. After PM_MANAGE_MAX_IDLE_TURNS turns in a row that only looked at the project, the framework
# puts the PM in pm_standby; worker messages and waiting workers wake it as before.
PM_MANAGE_MAX_IDLE_TURNS = 3
_MONITORING_ACTIONS = {
    "project_management": frozenset({"list_tasks", "get_dependency_graph", "get_task", "show_task", "view_task", "list"}),
    "manage_team": frozenset({"list_agents", "list_teams", "get_agent_details", "get_team_details"}),
}
_MONITORING_TOOLS = frozenset({"tool_information", "mark_message_read", "knowledge_base", "codebase_search", "system_help"})

PM_IDLE_STANDBY_MESSAGE = (
    "[Framework System Message]: You looked at the task and team lists {turns} times in a row without assigning, messaging or "
    "changing anything, so the open tasks are with your workers. To leave the (shared) model to them, the framework has put "
    "you in 'pm_standby'. You are woken automatically when a worker reports, sends you a message or is waiting for work."
)


def is_monitoring_call(call: Dict[str, Any]) -> bool:
    """True for a tool call that only LOOKS at the project (task/agent lists, file reads, tool help)."""
    name = call.get("name")
    args = _call_arguments(call)
    if name in _MONITORING_TOOLS:
        return True
    if name in _MONITORING_ACTIONS:
        return _clean_action(args) in _MONITORING_ACTIONS[name]
    return is_file_read_call(name, args)


PM_ALL_DONE_AUDIT_MESSAGE = (
    "[Framework System Message]: Your task list shows that EVERY task is finished, so the project work is done. Instead of "
    "listing the tasks again, the framework has moved you to 'pm_audit': verify the result and report the completion to the "
    "Admin AI."
)
PM_LIST_ALL_TASKS_MESSAGE = (
    "[Framework System Message]: You looked at the project {turns} times in a row without acting, and your last task list "
    "was filtered or missing, so it does not show whether work is still open. Call `project_management` with "
    "action=\"list_tasks\" and NO filters now. If every task is finished, request 'pm_audit'; if tasks are still open with "
    "your workers, request 'pm_standby'."
)


def pm_listing_status(history, agent_id) -> str:
    """
    Round 2f: what the PM's most recent task list says - 'none' (no list in the history), 'open' (an open task of another
    agent is listed), 'done' (an UNFILTERED list without open tasks) or 'filtered' (a filtered list without open tasks: not
    proof of anything). Tasks assigned to the PM itself (the decomposed kick-off task) are ignored.
    """
    listing = _last_task_listing(history)
    if listing is None:
        return "none"
    for task in listing["tasks"]:
        if not isinstance(task, dict) or task.get("assignee") == agent_id:
            continue
        progress = str(task.get("task_progress") or task.get("status") or "").strip().lower()
        if progress and progress not in _DONE_PROGRESS:
            return "open"
    return "filtered" if listing.get("_filters") else "done"


def trailing_monitoring_turns(history) -> int:
    """How many of the most recent assistant turns (counted back from the newest) made only monitoring calls, or no call."""
    count = 0
    for msg in reversed(list(history or [])):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        calls = [c for c in (msg.get("tool_calls") or []) if isinstance(c, dict)]
        if all(is_monitoring_call(call) for call in calls):
            count += 1
        else:
            break
    return count


# --- Tools an agent may use in its state (Round 2k) ---------------------------------------------------------------------
# 2026-10-20 10:19-11:03 (qwen3.5:9b): W1 was moved to worker_report and went on calling code_editor / file_system edits
# there for 17 cycles. Only send_message, mark_message_read and project_management are OFFERED in worker_report and
# worker_wait, but a call to another tool was still executed. PM1 (in pm_report_check) edited src/temp_convert.py itself
# 6 times while W1 edited the same lines, so every edit of both was made against line numbers the other had just shifted.
# A call the framework makes on an agent's behalf (e.g. the worker auto-save of code blocks) carries this argument; it is
# removed before the tool runs and the state rules do not apply to it.
FRAMEWORK_INTERNAL_ARG = "_framework_internal"
REPORT_AND_WAIT_TOOLS = frozenset({"send_message", "mark_message_read", "project_management"})
_STATE_TOOLS = {
    ("worker", "worker_report"): REPORT_AND_WAIT_TOOLS,
    ("worker", "worker_wait"): REPORT_AND_WAIT_TOOLS,
}
# file_system actions (and the aliases file_system accepts for them) that change, move or delete files
FILE_CHANGE_ACTIONS = FILE_WRITE_ACTIONS | FORCE_WRITE_ACTIONS | frozenset({
    "write_file", "write_with_lines", "write_with_content", "write_content", "create_file", "create", "new_file",
    "save_file", "save", "add_lines", "add_line", "insert_line", "replace_line", "delete", "delete_file", "remove_file",
    "remove", "move", "move_file", "rename", "rename_file", "copy", "replace_chunks", "code_editor",
})
_CODE_EDITOR_LOOK_ACTIONS = CODE_EDITOR_READ_ACTIONS | FILE_READ_ACTIONS | frozenset({"read_file_content", "get_info"})


def state_tool_allowlist(agent_type: Optional[str], state: Optional[str]) -> Optional[frozenset]:
    """The only tools an agent of this type may call in this state, or None if the state does not restrict tools."""
    return _STATE_TOOLS.get((str(agent_type or ""), str(state or "")))


def is_file_change_call(tool_name: Optional[str], arguments: Any) -> bool:
    """True for a file_system or code_editor call that writes, edits, moves or deletes a file."""
    args = arguments if isinstance(arguments, dict) else _call_arguments({"arguments": arguments})
    action = _clean_action(args)
    if tool_name == "file_system":
        return action in FILE_CHANGE_ACTIONS
    if tool_name == "code_editor":
        return action not in _CODE_EDITOR_LOOK_ACTIONS
    return False


def state_tool_block_message(agent_type: Optional[str], state: Optional[str], tool_name: Optional[str],
                             arguments: Any) -> Optional[str]:
    """
    The reason a tool call must not run for this agent type and state (None if it may run):
    * a worker in worker_report / worker_wait may only report (send_message, mark_message_read, project_management);
    * a Project Manager never changes project files - the worker who owns the task does.
    """
    allowed = state_tool_allowlist(agent_type, state)
    if allowed is not None and tool_name not in allowed:
        return (
            f"'{tool_name}' cannot be used in the '{state}' state, so NOTHING was done. Here you can only use: "
            f"{', '.join(sorted(allowed))}. To keep working on your task, call the 'request_state' tool with "
            "state='worker_work' and your task_id; to report, use send_message to your PM."
        )
    if str(agent_type or "") == "pm" and is_file_change_call(tool_name, arguments):
        args = arguments if isinstance(arguments, dict) else _call_arguments({"arguments": arguments})
        target = next((str(args[k]) for k in _PATH_KEYS if isinstance(args.get(k), str) and args.get(k).strip()), "a file")
        return (
            f"A Project Manager does not change project files, so this '{_clean_action(args) or tool_name}' on '{target}' "
            "was NOT done. The worker who owns the task makes the change: use send_message to tell that worker exactly "
            "what must change (or create/assign a task for it) and let the worker edit the file. A worker WITHOUT an open "
            "task (waiting in worker_wait) does not act on a message: for such a worker create a task for the change "
            "(project_management add_task with a description that names the file and the change, and "
            "assignee_agent_id='<worker id>')."  # Round 3o
        )
    return None


# --- Tool calls written as text (Round 2o) ------------------------------------------------------------------------------------
# 2026-10-21 15:50-16:06 (qwen3.5:9b): 27 of 153 replies were ONLY text like "[Action Taken: called file_system(action='write',
# filename='models/score_manager.py')]" with no real tool call, so nothing ran: W1 "requested" worker_work 13 times and stayed
# in worker_report, and its "writes" saved nothing. Round 2n had added such lines to every assistant message that made calls;
# the model copied them. A reply that only imitates a call is not shown, not reviewed and not kept as the agent's reply.
CALL_IMITATION_PREFIX = "[Action Taken:"
_IMITATED_TOOL_RE = _re.compile(r"\[Action Taken:\s*called\s+([A-Za-z_][\w.-]*)\s*\(")


STATE_CHANGE_RECORD_PREFIX = "[Framework Record]:"


def state_change_record(state: Any, task_id: Any = None) -> str:
    """
    Round 2x: the note the framework keeps in an agent's history when it accepts its request_state call - a SYSTEM note.
    It used to be an assistant message "[Action Taken: Transitioned to state 'worker_report', task_id=..., ...]" written
    as if the agent had said it, and the model copied it: 2026-10-22 12:18-12:19 W2's replies were four such texts (nothing
    ran) and PM1 wrote "[Action Taken: Transitioned to state 'pm_manage']" (Round 2o had removed only the "called ..." form).
    """
    task = f" for task {task_id}" if task_id else ""
    return f"{STATE_CHANGE_RECORD_PREFIX} request_state received - transitioning to state '{state}'{task}."


def is_call_imitation_text(text: Any) -> bool:
    """
    True if a reply (without real tool calls) starts with the framework's '[Action Taken: ...]' note (Round 2x: or with
    its '[Framework Record]:' state change note).
    """
    return isinstance(text, str) and text.strip().startswith((CALL_IMITATION_PREFIX, STATE_CHANGE_RECORD_PREFIX))


def call_imitation_notice(text: Any) -> str:
    """What the framework tells an agent whose reply only imitated a tool call or a state change in text."""
    shown = str(text or "").strip()
    if len(shown) > 160:
        shown = shown[:160].rstrip() + "..."
    match = _IMITATED_TOOL_RE.search(str(text or ""))
    tool = match.group(1) if match else None
    if tool == "request_state" or (not tool and ("Transitioned" in str(text or "") or "transitioning to state" in str(text or ""))):
        how = "To change your state, call the 'request_state' tool with the state (and your task_id)."
    elif tool:
        how = (f"To use '{tool}', make a real call of the '{tool}' tool with ALL its parameters (for a file write: "
               "'filename' and the complete 'content').")
    else:
        how = "To use a tool, make a real tool call with all its parameters."
    return (
        f"[Framework Notice]: Your last reply was only the text \"{shown}\". Writing that text does NOT run anything, so "
        f"NOTHING was executed or saved. Lines starting with '[Action Taken:' or '[Framework Record]:' are notes the "
        f"framework writes, not a way to call tools. {how}"
    )

# END OF FILE src/agents/tool_activity.py
