# START OF FILE src/agents/interaction_handler.py
import asyncio
import json
import logging
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Tuple
import copy # Import copy for deepcopy
import time # Import time for failed_tool_result timestamp

# Import base types and tools
from src.llm_providers.base import ToolResultDict, MessageDict
from src.tools.manage_team import ManageTeamTool
from src.tools.send_message import SendMessageTool
from src.tools.project_management import ProjectManagementTool # Import ProjectManagementTool
from src.tools.tool_parser import parse_tool_call # This seems unused, consider removing if not needed elsewhere

# Import status constants and agent types
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_EXECUTING_TOOL,
    AGENT_TYPE_WORKER, WORKER_STATE_WORK, BOOTSTRAP_AGENT_ID, AGENT_STATUS_ERROR, # Added BOOTSTRAP_AGENT_ID
    AGENT_STATUS_AWAITING_CG_REVIEW, AGENT_STATUS_AWAITING_USER_REVIEW_CG # Added CG states
)

# Import helper for prompt update
from src.agents.prompt_utils import update_agent_prompt_team_id

# Type hinting for AgentManager and Agent
if TYPE_CHECKING:
    from src.agents.manager import AgentManager # Keep BOOTSTRAP_AGENT_ID here if only used in this file
    from src.agents.core import Agent
    from src.agents.workflow_manager import AgentWorkflowManager # For type hinting

logger = logging.getLogger(__name__)

class AgentInteractionHandler:
    """
    Handles the processing of specific tool interactions and execution of tools,
    requiring context from the AgentManager. Includes robust target agent resolution
    and processing for ManageTeamTool actions like get_agent_details and set_agent_state.
    """
    def __init__(self, manager: 'AgentManager'):
        self._manager = manager
        logger.info("AgentInteractionHandler initialized.")

    async def handle_manage_team_action(
        self,
        action_to_perform: Optional[str], # Changed from 'action' to avoid conflict with kwargs
        action_params: Dict[str, Any],    # Changed from 'params'
        calling_agent_id: str
        ) -> Tuple[bool, str, Optional[Any]]:
        """
        Processes validated ManageTeamTool actions signaled by the tool's execution.
        Calls appropriate AgentManager methods for agent/team lifecycle operations or state changes.
        """
        if not action_to_perform: return False, "No action specified by ManageTeamTool.", None
        
        success, message, result_data = False, f"Action '{action_to_perform}' failed or not recognized by InteractionHandler.", None
        
        try:
            logger.debug(f"InteractionHandler: Processing ManageTeam signal '{action_to_perform}' from tool for agent '{calling_agent_id}' with params: {action_params}")
            
            # Extract common parameters
            agent_id_param = action_params.get("agent_id")
            team_id_param = action_params.get("team_id") # Renamed for clarity

            if action_to_perform == "create_agent":
                provider = action_params.get("provider")
                model = action_params.get("model")
                system_prompt = action_params.get("system_prompt")
                persona = action_params.get("persona")
                temperature = action_params.get("temperature")
                # Filter out known args before passing to **extra_kwargs for create_agent_instance
                known_create_args = ['action', 'agent_id', 'team_id', 'provider', 'model', 'system_prompt', 'persona', 'temperature']
                extra_kwargs = {k: v for k, v in action_params.items() if k not in known_create_args and k not in ['project_name', 'session_name']}

                duplicate_info = ""
                if team_id_param and persona: # Use team_id_param
                    existing_agents_in_team = self._manager.state_manager.get_agents_in_team(team_id_param)
                    for existing_agent in existing_agents_in_team:
                        if hasattr(existing_agent, 'persona') and isinstance(existing_agent.persona, str) and \
                           existing_agent.persona.lower() == persona.lower():
                             duplicate_info = f" [Note: Agent with persona '{persona}' already exists in team '{team_id_param}' with ID '{existing_agent.agent_id}'.]"
                             logger.warning(f"InteractionHandler: {duplicate_info}")
                             break
                success, message, created_agent_id = await self._manager.create_agent_instance(
                    agent_id_requested=agent_id_param, provider=provider, model=model,
                    system_prompt=system_prompt, persona=persona, team_id=team_id_param, # Use team_id_param
                    temperature=temperature, **extra_kwargs
                )
                if success and created_agent_id:
                    created_agent = self._manager.agents.get(created_agent_id)
                    final_provider = created_agent.provider_name if created_agent else (provider or 'auto')
                    final_model = created_agent.agent_config.get("config", {}).get("model", "auto") if created_agent else (model or 'auto')
                    # Ensure the actual created_agent_id is in result_data, along with requested if different
                    result_data = {
                        "requested_agent_id": agent_id_param, # The ID requested by the PM
                        "created_agent_id": created_agent_id, # The actual ID generated by the system
                        "persona": persona,
                        "provider": final_provider,
                        "model": final_model,
                        "team_id": team_id_param
                    }
                    message += duplicate_info
                    logger.info(f"InteractionHandler: create_agent successful. Requested ID: '{agent_id_param}', Created ID: '{created_agent_id}'.")

                    # --- START: Auto-add to creator's team ---
                    creator_team_id = self._manager.state_manager.get_agent_team(calling_agent_id)
                    if creator_team_id:
                        logger.info(f"Creator '{calling_agent_id}' is in team '{creator_team_id}'. Adding new agent '{created_agent_id}' to this team.")
                        add_success, add_message = await self._manager.state_manager.add_agent_to_team(created_agent_id, creator_team_id)
                        if add_success:
                            message += f" Agent '{created_agent_id}' also added to team '{creator_team_id}'."
                            result_data["team_id"] = creator_team_id # Update result data with the actual team
                            await update_agent_prompt_team_id(self._manager, created_agent_id, creator_team_id)
                        else:
                            message += f" [Warning: Failed to auto-add agent to team '{creator_team_id}': {add_message}]"
                            logger.warning(f"InteractionHandler: Failed to auto-add new agent '{created_agent_id}' to creator's team '{creator_team_id}': {add_message}")
                    else:
                        logger.info(f"InteractionHandler: Creator '{calling_agent_id}' is not in a team. New agent '{created_agent_id}' was not auto-assigned to a team.")
                    # --- END: Auto-add to creator's team ---

            elif action_to_perform == "delete_agent":
                success, message = await self._manager.delete_agent_instance(agent_id_param)
            
            elif action_to_perform == "create_team":
                 success, message = await self._manager.state_manager.create_new_team(team_id_param)
                 if success:
                     add_success, add_message = await self._manager.state_manager.add_agent_to_team(agent_id=calling_agent_id, team_id=team_id_param)
                     if add_success: message += f" Agent '{calling_agent_id}' automatically added."
                     else: logger.warning(f"Failed to auto-add creator '{calling_agent_id}' to new team '{team_id_param}': {add_message}"); message += f" (Warning: Failed to auto-add: {add_message})"
                     if "created successfully" in message or "already exists" in message: result_data = {"created_team_id": team_id_param}


            elif action_to_perform == "delete_team":
                success, message = await self._manager.state_manager.delete_existing_team(team_id_param)
            
            elif action_to_perform == "add_agent_to_team":
                success, message = await self._manager.state_manager.add_agent_to_team(agent_id_param, team_id_param)
                if success: await update_agent_prompt_team_id(self._manager, agent_id_param, team_id_param)
            
            elif action_to_perform == "remove_agent_from_team":
                success, message = await self._manager.state_manager.remove_agent_from_team(agent_id_param, team_id_param)
                if success: await update_agent_prompt_team_id(self._manager, agent_id_param, None)
            
            elif action_to_perform == "list_agents":
                 filter_team_id = action_params.get("team_id") # team_id is optional for list_agents
                 result_data = self._manager.get_agent_info_list_sync(filter_team_id=filter_team_id)
                 success = True; count = len(result_data); message = f"Found {count} agent(s)"
                 if filter_team_id: message += f" in team '{filter_team_id}'."
                 else: message += " in total."
                 try: json.dumps(result_data)
                 except TypeError: logger.error("list_agents result_data not JSON serializable"); result_data = [{"error": "data not serializable"}]
            
            elif action_to_perform == "list_teams":
                 result_data = self._manager.state_manager.get_team_info_dict()
                 success = True; message = f"Found {len(result_data)} team(s)."
            
            elif action_to_perform == "get_agent_details":
                 target_agent_id_details = action_params.get("agent_id") # Changed variable name
                 agent_instance = self._manager.agents.get(target_agent_id_details)
                 if not agent_instance:
                     success = False; message = f"Agent '{target_agent_id_details}' not found."
                 else:
                     agent_state_info = agent_instance.get_state() # Renamed variable
                     agent_config = getattr(agent_instance, 'agent_config', {}).get('config', {})
                     team_id_details = self._manager.state_manager.get_agent_team(target_agent_id_details) # Renamed variable
                     result_data = {
                         "agent_id": target_agent_id_details, "status": agent_state_info.get("status"),
                         "persona": agent_state_info.get("persona"), "team_id": team_id_details,
                         "provider": agent_state_info.get("provider"), "model": agent_config.get("model"),
                         "temperature": agent_state_info.get("temperature"), "current_tool": agent_state_info.get("current_tool"),
                         "current_plan": agent_state_info.get("current_plan"), "system_prompt": agent_config.get("system_prompt"),
                         "other_config": {k:v for k,v in agent_config.items() if k not in ['provider', 'model', 'system_prompt', 'temperature', 'persona']}
                     }
                     success = True; message = f"Successfully retrieved details for agent '{target_agent_id_details}'."
                     try: json.dumps(result_data)
                     except TypeError as json_err:
                          logger.error(f"Agent details for '{target_agent_id_details}' not JSON serializable: {json_err}")
                          result_data = {"agent_id": target_agent_id_details, "error": "Details contain non-serializable data."}
                          message = f"Retrieved agent '{target_agent_id_details}', but details non-serializable."
            
            # --- NEW: Handle set_agent_state ---
            elif action_to_perform == "set_agent_state":
                target_agent_id_state = action_params.get("agent_id")
                new_state = action_params.get("new_state")
                
                target_agent_instance = self._manager.agents.get(target_agent_id_state)
                if not target_agent_instance:
                    success = False; message = f"Error setting state: Agent '{target_agent_id_state}' not found."
                elif target_agent_id_state == BOOTSTRAP_AGENT_ID: # Double check restriction (already in tool)
                    success = False; message = f"Error: Cannot use 'set_agent_state' on Admin AI ('{BOOTSTRAP_AGENT_ID}')."
                else:
                    if hasattr(self._manager, 'workflow_manager'):
                        # Cast for type hinting if self._manager.workflow_manager exists
                        workflow_mgr: 'AgentWorkflowManager' = self._manager.workflow_manager
                        state_change_success = workflow_mgr.change_state(target_agent_instance, new_state)
                        if state_change_success:
                            success = True
                            message = f"Agent '{target_agent_id_state}' state successfully changed to '{new_state}'."
                            result_data = {"agent_id": target_agent_id_state, "new_state": new_state, "activated": False}
                            # Activate the agent if it's now idle and was successfully set to a new state
                            if target_agent_instance.status == AGENT_STATUS_IDLE:
                                logger.info(f"InteractionHandler: Agent '{target_agent_id_state}' is IDLE after state change to '{new_state}'. Scheduling cycle...")
                                asyncio.create_task(self._manager.schedule_cycle(target_agent_instance, 0))
                                result_data["activated"] = True
                                message += " Agent activated."
                            else:
                                message += f" Agent was not IDLE (Status: {target_agent_instance.status}), not reactivated by this action."
                        else:
                            success = False
                            message = f"Failed to change state for agent '{target_agent_id_state}' to '{new_state}'. It might be an invalid state for this agent type or already in that state."
                    else:
                        success = False; message = "Error: WorkflowManager not available to change agent state."
            # --- End set_agent_state ---

            logger.info(f"InteractionHandler: ManageTeamTool signal '{action_to_perform}' processed. Success={success}, Message='{message}'")
            return success, message, result_data
        except Exception as e:
            message = f"InteractionHandler Error processing ManageTeamTool signal '{action_to_perform}': {e}"
            logger.error(message, exc_info=True)
            return False, message, None


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

        formatted_message: MessageDict = { "role": "user", "content": f"[From @{sender_id} ({sender_agent.persona})]: {message_content}" }; # Added sender persona
        target_agent.message_history.append(formatted_message); logger.debug(f"InteractionHandler: Appended message from '{sender_id}' to history of '{resolved_target_id}'.")

        activation_task = None
        if target_agent.status == AGENT_STATUS_IDLE:
            logger.info(f"InteractionHandler: Target '{resolved_target_id}' ({target_agent.persona}) is IDLE. Scheduling cycle due to new message from '{sender_id}'.")
            activation_task = asyncio.create_task(self._manager.schedule_cycle(target_agent, 0))
        elif target_agent.status == AGENT_STATUS_ERROR:
            logger.info(f"InteractionHandler: Reset agent {target_agent.agent_id} ({target_agent.persona}) status from ERROR to IDLE due to incoming message from '{sender_id}'. Scheduling cycle.")
            target_agent.set_status(AGENT_STATUS_IDLE) # set_status also pushes UI update via manager
            activation_task = asyncio.create_task(self._manager.schedule_cycle(target_agent, 0))
            # No specific send_to_ui here as schedule_cycle and set_status handle agent status updates.
        else: # Agent is in some other active state
            exempt_states = {AGENT_STATUS_AWAITING_CG_REVIEW, AGENT_STATUS_AWAITING_USER_REVIEW_CG}
            if target_agent.status not in exempt_states:
                target_agent.needs_priority_recheck = True
                logger.info(f"InteractionHandler: Agent {target_agent.agent_id} ({target_agent.persona}) is in status {target_agent.status}. Flagged for priority recheck due to new message from '{sender_id}'.")
                await self._manager.send_to_ui({
                    "type": "status", # This type is generic, could be more specific if UI handles it
                    "agent_id": resolved_target_id,
                    "content": f"Message received from @{sender_id} ({sender_agent.persona}). Agent busy, flagged for recheck."
                })
            else: # Agent is in an exempt system-paused state
                logger.info(f"InteractionHandler: Agent {target_agent.agent_id} ({target_agent.persona}) is in system-paused status {target_agent.status}. New message from '{sender_id}' added to history, but agent will not be flagged or rescheduled by this handler.")
                await self._manager.send_to_ui({
                    "type": "status",
                    "agent_id": resolved_target_id,
                    "content": f"Message received from @{sender_id} ({sender_agent.persona}). Agent paused, message queued."
                })
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
        The AgentCycleHandler is responsible for any subsequent agent reactivation.
        """
        if not self._manager.tool_executor:
            logger.error("InteractionHandler: ToolExecutor unavailable in AgentManager. Cannot execute tool.")
            return {"call_id": call_id, "name": tool_name, "content": "[ToolExec Error: ToolExecutor unavailable]", "_raw_result": None} 

        tool_info = {"name": tool_name, "call_id": call_id}
        agent.set_status(AGENT_STATUS_EXECUTING_TOOL, tool_info=tool_info)
        raw_result: Optional[Any] = None
        result_content: str = "[Tool Execution Error: Unknown]"

        # --- Special Handling for send_message ---
        if tool_name == SendMessageTool.name:
            logger.debug(f"InteractionHandler: Intercepting '{tool_name}' for direct handling.")
            target_id = tool_args.get("target_agent_id")
            message_content = tool_args.get("message_content")
            if not target_id or message_content is None:
                result_content = "[ToolExec Error: `target_agent_id` and `message_content` are required for send_message.]"
                raw_result = {"status": "error", "message": result_content}
            else:
                await self.route_and_activate_agent_message(
                    sender_id=agent.agent_id,
                    target_identifier=target_id,
                    message_content=message_content
                )
                # The tool result for the *sender* is a simple confirmation.
                result_content = f"Message routing to agent '{target_id}' initiated by manager."
                raw_result = {"status": "success", "message": result_content}

            # Reset agent status after handling
            if agent.status == AGENT_STATUS_EXECUTING_TOOL:
                agent.set_status(AGENT_STATUS_PROCESSING)
            return {"call_id": call_id, "name": tool_name, "content": result_content, "_raw_result": raw_result}
        # --- End Special Handling ---

        try:
            logger.debug(f"InteractionHandler: Executing tool '{tool_name}' (ID: {call_id}) for '{agent.agent_id}' with context Project: {project_name}, Session: {session_name}")
            raw_result = await self._manager.tool_executor.execute_tool(
                agent_id=agent.agent_id,
                agent_sandbox_path=agent.sandbox_path,
                tool_name=tool_name,
                tool_args=tool_args,
                project_name=project_name,
                session_name=session_name,
                manager=self._manager
            )
            logger.debug(f"InteractionHandler: Tool '{tool_name}' completed execution.")
            
            # --- MODIFIED: Handle signal from ManageTeamTool for further processing ---
            if tool_name == ManageTeamTool.name and isinstance(raw_result, dict) and raw_result.get("status") == "success_signal_to_handler":
                logger.info(f"InteractionHandler: Received signal from ManageTeamTool for action '{raw_result.get('action_to_perform')}'. Processing further...")
                handler_success, handler_message, handler_data = await self.handle_manage_team_action(
                    action_to_perform=raw_result.get("action_to_perform"),
                    action_params=raw_result.get("action_params", {}),
                    calling_agent_id=agent.agent_id # The agent who originally called ManageTeamTool
                )
                # Format the final result for the agent that called ManageTeamTool
                result_content = f"[ManageTeam Result for '{raw_result.get('action_to_perform')}']: {handler_message}"
                if handler_data:
                    try: result_content += f"\nData: {json.dumps(handler_data, indent=2)}"
                    except TypeError: result_content += "\nData: [Unserializable]"
                # raw_result should be the result from handle_manage_team_action for history/logging
                raw_result = {"success": handler_success, "message": handler_message, "data": handler_data}
            # --- END MODIFIED ---
            elif isinstance(raw_result, str):
                result_content = raw_result
            else: # Other tools that might return non-string (should be rare now)
                 try: result_content = json.dumps(raw_result, indent=2)
                 except TypeError: result_content = str(raw_result)

        except Exception as e:
            error_msg = f"InteractionHandler: Error executing tool '{tool_name}': {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            result_content = f"[ToolExec Error: {error_msg}]"
            raw_result = None # Ensure raw_result is None on exception
        finally:
            if agent.status == AGENT_STATUS_EXECUTING_TOOL and agent.current_tool_info and agent.current_tool_info.get("call_id") == call_id:
                agent.set_status(AGENT_STATUS_PROCESSING)
                logger.debug(f"InteractionHandler: Set agent '{agent.agent_id}' status to PROCESSING after tool '{tool_name}'.")

            # Worker activation logic (if task assigned successfully via ProjectManagementTool)
            if tool_name == ProjectManagementTool.name and isinstance(raw_result, dict) and raw_result.get("status") == "success":
                action_performed = tool_args.get("action") # Get action from original tool_args
                assignee_id = raw_result.get("assignee")

                # Get the task description from the tool's result.
                # ProjectManagementTool's modify_task and add_task actions are designed to return
                # the correct, original/semantic description in the "description" field of their result.
                task_description_for_worker = raw_result.get("description")
                task_identifier_for_activation = raw_result.get("task_uuid") or str(raw_result.get("task_id", "N/A"))

                if assignee_id and task_description_for_worker and action_performed in ["add_task", "modify_task"]:
                    logger.info(f"InteractionHandler: Task '{action_performed}' successful for assignee '{assignee_id}'. Attempting worker activation via AgentManager.")

                    # Call the new AgentManager method to handle activation
                    await self._manager.activate_worker_with_task_details(
                        worker_agent_id=assignee_id,
                        task_id_from_tool=task_identifier_for_activation,
                        task_description_from_tool=task_description_for_worker
                    )

                    # Notification to the PM about worker activation can be added here if desired,
                    # or handled by the PM agent itself when it processes the successful tool result.
                    # For now, the activation is logged by activate_worker_with_task_details.
                    # If direct PM notification from here is needed, it can be added.
                    # Example:
                    # pm_agent = agent
                    # brief_task_desc = (task_description_for_worker[:70] + '...') if task_description_for_worker and len(task_description_for_worker) > 70 else task_description_for_worker
                    # notification_content = f"[Framework Notification]: Worker '{assignee_id}' activated for task '{brief_task_desc}'."
                    # pm_notification_message: MessageDict = {"role": "tool", "tool_call_id": f"worker_activation_{assignee_id}", "name": "framework_notification", "content": notification_content}
                    # pm_agent.message_history.append(pm_notification_message)
                    # if self._manager.current_session_db_id:
                    #     await self._manager.db_manager.log_interaction(session_id=self._manager.current_session_db_id, agent_id=pm_agent.agent_id, role="system_framework_notification", content=notification_content)
                    # await self._manager.send_to_ui({"type": "framework_notification_to_pm", "pm_agent_id": pm_agent.agent_id, "worker_agent_id": assignee_id, "task_description": task_description_for_worker, "message": notification_content})

                elif assignee_id and not task_description_for_worker and action_performed in ["add_task", "modify_task"]:
                    logger.warning(f"InteractionHandler: Task '{action_performed}' successful for assignee '{assignee_id}', but no task description was found in the tool result. Worker cannot be properly activated with task details.")
                elif assignee_id and task_description_for_worker and action_performed not in ["add_task", "modify_task"]:
                    logger.debug(f"InteractionHandler: ProjectManagementTool action '{action_performed}' was successful but is not an assignment action. No worker activation needed from this handler.")


        return {"call_id": call_id, "name": tool_name, "content": result_content, "_raw_result": raw_result} 


    async def failed_tool_result(self, call_id: Optional[str], tool_name: Optional[str]) -> Optional[ToolResultDict]:
        """
        Generates a formatted error result dictionary for failed tool dispatch/validation.
        """
        error_content = f"[ToolExec Error: Failed dispatch for '{tool_name or 'unknown'}'. Invalid format or arguments.]";
        final_call_id = call_id or f"invalid_call_{int(time.time())}";
        return {"call_id": final_call_id, "name": tool_name or "unknown_tool", "content": error_content, "_raw_result": {"status": "error", "message": error_content}}