<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages startup/shutdown. Instantiates `AgentManager`, runs `ModelRegistry.discover_models_and_providers()`, initializes bootstrap agents, calls `agent_manager.cleanup_providers()` (which now also saves metrics).
*   `src/main.py` (Script execution block) - Loads .env, configures logging, creates FastAPI app, runs Uvicorn.

## **Configuration (`src/config/`)**

*   `src/config/config_manager.py::ConfigManager` (Class) - Manages reading/writing of `config.yaml` (bootstrap agents).
*   (Other ConfigManager methods remain the same, focusing on bootstrap agent definitions)

*   `src/config/model_registry.py::ModelRegistry` (Class) - Handles discovery, filtering, storage of available models from reachable providers.
*   `src/config/model_registry.py::ModelRegistry.__init__(settings_obj)` - Initializes registry, needs `settings` instance. Reads `MODEL_TIER`.
*   `src/config/model_registry.py::ModelRegistry.discover_models_and_providers()` (Async) - Main entry point for discovery. Checks provider reachability, then discovers models.
*   `src/config/model_registry.py::ModelRegistry._discover_providers()` (Async Internal) - Checks provider reachability (env URLs, localhost).
*   `src/config/model_registry.py::ModelRegistry._check_local_provider(...)` (Async Internal) - Checks specific local provider reachability.
*   `src/config/model_registry.py::ModelRegistry._discover_openrouter_models()` (Async Internal) - Fetches models from OpenRouter API.
*   `src/config/model_registry.py::ModelRegistry._discover_ollama_models()` (Async Internal) - Fetches models from Ollama API.
*   `src/config/model_registry.py::ModelRegistry._discover_litellm_models()` (Async Internal) - Fetches models from LiteLLM API.
*   `src/config/model_registry.py::ModelRegistry._apply_filters()` (Internal) - Filters raw models based on reachability and `MODEL_TIER`.
*   `src/config/model_registry.py::ModelRegistry.get_available_models_list(...)` -> `List[str]` - Returns flat list of available model IDs (local prioritized).
*   `src/config/model_registry.py::ModelRegistry.get_available_models_dict()` -> `Dict` - Returns dict of available models by provider.
*   `src/config/model_registry.py::ModelRegistry.find_provider_for_model(...)` -> `Optional[str]` - Finds the provider for a model ID.
*   `src/config/model_registry.py::ModelRegistry.get_formatted_available_models()` -> `str` - Returns formatted string list for prompts.
*   `src/config/model_registry.py::ModelRegistry.is_model_available(...)` -> `bool` - Checks if a specific model is available.
*   `src/config/model_registry.py::ModelRegistry.get_reachable_provider_url(...)` -> `Optional[str]` - Gets the confirmed URL for a provider.
*   `src/config/model_registry.py::ModelRegistry._log_available_models()` (Internal) - Logs available models.

*   `src/config/settings.py::Settings` (Class) - Holds settings from `.env` and initial `config.yaml`. **Instantiates `ModelRegistry` after loading.**
*   `src/config/settings.py::Settings.__init__()` - Initializes settings, loads env vars, initial config.
*   `src/config/settings.py::Settings._ensure_projects_dir()` - Creates projects directory.
*   `src/config/settings.py::Settings._check_required_keys()` - Validates provider keys/URLs.
*   `src/config/settings.py::Settings.get_provider_config(...)` -> `Dict` - Gets base config for a provider.
*   `src/config/settings.py::Settings.is_provider_configured(...)` -> `bool` - Checks if provider has essential config.
*   `src/config/settings.py::Settings.get_agent_config_by_id(...)` -> `Optional[Dict]` - Retrieves bootstrap agent's config.
*   `src/config/settings.py::Settings.get_formatted_allowed_models()` -> `str` - Delegates to `ModelRegistry`.
*   `src/config/settings.py::settings` (Instance) - Singleton settings instance.
*   `src/config/settings.py::model_registry` (Instance) - Singleton ModelRegistry instance.

## **API Routes (`src/api/`)**

*   (No functional changes in this phase - static config endpoints remain but might be less relevant)
*   `src/api/http_routes.py::get_agent_manager_dependency(request: Request)` -> `'AgentManager'` - Retrieves shared AgentManager.
*   (Other HTTP routes remain for index, static config CRUD, project/session list/load/save)

## **WebSocket Management (`src/api/`)**

*   (No changes to function signatures or primary roles in this phase)
*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Injects shared `AgentManager`.
*   `src/api/websocket_manager.py::broadcast(message: str)` (Async) - Sends message to all connections.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` (Async) - Handler for `/ws`. **No longer handles `submit_user_override` messages.**

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents individual LLM agent.
*   `src/agents/core.py::Agent.__init__(...)` - Initializes agent. **Added `_failed_models_this_cycle` attribute placeholder.**
*   (Other Agent methods remain the same: `set_status`, `set_manager`, `ensure_sandbox_exists`, `_find_and_parse_tool_calls`, `process_message`, `get_state`, `clear_history`)

## **Agent State Manager (`src/agents/`)**

*   (No changes in this phase)
*   `src/agents/state_manager.py::AgentStateManager` (Class) - Manages dynamic team/agent assignment state.
*   (All methods remain the same)

## **Agent Session Manager (`src/agents/`)**

*   (No changes in this phase)
*   `src/agents/session_manager.py::SessionManager` (Class) - Handles saving/loading of session state.
*   (All methods remain the same)

## **Agent Performance Tracker (`src/agents/`)**

*   `src/agents/performance_tracker.py::ModelPerformanceTracker` (Class) - **(NEW)** Tracks model success/failure/latency metrics.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.__init__(metrics_file)` - **(NEW)** Initializes tracker, loads metrics from JSON file.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker._ensure_data_dir()` (Internal) - **(NEW)** Creates data directory.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker._load_metrics_sync()` (Internal) - **(NEW)** Sync loads metrics from file.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.save_metrics()` (Async) - **(NEW)** Async saves current metrics to JSON file.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.record_call(provider, model_id, duration_ms, success)` (Async) - **(NEW)** Records the outcome of an LLM call.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.get_metrics(...)` -> `Dict` - **(NEW)** Retrieves metrics (optionally filtered).
*   `src/agents/performance_tracker.py::ModelPerformanceTracker._calculate_score(...)` -> `float` - **(NEW Internal)** Calculates a basic performance score (placeholder for ranking).
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.get_ranked_models(...)` -> `List` - **(NEW)** Returns a list of models ranked by score (currently basic).

## **Agent Prompt Utilities (`src/agents/`)**

*   (No functional changes in this phase)
*   `src/agents/prompt_utils.py::STANDARD_FRAMEWORK_INSTRUCTIONS` (Constant str) - Template for dynamic agents.
*   `src/agents/prompt_utils.py::ADMIN_AI_OPERATIONAL_INSTRUCTIONS` (Constant str) - Template for Admin AI.
*   `src/agents/prompt_utils.py::update_agent_prompt_team_id(...)` (Async) - Updates prompt state.

## **Agent Interaction Handler (`src/agents/`)**

*   (No changes in this phase)
*   `src/agents/interaction_handler.py::AgentInteractionHandler` (Class) - Handles tool interactions/execution.
*   (All methods remain the same)

## **Agent Cycle Handler (`src/agents/`)**

*   `src/agents/cycle_handler.py::AgentCycleHandler` (Class) - Handles agent's execution cycle.
*   `src/agents/cycle_handler.py::AgentCycleHandler.__init__(...)` - Initializes handler.
*   `src/agents/cycle_handler.py::AgentCycleHandler.run_cycle(agent, retry_count)` (Async) - Manages agent's `process_message` loop. **Records metrics via `performance_tracker.record_call`. Triggers failover via `manager.handle_agent_model_failover` instead of requesting user override.**

## **Agent Manager (Coordinator) (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator. **Instantiates `ModelPerformanceTracker`.** Handles failover.
*   `src/agents/manager.py::AgentManager.__init__(...)` - Initializes self and components including `ModelPerformanceTracker`.
*   `src/agents/manager.py::AgentManager.initialize_bootstrap_agents()` (Async) - Loads bootstrap agents, auto-selects Admin AI model.
*   `src/agents/manager.py::AgentManager._create_agent_internal(...)` (Async Internal) - Core agent creation.
*   `src/agents/manager.py::AgentManager.create_agent_instance(...)` (Async) - Public method for dynamic agents.
*   `src/agents/manager.py::AgentManager.delete_agent_instance(...)` (Async) - Removes agent.
*   `src/agents/manager.py::AgentManager._generate_unique_agent_id(...)` -> `str` - Generates unique ID.
*   `src/agents/manager.py::AgentManager.schedule_cycle(...)` (Async) - Schedules agent cycle.
*   `src/agents/manager.py::AgentManager.handle_user_message(...)` (Async) - Routes user message. **No longer checks for AWAITING_USER_OVERRIDE status.**
*   `src/agents/manager.py::handle_user_override` - **(REMOVED)**
*   `src/agents/manager.py::request_user_override` - **(REMOVED)**
*   `src/agents/manager.py::handle_agent_model_failover(agent_id, last_error)` (Async) - **(NEW)** Attempts to switch agent model/provider based on tiers and `already_failed` set. Sets agent to ERROR if failover fails or limit reached.
*   `src/agents/manager.py::_select_next_available_model(agent, already_failed)` -> `Tuple` - **(NEW Internal)** Helper to select the next model for failover based on strict tiers (Local->Free->Paid) and excluding failed/current models.
*   `src/agents/manager.py::push_agent_status_update(...)` (Async Helper) - Sends status to UI.
*   `src/agents/manager.py::send_to_ui(...)` (Async Helper) - Sends data to UI.
*   `src/agents/manager.py::get_agent_status()` -> `Dict` - Gets agent statuses.
*   `src/agents/manager.py::save_session(...)` -> `Tuple` (Async) - Delegates save.
*   `src/agents/manager.py::load_session(...)` -> `Tuple` (Async) - Delegates load.
*   `src/agents/manager.py::cleanup_providers()` (Async) - Closes providers **and saves performance metrics**.
*   `src/agents/manager.py::_close_provider_safe(...)` (Async Internal) - Safely closes provider.
*   `src/agents/manager.py::get_agent_info_list_sync(...)` -> `List` - Gets agent info list.

## **LLM Providers Base (`src/llm_providers/`)**

*   (No changes)

## **LLM Providers Implementations (`src/llm_providers/`)**

*   (No functional changes, only specific fixes applied earlier)

## **Tools Base (`src/tools/`)**

*   (No changes)

## **Tool Executor (`src/tools/`)**

*   (No changes)

## **Tool Implementations (`src/tools/`)**

*   (No changes)

## **Frontend Logic (`static/js/app.js`)**

*   `static/js/app.js::DOMContentLoaded Listener` - Initializes UI. **No longer loads static config.**
*   `static/js/app.js::setupWebSocket()` - Manages WebSocket connection.
*   `static/js/app.js::handleWebSocketMessage(data)` - Handles incoming messages. **No longer handles `request_user_override`.**
*   `static/js/app.js::addMessage(...)` - Adds message to UI.
*   `static/js/app.js::appendAgentResponseChunk(...)` - Appends streaming text.
*   `static/js/app.js::finalizeAgentResponse(...)` - Marks stream as complete.
*   `static/js/app.js::updateLogStatus(...)` - Updates connection status display.
*   `static/js/app.js::updateAgentStatusUI(...)` - Updates Agent Status list entry.
*   `static/js/app.js::addOrUpdateAgentStatusEntry(...)` - Adds/updates agent status item.
*   `static/js/app.js::removeAgentStatusEntry(...)` - Removes agent status item.
*   `static/js/app.js::addRawLogEntry(...)` - Logs raw data to console.
*   `static/js/app.js::setupEventListeners()` - Attaches listeners. **Removed listeners for config buttons and modal forms.**
*   `static/js/app.js::showView(...)` - Handles bottom navigation clicks.
*   `static/js/app.js::handleSendMessage()` - Sends user message/file.
*   `static/js/app.js::handleFileSelect(...)` - Handles file input.
*   `static/js/app.js::displayFileInfo()` - Shows attached file info.
*   `static/js/app.js::clearFileInput()` - Clears file input.
*   `static/js/app.js::displayAgentConfigurations` - **(REMOVED)**
*   `static/js/app.js::handleSaveAgent` - **(REMOVED)**
*   `static/js/app.js::handleDeleteAgent` - **(REMOVED)**
*   `static/js/app.js::openModal` - **(REMOVED)**
*   `static/js/app.js::closeModal` - **(REMOVED)**
*   `static/js/app.js::showOverrideModal` - **(REMOVED)**
*   `static/js/app.js::handleSubmitOverride` - **(REMOVED)**
*   (Session management functions remain: `loadProjects`, `loadSessions`, `handleLoadSession`, `handleSaveSession`, `displaySessionStatus`)

---
