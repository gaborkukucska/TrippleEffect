# START OF FILE src/config/config_manager.py
import yaml
import os
import shutil
import asyncio # Import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging
import tempfile # Use tempfile for safer writing

# Import settings to get BASE_DIR and AGENT_CONFIG_PATH
# Note: This creates a slight circular dependency potential during startup,
# but should be okay as we only use BASE_DIR/AGENT_CONFIG_PATH constants.
# Consider passing the path directly if issues arise.
# from src.config.settings import BASE_DIR, AGENT_CONFIG_PATH # Moving import later if needed

logger = logging.getLogger(__name__)

# --- Define paths here to potentially resolve import order issues ---
BASE_DIR_CM = Path(__file__).resolve().parent.parent.parent
AGENT_CONFIG_PATH_CM = BASE_DIR_CM / 'config.yaml'


class ConfigManager:
    """
    Manages reading and writing of the agent configuration file (config.yaml).
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
        self._agents_data: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock() # Use asyncio.Lock for async safety
        # Initial load is done synchronously here for simplicity during startup.
        # If async loading at startup is needed, adjust main.py lifespan.
        self._load_config_sync()

    def _load_config_sync(self):
        """Synchronous initial load for use during startup."""
        logger.info(f"[Sync Load] Attempting to load agent configuration from: {self.config_path}")
        if not self.config_path.exists():
            logger.warning(f"[Sync Load] Configuration file not found at {self.config_path}. Initializing empty list.")
            self._agents_data = []
            return

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
                if config_data and isinstance(config_data.get('agents'), list):
                    self._agents_data = config_data['agents']
                    logger.info(f"[Sync Load] Successfully loaded {len(self._agents_data)} agent configurations.")
                else:
                    logger.warning(f"[Sync Load] 'agents' list not found or is not a list in {self.config_path}. Initializing empty list.")
                    self._agents_data = []
        except yaml.YAMLError as e:
            logger.error(f"[Sync Load] Error parsing YAML file {self.config_path}: {e}", exc_info=True)
            self._agents_data = [] # Reset internal state on error
        except Exception as e:
            logger.error(f"[Sync Load] Error reading configuration file {self.config_path}: {e}", exc_info=True)
            self._agents_data = [] # Reset internal state on error

    async def load_config(self) -> List[Dict[str, Any]]:
        """
        Asynchronously reads the YAML configuration file.
        Handles file not found and parsing errors. Thread-safe via asyncio.Lock.
        Returns the list of agent configurations.
        """
        async with self._lock:
            logger.info(f"Attempting to load agent configuration from: {self.config_path}")
            if not self.config_path.exists():
                logger.warning(f"Configuration file not found at {self.config_path}. Returning empty list.")
                self._agents_data = []
                return []
            try:
                 # Use asyncio.to_thread for file I/O to avoid blocking the event loop
                def read_file_sync():
                     with open(self.config_path, 'r', encoding='utf-8') as f:
                          return yaml.safe_load(f)

                config_data = await asyncio.to_thread(read_file_sync)

                if config_data and isinstance(config_data.get('agents'), list):
                    self._agents_data = config_data['agents']
                    logger.info(f"Successfully loaded {len(self._agents_data)} agent configurations.")
                    return self._agents_data[:] # Return a copy
                else:
                    logger.warning(f"'agents' list not found or is not a list in {self.config_path}. Returning empty list.")
                    self._agents_data = []
                    return []
            except yaml.YAMLError as e:
                logger.error(f"Error parsing YAML file {self.config_path}: {e}", exc_info=True)
                self._agents_data = [] # Reset internal state on error
                return []
            except Exception as e:
                logger.error(f"Error reading configuration file {self.config_path}: {e}", exc_info=True)
                self._agents_data = [] # Reset internal state on error
                return []

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
        Writes the current internal agent data list back to the YAML file atomically.
        Assumes the lock is already held by the caller. Uses a temporary file.

        Returns:
            bool: True if saving was successful, False otherwise.
        """
        logger.info(f"Attempting to save {len(self._agents_data)} agent configurations to: {self.config_path}")

        # 1. Create backup before writing temporary file
        if not await self._backup_config():
             logger.error("Aborting save due to backup failure.")
             return False

        # 2. Write to a temporary file in the same directory
        temp_file_path = None
        try:
            # Create a temporary file securely
            # Use a pattern and ensure it's in the same directory for atomic rename
            temp_fd, temp_path_str = tempfile.mkstemp(suffix=".tmp", prefix=self.config_path.name + '_', dir=self.config_path.parent)
            temp_file_path = Path(temp_path_str)

            config_to_save = {'agents': self._agents_data}

            # Use asyncio.to_thread for the blocking file write operation
            def write_yaml_sync():
                 with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                     yaml.dump(config_to_save, f, default_flow_style=False, sort_keys=False, indent=2)

            await asyncio.to_thread(write_yaml_sync)
            logger.debug(f"Successfully wrote configuration to temporary file: {temp_file_path}")

            # 3. Atomically replace the original file with the temporary file
            # os.replace is atomic on most POSIX systems and Windows
            await asyncio.to_thread(os.replace, temp_file_path, self.config_path)
            logger.info(f"Successfully saved configuration to {self.config_path} (atomic replace)")
            temp_file_path = None # Avoid deletion in finally block if successful

            # 4. Optionally clean up backup? Or leave it? Let's leave it for now.
            return True

        except Exception as e:
            logger.error(f"Error writing configuration file {self.config_path}: {e}", exc_info=True)
            # Attempt to restore from backup? Maybe not automatically. Log it clearly.
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
        """Returns a copy of the currently loaded agent configuration list. Async-safe."""
        async with self._lock:
            # Return a copy to prevent external modification of the internal list
            return [copy.deepcopy(agent) for agent in self._agents_data] # Use deepcopy for safety

    def get_config_sync(self) -> List[Dict[str, Any]]:
        """
        Synchronously returns a copy of the agent configuration list loaded during initialization.
        Intended for use during synchronous startup phases (like Settings init).
        Does NOT acquire the async lock. Relies on initial load being complete.
        """
        # Return a copy to prevent external modification of the internal list
        import copy
        return [copy.deepcopy(agent) for agent in self._agents_data] # Use deepcopy for safety


    def _find_agent_index_unsafe(self, agent_id: str) -> Optional[int]:
        """Internal helper: Finds the index of an agent by ID. Assumes lock is held."""
        for index, agent_data in enumerate(self._agents_data):
            if agent_data.get("agent_id") == agent_id:
                return index
        return None

    async def add_agent(self, agent_config_entry: Dict[str, Any]) -> bool:
        """
        Adds a new agent configuration entry to the list and triggers safe save.
        Validates ID uniqueness. Async-safe.

        Args:
            agent_config_entry (Dict[str, Any]): The complete agent entry dictionary
                                                 (e.g., {'agent_id': 'new_id', 'config': {...}}).

        Returns:
            bool: True if added and saved successfully, False otherwise.
        """
        async with self._lock:
            import copy # Import here if not globally available
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

            # Append a deep copy to avoid modifying the input dict
            self._agents_data.append(copy.deepcopy(agent_config_entry))
            logger.info(f"Agent '{agent_id}' added internally.")

            # Save the updated configuration safely
            if await self._save_config_safe():
                logger.info(f"Successfully added and saved agent '{agent_id}'.")
                return True
            else:
                # Rollback the addition if save failed
                self._agents_data.pop() # Remove the just added item
                logger.error(f"Failed to save configuration after adding agent '{agent_id}'. Addition rolled back.")
                return False

    async def update_agent(self, agent_id: str, updated_config_data: Dict[str, Any]) -> bool:
        """
        Updates the 'config' part of an existing agent entry identified by agent_id.
        Triggers safe save. Async-safe.

        Args:
            agent_id (str): The ID of the agent configuration to update.
            updated_config_data (Dict[str, Any]): The new dictionary for the 'config' key.

        Returns:
            bool: True if updated and saved successfully, False otherwise.
        """
        async with self._lock:
            import copy # Import here if not globally available
            index = self._find_agent_index_unsafe(agent_id)
            if index is None:
                logger.error(f"Cannot update agent: Agent with ID '{agent_id}' not found.")
                return False

            # Keep a deep copy of the original entry in case save fails
            original_config_entry = copy.deepcopy(self._agents_data[index])

            # Update the 'config' key with a deep copy of the new data
            self._agents_data[index]["config"] = copy.deepcopy(updated_config_data)
            logger.info(f"Agent '{agent_id}' config updated internally.")

            # Save the updated configuration safely
            if await self._save_config_safe():
                logger.info(f"Successfully updated and saved agent '{agent_id}'.")
                return True
            else:
                # Rollback the update if save failed
                self._agents_data[index] = original_config_entry # Restore original entry
                logger.error(f"Failed to save configuration after updating agent '{agent_id}'. Update rolled back.")
                return False

    async def delete_agent(self, agent_id: str) -> bool:
        """
        Removes an agent configuration entry by ID. Triggers safe save. Async-safe.

        Args:
            agent_id (str): The ID of the agent configuration to remove.

        Returns:
            bool: True if deleted and saved successfully, False otherwise.
        """
        async with self._lock:
            index = self._find_agent_index_unsafe(agent_id)
            if index is None:
                logger.error(f"Cannot delete agent: Agent with ID '{agent_id}' not found.")
                return False

            # Keep a copy of the item being deleted in case save fails
            deleted_entry = self._agents_data.pop(index)
            logger.info(f"Agent '{agent_id}' removed internally.")

            # Save the updated configuration safely
            if await self._save_config_safe():
                logger.info(f"Successfully deleted and saved agent '{agent_id}'.")
                return True
            else:
                # Rollback the deletion if save failed
                self._agents_data.insert(index, deleted_entry) # Put it back
                logger.error(f"Failed to save configuration after deleting agent '{agent_id}'. Deletion rolled back.")
                return False


# --- Create a Singleton Instance ---
# This instance will be used by other modules (like settings and API routes)
# The initial load is synchronous within __init__
config_manager = ConfigManager(AGENT_CONFIG_PATH_CM) # Use path defined above

# Quick check if initial load failed severely (optional)
# if not hasattr(config_manager, '_agents_data'):
#      raise RuntimeError("ConfigManager failed initial load critically.")
