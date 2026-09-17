"""The HTTP client: where Kith is, how to prove you may talk to him, and how to read a stream.

Three things here are load-bearing and none of them are obvious.

**The token is found, not configured, and finding it is a guess the server settles.** The
server writes a 0600 file next to its databases and the desktop app injects its contents into
the page it serves; a CLI has neither of those routes, so it reads the file. The trap is that
"next to its databases" is two different places — ``~/.kith`` when frozen, ``server/data`` from
a checkout (``settings._DEFAULT_DATA_DIR``) — and that a client resolves it from *its own*
build type, not from the server it is about to talk to. The installed ``kith`` is frozen; the
server on this port is usually a checkout; both files exist on any machine that has ever run a
packaged build. So the first readable file is not evidence of anything, and treating it as the
answer is a 401 that looks like a server fault. Every token is collected instead, and a 401 is
read as "wrong file, try the next one" rather than as a verdict. ``kith status`` prints which
one the server accepted.

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


def token_options() -> list[tuple[str, str]]:
    """Every token readable on this machine, best guess first, as ``(value, source)``.

    ``$KITH_TOKEN`` is alone when it is set: someone who names a token means that token, and
    silently using a different one would hide the typo they need to see.

    Otherwise this is a *list* rather than a choice, because nothing here can tell which file
    belongs to the server on the other end. ``settings.DATA_DIR`` answers for this binary —
    frozen means ``~/.kith`` — while the server answering on the port may be a checkout using
    ``server/data``, and a machine that has run both has both files. Existing is not the same
    as being the right one; see `Client._open`, which lets the server decide.
    """
    from_env = (os.environ.get("KITH_TOKEN") or "").strip()
    if from_env:
        return [(from_env, "$KITH_TOKEN")]
    options: list[tuple[str, str]] = []
    for candidate in token_candidates():
        try:
            value = candidate.read_text().strip()
        except OSError:
            continue
        if value and not any(value == known for known, _ in options):
            options.append((value, str(candidate)))
    return options


def find_token() -> tuple[str, str]:
    """The best guess at the token, and where it came from. Raises if there is none to find.

    The source travels with the value because ``kith status`` reports it and a 401 is otherwise
    undiagnosable: the two failures — wrong file, stale file — look identical from the server.
    """
    options = token_options()
    if options:
        return options[0]
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
        #: Tokens not yet tried, for `_open` to fall through to on a 401. Empty when the caller
        #: named a token: an explicit value is an instruction, not a guess to be second-guessed.
        self._spares: list[tuple[str, str]] = []
        #: `/api/health` is asked for the server's data directory at most once, and only after
        #: every file has been refused — see `_token_the_server_points_at`.
        self._asked_the_server = bool(token) or bool((os.environ.get("KITH_TOKEN") or "").strip())
        if token:
            self.token = token
        else:
            options = token_options()
            if not options:
                find_token()  # raises the "no API token found" failure, with where it looked
            self.token, self.token_source = options[0]
            self._spares = options[1:]
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
            if refused.code == 401 and not self._spares:
                # Nothing left to guess with, so stop guessing and ask. `/api/health` needs no
                # token and reports the server's own data directory — which is the only way a
                # frozen CLI can name the `server/data` of a checkout it was not built from.
                # Once, per client: a second round would be the same answer.
                asked = self._token_the_server_points_at()
                if asked:
                    self._spares.append(asked)
            if refused.code == 401 and self._spares:
                # A 401 is the only authority on which file belongs to this server, so it is
                # taken as one: adopt the next token and send the same request again. Safe to
                # repeat because the rejection happened in `api.auth` before the handler ran,
                # so nothing has been created, and cheap because it happens at most once per
                # file and the one that answers is kept for the rest of the process.
                self.token, self.token_source = self._spares.pop(0)
                request.add_header(HEADER, self.token)
                return self._open(request)
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

    def _token_the_server_points_at(self) -> tuple[str, str] | None:
        """The token in the directory `/api/health` names, if that is somewhere new.

        Returns ``None`` — never raises — for every failure: this runs while reporting a 401,
        and a diagnostic path that throws its own error replaces a message about the token with
        a message about the diagnostic. An older server that does not send ``dataDir`` simply
        has no answer, and the original 401 stands.
        """
        if self._asked_the_server:
            return None
        self._asked_the_server = True
        try:
            request = urllib.request.Request(f"{self.url}/api/health", headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=min(self.timeout, 10.0)) as response:
                payload = json.loads(response.read().decode() or "{}")
            directory = str((payload or {}).get("dataDir") or "").strip()
            if not directory:
                return None
            source = Path(directory) / TOKEN_FILENAME
            value = source.read_text().strip()
        except Exception:
            return None
        if not value or value == self.token:
            return None
        return value, str(source)

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


__all__ = [
    "HEADER",
    "Client",
    "base_url",
    "connect",
    "die",
    "find_token",
    "token_candidates",
    "token_options",
]
