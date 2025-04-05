# START OF FILE src/config/config_manager.py
import yaml
import os
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging
from threading import Lock # Use threading lock for file access synchronization

# Import settings to get BASE_DIR and AGENT_CONFIG_PATH
from src.config.settings import BASE_DIR, AGENT_CONFIG_PATH

logger = logging.getLogger(__name__)

class ConfigManager:
    """
    Manages reading and writing of the agent configuration file (config.yaml).
    Provides thread-safe methods for CRUD operations on agent configurations.
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
        self._lock = Lock() # Lock for thread-safe file operations
        self.load_config() # Load initial config

    def load_config(self) -> List[Dict[str, Any]]:
        """
        Reads the YAML configuration file. Handles file not found and parsing errors.
        Returns the list of agent configurations. This operation is thread-safe.
        """
        with self._lock:
            logger.info(f"Attempting to load agent configuration from: {self.config_path}")
            if not self.config_path.exists():
                logger.warning(f"Configuration file not found at {self.config_path}. Returning empty list.")
                self._agents_data = []
                return []
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config_data = yaml.safe_load(f)
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

    def _backup_config(self) -> bool:
        """Creates a backup of the current config file."""
        if not self.config_path.exists():
            return True # No file to backup
        backup_path = self.config_path.with_suffix(".yaml.bak")
        try:
            shutil.copy2(self.config_path, backup_path) # copy2 preserves metadata
            logger.info(f"Created backup of config file at: {backup_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to create backup of config file: {e}", exc_info=True)
            return False

    def save_config(self) -> bool:
        """
        Writes the current internal agent data list back to the YAML file.
        Includes a backup mechanism. This operation is thread-safe.

        Returns:
            bool: True if saving was successful, False otherwise.
        """
        with self._lock:
            logger.info(f"Attempting to save {len(self._agents_data)} agent configurations to: {self.config_path}")
            # Create backup before writing
            if not self._backup_config():
                 logger.error("Aborting save due to backup failure.")
                 return False

            try:
                # Ensure parent directory exists
                self.config_path.parent.mkdir(parents=True, exist_ok=True)

                config_to_save = {'agents': self._agents_data}
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(config_to_save, f, default_flow_style=False, sort_keys=False, indent=2)
                logger.info(f"Successfully saved configuration to {self.config_path}")
                return True
            except Exception as e:
                logger.error(f"Error writing configuration file {self.config_path}: {e}", exc_info=True)
                # Attempt to restore from backup? For now, just log the error.
                return False

    def get_config(self) -> List[Dict[str, Any]]:
        """Returns a copy of the currently loaded agent configuration list."""
        with self._lock:
            return self._agents_data[:] # Return a copy

    def find_agent_index(self, agent_id: str) -> Optional[int]:
        """Finds the index of an agent by ID in the internal list."""
        # Assumes lock is held by the caller if modification might occur
        for index, agent_data in enumerate(self._agents_data):
            if agent_data.get("agent_id") == agent_id:
                return index
        return None

    def add_agent(self, agent_config_entry: Dict[str, Any]) -> bool:
        """
        Adds a new agent configuration entry to the list and triggers save.
        Validates ID uniqueness. Thread-safe.

        Args:
            agent_config_entry (Dict[str, Any]): The complete agent entry dictionary
                                                 (e.g., {'agent_id': 'new_id', 'config': {...}}).

        Returns:
            bool: True if added and saved successfully, False otherwise.
        """
        with self._lock:
            agent_id = agent_config_entry.get("agent_id")
            if not agent_id:
                logger.error("Cannot add agent: 'agent_id' is missing.")
                return False
            if not isinstance(agent_config_entry.get("config"), dict):
                logger.error(f"Cannot add agent '{agent_id}': 'config' field is missing or not a dictionary.")
                return False

            if self.find_agent_index(agent_id) is not None:
                logger.error(f"Cannot add agent: Agent with ID '{agent_id}' already exists.")
                return False

            # Append the new agent entry
            self._agents_data.append(agent_config_entry)
            logger.info(f"Agent '{agent_id}' added internally.")

            # Save the updated configuration
            if self.save_config():
                logger.info(f"Successfully added and saved agent '{agent_id}'.")
                return True
            else:
                # Rollback the addition if save failed
                self._agents_data.pop()
                logger.error(f"Failed to save configuration after adding agent '{agent_id}'. Addition rolled back.")
                return False

    def update_agent(self, agent_id: str, updated_config_data: Dict[str, Any]) -> bool:
        """
        Updates the 'config' part of an existing agent entry identified by agent_id.
        Triggers save. Thread-safe.

        Args:
            agent_id (str): The ID of the agent configuration to update.
            updated_config_data (Dict[str, Any]): The new dictionary for the 'config' key.

        Returns:
            bool: True if updated and saved successfully, False otherwise.
        """
        with self._lock:
            index = self.find_agent_index(agent_id)
            if index is None:
                logger.error(f"Cannot update agent: Agent with ID '{agent_id}' not found.")
                return False

            # Keep a copy of the original config in case save fails
            original_config_entry = self._agents_data[index].copy()
            original_inner_config = original_config_entry.get("config", {}) # Get inner config

            # Update the 'config' key
            self._agents_data[index]["config"] = updated_config_data
            logger.info(f"Agent '{agent_id}' config updated internally.")

            # Save the updated configuration
            if self.save_config():
                logger.info(f"Successfully updated and saved agent '{agent_id}'.")
                return True
            else:
                # Rollback the update if save failed
                self._agents_data[index] = original_config_entry # Restore original entry
                logger.error(f"Failed to save configuration after updating agent '{agent_id}'. Update rolled back.")
                return False

    def delete_agent(self, agent_id: str) -> bool:
        """
        Removes an agent configuration entry by ID. Triggers save. Thread-safe.

        Args:
            agent_id (str): The ID of the agent configuration to remove.

        Returns:
            bool: True if deleted and saved successfully, False otherwise.
        """
        with self._lock:
            index = self.find_agent_index(agent_id)
            if index is None:
                logger.error(f"Cannot delete agent: Agent with ID '{agent_id}' not found.")
                return False

            # Keep a copy of the item being deleted in case save fails
            deleted_entry = self._agents_data.pop(index)
            logger.info(f"Agent '{agent_id}' removed internally.")

            # Save the updated configuration
            if self.save_config():
                logger.info(f"Successfully deleted and saved agent '{agent_id}'.")
                return True
            else:
                # Rollback the deletion if save failed
                self._agents_data.insert(index, deleted_entry) # Put it back
                logger.error(f"Failed to save configuration after deleting agent '{agent_id}'. Deletion rolled back.")
                return False


# --- Create a Singleton Instance ---
# This instance will be used by other modules (like settings and API routes)
config_manager = ConfigManager(AGENT_CONFIG_PATH)
