<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index (v2.25)

This file tracks the core functions/methods defined within the TrippleEffect framework (as of v2.25), categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages startup/shutdown. Initializes `DatabaseManager`, starts/stops Ollama proxy (optional), instantiates `AgentManager`, runs `ModelRegistry.discover_models_and_providers()`, initializes bootstrap agents via `AgentManager`, calls `agent_manager.cleanup_providers()` on shutdown.
*   `src/main.py` (Script execution block) - Loads .env, configures logging, creates FastAPI app instance with lifespan, mounts static files, includes API routers, runs Uvicorn server.

## **Configuration (`src/config/`)**

*   `src/config/config_manager.py::ConfigManager` (Class) - Manages reading/writing of `config.yaml` (bootstrap agents, teams, etc.). (Note: `settings.py` uses `ConfigManager` for the initial load of `config.yaml`. API routes also use `ConfigManager` to update `config.yaml` at runtime; however, these runtime changes require an application restart to be reflected in the active `settings` object and agent behaviors derived from it.)
*   `src/config/model_registry.py::ModelInfo` (Class) - Simple dictionary subclass for type hinting.
*   `src/config/model_registry.py::ModelRegistry` (Class) - Handles discovery, filtering, storage of available models from reachable providers (Ollama, OpenRouter, OpenAI).
*   `src/config/model_registry.py::ModelRegistry.__init__(settings_obj)` - Initializes registry with settings.
*   `src/config/model_registry.py::ModelRegistry.discover_models_and_providers()` (Async) - Coordinates provider reachability checks and model discovery.
*   `src/config/model_registry.py::ModelRegistry._discover_providers()` (Async Internal) - Checks reachability of configured or local providers.
*   `src/config/model_registry.py::ModelRegistry._check_local_provider_prioritized(...)` (Async Internal) - Checks local provider reachability.
*   `src/config/model_registry.py::ModelRegistry._check_single_local_url(...)` (Async Internal) -> `bool` - Checks reachability for a single local URL.
*   `src/config/model_registry.py::ModelRegistry._discover_openrouter_models()` (Async Internal) - Fetches models from OpenRouter API.
*   `src/config/model_registry.py::ModelRegistry._discover_ollama_models()` (Async Internal) - Fetches models from Ollama API.
*   `src/config/model_registry.py::ModelRegistry._discover_openai_models()` (Async Internal) - Manually adds common OpenAI models.
*   `src/config/model_registry.py::ModelRegistry._apply_filters()` (Internal) - Filters raw models based on reachability and `MODEL_TIER`.
*   `src/config/model_registry.py::ModelRegistry.get_available_models_list(provider=None)` -> `List[str]` - Returns sorted list of available model IDs.
*   `src/config/model_registry.py::ModelRegistry.get_available_models_dict()` -> `Dict` - Returns a deep copy of available models dictionary.
*   `src/config/model_registry.py::ModelRegistry.find_provider_for_model(model_id)` -> `Optional[str]` - Finds the provider for a given model ID.
*   `src/config/model_registry.py::ModelRegistry.get_formatted_available_models()` -> `str` - Returns a formatted string listing available models.
*   `src/config/model_registry.py::ModelRegistry.is_model_available(provider, model_id)` -> `bool` - Checks if a specific model is available.
*   `src/config/model_registry.py::ModelRegistry.get_reachable_provider_url(provider)` -> `Optional[str]` - Returns the base URL for a reachable provider.
*   `src/config/model_registry.py::ModelRegistry._log_available_models()` (Internal) - Logs the final available models.
*   `src/config/settings.py::Settings` (Class) - Holds settings loaded from `.env`, `prompts.json`, and initial `config.yaml`. Instantiates `ModelRegistry`.
*   `src/config/settings.py::Settings.__init__()` - Initializes settings, loads env vars, prompts, initial config, checks required keys.
*   `src/config/settings.py::Settings._load_prompts_from_json()` (Internal) - Loads prompts from `prompts.json`.
*   `src/config/settings.py::Settings._ensure_projects_dir()` (Internal) - Creates projects directory.
*   `src/config/settings.py::Settings._check_required_keys()` (Internal) - Validates provider keys/URLs and logs status.
*   `src/config/settings.py::Settings.get_provider_config(provider_name)` -> `Dict` - Gets base config (URL, referer) for a provider.
*   `src/config/settings.py::Settings.is_provider_configured(provider_name)` -> `bool` - Checks if provider has essential config.
*   `src/config/settings.py::Settings.get_agent_config_by_id(agent_id)` -> `Optional[Dict]` - Retrieves bootstrap agent's config from initial load.
*   `src/config/settings.py::Settings.get_formatted_allowed_models()` -> `str` - Delegates to `model_registry`.
*   `src/config/settings.py::settings` (Instance) - Singleton settings instance.
*   `src/config/settings.py::model_registry` (Instance) - Singleton ModelRegistry instance.

## **API Routes (`src/api/`)**

*   `src/api/http_routes.py::get_agent_manager_dependency(request: Request)` -> `'AgentManager'` - FastAPI dependency retrieving shared AgentManager from `app.state`.
*   `src/api/http_routes.py::get_index_page(request: Request)` (Async) -> `HTMLResponse` - Serves the main index.html page.
*   `src/api/http_routes.py::get_agent_configurations()` (Async) -> `List[AgentInfo]` - Retrieves list of static agent configs from `settings.BOOTSTRAP_AGENTS_CONFIG`.
*   `src/api/http_routes.py::create_agent_configuration(agent_data: AgentConfigCreate)` (Async) -> `GeneralResponse` - (Note: Modifies `config.yaml` via `ConfigManager`, but core logic uses `settings` load. Requires restart for changes to take effect).
*   `src/api/http_routes.py::update_agent_configuration(agent_id, agent_config_data)` (Async) -> `GeneralResponse` - (Note: Modifies `config.yaml` via `ConfigManager`. Requires restart).
*   `src/api/http_routes.py::delete_agent_configuration(agent_id)` (Async) -> `GeneralResponse` - (Note: Modifies `config.yaml` via `ConfigManager`. Requires restart).
*   `src/api/http_routes.py::list_projects()` (Async) -> `List[ProjectInfo]` - Lists projects by scanning the projects base directory.
*   `src/api/http_routes.py::list_sessions(project_name)` (Async) -> `List[SessionInfo]` - Lists sessions within a project by looking for session files.
*   `src/api/http_routes.py::save_current_session(project_name, session_input, manager)` (Async) -> `GeneralResponse` - Saves the current session state via `AgentManager.save_session`.
*   `src/api/http_routes.py::load_specific_session(project_name, session_name, manager)` (Async) -> `GeneralResponse` - Loads a session state via `AgentManager.load_session`.
*   `src/api/http_routes.py::approve_project_start(pm_agent_id: str, manager: AgentManager)` (Async) -> `GeneralResponse` - **(NEW)** API endpoint called by UI to approve project start, schedules the PM agent.

## **WebSocket Management (`src/api/`)**

*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level function to inject shared `AgentManager`.
*   `src/api/websocket_manager.py::broadcast(message: str)` (Async) - Sends message string to all connected WebSocket clients. Handles disconnects.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` (Async) - Main WebSocket handler. Accepts connections, forwards incoming messages (plain text or JSON commands like `submit_user_override`, `user_message_with_file`) to `AgentManager` via `asyncio.create_task`.

## **Database (`src/core/`)**

*   `src/core/database_manager.py::DatabaseManager` (Class) - Singleton class managing SQLite database interactions (projects, sessions, interactions, agents, knowledge).
*   `src/core/database_manager.py::DatabaseManager._initialize_db()` (Async Internal) - Creates tables if they don't exist. Called during app lifespan startup.
*   `src/core/database_manager.py::DatabaseManager.get_session()` (Async Context Manager) - Provides an async database session.
*   `src/core/database_manager.py::DatabaseManager.add_project(name)` (Async) -> `DBProject` - Adds or gets a project record.
*   `src/core/database_manager.py::DatabaseManager.get_project_by_name(name)` (Async) -> `Optional[DBProject]` - Retrieves a project record by name.
*   `src/core/database_manager.py::DatabaseManager.start_session(project_id, name)` (Async) -> `DBSession` - Creates a new session record.
*   `src/core/database_manager.py::DatabaseManager.end_session(session_id)` (Async) - Updates the end time of a session record.
*   `src/core/database_manager.py::DatabaseManager.get_session_id_by_name(project_id, name)` (Async) -> `Optional[int]` - Finds a session ID by project and name.
*   `src/core/database_manager.py::DatabaseManager.add_agent_record(session_id, agent_id, persona, model_config_dict)` (Async) - Logs the creation of an agent instance for a session.
*   `src/core/database_manager.py::DatabaseManager.log_interaction(session_id, agent_id, role, content, tool_calls=None, tool_results=None)` (Async) - Logs user messages, agent responses, tool usage, errors, etc.
*   `src/core/database_manager.py::DatabaseManager.save_knowledge(entry)` (Async) -> `bool` - Saves an entry to the knowledge base table.
*   `src/core/database_manager.py::DatabaseManager.search_knowledge(query, tags=None, limit=5)` (Async) -> `List[KnowledgeEntry]` - Searches the knowledge base.
*   `src/core/database_manager.py::close_db_connection()` (Async) - Closes the database engine connection pool. Called during app lifespan shutdown.
*   `src/core/database_manager.py::db_manager` (Instance) - Singleton DatabaseManager instance.

## **Agent Constants (`src/agents/`)**

*   `src/agents/constants.py` - Defines constants for agent operational statuses (`AGENT_STATUS_*`), workflow states (`ADMIN_STATE_*`, `PM_STATE_*`, `WORKER_STATE_*`), agent types (`AGENT_TYPE_*`), retry/failover logic, and regex patterns.

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents individual LLM agent. Parses XML tool calls, detects plans/thoughts/state requests.
*   `src/agents/core.py::Agent.__init__(agent_config, llm_provider, manager)` - Initializes agent state, message history, compiles regex patterns. Sets `agent_type`.
*   `src/agents/core.py::Agent.set_status(new_status, tool_info=None, plan_info=None)` - Updates operational status, clears/sets `current_tool_info` or `current_plan`, pushes UI update.
*   `src/agents/core.py::Agent.set_state(new_state)` - **(NEW)** Updates agent's workflow state (e.g., `conversation`, `planning`).
*   `src/agents/core.py::Agent.ensure_sandbox_exists()` -> `bool` - Creates the agent's sandbox directory.
*   `src/agents/core.py::Agent.process_message(history_override=None)` (Async Generator) - Main agent processing loop. Calls LLM provider, processes stream, detects `<request_state>`, `<think>`, `<plan>` tags, calls external `find_and_parse_xml_tool_calls`, yields events (`response_chunk`, `agent_state_change_requested`, `agent_thought`, `admin_plan_submitted`, `tool_requests`, `final_response`, `error`).
*   `src/agents/core.py::Agent.get_state()` -> `Dict` - Returns agent state, includes operational status, workflow state, type, etc.
*   `src/agents/core.py::Agent.clear_history()` - Clears message history, keeps system prompt.

## **Agent Tool Parser (`src/agents/`)**

*   `src/agents/agent_tool_parser.py::find_and_parse_xml_tool_calls(text_buffer, tools, raw_xml_pattern, markdown_xml_pattern, agent_id)` -> `List` - Standalone function to find/parse XML tool calls (raw/fenced).

## **Agent State Manager (`src/agents/`)**

*   `src/agents/state_manager.py::AgentStateManager` (Class) - Manages dynamic team/agent assignment state in memory.
*   `src/agents/state_manager.py::AgentStateManager.__init__(manager)` - Initializes state manager.
*   `src/agents/state_manager.py::AgentStateManager.create_new_team(team_id)` (Async) -> `Tuple[bool, str]` - Creates a new team (idempotent).
*   `src/agents/state_manager.py::AgentStateManager.delete_existing_team(team_id)` (Async) -> `Tuple[bool, str]` - Deletes an empty team.
*   `src/agents/state_manager.py::AgentStateManager.add_agent_to_team(agent_id, team_id)` (Async) -> `Tuple[bool, str]` - Adds agent to team state.
*   `src/agents/state_manager.py::AgentStateManager.remove_agent_from_team(agent_id, team_id)` (Async) -> `Tuple[bool, str]` - Removes agent from team state.
*   `src/agents/state_manager.py::AgentStateManager.get_agent_team(agent_id)` -> `Optional[str]` - Gets team ID for an agent.
*   `src/agents/state_manager.py::AgentStateManager.get_team_members(team_id)` -> `Optional[List[str]]` - Gets list of agent IDs in a team.
*   `src/agents/state_manager.py::AgentStateManager.get_agents_in_team(team_id)` -> `List[Agent]` - Gets actual Agent instances belonging to a team.
*   `src/agents/state_manager.py::AgentStateManager.get_team_info_dict()` -> `Dict` - Returns copy of the teams structure.
*   `src/agents/state_manager.py::AgentStateManager.remove_agent_from_all_teams_state(agent_id)` - Cleans up state when an agent is deleted.
*   `src/agents/state_manager.py::AgentStateManager.load_state(teams, agent_to_team)` - Overwrites current state from loaded data.
*   `src/agents/state_manager.py::AgentStateManager.clear_state()` - Clears all team and assignment state.

## **Agent Session Manager (`src/agents/`)**

*   `src/agents/session_manager.py::SessionManager` (Class) - Handles saving/loading of session state (agents, teams, histories, Taskwarrior data) to/from filesystem.
*   `src/agents/session_manager.py::SessionManager.__init__(manager, state_manager)` - Initializes session manager.
*   `src/agents/session_manager.py::SessionManager.save_session(project_name, session_name=None)` (Async) -> `Tuple[bool, str]` - Gathers current state and saves to JSON file, including Taskwarrior data.
*   `src/agents/session_manager.py::SessionManager.load_session(project_name, session_name)` (Async) -> `Tuple[bool, str]` - Loads session state from file, clears dynamic state, delegates agent recreation to `agent_lifecycle`, restores histories, loads Taskwarrior data.

## **Agent Performance Tracker (`src/agents/`)**

*   `src/agents/performance_tracker.py::ModelMetrics` (Class) - Dictionary subclass defining metric structure.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker` (Class) - Tracks model success/failure/latency metrics.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.__init__(metrics_file)` - Initializes tracker, loads metrics.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker._ensure_data_dir()` (Internal) - Ensures data directory exists.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker._load_metrics_sync()` (Internal) - Synchronously loads metrics from JSON.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.save_metrics()` (Async) - Asynchronously saves current metrics to JSON.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.record_call(provider, model_id, duration_ms, success)` (Async) - Records a single LLM call outcome.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.get_metrics(provider=None, model_id=None)` -> `Dict` - Retrieves metrics.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker._calculate_score(stats, min_calls_threshold)` (Internal) -> `float` - Calculates performance score.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.get_ranked_models(provider=None, min_calls=3)` -> `List` - Returns list of models ranked by score.

## **Agent Prompt Utilities (`src/agents/`)**

*   `src/agents/prompt_utils.py::update_agent_prompt_team_id(manager, agent_id, new_team_id)` (Async) - Updates team ID placeholder in a live agent's system prompt state.

## **Agent Interaction Handler (`src/agents/`)**

*   `src/agents/interaction_handler.py::AgentInteractionHandler` (Class) - Handles processing of specific tool interactions (ManageTeam, SendMessage) and execution of all tools via ToolExecutor.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.__init__(manager)` - Initializes interaction handler.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.handle_manage_team_action(action, params, calling_agent_id)` (Async) -> `Tuple[bool, str, Optional[Any]]` - Processes `ManageTeamTool` results, handles `get_agent_details`, checks duplicates, calls manager methods.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.route_and_activate_agent_message(sender_id, target_identifier, message_content)` (Async) -> `Optional[Task]` - Routes messages between agents, resolves target, checks permissions, schedules target cycle if idle.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.execute_single_tool(agent, call_id, tool_name, tool_args, project_name, session_name)` (Async) -> `Optional[Dict]` - Executes a single tool via `ToolExecutor`, passes context.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.failed_tool_result(call_id, tool_name)` (Async) -> `Optional[ToolResultDict]` - Generates a formatted error result for failed tool dispatch.

## **Agent Cycle Handler (`src/agents/`)**

*   `src/agents/cycle_handler.py::AgentCycleHandler` (Class) - Handles agent's execution cycle, retries, failover triggering, state/plan/tool processing.
*   `src/agents/cycle_handler.py::AgentCycleHandler.__init__(manager, interaction_handler)` - Initializes cycle handler.
*   `src/agents/cycle_handler.py::AgentCycleHandler._generate_system_health_report(agent)` (Async Internal) -> `Optional[str]` - Generates system health report for Admin AI based on recent history.
*   `src/agents/cycle_handler.py::AgentCycleHandler.run_cycle(agent, retry_count)` (Async) - Manages agent's `process_message` loop. Gets state-specific prompt via `WorkflowManager`, injects health report (Admin), handles events (`response_chunk`, `error`, `tool_requests`, `admin_plan_submitted`, `agent_state_change_requested`, `agent_thought`), processes tool results via `InteractionHandler`, records metrics, triggers failover, ensures reactivation.

## **Agent Failover Handler (`src/agents/`)**

*   `src/agents/failover_handler.py::handle_agent_model_failover(manager, agent_id, last_error_obj)` (Async) - Standalone function. Handles key cycling (via `ProviderKeyManager`) and model/provider switching logic based on tiers.
*   `src/agents/failover_handler.py::_select_next_failover_model(manager, agent, already_failed)` (Async Internal) -> `Tuple[Optional[str], Optional[str]]` - Selects next model based on tiers and availability.

## **Provider Key Manager (`src/agents/`)**

*   `src/agents/provider_key_manager.py::ProviderKeyManager` (Class) - Manages API Keys & Quarantine state.
*   `src/agents/provider_key_manager.py::ProviderKeyManager.__init__(provider_api_keys, settings_obj)` - Initializes key manager, loads quarantine state.
*   `src/agents/provider_key_manager.py::ProviderKeyManager._ensure_data_dir()` (Internal) - Ensures data directory exists.
*   `src/agents/provider_key_manager.py::ProviderKeyManager._load_quarantine_state_sync()` (Internal) - Synchronously loads quarantine state from JSON.
*   `src/agents/provider_key_manager.py::ProviderKeyManager.save_quarantine_state()` (Async) - Asynchronously saves current quarantine state to JSON.
*   `src/agents/provider_key_manager.py::ProviderKeyManager._unquarantine_expired_keys_sync()` (Internal) - Removes expired keys from quarantine.
*   `src/agents/provider_key_manager.py::ProviderKeyManager._get_clean_key_value(key_value)` (Internal) -> `Optional[str]` - Cleans key value.
*   `src/agents/provider_key_manager.py::ProviderKeyManager._is_key_quarantined(provider, key_value)` (Internal) -> `bool` - Checks if a specific key is quarantined.
*   `src/agents/provider_key_manager.py::ProviderKeyManager.get_active_key_config(provider)` (Async) -> `Optional[Dict]` - Gets config for the next available, non-quarantined key.
*   `src/agents/provider_key_manager.py::ProviderKeyManager.quarantine_key(provider, key_value, duration_seconds)` (Async) - Marks a key as quarantined.
*   `src/agents/provider_key_manager.py::ProviderKeyManager.is_provider_depleted(provider)` (Async) -> `bool` - Checks if all keys for a provider are quarantined.

## **Agent Lifecycle (`src/agents/`)**

*   `src/agents/agent_lifecycle.py::_select_best_available_model(manager)` (Async Internal) -> `Tuple[Optional[str], Optional[str]]` - Selects best model based on ranking/availability.
*   `src/agents/agent_lifecycle.py::initialize_bootstrap_agents(manager)` (Async) - Initializes bootstrap agents from settings, selects model for Admin AI if needed, sets initial state via `WorkflowManager`.
*   `src/agents/agent_lifecycle.py::_create_agent_internal(manager, agent_id_requested, agent_config_data, is_bootstrap, team_id, loading_from_session)` (Async Internal) -> `Tuple[bool, str, Optional[str]]` - Core agent creation logic. Selects model if needed, creates `Agent` instance, sets initial state.
*   `src/agents/agent_lifecycle.py::create_agent_instance(manager, agent_id_requested, provider, model, system_prompt, persona, team_id, temperature, **kwargs)` (Async) -> `Tuple[bool, str, Optional[str]]` - Public method for dynamic agents.
*   `src/agents/agent_lifecycle.py::delete_agent_instance(manager, agent_id)` (Async) -> `Tuple[bool, str]` - Removes agent and cleans up resources (state manager).
*   `src/agents/agent_lifecycle.py::_generate_unique_agent_id(manager, prefix)` -> `str` - Generates unique agent ID.

## **Agent Workflow Manager (`src/agents/`)**

*   `src/agents/workflow_manager.py::AgentWorkflowManager` (Class) - **(NEW)** Manages agent workflow states, transitions, and state-specific prompt selection.
*   `src/agents/workflow_manager.py::AgentWorkflowManager.__init__()` - Initializes valid states per agent type and prompt mapping.
*   `src/agents/workflow_manager.py::AgentWorkflowManager.is_valid_state(agent_type, state)` -> `bool` - Checks if a state is valid for an agent type.
*   `src/agents/workflow_manager.py::AgentWorkflowManager.change_state(agent, requested_state)` -> `bool` - Attempts to change agent's state, validating against allowed states.
*   `src/agents/workflow_manager.py::AgentWorkflowManager.get_system_prompt(agent, manager)` -> `str` - Gets the appropriate system prompt based on agent type/state, formats with context (incl. time, tools), prepends user-defined part for Admin AI in specific states.

## **Agent Manager (Coordinator) (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator. Instantiates components.
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager=None)` - Initializes manager, instantiates handlers, managers, tracker, executor. Instantiates `AgentWorkflowManager`. Starts default DB session task.
*   `src/agents/manager.py::AgentManager._ensure_default_db_session()` (Async Internal) - Ensures default project/session exists in DB on startup.
*   `src/agents/manager.py::AgentManager.set_project_session_context(project_name, session_name, loading=False)` (Async) - Sets current project/session context and updates DB records.
*   `src/agents/manager.py::AgentManager._ensure_projects_dir()` (Internal) - Ensures projects directory exists.
*   `src/agents/manager.py::AgentManager.initialize_bootstrap_agents()` (Async) - Delegates bootstrap agent initialization to `agent_lifecycle`, logs agents to DB.
*   `src/agents/manager.py::AgentManager.create_agent_instance(...)` (Async) -> `Tuple` - Delegates dynamic agent creation to `agent_lifecycle`, logs agent to DB.
*   `src/agents/manager.py::AgentManager.delete_agent_instance(agent_id)` (Async) -> `Tuple` - Delegates agent deletion to `agent_lifecycle`.
*   `src/agents/manager.py::AgentManager.schedule_cycle(agent, retry_count)` (Async) - Schedules agent execution via `cycle_handler` using `asyncio.create_task`.
*   `src/agents/manager.py::AgentManager.handle_user_message(message, client_id=None)` (Async) - Routes user message to Admin AI, logs to DB, queues if busy. Ensures DB context exists.
*   `src/agents/manager.py::AgentManager.handle_agent_model_failover(agent_id, last_error_obj)` (Async) - Delegates failover handling to `failover_handler` function.
*   `src/agents/manager.py::AgentManager.push_agent_status_update(agent_id)` (Async) - Sends agent status update to UI.
*   `src/agents/manager.py::AgentManager.send_to_ui(message_data)` (Async) - Sends message data to UI via broadcast function.
*   `src/agents/manager.py::AgentManager.get_agent_status()` -> `Dict` - Returns dictionary of current agent states.
*   `src/agents/manager.py::AgentManager.save_session(project_name, session_name=None)` (Async) -> `Tuple` - Delegates session saving to `session_manager`, updates DB context, auto-creates PM agent if needed.
*   `src/agents/manager.py::AgentManager.load_session(project_name, session_name)` (Async) -> `Tuple` - Delegates session loading to `session_manager`, updates DB context.
*   `src/agents/manager.py::AgentManager.create_project_and_pm_agent(project_title, plan_description)` (Async) -> `Tuple[bool, str, Optional[str]]` - Handles automatic creation of PM agent (via `create_agent_instance`) and initial project task (via `ToolExecutor` calling `ProjectManagementTool`), notifies UI for approval.
*   `src/agents/manager.py::AgentManager.get_agent_info_list_sync(filter_team_id=None)` -> `List[Dict]` - Synchronously gets a list of basic agent info.
*   `src/agents/manager.py::AgentManager.cleanup_providers()` (Async) - Ends final DB session, cleans up provider resources, saves metrics and quarantine state.
*   `src/agents/manager.py::AgentManager._close_provider_safe(provider)` (Async Internal) - Safely closes provider session if applicable.

## **LLM Providers Base (`src/llm_providers/`)**

*   `src/llm_providers/base.py::BaseLLMProvider` (Abstract Class) - Base class for all LLM providers.
*   `src/llm_providers/base.py::BaseLLMProvider.__init__(...)` (Abstract) - Provider initialization signature.
*   `src/llm_providers/base.py::BaseLLMProvider.stream_completion(...)` (Abstract Async Generator) - Defines signature for streaming completions.
*   `src/llm_providers/base.py::BaseLLMProvider.__repr__()` -> `str` - Basic representation.
*   `src/llm_providers/base.py::BaseLLMProvider.close_session()` (Async Optional) - Optional cleanup method.

## **LLM Providers Implementations (`src/llm_providers/`)**

*   `src/llm_providers/ollama_provider.py::OllamaProvider` (Class) - Implements interaction with Ollama using aiohttp.
*   `src/llm_providers/openai_provider.py::OpenAIProvider` (Class) - Implements interaction with OpenAI-compatible APIs using `openai` library.
*   `src/llm_providers/openrouter_provider.py::OpenRouterProvider` (Class) - Implements interaction with OpenRouter API using `openai` library.

## **Tools Base (`src/tools/`)**

*   `src/tools/base.py::ToolParameter` (Pydantic Model) - Defines schema for a tool parameter.
*   `src/tools/base.py::BaseTool` (Abstract Class) - Base class for all tools. Defines `name`, `description`, `parameters`, `auth_level`.
*   `src/tools/base.py::BaseTool.execute(...)` (Abstract Async) - Execution signature.
*   `src/tools/base.py::BaseTool.get_schema()` -> `Dict` - Returns tool's schema description.
*   `src/tools/base.py::BaseTool.get_detailed_usage()` -> `str` - Returns detailed usage string (optional override).

## **Tool Executor (`src/tools/`)**

*   `src/tools/executor.py::ToolExecutor` (Class) - Discovers tools, generates descriptions (XML/JSON), executes tools with authorization.
*   `src/tools/executor.py::ToolExecutor.__init__()` - Initializes executor, discovers tools.
*   `src/tools/executor.py::ToolExecutor._register_available_tools()` (Internal) - Dynamically scans and registers tools.
*   `src/tools/executor.py::ToolExecutor.register_tool(tool_instance)` - Manually registers a tool.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_xml()` -> `str` - Generates XML description of tools.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_json()` -> `str` - Generates JSON description of tools.
*   `src/tools/executor.py::ToolExecutor.execute_tool(agent_id, agent_sandbox_path, tool_name, tool_args, project_name=None, session_name=None, manager=None)` (Async) -> `Any` - Performs authorization check based on agent type and tool `auth_level`, validates args, executes tool, passes context (incl. manager for specific tools), handles result formatting.

## **Tool Implementations (`src/tools/`)**

*   `src/tools/file_system.py::FileSystemTool` (Class) - Tool for file operations (read, write, list, find_replace, mkdir, delete). `auth_level: worker`.
*   `src/tools/github_tool.py::GitHubTool` (Class) - Tool for GitHub API interaction (list_repos, list_files, read_file). `auth_level: worker`.
*   `src/tools/knowledge_base.py::KnowledgeBaseTool` (Class) - Tool for saving/searching knowledge in the SQLite DB. `auth_level: worker`.
*   `src/tools/manage_team.py::ManageTeamTool` (Class) - Tool for agent/team management (create/delete agent/team, add/remove agent, list agents/teams, get_agent_details). `auth_level: admin`.
*   `src/tools/project_management.py::ProjectManagementTool` (Class) - Tool for managing project tasks using `tasklib`. `auth_level: pm`.
*   `src/tools/project_management.py::ProjectManagementTool._get_taskwarrior_instance(...)` (Internal) -> `Optional[TaskWarrior]` - Initializes TaskWarrior with session-specific data path, ensures `.taskrc` exists.
*   `src/tools/project_management.py::ProjectManagementTool.execute(...)` (Async) -> `Dict` - Executes task actions (add_task, list_tasks, modify_task, complete_task). `add_task` uses CLI with tags/UDA for assignee. `list_tasks` extracts assignee from tags.
*   `src/tools/send_message.py::SendMessageTool` (Class) - Tool for sending messages between agents. `auth_level: worker`.
*   `src/tools/system_help.py::SystemHelpTool` (Class) - Tool for getting time, searching logs, getting tool info. `auth_level: worker`. Requires `manager` passed to `execute`.
*   `src/tools/tool_information.py::ToolInformationTool` (Class) - Tool for getting detailed usage of other tools. `auth_level: worker`. Requires `manager` passed to `execute`.
*   `src/tools/web_search.py::WebSearchTool` (Class) - Tool for web search (Tavily API w/ DDG scraping fallback). `auth_level: worker`.

## **Utilities (`src/utils/`)**

*   `src/utils/network_utils.py::find_available_port(start_port, end_port)` -> `Optional[int]` - Finds an available network port.
*   `src/utils/network_utils.py::is_port_in_use(port)` -> `bool` - Checks if a port is currently in use.
*   `src/utils/network_utils.py::discover_local_api_endpoints(subnets, port, path, scheme)` (Async) -> `List[str]` - Attempts to discover local API endpoints (e.g., Ollama) on specified subnets.

## **Frontend Logic (`static/js/`)**

*   (Key frontend files: `main.js`, `handlers.js`, `websocket.js`, `ui.js`, `api.js`, `state.js`, `domElements.js`, `session.js`, `configView.js`. These handle UI rendering, event handling, WebSocket communication, state management, and API calls for configuration/session management).

---
<!-- # END OF FILE helperfiles/FUNCTIONS_INDEX.md -->
