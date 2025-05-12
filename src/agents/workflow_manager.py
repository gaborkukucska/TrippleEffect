# START OF FILE src/agents/workflow_manager.py
import logging
import datetime # Added import
import asyncio # Import asyncio
from typing import Dict, List, Optional, TYPE_CHECKING, Tuple, Any # Added Tuple, Any

# Import constants
from src.agents.constants import (
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_STARTUP, ADMIN_STATE_CONVERSATION, ADMIN_STATE_PLANNING, ADMIN_STATE_WORK_DELEGATED, ADMIN_STATE_WORK,
    PM_STATE_STARTUP, PM_STATE_WORK, PM_STATE_MANAGE,
    WORKER_STATE_STARTUP, WORKER_STATE_WORK, WORKER_STATE_WAIT,
    DEFAULT_STATE, BOOTSTRAP_AGENT_ID
)
# Import settings for prompt access
from src.config.settings import settings

# Type hinting
if TYPE_CHECKING:
    from src.agents.core import Agent
    from src.agents.manager import AgentManager # Import AgentManager

logger = logging.getLogger(__name__)

class AgentWorkflowManager:
    """
    Manages agent workflow states, transitions, and state-specific prompt selection.
    Sets a flag on PM agents when transitioning to the 'work' state to trigger
    the initial mandatory 'list_tools' call in the CycleHandler.
    Dynamically populates context including an address book for agent communication.
    """
    def __init__(self):
        # Define valid states per agent type
        self._valid_states: Dict[str, List[str]] = {
            AGENT_TYPE_ADMIN: [
                ADMIN_STATE_STARTUP,
                ADMIN_STATE_CONVERSATION,
                ADMIN_STATE_PLANNING,
                ADMIN_STATE_WORK_DELEGATED,
                ADMIN_STATE_WORK,
                DEFAULT_STATE
            ],
            AGENT_TYPE_PM: [
                PM_STATE_STARTUP,
                PM_STATE_WORK,
                PM_STATE_MANAGE,
                DEFAULT_STATE
            ],
            AGENT_TYPE_WORKER: [
                WORKER_STATE_STARTUP,
                WORKER_STATE_WORK,
                WORKER_STATE_WAIT,
                DEFAULT_STATE
            ]
            # Add other agent types if needed
        }
        # Map agent type and state to prompt keys in prompts.json
        # These are keys for the STATE-SPECIFIC prompts
        self._prompt_map: Dict[Tuple[str, str], str] = {
            (AGENT_TYPE_ADMIN, ADMIN_STATE_STARTUP): "admin_ai_startup_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_CONVERSATION): "admin_ai_conversation_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_PLANNING): "admin_ai_planning_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_WORK_DELEGATED): "admin_ai_delegated_prompt",
            (AGENT_TYPE_ADMIN, ADMIN_STATE_WORK): "admin_work_prompt",
            (AGENT_TYPE_ADMIN, DEFAULT_STATE): "default_system_prompt",
            (AGENT_TYPE_PM, PM_STATE_STARTUP): "pm_startup_prompt",
            (AGENT_TYPE_PM, PM_STATE_WORK): "pm_work_prompt",
            (AGENT_TYPE_PM, PM_STATE_MANAGE): "pm_manage_prompt",
            (AGENT_TYPE_PM, DEFAULT_STATE): "default_system_prompt",
            (AGENT_TYPE_WORKER, WORKER_STATE_STARTUP): "worker_startup_prompt",
            (AGENT_TYPE_WORKER, WORKER_STATE_WORK): "worker_work_prompt",
            (AGENT_TYPE_WORKER, WORKER_STATE_WAIT): "worker_wait_prompt",
            (AGENT_TYPE_WORKER, DEFAULT_STATE): "default_system_prompt"
        }
        # Map agent type to its standard framework instruction prompt key
        self._standard_instructions_map: Dict[str, str] = {
            AGENT_TYPE_ADMIN: "admin_standard_framework_instructions",
            AGENT_TYPE_PM: "pm_standard_framework_instructions",
            AGENT_TYPE_WORKER: "worker_standard_framework_instructions",
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
            current_state = agent.state # Get current state before changing
            if current_state != requested_state:
                logger.info(f"WorkflowManager: Changing state for agent '{agent.agent_id}' ({agent.agent_type}) from '{current_state}' to '{requested_state}'.")
                agent.state = requested_state
                # --- Set flag for PM entering MANAGE state ---
                if agent.agent_type == AGENT_TYPE_PM and requested_state == PM_STATE_MANAGE:
                    agent._pm_needs_initial_list_tools = True
                    logger.info(f"WorkflowManager: Set '_pm_needs_initial_list_tools' flag for agent '{agent.agent_id}'.")
                # --- END flag setting ---
                # --- Send UI notification on state change ---
                if hasattr(agent, 'manager') and hasattr(agent.manager, 'send_to_ui'):
                    asyncio.create_task(agent.manager.send_to_ui({
                        "type": "agent_state_change", # Specific type for UI
                        "agent_id": agent.agent_id,
                        "old_state": current_state, # Use stored old state
                        "new_state": requested_state,
                        "message": f"Agent '{agent.agent_id}' state changed from '{current_state}' to '{requested_state}'."
                    }))
                # --- END UI notification ---
                return True
            else:
                logger.debug(f"WorkflowManager: Agent '{agent.agent_id}' already in state '{requested_state}'. No change.")
                # Clear flag if re-entering the same state (shouldn't happen often, but safe)
                if agent.agent_type == AGENT_TYPE_PM and requested_state == PM_STATE_MANAGE:
                    agent._pm_needs_initial_list_tools = False
                return True # Considered successful as it's already in the target state
        else:
            logger.warning(f"WorkflowManager: Invalid state transition requested for agent '{agent.agent_id}' ({agent.agent_type}) to state '{requested_state}'. Allowed states: {self._valid_states.get(agent.agent_type, [])}")
            return False

    def _get_agent_project_name(self, agent: 'Agent', manager: 'AgentManager') -> str:
        """Safely gets the project name associated with an agent."""
        # PMs should have project_name from their config or the plan_description's project context
        if agent.agent_type == AGENT_TYPE_PM:
            # Check if project_name is an attribute directly (might be set if PM handles multiple projects later)
            # For now, it's more likely in agent_config or derived from manager's current context when PM was created
            if hasattr(agent, 'agent_config') and 'config' in agent.agent_config and 'project_name' in agent.agent_config['config']:
                return agent.agent_config['config']['project_name']
            # Fallback to manager's current project if PM is associated with it
            # This assumes a PM is tied to the manager's current_project when created
            if manager.current_project and agent.agent_id.startswith(f"pm_{manager.current_project.replace(' ', '_')}"):
                 return manager.current_project
        # Workers might get it from their assigned task or team context eventually.
        # For now, fallback to manager's current project if the worker doesn't have it directly.
        # A more robust way would be to link workers to projects via tasks or teams.
        if hasattr(agent, 'agent_config') and 'config' in agent.agent_config and 'project_name' in agent.agent_config['config']:
            return agent.agent_config['config']['project_name']
        return manager.current_project or "N/A"


    def _build_address_book(self, agent: 'Agent', manager: 'AgentManager') -> str:
        """Builds the address book content string for the agent."""
        content_lines = []
        agent_type = agent.agent_type
        agent_id = agent.agent_id
        agent_project_name = self._get_agent_project_name(agent, manager)


        if agent_type == AGENT_TYPE_ADMIN:
            content_lines.append(f"- Admin AI (Yourself): {agent_id}")
            pms = [ag for ag_id, ag in manager.agents.items() if ag.agent_type == AGENT_TYPE_PM and ag_id != agent_id]
            if pms:
                content_lines.append("- Project Managers (PMs):")
                for pm in pms:
                    pm_proj_name = self._get_agent_project_name(pm, manager)
                    content_lines.append(f"  - PM for '{pm_proj_name}': {pm.agent_id} (Persona: {pm.persona})")
            else:
                content_lines.append("- Project Managers (PMs): (None active currently)")

        elif agent_type == AGENT_TYPE_PM:
            content_lines.append(f"- Project Manager (Yourself): {agent_id} for Project '{agent_project_name}'")
            content_lines.append(f"- Admin AI: {BOOTSTRAP_AGENT_ID}")
            
            other_pms = [ag for ag_id, ag in manager.agents.items() if ag.agent_type == AGENT_TYPE_PM and ag_id != agent_id]
            if other_pms:
                content_lines.append("- Other Project Managers:")
                for pm in other_pms:
                    other_pm_proj_name = self._get_agent_project_name(pm, manager)
                    content_lines.append(f"  - PM for '{other_pm_proj_name}': {pm.agent_id} (Persona: {pm.persona})")

            workers_in_my_project = []
            for worker_agent in manager.agents.values():
                worker_project_name = self._get_agent_project_name(worker_agent, manager)
                if worker_agent.agent_type == AGENT_TYPE_WORKER and worker_project_name == agent_project_name:
                    if worker_agent not in workers_in_my_project: 
                        workers_in_my_project.append(worker_agent)
            
            unique_workers = list({w.agent_id: w for w in workers_in_my_project}.values()) 

            if unique_workers:
                content_lines.append(f"- Your Worker Agents (for Project '{agent_project_name}'):")
                for worker in unique_workers:
                    worker_team = manager.state_manager.get_agent_team(worker.agent_id) or "N/A"
                    content_lines.append(f"  - {worker.agent_id} (Persona: {worker.persona}, Team: {worker_team})")
            else:
                content_lines.append(f"- Your Worker Agents (for Project '{agent_project_name}'): (None created yet or in your project)")


        elif agent_type == AGENT_TYPE_WORKER:
            content_lines.append(f"- Worker (Yourself): {agent_id} for Project '{agent_project_name}'")
            content_lines.append(f"- Admin AI: {BOOTSTRAP_AGENT_ID}")
            
            my_pm: Optional['Agent'] = None
            for pm_candidate in manager.agents.values():
                pm_candidate_project_name = self._get_agent_project_name(pm_candidate, manager)
                if pm_candidate.agent_type == AGENT_TYPE_PM and pm_candidate_project_name == agent_project_name:
                    my_pm = pm_candidate
                    break 
            if my_pm:
                content_lines.append(f"- Your Project Manager: {my_pm.agent_id} (Persona: {my_pm.persona})")
            else:
                content_lines.append("- Your Project Manager: (Not identified for this project)")

            team_id = manager.state_manager.get_agent_team(agent_id)
            if team_id:
                team_members = manager.state_manager.get_agents_in_team(team_id)
                other_team_members = [tm for tm in team_members if tm.agent_id != agent_id]
                if other_team_members:
                    content_lines.append(f"- Your Team Members (Team: {team_id}):")
                    for member in other_team_members:
                        content_lines.append(f"  - {member.agent_id} (Persona: {member.persona}, Type: {member.agent_type})")
                else:
                    content_lines.append(f"- Your Team Members (Team: {team_id}): (No other members in your current team)")
            else:
                content_lines.append("- Your Team Members: (You are not currently assigned to a team)")

        if not content_lines:
            return "(No specific contacts identified for your role in the current context)"
        return "\n".join(content_lines)

    def get_system_prompt(self, agent: 'Agent', manager: 'AgentManager') -> str: # Added manager type hint
        """
        Gets the appropriate system prompt based on the agent's type and current state.
        Formats the prompt with relevant context, including standard instructions and address book.

        Args:
            agent: The Agent instance.
            manager: The AgentManager instance (needed for context like project/session and address book).

        Returns:
            The formatted system prompt string.
        """
        if not hasattr(agent, 'agent_type') or not agent.agent_type:
            logger.error(f"Cannot get prompt for agent '{agent.agent_id}': Missing 'agent_type'. Using default.")
            return settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing.")

        # 1. Determine the key for the agent-type-specific standard instructions
        standard_instructions_key = self._standard_instructions_map.get(agent.agent_type)
        if not standard_instructions_key:
            logger.error(f"WorkflowManager: No standard instructions key found for agent type '{agent.agent_type}'. Using fallback.")
            standard_instructions_template = "Error: Standard instructions for your agent type are missing."
        else:
            standard_instructions_template = settings.PROMPTS.get(standard_instructions_key)
            if not standard_instructions_template:
                logger.error(f"WorkflowManager: Prompt template for standard key '{standard_instructions_key}' not found. Using fallback.")
                standard_instructions_template = "Error: Standard instructions template missing."

        # 2. Build Address Book
        address_book_content = self._build_address_book(agent, manager)
        agent_project_name_for_context = self._get_agent_project_name(agent, manager)


        # 3. Prepare context for standard instructions
        standard_formatting_context = {
            "agent_id": agent.agent_id,
            "agent_type": agent.agent_type,
            "team_id": manager.state_manager.get_agent_team(agent.agent_id) or "N/A",
            "project_name": agent_project_name_for_context,
            "session_name": manager.current_session or 'N/A',
            "current_time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(sep=' ', timespec='seconds'),
            "address_book": address_book_content,
        }

        # 4. Format the standard instructions
        try:
            formatted_standard_instructions = standard_instructions_template.format(**standard_formatting_context)
        except KeyError as fmt_err:
            logger.error(f"WorkflowManager: Failed to format standard instructions template '{standard_instructions_key}'. Missing key: {fmt_err}. Using raw template.")
            formatted_standard_instructions = standard_instructions_template # Fallback to unformatted
        except Exception as e:
            logger.error(f"WorkflowManager: Unexpected error formatting standard instructions '{standard_instructions_key}': {e}. Using raw template.", exc_info=True)
            formatted_standard_instructions = standard_instructions_template

        # 5. Determine the key for the state-specific prompt
        state_prompt_key = None
        if hasattr(agent, 'state') and agent.state:
            state_prompt_key = self._prompt_map.get((agent.agent_type, agent.state))
        
        if not state_prompt_key: # Fallback for missing state or mapping
            logger.warning(f"WorkflowManager: No specific prompt key for agent type '{agent.agent_type}', state '{agent.state}'. Using default for type.")
            state_prompt_key = self._prompt_map.get((agent.agent_type, DEFAULT_STATE)) # Try default state for agent type
            if not state_prompt_key: # Absolute fallback
                logger.error(f"WorkflowManager: No default state prompt found for type '{agent.agent_type}'. Using absolute default system prompt.")
                return settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing.")

        state_prompt_template = settings.PROMPTS.get(state_prompt_key)
        if not state_prompt_template:
            logger.error(f"WorkflowManager: State-specific prompt template for key '{state_prompt_key}' not found. Using absolute default.")
            return settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing.")

        # 6. Prepare context for the state-specific prompt
        state_formatting_context = {
            "agent_id": agent.agent_id,
            "persona": agent.persona,
            "project_name": agent_project_name_for_context, # Use the determined project name
            "session_name": manager.current_session or 'N/A',
            "team_id": manager.state_manager.get_agent_team(agent.agent_id) or "N/A",
            "current_time_utc": standard_formatting_context["current_time_utc"], 
            "pm_agent_id": getattr(agent, 'delegated_pm_id', '{pm_agent_id}'), 
            "task_description": getattr(agent, '_injected_task_description', getattr(agent, 'plan_description', '{task_description}')), # Check plan_description too

            # Inject the type-specific standard instructions
            # The key here must match the placeholder in the state-specific prompt
            self._standard_instructions_map.get(agent.agent_type, "standard_framework_instructions"): formatted_standard_instructions,
        }
        # Add personality for Admin AI
        if agent.agent_type == AGENT_TYPE_ADMIN:
            personality_instructions = ""
            if hasattr(agent, '_config_system_prompt') and isinstance(agent._config_system_prompt, str):
                personality_instructions = agent._config_system_prompt.strip()
            state_formatting_context["personality_instructions"] = personality_instructions
        else: 
             state_formatting_context["personality_instructions"] = ""


        # 7. Format the state-specific prompt
        try:
            final_prompt = state_prompt_template.format(**state_formatting_context)
            logger.info(f"WorkflowManager: Generated prompt for agent '{agent.agent_id}' using state key '{state_prompt_key}'.")
            return final_prompt
        except KeyError as fmt_err:
            missing_key = str(fmt_err).strip("'")
            logger.error(f"WorkflowManager: Failed to format state prompt template '{state_prompt_key}'. Missing key: {missing_key}. Prompt before error: {state_prompt_template[:500]}... Context keys: {list(state_formatting_context.keys())}")
            try:
                 # Fallback: attempt to format with only the *type-specific* standard instructions placeholder
                 # and personality, as these are the most common top-level placeholders in state prompts.
                 fallback_context = {
                     self._standard_instructions_map.get(agent.agent_type, "standard_framework_instructions"): formatted_standard_instructions,
                     "personality_instructions": state_formatting_context.get("personality_instructions","")
                 }
                 # Also add essential placeholders that might be directly in the state prompt, with defaults
                 fallback_context.update({
                    "agent_id": agent.agent_id, "persona": agent.persona, "project_name": agent_project_name_for_context,
                    "session_name": manager.current_session or 'N/A', "team_id": manager.state_manager.get_agent_team(agent.agent_id) or "N/A",
                    "current_time_utc": standard_formatting_context["current_time_utc"],
                    "pm_agent_id": getattr(agent, 'delegated_pm_id', 'N/A'),
                    "task_description": getattr(agent, '_injected_task_description', getattr(agent, 'plan_description', 'N/A')),
                 })
                 logger.warning(f"Attempting fallback formatting for '{state_prompt_key}' with limited context.")
                 return state_prompt_template.format(**fallback_context)
            except Exception as fallback_e:
                 logger.error(f"Fallback formatting also failed for '{state_prompt_key}': {fallback_e}")
                 return settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing and state prompt formatting failed.")
        except Exception as e:
            logger.error(f"WorkflowManager: Unexpected error formatting state prompt template '{state_prompt_key}': {e}. Using absolute default.", exc_info=True)
            return settings.PROMPTS.get("default_system_prompt", "Error: Default prompt missing.")