<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.5 (Phase 9 Planned - Dynamic Management V2)
**Date:** 2025-04-06 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI** agent that acts as the central coordinator, interpreting user requests and managing teams/agents dynamically.
*   **Enable dynamic agent/team creation and deletion *in memory* via Admin AI commands, without requiring application restarts.** *(Core Implementation Goal)*
*   **Inject standardized context (tool descriptions, identity, team info, basic communication instructions) into all agents' system prompts by the framework** to ensure consistent capabilities and simplify Admin AI's prompt generation task. *(New Core Principle)*
*   Empower agents to **communicate and collaborate autonomously** within their teams using framework-provided tools (`SendMessageTool`, `ManageTeamTool`). *(Refined Focus)*
*   Implement **session persistence**, capturing the state, histories, and **configurations of dynamically created agents** for reloading. *(Refined Focus)*
*   Utilize `config.yaml` primarily for bootstrapping the `Admin AI` and defining **allowed models/providers** for dynamic agent creation. *(Revised Scope)*
*   Implement a **Human User Interface** that dynamically reflects the current agent/team structure (via WebSockets) and manages Projects/Sessions. *(Revised Scope)*
*   Utilize the **XML-based tool calling mechanism** for all agent actions. *(Completed)*
*   Allow agents to utilize tools within sandboxed environments.
*   *(Future Goals)* Enhance Admin AI planning, resource management (agent limits), advanced collaboration patterns, dynamic provider management, GeUI, multi-modal inputs, voice control.

## 2. Scope

**In Scope (Phases up to ~11):**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling.
*   **Agent Core:** Agent class definition, state management, XML tool parsing. Uses framework-provided *final* system prompt. *(Revised)*
*   **Admin AI Agent:** Primary user request handler, plans tasks, uses `ManageTeamTool` (specifying role, persona, provider, model) and `SendMessageTool`. Defined in `config.yaml`. *(Revised Role)*
*   **Agent Manager:**
    *   Central registry for *all* agents (bootstrap + dynamic).
    *   **Injects standard instructions (tools, ID, team, comms) into agent system prompts.** *(New Responsibility - Phase 9)*
    *   Handles dynamic agent instantiation (`_create_agent_internal`) including **provider/model validation against allowed list**, provider management, sandbox setup. *(Revised - Phase 9)*
    *   Handles dynamic agent deletion (`delete_agent_instance`) including cleanup. *(Phase 9)*
    *   Manages team structures (`add_agent_to_team`, etc.) *in memory*. *(Phase 9)*
    *   Routes Admin AI's `ManageTeamTool` calls to internal methods. *(Phase 9)*
    *   Routes intra-team communication (`SendMessageTool`). *(Completed)*
    *   Manages autonomous agent activation cycles. *(Completed)*
    *   Handles session persistence (saving/loading dynamic agent configs + histories). *(Revised - Phase 9)*
*   **Dynamic Agent/Team Management:** In-memory CRUD via Admin AI. *(Phase 9)*
*   **Tooling:** `BaseTool`, `ToolExecutor`, `FileSystemTool`, `SendMessageTool`, **`ManageTeamTool`** (with enhanced `list_agents`, `list_teams` actions). *(Revised - Phase 9)*
*   **Configuration (`config.yaml`):** Defines `admin_ai`, **defines `allowed_sub_agent_models` list per provider**. *(Revised - Phase 9)*
*   **Settings (`settings.py`):** Loads bootstrap config and `allowed_sub_agent_models`. *(Revised - Phase 9)*
*   **Session Persistence:** Saving/Loading full state including dynamic agent configs, histories, teams. *(Revised - Phase 9)*
*   **Project Management (Basic):** API/logic for projects/sessions. *(Phase 9)*
*   **Human UI:**
    *   Dynamically updates agent/team displays via WebSockets. *(Phase 10)*
    *   Project/Session management UI. *(Phase 10)*
    *   Conversation view showing Admin AI, dynamic agents, intra-team messages. *(Phase 10)*
    *   Basic authentication. *(Phase 10)*
*   **WebSocket Communication:** Real-time streaming + dynamic state updates (`agent_added`, `agent_deleted`, `team_created`, etc.). *(Revised - Phase 9/10)*
*   **Basic Sandboxing:** Created dynamically for agents. *(Completed/Adapted)*
*   **LLM Integration:** OpenRouter, Ollama, OpenAI providers. *(Completed)*
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`.

**Out of Scope (Deferred to Future Phases 11+):**

*   Google LLM Provider.
*   ConfigTool (Replaced).
*   Dynamic changes via `config.yaml` (post-startup).
*   Dynamic LLM Provider *Type* Management.
*   Advanced Collaboration (complex delegation, conflict resolution, hierarchy).
*   Advanced Admin AI Intelligence (planning, memory, self-improvement).
*   Resource limiting for dynamic agents.
*   Multi-Team Projects.
*   Agent prompt updates *after* creation (e.g., on team change).
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
*   **Configuration:** YAML (`PyYAML`) for bootstrap & allowed models, `.env`. *(Revised)*
*   **State Management:** In-memory dictionaries in `AgentManager`.
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state (dynamic configs + histories). *(Revised)*
*   **Authentication (Basic):** Likely FastAPI middleware/dependencies *(Phase 10)*.
*   **XML Parsing:** Standard library `re`, `html`.

## 4. Proposed Architecture Refinement (Conceptual - Phase 9/10 - Dynamic V2)

(Diagram remains similar to V1, emphasizes Manager's role in prompt injection and validation)

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
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>+ Dynamic Agent/Team CRUD Methods (P9)<br>+ Routes Admin AI Tool Calls (P9)<br>+ Routes Intra-Team Msgs ✅<br>+ **Injects Standard Prompts (P9)**<br>+ **Validates Provider/Model (P9)**<br>+ Session Save/Load Logic (P9)<br>Controls All Agents"] %% Enhanced Role
        CONFIG_MANAGER["📝 Config Manager <br>(Reads config.yaml ONCE)"] %% Reduced Role

        subgraph Agents ["Bootstrap & Dynamic Agents"]
            direction LR
             ADMIN_AI["🤖 Admin AI Agent <br>(Loaded from Config)<br>Uses ManageTeamTool<br>Uses SendMessageTool"]
            DYNAMIC_AGENT_1["🤖 Dynamic Agent 1<br>(Created by Manager)<br>Receives Injected Prompt<br>Uses Tools"]
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
             TOOL_MANAGE_TEAM["🛠️ ManageTeamTool (P9)<br>Enhanced List Actions<br>Signals AgentManager"] %% REVISED
         end

         SANDBOXES["📁 Sandboxes <br>(Created Dynamically)"]
         PROJECT_SESSIONS["💾 Project/Session Storage <br>(Incl. Dynamic Configs) (P9)"]
    end

    subgraph External
        LLM_API_SVC["☁️ Ext. LLM APIs (OR, OpenAI)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(AdminAI + Allowed Models)"] %% REVISED
        DOT_ENV[".env File <br>(Secrets - Read Only) ✅"]
    end

    %% --- Connections --- (Mostly unchanged, interpretation shifts)
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP (API Calls, Auth, Session Mgmt) --> FASTAPI;
    Frontend -- WebSocket (Receives dynamic updates) --> WS_MANAGER;

    FASTAPI -- Calls Session Ops --> AGENT_MANAGER;
    FASTAPI -- Manages --> AGENT_MANAGER;
    WS_MANAGER -- Forwards Msgs / Sends Logs & Updates --> Frontend;
    WS_MANAGER -- Forwards User Msgs --> AGENT_MANAGER; # Routes to AdminAI

    AGENT_MANAGER -- "Loads Bootstrap Agent(s)" --> CONFIG_YAML;
    AGENT_MANAGER -- "Loads Allowed Models" --> CONFIG_YAML; # New read
    AGENT_MANAGER -- "Reads Config/Secrets" --> DOT_ENV;
    AGENT_MANAGER -- "Instantiates/Reuses" --> LLM_Providers;
    AGENT_MANAGER -- "Creates/Deletes/Manages" --> Agents;
    AGENT_MANAGER -- "**Injects Standard Context into Prompts**" --> Agents; # New Interaction
    AGENT_MANAGER -- "Handles ManageTeamTool Signals" --> AGENT_MANAGER;
    AGENT_MANAGER -- Routes Tool Calls --> TOOL_EXECUTOR;
    AGENT_MANAGER -- "Handles Agent-to-Agent Msgs & Activates Target" --> Agents;
    AGENT_MANAGER -- "Saves/Loads Dynamic Configs + Histories" --> PROJECT_SESSIONS;

    ADMIN_AI -- "Uses ManageTeamTool (Requests Provider/Model)" --> TOOL_EXECUTOR;
    ADMIN_AI -- "Uses SendMessageTool" --> TOOL_EXECUTOR;
    ADMIN_AI -- "Uses Provider" --> LLM_Providers;
    ADMIN_AI -- "Streams Text" --> AGENT_MANAGER;

    DYNAMIC_AGENT_1 -- "Uses Tools based on Injected Info" --> TOOL_EXECUTOR;
    DYNAMIC_AGENT_1 -- "Uses Provider" --> LLM_Providers;
    DYNAMIC_AGENT_1 -- "Streams Text" --> AGENT_MANAGER;

    TOOL_EXECUTOR -- Executes --> TOOL_FS;
    TOOL_EXECUTOR -- Executes --> TOOL_SENDMSG;
    TOOL_EXECUTOR -- Executes --> TOOL_MANAGE_TEAM;

    TOOL_MANAGE_TEAM -- "Signals Manager (Action & Args)" --> AGENT_MANAGER;

    CONFIG_MANAGER -- "Reads Bootstrap Config" --> CONFIG_YAML;

    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

```

## 5. Development Phases & Milestones

**Phase 1-8 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent Basics, Static Config Loading, Sandboxing, Basic Tools, LLM Abstraction, UI Enhancements, XML Tool Calling, Static Agent Configuration UI.

**Phase 9: Dynamic Agent Management V2 (Current / Next)**
*   **Goal:** Implement dynamic agent creation with framework-injected prompts, enhanced `ManageTeamTool`, provider/model validation, and adapted persistence.
*   [ ] **Configuration (`config.yaml`):**
    *   [ ] Define *only* `admin_ai` agent.
    *   [ ] Add `allowed_sub_agent_models` section mapping provider names to lists of allowed model strings.
    *   [ ] Revise `admin_ai` system prompt: Focus on planning, requesting agents via `ManageTeamTool` (specifying provider/model), using `list_agents`/`list_teams` for state, and delegating via `SendMessageTool`. Remove detailed tool instructions.
*   [ ] **Settings (`settings.py`):**
    *   [ ] Load `allowed_sub_agent_models` into a `settings` attribute.
*   [ ] **Agent Manager (`agents/manager.py`):**
    *   [ ] **`_create_agent_internal`:**
        *   Implement provider/model validation against `settings.allowed_sub_agent_models`. Reject creation if invalid.
        *   Construct the final system prompt by appending standard tool/ID/team/comms instructions to the prompt received from `ManageTeamTool`. Use this combined prompt when creating the `Agent` instance and store it on `agent.agent_config`.
    *   [ ] **`_handle_manage_team_action`:** Ensure it passes validated provider/model to `create_agent_instance`. Correctly handle return data for `list_agents`/`list_teams` feedback.
    *   [ ] **`get_agent_info_list` / `get_team_info_dict`:** Ensure methods return data in a format usable for Admin AI feedback.
*   [ ] **Tools (`ManageTeamTool`):**
    *   [ ] Update `list_agents` action to accept optional `team_id` parameter. Modify `execute` to handle this.
*   [ ] **Agent Core (`agents/core.py`):**
    *   [ ] Verify `Agent.__init__` correctly uses the potentially long, combined system prompt.
*   [ ] **Tool Executor (`tools/executor.py`):**
    *   [ ] Regenerate/update XML descriptions to reflect `ManageTeamTool` changes.
*   [ ] **Session Persistence (`agents/manager.py`):**
    *   [ ] Ensure `save_session` correctly saves the *final combined prompt* (from `agent.agent_config`).
    *   [ ] Ensure `load_session` correctly uses the saved configuration (including combined prompt) when calling `_create_agent_internal`.
*   [ ] **Testing:**
    *   [ ] Test Admin AI creating agent with valid/invalid models.
    *   [ ] Test Admin AI using `list_agents` (all/filtered) and `list_teams`. Verify feedback content.
    *   [ ] Retest Snake Game: Verify Admin AI creates agent -> gets ID feedback -> delegates -> **Verify created agent receives injected prompt** -> **Verify created agent saves code using `file_system` tool based on injected instructions.**

**Phase 10: Dynamic UI & Collaboration Polish (Planned)**
*   **Goal:** Implement dynamic UI updates reflecting in-memory state, Session Management UI, basic collaboration flows, logging/auth.
*   [ ] **Frontend UI (`static/js/app.js`, `templates/index.html`):** Implement dynamic updates for agent list/status/teams via WS messages (`agent_added`, etc.). Add Session Management UI.
*   [ ] **Workflow Testing:** Refine Coder -> Reviewer flows.
*   [ ] **Logging & Auth:** Implement as planned.

**Future Phases (11+) (High-Level)**
*   **Phase 11: Advanced Collaboration & Admin AI Intelligence.**
*   **Phase 12: Resource Management & Error Handling.**
*   **Phase 13+:** Multi-Team Projects, Hierarchy, GeUI, Advanced I/O, etc.

**Phase 16: Create Project Plan for Next Iteration:** Re-evaluate and plan.
