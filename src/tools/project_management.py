# START OF FILE src/tools/project_management.py
import logging
from pathlib import Path
from typing import Dict, Any, Optional

# Import tasklib safely
try:
    from tasklib import TaskWarrior, Task
    TASKLIB_AVAILABLE = True
except ImportError:
    TaskWarrior = None
    Task = None
    TASKLIB_AVAILABLE = False

from .base import BaseTool, ToolParameter # Import ToolParameter
from src.config.settings import BASE_DIR # Import BASE_DIR for constructing paths
from typing import List # Import List for type hinting

logger = logging.getLogger(__name__)

class ProjectManagementTool(BaseTool):
    """
    Tool for managing project tasks using the Tasklib library.
    Interacts with a Taskwarrior data backend located within the specific
    project/session directory.
    """
    name = "project_management"
    description = (
        "Manages project tasks (add, list, modify, complete) using Tasklib. "
        "Requires 'action' parameter. Task data is stored per project/session."
    )
    # Use ToolParameter objects
    parameters: List[ToolParameter] = [
        ToolParameter(name="action", type="str", required=True, description="The action to perform (e.g., 'add_task', 'list_tasks')."),
        ToolParameter(name="description", type="str", required=False, description="Task description (for 'add_task')."),
        ToolParameter(name="task_id", type="str", required=False, description="UUID or ID of the task to modify/get."),
        ToolParameter(name="status", type="str", required=False, description="New status for the task (e.g., 'pending', 'completed', 'deleted')."),
        ToolParameter(name="priority", type="str", required=False, description="Task priority (e.g., 'H', 'M', 'L')."),
        ToolParameter(name="project_filter", type="str", required=False, description="Filter tasks by project name (for 'list_tasks')."),
        ToolParameter(name="tags", type="list", required=False, description="List of tags to add or filter by."),
        ToolParameter(name="depends", type="str", required=False, description="UUID of the task this new task depends on (for 'add_task')."),
        ToolParameter(name="assignee_agent_id", type="str", required=False, description="The agent ID assigned to the task."),
    ]

    def __init__(self, project_name: Optional[str] = None, session_name: Optional[str] = None):
        """
        Initializes the tool. Project/session context is passed during execution.
        """
        if not TASKLIB_AVAILABLE:
            logger.error("Tasklib library is not installed. ProjectManagementTool will not function.")
            # Optionally raise an error or handle gracefully
        # No TaskWarrior instance created here, it's created per-execution

    def _get_taskwarrior_instance(self, project_name: str, session_name: str) -> Optional[TaskWarrior]:
        """Initializes TaskWarrior with the correct data location."""
        if not TASKLIB_AVAILABLE:
            logger.error("Tasklib not available.")
            return None
        if not project_name or not session_name:
            logger.error("Project name and session name are required to initialize TaskWarrior.")
            return None

        try:
            # Construct the path relative to BASE_DIR/projects
            data_path = BASE_DIR / "projects" / project_name / session_name / "task_data"
            # Ensure the directory exists
            data_path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Initializing TaskWarrior with data_location: {data_path}")
            # Override default config to point to our specific data location
            return TaskWarrior(data_location=str(data_path))
        except Exception as e:
            logger.error(f"Failed to initialize TaskWarrior at {data_path}: {e}", exc_info=True)
            return None

    async def execute(self, agent: Any, **kwargs) -> Dict[str, Any]:
        """Executes the specified project management action."""
        if not TASKLIB_AVAILABLE:
            return self._error_result("Tasklib library not installed.")

        action = kwargs.get("action")
        project_name = kwargs.get("project_name") # Passed by InteractionHandler
        session_name = kwargs.get("session_name") # Passed by InteractionHandler

        if not action:
            return self._error_result("Missing required parameter: 'action'.")
        if not project_name or not session_name:
            return self._error_result("Missing project/session context for task management.")

        tw = self._get_taskwarrior_instance(project_name, session_name)
        if not tw:
            return self._error_result("Failed to initialize TaskWarrior backend.")

        logger.info(f"Executing ProjectManagementTool action '{action}' for {project_name}/{session_name}")

        try:
            if action == "add_task":
                description = kwargs.get("description")
                if not description:
                    return self._error_result("Missing required parameter 'description' for action 'add_task'.")

                # Extract optional parameters
                priority = kwargs.get("priority")
                project = kwargs.get("project_filter")
                tags = kwargs.get("tags")
                depends_uuid = kwargs.get("depends")
                assignee = kwargs.get("assignee_agent_id") # Get assignee

                task_data = {'description': description}
                if priority: task_data['priority'] = priority
                if project: task_data['project'] = project
                if tags and isinstance(tags, list): task_data['tags'] = tags # Tasklib handles tags as a set internally
                if assignee: task_data['assignee'] = assignee # Store assignee as custom UDA (User Defined Attribute)
                if depends_uuid:
                    try: # Tasklib expects Task objects or UUIDs for dependencies
                        depends_task = tw.tasks.get(uuid=depends_uuid)
                        if depends_task:
                             task_data['depends'] = [depends_task]
                        else:
                             logger.warning(f"Dependency task with UUID '{depends_uuid}' not found.")
                             # Decide: fail or add without dependency? Let's add without for now.
                             # return self._error_result(f"Dependency task with UUID '{depends_uuid}' not found.")
                    except Exception as dep_err:
                         logger.warning(f"Error fetching dependency task UUID '{depends_uuid}': {dep_err}")
                         # return self._error_result(f"Error fetching dependency task UUID '{depends_uuid}'.")


                new_task = Task(tw, **task_data)
                new_task.save()
                logger.info(f"Task added: {new_task['description']} (UUID: {new_task['uuid']})")
                # Return relevant info, especially the UUID
                return self._success_result({
                    "message": "Task added successfully.",
                    "task_uuid": new_task['uuid'],
                    "task_id": new_task['id'], # Also return the integer ID
                    "description": new_task['description'],
                    "assignee": new_task.get('assignee') # Return assignee if set
                })

            elif action == "list_tasks":
                # Filters
                status_filter = kwargs.get("status")
                project_filter = kwargs.get("project_filter")
                tags_filter = kwargs.get("tags")
                assignee_filter = kwargs.get("assignee_agent_id") # Filter by assignee

                # Base query based on status
                tasks_query = tw.tasks.pending() if status_filter == "pending" else \
                              tw.tasks.completed() if status_filter == "completed" else \
                              tw.tasks.deleted() if status_filter == "deleted" else \
                              tw.tasks.all() # Default to all

                # Apply standard Tasklib filters
                if project_filter:
                    tasks_query = tasks_query.filter(project=project_filter)
                # Note: Tasklib tag filtering is complex (e.g., +tag1 +tag2 for AND).
                # We'll filter tags and assignee post-query for simplicity.

                # Execute query and perform post-filtering
                tasks = tasks_query.all() # Get all matching tasks first
                if tags_filter and isinstance(tags_filter, list):
                    tasks = [t for t in tasks if t['tags'] and all(tag in t['tags'] for tag in tags_filter)]
                if assignee_filter:
                    tasks = [t for t in tasks if t.get('assignee') == assignee_filter]

                # Format results
                task_list = []
                for task in tasks: # Iterate through the already filtered tasks
                    task_list.append({
                        "id": task['id'],
                        "uuid": task['uuid'],
                        "status": task['status'],
                        "description": task['description'],
                        "priority": task.get('priority'),
                        "project": task.get('project'),
                        "tags": list(task['tags']) if task['tags'] else [],
                        "depends": [dep['uuid'] for dep in task['depends']] if task['depends'] else [],
                        "assignee": task.get('assignee') # Include assignee
                    })

                return self._success_result({
                    "message": f"Found {len(task_list)} task(s).",
                    "tasks": task_list
                })

            elif action == "modify_task":
                task_id = kwargs.get("task_id")
                if not task_id:
                    return self._error_result("Missing required parameter 'task_id' for action 'modify_task'.")

                try:
                    # Try fetching by UUID first, then by integer ID
                    try:
                        task = tw.tasks.get(uuid=task_id)
                    except: # Tasklib raises Exception if not found by UUID
                        try:
                            task = tw.tasks.get(id=int(task_id))
                        except ValueError:
                             return self._error_result(f"Invalid task_id format: '{task_id}'. Must be UUID or integer.")
                        except Exception: # Tasklib raises Exception if not found by ID
                            return self._error_result(f"Task with ID or UUID '{task_id}' not found.")
                except Exception as e: # Catch potential errors during fetch
                     logger.error(f"Error fetching task '{task_id}': {e}", exc_info=True)
                     return self._error_result(f"Error fetching task '{task_id}': {e}")


                # Apply modifications
                modified_fields = []
                if "description" in kwargs:
                    task['description'] = kwargs["description"]
                    modified_fields.append("description")
                if "status" in kwargs:
                    new_status = kwargs["status"]
                    if new_status in ['pending', 'completed', 'deleted', 'waiting']: # Add valid statuses
                         task['status'] = new_status
                         modified_fields.append("status")
                    else:
                         logger.warning(f"Invalid status '{new_status}' provided for modify_task.")
                         # Optionally return error or ignore invalid status
                if "priority" in kwargs:
                    task['priority'] = kwargs["priority"]
                    modified_fields.append("priority")
                if "project_filter" in kwargs: # Assuming project_filter is used to set project
                    task['project'] = kwargs["project_filter"]
                    modified_fields.append("project")
                if "tags" in kwargs and isinstance(kwargs["tags"], list):
                    task['tags'] = set(kwargs["tags"]) # Replace tags
                    modified_fields.append("tags")
                if "assignee_agent_id" in kwargs: # Allow modifying assignee
                    task['assignee'] = kwargs["assignee_agent_id"]
                    modified_fields.append("assignee")
                # Add dependency modification if needed later

                if not modified_fields:
                    return self._error_result("No valid fields provided to modify.")

                task.save()
                logger.info(f"Task '{task_id}' modified. Fields changed: {', '.join(modified_fields)}")
                return self._success_result({
                    "message": f"Task '{task_id}' modified successfully.",
                    "task_uuid": task['uuid'],
                    "task_id": task['id'],
                    "modified_fields": modified_fields
                })

            elif action == "complete_task":
                task_id = kwargs.get("task_id")
                if not task_id:
                    return self._error_result("Missing required parameter 'task_id' for action 'complete_task'.")

                try:
                    # Try fetching by UUID first, then by integer ID
                    try:
                        task = tw.tasks.get(uuid=task_id)
                    except:
                        try:
                            task = tw.tasks.get(id=int(task_id))
                        except ValueError:
                             return self._error_result(f"Invalid task_id format: '{task_id}'. Must be UUID or integer.")
                        except Exception:
                            return self._error_result(f"Task with ID or UUID '{task_id}' not found.")
                except Exception as e:
                     logger.error(f"Error fetching task '{task_id}' for completion: {e}", exc_info=True)
                     return self._error_result(f"Error fetching task '{task_id}' for completion: {e}")

                if task['status'] == 'completed':
                     return self._success_result({"message": f"Task '{task_id}' is already completed."})

                task.done() # This marks as completed and saves
                logger.info(f"Task '{task_id}' marked as completed.")
                return self._success_result({
                    "message": f"Task '{task_id}' marked as completed.",
                    "task_uuid": task['uuid'],
                    "task_id": task['id']
                })

            else:
                return self._error_result(f"Unknown action: '{action}'.")

        except Exception as e:
            logger.error(f"Error executing ProjectManagementTool action '{action}': {e}", exc_info=True)
            return self._error_result(f"An unexpected error occurred: {e}")

    def get_detailed_usage(self) -> str:
        """Provides detailed usage instructions for the tool."""
        # Improve this with examples for each action
        usage = f"Tool: {self.name}\nDescription: {self.description}\nParameters:\n"
        # Iterate through the ToolParameter objects
        for param in self.parameters:
            req = "Required" if param.required else "Optional"
            usage += f"  - {param.name} ({param.type}, {req}): {param.description}\n"

        usage += "\nAvailable Actions:\n"
        usage += "  - add_task: Adds a new task.\n"
        usage += "    - Required: action='add_task', description='...'.\n"
        usage += "    - Optional: priority='H/M/L', project_filter='ProjectName', tags=['tag1', 'tag2'], depends='<dependency_task_uuid>', assignee_agent_id='<agent_id>'.\n"
        usage += "  - list_tasks: Lists tasks.\n"
        usage += "    - Required: action='list_tasks'.\n"
        usage += "    - Optional Filters: status='pending|completed|deleted|all', project_filter='ProjectName', tags=['tag1', 'tag2'] (AND logic), assignee_agent_id='<agent_id>'.\n"
        usage += "  - modify_task: Modifies an existing task.\n"
        usage += "    - Required: action='modify_task', task_id='<uuid_or_id>'.\n"
        usage += "    - Optional fields to modify: description='...', status='pending|completed|deleted|waiting', priority='H/M/L', project_filter='ProjectName', tags=['tag1', 'tag2'] (replaces existing), assignee_agent_id='<agent_id>'.\n"
        usage += "  - complete_task: Marks a task as completed.\n"
        usage += "    - Required: action='complete_task', task_id='<uuid_or_id>'.\n"

        return usage

# Example usage (for testing purposes, not called directly by agents):
# async def main():
#     if not TASKLIB_AVAILABLE:
#         print("Tasklib not installed, cannot run example.")
#         return
#
#     tool = ProjectManagementTool()
#     project = "TestProject"
#     session = "TestSession123"
#
#     # Add a task
#     add_result = await tool.execute(None, action="add_task", description="Implement list_tasks action", priority="H", project_name=project, session_name=session, tags=['dev', 'tooling'])
#     print("Add Task Result:", add_result)
#
#     if add_result.get("status") == "success":
#         task_uuid = add_result.get("data", {}).get("task_uuid")
#         # Add another task depending on the first one
#         add_dep_result = await tool.execute(None, action="add_task", description="Write tests for list_tasks", project_name=project, session_name=session, depends=task_uuid)
#         print("Add Dependent Task Result:", add_dep_result)
#
#     # List tasks (when implemented)
#     # list_result = await tool.execute(None, action="list_tasks", project_name=project, session_name=session)
#     # print("List Tasks Result:", list_result)
#
# if __name__ == "__main__":
#    logging.basicConfig(level=logging.DEBUG)
#    # Ensure the projects directory exists for the example
#    (BASE_DIR / "projects").mkdir(exist_ok=True)
#    asyncio.run(main())
