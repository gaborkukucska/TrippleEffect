<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.22 <!-- Updated Version -->
**Date:** 2025-04-21 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator. *(Completed)*
*   Enable dynamic agent/team creation and deletion *in memory*. *(Completed)*
*   Implement **standardized communication layers**: *(Partially Completed - Layer 1 & 2 Logic Done, UI Refactor In Progress)*
    *   **Layer 1:** User <-> Admin AI Interaction. *(Completed - UI Refactor in Phase 22)*
    *   **Layer 2:** Admin AI <-> Local Dynamic Agents (within the same instance/session). *(Completed - UI Refactor in Phase 22)*
    *   **Layer 3:** Admin AI <-> External Authorized Admin AIs / Groups (Federated Communication). *(Future Goal - Phase 25+)*
*   Inject standardized context into dynamic agents' system prompts. *(Completed)*
*   Empower agents to communicate and collaborate autonomously (within Layer 2). *(Completed)*
*   Implement **session persistence** (filesystem). *(Completed)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI`. *(Completed)*
*   **Dynamically discover reachable providers** and **available models**. *(Completed)*
*   **Filter discovered models** based on configuration (`MODEL_TIER` env var). *(Completed)*
*   **Automatically select the Admin AI's provider/model at startup**. *(Completed)*
*   Implement provider availability checks and **automatic retries** for transient errors. *(Completed)*
*   **Implement automatic model/provider failover** for agents experiencing persistent errors during generation, following preference tiers (Local -> Free -> Paid). *(Completed)*
*   **Implement basic performance metric tracking** (success rate, latency) per model, persisting data. *(Completed)*
*   Implement a **Human User Interface** reflecting system state. *(UI Refactor in Phase 22)*
*   Utilize **XML-based tool calling** with **sequential execution**. *(Completed)*
*   Allow tool use in sandboxed or **shared workspaces**. *(Completed)*
*   Implement **automatic project/session context setting**. *(Completed)*
*   Implement **automatic model selection** for dynamic agents if not specified by Admin AI. *(Completed)*
*   Implement **robust agent ID/persona handling** for `send_message` (Layer 2). *(Completed)*
*   Implement **structured planning phase** for Admin AI. *(Completed)*
*   Enhance `FileSystemTool` with **find/replace, mkdir, delete**. *(Completed)*
*   Enhance `GitHubTool` with **recursive listing**. *(Completed)*
*   Enhance `ManageTeamTool` with **agent detail retrieval**. *(Completed)*
*   Make `WebSearchTool` more robust with **API fallback**. *(Completed)*
*   Implement `SystemHelpTool` for Admin AI **time awareness and log searching**. *(Completed)*
*   Inject **current time context** into Admin AI LLM calls. *(Completed)*
*   Implement **Memory Foundation** using a database (SQLite) for basic recall and interaction logging. *(Completed in P21)*
*   **(Current Goal - Phase 22)** **Refactor UI for Communication Layers** and refine Admin AI memory usage prompts. Investigate agent activation logic.
*   **(Future Goals)** Implement **Governance Layer** (Constitution, Principles - P23), **Advanced Memory & Learning** (P24), **Proactive Behavior** (Scheduling - P25), **Federated Communication** (Layer 3 - P26+), Enhance Admin AI planning (few-shot examples), use tracked performance metrics for ranking, implement new Admin AI tools, resource management, advanced collaboration patterns, DB integration, formal project/task management.

## 2. Scope

**In Scope (Completed up to Phase 21):**

*   **Core Backend & Agent Core:** Base functionality.
*   **Admin AI Agent:** Core logic, planning phase, time context.
*   **Agent Manager & Handlers:** Orchestration, cycle management, interaction handling, failover, DB logging integration.
*   **State & Session Management:** Team state (runtime), Save/Load (filesystem).
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
*   **Configuration:** `config.yaml`, `.env`, `prompts.json`.
*   **Session Persistence:** Save/Load state (filesystem).
*   **Human UI:** Dynamic updates, Session management, Conversation view, Log View, Config View. *(UI Refactor in P22)*
*   **WebSocket Communication:** Real-time updates.
*   **Sandboxing & Shared Workspace:** Implemented.
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with retries/failover.
*   **Helper Files & Logging:** Maintained.
*   **Ollama Proxy Integration:** Optional, managed proxy.
*   **Database Integration (Phase 21):** SQLite backend, SQLAlchemy models, interaction/agent logging, knowledge save/search tools.
*   **Communication Layers:** Layer 1 (User<->Admin) & Layer 2 (Admin<->Local Agents) logic implemented.

**In Scope (Phase 22):**

*   **UI Refactoring:**
    *   Remove dedicated "Logs" view.
    *   Create new "Internal Comms" view.
    *   Route User<->Admin messages/responses to "Chat" view.
    *   Route Admin<->Agent messages, tool interactions, status, errors to "Internal Comms" view.
    *   Modify `index.html`, `style.css`, `app.js`.
*   **Prompt Refinement:** Update Admin AI prompt in `prompts.json` to emphasize mandatory `knowledge_base search` before planning.
*   **Activation Logic Investigation:** Analyze agent state transitions and reactivation logic (`cycle_handler.py`, `manager.py`) to diagnose potential idle issues (no immediate code changes planned, focus on understanding).

**Out of Scope (Deferred to Future Phases 23+):**

*   **Phase 23: Governance Layer.** (Constitution, Principles, Basic Enforcement).
*   **Phase 24: Advanced Memory & Learning.** (Feedback Loop, Learned Principles, Address Activation Issues if found in P22).
*   **Phase 25: Proactive Behavior.** (Scheduling, Goal Management).
*   **Phase 26+: Federated Communication (Layer 3).** (External Admin AI interaction - protocol, security, discovery).
*   **Phase 27+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource limiting, Advanced DB/Vector Store, GeUI, etc.

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **Database:** `SQLAlchemy` (Core, Asyncio), `aiosqlite` (for SQLite driver)
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Configuration:** YAML (`PyYAML`), `.env` (`python-dotenv`), JSON (`prompts.json`)
*   **Tooling APIs:** `tavily-python`
*   **Parsing:** `BeautifulSoup4` (HTML), `re`, `html` (XML)
*   **Model Discovery & Management:** Custom `ModelRegistry` class
*   **Performance Tracking:** Custom `ModelPerformanceTracker` class (JSON)
*   **Persistence:** JSON (session state - filesystem), SQLite (interactions, knowledge)
*   **Optional Proxy:** Node.js, Express, node-fetch
*   **Data Handling/Validation:** Pydantic (via FastAPI)
*   **Logging:** Standard library `logging`

## 4. Proposed Architecture Refinement (Conceptual - Post Phase 22)

*No major backend architecture changes in Phase 22. UI layer changes conceptually.*

```mermaid
graph TD
    %% Layer Definitions
    subgraph UserLayer [Layer 1: User Interface]
        USER[👨‍💻 Human User]
        subgraph Frontend [🌐 Human UI (Web)]
             direction LR
             UI_CHAT["**Chat View** <br> (User <-> Admin)"]
             UI_INTERNAL["**Internal Comms View** <br>(Admin <-> Agents, Tools, Status)"] %% NEW/MODIFIED
             UI_SESSIONS["Session View"]
             UI_CONFIG["Config View"]
             %% Removed Logs View
        end
    end

    subgraph CoreInstance [Layer 2: Local TrippleEffect Instance]
        direction TB
        BackendApp["🚀 FastAPI Backend"]

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
            CYCLE_HANDLER["🔄 AgentCycleHandler ✅<br>(Investigate Poking Issue)"] %% ANNOTATION
            INTERACTION_HANDLER["🤝 InteractionHandler ✅"]
            FAILOVER_HANDLER["💥 FailoverHandler (Func) ✅"]
        end

        subgraph CoreAgents ["Core & Dynamic Agents"]
             ADMIN_AI["🤖 Admin AI Agent <br>+ Planning ✅<br>+ Time Context ✅<br>+ KB Search Emphasis"] %% ANNOTATION
             subgraph DynamicTeam [Dynamic Team Example]
                direction LR
                 AGENT_DYN_1["🤖 Dynamic Agent 1"]
                 AGENT_DYN_N["🤖 ... Agent N"]
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
        end

        subgraph InstanceData ["Local Instance Data"]
            SANDBOXES["📁 Sandboxes"]
            SHARED_WORKSPACE["🌐 Shared Workspace"]
            PROJECT_SESSIONS["💾 Project/Session Files"]
            LOG_FILES["📄 Log Files <br>(Backend Only)"] %% ANNOTATION
            CONFIG_FILES["⚙️ Config (yaml, json, env)"]
            METRICS_FILE["📄 Metrics File"]
            QUARANTINE_FILE["📄 Key Quarantine File"]
            SQLITE_DB["**💾 SQLite DB <br>(Interactions, Knowledge)**"]
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
    subgraph FederatedLayer [Layer 3: Federated Instances (Future - Phase 26+)]
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

    CYCLE_HANDLER -- Runs --> CoreAgents;
    CYCLE_HANDLER -- Logs to --> DB_MANAGER;
    INTERACTION_HANDLER -- Delegates --> TOOL_EXECUTOR;
    INTERACTION_HANDLER -- Updates --> STATE_MANAGER;
    INTERACTION_HANDLER -- Routes Msg --> CoreAgents;

    TOOL_EXECUTOR -- Executes --> InstanceTools;
    InstanceTools -- Access --> InstanceData;
    InstanceTools -- Interact With --> ExternalServices;
    TOOL_KNOWLEDGE -- Uses --> DB_MANAGER;

    %% Data Persistence
    SESSION_MANAGER -- R/W --> PROJECT_SESSIONS;
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

**Phase 1-21 (Completed)**
*   [X] Core Functionality, Dynamic Agent/Team Mgmt, Refactoring, Provider/Model Discovery & Selection, Failover, Key Management, Prompt Centralization, Ollama Proxy, XML Tooling, Auto-Selection (Dyn), Robust Agent ID Handling, Structured Planning, Context Optimization & FS Tools, GitHub Recursive List, ManageTeam Details, WebSearch API Fallback, SystemHelp Tool, Admin Time Context, **Memory Foundation (DB & KB Tool)**.

**Phase 22: UI Layer Refactor & Workflow Refinements (Current)**
*   **Goal:** Visually separate User<->Admin and Admin<->Agent communication flows in the UI, remove the Logs view, enhance Admin AI memory usage, and investigate agent activation logic.
*   [ ] **UI Refactoring:**
    *   [ ] Modify `templates/index.html`: Remove `#logs-view`, add `#internal-comms-view`, update nav bar (`#bottom-nav`) to replace Logs button with Internal Comms button.
    *   [ ] Modify `static/css/style.css`: Add styling rules for `#internal-comms-view` and its child elements (e.g., `#internal-comms-area`). Ensure consistent layout.
    *   [ ] Modify `static/js/app.js`: Update `switchView` function. Update `handleWebSocketMessage` to route specific message types (`user`, `agent_response` from Admin) to the main Chat view, and other types (`agent_status_update`, internal `agent_response`, `tool_requests`, `tool_results`, `status`, `error`) to the new Internal Comms view.
*   [ ] **Prompt Refinement:**
    *   [ ] Modify `prompts.json`: Update `admin_ai_operational_instructions` to explicitly state that using `knowledge_base` search before planning is mandatory.
*   [ ] **Activation Logic Investigation:**
    *   [ ] Review `AgentCycleHandler`, `AgentManager`, `AgentInteractionHandler` code related to agent status changes (`set_status`), message queuing, and `schedule_cycle` calls.
    *   [ ] Identify conditions under which Admin AI might become idle when it should still be waiting for agent responses.
    *   [ ] *Document findings; implementation of fixes deferred if complex.*

**Future Phases (23+) (High-Level)**
*   **Phase 23:** Governance Layer (Constitution, Principles).
*   **Phase 24:** Advanced Memory & Learning (Feedback Loop, Learned Principles, Address Activation Issues if found in P22).
*   **Phase 25:** Proactive Behavior (Scheduling, Goal Management).
*   **Phase 26:** Federated Communication (Layer 3 - External Admin AI Interaction).
*   **Phase 27+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, etc.
