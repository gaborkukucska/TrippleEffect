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
*   `src/agents/core.py::Agent.__init__(agent_id: str, config: Optional[Dict[str, Any]] = None)` - Initializes an agent instance, sets ID, loads config/defaults from `settings`.
*   `src/agents/core.py::Agent.set_manager(manager)` - Stores a reference to the `AgentManager`.
*   `src/agents/core.py::Agent.initialize_openai_client(api_key: Optional[str] = None)` - Initializes the `openai.AsyncOpenAI` client using provided key or key from `settings`. Returns bool success/failure.
*   `src/agents/core.py::Agent.process_message(message_content: str)` - Async generator. Processes user message, adds to history, calls OpenAI API (`stream=True`), yields response chunks, updates history. Handles basic errors.
*   `src/agents/core.py::Agent.get_state()` -> `Dict[str, Any]` - Returns a dictionary with the agent's current status.
*   `src/agents/core.py::Agent.clear_history()` - Clears the agent's message history (keeps system prompt).
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager: Optional[Any] = None)` - Initializes the manager, creates agents via `_initialize_agents`, stores UI broadcast function reference.
*   `src/agents/manager.py::AgentManager._initialize_agents()` - Creates and initializes agent instances (currently single agent `agent_0`), calls `agent.initialize_openai_client()`.
*   `src/agents/manager.py::AgentManager.handle_user_message(message: str, client_id: Optional[str] = None)` - Async method. Entry point for user messages. Selects agent, checks status, calls `agent.process_message()`, and streams response back via `_send_to_ui()`.
*   `src/agents/manager.py::AgentManager._send_to_ui(message_data: Dict[str, Any])` - Async helper. Sends JSON-serialized data to the UI using the injected broadcast function.
*   `src/agents/manager.py::AgentManager.get_agent_status()` -> `Dict[str, Dict[str, Any]]` - Returns status dictionaries for all managed agents.
*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level function to inject the created `AgentManager` instance.
*   `src/api/websocket_manager.py::broadcast(message: str)` - *Updated version:* Async. Sends message to all active WebSocket connections with improved error handling and connection cleanup.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` - *Updated version:* Async. Handles connection lifecycle. On message receipt, calls `asyncio.create_task()` to run `agent_manager_instance.handle_user_message()` non-blockingly.

---

*(Index will be populated further as functions are implemented)*

---
