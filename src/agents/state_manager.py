# START OF FILE src/agents/state_manager.py
import asyncio
import logging
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING

# Type hinting for AgentManager if needed, avoid circular import
if TYPE_CHECKING:
    from src.agents.manager import AgentManager
    from src.agents.core import Agent

# --- NEW: Import Agent class for type hinting in get_agents_in_team ---
from src.agents.core import Agent
# --- END NEW ---

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
        """Creates a new, empty team or confirms if it already exists."""
        if not team_id:
            logger.error("Create team failed: Team ID cannot be empty.")
            return False, "Team ID cannot be empty."
        if team_id in self.teams:
            # --- MODIFIED: Return True if team already exists ---
            logger.warning(f"Create team request: Team '{team_id}' already exists.")
            return True, f"Team '{team_id}' already exists."
            # --- END MODIFICATION ---

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
        # Also check the list itself, just in case mapping is somehow inconsistent
        agents_in_team_list = self.teams.get(team_id, [])

        if agents_in_team_map or agents_in_team_list:
             member_list = list(set(agents_in_team_map + agents_in_team_list)) # Combine and unique
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
        if agent_id not in self._manager.agents:
             logger.error(f"StateManager add_agent_to_team failed: Agent '{agent_id}' not found in manager registry.")
             return False, f"Agent '{agent_id}' not found."

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

        if old_team and old_team in self.teams and agent_id in self.teams[old_team]:
            try: self.teams[old_team].remove(agent_id); logger.info(f"StateManager: Removed '{agent_id}' from old team list '{old_team}'.")
            except ValueError: logger.warning(f"StateManager: Agent '{agent_id}' not found in old team list '{old_team}' during removal (already removed?).")
            except Exception as e: logger.error(f"StateManager: Error removing '{agent_id}' from old team '{old_team}': {e}")

        try:
            if agent_id not in self.teams[team_id]: self.teams[team_id].append(agent_id); logger.info(f"StateManager: Appended '{agent_id}' to new team list '{team_id}'.")
        except KeyError: logger.error(f"StateManager: Team '{team_id}' unexpectedly missing after creation check."); return False, f"Internal error: Team '{team_id}' disappeared."
        except Exception as e: logger.error(f"StateManager: Error appending '{agent_id}' to new team list '{team_id}': {e}"); return False, f"Failed to add agent to team list: {e}"

        self.agent_to_team[agent_id] = team_id
        logger.info(f"StateManager: Updated agent_to_team map: '{agent_id}' -> '{team_id}'.")

        message = f"Agent '{agent_id}' added to team '{team_id}' state."
        await self._manager.send_to_ui({ "type": "agent_moved_team", "agent_id": agent_id, "new_team_id": team_id, "old_team_id": old_team })
        return True, message

    async def remove_agent_from_team(self, agent_id: str, team_id: str) -> Tuple[bool, str]:
        """Removes an agent from a team's state, updating mappings."""
        logger.debug(f"StateManager: Attempting to remove agent '{agent_id}' from team '{team_id}'.")
        if not agent_id or not team_id:
            logger.error("StateManager remove_agent_from_team failed: Agent ID or Team ID empty.")
            return False, "Agent ID and Team ID cannot be empty."
        if team_id not in self.teams:
            logger.warning(f"StateManager remove_agent_from_team: Team '{team_id}' not found.")
            if self.agent_to_team.get(agent_id) != team_id: return True, f"Agent '{agent_id}' was not assigned to non-existent team '{team_id}'."
            else: self.agent_to_team.pop(agent_id, None); return False, f"Team '{team_id}' not found, but agent mapping existed (cleaned up)."

        if self.agent_to_team.get(agent_id) != team_id:
             logger.warning(f"StateManager: Agent '{agent_id}' is not recorded as being in team '{team_id}'. Current team: {self.agent_to_team.get(agent_id)}")
             return False, f"Agent '{agent_id}' is not recorded as being in team '{team_id}'."

        try:
            if agent_id in self.teams[team_id]: self.teams[team_id].remove(agent_id); logger.info(f"StateManager: Removed '{agent_id}' from team list '{team_id}'.")
        except ValueError: logger.warning(f"StateManager: Agent '{agent_id}' not found in team list '{team_id}' during removal.")
        except Exception as e: logger.error(f"StateManager: Error removing '{agent_id}' from team list '{team_id}': {e}")

        old_team_id = self.agent_to_team.pop(agent_id, None)
        logger.info(f"StateManager: Removed agent_to_team map entry for '{agent_id}'.")

        message = f"Agent '{agent_id}' removed from team '{team_id}' state."
        await self._manager.send_to_ui({ "type": "agent_moved_team", "agent_id": agent_id, "new_team_id": None, "old_team_id": old_team_id })
        return True, message

    # --- Getters and Helper Methods ---

    def get_agent_team(self, agent_id: str) -> Optional[str]:
        """Gets the team ID for a given agent ID."""
        return self.agent_to_team.get(agent_id)

    def get_team_members(self, team_id: str) -> Optional[List[str]]:
        """Gets the list of member agent IDs for a given team ID."""
        return self.teams.get(team_id)

    # --- NEW: Get Agent Instances in a Team ---
    def get_agents_in_team(self, team_id: str) -> List[Agent]:
        """Gets the actual Agent instances belonging to a specific team."""
        agent_ids = self.teams.get(team_id, [])
        agents = [self._manager.agents.get(aid) for aid in agent_ids if aid in self._manager.agents]
        # Filter out None values in case an agent_id was in the list but not in manager.agents
        return [agent for agent in agents if agent is not None]
    # --- END NEW ---


    def get_team_info_dict(self) -> Dict[str, List[str]]:
        """Returns a copy of the current team structure dictionary."""
        return self.teams.copy()

    def remove_agent_from_all_teams_state(self, agent_id: str):
        """Removes agent mapping and list entries when an agent is deleted."""
        logger.debug(f"StateManager: Removing agent '{agent_id}' from all team state.")
        old_team_id = self.agent_to_team.pop(agent_id, None)
        if old_team_id and old_team_id in self.teams:
            if agent_id in self.teams[old_team_id]:
                try: self.teams[old_team_id].remove(agent_id); logger.info(f"StateManager: Removed '{agent_id}' from team list '{old_team_id}' during agent deletion.")
                except ValueError: pass

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
