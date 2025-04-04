<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py` (Script execution block) - Initializes FastAPI app, loads .env, instantiates `AgentManager`, injects it into `WebSocketManager`, mounts static files, includes routers, runs Uvicorn server.

## **Configuration (`src/config/`)**

*   `src/config/settings.py::load_agent_config()` -> `List[Dict[str, Any]]` - Loads agent configurations from `config.yaml`, handles file/parsing errors.
*   `src/config/settings.py::Settings` (Class) - Holds application settings loaded from `.env` and `config.yaml`. Manages provider API keys/URLs and default agent parameters.
*   `src/config/settings.py::Settings.__init__()` - Initializes settings, loads env vars and agent configs, calls `_check_required_keys`.
*   `src/config/settings.py::Settings._check_required_keys()` - Validates if necessary API keys/URLs are set based on configured agents/providers.
*   `src/config/settings.py::Settings.get_provider_config(provider_name: str)` -> `Dict` - Gets default API key/URL/referer config for a specific provider ('openai', 'ollama', 'openrouter').
*   `src/config/settings.py::Settings.get_agent_config_by_id(agent_id: str)` -> `Optional[Dict[str, Any]]` - Retrieves a specific agent's nested 'config' dictionary by its ID.
*   `src/config/settings.py::settings` (Instance) - Singleton instance of the `Settings` class, accessible globally.

## **API Routes (`src/api/`)**

*   `src/api/http_routes.py::get_index_page(request: Request)` - Serves the main `index.html` page using Jinja2 templates.

## **WebSocket Management (`src/api/`)**

*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level function to inject the `AgentManager` instance.
*   `src/api/websocket_manager.py::broadcast(message: str)` - Asynchronously sends a message string to all active WebSocket connections. Handles disconnects.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` - Async handler for the `/ws` endpoint. Manages connection lifecycle, receives messages, and asynchronously calls `agent_manager_instance.handle_user_message()`.

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents an individual LLM agent.
*   `src/agents/core.py::Agent.__init__(agent_config: Dict, llm_provider: BaseLLMProvider, tool_executor: Optional['ToolExecutor'], manager: Optional['AgentManager'])` - Initializes agent with config and injected dependencies (provider, executor, manager). Sets up state, sandbox path.
*   `src/agents/core.py::Agent.set_manager(manager: 'AgentManager')` - Sets the `AgentManager` reference.
*   `src/agents/core.py::Agent.set_tool_executor(tool_executor: 'ToolExecutor')` - Sets the `ToolExecutor` reference.
*   `src/agents/core.py::Agent.ensure_sandbox_exists()` -> `bool` - Creates the agent's sandbox directory if needed.
*   `src/agents/core.py::Agent.process_message(message_content: str)` -> `AsyncGenerator[Dict, Optional[List[ToolResultDict]]]` - Core agent logic. Calls the injected provider's `stream_completion`, yields standardized events (`response_chunk`, `tool_requests`, `error`, `status`), and interacts with the provider generator via `asend()` to facilitate tool calls.
*   `src/agents/core.py::Agent.get_state()` -> `Dict[str, Any]` - Returns a dictionary with the agent's current status (ID, persona, busy status, provider info, etc.).
*   `src/agents/core.py::Agent.clear_history()` - Clears the agent's message history, keeping only the system prompt.

## **Agent Manager (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator for agents.
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager: Optional[Any] = None)` - Initializes the manager, instantiates `ToolExecutor`, calls `_initialize_agents`.
*   `src/agents/manager.py::AgentManager._initialize_agents()` - Reads agent configurations, selects provider class, gathers final config (env + overrides), instantiates provider, instantiates agent with dependencies (provider, executor, manager), ensures sandbox, adds agent to `self.agents`.
*   `src/agents/manager.py::AgentManager.handle_user_message(message: str, client_id: Optional[str] = None)` - Entry point for user messages. Dispatches the task concurrently to all *available* agents using `asyncio.create_task` and `_handle_agent_generator`.
*   `src/agents/manager.py::AgentManager._handle_agent_generator(agent: Agent, message: str)` - Async method. Manages the agent's `process_message` generator loop, handles yielded events, calls `_execute_single_tool` for `tool_requests`, and sends results back to the agent's generator via `asend()`.
*   `src/agents/manager.py::AgentManager._execute_single_tool(agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any])` -> `Optional[ToolResultDict]` - Executes a single tool via `ToolExecutor`, formats the result/error dictionary for the agent generator.
*   `src/agents/manager.py::AgentManager._failed_tool_result(call_id: Optional[str], tool_name: Optional[str])` -> `Optional[ToolResultDict]` - Helper to return a formatted error result for tools that failed dispatch.
*   `src/agents/manager.py::AgentManager._send_to_ui(message_data: Dict[str, Any])` - Async helper. Sends JSON-serialized data to the UI using the injected broadcast function.
*   `src/agents/manager.py::AgentManager.get_agent_status()` -> `Dict[str, Dict[str, Any]]` - Returns status dictionaries for all managed agents.
*   `src/agents/manager.py::AgentManager.cleanup_providers()` - Async method. Iterates through agents and calls cleanup methods (like `close_session`) on their providers.

## **LLM Providers Base (`src/llm_providers/`)**

*   `src/llm_providers/base.py::BaseLLMProvider` (ABC) - Abstract Base Class defining the interface for LLM providers.
*   `src/llm_providers/base.py::BaseLLMProvider.__init__(api_key, base_url, **kwargs)` (Abstract) - Provider initialization signature.
*   `src/llm_providers/base.py::BaseLLMProvider.stream_completion(messages, model, temperature, max_tokens, tools, tool_choice, **kwargs)` -> `AsyncGenerator[Dict, Optional[List[ToolResultDict]]]` (Abstract Async) - Defines the core interaction method. Must yield standardized events ('response_chunk', 'tool_requests', 'error', 'status') and handle tool results sent back via `asend()`.

## **LLM Providers Implementations (`src/llm_providers/`)**

*   `src/llm_providers/openai_provider.py::OpenAIProvider` (Class inherits `BaseLLMProvider`) - Implementation for OpenAI API.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.__init__(...)` - Initializes `openai.AsyncOpenAI` client.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.stream_completion(...)` - Implements the abstract method using `openai` library, handling streaming and the tool call loop.
*   `src/llm_providers/ollama_provider.py::OllamaProvider` (Class inherits `BaseLLMProvider`) - Implementation for Ollama API.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.__init__(...)` - Initializes provider, stores base URL.
*   `src/llm_providers/ollama_provider.py::OllamaProvider._get_session()` -> `aiohttp.ClientSession` - Manages `aiohttp` session.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.close_session()` - Closes `aiohttp` session.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.stream_completion(...)` - Implements the abstract method using `aiohttp` to call Ollama's `/api/chat`, handling streaming and attempting tool calls based on model response.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider` (Class inherits `BaseLLMProvider`) - Implementation for OpenRouter API.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.__init__(...)` - Initializes `openai.AsyncOpenAI` client configured for OpenRouter (URL, key, headers).
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.stream_completion(...)` - Implements the abstract method using the configured `openai` client, handling streaming and tool calls via the compatible API.

## **Tools Base (`src/tools/`)**

*   `src/tools/base.py::ToolParameter` (Pydantic Class) - Defines parameters for a tool (name, type, description, required).
*   `src/tools/base.py::BaseTool` (ABC) - Abstract base class for all tools. Defines `name`, `description`, `parameters`.
*   `src/tools/base.py::BaseTool.execute(agent_id: str, agent_sandbox_path: Path, **kwargs: Any)` -> `Any` (Abstract Async Method) - Core execution logic signature for a tool.
*   `src/tools/base.py::BaseTool.get_schema()` -> `Dict[str, Any]` - Returns tool description schema suitable for LLM consumption (e.g., OpenAI `tools` parameter).

## **Tool Executor (`src/tools/`)**

*   `src/tools/executor.py::ToolExecutor` (Class) - Manages and executes available tools.
*   `src/tools/executor.py::ToolExecutor.__init__()` - Initializes and registers tools listed in `AVAILABLE_TOOL_CLASSES`.
*   `src/tools/executor.py::ToolExecutor._register_available_tools()` - Helper for registration during init.
*   `src/tools/executor.py::ToolExecutor.register_tool(tool_instance: BaseTool)` - Allows manual registration of a tool instance.
*   `src/tools/executor.py::ToolExecutor.get_tool_schemas()` -> `List[Dict[str, Any]]` - Gets schemas for all registered tools.
*   `src/tools/executor.py::ToolExecutor.execute_tool(agent_id: str, agent_sandbox_path: Path, tool_name: str, tool_args: Dict[str, Any])` -> `str` (Async Method) - Finds the tool by name and calls its `execute` method, returning the result as a string. Handles errors.

## **Tool Implementations (`src/tools/`)**

*   `src/tools/file_system.py::FileSystemTool` (Class inherits `BaseTool`) - Tool for file system operations within an agent's sandbox.
*   `src/tools/file_system.py::FileSystemTool.execute(...)` (Overrides `BaseTool.execute`) - Dispatches actions ('read', 'write', 'list') to helper methods.
*   `src/tools/file_system.py::FileSystemTool._resolve_and_validate_path(sandbox_path: Path, relative_file_path: str)` -> `Path | None` (Async Helper) - Securely resolves and validates a path within the agent's sandbox. **Crucial for security.**
*   `src/tools/file_system.py::FileSystemTool._read_file(sandbox_path: Path, filename: str)` -> `str` (Async Helper) - Reads file content from the sandbox.
*   `src/tools/file_system.py::FileSystemTool._write_file(sandbox_path: Path, filename: str, content: str)` -> `str` (Async Helper) - Writes file content to the sandbox.
*   `src/tools/file_system.py::FileSystemTool._list_directory(sandbox_path: Path, relative_dir: str)` -> `str` (Async Helper) - Lists directory contents within the sandbox.

## **Frontend Logic (`static/js/app.js`)**

*   `static/js/app.js::connectWebSocket()` - Establishes the WebSocket connection to `/ws`, sets up event handlers (`onopen`, `onmessage`, `onerror`, `onclose`).
*   `static/js/app.js::sendMessage()` - Reads message from input textarea, displays it locally as a user message, sends the message text over the WebSocket, clears the input.
*   `static/js/app.js::addMessage(data)` - Parses incoming WebSocket message data (JSON), determines message type (`status`, `error`, `user`, `agent_response`), creates/updates HTML elements in the message area, applies appropriate CSS classes (including agent-specific via `data-agent-id`), handles grouping of streamed agent responses.
*   `static/js/app.js::handleAgentResponseChunk(newChunkElement, agentId, chunkContent)` - Helper function (likely called by `addMessage`) to manage appending streamed text chunks to the correct agent's message block in the UI.

---

## **Discontinued Functions/Methods**

*   `src/main.py::read_root()` - Removed in Phase 1 (simple GET endpoint at `/`).
*   `src/agents/core.py::Agent.initialize_openai_client(api_key: Optional[str] = None)` - Removed in Phase 5.5 (provider handles client initialization).
*   `src/agents/core.py::Agent.update_system_prompt_with_tools()` - Removed in Phase 5.5 (relying on `tools` parameter passed to providers).
*   `src/agents/manager.py::AgentManager._process_message_for_agent(agent: Agent, message: str)` - Removed in Phase 5 (replaced by generator handling).
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions()` -> `str` - Potentially Obsolete in Phase 5.5 (Manual formatting for prompts less needed with structured `tools` parameter, though might be useful for non-OpenAI standard models).
*   `src/tools/executor.py::ToolExecutor.parse_tool_call(...)` -> `Optional[Tuple[str, Dict]]` - Obsolete in Phase 5 (Manual JSON parsing replaced by provider handling of native tool/function calls).

---
