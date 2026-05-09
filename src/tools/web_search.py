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
    description: str = "Searches the web for a given query or fetches the full text content of a specific web page."
    parameters: List[ToolParameter] = [
        ToolParameter(name="action", type="string", description="The action to perform: 'search' or 'get_page'. Defaults to 'search'.", required=False),
        ToolParameter(name="query", type="string", description="The search query (required if action='search').", required=False),
        ToolParameter(name="url", type="string", description="The URL to fetch (required if action='get_page').", required=False),
        ToolParameter(name="num_results", type="integer", description="Max number of results (default 3, for search).", required=False),
        ToolParameter(name="engines", type="string", description="Optional comma-separated list of engines (e.g., 'google,bing').", required=False),
        ToolParameter(name="time_range", type="string", description="Optional time range (e.g. 'day', 'week', 'month', 'year').", required=False),
        ToolParameter(name="language", type="string", description="Optional language code (e.g. 'en', 'fr').", required=False),
    ]

    async def execute(self, agent_id: str, **kwargs: Any) -> Dict[str, Any]: # type: ignore[reportIncompatibleMethodOverride]
        action = kwargs.get("action", "search").strip().lower()

        if action == "get_page":
            return await self._get_page_content(agent_id, kwargs.get("url"))

        # --- Search Logic ---
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
            results, source, unresponsive_engines = await self._perform_search(query, num_results, engines, time_range, language)

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
                suggestions = [
                    "Try using different keywords",
                    "Make your query more specific or more general",
                    "Check for spelling errors in your query"
                ]
                message = f"No search results found for query: '{query}'"

                if unresponsive_engines:
                    try:
                        unresponsive_info = ", ".join([f"{e[0]} ({e[1]})" for e in unresponsive_engines if len(e) >= 2])
                        if unresponsive_info:
                            message += f"\n\nNote: The following engines were unresponsive or blocked the request: {unresponsive_info}."
                            if engines:
                                message += f"\nSince you explicitly requested engines '{engines}', they might be blocking the request."
                                suggestions.insert(0, "Omit the 'engines' parameter to allow SearXNG to use all available engines automatically (RECOMMENDED)")
                                suggestions.insert(1, "Try specifying different engines like 'duckduckgo', 'bing', or 'startpage'")
                    except Exception as parse_err:
                        logger.warning(f"Failed to parse unresponsive_engines: {parse_err}")

                return {
                    "status": "success", 
                    "message": message,
                    "source": source, 
                    "results": [],
                    "suggestions": suggestions
                }

            logger.info(f"Agent {agent_id} retrieved {len(results)} search results from {source}")
            
            # Format results for the LLM agent context
            formatted_text = f"Found {len(results)} search results from {source}:\n\n"
            for i, res in enumerate(results, 1):
                formatted_text += f"--- Result {i} ---\n"
                formatted_text += f"Title: {res.get('title', 'N/A')}\n"
                formatted_text += f"URL: {res.get('url', 'N/A')}\n"
                formatted_text += f"Snippet: {res.get('snippet', 'N/A')}\n\n"
                
            return {"status": "success", "source": source, "results": results, "message": formatted_text.strip()}
            
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

    async def _perform_search(self, query: str, num_results: int, engines: Optional[str] = None, time_range: Optional[str] = None, language: str = "en") -> Tuple[Optional[List[Dict[str, Any]]], str, List[Any]]:
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
                unresponsive = data.get("unresponsive_engines", [])
                return results, "SearXNG API", unresponsive
            except Exception as e:
                logger.error(f"SearXNG API search failed: {e}", exc_info=True)
                # Fallback to scraping if SearXNG fails

        return await self._search_with_scraping(query, num_results), "DuckDuckGo Scraping", []

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

    async def _get_page_content(self, agent_id: str, url: Optional[str]) -> Dict[str, Any]:
        """Fetches the content of a web page and extracts readable text and links."""
        if not url:
            return {
                "status": "error",
                "message": "Missing required 'url' parameter for action='get_page'."
            }

        logger.info(f"Agent {agent_id} fetching web page: {url}")
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    response.raise_for_status()
                    html_content = await response.text()

            soup = BeautifulSoup(html_content, 'html.parser')

            # Remove scripts, styles, and non-content tags
            for element in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                element.decompose()

            # Extract text
            text = soup.get_text(separator='\n\n', strip=True)

            # Clean up excessive newlines
            lines = (line.strip() for line in text.splitlines())
            text = '\n'.join(line for line in lines if line)
            
            # Truncate text if it's too long (e.g. > 15k chars) to prevent context overflow
            if len(text) > 15000:
                text = text[:15000] + "\n\n...[CONTENT TRUNCATED DUE TO LENGTH]..."

            # Extract top 20 distinct links
            links = []
            seen_urls = set()
            for a_tag in soup.find_all('a', href=True):
                href = a_tag.get('href')
                if isinstance(href, list):
                    href = href[0]
                # Make relative URLs absolute
                absolute_url = urllib.parse.urljoin(url, str(href))
                # Filter out obvious non-web links and fragments
                if absolute_url.startswith(('http://', 'https://')) and '#' not in absolute_url:
                    if absolute_url not in seen_urls:
                        link_text = a_tag.get_text(strip=True)[:50]
                        if link_text:
                            links.append({"text": link_text, "url": absolute_url})
                            seen_urls.add(absolute_url)
                if len(links) >= 20:
                    break

            formatted_result = f"=== Page Content ({url}) ===\n\n{text}\n\n"
            if links:
                formatted_result += "=== Connected Links ===\n"
                for i, link in enumerate(links, 1):
                    formatted_result += f"[{i}] {link['text']} -> {link['url']}\n"

            return {
                "status": "success",
                "message": formatted_result
            }

        except Exception as e:
            logger.error(f"Failed to fetch page content {url}: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Failed to fetch page {url}: {type(e).__name__} - {str(e)}"
            }

    def get_detailed_usage(self, agent_context: Optional[Dict[str, Any]] = None, sub_action: Optional[str] = None) -> str:
        """Returns detailed usage instructions for the WebSearchTool."""
        common_header = (
            "**Tool Name:** web_search\n"
            "**Description:** Performs a web search or fetches the content of a specific web page. "
            "Returns search results or readable page text with connected links.\n"
        )
        
        action_details: Dict[str, str] = {
            "search": (
                "\n**Action: search**\n"
                "Perform a web search using SearXNG (or DuckDuckGo fallback).\n\n"
                "**Parameters:**\n"
                "* `<query>` (string, required): The search query.\n"
                "* `<num_results>` (integer, optional): Max results to return. Defaults to 3.\n"
                "* `<engines>` (string, optional): (SearXNG only) Comma-separated list of engines (e.g. 'google,bing').\n"
                "* `<time_range>` (string, optional): (SearXNG only) Time range ('day', 'week', 'month', 'year').\n\n"
                "**Example JSON:**\n"
                "```json\n"
                "{\n"
                "  \"action\": \"search\",\n"
                "  \"query\": \"Python async programming best practices\",\n"
                "  \"num_results\": 5\n"
                "}\n"
                "```\n"
            ),
            "get_page": (
                "\n**Action: get_page**\n"
                "Fetch the readable text content and absolute links of a specific URL.\n\n"
                "**Parameters:**\n"
                "* `<url>` (string, required): The absolute URL to fetch.\n\n"
                "**Example JSON:**\n"
                "```json\n"
                "{\n"
                "  \"action\": \"get_page\",\n"
                "  \"url\": \"https://docs.python.org/3/library/asyncio.html\"\n"
                "}\n"
                "```\n"
            )
        }

        if sub_action and sub_action in action_details:
            return common_header + action_details[sub_action]

        full_usage = common_header + "".join(action_details.values())
        return full_usage.strip()
