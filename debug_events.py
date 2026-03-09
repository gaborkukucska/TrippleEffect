import asyncio
import unittest.mock
from tests.unit.test_agent_core import TestAgentProcessMessageToolCalls

async def main():
    t = TestAgentProcessMessageToolCalls('test_scenario_1_single_tool_call')
    t.setUp()
    tool_name = "tool_one"
    tool_args = {"param1": "value1"}
    raw_tool_call_xml = f"<{tool_name}><param1>value1</param1></{tool_name}>"

    llm_response_events = [
        {"type": "response_chunk", "content": raw_tool_call_xml}
    ]
    
    with unittest.mock.patch('src.agents.core.find_and_parse_xml_tool_calls') as mock_find_parse:
        mock_find_parse.return_value = {"valid_calls": [(tool_name, tool_args, raw_tool_call_xml)], "parsing_errors": []}
        events = await t.run_process_message_and_collect_events(llm_response_events)
        print("EVENTS YIELDED:")
        for event in events:
            print(event, "\n")
            if "_exception_obj" in event:
                import traceback
                traceback.print_exception(type(event["_exception_obj"]), event["_exception_obj"], event["_exception_obj"].__traceback__)

asyncio.run(main())
