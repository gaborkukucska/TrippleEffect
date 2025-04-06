# START OF FILE src/agents/session_manager.py
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Tuple, Optional

# Import settings for project base directory
from src.config.settings import settings
from src.agents.core import AGENT_STATUS_IDLE # Import status constant

# Type hinting for AgentManager and StateManager
if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.state_manager import AgentStateManager

logger = logging.getLogger(__name__)

class SessionManager:
    """
    Handles saving and loading of the application session state,
    including dynamic agent configurations, histories, and team structures.
    Interacts with AgentManager and AgentStateManager.
    """
    def __init__(self, manager: 'AgentManager', state_manager: 'AgentStateManager'):
        """
        Initializes the SessionManager.

        Args:
            manager: Reference to the main AgentManager.
            state_manager: Reference to the AgentStateManager.
        """
        self._manager = manager
        self._state_manager = state_manager
        logger.info("SessionManager initialized.")

    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        """Saves the current state including dynamic agent configs and histories."""
        if not project_name:
            logger.error("Save session failed: Project name cannot be empty.")
            return False, "Project name cannot be empty."
        if not session_name:
            session_name = f"session_{int(time.time())}"

        session_file_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Saving session state to: {session_file_path}")

        # Prepare data structure to save
        session_data = {
            "project": project_name,
            "session": session_name,
            "timestamp": time.time(),
            "teams": self._state_manager.teams, # Get current team structure
            "agent_to_team": self._state_manager.agent_to_team, # Get current agent mappings
            "dynamic_agents_config": {}, # Store config for DYNAMIC agents only
            "agent_histories": {} # Store histories for ALL agents
        }

        # Iterate through current agents managed by AgentManager
        for agent_id, agent in self._manager.agents.items():
            # Save history for all agents
            try:
                json.dumps(agent.message_history) # Quick check for serializability
                session_data["agent_histories"][agent_id] = agent.message_history
            except TypeError as e:
                logger.error(f"History for agent '{agent_id}' is not JSON serializable: {e}. Saving placeholder.")
                session_data["agent_histories"][agent_id] = [{"role": "system", "content": f"[History Serialization Error: {e}]"}]

            # Save config ONLY for dynamic agents
            if agent_id not in self._manager.bootstrap_agents:
                 try:
                      # Get config stored on the agent instance (includes final combined prompt)
                      config_to_save = agent.agent_config.get("config") if hasattr(agent, 'agent_config') else None
                      if config_to_save:
                          session_data["dynamic_agents_config"][agent_id] = config_to_save
                          logger.debug(f"Saved final config for dynamic agent '{agent_id}'.")
                      else:
                          logger.warning(f"Could not find agent_config attribute on dynamic agent '{agent_id}'. Config not saved.")
                 except Exception as e_cfg:
                      logger.warning(f"Error accessing config for dynamic agent '{agent_id}': {e_cfg}. Config not saved.", exc_info=True)

        # Save to file asynchronously
        try:
            def save_sync():
                session_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(session_file_path, 'w', encoding='utf-8') as f:
                     json.dump(session_data, f, indent=2)

            await asyncio.to_thread(save_sync)
            logger.info(f"Session saved successfully: {session_file_path}")
            # Update manager's current project/session tracking
            self._manager.current_project, self._manager.current_session = project_name, session_name
            # Notify UI via main manager's function
            await self._manager.send_to_ui({"type": "system_event", "event": "session_saved", "project": project_name, "session": session_name})
            return True, f"Session '{session_name}' saved successfully in project '{project_name}'."
        except Exception as e:
            logger.error(f"Error saving session file to {session_file_path}: {e}", exc_info=True)
            return False, f"Error saving session file: {e}"

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        """Loads dynamic agents, teams, and histories from a saved session file."""
        session_file_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Attempting to load session from: {session_file_path}")
        if not session_file_path.is_file():
            return False, f"Session file '{session_name}' not found in project '{project_name}'."

        try:
            # Load session data from file
            def load_sync():
                 with open(session_file_path, 'r', encoding='utf-8') as f:
                     return json.load(f)
            session_data = await asyncio.to_thread(load_sync)

            # --- Clear current dynamic state before loading ---
            dynamic_agents_to_delete = [aid for aid in self._manager.agents if aid not in self._manager.bootstrap_agents]
            logger.info(f"Clearing current dynamic state. Agents to delete: {dynamic_agents_to_delete}")
            # Call AgentManager's delete method which handles cleanup
            delete_tasks = [self._manager.delete_agent_instance(aid) for aid in dynamic_agents_to_delete]
            delete_results = await asyncio.gather(*delete_tasks, return_exceptions=True)
            for i, res in enumerate(delete_results):
                 if isinstance(res, Exception):
                     logger.error(f"Error deleting agent {dynamic_agents_to_delete[i]} during load: {res}")

            # Clear team state using StateManager
            self._state_manager.clear_state()
            # Reset histories for bootstrap agents that remain
            for boot_id in self._manager.bootstrap_agents:
                if boot_id in self._manager.agents:
                     self._manager.agents[boot_id].clear_history() # Resets history to initial prompt
            logger.info("Cleared current dynamic agents and team state.")
            # --- End Clearing ---

            # Load teams and mappings into StateManager
            loaded_teams = session_data.get("teams", {})
            loaded_agent_to_team = session_data.get("agent_to_team", {})
            self._state_manager.load_state(loaded_teams, loaded_agent_to_team)

            # Load dynamic agent configs and histories
            dynamic_configs = session_data.get("dynamic_agents_config", {})
            histories = session_data.get("agent_histories", {})
            logger.info(f"Loaded {len(self._state_manager.teams)} teams and {len(dynamic_configs)} dynamic agent configs from session file.")

            # Recreate dynamic agents using AgentManager's internal method
            creation_tasks = []
            for agent_id, agent_cfg in dynamic_configs.items():
                team_id = self._state_manager.get_agent_team(agent_id) # Get team assignment from loaded state
                creation_tasks.append(self._manager._create_agent_internal(
                    agent_id_requested=agent_id,
                    agent_config_data=agent_cfg, # Use the loaded config
                    is_bootstrap=False,
                    team_id=team_id,
                    loading_from_session=True # Signal that prompt shouldn't be modified
                    ))

            creation_results = await asyncio.gather(*creation_tasks, return_exceptions=True)
            successful_creations = 0; failed_creations = []
            for i, result in enumerate(creation_results):
                 agent_id_attempted = list(dynamic_configs.keys())[i]
                 if isinstance(result, Exception):
                     logger.error(f"Failed recreating agent '{agent_id_attempted}' from session: {result}", exc_info=result)
                     failed_creations.append(f"{agent_id_attempted} (Error: {result})")
                 elif isinstance(result, tuple) and result[0]:
                     successful_creations += 1
                 else:
                     error_msg = result[1] if isinstance(result, tuple) else 'Unknown creation error'
                     logger.error(f"Failed recreating agent '{agent_id_attempted}' from session: {error_msg}")
                     failed_creations.append(f"{agent_id_attempted} (Failed: {error_msg})")

            logger.info(f"Successfully recreated {successful_creations}/{len(dynamic_configs)} dynamic agents.")
            if failed_creations:
                logger.warning(f"Failed to recreate the following agents: {', '.join(failed_creations)}")

            # Restore histories for ALL agents now present (bootstrap + successfully recreated dynamic)
            loaded_history_count = 0
            for agent_id, history in histories.items():
                agent = self._manager.agents.get(agent_id)
                if agent:
                     if isinstance(history, list) and all(isinstance(msg, dict) and 'role' in msg and 'content' in msg for msg in history):
                         agent.message_history = history
                         agent.set_status(AGENT_STATUS_IDLE) # Reset status
                         loaded_history_count += 1
                     else:
                         logger.warning(f"Invalid or missing history format for agent '{agent_id}' in session file. History not loaded.")

            logger.info(f"Loaded histories for {loaded_history_count} agents.")
            self._manager.current_project, self._manager.current_session = project_name, session_name

            # Send full state update to UI via AgentManager
            await asyncio.gather(*(self._manager.push_agent_status_update(aid) for aid in self._manager.agents.keys()))

            load_message = f"Session '{session_name}' loaded successfully. {successful_creations} dynamic agents recreated."
            if failed_creations: load_message += f" Failed to recreate {len(failed_creations)} agents."
            return True, load_message

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error loading session file {session_file_path}: {e}", exc_info=True)
            return False, "Invalid session file format (JSON decode error)."
        except Exception as e:
            logger.error(f"Unexpected error loading session from {session_file_path}: {e}", exc_info=True)
            return False, f"Unexpected error loading session: {e}"
