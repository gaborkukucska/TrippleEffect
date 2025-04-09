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
         def get_config_data_sync(self): return {"agents": [], "teams": {}, "allowed_sub_agent_models": {}}
         async def get_config(self): return []
         async def get_teams(self): return {}
         async def get_full_config(self): return {}
         async def load_config(self): return {"agents": [], "teams": {}, "allowed_sub_agent_models": {}}
         async def add_agent(self, a): return False
         async def update_agent(self, a, d): return False
         async def delete_agent(self, a): return False
     config_manager = DummyConfigManager()


logger = logging.getLogger(__name__)


class Settings:
    """
    Holds application settings, loaded from environment variables and config.yaml.
    Manages API keys, base URLs, default agent parameters, initial agent configs,
    and constraints for dynamic agents (allowed_sub_agent_models).
    Uses ConfigManager to load configurations synchronously at startup.
    Provides checks for provider configuration status.
    """
    def __init__(self):
        # --- Provider Configuration (from .env) ---
        self.OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
        self.OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL")
        self.OPENROUTER_API_KEY: Optional[str] = os.getenv("OPENROUTER_API_KEY")
        self.OPENROUTER_BASE_URL: Optional[str] = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1") # Added default
        self.OPENROUTER_REFERER: Optional[str] = os.getenv("OPENROUTER_REFERER")
        self.OLLAMA_BASE_URL: Optional[str] = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

        # --- Project/Session Configuration (from .env) ---
        self.PROJECTS_BASE_DIR: Path = Path(os.getenv("PROJECTS_BASE_DIR", str(BASE_DIR / "projects")))

        # --- Tool Configuration (from .env) --- # *** ADDED GITHUB TOKEN LOADING ***
        self.GITHUB_ACCESS_TOKEN: Optional[str] = os.getenv("GITHUB_ACCESS_TOKEN") # *** ENSURE THIS LINE EXISTS ***

        # --- Default Agent Configuration (from .env) ---
        self.DEFAULT_AGENT_PROVIDER: str = os.getenv("DEFAULT_AGENT_PROVIDER", "openrouter")
        self.DEFAULT_AGENT_MODEL: str = os.getenv("DEFAULT_AGENT_MODEL", "google/gemini-flash-1.5:free")
        self.DEFAULT_SYSTEM_PROMPT: str = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful assistant.")
        self.DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", 0.7))
        self.DEFAULT_PERSONA: str = os.getenv("DEFAULT_PERSONA", "General Assistant")

        # --- Load Initial Configurations using ConfigManager ---
        raw_config_data: Dict[str, Any] = {}
        try:
            raw_config_data = config_manager.get_config_data_sync()
            print("Successfully loaded initial config via ConfigManager.get_config_data_sync()")
        except Exception as e:
            logger.error(f"Failed to load initial config via ConfigManager: {e}", exc_info=True)

        # Bootstrap Agents config
        self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = raw_config_data.get("agents", [])
        if not isinstance(self.AGENT_CONFIGURATIONS, list):
            logger.error("Config format error: 'agents' key is not a list. Resetting to empty.")
            self.AGENT_CONFIGURATIONS = []

        # Allowed models for dynamic agents
        self.ALLOWED_SUB_AGENT_MODELS: Dict[str, List[str]] = raw_config_data.get("allowed_sub_agent_models", {})
        if not isinstance(self.ALLOWED_SUB_AGENT_MODELS, dict):
            logger.error("Config format error: 'allowed_sub_agent_models' key is not a dictionary. Resetting to empty.")
            self.ALLOWED_SUB_AGENT_MODELS = {}

        # Deprecated static Teams config
        self.TEAMS_CONFIG: Dict[str, List[str]] = raw_config_data.get("teams", {})
        if self.TEAMS_CONFIG:
            logger.warning("Config Warning: Static 'teams' definition found in config.yaml. This section is deprecated and teams should be managed dynamically via Admin AI.")

        # --- Log Loaded Config Summary ---
        if not self.AGENT_CONFIGURATIONS:
             print("Warning: No bootstrap agent configurations loaded.")
        else:
             print(f"Loaded bootstrap agent IDs: {[a.get('agent_id', 'N/A') for a in self.AGENT_CONFIGURATIONS]}")

        if not self.ALLOWED_SUB_AGENT_MODELS:
             print("Warning: No 'allowed_sub_agent_models' constraints loaded. Dynamic agent creation might be unrestricted or fail.")
        else:
             print("Loaded allowed sub-agent models:")
             [print(f"  - {provider}: {models}") for provider, models in self.ALLOWED_SUB_AGENT_MODELS.items()]

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

        print("-" * 30); print("Bootstrap Configuration Check:")
        if "openai" in providers_used_in_bootstrap:
            if not self.is_provider_configured("openai"): print("⚠️ WARNING: OpenAI provider used by bootstrap/default, but OPENAI_API_KEY missing/empty.")
            else: print("✅ OpenAI API Key: Found")
        if "openrouter" in providers_used_in_bootstrap:
            if not self.is_provider_configured("openrouter"): print("⚠️ WARNING: OpenRouter provider used by bootstrap/default, but OPENROUTER_API_KEY missing/empty.")
            else: print("✅ OpenRouter API Key: Found")
        if "ollama" in providers_used_in_bootstrap:
            if not self.is_provider_configured("ollama"): print("⚠️ WARNING: Ollama provider used by bootstrap/default, but OLLAMA_BASE_URL missing/empty (and no default).")
            elif self.OLLAMA_BASE_URL: print(f"✅ Ollama Base URL: {self.OLLAMA_BASE_URL}")

        # Check OpenRouter Referer separately
        if "openrouter" in providers_used_in_bootstrap:
            final_referer = self.OPENROUTER_REFERER or "http://localhost:8000/TrippleEffect" # Fallback
            if not self.OPENROUTER_REFERER: print("ℹ️ INFO: OpenRouter provider used, but OPENROUTER_REFERER not set. Using default.")
            else: print(f"✅ OpenRouter Referer: {final_referer}")

        # --- Check GitHub Token --- # *** ADDED THIS CHECK ***
        if self.GITHUB_ACCESS_TOKEN:
            print("✅ GitHub Access Token: Found (for GitHub tool)")
        else:
            print("ℹ️ INFO: GITHUB_ACCESS_TOKEN not set. GitHub tool will not function.")
        # --- End GitHub Token Check ---
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
        else:
             logger.warning(f"Requested provider config for unknown provider '{provider_name}'")
        return config

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
        else:
            logger.warning(f"Checking configuration for unknown provider: {provider_name}")
            return False

    def get_agent_config_by_id(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific bootstrap agent's configuration dictionary by its ID from the loaded configuration."""
        if isinstance(self.AGENT_CONFIGURATIONS, list):
             for agent_conf_entry in self.AGENT_CONFIGURATIONS:
                 if agent_conf_entry.get('agent_id') == agent_id:
                     return agent_conf_entry.get('config', {})
        return None

    def get_formatted_allowed_models(self) -> str:
        """ Returns a formatted string listing allowed models, suitable for prompts. """
        if not self.ALLOWED_SUB_AGENT_MODELS:
            return "Dynamic agent creation constraints: No models specified."
        lines = ["**Allowed Models for Dynamic Agent Creation:**"]
        try:
            valid_providers = { p: m for p, m in self.ALLOWED_SUB_AGENT_MODELS.items() if m and any(model.strip() for model in m) }
            if not valid_providers: return "Dynamic agent creation constraints: No valid models specified."
            for provider, models in valid_providers.items():
                 valid_models = [m for m in models if m and m.strip()]
                 if valid_models: lines.append(f"- **{provider}**: `{', '.join(valid_models)}`")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error formatting allowed models: {e}")
            try: return "**Allowed Models (Raw):**\n```json\n" + json.dumps(self.ALLOWED_SUB_AGENT_MODELS, indent=2) + "\n```"
            except: return "**Error:** Could not format allowed models list."

# Create a singleton instance of the Settings class
settings = Settings()
