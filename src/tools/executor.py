# START OF FILE src/tools/executor.py
import json
import re
import importlib # For dynamic imports
import inspect   # For inspecting modules/classes
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type
import xml.etree.ElementTree as ET
import html
import logging

# Import BaseTool ONLY (specific tools are now discovered)
from src.tools.base import BaseTool
# Need ManageTeamTool name for special result handling
from src.tools.manage_team import ManageTeamTool

logger = logging.getLogger(__name__)

class ToolExecutor:
    """
    Manages and executes available tools for agents.
    - Dynamically discovers tools in the 'src/tools' directory.
    - Provides schemas/descriptions of available tools in XML format for prompts.
    - Executes the requested tool based on parsed name and arguments.
    """

    # --- Tool Registration (Dynamic Discovery) ---
    # AVAILABLE_TOOL_CLASSES list is REMOVED

    def __init__(self):
        """Initializes the ToolExecutor and dynamically discovers/registers available tools."""
        self.tools: Dict[str, BaseTool] = {}
        self._register_available_tools() # Call the dynamic registration method
        # Log confirmation after registration attempts
        if not self.tools:
             logger.warning("ToolExecutor initialized, but no tools were discovered or registered.")
        else:
             logger.info(f"ToolExecutor initialized with dynamically discovered tools: {list(self.tools.keys())}")

    def _register_available_tools(self):
        """Dynamically scans the 'src/tools' directory, imports modules,
           and registers classes inheriting from BaseTool."""
        logger.info("Dynamically discovering and registering tools...")
        tools_dir = Path(__file__).parent # Directory of this file (src/tools)
        package_name = "src.tools"       # Base package for imports

        for filepath in tools_dir.glob("*.py"):
            module_name_local = filepath.stem # e.g., "web_search"

            # Skip special files
            if module_name_local.startswith("_") or module_name_local == "base":
                logger.debug(f"Skipping module: {module_name_local}")
                continue

            module_name_full = f"{package_name}.{module_name_local}"
            logger.debug(f"Attempting to import module: {module_name_full}")

            try:
                # Dynamically import the module
                module = importlib.import_module(module_name_full)

                # Inspect the imported module for classes
                for name, cls in inspect.getmembers(module, inspect.isclass):
                    # Check conditions:
                    # 1. Is it a subclass of BaseTool?
                    # 2. Is it NOT BaseTool itself?
                    # 3. Was the class *defined* in this specific module (not imported)?
                    if (issubclass(cls, BaseTool) and
                            cls is not BaseTool and
                            cls.__module__ == module_name_full):

                        logger.debug(f"  Found potential tool class: {name} in {module_name_full}")
                        try:
                            # Instantiate the tool class
                            instance = cls()
                            # Check for name conflicts before registering
                            if instance.name in self.tools:
                                logger.warning(f"  Tool name conflict: '{instance.name}' from {module_name_full} already registered (likely from {self.tools[instance.name].__class__.__module__}). Overwriting.")
                            self.tools[instance.name] = instance
                            logger.info(f"  Registered tool: '{instance.name}' (from {module_name_local}.py)")
                        except Exception as e:
                            logger.error(f"  Error instantiating tool class {cls.__name__} from {module_name_full}: {e}", exc_info=True)

            except ImportError as e:
                logger.error(f"Error importing module {module_name_full}: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Unexpected error processing module {module_name_full}: {e}", exc_info=True)

        # Log is now done in __init__ after this method completes


    def register_tool(self, tool_instance: BaseTool):
        """Manually registers a tool instance (useful for testing or non-standard tools)."""
        if not isinstance(tool_instance, BaseTool):
            logger.error(f"Error: Cannot register object of type {type(tool_instance)}. Must be subclass of BaseTool.")
            return
        if tool_instance.name in self.tools:
            logger.warning(f"Tool name conflict during manual registration: '{tool_instance.name}' already registered. Overwriting.")
        self.tools[tool_instance.name] = tool_instance
        logger.info(f"Manually registered tool: {tool_instance.name}")

    # --- Tool Schema/Discovery (Unchanged) ---
    def get_formatted_tool_descriptions_xml(self) -> str:
        """
        Formats tool schemas into an XML string suitable for LLM prompts.
        Reflects the latest parameter descriptions from tool classes.
        """
        if not self.tools:
            return "<!-- No tools available -->"

        root = ET.Element("tools")
        root.text = "\nYou have access to the following tools. Use the specified XML format to call them. ONLY ONE tool call per response message, placed at the very end.\n"
        sorted_tool_names = sorted(self.tools.keys())

        for tool_name in sorted_tool_names:
            tool = self.tools[tool_name]
            schema = tool.get_schema()
            tool_element = ET.SubElement(root, "tool")
            name_el = ET.SubElement(tool_element, "name")
            name_el.text = schema['name']
            desc_el = ET.SubElement(tool_element, "description")
            desc_el.text = schema['description'].strip()
            params_el = ET.SubElement(tool_element, "parameters")
            if schema['parameters']:
                 sorted_params = sorted(schema['parameters'], key=lambda p: p['name'])
                 for param_data in sorted_params:
                     param_el = ET.SubElement(params_el, "parameter")
                     param_name = ET.SubElement(param_el, "name")
                     param_name.text = param_data['name']
                     param_type = ET.SubElement(param_el, "type")
                     param_type.text = param_data['type']
                     param_req = ET.SubElement(param_el, "required")
                     param_req.text = str(param_data.get('required', True)).lower()
                     param_desc = ET.SubElement(param_el, "description")
                     param_desc.text = param_data['description'].strip()
            else:
                 params_el.text = "<!-- This tool takes no parameters -->"
            usage_el = ET.SubElement(tool_element, "usage_example")
            usage_str = f"\n<{schema['name']}>\n"
            if schema['parameters']:
                 sorted_params_usage = sorted(schema['parameters'], key=lambda p: p['name'])
                 for param_data in sorted_params_usage:
                    placeholder = "..."
                    if param_data['type'] == 'integer': placeholder = "123"
                    elif param_data['type'] == 'boolean': placeholder = "true"
                    elif param_data['type'] == 'float': placeholder = "1.23"
                    usage_str += f"  <{param_data['name']}>{placeholder}</{param_data['name']}>\n"
            else:
                 usage_str += f"  <!-- No parameters needed -->\n"
            usage_str += f"</{schema['name']}>\n"
            usage_el.text = f"<![CDATA[{usage_str}]]>"

        instructions_el = ET.SubElement(root, "general_instructions")
        instructions_el.text = (
            "\nTool Call Format Guidance:\n"
            "1. Enclose your SINGLE tool call in XML tags matching the tool name.\n"
            "2. Place each parameter within its own correctly named tag inside the main tool tag.\n"
            "3. Ensure the parameter value is between the opening and closing parameter tags.\n"
            "4. Place the entire XML block at the **very end** of your response message.\n"
            "5. Do not include any text after the closing tool tag."
        )
        try:
             ET.indent(root, space="  ")
             xml_string = ET.tostring(root, encoding='unicode', method='xml')
        except Exception:
             xml_string = ET.tostring(root, encoding='unicode', method='xml')
        final_description = xml_string
        return final_description


    # --- Tool Execution (Unchanged) ---
    async def execute_tool(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        tool_name: str,
        tool_args: Dict[str, Any]
    ) -> Any:
        """
        Executes the specified tool with the given arguments. Arguments are pre-parsed.
        Validates arguments against the tool's schema.
        Returns raw dictionary result for ManageTeamTool, otherwise ensures string result.
        """
        tool = self.tools.get(tool_name)
        if not tool:
            error_msg = f"Error: Tool '{tool_name}' not found."
            logger.error(error_msg)
            # Special handling for ManageTeamTool error format
            if tool_name == ManageTeamTool.name: # Compare with imported name
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
                if tool_name == ManageTeamTool.name: # Compare with imported name
                    return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
                else:
                    return error_msg

            # Prepare kwargs for tool execution
            kwargs_for_tool = validated_args.copy()
            kwargs_for_tool.pop('agent_id', None)
            kwargs_for_tool.pop('agent_sandbox_path', None)

            # Execute tool
            result = await tool.execute(
                agent_id=agent_id,
                agent_sandbox_path=agent_sandbox_path,
                **kwargs_for_tool
            )

            # Handle Result Formatting
            if tool_name == ManageTeamTool.name: # Compare with imported name
                 if not isinstance(result, dict):
                      logger.error(f"ManageTeamTool execution returned unexpected type: {type(result)}. Expected dict.")
                      return {"status": "error", "action": tool_args.get("action"), "message": f"Internal Error: ManageTeamTool returned unexpected type {type(result)}."}
                 logger.info(f"Executor: Tool '{tool_name}' execution returned result: {result}")
                 return result
            else:
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
            if tool_name == ManageTeamTool.name: # Compare with imported name
                 return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
            else:
                 return error_msg
