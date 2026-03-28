<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.44
**Date:** 2025-08-30

## 1. Project Goals

* Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
* Implement an **Admin AI** agent that acts as the central coordinator/initiator, operating via a state machine (`conversation`, `planning`). *(Completed, Refined in P24)*
* Implement a **Project Manager** agent, auto-created per session, to handle detailed task tracking and team coordination using `tasklib`, activated after **user approval**. *(Completed in P23, Activation updated in P24)*
* Enable dynamic agent/team creation and deletion *in memory*. *(Completed)*
* Implement **standardized communication layers**: *(Completed - UI Refactor Done)*
  * **Layer 1:** User <-> Admin AI Interaction. *(Completed)*
  * **Layer 2:** Admin AI <-> Local Dynamic Agents (within the same team or Admin). *(Completed)*
  * **Layer 3:** Admin AI <-> External Authorized Admin AIs / Groups (Federated Communication). *(Future Goal - Phase 29+)*
* Inject standardized context (incl. tools, time, health report for Admin) into dynamic agents' system prompts. *(Completed, Enhanced in P24/P25)*
* Empower agents to communicate and collaborate autonomously (within Layer 2). *(Completed)*
* Implement **session persistence** (filesystem), including project task data (`tasklib`). *(Completed, Enhanced in P23)*
* Utilize `config.yaml` primarily for bootstrapping the `Admin AI` and `Project Manager` agents. *(Completed, Updated in P23)*
* **Dynamically discover reachable providers** and **available models**. *(Completed)*
* **Filter discovered models** based on configuration (`MODEL_TIER` env var). *(Completed)*
* **Automatically select the Admin AI's provider/model at startup**. *(Completed)*
* Implement provider availability checks and **automatic retries** for transient errors. *(Completed)*
* **Implement automatic model/provider failover** for agents experiencing persistent errors during generation, following preference tiers (Local -> Free -> Paid). *(Completed)*
* **Implement basic performance metric tracking** (success rate, latency) per model, persisting data. *(Completed)*
* Implement a **Human User Interface** reflecting system state and communication layers, including **project approval workflow**. *(Completed - Refactored in P22, Approval in P24)*
* Utilize **Native JSON schemas** for tool execution (default) with **XML-based tool calling fallback** for legacy configurations. *(Completed, Upgraded in Phase I)*
* Allow tool use in sandboxed or **shared workspaces** with **authorization checks** based on agent type. *(Completed, Auth added P25)*
* Implement **automatic project/session context setting** (DB and filesystem). *(Completed)*
* Implement **automatic model selection** for dynamic agents if not specified. *(Refined in P26b)*
* Implement **robust agent ID/persona handling** for `send_message` (Layer 2). *(Completed)*
* Implement **structured planning phase** for Admin AI, followed by **framework-driven delegation to Project Manager** (requires user approval). *(Completed, Updated in P24)*
* Enhance `FileSystemTool` with **find/replace, mkdir, delete**. *(Completed)*
* Enhance `GitHubTool` with **recursive listing**. *(Completed)*
* Enhance `ManageTeamTool` with **agent detail retrieval**. *(Completed)*
* Make `WebSearchTool` more robust with **API fallback**. *(Completed)*
* Implement `SystemHelpTool` for Admin AI **time awareness, log searching**. *(Completed)*
* Implement `ProjectManagementTool` using `tasklib` for task tracking (assigns via tags/UDA CLI workaround). *(Completed in P23, Refined in P24/P25)*
* Implement `KnowledgeBaseTool` for saving/searching distilled knowledge and agent thoughts. *(Completed in P21, Thoughts added P25)*
* Implement `ToolInformationTool` for retrieving detailed tool usage. *(Completed in P25)*
* Inject **current time context** and **system health report** into Admin AI LLM calls. *(Completed)*
* Implement **Memory Foundation** using a database (SQLite) for interaction logging and knowledge/thought storage. *(Completed in P21, Enhanced P25)*
* Refactor UI for Communication Layers and refine Admin AI memory usage prompts. *(Completed in P22)*
* Fix UI message interleaving issue during concurrent streaming. *(Completed in P22)*
* Increase internal comms message history limit. *(Completed in P22)*
* Implement Local API Round-Robin for model selection and address various stability/logic issues. *(Completed in P26b)*
* **Implement Advanced Agent Health Monitoring System** with comprehensive loop detection, recovery, and XML validation capabilities. *(Completed in P27)*
* **(Future Goals)** **Advanced Memory & Learning** (P28), **Proactive Behavior** (Scheduling - P29), **Federated Communication** (Layer 3 - P30+), Enhance Admin AI planning (few-shot examples), implement new Admin AI tools, resource management, advanced collaboration patterns, DB integration, **Full transition to on-demand tool help** (removing static descriptions from prompts - P31+).

## 2. Scope

**In Scope (Completed up to Phase 27):**

* **Core Backend & Agent Core:** Base functionality, stateful agents (Admin, PM, Worker types).
* **Admin AI Agent:** Core logic, state machine (`conversation`, `planning`), time/health context, KB search prompt, **framework-driven delegation to PM workflow**.
* **Project Manager Agent:** Definition, automatic creation per session, prompt for active management, **requires user approval to start**.
* **Agent Manager & Handlers:** Orchestration, cycle management, interaction handling, failover, DB logging integration, **PM agent auto-creation**, **framework-driven initial task creation via ToolExecutor**, **Local API Round-Robin Index Management**.
* **Advanced Agent Health Monitoring (P27):** Comprehensive Constitutional Guardian system with intelligent agent behavior monitoring, loop detection, and recovery capabilities.
* **Cycle Components Architecture (P27):** Modular system including AgentHealthMonitor, XMLValidator, ContextSummarizer, NextStepScheduler, PromptAssembler, and OutcomeDeterminer.
* **Workflow Manager (`AgentWorkflowManager`):** Manages agent states and state-specific prompt selection.
* **State & Session Management:** Team state (runtime), Save/Load (filesystem), **Tasklib data persistence**.
* **Model Registry (`ModelRegistry`):** Provider/model discovery, filtering.
* **Automatic Model Selection:** Admin AI startup, dynamic agents, **API-first Round-Robin for local providers**.
* **Performance Tracking (`ModelPerformanceTracker`):** Tracks metrics, saves to JSON.
* **Automatic Agent Failover:** Handles provider/model switching.
* **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI tool calls.
* **Enhanced Tooling Architecture:** Tools natively generate strict JSON schemas via Pydantic auto-translation (`get_json_schema()`), enabling dual-compatibility (Native JSON schemas + XML Fallbacks).
  * `FileSystemTool`: Read/Write/List/FindReplace/Mkdir/Delete.
  * `GitHubTool`: List Repos/Files (Recursive), Read File.
  * `ManageTeamTool`: Agent/Team CRUD, Assign, List, Get Details.
  * `WebSearchTool`: Tavily API w/ DDG Scraping Fallback.
  * `SendMessageTool`: Local agent communication (Layer 2).
  * `SystemHelpTool`: Get Time, Search Logs, Get Tool Info.
  * `KnowledgeBaseTool`: Save/Search knowledge (DB).
  * `ProjectManagementTool`: Add, list, modify, complete tasks using `tasklib` (assigns via CLI tags/UDA).
  * `ToolInformationTool`: Get detailed tool usage.
* **Tool Executor (`ToolExecutor`):** Dynamic tool discovery, schema generation, execution with **authorization checks**, and **contextual error help injection** (auto-includes relevant action documentation in error messages).
* **Modular Tool Help System:** Segmented `get_detailed_usage(sub_action=...)` in `FileSystemTool`, `ProjectManagementTool`, and `ManageTeamTool`. Agents receive concise summaries by default and can request action-specific documentation.
* **Configuration:** `config.yaml`, `.env`, `prompts.json`, `governance.yaml`.
* **Session Persistence:** Save/Load state (filesystem).
* **Human UI:** Dynamic updates, Session management, **Separated Chat and Internal Comms views (UI Refactor P22)**, Config View, Fixed message interleaving (P22), Increased internal history limit (P22), **Project approval workflow**.
* **WebSocket Communication:** Real-time updates.
* **Sandboxing & Shared Workspace:** Implemented.
* **LLM Integration:** OpenRouter, Ollama, OpenAI providers with retries/failover.
* **Helper Files & Logging:** Maintained.
* **Ollama Proxy Integration:** Optional, managed proxy.
* **Database Integration (Phase 21):** SQLite backend, SQLAlchemy models, interaction/agent logging, knowledge save/search tools.
* **Communication Layers:** Layer 1 (User<->Admin) & Layer 2 (Admin<->Local Agents) logic implemented.
* **Agent Thoughts:** Capture via `<think>` tag and save to KB.
* **Constitutional Guardian (CG) Agent:** Backend infrastructure for agent output review with enhanced health monitoring.
* **PM Workflow & UI Interaction Refinements:** Reliability fixes for PM startup and CG concern handling.

**Out of Scope (Deferred to Future Phases):**

* **Phase 28: Advanced Memory & Learning.** (Feedback Loop, Learned Principles, Context Optimization).
* **Phase 29: Proactive Behavior.** (Scheduling, Goal Management, Autonomous Planning).
* **Phase 30+: Federated Communication (Layer 3).** (External Admin AI interaction - protocol, security, discovery).
* **Phase 31+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, etc.
* Global Governance Principle Injection (Removed, replaced by CG review).
* `TEAMS_CONFIG` and `allowed_sub_agent_models` (Removed from `ConfigManager`).

## 💻 Technology Stack

* **Backend:** Python 3.9+, FastAPI, Uvicorn
* **Asynchronous Operations:** `asyncio`
* **WebSockets:** `websockets` library integrated with FastAPI
* **Database:** `SQLAlchemy` (Core, Asyncio), `aiosqlite` (for SQLite driver)
* **Task Management:** `tasklib` (Python Taskwarrior library)
* **LLM Interaction:** `openai` library, `aiohttp`
* **Frontend:** HTML5, CSS3, Vanilla JavaScript
* **Configuration:** YAML (`PyYAML`), `.env` (`python-dotenv`), JSON (`prompts.json`)
* **Tooling APIs:** `tavily-python`
* **Parsing:** `BeautifulSoup4` (HTML), `re`, `html` (XML)
* **Model Discovery & Management:** Custom `ModelRegistry` class
* **Performance Tracking:** Custom `ModelPerformanceTracker` class (JSON)
* **Persistence:** JSON (session state - filesystem), SQLite (interactions, knowledge, thoughts), Taskwarrior files (project tasks via `tasklib`)
* **Optional Proxy:** Node.js, Express, node-fetch
* **Data Handling/Validation:** Pydantic (via FastAPI)
* **Local Auto Discovery** nmap
* **Logging:** Standard library `logging`

## 4. Development Phases & Milestones

**Phase 1-26b (Completed)**

* [X] Core Functionality, Dynamic Agent/Team Mgmt, Refactoring, Provider/Model Discovery & Selection, Failover, Key Management, Prompt Centralization, Ollama Proxy, XML Tooling, Auto-Selection (Dyn), Robust Agent ID Handling, Structured Planning, Context Optimization & FS Tools, GitHub Recursive List, ManageTeam Details, WebSearch API Fallback, SystemHelp Tool, Admin Time Context, **Memory Foundation (DB & KB Tool)**, **UI Layer Refactor & Workflow Refinements**, **Project Manager Agent & Tasklib Integration**, **Admin AI State Machine & Framework-Driven Project Init**, **Agent Logic, Taskwarrior Refinement & Governance Layer Foundation**, **Constitutional Guardian - Backend Implementation**, **PM Workflow & UI Interaction Refinements**, **Local API Round-Robin and Stability Fixes**.

**Phase 27: Advanced Agent Health Monitoring System (Completed)**

* **Goal:** Implement comprehensive agent health monitoring, loop detection, and recovery capabilities to enhance the Constitutional Guardian system.
* [X] **Agent Health Monitor Implementation:** Comprehensive agent behavior tracking system that monitors patterns, detects problematic loops (empty responses, minimal responses, stuck states), and implements intelligent recovery strategies.
* [X] **XML Validator Component:** Advanced XML validation and recovery system that automatically detects malformed XML tool calls and attempts intelligent repair using multiple strategies (markdown fence removal, bracket correction, content extraction).
* [X] **Context Summarizer Implementation:** Intelligent conversation context management system that optimizes context for smaller models by automatically summarizing lengthy conversations while preserving critical information and recent interactions.
* [X] **Next Step Scheduler Enhancement:** Smart agent reactivation and workflow continuation logic that enables multi-step workflows, handles both successful and failed tool executions, and prevents infinite loops through coordinated empty response detection.
* [X] **Cycle Components Architecture:** Modular, extensible system for agent cycle management including PromptAssembler for dynamic prompt construction and OutcomeDeterminer for cycle outcome analysis.
* [X] **Enhanced Loop Detection:** Multi-layered protection system against infinite loops, empty responses, and stuck patterns with coordinated detection between CycleHandler, NextStepScheduler, and AgentHealthMonitor.
* [X] **Workflow Continuation Support:** Advanced support for multi-step agent workflows that allows agents to continue working through tool execution results, process outputs, and maintain work state until natural completion.
* [X] **Constitutional Guardian Integration:** Enhanced CG system with comprehensive health monitoring integration, preventing false positive interventions while maintaining robust protection against genuine problematic patterns.
* [X] **Recovery Strategy Implementation:** Intelligent recovery mechanisms including context clearing, status resets, guidance injection, workflow reminders, and tool availability updates based on specific problem analysis.
* [X] **Performance Optimization:** Context optimization features including automatic summarization for resource-constrained environments and intelligent token management for improved performance with smaller models.

**Incremental Improvements (v2.41 - v2.42, Completed)**

* [X] **Modular Tool Help System (v2.42):** Refactored `get_detailed_usage()` across `FileSystemTool`, `ProjectManagementTool`, and `ManageTeamTool` to support `sub_action` parameter. Agents receive concise action summaries by default and can request detailed, action-specific documentation on demand.
* [X] **Contextual Error Help Injection:** `ToolExecutor` now automatically fetches action-specific help when a tool execution fails and includes it in the error message via `ToolErrorHandler`, enabling agents to self-correct with targeted documentation.
* [X] **Enhanced `ToolErrorHandler`:** Added `action_help` parameter to `generate_enhanced_error_response()` and `format_error_for_agent()` for richer error context.
* [X] **New FileSystemTool Actions:** Added `append`, `insert_lines`, and `replace_lines` actions for granular file manipulation.
* [X] **Type Safety Improvements:** Resolved all Pyright type errors across `executor.py`, `file_system.py`, `manage_team.py`, and `project_management.py`.

**Future Goals:**

* **Phase 28: Advanced Memory & Learning.** (Feedback Loop, Learned Principles, Advanced Context Management, Long-term Memory Systems).
* **Phase 29: Proactive Behavior.** (Scheduling, Goal Management, Autonomous Planning, Predictive Actions).
* **Phase 30+: Federated Communication (Layer 3).** (External Admin AI interaction - protocol, security, discovery, Cross-system coordination).
* **Phase 31+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, Distributed Processing, etc.

## Recent Development Notes & Issue Resolutions

### Agent Health Monitoring System Development (Phase 27)

**Challenge:** Agents, particularly Admin AI, were experiencing infinite loops with empty responses, almost empty responses, or identical repeated responses. The existing Constitutional Guardian system needed enhancement to catch, analyze, and restart troubled agents while preventing false positive interventions.

**Solution Implemented:** Comprehensive agent health monitoring system with multiple coordinated components:

1. **AgentHealthMonitor**: Tracks agent behavior patterns using AgentHealthRecord objects, monitors consecutive empty/minimal responses, detects repetitive patterns, and implements intelligent recovery strategies with context analysis.

2. **XMLValidator**: Handles malformed XML tool calls through automatic validation and recovery using multiple repair strategies including markdown fence removal, bracket correction, and content extraction.

3. **ContextSummarizer**: Manages conversation context through intelligent summarization for optimal performance with smaller models while preserving critical information.

4. **NextStepScheduler**: Enhanced with sophisticated agent reactivation logic that supports multi-step workflows, handles both successful and failed tool executions, and coordinates with other components to prevent infinite loops.

5. **Coordinated Loop Detection**: Multi-layered system where CycleHandler detects 3 consecutive empty responses and forces completion, while NextStepScheduler stops continuation after 2 empty cycles to prevent conflicts.

**Key Technical Achievements:**

* **False Positive Prevention**: 30-second cooldown after meaningful actions prevents incorrect health interventions
* **Multi-step Workflow Support**: Agents can now execute complex workflows with proper continuation after tool calls
* **Intelligent Recovery**: Context-aware recovery strategies based on specific problem analysis
* **Performance Optimization**: Context summarization and token management for improved efficiency
* **Robust Error Handling**: Enhanced XML validation with automatic repair capabilities

### PM Agent Loop Resolution History

**Issue History (June 2025):**
PM agents experienced loops in `pm_build_team_tasks` state after successful team creation, failing to proceed to worker agent creation.

**Solutions Attempted:**

1. Modified `pm_build_team_tasks_prompt` to change Step 1 from "Create Project Team" to "Ensure Project Team Exists"
2. Corrected `{project_name_snake_case}` placeholder error that was causing WorkflowManager failures
3. Enhanced Step 1 instructions to explicitly direct agents to proceed to Step 2 after team verification
4. Implemented more explicit action directives for tool listing and worker creation

**Current Status:** Resolved through enhanced prompt engineering and workflow state management improvements integrated into the Phase 27 agent health monitoring system.

### Code Audit Findings & Fixes (March 2025)

**Audit Scope:** Full codebase analysis of 18+ core files (~7000+ lines).

#### Critical: PM Agent Breakdown Root Causes (Fixed)

The PM agent was breaking down before worker agents could start due to 3 compounding issues:

1. **Fragile directive injection (`cycle_handler.py`):** PM state directives only fired when `len(tool_calls) == 1`. If the LLM produced multiple tool calls, directives were silently skipped, leaving the PM with no guidance. **Fixed:** Changed to `len(tool_calls) >= 1`, using `tool_calls[-1]` for matching.

2. **Silent death on failed cycles (`next_step_scheduler.py`):** When PM cycles failed in `build_team_tasks` or `activate_workers` state, the scheduler set the PM to IDLE without rescheduling — the PM permanently stopped. **Fixed:** Added PM-specific rescheduling in the failed-cycle branch.

3. **Duplicate tool result appending (`cycle_handler.py`):** Tool results were appended both inline to the assistant message AND as separate tool role messages, inflating the LLM context and causing confusion. **Fixed:** Removed inline appending; tool results now flow only through `NextStepScheduler`.

#### High: Overlapping Intervention Systems (Mitigated)

Five separate loop detection/intervention systems operate simultaneously (`AgentCycleHandler`, `NextStepScheduler._should_admin_continue_work`, `ConstitutionalGuardianHealthMonitor`, `_periodic_pm_manage_check`, XML error cleanup) with no coordination. **Mitigated:** Added `_has_too_many_recent_system_messages()` helper to prevent context flooding when multiple systems inject messages simultaneously.

#### Medium Issues Fixed

| Issue | File | Fix |
|---|---|---|
| `bare except:` | `prompt_assembler.py:74` | Changed to `except (json.JSONDecodeError, TypeError, ValueError):` |
| `\\\\n` literal escapes | `cycle_handler.py`, `next_step_scheduler.py` | Replaced with actual `\n` newlines (6 locations) |
| Uninitialized attribute access | `cycle_handler.py:1531,1583` | Added `getattr()` guards for `intervention_applied_for_build_team_tasks` and `text_buffer` |
| Placeholder prompt | `prompts.json:24` | Removed `do_not_use_this_is_just_an_example` |
| Diagnostic logging bloat | `next_step_scheduler.py` | Simplified 3 verbose DIAGNOSTIC blocks removing expensive JSON serialization |

#### Known Remaining Issues (Not Yet Fixed)

* `PM_STATE_PLAN_DECOMPOSITION` defined in constants but never entered by any workflow
* `pm_work_prompt` defined in prompts.json but never used by workflow manager
* Hardcoded 9-tool list in emergency overrides (`next_step_scheduler.py:303,340`)

#### Phase E: PM create_team Loop Fix (March 2025)

**Problem:** After Phase A-D fixes, PM successfully enters `pm_build_team_tasks` but loops calling `create_team` indefinitely. The LLM (gemma3:4b) anchors to Step 1's literal XML template in the system prompt instead of following the sandwiched directive.

**Fix (3-part):**

1. **`pm_kickoff_workflow.py`**: Auto-executes `create_team` + `tool_information get_info manage_team create_agent` during kickoff, injecting results into the transition directive.
2. **`cycle_handler.py`**: Auto-executes `tool_information` after any `create_team` call (fallback).
3. **`prompts.json`**: Removed Steps 1-2 from `pm_build_team_tasks_prompt` (now framework-automated). LLM starts at `create_agent`.

**Log file split fix:** Added PID to log filename in `main.py` to prevent split when startup spans a second boundary.

#### Phase F: Ollama Model Compatibility & PM Worker Creation Fixes (March 2026)

**Problem 1 (Model Compatibility):** Models like `gemma3:4b-it-q4_K_M` lost context or `qwen3-14b-extended:latest` produced gibberish due to hardcoded local provider settings (`num_ctx: 8192` and stop token `<|eot_id|>`).
**Fix 1:** Extended `ModelRegistry` and `ModelInfo` to parse and store `family`, `template`, `num_ctx`, and `stop` tokens from Ollama's `/api/show` endpoint. Updated `OllamaProvider` to dynamically pull model-specific metadata via `ModelRegistry.get_model_info()` at runtime, removing hardcoded values and warning when raw templates (`{{ .Prompt }}`) are detected.

**Problem 2 (PM Agent Worker Creation Failure):** With a proper model configuration, the PM agent failed silently during the `activate_workers` phase because its `create_agent` tool call was missing the `<manage_team><action>create_agent</action>` envelope, instead directly outputting `<create_agent>`.
**Fix 2:** Corrected the `pm_standard_framework_instructions` template in `prompts.json` to explicitly demonstrate the required `manage_team` XML structure for agent creation.

#### Phase G: Framework Tool Enhancements & Agent Stability Fixes (March 2026)

**Problem 1 (Worker -> PM Messaging):** Strict team ID checks were blocking worker-to-PM communication, preventing workers from reporting task completion.
**Fix 1:** Modified `route_and_activate_agent_message` in `interaction_handler.py` to allow messaging if both agents belong to the same project, relaxing strict team ID rules.

**Problem 2 (Admin AI Memory Loss):** Admin AI lacked context of past sessions upon restart.
**Fix 2:** Added `get_recent_sessions_summary()` to `database_manager.py` and integrated it into the Admin AI startup process to dynamically inject recent project history into its system prompt.

**Problem 3 (Agent Naming Token Inefficiency):** Long agent names (e.g., `pm_{project_title}_{session_id}`) wasted tokens.
**Fix 3:** Implemented sequential short naming conventions (e.g., P1, PM1, W1, W2) in project creation and team management workflows.

**Problem 4 (Constitutional Guardian Over-strictness & False Empty Responses):** Agents were getting caught in a "consecutive empty responses" loop because `cycle_handler.py` was checking text output too late. Furthermore, the CG thresholds were rigidly hardcoded making the AI feel too restrictive.
**Fix 4:** Refactored the LLM iteration loop in `cycle_handler.py` to explicitly preserve textual output alongside tool calls, preventing the false empty response triggers. Introduced a `CG_STRICTNESS_LEVEL` setting (1=Permissive, 2=Moderate, 3=Strict) to dynamically adjust intervention thresholds allowing user configuration.

**Problem 5 (Doubled Greeting on Startup):** The primary UI printed two initial greetings in the same output when loading the `admin_ai`.
**Fix 5:** Traced event loops in `cycle_handler.py`. Found that the explicit `action_taken_this_cycle` flag was not being toggled when yielding a valid `final_response` type. This caused the fallback iteration branch later in the handler to pick up the response buffer again and process a duplicate message. Applied `context.action_taken_this_cycle = True` inside the `final_response` processing wrapper.

**Problem 6 (PM Agent Stalling During CG Review — 345+ Second Stall):** PM1 stalled indefinitely in `awaiting_user_review_cg` status during team-building. Root cause was three compounding issues:

1. `qwen3:14b` uses `<tool_call>{"name":"manage_team",...}</tool_call>` JSON format which the XML parser didn't recognize, causing create_agent calls to be treated as plain text.
2. This plain text was sent to the CG for review. The CG returned empty string `''`, which was classified as a concern (not `<OK/>`), blocking the PM indefinitely.
3. Tool-call-only responses shouldn't have been sent to CG review at all since they are framework operations, not user-facing content.

**Fix 6a:** Added `<tool_call>` JSON format parsing in `agent_tool_parser.py` — new `_parse_tool_call_json_blocks()` function as fallback after standard XML parsing.
**Fix 6b:** Changed CG empty verdict handling to fail-open (`<OK/>`) instead of generating error strings that trigger `awaiting_user_review_cg`.
**Fix 6c:** Added tool-call-only response detection in `cycle_handler.py` to skip CG review for framework operations.

**Problem 7 (Worker Agent Team Registration & file_system Failures):** Three interrelated issues prevented workers from functioning:

1. `file_system` tool failed with `XML ParseError` when agents wrote HTML content without closing the `<content>` tag (e.g., `<content><!DOCTYPE html>...</html></file_system>` — no `</content>`). The existing regex `<param>(.*?)</param>` never matched, leaving HTML angle brackets unescaped.
2. Workers were created with `Team: N/A` because PM1 created workers before it had a team (PM1 creates workers at startup, but only calls `create_team` later). `_handle_create_agent` only checked the creator's team, found `None`, and skipped team assignment.
3. Bootstrapped `project_manager_agent` remained in address books alongside dynamic `PM1`, causing confusion.

**Fix 7a:** Enhanced `_sanitize_xml_block` in `agent_tool_parser.py` with fallback content extraction. When `<param>...</param>` regex doesn't match, extracts content between `<param>` and the next sibling tag or tool closing tag, inserts proper closing tag, and escapes content.
**Fix 7b:** Modified `_handle_create_agent` in `interaction_handler.py` to fall back to `team_id` from params or auto-generate from project context when creator has no team. Auto-creates the team and adds both creator and new agent.
**Fix 7c:** Filtered bootstrapped PM agents from `_build_address_book` in `workflow_manager.py` when dynamic PMs (PM1, PM2, ...) exist.

**Problem 8 (Kickoff Plan XML Parse Error, Worker PM Misdirection, and Bootstrapped PM Looping):** Three issues found in the latest test run:

1. `kickoff_plan` XML failed to parse when `<role>` content contained `&` (e.g., `Integration & Testing`). The existing escaping only covered `<task>` tags, not `<role>` tags.
2. Workers identified the bootstrapped `project_manager_agent` as their PM instead of `PM1` because `_build_address_book` picked the first matching PM from the ordered dict, and the bootstrapped PM was inserted first.
3. Bootstrapped `project_manager_agent` kept cycling in `pm_startup` state with no plan description after `PM1` was created, wasting LLM calls and confusing workers who messaged it.

**Fix 8a:** Generalized the `escape_task_content` function in `workflow_manager.py` to `escape_tag_content` factory, escaping both `<task>` and `<role>` content in `kickoff_plan` XML.
**Fix 8b:** Modified worker PM resolution in `_build_address_book` to prefer dynamic PMs (PM1, PM2, ...) over bootstrapped `project_manager_agent`.
**Fix 8c:** Added auto-deactivation in `get_system_prompt`: when a bootstrapped PM has no plan and a dynamic PM exists for the same project, it transitions to `pm_idle` state instead of spinning uselessly.

#### Phase H: Agent State Isolation & PM Loop Fixes (March 2026)

**Problem 1 (`send_message` / Work Mix-up):** Workers frequently misused `send_message` alongside other tools like `file_system`, causing parser errors when trying to report progress mid-task.
**Fix 1:** Created new, strictly separated `worker_report` and `pm_report_check` states. `send_message` was removed from `work` states to force structural communication isolation and prevent multi-tool call failure modes.

**Problem 2 (Context Destruction via Preemptive Activation):** The PM assigned kick-off tasks to workers while they were deeply focused (e.g., in `worker_report`), forcefully altering their state and corrupting their thought buffers.
**Fix 2:** Implemented a new `message_inbox` in the `Agent` core. When the PM assigns a task to a busy worker, `manager.py` defers the context change by pushing it into the inbox. `workflow_manager.py` cleanly flushes and applies these context updates only when the worker naturally transitions to a safe state like `worker_wait`.

**Problem 3 (Worker Output Loops in Report State):** A bug in `cycle_handler.py` failed to set the `needs_reactivation_after_cycle` flag when parsing embedded state transitions. Additionally, `WORKER_STATE_REPORT` was missing from `next_step_scheduler.py`'s persistent states, causing the worker to stall indefinitely instead of completing its report.
**Fix 3:** Assigned `context.needs_reactivation_after_cycle = True` inside the embedded state request block, and added `WORKER_STATE_REPORT` to the `persistent_states` tuple to ensure the worker inherently continues looping until its report is finished.

**Problem 4 (PM Endless Loop During `activate_workers`):** The PM repeatedly reassigned the same task despite TaskWarrior success. This occurred because the PM used a text description inside `<modify_task>`, but the framework's internal `unassigned_tasks_summary` mandated a strict UUID match to remove it from the backlog.
**Fix 4:** Reworked `cycle_handler.py` to extract and match purely on the `task_uuid` explicitly returned by the `modify_task` tool result JSON, guaranteeing absolute synchronization between the framework's backlog and TaskWarrior.

#### Phase I: Native JSON Tool Calling Upgrade (March 2026)

**Problem 1 (XML Constraint & Hallucination):** The framework's absolute reliance on XML parsing severely limited model compatibility, specifically for advanced reasoning models (like `qwen3` and OpenAI models) which expect native JSON tool arrays and frequently hallucinated or produced malformed XML (`<tool_call>...`) despite prompt instructions.
**Fix 1:** Introduced the `NATIVE_TOOL_CALLING_ENABLED` configuration flag (defaulting to True). `BaseTool` received a permanent `get_json_schema()` method to auto-generate strict JSON schema objects directly from existing Pydantic `ToolParameter` classes. Provider layers (`ollama_provider.py`, `openai_provider.py`) were overhauled to optionally pass the native `tools` array into the completion payload and yield newly architected `native_tool_calls` events alongside the traditional text streams.

**Problem 2 (XML Prompt Collision):** Hardcoded XML tool examples injected via `prompts.json` collided with the new JSON mechanics, forcing native-JSON-capable models to forcibly print XML blocks out of pure instruction adherence, resulting in fatal framework `XML ParseError` loops.
**Fix 2:** Refactored `src/agents/workflow_manager.py` and `prompts.json` to employ a dynamic parameter injection system (`{tool_examples}`). When native tools are enabled, the XML instructions are silently dropped from the context window assembly and replaced naturally by JSON fallback guidance, providing seamless context synchronization without relying on unreliable regex string replacement.

#### Phase J: Worker Task Decomposition & Inactivity Fixes (March 2026)

**Problem 1 (Worker Decompose Loop):** The PM eagerly assigned multiple tasks sequentially to workers before they could complete their mandatory task decomposition. Because `WORKER_STATE_DECOMPOSE` was missing from the "busy states" check, the PM forcibly wiped the short-term memory of workers mid-decomposition, causing an endless loop where workers never reached the work state.
**Fix 1:** Added `WORKER_STATE_DECOMPOSE` to `manager.py`. The PM now correctly queues tasks in the worker's `message_inbox` while the worker breaks down its initial kick-off task.

**Problem 2 (Empty LLM Generations in Work State):** Workers successfully transitioned to `worker_work` but outputted 0 tokens. This was caused by two edge cases: 1) Initializing a brand-new worker failed to inject the system directive if history was empty. 2) Using `<request_state>` updated internal state but failed to inject a `<user>` state transition confirmation message, leaving the context window ending with an `assistant` tag.
**Fix 2:** Fixed the history indentation bug in `manager.py` and implemented dynamic `[System State Change]` user message injection in `cycle_handler.py` to prompt the LLM to resume activity within its newly generated system context.

---

#### Phase K: PM Startup Strategy & Task Dependency Enhancements (March 2026)

**Problem 1 (Premature Task Assignment):** The PM's kick-off plan included testing and documentation tasks that couldn't be started until development was complete, leading to idle workers and wasted cycles.
**Fix 1:** Updated `pm_startup_prompt` in `prompts.yaml` to restrict kick-off tasks to three categories only: research (web/document/memory search), project setup (folder structure, requirements, environment), and foundational development (well-commented, debug-logged code). Testing and documentation are explicitly excluded from the initial plan and deferred to later project phases.

**Problem 2 (No Dependencies Between Kick-off Tasks):** Tasks created during the kickoff phase had no dependency relationships, allowing the PM to assign dependent tasks (e.g., coding before research) simultaneously.
**Fix 2:** Extended the `<task>` XML schema in `pm_startup_prompt` with `id` and `depends_on` attributes. Updated `pm_kickoff_workflow.py` to parse these attributes and pass them as `task_id` (alias) and `depends` to the `project_management` tool, leveraging the existing alias resolution system to create real TaskWarrior dependencies at task creation time.

**Problem 3 (Invalid `update_task_status` Action):** LLMs frequently hallucinated the action `update_task_status` when trying to modify task status via the `project_management` tool, causing `[TOOL_EXEC_FAILED]` errors.
**Fix 3:** Added `"update_task_status"` and `"update_status"` to the `action_suggestions` dictionary in `project_management.py`, auto-correcting them to `"modify_task"`.

**Problem 4 (file_system List Retries on Non-Existent Directories):** When a worker called `file_system` with `action=list` on a subdirectory that didn't exist yet (e.g., `frontend`), the tool returned an error status. The executor's retry logic then retried the same call 3 times before finally failing, wasting time and log space.
**Fix 4:** Changed `_list_directory` in `file_system.py` to return a `status: success` response with `items: []` and a helpful message listing the workspace root contents when the requested subdirectory doesn't exist. This guides the agent to create the directory with `mkdir` instead of triggering retries.

**Problem 5 (PM Assigns All Tasks Ignoring Dependencies):** Despite the `pm_activate_workers_prompt` instructing the PM to only assign actionable tasks, the framework's own system message in `cycle_handler.py` overrode this by saying "Your mandatory next action is to assign the next task" — forcing the PM to assign ALL 6 tasks including those with unmet dependencies.
**Fix 5:** Enhanced `cycle_handler.py` to track `depends` in `unassigned_tasks_summary` and check task dependencies before generating system directives. When all remaining tasks have unmet dependencies, the framework now instructs the PM to report completion and transition to manage state instead of continuing to assign blocked tasks.
**Problem 6 (Think-Tag Prefix Blocks Kickoff Plan Parsing):** When the PM (especially qwen3.5 models) outputs `<think>...</think>` tags before the `<kickoff_plan>` XML in startup state, the `workflow_manager.py` rejects the output as having a "problematic prefix". The existing `<think>` block allowance only covered the old `task_list` trigger tag, not the newer `kickoff_plan` trigger. This caused infinite retry loops where the PM keeps producing valid plans that the framework keeps rejecting.
**Fix 6:** Extended the `<think>` block prefix allowance condition in `workflow_manager.py` (line 436) from `trigger_tag == "task_list"` to `trigger_tag in ("task_list", "kickoff_plan")`, allowing both kickoff trigger formats to accept `<think>` prefixes.

**Problem 7 (Comma-Separated Dependencies Silently Dropped):** When the LLM sent `depends: "task_1,task_2,task_3"` (multiple alias IDs as a comma-separated string), `project_management.py` treated the entire string as a single value, failed UUID/ID validation, and silently dropped all dependencies with a warning log.
**Fix 7:** Refactored the dependency parsing block in `project_management.py` to split comma-separated `depends` values into individual items, resolve each through the alias system independently, and add all found dependencies as a set. Single-value inputs continue to work unchanged.

**Problem 8 (Worker Self-Activation Directive Flooding):** When a worker created sub-tasks assigned to itself (via `add_task` with `assignee_agent_id` matching its own ID), `interaction_handler.py` called `activate_worker_with_task_details()` for each sub-task. Since the worker was already busy in `worker_decompose`, all activations were deferred — queuing 5+ `[Framework Directive]: You have been assigned a new task` messages into the worker's inbox. When the worker transitioned to `worker_work`, all directives were injected at once, confusing the model about which task to work on.
**Fix 8:** Added a self-activation guard in `interaction_handler.py` that checks if `assignee_id == agent.agent_id`. When a worker assigns a task to itself (self-decomposition), activation is skipped entirely. Cross-agent activation (PM assigning to a worker) is unaffected.

**Problem 9 (Infinite "Already In State" Reactivation Loop):** When a worker requested a state it was already in (e.g., `worker_work` while already in `worker_work`), `cycle_handler.py` set `needs_reactivation_after_cycle = True` for all non-idle states. This appended a `[System State Change]` message and re-ran the cycle, but the LLM produced the same `<request_state>` output, creating an infinite loop consuming resources without progress.
**Fix 9:** Changed the same-state handling in `cycle_handler.py` to always set `needs_reactivation_after_cycle = False` regardless of state type. If an agent requests a state it's already in, it's treated as a no-op. The agent health monitor can intervene if the pattern indicates a stuck agent.

---

#### Phase L: Worker Ecosystem Stability (March 2026)

**Problem 1 (Unassigned Sub-Tasks Created by Workers):** When workers (like W1) decomposed their assigned tasks into sub-tasks using the `project_management.py` tool (`add_task`), they often failed to provide the `assignee_agent_id` parameter. This caused the tasks to remain unassigned, confusing the worker when it transitioned to work state.
**Fix 1:** Added auto-assignment logic in `project_management.py` so that if the calling agent is a worker and no `assignee_agent_id` is explicitly passed in the tool arguments, the generated task is automatically assigned to the caller.

**Problem 2 (Worker Cross-Cycle Duplicate Tool Loop):** Workers exhibiting confusion (e.g. looking for work but finding none) would repeatedly execute the `file_system` read action on `whiteboard.md` infinitely. While the cross-cycle duplicate tool call mechanism intercepted these duplicate calls, it was artificially restricted to only PM agents, allowing the workers to loop forever undetected.
**Fix 2:** Expanded the duplicate tool call detection scope (`_detect_cross_cycle_duplicate_tool_call`) in `cycle_handler.py` to include `AGENT_TYPE_WORKER`. Additionally, implemented a new escalation block specifically for workers: when `_duplicate_tool_call_count >= 3`, the framework injects a strong `[Framework System Message - AUTO-ADVANCE]` forcing the worker to stop repeating the exact identical tool call and to transition to a new tool or the `worker_report` state.

**Problem 3 (Project Filter Hallucination):** The LLM occasionally made typos when providing the `project_filter` argument to `list_tasks` (e.g., spelling "Snake_Game" as "Sake_Game"). Because `project_management.py` trusted the LLM-provided string to filter the list query, it incorrectly returned `0 task(s)` to the PM.
**Fix 3:** Implemented a "Project Filter Guard" in `project_management.py`. Whenever the tool is executed, it now forces `kwargs["project_filter"]` to exactly match the authenticated `project_name` provided by the framework context. This protects the native task database queries from any LLM spelling mistakes or hallucinations.

---

#### Phase M: Constitutional Guardian & Health Monitor Stabilization (March 2026)

**Problem 1 (CG Empty Verdict — Widespread):** The Constitutional Guardian returned empty verdicts on nearly every call, causing it to fail open and bypass governance checks entirely. Models using `<think>` blocks exhausted the hardcoded `max_tokens_for_verdict = 250` on internal reasoning before outputting any verdict content.
**Fix 1:** Replaced the hardcoded 250-token limit with a configurable `CG_MAX_TOKENS` setting (default `4000`) in `settings.py`, accessed via `getattr(settings, 'CG_MAX_TOKENS', 4000)` in `cycle_handler.py`.

**Problem 2 (PM Task Listing Token Overhead):** `list_tasks` returned all tasks including completed/deleted ones when no `status_filter` was specified, bloating agent context with irrelevant information and wasting tokens.
**Fix 2:** Modified `list_tasks` in `project_management.py` to default to `.pending()` when no `status_filter` is provided, automatically excluding completed and deleted tasks from output.

**Problem 3 (False-Positive Stuck-State CG Interventions):** Workers (W1, W2, W3) in `worker_work` and PM1 in `pm_manage` were falsely flagged as "stuck" by the `AgentHealthMonitor` despite actively executing tools (web searches, file writes, task management). W1/W3 accumulated 17 cycles, W2 hit 12, and PM1 reached 22+, all triggering repeated CG "STATE PROGRESSION REQUIRED" interventions that polluted agent history.
**Root Cause:** `cycle_count_in_current_state` in `AgentHealthRecord.record_response()` only reset on state changes, not when the agent took meaningful action (successful tool calls). Agents legitimately staying in their working state hit the `stuck_state_threshold` (default: 6) while doing productive work.
**Fix 3:** Added an `elif has_action` branch in `record_response()` that resets `cycle_count_in_current_state = 1` when the agent successfully executes tools. Truly stuck agents (no tool calls, no output) are still caught.

**Files:** `src/agents/cycle_handler.py`, `src/config/settings.py`, `src/tools/project_management.py`, `src/agents/cycle_components/agent_health_monitor.py`

---

#### Phase N: Failover & Context Optimization (March 2026)

**Problem 1 (Failover Cascade Death — No Tool Support Check):** When worker W1 failed on `ollama-local-192-168-0-22` (500 error), the failover handler switched to `ollama-local-192-168-0-24` with the same model. That host's `qwen3.5:9b-q4_K_M` returned 400: "does not support tools". Failover then tried `qwen3-coder:30b` on .22 — also lacking tool support. W1 produced 4 consecutive empty responses and was CG-BLOCKED. The failover handler validated provider health and model *availability* but not tool *capability*.
**Fix 1:** Added a module-level `_models_without_tool_support` runtime blacklist in `failover_handler.py`. When a "does not support tools" error is detected, the `(provider, model)` pair is permanently blacklisted for the process lifetime. Both Pass 1 (preferred model on alternate APIs) and Pass 2 (alternative model candidates) check the blacklist before attempting any switch.

**Problem 2 (Duplicate Cached Result Context Bloat):** Over 910 cross-cycle duplicate tool detections, the framework returned the full cached result each time as a tool response in agent history. Since these results could be thousands of characters, this added significant token consumption for information the agent had already seen.
**Fix 2:** Truncated cached duplicate results to 200 characters max in `cycle_handler.py`, appending a `[TRUNCATED - duplicate call, full result already returned previously]` suffix. Full results are still logged to DB.

**Problem 3 (`mark_completed` Action Hallucination):** Worker W1 used `mark_completed` instead of the correct `complete_task` action. The existing alias map covered `mark_complete` (without trailing 'd') but not `mark_completed`.
**Fix 3:** Added `mark_completed`, `complete`, and `task_complete` to the `action_suggestions` alias map in `project_management.py`.

**Problem 4 (Append Missing Filename):** Workers called `file_system` with `action=append` without the `filename` parameter. Already handled by existing validation at line 348. No code change needed.

**Files:** `src/agents/failover_handler.py`, `src/agents/cycle_handler.py`, `src/tools/project_management.py`

---

#### Phase O: Worker Persistent State Stagnation Interventions (March 2026)

**Problem 1 (Worker Stubbornly Repeating Identical Tool Calls):** Log analysis of a 656k-line database run revealed that workers (W2, W3) would occasionally enter an autoregressive loop. When modifying a task to `completed` via `modify_task` or when repeatedly calling the exact identical `send_message`, the framework's `CycleHandler` successfully blocked the cross-cycle duplicates and injected a strong `[Framework System Message - AUTO-ADVANCE]` directive. However, because smaller models (e.g. Qwen 3.5 9b) sometimes ignore appended system directives when their prior generation momentum is high, the worker would immediately *hallucinate the exact same tool call again*, resulting in infinite stagnation (e.g. W2 generated 27 identical calls in a row).
**Fix 1:** Introduced a "Hard Framework Loop Intervention" in `NextStepScheduler.schedule_next_step`. When any agent in a persistent state (`WORKER_STATE_WORK`, `WORKER_STATE_REPORT`, `PM_STATE_MANAGE`, `PM_STATE_WORK`) completes a cycle, the scheduler now checks `is_stuck_in_loop` (triggered after 4 consecutive duplicates). If true, the scheduler bypasses the AI entirely and forces an emergency state transition mapping:
- Looping `worker_work` -> forces transition to `worker_report`.
- Looping `worker_report` -> forces transition to `worker_wait`.
- Looping PMs -> triggers a hard context refresh/intervention.
This entirely breaks the LLM's autoregressive death spiral by physically moving the agent to a different state machine node where they are forced to process new logic or wait.

**Files:** `src/agents/cycle_components/next_step_scheduler.py`

---

#### Phase P: PM Audit State & Proactive CG Escalation (March 2026)

**Problem 1 (Undocumented/Unverified Project Completion):** PMs immediately messaged the Admin AI that the project was complete when their task list was exhausted, without doing any verification against the original instructions or documentation.
**Fix 1:** Created the `pm_audit` state. The PM securely transitions into this verification phase to review structural requirements, scan the codebase, and optionally run tests before compiling and sending a final Audit Report to the Admin AI. Only afterward will the PM transition to `pm_standby`.

**Problem 2 (Silent Agent Autoregressive Stalling):** Deep worker agent stalls (such as empty response loops) were passively blocked by the Constitutional Guardian, but supervising agents (PMs) remained completely unaware that their workers were frozen.
**Fix 2:** Upgraded the `AgentHealthMonitor` to deploy proactive escalation reports. When a stall threshold is crossed, the CG constructs a diagnostic report with the stalled agent's history and dispatches it directly into the supervisor's `message_inbox`, dynamically instructing them to use `send_message` for targeted Help/Human-in-the-Loop recovery.

**Files:** `src/agents/cycle_components/agent_health_monitor.py`, `src/agents/constants.py`, `src/agents/workflow_manager.py`, `src/agents/core.py`, `prompts.yaml`

---
<!-- # END OF FILE helperfiles/PROJECT_PLAN.md -->
