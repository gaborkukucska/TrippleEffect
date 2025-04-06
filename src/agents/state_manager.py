# START OF FILE src/agents/state_manager.py
import asyncio
import logging
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING

# Type hinting for AgentManager if needed, avoid circular import
if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.core import Agent

logger = logging.getLogger(__name__)

class AgentStateManager:
    """
    Manages the state related to teams and agent-team assignments.
    Used by AgentManager.
    """
    def __init__(self, manager: 'AgentManager'):
        """
        Initializes the StateManager.

        Args:
            manager: A reference to the main AgentManager instance to access
                     shared resources like agents dictionary and UI sending function.
        """
        self._manager = manager # Keep a reference to the main manager
        self.teams: Dict[str, List[str]] = {} # team_id -> [agent_id]
        self.agent_to_team: Dict[str, str] = {} # agent_id -> team_id
        logger.info("AgentStateManager initialized.")

    # --- Team State Methods ---

    async def create_new_team(self, team_id: str) -> Tuple[bool, str]:
        """Creates a new, empty team."""
        if not team_id:
            logger.error("Create team failed: Team ID cannot be empty.")
            return False, "Team ID cannot be empty."
        if team_id in self.teams:
            logger.warning(f"Create team failed: Team '{team_id}' already exists.")
            return False, f"Team '{team_id}' already exists."

        self.teams[team_id] = []
        message = f"Team '{team_id}' created successfully."
        logger.info(message)
        # Notify UI via main manager's function
        await self._manager.send_to_ui({"type": "team_created", "team_id": team_id, "members": []})
        return True, message

    async def delete_existing_team(self, team_id: str) -> Tuple[bool, str]:
        """Deletes an existing empty team."""
        if not team_id:
            logger.error("Delete team failed: Team ID cannot be empty.")
            return False, "Team ID cannot be empty."
        if team_id not in self.teams:
            logger.warning(f"Delete team failed: Team '{team_id}' not found.")
            return False, f"Team '{team_id}' not found."

        # Double-check if any agent is still mapped to this team
        agents_in_team_map = [aid for aid, tid in self.agent_to_team.items() if tid == team_id]
        if agents_in_team_map or self.teams.get(team_id):
             member_list = agents_in_team_map or self.teams.get(team_id, [])
             logger.warning(f"Delete team '{team_id}' failed. Team still contains agents: {member_list}.")
             return False, f"Team '{team_id}' is not empty. Remove agents first. Members: {member_list}"

        # Proceed with deletion
        del self.teams[team_id]
        message = f"Team '{team_id}' deleted successfully."
        logger.info(message)
        await self._manager.send_to_ui({"type": "team_deleted", "team_id": team_id})
        return True, message

    async def add_agent_to_team(self, agent_id: str, team_id: str) -> Tuple[bool, str]:
        """Adds an agent to a team's state, updating mappings."""
        logger.debug(f"StateManager: Attempting to add agent '{agent_id}' to team '{team_id}'.")
        if not agent_id or not team_id:
             logger.error("StateManager add_agent_to_team failed: Agent ID or Team ID empty.")
             return False, "Agent ID and Team ID cannot be empty."
        # Check if agent exists in the main manager's registry
        if agent_id not in self._manager.agents:
             logger.error(f"StateManager add_agent_to_team failed: Agent '{agent_id}' not found in manager registry.")
             return False, f"Agent '{agent_id}' not found."

        # Ensure team exists (create if needed)
        if team_id not in self.teams:
             logger.info(f"StateManager: Team '{team_id}' not found, creating.")
             success, msg = await self.create_new_team(team_id)
             if not success:
                 logger.error(f"StateManager add_agent_to_team failed: Could not auto-create team '{team_id}': {msg}")
                 return False, f"Failed to auto-create team '{team_id}': {msg}"

        old_team = self.agent_to_team.get(agent_id)
        if old_team == team_id:
             logger.info(f"StateManager: Agent '{agent_id}' already in team '{team_id}'.")
             return True, f"Agent '{agent_id}' is already in team '{team_id}'."

        # --- State Update ---
        # Remove from old team list
        if old_team and old_team in self.teams and agent_id in self.teams[old_team]:
            try:
                self.teams[old_team].remove(agent_id)
                logger.info(f"StateManager: Removed '{agent_id}' from old team list '{old_team}'.")
            except ValueError:
                 logger.warning(f"StateManager: Agent '{agent_id}' not found in old team list '{old_team}' during removal (already removed?).")
            except Exception as e:
                 logger.error(f"StateManager: Error removing '{agent_id}' from old team '{old_team}': {e}")

        # Add to new team list
        try:
            if agent_id not in self.teams[team_id]:
                self.teams[team_id].append(agent_id)
                logger.info(f"StateManager: Appended '{agent_id}' to new team list '{team_id}'.")
        except KeyError:
             logger.error(f"StateManager: Team '{team_id}' unexpectedly missing after creation check.")
             return False, f"Internal error: Team '{team_id}' disappeared."
        except Exception as e:
             logger.error(f"StateManager: Error appending '{agent_id}' to new team list '{team_id}': {e}")
             return False, f"Failed to add agent to team list: {e}"

        # Update agent_to_team mapping
        self.agent_to_team[agent_id] = team_id
        logger.info(f"StateManager: Updated agent_to_team map: '{agent_id}' -> '{team_id}'.")
        # --- End State Update ---

        message = f"Agent '{agent_id}' added to team '{team_id}' state."
        await self._manager.send_to_ui({ "type": "agent_moved_team", "agent_id": agent_id, "new_team_id": team_id, "old_team_id": old_team })
        # Status update is pushed by the caller (e.g., AgentManager._create_agent_internal or AgentManager._handle_manage_team_action)
        # await self._manager.push_agent_status_update(agent_id)
        return True, message

    async def remove_agent_from_team(self, agent_id: str, team_id: str) -> Tuple[bool, str]:
        """Removes an agent from a team's state, updating mappings."""
        logger.debug(f"StateManager: Attempting to remove agent '{agent_id}' from team '{team_id}'.")
        if not agent_id or not team_id:
            logger.error("StateManager remove_agent_from_team failed: Agent ID or Team ID empty.")
            return False, "Agent ID and Team ID cannot be empty."
        if team_id not in self.teams:
            logger.warning(f"StateManager remove_agent_from_team: Team '{team_id}' not found.")
            # If agent isn't mapped to it either, consider it a success (already removed)
            if self.agent_to_team.get(agent_id) != team_id:
                 return True, f"Agent '{agent_id}' was not assigned to non-existent team '{team_id}'."
            else: # Agent thinks it's in a team that doesn't exist in the list? Clean up map.
                 self.agent_to_team.pop(agent_id, None)
                 return False, f"Team '{team_id}' not found, but agent mapping existed (cleaned up)."

        if self.agent_to_team.get(agent_id) != team_id:
             logger.warning(f"StateManager: Agent '{agent_id}' is not recorded as being in team '{team_id}'. Current team: {self.agent_to_team.get(agent_id)}")
             # Allow removal from list even if mapping is wrong? Or return error? Let's return error for consistency.
             return False, f"Agent '{agent_id}' is not recorded as being in team '{team_id}'."

        # --- State Update ---
        # Remove from team list
        try:
            if agent_id in self.teams[team_id]:
                self.teams[team_id].remove(agent_id)
                logger.info(f"StateManager: Removed '{agent_id}' from team list '{team_id}'.")
        except ValueError:
            logger.warning(f"StateManager: Agent '{agent_id}' not found in team list '{team_id}' during removal.")
        except Exception as e:
             logger.error(f"StateManager: Error removing '{agent_id}' from team list '{team_id}': {e}")
             # Proceed to remove mapping anyway

        # Remove from agent_to_team mapping
        old_team_id = self.agent_to_team.pop(agent_id, None) # Use pop with default
        logger.info(f"StateManager: Removed agent_to_team map entry for '{agent_id}'.")
        # --- End State Update ---

        message = f"Agent '{agent_id}' removed from team '{team_id}' state."
        await self._manager.send_to_ui({ "type": "agent_moved_team", "agent_id": agent_id, "new_team_id": None, "old_team_id": old_team_id })
        # Status update pushed by caller
        return True, message

    def get_agent_team(self, agent_id: str) -> Optional[str]:
        """Gets the team ID for a given agent ID."""
        return self.agent_to_team.get(agent_id)

    def get_team_members(self, team_id: str) -> Optional[List[str]]:
        """Gets the list of member agent IDs for a given team ID."""
        return self.teams.get(team_id) # Returns None if team doesn't exist

    def get_team_info_dict(self) -> Dict[str, List[str]]:
        """Returns a copy of the current team structure dictionary."""
        return self.teams.copy()

    def remove_agent_from_all_teams_state(self, agent_id: str):
        """Removes agent mapping and list entries when an agent is deleted."""
        logger.debug(f"StateManager: Removing agent '{agent_id}' from all team state.")
        old_team_id = self.agent_to_team.pop(agent_id, None)
        if old_team_id and old_team_id in self.teams:
            if agent_id in self.teams[old_team_id]:
                try:
                    self.teams[old_team_id].remove(agent_id)
                    logger.info(f"StateManager: Removed '{agent_id}' from team list '{old_team_id}' during agent deletion.")
                except ValueError:
                    pass # Already removed, that's fine

    def load_state(self, teams: Dict[str, List[str]], agent_to_team: Dict[str, str]):
        """ Overwrites current state with loaded data (used during session load). """
        self.teams = teams.copy() if isinstance(teams, dict) else {}
        self.agent_to_team = agent_to_team.copy() if isinstance(agent_to_team, dict) else {}
        logger.info(f"AgentStateManager: Loaded state with {len(self.teams)} teams and {len(self.agent_to_team)} agent mappings.")

    def clear_state(self):
        """ Clears all team and assignment state. """
        self.teams = {}
        self.agent_to_team = {}
        logger.info("AgentStateManager: Cleared all team state.")
