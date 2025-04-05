<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 1.9 (Reflecting XML Tool Call Implementation)
**Date:** 2025-04-05 <!-- Updated Date -->

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
*   **Agent Manager:** Coordination logic for multiple agents, agent reloading/update on config change *(Phase 8/9)*, **management of tool result history**.
*   **Admin AI Agent (Foundation):** Configuration and basic interaction capability via tools *(Phase 9)*.
*   **Human UI (Foundation):** Two-page structure (Coms, Admin), basic authentication, backend log streaming/filtering UI *(Phase 10)*.
*   **Configuration:** Loading settings from `config.yaml`/`.env`. Backend API & UI for CRUD operations on `config.yaml` *(Phase 8)*. Safe config modification tool for Admin AI *(Phase 9)*.
*   **WebSocket Communication:** Real-time streaming of agent outputs/status, plus categorized backend logs *(Phase 10)*.
*   **Basic Sandboxing:** Agent file operation directories.
*   **Tooling:** `BaseTool`, `ToolExecutor` (**with XML description generation**), `FileSystemTool`, `ConfigTool` *(Phase 9)*.
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
*   **XML Parsing:** Standard library `re` (for stream parsing).

## 4. Proposed Architecture Refinement

(Diagram updated slightly to reflect XML parsing responsibility and history management)

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
        AGENT_MANAGER["🧑‍💼 Agent Manager <br>+ Reload Logic (P8/9)<br>+ Tool History Handling<br>Controls All Agents"] %% <-- Added History Handling

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
             TOOL_EXECUTOR["🛠️ Tool Executor<br>+ XML Desc Gen"] %% <-- Highlight change
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
    AGENT_MANAGER -- Routes Tool Calls --> TOOL_EXECUTOR;
    AGENT_MANAGER -- "Generates & Injects XML Prompts" --> Agents; %% <-- Clarified prompt injection
    AGENT_MANAGER -- "Appends Tool Results to Agent History" --> Agents; %% <-- Added history update path


    ADMIN_AI -- Uses --> PROVIDER_GOOGLE;
    ADMIN_AI -- "Streams Text" --> AGENT_MANAGER; %% <-- Changed interaction
    ADMIN_AI -- "Parses Own XML" --> ADMIN_AI; %% <-- Agent parses
    ADMIN_AI -- "Yields Tool Request" --> AGENT_MANAGER; %% <-- Agent yields request

    AGENT_INST_1 -- Uses --> LLM_Providers;
    AGENT_INST_1 -- "Streams Text" --> AGENT_MANAGER; %% <-- Changed interaction
    AGENT_INST_1 -- "Parses Own XML" --> AGENT_INST_1; %% <-- Agent parses
    AGENT_INST_1 -- "Yields Tool Request" --> AGENT_MANAGER; %% <-- Agent yields request

    AGENT_INST_N -- Uses --> LLM_Providers;
    AGENT_INST_N -- "Streams Text" --> AGENT_MANAGER; %% <-- Changed interaction
    AGENT_INST_N -- "Parses Own XML" --> AGENT_INST_N; %% <-- Agent parses
    AGENT_INST_N -- "Yields Tool Request" --> AGENT_MANAGER; %% <-- Agent yields request


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

**Phase 7: Refinement, XML Tool Calling & Optimization (Current - Implementing XML Tool Calling)**
*   [X] **Analysis:** Reviewed project and Cline approach. Confirmed plan.
*   [ ] **Implement XML Tool Calling:**
    *   [ ] **Tool Executor (`src/tools/executor.py`):**
        *   [ ] Add `get_formatted_tool_descriptions_xml()` method to generate tool descriptions in the required XML format for prompts.
    *   [ ] **Agent Manager (`src/agents/manager.py`):**
        *   [ ] Modify `_initialize_agents`: Call `get_formatted_tool_descriptions_xml()` and pass the result to the `Agent` constructor.
        *   [ ] Modify `_handle_agent_generator`: After receiving tool results from `_execute_single_tool`, append the appropriate `{"role": "tool", "tool_call_id": ..., "content": ...}` message(s) to the `agent.message_history`. **Crucial for context.**
    *   [ ] **Agent Core (`src/agents/core.py`):**
        *   [ ] Modify `__init__`: Accept `tool_descriptions_xml: str` parameter and prepend/append it to the `original_system_prompt` to form the final system prompt used in `message_history`.
        *   [ ] Modify `process_message`:
            *   Remove `tools` and `tool_choice` arguments from the call to `self.llm_provider.stream_completion`.
            *   Implement text buffering for `response_chunk` content.
            *   Implement XML parsing logic (e.g., `_parse_xml_tool_call` helper using `re`) within the loop processing chunks. Use `self.tool_executor.tools.keys()` for validation.
            *   Yield `response_chunk` events for text segments *before* any detected XML tool call.
            *   When a complete, valid XML tool call is parsed, yield a `{'type': 'tool_requests', 'calls': [{'id': 'xml_...', 'name': ..., 'arguments': ...}]}` event. Generate a simple unique ID (e.g., `f"xml_call_{self.agent_id}_{int(time.time())}"`).
            *   Manage the buffer to remove the parsed XML text.
            *   Keep the `asend()` structure to receive results (or a signal to continue/finish) after yielding `tool_requests`. Append the assistant's response *text* (including the raw XML call) and the *tool result messages* (added by the manager) to the `self.message_history`. *Correction*: Manager appends tool result to history. Agent only needs to append its own textual response including the raw XML.
    *   [ ] **LLM Providers (`src/llm_providers/*.py`):**
        *   [ ] Modify `stream_completion` methods in `openai_provider.py`, `ollama_provider.py`, `openrouter_provider.py` to remove the `tools` and `tool_choice` parameters from the API calls.
        *   [ ] Remove logic related to parsing native `tool_calls` from the API responses (e.g., `delta.tool_calls`, `message_chunk["tool_calls"]`).
        *   [ ] Ensure providers primarily yield `response_chunk` (text), `status`, and `error`. Do **not** yield `tool_requests`.
    *   [ ] **Configuration (`config.yaml`):** Review agent system prompts. Ensure they don't rely on native tool calling and clearly instruct the use of the XML format. (The specific tool definitions will be added dynamically).
*   [ ] **Testing:** Thoroughly test tool calling with OpenRouter/Gemini via XML. Test with other providers (Ollama, OpenAI) to ensure they still function correctly by generating the XML in their text response.
*   [ ] *(Deferred from Phase 7)* Improve general error handling and reporting.
*   [ ] *(Deferred from Phase 7)* Optimize performance.
*   [ ] *(Deferred from Phase 7)* Refine sandboxing security.
*   [ ] *(Deferred from Phase 7)* Add more tools (e.g., web search).
*   [ ] Update `README.md` and `FUNCTIONS_INDEX.md` after XML implementation is complete.
*   [ ] Code cleanup and review after XML implementation.

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
