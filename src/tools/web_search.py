# START OF FILE src/tools/web_search.py
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import aiohttp
from bs4 import BeautifulSoup
import urllib.parse

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings

logger = logging.getLogger(__name__)

SEARCH_URL_TEMPLATE = "https://html.duckduckgo.com/html/?q={query}"
HEADERS = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36" }
RESULT_SELECTOR = "div.result"
TITLE_SELECTOR = "h2.result__title a.result__a"
SNIPPET_SELECTOR = "a.result__snippet"
URL_SELECTOR = "a.result__url"

class WebSearchTool(BaseTool):
    name: str = "web_search"
    auth_level: str = "worker"
    summary: Optional[str] = "Performs a web search."
    description: str = "Searches the web for a given query."
    parameters: List[ToolParameter] = [
        ToolParameter(name="query", type="string", description="The search query.", required=True),
        ToolParameter(name="num_results", type="integer", description="Max number of results (default 3).", required=False),
        ToolParameter(name="engines", type="string", description="Optional comma-separated list of engines (e.g., 'google,bing').", required=False),
        ToolParameter(name="time_range", type="string", description="Optional time range (e.g. 'day', 'week', 'month', 'year').", required=False),
        ToolParameter(name="language", type="string", description="Optional language code (e.g. 'en', 'fr').", required=False),
    ]

    async def execute(self, agent_id: str, **kwargs: Any) -> Dict[str, Any]: # type: ignore[reportIncompatibleMethodOverride]
        query = kwargs.get("query")
        
        # Enhanced validation with better error messages
        if not query:
            return {
                "status": "error", 
                "message": "Missing required 'query' parameter. You must provide a search query.",
                "error_type": "missing_parameter",
                "suggestions": [
                    "Include the topic or question you want to search for",
                    "Use specific keywords for better results",
                    "Example: 'Python async programming best practices'"
                ]
            }
        
        if not query.strip():
            return {
                "status": "error",
                "message": "Search query cannot be empty or contain only whitespace.",
                "error_type": "invalid_parameter",
                "suggestions": [
                    "Provide a meaningful search query",
                    "Use specific terms related to your research topic"
                ]
            }

        try:
            num_results = int(kwargs.get("num_results", 3))
            if num_results <= 0 or num_results > 20:
                return {
                    "status": "error",
                    "message": f"Invalid num_results '{num_results}'. Must be between 1 and 20.",
                    "error_type": "invalid_parameter",
                    "suggestions": [
                        "Use a number between 1 and 20 for num_results",
                        "Default is 3 if not specified"
                    ]
                }
        except (ValueError, TypeError):
            return {
                "status": "error",
                "message": f"Invalid num_results parameter. Must be a number.",
                "error_type": "invalid_parameter",
                "suggestions": [
                    "Use a whole number for num_results (e.g., 3, 5, 10)",
                    "Default is 3 if not specified"
                ]
            }

        engines = kwargs.get("engines")
        time_range = kwargs.get("time_range")
        language = kwargs.get("language", "en")

        logger.info(f"Agent {agent_id} performing web search for: '{query[:100]}...' (num_results={num_results})")

        try:
            results, source = await self._perform_search(query, num_results, engines, time_range, language)

            if results is None:
                return {
                    "status": "error", 
                    "message": f"Failed to retrieve search results for query: '{query}'",
                    "error_type": "execution_error",
                    "suggestions": [
                        "Try a different search query",
                        "Check your internet connection",
                        "Wait a moment and try again"
                    ]
                }

            if not results:
                return {
                    "status": "success", 
                    "message": f"No search results found for query: '{query}'",
                    "source": source, 
                    "results": [],
                    "suggestions": [
                        "Try using different keywords",
                        "Make your query more specific or more general",
                        "Check for spelling errors in your query"
                    ]
                }

            logger.info(f"Agent {agent_id} retrieved {len(results)} search results from {source}")
            return {"status": "success", "source": source, "results": results, "message": f"Found {len(results)} search results"}
            
        except Exception as e:
            logger.error(f"Web search execution error for agent {agent_id}: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Web search failed: {type(e).__name__} - {str(e)}",
                "error_type": "execution_error",
                "suggestions": [
                    "Try again with a simpler query",
                    "Check your internet connection",
                    "Contact support if the problem persists"
                ]
            }

    async def _perform_search(self, query: str, num_results: int, engines: Optional[str] = None, time_range: Optional[str] = None, language: str = "en") -> Tuple[Optional[List[Dict[str, Any]]], str]:
        if settings.SEARXNG_URL:
            try:
                base_url = settings.SEARXNG_URL.rstrip('/')
                search_url = f"{base_url}/search"
                params = {
                    "q": query,
                    "format": settings.SEARXNG_FORMAT,
                    "language": language,
                }
                if engines: params["engines"] = engines
                if time_range: params["time_range"] = time_range

                async with aiohttp.ClientSession() as session:
                    async with session.get(search_url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as response:
                        response.raise_for_status()
                        data = await response.json()
                
                results = []
                # SearXNG returns 'results' list
                for r in data.get("results", [])[:num_results]:
                    results.append({
                        "title": r.get("title", "N/A"),
                        "url": r.get("url", "N/A"),
                        "snippet": r.get("content", "N/A")
                    })
                return results, "SearXNG API"
            except Exception as e:
                logger.error(f"SearXNG API search failed: {e}", exc_info=True)
                # Fallback to scraping if SearXNG fails

        return await self._search_with_scraping(query, num_results), "DuckDuckGo Scraping"

    async def _search_with_scraping(self, query: str, num_results: int) -> Optional[List[Dict]]:
        encoded_query = urllib.parse.quote_plus(query)
        search_url = SEARCH_URL_TEMPLATE.format(query=encoded_query)

        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(search_url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    response.raise_for_status()
                    html_content = await response.text()

            soup = BeautifulSoup(html_content, 'html.parser')
            result_divs = soup.select(RESULT_SELECTOR)

            results = []
            for div in result_divs[:num_results]:
                title_tag = div.select_one(TITLE_SELECTOR)
                snippet_tag = div.select_one(SNIPPET_SELECTOR)
                url_tag = div.select_one(URL_SELECTOR)

                title = title_tag.get_text(strip=True) if title_tag else "N/A"
                url = title_tag['href'] if title_tag and title_tag.has_attr('href') else f"https://{url_tag.get_text(strip=True)}" if url_tag else "N/A"
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else "N/A"

                if title != "N/A" and url != "N/A":
                    results.append({"title": title, "url": url, "snippet": snippet})
            return results
        except Exception as e:
            logger.error(f"DDG scraping failed: {e}", exc_info=True)
            return None

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """Returns detailed usage instructions for the WebSearchTool."""
        usage = """
        **Tool Name:** web_search

        **Description:**
        Performs a web search using the SearXNG API if configured, otherwise falls back to scraping DuckDuckGo. Returns a list of search results including title, URL, and snippet.

        **Parameters:**

        *   `<query>` (string, required): The search query. Be specific for better results.
        *   `<num_results>` (integer, optional): The maximum number of search results to return. Defaults to 3.
        *   `<engines>` (string, optional): (SearXNG only) Comma-separated list of engines (e.g. 'google,bing,wikipedia').
        *   `<time_range>` (string, optional): (SearXNG only) Time range ('day', 'week', 'month', 'year').
        *   `<language>` (string, optional): Language code (e.g. 'en'). Defaults to 'en'.

        **Example XML Call:**

        *   To perform a basic search for 'Python async programming':
            ```xml
            <web_search>
              <query>Python async programming best practices</query>
              <num_results>5</num_results>
            </web_search>
            ```

        *   To perform a specialized search:
            ```xml
            <web_search>
              <query>latest python release details</query>
              <engines>google,duckduckgo</engines>
              <time_range>month</time_range>
            </web_search>
            ```
        """
        return usage.strip()
