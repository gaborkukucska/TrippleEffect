# START OF FILE src/agents/manager.py
import logging
logging.info("manager.py: Module loading started...")

import asyncio
from typing import Dict, Any, Optional, List, Tuple, Set
import json
import os
import traceback
import time
import uuid
import fnmatch
import copy
import re

logging.info("manager.py: Importing database_manager...")
from src.core.database_manager import db_manager, close_db_connection, Project as DBProject, Session as DBSession
logging.info("manager.py: Imported database_manager.")

logging.info("manager.py: Importing constants...")
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_ERROR, BOOTSTRAP_AGENT_ID, ADMIN_STATE_CONVERSATION,
    AGENT_TYPE_PM, AGENT_TYPE_WORKER, PM_STATE_WORK, PM_STATE_MANAGE, PM_STATE_STARTUP, WORKER_STATE_WORK, # Added WORKER_STATE_WORK
    AGENT_STATUS_AWAITING_USER_REVIEW_CG # Added for CG concern resolution
)
logging.info("manager.py: Imported constants.")

logging.info("manager.py: Importing Agent core...")
from src.agents.core import Agent
logging.info("manager.py: Imported Agent core.")

logging.info("manager.py: Importing CycleContext...")
from src.agents.cycle_components.cycle_context import CycleContext
logging.info("manager.py: Imported CycleContext.")

logging.info("manager.py: Importing settings...")
from src.config.settings import settings, model_registry, BASE_DIR
logging.info("manager.py: Imported settings.")

logging.info("manager.py: Importing websocket_manager...")
from src.api.websocket_manager import broadcast
logging.info("manager.py: Imported websocket_manager.")

logging.info("manager.py: Importing ToolExecutor...")
from src.tools.executor import ToolExecutor
logging.info("manager.py: Imported ToolExecutor.")

logging.info("manager.py: Importing state_manager...")
from src.agents.state_manager import AgentStateManager
logging.info("manager.py: Imported state_manager.")
logging.info("manager.py: Importing session_manager...")
from src.agents.session_manager import SessionManager
logging.info("manager.py: Imported session_manager.")
logging.info("manager.py: Importing interaction_handler...")
from src.agents.interaction_handler import AgentInteractionHandler
logging.info("manager.py: Imported interaction_handler.")
logging.info("manager.py: Importing cycle_handler...")
from src.agents.cycle_handler import AgentCycleHandler
logging.info("manager.py: Imported cycle_handler.")
logging.info("manager.py: Importing performance_tracker...")
from src.agents.performance_tracker import ModelPerformanceTracker
logging.info("manager.py: Imported performance_tracker.")
logging.info("manager.py: Importing provider_key_manager...")
from src.agents.provider_key_manager import ProviderKeyManager
logging.info("manager.py: Imported provider_key_manager.")

logging.info("manager.py: Importing agent_lifecycle...")
from src.agents import agent_lifecycle # Imports the module
logging.info("manager.py: Imported agent_lifecycle.")
logging.info("manager.py: Importing failover_handler...")
from src.agents.failover_handler import handle_agent_model_failover
logging.info("manager.py: Imported failover_handler.")
logging.info("manager.py: Importing workflow_manager...")
from src.agents.workflow_manager import AgentWorkflowManager # Ensures this is imported
logging.info("manager.py: Imported workflow_manager.")

from pathlib import Path

logging.info("manager.py: Importing BaseLLMProvider...")
from src.llm_providers.base import BaseLLMProvider
logging.info("manager.py: Imported BaseLLMProvider.")

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_NAME = "DefaultProject"

class AgentManager:
    def __init__(self, websocket_manager: Optional[Any] = None): # websocket_manager is no longer used here
        self.bootstrap_agents: List[str] = []
        self.agents: Dict[str, Agent] = {}
        self.send_to_ui_func = broadcast
        self.current_project: Optional[str] = None
        self.current_session: Optional[str] = None
        self.db_manager = db_manager
        self.current_project_db_id: Optional[int] = None
        self.current_session_db_id: Optional[int] = None
        self.local_api_usage_round_robin_index: Dict[str, int] = {}
        self.available_local_providers_list: Dict[str, List[str]] = {}
        
        logger.info("AgentManager __init__: Instantiating ProviderKeyManager...")
        self.key_manager = ProviderKeyManager(settings.PROVIDER_API_KEYS, settings)
        logger.info("AgentManager __init__: Instantiating ToolExecutor...");
        self.tool_executor = ToolExecutor()
        logger.info(f"AgentManager __init__: ToolExecutor instantiated with tools: {list(self.tool_executor.tools.keys())}")
        
        logger.info("AgentManager __init__: Instantiating AgentStateManager...");
        self.state_manager = AgentStateManager(self)
        logger.info("AgentManager __init__: Instantiating SessionManager...");
        self.session_manager = SessionManager(self, self.state_manager)
        
        logger.info("AgentManager __init__: Instantiating AgentWorkflowManager...");
        self.workflow_manager = AgentWorkflowManager() 
        
        logger.info("AgentManager __init__: Instantiating AgentInteractionHandler...");
        self.interaction_handler = AgentInteractionHandler(self)
        logger.info("AgentManager __init__: Instantiating AgentCycleHandler...");
        self.cycle_handler = AgentCycleHandler(self, self.interaction_handler) 
        
        logger.info("AgentManager __init__: Instantiating ModelPerformanceTracker...");
        self.performance_tracker = ModelPerformanceTracker()
        
        self.model_registry = model_registry

        self._ensure_projects_dir()
        self._pm_manage_task: Optional[asyncio.Task] = None
        self._cg_heartbeat_task: Optional[asyncio.Task] = None
        logger.info("AgentManager __init__: Initialized synchronously.")
        asyncio.create_task(self._ensure_default_db_session())
        asyncio.create_task(self.start_pm_manage_timer())
        asyncio.create_task(self.start_cg_heartbeat_timer())

    async def _ensure_default_db_session(self):
        if self.current_session_db_id is None:
             await self.set_project_session_context(DEFAULT_PROJECT_NAME, f"startup_{int(time.time())}")

    async def _initialize_local_provider_lists(self):
        logger.info("AgentManager: Initializing local provider lists...")
        if not hasattr(self.model_registry, 'available_models') or not self.model_registry.available_models:
            logger.warning("AgentManager: model_registry.available_models is not available or empty. Skipping local provider initialization.")
            return

        local_providers_by_base: Dict[str, List[str]] = {}

        for specific_provider_name in self.model_registry.available_models.keys():
            base_type = None
            is_local = False

            if specific_provider_name.startswith("ollama-local") or specific_provider_name == "ollama-proxy":
                base_type = "ollama"
                is_local = True
            elif specific_provider_name.startswith("litellm-local") or specific_provider_name == "litellm-proxy":
                base_type = "litellm"
                is_local = True

            if is_local and base_type:
                if base_type not in local_providers_by_base:
                    local_providers_by_base[base_type] = []
                local_providers_by_base[base_type].append(specific_provider_name)

        for base_type, providers in local_providers_by_base.items():
            sorted_providers = sorted(providers)
            self.available_local_providers_list[base_type] = sorted_providers
            self.local_api_usage_round_robin_index[base_type] = 0
            logger.info(f"AgentManager: Discovered local providers for base type '{base_type}': {sorted_providers}")

        logger.info("AgentManager: Local provider list initialization complete.")

    async def set_project_session_context(self, project_name: str, session_name: str, loading: bool = False):
        logger.info(f"Setting context. Project: {project_name}, Session: {session_name}, Loading: {loading}")
        if self.current_session_db_id is not None and not (loading and self.current_project == project_name and self.current_session == session_name):
            await self.db_manager.end_session(self.current_session_db_id) # type: ignore
            self.current_session_db_id = None; self.current_project_db_id = None
        self.current_project = project_name; self.current_session = session_name
        project_record = await self.db_manager.get_project_by_name(project_name)
        if not project_record: project_record = await self.db_manager.add_project(name=project_name)
        if not project_record or project_record.id is None:
            logger.error(f"Failed to get or create project DB record for '{project_name}'!"); return
        self.current_project_db_id = project_record.id
        if loading:
            found_session_id = await self.db_manager.get_session_id_by_name(self.current_project_db_id, session_name)
            if found_session_id: self.current_session_db_id = found_session_id
            else:
                 new_session_record = await self.db_manager.start_session(self.current_project_db_id, session_name)
                 if new_session_record and new_session_record.id: self.current_session_db_id = new_session_record.id
                 else: logger.error(f"Failed to create new DB session record for loaded session '{project_name}/{session_name}'!")
        else:
            session_record = await self.db_manager.start_session(self.current_project_db_id, session_name)
            if session_record and session_record.id: self.current_session_db_id = session_record.id
            else: logger.error(f"Failed to start new DB session record for '{session_name}'!")
        logger.info(f"DB Context: ProjectID={self.current_project_db_id}, SessionID={self.current_session_db_id}")

    def _ensure_projects_dir(self):
        try: settings.PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e: logger.error(f"Error creating projects dir {settings.PROJECTS_BASE_DIR}: {e}", exc_info=True)

    async def initialize_bootstrap_agents(self):
        await agent_lifecycle.initialize_bootstrap_agents(self)
        if self.current_session_db_id is not None:
            for agent_id in self.bootstrap_agents:
                agent = self.agents.get(agent_id)
                if agent: await self.db_manager.add_agent_record(session_id=self.current_session_db_id, agent_id=agent.agent_id, persona=agent.persona, model_config_dict=agent.agent_config.get("config", {}))
        else: logger.warning("Cannot log bootstrap agent DB records: current_session_db_id is None.")

    async def create_agent_instance( self, agent_id_requested: Optional[str], provider: Optional[str], model: Optional[str], system_prompt: str, persona: str, team_id: Optional[str] = None, temperature: Optional[float] = None, **kwargs ) -> Tuple[bool, str, Optional[str]]:
        success, message, created_agent_id = await agent_lifecycle.create_agent_instance(self, agent_id_requested, provider, model, system_prompt, persona, team_id, temperature, **kwargs)
        if success and created_agent_id and self.current_session_db_id is not None:
            agent = self.agents.get(created_agent_id)
            if agent: await self.db_manager.add_agent_record(session_id=self.current_session_db_id, agent_id=agent.agent_id, persona=agent.persona, model_config_dict=agent.agent_config.get("config", {}))
        elif success and created_agent_id: logger.warning(f"Agent '{created_agent_id}' created but cannot log to DB: current_session_db_id is None.")
        return success, message, created_agent_id

    async def delete_agent_instance(self, agent_id: str) -> Tuple[bool, str]:
        return await agent_lifecycle.delete_agent_instance(self, agent_id)

    async def schedule_cycle(self, agent: Agent, retry_count: int = 0) -> Optional[asyncio.Task]:
        if not agent:
            logger.error("Schedule cycle called with invalid Agent object.")
            return None
        logger.info(f"Manager: schedule_cycle ASYNC called for agent '{agent.agent_id}' (Retry: {retry_count}). Creating asyncio task...")
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self.cycle_handler.run_cycle(agent, retry_count))
            task_name = getattr(task, 'get_name', lambda: 'N/A')()
            logger.info(f"Manager: Successfully created asyncio task object {task_name} for agent '{agent.agent_id}' cycle.")
            return task
        except RuntimeError as loop_err:
            logger.error(f"Manager: FAILED to get running event loop for agent '{agent.agent_id}' cycle: {loop_err}. Scheduling may fail if not in async context.")
            # Fallback ensure_future might still work if loop is not yet running but will be
            asyncio.ensure_future(self.cycle_handler.run_cycle(agent, retry_count))
            return None # Task object might not be reliably available here
        except Exception as e:
            logger.error(f"Manager: FAILED to create asyncio task for agent '{agent.agent_id}' cycle: {e}", exc_info=True)
            return None

    async def handle_user_message(self, message: str, client_id: Optional[str] = None):
        if self.current_project is None or self.current_session_db_id is None:
            # Ensure default session context if none exists
            await self.set_project_session_context(DEFAULT_PROJECT_NAME, f"session_{int(time.time())}")

        # Log the user interaction first, regardless of processing outcome for AdminAI
        if self.current_session_db_id:
            await self.db_manager.log_interaction(session_id=self.current_session_db_id, agent_id="human_user", role="user", content=message)

        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID)
        if not admin_agent:
            logger.error(f"Admin AI '{BOOTSTRAP_AGENT_ID}' not found.")
            await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."})
            return

        # Check for project approval command specifically for Admin AI
        # This regex will capture the PM agent ID from messages like "approve project pm_ProjectName_timestamp"
        approval_match = re.match(r"^approve project (pm_[a-zA-Z0-9_.-]+)$", message.strip())

        if admin_agent.agent_id == BOOTSTRAP_AGENT_ID and approval_match:
            pm_agent_id_from_message = approval_match.group(1)
            pm_agent_to_check = self.agents.get(pm_agent_id_from_message)

            # Check if the PM agent exists and was awaiting approval.
            # The _awaiting_project_approval flag is set by ProjectCreationWorkflow
            # and cleared by the HTTP /approve endpoint *before* the PM is scheduled.
            # So, if this flag is still True here, it's an edge case or a different timing.
            # A more reliable check might be to see if the PM is in PM_STATE_STARTUP
            # and its _awaiting_project_approval flag was recently true or if the project is in a pending state.
            # For now, let's rely on the fact that the HTTP route should handle the primary approval.
            # This interception is mainly to prevent AdminAI from misinterpreting the user's echo of this command.

            if pm_agent_to_check:
                logger.info(f"Manager: Intercepted potential project approval message for PM '{pm_agent_id_from_message}' directed at Admin AI ('{message.strip()}'). PM activation should be handled by the dedicated HTTP POST to /api/projects/approve/{pm_agent_id_from_message}. Admin AI will not be directly scheduled with this message to prevent misinterpretation.")

                # Ensure AdminAI is in a sensible state if it was busy
                if admin_agent.status != AGENT_STATUS_IDLE:
                    # Admin was busy, queue the message (it will be logged by default path later if not returned)
                    # but log that we are aware it's an approval.
                    logger.warning(f"Admin AI status is '{admin_agent.status}'. Approval message for '{pm_agent_id_from_message}' will be added to queue but Admin AI cycle not forced by this interception logic.")
                    # Fall through to default queuing logic, but the context of interception is logged.
                elif admin_agent.state != ADMIN_STATE_CONVERSATION:
                    logger.warning(f"Admin AI was in state '{admin_agent.state}' when approval message for '{pm_agent_id_from_message}' was intercepted. Forcing to conversation state. Message will be processed if Admin AI becomes idle.")
                    self.workflow_manager.change_state(admin_agent, ADMIN_STATE_CONVERSATION)
                     # Message will be processed by default logic if admin becomes idle
                else:
                    # Admin is IDLE and in CONVERSATION.
                    # This message is likely the user confirming via chat after UI approval.
                    # We will let it pass to AdminAI, but the prompt for admin_conversation_prompt
                    # (Step 3 of the plan) should make AdminAI handle it gracefully.
                    # The critical part is that the PM is activated by the HTTP route, not by AdminAI misinterpreting this.
                    # For now, we'll let it pass through to the standard logic below,
                    # relying on prompt changes to make AdminAI respond correctly.
                    # The key is that the PM's activation is decoupled from AdminAI processing this.
                    logger.info(f"Admin AI is IDLE and in CONVERSATION. Approval message for PM '{pm_agent_id_from_message}' will be passed to AdminAI. Its prompt should guide it to acknowledge correctly.")
                    pass # Let it fall through to the default handling

        # Default message handling for Admin AI
        if admin_agent.status == AGENT_STATUS_IDLE:
            admin_agent.message_history.append({"role": "user", "content": message})
            await self.schedule_cycle(admin_agent, 0)
        else:
            # Append to history even if busy, so it's there when it becomes idle
            admin_agent.message_history.append({"role": "user", "content": message})
            await self.push_agent_status_update(admin_agent.agent_id) # Update UI that it's busy
            await self.send_to_ui({
                "type": "status",
                "agent_id": admin_agent.agent_id,
                "content": f"Admin AI is currently {admin_agent.status}. Your message ('{message[:50]}...') has been queued."
            })

    async def handle_agent_model_failover(self, agent_id: str, last_error_obj: Exception) -> bool:
        return await handle_agent_model_failover(self, agent_id, last_error_obj)

    async def push_agent_status_update(self, agent_id: str):
        agent = self.agents.get(agent_id)
        if agent: state = agent.get_state(); state["team"] = self.state_manager.get_agent_team(agent_id)
        else: state = {"status": "deleted", "team": None}; logger.warning(f"Cannot push status for unknown agent: {agent_id}")
        await self.send_to_ui({"type": "agent_status_update", "agent_id": agent_id, "status": state})

    async def send_to_ui(self, message_data: Dict[str, Any]):
        if not self.send_to_ui_func: logger.warning("UI broadcast func not set."); return
        
        # Add debug logging for agent_thought, agent_raw_response, and tool_result events
        event_type = message_data.get("type", "unknown")
        if event_type in ["agent_thought", "agent_raw_response", "tool_result"]:
            logger.info(f"Manager: Sending {event_type} event to UI for agent {message_data.get('agent_id', 'unknown')}")
        
        try: 
            await self.send_to_ui_func(json.dumps(message_data))
            if event_type in ["agent_thought", "agent_raw_response", "tool_result"]:
                logger.info(f"Manager: Successfully sent {event_type} event to UI via broadcast")
        except Exception as e: 
            logger.error(f"Error sending to UI: {e}. Data: {message_data}", exc_info=True)

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        return {aid: (ag.get_state() | {"team": self.state_manager.get_agent_team(aid)}) for aid, ag in self.agents.items()}

    async def save_session(self, project_name: str, session_name: Optional[str] = None) -> Tuple[bool, str]:
        if not session_name: session_name = f"session_{int(time.time())}"
        fs_success, fs_message = await self.session_manager.save_session(project_name, session_name)
        if not fs_success: return False, fs_message
        await self.set_project_session_context(project_name, session_name, loading=False)
        if not self.current_session_db_id: return True, f"{fs_message} but failed to update database session record."
        return True, f"{fs_message} Session context and DB record updated."

    async def load_session(self, project_name: str, session_name: str) -> Tuple[bool, str]:
        fs_success, fs_message = await self.session_manager.load_session(project_name, session_name)
        if not fs_success: return False, fs_message
        await self.set_project_session_context(project_name, session_name, loading=True)
        if not self.current_session_db_id: fs_message += " (Warning: DB session record not found/created)"
        return True, fs_message

    def get_agent_info_list_sync(self, filter_team_id: Optional[str] = None) -> List[Dict[str, Any]]:
        info_list = []
        for agent_id, agent in self.agents.items():
             current_team = self.state_manager.get_agent_team(agent_id)
             if filter_team_id is not None and current_team != filter_team_id: continue
             state = agent.get_state(); info = {"agent_id": agent_id, "persona": state.get("persona"), "provider": state.get("provider"), "model": state.get("model"), "status": state.get("status"), "team": current_team}; info_list.append(info)
        return info_list

    async def cleanup_providers(self):
        logger.info("Manager: Cleaning up LLM providers, saving metrics, quarantine, stopping timers, closing DB...");
        await self.stop_pm_manage_timer()
        if self.current_session_db_id: await self.db_manager.end_session(self.current_session_db_id); self.current_session_db_id = None # type: ignore
        active_providers = {agent.llm_provider for agent in self.agents.values() if agent.llm_provider}
        provider_tasks = [asyncio.create_task(self._close_provider_safe(p)) for p in active_providers if hasattr(p, 'close_session')]
        all_cleanup_tasks = provider_tasks + [
            asyncio.create_task(self.performance_tracker.save_metrics()),
            asyncio.create_task(self.key_manager.save_quarantine_state())
        ]
        if all_cleanup_tasks: await asyncio.gather(*all_cleanup_tasks)
        await close_db_connection(); logger.info("Manager: Database connection closed.")

    async def _close_provider_safe(self, provider: BaseLLMProvider):
        try:
             if hasattr(provider, 'close_session') and callable(provider.close_session): await provider.close_session()
        except Exception as e: logger.error(f"Manager: Error closing session for {provider!r}: {e}", exc_info=True)

    async def _periodic_pm_manage_check(self):
        interval = settings.PM_MANAGE_CHECK_INTERVAL_SECONDS
        logger.info(f"Starting periodic PM manage check loop (Interval: {interval}s)...")
        while True:
            await asyncio.sleep(interval)
            logger.debug("Running periodic PM manage check...")
            try:
                agents_snapshot = list(self.agents.values()) 
                for agent in agents_snapshot:
                    if (agent.agent_type == AGENT_TYPE_PM and
                        agent.status == AGENT_STATUS_IDLE and
                        agent.state == PM_STATE_MANAGE and 
                        not getattr(agent, '_awaiting_project_approval', False)):
                        
                        # Enhanced loop prevention: Check if agent is cycling too frequently
                        current_time = time.time()
                        last_check_time = getattr(agent, '_last_periodic_check_time', 0)
                        time_since_last_check = current_time - last_check_time
                        
                        # Track cycle frequency
                        if not hasattr(agent, '_periodic_cycle_count'):
                            agent._periodic_cycle_count = 0
                        if not hasattr(agent, '_periodic_cycle_window_start'):
                            agent._periodic_cycle_window_start = current_time
                            
                        # Reset counter if outside window (5 minutes)
                        if current_time - agent._periodic_cycle_window_start > 300:
                            agent._periodic_cycle_count = 0
                            agent._periodic_cycle_window_start = current_time
                        
                        # Check if agent is cycling too frequently (more than 20 times in 5 minutes)
                        if agent._periodic_cycle_count >= 20:
                            logger.error(f"PM '{agent.agent_id}' has been triggered {agent._periodic_cycle_count} times in the last 5 minutes. This indicates infinite looping. Forcing to error state.")
                            agent.set_status(AGENT_STATUS_ERROR)
                            
                            error_message = f"PM agent '{agent.agent_id}' has been cycling excessively ({agent._periodic_cycle_count} times in 5 minutes). Stopped to prevent infinite loop."
                            agent.message_history.append({"role": "system", "content": f"[Framework Error]: {error_message}"})
                            
                            if self.current_session_db_id:
                                await self.db_manager.log_interaction(
                                    session_id=self.current_session_db_id,
                                    agent_id=agent.agent_id,
                                    role="system_error",
                                    content=error_message
                                )
                            
                            await self.send_to_ui({"type": "error", "agent_id": agent.agent_id, "content": error_message})
                            continue
                        
                        # Add completion detection check before scheduling
                        if await self._check_pm_completion_status(agent):
                            logger.info(f"PM '{agent.agent_id}' project appears complete. Skipping periodic scheduling.")
                            continue
                            
                        agent._periodic_cycle_count += 1
                        agent._last_periodic_check_time = current_time
                        
                        logger.info(f"PM '{agent.agent_id}' idle in MANAGE state. Scheduling cycle by timer. (Count: {agent._periodic_cycle_count})")
                        await self.schedule_cycle(agent, 0) 
            except Exception as e: logger.error(f"Error during periodic PM manage check: {e}", exc_info=True)

    async def start_pm_manage_timer(self):
        if self._pm_manage_task is None or self._pm_manage_task.done():
            self._pm_manage_task = asyncio.create_task(self._periodic_pm_manage_check())
        else: logger.info("PM manage timer task already running.")

    async def stop_pm_manage_timer(self):
        if self._pm_manage_task and not self._pm_manage_task.done():
            self._pm_manage_task.cancel()
            try: await self._pm_manage_task
            except asyncio.CancelledError: logger.info("PM manage timer task cancelled.")
            self._pm_manage_task = None
        else: logger.info("PM manage timer task not running or already stopped.")

    async def _periodic_cg_check(self):
        interval = settings.CG_HEARTBEAT_INTERVAL_SECONDS
        threshold = settings.CG_STALLED_THRESHOLD_SECONDS
        logger.info(f"Starting periodic CG check loop (Interval: {interval}s, Threshold: {threshold}s)...")
        while True:
            await asyncio.sleep(interval)
            logger.debug("Running periodic CG check...")
            try:
                agents_snapshot = list(self.agents.values())
                for agent in agents_snapshot:
                    if agent.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG and agent.cg_review_start_time is not None:
                        stalled_time = time.time() - agent.cg_review_start_time
                        if stalled_time > threshold:
                            logger.warning(f"Agent '{agent.agent_id}' has been awaiting CG review for {stalled_time:.2f} seconds. Notifying Admin AI.")
                            # Reset the start time to avoid repeated notifications for the same stall
                            agent.cg_review_start_time = time.time()

                            admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID)
                            if admin_agent:
                                message_content = f"[System Notification from Constitutional Guardian]: The agent '{agent.agent_id}' has been awaiting user review for over {threshold} seconds regarding a constitutional concern. You may need to inform the user or investigate."
                                admin_agent.message_history.append({
                                    "role": "system",
                                    "content": message_content
                                })
                                if admin_agent.status == AGENT_STATUS_IDLE:
                                    await self.schedule_cycle(admin_agent)
                                else:
                                    logger.info(f"Admin AI is busy, but CG stall notification for '{agent.agent_id}' was added to its queue.")
            except Exception as e:
                logger.error(f"Error during periodic CG check: {e}", exc_info=True)

    async def start_cg_heartbeat_timer(self):
        if self._cg_heartbeat_task is None or self._cg_heartbeat_task.done():
            self._cg_heartbeat_task = asyncio.create_task(self._periodic_cg_check())
        else:
            logger.info("CG heartbeat timer task already running.")

    async def stop_cg_heartbeat_timer(self):
        if self._cg_heartbeat_task and not self._cg_heartbeat_task.done():
            self._cg_heartbeat_task.cancel()
            try:
                await self._cg_heartbeat_task
            except asyncio.CancelledError:
                logger.info("CG heartbeat timer task cancelled.")
            self._cg_heartbeat_task = None
        else:
            logger.info("CG heartbeat timer task not running or already stopped.")

    async def resolve_cg_concern_approve(self, agent_id: str):
        agent = self.agents.get(agent_id)
        if not agent:
            logger.error(f"resolve_cg_concern_approve: Agent '{agent_id}' not found.")
            return False, f"Agent '{agent_id}' not found."

        if not (agent.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG and agent.cg_awaiting_user_decision):
            logger.warning(f"resolve_cg_concern_approve: Agent '{agent_id}' is not awaiting user decision on a CG concern. Status: {agent.status}, Flag: {agent.cg_awaiting_user_decision}")
            return False, f"Agent '{agent_id}' is not in the correct state for this action."

        logger.info(f"User approved original output for agent '{agent_id}' despite CG concern.")
        
        original_text = agent.cg_original_text
        original_event_data = agent.cg_original_event_data

        if original_text is not None and original_event_data is not None:
            # Process original output: Log to DB, Send to UI as original final_response
            if self.current_session_db_id: # Ensure session context for DB logging
                is_direct_response = not agent.message_history or not agent.message_history[-1].get("tool_calls")
                if is_direct_response: 
                     await self.db_manager.log_interaction(
                         session_id=self.current_session_db_id,
                         agent_id=agent.agent_id,
                         role="assistant",
                         content=original_text
                     )
            await self.send_to_ui(original_event_data) 

            if not any(msg['role'] == 'assistant' and msg['content'] == original_text for msg in reversed(agent.message_history[-2:])):
                 agent.message_history.append({"role": "assistant", "content": original_text})

        agent.cg_original_text = None
        agent.cg_concern_details = None
        agent.cg_original_event_data = None
        agent.cg_awaiting_user_decision = False
        agent.set_status(AGENT_STATUS_IDLE) 

        logger.info(f"Agent '{agent_id}' status set to IDLE after CG concern approval by user.") # Log message modified as per instruction (kept level as info)

        # New part: Re-process the approved output for workflows
        if original_text: # original_text is agent.cg_original_text, which was agent.last_response_for_cg_review
            logger.info(f"Re-processing user-approved output of agent '{agent_id}' for workflows (original output was: '{original_text[:100]}...').")
            # Create a new task for the workflow processing.
            # This ensures it doesn't block the current operation.
            asyncio.create_task(
                self.workflow_manager.process_agent_output_for_workflow(
                    manager=self, # process_agent_output_for_workflow expects manager as first arg
                    agent=agent,
                    llm_output=original_text # Parameter is named llm_output in the method
                )
            )
            logger.debug(f"Scheduled workflow processing task for agent '{agent_id}'.")
        else:
            logger.warning(f"Original text for agent '{agent_id}' was empty/None after CG concern approval; cannot process for workflows.")

        # Websocket updates would typically be here if this method directly sent them,
        # but status updates are pushed via agent.set_status and other UI messages directly.
        # The final return indicates success of this resolution step.
        return True, f"Agent '{agent_id}' output approved and processed. Workflow re-processing initiated if applicable."

    async def resolve_cg_concern_stop(self, agent_id: str):
        agent = self.agents.get(agent_id)
        if not agent:
            logger.error(f"resolve_cg_concern_stop: Agent '{agent_id}' not found.")
            return False, f"Agent '{agent_id}' not found."

        if not (agent.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG and agent.cg_awaiting_user_decision):
            logger.warning(f"resolve_cg_concern_stop: Agent '{agent_id}' is not awaiting user decision on a CG concern. Status: {agent.status}, Flag: {agent.cg_awaiting_user_decision}")
            return False, f"Agent '{agent_id}' is not in the correct state for this action."

        logger.info(f"User stopped agent '{agent_id}' due to CG concern: {agent.cg_concern_details}")
        
        agent.set_status(AGENT_STATUS_ERROR) 
        
        agent.cg_original_text = None
        agent.cg_concern_details = None
        agent.cg_original_event_data = None
        agent.cg_awaiting_user_decision = False
        
        return True, f"Agent '{agent_id}' stopped by user due to CG concern."

    async def resolve_cg_concern_retry(self, agent_id: str, user_feedback: str):
        agent = self.agents.get(agent_id)
        if not agent:
            logger.error(f"resolve_cg_concern_retry: Agent '{agent_id}' not found.")
            return False, f"Agent '{agent_id}' not found."

        if not (agent.status == AGENT_STATUS_AWAITING_USER_REVIEW_CG and agent.cg_awaiting_user_decision):
            logger.warning(f"resolve_cg_concern_retry: Agent '{agent_id}' is not awaiting user decision on a CG concern. Status: {agent.status}, Flag: {agent.cg_awaiting_user_decision}")
            return False, f"Agent '{agent_id}' is not in the correct state for this action."

        logger.info(f"User requested retry for agent '{agent_id}' with feedback, following CG concern.")

        feedback_message_content = (
            f"[Framework Feedback for Retry]\n"
            f"Your previous response was: '{agent.cg_original_text}'\n"
            f"A concern was raised by the Constitutional Guardian: '{agent.cg_concern_details}'\n"
            f"User Feedback: '{user_feedback}'\n"
            f"Please revise your response to address these points and retry your previous turn's objective."
        )
        
        agent.message_history.append({"role": "system", "content": feedback_message_content})
        
        agent.cg_original_text = None
        agent.cg_concern_details = None
        agent.cg_original_event_data = None
        agent.cg_awaiting_user_decision = False
        
        agent.set_status(AGENT_STATUS_IDLE) 

        await self.schedule_cycle(agent, retry_count=0) 
        
        logger.info(f"Agent '{agent_id}' set to IDLE and rescheduled for retry with user and CG feedback.")
        return True, f"Agent '{agent_id}' will retry with feedback."

    async def activate_worker_with_task_details(self, worker_agent_id: str, task_id_from_tool: str, task_description_from_tool: str):
        """
        Activates a worker agent with specific task details, ensuring the task description
        is injected for its next cycle.
        """
        worker_agent = self.agents.get(worker_agent_id)
        if not worker_agent:
            logger.error(f"AgentManager: Cannot activate worker '{worker_agent_id}'. Agent not found.")
            return

        if worker_agent.agent_type != AGENT_TYPE_WORKER:
            logger.error(f"AgentManager: Agent '{worker_agent_id}' is not a Worker. Cannot activate with task details.")
            return

        logger.info(f"AgentManager: Activating worker '{worker_agent_id}' for task ID '{task_id_from_tool}' with description: '{task_description_from_tool[:100]}...'")

        worker_agent._injected_task_description = task_description_from_tool
        worker_agent._needs_initial_work_context = True
        worker_agent.current_task_id = task_id_from_tool # Store the task ID as well

        # Ensure the agent is in the correct state and status to be scheduled
        self.workflow_manager.change_state(worker_agent, WORKER_STATE_WORK) # This also sets status to IDLE if state changes
        if worker_agent.status != AGENT_STATUS_IDLE: # If state didn't change but status was e.g. ERROR
            worker_agent.set_status(AGENT_STATUS_IDLE)

        # CRITICAL FIX: Initialize the worker's message history with the proper system prompt
        # This ensures the worker has context when it starts its first cycle
        if not worker_agent.message_history:  # Only if history is empty
            initial_system_prompt = self.workflow_manager.get_system_prompt(worker_agent, self)
            worker_agent.message_history.append({
                "role": "system", 
                "content": initial_system_prompt
            })
            logger.info(f"AgentManager: Initialized worker '{worker_agent_id}' with system prompt containing injected task context.")
        else:
            logger.debug(f"AgentManager: Worker '{worker_agent_id}' already has message history, skipping system prompt initialization.")

        # Log this activation and context injection to DB for traceability
        if self.current_session_db_id:
            await self.db_manager.log_interaction(
                session_id=self.current_session_db_id,
                agent_id=worker_agent_id,
                role="system_framework_event",
                content=f"Worker activated for task {task_id_from_tool}. Injected task description: {task_description_from_tool}"
            )

        await self.schedule_cycle(worker_agent, retry_count=0)
        logger.info(f"AgentManager: Worker '{worker_agent_id}' scheduled for task '{task_id_from_tool}' with proper context.")

    async def _check_pm_completion_status(self, agent: Agent) -> bool:
        """
        Check if a PM agent's project appears to be complete by examining task status
        and worker activity. Returns True if project appears complete.
        """
        try:
            # Use the project_management tool to check task status
            if not self.tool_executor or 'project_management' not in self.tool_executor.tools:
                logger.warning(f"PM completion check: project_management tool not available for agent '{agent.agent_id}'")
                return False
                
            # Get the current project name from the agent's config
            project_name = agent.agent_config.get("config", {}).get("project_name_context", "Unknown Project")
            
            # Call the project_management tool to list tasks
            result_dict = await self.interaction_handler.execute_single_tool(
                agent=agent,
                call_id="internal_completion_check",
                tool_name="project_management", 
                tool_args={"action": "list_tasks"},
                project_name=self.current_project,
                session_name=self.current_session
            )
            
            if not result_dict or result_dict.get("status") != "success":
                logger.debug(f"PM completion check: Failed to get task list for agent '{agent.agent_id}'")
                return False
                
            # Parse the result content
            import json
            result_content = result_dict.get("content", "{}")
            if isinstance(result_content, str):
                try:
                    task_data = json.loads(result_content)
                except json.JSONDecodeError:
                    logger.debug(f"PM completion check: Failed to parse task data for agent '{agent.agent_id}'")
                    return False
            else:
                task_data = result_content
                
            tasks = task_data.get("tasks", [])
            
            # Check if there are no unassigned tasks
            unassigned_tasks = [task for task in tasks if not task.get("depends")]
            
            if not unassigned_tasks:
                logger.info(f"PM completion check: No unassigned tasks found for agent '{agent.agent_id}'. Project may be complete.")
                
                # Additional check: verify workers are not actively working
                team_id = self.state_manager.get_agent_team(agent.agent_id)
                if team_id:
                    team_agents = self.state_manager.get_agents_in_team(team_id)
                    active_workers = [a for a in team_agents if a.agent_type == AGENT_TYPE_WORKER and a.status != AGENT_STATUS_IDLE]
                    
                    if not active_workers:
                        logger.info(f"PM completion check: No active workers found for team '{team_id}'. Project appears complete.")
                        return True
                    else:
                        logger.debug(f"PM completion check: Found {len(active_workers)} active workers for team '{team_id}'. Project not complete.")
                        return False
                else:
                    # No team means project is likely complete or has no workers
                    logger.info(f"PM completion check: No team found for agent '{agent.agent_id}'. Project may be complete.")
                    return True
            else:
                logger.debug(f"PM completion check: Found {len(unassigned_tasks)} unassigned tasks for agent '{agent.agent_id}'. Project not complete.")
                return False
                
        except Exception as e:
            logger.error(f"PM completion check: Error checking completion status for agent '{agent.agent_id}': {e}", exc_info=True)
            return False


logging.info("manager.py: Module loading finished.")

# Resolve forward references for CycleContext
CycleContext.update_refs(AgentManager=AgentManager, Agent=Agent)
