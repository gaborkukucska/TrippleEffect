# START OF FILE src/tools/executor.py
import json
import re
import importlib
import inspect
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type
import xml.etree.ElementTree as ET
import html
import logging

from src.tools.base import BaseTool
from src.tools.manage_team import ManageTeamTool
# --- NEW: Import agent type constants ---
from src.agents.constants import AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER
# --- END NEW ---

logger = logging.getLogger(__name__)

class ToolExecutor:
    """
    Manages and executes available tools for agents.
    - Dynamically discovers tools in the 'src/tools' directory.
    - Provides schemas/descriptions of available tools in XML or JSON format for prompts.
    - Executes the requested tool, passing necessary context (agent ID, sandbox, project, session).
    """

    def __init__(self):
        """Initializes the ToolExecutor and dynamically discovers/registers available tools."""
        self.tools: Dict[str, BaseTool] = {}
        self._register_available_tools()
        if not self.tools:
             logger.warning("ToolExecutor initialized, but no tools were discovered or registered.")
        else:
             logger.info(f"ToolExecutor initialized with dynamically discovered tools: {list(self.tools.keys())}")

    def _register_available_tools(self):
        """Dynamically scans the 'src/tools' directory, imports modules,
           and registers classes inheriting from BaseTool."""
        logger.info("Dynamically discovering and registering tools...")
        tools_dir = Path(__file__).parent
        package_name = "src.tools"

        for filepath in tools_dir.glob("*.py"):
            module_name_local = filepath.stem
            if module_name_local.startswith("_") or module_name_local == "base":
                logger.debug(f"Skipping module: {module_name_local}")
                continue

            module_name_full = f"{package_name}.{module_name_local}"
            logger.debug(f"Attempting to import module: {module_name_full}")

            try:
                module = importlib.import_module(module_name_full)
                for name, cls in inspect.getmembers(module, inspect.isclass):
                    if (issubclass(cls, BaseTool) and
                            cls is not BaseTool and
                            cls.__module__ == module_name_full):
                        logger.debug(f"  Found potential tool class: {name} in {module_name_full}")
                        try:
                            instance = cls()
                            if instance.name in self.tools:
                                logger.warning(f"  Tool name conflict: '{instance.name}' from {module_name_full} already registered. Overwriting.")
                            self.tools[instance.name] = instance
                            logger.info(f"  Registered tool: '{instance.name}' (from {module_name_local}.py)")
                        except Exception as e:
                            logger.error(f"  Error instantiating tool class {cls.__name__} from {module_name_full}: {e}", exc_info=True)
            except ImportError as e:
                logger.error(f"Error importing module {module_name_full}: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Unexpected error processing module {module_name_full}: {e}", exc_info=True)


    def register_tool(self, tool_instance: BaseTool):
        """Manually registers a tool instance."""
        if not isinstance(tool_instance, BaseTool):
            logger.error(f"Error: Cannot register object of type {type(tool_instance)}. Must be subclass of BaseTool.")
            return
        if tool_instance.name in self.tools:
            logger.warning(f"Tool name conflict during manual registration: '{tool_instance.name}' already registered. Overwriting.")
        self.tools[tool_instance.name] = tool_instance
        logger.info(f"Manually registered tool: {tool_instance.name}")

    def get_formatted_tool_descriptions_xml(self) -> str:
        """Generates an XML string describing available tools and their parameters."""
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
             # Attempt to indent for readability
             ET.indent(root, space="  ")
             xml_string = ET.tostring(root, encoding='unicode', method='xml')
        except Exception:
             # Fallback if indent fails (e.g., older Python versions)
             xml_string = ET.tostring(root, encoding='unicode', method='xml')
        final_description = xml_string
        return final_description

    def get_formatted_tool_descriptions_json(self) -> str:
        """Generates a JSON string describing available tools and their parameters."""
        if not self.tools:
            return json.dumps({"tools": [], "error": "No tools available"}, indent=2)

        tool_list = []
        sorted_tool_names = sorted(self.tools.keys())
        for tool_name in sorted_tool_names:
            tool = self.tools[tool_name]
            schema = tool.get_schema()
            tool_info = {
                "name": schema['name'],
                "description": schema['description'].strip(),
                "parameters": []
            }
            if schema['parameters']:
                sorted_params = sorted(schema['parameters'], key=lambda p: p['name'])
                for param_data in sorted_params:
                    tool_info["parameters"].append({
                        "name": param_data['name'],
                        "type": param_data['type'],
                        "required": param_data.get('required', True),
                        "description": param_data['description'].strip()
                    })
            tool_list.append(tool_info)

        # Add general instructions within the JSON structure
        instructions = (
            "Tool Call Format Guidance:\n"
            "1. Enclose your SINGLE tool call in a ```json ... ``` code block.\n"
            "2. The JSON object must have a 'tool_name' key and a 'parameters' key.\n"
            "3. 'parameters' should be an object containing parameter names and values.\n"
            "4. Place the entire JSON block at the **very end** of your response message.\n"
            "5. Do not include any text after the closing ```."
        )

        final_json_structure = {
            "available_tools": tool_list,
            "general_instructions": instructions
        }

        try:
            # Use ensure_ascii=False for better readability if non-ASCII chars are present
            json_string = json.dumps(final_json_structure, indent=2, ensure_ascii=False)
            return json_string
        except Exception as e:
            logger.error(f"Error formatting tool descriptions as JSON: {e}", exc_info=True)
            # Fallback JSON
            return json.dumps({"tools": [], "error": f"Failed to format tools: {e}"}, indent=2)


    async def execute_tool(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        tool_name: str,
        tool_args: Dict[str, Any], # Arguments are pre-parsed by Agent Core
        project_name: Optional[str] = None, # Added context
        session_name: Optional[str] = None,  # Added context
        manager: Optional[Any] = None # Pass manager instance for specific tools like system_help
    ) -> Any:
        """
        Executes the specified tool with the given arguments and context.
        Arguments are pre-parsed. Validates arguments against the tool's schema.
        Passes context (project/session) to the tool's execute method.
        Returns raw dictionary result for ManageTeamTool, otherwise ensures string result.

        Args:
            agent_id: The ID of the agent initiating the call.
            agent_sandbox_path: The sandbox path for the agent.
            tool_name: The name of the tool to execute.
            tool_args: The pre-parsed arguments dictionary for the tool.
            project_name: Current project context.
            session_name: Current session context.

        Returns:
            Any: The result of the tool execution.
        """
        tool = self.tools.get(tool_name)
        if not tool:
            error_msg = f"Error: Tool '{tool_name}' not found."
            logger.error(error_msg)
            if tool_name == ManageTeamTool.name:
                return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
            else:
                return error_msg

        # --- NEW: Authorization Check ---
        is_authorized = False
        agent_type = "unknown" # Default agent type for logging if not framework or found agent
        tool_auth_level = getattr(tool, 'auth_level', 'worker') # Default to worker if missing

        # Explicitly allow framework calls
        if agent_id == "framework":
            is_authorized = True
            agent_type = "framework" # Set type for logging
            logger.debug(f"Executor: Allowing tool '{tool_name}' for internal framework call.")
        else:
            # Check agent-based authorization
            agent_instance = manager.agents.get(agent_id) if manager else None
            if not agent_instance:
                 # This might happen if agent was deleted mid-process
                 logger.warning(f"Executor: Could not find agent instance for '{agent_id}' during authorization check. Denying tool use.")
                 # is_authorized remains False
            else:
                 agent_type = getattr(agent_instance, 'agent_type', AGENT_TYPE_WORKER) # Get agent type, default to worker

                 # Perform authorization check based on agent type and tool level
                 if agent_type == AGENT_TYPE_ADMIN:
                     is_authorized = True # Admin can use any tool
                 elif agent_type == AGENT_TYPE_PM:
                     is_authorized = tool_auth_level in [AGENT_TYPE_PM, AGENT_TYPE_WORKER]
                 elif agent_type == AGENT_TYPE_WORKER:
                     is_authorized = tool_auth_level == AGENT_TYPE_WORKER
                 else: # Unknown agent type
                      logger.warning(f"Executor: Unknown agent type '{agent_type}' for agent '{agent_id}'. Denying tool use.")
                      # is_authorized remains False

        # Final check on authorization status before proceeding
        if not is_authorized:
            error_msg = f"Error: Agent '{agent_id}' (type: {agent_type}) is not authorized to use tool '{tool_name}' (required level: {tool_auth_level})."
            logger.error(error_msg)
            if tool_name == ManageTeamTool.name:
                return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
            else:
                return error_msg
        # --- END Authorization Check ---

        logger.info(f"Executor: Executing tool '{tool_name}' for agent '{agent_id}' (Type: {agent_type}, Auth Level: {tool_auth_level}) with args: {tool_args} (Project: {project_name}, Session: {session_name})")
        try:
            # Argument Validation (using tool.get_schema())
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

            # Prepare kwargs for tool execution (validated tool-specific args)
            kwargs_for_tool = validated_args.copy()
            # Remove context args if they happen to be in kwargs (they are passed directly)
            kwargs_for_tool.pop('agent_id', None)
            kwargs_for_tool.pop('agent_sandbox_path', None)
            kwargs_for_tool.pop('project_name', None)
            kwargs_for_tool.pop('session_name', None)
            kwargs_for_tool.pop('manager', None) # Ensure manager isn't passed via kwargs

            # Prepare arguments for the tool's execute method
            execute_args = {
                "agent_id": agent_id,
                "agent_sandbox_path": agent_sandbox_path,
                "project_name": project_name,
                "session_name": session_name,
                **kwargs_for_tool # Pass the validated tool-specific args
            }

            # --- Pass manager specifically to SystemHelpTool ---
            if tool_name == "system_help":
                if manager:
                    execute_args["manager"] = manager
                else:
                    logger.error("ToolExecutor: Manager instance not provided, cannot execute 'get_tool_info' in SystemHelpTool.")
                    return "Error: Internal configuration error - manager instance missing for system_help tool."
            # --- Pass manager to ToolInformationTool ---
            elif tool_name == "tool_information":
                if manager:
                    execute_args["manager"] = manager
                else:
                    logger.error("ToolExecutor: Manager instance not provided, cannot execute ToolInformationTool.")
                    return "Error: Internal configuration error - manager instance missing for tool_information tool."
            # --- End ToolInformationTool specific logic ---

            # Execute with validated arguments and context
            result = await tool.execute(**execute_args)
            # Removed duplicated call arguments below

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
