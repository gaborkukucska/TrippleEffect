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
    # print(f"Loaded environment variables from: {dotenv_path}") # Optional debug print
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
    """
    def __init__(self):
        # OpenAI Configuration (still primarily from .env)
        self.OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")

        # Default Agent Configuration (used if not specified in config.yaml or if file is missing)
        self.DEFAULT_AGENT_MODEL: str = os.getenv("DEFAULT_AGENT_MODEL", "gpt-3.5-turbo")
        self.DEFAULT_SYSTEM_PROMPT: str = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful assistant.")
        self.DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", 0.7))
        self.DEFAULT_PERSONA: str = os.getenv("DEFAULT_PERSONA", "General Assistant")

        # Load agent configurations from YAML file
        self.AGENT_CONFIGURATIONS: List[Dict[str, Any]] = load_agent_config()

        # Add other global settings here as needed
        # e.g., LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

        # Post-initialization check for API key
        self._check_api_key()

    def _check_api_key(self):
        """Checks if the OpenAI API key is set and prints a warning if not."""
        if not self.OPENAI_API_KEY:
            print("="*50)
            print("WARNING: OPENAI_API_KEY environment variable is not set.")
            print("Please create a .env file in the project root directory with:")
            print("OPENAI_API_KEY='your_api_key_here'")
            print("Or set the environment variable directly.")
            print("LLM functionality will be disabled until the key is provided.")
            print("="*50)

    def get_agent_config_by_id(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific agent's configuration by its ID."""
        for agent_conf in self.AGENT_CONFIGURATIONS:
            if agent_conf.get('agent_id') == agent_id:
                # Return the 'config' dictionary nested within the agent entry
                return agent_conf.get('config', {}) # Return empty dict if 'config' key is missing
        return None # Return None if agent_id is not found

# Create a singleton instance of the Settings class
# Other modules can import this instance directly: from src.config.settings import settings
settings = Settings()

# Example of accessing a specific agent's config after import:
# from src.config.settings import settings
# coder_config = settings.get_agent_config_by_id('coder')
# if coder_config:
#     model = coder_config.get('model', settings.DEFAULT_AGENT_MODEL)
