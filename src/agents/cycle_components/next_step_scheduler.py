# START OF FILE src/agents/cycle_components/next_step_scheduler.py
import logging
import asyncio
from typing import TYPE_CHECKING
import json # <<< --- ADDED IMPORT ---

from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_ERROR,
    AGENT_STATUS_AWAITING_USER_REVIEW_CG, # Added for CG logic
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK,
    PM_STATE_STARTUP, PM_STATE_MANAGE, PM_STATE_WORK,
    # Add new PM states for checks
    PM_STATE_PLAN_DECOMPOSITION, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS,
    WORKER_STATE_WAIT, WORKER_STATE_WORK
)

if TYPE_CHECKING:
    from src.agents.core import Agent
    from src.agents.manager import AgentManager
    from src.agents.cycle_components.cycle_context import CycleContext

logger = logging.getLogger(__name__)

class NextStepScheduler:
    """
    Determines and schedules the next step for an agent after a processing cycle.
    Handles retries, failovers, and reactivation logic.
    """

    def __init__(self, manager: 'AgentManager'):
        self._manager = manager

    async def schedule_next_step(self, context: 'CycleContext') -> None:
        """
        Schedules the next step for the agent based on the outcome of the current cycle.
        This includes retrying, triggering failover, or reactivating for a new turn.
        """
        agent = context.agent
        agent_id = agent.agent_id

        # --- DIAGNOSTIC Location 2 (Start of schedule_next_step) ---
        try:
            history_preview = ""
            history_len = "N/A"
            history_id = "N/A"
            if agent and hasattr(agent, 'message_history') and agent.message_history is not None:
                history_len = len(agent.message_history)
                history_id = id(agent.message_history)
                if agent.message_history:
                    # Preview the first few items if list is long, or all if short
                    preview_items = agent.message_history[:3] # Use slicing for safety
                    history_preview = json.dumps(preview_items, default=str)[:250] # Limit preview length
            logger.critical(f"DIAGNOSTIC (NextStepScheduler - START of schedule_next_step): Agent ID: {getattr(agent, 'agent_id', 'N/A')}, Agent Obj ID: {id(agent) if agent else 'N/A'}, History ID: {history_id}, History Len: {history_len}, History Preview: {history_preview}...")
        except Exception as diag_err:
            logger.error(f"DIAGNOSTIC ERROR (NextStepScheduler - START): {diag_err}")
        # --- END DIAGNOSTIC Location 2 ---


        if context.trigger_failover:
            logger.warning(f"NextStepScheduler: Agent '{agent_id}' ({context.current_model_key_for_tracking}) requires failover. Error: {context.last_error_content[:100]}")
            failover_successful = await self._manager.handle_agent_model_failover(agent_id, context.last_error_obj)
            if failover_successful:
                logger.info(f"NextStepScheduler: Failover successful for agent '{agent_id}'. Agent config updated. Re-scheduling cycle.")
                await self._schedule_new_cycle(agent, 0) # Reset retry count for new config
            else:
                logger.error(f"NextStepScheduler: Failover handler exhausted options for agent '{agent_id}'. Agent remains in ERROR state.")
            self._log_end_of_schedule_next_step(agent, "Path A - Failover Triggered")
            return

        if context.needs_reactivation_after_cycle:
            reactivation_reason = self._determine_reactivation_reason(context)
            logger.info(f"NextStepScheduler: Reactivating agent '{agent_id}' ({context.current_model_key_for_tracking}) due to: {reactivation_reason}.")
            if agent.status != AGENT_STATUS_ERROR:
                agent.set_status(AGENT_STATUS_IDLE)

            # Check if the reactivation is for Admin AI after project creation workflow
            # and the last message in its history is the specific framework notification.
            if agent.agent_type == AGENT_TYPE_ADMIN and \
               agent.state == ADMIN_STATE_CONVERSATION and \
               reactivation_reason == "agent action taken": # This is the reason set when workflow adds notification
                last_message = agent.message_history[-1] if agent.message_history else None
                if last_message and \
                   last_message.get("role") == "system_framework_notification" and \
                   "has been created and is now awaiting user approval" in last_message.get("content", ""):
                    logger.info(f"NextStepScheduler: Suppressing immediate reactivation of Admin AI '{agent_id}' after project creation notification. Admin AI should wait for user input.")
                    # Do not schedule a new cycle, let the user interact or another event trigger the Admin AI.
                    self._log_end_of_schedule_next_step(agent, "Path B - Suppressed Reactivation for Admin AI post-project creation")
                    return

            # CRITICAL FIX: Always reactivate Admin AI after state transitions to work state
            if agent.agent_type == AGENT_TYPE_ADMIN and context.state_change_requested_this_cycle and agent.state == ADMIN_STATE_WORK:
                logger.info(f"NextStepScheduler: Admin AI '{agent_id}' just transitioned to work state - forcing immediate reactivation")
                await self._schedule_new_cycle(agent, 0)
                self._log_end_of_schedule_next_step(agent, "Path B - Admin Work State Transition Reactivation")
                return

            await self._schedule_new_cycle(agent, 0)
            self._log_end_of_schedule_next_step(agent, "Path B - Needs Reactivation")
            return

        if context.is_retryable_error_type and context.retry_count < context.max_retries_for_cycle:
            next_retry_count = context.retry_count + 1
            logger.warning(f"NextStepScheduler: Transient error for '{agent_id}' on {context.current_model_key_for_tracking}. Retrying in {context.retry_delay_for_cycle:.1f}s ({next_retry_count}/{context.max_retries_for_cycle}). Last Error: {context.last_error_content[:100]}")
            await self._manager.send_to_ui({"type": "status", "agent_id": agent_id, "content": f"Provider issue... Retrying '{agent.model}' (Attempt {next_retry_count + 1})..."})
            await asyncio.sleep(context.retry_delay_for_cycle)
            if agent.status != AGENT_STATUS_ERROR:
                agent.set_status(AGENT_STATUS_IDLE)
            await self._schedule_new_cycle(agent, next_retry_count)
            self._log_end_of_schedule_next_step(agent, "Path C - Retry Scheduled")
            return

        if context.is_retryable_error_type and context.retry_count >= context.max_retries_for_cycle:
            logger.error(f"NextStepScheduler: Agent '{agent_id}' ({context.current_model_key_for_tracking}) reached max retries ({context.max_retries_for_cycle}) for retryable errors. Triggering failover.")
            failover_successful = await self._manager.handle_agent_model_failover(agent_id, context.last_error_obj or ValueError("Max retries reached"))
            if failover_successful:
                logger.info(f"NextStepScheduler: Failover successful for agent '{agent_id}' after max retries. Re-scheduling cycle.")
                await self._schedule_new_cycle(agent, 0)
            else:
                logger.error(f"NextStepScheduler: Failover handler exhausted options for agent '{agent_id}' after max retries. Agent remains in ERROR state.")
            self._log_end_of_schedule_next_step(agent, "Path D - Max Retries -> Failover")
            return
        
        if context.cycle_completed_successfully:
            # --- Start Persistent Agent Logic ---
            # These states represent continuous work loops. Agents in these states should
            # be reactivated by default unless they explicitly change state.
            persistent_states = {
                (AGENT_TYPE_PM, PM_STATE_MANAGE),
                (AGENT_TYPE_WORKER, WORKER_STATE_WORK),
                (AGENT_TYPE_ADMIN, ADMIN_STATE_WORK)
            }
            
            # Special logic for Admin AI in work state - check if task is actually complete
            if (agent.agent_type, agent.state) == (AGENT_TYPE_ADMIN, ADMIN_STATE_WORK):
                # Admin AI should only continue in work state if it has made meaningful progress
                # and hasn't completed its task. Check if it should transition out of work state.
                should_continue_work = self._should_admin_continue_work(agent, context)
                if should_continue_work:
                    logger.info(f"NextStepScheduler: Admin AI '{agent_id}' continuing work state - task in progress.")
                    agent.set_status(AGENT_STATUS_IDLE)
                    await self._schedule_new_cycle(agent, 0)
                else:
                    logger.info(f"NextStepScheduler: Admin AI '{agent_id}' work appears complete, allowing natural transition.")
            # This logic ensures that if an agent in a persistent state completes a cycle
            # without error and without requesting a state change, it gets reactivated.
            elif (agent.agent_type, agent.state) in persistent_states and not context.state_change_requested_this_cycle:
                logger.info(f"NextStepScheduler: Agent '{agent_id}' is in a persistent state ('{agent.state}'). Reactivating for continuous work.")
                agent.set_status(AGENT_STATUS_IDLE)
                await self._schedule_new_cycle(agent, 0)
            # SECONDARY FIX: Ensure Admin AI in work state gets reactivated even if not caught by persistent states logic
            elif agent.agent_type == AGENT_TYPE_ADMIN and agent.state == ADMIN_STATE_WORK and not context.state_change_requested_this_cycle:
                logger.info(f"NextStepScheduler: Admin AI '{agent_id}' in work state needs reactivation (secondary catch)")
                agent.set_status(AGENT_STATUS_IDLE)
                await self._schedule_new_cycle(agent, 0)
            elif agent.agent_type == AGENT_TYPE_ADMIN and context.executed_tool_successfully_this_cycle:
                logger.critical(f"NextStepScheduler: Belt-and-suspenders check. Admin AI '{agent.agent_id}' successfully executed a tool. Forcing reactivation.")
                agent.set_status(AGENT_STATUS_IDLE)
                await self._schedule_new_cycle(agent, 0)
            # --- End Persistent Agent Logic ---
            elif agent.agent_type == AGENT_TYPE_PM and \
               agent.state == PM_STATE_STARTUP and \
               not context.action_taken_this_cycle and \
               not context.thought_produced_this_cycle: 
                logger.warning(f"NextStepScheduler: PM agent '{agent_id}' in PM_STARTUP finished but took NO action (tool/state/thought). Reactivating to enforce startup workflow.")
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                agent.set_status(AGENT_STATUS_IDLE)
                await self._schedule_new_cycle(agent, 0)
            elif agent.agent_type == AGENT_TYPE_PM and \
                 agent.state in [PM_STATE_PLAN_DECOMPOSITION, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS] and \
                 (not context.action_taken_this_cycle and not context.executed_tool_successfully_this_cycle): # MODIFIED HERE
                logger.warning(f"NextStepScheduler: PM agent '{agent_id}' in state '{agent.state}' finished but took NO ACTION (and no tool was run). Reactivating to enforce workflow.")
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                agent.set_status(AGENT_STATUS_IDLE)
                await self._schedule_new_cycle(agent, 0)
            else:
                logger.info(f"NextStepScheduler: Agent '{agent_id}' ({context.current_model_key_for_tracking}) finished cycle cleanly, no specific reactivation needed by this scheduler.")
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                if agent.status != AGENT_STATUS_ERROR:
                    # Only set to IDLE if not awaiting user decision on a CG concern
                    if not (agent.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG and getattr(agent, 'cg_awaiting_user_decision', False)):
                        agent.set_status(AGENT_STATUS_IDLE)
        else:
            # This block handles cases where cycle_completed_successfully is False,
            # but it's not a retryable error and not triggering failover.
            # E.g. an internal workflow error that doesn't fit other categories.
            if not context.trigger_failover and not context.is_retryable_error_type:
                logger.warning(f"NextStepScheduler: Agent '{agent_id}' cycle ended without explicit success or trigger. Status: {agent.status}. Last Error: {context.last_error_content[:100]}. Setting Idle if not Error and not awaiting CG review.")
                if agent.status != AGENT_STATUS_ERROR:
                    # Only set to IDLE if not awaiting user decision on a CG concern
                    if not (agent.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG and getattr(agent, 'cg_awaiting_user_decision', False)):
                        agent.set_status(AGENT_STATUS_IDLE)
        
        self._log_end_of_schedule_next_step(agent, "Path E - Default End")

    def _log_end_of_schedule_next_step(self, agent: 'Agent', path_identifier: str) -> None:
        try:
            history_preview = ""
            history_len = "N/A"
            history_id = "N/A"
            if agent and hasattr(agent, 'message_history') and agent.message_history is not None:
                history_len = len(agent.message_history)
                history_id = id(agent.message_history)
                if agent.message_history:
                    preview_items = agent.message_history[:3]
                    history_preview = json.dumps(preview_items, default=str)[:250]
            logger.critical(f"DIAGNOSTIC (NextStepScheduler - END of schedule_next_step via {path_identifier}): Agent ID: {getattr(agent, 'agent_id', 'N/A')}, Agent Obj ID: {id(agent) if agent else 'N/A'}, History ID: {history_id}, History Len: {history_len}, History Preview: {history_preview}...")
        except Exception as diag_err:
            logger.error(f"DIAGNOSTIC ERROR (NextStepScheduler - END): {diag_err}")

    def _determine_reactivation_reason(self, context: 'CycleContext') -> str:
        if context.state_change_requested_this_cycle:
            return f"state change to '{context.agent.state}'"
        if context.plan_submitted_this_cycle:
            return "Admin plan submission"
        if context.executed_tool_successfully_this_cycle:
            return "successful tool execution"
        if context.thought_produced_this_cycle and context.agent.agent_type == AGENT_TYPE_PM and context.agent.state == PM_STATE_STARTUP:
             return "PM startup thought produced (considered progress)"
        if context.action_taken_this_cycle: 
            return "agent action taken"
        return "unspecified condition (needs_reactivation_after_cycle was true)"

    def _should_admin_continue_work(self, agent: 'Agent', context: 'CycleContext') -> bool:
        """
        Determine if Admin AI should continue in work state or transition out.
        
        Admin AI should continue working through multi-step workflows until it explicitly 
        decides to transition out or gets stuck in problematic patterns.

        Args:
            agent: The Admin AI agent
            context: The cycle context
            
        Returns:
            True if Admin AI should continue working, False to allow natural transition
        """
        # If the Admin AI explicitly requested a state change, honor it and allow transition
        if context.state_change_requested_this_cycle:
            logger.info(f"NextStepScheduler: Admin AI '{agent.agent_id}' requested state change - stopping work continuation")
            return False
        
        # If there was a system error (not tool failure), don't continue to avoid loops
        if context.last_error_obj and not context.action_taken_this_cycle:
            logger.info(f"NextStepScheduler: Admin AI '{agent.agent_id}' had system error without action - stopping work continuation")
            return False
        
        # CRITICAL FIX: After successful tool execution, ALWAYS continue work unless explicitly told to stop
        if context.executed_tool_successfully_this_cycle:
            logger.critical(f"NextStepScheduler: Admin AI '{agent.agent_id}' successfully executed tools - FORCING continuation to process results")
            # Reset any problematic counters since we had successful action
            if hasattr(agent, '_consecutive_empty_work_cycles'):
                agent._consecutive_empty_work_cycles = 0
            if hasattr(agent, '_work_cycle_count'):
                agent._work_cycle_count = max(0, agent._work_cycle_count - 1)  # Reset progress since we're making progress
            return True
        
        # Check for explicit completion indicators in the agent's recent responses
        if hasattr(agent, 'message_history') and agent.message_history:
            # Look for completion messages in the last assistant response
            recent_messages = agent.message_history[-2:] if len(agent.message_history) >= 2 else agent.message_history
            for msg in reversed(recent_messages):
                if msg.get('role') == 'assistant':
                    # Ensure content is a string before calling .lower()
                    content = msg.get('content') or ''
                    content_lower = content.lower()
                    completion_phrases = [
                        'task completed', 'work finished', 'testing complete', 
                        'all tools tested', 'work done', 'task finished',
                        'completed successfully', 'finished testing',
                        'work is complete', 'task is complete'
                    ]
                    if any(phrase in content_lower for phrase in completion_phrases):
                        logger.info(f"NextStepScheduler: Admin AI '{agent.agent_id}' indicated work completion - stopping continuation")
                        return False
        
        # Check for problematic empty response patterns that indicate the AI is stuck
        if not context.action_taken_this_cycle and not context.thought_produced_this_cycle:
            # Track consecutive empty cycles
            if not hasattr(agent, '_consecutive_empty_work_cycles'):
                agent._consecutive_empty_work_cycles = 0
            agent._consecutive_empty_work_cycles += 1
            
            logger.warning(f"NextStepScheduler: Admin AI '{agent.agent_id}' had empty cycle #{agent._consecutive_empty_work_cycles} in work state")
            
            # After 2 consecutive empty cycles, stop continuation to prevent infinite loops
            # This aligns with the cycle handler's 3-empty-response detection
            if agent._consecutive_empty_work_cycles >= 2:
                logger.error(f"NextStepScheduler: Admin AI '{agent.agent_id}' had {agent._consecutive_empty_work_cycles} consecutive empty cycles - stopping work continuation to prevent infinite loop")
                # Reset counter
                agent._consecutive_empty_work_cycles = 0
                # The cycle handler will handle generating completion messages
                return False
        else:
            # Reset empty cycle counter when meaningful action is taken
            if hasattr(agent, '_consecutive_empty_work_cycles'):
                agent._consecutive_empty_work_cycles = 0
        
        # Failsafe: Check if Admin AI has been in work state for too long
        work_cycle_limit = 15  # Increased to allow for more complex workflows
        if not hasattr(agent, '_work_cycle_count'):
            agent._work_cycle_count = 0
            
        agent._work_cycle_count += 1
        
        if agent._work_cycle_count >= work_cycle_limit:
            logger.warning(f"NextStepScheduler: Admin AI '{agent.agent_id}' has been in work state for {work_cycle_limit} cycles - forcing completion")
            # Reset counter and inject completion guidance
            agent._work_cycle_count = 0
            completion_msg = {
                "role": "system", 
                "content": "[Framework Notice: Work session limit reached. Please summarize your work and transition to conversation state if appropriate.]"
            }
            agent.message_history.append(completion_msg)
            return False
            
        # DEFAULT: Continue working - Admin AI should stay active in work state for multi-step workflows
        # This allows it to process tool results, make decisions, and continue with next steps
        logger.info(f"NextStepScheduler: Admin AI '{agent.agent_id}' continuing work state (cycle {agent._work_cycle_count}) - multi-step workflow in progress")
        return True

    async def _schedule_new_cycle(self, agent: 'Agent', retry_count: int) -> None:
        """Schedules a new cycle for the agent using the AgentManager."""
        try:
            history_preview = ""
            history_len = "N/A"
            history_id = "N/A"
            if agent and hasattr(agent, 'message_history') and agent.message_history is not None:
                history_len = len(agent.message_history)
                history_id = id(agent.message_history)
                if agent.message_history:
                    preview_items = agent.message_history[:3]
                    history_preview = json.dumps(preview_items, default=str)[:250]
            logger.critical(f"DIAGNOSTIC (NextStepScheduler - START of _schedule_new_cycle): Agent ID: {getattr(agent, 'agent_id', 'N/A')}, Agent Obj ID: {id(agent) if agent else 'N/A'}, History ID: {history_id}, History Len: {history_len}, History Preview: {history_preview}...")
        except Exception as diag_err:
            logger.error(f"DIAGNOSTIC ERROR (NextStepScheduler - _schedule_new_cycle START): {diag_err}")

        logger.info(f"NextStepScheduler._schedule_new_cycle: CRITICAL - Scheduling new cycle for agent ID '{agent.agent_id}', instance ID: {id(agent)}, retry: {retry_count}")
        try:
            # AgentManager.schedule_cycle is now async, so await it.
            # It returns the task object, or None if scheduling failed internally.
            task = await self._manager.schedule_cycle(agent, retry_count)
            if task:
                 logger.info(f"NextStepScheduler: Successfully created asyncio task {getattr(task, 'get_name', lambda: 'N/A')()} for next cycle of '{agent.agent_id}'.")
            else:
                 logger.error(f"NextStepScheduler: Manager.schedule_cycle did not return a task for agent '{agent.agent_id}'. Next cycle might not run.")
        except Exception as schedule_err:
            logger.error(f"NextStepScheduler: FAILED to create/schedule asyncio task for next cycle of '{agent.agent_id}': {schedule_err}", exc_info=True)
