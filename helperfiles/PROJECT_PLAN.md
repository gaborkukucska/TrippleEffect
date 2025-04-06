<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.6 (Phase 9 Completed)
**Date:** 2025-04-06 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator, interpreting user requests and managing teams/agents dynamically.
*   **Enable dynamic agent/team creation and deletion *in memory* via Admin AI commands, without requiring application restarts.** *(Completed in Phase 9)*
*   **Inject standardized context (tool descriptions, identity, team info, basic communication instructions) into all agents' system prompts by the framework** to ensure consistent capabilities and simplify Admin AI's prompt generation task. *(Completed in Phase 9)*
*   Empower agents to **communicate and collaborate autonomously** within their teams using framework-provided tools (`SendMessageTool`, `ManageTeamTool`). *(Partially Completed - Basic comms work, complex collaboration for future phases)*
*   Implement **session persistence**, capturing the state, histories, and **configurations of dynamically created agents** for reloading. *(Completed in Phase 9, handled by SessionManager)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI` and defining **allowed models/providers** for dynamic agent creation. *(Completed in Phase 9)*
*   Implement **provider availability checks** based on `.env` configuration and **automatic retries for temporary stream errors**. *(Completed in Phase 9)*
*   Refactor core manager logic into `AgentManager`, `AgentStateManager`, and `SessionManager` for better maintainability. *(Completed in Phase 9)*
*   Implement a **Human User Interface** that dynamically reflects the current agent/team structure (via WebSockets) and manages Projects/Sessions. *(Phase 10)*
*   Utilize the **XML-based tool calling mechanism** for all agent actions. *(Completed)*
*   Allow agents to utilize tools within sandboxed environments. *(Completed)*
*   *(Future Goals)* Enhance Admin AI planning, resource management (agent limits), advanced collaboration patterns, dynamic provider management, GeUI, multi-modal inputs, voice control.

## 2. Scope

**In Scope (Completed up to Phase 9 & Planned for Phase 10):**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling.
*   **Agent Core:** Agent class definition, state management, XML tool parsing. Uses framework-provided *final* system prompt.
*   **Admin AI Agent:** Primary user request handler, plans tasks, uses `ManageTeamTool` (specifying role, persona, provider, model from allowed list) and `SendMessageTool`. Defined in `config.yaml`, receives allowed models list in prompt.
*   **Agent Manager (`AgentManager`):**
    *   Central orchestrator for agent lifecycle and task execution.
    *   Instantiates/holds `AgentStateManager` and `SessionManager`.
    *   Holds main `agents` registry (Agent instances).
    *   Handles dynamic agent instantiation (`_create_agent_internal`) including **provider configuration checks**, **provider/model validation against allowed list**, provider instance management, sandbox setup.
    *   Injects standard instructions (tools, ID, team, comms) into dynamic agent system prompts.
    *   Handles dynamic agent deletion (`delete_agent_instance`) including provider cleanup.
    *   Routes Admin AI's `ManageTeamTool` calls to internal methods or `AgentStateManager`.
    *   Routes intra-team communication (`SendMessageTool`) via `_route_and_activate_agent_message`.
    *   Manages autonomous agent activation cycles (`_handle_agent_generator`), including **automatic retries for temporary stream errors**.
    *   Delegates session persistence calls to `SessionManager`.
*   **Agent State Manager (`AgentStateManager`):** *(New in Phase 9 Refactor)*
    *   Manages team structures (`teams` dict) and agent-to-team mappings (`agent_to_team` dict) *in memory*.
    *   Provides methods for CRUD operations on team state (`create_new_team`, `delete_existing_team`, `add_agent_to_team`, `remove_agent_from_team`).
    *   Provides methods for querying team state (`get_agent_team`, `get_team_members`, `get_team_info_dict`).
*   **Agent Session Manager (`SessionManager`):** *(New in Phase 9 Refactor)*
    *   Handles saving (`save_session`) and loading (`load_session`) of application state (dynamic agent configs, all agent histories, team state) to/from JSON files.
    *   Interacts with `AgentManager` and `AgentStateManager` to gather/restore state.
*   **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI and `AgentStateManager`.
*   **Tooling:** `BaseTool`, `ToolExecutor`, `FileSystemTool`, `SendMessageTool`, `ManageTeamTool` (with enhanced `list_agents` filtering).
*   **Configuration (`config.yaml`):** Defines `admin_ai`, defines `allowed_sub_agent_models` list per provider.
*   **Settings (`settings.py`):** Loads bootstrap config, `allowed_sub_agent_models`. Provides `is_provider_configured` check based on `.env`.
*   **Session Persistence:** Saving/Loading full state including dynamic agent configs, histories, teams (via `SessionManager`).
*   **Project Management (Basic):** API endpoints for listing projects/sessions, triggering save/load.
*   **Human UI:** *(Phase 10 targets)*
    *   Dynamically updates agent/team displays via WebSockets (`agent_added`, `agent_deleted`, `team_created`, etc.).
    *   Project/Session management UI (Save/Load buttons).
    *   Conversation view showing Admin AI, dynamic agents, intra-team messages.
    *   Basic authentication.
*   **WebSocket Communication:** Real-time streaming + basic state updates (`agent_added`, `agent_deleted`, `team_created`, etc.). *(Dynamic UI updates targeted for Phase 10)*
*   **Basic Sandboxing:** Created dynamically for agents.
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with stream error handling.
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`.

**Out of Scope (Deferred to Future Phases 11+):**

*   Google LLM Provider.
*   ConfigTool (Replaced).
*   Dynamic changes via `config.yaml` (post-startup).
*   Dynamic LLM Provider *Type* Management.
*   Advanced Collaboration (complex delegation, conflict resolution, hierarchy).
*   Advanced Admin AI Intelligence (planning refinement based on failures, long-term memory).
*   Resource limiting for dynamic agents.
*   Multi-Team Projects.
*   Agent prompt updates *after* creation (other than team ID).
*   Generative UI (GeUI).
*   Advanced I/O, Voice Control.
*   Advanced Auth/Multi-User.
*   Automated testing suite.

## 3. Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (`PyYAML`) for bootstrap & allowed models, `.env`.
*   **State Management:** In-memory dictionaries in `AgentManager` and `AgentStateManager`. *(Refactored in P9)*
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state (via `SessionManager`). *(Refactored in P9)*
*   **XML Parsing:** Standard library `re`, `html`.

## 4. Proposed Architecture Refinement (Conceptual - Post Phase 9)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_SESSION_VIEW["Session View <br>(Agent Status/Comms)<br>Log Stream Filter<br>Adv I/O: P11+<br>**Dynamic Updates: P10**"]
        UI_MGMT["Project/Session Mgmt Page <br>(Save/Load UI - P10)<br>Auth UI: P10"]
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend <br>+ Session API ✅<br>+ Auth API (P10)<br>+ Log Endpoints (P10?)"]
        WS_MANAGER["🔌 WebSocket Manager <br>+ Dynamic State Updates (P9/10)<br>+ Log Categories (P10)"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Orchestrator)<br>+ Agent Create/Delete ✅<br>+ Routes Admin/User Msgs ✅<br>+ Routes Tool Calls ✅<br>+ Handles Agent Generators ✅<br>+ Stream Error Retries ✅<br>+ Uses State/Session Mgrs ✅<br>Controls All Agents"] %% REFINED ROLE
        STATE_MANAGER["📝 AgentStateManager <br>(Manages Teams State) P9 ✅"] %% NEW
        SESSION_MANAGER["💾 SessionManager <br>(Handles Save/Load Logic) P9 ✅"] %% NEW

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent <br>(Loaded from Config)<br>Receives Allowed Models ✅<br>Uses ManageTeamTool<br>Uses SendMessageTool"]
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1<br>(Created by Manager)<br>Receives Injected Prompt ✅<br>Uses Tools"]
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
    end

    subgraph External
        LLM_API_SVC["☁️ Ext. LLM APIs (OR, OpenAI)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(AdminAI + Allowed Models) ✅"]
        DOT_ENV[".env File <br>(Secrets/Config) ✅"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP (API Calls, Auth, Session Mgmt) --> FASTAPI;
    Frontend -- WebSocket (Receives dynamic updates) --> WS_MANAGER;

    FASTAPI -- Calls AgentManager Ops --> AGENT_MANAGER; # Simplified view
    FASTAPI -- Manages --> AGENT_MANAGER; # App startup context

    WS_MANAGER -- Forwards Msgs / Sends Logs & Updates --> Frontend;
    WS_MANAGER -- Forwards User Msgs --> AGENT_MANAGER; # Routes to AdminAI

    AGENT_MANAGER -- "Loads Bootstrap Agent(s)" --> CONFIG_YAML;
    AGENT_MANAGER -- "Uses Settings For Checks" --> DOT_ENV; # Via Settings
    AGENT_MANAGER -- "Instantiates/Reuses/Cleans" --> LLM_Providers;
    AGENT_MANAGER -- "Creates/Deletes/Manages Instances" --> Agents;
    AGENT_MANAGER -- "Injects Standard Context into Prompts" --> Agents;
    AGENT_MANAGER -- "Handles Tool Call Signals" --> Tools; # Handles ManageTeamTool signal
    AGENT_MANAGER -- Routes Tool Results Back --> Agents; # Handles SendMessage activation
    AGENT_MANAGER -- Delegates State Ops --> STATE_MANAGER; # NEW
    AGENT_MANAGER -- Delegates Session Ops --> SESSION_MANAGER; # NEW

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

    TOOL_EXECUTOR -- Executes --> TOOL_FS;
    TOOL_EXECUTOR -- Executes --> TOOL_SENDMSG;
    TOOL_EXECUTOR -- Executes --> TOOL_MANAGE_TEAM;

    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

```

## 5. Development Phases & Milestones

**Phase 1-8 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent Basics, Static Config Loading, Sandboxing, Basic Tools, LLM Abstraction, UI Enhancements, XML Tool Calling, Static Agent Configuration UI.

**Phase 9: Dynamic Agent Management V2 & Refactoring (Completed)**
*   **Goal:** Implement dynamic agent creation with framework-injected prompts, enhanced `ManageTeamTool`, provider/model validation, manager refactoring, and improved error handling.
*   [X] **Configuration (`config.yaml`):** Defined `admin_ai` only + `allowed_sub_agent_models`. Revised `admin_ai` prompt for dynamic flow & ID handling.
*   [X] **Settings (`settings.py`):** Loaded `allowed_sub_agent_models`. Added `is_provider_configured`.
*   [X] **Refactoring:** Split `AgentManager` logic into `AgentStateManager` (team state) and `SessionManager` (persistence).
*   [X] **Agent Manager (`agents/manager.py`):** Updated to orchestrate; uses State/Session managers; validates provider config & model; injects prompts; handles agent lifecycle; routes messages; sequential tool execution in `_handle_agent_generator`.
*   [X] **State Manager (`agents/state_manager.py`):** Created to handle team/assignment state.
*   [X] **Session Manager (`agents/session_manager.py`):** Created to handle save/load logic.
*   [X] **Tools (`ManageTeamTool`):** Updated param descriptions; `list_agents` filtering handled by Manager.
*   [X] **Agent Core (`agents/core.py`):** Verified compatibility.
*   [X] **LLM Providers:** Enhanced `stream_completion` to handle stream processing errors.
*   [X] **Agent Manager:** Implemented automatic retry logic for temporary stream errors in `_handle_agent_generator`.
*   [X] **Testing:** Confirmed Admin AI planning, agent creation (with validation), tool usage (`list_agents`, `list_teams`, `create_team`, `add_agent_to_team`, `send_message`), sequential execution, stream error recovery, session save/load.

**Phase 10: Dynamic UI & Collaboration Polish (Current / Next)**
*   **Goal:** Implement dynamic UI updates reflecting in-memory state, Session Management UI, basic collaboration flows, logging/auth.
*   [ ] **Frontend UI (`static/js/app.js`, `templates/index.html`):**
    *   [ ] Implement handling for WS messages (`agent_added`, `agent_deleted`, `team_created`, `team_deleted`, `agent_moved_team`, `agent_status_update`) to dynamically update the Agent Status/Config sections without page refresh.
    *   [ ] Add Project/Session Management UI elements (dropdowns, Save/Load buttons) to `index.html`.
    *   [ ] Connect UI buttons to existing API endpoints (`/api/projects`, `/api/projects/.../sessions`, `/api/projects/.../sessions/.../load`, `/api/projects/.../sessions/save`).
    *   [ ] Enhance Conversation/Log areas for better message association (e.g., show team ID, clearer sender/recipient).
*   [ ] **Backend API (`src/api/`):**
    *   [ ] Add basic authentication middleware/dependency (e.g., simple API key header or basic user/pass). Protect relevant API endpoints and WebSocket connection.
    *   [ ] Add simple logging configuration (e.g., different levels, file output).
*   [ ] **Workflow Testing:** Refine Coder -> Reviewer or similar simple collaborative flows initiated by Admin AI. Ensure messages are routed correctly and agents respond appropriately.

**Future Phases (11+) (High-Level)**
*   **Phase 11: Advanced Collaboration & Admin AI Intelligence.** (Planning refinement, complex delegation patterns).
*   **Phase 12: Resource Management & Error Handling.** (Limit dynamic agents, more robust error handling across system).
*   **Phase 13+:** Multi-Team Projects, Hierarchy, GeUI, Advanced I/O, etc.

**Phase 16: Create Project Plan for Next Iteration:** Re-evaluate and plan.
