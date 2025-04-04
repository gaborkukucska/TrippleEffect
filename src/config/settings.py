# START OF FILE src/config/settings.py
import os
from dotenv import load_dotenv
from pathlib import Path

# Define base directory relative to this file's location if needed
# BASE_DIR = Path(__file__).resolve().parent.parent.parent # This should point to TrippleEffect-main/

# Explicitly load .env file from the project root directory
# This ensures settings are loaded even if the script is run from a different directory
# Assumes .env file is in the parent directory of the 'src' directory
dotenv_path = Path(__file__).resolve().parent.parent.parent / '.env'
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)
    # print(f"Loaded environment variables from: {dotenv_path}") # Optional debug print
else:
    print(f"Warning: .env file not found at {dotenv_path}. Environment variables might not be loaded.")

class Settings:
    """
    Holds application settings, primarily loaded from environment variables.
    """
    # OpenAI Configuration
    OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")

    # Default Agent Configuration (can be overridden later by specific agent configs)
    DEFAULT_AGENT_MODEL: str = os.getenv("DEFAULT_AGENT_MODEL", "gpt-3.5-turbo")
    DEFAULT_SYSTEM_PROMPT: str = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful assistant.")
    DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", 0.7))

    # Add other global settings here as needed
    # e.g., LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Create a singleton instance of the Settings class
# Other modules can import this instance directly: from src.config.settings import settings
settings = Settings()

# Optional: Add a check for essential settings like the API key
if not settings.OPENAI_API_KEY:
    print("="*50)
    print("WARNING: OPENAI_API_KEY environment variable is not set.")
    print("Please create a .env file in the project root directory with:")
    print("OPENAI_API_KEY='your_api_key_here'")
    print("LLM functionality will be disabled until the key is provided.")
    print("="*50)
