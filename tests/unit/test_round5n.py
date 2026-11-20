# START OF FILE tests/unit/test_round5n.py
"""
Round 5n: the run of 2026-11-19 18:17 - 19:51 (WiFi_Snake_Game): all 11 tasks finished, the audit passed, and the game did
not work.

1. server.py's handler sent "200 text/html" and then `self.wfile.write(f.read())` raised TypeError (a str, not bytes): the
   browser got an empty page, and the run check said "sends the page for GET /".
2. server.py imported `serve` from `websockets.server` and never called it: no WebSocket server ran, and the import alone
   counted as one, so the page's `new WebSocket(...)` passed the check.
"""
import asyncio
import shutil

import pytest

import src.agents  # noqa: F401 - resolves pre-existing circular import order


# ============================================================================== 1. an empty page is no page
STR_SERVER = """import http.server


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        with open('public/index.html', 'r') as f:
            self.wfile.write(f.read())


if __name__ == '__main__':
    http.server.HTTPServer(('0.0.0.0', 8765), Handler).serve_forever()
"""


def test_an_empty_page_is_named_with_the_servers_error():
    from src.utils.program_run_check import _page_problem
    assert _page_problem((200, "text/html", "<html><body>Snake"), 8765, ["public/index.html"]) is None
    output = ('----------------------------------------\nException occurred during processing of request from (\'127.0.0.1\', '
              '5000)\nTraceback (most recent call last):\n  File "/w/server.py", line 10, in do_GET\n    self.wfile.write(f.read())'
              '\nTypeError: a bytes-like object is required, not \'str\'\n----------------------------------------\n')
    problem = _page_problem((200, "text/html", ""), 8765, ["public/index.html"], output)
    assert problem.startswith("the server runs, but http://localhost:8765/ answers 200 with an EMPTY page: the request "
                              "handler failed after it had sent the headers")
    assert "TypeError: a bytes-like object is required, not 'str'" in problem
    assert "open the file with 'rb'" in problem
    assert "curl -s http://localhost:8765/" in _page_problem((200, "text/html", ""), 8765, ["public/index.html"])


@pytest.mark.skipif(shutil.which("python") is None, reason="no `python` on PATH")
def test_the_run_check_opens_the_page_and_sees_it_empty(tmp_path, monkeypatch):
    import socket
    from src.utils import program_run_check
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(program_run_check, "RUN_CHECK_SECONDS", 4.0)
    monkeypatch.setattr(program_run_check, "HTTP_PROBE_DELAY_SECONDS", 1.5)
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text("<html><body>Snake</body></html>\n", encoding="utf-8")
    (tmp_path / "server.py").write_text(STR_SERVER.replace("8765", str(port)), encoding="utf-8")
    problem = asyncio.run(program_run_check.program_run_problem(tmp_path))
    assert problem and "answers 200 with an EMPTY page" in problem, problem
    assert "TypeError: a bytes-like object is required" in problem
    (tmp_path / "server.py").write_text(STR_SERVER.replace("8765", str(port)).replace("'r'", "'rb'"), encoding="utf-8")
    assert asyncio.run(program_run_check.program_run_problem(tmp_path)) is None


# ============================================================================== 2. an imported serve() nobody calls
def test_a_websockets_import_alone_accepts_no_websocket():
    from src.utils.product_check import accepts_websocket
    assert not accepts_websocket("from websockets.server import serve\nasync def _handle_websocket(r, w):\n    pass\n")
    assert accepts_websocket("from websockets.server import serve\nasync def main():\n    async with serve(h, '', 8765):\n"
                             "        await asyncio.Future()\n")
    assert accepts_websocket("import websockets\nstart = websockets.serve(handler, '0.0.0.0', 8765)\n")
    assert accepts_websocket("from websockets.asyncio.server import (serve, ServerConnection)\nserve(h, port=1)\n")
    assert accepts_websocket("const WebSocket = require('ws');\nconst wss = new WebSocket.Server({ server });\n")
    assert not accepts_websocket("import websockets\n")


# the run's server.py, shortened: the websockets import, a handler nothing starts, the HTTP server that sends the page
RUN_SERVER = """import json
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from websockets.server import serve
from websockets.exceptions import ConnectionClosed


class HTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open('public/index.html', 'r') as f:
                self.wfile.write(f.read())
        elif path == '/client.js':
            with open('public/client.js', 'r') as f:
                self.wfile.write(f.read())
        elif path == '/api/leaderboard':
            self.wfile.write(json.dumps([]).encode())


class Server:
    def __init__(self, host='0.0.0.0', port=8765):
        self.host, self.port = host, port
        self.http_server = None

    def start(self):
        self.http_server = HTTPServer((self.host, self.port), HTTPHandler)
        self.http_server.serve_forever()

    async def _handle_websocket(self, reader, writer):
        try:
            async for message in reader:
                await writer.write(message)
        except ConnectionClosed:
            pass


if __name__ == '__main__':
    Server().start()
"""


def test_the_page_check_names_the_runs_server(tmp_path):
    from src.utils.product_check import page_server_problems
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text('<html><body><canvas></canvas><script src="client.js"></script></body>'
                                                    '</html>\n', encoding="utf-8")
    (tmp_path / "public" / "client.js").write_text("const ws = new WebSocket(`ws://${window.location.host}`);\n"
                                                   "fetch('/api/leaderboard');\n", encoding="utf-8")
    (tmp_path / "server.py").write_text(RUN_SERVER, encoding="utf-8")
    problems = page_server_problems(tmp_path)
    assert problems and problems[0].startswith("`public/index.html` opens a WebSocket (`new WebSocket(...)` in the page or "
                                               "its script), but NO server file of the project ACCEPTS WebSocket"), problems

# END OF FILE tests/unit/test_round5n.py
