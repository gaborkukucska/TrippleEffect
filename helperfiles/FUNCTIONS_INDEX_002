<!-- # START OF FILE helperfiles/FUNCTIONS_INDEX.md -->
# Functions Index

This file tracks the core functions/methods defined within the TrippleEffect framework, categorized by component. It helps in understanding the codebase and navigating between different parts.

*   **Format:** `[File Path]::[Class Name]::[Method Name](parameters) - Description` or `[File Path]::[Function Name](parameters) - Description`

---

## **Application Entry Point (`src/main.py`)**

*   `src/main.py::lifespan(app: FastAPI)` (async context manager) - Manages startup/shutdown. **Initializes DB**. Instantiates `AgentManager`, runs `ModelRegistry.discover_models_and_providers()`, initializes bootstrap agents, starts/stops Ollama proxy, calls `agent_manager.cleanup_providers()` (saves metrics & quarantine state), **closes DB connection**.
*   `src/main.py` (Script execution block) - Loads .env, configures logging, creates FastAPI app, runs Uvicorn.

## **Core Components (`src/core/`)**

*   `src/core/database_manager.py::Base` (Class) - SQLAlchemy declarative base class.
*   `src/core/database_manager.py::Project(Base)` (Class) - SQLAlchemy model for projects table.
*   `src/core/database_manager.py::Session(Base)` (Class) - SQLAlchemy model for sessions table.
*   `src/core/database_manager.py::AgentRecord(Base)` (Class) - SQLAlchemy model for agent_records table.
*   `src/core/database_manager.py::Interaction(Base)` (Class) - SQLAlchemy model for interactions table.
*   `src/core/database_manager.py::LongTermKnowledge(Base)` (Class) - SQLAlchemy model for long_term_knowledge table.
*   `src/core/database_manager.py::DatabaseManager` (Class) - Handles database connection, session management, and CRUD operations.
*   `src/core/database_manager.py::DatabaseManager.__init__(db_url)` - Initializes DB manager, schedules async DB setup.
*   `src/core/database_manager.py::DatabaseManager._initialize_db()` (Async Internal) - Initializes async engine, sessionmaker, creates tables.
*   `src/core/database_manager.py::DatabaseManager.close()` (Async) - Closes the database engine connection pool.
*   `src/core/database_manager.py::DatabaseManager.get_session()` (Async Context Manager) -> `AsyncSession` - Provides a database session.
*   `src/core/database_manager.py::DatabaseManager.add_project(name, description=None)` (Async) -> `Optional[Project]` - Adds or returns existing project.
*   `src/core/database_manager.py::DatabaseManager.get_project_by_name(name)` (Async) -> `Optional[Project]` - Gets project by name.
*   `src/core/database_manager.py::DatabaseManager.start_session(project_id, session_name)` (Async) -> `Optional[Session]` - Creates a new session record.
*   `src/core/database_manager.py::DatabaseManager.end_session(session_id)` (Async) - Marks a session as ended.
*   `src/core/database_manager.py::DatabaseManager.add_agent_record(session_id, agent_id, persona, model_config_dict=None)` (Async) -> `Optional[AgentRecord]` - Adds agent creation record.
*   `src/core/database_manager.py::DatabaseManager.log_interaction(session_id, agent_id, role, content=None, tool_calls=None, tool_results=None)` (Async) -> `Optional[Interaction]` - Logs an interaction event.
*   `src/core/database_manager.py::DatabaseManager.save_knowledge(keywords, summary, session_id=None, interaction_id=None, importance=0.5)` (Async) -> `Optional[LongTermKnowledge]` - Saves a distilled knowledge item.
*   `src/core/database_manager.py::DatabaseManager.search_knowledge(query_keywords, min_importance=None, max_results=5)` (Async) -> `List[LongTermKnowledge]` - Searches knowledge by keywords.
*   `src/core/database_manager.py::db_manager` (Instance) - Singleton `DatabaseManager` instance.
*   `src/core/database_manager.py::close_db_connection()` (Async) - Global function to close the singleton DB manager connection.

## **Configuration (`src/config/`)**

*   `src/config/config_manager.py::ConfigManager` (Class) - Manages reading/writing of `config.yaml`.
*   (All `ConfigManager` methods remain the same from previous index)

*   `src/config/model_registry.py::ModelInfo` (Class) - Simple dictionary subclass.
*   `src/config/model_registry.py::ModelRegistry` (Class) - Handles discovery, filtering, storage of available models.
*   (All `ModelRegistry` methods remain the same from previous index)

*   `src/config/settings.py::Settings` (Class) - Holds settings from `.env`, `prompts.json`, and initial `config.yaml`. Includes Tavily API key.
*   (All `Settings` methods remain the same from previous index)

## **API Routes (`src/api/`)**

*   `src/api/http_routes.py::get_agent_manager_dependency(request: Request)` -> `'AgentManager'` - FastAPI dependency.
*   (Other HTTP routes remain the same from previous index)

## **WebSocket Management (`src/api/`)**

*   `src/api/websocket_manager.py::set_agent_manager(manager: 'AgentManager')` - Module-level function to inject shared `AgentManager`.
*   `src/api/websocket_manager.py::broadcast(message: str)` (Async) - Sends message to all connected WebSocket clients.
*   `src/api/websocket_manager.py::websocket_endpoint(websocket: WebSocket)` (Async) - Main WebSocket handler.

## **Agent Constants (`src/agents/`)**

*   (Constants remain the same)

## **Agent Core (`src/agents/`)**

*   `src/agents/core.py::Agent` (Class) - Represents individual LLM agent. Parses XML tool calls, detects plans.
*   (All `Agent` methods remain the same from previous index)

## **Agent Tool Parser (`src/agents/`)**

*   `src/agents/agent_tool_parser.py::find_and_parse_xml_tool_calls(...)` -> `List` - Standalone function to find/parse XML tool calls.

## **Agent State Manager (`src/agents/`)**

*   `src/agents/state_manager.py::AgentStateManager` (Class) - Manages dynamic team/agent assignment state.
*   (All `AgentStateManager` methods remain the same from previous index)

## **Agent Session Manager (`src/agents/`)**

*   `src/agents/session_manager.py::SessionManager` (Class) - Handles saving/loading of session state *filesystem* data. Imports status constants.
*   (All `SessionManager` methods remain the same from previous index)

## **Agent Performance Tracker (`src/agents/`)**

*   `src/agents/performance_tracker.py::ModelMetrics` (Class) - Dictionary subclass defining metric structure.
*   `src/agents/performance_tracker.py::ModelPerformanceTracker` (Class) - Tracks model success/failure/latency metrics.
*   (All `ModelPerformanceTracker` methods remain the same from previous index)

## **Agent Prompt Utilities (`src/agents/`)**

*   `src/agents/prompt_utils.py::update_agent_prompt_team_id(manager, agent_id, new_team_id)` (Async) - Updates team ID placeholder in a live agent's system prompt state.

## **Agent Interaction Handler (`src/agents/`)**

*   `src/agents/interaction_handler.py::AgentInteractionHandler` (Class) - Handles tool interactions/execution. Handles `get_agent_details`. Imports status constants.
*   (All `AgentInteractionHandler` methods remain the same from previous index)

## **Agent Cycle Handler (`src/agents/`)**

*   `src/agents/cycle_handler.py::AgentCycleHandler` (Class) - Handles agent's execution cycle, retries, plan approval, failover triggering. Injects time context for Admin AI. **Logs interactions to DB**. Imports status constants.
*   `src/agents/cycle_handler.py::AgentCycleHandler.__init__(manager, interaction_handler)` - Initializes cycle handler.
*   `src/agents/cycle_handler.py::AgentCycleHandler.run_cycle(agent, retry_count)` (Async) - Manages agent's `process_message` loop. **Includes DB logging calls** for plan, response, errors, tool requests/results.

## **Agent Failover Handler (`src/agents/`)**

*   `src/agents/failover_handler.py::handle_agent_model_failover(manager, agent_id, last_error_obj)` (Async) - Standalone function for key cycling and model/provider switching.
*   `src/agents/failover_handler.py::_select_next_failover_model(manager, agent, already_failed)` (Async Internal) -> `Tuple` - Selects next model based on tiers.

## **Provider Key Manager (`src/agents/`)**

*   `src/agents/provider_key_manager.py::ProviderKeyManager` (Class) - Manages API Keys & Quarantine state.
*   (All `ProviderKeyManager` methods remain the same from previous index)

## **Agent Lifecycle (`src/agents/`)**

*   `src/agents/agent_lifecycle.py::_select_best_available_model(manager)` (Async Internal) -> `Tuple` - Selects best model based on ranking/availability.
*   `src/agents/agent_lifecycle.py::initialize_bootstrap_agents(manager)` (Async) - Initializes bootstrap agents, **delegates DB logging to manager**.
*   `src/agents/agent_lifecycle.py::_create_agent_internal(...)` (Async Internal) -> `Tuple` - Core agent creation logic.
*   `src/agents/agent_lifecycle.py::create_agent_instance(...)` (Async) -> `Tuple` - Public method for dynamic agents, **delegates DB logging to manager**.
*   `src/agents/agent_lifecycle.py::delete_agent_instance(manager, agent_id)` (Async) -> `Tuple` - Removes agent.
*   `src/agents/agent_lifecycle.py::_generate_unique_agent_id(manager, prefix)` -> `str` - Generates unique agent ID.

## **Agent Manager (Coordinator) (`src/agents/`)**

*   `src/agents/manager.py::AgentManager` (Class) - Central coordinator. **Initializes `DatabaseManager`**. Manages DB context (project/session IDs). Imports status constants.
*   `src/agents/manager.py::AgentManager.__init__(websocket_manager=None)` - Initializes manager, handlers, tools, **DB manager instance**. **Starts default DB session**.
*   `src/agents/manager.py::AgentManager._ensure_default_db_session()` (Async Internal) - **(NEW)** Ensures default DB project/session exists on startup.
*   `src/agents/manager.py::AgentManager.set_project_session_context(project_name, session_name, loading=False)` (Async) - **(NEW)** Sets current project/session names and DB IDs.
*   `src/agents/manager.py::AgentManager.initialize_bootstrap_agents()` (Async) - Delegates init, **logs bootstrap agents to DB**.
*   `src/agents/manager.py::AgentManager.create_agent_instance(...)` (Async) -> `Tuple` - Delegates creation, **logs dynamic agent to DB**.
*   `src/agents/manager.py::AgentManager.handle_user_message(message, client_id=None)` (Async) - Routes user message, **ensures DB context, logs user interaction to DB**.
*   `src/agents/manager.py::AgentManager.save_session(project_name, session_name=None)` (Async) -> `Tuple` - Delegates filesystem save, **updates/creates DB session record**.
*   `src/agents/manager.py::AgentManager.load_session(project_name, session_name)` (Async) -> `Tuple` - Delegates filesystem load, **updates DB context (TODO: find existing session ID)**.
*   `src/agents/manager.py::AgentManager.cleanup_providers()` (Async) - Cleans up providers, saves metrics/quarantine, **ends current DB session**. *(DB close moved to main.py)*
*   (Other `AgentManager` methods remain the same)

## **LLM Providers Base (`src/llm_providers/`)**

*   (All `BaseLLMProvider` methods/signatures remain the same from previous index)

## **LLM Providers Implementations (`src/llm_providers/`)**

*   (No functional signature changes in this phase)

## **Tools Base (`src/tools/`)**

*   `src/tools/base.py::ToolParameter` (Pydantic Model) - Defines schema for a tool parameter.
*   `src/tools/base.py::BaseTool` (Abstract Class) - Base class for all tools.
*   `src/tools/base.py::BaseTool.execute(agent_id, agent_sandbox_path, project_name=None, session_name=None, **kwargs)` (Abstract Async) - Tool execution signature.
*   `src/tools/base.py::BaseTool.get_schema()` -> `Dict` - Returns tool's schema description.

## **Tool Executor (`src/tools/`)**

*   `src/tools/executor.py::ToolExecutor` (Class) - Discovers tools, generates descriptions, executes tools.
*   (All `ToolExecutor` methods remain the same from previous index)

## **Tool Implementations (`src/tools/`)**

*   `src/tools/file_system.py::FileSystemTool` (Class) - Tool for file operations (incl. mkdir, delete).
*   `src/tools/github_tool.py::GitHubTool` (Class) - Tool for GitHub API interaction (incl. recursive list).
*   `src/tools/manage_team.py::ManageTeamTool` (Class) - Tool for agent/team management (incl. get_agent_details).
*   `src/tools/send_message.py::SendMessageTool` (Class) - Tool for sending messages.
*   `src/tools/web_search.py::WebSearchTool` (Class) - Tool for web search (incl. Tavily API).
*   `src/tools/system_help.py::SystemHelpTool` (Class) - Tool for system info and log search.
*   `src/tools/knowledge_base.py::KnowledgeBaseTool` (Class) - **(NEW)** Tool for saving/searching long-term knowledge via DB.
*   `src/tools/knowledge_base.py::KnowledgeBaseTool.execute(...)` (Async) -> `str` - Executes 'save_knowledge' or 'search_knowledge'.

## **Frontend Logic (`static/js/app.js`)**

*   (No functional backend signature changes in this phase)

---
