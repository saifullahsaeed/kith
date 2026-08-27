"""The composition root: builds the Flask app and starts the work that outlives a request.

This is the one module allowed to reach into every layer, because wiring them together is the
whole job. It lives here rather than in ``kith/__init__.py`` so that importing *anything* from
``kith`` does not build the application — see that file's docstring.

See ``docs/startup.md`` for the reasoning behind the ordering, the CORS policy, and the
background-ownership rule.
"""

from __future__ import annotations

import os
from pathlib import Path

from apiflask import APIFlask
from flask_cors import CORS

from kith import settings
from kith.api import auth, csp, spa
from kith.api.routes import api
from kith.infra.db import config_store, migrations
from kith.services import embeddings, scheduler, tuning

# The function, not the module: `kith.services.connections` re-exports a ConnectionManager
# *instance* under the name `manager`, so importing from the package gets the object.
from kith.services.connections.manager import backfill_context_window_async
from kith.services.mcp.manager import connect_async as connect_mcp_async
from kith.services.persona import fragment_paths
from kith.settings import AGENT_DB_PATH, CONFIG_DB_PATH

__all__ = ["create_app"]


#: The Vite dev server, on both spellings of loopback. A page on the public internet cannot
#: forge its Origin, so listing these costs nothing.
_DEV_PORTS = (5173, 4173)
_ALLOWED_ORIGINS = [f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in _DEV_PORTS]


def _owns_background() -> bool:
    """Whether this process should run the long-lived background work.

    `flask run --reload` re-executes the module, so `create_app()` runs in two processes: the
    watcher that never serves a request, and the child that does. Nothing started below is
    idempotent across processes — two schedulers fire the same reminders twice, and two MCP
    connects spawn two sets of `npx` children with nothing to reap the old ones. So with the
    reloader on, only the process Werkzeug marks as serving owns any of it. Without it
    (packaged, Docker, plain `python app.py`, the test suite) there is one process, and it does.
    """
    if os.environ.get("KITH_RELOAD") != "1":
        return True
    return os.environ.get("WERKZEUG_RUN_MAIN") == "true"


def _start_background() -> None:
    """Start embeddings backfill, the context-window probe, MCP servers, and the scheduler.

    All off the request path deliberately: each is either a network call or a subprocess that
    downloads on first run, and none may stand between launching and answering.

    `KITH_NO_BACKGROUND=1` skips the scheduler only. It does *not* skip MCP — `agent_loop`
    reads `mcp_manager.snapshot()`, which stays empty until something connects, so skipping it
    silently handed the model no MCP tools at all for a whole dev session.
    """
    embeddings.backfill_async(AGENT_DB_PATH)
    backfill_context_window_async(CONFIG_DB_PATH)
    connect_mcp_async(CONFIG_DB_PATH)

    if os.environ.get("KITH_NO_BACKGROUND") == "1":
        print("[kith] background: scheduler off (KITH_NO_BACKGROUND); MCP still connecting")
        return

    # Imported here, not at module scope: the scheduler is told how to wake a conversation
    # rather than importing the way. This is the composition root, which may know about both
    # halves; the service may not.
    from kith.api.routes.chat import continue_conversation

    scheduler.start(continue_conversation)


def _init_storage() -> None:
    """Open both databases and bring settings up to date.

    Runs before anything can serve a request, so no reader ever sees a half-migrated state.
    """
    config_store.init(CONFIG_DB_PATH)
    migrations.init(AGENT_DB_PATH)
    # Settings live in a file now; the database has been released from that duty rather than
    # kept as a mirror. Carries an existing install across on the first start after upgrading,
    # and does nothing on every start after that.
    tuning._migrate_from_database()
    tuning.ensure_exists()


def _recover_interrupted_questions() -> None:
    """Restore questions the last process died holding.

    A parked turn is a daemon thread waiting on an in-memory event, so a restart took both and
    wrote nothing down. Done before serving, so the card is back by the time a window
    reconnects. See `questions.recover_interrupted`.
    """
    from kith.services import questions

    recovered = questions.recover_interrupted()
    if recovered:
        print(f"[kith] recovered {recovered} unanswered question(s) from an interrupted turn")


def create_app() -> APIFlask:
    app = _build_app()

    # NOT "*". A wildcard here means every website you visit while Kith is running can read
    # his files, config and memory, and can POST as you. Being bound to loopback is no
    # protection: the browser is on loopback too. Nothing legitimate needed it — the desktop
    # app is same-origin and sends no preflight, and curl sends no Origin at all.
    CORS(app, resources={r"/api/*": {"origins": _ALLOWED_ORIGINS}})

    app.register_blueprint(api, url_prefix="/api")

    # Every API call needs the shared secret from here on. CORS stops a page reading the
    # reply, not sending the request, and every process running as you is on loopback too.
    auth.register(app, settings.DATA_DIR)

    # The desktop app serves the UI from this process, making the browser and API origins the
    # same. Off unless KITH_UI_DIST is set, which is how the Docker stack (nginx in front)
    # keeps its current behaviour.
    served = spa.register(app)
    if served:
        # Hash the bundle's inline scripts so they still run under the policy. The pre-paint
        # theme script blocked means a white flash on every launch; the token script blocked
        # means the page loads with no token and 401s every call.
        csp.register(app, served, extra_inline=[spa.token_script()])

    _init_storage()

    if _owns_background():
        _recover_interrupted_questions()
        _start_background()

    _log_startup(served)
    return app


def _build_app() -> APIFlask:
    app = APIFlask(
        # "kith", not `__name__`. Flask resolves `root_path` — where it looks for static files
        # and templates — from the import name. The two agree today, but naming it explicitly
        # keeps that true if this file ever moves into a subpackage.
        "kith",
        title="Kith",
        version="1.0.0",
        docs_path="/docs",
        spec_path="/openapi.json",
    )
    app.description = (
        "Kith — a local AI you work with. Talks to Ollama, owns the persona "
        "and chat config, and streams reasoning + answer as NDJSON."
    )
    return app


def _log_startup(served: Path | None) -> None:
    """Print the resolved configuration.

    Nearly every confusing failure in this project has been a configuration one — a host that
    does not resolve, a path that is not set, a search provider forced to something
    unexpected. Printing resolved values means the log answers that before anyone reads code.
    """
    print(f"[kith] config db: {CONFIG_DB_PATH}")
    print(f"[kith] agent db:  {AGENT_DB_PATH}")
    print(f"[kith] embeddings: {tuning.value('embed_model')}")
    print(f"[kith] web ui:   {served or '(not served — the vite dev server is in front)'}")
    if served:
        count = len(csp._inline_scripts(served / "index.html"))
        print(f"[kith] csp:      script hashes for {count} inline script(s)")
    _log_configuration()
    _log_persona()


def _log_configuration() -> None:
    # Joined here rather than in `settings.describe()`: both halves belong in one entry, but
    # `settings` is the bottom of the tree and must not reach up to `tuning`.
    for key, value in {**settings.describe(), "tunables": tuning.describe()}.items():
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
