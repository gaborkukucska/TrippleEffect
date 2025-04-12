<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.14 <!-- Updated Version -->
**Date:** 2025-04-11 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator. *(Completed)*
*   Enable dynamic agent/team creation and deletion *in memory*. *(Completed)*
*   Inject standardized context into dynamic agents' system prompts. *(Completed)*
*   Empower agents to communicate and collaborate autonomously. *(Completed)*
*   Implement **session persistence**. *(Completed)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI`. *(Completed)*
*   **Dynamically discover reachable providers** and **available models**. *(Completed in P12)*
*   **Filter discovered models** based on configuration (`MODEL_TIER` env var). *(Completed in P12)*
*   **Automatically select the Admin AI's provider/model at startup**. *(Completed in P12)*
*   Implement provider availability checks and **automatic retries** for transient errors. *(Completed)*
*   **Implement automatic model/provider failover** for agents experiencing persistent errors during generation, following preference tiers (Local -> Free -> Paid). *(Completed in P13)*
*   **Implement basic performance metric tracking** (success rate, latency) per model, persisting data. *(Completed in P13)*
*   **Remove user override mechanism**, relying on automatic failover up to a limit, then setting agent to ERROR state. *(Completed in P13)*
*   Refactor core manager logic into specialized classes. *(Completed in P11)*
*   Implement a **Human User Interface** reflecting system state. *(Simplified in P13)*
*   Utilize **XML-based tool calling** with **sequential execution**. *(Completed)*
*   Allow tool use in sandboxed or shared workspaces. *(Completed in P11)*
*   Implement **automatic project/session context setting**. *(Completed in P11)*
*   **(Future Goals)** Enhance Admin AI planning, **use tracked performance metrics for ranking and automatic model selection** (for Admin AI and dynamic agents), implement new Admin AI tools (category selection, qualitative feedback), resource management, advanced collaboration patterns, database integration, formal project/task management.

## 2. Scope

**In Scope (Completed up to Phase 13):**

*   **Core Backend & Agent Core:** Base functionality. *(Completed)*
*   **Admin AI Agent:** Core logic. *(Completed)*
*   **Agent Manager & Handlers:** Orchestration, cycle management, interaction handling. *(Completed)*
*   **State & Session Management:** Team state, save/load. *(Completed)*
*   **Model Registry (`ModelRegistry`):** Provider/model discovery, filtering. *(Completed in P12)*
*   **Automatic Admin AI Model Selection:** Based on discovery/preferences. *(Completed in P12)*
*   **Performance Tracking (`ModelPerformanceTracker`):** Tracks success/failure/duration per model, saves to JSON. *(Completed in P13)*
*   **Automatic Agent Failover:** Agent switches provider/model on persistent errors based on tiers (Local->Free->Paid), up to `MAX_FAILOVER_ATTEMPTS`. *(Completed in P13)*
*   **User Override Removal:** No more modal prompt for user intervention on errors. *(Completed in P13)*
*   **Dynamic Agent/Team Management:** In-memory CRUD. *(Completed)*
*   **Tooling:** Core tools implemented. *(Completed)*
*   **Configuration:** `config.yaml` (Admin AI optional), `.env` (keys, URLs, tier). *(Completed)*
*   **Session Persistence:** Save/Load state. *(Completed)*
*   **Human UI:** Dynamic updates, Session management, Conversation view. *(Simplified in P13)*
*   **WebSocket Communication:** Real-time updates. *(Completed)*
*   **Sandboxing & Shared Workspace:** Implemented. *(Completed)*
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with retries/failover. *(Completed)*
*   **Helper Files & Logging:** Maintained. *(Ongoing)*
*   **Automatic Context:** Implemented. *(Completed)*

**Out of Scope (Deferred to Future Phases 14+):**

*   **Phase 14: Performance Ranking & Selection.** (Implement ranking algorithm, use ranks for Admin AI & dynamic agent auto-selection).
*   **Phase 15: New Admin AI Tools.** (Category selection, qualitative feedback tool).
*   LiteLLM Provider implementation.
*   Advanced Collaboration Patterns.
*   Advanced Admin AI Intelligence.
*   Resource limiting.
*   Formal Project/Task Management System.
*   Database/Vector Store integration.
*   Multi-Team Projects.
*   Agent prompt updates *after* creation.
*   Generative UI (GeUI).
*   Advanced I/O, Voice Control.
*   Advanced Auth/Multi-User.
*   Automated testing suite.
*   UI Refinements (Chat scrolling, WS message handling).

## 3. Technology Stack

*   (No changes here)

## 4. Proposed Architecture Refinement (Conceptual - Post Phase 13)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_CHAT_VIEW["Chat & Agents View ✅"]
        UI_LOGS_VIEW["System Logs View ✅"]
        UI_SESSION_VIEW["Project/Session View ✅"]
        UI_CONFIG_VIEW["Static Config Info View ✅"] %% Simplified
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend ✅"]
        WS_MANAGER["🔌 WebSocket Manager ✅"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Coordinator)<br>+ Agent Create/Delete ✅<br>+ Uses ModelRegistry ✅<br>+ Auto-Selects Admin AI Model ✅<br>+ **Handles Model Failover** ✅<br>+ Delegates Cycle Exec ✅<br>+ Manages Context ✅"]
        MODEL_REGISTRY["📚 Model Registry✅"]
        PERF_TRACKER["📊 Performance Tracker<br>+ Records Metrics ✅<br>+ Saves/Loads Metrics ✅"] %% Added
        CYCLE_HANDLER["🔄 Agent Cycle Handler<br>+ Handles Events/Retries ✅<br>+ **Triggers Failover** ✅<br>+ **Reports Metrics** ✅"] %% Updated
        INTERACTION_HANDLER["🤝 Interaction Handler ✅"]
        STATE_MANAGER["📝 AgentStateManager ✅"]
        SESSION_MANAGER["💾 SessionManager ✅"]

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent ✅"]
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1✅"]
            DYNAMIC_AGENT_N["🤖 Dynamic Agent N"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers"] %% Status Implicit
             PROVIDER_OR["🔌 OpenRouter"]
             PROVIDER_OLLAMA["🔌 Ollama"]
             PROVIDER_OPENAI["🔌 OpenAI"]
             PROVIDER_LITELLM["🔌 LiteLLM (TBD)"]
         end

         subgraph Tools ["🛠️ Tools"] %% Status Implicit
             TOOL_EXECUTOR["Executor"]
             TOOL_FS["FileSystem"]
             TOOL_SENDMSG["SendMessage"]
             TOOL_MANAGE_TEAM["ManageTeam"]
             TOOL_GITHUB["GitHub"]
             TOOL_WEBSEARCH["WebSearch"]
         end

         SANDBOXES["📁 Sandboxes ✅"]
         PROJECT_SESSIONS["💾 Project/Session Storage ✅"]
         SHARED_WORKSPACE["🌐 Shared Workspace ✅"]
         LOG_FILES["📄 Log Files ✅"]
         METRICS_FILE["📄 Metrics File ✅"] %% Added
    end

    subgraph External %% Status Implicit
        LLM_API_SVC["☁️ Ext. LLM APIs"]
        OLLAMA_SVC["⚙️ Local Ollama Svc"]
        LITELLM_SVC["⚙️ Local LiteLLM Svc"]
        CONFIG_YAML["⚙️ config.yaml"]
        DOT_ENV[".env File"]
    end

    %% --- Connections ---
    USER -- Interacts --> Frontend;
    Frontend -- HTTP/WebSocket --> Backend;

    FASTAPI -- Manages --> AGENT_MANAGER;
    FASTAPI -- Manages --> MODEL_REGISTRY;
    FASTAPI -- Manages --> PERF_TRACKER; # Via AgentManager init

    AGENT_MANAGER -- Uses --> MODEL_REGISTRY;
    AGENT_MANAGER -- Uses --> PERF_TRACKER; # To trigger save
    AGENT_MANAGER -- Instantiates --> LLM_Providers;
    AGENT_MANAGER -- Manages --> Agents;
    AGENT_MANAGER -- Delegates --> CYCLE_HANDLER;
    AGENT_MANAGER -- Delegates --> STATE_MANAGER;
    AGENT_MANAGER -- Delegates --> SESSION_MANAGER;
    AGENT_MANAGER -- Instantiates --> INTERACTION_HANDLER;
    AGENT_MANAGER -- Instantiates --> CYCLE_HANDLER;
    AGENT_MANAGER -- Handles Failover --> AGENT_MANAGER; # Calls self to switch model

    MODEL_REGISTRY -- Discovers --> External;
    PERF_TRACKER -- Reads/Writes --> METRICS_FILE;

    CYCLE_HANDLER -- Runs --> Agents;
    CYCLE_HANDLER -- Delegates --> INTERACTION_HANDLER;
    CYCLE_HANDLER -- Reports Metrics --> PERF_TRACKER;
    CYCLE_HANDLER -- Triggers Failover --> AGENT_MANAGER;

    INTERACTION_HANDLER -- Delegates --> TOOL_EXECUTOR;
    TOOL_EXECUTOR -- Executes --> Tools;

    Backend -- "Writes Logs" --> LOG_FILES;
    SESSION_MANAGER -- Reads/Writes --> PROJECT_SESSIONS;
```

## 5. Development Phases & Milestones

**Phase 1-12 (Completed)**
*   [X] Core Functionality, Dynamic Agent/Team Mgmt, Refactoring, Provider/Model Discovery, Admin AI Auto-Selection.

**Phase 13: Performance Tracking & Automatic Failover (Completed)**
*   **Goal:** Track basic model performance and implement automatic failover without user override.
*   [X] **Performance Tracker (`performance_tracker.py`):** Created class to track success/failure/duration per model. Implemented load/save to JSON (`data/model_performance_metrics.json`). Added basic scoring logic placeholder.
*   [X] **Cycle Handler Integration (`cycle_handler.py`):** Added timing for LLM calls. Reports results (success/failure, duration) to `performance_tracker`. Modified error handling to trigger failover instead of requesting override for persistent errors.
*   [X] **Agent Manager Integration (`manager.py`):** Instantiated `performance_tracker`. Added `handle_agent_model_failover` logic to select next available model respecting tiers (Local->Free->Paid) and `MAX_FAILOVER_ATTEMPTS`. Added `_select_next_available_model` helper. Removed `handle_user_override` and `request_user_override`. Added metrics save call during `cleanup_providers`.
*   [X] **Agent Core (`core.py`):** Removed `AGENT_STATUS_AWAITING_USER_OVERRIDE`.
*   [X] **Frontend Cleanup:** Removed override modal and static config editing from HTML and JS.
*   [X] **Helper File Updates:** Updated `PROJECT_PLAN.md` (this file) and `FUNCTIONS_INDEX.md`.

**Phase 14: Performance Ranking & Selection (Next)**
*   **Goal:** Utilize tracked performance metrics to rank models and automatically select the best ones for Admin AI and dynamic agents.
*   [ ] **Ranking Algorithm:** Refine/implement scoring logic in `ModelPerformanceTracker._calculate_score` and potentially `get_ranked_models`. Consider factors like recency, call volume threshold.
*   [ ] **Admin AI Selection:** Modify `AgentManager.initialize_bootstrap_agents` to use `performance_tracker.get_ranked_models()` when auto-selecting the Admin AI, choosing the top-ranked *available* model matching preferences.
*   [ ] **Dynamic Agent Selection:** Modify `AgentManager._create_agent_internal` (or potentially add a new selection method called by `ManageTeamTool` handler): If the Admin AI requests an agent with a specific *role* or *capability* instead of an exact model, use the performance rankings (potentially filtered by role/category later) to select the best available model.
*   [ ] **(Optional) LiteLLM Provider:** Implement the actual `LiteLLMProvider` class in `src/llm_providers`.

**Future Phases (15+) (High-Level)**
*   **Phase 15:** New Admin AI Tools (Qualitative Feedback, Category Selection).
*   Advanced Collaboration, Enhanced Admin AI, Resource Limits, DB/Vector Store, GeUI, etc.
