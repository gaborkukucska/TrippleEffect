# START OF FILE src/tools/project_management.py
import logging
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING # Import TYPE_CHECKING
import re

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
        ToolParameter(name="status", type="str", required=False, description="New status for the task."),
        ToolParameter(name="priority", type="str", required=False, description="Task priority."),
        ToolParameter(name="project_filter", type="str", required=False, description="Filter tasks by project."),
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

        # Check for common mistakes and provide helpful suggestions
        action_suggestions = {
            "create_task": "add_task",
            "new_task": "add_task", 
            "add": "add_task",
            "create": "add_task",
            "list": "list_tasks",
            "show": "list_tasks",
            "show_tasks": "list_tasks",
            "get_tasks": "list_tasks",
            "update_task": "modify_task",
            "edit_task": "modify_task",
            "change_task": "modify_task",
            "update": "modify_task",
            "edit": "modify_task",
            "finish_task": "complete_task",
            "done": "complete_task",
            "finish": "complete_task",
            "mark_complete": "complete_task"
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

        tw = self._get_taskwarrior_instance(project_name, session_name)
        if not tw:
            return {"status": "error", "message": "Failed to initialize TaskWarrior backend."}

        try:
            aliases = self._load_aliases(project_name, session_name)
            
            if action == "add_task":
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

                task = Task(tw, description=main_desc)
                if kwargs.get("priority"): task['priority'] = kwargs["priority"]
                if kwargs.get("project_filter"): task['project'] = kwargs["project_filter"]
                if kwargs.get("assignee_agent_id"): task['assignee'] = kwargs["assignee_agent_id"]
                if kwargs.get("tags"):
                    tags_arg = kwargs["tags"]
                    if isinstance(tags_arg, str):
                        task['tags'] = set([tag.strip() for tag in tags_arg.split(',') if tag.strip()])
                    else:
                        task['tags'] = set(tags_arg)
                    
                if kwargs.get("depends"): 
                    dep_val = str(kwargs["depends"]).strip()
                    if dep_val in aliases:
                        dep_val = aliases[dep_val]
                        
                    is_valid_uuid = bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', dep_val, re.IGNORECASE))
                    is_valid_id = dep_val.isdigit()
                    
                    if is_valid_uuid or is_valid_id:
                        try:
                            dep_task = tw.tasks.get(uuid=dep_val) if is_valid_uuid else tw.tasks.get(id=int(dep_val))
                            task['depends'] = {dep_task}
                        except Exception as e:
                            logger.warning(f"ProjectManagementTool: Dependency task '{dep_val}' not found: {e}. Task will be created without this dependency.")
                    else:
                        logger.warning(f"ProjectManagementTool: Skipping invalid dependency format: '{dep_val}'.")

                try:
                    task.save()
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
                if kwargs.get("status_filter"): tasks_query = tasks_query.filter(status=kwargs["status_filter"])

                tasks = tasks_query.all()
                if "tags_filter" in kwargs:
                    try:
                        filter_tags = set(tag.strip() for tag in kwargs["tags_filter"].split(","))
                        mode = kwargs.get("tags_filter_mode", "include")
                        filtered_tasks = []
                        for task in tasks:
                            task_tags = set(task['tags'] if task['tags'] is not None else [])
                            if mode == "include" and filter_tags.issubset(task_tags):
                                filtered_tasks.append(task)
                            elif mode == "any" and filter_tags.intersection(task_tags):
                                filtered_tasks.append(task)
                            elif mode == "exclude" and not filter_tags.intersection(task_tags):
                                filtered_tasks.append(task)
                        tasks = filtered_tasks
                    except Exception as e:
                        logger.warning(f"Error filtering tasks by tags: {e}")
                minimal_task_list = [{"uuid": task['uuid'], "id": task['id'], "description": task['description'], "status": task['status'], "assignee": task['assignee'] if task['assignee'] is not None else None, "tags": list(task['tags'] if task['tags'] is not None else []), "depends": [t['uuid'] for t in (task['depends'] if task['depends'] is not None else [])]} for task in tasks]
                return {"status": "success", "message": f"Found {len(minimal_task_list)} task(s).", "tasks": minimal_task_list}

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
                    if field in ["status", "description", "priority", "tags", "depends"]:
                        kwargs[field] = value
                    elif field in ["assignee", "assignee_agent_id"]:
                        kwargs["assignee_agent_id"] = value

                try:
                    if '-' in t_id_str and len(t_id_str) > 10:
                        task = tw.tasks.get(uuid=t_id_str)
                    elif t_id_str.isdigit():
                        task = tw.tasks.get(id=int(t_id_str))
                    else:
                        matching = tw.tasks.filter(description=t_id_str)
                        if len(matching) != 1:
                            matching = tw.tasks.filter(description=t_id_str.replace('_', ' '))
                        if len(matching) == 1:
                            task = matching[0]
                        else:
                            raise ValueError("Task not found by description")
                except Exception:
                    return {"status": "error", "message": f"Task '{task_id}' not found. Verify ID or exact Name."}

                # Validate status if provided
                if "status" in kwargs:
                    status = kwargs["status"]
                    valid_statuses = ["pending", "completed", "deleted", "waiting", "recurring"]
                    if status not in valid_statuses:
                        # Map common alternative statuses to valid ones
                        status_mapping = {
                            "assigned": "pending",
                            "open": "pending", 
                            "in_progress": "pending",
                            "active": "pending",
                            "not_started": "pending",
                            "todo": "pending",
                            "done": "completed",
                            "finished": "completed",
                            "closed": "completed"
                        }
                        if status in status_mapping:
                            status = status_mapping[status]
                            logger.info(f"TaskWarrior: Mapped invalid status '{kwargs['status']}' to valid status '{status}'")
                        else:
                            return {"status": "error", "message": f"Invalid status '{status}'. Valid statuses are: {', '.join(valid_statuses)}. Note: 'assigned' should be mapped to 'pending'."}
                    kwargs["status"] = status

                modified_fields = []
                if "description" in kwargs: task['description'] = kwargs["description"]; modified_fields.append("description")
                if "status" in kwargs: task['status'] = kwargs["status"]; modified_fields.append("status")
                if "priority" in kwargs: task['priority'] = kwargs["priority"]; modified_fields.append("priority")
                if "tags" in kwargs: 
                    tags_arg = kwargs["tags"]
                    if isinstance(tags_arg, str):
                        task['tags'] = set([tag.strip() for tag in tags_arg.split(',') if tag.strip()])
                    else:
                        task['tags'] = set(tags_arg)
                    modified_fields.append("tags")
                
                if kwargs.get("assignee_agent_id"): 
                    new_assignee = kwargs["assignee_agent_id"]
                    if task['assignee'] == new_assignee:
                        return {"status": "error", "message": f"The task '{task_id}' is ALREADY ASSIGNED to '{new_assignee}' and its current status is '{task['status']}'. You must NOT reassign it again. Wait for the agent to finish or send them a message."}
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
                    return {"status": "error", "message": "No valid fields provided for modification. Valid fields are: status, description, priority, tags, depends, assignee_agent_id."}

                task.save()
                assignee_to_return = kwargs.get("assignee_agent_id") or task['assignee']
                depends_list = [t['uuid'] for t in (task['depends'] if task['depends'] is not None else [])]
                return {"status": "success", "message": f"Task '{task_id}' modified successfully.", "task_uuid": task['uuid'], "task_id": task['id'], "modified_fields": modified_fields, "description": task['description'], "assignee": assignee_to_return, "depends": depends_list}

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
                        task = tw.tasks.get(uuid=t_id_str)
                    elif t_id_str.isdigit():
                        task = tw.tasks.get(id=int(t_id_str))
                    else:
                        matching = tw.tasks.filter(description=t_id_str)
                        if len(matching) != 1:
                            matching = tw.tasks.filter(description=t_id_str.replace('_', ' '))
                        if len(matching) == 1:
                            task = matching[0]
                        else:
                            raise ValueError("Task not found by description")
                except Exception:
                    return {"status": "error", "message": f"Task '{task_id}' not found. Verify ID or exact Name."}

                try:
                    task.done()
                except Exception as e:
                    if "completed" in str(e).lower() or "Cannot complete a completed task" in str(e):
                        return {"status": "success", "message": f"Task '{task_id}' is already completed.", "task_uuid": task['uuid'], "task_id": task['id']}
                    raise e
                    
                return {"status": "success", "message": f"Task '{task_id}' marked as completed.", "task_uuid": task['uuid'], "task_id": task['id']}

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
      <priority>H</priority>
      <tags>+backend,+api</tags>
    </project_management>
    ```
"""
        elif sub_action == "list_tasks":
            return common_header + f"""
**Action: list_tasks**
Lists existing tasks.
*   `<project_filter>` (string, optional): Filter tasks by a specific project name.
*   `<status_filter>` (string, optional): Filter by status (e.g., 'pending', 'completed').
*   `<tags_filter>` (string, optional): Filter by a comma-separated list of tags.
*   Example:
    ```xml
    <project_management>
      <action>list_tasks</action>
      <project_filter>{project_name_placeholder}</project_filter>
      <status_filter>pending</status_filter>
    </project_management>
    ```
"""
        elif sub_action == "modify_task":
            return common_header + f"""
**Action: modify_task**
Modifies an existing task.
*   `<task_id>` (string, required): The UUID or integer ID of the task to modify.
*   `<description>` (string, optional): New description for the task.
*   `<status>` (string, optional): New status (e.g., 'completed', 'deleted').
*   `<priority>` (string, optional): New priority.
*   `<tags>` (string, optional): New comma-separated list of tags. Use `+tag` to add and `-tag` to remove.
*   `<assignee_agent_id>` (string, optional): Reassign the task to a new agent. **REMEMBER to also update the tag.**
*   `<depends>` (string, optional): Add a dependency by providing the task ID or UUID it depends on.
*   Example:
    ```xml
    <project_management>
      <action>modify_task</action>
      <task_id>123e4567-e89b-12d3-a456-426614174000</task_id>
      <assignee_agent_id>{project_name_placeholder}_worker_1</assignee_agent_id>
      <tags>+{project_name_placeholder}_worker_1,assigned</tags>
      <depends>2</depends>
    </project_management>
    ```
"""
        elif sub_action == "complete_task":
            return common_header + """
**Action: complete_task**
Marks a task as completed. Shortcut for `modify_task` with `status='completed'`.
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
