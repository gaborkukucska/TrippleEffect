# START OF FILE src/tools/web_search.py
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from duckduckgo_search import AsyncDDGS # Use the async version

from src.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)

class WebSearchTool(BaseTool):
    """
    Performs a web search using DuckDuckGo and returns the results.
    Useful for finding current information, research, or code examples online.
    """
    name: str = "web_search"
    description: str = (
        "Searches the web using DuckDuckGo for a given query and returns a specified number of results. "
        "Useful for finding up-to-date information, researching topics, or finding examples."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="query",
            type="string",
            description="The search query string.",
            required=True,
        ),
        ToolParameter(
            name="num_results",
            type="integer",
            description="The maximum number of search results to return (e.g., 3, 5). Defaults to 3.",
            required=False,
        ),
    ]

    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Executes the web search using DuckDuckGo's async API.

        Args:
            agent_id (str): The ID of the agent calling the tool.
            agent_sandbox_path (Path): The path to the agent's sandbox (not used by this tool).
            **kwargs: Arguments containing 'query' and optionally 'num_results'.

        Returns:
            str: A formatted string containing the search results (title, URL, snippet)
                 or an error message.
        """
        query = kwargs.get("query")
        num_results_str = kwargs.get("num_results", "3") # Default to 3 results

        if not query:
            return "Error: 'query' parameter is required for web search."

        try:
            num_results = int(num_results_str)
            if num_results <= 0:
                num_results = 3 # Default to 3 if invalid number provided
        except ValueError:
            logger.warning(f"Invalid 'num_results' value '{num_results_str}' provided by {agent_id}. Defaulting to 3.")
            num_results = 3

        logger.info(f"Agent {agent_id} performing web search for: '{query}' (max results: {num_results})")

        try:
            # Use the async context manager for AsyncDDGS
            async with AsyncDDGS() as ddgs:
                results = await ddgs.atext(
                    keywords=query,
                    max_results=num_results
                )

            if not results:
                return f"No search results found for query: '{query}'"

            # Format the results
            formatted_results = [
                f"Title: {res.get('title', 'N/A')}\n"
                f"URL: {res.get('href', 'N/A')}\n"
                f"Snippet: {res.get('body', 'N/A')}\n---"
                for res in results
            ]
            logger.info(f"Web search for '{query}' completed successfully for {agent_id}.")
            return f"Search Results for '{query}':\n\n" + "\n".join(formatted_results)

        except Exception as e:
            logger.error(f"Error during web search for agent {agent_id} with query '{query}': {e}", exc_info=True)
            return f"Error performing web search for '{query}': {type(e).__name__} - {e}"
