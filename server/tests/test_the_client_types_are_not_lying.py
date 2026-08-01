"""The TypeScript interface must describe what the server actually sends.

TypeScript checks the client against itself, never against the API — so a declared field the
server stopped sending type-checks perfectly and crashes at runtime. There is no compiler on
that seam, which makes it the one place a hand-written assertion earns its keep.

It has already happened. `BrainSnapshot` declared `curiosities: Curiosity[]`, non-optional,
long after the curiosities feature was removed from the server. The Control Panel still had
a nav item, a count and a whole page for it, and the page opened with

    snap.curiosities.filter(...)

on a field that was no longer in the payload. Clicking "Curiosities" was a guaranteed
TypeError, `tsc` was perfectly happy, and it survived a full audit pass because nothing
compares the two sides.

The same technique as test_mind_feed.py: read the declaration out of the TypeScript and
compare it against what the service builds.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kith import settings
from kith.services import brain

CLIENT = settings.SERVER_ROOT.parent / "ui" / "src" / "lib" / "backend" / "brain.ts"


def declared_fields(interface: str) -> dict[str, bool]:
    """Field name -> whether it is optional, for one interface in brain.ts."""
    source = CLIENT.read_text(encoding="utf-8")
    match = re.search(rf"export interface {interface} \{{(.*?)^}}", source, re.S | re.M)
    assert match, f"could not find `export interface {interface}` — has brain.ts moved?"
    fields: dict[str, bool] = {}
    for line in match.group(1).splitlines():
        found = re.match(r"\s*(\w+)(\??):", line)
        if found:
            fields[found.group(1)] = found.group(2) == "?"
    return fields


@pytest.fixture
def snapshot(db: Path) -> dict:
    return brain.snapshot(db)


class TestTheBrainSnapshot:
    def test_the_client_declares_nothing_the_server_does_not_send(self, snapshot):
        """The direction that crashes.

        A required field the server omits is `undefined` at runtime, and the first `.map` or
        `.filter` on it throws.
        """
        required = {name for name, optional in declared_fields("BrainSnapshot").items() if not optional}
        missing = sorted(required - set(snapshot))
        assert not missing, (
            f"brain.ts requires fields /api/brain does not send: {missing}. "
            "Anything that reads one of these will throw the moment it renders."
        )

    def test_and_the_server_sends_nothing_the_client_has_forgotten(self, snapshot):
        """The other direction is not a crash, but it is dead weight on every request and
        usually means a feature was half-wired."""
        declared = set(declared_fields("BrainSnapshot"))
        unused = sorted(set(snapshot) - declared)
        assert not unused, f"/api/brain sends fields no client type knows about: {unused}"

    def test_an_optional_field_is_one_the_server_really_can_omit(self, snapshot):
        """`mood?` and `self?` are optional because a fresh brain has neither. If one is
        always present, the `?` costs every caller a null check that can never fire."""
        optional = {name for name, is_optional in declared_fields("BrainSnapshot").items() if is_optional}
        assert optional, "the interface has no optional fields — has the parser broken?"


class TestTheTimeline:
    def test_the_client_knows_every_kind_the_server_can_emit(self, db: Path):
        """An unknown kind falls through whatever switch renders it, usually to nothing —
        an event that happened and cannot be seen."""
        source = CLIENT.read_text(encoding="utf-8")
        match = re.search(r"export type TimelineKind =\s*(.*?);", source, re.S)
        assert match, "could not find TimelineKind in brain.ts"
        known = set(re.findall(r'"(\w+)"', match.group(1)))

        # Seed one of everything the timeline can carry, so this tests the real query rather
        # than an empty table.
        from kith.infra.db import repositories as repo

        repo.memories.add_memory(db, "something worth keeping")
        repo.notes.add_note(db, "a note", "with a body")
        repo.journal.add_journal(db, "a journal entry")
        repo.tasks.add_task(db, "a task")
        repo.reminders.add_reminder(db, "a reminder", "2030-01-01T00:00:00+00:00")
        repo.messages.add_message(db, "a message")

        emitted = {event["kind"] for event in brain.timeline(db)}
        unknown = sorted(emitted - known)
        assert not unknown, f"the server emits timeline kinds the client cannot render: {unknown}"
