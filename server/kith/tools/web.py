"""Reading the open web."""

from __future__ import annotations

from pathlib import Path

from kith.config import default_config
from kith.infra import websearch
from kith.infra import workspace as sandbox
from kith.services import tuning
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "web_search",
    "Search the web. Returns results with titles, URLs, and a substantial excerpt "
    "of each page — often enough to answer on its own, so read the excerpts before "
    "reaching for browse_page. If it comes back with a 'note' instead of results, "
    "search itself is broken, not the topic: don't conclude the web is empty.",
    {"query": STR, "limit": INT},
    required=("query",),
)
def web_search(path: Path, args: dict):
    # The adapter resolves what the search needs, which is what an adapter is for: `websearch`
    # talks to providers and should not be reading the settings service to find out which
    # engine to name.
    return websearch.search(
        args["query"],
        default_config(),
        args.get("limit") or 5,
        engine=str(tuning.value("search_engine")),
        carrier=str(tuning.value("search_model")),
    )


#: Below this many characters of prose, a plain download has not really returned a page —
#: it has returned the shell of one whose content arrives by JavaScript, which is the case a
#: real browser exists for. A threshold, not a law: a genuinely short page (a 404, a
#: one-paragraph note) will be rendered a second time for nothing, and that costs a few
#: seconds rather than a wrong answer. The other way round is the expensive mistake — a
#: skeleton reported as the page, which is how "the site says nothing about pricing" gets
#: said about a site whose pricing is right there.
_TOO_LITTLE_TO_BE_A_PAGE = 600


@tool(
    "browse_page",
    "Read a page from the internet as text. It downloads the page and, if that comes back "
    "skeletal because the content arrives by JavaScript, renders it in a real browser instead — "
    "so SPAs work without you knowing in advance which kind of site it is. The result says "
    "which way it read the page.",
    {
        "url": {**STR, "description": "An http(s) URL."},
        "render": {
            "type": "boolean",
            "description": "Force the browser (true) or forbid it (false). Default: only when "
            "the download comes back with nothing to read.",
        },
    },
    required=("url",),
)
def browse_page(path: Path, args: dict):
    """One tool for reading a page, because the choice it replaced could not be made in advance.

    There were two — `fetch_url` and `browse_page` — and picking between them meant knowing
    whether a site rendered on the server or in the client *before* looking at it. That is not
    something you can know, so the pattern in practice was a cheap fetch, a skeletal result,
    and a second round to fetch it again properly. Sometimes it was worse than that: the
    skeleton read as an answer, and a page whose content never downloaded was reported as a
    page with nothing on it.

    The escalation is what a person does anyway, so it belongs in the tool. `render` is kept
    for the two cases where the model does know better than the threshold: a site it has
    already learned needs the browser, and one where the plain text is enough and a render
    would only cost seconds.
    """
    url = args["url"]
    render = args.get("render")
    if render is True:
        return {"how": "rendered in a browser", "text": sandbox.browse_page(url)}

    text = sandbox.fetch_url(url)
    if render is False or len(text.strip()) >= _TOO_LITTLE_TO_BE_A_PAGE:
        return {"how": "downloaded", "text": text}

    try:
        rendered = sandbox.browse_page(url)
    except Exception as exc:
        # The download is still the best answer available, and saying why the browser was
        # tried is what stops a thin page being read as the whole story.
        return {
            "how": "downloaded",
            "text": text,
            "note": f"Too little text to be the whole page, and rendering it failed: {exc}",
        }
    if len(rendered.strip()) <= len(text.strip()):
        return {"how": "downloaded", "text": text}
    return {
        "how": "rendered in a browser",
        "text": rendered,
        "note": "The plain download came back skeletal, so this is the rendered page.",
    }
