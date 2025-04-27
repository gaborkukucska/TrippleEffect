<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.24 <!-- Updated Version -->
**Date:** 2025-04-25 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator/initiator. *(Completed, Role Refined in P23)*
*   Implement a **Project Manager** agent, auto-created per session, to handle detailed task tracking and team coordination using `tasklib`. *(Completed in P23)*
*   Enable dynamic agent/team creation and deletion *in memory*. *(Completed)*
*   Implement **standardized communication layers**: *(Completed - UI Refactor Done)*
    *   **Layer 1:** User <-> Admin AI Interaction. *(Completed)*
    *   **Layer 2:** Admin AI <-> Local Dynamic Agents (within the same instance/session). *(Completed)*
    *   **Layer 3:** Admin AI <-> External Authorized Admin AIs / Groups (Federated Communication). *(Future Goal - Phase 27+)*
*   Inject standardized context into dynamic agents' system prompts. *(Completed)*
*   Empower agents to communicate and collaborate autonomously (within Layer 2). *(Completed)*
*   Implement **session persistence** (filesystem), including project task data (`tasklib`). *(Completed, Enhanced in P23)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI` and `Project Manager` agents. *(Completed, Updated in P23)*
*   **Dynamically discover reachable providers** and **available models**. *(Completed)*
*   **Filter discovered models** based on configuration (`MODEL_TIER` env var). *(Completed)*
*   **Automatically select the Admin AI's provider/model at startup**. *(Completed)*
*   Implement provider availability checks and **automatic retries** for transient errors. *(Completed)*
*   **Implement automatic model/provider failover** for agents experiencing persistent errors during generation, following preference tiers (Local -> Free -> Paid). *(Completed)*
*   **Implement basic performance metric tracking** (success rate, latency) per model, persisting data. *(Completed)*
*   Implement a **Human User Interface** reflecting system state and communication layers. *(Completed - Refactored in P22)*
*   Utilize **XML-based tool calling** with **sequential execution**. *(Completed)*
*   Allow tool use in sandboxed or **shared workspaces**. *(Completed)*
*   Implement **automatic project/session context setting**. *(Completed)*
*   Implement **automatic model selection** for dynamic agents if not specified by Admin AI. *(Completed)*
*   Implement **robust agent ID/persona handling** for `send_message` (Layer 2). *(Completed)*
*   Implement **structured planning phase** for Admin AI, followed by **delegation to Project Manager**. *(Completed, Updated in P23)*
*   Enhance `FileSystemTool` with **find/replace, mkdir, delete**. *(Completed)*
*   Enhance `GitHubTool` with **recursive listing**. *(Completed)*
*   Enhance `ManageTeamTool` with **agent detail retrieval**. *(Completed)*
*   Make `WebSearchTool` more robust with **API fallback**. *(Completed)*
*   Implement `SystemHelpTool` for Admin AI **time awareness and log searching**. *(Completed)*
*   Implement `ProjectManagementTool` using `tasklib` for task tracking. *(Completed in P23)*
*   Inject **current time context** into Admin AI LLM calls. *(Completed)*
*   Implement **Memory Foundation** using a database (SQLite) for basic recall and interaction logging. *(Completed in P21)*
*   Refactor UI for Communication Layers and refine Admin AI memory usage prompts. *(Completed in P22)*
*   Fix UI message interleaving issue during concurrent streaming. *(Completed in P22)*
*   Increase internal comms message history limit. *(Completed in P22)*
*   **(Current Goal - Phase 24)** Implement **Governance Layer** (Constitution, Principles).
*   **(Future Goals)** Address agent logic issues (looping, placeholders, targeting - P25), **Advanced Memory & Learning** (P25), **Proactive Behavior** (Scheduling - P26), **Federated Communication** (Layer 3 - P27+), Enhance Admin AI planning (few-shot examples), use tracked performance metrics for ranking, implement new Admin AI tools, resource management, advanced collaboration patterns, DB integration.

## 2. Scope

**In Scope (Completed up to Phase 23):**

*   **Core Backend & Agent Core:** Base functionality.
*   **Admin AI Agent:** Core logic, planning phase, time context, refined KB search prompt, **delegation to PM workflow**.
*   **Project Manager Agent:** Definition, automatic creation per session, prompt for active management.
*   **Agent Manager & Handlers:** Orchestration, cycle management, interaction handling, failover, DB logging integration, **PM agent auto-creation**.
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
    *   `SystemHelpTool`: Get Time, Search Logs.
    *   `KnowledgeBaseTool`: Save/Search knowledge (DB).
    *   `ProjectManagementTool`: Add, list, modify, complete tasks using `tasklib`.
*   **Configuration:** `config.yaml`, `.env`, `prompts.json`.
*   **Session Persistence:** Save/Load state (filesystem).
*   **Human UI:** Dynamic updates, Session management, **Separated Chat and Internal Comms views (UI Refactor P22)**, Config View, Fixed message interleaving (P22), Increased internal history limit (P22).
*   **WebSocket Communication:** Real-time updates.
*   **Sandboxing & Shared Workspace:** Implemented.
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with retries/failover.
*   **Helper Files & Logging:** Maintained.
*   **Ollama Proxy Integration:** Optional, managed proxy.
*   **Database Integration (Phase 21):** SQLite backend, SQLAlchemy models, interaction/agent logging, knowledge save/search tools.
*   **Communication Layers:** Layer 1 (User<->Admin) & Layer 2 (Admin<->Local Agents) logic implemented.

**In Scope (Phase 24 - Current):**

*   **Governance Layer Foundation:**
    *   Define a structure for representing core principles or a 'constitution' (e.g., in a dedicated config file or DB table).
    *   Implement mechanisms to inject relevant principles into agent prompts (initially likely Admin AI).
    *   *Initial Goal:* Focus on defining the structure and injection, not complex enforcement or dynamic adaptation yet. Explore how Admin AI can use this during planning/review.

**Out of Scope (Deferred to Future Phases 25+):**

*   **Phase 25: Advanced Memory & Learning.** (Feedback Loop, Learned Principles, Address Activation/Looping/Placeholder/Targeting Issues identified in P22).
*   **Phase 26: Proactive Behavior.** (Scheduling, Goal Management).
*   **Phase 27+: Federated Communication (Layer 3).** (External Admin AI interaction - protocol, security, discovery).
*   **Phase 28+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, etc.

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
*   **Persistence:** JSON (session state - filesystem), SQLite (interactions, knowledge), Taskwarrior files (project tasks via `tasklib`) %% Updated P23
*   **Optional Proxy:** Node.js, Express, node-fetch
*   **Data Handling/Validation:** Pydantic (via FastAPI)
*   **Logging:** Standard library `logging`

## 4. Proposed Architecture Refinement (Conceptual - Reflects P23 Changes)

*Introduced Project Manager Agent and Tasklib integration.*

```mermaid
graph TD %% Updated P23
    %% Layer Definitions
    subgraph UserLayer [Layer 1: User Interface]
        USER[👨‍💻 Human User]
        subgraph Frontend [🌐 Human UI (Web) - Refactored P22]
             direction LR
             UI_CHAT["**Chat View** <br> (User <-> Admin)✅"]
             UI_INTERNAL["**Internal Comms View** <br>(Admin <-> Agents, Tools, Status)✅"] %% MODIFIED View Name
             UI_SESSIONS["Session View✅"]
             UI_CONFIG["Config View✅"]
             %% Removed Logs View
        end
    end

    subgraph CoreInstance [Layer 2: Local TrippleEffect Instance]
        direction TB
        BackendApp["🚀 FastAPI Backend✅"]

        subgraph Managers ["Management & Orchestration"]
            direction LR
            AGENT_MANAGER["🧑‍💼 Agent Manager ✅"]
            DB_MANAGER["**📦 Database Manager ✅**"]
            STATE_MANAGER["📝 AgentStateManager ✅"]
            SESSION_MANAGER["💾 SessionManager (FS) ✅"]
            PROVIDER_KEY_MGR["🔑 ProviderKeyManager ✅"]
            MODEL_REGISTRY["📚 ModelRegistry ✅"]
            PERF_TRACKER["📊 PerformanceTracker ✅"]
        end

        subgraph Handlers ["Core Logic Handlers"]
            direction LR
            CYCLE_HANDLER["🔄 AgentCycleHandler ✅<br>(Known agent logic issues: looping, placeholder replacement, targeting - See Phase 25)"] %% ANNOTATION Updated Phase
            INTERACTION_HANDLER["🤝 InteractionHandler ✅"]
            FAILOVER_HANDLER["💥 FailoverHandler (Func) ✅"]
        end

        subgraph CoreAgents ["Core & Dynamic Agents"] %% Updated P23
             ADMIN_AI["🤖 Admin AI Agent <br>(Initiator/User Interface)✅"]
             PM_AGENT["🤖 Project Manager Agent <br>(Per Session, Auto-Created)✅"]
             subgraph DynamicTeam [Dynamic Team Example]
                direction LR
                 AGENT_DYN_1["🤖 Worker Agent 1"]
                 AGENT_DYN_N["🤖 ... Worker Agent N"]
             end
        end

        subgraph InstanceTools ["🛠️ Tools"]
            TOOL_EXECUTOR["Executor"]
            TOOL_FS["FileSystem ✅"]
            TOOL_SENDMSG["SendMessage (Local) ✅"]
            TOOL_MANAGE_TEAM["ManageTeam ✅"]
            TOOL_GITHUB["GitHub ✅"]
            TOOL_WEBSEARCH["WebSearch ✅"]
            TOOL_SYSTEMHELP["SystemHelp ✅"]
            TOOL_KNOWLEDGE["**KnowledgeBase ✅**"]
            TOOL_PROJECT_MGMT["**ProjectManagement (Tasklib) ✅**"] %% Added P23
        end

        subgraph InstanceData ["Local Instance Data"] %% Updated P23
            SANDBOXES["📁 Sandboxes"]
            SHARED_WORKSPACE["🌐 Shared Workspace"]
            PROJECT_SESSIONS["💾 Project/Session Files"]
            LOG_FILES["📄 Log Files <br>(Backend Only)"] %% ANNOTATION
            CONFIG_FILES["⚙️ Config (yaml, json, env)"]
            METRICS_FILE["📄 Metrics File"]
            QUARANTINE_FILE["📄 Key Quarantine File"]
            SQLITE_DB["**💾 SQLite DB <br>(Interactions, Knowledge)**"]
            TASKLIB_DATA["**📊 Tasklib Data <br>(Per Session)**"] %% Added P23
        end
    end

    subgraph ExternalServices [External Services]
        LLM_API_SVC["☁️ Ext. LLM APIs"]
        TAVILY_API["☁️ Tavily API"]
        OLLAMA_SVC["⚙️ Local Ollama Svc"]
        OLLAMA_PROXY_SVC["🔌 Optional Ollama Proxy"]
        GITHUB_API["☁️ GitHub API"]
    end

    %% --- Layer 3 (Future) ---
    subgraph FederatedLayer [Layer 3: Federated Instances (Future - Phase 27+)] %% Updated Phase
         ExternalInstance["🏢 External TrippleEffect Instance"]
         ExternalAdminAI["🤖 External Admin AI"]
         ExternalDB["💾 External Instance DB"]
    end

    %% --- Connections ---
    USER -- HTTP/WS --> Frontend;
    Frontend -- HTTP/WS --> BackendApp;

    BackendApp -- Manages --> AGENT_MANAGER;
    BackendApp -- Manages --> DB_MANAGER; %% Via singleton / lifespan

    AGENT_MANAGER -- Uses --> DB_MANAGER;
    AGENT_MANAGER -- Uses --> STATE_MANAGER;
    AGENT_MANAGER -- Uses --> SESSION_MANAGER;
    AGENT_MANAGER -- Uses --> PROVIDER_KEY_MGR;
    AGENT_MANAGER -- Uses --> MODEL_REGISTRY;
    AGENT_MANAGER -- Uses --> PERF_TRACKER;
    AGENT_MANAGER -- Delegates --> CYCLE_HANDLER;
    AGENT_MANAGER -- Delegates --> INTERACTION_HANDLER;
    AGENT_MANAGER -- Triggers --> FAILOVER_HANDLER;
    AGENT_MANAGER -- Manages --> CoreAgents;
    AGENT_MANAGER -- Creates --> PM_AGENT; %% Added P23

    CYCLE_HANDLER -- Runs --> CoreAgents;
    CYCLE_HANDLER -- Logs to --> DB_MANAGER;
    INTERACTION_HANDLER -- Delegates --> TOOL_EXECUTOR;
    INTERACTION_HANDLER -- Updates --> STATE_MANAGER;
    INTERACTION_HANDLER -- Routes Msg --> CoreAgents; %% Includes Admin <-> PM

    TOOL_EXECUTOR -- Executes --> InstanceTools;
    InstanceTools -- Access --> InstanceData;
    InstanceTools -- Interact With --> ExternalServices;
    TOOL_KNOWLEDGE -- Uses --> DB_MANAGER;

    %% Data Persistence %% Updated P23
    SESSION_MANAGER -- R/W --> PROJECT_SESSIONS;
    TOOL_PROJECT_MGMT -- R/W --> TASKLIB_DATA; %% Added P23
    DB_MANAGER -- R/W --> SQLITE_DB;
    PERF_TRACKER -- R/W --> METRICS_FILE;
    PROVIDER_KEY_MGR -- R/W --> QUARANTINE_FILE;
    BackendApp -- Writes --> LOG_FILES;


    %% External Services Connections
    MODEL_REGISTRY -- Discovers --> LLM_API_SVC;
    MODEL_REGISTRY -- Discovers --> OLLAMA_SVC;
    MODEL_REGISTRY -- Discovers --> OLLAMA_PROXY_SVC;
    CoreAgents -- via LLM Providers --> LLM_API_SVC;
    CoreAgents -- via LLM Providers --> OLLAMA_SVC;
    CoreAgents -- via LLM Providers --> OLLAMA_PROXY_SVC;
    TOOL_WEBSEARCH -- Calls --> TAVILY_API;
    TOOL_GITHUB -- Calls --> GITHUB_API;

    %% Federated Layer Connections (Dashed lines for future)
    BackendApp -.->|External API Calls| ExternalInstance;
    ExternalInstance -.->|Callbacks / API Calls| BackendApp;

```

## 5. Development Phases & Milestones

**Phase 1-22 (Completed)**
*   [X] Core Functionality, Dynamic Agent/Team Mgmt, Refactoring, Provider/Model Discovery & Selection, Failover, Key Management, Prompt Centralization, Ollama Proxy, XML Tooling, Auto-Selection (Dyn), Robust Agent ID Handling, Structured Planning, Context Optimization & FS Tools, GitHub Recursive List, ManageTeam Details, WebSearch API Fallback, SystemHelp Tool, Admin Time Context, **Memory Foundation (DB & KB Tool)**, **UI Layer Refactor & Workflow Refinements**.

**Phase 23: Project Manager Agent & Tasklib Integration (Completed)**
*   **Goal:** Introduce a dedicated Project Manager agent to handle task tracking and coordination, improving Admin AI reliability and workflow structure.
*   [X] Add `tasklib` dependency.
*   [X] Create `ProjectManagementTool` with `add_task`, `list_tasks`, `modify_task`, `complete_task` actions, storing data per session.
*   [X] Define `project_manager_agent` in `config.yaml` with appropriate persona and instructions.
*   [X] Implement automatic creation of `pm_{project}_{session}` agent in `AgentManager.save_session`.
*   [X] Update Admin AI prompts (`prompts.json`) to delegate execution to the PM agent after planning.
*   [X] Update PM agent prompt (`config.yaml`) to encourage active monitoring and follow-up.
*   [X] Fix `ToolParameter` definition/usage bug in `base.py` and tool files.

**Phase 24: Admin AI State Machine & Framework-Driven Project Init (Completed)**
*   **Goal:** Refactor Admin AI workflow into distinct states and automate project/PM creation by the framework.
*   [X] Add Admin AI states (`conversation`, `planning`) and management logic (`Agent`, `CycleHandler`, `constants`).
*   [X] Create state-specific prompts (`prompts.json`) for Admin AI (`admin_ai_conversation_prompt`, `admin_ai_planning_prompt`).
*   [X] Require `<title>` tag in Admin AI plans.
*   [X] Implement framework logic (`CycleHandler`, `AgentManager`) to intercept Admin AI plans, extract title, automatically create project task (via `tasklib`) and PM agent (`pm_{project_title}_{session_id}`), assign admins, and transition Admin AI state.
*   [X] Update `SessionManager` to save/load Admin AI state.
*   [X] Remove separate `admin_ai_operational_instructions_local` prompt.
*   [X] Fix bootstrap agent initialization fallback logic (`agent_lifecycle.py`).

**Future Phases (25+) (High-Level)**
*   **Phase 25: Governance Layer & Agent Logic Issues**
    *   **Goal:** Establish a basic system for defining and injecting core principles or a 'constitution'. Address known agent logic issues (looping, placeholders, targeting).
    *   [ ] Define structure for principles (e.g., `governance.yaml` or DB table).
    *   [ ] Implement mechanism to load and inject principles into relevant agent prompts.
    *   [ ] Explore how Admin AI can reference/apply these principles during planning.
    *   [ ] Investigate and fix agent looping, placeholder replacement, and targeting issues noted previously.
*   **Phase 26:** Advanced Memory & Learning (Feedback Loop, Learned Principles).
*   **Phase 27:** Proactive Behavior (Scheduling, Goal Management).
*   **Phase 28+:** Federated Communication (Layer 3 - External Admin AI Interaction).
*   **Phase 29+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, **Full transition to on-demand tool help** (removing static descriptions from prompts), etc.
