#!/usr/bin/env python3
"""Does this canvas actually work?

You cannot see the page you just wrote, and the three ways a canvas fails are all invisible from
the source: a JavaScript error before the first paint leaves a blank rectangle, an animation loop
that never starts leaves a still picture you believe is moving, and a page far taller than it
should be means it will arrive clipped.

So this loads the file the way the app loads it — same sandbox, same offline policy, no network —
and reports the three things you cannot know otherwise:

    errors   anything the page threw, first line only
    height   what the frame will size itself to, at a narrow and a wide column
    moving   whether two frames a second apart actually differ
    canvas   whether anything was ever drawn into a <canvas>, if there is one

Run it on the file *before* you put the fence in the reply:

    python3 check_canvas.py page.html

Exits non-zero if the page threw, so it can be chained with && while you iterate.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

#: The two column widths a canvas actually gets: their window with the side panel closed, and with
#: it open. Checking one is checking half.
WIDE = 820
NARROW = 560

#: The palette the app hands a canvas. Injected here so a check sees the colours the reply will
#: see: run bare, every `var(--kith-…)` resolves to nothing, every fill silently becomes black on
#: black, and a page that will look right in the app reports as a dark rectangle here. Dark
#: because that is the theme to check against — light is the same page with different values.
_TOKENS = """:root{
  --kith-bg:#1a1713; --kith-line:#25221e; --kith-text:#eae5dd; --kith-dim:#a19a92;
  --kith-accent:#f1b65a; --kith-accent-soft:#352918; --kith-second:#5dd786;
  --kith-second-soft:#1d2e1f; --kith-muted:#26221f;
}
html{background:var(--kith-bg)} body{margin:0;color:var(--kith-text)}"""


def _with_tokens(source: str) -> str:
    """The page with the palette in front of it, as the frame would serve it.

    Prepended as text rather than injected after load, because a page's own inline script runs
    while the document is parsing — before anything a test harness could add — and reads the
    tokens at that moment.
    """
    style = f"<style>{_TOKENS}</style>"
    lowered = source.lower()
    for marker in ("<head>", "<html>", "<!doctype html>"):
        at = lowered.find(marker)
        if at != -1:
            cut = at + len(marker)
            return source[:cut] + style + source[cut:]
    return style + source

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - the message is the whole point
    sys.exit("playwright is not installed here: pip install playwright && playwright install chromium")


def check(path: Path) -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": WIDE, "height": 600})
        page.on("pageerror", lambda e: errors.append(str(e).split("\n")[0]))
        page.on("console", lambda m: errors.append("console: " + m.text[:160]) if m.type == "error" else None)

        # Offline, because the canvas will be. A page that only works with the network is a page
        # that will be blank in the reply, and finding that out here costs nothing.
        page.context.route("**/*", lambda route: route.abort()
                           if route.request.url.startswith(("http://", "https://")) else route.continue_())
        staged = Path(tempfile.mkdtemp()) / path.name
        staged.write_text(_with_tokens(path.read_text(encoding="utf-8", errors="replace")))
        page.goto(staged.resolve().as_uri())
        page.wait_for_timeout(700)

        # Two columns, because the canvas is narrower when their side panel is open. A page that
        # only works at one of these widths is a page that is broken half the time, and the source
        # never says which half.
        wide = page.evaluate("Math.ceil(document.body.scrollHeight)")
        page.set_viewport_size({"width": NARROW, "height": 600})
        page.wait_for_timeout(250)
        narrow = page.evaluate("Math.ceil(document.body.scrollHeight)")
        page.set_viewport_size({"width": WIDE, "height": 600})
        page.wait_for_timeout(250)
        first = page.screenshot()
        page.wait_for_timeout(1000)
        second = page.screenshot()
        moving = first != second

        # A canvas element that was never painted is the other silent failure: the loop runs, the
        # element is there, and nothing was ever drawn into it.
        painted = page.evaluate(
            """() => {
                 const c = document.querySelector('canvas');
                 if (!c) return null;
                 const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                 let lit = 0;
                 for (let i = 3; i < d.length; i += 4000) if (d[i] > 8) lit++;
                 return lit;
               }"""
        )
        browser.close()

    print(f"errors   {errors[0] if errors else 'none'}")
    reflow = "   (reflows badly — check it at both widths)" if narrow > wide * 1.9 else ""
    long = "   (long — trim it, or they will scroll a conversation)" if wide > 900 else ""
    print(f"height   {wide}px at {WIDE}, {narrow}px at {NARROW}{reflow}{long}")
    print(f"moving   {'yes' if moving else 'no'}")
    if painted is not None:
        print(f"canvas   {'painted' if painted else 'NOTHING DRAWN'} ({painted} lit samples)")
    if len(errors) > 1:
        print(f"         (+{len(errors) - 1} more)")
    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: check_canvas.py <file.html>")
    target = Path(sys.argv[1])
    if not target.is_file():
        sys.exit(f"no such file: {target}")
    sys.exit(check(target))
