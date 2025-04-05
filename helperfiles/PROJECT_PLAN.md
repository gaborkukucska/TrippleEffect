<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 1.9 (Reflecting XML Tool Call Implementation)
**Date:** 2025-04-04 (Note: Should be updated with actual dev dates)

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement an **Admin AI layer** capable of monitoring and controlling framework components (initially via config modification). *(Planned)*
*   Implement a **Human User Interface** with distinct views for communication/monitoring ("Coms") and administration ("Admin"). *(Planned)*
*   Enable real-time communication between the backend and frontend using WebSockets, including categorized logs.
*   Support multiple, configurable LLM agents (including the Admin AI) capable of collaborating on tasks.
*   **Implement XML-based tool calling mechanism (Cline-style) for enhanced compatibility, especially with providers like OpenRouter.**
*   Allow agents to utilize tools within sandboxed environments, including specialized tools for the Admin AI.
*   Ensure the framework is reasonably lightweight and performant for potential use on platforms like Termux.
*   Integrate with various LLM API providers (OpenRouter, Ollama, OpenAI, **Google** - *planned*).
*   Structure the project logically for maintainability and extensibility.
*   Adhere to the specified development principles.
*   *(Future Goals)* Explore advanced UI concepts (GeUI), multi-modal inputs (camera, mic), voice control, and dynamic agent management.

## 2. Scope

**In Scope (Phases up to ~10):**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling.
*   **Agent Core:** Agent class definition, state management. **Refactored interaction logic to parse XML tool calls from LLM text responses.**
*   **Agent Manager:** Coordination logic for multiple agents, agent reloading/update on config change *(Phase 8/9)*.
*   **Admin AI Agent (Foundation):** Configuration and basic interaction capability via tools *(Phase 9)*.
*   **Human UI (Foundation):** Two-page structure (Coms, Admin), basic authentication, backend log streaming/filtering UI *(Phase 10)*.
*   **Configuration:** Loading settings from `config.yaml`/`.env`. Backend API & UI for CRUD operations on `config.yaml` *(Phase 8)*. Safe config modification tool for Admin AI *(Phase 9)*.
*   **WebSocket Communication:** Real-time streaming of agent outputs/status, plus categorized backend logs *(Phase 10)*.
*   **Basic Sandboxing:** Agent file operation directories.
*   **Tooling:** `BaseTool`, `ToolExecutor`, `FileSystemTool`, `ConfigTool` *(Phase 9)*.
*   **LLM Integration:** Support for OpenRouter, Ollama, OpenAI, **Google** *(Phase 9)* via provider abstraction. **API calls modified to remove native `tools`/`tool_choice` parameters; system prompts updated to instruct XML format.**
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
*   **Native API Tool Calling:** Using provider-specific `tools`/`tool_choice` parameters (standardizing on XML-in-text).

## 3. Technology Stack

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library, `aiohttp`, **`google-generativeai`** *(Phase 9)*.
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (`PyYAML`), `.env` files (`python-dotenv`).
*   **Data Handling:** Pydantic (via FastAPI)
*   **Authentication (Basic):** Likely FastAPI middleware/dependencies *(Phase 10)*.
*   **XML Parsing:** Standard library `xml.etree.ElementTree` or similar *(New for Phase 7)*.

## 4. Proposed Architecture Refinement

(Diagram updated slightly to reflect XML parsing responsibility)

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
            AGENT_INST_1["🤖 Worker Agent 1 <br>+ XML Tool Parsing"] %% <-- Highlight change
            AGENT_INST_N["🤖 Worker Agent N <br>+ XML Tool Parsing"] %% <-- Highlight change
            GEUI_AGENT["🤖 GeUI Agent(s) (P11+)"]
        end

        subgraph LLM_Providers ["☁️ LLM Providers <br>(src/llm_providers/)"]
             PROVIDER_GOOGLE["🔌 Google Provider (P9)"]
             PROVIDER_OR["🔌 OpenRouter Provider"]
             PROVIDER_OLLAMA["🔌 Ollama Provider"]
             PROVIDER_OPENAI["🔌 OpenAI Provider"]
             %% Providers now mainly stream text, don't handle tool_calls internally
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
    FASTAPI -- Triggers --> CONFIG_WRITER;
        %% Via API calls handled by ConfigTool
    FASTAPI -- Notifies --> AGENT_MANAGER;
        %% On config change
    WS_MANAGER -- Forwards Msgs / Sends Logs --> Frontend;
    WS_MANAGER -- Forwards User Msgs --> AGENT_MANAGER;

    AGENT_MANAGER -- Controls --> ADMIN_AI;
    AGENT_MANAGER -- Controls --> AGENT_INST_1;
    AGENT_MANAGER -- Controls --> AGENT_INST_N;
    AGENT_MANAGER -- Controls --> GEUI_AGENT;

    AGENT_MANAGER -- "Reads Config/Secrets" --> CONFIG_YAML;
    AGENT_MANAGER -- "Reads Config/Secrets" --> DOT_ENV;
    AGENT_MANAGER -- Injects --> LLM_Providers;
    AGENT_MANAGER -- Routes Tool Calls --> TOOL_EXECUTOR; %% <--- Agent Manager now receives parsed requests

    ADMIN_AI -- Uses --> PROVIDER_GOOGLE;
    ADMIN_AI -- Requests Tool (via XML) --> AGENT_MANAGER;
    AGENT_INST_1 -- Uses --> LLM_Providers; %% <-- Simplified interaction
    AGENT_INST_N -- Uses --> LLM_Providers; %% <-- Simplified interaction

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

**Phase 1-6 (Completed)**
*   [X] Core Backend, Agent Core, Multi-Agent, Config Loading, Sandboxing, Basic Tools, LLM Abstraction, UI Enhancements, Initial Testing.

**Phase 7: Refinement, XML Tool Calling & Optimization (Current)**
*   [ ] **Implement XML Tool Calling:**
    *   [ ] **System Prompts:** Update default system prompt and agent-specific prompts in `config.yaml` to instruct LLMs on the required XML format for tool calls (similar to Cline's). Define the exact XML structure clearly.
    *   [ ] **LLM Providers:** Modify `stream_completion` methods in all providers (`openai`, `ollama`, `openrouter`) to remove the `tools` and `tool_choice` parameters from the API calls. They should primarily focus on streaming text content.
    *   [ ] **Agent Core (`src/agents/core.py`):**
        *   [ ] Refactor `process_message` to buffer the incoming text stream (`response_chunk`).
        *   [ ] Implement logic (`_parse_xml_tool_calls` helper?) to detect and parse the specific XML tool call structure within the buffered text.
        *   [ ] When a complete XML tool call is detected, yield the `{'type': 'tool_requests', 'calls': [...]}` event (similar to how providers did before, but now triggered by XML parsing).
        *   [ ] Ensure partial XML tags at the end of a chunk are handled correctly (buffered until complete).
        *   [ ] Clear the buffer corresponding to the parsed XML tool call.
        *   [ ] Continue yielding any remaining text before the tool call as `response_chunk`.
    *   [ ] **Agent Manager (`src/agents/manager.py`):** Modify `_handle_agent_generator` to correctly receive the `tool_requests` yielded by the *agent* (not the provider) and proceed with tool execution.
    *   [ ] **Tool Executor:** No major changes expected, as it receives parsed tool name and args.
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
*   [ ] **Backend Config Handling:** Safe read/modify/write functions for `config.yaml`. Error handling. **Strictly no secrets (API keys) managed here.**
*   [ ] **Agent Reloading:** Decide on strategy (manual restart vs. dynamic reload). Implement chosen strategy in `AgentManager`.
*   [ ] **Frontend UI:** Basic forms/modals for Add/Edit/Delete operations. API integration. Dynamic UI updates. Error display. (To be integrated into Admin Page in Phase 10).
*   [ ] **Security:** Backend input validation. Confirm file write restrictions.
*   [ ] **Documentation:** Update `README.md` for basic config UI usage.

**Phase 9: Admin AI Layer Foundation (Planned)**
*   **Goal:** Introduce the Admin AI agent and enable basic control via config modification using the XML tool format.
*   [ ] **Backend - Google Provider:**
    *   [ ] Add `google-generativeai` to `requirements.txt`.
    *   [ ] Implement `src/llm_providers/google_provider.py` inheriting `BaseLLMProvider`.
    *   [ ] Handle Google API key (`GOOGLE_API_KEY` in `.env`).
    *   [ ] Implement `stream_completion` (streaming text only, no native tool parameters).
*   [ ] **Backend - Config Tool:**
    *   [ ] Implement `src/tools/config_tool.py` inheriting `BaseTool`. Define actions and parameters.
    *   [ ] Use the safe config read/write functions from Phase 8.
    *   [ ] Provide clear success/error messages.
*   [ ] **Backend - Agent Manager:** Ensure `ToolExecutor` registers `ConfigTool`. Ensure manager can reload config/agents upon successful `ConfigTool` execution.
*   [ ] **Configuration:** Update `.env.example` with `GOOGLE_API_KEY`. Update `config.yaml` to define the Admin AI agent (google provider), and update its system prompt to use the *XML format* for the `ConfigTool`.
    *   *Model Note:* Confirm model name for "Gemini 2.5 Pro Experimental".
*   [ ] **Testing:** Manually send commands to Admin AI via UI (e.g., "AdminAI, use ConfigTool in XML format to add agent X..."). Verify changes and reloading.

**Phase 10: Human UI Foundation (Planned)**
*   **Goal:** Restructure UI into Coms/Admin pages and implement log streaming & basic auth.
*   [ ] **Frontend - Structure:** Refactor `index.html`/`app.js` for Coms/Admin views. Design layouts.
*   [ ] **Backend - Logging:** Introduce log categories. Modify logging setup/middleware. Update `WebSocketManager` for streaming categorized logs.
*   [ ] **Frontend - Coms Page:** Implement collapsible log stream display. Add log filters. Update WebSocket handling.
*   [ ] **Frontend - Admin Page:** Integrate Agent Config CRUD (Phase 8). Add read-only settings display (new API endpoint needed).
*   [ ] **Backend & Frontend - Authentication:** Implement basic password auth. Protect Admin APIs. Add login UI.

**Future Phases (11+) (High-Level)**
*   **Phase 11: GeUI Implementation:** Design and implement the Generative UI concept.
*   **Phase 12: Advanced I/O & Voice Control:** Integrate camera, microphone (STT), speaker (TTS).
*   **Phase 13: Advanced Admin AI Control:** Enhance Admin AI tools for dynamic control of running agents.
*   **Phase 14: Advanced Authentication & Multi-User:** Implement more robust authentication.
*   **Phase 15: Create Project Plan for Next Iteration:** Re-evaluate and plan.
