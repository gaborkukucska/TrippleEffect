<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.10 <!-- Updated Version -->
**Date:** 2025-04-09 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator, interpreting user requests and managing teams/agents dynamically.
*   **Enable dynamic agent/team creation and deletion *in memory* via Admin AI commands, without requiring application restarts.** *(Completed in Phase 9)*
*   **Inject standardized context (tool descriptions, identity, team info, basic communication/reporting instructions) into all dynamic agents' system prompts by the framework** to ensure consistent capabilities and simplify Admin AI's prompt generation task. *(Completed in Phase 9 / Refinement)*
*   Empower agents to **communicate and collaborate autonomously** within their teams using framework-provided tools (`SendMessageTool`, `ManageTeamTool`), including **reporting results back to the Admin AI**. *(Partially Completed - Basic comms & reporting robust, complex collaboration pending)*
*   Implement **session persistence**, capturing the state, histories, and **configurations of dynamically created agents** for reloading. *(Completed in Phase 9, handled by SessionManager)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI` and defining **allowed models/providers** for dynamic agent creation. *(Completed in Phase 9)*
*   Implement **provider availability checks** based on `.env` configuration and **automatic retries with user override** for temporary stream errors. *(Completed in Phase 9 / Refinement)*
*   Refactor core manager logic into `AgentManager`, `AgentStateManager`, and `SessionManager` for better maintainability. *(Completed in Phase 9)*
*   Implement a **Human User Interface** that dynamically reflects the current agent/team structure (via WebSockets), manages Projects/Sessions, and allows user intervention on persistent errors (e.g., Provider/Model override). *(Completed in Phase 10)*
*   Utilize the **XML-based tool calling mechanism** for all agent actions, supporting **sequential execution of multiple tool calls** within a single agent turn. *(Completed / Refined in Phase 9)*
*   Allow agents to utilize tools within sandboxed environments. *(Completed)*
*   *(Future Goals)* Enhance Admin AI planning, resource management (agent limits), advanced collaboration patterns, dynamic provider management, GeUI, multi-modal inputs, voice control, **implicit Admin AI status updates**, **direct Admin AI sandbox access (read-only initially)**, **formal project/task management**, **database/vector store for shared project memory and Admin AI long-term memory**.

## 2. Scope

**In Scope (Completed up to Phase 10 & Planned for Future):**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling. *(Completed)*
*   **Agent Core:** Agent class definition, state management, **parsing multiple XML tool calls**. *(Completed)*
*   **Admin AI Agent:** Primary user request handler, plans tasks, uses `ManageTeamTool` and `SendMessageTool`. Defined in `config.yaml`. *(Completed)*
*   **Agent Manager (`AgentManager`):** Central orchestrator, manages agent lifecycle, routes messages, handles tool calls & errors, injects standard prompts, uses State/Session managers. *(Completed)*
*   **Agent State Manager (`AgentStateManager`):** Manages team structures and agent-to-team mappings *in memory*. *(Completed)*
*   **Agent Session Manager (`SessionManager`):** Handles saving and loading of application state (dynamic agents, histories, teams) to/from JSON files. *(Completed)*
*   **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI and `AgentStateManager`. *(Completed)*
*   **Tooling:** `BaseTool`, `ToolExecutor`, `FileSystemTool`, `SendMessageTool` (robust), `ManageTeamTool`. *(Completed)*
*   **Configuration (`config.yaml`):** Defines `admin_ai`, defines `allowed_sub_agent_models`. *(Completed)*
*   **Settings (`settings.py`):** Loads bootstrap config, `allowed_sub_agent_models`, checks provider config. *(Completed)*
*   **Session Persistence:** Saving/Loading full state including dynamic agent configs, histories, teams. *(Completed)*
*   **Project Management (UI):** UI for listing projects/sessions, triggering save/load. *(Completed in P10)*
*   **Human UI:** *(Completed in P10)*
    *   Dynamically updates agent/team displays via WebSockets (`agent_added`, `agent_deleted`, `agent_status_update`).
    *   Project/Session management UI.
    *   Conversation view.
    *   Modal dialog for user override.
*   **WebSocket Communication:** Real-time streaming + state updates (`agent_added`, etc.) + user override. *(Completed)*
*   **Basic Sandboxing:** Created dynamically for agents. *(Completed)*
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with stream error handling and retry/override. *(Completed)*
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`. *(Ongoing)*
*   **Logging:** Basic console and timestamped file logging. *(Completed in P10)*

**Out of Scope (Deferred to Future Phases 11+):**

*   Google LLM Provider.
*   ConfigTool (Replaced by dynamic management).
*   Dynamic changes via `config.yaml` (post-startup).
*   Dynamic LLM Provider *Type* Management.
*   **Advanced Collaboration:** Complex delegation, conflict resolution, hierarchy, synchronous operations.
*   **Advanced Admin AI Intelligence:** Planning refinement based on failures, long-term memory (outside basic session state), **implicit state awareness via system updates**.
*   **Resource limiting** for dynamic agents.
*   **Admin AI direct access to other agent sandboxes**.
*   **Formal Project/Task Management System:** Defining tasks, sub-tasks, dependencies beyond simple delegation.
*   **Database/Vector Store:** For shared project knowledge base, advanced long-term memory, sophisticated state management.
*   Multi-Team Projects.
*   Agent prompt updates *after* creation (other than team ID).
*   Generative UI (GeUI).
*   Advanced I/O, Voice Control.
*   Advanced Auth/Multi-User. *(Basic auth deferred from P10)*
*   Automated testing suite.
*   **UI Refinements:** Chat scrolling fix, handling all WS message types gracefully. *(Minor fixes needed)*

## 3. Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (`PyYAML`) for bootstrap & allowed models, `.env`.
*   **State Management:** In-memory dictionaries in `AgentManager` and `AgentStateManager`.
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state (via `SessionManager`).
*   **XML Parsing:** Standard library `re`, `html`.
*   **Logging:** Standard library `logging`.

## 4. Proposed Architecture Refinement (Conceptual - Post Phase 10)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_CHAT_VIEW["Chat & Agents View<br>(Agent Status Dynamic ✅)<br>(Chat Scroll Fix Needed)"]
        UI_LOGS_VIEW["System Logs View<br>(WS Error Display Fix Needed)"]
        UI_SESSION_VIEW["Project/Session View ✅<br>(List/Save/Load UI)"]
        UI_CONFIG_VIEW["Static Config View<br>(Restart Needed)"]
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend <br>+ Session API ✅<br>+ Project API ✅<br>+ Config API ✅"]
        WS_MANAGER["🔌 WebSocket Manager <br>+ Handles State Updates ✅<br>+ Handles Override Handling ✅"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Orchestrator)<br>+ Agent Create/Delete ✅<br>+ Routes Msgs (Robust)✅<br>+ Routes Tool Calls (Multi)✅<br>+ Handles Agent Generators ✅<br>+ Stream Error Retries/Override ✅<br>+ Injects Standard Prompts ✅<br>+ Uses State/Session Mgrs ✅<br>+ Manages Shared Instance ✅<br>Controls All Agents"]
        STATE_MANAGER["📝 AgentStateManager <br>(Manages Teams State) ✅"]
        SESSION_MANAGER["💾 SessionManager <br>(Handles Save/Load Logic) ✅<br>(Logs Save/Load Details) ✅"]

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent <br>(Loaded from Config)<br>Receives Allowed Models ✅<br>Receives Standard Instr ✅<br>Uses ManageTeamTool<br>Uses SendMessageTool"]
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1<br>(Created by Manager)<br>Receives Injected Prompt ✅<br>Uses Tools, Reports Back ✅"]
            DYNAMIC_AGENT_N["🤖 Dynamic Agent N<br>(Created by Manager)"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers <br>(Instantiated by Manager)"]
             PROVIDER_OR["🔌 OpenRouter Provider(s)"]
             PROVIDER_OLLAMA["🔌 Ollama Provider(s)"]
             PROVIDER_OPENAI["🔌 OpenAI Provider(s)"]
         end

         subgraph Tools
             TOOL_EXECUTOR["🛠️ Tool Executor<br>+ XML Desc Gen ✅"]
             TOOL_FS["📄 FileSystem Tool ✅"]
             TOOL_SENDMSG["🗣️ SendMessageTool ✅"]
             TOOL_MANAGE_TEAM["🛠️ ManageTeamTool ✅<br>Signals AgentManager"]
         end

         SANDBOXES["📁 Sandboxes <br>(Created Dynamically) ✅"]
         PROJECT_SESSIONS["💾 Project/Session Storage <br>(JSON via SessionManager) ✅"]
         LOG_FILES["📄 Log Files<br>(Timestamped) ✅"]
    end

    subgraph External
        LLM_API_SVC["☁️ Ext. LLM APIs (OR, OpenAI)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(AdminAI + Allowed Models) ✅"]
        DOT_ENV[".env File <br>(Secrets/Config) ✅"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP (API Calls, Session Mgmt, Config) --> FASTAPI;
    Frontend -- WebSocket (Receives updates, Sends Msgs/Overrides) --> WS_MANAGER;

    FASTAPI -- Manages --> AGENT_MANAGER; # App startup context via app.state
    FASTAPI -- "Gets Manager via Depends()" --> AGENT_MANAGER; # Route dependency

    WS_MANAGER -- Forwards Msgs / Sends UI Updates / Requests Override --> Frontend;
    WS_MANAGER -- Forwards User Msgs & Overrides --> AGENT_MANAGER; # Uses shared instance

    AGENT_MANAGER -- "Loads Bootstrap Agent(s)" --> CONFIG_YAML;
    AGENT_MANAGER -- "Uses Settings For Checks" --> DOT_ENV; # Via Settings
    AGENT_MANAGER -- "Instantiates/Reuses/Cleans" --> LLM_Providers;
    AGENT_MANAGER -- "Creates/Deletes/Manages Instances" --> Agents;
    AGENT_MANAGER -- "Injects Standard Context into Prompts" --> Agents;
    AGENT_MANAGER -- "Handles Tool Call Signals" --> Tools;
    AGENT_MANAGER -- Routes Tool Results Back --> Agents;
    AGENT_MANAGER -- Delegates State Ops --> STATE_MANAGER;
    AGENT_MANAGER -- Delegates Session Ops --> SESSION_MANAGER;
    AGENT_MANAGER -- Handles User Override --> Agents;

    STATE_MANAGER -- Manages --> "[Team State Dictionaries]"; # Conceptual State
    SESSION_MANAGER -- Uses --> STATE_MANAGER; # To get/set state
    SESSION_MANAGER -- Uses --> AGENT_MANAGER; # To get agent configs/histories
    SESSION_MANAGER -- Reads/Writes --> PROJECT_SESSIONS;

    ADMIN_AI -- "Uses Tools" --> TOOL_EXECUTOR;
    ADMIN_AI -- "Uses Provider" --> LLM_Providers;
    ADMIN_AI -- "Streams Text" --> AGENT_MANAGER;

    DYNAMIC_AGENT_1 -- "Uses Tools based on Injected Info" --> TOOL_EXECUTOR;
    DYNAMIC_AGENT_1 -- "Uses Provider" --> LLM_Providers;
    DYNAMIC_AGENT_1 -- "Streams Text" --> AGENT_MANAGER;
    DYNAMIC_AGENT_1 -- "Sends Result Message" --> TOOL_SENDMSG;

    TOOL_EXECUTOR -- Executes --> TOOL_FS;
    TOOL_EXECUTOR -- Executes --> TOOL_SENDMSG;
    TOOL_EXECUTOR -- Executes --> TOOL_MANAGE_TEAM;

    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

    %% Logging Connection - Conceptual
    Backend -- "Writes Logs" --> LOG_FILES;

```

## 5. Development Phases & Milestones

**Phase 1-9 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent Basics, Config Loading, Sandboxing, Basic Tools, LLM Abstraction, UI Enhancements, XML Tool Calling, Static Agent Config UI, Dynamic Agent Management V2 & Refactoring, Provider Error Handling, Session Persistence Logic.

**Phase 10: Collaboration Polish & UI Enhancements (Mostly Completed)**
*   **Goal:** Ensure agents reliably report back results, refine basic collaboration flows, implement dynamic UI updates for state changes, add session management UI, and add basic logging.
*   [X] **Workflow Testing & Prompt Refinement:**
    *   [X] Improved `SendMessageTool` robustness (target validation, sender feedback).
    *   [ ] Further testing of collaborative flows (Coder -> Reviewer) & Admin AI prompt refinement. *(Deferred)*
*   [X] **Frontend UI (`static/js/app.js`, `templates/index.html`):**
    *   [X] Implemented handling for WS messages (`agent_added`, `agent_deleted`, `agent_status_update`) to dynamically update the Agent Status list. *(Team-related WS message handling needs minor UI fix)*
    *   [X] Added Project/Session Management UI elements (`index.html`).
    *   [X] Connected UI elements to backend API endpoints (`app.js`).
    *   [ ] Enhance Conversation/Log areas for better message association (e.g., show team ID, clearer sender/recipient). *(Minor UI fix needed for chat scroll)*
*   [X] **Backend API (`src/api/`):**
    *   [X] Corrected AgentManager dependency injection using `app.state`.
    *   [X] Fixed `list_sessions` API logic.
    *   [X] Added timestamped file logging. *(Basic file logging added)*
    *   [ ] Configure more advanced logging (levels, rotation). *(Deferred)*
    *   [ ] Add basic authentication middleware/dependency. *(Deferred)*

**Phase 11: UI/UX Refinements & Advanced Features Prep (Current / Next)**
*   **Goal:** Fix remaining UI quirks (chat scroll, log display), refine Admin AI prompts based on observed errors, potentially add basic auth, and prepare for more advanced features.
*   [ ] **UI Fixes:**
    *   [ ] Fix chat area scrolling in `style.css`.
    *   [ ] Add handlers for remaining WS message types (`team_created`, `team_deleted`, `agent_moved_team`) in `app.js` to show informative logs instead of raw data errors.
*   [ ] **Admin AI Prompt Tuning:** Review `admin_ai` system prompt in `config.yaml` to improve tool usage accuracy (e.g., providing correct `agent_id` for deletion).
*   [ ] **Basic Authentication:** Implement simple API key or basic auth protection for API/WebSocket. *(Stretch goal for this phase)*
*   [ ] **Workflow Testing:** Perform end-to-end tests of simple collaborative tasks (e.g., research -> develop -> document).
*   [ ] **Error Handling:** Review error messages sent to UI for clarity.

**Future Phases (12+) (High-Level)**
*   **Phase 12: Advanced Admin AI & Coordination.** (Planning refinement, long-term memory, implicit status updates, direct sandbox read access).
*   **Phase 13: Formal Project Management & Knowledge Base.** (Project/Task structure, DB/Vector Store integration, agent KB tools).
*   **Phase 14+:** Resource Management, Advanced Collaboration (Hierarchy, Conflict Resolution), Multi-Team Projects, GeUI, Advanced I/O, etc.

**Phase 16: Create Project Plan for Next Iteration:** Re-evaluate and plan.
