# START OF FILE src/agents/workflow_manager.py
import logging
import datetime # Added import
from typing import Dict, List, Optional, TYPE_CHECKING, Tuple, Any # Added Tuple, Any

# Import constants
from src.agents.constants import (
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED,
    AGENT_STATE_CONVERSATION, AGENT_STATE_WORK # Import general states too
)
# Import settings for prompt access
from src.config.settings import settings

# Type hinting
if TYPE_CHECKING:
    from src.agents.core import Agent

logger = logging.getLogger(__name__)

class AgentWorkflowManager:
    """
    Manages agent workflow states, transitions, and state-specific prompt selection.
    """
    def __init__(self):
        # Define valid states per agent type
        self._valid_states: Dict[str, List[str]] = {
            AGENT_TYPE_ADMIN: [
                ADMIN_STATE_STARTUP,
                ADMIN_STATE_CONVERSATION, # Distinct admin conversation state
                ADMIN_STATE_PLANNING,
                ADMIN_STATE_WORK_DELEGATED,
                AGENT_STATE_WORK # Add 'work' state for Admin AI tool use
            ],
            AGENT_TYPE_PM: [
                AGENT_STATE_CONVERSATION,
                AGENT_STATE_WORK # Add 'work' state
            ],
            AGENT_TYPE_WORKER: [
                AGENT_STATE_CONVERSATION,
                AGENT_STATE_WORK # Add 'work' state
            ]
            # Add other agent types if needed
        }
        # Map agent type and state to prompt keys in prompts.json
        self._prompt_map: Dict[Tuple[str, str], str] = {
            (AGENT_TYPE_ADMIN, ADMIN_STATE_STARTUP): "admin_ai_startup_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_CONVERSATION): "admin_ai_conversation_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_PLANNING): "admin_ai_planning_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_WORK_DELEGATED): "admin_ai_delegated_prompt",
            (AGENT_TYPE_ADMIN, AGENT_STATE_WORK): "agent_work_prompt", # Admin uses the generic work prompt too
            (AGENT_TYPE_PM, AGENT_STATE_CONVERSATION): "pm_conversation_prompt",
            (AGENT_TYPE_WORKER, AGENT_STATE_CONVERSATION): "agent_conversation_prompt",
            # Add mappings for 'work' state
            (AGENT_TYPE_PM, AGENT_STATE_WORK): "agent_work_prompt",
            (AGENT_TYPE_WORKER, AGENT_STATE_WORK): "agent_work_prompt"
        }
        logger.info("AgentWorkflowManager initialized.")

    def is_valid_state(self, agent_type: str, state: str) -> bool:
        """Checks if a state is valid for a given agent type."""
        return state in self._valid_states.get(agent_type, [])

    def change_state(self, agent: 'Agent', requested_state: str) -> bool:
        """
        Attempts to change the agent's state, validating against allowed states for its type.

        Args:
            agent: The Agent instance.
            requested_state: The desired new state.

        Returns:
            True if the state was changed successfully, False otherwise.
        """
        if not hasattr(agent, 'agent_type') or not agent.agent_type:
            logger.error(f"Cannot change state for agent '{agent.agent_id}': Missing 'agent_type'.")
            return False

        if self.is_valid_state(agent.agent_type, requested_state):
            if agent.state != requested_state:
                logger.info(f"WorkflowManager: Changing state for agent '{agent.agent_id}' ({agent.agent_type}) from '{agent.state}' to '{requested_state}'.")
                agent.state = requested_state
                # Note: Pushing status update might happen elsewhere (e.g., CycleHandler after state change)
                return True
            else:
                logger.debug(f"WorkflowManager: Agent '{agent.agent_id}' already in state '{requested_state}'. No change.")
                return True # Considered successful as it's already in the target state
        else:
            logger.warning(f"WorkflowManager: Invalid state transition requested for agent '{agent.agent_id}' ({agent.agent_type}) to state '{requested_state}'. Allowed states: {self._valid_states.get(agent.agent_type, [])}")
            return False

    def get_system_prompt(self, agent: 'Agent', manager: Optional[Any] = None) -> str:
        """
        Gets the appropriate system prompt based on the agent's type and current state.
        Formats the prompt with relevant context.

        Args:
            agent: The Agent instance.
            manager: The AgentManager instance (needed for context like project/session).

        Returns:
            The formatted system prompt string.
        """
        if not hasattr(agent, 'agent_type') or not agent.agent_type:
            logger.error(f"Cannot get prompt for agent '{agent.agent_id}': Missing 'agent_type'. Using default.")
            return settings.DEFAULT_SYSTEM_PROMPT
        if not hasattr(agent, 'state') or not agent.state:
            logger.error(f"Cannot get prompt for agent '{agent.agent_id}': Missing 'state'. Using default.")
            return settings.DEFAULT_SYSTEM_PROMPT

        prompt_key = self._prompt_map.get((agent.agent_type, agent.state))

        if not prompt_key:
            logger.warning(f"WorkflowManager: No specific prompt key found for agent type '{agent.agent_type}' in state '{agent.state}'. Falling back to default conversation prompt for type or absolute default.")
            # Fallback logic: Try default conversation prompt for type, then absolute default
            # Use the correct conversation state constant based on agent type
            default_conv_state = ADMIN_STATE_CONVERSATION if agent.agent_type == AGENT_TYPE_ADMIN else AGENT_STATE_CONVERSATION
            prompt_key = self._prompt_map.get((agent.agent_type, default_conv_state))
            if not prompt_key:
                logger.error(f"WorkflowManager: No default conversation prompt found for type '{agent.agent_type}'. Using absolute default.")
                return settings.DEFAULT_SYSTEM_PROMPT

        prompt_template = settings.PROMPTS.get(prompt_key)
        if not prompt_template:
            logger.error(f"WorkflowManager: Prompt template for key '{prompt_key}' not found in settings.PROMPTS. Using absolute default.")
            return settings.DEFAULT_SYSTEM_PROMPT

        # --- Format the prompt with context ---
        # Gather context needed for formatting
        formatting_context = {
            "agent_id": agent.agent_id,
            "persona": agent.persona,
            # Add other common placeholders
        }
        if manager:
            formatting_context["project_name"] = getattr(manager, 'current_project', 'N/A')
            formatting_context["session_name"] = getattr(manager, 'current_session', 'N/A')
            # Add team ID if available
            team_id = getattr(manager.state_manager, 'get_agent_team', lambda x: None)(agent.agent_id)
            formatting_context["team_id"] = team_id or "N/A"
            # Add current time
            formatting_context["current_time_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(sep=' ', timespec='seconds')
            # Add tool descriptions if needed by the prompt template
            if "{tool_descriptions_xml}" in prompt_template:
                 formatting_context["tool_descriptions_xml"] = getattr(manager, 'tool_descriptions_xml', "<!-- Tool descriptions unavailable -->")
            if "{tool_descriptions_json}" in prompt_template:
                 formatting_context["tool_descriptions_json"] = getattr(manager, 'tool_descriptions_json', "{}")
            # TODO: Add project task list for PM? Requires manager access to ProjectManagementTool instance for the session.

        try:
            # Prepend user-defined part ONLY for Admin AI in startup or conversation states
            user_defined_part = ""
            # --- MODIFIED: Check agent type and state before prepending ---
            if agent.agent_type == AGENT_TYPE_ADMIN and agent.state in [ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION]:
                if hasattr(agent, 'final_system_prompt') and isinstance(agent.final_system_prompt, str):
                    # Check if the original prompt contained the standard instructions marker
                    # (This check might be redundant if lifecycle always structures it, but safe to keep)
                    if "--- Standard Tool & Communication Protocol ---" in agent.final_system_prompt or \
                       "--- Admin AI Core Operational Workflow ---" in agent.final_system_prompt or \
                       "--- Primary Goal/Persona ---" in agent.final_system_prompt: # Check for admin marker too
                         user_defined_part = agent.final_system_prompt.split("\n\n---")[0] + "\n\n" # Extract user part
                         logger.debug(f"Prepending user-defined part for Admin AI in state '{agent.state}'.")
                    # else: # If no marker, assume the whole original prompt was user-defined? Risky.
                    #    user_defined_part = agent.final_system_prompt + "\n\n"
            # --- END MODIFICATION ---

            # Format the loaded template
            formatted_state_prompt = prompt_template.format(**formatting_context)

            # Combine user part (if any) and the formatted state-specific instructions
            final_prompt = user_defined_part + formatted_state_prompt
            logger.info(f"WorkflowManager: Generated prompt for agent '{agent.agent_id}' using key '{prompt_key}'.")
            return final_prompt

        except KeyError as fmt_err:
            logger.error(f"WorkflowManager: Failed to format prompt template '{prompt_key}'. Missing key: {fmt_err}. Using absolute default prompt.")
            return settings.DEFAULT_SYSTEM_PROMPT
        except Exception as e:
            logger.error(f"WorkflowManager: Unexpected error formatting prompt template '{prompt_key}': {e}. Using absolute default prompt.", exc_info=True)
            return settings.DEFAULT_SYSTEM_PROMPT
