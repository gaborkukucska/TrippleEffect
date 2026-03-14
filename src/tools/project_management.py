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
        ToolParameter(name="description", type="str", required=False, description="Task description."),
        ToolParameter(name="task_id", type="str", required=False, description="UUID or ID of the task."),
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
                suggested_action = action_suggestions[action]
                return {
                    "status": "error", 
                    "message": f"Invalid action '{action}'. Did you mean '{suggested_action}'? Valid actions are: {', '.join(valid_actions)}.",
                    "error_type": "invalid_action",
                    "suggested_action": suggested_action,
                    "valid_actions": valid_actions
                }
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
            if action == "add_task":
                description = kwargs.get("description")
                if not description:
                    return {"status": "error", "message": "Missing 'description' for 'add_task'."}

                add_cmd_args = ['add', description]
                if kwargs.get("priority"): add_cmd_args.append(f'priority:{kwargs["priority"]}')
                if kwargs.get("project_filter"): add_cmd_args.append(f'project:{kwargs["project_filter"]}')
                if kwargs.get("assignee_agent_id"): add_cmd_args.append(f'assignee:"{kwargs["assignee_agent_id"]}"')
                if kwargs.get("tags"): add_cmd_args.extend([f'+{tag}' for tag in kwargs["tags"]])
                if kwargs.get("depends"): add_cmd_args.append(f'depends:{kwargs["depends"]}')

                add_output_lines = tw.execute_command(add_cmd_args)
                id_match = re.search(r'Created task (\d+)\.', "\n".join(add_output_lines))
                if not id_match:
                    return {"status": "error", "message": f"Failed to create task. Output: {add_output_lines}"}

                created_task_id = int(id_match.group(1))
                final_task = tw.tasks.get(id=created_task_id)
                task_depends = final_task['depends'] if final_task['depends'] is not None else []
                depends_list = [t['uuid'] for t in task_depends]
                return {"status": "success", "message": "Task added successfully.", "task_uuid": final_task['uuid'], "task_id": final_task['id'], "description": final_task['description'], "assignee": kwargs.get("assignee_agent_id"), "depends": depends_list}

            elif action == "list_tasks":
                tasks_query = tw.tasks.all()
                if kwargs.get("project_filter"): tasks_query = tasks_query.filter(project=kwargs["project_filter"])

                tasks = tasks_query.all()
                minimal_task_list = [{"uuid": task['uuid'], "id": task['id'], "description": task['description'], "status": task['status'], "depends": [t['uuid'] for t in (task['depends'] if task['depends'] is not None else [])]} for task in tasks]
                return {"status": "success", "message": f"Found {len(minimal_task_list)} task(s).", "tasks": minimal_task_list}

            elif action == "modify_task":
                task_id = kwargs.get("task_id")
                if not task_id:
                    return {"status": "error", "message": "Missing 'task_id' for 'modify_task'."}

                try:
                    task = tw.tasks.get(uuid=task_id) if '-' in task_id else tw.tasks.get(id=int(task_id))
                except Exception:
                    return {"status": "error", "message": f"Task with ID or UUID '{task_id}' not found."}

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
                if "tags" in kwargs: task['tags'] = set(kwargs["tags"]); modified_fields.append("tags")
                if kwargs.get("assignee_agent_id"): task['assignee'] = kwargs["assignee_agent_id"]; modified_fields.append("assignee")
                if "depends" in kwargs:
                    try:
                        dep_id = kwargs["depends"]
                        dep_task = tw.tasks.get(uuid=dep_id) if '-' in dep_id else tw.tasks.get(id=int(dep_id))
                        if task['depends'] is None:
                            task['depends'] = set()
                        task['depends'].add(dep_task)
                        modified_fields.append("depends")
                    except Exception as e:
                        return {"status": "error", "message": f"Dependency task '{kwargs['depends']}' not found. {e}"}

                if not modified_fields:
                    return {"status": "error", "message": "No valid fields provided for modification."}

                task.save()
                assignee_to_return = kwargs.get("assignee_agent_id") or task['assignee']
                depends_list = [t['uuid'] for t in (task['depends'] if task['depends'] is not None else [])]
                return {"status": "success", "message": f"Task '{task_id}' modified successfully.", "task_uuid": task['uuid'], "task_id": task['id'], "modified_fields": modified_fields, "description": task['description'], "assignee": assignee_to_return, "depends": depends_list}

            elif action == "complete_task":
                task_id = kwargs.get("task_id")
                if not task_id:
                    return {"status": "error", "message": "Missing 'task_id' for 'complete_task'."}

                try:
                    task = tw.tasks.get(uuid=task_id) if '-' in task_id else tw.tasks.get(id=int(task_id))
                except Exception:
                    return {"status": "error", "message": f"Task with ID or UUID '{task_id}' not found."}

                task.done()
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
