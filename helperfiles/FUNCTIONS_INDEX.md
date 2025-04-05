<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages application startup and shutdown events. Calls `agent_manager.cleanup_providers()` on shutdown.
*   `src/main.py` (Script execution block) - Initializes FastAPI app with lifespan, loads .env, instantiates `AgentManager`, injects it into `WebSocketManager`, mounts static files, includes routers, runs Uvicorn server.

## **Configuration (`src/config/`)**

*   `src/config/settings.py::load_agent_config()` -> `List[Dict[str, Any]]` - Loads agent configurations from `config.yaml`. **(Note: May be superseded or used internally by `ConfigManager` in Phase 8)**.
*   `src/config/settings.py::Settings` (Class) - Holds application settings loaded from `.env` and `config.yaml`. Manages provider API keys/URLs and default agent parameters. **(Uses `ConfigManager` for loading in Phase 8)**.
*   `src/config/settings.py::Settings.__init__()` - Initializes settings, loads env vars and agent configs (via `ConfigManager`), calls `_check_required_keys`. *(Updated Description)*
*   `src/config/settings.py::Settings._check_required_keys()` - Validates if necessary API keys/URLs are set based on configured agents/providers.
*   `src/config/settings.py::Settings.get_provider_config(provider_name: str)` -> `Dict` - Gets default API key/URL/referer config for a specific provider ('openai', 'ollama', 'openrouter').
*   `src/config/settings.py::Settings.get_agent_config_by_id(agent_id: str)` -> `Optional[Dict[str, Any]]` - Retrieves a specific agent's nested 'config' dictionary by its ID **from the loaded configuration**.
*   `src/config/settings.py::settings` (Instance) - Singleton instance of the `Settings` class, accessible globally.

*   `src/config/config_manager.py::ConfigManager` (Class) - **(Phase 8)** Manages reading and writing of `config.yaml`. Provides safe methods for CRUD operations on agent configurations.
*   `src/config/config_manager.py::ConfigManager.__init__(config_path: Path)` - Initializes with the path to `config.yaml`.
*   `src/config/config_manager.py::ConfigManager.load_config()` -> `List[Dict[str, Any]]` - **(Phase 8)** Reads the YAML file and returns the list of agent configurations. Handles file not found and parsing errors.
*   `src/config/config_manager.py::ConfigManager.save_config(agents_data: List[Dict[str, Any]])` - **(Phase 8)** Writes the provided agent list back to the YAML file. Includes basic safety checks (like backup).
*   `src/config/config_manager.py::ConfigManager.add_agent(agent_config_entry: Dict[str, Any])` -> `bool` - **(Phase 8)** Adds a new agent configuration entry to the list and triggers save. Validates ID uniqueness. Returns success status.
*   `src/config/config_manager.py::ConfigManager.update_agent(agent_id: str, updated_config_data: Dict[str, Any])` -> `bool` - **(Phase 8)** Updates the 'config' part of an existing agent entry identified by `agent_id`. Triggers save. Returns success status.
*   `src/config/config_manager.py::ConfigManager.delete_agent(agent_id: str)` -> `bool` - **(Phase 8)** Removes an agent configuration entry by ID. Triggers save. Returns success status.
*   `src/config/config_manager.py::config_manager` (Instance) - **(Phase 8)** Singleton instance of the `ConfigManager`.

## **API Routes (`src/api/`)**

*   `src/api/http_routes.py::get_index_page(request: Request)` - Serves the main `index.html` page using Jinja2 templates.
*   `src/api/http_routes.py::AgentInfo` (Pydantic Model) - Model for basic agent info returned by `GET /api/config/agents`.
*   `src/api/http_routes.py::AgentConfigInput` (Pydantic Model) - **(Phase 8)** Model for validating agent configuration input (`POST`, `PUT`). Excludes potentially sensitive fields like direct API keys if exposed via API.
*   `src/api/http_routes.py::AgentConfigCreate` (Pydantic Model) - **(Phase 8)** Model specifically for creating new agents, requiring `agent_id`.
*   `src/api/http_routes.py::GeneralResponse` (Pydantic Model) - **(Phase 8)** Simple response model for success/failure messages.
*   `src/api/http_routes.py::get_agent_configurations()` -> `List[AgentInfo]` (Async) - API endpoint (`GET /api/config/agents`) to retrieve basic info for all configured agents from `settings`.
*   `src/api/http_routes.py::create_agent_configuration(agent_data: AgentConfigCreate)` -> `GeneralResponse` (Async) - **(Phase 8)** API endpoint (`POST /api/config/agents`) to add a new agent configuration using `ConfigManager`. Requires restart.
*   `src/api/http_routes.py::update_agent_configuration(agent_id: str, agent_data: AgentConfigInput)` -> `GeneralResponse` (Async) - **(Phase 8)** API endpoint (`PUT /api/config/agents/{agent_id}`) to update an existing agent's configuration using `ConfigManager`. Requires restart.
*   `src/api/http_routes.py::delete_agent_configuration(agent_id: str)` -> `GeneralResponse` (Async) - **(Phase 8)** API endpoint (`DELETE /api/config/agents/{agent_id}`) to remove an agent configuration using `ConfigManager`. Requires restart.

## **WebSocket Management (`src/api/`)**

*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level function to inject the `AgentManager` instance.
*   `src/api/websocket_manager.py::broadcast(message: str)` (Async) - Sends a message string to all active WebSocket connections. Handles disconnects.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` (Async) - Handler for the `/ws` endpoint. Manages connection lifecycle, receives messages, and asynchronously calls `agent_manager_instance.handle_user_message()`.

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents an individual LLM agent.
*   `src/agents/core.py::Agent.__init__(agent_config: Dict, llm_provider: BaseLLMProvider, manager: 'AgentManager', tool_descriptions_xml: str)` - Initializes agent with config, injected dependencies (provider, manager, tool descriptions), status, history, sandbox path. Compiles XML tool regex.
*   `src/agents/core.py::Agent::set_status(new_status: str, tool_info: Optional[Dict[str, str]] = None)` - Updates the agent's status (`self.status`), optionally stores tool info (`self.current_tool_info`), and asynchronously notifies the manager via `manager.push_agent_status_update()`.
*   `src/agents/core.py::Agent::set_manager(manager: 'AgentManager')` - Sets the `AgentManager` reference.
*   `src/agents/core.py::Agent::ensure_sandbox_exists()` -> `bool` - Creates the agent's sandbox directory if needed.
*   `src/agents/core.py::Agent::_parse_and_yield_xml_tool_call()` -> `Optional[Tuple[str, Dict[str, Any], int]]` - Helper method to check text buffer for complete XML tool calls, parse them using regex, validate against known tools, and return structured info.
*   `src/agents/core.py::Agent::process_message()` -> `AsyncGenerator[Dict, None]` - Core agent logic. Calls provider's `stream_completion` (without tools), buffers/yields `response_chunk`, parses buffer for XML tool calls using `_parse_and_yield_xml_tool_call`, yields `tool_requests` (with `raw_assistant_response`), yields `final_response`.
*   `src/agents/core.py::Agent::get_state()` -> `Dict[str, Any]` - Returns a dictionary with the agent's current state, including detailed status (`self.status`) and current tool info if applicable.
*   `src/agents/core.py::Agent::clear_history()` - Clears the agent's message history, keeping only the system prompt.

## **Agent Manager (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator for agents.
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager: Optional[Any] = None)` - Initializes the manager, instantiates `ToolExecutor`, gets tool descriptions, calls `_initialize_agents`.
*   `src/agents/manager.py::AgentManager._initialize_agents()` - Reads agent configurations (via `settings`), selects provider class, instantiates provider, instantiates agent with dependencies (provider, manager, `tool_descriptions_xml`), ensures sandbox, adds agent to `self.agents`.
*   `src/agents/manager.py::AgentManager::handle_user_message(message: str, client_id: Optional[str] = None)` (Async) - Entry point for user messages. Dispatches the task concurrently to all IDLE agents using `_handle_agent_generator`.
*   `src/agents/manager.py::AgentManager::_handle_agent_generator(agent: Agent, message: str)` (Async) - Manages the agent's `process_message` generator loop. Appends user message, handles yields (`response_chunk`, `tool_requests`, `final_response`). Appends assistant response (incl. XML) and tool results to history. Calls `_execute_single_tool` for `tool_requests`.
*   `src/agents/manager.py::AgentManager::_execute_single_tool(agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any])` -> `Optional[ToolResultDict]` (Async) - Sets agent status, executes a single tool via `ToolExecutor`, formats result/error, sets agent status back to `PROCESSING`.
*   `src/agents/manager.py::AgentManager::_failed_tool_result(call_id: Optional[str], tool_name: Optional[str])` -> `Optional[ToolResultDict]` (Async Helper) - Returns a formatted error result for failed tool dispatch.
*   `src/agents/manager.py::AgentManager::push_agent_status_update(agent_id: str)` (Async Helper) - Retrieves the full state of a specific agent via `agent.get_state()` and sends it to the UI.
*   `src/agents/manager.py::AgentManager::_send_to_ui(message_data: Dict[str, Any])` (Async Helper) - Sends JSON-serialized data to the UI.
*   `src/agents/manager.py::AgentManager::get_agent_status()` -> `Dict[str, Dict[str, Any]]` - Returns status dictionaries for all managed agents.
*   `src/agents/manager.py::AgentManager::cleanup_providers()` (Async) - Iterates through agents and calls cleanup methods on their providers.

## **LLM Providers Base (`src/llm_providers/`)**

*   `src/llm_providers/base.py::BaseLLMProvider` (ABC) - Abstract Base Class defining the interface for LLM providers.
*   `src/llm_providers/base.py::BaseLLMProvider.__init__(api_key, base_url, **kwargs)` (Abstract) - Provider initialization signature.
*   `src/llm_providers/base.py::BaseLLMProvider.stream_completion(messages, model, temperature, max_tokens, tools=None, tool_choice=None, **kwargs)` -> `AsyncGenerator[Dict, Optional[List[ToolResultDict]]]` (Abstract Async) - Defines the core interaction method. Must yield primarily `response_chunk`, `status`, `error`. Tool handling is external (via XML parsing).

## **LLM Providers Implementations (`src/llm_providers/`)**

*   `src/llm_providers/openai_provider.py::OpenAIProvider` (Class inherits `BaseLLMProvider`) - Implementation for OpenAI API.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.__init__(...)` - Initializes `openai.AsyncOpenAI` client.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.stream_completion(...)` (Async Generator) - Implements abstract method using `openai` lib *without* `tools`/`tool_choice`. Handles streaming text.
*   `src/llm_providers/ollama_provider.py::OllamaProvider` (Class inherits `BaseLLMProvider`) - Implementation for Ollama API.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.__init__(...)` - Initializes provider, stores base URL.
*   `src/llm_providers/ollama_provider.py::OllamaProvider._get_session()` -> `aiohttp.ClientSession` (Async) - Manages `aiohttp` session.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.close_session()` (Async) - Closes `aiohttp` session.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.stream_completion(...)` (Async Generator) - Implements abstract method using `aiohttp` to call Ollama *without* `tools`. Handles streaming text.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider` (Class inherits `BaseLLMProvider`) - Implementation for OpenRouter API.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.__init__(...)` - Initializes `openai.AsyncOpenAI` client for OpenRouter.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.stream_completion(...)` (Async Generator) - Implements abstract method using configured `openai` client *without* `tools`/`tool_choice`. Handles streaming text.

## **Tools Base (`src/tools/`)**

*   `src/tools/base.py::ToolParameter` (Pydantic Class) - Defines parameters for a tool (name, type, description, required).
*   `src/tools/base.py::BaseTool` (ABC) - Abstract base class for all tools. Defines `name`, `description`, `parameters`.
*   `src/tools/base.py::BaseTool.execute(agent_id: str, agent_sandbox_path: Path, **kwargs: Any)` -> `Any` (Abstract Async Method) - Core execution logic signature for a tool.
*   `src/tools/base.py::BaseTool.get_schema()` -> `Dict[str, Any]` - Returns tool description schema suitable for LLM consumption (e.g., XML generation).

## **Tool Executor (`src/tools/`)**

*   `src/tools/executor.py::ToolExecutor` (Class) - Manages and executes available tools.
*   `src/tools/executor.py::ToolExecutor.__init__()` - Initializes and registers tools.
*   `src/tools/executor.py::ToolExecutor._register_available_tools()` - Helper for registration.
*   `src/tools/executor.py::ToolExecutor.register_tool(tool_instance: BaseTool)` - Allows manual registration.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_xml()` -> `str` - Gets schemas formatted as an XML string for system prompts.
*   `src/tools/executor.py::ToolExecutor.execute_tool(agent_id: str, agent_sandbox_path: Path, tool_name: str, tool_args: Dict[str, Any])` -> `str` (Async Method) - Finds tool, validates pre-parsed `tool_args` against schema, calls `execute`, returns result string.

## **Tool Implementations (`src/tools/`)**

*   `src/tools/file_system.py::FileSystemTool` (Class inherits `BaseTool`) - Tool for file system operations within sandbox.
*   `src/tools/file_system.py::FileSystemTool.execute(...)` (Async) - Dispatches actions ('read', 'write', 'list').
*   `src/tools/file_system.py::FileSystemTool._resolve_and_validate_path(sandbox_path: Path, relative_file_path: str)` -> `Path | None` (Async Helper) - Securely resolves path within sandbox.
*   `src/tools/file_system.py::FileSystemTool._read_file(sandbox_path: Path, filename: str)` -> `str` (Async Helper) - Reads file content.
*   `src/tools/file_system.py::FileSystemTool._write_file(sandbox_path: Path, filename: str, content: str)` -> `str` (Async Helper) - Writes file content.
*   `src/tools/file_system.py::FileSystemTool._list_directory(sandbox_path: Path, relative_dir: str)` -> `str` (Async Helper) - Lists directory contents.

## **Frontend Logic (`static/js/app.js`)**

*   *(Phase 8 Additions)*
*   `static/js/app.js::displayAgentConfigurations(configs)` - *(Updated in Phase 8)* Adds Edit/Delete buttons.
*   `static/js/app.js::openAddAgentModal()` - **(Phase 8)** Shows the modal for adding a new agent configuration.
*   `static/js/app.js::openEditAgentModal(agentId)` - **(Phase 8)** Shows the modal for editing, populating with existing data.
*   `static/js/app.js::handleSaveAgent(event, agentId)` (Async) - **(Phase 8)** Handles Add/Edit form submission, calls respective API endpoint.
*   `static/js/app.js::handleDeleteAgent(agentId)` (Async) - **(Phase 8)** Handles delete confirmation and API call.
*   `static/js/app.js::closeModal(modalId)` - **(Phase 8)** Helper to close modal dialogs.
*   *(Existing Functions)* scrollToBottom, connectWebSocket, sendMessage, addMessage, clearAgentResponsePlaceholder, clearAllAgentResponsePlaceholders, clearAgentStatusUI, updateAgentStatusUI, fetchAgentConfigurations, handleFileSelect, displayFileInfo, clearSelectedFile.

---

## **Discontinued/Obsolete Functions/Methods**

*   `src/main.py::read_root()` - Removed in Phase 1.
*   `src/agents/core.py::Agent.initialize_openai_client(api_key: Optional[str] = None)` - Removed in Phase 5.5.
*   `src/agents/core.py::Agent.update_system_prompt_with_tools()` - Removed in Phase 5.5.
*   `src/agents/core.py::Agent.set_tool_executor(...)` - Obsolete in Phase 7.
*   `src/agents/manager.py::AgentManager._process_message_for_agent(agent: Agent, message: str)` - Removed in Phase 5.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions()` -> `str` - Obsolete in Phase 5.5 (Replaced by XML version).
*   `src/tools/executor.py::ToolExecutor.parse_tool_call(...)` -> `Optional[Tuple[str, Dict]]` - Obsolete in Phase 7.

---
