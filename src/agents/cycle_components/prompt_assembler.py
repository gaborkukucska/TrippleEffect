# START OF FILE src/agents/cycle_components/prompt_assembler.py
import logging
import json
from typing import TYPE_CHECKING, List, Optional

from src.llm_providers.base import MessageDict
from src.agents.constants import BOOTSTRAP_AGENT_ID

if TYPE_CHECKING:
    from src.agents.core import Agent
    from src.agents.manager import AgentManager
    from src.agents.cycle_components.cycle_context import CycleContext

logger = logging.getLogger(__name__)

class PromptAssembler:
    """
    Assembles the system prompt and message history for an LLM call within a cycle.
    Handles injection of system health reports for Admin AI.
    """

    def __init__(self, manager: 'AgentManager'):
        self._manager = manager

    async def _generate_system_health_report(self, agent: 'Agent') -> Optional[str]:
        """
        Generates a concise system health report based on the agent's recent history.
        (This logic is moved from the old CycleHandler)
        """
        # Logic for health report generation will be placed here.
        # For now, returning a placeholder.
        # Note: This was simplified in previous steps to be a static message if last turn was OK.
        # If more complex logic is needed again, it can be re-implemented here.
        # Based on the log, the last version was simple.
        if not agent or not agent.message_history:
             # If no history, assume it's the first turn for the agent or history was cleared.
            return "[Framework Internal Status: System is initializing or history is fresh. This is not a user query.]"

        # Simplified version from logs:
        # If the last message was not an error/tool failure, report OK.
        # This check is simplistic and might need to be more robust based on actual desired behavior.
        # For now, let's assume any recent activity implies 'OK' unless an error is explicitly logged.
        # A more robust check would look at the *types* of recent messages.
        # However, the log shows it simply injects "[Framework Internal Status: Last turn OK...]"
        # if there wasn't a specific error from the *previous* cycle of THIS agent.
        # The CycleContext will manage the "last_error_obj" for this agent's current cycle.
        # So, if we reach here, it means the previous cycle for *this agent* didn't end in a failover.
        return "[Framework Internal Status: Last turn OK. This is not a user query.]"


    async def prepare_llm_call_data(self, context: 'CycleContext') -> None:
        """
        Prepares the final system prompt and message history for the LLM call,
        storing them in the `CycleContext`.

        Args:
            context: The CycleContext object for the current agent cycle.
        """
        agent = context.agent
        manager = self._manager # AgentManager is passed to PromptAssembler constructor

        # 1. Get State-Specific System Prompt using WorkflowManager
        if not hasattr(manager, 'workflow_manager'):
            logger.error("PromptAssembler: WorkflowManager not found on AgentManager! Cannot get state-specific prompt.")
            context.final_system_prompt = agent.final_system_prompt or manager.settings.DEFAULT_SYSTEM_PROMPT
        else:
            context.final_system_prompt = manager.workflow_manager.get_system_prompt(agent, manager)
        
        logger.debug(f"PromptAssembler: Base system prompt for agent '{agent.agent_id}' (Type: {agent.agent_type}, State: {agent.state}) set.")

        # 2. Prepare History for LLM Call
        history_for_call = agent.message_history.copy() # Start with agent's current history
        logger.debug(f"PromptAssembler '{agent.agent_id}': Raw agent.message_history (len {len(agent.message_history)}) before modifications: {json.dumps(agent.message_history, indent=2)}")
        # Ensure system prompt is at the start of history
        if not history_for_call or history_for_call[0].get("role") != "system":
            history_for_call.insert(0, {"role": "system", "content": context.final_system_prompt})
        else:
            # Overwrite if it exists, ensuring it's the latest one for the current state
            history_for_call[0] = {"role": "system", "content": context.final_system_prompt}

        # 3. Inject System Health Report (Admin AI only)
        if agent.agent_id == BOOTSTRAP_AGENT_ID: # Check agent ID
            system_health_report = await self._generate_system_health_report(agent)
            if system_health_report:
                health_msg: MessageDict = {"role": "system", "content": system_health_report}
                # Insert *after* the main system prompt but before other history
                if len(history_for_call) > 1:
                    history_for_call.insert(1, health_msg)
                else:
                    history_for_call.append(health_msg) # Append if only system prompt was there
                logger.debug(f"Injected system health report for Admin AI '{agent.agent_id}'.")
        
        context.history_for_call = history_for_call

        # 4. Log the history being sent to the LLM
        logger.debug(f"PromptAssembler: Final history being sent to LLM for agent '{agent.agent_id}' (state: {agent.state}, length {len(context.history_for_call)}):")
        for i, msg_to_log in enumerate(context.history_for_call):
            content_preview = str(msg_to_log.get('content'))[:200]
            tool_calls_preview = msg_to_log.get('tool_calls')
            log_line = f"  [{i}] Role: {msg_to_log.get('role')}, Content: {content_preview}{'...' if len(str(msg_to_log.get('content'))) > 200 else ''}"
            if tool_calls_preview:
                log_line += f", ToolCalls: {json.dumps(tool_calls_preview)}"
            logger.debug(log_line)