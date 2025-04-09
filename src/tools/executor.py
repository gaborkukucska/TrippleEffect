# START OF FILE src/tools/executor.py
import json
import re # Keep re for potential future use, though less critical now
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type
import xml.etree.ElementTree as ET # Use ElementTree for robust parsing
import html # For unescaping potentially escaped values in XML
import logging

# Import BaseTool and specific tools
from src.tools.base import BaseTool
from src.tools.file_system import FileSystemTool
from src.tools.send_message import SendMessageTool
from src.tools.manage_team import ManageTeamTool
from src.tools.web_search import WebSearchTool # *** ADD THIS IMPORT ***
# Import GitHubTool later when we create it

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
        ManageTeamTool,
        WebSearchTool, # *** ADD WebSearchTool HERE ***
        # Add GitHubTool later
    ]

    def __init__(self):
        """Initializes the ToolExecutor and registers available tools."""
        self.tools: Dict[str, BaseTool] = {}
        self._register_available_tools()
        logger.info(f"ToolExecutor initialized with tools: {list(self.tools.keys())}") # Use logger

    def _register_available_tools(self):
        """Instantiates and registers tools defined in AVAILABLE_TOOL_CLASSES."""
        logger.info("Registering available tools...") # Use logger
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
        """
        Formats tool schemas into an XML string suitable for LLM prompts.
        Reflects the latest parameter descriptions from tool classes.
        """
        if not self.tools:
            return "<!-- No tools available -->"

        root = ET.Element("tools")
        # Updated introductory text slightly
        root.text = "\nYou have access to the following tools. Use the specified XML format to call them. ONLY ONE tool call per response message, placed at the very end.\n"

        # Sort tools alphabetically by name for consistent prompt output
        sorted_tool_names = sorted(self.tools.keys())

        for tool_name in sorted_tool_names:
            tool = self.tools[tool_name]
            # Use the tool's get_schema() method which reads current attributes
            schema = tool.get_schema()
            tool_element = ET.SubElement(root, "tool")

            name_el = ET.SubElement(tool_element, "name")
            name_el.text = schema['name']

            desc_el = ET.SubElement(tool_element, "description")
            desc_el.text = schema['description'].strip() # Trim whitespace

            params_el = ET.SubElement(tool_element, "parameters")
            if schema['parameters']:
                 # Sort parameters for consistency within the tool definition
                 sorted_params = sorted(schema['parameters'], key=lambda p: p['name'])
                 for param_data in sorted_params:
                     param_el = ET.SubElement(params_el, "parameter")
                     param_name = ET.SubElement(param_el, "name")
                     param_name.text = param_data['name']
                     param_type = ET.SubElement(param_el, "type")
                     param_type.text = param_data['type']
                     param_req = ET.SubElement(param_el, "required")
                     param_req.text = str(param_data.get('required', True)).lower() # Default to true if missing
                     param_desc = ET.SubElement(param_el, "description")
                     # Ensure description is read from the CURRENT schema data
                     param_desc.text = param_data['description'].strip() # Trim whitespace
            else:
                 # Indicate no parameters clearly
                 params_el.text = "<!-- This tool takes no parameters -->"

            # Add XML Usage Example
            usage_el = ET.SubElement(tool_element, "usage_example")
            usage_str = f"\n<{schema['name']}>\n"
            if schema['parameters']:
                 # Use the same sorted list for the example
                 sorted_params_usage = sorted(schema['parameters'], key=lambda p: p['name'])
                 for param_data in sorted_params_usage:
                    # Add placeholder value based on type for clarity
                    placeholder = "..."
                    if param_data['type'] == 'integer': placeholder = "123"
                    elif param_data['type'] == 'boolean': placeholder = "true"
                    elif param_data['type'] == 'float': placeholder = "1.23"
                    usage_str += f"  <{param_data['name']}>{placeholder}</{param_data['name']}>\n"
            else:
                 usage_str += f"  <!-- No parameters needed -->\n" # Adjust example text
            usage_str += f"</{schema['name']}>\n"
            # Use CDATA to prevent XML parsing issues with the example content
            usage_el.text = f"<![CDATA[{usage_str}]]>"


        # Add general XML tool use instructions outside the loop (slight wording tweak)
        instructions_el = ET.SubElement(root, "general_instructions")
        instructions_el.text = (
            "\nTool Call Format Guidance:\n"
            "1. Enclose your SINGLE tool call in XML tags matching the tool name.\n"
            "2. Place each parameter within its own correctly named tag inside the main tool tag.\n"
            "3. Ensure the parameter value is between the opening and closing parameter tags.\n"
            "4. Place the entire XML block at the **very end** of your response message.\n"
            "5. Do not include any text after the closing tool tag."
        )

        # Pretty print the XML for readability in logs/debugging
        try:
             ET.indent(root, space="  ")
             xml_string = ET.tostring(root, encoding='unicode', method='xml')
        except Exception: # Fallback if indent fails
             xml_string = ET.tostring(root, encoding='unicode', method='xml')


        # Combine with Markdown heading (optional, depends if LLM prefers it)
        # final_description = "# Tools Description (XML Format)\n\n" + xml_string
        final_description = xml_string # Return raw XML for now
        # logger.debug(f"Generated Tool Descriptions XML:\n{final_description}") # Optional: Log generated XML
        return final_description


    # --- Tool Execution (No changes needed here) ---
    async def execute_tool(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        tool_name: str,
        tool_args: Dict[str, Any] # Arguments are pre-parsed by Agent Core
    ) -> Any:
        """
        Executes the specified tool with the given arguments. Arguments are pre-parsed.
        Validates arguments against the tool's schema.
        Removes agent_id and agent_sandbox_path from args before passing via kwargs.
        Returns raw dictionary result for ManageTeamTool, otherwise ensures string result.
        """
        tool = self.tools.get(tool_name)
        if not tool:
            error_msg = f"Error: Tool '{tool_name}' not found."
            logger.error(error_msg)
            if tool_name == ManageTeamTool.name:
                return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
            else:
                return error_msg

        logger.info(f"Executor: Executing tool '{tool_name}' for agent '{agent_id}' with raw args: {tool_args}")
        try:
            # Argument Validation
            schema = tool.get_schema()
            validated_args = {}
            missing_required = []
            if schema.get('parameters'):
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
                if tool_name == ManageTeamTool.name:
                    return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
                else:
                    return error_msg

            # Prepare kwargs for tool execution
            kwargs_for_tool = validated_args.copy()
            kwargs_for_tool.pop('agent_id', None) # Already passed directly
            kwargs_for_tool.pop('agent_sandbox_path', None) # Already passed directly

            # Execute tool
            result = await tool.execute(
                agent_id=agent_id,
                agent_sandbox_path=agent_sandbox_path,
                **kwargs_for_tool
            )

            # Handle Result Formatting
            if tool_name == ManageTeamTool.name:
                 if not isinstance(result, dict):
                      logger.error(f"ManageTeamTool execution returned unexpected type: {type(result)}. Expected dict.")
                      return {"status": "error", "action": tool_args.get("action"), "message": f"Internal Error: ManageTeamTool returned unexpected type {type(result)}."}
                 logger.info(f"Executor: Tool '{tool_name}' execution returned result: {result}")
                 return result # Return the dict directly
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
            if tool_name == ManageTeamTool.name:
                 return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
            else:
                 return error_msg
