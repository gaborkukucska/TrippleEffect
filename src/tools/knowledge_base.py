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
        "'search_knowledge' (searches for relevant knowledge using keywords). "
        "Use this to persist important conclusions or retrieve relevant context from past sessions/interactions."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description="The operation to perform: 'save_knowledge' or 'search_knowledge'.",
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
            description="Optional: Maximum number of results to return for 'search_knowledge'. Defaults to 5.",
            required=False,
        ),
    ]

    async def execute(
        self,
        agent_id: str, # The agent calling the tool (likely admin_ai)
        agent_sandbox_path: Path, # Not used by this tool
        project_name: Optional[str] = None, # Context for potential future use
        session_name: Optional[str] = None, # Context for potential future use
        # We need the current session_id from the manager to link knowledge
        # But tools don't have direct manager access. This needs adjustment.
        # For now, let's assume the manager passes it via kwargs if possible,
        # or we link knowledge globally (less ideal).
        # **Let's assume session_id is NOT reliably passed here yet.**
        # We will link knowledge globally for now, or to the session found via project/session names if possible.
        **kwargs: Any
        ) -> Any:
        """Executes the knowledge base action."""
        action = kwargs.get("action")
        logger.info(f"Agent {agent_id} requesting KnowledgeBaseTool action '{action}' with params: {kwargs}")
        if not action or action not in ["save_knowledge", "search_knowledge"]:
            return "Error: Invalid or missing 'action'. Must be 'save_knowledge' or 'search_knowledge'."
        # --- Get DB Session ID (Best effort - Requires Manager modification ideally) ---
        # This is a temporary workaround. Ideally, ToolExecutor would get session_db_id from manager
        # and pass it here explicitly.
        current_session_db_id: Optional[int] = None
        # if project_name and session_name:
        #    # Need a db_manager method here: get_session_id(project_name, session_name)
        #    pass # Placeholder - cannot get session ID reliably here yet.

        try:
            if action == "save_knowledge":
                # --- MODIFIED: Make summary/keywords optional for saving ---
                summary = kwargs.get("summary")
                keywords_str = kwargs.get("keywords") # Renamed to avoid conflict
                importance_str = kwargs.get("importance", "0.5")

                # Provide defaults if missing (e.g., for automatic thought saving)
                if not summary:
                    # Attempt to get raw thought content if available (needs framework support)
                    # For now, use a placeholder if summary is truly missing.
                    summary = kwargs.get("_raw_thought_content", "[No Summary Provided]") # Example placeholder
                    logger.warning(f"KnowledgeBaseTool: 'summary' missing for save_knowledge called by {agent_id}. Using placeholder/raw content.")

                if not keywords_str:
                    keywords_str = f"agent_thought,{agent_id},auto_logged" # Default keywords
                    logger.warning(f"KnowledgeBaseTool: 'keywords' missing for save_knowledge called by {agent_id}. Using default tags: {keywords_str}")

                # if not summary or not keywords: # Original check removed
                #     return "Error: 'summary' and 'keywords' parameters are required for 'save_knowledge'."

                try:
                    importance = float(importance_str)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid importance value '{importance_str}' from agent {agent_id}. Defaulting to 0.5.")
                    importance = 0.5

                # TODO: Get interaction_id if possible to link the source. Requires framework changes.
                interaction_id = None

                saved_knowledge = await db_manager.save_knowledge(
                    keywords=keywords_str, # Use the potentially defaulted string
                    summary=summary, # Use the potentially defaulted summary
                    session_id=current_session_db_id, # May be None for now
                    interaction_id=interaction_id, # May be None
                    importance=importance
                )

                if saved_knowledge:
                    return f"Successfully saved knowledge item ID {saved_knowledge.id}."
                else:
                    return "Error: Failed to save knowledge item to database."

            elif action == "search_knowledge":
                query_keywords_str = kwargs.get("query_keywords")
                min_importance_str = kwargs.get("min_importance")
                max_results_str = kwargs.get("max_results", "5")

                if not query_keywords_str:
                    return "Error: 'query_keywords' parameter is required for 'search_knowledge'."

                query_keywords = [kw.strip() for kw in query_keywords_str.split(',') if kw.strip()]
                if not query_keywords:
                     return "Error: 'query_keywords' contained no valid keywords after splitting."

                min_importance = None
                if min_importance_str:
                    try:
                        min_importance = float(min_importance_str)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid min_importance value '{min_importance_str}' from agent {agent_id}. Ignoring.")

                try:
                    max_results = int(max_results_str)
                    if max_results <= 0: max_results = 5
                except ValueError:
                    logger.warning(f"Invalid 'max_results' value '{max_results_str}' provided by {agent_id}. Defaulting to 5.")
                    max_results = 5

                found_items = await db_manager.search_knowledge(
                    query_keywords=query_keywords,
                    min_importance=min_importance,
                    max_results=max_results
                )

                if not found_items:
                    return f"No knowledge items found matching keywords: '{query_keywords_str}'" + (f" with min importance {min_importance}." if min_importance else ".")

                # Format results
                output_lines = [f"Found {len(found_items)} knowledge item(s) matching '{query_keywords_str}':"]
                MAX_SUMMARY_LEN = 200
                for item in found_items:
                    summary_preview = item.summary[:MAX_SUMMARY_LEN] + ('...' if len(item.summary) > MAX_SUMMARY_LEN else '')
                    output_lines.append(
                        f"  - ID: {item.id}, Score: {item.importance_score:.2f}, Keywords: '{item.keywords}'\n    Summary: {summary_preview}"
                    )

                return "\n".join(output_lines)

        except Exception as e:
            logger.error(f"Unexpected error executing KnowledgeBaseTool (Action: {action}) for agent {agent_id}: {e}", exc_info=True)
            return f"Error executing knowledge base tool ({action}): {type(e).__name__} - {e}"

    # --- Detailed Usage Method ---
    def get_detailed_usage(self) -> str:
        """Returns detailed usage instructions for the KnowledgeBaseTool."""
        usage = """
        **Tool Name:** knowledge_base

        **Description:** Saves or searches for information in the long-term knowledge base. Useful for remembering past learnings, procedures, or context across sessions.

        **Actions & Parameters:**

        1.  **save_knowledge:** Saves a piece of knowledge.
            *   `<summary>` (string, required): A concise summary of the information to save.
            *   `<keywords>` (string, required): Comma-separated keywords relevant to the summary (e.g., 'python,fastapi,deployment,docker').
            *   `<importance>` (float, optional): A score from 0.1 to 1.0 indicating confidence or importance. Defaults to 0.5.
            *   Example:
                ```xml
                <knowledge_base>
                  <action>save_knowledge</action>
                  <summary>Successfully deployed the webapp using Docker on the staging server. Key steps involved updating the Dockerfile and nginx config.</summary>
                  <keywords>deployment,docker,webapp,staging,nginx</keywords>
                  <importance>0.9</importance>
                </knowledge_base>
                ```

        2.  **search_knowledge:** Searches the knowledge base for relevant items.
            *   `<query_keywords>` (string, required): Comma-separated keywords to search for (e.g., 'python,data analysis,pandas').
            *   `<max_results>` (integer, optional): Maximum number of results to return. Defaults to 5.
            *   `<min_importance>` (float, optional): Only return results with an importance score greater than or equal to this value.
            *   Example:
                ```xml
                <knowledge_base>
                  <action>search_knowledge</action>
                  <query_keywords>python,api,error handling</query_keywords>
                  <max_results>3</max_results>
                  <min_importance>0.7</min_importance>
                </knowledge_base>
                ```

        **Important Notes:**
        *   Use `save_knowledge` after successful complex tasks or when significant learning occurs.
        *   Use `search_knowledge` *before* planning complex tasks to leverage past information.
        """
        return usage.strip()
