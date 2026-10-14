import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock, call
import logging
import re

from src.agents.core import Agent, MessageDict
from src.llm_providers.base import BaseLLMProvider
from src.agents.manager import AgentManager
# Assuming other necessary imports like constants might be needed by Agent
from src.agents.constants import AGENT_STATUS_IDLE


# Disable most logging output for tests unless specifically testing logging
logging.disable(logging.CRITICAL)

class TestAgentProcessMessageToolCalls(unittest.TestCase):

    def setUp(self):
        # Mock dependencies for Agent
        self.mock_llm_provider = MagicMock(spec=BaseLLMProvider)
        self.mock_manager = MagicMock(spec=AgentManager)
        
        # Setup manager's sub-components
        self.mock_manager.tool_executor = MagicMock()
        self.mock_manager.tool_executor.tools = {
            "tool_one": MagicMock(), 
            "tool_two": MagicMock(),
            "another_tool": MagicMock() # For the think_then_tool test
        } 
        
        self.mock_manager.workflow_manager = MagicMock()
        self.mock_manager.workflow_manager.process_agent_output_for_workflow = AsyncMock(return_value=None) # No workflow trigger by default
        
        self.mock_manager.db_manager = AsyncMock() # For db logging if any path hits it
        self.mock_manager.cycle_handler = MagicMock() # For state request pattern if needed
        self.mock_manager.cycle_handler.request_state_pattern = re.compile(r"<request_state state='(\w+)'>")


        # Agent configuration
        self.agent_config = {
            "agent_id": "test_agent_xml_tools",
            "config": {
                "provider": "mock_provider",
                "model": "mock_model",
                "persona": "Test Persona for XML Tools",
                "agent_type": "worker"
            }
        }

        # Instantiate the Agent
        # The Agent's __init__ compiles tool patterns based on manager.tool_executor.tools
        self.agent = Agent(
            agent_config=self.agent_config,
            llm_provider=self.mock_llm_provider,
            manager=self.mock_manager
        )
        self.agent.status = AGENT_STATUS_IDLE # Ensure agent is in a runnable state

        # Ensure sandbox exists is handled or doesn't interfere
        self.agent.ensure_sandbox_exists = MagicMock(return_value=True)


    async def run_process_message_and_collect_events(self, stream_events: list):
        self.mock_llm_provider.stream_completion = AsyncMock(return_value=self._async_generator_from_list(stream_events))
        
        collected_events = []
        # The agent.process_message is an async generator
        async for event in self.agent.process_message(history_override=[]): # Pass empty history
            collected_events.append(event)
        return collected_events

    async def _async_generator_from_list(self, item_list):
        for item in item_list:
            yield item

    @patch('src.agents.core.find_and_parse_xml_tool_calls')
    def test_scenario_1_single_tool_call(self, mock_find_parse):
        tool_name = "tool_one"
        tool_args = {"param1": "value1"}
        raw_tool_call_xml = f"<{tool_name}><param1>value1</param1></{tool_name}>"
        
        # Mock LLM response
        llm_response_events = [
            {"type": "response_chunk", "content": raw_tool_call_xml}
        ]
        # Mock find_and_parse_xml_tool_calls
        mock_find_parse.return_value = [(tool_name, tool_args, raw_tool_call_xml)]

        events = asyncio.run(self.run_process_message_and_collect_events(llm_response_events))
        
        tool_requests_events = [e for e in events if e.get("type") == "tool_requests"]
        self.assertEqual(len(tool_requests_events), 1, "Should yield one tool_requests event.")
        
        tool_calls_list = tool_requests_events[0].get("calls", [])
        self.assertEqual(len(tool_calls_list), 1, "tool_requests should contain one call.")
        self.assertEqual(tool_calls_list[0]["name"], tool_name)
        self.assertEqual(tool_calls_list[0]["arguments"], tool_args)
        self.assertEqual(tool_requests_events[0].get("raw_assistant_response"), raw_tool_call_xml)

    @patch('src.agents.core.find_and_parse_xml_tool_calls')
    @patch('src.agents.core.logger.info') # Patch logger.info for the "Processing all" message
    @patch('src.agents.core.logger.debug') # Patch logger.debug for the "preparing to yield" message
    def test_scenario_2_multiple_tool_calls(self, mock_log_debug, mock_log_info, mock_find_parse):
        tool1_name = "tool_one"
        tool1_args = {"p1": "v1"}
        raw_tool1_xml = f"<{tool1_name}><p1>v1</p1></{tool1_name}>"
        
        tool2_name = "tool_two"
        tool2_args = {"p2": "v2"}
        raw_tool2_xml = f"<{tool2_name}><p2>v2</p2></{tool2_name}>"

        full_llm_response_content = f"{raw_tool1_xml}\n{raw_tool2_xml}"
        
        llm_response_events = [
            {"type": "response_chunk", "content": full_llm_response_content}
        ]
        # Mock find_and_parse_xml_tool_calls to return multiple calls
        mock_find_parse.return_value = [
            (tool1_name, tool1_args, raw_tool1_xml),
            (tool2_name, tool2_args, raw_tool2_xml)
        ]

        events = asyncio.run(self.run_process_message_and_collect_events(llm_response_events))

        tool_requests_events = [e for e in events if e.get("type") == "tool_requests"]
        self.assertEqual(len(tool_requests_events), 1, "Should yield one tool_requests event even for multiple raw calls.")
        
        tool_calls_list = tool_requests_events[0].get("calls", [])
        self.assertEqual(len(tool_calls_list), 2, "tool_requests should process all found calls.")
        
        # Check first tool call
        self.assertEqual(tool_calls_list[0]["name"], tool1_name)
        self.assertEqual(tool_calls_list[0]["arguments"], tool1_args)
        
        # Check second tool call
        self.assertEqual(tool_calls_list[1]["name"], tool2_name)
        self.assertEqual(tool_calls_list[1]["arguments"], tool2_args)
        
        self.assertEqual(tool_requests_events[0].get("raw_assistant_response"), full_llm_response_content)

        # Check logger.info for processing all
        self.assertTrue(mock_log_info.called)
        info_args, _ = mock_log_info.call_args
        self.assertIn("found 2 tool calls", info_args[0])
        self.assertIn("Processing all", info_args[0])

        # Check logger.debug for preparing to yield
        self.assertTrue(mock_log_debug.called)
        debug_args, _ = mock_log_debug.call_args
        self.assertIn("preparing to yield 2 tool requests", debug_args[0])


    @patch('src.agents.core.find_and_parse_xml_tool_calls')
    def test_scenario_3_no_tool_calls(self, mock_find_parse):
        plain_text_response = "This is a final response without any tools."
        llm_response_events = [
            {"type": "response_chunk", "content": plain_text_response}
        ]
        mock_find_parse.return_value = [] # No tools found

        events = asyncio.run(self.run_process_message_and_collect_events(llm_response_events))

        final_response_events = [e for e in events if e.get("type") == "final_response"]
        tool_requests_events = [e for e in events if e.get("type") == "tool_requests"]
        
        self.assertEqual(len(final_response_events), 1, "Should yield one final_response event.")
        self.assertEqual(final_response_events[0].get("content"), plain_text_response)
        self.assertEqual(len(tool_requests_events), 0, "Should not yield any tool_requests event.")

    @patch('src.agents.core.find_and_parse_xml_tool_calls')
    def test_scenario_4_think_tag_with_tool_call(self, mock_find_parse):
        think_content = "I should use another_tool for this."
        tool_name = "another_tool"
        tool_args = {"query": "specifics"}
        raw_tool_call_xml = f"<{tool_name}><query>specifics</query></{tool_name}>"
        
        # LLM response includes both <think> and a tool call
        # The agent first extracts <think>, then processes the rest for tools.
        full_llm_response_content = f"<think>{think_content}</think>\n{raw_tool_call_xml}"
        
        llm_response_events = [
            {"type": "response_chunk", "content": full_llm_response_content}
        ]
        
        # find_and_parse_xml_tool_calls will be called with the text *after* the think tag is removed.
        mock_find_parse.return_value = [(tool_name, tool_args, raw_tool_call_xml)]

        events = asyncio.run(self.run_process_message_and_collect_events(llm_response_events))

        thought_events = [e for e in events if e.get("type") == "agent_thought"]
        tool_requests_events = [e for e in events if e.get("type") == "tool_requests"]

        self.assertEqual(len(thought_events), 1, "Should yield one agent_thought event.")
        self.assertEqual(thought_events[0].get("content"), think_content)
        
        self.assertEqual(len(tool_requests_events), 1, "Should yield one tool_requests event after thought.")
        tool_calls_list = tool_requests_events[0].get("calls", [])
        self.assertEqual(len(tool_calls_list), 1)
        self.assertEqual(tool_calls_list[0]["name"], tool_name)
        self.assertEqual(tool_calls_list[0]["arguments"], tool_args)
        # The raw_assistant_response for tool_requests should be the original full response
        self.assertEqual(tool_requests_events[0].get("raw_assistant_response"), full_llm_response_content)

        # Ensure find_and_parse_xml_tool_calls was called with the correct remaining text
        # The agent's process_message internally strips the think tag before calling find_and_parse_xml_tool_calls
        mock_find_parse.assert_called_once()
        call_args_list = mock_find_parse.call_args_list
        args_passed_to_find_parse = call_args_list[0][0][0] # First arg of first call
        self.assertEqual(args_passed_to_find_parse.strip(), raw_tool_call_xml)


if __name__ == '__main__':
    unittest.main()
