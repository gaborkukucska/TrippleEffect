# START OF FILE src/agents/provider_key_manager.py
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import copy
import os # For remove/replace
import tempfile # For atomic writes

# Define path for storing quarantine state (adjust as needed)
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
        self._current_key_index: Dict[str, int] = {provider: 0 for provider in self._provider_keys}
        self._quarantined_keys: Dict[str, float] = {}
        self._lock = asyncio.Lock()

        self._load_quarantine_state_sync()

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
                    self._quarantined_keys = {
                        k: float(v) for k, v in loaded_data.items()
                        if isinstance(k, str) and isinstance(v, (int, float))
                    }
                    logger.info(f"Successfully loaded {len(self._quarantined_keys)} quarantine entries.")
                    self._unquarantine_expired_keys_sync()
                else:
                    logger.error("Invalid format in quarantine state file. Initializing empty.")
                    self._quarantined_keys = {}
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from quarantine file: {e}. Initializing empty.")
            self._quarantined_keys = {}
        except Exception as e:
            logger.error(f"Error loading quarantine file: {e}. Initializing empty.", exc_info=True)
            self._quarantined_keys = {}

    async def save_quarantine_state(self):
        """Asynchronously saves the current quarantine state to the JSON file."""
        async with self._lock:
            self._unquarantine_expired_keys_sync() # Clean up just before saving
            logger.info(f"Saving quarantine state ({len(self._quarantined_keys)} entries) to: {QUARANTINE_FILE_PATH}")
            temp_file_path = None
            try:
                await asyncio.to_thread(self._ensure_data_dir)
                temp_fd, temp_path_str = await asyncio.to_thread(
                    lambda: tempfile.mkstemp(suffix=".tmp", prefix=QUARANTINE_FILE_PATH.name + '_', dir=QUARANTINE_FILE_PATH.parent)
                )
                temp_file_path = Path(temp_path_str)
                def write_json_sync():
                    with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                        # Use cleaned keys from the dictionary directly
                        json.dump(self._quarantined_keys, f, indent=2)
                await asyncio.to_thread(write_json_sync)
                await asyncio.to_thread(os.replace, temp_file_path, QUARANTINE_FILE_PATH)
                logger.info(f"Successfully saved quarantine state to {QUARANTINE_FILE_PATH}")
                temp_file_path = None # Avoid deletion in finally
            except Exception as e:
                logger.error(f"Error saving quarantine state file {QUARANTINE_FILE_PATH}: {e}", exc_info=True)
                if temp_file_path and await asyncio.to_thread(temp_file_path.exists):
                    try: await asyncio.to_thread(os.remove, temp_file_path)
                    except Exception as rm_err: logger.error(f"Error removing temporary quarantine file {temp_file_path}: {rm_err}")


    def _unquarantine_expired_keys_sync(self):
        """Removes expired entries from the quarantine dictionary. Assumes lock is held or called during init."""
        current_time = time.time()
        # Use list comprehension for potentially faster creation of keys to delete
        expired_keys = [key for key, expiry in self._quarantined_keys.items() if expiry <= current_time]
        if expired_keys:
            deleted_count = 0
            for key in expired_keys:
                # Ensure key exists before trying to delete (important if called concurrently without lock, though not the case here)
                if key in self._quarantined_keys:
                    del self._quarantined_keys[key]
                    deleted_count += 1
            if deleted_count > 0:
                logger.info(f"Unquarantined {deleted_count} expired key(s): {expired_keys}")


    def _get_clean_key_value(self, key_value: Optional[str]) -> Optional[str]:
        """Helper to clean potential whitespace or unwanted chars from key."""
        if key_value is None: return None
        return key_value.strip().rstrip('>')

    def _is_key_quarantined(self, provider: str, key_value: str) -> bool:
        """Checks if a specific key for a provider is currently quarantined."""
        cleaned_key = self._get_clean_key_value(key_value)
        if not cleaned_key: return False

        quarantine_key = f"{provider}/{cleaned_key}"
        expiry = self._quarantined_keys.get(quarantine_key)
        if expiry is None: return False

        if expiry <= time.time():
            # logger.info(f"Found expired quarantine for '{quarantine_key}'. Removing.") # Moved logging to unquarantine func
            # This function is read-only check, actual removal done by _unquarantine_expired_keys_sync
            return False
        return True # Still valid and quarantined

    async def get_active_key_config(self, provider: str) -> Optional[Dict[str, Any]]:
        """ Gets config for the next available, non-quarantined key. """
        async with self._lock:
            self._unquarantine_expired_keys_sync()
            keys = self._provider_keys.get(provider)
            if not keys:
                if provider in ["ollama", "litellm"]: return self._settings.get_provider_config(provider)
                logger.debug(f"No API keys configured for provider: {provider}")
                return None

            num_keys = len(keys)
            start_index = self._current_key_index.get(provider, 0) % num_keys
            for i in range(num_keys):
                current_index = (start_index + i) % num_keys
                key_value = keys[current_index]
                if not self._is_key_quarantined(provider, key_value): # Uses cleaned key for check
                    self._current_key_index[provider] = (current_index + 1) % num_keys
                    logger.info(f"Providing active key (Index {current_index}) for provider '{provider}'.")
                    base_config = self._settings.get_provider_config(provider)
                    base_config['api_key'] = key_value # Use original key value
                    return base_config
                else:
                    logger.warning(f"Key (Index {current_index}) for provider '{provider}' is currently quarantined. Trying next.")
            logger.error(f"All {num_keys} keys for provider '{provider}' are currently quarantined.")
            return None

    async def quarantine_key(self, provider: str, key_value: Optional[str], duration_seconds: int = 86400):
        """ Marks a specific key for a provider as quarantined. """
        cleaned_key = self._get_clean_key_value(key_value)
        if not cleaned_key:
            logger.debug(f"Attempted to quarantine key for provider '{provider}' but key_value was None or invalid after cleaning.")
            return

        async with self._lock:
            quarantine_dict_key = f"{provider}/{cleaned_key}"
            expiry_time = time.time() + duration_seconds
            self._quarantined_keys[quarantine_dict_key] = expiry_time
            logger.warning(f"Quarantining key ending with '...{cleaned_key[-4:]}' for provider '{provider}' until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expiry_time))}.")
            # --- REMOVED await self.save_quarantine_state() ---
            # Let cleanup handle saving on shutdown or implement periodic saving elsewhere if needed


    async def is_provider_depleted(self, provider: str) -> bool:
        """ Checks if all configured keys for a provider are quarantined. """
        async with self._lock:
            self._unquarantine_expired_keys_sync()
            keys = self._provider_keys.get(provider)
            if not keys: return True
            for key_value in keys:
                if not self._is_key_quarantined(provider, key_value): # Uses cleaned key for check
                    return False
            return True
