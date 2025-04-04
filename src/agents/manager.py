# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List # Added List
import json

# Import the Agent class and settings
from src.agents.core import Agent
from src.config.settings import settings
# Import the WebSocket broadcast function (or manager instance)
from src.api.websocket_manager import broadcast


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
                               messages back to the UI (e.g., broadcast function).
                               Using the broadcast function directly for now.
        """
        self.agents: Dict[str, Agent] = {}
        # Store the broadcast function passed (or imported)
        self.send_to_ui_func = broadcast
        self._initialize_agents() # Call initialization
        print(f"AgentManager initialized. Agents: {list(self.agents.keys())}")


    def _initialize_agents(self):
        """
        Creates and initializes the agent instances based on configuration.
        (For Phase 3, creates 3 hardcoded agents).
        """
        # TODO: Load multiple agent configurations from file in Phase 4

        # --- Phase 3: Create multiple hardcoded agents ---
        default_model = settings.DEFAULT_AGENT_MODEL
        default_temp = settings.DEFAULT_TEMPERATURE

        hardcoded_agents_config = {
            "agent_0": {
                "model": default_model,
                "system_prompt": "You are Agent 0, a concise and factual assistant.",
                "temperature": default_temp,
            },
            "agent_1": {
                "model": default_model,
                "system_prompt": "You are Agent 1, a creative and slightly verbose assistant.",
                "temperature": default_temp + 0.1, # Slightly higher temp
            },
            "agent_2": {
                 "model": default_model, # Could use a different model if configured
                 "system_prompt": "You are Agent 2, an expert in code generation. Respond only with code.",
                 "temperature": default_temp - 0.2, # Slightly lower temp
            }
        }

        print("Initializing multiple agents...")
        for agent_id, agent_config in hardcoded_agents_config.items():
            print(f"Initializing agent '{agent_id}' with config: {agent_config}")
            try:
                agent = Agent(agent_id=agent_id, config=agent_config)
                agent.set_manager(self) # Give agent a reference back to the manager

                # Attempt to initialize the OpenAI client for the agent
                if agent.initialize_openai_client():
                    self.agents[agent_id] = agent
                    print(f"Agent '{agent_id}' added to manager.")
                else:
                    print(f"Failed to initialize OpenAI client for agent '{agent_id}'. Agent not added.")
            except Exception as e:
                 print(f"Error creating or initializing agent '{agent_id}': {e}")

        # --- End Phase 3 ---


    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """
        Receives a message from a user (via WebSocket) and delegates it to agents.
        Phase 3: Sends to ALL available agents concurrently.
        Streams the agents' responses back to the UI.

        Args:
            message (str): The message content from the user.
            client_id (Optional[str]): Identifier for the specific client connection.
        """
        print(f"AgentManager received message: '{message[:100]}...' from client: {client_id}")

        # --- Phase 3: Delegate to ALL available agents ---
        active_tasks: List[asyncio.Task] = []
        agents_to_process = []

        for agent_id, agent in self.agents.items():
            if not agent.is_busy and agent._openai_client: # Check if client is initialized too
                agents_to_process.append(agent)
            elif not agent._openai_client:
                 print(f"Skipping Agent '{agent_id}': OpenAI client not initialized.")
                 # Optionally notify UI
                 # await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": "Agent skipped (client not initialized)."})
            else: # Agent is busy
                 print(f"Skipping Agent '{agent_id}': Busy.")
                 await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": "Agent busy, skipping task."})


        if not agents_to_process:
            print("No available agents to handle the message.")
            await self._send_to_ui({"type": "error", "content": "All agents are busy or unavailable."})
            return

        # Create a processing task for each available agent
        for agent in agents_to_process:
            task = asyncio.create_task(self._process_message_for_agent(agent, message))
            active_tasks.append(task)

        # Wait for all tasks to complete (or handle them as they complete)
        if active_tasks:
            print(f"Delegated message to {len(active_tasks)} agents: {[a.agent_id for a in agents_to_process]}")
            await asyncio.gather(*active_tasks) # Wait for all agent processing to finish
            print("All agent processing tasks complete.")
        else:
             print("No tasks were created.")


    async def _process_message_for_agent(self, agent: Agent, message: str):
        """Helper coroutine to process message for a single agent and stream results."""
        await self._send_to_ui({"type": "status", "agent_id": agent.agent_id, "content": f"Agent {agent.agent_id} processing..."})
        try:
            async for chunk in agent.process_message(message):
                # Send each chunk to the UI as it arrives
                await self._send_to_ui({"type": "agent_response", "agent_id": agent.agent_id, "content": chunk})
            await self._send_to_ui({"type": "status", "agent_id": agent.agent_id, "content": f"Agent {agent.agent_id} finished."})
        except Exception as e:
            error_msg = f"Error during processing for agent {agent.agent_id}: {e}"
            print(error_msg)
            await self._send_to_ui({"type": "error", "agent_id": agent.agent_id, "content": error_msg})


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
            await self.send_to_ui_func(message_json)
        except Exception as e:
            print(f"Error sending message to UI via broadcast function: {e}")


    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """Returns the status of all managed agents."""
        return {agent_id: agent.get_state() for agent_id, agent in self.agents.items()}

    # TODO: Add methods for agent configuration updates, tool management etc. later
