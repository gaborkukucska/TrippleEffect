# START OF FILE src/tools/web_search.py
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp # For fallback scraping HTTP requests
from bs4 import BeautifulSoup # For fallback scraping HTML parsing
import urllib.parse # For fallback scraping URL encoding

# --- New Imports ---
from src.tools.base import BaseTool, ToolParameter
from src.config.settings import settings # To access API keys
try:
    from tavily import TavilyClient # Import Tavily client
    TAVILY_AVAILABLE = True
except ImportError:
    TavilyClient = None # Define as None if import fails
    TAVILY_AVAILABLE = False
# --- End New Imports ---

logger = logging.getLogger(__name__)

# --- Constants for Scraping Fallback ---
SEARCH_URL_TEMPLATE = "https://html.duckduckgo.com/html/?q={query}"
HEADERS = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36" }
RESULT_SELECTOR = "div.result"
TITLE_SELECTOR = "h2.result__title a.result__a"
SNIPPET_SELECTOR = "a.result__snippet"
URL_SELECTOR = "a.result__url"
# --- End Scraping Constants ---


class WebSearchTool(BaseTool):
    """
    Performs a web search. Uses the Tavily Search API if TAVILY_API_KEY is configured in .env.
    Otherwise, falls back to scraping DuckDuckGo's HTML interface.
    Returns a specified number of results (title, URL, content/snippet).
    """
    name: str = "web_search"
    auth_level: str = "worker" # Accessible by all
    summary: Optional[str] = "Performs a web search using Tavily API or DuckDuckGo scraping."
    description: str = ( # Updated description
        "Searches the web for a given query and returns a specified number of results "
        "(title, URL, content/snippet). Uses the Tavily API if configured (recommended), "
        "otherwise falls back to scraping DuckDuckGo HTML (less reliable). "
        "Useful for finding up-to-date information."
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
         # --- NEW: Tavily Specific Parameter ---
         ToolParameter(
            name="search_depth",
            type="string",
            description="Optional (Tavily API only): Search depth ('basic' or 'advanced'). Defaults to 'basic'. Advanced performs more in-depth research.",
            required=False,
        ),
        # --- End NEW Parameter ---
    ]

    # --- Tavily API Call Method ---
    async def _search_with_tavily(self, query: str, num_results: int, search_depth: str) -> Optional[List[Dict[str, Any]]]:
        """Attempts to perform search using the Tavily API."""
        if not TAVILY_AVAILABLE:
             logger.debug("Tavily library not installed. Cannot use Tavily API.")
             return None
        if not settings.TAVILY_API_KEY:
             logger.debug("TAVILY_API_KEY not found in settings. Cannot use Tavily API.")
             return None

        logger.info(f"Attempting web search using Tavily API. Depth: {search_depth}, Max Results: {num_results}")
        try:
            tavily = TavilyClient(api_key=settings.TAVILY_API_KEY)
            # Tavily search is synchronous, run it in a thread
            response = await asyncio.to_thread(
                tavily.search,
                query=query,
                search_depth=search_depth,
                max_results=num_results
            )
            # Extract relevant results (Tavily format might be slightly different)
            # Usually response['results'] is a list of dicts like {'title': ..., 'url': ..., 'content': ...}
            if isinstance(response, dict) and "results" in response and isinstance(response["results"], list):
                 logger.info(f"Tavily search successful, received {len(response['results'])} results.")
                 # Standardize keys slightly if needed (Tavily usually uses 'content')
                 standardized_results = [
                     {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")} # Use 'content' as 'snippet'
                     for r in response["results"]
                     if r.get("title") and r.get("url") # Ensure basic fields exist
                 ]
                 return standardized_results
            else:
                 logger.warning(f"Tavily API returned unexpected response format: {type(response)}")
                 return None
        except Exception as e:
            logger.error(f"Error during Tavily API search for query '{query}': {e}", exc_info=True)
            return None # Signal failure to fallback

    # --- Scraping Methods (Fallback) ---
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

    # --- Detailed Usage Method ---
    def get_detailed_usage(self) -> str:
        """Returns detailed usage instructions for the WebSearchTool."""
        usage = """
        **Tool Name:** web_search

        **Description:** Performs a web search using the Tavily API (if configured with TAVILY_API_KEY) or falls back to scraping DuckDuckGo. Returns a list of search results including title, URL, and snippet/content.

        **Parameters:**

        *   `<query>` (string, required): The search query.
        *   `<num_results>` (integer, optional): The maximum number of results to return. Defaults to 3.
        *   `<search_depth>` (string, optional, Tavily API only): How deep the search should go. Options: 'basic' (default) or 'advanced'. 'advanced' performs more in-depth research but takes longer and uses more credits if applicable.

        **Example (Basic Search):**
        ```xml
        <web_search>
          <query>latest advancements in AI agents</query>
          <num_results>5</num_results>
        </web_search>
        ```

        **Example (Advanced Tavily Search):**
        ```xml
        <web_search>
          <query>detailed comparison of LLM frameworks</query>
          <num_results>7</num_results>
          <search_depth>advanced</search_depth>
        </web_search>
        ```

        **Important Notes:**
        *   Requires `TAVILY_API_KEY` in the environment for Tavily search (recommended).
        *   DuckDuckGo scraping fallback is less reliable and may break if DDG changes its HTML structure.
        """
        return usage.strip()

    async def _parse_results(self, html_content: str, num_results: int) -> List[Dict[str, str]]:
        """Parses the DuckDuckGo HTML to extract search results."""
        results = []
        try:
            soup = BeautifulSoup(html_content, 'lxml') # Use lxml parser if available, falls back to html.parser
            result_divs = soup.select(RESULT_SELECTOR) # Find all result containers

            for i, result_div in enumerate(result_divs):
                if i >= num_results: break # Stop if we have enough results

                title_tag = result_div.select_one(TITLE_SELECTOR)
                snippet_tag = result_div.select_one(SNIPPET_SELECTOR)
                url_tag = result_div.select_one(URL_SELECTOR)

                title = title_tag.get_text(strip=True) if title_tag else "N/A"
                url = title_tag['href'] if title_tag and title_tag.has_attr('href') else None
                if not url and url_tag:
                     url_text = url_tag.get_text(strip=True)
                     url = "https://" + url_text.strip()

                snippet = snippet_tag.get_text(strip=True) if snippet_tag else "N/A"

                if title != "N/A" and url:
                    results.append({ "title": title, "url": url, "snippet": snippet })
            return results
        except Exception as e:
            logger.error(f"Error parsing search results HTML: {e}", exc_info=True)
            return []

    async def _search_with_scraping(self, query: str, num_results: int) -> Optional[List[Dict[str, str]]]:
         """Performs search by scraping DuckDuckGo HTML."""
         logger.info(f"Performing web search using DDG HTML scraping fallback.")
         encoded_query = urllib.parse.quote_plus(query)
         search_url = SEARCH_URL_TEMPLATE.format(query=encoded_query)
         html_content = await self._get_html(search_url)
         if not html_content:
            logger.error(f"Failed to fetch search results page from DuckDuckGo for query: '{query}'")
            return None
         parsed_results = await self._parse_results(html_content, num_results)
         return parsed_results

    # --- Main Execute Method ---
    async def execute(self, agent_id: str, agent_sandbox_path: Path, **kwargs: Any) -> Any:
        """
        Executes the web search. Tries Tavily API first if configured, otherwise falls back to scraping.
        """
        query = kwargs.get("query")
        num_results_str = kwargs.get("num_results", "3")
        search_depth = kwargs.get("search_depth", "basic").lower()
        if search_depth not in ["basic", "advanced"]: search_depth = "basic"

        if not query:
            return "Error: 'query' parameter is required for web search."

        try:
            num_results = int(num_results_str);
            if num_results <= 0: num_results = 3
        except ValueError:
            logger.warning(f"Invalid 'num_results' value '{num_results_str}' provided by {agent_id}. Defaulting to 3.")
            num_results = 3

        logger.info(f"Agent {agent_id} performing web search for: '{query}' (Depth: {search_depth}, Max Results: {num_results})")

        # 1. Try Tavily API
        api_results = await self._search_with_tavily(query, num_results, search_depth)

        search_results = []
        source = ""

        if api_results is not None: # Tavily succeeded (might be empty list)
             search_results = api_results
             source = "Tavily API"
        else: # Tavily failed or not configured, try scraping
             logger.warning(f"Tavily search failed or unavailable for query '{query}', falling back to DDG scraping.")
             scrape_results = await self._search_with_scraping(query, num_results)
             if scrape_results is not None:
                 search_results = scrape_results
                 source = "DuckDuckGo Scraping"
             else: # Both failed
                  error_msg = f"Error: Failed to retrieve search results from both Tavily API and DuckDuckGo scraping for query: '{query}'"
                  logger.error(error_msg)
                  return error_msg

        # Format results
        if not search_results:
            return f"No search results found via {source} for query: '{query}'"

        formatted_output = [
            f"Title: {res.get('title', 'N/A')}\n"
            f"URL: {res.get('url', 'N/A')}\n"
            f"Snippet: {res.get('snippet', 'N/A')}\n---"
            for i, res in enumerate(search_results) if i < num_results # Ensure max results respected
        ]
        logger.info(f"Web search for '{query}' completed successfully for {agent_id} via {source}.")
        return f"Search Results for '{query}' (Source: {source}):\n\n" + "\n".join(formatted_output)
