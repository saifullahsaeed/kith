"""Kith — a small, independent Flask service.

It talks to a local Ollama, owns the autonomous persona and chat parameters,
splits reasoning from the answer, and streams both. The web UI is just one
client; the API is documented with OpenAPI (Swagger UI at /docs) so anything can
drive it.
"""

from __future__ import annotations

from apiflask import APIFlask
from flask_cors import CORS

from kith import settings
from kith.api import csp, spa
from kith.api.routes import api
from kith.autonomy import runner
from kith.config import AGENT_DB_PATH, CONFIG_DB_PATH
from kith.infra.db import config_store, migrations
from kith.services import embeddings
from kith.services.persona import fragment_paths

__all__ = ["create_app"]


def create_app() -> APIFlask:
    app = APIFlask(
        __name__,
        title="Kith",
        version="1.0.0",
        docs_path="/docs",
        spec_path="/openapi.json",
    )
    app.description = (
        "Kith — a local, self-directed AI. Talks to Ollama, owns the persona "
        "and chat config, and streams reasoning + answer as NDJSON."
    )

    # Allow any local origin so the API is usable independently (curl, other
    # tools, a browser hitting it directly), not just via the frontend's proxy.
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    app.register_blueprint(api, url_prefix="/api")

    # The desktop app serves the UI from this process so the browser origin and
    # the API origin are the same one. Off unless KITH_UI_DIST is set, which is
    # how the Docker stack (nginx in front) keeps its current behaviour.
    served = spa.register(app)
    if served:
        # Hashes the bundle's inline theme script, so the pre-paint dark class keeps
        # working instead of being blocked into a white flash on every launch.
        csp.register(app, served)

    config_store.init(CONFIG_DB_PATH)
    migrations.init(AGENT_DB_PATH)
    embeddings.backfill_async(AGENT_DB_PATH)  # embed any memories that predate vectors
    runner.ensure_loop()  # keep the checker alive so reminders/schedules fire on time
    print(f"[kith] config db: {CONFIG_DB_PATH}")
    print(f"[kith] agent db:  {AGENT_DB_PATH}")
    print(f"[kith] embeddings: {embeddings.EMBED_MODEL}")
    print(f"[kith] web ui:   {served or '(not served — the vite dev server is in front)'}")
    if served:
        print(
            f"[kith] csp:      script hashes for {len(csp._inline_scripts(served / 'index.html'))} inline script(s)"
        )
    _log_configuration()
    _log_persona()
    return app


def _log_configuration() -> None:
    """Print every effective setting.

    Nearly every confusing failure in this project has been a configuration one — a
    host that does not resolve, a path that is not set, a search provider forced to
    something unexpected. Printing the resolved values means the log answers that
    before anyone starts reading code.
    """
    for key, value in settings.describe().items():
        if isinstance(value, dict):
            print(f"[kith] {key}:")
            for inner, detail in value.items():
                print(f"[kith]     {inner}: {detail}")
        else:
            print(f"[kith] {key}: {value}")


def _log_persona() -> None:
    """Print which persona fragments are in play, for transparency at startup."""
    if settings.SYSTEM_PROMPT_OVERRIDE:
        print("[kith] persona: using KITH_SYSTEM env override")
        return
    fragments = fragment_paths()
    names = ", ".join(path.name for path in fragments) or "(none)"
    print(f"[kith] persona: {len(fragments)} fragment(s) merged — {names}")
