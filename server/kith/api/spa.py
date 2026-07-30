"""Serve the built web UI from the Flask server itself.

Serving the UI from the API process means one origin, so the browser needs no
CORS and no proxy — and the desktop app has a single address to load.

Why the desktop build does it this way. The frontend makes 29 relative ``/api``
calls and streams two responses — NDJSON chat and an SSE activity feed. Anything
that puts a second hop in front of those has to reproduce nginx's streaming
semantics exactly, and getting it subtly wrong is worse than useless: a forwarded
``Content-Length`` truncates the body, a missing header flush stalls the feed, and
a 502 closes an ``EventSource`` *permanently* with no retry. Serving the UI from
the same origin as the API removes the hop, so there is nothing to reproduce and
no way to get it wrong.

Enabled by pointing ``KITH_UI_DIST`` at a built ``ui/dist``. Absent, the routes
are not registered at all and the server behaves exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, send_from_directory

from kith import settings

# Long-lived hashed asset filenames (index-B0MAEQC6.js) may be cached hard; the
# entry HTML must not be, or a rebuilt UI keeps loading yesterday's bundle.
_IMMUTABLE_MAX_AGE = 31_536_000  # one year
_ASSET_DIR = "assets"

# Paths the SPA fallback must never answer for. These belong to the API and the
# generated docs; a wrong turn under them is a 404, not a client-side route.
_RESERVED_PREFIXES = frozenset({"api", "docs", "openapi.json"})


def dist_dir() -> Path | None:
    """The built UI to serve, or None to leave SPA serving switched off."""
    configured = settings.UI_DIST
    if not configured:
        return None
    path = Path(configured).expanduser().resolve()
    return path if (path / "index.html").is_file() else None


def register(app: Flask) -> Path | None:
    """Attach SPA routes if a built UI is configured. Returns what it serves."""
    root = dist_dir()
    if root is None:
        return None

    @app.get("/")
    def _index():
        return _send(root, "index.html")

    # Everything that isn't /api and isn't a real file is a client-side route.
    # This is the try_files $uri $uri/ /index.html rule from nginx.conf: the app
    # uses BrowserRouter, so a deep link like /tasks must return index.html
    # rather than 404, or a reload on any sub-route breaks.
    @app.get("/<path:requested>")
    def _spa(requested: str):
        # Flask prefers the blueprint's literal rules over this converter, so
        # real endpoints still win. But an unknown /api path would otherwise fall
        # through to here and answer with HTML — a client expecting JSON should
        # get a 404, not an index page that parses as a successful response.
        if requested.split("/", 1)[0] in _RESERVED_PREFIXES:
            return {"error": "not found"}, 404

        candidate = (root / requested).resolve()
        # Resolve first, then confirm containment — otherwise ../ escapes the
        # directory. Serving arbitrary host files would be a real hole, since
        # this process can read everything the user can.
        if root in candidate.parents and candidate.is_file():
            return _send(root, str(candidate.relative_to(root)))
        return _send(root, "index.html")

    return root


def _send(root: Path, relative: str):
    response = send_from_directory(root, relative)
    if relative.startswith(f"{_ASSET_DIR}/"):
        response.headers["Cache-Control"] = f"public, max-age={_IMMUTABLE_MAX_AGE}, immutable"
    else:
        # index.html and the loose icons: always revalidate.
        response.headers["Cache-Control"] = "no-cache"
    return response
