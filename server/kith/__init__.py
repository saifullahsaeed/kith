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
from kith.api import auth, csp, spa
from kith.api.routes import api
from kith.autonomy import runner
from kith.config import AGENT_DB_PATH, CONFIG_DB_PATH
from kith.infra.db import config_store, migrations
from kith.services import embeddings, tuning

# The functions, not the modules. `kith.services.connections` re-exports a ConnectionManager
# *instance* under the name `manager`, so `from kith.services.connections import manager`
# gets the object and the call fails with AttributeError at startup — which it did.
from kith.services.connections.manager import backfill_context_window_async
from kith.services.mcp.manager import connect_async as connect_mcp_async
from kith.services.persona import fragment_paths

__all__ = ["create_app"]


#: The dev server, on both spellings of loopback. A page on the public internet
#: cannot forge its Origin, so listing these costs nothing.
_DEV_PORTS = (5173, 4173)
_ALLOWED_ORIGINS = [f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in _DEV_PORTS]


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

    # NOT "*", which is what this was. A wildcard here means every website you
    # visit while Kith is running can read his files, his config and his memory,
    # and can POST as you — a page at evil.example was answered with
    # `Access-Control-Allow-Origin: https://evil.example` and got the lot. Being
    # bound to loopback is no protection: the browser is on loopback too.
    #
    # Nothing legitimate needed the wildcard. The desktop app serves the interface
    # from this same process, so it is same-origin and sends no CORS preflight at
    # all; the only real cross-origin caller is the Vite dev server. curl and other
    # tools send no Origin header and are unaffected by any of this.
    CORS(app, resources={r"/api/*": {"origins": _ALLOWED_ORIGINS}})

    app.register_blueprint(api, url_prefix="/api")

    # Every API call needs the shared secret from here on. See kith.api.auth for why CORS
    # and a loopback bind were not enough: CORS stops a page reading the reply, not sending
    # the request, and every process running as you is on loopback too.
    auth.register(app, settings.DATA_DIR)

    # The desktop app serves the UI from this process so the browser origin and
    # the API origin are the same one. Off unless KITH_UI_DIST is set, which is
    # how the Docker stack (nginx in front) keeps its current behaviour.
    served = spa.register(app)
    if served:
        # Hashes the bundle's inline theme script, so the pre-paint dark class keeps
        # working instead of being blocked into a white flash on every launch.
        # The token script goes in the page on the way out, so its hash has to be in the
        # policy too — an inline script the CSP has not hashed is silently not run, and the
        # page then loads with no token and 401s every call.
        csp.register(app, served, extra_inline=[spa.token_script()])

    config_store.init(CONFIG_DB_PATH)
    migrations.init(AGENT_DB_PATH)
    embeddings.backfill_async(AGENT_DB_PATH)  # embed any memories that predate vectors
    # Learn the model's context window if it was chosen before that was recorded. Off the
    # request path for the same reason as the line above: it is a network call, and it must
    # not stand between launching and answering.
    backfill_context_window_async(CONFIG_DB_PATH)
    # Bring up any MCP servers that are switched on. In a thread for the same reason as the
    # two lines above: a server installed by npx or uvx downloads on first run, and that must
    # not be what stands between launching and answering.
    connect_mcp_async(CONFIG_DB_PATH)
    runner.ensure_loop()  # keep the checker alive so reminders/schedules fire on time
    print(f"[kith] config db: {CONFIG_DB_PATH}")
    print(f"[kith] agent db:  {AGENT_DB_PATH}")
    print(f"[kith] embeddings: {tuning.value('embed_model')}")
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
