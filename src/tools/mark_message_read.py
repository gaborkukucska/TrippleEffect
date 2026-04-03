from pathlib import Path
from typing import Any, Dict, List, Optional
import time

from src.tools.base import BaseTool, ToolParameter
import logging

logger = logging.getLogger(__name__)

class MarkMessageReadTool(BaseTool):
    """
    Tool for agents to acknowledge they have read incoming messages.
    """
    name: str = "mark_message_read"
    auth_level: str = "worker"
    summary: Optional[str] = "Marks a received message as read/acknowledged."
    description: str = (
        "Marks a message you received from another agent as read/acknowledged. "
        "Use this tool immediately after receiving and understanding a message, especially instructions or tasks. "
        "This will automatically filter the message from your active history in future cycles to save context space, "
        "and tracks your acknowledgements."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="message_id",
            type="string",
            description="The unique ID of the message you want to mark as read (e.g., 'msg_12345'). Found in the message tag.",
            required=True,
        ),
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, project_name: Optional[str] = None, session_name: Optional[str] = None, **kwargs: Any) -> Any:
        message_id = kwargs.get("message_id")
        if not message_id:
            return {"status": "error", "message": "Missing 'message_id'"}
            
        # The agent marking this read gets a confirmation
        # Agent cycle handler will intercept this and update the agent's set of read message IDs
        return {"status": "success", "message": f"Message '{message_id}' marked as read. It will be hidden from history in future cycles.", "action": "mark_read", "message_id": message_id}

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        return '''
        **Tool Name:** mark_message_read
        
        Use this tool to acknowledge you have received and read a message from the Project Manager or Admin.
        Example:
        <mark_message_read>
          <message_id>msg_172837362_PM1</message_id>
        </mark_message_read>
        '''
