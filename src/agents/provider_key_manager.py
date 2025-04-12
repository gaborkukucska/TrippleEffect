# START OF FILE src/agents/provider_key_manager.py
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import copy

# Define path for storing quarantine state (adjust as needed)
# Using the same data directory as performance metrics
QUARANTINE_FILE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "quarantine_state.json"

logger = logging.getLogger(__name__)

class ProviderKeyManager:
    """
    Manages multiple API keys for external LLM providers and handles
    temporary quarantining of keys that encounter persistent errors (e.g., rate limits).
    """

    def __init__(self, provider_api_keys: Dict[str, List[str]], settings_obj):
        """
        Initializes the key manager.

        Args:
            provider_api_keys (Dict[str, List[str]]): A dictionary where keys are
                                                     provider names (e.g., 'openrouter')
                                                     and values are lists of API keys.
            settings_obj: The application settings instance to retrieve base URLs etc.
        """
        self._settings = settings_obj
        self._provider_keys: Dict[str, List[str]] = copy.deepcopy(provider_api_keys)
        # Track the index of the *next* key to try for each provider
        self._current_key_index: Dict[str, int] = {provider: 0 for provider in self._provider_keys}
        # Stores quarantined keys: {"provider/key_value": expiry_timestamp}
        self._quarantined_keys: Dict[str, float] = {}
        self._lock = asyncio.Lock() # Lock for safe concurrent updates to state

        self._load_quarantine_state_sync() # Load sync on init

    def _ensure_data_dir(self):
        """Ensures the directory for the quarantine state file exists."""
        try:
            QUARANTINE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Error creating data directory {QUARANTINE_FILE_PATH.parent}: {e}", exc_info=True)

    def _load_quarantine_state_sync(self):
        """Synchronously loads quarantine state from the JSON file."""
        self._ensure_data_dir()
        if not QUARANTINE_FILE_PATH.exists():
            logger.info("Quarantine state file not found. Initializing empty state.")
            self._quarantined_keys = {}
            return

        logger.info(f"Loading quarantine state from: {QUARANTINE_FILE_PATH}")
        try:
            with open(QUARANTINE_FILE_PATH, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
                if isinstance(loaded_data, dict):
                    # Validate data format (string keys, float values)
                    self._quarantined_keys = {
                        k: float(v) for k, v in loaded_data.items()
                        if isinstance(k, str) and isinstance(v, (int, float))
                    }
                    logger.info(f"Successfully loaded {len(self._quarantined_keys)} quarantine entries.")
                    # Clean up expired entries immediately
                    self._unquarantine_expired_keys_sync()
                else:
                    logger.error("Invalid format in quarantine state file (expected dictionary). Initializing empty state.")
                    self._quarantined_keys = {}
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from quarantine state file {QUARANTINE_FILE_PATH}: {e}. Initializing empty state.")
            self._quarantined_keys = {}
        except Exception as e:
            logger.error(f"Error loading quarantine state file {QUARANTINE_FILE_PATH}: {e}. Initializing empty state.", exc_info=True)
            self._quarantined_keys = {}

    async def save_quarantine_state(self):
        """Asynchronously saves the current quarantine state to the JSON file."""
        async with self._lock:
            # Clean expired keys before saving
            self._unquarantine_expired_keys_sync() # Can run sync under lock

            logger.info(f"Saving quarantine state ({len(self._quarantined_keys)} entries) to: {QUARANTINE_FILE_PATH}")
            try:
                # Ensure directory exists just before saving
                await asyncio.to_thread(self._ensure_data_dir)
                # Use temp file for atomic write (similar to performance tracker)
                temp_fd, temp_path_str = await asyncio.to_thread(
                    lambda: tempfile.mkstemp(suffix=".tmp", prefix=QUARANTINE_FILE_PATH.name + '_', dir=QUARANTINE_FILE_PATH.parent)
                )
                temp_file_path = Path(temp_path_str)

                def write_json_sync():
                    with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                        json.dump(self._quarantined_keys, f, indent=2)
                await asyncio.to_thread(write_json_sync)
                await asyncio.to_thread(os.replace, temp_file_path, QUARANTINE_FILE_PATH)
                logger.info(f"Successfully saved quarantine state to {QUARANTINE_FILE_PATH}")
            except Exception as e:
                logger.error(f"Error saving quarantine state file {QUARANTINE_FILE_PATH}: {e}", exc_info=True)
                if temp_file_path and temp_file_path.exists():
                    try: await asyncio.to_thread(os.remove, temp_file_path)
                    except Exception as rm_err: logger.error(f"Error removing temporary quarantine file {temp_file_path}: {rm_err}")


    def _unquarantine_expired_keys_sync(self):
        """Removes expired entries from the quarantine dictionary. Assumes lock is held or called during init."""
        current_time = time.time()
        expired_keys = [key for key, expiry in self._quarantined_keys.items() if expiry <= current_time]
        if expired_keys:
            for key in expired_keys:
                del self._quarantined_keys[key]
            logger.info(f"Unquarantined expired keys: {expired_keys}")

    def _is_key_quarantined(self, provider: str, key_value: str) -> bool:
        """Checks if a specific key for a provider is currently quarantined. Assumes lock is held."""
        quarantine_key = f"{provider}/{key_value}"
        expiry = self._quarantined_keys.get(quarantine_key)
        if expiry is None:
            return False
        if expiry <= time.time():
            # Expired, remove it (should be handled by _unquarantine_expired_keys_sync, but good fallback)
            logger.info(f"Found expired quarantine for '{quarantine_key}'. Removing.")
            del self._quarantined_keys[quarantine_key]
            return False
        return True # Still quarantined

    async def get_active_key_config(self, provider: str) -> Optional[Dict[str, Any]]:
        """
        Gets the configuration dictionary (including key, base_url, etc.)
        for the next available, non-quarantined key for the provider.
        Cycles through keys if the current one is quarantined or fails.

        Returns:
            Optional[Dict[str, Any]]: Config dict for the provider instance, or None if no valid key available.
        """
        async with self._lock:
            self._unquarantine_expired_keys_sync() # Clean up first

            keys = self._provider_keys.get(provider)
            if not keys:
                # Provider might be local (no keys) or simply has none configured
                if provider in ["ollama", "litellm"]:
                    # Return base config from settings for keyless local providers
                    return self._settings.get_provider_config(provider)
                logger.debug(f"No API keys configured for provider: {provider}")
                return None

            num_keys = len(keys)
            start_index = self._current_key_index.get(provider, 0) % num_keys

            # Iterate through keys starting from the current index, wrapping around once
            for i in range(num_keys):
                current_index = (start_index + i) % num_keys
                key_value = keys[current_index]

                if not self._is_key_quarantined(provider, key_value):
                    # Found a valid, non-quarantined key
                    self._current_key_index[provider] = (current_index + 1) % num_keys # Set index for *next* call
                    logger.info(f"Providing active key (Index {current_index}) for provider '{provider}'.")
                    # Get the base config and merge the specific key
                    base_config = self._settings.get_provider_config(provider)
                    base_config['api_key'] = key_value # Override/set the key
                    return base_config
                else:
                    logger.warning(f"Key (Index {current_index}) for provider '{provider}' is currently quarantined. Trying next.")

            # If we loop through all keys and all are quarantined
            logger.error(f"All {num_keys} keys for provider '{provider}' are currently quarantined.")
            return None # Indicate that the provider is currently unusable

    async def quarantine_key(self, provider: str, key_value: Optional[str], duration_seconds: int = 86400):
        """
        Marks a specific key for a provider as quarantined for a duration.

        Args:
            provider (str): The provider name.
            key_value (Optional[str]): The specific API key value to quarantine. If None, does nothing.
            duration_seconds (int): Duration in seconds (default: 24 hours).
        """
        if not key_value:
            logger.debug(f"Attempted to quarantine key for provider '{provider}' but key_value was None.")
            return

        async with self._lock:
            quarantine_key = f"{provider}/{key_value}"
            expiry_time = time.time() + duration_seconds
            self._quarantined_keys[quarantine_key] = expiry_time
            logger.warning(f"Quarantining key ending with '...{key_value[-4:]}' for provider '{provider}' until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expiry_time))}.")
            # Optionally trigger save immediately or rely on periodic/shutdown save
            # asyncio.create_task(self.save_quarantine_state())


    async def is_provider_depleted(self, provider: str) -> bool:
        """
        Checks if all configured keys for a given provider are currently quarantined.

        Returns:
            bool: True if all keys are quarantined or no keys are configured, False otherwise.
        """
        async with self._lock:
            self._unquarantine_expired_keys_sync() # Ensure state is current

            keys = self._provider_keys.get(provider)
            if not keys:
                # No keys configured, provider is effectively depleted for keyed access
                return True

            for key_value in keys:
                if not self._is_key_quarantined(provider, key_value):
                    return False # Found at least one non-quarantined key

            # If loop finishes, all keys are quarantined
            return True

# --- Helper for atomic writes (needed for save_quarantine_state) ---
import os
import tempfile
