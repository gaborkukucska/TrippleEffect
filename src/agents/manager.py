# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator
import json
import os
import traceback # Import traceback

# Import the Agent class and settings
from src.agents.core import Agent
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
    Coordinates communication between agents and the UI, including tool usage.
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
                # These are the defaults unless overridden in agent's config.yaml entry
                base_provider_config = settings.get_provider_config(provider_name)

                # 3. Get Agent-Specific Overrides & Kwargs from config.yaml
                agent_api_key = agent_config_dict.get("api_key") # Explicit override in config.yaml
                agent_base_url = agent_config_dict.get("base_url") # Explicit override in config.yaml
                # Collect other kwargs from agent config, excluding known keys already handled
                agent_provider_kwargs = {
                     k: v for k, v in agent_config_dict.items()
                     if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona', 'api_key', 'base_url']
                }
                # Add referer from agent config if specified (primarily for OpenRouter)
                if agent_config_dict.get("referer"):
                    agent_provider_kwargs["referer"] = agent_config_dict["referer"]


                # 4. Determine Final Provider Init Args
                final_provider_args = {
                    # Start with .env defaults
                    **base_provider_config,
                     # Add agent-specific kwargs from config.yaml (overrides nothing yet)
                    **agent_provider_kwargs,
                    # Override api_key/base_url ONLY if explicitly set in agent's config.yaml
                    # Prioritize agent config over .env defaults for these specific keys.
                    "api_key": agent_api_key if agent_api_key is not None else base_provider_config.get('api_key'),
                    "base_url": agent_base_url if agent_base_url is not None else base_provider_config.get('base_url'),
                }
                # Remove None values before passing to provider constructor
                final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}


                print(f"  Instantiating provider {ProviderClass.__name__} with args: { {k: (v[:5]+'...' if k=='api_key' and isinstance(v,str) else v) for k, v in final_provider_args.items()} }") # Mask API key in log

                # 5. Instantiate Provider
                llm_provider_instance = ProviderClass(**final_provider_args)
                print(f"  Provider instance created: {llm_provider_instance}")

                # 6. Instantiate Agent, injecting provider and other dependencies
                agent = Agent(
                    agent_config=agent_conf_entry, # Pass the full entry {'agent_id': ..., 'config': ...}
                    llm_provider=llm_provider_instance,
                    tool_executor=self.tool_executor,
                    manager=self # Inject self (manager)
                )
                print(f"  Agent instance created for '{agent_id}'.")

                # 7. Ensure sandbox directory exists
                if agent.ensure_sandbox_exists():
                    print(f"  Sandbox ensured for agent '{agent_id}'.")
                else:
                    # This is not necessarily fatal if agent doesn't use file system tool
                    print(f"  Warning: Failed to ensure sandbox for agent '{agent_id}'. File operations might fail.")

                # 8. Add successfully initialized agent to the manager
                self.agents[agent_id] = agent
                successful_initializations += 1
                print(f"--- Agent '{agent_id}' successfully initialized and added. ---")

            except ValueError as ve: # Catch specific errors like missing API keys from provider init
                 print(f"  Configuration Error initializing provider for agent '{agent_id}': {ve}")
                 print(f"--- Agent '{agent_id}' initialization failed. ---")
            except Exception as e:
                 print(f"  Unexpected Error creating or initializing agent '{agent_id}': {e}")
                 traceback.print_exc() # Print stack trace for unexpected init errors
                 print(f"--- Agent '{agent_id}' initialization failed due to exception. ---")

        print(f"Finished agent initialization. Successfully initialized {successful_initializations}/{len(agent_configs_list)} agents.")


    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """
        Receives a message and delegates it to ALL available agents concurrently.
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

        for agent_id, agent in self.agents.items():
            # Check if agent is busy. Provider initialization check is now implicitly done during Agent init.
            if not agent.is_busy:
                agents_to_process.append(agent)
            else:
                 print(f"Skipping Agent '{agent_id}': Busy.")
                 await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Agent '{agent_id}' is busy."})

        if not agents_to_process:
            print("No available agents to handle the message at this time.")
            await self._send_to_ui({"type": "error", "agent_id": "manager", "content": "All active agents are currently busy."})
            return

        # Create a processing task for each available agent using the generator handler
        for agent in agents_to_process:
            # _handle_agent_generator now interacts with agent's process_message,
            # which in turn interacts with the provider's stream_completion generator.
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
        Listens for events yielded by agent.process_message(), executes tools when
        'tool_requests' are yielded, and sends results back to the agent's generator
        (which then forwards them to the underlying provider's generator via asend).
        """
        agent_id = agent.agent_id
        print(f"Starting generator handling for Agent '{agent_id}'...")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        results_to_send_back: Optional[List[ToolResultDict]] = None # Initialize here

        try:
            # Get the generator from the agent's process_message method
            agent_generator = agent.process_message(message)

            while True: # Loop to handle potential yields after sending tool results
                # Use anext to get the next item, sending results if available from previous iteration
                # Use agent_generator.asend(results_to_send_back) ?? -> Let's test anext first
                # Testing asend:
                try:
                    event = await agent_generator.asend(results_to_send_back)
                    results_to_send_back = None # Reset after sending
                except StopAsyncIteration:
                    print(f"Agent '{agent_id}' generator finished normally.")
                    break # Exit the while loop if generator finishes
                except Exception as gen_err:
                     print(f"Error interacting with agent '{agent_id}' generator: {gen_err}")
                     traceback.print_exc()
                     await self._send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Agent generator failed: {gen_err}]"})
                     break # Exit loop on generator error

                # Process the yielded event
                event_type = event.get("type")

                # Pass through simple events directly to UI
                if event_type in ["response_chunk", "status", "error"]:
                    # Ensure agent_id is present (agent should add it, but double-check)
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self._send_to_ui(event)
                    if event_type == "error":
                        print(f"Agent '{agent_id}' reported an error, stopping handling for this agent.")
                        break # Stop processing on agent error

                # Handle tool requests yielded by the agent
                elif event_type == "tool_requests":
                    tool_calls_requested = event.get("calls")
                    if not tool_calls_requested or not isinstance(tool_calls_requested, list):
                        print(f"Manager: Invalid 'tool_requests' format from Agent '{agent_id}': {event}")
                        await self._send_to_ui({"type": "error", "agent_id": agent_id, "content": "[Manager Error: Invalid tool request format received from agent]"})
                        break # Stop on invalid format

                    await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Agent '{agent_id}' requested {len(tool_calls_requested)} tool call(s)..."})

                    # Execute tools concurrently
                    tool_tasks = []
                    for call in tool_calls_requested:
                         call_id = call.get("id")
                         tool_name = call.get("name")
                         # Arguments should already be parsed dict by provider/agent
                         tool_args = call.get("arguments", {})
                         if call_id and tool_name and isinstance(tool_args, dict):
                              print(f"  Manager: Creating task for Tool: {tool_name}, Call ID: {call_id}, Args: {tool_args}")
                              task = asyncio.create_task(
                                   self._execute_single_tool(agent, call_id, tool_name, tool_args)
                              )
                              tool_tasks.append(task)
                         else:
                              print(f"  Manager: Skipping invalid tool request format from agent '{agent_id}': {call}")
                              # Add a placeholder result indicating failure?
                              # For now, just skip, provider might handle missing results
                              tool_tasks.append(asyncio.create_task(self._failed_tool_result(call_id, tool_name)))


                    # Wait for all tool executions for this batch
                    if tool_tasks:
                         results_from_gather = await asyncio.gather(*tool_tasks)
                         # Filter out potential None results and store for sending back
                         results_to_send_back = [res for res in results_from_gather if res is not None]
                         print(f"  Manager: Gathered {len(results_to_send_back)} tool result(s) for agent '{agent_id}'.")
                         # Loop continues, will send results via asend() at the start of the next iteration

                    else:
                         # No valid tool tasks were created, send back empty list? or None?
                         print(f"  Manager: No valid tool tasks created for agent '{agent_id}'. Sending empty results back.")
                         results_to_send_back = []
                         # Loop continues, will send [] via asend()


                # Handle other potential event types or ignore unknown ones
                else:
                    print(f"Manager: Received unknown event type '{event_type}' from agent '{agent_id}'.")

        except Exception as e:
            # Catch errors during the generator handling loop itself
            error_msg = f"Error during manager generator handling for agent {agent_id}: {type(e).__name__} - {e}"
            print(error_msg)
            traceback.print_exc()
            await self._send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: {error_msg}]"})
        finally:
             # Ensure agent is marked not busy, even if errors occurred
             if agent.is_busy:
                 agent.is_busy = False
                 print(f"Agent '{agent_id}' marked as not busy.")
             # Clean up generator?
             if agent_generator:
                 try:
                     await agent_generator.aclose()
                     print(f"Closed generator for agent '{agent_id}'.")
                 except Exception as close_err:
                     print(f"Error closing generator for agent '{agent_id}': {close_err}")
             await self._send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Agent '{agent_id}' finished processing."})


    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[ToolResultDict]:
        """
        Executes a single tool call via ToolExecutor and formats the result.
        (No changes needed here from previous version)
        """
        if not self.tool_executor:
             print(f"Manager Error: ToolExecutor not available for agent '{agent.agent_id}'. Cannot execute tool '{tool_name}'.")
             return {"call_id": call_id, "content": f"[Tool Execution Error: ToolExecutor not available]"}
        try:
            await self._send_to_ui({
                "type": "status", "agent_id": agent.agent_id, "content": f"Executing tool: `{tool_name}`..."
                # "detail": f"Args: {json.dumps(tool_args)}" # Too verbose
            })

            result_content = await self.tool_executor.execute_tool(
                agent_id=agent.agent_id,
                agent_sandbox_path=agent.sandbox_path,
                tool_name=tool_name,
                tool_args=tool_args
            )
            # Return dict format expected by providers
            return {"call_id": call_id, "content": str(result_content)}

        except Exception as e:
            error_msg = f"Manager error executing tool '{tool_name}' for agent '{agent.agent_id}': {type(e).__name__} - {e}"
            print(error_msg)
            traceback.print_exc()
            # Return error message in the expected format
            return {"call_id": call_id, "content": f"[Tool Execution Error: {error_msg}]"}

    async def _failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
         """Returns a generic error result for a tool call that couldn't be dispatched."""
         error_content = f"[Tool Execution Error: Failed to dispatch tool '{tool_name or 'unknown'}'. Invalid format.]"
         # If call_id is missing, we can't even send a result back properly, but try anyway
         return {"call_id": call_id or f"invalid_call_{os.urandom(4).hex()}", "content": error_content}

    async def _send_to_ui(self, message_data: Dict[str, Any]):
        """
        Sends a structured message back to the UI via the broadcast function.
        (No changes needed here)
        """
        if not self.send_to_ui_func:
            print("Warning: UI broadcast function not configured in AgentManager. Cannot send message to UI.")
            return
        try:
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

    async def cleanup_providers(self):
        """Calls cleanup methods on providers (e.g., close sessions) if they exist."""
        print("Cleaning up LLM providers...")
        for agent_id, agent in self.agents.items():
            provider = agent.llm_provider
            # Check if provider has a specific async close/cleanup method (like Ollama's close_session)
            if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session):
                try:
                    print(f"Closing session for provider of agent '{agent_id}'...")
                    await provider.close_session()
                except Exception as e:
                    print(f"Error closing session for provider of agent '{agent_id}': {e}")
            # Add checks for other potential cleanup methods here
        print("LLM Provider cleanup finished.")
