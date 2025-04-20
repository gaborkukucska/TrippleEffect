<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.21 <!-- Updated Version -->
**Date:** 2025-04-19 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator. *(Completed)*
*   Enable dynamic agent/team creation and deletion *in memory*. *(Completed)*
*   Implement **standardized communication layers**: *(Partially Completed)*
    *   **Layer 1:** User <-> Admin AI Interaction. *(Completed)*
    *   **Layer 2:** Admin AI <-> Local Dynamic Agents (within the same instance/session). *(Completed)*
    *   **Layer 3:** Admin AI <-> External Authorized Admin AIs / Groups (Federated Communication). *(Future Goal - Phase 25+)*
*   Inject standardized context into dynamic agents' system prompts. *(Completed)*
*   Empower agents to communicate and collaborate autonomously (within Layer 2). *(Completed)*
*   Implement **session persistence** (filesystem). *(Completed)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI`. *(Completed)*
*   **Dynamically discover reachable providers** and **available models**. *(Completed in P12)*
*   **Filter discovered models** based on configuration (`MODEL_TIER` env var). *(Completed in P12)*
*   **Automatically select the Admin AI's provider/model at startup**. *(Completed in P12)*
*   Implement provider availability checks and **automatic retries** for transient errors. *(Completed)*
*   **Implement automatic model/provider failover** for agents experiencing persistent errors during generation, following preference tiers (Local -> Free -> Paid). *(Completed in P13)*
*   **Implement basic performance metric tracking** (success rate, latency) per model, persisting data. *(Completed in P13)*
*   Implement a **Human User Interface** reflecting system state. *(Simplified in P13)*
*   Utilize **XML-based tool calling** with **sequential execution**. *(Restored & Refined in P16)*
*   Allow tool use in sandboxed or **shared workspaces**. *(Completed in P11, Enhanced in P18)*
*   Implement **automatic project/session context setting**. *(Partially Completed in P11)*
*   Implement **automatic model selection** for dynamic agents if not specified by Admin AI. *(Completed in P17)*
*   Implement **robust agent ID/persona handling** for `send_message` (Layer 2). *(Completed in P17)*
*   Implement **structured planning phase** for Admin AI. *(Completed in P17b)*
*   Enhance `FileSystemTool` with **find/replace, mkdir, delete**. *(Completed in P18, P20)*
*   Enhance `GitHubTool` with **recursive listing**. *(Completed in P20)*
*   Enhance `ManageTeamTool` with **agent detail retrieval**. *(Completed in P20)*
*   Make `WebSearchTool` more robust with **API fallback**. *(Completed in P20)*
*   Implement `SystemHelpTool` for Admin AI **time awareness and log searching**. *(Completed in P20)*
*   Inject **current time context** into Admin AI LLM calls. *(Completed in P20)*
*   Implement **Memory Foundation** using a database (SQLite) for basic recall and interaction logging. *(Completed in P21)*
*   **(Future Goals)** Implement **Governance Layer** (Constitution, Principles - P22), **Advanced Memory & Learning** (P23), **Proactive Behavior** (Scheduling - P24), **Federated Communication** (Layer 3 - P25+), Enhance Admin AI planning (few-shot examples), use tracked performance metrics for ranking, implement new Admin AI tools, resource management, advanced collaboration patterns, DB integration, formal project/task management.

## 2. Scope

**In Scope (Completed up to Phase 21):**

*   **Core Backend & Agent Core:** Base functionality. *(Completed)*
*   **Admin AI Agent:** Core logic, planning phase, time context. *(Completed)*
*   **Agent Manager & Handlers:** Orchestration, cycle management, interaction handling, failover, DB logging integration. *(Updated in P21)*
*   **State & Session Management:** Team state (runtime), Save/Load (filesystem). *(Completed)*
*   **Model Registry (`ModelRegistry`):** Provider/model discovery, filtering. *(Completed)*
*   **Automatic Model Selection:** Admin AI startup, dynamic agents. *(Completed)*
*   **Performance Tracking (`ModelPerformanceTracker`):** Tracks metrics, saves to JSON. *(Completed)*
*   **Automatic Agent Failover:** Handles provider/model switching. *(Completed)*
*   **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI tool calls. *(Completed)*
*   **Tooling (XML Format):** *(Updated in P20, P21)*
    *   `FileSystemTool`: Read/Write/List/FindReplace/Mkdir/Delete.
    *   `GitHubTool`: List Repos/Files (Recursive), Read File.
    *   `ManageTeamTool`: Agent/Team CRUD, Assign, List, Get Details.
    *   `WebSearchTool`: Tavily API w/ DDG Scraping Fallback.
    *   `SendMessageTool`: Local agent communication (Layer 2).
    *   `SystemHelpTool`: Get Time, Search Logs.
    *   `KnowledgeBaseTool`: **(NEW)** Save/Search knowledge (DB).
*   **Configuration:** `config.yaml`, `.env` (incl. Tavily Key), `prompts.json` (incl. SystemHelpTool, KnowledgeBaseTool info). *(Updated in P20/P21)*
*   **Session Persistence:** Save/Load state (filesystem). *(Completed)*
*   **Human UI:** Dynamic updates, Session management, Conversation view. *(Completed)*
*   **WebSocket Communication:** Real-time updates. *(Completed)*
*   **Sandboxing & Shared Workspace:** Implemented. *(Completed)*
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with retries/failover. *(Completed)*
*   **Helper Files & Logging:** Maintained. *(Ongoing)*
*   **Ollama Proxy Integration:** Optional, managed proxy. *(Completed)*
*   **Database Integration (Phase 21):** SQLite backend (`data/trippleeffect_memory.db`), SQLAlchemy models, basic interaction logging (user, assistant, tools, errors), knowledge save/search tools. *(Completed in P21)*
*   **Communication Layers:**
    *   Layer 1 (User<->Admin) & Layer 2 (Admin<->Local Agents) implemented. *(Completed)*

**Out of Scope (Deferred to Future Phases 22+):**

*   **Phase 22: Governance Layer.** (Constitution, Principles, Basic Enforcement).
*   **Phase 23: Advanced Memory & Learning.** (Feedback Loop, Learned Principles).
*   **Phase 24: Proactive Behavior.** (Scheduling, Goal Management).
*   **Phase 25+: Federated Communication (Layer 3).** (External Admin AI interaction - protocol, security, discovery).
*   **Phase 26+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource limiting, Advanced DB/Vector Store, GeUI, etc.

## 💻 Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Asynchronous Operations:** `asyncio`
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **Database:** `SQLAlchemy` (Core, Asyncio), `aiosqlite` (for SQLite driver) <!-- Added -->
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Configuration:** YAML (`PyYAML`), `.env` (`python-dotenv`), JSON (`prompts.json`)
*   **Tooling APIs:** `tavily-python`
*   **Parsing:** `BeautifulSoup4` (HTML), `re`, `html` (XML)
*   **Model Discovery & Management:** Custom `ModelRegistry` class
*   **Performance Tracking:** Custom `ModelPerformanceTracker` class (JSON)
*   **Persistence:** JSON (session state - filesystem), SQLite (interactions, knowledge) <!-- Updated -->
*   **Optional Proxy:** Node.js, Express, node-fetch
*   **Data Handling/Validation:** Pydantic (via FastAPI)
*   **Logging:** Standard library `logging`

## 4. Proposed Architecture Refinement (Conceptual - Post Phase 21)

```mermaid
graph TD
    %% Layer Definitions
    subgraph UserLayer [Layer 1: User Interface]
        USER[👨‍💻 Human User]
        Frontend[🌐 Human UI (Web)]
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
            CYCLE_HANDLER["🔄 AgentCycleHandler ✅"]
            INTERACTION_HANDLER["🤝 InteractionHandler ✅"]
            FAILOVER_HANDLER["💥 FailoverHandler (Func) ✅"]
        end

        subgraph CoreAgents ["Core & Dynamic Agents"]
             ADMIN_AI["🤖 Admin AI Agent <br>+ Planning ✅<br>+ Time Context ✅"]
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
            LOG_FILES["📄 Log Files"]
            CONFIG_FILES["⚙️ Config (yaml, json, env)"]
            METRICS_FILE["📄 Metrics File"]
            QUARANTINE_FILE["📄 Key Quarantine File"]
            SQLITE_DB["**💾 SQLite DB <br>(Interactions, Knowledge)**"] %% Added
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
    subgraph FederatedLayer [Layer 3: Federated Instances (Future - Phase 25+)]
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
    CYCLE_HANDLER -- Logs to --> DB_MANAGER; %% Added DB Log
    INTERACTION_HANDLER -- Delegates --> TOOL_EXECUTOR;
    INTERACTION_HANDLER -- Updates --> STATE_MANAGER;
    INTERACTION_HANDLER -- Routes Msg --> CoreAgents;

    TOOL_EXECUTOR -- Executes --> InstanceTools;
    InstanceTools -- Access --> InstanceData;
    InstanceTools -- Interact With --> ExternalServices;
    TOOL_KNOWLEDGE -- Uses --> DB_MANAGER; %% Added DB Tool

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
    %% Choosing REST API as an example transport mechanism
    BackendApp -.->|External API Calls| ExternalInstance;
    ExternalInstance -.->|Callbacks / API Calls| BackendApp;

```

## 5. Development Phases & Milestones

**Phase 1-20 (Completed)**
*   [X] Core Functionality, Dynamic Agent/Team Mgmt, Refactoring, Provider/Model Discovery & Selection, Failover, Key Management, Prompt Centralization, Ollama Proxy, XML Tooling, Auto-Selection (Dyn), Robust Agent ID Handling, Structured Planning, Context Optimization & FS Tools (`find_replace`, `mkdir`, `delete`), GitHub Recursive List, ManageTeam Details, WebSearch API Fallback, SystemHelp Tool, Admin Time Context.

**Phase 21: Memory Foundation (Completed)**
*   **Goal:** Establish database backend (SQLite) and implement basic short-term/long-term memory storage/retrieval for Admin AI. Integrate Phase 19 goals.
*   [X] **Database Setup:** Added `SQLAlchemy`, `aiosqlite`. Defined schema (`Project`, `Session`, `AgentRecord`, `Interaction`, `LongTermKnowledge`). Implemented `DatabaseManager` for async CRUD.
*   [X] **Interaction Logging:** Integrated calls in `AgentManager` and `CycleHandler` to log user messages, agent responses, tool calls/results, errors, and agent records to the DB.
*   [X] **Knowledge Base Tools:** Implemented `KnowledgeBaseTool` with `save_knowledge` and `search_knowledge` actions interacting with the `long_term_knowledge` table.
*   [X] **Admin AI Prompt Update:** Instructed Admin AI on using `knowledge_base` tool for memory search before planning and saving after successful tasks.
*   [X] **Phase 19 Goals Integration:** *(Assuming these were done alongside)* Few-shot examples added, Ranking algorithm implemented/refined.

**Phase 22: Governance Layer (Next)**
*   **Goal:** Define and integrate a system Constitution and Core Operational Principles to guide agent behavior.
*   [ ] **Define Constitution:** Create `CONSTITUTION.md` or similar.
*   [ ] **Define Core Principles:** Formalize and store principles (e.g., in `prompts.json` or DB).
*   [ ] **Prompt Injection:** Inject Constitution/Principles into all agent system prompts.
*   [ ] **Enforcement (Prompt-Based):** Update prompts to mandate adherence.
*   [ ] **(Optional) Basic Checkpoint:** Implement simple, non-LLM checks for sensitive tool calls based on principles.

**Future Phases (23+) (High-Level)**
*   **Phase 23:** Advanced Memory & Learning (Feedback Loop, Learned Principles).
*   **Phase 24:** Proactive Behavior (Scheduling, Goal Management).
*   **Phase 25:** Federated Communication (Layer 3 - External Admin AI Interaction).
*   **Phase 26+:** New Admin AI Tools, LiteLLM Provider, Advanced Collaboration, Resource Limiting, Advanced DB/Vector Store, GeUI, etc.
