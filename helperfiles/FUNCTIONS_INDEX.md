<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages application startup and shutdown. Instantiates the single `AgentManager`, stores it in `app.state.agent_manager`, injects it into `WebSocketManager`, asynchronously initializes bootstrap agents via `agent_manager.initialize_bootstrap_agents()` on startup, and calls `agent_manager.cleanup_providers()` on shutdown.
*   `src/main.py` (Script execution block) - Loads .env, **configures logging (console & timestamped file)**, creates FastAPI app with lifespan, mounts static files, includes routers, runs Uvicorn server. **AgentManager instance is created and managed within the `lifespan` context.**

## **Configuration (`src/config/`)**

*   `src/config/config_manager.py::ConfigManager` (Class) - Manages reading/writing of `config.yaml` (agents, teams, allowed_sub_agent_models). Async-safe CRUD via `asyncio.Lock` and atomic writes. Includes backup. *Note: Team management is primarily dynamic now, this handles static bootstrap/allowed models.*
*   `src/config/config_manager.py::ConfigManager.__init__(config_path: Path)` - Initializes with config path. Performs initial synchronous load.
*   `src/config/config_manager.py::ConfigManager._load_config_sync()` - **(Internal)** Synchronous load method. Loads full config dict, validates keys.
*   `src/config/config_manager.py::ConfigManager.load_config()` -> `Dict[str, Any]` (Async) - Asynchronously reads the full YAML file safely. Returns a deep copy. Async-safe. Keeps previous state on load/parse error.
*   `src/config/config_manager.py::ConfigManager._backup_config()` -> `bool` (Async Internal) - Backs up config file. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager._save_config_safe()` -> `bool` (Async Internal) - Safely writes internal full config state using atomic replace. Assumes lock is held. Prunes empty teams.
*   `src/config/config_manager.py::ConfigManager.get_config()` -> `List[Dict[str, Any]]` (Async) - Returns deep copy of agent config list ('agents'). Async-safe.
*   `src/config/config_manager.py::ConfigManager.get_teams()` -> `Dict[str, List[str]]` (Async) - Returns deep copy of teams config (static part, if any). Async-safe.
*   `src/config/config_manager.py::ConfigManager.get_full_config()` -> `Dict[str, Any]` (Async) - Returns deep copy of entire loaded config. Async-safe.
*   `src/config/config_manager.py::ConfigManager.get_config_data_sync()` -> `Dict[str, Any]` - **(Synchronous)** Returns deep copy of full config data loaded during `__init__`.
*   `src/config/config_manager.py::ConfigManager._find_agent_index_unsafe(agent_id: str)` -> `Optional[int]` - **(Internal)** Finds agent index. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager.add_agent(agent_config_entry: Dict[str, Any])` -> `bool` (Async) - Adds static agent entry to 'agents' list, saves full config. Async-safe.
*   `src/config/config_manager.py::ConfigManager.update_agent(agent_id: str, updated_config_data: Dict[str, Any])` -> `bool` (Async) - Updates static agent's 'config' dict, saves full config. Async-safe.
*   `src/config/config_manager.py::ConfigManager.delete_agent(agent_id: str)` -> `bool` (Async) - Removes static agent from 'agents' and 'teams', saves full config. Async-safe. Includes rollback.
*   `src/config/config_manager.py::config_manager` (Instance) - Singleton instance.

*   `src/config/settings.py::Settings` (Class) - Holds settings from `.env` and initial `config.yaml`. Manages keys, URLs, defaults, initial bootstrap configs, `allowed_sub_agent_models`. Uses `ConfigManager.get_config_data_sync()`.
*   `src/config/settings.py::Settings.__init__()` - Initializes settings, loads env vars & initial full config, calls helpers.
*   `src/config/settings.py::Settings._ensure_projects_dir()` - Creates project data directory.
*   `src/config/settings.py::Settings._check_required_keys()` - Validates necessary keys/URLs based on bootstrap agent providers using `is_provider_configured`.
*   `src/config/settings.py::Settings.get_provider_config(provider_name: str)` -> `Dict` - Gets base config (key, url, referer) for a provider from env vars.
*   `src/config/settings.py::Settings.is_provider_configured(provider_name: str)` -> `bool` - Checks if provider has essential config (API key/URL) set in settings.
*   `src/config/settings.py::Settings.get_agent_config_by_id(agent_id: str)` -> `Optional[Dict[str, Any]]` - Retrieves specific bootstrap agent's 'config' dict by ID from initial load.
*   `src/config/settings.py::Settings.get_formatted_allowed_models()` -> `str` - Returns formatted string list of allowed models for prompts, filtering invalid entries.
*   `src/config/settings.py::settings` (Instance) - Singleton instance.

## **API Routes (`src/api/`)**

*   **Pydantic Models (`src/api/http_routes.py`)**: `AgentInfo`, `GeneralResponse`, `SessionInfo`, `ProjectInfo`, `SaveSessionInput`, `AgentConfigInput`, `AgentConfigCreate`.
*   `src/api/http_routes.py::get_agent_manager_dependency(request: Request)` -> `'AgentManager'` - **FastAPI dependency retrieves the shared AgentManager instance from `request.app.state`.**
*   `src/api/http_routes.py::get_index_page(request: Request)` (Async) - Serves `index.html`.
*   `src/api/http_routes.py::get_agent_configurations()` -> `List[AgentInfo]` (Async) - (`GET /api/config/agents`) Retrieves basic info for STATIC agents listed in `config.yaml` via `ConfigManager`.
*   `src/api/http_routes.py::create_agent_configuration(agent_data: AgentConfigCreate)` -> `GeneralResponse` (Async) - (`POST /api/config/agents`) Adds STATIC agent to `config.yaml` via `ConfigManager`. Requires restart.
*   `src/api/http_routes.py::update_agent_configuration(agent_id: str, agent_config_data: AgentConfigInput)` -> `GeneralResponse` (Async) - (`PUT /api/config/agents/{agent_id}`) Updates STATIC agent in `config.yaml` via `ConfigManager`. Requires restart.
*   `src/api/http_routes.py::delete_agent_configuration(agent_id: str)` -> `GeneralResponse` (Async) - (`DELETE /api/config/agents/{agent_id}`) Removes STATIC agent from `config.yaml` via `ConfigManager`. Requires restart.
*   `src/api/http_routes.py::list_projects()` -> `List[ProjectInfo]` (Async) - (`GET /api/projects`) Lists project directories.
*   `src/api/http_routes.py::list_sessions(project_name: str)` -> `List[SessionInfo]` (Async) - (`GET /api/projects/{project_name}/sessions`) **Lists session directories by checking for `agent_session_data.json`.**
*   `src/api/http_routes.py::save_current_session(project_name: str, session_input: Optional[SaveSessionInput], manager: 'AgentManager')` -> `GeneralResponse` (Async) - (`POST /api/projects/{project_name}/sessions`) Saves current state via injected `AgentManager.save_session()`.
*   `src/api/http_routes.py::load_specific_session(project_name: str, session_name: str, manager: 'AgentManager')` -> `GeneralResponse` (Async) - (`POST /api/projects/{project_name}/sessions/{session_name}/load`) Loads saved state via injected `AgentManager.load_session()`.

## **WebSocket Management (`src/api/`)**

*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level injection of the shared `AgentManager` instance from `main.py`.
*   `src/api/websocket_manager.py::broadcast(message: str)` (Async) - Sends message to all active WebSocket connections.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` (Async) - Handler for `/ws`. Manages connection lifecycle, receives messages, routes user messages/overrides to the shared `agent_manager_instance`.

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents individual LLM agent. Handles prompt setup, LLM interaction via provider, XML tool parsing (multiple), sandbox creation.
*   `src/agents/core.py::Agent.__init__(agent_config: Dict, llm_provider: BaseLLMProvider, manager: 'AgentManager')` - Initializes agent using final config from manager. Stores config, compiles tool regex.
*   `src/agents/core.py::Agent.set_status(new_status: str, tool_info: Optional[Dict[str, str]] = None)` - Updates agent status, notifies manager.
*   `src/agents/core.py::Agent.set_manager(manager: 'AgentManager')` - Sets manager reference.
*   `src/agents/core.py::Agent.ensure_sandbox_exists()` -> `bool` - Creates agent's sandbox directory.
*   `src/agents/core.py::Agent._find_and_parse_tool_calls()` -> `List[Tuple[str, Dict[str, Any], Tuple[int, int]]]` - **(Internal)** Finds and parses *all* valid XML tool calls (raw or fenced) in the text buffer.
*   `src/agents/core.py::Agent.process_message()` -> `AsyncGenerator[Dict, None]` (Async) - Core logic. Calls provider `stream_completion`, yields events (`response_chunk`, `tool_requests`, `final_response`, `error`). CycleHandler handles history and tool results.
*   `src/agents/core.py::Agent.get_state()` -> `Dict[str, Any]` - Returns current agent state dictionary.
*   `src/agents/core.py::Agent.clear_history()` - Clears message history, resets with system prompt.

## **Agent State Manager (`src/agents/`)**

*   `src/agents/state_manager.py::AgentStateManager` (Class) - Manages dynamic team/agent assignment state (`teams`, `agent_to_team` dictionaries). In-memory.
*   `src/agents/state_manager.py::AgentStateManager.__init__(manager: 'AgentManager')` - Initializes with reference to main AgentManager.
*   `src/agents/state_manager.py::AgentStateManager.create_new_team(team_id: str)` -> `Tuple[bool, str]` (Async) - Creates new empty team. Notifies UI.
*   `src/agents/state_manager.py::AgentStateManager.delete_existing_team(team_id: str)` -> `Tuple[bool, str]` (Async) - Deletes an empty team. Notifies UI.
*   `src/agents/state_manager.py::AgentStateManager.add_agent_to_team(agent_id: str, team_id: str)` -> `Tuple[bool, str]` (Async) - Adds agent ID to team list and agent->team map. Creates team if needed. Notifies UI.
*   `src/agents/state_manager.py::AgentStateManager.remove_agent_from_team(agent_id: str, team_id: str)` -> `Tuple[bool, str]` (Async) - Removes agent ID from team list and agent->team map. Notifies UI.
*   `src/agents/state_manager.py::AgentStateManager.get_agent_team(agent_id: str)` -> `Optional[str]` - Gets team ID for an agent.
*   `src/agents/state_manager.py::AgentStateManager.get_team_members(team_id: str)` -> `Optional[List[str]]` - Gets member list for a team.
*   `src/agents/state_manager.py::AgentStateManager.get_team_info_dict()` -> `Dict[str, List[str]]` - Returns copy of the teams dictionary.
*   `src/agents/state_manager.py::AgentStateManager.remove_agent_from_all_teams_state(agent_id: str)` - Cleans up team state when an agent is deleted.
*   `src/agents/state_manager.py::AgentStateManager.load_state(teams: Dict, agent_to_team: Dict)` - Overwrites internal state from loaded data.
*   `src/agents/state_manager.py::AgentStateManager.clear_state()` - Resets internal team state.

## **Agent Session Manager (`src/agents/`)**

*   `src/agents/session_manager.py::SessionManager` (Class) - Handles saving/loading of session state (dynamic agents, histories, teams).
*   `src/agents/session_manager.py::SessionManager.__init__(manager: 'AgentManager', state_manager: 'AgentStateManager')` - Initializes with references to AgentManager and StateManager.
*   `src/agents/session_manager.py::SessionManager.save_session(project_name: str, session_name: Optional[str] = None)` -> `Tuple[bool, str]` (Async) - Gathers state from AgentManager/StateManager, saves to `agent_session_data.json`. **Logs details of saved data.** Notifies UI. Updates manager's current project/session.
*   `src/agents/session_manager.py::SessionManager.load_session(project_name: str, session_name: str)` -> `Tuple[bool, str]` (Async) - Loads state from `agent_session_data.json`. **Logs details of loaded data and agent presence at various stages.** Clears dynamic state via AgentManager/StateManager, recreates agents via AgentManager, loads histories, updates manager's current project/session. Notifies UI. **Ensures bootstrap agents are preserved.**

## **Agent Prompt Utilities (`src/agents/`)**

*   `src/agents/prompt_utils.py::STANDARD_FRAMEWORK_INSTRUCTIONS` (Constant str) - Template string containing standard instructions (tools, comms, ID, team, task breakdown, file system scopes) injected into dynamic agents.
*   `src/agents/prompt_utils.py::ADMIN_AI_OPERATIONAL_INSTRUCTIONS` (Constant str) - Template string containing specific workflow/tool usage instructions for Admin AI (including refined cleanup steps **and integrated tool descriptions placeholder**).
*   `src/agents/prompt_utils.py::update_agent_prompt_team_id(manager: 'AgentManager', agent_id: str, new_team_id: Optional[str])` (Async) - Updates the agent's internal prompt state (in memory & history) after team assignment changes.

## **Agent Interaction Handler (`src/agents/`)**

*   `src/agents/interaction_handler.py::AgentInteractionHandler` (Class) - Handles processing specific tool interactions and execution logic, using AgentManager context.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.__init__(manager: 'AgentManager')` - Initializes with reference to AgentManager.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.handle_manage_team_action(action: Optional[str], params: Dict[str, Any], calling_agent_id: str)` -> `Tuple[bool, str, Optional[Any]]` (Async) - Processes validated `ManageTeamTool` signals (create/delete agent/team, list, etc.), calling manager methods. Returns feedback for caller.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.route_and_activate_agent_message(sender_id: str, target_id: str, message_content: str)` -> `Optional[asyncio.Task]` (Async) - Routes messages via `SendMessageTool` signal. Checks target/team policy. Appends feedback to sender on failure. Appends message to target history and **schedules target agent cycle** via manager if idle.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.execute_single_tool(agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any], project_name: Optional[str], session_name: Optional[str])` -> `Optional[Dict]` (Async) - Executes a single validated tool call via `ToolExecutor`, **passing project/session context**. Formats results (raw dict for ManageTeam, string for others). Sets agent status.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.failed_tool_result(call_id: Optional[str], tool_name: Optional[str])` -> `Optional[ToolResultDict]` (Async Helper) - Generates formatted error result dict for failed tool dispatch.

## **Agent Cycle Handler (`src/agents/`)**

*   `src/agents/cycle_handler.py::AgentCycleHandler` (Class) - Handles the execution cycle of a single agent's turn.
*   `src/agents/cycle_handler.py::AgentCycleHandler.__init__(manager: 'AgentManager', interaction_handler: 'AgentInteractionHandler')` - Initializes with references to AgentManager and InteractionHandler.
*   `src/agents/cycle_handler.py::AgentCycleHandler.run_cycle(agent: Agent, retry_count: int = 0)` (Async) - Manages agent's `process_message` generator loop. Handles events (`response_chunk`, `status`, `error`, `final_response`, `tool_requests`). Handles stream errors with retry logic & user override request via manager. **Delegates tool execution to `InteractionHandler`**. Processes manager feedback, appends to history. **Schedules reactivation** via manager if needed based on tool success or queued messages.

## **Agent Manager (Coordinator) (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator. Manages agent instances, overall state (project/session), **delegates task execution cycles to `AgentCycleHandler`**, delegates state/session management, handles high-level error/override flow.
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager: Optional[Any] = None)` - Initializes self, `ToolExecutor`, `AgentStateManager`, `SessionManager`, `AgentInteractionHandler`, **and `AgentCycleHandler`**. Gets tool descriptions. Ensures projects dir.
*   `src/agents/manager.py::AgentManager._ensure_projects_dir()` - **(Internal)** Creates base project directory.
*   `src/agents/manager.py::AgentManager.initialize_bootstrap_agents()` (Async) - Loads bootstrap agents from `settings`, **constructs prompts using `prompt_utils`**, calls `_create_agent_internal`.
*   `src/agents/manager.py::AgentManager._create_agent_internal(...)` -> `Tuple[bool, str, Optional[str]]` (Async Internal) - Core logic: Validates provider config & allowed model, **constructs final prompt using `prompt_utils`**, instantiates Provider & Agent, ensures sandbox, adds agent to `self.agents`, delegates team state update to `StateManager`.
*   `src/agents/manager.py::AgentManager.create_agent_instance(...)` -> `Tuple[bool, str, Optional[str]]` (Async) - Public method for dynamic agent creation (called by `InteractionHandler`). Calls `_create_agent_internal`, notifies UI.
*   `src/agents/manager.py::AgentManager.delete_agent_instance(agent_id: str)` -> `Tuple[bool, str]` (Async) - Removes agent from `self.agents`, delegates team state cleanup to `StateManager`, closes provider. Notifies UI. Handles bootstrap agent check.
*   `src/agents/manager.py::AgentManager._generate_unique_agent_id(prefix="agent")` -> `str` - **(Internal)** Generates unique agent ID.
*   `src/agents/manager.py::AgentManager.schedule_cycle(agent: Agent, retry_count: int = 0)` (Async) - **Schedules the agent's execution cycle via the `AgentCycleHandler`**.
*   `src/agents/manager.py::AgentManager.handle_user_message(message: str, client_id: Optional[str] = None)` (Async) - Routes user message to Admin AI, **ensures default project/session context**, **schedules Admin AI cycle** via `schedule_cycle`. Checks agent status. Handles case where Admin AI is missing.
*   `src/agents/manager.py::AgentManager.handle_user_override(override_data: Dict[str, Any])` (Async) - Handles config override for a stuck agent, recreates provider, **schedules agent cycle** via `schedule_cycle`.
*   `src/agents/manager.py::AgentManager.request_user_override(agent_id: str, last_error: str)` (Async) - **Sends request for user override to the UI** (called by `CycleHandler`).
*   `src/agents/manager.py::AgentManager.push_agent_status_update(agent_id: str)` (Async Helper) - Gets agent state (incl. team from `StateManager`), sends to UI.
*   `src/agents/manager.py::AgentManager.send_to_ui(message_data: Dict[str, Any])` (Async Helper) - Sends JSON data to UI via `broadcast`.
*   `src/agents/manager.py::AgentManager.get_agent_status()` -> `Dict[str, Dict[str, Any]]` - **(Synchronous)** Gets snapshot of current agent statuses (incl. team from `StateManager`).
*   `src/agents/manager.py::AgentManager.save_session(...)` -> `Tuple[bool, str]` (Async) - Delegates call to `SessionManager`.
*   `src/agents/manager.py::AgentManager.load_session(...)` -> `Tuple[bool, str]` (Async) - Delegates call to `SessionManager`.
*   `src/agents/manager.py::AgentManager.cleanup_providers()` (Async) - Calls `close_session` on unique active providers.
*   `src/agents/manager.py::AgentManager._close_provider_safe(provider: BaseLLMProvider)` (Async Internal) - Safely calls `close_session`.
*   `src/agents/manager.py::AgentManager.get_agent_info_list_sync(filter_team_id: Optional[str])` -> `List[Dict]` - **(Synchronous)** Helper used by `InteractionHandler` to get filtered/full agent info list.

## **LLM Providers Base (`src/llm_providers/`)**

*   `src/llm_providers/base.py::BaseLLMProvider` (ABC) - Abstract Base Class for LLM providers.
*   `src/llm_providers/base.py::BaseLLMProvider.__init__(api_key, base_url, **kwargs)` (Abstract) - Init signature.
*   `src/llm_providers/base.py::BaseLLMProvider.stream_completion(...)` -> `AsyncGenerator[Dict, Optional[List[ToolResultDict]]]` (Abstract Async) - Core interaction method. Yields events. Can receive tool results via `asend`. Handles text chunks and potential errors.
*   `src/llm_providers/base.py::BaseLLMProvider.close_session()` (Async Abstract) - Optional cleanup method.
*   `src/llm_providers/base.py::BaseLLMProvider.__repr__()` - Basic instance representation.

## **LLM Providers Implementations (`src/llm_providers/`)**

*   **Ollama (`ollama_provider.py`)**
    *   `src/llm_providers/ollama_provider.py::OllamaProvider(BaseLLMProvider)` - Implements provider for local Ollama. Includes stream error handling and initial call retries. Ignores tools/tool_choice args.
    *   `(All other methods like __init__, _get_session, close_session, stream_completion)` - Handles aiohttp session and API interaction specifics for Ollama.
*   **OpenAI (`openai_provider.py`)**
    *   `src/llm_providers/openai_provider.py::OpenAIProvider(BaseLLMProvider)` - Implements provider for OpenAI API. Includes stream error handling and initial call retries. Ignores tools/tool_choice args.
    *   `(All other methods like __init__, stream_completion)` - Handles `openai` library client and API interaction specifics.
*   **OpenRouter (`openrouter_provider.py`)**
    *   `src/llm_providers/openrouter_provider.py::OpenRouterProvider(BaseLLMProvider)` - Implements provider for OpenRouter API. Includes stream error handling and initial call retries. Ignores tools/tool_choice args.
    *   `(All other methods like __init__, stream_completion)` - Handles `openai` library client (configured for OpenRouter) and API interaction specifics.

## **Tools Base (`src/tools/`)**

*   `src/tools/base.py::ToolParameter` (Pydantic Class) - Defines tool parameters.
*   `src/tools/base.py::BaseTool` (ABC) - Abstract base class for tools. Defines `name`, `description`, `parameters`.
*   `src/tools/base.py::BaseTool.execute(agent_id: str, agent_sandbox_path: Path, project_name: Optional[str], session_name: Optional[str], **kwargs: Any)` (Abstract Async) - Core execution logic signature, **includes project/session context**.
*   `src/tools/base.py::BaseTool.get_schema()` -> `Dict` - Returns tool schema dictionary.

## **Tool Executor (`src/tools/`)**

*   `src/tools/executor.py::ToolExecutor` (Class) - Manages and executes tools. Registers tools on init.
*   `src/tools/executor.py::ToolExecutor.__init__()` - Initializes and calls `_register_available_tools`.
*   `src/tools/executor.py::ToolExecutor._register_available_tools()` - **(Internal)** Instantiates tools from `AVAILABLE_TOOL_CLASSES`.
*   `src/tools/executor.py::ToolExecutor.register_tool(tool_instance: BaseTool)` - Manually registers tool.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_xml()` -> `str` - Gets tool schemas as XML string for prompts.
*   `src/tools/executor.py::ToolExecutor.execute_tool(agent_id: str, agent_sandbox_path: Path, tool_name: str, tool_args: Dict, project_name: Optional[str], session_name: Optional[str])` -> `Any` (Async) - Finds tool, validates args based on schema, calls `execute`, **passing project/session context**. Returns raw dict for `ManageTeamTool`, stringified result for others. Handles execution errors.

## **Tool Implementations (`src/tools/`)**

*   **FileSystem (`file_system.py`)**
    *   `src/tools/file_system.py::FileSystemTool(BaseTool)` - File operations in sandbox or shared space.
    *   `src/tools/file_system.py::FileSystemTool.execute(...)` (Async) - Delegates to internal read/write/list methods based on 'action' and 'scope'. **Uses project/session context for 'shared' scope.** Validates paths.
    *   `src/tools/file_system.py::FileSystemTool._resolve_and_validate_path(...)` (Async Internal) - Resolves/validates path within sandbox or shared space.
    *   `src/tools/file_system.py::FileSystemTool._read_file(...)` (Async Internal) - Reads file.
    *   `src/tools/file_system.py::FileSystemTool._write_file(...)` (Async Internal) - Writes file.
    *   `src/tools/file_system.py::FileSystemTool._list_directory(...)` (Async Internal) - Lists directory.
*   **GitHub (`github_tool.py`)**
    *   `src/tools/github_tool.py::GitHubTool(BaseTool)` - Interacts with GitHub API. **Uses correct endpoints for user/auth repo listing.**
    *   `src/tools/github_tool.py::GitHubTool.__init__()` - Checks for GitHub token.
    *   `src/tools/github_tool.py::GitHubTool._make_github_request(...)` (Async Internal) - Helper for API calls with error handling.
    *   `src/tools/github_tool.py::GitHubTool.execute(...)` (Async) - Executes list/read actions.
*   **ManageTeam (`manage_team.py`)**
    *   `src/tools/manage_team.py::ManageTeamTool(BaseTool)` - Signals AgentManager (via InteractionHandler) for agent/team management.
    *   `src/tools/manage_team.py::ManageTeamTool.execute(...)` (Async) - Validates params based on action, returns structured dict signal for InteractionHandler to process. **Includes specific parameter validation for delete/remove actions.**
*   **SendMessage (`send_message.py`)**
    *   `src/tools/send_message.py::SendMessageTool(BaseTool)` - Signals AgentManager (via InteractionHandler) for inter-agent messaging.
    *   `src/tools/send_message.py::SendMessageTool.execute(...)` (Async) - Validates params, returns confirmation string for sender's history. Actual routing done by InteractionHandler.
*   **WebSearch (`web_search.py`)**
    *   `src/tools/web_search.py::WebSearchTool(BaseTool)` - Performs web search via DDG HTML scraping.
    *   `src/tools/web_search.py::WebSearchTool._get_html(...)` (Async Internal) - Fetches HTML.
    *   `src/tools/web_search.py::WebSearchTool._parse_results(...)` (Async Internal) - Parses HTML for results.
    *   `src/tools/web_search.py::WebSearchTool.execute(...)` (Async) - Orchestrates search and formatting.

## **Frontend Logic (`static/js/app.js`)**

*   `static/js/app.js::DOMContentLoaded Listener` - Entry point. Gets DOM elements, initializes WebSocket connection (`setupWebSocket`), sets up event listeners (`setupEventListeners`), loads initial config display (`displayAgentConfigurations`), **loads initial project list (`loadProjects`)**. Handles initialization errors.
*   `static/js/app.js::setupWebSocket()` - Establishes and manages the WebSocket connection lifecycle (open, message, error, close) with automatic reconnection logic. Assigns global `ws` instance.
*   `static/js/app.js::handleWebSocketMessage(data)` - Central handler for incoming WebSocket messages. Parses message, determines type, and calls appropriate UI update or modal functions. **Handles `agent_added`, `agent_deleted`, `agent_status_update`.** (Team messages logged for now).
*   `static/js/app.js::addMessage(areaId, text, type, agentId)` - Adds a formatted message div to the specified message area (conversation or system log), handles timestamp for logs, scrolls the area.
*   `static/js/app.js::appendAgentResponseChunk(agentId, chunk)` - Appends streaming text chunks to an agent's response message in the conversation area. Creates the message div if needed. Scrolls area.
*   `static/js/app.js::finalizeAgentResponse(agentId, finalContent)` - Marks an agent's streaming response as complete. Adds full message if no chunks were received. Scrolls area.
*   `static/js/app.js::updateLogStatus(message, isError)` - Updates the connection status message shown in the system log area.
*   `static/js/app.js::updateAgentStatusUI(agentId, statusData)` - Entry point to update the Agent Status list. Calls `addOrUpdateAgentStatusEntry`.
*   `static/js/app.js::addOrUpdateAgentStatusEntry(agentId, statusData)` - Adds or updates a specific agent's display item in the status list UI. **Includes team display.**
*   `static/js/app.js::removeAgentStatusEntry(agentId)` - Removes an agent's display item from the status list UI.
*   `static/js/app.js::addRawLogEntry(data)` - Logs raw received WebSocket data to the browser console for debugging.
*   `static/js/app.js::setupEventListeners()` - Attaches event listeners for send button, message input (Enter key), file attachment, config buttons, modal forms, bottom navigation buttons, **session management elements**, and global modal closing.
*   `static/js/app.js::showView(viewId)` - Handles bottom navigation clicks. Hides all `.view-panel` elements, shows the target panel, and updates the active state of navigation buttons. **Refreshes project list when showing session view.**
*   `static/js/app.js::handleSendMessage()` - Gathers text from input and file data (if attached), checks WebSocket connection, formats message object, calls `addMessage` for user prompt, sends message via `ws.send()`, clears input/file.
*   `static/js/app.js::handleFileSelect(event)` - Handles file input change event, validates file type/size, reads file content using FileReader, stores file info/content in global variables.
*   `static/js/app.js::displayFileInfo()` - Updates the UI element to show attached file name and size, or clears it.
*   `static/js/app.js::clearFileInput()` - Resets file attachment state variables and clears the file input display.
*   `static/js/app.js::displayAgentConfigurations()` - Fetches static agent configurations from `/api/config/agents`, renders them in the config view, attaches edit/delete listeners.
*   `static/js/app.js::handleSaveAgent(event)` - Handles submission of the add/edit static agent modal form. Sends POST/PUT request to `/api/config/agents`. Reloads config display on success.
*   `static/js/app.js::handleDeleteAgent(agentId)` - Handles click on delete button for static agent config. Sends DELETE request to `/api/config/agents/{agentId}`. Reloads config display on success.
*   `static/js/app.js::openModal(modalId, editId)` - Displays the specified modal (`agent-modal` or `override-modal`). Pre-fills agent edit form if `editId` is provided.
*   `static/js/app.js::closeModal(modalId)` - Hides the specified modal and resets its form.
*   `static/js/app.js::showOverrideModal(data)` - Pre-fills and opens the agent override modal based on data received from backend.
*   `static/js/app.js::handleSubmitOverride(event)` - Handles submission of the agent override modal form. Sends `submit_user_override` message via WebSocket.
*   `static/js/app.js::loadProjects()` - Fetches project list from API and populates project dropdown.
*   `static/js/app.js::loadSessions(projectName)` - Fetches session list for a project and populates session dropdown.
*   `static/js/app.js::handleLoadSession()` - Handles click on 'Load Session' button, calls API endpoint. Includes null checks for UI elements.
*   `static/js/app.js::handleSaveSession()` - Handles click on 'Save Session' button, calls API endpoint.
*   `static/js/app.js::displaySessionStatus(message, isError)` - Shows success/error messages in the session view.

---

*Note: Functions previously listed under separate JS modules are integrated into `static/js/app.js`.*
