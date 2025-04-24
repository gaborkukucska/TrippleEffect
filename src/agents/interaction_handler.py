# START OF FILE src/agents/interaction_handler.py
import asyncio
import json
import logging
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Tuple
import copy # Import copy for deepcopy

# Import base types and tools
from src.llm_providers.base import ToolResultDict, MessageDict
from src.tools.manage_team import ManageTeamTool
from src.tools.send_message import SendMessageTool

# Import status constants
from src.agents.constants import AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_EXECUTING_TOOL

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
    requiring context from the AgentManager. Includes robust target agent resolution
    and processing for ManageTeamTool actions like get_agent_details.
    """
    def __init__(self, manager: 'AgentManager'):
        self._manager = manager
        logger.info("AgentInteractionHandler initialized.")

    async def handle_manage_team_action(
        self,
        action: Optional[str],
        params: Dict[str, Any],
        calling_agent_id: str
        ) -> Tuple[bool, str, Optional[Any]]:
        """
        Processes validated ManageTeamTool actions signaled by the tool's execution.
        Calls appropriate AgentManager methods for agent/team lifecycle operations.
        Adds check for duplicate persona on create_agent. Handles idempotent create_team.
        Handles get_agent_details action.
        """
        if not action: return False, "No action specified.", None
        success, message, result_data = False, f"Action '{action}' failed or not recognized.", None # Default message
        try:
            logger.debug(f"InteractionHandler: Processing ManageTeam action '{action}' from agent '{calling_agent_id}' with params: {params}")
            agent_id_param = params.get("agent_id"); team_id = params.get("team_id")
            provider = params.get("provider"); model = params.get("model")
            system_prompt = params.get("system_prompt"); persona = params.get("persona")
            temperature = params.get("temperature")
            known_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
            extra_kwargs = {k: v for k, v in params.items() if k not in known_args and k not in ['project_name', 'session_name']}

            if action == "create_agent":
                duplicate_info = ""
                if team_id and persona:
                    existing_agents_in_team = self._manager.state_manager.get_agents_in_team(team_id)
                    for existing_agent in existing_agents_in_team:
                        if hasattr(existing_agent, 'persona') and isinstance(existing_agent.persona, str) and \
                           existing_agent.persona.lower() == persona.lower():
                             duplicate_info = f" [Note: Agent with persona '{persona}' already exists in team '{team_id}' with ID '{existing_agent.agent_id}'.]"
                             logger.warning(f"InteractionHandler: {duplicate_info}")
                             break
                success, message, created_agent_id = await self._manager.create_agent_instance( agent_id_param, provider, model, system_prompt, persona, team_id, temperature, **extra_kwargs )
                if success and created_agent_id:
                    # Use the actual config used (which might include auto-selected provider/model)
                    created_agent = self._manager.agents.get(created_agent_id)
                    final_provider = created_agent.provider_name if created_agent else (provider or 'auto')
                    # Use the canonical model ID stored in the agent's config
                    final_model = created_agent.agent_config.get("config", {}).get("model", "auto") if created_agent else (model or 'auto')
                    result_data = { "created_agent_id": created_agent_id, "persona": persona, "provider": final_provider, "model": final_model, "team_id": team_id }
                    message += duplicate_info
            elif action == "delete_agent": success, message = await self._manager.delete_agent_instance(agent_id_param)
            elif action == "create_team":
                 success, message = await self._manager.state_manager.create_new_team(team_id);
                 if success and "created successfully" in message: result_data = {"created_team_id": team_id}
            elif action == "delete_team": success, message = await self._manager.state_manager.delete_existing_team(team_id)
            elif action == "add_agent_to_team": success, message = await self._manager.state_manager.add_agent_to_team(agent_id_param, team_id);
            if success and action == "add_agent_to_team": await update_agent_prompt_team_id(self._manager, agent_id_param, team_id)
            elif action == "remove_agent_from_team": success, message = await self._manager.state_manager.remove_agent_from_team(agent_id_param, team_id);
            if success and action == "remove_agent_from_team": await update_agent_prompt_team_id(self._manager, agent_id_param, None)
            elif action == "list_agents":
                 filter_team_id = params.get("team_id"); result_data = self._manager.get_agent_info_list_sync(filter_team_id=filter_team_id); success = True; count = len(result_data); message = f"Found {count} agent(s)";
                 if filter_team_id: message += f" in team '{filter_team_id}'."
                 else: message += " in total."
                 try: json.dumps(result_data)
                 except TypeError: logger.error("list_agents result_data not JSON serializable"); result_data = [{"error": "data not serializable"}]
            elif action == "list_teams": result_data = self._manager.state_manager.get_team_info_dict(); success = True; message = f"Found {len(result_data)} team(s)."
            # --- NEW: Handle get_agent_details ---
            elif action == "get_agent_details":
                 target_agent_id = params.get("agent_id")
                 agent_instance = self._manager.agents.get(target_agent_id)
                 if not agent_instance:
                     success = False; message = f"Agent '{target_agent_id}' not found."
                 else:
                     # Gather detailed info
                     agent_state = agent_instance.get_state() # Basic state: status, persona, provider, model etc.
                     agent_config = getattr(agent_instance, 'agent_config', {}).get('config', {}) # Full config incl prompt
                     team_id = self._manager.state_manager.get_agent_team(target_agent_id)

                     # Construct result_data (make a copy of config to avoid exposing internal objects directly if needed)
                     result_data = {
                         "agent_id": target_agent_id,
                         "status": agent_state.get("status"),
                         "persona": agent_state.get("persona"),
                         "team_id": team_id,
                         "provider": agent_state.get("provider"),
                         "model": agent_config.get("model"), # Get canonical model ID from config
                         "temperature": agent_state.get("temperature"),
                         "current_tool": agent_state.get("current_tool"), # If executing tool
                         "current_plan": agent_state.get("current_plan"), # If planning
                         # Include the full system prompt (potentially large)
                         "system_prompt": agent_config.get("system_prompt"),
                         # Include other config kwargs if needed
                         "other_config": {k:v for k,v in agent_config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona']}
                     }
                     success = True
                     message = f"Successfully retrieved details for agent '{target_agent_id}'."
                     try:
                         # Test serialization before returning success
                         json.dumps(result_data)
                     except TypeError as json_err:
                          logger.error(f"Agent details for '{target_agent_id}' not JSON serializable: {json_err}")
                          result_data = {"agent_id": target_agent_id, "error": "Details contain non-serializable data."}
                          # Still successful in finding agent, but data is limited
                          message = f"Retrieved agent '{target_agent_id}', but details could not be fully serialized."
            # --- End get_agent_details ---

            logger.info(f"InteractionHandler: ManageTeamTool action '{action}' processed. Success={success}, Message='{message}'")
            return success, message, result_data
        except Exception as e: message = f"InteractionHandler Error processing ManageTeamTool action '{action}': {e}"; logger.error(message, exc_info=True); return False, message, None


    async def route_and_activate_agent_message(
        self,
        sender_id: str,
        target_identifier: str,
        message_content: str
        ) -> Optional[asyncio.Task]:
        """
        Routes a message from sender to target agent.
        Attempts to resolve target by exact ID first, then by unique persona match.
        Appends feedback to sender on failure (not found, ambiguous).
        Appends message to target history and schedules target agent cycle if idle.
        """
        from src.agents.manager import BOOTSTRAP_AGENT_ID

        sender_agent = self._manager.agents.get(sender_id)
        if not sender_agent: logger.error(f"InteractionHandler SendMsg route error: Sender '{sender_id}' not found."); return None

        target_agent: Optional[Agent] = None; resolved_target_id: Optional[str] = None; error_msg: Optional[str] = None

        if target_identifier in self._manager.agents:
            resolved_target_id = target_identifier; target_agent = self._manager.agents[resolved_target_id]; logger.debug(f"SendMsg: Resolved target '{target_identifier}' directly by ID.")
        else:
            logger.debug(f"SendMsg: Target '{target_identifier}' not found by ID. Trying persona match...")
            matches = []; target_persona_lower = target_identifier.lower()
            for agent in self._manager.agents.values():
                if hasattr(agent, 'persona') and isinstance(agent.persona, str) and agent.persona.lower() == target_persona_lower: matches.append(agent)
            if len(matches) == 1: target_agent = matches[0]; resolved_target_id = target_agent.agent_id; logger.info(f"SendMsg: Resolved target '{target_identifier}' by unique persona match to agent ID '{resolved_target_id}'.")
            elif len(matches) > 1: error_msg = f"Failed to send message: Target persona '{target_identifier}' is ambiguous. Multiple agents found: {[a.agent_id for a in matches]}. Use the exact agent_id."; logger.warning(f"InteractionHandler SendMsg route error from '{sender_id}': {error_msg}")
            else: error_msg = f"Failed to send message: Target agent ID or persona '{target_identifier}' not found."; logger.error(f"InteractionHandler SendMsg route error from '{sender_id}': {error_msg}")

        if error_msg:
            feedback_message: MessageDict = { "role": "tool", "tool_call_id": f"send_message_failed_{target_identifier}", "content": f"[Manager Feedback for SendMessage]: {error_msg}" }
            sender_agent.message_history.append(feedback_message); logger.debug(f"InteractionHandler: Appended '{error_msg.split(':')[0]}' feedback to sender '{sender_id}' history."); return None

        if not target_agent or not resolved_target_id:
             logger.error(f"Internal error: Target agent or ID is None after resolution for target '{target_identifier}'."); feedback_message: MessageDict = { "role": "tool", "tool_call_id": f"send_message_failed_{target_identifier}", "content": f"[Manager Feedback for SendMessage]: Internal error resolving target agent."}; sender_agent.message_history.append(feedback_message); return None

        sender_team = self._manager.state_manager.get_agent_team(sender_id); target_team = self._manager.state_manager.get_agent_team(resolved_target_id)
        allowed = (sender_id == BOOTSTRAP_AGENT_ID or resolved_target_id == BOOTSTRAP_AGENT_ID or (sender_team and sender_team == target_team))
        if not allowed:
            error_msg = f"Message blocked: Sender '{sender_id}' (Team: {sender_team or 'N/A'}) cannot send to Target '{resolved_target_id}' (Persona: {target_agent.persona}, Team: {target_team or 'N/A'}). Only communication within the same team or with Admin AI is permitted."; logger.warning(f"InteractionHandler: {error_msg}"); feedback_message: MessageDict = { "role": "tool", "tool_call_id": f"send_message_failed_{target_identifier}", "content": f"[Manager Feedback for SendMessage]: {error_msg}" }; sender_agent.message_history.append(feedback_message); logger.debug(f"InteractionHandler: Appended 'communication blocked' feedback to sender '{sender_id}' history."); return None

        formatted_message: MessageDict = { "role": "user", "content": f"[From @{sender_id}]: {message_content}" }; target_agent.message_history.append(formatted_message); logger.debug(f"InteractionHandler: Appended message from '{sender_id}' to history of '{resolved_target_id}'.")

        activation_task = None
        if target_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"InteractionHandler: Target '{resolved_target_id}' is IDLE. Scheduling cycle...");
            activation_task = asyncio.create_task(self._manager.schedule_cycle(target_agent, 0))
        else:
            logger.info(f"InteractionHandler: Target '{resolved_target_id}' not IDLE (Status: {target_agent.status}). Message queued.")
            await self._manager.send_to_ui({ "type": "status", "agent_id": resolved_target_id, "content": f"Message received from @{sender_id}, queued." })
        return activation_task


    async def execute_single_tool(
        self,
        agent: 'Agent',
        call_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        project_name: Optional[str],
        session_name: Optional[str]
        ) -> Optional[Dict]:
        """
        Executes a single tool call via the ToolExecutor, passing necessary context including project/session names.
        """
        if not self._manager.tool_executor: logger.error("InteractionHandler: ToolExecutor unavailable in AgentManager. Cannot execute tool."); return {"call_id": call_id, "content": "[ToolExec Error: ToolExecutor unavailable]", "_raw_result": None}
        tool_info = {"name": tool_name, "call_id": call_id}; agent.set_status(AGENT_STATUS_EXECUTING_TOOL, tool_info=tool_info); raw_result: Optional[Any] = None; result_content: str = "[Tool Execution Error: Unknown]"
        try:
            logger.debug(f"InteractionHandler: Executing tool '{tool_name}' (ID: {call_id}) for '{agent.agent_id}' with context Project: {project_name}, Session: {session_name}")
            # Pass the manager instance to execute_tool
            raw_result = await self._manager.tool_executor.execute_tool(
                agent_id=agent.agent_id,
                agent_sandbox_path=agent.sandbox_path,
                tool_name=tool_name,
                tool_args=tool_args,
                project_name=project_name,
                session_name=session_name,
                manager=self._manager # Pass the manager instance
            )
            logger.debug(f"InteractionHandler: Tool '{tool_name}' completed execution.")
            if tool_name == ManageTeamTool.name: result_content = raw_result.get("message", str(raw_result)) if isinstance(raw_result, dict) else str(raw_result)
            elif isinstance(raw_result, str): result_content = raw_result
            else:
                 try: result_content = json.dumps(raw_result, indent=2)
                 except TypeError: result_content = str(raw_result)
        except Exception as e: error_msg = f"InteractionHandler: Error executing tool '{tool_name}': {type(e).__name__} - {e}"; logger.error(error_msg, exc_info=True); result_content = f"[ToolExec Error: {error_msg}]"; raw_result = None
        finally:
            if agent.status == AGENT_STATUS_EXECUTING_TOOL and agent.current_tool_info and agent.current_tool_info.get("call_id") == call_id: agent.set_status(AGENT_STATUS_PROCESSING)
        return {"call_id": call_id, "content": result_content, "_raw_result": raw_result}


    async def failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
        """
        Generates a formatted error result dictionary for failed tool dispatch/validation.
        """
        error_content = f"[ToolExec Error: Failed dispatch for '{tool_name or 'unknown'}'. Invalid format or arguments.]"; final_call_id = call_id or f"invalid_call_{int(time.time())}"; return {"call_id": final_call_id, "content": error_content, "_raw_result": {"status": "error", "message": error_content}}
