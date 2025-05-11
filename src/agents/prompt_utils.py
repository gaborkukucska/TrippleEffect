# START OF FILE src/agents/prompt_utils.py
import re
import logging
from typing import TYPE_CHECKING, Optional

# Type hinting for AgentManager if needed, avoid circular import
if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.core import Agent

logger = logging.getLogger(__name__)

async def update_agent_prompt_team_id(manager: 'AgentManager', agent_id: str, new_team_id: Optional[str]):
    """
    Updates the team ID placeholder within an existing agent's system prompt string.
    This function assumes the agent's prompt has already been loaded (now originating
    from prompts.json via settings).

    Args:
        manager: The AgentManager instance (to access agents).
        agent_id: The ID of the agent whose prompt needs updating.
        new_team_id: The new team ID string, or None to indicate removal from team.
    """
    agent = manager.agents.get(agent_id)
    # Ensure we don't modify bootstrap agents (like admin_ai) which shouldn't have team IDs in their *operational* prompt
    if agent and not (agent_id in manager.bootstrap_agents):
        try:
            # Regex to find the line setting the team ID
            # It looks for "Your Assigned Team ID:" followed by any characters until the end of the line
            team_line_regex = r"(Your Assigned Team ID:).*"
            # Replacement string: uses the captured group 1 (\1) which is "Your Assigned Team ID:"
            # and appends the new team ID or 'N/A'
            new_team_line = rf"\1 {new_team_id or 'N/A'}"

            # Check if the agent has the final_system_prompt attribute (should exist)
            if hasattr(agent, 'final_system_prompt') and isinstance(agent.final_system_prompt, str):
                # Update the prompt string stored in the agent object
                agent.final_system_prompt = re.sub(team_line_regex, new_team_line, agent.final_system_prompt)

                # Also update the prompt in the stored agent_config for consistency if saving/loading state
                if hasattr(agent, 'agent_config') and isinstance(agent.agent_config, dict) and "config" in agent.agent_config:
                    if isinstance(agent.agent_config["config"], dict):
                        agent.agent_config["config"]["system_prompt"] = agent.final_system_prompt
                    else:
                        logger.warning(f"Cannot update team ID in agent_config for '{agent_id}': 'config' value is not a dictionary.")

                # Update the history if the system prompt is the first message
                if agent.message_history and agent.message_history[0]["role"] == "system":
                    agent.message_history[0]["content"] = agent.final_system_prompt

                logger.info(f"Updated team ID ({new_team_id or 'N/A'}) in live prompt state for dynamic agent '{agent_id}'.")
            else:
                logger.warning(f"Cannot update team ID for agent '{agent_id}': 'final_system_prompt' attribute missing or not a string.")

        except Exception as e:
            logger.error(f"Error updating system prompt state for agent '{agent_id}' after team change: {e}", exc_info=True)
    elif not agent:
        logger.warning(f"Attempted to update team ID for non-existent agent '{agent_id}'.")
