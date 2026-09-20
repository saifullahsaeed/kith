"""Two Kiths on one machine, and the CLI holding the wrong key to the one that answered.

`~/.kith/api.token` belongs to a packaged install; `server/data/api.token` belongs to a
checkout. Both live on any machine that has run both, and `settings.DATA_DIR` picks between
them by asking what *this* binary is — frozen or not — which says nothing about the server on
the port. The installed `kith` is frozen, the server being developed against is a checkout, so
the CLI read the home file, got a 401 on every authenticated call, and reported it as "the
server rejected the API token" — a message that sounds like the server is wrong when the only
thing wrong is which of two files was opened.

The fix is not a better guess. It is that a 401 is the server telling you the file was wrong,
so the client tries the next one instead of stopping. These tests hold that: the order stays a
guess, the fallthrough is what makes the guess harmless, and an explicitly named token is
exempt because naming one is an instruction rather than a guess.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from kith.cli import client as cli_client
from kith.cli.errors import Failure


def _server(accepts: str, calls: list[str], data_dir: str | None = None):
    """A stand-in for urlopen that 401s everything but one token.

    `/api/health` answers without one, as the real server does, and carries `dataDir` only when
    the test says it should — a server too old to send it is the case that must still 401
    cleanly rather than crash in the lookup.
    """

    def urlopen(request, timeout=None):
        held = request.headers.get("X-kith-token", "")
        if request.full_url.endswith("/api/health"):
            calls.append("health")
            body = {"ok": True, "ollamaReachable": False}
            if data_dir is not None:
                body["dataDir"] = data_dir
            return io.BytesIO(json.dumps(body).encode())
        calls.append(held)
        if held != accepts:
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error": "bad token"}')
            )
        return io.BytesIO(b'{"ok": true}')

    return urlopen


@pytest.fixture
def two_installs(tmp_path, monkeypatch):
    """A frozen CLI's home token first, a checkout's token second — the real order."""
    home = tmp_path / ".kith"
    checkout = tmp_path / "server" / "data"
    for directory, value in ((home, "packaged-token"), (checkout, "checkout-token")):
        directory.mkdir(parents=True)
        (directory / "api.token").write_text(f"{value}\n")
    monkeypatch.delenv("KITH_TOKEN", raising=False)
    monkeypatch.setattr(
        cli_client, "token_candidates", lambda: [home / "api.token", checkout / "api.token"]
    )
    return home, checkout


def test_every_readable_token_is_collected_not_just_the_first(two_installs):
    assert [source.rsplit("/", 3)[-1] for _value, source in cli_client.token_options()] == [
        "api.token",
        "api.token",
    ]
    assert [value for value, _source in cli_client.token_options()] == [
        "packaged-token",
        "checkout-token",
    ]


def test_a_401_moves_on_to_the_next_token(two_installs, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", _server("checkout-token", calls))

    client = cli_client.Client(url="http://127.0.0.1:8611")
    assert client.get("/config") == {"ok": True}

    assert calls == ["packaged-token", "checkout-token"]
    # `status` reports the source, so it must be the one that worked, not the one first tried.
    assert client.token_source.endswith("server/data/api.token")


def test_the_working_token_is_kept_for_later_calls(two_installs, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", _server("checkout-token", calls))

    client = cli_client.Client(url="http://127.0.0.1:8611")
    client.get("/config")
    client.get("/projects")

    assert calls == ["packaged-token", "checkout-token", "checkout-token"]


def test_when_no_token_works_the_401_is_still_reported(two_installs, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", _server("nobody-has-this", calls))

    client = cli_client.Client(url="http://127.0.0.1:8611")
    with pytest.raises(Failure) as rejected:
        client.get("/config")

    assert "rejected the API token" in str(rejected.value)
    # The last of those is the health probe: every file refused, so the server was asked. It
    # answered without a `dataDir` here — an older build — and the 401 stands.
    assert calls == ["packaged-token", "checkout-token", "health"]


def test_an_explicitly_named_token_is_never_swapped_out(two_installs, monkeypatch):
    """`$KITH_TOKEN` and `Client(token=...)` are instructions. Falling through to a file would
    hide the typo the person needs to see, and could send a token to a server they did not mean."""
    calls: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", _server("checkout-token", calls))

    client = cli_client.Client(url="http://127.0.0.1:8611", token="typed-by-hand")
    with pytest.raises(Failure):
        client.get("/config")
    assert calls == ["typed-by-hand"]

    monkeypatch.setenv("KITH_TOKEN", "from-the-environment")
    calls.clear()
    from_env = cli_client.Client(url="http://127.0.0.1:8611")
    with pytest.raises(Failure):
        from_env.get("/config")
    assert calls == ["from-the-environment"]


def test_the_same_token_in_both_places_is_only_tried_once(tmp_path, monkeypatch):
    home = tmp_path / ".kith"
    checkout = tmp_path / "server" / "data"
    for directory in (home, checkout):
        directory.mkdir(parents=True)
        (directory / "api.token").write_text("one-server-two-paths\n")
    monkeypatch.delenv("KITH_TOKEN", raising=False)
    monkeypatch.setattr(
        cli_client, "token_candidates", lambda: [home / "api.token", checkout / "api.token"]
    )

    calls: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", _server("nobody-has-this", calls))
    with pytest.raises(Failure):
        cli_client.Client(url="http://127.0.0.1:8611").get("/config")
    assert calls == ["one-server-two-paths", "health"]


def test_a_frozen_cli_is_told_where_a_checkouts_token_is(tmp_path, monkeypatch):
    """The real failure on this machine, and the one guessing cannot fix.

    A frozen `kith` resolves `settings.DATA_DIR` to `~/.kith` — the same directory as the
    fallback — so its candidate list is one file long and the checkout's `server/data` is not
    in it under any name. There is nothing to fall through *to*. The server's own `dataDir`
    is the only thing that can name it.
    """
    home = tmp_path / ".kith"
    checkout = tmp_path / "elsewhere" / "server" / "data"
    home.mkdir(parents=True)
    checkout.mkdir(parents=True)
    (home / "api.token").write_text("packaged-token\n")
    (checkout / "api.token").write_text("checkout-token\n")
    monkeypatch.delenv("KITH_TOKEN", raising=False)
    monkeypatch.setattr(cli_client, "token_candidates", lambda: [home / "api.token"])

    calls: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", _server("checkout-token", calls, str(checkout)))

    client = cli_client.Client(url="http://127.0.0.1:8611")
    assert client.get("/config") == {"ok": True}
    assert calls == ["packaged-token", "health", "checkout-token"]
    assert client.token_source == str(checkout / "api.token")


def test_the_server_is_asked_once_however_many_calls_fail(tmp_path, monkeypatch):
    """A lookup that repeated would double every request against a server whose token this
    client will never hold — a dead conversation costing two round trips a call."""
    home = tmp_path / ".kith"
    home.mkdir(parents=True)
    (home / "api.token").write_text("packaged-token\n")
    monkeypatch.delenv("KITH_TOKEN", raising=False)
    monkeypatch.setattr(cli_client, "token_candidates", lambda: [home / "api.token"])

    calls: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", _server("nobody-has-this", calls, str(tmp_path)))

    client = cli_client.Client(url="http://127.0.0.1:8611")
    for _ in range(3):
        with pytest.raises(Failure):
            client.get("/config")

    assert calls.count("health") == 1
