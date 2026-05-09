# START OF FILE src/tools/asset_search_tool.py
"""
TrippleEffect Tool: Asset Search & Download

Searches and downloads open-source, royalty-free assets across multiple
categories (sounds, 3D models, vector/bitmap images, icons, textures).

Supported sources with REST APIs:
  - Freesound      (sounds, CC-licensed)         -> requires FREESOUND_API_KEY
  - Poly Pizza     (3D models, CC0)               -> no key required
  - Sketchfab      (3D models, filterable by CC)  -> requires SKETCHFAB_API_KEY
  - Iconify        (SVG icons, open-source sets)  -> no key required
  - Unsplash       (photos, royalty-free)         -> requires UNSPLASH_ACCESS_KEY
  - Pixabay        (photos/vectors/illustrations) -> requires PIXABAY_API_KEY
  - PolyHaven      (HDRIs, textures, CC0)         -> no key required

Place this file in:  src/tools/asset_search_tool.py
Add to .env.example: FREESOUND_API_KEY, SKETCHFAB_API_KEY,
                     UNSPLASH_ACCESS_KEY, PIXABAY_API_KEY
Add to requirements.txt: aiohttp, aiofiles
"""

import asyncio
import json
import logging
import os
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import aiohttp

from src.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get(session: aiohttp.ClientSession, url: str, params: Dict = None) -> Dict:
    """Perform a GET request and return parsed JSON, or an error dict."""
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status == 200:
                return await r.json(content_type=None)
            return {"_error": f"HTTP {r.status}", "_url": str(r.url)}
    except Exception as exc:
        return {"_error": str(exc)}


async def _download_file(session: aiohttp.ClientSession, url: str, dest: Path) -> str:
    """Stream-download *url* to *dest*.  Returns a status string."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                return f"Error: HTTP {r.status} downloading {url}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(dest, "wb") as fh:
                async for chunk in r.content.iter_chunked(65536):
                    await fh.write(chunk)
        return f"Downloaded to {dest}"
    except Exception as exc:
        return f"Error: {exc}"


def _format_results(results: List[Dict]) -> str:
    """Pretty-print a list of asset result dicts."""
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.get('name', 'Untitled')}")
        if r.get("url"):
            lines.append(f"    URL      : {r['url']}")
        if r.get("preview"):
            lines.append(f"    Preview  : {r['preview']}")
        if r.get("license"):
            lines.append(f"    License  : {r['license']}")
        if r.get("tags"):
            lines.append(f"    Tags     : {r['tags']}")
        if r.get("download_url"):
            lines.append(f"    Download : {r['download_url']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Source-specific search functions
# ---------------------------------------------------------------------------

async def _search_freesound(
    session: aiohttp.ClientSession, query: str, limit: int
) -> List[Dict]:
    key = os.environ.get("FREESOUND_API_KEY", "")
    if not key:
        return [{"name": "⚠ FREESOUND_API_KEY not set", "url": "", "license": ""}]
    data = await _get(
        session,
        "https://freesound.org/apiv2/search/text/",
        {
            "query": query,
            "page_size": limit,
            "fields": "id,name,url,license,tags,previews",
            "token": key,
        },
    )
    if "_error" in data:
        return [{"name": f"Freesound error: {data['_error']}", "url": ""}]
    out = []
    for item in data.get("results", []):
        out.append(
            {
                "name": item.get("name", ""),
                "url": item.get("url", ""),
                "preview": item.get("previews", {}).get("preview-hq-mp3", ""),
                "license": item.get("license", ""),
                "tags": ", ".join(item.get("tags", [])[:6]),
                "download_url": (
                    f"https://freesound.org/apiv2/sounds/{item['id']}/download/"
                    f"?token={key}"
                    if item.get("id")
                    else ""
                ),
            }
        )
    return out


async def _search_polypizza(
    session: aiohttp.ClientSession, query: str, limit: int
) -> List[Dict]:
    data = await _get(
        session,
        "https://api.poly.pizza/v1/search",
        {"q": query, "limit": limit, "format": "glb"},
    )
    if "_error" in data:
        return [{"name": f"Poly Pizza error: {data['_error']}", "url": ""}]
    out = []
    for item in data.get("results", []):
        slug = item.get("id", "")
        out.append(
            {
                "name": item.get("title", ""),
                "url": f"https://poly.pizza/m/{slug}",
                "license": "CC0",
                "tags": ", ".join(item.get("tags", [])[:6]),
                "download_url": item.get("download", ""),
            }
        )
    return out


async def _search_sketchfab(
    session: aiohttp.ClientSession, query: str, limit: int
) -> List[Dict]:
    key = os.environ.get("SKETCHFAB_API_KEY", "")
    params: Dict = {
        "q": query,
        "count": limit,
        "license": "cc0",
        "downloadable": "true",
        "type": "models",
    }
    headers = {"Authorization": f"Token {key}"} if key else {}
    try:
        async with session.get(
            "https://api.sketchfab.com/v3/models",
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            data = await r.json(content_type=None) if r.status == 200 else {"_error": f"HTTP {r.status}"}
    except Exception as exc:
        data = {"_error": str(exc)}
    if "_error" in data:
        return [{"name": f"Sketchfab error: {data['_error']}", "url": ""}]
    out = []
    for item in data.get("results", []):
        out.append(
            {
                "name": item.get("name", ""),
                "url": item.get("viewerUrl", ""),
                "preview": (item.get("thumbnails") or {}).get("images", [{}])[0].get("url", ""),
                "license": item.get("license", {}).get("label", "CC0"),
                "tags": ", ".join(t.get("name", "") for t in item.get("tags", [])[:6]),
                "download_url": "",  # Sketchfab downloads require OAuth; URL provided for reference
            }
        )
    return out


async def _search_iconify(
    session: aiohttp.ClientSession, query: str, limit: int
) -> List[Dict]:
    data = await _get(
        session,
        "https://api.iconify.design/search",
        {"query": query, "limit": limit},
    )
    if "_error" in data:
        return [{"name": f"Iconify error: {data['_error']}", "url": ""}]
    icons = data.get("icons", [])
    out = []
    for icon in icons:
        # icon format: "prefix:name"
        prefix, _, icon_name = icon.partition(":")
        out.append(
            {
                "name": icon,
                "url": f"https://icon-sets.iconify.design/{prefix}/",
                "preview": f"https://api.iconify.design/{prefix}/{icon_name}.svg",
                "license": "Open-source (varies by set)",
                "download_url": f"https://api.iconify.design/{prefix}/{icon_name}.svg",
            }
        )
    return out


async def _search_unsplash(
    session: aiohttp.ClientSession, query: str, limit: int
) -> List[Dict]:
    key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    if not key:
        return [{"name": "⚠ UNSPLASH_ACCESS_KEY not set", "url": "", "license": "Unsplash License"}]
    data = await _get(
        session,
        "https://api.unsplash.com/search/photos",
        {"query": query, "per_page": limit, "client_id": key},
    )
    if "_error" in data:
        return [{"name": f"Unsplash error: {data['_error']}", "url": ""}]
    out = []
    for item in data.get("results", []):
        out.append(
            {
                "name": item.get("description") or item.get("alt_description") or item["id"],
                "url": item.get("links", {}).get("html", ""),
                "preview": item.get("urls", {}).get("small", ""),
                "license": "Unsplash License (free commercial)",
                "download_url": item.get("links", {}).get("download", ""),
            }
        )
    return out


async def _search_pixabay(
    session: aiohttp.ClientSession, query: str, limit: int, image_type: str = "all"
) -> List[Dict]:
    key = os.environ.get("PIXABAY_API_KEY", "")
    if not key:
        return [{"name": "⚠ PIXABAY_API_KEY not set", "url": "", "license": "Pixabay License"}]
    data = await _get(
        session,
        "https://pixabay.com/api/",
        {"key": key, "q": urllib.parse.quote(query), "per_page": limit, "image_type": image_type},
    )
    if "_error" in data:
        return [{"name": f"Pixabay error: {data['_error']}", "url": ""}]
    out = []
    for item in data.get("hits", []):
        out.append(
            {
                "name": f"Pixabay #{item.get('id', '')} ({item.get('type', '')})",
                "url": item.get("pageURL", ""),
                "preview": item.get("previewURL", ""),
                "license": "Pixabay License (free commercial, no attribution required)",
                "tags": item.get("tags", ""),
                "download_url": item.get("largeImageURL", ""),
            }
        )
    return out


async def _search_polyhaven(
    session: aiohttp.ClientSession, query: str, limit: int, asset_type: str = "textures"
) -> List[Dict]:
    """
    PolyHaven has a simple JSON manifest at /api/assets.
    asset_type: 'textures', 'hdris', 'models'
    """
    type_map = {"textures": 0, "hdris": 1, "models": 2}
    ph_type = type_map.get(asset_type, 0)
    data = await _get(
        session,
        "https://api.polyhaven.com/assets",
        {"type": ph_type},
    )
    if "_error" in data:
        return [{"name": f"PolyHaven error: {data['_error']}", "url": ""}]
    q = query.lower()
    matches = [
        (slug, meta)
        for slug, meta in data.items()
        if q in slug.lower() or any(q in t.lower() for t in meta.get("tags", []))
    ][:limit]
    out = []
    for slug, meta in matches:
        out.append(
            {
                "name": meta.get("name", slug),
                "url": f"https://polyhaven.com/a/{slug}",
                "preview": f"https://cdn.polyhaven.com/asset_img/thumbs/{slug}.png?height=200",
                "license": "CC0",
                "tags": ", ".join(meta.get("tags", [])[:6]),
                "download_url": f"https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/{slug}/{slug}_4k.jpg"
                if asset_type == "textures"
                else f"https://polyhaven.com/a/{slug}",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

SOURCE_MAP = {
    "freesound":  ("sounds",   _search_freesound),
    "polypizza":  ("3d",       _search_polypizza),
    "sketchfab":  ("3d",       _search_sketchfab),
    "iconify":    ("icons",    _search_iconify),
    "unsplash":   ("images",   _search_unsplash),
    "pixabay":    ("images",   _search_pixabay),
    "polyhaven":  ("textures", _search_polyhaven),
}

CATEGORY_DEFAULTS = {
    "sounds":   ["freesound"],
    "3d":       ["polypizza", "sketchfab"],
    "icons":    ["iconify"],
    "images":   ["unsplash", "pixabay"],
    "textures": ["polyhaven"],
    "all":      list(SOURCE_MAP.keys()),
}


# ---------------------------------------------------------------------------
# Tool Class
# ---------------------------------------------------------------------------

class AssetSearchTool(BaseTool):
    """
    Search and optionally download open-source, royalty-free assets from
    multiple public APIs (Freesound, Poly Pizza, Sketchfab, Iconify, Unsplash,
    Pixabay, PolyHaven).
    """

    name: str = "asset_search"

    description: str = (
        "Search and download open-source, royalty-free creative assets. "
        "Supports sounds (Freesound), 3D models (Poly Pizza, Sketchfab), "
        "icons/SVG (Iconify), photos/vectors (Unsplash, Pixabay), and "
        "PBR textures/HDRIs (PolyHaven). "
        "Use action='search' to find assets, action='download' to save one, "
        "or action='list_sources' to see all available sources and their API key requirements."
    )

    parameters: List[ToolParameter] = [
        ToolParameter(
            name="action",
            type="string",
            description=(
                "Required. One of: 'search', 'download', 'list_sources'. "
                "'search' returns matching assets; 'download' saves a direct URL to the workspace; "
                "'list_sources' lists all supported sources."
            ),
            required=True,
        ),
        ToolParameter(
            name="query",
            type="string",
            description="Search query string. Required for action='search'.",
            required=False,
        ),
        ToolParameter(
            name="category",
            type="string",
            description=(
                "Asset category to search. One of: 'sounds', '3d', 'icons', 'images', "
                "'textures', 'all'. Defaults to 'all'. "
                "Determines which sources are queried unless 'source' is specified."
            ),
            required=False,
        ),
        ToolParameter(
            name="source",
            type="string",
            description=(
                "Specific source to query: 'freesound', 'polypizza', 'sketchfab', "
                "'iconify', 'unsplash', 'pixabay', 'polyhaven'. "
                "Overrides 'category' when provided."
            ),
            required=False,
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="Max results to return per source (1–20). Defaults to 5.",
            required=False,
        ),
        ToolParameter(
            name="download_url",
            type="string",
            description=(
                "Direct URL of the asset to download. Required for action='download'. "
                "Obtain from a prior 'search' result's 'Download' field."
            ),
            required=False,
        ),
        ToolParameter(
            name="filename",
            type="string",
            description=(
                "Destination filename for the downloaded asset (e.g. 'explosion.mp3'). "
                "Required for action='download'. Saved into the shared workspace 'assets/' folder."
            ),
            required=False,
        ),
    ]

    usage_example: str = """<![CDATA[
<!-- Search for explosion sound effects -->
<asset_search>
  <action>search</action>
  <query>explosion</query>
  <category>sounds</category>
  <limit>5</limit>
</asset_search>

<!-- Download a specific asset -->
<asset_search>
  <action>download</action>
  <download_url>https://freesound.org/apiv2/sounds/123456/download/?token=KEY</download_url>
  <filename>explosion.mp3</filename>
</asset_search>

<!-- List available sources -->
<asset_search>
  <action>list_sources</action>
</asset_search>
]]>"""

    # ------------------------------------------------------------------
    # execute
    # ------------------------------------------------------------------

    async def execute(
        self,
        agent_id: str,
        agent_sandbox_path: Path,
        project_name: Optional[str] = None,
        session_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        action = (kwargs.get("action") or "").strip().lower()

        # ── list_sources ────────────────────────────────────────────────
        if action == "list_sources":
            return self._list_sources()

        # ── search ──────────────────────────────────────────────────────
        if action == "search":
            query = (kwargs.get("query") or "").strip()
            if not query:
                return "Error: 'query' is required for action='search'."

            try:
                limit = max(1, min(20, int(kwargs.get("limit", 5))))
            except (ValueError, TypeError):
                limit = 5

            category = (kwargs.get("category") or "all").strip().lower()
            source_override = (kwargs.get("source") or "").strip().lower()

            if source_override:
                if source_override not in SOURCE_MAP:
                    return (
                        f"Error: Unknown source '{source_override}'. "
                        f"Valid sources: {', '.join(SOURCE_MAP.keys())}."
                    )
                sources_to_query = [source_override]
            else:
                sources_to_query = CATEGORY_DEFAULTS.get(
                    category, CATEGORY_DEFAULTS["all"]
                )

            logger.info(
                f"[AssetSearchTool] Agent {agent_id} searching '{query}' "
                f"via {sources_to_query} (limit={limit})"
            )

            async with aiohttp.ClientSession() as session:
                tasks = {
                    src: SOURCE_MAP[src][1](session, query, limit)
                    for src in sources_to_query
                }
                raw = await asyncio.gather(*tasks.values(), return_exceptions=True)

            output_parts = []
            for src, result in zip(tasks.keys(), raw):
                output_parts.append(f"\n=== {src.upper()} ===")
                if isinstance(result, Exception):
                    output_parts.append(f"Error: {result}")
                else:
                    output_parts.append(_format_results(result))

            return "\n".join(output_parts)

        # ── download ────────────────────────────────────────────────────
        if action == "download":
            download_url = (kwargs.get("download_url") or "").strip()
            filename = (kwargs.get("filename") or "").strip()
            if not download_url:
                return "Error: 'download_url' is required for action='download'."
            if not filename:
                return "Error: 'filename' is required for action='download'."

            # Sanitise filename
            filename = Path(filename).name  # strip any path traversal
            if not filename:
                return "Error: Invalid filename."

            # Resolve destination: shared workspace > agent sandbox
            if project_name and session_name:
                from src.config.settings import settings  # type: ignore
                workspace_root = Path(settings.BASE_DIR) / "workspace" / project_name / session_name
                dest = workspace_root / "assets" / filename
            else:
                dest = agent_sandbox_path / "assets" / filename

            logger.info(
                f"[AssetSearchTool] Agent {agent_id} downloading {download_url} -> {dest}"
            )

            async with aiohttp.ClientSession() as session:
                result = await _download_file(session, download_url, dest)

            return result

        # ── unknown ─────────────────────────────────────────────────────
        return (
            f"Error: Unknown action '{action}'. "
            "Valid actions: 'search', 'download', 'list_sources'."
        )

    # ------------------------------------------------------------------
    # Modular help
    # ------------------------------------------------------------------

    def get_detailed_usage(
        self,
        agent_context: Optional[Dict] = None,
        sub_action: Optional[str] = None,
    ) -> str:
        common_header = (
            "**Tool Name:** asset_search\n"
            "**Description:** Search and download open-source, royalty-free assets "
            "(sounds, 3D models, icons, images, PBR textures) from multiple public APIs.\n"
        )

        action_details: Dict[str, str] = {
            "search": (
                "\n**Action: search**\n"
                "Search one or more asset sources for a keyword.\n\n"
                "**Parameters:**\n"
                "* `<query>` (string, required): Search keyword(s).\n"
                "* `<category>` (string, optional): 'sounds', '3d', 'icons', 'images', "
                "'textures', or 'all' (default).\n"
                "* `<source>` (string, optional): Target a specific source — overrides category. "
                "Values: freesound, polypizza, sketchfab, iconify, unsplash, pixabay, polyhaven.\n"
                "* `<limit>` (integer, optional): Results per source, 1–20 (default 5).\n\n"
                "**Example:**\n"
                "<asset_search><action>search</action><query>forest ambience</query>"
                "<category>sounds</category><limit>3</limit></asset_search>\n"
            ),
            "download": (
                "\n**Action: download**\n"
                "Download an asset directly into the shared workspace 'assets/' folder.\n\n"
                "**Parameters:**\n"
                "* `<download_url>` (string, required): Direct file URL from a prior search result.\n"
                "* `<filename>` (string, required): Target filename (e.g. 'tree.glb').\n\n"
                "**Example:**\n"
                "<asset_search><action>download</action>"
                "<download_url>https://example.com/file.mp3</download_url>"
                "<filename>forest.mp3</filename></asset_search>\n"
            ),
            "list_sources": (
                "\n**Action: list_sources**\n"
                "Returns a table of all supported asset sources, their categories, "
                "and API key requirements.\n\n"
                "**Parameters:** none\n\n"
                "**Example:**\n"
                "<asset_search><action>list_sources</action></asset_search>\n"
            ),
        }

        if sub_action and sub_action in action_details:
            return common_header + action_details[sub_action]

        summary = (
            "\n**Available Actions:**\n"
            "1. **search** — Find assets by keyword across one or more sources.\n"
            "2. **download** — Save a specific asset URL to the workspace.\n"
            "3. **list_sources** — Show all sources and their API key requirements.\n\n"
            "**To get detailed help for a specific action:**\n"
            "<tool_information>\n"
            "  <action>get_info</action>\n"
            "  <tool_name>asset_search</tool_name>\n"
            "  <sub_action>search</sub_action>\n"
            "</tool_information>\n"
        )
        return common_header + summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _list_sources() -> str:
        rows = [
            ("freesound",  "sounds",   "Yes — FREESOUND_API_KEY",      "freesound.org"),
            ("polypizza",  "3d",       "No",                            "poly.pizza"),
            ("sketchfab",  "3d",       "Optional — SKETCHFAB_API_KEY", "sketchfab.com"),
            ("iconify",    "icons",    "No",                            "iconify.design"),
            ("unsplash",   "images",   "Yes — UNSPLASH_ACCESS_KEY",    "unsplash.com"),
            ("pixabay",    "images",   "Yes — PIXABAY_API_KEY",        "pixabay.com"),
            ("polyhaven",  "textures", "No",                            "polyhaven.com"),
        ]
        header = f"{'Source':<12} {'Category':<10} {'API Key Required':<30} {'Domain'}"
        sep = "-" * 70
        lines = [header, sep]
        for row in rows:
            lines.append(f"{row[0]:<12} {row[1]:<10} {row[2]:<30} {row[3]}")
        lines.append(sep)
        lines.append(
            "\nSet API keys in your .env file. "
            "Sources without a key requirement work immediately."
        )
        return "\n".join(lines)

# END OF FILE src/tools/asset_search_tool.py
