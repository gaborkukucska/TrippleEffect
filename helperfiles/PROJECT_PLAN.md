<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.4 (Phase 9 Planned - Dynamic Management)
**Date:** 2025-04-06 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   **Implement an Admin AI agent capable of interpreting user requests and dynamically creating, managing, and deleting teams and worker agents *in memory* to fulfill those requests.** *(New Core Focus)*
*   **Enable these dynamically managed agents to communicate and collaborate autonomously within their teams.** *(Refined Focus)*
*   **Implement persistence for collaborative sessions, capturing the state and configuration of dynamically created agents.** *(Refined Focus)*
*   Utilize `config.yaml` primarily for bootstrapping essential agents like the `Admin AI`.
*   Implement a **Human User Interface** that dynamically reflects the current agent/team structure and manages Projects/Sessions. *(Revised Scope)*
*   Enable real-time communication between the backend and frontend using WebSockets, including categorized logs and dynamic state updates. *(Revised Scope)*
*   Utilize the **XML-based tool calling mechanism** for agent capabilities, including Admin AI's management actions. *(Completed)*
*   Allow agents to utilize tools within sandboxed environments.
*   *(Future Goals)* Enhance Admin AI intelligence for planning and oversight. Explore advanced collaboration patterns, dynamic provider management, Generative UI (GeUI), multi-modal inputs, voice control.

## 2. Scope

**In Scope (Phases up to ~11):**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling.
*   **Agent Core:** Agent class definition, state management, XML tool parsing. *(Completed)*
*   **Admin AI Agent:** The primary recipient of user requests, responsible for planning and managing other agents/teams using `ManageTeamTool`. Defined in `config.yaml`.
*   **Agent Manager:**
    *   Central registry for *all* agents (bootstrap and dynamic).
    *   Handles dynamic agent instantiation (`create_agent_instance`) including provider management (reuse/creation) and sandbox setup.
    *   Handles dynamic agent deletion (`delete_agent_instance`) including cleanup.
    *   Manages team structures (`add_agent_to_team`, etc.) *in memory*.
    *   Routes intra-team communication (`SendMessageTool`).
    *   Manages autonomous agent activation cycles.
    *   Handles session persistence (saving/loading dynamic agent configs + histories).
*   **Dynamic Agent/Team Management:** In-memory creation, deletion, and modification of agents and teams via Admin AI commands, reflected immediately in the application state. *(Phase 9)*
*   **Tooling:** `BaseTool`, `ToolExecutor`, `FileSystemTool`, `SendMessageTool`, **`ManageTeamTool`** (with actions like `create_agent`, `delete_agent`, `add_agent_to_team`, etc.). *(Revised - Phase 9)*
*   **Session Persistence:** Saving and Loading the full state, including **dynamically created agent configurations** and message histories, within a Project structure. *(Revised - Phase 9)*
*   **Project Management (Basic):** API/logic for creating projects, saving sessions, listing/loading sessions. *(Phase 9)*
*   **Human UI:**
    *   Dynamically updates agent/team displays based on WebSocket messages (`agent_added`, `agent_deleted`, etc.). *(Phase 10)*
    *   UI controls for Project/Session management. *(Phase 10)*
    *   Displays conversation, including Admin AI and intra-team messages. *(Phase 10)*
    *   Basic authentication. *(Phase 10)*
*   **Configuration (`config.yaml`):** Primarily used only to define bootstrap agent(s) like `admin_ai`. *Not* used for dynamic agents. *(Revised Scope)*
*   **WebSocket Communication:** Real-time streaming of agent outputs/status, categorized backend logs, **dynamic agent/team state updates**. *(Revised Scope - Phase 9/10)*
*   **Basic Sandboxing:** Agent file operation directories created dynamically. *(Completed/Adapted)*
*   **LLM Integration:** Support for OpenRouter, Ollama, OpenAI via provider abstraction. *(Completed)*
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`.

**Out of Scope (Deferred to Future Phases 11+):**

*   **Google LLM Provider:** Removed from immediate plan.
*   **ConfigTool:** Replaced by `ManageTeamTool`.
*   **Dynamic changes via `config.yaml`:** Configuration file is static after startup (except for Admin AI definition).
*   **Dynamic LLM Provider Management:** Adding/removing provider *types* without restart.
*   **Advanced Collaboration:** Complex delegation hierarchies, automated review workflows, conflict resolution.
*   **Advanced Admin AI Intelligence:** Sophisticated planning, long-term memory, self-improvement.
*   **Multi-Team Projects:** Multiple distinct teams operating concurrently within the same project scope (Initial focus is single-project context).
*   **Generative UI (GeUI).**
*   **Advanced I/O:** Camera, microphone (STT), speaker (TTS), voice control.
*   Advanced Authentication / Multi-User Support.
*   Sophisticated automated testing suite.

## 3. Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (`PyYAML`) for bootstrap config, `.env` files (`python-dotenv`). *(Revised)*
*   **State Management:** In-memory dictionaries in `AgentManager` for dynamic agents/teams.
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state (dynamic configs + histories). *(Planned)*
*   **Authentication (Basic):** Likely FastAPI middleware/dependencies *(Phase 10)*.
*   **XML Parsing:** Standard library `re`, `html`.

## 4. Proposed Architecture Refinement (Conceptual - Phase 9/10 - Dynamic)

(Diagram updated: Config only for AdminAI, Manager handles dynamic creation/state)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_SESSION_VIEW["Session View <br>(Dynamic Agent Status/Comms)<br>Log Stream Filter - P10<br>Adv I/O: P11+"]
        UI_MGMT["Project/Session Mgmt Page <br>(Save/Load UI - P10)<br>Dynamic Config View (P10)<br>Auth UI: P10"]
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend <br>+ Session API (P9)<br>+ Auth API (P10)<br>+ Log Endpoints (P10?)"]
        WS_MANAGER["🔌 WebSocket Manager <br>+ Dynamic State Updates (P9)<br>+ Log Categories (P10)"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>+ Dynamic Agent/Team CRUD Methods (P9)<br>+ Routes Admin AI Tool Calls (P9)<br>+ Routes Intra-Team Msgs (P9)<br>+ Session Save/Load Logic (P9)<br>Controls All Agents"] %% Enhanced Role
        CONFIG_MANAGER["📝 Config Manager <br>(Reads config.yaml ONCE)"] %% Reduced Role

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent <br>(Loaded from Config)<br>Uses ManageTeamTool<br>Uses SendMessageTool"]
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1<br>(Created by Manager)<br>Uses FileSystemTool<br>Uses SendMessageTool"]
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
             TOOL_MANAGE_TEAM["🛠️ ManageTeamTool (P9)<br>Signals AgentManager"] %% NEW
         end

         SANDBOXES["📁 Sandboxes <br>(Created Dynamically)"]
         PROJECT_SESSIONS["💾 Project/Session Storage <br>(Incl. Dynamic Configs) (P9)"]
    end

    subgraph External
        LLM_API_SVC["☁️ Ext. LLM APIs (OR, OpenAI)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(Defines AdminAI ONLY - Read Once)"] %% Reduced Role
        DOT_ENV[".env File <br>(Secrets - Read Only) ✅"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP (API Calls, Auth, Session Mgmt) --> FASTAPI;
    Frontend -- WebSocket (Receives dynamic updates) --> WS_MANAGER;

    FASTAPI -- Calls Session Ops --> AGENT_MANAGER;
    FASTAPI -- Manages --> AGENT_MANAGER; %% Less direct management now
    WS_MANAGER -- Forwards Msgs / Sends Logs & Updates --> Frontend;
    WS_MANAGER -- Forwards User Msgs --> AGENT_MANAGER; %% Routes to AdminAI

    AGENT_MANAGER -- "Loads Bootstrap Agent(s)" --> CONFIG_YAML;
    AGENT_MANAGER -- "Reads Config/Secrets" --> DOT_ENV;
    AGENT_MANAGER -- "Instantiates/Reuses" --> LLM_Providers; %% Dynamic
    AGENT_MANAGER -- "Creates/Deletes/Manages" --> Agents; %% Dynamic
    AGENT_MANAGER -- "Handles ManageTeamTool Signals" --> AGENT_MANAGER; %% Internal methods
    AGENT_MANAGER -- Routes Tool Calls --> TOOL_EXECUTOR;
    AGENT_MANAGER -- "Handles Agent-to-Agent Msgs & Activates Target" --> Agents;
    AGENT_MANAGER -- "Saves/Loads Dynamic Configs + Histories" --> PROJECT_SESSIONS;

    ADMIN_AI -- "Uses ManageTeamTool" --> TOOL_EXECUTOR;
    ADMIN_AI -- "Uses SendMessageTool" --> TOOL_EXECUTOR;
    ADMIN_AI -- "Uses Provider" --> LLM_Providers;
    ADMIN_AI -- "Streams Text" --> AGENT_MANAGER;

    DYNAMIC_AGENT_1 -- "Uses FileSystemTool/SendMessageTool" --> TOOL_EXECUTOR;
    DYNAMIC_AGENT_1 -- "Uses Provider" --> LLM_Providers;
    DYNAMIC_AGENT_1 -- "Streams Text" --> AGENT_MANAGER;

    TOOL_EXECUTOR -- Executes --> TOOL_FS;
    TOOL_EXECUTOR -- Executes --> TOOL_SENDMSG;
    TOOL_EXECUTOR -- Executes --> TOOL_MANAGE_TEAM;

    TOOL_MANAGE_TEAM -- "Signals Manager (Action & Args)" --> AGENT_MANAGER; %% Tool signals Manager methods

    CONFIG_MANAGER -- "Reads Bootstrap Config" --> CONFIG_YAML; %% On init only

    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

```

## 5. Development Phases & Milestones

**Phase 1-8 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent Basics, Config Loading (Static), Sandboxing, Basic Tools, LLM Abstraction, UI Enhancements, XML Tool Calling, **Agent Configuration UI (Static)**.

**Phase 9: Dynamic Agent Management Foundation (Current / Next)**
*   **Goal:** Implement the core mechanics for Admin AI to dynamically manage agents and teams in memory via `ManageTeamTool`, adapting persistence accordingly.
*   [ ] **Configuration (`config.yaml`):**
    *   [ ] Simplify to define *only* the `admin_ai` agent. Remove other agents and the `teams` section.
    *   [ ] Update `admin_ai` system prompt: Explain its role as coordinator, instruct it to plan teams/agents, use `ManageTeamTool` (XML format) for creation/deletion, and `SendMessageTool` for delegation. Emphasize no restarts needed for `ManageTeamTool`.
*   [ ] **Tooling (`ManageTeamTool`):**
    *   [ ] Create `src/tools/manage_team.py`.
    *   [ ] Implement `ManageTeamTool` inheriting `BaseTool`.
    *   [ ] Define parameters for actions: `create_agent` (provider, model, system_prompt, persona, team_id, agent_id[optional]), `delete_agent` (agent_id), `add_agent_to_team` (agent_id, team_id), `remove_agent_from_team` (agent_id, team_id), `create_team` (team_id), `delete_team` (team_id), `list_agents`, `list_teams`.
    *   [ ] `execute` method validates args and **signals corresponding public method on AgentManager**. Returns success/error string.
*   [ ] **Agent Manager (`agents/manager.py`):**
    *   [ ] Remove loading of `teams` from `settings`. Initialize `self.teams` and `self.agent_to_team` as empty dicts.
    *   [ ] Modify `_initialize_agents` to *only* load the bootstrap agent(s) specified in the simplified `config.yaml`.
    *   [ ] Implement public async methods: `create_agent_instance`, `delete_agent_instance`, `add_agent_to_team`, `remove_agent_from_team`, `create_new_team`, `delete_existing_team`, `get_agent_info_list`, `get_team_info_dict`.
    *   [ ] `create_agent_instance`: Handles provider lookup/instantiation/reuse, `Agent` instantiation, adding to `self.agents`, updating team maps, creating sandbox, queueing WS update.
    *   [ ] `delete_agent_instance`: Handles removal from `self.agents`/`self.teams`/`self.agent_to_team`, provider cleanup, queueing WS update.
    *   [ ] Modify `_handle_agent_generator` to check for `ManageTeamTool` name in executed tool results, parse the signal, and `await` the corresponding manager method.
    *   [ ] Modify `save_session`: Save `self.teams`, `self.agent_to_team`, and for each agent in `self.agents` (excluding bootstrap?), save its full config (provider, model, prompt, persona) *and* its history.
    *   [ ] Modify `load_session`: Clear existing dynamic agents/teams. Rebuild `self.teams` map. Call `create_agent_instance` for each dynamic agent config found in save file. *Then* load histories.
    *   [ ] Modify `handle_user_message`: Route *only* to `admin_ai`. Add checks if `admin_ai` exists/is idle.
*   [ ] **Tool Executor (`tools/executor.py`):** Register `ManageTeamTool`. Update XML descriptions.
*   [ ] **WebSocket Manager / UI (`websocket_manager.py`, `app.js`):**
    *   [ ] Define WS message types (`agent_added`, `agent_deleted`, `team_created`, `team_deleted`, `agent_moved_team`).
    *   [ ] Implement basic JS handlers to log these events to console (full UI update in Phase 10).
*   [ ] **Session Persistence API (`api/http_routes.py`):** Implement Save/Load/List endpoints calling the updated manager methods.
*   [ ] **Testing:**
    *   [ ] Send request to Admin AI: "Create a coder agent and an analyst agent in team 'dev_team'". Verify agents are created in manager state.
    *   [ ] Send request: "List agents". Verify Admin AI uses tool and receives correct list.
    *   [ ] Send request: "Delete agent 'coder'". Verify removal.
    *   [ ] Test Save/Load: Verify dynamic agents and teams are restored correctly.

**Phase 10: Admin AI Delegation & Dynamic UI (Planned)**
*   **Goal:** Enable Admin AI to use dynamic capabilities effectively, refine UI for dynamic updates, implement collaboration, logging/auth.
*   [ ] **Workflow Testing:** Test full flow: User Request -> Admin AI plans & uses `ManageTeamTool` -> Admin AI uses `SendMessageTool` -> Dynamic Agents collaborate -> Completion/User Query.
*   [ ] **Admin AI Prompt Tuning:** Extensive tuning for planning, tool use, delegation.
*   [ ] **Frontend UI (`static/js/app.js`, `templates/index.html`):**
    *   Implement robust handling of dynamic WS messages (`agent_added`, etc.) to update UI lists/views without refresh.
    *   Integrate Project/Session UI controls.
    *   Refine conversation view for Admin AI and intra-team messages.
*   [ ] **Backend & Frontend - Logging & Auth:** Implement categorized log streaming and basic password authentication.

**Future Phases (11+) (High-Level)**
*   **Phase 11: Advanced Collaboration & Admin AI Intelligence.**
*   **Phase 12: Resource Management & Error Handling:** Implement limits on dynamic agents, better cleanup, refined error reporting.
*   **Phase 13+:** Multi-Team Projects, Hierarchy, GeUI, Advanced I/O, etc.

**Phase 16: Create Project Plan for Next Iteration:** Re-evaluate and plan.
