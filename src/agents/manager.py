# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List
import json
import os # Import os

# Import the Agent class and settings
from src.agents.core import Agent
from src.config.settings import settings # Import the settings instance
# Import the WebSocket broadcast function (or manager instance)
from src.api.websocket_manager import broadcast


class AgentManager:
    """
    Manages the lifecycle and task distribution for multiple agents.
    Coordinates communication between agents and the user interface.
    Initializes agents based on configurations found in settings.
    Ensures agent sandboxes are created.
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
        print(f"AgentManager initialized. Managed agents: {list(self.agents.keys())}")
        if not self.agents:
             print("Warning: AgentManager initialized with zero active agents. Check configuration and API keys.")


    def _initialize_agents(self):
        """
        Creates and initializes agent instances based on configurations loaded
        from `settings.AGENT_CONFIGURATIONS`. Ensures sandbox creation for each agent.
        """
        print("Initializing agents from configuration...")

        agent_configs = settings.AGENT_CONFIGURATIONS
        if not agent_configs:
            print("No agent configurations found in settings. No agents will be created.")
            return

        print(f"Found {len(agent_configs)} agent configuration(s). Attempting to initialize...")

        # Ensure the main 'sandboxes' directory exists
        main_sandbox_dir = settings.BASE_DIR / "sandboxes"
        try:
            main_sandbox_dir.mkdir(parents=True, exist_ok=True)
            print(f"Ensured main sandbox directory exists at: {main_sandbox_dir}")
        except Exception as e:
            print(f"Error creating main sandbox directory at {main_sandbox_dir}: {e}. Agent sandbox creation might fail.")
            # Decide if this is a fatal error? For now, continue and let individual agent sandboxes try.


        successful_initializations = 0
        for agent_conf in agent_configs:
            agent_id = agent_conf.get("agent_id")
            if not agent_id:
                print("Skipping agent configuration due to missing 'agent_id'.")
                continue

            print(f"--- Initializing agent '{agent_id}' ---")
            try:
                # 1. Create Agent instance
                agent = Agent(agent_config=agent_conf)
                print(f"  Instance created for agent '{agent_id}'.")

                # 2. Set manager reference
                agent.set_manager(self)

                # 3. Ensure sandbox directory exists
                if agent.ensure_sandbox_exists():
                    print(f"  Sandbox ensured for agent '{agent_id}'.")
                else:
                    # Log error but potentially continue depending on requirements
                    print(f"  Warning: Failed to ensure sandbox for agent '{agent_id}'. File operations might fail.")

                # 4. Initialize OpenAI client (requires API key from settings)
                if agent.initialize_openai_client():
                    print(f"  OpenAI client initialized for agent '{agent_id}'.")
                    # 5. Add successfully initialized agent to the manager
                    self.agents[agent_id] = agent
                    successful_initializations += 1
                    print(f"--- Agent '{agent_id}' successfully initialized and added. ---")
                else:
                    print(f"  Failed to initialize OpenAI client for agent '{agent_id}'. Agent will not be added.")
                    # Clean up? (e.g., remove sandbox?) - For now, leave sandbox as is.
                    print(f"--- Agent '{agent_id}' initialization failed. ---")

            except Exception as e:
                 print(f"Error creating or initializing agent from config '{agent_id}': {e}")
                 print(f"--- Agent '{agent_id}' initialization failed due to exception. ---")

        print(f"Finished agent initialization. Successfully initialized {successful_initializations}/{len(agent_configs)} agents.")


    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """
        Receives a message from a user (via WebSocket) and delegates it to agents.
        Sends to ALL available agents concurrently.
        Streams the agents' responses back to the UI.

        Args:
            message (str): The message content from the user.
            client_id (Optional[str]): Identifier for the specific client connection.
        """
        print(f"AgentManager received message: '{message[:100]}...' from client: {client_id}")

        active_tasks: List[asyncio.Task] = []
        agents_to_process = []

        # Check which managed agents are ready
        if not self.agents:
             print("No agents available in the manager.")
             await self._send_to_ui({"type": "error", "content": "No agents configured or initialized."})
             return

        for agent_id, agent in self.agents.items():
            if not agent.is_busy and agent._openai_client: # Check if client is initialized too
                agents_to_process.append(agent)
            elif not agent._openai_client:
                 print(f"Skipping Agent '{agent_id}': OpenAI client not initialized.")
                 await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Agent '{agent_id}' skipped (OpenAI client unavailable)." , "detail": "Check API key or configuration."})
            else: # Agent is busy
                 print(f"Skipping Agent '{agent_id}': Busy.")
                 await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Agent '{agent_id}' is busy."})


        if not agents_to_process:
            print("No available agents to handle the message at this time.")
            await self._send_to_ui({"type": "error", "content": "All active agents are currently busy."})
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
             print("No tasks were created (this shouldn't normally happen if agents_to_process was not empty).")


    async def _process_message_for_agent(self, agent: Agent, message: str):
        """Helper coroutine to process message for a single agent and stream results."""
        await self._send_to_ui({"type": "status", "agent_id": agent.agent_id, "content": f"Agent '{agent.agent_id}' ({agent.persona}) processing..."})
        try:
            async for chunk in agent.process_message(message):
                # Check for internal error messages from the agent itself
                if isinstance(chunk, str) and chunk.startswith("[Agent Error:"):
                     await self._send_to_ui({"type": "error", "agent_id": agent.agent_id, "content": chunk})
                elif isinstance(chunk, str) and chunk == "[Agent Busy]":
                     # This case should ideally be caught before calling process_message,
                     # but handle it defensively here too.
                     await self._send_to_ui({"type": "status", "agent_id": agent.agent_id, "content": f"Agent '{agent.agent_id}' reported busy."})
                else:
                     # Send normal content chunk to the UI
                     await self._send_to_ui({"type": "agent_response", "agent_id": agent.agent_id, "content": chunk})

            # Send a final status update once the stream is finished (unless an error occurred)
            await self._send_to_ui({"type": "status", "agent_id": agent.agent_id, "content": f"Agent '{agent.agent_id}' finished."})

        except Exception as e:
            # Catch errors that might occur within this wrapper coroutine itself
            error_msg = f"Error during task execution for agent {agent.agent_id}: {type(e).__name__} - {e}"
            print(error_msg)
            await self._send_to_ui({"type": "error", "agent_id": agent.agent_id, "content": error_msg})
            # Ensure agent is marked not busy if an unexpected error stopped processing prematurely
            if agent.is_busy:
                agent.is_busy = False


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
            # Ensure agent_id is present if possible, helps UI attribute messages
            if "agent_id" not in message_data:
                 message_data["agent_id"] = "system" # Or None, depending on UI handling

            message_json = json.dumps(message_data) # Ensure it's JSON string
            await self.send_to_ui_func(message_json)
        except TypeError as e:
             print(f"Error serializing message data to JSON before sending to UI: {e}")
             print(f"Data was: {message_data}")
        except Exception as e:
            print(f"Error sending message to UI via broadcast function: {e}")


    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """Returns the status of all managed agents."""
        return {agent_id: agent.get_state() for agent_id, agent in self.agents.items()}

    # TODO: Add methods for agent configuration updates, tool management etc. later
