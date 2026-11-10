# START OF FILE src/agents/tool_surface.py
"""
Round 5a: the tool surface - what of the tools a model is SHOWN in a call.

Measured on the code of Round 4o (no run needed: the schemas are built from the tool classes): every call of a worker in
'worker_work' carried 14 tool schemas of 22,800 characters, every call of a Project Manager 15 schemas of 24,700 and
every call of the Admin AI 17 schemas of 26,500 - about 6,000 to 7,500 tokens before the system prompt, the task and the
history, in each of the hundreds of calls of a run. For a 4B model with a 24k-32k window that is a quarter of the window,
and the history is summarized that much earlier. Most of it described things the caller cannot or need not use:

* `file_system` alone was 5,700 characters: 26 actions and 28 parameters, among them ten git actions, three more ways to
  replace text and the scope of other workspaces - also for a Project Manager, which may only read;
* a worker writing a JavaScript module was offered `asset_search`, `github_tool` and `zim_search` (4,700 characters) in
  every call, whether or not a token or a .zim library is configured;
* `project_management` showed a worker the PM's filters, tags and assignment parameters.

More choices are not only more tokens: a small model picks worse from 14 tools with 90 parameters than from 11 with 45.

This module decides two things, both from tables in this file:

* schema_for(): the VIEW of a tool for an agent type and state - the actions and parameters that caller uses there, with
  a description written for that view. A view only LEAVES OUT: every parameter it shows is a real parameter of the tool
  (a test checks it), and the tools accept everything they accepted before - a hidden action or parameter still works and
  `tool_information` still documents it.
* hidden_tools(): tools that are not offered because they cannot work here (`github_tool` without a token, `zim_search`
  without a library) or have nothing to do with the agent's role and task (`asset_search` for a task that names no
  image, sound or other asset). A hidden tool is listed by `tool_information` action 'list_tools' as available on
  request; asking for its help (`get_info`) offers it to that agent from its next step on.

The offered tools of an agent change only when its task or state changes, so the start of the prompt stays the same from
call to call (a local server re-uses its prompt cache). TOOL_SURFACE=full switches all of this off (the tools as they
were until Round 4o).
"""
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

ADMIN, PM, WORKER = "admin", "pm", "worker"


def focused() -> bool:
    """True unless TOOL_SURFACE=full."""
    try:
        from src.config.settings import settings
        return str(getattr(settings, "TOOL_SURFACE", "focused")).strip().lower() != "full"
    except Exception:
        return True


# --------------------------------------------------------------------------------------------------------------------------
# views: the actions and parameters of a tool that a caller is shown
# --------------------------------------------------------------------------------------------------------------------------
class View:
    """description: the tool description of this view; params: {parameter name: description, or None to keep the tool's
    own}; required: the required parameters shown (default: the tool's own that are in params)."""

    def __init__(self, description: Optional[str], params: Mapping[str, Optional[str]],
                 required: Optional[Iterable[str]] = None):
        self.description = description
        self.params = dict(params)
        self.required = list(required) if required is not None else None


_VALID_FILES = ("Python, JavaScript, JSON and shell files must stay valid: a write or change that breaks one is rejected "
                "and nothing is saved.")

FILE_EDIT_VIEW = View(
    "The files of your workspace (paths are relative to its root). Actions: 'read' (lines come numbered; with 'function' "
    "only that function, class or method), 'list' (a folder), 'write' (a NEW file - or a file you wrote yourself again - as "
    "its COMPLETE text), 'append' (add to the end), 'replace_lines' (start_line, end_line, content), 'insert_lines' "
    "(insert_line, content), 'find_replace' (find_text, replace_text), 'mkdir', 'delete', 'move'. To change one function "
    "of a code file use the code_editor tool (action 'replace_function'). " + _VALID_FILES + " When the 'write' of a NEW "
    "file is rejected, its text is kept: fix the faulty line with 'find_replace' or 'replace_lines' on that file, or add "
    "the rest with 'append', instead of sending the whole file again. More actions (tree, copy, git_...): "
    "tool_information.",
    {
        "action": "One of: 'read', 'list', 'write', 'append', 'replace_lines', 'insert_lines', 'find_replace', 'mkdir', "
                  "'delete', 'move'.",
        "filename": "Path of the file (for 'read', 'write', 'append', 'replace_lines', 'insert_lines', 'find_replace').",
        "content": "The text: the COMPLETE file for 'write'; the new lines for 'append', 'insert_lines' and 'replace_lines'.",
        "path": "Path of a folder or file for 'list' (default '.'), 'mkdir', 'delete' and 'move' (the source).",
        "function": None,
        "start_line": "First line (1-based): optional for 'read', required for 'replace_lines'.",
        "end_line": "Last line (inclusive): optional for 'read', required for 'replace_lines'.",
        "insert_line": "For 'insert_lines': the line number the content is inserted at.",
        "find_text": None,
        "replace_text": "For 'find_replace': the text that replaces find_text.",
        "destination_path": "For 'move': the new path.",
        "force_overwrite": "For 'write': true to replace a file that ANOTHER agent wrote. Not needed for your own files.",
    })

FILE_READ_VIEW = View(
    "Looks at the files of the project workspace (paths are relative to its root). Actions: 'read' (lines come numbered; "
    "with 'function' only that function, class or method), 'list' (a folder), 'tree' (all folders).",
    {
        "action": "One of: 'read', 'list', 'tree'.",
        "filename": "Path of the file for 'read'.",
        "path": "Path of the folder for 'list' and 'tree' (default '.').",
        "function": None,
        "start_line": "Optional for 'read': first line (1-based).",
        "end_line": "Optional for 'read': last line (inclusive).",
    })

CODE_EDITOR_VIEW = View(
    None,  # the tool's own description (Round 5a) names both actions
    {"action": None, "filename": None, "function": None, "code": None, "chunks": None},
    required=["action", "filename"])

TASKS_WORKER_VIEW = View(
    "Your tasks. Actions: 'list_tasks' (your open tasks with their ids), 'modify_task' (task_id and task_progress: set "
    "'doing' when you start a task, 'finished' when its work is saved and checked), 'add_task' (description: a sub-task of "
    "YOUR task; it is assigned to you).",
    {
        "action": "One of: 'list_tasks', 'modify_task', 'add_task'.",
        "task_id": "For 'modify_task': the task's UUID exactly as listed.",
        "task_progress": "For 'modify_task': 'doing', 'finished' or 'stuck'.",
        "description": "For 'add_task': what the sub-task must do, naming its file(s).",
        "depends": "Optional for 'add_task': UUID of a task that must be finished first (several separated by commas).",
    })

TASKS_PM_VIEW = View(
    None,
    {name: None for name in ("action", "description", "task_id", "task_progress", "priority", "assignee_agent_id",
                             "depends", "project_filter", "assignee_filter", "task_progress_filter")})

KNOWLEDGE_VIEW = View(
    "The project's shared knowledge base. Actions: 'save_knowledge' (summary and keywords: a design decision, an API, "
    "something your team must know), 'search_knowledge' (query_keywords).",
    {
        "action": "'save_knowledge' or 'search_knowledge'.",
        "summary": None,
        "keywords": None,
        "query_keywords": None,
        "max_results": "Optional for 'search_knowledge': how many results (default 5).",
    })

TEAM_PM_VIEW = View(
    None,
    {name: None for name in ("action", "target_agent_id", "team_id", "system_prompt", "role", "persona", "new_state")})

WEB_VIEW = View(None, {name: None for name in ("action", "query", "url", "num_results")})
SEARCH_VIEW = View(None, {name: None for name in ("query", "path", "include_pattern")})

# a worker creates and changes files in 'worker_work' / 'worker_test'; while it decides about sub-tasks it only looks
FILE_READ_ONLY_STATES = frozenset({(WORKER, "worker_decompose")})


def view_for(tool_name: str, agent_type: Optional[str], state: Optional[str]) -> Optional[View]:
    """The view of a tool for this caller (None: the tool as it is, without parameters every caller can leave out)."""
    agent_type, state = str(agent_type or ""), str(state or "")
    if tool_name == "file_system":
        if agent_type == PM or (agent_type == ADMIN and state == "planning"):
            return FILE_READ_VIEW
        return FILE_READ_VIEW if (agent_type, state) in FILE_READ_ONLY_STATES else FILE_EDIT_VIEW
    if tool_name == "code_editor":
        return CODE_EDITOR_VIEW
    if tool_name == "project_management":
        return TASKS_WORKER_VIEW if agent_type == WORKER else (TASKS_PM_VIEW if agent_type == PM else None)
    if tool_name == "knowledge_base":
        return KNOWLEDGE_VIEW
    if tool_name == "manage_team" and agent_type == PM:
        return TEAM_PM_VIEW
    if tool_name == "web_search":
        return WEB_VIEW
    if tool_name == "codebase_search":
        return SEARCH_VIEW
    return None


# parameters no view shows to a worker or a PM: they work in the project's shared workspace, which is the default
_LEFT_OUT_EVERYWHERE = frozenset({"scope"})


def schema_for(tool: Any, agent_type: Optional[str], state: Optional[str]) -> Dict[str, Any]:
    """The JSON schema of a tool as this caller is shown it (the tool's full schema with TOOL_SURFACE=full)."""
    full = tool.get_json_schema()
    if not focused():
        return full
    try:
        function = full["function"]
        properties: Dict[str, Any] = function["parameters"]["properties"]
        view = view_for(getattr(tool, "name", ""), agent_type, state)
        if view is None:
            shown = [name for name in properties if name not in _LEFT_OUT_EVERYWHERE or str(agent_type or "") == ADMIN]
            if len(shown) == len(properties):
                return full
            texts: Dict[str, Optional[str]] = {}
            description, required = function.get("description"), None
        else:
            shown = [name for name in view.params if name in properties]
            if str(agent_type or "") == ADMIN and "scope" in properties and "scope" not in shown:
                shown.append("scope")  # the Admin AI also works outside a project (its sandbox, the projects folder)
            texts = view.params
            description, required = view.description or function.get("description"), view.required
        new_properties = {}
        for name in shown:
            entry = dict(properties[name])
            if texts.get(name):
                entry["description"] = texts[name]
            new_properties[name] = entry
        if required is None:
            required = [name for name in function["parameters"].get("required", []) if name in new_properties]
        parameters = dict(function["parameters"])
        parameters["properties"] = new_properties
        parameters["required"] = [name for name in required if name in new_properties]
        return {"type": full.get("type", "function"),
                "function": {**function, "description": description, "parameters": parameters}}
    except Exception as err:  # a view must never cost a call its tools
        logger.warning(f"tool_surface: view of '{getattr(tool, 'name', '?')}' failed ({err}); the full schema is used.")
        return full


# --------------------------------------------------------------------------------------------------------------------------
# tools that are offered only when they can work and have to do with the agent's work
# --------------------------------------------------------------------------------------------------------------------------
_ASSET_RE = re.compile(
    r"\b(assets?|images?|pictures?|photos?|sprites?|icons?|textures?|sounds?|audio|music|sfx|artwork|graphics?|"
    r"illustrations?|logos?|fonts?|3d|models?|wallpapers?|backgrounds?|videos?|animations?|gifs?|png|jpe?g|svg|mp3|wav|ogg)\b",
    re.IGNORECASE)
_GITHUB_RE = re.compile(r"\b(github|repositor(?:y|ies)|repos?|pull requests?|open[- ]source|git clone|upstream)\b",
                        re.IGNORECASE)
_RESEARCH_RE = re.compile(
    r"\b(research\w*|investigat\w*|analys\w*|analyz\w*|writer?s?|author\w*|journalis\w*|fact\w*|histor\w*|wikipedia|"
    r"encyclop\w*|librar(?:y|ies)|knowledge|stud(?:y|ies)|report\w*|article\w*|essays?|summar\w*|literature|sources?|"
    r"look\s+up|offline)\b", re.IGNORECASE)

# never offered to a Project Manager unless it asks: a PM plans, assigns and checks - research and assets are workers' work
_PM_ON_REQUEST = frozenset({"asset_search", "github_tool", "zim_search", "web_search"})


def _configured(tool_name: str) -> bool:
    """False for a tool that cannot work on this installation."""
    try:
        if tool_name == "github_tool":
            from src.config.settings import settings
            return bool(getattr(settings, "GITHUB_ACCESS_TOKEN", None) or os.environ.get("GITHUB_ACCESS_TOKEN"))
        if tool_name == "zim_search":
            return bool(os.environ.get("ZIM_FILES_PATH", "").strip())
    except Exception:
        return True
    return True


def work_text(agent: Any) -> str:
    """What the agent is and what it works on: role, persona, its own system prompt and its current task."""
    parts = []
    for attr in ("role", "persona", "_config_system_prompt", "current_task_description", "_injected_task_description"):
        value = getattr(agent, attr, None)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return "\n".join(parts)


def _topic_fits(tool_name: str, text: str) -> bool:
    if tool_name == "asset_search":
        return bool(_ASSET_RE.search(text))
    if tool_name == "github_tool":
        return bool(_GITHUB_RE.search(text))
    if tool_name == "zim_search":
        return bool(_RESEARCH_RE.search(text))
    return True


ON_REQUEST_TOOLS: Tuple[str, ...] = ("asset_search", "github_tool", "zim_search", "web_search")


def unlocked(agent: Any) -> set:
    value = getattr(agent, "_unlocked_tools", None)
    return set(value) if isinstance(value, (set, frozenset, list, tuple)) else set()


def hidden_tools(agent: Any, tool_names: Optional[Iterable[str]] = None) -> List[str]:
    """
    The tools this agent is not offered although its type and state allow them: not configured, or nothing to do with
    its role and task. Tools the agent asked for (unlock()) are never hidden. Empty with TOOL_SURFACE=full.
    """
    if agent is None or not focused():
        return []
    try:
        agent_type = str(getattr(agent, "agent_type", "") or "")
        asked = unlocked(agent)
        known = set(tool_names) if tool_names is not None else None
        text: Optional[str] = None
        hidden = []
        for name in ON_REQUEST_TOOLS:
            if (known is not None and name not in known) or name in asked:
                continue
            if not _configured(name):
                hidden.append(name)
            elif agent_type == PM:
                if name in _PM_ON_REQUEST:
                    hidden.append(name)
            elif agent_type == WORKER and name != "web_search":
                if text is None:
                    text = work_text(agent)
                if not _topic_fits(name, text):
                    hidden.append(name)
        return hidden
    except Exception as err:
        logger.debug(f"tool_surface: hidden_tools failed: {err}")
        return []


def requestable_tools(agent: Any, tool_names: Optional[Iterable[str]] = None) -> List[str]:
    """The hidden tools the agent can ask for (configured, only not offered by default)."""
    return [name for name in hidden_tools(agent, tool_names) if _configured(name)]


def unlock(agent: Any, tool_name: str) -> bool:
    """Offer a hidden tool to this agent from now on. False if the tool cannot work here (not configured)."""
    if agent is None or not _configured(tool_name):
        return False
    try:
        current = unlocked(agent)
        current.add(tool_name)
        agent._unlocked_tools = current
        return True
    except Exception:
        return False


def not_configured_note(tool_name: str) -> str:
    if tool_name == "github_tool":
        return "`github_tool` cannot be used on this installation: no GITHUB_ACCESS_TOKEN is set."
    if tool_name == "zim_search":
        return "`zim_search` cannot be used on this installation: no offline library (ZIM_FILES_PATH) is set."
    return f"`{tool_name}` cannot be used on this installation."


def skipped_tools(manager: Any, agent: Any) -> List[str]:
    """Everything offered_tool_names() must skip for this agent now: `resolve_concern` without a concern that waits for its
    decision (Round 2o), and the hidden tools above."""
    skip: List[str] = []
    tools = getattr(getattr(manager, "tool_executor", None), "tools", None)
    names = list(tools.keys()) if isinstance(tools, dict) else None
    try:
        escalation = getattr(manager, "cg_escalation", None)
        if names is None or "resolve_concern" in names:
            if escalation is None or not escalation.pending_for_decider(getattr(agent, "agent_id", None)):
                skip.append("resolve_concern")
    except Exception:
        skip.append("resolve_concern")
    skip.extend(hidden_tools(agent, names))
    return skip

# END OF FILE src/agents/tool_surface.py
