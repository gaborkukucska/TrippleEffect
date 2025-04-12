# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List, Tuple
import json
import logging
import time

# Import Agent class, Status constants, and BaseLLMProvider types
from src.agents.core import Agent, AGENT_STATUS_IDLE
from src.llm_providers.base import BaseLLMProvider

# Import settings, model_registry, AND BASE_DIR
from src.config.settings import settings, model_registry, BASE_DIR

# Import WebSocket broadcast function
from src.api.websocket_manager import broadcast

# Import ToolExecutor
from src.tools.executor import ToolExecutor

# Import Provider classes (needed for type hinting potentially)
from src.llm_providers.openai_provider import OpenAIProvider
from src.llm_providers.ollama_provider import OllamaProvider
from src.llm_providers.openrouter_provider import OpenRouterProvider

# Import the component managers and utils
from src.agents.state_manager import AgentStateManager
from src.agents.session_manager import SessionManager
from src.agents.interaction_handler import AgentInteractionHandler
from src.agents.cycle_handler import AgentCycleHandler, MAX_FAILOVER_ATTEMPTS # Import constant
from src.agents.performance_tracker import ModelPerformanceTracker
from src.agents.provider_key_manager import ProviderKeyManager

# --- Import the new refactored modules ---
from src.agents import agent_lifecycle
from src.agents import failover_handler
# --- End Import ---

logger = logging.getLogger(__name__)

# Constants - Can likely be moved or sourced centrally later
BOOTSTRAP_AGENT_ID = "admin_ai"
DEFAULT_PROJECT_NAME = "DefaultProject"

class AgentManager:
    """
    Main coordinator for agents. Initializes components and delegates tasks like
    agent creation, deletion, message handling, failover, and session management
    to specialized handlers/modules.
    """
    def __init__(self, websocket_manager: Optional[Any] = None):
        self.bootstrap_agents: List[str] = [] # Track IDs of bootstrap agents
        self.agents: Dict[str, Agent] = {}   # Registry of active agent instances
        self.send_to_ui_func = broadcast     # Function to send updates to UI
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None

        # --- Instantiate Core Components ---
        logger.info("Instantiating ProviderKeyManager...")
        self.key_manager = ProviderKeyManager(settings.PROVIDER_API_KEYS, settings)
        logger.info("ProviderKeyManager instantiated.")

        logger.info("Instantiating ToolExecutor...");
        self.tool_executor = ToolExecutor()
        self.tool_descriptions_xml = self.tool_executor.get_formatted_tool_descriptions_xml()
        logger.info(f"ToolExecutor instantiated with tools: {list(self.tool_executor.tools.keys())}")

        logger.info("Instantiating AgentStateManager...");
        self.state_manager = AgentStateManager(self)
        logger.info("AgentStateManager instantiated.")

        logger.info("Instantiating SessionManager...");
        self.session_manager = SessionManager(self, self.state_manager)
        logger.info("SessionManager instantiated.")

        logger.info("Instantiating AgentInteractionHandler...");
        self.interaction_handler = AgentInteractionHandler(self)
        logger.info("AgentInteractionHandler instantiated.")

        logger.info("Instantiating AgentCycleHandler...");
        self.cycle_handler = AgentCycleHandler(self, self.interaction_handler)
        logger.info("AgentCycleHandler instantiated.")

        logger.info("Instantiating ModelPerformanceTracker...");
        self.performance_tracker = ModelPerformanceTracker()
        logger.info("ModelPerformanceTracker instantiated and metrics loaded.")

        self._ensure_projects_dir()
        logger.info("AgentManager initialized synchronously. Bootstrap agents and model discovery run asynchronously.")

    def _ensure_projects_dir(self):
        try:
            settings.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"Ensured projects directory exists at: {settings.PROJECTS_BASE_DIR}")
        except Exception as e:
            logger.error(f"Error creating projects directory at {settings.PROJECTS_BASE_DIR}: {e}", exc_info=True)

    # --- Initialization and Lifecycle ---

    async def initialize_bootstrap_agents(self):
        """ Delegates bootstrap agent initialization to the lifecycle handler. """
        await agent_lifecycle.initialize_bootstrap_agents(self)

    async def create_agent_instance( self, agent_id_requested: Optional[str], provider: str, model: str, system_prompt: str, persona: str, team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs ) -> Tuple[bool, str, Optional[str]]:
        """ Delegates dynamic agent creation to the lifecycle handler. """
        return await agent_lifecycle.create_agent_instance(self, agent_id_requested, provider, model, system_prompt, persona, team_id, temperature, **kwargs)

    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]:
        """ Delegates agent deletion to the lifecycle handler. """
        return await agent_lifecycle.delete_agent_instance(self, agent_id)

    # --- Message Handling and Execution ---

    async def schedule_cycle(self, agent: Agent, retry_count: int = 0):
        """ Schedules the agent's execution cycle via the AgentCycleHandler. """
        if not agent:
            logger.error("Schedule cycle called with invalid Agent object.")
            return
        logger.debug(f"Manager: Scheduling cycle for agent '{agent.agent_id}' (Retry: {retry_count}).")
        # Delegate to the cycle handler instance
        asyncio.create_task(self.cycle_handler.run_cycle(agent, retry_count))

    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """ Handles incoming messages from the user, routing them to the Admin AI. """
        logger.info(f"Manager: Received user message for Admin AI: '{message[:100]}...'");
        if self.current_project is None:
            # Auto-create default project/session if none exists
            logger.info("Manager: No active project/session context found. Creating default context...")
            default_project = DEFAULT_PROJECT_NAME
            default_session = time.strftime("%Y%m%d_%H%M%S")
            success = False; save_msg = "Initialization error"
            try:
                success, save_msg = await self.save_session(default_project, default_session)
                if success: logger.info(f"Manager: Auto-created session: '{default_project}/{default_session}'")
                else: logger.error(f"Manager: Failed to auto-save default session: {save_msg}")
            except Exception as e:
                logger.error(f"Manager: Error during default session auto-save: {e}", exc_info=True)
                save_msg = f"Error during auto-save: {e}"
            await self.send_to_ui({"type": "system_event", "event": "session_saved", "project": default_project, "session": default_session, "message": f"Context set to default: {default_project}/{default_session}" if success else f"Failed to create default context: {save_msg}"})

        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID);
        if not admin_agent:
            logger.error(f"Manager: Admin AI ('{BOOTSTRAP_AGENT_ID}') not found. Cannot process message.")
            await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."})
            return

        # Route message to Admin AI
        if admin_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Manager: Delegating message to '{BOOTSTRAP_AGENT_ID}' and scheduling cycle.")
            admin_agent.message_history.append({"role": "user", "content": message})
            await self.schedule_cycle(admin_agent, 0) # Start with retry_count 0
        else: # Busy or Error
            logger.info(f"Manager: Admin AI busy ({admin_agent.status}). Message queued.")
            admin_agent.message_history.append({"role": "user", "content": message})
            await self.push_agent_status_update(admin_agent.agent_id)
            await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI busy ({admin_agent.status}). Queued." })

    # --- Failover Handling (Delegation) ---

    async def handle_agent_model_failover(self, agent_id: str, last_error: str):
        """ Delegates failover handling to the failover handler module. """
        await failover_handler.handle_agent_model_failover(self, agent_id, last_error)

    # --- UI Communication ---

    async def push_agent_status_update(self, agent_id: str):
        """ Sends a status update for a specific agent to the UI. """
        agent = self.agents.get(agent_id);
        if agent:
            state = agent.get_state()
            state["team"] = self.state_manager.get_agent_team(agent_id)
        else:
            # Handle case where agent might have been deleted just before update
            state = {"status": "deleted", "team": None}
            logger.warning(f"Cannot push status update for unknown/deleted agent: {agent_id}")

        await self.send_to_ui({"type": "agent_status_update", "agent_id": agent_id, "status": state})

    async def send_to_ui(self, message_data: Dict[str, Any]):
        """ Sends arbitrary data to all connected UI clients. """
        if not self.send_to_ui_func:
            logger.warning("UI broadcast func not set.")
            return
        try:
            await self.send_to_ui_func(json.dumps(message_data))
        except Exception as e:
            logger.error(f"Error sending to UI: {e}. Data: {message_data}", exc_info=True)

    # --- State and Session Management (Delegation) ---

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """ Gets the status of all current agents. """
        return {
            aid: (ag.get_state() | {"team": self.state_manager.get_agent_team(aid)})
            for aid, ag in self.agents.items()
        }

    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        """ Delegates saving the session state. """
        logger.info(f"Manager: Delegating save_session for '{project_name}'...")
        return await self.session_manager.save_session(project_name, session_name)

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        """ Delegates loading a session state. """
        logger.info(f"Manager: Delegating load_session for '{project_name}/{session_name}'...")
        # Pass self (manager instance) to load_session as it needs access to agent creation etc.
        return await self.session_manager.load_session(project_name, session_name)

    def get_agent_info_list_sync(self, filter_team_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """ Gets a list of basic agent info, optionally filtered by team. """
        info_list = []
        for agent_id, agent in self.agents.items():
             current_team = self.state_manager.get_agent_team(agent_id)
             if filter_team_id is not None and current_team != filter_team_id: continue
             state = agent.get_state()
             info = {
                 "agent_id": agent_id,
                 "persona": state.get("persona"),
                 "provider": state.get("provider"),
                 "model": state.get("model"),
                 "status": state.get("status"),
                 "team": current_team
             }
             info_list.append(info)
        return info_list

    # --- Cleanup ---

    async def cleanup_providers(self):
        """ Cleans up provider sessions and saves persistent state. """
        logger.info("Manager: Cleaning up LLM providers, saving metrics, and saving quarantine state...");
        active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}
        provider_tasks = [asyncio.create_task(self._close_provider_safe(p)) for p in active_providers if hasattr(p, 'close_session')]
        metrics_save_task = asyncio.create_task(self.performance_tracker.save_metrics())
        quarantine_save_task = asyncio.create_task(self.key_manager.save_quarantine_state())

        all_cleanup_tasks = provider_tasks + [metrics_save_task, quarantine_save_task]
        if all_cleanup_tasks:
            await asyncio.gather(*all_cleanup_tasks)
            logger.info("Manager: Provider cleanup, metrics saving, and quarantine saving complete.")
        else:
            logger.info("Manager: No provider cleanup or saving needed.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        """ Safely closes a provider session if possible. """
        try:
             if hasattr(provider, 'close_session') and callable(provider.close_session):
                 await provider.close_session()
                 logger.info(f"Manager: Closed session for {provider!r}")
             else:
                 logger.debug(f"Manager: Provider {provider!r} does not have a close_session method.")
        except Exception as e:
            logger.error(f"Manager: Error closing session for {provider!r}: {e}", exc_info=True)
