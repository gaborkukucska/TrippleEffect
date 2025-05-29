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

logging.info("manager.py: Importing database_manager...")
from src.core.database_manager import db_manager, close_db_connection, Project as DBProject, Session as DBSession
logging.info("manager.py: Imported database_manager.")

logging.info("manager.py: Importing constants...")
from src.agents.constants import (
    AGENT_STATUS_IDLE, AGENT_STATUS_ERROR, BOOTSTRAP_AGENT_ID, ADMIN_STATE_CONVERSATION,
    AGENT_TYPE_PM, PM_STATE_WORK, PM_STATE_MANAGE, PM_STATE_STARTUP
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
        logger.info("AgentManager __init__: Initialized synchronously.")
        asyncio.create_task(self._ensure_default_db_session())
        asyncio.create_task(self.start_pm_manage_timer())

    async def _ensure_default_db_session(self):
        if self.current_session_db_id is None:
             await self.set_project_session_context(DEFAULT_PROJECT_NAME, f"startup_{int(time.time())}")

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
            await self.set_project_session_context(DEFAULT_PROJECT_NAME, f"session_{int(time.time())}")
        if self.current_session_db_id: await self.db_manager.log_interaction(session_id=self.current_session_db_id, agent_id="human_user", role="user", content=message)
        admin_agent = self.agents.get(BOOTSTRAP_AGENT_ID)
        if not admin_agent: logger.error(f"Admin AI '{BOOTSTRAP_AGENT_ID}' not found."); await self.send_to_ui({"type": "error", "agent_id": "manager", "content": "Admin AI unavailable."}); return
        if admin_agent.status == AGENT_STATUS_IDLE:
            admin_agent.message_history.append({"role": "user", "content": message})
            await self.schedule_cycle(admin_agent, 0) 
        else:
            admin_agent.message_history.append({"role": "user", "content": message})
            await self.push_agent_status_update(admin_agent.agent_id)
            await self.send_to_ui({ "type": "status", "agent_id": admin_agent.agent_id, "content": f"Admin AI busy ({admin_agent.status}). Queued." })

    async def handle_agent_model_failover(self, agent_id: str, last_error_obj: Exception) -> bool:
        return await handle_agent_model_failover(self, agent_id, last_error_obj)

    async def push_agent_status_update(self, agent_id: str):
        agent = self.agents.get(agent_id)
        if agent: state = agent.get_state(); state["team"] = self.state_manager.get_agent_team(agent_id)
        else: state = {"status": "deleted", "team": None}; logger.warning(f"Cannot push status for unknown agent: {agent_id}")
        await self.send_to_ui({"type": "agent_status_update", "agent_id": agent_id, "status": state})

    async def send_to_ui(self, message_data: Dict[str, Any]):
        if not self.send_to_ui_func: logger.warning("UI broadcast func not set."); return
        try: await self.send_to_ui_func(json.dumps(message_data))
        except Exception as e: logger.error(f"Error sending to UI: {e}. Data: {message_data}", exc_info=True)

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
                        logger.info(f"PM '{agent.agent_id}' idle in MANAGE state. Scheduling cycle by timer.")
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

logging.info("manager.py: Module loading finished.")

# Resolve forward references for CycleContext
CycleContext.update_refs(AgentManager=AgentManager, Agent=Agent)