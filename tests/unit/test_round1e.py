# START OF FILE tests/unit/test_round1e.py
"""
Round 1e regression tests:
- Worker task tracking blocks completed/finished tasks.
- Worker task tracking requires worker_wait when 0 pending tasks exist.
- Worker task tracking auto-infers pending task when available.
- PromptAssembler worker tasks report shows guidance when 0 pending tasks.
"""
import pytest
from unittest.mock import MagicMock, patch
from src.agents.constants import AGENT_TYPE_WORKER, WORKER_STATE_WORK


def test_handle_worker_task_tracking_blocks_finished_task():
    from src.agents.cycle_handler import AgentCycleHandler
    handler = AgentCycleHandler(manager=MagicMock(), interaction_handler=MagicMock())
    handler._manager.workflow_manager.resolve_state_alias.return_value = WORKER_STATE_WORK

    agent = MagicMock()
    agent.agent_id = "W2"
    agent.agent_type = AGENT_TYPE_WORKER

    test_uuid = "11111111-2222-3333-4444-555555555555"
    mock_task = {
        "task_progress": "finished",
        "status": "completed",
        "uuid": test_uuid,
        "description": "Task already done"
    }
    handler._manager.current_project = "Proj"
    handler._manager.current_session = "Sess"

    with pytest.raises(ValueError, match="already marked as"):
        with patch("src.tools.project_management.ProjectManagementTool._get_taskwarrior_instance") as mock_get_tw:
            tw = MagicMock()
            tw.tasks.get.return_value = mock_task
            mock_get_tw.return_value = tw
            handler._handle_worker_task_tracking(agent, WORKER_STATE_WORK, task_id=test_uuid)


def test_handle_worker_task_tracking_requires_worker_wait_when_no_tasks():
    from src.agents.cycle_handler import AgentCycleHandler
    handler = AgentCycleHandler(manager=MagicMock(), interaction_handler=MagicMock())
    handler._manager.workflow_manager.resolve_state_alias.return_value = WORKER_STATE_WORK
    handler._infer_worker_task = MagicMock(return_value=None)

    agent = MagicMock()
    agent.agent_id = "W2"
    agent.agent_type = AGENT_TYPE_WORKER

    with pytest.raises(ValueError, match="no pending tasks assigned to you.*worker_wait"):
        handler._handle_worker_task_tracking(agent, WORKER_STATE_WORK, task_id=None)


def test_handle_worker_task_tracking_infers_task_when_available():
    from src.agents.cycle_handler import AgentCycleHandler
    handler = AgentCycleHandler(manager=MagicMock(), interaction_handler=MagicMock())
    handler._manager.workflow_manager.resolve_state_alias.return_value = WORKER_STATE_WORK
    
    test_uuid = "22222222-3333-4444-5555-666666666666"
    handler._infer_worker_task = MagicMock(return_value=test_uuid)

    agent = MagicMock()
    agent.agent_id = "W1"
    agent.agent_type = AGENT_TYPE_WORKER

    mock_task = MagicMock()
    mock_task.__getitem__.side_effect = lambda key: {"task_progress": "todo", "status": "pending", "uuid": test_uuid}.get(key)
    handler._manager.current_project = "Proj"
    handler._manager.current_session = "Sess"

    with patch("src.tools.project_management.ProjectManagementTool._get_taskwarrior_instance") as mock_get_tw:
        tw = MagicMock()
        tw.tasks.get.return_value = mock_task
        mock_get_tw.return_value = tw
        handler._handle_worker_task_tracking(agent, WORKER_STATE_WORK, task_id=None)
        assert agent.active_task_id == test_uuid
# END OF FILE tests/unit/test_round1e.py
