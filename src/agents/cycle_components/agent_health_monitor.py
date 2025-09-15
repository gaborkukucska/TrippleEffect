# START OF FILE src/agents/cycle_components/agent_health_monitor.py
import logging
import time
import hashlib
import re
import asyncio
import json
from typing import Dict, List, Optional, Tuple, Any
from difflib import SequenceMatcher
from collections import deque, defaultdict
from pathlib import Path

from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_REVIEW_CG,
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER,
    ADMIN_STATE_CONVERSATION, ADMIN_STATE_WORK, ADMIN_STATE_PLANNING,
    PM_STATE_MANAGE, PM_STATE_BUILD_TEAM_TASKS, PM_STATE_ACTIVATE_WORKERS,
    WORKER_STATE_WORK, CONSTITUTIONAL_GUARDIAN_AGENT_ID
)

# Import for automatic contaminated history cleanup
from src.config.settings import settings
from src.core.database_manager import Interaction
from sqlalchemy import select, delete

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.core import Agent
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

class AgentHealthRecord:
    """Tracks health metrics and patterns for a single agent."""
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.consecutive_empty_responses = 0
        self.consecutive_minimal_responses = 0
        self.consecutive_identical_responses = 0
        self.last_meaningful_action_time = time.time()
        self.response_hashes = deque(maxlen=10)  # Store recent response hashes
        self.response_contents = deque(maxlen=5)  # Store actual content for analysis
        self.state_change_history = deque(maxlen=20)  # Track state changes
        self.cycle_count_in_current_state = 0
        self.total_reactivations = 0
        self.problematic_patterns = []
        self.last_analysis_time = 0
        self.intervention_history = []
        self.contaminated_messages_detected = 0
        self.last_cleanup_time = 0
        
    def record_response(self, content: str, has_action: bool, has_thought: bool, current_state: str):
        """Record a new response and update health metrics."""
        current_time = time.time()
        
        # A "meaningful" response has substantive content. It's not just whitespace or punctuation.
        is_meaningful_content = (
            content and
            len(content.strip()) > 2 and
            not content.strip().startswith("[") and
            any(c.isalnum() for c in content)
        )

        # CRITICAL: Reset ALL counters if agent took meaningful action
        if has_action or is_meaningful_content:
            self.consecutive_empty_responses = 0
            self.consecutive_minimal_responses = 0
            self.consecutive_identical_responses = 0
            self.last_meaningful_action_time = current_time
        else:
            # Analyze response patterns only when NO action was taken
            is_empty = not content or content.isspace()
            is_minimal = self._is_minimal_response(content, has_action, has_thought)
            is_identical = self._is_identical_response(content)
            
            # Update consecutive counters
            if is_empty:
                self.consecutive_empty_responses += 1
                self.consecutive_minimal_responses = 0
                self.consecutive_identical_responses = 0
            elif is_identical:
                self.consecutive_identical_responses += 1
                self.consecutive_empty_responses = 0
                self.consecutive_minimal_responses = 0
            elif is_minimal:
                self.consecutive_minimal_responses += 1
                self.consecutive_empty_responses = 0
                self.consecutive_identical_responses = 0
            else:
                self.consecutive_empty_responses = 0
                self.consecutive_minimal_responses = 0
                self.consecutive_identical_responses = 0
                self.last_meaningful_action_time = current_time
            
        # Store response hash and content for pattern detection
        if content and content.strip():
            content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()[:8]
            self.response_hashes.append((content_hash, current_time))
            self.response_contents.append((content.strip(), current_time))
            
        # Track state changes
        if not self.state_change_history or self.state_change_history[-1][0] != current_state:
            self.state_change_history.append((current_state, current_time))
            self.cycle_count_in_current_state = 1
        else:
            self.cycle_count_in_current_state += 1
            
    def _is_minimal_response(self, content: str, has_action: bool, has_thought: bool) -> bool:
        """Determine if a response is minimal (just thinking without meaningful action)."""
        if not content or has_action:
            return False
            
        # If it's only thinking without action, consider it minimal
        if has_thought and not has_action:
            return True
            
        # Check content length and substance
        clean_content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        clean_content = clean_content.strip()
        
        return len(clean_content) < 50  # Very short responses are minimal
    
    def _is_identical_response(self, content: str) -> bool:
        """Check if this response is identical to recent responses."""
        if not content or not content.strip():
            return False
            
        # Compare with last 3 responses
        for prev_content, _ in list(self.response_contents)[-3:]:
            if content.strip() == prev_content:
                return True
        return False
        
    def detect_repetitive_patterns(self, threshold: float = 0.8) -> bool:
        """Detect if agent is producing repetitive responses."""
        if len(self.response_hashes) < 3:
            return False
            
        recent_hashes = [h[0] for h in self.response_hashes[-5:]]
        
        # Check for exact duplicates
        if len(set(recent_hashes)) < len(recent_hashes) * 0.7:
            return True
            
        return False
        
    def is_stuck_in_state(self, max_cycles: int = 10) -> bool:
        """Check if agent is stuck in current state for too long."""
        return self.cycle_count_in_current_state >= max_cycles
        
    def time_since_meaningful_action(self) -> float:
        """Return seconds since last meaningful action."""
        return time.time() - self.last_meaningful_action_time
        
    def get_health_score(self) -> float:
        """Calculate overall health score (0.0 = unhealthy, 1.0 = healthy)."""
        score = 1.0
        
        # Penalize consecutive empty responses
        if self.consecutive_empty_responses > 0:
            score -= min(0.4, self.consecutive_empty_responses * 0.15)
            
        # Penalize consecutive identical responses
        if self.consecutive_identical_responses > 0:
            score -= min(0.5, self.consecutive_identical_responses * 0.2)
            
        # Penalize consecutive minimal responses
        if self.consecutive_minimal_responses > 0:
            score -= min(0.3, self.consecutive_minimal_responses * 0.1)
            
        # Penalize being stuck in state
        if self.is_stuck_in_state():
            score -= 0.3
            
        # Penalize long time without meaningful action
        time_penalty = min(0.2, self.time_since_meaningful_action() / 300)  # 5 minutes = max penalty
        score -= time_penalty
        
        return max(0.0, score)

class ConstitutionalGuardianHealthMonitor:
    """
    Enhanced Constitutional Guardian component that monitors agent health,
    detects problematic patterns (empty/identical responses), and implements 
    recovery strategies with detailed agent history analysis.
    """
    
    def __init__(self, manager: 'AgentManager'):
        self._manager = manager
        self._health_records: Dict[str, AgentHealthRecord] = {}
        self._monitoring_active = True
        self._last_global_check = time.time()
        
        # Configuration - More aggressive thresholds as requested
        self.empty_response_threshold = 2  # Block after 2 empty responses
        self.identical_response_threshold = 2  # Block after 2 identical responses
        self.minimal_response_threshold = 3  # Block after 3 minimal responses
        self.stuck_state_threshold = 6
        self.health_check_interval = 20  # Check every 20 seconds
        self.stalled_after_success_threshold = 150  # 2.5 minutes of inactivity after successful action
        
        # Contaminated history cleanup configuration
        self.contaminated_cleanup_interval = 300  # Check every 5 minutes
        self.last_contaminated_cleanup = time.time()
        self.contaminated_patterns = [
            # Failed tool_information calls with nested tool names
            r'<tool_information><action>execute</action><tool_name>.*?</tool_name>.*?</tool_information>',
            # Error messages about invalid action
            r'Invalid or missing \'action\'\. Must be \'list_tools\' or \'get_info\'',
            # Tool execution failed messages
            r'Tool Execution Failed.*?Invalid or missing \'action\'',
            # Specific failed call patterns from the logs
            r'<tool_information><action>execute</action><tool_name>(file_system|github_tool)</tool_name><parameters>.*?</parameters></tool_information>',
        ]
        self.compiled_contaminated_patterns = [re.compile(pattern, re.DOTALL | re.IGNORECASE) for pattern in self.contaminated_patterns]
        
        logger.info("ConstitutionalGuardianHealthMonitor initialized - enforcing strict empty/identical response blocking and automatic contaminated history cleanup")
        
    def record_agent_cycle(self, agent: 'Agent', content: str, has_action: bool, 
                          has_thought: bool, took_meaningful_action: bool) -> None:
        """Record the results of an agent cycle for health monitoring."""
        if not self._monitoring_active:
            return
            
        agent_id = agent.agent_id
        if agent_id not in self._health_records:
            self._health_records[agent_id] = AgentHealthRecord(agent_id)
            
        record = self._health_records[agent_id]
        
        # Use the took_meaningful_action parameter for accurate tracking. This is the critical flag
        # that is only True if a tool was successfully run or a state change was requested.
        # The generic 'has_action' is too broad and counts any final text response as an action.
        meaningful_action_occurred = took_meaningful_action
        
        record.record_response(content, meaningful_action_occurred, has_thought, agent.state or 'unknown')
        record.total_reactivations += 1
        
        # Log critical health metrics
        if record.total_reactivations % 3 == 0:  # More frequent logging
            health_score = record.get_health_score()
            logger.info(f"ConstitutionalGuardian: '{agent_id}' health score: {health_score:.2f}, "
                       f"empty: {record.consecutive_empty_responses}, "
                       f"identical: {record.consecutive_identical_responses}")
    
    async def analyze_agent_health(self, agent: 'Agent') -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        Analyze an agent's health and determine if Constitutional Guardian intervention is needed.
        
        Returns:
            (needs_intervention, problem_description, recovery_recommendation)
        """
        agent_id = agent.agent_id
        if agent_id not in self._health_records:
            return False, None, None
            
        record = self._health_records[agent_id]
        
        # Skip intervention for agents that recently took meaningful action (30 seconds)
        time_since_action = record.time_since_meaningful_action()
        if time_since_action < 30:
            logger.debug(f"ConstitutionalGuardian: Skipping intervention for '{agent_id}' - recent action ({time_since_action:.1f}s ago)")
            return False, None, None
        
        # PRIORITY 1: Block empty responses immediately
        if record.consecutive_empty_responses >= self.empty_response_threshold:
            logger.error(f"ConstitutionalGuardian: BLOCKING '{agent_id}' - {record.consecutive_empty_responses} consecutive empty responses")
            return await self._create_empty_response_intervention(agent, record)
            
        # PRIORITY 2: Block identical responses immediately  
        if record.consecutive_identical_responses >= self.identical_response_threshold:
            logger.error(f"ConstitutionalGuardian: BLOCKING '{agent_id}' - {record.consecutive_identical_responses} consecutive identical responses")
            return await self._create_identical_response_intervention(agent, record)
            
        # PRIORITY 3: Handle minimal responses (thinking only)
        if record.consecutive_minimal_responses >= self.minimal_response_threshold:
            logger.warning(f"ConstitutionalGuardian: Intervening for '{agent_id}' - {record.consecutive_minimal_responses} minimal responses")
            return await self._create_minimal_response_intervention(agent, record)
            
        # PRIORITY 4: Handle stuck states
        if record.is_stuck_in_state(self.stuck_state_threshold):
            logger.warning(f"ConstitutionalGuardian: Intervening for '{agent_id}' - stuck in state '{agent.state}'")
            return await self._create_stuck_state_intervention(agent, record)
                
        return False, None, None
    
    async def _create_empty_response_intervention(self, agent: 'Agent', record: AgentHealthRecord) -> Tuple[bool, str, Dict]:
        """Create intervention for empty response loops."""
        
        # Analyze agent history to understand the cause
        history_analysis = await self._analyze_agent_history(agent, "empty_responses")
        
        description = (f"Constitutional Guardian BLOCKED agent '{agent.agent_id}' - "
                      f"{record.consecutive_empty_responses} consecutive empty responses detected. "
                      f"This violates the framework's requirement for agents to produce meaningful output.")
        
        recovery = {
            "type": "empty_response_violation",
            "severity": "critical",
            "history_analysis": history_analysis,
            "actions": [
                {
                    "action": "inject_guidance",
                    "message": self._generate_empty_response_guidance(agent, history_analysis)
                },
                {
                    "action": "clear_problematic_context",
                    "keep_last_n_messages": 3
                },
                {
                    "action": "reset_status", 
                    "new_status": AGENT_STATUS_IDLE
                }
            ]
        }
        
        # Add agent-specific recovery actions
        if agent.agent_type == AGENT_TYPE_ADMIN and agent.state == ADMIN_STATE_WORK:
            recovery["actions"].append({
                "action": "suggest_work_completion",
                "completion_message": self._generate_work_completion_message(agent, history_analysis)
            })
        
        return True, description, recovery
    
    async def _create_identical_response_intervention(self, agent: 'Agent', record: AgentHealthRecord) -> Tuple[bool, str, Dict]:
        """Create intervention for identical response loops."""
        
        history_analysis = await self._analyze_agent_history(agent, "identical_responses")
        
        description = (f"Constitutional Guardian BLOCKED agent '{agent.agent_id}' - "
                      f"{record.consecutive_identical_responses} consecutive identical responses detected. "
                      f"This indicates the agent is stuck in a loop and not progressing.")
        
        recovery = {
            "type": "identical_response_violation",
            "severity": "critical", 
            "history_analysis": history_analysis,
            "actions": [
                {
                    "action": "inject_guidance",
                    "message": self._generate_identical_response_guidance(agent, history_analysis)
                },
                {
                    "action": "randomize_approach",
                    "instruction": "Try a completely different approach to your current task"
                },
                {
                    "action": "reset_status",
                    "new_status": AGENT_STATUS_IDLE
                }
            ]
        }
        
        return True, description, recovery
    
    async def _create_minimal_response_intervention(self, agent: 'Agent', record: AgentHealthRecord) -> Tuple[bool, str, Dict]:
        """Create intervention for minimal response patterns."""
        
        history_analysis = await self._analyze_agent_history(agent, "minimal_responses")
        
        description = (f"Constitutional Guardian identified agent '{agent.agent_id}' producing "
                      f"{record.consecutive_minimal_responses} consecutive minimal responses. "
                      f"Agent appears to be thinking but not taking concrete actions.")
        
        recovery = {
            "type": "minimal_response_pattern",
            "severity": "high",
            "history_analysis": history_analysis,
            "actions": [
                {
                    "action": "inject_guidance",
                    "message": self._generate_action_encouragement_guidance(agent, history_analysis)
                },
                {
                    "action": "provide_available_tools",
                    "include_examples": True
                }
            ]
        }
        
        return True, description, recovery
    
    async def _create_stuck_state_intervention(self, agent: 'Agent', record: AgentHealthRecord) -> Tuple[bool, str, Dict]:
        """Create intervention for agents stuck in the same state."""
        
        history_analysis = await self._analyze_agent_history(agent, "stuck_state")
        
        description = (f"Constitutional Guardian identified agent '{agent.agent_id}' stuck in state "
                      f"'{agent.state}' for {record.cycle_count_in_current_state} cycles without progress.")
        
        recovery = {
            "type": "stuck_state_pattern",
            "severity": "high",
            "history_analysis": history_analysis,
            "actions": [
                {
                    "action": "inject_guidance",
                    "message": self._generate_state_progression_guidance(agent, history_analysis)
                },
                {
                    "action": "provide_workflow_reminder",
                    "workflow_state": agent.state
                }
            ]
        }
        
        return True, description, recovery
    
    async def _analyze_agent_history(self, agent: 'Agent', focus: str) -> Dict[str, Any]:
        """
        Perform detailed analysis of agent's conversation history to identify 
        the root cause of problematic patterns.
        """
        
        analysis = {
            "focus": focus,
            "agent_type": agent.agent_type,
            "current_state": agent.state,
            "status": agent.status,
            "history_length": len(agent.message_history) if agent.message_history else 0,
            "recent_tool_usage": [],
            "recent_state_changes": [],
            "recent_errors": [],
            "context_complexity": "unknown",
            "potential_causes": [],
            "recent_system_messages": [],
            "loop_indicators": []
        }
        
        if not agent.message_history:
            analysis["potential_causes"].append("No message history available - agent may be in initial state")
            return analysis
            
        # Analyze last 15 messages for comprehensive context
        recent_messages = agent.message_history[-15:] if len(agent.message_history) >= 15 else agent.message_history
        
        # Track different message types and patterns
        tool_calls_count = 0
        error_count = 0
        system_message_count = 0
        
        for i, msg in enumerate(recent_messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            # Track tool usage patterns
            if role == "tool":
                tool_name = msg.get("name", "unknown_tool")
                analysis["recent_tool_usage"].append({
                    "tool": tool_name,
                    "position": i,
                    "content_snippet": content[:100] if content else ""
                })
                tool_calls_count += 1
                
                # Check for tool errors
                if "error" in content.lower() or "failed" in content.lower():
                    analysis["recent_errors"].append({
                        "type": "tool_error",
                        "tool": tool_name,
                        "error_snippet": content[:200]
                    })
                    error_count += 1
                    
            elif role == "assistant" and msg.get("tool_calls"):
                for tool_call in msg.get("tool_calls", []):
                    analysis["recent_tool_usage"].append({
                        "tool": tool_call.get("name", "unknown_tool"),
                        "position": i,
                        "attempted": True
                    })
                    
            # Track system messages and interventions
            elif role == "system":
                system_message_count += 1
                analysis["recent_system_messages"].append({
                    "position": i,
                    "content_snippet": content[:100]
                })
                
                # Detect previous interventions
                if "framework" in content.lower() or "intervention" in content.lower():
                    analysis["loop_indicators"].append("Previous framework intervention detected")
                    
            # Track state changes
            elif role == "agent_state_change":
                analysis["recent_state_changes"].append({
                    "position": i,
                    "state": content
                })
        
        # Analyze context complexity
        analysis["context_complexity"] = self._assess_context_complexity(
            len(recent_messages), tool_calls_count, error_count, system_message_count
        )
        
        # Generate focus-specific analysis
        if focus == "empty_responses":
            analysis["potential_causes"].extend([
                "Agent may have completed its task but doesn't know how to conclude",
                "Context may be too complex or confusing",
                "Tool execution failures may have caused confusion",
                "Agent may be waiting for external input"
            ])
            
            # Check for completion indicators
            if any("complete" in msg.get("content", "").lower() for msg in recent_messages[-3:]):
                analysis["loop_indicators"].append("Completion language detected in recent messages")
                
        elif focus == "identical_responses":
            analysis["potential_causes"].extend([
                "Agent is repeating the same failed approach",
                "Context loop preventing progress to next step",
                "Tool or workflow configuration issue",
                "Agent stuck on invalid or impossible task"
            ])
            
            # Check for repetitive patterns
            contents = [msg.get("content", "") for msg in recent_messages if msg.get("role") == "assistant"]
            if len(set(contents[-3:])) < len(contents[-3:]) * 0.7:  # Less than 70% unique
                analysis["loop_indicators"].append("High content repetition in recent assistant messages")
                
        elif focus == "minimal_responses":
            analysis["potential_causes"].extend([
                "Agent is overthinking without taking action",
                "Unclear about available tools or next steps", 
                "May be waiting for external input or approval",
                "Tool usage restrictions preventing action"
            ])
            
        elif focus == "stuck_state":
            analysis["potential_causes"].extend([
                "Unclear state transition conditions",
                "Missing required information to progress",
                "Workflow bottleneck or deadlock",
                "State-specific prompt or configuration issues"
            ])
        
        return analysis
    
    def _assess_context_complexity(self, message_count: int, tool_count: int, 
                                  error_count: int, system_count: int) -> str:
        """Assess the complexity of the agent's current context."""
        
        complexity_score = 0
        
        if message_count > 10:
            complexity_score += 1
        if tool_count > 5:
            complexity_score += 1
        if error_count > 2:
            complexity_score += 2
        if system_count > 3:
            complexity_score += 1
            
        if complexity_score >= 4:
            return "very_high"
        elif complexity_score >= 3:
            return "high"
        elif complexity_score >= 2:
            return "medium"
        else:
            return "low"
    
    def _generate_empty_response_guidance(self, agent: 'Agent', history_analysis: Dict) -> str:
        """Generate specific guidance for agents with empty response issues."""
        
        base_msg = ("[Constitutional Guardian - CRITICAL VIOLATION]: You have been producing empty responses, "
                   "which violates the framework's core requirement for meaningful agent output. ")
        
        # Add context-specific guidance based on history analysis
        if "Completion language detected" in history_analysis.get("loop_indicators", []):
            base_msg += ("You appear to have completed work. Provide a comprehensive summary of what you accomplished, "
                        "then transition to an appropriate state.")
        elif history_analysis.get("context_complexity") in ["high", "very_high"]:
            base_msg += ("Your context appears complex. Focus on the most important next step and execute it clearly.")
        elif not history_analysis.get("recent_tool_usage"):
            base_msg += ("You haven't used any tools recently. Review your available tools and take concrete action.")
        else:
            base_msg += ("Review your recent actions and determine the next logical step to complete your task.")
            
        # Add agent-specific guidance
        if agent.agent_type == AGENT_TYPE_ADMIN:
            if agent.state == ADMIN_STATE_WORK:
                base_msg += (" As Admin AI in work state, you must either continue your work with tools or "
                           "provide a final comprehensive response and transition states.")
            else:
                base_msg += (" As Admin AI, you must engage with users and provide meaningful responses.")
                
        return base_msg
    
    def _generate_identical_response_guidance(self, agent: 'Agent', history_analysis: Dict) -> str:
        """Generate guidance for agents producing identical responses."""
        
        return ("[Constitutional Guardian - CRITICAL VIOLATION]: You have been producing identical responses, "
               "indicating you are stuck in a loop. You must break this pattern by: "
               "1) Analyzing what went wrong with your previous approach, "
               "2) Trying a completely different method or tool, "
               "3) If stuck, requesting help or transitioning to a different state. "
               "Do NOT repeat your previous response.")
    
    def _generate_action_encouragement_guidance(self, agent: 'Agent', history_analysis: Dict) -> str:
        """Generate guidance to encourage agents to take action instead of just thinking."""
        
        return ("[Constitutional Guardian - ACTION REQUIRED]: You have been thinking without taking concrete action. "
               "After using <think> tags to plan, you MUST execute actual tool calls or make decisions. "
               "Stop overthinking and start acting on your plans.")
    
    def _generate_state_progression_guidance(self, agent: 'Agent', history_analysis: Dict) -> str:
        """Generate guidance for agents stuck in the same state."""
        
        return (f"[Constitutional Guardian - STATE PROGRESSION REQUIRED]: You have been stuck in state "
               f"'{agent.state}' without making progress. Review your workflow requirements for this state "
               f"and either complete the required actions or request an appropriate state change.")
    
    def _generate_work_completion_message(self, agent: 'Agent', history_analysis: Dict) -> str:
        """Generate a completion message for Admin AI stuck in work state."""
        
        if history_analysis.get("recent_tool_usage"):
            tools_used = [tool["tool"] for tool in history_analysis["recent_tool_usage"][-3:]]
            return (f"I have completed my analysis using {', '.join(set(tools_used))}. "
                   f"Based on the results, I can provide the following summary of my findings...")
        else:
            return ("I have completed my review of the current situation. "
                   "Based on my analysis, I can provide the following assessment...")
    
    async def execute_recovery_plan(self, agent: 'Agent', recovery_plan: Dict) -> bool:
        """Execute the Constitutional Guardian's recovery plan for a troubled agent."""
        
        try:
            intervention_type = recovery_plan.get("type", "unknown")
            logger.error(f"ConstitutionalGuardian: Executing CRITICAL intervention for agent '{agent.agent_id}' - {intervention_type}")
            
            # Record intervention in agent's health record
            if agent.agent_id in self._health_records:
                record = self._health_records[agent.agent_id]
                record.intervention_history.append({
                    "timestamp": time.time(),
                    "type": intervention_type,
                    "severity": recovery_plan.get("severity", "medium"),
                    "reason": recovery_plan.get("description", "Unknown reason")
                })
                
                # Reset problematic counters
                record.consecutive_empty_responses = 0
                record.consecutive_minimal_responses = 0
                record.consecutive_identical_responses = 0
                record.problematic_patterns.clear()
                
            # Execute each recovery action
            for action in recovery_plan.get("actions", []):
                await self._execute_recovery_action(agent, action, recovery_plan.get("history_analysis", {}))
                
            # Log the intervention to database
            await self._manager.db_manager.log_interaction(
                session_id=self._manager.current_session_db_id,
                agent_id=agent.agent_id,
                role="constitutional_guardian_intervention",
                content=f"Constitutional Guardian intervention: {intervention_type} - {recovery_plan.get('description', '')}"
            )
            
            # Notify UI with detailed information
            await self._manager.send_to_ui({
                "type": "constitutional_guardian_intervention",
                "agent_id": agent.agent_id,
                "intervention_type": intervention_type,
                "severity": recovery_plan.get("severity", "medium"),
                "description": recovery_plan.get("description", ""),
                "history_analysis": recovery_plan.get("history_analysis", {}),
                "timestamp": time.time()
            })
            
            logger.info(f"ConstitutionalGuardian: Successfully executed intervention for '{agent.agent_id}'")
            return True
            
        except Exception as e:
            logger.error(f"ConstitutionalGuardian: Failed to execute recovery plan for '{agent.agent_id}': {e}", exc_info=True)
            return False
    
    async def _execute_recovery_action(self, agent: 'Agent', action: Dict, history_analysis: Dict) -> None:
        """Execute a specific Constitutional Guardian recovery action."""
        
        action_type = action.get("action")
        
        if action_type == "inject_guidance":
            guidance_msg = {
                "role": "system",
                "content": action.get("message", "[Constitutional Guardian]: Please review your current state and take appropriate action.")
            }
            agent.message_history.append(guidance_msg)
            
        elif action_type == "clear_problematic_context":
            # Keep only the last N messages to clear confusing context
            keep_count = action.get("keep_last_n_messages", 5)
            if len(agent.message_history) > keep_count:
                system_msg = agent.message_history[0] if agent.message_history and agent.message_history[0].get("role") == "system" else None
                recent_msgs = agent.message_history[-keep_count:]
                agent.message_history.clear()
                if system_msg:
                    agent.message_history.append(system_msg)
                agent.message_history.extend(recent_msgs)
                
                # Add explanation message
                agent.message_history.append({
                    "role": "system",
                    "content": f"[Constitutional Guardian]: Context cleared due to problematic patterns. Kept last {keep_count} messages."
                })
                logger.info(f"ConstitutionalGuardian: Cleared problematic context for '{agent.agent_id}', kept last {keep_count} messages")
                
        elif action_type == "reset_status":
            new_status = action.get("new_status", AGENT_STATUS_IDLE)
            agent.set_status(new_status)
            
        elif action_type == "suggest_work_completion":
            completion_message = action.get("completion_message", "Work appears to be complete.")
            suggestion_msg = {
                "role": "system",
                "content": f"[Constitutional Guardian]: {completion_message} Please provide a comprehensive summary and consider transitioning states if appropriate."
            }
            agent.message_history.append(suggestion_msg)
            
        elif action_type == "randomize_approach":
            instruction = action.get("instruction", "Try a different approach")
            randomization_msg = {
                "role": "system",
                "content": f"[Constitutional Guardian]: {instruction}. Use a completely different method than your previous attempts."
            }
            agent.message_history.append(randomization_msg)
            
        elif action_type == "provide_available_tools":
            if hasattr(self._manager, 'tool_executor') and self._manager.tool_executor:
                tools_list = list(self._manager.tool_executor.tools.keys())
                include_examples = action.get("include_examples", False)
                
                tools_msg = f"[Constitutional Guardian]: Available tools: {', '.join(tools_list)}. "
                if include_examples:
                    tools_msg += "Use XML format: <tool_name><parameter>value</parameter></tool_name>"
                    
                reminder_msg = {
                    "role": "system",
                    "content": tools_msg
                }
                agent.message_history.append(reminder_msg)
                
        elif action_type == "provide_workflow_reminder":
            workflow_state = action.get("workflow_state", agent.state)
            workflow_reminder = self._get_workflow_reminder(agent, workflow_state)
            if workflow_reminder:
                reminder_msg = {
                    "role": "system", 
                    "content": f"[Constitutional Guardian - Workflow Reminder]: {workflow_reminder}"
                }
                agent.message_history.append(reminder_msg)
                
    def _get_workflow_reminder(self, agent: 'Agent', workflow_state: str) -> Optional[str]:
        """Get a workflow reminder message for the specific agent type and state."""
        
        reminders = {
            (AGENT_TYPE_ADMIN, ADMIN_STATE_WORK): "Complete your assigned work systematically using available tools, then provide a comprehensive final response.",
            
            (AGENT_TYPE_PM, PM_STATE_BUILD_TEAM_TASKS): "Create worker agents for each unique role. Execute one action per turn: create team → get agent info → create workers → request activate_workers state.",
            
            (AGENT_TYPE_PM, PM_STATE_ACTIVATE_WORKERS): "Assign tasks to appropriate worker agents. One action per turn: list tasks → list agents → assign task → repeat until all assigned → report to admin.",
            
            (AGENT_TYPE_PM, PM_STATE_MANAGE): "Continuously manage the project: assess status → analyze and decide → take one management action → repeat.",
            
            (AGENT_TYPE_WORKER, WORKER_STATE_WORK): "Complete your assigned task step by step, save your work to files, and report progress to your Project Manager."
        }
        
        return reminders.get((agent.agent_type, workflow_state))
    
    async def run_periodic_health_check(self) -> None:
        """Run a Constitutional Guardian periodic health check on all active agents."""
        
        if not self._monitoring_active:
            return
            
        current_time = time.time()
        if current_time - self._last_global_check < self.health_check_interval:
            return
            
        self._last_global_check = current_time
        
        try:
            agents_checked = 0
            interventions_performed = 0
            
            # Check all active agents
            for agent_id, agent in self._manager.agents.items():
                # Skip the Constitutional Guardian itself and error/review states
                if agent_id == CONSTITUTIONAL_GUARDIAN_AGENT_ID:
                    continue
                    
                if agent.status not in [AGENT_STATUS_ERROR, AGENT_STATUS_AWAITING_USER_REVIEW_CG]:
                    needs_intervention, problem_desc, recovery_plan = await self.analyze_agent_health(agent)
                    
                    if needs_intervention and recovery_plan:
                        success = await self.execute_recovery_plan(agent, recovery_plan)
                        if success:
                            interventions_performed += 1
                            
                    agents_checked += 1
            
            # Run contaminated history cleanup periodically
            await self._run_automatic_contaminated_cleanup()
                    
            if interventions_performed > 0:
                logger.error(f"ConstitutionalGuardian: Periodic check complete - {agents_checked} agents checked, {interventions_performed} CRITICAL interventions performed")
            elif agents_checked > 0:
                logger.debug(f"ConstitutionalGuardian: Periodic check complete - {agents_checked} agents healthy")
                
        except Exception as e:
            logger.error(f"ConstitutionalGuardian: Error during periodic health check: {e}", exc_info=True)
            
    def get_agent_health_report(self, agent_id: str) -> Optional[Dict]:
        """Get a comprehensive health report for a specific agent."""
        
        if agent_id not in self._health_records:
            return None
            
        record = self._health_records[agent_id]
        
        return {
            "agent_id": agent_id,
            "health_score": record.get_health_score(),
            "consecutive_empty_responses": record.consecutive_empty_responses,
            "consecutive_identical_responses": record.consecutive_identical_responses,
            "consecutive_minimal_responses": record.consecutive_minimal_responses,
            "time_since_meaningful_action": record.time_since_meaningful_action(),
            "cycles_in_current_state": record.cycle_count_in_current_state,
            "total_reactivations": record.total_reactivations,
            "is_stuck_in_state": record.is_stuck_in_state(),
            "has_repetitive_patterns": record.detect_repetitive_patterns(),
            "intervention_count": len(record.intervention_history),
            "last_intervention": record.intervention_history[-1] if record.intervention_history else None
        }
        
    def reset_agent_health_record(self, agent_id: str) -> None:
        """Reset the health record for an agent (e.g., after successful intervention)."""
        
        if agent_id in self._health_records:
            del self._health_records[agent_id]
            logger.info(f"ConstitutionalGuardian: Reset health record for agent '{agent_id}'")

    async def _run_automatic_contaminated_cleanup(self) -> None:
        """Run automatic contaminated history cleanup as part of health monitoring."""
        
        current_time = time.time()
        if current_time - self.last_contaminated_cleanup < self.contaminated_cleanup_interval:
            return
            
        self.last_contaminated_cleanup = current_time
        
        try:
            cleanup_stats = {
                'agents_cleaned': 0,
                'messages_removed': 0,
                'database_interactions_removed': 0
            }
            
            # Clean message histories from active agents
            for agent_id, agent in self._manager.agents.items():
                if agent_id == CONSTITUTIONAL_GUARDIAN_AGENT_ID:
                    continue
                    
                removed_count = await self._clean_agent_message_history(agent)
                if removed_count > 0:
                    cleanup_stats['agents_cleaned'] += 1
                    cleanup_stats['messages_removed'] += removed_count
                    
                    # Update health record
                    if agent_id in self._health_records:
                        self._health_records[agent_id].contaminated_messages_detected += removed_count
                        self._health_records[agent_id].last_cleanup_time = current_time
            
            # Clean database interactions
            database_removed = await self._clean_contaminated_database_interactions()
            cleanup_stats['database_interactions_removed'] = database_removed
            
            # Log cleanup results
            if cleanup_stats['messages_removed'] > 0 or cleanup_stats['database_interactions_removed'] > 0:
                logger.info(f"ConstitutionalGuardian: Automatic cleanup completed - "
                           f"{cleanup_stats['agents_cleaned']} agents cleaned, "
                           f"{cleanup_stats['messages_removed']} messages removed, "
                           f"{cleanup_stats['database_interactions_removed']} database interactions removed")
                
                # Notify UI about cleanup
                await self._manager.send_to_ui({
                    "type": "automatic_contaminated_cleanup",
                    "stats": cleanup_stats,
                    "timestamp": current_time
                })
            else:
                logger.debug("ConstitutionalGuardian: Automatic cleanup check - no contaminated history found")
                
        except Exception as e:
            logger.error(f"ConstitutionalGuardian: Error during automatic contaminated cleanup: {e}", exc_info=True)

    async def _clean_agent_message_history(self, agent: 'Agent') -> int:
        """Clean contaminated messages from a single agent's history."""
        
        if not agent.message_history:
            return 0
            
        original_length = len(agent.message_history)
        cleaned_history = []
        removed_count = 0
        
        for message in agent.message_history:
            if self._is_contaminated_message(message):
                removed_count += 1
                logger.debug(f"ConstitutionalGuardian: Removing contaminated message from {agent.agent_id}: {str(message)[:100]}...")
            else:
                cleaned_history.append(message)
        
        if removed_count > 0:
            agent.message_history = cleaned_history
            logger.info(f"ConstitutionalGuardian: Cleaned {removed_count} contaminated messages from agent {agent.agent_id}")
            
        return removed_count

    def _is_contaminated_message(self, message: Dict[str, Any]) -> bool:
        """Check if a message contains contaminated content."""
        
        # Never treat tool results as contaminated, as their content can be unpredictable
        if message.get("role") == "tool":
            return False

        if not isinstance(message, dict) or 'content' not in message:
            return False
            
        content = str(message.get('content', ''))
        
        # Check for contaminated patterns
        for pattern in self.compiled_contaminated_patterns:
            if pattern.search(content):
                return True
                
        # Check for specific tool execution failure sequences
        if ('tool_information' in content and 
            'action' in content and 
            'execute' in content and 
            'Invalid or missing' in content):
            return True
            
        return False

    async def _clean_contaminated_database_interactions(self) -> int:
        """Clean contaminated interactions from the database."""
        
        try:
            if not hasattr(self._manager, 'db_manager') or not self._manager.db_manager:
                return 0
                
            async with self._manager.db_manager.get_session() as session:
                # Get current session interactions if available
                current_session_id = getattr(self._manager, 'current_session_db_id', None)
                
                # Build query to find contaminated interactions
                stmt = select(Interaction)
                if current_session_id:
                    stmt = stmt.where(Interaction.session_id == current_session_id)
                
                result = await session.execute(stmt)
                interactions = result.scalars().all()
                
                # Find contaminated interactions
                contaminated_ids = []
                
                for interaction in interactions:
                    # Check content for contaminated patterns
                    content = interaction.content or ""
                    tool_calls = interaction.tool_calls_json or []
                    tool_results = interaction.tool_results_json or []
                    
                    # Convert to strings for pattern matching
                    full_content = f"{content} {json.dumps(tool_calls)} {json.dumps(tool_results)}"
                    
                    # Check for contaminated patterns
                    is_contaminated = False
                    for pattern in self.compiled_contaminated_patterns:
                        if pattern.search(full_content):
                            is_contaminated = True
                            break
                    
                    # Additional specific checks
                    if not is_contaminated:
                        if ('Invalid or missing' in full_content and 
                            'action' in full_content and 
                            'list_tools' in full_content):
                            is_contaminated = True
                    
                    if is_contaminated:
                        contaminated_ids.append(interaction.id)
                
                # Delete contaminated interactions
                if contaminated_ids:
                    delete_stmt = delete(Interaction).where(Interaction.id.in_(contaminated_ids))
                    result = await session.execute(delete_stmt)
                    removed_count = result.rowcount
                    logger.info(f"ConstitutionalGuardian: Automatically removed {removed_count} contaminated database interactions")
                    return removed_count
                
                return 0
                
        except Exception as e:
            logger.error(f"ConstitutionalGuardian: Error cleaning contaminated database interactions: {e}", exc_info=True)
            return 0

    async def force_contaminated_cleanup(self) -> Dict[str, int]:
        """Force immediate contaminated history cleanup (can be called externally)."""
        
        logger.info("ConstitutionalGuardian: Starting forced contaminated history cleanup...")
        
        cleanup_stats = {
            'agents_cleaned': 0,
            'messages_removed': 0,
            'database_interactions_removed': 0
        }
        
        try:
            # Clean message histories from all agents
            for agent_id, agent in self._manager.agents.items():
                if agent_id == CONSTITUTIONAL_GUARDIAN_AGENT_ID:
                    continue
                    
                removed_count = await self._clean_agent_message_history(agent)
                if removed_count > 0:
                    cleanup_stats['agents_cleaned'] += 1
                    cleanup_stats['messages_removed'] += removed_count
                    
                    # Update health record
                    if agent_id in self._health_records:
                        self._health_records[agent_id].contaminated_messages_detected += removed_count
                        self._health_records[agent_id].last_cleanup_time = time.time()
            
            # Clean database interactions
            database_removed = await self._clean_contaminated_database_interactions()
            cleanup_stats['database_interactions_removed'] = database_removed
            
            logger.info(f"ConstitutionalGuardian: Forced cleanup completed - "
                       f"{cleanup_stats['agents_cleaned']} agents cleaned, "
                       f"{cleanup_stats['messages_removed']} messages removed, "
                       f"{cleanup_stats['database_interactions_removed']} database interactions removed")
            
            # Reset cleanup timer
            self.last_contaminated_cleanup = time.time()
            
            return cleanup_stats
            
        except Exception as e:
            logger.error(f"ConstitutionalGuardian: Error during forced contaminated cleanup: {e}", exc_info=True)
            return cleanup_stats

# Keep the original AgentHealthMonitor class for backward compatibility
# but delegate to the new ConstitutionalGuardianHealthMonitor
class AgentHealthMonitor(ConstitutionalGuardianHealthMonitor):
    """Legacy AgentHealthMonitor - now delegates to ConstitutionalGuardianHealthMonitor."""
    
    def __init__(self, manager: 'AgentManager'):
        super().__init__(manager)
        logger.info("AgentHealthMonitor initialized - delegating to ConstitutionalGuardianHealthMonitor")
