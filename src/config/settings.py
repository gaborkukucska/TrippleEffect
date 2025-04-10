# START OF FILE src/config/settings.py
import os
from dotenv import load_dotenv
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging
import json

# Define base directory relative to this file's location
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Explicitly load .env file from the project root directory
dotenv_path = BASE_DIR / '.env'
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)
    print(f"Loaded environment variables from: {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}. Environment variables might not be loaded.")

# Define the path to the agent configuration file
AGENT_CONFIG_PATH = BASE_DIR / 'config.yaml'

# Import the ConfigManager singleton instance
try:
    from src.config.config_manager import config_manager
    print("Successfully imported config_manager instance.")
except ImportError as e:
     print(f"Error importing config_manager: {e}. Agent configurations will not be loaded dynamically.")
     class DummyConfigManager:
         def get_config_data_sync(self): return {"agents": [], "teams": {}} # Removed allowed_models
         async def get_config(self): return []
         async def get_teams(self): return {}
         async def get_full_config(self): return {}
         async def load_config(self): return {"agents": [], "teams": {}} # Removed allowed_models
         async def add_agent(self, a): return False
         async def update_agent(self, a, d): return False
         async def delete_agent(self, a): return False
     config_manager = DummyConfigManager()

# --- *** NEW: Import ModelRegistry *** ---
try:
    from src.config.model_registry import ModelRegistry
    print("Successfully imported ModelRegistry.")
    # --- *** NEW: Instantiate ModelRegistry *** ---
    model_registry = ModelRegistry()
    print("Instantiated ModelRegistry.")
except ImportError as e:
    print(f"Error importing ModelRegistry: {e}. Dynamic model discovery will not be available.")
    class DummyModelRegistry:
        available_models: Dict = {}
        async def discover_models(self): logger.error("ModelRegistry unavailable."); pass
        def get_available_models_list(self, p=None): return []
        def get_formatted_available_models(self): return "Model Registry Unavailable."
        def is_model_available(self, p, m): return False
        def get_available_models_dict(self) -> Dict[str, List[Any]]: return {}
    model_registry = DummyModelRegistry()


logger = logging.getLogger(__name__)


class Settings:
    """
    Holds application settings, loaded from environment variables and config.yaml.
    Manages API keys, base URLs, default agent parameters, initial agent configs.
    Uses ConfigManager to load configurations synchronously at startup.
    Uses ModelRegistry for dynamic model availability (discovery run separately).
    Provides checks for provider configuration status.
    """
    def __init__(self):
        # --- Provider Configuration (from .env) ---
        self.OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
        self.OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL")
        self.OPENROUTER_API_KEY: Optional[str] = os.getenv("OPENROUTER_API_KEY")
        self.OPENROUTER_BASE_URL: Optional[str] = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.OPENROUTER_REFERER: Optional[str] = os.getenv("OPENROUTER_REFERER")
        self.OLLAMA_BASE_URL: Optional[str] = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        # --- *** NEW: LiteLLM Config *** ---
        self.LITELLM_BASE_URL: Optional[str] = os.getenv("LITELLM_BASE_URL")
        self.LITELLM_API_KEY: Optional[str] = os.getenv("LITELLM_API_KEY")

        # --- *** NEW: Model Tier *** ---
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
            # Load only agents and teams now, allowed_models handled by registry
            raw_config_data = config_manager.get_config_data_sync()
            print("Successfully loaded initial config via ConfigManager.get_config_data_sync()")
        except Exception as e:
            logger.error(f"Failed to load initial config via ConfigManager: {e}", exc_info=True)

        # Bootstrap Agents config
        self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = raw_config_data.get("agents", [])
        if not isinstance(self.AGENT_CONFIGURATIONS, list):
            logger.error("Config format error: 'agents' key is not a list. Resetting to empty.")
            self.AGENT_CONFIGURATIONS = []

        # Deprecated static Teams config
        self.TEAMS_CONFIG: Dict[str, List[str]] = raw_config_data.get("teams", {})
        if self.TEAMS_CONFIG:
            logger.warning("Config Warning: Static 'teams' definition found in config.yaml. This section is deprecated and teams should be managed dynamically via Admin AI.")

        # --- REMOVED loading of ALLOWED_SUB_AGENT_MODELS from config ---

        # --- Log Loaded Config Summary ---
        if not self.AGENT_CONFIGURATIONS:
             print("Warning: No bootstrap agent configurations loaded.")
        else:
             print(f"Loaded bootstrap agent IDs: {[a.get('agent_id', 'N/A') for a in self.AGENT_CONFIGURATIONS]}")

        print(f"Model Tier setting: {self.MODEL_TIER}")
        # Model registry availability will be logged after discovery in main.py

        # --- Other global settings ---
        # e.g., LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

        self._ensure_projects_dir()
        self._check_required_keys() # Check keys AFTER loading all env vars

    def _ensure_projects_dir(self):
        """Creates the base directory for storing project/session data if it doesn't exist."""
        try:
             self.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
             print(f"Ensured projects directory exists at: {self.PROJECTS_BASE_DIR}")
        except Exception as e:
             print(f"Error creating projects directory at {self.PROJECTS_BASE_DIR}: {e}")

    def _check_required_keys(self):
        """Checks if necessary API keys/URLs are set based on bootstrap agent configurations."""
        providers_used_in_bootstrap = {self.DEFAULT_AGENT_PROVIDER}
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 provider = agent_conf_entry.get("config", {}).get("provider")
                 if provider: providers_used_in_bootstrap.add(provider)

        print("-" * 30); print("Provider Configuration Check:")
        # OpenAI
        if self.is_provider_configured("openai"): print("✅ OpenAI API Key: Found")
        elif "openai" in providers_used_in_bootstrap: print("⚠️ WARNING: OpenAI used but OPENAI_API_KEY missing.")
        # OpenRouter
        if self.is_provider_configured("openrouter"): print("✅ OpenRouter API Key: Found")
        elif "openrouter" in providers_used_in_bootstrap: print("⚠️ WARNING: OpenRouter used but OPENROUTER_API_KEY missing.")
        # Ollama
        if self.is_provider_configured("ollama"): print(f"✅ Ollama Base URL: {self.OLLAMA_BASE_URL}")
        elif "ollama" in providers_used_in_bootstrap: print("⚠️ WARNING: Ollama used but OLLAMA_BASE_URL missing.")
        # --- *** NEW: LiteLLM Check *** ---
        if self.is_provider_configured("litellm"): print(f"✅ LiteLLM Base URL: {self.LITELLM_BASE_URL}")
        elif "litellm" in providers_used_in_bootstrap: print("⚠️ WARNING: LiteLLM used but LITELLM_BASE_URL missing.")
        if self.LITELLM_API_KEY: print("✅ LiteLLM API Key: Found (Optional)")
        # --- *** END NEW *** ---

        # Check OpenRouter Referer separately
        if "openrouter" in providers_used_in_bootstrap:
            final_referer = self.OPENROUTER_REFERER or "http://localhost:8000/TrippleEffect" # Fallback
            if not self.OPENROUTER_REFERER: print("ℹ️ INFO: OpenRouter used, but OPENROUTER_REFERER not set. Using default.")
            else: print(f"✅ OpenRouter Referer: {final_referer}")

        # Check GitHub Token
        if self.GITHUB_ACCESS_TOKEN: print("✅ GitHub Access Token: Found (for GitHub tool)")
        else: print("ℹ️ INFO: GITHUB_ACCESS_TOKEN not set. GitHub tool will not function.")
        print("-" * 30)

    def get_provider_config(self, provider_name: str) -> Dict[str, Any]:
        """
        Gets the relevant API key, base URL, and referer for a given provider name.
        """
        config = {}
        provider_name = provider_name.lower()
        if provider_name == "openai":
            config = {'api_key': self.OPENAI_API_KEY, 'base_url': self.OPENAI_BASE_URL}
        elif provider_name == "openrouter":
             referer = self.OPENROUTER_REFERER or "http://localhost:8000/TrippleEffect" # Fallback
             config = {'api_key': self.OPENROUTER_API_KEY, 'base_url': self.OPENROUTER_BASE_URL, 'referer': referer}
        elif provider_name == "ollama":
            config = {'api_key': None, 'base_url': self.OLLAMA_BASE_URL}
        # --- *** NEW: LiteLLM Config *** ---
        elif provider_name == "litellm":
             config = {'api_key': self.LITELLM_API_KEY, 'base_url': self.LITELLM_BASE_URL}
        # --- *** END NEW *** ---
        else:
             logger.warning(f"Requested provider config for unknown provider '{provider_name}'")
        # Filter out None values before returning
        return {k: v for k, v in config.items() if v is not None}


    def is_provider_configured(self, provider_name: str) -> bool:
        """
        Checks if a provider has its essential configuration set in .env.
        """
        provider_name = provider_name.lower()
        if provider_name == "openai":
            return bool(self.OPENAI_API_KEY and self.OPENAI_API_KEY.strip())
        elif provider_name == "openrouter":
            return bool(self.OPENROUTER_API_KEY and self.OPENROUTER_API_KEY.strip())
        elif provider_name == "ollama":
            return bool(self.OLLAMA_BASE_URL and self.OLLAMA_BASE_URL.strip())
        # --- *** NEW: LiteLLM Check *** ---
        elif provider_name == "litellm":
             return bool(self.LITELLM_BASE_URL and self.LITELLM_BASE_URL.strip()) # API key is optional
        # --- *** END NEW *** ---
        else:
            # Only log warning if provider name is not empty/None
            if provider_name:
                logger.warning(f"Checking configuration for unknown provider: {provider_name}")
            return False

    def get_agent_config_by_id(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific bootstrap agent's configuration dictionary by its ID from the loaded configuration."""
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 if agent_conf_entry.get('agent_id') == agent_id:
                     return agent_conf_entry.get('config', {})
        return None

    # --- *** MODIFIED: Delegate to ModelRegistry *** ---
    def get_formatted_allowed_models(self) -> str:
        """
        Returns a formatted string listing AVAILABLE models from the registry.
        NOTE: Requires model_registry.discover_models() to have been run.
        """
        global model_registry
        return model_registry.get_formatted_available_models()

# Create a singleton instance of the Settings class
settings = Settings()
