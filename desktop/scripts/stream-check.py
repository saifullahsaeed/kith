#!/usr/bin/env python3
"""Prove that an origin streams Server-Sent Events incrementally rather than buffering.

This is the acceptance test for the Electron shell's /api reverse proxy. Run it
against the Flask server to get a baseline, then against the Electron loopback
origin — the shapes must match. A buffering proxy looks perfectly healthy to an
ordinary request and only breaks streaming, so "it returned 200" proves nothing.
The timing is the test.

Deliberately raw sockets, not urllib: the thing under test is wire behaviour, and
a client library that transparently decodes chunked encoding (or does its own
buffering) would hide exactly the bug we are hunting.

How it decides, using GET /api/autonomy/stream — which emits a ": ping" keep-alive
every 15s while idle:

  * ``Content-Length`` must be absent. A length means the whole body was collected
    before sending, which is the classic buffering failure.
  * Bytes must arrive in at least two separate reads, more than 10s apart. One
    arrival proves nothing; a gap proves the connection stayed open and delivered
    as data was produced.

Note on a quiet system: with autonomy stopped the activity buffer is empty, so the
generator produces nothing until its first keep-alive — and Werkzeug withholds
response headers until the first yield. First bytes at ~15s is therefore CORRECT
here, not a fault. The test does not police first-byte latency for that reason.

Usage:
    stream-check.py http://127.0.0.1:8611     # baseline, straight to Flask
    stream-check.py http://127.0.0.1:PORT     # through the Electron shell
"""

from __future__ import annotations

import socket
import sys
import time
from urllib.parse import urlparse

PATH = "/api/autonomy/stream"
WINDOW_SECONDS = 34.0        # room for two 15s keep-alives
MIN_ARRIVALS = 2
MIN_GAP_SECONDS = 10.0


def main(base: str) -> int:
    parsed = urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    print(f"connecting: {host}:{port}{PATH}  (watching for {WINDOW_SECONDS:.0f}s)")

    started = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=5)
    except OSError as exc:
        print(f"FAIL: could not connect: {exc}")
        return 1

    request = (
        f"GET {PATH} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Accept: text/event-stream\r\n"
        "Connection: keep-alive\r\n"
        "\r\n"
    )
    sock.sendall(request.encode())
    sock.settimeout(1.0)

    raw = b""
    arrivals: list[float] = []
    try:
        while time.monotonic() - started < WINDOW_SECONDS:
            try:
                chunk = sock.recv(8192)
            except TimeoutError:
                continue
            except OSError as exc:
                print(f"  socket error: {exc}")
                break
            if not chunk:
                print("  connection closed by origin")
                break
            at = time.monotonic() - started
            arrivals.append(at)
            raw += chunk
            preview = chunk.replace(b"\r\n", b" ")[:96]
            print(f"  {at:5.2f}s  +{len(chunk):4d}B  {preview!r}")
    finally:
        sock.close()

    print()
    head, _, _ = raw.partition(b"\r\n\r\n")
    headers = head.decode(errors="replace")
    lowered = headers.lower()

    problems = []
    if not raw:
        problems.append("no bytes arrived at all")
    if "content-length:" in lowered:
        problems.append("Content-Length present — body was buffered, not streamed")
    if "text/event-stream" not in lowered:
        problems.append("Content-Type is not text/event-stream")
    if len(arrivals) < MIN_ARRIVALS:
        problems.append(f"only {len(arrivals)} arrival(s); need {MIN_ARRIVALS} to show incremental delivery")
    else:
        gap = arrivals[-1] - arrivals[0]
        if gap < MIN_GAP_SECONDS:
            problems.append(
                f"all bytes arrived within {gap:.2f}s — consistent with one buffered flush, "
                f"expected arrivals spread over >{MIN_GAP_SECONDS}s"
            )

    print("--- response head ---")
    for line in headers.splitlines():
        print(f"  {line}")
    print()

    if problems:
        print("FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"PASS  {len(arrivals)} arrivals over {arrivals[-1] - arrivals[0]:.1f}s, streamed incrementally")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
