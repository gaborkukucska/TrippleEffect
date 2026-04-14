# START OF FILE src/tools/project_management.py
import logging
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING # Import TYPE_CHECKING
import re
import json
import asyncio
from src.api.websocket_manager import broadcast

# Import tasklib safely
try:
    from tasklib import TaskWarrior, Task
    TASKLIB_AVAILABLE = True
except ImportError:
    from typing import Any
    TaskWarrior: Any = None
    Task: Any = None
    TASKLIB_AVAILABLE = False


from .base import BaseTool, ToolParameter
from src.config.settings import BASE_DIR
from typing import List

logger = logging.getLogger(__name__)

class ProjectManagementTool(BaseTool):
    name = "project_management"
    auth_level: str = "worker"
    summary: Optional[str] = "Manages project tasks (add_task, get_tasks, modify_task, get_project_state) via Taskwarrior. Use tool_information with sub_action for per-action help."
    description = "Manages project tasks using Tasklib."
    parameters: List[ToolParameter] = [
        ToolParameter(name="action", type="str", required=True, description="The action to perform."),
        ToolParameter(name="title", type="str", required=False, description="Short task title (used as description if description is omitted)."),
        ToolParameter(name="description", type="str", required=False, description="Task description."),
        ToolParameter(name="task_id", type="str", required=False, description="Task attribute. For add_task: assign a custom alias (e.g., 'task_1'). For other actions: use UUID, ID, or your custom alias."),
        ToolParameter(name="task_progress", type="str", required=False, description="Granular task progress state (e.g., todo, in_progress, waiting, stuck, finished)."),
        ToolParameter(name="priority", type="str", required=False, description="Task priority."),
        ToolParameter(name="project_filter", type="str", required=False, description="Filter tasks by project."),
        ToolParameter(name="assignee_filter", type="str", required=False, description="Filter tasks by assignee."),
        ToolParameter(name="task_progress_filter", type="str", required=False, description="Filter tasks by granuler progress state."),
        ToolParameter(name="tags", type="list", required=False, description="List of tags."),
        ToolParameter(name="depends", type="str", required=False, description="Dependency task UUID."),
        ToolParameter(name="assignee_agent_id", type="str", required=False, description="Agent ID for assignment."),
    ]

    def __init__(self, project_name: Optional[str] = None, session_name: Optional[str] = None):
        if not TASKLIB_AVAILABLE:
            logger.error("Tasklib library is not installed. ProjectManagementTool will not function.")

    def _get_taskwarrior_instance(self, project_name: str, session_name: str) -> Any:
        if not TASKLIB_AVAILABLE:
            return None
        data_path = BASE_DIR / "projects" / project_name / session_name / "task_data"
        try:
            data_path.mkdir(parents=True, exist_ok=True)
            taskrc_path = data_path / '.taskrc'
            if not taskrc_path.exists():
                with open(taskrc_path, 'w') as f:
                    f.write("uda.assignee.type=string\nuda.assignee.label=Assignee\n")
                    f.write("uda.task_progress.type=string\nuda.task_progress.label=Task Progress\n")
            else:
                # Patch existing taskrc
                with open(taskrc_path, 'r') as f:
                    content = f.read()
                if "uda.task_progress.type" not in content:
                    with open(taskrc_path, 'a') as f:
                        f.write("\nuda.task_progress.type=string\nuda.task_progress.label=Task Progress\n")
            return TaskWarrior(data_location=str(data_path), taskrc_location=str(taskrc_path))
        except Exception as e:
             logger.error(f"Failed to initialize TaskWarrior at {data_path}: {e}", exc_info=True)
             return None

    def _load_aliases(self, project_name: str, session_name: str) -> Dict[str, str]:
        import json
        alias_path = BASE_DIR / "projects" / project_name / session_name / "task_data" / "task_aliases.json"
        if alias_path.exists():
            try:
                with open(alias_path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_aliases(self, project_name: str, session_name: str, aliases: Dict[str, str]):
        import json
        alias_path = BASE_DIR / "projects" / project_name / session_name / "task_data" / "task_aliases.json"
        try:
            with open(alias_path, "w") as f:
                json.dump(aliases, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save aliases: {e}")

    def _map_task_progress(self, raw_progress: Optional[str]) -> tuple[str, str]:
        """
        Maps a varied text description to a standard 'task_progress' UDA value
        and calculates the underlying strict TaskWarrior 'status'.
        Returns: (standard_progress, tw_status)
        """
        if not raw_progress:
            return "todo", "pending"
            
        progress_norm = str(raw_progress).strip().lower()
        
        # Standard progress mapping rules to give LLMs flexibility
        if progress_norm in ["todo", "not_started", "open", "new", "pending"]:
            return "todo", "pending"
        elif progress_norm in ["doing"]:
            return "doing", "pending"
        elif progress_norm in ["in_progress", "active", "working", "started", "progressing"]:
            return "in_progress", "pending"
        elif progress_norm in ["waiting", "blocked", "paused", "on_hold"]:
            return "waiting", "waiting"
        elif progress_norm in ["stuck", "failed", "error", "issue"]:
            return "stuck", "pending"  # Still pending structurally, just stuck temporally
        elif progress_norm in ["decomposed", "broken_down", "split"]:
            return "decomposed", "pending"
        elif progress_norm in ["finished", "done", "completed", "complete", "closed"]:
            return "finished", "completed"
        elif progress_norm in ["deleted", "cancelled", "canceled", "dropped"]:
            return "deleted", "deleted"
            
        # Fallback if unknown but provided
        return progress_norm, "pending"

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        if not TASKLIB_AVAILABLE:
            return {"status": "error", "message": "Tasklib library not installed."}

        action = kwargs.get("action")
        
        if action in ["send_status_update", "status_update", "message", "send_message"]:
            return {
                "status": "error",
                "message": "Invalid action. The 'project_management' tool is ONLY for modifying the task database. If you are trying to talk to the Project Manager or report progress, please use the separate 'send_message' tool."
            }
            
        if action in ["switch_state", "change_state", "request_state"]:
            return {
                "status": "error",
                "message": "ERROR: You cannot change your state using the project_management tool. You must use the standalone XML tag <request_state state='...'/> instead."
            }

        # Check for common mistakes and provide helpful suggestions
        action_suggestions = {
            "assign_task": "modify_task",
            "create_task": "add_task",
            "decompose_task": "add_task",
            "new_task": "add_task", 
            "add": "add_task",
            "create": "add_task",
            "list": "list_tasks",
            "show": "list_tasks",
            "show_tasks": "list_tasks",
            "get_tasks": "list_tasks",
            "list_subtasks": "list_tasks",
            "update_project_status": "modify_task",
            "update_task_status": "modify_task",
            "update_status": "modify_task",
            "update_task": "modify_task",
            "edit_task": "modify_task",
            "change_task": "modify_task",
            "update": "modify_task",
            "edit": "modify_task",
            "update_progress": "modify_task",
            "set_progress": "modify_task",
            "progress_update": "modify_task",
            "finish_task": "complete_task",
            "done": "complete_task",
            "finish": "complete_task",
            "mark_complete": "complete_task",
            "mark_completed": "complete_task",
            "complete": "complete_task",
            "task_complete": "complete_task",
            "get_task": "list_tasks",
            "get": "list_tasks",
            "task_list": "list_tasks",
            "list_sub_tasks": "list_tasks",
            "list_subtask": "list_tasks",
            "view_tasks": "list_tasks",
            "start_task": "modify_task",
            "begin_task": "modify_task",
        }
        
        if not action:
            return {
                "status": "error", 
                "message": "Missing required 'action' parameter. Must be one of: add_task, list_tasks, modify_task, complete_task.",
                "error_type": "missing_parameter",
                "valid_actions": ["add_task", "list_tasks", "modify_task", "complete_task"]
            }
        
        valid_actions = ["add_task", "list_tasks", "modify_task", "complete_task"]
        if action not in valid_actions:
            if action in action_suggestions:
                corrected_action = action_suggestions[action]
                logger.info(f"ProjectManagementTool: Auto-correcting action '{action}' -> '{corrected_action}'")
                action = corrected_action
            else:
                return {
                    "status": "error", 
                    "message": f"Invalid action '{action}'. Valid actions are: {', '.join(valid_actions)}.",
                    "error_type": "invalid_action",
                    "valid_actions": valid_actions
                }
        if not project_name or not session_name:
            return {"status": "error", "message": "Missing project/session context."}
            
        # [CRITICAL GUARD] Override any user/LLM-provided project_filter with the authenticated context project_name.
        # This prevents Hallucinations/Typos (like "Snake" vs "Sake") from causing empty task list queries 
        # or creating tasks with incorrect project attributes in the dedicated taskwarrior DB.
        kwargs["project_filter"] = project_name

        tw = self._get_taskwarrior_instance(project_name, session_name)
        if not tw:
            return {"status": "error", "message": "Failed to initialize TaskWarrior backend."}

        try:
            aliases = self._load_aliases(project_name, session_name)
            
            if action == "add_task":
                # Auto-map hallucinated parameter names common with LLMs
                if "task_title" in kwargs and not kwargs.get("title"): kwargs["title"] = kwargs.get("task_title")
                if "task_description" in kwargs and not kwargs.get("description"): kwargs["description"] = kwargs.get("task_description")
                if "task_priority" in kwargs and not kwargs.get("priority"): kwargs["priority"] = kwargs.get("task_priority")
                if "task_assignee" in kwargs and not kwargs.get("assignee_agent_id"): kwargs["assignee_agent_id"] = kwargs.get("task_assignee")
                if "task_depends" in kwargs and not kwargs.get("depends"): kwargs["depends"] = kwargs.get("task_depends")

                title = kwargs.get("title")
                description = kwargs.get("description")
                
                main_desc = None
                if title and description:
                    main_desc = f"{title}: {description}"
                elif title:
                    main_desc = title
                elif description:
                    main_desc = description
                
                if not main_desc:
                    return {"status": "error", "message": "Missing 'title' or 'description' for 'add_task'."}

                import difflib
                existing_tasks = tw.tasks.pending()
                if kwargs.get("project_filter"):
                    existing_tasks = existing_tasks.filter(project=kwargs["project_filter"])
                    
                target_desc_lower = main_desc.lower()
                for t in existing_tasks:
                    existing_desc = (t['description'] or "").lower() if 'description' in t else ""
                    if len(target_desc_lower) < 5 or len(existing_desc) < 5:
                        continue
                    
                    similarity = difflib.SequenceMatcher(None, target_desc_lower, existing_desc).ratio()
                    if similarity > 0.8 or target_desc_lower in existing_desc or existing_desc in target_desc_lower:
                        logger.warning(f"ProjectManagementTool: Prevented duplicate task creation: '{main_desc}' is very similar ({similarity:.2f}) to existing task UUID {t['uuid']}")
                        depends_list = [dep['uuid'] for dep in t['depends']] if 'depends' in t and t['depends'] else []
                        return {
                            "status": "success",
                            "message": f"Task skipped to prevent duplication. An existing task ('{t['description'] if 'description' in t else ''}') is >80% similar.",
                            "task_uuid": t['uuid'],
                            "task_id": t['id'] if 'id' in t else 0,
                            "description": t['description'] if 'description' in t else '',
                            "assignee": t['assignee'] if 'assignee' in t and t['assignee'] else 'None',
                            "depends": depends_list,
                            "is_duplicate": True
                        }

                task = Task(tw, description=main_desc)
                
                # Apply unified task_progress mapping (alias handling for 'status' fallback)
                raw_prog = kwargs.get("task_progress") or kwargs.get("status")
                t_prog, t_status = self._map_task_progress(raw_prog)
                task['task_progress'] = t_prog
                task['status'] = t_status
                
                if kwargs.get("priority"):
                    prio = str(kwargs["priority"]).upper()
                    if prio in ['HIGH', 'H']: prio = 'H'
                    elif prio in ['MEDIUM', 'M']: prio = 'M'
                    elif prio in ['LOW', 'L']: prio = 'L'
                    else: return {"status": "error", "message": f"Invalid priority '{kwargs['priority']}'. Valid priorities are: H (high), M (medium), L (low)."}
                    task['priority'] = prio
                if kwargs.get("project_filter"): task['project'] = kwargs["project_filter"]
                
                if kwargs.get("assignee_agent_id"): 
                    task['assignee'] = kwargs["assignee_agent_id"]
                elif agent_id.startswith("W"):
                    # Auto-assign the task to the worker who is creating it if no assignee is specified
                    task['assignee'] = agent_id
                    
                if kwargs.get("tags"):
                    tags_arg = kwargs["tags"]
                    if isinstance(tags_arg, str):
                        raw_tags = [tag.strip() for tag in tags_arg.split(',') if tag.strip()]
                    elif isinstance(tags_arg, list):
                        raw_tags = [str(t).strip() for t in tags_arg if t]
                    else:
                        raw_tags = [str(tags_arg).strip()]
                    # Sanitize: strip +/- prefixes, quotes, brackets that corrupt TaskWarrior JSON
                    sanitized = []
                    for t in raw_tags:
                        t = t.lstrip('+-').strip()
                        t = t.replace('"', '').replace("'", "").replace("[", "").replace("]", "").strip()
                        if t:
                            sanitized.append(t)
                    task['tags'] = set(sanitized)
                    
                if kwargs.get("depends"): 
                    dep_val_raw = str(kwargs["depends"]).strip()
                    # Support comma-separated dependency lists (e.g. "task_1,task_2,task_3")
                    dep_items = [d.strip() for d in dep_val_raw.split(',') if d.strip()]
                    resolved_deps = set()
                    for dep_item in dep_items:
                        # Resolve aliases first
                        if dep_item in aliases:
                            dep_item = aliases[dep_item]
                        
                        is_valid_uuid = bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', dep_item, re.IGNORECASE))
                        is_valid_id = dep_item.isdigit()
                        
                        if is_valid_uuid or is_valid_id:
                            try:
                                dep_task = tw.tasks.get(uuid=dep_item) if is_valid_uuid else tw.tasks.get(id=int(dep_item))
                                resolved_deps.add(dep_task)
                                logger.info(f"ProjectManagementTool: Resolved dependency '{dep_item}' -> UUID '{dep_task['uuid']}'.")
                            except Exception as e:
                                logger.warning(f"ProjectManagementTool: Dependency task '{dep_item}' not found: {e}. Skipping this dependency.")
                        else:
                            logger.warning(f"ProjectManagementTool: Invalid dependency format: '{dep_item}' (from raw: '{dep_val_raw}').")
                            return {
                                "status": "error",
                                "message": f"Invalid dependency format: '{dep_item}'. Dependencies must be valid UUIDs, integer IDs, or recognized task aliases (e.g. 'T1').",
                                "error_type": "invalid_dependency_format"
                            }
                    if resolved_deps:
                        task['depends'] = resolved_deps
                        logger.info(f"ProjectManagementTool: Set {len(resolved_deps)} dependencies for task.")

                # --- AUTO-LINK DEPENDENCY FOR DECOMPOSE STATE ---
                manager = kwargs.get("manager")
                if manager:
                    agent = manager.agents.get(agent_id)
                    if agent and agent.agent_type == "worker" and agent.state == "worker_decompose" and getattr(agent, 'current_task_id', None):
                        try:
                            parent_task_id = str(agent.current_task_id).strip()
                            parent_is_uuid = bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', parent_task_id, re.IGNORECASE))
                            parent_task = tw.tasks.get(uuid=parent_task_id) if parent_is_uuid else tw.tasks.get(id=int(parent_task_id))
                            
                            if 'depends' not in task or task['depends'] is None:
                                task['depends'] = set()
                            
                            task['depends'].add(parent_task)
                            
                            # --- FIX: Mark parent task as COMPLETED after decomposition ---
                            # The parent task is now broken into sub-tasks. Mark it as completed
                            # so the PM doesn't see it as active work. The PM should focus on
                            # the resulting sub-tasks instead.
                            parent_task['task_progress'] = 'completed'
                            parent_task['tags'] = list(set(list(parent_task.get('tags', [])) + ['decomposed']))
                            
                            # Complete the parent task in Taskwarrior
                            try:
                                parent_task.done()
                                logger.info(f"ProjectManagementTool: Parent task '{parent_task_id}' marked as COMPLETED (decomposed into sub-tasks).")
                            except Exception as done_err:
                                # Fallback: just save with completed progress if .done() fails
                                parent_task.save()
                                logger.warning(f"ProjectManagementTool: Could not call .done() on parent task '{parent_task_id}': {done_err}. Saved with 'completed' progress instead.")
                            
                            logger.info(f"ProjectManagementTool: Auto-linked new sub-task to parent task '{parent_task_id}'. Parent task completed after decomposition.")
                        except Exception as e:
                            logger.warning(f"ProjectManagementTool: Failed to auto-link parent task '{agent.current_task_id}': {e}")
                # ------------------------------------------------

                try:
                    task.save()
                    asyncio.create_task(broadcast(json.dumps({"type": "project_tasks_updated", "project_name": project_name, "session_name": session_name})))
                except Exception as e:
                    return {"status": "error", "message": f"Failed to save task: {e}"}

                user_task_id = kwargs.get("task_id")
                if user_task_id and isinstance(user_task_id, str):
                    aliases[user_task_id] = task['uuid']
                    self._save_aliases(project_name, session_name, aliases)
                    
                depends_list = [t['uuid'] for t in (task['depends'] if task['depends'] is not None else [])]
                return {"status": "success", "message": "Task added successfully.", "task_uuid": task['uuid'], "task_id": task['id'], "description": task['description'], "assignee": kwargs.get("assignee_agent_id"), "depends": depends_list}

            elif action == "list_tasks":
                tasks_query = tw.tasks.all()
                if kwargs.get("project_filter"): tasks_query = tasks_query.filter(project=kwargs["project_filter"])
                
                # Allow new task_progress_filter or legacy status_filter
                if "task_progress_filter" in kwargs:
                    tasks_query = tasks_query.filter(task_progress=kwargs["task_progress_filter"])
                elif "status_filter" in kwargs:
                    if kwargs["status_filter"] != "all":
                        tasks_query = tasks_query.filter(status=kwargs["status_filter"])
                    # else: "all" means no status filtering at all
                else:
                    # Default: filter out completed tasks to save tokens
                    tasks_query = tasks_query.pending()
                
                completed_count = 0
                try:
                    all_tasks_query = tw.tasks.all()
                    if kwargs.get("project_filter"): 
                        all_tasks_query = all_tasks_query.filter(project=kwargs["project_filter"])
                    completed_count = len(all_tasks_query.completed())
                except Exception as e:
                    logger.debug(f"Could not fetch completed tasks count: {e}")
                
                has_filters = any([
                    kwargs.get("task_progress_filter"),
                    kwargs.get("status_filter"),
                    kwargs.get("assignee_filter"),
                    kwargs.get("tags_filter")
                ])
                if not has_filters and agent_id and not agent_id.startswith("W"):
                    # PM must always specify filtering. List yet unassigned tasks by default when no filter is provided.
                    kwargs["assignee_filter"] = "unassigned"

                if "assignee_filter" in kwargs: 
                    a_filter = str(kwargs["assignee_filter"]).strip()
                    if a_filter.lower() not in ["none", "null", "unassigned"]:
                        tasks_query = tasks_query.filter(assignee=a_filter)
                elif agent_id and agent_id.startswith("W"):
                    # Auto-filter for the specific worker if no filter is provided
                    tasks_query = tasks_query.filter(assignee=agent_id)
                
                # Gracefully handle corrupted TaskWarrior data (e.g. malformed tags causing Invalid JSON)
                try:
                    tasks = tasks_query.all()
                except Exception as tw_err:
                    err_str = str(tw_err)
                    if "Invalid JSON" in err_str or "JSONDecodeError" in err_str:
                        logger.error(f"ProjectManagementTool: TaskWarrior data corruption detected: {err_str[:200]}")
                        # Fallback: try fetching without filters to identify scope
                        try:
                            raw_count = len(tw.tasks.all())
                        except Exception:
                            raw_count = "unknown"
                        return {
                            "status": "error",
                            "message": f"TaskWarrior data contains corrupted entries (likely malformed tags). Total tasks in DB: {raw_count}. Consider modifying the corrupted task's tags to fix this.",
                            "error_type": "data_corruption"
                        }
                    raise  # re-raise non-JSON errors
                
                if "assignee_filter" in kwargs and str(kwargs["assignee_filter"]).strip().lower() in ["none", "null", "unassigned"]:
                    tasks = [t for t in tasks if t['assignee'] is None or str(t['assignee']).strip() == ""]

                # Fallback: if 'tags' is passed but not 'tags_filter', use 'tags' as filter to be tolerant of LLM errors
                if "tags_filter" not in kwargs and kwargs.get("tags"):
                    tags_arg = kwargs.get("tags")
                    if isinstance(tags_arg, list):
                        kwargs["tags_filter"] = ",".join(str(t) for t in tags_arg)
                    elif isinstance(tags_arg, str):
                        kwargs["tags_filter"] = tags_arg

                if "tags_filter" in kwargs:
                    try:
                        filter_tags = set(tag.strip().lower() for tag in kwargs["tags_filter"].split(","))
                        mode = str(kwargs.get("tags_filter_mode", "include")).lower().strip()
                        filtered_tasks = []
                        for task in tasks:
                            task_tags = set(str(tag).lower() for tag in (task['tags'] if task['tags'] is not None else []))
                            
                            # Magic tags based on assignee status to accommodate LLM behavior
                            is_assigned = task['assignee'] is not None and str(task['assignee']).strip() != ""
                            if is_assigned:
                                task_tags.add("assigned")
                            else:
                                task_tags.add("unassigned")
                                
                            if mode == "include" and filter_tags.issubset(task_tags):
                                filtered_tasks.append(task)
                            elif mode == "any" and filter_tags.intersection(task_tags):
                                filtered_tasks.append(task)
                            elif mode == "exclude" and not filter_tags.intersection(task_tags):
                                filtered_tasks.append(task)
                        tasks = filtered_tasks
                    except Exception as e:
                        logger.warning(f"Error filtering tasks by tags: {e}")
                
                # Create a truly minimal output format replacing low-fi status with descriptive task_progress
                minimal_task_list = []
                for task in tasks:
                    try:
                        t_prog = task['task_progress']
                    except KeyError:
                        t_prog = None
                        
                    if not t_prog:
                        # Fallback for old tasks that lacked the UDA
                        try:
                            t_status = task['status']
                        except KeyError:
                            t_status = None
                        t_prog = "finished" if t_status == "completed" else "todo"
                        
                    minimal_task_list.append({
                        "uuid": task['uuid'], 
                        "description": task['description'], 
                        "task_progress": t_prog, 
                        "assignee": task['assignee'] if task['assignee'] is not None else None,
                        "depends": [t['uuid'] for t in (task['depends'] if task['depends'] is not None else [])]
                    })
                return {"status": "success", "message": f"Found {len(minimal_task_list)} task(s). ({completed_count} completed tasks are hidden by default).", "tasks": minimal_task_list}

            elif action == "modify_task":
                task_id = kwargs.get("task_id") or kwargs.get("task_uuid")
                if task_id is None or task_id == "":
                    return {"status": "error", "message": "Missing 'task_id' for 'modify_task'."}
                
                t_id_str = str(task_id).strip()
                if t_id_str in aliases:
                    t_id_str = aliases[t_id_str]
                    
                if t_id_str == "0":
                    return {"status": "error", "message": "Task ID '0' indicates a completed or deleted task. You must use the task's 'uuid' to modify it, or realize it is already completed."}

                if "field" in kwargs and "value" in kwargs:
                    field = str(kwargs["field"]).lower().strip()
                    value = kwargs["value"]
                    if field in ["status", "task_progress", "description", "priority", "tags", "depends"]:
                        kwargs[field] = value
                    elif field in ["assignee", "assignee_agent_id"]:
                        kwargs["assignee_agent_id"] = value

                # Auto-extract from hallucinated 'fields' dict or string
                fields_arg = kwargs.get("fields")
                if fields_arg and isinstance(fields_arg, dict):
                    for fk, fv in fields_arg.items():
                        fk_lower = str(fk).lower().strip()
                        if fk_lower and fv and (fk_lower not in kwargs or kwargs.get(fk_lower) in (None, '')):
                            kwargs[fk_lower] = fv
                    logger.info(f"ProjectManagementTool: Auto-extracted fields from 'fields' dict: {list(fields_arg.keys())}")
                elif fields_arg and isinstance(fields_arg, str) and fields_arg.strip():
                    # Try to parse "task_progress=finished" or "status:done" style
                    for sep in ['=', ':']:
                        if sep in fields_arg:
                            parts = fields_arg.split(sep, 1)
                            if len(parts) == 2:
                                fk, fv = parts[0].strip().lower(), parts[1].strip()
                                if fk and fv and (fk not in kwargs or kwargs.get(fk) in (None, '')):
                                    kwargs[fk] = fv
                                    logger.info(f"ProjectManagementTool: Auto-extracted field '{fk}'='{fv}' from 'fields' string")
                                break

                try:
                    if '-' in t_id_str and len(t_id_str) > 10:
                        try:
                            task = tw.tasks.get(uuid=t_id_str)
                        except Exception as get_err:
                            if "exist" in str(get_err) and len(t_id_str) < 36:
                                matches = [t for t in tw.tasks.all() if t['uuid'].startswith(t_id_str)]
                                if len(matches) == 1:
                                    task = matches[0]
                                else:
                                    raise get_err
                            else:
                                raise get_err
                    elif t_id_str.isdigit():
                        task = tw.tasks.get(id=int(t_id_str))
                    else:
                        matching = tw.tasks.filter(description=t_id_str)
                        if len(matching) != 1:
                            matching = tw.tasks.filter(description=t_id_str.replace('_', ' '))
                        
                        if len(matching) == 1:
                            task = matching[0]
                        else:
                            # Fuzzy matching fallback
                            search_str = t_id_str.lower().replace('_', ' ')
                            all_tasks_to_search = tw.tasks.pending() if action != "complete_task" else tw.tasks.all()
                            fuzzy_matches = []
                            for t in all_tasks_to_search:
                                desc = (t['description'] or '').lower()
                                if search_str in desc:
                                    fuzzy_matches.append(t)
                            
                            if len(fuzzy_matches) == 1:
                                task = fuzzy_matches[0]
                                logger.info(f"ProjectManagementTool: Fuzzy matched '{t_id_str}' to UUID '{task['uuid']}'")
                            elif len(fuzzy_matches) > 1:
                                raise ValueError(f"Ambiguous task id/name '{t_id_str}' matched {len(fuzzy_matches)} tasks.")
                            else:
                                raise ValueError("Task not found by description or ID.")
                except Exception as e:
                    # Provide an actionable error message suggesting they list the tasks
                    return {
                        "status": "error", 
                        "message": f"Task '{task_id}' not found. Note that integer IDs shift as tasks are completed, and UUIDs must be exact. Detail: {e}",
                        "suggestion": "IMPORTANT: You should run <project_management><action>list_tasks</action></project_management> to get the current list of tasks and their valid IDs/UUIDs."
                    }

                # Validate task_progress or legacy status if provided
                if "task_progress" in kwargs or "status" in kwargs:
                    raw_prog = kwargs.get("task_progress") or kwargs.get("status")
                    t_prog, t_status = self._map_task_progress(raw_prog)
                    task["task_progress"] = t_prog
                    task["status"] = t_status
                    modified_fields = ["task_progress", "status"]
                else:
                    modified_fields = []

                if "description" in kwargs: 
                    task['description'] = kwargs["description"]; modified_fields.append("description")
                if "priority" in kwargs: 
                    prio = str(kwargs["priority"]).upper()
                    if prio in ['HIGH', 'H']: prio = 'H'
                    elif prio in ['MEDIUM', 'M']: prio = 'M'
                    elif prio in ['LOW', 'L']: prio = 'L'
                    elif prio in ['', 'NONE', 'NULL']: prio = ''
                    else: return {"status": "error", "message": f"Invalid priority '{kwargs['priority']}'. Valid priorities are: H (high), M (medium), L (low)."}
                    task['priority'] = prio; modified_fields.append("priority")
                if "tags" in kwargs: 
                    tags_arg = kwargs["tags"]
                    if isinstance(tags_arg, str):
                        raw_tags = [tag.strip() for tag in tags_arg.split(',') if tag.strip()]
                    elif isinstance(tags_arg, list):
                        raw_tags = [str(t).strip() for t in tags_arg if t]
                    else:
                        raw_tags = [str(tags_arg).strip()]
                    current_tags = task['tags'] if task['tags'] else set()
                    if isinstance(current_tags, list):
                        current_tags = set(current_tags)
                    elif isinstance(current_tags, set):
                        current_tags = current_tags.copy()
                        
                    for t in raw_tags:
                        t = t.strip()
                        is_remove = t.startswith('-')
                        t_clean = t.lstrip('+-').strip()
                        t_clean = t_clean.replace('"', '').replace("'", "").replace("[", "").replace("]", "").strip()
                        if not t_clean:
                            continue
                            
                        if is_remove:
                            if t_clean in current_tags:
                                current_tags.remove(t_clean)
                        else:
                            current_tags.add(t_clean)
                    task['tags'] = set(current_tags)
                    modified_fields.append("tags")
                
                assignee_arg = kwargs.get("assignee_agent_id") or kwargs.get("assignee")
                if assignee_arg: 
                    new_assignee = assignee_arg
                    if task['assignee'] == new_assignee:
                        # Silently accept redundant assignment to prevent LLM retry loops
                        pass
                    elif task['status'] == 'pending' and task['assignee'] and task['assignee'] != agent_id and not agent_id.startswith("admin") and not agent_id.startswith("PM"):
                        return {"status": "error", "message": f"Task '{task_id}' is already 'pending' and actively assigned to '{task['assignee']}'. Reassigning active tasks owned by others is blocked to prevent disruption. Only the assignee can hand it off, or its status must first be changed to 'waiting'."}
                    else:
                        task['assignee'] = new_assignee
                        modified_fields.append("assignee")
                if "depends" in kwargs:
                    try:
                        dep_id = str(kwargs["depends"]).strip()
                        if dep_id in aliases:
                            dep_id = aliases[dep_id]
                        dep_task = tw.tasks.get(uuid=dep_id) if '-' in dep_id else tw.tasks.get(id=int(dep_id))
                        if task['depends'] is None:
                            task['depends'] = set()
                        task['depends'].add(dep_task)
                        modified_fields.append("depends")
                    except Exception as e:
                        return {"status": "error", "message": f"Dependency task '{kwargs['depends']}' not found. Integer IDs shift when tasks are completed. Use UUID or custom alias instead. Detail: {e}"}

                if not modified_fields:
                    return {"status": "error", "message": (
                        "No valid fields provided for modification. "
                        "You must provide at least one of these as a DIRECT parameter (not inside a 'fields' wrapper): "
                        "task_progress, status, description, priority, tags, depends, assignee_agent_id. "
                        "CORRECT example: <project_management><action>modify_task</action>"
                        "<task_id>UUID</task_id><task_progress>finished</task_progress></project_management>"
                    )}

                task.save()
                asyncio.create_task(broadcast(json.dumps({"type": "project_tasks_updated", "project_name": project_name, "session_name": session_name})))
                assignee_to_return = kwargs.get("assignee_agent_id") or task['assignee']
                depends_list = [t['uuid'] for t in (task['depends'] if task['depends'] is not None else [])]
                return {"status": "success", "message": f"Task '{task_id}' modified successfully.", "task_uuid": task['uuid'], "task_id": task['id'], "modified_fields": modified_fields, "task_progress": task['task_progress'], "description": task['description'], "assignee": assignee_to_return, "depends": depends_list}

            elif action == "complete_task":
                task_id = kwargs.get("task_id") or kwargs.get("task_uuid")
                if task_id is None or task_id == "":
                    return {"status": "error", "message": "Missing 'task_id' for 'complete_task'."}
                
                t_id_str = str(task_id).strip()
                if t_id_str in aliases:
                    t_id_str = aliases[t_id_str]
                    
                if t_id_str == "0":
                    return {"status": "error", "message": "Task ID '0' indicates a completed or deleted task. You must use the task's 'uuid' to modify it, or realize it is already completed."}

                try:
                    if '-' in t_id_str and len(t_id_str) > 10:
                        try:
                            task = tw.tasks.get(uuid=t_id_str)
                        except Exception as get_err:
                            if "exist" in str(get_err) and len(t_id_str) < 36:
                                matches = [t for t in tw.tasks.all() if t['uuid'].startswith(t_id_str)]
                                if len(matches) == 1:
                                    task = matches[0]
                                else:
                                    raise get_err
                            else:
                                raise get_err
                    elif t_id_str.isdigit():
                        task = tw.tasks.get(id=int(t_id_str))
                    else:
                        matching = tw.tasks.filter(description=t_id_str)
                        if len(matching) != 1:
                            matching = tw.tasks.filter(description=t_id_str.replace('_', ' '))
                        
                        if len(matching) == 1:
                            task = matching[0]
                        else:
                            # Fuzzy matching fallback
                            search_str = t_id_str.lower().replace('_', ' ')
                            all_tasks_to_search = tw.tasks.pending() if action != "complete_task" else tw.tasks.all()
                            fuzzy_matches = []
                            for t in all_tasks_to_search:
                                desc = (t['description'] or '').lower()
                                if search_str in desc:
                                    fuzzy_matches.append(t)
                            
                            if len(fuzzy_matches) == 1:
                                task = fuzzy_matches[0]
                                logger.info(f"ProjectManagementTool: Fuzzy matched '{t_id_str}' to UUID '{task['uuid']}'")
                            elif len(fuzzy_matches) > 1:
                                raise ValueError(f"Ambiguous task id/name '{t_id_str}' matched {len(fuzzy_matches)} tasks.")
                            else:
                                raise ValueError("Task not found by description or ID.")
                except Exception as e:
                    # Provide an actionable error message suggesting they list the tasks
                    return {
                        "status": "error", 
                        "message": f"Task '{task_id}' not found. Note that integer IDs shift as tasks are completed, and UUIDs must be exact. Detail: {e}",
                        "suggestion": "IMPORTANT: You should run <project_management><action>list_tasks</action></project_management> to get the current list of tasks and their valid IDs/UUIDs."
                    }

                try:
                    task.done() # This triggers Taskwarrior to set status=completed
                    task['task_progress'] = "finished"
                    task.save()
                    asyncio.create_task(broadcast(json.dumps({"type": "project_tasks_updated", "project_name": project_name, "session_name": session_name})))
                except Exception as e:
                    if "completed" in str(e).lower() or "Cannot complete a completed task" in str(e):
                        return {"status": "success", "message": f"Task '{task_id}' is already completed ('finished').", "task_uuid": task['uuid'], "task_id": task['id']}
                    raise e
                    
                return {"status": "success", "message": f"Task '{task_id}' marked as finished.", "task_uuid": task['uuid'], "task_id": task['id']}

            else:
                return {"status": "error", "message": f"Unknown action: '{action}'."}

        except Exception as e:
            logger.error(f"Error executing ProjectManagementTool action '{action}': {e}", exc_info=True)
            return {"status": "error", "message": f"An unexpected error occurred: {e}"}

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """Returns detailed usage instructions for the ProjectManagementTool."""
        project_name_placeholder = agent_context.get('project_name', '{project_name}') if agent_context else '{project_name}'

        common_header = f"""**Tool Name:** project_management

**Description:** Manages project tasks using a Taskwarrior backend. This tool is available to PMs (for general project management) and Workers (to decompose tasks into sub-tasks).

**CRITICAL - Task Assignment:**
When assigning a task to an agent, you MUST use BOTH the `assignee_agent_id` parameter AND add the agent's ID as a tag (e.g., `tags=+{project_name_placeholder}_worker_1`). This ensures the framework can correctly track assignments.
"""

        if sub_action == "add_task":
            return common_header + f"""
**Action: add_task**
Creates a new task.
*   `<description>` (string, required): A clear and concise description of the task.
*   `<project_filter>` (string, optional): The project name to associate the task with. Defaults to current project.
*   `<task_progress>` (string, optional): Initial progress state. Valid options: 'todo', 'in_progress', 'waiting', 'stuck', 'failed', 'finished'. Defaults to 'todo'.
*   `<priority>` (string, optional): Task priority (e.g., 'H', 'M', 'L').
*   `<tags>` (string, optional): Comma-separated list of tags to add (e.g., `+bug,+urgent`).
*   `<assignee_agent_id>` (string, optional): The ID of the agent to assign this task to.
*   `<depends>` (string, optional): The UUID of a task that this new task depends on.
*   Example:
    ```xml
    <project_management>
      <action>add_task</action>
      <description>Implement user authentication API endpoint</description>
      <project_filter>{project_name_placeholder}</project_filter>
      <task_progress>todo</task_progress>
      <priority>H</priority>
      <tags>+backend,+api</tags>
    </project_management>
    ```
"""
        elif sub_action == "list_tasks":
            return common_header + f"""
**Action: list_tasks**
Lists existing tasks. Note that we use a granular 'task_progress' system to track exact task states.
*   `<project_filter>` (string, optional): Filter tasks by a specific project name.
*   `<task_progress_filter>` (string, optional): Filter by exact progress (e.g., 'todo', 'in_progress', 'stuck', 'finished').
*   `<assignee_filter>` (string, optional): Filter by assigned agent ID. To find unassigned tasks awaiting delegation, set this exact value to 'unassigned'.
*   `<tags_filter>` (string, optional): Filter by a comma-separated list of tags. You can also use the magic tags 'assigned' or 'unassigned' here in combination with exclude/include modes.
*   Example:
    ```xml
    <project_management>
      <action>list_tasks</action>
      <project_filter>{project_name_placeholder}</project_filter>
      <assignee_filter>unassigned</assignee_filter>
      <task_progress_filter>todo</task_progress_filter>
    </project_management>
    ```
"""
        elif sub_action == "modify_task":
            return common_header + f"""
**Action: modify_task**
Modifies an existing task.
*   `<task_id>` (string, required): The UUID or integer ID of the task to modify.
*   `<description>` (string, optional): New description for the task.
*   `<task_progress>` (string, optional): Update task progress. Use detailed states: 'todo', 'in_progress', 'waiting', 'stuck', 'failed', 'finished'. (Calling this automatically updates underlying system status).
*   `<priority>` (string, optional): New priority.
*   `<tags>` (string, optional): New comma-separated list of tags. Use `+tag` to add and `-tag` to remove.
*   `<assignee_agent_id>` (string, optional): Reassign the task to a new agent. **REMEMBER to also update the tag.**
*   `<depends>` (string, optional): Add a dependency by providing the task ID or UUID it depends on.
*   Example:
    ```xml
    <project_management>
      <action>modify_task</action>
      <task_id>123e4567-e89b-12d3-a456-426614174000</task_id>
      <task_progress>in_progress</task_progress>
      <assignee_agent_id>{project_name_placeholder}_worker_1</assignee_agent_id>
      <tags>+{project_name_placeholder}_worker_1,assigned</tags>
    </project_management>
    ```
"""
        elif sub_action == "complete_task":
            return common_header + """
**Action: complete_task**
Marks a task as completely finished. Shortcut for `modify_task` with `task_progress='finished'`.
*   `<task_id>` (string, required): The UUID or integer ID of the task to complete.
*   Example:
    ```xml
    <project_management>
      <action>complete_task</action>
      <task_id>1</task_id>
    </project_management>
    ```
"""

        return common_header + """
**Available Actions Summary:**
1.  **add_task:** Creates a new task.
2.  **list_tasks:** Lists existing tasks.
3.  **modify_task:** Modifies an existing task.
4.  **complete_task:** Marks a task as completed.

**To get detailed instructions and parameter lists for a specific action, call:**
<tool_information>
  <action>get_info</action>
  <tool_name>project_management</tool_name>
  <sub_action>ACTION_NAME</sub_action>
</tool_information>
"""
