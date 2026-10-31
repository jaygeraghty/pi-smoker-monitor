"""The web server: Python's built-in HTTP server, wrapped around `WebApp`.

Chosen over a web framework because this page has one user at a time on a
Raspberry Pi 3: the standard library is plenty, starts fast, and adds
nothing to install. Each request gets its own thread (so a slow phone can't
block another), and `WebApp` does the real work.
"""

from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from smoker_monitor.web.api import MAX_BODY_BYTES, WebApp

log = logging.getLogger(__name__)


def make_server(app: WebApp, host: str, port: int) -> ThreadingHTTPServer:
    """A server ready to `serve_forever()`. Port 0 picks a free one (for tests)."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "smoker"
        sys_version = ""

        def do_GET(self) -> None:
            self._answer()

        do_HEAD = do_GET

        def do_PUT(self) -> None:
            self._answer()

        def do_POST(self) -> None:
            self._answer()

        def do_DELETE(self) -> None:
            self._answer()

        def _answer(self) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            # A body that's too big (or a length that makes no sense) is not
            # read at all: the app just says no, and the connection is closed.
            unreadable = length < 0 or length > MAX_BODY_BYTES
            body = b"" if unreadable else self.rfile.read(length)
            response = app.handle(
                self.command,
                self.path,
                dict(self.headers),
                body,
                self.client_address[0],
                declared_length=length if unreadable else None,
            )
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            for name, value in response.headers.items():
                self.send_header(name, value)
            if unreadable:
                self.send_header("Connection", "close")  # unread data is left on the wire
                self.close_connection = True
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            log.debug("%s %s", self.client_address[0], format % args)

    return ThreadingHTTPServer((host, port), Handler)
