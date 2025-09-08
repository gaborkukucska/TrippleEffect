<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.40
**Date:** 2025-08-30

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator/initiator, operating via a state machine (`conversation`, `planning`). *(Completed, Refined in P24)*
*   Implement a **Project Manager** agent, auto-created per session, to handle detailed task tracking and team coordination using `tasklib`, activated after **user approval**. *(Completed in P23, Activation updated in P24)*
*   Enable dynamic agent/team creation and deletion *in memory*. *(Completed)*
*   Implement **standardized communication layers**: *(Completed - UI Refactor Done)*
    *   **Layer 1:** User <-> Admin AI Interaction. *(Completed)*
    *   **Layer 2:** Admin AI <-> Local Dynamic Agents (within the same team or Admin). *(Completed)*
    *   **Layer 3:** Admin AI <-> External Authorized Admin AIs / Groups (Federated Communication). *(Future Goal - Phase 29+)*
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
*   Implement **automatic model selection** for dynamic agents if not specified. *(Refined in P26b)*
*   Implement **robust agent ID/persona handling** for `send_message` (Layer 2). *(Completed)*
*   Implement **structured planning phase** for Admin AI, followed by **framework-driven delegation to Project Manager** (requires user approval). *(Completed, Updated in P24)*
*   Enhance `FileSystemTool` with **find/replace, mkdir, delete**. *(Completed)*
*   Enhance `GitHubTool` with **recursive listing**. *(Completed)*
*   Enhance `ManageTeamTool` with **agent detail retrieval**. *(Completed)*
*   Make `WebSearchTool` more robust with **API fallback**. *(Completed)*
*   Implement `SystemHelpTool` for Admin AI **time awareness, log searching**. *(Completed)*
*   Implement `ProjectManagementTool` using `tasklib` for task tracking (assigns via tags/UDA CLI workaround). *(Completed in P23, Refined in P24/P25)*
*   Implement `KnowledgeBaseTool` for saving/searching distilled knowledge and agent thoughts. *(Completed in P21, Thoughts added P25)*
*   Implement `ToolInformationTool` for retrieving detailed tool usage. *(Completed in P25)*
*   Inject **current time context** and **system health report** into Admin AI LLM calls. *(Completed)*
*   Implement **Memory Foundation** using a database (SQLite) for interaction logging and knowledge/thought storage. *(Completed in P21, Enhanced P25)*
*   Refactor UI for Communication Layers and refine Admin AI memory usage prompts. *(Completed in P22)*
*   Fix UI message interleaving issue during concurrent streaming. *(Completed in P22)*
*   Increase internal comms message history limit. *(Completed in P22)*
*   Implement Local API Round-Robin for model selection and address various stability/logic issues. *(Completed in P26b)*
*   **Implement Advanced Agent Health Monitoring System** with comprehensive loop detection, recovery, and XML validation capabilities. *(Completed in P27)*
*   **(Future Goals)** **Advanced Memory & Learning** (P28), **Proactive Behavior** (Scheduling - P29), **Federated Communication** (Layer 3 - P30+), Enhance Admin AI planning (few-shot examples), implement new Admin AI tools, resource management, advanced collaboration patterns, DB integration, **Full transition to on-demand tool help** (removing static descriptions from prompts - P31+).

## 2. Scope

**In Scope (Completed up to Phase 27):**

*   **Core Backend & Agent Core:** Base functionality, stateful agents (Admin, PM, Worker types).
*   **Admin AI Agent:** Core logic, state machine (`conversation`, `planning`), time/health context, KB search prompt, **framework-driven delegation to PM workflow**.
*   **Project Manager Agent:** Definition, automatic creation per session, prompt for active management, **requires user approval to start**.
*   **Agent Manager & Handlers:** Orchestration, cycle management, interaction handling, failover, DB logging integration, **PM agent auto-creation**, **framework-driven initial task creation via ToolExecutor**, **Local API Round-Robin Index Management**.
*   **Advanced Agent Health Monitoring (P27):** Comprehensive Constitutional Guardian system with intelligent agent behavior monitoring, loop detection, and recovery capabilities.
*   **Cycle Components Architecture (P27):** Modular system including AgentHealthMonitor, XMLValidator, ContextSummarizer, NextStepScheduler, PromptAssembler, and OutcomeDeterminer.
*   **Workflow Manager (`AgentWorkflowManager`):** Manages agent states and state-specific prompt selection.
*   **State & Session Management:** Team state (runtime), Save/Load (filesystem), **Tasklib data persistence**.
*   **Model Registry (`ModelRegistry`):** Provider/model discovery, filtering.
*   **Automatic Model Selection:** Admin AI startup, dynamic agents, **API-first Round-Robin for local providers**.
*   **Performance Tracking (`ModelPerformanceTracker`):** Tracks metrics, saves to JSON.
*   **Automatic Agent Failover:** Handles provider/model switching.
*   **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI tool calls.
*   **Enhanced Tooling (XML Format):**
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
*   **Configuration:** `config.yaml`, `.env`, `prompts.json`, `governance.yaml`.
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
*   **Constitutional Guardian (CG) Agent:** Backend infrastructure for agent output review with enhanced health monitoring.
*   **PM Workflow & UI Interaction Refinements:** Reliability fixes for PM startup and CG concern handling.

**Out of Scope (Deferred to Future Phases):**

*   **Phase 28: Advanced Memory & Learning.** (Feedback Loop, Learned Principles, Context Optimization).
*   **Phase 29: Proactive Behavior.** (Scheduling, Goal Management, Autonomous Planning).
*   **Phase 30+: Federated Communication (Layer 3).** (External Admin AI interaction - protocol, security, discovery).
*   **Phase 31+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, **Full transition to on-demand tool help** (removing static descriptions from prompts), etc.
*   Global Governance Principle Injection (Removed, replaced by CG review).
*   `TEAMS_CONFIG` and `allowed_sub_agent_models` (Removed from `ConfigManager`).

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **Database:** `SQLAlchemy` (Core, Asyncio), `aiosqlite` (for SQLite driver)
*   **Task Management:** `tasklib` (Python Taskwarrior library)
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Configuration:** YAML (`PyYAML`), `.env` (`python-dotenv`), JSON (`prompts.json`)
*   **Tooling APIs:** `tavily-python`
*   **Parsing:** `BeautifulSoup4` (HTML), `re`, `html` (XML)
*   **Model Discovery & Management:** Custom `ModelRegistry` class
*   **Performance Tracking:** Custom `ModelPerformanceTracker` class (JSON)
*   **Persistence:** JSON (session state - filesystem), SQLite (interactions, knowledge, thoughts), Taskwarrior files (project tasks via `tasklib`)
*   **Optional Proxy:** Node.js, Express, node-fetch
*   **Data Handling/Validation:** Pydantic (via FastAPI)
*   **Local Auto Discovery** nmap
*   **Logging:** Standard library `logging`

## 4. Development Phases & Milestones

**Phase 1-26b (Completed)**
*   [X] Core Functionality, Dynamic Agent/Team Mgmt, Refactoring, Provider/Model Discovery & Selection, Failover, Key Management, Prompt Centralization, Ollama Proxy, XML Tooling, Auto-Selection (Dyn), Robust Agent ID Handling, Structured Planning, Context Optimization & FS Tools, GitHub Recursive List, ManageTeam Details, WebSearch API Fallback, SystemHelp Tool, Admin Time Context, **Memory Foundation (DB & KB Tool)**, **UI Layer Refactor & Workflow Refinements**, **Project Manager Agent & Tasklib Integration**, **Admin AI State Machine & Framework-Driven Project Init**, **Agent Logic, Taskwarrior Refinement & Governance Layer Foundation**, **Constitutional Guardian - Backend Implementation**, **PM Workflow & UI Interaction Refinements**, **Local API Round-Robin and Stability Fixes**.

**Phase 27: Advanced Agent Health Monitoring System (Completed)**
*   **Goal:** Implement comprehensive agent health monitoring, loop detection, and recovery capabilities to enhance the Constitutional Guardian system.
*   [X] **Agent Health Monitor Implementation:** Comprehensive agent behavior tracking system that monitors patterns, detects problematic loops (empty responses, minimal responses, stuck states), and implements intelligent recovery strategies.
*   [X] **XML Validator Component:** Advanced XML validation and recovery system that automatically detects malformed XML tool calls and attempts intelligent repair using multiple strategies (markdown fence removal, bracket correction, content extraction).
*   [X] **Context Summarizer Implementation:** Intelligent conversation context management system that optimizes context for smaller models by automatically summarizing lengthy conversations while preserving critical information and recent interactions.
*   [X] **Next Step Scheduler Enhancement:** Smart agent reactivation and workflow continuation logic that enables multi-step workflows, handles both successful and failed tool executions, and prevents infinite loops through coordinated empty response detection.
*   [X] **Cycle Components Architecture:** Modular, extensible system for agent cycle management including PromptAssembler for dynamic prompt construction and OutcomeDeterminer for cycle outcome analysis.
*   [X] **Enhanced Loop Detection:** Multi-layered protection system against infinite loops, empty responses, and stuck patterns with coordinated detection between CycleHandler, NextStepScheduler, and AgentHealthMonitor.
*   [X] **Workflow Continuation Support:** Advanced support for multi-step agent workflows that allows agents to continue working through tool execution results, process outputs, and maintain work state until natural completion.
*   [X] **Constitutional Guardian Integration:** Enhanced CG system with comprehensive health monitoring integration, preventing false positive interventions while maintaining robust protection against genuine problematic patterns.
*   [X] **Recovery Strategy Implementation:** Intelligent recovery mechanisms including context clearing, status resets, guidance injection, workflow reminders, and tool availability updates based on specific problem analysis.
*   [X] **Performance Optimization:** Context optimization features including automatic summarization for resource-constrained environments and intelligent token management for improved performance with smaller models.

**Future Goals:**
*   **Phase 28: Advanced Memory & Learning.** (Feedback Loop, Learned Principles, Advanced Context Management, Long-term Memory Systems).
*   **Phase 29: Proactive Behavior.** (Scheduling, Goal Management, Autonomous Planning, Predictive Actions).
*   **Phase 30+: Federated Communication (Layer 3).** (External Admin AI interaction - protocol, security, discovery, Cross-system coordination).
*   **Phase 31+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, **Full transition to on-demand tool help** (removing static descriptions from prompts), Distributed Processing, etc.

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
- **False Positive Prevention**: 30-second cooldown after meaningful actions prevents incorrect health interventions
- **Multi-step Workflow Support**: Agents can now execute complex workflows with proper continuation after tool calls
- **Intelligent Recovery**: Context-aware recovery strategies based on specific problem analysis
- **Performance Optimization**: Context summarization and token management for improved efficiency
- **Robust Error Handling**: Enhanced XML validation with automatic repair capabilities

### PM Agent Loop Resolution History

**Issue History (June 2025):**
PM agents experienced loops in `pm_build_team_tasks` state after successful team creation, failing to proceed to worker agent creation.

**Solutions Attempted:**
1. Modified `pm_build_team_tasks_prompt` to change Step 1 from "Create Project Team" to "Ensure Project Team Exists"
2. Corrected `{project_name_snake_case}` placeholder error that was causing WorkflowManager failures
3. Enhanced Step 1 instructions to explicitly direct agents to proceed to Step 2 after team verification
4. Implemented more explicit action directives for tool listing and worker creation

**Current Status:** Resolved through enhanced prompt engineering and workflow state management improvements integrated into the Phase 27 agent health monitoring system.

---
<!-- # END OF FILE helperfiles/PROJECT_PLAN.md -->
