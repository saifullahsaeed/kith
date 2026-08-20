"""Every request Kith sends says which app sent it.

Not vanity. `HTTP-Referer` is the primary identifier OpenRouter groups an app's usage under,
and both places that build a chat request sent `http://localhost` — a string shared with every
program ever run on a laptop. Kith's own traffic was therefore attributed to nothing, and the
failure is invisible from here: attribution that lands in the wrong bucket looks exactly like
attribution that worked.

The other half is the gate. `X-OpenRouter-Title` and `X-OpenRouter-Categories` are OpenRouter's
vocabulary, and the categories are a closed set of slugs where an unrecognised one is dropped
silently — so a typo, or a slug invented from memory, is a header that goes out, is thrown away,
and reports nothing.
"""

from __future__ import annotations

from pathlib import Path

import requests

from kith.domain import connection
from kith.domain.chat import Config
from kith.infra import websearch
from kith.llm import openai_compat

OPENROUTER = "https://openrouter.ai/api/v1"

#: OpenRouter's marketplace slugs, all fifteen, as their attribution documentation lists them.
#: Written out rather than derived, because the point is to catch a slug that this app made up.
DOCUMENTED = {
    "cli-agent",
    "ide-extension",
    "cloud-agent",
    "programming-app",
    "native-app-builder",
    "creative-writing",
    "video-gen",
    "image-gen",
    "audio-gen",
    "writing-assistant",
    "general-chat",
    "personal-agent",
    "legal",
    "roleplay",
    "game",
}


def a_config(base_url: str) -> Config:
    return Config(
        model="openai/gpt-5.6-luna",
        num_ctx=8192,
        num_predict=512,
        system="You are Kith.",
        think=False,
        base_url=base_url,
        api_key="test-key",
    )


def test_it_is_a_real_url_and_not_localhost():
    headers = connection.attribution(OPENROUTER)
    assert headers["HTTP-Referer"] == connection.APP_URL
    assert headers["HTTP-Referer"].startswith("https://")
    assert "localhost" not in headers["HTTP-Referer"]


def test_it_says_its_name_under_both_the_old_and_the_new_key():
    # `X-Title` is the older name and still honoured; the same value under both cannot mean two
    # different things, which is the whole reason it is safe to keep sending it.
    headers = connection.attribution(OPENROUTER)
    assert headers["X-Title"] == connection.APP_NAME == "Kith"
    assert headers["X-OpenRouter-Title"] == connection.APP_NAME


def test_the_categories_are_slugs_openrouter_actually_has():
    sent = connection.attribution(OPENROUTER)["X-OpenRouter-Categories"].split(",")
    assert sent, "an app with no category belongs to nothing"
    assert set(sent) <= DOCUMENTED, f"invented slugs: {sorted(set(sent) - DOCUMENTED)}"


def test_no_more_than_two_categories_go_out(monkeypatch):
    # Their per-request ceiling. Asserted against a longer list than the app carries, so this
    # keeps holding the day someone adds a third.
    monkeypatch.setattr(connection, "APP_CATEGORIES", ("personal-agent", "programming-app", "general-chat"))
    sent = connection.attribution(OPENROUTER)["X-OpenRouter-Categories"].split(",")
    assert len(sent) == connection.MAX_CATEGORIES == 2


def test_openrouter_only_headers_go_only_to_openrouter():
    # The same rule as the `web` plugin and the `provider`/`usage` extensions: their vocabulary
    # is theirs. Everyone still gets the referer and the name.
    for elsewhere in ("https://api.openai.com/v1", "https://integrate.api.nvidia.com/v1", ""):
        headers = connection.attribution(elsewhere)
        assert headers["HTTP-Referer"] == connection.APP_URL
        assert headers["X-Title"] == connection.APP_NAME
        assert "X-OpenRouter-Title" not in headers
        assert "X-OpenRouter-Categories" not in headers


def test_the_chat_stream_sends_it(monkeypatch):
    seen: dict[str, str] = {}

    def refuse(url, **kwargs):
        seen.update(kwargs["headers"])
        # Far enough: the headers are built before the request goes out, and a transport error
        # is a documented event rather than an exception, so the generator ends cleanly.
        raise requests.exceptions.RequestException("not going anywhere")

    monkeypatch.setattr(openai_compat.requests, "post", refuse)
    events = list(openai_compat.stream_once([{"role": "user", "content": "hi"}], a_config(OPENROUTER)))
    assert [event["type"] for event in events] == ["error"]
    assert seen["HTTP-Referer"] == connection.APP_URL
    assert seen["X-OpenRouter-Title"] == connection.APP_NAME
    assert "X-OpenRouter-Categories" in seen


def test_the_search_sends_it(monkeypatch):
    # `infra.websearch` builds its own chat payload, which is why this is two tests and not one.
    seen: dict[str, str] = {}

    def capture(url, **kwargs):
        seen.update(kwargs["headers"])
        return requests.Response()

    monkeypatch.setattr(websearch.requests, "post", capture)
    websearch._post(a_config(OPENROUTER), {"model": "openai/gpt-5.6-luna", "messages": []})
    assert seen["HTTP-Referer"] == connection.APP_URL
    assert seen["X-OpenRouter-Title"] == connection.APP_NAME


def test_nobody_grew_a_second_copy():
    """One module decides what Kith calls itself.

    Two did, they drifted to being identically wrong, and nothing owned the question — the same
    story `is_openrouter` is in `domain/connection.py` for. This is the guard that keeps the
    third copy from being written.
    """
    root = Path(connection.__file__).resolve().parent.parent
    writers = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "HTTP-Referer" in path.read_text(encoding="utf-8")
    )
    assert writers == ["domain/connection.py"]
