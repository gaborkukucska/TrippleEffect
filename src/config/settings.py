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
    from src.config.config_manager import config_manager
    print("Successfully imported config_manager instance.")
except ImportError as e:
     print(f"Error importing config_manager: {e}. Agent configurations will not be loaded dynamically.")
     # Provide a fallback or raise an error depending on desired behavior
     # Fallback: define a dummy config_manager or load statically
     class DummyConfigManager:
         def _load_config_sync(self):
             self._agents_data = []
             self._teams_data = {} # Add dummy teams data
         def get_config_sync(self): return [], {} # Return tuple now
         # Add dummy async methods if needed elsewhere during testing
         async def get_config(self): return []
         async def get_teams(self): return {} # Add dummy async teams getter
         async def load_config(self): return []

     # Use the actual AGENT_CONFIG_PATH for the dummy if needed
     config_manager = DummyConfigManager()


logger = logging.getLogger(__name__)


class Settings:
    """
    Holds application settings, loaded from environment variables and config.yaml.
    Manages API keys, base URLs, default agent parameters, and team configurations.
    Uses ConfigManager to load agent and team configurations synchronously at startup.
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

        # --- Project/Session Configuration (from .env) ---
        self.PROJECTS_BASE_DIR: Path = Path(os.getenv("PROJECTS_BASE_DIR", str(BASE_DIR / "projects")))

        # --- Default Agent Configuration (from .env) ---
        self.DEFAULT_AGENT_PROVIDER: str = os.getenv("DEFAULT_AGENT_PROVIDER", "openrouter") # Changed default
        self.DEFAULT_AGENT_MODEL: str = os.getenv("DEFAULT_AGENT_MODEL", "google/gemini-2.5-pro-exp-03-25:free") # Changed default
        self.DEFAULT_SYSTEM_PROMPT: str = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful assistant.")
        self.DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", 0.7))
        self.DEFAULT_PERSONA: str = os.getenv("DEFAULT_PERSONA", "General Assistant")

        # --- Load Agent and Team Configurations using ConfigManager (Synchronously) ---
        # Use the new synchronous getter for initialization.
        # Need to adjust ConfigManager's sync load to also fetch teams if we modify it,
        # or keep loading the whole structure here. Let's load the whole structure.
        full_config = {}
        try:
            # Load the raw config dictionary synchronously
            # Modify ConfigManager's _load_config_sync and get_config_sync if needed
            # For now, assume config_manager._agents_data holds the list as before
            # and add loading for the 'teams' key directly here if needed or enhance ConfigManager.
            # Let's enhance ConfigManager first.
            # Assuming ConfigManager's get_config_sync is updated to return both.
            # Placeholder: Modify ConfigManager.py first, then update here.

            # ---> Modification needed in ConfigManager._load_config_sync & get_config_sync
            # ---> For now, we will access the raw loaded data via ConfigManager.

            # Accessing the raw loaded data (less ideal, better to enhance ConfigManager)
            # This requires accessing a potentially "private" attribute, which isn't best practice.
            # Let's stick to the plan: Update ConfigManager first.
            # Assume ConfigManager.get_full_config_sync() exists and returns {'agents': [...], 'teams': {...}}

            # --- Let's update ConfigManager first ---
            # Assume for now that config_manager loads the full structure.
            # We'll load the sections here.

            raw_config_data = config_manager.get_config_data_sync() # Needs to be added to ConfigManager

            self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = raw_config_data.get("agents", [])
            self.TEAMS_CONFIG: Dict[str, List[str]] = raw_config_data.get("teams", {})

        except AttributeError:
             logger.error("ConfigManager does not have 'get_config_data_sync' method. Loading manually.")
             # Manual loading as fallback (less ideal)
             self.AGENT_CONFIGURATIONS = []
             self.TEAMS_CONFIG = {}
             if AGENT_CONFIG_PATH.exists():
                 try:
                     with open(AGENT_CONFIG_PATH, 'r', encoding='utf-8') as f:
                         yaml_data = yaml.safe_load(f)
                         if yaml_data:
                             self.AGENT_CONFIGURATIONS = yaml_data.get("agents", [])
                             self.TEAMS_CONFIG = yaml_data.get("teams", {})
                 except Exception as e:
                     logger.error(f"Error loading config manually in settings: {e}")


        if not self.AGENT_CONFIGURATIONS:
             print("Warning: No agent configurations loaded.")
        if not self.TEAMS_CONFIG:
             print("Warning: No team configurations loaded.")
        else:
             print(f"Loaded teams: {list(self.TEAMS_CONFIG.keys())}")
             # Add validation: check if all agents listed in teams exist in the agents list
             all_configured_agent_ids = {agent.get("agent_id") for agent in self.AGENT_CONFIGURATIONS if agent.get("agent_id")}
             for team_name, member_ids in self.TEAMS_CONFIG.items():
                 for member_id in member_ids:
                     if member_id not in all_configured_agent_ids:
                         print(f"⚠️ WARNING: Agent '{member_id}' listed in team '{team_name}' but not found in the main 'agents' configuration list.")


        # --- Other global settings ---
        # e.g., LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

        # Ensure projects base directory exists
        self._ensure_projects_dir()

        # Post-initialization check for necessary keys based on loaded configs
        self._check_required_keys()

    def _ensure_projects_dir(self):
        """Creates the base directory for storing project/session data if it doesn't exist."""
        try:
             self.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             print(f"Ensured projects directory exists at: {self.PROJECTS_BASE_DIR}")
        except Exception as e:
             print(f"Error creating projects directory at {self.PROJECTS_BASE_DIR}: {e}")
             # Consider if this should be a fatal error

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
# openrouter_defaults = settings.get_provider_config('openrouter')
# all_agent_configs = settings.AGENT_CONFIGURATIONS
# teams_structure = settings.TEAMS_CONFIG
# projects_dir = settings.PROJECTS_BASE_DIR
