<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions defined within the TrippleEffect framework, including their location, purpose, and key parameters. It helps in understanding the codebase and navigating between different components.

*   **Format:** `[File Path]::[Function Name](parameters) - Description`

---

**Phase 1: Core Backend Setup & Basic UI**

*   `src/main.py::read_root()` - (Removed in Phase 1) Simple HTTP GET endpoint at `/`.
*   `src/api/http_routes.py::get_index_page(request: Request)` - Serves the main `index.html` page using Jinja2 templates.
*   `src/api/websocket_manager.py::broadcast(message: str)` - *Initial version:* Sends a message to all active WebSocket connections.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` - *Initial version:* Handles WebSocket connections, accepts messages, echoes back.

**Phase 2: Agent Core & Single Agent Interaction**

*   `src/config/settings.py::Settings` (Class) - Holds application settings loaded from environment variables.
*   `src/config/settings.py::settings` (Instance) - Singleton instance of the `Settings` class.
*   `src/agents/core.py::Agent.__init__(agent_id: str, config: Optional[Dict[str, Any]] = None)` - *Old version:* Initializes an agent instance, sets ID, loads config/defaults from `settings`.
*   `src/agents/core.py::Agent.set_manager(manager)` - Stores a reference to the `AgentManager`.
*   `src/agents/core.py::Agent.initialize_openai_client(api_key: Optional[str] = None)` -> `bool` - Initializes the `openai.AsyncOpenAI` client using provided key or key from `settings`. Returns bool success/failure.
*   `src/agents/core.py::Agent.process_message(message_content: str)` - *Old version (Async generator):* Processes user message, adds to history, calls OpenAI API (`stream=True`), yields response chunks, updates history. Handles basic errors.
*   `src/agents/core.py::Agent.get_state()` -> `Dict[str, Any]` - Returns a dictionary with the agent's current status.
*   `src/agents/core.py::Agent.clear_history()` - Clears the agent's message history (keeps system prompt).
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager: Optional[Any] = None)` - *Updated version (Phase 3):* Initializes the manager, creates multiple agents (hardcoded in Phase 3) via `_initialize_agents`, stores UI broadcast function reference.
*   `src/agents/manager.py::AgentManager._initialize_agents()` - *Updated version (Phase 3):* Creates and initializes multiple hardcoded agent instances (`agent_0`, `agent_1`, `agent_2`), calls `agent.initialize_openai_client()`.
*   `src/agents/manager.py::AgentManager.handle_user_message(message: str, client_id: Optional[str] = None)` - *Updated version (Phase 3):* Async method. Entry point for user messages. Dispatches the task concurrently to all *available* agents using `asyncio.create_task` and `_process_message_for_agent`.
*   `src/agents/manager.py::AgentManager._send_to_ui(message_data: Dict[str, Any])` - Async helper. Sends JSON-serialized data to the UI using the injected broadcast function.
*   `src/agents/manager.py::AgentManager.get_agent_status()` -> `Dict[str, Dict[str, Any]]` - Returns status dictionaries for all managed agents.
*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level function to inject the created `AgentManager` instance.
*   `src/api/websocket_manager.py::broadcast(message: str)` - *Updated version (Phase 2):* Async. Sends message to all active WebSocket connections with improved error handling and connection cleanup.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` - *Updated version (Phase 2):* Async. Handles connection lifecycle. On message receipt, calls `asyncio.create_task()` to run `agent_manager_instance.handle_user_message()` non-blockingly.

**Phase 3: Multi-Agent Setup & Basic Coordination**

*   `src/agents/manager.py::AgentManager._process_message_for_agent(agent: Agent, message: str)` - *(Removed in Phase 5)* Async helper coroutine. Handles calling `agent.process_message` for a single agent, streaming results back to the UI via `_send_to_ui`, including status updates.
*   `static/js/app.js::addMessage(data)` - (Frontend JS) Parses incoming WebSocket message data (JSON), creates/updates HTML elements in the message area, handles message types (`status`, `error`, `user`, `agent_response`), groups streamed agent responses by `agent_id`, applies CSS classes. Handles tool execution status messages.
*   `static/js/app.js::connectWebSocket()` - (Frontend JS) Establishes the WebSocket connection, sets up event handlers (`onopen`, `onmessage`, `onerror`, `onclose`).
*   `static/js/app.js::sendMessage()` - (Frontend JS) Reads message from input, displays it locally as a user message, sends the message text over the WebSocket, clears the input.

**Phase 4: Configuration & Sandboxing**

*   `src/config/settings.py::load_agent_config()` -> `List[Dict[str, Any]]` - Loads agent configurations from `config.yaml`, handles file not found and parsing errors.
*   `src/config/settings.py::Settings.__init__()` - Modified to call `load_agent_config` and store results in `self.AGENT_CONFIGURATIONS`. Also loads default agent parameters from environment variables.
*   `src/config/settings.py::Settings.get_agent_config_by_id(agent_id: str)` -> `Optional[Dict[str, Any]]` - Retrieves a specific agent's configuration dictionary by its ID from the loaded list.
*   `src/agents/core.py::Agent.__init__(agent_config: Dict[str, Any])` - *Updated version (Phase 4):* Initializes an agent using a configuration dictionary (typically from `config.yaml`). Sets agent parameters (`model`, `system_prompt`, `temperature`, `persona`) and defines `sandbox_path`.
*   `src/agents/core.py::Agent.ensure_sandbox_exists()` -> `bool` - Creates the agent's specific sandbox directory (`sandboxes/agent_<id>/`) if it doesn't exist. Returns True on success/existence, False on error.
*   `src/agents/core.py::Agent.get_state()` -> `Dict[str, Any]` - *Updated version (Phase 4):* Includes `persona` and `sandbox_path` in the returned state dictionary.
*   `src/agents/manager.py::AgentManager.__init__(...)` - *Updated version (Phase 4):* Calls the modified `_initialize_agents` which now uses `config.yaml`.
*   `src/agents/manager.py::AgentManager._initialize_agents()` - *Rewritten version (Phase 4):* Reads agent configurations from `settings.AGENT_CONFIGURATIONS`, loops through them, creates Agent instances, ensures sandbox directories exist, initializes OpenAI clients, and adds successful agents to `self.agents`.

**Phase 5: Basic Tool Implementation (Internal MCP-Inspired)**

*   `src/tools/base.py::ToolParameter` (Pydantic Class) - Defines tool parameters.
*   `src/tools/base.py::BaseTool` (ABC) - Abstract base class for tools.
*   `src/tools/base.py::BaseTool.execute(agent_id: str, agent_sandbox_path: Path, **kwargs: Any)` -> `Any` (Abstract Async Method) - Core execution logic for a tool.
*   `src/tools/base.py::BaseTool.get_schema()` -> `Dict[str, Any]` - Returns tool description schema.
*   `src/tools/file_system.py::FileSystemTool` (Class inherits `BaseTool`) - Implements file system operations.
*   `src/tools/file_system.py::FileSystemTool.execute(...)` (Overrides `BaseTool.execute`) - Dispatches actions ('read', 'write', 'list').
*   `src/tools/file_system.py::FileSystemTool._resolve_and_validate_path(sandbox_path: Path, relative_file_path: str)` -> `Path | None` (Async Helper) - Secure path validation within sandbox.
*   `src/tools/file_system.py::FileSystemTool._read_file(sandbox_path: Path, filename: str)` -> `str` (Async Helper) - Reads file content from sandbox.
*   `src/tools/file_system.py::FileSystemTool._write_file(sandbox_path: Path, filename: str, content: str)` -> `str` (Async Helper) - Writes file content to sandbox.
*   `src/tools/file_system.py::FileSystemTool._list_directory(sandbox_path: Path, relative_dir: str)` -> `str` (Async Helper) - Lists directory contents in sandbox.
*   `src/tools/executor.py::ToolExecutor` (Class) - Manages and executes tools.
*   `src/tools/executor.py::ToolExecutor.__init__()` - Initializes and registers available tools (e.g., `FileSystemTool`).
*   `src/tools/executor.py::ToolExecutor._register_available_tools()` - Helper for registration.
*   `src/tools/executor.py::ToolExecutor.register_tool(tool_instance: BaseTool)` - Manual registration method.
*   `src/tools/executor.py::ToolExecutor.get_tool_schemas()` -> `List[Dict[str, Any]]` - Gets schemas for all registered tools.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions()` -> `str` - Formats tool schemas into a string suitable for LLM prompts.
*   `src/tools/executor.py::ToolExecutor.execute_tool(agent_id: str, agent_sandbox_path: Path, tool_name: str, tool_args: Dict[str, Any])` -> `str` (Async Method) - Executes a specific registered tool by name.
*   `src/agents/core.py::Agent.__init__(...)` - *Updated version (Phase 5):* Stores `original_system_prompt`, adds `tool_executor` attribute.
*   `src/agents/core.py::Agent.set_tool_executor(tool_executor: 'ToolExecutor')` - New method to inject `ToolExecutor`.
*   `src/agents/core.py::Agent.update_system_prompt_with_tools()` - New method to potentially add tool descriptions to the system prompt (caller decides).
*   `src/agents/core.py::Agent.process_message(message_content: str)` -> `AsyncGenerator[Dict[str, Any], Optional[List[Dict[str, Any]]]]` - **Rewritten version (Phase 5):** Async generator using OpenAI tool calling. Yields events (`response_chunk`, `tool_requests`, `final_response`, `error`, `status`). Receives tool results via `generator.asend()`. Handles streaming tool calls.
*   `src/agents/core.py::Agent.get_state()` -> `Dict[str, Any]` - *Updated version (Phase 5):* Includes `tool_executor_set` status.
*   `src/agents/core.py::Agent.clear_history()` - *Updated version (Phase 5):* Resets using `original_system_prompt`.
*   `src/agents/manager.py::AgentManager.__init__(...)` - *Updated version (Phase 5):* Instantiates `ToolExecutor`.
*   `src/agents/manager.py::AgentManager._initialize_agents()` - *Updated version (Phase 5):* Injects `ToolExecutor` into created agents.
*   `src/agents/manager.py::AgentManager.handle_user_message(...)` - *Updated version (Phase 5):* Creates tasks using `_handle_agent_generator`.
*   `src/agents/manager.py::AgentManager._handle_agent_generator(agent: Agent, message: str)` - **New Async Method (Phase 5):** Manages the agent's `process_message` generator loop, handles yielded events, calls `_execute_single_tool` for `tool_requests`, and sends results back via `asend()`.
*   `src/agents/manager.py::AgentManager._execute_single_tool(agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any])` -> `Optional[Dict[str, str]]` - **New Async Helper (Phase 5):** Executes a single tool via `ToolExecutor`, formats the result/error dictionary for the agent generator.
*   `static/js/app.js::handleAgentResponseChunk(newChunkElement, agentId, chunkContent)` - **New JS function (Phase 5):** Handles grouping of agent response chunks directly in the DOM.

---

*(Index will be populated further as functions are implemented)*
