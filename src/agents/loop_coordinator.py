import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class LoopCoordinator:
    """
    Advisory coordinator that existing watchdogs and schedulers consult before acting.
    Tracks intervention history, cooldown periods, and cross-system conflicts to prevent
    overlapping recovery mechanisms and thrashing.
    """
    def __init__(self):
        # Maps "agent_id:intervention_type" to a list of timestamps
        self._intervention_history: Dict[str, List[float]] = {}
        # Maps "agent_id:intervention_type" to a cooldown expiration timestamp
        self._cooldowns: Dict[str, float] = {}
        
        # Default cooldown periods in seconds
        self._default_cooldowns = {
            "emergency_override": 60.0,
            "pm_wake_worker": 30.0,
            "stuck_loop_nudge": 45.0,
            "pm_manage_check": 15.0
        }

    def record_intervention(self, agent_id: str, intervention_type: str, custom_cooldown: Optional[float] = None):
        """Records an intervention and sets a cooldown."""
        key = f"{agent_id}:{intervention_type}"
        now = time.time()
        
        if key not in self._intervention_history:
            self._intervention_history[key] = []
        self._intervention_history[key].append(now)
        
        # Maintain only recent history (e.g., last 1 hour)
        self._intervention_history[key] = [t for t in self._intervention_history[key] if now - t < 3600]
        
        cooldown = custom_cooldown if custom_cooldown is not None else self._default_cooldowns.get(intervention_type, 30.0)
        self._cooldowns[key] = now + cooldown
        
        logger.debug(f"LoopCoordinator: Recorded '{intervention_type}' for agent '{agent_id}'. Cooldown set for {cooldown}s.")

    def should_intervene(self, agent_id: str, intervention_type: str) -> bool:
        """
        Consults the coordinator to determine if an intervention is allowed based on cooldowns.
        """
        key = f"{agent_id}:{intervention_type}"
        now = time.time()
        
        if key in self._cooldowns and now < self._cooldowns[key]:
            remaining = self._cooldowns[key] - now
            logger.debug(f"LoopCoordinator: Blocking '{intervention_type}' for agent '{agent_id}'. Cooldown active for {remaining:.1f}s.")
            return False
            
        return True

    def get_intervention_count(self, agent_id: str, intervention_type: str, time_window: float = 300) -> int:
        """Gets the number of times an intervention occurred within the time window."""
        key = f"{agent_id}:{intervention_type}"
        if key not in self._intervention_history:
            return 0
            
        now = time.time()
        count = sum(1 for t in self._intervention_history[key] if now - t <= time_window)
        return count
