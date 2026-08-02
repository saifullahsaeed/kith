"""A working session that spends past its ceiling stops itself, once, with a durable note.

This is the seatbelt: the process-wide meter in agent_loop counts everything but is keyed to
nothing, so a single "keep working" session could run through tens of millions of tokens with
no cumulative ceiling. Here the runner keeps a per-session tally and rests the session — using
the same set_working(False) baton that rest()/idle already use — the moment it crosses the cap.

Every charge below passes `cost_usd=0.0`, which is what a provider that reports no price
looks like, and which is the only case the *token* ceiling still governs. The ceiling that
matters is money — see `test_the_budget_is_money.py` — because charging the whole prompt
including cached re-reads ran the meter four times too fast and stopped a session that had
spent twenty-six cents.

The token cap's floor is 100k (a single heavy tick can be ~700k), so these tests work in
hundreds of thousands, not the toy numbers the clamp would swallow.
"""

import sys

from kith.infra.db import repositories as repo
from kith.services import conversations, tuning


def _runner_on(db, monkeypatch):
    """A runner pointed at a temp database. See test_a_session_that_keeps_working."""
    module = sys.modules["kith.autonomy.runner"]
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    return module.AutonomyRunner()


def test_crossing_the_token_cap_rests_the_session_and_leaves_a_note(db, monkeypatch):
    tuning.apply({"session_token_cap": 100_000})  # the floor; the charges below cross it
    conv_id = conversations.start(db, "build a thing")["id"]
    repo.conversations.set_working(db, conv_id, True)

    r = _runner_on(db, monkeypatch)
    r._charge_session(conv_id, 60_000, 0.0)
    assert repo.conversations.is_working(db, conv_id)  # 60k < 100k → still working
    r._charge_session(conv_id, 60_000, 0.0)  # 120k ≥ 100k → over budget
    assert not repo.conversations.is_working(db, conv_id)

    notes = repo.messages.list_messages(db, limit=5)
    assert any("budget" in m["body"].lower() for m in notes)


def test_the_meter_only_trips_once(db, monkeypatch):
    tuning.apply({"session_token_cap": 100_000})
    conv_id = conversations.start(db, "build a thing")["id"]
    repo.conversations.set_working(db, conv_id, True)

    r = _runner_on(db, monkeypatch)
    r._charge_session(conv_id, 200_000, 0.0)  # blows straight past
    r._charge_session(conv_id, 200_000, 0.0)  # a second charge must not post a second note
    stuck = [m for m in repo.messages.list_messages(db, limit=10) if "budget" in m["body"].lower()]
    assert len(stuck) == 1


def test_re_entering_keep_working_resets_the_meter(db, monkeypatch):
    tuning.apply({"session_token_cap": 100_000})
    conv_id = conversations.start(db, "build a thing")["id"]
    r = _runner_on(db, monkeypatch)
    r.keep_working(conv_id)
    r._charge_session(conv_id, 90_000, 0.0)
    r.keep_working(conv_id)  # explicit restart clears the meter (and the capped flag)
    assert r._session_tokens.get(conv_id, 0) == 0


def test_an_empty_conversation_id_is_a_no_op(db, monkeypatch):
    tuning.apply({"session_token_cap": 100_000})
    r = _runner_on(db, monkeypatch)
    r._charge_session("", 10_000_000, 0.0)  # a step run with nobody working must not explode
