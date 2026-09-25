"""The two statements of the seal agree, and neither has quietly widened.

The policy itself now lives only on the server (`domain/seal.py`), which serves every sealed
document — today that is a plugin surface. The interface still states one half of the seal: the
`sandbox` attribute it mounts the frame with, in `ui/src/lib/seal.ts`. Two statements of one rule
drift, and these once had (the retired canvas's `<meta>` copy carried neither `form-action` nor
`base-uri`), so the half that is still stated twice is compared here.

Reading the TypeScript from Python is the precedent this codebase already set for a rule that
has to hold on both sides of a boundary with no type coupling across it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kith.domain import seal

SEAL_TS = Path(__file__).resolve().parents[2] / "ui" / "src" / "lib" / "seal.ts"


@pytest.fixture(scope="module")
def seal_ts() -> str:
    if not SEAL_TS.is_file():
        pytest.skip("the interface source is not in this checkout")
    return SEAL_TS.read_text()


def test_the_sandbox_attribute_is_the_same(seal_ts: str):
    match = re.search(r'export const FRAME_SANDBOX = "([^"]*)"', seal_ts)
    assert match, "could not find FRAME_SANDBOX in seal.ts"
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
