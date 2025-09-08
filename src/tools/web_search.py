# START OF FILE src/tools/web_search.py
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp
from bs4 import BeautifulSoup
import urllib.parse

from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings
try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    TavilyClient = None
    TAVILY_AVAILABLE = False

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
        ToolParameter(name="search_depth", type="string", description="Tavily only: 'basic' or 'advanced'.", required=False),
    ]

    async def execute(self, agent_id: str, **kwargs: Any) -> Dict[str, Any]:
        query = kwargs.get("query")
        if not query:
            return {"status": "error", "message": "'query' parameter is required."}

        num_results = int(kwargs.get("num_results", 3))
        search_depth = kwargs.get("search_depth", "basic").lower()

        results, source = await self._perform_search(query, num_results, search_depth)

        if results is None:
            return {"status": "error", "message": f"Failed to retrieve search results for query: '{query}'"}

        return {"status": "success", "source": source, "results": results}

    async def _perform_search(self, query: str, num_results: int, search_depth: str) -> (Optional[List[Dict]], str):
        if TAVILY_AVAILABLE and settings.TAVILY_API_KEY:
            try:
                tavily = TavilyClient(api_key=settings.TAVILY_API_KEY)
                response = await asyncio.to_thread(tavily.search, query=query, search_depth=search_depth, max_results=num_results)
                if "results" in response:
                    return [self._standardize_result(r) for r in response["results"]], "Tavily API"
            except Exception as e:
                logger.error(f"Tavily API search failed: {e}", exc_info=True)

        return await self._search_with_scraping(query, num_results), "DuckDuckGo Scraping"

    def _standardize_result(self, result: Dict) -> Dict:
        return {"title": result.get("title"), "url": result.get("url"), "snippet": result.get("content")}

    async def _search_with_scraping(self, query: str, num_results: int) -> Optional[List[Dict]]:
        encoded_query = urllib.parse.quote_plus(query)
        search_url = SEARCH_URL_TEMPLATE.format(query=encoded_query)

        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(search_url, timeout=15) as response:
                    response.raise_for_status()
                    html_content = await response.text()

            soup = BeautifulSoup(html_content, 'lxml')
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
        Performs a web search using the Tavily API if available, otherwise falls back to scraping DuckDuckGo. Returns a list of search results including title, URL, and snippet.

        **Parameters:**

        *   `<query>` (string, required): The search query. Be specific for better results.
        *   `<num_results>` (integer, optional): The maximum number of search results to return. Defaults to 3.
        *   `<search_depth>` (string, optional): (Tavily API only) The depth of the search. Can be 'basic' or 'advanced'. 'Advanced' is more thorough but slower and uses more credits. Defaults to 'basic'.

        **Example XML Call:**

        *   To perform a basic search for 'Python async programming':
            ```xml
            <web_search>
              <query>Python async programming best practices</query>
              <num_results>5</num_results>
            </web_search>
            ```

        *   To perform an advanced search using the Tavily API:
            ```xml
            <web_search>
              <query>latest advancements in large language models</query>
              <search_depth>advanced</search_depth>
            </web_search>
            ```
        """
        return usage.strip()
