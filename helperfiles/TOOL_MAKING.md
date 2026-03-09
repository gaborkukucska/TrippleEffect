<!-- # START OF FILE helperfiles/TOOL_MAKING.md -->
# TrippleEffect: Tool Development Guide

This guide explains how to create new tools that agents within the TrippleEffect framework can utilize. Tools are invoked using an **XML format**.

## Core Concepts

*   **Discovery:** Tools are dynamically discovered by the `ToolExecutor` at application startup.
*   **Location:** All tool implementation files must reside directly within the `src/tools/` directory.
*   **Structure:** Each tool should ideally be in its own Python file (e.g., `my_tool.py`).
*   **Base Class:** Every tool class MUST inherit from `src.tools.base.BaseTool`.
*   **Registration:** The `ToolExecutor` automatically finds and registers valid tool classes inheriting from `BaseTool` (excluding `BaseTool` itself and files starting with `_`).
*   **Invocation Format:** Agents request tool execution using an **XML block** at the end of their message.

## Creating a New Tool

Follow these steps to create a new tool:

1.  **Create the File:** Create a new Python file in the `src/tools/` directory (e.g., `calculator_tool.py`).

2.  **Import Base Classes:** Import `BaseTool` and `ToolParameter` from `src.tools.base`. Also import `Path` from `pathlib` and potentially `logging`, `asyncio`, and any libraries your tool needs.

    ```python
    # START OF FILE src/tools/calculator_tool.py
    import asyncio
    import logging
    from pathlib import Path
    from typing import Any, Dict, List, Optional
    import math # Example library

    from src.tools.base import BaseTool, ToolParameter

    logger = logging.getLogger(__name__)
    ```

3.  **Define the Class:** Create a class that inherits from `BaseTool`.

    ```python
    class CalculatorTool(BaseTool):
        # ... attributes and methods ...
    ```

4.  **Define Class Attributes:** Define the required class attributes:
    *   `name` (str): A unique, snake\_case identifier for the tool. This is used in the XML tag `<tool_name>`.
    *   `description` (str): A clear, concise description for the LLM, explaining what the tool does and when to use it.
    *   `parameters` (List[ToolParameter]): A list defining the inputs the tool accepts. Use the `ToolParameter` model:
        *   `name`: Parameter name (snake\_case), used for XML tags `<param_name>`.
        *   `type`: Expected data type (e.g., 'string', 'integer', 'float', 'boolean'). This is mainly for documentation; validation might be basic.
        *   `description`: Clear explanation of the parameter for the LLM.
        *   `required` (bool): Whether the agent *must* provide this parameter (defaults to `True`).
    *   `usage_example` (str, optional): A string, often using CDATA for clarity if it contains XML, showing the LLM exactly how to format the XML call for this tool. This is crucial for complex tools or when specific formatting is needed.

    ```python
    class CalculatorTool(BaseTool):
        name: str = "calculator"
        description: str = (
            "Performs basic arithmetic operations (add, subtract, multiply, divide, power). "
            "Use for calculations that require precision."
        )
        parameters: List[ToolParameter] = [
            ToolParameter(
                name="operation",
                type="string",
                description="The operation to perform: 'add', 'subtract', 'multiply', 'divide', 'power'.",
                required=True,
            ),
            ToolParameter(
                name="operand1",
                type="float",
                description="The first number for the operation.",
                required=True,
            ),
            ToolParameter(
                name="operand2",
                type="float",
                description="The second number for the operation.",
                required=True,
            ),
        ]
        # Optional: Provide a clear example for the LLM
        usage_example: str = """<![CDATA[
<calculator>
  <operation>add</operation>
  <operand1>5</operand1>
  <operand2>3</operand2>
</calculator>
]]>"""
    ```

5.  **Implement `execute` Method:** Implement the core logic within the `async def execute(...)` method.
    *   **Signature:** Must match `async def execute(self, agent_id: str, agent_sandbox_path: Path, project_name: Optional[str] = None, session_name: Optional[str] = None, **kwargs: Any) -> Any:`
    *   `agent_id`: ID of the agent calling the tool (useful for logging or context).
    *   `agent_sandbox_path`: Absolute `Path` object to the agent's dedicated working directory. **Crucially, any file operations should be restricted to this path** using methods like `Path.is_relative_to()` for security. Most tools won't need this directly unless operating in the private sandbox.
    *   `project_name`, `session_name`: **(NEW)** Context passed from the `ToolExecutor` containing the currently active project and session names. This is essential for tools that need to interact with the shared workspace (like `FileSystemTool` with `scope='shared'`). Tools can use these to construct the correct path to the shared workspace.
    *   `**kwargs`: A dictionary containing the arguments provided by the agent (parsed from the XML by `Agent Core`). Access parameters using `kwargs.get("param_name")`. The `ToolExecutor` performs basic validation based on your `parameters` definition (checking required fields). You might add more specific validation inside `execute`.
    *   **Return Value:**
        *   For most tools: Return a **string** summarizing the result or confirming success. This string is added to the calling agent's history.
        *   For errors: Return a descriptive **string** starting with "Error: ".
        *   *Special Case:* `ManageTeamTool` returns a dictionary signal for the `AgentManager`. Avoid returning complex objects unless handled specifically by the `AgentManager`.
    *   **Blocking I/O:** If your tool needs to perform blocking operations (like network requests with `requests`, synchronous file I/O, or heavy computation), use `await asyncio.to_thread(your_blocking_function, args)` to avoid blocking the main application event loop. Use async libraries (like `aiohttp`, `AsyncGithub`, `TavilyClient`) whenever possible.
    *   **Logging:** Use the `logger` instance for informative messages.

    ```python
    # Example execute method incorporating project/session context check
    class CalculatorTool(BaseTool):
        # ... name, description, parameters ...

        async def execute(self, agent_id: str, agent_sandbox_path: Path, project_name: Optional[str] = None, session_name: Optional[str] = None, **kwargs: Any) -> Any:
            operation = kwargs.get("operation")
            op1_str = kwargs.get("operand1")
            op2_str = kwargs.get("operand2")

            # Example check if context is needed (though calculator doesn't need it)
            # if not project_name or not session_name:
            #    logger.warning(f"CalculatorTool called without project/session context (Project: {project_name}, Session: {session_name})")
            #    # You might return an error if context is strictly required by the tool logic

            # Validate inputs (ToolExecutor already checked for presence if required)
            if not operation or operation not in ['add', 'subtract', 'multiply', 'divide', 'power']:
                return "Error: Invalid 'operation'. Must be 'add', 'subtract', 'multiply', 'divide', or 'power'."
            try:
                op1 = float(op1_str)
                op2 = float(op2_str)
            except (ValueError, TypeError):
                return f"Error: Invalid operands. Could not convert '{op1_str}' or '{op2_str}' to numbers."

            logger.info(f"Agent {agent_id} performing calculation: {op1} {operation} {op2} (Project: {project_name}, Session: {session_name})") # Log context

            # Perform calculation
            result: Optional[float] = None
            try:
                if operation == 'add':
                    result = op1 + op2
                elif operation == 'subtract':
                    result = op1 - op2
                elif operation == 'multiply':
                    result = op1 * op2
                elif operation == 'divide':
                    if op2 == 0:
                        return "Error: Division by zero."
                    result = op1 / op2
                elif operation == 'power':
                    # Use asyncio.to_thread for potentially CPU-bound math.pow if numbers were huge
                    # For simple cases, direct call is fine.
                    result = await asyncio.to_thread(math.pow, op1, op2)
                    # result = math.pow(op1, op2) # Simpler alternative for typical cases

                if result is not None:
                    return f"Calculation result: {op1} {operation} {op2} = {result}"
                else:
                    # Should not happen with current logic, but defensive
                    return f"Error: Calculation failed for unknown reason."

            except Exception as e:
                logger.error(f"Error during calculation for agent {agent_id}: {e}", exc_info=True)
                return f"Error performing calculation: {type(e).__name__}"

    ```

6.  **Dependencies:** If your tool requires external libraries (like `requests`, `beautifulsoup4`, `tavily-python`, `PyGithub`), add them to the main `requirements.txt` file.

7.  **Environment Variables:** If your tool needs secrets or configuration (like API keys), add corresponding variables to `.env.example` (with placeholders) and instruct users to set them in their `.env` file. Access them within your tool using `from src.config.settings import settings` and then `settings.MY_VARIABLE_NAME`.

8.  **Restart:** After adding the new tool file and installing any dependencies, restart the TrippleEffect application. The `ToolExecutor` will automatically discover and register your new tool.

## Example Agent Usage (XML)

An agent would call the `CalculatorTool` like this in its response:

```xml
Okay, I can calculate that for you.
<calculator>
  <operation>multiply</operation>
  <operand1>123.45</operand1>
  <operand2>67.8</operand2>
</calculator>
```

The framework parses this, calls the execute method, and the tool's string result (e.g., "Calculation result: 123.45 multiply 67.8 = 8370.01") is added back into the agent's message history.

## Implementing Modular Tool Help (`get_detailed_usage`)

Tools should implement segmented help documentation via the `get_detailed_usage` method. This allows agents to request help for specific actions rather than receiving the entire documentation block, reducing token usage and improving context relevance.

### How It Works

The `BaseTool.get_detailed_usage()` method accepts an optional `sub_action` parameter:

*   **No `sub_action`** → Return a concise summary listing all available actions with one-line descriptions, plus instructions for requesting detailed help.
*   **With `sub_action`** → Return the common header + detailed parameters/examples for that specific action only.

### Implementation Pattern

```python
def get_detailed_usage(self, agent_context: Optional[Dict] = None, sub_action: Optional[str] = None) -> str:
    common_header = """
**Tool Name:** my_tool
**Description:** Brief description of what the tool does.
"""
    # Specific action help sections
    action_details = {
        "action_one": """
**Action: action_one**
Does something specific.
*   `<param1>` (string, required): Description.
*   `<param2>` (integer, optional): Description.
""",
        "action_two": """
**Action: action_two**
Does something else.
*   `<param1>` (string, required): Description.
""",
    }

    # If a specific sub_action is requested, return just that section
    if sub_action and sub_action in action_details:
        return common_header + action_details[sub_action]

    # Otherwise, return summary with instructions to get specific help
    return common_header + """
**Available Actions Summary:**
1.  **action_one:** Does something specific.
2.  **action_two:** Does something else.

**To get detailed instructions for a specific action, call:**
<tool_information>
  <action>get_info</action>
  <tool_name>my_tool</tool_name>
  <sub_action>ACTION_NAME</sub_action>
</tool_information>
"""
```

### Contextual Error Help Injection

When a tool execution fails, the `ToolExecutor` automatically:
1.  Extracts the `action` from the failed tool call arguments.
2.  Calls `tool.get_detailed_usage(sub_action=action)` to get the relevant help section.
3.  Passes this `action_help` to the `ToolErrorHandler`, which includes it in the error message sent back to the agent.

This means agents automatically receive the relevant documentation for the specific action that failed, enabling them to self-correct without needing to request help manually.

### Existing Implementations

The following tools already implement modular help:
*   `FileSystemTool` — 12 actions (`read`, `write`, `append`, `insert_lines`, `replace_lines`, `list`, `mkdir`, `delete`, `find_replace`, `regex_replace`, `copy`, `move`)
*   `ProjectManagementTool` — 4 actions (`add_task`, `modify_task`, `get_tasks`, `get_project_state`)
*   `ManageTeamTool` — 4 actions (`create_agent`, `set_agent_state`, `send_message`, `get_team_status`)

