# START OF FILE src/agents/session_manager.py
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Tuple, Optional, Dict, List, Any

# Import settings for project base directory
from src.config.settings import settings

# --- NEW: Import status and ID constants ---
from src.agents.constants import AGENT_STATUS_IDLE, BOOTSTRAP_AGENT_ID # Import BOOTSTRAP_AGENT_ID
# --- END NEW ---

# Type hinting for AgentManager and StateManager
if TYPE_CHECKING:
    from src.agents.manager import AgentManager # Keep AgentManager for main reference
    from src.agents.state_manager import AgentStateManager

# Import the agent lifecycle module for agent creation/deletion
from src.agents import agent_lifecycle

logger = logging.getLogger(__name__)

class SessionManager:
    """
    Handles saving and loading of the application session state,
    including dynamic agent configurations, histories, and team structures.
    Interacts with AgentManager and AgentStateManager.
    Uses agent_lifecycle module for recreating agents during load.
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
        # (No changes needed in save_session - still reads from manager state)
        if not project_name:
            logger.error("Save session failed: Project name cannot be empty.")
            return False, "Project name cannot be empty."
        if not session_name:
            session_name = f"session_{int(time.time())}"
            logger.info(f"No session name provided, using generated name: {session_name}")

        session_file_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Preparing to save session state to: {session_file_path}")

        current_teams = self._state_manager.teams
        current_agent_to_team = self._state_manager.agent_to_team

        session_data = {
            "project": project_name,
            "session": session_name,
            "timestamp": time.time(),
            "teams": current_teams,
            "agent_to_team": current_agent_to_team,
            "dynamic_agents_config": {},
            "agent_histories": {},
            "agent_states": {} # NEW: To store workflow states
        }

        logger.info(f"Gathering data for save. Current Teams: {list(current_teams.keys())}. Agent Mappings: {len(current_agent_to_team)}")
        dynamic_agent_ids_found = []
        all_agent_ids_found = list(self._manager.agents.keys())

        for agent_id, agent in self._manager.agents.items():
            try:
                json.dumps(agent.message_history)
                session_data["agent_histories"][agent_id] = agent.message_history
                logger.debug(f"  Added history for agent '{agent_id}' (Length: {len(agent.message_history)})")
            except TypeError as e:
                logger.error(f"History for agent '{agent_id}' is not JSON serializable: {e}. Saving placeholder.")
                session_data["agent_histories"][agent_id] = [{"role": "system", "content": f"[History Serialization Error: {e}]"}]

            # Save workflow state if it exists (primarily for Admin AI)
            if hasattr(agent, 'state') and agent.state is not None:
                session_data["agent_states"][agent_id] = agent.state
                logger.debug(f"  Added workflow state '{agent.state}' for agent '{agent_id}'.")

            if agent_id not in self._manager.bootstrap_agents:
                dynamic_agent_ids_found.append(agent_id)
                try:
                    # Save the entire agent_config dictionary associated with the agent instance
                    agent_full_config = getattr(agent, 'agent_config', None)
                    if agent_full_config and isinstance(agent_full_config, dict) and "config" in agent_full_config:
                        session_data["dynamic_agents_config"][agent_id] = agent_full_config["config"] # Save the inner 'config' dict
                        logger.debug(f"  Added config for dynamic agent '{agent_id}'. Keys: {list(agent_full_config['config'].keys())}")
                    else:
                        logger.warning(f"  Could not find valid 'config' dictionary for dynamic agent '{agent_id}'. Config not saved.")
                except Exception as e_cfg:
                    logger.warning(f"  Error accessing/processing config for dynamic agent '{agent_id}': {e_cfg}. Config not saved.", exc_info=True)
            else:
                 logger.debug(f"  Skipping config save for bootstrap agent '{agent_id}'.")

        logger.info(f"Data gathering complete. Found {len(all_agent_ids_found)} total agents.")
        logger.info(f"Found {len(dynamic_agent_ids_found)} dynamic agents to save config for: {dynamic_agent_ids_found}")
        logger.info(f"Saving {len(session_data['dynamic_agents_config'])} dynamic configs and {len(session_data['agent_histories'])} histories.")

        try:
            def save_sync():
                session_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(session_file_path, 'w', encoding='utf-8') as f:
                     json.dump(session_data, f, indent=2)
            await asyncio.to_thread(save_sync)
            logger.info(f"Session saved successfully: {session_file_path}")
            self._manager.current_project, self._manager.current_session = project_name, session_name
            await self._manager.send_to_ui({"type": "system_event", "event": "session_saved", "project": project_name, "session": session_name})
            return True, f"Session '{session_name}' saved successfully in project '{project_name}'."
        except Exception as e:
            logger.error(f"Error saving session file to {session_file_path}: {e}", exc_info=True)
            return False, f"Error saving session file: {e}"


    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        """Loads dynamic agents, teams, and histories from a saved session file."""
        # BOOTSTRAP_AGENT_ID is now imported at the module level

        session_file_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Attempting to load session from: {session_file_path}")
        if not session_file_path.is_file():
            logger.error(f"Session file not found: {session_file_path}")
            return False, f"Session file '{session_name}' not found in project '{project_name}'."

        try:
            def load_sync():
                 with open(session_file_path, 'r', encoding='utf-8') as f:
                     return json.load(f)
            session_data = await asyncio.to_thread(load_sync)
            logger.info(f"Successfully loaded JSON data from {session_file_path}")

            loaded_teams_data = session_data.get("teams", {})
            loaded_agent_to_team_data = session_data.get("agent_to_team", {})
            loaded_dynamic_configs = session_data.get("dynamic_agents_config", {})
            loaded_histories = session_data.get("agent_histories", {})
            loaded_states = session_data.get("agent_states", {}) # NEW: Load states
            logger.info(f"Loaded 'teams' keys: {list(loaded_teams_data.keys())}")
            logger.info(f"Loaded 'agent_to_team' keys: {list(loaded_agent_to_team_data.keys())}")
            logger.info(f"Loaded 'dynamic_agents_config' keys: {list(loaded_dynamic_configs.keys())}")
            logger.info(f"Loaded 'agent_histories' keys: {list(loaded_histories.keys())}")

            # Clear current dynamic state
            current_agent_ids = list(self._manager.agents.keys())
            dynamic_agents_to_delete = [aid for aid in current_agent_ids if aid not in self._manager.bootstrap_agents]
            logger.info(f"Clearing current dynamic state. Agents managed: {current_agent_ids}. Dynamic to delete: {dynamic_agents_to_delete}")
            logger.debug(f"LOAD_DEBUG (Before Dynamic Deletion): Agents keys = {list(self._manager.agents.keys())}")

            # Use agent_lifecycle handler for deletion
            delete_tasks = [agent_lifecycle.delete_agent_instance(self._manager, aid) for aid in dynamic_agents_to_delete]
            delete_results = await asyncio.gather(*delete_tasks, return_exceptions=True)
            successful_deletions = 0
            for i, res in enumerate(delete_results):
                 if isinstance(res, tuple) and res[0]: successful_deletions += 1
                 elif isinstance(res, Exception): logger.error(f"Error deleting agent {dynamic_agents_to_delete[i]} during load: {res}")
                 else: logger.error(f"Failed to delete agent {dynamic_agents_to_delete[i]} during load: {res[1] if isinstance(res, tuple) else 'Unknown error'}")
            logger.info(f"Successfully deleted {successful_deletions}/{len(dynamic_agents_to_delete)} dynamic agents.")
            logger.debug(f"LOAD_DEBUG (After Dynamic Deletion): Agents keys = {list(self._manager.agents.keys())}")

            self._state_manager.clear_state()
            bootstrap_agents_present = []
            for boot_id in self._manager.bootstrap_agents:
                if boot_id in self._manager.agents:
                     # Only clear history if this is during actual session loading, not during normal operation
                     # Check if the Admin AI has meaningful history that should be preserved
                     agent = self._manager.agents[boot_id]
                     if boot_id == BOOTSTRAP_AGENT_ID and len(agent.message_history) > 1:
                         # Admin AI has meaningful history - preserve it during session operations
                         logger.info(f"Preserving Admin AI history during session operation (length: {len(agent.message_history)})")
                     else:
                         # Safe to clear for other bootstrap agents or Admin AI with minimal history
                         agent.clear_history()
                         logger.info(f"Cleared history for bootstrap agent '{boot_id}' during session load")
                     bootstrap_agents_present.append(boot_id)
                else: logger.error(f"Bootstrap agent '{boot_id}' not found in manager's registry during history reset!")
            logger.info(f"Processed team state and bootstrap agent histories: {bootstrap_agents_present}")
            logger.debug(f"LOAD_DEBUG (After History Reset): Agents keys = {list(self._manager.agents.keys())}")

            # Load teams and mappings
            self._state_manager.load_state(loaded_teams_data, loaded_agent_to_team_data)

            # Recreate dynamic agents using agent_lifecycle handler
            logger.info(f"Recreating {len(loaded_dynamic_configs)} dynamic agents from session file...")
            creation_tasks = []
            attempted_agent_ids = list(loaded_dynamic_configs.keys())
            for agent_id in attempted_agent_ids:
                agent_cfg = loaded_dynamic_configs.get(agent_id)
                team_id = self._state_manager.get_agent_team(agent_id)
                if not isinstance(agent_cfg, dict):
                     logger.error(f"Skipping recreation of agent '{agent_id}': Invalid config format. Found: {type(agent_cfg)}")
                     continue
                # Use agent_lifecycle._create_agent_internal
                creation_tasks.append(agent_lifecycle._create_agent_internal(
                    self._manager, agent_id_requested=agent_id, agent_config_data=agent_cfg,
                    is_bootstrap=False, team_id=team_id, loading_from_session=True
                ))

            creation_results = await asyncio.gather(*creation_tasks, return_exceptions=True)
            successful_creations = 0; failed_creations = []; recreated_agent_ids = []
            for i, result in enumerate(creation_results):
                 agent_id_attempted = attempted_agent_ids[i] if i < len(attempted_agent_ids) else f"unknown_index_{i}"
                 if isinstance(result, Exception): logger.error(f"Failed recreating agent '{agent_id_attempted}': {result}", exc_info=result); failed_creations.append(f"{agent_id_attempted} (Error: {result})")
                 elif isinstance(result, tuple) and result[0]:
                     created_agent_id = result[2]
                     if created_agent_id: successful_creations += 1; recreated_agent_ids.append(created_agent_id)
                     else: logger.error(f"Recreating agent '{agent_id_attempted}' reported success but no ID."); failed_creations.append(f"{agent_id_attempted} (Success, but ID missing)")
                 else: error_msg = result[1] if isinstance(result, tuple) else 'Unknown'; logger.error(f"Failed recreating agent '{agent_id_attempted}': {error_msg}"); failed_creations.append(f"{agent_id_attempted} (Failed: {error_msg})")
            logger.info(f"Successfully recreated {successful_creations}/{len(loaded_dynamic_configs)} dynamic agents. Recreated IDs: {recreated_agent_ids}")
            if failed_creations: logger.warning(f"Failed to recreate agents: {', '.join(failed_creations)}")
            logger.debug(f"LOAD_DEBUG (After Dynamic Recreation): Agents keys = {list(self._manager.agents.keys())}")

            # Restore histories
            loaded_history_count = 0; loaded_state_count = 0
            agents_with_loaded_history = []; agents_with_loaded_state = []
            all_recreated_or_bootstrap_ids = list(self._manager.agents.keys()) # Get all agents present after recreation

            for agent_id in all_recreated_or_bootstrap_ids:
                agent = self._manager.agents.get(agent_id)
                if not agent: continue # Should not happen, but safety check

                # Restore History
                history = loaded_histories.get(agent_id)
                if history:
                    if isinstance(history, list) and all(isinstance(msg, dict) and 'role' in msg and 'content' in msg for msg in history):
                        agent.message_history = history
                        loaded_history_count += 1
                        agents_with_loaded_history.append(agent_id)
                    else:
                        logger.warning(f"Invalid history format for agent '{agent_id}'. History not loaded.")
                else:
                     # If history wasn't saved (e.g., bootstrap), only clear if it's not the Admin AI with meaningful history
                     if agent_id == BOOTSTRAP_AGENT_ID and len(agent.message_history) > 1:
                         logger.debug(f"Preserving Admin AI history during session load - no saved history but has {len(agent.message_history)} existing messages")
                     else:
                         agent.clear_history()
                         logger.debug(f"No saved history found for agent '{agent_id}', history reset.")

                # Restore State (NEW)
                state = loaded_states.get(agent_id)
                if hasattr(agent, 'set_state') and state is not None:
                    agent.set_state(state) # Use the new method
                    loaded_state_count += 1
                    agents_with_loaded_state.append(f"{agent_id}({state})")
                elif agent_id == BOOTSTRAP_AGENT_ID and hasattr(agent, 'set_state'):
                     # Ensure Admin AI defaults to conversation if no state was saved
                     from src.agents.constants import ADMIN_STATE_CONVERSATION
                     agent.set_state(ADMIN_STATE_CONVERSATION)
                     logger.debug(f"No saved state for Admin AI '{agent_id}', defaulted to {ADMIN_STATE_CONVERSATION}.")


                # Set final status to IDLE after loading everything
                agent.set_status(AGENT_STATUS_IDLE)

            logger.info(f"Loaded histories for {loaded_history_count} agents: {agents_with_loaded_history}")
            logger.info(f"Loaded workflow states for {loaded_state_count} agents: {agents_with_loaded_state}")
            logger.debug(f"LOAD_DEBUG (After History/State Loading): Agents keys = {list(self._manager.agents.keys())}")

            # Update manager's state
            self._manager.current_project, self._manager.current_session = project_name, session_name

            # Send full UI update
            all_current_agent_ids = list(self._manager.agents.keys())
            logger.info(f"Pushing final status updates to UI for agents: {all_current_agent_ids}")
            logger.debug(f"LOAD_DEBUG (Before Final Status Push): Agents keys = {list(self._manager.agents.keys())}")
            status_update_tasks = [self._manager.push_agent_status_update(aid) for aid in all_current_agent_ids]
            await asyncio.gather(*status_update_tasks)

            load_message = f"Session '{session_name}' loaded. {successful_creations} dynamic agents recreated."
            if failed_creations: load_message += f" Failed to recreate {len(failed_creations)} agents."
            if BOOTSTRAP_AGENT_ID not in self._manager.agents: load_message += " CRITICAL WARNING: Admin AI instance seems missing!"
            await self._manager.send_to_ui({"type": "system_event", "event": "session_loaded", "project": project_name, "session": session_name, "message": load_message})
            logger.debug(f"LOAD_DEBUG (End of Load Function): Agents keys = {list(self._manager.agents.keys())}")
            logger.info(f"Session load process complete for '{project_name}/{session_name}'.")
            return True, load_message

        except json.JSONDecodeError as e: logger.error(f"JSON decode error loading session file {session_file_path}: {e}", exc_info=True); return False, "Invalid session file format."
        except Exception as e: logger.error(f"Unexpected error loading session from {session_file_path}: {e}", exc_info=True); return False, f"Unexpected error loading session: {e}"
