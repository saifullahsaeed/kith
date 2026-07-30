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
    app.run(host=host, port=port, threaded=True)
