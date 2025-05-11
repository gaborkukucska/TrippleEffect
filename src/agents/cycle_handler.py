# START OF FILE src/agents/cycle_handler.py
import asyncio
import json
import logging
import time
import datetime # Import datetime for timestamp
import re # Import re for health report parsing
from typing import TYPE_CHECKING, Dict, Any, Optional, List, AsyncGenerator

# Import base types and Agent class
from src.llm_providers.base import ToolResultDict, MessageDict
from src.agents.core import Agent

# --- NEW: Import status, state, and other constants ---
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_PROCESSING, AGENT_STATUS_PLANNING,
    AGENT_STATUS_EXECUTING_TOOL, AGENT_STATUS_AWAITING_TOOL,
    AGENT_STATUS_ERROR,
    # Workflow States (Import all for clarity)
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK, # Admin-specific
    PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE,
    WORKER_STATE_STARTUP, WORKER_STATE_WORK, WORKER_STATE_WAIT,
    # Agent Types
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER, # Import all types
    # Other Constants
    REQUEST_STATE_TAG_PATTERN, # Import the compiled pattern
    # Import retry/error constants
    RETRYABLE_EXCEPTIONS, RETRYABLE_STATUS_CODES, KEY_RELATED_ERRORS, KEY_RELATED_STATUS_CODES
)
# --- END NEW ---

# --- Import settings ---
from src.config.settings import settings
# --- End Import settings ---

# Import tools for type checking/logic if needed
from src.tools.manage_team import ManageTeamTool
from src.tools.send_message import SendMessageTool
from src.tools.tool_information import ToolInformationTool # Import ToolInformationTool

# Import specific exception types
import openai

# Type hinting for AgentManager and InteractionHandler
if TYPE_CHECKING:
    from src.agents.manager import AgentManager, BOOTSTRAP_AGENT_ID # Import BOOTSTRAP_AGENT_ID
    from src.agents.interaction_handler import AgentInteractionHandler

# --- Define logger BEFORE potential use in except block ---
logger = logging.getLogger(__name__)
# --- End logger definition ---

# --- NEW: Import PROVIDER_LEVEL_ERRORS ---
# Import cautiously to avoid circular dependencies if possible,
# though failover_handler doesn't import cycle_handler.
try:
    from src.agents.failover_handler import PROVIDER_LEVEL_ERRORS
except ImportError:
    logger.warning("Could not import PROVIDER_LEVEL_ERRORS from failover_handler. Redefining locally.") # logger is now defined
    # Re-define locally as a fallback if import fails
    import aiohttp, asyncio, openai
    PROVIDER_LEVEL_ERRORS = (
        aiohttp.ClientConnectorError,
        asyncio.TimeoutError,
        openai.APIConnectionError,
    )
# --- END NEW ---

# Constants
HEALTH_REPORT_HISTORY_LOOKBACK = 10 # How many recent messages to check for the report

# Retry/Error constants imported above


class AgentCycleHandler:
    """
    Handles the agent execution cycle, including retries for transient errors
    and triggering failover (via AgentManager) for persistent/fatal errors.
    Handles the planning phase by auto-approving plans and reactivating the agent.
    Records performance metrics. Passes exception objects to failover handler.
    Injects current time context and a system health report for Admin AI calls.
    Logs interactions to the database.
    Allows multiple tool calls (of same or different types) in one turn.
    Uses retry/failover limits from settings.
    Handles initial mandatory tool call for PM agent in 'work' state.
    Passes max_tokens override to Agent.process_message based on state.
    Injects task description and tool list for worker's first 'work' cycle.
    """
    def __init__(self, manager: 'AgentManager', interaction_handler: 'AgentInteractionHandler'):
        self._manager = manager
        self._interaction_handler = interaction_handler
        # Import BOOTSTRAP_AGENT_ID from manager to avoid module-level import issues potentially
        from src.agents.manager import BOOTSTRAP_AGENT_ID
        self.BOOTSTRAP_AGENT_ID = BOOTSTRAP_AGENT_ID
        # Use the imported compiled pattern directly
        self.request_state_pattern = REQUEST_STATE_TAG_PATTERN
        if not self.request_state_pattern:
             logger.error("REQUEST_STATE_TAG_PATTERN failed to compile during import!")
        logger.info("AgentCycleHandler initialized.")

    # --- System Health Report Helper ---
    async def _generate_system_health_report(self, agent: Agent) -> Optional[str]:
        """Generates a concise report based on the agent's recent history."""
        if not agent or not agent.message_history: return None

        try:
            recent_messages = agent.message_history[-HEALTH_REPORT_HISTORY_LOOKBACK:]
            tool_success_count = 0; tool_failure_count = 0; error_details = []; key_events = []

            for msg in reversed(recent_messages):
                role = msg.get("role"); content = msg.get("content", ""); call_id = msg.get("tool_call_id")
                if role == 'assistant':
                    # Stop looking after last assistant output unless it was just a plan or state request
                    if '<plan>' not in content and '<request_state' not in content:
                         break
                elif role == 'tool':
                     is_error = content.strip().lower().startswith(("[toolexec error:", "error:", "[manager error:"))
                     if is_error:
                         tool_failure_count += 1
                         error_summary = content.split('\n')[0]; error_details.append(f"- Tool Call {call_id or 'N/A'}: {error_summary[:150]}{'...' if len(error_summary) > 150 else ''}")
                     else: tool_success_count += 1
                elif role == 'system_feedback':
                     if '[Manager Result' in content:
                         success_match = re.search(r'Success=(\w+)', content); action_match = re.search(r'for (\w+)', content); action_name = action_match.group(1) if action_match else 'unknown action'
                         if success_match and success_match.group(1).lower() == 'true':
                             tool_success_count += 1
                             data_match = re.search(r'Data:\s*(\{.*?\})\s*$', content, re.DOTALL | re.IGNORECASE)
                             if data_match:
                                 try: data_dict = json.loads(data_match.group(1))
                                 except json.JSONDecodeError: data_dict = None
                                 if data_dict:
                                      if 'created_agent_id' in data_dict: key_events.append(f"Agent '{data_dict['created_agent_id']}' created.")
                                      elif 'created_team_id' in data_dict: key_events.append(f"Team '{data_dict['created_team_id']}' created.")
                                      # Add more key events as needed
                         elif success_match and success_match.group(1).lower() == 'false':
                             tool_failure_count += 1; error_summary = content.split('Message: ')[-1]; error_details.append(f"- Feedback ({action_name}): {error_summary[:150]}{'...' if len(error_summary) > 150 else ''}")
                         else: logger.debug(f"Could not parse success status from system_feedback: {content[:100]}"); tool_success_count += 1
                elif role == 'system_error':
                     tool_failure_count += 1; error_summary = content.split('\n')[0]; error_details.append(f"- System Error: {error_summary[:150]}{'...' if len(error_summary) > 150 else ''}")

            if tool_failure_count == 0 and not key_events:
                # Make the "OK" report less ambiguous for the LLM
                return "[Framework Internal Status: Last turn OK. This is not a user query.]"

            report_lines = ["[System Health Report - Previous Turn]"]
            if tool_success_count > 0 or tool_failure_count > 0: report_lines.append(f"- Tool Executions: {tool_success_count} succeeded, {tool_failure_count} failed.")
            if error_details: report_lines.append("- Errors/Warnings:"); report_lines.extend(error_details[:3]);
            if len(error_details) > 3: report_lines.append("  ...")
            if key_events: report_lines.append("- Key Events:"); report_lines.extend([f"  - {evt}" for evt in key_events[:3]]);
            if len(key_events) > 3: report_lines.append("  ...")
            return "\n".join(report_lines)
        except Exception as e:
            logger.error(f"Error generating system health report for agent {agent.agent_id}: {e}", exc_info=True)
            return "[System Health Report - Error generating report]"
    # --- End Health Report Helper ---

    async def run_cycle(self, agent: Agent, retry_count: int = 0):
        # --- VERY FIRST LINE LOGGING ---
        logger.critical(f"!!! CycleHandler: run_cycle TASK STARTED for Agent '{agent.agent_id}' (Retry: {retry_count}) !!!")
        # --- END VERY FIRST LINE LOGGING ---
        # --- ADDED LOGGING ---
        logger.info(f"CycleHandler: run_cycle ENTERED for Agent '{agent.agent_id}' (Retry: {retry_count}).")
        # --- END ADDED LOGGING ---

        # Uses imported constants
        agent_id = agent.agent_id
        state_before_cycle = agent.state # Store state at the beginning
        current_provider = agent.provider_name
        current_model = agent.model
        # --- Use settings for limits ---
        max_retries = settings.MAX_STREAM_RETRIES
        retry_delay = settings.RETRY_DELAY_SECONDS
        # --- End Use settings ---
        logger.info(f"CycleHandler: Starting cycle for Agent '{agent_id}' (Model: {current_provider}/{current_model}, Retry: {retry_count}/{max_retries}).")

        agent_generator: Optional[AsyncGenerator[Dict[str, Any], Optional[List[ToolResultDict]]]] = None
        manager_action_feedback: List[Dict] = []
        cycle_completed_successfully = False
        trigger_failover = False
        last_error_obj: Optional[Exception] = None
        last_error_content = ""
        is_retryable_error_type = False
        is_key_related_error = False
        plan_approved_this_cycle = False # Reset flag for this cycle
        state_request_match = None # Initialize here
        generator_finished_normally = False # NEW FLAG

        history_len_before = len(agent.message_history)
        executed_tool_successfully_this_cycle = False # Reset flag for this cycle
        # --- MODIFIED: Default needs_reactivation to FALSE ---
        needs_reactivation_after_cycle = False
        # --- END MODIFIED ---
        start_time = time.perf_counter()
        llm_call_duration_ms = 0.0
        action_taken_this_cycle = False # NEW: Track if a tool call or state change was attempted

        if not hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle = set()
        current_model_key = f"{current_provider}/{current_model}"
        agent._failed_models_this_cycle.add(current_model_key)
        logger.debug(f"Agent '{agent_id}' attempting model '{current_model_key}'. Failed this sequence so far: {agent._failed_models_this_cycle}")

        current_db_session_id = self._manager.current_session_db_id

        # --- WRAP ENTIRE FUNCTION BODY ---
        try:
            # --- Inner try...finally block for core logic ---
            try:
                # --- NEW: Check for WORKER Agent's first cycle in WORK state ---
                is_worker_first_work_cycle = (
                    agent.agent_type == AGENT_TYPE_WORKER and
                    agent.state == WORKER_STATE_WORK and
                    getattr(agent, '_needs_initial_work_context', False) # Check flag set by InteractionHandler
                )
                skip_standard_prompt_fetch = False
                final_system_prompt = "" # Initialize

                if is_worker_first_work_cycle:
                    logger.info(f"CycleHandler: Worker Agent '{agent_id}' first cycle in WORK state. Injecting task/tool context.")
                    agent._needs_initial_work_context = False # Clear the flag

                    # Retrieve task description and tool list (assuming stored on agent by InteractionHandler)
                    task_description = getattr(agent, '_injected_task_description', "[Task description not injected]")
                    available_tools_list_str = getattr(agent, '_injected_tools_list_str', "[Tool list not injected]")
                    # Clear temporary attributes after use
                    if hasattr(agent, '_injected_task_description'): delattr(agent, '_injected_task_description')
                    if hasattr(agent, '_injected_tools_list_str'): delattr(agent, '_injected_tools_list_str')

                    # Get the base prompt template
                    # Ensure workflow_manager exists before accessing _prompt_map
                    prompt_key = "worker_work_prompt" # Default key
                    if hasattr(self._manager, 'workflow_manager') and hasattr(self._manager.workflow_manager, '_prompt_map'):
                         prompt_key = self._manager.workflow_manager._prompt_map.get((AGENT_TYPE_WORKER, WORKER_STATE_WORK), "worker_work_prompt")
                    else:
                         logger.error("WorkflowManager or its _prompt_map not found on AgentManager! Using default prompt key.")

                    prompt_template = settings.PROMPTS.get(prompt_key)

                    if prompt_template:
                        # Manually format the prompt with extra context
                        formatting_context = {
                            "agent_id": agent.agent_id,
                            "persona": agent.persona,
                            "project_name": getattr(self._manager, 'current_project', 'N/A'),
                            "session_name": getattr(self._manager, 'current_session', 'N/A'),
                            "team_id": self._manager.state_manager.get_agent_team(agent.agent_id) or "N/A",
                            "current_time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(sep=' ', timespec='seconds'),
                            # --- Inject the specific task and tool list ---
                            "task_description": task_description,
                            "available_tools_list": available_tools_list_str
                        }
                        try:
                            final_system_prompt = prompt_template.format(**formatting_context)
                            skip_standard_prompt_fetch = True
                            logger.debug(f"CycleHandler: Manually formatted prompt for worker '{agent_id}' first work cycle.")
                        except KeyError as fmt_err:
                            logger.error(f"CycleHandler: Failed to format worker first work prompt '{prompt_key}'. Missing key: {fmt_err}. Falling back.")
                            final_system_prompt = settings.DEFAULT_SYSTEM_PROMPT # Fallback
                        except Exception as e:
                            logger.error(f"CycleHandler: Unexpected error formatting worker first work prompt '{prompt_key}': {e}. Falling back.", exc_info=True)
                            final_system_prompt = settings.DEFAULT_SYSTEM_PROMPT # Fallback
                    else:
                        logger.error(f"CycleHandler: Prompt template for key '{prompt_key}' not found. Using absolute default.")
                        final_system_prompt = settings.DEFAULT_SYSTEM_PROMPT
                # --- End Worker First Cycle Check ---

                # --- Prepare history for LLM call ---
                history_for_call = agent.message_history.copy() # Start with current history

                # --- Get State-Specific Prompt (if not handled above) ---
                if not skip_standard_prompt_fetch: # Only fetch standard prompt if not worker's first work cycle
                    if hasattr(self._manager, 'workflow_manager'):
                        final_system_prompt = self._manager.workflow_manager.get_system_prompt(agent, self._manager)
                        logger.debug(f"CycleHandler: Set system prompt for agent '{agent_id}' (Type: {agent.agent_type}, State: {agent.state}) via WorkflowManager.")
                    else:
                        logger.error("WorkflowManager not found on AgentManager! Cannot get state-specific prompt. Using agent's default.")
                        final_system_prompt = agent.final_system_prompt # Fallback to agent's stored prompt

                # Ensure system prompt is at the start of history
                if not history_for_call or history_for_call[0].get("role") != "system":
                    history_for_call.insert(0, {"role": "system", "content": final_system_prompt})
                else:
                    history_for_call[0] = {"role": "system", "content": final_system_prompt} # Overwrite/set

                # --- Inject System Health Report (Admin AI only) ---
                # (This logic remains, but happens *after* the main system prompt is set)
                if agent.agent_id == self.BOOTSTRAP_AGENT_ID: # Check agent ID, not type
                    system_health_report = await self._generate_system_health_report(agent)
                    if system_health_report: # Append health report if generated
                        health_msg: MessageDict = {"role": "system", "content": system_health_report}
                        # Insert *after* the main system prompt but before other history
                        history_for_call.insert(1, health_msg)
                        logger.debug(f"Injected system health report for {agent_id}")
                # --- End History Preparation (Prompt + Health Report) ---

                # --- Make LLM Call ---
                # --- Pass max_tokens to process_message ---
                agent_generator = agent.process_message(history_override=history_for_call) # Pass potentially modified history
                # --- END ---

                # --- Proceed with event loop ---
                while True:
                    event = None # Initialize event outside try
                    try:
                        event = await agent_generator.asend(None)
                        # --- TEMP LOGGING: Event Received ---
                        logger.debug(f"CYCLE HANDLER RECEIVED Event: {event}")
                        # --- END TEMP LOGGING ---
                    except StopAsyncIteration:
                        logger.info(f"CycleHandler: Agent '{agent_id}' generator finished normally.")
                        cycle_completed_successfully = True
                        generator_finished_normally = True # Set flag

                        # Log the final response normally if no state change was requested during the stream
                        if not needs_reactivation_after_cycle and current_db_session_id is not None:
                            final_content = agent.text_buffer.strip() # Get final content again
                            if final_content: # Only log if there's actual content
                                await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="assistant", content=final_content)
                        # --- END NEW ---
                        break # Exit inner loop as generator finished

                    except Exception as gen_err:
                        logger.error(f"CycleHandler: Generator error for '{agent_id}': {gen_err}", exc_info=True)
                        last_error_obj = gen_err; last_error_content = f"[Manager Error: Unexpected error in generator handler - {gen_err}]"; is_retryable_error_type = False; is_key_related_error = False; trigger_failover = True; break

                    event_type = event.get("type")

                    # --- START STATE/PLAN/TOOL HANDLING ---

                    # Handle plan submission (now specific to Admin AI AND only if in PLANNING state)
                    if event_type == "admin_plan_submitted":
                        action_taken_this_cycle = True # Mark action
                        # --- NEW: Check if agent is actually in PLANNING state ---
                        if agent.agent_id == self.BOOTSTRAP_AGENT_ID and getattr(agent, 'state', None) == ADMIN_STATE_PLANNING:
                            plan_content = event.get("plan_content", "[No Plan Content]")
                            agent_id_from_event = event.get("agent_id") # Should be admin_ai
                            logger.info(f"CycleHandler: Received plan submission from agent '{agent_id_from_event}' (State: PLANNING).")

                            # --- ADD LOGGING ---
                            logger.debug(f"CycleHandler: Attempting to log plan to DB for session {current_db_session_id}...")
                            # --- END LOGGING ---
                            if current_db_session_id is not None:
                                try:
                                    await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="assistant_plan", content=plan_content) # Log the plan
                                    # --- ADD LOGGING ---
                                    logger.debug("CycleHandler: Plan logged to DB successfully.")
                                    # --- END LOGGING ---
                                except Exception as db_log_err:
                                     logger.error(f"Failed to log admin plan to DB: {db_log_err}", exc_info=True)
                            else:
                                logger.warning("Cannot log admin plan to DB: current_session_db_id is None.")

                            # --- Framework Project Creation Logic ---
                            project_title = f"Project_{int(time.time())}" # Default title
                            creation_success = False
                            creation_message = f"[Framework Notification] Failed to initiate project '{project_title}'."
                            pm_agent_id = None # Initialize pm_agent_id

                            try:
                                # Extract title
                                title_match = re.search(r"<title>(.*?)</title>", plan_content, re.IGNORECASE | re.DOTALL)
                                if title_match:
                                    extracted_title = title_match.group(1).strip()
                                    if extracted_title: # Ensure title is not empty
                                        project_title = extracted_title
                                        logger.info(f"Extracted project title: '{project_title}'")
                                    else:
                                        logger.warning("Extracted <title> was empty. Using default project title.")
                                else:
                                    logger.warning("Could not extract <title> from plan. Using default project title.")

                                # --- Call AgentManager to handle creation ---
                                # --- ADD LOGGING ---
                                logger.debug(f"CycleHandler: Calling manager.create_project_and_pm_agent for title '{project_title}'...")
                                # --- END LOGGING ---
                                if hasattr(self._manager, 'create_project_and_pm_agent'):
                                    creation_success, creation_message, pm_agent_id = await self._manager.create_project_and_pm_agent(
                                        project_title=project_title, # Use original title for display/task name
                                        plan_description=plan_content # Pass full plan as description
                                    )
                                    # --- ADD LOGGING ---
                                    logger.debug(f"CycleHandler: manager.create_project_and_pm_agent returned: success={creation_success}, msg='{creation_message}', pm_id={pm_agent_id}")
                                    # --- END LOGGING ---
                                else:
                                     logger.error("AgentManager does not have 'create_project_and_pm_agent' method!")
                                     creation_message = "[Framework Error] Project creation function not implemented."

                            except Exception as creation_err:
                                logger.error(f"Error during framework project/PM creation: {creation_err}", exc_info=True)
                                creation_message = f"[Framework Error] An error occurred during project creation: {creation_err}"
                            # --- End Creation Call ---

                            # --- Inject Confirmation & Set State ---
                            # --- ADD LOGGING ---
                            logger.debug(f"CycleHandler: Preparing to inject confirmation and set state back to conversation.")
                            # --- END LOGGING ---
                            # Use the potentially updated message from create_project_and_pm_agent
                            confirm_msg: MessageDict = {"role": "system", "content": creation_message} # Keep confirmation message
                            agent.message_history.append(confirm_msg)
                            # --- UI notification is now sent from manager.py ---
                            if hasattr(agent, 'set_state'):
                                 # --- Ensure state is set to CONVERSATION after plan submission ---
                                 agent.set_state(ADMIN_STATE_CONVERSATION) # Explicitly set to conversation
                                 logger.info(f"CycleHandler: Set Admin AI state to '{ADMIN_STATE_CONVERSATION}' after plan submission.")
                                 # Log state change?
                                 if current_db_session_id is not None:
                                      try:
                                          await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="agent_state_change", content=f"State changed to: {ADMIN_STATE_CONVERSATION} after plan submission.")
                                      except Exception as db_log_err:
                                           logger.error(f"Failed to log state change to DB: {db_log_err}", exc_info=True)
                            else:
                                 logger.error("Cannot set Admin AI state back to conversation: set_state method missing.")
                            # --- END State Setting ---

                            # --- Ensure no automatic reactivation after plan submission ---
                            # The Admin AI should wait for user input or PM updates in the conversation state.
                            needs_reactivation_after_cycle = False # EXPLICITLY SET TO FALSE
                            logger.debug("CycleHandler: Plan submitted, Admin AI state set to conversation, preventing immediate reactivation.")
                            # --- END Reactivation Prevention ---
                            break # Exit event loop after handling plan
                        else:
                             # Log warning if plan submitted in wrong state
                             logger.warning(f"CycleHandler: Agent '{agent.agent_id}' submitted a plan but was not in PLANNING state. Ignoring plan.")
                             # Continue processing other events? Or break? Let's break and reactivate.
                             needs_reactivation_after_cycle = True
                             break
                    # --- END STATE/PLAN HANDLING ---
                    # --- Handle agent_state_change_requested event ---
                    elif event_type == "agent_state_change_requested":
                        action_taken_this_cycle = True # Mark action
                        requested_state = event.get("requested_state")
                        logger.info(f"CycleHandler: Received state change request to '{requested_state}' from agent '{agent_id}'.")
                        # Use WorkflowManager to handle the state change
                        state_change_success = False
                        if hasattr(self._manager, 'workflow_manager') and requested_state:
                            state_change_success = self._manager.workflow_manager.change_state(agent, requested_state)
                        else:
                            logger.error(f"Cannot process state change request for agent '{agent_id}': WorkflowManager missing or requested_state empty.")

                        if state_change_success:
                             needs_reactivation_after_cycle = True # Reactivate after successful state change
                        else:
                             # Optionally send feedback if state change failed validation?
                             logger.warning(f"State change to '{requested_state}' denied for agent '{agent_id}'. Agent remains in state '{agent.state}'.")
                             # --- Reactivate on FAILED state change ---
                             needs_reactivation_after_cycle = True
                             # --- END ---
                        break # Exit event loop, let finally handle reactivation (or lack thereof)
                    # --- End state change handling ---
                    # --- NEW: Handle agent_thought event ---
                    elif event_type == "agent_thought":
                        # action_taken_this_cycle = True # A thought doesn't count as an 'action' for reactivation logic
                        thought_content = event.get("content")
                        if thought_content:
                            logger.info(f"CycleHandler: Received thought from agent '{agent_id}'. Saving to KB.")
                            # Construct KB save arguments
                            kb_key = f"thought_{agent_id}_{int(time.time())}"
                            kb_entry = {
                                "key": kb_key,
                                "value": thought_content,
                                "tags": ["temp_thought_log", f"agent:{agent_id}"] # Add agent ID tag
                            }
                            kb_args = {
                                "action": "save_knowledge",
                                "entry": kb_entry
                            }
                            # Execute KB save directly using ToolExecutor
                            # Note: This bypasses normal tool result handling/history injection,
                            # which is likely fine for internal logging.
                            try:
                                # Correctly call execute_tool, identifying as 'framework'
                                kb_result = await self._manager.tool_executor.execute_tool(
                                    agent_id="framework",   # Identify call as framework internal
                                    agent_sandbox_path=agent.sandbox_path, # Still use agent's sandbox context
                                    tool_name="knowledge_base",
                                    tool_args=kb_args,      # Pass args via keyword 'tool_args'
                                    project_name=self._manager.current_project, # Optional kwarg
                                    session_name=self._manager.current_session, # Optional kwarg
                                    manager=self._manager   # Pass manager explicitly if needed by auth check (though framework bypasses)
                                )
                                # Check result (knowledge_base returns string on success/error)
                                if isinstance(kb_result, str) and kb_result.startswith("Error:"):
                                    logger.error(f"Failed to save agent '{agent_id}' thought to KB. Result: {kb_result}")
                                elif not isinstance(kb_result, str): # Should be string on success too
                                     logger.warning(f"Unexpected result type saving thought to KB: {type(kb_result)}. Result: {kb_result}")

                            except Exception as kb_err:
                                logger.error(f"Exception saving agent '{agent_id}' thought to KB: {kb_err}", exc_info=True)
                        # Continue: Allow loop to proceed naturally after handling thought
                        continue
                    # --- END NEW ---
                    elif event_type in ["response_chunk", "status", "final_response"]:
                        if "agent_id" not in event: event["agent_id"] = agent_id
                        # --- TEMP LOGGING: Event Before Send ---
                        logger.debug(f"CYCLE HANDLER SENDING Event: {event}")
                        # --- END TEMP LOGGING ---
                        await self._manager.send_to_ui(event)
                        # Log final response normally
                        if event_type == "final_response":
                            final_content = event.get("content", "")
                            if current_db_session_id is not None:
                                 await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="assistant", content=final_content)
                    elif event_type == "error":
                        last_error_obj = event.get('_exception_obj', ValueError(event.get('content', 'Unknown Error')))
                        last_error_content = event.get("content", "[Agent Error: Unknown error from provider]")
                        logger.error(f"CycleHandler: Agent '{agent_id}' reported error event: {last_error_content}")

                        # --- REVISED ERROR CHECKING ORDER ---
                        # 1. Check for Provider-Level Errors (should trigger failover immediately)
                        if isinstance(last_error_obj, PROVIDER_LEVEL_ERRORS):
                            logger.warning(f"CycleHandler: Agent '{agent_id}' encountered provider-level error: {type(last_error_obj).__name__}. Triggering failover.")
                            is_retryable_error_type = False
                            is_key_related_error = False # Provider errors aren't key errors per se
                            trigger_failover = True

                        # 2. Check for Key-Related Errors (should trigger failover/key cycle)
                        elif isinstance(last_error_obj, KEY_RELATED_ERRORS) or \
                             (isinstance(last_error_obj, openai.APIStatusError) and last_error_obj.status_code in KEY_RELATED_STATUS_CODES):
                            logger.warning(f"CycleHandler: Agent '{agent_id}' encountered key-related/rate-limit error: {type(last_error_obj).__name__}. Triggering failover/key cycle.")
                            is_retryable_error_type = False
                            is_key_related_error = True
                            trigger_failover = True

                        # 3. Check for other Retryable Errors (retry same config up to limit)
                        elif isinstance(last_error_obj, RETRYABLE_EXCEPTIONS) or \
                             (isinstance(last_error_obj, openai.APIStatusError) and last_error_obj.status_code in RETRYABLE_STATUS_CODES):
                            logger.warning(f"CycleHandler: Agent '{agent_id}' encountered retryable error: {type(last_error_obj).__name__}. Will retry same config if limit not reached.")
                            is_retryable_error_type = True
                            is_key_related_error = False
                            trigger_failover = False # Don't trigger fail 

                        # 5. Check for Tool Execution Errors (should not trigger failover)
                        elif "ToolExec Error" in last_error_content:
                            logger.warning(f"CycleHandler: Agent '{agent_id}' encountered tool execution error: {last_error_content}. Not triggering failover.")
                            is_retryable_error_type = True # Still retryable from LLM perspective
                            is_key_related_error = False
                            trigger_failover = False

                        # 4. All other errors (non-retryable, non-key, non-provider) -> Failover
                        else:
                            logger.warning(f"CycleHandler: Agent '{agent_id}' encountered non-retryable/unknown error: {type(last_error_obj).__name__}. Triggering failover.")
                            is_retryable_error_type = False
                            is_key_related_error = False
                            trigger_failover = True

                        if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="system_error", content=last_error_content)
                        break # Exit the event processing loop on any error
                    elif event_type == "tool_requests":
                        action_taken_this_cycle = True # Mark action
                        all_tool_calls: List[Dict] = event.get("calls", [])
                        agent_response_content = event.get("raw_assistant_response")
                        if not all_tool_calls: continue
                        logger.info(f"CycleHandler: Agent '{agent_id}' yielded {len(all_tool_calls)} tool request(s).")
                        if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="assistant", content=agent_response_content, tool_calls=all_tool_calls)
                        else: logger.warning("Cannot log assistant tool request response to DB: current_session_db_id is None.")

                        # --- MODIFIED: REMOVE BATCHING ENFORCEMENT ---
                        # tool_names_in_batch = {call.get("name") for call in all_tool_calls if call.get("name")}
                        # if len(tool_names_in_batch) > 1:
                        #     violation_msg = f"Error: Agent '{agent_id}' attempted to call multiple tool types in one turn ({', '.join(sorted(tool_names_in_batch))}). Only one tool type per response is allowed."
                        #     logger.error(violation_msg)
                        #     first_call_id = all_tool_calls[0].get('id', f"batch_violation_{int(time.time())}")
                        #     tool_feedback: Optional[ToolResultDict] = await self._interaction_handler.failed_tool_result(first_call_id, ", ".join(sorted(tool_names_in_batch)))
                        #     if tool_feedback:
                        #          tool_feedback["content"] = f"[Framework Rule Violation]: {violation_msg}"
                        #          agent.message_history.append({"role": "tool", **tool_feedback})
                        #          if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="system_feedback", content=tool_feedback["content"], tool_results=[tool_feedback])
                        #     needs_reactivation_after_cycle = True; break
                        # --- END MODIFIED: REMOVE BATCHING ENFORCEMENT ---
                        logger.info(f"CycleHandler: Agent '{agent_id}' requested {len(all_tool_calls)} tools. Executing sequentially.")


                        # --- Process all tool calls and provide feedback ---
                        tool_results = []
                        calls_to_execute = all_tool_calls
                        executed_tool_successfully_this_cycle = False # Initialize here for this batch
                        for call_index, call in enumerate(calls_to_execute): # Added call_index for logging
                            call_id = call.get("id"); tool_name = call.get("name"); tool_args = call.get("arguments", {})
                            logger.info(f"CycleHandler: Executing tool call {call_index + 1}/{len(calls_to_execute)} for agent '{agent_id}': Tool='{tool_name}', Args='{tool_args}'")

                            # --- REMOVED: Defaulting 'action' for tool_information here. Let the tool or parser handle defaults. ---
                            # if tool_name == "tool_information" and "action" not in tool_args:
                            #     tool_args["action"] = "list_tools"
                            #     logger.debug(f"CycleHandler: Defaulting 'action' to 'list_tools' for tool_information call from agent '{agent_id}'.")
                            # --- END REMOVED ---

                            if not (call_id and tool_name and isinstance(tool_args, dict)):
                                # Invalid call format, generate failed tool result
                                fail_res = await self._interaction_handler.failed_tool_result(call_id, tool_name)
                                if fail_res: tool_results.append(fail_res)
                            else:
                                result = await self._interaction_handler.execute_single_tool(agent, call_id, tool_name, tool_args, project_name=self._manager.current_project, session_name=self._manager.current_session)
                                tool_results.append(result)
                                # Consider a tool execution successful if it doesn't start with "Error:"
                                if isinstance(result, dict) and isinstance(result.get("content"), str) and not result.get("content", "").lower().startswith("error:"):
                                    executed_tool_successfully_this_cycle = True # Set to true if ANY tool in batch succeeds

                        # Append tool results to agent's message history and log to DB
                        for res in tool_results:
                            if res:
                                tool_msg: MessageDict = {"role": "tool", "tool_call_id": res.get("call_id"), "content": str(res.get("content", ""))}
                                agent.message_history.append(tool_msg)
                                if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="tool", content=str(res.get("content", "")), tool_results=[res])

                        # Send tool results to UI
                        for res in tool_results:
                            if res:
                                await self._manager.send_to_ui({
                                    "type": "tool_result",
                                    "agent_id": agent_id,
                                    "call_id": res.get("call_id"),
                                    "name": res.get("name"), # Send tool name to UI
                                    "content": str(res.get("content", ""))
                                })

                        # --- MODIFIED Reactivation Logic for Tool Execution ---
                        if executed_tool_successfully_this_cycle:
                            # Default to reactivating after successful tool use
                            needs_reactivation_after_cycle = True
                            logger.debug(f"CycleHandler: Tool(s) executed. Setting needs_reactivation_after_cycle=True initially for agent '{agent_id}'.")

                            # Specific conditions to NOT reactivate
                            if agent.agent_type == AGENT_TYPE_ADMIN and (agent.state in [ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION]):
                                needs_reactivation_after_cycle = False
                                logger.debug(f"CycleHandler: Not reactivating Admin AI '{agent_id}' in state '{agent.state}' after tool use.")
                            elif agent.agent_type == AGENT_TYPE_PM and (agent.state == PM_STATE_MANAGE):
                                needs_reactivation_after_cycle = False
                                logger.debug(f"CycleHandler: Not reactivating PM Agent '{agent_id}' in state '{agent.state}' after tool use.")
                            elif agent.agent_type == AGENT_TYPE_WORKER and (agent.state == WORKER_STATE_WAIT):
                                needs_reactivation_after_cycle = False
                                logger.debug(f"CycleHandler: Not reactivating Worker Agent '{agent_id}' in state '{agent.state}' after tool use.")
                            # Check for specific "final step & stop" messages (less reliable)
                            elif any(stop_phrase in agent_response_content.lower() for stop_phrase in ["final step & stop", "task complete. stopping."]):
                                needs_reactivation_after_cycle = False
                                logger.info(f"CycleHandler: Agent '{agent_id}' indicated task completion. Not reactivating.")
                        else:
                            # If tool execution failed (e.g., parsing error, tool error response), always reactivate to let agent try again or report.
                            needs_reactivation_after_cycle = True
                            logger.warning(f"CycleHandler: Tool execution failed or no successful tool ran for agent '{agent_id}'. Setting needs_reactivation_after_cycle=True.")
                        # --- END MODIFIED Reactivation Logic ---

                        # Manager action feedback (e.g., from manage_team)
                        if manager_action_feedback:
                             feedback_appended = False
                             for fb in manager_action_feedback:
                                 fb_content = f"[Manager Result for {fb.get('action', 'N/A')} (Call ID: {fb['call_id']})]: Success={fb['success']}. Message: {fb['message']}"
                                 if fb.get("data"):
                                     try: data_str = json.dumps(fb['data'], indent=2); fb_content += f"\nData:\n{data_str[:1500]}{'... (truncated)' if len(data_str) > 1500 else ''}"
                                     except TypeError: fb_content += "\nData: [Unserializable]"
                                 fb_msg: MessageDict = {"role": "tool", "tool_call_id": fb['call_id'], "content": fb_content}
                                 if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("content") != fb_content: agent.message_history.append(fb_msg); feedback_appended = True
                                 if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="system_feedback", content=fb_content, tool_results=[fb])

                                 is_admin_creating_pm = (agent_id == self.BOOTSTRAP_AGENT_ID and fb.get('action') == 'create_agent')
                                 if not is_admin_creating_pm:
                                     await self._manager.send_to_ui({
                                         "type": "system_feedback",
                                         "agent_id": agent_id,
                                         "call_id": fb['call_id'],
                                         "action": fb.get('action', 'N/A'),
                                         "content": fb_content
                                     })
                        break # Break event loop after tool processing
                    else: logger.warning(f"CycleHandler: Unknown event type '{event_type}' from '{agent_id}'.")

            # --- Inner Exception Handling ---
            except Exception as e:
                logger.error(f"CycleHandler: Error during core processing for '{agent_id}': {e}", exc_info=True)
                last_error_obj = e; last_error_content = f"[Manager Error: Unexpected error in cycle handler - {e}]"; is_retryable_error_type = False; is_key_related_error = False; trigger_failover = True; cycle_completed_successfully = False
                try: await self._manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": last_error_content})
                except Exception as ui_err: logger.error(f"Error sending error status to UI in cycle handler: {ui_err}")
                if current_db_session_id is not None:
                     try: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="system_error", content=last_error_content)
                     except Exception as db_log_err: logger.error(f"Failed to log handler error to DB: {db_log_err}", exc_info=True)

            finally:
                # --- RE-ADD Explicit Generator Closing ---
                logger.debug(f"CycleHandler: Reached finally block for '{agent_id}'. Generator finished normally: {generator_finished_normally}, Error caught: {bool(last_error_obj)}")
                if agent_generator and not generator_finished_normally:
                    try:
                        logger.debug(f"Attempting explicit aclose() on generator for '{agent_id}' in finally block.")
                        await agent_generator.aclose()
                        logger.debug(f"Explicit aclose() successful for '{agent_id}' in finally block.")
                    except RuntimeError as close_err:
                        if "already running" in str(close_err): logger.warning(f"Generator aclose() error in finally (already running/closed?): {close_err}")
                        else: raise # Re-raise unexpected RuntimeError
                    except Exception as close_err: logger.error(f"Unexpected error during explicit aclose() in finally for '{agent_id}': {close_err}", exc_info=True)
                # --- END RE-ADD ---

                end_time = time.perf_counter(); llm_call_duration_ms = (end_time - start_time) * 1000
                call_success_for_metrics = (cycle_completed_successfully or executed_tool_successfully_this_cycle) and not trigger_failover and not plan_approved_this_cycle

                logger.debug(f"Cycle outcome for {current_provider}/{current_model} (Retry: {retry_count}): SuccessForMetrics={call_success_for_metrics}, TriggerFailover={trigger_failover}, PlanApproved={plan_approved_this_cycle}, NeedsReactivation={needs_reactivation_after_cycle}, Duration={llm_call_duration_ms:.2f}ms, Error? {bool(last_error_obj)}")

                if not plan_approved_this_cycle:
                     try: await self._manager.performance_tracker.record_call(provider=current_provider, model_id=current_model, duration_ms=llm_call_duration_ms, success=call_success_for_metrics)
                     except Exception as record_err: logger.error(f"Failed to record performance metrics for {current_provider}/{current_model}: {record_err}", exc_info=True)

                # --- Final Action Logic (Uses settings for retry/failover) ---
                if trigger_failover:
                    logger.warning(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) requires failover. Error Type: {type(last_error_obj).__name__ if last_error_obj else 'N/A'}. Triggering failover handler.")
                    if agent_generator and not generator_finished_normally:
                        try:
                            logger.debug(f"Attempting explicit aclose() on generator for '{agent_id}' before failover (redundant check).")
                            await agent_generator.aclose()
                            logger.debug(f"Explicit aclose() successful for '{agent_id}' before failover (redundant check).")
                        except RuntimeError as close_err:
                            if "already running" in str(close_err): logger.warning(f"Generator aclose() error before failover (redundant check, already running/closed?): {close_err}")
                            else: raise # Re-raise unexpected RuntimeError
                        except Exception as close_err: logger.error(f"Unexpected error during explicit aclose() before failover (redundant check) for '{agent_id}': {close_err}", exc_info=True)

                    failover_successful = await self._manager.handle_agent_model_failover(agent_id, last_error_obj)
                    logger.debug(f"CycleHandler: Received failover_successful = {failover_successful} from handle_agent_model_failover for agent '{agent_id}'.")
                    if failover_successful:
                        logger.info(f"CycleHandler: Failover successful for agent '{agent_id}'. Agent config updated. Re-scheduling cycle attempt with new config.")
                        try:
                            loop = asyncio.get_running_loop()
                            if not loop.is_closed():
                                loop.create_task(self.run_cycle(agent, 0))
                                logger.info(f"CycleHandler: Re-scheduled run_cycle for agent '{agent_id}' with new configuration.")
                            else:
                                logger.warning(f"CycleHandler: Event loop closed. Cannot re-schedule cycle after successful failover for agent '{agent_id}'.")
                        except RuntimeError as loop_err:
                            logger.warning(f"CycleHandler: Could not get running loop to schedule failover retry cycle for agent '{agent_id}': {loop_err}")
                        except Exception as schedule_err:
                              logger.error(f"CycleHandler: FAILED to create asyncio task for re-scheduling cycle after failover of '{agent_id}': {schedule_err}", exc_info=True)
                    else:
                        logger.error(f"CycleHandler: Failover handler exhausted all options for agent '{agent_id}'. Agent remains in ERROR state.")

                elif needs_reactivation_after_cycle: # Check reactivation after other conditions
                    reactivation_reason = "unknown condition"
                    current_agent_state_in_finally = getattr(agent, 'state', None)
                    if current_agent_state_in_finally == ADMIN_STATE_PLANNING: reactivation_reason = "state change to planning"
                    elif current_agent_state_in_finally == ADMIN_STATE_CONVERSATION: reactivation_reason = "state change to conversation"
                    elif current_agent_state_in_finally == ADMIN_STATE_WORK_DELEGATED: reactivation_reason = "state change to work_delegated"
                    elif plan_approved_this_cycle: reactivation_reason = "plan approved by user"
                    elif executed_tool_successfully_this_cycle: reactivation_reason = "successful tool execution"

                    logger.info(f"CycleHandler: Reactivating agent '{agent_id}' ({current_model_key}) after {reactivation_reason}.")

                    if agent_generator and not generator_finished_normally:
                        try:
                            logger.debug(f"Attempting explicit aclose() on generator for '{agent_id}' before reactivation (redundant check).")
                            await agent_generator.aclose()
                            logger.debug(f"Explicit aclose() successful for '{agent_id}' before reactivation (redundant check).")
                        except RuntimeError as close_err:
                            if "already running" in str(close_err): logger.warning(f"Generator aclose() error before reactivation (redundant check, already running/closed?): {close_err}")
                            else: raise # Re-raise unexpected RuntimeError
                        except Exception as close_err: logger.error(f"Unexpected error during explicit aclose() before reactivation (redundant check) for '{agent_id}': {close_err}", exc_info=True)

                    agent.set_status(AGENT_STATUS_IDLE)
                    await asyncio.sleep(0.01) # Small delay
                    logger.debug(f"CycleHandler: Preparing to schedule next cycle for '{agent_id}' via asyncio.create_task...")
                    current_agent_state_in_finally = getattr(agent, 'state', 'N/A')
                    logger.info(f"CycleHandler: Agent '{agent_id}' state in finally block *before* scheduling reactivation: {current_agent_state_in_finally}")

                    try:
                        loop = asyncio.get_running_loop()
                        if not loop.is_closed():
                            task = loop.create_task(self._manager.schedule_cycle(agent, 0)) # Use loop.create_task
                            logger.info(f"CycleHandler: Successfully created asyncio task {task.get_name()} for next cycle of '{agent_id}'.")
                            await asyncio.sleep(0) # Yield control briefly
                            logger.debug(f"CycleHandler: asyncio.sleep(0) completed after scheduling task for {agent_id}.")
                        else:
                            logger.warning(f"CycleHandler: Event loop closed. Cannot schedule next cycle for agent '{agent_id}'.")
                    except RuntimeError as loop_err: # Catch 'no running loop' error specifically
                        logger.warning(f"CycleHandler: Could not get running loop to schedule next cycle for agent '{agent_id}': {loop_err}")
                    except Exception as schedule_err:
                        logger.error(f"CycleHandler: FAILED to create asyncio task for next cycle of '{agent_id}': {schedule_err}", exc_info=True)

                elif is_retryable_error_type and retry_count < settings.MAX_STREAM_RETRIES: # Check retry THIRD
                     logger.warning(f"CycleHandler: Transient error for '{agent_id}' on {current_model_key}. Retrying same model/key in {settings.RETRY_DELAY_SECONDS:.1f}s ({retry_count + 1}/{settings.MAX_STREAM_RETRIES})... Last Error: {last_error_content}")
                     await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Provider issue... Retrying '{current_model}' (Attempt {retry_count + 2})..."})
                     await asyncio.sleep(settings.RETRY_DELAY_SECONDS) # Use settings
                     agent.set_status(AGENT_STATUS_IDLE)
                     try:
                         loop = asyncio.get_running_loop()
                         if not loop.is_closed():
                             loop.create_task(self._manager.schedule_cycle(agent, retry_count + 1))
                         else:
                             logger.warning(f"CycleHandler: Event loop closed. Cannot schedule retry cycle for agent '{agent_id}'.")
                     except RuntimeError as loop_err:
                         logger.warning(f"CycleHandler: Could not get running loop to schedule retry cycle for agent '{agent_id}': {loop_err}")
                     except Exception as schedule_err:
                         logger.error(f"CycleHandler: FAILED to create asyncio task for retry cycle of '{agent_id}': {schedule_err}", exc_info=True)

                # --- MODIFIED: Special reactivation logic for PM in startup if no action was taken ---
                elif agent.agent_type == AGENT_TYPE_PM and agent.state == PM_STATE_STARTUP and not action_taken_this_cycle and call_success_for_metrics:
                    logger.warning(f"CycleHandler: PM agent '{agent_id}' in PM_STARTUP finished cleanly but took NO ACTION (no tool call/state change). Reactivating to enforce startup workflow.")
                    if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear() # Clear if it was a good model but bad output
                    agent.set_status(AGENT_STATUS_IDLE)
                    asyncio.create_task(self._manager.schedule_cycle(agent, 0)) # Reactivate
                # --- END MODIFIED ---

                elif call_success_for_metrics: # Check normal completion FOURTH (but after PM startup check)
                    history_len_after = len(agent.message_history)
                    if history_len_after > history_len_before and agent.message_history[-1].get("role") == "user":
                        logger.info(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) finished cleanly, but new user message detected. Reactivating.")
                        if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                        agent.set_status(AGENT_STATUS_IDLE)
                        asyncio.create_task(self._manager.schedule_cycle(agent, 0))
                    else:
                        logger.info(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) finished cycle cleanly, no reactivation needed.")
                        if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                        if agent.status != AGENT_STATUS_ERROR: agent.set_status(AGENT_STATUS_IDLE)

                else: # Fallback case (e.g., max retries reached for retryable error, or other unexpected end)
                     if is_retryable_error_type and retry_count >= settings.MAX_STREAM_RETRIES:
                          logger.error(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) reached max retries ({settings.MAX_STREAM_RETRIES}) for retryable errors. Triggering failover.")
                          failover_successful = await self._manager.handle_agent_model_failover(agent_id, last_error_obj or ValueError("Max retries reached"))
                          logger.debug(f"CycleHandler: Received failover_successful = {failover_successful} from handle_agent_model_failover (after max retries) for agent '{agent_id}'.")
                          if failover_successful:
                              logger.info(f"CycleHandler: Failover successful for agent '{agent_id}' after max retries. Re-scheduling cycle attempt with new config.")
                              try:
                                  loop = asyncio.get_running_loop()
                                  if not loop.is_closed():
                                      loop.create_task(self.run_cycle(agent, 0))
                                      logger.info(f"CycleHandler: Re-scheduled run_cycle for agent '{agent_id}' with new configuration after max retries.")
                                  else:
                                      logger.warning(f"CycleHandler: Event loop closed. Cannot re-schedule cycle after successful failover (max retries) for agent '{agent_id}'.")
                              except RuntimeError as loop_err:
                                  logger.warning(f"CycleHandler: Could not get running loop to schedule failover retry cycle for agent '{agent_id}': {loop_err}")
                              except Exception as schedule_err:
                                  logger.error(f"CycleHandler: FAILED to create asyncio task for re-scheduling cycle after failover (max retries) of '{agent_id}': {schedule_err}", exc_info=True)
                          else:
                              logger.error(f"CycleHandler: Failover handler exhausted all options for agent '{agent_id}' after max retries. Agent remains in ERROR state.")
                     elif not trigger_failover: # If not failover and not other conditions met (like max retries handled above), just set idle
                          logger.warning(f"CycleHandler: Agent '{agent_id}' cycle ended without explicit success or trigger. Setting Idle. Last Error: {last_error_content}")
                          if agent.status != AGENT_STATUS_ERROR: agent.set_status(AGENT_STATUS_IDLE)

                log_level = logging.ERROR if agent.status == AGENT_STATUS_ERROR else logging.INFO
                logger.log(log_level, f"CycleHandler: Finished cycle logic for Agent '{agent_id}'. Final status for this attempt: {agent.status}")
        except Exception as outer_err:
            # --- CATCH ALL ERRORS WITHIN run_cycle ---
            logger.critical(f"!!! CycleHandler: UNCAUGHT EXCEPTION in run_cycle for Agent '{agent_id}': {outer_err} !!!", exc_info=True)
            try:
                agent.set_status(AGENT_STATUS_ERROR) # Try setting status first
                if self._manager:
                    try:
                        loop = asyncio.get_running_loop()
                        if not loop.is_closed():
                            await self._manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Framework Error] Critical error in agent cycle: {outer_err}"})
                        else:
                            logger.warning("Event loop closed, cannot send critical error UI message.")
                    except RuntimeError as loop_err:
                        logger.warning(f"Could not get running loop to send critical error UI message: {loop_err}")
                    except Exception as ui_err:
                        logger.error(f"Error sending critical error UI message: {ui_err}")
            except Exception as final_err:
                logger.error(f"Error setting agent status during critical run_cycle exception handling: {final_err}")
        # --- END WRAP ---