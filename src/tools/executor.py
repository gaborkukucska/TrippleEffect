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
# --- Import the updated ManageTeamTool ---
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
        ManageTeamTool, # Ensure ManageTeamTool is listed
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
        # (Unchanged from previous step)
        """
        Formats tool schemas into an XML string suitable for LLM prompts.
        Reflects the latest parameter descriptions from tool classes.
        """
        if not self.tools:
            return "<!-- No tools available -->"

        root = ET.Element("tools")
        root.text = "\nYou have access to the following tools. Use the specified XML format to call them. Only one tool call per message.\n"

        # Sort tools alphabetically by name for consistent output
        sorted_tool_names = sorted(self.tools.keys())

        for tool_name in sorted_tool_names:
            tool = self.tools[tool_name]
            # Use the tool's get_schema() method which reads current attributes
            schema = tool.get_schema()
            tool_element = ET.SubElement(root, "tool")

            name_el = ET.SubElement(tool_element, "name")
            name_el.text = schema['name']

            desc_el = ET.SubElement(tool_element, "description")
            desc_el.text = schema['description'].strip()

            params_el = ET.SubElement(tool_element, "parameters")
            if schema['parameters']:
                 # Sort parameters for consistency
                 sorted_params = sorted(schema['parameters'], key=lambda p: p['name'])
                 for param_data in sorted_params: # Use param_data instead of param to avoid confusion
                     param_el = ET.SubElement(params_el, "parameter")
                     param_name = ET.SubElement(param_el, "name")
                     param_name.text = param_data['name']
                     param_type = ET.SubElement(param_el, "type")
                     param_type.text = param_data['type']
                     param_req = ET.SubElement(param_el, "required")
                     param_req.text = str(param_data.get('required', True)).lower()
                     param_desc = ET.SubElement(param_el, "description")
                     # Ensure description is read from the CURRENT schema data
                     param_desc.text = param_data['description'].strip()
            else:
                 params_el.text = "<!-- No parameters -->"

            # Add XML Usage Example
            usage_el = ET.SubElement(tool_element, "usage_example")
            usage_str = f"\n<{schema['name']}>\n"
            if schema['parameters']:
                 sorted_params_usage = sorted(schema['parameters'], key=lambda p: p['name'])
                 for param_data in sorted_params_usage:
                    usage_str += f"  <{param_data['name']}>...</{param_data['name']}>\n"
            else:
                 usage_str += f"  <!-- No parameters -->\n"
            usage_str += f"</{schema['name']}>\n"
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

        # Pretty print the XML
        ET.indent(root, space="  ")
        xml_string = ET.tostring(root, encoding='unicode', method='xml')

        # Combine with Markdown heading
        final_description = "# Tools Description (XML Format)\n\n" + xml_string
        # logger.debug(f"Generated Tool Descriptions XML:\n{final_description}") # Optional: Log generated XML
        return final_description


    # --- *** UPDATED Tool Execution *** ---
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

        Args:
            agent_id: The ID of the agent initiating the call.
            agent_sandbox_path: The sandbox path for the agent.
            tool_name: The name of the tool to execute.
            tool_args: The pre-parsed arguments dictionary for the tool.

        Returns:
            Any: The result of the tool execution.
                 - For ManageTeamTool: The dictionary signal {'status': 'success'|'error', ...}
                 - For other tools: A string result or error message.
                 Returns an error message string or dict if the tool/args are invalid.
        """
        tool = self.tools.get(tool_name)
        if not tool:
            error_msg = f"Error: Tool '{tool_name}' not found."
            logger.error(error_msg)
            # Return error in format appropriate for ManageTeamTool if it was the intended target
            if tool_name == ManageTeamTool.name:
                return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
            else:
                return error_msg

        logger.info(f"Executor: Executing tool '{tool_name}' for agent '{agent_id}' with raw args: {tool_args}")
        try:
            # --- Argument Validation (using tool.get_schema()) ---
            schema = tool.get_schema()
            validated_args = {}
            missing_required = []
            # Check parameters defined in the schema
            if schema.get('parameters'):
                for param_info in schema['parameters']:
                    param_name = param_info['name']
                    is_required = param_info.get('required', True)

                    if param_name in tool_args:
                        # Basic type check/conversion could be added here if needed
                        validated_args[param_name] = tool_args[param_name]
                    elif is_required:
                        missing_required.append(param_name)

            # Report missing required parameters
            if missing_required:
                error_msg = f"Error: Tool '{tool_name}' execution failed. Missing required parameters: {', '.join(missing_required)}"
                logger.error(error_msg)
                # Return error in appropriate format
                if tool_name == ManageTeamTool.name:
                    return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
                else:
                    return error_msg
            # --- End Argument Validation ---

            # --- Remove explicitly passed args from kwargs ---
            # These are passed directly to tool.execute, not via kwargs
            kwargs_for_tool = validated_args.copy() # Start with validated args
            kwargs_for_tool.pop('agent_id', None)
            kwargs_for_tool.pop('agent_sandbox_path', None)
            # --- End Removal ---

            # Execute with validated arguments
            result = await tool.execute(
                agent_id=agent_id, # Pass caller's ID explicitly
                agent_sandbox_path=agent_sandbox_path, # Pass sandbox explicitly
                **kwargs_for_tool # Pass the REST of the validated args
            )

            # --- Handle Result ---
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
            # Return appropriate error format
            if tool_name == ManageTeamTool.name:
                 return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
            else:
                 return error_msg
