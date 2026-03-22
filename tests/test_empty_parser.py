from src.agents.agent_tool_parser import _parse_tool_call_json_blocks
import logging
from src.tools.base import BaseTool

class DummyTool(BaseTool):
    def __init__(self, name):
        super().__init__()
        self._name = name
    @property
    def name(self): return self._name
    def get_schema(self): return {"name": self.name}
    async def execute(self, **kwargs): return {}
    def get_detailed_usage(self): return ""

tools = {"manage_team": DummyTool("manage_team"), "project_management": DummyTool("project_management")}
processed_spans = set()
buffer = '<tool_call>\n{"name": "", "arguments": {}}\n</tool_call>'

calls, errors = _parse_tool_call_json_blocks(buffer, tools, processed_spans, "PM1")
print("Calls:", calls)
print("Errors:", errors)
