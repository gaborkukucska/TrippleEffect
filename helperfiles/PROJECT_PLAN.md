<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.42
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
* Utilize **XML-based tool calling** with **sequential execution** (one tool type per turn enforced). *(Completed, Enhanced in P25)*
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
* **Enhanced Tooling (XML Format):**
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

---
<!-- # END OF FILE helperfiles/PROJECT_PLAN.md -->
