"""The head of every request is the same bytes at any hour and after anything he does.

The prompt cache is prefix-matched: one changed byte in the head re-bills everything behind it.
Measured: 89% of prompt tokens cached overall, 98.5% within a turn on DeepSeek. And the explicit
breakpoints in `llm/caching.py` only act for anthropic, qwen and google — 0.5% of traffic — so on
everything else the cache holds for one reason only: nothing volatile has ever been put in the
persona head (`config.system`, which is `persona.load_persona()` + `skills.index()`) or in the
tool block (`tools.host`). That was a convention written in docstrings, and nothing failed when
it was broken. This fails.

Two ways a head goes volatile, and each gets a test: something reads the clock (a date, "3
minutes ago"), or something reads his state (a count of memories, the open tasks, a reminder).
Both are provoked here rather than hoped against — the clock is moved five years and every piece
of state the present-state block reports is changed in between — and the head must not notice.

Deliberately silent about *where* the per-turn present-state block goes. That block is supposed
to change every turn; the only claim here is that it is not in the head.
"""

from __future__ import annotations

import contextlib
import json
import sys
from datetime import UTC, date, datetime

import pytest

import kith.settings
from kith import tools
from kith.config import default_config
from kith.infra.db import repositories as repo
from kith.kernel import clock
from kith.services.turn.prompt import _build_messages

WINTER_MORNING = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
SUMMER_MIDNIGHT = datetime(2031, 7, 19, 23, 59, tzinfo=UTC)


@contextlib.contextmanager
def _the_clock_reads(moment: datetime):
    """Every clock Kith reads, set to ``moment``: the kernel's, and any module's own `datetime`.

    The kernel clock is the sanctioned one, but a head that went volatile would most likely do it
    with a bare `datetime.now()` somewhere, so each `kith` module holding the `datetime` or
    `date` class gets a frozen one too — the same every-holder patch `conftest` does for the
    database paths, for the same reason.
    """

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return moment.replace(tzinfo=None)

        @classmethod
        def today(cls):
            return cls.now()

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return moment.date()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clock, "now_utc", lambda: moment)
        for name, module in list(sys.modules.items()):
            if not name.startswith("kith"):
                continue
            if getattr(module, "datetime", None) is datetime:
                patch.setattr(module, "datetime", Frozen)
            if getattr(module, "date", None) is date:
                patch.setattr(module, "date", FrozenDate)
        yield


def _he_gets_on_with_things() -> None:
    """One of everything the present-state block reports, added between two builds of the head."""
    db = kith.settings.AGENT_DB_PATH
    project = repo.projects.add_project(db, "Launch", "the thing he is working on")
    repo.memories.add_memory(db, "His person prefers terse answers.", level="core")
    repo.memories.add_memory(db, "The launch is on Friday.", project_id=project["id"])
    repo.tasks.add_task(db, "Write the release notes")
    repo.reminders.add_reminder(db, "2031-07-20T08:00:00+00:00", "Ship it")
    repo.journal.add_journal(db, "Started on the release notes.")


class TestThePersonaHeadHoldsStill:
    def test_the_head_is_the_same_at_any_hour(self):
        with _the_clock_reads(WINTER_MORNING):
            morning = default_config().system
        with _the_clock_reads(SUMMER_MIDNIGHT):
            midnight = default_config().system
        assert morning, "the head is empty, so this proves nothing"
        assert morning == midnight, "something in the persona or skill index reads the clock"

    def test_the_head_is_the_same_after_anything_he_does(self):
        with _the_clock_reads(WINTER_MORNING):
            before = default_config().system
        _he_gets_on_with_things()
        with _the_clock_reads(SUMMER_MIDNIGHT):
            after = default_config().system
        assert before == after, "a memory, a task or a reminder reached the cached head"

    def test_the_message_that_carries_it_holds_still_too(self):
        """What is actually sent first: the head plus the chat directive. Built through the real
        prompt builder, so the present-state block is assembled — clock and all — and must land
        somewhere other than here."""
        with _the_clock_reads(WINTER_MORNING):
            first = _build_messages([{"role": "user", "content": "morning"}], default_config(), "")[0]
        _he_gets_on_with_things()
        with _the_clock_reads(SUMMER_MIDNIGHT):
            later = _build_messages([{"role": "user", "content": "still there?"}], default_config(), "")[0]
        assert first["role"] == "system"
        assert json.dumps(first, sort_keys=True) == json.dumps(later, sort_keys=True)


class TestTheToolBlockHoldsStill:
    def test_two_turns_with_the_same_settings_send_the_same_tool_bytes(self):
        """Not just the same names in the same order — the same bytes. A description that quoted
        a count or a date would keep every name and still discard the cache."""
        db = kith.settings.AGENT_DB_PATH
        with _the_clock_reads(WINTER_MORNING):
            first = json.dumps(tools.host(db).schemas())
        _he_gets_on_with_things()
        with _the_clock_reads(SUMMER_MIDNIGHT):
            second = json.dumps(tools.host(db).schemas())
        assert json.loads(first), "no tools, so this proves nothing"
        assert first == second, "the tool block changed between two turns with nothing reconfigured"
