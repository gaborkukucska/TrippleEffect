<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.13 <!-- Updated Version -->
**Date:** 2025-04-10 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator, interpreting user requests and managing teams/agents dynamically.
*   Enable dynamic agent/team creation and deletion *in memory* via Admin AI commands. *(Completed)*
*   Inject standardized context into dynamic agents' system prompts. *(Completed)*
*   Empower agents to communicate and collaborate autonomously. *(Completed)*
*   Implement **session persistence**. *(Completed)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI`. *(Completed)*
*   Implement **provider availability checks** (reachability, keys/URLs) and **automatic retries with user override**. *(Completed & Enhanced in P12)*
*   **Dynamically discover reachable providers** (Ollama, LiteLLM, etc.) and **discover available models** from them and configured public providers (OpenRouter, etc.). *(Completed in P12)*
*   **Filter discovered models** based on configuration (`MODEL_TIER` env var). *(Completed in P12)*
*   **Automatically select the Admin AI's provider/model at startup** if not explicitly set in `config.yaml`, based on discovered models and preferences. *(Completed in P12)*
*   Refactor core manager logic into specialized classes. *(Completed in P11)*
*   Implement a **Human User Interface** reflecting system state and allowing intervention. *(Completed)*
*   Utilize **XML-based tool calling** with **sequential execution**. *(Completed)*
*   Allow tool use in sandboxed or shared workspaces. *(Completed in P11)*
*   Implement **automatic project/session context setting**. *(Completed in P11)*
*   **(Future Goals)** Enhance Admin AI planning, implement **model performance tracking and ranking**, **auto-select models for dynamic agents based on rank/category**, implement new tools for Admin AI (model selection control, qualitative feedback), resource management (agent limits), advanced collaboration patterns, GeUI, multi-modal inputs, database integration, formal project/task management.

## 2. Scope

**In Scope (Completed up to Phase 12):**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling. *(Completed)*
*   **Agent Core:** Agent class definition, state management, parsing multiple XML tool calls. *(Completed)*
*   **Admin AI Agent:** Primary user request handler, plans tasks, uses `ManageTeamTool` and `SendMessageTool`. Defined in `config.yaml`. *(Completed)*
*   **Agent Manager (`AgentManager`):** Central coordinator, manages agent lifecycle, delegates execution, manages project/session context, uses State/Session managers. *(Refactored in P11)*
*   **Agent Cycle Handler (`AgentCycleHandler`):** Runs agent execution loop, handles events, retries/overrides, tool execution (via InteractionHandler), reactivation logic. *(Created in P11)*
*   **Agent Interaction Handler (`AgentInteractionHandler`):** Processes ManageTeam/SendMessage signals, executes tools passing context. *(Created in P11)*
*   **Agent Prompt Utils (`prompt_utils.py`):** Holds prompt constants and helpers. *(Created in P11)*
*   **Agent State Manager (`AgentStateManager`):** Manages team structures and agent-to-team mappings *in memory*. *(Completed)*
*   **Agent Session Manager (`SessionManager`):** Handles saving and loading of application state. *(Completed)*
*   **Model Registry (`ModelRegistry`):** *(Completed in P12)*
    *   Checks provider reachability (local discovery, config checks).
    *   Discovers models from reachable providers (Ollama, LiteLLM, OpenRouter).
    *   Applies `MODEL_TIER` filtering.
    *   Stores available models.
*   **Automatic Admin AI Model Selection:** *(Completed in P12)*
    *   `AgentManager` selects Admin AI provider/model at startup based on discovery and preferences if not set/valid in `config.yaml`.
*   **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI. *(Completed)*
*   **Tooling:** `BaseTool`, `ToolExecutor`, `FileSystemTool`, `SendMessageTool`, `ManageTeamTool`, `GitHubTool`, `WebSearchTool`. *(Completed/Fixed in P11)*
*   **Configuration (`config.yaml`):** Defines `admin_ai` (provider/model now optional). *(Updated in P12)*
*   **Settings (`settings.py`, `.env`):** Loads bootstrap config, checks provider config, loads API keys/URLs, `MODEL_TIER`. *(Updated in P12)*
*   **Session Persistence:** Saving/Loading full state. *(Completed)*
*   **Project Management (UI):** UI for listing projects/sessions, triggering save/load. *(Completed)*
*   **Human UI:** Dynamic updates, Project/Session management, Conversation view, Override modal. *(Completed)*
*   **WebSocket Communication:** Real-time updates + state + override. *(Completed)*
*   **Sandboxing & Shared Workspace:** Dynamically created. *(Completed/Fixed in P11)*
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with stream error handling and retry/override. *(Completed)*
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`. *(Ongoing)*
*   **Logging:** Console and timestamped file logging. *(Completed)*
*   **Automatic Context:** Default project/session context. *(Completed in P11)*

**Out of Scope (Deferred to Future Phases 13+):**

*   **Phase 13: Performance Tracking & Basic Ranking.** (Track success/failure/latency per model, simple ranking).
*   **Phase 14: Advanced Ranking & Dynamic Agent Auto-Selection.** (Category-based ranking, auto-select for worker agents).
*   **Phase 15: New Admin AI Tools.** (Tool for category-based agent creation, tool for qualitative feedback).
*   LiteLLM Provider implementation (discovery added, provider class needed).
*   Advanced Collaboration Patterns.
*   Advanced Admin AI Intelligence (planning refinement, long-term memory).
*   Resource limiting.
*   Formal Project/Task Management System.
*   Database/Vector Store integration.
*   Multi-Team Projects.
*   Agent prompt updates *after* creation (other than team ID).
*   Generative UI (GeUI).
*   Advanced I/O, Voice Control.
*   Advanced Auth/Multi-User.
*   Automated testing suite.
*   UI Refinements (Chat scrolling, WS message handling).

## 3. Technology Stack

*   (No changes here)

## 4. Proposed Architecture Refinement (Conceptual - Post Phase 12)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_CHAT_VIEW["Chat & Agents View ✅"]
        UI_LOGS_VIEW["System Logs View ✅"]
        UI_SESSION_VIEW["Project/Session View ✅"]
        UI_CONFIG_VIEW["Static Config View ✅<br>(Provider/Model Optional)"]
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend ✅"]
        WS_MANAGER["🔌 WebSocket Manager ✅"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Coordinator)<br>+ Agent Create/Delete ✅<br>+ **Uses ModelRegistry** ✅<br>+ **Auto-Selects Admin AI Model** ✅<br>+ Delegates Cycle Exec ✅<br>+ Manages Context ✅"]
        MODEL_REGISTRY["📚 Model Registry<br>+ Discovers Providers ✅<br>+ Discovers Models ✅<br>+ Filters by Tier ✅<br>+ Stores Reachable/Available ✅"]
        CYCLE_HANDLER["🔄 Agent Cycle Handler ✅"]
        INTERACTION_HANDLER["🤝 Interaction Handler ✅"]
        STATE_MANAGER["📝 AgentStateManager ✅"]
        SESSION_MANAGER["💾 SessionManager ✅"]

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent <br>(Model Auto-Selected?)✅<br>Receives Available Models ✅"]
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1<br>(Uses Available Models) ✅"]
            DYNAMIC_AGENT_N["🤖 Dynamic Agent N"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers <br>(Instantiated by Manager)"]
             PROVIDER_OR["🔌 OpenRouter Provider(s)"]
             PROVIDER_OLLAMA["🔌 Ollama Provider(s)"]
             PROVIDER_OPENAI["🔌 OpenAI Provider(s)"]
             PROVIDER_LITELLM["🔌 LiteLLM Provider(s)<br>(Class TBD)"]
         end

         subgraph Tools
             TOOL_EXECUTOR["🛠️ Tool Executor✅"]
             TOOL_FS["📄 FileSystem Tool ✅"]
             TOOL_SENDMSG["🗣️ SendMessageTool ✅"]
             TOOL_MANAGE_TEAM["🛠️ ManageTeamTool ✅"]
             TOOL_GITHUB["🐙 GitHub Tool ✅"]
             TOOL_WEBSEARCH["🌐 Web Search Tool ✅"]
         end

         SANDBOXES["📁 Sandboxes ✅"]
         PROJECT_SESSIONS["💾 Project/Session Storage ✅"]
         SHARED_WORKSPACE["🌐 Shared Workspace ✅"]
         LOG_FILES["📄 Log Files ✅"]
    end

    subgraph External
        LLM_API_SVC["☁️ Ext. LLM APIs"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        LITELLM_SVC["⚙️ Local LiteLLM Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(AdminAI Optional) ✅"]
        DOT_ENV[".env File <br>(Secrets/URLs/Tier) ✅"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP/WebSocket --> Backend;

    FASTAPI -- Manages/Uses --> AGENT_MANAGER;
    FASTAPI -- Manages/Uses --> MODEL_REGISTRY; # App startup lifespan

    AGENT_MANAGER -- Uses --> MODEL_REGISTRY; # For validation & selection
    AGENT_MANAGER -- Instantiates/Uses --> LLM_Providers;
    AGENT_MANAGER -- Creates/Deletes/Manages --> Agents;
    AGENT_MANAGER -- Delegates --> CYCLE_HANDLER;
    AGENT_MANAGER -- Delegates --> STATE_MANAGER;
    AGENT_MANAGER -- Delegates --> SESSION_MANAGER;
    AGENT_MANAGER -- Instantiates --> INTERACTION_HANDLER;
    AGENT_MANAGER -- Instantiates --> CYCLE_HANDLER;

    MODEL_REGISTRY -- Discovers --> LLM_API_SVC;
    MODEL_REGISTRY -- Discovers --> OLLAMA_SVC;
    MODEL_REGISTRY -- Discovers --> LITELLM_SVC;

    CYCLE_HANDLER -- Runs --> Agents;
    CYCLE_HANDLER -- Delegates --> INTERACTION_HANDLER;
    INTERACTION_HANDLER -- Delegates --> TOOL_EXECUTOR;
    TOOL_EXECUTOR -- Executes --> Tools;

    Backend -- "Writes Logs" --> LOG_FILES;
    SESSION_MANAGER -- Reads/Writes --> PROJECT_SESSIONS;

```

## 5. Development Phases & Milestones

**Phase 1-11 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent Basics, Config Loading, Sandboxing, Basic Tools, LLM Abstraction, UI Enhancements, XML Tool Calling, Static Agent Config UI, Dynamic Agent Management V2 & Refactoring, Provider Error Handling, Session Persistence Logic, UI State/Session Management, Basic Logging, Agent Manager Refactoring, Core Fixes (Context, GitHub, Reactivation), Prompt Refinements.

**Phase 12: Dynamic Discovery & Auto-Selection (Completed)**
*   **Goal:** Automatically discover providers/models, filter them, and auto-select Admin AI model.
*   [X] **Environment:** Added `MODEL_TIER`, `LITELLM_BASE_URL`, `LITELLM_API_KEY` to `.env`.
*   [X] **Model Registry (`model_registry.py`):**
    *   [X] Implemented provider reachability checks (Ollama, LiteLLM local discovery, config check for others).
    *   [X] Implemented model discovery from reachable providers (Ollama tags, LiteLLM /models, OpenRouter /models).
    *   [X] Implemented filtering based on reachability and `MODEL_TIER`.
    *   [X] Added helper methods (`get_available_models_list`, `is_model_available`, etc.).
*   [X] **Settings Integration (`settings.py`):**
    *   [X] Integrated `ModelRegistry`.
    *   [X] Removed reliance on static `allowed_sub_agent_models`.
*   [X] **Agent Manager Integration (`manager.py`):**
    *   [X] Updated `initialize_bootstrap_agents` to use `ModelRegistry` for Admin AI prompt injection.
    *   [X] Added logic to automatically select Admin AI model if not specified/available in config, using preferences and discovered models.
    *   [X] Updated `_create_agent_internal` to validate dynamic agent models against `ModelRegistry`.
*   [X] **Configuration (`config.yaml`):**
    *   [X] Removed `allowed_sub_agent_models` section.
    *   [X] Made `provider`/`model` for Admin AI optional (commented out).
*   [X] **Startup (`main.py`):** Integrated `model_registry.discover_models_and_providers()` call into lifespan.
*   [X] **Helper File Updates:** Updated `PROJECT_PLAN.md` (this file).

**Phase 13: Performance Tracking & Basic Ranking (Next)**
*   **Goal:** Track basic quantitative performance metrics (success rate, latency) for available models to enable future ranking.
*   [ ] **Metrics Storage:** Design data structure and persistence mechanism (e.g., JSON file) for metrics (success_count, failure_count, total_duration per provider/model).
*   [ ] **Tracking Class:** Create `ModelPerformanceTracker` class to manage metrics (load, update, save).
*   [ ] **Integration:**
    *   Modify `AgentCycleHandler` to capture LLM call duration and success/failure status.
    *   Report results to `ModelPerformanceTracker`.
    *   Integrate tracker into `AgentManager`.
*   [ ] **Basic Ranking:** Implement a simple ranking function within the tracker based on success rate and average latency.
*   [ ] **Admin AI Selection Refinement:** Update `AgentManager.initialize_bootstrap_agents` to use the basic ranking from the tracker when auto-selecting Admin AI model.

**Future Phases (14+) (High-Level)**
*   **Phase 14:** Advanced Ranking & Dynamic Agent Auto-Selection.
*   **Phase 15:** New Admin AI Tools (Qualitative Feedback, Category Selection).
*   LiteLLM Provider implementation, Advanced Collaboration, Enhanced Admin AI, Resource Limits, DB/Vector Store, GeUI, etc.

```

Wait for confirmation before proceeding to `FUNCTIONS_INDEX.md`.
