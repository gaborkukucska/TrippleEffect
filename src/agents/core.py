# START OF FILE src/agents/core.py
import asyncio
import openai # Added for type hinting, will be used properly soon
from typing import Dict, Any, List, Optional # For type hinting

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
                                                Will be properly defined later.
        """
        self.agent_id: str = agent_id
        # Basic configuration - will be expanded in configuration phase
        self.config: Dict[str, Any] = config if config else {}
        self.model: str = self.config.get("model", "gpt-3.5-turbo") # Default model placeholder
        self.system_prompt: str = self.config.get("system_prompt", "You are a helpful assistant.") # Default prompt
        self.temperature: float = self.config.get("temperature", 0.7)
        # TODO: Add other parameters like top_p, persona, etc. from config

        # Basic state management
        self.is_busy: bool = False
        self.message_history: MessageHistory = []
        if self.system_prompt:
             self.message_history.append({"role": "system", "content": self.system_prompt})

        # Placeholder for the AgentManager reference (if needed for direct communication)
        self.manager = None # Will be set by the AgentManager upon creation

        # Placeholder for OpenAI client (will be initialized properly with API key)
        self._openai_client = None

        print(f"Agent {self.agent_id} initialized with model {self.model}.")

    def set_manager(self, manager):
        """Sets a reference to the AgentManager."""
        self.manager = manager

    def initialize_openai_client(self, api_key: Optional[str]):
        """Initializes the OpenAI async client."""
        if not api_key:
            print(f"Warning: OpenAI API key not provided for Agent {self.agent_id}. LLM calls will fail.")
            self._openai_client = None
            return

        try:
            # Use AsyncOpenAI for compatibility with FastAPI/asyncio
            self._openai_client = openai.AsyncOpenAI(api_key=api_key)
            print(f"OpenAI client initialized successfully for Agent {self.agent_id}.")
        except Exception as e:
            print(f"Error initializing OpenAI client for Agent {self.agent_id}: {e}")
            self._openai_client = None


    async def process_message(self, message_content: str):
        """
        Processes an incoming message, interacts with the LLM, and streams the response.
        (Basic implementation for now)

        Args:
            message_content (str): The user's message content.

        Yields:
            str: Chunks of the LLM's response.
        """
        if self.is_busy:
            print(f"Agent {self.agent_id} is busy. Ignoring message: {message_content[:50]}...")
            yield "[Agent Busy]" # Or raise an exception/return specific status
            return

        if not self._openai_client:
            print(f"Agent {self.agent_id}: OpenAI client not initialized. Cannot process message.")
            yield "[Agent Error: OpenAI Client not configured]"
            return

        self.is_busy = True
        print(f"Agent {self.agent_id} processing message: {message_content[:100]}...")

        try:
            # Add user message to history
            self.message_history.append({"role": "user", "content": message_content})

            # --- Basic LLM Call ---
            # In the future, this will handle streaming, tool calls, etc.
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

        except openai.APIError as e:
            error_msg = f"OpenAI API Error for Agent {self.agent_id}: {e}"
            print(error_msg)
            yield f"[Agent Error: {error_msg}]"
        except Exception as e:
            error_msg = f"Error processing message in Agent {self.agent_id}: {e}"
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
            # Add other relevant state info later
        }

    def clear_history(self):
        """Clears the agent's message history, keeping the system prompt if one exists."""
        print(f"Clearing message history for Agent {self.agent_id}")
        self.message_history = []
        if self.system_prompt:
             self.message_history.append({"role": "system", "content": self.system_prompt})
