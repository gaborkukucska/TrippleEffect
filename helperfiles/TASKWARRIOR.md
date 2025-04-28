<!-- # START OF FILE helperfiles/TASKWARRIOR.md -->
# Task Management with Tasklib

This project uses the `tasklib` Python library to interact with Taskwarrior data for project task management. Task data is stored in separate directories for each project session under `projects/{ProjectName}/{SessionName}/task_data/`.

## Core Usage (`src/tools/project_management.py`)

The `ProjectManagementTool` handles interactions with Taskwarrior via `tasklib`.

1.  **Initialization:**
    *   A `TaskWarrior` instance is created for the specific project/session using `tw = TaskWarrior(data_location=str(data_path))`.
    *   A minimal `.taskrc` file defining the `assignee` UDA (`uda.assignee.type=string`) is automatically created within the session's `task_data` directory if it doesn't exist.

2.  **Adding Tasks (`add_task` action):**
    *   Due to observed inconsistencies with setting UDAs directly via the CLI `add` command within `tasklib`, the current approach uses `tw.execute_command([...])` with a specific argument order.
    *   **Command Structure (Current):** `task add priority:H project:ProjectName description +project_plan +assignee_id assignee:"assignee_id"` (Includes both tag and UDA attempt).
    *   **Assignee Handling:** The `assignee_agent_id` is added both as a tag (`+pm_agent_id_123`) and as a final `assignee:"..."` argument in the CLI command. The `list_tasks` action currently extracts the assignee from the tag.
    *   The task ID is parsed from the command output.
    *   The final task state is fetched using `tw.tasks.get(id=created_task_id)` for the return value.

3.  **Listing Tasks (`list_tasks` action):**
    *   Tasks are queried using `tw.tasks.filter(...)` or `tw.tasks.all()`.
    *   Standard attributes like `priority` and `project` are retrieved using `task._data.get('priority')` and `task._data.get('project')`.
    *   **Assignee Handling:** The code iterates through the task's tags (`task['tags']`). If a tag matches the expected agent ID pattern (e.g., starts with `pm_`), that tag value is used as the `assignee` in the returned JSON data. The `assignee` UDA itself is not directly read from `_data` for the final result, although `getattr(final_task, 'assignee', None)` is used when fetching the task state after creation in `add_task`.

4.  **Modifying/Completing Tasks:**
    *   Tasks are fetched using `tw.tasks.get(uuid=...)` or `tw.tasks.get(id=...)`.
    *   Modifications are applied directly to the `Task` object attributes (e.g., `task['status'] = 'completed'`, `task['priority'] = 'M'`).
    *   Changes are saved using `task.save()`.
    *   Completion uses `task.done()`.

## Key Considerations

*   **Assignee UDA vs. Tag:** Setting the `assignee` UDA via `task add` or `task modify` using `tw.execute_command` has proven unreliable (corrupting descriptions or failing silently). The current stable method involves adding the assignee ID as a tag (`+agent_id`) during creation and extracting the assignee from this tag during listing. The `add_task` command currently includes *both* the tag and the `assignee:` argument as a test.
*   **Session-Specific Data:** All task data is isolated within the specific project/session directory.
*   **Library:** Ensure you are referencing `tasklib` documentation, not `taskw`.
