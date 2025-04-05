<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 2.2 (Phase 8 Completed)
**Date:** 2025-04-06 <!-- Updated Date -->

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
*   **Agent Core:** Agent class definition, state management. **Refactored interaction logic to parse XML tool calls from LLM text responses, including handling markdown fences.** *(Completed)*
*   **Agent Manager:** Coordination logic for multiple agents, agent reloading/update on config change *(Simplified - Restart Required)*, **management of tool result history**. *(Completed)*
*   **Admin AI Agent (Foundation):** Configuration and basic interaction capability via tools *(Phase 9)*.
*   **Human UI (Foundation):** Two-page structure (Coms, Admin), basic authentication, backend log streaming/filtering UI *(Phase 10)*. **Basic page refresh button added.** *(Completed)*
*   **Configuration:** Loading settings from `config.yaml`/`.env`. **Backend API & UI for CRUD operations on `config.yaml` via `ConfigManager`. Requires restart.** *(Completed)* Safe config modification tool for Admin AI *(Phase 9)*.
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
*   **Dynamic Agent Reloading without Restart:** Deferred.
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
*   **Configuration:** YAML (`PyYAML`), `.env` files (`python-dotenv`). **Safe YAML read/write via `ConfigManager`.** *(Completed)*
*   **Data Handling:** Pydantic (via FastAPI)
*   **Authentication (Basic):** Likely FastAPI middleware/dependencies *(Phase 10)*.
*   **XML Parsing:** Standard library `re`, `html`.

## 4. Proposed Architecture Refinement

(Diagram reflects Agent responsibility for XML parsing, Manager handling history, and ConfigManager for R/W)

```mermaid
graph TD
    %% Changed to Top-Down for better layer visualization
    USER[👨‍💻 Human User]

    subgraph Frontend [Human UI Layer]
        direction LR
        UI_COMS["Coms Page <br>(Log Stream Filter - P10)<br>Adv I/O: P11+"]
        UI_ADMIN["Admin Page <br>(Config CRUD UI - P8 ✅)<br>Settings View<br>Auth UI: P10<br>Refresh Button ✅"] %% Updated
    end

    subgraph Backend
        FASTAPI["🚀 FastAPI Backend <br>+ Config CRUD API (P8 ✅)<br>+ Auth API (P10)<br>+ Log Endpoints (P10?)"]
        WS_MANAGER["🔌 WebSocket Manager <br>+ Log Categories (P10)"]
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>+ Reload Signal (Future)<br>+ Tool History Handling ✅<br>Controls All Agents"]
        CONFIG_MANAGER["📝 Config Manager <br>(Safe R/W config.yaml) P8 ✅"]

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
             TOOL_CONFIG["📝 ConfigTool (P9)<br>Uses Config Manager"]
             TOOL_FS["📄 FileSystem Tool ✅"]
             %% Other tools...
         end

         SANDBOXES["📁 Sandboxes ✅"]
    end

    subgraph External
        GOOGLE_API["☁️ Google AI APIs"]
        LLM_API_SVC["☁️ Ext. LLM APIs (OR, OpenAI)"]
        OLLAMA_SVC["⚙️ Local Ollama Service"]
        CONFIG_YAML["⚙️ config.yaml <br>(Read/Write via Config Manager) ✅"]
        DOT_ENV[".env File <br>(Secrets - Read Only) ✅"]
    end

    %% --- Connections ---
    USER -- Interacts via Browser --> Frontend;
    Frontend -- HTTP (API Calls, Auth) --> FASTAPI;
    Frontend -- WebSocket --> WS_MANAGER;

    FASTAPI -- Calls CRUD Ops --> CONFIG_MANAGER;
    FASTAPI -- Manages --> AGENT_MANAGER;
    WS_MANAGER -- Forwards Msgs / Sends Logs --> Frontend;
    WS_MANAGER -- Forwards User Msgs --> AGENT_MANAGER;

    AGENT_MANAGER -- Controls --> ADMIN_AI;
    AGENT_MANAGER -- Controls --> AGENT_INST_1;
    AGENT_MANAGER -- Controls --> AGENT_INST_N;
    AGENT_MANAGER -- Controls --> GEUI_AGENT;
    AGENT_MANAGER -- "Reads Initial Config Via Settings Module" --> CONFIG_YAML;
    AGENT_MANAGER -- "Reads Config/Secrets" --> DOT_ENV;
    AGENT_MANAGER -- Injects --> LLM_Providers;
    AGENT_MANAGER -- Routes Tool Calls --> TOOL_EXECUTOR;
    AGENT_MANAGER -- "Generates & Injects XML Prompts ✅" --> Agents;
    AGENT_MANAGER -- "Appends Tool Results to Agent History ✅" --> Agents;

    ADMIN_AI -- Uses --> PROVIDER_GOOGLE;
    ADMIN_AI -- "Streams Text" --> AGENT_MANAGER;
    ADMIN_AI -- "Parses Own XML" --> ADMIN_AI;
    ADMIN_AI -- "Yields Tool Request (ConfigTool)" --> AGENT_MANAGER;

    AGENT_INST_1 -- Uses --> LLM_Providers;
    AGENT_INST_1 -- "Streams Text ✅" --> AGENT_MANAGER;
    AGENT_INST_1 -- "Parses Own XML ✅" --> AGENT_INST_1;
    AGENT_INST_1 -- "Yields Tool Request (e.g., FileSystemTool)" --> AGENT_MANAGER;

    AGENT_INST_N -- Uses --> LLM_Providers;
    AGENT_INST_N -- "Streams Text ✅" --> AGENT_MANAGER;
    AGENT_INST_N -- "Parses Own XML ✅" --> AGENT_INST_N;
    AGENT_INST_N -- "Yields Tool Request" --> AGENT_MANAGER;


    TOOL_EXECUTOR -- Executes --> TOOL_CONFIG;
    TOOL_EXECUTOR -- Executes --> TOOL_FS;

    TOOL_CONFIG -- Uses --> CONFIG_MANAGER;
    CONFIG_MANAGER -- Reads/Writes --> CONFIG_YAML;

    PROVIDER_GOOGLE -- Interacts --> GOOGLE_API;
    PROVIDER_OR -- Interacts --> LLM_API_SVC;
    PROVIDER_OLLAMA -- Interacts --> OLLAMA_SVC;
    PROVIDER_OPENAI -- Interacts --> LLM_API_SVC;

```

## 5. Development Phases & Milestones

**Phase 1-7 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent, Config Loading, Sandboxing, Basic Tools, LLM Abstraction, UI Enhancements, Initial Testing, XML Tool Calling Refinement.

**Phase 8: Agent Configuration UI Management (Completed)**
*   **Goal:** Allow users to create, edit, and delete agent configurations via the web UI. Requires application restart after changes.
*   [X] **Analysis & Planning:** Reviewed project, defined Phase 8 scope.
*   [X] **Backend - Config Manager:** Implemented `src/config/config_manager.py` with `asyncio.Lock` and safe/atomic YAML read/write operations. Added synchronous getter for startup.
*   [X] **Backend - Settings:** Modified `src/config/settings.py` to use `ConfigManager.get_config_sync()` during initialization.
*   [X] **Backend - API:** Added Pydantic models and async CRUD endpoints (`POST`, `PUT`, `DELETE` for `/api/config/agents`) using `ConfigManager`. Fixed `await` issues.
*   [X] **Frontend - UI Elements (`index.html`, `style.css`):** Added Add/Edit/Delete/Refresh buttons and modal structure. Styled new elements.
*   [X] **Frontend - Logic (`app.js`):** Implemented modal display logic, form submission handlers for CRUD API calls, delete confirmation, config list refresh, and page reload via refresh button.
*   [X] **Fixes:** Corrected `ConfigManager` locking/saving logic, resolved `await` issues in API routes, fixed `NameError` related to `copy` import.
*   [X] **Tool Parsing Fix:** Significantly improved XML tool call parsing in `src/agents/core.py` to handle markdown fences and variations in LLM output, successfully enabling tool usage.
*   [X] **Testing:** Verified CRUD operations via UI, confirmed `config.yaml` updates, tested page refresh, confirmed tool execution.
*   [X] **Documentation:** Updated `README.md` and `FUNCTIONS_INDEX.md`.

**Phase 9: Admin AI Layer Foundation (Current / Next)**
*   **Goal:** Introduce the Admin AI agent and enable basic control via config modification using the XML tool format.
*   [ ] **Backend - Google Provider:**
    *   [ ] Add `google-generativeai` to `requirements.txt`.
    *   [ ] Implement `src/llm_providers/google_provider.py` inheriting `BaseLLMProvider`.
    *   [ ] Handle Google API key (`GOOGLE_API_KEY` in `.env`).
    *   [ ] Implement `stream_completion` (streaming text only, adhering to XML tool format).
*   [ ] **Backend - Config Tool:**
    *   [ ] Implement `src/tools/config_tool.py` inheriting `BaseTool`. Define actions and parameters.
    *   [ ] Use the async methods of the `ConfigManager` singleton for safe read/write operations.
    *   [ ] Provide clear success/error messages (mentioning restart required).
*   [ ] **Backend - Agent Manager:** Ensure `ToolExecutor` registers `ConfigTool`. Ensure manager correctly handles the flow after `ConfigTool` execution (currently, no dynamic reload, user needs to restart).
*   [ ] **Configuration:** Update `.env.example` with `GOOGLE_API_KEY`. Update `config.yaml` to define the Admin AI agent (google provider), and update its system prompt to use the *XML format* for the `ConfigTool`.
    *   *Model Note:* Confirm official model name for Google AI Studio / Gemini API.
*   [ ] **Testing:** Manually send commands to Admin AI via UI (e.g., "AdminAI, use ConfigTool in XML format to add agent X..."). Verify changes and test restart/refresh workflow.

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
