# TrippleEffect - Current Issues

**Last Updated:** 2026-04-28
**Based on log:** `app_20260428_102543_830757.log` (SnakeyDoodle test run #3)

---

## Active Issues

### ~~+20. Missing `get_task` / `start_task` / `task_list` / `list_sub_tasks` Action Aliases~~ (RESOLVED)

- **Severity:** Low (P3)
- **Description:** Workers hallucinate action names like `get_task`, `start_task`, `task_list`, and `list_sub_tasks` for the `project_management` tool. These fail with `Invalid action` errors. Additionally, `list_sub_tasks` and `task_list` are sent as **tool names** (not action parameters), bypassing the alias system entirely.
- **Root Cause:** The `action_suggestions` map in `project_management.py` doesn't cover these variants. The tool-name-level hallucination (`list_sub_tasks` as a tool) needs to be intercepted at the `ToolExecutor` level.
- **Fix:** (RESOLVED) Added `get_task`, `get`, `task_list`, `list_sub_tasks`, `list_subtask`, `view_tasks`, `start_task`, and `begin_task` to the `action_suggestions` alias map in `project_management.py`. Tool-name-level hallucinations remain a separate, lower-priority concern.
- **Files:** `src/tools/project_management.py`

### +19. Workers Omitting `task_id` in State Transitions

- **Severity:** Medium (P2)
- **Description:** Workers repeatedly attempt `<request_state state='worker_work'/>` without the mandatory `task_id` parameter, causing 5+ rejection cycles before the LLM finally includes it. The framework error messages are correct and list available task UUIDs, but smaller models take multiple cycles to parse them.
- **Root Cause:** LLM autoregressive momentum on the 9b model.
- **Suggested Fix:** After 2 consecutive `task_id` omission rejections, auto-infer the task_id (oldest `in_progress` or `todo` task assigned to the worker) and apply it, logging a warning.
- **Files:** `src/agents/cycle_handler.py` or `src/agents/workflow_manager.py`

### +18. `command_executor` Return Code Opacity

- **Severity:** Low (P3)
- **Description:** 12 `command_executor` calls failed with return codes 1 or 2. The error message says `Command execution completed with return code 1.` but omits `stdout`/`stderr` output, making it impossible for agents to diagnose failures.
- **Root Cause:** The tool returns only the return code without the actual error output on failure.
- **Suggested Fix:** Ensure `command_executor` always includes at least the last 500 characters of `stderr` in the error response.
- **Files:** `src/tools/command_executor.py`

---

## Recently Resolved Issues

### +30. Worker Loses Assigned Task During Decompose→Work Transition
- **Severity:** Critical (P0)
- **Description:** When a worker agent (e.g., W1) entered `worker_decompose` and skipped decomposition (for simple tasks), the framework falsely concluded the worker had decomposed and stripped its task. The `tw.tasks.filter(depends=main_task)` query matched *unrelated kick-off tasks* that simply depended on the parent, not sub-tasks the worker created. The parent was then marked `decomposed` (hidden from `list_tasks`) and reassigned to PM — leaving the worker with 0 tasks and causing a stall loop.
- **Fix:** (RESOLVED) Added an assignee filter in `WorkflowManager.change_state` to only count subtasks assigned to **this specific worker** (`st.get('assignee') == agent.agent_id`) as evidence of decomposition. If no worker-owned subtasks exist, the transition is allowed without marking the parent as decomposed — the worker retains its original task. Workers who genuinely decompose still trigger the old behaviour (parent marked `decomposed`, reassigned to PM).
- **Files:** `src/agents/workflow_manager.py` (lines ~297–388)

### +29. XML Tool-Call Instructions Persisting in prompts.yaml
- **Severity:** Medium (P2)
- **Description:** Several worker and PM state prompts in `prompts.yaml` contained hardcoded XML tool-call tag examples (e.g. `<send_message>`, `<project_management>`, `<manage_team>`). When the framework operates in native JSON mode (`NATIVE_TOOL_CALLING_ENABLED=true`), agents would occasionally output XML-wrapped tool calls because the prompt examples reinforced that pattern, triggering XML parse failures.
- **Fix:** (RESOLVED) Removed all embedded XML tag examples from `prompts.yaml`. Tool references were replaced with backtick-quoted tool names (e.g. `` `send_message` ``, `` `project_management` ``), allowing the `WorkflowManager` to dynamically inject the correct format (JSON vs XML) at runtime via the standard native tool call preamble.
- **Files:** `prompts.yaml`

### +28. FileSystem Tool Errors Provide Insufficient Recovery Context
- **Severity:** Medium (P2)
- **Description:** When an agent attempted to read a non-existent file, the generic `FileNotFoundError` gave no indication of what files actually existed nearby, causing agents to retry the same path 3+ times before recovering. Similarly, when agents attempted to overwrite an existing file via `file_system` write, the error message included XML-formatted instructions for `code_editor` which clashed with native JSON mode.
- **Fix:** (RESOLVED) `src/tools/file_system.py` updated in two areas: (1) `_read_file` now immediately runs `list_directory` on the parent path and appends the directory listing to any `FileNotFoundError`, giving agents instant filesystem context. (2) `_write_file` now emits plain-text (non-XML) `code_editor` instructions with a native JSON tool call example so the agent can self-correct without format confusion.
- **Files:** `src/tools/file_system.py`

### +27. Background Process Port Locking
- **Severity:** Medium (P2)
- **Description:** Background processes (like dev servers) spawned child processes that were not being killed when the background process was terminated via `command_executor`. This caused ports to remain locked and prevented subsequent restarts of the dev server.
- **Fix:** (RESOLVED) Updated `src/tools/command_executor.py` to use `os.setsid` during subprocess creation and `os.killpg` during termination to cleanly kill the entire process tree.

### +26. Missing Tool Arguments Result in Raw Exceptions
- **Severity:** Medium (P2)
- **Description:** Agents omitting required arguments like `action` in `github_tool` or `role` in `manage_team` resulted in raw Python exceptions or generic errors, causing agent confusion.
- **Fix:** (RESOLVED) Enhanced error handling in `github_tool.py` and `manage_team.py` to explicitly specify which parameter is missing and provide actionable XML examples to correct it.

### +25. Overly Strict Code Editor Matching
- **Severity:** High (P1)
- **Description:** The `code_editor` tool's exact string matching was failing frequently because local LLMs sometimes output different indentation or add/remove empty lines, causing Tier 2 fallback to fail and breaking code edits.
- **Fix:** (RESOLVED) Updated `src/tools/code_editor.py` to use fuzzy whitespace matching by stripping lines before comparison during the Tier 2 fallback search.

### +24. PM Context Loops (Stuttering)
- **Severity:** Critical (P0)
- **Description:** PM agents occasionally became trapped in autoregressive memory loops, repeatedly calling the exact same tool and parameters even after receiving a `DUPLICATE BLOCKED` feedback message, polluting their context window.
- **Fix:** (RESOLVED) Lowered the `is_stuck_in_loop` threshold from 4 to 2 in `next_step_scheduler.py` and introduced a critical fix to truncate the agent's recent message history (removing the last 4 messages) when a loop is detected. This effectively breaks the context loop.

### +23. Missing Context in Worker Report/Wait States
- **Severity:** High (P1)
- **Description:** Workers lost task context when in `worker_report` and `worker_wait` states due to prompt injection logic omitting task descriptions, leading to amnesia loops.
- **Fix:** (RESOLVED) Relaxed context injection controls in `workflow_manager.py` to ensure `worker_report` and `worker_wait` states consistently receive `_injected_task_description`.
- **Files:** `src/agents/workflow_manager.py`

### +22. PM send_message Constraint Blocking
- **Severity:** Critical (P0)
- **Description:** PM agents attempting to update tasks and immediately notify workers in the same turn triggered a multi-tool block, preventing workers from waking up.
- **Fix:** (RESOLVED) Exempted Project Managers (`pm`) from the multi-tool isolation constraint in `cycle_handler.py`. PMs can now seamlessly pair tool actions with `send_message`.
- **Files:** `src/agents/cycle_handler.py`

### +21. Task Tag Auto-Assignment Disconnect
- **Severity:** Medium (P2)
- **Description:** When agents assigned a task, they often failed to explicitly set mapping tags (e.g. `assigned` or `+agent_id`), rendering the tasks invisible to specific worker queues.
- **Fix:** (RESOLVED) Updated `project_management.py` mapping logic. When `assignee_agent_id` parameter is specified, the tool now unilaterally adds the `assigned` tag and worker's ID to the TaskWarrior record behind-the-scenes.
- **Files:** `src/tools/project_management.py`

### +20b. Cross-Agent Situational Blindness
- **Severity:** High (P1)
- **Description:** Agents execute strictly partitioned actions without a shared awareness of other current worker statuses, causing task duplication or timeline confusion.
- **Fix:** (RESOLVED) Integrated dynamic Team Work In Progress (WIP) updates. `_build_team_wip_updates()` constructs real-time worker state/activity context, automatically injected directly into all active interaction prompts via `prompts.yaml` and `settings.py`.
- **Files:** `prompts.yaml`, `src/config/settings.py`, `src/agents/workflow_manager.py`

### +20a. Tool Execution MISSING_PARAMETER Raw Errors
- **Severity:** Medium (P2)
- **Description:** Missing tool parameters caused raw python exceptions, making it hard for agents to self-correct.
- **Fix:** (RESOLVED) Integrated the `ErrorType.MISSING_PARAMETER` into the global `tool_error_handler` inside `executor.py` to automatically supply an LLM-friendly context recovery message for parameter corrections.
- **Files:** `src/tools/executor.py`

### +16. Whiteboard Context Bloating (Deprecation)

- **Severity:** High (P1)
- **Description:** Global `whiteboard.md` system scaled improperly, causing extreme context bloat as simple progress entries accumulated into thousands of lines.
- **Root Cause:** Flat-file global context scaling.
- **Fix:** (RESOLVED) Deprecated `whiteboard.md` entirely in favor of vectorizing project context into the scalable SQLite `knowledge_base` tool using the `search_knowledge` tool alias routing. Prompt structures mapped over dynamically.
- **Files:** `prompts.yaml`, `src/config/settings.py`, `src/tools/knowledge_base.py`, `src/agents/workflow_manager.py`, `src/agents/manager.py`

### +15. Empty Response / Watchdog Context Accumulation Loop 

- **Severity:** Critical (P0)
- **Description:** Small local LLMs regularly generate empty responses when confused or overwhelmed. Every empty response triggered a `[Framework Watchdog]` intervention injected into history.
- **Root Cause:** No hard-clearing or summarization bounds existed for watchdog warnings.
- **Fix:** (RESOLVED) Implemented strict pruning in `AgentHealthMonitor._execute_recovery_action`. The system now completely strips old `[Framework Watchdog]` blocks and trims other warnings when thresholds are hit to prevent the "wall of warnings" loop.
- **Files:** `src/agents/cycle_components/agent_health_monitor.py`

### +14. PM Audit State (pm_audit) Stalling

- **Severity:** Critical (P0)
- **Description:** The PM transitioned to `pm_audit` three times, spending 4.5 hours in total spinning in Watchdog-reschedule loops.
- **Root Cause:** The `pm_audit` system prompt lacked a concrete, actionable loop mechanism (no step-by-step checklist).
- **Fix:** (RESOLVED) Restructured the `pm_audit` prompt into a strict 3-step concrete flow. Added a hard cycle limit (5 cycles) to `next_step_scheduler.py` before forcing a transition to `pm_standby`.
- **Files:** `src/config/settings.py`, `src/agents/cycle_components/next_step_scheduler.py`

### +13. PM Standby ↔ Manage Micro-Oscillation

- **Severity:** High (P1)
- **Description:** The PM wasted massive compute toggling between `pm_standby` and `pm_manage` continuously when workers were active.
- **Root Cause:** Naive timer-based waking rules lacking exponential backoff.
- **Fix:** (RESOLVED) Re-written PM sleep/standby logic in `manager.py`. The PM now uses an exponentially decaying backoff curve based on state triggers and remains asleep unless hard events occur.
- **Files:** `src/agents/manager.py`

### +12. `send_message` Blocked in WORK State

- **Severity:** High (P1)
- **Description:** Workers attempted to use `send_message` while in `worker_work` state. The framework blocked this causing errors and confusion loops.
- **Fix:** (RESOLVED) Removed the strict worker_work tool blocking rule from `cycle_handler.py`, enabling native communication via `send_message` at all worker stages.
- **Files:** `src/agents/cycle_handler.py`

### +11. Cross-Cycle Tool Duplicate Loop Recovery Failure

- **Severity:** High (P1)
- **Description:** Agents attempting duplicate read tool calls were given generic blocks, causing them to "forget" context and repeatedly stall trying to fetch it.
- **Fix:** (RESOLVED) Implemented dynamic string preservation in `cycle_handler.py`. By extending the maximum string truncation limit to 4,000 characters for read-only tools, agents get their missing context re-injected gracefully without stalling out.
- **Files:** `src/agents/cycle_handler.py`

### +10. PM Initial Task Assignment Loop & Filter Parsing

- **Severity:** High (PM mistakenly tried to re-assign or work on the root system task)
- **Description:** After a project was kicked off and successfully decomposed by the PM, the PM's task list still showed the initial root system task (`PROJECT KICK-OFF: ...`). This caused the PM to get confused and hallucinate work loops.
- **Root Cause:**
  1. `PMKickoffWorkflow` used `tags` instead of `tags_filter` during the `list_tasks` phase of its cleanup routine, causing the query to return all tasks instead of specifically the root task. This caused the cleanup routine to quietly fail, leaving the task in the `pending` state permanently.
  2. The PM's subsequent `list_tasks` attempt failed to automatically exclude the pending initial task due to formatting anomalies.
- **Fix:** (RESOLVED)
  1. Refactored the kickoff script to rigorously target `"tags_filter": "project_kickoff,auto_created_by_framework"` so the initial task is correctly retrieved and marked `.done()`.
  2. Enhanced `project_management.py` with a tolerant fallback that automatically maps python list `tags: [...]` arguments provided by the LLM into well-formatted `tags_filter` strings upon query execution.
- **Files:** `src/workflows/pm_kickoff_workflow.py`, `src/tools/project_management.py`

### +9. UI Task Visibility Synchronization

- **Severity:** Moderate (UI data stale without hard refresh)
- **Description:** While workers correctly transitioned their assigned Taskwarrior backend tasks to `doing` through state logic when entering `worker_work`, the frontend UI's "Active Tasks" column was not notified of this progress change, remaining empty.
- **Root Cause:** The `CycleHandler`'s gatekeeping verification routine for task transitions correctly performed database writes but lacked an event-hook to push an update back to the React UI context.
- **Fix:** (RESOLVED) `_handle_worker_task_tracking` specifically fires `await broadcast(json.dumps({"type": "project_tasks_updated"...}))` natively whenever a worker task's progress shifts into the `doing` phase in real-time. Native tools and Cycle handlers now strictly strip and respect the `task_id` argument to enforce tracking. Additionally, generalized `.get()` attribute fetches across `tasklib` querysets were mapped over to safer array/dictionary index calls.
- **Files:** `src/agents/cycle_handler.py`, `src/agents/core.py`, `src/agents/cycle_components/prompt_assembler.py`, `src/agents/workflow_manager.py`

### +8. Message Read/Acknowledgement System (BUG-2)

- **Severity:** High (stale context accumulation, missed instructions)
- **Description:** Agents had no way to acknowledge received messages, leading to stale messages persisting in the context window across cycles. Workers could also miss critical PM instructions buried in old history.
- **Root Cause:** No message lifecycle tracking — once delivered, messages stayed in history indefinitely.
- **Fix:** (RESOLVED) Implemented a complete message read/ack pipeline:
  1. `mark_message_read` tool created for explicit agent acknowledgement.
  2. Unique `message_id` injected into every routed message via `interaction_handler.py`.
  3. `Agent.read_message_ids` set tracks acknowledged messages in `core.py`.
  4. `cycle_handler.py` updates the set when `mark_message_read` tool succeeds.
  5. `prompt_assembler.py` filters read messages from history before LLM call.
  6. `prompt_assembler.py` injects ack instructions for agents with unread messages.
  7. Report-state safety check warns workers of unread messages before they report to PM.
- **Files:** `src/tools/mark_message_read.py` (NEW), `src/agents/core.py`, `src/agents/interaction_handler.py`, `src/agents/cycle_handler.py`, `src/agents/cycle_components/prompt_assembler.py`

### +7. CG Intervention Upper Limit & PM Escalation (BUG-4)

- **Severity:** High (workers looping indefinitely under CG corrections)
- **Description:** Constitutional Guardian interventions lacked actionable feedback for workers and didn't notify supervisors. Workers could loop forever under CG corrections without the PM being aware.
- **Root Cause:** CG guidance was generic, and no escalation path existed for repeated interventions.
- **Fix:** (RESOLVED)
  1. Upper limit: After 3 CG interventions in 10 minutes, workers are forced to `worker_wait`.
  2. PM escalation: PM receives a `🚨 CRITICAL ESCALATION` message via `interaction_handler`.
  3. Worker-specific empty response guidance now includes concrete tool examples and XML state transitions.
  4. UI notification broadcast for `forced_wait_state` events.
- **Files:** `src/agents/cycle_components/agent_health_monitor.py`

### +6. Stuck Worker State Progression Guidance (BUG-5)

- **Severity:** Moderate (workers stuck in `worker_work` received vague guidance)
- **Description:** Workers stuck in `worker_work` received generic guidance without concrete state transition instructions, causing them to remain stuck.
- **Fix:** (RESOLVED) Added explicit `<request_state state='worker_report'/>` and `<request_state state='worker_wait'/>` XML examples to CG state progression guidance, giving workers three clear options: report to PM, wait, or take concrete action with a tool.
- **Files:** `src/agents/cycle_components/agent_health_monitor.py`

### +5. PM send_message Multi-Tool Loop (BUG-1)

- **Severity:** High (infinite retry loop)
- **Description:** When the PM used `send_message` alongside other tools, the framework blocked the message entirely, causing infinite retry loops. The PM would re-attempt the same multi-tool combination indefinitely.
- **Root Cause:** The multi-tool constraint treated all `send_message` combos equally without considering that `send_message` + state change is a valid and common pattern (e.g., reporting then transitioning).
- **Fix:** (RESOLVED)
  1. `send_message` + `request_state` combos are now permitted (both execute).
  2. Other multi-tool combos: non-message tools execute, `send_message` returns an instructive error.
  3. Circuit breaker: After 3 consecutive multi-tool errors, the framework auto-executes `send_message`.
- **Files:** `src/agents/cycle_handler.py`

### +4. Failover Model Pre-Filtering for Tool Support (BUG-3)

- **Severity:** Moderate (failover selected models that couldn't handle tools)
- **Description:** Failover selected models with RAW templates (`{{ .Prompt }}`) that lack tool-calling support, causing cascading failures when agents needed tools.
- **Fix:** (RESOLVED) Added blacklist check for models whose Ollama templates contain `{{ .Prompt }}` (RAW indicator). These are excluded from failover candidate lists before selection.
- **Files:** `src/agents/failover_handler.py`

### +3. Invalid Action Alias `update_project_status` (BUG-6)

- **Severity:** Low (tool execution failure, agent self-corrects)
- **Description:** LLMs generated `update_project_status` instead of the valid `modify_task` action, causing tool execution failure.
- **Fix:** (RESOLVED) Added `"update_project_status": "modify_task"` to the alias mapping dictionary in `project_management.py`.
- **Files:** `src/tools/project_management.py`

### +2. Shared Workspace Tree Context Explosion

- **Severity:** Critical (75MB logs, 15-second cycles)
- **Description:** When an agent initialized a Node.js project, the `[SHARED WORKSPACE TREE]` recursively mapped every sub-folder in `node_modules`. This bloated the LLM system prompt string with 30,000+ files, destroying context limits and grinding local models to a halt.
- **Root Cause:** `PromptAssembler` used `os.walk` without pruning deep dependency paths or limiting total files.
- **Fix:** (RESOLVED) Hard-capped the tree generation to `MAX_DEPTH = 4` and `MAX_FILES = 200`. Added an `EXCLUDE_DIRS` blacklist (`node_modules`, `.git`, `.venv`) to instantly prune traversal.
- **Files:** `src/agents/cycle_components/prompt_assembler.py`

### +4. PM Audit State Stalling

- **Severity:** High (PM completely stalled out of work loops)
- **Description:** When the PM agent transitioned into the `pm_audit` state, the `NextStepScheduler` failed to recognize it as a continuous persistent state. After executing a tool, it dropped the PM into `IDLE` status on `Path E - Default End`, waiting indefinitely.
- **Root Cause:** `PM_STATE_AUDIT` was missing from `persistent_states` in `NextStepScheduler`.
- **Fix:** (RESOLVED) Appended `PM_STATE_AUDIT` to persistent handling rules so the PM is constantly reactivated during its audit phase.
- **Files:** `src/agents/cycle_components/next_step_scheduler.py`

### +3. PM Hallucinating Aliases resolving to Duplicate Agent Bypasses

- **Severity:** High (wasted LLM generation, duplicate worker bloat)
- **Description:** Because the PM occasionally omitted the `team_id` when calling `manage_team(action="create_agent")`, the previous fix using `state_manager.get_agents_in_team(creator_team_id)` returned `None`, entirely bypassing the validation filter and permitting identical redundant worker agent roles to be created.
- **Fix:** (RESOLVED) Patched `_handle_create_agent` to default its redundancy verification array to `list(self._manager.agents.values())` globally if local `creator_team_id` is unattainable.
- **Files:** `src/agents/interaction_handler.py`, `src/agents/cycle_handler.py`

### +1. PM Token Bloat on Unfiltered `list_tasks`

- **Severity:** High (wasted context on delegated tasks)
- **Description:** The PM often requested `list_tasks` without any filters, forcing the framework to dump the entire project's task matrix into the prompt, eating up thousands of tokens for tasks already being worked on.
- **Fix:** (RESOLVED) Injected an overriding `assignee_filter="unassigned"` directly into `ProjectManagementTool.execute`. If the PM attempts a global search, it receives a targeted list of only unassigned tasks, forcing better efficiency.
- **Files:** `src/tools/project_management.py`

### -16. Admin AI Passive Oversight Limitation

- **Severity:** High (Admin AI functioned merely as a message relay)
- **Description:** When users asked for project updates, the Admin AI passively messaged the Project Manager. Furthermore, it was explicitly banned from using management tools while a project was delegated, preventing it from detecting stalled projects or verifying success.
- **Root Cause:** System prompts restricted native tool usage and assumed the PM was the sole source of truth.
- **Fix:** (RESOLVED) Upgraded the Admin AI to the "Ultimate Orchestrator." Fully rewrote the `admin_work` state into an autonomous auditing hub where it uses `list_tasks` and `list_agents` to verify progress. Given proactive sweeping capabilities in the delegated state.
- **Files:** `prompts.yaml`, `src/config/settings.py`

### -15. Coarse Task Status & Watchdog Isolation

- **Severity:** High (PMs lacked visibility into granular task blockers)
- **Description:** Taskwarrior's native `status` (pending/completed) was insufficient for LLMs to understand *why* a task was pending (e.g., `stuck`, `failed`, `in_progress`). Additionally, the framework's periodic check was isolated to just PMs.
- **Root Cause:** Strict adherence to Taskwarrior defaults and localized watchdogs.
- **Fix:** (RESOLVED) Integrated a custom `task_progress` tracking system (`todo`, `in_progress`, `waiting`, `stuck`, `failed`, `finished`). Agents now only interface with `task_progress`. Promoted the PM check to a `_universal_framework_watchdog` checking all agents.
- **Files:** `src/tools/project_management.py`, `src/agents/manager.py`, `prompts.yaml`

### -14. Project Manager Pre-mature Completion & Audit Phase

- **Severity:** High (PM declared completion without formal review)
- **Description:** Upon task exhaustion, PMs immediately messaged the Admin AI that the project was complete without verifying the generated output against instructions, leading to unverified deliverables and communication disconnects at the end of runs.
- **Root Cause:** The `pm_manage` state abruptly transitioned to reporting completion.
- **Fix:** (RESOLVED) Implemented a new `pm_audit` workflow state. PMs now automatically transition to audit phase upon believing the project is complete. They are instructed to review structural requirements, scan the codebase, and optionally run tests before compiling a final Audit Report and sending it to the Admin AI.
- **Files:** `prompts.yaml`, `src/agents/constants.py`, `src/agents/workflow_manager.py`, `src/agents/core.py`

### -13. Autoregressive Stalling & Empty Loops

- **Severity:** High
- **Description:** Worker agents and PMs would occasionally get stuck in loops generating empty responses or invalid commands. The Constitutional Guardian (CG) would passively block them, but the agents' managers were unaware of the freeze.
- **Root Cause:** Disconnected escalation path. Managers had no framework visibility into a worker's systemic internal stall.
- **Fix:** (RESOLVED) Built a proactive escalation mechanism into the CG (`AgentHealthMonitor`). When an agent crosses the stall threshold, the CG dynamically constructs a diagnostic report with history and errors, injecting it directly into the supervising agent's `message_inbox` via `interaction_handler`, prompting the supervisor to deploy Human-in-the-Loop style recovery via `send_message`.
- **Files:** `src/agents/cycle_components/agent_health_monitor.py`, `prompts.yaml`

### -12. Symmetrical Cross-Agent Message Queuing

- **Severity:** High (caused cognitive interrupts and logic stalls)
- **Description:** Agents were violently interrupted mid-thought by forced state transitions upon sending or receiving messages (e.g., workers forced into `worker_wait` upon sending, PM forced into `pm_report_check` upon receiving). This corrupted their context and resulted in loops.
- **Root Cause:** Hardcoded state switches in `route_and_activate_agent_message` and `execute_single_tool`.
- **Fix:** (RESOLVED) Implemented a strict, fully non-interruptive async queuing architecture. All messages go to the recipient's `message_inbox` unless they are already asleep. Sender's inboxes are automatically flushed into their active history concurrently with outbound dispatches.
- **Files:** `src/agents/interaction_handler.py`

### -11. Workspace Project & Session Path Nesting Bug

- **Severity:** High (sandbox fragmentation)
- **Description:** Agents were erroneously instructed to (and succeeded in) prepending `{session_name}` or `{project_name}` to folder paths, creating nested folder architectures instead of using the flat `shared_workspace` root.
- **Root Cause:** `file_system.py` didn't scrub session prefixes, and the PM's system prompt lacked constraints preventing it from passing bad pathing rules to workers.
- **Fix:** (RESOLVED) Hardened `FileSystemTool._resolve_and_validate_path()` to auto-strip `{session_name}` strings. Added an explicit `[WORKSPACE & FILE SYSTEM]` directive to `pm_standard_framework_instructions` forbidding nested paths.
- **Files:** `src/tools/file_system.py`, `src/config/settings.py`

### -10. Project Manager State Reversion Cutoff

- **Severity:** High (PM ignored worker replies)
- **Description:** If PM1 responded to a worker and executed the `send_message` tool successfully without manually appending a `<request_state>` tag, the `NextStepScheduler` forcefully reverted its state to `pm_manage`, causing it to drop its `pm_report_check` context.
- **Root Cause:** Aggressive auto-reversion logic for PMs.
- **Fix:** (RESOLVED) Updated `NextStepScheduler` to allow PMs to remain in their active interaction states (`pm_manage` or `pm_report_check`) on a successful tool call, properly scheduling a continuation cycle.
- **Files:** `src/agents/cycle_components/next_step_scheduler.py`

### -9. Task Object AttributeError on Modify

- **Severity:** Moderate
- **Description:** The PM triggered an `'Task' object has no attribute 'get'` error when calling `modify_task` to set a task status.
- **Root Cause:** `tasklib.Task` uses dictionary element access `task['status']` rather than `.get()`.
- **Fix:** (RESOLVED) Switched to `task['status']` to safely look up task data.
- **Files:** `src/tools/project_management.py`

### -7. Failover Cascade Death from Models Lacking Tool Support

- **Severity:** Critical (killed worker agent W1 entirely)
- **Description:** When W1 failed on `ollama-local-192-168-0-22` (500 error), the failover handler switched to `ollama-local-192-168-0-24` with the same model (`qwen3.5:9b-q4_K_M`). That host returned 400: "does not support tools". Failover then tried `qwen3-coder:30b` on .22 — also "does not support tools". W1 produced 4 consecutive empty responses and was CG-BLOCKED.
- **Root Cause:** The failover handler checked provider health and model availability but did NOT validate whether the target model supported native tool calling on the new host.
- **Fix:** (RESOLVED) Added a module-level `_models_without_tool_support` runtime blacklist in `failover_handler.py`. On "does not support tools" errors, the `(provider, model)` pair is added to the blacklist. Both Pass 1 (preferred model on alternate APIs) and Pass 2 (alternative models) check the blacklist before attempting a switch.
- **Files:** `src/agents/failover_handler.py`

### -6.5. Duplicate Tool Call Cached Result Context Bloat

- **Severity:** Moderate (wastes context tokens across 910+ duplicate detections)
- **Description:** When the framework detected a cross-cycle duplicate tool call, it returned the full cached result to the agent's history each time, even though the agent had already received and processed this result previously. Over a 2h47m run, this contributed to unnecessary context window consumption.
- **Root Cause:** The cached `prev_result` was injected into `all_tool_results_for_history` at full length on every duplicate detection.
- **Fix:** (RESOLVED) Truncated cached duplicate results to 200 characters max, with a `[TRUNCATED - duplicate call]` suffix. Full results are still logged to DB for debugging.
- **Files:** `src/agents/cycle_handler.py`

### -6. `mark_completed` Action Hallucination

- **Severity:** Moderate (causes tool execution failure)
- **Description:** Worker W1 used `<action>mark_completed</action>` instead of the correct `<action>complete_task</action>` to finish tasks. The existing alias map had `mark_complete` → `complete_task` but not `mark_completed` (with trailing 'd').
- **Root Cause:** LLM-generated action name variation not covered by the auto-correction map.
- **Fix:** (RESOLVED) Added `mark_completed`, `complete`, and `task_complete` to the `action_suggestions` alias map in `project_management.py`.
- **Files:** `src/tools/project_management.py`

### -5.5. File Append Missing Filename Parameter

- **Severity:** Low (clear error message, agent self-corrects)
- **Description:** Worker W1 called `file_system` with `action=append` but omitted the required `filename` parameter twice (~20:16-20:18 UTC).
- **Root Cause:** Native JSON tool calling model generated the call without the `filename` field.
- **Status:** Already handled by existing validation at line 348 of `file_system.py`. The error message clearly states `'filename' parameter is required for 'append'`.
- **Files:** `src/tools/file_system.py` (no change needed)

### -5. Constitutional Guardian Returns Empty Verdict

- **Severity:** High (previously classified Low — actual impact was severe)
- **Description:** The CG frequently returned empty verdicts, causing it to fail open and bypass governance checks entirely. In the latest log, this occurred on nearly every CG call.
- **Root Cause:** The `max_tokens_for_verdict` was hardcoded to `250` in `cycle_handler.py`. Models using `<think>` blocks (e.g., Qwen3 reasoning variants) exhausted the 250-token limit on internal reasoning before outputting the final `<OK/>` or `<CONCERN>` verdict, resulting in empty `content` fields.
- **Fix:** (RESOLVED) Replaced the hardcoded limit with a configurable `CG_MAX_TOKENS` setting (default `4000`), sourced from `settings.py` via `getattr(settings, 'CG_MAX_TOKENS', 4000)`.
- **Files:** `src/agents/cycle_handler.py`, `src/config/settings.py`

### -4.5. False-Positive Stuck-State CG Interventions

- **Severity:** High (caused repeated unnecessary CG interventions for productive agents)
- **Description:** The Constitutional Guardian's `AgentHealthMonitor` falsely flagged workers (W1, W2, W3) in `worker_work` and the PM (PM1) in `pm_manage` as "stuck" even while they were actively executing tools (web searches, file writes, task management). W1/W3 hit 17 cycles, W2 hit 12 cycles, PM1 hit 19→22+ cycles, all triggering repeated CG interventions that polluted agent history with system messages.
- **Root Cause:** `cycle_count_in_current_state` in `AgentHealthRecord.record_response()` only reset when the agent's state *changed*. It did not reset when the agent took meaningful action (successful tool calls). This meant agents legitimately staying in their working state (doing real productive work) accumulated cycles and hit the `stuck_state_threshold` (default: 6).
- **Fix:** (RESOLVED) Added an `elif has_action` branch in `record_response()` that resets `cycle_count_in_current_state = 1` whenever the agent successfully executes tools, so productive agents are not falsely flagged. Truly stuck agents (no tool calls, no meaningful output) are still caught.
- **Files:** `src/agents/cycle_components/agent_health_monitor.py`

### -4. Ollama Tool Response XML Hallucinations (Stall)

- **Severity:** High
- **Description:** Workers (specifically Qwen 3.5 via Ollama) got stuck in an infinite loop outputting `<tool_response name='web_search'>` text instead of invoking tools.
- **Root Cause:** The `OllamaProvider` was wrapping tool execution results in `<tool_response>` XML tags. Seeing these XML formatting tags in its message history tricked the autoregressive Qwen model into continuously generating the exact same tags itself.
- **Fix:** (RESOLVED) Updated `src/llm_providers/ollama_provider.py` to strip the XML `<tool_response>` wrapper and replace it with a plain-text Markdown equivalent (`--- Tool Response (name) ---`).
- **Files:** `src/llm_providers/ollama_provider.py`

### -3. Workspace Path Project Nesting Bug

- **Severity:** High
- **Description:** Worker agents incorrectly resolved the `ProjectName` internally as a sub-directory component of the `WorkspacePath`, nesting their projects incorrectly.
- **Root Cause:** The `file_system` tool was exclusively stripping the internal project name from the *beginning* of tool paths rather than safely mapping paths to the global workspace root boundary constraint.
- **Fix:** (RESOLVED) Hardened `FileSystemTool._resolve_path()` to treat the `WorkspacePath` as the absolute isolated root regardless of whether the LLM path includes the project name prefix alias. Added validation to prevent sandbox escape.
- **Files:** `src/tools/file_system.py`

### -2.5. PM/Worker Context Bloat on `list_tasks`

- **Severity:** Moderate
- **Description:** `list_tasks` output was enormous, returning full schema objects and threatening token limits. Workers lacked a way to easily filter out tasks unassigned to them.
- **Root Cause:** Raw Taskwarrior models being JSON dumped into the agent history.
- **Fix:** (RESOLVED) Severely trimmed returned fields to `uuid`, `description`, `status`, `assignee`, and `depends`. Added an `assignee_filter` parameter. If a Worker calls `list_tasks` without a filter, the system automatically forces it to filter by their agent ID. Additionally, `list_tasks` now defaults to `.pending()` when no `status_filter` is provided, automatically excluding completed/deleted tasks to further reduce token consumption.
- **Files:** `src/tools/project_management.py`

### -2. Worker Inactivity (Empty `worker_work` responses)

- **Severity:** High
- **Description:** Workers were transitioning to `worker_work` but producing empty responses (0 tokens) leading to endless idle states.
- **Root Cause:** Two interconnected bugs: 1) Initializing a brand new worker agent missed appending the `[Framework Directive]` task assignment prompt if the worker had no prior history. 2) When utilizing `<request_state>`, the framework updated the state but failed to inject a `user` message confirming the transition, meaning the LLM received a context ending with an `assistant` tag.
- **Fix:** (RESOLVED) Aligned indentation in `activate_worker_with_task_details` to reliably append the activation message. Added a `[System State Change]` user injection in `cycle_handler.py` to prompt the LLM to resume activity within its newly generated system prompt context.
- **Files:** `src/agents/manager.py`, `src/agents/cycle_handler.py`

### -1. PM Interrupting Worker Decomposition (Decompose Loop)

- **Severity:** High
- **Description:** Workers would get stuck in the `worker_decompose` state, constantly having their context wiped and replaced with new task assignments from the PM before they could finish breaking down their first task.
- **Root Cause:** The `WORKER_STATE_DECOMPOSE` state was missing from the "busy states" check in `manager.py`. The PM eagerly assigned multiple tasks sequentially, forcibly reactivating the worker and destroying its short-term memory each time.
- **Fix:** (RESOLVED) Added `WORKER_STATE_DECOMPOSE` to the list of busy states in `activate_worker_with_task_details`. Incoming tasks are now properly routed to the worker's `message_inbox` until decomposition is complete.
- **Files:** `src/agents/manager.py`

### 0. `insert_lines` Crashes with `'str' - 'int'` TypeError

- **Severity:** High (causes tool execution failure and blocks worker progress)
- **Description:** The `insert_lines` action in `FileSystemTool` crashed with `unsupported operand type(s) for -: 'str' and 'int'` inside `_insert_lines_in_file`. This occurred 3 times in `startup_1774068944`, blocking Worker W1 from editing `package.json`.
- **Root Cause:** Three compounding issues:
  1. The `position` kwarg (e.g., `<position>after</position>`) was included in the `_fo()` alias chain for `insert_line`, causing non-numeric strings like `"after"` to leak through as the line number value.
  2. The internal `_insert_lines_in_file` method trusted its caller to pass an `int` and had no defensive conversion.
  3. LLMs sometimes used `<action>insert_line</action>` (singular) which was not recognized as an alias.
- **Fix:** (RESOLVED)
  1. Removed `kwargs.get("position")` from the `insert_line` alias chain — `position` is now treated as a semantic hint ("before"/"after") only.
  2. Added defensive `int()` conversion inside `_insert_lines_in_file` with a graceful error return.
  3. Added `insert_line` → `insert_lines` and `replace_line` → `replace_lines` to the action alias map.
  4. Properly routed the `position` kwarg as `position_hint` for search-based insertion.
- **Files:** `src/tools/file_system.py`

### 0.5. `search_replace_block` Skips Exact Matches

- **Severity:** High
- **Description:** The `search_replace_block` tool was consistently failing to find matches even when the provided block was exactly present in the file.
- **Root Cause:** A critical indentation error in `src/tools/file_system.py`. The "Tier 1: Exact substring match" logic was indented 4 spaces too far, placing it inside an `if search_block is None:` block that immediately returned `False`. Thus, exact matches were completely unreachable dead code, forcing the tool to fall back to fuzzy/first-last matching which often failed or corrupted blocks.
- **Fix:** (RESOLVED) Dedented the Tier 1 exact match block by 4 spaces so it correctly executes when `search_block` is provided, restoring reliable exact replace functionality.
- **Files:** `src/tools/file_system.py`

### 1. `send_message` Tool Isolation Violations

- **Severity:** High
- **Description:** Workers and PM frequently misused the `send_message` tool by including it in the same response as file system or task management tools, causing parser errors and xml validation failures.
- **Root Cause:** Prompt instructions were insufficient to force single-tool use for messages when agents felt they needed to report progress immediately after saving a file.
- **Fix:** (RESOLVED) Implemented structural state isolation. Added a new `WORKER_STATE_REPORT` state for workers to explicitly report progress, removing `send_message` from the `work` state completely. Added `PM_STATE_REPORT_CHECK` to give the PM a focused context for responding to worker messages without getting distracted by overarching management tasks. Integrated an auto-switch mechanism in `interaction_handler.py`.
- **Files:** `src/agents/constants.py`, `src/agents/workflow_manager.py`, `src/agents/cycle_handler.py`, `src/agents/interaction_handler.py`, `prompts.json`

### 2. PM Context Disrupted in High-Focus States

- **Severity:** High
- **Description:** Workers occasionally send progress reports or questions while the PM is actively building the project plan, assembling the team, or assigning kick-off tasks. This sudden context shift confuses the PM and disrupts the core setup workflow.
- **Root Cause:** The `interaction_handler` delivered messages directly into the PM's immediate history regardless of the PM's current phase of work, injecting irrelevant information.
- **Fix:** (RESOLVED) Implemented message queuing. `src/agents/core.py`'s `Agent` class has been given a `message_inbox`. `interaction_handler.py` intercepts messages arriving while the PM is in `pm_startup`, `pm_build_team_tasks`, `pm_activate_workers`, or `pm_work` and delays them. `workflow_manager.py` checks the inbox when the PM transitions back into a safe state (`pm_manage`, `pm_standby`) and injects the queued messages.
- **Files:** `src/agents/core.py`, `src/agents/interaction_handler.py`, `src/agents/workflow_manager.py`

### 2.5. Worker Agent Preemptive Activation During Report Phase

- **Severity:** High
- **Description:** When a worker is in the `WORKER_STATE_REPORT` state (e.g. creating a progress report to send to the PM), if the PM happens to assign them a new task, the framework automatically forces their state to `WORKER_STATE_WORK`. This causes the worker to suddenly change behavior mid-cycle and produce a normal work response (e.g. attempting to do work instead of finishing the report).
- **Root Cause:** `activate_worker_with_task_details` in `manager.py` unconditionally changed the worker's state and injected the new task directive into its active history, disrupting the internal report context.
- **Fix:** (RESOLVED) Implemented an activation block in `activate_worker_with_task_details` if the worker is in `WORKER_STATE_REPORT`. The activation framework directive is instead pushed to the `message_inbox`. `workflow_manager.py` was then updated to flush worker's inbox when transitioning into `WORKER_STATE_WAIT` or `WORKER_STATE_WORK`. Lastly, the wait/startup prompts in `prompts.json` were modified to explicitly tell workers to request `worker_work` state when reading these directives.
- **Files:** `src/agents/manager.py`, `src/agents/workflow_manager.py`, `prompts.json`

### 3. Worker Drops Out of Reporting State Early

- **Severity:** High
- **Description:** When a worker outputs `<request_state state='worker_report'/>` alongside other tool calls (like task modification or file writing) and the tools resolve or fail, the cycle terminates instead of restarting the worker in the new `worker_report` state.
- **Root Cause:** In `cycle_handler.py`, an embedded state change correctly triggered the transition but forgot to set the `needs_reactivation_after_cycle` flag. Furthermore, `worker_report` was not added to the `persistent_states` list in `next_step_scheduler.py`.
- **Fix:** (RESOLVED) Assigned `context.needs_reactivation_after_cycle = True` when parsing embedded state changes, and added `WORKER_STATE_REPORT` to the `persistent_states` tuple in the scheduler.
- **Files:** `src/agents/cycle_components/next_step_scheduler.py`, `src/agents/cycle_handler.py`

### 4. PM Repeats Task Assignments in `activate_workers`

- **Severity:** High
- **Description:** The PM loops and re-assigns the first task continuously after all tasks have technically been assigned to workers.
- **Root Cause:** When the PM used `modify_task` to assign a task, it sometimes used the task's text description or short integer ID instead of the full UUID. While TaskWarrior accepts this, the `unassigned_tasks_summary` logic in `cycle_handler.py` performed a strict string comparison against the framework's stored UUIDs. When the match failed, the assigned task was never removed from the "remaining tasks" summary. The PM saw the first task persisting in its list of unassigned tasks and attempted to reassign it recursively.
- **Fix:** (RESOLVED) Updated `cycle_handler.py` to parse the definitive `task_uuid` returned within the `modify_task` tool execution result. This guarantees that the correct UUID is used to drop the task from the internal tracker, regardless of whether the LLM queried it via ID, description, or UUID.
- **Files:** `src/agents/cycle_handler.py`

### 5. PM Stalls in `list_tasks` Loop

- **Severity:** High
- **Description:** The PM repeatedly calls `list_tasks` without taking further action.
- **Root Cause:** The `pm_manage_prompt` continuously instructed the PM to return to step 1 (`list_tasks`) after every action, causing small models to loop helplessly.
- **Fix:** (RESOLVED) Updated `pm_manage_prompt` to skip `list_tasks` if a recent list is already in history, forcing the PM to transition to Step 2 (Analyze and Decide) automatically and unblocking the loop. Added explicit `DO NOT REPEAT` instructions for `DUPLICATE BLOCKED` feedback.
- **File:** `prompts.json`

### 3. Taskwarrior Placeholder Dependency Crash

- **Severity:** Moderate
- **Description:** `project_management` tool fails with an error when the LLM attempts to use placeholder IDs like `T1_1` for dependencies instead of valid Taskwarrior integers/UUIDs. This errors out and blocks task creation entirely.
- **Fix:** (RESOLVED) Updated the `add_task` action in `project_management.py` to filter out invalid dependency formats with a warning instead of returning an error, allowing task creation to proceed.
- **File:** `src/tools/project_management.py`

### 7.5. Comma-Separated Dependencies Silently Dropped

- **Severity:** High
- **Description:** When the LLM sent `depends: "task_1,task_2,task_3"` (comma-separated alias IDs), `project_management.py` treated the entire string as a single value, failed validation, and silently dropped all dependencies.
- **Fix:** (RESOLVED) Refactored dependency parsing to split comma-separated values and resolve each item independently through the alias/UUID/ID system.
- **File:** `src/tools/project_management.py`

### 7.6. Worker Self-Activation Directive Flooding

- **Severity:** High
- **Description:** When a worker created sub-tasks assigned to itself via `add_task`, `interaction_handler.py` triggered `activate_worker_with_task_details()` for each one. Since the worker was busy, 5+ directives were deferred and injected at once, confusing the model.
- **Fix:** (RESOLVED) Added a self-activation guard: if `assignee_id == agent.agent_id`, skip activation. Cross-agent activation (PM → worker) is unaffected.
- **File:** `src/agents/interaction_handler.py`

### 7.7. Infinite "Already In State" Reactivation Loop

- **Severity:** Critical
- **Description:** When a worker requested a state it was already in (e.g., `worker_work` → `worker_work`), `cycle_handler.py` forced reactivation for non-idle states, feeding back the same state-change message and creating an infinite loop.
- **Fix:** (RESOLVED) Same-state requests are now always treated as no-op (no reactivation), preventing the loop.
- **File:** `src/agents/cycle_handler.py`

---

## Critical Issues

### 1. DUPLICATE BLOCKED Messages Accumulate in PM History

- **Severity:** Critical (causes context window bloat and eventual stall)
- **Description:** The cross-cycle duplicate detection correctly intercepts repeated identical tool calls and injects `[Framework System Message - DUPLICATE BLOCKED]` directives. However, because it `continue`s past tool execution, the state-specific block (where `_deduplicate_pm_framework_messages` is normally called) is skipped. As a result, duplicate warnings accumulated infinitely within the PM's context.
- **Result:** 124 DUPLICATE BLOCKED messages accumulated in the PM's history during the run, bloating the context window.
- **Fix:** (RESOLVED) Updated `_deduplicate_pm_framework_messages()` timing. Called it explicitly before appending escalation messages in the `_detect_cross_cycle_duplicate_tool_call` handler within `run_cycle`.
- **File:** `src/agents/cycle_handler.py`

### 2. PM Repeats `list_tasks` Despite DUPLICATE BLOCKED Directive

- **Severity:** High
- **Description:** Even after receiving a `[Framework System Message - DUPLICATE BLOCKED]` directive telling it to proceed to the next workflow step, the local LLM (qwen3:14b) ignores it and calls `list_tasks` again. The framework correctly detects the duplicate (2 detections logged), but the LLM does not follow the escalated directive.
- **Root Cause:** The `PromptAssembler` was regenerating the full state prompt (including the `Step 1A: <project_management><action>list_tasks</action>` template) and overwriting the start of the history on *every* cycle. The LLM latched onto this template rather than following the new directive.
- **Fix:** (RESOLVED) Implemented "Option C". Modified `PromptAssembler.prepare_llm_call_data()` to only inject the full state prompt once when entering a state. If the history already begins with the state's prompt, it is preserved, preventing the Step 1A template from re-appearing after it has successfully completed.
- **File:** `src/agents/cycle_components/prompt_assembler.py`

---

## Moderate Issues

### 3. PM Creates Near-Duplicate Worker Agents

- **Severity:** Moderate (mitigated by framework's duplicate prevention)
- **Description:** During the "Build Team" phase, PM1 (qwen3:14b) successfully created "W1" (Coder). On the next turn, despite being prompted to review the kickoff plan and not create duplicates, the LLM hallucinates a similar `create_agent` call. Since local LLMs sometimes vary their JSON output arguments (e.g., `system_prompt` wording differs), exact string matching failed to detect the cross-cycle duplicate.
- **Result:** The LLM bypasses duplicate interception, the tool executes and throws a native domain error: "An agent with the role/persona 'Technical Writer' already exists in your team."
- **Fix:** (RESOLVED) Augmented `_detect_cross_cycle_duplicate_tool_call` to perform a semantic equivalence check for `manage_team` -> `create_agent`, triggering a block if the target persona/role exactly matches an immediately preceding cycle call.
- **File:** `src/agents/cycle_handler.py`

### 3.5. System Messages Parsed as Tool Results Leading to "Tool Error"

- **Severity:** High (causes model confusion and incorrect state transitions)
- **Description:** In the `PM_STATE_ACTIVATE_WORKERS` state interventions, the framework fetches the last tool result from `all_tool_results_for_history[-1]`. If the preceding logic intercepted a duplicate tool call, it appends a `[Framework System Message - DUPLICATE BLOCKED]` to the end. The state intervention sees this system message, fails `json.loads` because it's not a JSON dict, and fallbacks to setting the tool status to "error", returning a false `[Framework Feedback: Tool Error]`.
- **Fix:** (RESOLVED) Updated the parser to iterate backwards through `all_tool_results_for_history` and only fetch the first item where `role == "tool"`.
- **File:** `src/agents/cycle_handler.py`

### 4. PM Produces Malformed XML (`<tool_call>` JSON format)

- **Severity:** Moderate (single occurrence, recovered by framework)
- **Description:** PM1 produced a `<tool_call>` block with JSON inside instead of the expected XML format. The framework detected it as malformed and provided feedback.
- **Log entry:** Line 9360: `Agent PM1: <tool_call> JSON missing 'name' field.`
- **Root Cause:** LLM confusion between XML tool format and JSON tool_call format (common with qwen3:14b model).
- **Mitigation:** The framework already handles this via error feedback. Consider adding a recovery parser that can extract tool name/args from JSON-formatted tool_call blocks.
- **File:** `src/agents/agent_tool_parser.py`

### 5. `tags` Parameter Type Mismatch (str vs list)

- **Severity:** Moderate (tool handles gracefully but logs warning)
- **Description:** The PM sends `tags` as a string `"+W2,assigned"` but the tool expects a list. 3 occurrences in the log.
- **Log entry:** `Tool 'project_management' parameter 'tags' expects a list, but received str.`
- **Root Cause:** XML parsing produces strings, not lists. The PM follows the workflow prompt which uses `<tags>+W2,assigned</tags>` (a string).
- **Fix:** Either update the tool schema to accept strings, or add automatic string-to-list parsing in the executor for list-type parameters.
- **File:** `src/tools/executor.py` and `src/tools/project_management.py`

---

## Low / Informational Issues

### 7. Duplicate `create_team` Calls

- **Severity:** Low (harmlessly rejected by framework)
- **Description:** 5 occurrences of "Team already exists" warning. The PM attempts to create the team multiple times because the framework message history includes the `create_team` instruction even after the team is created.
- **Fix:** The framework already handles this gracefully. Could improve by removing the `create_team` instruction from the prompt after the team is created.
- **File:** `src/agents/workflow_manager.py` (build_team_tasks state prompt)

### 8. Ollama Model RAW Template Warnings

- **Severity:** Info
- **Description:** Multiple Ollama models (embedding models, qwen3.5 variants, etc.) have RAW templates which may not work correctly for multi-turn conversations.
- **Fix:** Only relevant if these models are selected for agent use. The framework correctly avoids them for agents.
- **File:** `src/config/model_registry.py`

---

## Summary of Latest Run (2026-03-26)

| Metric | Value |
| -------- | ------- |
| Log file | `app_20260326_174455_1570101.log` (656,818 lines) |
| ERRORs | 35 (mostly Ollama provider 500s and tool param errors) |
| Tool Exec Failures | 8 |
| CG Stuck-State Interventions | **0** (Phase M fix validated) |
| CG Empty-Response Blocks | 1 (W1, legitimate after failover cascade) |
| DUPLICATE BLOCKED messages | 910 (272 file_system, 236 project_management, 52 send_message) |
| AUTO-ADVANCE messages | 295 |
| Workers created | 3 (W1-Game_Developer, W2-Technical_Writer, W3-Game_Developer) |
| Workspace files produced | 25 (14 source, 7 docs, 4 leaderboard system) |
| PM state reached | Full lifecycle: startup → build_team → activate_workers → manage ↔ report_check (9 round-trips) |
| Duration | ~2h 47min |

## What Worked Well

- **Zero** false-positive stuck-state CG interventions (Phase M fix validated)
- No empty CG verdicts (configurable token limit fix working)
- PM completed full lifecycle with 9 manage↔report_check round-trips
- All 3 workers followed healthy decompose→work→report cycles
- Substantial output: 25 files including game scenes, managers, docs, and architecture files
- Failover mechanism partially working: W1 successfully switched hosts on first attempt
- Cross-cycle duplicate detection prevented redundant tool executions

### +26. Worker Deadlock in `worker_wait` State
- **Severity:** High (P1)
- **Description:** Workers forced into `worker_wait` by the Constitutional Guardian while holding active tasks were ignored by the PM (who assigns tasks to task-less workers), creating a permanent deadlock.
- **Root Cause:** PM logic relies on task-assignment rather than active pinging.
- **Fix:** (RESOLVED) Implemented an Auto-Recovery Watchdog in `manager.py`. It actively scans `worker_wait` agents. If they hold a `pending` TaskWarrior task, it automatically invokes `activate_worker_with_task_details()` to revive them.
- **Files:** `src/agents/manager.py`

### +27. Local LLM Stream Timeout Deadlock
- **Severity:** Critical (P0)
- **Description:** Small local LLMs occasionally stall without emitting stream chunks or closing the connection. Because `DEFAULT_READ_TIMEOUT` was 20 minutes (1200s), stalled requests held the exact `limit=1` semaphore open, stalling all agents for 20 minutes before timing out.
- **Root Cause:** `DEFAULT_READ_TIMEOUT` inside `ollama_provider.py` was too high for a fast-paced agent loop.
- **Fix:** (RESOLVED) Reduced `DEFAULT_READ_TIMEOUT` to 120s and `DEFAULT_TOTAL_TIMEOUT` to 600s, enabling the system to rapidly raise stream errors, drop bad cycles, and autorecover.
- **Files:** `src/llm_providers/ollama_provider.py`

### +28. Report Messages Blocked via UI
- **Severity:** Medium (P2)
- **Description:** Workers continually failed to mark report messages as read during the "Report Safety Check," causing endless looping.
- **Root Cause:** `mark_messages_read` occasionally fails due to payload quirks.
- **Fix:** (RESOLVED) Implemented a 3-strike loop breaker inside `prompt_assembler.py`. After 3 consecutive fails, the safety check automatically forces `unread_system_count = 0` to let the worker proceed.
- **Files:** `src/agents/cycle_components/prompt_assembler.py`

### +29. Task UUID Strictness in Re-Assignment
- **Severity:** Low (P3)
- **Description:** The PM occasionally failed to modify/complete tasks because it forgot the exact UUID and hallucinated a short name.
- **Root Cause:** Strict filter checking in `project_management.py`.
- **Fix:** (RESOLVED) Added a fuzzy-matching fallback array lookup. If UUIDs fail, the tool tries to resolve via a case-insensitive substring match of the task description/title.
- **Files:** `src/tools/project_management.py`

### +30. Redundant Framework Command Cycles
- **Severity:** Low (P3)
- **Description:** Agents were repeatedly issuing `npm install` and `git init` during decomposition phases, wasting extremely expensive LLM cycles.
- **Root Cause:** Command tools executed blindly without checking target conditions.
- **Fix:** (RESOLVED) Added idempotency caching. `command_executor` instantly mimics success if `node_modules` exists on an `npm install`, and `file_system` skips git init if `.git` is detected.
- **Files:** `src/tools/command_executor.py`, `src/tools/file_system.py`
