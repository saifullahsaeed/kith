"""A page that is slow to answer is not a desktop app that has gone away.

The desktop shell registers itself with this server once, at launch. Every failure to reach it
used to drop that registration — a read timeout included — so one page that took longer than
forty seconds to settle left every later browser call, notification, rendered read and folder
picker reporting "no desktop app" until the app was restarted. The shell was right there the
whole time, finishing the slow page.

So the two are told apart: a timeout keeps the registration and says the page is slow; a refused
or dropped connection forgets it, which is what saves every later call from waiting to find out.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from kith.infra import renderer


class _Slow(BaseHTTPRequestHandler):
    """A shell that answers every call, eventually."""

    def do_POST(self) -> None:
        time.sleep(0.6)
        body = b'{"text": "late", "url": "", "title": ""}'
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # the caller stopped waiting, which is the point of the test

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def slow_shell(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(renderer, "_BROWSE_TIMEOUT", 0.2)
    monkeypatch.setattr(renderer, "_TIMEOUT_SECONDS", 0.2)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    renderer.register(url, "token")
    try:
        yield url
    finally:
        renderer.unregister()
        server.shutdown()
        server.server_close()


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_browser_call_that_times_out_keeps_the_app(slow_shell: str) -> None:
    answer = renderer.browse("open", args={"url": "https://example.com"})

    assert answer is not None
    assert "stopped waiting" in answer["error"]
    assert renderer.available(), "a slow page made the server forget the desktop app"


def test_a_render_that_times_out_keeps_the_app(slow_shell: str) -> None:
    with pytest.raises(RuntimeError, match="took longer"):
        renderer.render("https://example.com")

    assert renderer.available()


def test_an_app_that_is_not_there_is_forgotten() -> None:
    renderer.register(f"http://127.0.0.1:{_closed_port()}", "token")
    try:
        answer = renderer.browse("read")

        assert answer is not None
        assert "went away" in answer["error"]
        assert not renderer.available(), "a refused connection must drop the registration"
    finally:
        renderer.unregister()
