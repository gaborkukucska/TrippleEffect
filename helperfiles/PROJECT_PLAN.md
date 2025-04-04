<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 1.8 (Planning Admin AI & Human UI Layers)
**Date:** 2025-04-04 (Note: Should be updated with actual dev dates)

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI layer** capable of monitoring and controlling framework components (initially via config modification). *(Planned)*
*   Implement a **Human User Interface** with distinct views for communication/monitoring ("Coms") and administration ("Admin"). *(Planned)*
*   Enable real-time communication between the backend and frontend using WebSockets, including categorized logs.
*   Support multiple, configurable LLM agents (including the Admin AI) capable of collaborating on tasks.
*   Allow agents to utilize tools within sandboxed environments, including specialized tools for the Admin AI.
*   Ensure the framework is reasonably lightweight and performant for potential use on platforms like Termux.
*   Integrate with various LLM API providers (OpenRouter, Ollama, OpenAI, **Google** - *planned*).
*   Structure the project logically for maintainability and extensibility.
*   Adhere to the specified development principles.
*   *(Future Goals)* Explore advanced UI concepts (GeUI), multi-modal inputs (camera, mic), voice control, and dynamic agent management.

## 2. Scope

**In Scope (Phases up to ~10):**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling.
*   **Agent Core:** Agent class definition, state management, interaction logic via abstracted LLM providers.
*   **Agent Manager:** Coordination logic for multiple agents, agent reloading/update on config change *(Phase 8/9)*.
*   **Admin AI Agent (Foundation):** Configuration and basic interaction capability via tools *(Phase 9)*.
*   **Human UI (Foundation):** Two-page structure (Coms, Admin), basic authentication, backend log streaming/filtering UI *(Phase 10)*.
*   **Configuration:** Loading settings from `config.yaml`/`.env`. Backend API & UI for CRUD operations on `config.yaml` *(Phase 8)*. Safe config modification tool for Admin AI *(Phase 9)*.
*   **WebSocket Communication:** Real-time streaming of agent outputs/status, plus categorized backend logs *(Phase 10)*.
*   **Basic Sandboxing:** Agent file operation directories.
*   **Tooling:** `BaseTool`, `ToolExecutor`, `FileSystemTool`, `ConfigTool` *(Phase 9)*.
*   **LLM Integration:** Support for OpenRouter, Ollama, OpenAI, **Google** *(Phase 9)* via provider abstraction.
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`.

**Out of Scope (Deferred to Future Phases 11+):**

*   **Generative UI (GeUI):** Dynamic UI generation by LLMs.
*   **Advanced I/O:** Camera input processing, microphone input (speech-to-text), speaker output (text-to-speech).
*   **Voice Control:** Full framework control via voice commands.
*   **Advanced Authentication:** Voice matching, multi-user support.
*   **Dynamic Admin AI Control:** Real-time modification of running agents (beyond config file changes).
*   Complex agent-to-agent delegation protocols.
*   Advanced UI frameworks (React, Vue, etc.).
*   Advanced sandboxing (containerization).
*   Sophisticated automated testing suite.

## 3. Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library, `aiohttp`, **`google-generativeai`** *(Phase 9)*.
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (`PyYAML`), `.env` files (`python-dotenv`).
*   **Data Handling:** Pydantic (via FastAPI)
*   **Authentication (Basic):** Likely FastAPI middleware/dependencies *(Phase 10)*.

## 4. Proposed Architecture Refinement

(Diagram updated to reflect planned Admin AI and UI structure changes)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer - Phase 10+]
        direction LR
        UI_COMS["Coms Page <br>(GeUI: Phase 11+)<br>Log Stream Filter<br>Adv I/O: Phase 11+"]
        UI_ADMIN["Admin Page <br>(Config CRUD: Phase 8)<br>Settings View"]
        AUTH_UI["Login UI <br>(Basic Auth: Phase 10)"]
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend <br>+ Config CRUD API (P8)<br>+ Auth API (P10)<br>+ Log Endpoints (P10?)"]
        WS_MANAGER["🔌 WebSocket Manager <br>+ Log Categories (P10)"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>+ Reload Logic (P8/9)<br>Controls All Agents"]

        subgraph Agents
            ADMIN_AI["🤖 Admin AI Agent <br>(Google Provider - P9)<br>Uses ConfigTool"]
            AGENT_INST_1["🤖 Worker Agent 1"]
            AGENT_INST_N["🤖 Worker Agent N"]
            GEUI_AGENT["🤖 GeUI Agent(s) (P11+)"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers <br>(src/llm_providers/)"]
             PROVIDER_GOOGLE["🔌 Google Provider (P9)"]
             PROVIDER_OR["🔌 OpenRouter Provider"]
             PROVIDER_OLLAMA["🔌 Ollama Provider"]
             PROVIDER_OPENAI["🔌 OpenAI Provider"]
         end

         subgraph Tools
             TOOL_EXECUTOR["🛠️ Tool Executor"]
             TOOL_CONFIG["📝 ConfigTool (P9)<br>Safe R/W config.yaml"]
             TOOL_FS["📄 FileSystem Tool"]
             %% Other tools...
         end

         SANDBOXES["📁 Sandboxes"]
         CONFIG_WRITER["📝 Config Writer <br>(Used by ConfigTool - P9)"]
    end

    subgraph External
        GOOGLE_API["☁️ Google AI APIs"]
        LLM_API_SVC["☁️ Ext. LLM APIs (OR, OpenAI)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(Read/Write via ConfigTool)"]
        DOT_ENV[".env File <br>(Secrets - Read Only)"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP (API Calls, Auth) --> FASTAPI;
    Frontend -- WebSocket --> WS_MANAGER;

    FASTAPI -- Manages --> AGENT_MANAGER;
    FASTAPI -- Triggers --> CONFIG_WRITER; %% Via API calls handled by ConfigTool
    FASTAPI -- Notifies --> AGENT_MANAGER; %% On config change
    WS_MANAGER -- Forwards Msgs / Sends Logs --> Frontend;
    WS_MANAGER -- Forwards User Msgs --> AGENT_MANAGER;

    AGENT_MANAGER -- Controls --> ADMIN_AI;
    AGENT_MANAGER -- Controls --> AGENT_INST_1;
    AGENT_MANAGER -- Controls --> AGENT_INST_N;
    AGENT_MANAGER -- Controls --> GEUI_AGENT;

    AGENT_MANAGER -- "Reads Config/Secrets" --> CONFIG_YAML;
    AGENT_MANAGER -- "Reads Config/Secrets" --> DOT_ENV;
    AGENT_MANAGER -- Injects --> LLM_Providers;
    AGENT_MANAGER -- Routes Tool Calls --> TOOL_EXECUTOR;

    ADMIN_AI -- Uses --> PROVIDER_GOOGLE;
    ADMIN_AI -- Requests Tool --> TOOL_EXECUTOR;
    %% Other agents use their providers

    TOOL_EXECUTOR -- Executes --> TOOL_CONFIG;
    TOOL_EXECUTOR -- Executes --> TOOL_FS;

    TOOL_CONFIG -- Uses --> CONFIG_WRITER;
    CONFIG_WRITER -- R/W --> CONFIG_YAML;

    PROVIDER_GOOGLE -- Interacts --> GOOGLE_API;
    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

```

## 5. Development Phases & Milestones

**Phase 1-5.5 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent, Config Loading, Sandboxing, Basic Tools, LLM Abstraction.

**Phase 6: UI Enhancements & Initial Testing (Completed)**
*   [X] UI Layout, Status Indicators, Client History, File Upload Context, Config Viewing.
*   [X] Initial multi-provider testing (ongoing refinement needed).

**Phase 7: Refinement, Optimization & Documentation (Next)**
*   [ ] Improve error handling and reporting (backend and UI).
*   [ ] Optimize performance, especially WebSocket handling and LLM calls.
*   [ ] Refine sandboxing security (more robust path traversal checks, consider limits).
*   [ ] Add more tools (e.g., simple web search using `aiohttp`).
*   [ ] Write comprehensive usage instructions in `README.md`.
*   [ ] Add more details/refine `FUNCTIONS_INDEX.md`.
*   [ ] Code cleanup and final review.

**Phase 8: Agent Configuration UI Management (Planned)**
*   **Goal:** Allow users to create, edit, and delete agent configurations via the web UI (foundation for Admin page).
*   [ ] **Backend API:** CRUD endpoints (`POST`, `PUT`, `DELETE`) for `/api/config/agents`. Pydantic models for validation.
*   [ ] **Backend Config Handling:** Safe read/modify/write functions for `config.yaml` (likely in `src/config/`). Error handling. **Strictly no secrets (API keys) managed here.**
*   [ ] **Agent Reloading:** Decide on strategy (manual restart vs. dynamic reload). Implement chosen strategy in `AgentManager`.
*   [ ] **Frontend UI:** Basic forms/modals for Add/Edit/Delete operations. API integration. Dynamic UI updates. Error display. (To be integrated into Admin Page in Phase 10).
*   [ ] **Security:** Backend input validation. Confirm file write restrictions.
*   [ ] **Documentation:** Update `README.md` for basic config UI usage.

**Phase 9: Admin AI Layer Foundation (Planned)**
*   **Goal:** Introduce the Admin AI agent and enable basic control via config modification.
*   [ ] **Backend - Google Provider:**
    *   [ ] Add `google-generativeai` to `requirements.txt`.
    *   [ ] Implement `src/llm_providers/google_provider.py` inheriting `BaseLLMProvider`.
    *   [ ] Handle Google API key (`GOOGLE_API_KEY` in `.env`).
    *   [ ] Implement `stream_completion`, checking Gemini Pro's tool/function calling support.
*   [ ] **Backend - Config Tool:**
    *   [ ] Implement `src/tools/config_tool.py` inheriting `BaseTool`.
    *   [ ] Define actions like `read_config`, `add_agent`, `update_agent`, `delete_agent`.
    *   [ ] Parameters should specify agent details (ID, provider, model, prompt, etc. - *NO API Keys*).
    *   [ ] Use the safe config read/write functions from Phase 8.
    *   [ ] Provide clear success/error messages as return values.
*   [ ] **Backend - Agent Manager:** Ensure `ToolExecutor` registers `ConfigTool`. Ensure manager can reload config/agents upon successful `ConfigTool` execution (using Phase 8 mechanism).
*   [ ] **Configuration:** Update `.env.example` with `GOOGLE_API_KEY`. Update `config.yaml` example to show how to define the Admin AI agent using the `google` provider and giving it access ONLY to `ConfigTool` (if possible, else document the risk).
    *   *Model Note:* Confirm exact model name for "Gemini 2.5 Pro Experimental" on Google's API and update config.
*   [ ] **Testing:** Manually send commands to the Admin AI via the existing UI (e.g., "AdminAI, use ConfigTool to add agent X..."). Verify `config.yaml` changes and agent reloading (or check console for restart instruction).

**Phase 10: Human UI Foundation (Planned)**
*   **Goal:** Restructure UI into Coms/Admin pages and implement log streaming & basic auth.
*   [ ] **Frontend - Structure:**
    *   [ ] Refactor `index.html` and `app.js` to manage two main views/pages ("Coms", "Admin"). Use simple routing (e.g., hash-based) or div visibility toggling.
    *   [ ] Design layout for Coms page (placeholder for GeUI, collapsible log stream at bottom).
    *   [ ] Design layout for Admin page (placeholder for settings).
*   [ ] **Backend - Logging:**
    *   [ ] Introduce log levels/categories (e.g., 'INFO', 'WARN', 'ERROR', 'AGENT_MSG', 'TOOL_CALL').
    *   [ ] Modify logging setup (or use middleware) to capture relevant logs.
    *   [ ] Update `WebSocketManager` or add dedicated endpoint to stream categorized logs to connected clients.
*   [ ] **Frontend - Coms Page:**
    *   [ ] Implement the collapsible log stream display area.
    *   [ ] Add dropdown/buttons to filter logs by category/level.
    *   [ ] Update WebSocket handling in `app.js` to receive and display categorized logs in this area.
*   [ ] **Frontend - Admin Page:**
    *   [ ] Integrate the Agent Config CRUD components (forms/display) developed in Phase 8 into this page.
    *   [ ] Add section to display read-only default settings loaded from `.env` on the backend (requires new API endpoint).
*   [ ] **Backend & Frontend - Authentication:**
    *   [ ] Implement basic password authentication (e.g., HTTP Basic Auth, simple form + session/token).
    *   [ ] Protect the Admin page API endpoints and potentially the main page load.
    *   [ ] Add login form/mechanism to the frontend. Handle login/logout state.

**Future Phases (11+) (High-Level)**
*   **Phase 11: GeUI Implementation:** Design and implement the Generative UI concept for the Coms page. Requires dedicated agent(s) and complex frontend rendering.
*   **Phase 12: Advanced I/O & Voice Control:** Integrate camera, microphone (STT), speaker (TTS) functionalities using browser APIs and backend processing. Implement voice command parsing and execution mapping.
*   **Phase 13: Advanced Admin AI Control:** Enhance Admin AI tools and backend APIs to allow dynamic control over running agents (start, stop, pause, modify prompts/memory).
*   **Phase 14: Advanced Authentication & Multi-User:** Implement more robust authentication (e.g., voice matching) and potentially support multiple users.
*   **Phase 15: Create Project Plan for Next Iteration:** Re-evaluate and plan further enhancements.
