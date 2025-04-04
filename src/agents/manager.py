# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional
import json # Import json for sending structured messages

# Import the Agent class and settings
from src.agents.core import Agent
from src.config.settings import settings
# Import the WebSocket manager for sending messages back to UI (using dependency injection)
# We define a base class or use typing.Callable for flexibility later,
# but for now, let's assume we pass the websocket manager instance.
from src.api.websocket_manager import broadcast # Or pass manager instance


class AgentManager:
    """
    Manages the lifecycle and task distribution for multiple agents.
    Coordinates communication between agents and the user interface.
    """
    def __init__(self, websocket_manager: Optional[Any] = None):
        """
        Initializes the AgentManager.

        Args:
            websocket_manager: An instance or callable capable of sending
                               messages back to the UI (e.g., broadcast function
                               or the websocket manager instance itself).
                               This uses dependency injection.
        """
        self.agents: Dict[str, Agent] = {}
        # Option 1: Pass the broadcast function directly
        self.send_to_ui_func = broadcast # Assign the function
        # Option 2: Pass the manager instance (less direct, more coupled)
        # self.websocket_manager_instance = websocket_manager

        self._initialize_agents()
        print(f"AgentManager initialized. Agents: {list(self.agents.keys())}")


    def _initialize_agents(self):
        """
        Creates and initializes the agent instances based on configuration.
        (For Phase 2, creates a single default agent).
        """
        # In future phases, this will load configuration for multiple agents
        # from a file (e.g., config.yaml)

        # --- Phase 2: Create a single default agent ---
        agent_id = "agent_0"
        # Use default settings for now, config file loading comes later
        agent_config = {
            "model": settings.DEFAULT_AGENT_MODEL,
            "system_prompt": settings.DEFAULT_SYSTEM_PROMPT,
            "temperature": settings.DEFAULT_TEMPERATURE
        }
        print(f"Initializing agent '{agent_id}' with config: {agent_config}")
        agent = Agent(agent_id=agent_id, config=agent_config)
        agent.set_manager(self) # Give agent a reference back to the manager

        # Attempt to initialize the OpenAI client for the agent
        if agent.initialize_openai_client():
             self.agents[agent_id] = agent
             print(f"Agent '{agent_id}' added to manager.")
        else:
             print(f"Failed to initialize OpenAI client for agent '{agent_id}'. Agent not added.")
        # --- End Phase 2 ---

        # TODO: Load multiple agent configurations in Phase 3/4

    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """
        Receives a message from a user (via WebSocket) and delegates it to an agent.
        Streams the agent's response back to the UI.

        Args:
            message (str): The message content from the user.
            client_id (Optional[str]): Identifier for the specific client connection
                                       (might be useful later for targeted responses).
        """
        print(f"AgentManager received message: '{message[:100]}...' from client: {client_id}")

        # --- Phase 2: Delegate to the single agent ---
        target_agent_id = "agent_0" # Hardcoded for now
        agent = self.agents.get(target_agent_id)

        if not agent:
            print(f"Error: Agent '{target_agent_id}' not found or not initialized.")
            await self._send_to_ui({"type": "error", "agent_id": target_agent_id, "content": f"Agent '{target_agent_id}' is not available."})
            return

        if agent.is_busy:
             print(f"Agent '{target_agent_id}' is busy. Task rejected.")
             await self._send_to_ui({"type": "status", "agent_id": target_agent_id, "content": f"Agent '{target_agent_id}' is currently busy."})
             return

        # Stream the response back to the UI
        await self._send_to_ui({"type": "status", "agent_id": agent.agent_id, "content": "Processing request..."})
        try:
            async for chunk in agent.process_message(message):
                # Send each chunk to the UI as it arrives
                # Adding agent_id to identify the source in multi-agent setups
                await self._send_to_ui({"type": "agent_response", "agent_id": agent.agent_id, "content": chunk})

            # Optionally send a completion status message
            await self._send_to_ui({"type": "status", "agent_id": agent.agent_id, "content": "Processing complete."})

        except Exception as e:
            print(f"Error during agent processing or streaming for agent {agent.agent_id}: {e}")
            await self._send_to_ui({"type": "error", "agent_id": agent.agent_id, "content": f"An internal error occurred: {e}"})
        # --- End Phase 2 ---

        # TODO: Implement more sophisticated task distribution in Phase 3

    async def _send_to_ui(self, message_data: Dict[str, Any]):
        """
        Sends a structured message back to the UI via the injected broadcast function.

        Args:
            message_data (Dict[str, Any]): The data payload to send (should be JSON-serializable).
        """
        if not self.send_to_ui_func:
            print("Warning: UI broadcast function not configured in AgentManager. Cannot send message to UI.")
            return

        try:
            message_json = json.dumps(message_data) # Ensure it's JSON string
            # Call the injected broadcast function directly
            await self.send_to_ui_func(message_json)
        except Exception as e:
            print(f"Error sending message to UI via broadcast function: {e}")

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """Returns the status of all managed agents."""
        return {agent_id: agent.get_state() for agent_id, agent in self.agents.items()}

    # TODO: Add methods for agent configuration updates, tool management etc. later
