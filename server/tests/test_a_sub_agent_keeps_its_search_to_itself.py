"""Delegating is worth doing only if the searching really does stay behind.

The whole economics: twelve greps and eight reads to learn one fact is a couple of thousand
tokens of answer inside forty thousand tokens of looking, and once that looking is in the turn
it is in every later round of the turn. A sub-agent that handed back its transcript would be
strictly worse than not having one — the same tokens, plus a second model's worth of them.

So what comes back is one dict, and these are the four things about it that matter: it starts
from nothing, it is only the report, it says how much looking it took, and a worker that dies
half way still hands over what it had.
"""

from __future__ import annotations

from pathlib import Path

from kith.services import agent_loop
from kith.services.turn import frozen
from kith.tools import delegation


def _rounds(*script):
    """A model that says exactly this, one canned round per call.

    Each entry is `(text, tool_calls)`. The text arrives as deltas — which is what the report
    reads — and the round's `turn` event carries the calls.
    """
    calls: list[int] = []

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        calls.append(1)
        text, tool_calls = script[min(len(calls) - 1, len(script) - 1)]
        if text:
            yield {"type": "delta", "role": "text", "text": text}
        yield {"type": "turn", "content": text, "tool_calls": list(tool_calls), "stats": {}}

    return fake, calls


def _a_call(name: str):
    return {"function": {"name": name, "arguments": "{}"}}


class TestOnlyTheFindingsComeBack:
    def test_the_narration_of_the_search_is_dropped(self, db: Path, monkeypatch):
        """A round that calls tools usually opens with a line of narration. Concatenated with
        the answer, a report reads like a transcript of someone thinking — and the caller has
        to spend context reading it to find the one sentence it wanted."""
        fake, _ = _rounds(
            ("Let me look at the board first.", [_a_call("list_projects")]),
            ("Checking one more thing.", [_a_call("list_projects")]),
            ("Nothing is filed under that name.", []),
        )
        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        answer = delegation.delegate_subtask(db, {"objective": "is anything filed about ledgers"})

        assert answer["findings"] == "Nothing is filed under that name."
        assert "Let me look" not in answer["findings"]
        assert "Checking one more thing" not in answer["findings"]

    def test_it_says_how_much_looking_it_did(self, db: Path, monkeypatch):
        """A confident report off two calls and a confident report off eleven are not equally
        worth believing, and nothing in the prose tells them apart."""
        fake, _ = _rounds(
            ("", [_a_call("list_projects"), _a_call("list_projects")]),
            ("Found it.", []),
        )
        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        answer = delegation.delegate_subtask(db, {"objective": "look at something"})

        assert answer["looked_at"] == "list_projects x2"

    def test_a_worker_that_dies_still_hands_over_what_it_had(self, db: Path, monkeypatch):
        """Nine files read and the provider lost on the last round is still eight things worth
        having. Reporting only the error throws the whole delegation away."""

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            yield {"type": "error", "message": "Cloud model returned 401: unpaid"}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        answer = delegation.delegate_subtask(db, {"objective": "look at something"})

        assert "401" in answer["error"]
        assert "partial" in answer["note"]

    def test_an_empty_objective_is_refused_before_a_model_is_paid(self, db: Path, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("no model call should have been made")

        monkeypatch.setattr(agent_loop, "_stream_once", explode)

        answer = delegation.delegate_subtask(db, {"objective": "   "})

        assert "objective" in answer["error"]


class TestItStartsFromNothing:
    def test_the_scratchpad_is_the_brief_and_the_objective(self):
        """Two messages. The caller's context does not leak in — which is what makes this
        cheap — and so the objective has to stand on its own, which is what the tool
        description asks the caller for."""
        pad = delegation._scratchpad("PERSONA", "find the ledger")

        assert [m["role"] for m in pad] == ["system", "user"]
        assert pad[1]["content"] == "find the ledger"

    def test_the_persona_stays_at_the_head_of_the_system_prompt(self):
        """Not cosmetic — it is what the sub-agent costs.

        `llm/caching.stable_head` measures the cached prefix by how much of the system prompt
        starts with the persona, and that prefix is the ~8,700 tokens every request Kith makes
        already has warm. A worker whose brief went first would share none of it and write its
        own copy on every call.
        """
        pad = delegation._scratchpad("PERSONA", "find the ledger")

        assert pad[0]["content"].startswith("PERSONA")
        assert delegation.BRIEF in pad[0]["content"]


class TestItHasNoLandingPhase:
    def test_the_reserve_is_off_for_a_worker(self, db: Path):
        """Landing exists because a turn that gathers for forty rounds and gets cut off leaves
        nothing behind. A worker's output *is* its last message, and `_final_answer` already
        forces that — so the reserve buys nothing, and the directive it comes with tells the
        worker to write to a file, add a deliverable and tick off a checklist, all of which it
        deliberately cannot do."""
        from kith.config import default_config

        worker = frozen.begin(default_config(), db, "", 6, 0)
        ordinary = frozen.begin(default_config(), db, "", 6, None)

        assert worker.reserve == 0
        assert ordinary.reserve > 0, "every other caller still lands"
