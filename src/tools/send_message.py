# START OF FILE src/tools/send_message.py
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.base import BaseTool, ToolParameter
import logging # Add logging

logger = logging.getLogger(__name__)

class SendMessageTool(BaseTool):
    """
    Tool for sending a message to another agent within the same team.
    This tool signals the AgentManager to route the message based on the tool call request.
    """
    name: str = "send_message"
    auth_level: str = "worker" # Accessible by all
    summary: Optional[str] = "Sends a message to another specified agent."
    description: str = (
        "Sends a message to a specified teammate agent. "
        "Use this to ask questions, delegate tasks, provide information, or request reviews from agents listed in your system prompt."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="target_agent_id",
            type="string",
            description="The unique ID of the agent teammate you want to send the message to (e.g., 'coder', 'analyst'). Must be a valid agent ID.",
            required=True,
        ),
        ToolParameter(
            name="message_content",
            type="string",
            description="The content of the message you want to send to your teammate.",
            required=True,
        ),
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Validates parameters provided for the tool call.
        The actual message routing is handled by the AgentManager intercepting the tool request.
        This method simply returns a confirmation string to be added to the calling agent's history.

        Args:
            agent_id (str): The ID of the agent calling the tool (the sender).
            agent_sandbox_path (Path): The path to the agent's sandbox directory (not used by this tool).
            **kwargs: Arguments containing 'target_agent_id' and 'message_content'.

        Returns:
            str: A confirmation message indicating the message is being routed by the manager,
                 or an error message if basic validation fails (though ToolExecutor handles schema checks).
        """
        target_agent_id = kwargs.get("target_agent_id")
        message_content = kwargs.get("message_content")

        # Enhanced validation with better error messages
        if not target_agent_id:
            return {
                "status": "error", 
                "message": "Missing required 'target_agent_id' parameter. You must specify which agent to send the message to.",
                "error_type": "missing_parameter",
                "suggestions": [
                    "Use 'manage_team' with action 'list_agents' to see available agents",
                    "Ensure you use the exact agent ID, not the persona name"
                ]
            }
        
        if not message_content:
            return {
                "status": "error", 
                "message": "Missing required 'message_content' parameter. You must provide the message to send.",
                "error_type": "missing_parameter",
                "suggestions": [
                    "Include the content you want to communicate to the other agent",
                    "For large content, consider saving to a file first and referencing it"
                ]
            }

        # Check for common mistakes
        if target_agent_id == agent_id:
            return {
                "status": "error",
                "message": f"Cannot send message to yourself ('{agent_id}'). Use a different target agent.",
                "error_type": "invalid_parameter",
                "suggestions": [
                    "Use 'manage_team' with action 'list_agents' to see other available agents",
                    "Messages are for communication between different agents"
                ]
            }

        logger.info(f"SendMessageTool request validated for execution by '{agent_id}' targeting '{target_agent_id}'.")

        # The return value of this execute method becomes the "tool result" message
        # that gets appended to the *sender's* history.
        # The recipient agent receives the actual message content via the AgentManager.
        return f"Message routing to agent '{target_agent_id}' initiated by manager."

    # --- Detailed Usage Method ---
    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """Returns detailed usage instructions for the SendMessageTool."""
        usage = """
        **Tool Name:** send_message

        **Description:** Sends a message to another agent. Used for delegation, asking questions, providing information, or reporting results.

        **Parameters:**

        *   `<target_agent_id>` (string, required): The unique ID of the agent to send the message to.
            *   **CRITICAL:** Use the exact agent ID (e.g., `agent_17..._abc`, `admin_ai`) obtained from `ManageTeamTool` (`create_agent` feedback or `list_agents`). Using personas might fail if not unique.
        *   `<message_content>` (string, required): The content of the message to send.
            *   **IMPORTANT:** For large outputs (code, reports), use the `file_system` tool to write the content to a file first, then use `send_message` to notify the recipient about the file (`filename` and `scope`). Do not include large content directly in the message.

        **Example:**
        ```xml
        <send_message>
          <target_agent_id>agent_coder_123</target_agent_id>
          <message_content>Please review the code in shared workspace file 'src/utils.py' and provide feedback.</message_content>
        </send_message>
        ```

        **Reporting Task Completion:**
        After completing your assigned task, your **final action** MUST be to use this tool to report completion and results (or file location) back to the agent who assigned the task (usually `admin_ai`). Stop generating output after sending this final message.
        """
        return usage.strip()
