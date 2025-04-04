<!-- # START OF FILE helperfiles/PROJECT_PLAN.md -->
# Project Plan: TrippleEffect

**Version:** 1.0
**Date:** 2025-04-04 (Note: Should be updated with actual dev dates)

## 1. Project Goals

*   Develop an asynchronous, collaborative multi-agent framework (`TrippleEffect`).
*   Implement a browser-based UI for user interaction, agent configuration, and monitoring.
*   Enable real-time communication between the backend and frontend using WebSockets.
*   Support multiple, configurable LLM agents capable of collaborating on tasks.
*   Allow agents to utilize tools within sandboxed environments.
*   Ensure the framework is reasonably lightweight and performant for potential use on platforms like Termux.
*   Integrate with various LLM API providers (OpenAI initially, extensible to others like Ollama, LiteLLM, etc.).
*   Structure the project logically for maintainability and extensibility.
*   Adhere to the specified development principles (full file analysis, incremental updates with confirmation, consistency, helper file maintenance).

## 2. Scope

**In Scope:**

*   **Core Backend:** FastAPI application, WebSocket management, asynchronous task handling.
*   **Agent Core:** Agent class definition, state management, interaction logic with LLM APIs.
*   **Agent Manager:** Coordination logic for multiple agents (task assignment, message routing).
*   **Basic UI:** HTML/CSS/Vanilla JS frontend for submitting tasks, displaying agent outputs, basic configuration.
*   **Configuration:** Loading agent settings (LLM provider, model, system prompt, temperature, etc.) from a configuration source (`config.yaml`).
*   **WebSocket Communication:** Real-time streaming of agent thoughts/responses to the UI.
*   **Basic Sandboxing:** Creation of dedicated directories for agent file operations (`sandboxes/agent_<id>/`).
*   **Initial Tooling:**
    *   Framework for defining and registering tools.
    *   Simple file system tool (read/write within sandbox).
    *   Mechanism for agents to request tool use and receive results, potentially inspired by MCP's structured approach but implemented internally first.
*   **LLM Integration:** Initial support for OpenAI API, structure for adding other providers.
*   **Helper Files:** Maintenance of `PROJECT_PLAN.md` and `FUNCTIONS_INDEX.md`.

**Out of Scope (for initial versions, can be added later):**

*   Advanced UI frameworks (React, Vue, etc.) - starting with Vanilla JS for lightness.
*   Complex user authentication/multi-user support.
*   Advanced sandboxing with containerization (e.g., Docker) or robust conda environment management per agent (start with directory isolation).
*   Full Model Context Protocol (MCP) server implementation (will aim for an MCP-inspired *internal* structure first due to complexity).
*   Sophisticated automated testing suite (focus on manual testing initially).
*   Complex agent-to-agent delegation protocols (start with coordinator-based interaction).
*   Voice/Camera input processing (focus on text/file input first).

## 3. Technology Stack (Confirming from README)

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **WebSockets:** `websockets` library integrated with FastAPI
*   **LLM Interaction:** `openai` library initially, `aiohttp` for other potential HTTP-based APIs.
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript
*   **Asynchronous Operations:** `asyncio`
*   **Configuration:** YAML (using `PyYAML`), `.env` files.
*   **Data Handling:** Pydantic (via FastAPI)

## 4. Proposed Architecture Refinement

(Based on README, aiming for clarity and modularity)


+---------------------+ +----------------------------+ +-----------------------+
| Browser UI |<---->| FastAPI Backend (main.py) |<---->| LLM APIs (OpenAI, etc)|
| (static/, templates/)| | - HTTP Routes (api/http) | +-----------------------+
| (HTML/CSS/JS) | | - WS Manager (api/ws) |
+---------------------+ | - AgentManager Instance    |
▲ | +----------------------------+
| (WebSocket /ws) | ▲ | ▲ | WebSocket Msgs
▼ ▼ | | | Agent Actions/Updates
+---------------------+ +----------------------------+ ▼ +-----------------------+
| WebSocket Manager |<----->| Agent Manager (agents/mgr) |<---->| Agent Instances |
| (api/websocket_mgr.py)| | - Task Handling | | (agents/core.py) |
| - Forwards msgs to Mgr| | - Agent Lifecycle | | - LLM Interaction |
| - Receives msgs fm Mgr| | - Inter-Agent Comms | | - State / Sandbox |
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

*   **`main.py`**: Entry point, FastAPI app setup, **instantiates AgentManager**, mounts routers, **injects AgentManager into WebSocketManager**.
*   **`src/api/`**: Contains API logic.
    *   `http_routes.py`: Handles standard HTTP requests (e.g., serving UI, config endpoints).
    *   `websocket_manager.py`: Handles WebSocket connections, **receives AgentManager instance**, routes incoming messages to AgentManager using `asyncio.create_task`, provides `broadcast` function for AgentManager to send messages to UI.
*   **`src/agents/`**: Agent-related logic.
    *   `manager.py`: The central coordinator (`AgentManager` class). Manages agent lifecycles, receives tasks via `handle_user_message`, orchestrates agent processing, **uses injected broadcast function** to send results/status to UI. **Initializes agents from `config.yaml` via `settings` and ensures sandboxes exist.**
    *   `core.py`: Defines the `Agent` class, responsible for interacting with LLMs (async streaming via `process_message`), managing its own state, memory (simple), **initializes OpenAI client using settings**, **manages its sandbox path**, **loads config from manager**.
    *   `prompts.py` (New - *Not Implemented Yet*): Store default system prompts, persona templates.
*   **`src/tools/`**: Tool implementations and management.
    *   `executor.py`: Handles parsing agent tool requests, finding the correct tool, executing it securely (within sandbox context), and returning results.
    *   `base.py`: Base class or definition for tools.
    *   `file_system.py`, `web_search.py`, etc.: Individual tool implementations.
*   **`src/config/`**: Configuration loading and validation.
    *   `settings.py`: Loads config (e.g., API keys, defaults) from `.env` / environment variables **and agent configurations from `config.yaml`**.
*   **`src/utils/`**: Common utility functions.
*   **`sandboxes/`**: Dynamically created agent working directories.
*   **`static/` & `templates/`**: Frontend files.
    *   `app.js`: Handles WebSocket connection, sending messages, **receiving structured messages (status, error, agent_response), groups streamed agent responses**.
*   **`helperfiles/`**: Project plan, function index.
*   **`config.yaml`**: New file defining agent configurations.

## 5. Development Phases & Milestones

**Phase 1: Core Backend Setup & Basic UI (Completed)**

*   [X] Update `requirements.txt`.
*   [X] Set up project structure with refined directories. *(Structure already exists)*
*   [X] Implement basic FastAPI app (`main.py`).
*   [X] Create `templates/index.html` served by an HTTP route (`api/http_routes.py`).
*   [X] Create basic `static/css/style.css`.
*   [X] Implement basic WebSocket manager (`api/websocket_manager.py`) capable of connecting/disconnecting clients.
*   [X] Implement basic bidirectional communication (UI sends message, backend echoes back via WebSocket).
*   [X] Initialize `helperfiles/FUNCTIONS_INDEX.md`.
*   [X] Update `src/main.py` to include the HTTP router.
*   [X] Update `src/main.py` to include the WebSocket router.

**Phase 2: Agent Core & Single Agent Interaction (Completed)**

*   [X] Define `Agent` class (`agents/core.py`) with basic state (ID, config placeholder).
*   [X] Implement basic LLM interaction within `Agent` (using OpenAI library and `asyncio`, streaming).
*   [X] Create `AgentManager` (`agents/manager.py`) capable of creating a single `Agent` instance.
*   [X] Connect WebSocket Manager to `AgentManager`: UI message -> WS Manager -> (asyncio task) Agent Manager -> Agent -> LLM.
*   [X] Stream LLM response back: LLM -> Agent -> Agent Manager -> WS Manager (via broadcast) -> UI.
*   [X] Display streamed LLM response in the UI (`app.js`, handling structured messages and grouping chunks).
*   [X] Implement basic configuration loading (`src/config/settings.py`) for API keys and default agent settings (from `.env`).
*   [X] Update `FUNCTIONS_INDEX.md` with new functions.

**Phase 3: Multi-Agent Setup & Basic Coordination (Completed)**

*   [X] Enhance `AgentManager` to create and manage multiple `Agent` instances (e.g., based on config file - Phase 4 dependency). Started by hardcoding 3 agents.
*   [X] Refine WebSocket communication to handle messages from/to specific agents or the manager (UI needs updating). *(Backend sends structured JSON including `agent_id`, `type`)*
*   [X] Update UI (`app.js`) to show outputs from multiple agents, handling structured messages and grouping streamed chunks. Uses CSS for styling.
*   [X] Implement a basic task distribution mechanism in `AgentManager` (broadcast task to all *available* agents concurrently).
*   [ ] Define basic inter-agent communication placeholder logic within `AgentManager` (e.g., one agent sending a message to another via the manager). *(Deferred to later phase/refinement)*
*   [X] Update `FUNCTIONS_INDEX.md` with new functions.

**Phase 4: Configuration & Sandboxing (Completed)**

*   [X] Implement loading detailed agent configurations (model, system prompt, temperature, persona) from a file (`config.yaml`). Linked this to Phase 3 agent creation (replaced hardcoding).
*   [X] Update `Agent` and `AgentManager` to use loaded configurations (`settings.py`, `agents/core.py`, `agents/manager.py`).
*   [ ] Implement UI elements (potentially on a separate settings page/modal later) to *view* current configurations. (Editing via UI can be a later phase). *(Deferred)*
*   [X] Implement dynamic creation of sandbox directories (`sandboxes/agent_<id>/`) when agents are initialized (`agents/manager.py`, `agents/core.py`).
*   [X] Ensure agents know their sandbox path (`agents/core.py`).
*   [X] Update `FUNCTIONS_INDEX.md` with new functions.

**Phase 5: Basic Tool Implementation (Internal MCP-Inspired) (~ In Progress ~)**

*   [ ] Define a structure/base class for tools (`tools/base.py`).
*   [ ] Implement `ToolRegistry` and `ToolExecutor` (`tools/executor.py`).
*   [ ] Implement a simple `FileSystemTool` (`tools/file_system.py`) with `read_file` and `write_file` functions operating strictly within the agent's sandbox.
*   [ ] Modify `Agent` class:
    *   Detect tool use requests in LLM responses (e.g., specific JSON format or XML tags).
    *   Format requests for the `ToolExecutor`.
    *   Process results from the `ToolExecutor` and potentially feed them back to the LLM.
*   [ ] Modify `AgentManager` to route tool requests from Agents to the `ToolExecutor`.
*   [ ] Update UI to show tool usage indication.
*   [ ] Update `FUNCTIONS_INDEX.md` with new functions.

**Phase 6: UI Enhancements & Advanced Features**

*   [ ] Improve UI layout for clarity (separate areas for input, system messages, agent outputs).
*   [ ] Display agent status indicators (e.g., idle, thinking, using tool).
*   [ ] Implement message history display.
*   [ ] Add basic file upload capability in UI to send context/files to agents (initial step: save to a shared or agent-specific location).
*   [ ] Implement UI for editing agent configurations and saving them.
*   [ ] Add support for more LLM providers (e.g., Ollama via local HTTP endpoint).

**Phase 7: Refinement, Optimization & Documentation**

*   [ ] Improve error handling and reporting (backend and UI).
*   [ ] Optimize performance, especially WebSocket handling and LLM calls. Review dependencies for lightness.
*   [ ] Refine sandboxing security (basic path traversal checks).
*   [ ] Add more tools (e.g., simple web search using `aiohttp`).
*   [ ] Write comprehensive usage instructions in `README.md`.
*   [ ] Add more details to `FUNCTIONS_INDEX.md`.
*   [ ] Code cleanup and final review.

## 6. MCP Integration Note

While a full MCP server is out of scope initially, the internal Tool Executor design (Phase 5) will be inspired by MCP's core idea: structured, discoverable tools that agents can request. We will focus on a simplified internal protocol first (e.g., agents outputting a specific JSON or XML structure requesting a tool call). This provides a foundation that *could* be adapted to interface with a proper MCP server later if desired.
