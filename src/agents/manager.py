# START OF FILE src/agents/manager.py
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator, Tuple # Added Tuple
import json
import os
import traceback # Import traceback
import time # Import time for generating unique call IDs
import logging # Import logging

# Import the Agent class and settings
# Import AGENT_STATUS constants
from src.agents.core import Agent, AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL, AGENT_STATUS_ERROR
# Import settings instance AND BASE_DIR directly
from src.config.settings import settings, BASE_DIR # Also imports TEAMS_CONFIG implicitly

# Import the WebSocket broadcast function
from src.api.websocket_manager import broadcast

# Import ToolExecutor
from src.tools.executor import ToolExecutor

# Import Provider classes and Base class
from src.llm_providers.base import BaseLLMProvider, ToolResultDict, MessageDict
from src.llm_providers.openai_provider import OpenAIProvider
from src.llm_providers.ollama_provider import OllamaProvider
from src.llm_providers.openrouter_provider import OpenRouterProvider

# --- Added Project/Session Management Imports (Placeholder for now) ---
from pathlib import Path
# from src.persistence.session_manager import SessionPersistence # To be created later

logger = logging.getLogger(__name__)


# Mapping from provider name string to provider class
PROVIDER_CLASS_MAP: Dict[str, type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "openrouter": OpenRouterProvider,
    # Add other providers here as they are implemented
}


class AgentManager:
    """
    Manages the lifecycle and task distribution for multiple agents within teams.
    Instantiates providers and agents based on configuration.
    Coordinates communication between agents (intra-team), the UI, and tools.
    Handles autonomous agent activation cycles triggered by SendMessageTool.
    Includes basic session persistence logic (save/load agent histories).
    """
    def __init__(self, websocket_manager: Optional[Any] = None):
        """
        Initializes the AgentManager. Instantiates ToolExecutor and Agents, loads teams.
        """
        self.agents: Dict[str, Agent] = {}
        self.teams: Dict[str, List[str]] = settings.TEAMS_CONFIG # Load teams from settings
        self.agent_to_team: Dict[str, str] = self._map_agents_to_teams() # Helper mapping

        self.send_to_ui_func = broadcast # Use imported broadcast function

        logger.info("Instantiating ToolExecutor...")
        self.tool_executor = ToolExecutor()
        logger.info("ToolExecutor instantiated.")

        # Get formatted XML tool descriptions once
        self.tool_descriptions_xml = self.tool_executor.get_formatted_tool_descriptions_xml()
        logger.info("Generated XML tool descriptions for prompts.")

        # Initialize agents (handles provider instantiation, sandbox, prompt injection)
        self._initialize_agents()
        logger.info(f"AgentManager initialized. Managed agents: {list(self.agents.keys())}")
        logger.info(f"Teams configured: {self.teams}")
        if not self.agents:
             logger.warning("AgentManager initialized with zero active agents. Check configuration and API keys/URLs.")

        # --- Project/Session State (Placeholder) ---
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None
        # self.persistence_manager = SessionPersistence(settings.PROJECTS_BASE_DIR) # Instantiate later

    def _map_agents_to_teams(self) -> Dict[str, str]:
        """Creates a reverse mapping from agent_id to team_name."""
        mapping = {}
        for team_name, agent_ids in self.teams.items():
            for agent_id in agent_ids:
                if agent_id in mapping:
                     logger.warning(f"Agent '{agent_id}' is listed in multiple teams ('{mapping[agent_id]}' and '{team_name}'). Using the last one found.")
                mapping[agent_id] = team_name
        return mapping

    def _initialize_agents(self):
        """
        Creates and initializes agent instances based on configurations loaded
        from `settings.AGENT_CONFIGURATIONS`. Instantiates providers, ensures sandbox,
        injects dependencies including XML tool descriptions.
        """
        logger.info("Initializing agents from configuration...")

        agent_configs_list = settings.AGENT_CONFIGURATIONS
        if not agent_configs_list:
            logger.warning("No agent configurations found in settings. No agents will be created.")
            return

        logger.info(f"Found {len(agent_configs_list)} agent configuration(s). Attempting to initialize...")

        # Ensure the main 'sandboxes' directory exists
        main_sandbox_dir = BASE_DIR / "sandboxes"
        try:
            main_sandbox_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Ensured main sandbox directory exists at: {main_sandbox_dir}")
        except Exception as e:
            logger.error(f"Error creating main sandbox directory at {main_sandbox_dir}: {e}. Agent sandbox creation might fail.")

        successful_initializations = 0
        for agent_conf_entry in agent_configs_list:
            agent_id = agent_conf_entry.get("agent_id")
            if not agent_id:
                logger.warning("Skipping agent configuration due to missing 'agent_id'.")
                continue

            agent_config_dict = agent_conf_entry.get("config", {})
            provider_name = agent_config_dict.get("provider", settings.DEFAULT_AGENT_PROVIDER)

            logger.info(f"--- Initializing agent '{agent_id}' (Provider: {provider_name}) ---")
            try:
                # 1. Select Provider Class
                ProviderClass = PROVIDER_CLASS_MAP.get(provider_name)
                if not ProviderClass:
                    logger.error(f"  Error: Unknown provider '{provider_name}' specified for agent '{agent_id}'. Skipping.")
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
                if agent_config_dict.get("referer"): # Specific handling for referer
                    agent_provider_kwargs["referer"] = agent_config_dict["referer"]


                # 4. Determine Final Provider Init Args
                final_provider_args = {
                    **base_provider_config,
                    **agent_provider_kwargs,
                    "api_key": agent_api_key if agent_api_key is not None else base_provider_config.get('api_key'),
                    "base_url": agent_base_url if agent_base_url is not None else base_provider_config.get('base_url'),
                    "referer": agent_provider_kwargs.get('referer') or base_provider_config.get('referer')
                }
                final_provider_args = {k: v for k, v in final_provider_args.items() if v is not None}

                logger.info(f"  Instantiating provider {ProviderClass.__name__} with args: { {k: (v[:5]+'...' if k=='api_key' and isinstance(v,str) else v) for k, v in final_provider_args.items()} }") # Mask API key

                # 5. Instantiate Provider
                llm_provider_instance = ProviderClass(**final_provider_args)
                logger.info(f"  Provider instance created: {llm_provider_instance}")

                # 6. Instantiate Agent
                agent = Agent(
                    agent_config=agent_conf_entry,
                    llm_provider=llm_provider_instance,
                    manager=self,
                    tool_descriptions_xml=self.tool_descriptions_xml
                )
                logger.info(f"  Agent instance created for '{agent_id}' with tool descriptions injected.")

                # 7. Ensure sandbox directory exists
                if agent.ensure_sandbox_exists():
                    logger.info(f"  Sandbox ensured for agent '{agent_id}'.")
                else:
                    logger.warning(f"  Warning: Failed to ensure sandbox for agent '{agent_id}'. File operations might fail.")

                # 8. Add successfully initialized agent to the manager
                self.agents[agent_id] = agent
                successful_initializations += 1
                logger.info(f"--- Agent '{agent_id}' successfully initialized and added. ---")

            except ValueError as ve:
                 logger.error(f"  Configuration Error initializing provider for agent '{agent_id}': {ve}", exc_info=True)
                 logger.info(f"--- Agent '{agent_id}' initialization failed. ---")
            except Exception as e:
                 logger.error(f"  Unexpected Error creating or initializing agent '{agent_id}': {e}", exc_info=True)
                 logger.info(f"--- Agent '{agent_id}' initialization failed due to exception. ---")

        logger.info(f"Finished agent initialization. Successfully initialized {successful_initializations}/{len(agent_configs_list)} agents.")


    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        """
        Receives a message from the UI and delegates it to ALL agents in the
        'default_team' (or a designated entry point team) whose status is IDLE.

        Args:
            message (str): The message content from the user.
            client_id (Optional[str]): Identifier for the client connection.
        """
        logger.info(f"AgentManager received user message: '{message[:100]}...' from client: {client_id}")

        # --- Determine Target Team (simple approach for now) ---
        # TODO: Enhance this later to handle multiple teams or project context
        target_team_name = "default_team"
        agents_in_target_team = self.teams.get(target_team_name, [])

        if not agents_in_target_team:
            logger.warning(f"No agents configured for the target team '{target_team_name}'. Cannot process message.")
            await self.send_to_ui({"type": "error", "agent_id": "manager", "content": f"No agents found for team '{target_team_name}'."})
            return

        active_tasks: List[asyncio.Task] = []
        agents_to_process = []

        # Process only agents in the target team
        for agent_id in agents_in_target_team:
            agent = self.agents.get(agent_id)
            if not agent:
                 logger.warning(f"Agent '{agent_id}' listed in team '{target_team_name}' but not found in initialized agents.")
                 continue

            if agent.status == AGENT_STATUS_IDLE:
                agents_to_process.append(agent)
            else:
                 logger.info(f"Skipping Agent '{agent_id}' in team '{target_team_name}': Status is '{agent.status}'")
                 # Send current status to UI
                 await self.push_agent_status_update(agent_id)

        if not agents_to_process:
            logger.info(f"No IDLE agents available in team '{target_team_name}' to handle the user message.")
            await self.send_to_ui({"type": "error", "agent_id": "manager", "content": f"All agents in team '{target_team_name}' are currently busy or in error state."})
            return

        # Add user message and create processing task for each available agent
        for agent in agents_to_process:
             logger.info(f"Adding user message to history for agent '{agent.agent_id}' and starting processing task.")
             # Add user message BEFORE starting task
             agent.message_history.append({"role": "user", "content": message})
             task = asyncio.create_task(self._handle_agent_generator(agent)) # No longer pass message arg
             active_tasks.append(task)

        if active_tasks:
            logger.info(f"Delegated user message to {len(active_tasks)} agents in team '{target_team_name}': {[a.agent_id for a in agents_to_process]}")
        else:
             logger.warning("No agent processing tasks were created for the user message.")


    async def _handle_agent_generator(self, agent: Agent):
        """
        Handles the async generator interaction for a single agent's processing cycle.
        Listens for events, executes tools, handles 'send_message' routing and activation.

        Args:
            agent (Agent): The agent instance whose generator is being handled.
                           The initial message should already be in the agent's history.
        """
        agent_id = agent.agent_id
        logger.info(f"Starting generator handling for Agent '{agent_id}'...")
        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        send_message_requests_to_route: List[Tuple[str, str, str]] = [] # Store (sender_id, target_id, message_content)

        try:
            # --- Start the agent's processing generator ---
            # Message is already in history, agent.process_message reads it.
            agent_generator = agent.process_message()

            while True:
                try:
                    # Send None (results no longer needed by generator) and get next event
                    event = await agent_generator.asend(None)
                    # logger.debug(f"Manager: Received event from Agent '{agent_id}': {event.get('type')}") # Verbose debug
                except StopAsyncIteration:
                    logger.info(f"Agent '{agent_id}' generator finished normally.")
                    break # Generator completed its work for this cycle
                except Exception as gen_err:
                     logger.error(f"Error interacting with agent '{agent_id}' generator: {gen_err}", exc_info=True)
                     agent.set_status(AGENT_STATUS_ERROR) # Set error state
                     await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: Agent generator failed: {gen_err}]"})
                     break # Stop handling this agent's cycle

                # Process the yielded event
                event_type = event.get("type")

                # Pass through simple events directly to UI
                if event_type in ["response_chunk", "status", "error", "final_response"]:
                    if "agent_id" not in event: event["agent_id"] = agent_id
                    await self.send_to_ui(event)
                    if event_type == "error":
                        logger.error(f"Agent '{agent_id}' reported an error, stopping handling.")
                        break
                    if event_type == "final_response":
                        # Append final response to history
                        final_content = event.get("content")
                        if final_content and (not agent.message_history or agent.message_history[-1].get("content") != final_content or agent.message_history[-1].get("role") != "assistant"):
                            agent.message_history.append({"role": "assistant", "content": final_content})
                            logger.debug(f"Manager: Appended final assistant response to history for Agent '{agent_id}'.")
                        # Let the generator finish naturally

                # --- Handle tool requests yielded by the agent ---
                elif event_type == "tool_requests":
                    tool_calls_requested = event.get("calls")
                    agent_last_response = event.get("raw_assistant_response") # Full response including XML

                    # Append assistant response (with XML) to history *first*
                    if agent_last_response and (not agent.message_history or agent.message_history[-1].get("content") != agent_last_response or agent.message_history[-1].get("role") != "assistant"):
                         agent.message_history.append({"role": "assistant", "content": agent_last_response})
                         logger.debug(f"Manager: Appended assistant response (with tools) to history for Agent '{agent_id}'.")
                    elif not agent_last_response:
                         logger.warning(f"Manager: Received 'tool_requests' from Agent '{agent_id}' but no 'raw_assistant_response' was included.")

                    if not tool_calls_requested or not isinstance(tool_calls_requested, list):
                        logger.error(f"Manager: Invalid 'tool_requests' format from Agent '{agent_id}': {event}")
                        agent.set_status(AGENT_STATUS_ERROR)
                        await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": "[Manager Error: Invalid tool request format received from agent]"})
                        break # Stop processing

                    # Prepare tool execution tasks and identify SendMessage requests
                    tool_tasks = []
                    send_message_details_pending: List[Dict] = [] # Store args for send_message calls

                    valid_requests_count = 0
                    for call in tool_calls_requested:
                        call_id = call.get("id")
                        tool_name = call.get("name")
                        tool_args = call.get("arguments", {})
                        if call_id and tool_name and isinstance(tool_args, dict):
                            valid_requests_count += 1
                            # --- Check specifically for SendMessageTool ---
                            if tool_name == "send_message":
                                target_id = tool_args.get("target_agent_id")
                                msg_content = tool_args.get("message_content")
                                if target_id and msg_content is not None:
                                     # Store details needed for routing *after* execution confirms validation
                                     send_message_details_pending.append({
                                         "call_id": call_id, # Match result later
                                         "sender_id": agent_id,
                                         "target_id": target_id,
                                         "content": msg_content
                                     })
                                     logger.info(f"Manager: Identified send_message request from '{agent_id}' to '{target_id}'. Will route after tool execution.")
                                else:
                                     logger.warning(f"Manager: SendMessageTool call from '{agent_id}' missing target_id or message_content. Args: {tool_args}")
                                     # Still execute tool to get validation error message back to sender
                            # --- End SendMessageTool Check ---

                            # Create execution task for all tools (including send_message for validation)
                            task = asyncio.create_task(
                                self._execute_single_tool(agent, call_id, tool_name, tool_args)
                            )
                            tool_tasks.append(task)
                        else:
                            logger.warning(f"  Manager: Skipping invalid tool request format from agent '{agent_id}': {call}")
                            tool_tasks.append(asyncio.create_task(self._failed_tool_result(call_id, tool_name))) # Handle failed dispatch

                    if valid_requests_count > 0:
                        await self.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {valid_requests_count} tool(s)..."})

                    # Wait for all tool executions for this batch
                    tool_results_raw: List[Optional[ToolResultDict]] = []
                    executed_tool_results: Optional[List[ToolResultDict]] = None
                    if tool_tasks:
                         tool_results_raw = await asyncio.gather(*tool_tasks)
                         executed_tool_results = [res for res in tool_results_raw if res is not None]
                         logger.debug(f"  Manager: Gathered {len(executed_tool_results)} tool result(s) for agent '{agent_id}'.")
                    else:
                         logger.warning(f"  Manager: No valid tool tasks were created for agent '{agent_id}'.")
                         executed_tool_results = []

                    # Append Tool Results to *sender's* history
                    if executed_tool_results:
                        append_count = 0
                        for result in executed_tool_results:
                            tool_message: MessageDict = {
                                "role": "tool", "tool_call_id": result["call_id"], "content": result["content"]
                            }
                            # Avoid duplicate tool results (can happen in complex flows?)
                            if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != result["call_id"]:
                                 agent.message_history.append(tool_message)
                                 append_count += 1
                        logger.debug(f"Manager: Appended {append_count} tool result(s) to history for Agent '{agent_id}'.")

                    # --- Route SendMessage requests AFTER appending results to sender ---
                    activation_tasks = [] # Tasks to activate recipient agents
                    for request_details in send_message_details_pending:
                         # Find the corresponding execution result (it should just be the confirmation string)
                         exec_result = next((r for r in executed_tool_results if r["call_id"] == request_details["call_id"]), None)
                         if exec_result and not exec_result["content"].startswith("Error:"):
                              activation_task = await self._route_and_activate_agent_message(
                                   sender_id=request_details["sender_id"],
                                   target_id=request_details["target_id"],
                                   message_content=request_details["content"]
                              )
                              if activation_task:
                                   activation_tasks.append(activation_task)
                         else:
                              logger.warning(f"SendMessageTool call {request_details['call_id']} from '{agent_id}' failed validation or execution, not routing. Result: {exec_result}")

                    if activation_tasks:
                         logger.info(f"Manager: Triggered activation for {len(activation_tasks)} agents based on send_message calls from '{agent_id}'.")
                         # We don't wait for these tasks here, they run independently

                    # Loop continues, will call asend(None) to resume the agent generator

                else:
                    logger.warning(f"Manager: Received unknown event type '{event_type}' from agent '{agent_id}'.")

        except Exception as e:
            error_msg = f"Error during manager generator handling for agent {agent_id}: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            agent.set_status(AGENT_STATUS_ERROR) # Ensure error status is set
            await self.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Manager Error: {error_msg}]"})
        finally:
             if agent_generator:
                 try:
                     await agent_generator.aclose()
                     logger.debug(f"Closed generator for agent '{agent_id}'.")
                 except Exception as close_err:
                     logger.error(f"Error closing generator for agent '{agent_id}': {close_err}", exc_info=True)
             # Agent should set its final status in its finally block. Push one last update.
             await self.push_agent_status_update(agent_id)
             logger.info(f"Manager finished handling generator cycle for Agent '{agent_id}'. Final agent status: {agent.status}")


    async def _route_and_activate_agent_message(self, sender_id: str, target_id: str, message_content: str) -> Optional[asyncio.Task]:
        """
        Routes a message from a sender agent to a target agent, appends it to the
        target's history, and activates the target agent if it's idle.

        Returns:
            An asyncio.Task if the target agent was activated, otherwise None.
        """
        sender_agent = self.agents.get(sender_id)
        target_agent = self.agents.get(target_id)

        if not sender_agent or not target_agent:
            logger.error(f"SendMessage routing error: Sender '{sender_id}' or Target '{target_id}' not found.")
            # Optionally send error back to sender? Difficult at this stage.
            return None

        # --- Team Validation (Optional but recommended) ---
        sender_team = self.agent_to_team.get(sender_id)
        target_team = self.agent_to_team.get(target_id)
        if not sender_team or not target_team or sender_team != target_team:
             logger.warning(f"SendMessage attempt blocked: Sender '{sender_id}' (Team: {sender_team}) and Target '{target_id}' (Team: {target_team}) are not in the same team.")
             # Send error back to sender via history? Needs careful implementation.
             # For now, just don't route. The sender received the confirmation msg from the tool.
             # TODO: Add error feedback mechanism to sender?
             return None
        # --- End Team Validation ---

        logger.info(f"Routing message from '{sender_id}' to '{target_id}' in team '{target_team}'.")

        # Format message for target's history (treat as user input)
        formatted_message: MessageDict = {
            "role": "user",
            "content": f"[From @{sender_id}]: {message_content}"
        }
        target_agent.message_history.append(formatted_message)
        logger.debug(f"Appended message from '{sender_id}' to history of '{target_id}'.")

        # Activate target agent if idle
        if target_agent.status == AGENT_STATUS_IDLE:
             logger.info(f"Target agent '{target_id}' is IDLE. Creating activation task...")
             # Create task to handle the target agent's generator
             activation_task = asyncio.create_task(self._handle_agent_generator(target_agent))
             return activation_task
        else:
             logger.info(f"Target agent '{target_id}' is not IDLE (Status: {target_agent.status}). Message appended, but agent not activated now.")
             return None


    async def _execute_single_tool(self, agent: Agent, call_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[ToolResultDict]:
        """
        Executes a single tool call via ToolExecutor, updating agent status during execution.
        Args are pre-parsed by the Agent.
        """
        if not self.tool_executor:
             logger.error(f"Manager Error: ToolExecutor not available for agent '{agent.agent_id}'. Cannot execute tool '{tool_name}'.")
             return {"call_id": call_id, "content": f"[Tool Execution Error: ToolExecutor not available]"}

        tool_info = {"name": tool_name, "call_id": call_id}
        agent.set_status(AGENT_STATUS_EXECUTING_TOOL, tool_info=tool_info)

        result_content = "[Tool Execution Error: Unknown error]"
        try:
            logger.debug(f"Manager: Executing tool '{tool_name}' (Call ID: {call_id}) for agent '{agent.agent_id}'")
            # ToolExecutor handles argument validation internally now
            result = await self.tool_executor.execute_tool(
                agent_id=agent.agent_id,
                agent_sandbox_path=agent.sandbox_path,
                tool_name=tool_name,
                tool_args=tool_args
            )
            result_content = result # execute_tool ensures result is string
            logger.debug(f"Tool '{tool_name}' (Call ID: {call_id}) execution successful for agent '{agent.agent_id}'.")

        except Exception as e:
            error_msg = f"Manager error executing tool '{tool_name}' (Call ID: {call_id}) for agent '{agent.agent_id}': {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            result_content = f"[Tool Execution Error: {error_msg}]"
        finally:
            # Revert status after execution attempt. Back to PROCESSING allows agent gen to continue if needed.
            if agent.status == AGENT_STATUS_EXECUTING_TOOL:
                 agent.set_status(AGENT_STATUS_PROCESSING)

            return {"call_id": call_id, "content": result_content}


    async def _failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
         """Returns a generic error result for a tool call that couldn't be dispatched."""
         error_content = f"[Tool Execution Error: Failed to dispatch tool '{tool_name or 'unknown'}'. Invalid format.]"
         final_call_id = call_id or f"invalid_xml_call_{int(time.time())}_{os.urandom(2).hex()}"
         return {"call_id": final_call_id, "content": error_content}


    async def push_agent_status_update(self, agent_id: str):
        """Retrieves status from a specific agent and sends it to the UI."""
        agent = self.agents.get(agent_id)
        if agent:
            status_data = agent.get_state() # Get the full state dict
            # Add team info to status if available
            status_data["team"] = self.agent_to_team.get(agent_id, "N/A")
            await self.send_to_ui({ # Use public method
                "type": "agent_status_update",
                "agent_id": agent_id,
                "status": status_data # Send the whole state dict including team
            })
        else:
            logger.warning(f"Manager: Cannot push status for unknown agent_id: {agent_id}")


    async def send_to_ui(self, message_data: Dict[str, Any]): # Renamed from _send_to_ui
        """Sends a structured message back to the UI via the broadcast function."""
        if not self.send_to_ui_func:
            logger.warning("Warning: UI broadcast function not configured in AgentManager. Cannot send message to UI.")
            return
        try:
            if 'agent_id' not in message_data and message_data.get('type') == 'error':
                 message_data['agent_id'] = 'manager'
            message_json = json.dumps(message_data)
            await self.send_to_ui_func(message_json)
        except TypeError as e:
             logger.error(f"Error serializing message data to JSON before sending to UI: {e}", exc_info=True)
             logger.debug(f"Data that failed serialization: {message_data}")
             try:
                 # Attempt to send a simplified error message
                 fallback_msg = json.dumps({"type": "error", "agent_id": message_data.get("agent_id", "manager"), "content": f"[Internal Error: Could not serialize message data - Check logs]"})
                 await self.send_to_ui_func(fallback_msg)
             except Exception as fallback_e:
                 logger.error(f"Failed to send fallback error message to UI: {fallback_e}")
        except Exception as e:
            logger.error(f"Error sending message to UI via broadcast function: {e}", exc_info=True)


    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """Returns the status of all managed agents, including team info."""
        statuses = {}
        for agent_id, agent in self.agents.items():
            state = agent.get_state()
            state["team"] = self.agent_to_team.get(agent_id, "N/A") # Add team info
            statuses[agent_id] = state
        return statuses

    # --- Session Persistence Methods (Basic Implementation) ---

    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        """Saves the current message histories of all agents to a session file."""
        if not project_name:
            return False, "Project name cannot be empty."

        # Generate session name if not provided (e.g., timestamp)
        if not session_name:
            session_name = f"session_{int(time.time())}"

        project_dir = settings.PROJECTS_BASE_DIR / project_name
        session_dir = project_dir / session_name
        histories_file = session_dir / "agent_histories.json"

        logger.info(f"Attempting to save session to: {histories_file}")

        session_data = {
            "project": project_name,
            "session": session_name,
            "timestamp": time.time(),
            "agents": {}
        }
        # Collect histories from all agents
        for agent_id, agent in self.agents.items():
             # Ensure history is serializable (should be list of dicts)
             try:
                 json.dumps(agent.message_history)
                 session_data["agents"][agent_id] = {
                     "persona": agent.persona,
                     "message_history": agent.message_history
                 }
             except TypeError as e:
                 logger.error(f"History for agent '{agent_id}' is not JSON serializable: {e}. Skipping history save for this agent.")
                 session_data["agents"][agent_id] = {
                     "persona": agent.persona,
                     "message_history": [{"role": "system", "content": f"[History serialization error: {e}]"}]
                 }


        try:
            # Use asyncio.to_thread for blocking I/O
            def save_sync():
                session_dir.mkdir(parents=True, exist_ok=True)
                with open(histories_file, 'w', encoding='utf-8') as f:
                    json.dump(session_data, f, indent=2)

            await asyncio.to_thread(save_sync)
            logger.info(f"Session saved successfully to {histories_file}")
            self.current_project = project_name
            self.current_session = session_name
            return True, f"Session '{session_name}' saved successfully in project '{project_name}'."

        except Exception as e:
            logger.error(f"Error saving session to {histories_file}: {e}", exc_info=True)
            return False, f"Error saving session: {e}"

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        """Loads agent message histories from a saved session file."""
        if not project_name or not session_name:
            return False, "Project and session names cannot be empty."

        histories_file = settings.PROJECTS_BASE_DIR / project_name / session_name / "agent_histories.json"
        logger.info(f"Attempting to load session from: {histories_file}")

        if not histories_file.is_file():
            logger.error(f"Session file not found: {histories_file}")
            return False, f"Session file '{session_name}' not found in project '{project_name}'."

        try:
            # Use asyncio.to_thread for blocking I/O
            def load_sync():
                 with open(histories_file, 'r', encoding='utf-8') as f:
                     return json.load(f)

            session_data = await asyncio.to_thread(load_sync)
            loaded_agents_data = session_data.get("agents", {})

            # Load histories into current agents, clearing existing ones
            loaded_count = 0
            missed_agents = []
            for agent_id, agent_data in loaded_agents_data.items():
                agent = self.agents.get(agent_id)
                if agent:
                    loaded_history = agent_data.get("message_history")
                    if isinstance(loaded_history, list):
                         # Basic validation of history format
                         if all(isinstance(msg, dict) and 'role' in msg and 'content' in msg for msg in loaded_history):
                              agent.message_history = loaded_history
                              agent.set_status(AGENT_STATUS_IDLE) # Reset status after loading
                              logger.debug(f"Loaded history for agent '{agent_id}'.")
                              loaded_count += 1
                         else:
                              logger.warning(f"Invalid history format loaded for agent '{agent_id}'. History not loaded.")
                              agent.clear_history() # Clear to default state
                    else:
                         logger.warning(f"History data for agent '{agent_id}' in session file is not a list. History not loaded.")
                         agent.clear_history()
                else:
                    missed_agents.append(agent_id)

            logger.info(f"Session loaded from {histories_file}. Loaded histories for {loaded_count} agents.")
            if missed_agents:
                logger.warning(f"Agents found in session file but not currently configured/initialized: {missed_agents}")

            self.current_project = project_name
            self.current_session = session_name
            # Push status updates for all agents after load
            await asyncio.gather(*(self.push_agent_status_update(aid) for aid in self.agents.keys()))
            return True, f"Session '{session_name}' from project '{project_name}' loaded successfully."

        except json.JSONDecodeError as e:
             logger.error(f"Error decoding JSON from session file {histories_file}: {e}")
             return False, f"Error reading session file: Invalid JSON format."
        except Exception as e:
            logger.error(f"Error loading session from {histories_file}: {e}", exc_info=True)
            return False, f"Error loading session: {e}"

    # --- End Session Persistence ---

    async def cleanup_providers(self):
        """Calls cleanup methods on providers (e.g., close sessions) if they exist."""
        logger.info("Cleaning up LLM providers...")
        for agent_id, agent in self.agents.items():
            provider = agent.llm_provider
            if hasattr(provider, 'close_session') and asyncio.iscoroutinefunction(provider.close_session):
                try:
                    logger.info(f"Closing session for provider of agent '{agent_id}'...")
                    await provider.close_session()
                except Exception as e:
                    logger.error(f"Error closing session for provider of agent '{agent_id}': {e}", exc_info=True)
        logger.info("LLM Provider cleanup finished.")
