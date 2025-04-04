# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator # Added AsyncGenerator
import json
import os # Import os

# Import the Agent class and settings
from src.agents.core import Agent
from src.config.settings import settings # Import the settings instance

# Import the WebSocket broadcast function (or manager instance)
from src.api.websocket_manager import broadcast

# Import ToolExecutor
from src.tools.executor import ToolExecutor


class AgentManager:
    """
    Manages the lifecycle and task distribution for multiple agents.
    Coordinates communication between agents and the user interface, including tool usage.
    Initializes agents based on configurations found in settings.
    Ensures agent sandboxes are created.
    Instantiates and provides the ToolExecutor to agents.
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

        # Instantiate the Tool Executor
        print("Instantiating ToolExecutor...")
        self.tool_executor = ToolExecutor()
        print("ToolExecutor instantiated.")

        # Initialize agents, injecting the tool executor
        self._initialize_agents() # Call initialization
        print(f"AgentManager initialized. Managed agents: {list(self.agents.keys())}")
        if not self.agents:
             print("Warning: AgentManager initialized with zero active agents. Check configuration and API keys.")


    def _initialize_agents(self):
        """
        Creates and initializes agent instances based on configurations loaded
        from `settings.AGENT_CONFIGURATIONS`. Ensures sandbox creation and injects
        the ToolExecutor instance. Updates agent system prompts with tool info.
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

                # 3. Inject ToolExecutor reference
                agent.set_tool_executor(self.tool_executor)
                print(f"  ToolExecutor injected into agent '{agent_id}'.")

                # 4. Update system prompt with tool descriptions (now that executor is set)
                # agent.update_system_prompt_with_tools() # Let Agent handle this internally if needed, less coupled
                # print(f"  System prompt potentially updated with tool info for agent '{agent_id}'.")

                # 5. Ensure sandbox directory exists
                if agent.ensure_sandbox_exists():
                    print(f"  Sandbox ensured for agent '{agent_id}'.")
                else:
                    print(f"  Warning: Failed to ensure sandbox for agent '{agent_id}'. File operations might fail.")

                # 6. Initialize OpenAI client (requires API key from settings)
                if agent.initialize_openai_client():
                    print(f"  OpenAI client initialized for agent '{agent_id}'.")
                    # 7. Add successfully initialized agent to the manager
                    self.agents[agent_id] = agent
                    successful_initializations += 1
                    print(f"--- Agent '{agent_id}' successfully initialized and added. ---")
                else:
                    print(f"  Failed to initialize OpenAI client for agent '{agent_id}'. Agent will not be added.")
                    print(f"--- Agent '{agent_id}' initialization failed. ---")

            except Exception as e:
                 print(f"Error creating or initializing agent from config '{agent_id}': {e}")
                 import traceback
                 traceback.print_exc() # Print stack trace for init errors
                 print(f"--- Agent '{agent_id}' initialization failed due to exception. ---")

        print(f"Finished agent initialization. Successfully initialized {successful_initializations}/{len(agent_configs)} agents.")


    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """
        Receives a message from a user (via WebSocket) and delegates it to agents.
        Sends to ALL available agents concurrently.
        Manages the async generator interaction for each agent, including tool calls.

        Args:
            message (str): The message content from the user.
            client_id (Optional[str]): Identifier for the specific client connection.
        """
        print(f"AgentManager received message: '{message[:100]}...' from client: {client_id}")

        active_tasks: List[asyncio.Task] = []
        agents_to_process = []

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

        # Create a processing task for each available agent using the generator handler
        for agent in agents_to_process:
            # Use the new generator handling coroutine
            task = asyncio.create_task(self._handle_agent_generator(agent, message))
            active_tasks.append(task)

        if active_tasks:
            print(f"Delegated message to {len(active_tasks)} agents: {[a.agent_id for a in agents_to_process]}")
            await asyncio.gather(*active_tasks) # Wait for all agent processing cycles to finish
            print("All agent processing tasks complete.")
        else:
             print("No tasks were created.")


    async def _handle_agent_generator(self, agent: Agent, message: str):
        """
        Handles the async generator interaction for a single agent's processing cycle.
        Listens for yielded events, executes tools, and sends results back.
        """
        agent_id = agent.agent_id
        print(f"Starting generator handling for Agent '{agent_id}'...")
        generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[Dict[str, Any]]]]] = None

        try:
            # Get the generator from the agent
            generator = agent.process_message(message)

            # Iterate through the generator's yielded events
            async for event in generator:
                event_type = event.get("type")

                if event_type == "response_chunk":
                    await self._send_to_ui({"type": "agent_response", "agent_id": agent_id, "content": event.get("content", "")})

                elif event_type == "tool_requests":
                    await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Agent '{agent_id}' requested tool use..."})
                    tool_calls_requested = event.get("calls", [])
                    if not tool_calls_requested:
                        print(f"Agent '{agent_id}' yielded 'tool_requests' but no calls found.")
                        continue # Or send None back?

                    # Execute tools concurrently
                    tool_tasks = []
                    for call in tool_calls_requested:
                         call_id = call.get("id")
                         tool_name = call.get("name")
                         tool_args = call.get("arguments", {})
                         if call_id and tool_name:
                              print(f"  Creating task for Tool: {tool_name}, Call ID: {call_id}, Args: {tool_args}")
                              task = asyncio.create_task(
                                   self._execute_single_tool(agent, call_id, tool_name, tool_args)
                              )
                              tool_tasks.append(task)
                         else:
                              print(f"  Skipping invalid tool request format: {call}")
                              # Add a placeholder result indicating failure?
                              # Or just rely on agent history not getting the result?

                    # Wait for all tool executions for this turn
                    tool_results = []
                    if tool_tasks:
                         results_from_gather = await asyncio.gather(*tool_tasks)
                         # Filter out potential None results if _execute_single_tool handles errors that way
                         tool_results = [res for res in results_from_gather if res is not None]

                    # Send results back to the agent's generator
                    print(f"  Sending {len(tool_results)} result(s) back to Agent '{agent_id}' generator...")
                    try:
                         # Use asend() for async generators
                         await generator.asend(tool_results)
                    except StopAsyncIteration:
                        # Generator finished after receiving results, expected behavior
                        print(f"Agent '{agent_id}' generator finished after receiving tool results.")
                        break # Exit the async for loop
                    except Exception as send_err:
                        print(f"Error sending tool results back to Agent '{agent_id}': {send_err}")
                        # Agent might be in a bad state, stop processing for this agent
                        await self._send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Failed to send tool results back to agent: {send_err}]"})
                        break # Exit the async for loop

                elif event_type == "final_response":
                    # Agent indicated the final text response (already streamed via chunks)
                    print(f"Agent '{agent_id}' yielded final response marker.")
                    # Optionally send a concluding status message
                    await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Agent '{agent_id}' finished."})
                    # Generator should finish after this, but break just in case
                    break

                elif event_type == "error":
                    error_content = event.get("content", "Unknown agent error")
                    print(f"Agent '{agent_id}' yielded error: {error_content}")
                    await self._send_to_ui({"type": "error", "agent_id": agent_id, "content": error_content})
                    # Should we break the loop on agent error? Yes, probably.
                    break

                elif event_type == "status":
                    status_content = event.get("content", "Agent status update")
                    print(f"Agent '{agent_id}' yielded status: {status_content}")
                    await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": status_content})

                else:
                    print(f"Agent '{agent_id}' yielded unknown event type: {event_type}")
                    # Ignore or log? For now, just log.

        except StopAsyncIteration:
            # Generator finished normally (e.g., after yielding final_response or breaking)
             print(f"Agent '{agent_id}' generator finished.")
             # Ensure a final status is sent if not already done
             await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Agent '{agent_id}' finished processing."})

        except Exception as e:
            # Catch errors during the generator handling itself
            error_msg = f"Error during generator handling for agent {agent_id}: {type(e).__name__} - {e}"
            print(error_msg)
            import traceback
            traceback.print_exc() # Print full traceback for manager-level errors
            await self._send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: {error_msg}]"})
        finally:
             # Ensure agent is marked not busy, even if errors occurred
             if agent.is_busy:
                 agent.is_busy = False
                 print(f"Agent '{agent_id}' marked as not busy.")
             # Clean up generator reference? Should close automatically.
             # if generator: await generator.aclose() # Consider explicit closing?

    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        Executes a single tool call and formats the result for the agent's generator.

        Args:
            agent: The agent instance making the call.
            call_id: The unique ID for this specific tool call from the LLM.
            tool_name: The name of the tool to execute.
            tool_args: The arguments for the tool.

        Returns:
            A dictionary {'call_id': str, 'content': str} containing the result or error,
            or None if the call couldn't be processed.
        """
        try:
            # Notify UI that a tool is being executed
            await self._send_to_ui({
                "type": "status",
                "agent_id": agent.agent_id,
                "content": f"Executing tool: `{tool_name}` (Call ID: {call_id})"
                #"detail": f"Arguments: {json.dumps(tool_args)}" # Maybe too verbose for UI
            })

            result_content = await self.tool_executor.execute_tool(
                agent_id=agent.agent_id,
                agent_sandbox_path=agent.sandbox_path,
                tool_name=tool_name,
                tool_args=tool_args
            )
            # Tool execution result (string) or error message (string)
            return {"call_id": call_id, "content": result_content}

        except Exception as e:
            error_msg = f"Unexpected error executing tool '{tool_name}' via manager: {type(e).__name__} - {e}"
            print(error_msg)
            # Return the error message as the content for this call_id
            return {"call_id": call_id, "content": f"[Tool Execution Error: {error_msg}]"}


    async def _send_to_ui(self, message_data: Dict[str, Any]):
        """
        Sends a structured message back to the UI via the injected broadcast function.
        """
        if not self.send_to_ui_func:
            print("Warning: UI broadcast function not configured in AgentManager. Cannot send message to UI.")
            return

        try:
            # Ensure agent_id is present if possible
            if "agent_id" not in message_data:
                 message_data["agent_id"] = "system"

            message_json = json.dumps(message_data)
            await self.send_to_ui_func(message_json)
        except TypeError as e:
             print(f"Error serializing message data to JSON before sending to UI: {e}")
             print(f"Data was: {message_data}")
        except Exception as e:
            print(f"Error sending message to UI via broadcast function: {e}")


    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """Returns the status of all managed agents."""
        return {agent_id: agent.get_state() for agent_id, agent in self.agents.items()}

    # Placeholder for old _process_message_for_agent (now unused)
    # async def _process_message_for_agent(self, agent: Agent, message: str): ...
