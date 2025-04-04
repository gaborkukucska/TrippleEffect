<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, including their location, purpose, and key parameters. It helps in understanding the codebase and navigating between different components.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

**Phase 1: Core Backend Setup & Basic UI (Completed)**

*   `src/main.py::read_root()` - (Removed)
*   `src/api/http_routes.py::get_index_page(request: Request)` - Serves the main `index.html` page.
*   `src/api/websocket_manager.py::broadcast(message: str)` - Sends a message to all active WebSocket connections.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` - Handles WebSocket connections, accepts messages, passes to AgentManager.

**Phase 2: Agent Core & Single Agent Interaction (Completed)**

*   `src/config/settings.py::Settings` (Class) - Holds application settings from .env.
*   `src/config/settings.py::settings` (Instance) - Singleton instance of `Settings`.
*   `src/agents/core.py::Agent.__init__(...)` - (Old Version)
*   `src/agents/core.py::Agent.set_manager(...)` - Stores reference to `AgentManager`.
*   `src/agents/core.py::Agent.initialize_openai_client(...)` - (Removed in Phase 5.5)
*   `src/agents/core.py::Agent.process_message(...)` - (Old Version - Direct OpenAI call)
*   `src/agents/core.py::Agent.get_state()` - Returns agent status dictionary.
*   `src/agents/core.py::Agent.clear_history()` - Clears agent message history.
*   `src/agents/manager.py::AgentManager.__init__(...)` - (Old Versions)
*   `src/agents/manager.py::AgentManager._initialize_agents()` - (Old Versions)
*   `src/agents/manager.py::AgentManager.handle_user_message(...)` - (Old Versions)
*   `src/agents/manager.py::AgentManager._send_to_ui(...)` - Sends JSON data to UI via broadcast function.
*   `src/agents/manager.py::AgentManager.get_agent_status()` - Returns status for all agents.
*   `src/api/websocket_manager.py::set_agent_manager(...)` - Injects `AgentManager` instance.
*   `src/api/websocket_manager.py::broadcast(...)` - Async, sends message to all connections.
*   `src/api/websocket_manager.py::websocket_endpoint(...)` - Async, handles connection lifecycle, calls `AgentManager.handle_user_message`.

**Phase 3: Multi-Agent Setup & Basic Coordination (Completed)**

*   (No major function signature changes relevant here, logic within manager updated).

**Phase 4: Configuration & Sandboxing (Completed)**

*   `src/config/settings.py::load_agent_config()` -> `List[Dict[str, Any]]` - Loads agent configurations from `config.yaml`.
*   `src/config/settings.py::Settings.__init__()` - Updated to load agent configs and defaults.
*   `src/config/settings.py::Settings.get_agent_config_by_id(...)` -> `Optional[Dict[str, Any]]` - Retrieves a specific agent's config dict.
*   `src/agents/core.py::Agent.__init__(...)` - (Updated in Phase 4 for config dict)
*   `src/agents/core.py::Agent.ensure_sandbox_exists()` -> `bool` - Creates agent's sandbox directory.
*   `src/agents/core.py::Agent.get_state()` -> `Dict[str, Any]` - Updated to include persona, sandbox_path.
*   `src/agents/manager.py::AgentManager.__init__(...)` - Updated to call revised `_initialize_agents`.
*   `src/agents/manager.py::AgentManager._initialize_agents()` - Rewritten to use `settings.AGENT_CONFIGURATIONS`, create agents, ensure sandboxes.

**Phase 5: Basic Tool Implementation (Internal MCP-Inspired) (Completed)**

*   `src/tools/base.py::ToolParameter` (Pydantic Class) - Defines tool parameters.
*   `src/tools/base.py::BaseTool` (ABC) - Abstract base class for tools.
*   `src/tools/base.py::BaseTool.execute(...)` -> `Any` (Abstract Async Method) - Core tool execution logic.
*   `src/tools/base.py::BaseTool.get_schema()` -> `Dict[str, Any]` - Returns tool description schema.
*   `src/tools/file_system.py::FileSystemTool` (Class inherits `BaseTool`) - Implements file system operations.
*   `src/tools/file_system.py::FileSystemTool.execute(...)` - Dispatches file actions ('read', 'write', 'list').
*   `src/tools/file_system.py::FileSystemTool._resolve_and_validate_path(...)` -> `Path | None` - Secure path validation.
*   `src/tools/file_system.py::FileSystemTool._read_file(...)` -> `str` - Reads file from sandbox.
*   `src/tools/file_system.py::FileSystemTool._write_file(...)` -> `str` - Writes file to sandbox.
*   `src/tools/file_system.py::FileSystemTool._list_directory(...)` -> `str` - Lists directory contents.
*   `src/tools/executor.py::ToolExecutor` (Class) - Manages and executes tools.
*   `src/tools/executor.py::ToolExecutor.__init__()` - Registers available tools.
*   `src/tools/executor.py::ToolExecutor._register_available_tools()` - Helper for registration.
*   `src/tools/executor.py::ToolExecutor.register_tool(...)` - Manual registration.
*   `src/tools/executor.py::ToolExecutor.get_tool_schemas()` -> `List[Dict[str, Any]]` - Gets schemas for all tools (used for LLM).
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions()` -> `str` - **(Potentially Obsolete)** Formats schemas for prompts.
*   `src/tools/executor.py::ToolExecutor.parse_tool_call(...)` -> `Optional[Tuple[str, Dict]]` - **(Obsolete)** Parses manual JSON tool calls.
*   `src/tools/executor.py::ToolExecutor.execute_tool(...)` -> `str` - Executes a specified tool by name.
*   `src/agents/core.py::Agent.__init__(...)` - (Updated in Phase 5)
*   `src/agents/core.py::Agent.set_tool_executor(...)` - Injects `ToolExecutor`.
*   `src/agents/core.py::Agent.update_system_prompt_with_tools()` - (Removed in Phase 5.5)
*   `src/agents/core.py::Agent.process_message(...)` -> `AsyncGenerator[Dict, Optional[List[Dict]]]` - Rewritten for OpenAI tool calling generator.
*   `src/agents/core.py::Agent.get_state()` -> `Dict[str, Any]` - Updated.
*   `src/agents/core.py::Agent.clear_history()` - Updated.
*   `src/agents/manager.py::AgentManager.__init__(...)` - Updated to instantiate `ToolExecutor`.
*   `src/agents/manager.py::AgentManager._initialize_agents()` - Updated to inject `ToolExecutor`.
*   `src/agents/manager.py::AgentManager.handle_user_message(...)` - Updated to use `_handle_agent_generator`.
*   `src/agents/manager.py::AgentManager._handle_agent_generator(...)` - Manages agent generator loop, calls `_execute_single_tool`, sends results via `asend()`.
*   `src/agents/manager.py::AgentManager._execute_single_tool(...)` -> `Optional[Dict[str, str]]` - Executes single tool via `ToolExecutor`.
*   `static/js/app.js::handleAgentResponseChunk(...)` - Handles grouping agent response chunks.
*   `static/js/app.js::addMessage(data)` - Parses WebSocket messages, updates UI.
*   `static/js/app.js::connectWebSocket()` - Establishes WebSocket connection.
*   `static/js/app.js::sendMessage()` - Sends user message via WebSocket.

**Phase 5.5: LLM Provider Abstraction (Completed)**

*   `src/llm_providers/base.py::BaseLLMProvider` (ABC) - Defines the interface for LLM providers.
*   `src/llm_providers/base.py::BaseLLMProvider.__init__(api_key, base_url, **kwargs)` (Abstract) - Provider initialization.
*   `src/llm_providers/base.py::BaseLLMProvider.stream_completion(...)` -> `AsyncGenerator[Dict, Optional[List[ToolResultDict]]]` (Abstract Async) - Core method for streaming completions and handling tool calls. Yields standardized events. Receives tool results via `asend()`.
*   `src/llm_providers/openai_provider.py::OpenAIProvider` (Class inherits `BaseLLMProvider`) - Implements provider using `openai` library.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.__init__(...)` - Initializes `openai.AsyncOpenAI`.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.stream_completion(...)` - Implements streaming and tool call loop via OpenAI API.
*   `src/llm_providers/ollama_provider.py::OllamaProvider` (Class inherits `BaseLLMProvider`) - Implements provider using `aiohttp`.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.__init__(...)` - Initializes provider, stores base URL.
*   `src/llm_providers/ollama_provider.py::OllamaProvider._get_session()` -> `aiohttp.ClientSession` - Gets/creates `aiohttp` session.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.close_session()` - Closes `aiohttp` session.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.stream_completion(...)` - Implements streaming and tool call loop via Ollama `/api/chat` endpoint.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider` (Class inherits `BaseLLMProvider`) - Implements provider using `openai` library configured for OpenRouter.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.__init__(...)` - Initializes `openai.AsyncOpenAI` with OpenRouter URL, key, and headers.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.stream_completion(...)` - Implements streaming and tool call loop via OpenRouter's OpenAI-compatible API.
*   `src/config/settings.py::Settings.__init__()` - Updated to load provider keys/URLs/defaults from `.env`.
*   `src/config/settings.py::Settings._check_required_keys()` - Validates presence of needed keys based on config.
*   `src/config/settings.py::Settings.get_provider_config(provider_name)` -> `Dict` - Gets default key/URL for a provider.
*   `src/agents/core.py::Agent.__init__(agent_config, llm_provider, tool_executor, manager)` - **Rewritten** to accept injected `BaseLLMProvider`. Stores provider name and kwargs.
*   `src/agents/core.py::Agent.process_message(...)` -> `AsyncGenerator[Dict, Optional[List[ToolResultDict]]]` - **Rewritten** to call `llm_provider.stream_completion()` and handle its event stream, forwarding `tool_requests` and sending back results via the provider's `asend()`.
*   `src/agents/core.py::Agent.get_state()` - Updated to include provider info.
*   `src/agents/manager.py::AgentManager.__init__(...)` - Initializes agents using updated `_initialize_agents`.
*   `src/agents/manager.py::AgentManager._initialize_agents()` - **Rewritten** to select provider class, gather final config (env defaults + config overrides), instantiate the correct provider, and inject it into the `Agent`.
*   `src/agents/manager.py::AgentManager._handle_agent_generator(...)` - Updated loop logic to use `agent_generator.asend()` to pass tool results back to the agent (which then passes them to the provider).
*   `src/agents/manager.py::AgentManager.cleanup_providers()` - **New Async Method** - Iterates agents and calls `close_session()` on providers if available (for `aiohttp`).
*   `src/agents/manager.py::AgentManager._failed_tool_result(...)` - **New Async Helper** - Creates error dict for failed tool dispatch.

---

*(Index reflects state after Phase 5.5)*
