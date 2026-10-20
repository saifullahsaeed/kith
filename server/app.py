"""Entry point: ``python app.py`` (or ``flask --app app run``). Serves Kith on 127.0.0.1:8611.

Two servers, chosen by environment rather than preference: waitress by default, and Werkzeug
when ``KITH_RELOAD=1`` — the auto-reloader is the one thing it has that waitress does not.

Environment:
    ``HOST``           bind address (default 127.0.0.1; set 0.0.0.0 in a container)
    ``PORT``           bind port (default 8611)
    ``KITH_THREADS``   waitress worker threads (default 16)
    ``KITH_RELOAD``    ``1`` to use Werkzeug with the auto-reloader instead

See ``docs/startup.md`` for why waitress, why sixteen threads, and why never ``debug=True``.
"""

from __future__ import annotations

import os

from kith import create_app

app = create_app()

#: Enough for every window's event stream, every parked turn, and ordinary requests alongside
#: them. Each long-running response holds its worker for its whole life, so this is a floor on
#: concurrency, not a performance dial.
THREADS = int(os.environ.get("KITH_THREADS", "16"))


def _serve_with_reloader(host: str, port: int) -> None:
    """Development only. `use_reloader`, never `debug=True` — debug mode also mounts the
    Werkzeug debugger, an interactive console on any traceback, and every process running as
    you can reach loopback.

    `reloader_type="stat"` rather than "auto": auto prefers watchdog, which watches whole
    directories including `server/data/`, so every database write would restart the server
    underneath itself. The stat reloader polls only the imported .py files.
    """
    app.run(host=host, port=port, threaded=True, use_reloader=True, reloader_type="stat")


def _serve_for_real(host: str, port: int) -> None:
    """Production, and the desktop bundle."""
    from waitress import serve

    serve(
        app,
        host=host,
        port=port,
        threads=THREADS,
        # Must outlast a deliberately idle event stream, which keeps alive every 15s.
        channel_timeout=300,
        # Nothing sits in front of this — it is loopback, spoken to by one machine's own
        # browser — so there is no proxy whose headers should be trusted.
        clear_untrusted_proxy_headers=True,
        ident="Kith",
    )


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8611"))

    if os.environ.get("KITH_RELOAD") == "1":
        _serve_with_reloader(host, port)
    else:
        _serve_for_real(host, port)
