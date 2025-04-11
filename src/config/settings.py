# START OF FILE src/config/settings.py
import os
from dotenv import load_dotenv
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging
import json

# Define base directory relative to this file's location
BASE_DIR = Path(__file__).resolve().parent.parent.parent # Define BASE_DIR here for module scope

# Explicitly load .env file from the project root directory
dotenv_path = BASE_DIR / '.env'
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)
    print(f"Loaded environment variables from: {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}. Environment variables might not be loaded.")

# Import ConfigManager - this should be safe as it doesn't import settings
try:
    from src.config.config_manager import config_manager
    print("Successfully imported config_manager instance.")
except ImportError as e:
     print(f"Error importing config_manager: {e}. Agent configurations will not be loaded dynamically.")
     class DummyConfigManager: # Define Dummy if import fails
         def get_config_data_sync(self): return {"agents": [], "teams": {}}
         async def get_config(self): return []
         async def get_teams(self): return {}
         async def get_full_config(self): return {}
         async def load_config(self): return {"agents": [], "teams": {}}
         async def add_agent(self, a): return False
         async def update_agent(self, a, d): return False
         async def delete_agent(self, a): return False
     config_manager = DummyConfigManager()


# --- *** Import ModelRegistry *class* only *** ---
# We will instantiate it AFTER Settings is defined.
try:
    from src.config.model_registry import ModelRegistry as ModelRegistryClass # Rename to avoid name clash
    print("Successfully imported ModelRegistry class.")
    _ModelRegistry = ModelRegistryClass # Assign to temp variable
except ImportError as e:
    print(f"Error importing ModelRegistry class: {e}. Dynamic model discovery will not be available.")
    class DummyModelRegistry: # Define Dummy if import fails
        available_models: Dict = {}
        def __init__(self, settings_obj=None): pass # Accept arg even if dummy
        async def discover_models_and_providers(self): logger.error("ModelRegistry unavailable."); pass
        def get_available_models_list(self, p=None): return []
        def get_formatted_available_models(self): return "Model Registry Unavailable."
        def is_model_available(self, p, m): return False
        def get_available_models_dict(self) -> Dict[str, List[Any]]: return {}
        def find_provider_for_model(self, model_id: str) -> Optional[str]: return None
        def get_reachable_provider_url(self, provider: str) -> Optional[str]: return None
    _ModelRegistry = DummyModelRegistry # Assign Dummy to temp variable


logger = logging.getLogger(__name__)


class Settings:
    """
    Holds application settings, loaded from environment variables and config.yaml.
    Manages API keys, base URLs, default agent parameters, initial agent configs.
    Uses ConfigManager to load configurations synchronously at startup.
    ModelRegistry is instantiated *after* settings are loaded.
    Provides checks for provider configuration status.
    """
    def __init__(self):
        # --- Provider Configuration (from .env) ---
        self.OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
        self.OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL")
        self.OPENROUTER_API_KEY: Optional[str] = os.getenv("OPENROUTER_API_KEY")
        self.OPENROUTER_BASE_URL: Optional[str] = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.OPENROUTER_REFERER: Optional[str] = os.getenv("OPENROUTER_REFERER")
        self.OLLAMA_BASE_URL: Optional[str] = os.getenv("OLLAMA_BASE_URL") # Allow None, discovery will try localhost
        self.LITELLM_BASE_URL: Optional[str] = os.getenv("LITELLM_BASE_URL") # Allow None, discovery will try localhost
        self.LITELLM_API_KEY: Optional[str] = os.getenv("LITELLM_API_KEY")
        # Add other provider keys/URLs here...
        self.ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
        self.GOOGLE_API_KEY: Optional[str] = os.getenv("GOOGLE_API_KEY")
        self.DEEPSEEK_API_KEY: Optional[str] = os.getenv("DEEPSEEK_API_KEY")

        # --- Model Tier ---
        self.MODEL_TIER: str = os.getenv("MODEL_TIER", "ALL").upper()
        if self.MODEL_TIER not in ["FREE", "ALL"]:
             print(f"Warning: Invalid MODEL_TIER '{self.MODEL_TIER}'. Defaulting to 'ALL'.")
             self.MODEL_TIER = "ALL"

        # --- Project/Session Configuration (from .env) ---
        self.PROJECTS_BASE_DIR: Path = Path(os.getenv("PROJECTS_BASE_DIR", str(BASE_DIR / "projects")))

        # --- Tool Configuration (from .env) ---
        self.GITHUB_ACCESS_TOKEN: Optional[str] = os.getenv("GITHUB_ACCESS_TOKEN")

        # --- Default Agent Configuration (from .env) ---
        self.DEFAULT_AGENT_PROVIDER: str = os.getenv("DEFAULT_AGENT_PROVIDER", "openrouter")
        self.DEFAULT_AGENT_MODEL: str = os.getenv("DEFAULT_AGENT_MODEL", "google/gemini-flash-1.5:free")
        self.DEFAULT_SYSTEM_PROMPT: str = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful assistant.")
        self.DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", 0.7))
        self.DEFAULT_PERSONA: str = os.getenv("DEFAULT_PERSONA", "Assistant Agent")

        # --- Load Initial Configurations using ConfigManager ---
        raw_config_data: Dict[str, Any] = {}
        try:
            raw_config_data = config_manager.get_config_data_sync()
            print("Successfully loaded initial config via ConfigManager.get_config_data_sync()")
        except Exception as e:
            logger.error(f"Failed to load initial config via ConfigManager: {e}", exc_info=True)

        self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = raw_config_data.get("agents", [])
        if not isinstance(self.AGENT_CONFIGURATIONS, list):
            logger.error("Config format error: 'agents' key is not a list. Resetting to empty.")
            self.AGENT_CONFIGURATIONS = []

        self.TEAMS_CONFIG: Dict[str, List[str]] = raw_config_data.get("teams", {})
        if self.TEAMS_CONFIG:
            logger.warning("Config Warning: Static 'teams' definition found in config.yaml. This section is deprecated.")

        # --- Log Loaded Config Summary ---
        if not self.AGENT_CONFIGURATIONS: print("Warning: No bootstrap agent configurations loaded.")
        else: print(f"Loaded bootstrap agent IDs: {[a.get('agent_id', 'N/A') for a in self.AGENT_CONFIGURATIONS]}")
        print(f"Model Tier setting: {self.MODEL_TIER}")

        self._ensure_projects_dir()
        self._check_required_keys()

    def _ensure_projects_dir(self):
        """Creates the base directory for storing project/session data if it doesn't exist."""
        try:
             self.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             print(f"Ensured projects directory exists at: {self.PROJECTS_BASE_DIR}")
        except Exception as e:
             print(f"Error creating projects directory at {self.PROJECTS_BASE_DIR}: {e}")

    def _check_required_keys(self):
        """Checks if necessary API keys/URLs are set based on intent to use providers."""
        # Identify providers intended for use (bootstrap, defaults, or just check all known)
        # Let's check all known providers for simplicity now.
        known_providers = ["openai", "openrouter", "ollama", "litellm", "anthropic", "google", "deepseek"] # Add others
        providers_used_in_bootstrap = {self.DEFAULT_AGENT_PROVIDER}
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 provider = agent_conf_entry.get("config", {}).get("provider")
                 if provider: providers_used_in_bootstrap.add(provider)

        print("-" * 30); print("Provider Configuration Check:")
        for provider in known_providers:
             is_configured = self.is_provider_configured(provider)
             is_used = provider in providers_used_in_bootstrap
             if is_configured:
                 config_details = self.get_provider_config(provider)
                 detail_str = ", ".join(f"{k}: Set" for k, v in config_details.items() if v) # Show which parts are set
                 if not detail_str: detail_str = "URL Set" if provider in ["ollama", "litellm"] else "Key Set" # Fallback for local
                 print(f"✅ {provider.capitalize()}: Configured ({detail_str})")
             elif is_used:
                 print(f"⚠️ WARNING: {provider.capitalize()} used by bootstrap/default but not fully configured in .env.")
             # else: print(f"ℹ️ INFO: {provider.capitalize()} not configured and not explicitly used by bootstrap agents.") # Optional: Too verbose?

        # Specific checks
        if self.is_provider_configured("openrouter"):
            final_referer = self.OPENROUTER_REFERER or "http://localhost:8000/TrippleEffect"
            if not self.OPENROUTER_REFERER: print("  - OpenRouter Referer: Not set, using default.")
            else: print(f"  - OpenRouter Referer: {final_referer}")
        if self.GITHUB_ACCESS_TOKEN: print("✅ GitHub Access Token: Found (for GitHub tool)")
        else: print("ℹ️ INFO: GITHUB_ACCESS_TOKEN not set. GitHub tool will not function.")
        print("-" * 30)

    def get_provider_config(self, provider_name: str) -> Dict[str, Any]:
        """ Gets the relevant API key, base URL, and referer for a given provider name. """
        config = {}
        provider_name = provider_name.lower()
        if provider_name == "openai": config = {'api_key': self.OPENAI_API_KEY, 'base_url': self.OPENAI_BASE_URL}
        elif provider_name == "openrouter":
             referer = self.OPENROUTER_REFERER or "http://localhost:8000/TrippleEffect"; config = {'api_key': self.OPENROUTER_API_KEY, 'base_url': self.OPENROUTER_BASE_URL, 'referer': referer}
        elif provider_name == "ollama": config = {'api_key': None, 'base_url': self.OLLAMA_BASE_URL}
        elif provider_name == "litellm": config = {'api_key': self.LITELLM_API_KEY, 'base_url': self.LITELLM_BASE_URL}
        elif provider_name == "anthropic": config = {'api_key': self.ANTHROPIC_API_KEY} # Base URL often default
        elif provider_name == "google": config = {'api_key': self.GOOGLE_API_KEY} # Base URL often default
        elif provider_name == "deepseek": config = {'api_key': self.DEEPSEEK_API_KEY} # Base URL often default
        else:
             if provider_name: logger.warning(f"Requested provider config for unknown provider '{provider_name}'")
        return {k: v for k, v in config.items() if v is not None} # Filter out None values

    def is_provider_configured(self, provider_name: str) -> bool:
        """ Checks if a provider has its essential configuration set in .env. """
        provider_name = provider_name.lower()
        if provider_name == "openai": return bool(self.OPENAI_API_KEY)
        elif provider_name == "openrouter": return bool(self.OPENROUTER_API_KEY)
        elif provider_name == "ollama": return bool(self.OLLAMA_BASE_URL) # Only need URL, discovery checks reachability
        elif provider_name == "litellm": return bool(self.LITELLM_BASE_URL) # Only need URL, discovery checks reachability
        elif provider_name == "anthropic": return bool(self.ANTHROPIC_API_KEY)
        elif provider_name == "google": return bool(self.GOOGLE_API_KEY)
        elif provider_name == "deepseek": return bool(self.DEEPSEEK_API_KEY)
        else:
             # if provider_name: logger.warning(f"Checking configuration for unknown provider: {provider_name}")
             return False

    def get_agent_config_by_id(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific bootstrap agent's configuration dictionary by its ID."""
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 if agent_conf_entry.get('agent_id') == agent_id:
                     return agent_conf_entry.get('config', {})
        return None

    def get_formatted_allowed_models(self) -> str:
        """ Delegates to ModelRegistry. Requires discover_models() to have been run. """
        global model_registry # Access the global instance
        return model_registry.get_formatted_available_models()

# --- Create Singleton Instances ---
settings = Settings()

# --- *** Instantiate ModelRegistry *after* settings is created *** ---
# Pass the created settings instance to the registry constructor
model_registry = _ModelRegistry(settings)
print("Instantiated ModelRegistry singleton.")
# --- *** END INSTANTIATION *** ---
