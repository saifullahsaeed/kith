"""Who is allowed to talk to this server.

Nothing was. The API is bound to loopback and CORS was tightened to a short list of
origins, and both of those sound like protection until you look at what they actually stop:

* **CORS does not stop a request, it stops the reply being read.** A page on any website
  you happen to have open can fire ``POST /api/workspace/root`` or ``POST /api/chat`` at
  127.0.0.1 and the browser sends it. The attacker learns nothing from the response and
  does not need to — the side effect already happened.
* **Loopback is not a boundary on a desktop.** Every process running as you is on loopback
  too, so any script, any npm postinstall, any app you tried once could read every
  transcript and run shell commands through the tools.

So: a secret, in a file only your user can read, required on every API call. Two properties
matter and they are different from each other:

**Against other processes**, the value has to be secret — hence 0600 and
:func:`hmac.compare_digest`.

**Against web pages**, it is enough that a *custom header* is required at all. A
cross-origin ``X-Kith-Token`` forces a CORS preflight, the preflight is answered against
the allow-list, and the real request is never sent. That holds even if the token were
public, which makes it the part that closes the browser hole.

The token persists rather than being minted per boot. A per-boot secret is marginally
better on paper and much worse in practice here: the Vite dev server has to read the same
value to proxy in dev, and a token that changes under it turns every server restart into a
mystery. It lives beside the databases, which are already the crown jewels — anyone who can
read the token file can read his memory directly.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
from pathlib import Path

from flask import jsonify, request

HEADER = "X-Kith-Token"

#: Where the secret lives. Beside the databases on purpose: the threat model for the token
#: is exactly the threat model for them, so a reader who can reach one can reach the other
#: and putting the token somewhere "safer" would be theatre.
FILENAME = "api.token"

#: Paths that answer without a token, and why each one has to.
#:
#: ``/api/health`` — the shell polls this before the window exists to know whether the
#: server is up. It returns nothing but liveness.
#:
#: ``/api/events`` — a browser `EventSource`, which cannot send headers. Putting the token in the
#: query string instead would print it into the request log on every reconnect, which is a worse
#: leak than the one being closed. So it is gated on being same-origin instead (see
#: :func:`_same_origin`): a web page cannot subscribe, a local process can, and what it gets is
#: status lines, token counts and the *names* of things that changed — never a value.
#:
#: This used to be two entries, `/api/activity/stream` and `/api/changes`, and shrinking it to one
#: is most of what could be shrunk. The rest is real: **the desktop app no longer relies on this
#: exemption at all.** Its stream is held by the Electron main process over Node's HTTP client,
#: which sends `X-Kith-Token` like every other call — so the exemption now covers only a browser
#: tab opened against the server directly, which is a development shape rather than the product.
OPEN_PATHS = frozenset({"/api/health", "/api/events"})

#: ``/api/plugins/frame/<ticket>`` — a frame's ``src``, and a navigation cannot carry a custom
#: header any more than an EventSource can. Same trade, and a smaller one: the ticket is 24
#: random bytes from `secrets.token_urlsafe`, handed out over an authenticated POST, so guessing
#: it is the only way in, and what it buys is a document already on this machine's disk.
#: Same-origin gated like the others. (``/api/canvas/<id>`` sat here on the same terms until the
#: ```html canvas was retired.)
#:
#: **The unguessable ticket is the whole of it, so a stable path must never be added here.**
#: `/api/plugins/<id>/surface/<view>` would be a plugin surface readable by anything that can
#: reach this port and knows a plugin's name, and the exemption would go from "a secret in the
#: URL" to "no gate at all". Only the GET is exempt: the ticket's `POST .../state` and its
#: DELETE carry the header like everything else, because a write is not a navigation.
OPEN_GET_PREFIXES = ("/api/plugins/frame/",)

#: Documentation. Serving the schema of an API someone cannot call is not a leak, and
#: locking it means /docs is a login wall on a single-user machine.
OPEN_PREFIXES = ("/docs", "/openapi.json")

_cached: str | None = None


def token(data_dir: Path) -> str:
    """The shared secret, created on first use.

    Written with 0600 *before* anything goes in it — creating a world-readable file and
    then chmod'ing it leaves a window where the secret is readable, which is the whole
    thing this is for.
    """
    global _cached
    if _cached:
        return _cached
    path = Path(data_dir) / FILENAME
    if path.is_file():
        existing = path.read_text().strip()
        if existing:
            _cached = existing
            return _cached
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = secrets.token_urlsafe(32)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as file:
        file.write(fresh)
    _cached = fresh
    return fresh


def _same_origin() -> bool:
    """Is this request from our own page, rather than from another site?

    Only meaningful for browsers — a local process can claim whatever it likes — and it is
    only used where a header is impossible. ``Sec-Fetch-Site`` is set by the browser and
    cannot be spoofed by page script, so it is preferred; the absence of both headers means
    a non-browser caller, which this check is not the one guarding against.
    """
    site = request.headers.get("Sec-Fetch-Site")
    if site:
        return site in {"same-origin", "none"}
    origin = request.headers.get("Origin")
    if origin:
        return origin.rstrip("/") == request.host_url.rstrip("/")
    return True


#: Extra names this server may be reached by, for a setup that puts it behind one — a container
#: reached by service name, a machine on the LAN. Comma-separated; empty for the desktop app.
ALLOWED_HOSTS_ENV = "KITH_ALLOWED_HOSTS"


def _known_host() -> bool:
    """Is this request addressed to this machine by a name only this machine can mean?

    **The defence against DNS rebinding, and the one the token cannot provide.** The token stops
    a cross-site page because it must travel in a custom header, and a cross-origin custom header
    forces a preflight. A rebound page is not cross-origin: it lives at ``http://evil.example:8611``,
    its name is repointed at 127.0.0.1, and from then on it is fetching *its own origin* — so it
    can read ``/`` (which is served without a token, and carries the token in the page) and then
    call the API with it, preflight-free. The only thing that differs from a real request is the
    ``Host`` header, which names the attacker's domain.

    So a request must be addressed by IP literal, ``localhost`` or a ``.localhost`` name — none of
    which a stranger's DNS can repoint — or by a name listed in ``KITH_ALLOWED_HOSTS``. No Host at
    all is a non-browser client, which this is not the check for.
    """
    header = (request.host or "").strip().lower()
    if not header:
        return True
    if header.startswith("["):
        name = header[1 : header.find("]")] if "]" in header else ""
    else:
        name = header.rsplit(":", 1)[0] if header.count(":") == 1 else header
    name = name.rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    extra = {one.strip().lower() for one in os.environ.get(ALLOWED_HOSTS_ENV, "").split(",") if one.strip()}
    return name in extra


def register(app, data_dir: Path) -> str:
    """Require the token on every API call. Returns it, for the SPA to hand to the page."""
    expected = token(data_dir)

    @app.before_request
    def _check():  # pyright: ignore[reportUnusedFunction]
        # Before anything else, the page at `/` included: that page carries the token.
        if not _known_host():
            return jsonify({"error": "this server does not answer to that name"}), 403
        path = request.path
        if path.startswith(OPEN_PREFIXES):
            return None
        if not path.startswith("/api/"):
            return None  # the SPA and its assets; nothing to protect and no way to send one
        if request.method == "OPTIONS":
            return None  # the preflight itself, which flask-cors answers
        if path in OPEN_PATHS or (request.method == "GET" and path.startswith(OPEN_GET_PREFIXES)):
            return None if _same_origin() else (jsonify({"error": "cross-site request"}), 403)
        presented = request.headers.get(HEADER, "")
        if presented and hmac.compare_digest(presented, expected):
            return None
        # Deliberately unhelpful about *why*. "Wrong token" versus "no token" is a hint, and
        # the only caller who needs to know either is one that can read the file anyway.
        return jsonify({"error": "not authorised"}), 401

    return expected
