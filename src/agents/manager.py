# START OF FILE src/agents/manager.py
import logging # Ensure logging is imported
# --- Logging statement at the very top ---
logging.info("manager.py: Module loading started...")
# --- End Logging statement ---

import asyncio
from typing import Dict, Any, Optional, List, Tuple, Set
import json
import os
import traceback
import time
# import logging (already imported)
import uuid
import fnmatch
import copy

# --- Import DB Manager and Models ---
logging.info("manager.py: Importing database_manager...")
from src.core.database_manager import db_manager, close_db_connection, Project as DBProject, Session as DBSession # Import singleton and specific models if needed
logging.info("manager.py: Imported database_manager.")

# --- Agent Status/Type/ID Constants ---
logging.info("manager.py: Importing constants...")
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_ERROR, BOOTSTRAP_AGENT_ID,
    AGENT_TYPE_PM, AGENT_STATE_CONVERSATION, AGENT_STATE_MANAGE # Add PM states/type
)
logging.info("manager.py: Imported constants.")

# --- Import Agent class for type hinting ---
logging.info("manager.py: Importing Agent core...")
from src.agents.core import Agent
logging.info("manager.py: Imported Agent core.")

# Import settings, model_registry, AND BASE_DIR
logging.info("manager.py: Importing settings...")
from src.config.settings import settings, model_registry, BASE_DIR
logging.info("manager.py: Imported settings.")

# Import WebSocket broadcast function
logging.info("manager.py: Importing websocket_manager...")
from src.api.websocket_manager import broadcast
logging.info("manager.py: Imported websocket_manager.")

# Import ToolExecutor
logging.info("manager.py: Importing ToolExecutor...")
from src.tools.executor import ToolExecutor
logging.info("manager.py: Imported ToolExecutor.")

# Import the component managers and utils
logging.info("manager.py: Importing state_manager...")
from src.agents.state_manager import AgentStateManager
logging.info("manager.py: Imported state_manager.")
logging.info("manager.py: Importing session_manager...")
from src.agents.session_manager import SessionManager
logging.info("manager.py: Imported session_manager.")
logging.info("manager.py: Importing interaction_handler...")
from src.agents.interaction_handler import AgentInteractionHandler
logging.info("manager.py: Imported interaction_handler.")
logging.info("manager.py: Importing cycle_handler...")
# --- *** MODIFIED: Removed import of MAX_FAILOVER_ATTEMPTS *** ---
from src.agents.cycle_handler import AgentCycleHandler
# --- *** END MODIFICATION *** ---
logging.info("manager.py: Imported cycle_handler.")
logging.info("manager.py: Importing performance_tracker...")
from src.agents.performance_tracker import ModelPerformanceTracker
logging.info("manager.py: Imported performance_tracker.")
logging.info("manager.py: Importing provider_key_manager...")
from src.agents.provider_key_manager import ProviderKeyManager
logging.info("manager.py: Imported provider_key_manager.")

# Import the refactored module functions
logging.info("manager.py: Importing agent_lifecycle...")
from src.agents import agent_lifecycle
logging.info("manager.py: Imported agent_lifecycle.")
# Import the failover handler function
logging.info("manager.py: Importing failover_handler...")
from src.agents.failover_handler import handle_agent_model_failover
logging.info("manager.py: Imported failover_handler.")
# --- NEW: Import Workflow Manager ---
logging.info("manager.py: Importing workflow_manager...")
from src.agents.workflow_manager import AgentWorkflowManager
logging.info("manager.py: Imported workflow_manager.")
# --- END NEW ---

from pathlib import Path # Used

# Import BaseLLMProvider types (still needed for _close_provider_safe)
logging.info("manager.py: Importing BaseLLMProvider...")
from src.llm_providers.base import BaseLLMProvider
logging.info("manager.py: Imported BaseLLMProvider.")

logger = logging.getLogger(__name__)

# Constants
# BOOTSTRAP_AGENT_ID imported from constants
DEFAULT_PROJECT_NAME = "DefaultProject"

# Class definition starts here...
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
        logger.info("AgentManager __init__: Instantiating ProviderKeyManager...")
        self.key_manager = ProviderKeyManager(settings.PROVIDER_API_KEYS, settings)
        logger.info("AgentManager __init__: ProviderKeyManager instantiated.")
        logger.info("AgentManager __init__: Instantiating ToolExecutor...");
        self.tool_executor = ToolExecutor()
        self.tool_descriptions_xml = self.tool_executor.get_formatted_tool_descriptions_xml()
        self.tool_descriptions_json = self.tool_executor.get_formatted_tool_descriptions_json()
        logger.info(f"AgentManager __init__: ToolExecutor instantiated with tools: {list(self.tool_executor.tools.keys())}")
        logger.info("AgentManager __init__: Instantiating AgentStateManager...");
        self.state_manager = AgentStateManager(self)
        logger.info("AgentManager __init__: AgentStateManager instantiated.")
        logger.info("AgentManager __init__: Instantiating SessionManager...");
        self.session_manager = SessionManager(self, self.state_manager)
        logger.info("AgentManager __init__: SessionManager instantiated.")
        logger.info("AgentManager __init__: Instantiating AgentInteractionHandler...");
        self.interaction_handler = AgentInteractionHandler(self)
        logger.info("AgentManager __init__: AgentInteractionHandler instantiated.")
        logger.info("AgentManager __init__: Instantiating AgentCycleHandler...");
        self.cycle_handler = AgentCycleHandler(self, self.interaction_handler)
        logger.info("AgentManager __init__: AgentCycleHandler instantiated.")
        logger.info("AgentManager __init__: Instantiating ModelPerformanceTracker...");
        self.performance_tracker = ModelPerformanceTracker()
        logger.info("AgentManager __init__: ModelPerformanceTracker instantiated and metrics loaded.")
        # --- NEW: Instantiate Workflow Manager ---
        logger.info("AgentManager __init__: Instantiating AgentWorkflowManager...");
        self.workflow_manager = AgentWorkflowManager()
        logger.info("AgentManager __init__: AgentWorkflowManager instantiated.")
        # --- END NEW ---
        self._ensure_projects_dir()
        # --- NEW: PM Manage Timer Task ---
        self._pm_manage_task: Optional[asyncio.Task] = None
        # --- END NEW ---
        logger.info("AgentManager __init__: Initialized synchronously. Bootstrap agents, model discovery, and timers run asynchronously.")
        # Start default session DB logging on init
        asyncio.create_task(self._ensure_default_db_session())
        # --- NEW: Start PM Manage Timer ---
        asyncio.create_task(self.start_pm_manage_timer())
        # --- END NEW ---

    async def _ensure_default_db_session(self):
        """Ensures the default project and a new session are logged in the DB on startup."""
        if self.current_session_db_id is None:
             await self.set_project_session_context(DEFAULT_PROJECT_NAME, f"startup_{int(time.time())}")

    async def set_project_session_context(self, project_name: str, session_name: str, loading: bool = False):
        """
        Sets the current project/session context AND updates the database records.
        When loading=True, it attempts to find the existing DB session ID.
        """
        logger.info(f"Setting context. Project: {project_name}, Session: {session_name}, Loading: {loading}")

        # End previous DB session if one was active and we are not just reloading the same one
        if self.current_session_db_id is not None and not (loading and self.current_project == project_name and self.current_session == session_name):
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

        # --- DB Session Handling ---
        if loading:
            # Attempt to find the existing session ID in the database
            found_session_id = await self.db_manager.get_session_id_by_name(self.current_project_db_id, session_name)
            if found_session_id:
                self.current_session_db_id = found_session_id
                logger.info(f"DB Context Set (Load): Found existing Session ID {self.current_session_db_id} for {project_name}/{session_name}")
            else:
                 logger.warning(f"DB session record not found for loaded session '{project_name}/{session_name}'. Creating new DB session record.")
                 new_session_record = await self.db_manager.start_session(self.current_project_db_id, session_name)
                 if new_session_record and new_session_record.id:
                     self.current_session_db_id = new_session_record.id
                     logger.info(f"DB Context Set (Load): Created new Session ID {self.current_session_db_id} for {project_name}/{session_name}")
                 else:
                     logger.error(f"Failed to create new session record for loaded session '{project_name}/{session_name}' in database!")
                     self.current_session_db_id = None
        else:
            # Start a new session record if creating/saving
            session_record = await self.db_manager.start_session(self.current_project_db_id, session_name)
            if not session_record or session_record.id is None:
                logger.error(f"Failed to start new session record for '{session_name}' in database!")
                self.current_session_db_id = None
            else:
                self.current_session_db_id = session_record.id
                logger.info(f"DB Context Set (New/Save): Project ID {self.current_project_db_id}, Session ID {self.current_session_db_id}")
        # --- End DB Session Handling ---


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
        if not agent:
            logger.error("Schedule cycle called with invalid Agent object.")
            return
        logger.info(f"Manager: schedule_cycle called for agent '{agent.agent_id}' (Retry: {retry_count}). Creating asyncio task...")
        try:
            # --- ADDED MORE LOGGING ---
            logger.debug(f"Manager: Attempting asyncio.create_task for agent '{agent.agent_id}'...")
            task = asyncio.create_task(self.cycle_handler.run_cycle(agent, retry_count))
            logger.info(f"Manager: Successfully created asyncio task object {task.get_name()} for agent '{agent.agent_id}' cycle.")
            # --- END ADDED LOGGING ---
        except Exception as e:
            logger.error(f"Manager: FAILED to create asyncio task for agent '{agent.agent_id}' cycle: {e}", exc_info=True)

    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        # --- ADDED LOGGING ---
        logger.info(f"Manager: handle_user_message ENTERED. Message: '{message[:100]}...', Client ID: {client_id}")
        # --- END ADDED LOGGING ---

        # Ensure context and log interaction
        if self.current_project is None or self.current_session_db_id is None:
            logger.info("Manager: No active project/DB session context. Setting default...")
            await self.set_project_session_context(DEFAULT_PROJECT_NAME, f"session_{int(time.time())}")
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
        if not admin_agent:
            logger.error(f"Manager: Admin AI ('{BOOTSTRAP_AGENT_ID}') not found.");
            await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."});
            return

        # --- ADDED LOGGING ---
        logger.info(f"Manager: Found Admin AI. Status: {admin_agent.status}. Checking if idle...")
        # --- END ADDED LOGGING ---
        if admin_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"Manager: Admin AI is IDLE. Appending user message and scheduling cycle.")
            admin_agent.message_history.append({"role": "user", "content": message});
            await self.schedule_cycle(admin_agent, 0) # Schedule the cycle
        else:
            logger.info(f"Manager: Admin AI busy ({admin_agent.status}). Message queued.");
            admin_agent.message_history.append({"role": "user", "content": message});
            await self.push_agent_status_update(admin_agent.agent_id);
            await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI busy ({admin_agent.status}). Queued." })
        # --- ADDED LOGGING ---
        logger.info(f"Manager: handle_user_message EXITED for message: '{message[:100]}...'")
        # --- END ADDED LOGGING ---

    # --- Failover Handling (Delegation) ---
    async def handle_agent_model_failover(self, agent_id: str, last_error_obj: Exception) -> bool: # Add return type hint
        """ Delegates failover handling to the imported failover handler function and returns its result. """
        # --- FIXED: Added return statement & logging ---
        result = await handle_agent_model_failover(self, agent_id, last_error_obj)
        logger.debug(f"AgentManager.handle_agent_model_failover: Returning result = {result} for agent '{agent_id}'.")
        # +++ END ADDED LOGGING +++
        return result

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

        fs_success, fs_message = await self.session_manager.save_session(project_name, session_name)
        if not fs_success: return False, fs_message
        await self.set_project_session_context(project_name, session_name, loading=False)
        if not self.current_session_db_id:
            logger.error(f"Failed to update database session record after saving session '{session_name}'.")
            # Even if DB update fails, FS save succeeded, so return True but with error message
            return True, f"{fs_message} but failed to update database session record."

        # --- Auto-create Project Manager Agent for the new session ---
        pm_creation_message = ""
        try:
            import re # Import re locally for sanitization
            # Sanitize names for use in agent ID
            sanitized_project = re.sub(r'\W+', '_', project_name)
            sanitized_session = re.sub(r'\W+', '_', session_name)
            pm_instance_id = f"pm_{sanitized_project}_{sanitized_session}"
            pm_bootstrap_id = "project_manager_agent" # ID from config.yaml

            if pm_instance_id not in self.agents:
                 logger.info(f"Attempting to auto-create Project Manager '{pm_instance_id}' for session '{project_name}/{session_name}'...")
                 pm_config = settings.get_agent_config_by_id(pm_bootstrap_id)
                 if pm_config:
                     pm_persona = pm_config.get("persona", "Project Manager") + f" ({project_name}/{session_name})" # Unique persona
                     pm_system_prompt = pm_config.get("system_prompt", "Manage project tasks.")
                     pm_provider = pm_config.get("provider")
                     pm_model = pm_config.get("model")
                     pm_temp = pm_config.get("temperature")
                     pm_extra_kwargs = {k: v for k, v in pm_config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona']}

                     pm_create_success, pm_create_msg, pm_created_id = await self.create_agent_instance(
                         agent_id_requested=pm_instance_id,
                         provider=pm_provider,
                         model=pm_model,
                         system_prompt=pm_system_prompt,
                         persona=pm_persona,
                         team_id=None, # PM agent is not assigned to a team initially
                         temperature=pm_temp,
                         **pm_extra_kwargs
                     )
                     if pm_create_success and pm_created_id:
                         logger.info(f"Successfully auto-created Project Manager agent '{pm_created_id}' for session.")
                         pm_creation_message = f" [Auto-created Project Manager: '{pm_created_id}'.]"
                     else:
                         logger.error(f"Failed to auto-create Project Manager agent for session: {pm_create_msg}")
                         pm_creation_message = f" [Warning: Failed to auto-create Project Manager: {pm_create_msg}]"
                 else:
                     logger.warning(f"Could not find configuration for bootstrap agent '{pm_bootstrap_id}'. Cannot auto-create Project Manager.")
                     pm_creation_message = f" [Warning: Config for '{pm_bootstrap_id}' not found, cannot auto-create PM.]"
            else:
                logger.info(f"Project Manager agent '{pm_instance_id}' already exists for this session. Skipping auto-creation.")
                pm_creation_message = f" [Note: Project Manager '{pm_instance_id}' already exists.]"
        except Exception as pm_err:
            logger.error(f"Error during Project Manager auto-creation: {pm_err}", exc_info=True)
            pm_creation_message = f" [Error during PM auto-creation: {pm_err}]"
        # --- End Auto-create ---

        final_message = f"{fs_message} Session context and DB record updated." + pm_creation_message
        return True, final_message


    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        logger.info(f"Manager: Initiating load_session for '{project_name}/{session_name}'...")
        fs_success, fs_message = await self.session_manager.load_session(project_name, session_name)
        if not fs_success: return False, fs_message
        await self.set_project_session_context(project_name, session_name, loading=True)
        if not self.current_session_db_id:
             logger.warning(f"Could not find or create a DB session record for loaded session '{project_name}/{session_name}'. Interaction logging might fail.")
             fs_message += " (Warning: DB session record not found/created)"
        return True, fs_message

    # --- NEW: Framework-driven Project/PM Creation ---
    async def create_project_and_pm_agent(self, project_title: str, plan_description: str) -> Tuple[bool, str, Optional[str]]:
        """
         Handles the automatic creation of a project task and its dedicated PM agent.
         Called by CycleHandler when Admin AI submits a plan.
         """
        from src.tools.project_management import ProjectManagementTool, TASKLIB_AVAILABLE, Task # Import Task locally
        from src.agents.constants import ADMIN_STATE_CONVERSATION # Import locally
        import re # Import re locally for sanitization

        logger.info(f"Framework attempting to create project '{project_title}' and PM agent.")
        if not self.current_project or not self.current_session:
            msg = "Framework Error: Cannot create project/PM agent without active project/session context."
            logger.error(msg)
            return False, msg, None

        # --- REMOVED Old Task Creation Logic ---

        # 2. Create PM Agent Instance (Keep this part)
        pm_agent_id = None
        pm_creation_success = False
        pm_creation_message = f"Failed to create PM agent for project '{project_title}'."
        try:
            # Sanitize names for use in agent ID
            sanitized_project = re.sub(r'\W+', '_', project_title)
            sanitized_session = re.sub(r'\W+', '_', self.current_session)
            pm_instance_id = f"pm_{sanitized_project}_{sanitized_session}"
            pm_bootstrap_id = "project_manager_agent" # ID from config.yaml

            if pm_instance_id not in self.agents:
                 logger.info(f"Attempting to create Project Manager '{pm_instance_id}' for project '{project_title}'...")
                 # --- MODIFIED LOGIC ---
                 # Get base config for persona/temp etc., but don't rely on its provider/model
                 pm_config_base = settings.get_agent_config_by_id(pm_bootstrap_id)

                 if pm_config_base:
                     pm_persona_base = pm_config_base.get("persona", "Project Manager")
                     pm_persona = pm_persona_base + f" ({project_title})" # Unique persona

                     # Try getting prompt from prompts.json first (key assumed to match bootstrap ID)
                     pm_system_prompt = settings.PROMPTS.get(pm_bootstrap_id)
                     if not pm_system_prompt:
                         pm_system_prompt = pm_config_base.get("system_prompt") # Fallback to config
                     if not pm_system_prompt:
                         pm_system_prompt = "Manage project tasks." # Hardcoded default fallback
                         logger.warning(f"Could not find system prompt for '{pm_bootstrap_id}' in prompts.json or config. Using default.")

                     pm_temp = pm_config_base.get("temperature") # Get temp if possible
                     # Get other kwargs, excluding ones handled by create_agent_instance or lifecycle
                     pm_extra_kwargs = {k: v for k, v in pm_config_base.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona']}
                     pm_extra_kwargs['plan_description'] = plan_description  # Add plan_description to kwargs
                     pm_extra_kwargs['task_description'] = plan_description  # Add task_description as well for compatibility

                     # Call create_agent_instance forcing auto-selection for provider/model
                     logger.info(f"Calling create_agent_instance for '{pm_instance_id}' with provider=None, model=None to force auto-selection.")
                     pm_creation_success, pm_creation_message, pm_agent_id = await self.create_agent_instance(
                         agent_id_requested=pm_instance_id,
                         provider=None, # Force auto-selection
                         model=None,    # Force auto-selection
                         system_prompt=pm_system_prompt,
                         persona=pm_persona,
                         team_id=None, # PM not in a team initially
                         temperature=pm_temp,
                         **pm_extra_kwargs
                     )
                     # --- NEW: Set approval flag on successful PM creation ---
                     if pm_creation_success and pm_agent_id:
                         pm_agent = self.agents.get(pm_agent_id)
                         if pm_agent:
                             pm_agent._awaiting_project_approval = True
                             logger.info(f"Set _awaiting_project_approval flag for new PM agent '{pm_agent_id}'.")
                         else:
                             logger.error(f"Could not find newly created PM agent '{pm_agent_id}' to set approval flag!")
                     # --- END NEW ---
                 else:
                     # Config for the bootstrap PM agent itself wasn't found
                     pm_creation_success = False
                     pm_creation_message = f"Config for bootstrap agent '{pm_bootstrap_id}' not found. Cannot create PM."
                     logger.error(pm_creation_message)
                 # --- END MODIFIED LOGIC ---
            else:
                pm_agent_id = pm_instance_id
                pm_creation_success = True # Agent already exists
                pm_creation_message = f"Project Manager agent '{pm_agent_id}' already exists for this project/session."
                logger.info(pm_creation_message)

        except Exception as pm_err:
            logger.error(f"Error during PM agent creation for project '{project_title}': {pm_err}", exc_info=True)
            pm_creation_message = f"Error creating PM agent: {pm_err}"

        if not pm_creation_success or not pm_agent_id:
            # If PM creation failed, return the error
            logger.error(f"PM Agent creation failed for project '{project_title}'. Cannot proceed.")
            return False, f"Failed to create PM agent: {pm_creation_message}", None

        # 3. Create Initial "Project Plan" Task using ToolExecutor
        task_creation_success = False
        task_creation_message = f"Failed to create initial 'Project Plan' task for project '{project_title}'."
        task_uuid = None

        try:
            logger.info(f"Attempting to add 'Project Plan' task via ToolExecutor for PM '{pm_agent_id}'...")
            tool_args = {
                "action": "add_task",
                "description": f"Project Plan: {project_title}\n\n{plan_description}", # Combine title and plan
                "priority": "H",
                "project_filter": project_title, # Set the project field
                "tags": ["project_plan"], # Use standard tag
                "assignee_agent_id": pm_agent_id # Assign to the new PM
            }
            # Execute using ToolExecutor - Execute as 'framework'
            pm_agent = self.agents.get(pm_agent_id)
            if not pm_agent:
                 raise ValueError(f"Could not find PM agent '{pm_agent_id}' after creation.")

            # Execute the tool call using "framework" as the agent_id
            task_result = await self.tool_executor.execute_tool(
                agent_id="framework", # Explicitly identify as framework call
                agent_sandbox_path=pm_agent.sandbox_path, # Still use PM's sandbox context
                tool_name="project_management",
                tool_args=tool_args,
                project_name=self.current_project, # Pass context
                session_name=self.current_session   # Pass context
            )

            # --- Corrected Logic for Checking Task Creation Result ---
            if isinstance(task_result, dict) and task_result.get("status") == "success":
                task_uuid = task_result.get("task_uuid")
                task_id = task_result.get("task_id")
                task_creation_success = True
                task_creation_message = f"Successfully created 'Project Plan' task (ID: {task_id}, UUID: {task_uuid}) and assigned to PM '{pm_agent_id}'."
                logger.info(task_creation_message)
            else:
                # Handle cases where task_result is not a success dict or not a dict at all
                error_detail = task_result.get("message", "Unknown error from tool execution.") if isinstance(task_result, dict) else str(task_result)
                task_creation_message = f"Failed to create 'Project Plan' task via ToolExecutor: {error_detail}"
                logger.error(task_creation_message)
                task_creation_success = False # Ensure flag is false on failure
            # --- End Corrected Logic ---

        except Exception as task_err:
            logger.error(f"Exception during 'Project Plan' task creation via ToolExecutor: {task_err}", exc_info=True)
            task_creation_message = f"Failed to create 'Project Plan' task due to exception: {task_err}"
            task_creation_success = False # Ensure success is false on exception

        # --- CORRECTED: Check task_creation_success flag and adjust final message ---
        if not task_creation_success:
            # Log error but proceed with approval request.
            logger.error(f"Proceeding with PM agent '{pm_agent_id}' but failed to create initial plan task: {task_creation_message}")
            # Modify the final message to indicate the task failure clearly
            final_status_message = f"Project '{project_title}' PM agent '{pm_agent_id}' created, but failed to add initial plan task ({task_creation_message}). Awaiting user approval to start."
        else:
            # Task creation was successful
            final_status_message = f"Project '{project_title}' created and PM agent '{pm_agent_id}' assigned. {task_creation_message}. Awaiting user approval to start."


        # 4. Send Approval Notification to UI (AFTER task creation attempt)
        logger.info(f"Framework: {final_status_message}") # Log the accurate final status

        # --- Add a small delay before sending the approval notification ---
        await asyncio.sleep(0.1) # e.g., 100ms delay
        # --- End delay ---

        await self.send_to_ui({
            "type": "project_pending_approval",
            "project_title": project_title,
            "plan_content": plan_description, # Send the original plan content
            "pm_agent_id": pm_agent_id,
            # Use the accurate status message for the UI notification
            "message": f"Project '{project_title}' is planned ({'Task OK' if task_creation_success else 'Task Failed'}). Please approve to start execution."
        })

        # --- REMOVED: Auto-scheduling of PM agent ---
        # pm_agent = self.agents.get(pm_agent_id)
        # if pm_agent:
        #     logger.info(f"Scheduling initial cycle for new PM agent '{pm_agent_id}'...")
        #     asyncio.create_task(self.schedule_cycle(pm_agent))
        # else:
        #     logger.error(f"Could not find newly created PM agent '{pm_agent_id}' in manager to schedule cycle!")
        # --- END REMOVED ---

        return True, final_status_message, pm_agent_id # Use the correct variable name
    # --- END NEW ---


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
        if self.current_session_db_id is not None:
             logger.info(f"Ending final active DB session ID: {self.current_session_db_id}")
             await self.db_manager.end_session(self.current_session_db_id)
             self.current_session_db_id = None

        active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}
        provider_tasks = [asyncio.create_task(self._close_provider_safe(p)) for p in active_providers if hasattr(p, 'close_session')]
        metrics_save_task = asyncio.create_task(self.performance_tracker.save_metrics())
        quarantine_save_task = asyncio.create_task(self.key_manager.save_quarantine_state())
        all_cleanup_tasks = provider_tasks + [metrics_save_task, quarantine_save_task]
        if all_cleanup_tasks: await asyncio.gather(*all_cleanup_tasks); logger.info("Manager: Provider cleanup, metrics saving, and quarantine saving complete.")
        else: logger.info("Manager: No provider cleanup or saving needed.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        try:
             if hasattr(provider, 'close_session') and callable(provider.close_session): await provider.close_session(); logger.info(f"Manager: Closed session for {provider!r}")
             else: logger.debug(f"Manager: Provider {provider!r} does not have close_session.")
        except Exception as e: logger.error(f"Manager: Error closing session for {provider!r}: {e}", exc_info=True)

    # --- NEW: PM Manage State Timer Logic ---
    async def _periodic_pm_manage_check(self):
        """Periodically checks for idle PM agents and transitions them to the MANAGE state."""
        interval = settings.PM_MANAGE_CHECK_INTERVAL_SECONDS
        logger.info(f"Starting periodic PM manage check loop (Interval: {interval}s)...")
        while True:
            await asyncio.sleep(interval)
            logger.debug("Running periodic PM manage check...")
            try:
                idle_pms_to_activate = []
                # Iterate over a copy of agent values to avoid issues if dict changes during iteration
                agents_snapshot = list(self.agents.values())
                for agent in agents_snapshot:
                    if (agent.agent_type == AGENT_TYPE_PM and
                        agent.status == AGENT_STATUS_IDLE and
                        agent.state == AGENT_STATE_CONVERSATION): # Only activate idle PMs in conversation state
                        idle_pms_to_activate.append(agent)

                if idle_pms_to_activate:
                    logger.info(f"Found {len(idle_pms_to_activate)} idle PM agent(s) potentially ready for MANAGE state.")
                    for pm_agent in idle_pms_to_activate:
                        # --- NEW: Check if project is awaiting approval ---
                        if getattr(pm_agent, '_awaiting_project_approval', False):
                            logger.debug(f"Skipping MANAGE state transition for PM '{pm_agent.agent_id}': Project awaiting approval.")
                            continue # Skip this PM for now
                        # --- END NEW ---

                        logger.debug(f"Attempting to transition PM '{pm_agent.agent_id}' to MANAGE state.")
                        state_changed = False
                        if hasattr(self, 'workflow_manager'):
                            state_changed = self.workflow_manager.change_state(pm_agent, AGENT_STATE_MANAGE)
                        else:
                            logger.error("WorkflowManager not found on AgentManager. Cannot change PM state.")

                        if state_changed:
                            logger.info(f"Transitioned PM '{pm_agent.agent_id}' to MANAGE state. Scheduling cycle.")
                            await self.schedule_cycle(pm_agent, 0) # Schedule cycle immediately after state change
                        else:
                            logger.warning(f"Failed to transition PM '{pm_agent.agent_id}' to MANAGE state (already in state or invalid transition?).")
            except Exception as e:
                logger.error(f"Error during periodic PM manage check: {e}", exc_info=True)
                # Optional: Add a longer sleep after an error to prevent rapid error loops
                await asyncio.sleep(interval * 2)

    async def start_pm_manage_timer(self):
        """Starts the background task for periodic PM checks if not already running."""
        if self._pm_manage_task is None or self._pm_manage_task.done():
            logger.info("Starting PM manage timer background task.")
            self._pm_manage_task = asyncio.create_task(self._periodic_pm_manage_check())
        else:
            logger.info("PM manage timer task already running.")

    async def stop_pm_manage_timer(self):
        """Stops the background task for periodic PM checks."""
        if self._pm_manage_task and not self._pm_manage_task.done():
            logger.info("Stopping PM manage timer background task...")
            self._pm_manage_task.cancel()
            try:
                await self._pm_manage_task # Wait for cancellation
            except asyncio.CancelledError:
                logger.info("PM manage timer task successfully cancelled.")
            except Exception as e:
                logger.error(f"Error stopping PM manage timer task: {e}", exc_info=True)
            self._pm_manage_task = None
        else:
            logger.info("PM manage timer task not running or already stopped.")
    # --- END NEW ---

    # --- Cleanup ---
    async def cleanup_providers(self):
        logger.info("Manager: Cleaning up LLM providers, saving metrics, saving quarantine state, stopping timers, and closing DB...");
        # --- NEW: Stop Timer ---
        await self.stop_pm_manage_timer()
        # --- END NEW ---
        if self.current_session_db_id is not None:
             logger.info(f"Ending final active DB session ID: {self.current_session_db_id}")
             await self.db_manager.end_session(self.current_session_db_id)
             self.current_session_db_id = None

        active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}
        provider_tasks = [asyncio.create_task(self._close_provider_safe(p)) for p in active_providers if hasattr(p, 'close_session')]
        metrics_save_task = asyncio.create_task(self.performance_tracker.save_metrics())
        quarantine_save_task = asyncio.create_task(self.key_manager.save_quarantine_state())
        all_cleanup_tasks = provider_tasks + [metrics_save_task, quarantine_save_task]
        if all_cleanup_tasks: await asyncio.gather(*all_cleanup_tasks); logger.info("Manager: Provider cleanup, metrics saving, and quarantine saving complete.")
        else: logger.info("Manager: No provider cleanup or saving needed.")
        # Close DB connection after all other cleanup
        await close_db_connection()
        logger.info("Manager: Database connection closed.")


# --- Logging statement at the very end ---
logging.info("manager.py: Module loading finished.")
# --- End Logging statement ---
