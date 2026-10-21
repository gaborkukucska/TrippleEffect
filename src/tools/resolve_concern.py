# START OF FILE src/tools/resolve_concern.py
"""
resolve_concern (Round 2o): how a Project Manager or Admin AI decides a Constitutional Guardian concern about a message that
the Guardian held (see src/agents/cg_escalation.py). It is offered to an agent only while a concern waits for its decision.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)


class ResolveConcernTool(BaseTool):
    name: str = "resolve_concern"
    auth_level: str = "pm"
    summary: Optional[str] = "Decide a Constitutional Guardian concern about a held message: approve, revise or escalate."
    description: str = (
        "Decide a Constitutional Guardian concern that is waiting for YOUR decision (its message names the concern_id). "
        "decision='approve' delivers the held text as it is; decision='revise' sends it back to its author with your "
        "'feedback' (the new version is checked again); decision='escalate' passes it to the next decider (Admin AI, then "
        "the user). A concern about a critical principle is not settled by approving it."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(name="concern_id", type="string", required=True,
                      description="The id of the concern, e.g. 'cg_3' (from the Guardian's DECISION NEEDED message)."),
        ToolParameter(name="decision", type="string", required=True,
                      description="One of: 'approve', 'revise', 'escalate'."),
        ToolParameter(name="feedback", type="string", required=False,
                      description="For 'revise': what the author must change. For 'escalate': why (optional)."),
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, project_name: Optional[str] = None,
                      session_name: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        manager = kwargs.get("manager")
        escalation = getattr(manager, "cg_escalation", None) if manager is not None else None
        if escalation is None:
            return {"status": "error", "message": "Constitutional Guardian concerns are not available right now."}
        ok, message = await escalation.decide(agent_id, kwargs.get("concern_id"), kwargs.get("decision"), kwargs.get("feedback"))
        return {"status": "success" if ok else "error", "message": message}

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        return (
            "**Tool Name:** resolve_concern\n\n"
            "Use it when the Constitutional Guardian sends you '[Constitutional Guardian - DECISION NEEDED] concern_id=...'.\n"
            "* approve: the held message is delivered unchanged.\n"
            "* revise (with feedback): the author must rewrite it; the Guardian checks the new version.\n"
            "* escalate: the next decider (Admin AI, then the user) decides.\n"
            "Example: resolve_concern(concern_id='cg_3', decision='revise', feedback='Do not include the user's e-mail address.')"
        )

# END OF FILE src/tools/resolve_concern.py
