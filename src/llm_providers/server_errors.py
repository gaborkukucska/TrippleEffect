# START OF FILE src/llm_providers/server_errors.py
"""
Round 3u: errors that say the SERVER (or a proxy in front of it) did not answer - not that the model failed.

2026-10-26 19:53-21:48 (WiFi_Snake_Multiplayer, qwen3.5:4b on an Ollama server behind a Cloudflare proxy): 76 requests ended
with "Status 524" (the proxy gave up waiting), others with a "Temporarily Offline" HTML page (status 500), and the server was
unreachable for a while (failed health checks, "Connection timed out"). After the retries such an error was an
aiohttp.ClientResponseError, which the framework counted as an unknown error:
* the Guardian counted each one as an EMPTY reply of the agent and, after two, blocked W1 and W2 ("2 consecutive empty
  responses"), forced them into worker_wait and sent PM1 a stall report about agents that had done nothing wrong;
* failover switched W1, W2 and PM1 to OTHER models on the SAME server (gemma4:12b, qwen3.5:9b): the server had to load them
  (one load took 56.9 s) next to the model the other agents used, and the agents never returned to their own model.

These errors are now provider-level errors (src/agents/failover_handler.is_provider_level_error): they are not counted as
the agent's replies, and failover skips the failing server - another server is tried, or the agent retries its own model
after a cooldown. A 500 with Ollama's own JSON error ("model requires more system memory ...") is still a model error.
"""
from typing import Any, Optional

try:
    import aiohttp
except ImportError:  # pragma: no cover - aiohttp is a framework requirement
    aiohttp = None  # type: ignore[assignment]

# Gateway / proxy statuses: 502 Bad Gateway, 503 Service Unavailable, 504 Gateway Timeout, Cloudflare's 520-527 (unknown
# origin error, web server down, connection timed out, origin unreachable, a timeout occurred, SSL handshake failed, invalid
# SSL certificate, Railgun error) and 530 (origin DNS error / frozen origin).
GATEWAY_STATUS_CODES = frozenset({502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530})
BODY_PREVIEW_ATTR = "te_body_preview"
BODY_PREVIEW_CHARS = 400


def looks_like_html(text: Any) -> bool:
    """True for a body that is an HTML page (a proxy's or web server's error page) rather than an API answer."""
    head = str(text or "").lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or ("<head" in head and "<" == head[:1])


def server_unavailable_status(status: Any, body: Any = None) -> bool:
    """True when this HTTP status (with this body, if known) means the server or a proxy did not answer the request."""
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    if code in GATEWAY_STATUS_CODES:
        return True
    return code >= 500 and looks_like_html(body)


def mark_response_error(error: Any, body: Any) -> Any:
    """Keeps the start of the response body on the error object (for is_server_unavailable_error). Returns the error."""
    try:
        setattr(error, BODY_PREVIEW_ATTR, str(body or "")[:BODY_PREVIEW_CHARS])
    except Exception:
        pass
    return error


def error_status(error: Any) -> Optional[int]:
    """The HTTP status of an aiohttp / openai response error, or None."""
    for name in ("status", "status_code"):
        value = getattr(error, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def is_server_unavailable_error(error: Any) -> bool:
    """
    True for an aiohttp response error (the Ollama provider) whose status says the server or a proxy in front of it did
    not answer: a gateway status (GATEWAY_STATUS_CODES) or a 5xx whose body is an HTML page. Errors of the OpenAI client
    (OpenRouter, OpenAI, vLLM) keep their own handling: an OpenRouter 502 is often one upstream model failing, and another
    model of the same provider may answer. Never raises.
    """
    try:
        if aiohttp is None or not isinstance(error, aiohttp.ClientResponseError):
            return False
        status = error_status(error)
        if status is None:
            return False
        return server_unavailable_status(status, getattr(error, BODY_PREVIEW_ATTR, None))
    except Exception:
        return False

# END OF FILE src/llm_providers/server_errors.py
