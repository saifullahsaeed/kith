"""The two copies of the seal agree, and neither has quietly widened.

A sealed document's policy exists twice by necessity: once on the response that serves it, and
once as a `<meta>` the interface bakes into the document itself. The second copy is not
redundancy — it is what keeps a document safe when it is mounted some way other than through
the route that has the authoritative header.

Two statements of one rule drift, and these had. The route's carried `form-action 'none'` and
`base-uri 'none'`; `ui/src/lib/canvas.ts`'s carried neither, and nothing failed anywhere. It was
harmless only because policies compose by intersection and the route happened to be the stricter
half. A plugin surface is the second consumer, which is what turned a latent inconsistency into
something worth a test.

Reading the TypeScript from Python is the precedent this codebase already set for a rule that
has to hold on both sides of a boundary with no type coupling across it.

`ui/src/lib/canvas.test.ts` guards the other half — that the sandbox is not widened for
convenience. This guards that the two languages say the same thing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kith.domain import seal

CANVAS_TS = Path(__file__).resolve().parents[2] / "ui" / "src" / "lib" / "canvas.ts"


def _directives_in(source: str, name: str) -> list[str]:
    """The string literals of a `const <name> = [ ... ].join("; ")` array."""
    match = re.search(rf"const {name} = \[(.*?)\]\.join", source, re.S)
    assert match, f"could not find the {name} array in canvas.ts"
    return re.findall(r'"([^"]+)"', match.group(1))


@pytest.fixture(scope="module")
def canvas_ts() -> str:
    if not CANVAS_TS.is_file():
        pytest.skip("the interface source is not in this checkout")
    return CANVAS_TS.read_text()


def test_the_policies_are_the_same_directives(canvas_ts: str):
    assert _directives_in(canvas_ts, "POLICY") == list(seal.DIRECTIVES)


def test_the_sandbox_attribute_is_the_same(canvas_ts: str):
    match = re.search(r'export const FRAME_SANDBOX = "([^"]*)"', canvas_ts)
    assert match, "could not find FRAME_SANDBOX in canvas.ts"
    assert match.group(1) == seal.FRAME_SANDBOX


def test_the_seal_denies_by_default():
    """The one directive everything else rests on. Connect, frame, worker, form and object all
    inherit this denial, so losing it does not read as a narrower policy — it reads as none."""
    assert seal.DIRECTIVES[0] == "default-src 'none'"


def test_nothing_can_turn_text_into_code():
    """`'unsafe-eval'` has no legitimate use in a document with no network to load a library
    from, and it is the one construct that turns arbitrary text into running code."""
    assert "unsafe-eval" not in seal.POLICY


def test_the_frame_never_gets_its_own_origin():
    """`allow-same-origin` beside `allow-scripts` hands the frame the app's origin and the
    session it already trusts, which is the entire thing the seal prevents."""
    assert "allow-same-origin" not in seal.FRAME_SANDBOX
