# START OF FILE src/tools/executor.py
import json
import re # Import re for XML parsing
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type
import xml.etree.ElementTree as ET # Import ElementTree for XML parsing

# Import BaseTool and specific tools
from src.tools.base import BaseTool
from src.tools.file_system import FileSystemTool
# Import other tools here as they are created, e.g.:
# from src.tools.web_search import WebSearchTool

class ToolExecutor:
    """
    Manages and executes available tools for agents.
    - Registers tools.
    - Provides schemas/descriptions of available tools in XML format.
    - Parses XML tool call requests from LLM responses.
    - Executes the requested tool within the agent's context (sandbox).
    """

    # --- Tool Registration ---
    AVAILABLE_TOOL_CLASSES: List[Type[BaseTool]] = [
        FileSystemTool,
        # Add other tool classes here, e.g., WebSearchTool
    ]

    def __init__(self):
        """Initializes the ToolExecutor and registers available tools."""
        self.tools: Dict[str, BaseTool] = {}
        self._register_available_tools()
        print(f"ToolExecutor initialized with tools: {list(self.tools.keys())}")

    def _register_available_tools(self):
        """Instantiates and registers tools defined in AVAILABLE_TOOL_CLASSES."""
        print("Registering available tools...")
        for tool_cls in self.AVAILABLE_TOOL_CLASSES:
            try:
                instance = tool_cls()
                if instance.name in self.tools:
                    print(f"Warning: Tool name conflict. '{instance.name}' already registered. Overwriting.")
                self.tools[instance.name] = instance
                print(f"  Registered tool: {instance.name}")
            except Exception as e:
                print(f"Error instantiating or registering tool {tool_cls.__name__}: {e}")

    def register_tool(self, tool_instance: BaseTool):
        """Manually registers a tool instance."""
        if not isinstance(tool_instance, BaseTool):
            print(f"Error: Cannot register object of type {type(tool_instance)}. Must be subclass of BaseTool.")
            return
        if tool_instance.name in self.tools:
            print(f"Warning: Tool name conflict. '{tool_instance.name}' already registered. Overwriting.")
        self.tools[tool_instance.name] = tool_instance
        print(f"Manually registered tool: {tool_instance.name}")

    # --- Tool Schema/Discovery ---

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Returns a list of schemas for all registered tools (still potentially useful internally)."""
        # Kept for potential internal use or future native tool support, but not used for the XML prompt.
        return [tool.get_schema() for tool in self.tools.values()]

    def get_formatted_tool_descriptions_xml(self) -> str:
        """Formats tool schemas into an XML string suitable for LLM prompts (Cline style)."""
        if not self.tools:
            return "No tools available."

        description = "# Tools\n\n" # Using Markdown heading for clarity in the prompt
        for tool in self.tools.values():
            schema = tool.get_schema()
            description += f"## {schema['name']}\n"
            description += f"Description: {schema['description']}\n"
            description += "Parameters:\n"
            if schema['parameters']:
                 for param in schema['parameters']:
                     req = "required" if param['required'] else "optional"
                     # Description format change for XML clarity
                     description += f"- {param['name']}: ({param['type']}, {req}) {param['description']}\n"
            else:
                 description += "- None\n"
            # XML Usage Example
            description += "Usage:\n"
            description += f"<{schema['name']}>\n"
            if schema['parameters']:
                for param in schema['parameters']:
                    description += f"<{param['name']}>{param['name']} value here</{param['name']}>\n"
            else:
                 description += f"<!-- No parameters for this tool -->\n"
            description += f"</{schema['name']}>\n\n"

        # Add general XML tool use instructions
        description += (
            "# Tool Use Formatting\n\n"
            "Tool use is formatted using XML-style tags. The tool name is enclosed in opening and closing tags, "
            "and each parameter is similarly enclosed within its own set of tags. Here's the structure:\n\n"
            "<tool_name>\n"
            "<parameter1_name>value1</parameter1_name>\n"
            "<parameter2_name>value2</parameter2_name>\n"
            "...\n"
            "</tool_name>\n\n"
            "For example:\n\n"
            "<file_system>\n"
            "<action>write</action>\n"
            "<filename>output.txt</filename>\n"
            "<content>This is the content to write.</content>\n"
            "</file_system>\n\n"
            "Always adhere to this format for the tool use to ensure proper parsing and execution. "
            "Only one tool call can be made per response message. Place the tool call XML block at the end of your response."
        )
        return description


    # --- Tool Call Parsing & Execution ---

    def parse_xml_tool_call(self, response_content: str) -> Optional[List[Tuple[str, Dict[str, Any]]]]:
        """
        Parses the LLM response content to find XML structures indicating tool calls.
        Looks for <tool_name>...</tool_name> blocks. Returns a list as providers might
        handle multiple calls differently, though the manager will likely process one.

        Args:
            response_content (str): The raw text response from the LLM.

        Returns:
            A list of tuples [(tool_name, tool_args), ...] if valid tool calls are found,
            otherwise None. Returns an empty list if XML is found but doesn't match known tools.
        """
        tool_calls = []
        # Basic regex to find potential XML blocks for known tools
        tool_pattern = rf"<({'|'.join(re.escape(name) for name in self.tools.keys())})>([\s\S]*?)</\1>"
        matches = re.findall(tool_pattern, response_content, re.IGNORECASE | re.DOTALL)

        if not matches:
            return None # No potential tool calls found

        for tool_name_match, inner_content in matches:
            # Find the tool case-insensitively, but use the registered case
            matched_tool_name_registered_case = next(
                (name for name in self.tools if name.lower() == tool_name_match.lower()),
                None
            )
            if not matched_tool_name_registered_case:
                print(f"Warning: Found XML tag <{tool_name_match}> but no matching tool is registered.")
                continue # Skip this potential call

            tool_args = {}
            # Regex to extract parameters within the tool block
            param_pattern = r"<(\w+)>([\s\S]*?)</\1>"
            param_matches = re.findall(param_pattern, inner_content, re.DOTALL)

            for param_name, param_value in param_matches:
                 # Basic unescaping (might need more robust XML unescaping if values can be complex)
                tool_args[param_name] = param_value.strip().replace('<', '<').replace('>', '>').replace('&', '&')

            # Basic validation (check if required params are present)
            tool_schema = self.tools[matched_tool_name_registered_case].get_schema()
            missing_required = []
            if tool_schema and tool_schema.get('parameters'):
                for param_info in tool_schema['parameters']:
                    if param_info.get('required') and param_info['name'] not in tool_args:
                        missing_required.append(param_info['name'])

            if missing_required:
                print(f"Warning: Skipping tool call for '{matched_tool_name_registered_case}'. Missing required parameters: {', '.join(missing_required)}")
                continue # Skip this call due to missing required args

            print(f"Parsed XML tool call: Name='{matched_tool_name_registered_case}', Args={tool_args}")
            tool_calls.append((matched_tool_name_registered_case, tool_args))

        # Return the list of parsed calls (could be empty if validation failed)
        # Return None only if no initial tool pattern was found at all.
        return tool_calls


    # execute_tool remains largely the same
    async def execute_tool(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        tool_name: str,
        tool_args: Dict[str, Any]
    ) -> str:
        """
        Executes the specified tool with the given arguments.

        Args:
            agent_id: The ID of the agent initiating the call.
            agent_sandbox_path: The sandbox path for the agent.
            tool_name: The name of the tool to execute.
            tool_args: The arguments for the tool.

        Returns:
            str: The result of the tool execution (should be a string or serializable).
                 Returns an error message string if execution fails.
        """
        tool = self.tools.get(tool_name)
        if not tool:
            return f"Error: Tool '{tool_name}' not found."

        print(f"Executing tool '{tool_name}' for agent '{agent_id}' with args: {tool_args}")
        try:
            result = await tool.execute(
                agent_id=agent_id,
                agent_sandbox_path=agent_sandbox_path,
                **tool_args
            )
            # Ensure result is string
            if not isinstance(result, str):
                 try:
                     # Attempt JSON serialization for complex types, fallback to str()
                     result_str = json.dumps(result, indent=2)
                 except TypeError:
                     result_str = str(result)
            else:
                 result_str = result

            print(f"Tool '{tool_name}' execution result (first 100 chars): {result_str[:100]}...")
            return result_str

        except Exception as e:
            error_msg = f"Error executing tool '{tool_name}': {type(e).__name__} - {e}"
            print(error_msg)
            # Consider logging the full traceback here for debugging
            # import traceback
            # traceback.print_exc()
            return error_msg
