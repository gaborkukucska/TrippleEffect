<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages startup/shutdown. Instantiates `AgentManager`, runs `ModelRegistry.discover_models_and_providers()`, initializes bootstrap agents, starts/stops Ollama proxy if configured, calls `agent_manager.cleanup_providers()` (saves metrics & quarantine state).
*   `src/main.py` (Script execution block) - Loads .env, configures logging, creates FastAPI app, runs Uvicorn.

## **Configuration (`src/config/`)**

*   `src/config/config_manager.py::ConfigManager` (Class) - Manages reading/writing of `config.yaml` (bootstrap agents).
*   (Other ConfigManager methods remain the same)

*   `src/config/model_registry.py::ModelRegistry` (Class) - Handles discovery, filtering, storage of available models from reachable providers.
*   (All methods remain the same)

*   `src/config/settings.py::Settings` (Class) - Holds settings from `.env`, `prompts.json`, and initial `config.yaml`. Instantiates `ModelRegistry` after loading.
*   `src/config/settings.py::Settings.__init__()` - Initializes settings, loads env vars, prompts, initial config.
*   `src/config/settings.py::Settings._load_prompts_from_json()` (Internal) - Loads prompts from `prompts.json`.
*   `src/config/settings.py::Settings._ensure_projects_dir()` - Creates projects directory.
*   `src/config/settings.py::Settings._check_required_keys()` - Validates provider keys/URLs and logs status.
*   `src/config/settings.py::Settings.get_provider_config(...)` -> `Dict` - Gets base config (URL, referer) for a provider.
*   `src/config/settings.py::Settings.is_provider_configured(...)` -> `bool` - Checks if provider has essential config (keys or URL/proxy). Updated logic for Ollama proxy.
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

## **Agent Constants (`src/agents/`)**

*   `src/agents/constants.py::AGENT_STATUS_IDLE` - Constant string.
*   `src/agents/constants.py::AGENT_STATUS_PROCESSING` - Constant string.
*   `src/agents/constants.py::AGENT_STATUS_PLANNING` - **(NEW)** Constant string.
*   `src/agents/constants.py::AGENT_STATUS_AWAITING_TOOL` - Constant string.
*   `src/agents/constants.py::AGENT_STATUS_EXECUTING_TOOL` - Constant string.
*   `src/agents/constants.py::AGENT_STATUS_ERROR` - Constant string.

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents individual LLM agent. Parses XML tool calls, detects plans. **Imports status constants.**
*   `src/agents/core.py::Agent.__init__(...)` - Initializes agent. Compiles XML/Plan regex patterns. Added `current_plan` attribute.
*   `src/agents/core.py::Agent.set_status(...)` - Updates status, clears/sets `current_tool_info` or `current_plan`.
*   `src/agents/core.py::Agent.process_message(...)` (Async Generator) - Processes LLM stream. **Detects `<plan>` tag and yields `plan_generated` event.** Calls external `find_and_parse_xml_tool_calls`. Yields `tool_requests` or `final_response`.
*   `src/agents/core.py::Agent.get_state()` -> `Dict` - Returns agent state, **includes `current_plan` if status is PLANNING.**
*   (Other Agent methods remain the same: `set_manager`, `ensure_sandbox_exists`, `clear_history`)

## **Agent Tool Parser (`src/agents/`)**

*   `src/agents/agent_tool_parser.py::find_and_parse_xml_tool_calls(...)` -> `List` - **(NEW)** Standalone function to find/parse XML tool calls (raw/fenced). Handles validation for universally required params.

## **Agent State Manager (`src/agents/`)**

*   `src/agents/state_manager.py::AgentStateManager` (Class) - Manages dynamic team/agent assignment state.
*   `src/agents/state_manager.py::AgentStateManager.create_new_team(...)` (Async) -> `Tuple` - **Made idempotent** (returns True if team already exists).
*   `src/agents/state_manager.py::AgentStateManager.get_agents_in_team(...)` -> `List[Agent]` - **(NEW)** Helper to get agent instances in a team.
*   (Other methods remain the same)

## **Agent Session Manager (`src/agents/`)**

*   `src/agents/session_manager.py::SessionManager` (Class) - Handles saving/loading of session state. Delegates agent recreation to `agent_lifecycle` module during load. **Imports status constants.**
*   (All methods remain the same functionally, just updated constant import)

## **Agent Performance Tracker (`src/agents/`)**

*   `src/agents/performance_tracker.py::ModelPerformanceTracker` (Class) - Tracks model success/failure/latency metrics.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker.get_ranked_models(...)` -> `List` - Returns list of models ranked by score. Used by auto-selection.
*   (Other methods remain the same)

## **Agent Prompt Utilities (`src/agents/`)**

*   `src/agents/prompt_utils.py::update_agent_prompt_team_id(...)` (Async) - Updates team ID placeholder in agent's system prompt state.

## **Agent Interaction Handler (`src/agents/`)**

*   `src/agents/interaction_handler.py::AgentInteractionHandler` (Class) - Handles tool interactions/execution. **Imports status constants.**
*   `src/agents/interaction_handler.py::AgentInteractionHandler.handle_manage_team_action(...)` (Async) -> `Tuple` - Processes `ManageTeamTool` results. **Checks for duplicate persona on create_agent.** Handles idempotent `create_team` result.
*   `src/agents/interaction_handler.py::AgentInteractionHandler.route_and_activate_agent_message(...)` (Async) -> `Optional[Task]` - Routes messages. **Resolves target by ID then unique persona.** Provides feedback on failure/ambiguity. **Schedules target agent cycle if idle.**
*   (Other methods remain the same)

## **Agent Cycle Handler (`src/agents/`)**

*   `src/agents/cycle_handler.py::AgentCycleHandler` (Class) - Handles agent's execution cycle, including retries, plan approval, and triggering failover. **Imports status constants.**
*   `src/agents/cycle_handler.py::AgentCycleHandler.run_cycle(agent, retry_count)` (Async) - Manages agent's `process_message` loop. **Handles `plan_generated` event (auto-approves, reactivates).** Fixed `UnboundLocalError` for `activation_task`. Records metrics. Triggers failover. **Ensures reactivation after tool execution.**

## **Agent Failover Handler (`src/agents/`)**

*   `src/agents/failover_handler.py::handle_agent_model_failover(manager, agent_id, last_error_obj)` (Async) - Handles key cycling and model/provider switching logic. **Imports status constants.**
*   `src/agents/failover_handler.py::_select_next_failover_model(manager, agent, already_failed)` -> `Tuple` (Async Internal) - Selects next model based on tiers and availability.

## **Provider Key Manager (`src/agents/`)**

*   `src/agents/provider_key_manager.py::ProviderKeyManager` (Class) - Manages API Keys & Quarantine state.
*   (All methods remain the same)

## **Agent Lifecycle (`src/agents/`)**

*   `src/agents/agent_lifecycle.py::_select_best_available_model(manager)` -> `Tuple` (Async Internal) - **(NEW)** Selects best model based on ranking/availability for dynamic agents.
*   `src/agents/agent_lifecycle.py::initialize_bootstrap_agents(manager)` (Async) - Initializes bootstrap agents, **calls `_select_best_available_model` for Admin AI if needed**, injects XML tool descriptions. **Refined validation.**
*   `src/agents/agent_lifecycle.py::_create_agent_internal(...)` (Async Internal) - Core agent creation logic. **Calls `_select_best_available_model` if provider/model omitted.** **Refined validation for provider/model match and format.** Injects XML tool descriptions.
*   `src/agents/agent_lifecycle.py::create_agent_instance(...)` (Async) -> `Tuple` - Public method for dynamic agents, **provider/model now optional**.
*   `src/agents/agent_lifecycle.py::delete_agent_instance(...)` (Async) -> `Tuple` - Removes agent.
*   `src/agents/agent_lifecycle.py::_generate_unique_agent_id(manager, prefix)` -> `str` - Generates unique ID.

## **Agent Manager (Coordinator) (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator. Instantiates components. **Imports status constants.**
*   (Other methods delegate to respective handlers/modules, functionality mostly unchanged at this level)

## **LLM Providers Base (`src/llm_providers/`)**

*   (No changes)

## **LLM Providers Implementations (`src/llm_providers/`)**

*   (No functional changes in this phase)

## **Tools Base (`src/tools/`)**

*   (No changes)

## **Tool Executor (`src/tools/`)**

*   `src/tools/executor.py::ToolExecutor` (Class) - Discovers tools, generates descriptions (XML), executes tools.
*   (No functional changes in this phase, but generated XML description for `FileSystemTool` will update)

## **Tool Implementations (`src/tools/`)**

*   `src/tools/file_system.py::FileSystemTool` - **Added `find_replace` action** and associated parameters/logic. Updated description.
*   `src/tools/manage_team.py::ManageTeamTool` - **Marked `provider`/`model` parameters as optional.** **Moved action-specific validation into `execute` method.** Updated description.
*   (Other tools unchanged)

## **Frontend Logic (`static/js/app.js`)**

*   (No functional changes in this phase)

---
