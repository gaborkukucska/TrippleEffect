<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages application startup and shutdown events. Calls `agent_manager.cleanup_providers()` on shutdown.
*   `src/main.py` (Script execution block) - Initializes FastAPI app with lifespan, loads .env, instantiates `AgentManager`, injects it into `WebSocketManager`, mounts static files, includes routers, runs Uvicorn server.

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
*   `src/api/http_routes.py::get_agent_configurations()` -> `List[AgentInfo]` (Async) - API endpoint (`GET /api/config/agents`) to retrieve basic info for all configured agents from `settings`.

## **WebSocket Management (`src/api/`)**

*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level function to inject the `AgentManager` instance.
*   `src/api/websocket_manager.py::broadcast(message: str)` (Async) - Sends a message string to all active WebSocket connections. Handles disconnects.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` (Async) - Handler for the `/ws` endpoint. Manages connection lifecycle, receives messages, and asynchronously calls `agent_manager_instance.handle_user_message()`.

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents an individual LLM agent.
*   `src/agents/core.py::Agent.__init__(agent_config: Dict, llm_provider: BaseLLMProvider, manager: 'AgentManager', tool_descriptions_xml: str)` - Initializes agent with config, injected dependencies (provider, manager, tool descriptions), status, history, sandbox path. Compiles XML tool regex. *(Updated Signature)*
*   `src/agents/core.py::Agent::set_status(new_status: str, tool_info: Optional[Dict[str, str]] = None)` - Updates the agent's status (`self.status`), optionally stores tool info (`self.current_tool_info`), and asynchronously notifies the manager via `manager.push_agent_status_update()`.
*   `src/agents/core.py::Agent::set_manager(manager: 'AgentManager')` - Sets the `AgentManager` reference.
*   `src/agents/core.py::Agent::ensure_sandbox_exists()` -> `bool` - Creates the agent's sandbox directory if needed.
*   `src/agents/core.py::Agent::_parse_and_yield_xml_tool_call()` -> `Optional[Tuple[str, Dict[str, Any], int]]` - Helper method to check text buffer for complete XML tool calls, parse them using regex, validate against known tools, and return structured info. *(New)*
*   `src/agents/core.py::Agent::process_message()` -> `AsyncGenerator[Dict, None]` - Core agent logic. Appends user msg to history. Calls provider's `stream_completion` (without tools), buffers/yields `response_chunk`, parses buffer for XML tool calls using `_parse_and_yield_xml_tool_call`, yields `tool_requests` (with `raw_assistant_response`), ignores `asend()` value, yields `final_response`. *(Updated Signature & Logic)*
*   `src/agents/core.py::Agent::get_state()` -> `Dict[str, Any]` - Returns a dictionary with the agent's current state, including detailed status (`self.status`) and current tool info if applicable.
*   `src/agents/core.py::Agent::clear_history()` - Clears the agent's message history, keeping only the system prompt.

## **Agent Manager (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator for agents.
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager: Optional[Any] = None)` - Initializes the manager, instantiates `ToolExecutor`, calls `tool_executor.get_formatted_tool_descriptions_xml()`, calls `_initialize_agents`. *(Updated)*
*   `src/agents/manager.py::AgentManager._initialize_agents()` - Reads agent configurations, selects provider class, instantiates provider, instantiates agent with dependencies (provider, manager, `tool_descriptions_xml`), ensures sandbox, adds agent to `self.agents`. *(Updated: No tool_executor passed to Agent)*
*   `src/agents/manager.py::AgentManager::handle_user_message(message: str, client_id: Optional[str] = None)` (Async) - Entry point for user messages. Dispatches the task concurrently to all agents with status `AGENT_STATUS_IDLE` using `asyncio.create_task` and `_handle_agent_generator`. Pushes status updates for busy agents.
*   `src/agents/manager.py::AgentManager::_handle_agent_generator(agent: Agent, message: str)` (Async) - Manages the agent's `process_message` generator loop. Appends user message to agent history. Handles yielded events (`response_chunk`, `tool_requests`, etc.). Appends assistant response (incl. XML) to history. Calls `_execute_single_tool` for `tool_requests`. Appends tool results to history. Calls `asend(None)` to resume agent generator. *(Updated Logic)*
*   `src/agents/manager.py::AgentManager::_execute_single_tool(agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any])` -> `Optional[ToolResultDict]` (Async) - Sets agent status to `EXECUTING_TOOL`, executes a single tool via `ToolExecutor` (args are pre-parsed by Agent), formats the result/error, and sets agent status back to `PROCESSING` upon completion/error before returning. *(Updated Logic)*
*   `src/agents/manager.py::AgentManager::_failed_tool_result(call_id: Optional[str], tool_name: Optional[str])` -> `Optional[ToolResultDict]` (Async Helper) - Returns a formatted error result for tools that failed dispatch.
*   `src/agents/manager.py::AgentManager::push_agent_status_update(agent_id: str)` (Async Helper) - Retrieves the full state of a specific agent via `agent.get_state()` and sends it to the UI via `_send_to_ui()` using the `agent_status_update` message type.
*   `src/agents/manager.py::AgentManager::_send_to_ui(message_data: Dict[str, Any])` (Async Helper) - Sends JSON-serialized data to the UI using the injected broadcast function. Includes fallback error handling for serialization issues.
*   `src/agents/manager.py::AgentManager::get_agent_status()` -> `Dict[str, Dict[str, Any]]` - Returns status dictionaries for all managed agents (less critical with proactive updates).
*   `src/agents/manager.py::AgentManager::cleanup_providers()` (Async) - Iterates through agents and calls cleanup methods (like `close_session`) on their providers.

## **LLM Providers Base (`src/llm_providers/`)**

*   `src/llm_providers/base.py::BaseLLMProvider` (ABC) - Abstract Base Class defining the interface for LLM providers.
*   `src/llm_providers/base.py::BaseLLMProvider.__init__(api_key, base_url, **kwargs)` (Abstract) - Provider initialization signature.
*   `src/llm_providers/base.py::BaseLLMProvider.stream_completion(messages, model, temperature, max_tokens, tools=None, tool_choice=None, **kwargs)` -> `AsyncGenerator[Dict, Optional[List[ToolResultDict]]]` (Abstract Async) - Defines the core interaction method. Must yield primarily `response_chunk` (raw text), `status`, `error`. Tool handling is external (via XML parsing). `asend()` value is typically ignored by implementations. *(Updated Description)*

## **LLM Providers Implementations (`src/llm_providers/`)**

*   `src/llm_providers/openai_provider.py::OpenAIProvider` (Class inherits `BaseLLMProvider`) - Implementation for OpenAI API.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.__init__(...)` - Initializes `openai.AsyncOpenAI` client.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.stream_completion(...)` (Async Generator) - Implements the abstract method using `openai` library, making API calls *without* `tools` or `tool_choice`. Handles streaming text responses. *(Updated Logic)*
*   `src/llm_providers/ollama_provider.py::OllamaProvider` (Class inherits `BaseLLMProvider`) - Implementation for Ollama API.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.__init__(...)` - Initializes provider, stores base URL.
*   `src/llm_providers/ollama_provider.py::OllamaProvider._get_session()` -> `aiohttp.ClientSession` (Async) - Manages `aiohttp` session.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.close_session()` (Async) - Closes `aiohttp` session.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.stream_completion(...)` (Async Generator) - Implements the abstract method using `aiohttp` to call Ollama's `/api/chat`, making API calls *without* `tools`. Handles streaming text responses. *(Updated Logic)*
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider` (Class inherits `BaseLLMProvider`) - Implementation for OpenRouter API.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.__init__(...)` - Initializes `openai.AsyncOpenAI` client configured for OpenRouter (URL, key, headers).
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.stream_completion(...)` (Async Generator) - Implements the abstract method using the configured `openai` client, making API calls *without* `tools` or `tool_choice`. Handles streaming text responses. *(Updated Logic)*

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
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_xml()` -> `str` - Gets schemas for all registered tools formatted as an XML string for system prompts. *(New)*
*   `src/tools/executor.py::ToolExecutor.execute_tool(agent_id: str, agent_sandbox_path: Path, tool_name: str, tool_args: Dict[str, Any])` -> `str` (Async Method) - Finds the tool by name, validates pre-parsed `tool_args` against schema, calls its `execute` method, returning the result as a string. Handles errors. *(Updated: Args pre-parsed, validation added)*

## **Tool Implementations (`src/tools/`)**

*   `src/tools/file_system.py::FileSystemTool` (Class inherits `BaseTool`) - Tool for file system operations within an agent's sandbox.
*   `src/tools/file_system.py::FileSystemTool.execute(...)` (Async) - (Overrides `BaseTool.execute`) Dispatches actions ('read', 'write', 'list') to helper methods.
*   `src/tools/file_system.py::FileSystemTool._resolve_and_validate_path(sandbox_path: Path, relative_file_path: str)` -> `Path | None` (Async Helper) - Securely resolves and validates a path within the agent's sandbox. **Crucial for security.**
*   `src/tools/file_system.py::FileSystemTool._read_file(sandbox_path: Path, filename: str)` -> `str` (Async Helper) - Reads file content from the sandbox.
*   `src/tools/file_system.py::FileSystemTool._write_file(sandbox_path: Path, filename: str, content: str)` -> `str` (Async Helper) - Writes file content to the sandbox.
*   `src/tools/file_system.py::FileSystemTool._list_directory(sandbox_path: Path, relative_dir: str)` -> `str` (Async Helper) - Lists directory contents within the sandbox.

## **Frontend Logic (`static/js/app.js`)**

*   *No changes in Phase 7.* Functions remain the same (scrollToBottom, connectWebSocket, sendMessage, addMessage, clearAgentResponsePlaceholder, clearAllAgentResponsePlaceholders, clearAgentStatusUI, updateAgentStatusUI, fetchAgentConfigurations, displayAgentConfigurations, handleFileSelect, displayFileInfo, clearSelectedFile).

---

## **Discontinued/Obsolete Functions/Methods**

*   `src/main.py::read_root()` - Removed in Phase 1.
*   `src/agents/core.py::Agent.initialize_openai_client(api_key: Optional[str] = None)` - Removed in Phase 5.5.
*   `src/agents/core.py::Agent.update_system_prompt_with_tools()` - Removed in Phase 5.5.
*   `src/agents/core.py::Agent.set_tool_executor(...)` - Obsolete in Phase 7 (Agent no longer uses executor directly). *(New)*
*   `src/agents/manager.py::AgentManager._process_message_for_agent(agent: Agent, message: str)` - Removed in Phase 5.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions()` -> `str` - Obsolete in Phase 5.5 (Replaced by XML version).
*   `src/tools/executor.py::ToolExecutor.parse_tool_call(...)` -> `Optional[Tuple[str, Dict]]` - Obsolete in Phase 7 (Agent handles XML parsing). *(Marked Obsolete)*

---
