<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 1.1 (Updated for Phase 5.5)
**Date:** 2025-04-04 (Note: Should be updated with actual dev dates)

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement a browser-based UI for user interaction, agent configuration, and monitoring.
*   Enable real-time communication between the backend and frontend using WebSockets.
*   Support multiple, configurable LLM agents capable of collaborating on tasks.
*   Allow agents to utilize tools within sandboxed environments.
*   Ensure the framework is reasonably lightweight and performant for potential use on platforms like Termux.
*   Integrate with various LLM API providers (OpenAI initially, extensible to others like Ollama, LiteLLM, OpenRouter, Google, Anthropic, DeepSeek, etc.).
*   Structure the project logically for maintainability and extensibility.
*   Adhere to the specified development principles (full file analysis, incremental updates with confirmation, consistency, helper file maintenance).

## 2. Scope

**In Scope:**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling.
*   **Agent Core:** Agent class definition, state management, interaction logic with LLM APIs (including tool use).
*   **Agent Manager:** Coordination logic for multiple agents (task assignment, message routing, tool execution orchestration).
*   **Basic UI:** HTML/CSS/Vanilla JS frontend for submitting tasks, displaying agent outputs, basic configuration, basic tool usage indication.
*   **Configuration:** Loading agent settings (LLM provider, model, system prompt, temperature, etc.) from a configuration source (`config.yaml`).
*   **WebSocket Communication:** Real-time streaming of agent thoughts/responses/status to the UI.
*   **Basic Sandboxing:** Creation of dedicated directories for agent file operations (`sandboxes/agent_<id>/`).
*   **Initial Tooling:**
    *   Framework for defining and registering tools (`BaseTool`).
    *   Tool execution logic (`ToolExecutor`).
    *   Simple file system tool (`FileSystemTool`) operating within the sandbox.
    *   Mechanism for agents (via LLM function/tool calling) to request tool use and receive results.
*   **LLM Integration:** Initial support for OpenAI API (tool calling). **Refactoring to support multiple providers (Ollama, OpenRouter).**
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`.

**Out of Scope (for initial versions, can be added later):**

*   Advanced UI frameworks (React, Vue, etc.) - starting with Vanilla JS for lightness.
*   Complex user authentication/multi-user support.
*   Advanced sandboxing with containerization (e.g., Docker) or robust conda environment management per agent (start with directory isolation).
*   Full Model Context Protocol (MCP) server implementation (current internal implementation is inspired by it but not a full server).
*   Sophisticated automated testing suite (focus on manual testing initially).
*   Complex agent-to-agent delegation protocols (start with coordinator-based interaction).
*   Voice/Camera input processing (focus on text/file input first).
*   Support for *all* possible LLM providers (focus on OpenAI, Ollama, OpenRouter initially).

## 3. Technology Stack (Confirming from README)

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library initially, `aiohttp`, potentially `litellm` or provider-specific libraries.
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (using `PyYAML`), `.env` files.
*   **Data Handling:** Pydantic (via FastAPI)

## 4. Proposed Architecture Refinement

(Remains largely the same, but LLM Interaction becomes abstracted)

+---------------------+ +----------------------------+ +-----------------------+
| Browser UI |<---->| FastAPI Backend (main.py) |<---->| LLM Providers |
| (static/, templates/)| | - HTTP Routes (api/http) | | (src/llm_providers/) |
| (HTML/CSS/JS) | | - WS Manager (api/ws) | +----------+------------+
+---------------------+ | - AgentManager Instance    |           ▲
▲ | +----------------------------+           | (API Calls)
| (WebSocket /ws) | ▲ | ▲ | WebSocket Msgs ▼
▼ ▼ | | | Agent Actions/Updates +-----------------------+
+---------------------+ +----------------------------+ ▼ +-----------------------+
| WebSocket Manager |<----->| Agent Manager (agents/mgr) |<---->| Agent Instances |
| (api/websocket_mgr.py)| | - Task Handling | | (agents/core.py) |
| - Forwards msgs to Mgr| | - Agent Lifecycle | | - *LLM Provider Usage*|
| - Receives msgs fm Mgr| | - Tool Orchestration | | - State / Sandbox |
+---------------------+ +-------------+--------------+ | - Tool Requesting |
| | +----------+----------+
| | ▼ (Tool Execution) ▼ (File I/O)
| +-----------------+ +----------------------+
| | Tool Executor | | Sandboxes (sandboxes/)|
+------------------------->| (tools/executor)| +----------------------+
| - Tool Registry |
| - Tool Runner |
+--------+--------+
|
▼ (Tool Definitions)
+-----------------+
| Tools (tools/) |
| - file_system.py|
| - web_search.py |
| - ... |
+-----------------+

*   **`src/llm_providers/` (New):** Contains the base interface and specific provider implementations (`openai_provider.py`, `ollama_provider.py`, `openrouter_provider.py`).
*   **`src/agents/core.py`**: Now interacts with an injected `BaseLLMProvider` instance instead of directly with `openai`. Handles potential differences in tool call formats.
*   **`src/agents/manager.py`**: Instantiates the appropriate `LLMProvider` based on agent config and injects it into the `Agent`.

## 5. Development Phases & Milestones

**Phase 1: Core Backend Setup & Basic UI (Completed)**

*   [X] Update `requirements.txt`.
*   [X] Set up project structure with refined directories.
*   [X] Implement basic FastAPI app (`main.py`).
*   [X] Create `templates/index.html` served by an HTTP route (`api/http_routes.py`).
*   [X] Create basic `static/css/style.css`.
*   [X] Implement basic WebSocket manager (`api/websocket_manager.py`).
*   [X] Implement basic bidirectional communication.
*   [X] Initialize `helperfiles/FUNCTIONS_INDEX.md`.
*   [X] Update `src/main.py` to include the HTTP router & WebSocket router.

**Phase 2: Agent Core & Single Agent Interaction (Completed)**

*   [X] Define `Agent` class (`agents/core.py`) with basic state.
*   [X] Implement basic LLM interaction within `Agent` (using `openai` library, streaming).
*   [X] Create `AgentManager` (`agents/manager.py`) for a single `Agent`.
*   [X] Connect WebSocket Manager to `AgentManager`.
*   [X] Stream LLM response back to UI.
*   [X] Display streamed LLM response in the UI (`app.js`).
*   [X] Implement basic configuration loading (`src/config/settings.py`) for API keys/defaults.
*   [X] Update `FUNCTIONS_INDEX.md`.

**Phase 3: Multi-Agent Setup & Basic Coordination (Completed)**

*   [X] Enhance `AgentManager` for multiple `Agent` instances (initially hardcoded).
*   [X] Refine WebSocket communication for structured messages.
*   [X] Update UI (`app.js`) for multiple agents.
*   [X] Implement concurrent task distribution in `AgentManager`.
*   [ ] Define basic inter-agent communication placeholder logic *(Deferred)*.
*   [X] Update `FUNCTIONS_INDEX.md`.

**Phase 4: Configuration & Sandboxing (Completed)**

*   [X] Implement loading agent configurations from `config.yaml`.
*   [X] Update `Agent` and `AgentManager` to use loaded configurations.
*   [ ] Implement UI elements to *view* current configurations *(Deferred)*.
*   [X] Implement dynamic creation of sandbox directories.
*   [X] Ensure agents know their sandbox path.
*   [X] Update `FUNCTIONS_INDEX.md`.

**Phase 5: Basic Tool Implementation (Internal MCP-Inspired) (Completed)**

*   [X] Define `BaseTool` structure (`tools/base.py`).
*   [X] Implement `ToolExecutor` (`tools/executor.py`).
*   [X] Implement `FileSystemTool` (`tools/file_system.py`).
*   [X] Modify `Agent` (`agents/core.py`) to use OpenAI tool calling, adapt `process_message` generator, handle tool results.
*   [X] Modify `AgentManager` (`agents/manager.py`) to instantiate `ToolExecutor`, inject it, handle the agent generator loop (`_handle_agent_generator`), route tool requests (`_execute_single_tool`), and send results back.
*   [X] Update UI (`static/js/app.js`) for basic tool status.
*   [X] Update `FUNCTIONS_INDEX.md`.

**Phase 5.5: LLM Provider Abstraction (Current Phase)**

*   [ ] **Goal:** Refactor LLM interaction to support multiple providers (OpenAI, Ollama, OpenRouter initially).
*   [ ] Create `src/llm_providers/` directory.
*   [ ] Define `BaseLLMProvider` interface (`src/llm_providers/base.py`).
*   [ ] Implement `OpenAIProvider` (`src/llm_providers/openai_provider.py`).
*   [ ] Implement `OllamaProvider` (`src/llm_providers/ollama_provider.py`) using `aiohttp`.
*   [ ] Implement `OpenRouterProvider` (`src/llm_providers/openrouter_provider.py`) potentially using `aiohttp` or adapting `openai` library if API is compatible.
*   [ ] Update `config.yaml` structure to include `provider` type and necessary details (API key, base URL). Update `.env.example`.
*   [ ] Update `src/config/settings.py` to load provider configuration. Add necessary environment variables (e.g., `OLLAMA_BASE_URL`, `OPENROUTER_API_KEY`).
*   [ ] Refactor `src/agents/core.py` (`Agent` class):
    *   Remove direct `openai` client usage.
    *   Accept an injected `BaseLLMProvider` instance.
    *   Adapt `process_message` generator to call the provider's method.
    *   Handle potential variations in tool call response formats from different providers, aiming to standardize the yielded `tool_requests` event.
*   [ ] Refactor `src/agents/manager.py` (`AgentManager`):
    *   In `_initialize_agents`, instantiate the correct `LLMProvider` based on agent config.
    *   Inject the provider instance into the `Agent`.
*   [ ] Update `requirements.txt` if new libraries are needed (e.g., `litellm` if chosen, though `aiohttp` might suffice initially).
*   [ ] Update `FUNCTIONS_INDEX.md` with new provider functions/methods.
*   [ ] Update `README.md` to reflect multi-provider support and new configuration options.

**Phase 6: UI Enhancements & Advanced Features (Next)**

*   [ ] Improve UI layout for clarity.
*   [ ] Display detailed agent status indicators.
*   [ ] Implement message history display.
*   [ ] Add basic file upload capability.
*   [ ] Implement UI for *viewing* agent configurations.
*   [ ] Test and refine multi-provider support (Ollama, OpenRouter).

**Phase 7: Refinement, Optimization & Documentation**

*   [ ] Improve error handling and reporting.
*   [ ] Optimize performance.
*   [ ] Refine sandboxing security.
*   [ ] Add more tools (e.g., simple web search).
*   [ ] Write comprehensive usage instructions.
*   [ ] Add more details to `FUNCTIONS_INDEX.md`.
*   [ ] Code cleanup and final review.
*   [ ] Create a Project Plan for the new run with upgrade phases.

## 6. MCP Integration Note

While a full MCP server is out of scope initially, the internal Tool Executor design (Phase 5) uses OpenAI's standard tool-calling mechanism, which is a common pattern. The internal execution logic is inspired by MCP's structured approach but remains internal. This provides a solid foundation for agent tool use. **Phase 5.5 aims to adapt this for other providers.**
