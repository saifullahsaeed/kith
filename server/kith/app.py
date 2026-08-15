"""Building the Flask app, and starting the work that outlives a request.

This is the composition root: the one place allowed to reach into every layer, because
wiring them together is the whole job. It lived in ``kith/__init__.py`` until it was
measured, and the reason it moved is in that file's docstring — a package root that builds
the application makes *every* import of *anything* build the application.
"""

from __future__ import annotations

import os

from apiflask import APIFlask
from flask_cors import CORS

from kith import settings
from kith.api import auth, csp, spa
from kith.api.routes import api
from kith.infra.db import config_store, migrations
from kith.services import embeddings, scheduler, tuning

# The functions, not the modules. `kith.services.connections` re-exports a ConnectionManager
# *instance* under the name `manager`, so `from kith.services.connections import manager`
# gets the object and the call fails with AttributeError at startup — which it did.
from kith.services.connections.manager import backfill_context_window_async
from kith.services.mcp.manager import connect_async as connect_mcp_async
from kith.services.persona import fragment_paths
from kith.settings import AGENT_DB_PATH, CONFIG_DB_PATH

__all__ = ["create_app"]


#: The dev server, on both spellings of loopback. A page on the public internet
#: cannot forge its Origin, so listing these costs nothing.
_DEV_PORTS = (5173, 4173)
_ALLOWED_ORIGINS = [f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in _DEV_PORTS]


def _owns_background() -> bool:
    """Whether this process is the one that should run the long-lived background work.

    Building the app and owning background work were the same statement until a reloader
    existed, and then they stopped being: `flask run --reload` re-executes the module, so
    `app = create_app()` happens in *two* processes — the watcher that never serves a request,
    and the child that does. Nothing here is idempotent across processes. `runner.ensure_loop`
    guards on `self._thread`, which is per-process, so two processes means two schedulers
    firing the same reminders and taking the same steps twice. `connect_async` spawns MCP
    servers under `npx`, so it means two sets of those, and there is no `atexit` anywhere to
    reap the ones the previous child left behind.

    So it is decided here rather than left to luck: with the reloader on, only the child that
    Werkzeug marks as the serving process owns any of it. Without the reloader — packaged,
    Docker, a plain `python app.py`, the test suite — there is only one process, and it does.
    """
    if os.environ.get("KITH_RELOAD") != "1":
        return True
    return os.environ.get("WERKZEUG_RUN_MAIN") == "true"


def _start_background() -> None:
    """The work that outlives a request: embeddings, the context-window probe, MCP servers,
    and the scheduler.

    All off the request path deliberately — each one is either a network call or a subprocess
    that downloads on first run, and none of them may stand between launching and answering.

    `KITH_NO_BACKGROUND` skips the scheduler only — the one thing a reloader restart can do
    real damage with, since it restarts mid-step and can re-fire a reminder that already fired.

    It deliberately does *not* skip MCP any more. It did at first, on the grounds that a reload
    drops MCP child processes without reaping them, so each save leaks an `npx`. True, but the
    wrong trade by a wide margin: `agent_loop` reads `mcp_manager.snapshot()`, which is empty
    until something has connected, so skipping this silently handed the model **no MCP tools at
    all** for a whole dev session. A leaked process is untidy; a model quietly missing half its
    tools is a different program.
    """
    embeddings.backfill_async(AGENT_DB_PATH)  # embed any memories that predate vectors
    # Learn the model's context window if it was chosen before that was recorded.
    backfill_context_window_async(CONFIG_DB_PATH)
    # Bring up any MCP servers that are switched on, in a thread: one installed by npx or uvx
    # downloads on first run, and that must not be what stands between launching and answering.
    connect_mcp_async(CONFIG_DB_PATH)
    if os.environ.get("KITH_NO_BACKGROUND") == "1":
        print("[kith] background: scheduler off (KITH_NO_BACKGROUND); MCP still connecting")
        return
    # The one thing that still runs unasked: checking whether a reminder or a schedule has
    # come due. Started here rather than at import, so a test suite never inherits it.
    # The scheduler is told how to wake a conversation rather than importing the way. This is
    # the composition root, which is allowed to know about both halves; the service is not.
    from kith.api.routes.chat import continue_conversation

    scheduler.start(continue_conversation)


def create_app() -> APIFlask:
    app = APIFlask(
        # "kith", not `__name__`. Flask resolves `root_path` — where it looks for static files
        # and templates — from the import name, and the two happen to agree here: the package
        # `kith` and the module `kith.app` both sit in the same directory. Named explicitly
        # anyway, so that stays true if this file is ever moved into a subpackage, where
        # `__name__` would silently start pointing one level down.
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
    # Settings live in a file now, and the database has been released from that duty rather
    # than kept as a mirror. This carries an existing installation across on the first start
    # after upgrading and does nothing on every start after that. Before anything can serve a
    # request, so no reader ever sees the half-moved state.
    from kith.services import tuning as _tuning

    _tuning._migrate_from_database()
    _tuning.ensure_exists()
    # Questions the last process died holding. A parked turn is a daemon thread waiting on an
    # in-memory event, so a restart took both and wrote nothing down — see
    # `questions.recover_interrupted` for the measurements. Done here, before anything can
    # serve a request, so the card is already back by the time the window reconnects.
    if _owns_background():
        from kith.services import questions

        recovered = questions.recover_interrupted()
        if recovered:
            print(f"[kith] recovered {recovered} unanswered question(s) from an interrupted turn")
        _start_background()
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
    # Joined here rather than inside `settings.describe()`. Both halves belong in one entry,
    # but `settings` is the bottom of the tree and reaching up to `tuning` for the second half
    # is what forced that import to be written inside the function to hide it from Python.
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
