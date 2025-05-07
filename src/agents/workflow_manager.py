# START OF FILE src/agents/workflow_manager.py
import logging
import datetime # Added import
import asyncio # Import asyncio
from typing import Dict, List, Optional, TYPE_CHECKING, Tuple, Any # Added Tuple, Any

# Import constants
from src.agents.constants import (
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED,
    AGENT_STATE_CONVERSATION, AGENT_STATE_WORK, AGENT_STATE_MANAGE # Import general states and MANAGE
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
    Sets a flag on PM agents when transitioning to the 'work' state to trigger
    the initial mandatory 'list_tools' call in the CycleHandler.
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
                AGENT_STATE_WORK, # Add 'work' state
                AGENT_STATE_MANAGE # Add 'manage' state
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
            (AGENT_TYPE_ADMIN, AGENT_STATE_WORK): "admin_work_prompt", # Use specific admin work prompt
            (AGENT_TYPE_PM, AGENT_STATE_CONVERSATION): "pm_conversation_prompt",
            (AGENT_TYPE_WORKER, AGENT_STATE_CONVERSATION): "agent_conversation_prompt",
            # Use role-specific work/manage prompts
            (AGENT_TYPE_PM, AGENT_STATE_WORK): "pm_work_prompt",
            (AGENT_TYPE_PM, AGENT_STATE_MANAGE): "pm_manage_prompt", # Add mapping for PM manage state
            (AGENT_TYPE_WORKER, AGENT_STATE_WORK): "worker_work_prompt"
        }
        logger.info("AgentWorkflowManager initialized.")

    def is_valid_state(self, agent_type: str, state: str) -> bool:
        """Checks if a state is valid for a given agent type."""
        return state in self._valid_states.get(agent_type, [])

    def change_state(self, agent: 'Agent', requested_state: str) -> bool:
        """
        Attempts to change the agent's state, validating against allowed states for its type.
        Sets a flag on PM agents when entering the 'work' state.

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
                # --- NEW: Set flag for PM entering WORK state ---
                if agent.agent_type == AGENT_TYPE_PM and requested_state == AGENT_STATE_WORK:
                    agent._pm_needs_initial_list_tools = True
                    logger.info(f"WorkflowManager: Set '_pm_needs_initial_list_tools' flag for agent '{agent.agent_id}'.")
                # --- END NEW ---
                # --- NEW: Send UI notification on state change ---
                if hasattr(agent, 'manager') and hasattr(agent.manager, 'send_to_ui'):
                    asyncio.create_task(agent.manager.send_to_ui({
                        "type": "agent_state_change", # Specific type for UI
                        "agent_id": agent.agent_id,
                        "old_state": agent.state, # Include old state for context
                        "new_state": requested_state,
                        "message": f"Agent '{agent.agent_id}' state changed from '{agent.state}' to '{requested_state}'."
                    }))
                # --- END NEW ---
                return True
            else:
                logger.debug(f"WorkflowManager: Agent '{agent.agent_id}' already in state '{requested_state}'. No change.")
                # Clear flag if re-entering the same state (shouldn't happen often, but safe)
                if agent.agent_type == AGENT_TYPE_PM and requested_state == AGENT_STATE_WORK:
                    agent._pm_needs_initial_list_tools = False
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
            # If state is None (e.g., during initial creation before lifecycle sets it), use a default
            logger.warning(f"Agent '{agent.agent_id}' state is None. Using default conversation prompt for type or absolute default.")
            default_conv_state = AGENT_STATE_CONVERSATION # Use general conversation as fallback
            prompt_key = self._prompt_map.get((agent.agent_type, default_conv_state))
            if not prompt_key:
                logger.error(f"WorkflowManager: No default conversation prompt found for type '{agent.agent_type}'. Using absolute default.")
                return settings.DEFAULT_SYSTEM_PROMPT
        else:
            # Normal state lookup
            prompt_key = self._prompt_map.get((agent.agent_type, agent.state))

            if not prompt_key:
                logger.warning(f"WorkflowManager: No specific prompt key found for agent type '{agent.agent_type}' in state '{agent.state}'. Falling back to default conversation prompt for type or absolute default.")
                # Fallback logic: Try default conversation prompt for type, then absolute default
                default_conv_state = AGENT_STATE_CONVERSATION # Use general conversation as fallback
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
            # --- NEW: Add task_description/plan_description for PM in WORK state ---
            if agent.agent_type == AGENT_TYPE_PM and agent.state == AGENT_STATE_WORK:
                if hasattr(agent, 'plan_description'):
                    formatting_context["task_description"] = agent.plan_description
                    logger.debug(f"WorkflowManager: Added 'task_description' to formatting context for PM agent '{agent.agent_id}' in WORK state.")
                else:
                    logger.warning(f"WorkflowManager: PM agent '{agent.agent_id}' in WORK state missing 'plan_description' attribute.")
            # --- END NEW ---
            # Add tool descriptions if needed by the prompt template
            if "{tool_descriptions_xml}" in prompt_template:
                 formatting_context["tool_descriptions_xml"] = getattr(manager, 'tool_descriptions_xml', "<!-- Tool descriptions unavailable -->")
            if "{tool_descriptions_json}" in prompt_template:
                 formatting_context["tool_descriptions_json"] = getattr(manager, 'tool_descriptions_json', "{}")
            # --- REMOVED available_tools_list injection block ---
            # TODO: Add project task list for PM? Requires manager access to ProjectManagementTool instance for the session.

        try:
            # --- NEW: Get personality instructions for Admin AI ---
            personality_instructions = ""
            if agent.agent_type == AGENT_TYPE_ADMIN:
                # Access the stored config prompt
                if hasattr(agent, '_config_system_prompt') and isinstance(agent._config_system_prompt, str):
                    personality_instructions = agent._config_system_prompt.strip()
                    if personality_instructions:
                         logger.debug(f"Found personality instructions for Admin AI from config.")
                    else:
                         logger.debug("Admin AI config system_prompt is empty, no personality instructions added.")
                else:
                     logger.warning("Admin AI missing '_config_system_prompt' attribute or it's not a string.")
            # Add personality instructions to the formatting context if available
            formatting_context["personality_instructions"] = personality_instructions
            # --- END NEW ---

            # Format the loaded template using the potentially updated context
            formatted_state_prompt = prompt_template.format(**formatting_context)

            # The final prompt is just the formatted state prompt now
            final_prompt = formatted_state_prompt
            logger.info(f"WorkflowManager: Generated prompt for agent '{agent.agent_id}' using key '{prompt_key}'.")
            return final_prompt

        except KeyError as fmt_err:
            # --- MODIFIED: Handle missing personality_instructions gracefully ---
            missing_key = str(fmt_err).strip("'") # Get the missing key name
            if missing_key == "personality_instructions":
                 # This is expected for non-admin agents or if the admin prompt doesn't use it. Log as debug.
                 logger.debug(f"WorkflowManager: Prompt template '{prompt_key}' is missing the optional '{{personality_instructions}}' placeholder. Proceeding without it.")
                 # Attempt to format again after removing the key from context
                 formatting_context.pop("personality_instructions", None)
                 try:
                     formatted_state_prompt = prompt_template.format(**formatting_context)
                     return formatted_state_prompt # Return successfully formatted prompt without personality
                 except KeyError as inner_fmt_err:
                      # Failure even after removing personality means another key is missing
                      logger.error(f"WorkflowManager: Failed to format prompt template '{prompt_key}' even after removing personality placeholder. Missing key: {inner_fmt_err}. Using absolute default prompt.")
                      return settings.DEFAULT_SYSTEM_PROMPT
                 except Exception as inner_e:
                      logger.error(f"WorkflowManager: Unexpected error formatting prompt template '{prompt_key}' after removing personality placeholder: {inner_e}. Using absolute default prompt.", exc_info=True)
                      return settings.DEFAULT_SYSTEM_PROMPT
            else:
                 # Error for a different, unexpected missing key
                 logger.error(f"WorkflowManager: Failed to format prompt template '{prompt_key}'. Missing required key: {fmt_err}. Using absolute default prompt.")
                 return settings.DEFAULT_SYSTEM_PROMPT
            # --- END MODIFIED ---
        except Exception as e:
            logger.error(f"WorkflowManager: Unexpected error formatting prompt template '{prompt_key}': {e}. Using absolute default prompt.", exc_info=True)
            return settings.DEFAULT_SYSTEM_PROMPT
