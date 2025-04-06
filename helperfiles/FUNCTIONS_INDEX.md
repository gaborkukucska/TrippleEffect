<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages application startup and shutdown events. Initializes bootstrap agents via `agent_manager.initialize_bootstrap_agents()` on startup and calls `agent_manager.cleanup_providers()` on shutdown.
*   `src/main.py` (Script execution block) - Initializes FastAPI app with lifespan, loads .env, instantiates `AgentManager` (sync part), injects it into `WebSocketManager`, mounts static files, includes routers, runs Uvicorn server.

## **Configuration (`src/config/`)**

*   `src/config/config_manager.py::ConfigManager` (Class) - Manages reading and writing of `config.yaml` (agents and teams). Provides async-safe methods for CRUD using `asyncio.Lock` and atomic writes with backups.
*   `src/config/config_manager.py::ConfigManager.__init__(config_path: Path)` - Initializes with the path to `config.yaml`. Performs initial synchronous load of the full config.
*   `src/config/config_manager.py::ConfigManager._load_config_sync()` - **(Internal)** Synchronous load method used during `__init__`. Loads full config (agents/teams).
*   `src/config/config_manager.py::ConfigManager.load_config()` -> `Dict[str, Any]` (Async) - Asynchronously reads the full YAML file safely using `asyncio.to_thread`. Returns a deep copy of the full config. Async-safe.
*   `src/config/config_manager.py::ConfigManager._backup_config()` -> `bool` (Async Internal) - Backs up the current config file before saving using `asyncio.to_thread`. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager._save_config_safe()` -> `bool` (Async Internal) - Safely writes internal full config state (`_config_data`) to file using a temporary file and atomic replace. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager.get_config()` -> `List[Dict[str, Any]]` (Async) - Returns a deep copy of the currently loaded agent configuration list ('agents'). Backward compatible API. Async-safe.
*   `src/config/config_manager.py::ConfigManager.get_teams()` -> `Dict[str, List[str]]` (Async) - Returns a deep copy of the currently loaded teams configuration. Async-safe.
*   `src/config/config_manager.py::ConfigManager.get_full_config()` -> `Dict[str, Any]` (Async) - Returns a deep copy of the entire loaded configuration data. Async-safe.
*   `src/config/config_manager.py::ConfigManager.get_config_data_sync()` -> `Dict[str, Any]` - **(Synchronous)** Returns a deep copy of the full config data loaded during `__init__`. For use in synchronous startup contexts.
*   `src/config/config_manager.py::ConfigManager._find_agent_index_unsafe(agent_id: str)` -> `Optional[int]` - **(Internal)** Finds agent index within 'agents' list. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager.add_agent(agent_config_entry: Dict[str, Any])` -> `bool` (Async) - Adds a new agent entry to 'agents' list and saves the *full* config. Validates ID uniqueness. Async-safe.
*   `src/config/config_manager.py::ConfigManager.update_agent(agent_id: str, updated_config_data: Dict[str, Any])` -> `bool` (Async) - Updates an existing agent's 'config' dict in 'agents' list and saves the *full* config. Async-safe.
*   `src/config/config_manager.py::ConfigManager.delete_agent(agent_id: str)` -> `bool` (Async) - Removes an agent entry from 'agents' list, removes agent from 'teams', and saves the *full* config. Async-safe.
*   `src/config/config_manager.py::config_manager` (Instance) - Singleton instance of the `ConfigManager`.

*   `src/config/settings.py::Settings` (Class) - Holds application settings loaded from `.env` and initial `config.yaml`. Manages provider keys/URLs, defaults, initial agent/team configs. Uses `ConfigManager.get_config_data_sync()`.
*   `src/config/settings.py::Settings.__init__()` - Initializes settings, loads env vars and initial agent/team configs (via `ConfigManager.get_config_data_sync()`), calls `_ensure_projects_dir` and `_check_required_keys`.
*   `src/config/settings.py::Settings._ensure_projects_dir()` - Creates the base directory for project/session data.
*   `src/config/settings.py::Settings._check_required_keys()` - Validates if necessary API keys/URLs are set based on loaded agent providers.
*   `src/config/settings.py::Settings.get_provider_config(provider_name: str)` -> `Dict` - Gets default API key/URL/referer config for a specific provider.
*   `src/config/settings.py::Settings.get_agent_config_by_id(agent_id: str)` -> `Optional[Dict[str, Any]]` - Retrieves a specific agent's nested 'config' dictionary by its ID from the **configuration loaded at startup (not live)**.
*   `src/config/settings.py::settings` (Instance) - Singleton instance of the `Settings` class, accessible globally.

## **API Routes (`src/api/`)**

*   **Pydantic Models (`src/api/http_routes.py`)**:
    *   `AgentInfo` - Basic agent info for UI list.
    *   `GeneralResponse` - Simple success/error message response.
    *   `SessionInfo` - Project and session name.
    *   `ProjectInfo` - Project name and list of sessions.
    *   `SaveSessionInput` - Optional session name for saving.
    *   `AgentConfigInput` - Validates the 'config' part of agent configuration for PUT.
    *   `AgentConfigCreate` - Validates agent creation input for POST (requires `agent_id`, `config`).
*   `src/api/http_routes.py::get_agent_manager_dependency()` -> `'AgentManager'` - FastAPI dependency function to inject `AgentManager`.
*   `src/api/http_routes.py::get_index_page(request: Request)` (Async) - Serves the main `index.html` page using Jinja2 templates.
*   `src/api/http_routes.py::get_agent_configurations()` -> `List[AgentInfo]` (Async) - API endpoint (`GET /api/config/agents`) to retrieve basic info for agents listed in `config.yaml` using `ConfigManager.get_config()`.
*   `src/api/http_routes.py::create_agent_configuration(agent_data: AgentConfigCreate)` -> `GeneralResponse` (Async) - API endpoint (`POST /api/config/agents`) to add a new agent configuration to `config.yaml` using `ConfigManager.add_agent()`. Requires restart.
*   `src/api/http_routes.py::update_agent_configuration(agent_id: str, agent_config_data: AgentConfigInput)` -> `GeneralResponse` (Async) - API endpoint (`PUT /api/config/agents/{agent_id}`) to update an existing agent's configuration in `config.yaml` using `ConfigManager.update_agent()`. Requires restart.
*   `src/api/http_routes.py::delete_agent_configuration(agent_id: str)` -> `GeneralResponse` (Async) - API endpoint (`DELETE /api/config/agents/{agent_id}`) to remove an agent configuration from `config.yaml` using `ConfigManager.delete_agent()`. Requires restart.
*   `src/api/http_routes.py::list_projects()` -> `List[ProjectInfo]` (Async) - API endpoint (`GET /api/projects`) to list project directories.
*   `src/api/http_routes.py::list_sessions(project_name: str)` -> `List[SessionInfo]` (Async) - API endpoint (`GET /api/projects/{project_name}/sessions`) to list session directories within a project.
*   `src/api/http_routes.py::save_current_session(project_name: str, session_input: Optional[SaveSessionInput], manager: 'AgentManager')` -> `GeneralResponse` (Async) - API endpoint (`POST /api/projects/{project_name}/sessions`) to save the current dynamic agent state using `AgentManager.save_session()`.
*   `src/api/http_routes.py::load_specific_session(project_name: str, session_name: str, manager: 'AgentManager')` -> `GeneralResponse` (Async) - API endpoint (`POST /api/projects/{project_name}/sessions/{session_name}/load`) to load a saved session state using `AgentManager.load_session()`.

## **WebSocket Management (`src/api/`)**

*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level function to inject the `AgentManager` instance.
*   `src/api/websocket_manager.py::broadcast(message: str)` (Async) - Sends a message string to all active WebSocket connections. Handles disconnects.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` (Async) - Handler for the `/ws` endpoint. Manages connection lifecycle, receives messages, and asynchronously calls `agent_manager_instance.handle_user_message()` using `asyncio.create_task`.

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents an individual LLM agent capable of processing tasks, parsing XML tool calls, and managing state/sandbox.
*   `src/agents/core.py::Agent.__init__(agent_config: Dict, llm_provider: BaseLLMProvider, manager: 'AgentManager', tool_descriptions_xml: str)` - Initializes agent with config, injected dependencies (provider, manager), status, history, sandbox path. Compiles XML tool regex patterns.
*   `src/agents/core.py::Agent.set_status(new_status: str, tool_info: Optional[Dict[str, str]] = None)` - Updates the agent's status and notifies the manager via `asyncio.create_task`.
*   `src/agents/core.py::Agent.set_manager(manager: 'AgentManager')` - Sets the `AgentManager` reference.
*   `src/agents/core.py::Agent.set_tool_executor(tool_executor: Any)` - **(Discouraged)** Sets ToolExecutor reference, but logs warning as Agent no longer uses it directly.
*   `src/agents/core.py::Agent.ensure_sandbox_exists()` -> `bool` - Creates the agent's sandbox directory if needed.
*   `src/agents/core.py::Agent._find_and_parse_last_tool_call()` -> `Optional[Tuple[str, Dict[str, Any], Tuple[int, int]]]` - **(Internal Updated)** Finds the *last* valid tool call (raw or fenced) in the buffer, parses it, returns name, args, and match span. Uses `html.unescape` and regex.
*   `src/agents/core.py::Agent.process_message()` -> `AsyncGenerator[Dict, None]` (Async) - Core agent logic loop. Calls provider's `stream_completion`, yields `response_chunk`s, parses final buffer for tool calls using `_find_and_parse_last_tool_call`, yields `tool_requests` (with raw response) or `final_response`. Receives `None` via `asend`. Manager handles history appends.
*   `src/agents/core.py::Agent.get_state()` -> `Dict[str, Any]` - Returns a dictionary with the agent's current state (ID, persona, status, provider, etc.).
*   `src/agents/core.py::Agent.clear_history()` - Clears the agent's message history, keeping the system prompt.

## **Agent Manager (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator for bootstrap and dynamic agents/teams. Routes messages, handles tool execution signals, manages session persistence.
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager: Optional[Any] = None)` - **(Synchronous)** Initializes manager, creates `ToolExecutor`, gets tool descriptions XML, ensures projects dir. Defers bootstrap agent loading.
*   `src/agents/manager.py::AgentManager._ensure_projects_dir()` - **(Internal)** Creates the base directory for storing project/session data.
*   `src/agents/manager.py::AgentManager.initialize_bootstrap_agents()` (Async) - Loads bootstrap agents from `settings` during application startup. Calls `_create_agent_internal`.
*   `src/agents/manager.py::AgentManager._create_agent_internal(agent_id_requested: Optional[str], agent_config_data: Dict[str, Any], is_bootstrap: bool, team_id: Optional[str])` -> `Tuple[bool, str, Optional[str]]` (Async Internal) - Core logic to instantiate agent, provider, ensure sandbox, add to state, and optionally add to team.
*   `src/agents/manager.py::AgentManager.create_agent_instance(agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str, team_id: Optional[str], temperature: Optional[float], **kwargs)` -> `Tuple[bool, str, Optional[str]]` (Async) - Public method for dynamic agent creation (via `ManageTeamTool`). Calls `_create_agent_internal`. Sends `agent_added` WS event.
*   `src/agents/manager.py::AgentManager.handle_user_message(message: str, client_id: Optional[str] = None)` (Async) - Entry point for user messages. Routes message exclusively to the Admin AI (`BOOTSTRAP_AGENT_ID`) and starts its processing cycle (`_handle_agent_generator`).
*   `src/agents/manager.py::AgentManager._handle_agent_generator(agent: Agent)` (Async) - Manages an agent's `process_message` generator loop. Handles yielded events (`response_chunk`, `tool_requests`, `final_response`, `status`, `error`). Appends assistant response (including XML) and raw tool results to agent history. Calls `_execute_single_tool`. Processes `ManageTeamTool`/`SendMessageTool` results post-execution by calling internal manager methods or routing messages. Appends manager feedback to agent history. Reactivates agent if needed.
*   `src/agents/manager.py::AgentManager._handle_manage_team_action(action: Optional[str], params: Dict[str, Any])` -> `Tuple[bool, str, Optional[Any]]` (Async Internal) - Dispatches validated `ManageTeamTool` actions to the corresponding `AgentManager` methods (e.g., `create_agent_instance`, `delete_agent_instance`, `create_new_team`, etc.). Returns structured result for feedback message.
*   `src/agents/manager.py::AgentManager._generate_unique_agent_id(prefix="agent")` -> `str` - **(Internal)** Generates a unique agent ID.
*   `src/agents/manager.py::AgentManager.delete_agent_instance(agent_id: str)` -> `Tuple[bool, str]` (Async) - Deletes a dynamic agent instance, removes from team map, closes provider session. Sends `agent_deleted` WS event.
*   `src/agents/manager.py::AgentManager.create_new_team(team_id: str)` -> `Tuple[bool, str]` (Async) - Creates a new, empty team in the team map. Sends `team_created` WS event.
*   `src/agents/manager.py::AgentManager.delete_existing_team(team_id: str)` -> `Tuple[bool, str]` (Async) - Deletes an existing empty team from the team map. Sends `team_deleted` WS event.
*   `src/agents/manager.py::AgentManager.add_agent_to_team(agent_id: str, team_id: str)` -> `Tuple[bool, str]` (Async) - Adds an agent to a team (creates team if needed), updates mappings. Sends `agent_moved_team` WS event. Pushes agent status update.
*   `src/agents/manager.py::AgentManager.remove_agent_from_team(agent_id: str, team_id: str)` -> `Tuple[bool, str]` (Async) - Removes an agent from a team, updates mappings. Sends `agent_moved_team` WS event. Pushes agent status update.
*   `src/agents/manager.py::AgentManager.get_agent_info_list()` -> `List[Dict[str, Any]]` (Async) - Returns a list of dictionaries containing basic info about all current agents (ID, persona, status, team, etc.).
*   `src/agents/manager.py::AgentManager.get_team_info_dict()` -> `Dict[str, List[str]]` (Async) - Returns a copy of the current team structure dictionary.
*   `src/agents/manager.py::AgentManager._route_and_activate_agent_message(sender_id: str, target_id: str, message_content: str)` -> `Optional[asyncio.Task]` (Async Internal) - Routes a message from `SendMessageTool`, appends to target history, activates target agent if idle. Checks team membership (allows Admin AI override).
*   `src/agents/manager.py::AgentManager._execute_single_tool(agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any])` -> `Optional[Dict]` (Async) - Sets agent status to 'executing_tool', calls `ToolExecutor.execute_tool`, sets status back to 'processing'. Returns tool result dictionary (including `_raw_result` for manager processing).
*   `src/agents/manager.py::AgentManager._failed_tool_result(call_id: Optional[str], tool_name: Optional[str])` -> `Optional[ToolResultDict]` (Async Helper) - Returns a formatted error result dictionary for failed tool dispatch.
*   `src/agents/manager.py::AgentManager.push_agent_status_update(agent_id: str)` (Async Helper) - Retrieves the full state of a specific agent and sends it to the UI via WebSocket.
*   `src/agents/manager.py::AgentManager.send_to_ui(message_data: Dict[str, Any])` (Async Helper) - Sends JSON-serialized data to the UI via the `broadcast` function. Handles serialization errors.
*   `src/agents/manager.py::AgentManager.get_agent_status()` -> `Dict[str, Dict[str, Any]]` - **(Synchronous)** Returns a snapshot dictionary of current statuses for all managed agents.
*   `src/agents/manager.py::AgentManager.save_session(project_name: str, session_name: Optional[str] = None)` -> `Tuple[bool, str]` (Async) - Saves current state (dynamic agent configs, histories, teams) to a JSON file within the project/session structure.
*   `src/agents/manager.py::AgentManager.load_session(project_name: str, session_name: str)` -> `Tuple[bool, str]` (Async) - Loads state from a JSON file, clearing current dynamic agents/teams, recreating agents, and restoring histories.
*   `src/agents/manager.py::AgentManager.cleanup_providers()` (Async) - Iterates through unique active agent providers and calls `_close_provider_safe` on them.
*   `src/agents/manager.py::AgentManager._close_provider_safe(provider: BaseLLMProvider)` (Async Internal) - Safely attempts to call the `close_session` method on a provider instance.

## **LLM Providers Base (`src/llm_providers/`)**

*   `src/llm_providers/base.py::BaseLLMProvider` (ABC) - Abstract Base Class defining the interface for LLM providers.
*   `src/llm_providers/base.py::BaseLLMProvider.__init__(api_key, base_url, **kwargs)` (Abstract) - Provider initialization signature.
*   `src/llm_providers/base.py::BaseLLMProvider.stream_completion(messages, model, temperature, max_tokens, tools=None, tool_choice=None, **kwargs)` -> `AsyncGenerator[Dict, Optional[List[ToolResultDict]]]` (Abstract Async) - Defines the core interaction method. Expected to yield events like `response_chunk`, `status`, `error`. Receiving tool results via `asend` is less relevant with XML approach but kept in signature.
*   `src/llm_providers/base.py::BaseLLMProvider.close_session()` (Async Abstract) - Optional abstract method for cleanup (e.g., closing network sessions).
*   `src/llm_providers/base.py::BaseLLMProvider.__repr__()` - Basic instance representation.

## **LLM Providers Implementations (`src/llm_providers/`)**

*   **Ollama (`ollama_provider.py`)**
    *   `src/llm_providers/ollama_provider.py::OllamaProvider(BaseLLMProvider)` - Implements provider for local Ollama.
    *   `src/llm_providers/ollama_provider.py::OllamaProvider.__init__(api_key, base_url, **kwargs)` - Initializes provider, sets base URL, ignores api_key.
    *   `src/llm_providers/ollama_provider.py::OllamaProvider._get_session()` -> `aiohttp.ClientSession` (Async Internal) - Manages `aiohttp` session.
    *   `src/llm_providers/ollama_provider.py::OllamaProvider.close_session()` (Async) - Closes the `aiohttp` session.
    *   `src/llm_providers/ollama_provider.py::OllamaProvider.stream_completion(...)` -> `AsyncGenerator` (Async) - Makes POST request to `/api/chat` with retry logic. Yields `response_chunk`, `status`, `error` based on streaming JSON response. Ignores `tools`/`tool_choice`.
    *   `src/llm_providers/ollama_provider.py::OllamaProvider.__repr__()` - Instance representation.
    *   `src/llm_providers/ollama_provider.py::OllamaProvider.__aenter__()` (Async) - Returns self after ensuring session.
    *   `src/llm_providers/ollama_provider.py::OllamaProvider.__aexit__(...)` (Async) - Closes session on exit.
*   **OpenAI (`openai_provider.py`)**
    *   `src/llm_providers/openai_provider.py::OpenAIProvider(BaseLLMProvider)` - Implements provider for OpenAI API.
    *   `src/llm_providers/openai_provider.py::OpenAIProvider.__init__(api_key, base_url, **kwargs)` - Initializes `openai.AsyncOpenAI` client with `max_retries=0`.
    *   `src/llm_providers/openai_provider.py::OpenAIProvider.stream_completion(...)` -> `AsyncGenerator` (Async) - Calls `client.chat.completions.create` with retry logic. Yields `response_chunk`, `error` based on stream events. Ignores `tools`/`tool_choice`.
    *   `src/llm_providers/openai_provider.py::OpenAIProvider.__repr__()` - Instance representation.
*   **OpenRouter (`openrouter_provider.py`)**
    *   `src/llm_providers/openrouter_provider.py::OpenRouterProvider(BaseLLMProvider)` - Implements provider for OpenRouter API.
    *   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.__init__(api_key, base_url, **kwargs)` - Initializes `openai.AsyncOpenAI` client configured for OpenRouter (URL, headers including Referer), `max_retries=0`.
    *   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.stream_completion(...)` -> `AsyncGenerator` (Async) - Calls `client.chat.completions.create` with retry logic. Yields `response_chunk`, `error` based on stream events. Ignores `tools`/`tool_choice`.
    *   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.__repr__()` - Instance representation.

## **Tools Base (`src/tools/`)**

*   `src/tools/base.py::ToolParameter` (Pydantic Class) - Defines parameters for a tool (name, type, description, required).
*   `src/tools/base.py::BaseTool` (ABC) - Abstract base class for all tools. Defines `name`, `description`, `parameters`.
*   `src/tools/base.py::BaseTool.execute(agent_id: str, agent_sandbox_path: Path, **kwargs: Any)` -> `Any` (Abstract Async Method) - Core execution logic signature. Requires sandbox path.
*   `src/tools/base.py::BaseTool.get_schema()` -> `Dict[str, Any]` - Returns tool description schema based on attributes.

## **Tool Executor (`src/tools/`)**

*   `src/tools/executor.py::ToolExecutor` (Class) - Manages and executes available tools.
*   `src/tools/executor.py::ToolExecutor.__init__()` - Initializes and calls `_register_available_tools`.
*   `src/tools/executor.py::ToolExecutor._register_available_tools()` - **(Internal)** Instantiates and registers tools from `AVAILABLE_TOOL_CLASSES`.
*   `src/tools/executor.py::ToolExecutor.register_tool(tool_instance: BaseTool)` - Allows manual registration of an instantiated tool.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_xml()` -> `str` - Gets tool schemas formatted as an XML string (including usage examples) for system prompts using `xml.etree.ElementTree`.
*   `src/tools/executor.py::ToolExecutor.execute_tool(agent_id: str, agent_sandbox_path: Path, tool_name: str, tool_args: Dict[str, Any])` -> `Any` (Async Method) - Finds tool, validates pre-parsed `tool_args` against schema, calls `tool.execute`. Returns raw result for `ManageTeamTool`, ensures string result for others.

## **Tool Implementations (`src/tools/`)**

*   **FileSystem (`file_system.py`)**
    *   `src/tools/file_system.py::FileSystemTool(BaseTool)` - Tool for file operations within agent sandbox.
    *   `src/tools/file_system.py::FileSystemTool.execute(agent_id: str, agent_sandbox_path: Path, **kwargs: Any)` -> `Any` (Async) - Main execution method, delegates based on 'action' kwarg ('read', 'write', 'list'). Returns string result/error.
    *   `src/tools/file_system.py::FileSystemTool._resolve_and_validate_path(sandbox_path: Path, relative_file_path: str)` -> `Path | None` (Async Internal) - Resolves relative path and checks if it's securely within the sandbox.
    *   `src/tools/file_system.py::FileSystemTool._read_file(sandbox_path: Path, filename: str)` -> `str` (Async Internal) - Reads file content after validation using `asyncio.to_thread`.
    *   `src/tools/file_system.py::FileSystemTool._write_file(sandbox_path: Path, filename: str, content: str)` -> `str` (Async Internal) - Writes file content after validation using `asyncio.to_thread`. Ensures parent dirs exist.
    *   `src/tools/file_system.py::FileSystemTool._list_directory(sandbox_path: Path, relative_dir: str)` -> `str` (Async Internal) - Lists directory contents after validation using `asyncio.to_thread`.
*   **ManageTeam (`manage_team.py`)**
    *   `src/tools/manage_team.py::ManageTeamTool(BaseTool)` - Tool for Admin AI to dynamically manage agents/teams. Signals AgentManager.
    *   `src/tools/manage_team.py::ManageTeamTool.execute(agent_id: str, agent_sandbox_path: Path, **kwargs: Any)` -> `Any` (Async) - Validates parameters based on 'action'. Returns a structured dictionary `{'status': 'success'|'error', 'action': ..., 'params': ..., 'message': ...}` for the AgentManager to process.
*   **SendMessage (`send_message.py`)**
    *   `src/tools/send_message.py::SendMessageTool(BaseTool)` - Tool for inter-agent messaging within teams (or by Admin AI). Signals AgentManager.
    *   `src/tools/send_message.py::SendMessageTool.execute(agent_id: str, agent_sandbox_path: Path, **kwargs: Any)` -> `Any` (Async) - Validates parameters. Returns a simple confirmation string (`Message routing...`) for the *sender's* history. AgentManager handles actual routing.

## **Frontend Logic (`static/js/app.js`)**

*   `static/js/app.js::displayAgentConfigurations(configs)` - Renders agent list (from config API) in UI, including Edit/Delete buttons.
*   `static/js/app.js::openAddAgentModal()` - Shows the modal for adding a new agent configuration.
*   `static/js/app.js::openEditAgentModal(agentId)` (Async) - Shows the modal for editing, currently does not prefill full data, just sets ID. *(Needs improvement for pre-filling)*
*   `static/js/app.js::handleSaveAgent(event)` (Async) - Handles Add/Edit form submission via config API, adds basic validation.
*   `static/js/app.js::handleDeleteAgent(agentId)` (Async) - Handles delete confirmation and config API call.
*   `static/js/app.js::closeModal(modalId)` - Helper to close modal dialogs.
*   `static/js/app.js` - Added event listener for refresh button (`refreshConfigButton`) to reload the page.
*   `static/js/app.js::scrollToBottom(element)` - Utility to scroll element down.
*   `static/js/app.js::connectWebSocket()` - Establishes WebSocket connection and sets up handlers (`onopen`, `onmessage`, `onerror`, `onclose`).
*   `static/js/app.js::sendMessage()` - Sends message from input box (+ attached file content) via WebSocket.
*   `static/js/app.js::addMessage(areaId, text, type, agentId = null)` - Adds formatted message to specified UI area (conversation or system log). Handles different message types.
*   `static/js/app.js::clearAgentResponsePlaceholder(agentId)` - Removes 'Waiting for response...' placeholder for a specific agent.
*   `static/js/app.js::clearAllAgentResponsePlaceholders()` - Clears all 'Waiting...' placeholders.
*   `static/js/app.js::clearMessages()` - Clears both conversation and system log areas.
*   `static/js/app.js::clearAgentStatusUI()` - Clears the agent status display area.
*   `static/js/app.js::updateAgentStatusUI(statusData)` - Updates the UI display for agent statuses based on incoming WS data or initial load.
*   `static/js/app.js::fetchAgentConfigurations()` (Async) - Fetches agent configs from API and calls `displayAgentConfigurations`.
*   `static/js/app.js::handleFileSelect(event)` - Handles file selection from input, reads content.
*   `static/js/app.js::displayFileInfo(file)` - Shows selected file info in UI.
*   `static/js/app.js::clearSelectedFile()` - Clears selected file info and input value.
*   `static/js/app.js::displayProjects(projects)` - Renders project list in UI (likely needs associated HTML).
*   `static/js/app.js::displaySessions(project, sessions)` - Renders session list for a project in UI (likely needs associated HTML).
*   `static/js/app.js::fetchProjects()` (Async) - Fetches project list from API.
*   `static/js/app.js::fetchSessions(projectName)` (Async) - Fetches session list for a project from API.
*   `static/js/app.js::handleSaveSession()` (Async) - Calls save session API endpoint.
*   `static/js/app.js::handleLoadSession(projectName, sessionName)` (Async) - Calls load session API endpoint.
*   `static/js/app.js::updateAgentList(agentData)` - Handles `agent_added`, `agent_deleted`, `agent_moved_team` WS messages to dynamically update the agent status UI.
*   `static/js/app.js::updateTeamList(teamData)` - Placeholder for handling `team_created`, `team_deleted` WS messages (requires UI elements).
*   *(DOMContentLoaded listener)* - Sets up initial connections, fetches initial data (configs, status), and sets up event listeners for buttons/inputs.

---

*Note: Obsolete functions listed in previous versions have been removed as this index reflects the current state based on the provided files.*

---
