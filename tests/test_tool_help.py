import asyncio
from src.tools.file_system import FileSystemTool
from src.tools.project_management import ProjectManagementTool
from src.tools.manage_team import ManageTeamTool
from src.tools.executor import ToolExecutor
from unittest.mock import MagicMock

async def test_help():
    print("Testing ToolExecutor Error Context Injection:")
    executor = ToolExecutor()
    executor._tool_map = {"manage_team": ManageTeamTool()}
    
    test_xml = """<manage_team>
    <action>create_agent</action>
    <persona>Test Persona</persona>
</manage_team>"""

    # We mock out ToolErrorHandler so we can observe what action_help was passed
    executor.tool_error_handler = MagicMock()
    
    agent_context = {"project_name": "TestProject"}
    await executor.execute_tools("agent_1", test_xml, agent_context)
    
    # Check if generate_enhanced_error_response was called with our created action help
    calls = executor.tool_error_handler.generate_enhanced_error_response.call_args_list
    if calls:
        kwargs = calls[0][1]
        action_help = kwargs.get('action_help', '')
        if action_help:
             print(f"\nCaptured action_help length: {len(action_help)}")
             print(f"Captured action_help sample: {action_help[:150]}...")
        else:
             print("No action_help captured.")
    else:
        print("generate_enhanced_error_response not called.")

asyncio.run(test_help())
