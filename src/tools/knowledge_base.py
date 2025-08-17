# START OF FILE src/tools/knowledge_base.py
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.base import BaseTool, ToolParameter
# Import the database manager instance
from src.core.database_manager import db_manager

logger = logging.getLogger(__name__)

class KnowledgeBaseTool(BaseTool):
    """
    Tool for interacting with the long-term knowledge base stored in the database.
    Allows saving distilled information and searching for relevant past knowledge.
    Intended primarily for the Admin AI.
    """
    name: str = "knowledge_base" # Changed name for clarity
    auth_level: str = "worker" # Accessible by all
    summary: Optional[str] = "Saves or searches long-term knowledge (learnings, procedures)."
    description: str = (
        "Interacts with the long-term knowledge base. Actions: "
        "'save_knowledge' (saves a summary of learned information with keywords), "
        "'search_knowledge' (searches for relevant knowledge using keywords), "
        "'search_agent_thoughts' (searches for past thoughts of a specific agent). "
        "Use this to persist important conclusions or retrieve relevant context from past sessions/interactions, including specific agent thoughts."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation to perform: 'save_knowledge', 'search_knowledge', or 'search_agent_thoughts'.",
            required=True,
        ),
        # Parameters for save_knowledge
        ToolParameter(
            name="summary",
            type="string",
            description="A concise summary of the information or learning to be saved. Required for 'save_knowledge'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="keywords",
            type="string",
            description="Comma-separated keywords relevant to the knowledge being saved. Required for 'save_knowledge'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="importance",
            type="float",
            description="Optional numerical score (e.g., 0.1 to 1.0) indicating the importance or confidence in the knowledge. Defaults to 0.5.",
            required=False,
        ),
        # Parameters for search_knowledge
        ToolParameter(
            name="query_keywords",
            type="string",
            description="Comma-separated keywords to search for in the knowledge base. Required for 'search_knowledge'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="min_importance",
            type="float",
            description="Optional: Minimum importance score for results returned by 'search_knowledge'.",
            required=False,
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="Optional: Maximum number of results to return for 'search_knowledge' or 'search_agent_thoughts'. Defaults to 5.",
            required=False,
        ),
        # Parameters for search_agent_thoughts
        ToolParameter(
            name="agent_identifier",
            type="string",
            description="The ID or unique identifier of the agent whose thoughts are being searched. Required for 'search_agent_thoughts'.",
            required=False, # Dynamically required
        ),
        ToolParameter(
            name="additional_keywords",
            type="string",
            description="Optional: Comma-separated string of additional keywords to narrow down the thought search for 'search_agent_thoughts'.",
            required=False,
        ),
    ]

    async def execute(
        self,
        agent_id: str, # The agent calling the tool (likely admin_ai)
        agent_sandbox_path: Path, # Not used by this tool
        project_name: Optional[str] = None, # Context for potential future use
        session_name: Optional[str] = None, # Context for potential future use
        **kwargs: Any
        ) -> Dict[str, Any]:
        """Executes the knowledge base action."""
        action = kwargs.get("action")
        logger.info(f"Agent {agent_id} requesting KnowledgeBaseTool action '{action}' with params: {kwargs}")
        if not action or action not in ["save_knowledge", "search_knowledge", "search_agent_thoughts"]:
            return {"status": "error", "message": "Invalid or missing 'action'. Must be 'save_knowledge', 'search_knowledge', or 'search_agent_thoughts'."}

        current_session_db_id: Optional[int] = None

        try:
            if action == "save_knowledge":
                summary = kwargs.get("summary")
                keywords_str = kwargs.get("keywords")
                importance_str = kwargs.get("importance", "0.5")

                if not summary:
                    summary = kwargs.get("_raw_thought_content", "[No Summary Provided]")
                if not keywords_str:
                    keywords_str = f"agent_thought,{agent_id},auto_logged"

                try:
                    importance = float(importance_str)
                except (ValueError, TypeError):
                    importance = 0.5

                saved_knowledge = await db_manager.save_knowledge(
                    keywords=keywords_str,
                    summary=summary,
                    session_id=current_session_db_id,
                    interaction_id=None,
                    importance=importance
                )

                if saved_knowledge:
                    return {"status": "success", "message": f"Successfully saved knowledge item ID {saved_knowledge.id}."}
                else:
                    return {"status": "error", "message": "Failed to save knowledge item to database."}

            elif action == "search_knowledge":
                query_keywords_str = kwargs.get("query_keywords")
                if not query_keywords_str:
                    return {"status": "error", "message": "'query_keywords' parameter is required for 'search_knowledge'."}

                query_keywords = [kw.strip() for kw in query_keywords_str.split(',') if kw.strip()]
                if not query_keywords:
                     return {"status": "error", "message": "'query_keywords' contained no valid keywords."}

                min_importance = None
                if kwargs.get("min_importance"):
                    try:
                        min_importance = float(kwargs["min_importance"])
                    except (ValueError, TypeError):
                        pass

                max_results = 5
                if kwargs.get("max_results"):
                    try:
                        max_results = int(kwargs["max_results"])
                    except (ValueError, TypeError):
                        pass

                found_items = await db_manager.search_knowledge(
                    query_keywords=query_keywords,
                    min_importance=min_importance,
                    max_results=max_results
                )

                if not found_items:
                    return {"status": "success", "message": f"No knowledge items found matching keywords: '{query_keywords_str}'.", "items": []}

                items_data = [{"id": item.id, "score": f"{item.importance_score:.2f}", "keywords": item.keywords, "summary": item.summary} for item in found_items]
                message = f"Found {len(found_items)} knowledge item(s) matching '{query_keywords_str}'."
                return {"status": "success", "message": message, "items": items_data}
            
            elif action == "search_agent_thoughts":
                agent_identifier = kwargs.get("agent_identifier")
                if not agent_identifier:
                    return {"status": "error", "message": "'agent_identifier' parameter is required for 'search_agent_thoughts'."}

                search_keywords = ["agent_thought", str(agent_identifier).strip()]
                if kwargs.get("additional_keywords"):
                    search_keywords.extend([kw.strip() for kw in kwargs["additional_keywords"].split(',') if kw.strip()])

                max_results = 5
                if kwargs.get("max_results"):
                    try:
                        max_results = int(kwargs["max_results"])
                    except (ValueError, TypeError):
                        pass
                
                found_items = await db_manager.search_knowledge(
                    query_keywords=search_keywords,
                    max_results=max_results
                )

                if not found_items:
                    return {"status": "success", "message": f"No thoughts found for agent '{agent_identifier}' matching keywords.", "thoughts": []}
                
                thoughts_data = [{"id": item.id, "timestamp": item.timestamp.strftime('%Y-%m-%d %H:%M:%S') if item.timestamp else 'N/A', "keywords": item.keywords, "thought": item.summary} for item in found_items]
                message = f"Found {len(found_items)} thought(s) for agent '{agent_identifier}'."
                return {"status": "success", "message": message, "thoughts": thoughts_data}

        except Exception as e:
            logger.error(f"Unexpected error executing KnowledgeBaseTool (Action: {action}) for agent {agent_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Error executing knowledge base tool ({action}): {type(e).__name__} - {e}"}

    def get_detailed_usage(self) -> str:
        """Returns detailed usage instructions for the KnowledgeBaseTool."""
        return super().get_detailed_usage()
