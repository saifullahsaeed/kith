"""Recording, delivering, ticking off, handing back — landing is not deciding what to do
next, and reasoning is billed as output tokens whether or not any of it is shown.

`landing_effort` lowers the dial for exactly the rounds the landing reserve already set
aside, and only those — every round before landing keeps whatever effort the turn was
actually configured with.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from kith import tools
from kith.config import default_config
from kith.services import agent_loop, tuning


def _drive(monkeypatch, db: Path, config, max_rounds: int) -> list[str]:
    """Run `_run_turn` for `max_rounds` rounds, recording the effort each request actually
    carried. The first call reaches for a real, harmless, argument-free tool so the loop has
    something to keep going on; every call after that ends the turn."""
    seen: list[str] = []
    calls = 0

    def fake_stream(convo, round_config, host, tools=None, tool_choice="auto", routing=None):
        nonlocal calls
        seen.append(round_config.effort)
        calls += 1
        if calls == 1:
            yield {
                "type": "turn",
                "content": "",
                "tool_calls": [{"function": {"name": "list_tasks", "arguments": {}}}],
                "stats": {},
            }
        else:
            yield {"type": "turn", "content": "done", "tool_calls": [], "stats": {}}

    monkeypatch.setattr(agent_loop, "_stream_once", fake_stream)
    list(
        agent_loop._run_turn(
            [], config, "host", db, tools.host(db, language_server=False), max_rounds=max_rounds
        )
    )
    return seen


class TestOnlyLandingRoundsAreLightened:
    def test_an_earlier_round_keeps_the_turns_own_effort(self, db: Path, monkeypatch):
        tuning.apply({"landing_reserve": 1, "landing_effort": "low"})
        config = replace(default_config(), effort="high")

        seen = _drive(monkeypatch, db, config, max_rounds=2)

        assert seen[0] == "high", "the first, non-landing round was overridden"

    def test_the_landing_round_is_lightened(self, db: Path, monkeypatch):
        tuning.apply({"landing_reserve": 1, "landing_effort": "low"})
        config = replace(default_config(), effort="high")

        seen = _drive(monkeypatch, db, config, max_rounds=2)

        assert seen[-1] == "low"

    def test_a_blank_override_changes_nothing(self, db: Path, monkeypatch):
        """Blank means leave it to the model, landing included — not force it to whatever
        `effort=''` happens to mean downstream."""
        tuning.apply({"landing_reserve": 1, "landing_effort": ""})
        config = replace(default_config(), effort="high")

        seen = _drive(monkeypatch, db, config, max_rounds=2)

        assert seen == ["high", "high"]

    def test_the_knob_is_read_live_not_captured_at_import(self, db: Path, monkeypatch):
        tuning.apply({"landing_reserve": 1, "landing_effort": "none"})
        config = replace(default_config(), effort="medium")

        seen = _drive(monkeypatch, db, config, max_rounds=2)

        assert seen == ["medium", "none"]
