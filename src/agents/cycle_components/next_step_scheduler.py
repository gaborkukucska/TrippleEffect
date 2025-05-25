# START OF FILE src/agents/cycle_components/next_step_scheduler.py
import logging
import asyncio
from typing import TYPE_CHECKING
import json # <<< --- ADDED IMPORT ---

from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_ERROR,
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED,
    PM_STATE_STARTUP, PM_STATE_MANAGE, PM_STATE_WORK,
    # Add new PM states for checks
    PM_STATE_PLAN_DECOMPOSITION, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS,
    WORKER_STATE_WAIT
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
            if agent.agent_type == AGENT_TYPE_PM and \
               agent.state == PM_STATE_STARTUP and \
               not context.action_taken_this_cycle and \
               not context.thought_produced_this_cycle: 
                logger.warning(f"NextStepScheduler: PM agent '{agent_id}' in PM_STARTUP finished but took NO action (tool/state/thought). Reactivating to enforce startup workflow.")
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                agent.set_status(AGENT_STATUS_IDLE)
                await self._schedule_new_cycle(agent, 0)
            elif agent.agent_type == AGENT_TYPE_PM and \
                 agent.state in [PM_STATE_PLAN_DECOMPOSITION, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS] and \
                 not context.action_taken_this_cycle:
                logger.warning(f"NextStepScheduler: PM agent '{agent_id}' in state '{agent.state}' finished but took NO ACTION. Reactivating to enforce workflow.")
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                agent.set_status(AGENT_STATUS_IDLE)
                await self._schedule_new_cycle(agent, 0)
            else:
                logger.info(f"NextStepScheduler: Agent '{agent_id}' ({context.current_model_key_for_tracking}) finished cycle cleanly, no specific reactivation needed by this scheduler.")
                if hasattr(agent, '_failed_models_this_cycle'): agent._failed_models_this_cycle.clear()
                if agent.status != AGENT_STATUS_ERROR:
                    agent.set_status(AGENT_STATUS_IDLE)
        else:
            if not context.trigger_failover and not context.is_retryable_error_type:
                logger.warning(f"NextStepScheduler: Agent '{agent_id}' cycle ended without explicit success or trigger. Status: {agent.status}. Last Error: {context.last_error_content[:100]}. Setting Idle if not Error.")
                if agent.status != AGENT_STATUS_ERROR:
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

        logger.info(f"NextStepScheduler._schedule_new_cycle: Scheduling for agent ID '{agent.agent_id}', instance ID: {id(agent)}, retry: {retry_count}")
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