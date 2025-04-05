<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.1 (Phase 8 In Progress)
**Date:** 2025-04-05 <!-- Keep date until phase is complete -->

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI layer** capable of monitoring and controlling framework components (initially via config modification). *(Planned)*
*   Implement a **Human User Interface** with distinct views for communication/monitoring ("Coms") and administration ("Admin"). *(Planned)*
*   Enable real-time communication between the backend and frontend using WebSockets, including categorized logs.
*   Support multiple, configurable LLM agents (including the Admin AI) capable of collaborating on tasks.
*   **Implement XML-based tool calling mechanism (Cline-style) for enhanced compatibility, especially with providers like OpenRouter.** *(Completed)*
*   Allow agents to utilize tools within sandboxed environments, including specialized tools for the Admin AI.
*   Ensure the framework is reasonably lightweight and performant for potential use on platforms like Termux.
*   Integrate with various LLM API providers (OpenRouter, Ollama, OpenAI, **Google** - *planned*).
*   Structure the project logically for maintainability and extensibility.
*   Adhere to the specified development principles.
*   *(Future Goals)* Explore advanced UI concepts (GeUI), multi-modal inputs (camera, mic), voice control, and dynamic agent management.

## 2. Scope

**In Scope (Phases up to ~10):**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling.
*   **Agent Core:** Agent class definition, state management. **Refactored interaction logic to parse XML tool calls from LLM text responses.** *(Completed)*
*   **Agent Manager:** Coordination logic for multiple agents, agent reloading/update on config change *(Simplified for Phase 8 - Restart Required)*, **management of tool result history**. *(Completed)*
*   **Admin AI Agent (Foundation):** Configuration and basic interaction capability via tools *(Phase 9)*.
*   **Human UI (Foundation):** Two-page structure (Coms, Admin), basic authentication, backend log streaming/filtering UI *(Phase 10)*.
*   **Configuration:** Loading settings from `config.yaml`/`.env`. **Backend API & UI for CRUD operations on `config.yaml` *(Phase 8)*.** Safe config modification tool for Admin AI *(Phase 9)*.
*   **WebSocket Communication:** Real-time streaming of agent outputs/status, plus categorized backend logs *(Phase 10)*.
*   **Basic Sandboxing:** Agent file operation directories.
*   **Tooling:** `BaseTool`, `ToolExecutor` (**with XML description generation** *(Completed)*), `FileSystemTool`, `ConfigTool` *(Phase 9)*.
*   **LLM Integration:** Support for OpenRouter, Ollama, OpenAI, **Google** *(Phase 9)* via provider abstraction. **API calls modified to remove native `tools`/`tool_choice` parameters; system prompts updated to instruct XML format.** *(Completed)*
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`.

**Out of Scope (Deferred to Future Phases 11+):**

*   **Generative UI (GeUI):** Dynamic UI generation by LLMs.
*   **Advanced I/O:** Camera input processing, microphone input (speech-to-text), speaker output (text-to-speech).
*   **Voice Control:** Full framework control via voice commands.
*   **Advanced Authentication:** Voice matching, multi-user support.
*   **Dynamic Admin AI Control:** Real-time modification of running agents (beyond config file changes).
*   **Dynamic Agent Reloading without Restart:** Deferred from Phase 8.
*   Complex agent-to-agent delegation protocols.
*   Advanced UI frameworks (React, Vue, etc.).
*   Advanced sandboxing (containerization).
*   Sophisticated automated testing suite.
*   **Native API Tool Calling:** Using provider-specific `tools`/`tool_choice` parameters (standardizing on XML-in-text).

## 3. Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library, `aiohttp`, **`google-generativeai`** *(Phase 9)*.
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (`PyYAML`), `.env` files (`python-dotenv`). **Safe YAML read/write.** *(Phase 8)*
*   **Data Handling:** Pydantic (via FastAPI)
*   **Authentication (Basic):** Likely FastAPI middleware/dependencies *(Phase 10)*.
*   **XML Parsing:** Standard library `re`, `html`.

## 4. Proposed Architecture Refinement

(Diagram reflects Agent responsibility for XML parsing, Manager handling history, and ConfigManager for R/W)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer] %% Updated Title
        direction LR
        UI_COMS["Coms Page <br>(Log Stream Filter - P10)<br>Adv I/O: P11+"]
        UI_ADMIN["Admin Page <br>(Config CRUD UI - P8 ✅)<br>Settings View<br>Auth UI: P10"] %% Updated
        %% AUTH_UI["Login UI <br>(Basic Auth: P10)"] %% Merged into Admin Page area
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend <br>+ Config CRUD API (P8 ✅)<br>+ Auth API (P10)<br>+ Log Endpoints (P10?)"]
        WS_MANAGER["🔌 WebSocket Manager <br>+ Log Categories (P10)"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>+ Reload Signal (Future)<br>+ Tool History Handling ✅<br>Controls All Agents"] %% Updated Reload
        CONFIG_MANAGER["📝 Config Manager <br>(Safe R/W config.yaml) P8 ✅"] %% Added P8 Config Manager

        subgraph Agents
            ADMIN_AI["🤖 Admin AI Agent <br>(Google Provider - P9)<br>Uses ConfigTool"]
            AGENT_INST_1["🤖 Worker Agent 1 <br>+ XML Tool Parsing ✅"]
            AGENT_INST_N["🤖 Worker Agent N <br>+ XML Tool Parsing ✅"]
            GEUI_AGENT["🤖 GeUI Agent(s) (P11+)"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers <br>(src/llm_providers/)"]
             PROVIDER_GOOGLE["🔌 Google Provider (P9)"]
             PROVIDER_OR["🔌 OpenRouter Provider ✅"]
             PROVIDER_OLLAMA["🔌 Ollama Provider ✅"]
             PROVIDER_OPENAI["🔌 OpenAI Provider ✅"]
         end

         subgraph Tools
             TOOL_EXECUTOR["🛠️ Tool Executor<br>+ XML Desc Gen ✅"]
             TOOL_CONFIG["📝 ConfigTool (P9)<br>Uses Config Manager"] %% Updated to use Config Mgr
             TOOL_FS["📄 FileSystem Tool ✅"]
             %% Other tools...
         end

         SANDBOXES["📁 Sandboxes ✅"]
    end

    subgraph External
        GOOGLE_API["☁️ Google AI APIs"]
        LLM_API_SVC["☁️ Ext. LLM APIs (OR, OpenAI)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(Read/Write via Config Manager)"] %% Updated description
        DOT_ENV[".env File <br>(Secrets - Read Only) ✅"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP (API Calls, Auth) --> FASTAPI;
    Frontend -- WebSocket --> WS_MANAGER;

    FASTAPI -- Calls CRUD Ops --> CONFIG_MANAGER; %% Added API to Config Mgr link
    FASTAPI -- Manages --> AGENT_MANAGER;
    %% FastAPI -- Notifies --> AGENT_MANAGER; %% Deferred dynamic notification
    WS_MANAGER -- Forwards Msgs / Sends Logs --> Frontend;
    WS_MANAGER -- Forwards User Msgs --> AGENT_MANAGER;

    AGENT_MANAGER -- Controls --> ADMIN_AI;
    AGENT_MANAGER -- Controls --> AGENT_INST_1;
    AGENT_MANAGER -- Controls --> AGENT_INST_N;
    AGENT_MANAGER -- Controls --> GEUI_AGENT;
    AGENT_MANAGER -- "Reads Initial Config Via Settings Module" --> CONFIG_YAML; %% Clarified reading path
    AGENT_MANAGER -- "Reads Config/Secrets" --> DOT_ENV;
    AGENT_MANAGER -- Injects --> LLM_Providers;
    AGENT_MANAGER -- Routes Tool Calls --> TOOL_EXECUTOR;
    AGENT_MANAGER -- "Generates & Injects XML Prompts ✅" --> Agents;
    AGENT_MANAGER -- "Appends Tool Results to Agent History ✅" --> Agents;

    ADMIN_AI -- Uses --> PROVIDER_GOOGLE;
    ADMIN_AI -- "Streams Text" --> AGENT_MANAGER;
    ADMIN_AI -- "Parses Own XML" --> ADMIN_AI;
    ADMIN_AI -- "Yields Tool Request (ConfigTool)" --> AGENT_MANAGER; %% Example tool request

    AGENT_INST_1 -- Uses --> LLM_Providers;
    AGENT_INST_1 -- "Streams Text ✅" --> AGENT_MANAGER;
    AGENT_INST_1 -- "Parses Own XML ✅" --> AGENT_INST_1;
    AGENT_INST_1 -- "Yields Tool Request (e.g., FileSystemTool)" --> AGENT_MANAGER; %% Example tool request

    AGENT_INST_N -- Uses --> LLM_Providers;
    AGENT_INST_N -- "Streams Text ✅" --> AGENT_MANAGER;
    AGENT_INST_N -- "Parses Own XML ✅" --> AGENT_INST_N;
    AGENT_INST_N -- "Yields Tool Request" --> AGENT_MANAGER;


    TOOL_EXECUTOR -- Executes --> TOOL_CONFIG;
    TOOL_EXECUTOR -- Executes --> TOOL_FS;

    TOOL_CONFIG -- Uses --> CONFIG_MANAGER; %% Updated ConfigTool connection
    %% CONFIG_WRITER Removed, merged into Config Manager
    CONFIG_MANAGER -- Reads/Writes --> CONFIG_YAML; %% Added Config Mgr R/W link

    PROVIDER_GOOGLE -- Interacts --> GOOGLE_API;
    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

```

## 5. Development Phases & Milestones

**Phase 1-7 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent, Config Loading, Sandboxing, Basic Tools, LLM Abstraction, UI Enhancements, Initial Testing, XML Tool Calling Refinement.

**Phase 8: Agent Configuration UI Management (In Progress)**
*   **Goal:** Allow users to create, edit, and delete agent configurations via the web UI (foundation for Admin page). **Requires application restart after changes.**
*   [ ] **Analysis & Planning:** Reviewed project, defined Phase 8 scope, outlined file changes. *(Completed)*
*   [ ] **Backend - Config Manager:**
    *   [ ] Create `src/config/config_manager.py`.
    *   [ ] Implement `ConfigManager` class.
    *   [ ] Add methods: `load_config()`, `save_config()`, `add_agent(config)`, `update_agent(agent_id, config)`, `delete_agent(agent_id)`.
    *   [ ] Include basic validation (e.g., check for `agent_id`, unique IDs).
    *   [ ] Ensure safe YAML writing (backup previous file?).
*   [ ] **Backend - Settings:**
    *   [ ] Modify `src/config/settings.py` to use `ConfigManager.load_config()` during initialization.
*   [ ] **Backend - API:**
    *   [ ] Add Pydantic models in `src/api/http_routes.py` for agent configuration input/output (ensure sensitive fields like `api_key` are handled appropriately - likely excluded or masked).
    *   [ ] Implement `POST /api/config/agents` endpoint: Validate input, call `ConfigManager.add_agent`, save, return success/error (mention restart).
    *   [ ] Implement `PUT /api/config/agents/{agent_id}` endpoint: Validate input, call `ConfigManager.update_agent`, save, return success/error (mention restart).
    *   [ ] Implement `DELETE /api/config/agents/{agent_id}` endpoint: Call `ConfigManager.delete_agent`, save, return success/error (mention restart).
*   [ ] **Frontend - UI Elements (`index.html`, `style.css`):**
    *   [ ] Add "Add Agent" button.
    *   [ ] Add "Edit" and "Delete" buttons next to each agent listed in the "Configuration" section.
    *   [ ] Create basic HTML structure for Add/Edit modal dialogs (initially hidden).
    *   [ ] Add CSS for buttons and modals.
*   [ ] **Frontend - Logic (`app.js`):**
    *   [ ] Modify `displayAgentConfigurations` to add Edit/Delete buttons and attach event listeners.
    *   [ ] Implement function to show the "Add Agent" modal.
    *   [ ] Implement function to show the "Edit Agent" modal, populating it with data fetched via `GET /api/config/agents/{agent_id}` (or use existing data).
    *   [ ] Implement form submission handlers for Add/Edit modals.
        *   Gather form data.
        *   Call `POST` or `PUT` API endpoints.
        *   Handle responses, show success/error messages (incl. restart note).
        *   Close modal and refresh the config list on success using `fetchAgentConfigurations`.
    *   [ ] Implement handler for "Delete" button click.
        *   Show confirmation prompt.
        *   Call `DELETE /api/config/agents/{agent_id}` API endpoint.
        *   Handle responses, show success/error messages (incl. restart note).
        *   Refresh the config list on success.
*   [ ] **Testing:** Test CRUD operations via UI, verify `config.yaml` changes, confirm restart instructions appear.
*   [ ] **Documentation:** Update `README.md` for config UI usage. Update `FUNCTIONS_INDEX.md`.

**Phase 9: Admin AI Layer Foundation (Planned)**
*   **Goal:** Introduce the Admin AI agent and enable basic control via config modification using the XML tool format.
*   [ ] **Backend - Google Provider:** Implement `google_provider.py`. Add dependency. Handle API key.
*   [ ] **Backend - Config Tool:** Implement `config_tool.py` using `ConfigManager`.
*   [ ] **Backend - Agent Manager:** Register `ConfigTool`. Ensure manager signals for restart (or handles dynamic reload in future) after `ConfigTool` use.
*   [ ] **Configuration:** Update `.env.example`, `config.yaml` for Admin AI and XML tool prompt.
*   [ ] **Testing:** Manually test Admin AI commands via UI.

**Phase 10: Human UI Foundation (Planned)**
*   **Goal:** Restructure UI into Coms/Admin pages and implement log streaming & basic auth.
*   [ ] **Frontend - Structure:** Refactor UI for Coms/Admin views.
*   [ ] **Backend - Logging:** Introduce categories, update WebSocket streaming.
*   [ ] **Frontend - Coms Page:** Implement log stream display/filters.
*   [ ] **Frontend - Admin Page:** Integrate Agent Config CRUD (Phase 8). Add read-only settings display.
*   [ ] **Backend & Frontend - Authentication:** Implement basic password auth.

**Future Phases (11+) (High-Level)**
*   **Phase 11: GeUI Implementation:** Design and implement the Generative UI concept.
*   **Phase 12: Advanced I/O & Voice Control:** Integrate camera, microphone (STT), speaker (TTS).
*   **Phase 13: Advanced Admin AI Control:** Enhance Admin AI tools for dynamic control of running agents.
*   **Phase 14: Advanced Authentication & Multi-User:** Implement more robust authentication.
*   **Phase 15: Dynamic Agent Reloading:** Implement agent reloading without requiring a full application restart.
*   **Phase 16: Create Project Plan for Next Iteration:** Re-evaluate and plan.
