# START OF FILE src/config/config_manager.py
import yaml
import os
import shutil
import asyncio # Import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging
import tempfile # Use tempfile for safer writing
import copy # Use copy for deepcopy

# Define paths here to potentially resolve import order issues
BASE_DIR_CM = Path(__file__).resolve().parent.parent.parent
AGENT_CONFIG_PATH_CM = BASE_DIR_CM / 'config.yaml'


logger = logging.getLogger(__name__)

class ConfigManager:
    """
    Manages reading and writing of the agent configuration file (config.yaml),
    including both 'agents' and 'teams' structures.
    Provides async-safe methods for CRUD operations on agent configurations
    using asyncio.Lock and atomic file writes.
    Includes a basic backup mechanism before writing.
    """

    def __init__(self, config_path: Path):
        """
        Initializes the ConfigManager.

        Args:
            config_path (Path): The path to the config.yaml file.
        """
        self.config_path = config_path
        # Store the entire loaded config data now
        self._config_data: Dict[str, Any] = {"agents": [], "teams": {}}
        self._lock = asyncio.Lock() # Use asyncio.Lock for async safety
        # Initial load is done synchronously here for simplicity during startup.
        self._load_config_sync()

    def _load_config_sync(self):
        """Synchronous initial load for use during startup."""
        logger.info(f"[Sync Load] Attempting to load full configuration from: {self.config_path}")
        default_structure = {"agents": [], "teams": {}}
        if not self.config_path.exists():
            logger.warning(f"[Sync Load] Configuration file not found at {self.config_path}. Initializing empty structure.")
            self._config_data = default_structure
            return

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                loaded_data = yaml.safe_load(f)
                # Validate basic structure
                if isinstance(loaded_data, dict):
                     self._config_data['agents'] = loaded_data.get('agents', [])
                     self._config_data['teams'] = loaded_data.get('teams', {})
                     if not isinstance(self._config_data['agents'], list):
                         logger.warning(f"[Sync Load] 'agents' key in {self.config_path} is not a list. Resetting to empty list.")
                         self._config_data['agents'] = []
                     if not isinstance(self._config_data['teams'], dict):
                         logger.warning(f"[Sync Load] 'teams' key in {self.config_path} is not a dictionary. Resetting to empty dict.")
                         self._config_data['teams'] = {}
                     logger.info(f"[Sync Load] Successfully loaded configuration: {len(self._config_data['agents'])} agents, {len(self._config_data['teams'])} teams.")
                else:
                    logger.warning(f"[Sync Load] Configuration file {self.config_path} does not contain a valid dictionary structure. Initializing empty.")
                    self._config_data = default_structure

        except yaml.YAMLError as e:
            logger.error(f"[Sync Load] Error parsing YAML file {self.config_path}: {e}", exc_info=True)
            self._config_data = default_structure # Reset internal state on error
        except Exception as e:
            logger.error(f"[Sync Load] Error reading configuration file {self.config_path}: {e}", exc_info=True)
            self._config_data = default_structure # Reset internal state on error

    async def load_config(self) -> Dict[str, Any]:
        """
        Asynchronously reads the full YAML configuration file ('agents' and 'teams').
        Handles file not found and parsing errors. Async-safe via asyncio.Lock.
        Returns the full configuration dictionary.
        """
        async with self._lock:
            logger.info(f"Attempting to load full configuration from: {self.config_path}")
            default_structure = {"agents": [], "teams": {}}
            if not self.config_path.exists():
                logger.warning(f"Configuration file not found at {self.config_path}. Returning empty structure.")
                self._config_data = default_structure
                return copy.deepcopy(self._config_data)

            try:
                # Use asyncio.to_thread for file I/O to avoid blocking the event loop
                def read_file_sync():
                     with open(self.config_path, 'r', encoding='utf-8') as f:
                          return yaml.safe_load(f)

                loaded_data = await asyncio.to_thread(read_file_sync)

                # Validate basic structure
                if isinstance(loaded_data, dict):
                     self._config_data['agents'] = loaded_data.get('agents', [])
                     self._config_data['teams'] = loaded_data.get('teams', {})
                     if not isinstance(self._config_data['agents'], list):
                         logger.warning(f"'agents' key in {self.config_path} is not a list. Resetting to empty list.")
                         self._config_data['agents'] = []
                     if not isinstance(self._config_data['teams'], dict):
                         logger.warning(f"'teams' key in {self.config_path} is not a dictionary. Resetting to empty dict.")
                         self._config_data['teams'] = {}
                     logger.info(f"Successfully loaded configuration: {len(self._config_data['agents'])} agents, {len(self._config_data['teams'])} teams.")
                else:
                    logger.warning(f"Configuration file {self.config_path} does not contain a valid dictionary structure. Initializing empty.")
                    self._config_data = default_structure

            except yaml.YAMLError as e:
                logger.error(f"Error parsing YAML file {self.config_path}: {e}", exc_info=True)
                self._config_data = default_structure # Reset internal state on error
            except Exception as e:
                logger.error(f"Error reading configuration file {self.config_path}: {e}", exc_info=True)
                self._config_data = default_structure # Reset internal state on error

            # Return deep copy of the potentially updated internal state
            return copy.deepcopy(self._config_data)

    async def _backup_config(self) -> bool:
        """Creates a backup of the current config file. Assumes lock is held."""
        if not self.config_path.exists():
            return True # No file to backup
        backup_path = self.config_path.with_suffix(".yaml.bak")
        try:
             # Use asyncio.to_thread for file I/O
             await asyncio.to_thread(shutil.copy2, self.config_path, backup_path) # copy2 preserves metadata
             logger.info(f"Created backup of config file at: {backup_path}")
             return True
        except Exception as e:
             logger.error(f"Failed to create backup of config file: {e}", exc_info=True)
             return False

    async def _save_config_safe(self) -> bool:
        """
        Writes the current internal full configuration data (_config_data)
        back to the YAML file atomically.
        Assumes the lock is already held by the caller. Uses a temporary file.

        Returns:
            bool: True if saving was successful, False otherwise.
        """
        agents_count = len(self._config_data.get('agents', []))
        teams_count = len(self._config_data.get('teams', {}))
        logger.info(f"Attempting to save {agents_count} agents and {teams_count} teams to: {self.config_path}")

        # 1. Create backup before writing temporary file
        if not await self._backup_config():
             logger.error("Aborting save due to backup failure.")
             return False

        # 2. Write to a temporary file in the same directory
        temp_file_path = None
        try:
            temp_fd, temp_path_str = tempfile.mkstemp(suffix=".tmp", prefix=self.config_path.name + '_', dir=self.config_path.parent)
            temp_file_path = Path(temp_path_str)

            # Save the entire internal structure
            config_to_save = self._config_data

            # Use asyncio.to_thread for the blocking file write operation
            def write_yaml_sync():
                 with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                     # Ensure correct structure with 'agents' and 'teams' keys
                     yaml.dump(config_to_save, f, default_flow_style=False, sort_keys=False, indent=2)

            await asyncio.to_thread(write_yaml_sync)
            logger.debug(f"Successfully wrote configuration to temporary file: {temp_file_path}")

            # 3. Atomically replace the original file with the temporary file
            await asyncio.to_thread(os.replace, temp_file_path, self.config_path)
            logger.info(f"Successfully saved configuration to {self.config_path} (atomic replace)")
            temp_file_path = None # Avoid deletion in finally block if successful

            return True

        except Exception as e:
            logger.error(f"Error writing configuration file {self.config_path}: {e}", exc_info=True)
            logger.error("Configuration save failed. Original file might be intact or restored from backup if possible manually.")
            return False
        finally:
            # Ensure temporary file is removed if an error occurred after its creation
            if temp_file_path and temp_file_path.exists():
                 try:
                      await asyncio.to_thread(os.remove, temp_file_path)
                      logger.debug(f"Removed temporary config file: {temp_file_path}")
                 except Exception as rm_err:
                      logger.error(f"Error removing temporary config file {temp_file_path}: {rm_err}")

    async def get_config(self) -> List[Dict[str, Any]]:
        """
        Returns a deep copy of the currently loaded agent configuration list ('agents').
        Maintains backward compatibility for API routes. Async-safe.
        """
        async with self._lock:
            # Return a deep copy of just the agents list
            agents_list = self._config_data.get("agents", [])
            return copy.deepcopy(agents_list)

    async def get_teams(self) -> Dict[str, List[str]]:
        """Returns a deep copy of the currently loaded teams configuration. Async-safe."""
        async with self._lock:
             teams_dict = self._config_data.get("teams", {})
             return copy.deepcopy(teams_dict)

    async def get_full_config(self) -> Dict[str, Any]:
        """Returns a deep copy of the entire loaded configuration data. Async-safe."""
        async with self._lock:
             return copy.deepcopy(self._config_data)

    def get_config_data_sync(self) -> Dict[str, Any]:
        """
        Synchronously returns a deep copy of the full configuration data loaded during initialization.
        Intended for use during synchronous startup phases (like Settings init).
        Does NOT acquire the async lock. Relies on initial load being complete.
        """
        # Return a deep copy to prevent external modification of the internal structure
        return copy.deepcopy(self._config_data)

    def _find_agent_index_unsafe(self, agent_id: str) -> Optional[int]:
        """Internal helper: Finds the index of an agent by ID within the 'agents' list. Assumes lock is held."""
        agents_list = self._config_data.get("agents", [])
        for index, agent_data in enumerate(agents_list):
            if agent_data.get("agent_id") == agent_id:
                return index
        return None

    # --- Agent CRUD Methods (operate on self._config_data['agents']) ---

    async def add_agent(self, agent_config_entry: Dict[str, Any]) -> bool:
        """
        Adds a new agent configuration entry to the 'agents' list and triggers safe save
        of the entire configuration. Validates ID uniqueness. Async-safe.
        """
        async with self._lock:
            agent_id = agent_config_entry.get("agent_id")
            if not agent_id:
                logger.error("Cannot add agent: 'agent_id' is missing.")
                return False
            if not isinstance(agent_config_entry.get("config"), dict):
                logger.error(f"Cannot add agent '{agent_id}': 'config' field is missing or not a dictionary.")
                return False

            if self._find_agent_index_unsafe(agent_id) is not None:
                logger.error(f"Cannot add agent: Agent with ID '{agent_id}' already exists.")
                return False

            # Ensure 'agents' list exists
            if "agents" not in self._config_data or not isinstance(self._config_data["agents"], list):
                 self._config_data["agents"] = []

            # Append a deep copy to the internal 'agents' list
            self._config_data["agents"].append(copy.deepcopy(agent_config_entry))
            logger.info(f"Agent '{agent_id}' added internally.")

            # Save the updated *full* configuration safely
            if await self._save_config_safe():
                logger.info(f"Successfully added agent '{agent_id}' and saved configuration.")
                return True
            else:
                # Rollback the addition if save failed
                self._config_data["agents"].pop() # Remove the just added item
                logger.error(f"Failed to save configuration after adding agent '{agent_id}'. Addition rolled back.")
                return False

    async def update_agent(self, agent_id: str, updated_config_data: Dict[str, Any]) -> bool:
        """
        Updates the 'config' part of an existing agent entry in the 'agents' list.
        Triggers safe save of the entire configuration. Async-safe.
        """
        async with self._lock:
            index = self._find_agent_index_unsafe(agent_id)
            if index is None:
                logger.error(f"Cannot update agent: Agent with ID '{agent_id}' not found.")
                return False

             # Ensure 'agents' list exists and index is valid
            if "agents" not in self._config_data or not isinstance(self._config_data.get("agents"), list) or index >= len(self._config_data["agents"]):
                 logger.error(f"Internal state error: Could not find agent at index {index} for update.")
                 return False

            # Keep a deep copy of the original entry in case save fails
            original_config_entry = copy.deepcopy(self._config_data["agents"][index])

            # Update the 'config' key within the 'agents' list
            self._config_data["agents"][index]["config"] = copy.deepcopy(updated_config_data)
            logger.info(f"Agent '{agent_id}' config updated internally.")

            # Save the updated *full* configuration safely
            if await self._save_config_safe():
                logger.info(f"Successfully updated agent '{agent_id}' and saved configuration.")
                return True
            else:
                # Rollback the update if save failed
                self._config_data["agents"][index] = original_config_entry # Restore original entry
                logger.error(f"Failed to save configuration after updating agent '{agent_id}'. Update rolled back.")
                return False

    async def delete_agent(self, agent_id: str) -> bool:
        """
        Removes an agent configuration entry from the 'agents' list by ID.
        Triggers safe save of the entire configuration. Async-safe.
        """
        async with self._lock:
            index = self._find_agent_index_unsafe(agent_id)
            if index is None:
                logger.error(f"Cannot delete agent: Agent with ID '{agent_id}' not found.")
                return False

            # Ensure 'agents' list exists and index is valid
            if "agents" not in self._config_data or not isinstance(self._config_data.get("agents"), list) or index >= len(self._config_data["agents"]):
                 logger.error(f"Internal state error: Could not find agent at index {index} for deletion.")
                 return False

            # Keep a copy of the item being deleted in case save fails
            deleted_entry = self._config_data["agents"].pop(index)
            logger.info(f"Agent '{agent_id}' removed internally from 'agents' list.")

             # Also remove the agent from any teams they might be in (important!)
            teams_modified = False
            if "teams" in self._config_data and isinstance(self._config_data["teams"], dict):
                 for team_name, members in list(self._config_data["teams"].items()): # Iterate over copy of items
                     if isinstance(members, list) and agent_id in members:
                         logger.info(f"Removing deleted agent '{agent_id}' from team '{team_name}'.")
                         self._config_data["teams"][team_name].remove(agent_id)
                         teams_modified = True
                         # Optional: Remove team if it becomes empty?
                         # if not self._config_data["teams"][team_name]:
                         #     del self._config_data["teams"][team_name]
            else:
                 logger.warning("Could not find 'teams' structure to clean up deleted agent ID.")


            # Save the updated *full* configuration safely
            if await self._save_config_safe():
                logger.info(f"Successfully deleted agent '{agent_id}' (and removed from teams if applicable) and saved configuration.")
                return True
            else:
                # Rollback the deletion if save failed
                self._config_data["agents"].insert(index, deleted_entry) # Put agent back
                # Rollback team changes (more complex - might need to store original teams state)
                # For simplicity now, we only log the failure. A full rollback would require storing the original team state.
                if teams_modified:
                     logger.error(f"Failed to save configuration after deleting agent '{agent_id}'. Agent deletion rolled back, but TEAM CHANGES MAY NOT BE FULLY ROLLED BACK.")
                else:
                     logger.error(f"Failed to save configuration after deleting agent '{agent_id}'. Deletion rolled back.")
                return False


# --- Create a Singleton Instance ---
# This instance will be used by other modules (like settings and API routes)
config_manager = ConfigManager(AGENT_CONFIG_PATH_CM)
