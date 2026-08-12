"""What is allowed to interrupt.

Every message used to arrive identically — a note he made while working, a question he was
blocked on, and "I'm stuck" all unread and all worth a notification — so the ones that wanted
an answer were buried in the ones that did not. These tests pin the two properties that make
a threshold safe: the numerous kind is the one that gets quiet first, and nothing is ever
dropped.
"""

from __future__ import annotations

import threading
from typing import ClassVar

import pytest

from kith.infra.db import repositories as repo
from kith.services import notify


class _Store:
    values: ClassVar[dict] = {}

    def load_settings(self, _path):
        return dict(self.values)

    def update_settings(self, _path, updates):
        _Store.values = {**_Store.values, **updates}
        return dict(_Store.values)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    _Store.values = {}
    monkeypatch.setattr(notify, "_store", lambda: (tmp_path / "config.db", _Store()))
    # Never actually post a notification from a test.
    # Signature must match the real one — announce() swallows exceptions so a tool call is
    # never taken down by a doorbell, which means a stale stub here fails as a silent False
    # rather than as a TypeError. That is exactly how these two tests broke when `link` was
    # added, so the stub takes it too.
    monkeypatch.setattr("kith.infra.renderer.notify", lambda title, body, link=None: True)
    yield
    _Store.values = {}


class TestTheThreshold:
    def test_the_default_lets_through_what_needs_an_answer(self):
        assert notify.level() is notify.Level.NEEDS_YOU
        assert notify.interrupts("asked")
        assert notify.interrupts("stuck")
        assert notify.interrupts("delivered")
        assert notify.interrupts("reachout")

    def test_the_default_silences_running_commentary(self):
        """The one that was burying everything else."""
        assert not notify.interrupts("note")

    def test_everything_means_everything(self):
        notify.set_level("all")
        assert notify.interrupts("note")

    def test_the_quietest_setting_is_still_a_channel(self):
        notify.set_level("reachout")
        assert notify.interrupts("reachout")
        for quiet in ("note", "asked", "stuck", "delivered"):
            assert not notify.interrupts(quiet)

    def test_an_unknown_stored_level_falls_back_to_the_default(self):
        _Store.values["notify_level"] = "loud"
        assert notify.level() is notify.Level.NEEDS_YOU

    def test_a_bad_level_is_refused_rather_than_stored(self):
        with pytest.raises(ValueError):
            notify.set_level("whatever")


class TestNothingIsDropped:
    def test_a_quiet_message_is_still_recorded(self, db):
        """ "Quieter" must never mean "you did not find out" — the channel is the history."""
        notify.set_level("reachout")
        repo.messages.add_message(db, "a note while working", kind="note")
        assert len(repo.messages.list_messages(db)) == 1

    def test_a_quiet_message_does_not_light_the_badge(self, db):
        notify.set_level("reachout")
        repo.messages.add_message(db, "a note while working", kind="note")
        assert repo.messages.list_messages(db, unread_only=True) == []

    def test_a_loud_message_does(self, db):
        repo.messages.add_message(db, "I need your input", kind="asked")
        assert len(repo.messages.list_messages(db, unread_only=True)) == 1

    def test_your_own_messages_never_interrupt_you(self, db):
        """You were there when you wrote it."""
        notify.set_level("all")
        repo.messages.add_message(db, "hello", sender="user")
        assert repo.messages.list_messages(db, unread_only=True) == []

    def test_the_kind_is_kept_on_the_row(self, db):
        repo.messages.add_message(db, "finished a thing", kind="delivered")
        assert repo.messages.list_messages(db)[0]["kind"] == "delivered"


class TestAnnouncing:
    def test_it_reports_whether_one_went_out(self):
        assert notify.announce("asked", "come look") is True
        notify.set_level("reachout")
        assert notify.announce("note", "just working") is False

    def test_a_failing_notification_never_breaks_the_caller(self, monkeypatch):
        """He said the thing and it is recorded; a doorbell that will not ring is not a
        reason to fail the tool call that rang it.

        This used to assert `announce(...) is False` — a failing delivery reported itself. That
        stopped being possible when delivery moved onto its own thread, because
        `renderer.notify` posts to the desktop shell with a 55-second timeout and the things
        raising notifications are now a permission gate and a question, both of which the turn is
        already parked on. A doorbell must not hold the door.

        So the return value means "it was sent", not "it arrived", which is the honest thing to
        promise about a notification. The property the test is named for is unchanged and is what
        it checks now: the caller comes back cleanly from a delivery that is going to fail.
        """
        tried = threading.Event()

        def explode(title, body, link=None):
            tried.set()
            raise RuntimeError("no desktop app")

        monkeypatch.setattr("kith.infra.renderer.notify", explode)

        # Returns rather than raises, and says it was sent.
        assert notify.announce("asked", "come look") is True
        # And the failure really did happen — otherwise this passes on a delivery never attempted,
        # which would make it a test of nothing.
        assert tried.wait(timeout=5), "the notification was never attempted"

    def test_a_long_body_is_trimmed_rather_than_truncated_mid_word(self, monkeypatch):
        seen = _delivery(monkeypatch)
        notify.announce("stuck", "x " * 400)
        assert seen.wait(timeout=5), "the notification was never delivered"
        assert len(seen["body"]) <= 160
        assert seen["body"].endswith("…")
        assert seen["title"] == "Kith is stuck"

    def test_the_link_reaches_the_notification(self, monkeypatch):
        """The whole point of a deeplink: a notification that names a task has to be able to
        open it, or you read "I need your input on task #42" and go hunting for task #42."""
        seen = _delivery(monkeypatch)
        notify.announce("asked", "come look", "/tasks/42")
        assert seen.wait(timeout=5), "the notification was never delivered"
        assert seen["link"] == "/tasks/42"


class _Delivery(dict):
    """What the notification was called with, and a way to wait for it.

    Waiting is not optional any more. `announce` hands delivery to its own thread — a doorbell
    must not hold the door — so reading these straight after the call is a race that the caller
    usually wins and does not have to. Demonstrated rather than assumed: with 50ms of delay in
    the notifier, the previous shape of these two tests fails with `KeyError: 'body'`.
    """

    def __init__(self) -> None:
        super().__init__()
        self.delivered = threading.Event()

    def wait(self, timeout: float) -> bool:
        return self.delivered.wait(timeout=timeout)


def _delivery(monkeypatch) -> _Delivery:
    seen = _Delivery()

    def record(title, body, link=None):
        seen.update(title=title, body=body, link=link)
        seen.delivered.set()
        return True

    monkeypatch.setattr("kith.infra.renderer.notify", record)
    return seen
