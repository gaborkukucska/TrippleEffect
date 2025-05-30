<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.25 <!-- Updated Version -->
**Date:** 2025-04-28 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator/initiator, operating via a state machine (`conversation`, `planning`). *(Completed, Refined in P24)*
*   Implement a **Project Manager** agent, auto-created per session, to handle detailed task tracking and team coordination using `tasklib`, activated after **user approval**. *(Completed in P23, Activation updated in P24)*
*   Enable dynamic agent/team creation and deletion *in memory*. *(Completed)*
*   Implement **standardized communication layers**: *(Completed - UI Refactor Done)*
    *   **Layer 1:** User <-> Admin AI Interaction. *(Completed)*
    *   **Layer 2:** Admin AI <-> Local Dynamic Agents (within the same team or Admin). *(Completed)*
    *   **Layer 3:** Admin AI <-> External Authorized Admin AIs / Groups (Federated Communication). *(Future Goal - Phase 27+)*
*   Inject standardized context (incl. tools, time, health report for Admin) into dynamic agents' system prompts. *(Completed, Enhanced in P24/P25)*
*   Empower agents to communicate and collaborate autonomously (within Layer 2). *(Completed)*
*   Implement **session persistence** (filesystem), including project task data (`tasklib`). *(Completed, Enhanced in P23)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI` and `Project Manager` agents. *(Completed, Updated in P23)*
*   **Dynamically discover reachable providers** and **available models**. *(Completed)*
*   **Filter discovered models** based on configuration (`MODEL_TIER` env var). *(Completed)*
*   **Automatically select the Admin AI's provider/model at startup**. *(Completed)*
*   Implement provider availability checks and **automatic retries** for transient errors. *(Completed)*
*   **Implement automatic model/provider failover** for agents experiencing persistent errors during generation, following preference tiers (Local -> Free -> Paid). *(Completed)*
*   **Implement basic performance metric tracking** (success rate, latency) per model, persisting data. *(Completed)*
*   Implement a **Human User Interface** reflecting system state and communication layers, including **project approval workflow**. *(Completed - Refactored in P22, Approval in P24)*
*   Utilize **XML-based tool calling** with **sequential execution** (one tool type per turn enforced). *(Completed, Enhanced in P25)*
*   Allow tool use in sandboxed or **shared workspaces** with **authorization checks** based on agent type. *(Completed, Auth added P25)*
*   Implement **automatic project/session context setting** (DB and filesystem). *(Completed)*
*   Implement **automatic model selection** for dynamic agents if not specified. *(Completed)*
*   Implement **robust agent ID/persona handling** for `send_message` (Layer 2). *(Completed)*
*   Implement **structured planning phase** for Admin AI, followed by **framework-driven delegation to Project Manager** (requires user approval). *(Completed, Updated in P24)*
*   Enhance `FileSystemTool` with **find/replace, mkdir, delete**. *(Completed)*
*   Enhance `GitHubTool` with **recursive listing**. *(Completed)*
*   Enhance `ManageTeamTool` with **agent detail retrieval**. *(Completed)*
*   Make `WebSearchTool` more robust with **API fallback**. *(Completed)*
*   Implement `SystemHelpTool` for Admin AI **time awareness, log searching, and tool info retrieval**. *(Completed)*
*   Implement `ProjectManagementTool` using `tasklib` for task tracking (assigns via tags/UDA CLI workaround). *(Completed in P23, Refined in P24/P25)*
*   Implement `KnowledgeBaseTool` for saving/searching distilled knowledge and agent thoughts. *(Completed in P21, Thoughts added P25)*
*   Implement `ToolInformationTool` for retrieving detailed tool usage. *(Completed in P25)*
*   Inject **current time context** and **system health report** into Admin AI LLM calls. *(Completed)*
*   Implement **Memory Foundation** using a database (SQLite) for interaction logging and knowledge/thought storage. *(Completed in P21, Enhanced P25)*
*   Refactor UI for Communication Layers and refine Admin AI memory usage prompts. *(Completed in P22)*
*   Fix UI message interleaving issue during concurrent streaming. *(Completed in P22)*
*   Increase internal comms message history limit. *(Completed in P22)*
*   **(Current Goal - Phase 25)** Address agent logic issues, Taskwarrior UDA issues, rate limiting. Implement basic Governance Layer. Refine agent thought capture/usage. Model selection refinement.
*   **(Future Goals)** **Advanced Memory & Learning** (P26), **Proactive Behavior** (Scheduling - P27), **Federated Communication** (Layer 3 - P28+), Enhance Admin AI planning (few-shot examples), implement new Admin AI tools, resource management, advanced collaboration patterns, DB integration, **Full transition to on-demand tool help** (removing static descriptions from prompts - P28+).

## 2. Scope

**In Scope (Completed up to Phase 24):**

*   **Core Backend & Agent Core:** Base functionality, stateful agents (Admin, PM, Worker types).
*   **Admin AI Agent:** Core logic, state machine (`conversation`, `planning`), time/health context, KB search prompt, **framework-driven delegation to PM workflow**.
*   **Project Manager Agent:** Definition, automatic creation per session, prompt for active management, **requires user approval to start**.
*   **Agent Manager & Handlers:** Orchestration, cycle management, interaction handling, failover, DB logging integration, **PM agent auto-creation**, **framework-driven initial task creation via ToolExecutor**.
*   **Workflow Manager (`AgentWorkflowManager`):** Manages agent states and state-specific prompt selection.
*   **State & Session Management:** Team state (runtime), Save/Load (filesystem), **Tasklib data persistence**.
*   **Model Registry (`ModelRegistry`):** Provider/model discovery, filtering.
*   **Automatic Model Selection:** Admin AI startup, dynamic agents.
*   **Performance Tracking (`ModelPerformanceTracker`):** Tracks metrics, saves to JSON.
*   **Automatic Agent Failover:** Handles provider/model switching.
*   **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI tool calls.
*   **Tooling (XML Format):**
    *   `FileSystemTool`: Read/Write/List/FindReplace/Mkdir/Delete.
    *   `GitHubTool`: List Repos/Files (Recursive), Read File.
    *   `ManageTeamTool`: Agent/Team CRUD, Assign, List, Get Details.
    *   `WebSearchTool`: Tavily API w/ DDG Scraping Fallback.
    *   `SendMessageTool`: Local agent communication (Layer 2).
    *   `SystemHelpTool`: Get Time, Search Logs, Get Tool Info.
    *   `KnowledgeBaseTool`: Save/Search knowledge (DB).
    *   `ProjectManagementTool`: Add, list, modify, complete tasks using `tasklib` (assigns via CLI tags/UDA).
    *   `ToolInformationTool`: Get detailed tool usage.
*   **Tool Executor (`ToolExecutor`):** Dynamic tool discovery, schema generation, execution with **authorization checks**.
*   **Configuration:** `config.yaml`, `.env`, `prompts.json`.
*   **Session Persistence:** Save/Load state (filesystem).
*   **Human UI:** Dynamic updates, Session management, **Separated Chat and Internal Comms views (UI Refactor P22)**, Config View, Fixed message interleaving (P22), Increased internal history limit (P22), **Project approval workflow**.
*   **WebSocket Communication:** Real-time updates.
*   **Sandboxing & Shared Workspace:** Implemented.
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with retries/failover.
*   **Helper Files & Logging:** Maintained.
*   **Ollama Proxy Integration:** Optional, managed proxy.
*   **Database Integration (Phase 21):** SQLite backend, SQLAlchemy models, interaction/agent logging, knowledge save/search tools.
*   **Communication Layers:** Layer 1 (User<->Admin) & Layer 2 (Admin<->Local Agents) logic implemented.
*   **Agent Thoughts:** Capture via `<think>` tag and save to KB.

**In Scope (Phase 25 - Current):**

*   **Agent Logic Refinement:**
    *   Investigating `MODEL_TIER` & `MODEL_COST` interference, need to refactor is likely.
    *   Investigate and fix other potential logic issues (looping, placeholders, targeting).
*   **Memory/Learning Foundation:**
    *   Refine agent thought capture/usage.
*   **Governance Layer Foundation:**
    *   Define a structure for representing core principles or a 'constitution'.
    *   Implement mechanisms to inject relevant principles into agent prompts.
    *   *Initial Goal:* Focus on structure and injection, not complex enforcement. Explore Admin AI use during planning/review.

**Out of Scope (Deferred to Future Phases 26+):**

*   **Phase 26: Advanced Memory & Learning.** (Feedback Loop, Learned Principles).
*   **Phase 27: Proactive Behavior.** (Scheduling, Goal Management).
*   **Phase 28+: Federated Communication (Layer 3).** (External Admin AI interaction - protocol, security, discovery).
*   **Phase 29+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, **Full transition to on-demand tool help** (removing static descriptions from prompts), etc.

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **Database:** `SQLAlchemy` (Core, Asyncio), `aiosqlite` (for SQLite driver)
*   **Task Management:** `tasklib` (Python Taskwarrior library) %% Added P23
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Configuration:** YAML (`PyYAML`), `.env` (`python-dotenv`), JSON (`prompts.json`)
*   **Tooling APIs:** `tavily-python`
*   **Parsing:** `BeautifulSoup4` (HTML), `re`, `html` (XML)
*   **Model Discovery & Management:** Custom `ModelRegistry` class
*   **Performance Tracking:** Custom `ModelPerformanceTracker` class (JSON)
*   **Persistence:** JSON (session state - filesystem), SQLite (interactions, knowledge, thoughts), Taskwarrior files (project tasks via `tasklib`) %% Updated P25
*   **Optional Proxy:** Node.js, Express, node-fetch
*   **Data Handling/Validation:** Pydantic (via FastAPI)
*   **Local Auto Discovery** nmap
*   **Logging:** Standard library `logging`

## 4. Development Phases & Milestones

**Phase 1-23 (Completed)**
*   [X] Core Functionality, Dynamic Agent/Team Mgmt, Refactoring, Provider/Model Discovery & Selection, Failover, Key Management, Prompt Centralization, Ollama Proxy, XML Tooling, Auto-Selection (Dyn), Robust Agent ID Handling, Structured Planning, Context Optimization & FS Tools, GitHub Recursive List, ManageTeam Details, WebSearch API Fallback, SystemHelp Tool, Admin Time Context, **Memory Foundation (DB & KB Tool)**, **UI Layer Refactor & Workflow Refinements**, **Project Manager Agent & Tasklib Integration**.

**Phase 24: Admin AI State Machine & Framework-Driven Project Init (Completed)**
*   **Goal:** Refactor Admin AI workflow into distinct states and automate project/PM creation by the framework, requiring user approval.
*   [X] Add Admin AI states (`conversation`, `planning`) and `AgentWorkflowManager` for state logic.
*   [X] Create state-specific prompts (`prompts.json`) used by `AgentWorkflowManager`.
*   [X] Require `<title>` tag in Admin AI plans.
*   [X] Implement framework logic (`CycleHandler`, `AgentManager`) to intercept Admin AI plans, extract title, automatically create PM agent and initial project task (via `ToolExecutor` calling `ProjectManagementTool`), assign PM via tags/UDA, and transition Admin AI state.
*   [X] Implement UI notification for **user approval** of project start.
*   [X] Implement API endpoint (`/approve`) and logic in `AgentManager` to schedule PM agent upon approval.
*   [X] Update `SessionManager` to save/load Admin AI state.
*   [X] Fix bootstrap agent initialization fallback logic (`agent_lifecycle.py`).
*   [X] Correct `AgentManager` check for initial task creation result.

**Phase 25: Agent Logic, Taskwarrior Refinement & Governance (Current)**
    *   **Goal:** Address known agent logic issues, stabilize Taskwarrior integration, implement basic Governance Layer, and refine thought capture.
    *   [~] Investigate and fix `MODEL_TIER` and `MODEL_COST` switching issues (Partially addressed: Model selection logic refactored to include performance and then model size (parameter count), improving clarity and introducing new selection priority: Tier -> Size (larger preferred) -> Performance Score -> ID).
    *   [X] Address agent logic issues: "PM multi-tool calls" now supported via sequential execution of all detected tools in a single turn, with results aggregated and fed back to the agent. (Other items like looping, placeholders, targeting still pending).
    *   [X] Refine agent thought capture/usage: Implemented smarter keyword generation for thoughts saved to Knowledge Base and added a `search_agent_thoughts` action to `KnowledgeBaseTool`.
    *   [X] Implement basic Governance Layer: Principles are now loaded from `governance.yaml` (PyYAML added as dependency) and injected into agent system prompts based on applicability.
    *   [X] Implement `ToolInformationTool`. (Moved from previous phase as it was completed in P25)
    *   [X] Implement authorization checks in `ToolExecutor`. (Moved from previous phase as it was completed in P25)
    *   [X] Inject system health report into Admin AI context. (Moved from previous phase as it was completed in P25)
    *   [X] Comprehensive unit tests added for model selection, `num_parameters` handling in `ModelRegistry`, governance loading/injection, and multi-tool call processing.
*   **Phase 26:** Advanced Memory & Learning (Feedback Loop, Learned Principles, Advanced Thought Usage).
*   **Phase 27:** Proactive Behavior (Scheduling, Goal Management).
*   **Phase 28+:** Federated Communication (Layer 3 - External Admin AI Interaction).
*   **Phase 29+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, **Full transition to on-demand tool help** (removing static descriptions from prompts), etc.

## 3. Technology Stack
*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **Database:** `SQLAlchemy` (Core, Asyncio), `aiosqlite` (for SQLite driver)
*   **Task Management:** `tasklib` (Python Taskwarrior library)
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Configuration:** YAML (`PyYAML`), `.env` (`python-dotenv`), JSON (`prompts.json`), `governance.yaml` (`PyYAML`)
*   **Tooling APIs:** `tavily-python`
*   **Parsing:** `BeautifulSoup4` (HTML), `re`, `html` (XML)
*   **Model Discovery & Management:** Custom `ModelRegistry` class (now includes model parameter size) <!-- Updated P25 -->
*   **Performance Tracking:** Custom `ModelPerformanceTracker` class (JSON)
*   **Persistence:** JSON (session state - filesystem), SQLite (interactions, knowledge, thoughts), Taskwarrior files (project tasks via `tasklib`)
*   **Optional Proxy:** Node.js, Express, node-fetch
*   **Data Handling/Validation:** Pydantic (via FastAPI)
*   **Local Auto Discovery** nmap
*   **Logging:** Standard library `logging`

## 4. Development Phases & Milestones

**Phase 1-23 (Completed)**
*   [X] Core Functionality, Dynamic Agent/Team Mgmt, Refactoring, Provider/Model Discovery & Selection, Failover, Key Management, Prompt Centralization, Ollama Proxy, XML Tooling, Auto-Selection (Dyn), Robust Agent ID Handling, Structured Planning, Context Optimization & FS Tools, GitHub Recursive List, ManageTeam Details, WebSearch API Fallback, SystemHelp Tool, Admin Time Context, **Memory Foundation (DB & KB Tool)**, **UI Layer Refactor & Workflow Refinements**, **Project Manager Agent & Tasklib Integration**.

**Phase 24: Admin AI State Machine & Framework-Driven Project Init (Completed)**
*   **Goal:** Refactor Admin AI workflow into distinct states and automate project/PM creation by the framework, requiring user approval.
*   [X] Add Admin AI states (`conversation`, `planning`) and `AgentWorkflowManager` for state logic.
*   [X] Create state-specific prompts (`prompts.json`) used by `AgentWorkflowManager`.
*   [X] Require `<title>` tag in Admin AI plans.
*   [X] Implement framework logic (`CycleHandler`, `AgentManager`) to intercept Admin AI plans, extract title, automatically create PM agent and initial project task (via `ToolExecutor` calling `ProjectManagementTool`), assign PM via tags/UDA, and transition Admin AI state.
*   [X] Implement UI notification for **user approval** of project start.
*   [X] Implement API endpoint (`/approve`) and logic in `AgentManager` to schedule PM agent upon approval.
*   [X] Update `SessionManager` to save/load Admin AI state.
*   [X] Fix bootstrap agent initialization fallback logic (`agent_lifecycle.py`).
*   [X] Correct `AgentManager` check for initial task creation result.

**Phase 25: Agent Logic, Taskwarrior Refinement & Governance Layer Foundation (Completed/Advanced)**
    *   **Goal:** Address known agent logic issues, stabilize Taskwarrior integration, implement basic Governance Layer, refine thought capture, and enhance model selection.
    *   [X] Enhanced Model Selection Logic: Model selection logic refactored. New priority: Tier -> Size (larger preferred, `num_parameters` discovered for OpenRouter/Ollama) -> Performance Score -> ID.
    *   [X] Sequential Multi-Action Execution: Agents can have multiple tools execute sequentially in one "turn", with results aggregated and fed back.
    *   [X] Refined Thought Usage: Implemented smarter keyword generation (using `src/utils/text_utils.py::extract_keywords_from_text`) for thoughts saved to Knowledge Base. (Assuming `search_agent_thoughts` was part of this or an earlier completion).
    *   [X] Governance Layer Foundation: Principles loaded from `governance.yaml`. Global injection into agent prompts has been REMOVED; principles are now intended for use by the Constitutional Guardian (CG) agent.
    *   [X] `ToolInformationTool` implemented.
    *   [X] Authorization checks in `ToolExecutor` implemented.
    *   [X] System health report injected into Admin AI context.
    *   [X] Pydantic forward references for `CycleContext` and `WorkflowResult` updated for v1/v2 compatibility.
    *   [X] Corrected `manager.settings` AttributeErrors by using global `settings` object where appropriate.
    *   [X] Refined `admin_ai_startup_prompt` for more explicit state change instructions.
    *   [X] Removed `TEAMS_CONFIG` and `allowed_sub_agent_models` handling from `ConfigManager` as they were unused.
    *   [X] Comprehensive unit tests added/updated for model selection, `num_parameters` handling, governance loading, and multi-tool call processing.

**Phase 26: Constitutional Guardian - Backend Implementation (Newly Added)**
    *   **Goal:** Implement the backend infrastructure for a Constitutional Guardian (CG) agent to review agent outputs.
    *   [X] CG Agent Definition: `constitutional_guardian_ai` configured by user (via `config.yaml`), specific `cg_system_prompt` added to `prompts.json` detailing its review task and output format (`<OK/>` or `<CONCERN>details</CONCERN>`). Prompt refined to expect user message as text to review.
    *   [X] Core Logic in `AgentCycleHandler`: Implemented `_get_cg_verdict` for direct LLM call to CG (using `stream_completion`). `run_cycle` modified to intercept final agent text outputs, call CG, and if concern is raised, original agent status set to `AGENT_STATUS_AWAITING_USER_REVIEW_CG`, its output is paused, and a `cg_concern` UI message is generated.
    *   [X] Agent State for CG: Added `cg_original_text`, `cg_concern_details`, `cg_original_event_data`, `cg_awaiting_user_decision` attributes to `Agent` class (`core.py`). New constants `CONSTITUTIONAL_GUARDIAN_AGENT_ID`, `AGENT_STATUS_AWAITING_CG_REVIEW` (now unused, effectively replaced by direct call), `AGENT_STATUS_AWAITING_USER_REVIEW_CG` added.
    *   [X] User Decision Handling (Backend): Implemented `AgentManager` methods (`resolve_cg_concern_approve`, `resolve_cg_concern_stop`, `resolve_cg_concern_retry`) to process user's response to a CG concern.
    *   [X] Scheduler Adjustment: `NextStepScheduler` modified to ensure agents awaiting user review (`AGENT_STATUS_AWAITING_USER_REVIEW_CG` with `cg_awaiting_user_decision = True`) are not prematurely idled.
    *   **Note:** UI and API endpoint implementation for user interaction with CG concerns are required for full functionality and are external to these backend changes.

*   **Phase 27:** Advanced Memory & Learning (Feedback Loop, Learned Principles, Advanced Thought Usage).
*   **Phase 28:** Proactive Behavior (Scheduling, Goal Management).
*   **Phase 29+:** Federated Communication (Layer 3 - External Admin AI Interaction).
*   **Phase 30+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, **Full transition to on-demand tool help** (removing static descriptions from prompts), etc.
*   Enhanced Model Selection (Tier -> Size -> Performance -> ID)
*   Sequential Multi-Action Execution for tools.
*   Constitutional Guardian (CG) Agent - Backend Foundation.

**Out of Scope (Deferred to Future Phases 27+):**

*   Global Governance Principle Injection (Removed, replaced by CG review).
*   `TEAMS_CONFIG` and `allowed_sub_agent_models` (Removed from `ConfigManager`).
*   **Phase 27: Advanced Memory & Learning.** (Feedback Loop, Learned Principles).
*   **Phase 28: Proactive Behavior.** (Scheduling, Goal Management).
*   **Phase 29+: Federated Communication (Layer 3).** (External Admin AI interaction - protocol, security, discovery).
*   **Phase 30+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, **Full transition to on-demand tool help** (removing static descriptions from prompts), etc.
<!-- # END OF FILE helperfiles/PROJECT_PLAN.md -->
