"""Entry point: `python app.py` (or `flask --app app run`).

Runs the standalone Kith server on 127.0.0.1:8611 by default. Threaded so
streaming one chat doesn't block the health check or a second request.
"""

from __future__ import annotations

import os

from kith import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")  # set HOST=0.0.0.0 in a container
    port = int(os.environ.get("PORT", "8611"))
    reload = os.environ.get("KITH_RELOAD") == "1"

    if reload:
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
        app.run(host=host, port=port, threaded=True)
