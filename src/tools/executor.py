# START OF FILE src/tools/executor.py
import json
import re
import importlib
import inspect
import time
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type
import xml.etree.ElementTree as ET
import html
import logging

from src.tools.base import BaseTool
from src.tools.manage_team import ManageTeamTool
from src.agents.constants import AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER
from src.tools.project_management import ProjectManagementTool
from src.api.websocket_manager import broadcast


logger = logging.getLogger(__name__)

class ToolExecutor:
    def __init__(self):
        self.tools: Dict[str, BaseTool] = {}
        self._register_available_tools()
        
        # Tool execution robustness settings
        self._max_retry_attempts = 3
        self._retry_delay_seconds = 1.0
        self._execution_stats = {
            "total_attempts": 0,
            "successful_executions": 0, 
            "failed_executions": 0,
            "retried_executions": 0,
            "fallback_used": 0
        }
        
        if not self.tools:
             logger.warning("ToolExecutor initialized, but no tools were discovered or registered.")
        else:
             logger.info(f"ToolExecutor initialized with dynamically discovered tools: {list(self.tools.keys())}")
             logger.info(f"Tool execution robustness enabled: max_retries={self._max_retry_attempts}, retry_delay={self._retry_delay_seconds}s")

    def _register_available_tools(self):
        logger.info("Dynamically discovering and registering tools...")
        tools_dir = Path(__file__).parent
        package_name = "src.tools" 

        for filepath in tools_dir.glob("*.py"):
            module_name_local = filepath.stem
            if module_name_local.startswith("_") or module_name_local == "base" or module_name_local == "tool_parser":
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
        if not isinstance(tool_instance, BaseTool):
            logger.error(f"Error: Cannot register object of type {type(tool_instance)}. Must be subclass of BaseTool.")
            return
        if tool_instance.name in self.tools:
            logger.warning(f"Tool name conflict during manual registration: '{tool_instance.name}' already registered. Overwriting.")
        self.tools[tool_instance.name] = tool_instance
        logger.info(f"Manually registered tool: {tool_instance.name}")

    def get_formatted_tool_descriptions_xml(self) -> str:
        if not self.tools:
            return "<!-- No tools available -->"
        root = ET.Element("tools")
        root.text = "\nYou have access to the following tools. Use the specified XML format to call them. ONLY ONE tool call per response message, placed at the very end.\n"
        sorted_tool_names = sorted(list(self.tools.keys()))
        for tool_name in sorted_tool_names:
            tool = self.tools[tool_name]
            schema = tool.get_schema()
            tool_element = ET.SubElement(root, "tool")
            name_el = ET.SubElement(tool_element, "name")
            name_el.text = schema['name']
            desc_el = ET.SubElement(tool_element, "description")
            desc_el.text = schema['description'].strip()
            if schema.get('summary') and schema['summary'] != schema['description']:
                summary_el = ET.SubElement(tool_element, "summary")
                summary_el.text = schema['summary'].strip()
            auth_el = ET.SubElement(tool_element, "auth_level")
            auth_el.text = schema.get('auth_level', 'worker') 

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
            
            usage_example_str = "<!-- Example not available -->"
            if hasattr(tool, 'get_detailed_usage') and callable(tool.get_detailed_usage):
                try:
                    detailed_usage = tool.get_detailed_usage()
                    example_match = re.search(r"```xml\s*(<"+re.escape(schema['name'])+r">[\s\S]*?</"+re.escape(schema['name'])+r">)\s*```", detailed_usage, re.DOTALL)
                    if example_match:
                        usage_example_str = example_match.group(1)
                    else: 
                        usage_example_str = f"\n<{schema['name']}>\n  <!-- Refer to 'get_tool_info' for parameter details -->\n</{schema['name']}>\n"
                except Exception as e_usage:
                    logger.warning(f"Could not generate usage example for tool {schema['name']}: {e_usage}")
            
            usage_el = ET.SubElement(tool_element, "usage_example")
            usage_el.text = f"<![CDATA[{usage_example_str}]]>"

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

    def get_formatted_tool_descriptions_json(self) -> str:
        if not self.tools:
            return json.dumps({"tools": [], "error": "No tools available"}, indent=2)

        tool_list = []
        sorted_tool_names = sorted(list(self.tools.keys()))
        for tool_name in sorted_tool_names:
            tool = self.tools[tool_name]
            schema = tool.get_schema()
            tool_info = {
                "name": schema['name'],
                "description": schema['description'].strip(),
                "summary": schema.get('summary', schema['description'].strip()), 
                "auth_level": schema.get('auth_level', 'worker'), 
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
            json_string = json.dumps(final_json_structure, indent=2, ensure_ascii=False)
            return json_string
        except Exception as e:
            logger.error(f"Error formatting tool descriptions as JSON: {e}", exc_info=True)
            return json.dumps({"tools": [], "error": f"Failed to format tools: {e}"}, indent=2)

    def get_available_tools_list_str(self, agent_type: str) -> str:
        if not self.tools:
            return "No tools are currently available."

        authorized_tools_summary = []
        all_tool_names = sorted(list(self.tools.keys()))

        for name in all_tool_names:
            tool_instance = self.tools.get(name)
            if not tool_instance: continue

            tool_auth_level = getattr(tool_instance, 'auth_level', 'worker')
            is_authorized = False
            if agent_type == AGENT_TYPE_ADMIN: is_authorized = True
            elif agent_type == AGENT_TYPE_PM: is_authorized = tool_auth_level in [AGENT_TYPE_PM, AGENT_TYPE_WORKER]
            elif agent_type == AGENT_TYPE_WORKER: is_authorized = tool_auth_level == AGENT_TYPE_WORKER

            if is_authorized:
                summary = getattr(tool_instance, 'summary', None) or tool_instance.description
                authorized_tools_summary.append(f"- {name}: {summary.strip()}")
        
        if not authorized_tools_summary:
            return f"No tools are accessible for your agent type ({agent_type})."
        return f"Tools available to you (Agent Type: {agent_type}):\n" + "\n".join(authorized_tools_summary)

    def _update_execution_stats(self, success: bool, retried: bool = False, fallback_used: bool = False):
        """Update internal execution statistics"""
        self._execution_stats["total_attempts"] += 1
        if success:
            self._execution_stats["successful_executions"] += 1
        else:
            self._execution_stats["failed_executions"] += 1
        if retried:
            self._execution_stats["retried_executions"] += 1
        if fallback_used:
            self._execution_stats["fallback_used"] += 1

    def _get_execution_stats(self) -> Dict[str, Any]:
        """Get current execution statistics"""
        stats = self._execution_stats.copy()
        if stats["total_attempts"] > 0:
            stats["success_rate"] = round((stats["successful_executions"] / stats["total_attempts"]) * 100, 2)
        else:
            stats["success_rate"] = 0.0
        return stats

    def report_execution_stats(self):
        """Report tool execution statistics to logs"""
        stats = self._get_execution_stats()
        if stats["total_attempts"] > 0:
            logger.info(f"Tool Execution Statistics - "
                       f"Total: {stats['total_attempts']}, "
                       f"Successful: {stats['successful_executions']}, "
                       f"Failed: {stats['failed_executions']}, "
                       f"Retried: {stats['retried_executions']}, "
                       f"Fallback Used: {stats['fallback_used']}, "
                       f"Success Rate: {stats['success_rate']}%")
        else:
            logger.info("Tool Execution Statistics - No executions recorded yet")
        return stats

    async def _execute_tool_with_retry(
        self,
        tool: BaseTool,
        execute_args: Dict[str, Any],
        tool_name: str,
        agent_id: str,
        execution_id: str
    ) -> Tuple[Any, bool, str]:
        """
        Execute a tool with retry logic and enhanced error handling
        Returns: (result, success, error_message)
        """
        last_error = None
        
        for attempt in range(self._max_retry_attempts):
            try:
                logger.debug(f"[TOOL_EXEC_ATTEMPT] ID:{execution_id} | Attempt:{attempt+1}/{self._max_retry_attempts}")
                result = await tool.execute(**execute_args)
                
                # Check if result indicates an error for certain tools
                if isinstance(result, dict) and result.get("status") == "error":
                    error_msg = result.get("message", "Tool returned error status")
                    logger.warning(f"[TOOL_EXEC_ERROR] ID:{execution_id} | Attempt:{attempt+1} | Tool error: {error_msg}")
                    last_error = error_msg
                    
                    # For certain recoverable errors, try again
                    if self._is_recoverable_error(error_msg) and attempt < self._max_retry_attempts - 1:
                        await asyncio.sleep(self._retry_delay_seconds * (attempt + 1))  # Exponential backoff
                        continue
                    else:
                        return result, False, error_msg
                
                # Success case
                logger.info(f"[TOOL_EXEC_SUCCESS] ID:{execution_id} | Attempt:{attempt+1}")
                return result, True, ""
                
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                logger.warning(f"[TOOL_EXEC_EXCEPTION] ID:{execution_id} | Attempt:{attempt+1} | {error_msg}")
                last_error = error_msg
                
                # Check if this is a recoverable error
                if self._is_recoverable_error(error_msg) and attempt < self._max_retry_attempts - 1:
                    logger.info(f"[TOOL_EXEC_RETRY] ID:{execution_id} | Retrying in {self._retry_delay_seconds * (attempt + 1)}s")
                    await asyncio.sleep(self._retry_delay_seconds * (attempt + 1))  # Exponential backoff
                    continue
                else:
                    # Non-recoverable error or max attempts reached
                    break
        
        # All attempts failed
        final_error = f"Tool execution failed after {self._max_retry_attempts} attempts. Last error: {last_error}"
        logger.error(f"[TOOL_EXEC_FAILED] ID:{execution_id} | {final_error}")
        return None, False, final_error

    def _is_recoverable_error(self, error_msg: str) -> bool:
        """Determine if an error is recoverable and worth retrying"""
        recoverable_patterns = [
            "timeout",
            "connection",
            "network",
            "temporary",
            "rate limit",
            "server error",
            "503",
            "502",
            "500"
        ]
        error_lower = error_msg.lower()
        return any(pattern in error_lower for pattern in recoverable_patterns)

    def _generate_fallback_response(self, tool_name: str, tool_args: Dict[str, Any], error_msg: str) -> Any:
        """Generate a fallback response when tool execution fails completely"""
        fallback_response = {
            "status": "error",
            "message": f"Tool '{tool_name}' execution failed: {error_msg}",
            "fallback_response": True,
            "suggested_action": "Please check your parameters and try again, or use an alternative approach."
        }
        
        # Add tool-specific fallback information
        if tool_name == "file_system":
            fallback_response["suggested_action"] = "File operation failed. Verify the file path exists and you have proper permissions."
        elif tool_name == "web_search":
            fallback_response["suggested_action"] = "Web search failed. Check your internet connection or try a different search query."
        elif tool_name == "project_management":
            fallback_response["suggested_action"] = "Project management operation failed. Verify task IDs and project state."
        
        return fallback_response

    async def execute_tool(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        tool_name: str,
        tool_args: Dict[str, Any], 
        project_name: Optional[str] = None, 
        session_name: Optional[str] = None,  
        manager: Optional[Any] = None # Type hint as 'AgentManager' if possible, else Any
    ) -> Any:
        # Enhanced logging for tool execution lifecycle
        execution_id = f"{agent_id}_{tool_name}_{hash(str(tool_args))}_{int(time.time())}"[-12:]
        logger.info(f"[TOOL_EXEC_START] ID:{execution_id} | Tool:'{tool_name}' | Agent:'{agent_id}' | Args:{tool_args}")
        
        # Emit WebSocket event for tool execution start
        try:
            await broadcast(json.dumps({
                "type": "tool_execution_start",
                "agent_id": agent_id,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "execution_id": execution_id,
                "timestamp": time.time()
            }))
        except Exception as e:
            logger.warning(f"Failed to broadcast tool_execution_start event: {e}")
        
        tool = self.tools.get(tool_name)
        if not tool:
            error_msg = f"Error: Tool '{tool_name}' not found. Available tools: {list(self.tools.keys())}"
            logger.error(f"[TOOL_EXEC_ERROR] ID:{execution_id} | {error_msg}")
            self._update_execution_stats(success=False)
            if tool_name == ManageTeamTool.name: 
                return {"status": "error", "action": tool_args.get("action"), "message": error_msg, "execution_id": execution_id}
            else:
                return f"{error_msg} [ID:{execution_id}]"

        original_state = None
        current_agent_instance_for_tool_call = None 

        # --- Authorization Check Section ---
        is_authorized = False
        agent_type_for_auth = "unknown" 
        tool_auth_level = getattr(tool, 'auth_level', 'worker') 

        if agent_id == "framework": 
            is_authorized = True
            agent_type_for_auth = "framework" 
            logger.debug(f"ToolExecutor: Allowing tool '{tool_name}' for internal framework call by '{agent_id}'.")
        elif manager and hasattr(manager, 'agents') and isinstance(manager.agents, dict):
            # --- MORE DETAILED LOGGING FOR THE MANAGER AND AGENTS DICT ITSELF ---
            logger.critical(f"ToolExecutor: Auth: Manager object received by execute_tool: id={id(manager)}, type={type(manager)}")
            logger.critical(f"ToolExecutor: Auth: manager.agents dictionary ID: {id(manager.agents)}")
            logger.critical(f"ToolExecutor: Auth: Attempting lookup for agent_id='{agent_id}' (type: {type(agent_id)}, repr: {repr(agent_id)})")
            current_manager_agents_keys = list(manager.agents.keys()) # Snapshot before get
            logger.critical(f"ToolExecutor: Auth: Keys in manager.agents ({len(current_manager_agents_keys)}) BEFORE get: {current_manager_agents_keys}")
            
            current_agent_instance_for_tool_call = manager.agents.get(agent_id) # THE LOOKUP
            
            is_key_present_after_get = agent_id in manager.agents # Check again after get, though unlikely to change
            logger.critical(f"ToolExecutor: Auth: Is agent_id='{agent_id}' in manager.agents keys AFTER get? {is_key_present_after_get}")
            # --- END MORE DETAILED LOGGING ---
            
            if current_agent_instance_for_tool_call:
                original_state = current_agent_instance_for_tool_call.state
                agent_type_for_auth = getattr(current_agent_instance_for_tool_call, 'agent_type', AGENT_TYPE_WORKER) 
                logger.critical(f"ToolExecutor: Auth: Found agent instance for '{agent_id}'. Original state: '{original_state}', Determined Type for Auth: '{agent_type_for_auth}', Tool auth level: '{tool_auth_level}'")

                if agent_type_for_auth == AGENT_TYPE_ADMIN:
                    is_authorized = True 
                    logger.critical(f"ToolExecutor: Auth: ADMIN agent '{agent_id}' authorized for tool '{tool_name}'")
                elif agent_type_for_auth == AGENT_TYPE_PM:
                    is_authorized = tool_auth_level in [AGENT_TYPE_PM, AGENT_TYPE_WORKER]
                    logger.critical(f"ToolExecutor: Auth: PM agent '{agent_id}' authorization check: tool_auth_level='{tool_auth_level}' in ['pm', 'worker'] = {is_authorized}")
                elif agent_type_for_auth == AGENT_TYPE_WORKER:
                    is_authorized = tool_auth_level == AGENT_TYPE_WORKER
                    logger.critical(f"ToolExecutor: Auth: WORKER agent '{agent_id}' authorization check: tool_auth_level='{tool_auth_level}' == 'worker' = {is_authorized}")
                else: 
                    logger.error(f"ToolExecutor: Auth: Unknown agent type '{agent_type_for_auth}' for agent '{agent_id}'. Denying tool use.")
            else:
                logger.error(f"ToolExecutor: Auth: Agent instance for '{agent_id}' NOT FOUND in manager.agents dict. Available agents: {list(manager.agents.keys())}")
        else:
            logger.error(f"ToolExecutor: Auth: Manager object missing (is None: {manager is None}), or manager.agents not a dict (hasattr: {hasattr(manager, 'agents')}, isinstance: {isinstance(getattr(manager, 'agents', None), dict) if manager else 'N/A'}). Cannot perform authorization.")
        # --- End Authorization Check Section ---

        if not is_authorized:
            error_msg = f"AUTHORIZATION FAILED: Agent '{agent_id}' (type: {agent_type_for_auth}) is not authorized to use tool '{tool_name}' (required level: {tool_auth_level})."
            logger.error(f"[TOOL_EXEC_ERROR] ID:{execution_id} | {error_msg}")
            self._update_execution_stats(success=False)
            if tool_name == ManageTeamTool.name:
                return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
            else:
                return error_msg
        
        logger.info(f"Executor: Executing tool '{tool_name}' for agent '{agent_id}' (Type: {agent_type_for_auth}, Auth Level: {tool_auth_level}) with args: {tool_args} (Project: {project_name}, Session: {session_name})")
        try:
            # --- Handle parameter aliases and action corrections before validation ---
            if tool_name == 'file_system':
                # Handle parameter aliases
                if 'filepath' in tool_args and 'filename' not in tool_args:
                    logger.debug(f"Found 'filepath' alias for 'file_system' tool. Mapping to 'filename'.")
                    tool_args['filename'] = tool_args['filepath']
                
                # Handle common action name mistakes with automatic correction
                action = tool_args.get('action')
                action_corrections = {
                    'create_directory': 'mkdir',
                    'create_file': 'write',
                    'create': 'write',
                    'make_directory': 'mkdir',
                    'make_dir': 'mkdir',
                    'new_file': 'write',
                    'save_file': 'write',
                    'save': 'write'
                }
                
                if action in action_corrections:
                    corrected_action = action_corrections[action]
                    logger.info(f"Auto-correcting file_system action '{action}' to '{corrected_action}' for agent {agent_id}")
                    tool_args['action'] = corrected_action

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
                error_msg = f"Error: Tool '{tool_name}' execution failed. Missing required parameters: {', '.join(missing_required)}. Please check the tool's documentation for required parameters."
                logger.error(error_msg)
                if tool_name == ManageTeamTool.name:
                    return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
                else:
                    return error_msg

            for param_name, param_value in tool_args.items(): 
                param_info = next((p for p in schema['parameters'] if p['name'] == param_name), None)
                if param_info:
                    expected_type = param_info['type']
                    if expected_type == 'integer':
                        if not isinstance(param_value, int):
                            try: validated_args[param_name] = int(param_value)
                            except (ValueError, TypeError):
                                error_msg = f"Error: Tool '{tool_name}' parameter '{param_name}' expects an integer, but received {type(param_value).__name__} ('{param_value}') and could not convert."
                                logger.error(error_msg); return error_msg
                    elif expected_type == 'boolean':
                        if not isinstance(param_value, bool):
                            if isinstance(param_value, str) and param_value.lower() in ['true', 'false']:
                                validated_args[param_name] = param_value.lower() == 'true'
                            else:
                                error_msg = f"Error: Tool '{tool_name}' parameter '{param_name}' expects a boolean (true/false), but received {type(param_value).__name__} ('{param_value}')."
                                logger.error(error_msg); return error_msg
                    elif expected_type == 'float':
                         if not isinstance(param_value, (int, float)): 
                            try: validated_args[param_name] = float(param_value)
                            except (ValueError, TypeError):
                                error_msg = f"Error: Tool '{tool_name}' parameter '{param_name}' expects a float, but received {type(param_value).__name__} ('{param_value}') and could not convert."
                                logger.error(error_msg); return error_msg
                    elif expected_type == 'list' and not isinstance(param_value, list):
                        logger.warning(f"Tool '{tool_name}' parameter '{param_name}' expects a list, but received {type(param_value).__name__}. Tool should handle parsing if it's a string representation of a list.")
            
            kwargs_for_tool = validated_args.copy()
            kwargs_for_tool.pop('agent_id', None) 
            kwargs_for_tool.pop('agent_sandbox_path', None)
            kwargs_for_tool.pop('project_name', None)
            kwargs_for_tool.pop('session_name', None)
            kwargs_for_tool.pop('manager', None) 

            execute_args = {
                "agent_id": agent_id, 
                "agent_sandbox_path": agent_sandbox_path,
                "project_name": project_name,
                "session_name": session_name,
                **kwargs_for_tool 
            }

            if tool_name == "system_help" or tool_name == "tool_information":
                if manager: execute_args["manager"] = manager
                else:
                    error_msg = f"Error: Internal configuration error - manager instance missing for '{tool_name}' tool."
                    logger.error(f"ToolExecutor: {error_msg}")
                    return error_msg
            
            # Use the robust retry mechanism
            result, success, error_message = await self._execute_tool_with_retry(
                tool=tool,
                execute_args=execute_args,
                tool_name=tool_name,
                agent_id=agent_id,
                execution_id=execution_id
            )
            
            # Update statistics
            retry_was_used = self._execution_stats["retried_executions"] > 0
            self._update_execution_stats(success=success, retried=retry_was_used)
            
            # Handle state restoration
            if original_state and current_agent_instance_for_tool_call and \
               hasattr(current_agent_instance_for_tool_call, 'state') and \
               current_agent_instance_for_tool_call.state != original_state:
                logger.debug(f"ToolExecutor: Restoring original state '{original_state}' for agent '{agent_id}' after tool execution.")
                current_agent_instance_for_tool_call.state = original_state
            
            # Handle failure case with fallback
            if not success:
                logger.error(f"[TOOL_EXEC_FAILED] ID:{execution_id} | {error_message}")
                
                # Emit WebSocket event for failed tool execution
                try:
                    await broadcast(json.dumps({
                        "type": "tool_execution_complete",
                        "agent_id": agent_id,
                        "tool_name": tool_name,
                        "execution_id": execution_id,
                        "success": False,
                        "error_message": error_message[:200],  # Truncate for event
                        "timestamp": time.time()
                    }))
                except Exception as e:
                    logger.warning(f"Failed to broadcast tool_execution_complete (failure) event: {e}")
                
                # Generate fallback response
                fallback_response = self._generate_fallback_response(tool_name, tool_args, error_message)
                self._update_execution_stats(success=False, fallback_used=True)
                
                if tool_name == ManageTeamTool.name:
                    fallback_response["action"] = tool_args.get("action", "unknown_action")
                    fallback_response["execution_id"] = execution_id
                    return fallback_response
                elif tool_name == ProjectManagementTool.name:
                    fallback_response["action"] = tool_args.get("action", "unknown_action")
                    return fallback_response
                else:
                    return f"[Tool Execution Failed] {error_message} [ID:{execution_id}] - {fallback_response['suggested_action']}"

            # Handle success case - format result appropriately
            logger.info(f"[TOOL_EXEC_COMPLETE] ID:{execution_id} | Success: {success}")
            
            # Emit WebSocket event for successful tool execution completion
            try:
                # Prepare result summary for the event
                if tool_name == ManageTeamTool.name or tool_name == ProjectManagementTool.name:
                    result_summary = json.dumps(result, default=str)[:200] if isinstance(result, dict) else str(result)[:200]
                else:
                    if not isinstance(result, str):
                        try: 
                            result_summary = json.dumps(result, indent=2)[:200]
                        except TypeError: 
                            result_summary = str(result)[:200]
                    else: 
                        result_summary = result[:200]
                
                await broadcast(json.dumps({
                    "type": "tool_execution_complete",
                    "agent_id": agent_id,
                    "tool_name": tool_name,
                    "execution_id": execution_id,
                    "success": True,
                    "result_summary": result_summary,
                    "timestamp": time.time()
                }))
            except Exception as e:
                logger.warning(f"Failed to broadcast tool_execution_complete event: {e}")
            
            if tool_name == ManageTeamTool.name or tool_name == ProjectManagementTool.name:
                if not isinstance(result, dict):
                    logger.error(f"{tool_name} execution returned unexpected type: {type(result)}. Expected dict.")
                    action_taken = tool_args.get("action", "unknown_action")
                    return {"status": "error", "action": action_taken, "message": f"Internal Error: {tool_name} returned unexpected type {type(result)}."}
                logger.info(f"Executor: Tool '{tool_name}' execution returned result: {json.dumps(result, default=str)[:200]}...")
                return result
            else:
                # Handle structured (dict) vs. simple (str) tool results for all other tools
                if isinstance(result, dict) and 'status' in result:
                    # This is a structured response from a tool like FileSystemTool
                    if result['status'] == 'success':
                        # Prefer 'message' for user-facing confirmation, fallback to 'content'
                        result_str = result.get('message', result.get('content', 'Tool execution was successful.'))
                        if not isinstance(result_str, str):
                            result_str = json.dumps(result_str, indent=2)
                    else: # status == 'error'
                        result_str = f"Error from tool '{tool_name}': {result.get('message', 'An unspecified error occurred.')}"
                    logger.info(f"Executor: Tool '{tool_name}' successful. Structured result: {result_str[:150]}...")
                    return result_str
                elif isinstance(result, str):
                    # This is a simple string response from a tool like SendMessageTool
                    logger.info(f"Executor: Tool '{tool_name}' successful. Simple result: {result[:150]}...")
                    return result
                else:
                    # Fallback for unexpected result types
                    try:
                        result_str = json.dumps(result, indent=2)
                    except TypeError:
                        result_str = str(result)
                    logger.info(f"Executor: Tool '{tool_name}' execution successful. Result (stringified, first 100 chars): {result_str[:100]}...")
                    return result_str

        except Exception as e: 
            error_msg = f"Executor: Critical error executing tool '{tool_name}': {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)
            self._update_execution_stats(success=False)
            
            # Restore state on critical error
            if original_state and current_agent_instance_for_tool_call and \
               hasattr(current_agent_instance_for_tool_call, 'state'): 
                logger.debug(f"ToolExecutor: Restoring original state '{original_state}' after critical error for agent '{agent_id}'")
                current_agent_instance_for_tool_call.state = original_state
            
            if tool_name == ManageTeamTool.name or tool_name == ProjectManagementTool.name: 
                 return {"status": "error", "action": tool_args.get("action"), "message": error_msg}
            else:
                 return error_msg
