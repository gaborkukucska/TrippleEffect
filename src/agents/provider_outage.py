# START OF FILE src/agents/provider_outage.py
"""
Round 4h: the USER is told, in the main chat, when the model server stays unreachable - and again when it answers.

2026-10-30 04:00:09-07:50 (WiFi_Snake_Game) every request to https://oli.gaborkukucska.com answered "Status 530" (the
Cloudflare page "origin is unreachable": the server behind the tunnel was down). W3's failover found nothing else, and the
framework retried 43 times for 3 h 50 min. The only trace was an agent status line "All providers down. Auto-retry 43 in
300s..." in the internal-comms panel - the run looked stuck, and the user could not tell that the work was waiting for the
model server, not for the agents.

* note_provider_outage(): called each time an agent's failover is exhausted. The first time a provider is seen down, the
  time is kept; once it has been down for OUTAGE_NOTICE_AFTER_SECONDS, ONE notice ("provider_outage") goes to the main chat,
  naming the server, the error and that the work resumes by itself.
* note_provider_answered(): called after an agent's cycle without an error. For a provider with a notified outage, ONE
  notice ("provider_recovered") says it answers again and how long it was down.
State lives on the manager (manager._provider_outages). Never raises.
"""
import logging
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

OUTAGE_NOTICE_AFTER_SECONDS = 120.0
_STATUS_RE = re.compile(r"\bStatus (\d{3})\b")
_STATUS_MEANINGS = {
    502: "the proxy in front of the server got no valid answer from it (HTTP 502)",
    503: "the server is unavailable (HTTP 503)",
    504: "the proxy in front of the server timed out waiting for it (HTTP 504)",
    520: "Cloudflare got an unknown error from the server behind it (HTTP 520)",
    521: "Cloudflare reached no running web server behind it (HTTP 521: the server is down)",
    522: "Cloudflare could not connect to the server behind it in time (HTTP 522)",
    523: "Cloudflare cannot reach the server behind it (HTTP 523: the origin is unreachable)",
    524: "the server behind Cloudflare answered too slowly (HTTP 524)",
    530: "Cloudflare cannot reach the server behind it (HTTP 530: the server or its tunnel is not running)",
}


def _outages(manager: Any) -> Dict[str, Dict[str, Any]]:
    outages = getattr(manager, "_provider_outages", None)
    if not isinstance(outages, dict):
        outages = {}
        try:
            setattr(manager, "_provider_outages", outages)
        except Exception:
            pass
    return outages


def provider_of(agent: Any) -> str:
    """The provider an agent uses ('ollama-local-oli-gaborkukucska-com'), '' if unknown."""
    return str(getattr(agent, "provider_name", "") or "").strip()


def provider_url(agent: Any) -> str:
    provider = getattr(agent, "llm_provider", None)
    for attr in ("base_url", "_base_url", "api_base"):
        value = getattr(provider, attr, None)
        if value:
            return str(value)
    return ""


def error_meaning(error_text: Any) -> str:
    """What a provider error says, in words: the HTTP status the server answered, or the error text (shortened)."""
    text = " ".join(str(error_text or "").split())
    match = _STATUS_RE.search(text)
    if match:
        status = int(match.group(1))
        return _STATUS_MEANINGS.get(status, f"it answers HTTP {status}")
    if re.search(r"Cannot connect|Connection refused|Name or service not known|ClientConnector", text, re.IGNORECASE):
        return "no connection to it can be made"
    return text[:160] or "it does not answer"


def _clock(seconds: float) -> str:
    return time.strftime("%H:%M", time.localtime(seconds))


def _duration(seconds: float) -> str:
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    return f"{minutes // 60} h {minutes % 60} min"


async def note_provider_outage(manager: Any, agent: Any, error_text: Any, now: Optional[float] = None) -> bool:
    """Round 4h: remembers that the agent's provider is down; tells the user once it has been down for a while. Returns
    True if a notice was sent now."""
    try:
        provider = provider_of(agent)
        if not provider:
            return False
        now = time.time() if now is None else now
        outages = _outages(manager)
        record = outages.setdefault(provider, {"since": now, "notified": False, "agents": []})
        record["error"] = str(error_text or "")
        agent_id = str(getattr(agent, "agent_id", "") or "")
        if agent_id and agent_id not in record["agents"]:
            record["agents"].append(agent_id)
        if record["notified"] or now - record["since"] < OUTAGE_NOTICE_AFTER_SECONDS:
            return False
        record["notified"] = True
        where = provider_url(agent) or provider
        waiting = ", ".join(record["agents"][:6])
        content = (f"⚠️ The model server {where} has not answered since {_clock(record['since'])}: "
                   f"{error_meaning(error_text)}. No other model server is available, so the agents' work is paused "
                   f"({waiting} waiting). They retry every few minutes and continue by themselves when the server answers "
                   "again - nothing in the project is lost. If the server is yours, check that it (and its tunnel) is "
                   "running.")
        logger.warning(f"provider_outage: '{provider}' down since {_clock(record['since'])}: told the user.")
        send = getattr(manager, "send_to_ui", None)
        if send is not None:
            await send({"type": "provider_outage", "agent_id": "system", "provider": provider, "content": content})
        return True
    except Exception as err:
        logger.debug(f"note_provider_outage failed: {err}")
        return False


async def note_provider_answered(manager: Any, agent: Any, now: Optional[float] = None) -> bool:
    """Round 4h: the agent's provider answered: forgets its outage, and tells the user if they were told about it.
    Returns True if a notice was sent now."""
    try:
        outages = getattr(manager, "_provider_outages", None)
        provider = provider_of(agent)
        if not isinstance(outages, dict) or not provider or provider not in outages:
            return False
        record = outages.pop(provider)
        if not record.get("notified"):
            return False
        now = time.time() if now is None else now
        where = provider_url(agent) or provider
        content = (f"✅ The model server {where} answers again (it was unreachable from {_clock(record['since'])} for "
                   f"{_duration(now - record['since'])}). The agents continue their work.")
        logger.info(f"provider_outage: '{provider}' answers again: told the user.")
        send = getattr(manager, "send_to_ui", None)
        if send is not None:
            await send({"type": "provider_recovered", "agent_id": "system", "provider": provider, "content": content})
        return True
    except Exception as err:
        logger.debug(f"note_provider_answered failed: {err}")
        return False

# END OF FILE src/agents/provider_outage.py
