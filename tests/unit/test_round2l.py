# START OF FILE tests/unit/test_round2l.py
"""
Round 2l: fixtures from the 2026-10-20 13:08-16:44 run (project Browser_Snake_Game, session startup_1792472935).

* 13:08:55 - with MODEL_TIER=LOCAL, LOCAL_API_SCAN_ENABLED=false and OLLAMA_API_URLS="https://oli.gaborkukucska.com", the
  registry still verified http://localhost:11434 and http://127.0.0.1:11434, registered them as 'ollama-local', named the
  remote server 'ollama-local-unknown-host' (and failed its duplicate check: "invalid literal for int() with base 10: ''"),
  and spread the agents round-robin: Admin AI and PM1 on localhost, the Guardian and W1 on the remote server.
* 16:30-16:40 - W1 reported Kick-off Task 6 as COMPLETED and requested worker_wait 8 times without marking it finished;
  the watchdog started it again every minute until PM1 marked it finished.
* 16:29:14 - the workspace listing for W1 had 2502 files; the 50 shown were all node_modules files.
"""
import asyncio
import logging
import shutil
import types

import pytest

from src.config.model_registry import (
    ModelRegistry, drop_default_localhost_ollama, provider_suffix_for_host, url_host_port,
)

REMOTE = "https://oli.gaborkukucska.com"
URLS_13_08 = {"http://127.0.0.1:11434", "http://127.0.0.1:8000", "http://localhost:4000", "http://localhost:11434", REMOTE,
              "http://127.0.0.1:4000", "http://localhost:8000"}


# --- URL helpers ----------------------------------------------------------------------------------------------------------
def test_url_host_port():
    assert url_host_port(REMOTE) == ("oli.gaborkukucska.com", 443)
    assert url_host_port("http://oli.gaborkukucska.com/") == ("oli.gaborkukucska.com", 80)
    assert url_host_port("http://localhost:11434") == ("localhost", 11434)
    assert url_host_port("192.168.0.22:11434") == ("192.168.0.22", 11434)


def test_provider_suffix_for_host():
    assert provider_suffix_for_host("oli.gaborkukucska.com") == "oli-gaborkukucska-com"
    assert provider_suffix_for_host("localhost") == "127-0-0-1"
    assert provider_suffix_for_host("192.168.0.22") == "192-168-0-22"
    assert provider_suffix_for_host("") == "unknown-host"


def test_explicit_ollama_urls_replace_the_default_localhost_ollama():
    kept = drop_default_localhost_ollama(set(URLS_13_08), [REMOTE], include_localhost=False)
    assert kept == URLS_13_08 - {"http://127.0.0.1:11434", "http://localhost:11434"}
    assert drop_default_localhost_ollama(set(URLS_13_08), [REMOTE], include_localhost=True) == URLS_13_08
    assert drop_default_localhost_ollama(set(URLS_13_08), [], include_localhost=False) == URLS_13_08
    # a localhost Ollama that is listed explicitly stays
    kept = drop_default_localhost_ollama(set(URLS_13_08), [REMOTE, "http://localhost:11434"], include_localhost=False)
    assert "http://localhost:11434" in kept and "http://127.0.0.1:11434" not in kept


# --- discovery ------------------------------------------------------------------------------------------------------------
def _settings(include_localhost=False, urls=(REMOTE,)):
    return types.SimpleNamespace(
        MODEL_TIER="LOCAL", OLLAMA_API_URLS=list(urls), VLLM_API_URLS=[], LOCAL_API_SCAN_PORTS=[11434, 8000],
        LOCAL_API_SCAN_ENABLED=False, LOCAL_API_SCAN_TIMEOUT=0.5, OLLAMA_INCLUDE_LOCALHOST=include_localhost,
        is_provider_configured=lambda name: False)


def _discover(settings):
    registry = ModelRegistry(settings)
    checked = []

    async def fake_verify(url):
        checked.append(url)
        host, port = url_host_port(url)
        if port in (11434, 443):  # both Ollama servers answer; the vLLM/LiteLLM ports do not
            return f"ollama-local-{provider_suffix_for_host(host)}", url, [{"id": "qwen3.5:9b-q4_K_M", "name": "qwen3.5:9b-q4_K_M"}]
        return None

    registry._verify_and_fetch_models = fake_verify
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(registry.discover_models_and_providers())
    finally:
        logging.disable(logging.NOTSET)
    return registry, checked


def test_only_the_configured_ollama_server_is_used():
    registry, checked = _discover(_settings())
    assert REMOTE in checked
    assert "http://localhost:11434" not in checked and "http://127.0.0.1:11434" not in checked
    assert sorted(registry.available_models) == ["ollama-local-oli-gaborkukucska-com"]
    assert registry._reachable_providers["ollama-local-oli-gaborkukucska-com"] == REMOTE


def test_localhost_is_added_on_request():
    registry, checked = _discover(_settings(include_localhost=True))
    assert sorted(registry.available_models) == ["ollama-local", "ollama-local-oli-gaborkukucska-com"]


def test_without_explicit_urls_localhost_is_used_as_before():
    registry, checked = _discover(_settings(urls=()))
    assert sorted(registry.available_models) == ["ollama-local"]


# --- a worker does not park with its own open task ------------------------------------------------------------------------
TASK_6 = "Kick-off Task 6: Write README.md documentation including usage instructions for opening the file locally"


@pytest.fixture
def store(tmp_path, monkeypatch):
    pytest.importorskip("tasklib")
    if shutil.which("task") is None:
        pytest.skip("Taskwarrior binary not installed")
    import src.tools.project_management as pm
    monkeypatch.setattr(pm, "BASE_DIR", tmp_path)
    tool = pm.ProjectManagementTool()
    asyncio.run(tool.execute("PM1", tmp_path, project_name="Browser_Snake_Game", session_name="startup_1792472935",
                             action="add_task", description=TASK_6, assignee_agent_id="W1"))
    tw = tool._get_taskwarrior_instance("Browser_Snake_Game", "startup_1792472935")
    task = tw.tasks.pending()[0]
    task["task_progress"] = "doing"
    task.save()
    manager = types.SimpleNamespace(current_project="Browser_Snake_Game", current_session="startup_1792472935")
    return manager, tw


def _w1():
    return types.SimpleNamespace(agent_id="W1", agent_type="worker", state="worker_report")


def test_wait_is_refused_while_the_own_task_is_open(store):
    from src.agents.assignable_work import WORKER_WAIT_GUARD_TAG, worker_wait_block_message
    manager, tw = store
    agent = _w1()
    uuid = tw.tasks.pending()[0]["uuid"]
    message = worker_wait_block_message(manager, agent, "worker_wait")
    assert message.startswith(f"{WORKER_WAIT_GUARD_TAG} You did NOT go to worker_wait: your task {uuid}")
    assert "is still 'doing'" in message
    assert f'{{"action": "modify_task", "task_id": "{uuid}", "task_progress": "finished"}}' in message
    assert f"state='worker_work' and task_id='{uuid}'" in message
    # twice per task, then the request is allowed (the old behaviour)
    assert worker_wait_block_message(manager, agent, "worker_wait") is not None
    assert worker_wait_block_message(manager, agent, "worker_wait") is None


def test_wait_is_allowed_when_nothing_of_the_worker_is_open(store):
    from src.agents.assignable_work import worker_wait_block_message
    manager, tw = store
    task = tw.tasks.pending()[0]
    task["task_progress"] = "finished"
    task.save()
    assert worker_wait_block_message(manager, _w1(), "worker_wait") is None


def test_other_requests_and_agents_are_not_checked(store):
    from src.agents.assignable_work import worker_wait_block_message
    manager, _ = store
    assert worker_wait_block_message(manager, _w1(), "worker_work") is None
    pm = types.SimpleNamespace(agent_id="W1", agent_type="pm", state="pm_manage")
    assert worker_wait_block_message(manager, pm, "worker_wait") is None
    assert worker_wait_block_message(types.SimpleNamespace(), _w1(), "worker_wait") is None


def test_own_open_ready_tasks():
    from src.agents.assignable_work import own_open_ready_tasks
    tasks = [
        {"uuid": "a", "assignee": "W1", "status": "pending", "task_progress": "doing", "tags": ["task_order_2"]},
        {"uuid": "b", "assignee": "W1", "status": "pending", "task_progress": "todo", "tags": ["task_order_1"]},
        {"uuid": "c", "assignee": "W1", "status": "pending", "task_progress": "finished"},
        {"uuid": "d", "assignee": "W2", "status": "pending", "task_progress": "todo"},
        {"uuid": "e", "assignee": "W1", "status": "pending", "task_progress": "todo",
         "depends": [{"uuid": "x", "status": "pending", "task_progress": "todo"}]},
    ]
    assert [t["uuid"] for t in own_open_ready_tasks(tasks, "W1")] == ["b", "a"]


# --- the activation listing shows the project's files ---------------------------------------------------------------------
def test_the_activation_listing_skips_dependency_folders():
    from src.agents.manager import WORKSPACE_LISTING_SKIP_DIRS
    assert {"node_modules", ".te_background", ".git", "__pycache__", ".venv"} <= WORKSPACE_LISTING_SKIP_DIRS


def test_the_cycle_handler_refuses_the_wait_request(store):
    from unittest.mock import AsyncMock, MagicMock
    from src.agents.assignable_work import WORKER_WAIT_GUARD_TAG
    from src.agents.constants import AGENT_STATUS_IDLE
    from src.agents.core import Agent
    from src.agents.cycle_handler import AgentCycleHandler
    from src.agents.manager import AgentManager
    store_manager, tw = store

    logging.disable(logging.CRITICAL)
    try:
        manager = MagicMock(spec=AgentManager)
        manager.db_manager = AsyncMock()
        manager.performance_tracker = AsyncMock()
        manager.send_to_ui = AsyncMock()
        manager.schedule_cycle = AsyncMock(return_value=None)
        manager.current_project = store_manager.current_project
        manager.current_session = store_manager.current_session
        manager.current_session_db_id = None
        manager._agent_cycle_locks = {}
        workflow_manager = MagicMock()
        workflow_manager.resolve_state_alias.side_effect = lambda agent_type, state: state
        changes = []
        workflow_manager.change_state.side_effect = lambda agent, state, task_description=None: changes.append(state) or True
        manager.workflow_manager = workflow_manager

        agent = MagicMock(spec=Agent)
        agent.agent_id, agent.agent_type, agent.state, agent.status = "W1", "worker", "worker_report", AGENT_STATUS_IDLE
        agent.provider_name, agent.model, agent.persona = "ollama-local-oli-gaborkukucska-com", "qwen3.5:9b-q4_K_M", "Coder"
        agent.message_history = [{"role": "system", "content": "worker prompt"}]
        agent._failover_state, agent._failed_models_this_cycle = {}, set()

        async def events(*_args, **_kwargs):
            yield {"type": "agent_state_change_requested", "requested_state": "worker_wait", "agent_id": "W1",
                   "task_description": "Kick-off Task 6: Write README.md documentation — COMPLETED", "task_id": None}

        agent.process_message = MagicMock(side_effect=events)
        asyncio.run(AgentCycleHandler(manager, MagicMock()).run_cycle(agent, 0))
    finally:
        logging.disable(logging.NOTSET)
    assert "worker_wait" not in changes
    assert any(str(m.get("content", "")).startswith(WORKER_WAIT_GUARD_TAG) for m in agent.message_history)
