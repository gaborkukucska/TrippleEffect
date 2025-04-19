<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages startup/shutdown. Instantiates `AgentManager`, runs `ModelRegistry.discover_models_and_providers()`, initializes bootstrap agents, starts/stops Ollama proxy if configured, calls `agent_manager.cleanup_providers()` (which saves metrics & quarantine state).
*   `src/main.py` (Script execution block) - Loads .env, configures logging, creates FastAPI app, runs Uvicorn.

## **Configuration (`src/config/`)**

*   `src/config/config_manager.py::ConfigManager` (Class) - Manages reading/writing of `config.yaml` (bootstrap agents).
*   (Other ConfigManager methods remain the same, focusing on bootstrap agent definitions)

*   `src/config/model_registry.py::ModelRegistry` (Class) - Handles discovery, filtering, storage of available models from reachable providers.
*   `src/config/model_registry.py::ModelRegistry.__init__(settings_obj)` - Initializes registry, needs `settings` instance. Reads `MODEL_TIER`.
*   `src/config/model_registry.py::ModelRegistry.discover_models_and_providers()` (Async) - Main entry point for discovery. Checks provider reachability, then discovers models.
*   `src/config/model_registry.py::ModelRegistry._discover_providers()` (Async Internal) - Checks provider reachability (env URLs, localhost, proxy).
*   `src/config/model_registry.py::ModelRegistry._check_local_provider_prioritized(...)` (Async Internal) - Checks specific local provider reachability using prioritized checks (env, localhost, network, proxy).
*   `src/config/model_registry.py::ModelRegistry._check_single_local_url(...)` (Async Internal) - Helper to check a single URL for a local provider/proxy.
*   `src/config/model_registry.py::ModelRegistry._discover_openrouter_models()` (Async Internal) - Fetches models from OpenRouter API.
*   `src/config/model_registry.py::ModelRegistry._discover_ollama_models()` (Async Internal) - Fetches models from Ollama API (direct connection used for discovery).
*   `src/config/model_registry.py::ModelRegistry._discover_litellm_models()` (Async Internal) - Fetches models from LiteLLM API.
*   `src/config/model_registry.py::ModelRegistry._discover_openai_models()` (Async Internal) - Adds common OpenAI models.
*   `src/config/model_registry.py::ModelRegistry._apply_filters()` (Internal) - Filters raw models based on reachability and `MODEL_TIER`.
*   `src/config/model_registry.py::ModelRegistry.get_available_models_list(...)` -> `List[str]` - Returns flat list of available model IDs (local prioritized).
*   `src/config/model_registry.py::ModelRegistry.get_available_models_dict()` -> `Dict` - Returns dict of available models by provider.
*   `src/config/model_registry.py::ModelRegistry.find_provider_for_model(...)` -> `Optional[str]` - Finds the provider for a model ID.
*   `src/config/model_registry.py::ModelRegistry.get_formatted_available_models()` -> `str` - Returns formatted string list for prompts.
*   `src/config/model_registry.py::ModelRegistry.is_model_available(...)` -> `bool` - Checks if a specific model is available.
*   `src/config/model_registry.py::ModelRegistry.get_reachable_provider_url(...)` -> `Optional[str]` - Gets the confirmed URL for a provider (might be proxy URL for Ollama).
*   `src/config/model_registry.py::ModelRegistry._log_available_models()` (Internal) - Logs available models.

*   `src/config/settings.py::Settings` (Class) - Holds settings from `.env`, `prompts.json`, and initial `config.yaml`. Instantiates `ModelRegistry` after loading.
*   `src/config/settings.py::Settings.__init__()` - Initializes settings, loads env vars, prompts, initial config.
*   `src/config/settings.py::Settings._load_prompts_from_json()` (Internal) - Loads prompts from `prompts.json`.
*   `src/config/settings.py::Settings._ensure_projects_dir()` - Creates projects directory.
*   `src/config/settings.py::Settings._check_required_keys()` - Validates provider keys/URLs and logs status.
*   `src/config/settings.py::Settings.get_provider_config(...)` -> `Dict` - Gets base config (URL, referer) for a provider.
*   `src/config/settings.py::Settings.is_provider_configured(...)` -> `bool` - Checks if provider has essential config (keys or URL/proxy). **Updated logic for Ollama proxy.**
*   `src/config/settings.py::Settings.get_agent_config_by_id(...)` -> `Optional[Dict]` - Retrieves bootstrap agent's config.
*   `src/config/settings.py::Settings.get_formatted_allowed_models()` -> `str` - Delegates to `ModelRegistry`.
*   `src/config/settings.py::settings` (Instance) - Singleton settings instance.
*   `src/config/settings.py::model_registry` (Instance) - Singleton ModelRegistry instance.

## **API Routes (`src/api/`)**

*   `src/api/http_routes.py::get_agent_manager_dependency(request: Request)` -> `'AgentManager'` - Retrieves shared AgentManager.
*   (Other HTTP routes remain for index, config CRUD, project/session list/load/save)

## **WebSocket Management (`src/api/`)**

*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Injects shared `AgentManager`.
*   `src/api/websocket_manager.py::broadcast(message: str)` (Async) - Sends message to all connections.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` (Async) - Handler for `/ws`. Forwards messages to AgentManager.

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents individual LLM agent. Parses XML tool calls.
*   `src/agents/core.py::Agent.__init__(...)` - Initializes agent. **Compiles XML regex patterns only.** Added `_last_api_key_used` and `_failed_models_this_cycle` attributes.
*   `src/agents/core.py::Agent._find_and_parse_tool_calls(...)` -> `List` - **Reverted to find/parse XML tool calls only.** Handles raw and fenced XML. Unescapes parameters. Validates args.
*   (Other Agent methods remain the same: `set_status`, `set_manager`, `ensure_sandbox_exists`, `process_message`, `get_state`, `clear_history`)

## **Agent State Manager (`src/agents/`)**

*   `src/agents/state_manager.py::AgentStateManager` (Class) - Manages dynamic team/agent assignment state.
*   (All methods remain the same)

## **Agent Session Manager (`src/agents/`)**

*   `src/agents/session_manager.py::SessionManager` (Class) - Handles saving/loading of session state. Delegates agent recreation to `agent_lifecycle` module during load.
*   (All methods remain the same)

## **Agent Performance Tracker (`src/agents/`)**

*   `src/agents/performance_tracker.py::ModelPerformanceTracker` (Class) - Tracks model success/failure/latency metrics.
*   (All methods remain the same)

## **Agent Prompt Utilities (`src/agents/`)**

*   `src/agents/prompt_utils.py::update_agent_prompt_team_id(...)` (Async) - Updates team ID placeholder in agent's system prompt state.

## **Agent Interaction Handler (`src/agents/`)**

*   `src/agents/interaction_handler.py::AgentInteractionHandler` (Class) - Handles tool interactions/execution.
*   (All methods remain the same)

## **Agent Cycle Handler (`src/agents/`)**

*   `src/agents/cycle_handler.py::AgentCycleHandler` (Class) - Handles agent's execution cycle, including retries and triggering failover.
*   `src/agents/cycle_handler.py::AgentCycleHandler.run_cycle(agent, retry_count)` (Async) - Manages agent's `process_message` loop. **Fixed UnboundLocalError** in tool request processing. Records metrics. Triggers failover via `manager.handle_agent_model_failover`.

## **Agent Failover Handler (`src/agents/`)**

*   `src/agents/failover_handler.py::handle_agent_model_failover(manager, agent_id, last_error_obj)` (Async) - Handles key cycling (via `ProviderKeyManager`) and model/provider switching logic.
*   `src/agents/failover_handler.py::_select_next_failover_model(manager, agent, already_failed)` -> `Tuple` (Async Internal) - Selects next model based on tiers and availability.

## **Provider Key Manager (`src/agents/`)**

*   `src/agents/provider_key_manager.py::ProviderKeyManager` (Class) - Manages API Keys & Quarantine state (loaded/saved to JSON).
*   (All methods remain the same)

## **Agent Lifecycle (`src/agents/`)**

*   `src/agents/agent_lifecycle.py::initialize_bootstrap_agents(manager)` (Async) - Initializes bootstrap agents, auto-selects Admin AI model, **injects XML tool descriptions**.
*   `src/agents/agent_lifecycle.py::_create_agent_internal(...)` (Async Internal) - Core agent creation logic. **Injects XML tool descriptions**. **Validates provider/model match**. **Adjusts local model ID for provider.**
*   `src/agents/agent_lifecycle.py::create_agent_instance(...)` (Async) - Public method for dynamic agents.
*   `src/agents/agent_lifecycle.py::delete_agent_instance(...)` (Async) - Removes agent.
*   `src/agents/agent_lifecycle.py::_generate_unique_agent_id(manager, prefix)` -> `str` - **Moved here from manager.py.** Generates unique ID.

## **Agent Manager (Coordinator) (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator. Instantiates components (`ProviderKeyManager`, `ToolExecutor`, `ModelPerformanceTracker`, handlers).
*   `src/agents/manager.py::AgentManager.__init__(...)` - Initializes self and components. Generates tool descriptions (XML/JSON).
*   `src/agents/manager.py::AgentManager.initialize_bootstrap_agents()` (Async) - Delegates to `agent_lifecycle`.
*   `src/agents/manager.py::AgentManager.create_agent_instance(...)` (Async) - Delegates to `agent_lifecycle`.
*   `src/agents/manager.py::AgentManager.delete_agent_instance(...)` (Async) - Delegates to `agent_lifecycle`.
*   `src/agents/manager.py::AgentManager.schedule_cycle(...)` (Async) - Schedules agent cycle via `AgentCycleHandler`.
*   `src/agents/manager.py::AgentManager.handle_user_message(...)` (Async) - Routes user message to Admin AI.
*   `src/agents/manager.py::handle_agent_model_failover(agent_id, last_error_obj)` (Async) - **Delegates** to `failover_handler.handle_agent_model_failover`.
*   `src/agents/manager.py::push_agent_status_update(...)` (Async Helper) - Sends status to UI.
*   `src/agents/manager.py::send_to_ui(...)` (Async Helper) - Sends data to UI.
*   `src/agents/manager.py::get_agent_status()` -> `Dict` - Gets agent statuses.
*   `src/agents/manager.py::save_session(...)` -> `Tuple` (Async) - Delegates save to `SessionManager`.
*   `src/agents/manager.py::load_session(...)` -> `Tuple` (Async) - Delegates load to `SessionManager`.
*   `src/agents/manager.py::cleanup_providers()` (Async) - Closes providers and triggers saving of metrics and quarantine state.
*   `src/agents/manager.py::_close_provider_safe(...)` (Async Internal) - Safely closes provider.
*   `src/agents/manager.py::get_agent_info_list_sync(...)` -> `List` - Gets agent info list.
*   `src/agents/manager.py::_generate_unique_agent_id` - **REMOVED** (Moved to `agent_lifecycle.py`).

## **LLM Providers Base (`src/llm_providers/`)**

*   (No changes)

## **LLM Providers Implementations (`src/llm_providers/`)**

*   `src/llm_providers/ollama_provider.py` - Updated to create session per-request, checks proxy settings during init.
*   (Other providers unchanged functionally)

## **Tools Base (`src/tools/`)**

*   `src/tools/base.py::BaseTool.execute(...)` - Signature updated to include `project_name` and `session_name` context.

## **Tool Executor (`src/tools/`)**

*   `src/tools/executor.py::ToolExecutor` (Class) - Discovers tools, generates descriptions (XML primarily used now), executes tools.
*   `src/tools/executor.py::ToolExecutor._register_available_tools()` - Discovers tools.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_xml()` -> `str` - Generates XML descriptions.
*   `src/tools/executor.py::ToolExecutor.get_formatted_tool_descriptions_json()` -> `str` - Generates JSON descriptions (less relevant now).
*   `src/tools/executor.py::ToolExecutor.execute_tool(...)` (Async) - Executes a tool, passing context (`project_name`, `session_name`). Validates required args based on schema.

## **Tool Implementations (`src/tools/`)**

*   `src/tools/manage_team.py::ManageTeamTool.execute(...)` - **Updated validation** logic for required parameters per action (esp. `create_agent`).
*   `src/tools/file_system.py::FileSystemTool.execute(...)` - Updated signature to accept context.
*   `src/tools/github_tool.py::GitHubTool.execute(...)` - Updated signature to accept context.
*   (Other tools updated signature, functionality unchanged)

## **Frontend Logic (`static/js/app.js`)**

*   (No functional changes in this phase)
