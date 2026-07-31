"""OpenRouter stickiness: which upstream host a request is steered to.

OpenRouter spreads a model's traffic across providers and a prefix cache lives on one
host, so consecutive requests can each land somewhere cold. `session_id` is its own answer
to that — requests sharing an id are steered to the same upstream.

The only thing worth caching here is the persona: `caching.stable_head` cuts the system
message at the end of it, because everything after (the mode directive, the clock, his
mood, whatever memory is present) changes between requests. That prefix is byte-identical
between a chat turn and a tick — so a session whose chat and ticks land on different hosts
pays to warm the same bytes twice, which is what happened until the tick started naming
its conversation.
"""

from __future__ import annotations

import sys

import pytest

from kith.infra.db import repositories as repo
from kith.services import conversations


def runner_on(db, monkeypatch):
    module = sys.modules["kith.autonomy.runner"]
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    return module.AutonomyRunner()


@pytest.fixture
def captured(db, monkeypatch):
    """Run a tick and capture the conversation_id it handed to the agent loop."""
    module = sys.modules["kith.autonomy.runner"]
    seen: list[str] = []

    monkeypatch.setattr(module.repo.tasks, "tasks_awaiting_kith", lambda _p: [])
    monkeypatch.setattr(module.repo.reminders, "due_reminders", lambda _p, _n: [])
    monkeypatch.setattr(module.repo.schedules, "due_schedules", lambda _p, _n: [])
    monkeypatch.setattr(
        module, "stream_agent", lambda *a, **k: seen.append(k.get("conversation_id", "")) or iter(())
    )
    return seen


class TestAStepNamesItsSession:
    def test_a_working_session_steers_its_own_steps(self, db, monkeypatch, captured):
        runner = runner_on(db, monkeypatch)
        monkeypatch.setattr(runner, "_new_pending", lambda: [])
        session = conversations.start(db, "some work")["id"]
        repo.tasks.add_task(db, "a step")
        repo.conversations.set_working(db, session, True)

        runner._tick()

        # Not "" — that is the install-wide id, and it would put this session's ticks on a
        # different host from its own chat turns for no reason.
        assert captured == [session]

    def test_two_sessions_steer_separately(self, db, monkeypatch, captured):
        runner = runner_on(db, monkeypatch)
        monkeypatch.setattr(runner, "_new_pending", lambda: [])
        one = conversations.start(db, "one")["id"]
        two = conversations.start(db, "two")["id"]
        first = repo.projects.add_project(db, "First", "")
        second = repo.projects.add_project(db, "Second", "")
        repo.tasks.add_task(db, "first step", project_id=first["id"])
        repo.tasks.add_task(db, "second step", project_id=second["id"])
        repo.conversations.set_project(db, one, first["id"])
        repo.conversations.set_project(db, two, second["id"])
        repo.conversations.set_working(db, one, True)
        repo.conversations.set_working(db, two, True)

        runner._tick()
        runner._tick()

        assert captured == [one, two]

    def test_a_step_with_nobody_working_falls_back(self, db, monkeypatch, captured):
        """"Run" with no session is work belonging to no conversation, and the install-wide
        id is the honest answer for it rather than an arbitrary session's."""
        runner = runner_on(db, monkeypatch)
        monkeypatch.setattr(runner, "_new_pending", lambda: [])
        repo.tasks.add_task(db, "an errand")

        runner._tick()

        assert captured == [""]


class TestTheIdItself:
    def test_a_conversation_gets_its_own_and_keeps_it(self, db):
        one = conversations.start(db, "one")["id"]
        two = conversations.start(db, "two")["id"]
        first, second = conversations.session_id(db, one), conversations.session_id(db, two)

        assert first and second and first != second
        # Stable across reads, or it is not stickiness.
        assert conversations.session_id(db, one) == first

    def test_it_fits_openrouters_limit(self, db):
        # Documented maximum is 256 characters; ours is `kith-` plus 16 hex.
        assert len(conversations.session_id(db, conversations.start(db, "x")["id"])) <= 256

    def test_the_install_wide_id_is_persisted_not_per_process(self, config_db):
        """A restart that threw this away would throw away a warm cache with it."""
        from kith.infra.db import config_store
        from kith.llm import caching

        stored = config_store.load_settings(config_db)
        first = caching.session_id(
            stored, lambda fresh: config_store.update_settings(config_db, {caching.SESSION_KEY: fresh})
        )
        again = caching.session_id(config_store.load_settings(config_db), lambda _f: None)
        assert first == again


class TestWhatIsWorthCaching:
    def test_the_cacheable_prefix_is_the_persona_and_stops_there(self):
        """Which is why chat and a tick can share a host at all: both start with the same
        bytes and diverge immediately after."""
        from kith.llm import caching

        persona = "You are Kith."
        chat = f"{persona}\n\nYour person is here, talking to you."
        tick = f"{persona}\n\nYou are working on your own."

        assert caching.stable_head(chat, persona) == len(persona)
        assert caching.stable_head(tick, persona) == len(persona)
        assert chat[: caching.stable_head(chat, persona)] == tick[: caching.stable_head(tick, persona)]

    def test_a_prompt_that_is_not_his_is_cached_whole_rather_than_guessed_at(self):
        from kith.llm import caching

        assert caching.stable_head("Something else entirely", "You are Kith.") == 0
