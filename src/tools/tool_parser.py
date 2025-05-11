# START OF FILE src/tools/tool_parser.py
import xml.etree.ElementTree as ET
from typing import Dict, Any

def parse_tool_call(tool_call_xml: str) -> Dict[str, Any]:
    try:
        root = ET.fromstring(tool_call_xml)
        tool_name = root.tag
        tool_args = {}
        for child in root:
            tool_args[child.tag] = child.text
        return {"tool_name": tool_name, "parameters": tool_args}
    except ET.ParseError as e:
        # Attempt to handle minor formatting issues
        try:
            # Remove any text after the closing tool tag
            closing_tag_index = tool_call_xml.rfind('</' + tool_name + '>')
            if closing_tag_index != -1:
                tool_call_xml = tool_call_xml[:closing_tag_index + len('</' + tool_name + '>')]
            root = ET.fromstring(tool_call_xml)
            tool_name = root.tag
            tool_args = {}
            for child in root:
                tool_args[child.tag] = child.text
            return {"tool_name": tool_name, "parameters": tool_args}
        except Exception as e:
            return {"error": f"Failed to parse tool call: {str(e)}"}

# Example usage:
# tool_call_xml = "<tool_name><param1>value1</param1></tool_name>"
# print(parse_tool_call(tool_call_xml))
