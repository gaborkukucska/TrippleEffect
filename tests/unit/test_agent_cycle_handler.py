import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock, call
import logging

# Modules to be tested
from src.agents.cycle_handler import AgentCycleHandler
from src.agents.core import Agent
from src.agents.manager import AgentManager
from src.agents.interaction_handler import AgentInteractionHandler
from src.agents.constants import AGENT_STATUS_PROCESSING, AGENT_STATUS_IDLE
from src.llm_providers.base import ToolResultDict, MessageDict
from src.config.settings import Settings # To mock settings if needed by CycleHandler directly or context

# Disable most logging output for tests unless specifically testing logging
logging.disable(logging.CRITICAL)

class TestAgentCycleHandlerMultiTool(unittest.TestCase):

    def setUp(self):
        # Mock Manager and its components
        self.manager_mock = MagicMock(spec=AgentManager)
        self.manager_mock.db_manager = AsyncMock()
        self.manager_mock.performance_tracker = AsyncMock()
        self.manager_mock.workflow_manager = MagicMock()
        # CRITICAL FIX: Mock the return value of get_system_prompt to be a string
        self.manager_mock.workflow_manager.get_system_prompt.return_value = "Mock system prompt"
        self.manager_mock.settings = MagicMock(spec=Settings) # Settings for CycleContext
        self.manager_mock.settings.MAX_STREAM_RETRIES = 1 # Simplify retry logic for tests
        self.manager_mock.settings.RETRY_DELAY_SECONDS = 0.01
        self.manager_mock.current_session_db_id = 12345 # For CycleContext
        self.manager_mock.current_project = "test_project"
        self.manager_mock.current_session = "test_session"
        self.manager_mock.send_to_ui = AsyncMock()
        self.manager_mock.key_manager = AsyncMock() # If failover paths were taken

        # Mock Agent
        self.agent_mock = MagicMock(spec=Agent)
        self.agent_mock.agent_id = "test_multi_tool_agent"
        self.agent_mock.provider_name = "mock_provider"
        self.agent_mock.model = "mock_model"
        self.agent_mock.message_history = []
        # Corrected: process_message is a sync function returning an async generator, so use MagicMock.
        self.agent_mock.process_message = MagicMock() # This will yield events
        self.agent_mock.set_status = MagicMock() # To observe status changes
        # Add status and state attributes to the mock to prevent AttributeErrors from logging
        self.agent_mock.status = AGENT_STATUS_IDLE
        self.agent_mock.state = "test_state"
        # Initialize _failover_state if any error path in CycleHandler might access it
        self.agent_mock._failover_state = {} 
        self.agent_mock._failed_models_this_cycle = set()


        # Mock InteractionHandler
        self.interaction_handler_mock = MagicMock(spec=AgentInteractionHandler)
        self.interaction_handler_mock.execute_single_tool = AsyncMock()

        # Instantiate CycleHandler
        self.cycle_handler = AgentCycleHandler(self.manager_mock, self.interaction_handler_mock)

    async def _async_generator_from_list(self, item_list):
        for item in item_list:
            yield item

    def test_scenario_1_multiple_successful_tool_calls(self):
        # --- Setup Mocks ---
        tool_call_1 = {"id": "call_1", "name": "tool_A", "arguments": {"arg1": "val1"}}
        tool_call_2 = {"id": "call_2", "name": "tool_B", "arguments": {"arg2": "val2"}}
        
        tool_result_1_content = "Result from tool_A"
        tool_result_2_content = "Result from tool_B"

        # Mock agent.process_message to yield a tool_requests event
        tool_requests_event = {
            "type": "tool_requests",
            "calls": [tool_call_1, tool_call_2],
            "raw_assistant_response": "<tool_A>...</tool_A><tool_B>...</tool_B>"
        }
        self.agent_mock.process_message.return_value = self._async_generator_from_list([tool_requests_event])

        # Mock interaction_handler.execute_single_tool to return success for both
        self.interaction_handler_mock.execute_single_tool.side_effect = [
            {"call_id": "call_1", "name": "tool_A", "content": tool_result_1_content}, # Result for tool_A
            {"call_id": "call_2", "name": "tool_B", "content": tool_result_2_content}  # Result for tool_B
        ]

        # --- Run the cycle ---
        asyncio.run(self.cycle_handler.run_cycle(self.agent_mock, 0))

        # --- Assertions ---
        # 1. execute_single_tool called for each tool
        self.assertEqual(self.interaction_handler_mock.execute_single_tool.call_count, 2)
        self.interaction_handler_mock.execute_single_tool.assert_any_call(
            self.agent_mock, tool_call_1["id"], tool_call_1["name"], tool_call_1["arguments"], 
            self.manager_mock.current_project, self.manager_mock.current_session
        )
        self.interaction_handler_mock.execute_single_tool.assert_any_call(
            self.agent_mock, tool_call_2["id"], tool_call_2["name"], tool_call_2["arguments"],
            self.manager_mock.current_project, self.manager_mock.current_session
        )

        # 2. agent.message_history updated
        self.assertEqual(len(self.agent_mock.message_history), 3) # 1 assistant (tool_requests) + 2 tool results
        self.assertEqual(self.agent_mock.message_history[0]["role"], "assistant")
        self.assertEqual(self.agent_mock.message_history[0]["content"], tool_requests_event["raw_assistant_response"])
        self.assertEqual(self.agent_mock.message_history[1]["role"], "tool")
        self.assertEqual(self.agent_mock.message_history[1]["tool_call_id"], "call_1")
        self.assertEqual(self.agent_mock.message_history[1]["content"], tool_result_1_content)
        self.assertEqual(self.agent_mock.message_history[2]["role"], "tool")
        self.assertEqual(self.agent_mock.message_history[2]["tool_call_id"], "call_2")
        self.assertEqual(self.agent_mock.message_history[2]["content"], tool_result_2_content)

        # 3. context.needs_reactivation_after_cycle (indirectly via NextStepScheduler)
        # We check if schedule_cycle was called for the current agent due to needs_reactivation_after_cycle being true
        self.manager_mock.schedule_cycle.assert_called_with(self.agent_mock, 0) # Assuming retry_count 0 for next cycle

        # 4. UI and DB logging (check for calls with relevant content)
        self.assertEqual(self.manager_mock.send_to_ui.call_count, 2) # For 2 tool results
        self.manager_mock.send_to_ui.assert_any_call(unittest.mock.ANY) # Basic check
        # More specific check for UI calls if needed:
        ui_call_args_list = self.manager_mock.send_to_ui.call_args_list
        self.assertIn(tool_result_1_content, str(ui_call_args_list[0]))
        self.assertIn("tool_sequence", str(ui_call_args_list[0])) # Check for sequence info
        self.assertIn(tool_result_2_content, str(ui_call_args_list[1]))
        self.assertIn("tool_sequence", str(ui_call_args_list[1]))


        self.assertEqual(self.manager_mock.db_manager.log_interaction.call_count, 3) # 1 assistant, 2 tool results
        # Example for one DB log call for a tool result:
        self.manager_mock.db_manager.log_interaction.assert_any_call(
            session_id=self.manager_mock.current_session_db_id, 
            agent_id=self.agent_mock.agent_id, 
            role="tool", 
            content=tool_result_1_content,
            tool_results=[{"call_id": "call_1", "name": "tool_A", "content": tool_result_1_content}]
        )


    def test_scenario_2_one_tool_fails_others_succeed(self):
        tool_call_1 = {"id": "call_s1", "name": "tool_Success1", "arguments": {}}
        tool_call_fail = {"id": "call_f1", "name": "tool_Fail", "arguments": {}}
        tool_call_3 = {"id": "call_s2", "name": "tool_Success2", "arguments": {}}

        tool_result_s1_content = "Success 1"
        tool_result_fail_content = "[ToolError] Failed tool execution"
        tool_result_s2_content = "Success 2"

        tool_requests_event = {
            "type": "tool_requests",
            "calls": [tool_call_1, tool_call_fail, tool_call_3],
            "raw_assistant_response": "..." 
        }
        self.agent_mock.process_message.return_value = self._async_generator_from_list([tool_requests_event])

        self.interaction_handler_mock.execute_single_tool.side_effect = [
            {"call_id": "call_s1", "name": "tool_Success1", "content": tool_result_s1_content},
            {"call_id": "call_f1", "name": "tool_Fail", "content": tool_result_fail_content}, # Error result
            {"call_id": "call_s2", "name": "tool_Success2", "content": tool_result_s2_content}
        ]

        asyncio.run(self.cycle_handler.run_cycle(self.agent_mock, 0))

        # 1. All tools attempted
        self.assertEqual(self.interaction_handler_mock.execute_single_tool.call_count, 3)

        # 2. All results in history
        self.assertEqual(len(self.agent_mock.message_history), 4) # 1 assistant, 3 tool results
        self.assertEqual(self.agent_mock.message_history[1]["content"], tool_result_s1_content)
        self.assertEqual(self.agent_mock.message_history[2]["content"], tool_result_fail_content)
        self.assertEqual(self.agent_mock.message_history[3]["content"], tool_result_s2_content)
        
        # 3. Agent should still be reactivated to process results (including the error)
        self.manager_mock.schedule_cycle.assert_called_with(self.agent_mock, 0)

        # 4. UI and DB logging for all three results
        self.assertEqual(self.manager_mock.send_to_ui.call_count, 3)
        self.assertEqual(self.manager_mock.db_manager.log_interaction.call_count, 4) # 1 assistant, 3 tool results


    def test_scenario_3_tool_requests_event_empty_calls(self):
        tool_requests_event_empty = {
            "type": "tool_requests",
            "calls": [], # Empty list of calls
            "raw_assistant_response": "Assistant said something but no tools." 
        }
        self.agent_mock.process_message.return_value = self._async_generator_from_list([tool_requests_event_empty])

        asyncio.run(self.cycle_handler.run_cycle(self.agent_mock, 0))

        # 1. execute_single_tool should not be called
        self.interaction_handler_mock.execute_single_tool.assert_not_called()
        
        # 2. Message history should contain assistant's raw response
        self.assertEqual(len(self.agent_mock.message_history), 1)
        self.assertEqual(self.agent_mock.message_history[0]["role"], "assistant")
        self.assertEqual(self.agent_mock.message_history[0]["content"], tool_requests_event_empty["raw_assistant_response"])

        # 3. Needs reactivation? If no tools, agent effectively did nothing actionable this cycle.
        #    The current logic for context.needs_reactivation_after_cycle = True for tool_requests.
        #    This might be okay, as the agent might then respond with text.
        #    Let's assume it's still reactivated to potentially produce a final_response.
        self.manager_mock.schedule_cycle.assert_called_with(self.agent_mock, 0) 
        
        # 4. DB logging for assistant response
        self.manager_mock.db_manager.log_interaction.assert_called_once_with(
            session_id=self.manager_mock.current_session_db_id, 
            agent_id=self.agent_mock.agent_id, 
            role="assistant", 
            content=tool_requests_event_empty["raw_assistant_response"],
            tool_calls=[] # Should log the empty tool_calls list
        )


if __name__ == '__main__':
    unittest.main()
