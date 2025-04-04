# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator
import json
import os
import traceback # Import traceback

# Import the Agent class and settings
# Import AGENT_STATUS constants
from src.agents.core import Agent, AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL, AGENT_STATUS_ERROR
from src.config.settings import settings # Import the settings instance

# Import the WebSocket broadcast function
from src.api.websocket_manager import broadcast

# Import ToolExecutor
from src.tools.executor import ToolExecutor

# Import Provider classes and Base class
from src.llm_providers.base import BaseLLMProvider, ToolResultDict
from src.llm_providers.openai_provider import OpenAIProvider
from src.llm_providers.ollama_provider import OllamaProvider
from src.llm_providers.openrouter_provider import OpenRouterProvider

# Mapping from provider name string to provider class
PROVIDER_CLASS_MAP: Dict[str, type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "openrouter": OpenRouterProvider,
    # Add other providers here as they are implemented
}


class AgentManager:
    """
    Manages the lifecycle and task distribution for multiple agents.
    Instantiates appropriate LLM providers and agents based on configuration.
    Coordinates communication between agents and the UI, including tool usage and status updates.
    """
    def __init__(self, websocket_manager: Optional[Any] = None):
        """
        Initializes the AgentManager. Instantiates ToolExecutor and Agents.
        """
        self.agents: Dict[str, Agent] = {}
        self.send_to_ui_func = broadcast # Use imported broadcast function

        print("Instantiating ToolExecutor...")
        self.tool_executor = ToolExecutor()
        print("ToolExecutor instantiated.")

        # Initialize agents (this will now also handle provider instantiation)
        self._initialize_agents()
        print(f"AgentManager initialized. Managed agents: {list(self.agents.keys())}")
        if not self.agents:
             print("Warning: AgentManager initialized with zero active agents. Check configuration and API keys/URLs.")


    def _initialize_agents(self):
        """
        Creates and initializes agent instances based on configurations loaded
        from `settings.AGENT_CONFIGURATIONS`. Instantiates the correct LLM provider
        for each agent, ensures sandbox creation, and injects dependencies.
        """
        print("Initializing agents from configuration...")

        agent_configs_list = settings.AGENT_CONFIGURATIONS
        if not agent_configs_list:
            print("No agent configurations found in settings. No agents will be created.")
            return

        print(f"Found {len(agent_configs_list)} agent configuration(s). Attempting to initialize...")

        # Ensure the main 'sandboxes' directory exists
        main_sandbox_dir = settings.BASE_DIR / "sandboxes"
        try:
            main_sandbox_dir.mkdir(parents=True, exist_ok=True)
            print(f"Ensured main sandbox directory exists at: {main_sandbox_dir}")
        except Exception as e:
            print(f"Error creating main sandbox directory at {main_sandbox_dir}: {e}. Agent sandbox creation might fail.")

        successful_initializations = 0
        for agent_conf_entry in agent_configs_list:
            agent_id = agent_conf_entry.get("agent_id")
            if not agent_id:
                print("Skipping agent configuration due to missing 'agent_id'.")
                continue

            agent_config_dict = agent_conf_entry.get("config", {})
            provider_name = agent_config_dict.get("provider", settings.DEFAULT_AGENT_PROVIDER)

            print(f"--- Initializing agent '{agent_id}' (Provider: {provider_name}) ---")
            try:
                # 1. Select Provider Class
                ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
                if not ProviderClass:
                    print(f"  Error: Unknown provider '{provider_name}' specified for agent '{agent_id}'. Skipping.")
                    continue

                # 2. Get Base Provider Config (API Key, URL from .env)
                base_provider_config = settings.get_provider_config(provider_name)

                # 3. Get Agent-Specific Overrides & Kwargs from config.yaml
                agent_api_key = agent_config_dict.get("api_key")
                agent_base_url = agent_config_dict.get("base_url")
                agent_provider_kwargs = {
                     k: v for k, v in agent_config_dict.items()
                     if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url']
                }
                if agent_config_dict.get("referer"):
                    agent_provider_kwargs["referer"] = agent_config_dict["referer"]


                # 4. Determine Final Provider Init Args
                final_provider_args = {
                    **base_provider_config,
                    **agent_provider_kwargs,
                    "api_key": agent_api_key if agent_api_key is not None else base_provider_config.get('api_key'),
                    "base_url": agent_base_url if agent_base_url is not None else base_provider_config.get('base_url'),
                }
                final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

                print(f"  Instantiating provider {ProviderClass.__name__} with args: { {k: (v[:5]+'...' if k=='api_key' and isinstance(v,str) else v) for k, v in final_provider_args.items()} }") # Mask API key

                # 5. Instantiate Provider
                llm_provider_instance = ProviderClass(**final_provider_args)
                print(f"  Provider instance created: {llm_provider_instance}")

                # 6. Instantiate Agent, injecting provider and other dependencies
                agent = Agent(
                    agent_config=agent_conf_entry,
                    llm_provider=llm_provider_instance,
                    tool_executor=self.tool_executor,
                    manager=self
                )
                print(f"  Agent instance created for '{agent_id}'.")

                # 7. Ensure sandbox directory exists
                if agent.ensure_sandbox_exists():
                    print(f"  Sandbox ensured for agent '{agent_id}'.")
                else:
                    print(f"  Warning: Failed to ensure sandbox for agent '{agent_id}'. File operations might fail.")

                # 8. Add successfully initialized agent to the manager
                self.agents[agent_id] = agent
                successful_initializations += 1
                print(f"--- Agent '{agent_id}' successfully initialized and added. ---")

            except ValueError as ve:
                 print(f"  Configuration Error initializing provider for agent '{agent_id}': {ve}")
                 print(f"--- Agent '{agent_id}' initialization failed. ---")
            except Exception as e:
                 print(f"  Unexpected Error creating or initializing agent '{agent_id}': {e}")
                 traceback.print_exc()
                 print(f"--- Agent '{agent_id}' initialization failed due to exception. ---")

        print(f"Finished agent initialization. Successfully initialized {successful_initializations}/{len(agent_configs_list)} agents.")


    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """
        Receives a message and delegates it to ALL agents whose status is IDLE.
        Manages the async generator interaction for each agent.

        Args:
            message (str): The message content from the user.
            client_id (Optional[str]): Identifier for the client connection.
        """
        print(f"AgentManager received message: '{message[:100]}...' from client: {client_id}")

        active_tasks: List[asyncio.Task] = []
        agents_to_process = []

        if not self.agents:
             print("No agents available in the manager.")
             await self._send_to_ui({"type": "error", "agent_id": "manager", "content": "No agents configured or initialized."})
             return

        # Check agent status instead of just 'is_busy'
        for agent_id, agent in self.agents.items():
            if agent.status == AGENT_STATUS_IDLE:
                agents_to_process.append(agent)
            else:
                 print(f"Skipping Agent '{agent_id}': Status is '{agent.status}'")
                 # Send current status to UI in case it missed an update
                 await self.push_agent_status_update(agent_id)
                 # Don't send a generic busy message, let the actual status reflect

        if not agents_to_process:
            print("No IDLE agents available to handle the message at this time.")
            await self._send_to_ui({"type": "error", "agent_id": "manager", "content": "All active agents are currently busy or in error state."})
            return

        # Create a processing task for each available agent using the generator handler
        for agent in agents_to_process:
            task = asyncio.create_task(self._handle_agent_generator(agent, message))
            active_tasks.append(task)

        if active_tasks:
            print(f"Delegated message to {len(active_tasks)} agents: {[a.agent_id for a in agents_to_process]}")
            # No need to await asyncio.gather here, tasks run independently.
            # await asyncio.gather(*active_tasks) # This would wait for *all* agents to finish before handling new messages
            print(f"{len(active_tasks)} agent processing tasks started.")
        else:
             print("No tasks were created.")


    async def _handle_agent_generator(self, agent: Agent, message: str):
        """
        Handles the async generator interaction for a single agent's processing cycle.
        Listens for events yielded by agent.process_message(), executes tools when
        'tool_requests' are yielded (updating agent status during execution),
        and sends results back to the agent's generator.
        """
        agent_id = agent.agent_id
        print(f"Starting generator handling for Agent '{agent_id}'...")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        results_to_send_back: Optional[List[ToolResultDict]] = None

        try:
            agent_generator = agent.process_message(message)

            while True:
                try:
                    # Send results from previous tool execution (if any) and get next event
                    event = await agent_generator.asend(results_to_send_back)
                    results_to_send_back = None # Reset after sending
                except StopAsyncIteration:
                    print(f"Agent '{agent_id}' generator finished normally.")
                    break
                except Exception as gen_err:
                     print(f"Error interacting with agent '{agent_id}' generator: {gen_err}")
                     traceback.print_exc()
                     agent.set_status(AGENT_STATUS_ERROR) # Set error state
                     await self._send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Agent generator failed: {gen_err}]"})
                     break

                # Process the yielded event
                event_type = event.get("type")

                # Pass through simple events directly to UI
                if event_type in ["response_chunk", "status", "error"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self._send_to_ui(event)
                    if event_type == "error":
                        # Agent should have already set its status to error via set_status
                        print(f"Agent '{agent_id}' reported an error, stopping handling for this agent.")
                        break

                # Handle tool requests yielded by the agent
                elif event_type == "tool_requests":
                    tool_calls_requested = event.get("calls")
                    if not tool_calls_requested or not isinstance(tool_calls_requested, list):
                        print(f"Manager: Invalid 'tool_requests' format from Agent '{agent_id}': {event}")
                        agent.set_status(AGENT_STATUS_ERROR)
                        await self._send_to_ui({"type": "error", "agent_id": agent_id, "content": "[Manager Error: Invalid tool request format received from agent]"})
                        break

                    # Agent status should be AGENT_STATUS_AWAITING_TOOL (set by agent before yield)
                    # Now, execute the tools
                    tool_tasks = []
                    valid_requests_count = 0
                    for call in tool_calls_requested:
                         call_id = call.get("id")
                         tool_name = call.get("name")
                         tool_args = call.get("arguments", {})
                         if call_id and tool_name and isinstance(tool_args, dict):
                              print(f"  Manager: Creating task for Tool: {tool_name}, Call ID: {call_id}")
                              # Pass agent, call_id, tool_name, tool_args to executor function
                              task = asyncio.create_task(
                                   self._execute_single_tool(agent, call_id, tool_name, tool_args)
                              )
                              tool_tasks.append(task)
                              valid_requests_count += 1
                         else:
                              print(f"  Manager: Skipping invalid tool request format from agent '{agent_id}': {call}")
                              tool_tasks.append(asyncio.create_task(self._failed_tool_result(call_id, tool_name)))

                    if valid_requests_count > 0:
                         # Update UI status to show aggregate tool execution state
                         await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {valid_requests_count} tool(s)..."})
                         # Note: Individual tool status is handled within _execute_single_tool

                    # Wait for all tool executions for this batch
                    if tool_tasks:
                         results_from_gather = await asyncio.gather(*tool_tasks)
                         results_to_send_back = [res for res in results_from_gather if res is not None]
                         print(f"  Manager: Gathered {len(results_to_send_back)} tool result(s) for agent '{agent_id}'.")
                    else:
                         print(f"  Manager: No valid tool tasks created for agent '{agent_id}'. Sending empty results back.")
                         results_to_send_back = []

                    # Loop continues, will send results via asend()
                    # Agent's set_status will be called when it receives results

                else:
                    print(f"Manager: Received unknown event type '{event_type}' from agent '{agent_id}'.")

        except Exception as e:
            error_msg = f"Error during manager generator handling for agent {agent_id}: {type(e).__name__} - {e}"
            print(error_msg)
            traceback.print_exc()
            agent.set_status(AGENT_STATUS_ERROR) # Ensure error status is set
            await self._send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: {error_msg}]"})
        finally:
             # Agent should set its own final status in its finally block
             # Manager just needs to ensure the generator is closed
             if agent_generator:
                 try:
                     await agent_generator.aclose()
                     print(f"Closed generator for agent '{agent_id}'.")
                 except Exception as close_err:
                     print(f"Error closing generator for agent '{agent_id}': {close_err}")
             # Send one final status update from the manager's perspective
             await self.push_agent_status_update(agent_id)
             print(f"Manager finished handling generator for Agent '{agent_id}'. Final agent status: {agent.status}")


    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[ToolResultDict]:
        """
        Executes a single tool call via ToolExecutor, updating agent status during execution.
        """
        if not self.tool_executor:
             print(f"Manager Error: ToolExecutor not available for agent '{agent.agent_id}'. Cannot execute tool '{tool_name}'.")
             # Don't change agent status here, return error result
             return {"call_id": call_id, "content": f"[Tool Execution Error: ToolExecutor not available]"}

        # Set agent status to EXECUTING_TOOL *before* execution
        tool_info = {"name": tool_name, "call_id": call_id}
        agent.set_status(AGENT_STATUS_EXECUTING_TOOL, tool_info=tool_info)
        # UI update happens via set_status -> push_agent_status_update
        # Optional: Send a specific message via _send_to_ui as well? Maybe redundant.
        # await self._send_to_ui({
        #     "type": "status", "agent_id": agent.agent_id, "content": f"Executing tool: `{tool_name}` (Call ID: {call_id})..."
        # })

        result_content = "[Tool Execution Error: Unknown error]" # Default error content
        try:
            print(f"Manager: Executing tool '{tool_name}' (Call ID: {call_id}) for agent '{agent.agent_id}'")
            result = await self.tool_executor.execute_tool(
                agent_id=agent.agent_id,
                agent_sandbox_path=agent.sandbox_path,
                tool_name=tool_name,
                tool_args=tool_args
            )
            # Result processing remains the same
            if not isinstance(result, str):
                 try:
                     result_content = json.dumps(result, indent=2)
                 except Exception:
                     result_content = str(result)
            else:
                 result_content = result

            print(f"Tool '{tool_name}' (Call ID: {call_id}) execution successful for agent '{agent.agent_id}'.")
            # Status change after successful execution happens when agent receives result

        except Exception as e:
            error_msg = f"Manager error executing tool '{tool_name}' (Call ID: {call_id}) for agent '{agent.agent_id}': {type(e).__name__} - {e}"
            print(error_msg)
            traceback.print_exc()
            result_content = f"[Tool Execution Error: {error_msg}]"
            # If tool execution fails, should we set agent status back?
            # Let's set it back to Awaiting Tool Result, as it needs to send the error back
            # agent.set_status(AGENT_STATUS_AWAITING_TOOL) # Or should it be ERROR? Let's try AWAITING.
            # The agent itself will likely go to PROCESSING or ERROR upon receiving the error result.
        finally:
            # Important: If the agent's status is still EXECUTING_TOOL after the try/except,
            # it means the tool finished (success or error) but the agent hasn't processed the result yet.
            # Let's revert the status here to AWAITING_TOOL_RESULT, as the manager's execution part is done.
            # The agent will then change it to PROCESSING or ERROR when the result is passed back via asend.
            if agent.status == AGENT_STATUS_EXECUTING_TOOL:
                agent.set_status(AGENT_STATUS_AWAITING_TOOL)

            # Return the result dict (either success content or error content)
            return {"call_id": call_id, "content": result_content}


    async def _failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
         """Returns a generic error result for a tool call that couldn't be dispatched."""
         error_content = f"[Tool Execution Error: Failed to dispatch tool '{tool_name or 'unknown'}'. Invalid format.]"
         return {"call_id": call_id or f"invalid_call_{os.urandom(4).hex()}", "content": error_content}


    async def push_agent_status_update(self, agent_id: str):
        """Retrieves status from a specific agent and sends it to the UI."""
        agent = self.agents.get(agent_id)
        if agent:
            status_data = agent.get_state() # Get the full state dict
            # Send a specific message type for status updates
            await self._send_to_ui({
                "type": "agent_status_update",
                "agent_id": agent_id,
                "status": status_data # Send the whole state dict
            })
        else:
            print(f"Manager: Cannot push status for unknown agent_id: {agent_id}")


    async def _send_to_ui(self, message_data: Dict[str, Any]):
        """
        Sends a structured message back to the UI via the broadcast function.
        """
        if not self.send_to_ui_func:
            print("Warning: UI broadcast function not configured in AgentManager. Cannot send message to UI.")
            return
        try:
            # Add agent_id if it's missing but should be there (e.g., for manager errors)
            if 'agent_id' not in message_data and message_data.get('type') == 'error':
                 message_data['agent_id'] = 'manager'

            message_json = json.dumps(message_data)
            await self.send_to_ui_func(message_json)
        except TypeError as e:
             print(f"Error serializing message data to JSON before sending to UI: {e}")
             print(f"Data was: {message_data}")
             # Try sending a fallback error message
             try:
                 fallback_msg = json.dumps({"type": "error", "agent_id": message_data.get("agent_id", "manager"), "content": f"[Internal Error: Could not serialize message - {e}]"})
                 await self.send_to_ui_func(fallback_msg)
             except Exception as fallback_e:
                 print(f"Failed to send fallback error message to UI: {fallback_e}")

        except Exception as e:
            print(f"Error sending message to UI via broadcast function: {e}")


    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """Returns the status of all managed agents."""
        # This might become less important if status is pushed proactively
        return {agent_id: agent.get_state() for agent_id, agent in self.agents.items()}

    async def cleanup_providers(self):
        """Calls cleanup methods on providers (e.g., close sessions) if they exist."""
        print("Cleaning up LLM providers...")
        for agent_id, agent in self.agents.items():
            provider = agent.llm_provider
            if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session):
                try:
                    print(f"Closing session for provider of agent '{agent_id}'...")
                    await provider.close_session()
                except Exception as e:
                    print(f"Error closing session for provider of agent '{agent_id}': {e}")
        print("LLM Provider cleanup finished.")
