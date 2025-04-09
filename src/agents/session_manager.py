# START OF FILE src/agents/session_manager.py
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Tuple, Optional, Dict, List, Any # Added Dict, List, Any

# Import settings for project base directory
from src.config.settings import settings
from src.agents.core import AGENT_STATUS_IDLE # Import status constant

# Type hinting for AgentManager and StateManager
if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.state_manager import AgentStateManager
    # If needed: from src.agents.core import MessageDict

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

    # --- *** Updated save_session with enhanced logging *** ---
    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        """Saves the current state including dynamic agent configs and histories."""
        if not project_name:
            logger.error("Save session failed: Project name cannot be empty.")
            return False, "Project name cannot be empty."
        if not session_name:
            # Generate timestamp-based name if none provided
            session_name = f"session_{int(time.time())}"
            logger.info(f"No session name provided, using generated name: {session_name}")

        session_file_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Preparing to save session state to: {session_file_path}")

        # Get current state from managers
        current_teams = self._state_manager.teams
        current_agent_to_team = self._state_manager.agent_to_team

        # Prepare data structure to save
        session_data = {
            "project": project_name,
            "session": session_name,
            "timestamp": time.time(),
            "teams": current_teams,
            "agent_to_team": current_agent_to_team,
            "dynamic_agents_config": {},
            "agent_histories": {}
        }

        # --- Enhanced Logging: Log what's being gathered ---
        logger.info(f"Gathering data for save. Current Teams: {list(current_teams.keys())}. Agent Mappings: {len(current_agent_to_team)}")
        dynamic_agent_ids_found = []
        all_agent_ids_found = list(self._manager.agents.keys()) # Get all agent IDs currently in manager

        for agent_id, agent in self._manager.agents.items():
            # --- Log history saving attempt ---
            try:
                # Quick check for serializability before adding
                json.dumps(agent.message_history)
                session_data["agent_histories"][agent_id] = agent.message_history
                logger.debug(f"  Added history for agent '{agent_id}' (Length: {len(agent.message_history)})")
            except TypeError as e:
                logger.error(f"History for agent '{agent_id}' is not JSON serializable: {e}. Saving placeholder.")
                session_data["agent_histories"][agent_id] = [{"role": "system", "content": f"[History Serialization Error: {e}]"}]

            # --- Save config ONLY for dynamic agents ---
            if agent_id not in self._manager.bootstrap_agents:
                dynamic_agent_ids_found.append(agent_id) # Track dynamic agents found
                try:
                    # Get config stored on the agent instance (should contain the 'config' key)
                    agent_full_config = getattr(agent, 'agent_config', None) # Use getattr for safety
                    config_to_save = agent_full_config.get("config") if isinstance(agent_full_config, dict) else None

                    if config_to_save and isinstance(config_to_save, dict):
                        session_data["dynamic_agents_config"][agent_id] = config_to_save
                        logger.debug(f"  Added config for dynamic agent '{agent_id}'. Keys: {list(config_to_save.keys())}")
                    else:
                        logger.warning(f"  Could not find valid 'config' dictionary within agent_config attribute for dynamic agent '{agent_id}'. Config not saved.")
                except Exception as e_cfg:
                    logger.warning(f"  Error accessing/processing config for dynamic agent '{agent_id}': {e_cfg}. Config not saved.", exc_info=True)
            else:
                 logger.debug(f"  Skipping config save for bootstrap agent '{agent_id}'.")

        logger.info(f"Data gathering complete. Found {len(all_agent_ids_found)} total agents.")
        logger.info(f"Found {len(dynamic_agent_ids_found)} dynamic agents to save config for: {dynamic_agent_ids_found}")
        logger.info(f"Saving {len(session_data['dynamic_agents_config'])} dynamic configs and {len(session_data['agent_histories'])} histories.")
        # Optionally log the entire structure before saving (can be very verbose)
        # try:
        #     logger.debug(f"Full session_data before save:\n{json.dumps(session_data, indent=2)}")
        # except Exception as json_e:
        #     logger.error(f"Could not serialize full session_data for debug log: {json_e}")
        # --- End Enhanced Logging ---

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
    # --- *** End updated save_session *** ---


    # --- *** Updated load_session with enhanced logging and corrected structure *** ---
    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        """Loads dynamic agents, teams, and histories from a saved session file."""
        session_file_path = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_session_data.json"
        logger.info(f"Attempting to load session from: {session_file_path}")
        if not session_file_path.is_file():
            logger.error(f"Session file not found: {session_file_path}")
            return False, f"Session file '{session_name}' not found in project '{project_name}'."

        try:
            # Load session data from file
            def load_sync():
                 with open(session_file_path, 'r', encoding='utf-8') as f:
                     return json.load(f)
            session_data = await asyncio.to_thread(load_sync)
            logger.info(f"Successfully loaded JSON data from {session_file_path}")

            # --- Enhanced Logging: Log loaded content ---
            loaded_teams_data = session_data.get("teams", {})
            loaded_agent_to_team_data = session_data.get("agent_to_team", {})
            loaded_dynamic_configs = session_data.get("dynamic_agents_config", {})
            loaded_histories = session_data.get("agent_histories", {})
            logger.info(f"Loaded 'teams' keys: {list(loaded_teams_data.keys())}")
            logger.info(f"Loaded 'agent_to_team' keys: {list(loaded_agent_to_team_data.keys())}")
            logger.info(f"Loaded 'dynamic_agents_config' keys: {list(loaded_dynamic_configs.keys())}")
            logger.info(f"Loaded 'agent_histories' keys: {list(loaded_histories.keys())}")
            # --- End Enhanced Logging ---

            # --- Clear current dynamic state before loading ---
            current_agent_ids = list(self._manager.agents.keys())
            dynamic_agents_to_delete = [aid for aid in current_agent_ids if aid not in self._manager.bootstrap_agents]
            logger.info(f"Clearing current dynamic state. Agents managed: {current_agent_ids}. Dynamic to delete: {dynamic_agents_to_delete}")

            delete_tasks = [self._manager.delete_agent_instance(aid) for aid in dynamic_agents_to_delete]
            delete_results = await asyncio.gather(*delete_tasks, return_exceptions=True)
            successful_deletions = 0
            for i, res in enumerate(delete_results):
                 if isinstance(res, tuple) and res[0]:
                     successful_deletions += 1
                 elif isinstance(res, Exception):
                     logger.error(f"Error deleting agent {dynamic_agents_to_delete[i]} during load: {res}")
                 else:
                      error_msg = res[1] if isinstance(res, tuple) else "Unknown deletion error"
                      logger.error(f"Failed to delete agent {dynamic_agents_to_delete[i]} during load: {error_msg}")
            logger.info(f"Successfully deleted {successful_deletions}/{len(dynamic_agents_to_delete)} dynamic agents.")

            self._state_manager.clear_state()
            bootstrap_agents_present = []
            for boot_id in self._manager.bootstrap_agents:
                if boot_id in self._manager.agents:
                     self._manager.agents[boot_id].clear_history()
                     bootstrap_agents_present.append(boot_id)
                else:
                     logger.error(f"Bootstrap agent '{boot_id}' not found in manager's registry during history reset phase of load!")
            logger.info(f"Cleared team state and reset histories for present bootstrap agents: {bootstrap_agents_present}")
            # --- End Clearing ---

            # Load teams and mappings into StateManager using the data loaded earlier
            self._state_manager.load_state(loaded_teams_data, loaded_agent_to_team_data)

            # Recreate dynamic agents using AgentManager's internal method
            logger.info(f"Recreating {len(loaded_dynamic_configs)} dynamic agents from session file...")
            creation_tasks = []
            attempted_agent_ids = list(loaded_dynamic_configs.keys()) # Store IDs for logging
            for agent_id in attempted_agent_ids:
                agent_cfg = loaded_dynamic_configs.get(agent_id) # Get config safely
                team_id = self._state_manager.get_agent_team(agent_id)
                if not isinstance(agent_cfg, dict):
                     logger.error(f"Skipping recreation of agent '{agent_id}': Invalid config format found (not a dictionary). Found: {type(agent_cfg)}")
                     continue
                creation_tasks.append(self._manager._create_agent_internal(
                    agent_id_requested=agent_id,
                    agent_config_data=agent_cfg,
                    is_bootstrap=False,
                    team_id=team_id,
                    loading_from_session=True
                    ))

            creation_results = await asyncio.gather(*creation_tasks, return_exceptions=True)
            successful_creations = 0; failed_creations = []; recreated_agent_ids = []
            for i, result in enumerate(creation_results):
                 agent_id_attempted = attempted_agent_ids[i] if i < len(attempted_agent_ids) else f"unknown_index_{i}"
                 if isinstance(result, Exception):
                     logger.error(f"Failed recreating agent '{agent_id_attempted}' from session: {result}", exc_info=result)
                     failed_creations.append(f"{agent_id_attempted} (Error: {result})")
                 elif isinstance(result, tuple) and result[0]:
                     created_agent_id = result[2]
                     if created_agent_id:
                         successful_creations += 1
                         recreated_agent_ids.append(created_agent_id)
                     else:
                         logger.error(f"Recreating agent '{agent_id_attempted}' reported success but no agent ID returned.")
                         failed_creations.append(f"{agent_id_attempted} (Success reported, but ID missing)")
                 else:
                     error_msg = result[1] if isinstance(result, tuple) else 'Unknown creation error'
                     logger.error(f"Failed recreating agent '{agent_id_attempted}' from session: {error_msg}")
                     failed_creations.append(f"{agent_id_attempted} (Failed: {error_msg})")

            logger.info(f"Successfully recreated {successful_creations}/{len(loaded_dynamic_configs)} dynamic agents. Recreated IDs: {recreated_agent_ids}")
            if failed_creations:
                logger.warning(f"Failed to recreate the following agents: {', '.join(failed_creations)}")

            # Restore histories for ALL agents now present
            loaded_history_count = 0
            agents_with_loaded_history = []
            for agent_id, history in loaded_histories.items():
                agent = self._manager.agents.get(agent_id)
                if agent:
                     if isinstance(history, list) and all(isinstance(msg, dict) and 'role' in msg and 'content' in msg for msg in history):
                         agent.message_history = history
                         agent.set_status(AGENT_STATUS_IDLE)
                         loaded_history_count += 1
                         agents_with_loaded_history.append(agent_id)
                     else:
                         logger.warning(f"Invalid or missing history format for agent '{agent_id}' in session file. History not loaded.")
                else:
                     if agent_id not in self._manager.bootstrap_agents and agent_id in attempted_agent_ids:
                          logger.warning(f"History found for dynamic agent '{agent_id}' but agent instance does not exist (failed recreation?). History not loaded.")
                     elif agent_id in self._manager.bootstrap_agents:
                          logger.error(f"CRITICAL: History found for bootstrap agent '{agent_id}' but agent instance is MISSING from manager after load! History not loaded.")
                     else:
                          logger.warning(f"History found for unknown agent '{agent_id}' in session file, but agent instance does not exist. History not loaded.")

            logger.info(f"Loaded histories for {loaded_history_count} agents: {agents_with_loaded_history}")

            # Update manager's tracked state
            self._manager.current_project, self._manager.current_session = project_name, session_name

            # Send full state update to UI
            all_current_agent_ids = list(self._manager.agents.keys())
            logger.info(f"Pushing final status updates to UI for agents: {all_current_agent_ids}")
            status_update_tasks = [self._manager.push_agent_status_update(aid) for aid in all_current_agent_ids]
            await asyncio.gather(*status_update_tasks)

            # Construct final status message
            load_message = f"Session '{session_name}' loaded. {successful_creations} dynamic agents recreated."
            if failed_creations: load_message += f" Failed to recreate {len(failed_creations)} agents."
            # Use BOOTSTRAP_AGENT_ID constant
            from src.agents.manager import BOOTSTRAP_AGENT_ID # Import locally if not already available
            if BOOTSTRAP_AGENT_ID not in self._manager.agents:
                 load_message += " CRITICAL WARNING: Admin AI instance seems to be missing after load!"
                 logger.critical("Admin AI instance missing after session load completed!")

            # Send final system event to UI
            await self._manager.send_to_ui({
                 "type": "system_event",
                 "event": "session_loaded",
                 "project": project_name,
                 "session": session_name,
                 "message": load_message
             })
            logger.info(f"Session load process complete for '{project_name}/{session_name}'.")
            return True, load_message

        # --- Correctly indented except blocks ---
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error loading session file {session_file_path}: {e}", exc_info=True)
            return False, "Invalid session file format (JSON decode error)."
        except Exception as e:
            logger.error(f"Unexpected error loading session from {session_file_path}: {e}", exc_info=True)
            return False, f"Unexpected error loading session: {e}"
    # --- *** End updated load_session *** ---
