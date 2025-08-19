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
    TaskWarrior = None
    Task = None
    TASKLIB_AVAILABLE = False

if TYPE_CHECKING:
    from tasklib import TaskWarrior, Task

from .base import BaseTool, ToolParameter
from src.config.settings import BASE_DIR
from typing import List

logger = logging.getLogger(__name__)

class ProjectManagementTool(BaseTool):
    name = "project_management"
    auth_level: str = "pm"
    summary: Optional[str] = "Manages project tasks (add, list, modify, complete) via Taskwarrior."
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

    def _get_taskwarrior_instance(self, project_name: str, session_name: str) -> Optional['TaskWarrior']:
        if not TASKLIB_AVAILABLE:
            return None
        try:
            data_path = BASE_DIR / "projects" / project_name / session_name / "task_data"
            data_path.mkdir(parents=True, exist_ok=True)
            taskrc_path = data_path / '.taskrc'
            if not taskrc_path.exists():
                with open(taskrc_path, 'w') as f:
                    f.write("uda.assignee.type=string\nuda.assignee.label=Assignee\n")
            return TaskWarrior(data_location=str(data_path))
        except Exception as e:
             logger.error(f"Failed to initialize TaskWarrior at {data_path}: {e}", exc_info=True)
             return None

    async def execute(self, **kwargs) -> Dict[str, Any]:
        if not TASKLIB_AVAILABLE:
            return {"status": "error", "message": "Tasklib library not installed."}

        action = kwargs.get("action")
        project_name = kwargs.get("project_name")
        session_name = kwargs.get("session_name")

        if not action:
            return {"status": "error", "message": "Missing required parameter: 'action'."}
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

                add_output_lines = tw.execute_command(add_cmd_args)
                id_match = re.search(r'Created task (\d+)\.', "\n".join(add_output_lines))
                if not id_match:
                    return {"status": "error", "message": f"Failed to create task. Output: {add_output_lines}"}

                created_task_id = int(id_match.group(1))
                final_task = tw.tasks.get(id=created_task_id)
                return {"status": "success", "message": "Task added successfully.", "task_uuid": final_task['uuid'], "task_id": final_task['id'], "description": final_task['description'], "assignee": kwargs.get("assignee_agent_id")}

            elif action == "list_tasks":
                tasks_query = tw.tasks.all()
                if kwargs.get("project_filter"): tasks_query = tasks_query.filter(project=kwargs["project_filter"])

                tasks = tasks_query.all()
                minimal_task_list = [{"uuid": task['uuid'], "description": task['description']} for task in tasks]
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

                if not modified_fields:
                    return {"status": "error", "message": "No valid fields provided for modification."}

                task.save()
                assignee_to_return = kwargs.get("assignee_agent_id") or task['assignee']
                return {"status": "success", "message": f"Task '{task_id}' modified successfully.", "task_uuid": task['uuid'], "task_id": task['id'], "modified_fields": modified_fields, "description": task['description'], "assignee": assignee_to_return}

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

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None) -> str:
        return "Detailed usage is available via the tool's description."
