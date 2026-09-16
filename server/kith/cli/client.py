"""The HTTP client: where Kith is, how to prove you may talk to him, and how to read a stream.

Three things here are load-bearing and none of them are obvious.

**The token is found, not configured.** The server writes a 0600 file next to its databases
and the desktop app injects its contents into the page it serves; a CLI has neither of those
routes, so it reads the file. The trap is that "next to its databases" is two different places:
``~/.kith`` when frozen, ``server/data`` from a checkout (``settings._DEFAULT_DATA_DIR``). A
client that only knows the first works for everyone who installed Kith and fails for everyone
who is building it. Both are tried, in that order, and ``kith status`` prints which one
answered — because the failure mode otherwise is a 401 that looks like a server fault.

**The stream is read line by line, not buffered.** ``/api/chat`` returns
``application/x-ndjson`` and the whole point is that it arrives as it happens. ``urlopen``
gives a file object whose ``readline`` returns as soon as a line is complete, so the loop here
is the streaming — but only as long as nothing wraps it in something that reads ahead. That is
the reason this file contains a hand-rolled loop instead of ``for line in response``, which is
free to buffer.

**The raw line survives parsing.** ``--json`` promises Claude the server's own documented
contract, byte for byte, and the moment this module re-serialises a parsed dict that promise is
quietly broken — key order changes, floats round-trip differently, and a field added to the
server appears reformatted. So `stream` yields *both* halves: the original text to print and
the parsed object to act on.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kith import settings
from kith.cli.errors import FAILED, UNAVAILABLE, Failure, die

#: Must equal ``kith.api.auth.HEADER``. Not imported from there: that module pulls in Flask,
#: and a CLI that imports a web framework to learn a string pays for it on every invocation.
#: ``tests/test_the_cli_is_only_a_client.py`` asserts the two agree, which is the same
#: across-the-seam technique ``test_the_client_types_are_not_lying.py`` uses on TypeScript.
HEADER = "X-Kith-Token"

#: Must equal ``kith.api.auth.FILENAME``, for the same reason.
TOKEN_FILENAME = "api.token"

#: Where the server listens when nothing says otherwise — the same port ``run`` and the
#: desktop app use.
DEFAULT_URL = "http://127.0.0.1:8611"

#: Long enough that a fold, a slow provider or a twelve-round turn does not look like a hang.
#: This is a *socket* timeout, so it is the gap between bytes rather than the length of the
#: turn: a turn that keeps streaming never trips it however long it runs.
DEFAULT_TIMEOUT = 600.0


def base_url() -> str:
    return (os.environ.get("KITH_URL") or DEFAULT_URL).rstrip("/")


def token_candidates() -> list[Path]:
    """Every place the token could be, best first, with duplicates removed.

    ``settings.DATA_DIR`` already honours ``$KITH_DATA_DIR`` and already resolves frozen versus
    checkout, so it is asked first rather than second-guessed. ``~/.kith`` follows it for the
    case this is a frozen CLI talking to a server someone is running from a checkout — the two
    binaries disagree about ``DATA_DIR`` and the home directory is where they meet.
    """
    seen: list[Path] = []
    for directory in (settings.DATA_DIR, Path.home() / ".kith"):
        candidate = Path(directory) / TOKEN_FILENAME
        if candidate not in seen:
            seen.append(candidate)
    return seen


def find_token() -> tuple[str, str]:
    """The token and where it came from. Raises if there is none to find.

    The source travels with the value because ``kith status`` reports it and a 401 is otherwise
    undiagnosable: the two failures — wrong file, stale file — look identical from the server.
    """
    from_env = (os.environ.get("KITH_TOKEN") or "").strip()
    if from_env:
        return from_env, "$KITH_TOKEN"
    for candidate in token_candidates():
        try:
            value = candidate.read_text().strip()
        except OSError:
            continue
        if value:
            return value, str(candidate)
    looked = " or ".join(str(path) for path in token_candidates())
    raise Failure(
        "no API token found",
        UNAVAILABLE,
        f"looked in {looked} — the server writes it on first run",
    )


class Client:
    """One conversation with the server. Cheap to build; holds a token and a base URL."""

    def __init__(self, url: str = "", token: str = "", timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = (url or base_url()).rstrip("/")
        self.token_source = ""
        if token:
            self.token = token
        else:
            self.token, self.token_source = find_token()
        self.timeout = timeout

    # ----------------------------------------------------------------- requests

    def _request(self, method: str, path: str, body: Any = None, params: dict | None = None):
        target = f"{self.url}/api{path}"
        if params:
            clean = {key: value for key, value in params.items() if value not in (None, "")}
            if clean:
                target = f"{target}?{urllib.parse.urlencode(clean)}"
        data = None
        headers = {HEADER: self.token, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(target, data=data, headers=headers, method=method)

    def _open(self, request: urllib.request.Request):
        """Send it, and turn every transport failure into a `Failure` that says what to do.

        ``URLError`` wrapping ``ConnectionRefusedError`` is the overwhelmingly common case and
        has exactly one fix, so it gets the one hint worth printing. Everything else keeps the
        server's own message: a 400 from ``/api/tuning`` explains which knob was wrong far
        better than anything this layer could invent.
        """
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as refused:
            detail = ""
            try:
                payload = json.loads(refused.read().decode() or "{}")
                detail = str(payload.get("error") or payload.get("message") or "")
            except Exception:
                pass
            if refused.code == 401:
                raise Failure(
                    "the server rejected the API token",
                    FAILED,
                    f"read from {self.token_source or 'an explicit value'} — is this the same Kith?",
                ) from refused
            raise Failure(detail or f"server said {refused.code}", FAILED) from refused
        except urllib.error.URLError as unreachable:
            raise Failure(
                f"no server on {self.url.removeprefix('http://')}",
                UNAVAILABLE,
                "start it with: ./run server",
            ) from unreachable
        except TimeoutError as slow:
            raise Failure(f"timed out after {self.timeout:g}s waiting for {self.url}", FAILED) from slow

    def get(self, path: str, **params: Any) -> Any:
        with self._open(self._request("GET", path, params=params)) as response:
            return json.loads(response.read().decode() or "null")

    def post(self, path: str, body: Any = None) -> Any:
        with self._open(self._request("POST", path, body=body if body is not None else {})) as response:
            return json.loads(response.read().decode() or "null")

    def patch(self, path: str, body: Any) -> Any:
        with self._open(self._request("PATCH", path, body=body)) as response:
            return json.loads(response.read().decode() or "null")

    def put(self, path: str, body: Any) -> Any:
        with self._open(self._request("PUT", path, body=body)) as response:
            return json.loads(response.read().decode() or "null")

    def delete(self, path: str) -> Any:
        with self._open(self._request("DELETE", path)) as response:
            return json.loads(response.read().decode() or "null")

    # ------------------------------------------------------------------ streams

    def stream(self, path: str, body: Any = None) -> Iterator[tuple[str, dict]]:
        """Read an NDJSON endpoint, yielding `(raw line, parsed event)` as each arrives.

        Blank lines are dropped rather than parsed. The server opens every stream with one —
        deliberately, so that the HTTP response headers are written before the turn has
        produced anything and the client can tell it is connected (see ``api.routes.chat._ndjson``).
        A client that treats it as an event would report a parse error on every single call.

        A line that is not JSON is also dropped rather than raised on. The stream is a
        sequence of independent events and one malformed line does not invalidate the ones
        after it; killing the turn over a line we could not read would throw away the answer.
        """
        method = "POST" if body is not None else "GET"
        request = self._request(method, path, body=body)
        with self._open(request) as response:
            while True:
                raw = response.readline()
                if not raw:
                    return
                text = raw.decode("utf-8", "replace")
                stripped = text.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield text if text.endswith("\n") else text + "\n", event

    # ------------------------------------------------------------------ probing

    def alive(self) -> bool:
        """Is anything answering? Used by `status`, which must report rather than fail."""
        try:
            self.get("/health")
            return True
        except Failure:
            return False


def connect(timeout: float = DEFAULT_TIMEOUT) -> Client:
    """A client, or a `Failure` explaining which half is missing."""
    return Client(timeout=timeout)


__all__ = ["HEADER", "Client", "base_url", "connect", "die", "find_token", "token_candidates"]
