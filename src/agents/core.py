# START OF FILE src/agents/core.py
import asyncio
import openai
from typing import Dict, Any, List, Optional
import os # Import os for path operations
from pathlib import Path # Import Path for object-oriented paths

# Import settings to access the API key and defaults
from src.config.settings import settings, BASE_DIR # Import BASE_DIR too

# Placeholder for a more sophisticated message history later
MessageHistory = List[Dict[str, str]] # e.g., [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]

class Agent:
    """
    Represents an individual LLM agent capable of processing tasks,
    communicating with an LLM, managing its sandbox, and potentially using tools.
    """
    def __init__(self, agent_config: Dict[str, Any]):
        """
        Initializes an Agent instance using a configuration dictionary.

        Args:
            agent_config (Dict[str, Any]): The configuration dictionary for this agent,
                                           typically loaded from config.yaml via settings.
                                           Expected structure: {'agent_id': '...', 'config': {'model': ..., 'system_prompt': ...}}
        """
        self.agent_id: str = agent_config.get("agent_id", f"unknown_agent_{os.urandom(4).hex()}")
        config: Dict[str, Any] = agent_config.get("config", {}) # Get the nested config dict

        # Load agent parameters from config, falling back to defaults from settings
        self.model: str = config.get("model", settings.DEFAULT_AGENT_MODEL)
        self.system_prompt: str = config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        self.temperature: float = float(config.get("temperature", settings.DEFAULT_TEMPERATURE))
        self.persona: str = config.get("persona", settings.DEFAULT_PERSONA) # Added persona

        # Basic state management
        self.is_busy: bool = False
        self.message_history: MessageHistory = []
        if self.system_prompt:
             self.message_history.append({"role": "system", "content": self.system_prompt})

        # Placeholder for the AgentManager reference (if needed for direct communication)
        self.manager = None # Will be set by the AgentManager upon creation

        # OpenAI client - will be initialized by initialize_openai_client()
        self._openai_client: Optional[openai.AsyncOpenAI] = None

        # --- Sandboxing ---
        # Define the path for the agent's sandbox directory
        self.sandbox_path: Path = BASE_DIR / "sandboxes" / f"agent_{self.agent_id}"
        # Note: Directory creation is handled by the AgentManager after __init__

        print(f"Agent {self.agent_id} ({self.persona}) initialized with model {self.model}. Sandbox: {self.sandbox_path}")
        # Note: Client is NOT initialized here. Manager should call initialize_openai_client.

    def set_manager(self, manager):
        """Sets a reference to the AgentManager."""
        self.manager = manager

    def initialize_openai_client(self, api_key: Optional[str] = None) -> bool:
        """
        Initializes the OpenAI async client.
        Uses the provided key, otherwise falls back to the key from settings.
        Returns True on success, False on failure.
        """
        resolved_api_key = api_key or settings.OPENAI_API_KEY

        if not resolved_api_key:
            print(f"Warning: OpenAI API key not available for Agent {self.agent_id}. LLM calls will fail.")
            self._openai_client = None
            return False # Indicate failure

        if self._openai_client:
             # print(f"OpenAI client already initialized for Agent {self.agent_id}.") # Less verbose
             return True # Indicate success (already initialized)

        try:
            # Use AsyncOpenAI for compatibility with FastAPI/asyncio
            self._openai_client = openai.AsyncOpenAI(api_key=resolved_api_key)
            print(f"OpenAI client initialized successfully for Agent {self.agent_id}.")
            return True # Indicate success
        except Exception as e:
            print(f"Error initializing OpenAI client for Agent {self.agent_id}: {e}")
            self._openai_client = None
            return False # Indicate failure

    def ensure_sandbox_exists(self) -> bool:
        """
        Creates the agent's sandbox directory if it doesn't exist.
        Returns True if the directory exists or was created, False otherwise.
        """
        try:
            self.sandbox_path.mkdir(parents=True, exist_ok=True)
            # print(f"Sandbox directory ensured for Agent {self.agent_id} at {self.sandbox_path}") # Less verbose
            return True
        except OSError as e:
            print(f"Error creating sandbox directory for Agent {self.agent_id} at {self.sandbox_path}: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error ensuring sandbox for Agent {self.agent_id}: {e}")
            return False

    async def process_message(self, message_content: str):
        """
        Processes an incoming message, interacts with the LLM, and streams the response.

        Args:
            message_content (str): The user's message content.

        Yields:
            str: Chunks of the LLM's response or error messages.
        """
        if self.is_busy:
            print(f"Agent {self.agent_id} is busy. Ignoring message: {message_content[:50]}...")
            yield "[Agent Busy]" # Or raise an exception/return specific status
            return

        if not self._openai_client:
            print(f"Agent {self.agent_id}: OpenAI client not initialized. Cannot process message.")
            if not self.initialize_openai_client():
                 yield "[Agent Error: OpenAI Client not configured or initialization failed]"
                 return

        self.is_busy = True
        print(f"Agent {self.agent_id} processing message: {message_content[:100]}...")

        try:
            # Ensure sandbox exists before processing (relevant for future file tools)
            if not self.ensure_sandbox_exists():
                 yield f"[Agent Error: Could not ensure sandbox directory {self.sandbox_path}]"
                 # Optionally stop processing if sandbox is critical? For now, continue.

            # Add user message to history
            self.message_history.append({"role": "user", "content": message_content})

            # --- Basic LLM Call ---
            response_stream = await self._openai_client.chat.completions.create(
                model=self.model,
                messages=self.message_history,
                temperature=self.temperature,
                stream=True # Enable streaming
                # TODO: Add other parameters like top_p from config if needed
            )

            assistant_response = ""
            async for chunk in response_stream:
                content = chunk.choices[0].delta.content
                if content is not None:
                    assistant_response += content
                    yield content # Yield each chunk as it arrives

            # Add full assistant response to history once streaming is complete
            if assistant_response:
                 self.message_history.append({"role": "assistant", "content": assistant_response})

            # --- End Basic LLM Call ---

        except openai.APIAuthenticationError as e:
            error_msg = f"OpenAI Authentication Error for Agent {self.agent_id}: Check your API key ({e})"
            print(error_msg)
            yield f"[Agent Error: {error_msg}]"
            self._openai_client = None # Invalidate client on auth error
        except openai.APIError as e:
            error_msg = f"OpenAI API Error for Agent {self.agent_id}: {e}"
            print(error_msg)
            yield f"[Agent Error: {error_msg}]"
        except Exception as e:
            error_msg = f"Error processing message in Agent {self.agent_id}: {type(e).__name__} - {e}"
            print(error_msg)
            yield f"[Agent Error: {error_msg}]"
        finally:
            self.is_busy = False
            print(f"Agent {self.agent_id} finished processing.")

    def get_state(self) -> Dict[str, Any]:
        """Returns the current state of the agent."""
        return {
            "agent_id": self.agent_id,
            "persona": self.persona,
            "is_busy": self.is_busy,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "message_history_length": len(self.message_history),
            "client_initialized": self._openai_client is not None,
            "sandbox_path": str(self.sandbox_path), # Convert Path to string for JSON serialization
            # Add other relevant state info later
        }

    def clear_history(self):
        """Clears the agent's message history, keeping the system prompt if one exists."""
        print(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = []
        if self.system_prompt:
             self.message_history.append({"role": "system", "content": self.system_prompt})
