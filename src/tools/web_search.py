# START OF FILE src/tools/web_search.py
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp # For making HTTP requests
from bs4 import BeautifulSoup # For parsing HTML
import urllib.parse # For URL encoding the query

from src.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)

# --- Constants for Scraping ---
# Using DuckDuckGo's HTML version which is simpler to parse
SEARCH_URL_TEMPLATE = "https://html.duckduckgo.com/html/?q={query}"
# User agent to mimic a browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}
# Selectors to find result elements (these might change if DDG updates its HTML structure)
RESULT_SELECTOR = "div.result" # Main container for each result
TITLE_SELECTOR = "h2.result__title a.result__a" # Link containing the title
SNIPPET_SELECTOR = "a.result__snippet" # Snippet text container
URL_SELECTOR = "a.result__url" # URL display element (needs cleaning)
# --- End Constants ---


class WebSearchTool(BaseTool):
    """
    Performs a web search using DuckDuckGo's HTML interface and returns scraped results.
    Useful for finding current information, research, or code examples online.
    NOTE: Relies on HTML scraping and may break if DuckDuckGo changes its page structure.
    """
    name: str = "web_search"
    description: str = (
        "Searches the web using DuckDuckGo (HTML scraping) for a given query and returns a specified "
        "number of results (title, URL, snippet). Useful for finding up-to-date information. "
        "Results depend on parsing website structure and might be inconsistent."
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
            description="The approximate maximum number of search results to return (e.g., 3, 5). Defaults to 3. Fewer may be returned.",
            required=False,
        ),
    ]

    async def _get_html(self, url: str) -> Optional[str]:
        """Fetches HTML content from a URL using aiohttp."""
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(url, timeout=15) as response: # Add timeout
                    response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
                    return await response.text()
        except aiohttp.ClientError as e:
            logger.error(f"HTTP Client Error fetching {url}: {e}")
            return None
        except asyncio.TimeoutError:
             logger.error(f"Timeout error fetching {url}")
             return None
        except Exception as e:
            logger.error(f"Unexpected error fetching {url}: {e}", exc_info=True)
            return None

    async def _parse_results(self, html_content: str, num_results: int) -> List[Dict[str, str]]:
        """Parses the DuckDuckGo HTML to extract search results."""
        results = []
        try:
            soup = BeautifulSoup(html_content, 'lxml') # Use lxml parser if available, falls back to html.parser
            result_divs = soup.select(RESULT_SELECTOR) # Find all result containers

            for i, result_div in enumerate(result_divs):
                if i >= num_results: # Stop if we have enough results
                    break

                title_tag = result_div.select_one(TITLE_SELECTOR)
                snippet_tag = result_div.select_one(SNIPPET_SELECTOR)
                url_tag = result_div.select_one(URL_SELECTOR)

                title = title_tag.get_text(strip=True) if title_tag else "N/A"
                # Extract URL from the title link if possible, otherwise try the URL tag
                url = title_tag['href'] if title_tag and title_tag.has_attr('href') else None
                if not url and url_tag: # Fallback to url_tag if title link fails
                     url_text = url_tag.get_text(strip=True)
                     # Clean the displayed URL text (remove protocol, trailing slash if present)
                     url = "https://" + url_text.strip() # Assume https if missing

                snippet = snippet_tag.get_text(strip=True) if snippet_tag else "N/A"

                if title != "N/A" and url: # Only add if we have a title and URL
                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet
                    })
            return results
        except Exception as e:
            logger.error(f"Error parsing search results HTML: {e}", exc_info=True)
            return [] # Return empty list on parsing error


    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Executes the web search by fetching and scraping DuckDuckGo HTML results.

        Args:
            agent_id (str): The ID of the agent calling the tool.
            agent_sandbox_path (Path): Not used by this tool.
            **kwargs: Arguments containing 'query' and optionally 'num_results'.

        Returns:
            str: A formatted string containing the search results or an error message.
        """
        query = kwargs.get("query")
        num_results_str = kwargs.get("num_results", "3")

        if not query:
            return "Error: 'query' parameter is required for web search."

        try:
            num_results = int(num_results_str)
            if num_results <= 0: num_results = 3
        except ValueError:
            logger.warning(f"Invalid 'num_results' value '{num_results_str}' provided by {agent_id}. Defaulting to 3.")
            num_results = 3

        logger.info(f"Agent {agent_id} performing web search (scraping) for: '{query}' (max results: {num_results})")

        # URL encode the query
        encoded_query = urllib.parse.quote_plus(query)
        search_url = SEARCH_URL_TEMPLATE.format(query=encoded_query)

        # Fetch HTML content
        html_content = await self._get_html(search_url)
        if not html_content:
            return f"Error: Failed to fetch search results page from DuckDuckGo for query: '{query}'"

        # Parse results
        parsed_results = await self._parse_results(html_content, num_results)

        if not parsed_results:
            return f"No valid search results found or parsed for query: '{query}'"

        # Format the results for the agent
        formatted_output = [
            f"Title: {res['title']}\n"
            f"URL: {res['url']}\n"
            f"Snippet: {res['snippet']}\n---"
            for res in parsed_results
        ]
        logger.info(f"Web search (scraping) for '{query}' completed successfully for {agent_id}.")
        return f"Search Results for '{query}':\n\n" + "\n".join(formatted_output)
