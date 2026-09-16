"""Every route the server forwards to the desktop shell is a route the shell actually serves.

The two halves of the native bridge are written in different languages, in different
directories, and nothing connects them but a string. `infra/renderer.py` posts to `/notify`;
`render-service.ts` keeps a list of routes it will answer and 404s everything else. A typo, or
a route added on one side only, produces a capability that exists in the API, returns cleanly,
and silently does nothing — `_ask` treats a 404 as "no shell" and every caller here is written
to degrade quietly when there is no shell.

That is the worst shape a failure can have: the button works, the request succeeds, nothing
happens, and the fallback path that was meant for "running as a bare server" swallows it.

Adding the CLI installer is what made this worth writing. It added two routes across the seam
in one change, and the only thing that would have caught a mismatch was launching the packaged
app and clicking the button.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
FORWARDER = ROOT / "server" / "kith" / "infra" / "renderer.py"
SERVICE = ROOT / "desktop" / "src" / "render" / "render-service.ts"

pytestmark = pytest.mark.skipif(not SERVICE.is_file(), reason="the desktop shell is not in this checkout")


def _asked_for() -> set[str]:
    """Every path `renderer.py` passes to `_ask`, read from the AST rather than by regex.

    The first argument of every `_ask(...)` call. A literal in every case today, and a
    non-literal would be a route this test cannot verify — which is itself worth knowing, so
    it fails rather than skipping quietly.
    """
    asked: set[str] = set()
    for node in ast.walk(ast.parse(FORWARDER.read_text())):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Name) and target.id == "_ask"):
            continue
        assert node.args, "an `_ask` with no route — this test cannot check it"
        first = node.args[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
            "an `_ask` whose route is computed rather than written down; this test can only "
            "check literals, so either write the route or teach this test how to find it"
        )
        asked.add(first.value)
    return asked


def _served() -> set[str]:
    """The shell's own ROUTES array."""
    source = SERVICE.read_text()
    match = re.search(r"const ROUTES = \[(.*?)\]", source, re.DOTALL)
    assert match, "the shell's ROUTES array moved — this test is reading nothing"
    return set(re.findall(r'"(/[a-z-]+)"', match.group(1)))


def test_the_shell_answers_every_route_the_server_forwards_to():
    missing = _asked_for() - _served()
    assert not missing, (
        f"renderer.py forwards to {sorted(missing)} and render-service.ts does not serve them. "
        "A 404 here reads as 'no desktop shell', so the feature fails silently and cleanly."
    )


def test_the_shell_serves_nothing_the_server_never_asks_for():
    """The other direction. A route nobody calls is dead code in the one process where dead
    code is hardest to notice — it has no tests, no types shared with its caller, and the
    caller is in another language.

    Mentioned *anywhere* in the forwarder rather than passed to `_ask`, because two routes are
    deliberately not called through it. `/render` and `/browse` build their request by hand so
    that an HTTP failure can be read and reported instead of swallowed as "there is no shell" —
    a browser pane that refused a page has something to say, and `_ask` is written to lose it.
    Requiring `_ask` here would mean this test dictating how a route is called, which is not
    what it is for.
    """
    source = FORWARDER.read_text()
    orphans = {route for route in _served() if route.lstrip("/") not in source}
    assert not orphans, f"render-service.ts serves {sorted(orphans)}, which nothing calls"
