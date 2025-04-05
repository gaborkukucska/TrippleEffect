# START OF FILE src/config/settings.py
import os
# import yaml # No longer needed here directly for loading
from dotenv import load_dotenv
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging

# Define base directory relative to this file's location
BASE_DIR = Path(__file__).resolve().parent.parent.parent # This should point to TrippleEffect-main/

# Explicitly load .env file from the project root directory
dotenv_path = BASE_DIR / '.env'
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)
    print(f"Loaded environment variables from: {dotenv_path}") # Optional debug print
else:
    print(f"Warning: .env file not found at {dotenv_path}. Environment variables might not be loaded.")

# Define the path to the agent configuration file
AGENT_CONFIG_PATH = BASE_DIR / 'config.yaml'

# --- Import the ConfigManager singleton instance ---
# This assumes config_manager.py is executed and the instance is created
try:
    # Use the path defined in config_manager itself if needed:
    # from src.config.config_manager import config_manager, AGENT_CONFIG_PATH_CM
    from src.config.config_manager import config_manager
    print("Successfully imported config_manager instance.")
except ImportError as e:
     print(f"Error importing config_manager: {e}. Agent configurations will not be loaded dynamically.")
     # Provide a fallback or raise an error depending on desired behavior
     # Fallback: define a dummy config_manager or load statically
     class DummyConfigManager:
         def _load_config_sync(self): self._agents_data = []
         def get_config_sync(self): return []
         # Add dummy async methods if needed elsewhere during testing
         async def get_config(self): return []
         async def load_config(self): return []

     # Use the actual AGENT_CONFIG_PATH for the dummy if needed
     config_manager = DummyConfigManager()


logger = logging.getLogger(__name__)


class Settings:
    """
    Holds application settings, loaded from environment variables and config.yaml.
    Manages API keys and base URLs for different LLM providers.
    Uses ConfigManager to load agent configurations synchronously at startup.
    """
    def __init__(self):
        # --- Provider Configuration (from .env) ---
        # OpenAI
        self.OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
        self.OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL") # Optional

        # OpenRouter
        self.OPENROUTER_API_KEY: Optional[str] = os.getenv("OPENROUTER_API_KEY")
        self.OPENROUTER_BASE_URL: Optional[str] = os.getenv("OPENROUTER_BASE_URL") # Optional
        self.OPENROUTER_REFERER: Optional[str] = os.getenv("OPENROUTER_REFERER") # Optional

        # Ollama
        self.OLLAMA_BASE_URL: Optional[str] = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") # Default if not set

        # --- Default Agent Configuration (from .env) ---
        # Used if not specified in config.yaml or if file is missing/incomplete.
        self.DEFAULT_AGENT_PROVIDER: str = os.getenv("DEFAULT_AGENT_PROVIDER", "openai") # Default provider
        self.DEFAULT_AGENT_MODEL: str = os.getenv("DEFAULT_AGENT_MODEL", "gpt-3.5-turbo")
        self.DEFAULT_SYSTEM_PROMPT: str = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful assistant.")
        self.DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", 0.7))
        self.DEFAULT_PERSONA: str = os.getenv("DEFAULT_PERSONA", "General Assistant")

        # --- Load Agent Configurations using ConfigManager (Synchronously) ---
        # Use the new synchronous getter for initialization.
        self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = config_manager.get_config_sync()
        if not self.AGENT_CONFIGURATIONS:
             print("Warning: No agent configurations loaded via ConfigManager.")


        # --- Other global settings ---
        # e.g., LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

        # Post-initialization check for necessary keys based on loaded configs
        self._check_required_keys()

    def _check_required_keys(self):
        """Checks if necessary API keys/URLs are set based on agent configurations."""
        required_openai = False
        required_openrouter = False
        required_ollama = False # Ollama usually doesn't require keys, maybe check URL reachability later?

        # Check defaults first
        if self.DEFAULT_AGENT_PROVIDER == "openai": required_openai = True
        if self.DEFAULT_AGENT_PROVIDER == "openrouter": required_openrouter = True
        if self.DEFAULT_AGENT_PROVIDER == "ollama": required_ollama = True

        # Check specific agent configs
        # Ensure AGENT_CONFIGURATIONS is iterable before looping
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 agent_conf = agent_conf_entry.get("config", {})
                 provider = agent_conf.get("provider")
                 if provider == "openai": required_openai = True
                 if provider == "openrouter": required_openrouter = True
                 if provider == "ollama": required_ollama = True
                 # Allow override via config.yaml's api_key/base_url - if they are set, we don't strictly need the .env var
                 if provider == "openai" and agent_conf.get("api_key"): required_openai = False
                 if provider == "openrouter" and agent_conf.get("api_key"): required_openrouter = False
        else:
             logger.error("AGENT_CONFIGURATIONS is not a list during _check_required_keys. Check loading.")


        print("-" * 30)
        print("Configuration Check:")
        if required_openai and not self.OPENAI_API_KEY:
            print("⚠️ WARNING: OpenAI provider is used, but OPENAI_API_KEY is missing in .env.")
        elif self.OPENAI_API_KEY:
             print("✅ OpenAI API Key: Found")

        if required_openrouter and not self.OPENROUTER_API_KEY:
            print("⚠️ WARNING: OpenRouter provider is used, but OPENROUTER_API_KEY is missing in .env.")
        elif self.OPENROUTER_API_KEY:
             print("✅ OpenRouter API Key: Found")

        if required_ollama and not self.OLLAMA_BASE_URL:
             # This default is set in __init__, so this might not trigger unless default changes
             print("⚠️ WARNING: Ollama provider is used, but OLLAMA_BASE_URL is missing in .env (and no default set).")
        elif self.OLLAMA_BASE_URL:
             print(f"✅ Ollama Base URL: {self.OLLAMA_BASE_URL}")

        # Check OpenRouter Referer (Recommended)
        if required_openrouter and not self.OPENROUTER_REFERER:
            print("ℹ️ INFO: OpenRouter provider is used, but OPENROUTER_REFERER is not set in .env. Using default.")
        elif self.OPENROUTER_REFERER:
            print(f"✅ OpenRouter Referer: {self.OPENROUTER_REFERER}")

        print("-" * 30)

    def get_provider_config(self, provider_name: str) -> Dict[str, Any]:
        """
        Gets the relevant API key and base URL for a given provider name
        based on environment variables.

        Args:
            provider_name (str): 'openai', 'ollama', or 'openrouter'.

        Returns:
            Dict[str, Any]: Containing 'api_key' and/or 'base_url' if applicable.
        """
        config = {}
        if provider_name == "openai":
            config['api_key'] = self.OPENAI_API_KEY
            config['base_url'] = self.OPENAI_BASE_URL # Can be None
        elif provider_name == "openrouter":
            config['api_key'] = self.OPENROUTER_API_KEY
            config['base_url'] = self.OPENROUTER_BASE_URL # Can be None
            config['referer'] = self.OPENROUTER_REFERER # Pass referer for header setup
        elif provider_name == "ollama":
            # Ollama typically doesn't use an API key
            config['api_key'] = None
            config['base_url'] = self.OLLAMA_BASE_URL
        else:
             logger.warning(f"Requested provider config for unknown provider '{provider_name}'")

        # Filter out None values before returning
        return {k: v for k, v in config.items() if v is not None}


    def get_agent_config_by_id(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific agent's configuration dictionary by its ID from the loaded configuration."""
        # Access the already loaded list
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 if agent_conf_entry.get('agent_id') == agent_id:
                     # Return the nested 'config' dictionary
                     return agent_conf_entry.get('config', {})
        return None

# Create a singleton instance of the Settings class
settings = Settings()

# Example Usage (after import: from src.config.settings import settings):
# openrouter_defaults = settings.get_provider_config('openrouter') -> {'api_key': '...', 'referer': '...'}
# ollama_defaults = settings.get_provider_config('ollama') -> {'base_url': 'http://...'}
# coder_config_dict = settings.get_agent_config_by_id('coder') -> {'provider': 'openai', 'model': ...}
# all_agent_configs = settings.AGENT_CONFIGURATIONS
