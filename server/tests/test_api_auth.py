"""Who may talk to the API.

Nobody needed a credential before this, and the two things that looked like protection each
stop something other than what matters:

* **CORS stops a reply being read, not a request being sent.** A page on any site you have
  open could fire ``POST /api/workspace/root`` at 127.0.0.1 and the browser would send it.
  The attacker learns nothing from the response and does not need to — the side effect has
  already happened.
* **Loopback is not a boundary on a desktop.** Every process running as you is on loopback,
  so any script or app could read every transcript and run shell commands through the tools.

Two separate properties are being tested here, because they defend against different things
and could each be broken without the other noticing. The token must be *secret* (that is
what stops another process) and it must be *a custom header* (that is what stops a web page,
since a cross-origin custom header forces a preflight that CORS then refuses).
"""

from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path

import pytest

from kith.api import auth


@contextlib.contextmanager
def kith_app(data_dir: Path, ui_dist: Path | None = None):
    """A real Flask app pointed at a temporary data directory, and put back afterwards.

    The put-back is the fiddly part and it is not optional. `kith.settings` reads the
    environment at import time and `kith.config` derives the database paths from it at *its*
    import time, so pointing this at a temp folder means reloading both — and leaving them
    reloaded leaks a temp path into every test that runs after, which is exactly what
    happened: test_paths started asserting against a pytest tmpdir. Restoring the
    environment is not enough on its own; the modules have to be reloaded again under it.
    """
    import importlib

    from kith import config as config_module
    from kith import settings as settings_module

    before = {key: os.environ.get(key) for key in ("KITH_DATA_DIR", "KITH_UI_DIST")}
    os.environ["KITH_DATA_DIR"] = str(data_dir)
    if ui_dist is not None:
        os.environ["KITH_UI_DIST"] = str(ui_dist)
    else:
        os.environ.pop("KITH_UI_DIST", None)
    auth._cached = None
    importlib.reload(settings_module)
    importlib.reload(config_module)
    try:
        from kith import create_app

        created = create_app()
        created.config.update(TESTING=True)
        yield created
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        auth._cached = None
        importlib.reload(settings_module)
        importlib.reload(config_module)


@pytest.fixture
def app(tmp_path):
    """A real app, with its own data directory so the suite never touches the real token."""
    with kith_app(tmp_path / "data") as created:
        yield created


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def good(app):
    from kith import settings

    return auth.token(settings.DATA_DIR)


class TestTheDoorIsShut:
    @pytest.mark.parametrize(
        "path", ["/api/autonomy", "/api/conversations", "/api/permissions", "/api/tuning"]
    )
    def test_no_token_is_refused(self, client, path):
        assert client.get(path).status_code == 401

    def test_the_right_token_gets_in(self, client, good, path="/api/autonomy"):
        assert client.get(path, headers={auth.HEADER: good}).status_code == 200

    def test_a_wrong_token_is_refused(self, client):
        assert client.get("/api/autonomy", headers={auth.HEADER: "not-it"}).status_code == 401

    def test_an_empty_token_is_refused(self, client):
        # An empty header must not compare equal to anything, and must not be read as
        # "no header, so skip the check".
        assert client.get("/api/autonomy", headers={auth.HEADER: ""}).status_code == 401

    def test_writes_are_refused_too(self, client):
        # The reads are the obvious case; the writes are the ones that cost something. This
        # exact route can move his whole workspace.
        response = client.post("/api/workspace/root", json={"path": "/tmp/somewhere"})
        assert response.status_code == 401

    def test_the_refusal_does_not_say_which_way_it_failed(self, client):
        without = client.get("/api/autonomy").get_json()
        wrong = client.get("/api/autonomy", headers={auth.HEADER: "x"}).get_json()
        # "Wrong token" versus "no token" is a hint, and nobody who needs the difference is
        # unable to read the file.
        assert without == wrong


class TestWhatStaysOpen:
    def test_health_answers_without_a_token(self, client):
        # The shell polls this before a window exists, and it returns nothing but liveness.
        assert client.get("/api/health").status_code == 200

    def test_the_autonomy_stream_is_open_to_our_own_page(self, client):
        # An EventSource cannot send headers. Putting the token in the query string would
        # print it into the request log on every reconnect — a worse leak than the one being
        # closed — so this is gated on being same-origin instead.
        assert (
            client.get("/api/autonomy/stream", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
        )

    def test_the_autonomy_stream_refuses_another_site(self, client):
        response = client.get("/api/autonomy/stream", headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403

    def test_a_cross_origin_header_also_refuses_the_stream(self, client):
        response = client.get("/api/autonomy/stream", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403

    def test_the_schema_stays_readable(self, client):
        # Serving the shape of an API someone cannot call is not a leak, and locking it turns
        # /docs into a login wall on a single-user machine.
        assert client.get("/openapi.json").status_code == 200

    def test_a_preflight_is_not_the_request(self, client):
        # The browser cannot attach the token to a preflight. Refusing it would break every
        # cross-origin call from the dev server before the real request was ever made.
        assert client.options("/api/autonomy").status_code < 400


class TestTheSecretItself:
    def test_the_file_is_readable_only_by_its_owner(self, app, good, tmp_path):
        path = tmp_path / "data" / auth.FILENAME
        mode = stat.S_IMODE(os.stat(path).st_mode)
        # 0600, and set at creation rather than after: a world-readable file that is chmod'ed
        # a moment later has already been readable, which is the whole thing this prevents.
        assert mode == 0o600, oct(mode)

    def test_it_is_long_enough_to_be_worth_having(self, good):
        assert len(good) >= 32

    def test_it_is_stable_across_reads(self, app, tmp_path):
        from kith import settings

        first = auth.token(settings.DATA_DIR)
        auth._cached = None  # force it back to disk
        assert auth.token(settings.DATA_DIR) == first

    def test_an_empty_file_is_replaced_rather_than_honoured(self, app, tmp_path, monkeypatch):
        path = tmp_path / "data" / auth.FILENAME
        path.write_text("   ")
        monkeypatch.setattr(auth, "_cached", None, raising=False)

        from kith import settings

        fresh = auth.token(settings.DATA_DIR)

        # An empty token would compare equal to an empty header and let everyone in.
        assert fresh.strip()
        assert len(fresh) >= 32


class TestTheServedPageGetsTheToken:
    def test_index_html_carries_it(self, tmp_path):
        """The page is the one caller that cannot read the file, so it is handed the value.

        There is nowhere else it could come from: an endpoint that gave it out would have to
        be unauthenticated, which is the same hole wearing a different hat.
        """
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><head><title>Kith</title></head><body></body></html>")

        with kith_app(tmp_path / "data", dist) as app:
            body = app.test_client().get("/").get_data(as_text=True)

            from kith import settings

            assert "window.__kithToken" in body
            assert auth.token(settings.DATA_DIR) in body
            # Before </head>, so it runs before the bundle that will need it.
            assert body.index("window.__kithToken") < body.index("</head>")

    def test_the_page_carrying_a_secret_is_never_cached(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><head></head><body></body></html>")

        with kith_app(tmp_path / "data", dist) as app:
            response = app.test_client().get("/")

        # A cached index carrying a stale token would 401 every request and read as the
        # server having died.
        assert "no-store" in response.headers.get("Cache-Control", "")


class TestTheInjectedScriptIsAllowedToRun:
    """The bug this class exists for shipped and was only caught by loading the page.

    The token went into index.html correctly — curl showed it there — and the page still
    401'd everything, because the Content-Security-Policy hashes inline scripts byte for
    byte and knew only about the bundle's own. The browser refused to run the new one and
    said nothing in the console. Everything looked right except the behaviour.
    """

    def test_the_policy_hashes_the_token_script(self, tmp_path):
        import base64
        import hashlib

        from kith.api import csp, spa

        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><head></head><body></body></html>")

        with kith_app(tmp_path / "data"):
            script = spa.token_script()
            policy = csp.policy_for(dist, extra_inline=[script])

        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in policy

    def test_the_served_page_and_the_policy_agree(self, tmp_path):
        """The end-to-end version: whatever is in the page must be hashed in the header.

        Byte for byte — the two used to be built from separate f-strings, and one character
        of difference is a page that loads and does nothing.
        """
        import base64
        import hashlib
        import re

        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><head></head><body></body></html>")

        with kith_app(tmp_path / "data", dist) as app:
            response = app.test_client().get("/")
        body = response.get_data(as_text=True)
        policy = response.headers["Content-Security-Policy"]

        inline = re.search(r"<script>(.*?)</script>", body, re.S)
        assert inline, "the page should carry an inline script"
        digest = base64.b64encode(hashlib.sha256(inline.group(1).encode()).digest()).decode()
        assert f"'sha256-{digest}'" in policy
