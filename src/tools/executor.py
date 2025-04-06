# START OF FILE src/tools/executor.py
import json
import re # Keep re for potential future use, though less critical now
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type
import xml.etree.ElementTree as ET # Use ElementTree for robust parsing
import html # For unescaping potentially escaped values in XML
import logging # Added logging

# Import BaseTool and specific tools
from src.tools.base import BaseTool
from src.tools.file_system import FileSystemTool
from src.tools.send_message import SendMessageTool
# --- Import the new ManageTeamTool ---
from src.tools.manage_team import ManageTeamTool


logger = logging.getLogger(__name__)

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
        SendMessageTool,
        ManageTeamTool, # <-- Added ManageTeamTool here
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
                    logger.warning(f"Tool name conflict. '{instance.name}' already registered. Overwriting.")
                self.tools[instance.name] = instance
                logger.info(f"  Registered tool: {instance.name}")
            except Exception as e:
                logger.error(f"Error instantiating or registering tool {tool_cls.__name__}: {e}", exc_info=True)

    def register_tool(self, tool_instance: BaseTool):
        """Manually registers a tool instance."""
        if not isinstance(tool_instance, BaseTool):
            logger.error(f"Error: Cannot register object of type {type(tool_instance)}. Must be subclass of BaseTool.")
            return
        if tool_instance.name in self.tools:
            logger.warning(f"Tool name conflict. '{tool_instance.name}' already registered. Overwriting.")
        self.tools[tool_instance.name] = tool_instance
        logger.info(f"Manually registered tool: {tool_instance.name}")

    # --- Tool Schema/Discovery ---

    def get_formatted_tool_descriptions_xml(self) -> str:
        """Formats tool schemas into an XML string suitable for LLM prompts (Cline style)."""
        if not self.tools:
            return "<!-- No tools available -->" # Use XML comment for clarity

        # Using a more structured XML approach for the description itself
        root = ET.Element("tools")
        root.text = "\nYou have access to the following tools. Use the specified XML format to call them. Only one tool call per message.\n"

        # Sort tools alphabetically by name for consistent output
        sorted_tool_names = sorted(self.tools.keys())

        for tool_name in sorted_tool_names:
            tool = self.tools[tool_name]
            schema = tool.get_schema()
            tool_element = ET.SubElement(root, "tool")

            name_el = ET.SubElement(tool_element, "name")
            name_el.text = schema['name']

            desc_el = ET.SubElement(tool_element, "description")
            desc_el.text = schema['description'].strip() # Ensure no leading/trailing whitespace

            params_el = ET.SubElement(tool_element, "parameters")
            if schema['parameters']:
                 # Sort parameters for consistency
                 sorted_params = sorted(schema['parameters'], key=lambda p: p['name'])
                 for param in sorted_params:
                     param_el = ET.SubElement(params_el, "parameter")
                     param_name = ET.SubElement(param_el, "name")
                     param_name.text = param['name']
                     param_type = ET.SubElement(param_el, "type")
                     param_type.text = param['type']
                     param_req = ET.SubElement(param_el, "required")
                     # Use .get() for safety, default to True if missing for some reason
                     param_req.text = str(param.get('required', True)).lower()
                     param_desc = ET.SubElement(param_el, "description")
                     param_desc.text = param['description'].strip()
            else:
                 params_el.text = "<!-- No parameters -->"

            # Add XML Usage Example within the description block using CDATA
            usage_el = ET.SubElement(tool_element, "usage_example")
            usage_str = f"\n<{schema['name']}>\n"
            if schema['parameters']:
                 # Use sorted parameters in example too
                 sorted_params_usage = sorted(schema['parameters'], key=lambda p: p['name'])
                 for param in sorted_params_usage:
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


    # --- Tool Execution ---

    async def execute_tool(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        tool_name: str,
        tool_args: Dict[str, Any] # Arguments are now parsed by the Agent Core
    ) -> Any: # Return type changed to Any, as ManageTeamTool returns dict
        """
        Executes the specified tool with the given arguments. Arguments are pre-parsed.
        For ManageTeamTool, it returns the structured dictionary result directly.
        For other tools, it ensures the result is a string.

        Args:
            agent_id: The ID of the agent initiating the call.
            agent_sandbox_path: The sandbox path for the agent.
            tool_name: The name of the tool to execute.
            tool_args: The pre-parsed arguments dictionary for the tool.

        Returns:
            Any: The result of the tool execution.
                 - For ManageTeamTool: The dictionary signal {'status': 'success'|'error', ...}
                 - For other tools: A string result or error message.
                 Returns an error message string if the tool itself is not found.
        """
        tool = self.tools.get(tool_name)
        if not tool:
            error_msg = f"Error: Tool '{tool_name}' not found."
            logger.error(error_msg)
            return error_msg # Return string for tool not found

        logger.info(f"Executor: Executing tool '{tool_name}' for agent '{agent_id}' with args: {tool_args}")
        try:
            # --- Argument Validation (Schema-based) ---
            schema = tool.get_schema()
            validated_args = {}
            missing_required = []
            if schema.get('parameters'):
                param_map = {p['name']: p for p in schema['parameters']}
                for param_info in schema['parameters']:
                    param_name = param_info['name']
                    is_required = param_info.get('required', True)
                    if param_name in tool_args:
                        validated_args[param_name] = tool_args[param_name]
                    elif is_required:
                        missing_required.append(param_name)

            if missing_required:
                error_msg = f"Error: Tool '{tool_name}' execution failed. Missing required parameters: {', '.join(missing_required)}"
                logger.error(error_msg)
                # If ManageTeamTool, return structured error, otherwise string
                if tool_name == ManageTeamTool.name:
                    return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
                else:
                    return error_msg
            # --- End Argument Validation ---

            # Execute with validated arguments
            result = await tool.execute(
                agent_id=agent_id,
                agent_sandbox_path=agent_sandbox_path,
                **validated_args # Use validated args
            )

            # --- Handle Result ---
            # If it's the ManageTeamTool, return the result dictionary directly
            if tool_name == ManageTeamTool.name:
                 logger.info(f"Executor: Tool '{tool_name}' execution successful. Result: {result}")
                 return result
            else:
                 # For other tools, ensure result is string
                 if not isinstance(result, str):
                     try: result_str = json.dumps(result, indent=2)
                     except TypeError: result_str = str(result)
                 else:
                     result_str = result
                 logger.info(f"Executor: Tool '{tool_name}' execution successful. Result (stringified, first 100 chars): {result_str[:100]}...")
                 return result_str

        except Exception as e:
            error_msg = f"Executor: Error executing tool '{tool_name}': {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            # Return appropriate error format
            if tool_name == ManageTeamTool.name:
                 return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
            else:
                 return error_msg
