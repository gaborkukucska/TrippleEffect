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
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, # Added Admin States
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

# Import specific exception types
import openai

# Type hinting for AgentManager and InteractionHandler
if TYPE_CHECKING:
    from src.agents.manager import AgentManager, BOOTSTRAP_AGENT_ID # Import BOOTSTRAP_AGENT_ID
    from src.agents.interaction_handler import AgentInteractionHandler

logger = logging.getLogger(__name__)

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
    Enforces single tool type per execution batch.
    Uses retry/failover limits from settings.
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
        needs_reactivation_after_cycle = False # Reset flag for this cycle
        start_time = time.perf_counter()
        llm_call_duration_ms = 0.0

        if not hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle = set()
        current_model_key = f"{current_provider}/{current_model}"
        agent._failed_models_this_cycle.add(current_model_key)
        logger.debug(f"Agent '{agent_id}' attempting model '{current_model_key}'. Failed this sequence so far: {agent._failed_models_this_cycle}")

        current_db_session_id = self._manager.current_session_db_id

        # --- WRAP ENTIRE FUNCTION BODY ---
        try:
            # --- Inner try...finally block for core logic ---
            try:
                # --- Prepare history for LLM call ---
                history_for_call = agent.message_history.copy() # Start with current history
                current_time_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(sep=' ', timespec='seconds') # Get time once

                # --- State-Aware Prompt Injection for Admin AI ---
                if agent.agent_id == self.BOOTSTRAP_AGENT_ID:
                    # --- MODIFIED: Use STARTUP or CONVERSATION based on state ---
                    current_state = getattr(agent, 'state', ADMIN_STATE_STARTUP) # Default to STARTUP now
                    prompt_key = None
                    if current_state == ADMIN_STATE_PLANNING:
                        prompt_key = "admin_ai_planning_prompt"
                    elif current_state == ADMIN_STATE_CONVERSATION:
                        prompt_key = "admin_ai_conversation_prompt"
                    elif current_state == ADMIN_STATE_STARTUP:
                        prompt_key = "admin_ai_startup_prompt" # Use the new startup prompt
                    # Add elif for other states like work_delegated if they need specific prompts

                    if prompt_key:
                        state_prompt_template = settings.PROMPTS.get(prompt_key)
                        if state_prompt_template:
                            # Replace the first message (original system prompt) with the state-specific one
                            # Need to format context into the prompt (time, project, session)
                            try:
                                # TODO: Inject active project list for conversation state if needed
                                formatted_state_prompt = state_prompt_template.format(
                                    project_name=self._manager.current_project or "N/A",
                                    session_name=self._manager.current_session or "N/A",
                                    current_time_utc=current_time_utc_iso
                                    # Add other context placeholders as needed by prompts
                                )
                                # Prepend the user-defined part from config.yaml (first part of original system prompt)
                                user_defined_part = agent.final_system_prompt.split("\n\n---")[0] # Extract user part from original prompt
                                final_state_prompt = f"{user_defined_part}\n\n{formatted_state_prompt}"

                                history_for_call[0] = {"role": "system", "content": final_state_prompt}
                                logger.info(f"Admin AI state is '{current_state}'. Loaded prompt '{prompt_key}'.")
                            except KeyError as fmt_err:
                                 logger.error(f"Failed to format state prompt '{prompt_key}': Missing key {fmt_err}. Using original prompt.")
                                 # Ensure history_for_call[0] exists even if formatting fails
                                 if not history_for_call: history_for_call.append({"role": "system", "content": agent.final_system_prompt})
                                 elif history_for_call[0].get("role") != "system": history_for_call.insert(0, {"role": "system", "content": agent.final_system_prompt})
                            except Exception as prompt_err:
                                 logger.error(f"Error loading/formatting state prompt '{prompt_key}': {prompt_err}. Using original prompt.", exc_info=True)
                                 # Ensure history_for_call[0] exists even if formatting fails
                                 if not history_for_call: history_for_call.append({"role": "system", "content": agent.final_system_prompt})
                                 elif history_for_call[0].get("role") != "system": history_for_call.insert(0, {"role": "system", "content": agent.final_system_prompt})
                        else:
                            logger.error(f"State prompt key '{prompt_key}' not found in prompts.json! Using original prompt.")
                    else:
                        logger.warning(f"Admin AI state '{current_state}' has no specific prompt key defined. Using original prompt.")

                # --- Inject System Health Report (Admin AI only) ---
                system_health_report = None
                if agent.agent_id == self.BOOTSTRAP_AGENT_ID:
                    system_health_report = await self._generate_system_health_report(agent)
                    if system_health_report: # Append health report if generated
                        health_msg: MessageDict = {"role": "system", "content": system_health_report}
                        # Insert *after* the main system prompt but before other history
                        history_for_call.insert(1, health_msg)
                        logger.debug(f"Injected system health report for {agent_id}")

                # --- Inject Time Context (Admin AI only, if not already in state prompt's context) ---
                # The state prompts now include {current_time_utc}, so explicit injection might be redundant
                # We'll keep it for now as a fallback or if state prompts change.
                time_context_already_in_prompt = '{current_time_utc}' in history_for_call[0].get("content", "")
                if agent.agent_id == self.BOOTSTRAP_AGENT_ID and not time_context_already_in_prompt:
                     try:
                         # Use the time calculated earlier
                         time_context_msg: MessageDict = {"role": "system", "content": f"[Framework Context - Current Time: {current_time_utc_iso}]"}
                         # Insert after health report if present, otherwise after system prompt
                         insert_index = 2 if system_health_report else 1
                         history_for_call.insert(insert_index, time_context_msg)
                         logger.debug(f"Explicitly injected time context for Admin AI call: {current_time_utc_iso}")
                     except Exception as time_err:
                         logger.error(f"Failed to create or inject explicit time context for Admin AI: {time_err}", exc_info=True)
                # --- End History Preparation ---

                # --- Make LLM Call ---
                # --- NEW: Send status update before calling LLM ---
                await self._manager.send_to_ui({
                    "type": "status",
                    "agent_id": agent_id,
                    "content": f"Contacting model {current_provider}/{current_model}..."
                })
                # --- END NEW ---
                agent_generator = agent.process_message(history_for_call) # Pass potentially modified history

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

                        # --- NEW: Check for state change request AFTER generator finishes ---
                        # This block is now redundant as state change is handled by agent_state_change_requested event
                        # state_request_match = None # Reset here for safety within the loop scope
                        # if agent.agent_id == self.BOOTSTRAP_AGENT_ID and self.request_state_pattern:
                        #     # Check the *complete* assistant response buffer for the tag
                        #     final_buffer_content = agent.text_buffer.strip()
                        #     state_request_match = self.request_state_pattern.search(final_buffer_content)
                        #     # ... rest of state change logic ...
                        # --- END REDUNDANT BLOCK ---

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
                        # --- NEW: Check if agent is actually in PLANNING state ---
                        if agent.agent_id == self.BOOTSTRAP_AGENT_ID and getattr(agent, 'state', None) == ADMIN_STATE_PLANNING:
                            plan_content = event.get("plan_content", "[No Plan Content]")
                            agent_id_from_event = event.get("agent_id") # Should be admin_ai
                            logger.info(f"CycleHandler: Received plan submission from agent '{agent_id_from_event}' (State: PLANNING).")

                            if current_db_session_id is not None:
                                try:
                                    await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="assistant_plan", content=plan_content) # Log the plan
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
                                if hasattr(self._manager, 'create_project_and_pm_agent'):
                                    creation_success, creation_message, pm_agent_id = await self._manager.create_project_and_pm_agent(
                                        project_title=project_title, # Use original title for display/task name
                                        plan_description=plan_content # Pass full plan as description
                                    )
                                else:
                                     logger.error("AgentManager does not have 'create_project_and_pm_agent' method!")
                                     creation_message = "[Framework Error] Project creation function not implemented."

                            except Exception as creation_err:
                                logger.error(f"Error during framework project/PM creation: {creation_err}", exc_info=True)
                                creation_message = f"[Framework Error] An error occurred during project creation: {creation_err}"
                            # --- End Creation Call ---

                            # --- Inject Confirmation & Set State ---
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

                            needs_reactivation_after_cycle = True # Reactivate in conversation state
                            break # Exit event loop
                        else:
                             # Log warning if plan submitted in wrong state
                             logger.warning(f"CycleHandler: Agent '{agent.agent_id}' submitted a plan but was not in PLANNING state. Ignoring plan.")
                             # Continue processing other events? Or break? Let's break and reactivate.
                             needs_reactivation_after_cycle = True
                             break
                    # --- END STATE/PLAN HANDLING ---
                    # --- NEW: Handle agent_state_change_requested event ---
                    elif event_type == "agent_state_change_requested":
                        requested_state = event.get("requested_state")
                        logger.info(f"CycleHandler: Received state change request to '{requested_state}' from agent '{agent_id}'.")
                        if hasattr(agent, 'set_state') and requested_state:
                            agent.set_state(requested_state)
                            # Log state change? (Already logged in core.py after yielding)
                        else:
                            logger.error(f"Cannot process state change request for agent '{agent_id}': set_state method missing or requested_state empty.")
                        needs_reactivation_after_cycle = True # Always reactivate after state change request
                        break # Exit event loop, let finally handle reactivation
                    # --- END NEW ---
                    # --- NEW: Handle agent_thought event ---
                    elif event_type == "agent_thought":
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
                                # Correctly call execute_tool with positional and keyword args
                                kb_result = await self._manager.tool_executor.execute_tool(
                                    agent_id,               # Positional arg 1: agent_id
                                    agent.sandbox_path,     # Positional arg 2: agent_sandbox_path
                                    "knowledge_base",       # Positional arg 3: tool_name
                                    kb_args,                # Positional arg 4: tool_args
                                    project_name=self._manager.current_project, # Optional kwarg
                                    session_name=self._manager.current_session  # Optional kwarg
                                    # manager kwarg not needed for knowledge_base
                                )
                                # Check result (knowledge_base returns string on success/error)
                                if isinstance(kb_result, str) and kb_result.startswith("Error:"):
                                    logger.error(f"Failed to save agent '{agent_id}' thought to KB. Result: {kb_result}")
                                elif not isinstance(kb_result, str): # Should be string on success too
                                     logger.warning(f"Unexpected result type saving thought to KB: {type(kb_result)}. Result: {kb_result}")

                            except Exception as kb_err:
                                logger.error(f"Exception saving agent '{agent_id}' thought to KB: {kb_err}", exc_info=True)
                        # REMOVED continue: Allow loop to proceed naturally after handling thought
                        # continue
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
                        # Use imported constants for error checking
                        if isinstance(last_error_obj, RETRYABLE_EXCEPTIONS): is_retryable_error_type = True; is_key_related_error = False; trigger_failover = False; logger.warning(f"CycleHandler: Agent '{agent_id}' encountered retryable exception type: {type(last_error_obj).__name__}")
                        elif isinstance(last_error_obj, openai.APIStatusError) and (last_error_obj.status_code in RETRYABLE_STATUS_CODES or last_error_obj.status_code >= 500): is_retryable_error_type = True; is_key_related_error = False; trigger_failover = False; logger.warning(f"CycleHandler: Agent '{agent_id}' encountered retryable status code: {last_error_obj.status_code}")
                        elif isinstance(last_error_obj, KEY_RELATED_ERRORS) or (isinstance(last_error_obj, openai.APIStatusError) and last_error_obj.status_code in KEY_RELATED_STATUS_CODES): is_retryable_error_type = False; is_key_related_error = True; trigger_failover = True; logger.warning(f"CycleHandler: Agent '{agent_id}' encountered key-related error: {type(last_error_obj).__name__}. Triggering failover/key cycle.")
                        # Removed redundant RateLimitError check as it's covered by KEY_RELATED_ERRORS
                        # elif isinstance(last_error_obj, openai.RateLimitError) or (isinstance(last_error_obj, openai.APIStatusError) and last_error_obj.status_code == 429): is_retryable_error_type = False; is_key_related_error = True; trigger_failover = True; logger.warning(f"CycleHandler: Agent '{agent_id}' encountered rate limit error. Triggering failover/key cycle.")
                        else: is_retryable_error_type = False; is_key_related_error = False; trigger_failover = True; logger.warning(f"CycleHandler: Agent '{agent_id}' encountered non-retryable/unknown error: {type(last_error_obj).__name__}. Triggering failover.")
                        if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="system_error", content=last_error_content)
                        break
                    elif event_type == "tool_requests":
                        all_tool_calls: List[Dict] = event.get("calls", [])
                        agent_response_content = event.get("raw_assistant_response")
                        if not all_tool_calls: continue
                        logger.info(f"CycleHandler: Agent '{agent_id}' yielded {len(all_tool_calls)} tool request(s).")
                        if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="assistant", content=agent_response_content, tool_calls=all_tool_calls)
                        else: logger.warning("Cannot log assistant tool request response to DB: current_session_db_id is None.")

                        # --- Batching Enforcement ---
                        tool_names_in_batch = {call.get("name") for call in all_tool_calls if call.get("name")}
                        if len(tool_names_in_batch) > 1:
                            violation_msg = f"Error: Agent '{agent_id}' attempted to call multiple tool types in one turn ({', '.join(sorted(tool_names_in_batch))}). Only one tool type per response is allowed."
                            logger.error(violation_msg)
                            first_call_id = all_tool_calls[0].get('id', f"batch_violation_{int(time.time())}")
                            tool_feedback: Optional[ToolResultDict] = await self._interaction_handler.failed_tool_result(first_call_id, ", ".join(sorted(tool_names_in_batch)))
                            if tool_feedback:
                                 tool_feedback["content"] = f"[Framework Rule Violation]: {violation_msg}"
                                 agent.message_history.append({"role": "tool", **tool_feedback})
                                 if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="system_feedback", content=tool_feedback["content"], tool_results=[tool_feedback])
                            needs_reactivation_after_cycle = True; break
                        # --- End Batching Enforcement ---

                        # Tool validation & execution loop
                        mgmt_calls = []; other_calls = []; invalid_call_results = []
                        for call in all_tool_calls:
                             cid, tname, targs = call.get("id"), call.get("name"), call.get("arguments", {})
                             fail_res: Optional[ToolResultDict] = None
                             if cid and tname and isinstance(targs, dict):
                                 if tname == ManageTeamTool.name: mgmt_calls.append(call)
                                 else: other_calls.append(call)
                             else: fail_res = await self._interaction_handler.failed_tool_result(cid, tname);
                             if fail_res: invalid_call_results.append(fail_res)
                        if invalid_call_results:
                            for res in invalid_call_results:
                                 agent.message_history.append({"role": "tool", **res})
                                 if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="tool", content=res.get("content"), tool_results=[res])

                        calls_to_execute = mgmt_calls + other_calls; activation_tasks = []; manager_action_feedback = []; executed_tool_successfully_this_cycle = False
                        if calls_to_execute:
                            logger.info(f"CycleHandler: Executing {len(calls_to_execute)} tool(s) sequentially for '{agent_id}'.")
                            await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Executing {len(calls_to_execute)} tool(s)..."})
                            for call in calls_to_execute:
                                 call_id = call['id']; tool_name = call['name']; tool_args = call['arguments']
                                 result = await self._interaction_handler.execute_single_tool(agent, call_id, tool_name, tool_args, project_name=self._manager.current_project, session_name=self._manager.current_session)
                                 if result:
                                     raw_content_hist = result.get("content", "[Tool Error: No content]"); tool_msg: MessageDict = {"role": "tool", "tool_call_id": call_id, "content": str(raw_content_hist)}
                                     if not agent.message_history or agent.message_history[-1].get("role") != "tool" or agent.message_history[-1].get("tool_call_id") != call_id: agent.message_history.append(tool_msg)
                                     tool_exec_success = not str(raw_content_hist).strip().startswith(("Error:", "[ToolExec Error:", "[Manager Error:"))
                                     if tool_exec_success: executed_tool_successfully_this_cycle = True
                                     if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="tool", content=str(raw_content_hist), tool_results=[result])
                                     # --- NEW: Send tool result to UI ---
                                     await self._manager.send_to_ui({
                                         "type": "tool_result", # Use the type the frontend expects
                                         "agent_id": agent_id,
                                         "call_id": call_id,
                                         "tool_name": tool_name,
                                         "content": str(raw_content_hist) # Send the string representation
                                     })
                                     # --- END NEW ---
                                     raw_tool_output = result.get("_raw_result")
                                     if tool_name == ManageTeamTool.name:
                                         if isinstance(raw_tool_output, dict) and raw_tool_output.get("status") == "success":
                                             action = raw_tool_output.get("action"); params = raw_tool_output.get("params", {}); act_success, act_msg, act_data = await self._interaction_handler.handle_manage_team_action(action, params, agent_id)
                                             feedback = {"call_id": call_id, "action": action, "success": act_success, "message": act_msg};
                                             if act_data: feedback["data"] = act_data; manager_action_feedback.append(feedback)
                                         elif isinstance(raw_tool_output, dict): manager_action_feedback.append({"call_id": call_id, "action": raw_tool_output.get("action"), "success": False, "message": raw_tool_output.get("message", "Tool exec failed.")})
                                         else: manager_action_feedback.append({"call_id": call_id, "action": "unknown", "success": False, "message": "Unexpected tool result."})
                                     elif tool_name == SendMessageTool.name:
                                         target_id = call['arguments'].get("target_agent_id"); msg_content = call['arguments'].get("message_content")
                                         activation_task = None
                                         if target_id and msg_content is not None:
                                               activation_task = await self._interaction_handler.route_and_activate_agent_message(agent_id, target_id, msg_content);
                                               if activation_task: activation_tasks.append(activation_task)
                                         elif not target_id or msg_content is None: manager_action_feedback.append({"call_id": call_id, "action": "send_message", "success": False, "message": f"Validation Error: Missing {'target_agent_id' if not target_id else 'message_content'}."})
                                 else:
                                     manager_action_feedback.append({"call_id": call_id, "action": tool_name, "success": False, "message": "Tool execution failed (no result)."})
                                     if current_db_session_id is not None: await self._manager.db_manager.log_interaction(session_id=current_db_session_id, agent_id=agent_id, role="tool", content="[Tool Execution Error: Tool failed internally (no result)]", tool_results=[{"call_id": call_id, "content": "[Tool Execution Error: Tool failed internally (no result)]"}])

                            if activation_tasks: await asyncio.gather(*activation_tasks); logger.info(f"CycleHandler: Completed activation tasks for '{agent_id}'.")

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
                                     # --- NEW: Send system feedback to UI ---
                                     await self._manager.send_to_ui({
                                         "type": "system_feedback", # Use a distinct type
                                         "agent_id": agent_id,
                                         "call_id": fb['call_id'],
                                         "action": fb.get('action', 'N/A'),
                                         "content": fb_content # Send the formatted feedback content
                                     })
                                     # --- END NEW ---

                        if calls_to_execute:
                            logger.debug(f"CycleHandler: Tools executed for '{agent_id}', breaking inner loop to allow reactivation check.");
                            if executed_tool_successfully_this_cycle:
                                 needs_reactivation_after_cycle = True
                            break # Break loop after tool execution
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
                if plan_approved_this_cycle or executed_tool_successfully_this_cycle or (not cycle_completed_successfully and not trigger_failover and not is_retryable_error_type):
                     needs_reactivation_after_cycle = True

                logger.debug(f"Cycle outcome for {current_provider}/{current_model} (Retry: {retry_count}): SuccessForMetrics={call_success_for_metrics}, TriggerFailover={trigger_failover}, PlanApproved={plan_approved_this_cycle}, NeedsReactivation={needs_reactivation_after_cycle}, Duration={llm_call_duration_ms:.2f}ms, Error? {bool(last_error_obj)}")

                if not plan_approved_this_cycle:
                     try: await self._manager.performance_tracker.record_call(provider=current_provider, model_id=current_model, duration_ms=llm_call_duration_ms, success=call_success_for_metrics)
                     except Exception as record_err: logger.error(f"Failed to record performance metrics for {current_provider}/{current_model}: {record_err}", exc_info=True)

                # --- Final Action Logic (Uses settings for retry/failover) ---
                # --- REVISED ORDER: Check FAILOVER FIRST ---
                if trigger_failover:
                    logger.warning(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) requires failover. Error Type: {type(last_error_obj).__name__ if last_error_obj else 'N/A'}. Triggering failover handler.")
                    # --- RE-ADD Explicitly close generator before failover ---
                    # Note: This is redundant with the finally block above, but kept for clarity of intent
                    if agent_generator and not generator_finished_normally:
                        try:
                            logger.debug(f"Attempting explicit aclose() on generator for '{agent_id}' before failover (redundant check).")
                            await agent_generator.aclose()
                            logger.debug(f"Explicit aclose() successful for '{agent_id}' before failover (redundant check).")
                        except RuntimeError as close_err:
                            if "already running" in str(close_err): logger.warning(f"Generator aclose() error before failover (redundant check, already running/closed?): {close_err}")
                            else: raise # Re-raise unexpected RuntimeError
                        except Exception as close_err: logger.error(f"Unexpected error during explicit aclose() before failover (redundant check) for '{agent_id}': {close_err}", exc_info=True)
                    # --- End explicit close ---
                    asyncio.create_task(self._manager.handle_agent_model_failover(agent_id, last_error_obj))
                elif needs_reactivation_after_cycle: # Check reactivation SECOND
                     # Determine reason for logging clarity - MORE SPECIFIC REASONS
                     reactivation_reason = "unknown condition" # Default
                     current_agent_state_in_finally = getattr(agent, 'state', None) # Get state *now*
                     # Check specific event flags from the cycle
                     # Note: Checking locals() for processed_events is unreliable. Rely on state/flags.
                     if current_agent_state_in_finally == ADMIN_STATE_PLANNING: reactivation_reason = "state change to planning"
                     elif current_agent_state_in_finally == ADMIN_STATE_CONVERSATION: reactivation_reason = "state change to conversation" # e.g., after plan submission or other event
                     elif current_agent_state_in_finally == ADMIN_STATE_WORK_DELEGATED: reactivation_reason = "state change to work_delegated"
                     elif plan_approved_this_cycle: reactivation_reason = "plan approved by user"
                     elif executed_tool_successfully_this_cycle: reactivation_reason = "successful tool execution"
                     # Add more specific reasons if needed based on flags set during the cycle

                     logger.info(f"CycleHandler: Reactivating agent '{agent_id}' ({current_model_key}) after {reactivation_reason}.")

                     # --- RE-ADD Explicitly close generator before reactivation ---
                     # Note: This is redundant with the finally block above, but kept for clarity of intent
                     if agent_generator and not generator_finished_normally:
                         try:
                             logger.debug(f"Attempting explicit aclose() on generator for '{agent_id}' before reactivation (redundant check).")
                             await agent_generator.aclose()
                             logger.debug(f"Explicit aclose() successful for '{agent_id}' before reactivation (redundant check).")
                         except RuntimeError as close_err:
                             if "already running" in str(close_err): logger.warning(f"Generator aclose() error before reactivation (redundant check, already running/closed?): {close_err}")
                             else: raise # Re-raise unexpected RuntimeError
                         except Exception as close_err: logger.error(f"Unexpected error during explicit aclose() before reactivation (redundant check) for '{agent_id}': {close_err}", exc_info=True)
                     # --- End explicit close ---

                     agent.set_status(AGENT_STATUS_IDLE)
                     await asyncio.sleep(0.01) # Small delay
                     # --- ADDED LOGGING ---
                     logger.debug(f"CycleHandler: Preparing to schedule next cycle for '{agent_id}' via asyncio.create_task...")
                     # --- MOVED TASK CREATION TO END OF TRY BLOCK ---
                     # Log state before scheduling
                     current_agent_state_in_finally = getattr(agent, 'state', 'N/A')
                     logger.info(f"CycleHandler: Agent '{agent_id}' state in finally block *before* scheduling reactivation: {current_agent_state_in_finally}")

                     # Schedule the task last
                     try:
                         task = asyncio.create_task(self._manager.schedule_cycle(agent, 0)) # Get task object
                         logger.info(f"CycleHandler: Successfully created asyncio task {task.get_name()} for next cycle of '{agent_id}'.")
                         await asyncio.sleep(0) # Yield control briefly
                         logger.debug(f"CycleHandler: asyncio.sleep(0) completed after scheduling task for {agent_id}.")
                     except Exception as schedule_err:
                         logger.error(f"CycleHandler: FAILED to create asyncio task for next cycle of '{agent_id}': {schedule_err}", exc_info=True)
                # --- REMOVED DUPLICATE FAILOVER CHECK ---
                elif is_retryable_error_type and retry_count < settings.MAX_STREAM_RETRIES: # Check retry THIRD
                     logger.warning(f"CycleHandler: Transient error for '{agent_id}' on {current_model_key}. Retrying same model/key in {settings.RETRY_DELAY_SECONDS:.1f}s ({retry_count + 1}/{settings.MAX_STREAM_RETRIES})... Last Error: {last_error_content}")
                     await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Provider issue... Retrying '{current_model}' (Attempt {retry_count + 2})..."})
                     await asyncio.sleep(settings.RETRY_DELAY_SECONDS) # Use settings
                     agent.set_status(AGENT_STATUS_IDLE); asyncio.create_task(self._manager.schedule_cycle(agent, retry_count + 1))
                # --- REMOVED DUPLICATE needs_reactivation_after_cycle block ---
                # elif needs_reactivation_after_cycle:
                #      reactivation_reason = "plan approved" if plan_approved_this_cycle else ("tool/feedback processing" if executed_tool_successfully_this_cycle else "batching rule violation/feedback")
                #      logger.info(f"CycleHandler: Reactivating agent '{agent_id}' ({current_model_key}) after {reactivation_reason}.")
                #      agent.set_status(AGENT_STATUS_IDLE)
                #      await asyncio.sleep(0.01) # Small delay
                #      # --- ADDED LOGGING ---
                #      logger.debug(f"CycleHandler: Attempting to schedule next cycle for '{agent_id}' via asyncio.create_task...")
                #      try:
                #          asyncio.create_task(self._manager.schedule_cycle(agent, 0))
                #          logger.info(f"CycleHandler: Successfully created asyncio task for next cycle of '{agent_id}'.")
                #      except Exception as schedule_err:
                #          logger.error(f"CycleHandler: FAILED to create asyncio task for next cycle of '{agent_id}': {schedule_err}", exc_info=True)
                #      # --- END ADDED LOGGING ---
                elif call_success_for_metrics:
                     history_len_after = len(agent.message_history)
                     if history_len_after > history_len_before and agent.message_history[-1].get("role") == "user":
                          logger.info(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) finished cleanly, but new user message detected. Reactivating.")
                          if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                          agent.set_status(AGENT_STATUS_IDLE); asyncio.create_task(self._manager.schedule_cycle(agent, 0))
                     else:
                          logger.info(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) finished cycle cleanly, no reactivation needed.")
                          if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                          if agent.status != AGENT_STATUS_ERROR: agent.set_status(AGENT_STATUS_IDLE)
                else: # Fallback case
                     if not is_retryable_error_type and retry_count >= settings.MAX_STREAM_RETRIES and not trigger_failover: # Use settings
                          logger.error(f"CycleHandler: Agent '{agent_id}' ({current_model_key}) reached max retries ({settings.MAX_STREAM_RETRIES}) for transient errors. Triggering failover.")
                          asyncio.create_task(self._manager.handle_agent_model_failover(agent_id, last_error_obj or ValueError("Max retries reached")))
                     elif not trigger_failover:
                          logger.warning(f"CycleHandler: Agent '{agent_id}' cycle ended without explicit success or trigger. Setting Idle. Last Error: {last_error_content}")
                          if agent.status != AGENT_STATUS_ERROR: agent.set_status(AGENT_STATUS_IDLE)

                log_level = logging.ERROR if agent.status == AGENT_STATUS_ERROR else logging.INFO
                logger.log(log_level, f"CycleHandler: Finished cycle logic for Agent '{agent_id}'. Final status for this attempt: {agent.status}")
        except Exception as outer_err:
            # --- CATCH ALL ERRORS WITHIN run_cycle ---
            logger.critical(f"!!! CycleHandler: UNCAUGHT EXCEPTION in run_cycle for Agent '{agent_id}': {outer_err} !!!", exc_info=True)
            # Optionally try to set agent status to error if possible
            try:
                agent.set_status(AGENT_STATUS_ERROR)
                if self._manager:
                    await self._manager.send_to_ui({"type": "error", "agent_id": agent_id, "content": f"[Framework Error] Critical error in agent cycle: {outer_err}"})
            except Exception as final_err:
                logger.error(f"Error setting agent status/sending UI message during critical run_cycle exception handling: {final_err}")
        # --- END WRAP ---
