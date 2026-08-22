"""Entry point: `python app.py` (or `flask --app app run`).

Runs the Kith server on 127.0.0.1:8611 by default.

**Two servers, and which one you get is not a preference.** Werkzeug's own documentation says not
to use its development server to serve an application, and this application is not a script someone
runs in a terminal — it is the backend of a desktop app that people leave open all day. So the
default is waitress, and Werkzeug is what `KITH_RELOAD=1` gets you, because the auto-reloader is
the one thing it has that waitress does not.

waitress rather than gunicorn or uvicorn: it is pure Python with no C extensions, which is what
lets PyInstaller freeze it into the desktop bundle without a compiler on the machine doing the
build. It is also the one that works unchanged on Windows.

Threads matter here for a specific reason. Every long-running response holds its worker for as long
as it lasts, and this app has two kinds: the event stream, which is open for the life of a window,
and a turn's `POST /api/chat`, which is open for the life of a turn — and a turn parked on an `ask`
or a permission can sit there for its full fifteen-minute deadline. Sixteen is generous for a
single-user machine and cheap; the failure it prevents is the whole app appearing to hang because
every thread is holding a stream.
"""

from __future__ import annotations

import os

from kith import create_app

app = create_app()

#: Enough for every window's stream, every parked turn, and ordinary requests alongside them.
THREADS = int(os.environ.get("KITH_THREADS", "16"))

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")  # set HOST=0.0.0.0 in a container
    port = int(os.environ.get("PORT", "8611"))
    reload = os.environ.get("KITH_RELOAD") == "1"

    if reload:
        # Development only, for the reloader.
        #
        # `use_reloader` alone, never `debug=True`. Debug mode also mounts the Werkzeug
        # debugger, which is an interactive Python console on any traceback — a remote shell
        # for anything that can reach the port, and every process running as you can reach
        # loopback. Reloading is the part that was wanted; the console is not.
        #
        # `reloader_type="stat"` rather than the default "auto". Auto uses watchdog when it is
        # installed, and watchdog watches whole *directories* — including `server/`, which holds
        # `data/` with the config and agent databases in it. He writes to those constantly, so
        # every tool call would restart the server underneath itself. The stat reloader polls
        # only the .py files actually imported, which is exactly the set worth watching.
        app.run(
            host=host,
            port=port,
            threaded=True,
            use_reloader=True,
            reloader_type="stat",
        )
    else:
        from waitress import serve

        # `channel_timeout` has to outlast a stream that is deliberately idle. The event stream
        # sends a keep-alive comment every fifteen seconds, so anything above that is safe; this is
        # well above it, because the cost of being wrong is a window that silently stops hearing.
        serve(
            app,
            host=host,
            port=port,
            threads=THREADS,
            channel_timeout=300,
            # Nothing sits in front of this — it is loopback, spoken to by one machine's own
            # browser — so there is no proxy whose headers should be trusted.
            clear_untrusted_proxy_headers=True,
            ident="Kith",
        )
