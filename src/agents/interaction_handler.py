# START OF FILE src/agents/interaction_handler.py
import asyncio
import json
import logging
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Tuple

# Import base types and tools
from src.llm_providers.base import ToolResultDict, MessageDict
from src.tools.manage_team import ManageTeamTool
from src.tools.send_message import SendMessageTool

# Import helper for prompt update
from src.agents.prompt_utils import update_agent_prompt_team_id

# Type hinting for AgentManager and Agent
if TYPE_CHECKING:
    from src.agents.manager import AgentManager, BOOTSTRAP_AGENT_ID
    from src.agents.core import Agent

logger = logging.getLogger(__name__)

class AgentInteractionHandler:
    """
    Handles the processing of specific tool interactions and execution of tools,
    requiring context from the AgentManager.
    """
    def __init__(self, manager: 'AgentManager'):
        self._manager = manager
        logger.info("AgentInteractionHandler initialized.")

    async def handle_manage_team_action(
        self,
        action: Optional[str],
        params: Dict[str, Any],
        calling_agent_id: str # Added for context
        ) -> Tuple[bool, str, Optional[Any]]:
        """
        Processes validated ManageTeamTool actions signaled by the tool's execution.
        Calls appropriate AgentManager methods for agent/team lifecycle operations.

        Args:
            action: The validated action name (e.g., 'create_agent').
            params: The validated parameters dictionary from the tool call.
            calling_agent_id: The ID of the agent that invoked the tool.

        Returns:
            Tuple[bool, str, Optional[Any]]: (success_flag, message, optional_data_for_feedback)
        """
        if not action:
            return False, "No action specified.", None
        success, message, result_data = False, "Unknown action or error.", None
        try:
            logger.debug(f"InteractionHandler: Processing ManageTeam action '{action}' from agent '{calling_agent_id}' with params: {params}")
            agent_id_param = params.get("agent_id"); team_id = params.get("team_id")
            provider = params.get("provider"); model = params.get("model")
            system_prompt = params.get("system_prompt"); persona = params.get("persona")
            temperature = params.get("temperature")
            known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
            extra_kwargs = {k: v for k, v in params.items() if k not in known_args and k not in ['project_name', 'session_name']} # Exclude context params

            if action == "create_agent":
                # Call AgentManager's public method
                success, message, created_agent_id = await self._manager.create_agent_instance(
                    agent_id_param, provider, model, system_prompt, persona, team_id, temperature, **extra_kwargs
                )
                if success and created_agent_id:
                    # Prepare data for feedback message to the calling agent
                    result_data = {
                        "created_agent_id": created_agent_id,
                        "persona": persona,
                        "provider": provider,
                        "model": model,
                        "team_id": team_id
                    }
                    message = f"Agent '{persona}' created successfully with ID '{created_agent_id}'." # Overwrite default success msg
            elif action == "delete_agent":
                 # Agent ID validation already happened in ManageTeamTool.execute
                 success, message = await self._manager.delete_agent_instance(agent_id_param)
            elif action == "create_team":
                 success, message = await self._manager.state_manager.create_new_team(team_id)
                 if success: result_data = {"created_team_id": team_id}
            elif action == "delete_team":
                 success, message = await self._manager.state_manager.delete_existing_team(team_id)
            elif action == "add_agent_to_team":
                 success, message = await self._manager.state_manager.add_agent_to_team(agent_id_param, team_id)
                 if success:
                      # Call the prompt update helper function
                      await update_agent_prompt_team_id(self._manager, agent_id_param, team_id)
            elif action == "remove_agent_from_team":
                 success, message = await self._manager.state_manager.remove_agent_from_team(agent_id_param, team_id)
                 if success:
                      # Call the prompt update helper function
                      await update_agent_prompt_team_id(self._manager, agent_id_param, None)
            elif action == "list_agents":
                 filter_team_id = params.get("team_id");
                 # Use manager's sync helper
                 result_data = self._manager.get_agent_info_list_sync(filter_team_id=filter_team_id)
                 success = True; count = len(result_data)
                 message = f"Found {count} agent(s)"
                 if filter_team_id: message += f" in team '{filter_team_id}'."
                 else: message += " in total."
                 # Ensure result_data is serializable (it should be list of dicts)
                 try: json.dumps(result_data)
                 except TypeError: logger.error("list_agents result_data not JSON serializable"); result_data = [{"error": "data not serializable"}]

            elif action == "list_teams":
                 result_data = self._manager.state_manager.get_team_info_dict(); success = True; message = f"Found {len(result_data)} team(s)."
            else: message = f"Unrecognized action: {action}"; logger.warning(message)

            logger.info(f"InteractionHandler: ManageTeamTool action '{action}' processed. Success={success}, Message='{message}'")
            return success, message, result_data
        except Exception as e:
             message = f"InteractionHandler Error processing ManageTeamTool action '{action}': {e}"
             logger.error(message, exc_info=True)
             return False, message, None


    async def route_and_activate_agent_message(
        self,
        sender_id: str,
        target_id: str,
        message_content: str
        ) -> Optional[asyncio.Task]:
        """
        Routes a message from sender to target agent via AgentManager state.
        Validates target existence and team state. Appends feedback to sender on failure.
        Appends message to target history and activates target if idle (or queues).

        Args:
            sender_id: The ID of the agent sending the message.
            target_id: The ID of the agent receiving the message.
            message_content: The content of the message.

        Returns:
            Optional[asyncio.Task]: An asyncio Task for the target agent's generator handling if activated, otherwise None.
        """
        # Import BOOTSTRAP_AGENT_ID locally if needed (or pass from manager)
        from src.agents.manager import BOOTSTRAP_AGENT_ID

        sender_agent = self._manager.agents.get(sender_id)
        target_agent = self._manager.agents.get(target_id)

        # Ensure sender exists (should always be true if this is called)
        if not sender_agent:
            logger.error(f"InteractionHandler SendMsg route error: Sender '{sender_id}' not found (this should not happen)."); return None

        # --- Validate Target Agent ---
        if not target_agent:
            error_msg = f"Failed to send message: Target agent '{target_id}' not found."
            logger.error(f"InteractionHandler SendMsg route error from '{sender_id}': {error_msg}")
            # Append feedback directly to sender's history
            feedback_message: MessageDict = { "role": "tool", "tool_call_id": f"send_message_failed_{target_id}", "content": f"[Manager Feedback for SendMessage]: {error_msg}" }
            sender_agent.message_history.append(feedback_message)
            logger.debug(f"InteractionHandler: Appended 'target not found' feedback to sender '{sender_id}' history.")
            # Signal to AgentManager._handle_agent_generator that sender needs reactivation to see feedback
            self._manager.reactivate_agent_flags[sender_id] = True # Using a flag example
            return None

        # --- Validate Communication Policy (Team / Admin AI) ---
        sender_team = self._manager.state_manager.get_agent_team(sender_id)
        target_team = self._manager.state_manager.get_agent_team(target_id)
        # Communication allowed if: sender is admin, target is admin, or both in the same team
        allowed = (sender_id == BOOTSTRAP_AGENT_ID or
                   target_id == BOOTSTRAP_AGENT_ID or
                   (sender_team and sender_team == target_team))

        if not allowed:
            error_msg = f"Message blocked: Sender '{sender_id}' (Team: {sender_team or 'N/A'}) cannot send to Target '{target_id}' (Team: {target_team or 'N/A'}). Only communication within the same team or with Admin AI is permitted."
            logger.warning(f"InteractionHandler: {error_msg}")
            # Append feedback to sender
            feedback_message: MessageDict = { "role": "tool", "tool_call_id": f"send_message_failed_{target_id}", "content": f"[Manager Feedback for SendMessage]: {error_msg}" }
            sender_agent.message_history.append(feedback_message)
            logger.debug(f"InteractionHandler: Appended 'communication blocked' feedback to sender '{sender_id}' history.")
            # Signal sender reactivation
            self._manager.reactivate_agent_flags[sender_id] = True
            return None

        # --- Deliver Message ---
        formatted_message: MessageDict = { "role": "user", "content": f"[From @{sender_id}]: {message_content}" }
        target_agent.message_history.append(formatted_message)
        logger.debug(f"InteractionHandler: Appended message from '{sender_id}' to history of '{target_id}'.")

        # --- Activate Target (if appropriate) ---
        if target_agent.status == "idle": # Use constant AGENT_STATUS_IDLE if imported
            logger.info(f"InteractionHandler: Target '{target_id}' is IDLE. Activating...");
            # Call AgentManager's internal method to start the generator handling
            # Need access to _handle_agent_generator or a public wrapper
            return asyncio.create_task(self._manager._handle_agent_generator(target_agent, 0))
        elif target_agent.status == "awaiting_user_override": # Use constant
             logger.info(f"InteractionHandler: Target '{target_id}' is {target_agent.status}. Message queued, not activating.")
             # Send status update to UI via manager
             await self._manager.send_to_ui({ "type": "status", "agent_id": target_id, "content": f"Message received from @{sender_id}, queued (awaiting user override)." })
             return None
        else: # Agent is busy (processing, executing tool, etc.)
            logger.info(f"InteractionHandler: Target '{target_id}' not IDLE (Status: {target_agent.status}). Message queued.")
            await self._manager.send_to_ui({ "type": "status", "agent_id": target_id, "content": f"Message received from @{sender_id}, queued." })
            return None


    async def execute_single_tool(
        self,
        agent: 'Agent',
        call_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        project_name: Optional[str], # Context parameter
        session_name: Optional[str]  # Context parameter
        ) -> Optional[Dict]:
        """
        Executes a single tool call via the ToolExecutor, passing necessary context including project/session names.

        Args:
            agent: The Agent instance calling the tool.
            call_id: The unique ID for this tool call.
            tool_name: The name of the tool to execute.
            tool_args: The arguments for the tool.
            project_name: The current project context from the AgentManager.
            session_name: The current session context from the AgentManager.

        Returns:
            Optional[Dict]: A dictionary containing the call_id, formatted content result,
                            and the raw result, or None on executor failure.
        """
        if not self._manager.tool_executor:
            logger.error("InteractionHandler: ToolExecutor unavailable in AgentManager. Cannot execute tool.")
            return {"call_id": call_id, "content": "[ToolExec Error: ToolExecutor unavailable]", "_raw_result": None}

        tool_info = {"name": tool_name, "call_id": call_id}
        agent.set_status("executing_tool", tool_info=tool_info) # Use constant
        raw_result: Optional[Any] = None
        result_content: str = "[Tool Execution Error: Unknown]"

        try:
            # --- Call ToolExecutor with Context ---
            logger.debug(f"InteractionHandler: Executing tool '{tool_name}' (ID: {call_id}) for '{agent.agent_id}' with context Project: {project_name}, Session: {session_name}")
            raw_result = await self._manager.tool_executor.execute_tool(
                agent.agent_id,
                agent.sandbox_path,
                tool_name,
                tool_args,
                project_name=project_name, # Pass context
                session_name=session_name  # Pass context
            )
            logger.debug(f"InteractionHandler: Tool '{tool_name}' completed execution.")

            # Handle Result Formatting (same logic as before)
            if tool_name == ManageTeamTool.name:
                 result_content = raw_result.get("message", str(raw_result)) if isinstance(raw_result, dict) else str(raw_result)
            elif isinstance(raw_result, str):
                 result_content = raw_result
            else:
                 try: result_content = json.dumps(raw_result, indent=2)
                 except TypeError: result_content = str(raw_result)

        except Exception as e:
            error_msg = f"InteractionHandler: Error executing tool '{tool_name}': {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            result_content = f"[ToolExec Error: {error_msg}]" # Use the captured error message
            raw_result = None # Ensure raw_result is None on error
        finally:
            # Reset agent status if it was executing *this* tool call
            if agent.status == "executing_tool" and agent.current_tool_info and agent.current_tool_info.get("call_id") == call_id:
                agent.set_status("processing") # Use constant

        # Return structured result dictionary
        return {"call_id": call_id, "content": result_content, "_raw_result": raw_result}


    async def failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
        """
        Generates a formatted error result dictionary for failed tool dispatch/validation.
        """
        error_content = f"[ToolExec Error: Failed dispatch for '{tool_name or 'unknown'}'. Invalid format or arguments.]"
        final_call_id = call_id or f"invalid_call_{int(time.time())}"
        # Return structure matching ToolResultDict
        return {"call_id": final_call_id, "content": error_content, "_raw_result": {"status": "error", "message": error_content}}
