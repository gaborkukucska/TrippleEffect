# START OF FILE src/tools/executor.py
import json
import re # Keep re for the initial block detection
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type
import xml.etree.ElementTree as ET # Use ElementTree for robust parsing
import html # For unescaping potentially escaped values in XML

# Import BaseTool and specific tools
from src.tools.base import BaseTool
from src.tools.file_system import FileSystemTool
# Import other tools here as they are created, e.g.:
# from src.tools.web_search import WebSearchTool

class ToolExecutor:
    """
    Manages and executes available tools for agents.
    - Registers tools.
    - Provides schemas/descriptions of available tools in XML format for prompts.
    - Executes the requested tool based on parsed name and arguments.
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

    def get_formatted_tool_descriptions_xml(self) -> str:
        """Formats tool schemas into an XML string suitable for LLM prompts (Cline style)."""
        if not self.tools:
            return "<!-- No tools available -->" # Use XML comment for clarity

        # Using a more structured XML approach for the description itself
        root = ET.Element("tools")
        root.text = "\nYou have access to the following tools. Use the specified XML format to call them. Only one tool call per message.\n"

        for tool in self.tools.values():
            schema = tool.get_schema()
            tool_element = ET.SubElement(root, "tool")

            name_el = ET.SubElement(tool_element, "name")
            name_el.text = schema['name']

            desc_el = ET.SubElement(tool_element, "description")
            desc_el.text = schema['description'].strip() # Ensure no leading/trailing whitespace

            params_el = ET.SubElement(tool_element, "parameters")
            if schema['parameters']:
                 for param in schema['parameters']:
                     param_el = ET.SubElement(params_el, "parameter")
                     param_name = ET.SubElement(param_el, "name")
                     param_name.text = param['name']
                     param_type = ET.SubElement(param_el, "type")
                     param_type.text = param['type']
                     param_req = ET.SubElement(param_el, "required")
                     param_req.text = str(param['required']).lower() # 'true' or 'false'
                     param_desc = ET.SubElement(param_el, "description")
                     param_desc.text = param['description'].strip()
            else:
                 params_el.text = "<!-- No parameters -->"

            # Add XML Usage Example within the description block using CDATA
            usage_el = ET.SubElement(tool_element, "usage_example")
            usage_str = f"\n<{schema['name']}>\n"
            if schema['parameters']:
                for param in schema['parameters']:
                    # Simple placeholder like <param>value</param>
                    usage_str += f"  <{param['name']}>...</{param['name']}>\n"
            else:
                 usage_str += f"  <!-- No parameters -->\n"
            usage_str += f"</{schema['name']}>\n"
            # Using CDATA helps prevent confusion if example includes XML-like chars
            usage_el.text = f"<![CDATA[{usage_str}]]>"


        # Add general XML tool use instructions outside the loop
        instructions_el = ET.SubElement(root, "general_instructions")
        instructions_el.text = (
            "\nTool Call Format:\n"
            "Enclose tool calls in XML tags matching the tool name. Place parameters inside their own tags within the tool call block.\n"
            "Example:\n"
            "<{tool_name}>\n"
            "  <{parameter1_name}>value1</{parameter1_name}>\n"
            "  <{parameter2_name}>value2</{parameter2_name}>\n"
            "</{tool_name}>\n"
            "Place the **entire** XML block for the single tool call at the **very end** of your message."
        )

        # Pretty print the XML for readability in the prompt
        ET.indent(root, space="  ")
        xml_string = ET.tostring(root, encoding='unicode', method='xml')

        # Combine with Markdown heading
        final_description = "# Tools Description (XML Format)\n\n" + xml_string
        return final_description


    # --- Tool Execution (no XML parsing needed here anymore) ---

    async def execute_tool(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        tool_name: str,
        tool_args: Dict[str, Any] # Arguments are now parsed by the Agent Core
    ) -> str:
        """
        Executes the specified tool with the given arguments. Arguments are pre-parsed.

        Args:
            agent_id: The ID of the agent initiating the call.
            agent_sandbox_path: The sandbox path for the agent.
            tool_name: The name of the tool to execute.
            tool_args: The pre-parsed arguments dictionary for the tool.

        Returns:
            str: The result of the tool execution (should be a string or serializable).
                 Returns an error message string if execution fails.
        """
        tool = self.tools.get(tool_name)
        if not tool:
            return f"Error: Tool '{tool_name}' not found."

        print(f"Executor: Executing tool '{tool_name}' for agent '{agent_id}' with args: {tool_args}")
        try:
            # --- Argument Validation (moved from parsing step) ---
            schema = tool.get_schema()
            validated_args = {}
            missing_required = []
            if schema.get('parameters'):
                param_map = {p['name']: p for p in schema['parameters']}
                for param_info in schema['parameters']:
                    param_name = param_info['name']
                    is_required = param_info.get('required', False)
                    if param_name in tool_args:
                        # Basic type check could be added here if needed, but Pydantic in BaseTool might handle it
                        validated_args[param_name] = tool_args[param_name]
                    elif is_required:
                        missing_required.append(param_name)

                # Check for unexpected arguments (optional)
                # for arg_name in tool_args:
                #     if arg_name not in param_map:
                #         print(f"Warning: Unexpected argument '{arg_name}' provided for tool '{tool_name}'")

            if missing_required:
                return f"Error: Tool '{tool_name}' execution failed. Missing required parameters: {', '.join(missing_required)}"
            # --- End Argument Validation ---


            # Execute with validated arguments
            result = await tool.execute(
                agent_id=agent_id,
                agent_sandbox_path=agent_sandbox_path,
                **validated_args # Use validated args
            )

            # Ensure result is string
            if not isinstance(result, str):
                 try:
                     result_str = json.dumps(result, indent=2)
                 except TypeError:
                     result_str = str(result)
            else:
                 result_str = result

            print(f"Executor: Tool '{tool_name}' execution result (first 100 chars): {result_str[:100]}...")
            return result_str

        except Exception as e:
            error_msg = f"Executor: Error executing tool '{tool_name}': {type(e).__name__} - {e}"
            print(error_msg)
            # import traceback # Uncomment for detailed debug logs
            # traceback.print_exc() # Uncomment for detailed debug logs
            return error_msg
