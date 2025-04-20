# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List, Tuple, Set
import json
import os
import traceback
import time
import logging
import uuid
import fnmatch
import copy

# --- Import DB Manager and Models ---
from src.core.database_manager import db_manager, close_db_connection, Project as DBProject, Session as DBSession # Import singleton and specific models if needed

# --- Agent Status Constants ---
from src.agents.constants import AGENT_STATUS_IDLE, AGENT_STATUS_ERROR

# --- Import Agent class for type hinting ---
from src.agents.core import Agent

# Import settings, model_registry, AND BASE_DIR
from src.config.settings import settings, model_registry, BASE_DIR

# Import WebSocket broadcast function
from src.api.websocket_manager import broadcast

# Import ToolExecutor
from src.tools.executor import ToolExecutor

# Import the component managers and utils
from src.agents.state_manager import AgentStateManager
from src.agents.session_manager import SessionManager
from src.agents.interaction_handler import AgentInteractionHandler
# Import cycle handler and failover constants/types
from src.agents.cycle_handler import AgentCycleHandler, MAX_FAILOVER_ATTEMPTS
from src.agents.performance_tracker import ModelPerformanceTracker
from src.agents.provider_key_manager import ProviderKeyManager

# Import the refactored module functions
from src.agents import agent_lifecycle # Use the module directly
# Import the failover handler function
from src.agents.failover_handler import handle_agent_model_failover # Keep this

from pathlib import Path

# Import BaseLLMProvider types (still needed for _close_provider_safe)
from src.llm_providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)

# Constants
BOOTSTRAP_AGENT_ID = "admin_ai"
DEFAULT_PROJECT_NAME = "DefaultProject"

class AgentManager:
    """
    Main coordinator for agents. Initializes components and delegates tasks like
    agent creation, deletion, message handling, failover, session management,
    and database logging to specialized handlers/modules.
    """
    def __init__(self, websocket_manager: Optional[Any] = None):
        self.bootstrap_agents: List[str] = []
        self.agents: Dict[str, Agent] = {}
        self.send_to_ui_func = broadcast
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None
        # --- DB State ---
        self.db_manager = db_manager # Use the singleton instance
        self.current_project_db_id: Optional[int] = None
        self.current_session_db_id: Optional[int] = None
        # --- End DB State ---
        logger.info("Instantiating ProviderKeyManager...")
        self.key_manager = ProviderKeyManager(settings.PROVIDER_API_KEYS, settings)
        logger.info("ProviderKeyManager instantiated.")
        logger.info("Instantiating ToolExecutor...");
        self.tool_executor = ToolExecutor()
        self.tool_descriptions_xml = self.tool_executor.get_formatted_tool_descriptions_xml()
        self.tool_descriptions_json = self.tool_executor.get_formatted_tool_descriptions_json()
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
        # Start default session DB logging on init
        asyncio.create_task(self._ensure_default_db_session())

    async def _ensure_default_db_session(self):
        """Ensures the default project and a new session are logged in the DB on startup."""
        if self.current_session_db_id is None:
             await self.set_project_session_context(DEFAULT_PROJECT_NAME, f"startup_{int(time.time())}")

    async def set_project_session_context(self, project_name: str, session_name: str, loading: bool = False):
        """Sets the current project/session context AND updates the database records."""
        logger.info(f"Setting context. Project: {project_name}, Session: {session_name}, Loading: {loading}")

        # End previous DB session if one was active
        if self.current_session_db_id is not None:
            logger.debug(f"Ending previous DB session ID: {self.current_session_db_id}")
            await self.db_manager.end_session(self.current_session_db_id)
            self.current_session_db_id = None
            self.current_project_db_id = None

        self.current_project = project_name
        self.current_session = session_name

        # Get or create project in DB
        project_record = await self.db_manager.get_project_by_name(project_name)
        if not project_record:
            project_record = await self.db_manager.add_project(name=project_name)

        if not project_record or project_record.id is None:
            logger.error(f"Failed to get or create project record for '{project_name}' in database!")
            self.current_project_db_id = None
            self.current_session_db_id = None
            return # Cannot proceed without project ID

        self.current_project_db_id = project_record.id

        # Start new session in DB only if *not* loading (load_session handles its own DB check)
        # Also start if loading=False (meaning new session or save_session)
        if not loading:
             session_record = await self.db_manager.start_session(self.current_project_db_id, session_name)
             if not session_record or session_record.id is None:
                 logger.error(f"Failed to start session record for '{session_name}' in database!")
                 self.current_session_db_id = None
             else:
                 self.current_session_db_id = session_record.id
                 logger.info(f"DB Context Updated: Project ID {self.current_project_db_id}, Session ID {self.current_session_db_id}")
        else:
             # When loading, we need to find the existing session ID
             # This logic should ideally live within load_session or db_manager
             logger.warning("Need to implement finding existing session ID during load_session")
             # Placeholder: Set to None, load_session needs to set this correctly
             self.current_session_db_id = None


    def _ensure_projects_dir(self):
        try: settings.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True); logger.info(f"Ensured projects dir: {settings.PROJECTS_BASE_DIR}")
        except Exception as e: logger.error(f"Error creating projects dir {settings.PROJECTS_BASE_DIR}: {e}", exc_info=True)

    # --- Initialization and Lifecycle (Delegated) ---
    async def initialize_bootstrap_agents(self):
        await agent_lifecycle.initialize_bootstrap_agents(self)
        # Log bootstrap agent creation to DB
        if self.current_session_db_id is not None:
            for agent_id in self.bootstrap_agents:
                agent = self.agents.get(agent_id)
                if agent:
                     config_to_log = agent.agent_config.get("config", {})
                     await self.db_manager.add_agent_record(
                         session_id=self.current_session_db_id,
                         agent_id=agent.agent_id,
                         persona=agent.persona,
                         model_config_dict=config_to_log
                     )
        else:
             logger.warning("Cannot log bootstrap agent DB records: current_session_db_id is None.")


    async def create_agent_instance( self, agent_id_requested: Optional[str], provider: Optional[str], model: Optional[str], system_prompt: str, persona: str, team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs ) -> Tuple[bool, str, Optional[str]]:
        success, message, created_agent_id = await agent_lifecycle.create_agent_instance(self, agent_id_requested, provider, model, system_prompt, persona, team_id, temperature, **kwargs)
        # Log dynamic agent creation to DB
        if success and created_agent_id and self.current_session_db_id is not None:
            agent = self.agents.get(created_agent_id)
            if agent:
                 config_to_log = agent.agent_config.get("config", {})
                 await self.db_manager.add_agent_record(
                     session_id=self.current_session_db_id,
                     agent_id=agent.agent_id,
                     persona=agent.persona,
                     model_config_dict=config_to_log
                 )
        elif success and created_agent_id and self.current_session_db_id is None:
             logger.warning(f"Agent '{created_agent_id}' created but cannot log to DB: current_session_db_id is None.")
        return success, message, created_agent_id

    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]:
        # Deletion logic doesn't change DB records for history, just runtime state
        return await agent_lifecycle.delete_agent_instance(self, agent_id)

    # --- Message Handling and Execution ---
    async def schedule_cycle(self, agent: Agent, retry_count: int = 0):
        if not agent: logger.error("Schedule cycle called with invalid Agent object."); return
        logger.debug(f"Manager: Scheduling cycle for agent '{agent.agent_id}' (Retry: {retry_count}).")
        # Log the intention/start of the cycle? Maybe too verbose. Logging happens within CycleHandler.
        asyncio.create_task(self.cycle_handler.run_cycle(agent, retry_count))

    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        logger.info(f"Manager: Received user message for Admin AI: '{message[:100]}...'");

        # Ensure context and log interaction
        if self.current_project is None or self.current_session_db_id is None:
            logger.info("Manager: No active project/DB session context. Setting default...")
            # Setting context also starts the DB session record
            await self.set_project_session_context(DEFAULT_PROJECT_NAME, f"session_{int(time.time())}")
            # Save filesystem session immediately after setting context
            save_success, save_msg = await self.session_manager.save_session(self.current_project, self.current_session)
            await self.send_to_ui({"type": "system_event", "event": "session_saved", "project": self.current_project, "session": self.current_session, "message": f"Context set: {self.current_project}/{self.current_session}" if save_success else f"Failed default context: {save_msg}"})

        if self.current_session_db_id is not None:
            await self.db_manager.log_interaction(
                session_id=self.current_session_db_id,
                agent_id="human_user", # Special ID for user input
                role="user",
                content=message
            )
        else:
             logger.error("Cannot log user message to DB: current_session_db_id is None.")

        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID);
        if not admin_agent: logger.error(f"Manager: Admin AI ('{BOOTSTRAP_AGENT_ID}') not found."); await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."}); return
        if admin_agent.status == AGENT_STATUS_IDLE: # Uses imported constant
            logger.info(f"Manager: Delegating message to '{BOOTSTRAP_AGENT_ID}' and scheduling cycle.")
            admin_agent.message_history.append({"role": "user", "content": message}); await self.schedule_cycle(admin_agent, 0)
        else:
            logger.info(f"Manager: Admin AI busy ({admin_agent.status}). Message queued."); admin_agent.message_history.append({"role": "user", "content": message}); await self.push_agent_status_update(admin_agent.agent_id); await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI busy ({admin_agent.status}). Queued." })

    # --- Failover Handling (Delegation) ---
    async def handle_agent_model_failover(self, agent_id: str, last_error_obj: Exception):
        """ Delegates failover handling to the imported failover handler function. """
        await handle_agent_model_failover(self, agent_id, last_error_obj)

    # --- UI Communication ---
    async def push_agent_status_update(self, agent_id: str):
        agent = self.agents.get(agent_id);
        if agent: state = agent.get_state(); state["team"] = self.state_manager.get_agent_team(agent_id);
        else: state = {"status": "deleted", "team": None}; logger.warning(f"Cannot push status for unknown agent: {agent_id}");
        await self.send_to_ui({"type": "agent_status_update", "agent_id": agent_id, "status": state})

    async def send_to_ui(self, message_data: Dict[str, Any]):
        if not self.send_to_ui_func: logger.warning("UI broadcast func not set."); return;
        try: await self.send_to_ui_func(json.dumps(message_data));
        except Exception as e: logger.error(f"Error sending to UI: {e}. Data: {message_data}", exc_info=True)

    # --- State and Session Management (Delegation & DB Integration) ---
    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        return {aid: (ag.get_state() | {"team": self.state_manager.get_agent_team(aid)}) for aid, ag in self.agents.items()}

    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        logger.info(f"Manager: Initiating save_session for '{project_name}'...")
        if not session_name:
            session_name = f"session_{int(time.time())}"

        # 1. Save filesystem state via SessionManager
        fs_success, fs_message = await self.session_manager.save_session(project_name, session_name)
        if not fs_success:
             return False, fs_message # Filesystem save failed

        # 2. Set context and update/start DB session record
        await self.set_project_session_context(project_name, session_name, loading=False)
        if not self.current_session_db_id:
             return False, f"{fs_message} but failed to update database session record."

        return True, f"{fs_message} Session context and DB record updated."


    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        logger.info(f"Manager: Initiating load_session for '{project_name}/{session_name}'...")

        # 1. Load filesystem state via SessionManager
        fs_success, fs_message = await self.session_manager.load_session(project_name, session_name)
        if not fs_success:
             return False, fs_message

        # 2. Update the database context (Project and Session IDs)
        # This version doesn't start a *new* DB session, it finds the existing one
        await self.set_project_session_context(project_name, session_name, loading=True)

        # Need to find the DB ID for the loaded session
        if self.current_project_db_id is not None:
             # TODO: Implement db_manager.get_session_by_name(project_id, session_name)
             # session_record = await self.db_manager.get_session_by_name(self.current_project_db_id, session_name)
             session_record = None # Placeholder
             if session_record and session_record.id:
                 self.current_session_db_id = session_record.id
                 logger.info(f"Found existing DB Session ID {self.current_session_db_id} for loaded session.")
             else:
                  logger.error(f"Failed to find corresponding DB session record for loaded session '{project_name}/{session_name}'. Interaction logging might fail.")
                  self.current_session_db_id = None # Ensure it's None if not found
        else:
             logger.error("Failed to get project DB ID during session load.")
             self.current_session_db_id = None

        return True, fs_message # Return the message from the filesystem load


    def get_agent_info_list_sync(self, filter_team_id: Optional[str] = None) -> List[Dict[str, Any]]:
        info_list = [];
        for agent_id, agent in self.agents.items():
             current_team = self.state_manager.get_agent_team(agent_id);
             if filter_team_id is not None and current_team != filter_team_id: continue;
             state = agent.get_state(); info = {"agent_id": agent_id, "persona": state.get("persona"), "provider": state.get("provider"), "model": state.get("model"), "status": state.get("status"), "team": current_team}; info_list.append(info);
        return info_list

    # --- Cleanup ---
    async def cleanup_providers(self):
        logger.info("Manager: Cleaning up LLM providers, saving metrics, saving quarantine state, and closing DB...");
        # End the final DB session
        if self.current_session_db_id is not None:
             logger.info(f"Ending final active DB session ID: {self.current_session_db_id}")
             await self.db_manager.end_session(self.current_session_db_id)
             self.current_session_db_id = None

        active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}
        provider_tasks = [asyncio.create_task(self._close_provider_safe(p)) for p in active_providers if hasattr(p, 'close_session')]
        metrics_save_task = asyncio.create_task(self.performance_tracker.save_metrics())
        quarantine_save_task = asyncio.create_task(self.key_manager.save_quarantine_state())
        # Close DB connection pool
        db_close_task = asyncio.create_task(close_db_connection())

        all_cleanup_tasks = provider_tasks + [metrics_save_task, quarantine_save_task, db_close_task]
        if all_cleanup_tasks: await asyncio.gather(*all_cleanup_tasks); logger.info("Manager: Provider cleanup, metrics saving, quarantine saving, and DB closure complete.")
        else: logger.info("Manager: No provider cleanup or saving needed.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        try:
             if hasattr(provider, 'close_session') and callable(provider.close_session): await provider.close_session(); logger.info(f"Manager: Closed session for {provider!r}")
             else: logger.debug(f"Manager: Provider {provider!r} does not have close_session.")
        except Exception as e: logger.error(f"Manager: Error closing session for {provider!r}: {e}", exc_info=True)
