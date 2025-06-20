# START OF FILE src/tools/project_management.py
import logging
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING # Import TYPE_CHECKING

# Import tasklib safely
try:
    from tasklib import TaskWarrior, Task
    TASKLIB_AVAILABLE = True
except ImportError:
    TaskWarrior = None
    Task = None
    TASKLIB_AVAILABLE = False

# Conditionally import for type checking only to avoid runtime errors if tasklib is missing
if TYPE_CHECKING:
    from tasklib import TaskWarrior, Task

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
    auth_level: str = "pm" # PMs manage tasks
    summary: Optional[str] = "Manages project tasks (add, list, modify, complete) via Taskwarrior."
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

    def _get_taskwarrior_instance(self, project_name: str, session_name: str) -> Optional['TaskWarrior']: # Use string literal for type hint
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
            # --- NEW: Ensure a minimal .taskrc exists ---
            taskrc_path = data_path / '.taskrc'
            if not taskrc_path.exists():
                try:
                    # Define necessary UDAs
                    uda_config = """
uda.assignee.type=string
uda.assignee.label=Assignee
# Add other UDAs here if needed in the future
"""
                    with open(taskrc_path, 'w') as f:
                        f.write(uda_config.strip() + '\n')
                    logger.info(f"Created .taskrc with UDA definitions at {taskrc_path}")
                except Exception as rc_err:
                    logger.warning(f"Failed to create .taskrc with UDA definitions at {taskrc_path}: {rc_err}. Task assignment might fail.")
            # --- END MODIFIED ---
            logger.debug(f"Initializing TaskWarrior with data_location: {data_path}")
            # Override default config to point to our specific data location
            return TaskWarrior(data_location=str(data_path))
        except Exception as e:
             logger.error(f"Failed to initialize TaskWarrior at {data_path}: {e}", exc_info=True)
             return None

    # --- NEW: Helper methods for consistent results ---
    def _success_result(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Formats a successful result."""
        return {"status": "success", **data}

    def _error_result(self, message: str) -> Dict[str, Any]:
        """Formats an error result."""
        return {"status": "error", "message": message}
    # --- END NEW ---

    async def execute(self, **kwargs) -> Dict[str, Any]: # Removed agent: Any parameter
        """Executes the specified project management action."""
        agent_id = kwargs.get("agent_id", "UnknownAgent") # Get agent_id from kwargs for logging
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

                # --- Use execute_command ONLY for adding ---
                logger.debug(f"ProjectManagementTool add_task: Adding task via execute_command. Description: {description[:50]}...")
                try: # Wrap the command execution and parsing
                    add_cmd_args = ['add']
                    # Add attributes directly to command
                    if priority: add_cmd_args.append(f'priority:{priority}')
                    if project: add_cmd_args.append(f'project:{project}')
                    if assignee: add_cmd_args.append(f'assignee:{assignee}') # Add assignee UDA via command
                    if tags and isinstance(tags, list):
                        add_cmd_args.extend([f'+{tag}' for tag in tags])
                    # Handle depends? Adding dependencies via CLI add is complex, might need separate modify. Skip for now.

                    # --- User Requested CLI Add Order: priority, project, description, +tags, +assignee_tag ---
                    final_add_cmd_args = ['add']
                    if priority: final_add_cmd_args.append(f'priority:{priority}')
                    if project: final_add_cmd_args.append(f'project:{project}')

                    # Add description next
                    final_add_cmd_args.append(description)

                    # Add standard tags
                    if tags and isinstance(tags, list):
                        final_add_cmd_args.extend([f'+{tag}' for tag in tags])

                    # Add assignee as a tag AND as an assignee: argument at the end
                    if assignee:
                        final_add_cmd_args.append(f'+{assignee}') # Add as +tag
                        final_add_cmd_args.append(f'assignee:"{assignee}"') # Also add as assignee: argument

                    logger.debug(f"ProjectManagementTool: Executing command (assignee tag & arg last): task {' '.join(final_add_cmd_args)}")
                    add_output_lines = tw.execute_command(final_add_cmd_args) # Execute the constructed command
                    add_output_str = "\n".join(add_output_lines)
                    logger.debug(f"Taskwarrior 'add' command output: {add_output_str}")

                    created_task_id = None
                    # Import re if not already at the top
                    import re
                    id_match = re.search(r'Created task (\d+)\.', add_output_str)
                    if not id_match:
                        logger.error(f"Failed to parse task ID from 'add' command output: {add_output_str}")
                        return self._error_result(f"Failed to create task (could not parse ID from output). Output: {add_output_str}")

                    created_task_id = int(id_match.group(1))
                    logger.info(f"Task added via command (ID: {created_task_id}). Fetching final state...")

                    # Fetch the final task state using tasklib
                    try:
                        final_task = tw.tasks.get(id=created_task_id)
                        logger.debug(f"ProjectManagementTool: Final task state after add/modify: {final_task.export_data()}")
                        return self._success_result({
                            "message": "Task added successfully.",
                            "task_uuid": final_task['uuid'],
                            "task_id": final_task['id'],
                            "description": final_task['description'],
                            "assignee": kwargs.get("assignee_agent_id")
                        })
                    except Exception as fetch_err:
                        logger.error(f"Failed to fetch task ID {created_task_id} after add/modify: {fetch_err}", exc_info=True)
                        return self._success_result({
                            "message": f"Task added (ID: {created_task_id}), but failed to fetch final state.",
                            "task_id": created_task_id,
                            "task_uuid": None
                        })
                except Exception as cmd_err: # This except should align with the outer try
                    logger.error(f"Error during Taskwarrior command execution or parsing for add_task: {cmd_err}", exc_info=True)
                    return self._error_result(f"Failed during task add execution: {cmd_err}")
                # --- End CLI Create-then-Modify ---

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
                    # --- CORRECTED: Use ['tags'] for Tasklib ---
                    tasks = [t for t in tasks if t['tags'] and all(tag in t['tags'] for tag in tags_filter)]
                if assignee_filter:
                    # --- CORRECTED: Use ['assignee'] or getattr for Tasklib ---
                    tasks = [t for t in tasks if getattr(t, 'assignee', None) == assignee_filter]

                # Format results
                task_list = []
                for task in tasks: # Iterate through the already filtered tasks
                    # --- Extract assignee from tags ---
                    assignee_val = None
                    tags_list = list(task['tags']) if task['tags'] else []
                    # Simple check: if a tag looks like a PM agent ID, assume it's the assignee
                    # This assumes assignee tags are unique and follow the pm_... pattern
                    for tag in tags_list:
                        if tag.startswith("pm_"): # Basic check for our PM agent ID pattern
                            assignee_val = tag
                            # Optional: Remove the assignee tag from the list returned to the agent?
                            # try: tags_list.remove(tag)
                            # except ValueError: pass # Should not happen
                            break # Assume only one assignee tag per task

                    # --- Get other values using _data.get() ---
                    project_val = task._data.get('project')   # Standard key is lowercase
                    priority_val = task._data.get('priority') # Standard key is lowercase
                    logger.debug(f"Task {task['id']} raw data: {task._data}, Extracted Assignee Tag: {assignee_val}") # Log raw data and extracted tag

                    task_list.append({
                        "id": task['id'],
                        "uuid": task['uuid'],
                        "status": task['status'],
                        "description": task['description'],
                        "priority": priority_val,
                        "project": project_val,
                        "tags": tags_list, # Return the list of tags (potentially without assignee tag if removed)
                        "depends": [dep['uuid'] for dep in task['depends']] if task['depends'] else [],
                        "assignee": assignee_val # Use the value extracted from the tag
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
                intended_assignee_for_modification = kwargs.get("assignee_agent_id")
                assignee_value_for_result = None

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
                if "priority" in kwargs:
                    task['priority'] = kwargs["priority"]
                    modified_fields.append("priority")
                if "project_filter" in kwargs: # Assuming project_filter is used to set project
                    task['project'] = kwargs["project_filter"]
                    modified_fields.append("project")

                # Handle tags separately from assignee logic for now, then combine if assignee changes
                if "tags" in kwargs and isinstance(kwargs["tags"], list):
                    task['tags'] = set(kwargs["tags"]) # Replace tags
                    modified_fields.append("tags")

                if intended_assignee_for_modification:
                    task['assignee'] = intended_assignee_for_modification
                    modified_fields.append("assignee")

                    current_tags = set(task['tags'] if task['tags'] else [])
                    tags_to_remove = {
                        tag for tag in current_tags
                        if (tag.startswith('pm_') or tag.startswith('worker_') or tag.startswith('admin_ai'))
                           and tag != intended_assignee_for_modification
                    }
                    current_tags = current_tags - tags_to_remove
                    current_tags.add(intended_assignee_for_modification)
                    task['tags'] = list(current_tags)
                    if "tags" not in modified_fields: # Avoid double-adding "tags" if it was already there
                        modified_fields.append("tags") # Tags were modified due to assignee change

                    assignee_value_for_result = intended_assignee_for_modification
                else:
                    # Assignee not being actively changed by this call
                    assignee_value_for_result = getattr(task, 'assignee', None)
                    if not assignee_value_for_result and task['tags']:
                        for tag_val in task['tags']: # Corrected variable name from 'tag' to 'tag_val'
                            if isinstance(tag_val, str) and (tag_val.startswith('pm_') or tag_val.startswith('worker_') or tag_val.startswith('admin_ai')):
                                assignee_value_for_result = tag_val
                                break

                if not modified_fields and not intended_assignee_for_modification : # Check if any actual modification happened
                    # If only assignee_agent_id was passed but it's the same as current, it's not a modification
                    # Or if no modification fields were passed at all.
                    current_assignee_from_uda = getattr(task, 'assignee', None)
                    if not intended_assignee_for_modification or intended_assignee_for_modification == current_assignee_from_uda:
                         return self._error_result("No valid fields provided for modification or assignee is already set to the provided value.")


                task.save() # Save after all modifications
                logger.info(f"Task '{task_id}' modified. Fields changed: {', '.join(modified_fields)}")

                return self._success_result({
                    "message": f"Task '{task_id}' modified successfully.",
                    "task_uuid": task['uuid'],
                    "task_id": task['id'],
                    "modified_fields": modified_fields,
                    "description": task['description'],
                    "assignee": assignee_value_for_result
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
