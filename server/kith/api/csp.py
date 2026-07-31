"""Content Security Policy for the served UI.

Only applies when this process serves the SPA (the desktop build). In Docker, nginx
is in front and owns response headers for the UI.

The policy is computed from the built ``index.html`` rather than written by hand,
because the one inline script in it is load-bearing: it sets the dark class before
paint, and blocking it means a white flash on every single launch. A hand-written
policy gets that wrong once and nobody connects the flash to the header.

Three deliberate relaxations, all consequences of how the UI is built:

* ``style-src`` allows inline. The app uses ``style={{…}}`` in several components and
  tints its whole surface by his current mood at runtime; there is no nonce path for
  React inline styles.
* ``img-src`` allows ``data:`` and ``blob:``. Icons are inlined and file previews are
  built client-side. Remote images stay blocked, which also means agent-authored
  markdown cannot silently phone home by referencing an image.
* ``object-src`` allows ``blob:``. A PDF he wrote is shown in the viewer by handing a
  blob to the browser's own PDF reader, and ``object-src`` is the directive that governs
  the ``<embed>`` it lives in. Scoped to ``blob:`` alone — no ``'self'``, no remote — so
  it permits exactly "render bytes this page already fetched and nothing else". The
  alternative was refusing to show a PDF at all, or shipping a JS PDF renderer to avoid
  one CSP token, and neither is a better trade on a local single-user app.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from flask import Flask

_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)

# Same-origin only. The UI talks to /api on its own origin and nothing else, so
# there is no host to allow-list here — connect-src 'self' covers fetch and SSE.
_BASE = (
    "default-src 'self'",
    "script-src 'self' {script_hashes}",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "media-src 'self'",
    "object-src blob:",
    "frame-src blob:",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'none'",
)


def policy_for(dist: Path, extra_inline: Sequence[str] = ()) -> str:
    """Build the policy, hashing whatever inline scripts the page will actually have.

    ``extra_inline`` is for scripts this process adds to index.html on the way out rather
    than ones the bundle shipped with — the API token handed to the page is one. They are
    passed in rather than looked up so this module needs to know nothing about them.
    """
    sources = [*_inline_scripts(dist / "index.html"), *extra_inline]
    hashes = " ".join(_hash(source) for source in sources)
    return "; ".join(rule.format(script_hashes=hashes).strip() for rule in _BASE)


def _inline_scripts(index: Path) -> list[str]:
    try:
        html = index.read_text()
    except OSError:
        return []
    return [body for body in _INLINE_SCRIPT.findall(html) if body.strip()]


def _hash(source: str) -> str:
    """A CSP script hash covers the element's exact text — byte for byte."""
    digest = hashlib.sha256(source.encode()).digest()
    return f"'sha256-{base64.b64encode(digest).decode()}'"


def register(app: Flask, dist: Path, extra_inline: Sequence[str] = ()) -> str:
    """Attach the policy to UI responses. Returns it, for logging at startup."""
    policy = policy_for(dist, extra_inline)

    @app.after_request
    def _apply(response):
        # HTML only. Putting a CSP on JSON tool results or a downloaded workspace
        # file achieves nothing and confuses anyone reading the headers.
        if response.mimetype == "text/html":
            response.headers.setdefault("Content-Security-Policy", policy)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    return policy
