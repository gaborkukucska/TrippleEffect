# START OF FILE tests/unit/test_agent_cycle_handler.py
"""
Tests for AgentCycleHandler.run_cycle(): a reply with several tool calls.

Round 2m: the old tests used MagicMock(spec=Agent), which only knows the class attributes - every instance attribute
(state, llm_provider, needs_priority_recheck, ...) raised AttributeError, so the cycle stopped with "Unhandled
exception". They also expected the tool results inside the assistant message count of an older design. These tests
run the real cycle with a real Agent whose process_message() yields the events of one reply.

Rules under test:
* every call of a 'tool_requests' event is executed in order with (agent, id, name, arguments, project, session);
* history: one assistant message (the reply text, with 'tool_calls') and then one 'tool' message per result, in order;
  a failed tool does not stop the others; identical calls in one reply run once;
* each result goes to the UI as 'tool_result' with tool_sequence 'i_of_n' and to the DB as role 'tool'; the assistant
  message is logged with its tool_calls;
* the agent is scheduled again (retry 0) so it reads the results, and ends the cycle idle without an error;
* send_message together with an action tool is dropped with a "[Tool Execution Blocked]" result, the action tool runs.
"""
import asyncio
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.agents.constants import AGENT_STATUS_IDLE, AGENT_TYPE_WORKER, WORKER_STATE_WORK
from src.agents.core import Agent
from src.agents.cycle_handler import AgentCycleHandler
from src.agents.manager import AgentManager

DB_SESSION_ID = 12345


def make_manager():
    manager = MagicMock(spec=AgentManager)
    manager.db_manager = AsyncMock()
    manager.performance_tracker = AsyncMock()
    manager.send_to_ui = AsyncMock()
    manager.push_agent_status_update = AsyncMock()
    manager.schedule_cycle = AsyncMock(return_value=MagicMock(name="scheduled_task"))
    manager.current_project = "test_project"
    manager.current_session = "test_session"
    manager.current_session_db_id = DB_SESSION_ID
    manager._agent_cycle_locks = {}
    manager.tool_executor = MagicMock()
    manager.tool_executor.tools = {}
    workflow_manager = MagicMock()
    workflow_manager.resolve_state_alias.side_effect = lambda agent_type, state: state
    workflow_manager.get_system_prompt.return_value = "worker prompt"
    manager.workflow_manager = workflow_manager
    return manager


class TestCycleHandlerMultiTool(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.manager = make_manager()
        self.agent = Agent(agent_config={"agent_id": "W1", "config": {
            "provider": "ollama-local", "model": "qwen3.5:9b", "persona": "Coder", "agent_type": AGENT_TYPE_WORKER}},
            llm_provider=MagicMock(), manager=self.manager)
        self.agent.state = WORKER_STATE_WORK
        self.agent.message_history = [{"role": "system", "content": "worker prompt"}]
        sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(sandbox.cleanup)
        self.agent.sandbox_path = Path(sandbox.name) / "agent_W1"
        self.interaction_handler = MagicMock()
        self.interaction_handler.execute_single_tool = AsyncMock()
        self.handler = AgentCycleHandler(self.manager, self.interaction_handler)

    def run_reply(self, calls, results, text="Working on it."):
        event = {"type": "tool_requests", "calls": calls, "raw_assistant_response": text, "content_for_history": text,
                 "agent_id": "W1"}

        async def events(*_args, **_kwargs):
            yield event

        self.agent.process_message = MagicMock(side_effect=events)
        self.interaction_handler.execute_single_tool.side_effect = list(results)
        asyncio.run(self.handler.run_cycle(self.agent, 0))

    def after_prompt(self):
        return self.agent.message_history[1:]

    def ui_events(self, event_type):
        return [c.args[0] for c in self.manager.send_to_ui.call_args_list if c.args[0].get("type") == event_type]

    def db_roles(self):
        return [c.kwargs.get("role") for c in self.manager.db_manager.log_interaction.call_args_list]

    def test_multiple_successful_tool_calls(self):
        call_1 = {"id": "call_1", "name": "file_system", "arguments": {"action": "list"}}
        call_2 = {"id": "call_2", "name": "codebase_search", "arguments": {"query": "main"}}
        self.run_reply([call_1, call_2], [{"call_id": "call_1", "name": "file_system", "content": "a.py\nb.py"},
                                          {"call_id": "call_2", "name": "codebase_search", "content": "a.py:1 def main()"}])
        self.assertEqual([c.args for c in self.interaction_handler.execute_single_tool.call_args_list], [
            (self.agent, "call_1", "file_system", {"action": "list"}, "test_project", "test_session"),
            (self.agent, "call_2", "codebase_search", {"query": "main"}, "test_project", "test_session"),
        ])
        self.assertEqual(self.after_prompt(), [
            {"role": "assistant", "content": "Working on it.", "tool_calls": [call_1, call_2]},
            {"role": "tool", "tool_call_id": "call_1", "name": "file_system", "content": "a.py\nb.py"},
            {"role": "tool", "tool_call_id": "call_2", "name": "codebase_search", "content": "a.py:1 def main()"},
        ])
        results = self.ui_events("tool_result")
        self.assertEqual([(r["call_id"], r["tool_sequence"]) for r in results], [("call_1", "1_of_2"), ("call_2", "2_of_2")])
        self.assertEqual(self.ui_events("error"), [])
        self.assertEqual(self.db_roles(), ["assistant", "tool", "tool"])
        self.manager.db_manager.log_interaction.assert_any_call(
            session_id=DB_SESSION_ID, agent_id="W1", role="assistant", content="Working on it.", tool_calls=[call_1, call_2])
        self.manager.db_manager.log_interaction.assert_any_call(
            session_id=DB_SESSION_ID, agent_id="W1", role="tool", content="a.py\nb.py",
            tool_results=[{"call_id": "call_1", "name": "file_system", "content": "a.py\nb.py"}])
        self.manager.schedule_cycle.assert_called_with(self.agent, 0)
        self.assertEqual(self.agent.status, AGENT_STATUS_IDLE)

    def test_one_tool_fails_the_others_still_run(self):
        calls = [{"id": "call_s1", "name": "file_system", "arguments": {"action": "list"}},
                 {"id": "call_f1", "name": "codebase_search", "arguments": {"query": "x"}},
                 {"id": "call_s2", "name": "file_system", "arguments": {"action": "read", "filepath": "a.py"}}]
        self.run_reply(calls, [{"call_id": "call_s1", "name": "file_system", "content": "a.py"},
                               {"call_id": "call_f1", "name": "codebase_search", "content": "[ToolError] Failed tool execution"},
                               {"call_id": "call_s2", "name": "file_system", "content": "x = 1"}])
        self.assertEqual(self.interaction_handler.execute_single_tool.call_count, 3)
        self.assertEqual([m["content"] for m in self.after_prompt()[1:]],
                         ["a.py", "[ToolError] Failed tool execution", "x = 1"])
        self.assertEqual(len(self.ui_events("tool_result")), 3)
        self.assertEqual(self.ui_events("error"), [])
        self.assertEqual(self.db_roles(), ["assistant", "tool", "tool", "tool"])
        self.manager.schedule_cycle.assert_called_with(self.agent, 0)

    def test_identical_calls_in_one_reply_run_once(self):
        call = {"id": "call_1", "name": "file_system", "arguments": {"action": "list"}}
        self.run_reply([call, dict(call, id="call_2")], [{"call_id": "call_1", "name": "file_system", "content": "a.py"}])
        self.assertEqual(self.interaction_handler.execute_single_tool.call_count, 1)
        self.assertEqual([m["role"] for m in self.after_prompt()], ["assistant", "tool"])

    def test_send_message_with_an_action_tool_is_dropped(self):
        calls = [{"id": "call_w", "name": "file_system", "arguments": {"action": "write", "filepath": "a.py", "content": "x"}},
                 {"id": "call_m", "name": "send_message", "arguments": {"target_agent_id": "PM1", "message_content": "Done"}}]
        self.run_reply(calls, [{"call_id": "call_w", "name": "file_system", "content": "Wrote a.py"}])
        self.assertEqual([c.args[2] for c in self.interaction_handler.execute_single_tool.call_args_list], ["file_system"])
        tool_messages = self.after_prompt()[1:]
        self.assertEqual(tool_messages[0]["content"], "Wrote a.py")
        self.assertEqual(tool_messages[1]["tool_call_id"], "call_m")
        self.assertTrue(tool_messages[1]["content"].startswith("[Tool Execution Blocked]"))


if __name__ == '__main__':
    unittest.main()
