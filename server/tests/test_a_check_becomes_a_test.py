"""A check Kith did in the browser, kept as a Playwright test the project runs without him.

The desktop app records each act in the shape a test needs — a click keeps its element's role and
name, a field keeps how it was found — and `playwright_spec` writes that out as an ordinary
`@playwright/test` spec. Asserted here: that each kind of step becomes the locator a person
writing the test would have chosen, that a `wait` becomes the assertion, and that a recording
with no `wait` says it checks nothing rather than looking like coverage.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from kith.domain import playwright_spec
from kith.infra import renderer
from kith.tools import browser as browser_tool

FLOW = [
    {"kind": "goto", "url": "http://localhost:5173/form"},
    {"kind": "fill", "by": "label", "key": "Work email", "text": "saif@example.com"},
    {"kind": "fill", "by": "placeholder", "key": "Search", "text": "shoes"},
    {"kind": "fill", "by": "selector", "key": '[name="card"]', "text": "4242"},
    {"kind": "select", "by": "label", "key": "Country", "option": "Canada"},
    {"kind": "click", "role": "button", "name": "Send", "selector": "", "point": "", "openedTab": False},
    {"kind": "press", "key": "Enter"},
    {"kind": "wait", "text": "Thanks, we got it", "selector": "", "url": "", "gone": False},
    {"kind": "wait", "text": "", "selector": "", "url": "/done", "gone": False},
]


def test_each_step_becomes_the_locator_a_person_would_write():
    source, _ = playwright_spec.write("the form sends", FLOW, signed_in=False, when=date(2026, 9, 30))

    assert source.startswith('import { test, expect } from "@playwright/test";')
    assert 'test("the form sends", async ({ page }) => {' in source
    for line in (
        'await page.goto("http://localhost:5173/form");',
        'await page.getByLabel("Work email").fill("saif@example.com");',
        'await page.getByPlaceholder("Search").fill("shoes");',
        """await page.locator("[name=\\"card\\"]").fill("4242");""",
        'await page.getByLabel("Country").selectOption({ label: "Canada" });',
        'await page.getByRole("button", { name: "Send" }).first().click();',
        'await page.keyboard.press("Enter");',
        'await expect(page.getByText("Thanks, we got it").first()).toBeVisible();',
        'await page.waitForURL((url) => url.href.includes("/done"));',
    ):
        assert line in source, line


def test_a_recording_that_checks_nothing_says_so():
    source, notes = playwright_spec.write("clicks around", FLOW[:6], signed_in=False)

    assert "Nothing here checks an outcome" in source
    assert any("passes as long as no step throws" in note for note in notes)
    # No assertion, so no `expect` to import — an unused import is a lint failure in their repo.
    assert source.startswith('import { test } from "@playwright/test";')


def test_a_flow_recorded_signed_in_warns_that_the_test_starts_signed_out():
    _, notes = playwright_spec.write("x", FLOW, signed_in=True)

    assert any("starts signed out" in note for note in notes)


def test_a_click_at_a_point_is_kept_as_a_note_not_as_a_guess():
    steps = [
        FLOW[0],
        {"kind": "click", "role": "", "name": "", "selector": "", "point": "10,20", "openedTab": False},
    ]
    source, notes = playwright_spec.write("x", steps, signed_in=False)

    assert "// Clicked at 10,20" in source
    assert notes and "screen position" in notes[0]


def test_quotes_in_the_page_cannot_break_the_file():
    steps = [
        FLOW[0],
        {"kind": "wait", "text": 'He said "done" `now`', "selector": "", "url": "", "gone": True},
    ]
    source, _ = playwright_spec.write('a "quoted" name', steps, signed_in=False)

    assert 'test("a \\"quoted\\" name"' in source
    assert 'page.getByText("He said \\"done\\" `now`").first()).toBeHidden()' in source


# --------------------------------------------------------------------------- #
# save_test, through the tool
# --------------------------------------------------------------------------- #


@pytest.fixture
def written(monkeypatch) -> dict:
    kept: dict = {}
    from kith.infra import workspace

    monkeypatch.setattr(
        workspace, "write_file", lambda path, content: kept.update({path: content}) or "wrote"
    )
    monkeypatch.setattr(renderer, "browse", lambda act, owner="", args=None: {"steps": FLOW, "private": True})
    return kept


def test_save_test_writes_the_spec_where_it_was_asked(written, db: Path):
    answer = browser_tool.browser(
        db, {"act": "save_test", "name": "the form sends", "path": "tests/e2e/form.spec.ts"}
    )

    assert answer == {"saved": "tests/e2e/form.spec.ts", "steps": len(FLOW)}
    assert 'test("the form sends"' in written["tests/e2e/form.spec.ts"]


@pytest.mark.parametrize(
    ("args", "said"),
    [({"act": "save_test"}, "needs a path"), ({"act": "save_test", "path": "notes.txt"}, "not a test file")],
)
def test_save_test_refuses_without_a_proper_path(written, db: Path, args, said):
    assert said in browser_tool.browser(db, args)["error"]
    assert written == {}


def test_save_test_with_nothing_recorded_says_what_to_do(monkeypatch, db: Path):
    monkeypatch.setattr(renderer, "browse", lambda *a, **k: {"steps": [FLOW[0]]})

    assert (
        "Nothing to save yet" in browser_tool.browser(db, {"act": "save_test", "path": "t.spec.ts"})["error"]
    )
