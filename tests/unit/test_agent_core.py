# START OF FILE tests/unit/test_agent_core.py
"""
Tests for Agent.process_message(): how a provider reply becomes events.

Round 2m: the old tests yielded no events at all - they mocked stream_completion as an AsyncMock (a coroutine; the
agent uses it directly as an async generator) and find_and_parse_xml_tool_calls as returning a list (it returns
{"valid_calls": [...], "parsing_errors": [...]}). These tests use a fake provider whose stream_completion is an async
generator, a real AgentWorkflowManager for state names, and cover both the native tool-call path (the one used with
Ollama) and the XML path.

Rules under test:
* native tool calls -> one 'tool_requests' event with every known tool; unknown tools are skipped; a native
  request_state call becomes 'agent_state_change_requested' (after the tool requests), an invalid state is not;
* XML path (native tool calling off): the parsed calls -> one 'tool_requests' event whose raw_assistant_response is the
  whole reply; a parsing error -> 'malformed_tool_call';
* plain text -> 'final_response'; a <think> block -> 'agent_thought' and the rest is parsed without it;
* a provider error -> an 'error' event and nothing else is parsed;
* finish_info 'length' without a tool call marks the reply as cut off; a reply that only thought switches thinking
  off for the next call; the tools offered follow the state allowlist and auth level.
"""
import asyncio
import logging
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents import core
from src.agents.constants import AGENT_TYPE_WORKER, WORKER_STATE_WORK
from src.agents.core import Agent
from src.agents.workflow_manager import AgentWorkflowManager
from src.workflows.base import WorkflowResult


class FakeProvider:
    """stream_completion is an async generator, like the real providers; the call arguments are recorded."""

    def __init__(self, events):
        self.events = events
        self.calls = []

    async def stream_completion(self, **kwargs):
        self.calls.append(kwargs)
        for event in self.events:
            yield event


def make_tool(name, auth_level="worker"):
    tool = MagicMock()
    tool.auth_level = auth_level
    tool.get_json_schema.return_value = {"type": "function", "function": {"name": name}}
    return tool


def native_call(name, arguments, call_id=None):
    return {"id": call_id or f"call_{name}", "function": {"name": name, "arguments": arguments}}


class AgentProcessMessageBase(unittest.TestCase):
    native = True

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        patcher = patch.object(core.settings, "NATIVE_TOOL_CALLING_ENABLED", self.native)
        patcher.start()
        self.addCleanup(patcher.stop)
        workflow_manager = AgentWorkflowManager()
        workflow_manager.process_agent_output_for_workflow = AsyncMock(return_value=None)
        self.manager = MagicMock()
        self.manager.push_agent_status_update = AsyncMock()
        self.manager.workflow_manager = workflow_manager
        self.manager.tool_executor.tools = {
            "file_system": make_tool("file_system"),
            "project_management": make_tool("project_management"),
            "manage_team": make_tool("manage_team", auth_level="pm"),
        }
        self.manager.cycle_handler = MagicMock(spec=["request_state_pattern"])
        self.manager.cycle_handler.request_state_pattern = None
        self.agent = Agent(agent_config={"agent_id": "W1", "config": {
            "provider": "ollama-local-test", "model": "qwen3.5:9b", "persona": "Coder", "agent_type": AGENT_TYPE_WORKER}},
            llm_provider=None, manager=self.manager)
        self.agent.state = WORKER_STATE_WORK
        sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(sandbox.cleanup)
        self.agent.sandbox_path = Path(sandbox.name) / "agent_W1"

    def run_reply(self, *events):
        self.provider = FakeProvider(list(events))
        self.agent.llm_provider = self.provider

        async def collect():
            return [event async for event in self.agent.process_message(history_override=[])]
        return asyncio.run(collect())

    @staticmethod
    def of_type(events, event_type):
        return [event for event in events if event.get("type") == event_type]


class TestNativeToolCalls(AgentProcessMessageBase):

    def test_single_tool_call(self):
        events = self.run_reply({"type": "native_tool_calls", "tool_calls": [
            native_call("file_system", {"action": "write", "filepath": "a.py", "content": "x = 1"})]})
        requests = self.of_type(events, "tool_requests")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["calls"], [{"id": "call_file_system", "name": "file_system",
                                                  "arguments": {"action": "write", "filepath": "a.py", "content": "x = 1"}}])
        self.assertEqual(self.of_type(events, "final_response"), [])

    def test_multiple_calls_string_arguments_and_unknown_tool(self):
        events = self.run_reply({"type": "native_tool_calls", "tool_calls": [
            native_call("file_system", '{"action": "list"}'),
            native_call("no_such_tool", {}),
            native_call("project_management", {"action": "list_tasks"}),
        ]})
        requests = self.of_type(events, "tool_requests")
        self.assertEqual(len(requests), 1)
        self.assertEqual([(c["name"], c["arguments"]) for c in requests[0]["calls"]],
                         [("file_system", {"action": "list"}), ("project_management", {"action": "list_tasks"})])

    def test_request_state_comes_after_the_tool_requests(self):
        events = self.run_reply({"type": "native_tool_calls", "tool_calls": [
            native_call("file_system", {"action": "list"}),
            native_call("request_state", {"state": "worker_report", "task_id": "t-1"}),
        ]})
        self.assertEqual([e["type"] for e in events], ["tool_requests", "agent_state_change_requested"])
        self.assertEqual(events[1]["requested_state"], "worker_report")
        self.assertEqual(events[1]["task_id"], "t-1")

    def test_invalid_state_request_is_not_a_state_change(self):
        events = self.run_reply({"type": "native_tool_calls", "tool_calls": [
            native_call("request_state", {"state": "no_such_state"})]})
        self.assertEqual(self.of_type(events, "agent_state_change_requested"), [])

    def test_tools_offered_follow_auth_level_and_include_request_state(self):
        self.run_reply({"type": "response_chunk", "content": "Hello."})
        offered = [schema["function"]["name"] for schema in self.provider.calls[0]["tools"]]
        self.assertIn("file_system", offered)
        self.assertIn("project_management", offered)
        self.assertNotIn("manage_team", offered)  # a PM tool is not offered to a worker
        self.assertIn("request_state", offered)
        self.assertEqual(self.provider.calls[0]["model"], "qwen3.5:9b")


class TestTextReplies(AgentProcessMessageBase):

    def test_plain_text_is_a_final_response(self):
        events = self.run_reply({"type": "response_chunk", "content": "This is a final "},
                                {"type": "response_chunk", "content": "response without any tools."})
        finals = self.of_type(events, "final_response")
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]["content"], "This is a final response without any tools.")
        self.assertEqual(self.of_type(events, "agent_raw_response")[0]["content"],
                         "This is a final response without any tools.")
        self.assertEqual(len(self.of_type(events, "response_chunk")), 2)

    def test_think_block_is_a_thought_and_the_rest_the_response(self):
        events = self.run_reply({"type": "response_chunk", "content": "<think>Plan the work.</think>\nAll done."})
        self.assertEqual([e["content"] for e in self.of_type(events, "agent_thought")], ["Plan the work."])
        self.assertEqual([e["content"] for e in self.of_type(events, "final_response")], ["All done."])

    def test_provider_error_stops_processing(self):
        events = self.run_reply({"type": "response_chunk", "content": "partial"},
                                {"type": "error", "content": "connection lost"})
        errors = self.of_type(events, "error")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["content"], "[ollama-local-test Error] connection lost")
        self.assertEqual(self.of_type(events, "final_response"), [])

    def test_cut_off_reply_is_marked(self):
        self.run_reply({"type": "response_chunk", "content": "def main("},
                       {"type": "finish_info", "done_reason": "length"})
        self.assertTrue(self.agent._last_reply_cut_off)
        self.assertEqual(self.agent._consecutive_cut_off_replies, 1)
        self.run_reply({"type": "response_chunk", "content": "Done."}, {"type": "finish_info", "done_reason": "stop"})
        self.assertFalse(self.agent._last_reply_cut_off)
        self.assertEqual(self.agent._consecutive_cut_off_replies, 0)

    def test_reply_that_only_thought_switches_thinking_off_once(self):
        events = self.run_reply({"type": "finish_info", "done_reason": "stop", "thinking_chars": 900,
                                 "thinking_tail": "...next I write the file"})
        self.assertEqual(self.of_type(events, "final_response"), [])
        self.assertTrue(self.agent._think_off_next_call)
        self.assertEqual(self.agent._last_reply_thinking_tail, "...next I write the file")

    def test_workflow_result_ends_processing(self):
        result = WorkflowResult(success=True, message="ok", workflow_name="pm_project_kickoff", next_agent_state="pm_manage")
        self.manager.workflow_manager.process_agent_output_for_workflow = AsyncMock(return_value=result)
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # Round 2m: model_dump(), not the deprecated .dict()
            events = self.run_reply({"type": "response_chunk", "content": "{\"kickoff_plan\": {}}"})
        self.assertEqual(events[-1]["type"], "workflow_executed")
        self.assertEqual(events[-1]["result_data"]["next_agent_state"], "pm_manage")
        self.assertEqual(events[-1]["result_data"]["workflow_name"], "pm_project_kickoff")
        self.assertEqual(self.of_type(events, "final_response"), [])


class TestXmlToolCalls(AgentProcessMessageBase):
    native = False

    def parse_result(self, valid_calls=(), parsing_errors=()):
        return {"valid_calls": list(valid_calls), "parsing_errors": list(parsing_errors)}

    def test_single_xml_call(self):
        xml = "<file_system><action>list</action></file_system>"
        with patch.object(core, "find_and_parse_xml_tool_calls",
                          return_value=self.parse_result([("file_system", {"action": "list"}, (0, len(xml)))])):
            events = self.run_reply({"type": "response_chunk", "content": xml})
        self.assertIsNone(self.provider.calls[0]["tools"])
        requests = self.of_type(events, "tool_requests")
        self.assertEqual(len(requests), 1)
        self.assertEqual([(c["name"], c["arguments"]) for c in requests[0]["calls"]], [("file_system", {"action": "list"})])
        self.assertEqual(requests[0]["raw_assistant_response"], xml)

    def test_multiple_xml_calls_in_one_event(self):
        xml = "<file_system><action>list</action></file_system>\n<project_management><action>list_tasks</action></project_management>"
        calls = [("file_system", {"action": "list"}, (0, 1)), ("project_management", {"action": "list_tasks"}, (2, 3))]
        with patch.object(core, "find_and_parse_xml_tool_calls", return_value=self.parse_result(calls)):
            events = self.run_reply({"type": "response_chunk", "content": xml})
        requests = self.of_type(events, "tool_requests")
        self.assertEqual(len(requests), 1)
        self.assertEqual([c["name"] for c in requests[0]["calls"]], ["file_system", "project_management"])
        self.assertEqual(requests[0]["raw_assistant_response"], xml)

    def test_think_then_xml_call_parses_the_text_without_the_thought(self):
        xml = "<file_system><action>list</action></file_system>"
        reply = f"<think>I should list the files.</think>\n{xml}"
        with patch.object(core, "find_and_parse_xml_tool_calls",
                          return_value=self.parse_result([("file_system", {"action": "list"}, (0, len(xml)))])) as parse:
            events = self.run_reply({"type": "response_chunk", "content": reply})
        self.assertEqual([e["content"] for e in self.of_type(events, "agent_thought")], ["I should list the files."])
        parse.assert_called_once()
        self.assertEqual(parse.call_args.args[0], xml)
        requests = self.of_type(events, "tool_requests")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["raw_assistant_response"], reply)

    def test_parsing_error_is_a_malformed_tool_call(self):
        error = {"tool_name": "file_system", "error_message": "missing </file_system>", "xml_block": "<file_system>"}
        with patch.object(core, "find_and_parse_xml_tool_calls", return_value=self.parse_result(parsing_errors=[error])):
            events = self.run_reply({"type": "response_chunk", "content": "<file_system><action>list</action>"})
        malformed = self.of_type(events, "malformed_tool_call")
        self.assertEqual(len(malformed), 1)
        self.assertEqual(malformed[0]["error_message"], "missing </file_system>")
        self.assertEqual(self.of_type(events, "tool_requests"), [])


if __name__ == '__main__':
    unittest.main()
