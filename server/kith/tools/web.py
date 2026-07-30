"""Reading the open web."""

from __future__ import annotations

from pathlib import Path

from kith.infra import sandbox, websearch
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "web_search",
    "Search the web. Returns results with titles, URLs, and a substantial excerpt "
    "of each page — often enough to answer on its own, so read the excerpts before "
    "reaching for fetch_url. If it comes back with a 'note' instead of results, "
    "search itself is broken, not the topic: don't conclude the web is empty.",
    {"query": STR, "limit": INT},
    required=("query",),
)
def web_search(path: Path, args: dict):
    return websearch.search(args["query"], args.get("limit") or 5)


@tool(
    "fetch_url",
    "Fetch a page from the internet and return its text. Fast, but it doesn't run "
    "JavaScript — use browse_page for sites that render their content client-side.",
    {"url": {**STR, "description": "An http(s) URL."}},
    required=("url",),
)
def fetch_url(path: Path, args: dict):
    return sandbox.fetch_url(args["url"])


@tool(
    "browse_page",
    "Open a page in a real headless browser and return the text a human would see "
    "after it renders. Use this for JS-heavy sites and SPAs (Behance, Dribbble, "
    "Upwork, LinkedIn, most modern sites) where fetch_url comes back empty or "
    "skeletal. Slower and heavier than fetch_url, so reach for it when fetch_url "
    "isn't enough.",
    {"url": {**STR, "description": "An http(s) URL."}},
    required=("url",),
)
def browse_page(path: Path, args: dict):
    return sandbox.browse_page(args["url"])
