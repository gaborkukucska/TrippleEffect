# START OF FILE src/tools/executor.py
import json
import re # Regular expression for parsing potential JSON
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

# Import BaseTool and specific tools
from src.tools.base import BaseTool
from src.tools.file_system import FileSystemTool
# Import other tools here as they are created, e.g.:
# from src.tools.web_search import WebSearchTool

class ToolExecutor:
    """
    Manages and executes available tools for agents.
    - Registers tools.
    - Provides schemas of available tools.
    - Parses potential tool call requests from LLM responses (looking for specific JSON format).
    - Executes the requested tool within the agent's context (sandbox).
    """

    # --- Tool Registration ---
    # Define which tool classes are available
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
        """Returns a list of schemas for all registered tools."""
        return [tool.get_schema() for tool in self.tools.values()]

    def get_formatted_tool_descriptions(self) -> str:
        """Formats tool schemas into a string suitable for LLM prompts."""
        if not self.tools:
            return "No tools available."

        description = "Available Tools:\n"
        for tool in self.tools.values():
            schema = tool.get_schema()
            params_desc = "\n    Parameters:\n"
            if schema['parameters']:
                 for param in schema['parameters']:
                     req = "required" if param['required'] else "optional"
                     params_desc += f"      - {param['name']} ({param['type']}, {req}): {param['description']}\n"
            else:
                 params_desc += "      None\n"

            description += f"  - Tool Name: `{schema['name']}`\n"
            description += f"    Description: {schema['description']}\n"
            description += params_desc
            description += "\n" # Add a newline between tools

        # Add instructions on how to call the tool
        description += (
            "To use a tool, output a JSON block *only* in the following format within your response:\n"
            "```json\n"
            "{\n"
            '  "tool_call": {\n'
            '    "name": "tool_name",\n'
            '    "arguments": {\n'
            '      "param_name1": "value1",\n'
            '      "param_name2": value2\n'
            '    }\n'
            '  }\n'
            "}\n"
            "```\n"
            "Replace `tool_name` with the name of the tool you want to use (e.g., `file_system`).\n"
            "Replace the `arguments` dictionary with the parameters required by the tool.\n"
            "Ensure the JSON is valid. Do not include anything else before or after this JSON block if you are making a tool call."
        )
        return description

    # --- Tool Call Parsing & Execution ---

    def parse_tool_call(self, response_content: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Parses the LLM response content to find a specific JSON structure indicating a tool call.

        Looks for:
        ```json
        {
          "tool_call": {
            "name": "<tool_name>",
            "arguments": { <parameters> }
          }
        }
        ```
        It allows for potential leading/trailing whitespace but expects the JSON block
        to be the primary content if a tool call is intended.

        Args:
            response_content (str): The raw text response from the LLM.

        Returns:
            A tuple (tool_name, tool_args) if a valid tool call JSON is found, otherwise None.
        """
        # Regex to find a JSON block potentially enclosed in ```json ... ```
        # This is a simplified regex and might need refinement. It looks for '{' at the start
        # and '}' at the end, capturing everything in between non-greedily.
        # It also handles optional markdown code fences.
        match = re.search(r"```json\s*(\{.*?\})\s*```|(\{.*?\})", response_content.strip(), re.DOTALL)

        if not match:
            return None

        # Extract the JSON part (either from group 1 or 2)
        json_text = match.group(1) or match.group(2)

        try:
            data = json.loads(json_text)
            tool_call_data = data.get("tool_call")

            if isinstance(tool_call_data, dict):
                tool_name = tool_call_data.get("name")
                tool_args = tool_call_data.get("arguments")

                if isinstance(tool_name, str) and isinstance(tool_args, dict) and tool_name in self.tools:
                    print(f"Parsed tool call: Name='{tool_name}', Args={tool_args}")
                    return tool_name, tool_args
                else:
                    if tool_name not in self.tools:
                         print(f"Parsed tool name '{tool_name}' not found in registered tools.")
                    # else: print("Parsed JSON, but 'name' or 'arguments' format is incorrect.") # Debugging noise
                    return None # Format invalid or tool not found

        except json.JSONDecodeError as e:
            # print(f"JSON decoding failed: {e}") # Can be noisy if LLM outputs non-JSON
            return None # Not a valid JSON tool call structure
        except Exception as e:
             print(f"Unexpected error parsing potential tool call: {e}")
             return None

        return None # No valid tool call found


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
            # Validate args? Pydantic in BaseTool might handle some validation if used within execute.
            # For now, pass kwargs directly.
            result = await tool.execute(
                agent_id=agent_id,
                agent_sandbox_path=agent_sandbox_path,
                **tool_args
            )
            # Ensure result is string (or easily convertible) for feeding back to LLM/UI
            if not isinstance(result, str):
                 try:
                     result_str = json.dumps(result, indent=2)
                 except Exception:
                     result_str = str(result) # Fallback to plain string representation
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
