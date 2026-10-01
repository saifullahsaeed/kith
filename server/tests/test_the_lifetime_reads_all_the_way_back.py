"""The Lifetime screen can read his whole history, a page at a time.

`GET /api/brain/timeline` was the newest 500 events and nothing more: five tables loaded
whole, merged, sorted, and cut. On this machine that is 1,336 events, so the screen ended in
late August with no way past it — and the channel's own query was capped at 100, so most of
his messages were never in the merge at all. The chips counted what had loaded, so "Journal
312" on a journal of 709.

These pin the page: strictly older than its cursor, no gaps and no repeats between pages, the
whole history reachable by following `has_more`, and totals that count everything rather than
the page. And the bare call keeps answering an array, because Today and anything else that
asks with no parameters was written against that shape.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from kith.infra.db import repositories as repo
from kith.services import brain

START = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def _stamp(table: str, db, row_id: int, minutes: int) -> str:
    """Move a row to a known time, so the merge's order is the test's to choose."""
    at = (START + timedelta(minutes=minutes)).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute(f"UPDATE {table} SET created_at = ? WHERE id = ?", (at, row_id))
    return at


@pytest.fixture
def history(db):
    """Forty events, one a minute, cycling through all five tables."""
    makers = [
        ("memories", lambda n: repo.memories.add_memory(db, f"fact {n}")),
        ("journal", lambda n: repo.journal.add_journal(db, f"entry {n}")),
        ("tasks", lambda n: repo.tasks.add_task(db, f"goal {n}")),
        ("reminders", lambda n: repo.reminders.add_reminder(db, START.isoformat(), f"nudge {n}")),
        ("messages", lambda n: repo.messages.add_message(db, f"note {n}")),
    ]
    stamps = []
    for n in range(40):
        table, make = makers[n % len(makers)]
        stamps.append(_stamp(table, db, make(n)["id"], n))
    return db, stamps


def _walk(db, limit: int) -> list[list[dict]]:
    pages, before = [], None
    while True:
        page = brain.timeline_page(db, before=before, limit=limit)
        pages.append(page["events"])
        if not page["has_more"]:
            return pages
        before = page["events"][-1]["at"]


class TestAPage:
    def test_the_first_page_is_the_newest_and_says_there_is_more(self, history):
        db, stamps = history

        page = brain.timeline_page(db, limit=10)

        assert [e["at"] for e in page["events"]] == sorted(stamps, reverse=True)[:10]
        assert page["has_more"] is True

    def test_a_page_is_strictly_older_than_its_cursor(self, history):
        db, stamps = history
        cursor = sorted(stamps)[25]

        page = brain.timeline_page(db, before=cursor, limit=5)

        assert all(e["at"] < cursor for e in page["events"])
        assert [e["at"] for e in page["events"]] == sorted(stamps, reverse=True)[15:20]

    def test_following_the_pages_reaches_the_first_thing_with_no_gap_and_no_repeat(self, history):
        db, stamps = history

        pages = _walk(db, limit=7)

        joined = [e["at"] for page in pages for e in page]
        assert joined == sorted(stamps, reverse=True), "every event exactly once, newest first"
        assert len(pages) == 6

    def test_a_page_past_the_beginning_is_empty_rather_than_the_last_page_again(self, history):
        db, stamps = history

        page = brain.timeline_page(db, before=min(stamps), limit=10)

        assert page["events"] == []
        assert page["has_more"] is False

    def test_a_page_never_cuts_through_events_that_share_a_moment(self, db):
        """A cut through a tie would lose its second half: the next page asks for strictly
        older than the last event shown, and the rest of the tie is not older."""
        for n in range(3):
            _stamp("journal", db, repo.journal.add_journal(db, f"later {n}")["id"], 10 + n)
        for n in range(4):
            _stamp("memories", db, repo.memories.add_memory(db, f"same moment {n}")["id"], 5)
        _stamp("tasks", db, repo.tasks.add_task(db, "first")["id"], 0)

        first = brain.timeline_page(db, limit=5)
        rest = brain.timeline_page(db, before=first["events"][-1]["at"], limit=5)

        assert [e["text"] for e in first["events"]] == ["later 2", "later 1", "later 0"]
        assert sorted(e["text"] for e in rest["events"][:4]) == [f"same moment {n}" for n in range(4)]
        assert rest["events"][-1]["text"] == "first"

    def test_the_limit_is_held_to_the_most_anybody_can_ask_for(self, history):
        db, _ = history
        assert len(brain.timeline_page(db, limit=10**6)["events"]) == 40
        assert brain.MAX_PAGE == 2000


class TestTotals:
    def test_totals_count_all_of_history_not_the_page(self, history):
        db, stamps = history

        page = brain.timeline_page(db, limit=3)

        assert page["totals"] == {"memory": 8, "journal": 8, "task": 8, "reminder": 8, "message": 8}
        assert page["oldest"] == min(stamps)

    def test_an_output_alert_is_counted_as_delivered_whatever_it_was_filed_as(self, db):
        """Older output alerts were filed as notes. The screen knows them by their words, so the
        Outputs chip's total has to count them the same way or the chip and its rows disagree."""
        repo.messages.add_message(db, "Finished something on “Pace”: home screen", kind="delivered")
        repo.messages.add_message(db, "Finished something on “Pace”: settings screen", kind="note")
        repo.messages.add_message(db, "I need an answer before I can carry on: which font?", kind="asked")
        repo.messages.add_message(db, "just a note")

        kinds = brain.timeline_page(db, limit=10)["message_kinds"]

        assert kinds == {"delivered": 2, "asked": 1, "note": 1}

    def test_a_message_says_what_it_was_without_the_snapshot(self, db):
        """The snapshot holds the newest hundred messages; a page from months ago must still
        know a question from a note."""
        repo.messages.add_message(db, "which font?", kind="asked", link="/chat/abc")

        [event] = brain.timeline_page(db, limit=10)["events"]

        assert event["meta"]["kind"] == "asked"
        assert event["meta"]["link"] == "/chat/abc"

    def test_an_empty_mind_has_no_beginning(self, db):
        page = brain.timeline_page(db)

        assert page == {
            "events": [],
            "has_more": False,
            "totals": {"memory": 0, "journal": 0, "task": 0, "reminder": 0, "message": 0},
            "message_kinds": {},
            "oldest": None,
        }


@pytest.fixture
def client(history, monkeypatch):
    from kith import settings
    from kith.api import auth
    from kith.api.routes import brain as route
    from kith.app import create_app

    db, _ = history
    monkeypatch.setattr(route, "AGENT_DB_PATH", db)
    made = create_app()
    made.config["TESTING"] = True
    given = made.test_client()
    given.environ_base[f"HTTP_{auth.HEADER.upper().replace('-', '_')}"] = auth.token(settings.DATA_DIR)
    return given


class TestTheEndpoint:
    def test_asked_bare_it_is_still_an_array(self, client):
        body = client.get("/api/brain/timeline").get_json()

        assert isinstance(body, list)
        assert len(body) == 40

    def test_asked_for_a_page_it_is_a_page(self, client):
        body = client.get("/api/brain/timeline?page=1&limit=10").get_json()

        assert len(body["events"]) == 10
        assert body["has_more"] is True
        assert body["totals"]["journal"] == 8

    def test_a_cursor_in_another_offset_means_the_same_moment(self, client, history):
        """Stored times compare as text, so a cursor written with `Z` or +04:00 has to be put in
        the stored form first or it sorts against the wrong events."""
        _, stamps = history
        cursor = sorted(stamps)[20]
        dubai = datetime.fromisoformat(cursor).astimezone(timezone(timedelta(hours=4)))
        as_utc = client.get(f"/api/brain/timeline?before={cursor.replace('+00:00', 'Z')}").get_json()
        local = client.get("/api/brain/timeline", query_string={"before": dubai.isoformat()}).get_json()

        assert [e["at"] for e in as_utc["events"]] == sorted(stamps, reverse=True)[20:]
        assert local["events"] == as_utc["events"]

    def test_a_cursor_that_is_not_a_time_is_refused(self, client):
        response = client.get("/api/brain/timeline?before=yesterday")

        assert response.status_code == 400

    def test_a_bad_limit_is_the_default_not_an_error(self, client):
        body = client.get("/api/brain/timeline?page=1&limit=lots").get_json()

        assert len(body["events"]) == 40
