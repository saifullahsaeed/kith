"""Reaching something that is not on this machine.

Here rather than in `files.py` because a URL is not a path. It shares the workspace's output
clipping and its shell — `browse_page` shells out to a real browser — and nothing else.
"""

from __future__ import annotations

import html
import re
import shlex

from kith.infra import renderer

from .base import WorkspaceError, _clip
from .shell import _capture

#: How much of a page to download before converting it to prose. A bound on bandwidth and
#: parsing time, *not* on what reaches the model — the conversion throws away the
#: overwhelming majority, and the text it produces is clipped separately.
_MAX_FETCH_BYTES = 5_000_000


def fetch_url(url: str) -> str:
    """A page, as the prose a reader would see.

    The download is bounded and the conversion is bounded, and getting those the wrong way
    round made this useless on anything real. It used to go through ``run_command``, which
    clips output to 8,000 characters — so the *HTML* was cut at 8,000 bytes and only then
    turned into text. On a modern page that is the middle of ``<head>``.

    Measured before the fix: the Wikipedia article on prompt engineering, 470,144 characters
    of markup, came back as 114 characters — its title and half a stray ``<link>`` tag.
    Hacker News gave 805. Only example.com worked, because example.com is smaller than the
    limit. The `<head>` strip below cannot help either, since with the closing tag cut off
    the pattern never matches.

    Downloading the whole thing costs nothing that matters: curl's output is not context.
    What reaches him is the same 8,000 characters it always was — of prose now instead of
    markup.
    """
    target = url.strip()
    if not re.match(r"^https?://", target):
        raise WorkspaceError("url must start with http:// or https://")
    command = (
        f"curl -sL --max-time 25 --max-filesize {_MAX_FETCH_BYTES} "
        f"-A 'Mozilla/5.0 (Kith)' {shlex.quote(target)}"
    )
    code, markup = _capture(command, timeout=30)
    if code != 0 and not markup:
        raise WorkspaceError("fetch failed (is this machine online?)")
    return _html_to_text(markup)


def browse_page(url: str) -> str:
    """Render a page in a real browser and return its visible text.

    Uses the desktop app's Chromium when it is running — already installed, already
    updated with Electron. Without it there is no fallback any more: the container carried
    a Playwright install and this machine may not have one, so the honest answer is to say
    so and let him use fetch_url.
    """
    target = url.strip()
    if not re.match(r"^https?://", target):
        raise WorkspaceError("url must start with http:// or https://")
    rendered = renderer.render(target)
    if rendered is not None:
        return _clip(rendered)
    raise WorkspaceError(
        "No browser renderer available — the desktop app provides it, so this needs Kith "
        "running in the app rather than a bare server. Try fetch_url for a static page."
    )


def _html_to_text(markup: str) -> str:
    if "<" not in markup:
        return _clip(markup)
    text = re.sub(r"(?is)<(script|style|head|noscript|svg)[^>]*>.*?</\1>", " ", markup)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return _clip(text.strip())
