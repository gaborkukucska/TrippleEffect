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
        message_content = kwargs.get("message_content") # content checked by ToolExecutor based on schema

        logger.info(f"SendMessageTool request validated for execution by '{agent_id}' targeting '{target_agent_id}'.")

        # Note: The primary validation (required fields, basic types if specified in ToolParameter)
        # happens in ToolExecutor based on the 'parameters' schema defined above.
        # This execute method is called *after* that validation passes.
        # We could add more specific validation here if needed (e.g., check target_agent_id format).

        # The return value of this execute method becomes the "tool result" message
        # that gets appended to the *sender's* history.
        # The recipient agent receives the actual message content via the AgentManager.
        return f"Message routing to agent '{target_agent_id}' initiated by manager."
