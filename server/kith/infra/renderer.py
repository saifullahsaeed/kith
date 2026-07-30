"""Render a page with the desktop app's Chromium, when the desktop app is running.

Kith needs a real browser for JS-heavy or WAF-protected sites. There are two ways
to give him one:

* **Playwright in his sandbox** — always available, but a second Chromium download
  (~150 MB), its own install step, and something a packaged app would have to ship.
* **The desktop shell's Chromium** — already installed, already updated with
  Electron, nothing extra to ship.

So the shell is preferred and the sandbox is the fallback. The shell registers
itself at startup (``POST /api/renderer``) because it is a separate process and
the server may well have been running first.

Registration is deliberately in-memory: the endpoint's port and token are minted
per launch, so a value persisted to disk would be stale by definition and only
useful for confusing a later run.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Rendering is a page load plus a settle delay; the shell caps itself at 45s, so
# allow a little beyond that before deciding it is unreachable.
_TIMEOUT_SECONDS = 55

# Docker Desktop's alias for the machine the container runs on.
_HOST_FROM_CONTAINER = "host.docker.internal"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Endpoint:
    """Where the desktop renderer is, and the secret needed to use it."""

    url: str
    token: str


_lock = threading.Lock()
_endpoint: Endpoint | None = None


def register(url: str, token: str) -> None:
    """Remember a renderer. Called by the desktop shell as it starts."""
    global _endpoint
    if not url or not token:
        raise ValueError("both url and token are required")
    with _lock:
        _endpoint = Endpoint(url=_reachable(url.rstrip("/")), token=token)


def _reachable(url: str) -> str:
    """Translate the shell's address into one this process can actually dial.

    The shell binds to 127.0.0.1 and reports that, which is correct from where it
    is standing. But when this server runs inside a container, 127.0.0.1 is the
    *container's* own loopback — the render service is on the host, and the request
    would fail with a connection refused that looks like the shell isn't running.

    Running the server natively makes this a no-op, which is the better end state.
    """
    if not _in_container():
        return url
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname not in _LOOPBACK_HOSTS:
        return url
    port = f":{parsed.port}" if parsed.port else ""
    rewritten = urllib.parse.urlunsplit(
        (parsed.scheme, f"{_HOST_FROM_CONTAINER}{port}", parsed.path, parsed.query, parsed.fragment)
    )
    print(f"[kith] renderer is on the host; using {rewritten} from inside the container")
    return rewritten


def _in_container() -> bool:
    return Path("/.dockerenv").exists()


def unregister() -> None:
    """Forget the renderer — the shell is quitting."""
    global _endpoint
    with _lock:
        _endpoint = None


def available() -> bool:
    with _lock:
        return _endpoint is not None


def describe() -> str:
    with _lock:
        return _endpoint.url if _endpoint else "(none registered)"


def render(url: str) -> str | None:
    """Visible text of a rendered page, or None if no renderer is registered.

    Returning None rather than raising is the point: "the desktop app isn't
    running" is not an error, it is a signal to the caller to use the sandbox
    instead. A genuine failure to render — a timeout, a refused URL — does raise,
    because falling back silently would hide a real problem behind a slower path.
    """
    with _lock:
        endpoint = _endpoint
    if endpoint is None:
        return None

    payload = json.dumps({"url": url}).encode()
    request = urllib.request.Request(
        f"{endpoint.url}/render",
        data=payload,
        headers={"Content-Type": "application/json", "X-Kith-Token": endpoint.token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise RuntimeError(f"desktop renderer refused this page ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        # The shell has gone away (quit, crashed, restarted on a new port). Drop the
        # stale registration so later calls go straight to the sandbox instead of
        # paying this timeout every time.
        unregister()
        raise RuntimeError(f"desktop renderer unreachable, forgetting it: {exc}") from exc

    text = body.get("text")
    if not isinstance(text, str):
        raise RuntimeError("desktop renderer returned no text")
    return text


def notify(title: str, body: str) -> bool:
    """Post a native notification through the desktop shell.

    False when there is no shell registered — running as a bare server, there is no app
    identity for macOS to attribute a notification to, and saying so is better than
    pretending it went out.

    This exists because the renderer's own Notification API does not work: it reports
    permission "granted", throws nothing, and macOS drops the notification, because a
    notification from a page has no app to attribute. The shell's main process does.
    """
    return _ask("/notify", {"title": title, "body": body}) is not None


def open_settings_pane(pane: str) -> bool:
    """Open one of macOS's own settings panes, by name.

    By name rather than by URL: the pane list lives in the shell. A page asking for
    "x-apple.systempreferences:<anything>" is a wider capability than one button needs,
    and deliverables carry agent-authored links.
    """
    return _ask("/open-pane", {"pane": pane}) is not None


def _ask(route: str, payload: dict) -> dict | None:
    """One short request to the shell. None when it is not there or says no.

    Short timeout on purpose: these are things a person is waiting on with a finger still
    on the button, and a shell that has gone away should fail immediately rather than
    holding the click for a minute.
    """
    with _lock:
        endpoint = _endpoint
    if endpoint is None:
        return None
    request = urllib.request.Request(
        f"{endpoint.url}{route}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Kith-Token": endpoint.token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None
