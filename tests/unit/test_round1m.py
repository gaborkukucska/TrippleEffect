"""Round 1m tests: web_search must say so when the search BACKEND is failing.

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
