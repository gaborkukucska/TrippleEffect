# START OF FILE src/tools/executor.py
import json
import re # Regular expression for parsing potential JSON
import logging # Use logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

# Import BaseTool and specific tools
from src.tools.base import BaseTool
from src.tools.file_system import FileSystemTool
# Import other tools here as they are created, e.g.:
# from src.tools.web_search import WebSearchTool

logger = logging.getLogger(__name__)

# --- Standard Instructions for Tool Usage ---
# This explains HOW the LLM should format a tool call request.
# It is intended to be appended to the system prompt if tools are available.
TOOL_USAGE_INSTRUCTIONS = """
When you need to use a tool to fulfill the user's request, respond ONLY with a single JSON block in the following format. Do not include any other text, explanation, or markdown formatting before or after the JSON block:
```json
{
  "tool_call": {
    "name": "<tool_name>",
    "arguments": {
      "<param_name1>": "<value1>",
      "<param_name2>": <value2>
    }
  }
}
```
Replace `<tool_name>` with the exact name of the tool you want to use from the available tools list.
Replace the `<param_nameX>` and `<valueX>` placeholders within the `arguments` object with the required parameters and their corresponding values for the chosen tool. Ensure the argument values match the expected types (string, integer, boolean, etc.).
If a tool execution is successful, you will receive a confirmation message. If it fails, you will receive an error message. Use this feedback to continue the task or inform the user.
"""

class ToolExecutor:
    """
    Manages and executes available tools for agents.
    - Registers tools.
    - Provides schemas of available tools (for the 'tools' API parameter).
    - Provides standard instructions on how LLMs should format tool calls.
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
        logger.info(f"ToolExecutor initialized with tools: {list(self.tools.keys())}")

    def _register_available_tools(self):
        """Instantiates and registers tools defined in AVAILABLE_TOOL_CLASSES."""
        logger.info("Registering available tools...")
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
            logger.error(f"Cannot register object of type {type(tool_instance)}. Must be subclass of BaseTool.")
            return
        if tool_instance.name in self.tools:
            logger.warning(f"Tool name conflict. '{tool_instance.name}' already registered. Overwriting.")
        self.tools[tool_instance.name] = tool_instance
        logger.info(f"Manually registered tool: {tool_instance.name}")

    # --- Tool Schema/Discovery ---

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Returns a list of schemas for all registered tools (for the 'tools' API parameter)."""
        return [tool.get_schema() for tool in self.tools.values()]

    def get_tool_usage_instructions(self) -> str:
        """Returns the standard string explaining how LLMs should format tool calls."""
        # Check if any tools are actually registered before returning instructions
        if not self.tools:
            return "" # Return empty string if no tools are available
        return TOOL_USAGE_INSTRUCTIONS

    # --- Tool Call Parsing & Execution ---
    # NOTE: The parsing logic below (`parse_tool_call`) was designed for LLMs that
    # output the specific JSON block within their regular text response.
    # Newer OpenAI API compatible providers (like used here) handle tool calls
    # via a dedicated 'tool_calls' field in the API response delta, which is
    # processed by the LLM Provider classes (`openai_provider.py`, `openrouter_provider.py`).
    # Therefore, this specific parsing method in ToolExecutor is likely NOT being used
    # in the current flow with these providers. It's kept here for potential future use
    # or compatibility with different LLM response styles.

    def parse_tool_call(self, response_content: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        (Likely Unused with current OpenAI-style providers)
        Parses LLM response content for a specific JSON structure indicating a tool call.
        """
        # Regex to find a JSON block potentially enclosed in ```json ... ```
        match = re.search(r"```json\s*(\{.*?\})\s*```|(\{.*?\})", response_content.strip(), re.DOTALL)
        if not match: return None
        json_text = match.group(1) or match.group(2)

        try:
            data = json.loads(json_text)
            tool_call_data = data.get("tool_call")

            if isinstance(tool_call_data, dict):
                tool_name = tool_call_data.get("name")
                tool_args = tool_call_data.get("arguments")
                if isinstance(tool_name, str) and isinstance(tool_args, dict) and tool_name in self.tools:
                    logger.debug(f"Parsed tool call via legacy method: Name='{tool_name}', Args={tool_args}")
                    return tool_name, tool_args
                else:
                    # Log if format is wrong or tool is unknown
                    if tool_name and tool_name not in self.tools:
                         logger.warning(f"Legacy parser found tool name '{tool_name}' not in registered tools.")
                    else:
                         logger.warning("Legacy parser found JSON, but 'name'/'arguments' format incorrect or missing.")
                    return None
        except json.JSONDecodeError:
            # This is expected if the LLM response is not the specific JSON format
            return None
        except Exception as e:
             logger.error(f"Unexpected error parsing potential legacy tool call: {e}", exc_info=True)
             return None
        return None


    async def execute_tool(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        tool_name: str,
        tool_args: Dict[str, Any]
    ) -> str:
        """
        Executes the specified tool with the given arguments.
        (Execution logic remains the same)
        """
        tool = self.tools.get(tool_name)
        if not tool:
            return f"Error: Tool '{tool_name}' not found."

        logger.info(f"Executing tool '{tool_name}' for agent '{agent_id}' with args: {tool_args}")
        try:
            result = await tool.execute(
                agent_id=agent_id,
                agent_sandbox_path=agent_sandbox_path,
                **tool_args
            )
            if not isinstance(result, str):
                 try:
                     result_str = json.dumps(result, indent=2)
                 except Exception:
                     result_str = str(result)
            else:
                 result_str = result

            logger.debug(f"Tool '{tool_name}' execution result (first 100 chars): {result_str[:100]}...")
            return result_str

        except Exception as e:
            error_msg = f"Error executing tool '{tool_name}': {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True) # Add traceback to log
            return error_msg
