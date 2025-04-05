<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages application startup and shutdown events. Calls `agent_manager.cleanup_providers()` on shutdown.
*   `src/main.py` (Script execution block) - Initializes FastAPI app with lifespan, loads .env, instantiates `AgentManager`, injects it into `WebSocketManager`, mounts static files, includes routers, runs Uvicorn server.

## **Configuration (`src/config/`)**

*   `src/config/config_manager.py::ConfigManager` (Class) - Manages reading and writing of `config.yaml`. Provides async-safe methods for CRUD operations using `asyncio.Lock` and atomic writes.
*   `src/config/config_manager.py::ConfigManager.__init__(config_path: Path)` - Initializes with the path to `config.yaml`. Performs initial synchronous load.
*   `src/config/config_manager.py::ConfigManager._load_config_sync()` - **(Internal)** Synchronous load method used during `__init__`.
*   `src/config/config_manager.py::ConfigManager.load_config()` -> `List[Dict[str, Any]]` (Async) - Asynchronously reads the YAML file safely. Returns a deep copy.
*   `src/config/config_manager.py::ConfigManager._backup_config()` -> `bool` (Async Internal) - Backs up the current config file before saving. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager._save_config_safe()` -> `bool` (Async Internal) - Safely writes internal state to config file using a temporary file and atomic replace. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager.get_config()` -> `List[Dict[str, Any]]` (Async) - Returns a deep copy of the currently loaded agent configuration list. Async-safe.
*   `src/config/config_manager.py::ConfigManager.get_config_sync()` -> `List[Dict[str, Any]]` - **(Synchronous)** Returns a deep copy of the agent configuration list loaded during `__init__`. For use in synchronous contexts like `Settings` init.
*   `src/config/config_manager.py::ConfigManager._find_agent_index_unsafe(agent_id: str)` -> `Optional[int]` - **(Internal)** Finds agent index. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager.add_agent(agent_config_entry: Dict[str, Any])` -> `bool` (Async) - Adds a new agent configuration entry and triggers safe save. Validates ID uniqueness. Async-safe.
*   `src/config/config_manager.py::ConfigManager.update_agent(agent_id: str, updated_config_data: Dict[str, Any])` -> `bool` (Async) - Updates an existing agent's 'config' and triggers safe save. Async-safe.
*   `src/config/config_manager.py::ConfigManager.delete_agent(agent_id: str)` -> `bool` (Async) - Removes an agent configuration entry by ID and triggers safe save. Async-safe.
*   `src/config/config_manager.py::config_manager` (Instance) - Singleton instance of the `ConfigManager`.

*   `src/config/settings.py::Settings` (Class) - Holds application settings loaded from `.env` and `config.yaml`. Manages provider API keys/URLs and default agent parameters. Uses `ConfigManager` for loading config synchronously at startup.
*   `src/config/settings.py::Settings.__init__()` - Initializes settings, loads env vars and agent configs (via `ConfigManager.get_config_sync()`), calls `_check_required_keys`. *(Updated)*
*   `src/config/settings.py::Settings._check_required_keys()` - Validates if necessary API keys/URLs are set based on configured agents/providers.
*   `src/config/settings.py::Settings.get_provider_config(provider_name: str)` -> `Dict` - Gets default API key/URL/referer config for a specific provider ('openai', 'ollama', 'openrouter').
*   `src/config/settings.py::Settings.get_agent_config_by_id(agent_id: str)` -> `Optional[Dict[str, Any]]` - Retrieves a specific agent's nested 'config' dictionary by its ID from the **configuration loaded at startup**.
*   `src/config/settings.py::settings` (Instance) - Singleton instance of the `Settings` class, accessible globally.

## **API Routes (`src/api/`)**

*   `src/api/http_routes.py::AgentInfo` (Pydantic Model) - Model for basic agent info returned by `GET /api/config/agents`.
*   `src/api/http_routes.py::AgentConfigInput` (Pydantic Model) - Model for validating the 'config' part of agent configuration input (`PUT`).
*   `src/api/http_routes.py::AgentConfigCreate` (Pydantic Model) - Model for validating agent creation input (`POST`), requires `agent_id` and `config`.
*   `src/api/http_routes.py::GeneralResponse` (Pydantic Model) - Simple response model for success/failure messages from CRUD operations.
*   `src/api/http_routes.py::get_index_page(request: Request)` - Serves the main `index.html` page using Jinja2 templates.
*   `src/api/http_routes.py::get_agent_configurations()` -> `List[AgentInfo]` (Async) - API endpoint (`GET /api/config/agents`) to retrieve basic info for all agents currently in the config file using `ConfigManager.get_config()`. *(Updated)*
*   `src/api/http_routes.py::create_agent_configuration(agent_data: AgentConfigCreate)` -> `GeneralResponse` (Async) - API endpoint (`POST /api/config/agents`) to add a new agent configuration using `ConfigManager.add_agent()`. Requires restart. *(Updated)*
*   `src/api/http_routes.py::update_agent_configuration(agent_id: str, agent_data: AgentConfigInput)` -> `GeneralResponse` (Async) - API endpoint (`PUT /api/config/agents/{agent_id}`) to update an existing agent's configuration using `ConfigManager.update_agent()`. Requires restart. *(Updated)*
*   `src/api/http_routes.py::delete_agent_configuration(agent_id: str)` -> `GeneralResponse` (Async) - API endpoint (`DELETE /api/config/agents/{agent_id}`) to remove an agent configuration using `ConfigManager.delete_agent()`. Requires restart. *(Updated)*

## **WebSocket Management (`src/api/`)**

*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level function to inject the `AgentManager` instance.
*   `src/api/websocket_manager.py::broadcast(message: str)` (Async) - Sends a message string to all active WebSocket connections. Handles disconnects.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` (Async) - Handler for the `/ws` endpoint. Manages connection lifecycle, receives messages, and asynchronously calls `agent_manager_instance.handle_user_message()`.

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents an individual LLM agent.
*   `src/agents/core.py::Agent.__init__(agent_config: Dict, llm_provider: BaseLLMProvider, manager: 'AgentManager', tool_descriptions_xml: str)` - Initializes agent with config, injected dependencies, status, history, sandbox path. Compiles XML tool regex patterns (raw and fenced). *(Updated)*
*   `src/agents/core.py::Agent::set_status(new_status: str, tool_info: Optional[Dict[str, str]] = None)` - Updates the agent's status and notifies the manager.
*   `src/agents/core.py::Agent::set_manager(manager: 'AgentManager')` - Sets the `AgentManager` reference.
*   `src/agents/core.py::Agent::ensure_sandbox_exists()` -> `bool` - Creates the agent's sandbox directory if needed.
*   `src/agents/core.py::Agent::_find_and_parse_last_tool_call()` -> `Optional[Tuple[str, Dict[str, Any], Tuple[int, int]]]` - **(Internal Updated)** Finds the *last* valid tool call (raw or fenced) in the buffer, parses it, returns name, args, and match span.
*   `src/agents/core.py::Agent::process_message()` -> `AsyncGenerator[Dict, None]` - Core agent logic. Calls provider's `stream_completion`, yields `response_chunk`s, parses final buffer for tool calls using `_find_and_parse_last_tool_call`, yields `tool_requests` or `final_response`. *(Updated Logic)*
*   `src/agents/core.py::Agent::get_state()` -> `Dict[str, Any]` - Returns a dictionary with the agent's current state.
*   `src/agents/core.py::Agent::clear_history()` - Clears the agent's message history, keeping the system prompt.

## **Agent Manager (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator for agents.
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager: Optional[Any] = None)` - Initializes the manager, instantiates `ToolExecutor`, gets tool descriptions, calls `_initialize_agents`.
*   `src/agents/manager.py::AgentManager._initialize_agents()` - Reads agent configurations (via `settings`), selects provider class, instantiates provider, instantiates agent with dependencies.
*   `src/agents/manager.py::AgentManager::handle_user_message(message: str, client_id: Optional[str] = None)` (Async) - Entry point for user messages. Dispatches the task concurrently to all IDLE agents using `_handle_agent_generator`.
*   `src/agents/manager.py::AgentManager::_handle_agent_generator(agent: Agent, message: str)` (Async) - Manages the agent's `process_message` generator loop. Appends user message, handles yields (`response_chunk`, `tool_requests`, `final_response`). Appends assistant response (incl. XML) and tool results to history. Calls `_execute_single_tool`.
*   `src/agents/manager.py::AgentManager::_execute_single_tool(agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any])` -> `Optional[ToolResultDict]` (Async) - Sets agent status, executes a single tool via `ToolExecutor`, formats result/error, sets agent status back to `PROCESSING`.
*   `src/agents/manager.py::AgentManager::_failed_tool_result(call_id: Optional[str], tool_name: Optional[str])` -> `Optional[ToolResultDict]` (Async Helper) - Returns a formatted error result for failed tool dispatch.
*   `src/agents/manager.py::AgentManager::push_agent_status_update(agent_id: str)` (Async Helper) - Retrieves the full state of a specific agent and sends it to the UI.
*   `src/agents/manager.py::AgentManager::_send_to_ui(message_data: Dict[str, Any])` (Async Helper) - Sends JSON-serialized data to the UI.
*   `src/agents/manager.py::AgentManager::get_agent_status()` -> `Dict[str, Dict[str, Any]]` - Returns status dictionaries for all managed agents.
*   `src/agents/manager.py::AgentManager::cleanup_providers()` (Async) - Iterates through agents and calls cleanup methods on their providers.

## **LLM Providers Base (`src/llm_providers/`)**

*   `src/llm_providers/base.py::BaseLLMProvider` (ABC) - Abstract Base Class defining the interface for LLM providers.
*   `src/llm_providers/base.py::BaseLLMProvider.__init__(api_key, base_url, **kwargs)` (Abstract) - Provider initialization signature.
*   `src/llm_providers/base.py::BaseLLMProvider.stream_completion(messages, model, temperature, max_tokens, tools=None, tool_choice=None, **kwargs)` -> `AsyncGenerator[Dict, Optional[List[ToolResultDict]]]` (Abstract Async) - Defines the core interaction method. Must yield primarily `response_chunk`, `status`, `error`. Tool handling is external.

## **LLM Providers Implementations (`src/llm_providers/`)**

*   *(No changes in Phase 8)*

## **Tools Base (`src/tools/`)**

*   `src/tools/base.py::ToolParameter` (Pydantic Class) - Defines parameters for a tool (name, type, description, required).
*   `src/tools/base.py::BaseTool` (ABC) - Abstract base class for all tools. Defines `name`, `description`, `parameters`.
*   `src/tools/base.py::BaseTool.execute(agent_id: str, agent_sandbox_path: Path, **kwargs: Any)` -> `Any` (Abstract Async Method) - Core execution logic signature.
*   `src/tools/base.py::BaseTool.get_schema()` -> `Dict[str, Any]` - Returns tool description schema.

## **Tool Executor (`src/tools/`)**

*   `src/tools/executor.py::ToolExecutor` (Class) - Manages and executes available tools.
*   `src/tools/executor.py::ToolExecutor.__init__()` - Initializes and registers tools.
*   `src/tools/executor.py::ToolExecutor._register_available_tools()` - Helper for registration.
*   `src/tools/executor.py::ToolExecutor.register_tool(tool_instance: BaseTool)` - Allows manual registration.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_xml()` -> `str` - Gets schemas formatted as an XML string for system prompts.
*   `src/tools/executor.py::ToolExecutor.execute_tool(agent_id: str, agent_sandbox_path: Path, tool_name: str, tool_args: Dict[str, Any])` -> `str` (Async Method) - Finds tool, validates pre-parsed `tool_args`, calls `execute`, returns result string.

## **Tool Implementations (`src/tools/`)**

*   *(No changes in Phase 8)*

## **Frontend Logic (`static/js/app.js`)**

*   `static/js/app.js::displayAgentConfigurations(configs)` - *(Updated in Phase 8)* Adds Edit/Delete buttons.
*   `static/js/app.js::openAddAgentModal()` - Shows the modal for adding a new agent configuration.
*   `static/js/app.js::openEditAgentModal(agentId)` (Async) - Shows the modal for editing, attempts to prefill (partially). *(Updated)*
*   `static/js/app.js::handleSaveAgent(event)` (Async) - Handles Add/Edit form submission, calls respective API endpoint, adds basic validation. *(Updated)*
*   `static/js/app.js::handleDeleteAgent(agentId)` (Async) - Handles delete confirmation and API call.
*   `static/js/app.js::closeModal(modalId)` - Helper to close modal dialogs.
*   `static/js/app.js` - Added event listener for refresh button (`refreshConfigButton`) to reload the page.
*   *(Existing Functions)* scrollToBottom, connectWebSocket, sendMessage, addMessage, clearAgentResponsePlaceholder, clearAllAgentResponsePlaceholders, clearAgentStatusUI, updateAgentStatusUI, fetchAgentConfigurations, handleFileSelect, displayFileInfo, clearSelectedFile.

---

## **Discontinued/Obsolete Functions/Methods**

*   `src/main.py::read_root()` - Removed in Phase 1.
*   `src/agents/core.py::Agent.initialize_openai_client(api_key: Optional[str] = None)` - Removed in Phase 5.5.
*   `src/agents/core.py::Agent.update_system_prompt_with_tools()` - Removed in Phase 5.5.
*   `src/agents/core.py::Agent.set_tool_executor(...)` - Obsolete in Phase 7.
*   `src/agents/core.py::Agent::_parse_and_yield_xml_tool_call()` - Obsolete in Phase 8 (Replaced by `_find_and_parse_last_tool_call`). *(Marked Obsolete)*
*   `src/config/settings.py::load_agent_config()` - Obsolete in Phase 8 (Superseded by `ConfigManager`). *(Marked Obsolete)*
*   `src/agents/manager.py::AgentManager._process_message_for_agent(agent: Agent, message: str)` - Removed in Phase 5.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions()` -> `str` - Obsolete in Phase 5.5.
*   `src/tools/executor.py::ToolExecutor.parse_tool_call(...)` -> `Optional[Tuple[str, Dict]]` - Obsolete in Phase 7.

---
