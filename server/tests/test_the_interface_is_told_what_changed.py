"""One stream saying what changed, instead of eleven timers asking.

The app had exactly one push channel — `/api/activity/stream` — carrying the activity feed and
nothing else. Everything else asked on its own clock: eleven `setInterval`s between 1.2 and 20
seconds, each fetching its own endpoint. So a task appeared when the working-task card next polled,
a background task when *that* one did, the board when the control panel got round to it. One Cmd-R
got you one of them and a second got you another.

And underneath that, a hole no poll interval closes: **a turn the server starts on its own** — a
reminder firing, a background task finishing — pushed nothing at all. The transcript grew on disk,
`live_turns` had the stream ready to be watched, and the open window never learned to attach. No
amount of polling fixes that, because none of the things being polled is "a turn just started".

So: one `changes` stream, one subscription, typed events. A widget refetches when something it cares
about actually changed rather than on a timer, and a turn that starts without you announces itself.

Deliberately *not* carrying the new data. An event says "tasks changed", not the task — because the
payload would then have to satisfy every consumer of every shape, and the fetch that follows already
exists and is already correct. Say what changed; let them ask.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from kith.kernel import changes


@pytest.fixture(autouse=True)
def no_leftover_subscribers():
    yield
    changes.stream._subscribers.clear()


def _drain(subscription) -> list[dict]:
    out = []
    while True:
        try:
            out.append(subscription.get_nowait())
        except Exception:
            return out


class TestSayingWhatChanged:
    def test_a_subscriber_hears_an_event(self):
        subscription = changes.subscribe()
        changes.publish("task")
        assert [event["kind"] for event in _drain(subscription)] == ["task"]

    def test_two_subscribers_both_hear_it(self):
        """Two windows, or a window and the desktop shell."""
        first, second = changes.subscribe(), changes.subscribe()
        changes.publish("task")
        assert len(_drain(first)) == 1
        assert len(_drain(second)) == 1

    def test_an_unsubscribed_queue_stops_hearing(self):
        subscription = changes.subscribe()
        changes.unsubscribe(subscription)
        changes.publish("task")
        assert _drain(subscription) == []

    def test_it_carries_the_conversation_when_there_is_one(self):
        """A widget showing one conversation must be able to ignore another's noise."""
        subscription = changes.subscribe()
        changes.publish("process", conversation="c-1")
        assert _drain(subscription)[0]["conversation"] == "c-1"

    def test_the_event_is_json(self):
        """It goes out over SSE as a `data:` line, so anything unserialisable is a broken stream."""
        subscription = changes.subscribe()
        changes.publish("turn", conversation="c-1")
        json.dumps(_drain(subscription)[0])

    def test_publishing_with_nobody_listening_is_fine(self):
        """Every write path calls this. None of them may fail because no window is open."""
        changes.publish("task")

    def test_a_slow_subscriber_cannot_block_a_writer(self):
        """A queue nobody drains must not stop a task being saved — the write is the point and this
        is the notification about it."""
        changes.subscribe()
        for _ in range(500):
            changes.publish("task")


class TestWhatPublishes:
    def test_starting_a_turn_says_so(self):
        """The one that fixes the reload. A turn the server starts — a reminder, a finished
        background task — is invisible to an open window until something says it began."""
        from kith.kernel import live_turns

        subscription = changes.subscribe()
        turn = live_turns.begin("c-1")
        events = _drain(subscription)
        live_turns.finish(turn)

        assert any(e["kind"] == "turn" and e["conversation"] == "c-1" for e in events), events

    def test_finishing_a_turn_says_so_too(self):
        from kith.kernel import live_turns

        turn = live_turns.begin("c-1")
        subscription = changes.subscribe()
        live_turns.finish(turn)

        assert any(e["kind"] == "turn" for e in _drain(subscription))

    def test_filing_a_task_says_so(self, db):
        from kith.infra.db import repositories as repo

        subscription = changes.subscribe()
        repo.tasks.add_task(db, "Do it")

        assert any(e["kind"] == "task" for e in _drain(subscription))

    def test_changing_a_task_says_so(self, db):
        from kith.infra.db import repositories as repo

        task = repo.tasks.add_task(db, "Do it")
        subscription = changes.subscribe()
        repo.tasks.update_task(db, int(task["id"]), status="working")

        assert any(e["kind"] == "task" for e in _drain(subscription))

    def test_ticking_a_checklist_item_says_so(self, db):
        """The one you watch while he works — it is what the working-task card counts through."""
        from kith.infra.db import repositories as repo

        task = repo.tasks.add_task(db, "Do it")
        item = repo.tasks.add_checklist_item(db, int(task["id"]), "step one")
        subscription = changes.subscribe()
        repo.tasks.set_checklist_item(db, int(item["id"]), done=True)

        assert any(e["kind"] == "task" for e in _drain(subscription))

    def test_a_new_message_says_so(self, db):
        from kith.infra.db import repositories as repo

        subscription = changes.subscribe()
        repo.messages.add_message(db, "look at this", kind="asked")

        assert any(e["kind"] == "message" for e in _drain(subscription))


def test_every_kind_has_a_publisher_and_every_publisher_a_kind():
    """`changes.KINDS` promises something `publish()` cannot enforce, so assert it here.

    The comment on `KINDS` says the tuple exists "so a typo in a publisher is a test failure
    here rather than a widget that silently never updates". Nothing made that true: `publish`
    takes a `str` and always has, so a misspelled kind was published cheerfully to nobody.

    Both directions fail silently, which is why both are checked:

    * a publisher naming a kind that is not declared updates no widget;
    * a declared kind nobody publishes is a subscription that can never fire, and reads from
      the interface as a feature that is merely quiet.

    `conversation` was the second case — declared here and in the interface's `ChangeKind`,
    published by no one — and is gone.
    """
    import ast

    source = Path(__file__).resolve().parent.parent / "kith"
    published: dict[str, list[str]] = defaultdict(list)
    for path in sorted(source.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # `changes.publish` specifically. `services/activity.py` has a `publish` of its
            # own with a different vocabulary — "done", "reminder" — and matching on the
            # method name alone swept those in.
            is_publish = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "publish"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "changes"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            )
            if is_publish:
                published[node.args[0].value].append(f"{path.relative_to(source.parent)}:{node.lineno}")

    undeclared = {kind: where for kind, where in published.items() if kind not in changes.KINDS}
    assert not undeclared, "published but not in KINDS — nothing is listening:\n" + "\n".join(
        f"  {kind!r} at {', '.join(where)}" for kind, where in sorted(undeclared.items())
    )

    unpublished = [kind for kind in changes.KINDS if kind not in published]
    assert not unpublished, (
        f"declared in KINDS but nothing publishes them: {unpublished}. A widget subscribing to "
        "one would never update, and would look merely quiet rather than broken."
    )
