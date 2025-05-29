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
    including 'agents'.
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
        # Initialize with expected keys, even if empty
        self._config_data: Dict[str, Any] = {
            "agents": []
        }
        self._lock = asyncio.Lock() # Use asyncio.Lock for async safety
        # Initial load is done synchronously here for simplicity during startup.
        self._load_config_sync()

    def _load_config_sync(self):
        """Synchronous initial load for use during startup. Loads the *entire* config."""
        logger.info(f"[Sync Load] Attempting to load full configuration from: {self.config_path}")
        # Default structure if file is missing or invalid
        default_structure = {"agents": []}

        if not self.config_path.exists():
            logger.warning(f"[Sync Load] Configuration file not found at {self.config_path}. Initializing empty structure.")
            self._config_data = copy.deepcopy(default_structure)
            return

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                loaded_data = yaml.safe_load(f)

                # *** CORRECTED LOADING LOGIC ***
                if isinstance(loaded_data, dict):
                    # Assign the whole loaded dictionary
                    self._config_data = loaded_data

                    # --- Validation and Defaulting ---
                    # Ensure mandatory keys exist, defaulting if necessary
                    if "agents" not in self._config_data or not isinstance(self._config_data.get("agents"), list):
                        logger.warning(f"[Sync Load] 'agents' key missing or invalid in {self.config_path}. Initializing empty list.")
                        self._config_data["agents"] = []
                    else:
                        # Validate individual agent entries (basic structure check)
                        valid_agents = []
                        for i, agent_entry in enumerate(self._config_data["agents"]):
                            if isinstance(agent_entry, dict) and "agent_id" in agent_entry and isinstance(agent_entry.get("config"), dict):
                                valid_agents.append(agent_entry)
                            else:
                                logger.warning(f"[Sync Load] Invalid agent entry at index {i} in {self.config_path}. Skipping.")
                        self._config_data["agents"] = valid_agents
                    # --- End Validation ---

                    logger.info(f"[Sync Load] Successfully loaded configuration: {len(self._config_data['agents'])} agents.")

                else:
                    logger.warning(f"[Sync Load] Configuration file {self.config_path} does not contain a valid dictionary structure. Initializing empty.")
                    self._config_data = copy.deepcopy(default_structure)

        except yaml.YAMLError as e:
            logger.error(f"[Sync Load] Error parsing YAML file {self.config_path}: {e}", exc_info=True)
            self._config_data = copy.deepcopy(default_structure) # Reset internal state on error
        except Exception as e:
            logger.error(f"[Sync Load] Error reading configuration file {self.config_path}: {e}", exc_info=True)
            self._config_data = copy.deepcopy(default_structure) # Reset internal state on error

    async def load_config(self) -> Dict[str, Any]:
        """
        Asynchronously reads the full YAML configuration file.
        Handles file not found and parsing errors. Async-safe via asyncio.Lock.
        Returns a deep copy of the full configuration dictionary.
        """
        async with self._lock:
            logger.info(f"Attempting to load full configuration from: {self.config_path}")
            # Don't use default_structure here, rely on the existing self._config_data as the fallback

            if not self.config_path.exists():
                logger.warning(f"Configuration file not found at {self.config_path}. Returning current internal state.")
                return copy.deepcopy(self._config_data)

            try:
                # Use asyncio.to_thread for file I/O
                def read_file_sync():
                     with open(self.config_path, 'r', encoding='utf-8') as f:
                          return yaml.safe_load(f)

                loaded_data = await asyncio.to_thread(read_file_sync)

                # *** CORRECTED ASYNC LOADING LOGIC ***
                if isinstance(loaded_data, dict):
                    # Store the successfully loaded data temporarily
                    temp_config_data = loaded_data

                    # --- Validation and Defaulting (similar to sync) ---
                    valid_structure = True
                    if "agents" not in temp_config_data or not isinstance(temp_config_data.get("agents"), list):
                        logger.warning(f"'agents' key missing or invalid in loaded data. Keeping previous state.")
                        temp_config_data["agents"] = self._config_data.get("agents", []) # Keep old if invalid
                        # valid_structure = False # Decide if partial load is okay or keep old state entirely
                    else: # Validate agent entries
                         valid_agents = []
                         for i, agent_entry in enumerate(temp_config_data["agents"]):
                             if isinstance(agent_entry, dict) and "agent_id" in agent_entry and isinstance(agent_entry.get("config"), dict):
                                 valid_agents.append(agent_entry)
                             else: logger.warning(f"Invalid agent entry at index {i} in loaded data. Skipping.")
                         temp_config_data["agents"] = valid_agents

                    # If structure seems valid enough, update the internal state
                    # if valid_structure: # Or decide to always update with validated data
                    self._config_data = temp_config_data
                    logger.info(f"Successfully loaded and validated configuration: {len(self._config_data['agents'])} agents.")

                else:
                    logger.warning(f"Configuration file {self.config_path} does not contain a valid dictionary structure. Using previous internal state.")
                    # Don't overwrite internal state if load fails validation

            except yaml.YAMLError as e:
                logger.error(f"Error parsing YAML file {self.config_path}: {e}. Using previous internal state.", exc_info=True)
                # Keep previous state on parse error
            except Exception as e:
                logger.error(f"Error reading configuration file {self.config_path}: {e}. Using previous internal state.", exc_info=True)
                # Keep previous state on read error

            # Return deep copy of the potentially updated internal state
            return copy.deepcopy(self._config_data)

    async def _backup_config(self) -> bool:
        """Creates a backup of the current config file. Assumes lock is held."""
        if not self.config_path.exists():
            return True # No file to backup
        backup_path = self.config_path.with_suffix(".yaml.bak")
        try:
             await asyncio.to_thread(shutil.copy2, self.config_path, backup_path)
             logger.info(f"Created backup of config file at: {backup_path}")
             return True
        except Exception as e:
             logger.error(f"Failed to create backup of config file: {e}", exc_info=True)
             return False

    async def _save_config_safe(self) -> bool:
        """
        Writes the current internal full configuration data (_config_data)
        back to the YAML file atomically. Assumes lock is held.
        """
        agents_count = len(self._config_data.get('agents', []))
        logger.info(f"Attempting to save config ({agents_count} agents) to: {self.config_path}")

        if not await self._backup_config():
             logger.error("Aborting save due to backup failure.")
             return False

        temp_file_path = None
        try:
            # Use a temporary file for atomic write
            # Ensure config_to_save has all necessary top-level keys before dumping
            config_to_save = {
                "agents": self._config_data.get("agents", [])
            }

            temp_fd, temp_path_str = tempfile.mkstemp(suffix=".tmp", prefix=self.config_path.name + '_', dir=self.config_path.parent)
            temp_file_path = Path(temp_path_str)

            def write_yaml_sync():
                 with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                     # Use sort_keys=False to maintain order as much as possible
                     yaml.dump(config_to_save, f, default_flow_style=False, sort_keys=False, indent=2)

            await asyncio.to_thread(write_yaml_sync)
            logger.debug(f"Successfully wrote configuration to temporary file: {temp_file_path}")

            # Atomically replace the original file
            await asyncio.to_thread(os.replace, temp_file_path, self.config_path)
            logger.info(f"Successfully saved configuration to {self.config_path}")
            temp_file_path = None # Avoid deletion in finally block
            return True

        except Exception as e:
            logger.error(f"Error writing configuration file {self.config_path}: {e}", exc_info=True)
            return False
        finally:
            # Cleanup temp file on error
            if temp_file_path and temp_file_path.exists():
                 try: await asyncio.to_thread(os.remove, temp_file_path); logger.debug(f"Removed temporary config file: {temp_file_path}")
                 except Exception as rm_err: logger.error(f"Error removing temporary config file {temp_file_path}: {rm_err}")

    # --- Getters for specific parts or full config ---

    async def get_config(self) -> List[Dict[str, Any]]:
        """Returns a deep copy of the currently loaded agent configuration list ('agents'). Async-safe."""
        async with self._lock:
            agents_list = self._config_data.get("agents", [])
            return copy.deepcopy(agents_list)

    async def get_full_config(self) -> Dict[str, Any]:
        """Returns a deep copy of the entire loaded configuration data. Async-safe."""
        async with self._lock:
             return copy.deepcopy(self._config_data)

    def get_config_data_sync(self) -> Dict[str, Any]:
        """Synchronously returns a deep copy of the full configuration data loaded during initialization."""
        # Return a deep copy to prevent external modification
        return copy.deepcopy(self._config_data)

    # --- Internal Helper ---

    def _find_agent_index_unsafe(self, agent_id: str) -> Optional[int]:
        """Internal helper: Finds the index of an agent by ID. Assumes lock is held."""
        agents_list = self._config_data.get("agents", [])
        for index, agent_data in enumerate(agents_list):
            if isinstance(agent_data, dict) and agent_data.get("agent_id") == agent_id: # Added safety check
                return index
        return None

    # --- Agent CRUD Methods (operate on self._config_data['agents']) ---

    async def add_agent(self, agent_config_entry: Dict[str, Any]) -> bool:
        """Adds agent, saves full config. Async-safe."""
        async with self._lock:
            agent_id = agent_config_entry.get("agent_id")
            if not agent_id: logger.error("Cannot add agent: 'agent_id' missing."); return False
            if not isinstance(agent_config_entry.get("config"), dict): logger.error(f"Cannot add agent '{agent_id}': 'config' missing/invalid."); return False
            if self._find_agent_index_unsafe(agent_id) is not None: logger.error(f"Cannot add agent: ID '{agent_id}' already exists."); return False

            # Ensure 'agents' list exists and is a list
            if not isinstance(self._config_data.get("agents"), list):
                 logger.warning("Internal 'agents' data is not a list. Reinitializing.")
                 self._config_data["agents"] = []

            self._config_data["agents"].append(copy.deepcopy(agent_config_entry))
            logger.info(f"Agent '{agent_id}' added internally.")

            if await self._save_config_safe():
                logger.info(f"Successfully added agent '{agent_id}' and saved configuration.")
                return True
            else:
                # Rollback addition if save failed
                # Find and remove the entry we just added
                new_index = self._find_agent_index_unsafe(agent_id)
                if new_index is not None:
                    self._config_data["agents"].pop(new_index)
                logger.error(f"Failed to save configuration after adding agent '{agent_id}'. Rolled back.")
                return False

    async def update_agent(self, agent_id: str, updated_config_data: Dict[str, Any]) -> bool:
        """Updates agent's 'config', saves full config. Async-safe."""
        async with self._lock:
            index = self._find_agent_index_unsafe(agent_id)
            if index is None: logger.error(f"Cannot update agent: ID '{agent_id}' not found."); return False

            # Check if agents list exists and index is valid
            agents_list = self._config_data.get("agents")
            if not isinstance(agents_list, list) or index >= len(agents_list):
                 logger.error(f"Internal state error finding agent index {index} for update."); return False

            original_config_entry = copy.deepcopy(agents_list[index])
            # Ensure the 'config' key exists before updating
            if "config" not in agents_list[index] or not isinstance(agents_list[index]["config"], dict):
                 logger.warning(f"Agent '{agent_id}' entry missing 'config' dictionary. Creating it.")
                 agents_list[index]["config"] = {}

            agents_list[index]["config"] = copy.deepcopy(updated_config_data)
            logger.info(f"Agent '{agent_id}' config updated internally.")

            if await self._save_config_safe():
                logger.info(f"Successfully updated agent '{agent_id}' and saved configuration.")
                return True
            else:
                # Rollback the update
                agents_list[index] = original_config_entry
                logger.error(f"Failed to save configuration after updating agent '{agent_id}'. Rolled back.")
                return False

    async def delete_agent(self, agent_id: str) -> bool:
        """Removes agent from 'agents' and 'teams', saves full config. Async-safe."""
        async with self._lock:
            index = self._find_agent_index_unsafe(agent_id)
            if index is None: logger.error(f"Cannot delete agent: ID '{agent_id}' not found."); return False

            agents_list = self._config_data.get("agents")
            if not isinstance(agents_list, list) or index >= len(agents_list):
                 logger.error(f"Internal state error finding agent index {index} for deletion."); return False

            # Backup original state for rollback
            original_agents = copy.deepcopy(agents_list)

            # Perform deletion
            deleted_entry = agents_list.pop(index)
            logger.info(f"Agent '{agent_id}' removed internally from 'agents' list.")

            # Attempt to save
            if await self._save_config_safe():
                logger.info(f"Successfully deleted agent '{agent_id}' and saved configuration.")
                return True
            else:
                # Rollback deletion
                self._config_data["agents"] = original_agents
                logger.error(f"Failed to save configuration after deleting agent '{agent_id}'. Deletion rolled back.")
                return False


# --- Create a Singleton Instance ---
config_manager = ConfigManager(AGENT_CONFIG_PATH_CM)
