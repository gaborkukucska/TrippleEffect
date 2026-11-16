# START OF FILE src/utils/plan_checks.py
"""
Round 5f: checks of the Admin AI's plan before the user approves it.

2026-11-15 (WiFi_Snake_Multiplayer_Game, qwen3.5:4b): the plan for "a browser-based multiplayer Snake game playable on mobile
devices connected to the same WiFi network" had eight tasks - game engine, players, score, leaderboard, "peer-to-peer
WebSocket communication ... in src/network.js", the page, touch controls, tests - and NO server. Browsers cannot accept
connections from each other: a page can only connect to a server (WebSocket) and WebRTC needs one to introduce the peers.
package.json said `"start": "node src/server.js"`, a file no task made, so the game could never run; 3 h 24 min of work went
into modules that had nothing to connect to.

* network_server_gap(): the plan of a browser project played or used across devices that names no server -> the plan item
  that adds one (Node.js, or Python when the plan is Python), else None.
* with_network_server_item(): the plan text with that item inserted before the test / documentation items (numbers moved).
"""
import re
from typing import Optional, Tuple

_ACROSS_DEVICES_RE = re.compile(
    r"wi-?fi|\blan\b|local network|same network|over the network|\bonline\b|internet|remote (?:players|users)|"
    r"other devices|multiple devices|several devices|mobile devices|\bphones?\b|websockets?|webrtc|peer-to-peer|\bp2p\b|"
    r"socket\.io|network(?:ed)? (?:play|game|multiplayer)", re.IGNORECASE)
_SHARED_RE = re.compile(r"multi-?player|\bplayers\b|\bchat\b|collaborat|real-?time|\bsync", re.IGNORECASE)
_BROWSER_RE = re.compile(r"browser|\bhtml5?\b|canvas|web ?page|web app|javascript|\bjs\b|front-?end|\.html\b|\.js\b",
                         re.IGNORECASE)
_SERVER_RE = re.compile(
    r"\bserver\b|\bbackend\b|back-end|express|flask|fastapi|django|aiohttp|\bnode(?:\.js)? (?:http|app)\b|"
    r"\bserver\.(?:js|mjs|ts|py)\b|\bapp\.py\b", re.IGNORECASE)
_PYTHON_RE = re.compile(r"\bpython\b|\.py\b|flask|django|fastapi|pytest", re.IGNORECASE)
_ITEM_RE = re.compile(r"^(\s*)(\d+)\.\s+", re.MULTILINE)
_LATE_ITEM_RE = re.compile(r"\btests?\b|testing|readme|documentation|\bdocs?\b|deploy", re.IGNORECASE)


def network_server_gap(plan_text: Optional[str]) -> Optional[str]:
    """The text of the plan item that adds the missing server (without its number), else None."""
    text = str(plan_text or "")
    if not text.strip() or _SERVER_RE.search(text):
        return None
    if not (_ACROSS_DEVICES_RE.search(text) and _SHARED_RE.search(text) and _BROWSER_RE.search(text)):
        return None
    if _PYTHON_RE.search(text):
        return ("**Server (added by the framework - browsers cannot connect to each other without one):** `server.py` "
                "(Flask with Flask-SocketIO) serves the page and its files, keeps the shared state (players, scores) and "
                "relays every player's moves to the other players; `python server.py` starts it on `0.0.0.0` so other "
                "devices on the network open `http://<this computer's IP>:<port>`. The browser code connects to it with "
                "Socket.IO.")
    return ("**Server (added by the framework - browsers cannot connect to each other without one):** `server.js` (Node.js) "
            "serves the page and its files with Node's `http` module, keeps the shared state (players, scores) and relays "
            "every player's moves to the other players over WebSocket with the `ws` package (its only dependency, in "
            "package.json); `npm start` (`node server.js`) starts it on `0.0.0.0` so phones on the same network open "
            "`http://<this computer's IP>:<port>`. The browser code connects to it with `new WebSocket('ws://' + "
            "location.host)`.")


def with_network_server_item(plan_text: Optional[str]) -> Tuple[str, Optional[str]]:
    """(the plan with the server item, the item) - or (the plan unchanged, None) when it needs none."""
    text = str(plan_text or "")
    item = network_server_gap(text)
    if not item:
        return text, None
    items = list(_ITEM_RE.finditer(text))
    if not items:
        return text.rstrip() + f"\n\n1. {item}\n", item
    insert_at = None
    for match in items:
        line_end = text.find("\n", match.end())
        line = text[match.end():line_end if line_end != -1 else len(text)]
        if _LATE_ITEM_RE.search(line):
            insert_at = match
            break
    if insert_at is None:
        last = items[-1]
        number = int(last.group(2)) + 1
        end = text.find("\n\n", last.end())
        position = len(text.rstrip()) if end == -1 else end
        return text[:position] + f"\n{last.group(1)}{number}. {item}" + text[position:], item
    number = int(insert_at.group(2))
    head = text[:insert_at.start()]
    tail = text[insert_at.start():]

    def bump(match: "re.Match") -> str:
        return f"{match.group(1)}{int(match.group(2)) + 1}. "

    tail = _ITEM_RE.sub(bump, tail)
    return head + f"{insert_at.group(1)}{number}. {item}\n" + tail, item

# END OF FILE src/utils/plan_checks.py
