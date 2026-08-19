"""A canvas document, served with a policy of its own.

The UI renders an ``html`` fence he wrote as a live frame. It cannot do that from ``srcdoc``,
and the reason is worth stating once because it is not obvious and it cost a shipped feature:

**A framed document on a local scheme inherits the embedder's CSP.** ``srcdoc``, ``blob:`` and
``data:`` all behave this way — measured in Chromium, all three, not assumed from the spec. The
app is served with ``script-src 'self'`` (see ``csp.py``), so a canvas mounted any of those ways
renders its markup and its stylesheet and then silently runs none of its script. Which is most of
what a canvas is for. The frame's own ``<meta>`` policy cannot help: policies compose by
intersection, so an inner one can only ever take more away.

A document fetched over http is not on a local scheme and does not inherit anything. It carries
the policy in this module instead — which is the same seal, applied at the only layer that can
actually apply it.

Handing the canvas a real URL does not hand it a real origin. The frame is still mounted
``sandbox="allow-scripts"`` with no ``allow-same-origin``, so its document origin stays opaque:
storage, cookies and the parent DOM all throw ``SecurityError`` from inside, and its own policy
denies it every way of fetching anything. That was measured too, from inside the frame, rather
than reasoned about.

Kept in memory rather than on disk. A canvas is a view of a message that already exists in the
transcript, so persisting it would be a second copy of something we already have, with a lifetime
nobody would remember to manage. Losing them on restart costs a re-POST the UI makes anyway when
the frame mounts.
"""

from __future__ import annotations

import secrets
from collections import OrderedDict
from threading import Lock

from flask import Response, abort, request

from kith.api.blueprint import api

#: What the canvas may do. The mirror of ``FRAME_SANDBOX``/``POLICY`` in ``ui/src/lib/canvas.ts``
#: — the UI bakes the same policy into the document as a ``<meta>``, and this is the copy that has
#: authority. Two statements of one rule is a cost, but the alternative is a document whose only
#: policy arrives from a server it might not have come from.
#:
#: ``default-src 'none'`` carries it: connect, frame, worker, form and object all inherit that
#: denial. The exceptions are what a drawing needs and none of them leave the page — inline style,
#: inline script, and data-URI images, fonts and media. Not ``'unsafe-eval'``: there is no network
#: to load a library from, so nothing legitimate needs it.
POLICY = "; ".join(
    (
        "default-src 'none'",
        "style-src 'unsafe-inline'",
        "script-src 'unsafe-inline'",
        "img-src data: blob:",
        "font-src data:",
        "media-src data: blob:",
        "form-action 'none'",
        "base-uri 'none'",
    )
)

#: A ceiling on one document. Generous next to anything he actually writes — the animated pages in
#: the transcripts run 4-6 KB — and small enough that a runaway reply cannot park a large object
#: here.
MAX_BYTES = 2_000_000

#: How many to keep. One conversation on screen is a handful of canvases; the oldest going first
#: only means a frame that scrolls back into view re-POSTs, which it does on mount regardless.
LIMIT = 64

_store: OrderedDict[str, bytes] = OrderedDict()
_lock = Lock()


def _remember(document: bytes) -> str:
    """Keep a document and return the unguessable name it will be served under.

    Random rather than a hash of the content. A content address would mean anyone able to guess
    the document could confirm it exists here, and the id is the only thing standing between a
    local process and the read — see ``auth.py`` for why this route cannot require the token.
    """
    name = secrets.token_urlsafe(24)
    with _lock:
        _store[name] = document
        while len(_store) > LIMIT:
            _store.popitem(last=False)
    return name


@api.post("/canvas")
@api.doc(
    summary="Hold a canvas document for framing",
    description="Takes the sealed HTML the UI built and returns the path to frame it from.",
)
def put_canvas():
    payload = request.get_json(silent=True) or {}
    html = payload.get("html")
    if not isinstance(html, str) or not html.strip():
        return {"error": "html is required"}, 400
    document = html.encode("utf-8")
    if len(document) > MAX_BYTES:
        return {"error": "canvas too large"}, 413
    name = _remember(document)
    return {"id": name, "url": f"/api/canvas/{name}"}


@api.get("/canvas/<name>")
@api.doc(
    summary="Serve a held canvas document",
    description="Returns the document under the sealed policy. Framed, never navigated to.",
)
def get_canvas(name: str):
    with _lock:
        document = _store.get(name)
    if document is None:
        abort(404)
    response = Response(document, mimetype="text/html")
    # Set rather than defaulted, and set before ``csp.py``'s ``after_request`` runs — that one
    # uses ``setdefault``, so this policy is the one that survives. Without this line the canvas
    # would be served under the *app's* policy, which is the bug this whole module exists for.
    response.headers["Content-Security-Policy"] = POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Nothing here is worth a second read from a cache we do not control, and the id is single-use
    # in practice.
    response.headers["Cache-Control"] = "no-store"
    return response
