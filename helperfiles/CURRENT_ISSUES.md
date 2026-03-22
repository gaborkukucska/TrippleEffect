# TrippleEffect - Current Issues

**Last Updated:** 2026-03-21
**Based on log:** `startup_1774068944` and `startup_1774077203`

---

## Recently Resolved Issues

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

### 6. Constitutional Guardian Returns Empty Verdict

- **Severity:** Low (fails open as `<OK/>`)
- **Description:** CG returned an empty verdict once (line 3977), treated as OK to prevent stall. This is likely due to the CG model producing an empty response.
- **Fix:** No immediate fix needed; the fail-open behavior is correct. Monitor frequency in future runs.
- **File:** `src/agents/cycle_handler.py`

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

## Summary of Latest Run (2026-03-11)

| Metric | Value |
|--------|-------|

| ERRORs | 0 |
| WARNINGs | 140 |
| Cross-cycle duplicates detected | 2 |
| Workers created | 4 (W1-Coder, W2-Full-Stack Dev, W3-UI/UX, W4-Tester) |
| Tasks assigned | 1+ (W2 assigned and started producing code) |
| Workers producing output | Yes (W2 wrote server setup files) |
| PM state reached | `pm_activate_workers` → task assignment |
| Duration | ~20 minutes |

## What Worked Well

- Cross-cycle duplicate detection activated correctly, preventing redundant tool executions
- Worker creation pipeline: 4 workers created successfully with correct models
- Duplicate agent prevention: framework blocked 2 duplicate persona attempts
- Workers started producing tangible output (W2 created server files)
- No ERRORs in the entire run (major improvement)
- Constitutional Guardian reviews passed successfully
