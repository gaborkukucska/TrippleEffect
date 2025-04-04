# START OF FILE src/config/settings.py
import os
import yaml # Import the YAML library
from dotenv import load_dotenv
from pathlib import Path
from typing import List, Dict, Any, Optional

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

def load_agent_config() -> List[Dict[str, Any]]:
    """Loads agent configurations from the config.yaml file."""
    if not AGENT_CONFIG_PATH.exists():
        print(f"Warning: Agent configuration file not found at {AGENT_CONFIG_PATH}. Returning empty list.")
        return []
    try:
        with open(AGENT_CONFIG_PATH, 'r') as f:
            config_data = yaml.safe_load(f)
            if config_data and isinstance(config_data.get('agents'), list):
                print(f"Loaded {len(config_data['agents'])} agent configurations from {AGENT_CONFIG_PATH}.")
                return config_data['agents'] # Return the list of agents
            else:
                print(f"Warning: 'agents' list not found or empty in {AGENT_CONFIG_PATH}. Returning empty list.")
                return []
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file {AGENT_CONFIG_PATH}: {e}")
        return []
    except Exception as e:
        print(f"Error reading agent configuration file {AGENT_CONFIG_PATH}: {e}")
        return []


class Settings:
    """
    Holds application settings, loaded from environment variables and config.yaml.
    Manages API keys and base URLs for different LLM providers.
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

        # --- Load Agent Configurations from YAML ---
        # This list contains dictionaries like: {'agent_id': '...', 'config': {'provider': ..., 'model': ...}}
        self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = load_agent_config()

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
        for agent_conf_entry in self.AGENT_CONFIGURATIONS:
            agent_conf = agent_conf_entry.get("config", {})
            provider = agent_conf.get("provider")
            if provider == "openai": required_openai = True
            if provider == "openrouter": required_openrouter = True
            if provider == "ollama": required_ollama = True
            # Allow override via config.yaml's api_key/base_url - if they are set, we don't strictly need the .env var
            if provider == "openai" and agent_conf.get("api_key"): required_openai = False
            if provider == "openrouter" and agent_conf.get("api_key"): required_openrouter = False

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
             print(f"Warning: Requested provider config for unknown provider '{provider_name}'")

        # Filter out None values before returning
        return {k: v for k, v in config.items() if v is not None}


    # get_agent_config_by_id remains useful if needed elsewhere, no changes needed.
    def get_agent_config_by_id(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific agent's configuration dictionary by its ID."""
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
