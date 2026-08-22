"""A turn you walked away from is still a turn, and the interface can see it.

Two things were wrong and they were the same thing seen from either end.

**The shell could not tell you.** `workspace.tsx` had `const working = false` — one variable driving
both the presence orb and the ambient wash — with a comment saying they were switched off rather
than lying, waiting for "one honest meaning: a session is busy if and only if a turn is live in it".
Nothing exposed that meaning. A turn running in a conversation you were not looking at announced
itself as a 6px dot on a row inside a panel you had to have open, which is indistinguishable from
nothing happening.

**And leaving killed it anyway.** The client posted `/stop` from its abort handler, and an abort is
not a statement of intent — it fires for a person pressing Stop and for a view being torn down
alike. So switching conversations ended the work in the one you left. `live_turns`' own docstring
describes that exact failure as the thing it was written to end.

So: `live()` names the conversations with a turn running, `/api/chat/live` serves it, and a `turn`
change invalidates it — the interface asks when something starts or finishes and not otherwise.
"""

from __future__ import annotations

import pytest

from kith.kernel import events, live_turns


@pytest.fixture(autouse=True)
def no_leftover_turns():
    yield
    live_turns._LIVE.clear()
    events.log._subscribers.clear()


@pytest.fixture
def client():
    from kith import settings
    from kith.api import auth
    from kith.app import create_app

    made = create_app()
    made.config["TESTING"] = True
    given = made.test_client()
    given.environ_base[f"HTTP_{auth.HEADER.upper().replace('-', '_')}"] = auth.token(settings.DATA_DIR)
    return given


class TestWhoIsWorking:
    def test_nothing_running_is_an_empty_list(self):
        assert live_turns.live() == []

    def test_a_live_turn_names_its_conversation(self):
        turn = live_turns.begin("c-1")
        try:
            assert live_turns.live() == ["c-1"]
        finally:
            live_turns.finish(turn)

    def test_a_finished_turn_is_not_live(self):
        """The orb going out is as load bearing as it coming on. A busy signal that outlives the
        work is worse than none, because you stop believing the one that is true."""
        turn = live_turns.begin("c-1")
        live_turns.finish(turn)
        assert live_turns.live() == []

    def test_two_conversations_can_both_be_working(self):
        """Which is the case the interface had no way to show at all — and the reason this returns
        ids rather than a count. "Something is running" and "*that* conversation is running" are
        different sentences, and only the second tells you where to go."""
        first, second = live_turns.begin("c-1"), live_turns.begin("c-2")
        try:
            assert sorted(live_turns.live()) == ["c-1", "c-2"]
        finally:
            live_turns.finish(first)
            live_turns.finish(second)

    def test_beginning_again_in_one_conversation_does_not_double_it(self):
        """`begin` replaces a stale record rather than adding a second. One conversation cannot be
        working twice, and a list that said so would put two rows in the header for one turn."""
        first = live_turns.begin("c-1")
        second = live_turns.begin("c-1")
        try:
            assert live_turns.live() == ["c-1"]
        finally:
            live_turns.finish(first)
            live_turns.finish(second)


class TestTheEndpoint:
    def test_it_answers_with_the_ids(self, client):
        turn = live_turns.begin("c-1")
        try:
            answered = client.get("/api/chat/live")
            assert answered.status_code == 200
            assert answered.get_json() == {"conversations": ["c-1"]}
        finally:
            live_turns.finish(turn)

    def test_live_is_not_read_as_a_conversation_id(self, client):
        """`/api/chat/live` sits beside `/api/chat/<conversation_id>/…`, so the one thing that could
        go quietly wrong is the router treating "live" as an id. It does not — static segments win
        — but the failure would be a 404 on the endpoint the header depends on, which reads as
        nothing ever working."""
        assert client.get("/api/chat/live").status_code == 200


class TestTheListingSaysWhichRowIsAlive:
    def test_a_row_with_a_live_turn_is_marked_working(self, db):
        """The dot in the history panel, which could never light up.

        `working` has been on the public conversation shape all along and nothing set it — it was
        read from `row.get("working")` and there is no such column. So the one existing sign that a
        conversation you were not looking at was alive was decoration.
        """
        from kith.services import conversations

        started = conversations.start(db, "hello")
        turn = live_turns.begin(str(started["id"]))
        try:
            rows = conversations.recent(db, 10)
            mine = next(row for row in rows if row["id"] == started["id"])
            assert mine["working"] is True
        finally:
            live_turns.finish(turn)

    def test_a_quiet_row_is_not(self, db):
        from kith.services import conversations

        started = conversations.start(db, "hello")
        rows = conversations.recent(db, 10)
        mine = next(row for row in rows if row["id"] == started["id"])
        assert mine["working"] is False


class TestWhatTheRowsSay:
    def test_a_conversation_blocked_on_a_question_is_waiting(self, db):
        """The state the panel had no way to show. A parked question announced itself once, as a
        badge and a notification, and then nothing — so a card you scrolled past is a turn sitting
        there for its full deadline looking hung."""
        from kith.services import conversations, questions

        started = conversations.start(db, "hello")
        id = str(started["id"])
        questions._OPEN[id] = questions.Question(id="q1", conversation_id=id, asked=[])
        try:
            mine = next(row for row in conversations.recent(db, 10) if row["id"] == id)
            assert mine["waiting"] is True
            assert mine["working"] is False
        finally:
            questions._OPEN.pop(id, None)

    def test_answered_is_no_longer_waiting(self, db):
        from kith.services import conversations, questions

        started = conversations.start(db, "hello")
        id = str(started["id"])
        question = questions.Question(id="q1", conversation_id=id, asked=[])
        questions._OPEN[id] = question
        try:
            question.replies = []  # answered
            mine = next(row for row in conversations.recent(db, 10) if row["id"] == id)
            assert mine["waiting"] is False
        finally:
            questions._OPEN.pop(id, None)

    def test_working_and_waiting_are_different_questions(self, db):
        """One needs nothing from you; the other needs only you. A row that conflated them would
        put "still working" on a turn that has stopped and is asking."""
        from kith.services import conversations

        started = conversations.start(db, "hello")
        id = str(started["id"])
        turn = live_turns.begin(id)
        try:
            mine = next(row for row in conversations.recent(db, 10) if row["id"] == id)
            assert (mine["working"], mine["waiting"]) == (True, False)
        finally:
            live_turns.finish(turn)


class TestSayingSo:
    def test_beginning_a_turn_publishes(self):
        """What makes the header current without asking. Both directions matter: the interface
        needs to hear the turn start to light up, and hear it finish to go dark."""
        subscription = events.subscribe()
        try:
            turn = live_turns.begin("c-1")
            heard = _kinds(subscription)
            live_turns.finish(turn)
        finally:
            events.unsubscribe(subscription)
        assert "turn" in heard

    def test_finishing_a_turn_publishes_too(self):
        turn = live_turns.begin("c-1")
        subscription = events.subscribe()
        try:
            live_turns.finish(turn)
            assert "turn" in _kinds(subscription)
        finally:
            events.unsubscribe(subscription)


def _kinds(subscription) -> list[str]:
    out = []
    while True:
        try:
            event = subscription.queue.get_nowait()
        except Exception:
            return out
        if event.type == "changed":
            out.append(event.data["kind"])


class TestAWakeIsNotSomethingYouSaid:
    """A reminder firing opens a turn with a sentence nobody typed.

    It was recorded as a `user` message, and three things followed. Reopening the conversation
    showed a scheduler's prose in the person's own bubble with an edit pencil on it. Every later
    turn replayed it to the model as their words, so a harness instruction became a standing
    request from the person for the rest of the conversation's life. And the message count grew
    by one for a turn nobody started.
    """

    def test_it_is_recorded_as_system(self, db, tmp_path, monkeypatch):
        from kith.services import conversations

        started = conversations.start(db, "hello")
        id = str(started["id"])
        conversations.record(db, id, "system", "One of your reminders just fired.")

        entries = [e for e in conversations.read(id) if e.get("type") == "message"]
        assert entries[-1]["role"] == "system"

    def test_it_does_not_count_as_a_message(self, db):
        """"12 messages" should mean what a person would count."""
        from kith.infra.db import repositories as repo
        from kith.services import conversations

        started = conversations.start(db, "hello")
        id = str(started["id"])
        before = int(repo.conversations.get(db, id)["messages"])
        conversations.record(db, id, "system", "One of your reminders just fired.")
        assert int(repo.conversations.get(db, id)["messages"]) == before

    def test_the_model_still_sees_it_as_system(self, db):
        """Replayed, because the turn after a wake needs to know why the one before it happened —
        and replayed *as system*, because nobody said it."""
        from kith.services import conversations

        started = conversations.start(db, "hello")
        id = str(started["id"])
        conversations.record(db, id, "system", "One of your reminders just fired.")

        replayed = conversations.full_messages(id)
        assert replayed[-1]["role"] == "system"
        assert "reminders just fired" in replayed[-1]["content"]

    def test_the_interface_can_tell_it_apart(self, db):
        """`timeline` carries the role through rather than flattening it into `user`, which is
        what let the interface draw it in the person's bubble."""
        from kith.services import conversations

        started = conversations.start(db, "hello")
        id = str(started["id"])
        conversations.record(db, id, "system", "One of your reminders just fired.")

        assert conversations.timeline(id)[-1]["role"] == "system"

    def test_it_still_closes_the_turn_before_it(self, db):
        """The one thing recording it as `user` was really buying. A wake starts a turn, so
        anything left open by the turn before it is over."""
        from kith.services import conversations

        started = conversations.start(db, "hello")
        id = str(started["id"])
        conversations.record(db, id, "assistant", "done")
        conversations.record(db, id, "system", "One of your reminders just fired.")
        conversations.record(db, id, "assistant", "checked")

        roles = [m["role"] for m in conversations.full_messages(id)]
        assert roles[-3:] == ["assistant", "system", "assistant"]


class TestTwoTurnsDoNotRaceInOneConversation:
    """A reminder firing mid-turn used to start a second turn beside the first.

    Both ran at once, both wrote `said` events into one transcript, and they interleaved — so one
    reply appeared to split in half and then talk past itself about the same test suite. Observed
    on 2026-08-22 in conversation 20260819-193414906: "ok when suite is done please push and watch
    the CI", then forty seconds later a standing reminder, then two narratives braided together
    and two final answers back to back with nothing between them.

    It is also the race `queued-send.ts` warns about — two turns sharing one live-turn slot and
    one stop switch, where `live_turns.begin` replacing the record orphans the loser and nobody
    can stop it.
    """

    def test_a_wake_steers_a_running_turn_rather_than_starting_one(self, db, monkeypatch):
        from kith.api.routes import chat as route
        from kith.services import steering

        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        started: list = []
        monkeypatch.setattr(route, "begin_turn", lambda *a, **k: started.append(a) and None)

        turn = live_turns.begin("c-race")
        try:
            steering.forget("c-race")
            route.continue_conversation("c-race", "One of your reminders just fired.")

            assert started == [], "no second turn"
            assert steering.take("c-race") == "One of your reminders just fired."
        finally:
            steering.forget("c-race")
            live_turns.finish(turn)

    def test_a_wake_with_nothing_running_starts_a_turn_as_before(self, db, monkeypatch):
        from kith.api.routes import chat as route

        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        started: list = []
        monkeypatch.setattr(route, "begin_turn", lambda *a, **k: started.append(a) and None)
        monkeypatch.setattr(route.live_turns, "watch", lambda live: iter(()))

        route.continue_conversation("c-quiet", "One of your reminders just fired.")
        assert len(started) == 1

    def test_a_refused_steer_falls_through_to_a_turn(self, db, monkeypatch):
        """A reminder that says nothing is worse than one that says it a moment later."""
        from kith.api.routes import chat as route
        from kith.services import steering

        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        monkeypatch.setattr(steering, "steer", lambda cid, text: False)
        started: list = []
        monkeypatch.setattr(route, "begin_turn", lambda *a, **k: started.append(a) and None)
        monkeypatch.setattr(route.live_turns, "watch", lambda live: iter(()))

        turn = live_turns.begin("c-full")
        try:
            route.continue_conversation("c-full", "One of your reminders just fired.")
            assert len(started) == 1
        finally:
            live_turns.finish(turn)
