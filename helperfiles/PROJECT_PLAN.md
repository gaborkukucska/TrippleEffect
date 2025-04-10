<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.11 <!-- Updated Version -->
**Date:** 2025-04-10 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator, interpreting user requests and managing teams/agents dynamically.
*   **Enable dynamic agent/team creation and deletion *in memory* via Admin AI commands, without requiring application restarts.** *(Completed)*
*   **Inject standardized context (tool descriptions, identity, team info, basic communication/reporting instructions) into all dynamic agents' system prompts by the framework** to ensure consistent capabilities and simplify Admin AI's prompt generation task. *(Completed)*
*   Empower agents to **communicate and collaborate autonomously** within their teams using framework-provided tools (`SendMessageTool`, `ManageTeamTool`), including **reporting results back to the Admin AI**. *(Completed)*
*   Implement **session persistence**, capturing the state, histories, and **configurations of dynamically created agents** for reloading. *(Completed)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI` and defining **allowed models/providers** for dynamic agent creation. *(Completed)*
*   Implement **provider availability checks** based on `.env` configuration and **automatic retries with user override** for temporary stream errors. *(Completed)*
*   Refactor core manager logic into `AgentManager`, `AgentStateManager`, `SessionManager`, **`AgentInteractionHandler`**, and utility modules (`prompt_utils`). *(Completed in Phase 11)*
*   Implement a **Human User Interface** that dynamically reflects the current agent/team structure (via WebSockets), manages Projects/Sessions, and allows user intervention on persistent errors (e.g., Provider/Model override). *(Completed)*
*   Utilize the **XML-based tool calling mechanism** for all agent actions, supporting **sequential execution of multiple tool calls** within a single agent turn. *(Completed)*
*   Allow agents to utilize tools within sandboxed environments (`scope: private`) or a **shared project workspace (`scope: shared`), passing necessary context.** *(Completed in Phase 11)*
*   *(Future Goals)* Implement automatic project/session context setting on first interaction. Enhance Admin AI planning, resource management (agent limits), advanced collaboration patterns, dynamic provider management, GeUI, multi-modal inputs, voice control, **implicit Admin AI status updates**, **direct Admin AI sandbox access (read-only initially)**, **formal project/task management**, **database/vector store for shared project memory and Admin AI long-term memory**.

## 2. Scope

**In Scope (Completed up to Phase 11 & Planned for Future):**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling. *(Completed)*
*   **Agent Core:** Agent class definition, state management, **parsing multiple XML tool calls**. *(Completed)*
*   **Admin AI Agent:** Primary user request handler, plans tasks, uses `ManageTeamTool` and `SendMessageTool`. Defined in `config.yaml`. *(Completed)*
*   **Agent Manager (`AgentManager`):** Central orchestrator, manages agent lifecycle, **delegates tool execution/handling to InteractionHandler**, routes messages, handles errors/retries, uses State/Session managers. *(Refactored in P11)*
*   **Agent Interaction Handler (`AgentInteractionHandler`):** Handles ManageTeam/SendMessage signals, executes tools **passing project/session context**. *(Created in P11)*
*   **Agent Prompt Utils (`prompt_utils.py`):** Holds prompt constants and helpers. *(Created in P11)*
*   **Agent State Manager (`AgentStateManager`):** Manages team structures and agent-to-team mappings *in memory*. *(Completed)*
*   **Agent Session Manager (`SessionManager`):** Handles saving and loading of application state (dynamic agents, histories, teams) to/from JSON files. *(Completed)*
*   **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI and `AgentStateManager`. *(Completed)*
*   **Tooling:** `BaseTool`, `ToolExecutor`, `FileSystemTool` (with shared scope fix), `SendMessageTool` (robust), `ManageTeamTool` (robust), `GitHubTool`, `WebSearchTool`. *(FileSystemTool fixed in P11)*
*   **Configuration (`config.yaml`):** Defines `admin_ai`, defines `allowed_sub_agent_models`. *(Completed)*
*   **Settings (`settings.py`):** Loads bootstrap config, `allowed_sub_agent_models`, checks provider config. *(Completed)*
*   **Session Persistence:** Saving/Loading full state including dynamic agent configs, histories, teams. *(Completed)*
*   **Project Management (UI):** UI for listing projects/sessions, triggering save/load. *(Completed)*
*   **Human UI:** *(Completed)*
    *   Dynamically updates agent/team displays via WebSockets.
    *   Project/Session management UI.
    *   Conversation view.
    *   Modal dialog for user override.
*   **WebSocket Communication:** Real-time streaming + state updates + user override. *(Completed)*
*   **Sandboxing & Shared Workspace:** Dynamically created for agents (private) or project/session (shared). *(Completed/Fixed in P11)*
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers with stream error handling and retry/override. *(Completed)*
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`. *(Ongoing)*
*   **Logging:** Basic console and timestamped file logging. *(Completed)*

**Out of Scope (Deferred to Future Phases 11+):**

*   **(Phase 11 Goal):** Implement automatic project/session context setting on first interaction.
*   Dynamic LLM Provider *Type* Management.
*   **Advanced Collaboration:** Complex delegation, conflict resolution, hierarchy, synchronous operations.
*   **Advanced Admin AI Intelligence:** Planning refinement based on failures, long-term memory (outside basic session state), **implicit state awareness via system updates**.
*   **Resource limiting** for dynamic agents.
*   **Formal Project/Task Management System:** Defining tasks, sub-tasks, dependencies beyond simple delegation.
*   **Database/Vector Store:** For shared project knowledge base, advanced long-term memory, sophisticated state management.
*   Multi-Team Projects.
*   Agent prompt updates *after* creation (other than team ID).
*   Generative UI (GeUI).
*   Advanced I/O, Voice Control.
*   Advanced Auth/Multi-User. *(Basic auth deferred)*
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

## 4. Proposed Architecture Refinement (Conceptual - Post Phase 11)

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
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>(Orchestrator)<br>+ Agent Create/Delete ✅<br>+ Handles Agent Generators ✅<br>+ Stream Error Retries/Override ✅<br>+ Injects Prompts (via Utils) ✅<br>+ Delegates to State/Session Mgrs ✅<br>+ **Delegates Interactions (Tool Exec, Msg Route) to InteractionHandler ✅** <br>+ Manages Shared Instance ✅<br>+ Tracks Project/Session ✅"]
        INTERACTION_HANDLER["🤝 Interaction Handler<br>+ Processes ManageTeam Signals ✅<br>+ Routes Msgs (via State Mgr) ✅<br>+ Executes Tools (via Executor, **passes context**) ✅"]
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
             TOOL_EXECUTOR["🛠️ Tool Executor<br>+ XML Desc Gen ✅<br>+ **Receives Project/Session Context ✅**"]
             TOOL_FS["📄 FileSystem Tool ✅<br>+ **Uses Context for Shared Scope ✅**"]
             TOOL_SENDMSG["🗣️ SendMessageTool ✅<br>Signals InteractionHandler"]
             TOOL_MANAGE_TEAM["🛠️ ManageTeamTool ✅<br>Signals InteractionHandler"]
             TOOL_GITHUB["🐙 GitHub Tool ✅"]
             TOOL_WEBSEARCH["🌐 Web Search Tool ✅"]
         end

         SANDBOXES["📁 Sandboxes <br>(Created Dynamically) ✅"]
         PROJECT_SESSIONS["💾 Project/Session Storage <br>(JSON via SessionManager) ✅"]
         SHARED_WORKSPACE["🌐 Shared Workspace <br>(Created Dynamically by FS Tool) ✅"]
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
    AGENT_MANAGER -- "Injects Standard Context into Prompts" --> Agents; # Using prompt_utils
    AGENT_MANAGER -- Delegates State Ops --> STATE_MANAGER;
    AGENT_MANAGER -- Delegates Session Ops --> SESSION_MANAGER;
    AGENT_MANAGER -- Handles User Override --> Agents;
    AGENT_MANAGER -- "Handles Tool Call Event from Generator" --> INTERACTION_HANDLER;
    AGENT_MANAGER -- "Passes Project/Session Context" --> INTERACTION_HANDLER;
    AGENT_MANAGER -- Instantiates --> INTERACTION_HANDLER;

    INTERACTION_HANDLER -- "Uses AgentManager for agent ops" --> AGENT_MANAGER;
    INTERACTION_HANDLER -- "Uses StateManager for checks" --> STATE_MANAGER;
    INTERACTION_HANDLER -- "Calls Tool Executor" --> TOOL_EXECUTOR;
    INTERACTION_HANDLER -- Routes Tool Results Back --> AGENT_MANAGER; # Via feedback mechanism

    STATE_MANAGER -- Manages --> "[Team State Dictionaries]"; # Conceptual State
    SESSION_MANAGER -- Uses --> STATE_MANAGER; # To get/set state
    SESSION_MANAGER -- Uses --> AGENT_MANAGER; # To get agent configs/histories
    SESSION_MANAGER -- Reads/Writes --> PROJECT_SESSIONS;

    ADMIN_AI -- "Uses Tools" --> INTERACTION_HANDLER; # Via manager
    ADMIN_AI -- "Uses Provider" --> LLM_Providers;
    ADMIN_AI -- "Streams Text" --> AGENT_MANAGER;

    DYNAMIC_AGENT_1 -- "Uses Tools based on Injected Info" --> INTERACTION_HANDLER; # Via manager
    DYNAMIC_AGENT_1 -- "Uses Provider" --> LLM_Providers;
    DYNAMIC_AGENT_1 -- "Streams Text" --> AGENT_MANAGER;
    DYNAMIC_AGENT_1 -- "Sends Result Message" --> TOOL_SENDMSG; # Signals handler

    TOOL_EXECUTOR -- Executes --> TOOL_FS;
    TOOL_EXECUTOR -- Executes --> TOOL_SENDMSG;
    TOOL_EXECUTOR -- Executes --> TOOL_MANAGE_TEAM;
    TOOL_EXECUTOR -- Executes --> TOOL_GITHUB;
    TOOL_EXECUTOR -- Executes --> TOOL_WEBSEARCH;

    TOOL_FS -- Reads/Writes --> SANDBOXES;
    TOOL_FS -- Reads/Writes --> SHARED_WORKSPACE; # Uses project/session context

    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

    %% Logging Connection - Conceptual
    Backend -- "Writes Logs" --> LOG_FILES;

```

## 5. Development Phases & Milestones

**Phase 1-10 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent Basics, Config Loading, Sandboxing, Basic Tools, LLM Abstraction, UI Enhancements, XML Tool Calling, Static Agent Config UI, Dynamic Agent Management V2 & Refactoring, Provider Error Handling, Session Persistence Logic, UI State/Session Management, Basic Logging.

**Phase 11: Agent Manager Refactoring & Context Fix (Current / Mostly Completed)**
*   **Goal:** Refactor the large `AgentManager` into smaller, focused components (`InteractionHandler`, `prompt_utils`). Fix the passing of project/session context to tools (specifically `FileSystemTool` for shared scope). Refine Admin AI cleanup instructions.
*   [X] **Refactoring:**
    *   [X] Created `src/agents/prompt_utils.py` and moved prompt constants/helpers.
    *   [X] Created `src/agents/interaction_handler.py` and moved tool execution/handling logic.
    *   [X] Updated `src/agents/manager.py` to delegate interactions and use utils.
*   [X] **Context Fix:**
    *   [X] Modified `AgentInteractionHandler.execute_single_tool` to accept and pass context.
    *   [X] Modified `ToolExecutor.execute_tool` to accept and pass context to `BaseTool.execute`.
    *   [X] Modified `BaseTool.execute` signature to include context parameters.
    *   [X] Modified `FileSystemTool.execute` to use context for shared scope path.
    *   [X] Modified `AgentManager._handle_agent_generator` to pass `current_project`/`current_session` to the interaction handler.
*   [X] **Prompt Refinement:**
    *   [X] Updated `ADMIN_AI_OPERATIONAL_INSTRUCTIONS` in `prompt_utils.py` for clearer `delete_agent` instructions.
*   [X] **Helper File Updates:**
    *   [X] Updated `FUNCTIONS_INDEX.md`.
    *   [X] Updated `PROJECT_PLAN.md`.
*   [ ] **Next Step:** Implement automatic project/session context setting on first user interaction in `AgentManager.handle_user_message`.

**Phase 12: Auto Context & UI/UX Refinements (Next)**
*   **Goal:** Implement auto-setting of project/session context. Fix remaining UI quirks (chat scroll, log display). Refine Admin AI prompts based on observed errors. Potentially add basic auth.
*   [ ] **Auto Context:** Modify `AgentManager.handle_user_message` to create/save a default project/session on first message if context is `None`.
*   [ ] **UI Fixes:**
    *   [ ] Fix chat area scrolling in `style.css`.
    *   [ ] Add handlers for remaining WS message types (`team_created`, `team_deleted`, `agent_moved_team`) in `app.js` to show informative logs instead of raw data errors.
*   [ ] **Admin AI Prompt Tuning:** Review `admin_ai` prompt usage after recent changes, particularly cleanup steps.
*   [ ] **Basic Authentication:** Implement simple API key or basic auth protection for API/WebSocket. *(Stretch goal)*
*   [ ] **Workflow Testing:** Perform end-to-end tests of collaborative tasks involving shared file scope.
*   [ ] **Error Handling:** Review error messages sent to UI for clarity.

**Future Phases (13+) (High-Level)**
*   **Phase 13: Advanced Admin AI & Coordination.** (Planning refinement, long-term memory, implicit status updates, direct sandbox read access).
*   **Phase 14: Formal Project Management & Knowledge Base.** (Project/Task structure, DB/Vector Store integration, agent KB tools).
*   **Phase 15+:** Resource Management, Advanced Collaboration (Hierarchy, Conflict Resolution), Multi-Team Projects, GeUI, Advanced I/O, etc.

**Phase 17: Create Project Plan for Next Iteration:** Re-evaluate and plan.
