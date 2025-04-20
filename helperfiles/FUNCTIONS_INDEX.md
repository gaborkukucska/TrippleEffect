<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages startup/shutdown. Instantiates `AgentManager`, runs `ModelRegistry.discover_models_and_providers()`, initializes bootstrap agents, starts/stops Ollama proxy if configured, calls `agent_manager.cleanup_providers()` (saves metrics & quarantine state).
*   `src/main.py` (Script execution block) - Loads .env, configures logging, creates FastAPI app, runs Uvicorn.

## **Configuration (`src/config/`)**

*   `src/config/config_manager.py::ConfigManager` (Class) - Manages reading/writing of `config.yaml` (bootstrap agents, teams, etc.), including backups and atomic writes.
*   `src/config/config_manager.py::ConfigManager.__init__(config_path)` - Initializes manager with config path, loads config synchronously.
*   `src/config/config_manager.py::ConfigManager._load_config_sync()` (Internal) - Synchronously loads the full configuration from YAML file during init, handles errors, validates basic structure.
*   `src/config/config_manager.py::ConfigManager.load_config()` (Async) -> `Dict` - Asynchronously reads the full YAML config, validates structure, updates internal state, returns deep copy. Async-safe.
*   `src/config/config_manager.py::ConfigManager._backup_config()` (Async Internal) -> `bool` - Creates a `.bak` file of the current config. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager._save_config_safe()` (Async Internal) -> `bool` - Writes the full internal config data to YAML atomically using a temp file. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager.get_config()` (Async) -> `List` - Returns a deep copy of the agent configuration list ('agents'). Async-safe.
*   `src/config/config_manager.py::ConfigManager.get_teams()` (Async) -> `Dict` - Returns a deep copy of the teams configuration. Async-safe.
*   `src/config/config_manager.py::ConfigManager.get_full_config()` (Async) -> `Dict` - Returns a deep copy of the entire loaded configuration. Async-safe.
*   `src/config/config_manager.py::ConfigManager.get_config_data_sync()` -> `Dict` - Synchronously returns a deep copy of the full configuration loaded at initialization.
*   `src/config/config_manager.py::ConfigManager._find_agent_index_unsafe(agent_id)` (Internal) -> `Optional[int]` - Finds list index of an agent by ID. Assumes lock is held.
*   `src/config/config_manager.py::ConfigManager.add_agent(agent_config_entry)` (Async) -> `bool` - Adds agent config, saves full config. Async-safe.
*   `src/config/config_manager.py::ConfigManager.update_agent(agent_id, updated_config_data)` (Async) -> `bool` - Updates agent's 'config' field, saves full config. Async-safe.
*   `src/config/config_manager.py::ConfigManager.delete_agent(agent_id)` (Async) -> `bool` - Removes agent from 'agents' and 'teams', saves full config. Async-safe.
*   `src/config/config_manager.py::config_manager` (Instance) - Singleton `ConfigManager` instance.

*   `src/config/model_registry.py::ModelInfo` (Class) - Simple dictionary subclass for type hinting.
*   `src/config/model_registry.py::ModelRegistry` (Class) - Handles discovery, filtering, storage of available models from reachable providers.
*   `src/config/model_registry.py::ModelRegistry.__init__(settings_obj)` - Initializes registry with settings.
*   `src/config/model_registry.py::ModelRegistry.discover_models_and_providers()` (Async) - Coordinates provider reachability checks and model discovery.
*   `src/config/model_registry.py::ModelRegistry._discover_providers()` (Async Internal) - Checks reachability of configured or local providers.
*   `src/config/model_registry.py::ModelRegistry._check_local_provider_prioritized(provider_name, env_url, default_port)` (Async Internal) - Checks local provider reachability with specific priority order.
*   `src/config/model_registry.py::ModelRegistry._check_single_local_url(provider_name, base_url, source_description)` (Async Internal) -> `bool` - Checks reachability for a single local URL.
*   `src/config/model_registry.py::ModelRegistry._discover_openrouter_models()` (Async Internal) - Fetches models from OpenRouter API.
*   `src/config/model_registry.py::ModelRegistry._discover_ollama_models()` (Async Internal) - Fetches models from Ollama API (direct connection).
*   `src/config/model_registry.py::ModelRegistry._discover_litellm_models()` (Async Internal) - Fetches models from LiteLLM API.
*   `src/config/model_registry.py::ModelRegistry._discover_openai_models()` (Async Internal) - Manually adds common OpenAI models if provider reachable.
*   `src/config/model_registry.py::ModelRegistry._apply_filters()` (Internal) - Filters raw models based on reachability and `MODEL_TIER`.
*   `src/config/model_registry.py::ModelRegistry.get_available_models_list(provider=None)` -> `List[str]` - Returns sorted list of available model IDs.
*   `src/config/model_registry.py::ModelRegistry.get_available_models_dict()` -> `Dict` - Returns a deep copy of available models dictionary.
*   `src/config/model_registry.py::ModelRegistry.find_provider_for_model(model_id)` -> `Optional[str]` - Finds the provider for a given model ID.
*   `src/config/model_registry.py::ModelRegistry.get_formatted_available_models()` -> `str` - Returns a formatted string listing available models for prompts/UI.
*   `src/config/model_registry.py::ModelRegistry.is_model_available(provider, model_id)` -> `bool` - Checks if a specific model is available for a provider.
*   `src/config/model_registry.py::ModelRegistry.get_reachable_provider_url(provider)` -> `Optional[str]` - Returns the base URL for a reachable provider.
*   `src/config/model_registry.py::ModelRegistry._log_available_models()` (Internal) - Logs the final available models.

*   `src/config/settings.py::Settings` (Class) - Holds settings from `.env`, `prompts.json`, and initial `config.yaml`. Instantiates `ModelRegistry` after loading. **Includes Tavily API key**.
*   `src/config/settings.py::Settings.__init__()` - Initializes settings, loads env vars (incl. Tavily), prompts, initial config.
*   `src/config/settings.py::Settings._load_prompts_from_json()` (Internal) - Loads prompts from `prompts.json`.
*   `src/config/settings.py::Settings._ensure_projects_dir()` (Internal) - Creates projects directory.
*   `src/config/settings.py::Settings._check_required_keys()` (Internal) - Validates provider keys/URLs and logs status **(incl. Tavily)**.
*   `src/config/settings.py::Settings.get_provider_config(provider_name)` -> `Dict` - Gets base config (URL, referer) for a provider.
*   `src/config/settings.py::Settings.is_provider_configured(provider_name)` -> `bool` - Checks if provider has essential config (keys or URL/proxy).
*   `src/config/settings.py::Settings.get_agent_config_by_id(agent_id)` -> `Optional[Dict]` - Retrieves bootstrap agent's config.
*   `src/config/settings.py::Settings.get_formatted_allowed_models()` -> `str` - Delegates to `model_registry`.
*   `src/config/settings.py::settings` (Instance) - Singleton settings instance.
*   `src/config/settings.py::model_registry` (Instance) - Singleton ModelRegistry instance.

## **API Routes (`src/api/`)**

*   `src/api/http_routes.py::get_agent_manager_dependency(request: Request)` -> `'AgentManager'` - FastAPI dependency retrieving shared AgentManager from `app.state`.
*   `src/api/http_routes.py::get_index_page(request: Request)` (Async) -> `HTMLResponse` - Serves the main index.html page.
*   `src/api/http_routes.py::get_agent_configurations()` (Async) -> `List[AgentInfo]` - Retrieves list of static agent configs from `config_manager`.
*   `src/api/http_routes.py::create_agent_configuration(agent_data: AgentConfigCreate)` (Async) -> `GeneralResponse` - Adds agent config to `config.yaml` via `config_manager`. Requires restart.
*   `src/api/http_routes.py::update_agent_configuration(agent_id, agent_config_data)` (Async) -> `GeneralResponse` - Updates agent config in `config.yaml` via `config_manager`. Requires restart.
*   `src/api/http_routes.py::delete_agent_configuration(agent_id)` (Async) -> `GeneralResponse` - Removes agent config from `config.yaml` via `config_manager`. Requires restart.
*   `src/api/http_routes.py::list_projects()` (Async) -> `List[ProjectInfo]` - Lists projects by scanning the projects base directory.
*   `src/api/http_routes.py::list_sessions(project_name)` (Async) -> `List[SessionInfo]` - Lists sessions within a project by looking for session files.
*   `src/api/http_routes.py::save_current_session(project_name, session_input, manager)` (Async) -> `GeneralResponse` - Saves the current session state via `AgentManager`.
*   `src/api/http_routes.py::load_specific_session(project_name, session_name, manager)` (Async) -> `GeneralResponse` - Loads a session state via `AgentManager`.

## **WebSocket Management (`src/api/`)**

*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level function to inject shared `AgentManager`.
*   `src/api/websocket_manager.py::broadcast(message: str)` (Async) - Sends message to all connected WebSocket clients.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` (Async) - Main WebSocket handler. Accepts connections, forwards incoming messages to `AgentManager`.

## **Agent Constants (`src/agents/`)**

*   `src/agents/constants.py::AGENT_STATUS_IDLE` - Constant string.
*   `src/agents/constants.py::AGENT_STATUS_PROCESSING` - Constant string.
*   `src/agents/constants.py::AGENT_STATUS_PLANNING` - Constant string.
*   `src/agents/constants.py::AGENT_STATUS_AWAITING_TOOL` - Constant string.
*   `src/agents/constants.py::AGENT_STATUS_EXECUTING_TOOL` - Constant string.
*   `src/agents/constants.py::AGENT_STATUS_ERROR` - Constant string.

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents individual LLM agent. Parses XML tool calls, detects plans. Imports status constants.
*   `src/agents/core.py::Agent.__init__(agent_config, llm_provider, manager)` - Initializes agent state, message history, compiles regex patterns. Added `current_plan` attribute.
*   `src/agents/core.py::Agent.set_status(new_status, tool_info=None, plan_info=None)` - Updates status, clears/sets `current_tool_info` or `current_plan`, pushes UI update.
*   `src/agents/core.py::Agent.set_manager(manager)` - Sets the agent manager reference.
*   `src/agents/core.py::Agent.set_tool_executor(tool_executor)` - Sets the tool executor reference (marked as legacy/unused).
*   `src/agents/core.py::Agent.ensure_sandbox_exists()` -> `bool` - Creates the agent's sandbox directory.
*   `src/agents/core.py::Agent.process_message()` (Async Generator) - Main agent processing loop. Calls LLM provider, processes stream, detects `<plan>` tag (yields `plan_generated`), calls external `find_and_parse_xml_tool_calls`, yields `tool_requests` or `final_response`. **(Note: Time context injection requires modification here or in caller)**
*   `src/agents/core.py::Agent.get_state()` -> `Dict` - Returns agent state, includes `current_plan` if status is PLANNING.
*   `src/agents/core.py::Agent.clear_history()` - Clears message history, keeps system prompt.

## **Agent Tool Parser (`src/agents/`)**

*   `src/agents/agent_tool_parser.py::find_and_parse_xml_tool_calls(text_buffer, tools, raw_xml_pattern, markdown_xml_pattern, agent_id)` -> `List` - Standalone function to find/parse XML tool calls (raw/fenced). Handles validation for universally required params.

## **Agent State Manager (`src/agents/`)**

*   `src/agents/state_manager.py::AgentStateManager` (Class) - Manages dynamic team/agent assignment state.
*   `src/agents/state_manager.py::AgentStateManager.__init__(manager)` - Initializes state manager with reference to main manager.
*   `src/agents/state_manager.py::AgentStateManager.create_new_team(team_id)` (Async) -> `Tuple[bool, str]` - Creates a new team or confirms existence (idempotent).
*   `src/agents/state_manager.py::AgentStateManager.delete_existing_team(team_id)` (Async) -> `Tuple[bool, str]` - Deletes an empty team.
*   `src/agents/state_manager.py::AgentStateManager.add_agent_to_team(agent_id, team_id)` (Async) -> `Tuple[bool, str]` - Adds agent to team state, updates mappings.
*   `src/agents/state_manager.py::AgentStateManager.remove_agent_from_team(agent_id, team_id)` (Async) -> `Tuple[bool, str]` - Removes agent from team state, updates mappings.
*   `src/agents/state_manager.py::AgentStateManager.get_agent_team(agent_id)` -> `Optional[str]` - Gets team ID for an agent.
*   `src/agents/state_manager.py::AgentStateManager.get_team_members(team_id)` -> `Optional[List[str]]` - Gets list of agent IDs in a team.
*   `src/agents/state_manager.py::AgentStateManager.get_agents_in_team(team_id)` -> `List[Agent]` - Gets actual Agent instances belonging to a team.
*   `src/agents/state_manager.py::AgentStateManager.get_team_info_dict()` -> `Dict` - Returns copy of the teams structure.
*   `src/agents/state_manager.py::AgentStateManager.remove_agent_from_all_teams_state(agent_id)` - Cleans up state when an agent is deleted.
*   `src/agents/state_manager.py::AgentStateManager.load_state(teams, agent_to_team)` - Overwrites current state from loaded data.
*   `src/agents/state_manager.py::AgentStateManager.clear_state()` - Clears all team and assignment state.

## **Agent Session Manager (`src/agents/`)**

*   `src/agents/session_manager.py::SessionManager` (Class) - Handles saving/loading of session state. Delegates agent recreation to `agent_lifecycle` module during load. Imports status constants.
*   `src/agents/session_manager.py::SessionManager.__init__(manager, state_manager)` - Initializes session manager.
*   `src/agents/session_manager.py::SessionManager.save_session(project_name, session_name=None)` (Async) -> `Tuple[bool, str]` - Gathers current state (teams, agents, histories) and saves to JSON file.
*   `src/agents/session_manager.py::SessionManager.load_session(project_name, session_name)` (Async) -> `Tuple[bool, str]` - Loads session state from file, clears dynamic state, delegates agent recreation to `agent_lifecycle`, restores histories.

## **Agent Performance Tracker (`src/agents/`)**

*   `src/agents/performance_tracker.py::ModelMetrics` (Class) - Dictionary subclass defining metric structure.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker` (Class) - Tracks model success/failure/latency metrics.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.__init__(metrics_file)` - Initializes tracker, loads metrics synchronously.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker._ensure_data_dir()` (Internal) - Ensures data directory exists.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker._load_metrics_sync()` (Internal) - Synchronously loads metrics from JSON.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.save_metrics()` (Async) - Asynchronously saves current metrics to JSON atomically.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.record_call(provider, model_id, duration_ms, success)` (Async) - Records a single LLM call outcome.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.get_metrics(provider=None, model_id=None)` -> `Dict` - Retrieves metrics, optionally filtered, returns deep copy.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker._calculate_score(stats, min_calls_threshold)` (Internal) -> `float` - Calculates performance score.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.get_ranked_models(provider=None, min_calls=3)` -> `List` - Returns list of models ranked by score.

## **Agent Prompt Utilities (`src/agents/`)**

*   `src/agents/prompt_utils.py::update_agent_prompt_team_id(manager, agent_id, new_team_id)` (Async) - Updates team ID placeholder in a live agent's system prompt state.

## **Agent Interaction Handler (`src/agents/`)**

*   `src/agents/interaction_handler.py::AgentInteractionHandler` (Class) - Handles tool interactions/execution. Imports status constants.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.__init__(manager)` - Initializes interaction handler.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.handle_manage_team_action(action, params, calling_agent_id)` (Async) -> `Tuple[bool, str, Optional[Any]]` - Processes `ManageTeamTool` results, **handles `get_agent_details` action**, checks for duplicate persona, handles idempotent `create_team`. Calls manager methods.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.route_and_activate_agent_message(sender_id, target_identifier, message_content)` (Async) -> `Optional[Task]` - Routes messages. Resolves target by ID then unique persona. Provides feedback on failure/ambiguity. Schedules target agent cycle if idle.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.execute_single_tool(agent, call_id, tool_name, tool_args, project_name, session_name)` (Async) -> `Optional[Dict]` - Executes a single tool via `ToolExecutor`, passes context.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.failed_tool_result(call_id, tool_name)` (Async) -> `Optional[ToolResultDict]` - Generates a formatted error result for failed tool dispatch.

## **Agent Cycle Handler (`src/agents/`)**

*   `src/agents/cycle_handler.py::AgentCycleHandler` (Class) - Handles agent's execution cycle, retries, plan approval, failover triggering. **Injects time context for Admin AI**. Imports status constants.
*   `src/agents/cycle_handler.py::AgentCycleHandler.__init__(manager, interaction_handler)` - Initializes cycle handler.
*   `src/agents/cycle_handler.py::AgentCycleHandler.run_cycle(agent, retry_count)` (Async) - Manages agent's `process_message` loop. Handles `plan_generated` event (auto-approves, reactivates), processes tool results, records metrics, triggers failover, ensures reactivation.

## **Agent Failover Handler (`src/agents/`)**

*   `src/agents/failover_handler.py::handle_agent_model_failover(manager, agent_id, last_error_obj)` (Async) - Standalone function. Handles key cycling (via `ProviderKeyManager`) and model/provider switching logic based on tiers. Imports status constants.
*   `src/agents/failover_handler.py::_select_next_failover_model(manager, agent, already_failed)` (Async Internal) -> `Tuple[Optional[str], Optional[str]]` - Selects next model based on tiers and availability, skipping already failed ones.

## **Provider Key Manager (`src/agents/`)**

*   `src/agents/provider_key_manager.py::ProviderKeyManager` (Class) - Manages API Keys & Quarantine state.
*   `src/agents/provider_key_manager.py::ProviderKeyManager.__init__(provider_api_keys, settings_obj)` - Initializes key manager, loads quarantine state.
*   `src/agents/provider_key_manager.py::ProviderKeyManager._ensure_data_dir()` (Internal) - Ensures data directory exists.
*   `src/agents/provider_key_manager.py::ProviderKeyManager._load_quarantine_state_sync()` (Internal) - Synchronously loads quarantine state from JSON.
*   `src/agents/provider_key_manager.py::ProviderKeyManager.save_quarantine_state()` (Async) - Asynchronously saves current quarantine state to JSON atomically.
*   `src/agents/provider_key_manager.py::ProviderKeyManager._unquarantine_expired_keys_sync()` (Internal) - Removes expired keys from quarantine.
*   `src/agents/provider_key_manager.py::ProviderKeyManager._get_clean_key_value(key_value)` (Internal) -> `Optional[str]` - Cleans key value.
*   `src/agents/provider_key_manager.py::ProviderKeyManager._is_key_quarantined(provider, key_value)` (Internal) -> `bool` - Checks if a specific key is quarantined.
*   `src/agents/provider_key_manager.py::ProviderKeyManager.get_active_key_config(provider)` (Async) -> `Optional[Dict]` - Gets config for the next available, non-quarantined key.
*   `src/agents/provider_key_manager.py::ProviderKeyManager.quarantine_key(provider, key_value, duration_seconds)` (Async) - Marks a key as quarantined.
*   `src/agents/provider_key_manager.py::ProviderKeyManager.is_provider_depleted(provider)` (Async) -> `bool` - Checks if all keys for a provider are quarantined.

## **Agent Lifecycle (`src/agents/`)**

*   `src/agents/agent_lifecycle.py::_select_best_available_model(manager)` (Async Internal) -> `Tuple[Optional[str], Optional[str]]` - Selects best model based on ranking/availability for dynamic agents.
*   `src/agents/agent_lifecycle.py::initialize_bootstrap_agents(manager)` (Async) - Initializes bootstrap agents, calls `_select_best_available_model` for Admin AI if needed, injects XML tool descriptions, refines validation.
*   `src/agents/agent_lifecycle.py::_create_agent_internal(manager, agent_id_requested, agent_config_data, is_bootstrap, team_id, loading_from_session)` (Async Internal) -> `Tuple[bool, str, Optional[str]]` - Core agent creation logic. Calls `_select_best_available_model` if provider/model omitted. Refined validation for provider/model match and format. Injects XML tool descriptions.
*   `src/agents/agent_lifecycle.py::create_agent_instance(manager, agent_id_requested, provider, model, system_prompt, persona, team_id, temperature, **kwargs)` (Async) -> `Tuple[bool, str, Optional[str]]` - Public method for dynamic agents, provider/model now optional.
*   `src/agents/agent_lifecycle.py::delete_agent_instance(manager, agent_id)` (Async) -> `Tuple[bool, str]` - Removes agent and cleans up resources.
*   `src/agents/agent_lifecycle.py::_generate_unique_agent_id(manager, prefix)` -> `str` - Generates unique agent ID.

## **Agent Manager (Coordinator) (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator. Instantiates components. Imports status constants.
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager=None)` - Initializes manager, instantiates handlers, key manager, performance tracker, state/session managers, tool executor.
*   `src/agents/manager.py::AgentManager._ensure_projects_dir()` (Internal) - Ensures projects directory exists.
*   `src/agents/manager.py::AgentManager.initialize_bootstrap_agents()` (Async) - Delegates bootstrap agent initialization to `agent_lifecycle`.
*   `src/agents/manager.py::AgentManager.create_agent_instance(...)` (Async) -> `Tuple` - Delegates dynamic agent creation to `agent_lifecycle`.
*   `src/agents/manager.py::AgentManager.delete_agent_instance(agent_id)` (Async) -> `Tuple` - Delegates agent deletion to `agent_lifecycle`.
*   `src/agents/manager.py::AgentManager.schedule_cycle(agent, retry_count)` (Async) - Schedules agent execution via `cycle_handler`.
*   `src/agents/manager.py::AgentManager.handle_user_message(message, client_id=None)` (Async) - Routes user message to Admin AI, queues if busy.
*   `src/agents/manager.py::AgentManager.handle_agent_model_failover(agent_id, last_error_obj)` (Async) - Delegates failover handling to `failover_handler` function.
*   `src/agents/manager.py::AgentManager.push_agent_status_update(agent_id)` (Async) - Sends agent status update to UI.
*   `src/agents/manager.py::AgentManager.send_to_ui(message_data)` (Async) - Sends message data to UI via broadcast function.
*   `src/agents/manager.py::AgentManager.get_agent_status()` -> `Dict` - Returns dictionary of current agent states.
*   `src/agents/manager.py::AgentManager.save_session(project_name, session_name=None)` (Async) -> `Tuple` - Delegates session saving to `session_manager`.
*   `src/agents/manager.py::AgentManager.load_session(project_name, session_name)` (Async) -> `Tuple` - Delegates session loading to `session_manager`.
*   `src/agents/manager.py::AgentManager.get_agent_info_list_sync(filter_team_id=None)` -> `List[Dict]` - Synchronously gets a list of basic agent info.
*   `src/agents/manager.py::AgentManager.cleanup_providers()` (Async) - Cleans up provider resources, saves metrics and quarantine state.
*   `src/agents/manager.py::AgentManager._close_provider_safe(provider)` (Async Internal) - Safely closes provider session if applicable.

## **LLM Providers Base (`src/llm_providers/`)**

*   `src/llm_providers/base.py::BaseLLMProvider` (Abstract Class) - Base class for all LLM providers.
*   `src/llm_providers/base.py::BaseLLMProvider.__init__(api_key=None, base_url=None, **kwargs)` (Abstract) - Provider initialization signature.
*   `src/llm_providers/base.py::BaseLLMProvider.stream_completion(messages, model, temperature, **kwargs)` (Abstract Async Generator) - Defines signature for streaming completions.
*   `src/llm_providers/base.py::BaseLLMProvider.__repr__()` -> `str` - Basic representation.
*   `src/llm_providers/base.py::BaseLLMProvider.close_session()` (Async Optional) - Optional cleanup method.

## **LLM Providers Implementations (`src/llm_providers/`)**

*   `src/llm_providers/ollama_provider.py::OllamaProvider` (Class) - Implements interaction with Ollama using aiohttp, handles proxy settings, per-request sessions.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.__init__(api_key=None, base_url=None, **kwargs)` - Initializes Ollama provider, determines effective base URL.
*   `src/llm_providers/ollama_provider.py::OllamaProvider._create_request_session()` (Async Internal) -> `aiohttp.ClientSession` - Creates a new session for a request.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.close_session()` (Async) - No-op as sessions are per-request.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.stream_completion(messages, model, temperature, **kwargs)` (Async Generator) - Streams completion from Ollama, handles retries and streaming logic.
*   `src/llm_providers/ollama_provider.py::OllamaProvider._read_response_safe(response)` (Async Internal) -> `str` - Safely reads response text.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.__repr__()` -> `str` - Representation.
*   `src/llm_providers/ollama_provider.py::OllamaProvider.__aenter__()` / `__aexit__()` - Context managers (no-ops).

*   `src/llm_providers/openai_provider.py::OpenAIProvider` (Class) - Implements interaction with OpenAI-compatible APIs using `openai` library.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.__init__(api_key=None, base_url=None, **kwargs)` - Initializes `openai.AsyncOpenAI` client.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.stream_completion(messages, model, temperature, **kwargs)` (Async Generator) - Streams completion, handles retries and stream errors. Ignores tools.
*   `src/llm_providers/openai_provider.py::OpenAIProvider.__repr__()` -> `str` - Representation.

*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider` (Class) - Implements interaction with OpenRouter API using `openai` library.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.__init__(api_key=None, base_url=None, **kwargs)` - Initializes `openai.AsyncOpenAI` client configured for OpenRouter.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.stream_completion(messages, model, temperature, **kwargs)` (Async Generator) - Streams completion, handles retries and stream errors. Ignores tools.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider.__repr__()` -> `str` - Representation.

## **Tools Base (`src/tools/`)**

*   `src/tools/base.py::ToolParameter` (Pydantic Model) - Defines schema for a tool parameter.
*   `src/tools/base.py::BaseTool` (Abstract Class) - Base class for all tools.
*   `src/tools/base.py::BaseTool.execute(agent_id, agent_sandbox_path, project_name=None, session_name=None, **kwargs)` (Abstract Async) - **Signature updated** to include optional project/session context.
*   `src/tools/base.py::BaseTool.get_schema()` -> `Dict` - Returns tool's schema description.

## **Tool Executor (`src/tools/`)**

*   `src/tools/executor.py::ToolExecutor` (Class) - Discovers tools, generates descriptions (XML/JSON), executes tools.
*   `src/tools/executor.py::ToolExecutor.__init__()` - Initializes executor, discovers tools.
*   `src/tools/executor.py::ToolExecutor._register_available_tools()` (Internal) - Dynamically scans and registers tools.
*   `src/tools/executor.py::ToolExecutor.register_tool(tool_instance)` - Manually registers a tool.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_xml()` -> `str` - Generates XML description of tools.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_json()` -> `str` - Generates JSON description of tools.
*   `src/tools/executor.py::ToolExecutor.execute_tool(agent_id, agent_sandbox_path, tool_name, tool_args, project_name=None, session_name=None)` (Async) -> `Any` - Executes a tool, validates args, **passes context**.

## **Tool Implementations (`src/tools/`)**

*   `src/tools/file_system.py::FileSystemTool` (Class) - Tool for file operations. **Added `mkdir` and `delete` actions**.
*   `src/tools/file_system.py::FileSystemTool.execute(...)` (Async) -> `str` - Executes file action based on scope and parameters.
*   `src/tools/file_system.py::FileSystemTool._resolve_and_validate_path(...)` (Async Internal) -> `Optional[Path]` - Resolves and validates path within scope.
*   `src/tools/file_system.py::FileSystemTool._read_file(...)` (Async Internal) -> `str` - Reads file content.
*   `src/tools/file_system.py::FileSystemTool._write_file(...)` (Async Internal) -> `str` - Writes file content.
*   `src/tools/file_system.py::FileSystemTool._list_directory(...)` (Async Internal) -> `str` - Lists directory contents.
*   `src/tools/file_system.py::FileSystemTool._find_replace_in_file(...)` (Async Internal) -> `str` - Finds and replaces text in a file.
*   `src/tools/file_system.py::FileSystemTool._create_directory(...)` (Async Internal) -> `str` - **(NEW)** Creates directory.
*   `src/tools/file_system.py::FileSystemTool._delete_item(...)` (Async Internal) -> `str` - **(NEW)** Deletes file or empty directory.

*   `src/tools/github_tool.py::GitHubTool` (Class) - Tool for GitHub API interaction. **Added `recursive` parameter for `list_files`**.
*   `src/tools/github_tool.py::GitHubTool.__init__()` - Initializes tool, checks for token.
*   `src/tools/github_tool.py::GitHubTool._make_github_request(...)` (Async Internal) -> `Optional[Any]` - Makes authenticated GitHub API request.
*   `src/tools/github_tool.py::GitHubTool._list_repo_recursively(...)` (Async Internal) -> `List[Dict]` - **(NEW)** Recursively lists repo contents.
*   `src/tools/github_tool.py::GitHubTool.execute(...)` (Async) -> `str` - Executes GitHub action (list_repos, list_files, read_file).

*   `src/tools/manage_team.py::ManageTeamTool` (Class) - Tool for agent/team management. **Added `get_agent_details` action**.
*   `src/tools/manage_team.py::ManageTeamTool.execute(...)` (Async) -> `Dict` - Validates action-specific parameters, returns signal dictionary.

*   `src/tools/send_message.py::SendMessageTool` (Class) - Tool for sending messages between agents.
*   `src/tools/send_message.py::SendMessageTool.execute(...)` (Async) -> `str` - Validates parameters, returns confirmation string (routing handled by manager).

*   `src/tools/web_search.py::WebSearchTool` (Class) - Tool for web search. **Added Tavily API support with scraping fallback**.
*   `src/tools/web_search.py::WebSearchTool._search_with_tavily(...)` (Async Internal) -> `Optional[List[Dict]]` - **(NEW)** Searches using Tavily API.
*   `src/tools/web_search.py::WebSearchTool._get_html(...)` (Async Internal) -> `Optional[str]` - Fetches HTML (scraping fallback).
*   `src/tools/web_search.py::WebSearchTool._parse_results(...)` (Async Internal) -> `List[Dict]` - Parses DDG HTML (scraping fallback).
*   `src/tools/web_search.py::WebSearchTool._search_with_scraping(...)` (Async Internal) -> `Optional[List[Dict]]` - **(NEW)** Performs scraping search.
*   `src/tools/web_search.py::WebSearchTool.execute(...)` (Async) -> `str` - Executes search (tries Tavily first).

*   `src/tools/system_help.py::SystemHelpTool` (Class) - **(NEW)** Tool for system info and log search.
*   `src/tools/system_help.py::SystemHelpTool.execute(...)` (Async) -> `str` - Executes 'get_time' or 'search_logs'.
*   `src/tools/system_help.py::SystemHelpTool._search_logs_safe(...)` (Async Internal) -> `List[str] | str` - **(NEW)** Safely searches the latest log file.

## **Frontend Logic (`static/js/app.js`)**

*   (JavaScript functions are not typically included in a backend Python functions index, but key UI functionalities are handled here: WebSocket connection, message display, UI updates based on backend events, form handling, modal control, view switching, session management interaction).

---
