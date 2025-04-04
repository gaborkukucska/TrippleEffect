# START OF FILE src/agents/core.py
import asyncio
import openai
from typing import Dict, Any, List, Optional

# Import settings to access the API key
from src.config.settings import settings

# Placeholder for a more sophisticated message history later
MessageHistory = List[Dict[str, str]] # e.g., [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]

class Agent:
    """
    Represents an individual LLM agent capable of processing tasks,
    communicating with an LLM, and potentially using tools.
    """
    def __init__(self, agent_id: str, config: Optional[Dict[str, Any]] = None):
        """
        Initializes an Agent instance.

        Args:
            agent_id (str): A unique identifier for the agent.
            config (Optional[Dict[str, Any]]): Configuration dictionary for the agent
                                                (e.g., model, system_prompt, temperature).
                                                Defaults will be loaded from settings if not provided.
        """
        self.agent_id: str = agent_id
        # Basic configuration - Load defaults from settings if not in provided config
        self.config: Dict[str, Any] = config if config else {}
        self.model: str = self.config.get("model", settings.DEFAULT_AGENT_MODEL)
        self.system_prompt: str = self.config.get("system_prompt", settings.DEFAULT_SYSTEM_PROMPT)
        self.temperature: float = float(self.config.get("temperature", settings.DEFAULT_TEMPERATURE))
        # TODO: Add other parameters like top_p, persona, etc. from config/settings

        # Basic state management
        self.is_busy: bool = False
        self.message_history: MessageHistory = []
        if self.system_prompt:
             self.message_history.append({"role": "system", "content": self.system_prompt})

        # Placeholder for the AgentManager reference (if needed for direct communication)
        self.manager = None # Will be set by the AgentManager upon creation

        # OpenAI client - will be initialized by initialize_openai_client()
        self._openai_client: Optional[openai.AsyncOpenAI] = None

        print(f"Agent {self.agent_id} initialized with model {self.model}.")
        # Note: Client is NOT initialized here. Manager should call initialize_openai_client.

    def set_manager(self, manager):
        """Sets a reference to the AgentManager."""
        self.manager = manager

    def initialize_openai_client(self, api_key: Optional[str] = None):
        """
        Initializes the OpenAI async client.
        Uses the provided key, otherwise falls back to the key from settings.
        """
        resolved_api_key = api_key or settings.OPENAI_API_KEY

        if not resolved_api_key:
            print(f"Warning: OpenAI API key not available for Agent {self.agent_id}. LLM calls will fail.")
            self._openai_client = None
            return False # Indicate failure

        if self._openai_client:
             print(f"OpenAI client already initialized for Agent {self.agent_id}.")
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
            # Attempt to initialize now (maybe key was set after startup?)
            if not self.initialize_openai_client():
                 yield "[Agent Error: OpenAI Client not configured or initialization failed]"
                 return
            # If initialization succeeded, proceed

        self.is_busy = True
        print(f"Agent {self.agent_id} processing message: {message_content[:100]}...")

        try:
            # Add user message to history
            self.message_history.append({"role": "user", "content": message_content})

            # --- Basic LLM Call ---
            response_stream = await self._openai_client.chat.completions.create(
                model=self.model,
                messages=self.message_history,
                temperature=self.temperature,
                stream=True # Enable streaming
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
            # Maybe disable agent or client after auth error?
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
            "is_busy": self.is_busy,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "message_history_length": len(self.message_history),
            "client_initialized": self._openai_client is not None,
            # Add other relevant state info later
        }

    def clear_history(self):
        """Clears the agent's message history, keeping the system prompt if one exists."""
        print(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = []
        if self.system_prompt:
             self.message_history.append({"role": "system", "content": self.system_prompt})
