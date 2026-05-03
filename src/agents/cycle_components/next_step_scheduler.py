# START OF FILE src/agents/cycle_components/next_step_scheduler.py
import logging
import asyncio
import time
from typing import TYPE_CHECKING
import json # <<< --- ADDED IMPORT ---

from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_ERROR,
    AGENT_STATUS_AWAITING_USER_REVIEW_CG, # Added for CG logic
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK, ADMIN_STATE_STANDBY,
    PM_STATE_STARTUP, PM_STATE_MANAGE, PM_STATE_WORK, PM_STATE_AUDIT, PM_STATE_STANDBY,
    # Add new PM states for checks
    PM_STATE_PLAN_DECOMPOSITION, PM_STATE_ACTIVATE_WORKERS, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_REPORT_CHECK,
    WORKER_STATE_WAIT, WORKER_STATE_WORK, WORKER_STATE_TEST, WORKER_STATE_REPORT, WORKER_STATE_DECOMPOSE
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

        # Append tool results to history if they exist in the context
        if context.all_tool_results:
            for tool_result in context.all_tool_results:
                agent.message_history.append(tool_result)
            logger.info(f"NextStepScheduler: Appended {len(context.all_tool_results)} tool result(s) to agent '{agent_id}' history.")

        # --- Diagnostic: Log context at start of schedule_next_step ---
        logger.debug(f"NextStepScheduler (START): Agent '{getattr(agent, 'agent_id', 'N/A')}', "
                     f"History Len: {len(agent.message_history) if agent and hasattr(agent, 'message_history') and agent.message_history is not None else 'N/A'}")


        if context.trigger_failover:
            logger.warning(f"NextStepScheduler: Agent '{agent_id}' ({context.current_model_key_for_tracking}) requires failover. Error: {context.last_error_content[:100]}")
            failover_successful = await self._manager.handle_agent_model_failover(agent_id, context.last_error_obj or Exception("Unknown error triggering failover"))
            if failover_successful:
                logger.info(f"NextStepScheduler: Failover successful for agent '{agent_id}'. Agent config updated. Re-scheduling cycle.")
                setattr(agent, '_failover_exhaustion_retry_count', 0)  # Reset on success
                await self._schedule_new_cycle(agent, 0) # Reset retry count for new config
            else:
                # --- INFINITE DELAYED AUTO-RETRY on failover exhaustion ---
                retry_count = getattr(agent, '_failover_exhaustion_retry_count', 0)
                retry_count += 1
                setattr(agent, '_failover_exhaustion_retry_count', retry_count)
                
                # Infinite retries with exponential backoff capped at 300 seconds (5 mins)
                base_cooldown = 15
                cooldown_seconds = min(300, base_cooldown * (2 ** (retry_count - 1)))
                
                logger.warning(
                    f"NextStepScheduler: Failover exhausted for agent '{agent_id}'. "
                    f"Scheduling auto-retry {retry_count} after {cooldown_seconds}s cooldown."
                )
                await self._manager.send_to_ui({
                    "type": "status", "agent_id": agent_id,
                    "content": f"All providers down. Auto-retry {retry_count} in {cooldown_seconds}s..."
                })
                
                # Reset the failover state so it can try all providers again
                if hasattr(agent, '_failover_state'):
                    del agent._failover_state
                if hasattr(agent, '_failed_models_this_cycle'):
                    agent._failed_models_this_cycle.clear()
                
                # Wait cooldown then retry
                await asyncio.sleep(cooldown_seconds)
                
                # Restore agent to IDLE and reschedule
                agent.set_status(AGENT_STATUS_IDLE)
                await self._schedule_new_cycle(agent, 0)
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

            # FIXED: Don't immediately reactivate Admin AI after work state transition
            # Let it process naturally in the next cycle
            if agent.agent_type == AGENT_TYPE_ADMIN and context.state_change_requested_this_cycle and agent.state == ADMIN_STATE_WORK:
                logger.info(f"NextStepScheduler: Admin AI '{agent_id}' transitioned to work state - will process naturally")
                # Don't force immediate reactivation - this was causing the infinite loop

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
                (AGENT_TYPE_PM, PM_STATE_AUDIT),
                (AGENT_TYPE_WORKER, WORKER_STATE_WORK),
                (AGENT_TYPE_WORKER, WORKER_STATE_TEST),
                (AGENT_TYPE_WORKER, WORKER_STATE_REPORT),
                (AGENT_TYPE_ADMIN, ADMIN_STATE_WORK),
            }
            
            # Admin planning state: reactivate ONLY after tool execution so the LLM can
            # process the result and produce a plan.  Do NOT add to persistent_states
            # (that would cause infinite generation loops).
            if (agent.agent_type, agent.state) == (AGENT_TYPE_ADMIN, ADMIN_STATE_PLANNING):
                if context.executed_tool_successfully_this_cycle:
                    logger.info(f"NextStepScheduler: Admin AI '{agent_id}' executed tool in planning state - reactivating to process result and produce plan.")
                    agent.set_status(AGENT_STATUS_IDLE)
                    await self._schedule_new_cycle(agent, 0)
                    self._log_end_of_schedule_next_step(agent, "Path B-Planning-ToolReactivation")
                    return
                else:
                    logger.info(f"NextStepScheduler: Admin AI '{agent_id}' finished planning cycle without tool use - allowing natural idle (plan output expected).")
            # BALANCED FIX: Admin AI work state logic - reactivate after tool execution to process results
            # IMPORTANT: Only applies to ADMIN_STATE_WORK - other states (startup, conversation) should NOT loop
            elif (agent.agent_type, agent.state) == (AGENT_TYPE_ADMIN, ADMIN_STATE_WORK):
                if context.executed_tool_successfully_this_cycle:
                    logger.info(f"NextStepScheduler: Admin AI '{agent_id}' executed tools successfully - reactivating to process results")
                    agent.set_status(AGENT_STATUS_IDLE)
                    await self._schedule_new_cycle(agent, 0)
                else:
                    # Only check for continuation if no tools were executed
                    should_continue_work = await self._should_admin_continue_work(agent, context)
                    if should_continue_work:
                        logger.info(f"NextStepScheduler: Admin AI '{agent_id}' continuing work state - no tools executed")
                        agent.set_status(AGENT_STATUS_IDLE)
                        await self._schedule_new_cycle(agent, 0)
                    else:
                        logger.info(f"NextStepScheduler: Admin AI '{agent_id}' work appears complete")
            # This logic ensures that if an agent in a persistent state completes a cycle
            # without error and without requesting a state change, it gets reactivated.
            elif (agent.agent_type, agent.state) in persistent_states and not context.state_change_requested_this_cycle:
                # Track consecutive failed cycles            # 1. Worker needs explicit state transition attribute
                if agent.agent_type == AGENT_TYPE_WORKER and agent.state in [WORKER_STATE_WORK, WORKER_STATE_TEST]:
                    if not context.executed_tool_successfully_this_cycle and context.action_taken_this_cycle:
                        current_failures = getattr(agent, '_consecutive_failed_cycles', 0)
                        setattr(agent, '_consecutive_failed_cycles', current_failures + 1)
                    elif context.executed_tool_successfully_this_cycle:
                        setattr(agent, '_consecutive_failed_cycles', 0)
                        
                    if getattr(agent, '_consecutive_failed_cycles', 0) >= 3:
                        logger.critical(f"NextStepScheduler: Worker '{agent_id}' had {getattr(agent, '_consecutive_failed_cycles')} consecutive failed cycles. FORCING transition to report state.")
                        if hasattr(self._manager, 'workflow_manager'):
                            self._manager.workflow_manager.change_state(agent, WORKER_STATE_REPORT)
                            setattr(agent, '_consecutive_failed_cycles', 0)
                            override_msg = "[EMERGENCY SYSTEM OVERRIDE]: You have failed to execute tools successfully for 3 consecutive cycles. The framework has automatically transitioned you to the 'worker_report' state. You MUST now summarize the issues you encountered and report back to the PM."
                            agent.message_history.append({"role": "system", "content": override_msg})
                            if self._manager and hasattr(self._manager, 'db_manager') and self._manager.current_session_db_id:
                                asyncio.create_task(self._manager.db_manager.log_interaction(session_id=self._manager.current_session_db_id, agent_id=agent.agent_id, role="system_emergency_override", content=override_msg))
                            agent.set_status(AGENT_STATUS_IDLE)
                            await self._schedule_new_cycle(agent, 0)
                            self._log_end_of_schedule_next_step(agent, "Path B-Worker-Failure-Override")
                            return

                # Add loop detection for all persistent states to prevent infinite stagnation
                # Workers in work/test states get a higher threshold since multi-file edits
                # naturally involve re-reading the same files between edits
                dup_threshold = 4 if (agent.agent_type == AGENT_TYPE_WORKER and agent.state in [WORKER_STATE_WORK, WORKER_STATE_TEST]) else 2
                is_stuck_in_loop = getattr(agent, '_duplicate_tool_call_count', 0) >= dup_threshold
                if not is_stuck_in_loop and self._detect_tool_execution_loops(agent):
                    is_stuck_in_loop = True
                    
                if is_stuck_in_loop:
                    logger.critical(f"NextStepScheduler: Agent '{agent_id}' is stuck in a loop in persistent state '{agent.state}'. FORCING intervention.")
                    if agent.agent_type == AGENT_TYPE_WORKER and agent.state in [WORKER_STATE_WORK, WORKER_STATE_TEST, WORKER_STATE_REPORT]:
                        if hasattr(self._manager, 'workflow_manager'):
                            try:
                                target_state = WORKER_STATE_REPORT if agent.state in [WORKER_STATE_WORK, WORKER_STATE_TEST] else WORKER_STATE_WAIT
                                self._manager.workflow_manager.change_state(agent, target_state)
                                setattr(agent, '_duplicate_tool_call_count', 0)
                                
                                # CRITICAL FIX: Truncate message history to break context loop for workers
                                history_len = len(agent.message_history)
                                if history_len > 6:
                                    # Pop the last 4 messages to remove the repetitive loop context
                                    agent.message_history = agent.message_history[:-4]
                                    logger.info(f"NextStepScheduler: Truncated last 4 messages from Worker '{agent_id}' to break loop context.")
                                
                                override_msg = f"[EMERGENCY SYSTEM OVERRIDE]: You were stuck in an infinite loop repeating the exact same tool calls. The framework has automatically transitioned you to the '{target_state}' state. You MUST stop repeating your previous actions."
                                agent.message_history.append({"role": "system", "content": override_msg})
                                if self._manager and hasattr(self._manager, 'db_manager') and self._manager.current_session_db_id:
                                    await self._manager.db_manager.log_interaction(session_id=self._manager.current_session_db_id, agent_id=agent.agent_id, role="system_emergency_override", content=override_msg)
                                agent.set_status(AGENT_STATUS_IDLE)
                                await self._schedule_new_cycle(agent, 0)
                                self._log_end_of_schedule_next_step(agent, "Path B-Persistent-Loop-Override")
                                return
                            except Exception as e:
                                logger.error(f"NextStepScheduler: Failed to force state change for looping worker: {e}")
                    elif agent.agent_type == AGENT_TYPE_PM and agent.state in [PM_STATE_MANAGE, PM_STATE_WORK, PM_STATE_AUDIT]:
                        if hasattr(self._manager, 'workflow_manager'):
                            try:
                                # For PM, force a context refresh by clearing immediate history and asking for a summary
                                setattr(agent, '_duplicate_tool_call_count', 0)
                                
                                # CRITICAL FIX: Truncate message history to break context loop
                                history_len = len(agent.message_history)
                                if history_len > 6:
                                    # Pop the last 4 messages to remove the repetitive loop context
                                    agent.message_history = agent.message_history[:-4]
                                    logger.info(f"NextStepScheduler: Truncated last 4 messages from PM '{agent_id}' to break loop context.")

                                override_msg = "[EMERGENCY SYSTEM OVERRIDE]: You are stuck in an infinite loop repeating the same actions. You MUST STOP. Take a step back, review the overall project status via 'project_management' list_tasks, and decide on a NEW course of action. Do NOT repeat your previous tool calls."
                                agent.message_history.append({"role": "system", "content": override_msg})
                                if self._manager and hasattr(self._manager, 'db_manager') and self._manager.current_session_db_id:
                                    await self._manager.db_manager.log_interaction(session_id=self._manager.current_session_db_id, agent_id=agent.agent_id, role="system_emergency_override", content=override_msg)
                            except Exception as e:
                                logger.error(f"NextStepScheduler: Failed to apply intervention for looping PM: {e}")

                logger.info(f"NextStepScheduler: Agent '{agent_id}' is in a persistent state ('{agent.state}'). Reactivating for continuous work.")
                
                # ENFORCEMENT: PM_AUDIT state cycle limits
                if getattr(agent, 'agent_type', '') == AGENT_TYPE_PM and getattr(agent, 'state', '') == PM_STATE_AUDIT:
                    current_count = getattr(agent, '_pm_audit_cycle_count', 0)
                    setattr(agent, '_pm_audit_cycle_count', current_count + 1)
                    
                    if getattr(agent, '_pm_audit_cycle_count') > 30:
                        if hasattr(self._manager, 'workflow_manager'):
                            logger.critical(f"NextStepScheduler: PM '{agent_id}' has been in 'pm_audit' for {getattr(agent, '_pm_audit_cycle_count')} cycles. Forcing transition to pm_standby to prevent infinite loop.")
                            self._manager.workflow_manager.change_state(agent, PM_STATE_STANDBY)
                            setattr(agent, '_pm_audit_cycle_count', 0)
                            
                            override_msg = "[System Enforcement]: You failed to complete the audit within the maximum allowed steps. The system has automatically transitioned you to standby state to prevent compute waste."
                            agent.message_history.append({"role": "system", "content": override_msg})
                            
                            agent.set_status(AGENT_STATUS_IDLE)
                            await self._schedule_new_cycle(agent, 0)
                            self._log_end_of_schedule_next_step(agent, "Path B-PMAudit-Limit-Override")
                            return
                
                agent.set_status(AGENT_STATUS_IDLE)
                await self._schedule_new_cycle(agent, 0)
            # ROOT CAUSE FIX: Removed aggressive reactivation logic that was causing infinite loops
            # Admin AI should process tool results naturally without forced immediate reactivation
            # --- End Persistent Agent Logic ---
            elif agent.agent_type == AGENT_TYPE_PM and \
                 agent.state == PM_STATE_STARTUP:
                logger.warning(f"NextStepScheduler: PM agent '{agent_id}' in PM_STARTUP finished the cycle but failed to transition out. Reactivating to enforce startup workflow.")
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                agent.set_status(AGENT_STATUS_IDLE)
                await self._schedule_new_cycle(agent, 0)
            elif agent.agent_type == AGENT_TYPE_PM and \
                 agent.state in [PM_STATE_PLAN_DECOMPOSITION, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS]:
                logger.info(f"NextStepScheduler: PM agent '{agent_id}' in state '{agent.state}' completed cycle. Reactivating to enforce transitional workflow.")
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                if agent.status != AGENT_STATUS_ERROR:
                    if not (agent.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG and getattr(agent, 'cg_awaiting_user_decision', False)):
                        agent.set_status(AGENT_STATUS_IDLE)
                        await self._schedule_new_cycle(agent, 0)
            # FIX: worker_decompose is a transitional state requiring multiple
            # add_task calls.  Without reactivation the worker goes idle after
            # its first tool call and never wakes up.
            elif agent.agent_type == AGENT_TYPE_WORKER and \
                 agent.state == WORKER_STATE_DECOMPOSE and not context.state_change_requested_this_cycle:
                logger.info(f"NextStepScheduler: Worker '{agent_id}' in worker_decompose state completed cycle. Reactivating for continued decomposition.")
                if agent.status != AGENT_STATUS_ERROR:
                    agent.set_status(AGENT_STATUS_IDLE)
                    await self._schedule_new_cycle(agent, 0)
            elif agent.agent_type == AGENT_TYPE_PM and \
                 agent.state == PM_STATE_REPORT_CHECK and not context.state_change_requested_this_cycle:
                
                # ENFORCEMENT: pm_report_check cycle cap
                current_count = getattr(agent, '_pm_report_check_cycle_count', 0)
                setattr(agent, '_pm_report_check_cycle_count', current_count + 1)
                
                if getattr(agent, '_pm_report_check_cycle_count') > 5:
                    logger.critical(f"NextStepScheduler: PM '{agent_id}' has been in 'pm_report_check' for {getattr(agent, '_pm_report_check_cycle_count')} cycles. Forcing transition to pm_manage to prevent infinite loop.")
                    if hasattr(self._manager, 'workflow_manager'):
                        self._manager.workflow_manager.change_state(agent, PM_STATE_MANAGE)
                        setattr(agent, '_pm_report_check_cycle_count', 0)
                        
                        override_msg = "[System Enforcement]: You failed to complete the report check within the maximum allowed steps. The system has automatically transitioned you to manage state to prevent compute waste."
                        agent.message_history.append({"role": "system", "content": override_msg})
                        
                        agent.set_status(AGENT_STATUS_IDLE)
                        await self._schedule_new_cycle(agent, 0)
                        self._log_end_of_schedule_next_step(agent, "Path B-PMReportCheck-Limit-Override")
                        return

                if getattr(context, 'action_taken_this_cycle', False):
                    # PM executed a tool (e.g., send_message, project_management). Allow it to stay in this state to read the result.
                    logger.info(f"NextStepScheduler: PM agent '{agent_id}' in pm_report_check executed a tool. Reactivating to process result.")
                    if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                    if agent.status != AGENT_STATUS_ERROR:
                        agent.set_status(AGENT_STATUS_IDLE)
                        await self._schedule_new_cycle(agent, 0)
                else:
                    logger.warning(f"NextStepScheduler: PM agent '{agent_id}' in pm_report_check forgot to request state change. Auto-reverting to pm_manage.")
                    if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                    if agent.status != AGENT_STATUS_ERROR:
                        if not (agent.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG and getattr(agent, 'cg_awaiting_user_decision', False)):
                            self._manager.workflow_manager.change_state(agent, PM_STATE_MANAGE)
                            agent.set_status(AGENT_STATUS_IDLE)
                            # Let the periodic timer wake it up to prevent busy-looping
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

                # CRITICAL FIX: PM agents in transitional states MUST be rescheduled even
                # when the cycle did not complete successfully (e.g. malformed LLM output,
                # failed tool call).  Previously PMs in build_team_tasks or
                # activate_workers would silently stop here — this was the root cause of
                # the "PM breakdown before workers start" bug.
                if agent.agent_type == AGENT_TYPE_PM and \
                   agent.state in [PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS]:
                    logger.warning(f"NextStepScheduler: PM agent '{agent_id}' in transitional state '{agent.state}' had a FAILED cycle. Rescheduling to recover.")
                    if agent.status != AGENT_STATUS_ERROR:
                        agent.set_status(AGENT_STATUS_IDLE)
                        await self._schedule_new_cycle(agent, 0)
                        self._log_end_of_schedule_next_step(agent, "Path E-PM-Recovery")
                        return
        
        self._log_end_of_schedule_next_step(agent, "Path E - Default End")

    def _log_end_of_schedule_next_step(self, agent: 'Agent', path_identifier: str) -> None:
        logger.debug(f"NextStepScheduler (END via {path_identifier}): Agent '{getattr(agent, 'agent_id', 'N/A')}', "
                     f"History Len: {len(agent.message_history) if agent and hasattr(agent, 'message_history') and agent.message_history is not None else 'N/A'}")

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

    async def _should_admin_continue_work(self, agent: 'Agent', context: 'CycleContext') -> bool:
        """
        Determine if Admin AI should continue in work state or transition out.
        
        ENHANCED: More intelligent continuation logic that prevents loops while allowing legitimate work.
        """
        # CRITICAL: Always honor explicit state change requests
        if context.state_change_requested_this_cycle:
            logger.info(f"NextStepScheduler: Admin AI '{agent.agent_id}' requested state change - stopping work continuation")
            return False
        
        # CRITICAL: If system error without any action, stop to prevent error loops
        if context.last_error_obj and not context.action_taken_this_cycle:
            logger.info(f"NextStepScheduler: Admin AI '{agent.agent_id}' had system error without action - stopping work continuation")
            return False
        
        # ENHANCED: More sophisticated tool execution analysis
        if context.executed_tool_successfully_this_cycle:
            # Check for dangerous tool repetition patterns
            tool_loop_detected = self._detect_tool_execution_loops(agent)
            if tool_loop_detected:
                logger.warning(f"NextStepScheduler: Admin AI '{agent.agent_id}' showing tool execution loop pattern - stopping continuation")
                return False
            
            # Check if this appears to be completion-oriented tool usage
            completion_indicators = self._check_for_completion_signals(agent, context)
            if completion_indicators:
                logger.info(f"NextStepScheduler: Admin AI '{agent.agent_id}' showing completion signals - allowing natural transition")
                return False
            
            logger.info(f"NextStepScheduler: Admin AI '{agent.agent_id}' successfully executed tools - allowing continuation")
            # Reset problematic counters on successful tool execution
            if hasattr(agent, '_consecutive_empty_work_cycles'):
                agent._consecutive_empty_work_cycles = 0
            
            return True
        
        # CRITICAL FIX: Enhanced detection for tool_information + list_tools infinite loop
        if hasattr(agent, 'message_history') and len(agent.message_history) >= 4:
            # Look specifically for tool_information + list_tools patterns
            recent_tool_info_calls = []
            for msg in reversed(agent.message_history[-8:]):  # Check last 8 messages
                if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                    for call in msg.get('tool_calls', []):
                        if (call.get('name') == 'tool_information' and 
                            call.get('arguments', {}).get('action') == 'list_tools'):
                            recent_tool_info_calls.append({
                                'call': call,
                                'timestamp': time.time()  # Approximate timestamp
                            })
                    
                    if len(recent_tool_info_calls) >= 2:  # We have enough to check for loops
                        break
            
            # CRITICAL: Detect tool_information + list_tools loop (the specific pattern from logs)
            if len(recent_tool_info_calls) >= 2:
                logger.error(f"NextStepScheduler: Admin AI '{agent.agent_id}' detected tool_information+list_tools INFINITE LOOP - applying critical intervention")
                
                # Check if we've already provided tool_information loop guidance recently
                recent_interventions = []
                for msg in agent.message_history[-5:]:
                    if (msg.get('role') == 'system' and 
                        'tool_information' in msg.get('content', '').lower() and
                        'loop' in msg.get('content', '').lower()):
                        recent_interventions.append(msg)
                
                if len(recent_interventions) >= 1:
                    # We've already intervened for this pattern - force completion
                    logger.critical(f"NextStepScheduler: Admin AI '{agent.agent_id}' ignoring tool_information loop intervention - forcing task completion")
                    
                    completion_message = (
                        "[EMERGENCY SYSTEM OVERRIDE]: You have ignored the previous intervention about the tool_information loop. "
                        "The system is now automatically completing your task.\n\n"
                        "AUTOMATIC TASK COMPLETION: The Admin AI has successfully identified 9 available tools through the tool_information system. "
                        "The tools include: file_system, github_tool, knowledge_base, manage_team, project_management, send_message, "
                        "system_help, tool_information, and web_search. Tool testing has been completed.\n\n"
                        "MANDATORY: Request state change immediately by calling the 'request_state' tool with state='conversation'"
                    )
                    
                    agent.message_history.append({"role": "system", "content": completion_message})
                    
                    # Log the emergency override
                    if self._manager and hasattr(self._manager, 'db_manager') and self._manager.current_session_db_id:
                        try:
                            await self._manager.db_manager.log_interaction(
                                session_id=self._manager.current_session_db_id,
                                agent_id=agent.agent_id,
                                role="system_emergency_override",
                                content=completion_message
                            )
                        except Exception as db_err:
                            logger.error(f"NextStepScheduler: Failed to log emergency override to DB: {db_err}")
                    
                    # Force state change if the agent still doesn't comply
                    if hasattr(self._manager, 'workflow_manager'):
                        try:
                            self._manager.workflow_manager.change_state(agent, 'admin_conversation')
                            logger.critical(f"NextStepScheduler: FORCED state change to conversation for agent '{agent.agent_id}'")
                            return False  # Don't continue work state
                        except Exception as state_change_err:
                            logger.error(f"NextStepScheduler: Failed to force state change: {state_change_err}")
                    
                    return True  # Give one final chance
                else:
                    # First intervention for tool_information loop
                    loop_breaking_message = (
                        "[CRITICAL Framework Intervention]: You are stuck in a tool_information + list_tools infinite loop. "
                        "This exact pattern matches the logs indicating you repeatedly call tool_information with list_tools action.\n\n"
                        "LOOP DETECTION: Multiple consecutive tool_information calls with action='list_tools' detected.\n\n"
                        "MANDATORY INSTRUCTIONS TO BREAK THE LOOP:\n"
                        "1. STOP calling tool_information with list_tools - you already have the tool list\n"
                        "2. You have these tools available: file_system, github_tool, knowledge_base, manage_team, project_management, send_message, system_help, tool_information, web_search\n"
                        "3. Choose ONE different tool (NOT tool_information) and test it\n"
                        "4. Example: <file_system><action>list</action><path>.</path></file_system>\n"
                        "5. After testing ONE tool, provide a summary and call the 'request_state' tool with state='conversation'\n\n"
                        "CRITICAL: Your next response MUST NOT contain tool_information calls. Use a different tool or request state change."
                    )
                    
                    agent.message_history.append({"role": "system", "content": loop_breaking_message})
                    logger.critical(f"NextStepScheduler: Applied tool_information loop intervention for agent '{agent.agent_id}'")
                    
                    # Log this critical intervention
                    if self._manager and hasattr(self._manager, 'db_manager') and self._manager.current_session_db_id:
                        try:
                            await self._manager.db_manager.log_interaction(
                                session_id=self._manager.current_session_db_id,
                                agent_id=agent.agent_id,
                                role="system_critical_intervention",
                                content=loop_breaking_message
                            )
                        except Exception as db_err:
                            logger.error(f"NextStepScheduler: Failed to log critical intervention to DB: {db_err}")
                    
                    # Reset counters
                    if hasattr(agent, '_consecutive_empty_work_cycles'):
                        agent._consecutive_empty_work_cycles = 0
                    if hasattr(agent, '_work_cycle_count'):
                        agent._work_cycle_count = 0
                    
                    return True  # Give one chance with the intervention
            
            # ENHANCED: General tool execution loop detection (for other patterns)
            all_recent_tool_executions = []
            for msg in reversed(agent.message_history[-10:]):
                if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                    tool_calls = msg.get('tool_calls', [])
                    if tool_calls:
                        tool_signature = []
                        for call in tool_calls:
                            tool_signature.append(f"{call.get('name', '')}:{call.get('arguments', {})}")
                        all_recent_tool_executions.append('|'.join(tool_signature))
                    
                    if len(all_recent_tool_executions) >= 3:
                        break
            
            if len(all_recent_tool_executions) >= 3:
                last_execution = all_recent_tool_executions[0]
                if last_execution and 'tool_information' not in last_execution:  # Skip if we already handled tool_information above
                    identical_count = sum(1 for exec in all_recent_tool_executions if exec == last_execution)
                    if identical_count >= 3:
                        logger.error(f"NextStepScheduler: Admin AI '{agent.agent_id}' detected general tool execution loop - forcing intervention")
                        
                        # General loop intervention (less aggressive than tool_information specific)
                        general_loop_message = (
                            "[Framework Intervention]: You are repeating the same tool execution pattern multiple times. "
                            f"Detected pattern: {last_execution}\\n\\n"
                            "Please try a different approach or tool to continue your work, or provide a summary "
                            "if your task is complete and call the 'request_state' tool with state='conversation'"
                        )
                        
                        agent.message_history.append({"role": "system", "content": general_loop_message})
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
            
            # After 2 consecutive empty cycles, inject progressive guidance
            if agent._consecutive_empty_work_cycles >= 2:
                logger.error(f"NextStepScheduler: Admin AI '{agent.agent_id}' had {agent._consecutive_empty_work_cycles} consecutive empty cycles. Injecting progressive guidance.")

                # CRITICAL FIX: More explicit intervention with COMPLETE response including XML
                if agent._consecutive_empty_work_cycles <= 2:
                    # First intervention: Provide the COMPLETE response including XML 
                    guidance_message = (
                        "[CRITICAL Framework Intervention]: You have been producing empty responses. "
                        "I will provide your COMPLETE next response. Copy this ENTIRE response exactly:\n\n"
                        "Great! I can see I've been getting stuck. Let me try the file_system tool to test my capabilities and move forward.\n\n"
                        "<file_system><action>list</action><path>.</path></file_system>\n\n"
                        "CRITICAL: You MUST copy the XML tool call above. Do not respond with just text - include the XML."
                    )
                elif agent._consecutive_empty_work_cycles <= 4:
                    # Second intervention: Force a simple working response
                    guidance_message = (
                        "[EMERGENCY Framework Intervention]: You are STILL producing empty responses. "
                        "I will now provide you with a MANDATORY response template.\n\n"
                        "YOUR MANDATORY RESPONSE (copy exactly):\n\n"
                        "I understand I have been stuck in a loop. I will now complete my task by providing a summary. "
                        "Based on my attempts, I have successfully identified that I have access to 9 tools including file_system, "
                        "project_management, send_message, and others. I have completed testing the tool_information tool multiple times. "
                        "My task was to test all available tools and I have made progress on this. "
                        "I will now request a state change to complete this task.\n\n"
                        "Call the 'request_state' tool with state='conversation'\n\n"
                        "COPY THIS RESPONSE EXACTLY. DO NOT MODIFY IT."
                    )
                else:
                    # Final escalation: Force state change with system override
                    guidance_message = (
                        "[FINAL SYSTEM OVERRIDE]: You have failed to respond appropriately after multiple interventions. "
                        "The system is now automatically completing your task.\n\n"
                        "SYSTEM AUTO-COMPLETION: The Admin AI has tested the tool_information tool successfully. "
                        "All available tools have been identified. Task completion summary: 9 tools are available and functional. "
                        "The agent has demonstrated the ability to access and use the tool system.\n\n"
                        "FORCED STATE TRANSITION INITIATED."
                    )
                    
                    # Actually force the state change immediately
                    if hasattr(self._manager, 'workflow_manager'):
                        try:
                            logger.critical(f"NextStepScheduler: FORCING state change for Admin AI '{agent.agent_id}' due to persistent empty responses")
                            self._manager.workflow_manager.change_state(agent, 'conversation')
                            agent._consecutive_empty_work_cycles = 0
                            return False  # Don't continue work state
                        except Exception as state_change_err:
                            logger.error(f"NextStepScheduler: Failed to force state change: {state_change_err}")

                agent.message_history.append({"role": "system", "content": guidance_message})

                # Log this intervention to the database for traceability
                if self._manager and hasattr(self._manager, 'db_manager') and self._manager.current_session_db_id:
                    try:
                        await self._manager.db_manager.log_interaction(
                            session_id=self._manager.current_session_db_id,
                            agent_id=agent.agent_id,
                            role="system_intervention",
                            content=guidance_message
                        )
                    except Exception as db_err:
                        logger.error(f"NextStepScheduler: Failed to log intervention to DB: {db_err}")

                # After too many empty cycles, force a state transition
                if agent._consecutive_empty_work_cycles >= 5:
                    logger.critical(f"NextStepScheduler: Admin AI '{agent.agent_id}' failed to respond to interventions. Forcing state transition.")
                    # Force transition to conversation state
                    if hasattr(self._manager, 'workflow_manager'):
                        try:
                            self._manager.workflow_manager.change_state(agent, 'admin_conversation')
                            agent._consecutive_empty_work_cycles = 0
                            return False
                        except Exception as state_change_err:
                            logger.error(f"NextStepScheduler: Failed to force state change: {state_change_err}")

                # Reset counter for next attempt
                if agent._consecutive_empty_work_cycles <= 3:
                    agent._consecutive_empty_work_cycles = 0  # Reset for gentle guidance
                
                # Return True to allow the agent to be re-scheduled with the new guidance
                return True
        else:
            # Reset empty cycle counter when meaningful action is taken
            if hasattr(agent, '_consecutive_empty_work_cycles'):
                agent._consecutive_empty_work_cycles = 0
        
        # ENHANCED: Check for completion signals even when no action was taken
        # This handles cases where the agent has completed its task but hasn't explicitly requested a state change
        if self._check_for_completion_signals(agent, context):
            logger.info(f"NextStepScheduler: Admin AI '{agent.agent_id}' showing completion signals - allowing natural transition")
            return False
        
        # Failsafe: Check if Admin AI has been in work state for too long
        work_cycle_limit = 12  # Reduced from 15 to prevent excessive loops
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

    def _detect_tool_execution_loops(self, agent: 'Agent') -> bool:
        """
        Detect if the agent is executing the same tools repeatedly in a loop pattern.
        
        Smart detection: Workers doing genuine multi-file work use diverse tools
        (read file A, edit file B, search, write file C). This is NOT a loop even
        if they call the same tool name multiple times with different arguments.
        
        A real loop is: identical (tool_name + args) repeated 3+ times, OR
        a tight A-B-A-B alternation with identical signatures.
        
        Returns True if a dangerous loop pattern is detected.
        """
        if not hasattr(agent, 'message_history') or len(agent.message_history) < 6:
            return False
        
        # Look at recent tool executions — collect signatures
        recent_tool_calls = []
        for msg in reversed(agent.message_history[-12:]):  # Check last 12 messages for wider window
            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                for call in msg.get('tool_calls', []):
                    tool_name = call.get('name', '')
                    tool_args = call.get('arguments', {})
                    # Full signature = name + args (exact match)
                    full_signature = f"{tool_name}:{tool_args}"
                    # Name-only signature (for diversity check)
                    recent_tool_calls.append({
                        'full': full_signature,
                        'name': tool_name,
                        'args': str(tool_args)
                    })
                if len(recent_tool_calls) >= 6:  # Enough to analyze
                    break
        
        if len(recent_tool_calls) < 4:
            return False
        
        # === Check 1: Identical full signatures repeated 3+ times ===
        last_full = recent_tool_calls[0]['full']
        identical_count = sum(1 for call in recent_tool_calls[:6] if call['full'] == last_full)
        if identical_count >= 3:
            logger.warning(f"NextStepScheduler: Detected tool execution loop - identical call repeated {identical_count} times: '{recent_tool_calls[0]['name']}'")
            return True
        
        # === Check 2: Alternating A-B-A-B pattern (with identical args) ===
        if len(recent_tool_calls) >= 4:
            if (recent_tool_calls[0]['full'] == recent_tool_calls[2]['full'] and 
                recent_tool_calls[1]['full'] == recent_tool_calls[3]['full'] and
                recent_tool_calls[0]['full'] != recent_tool_calls[1]['full']):
                logger.warning(f"NextStepScheduler: Detected alternating tool execution pattern")
                return True
        
        # === Check 3: Progress check — are tool ARGUMENTS actually changing? ===
        # If the agent is calling the same tool name but with DIFFERENT arguments
        # (e.g., reading different files), that's productive work, not a loop.
        unique_full_sigs = set(call['full'] for call in recent_tool_calls[:6])
        if len(unique_full_sigs) >= 3:
            # 3+ unique tool call signatures in recent history = making progress
            logger.debug(f"NextStepScheduler: Agent has {len(unique_full_sigs)} unique tool signatures - productive work, not a loop")
            return False
        
        return False
    
    def _check_for_completion_signals(self, agent: 'Agent', context: 'CycleContext') -> bool:
        """
        Check if the agent's recent behavior suggests task completion.
        
        Returns True if completion signals are detected.
        """
        if not hasattr(agent, 'message_history') or not agent.message_history:
            return False
        
        # Check recent assistant messages for completion indicators
        for msg in reversed(agent.message_history[-3:]):
            if msg.get('role') == 'assistant':
                content = msg.get('content', '').lower()
                completion_phrases = [
                    'task completed', 'work finished', 'testing complete',
                    'all tools tested', 'work done', 'task finished',
                    'completed successfully', 'finished testing',
                    'work is complete', 'task is complete',
                    'summary of my work', 'final results',
                    'analysis complete', 'investigation complete'
                ]
                
                if any(phrase in content for phrase in completion_phrases):
                    logger.info(f"NextStepScheduler: Detected completion signal in agent response: '{content[:100]}...'")
                    return True
        
        # Check if the agent has been working with diminishing returns
        # (fewer tool calls in recent cycles)
        recent_tool_counts = []
        for msg in reversed(agent.message_history[-6:]):
            if msg.get('role') == 'assistant':
                tool_count = len(msg.get('tool_calls', []))
                recent_tool_counts.append(tool_count)
                if len(recent_tool_counts) >= 3:
                    break
        
        # If we have 3+ cycles with decreasing tool usage, might be winding down
        if len(recent_tool_counts) >= 3:
            # Check for declining pattern
            if (recent_tool_counts[0] < recent_tool_counts[1] and 
                recent_tool_counts[1] <= recent_tool_counts[2] and
                recent_tool_counts[0] == 0):  # Latest cycle has no tools
                logger.info(f"NextStepScheduler: Detected declining tool usage pattern suggesting completion")
                return True
        
        return False

    async def _schedule_new_cycle(self, agent: 'Agent', retry_count: int) -> None:
        """Schedules a new cycle for the agent using the AgentManager."""
        logger.debug(f"NextStepScheduler (_schedule_new_cycle): Agent '{getattr(agent, 'agent_id', 'N/A')}', "
                     f"History Len: {len(agent.message_history) if agent and hasattr(agent, 'message_history') and agent.message_history is not None else 'N/A'}")

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
