<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.3 (Phase 9 Planned)
**Date:** 2025-04-06 <!-- Updated Date -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   **Enable agents to communicate and collaborate autonomously within defined "Teams" to achieve user goals, potentially engaging in multiple internal exchanges before interacting with the user.** *(New Focus - Enhanced)*
*   **Implement persistence for collaborative sessions within a Project structure, allowing users to save and reload work.** *(New Focus)*
*   Implement a **Human User Interface** adaptable to managing Projects, Sessions, and Team interactions. *(Revised Scope)*
*   Enable real-time communication between the backend and frontend using WebSockets, including categorized logs.
*   Support multiple, configurable LLM agents capable of working within teams.
*   Utilize the **XML-based tool calling mechanism** for agent capabilities. *(Completed)*
*   Allow agents to utilize tools within sandboxed environments.
*   *(Future Goals)* Implement an **Admin AI layer** capable of monitoring and managing team collaborations and projects. Explore advanced collaboration patterns, dynamic team management, Generative UI (GeUI), multi-modal inputs, voice control.

## 2. Scope

**In Scope (Phases up to ~11):**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling.
*   **Agent Core:** Agent class definition, state management, XML tool parsing. *(Completed)*
*   **Agent Manager:** Coordination logic for multiple agents, **routing intra-team communication**, agent loading, **managing autonomous agent activation cycles within a team**, session persistence logic. *(Revised)*
*   **Team Concept (Foundation):** Defining teams (e.g., via config), basic manager awareness. *(Phase 9)*
*   **Agent Communication:** Mechanism for agent-to-agent messaging within a team (e.g., `SendMessageTool` mediated by `AgentManager`). *(Phase 9)*
*   **Autonomous Operation:** Logic enabling agents receiving intra-team messages to activate and process them, facilitating chained interactions without immediate user input. Agents determine completion or need for user interaction based on prompts/logic. *(Phase 9)*
*   **Session Persistence:** Saving and Loading agent histories associated with a specific task/session within a Project structure (e.g., `projects/project_name/session_name/histories.json`). *(Phase 9)*
*   **Project Management (Basic):** API/logic for creating projects, saving sessions, listing/loading sessions. *(Phase 9)*
*   **Human UI:** Basic UI elements for Project/Session management (Save, Load, List), display of team structure, categorized log streaming, basic authentication. *(Phase 10)* Agent Config CRUD UI available. *(Revised)*
*   **Configuration:** Loading settings from `config.yaml`/`.env`. Backend API & UI for CRUD operations on `config.yaml` (requires restart). *(Completed)*
*   **WebSocket Communication:** Real-time streaming of agent outputs/status (including internal team exchanges), plus categorized backend logs. *(Phase 10)*
*   **Basic Sandboxing:** Agent file operation directories. *(Completed)*
*   **Tooling:** `BaseTool`, `ToolExecutor`, `FileSystemTool`, **`SendMessageTool`** *(Phase 9)*.
*   **LLM Integration:** Support for OpenRouter, Ollama, OpenAI via provider abstraction. *(Completed)*
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`.

**Out of Scope (Deferred to Future Phases 11+):**

*   **Google LLM Provider:** Removed from immediate plan.
*   **ConfigTool:** Deferred, Admin AI's role shifted.
*   **Advanced Collaboration:** Complex delegation, task decomposition, automated review workflows, agent hierarchy within teams, conflict resolution.
*   **Admin AI Management:** Active management of teams/projects by an Admin AI agent.
*   **Dynamic Agent/Team Management:** Creating/modifying agents or teams without restart.
*   **Multi-Team Projects:** Multiple distinct teams operating concurrently within the same project scope.
*   **Sophisticated Completion/Interrupt Logic:** Advanced reasoning by agents about when exactly to stop or ask the user (initially relies on basic LLM instruction following).
*   **Generative UI (GeUI):** Dynamic UI generation by LLMs.
*   **Advanced I/O:** Camera, microphone (STT), speaker (TTS), voice control.
*   Advanced Authentication / Multi-User Support.
*   Sophisticated automated testing suite.

## 3. Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library, `aiohttp`
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (`PyYAML`), `.env` files (`python-dotenv`), `ConfigManager`. *(Completed)*
*   **Data Handling:** Pydantic (via FastAPI)
*   **Persistence:** JSON files for session state/histories. *(Planned)*
*   **Authentication (Basic):** Likely FastAPI middleware/dependencies *(Phase 10)*.
*   **XML Parsing:** Standard library `re`, `html`.

## 4. Proposed Architecture Refinement (Conceptual - Phase 9/10)

(Diagram remains largely the same, but the *implications* of the connections are enhanced, especially Agent Manager's role in routing and activation)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_COMS["Coms Page / Session View <br>(Log Stream Filter - P10)<br>Adv I/O: P11+"]
        UI_ADMIN["Project/Session Mgmt Page <br>(Save/Load UI - P10)<br>Config CRUD UI (P8 ✅)<br>Auth UI: P10<br>Refresh Button ✅"]
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend <br>+ Config CRUD API (P8 ✅)<br>+ Session API (P9)<br>+ Auth API (P10)<br>+ Log Endpoints (P10?)"]
        WS_MANAGER["🔌 WebSocket Manager <br>+ Log Categories (P10)"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>+ Intra-Team Msg Routing & Activation (P9)<br>+ Session Save/Load Logic (P9)<br>+ Tool History Handling ✅<br>Controls All Agents"] %% Enhanced Role
        CONFIG_MANAGER["📝 Config Manager <br>(Safe R/W config.yaml) P8 ✅"]

        subgraph Agents ["Team Agents"]
            direction LR
            AGENT_INST_1["🤖 Worker Agent 1 <br>+ XML Tool Parsing ✅<br>+ Uses SendMessageTool (P9)"]
            AGENT_INST_N["🤖 Worker Agent N <br>+ XML Tool Parsing ✅<br>+ Uses SendMessageTool (P9)"]
            ADMIN_AI["🤖 Admin AI (Future - P11+)"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers <br>(src/llm_providers/)"]
             PROVIDER_OR["🔌 OpenRouter Provider ✅"]
             PROVIDER_OLLAMA["🔌 Ollama Provider ✅"]
             PROVIDER_OPENAI["🔌 OpenAI Provider ✅"]
         end

         subgraph Tools
             TOOL_EXECUTOR["🛠️ Tool Executor<br>+ XML Desc Gen ✅"]
             TOOL_FS["📄 FileSystem Tool ✅"]
             TOOL_SENDMSG["🗣️ SendMessageTool (P9)"]
             %% Other tools...
         end

         SANDBOXES["📁 Sandboxes ✅"]
         PROJECT_SESSIONS["💾 Project/Session Storage <br>(e.g., JSON histories) (P9)"]
    end

    subgraph External
        LLM_API_SVC["☁️ Ext. LLM APIs (OR, OpenAI)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(Read/Write via Config Manager) ✅"]
        DOT_ENV[".env File <br>(Secrets - Read Only) ✅"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP (API Calls, Auth, Session Mgmt) --> FASTAPI;
    Frontend -- WebSocket --> WS_MANAGER;

    FASTAPI -- Calls CRUD Ops --> CONFIG_MANAGER;
    FASTAPI -- Calls Session Ops --> AGENT_MANAGER;
    FASTAPI -- Manages --> AGENT_MANAGER;
    WS_MANAGER -- Forwards Msgs / Sends Logs --> Frontend;
    WS_MANAGER -- Forwards User Msgs --> AGENT_MANAGER;

    AGENT_MANAGER -- Controls --> Agents;
    AGENT_MANAGER -- "Reads Initial Config Via Settings Module" --> CONFIG_YAML;
    AGENT_MANAGER -- "Reads Config/Secrets" --> DOT_ENV;
    AGENT_MANAGER -- Injects --> LLM_Providers;
    AGENT_MANAGER -- Routes Tool Calls --> TOOL_EXECUTOR;
    AGENT_MANAGER -- "Generates & Injects XML Prompts ✅" --> Agents;
    AGENT_MANAGER -- "Appends Tool Results to Agent History ✅" --> Agents;
    AGENT_MANAGER -- "Handles Agent-to-Agent Msgs & Activates Target (P9)" --> Agents; %% Enhanced Link
    AGENT_MANAGER -- "Saves/Loads Histories" --> PROJECT_SESSIONS;

    %% Agent Connections (Illustrative)
    AGENT_INST_1 -- Uses --> LLM_Providers;
    AGENT_INST_1 -- "Streams Text ✅" --> AGENT_MANAGER;
    AGENT_INST_1 -- "Parses Own XML ✅" --> AGENT_INST_1;
    AGENT_INST_1 -- "Yields Tool Request (FS, SendMessage)" --> AGENT_MANAGER;

    AGENT_INST_N -- Uses --> LLM_Providers;
    AGENT_INST_N -- "Streams Text ✅" --> AGENT_MANAGER;
    AGENT_INST_N -- "Parses Own XML ✅" --> AGENT_INST_N;
    AGENT_INST_N -- "Yields Tool Request" --> AGENT_MANAGER;

    TOOL_EXECUTOR -- Executes --> TOOL_FS;
    TOOL_EXECUTOR -- Executes --> TOOL_SENDMSG;

    TOOL_SENDMSG -- "Signals Manager (Target & Msg)" --> AGENT_MANAGER; %% Clarified Signal

    CONFIG_MANAGER -- Reads/Writes --> CONFIG_YAML;

    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

```

## 5. Development Phases & Milestones

**Phase 1-8 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent, Config Loading, Sandboxing, Basic Tools, LLM Abstraction, UI Enhancements, XML Tool Calling Refinement, **Agent Configuration UI Management**.

**Phase 9: Agent Communication, Autonomous Operation & Session Persistence Foundation (Current / Next)**
*   **Goal:** Establish basic agent-to-agent communication, enable autonomous chained processing within a team, and implement session saving/loading.
*   [ ] **Analysis & Design:**
    *   [ ] Define "Team" structure (e.g., list of agent IDs per team in `config.yaml` or a separate section).
    *   [ ] Design agent communication flow: `SendMessageTool` signals `AgentManager` -> Manager appends message to target's history -> Manager triggers target agent's `process_message` loop if idle.
    *   [ ] Design how agents signal task completion or need for user input (e.g., specific phrasing in response, or lack of further tool calls).
    *   [ ] Define Project/Session storage format (e.g., `projects/MyProject/Session_Timestamp/agent_histories.json`, maybe include team definition).
*   [ ] **Backend - SendMessageTool:**
    *   [ ] Implement `src/tools/send_message.py` inheriting `BaseTool`. Parameters: `target_agent_id`, `message_content`.
    *   [ ] Tool execution signals `AgentManager` with source agent ID, target agent ID, and message content.
*   [ ] **Backend - AgentManager Enhancements:**
    *   [ ] Add logic to parse/understand team structures from config.
    *   [ ] Implement handler for `SendMessageTool` signal:
        *   [ ] Identify source and target agents within their team context.
        *   [ ] Append message to target's history (e.g., `{'role': 'user', 'content': f'[From @{source_agent_id}]: {message_content}'}`). *Treating inter-agent messages as 'user' role for the recipient simplifies prompt structure.*
        *   [ ] **Crucially:** If the target agent is `IDLE`, create an `asyncio.task` to run its `_handle_agent_generator` loop (similar to how user messages are handled, but triggered internally).
        *   [ ] Manage overall session state: Track which agents are active in the current collaborative task. Only signal completion/readiness to the user when relevant agents become idle *or* one explicitly requests user interaction.
    *   [ ] Implement `save_session(project_name, session_name)` method: Collect histories of agents involved in the current context, save to JSON.
    *   [ ] Implement `load_session(project_name, session_name)` method: Read histories, load into corresponding agents, potentially set agents to `IDLE`.
*   [ ] **Backend - Agent Core & Prompts:**
    *   [ ] Ensure `Agent.process_message` can be triggered even if the last message wasn't from the direct user (i.e., from another agent).
    *   [ ] Refine example system prompts to instruct agents on how/when to use `SendMessageTool` and how to determine task completion or the need for user input.
*   [ ] **Backend - API for Persistence:**
    *   [ ] Add FastAPI endpoints (e.g., `POST /api/projects/.../sessions`, `GET /api/projects/.../sessions`, `POST /api/sessions/.../load`) for save/list/load.
*   [ ] **Configuration:** Update `config.yaml` example to show team grouping. Update `.env.example` if needed (e.g., projects directory).
*   [ ] **Testing:**
    *   [ ] Test simple `A -> B` communication via `SendMessageTool`.
    *   [ ] Test chained `A -> B -> A` communication without user intervention.
    *   [ ] Verify that the system waits for internal exchanges to complete before idling (unless user input is requested).
    *   [ ] Test save/load functionality via API. Verify history restoration and continued operation.

**Phase 10: UI for Teams/Sessions & Foundational Collaboration (Planned)**
*   **Goal:** Adapt UI for teams/sessions, visualize autonomous collaboration, implement basic collaborative workflows, log streaming, and auth.
*   [ ] **Frontend - UI Enhancements:**
    *   [ ] Display agents grouped by Team.
    *   [ ] Add UI controls for Project/Session management.
    *   [ ] Display current Project/Session context.
    *   [ ] Clearly distinguish user messages from intra-agent messages in the conversation view. Show which agent is "speaking" to another.
    *   [ ] Potentially add visual cues when the system is processing internal agent exchanges vs. waiting for the user.
*   [ ] **Backend & Frontend - Collaboration Flow:** Test more involved multi-agent workflows (decomposition, simple review).
*   [ ] **Backend & Frontend - Logging & Auth:** Implement categorized log streaming and basic password authentication.

**Future Phases (11+) (High-Level)**
*   **Phase 11: Advanced Collaboration & Admin AI Foundation.**
*   **Phase 12: Admin AI Management & Dynamic Teams.**
*   **Phase 13: Multi-Team Projects & Hierarchy.**
*   **Phase 14+:** GeUI, Advanced I/O, Voice Control, Advanced Authentication, etc.

**Phase 16: Create Project Plan for Next Iteration:** Re-evaluate and plan.
