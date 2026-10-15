#!/usr/bin/env python3
"""
TrippleEffect: Round 1m Surgical Fix  (src/tools/web_search.py)

OBSERVED (2026-10-15 15:32-15:45, Temp_Convert_CLI run): every SearXNG request answered HTTP 429 and the DuckDuckGo
fallback returned nothing, yet web_search answered "No search results found" with status success. The worker (W1)
rewrote the query and tried again five times; every empty answer counted as a failed cycle, so after three of them the
framework forced W1 into worker_report (twice) before it had done any research. A search service that is down looks
exactly like "your query found nothing", and a 4B model cannot tell the two apart unless the tool says so.

 1. web_search: when the SearXNG backend itself fails (HTTP 429/403/5xx, timeout, ...) AND the DuckDuckGo fallback also
    returns nothing, the tool now returns status "error" / error_type "search_backend_unavailable" with the reason and
    an instruction not to repeat the search (use zim_search / knowledge_base or continue and say so in the report).
    If DuckDuckGo does return results, they are used exactly as before.
 2. A 429 that carries a short numeric Retry-After (<= 10 s) is retried once. Anything else is not retried.
 3. The log line for an HTTP error from SearXNG is now one WARNING with a hint (429: set server.limiter: false in the
    SearXNG settings.yml for a private instance; 403: enable json in search.formats) instead of an ERROR with a full
    traceback. Unexpected exceptions still log the traceback.
 Unchanged: a working SearXNG that simply finds nothing still answers "No search results found" (status success), the
 "unresponsive engines" note, SearXNG results, get_page, parameter validation. Public signatures are unchanged
 (_perform_search still returns (results, source, unresponsive_list)).
 4. tests/unit/test_round1m.py (new, 18 tests, local fake SearXNG/DuckDuckGo server, no real network). 13 fail without
    this fix.

Usage:
    python3 fix.py --dry-run
    python3 fix.py

All-or-nothing: if ANY find-string is missing (or matches the wrong number of times) nothing is written.
"""
import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path.cwd()
WS = "src/tools/web_search.py"

PATCHES = [
    {
        "file": WS,
        "find": r'''URL_SELECTOR = "a.result__url"
''',
        "replace": r'''URL_SELECTOR = "a.result__url"

# Round 1m: marker (3rd element of an "unresponsive engines" tuple) for a failure of the search BACKEND itself
# (SearXNG HTTP 429, timeout, ...), as opposed to an engine that was merely unresponsive inside a working SearXNG.
BACKEND_ERROR_MARKER = "backend_error"
# A 429 that carries a Retry-After up to this many seconds is retried once; anything longer is not worth blocking a worker.
MAX_RETRY_AFTER_SECONDS = 10.0
''',
    },
    {
        "file": WS,
        "find": r'''            results, source, unresponsive_engines = await self._perform_search(query, num_results, engines, time_range, language)
''',
        "replace": r'''            results, source, unresponsive_engines = await self._perform_search(query, num_results, engines, time_range, language)

            # Round 1m: failures of the search BACKEND itself are tagged by _perform_search.
            backend_failures = [
                e for e in (unresponsive_engines or [])
                if isinstance(e, (list, tuple)) and len(e) >= 3 and e[2] == BACKEND_ERROR_MARKER
            ]
            backend_reasons = "; ".join(f"{e[0]}: {e[1]}" for e in backend_failures)
''',
    },
    {
        "file": WS,
        "find": r'''                    "message": f"Failed to retrieve search results for query: '{query}'",
''',
        "replace": r'''                    "message": f"Failed to retrieve search results for query: '{query}'" + (f" ({backend_reasons})" if backend_reasons else ""),
''',
    },
    {
        "file": WS,
        "find": r'''            if not results:
                suggestions = [
''',
        "replace": r'''            if not results and backend_failures:
                # Round 1m: before, this case was reported as "No search results found" (status success), so the model
                # rewrote the query and tried again, burning cycles until the worker was forced into the report state.
                logger.warning(f"Agent {agent_id}: web search unavailable ({backend_reasons}) and the DuckDuckGo fallback returned nothing for '{query[:80]}'.")
                return {
                    "status": "error",
                    "message": (
                        f"Web search is unavailable right now ({backend_reasons}) and the DuckDuckGo fallback returned nothing "
                        f"for query: '{query}'. The search service is failing, not your query: do NOT repeat this search or a "
                        "reworded one right away. Use zim_search or knowledge_base for reference material, or continue with "
                        "your own knowledge and state in your report that web research was unavailable."
                    ),
                    "error_type": "search_backend_unavailable",
                    "suggestions": [
                        "Do not repeat the same search right away",
                        "Use zim_search or knowledge_base for reference material",
                        "Continue with your own knowledge and say in your report that web research was unavailable",
                    ],
                }

            if not results:
                suggestions = [
''',
    },
    {
        "file": WS,
        "find": r'''    async def _perform_search(self, query: str, num_results: int, engines: Optional[str] = None, time_range: Optional[str] = None, language: str = "en") -> Tuple[Optional[List[Dict[str, Any]]], str, List[Any]]:
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
''',
        "replace": r'''    @staticmethod
    def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
        """Parses a numeric Retry-After header; returns None when absent or not a plain number of seconds."""
        if value is None:
            return None
        try:
            seconds = float(str(value).strip())
        except (TypeError, ValueError):
            return None
        return seconds if seconds >= 0 else None

    async def _searxng_request(self, search_url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """GET the SearXNG JSON API. A 429 that carries a short Retry-After is retried once; every other HTTP error
        status is raised (aiohttp.ClientResponseError) for the caller to report."""
        for attempt in (1, 2):
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 429 and attempt == 1:
                        wait = self._retry_after_seconds(response.headers.get("Retry-After"))
                        if wait is not None and wait <= MAX_RETRY_AFTER_SECONDS:
                            logger.info(f"SearXNG answered 429 with Retry-After={wait:g}s: retrying once.")
                            await asyncio.sleep(wait)
                            continue
                    response.raise_for_status()
                    return await response.json()
        raise RuntimeError("unreachable")  # pragma: no cover - the loop always returns or raises

    async def _perform_search(self, query: str, num_results: int, engines: Optional[str] = None, time_range: Optional[str] = None, language: str = "en") -> Tuple[Optional[List[Dict[str, Any]]], str, List[Any]]:
        # Failures of the SearXNG backend itself are collected here as (name, reason, BACKEND_ERROR_MARKER) tuples and
        # returned in the third slot, so execute() can tell "backend broken" apart from "backend works, nothing found".
        backend_failures: List[Any] = []
        if settings.SEARXNG_URL:
            base_url = settings.SEARXNG_URL.rstrip('/')
            try:
                search_url = f"{base_url}/search"
                params = {
                    "q": query,
                    "format": settings.SEARXNG_FORMAT,
                    "language": language,
                }
                if engines: params["engines"] = engines
                if time_range: params["time_range"] = time_range

                data = await self._searxng_request(search_url, params)

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
            except aiohttp.ClientResponseError as e:
                reason = f"HTTP {e.status} {e.message}".strip()
                hint = ""
                if e.status == 429:
                    hint = " Hint: SearXNG's bot limiter is rejecting this client; for a private instance set server.limiter: false in its settings.yml (or allow this client's IP in limiter.toml)."
                elif e.status == 403:
                    hint = " Hint: SearXNG may not allow the JSON format; add json to search.formats in its settings.yml."
                logger.warning(f"SearXNG API search failed: {reason} ({base_url}); falling back to DuckDuckGo scraping.{hint}")
                backend_failures.append(("SearXNG API", reason, BACKEND_ERROR_MARKER))
            except Exception as e:
                logger.error(f"SearXNG API search failed: {e}", exc_info=True)
                backend_failures.append(("SearXNG API", f"{type(e).__name__}: {e}"[:160], BACKEND_ERROR_MARKER))
                # Fallback to scraping if SearXNG fails

        return await self._search_with_scraping(query, num_results), "DuckDuckGo Scraping", backend_failures
''',
    },
]

NEW_FILES = {
    "tests/unit/test_round1m.py": r'''"""Round 1m tests: web_search must say so when the search BACKEND is failing.

Observed 2026-10-15 15:32-15:45 (Temp_Convert_CLI run): every SearXNG request answered HTTP 429, the DuckDuckGo
fallback returned nothing, and the tool reported "No search results found" with status success. The worker rewrote the
query and tried again (five times), and each empty answer counted as a failed cycle until the framework forced the
worker into the report state - twice - before it had done any research.

These tests run a tiny local HTTP server that plays SearXNG and DuckDuckGo; no real network is used.
"""
import asyncio

import pytest
from aiohttp import web

import src.agents  # noqa: F401 - resolves pre-existing circular import order
import src.tools.web_search as ws

DDG_HTML_WITH_RESULT = """<html><body>
<div class="result">
  <h2 class="result__title"><a class="result__a" href="https://example.org/f">Fahrenheit formula</a></h2>
  <a class="result__snippet">F = C * 9/5 + 32</a>
  <a class="result__url">example.org/f</a>
</div></body></html>"""
DDG_HTML_EMPTY = "<html><body><p>nothing here</p></body></html>"


def run_search(monkeypatch, *, searxng, ddg, query="temp formula", configure_searxng=True, ddg_reachable=True):
    """searxng / ddg: handler(request, hits) -> web.Response. Returns (result_dict, hits)."""
    hits = {"searxng": 0, "ddg": 0}

    async def go():
        app = web.Application()

        async def searxng_h(request):
            hits["searxng"] += 1
            return searxng(request, hits)

        async def ddg_h(request):
            hits["ddg"] += 1
            return ddg(request, hits)

        app.router.add_get("/search", searxng_h)
        app.router.add_get("/ddg", ddg_h)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        monkeypatch.setattr(ws.settings, "SEARXNG_URL", base if configure_searxng else "", raising=False)
        monkeypatch.setattr(ws.settings, "SEARXNG_FORMAT", "json", raising=False)
        ddg_base = base if ddg_reachable else "http://127.0.0.1:1"
        monkeypatch.setattr(ws, "SEARCH_URL_TEMPLATE", ddg_base + "/ddg?q={query}")
        try:
            return await ws.WebSearchTool().execute("W1", query=query)
        finally:
            await runner.cleanup()

    return asyncio.run(go()), hits


def too_many(request, hits):
    return web.Response(status=429, text="Too Many Requests")


def ok_json(results, unresponsive=()):
    def h(request, hits):
        return web.json_response({"results": results, "unresponsive_engines": list(unresponsive)})
    return h


def ddg_page(html):
    def h(request, hits):
        return web.Response(text=html, content_type="text/html")
    return h


def test_backend_429_and_empty_fallback_is_reported_as_error(monkeypatch):
    r, hits = run_search(monkeypatch, searxng=too_many, ddg=ddg_page(DDG_HTML_EMPTY))
    assert r["status"] == "error"
    assert r["error_type"] == "search_backend_unavailable"
    assert "429" in r["message"]
    assert "No search results found" not in r["message"]
    assert "do NOT repeat" in r["message"]
    assert hits["searxng"] == 1  # no Retry-After header -> no retry


def test_backend_429_falls_back_to_duckduckgo_results(monkeypatch):
    r, hits = run_search(monkeypatch, searxng=too_many, ddg=ddg_page(DDG_HTML_WITH_RESULT))
    assert r["status"] == "success"
    assert r["source"] == "DuckDuckGo Scraping"
    assert len(r["results"]) == 1 and r["results"][0]["title"] == "Fahrenheit formula"


def test_working_searxng_with_results_is_unchanged(monkeypatch):
    res = [{"title": "T", "url": "https://x.org", "content": "snippet"}]
    r, hits = run_search(monkeypatch, searxng=ok_json(res), ddg=ddg_page(DDG_HTML_EMPTY))
    assert r["status"] == "success" and r["source"] == "SearXNG API"
    assert r["results"][0]["snippet"] == "snippet"
    assert hits["ddg"] == 0


def test_working_searxng_with_zero_results_is_still_plain_no_results(monkeypatch):
    r, hits = run_search(monkeypatch, searxng=ok_json([]), ddg=ddg_page(DDG_HTML_EMPTY))
    assert r["status"] == "success"
    assert r["message"].startswith("No search results found")
    assert r["results"] == []


def test_unresponsive_engines_inside_working_searxng_keep_the_old_note(monkeypatch):
    r, hits = run_search(monkeypatch, searxng=ok_json([], unresponsive=[["google", "CAPTCHA"], ["bing", "timeout", True]]),
                         ddg=ddg_page(DDG_HTML_EMPTY))
    assert r["status"] == "success"
    assert "google (CAPTCHA)" in r["message"]


def test_no_searxng_configured_and_empty_duckduckgo_is_plain_no_results(monkeypatch):
    r, hits = run_search(monkeypatch, searxng=too_many, ddg=ddg_page(DDG_HTML_EMPTY), configure_searxng=False)
    assert r["status"] == "success"
    assert r["message"].startswith("No search results found")
    assert hits["searxng"] == 0


def test_short_retry_after_is_honoured_exactly_once(monkeypatch):
    def flaky(request, hits):
        if hits["searxng"] == 1:
            return web.Response(status=429, headers={"Retry-After": "0"}, text="slow down")
        return web.json_response({"results": [{"title": "T", "url": "https://x.org", "content": "c"}]})

    r, hits = run_search(monkeypatch, searxng=flaky, ddg=ddg_page(DDG_HTML_EMPTY))
    assert r["status"] == "success" and r["source"] == "SearXNG API"
    assert hits["searxng"] == 2


def test_long_retry_after_is_not_waited_for(monkeypatch):
    def limited(request, hits):
        return web.Response(status=429, headers={"Retry-After": "3600"}, text="later")

    r, hits = run_search(monkeypatch, searxng=limited, ddg=ddg_page(DDG_HTML_EMPTY))
    assert r["status"] == "error" and r["error_type"] == "search_backend_unavailable"
    assert hits["searxng"] == 1


def test_retry_after_is_only_retried_once(monkeypatch):
    def limited(request, hits):
        return web.Response(status=429, headers={"Retry-After": "0"}, text="still no")

    r, hits = run_search(monkeypatch, searxng=limited, ddg=ddg_page(DDG_HTML_EMPTY))
    assert r["status"] == "error"
    assert hits["searxng"] == 2


@pytest.mark.parametrize("value,expected", [(None, None), ("3", 3.0), (" 2.5 ", 2.5), ("-1", None),
                                           ("Wed, 21 Oct 2026 07:28:00 GMT", None), ("", None), ("abc", None)])
def test_retry_after_parsing(value, expected):
    assert ws.WebSearchTool._retry_after_seconds(value) == expected


def test_both_backends_failing_hard_mentions_the_searxng_reason(monkeypatch):
    r, hits = run_search(monkeypatch, searxng=too_many, ddg=ddg_page(DDG_HTML_EMPTY), ddg_reachable=False)
    assert r["status"] == "error"
    assert "Failed to retrieve search results" in r["message"]
    assert "429" in r["message"]


def test_searxng_server_error_is_also_a_backend_failure(monkeypatch):
    def boom(request, hits):
        return web.Response(status=503, text="down")

    r, hits = run_search(monkeypatch, searxng=boom, ddg=ddg_page(DDG_HTML_EMPTY))
    assert r["status"] == "error" and r["error_type"] == "search_backend_unavailable"
    assert "503" in r["message"]
''',
}

def apply_patches(dry_run: bool) -> int:
    originals = {}
    updated = {}
    errors = []

    for idx, p in enumerate(PATCHES, 1):
        rel = p["file"]
        path = ROOT / rel
        expected = p.get("count", 1)
        if rel not in updated:
            if not path.is_file():
                errors.append(f"[patch {idx}] File not found: {rel}")
                continue
            originals[rel] = path.read_text(encoding="utf-8")
            updated[rel] = originals[rel]
        count = updated[rel].count(p["find"])
        if count == 0:
            errors.append(f"[patch {idx}] NO MATCH in {rel} for find-string starting: {p['find'][:80]!r}")
        elif count != expected:
            errors.append(f"[patch {idx}] {count} MATCHES (expected exactly {expected}) in {rel}: {p['find'][:80]!r}")
        else:
            updated[rel] = updated[rel].replace(p["find"], p["replace"])

    for rel in NEW_FILES:
        if (ROOT / rel).exists():
            errors.append(f"New file already exists, refusing to overwrite: {rel}")

    if errors:
        print("ERROR: patch aborted, NOTHING was written:\n", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1

    for rel, new_text in updated.items():
        if new_text == originals[rel]:
            continue
        if dry_run:
            diff = difflib.unified_diff(
                originals[rel].splitlines(keepends=True), new_text.splitlines(keepends=True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}", n=2,
            )
            sys.stdout.writelines(diff)
            print()
        else:
            (ROOT / rel).write_text(new_text, encoding="utf-8")
            print(f"PATCHED  {rel}")

    for rel, content in NEW_FILES.items():
        if dry_run:
            print(f"[dry-run] would create {rel} ({len(content.splitlines())} lines)")
        else:
            target = ROOT / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            print(f"CREATED  {rel}")

    print("\nDry run complete: no files changed." if dry_run else "\nAll patches applied successfully.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply TrippleEffect Round 1m fixes.")
    ap.add_argument("--dry-run", action="store_true", help="Show diffs only; do not write any files.")
    args = ap.parse_args()
    if not (ROOT / "src").is_dir():
        print("ERROR: run this from the TrippleEffect repository root.", file=sys.stderr)
        return 1
    return apply_patches(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
