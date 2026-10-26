# START OF FILE src/agents/tool_guidance.py
"""
One source for what an agent is told about its tools (Round 3v).

Until Round 3u the same rules were written in several places that had drifted apart, so a model was told different
things in the same prompt:
* WHICH tools a state has: core.py offered its own lists (worker_decompose, pm_build_team_tasks), the executor enforced
  another (tool_activity.state_tool_allowlist), and the prompts said a third ("in worker_decompose you are restricted to
  ONLY project_management" while file_system was offered; "worker_report is the ONLY state where you can send messages"
  while send_message was offered in every worker state; worker_wait "do NOT execute any tools" while three were allowed).
* HOW files change: the standard [TOOL USE] text said "ALWAYS use code_editor for modifying existing code" - also to the
  PM, who may never change project files - the worker prompt "You MUST use code_editor for ALL modifications" and "to
  rewrite a file set force_overwrite", while the file_system help recommends replace_lines / find_replace and replaces
  the agent's OWN file without force_overwrite, and a rejected new file is fixed with find_replace / replace_lines.
* HOW to call a tool: Guardian and framework notices gave raw JSON such as `{"action": "list", "path": "."}` (no tool name,
  as text), XML tags (`<send_message>`) and state names of other agent types ('admin_conversation' to a worker).

This module holds those rules once:
* offered_tool_names(): the tools a state OFFERS (the native schemas the model sees) - core.py and the prompt line
  "Tools you can call in this state" use it, and it never offers a tool the executor refuses for the whole state.
* tool_use_instructions(): the [TOOL USE] block of the system prompt, per agent type and state.
* FILE_CHANGE_RULE / PM_FILE_RULE / ADMIN_FILE_RULE: how files are created and changed, as the tools' own help says.
* call_text() / request_state_text(): how a notice names a tool call (native tool calling, never JSON or XML text).
* summary_state(): the state an agent of a type requests after summarizing (never another type's state).
"""
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

ADMIN, PM, WORKER = "admin", "pm", "worker"
REQUEST_STATE = "request_state"

# Tools a state OFFERS (when it offers fewer than the agent type has). The executor enforces only
# tool_activity.state_tool_allowlist (worker_report / worker_wait); a state listed here offers only these tools.
OFFERED_STATE_TOOLS: Dict[Tuple[str, str], frozenset] = {
    (WORKER, "worker_decompose"): frozenset({"project_management", "file_system", "codebase_search", "mark_message_read",
                                             "send_message"}),
    (PM, "pm_startup"): frozenset(),  # the reply is the JSON kick-off plan
    (PM, "pm_build_team_tasks"): frozenset({"manage_team", "tool_information", "mark_message_read", "send_message"}),
}
# Round 3v: a PM never changes project files (the executor refuses it): code_editor, which only edits, is not offered to it
NEVER_OFFERED: Dict[str, frozenset] = {PM: frozenset({"code_editor"})}
# request_state is not offered where the reply must be something else
NO_REQUEST_STATE: frozenset = frozenset({(PM, "pm_startup")})


def authorized(agent_type: Optional[str], auth_level: Optional[str]) -> bool:
    """The tool authorization of core.py: the Admin AI may use every tool, a PM 'pm' and 'worker' tools, a worker 'worker' tools."""
    level = auth_level or "worker"
    if agent_type == ADMIN:
        return True
    if agent_type == PM:
        return level in ("pm", "worker")
    if agent_type == WORKER:
        return level == "worker"
    return False


def offered_tool_names(agent_type: Optional[str], state: Optional[str], tools: Mapping[str, Any],
                       skip: Iterable[str] = ()) -> List[str]:
    """
    The names of the tools an agent of this type is OFFERED in this state, in the order of `tools` (name -> tool object
    with an 'auth_level'). `skip` holds tools left out for another reason (resolve_concern without a pending concern).
    request_state is not included (see offers_request_state()).
    """
    from src.agents.tool_activity import state_tool_allowlist
    agent_type, state = str(agent_type or ""), str(state or "")
    only = OFFERED_STATE_TOOLS.get((agent_type, state))
    enforced = state_tool_allowlist(agent_type, state)
    never = NEVER_OFFERED.get(agent_type, frozenset())
    skipped = set(skip or ())
    names = []
    for name, tool in tools.items():
        if name in skipped or name in never:
            continue
        if only is not None and name not in only:
            continue
        if enforced is not None and name not in enforced:
            continue
        if not authorized(agent_type, getattr(tool, "auth_level", "worker")):
            continue
        names.append(name)
    return names


def offers_request_state(agent_type: Optional[str], state: Optional[str]) -> bool:
    return (str(agent_type or ""), str(state or "")) not in NO_REQUEST_STATE


# --- How files are created and changed (the file_system and code_editor help say the same) ---------------------------------
FILE_CHANGE_RULE = (
    "- **Files:** create a NEW file with `file_system` action 'write'. Change an EXISTING file with small edits: "
    "`code_editor` action 'replace_chunks' (chunks with 'start_line' and 'end_line' from a fresh 'read', or an exact "
    "'search' text, plus 'replace'), or `file_system` action 'replace_lines', 'find_replace' or 'insert_lines'. To replace "
    "a whole file you wrote yourself, 'write' it again; another file needs 'force_overwrite': true. If the 'write' of a NEW "
    "file is rejected for a syntax error, the file does NOT exist but your text is kept: fix the faulty line with "
    "'find_replace' or 'replace_lines' on that filename, or add the rest with 'append' - never resend the whole file.")
PM_FILE_RULE = (
    "- **Files:** you READ project files (`file_system` action 'read' or 'list') but never change them - the framework "
    "refuses it. A change is made by the worker whose task names the file: tell it with `send_message`, or create and "
    "assign a task for it with `project_management`.")
ADMIN_FILE_RULE = (
    "- **Files:** you do not build projects or change the files of a project a PM builds (the framework refuses it): "
    "projects are planned in 'planning' and built by a PM and its workers.")
CALL_RULE = (
    "- **How to call:** use the native tool calls you were given - never write a tool call as text, JSON or XML in your "
    "reply. Each tool's description and parameters are its rules.")


def tool_use_instructions(agent_type: Optional[str], state: Optional[str], offered: Optional[Iterable[str]],
                          native: bool = True) -> str:
    """
    The [TOOL USE] block of the system prompt for this agent type and state. `offered` are the offered tool names (None
    when unknown: the list line is left out). Only native tool calling is described when `native` (the default setting).
    """
    agent_type, state = str(agent_type or ""), str(state or "")
    names = list(offered) if offered is not None else None
    if not names and not offers_request_state(agent_type, state):
        return ("[TOOL USE]\nNo tools are offered in this state: your reply is the JSON block the workflow below asks for "
                "(after your <think> block), nothing else.")
    lines = ["[TOOL USE]"]
    if native:
        lines.append(CALL_RULE)
    if names is not None:
        shown = sorted(names) + ([REQUEST_STATE] if offers_request_state(agent_type, state) else [])
        # the state is named, so the agent knows it (and a log reader sees which state a prompt is for)
        lines.append(f"- **Tools you can call in this state ('{state}'):** {', '.join(shown)}. Other tools are not available here"
                     + (" - request the state that has them." if offers_request_state(agent_type, state) else "."))
        if "tool_information" in names:
            lines.append("- A tool's full help: `tool_information` action 'get_info' with its 'tool_name'.")
    if agent_type == WORKER and (names is None or {"file_system", "code_editor"} & set(names)):
        if names is not None and "code_editor" not in names:
            lines.append("- **Files:** read files here (`file_system` action 'read' or 'list'); you create and change "
                         "files in 'worker_work'.")
        else:
            lines.append(FILE_CHANGE_RULE)
    elif agent_type == PM and (names is None or "file_system" in names):
        lines.append(PM_FILE_RULE)
    elif agent_type == ADMIN:
        lines.append(ADMIN_FILE_RULE)
    return "\n".join(lines)


# --- How a notice names a call ---------------------------------------------------------------------------------------------
def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value) + "'"


def call_text(tool: str, **arguments: Any) -> str:
    """"the `project_management` tool with action='list_tasks', task_id='...'" - a native call, named in words."""
    if not arguments:
        return f"the `{tool}` tool"
    return f"the `{tool}` tool with " + ", ".join(f"{key}={_value(value)}" for key, value in arguments.items())


def request_state_text(state: str, task_id: Optional[str] = None) -> str:
    """"the 'request_state' tool with state='worker_report'" (and the task_id)."""
    return f"the 'request_state' tool with state='{state}'" + (f" and task_id='{task_id}'" if task_id else "")


# the state an agent requests after it has summarized its work (never a state of another agent type)
_SUMMARY_STATE = {ADMIN: "admin_conversation", PM: "pm_manage", WORKER: "worker_report"}


def summary_state(agent_type: Optional[str]) -> str:
    return _SUMMARY_STATE.get(str(agent_type or ""), "worker_report")


def look_around_call(agent_type: Optional[str]) -> str:
    """A simple, always allowed first step for an agent that is stuck (a call its type is offered in its working states)."""
    if str(agent_type or "") in (PM, ADMIN):
        return call_text("project_management", action="list_tasks")
    return call_text("project_management", action="list_tasks") + " (your tasks), or " + call_text(
        "file_system", action="list", path=".")


def native_tool_calling() -> bool:
    """True when agents call tools natively (settings.NATIVE_TOOL_CALLING_ENABLED, the default)."""
    try:
        from src.config.settings import settings
        return bool(getattr(settings, "NATIVE_TOOL_CALLING_ENABLED", True))
    except Exception:  # a notice must never fail
        return True


LIST_TOOLS_CALL = call_text("tool_information", action="list_tools")


def offered_in(manager: Any, agent_type: Optional[str], state: Optional[str]) -> Optional[List[str]]:
    """offered_tool_names() for the manager's tool executor, or None when it has none."""
    tools = getattr(getattr(manager, "tool_executor", None), "tools", None)
    if not isinstance(tools, dict):
        return None
    return offered_tool_names(agent_type, state, tools, skip=["resolve_concern"])


def tools_line(agent_type: Optional[str], state: Optional[str], offered: Optional[Iterable[str]]) -> str:
    """"The tools you can call in state 'x': a, b, request_state." (empty when unknown)."""
    if offered is None:
        return ""
    names = sorted(offered) + ([REQUEST_STATE] if offers_request_state(agent_type, state) else [])
    if not names:
        return f"No tools are offered in state '{state}'."
    return f"The tools you can call in state '{state}': {', '.join(names)}."


# --- Guardian resets (Round 3v) ---------------------------------------------------------------------------------------------
# After these Guardian interventions the cycle handler puts the agent into a fixed state of its own type. The guidance the
# Guardian injects names that state and what to do there - it used to say "use file_system list" to a worker that was
# then put into worker_wait (no file tools), or "[Framework Override]: returned to the 'wait' state" with no next step.
GUARDIAN_RESET_TYPES = frozenset({"minimal_response_pattern", "stuck_in_state", "tool_information_loop_violation",
                                  "empty_response_violation"})
_GUARDIAN_RESET_STATE = {ADMIN: "admin_standby", PM: "pm_manage", WORKER: "worker_wait"}


def guardian_reset_state(agent_type: Optional[str], recovery_type: Optional[str]) -> Optional[str]:
    """The state an agent is put into after a Guardian intervention of this type, or None (no reset)."""
    if recovery_type not in GUARDIAN_RESET_TYPES:
        return None
    return _GUARDIAN_RESET_STATE.get(str(agent_type or ""))


def state_change_note(agent_type: Optional[str], new_state: str, task_id: Optional[str] = None) -> str:
    """
    "[Framework Override]: your state is now 'x'. <what to do there>" - for a state the framework (not the agent) chose.
    Only tools and states the agent really has in that state are named.
    """
    agent_type = str(agent_type or "")
    note = f"[Framework Override]: Your state is now '{new_state}'. "
    if agent_type == WORKER and new_state == "worker_wait":
        note += ("First look at your tasks with " + call_text("project_management", action="list_tasks")
                 + ". If a task assigned to you is still open, take it up again: call "
                 + request_state_text("worker_decompose") + (f" (your task was {task_id})" if task_id else "")
                 + ". If none is open, wait - your PM gives you the next task.")
    elif agent_type == WORKER and new_state == "worker_report":
        note += ("Tell your PM with `send_message` what you have done and what blocks you, then call "
                 + request_state_text("worker_work", task_id or "YOUR_TASK_ID")
                 + " to continue, or " + request_state_text("worker_wait") + " when your task is finished.")
    elif agent_type == PM and new_state == "pm_manage":
        note += ("Start with Step 1 of its workflow: " + call_text("project_management", action="list_tasks")
                 + ", then take ONE management action.")
    elif agent_type == PM and new_state == "pm_standby":
        note += "You stay there until a worker reports or the Admin AI writes to you."
    elif agent_type == ADMIN and new_state == "admin_standby":
        note += "Wait for the next message from the user or a Project Manager."
    elif agent_type == ADMIN and new_state == "admin_conversation":
        note += "Tell the user in a short text reply where things stand, or ask them what they want next."
    else:
        note += "Follow the workflow of that state in your system prompt."
    return note

# END OF FILE src/agents/tool_guidance.py
