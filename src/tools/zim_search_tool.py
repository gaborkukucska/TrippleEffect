# src/tools/zim_search_tool.py
#
# Kiwix ZIM Search Tool for TrippleEffect
#
# DROP THIS FILE INTO: src/tools/zim_search_tool.py
#
# DEPENDENCIES (add to requirements.txt):
#   libzim>=3.4.0
#   beautifulsoup4>=4.12.0
#
# ENVIRONMENT VARIABLES (add to .env):
#   ZIM_FILES_PATH=/path/to/your/zim/files   # directory containing .zim files
#                                             # OR a single .zim file path
#
# The tool uses libzim's built-in Xapian full-text index for fast on-the-fly
# search — no preprocessing or vector DB required. Works with any .zim file
# that has an embedded search index (Wikipedia, Stack Overflow, etc.).

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import libzim
    LIBZIM_AVAILABLE = True
except ImportError:
    LIBZIM_AVAILABLE = False
    logger.warning("libzim not installed. ZimSearchTool will be unavailable. Run: pip install libzim")

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    logger.warning("beautifulsoup4 not installed. HTML stripping will be basic. Run: pip install beautifulsoup4")

from src.tools.base import BaseTool, ToolParameter


def _strip_html(html: str) -> str:
    """Strip HTML tags and return clean plain text."""
    if BS4_AVAILABLE:
        soup = BeautifulSoup(html, "html.parser")
        # Remove script/style blocks entirely
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)
    else:
        # Basic fallback: strip tags with a simple approach
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        return " ".join(text.split())


class ZimSearchTool(BaseTool):
    """
    Search Kiwix .zim files on-the-fly using their built-in Xapian full-text
    index. No preprocessing required — results are returned directly from the
    compressed archive in milliseconds.
    """

    name: str = "zim_search"
    description: str = (
        "Search offline Kiwix knowledge bases (.zim files) such as Wikipedia, "
        "Stack Overflow, Project Gutenberg, medical references, and more. "
        "Use this tool to look up factual information, technical answers, "
        "encyclopaedic content, or any topic that may be in the available ZIM "
        "archives. Returns article titles and text snippets. "
        "Prefer this over web search when the topic is encyclopaedic or "
        "technical and an offline knowledge base is likely to contain the answer."
    )
    parameters: List[ToolParameter] = [
        ToolParameter(
            name="query",
            type="string",
            description="The search query — a keyword phrase or natural language question.",
            required=True,
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description=(
                "Maximum number of articles to return. Defaults to 3. "
                "Use 1 for a quick lookup, up to 5 for broader research."
            ),
            required=False,
        ),
        ToolParameter(
            name="zim_file",
            type="string",
            description=(
                "Optional filename (e.g. 'wikipedia_en_all.zim') to search a "
                "specific archive. If omitted, all available ZIM files are searched."
            ),
            required=False,
        ),
        ToolParameter(
            name="snippet_length",
            type="integer",
            description=(
                "Maximum number of characters to return per article snippet. "
                "Defaults to 1500. Increase for more context, decrease to save tokens."
            ),
            required=False,
        ),
    ]
    usage_example: str = (
        "<![CDATA["
        "<zim_search>"
        "<query>photosynthesis light reactions</query>"
        "<max_results>3</max_results>"
        "</zim_search>"
        "]]>"
    )

    # ------------------------------------------------------------------ #
    # Internal cache: {zim_path_str -> libzim.Archive}                    #
    # Archives are kept open for the lifetime of the process — libzim     #
    # only decompresses blocks that are actually read, so memory usage     #
    # stays low even for 80 GB Wikipedia archives.                        #
    # ------------------------------------------------------------------ #
    _archive_cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #

    def _get_zim_paths(self, zim_file: Optional[str] = None) -> List[Path]:
        """Resolve which ZIM files to search."""
        zim_env = os.environ.get("ZIM_FILES_PATH", "")
        if not zim_env:
            return []

        base = Path(zim_env)

        if base.is_file() and base.suffix == ".zim":
            # Single file pointed to directly
            if zim_file and base.name != zim_file:
                return []
            return [base]

        if base.is_dir():
            if zim_file:
                candidate = base / zim_file
                return [candidate] if candidate.is_file() else []
            return sorted(base.glob("*.zim"))

        return []

    def _get_archive(self, path: Path) -> Optional[Any]:
        """Open (or return cached) a libzim Archive."""
        key = str(path)
        if key not in self._archive_cache:
            try:
                self._archive_cache[key] = libzim.Archive(key)
                logger.info(f"ZimSearchTool: opened archive {path.name}")
            except Exception as exc:
                logger.error(f"ZimSearchTool: failed to open {path}: {exc}")
                return None
        return self._archive_cache[key]

    def _search_archive(
        self,
        archive: Any,
        archive_name: str,
        query: str,
        max_results: int,
        snippet_length: int,
    ) -> List[Dict[str, str]]:
        """Run a full-text search against a single archive (blocking)."""
        results = []
        try:
            searcher = libzim.Searcher(archive)
            q = libzim.Query().set_query(query)
            search = searcher.search(q)
            hits = search.getResults(0, max_results)

            for entry in hits:
                try:
                    item = entry.get_item()
                    mime = item.mimetype or ""
                    if not mime.startswith("text/html"):
                        continue
                    raw = bytes(item.content).decode("utf-8", errors="ignore")
                    text = _strip_html(raw)
                    snippet = text[:snippet_length].strip()
                    if not snippet:
                        continue
                    results.append({
                        "source": archive_name,
                        "title": entry.title or entry.path,
                        "path": entry.path,
                        "snippet": snippet,
                    })
                except Exception as article_exc:
                    logger.debug(f"ZimSearchTool: skipping entry in {archive_name}: {article_exc}")
                    continue

        except Exception as search_exc:
            logger.error(f"ZimSearchTool: search failed in {archive_name}: {search_exc}")

        return results

    def _format_results(self, results: List[Dict[str, str]]) -> str:
        """Format search results into a readable string for the agent."""
        if not results:
            return "No results found in the available ZIM archives for that query."

        lines = [f"Found {len(results)} result(s) from ZIM archives:\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"--- Result {i} ---")
            lines.append(f"Source : {r['source']}")
            lines.append(f"Title  : {r['title']}")
            lines.append(f"Snippet:\n{r['snippet']}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Main entry point                                                     #
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any,
    ) -> str:

        if not LIBZIM_AVAILABLE:
            return (
                "Error: libzim is not installed. "
                "Please run 'pip install libzim' and restart TrippleEffect."
            )

        query: str = kwargs.get("query", "").strip()
        if not query:
            return "Error: 'query' parameter is required."

        max_results: int = int(kwargs.get("max_results") or 3)
        max_results = max(1, min(max_results, 10))  # clamp 1–10

        snippet_length: int = int(kwargs.get("snippet_length") or 1500)
        snippet_length = max(200, min(snippet_length, 5000))  # clamp 200–5000

        zim_file: Optional[str] = kwargs.get("zim_file", None)

        zim_paths = self._get_zim_paths(zim_file)

        if not zim_paths:
            env_val = os.environ.get("ZIM_FILES_PATH", "<not set>")
            return (
                f"Error: No ZIM files found. "
                f"ZIM_FILES_PATH is currently '{env_val}'. "
                f"Set it to a directory containing .zim files or a direct path to a .zim file."
            )

        logger.info(
            f"ZimSearchTool: agent={agent_id} query='{query}' "
            f"archives={[p.name for p in zim_paths]}"
        )

        all_results: List[Dict[str, str]] = []

        for zim_path in zim_paths:
            archive = self._get_archive(zim_path)
            if archive is None:
                continue

            # Run blocking libzim search in a thread so we don't block the event loop
            archive_results = await asyncio.to_thread(
                self._search_archive,
                archive,
                zim_path.name,
                query,
                max_results,
                snippet_length,
            )
            all_results.extend(archive_results)

            # Stop early if we already have enough results across all archives
            if len(all_results) >= max_results:
                break

        # Trim to requested count
        all_results = all_results[:max_results]

        return self._format_results(all_results)

    # ------------------------------------------------------------------ #
    # Modular help (reduces token usage for agents requesting docs)       #
    # ------------------------------------------------------------------ #

    def get_detailed_usage(
        self,
        agent_context: Optional[Dict] = None,
        sub_action: Optional[str] = None,
    ) -> str:
        common_header = """
**Tool Name:** zim_search
**Description:** Search offline Kiwix .zim knowledge bases (Wikipedia, Stack Overflow, etc.)
  on-the-fly using their built-in full-text index. Fast, no preprocessing needed.
"""
        action_details = {
            "search": """
**Action: search (default)**
Performs a full-text search across one or all available ZIM archives.

*   `<query>` (string, required): Keyword phrase or natural language question.
*   `<max_results>` (integer, optional, default 3): Number of articles to return (1–10).
*   `<zim_file>` (string, optional): Filename of a specific archive to search,
    e.g. 'wikipedia_en_all.zim'. Omit to search all available archives.
*   `<snippet_length>` (integer, optional, default 1500): Characters per snippet (200–5000).

**Example:**
<zim_search>
  <query>black hole event horizon</query>
  <max_results>2</max_results>
  <zim_file>wikipedia_en_all.zim</zim_file>
</zim_search>
""",
        }

        if sub_action and sub_action in action_details:
            return common_header + action_details[sub_action]

        return common_header + """
**Available Actions Summary:**
1.  **search:** Full-text search across ZIM archives, returns article snippets.

**To get detailed instructions, call:**
<tool_information>
  <action>get_info</action>
  <tool_name>zim_search</tool_name>
  <sub_action>search</sub_action>
</tool_information>
"""
