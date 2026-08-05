"""The context reading has to survive the trip to the interface, and to a reopened conversation.

A meter nobody sees is accounting. Two paths carry it, and they have to agree — a resumed
conversation should look like the one you had, which is the rule the token counts already follow
(see `_stored_turns` in `workspace.tsx`, and the note about a reopened conversation becoming a
column of six-figure numbers).

The interesting decision here is what is *not* recorded. A `context` event lands every round,
and each one is a reading of the window rather than something that happened — so only the last
is still true. Writing all forty would put a categorised breakdown in the transcript on every
round to say what the final one already says, which is how a 2MB transcript becomes a 6MB one.
So the recorder holds the latest and writes it once, at the end, including when the turn errored
or was stopped: a turn that died is exactly the one whose context you want to look at.
"""

from __future__ import annotations

import json
from pathlib import Path

from kith.api.routes import chat as chat_route
from kith.services import conversations

LEDGER = {
    "window": 1_050_000,
    "used": 213_700,
    "free": 836_300,
    "share": 0.2035,
    "lines": [
        {"key": "messages", "label": "Messages", "tokens": 178_800, "share": 0.1703},
        {"key": "built_in_tools", "label": "System tools", "tokens": 14_400, "share": 0.0137},
    ],
}


def _the_recorder_class():
    """The transcript recorder, whatever it is called.

    Found by behaviour rather than by name so a rename does not silently skip this file — a test
    that quietly stops running is worse than one that fails.
    """
    for value in vars(chat_route).values():
        if isinstance(value, type) and hasattr(value, "saw") and hasattr(value, "finish"):
            return value
    raise AssertionError("no transcript recorder in kith.api.routes.chat")


class TestWhatGetsWrittenDown:
    def test_only_the_last_reading_is_recorded(self, db: Path, tmp_path, monkeypatch):
        monkeypatch.setattr(conversations, "directory", lambda: tmp_path)
        recorder = _the_recorder_class()("c1")
        for used in (10_000, 90_000, 213_700):
            recorder.saw({"type": "context", "context": {**LEDGER, "used": used}})
        recorder.finish()

        written = [
            json.loads(line)
            for line in (tmp_path / "c1.jsonl").read_text().splitlines()
            if line.strip()
        ]
        readings = [r for r in written if r.get("type") == "context"]
        assert len(readings) == 1, f"{len(readings)} readings written; forty rounds would be forty"
        assert readings[0]["context"]["used"] == 213_700

    def test_a_turn_that_never_folded_says_so(self, db: Path, tmp_path, monkeypatch):
        monkeypatch.setattr(conversations, "directory", lambda: tmp_path)
        recorder = _the_recorder_class()("c2")
        recorder.saw({"type": "context", "context": LEDGER})
        recorder.finish()
        reading = next(
            json.loads(line)
            for line in (tmp_path / "c2.jsonl").read_text().splitlines()
            if line.strip() and json.loads(line).get("type") == "context"
        )
        assert reading["folded"] is False

    def test_folding_is_remembered(self, db: Path, tmp_path, monkeypatch):
        monkeypatch.setattr(conversations, "directory", lambda: tmp_path)
        recorder = _the_recorder_class()("c3")
        recorder.saw({"type": "context", "context": LEDGER})
        recorder.saw({"type": "compacting", "used": 900_000, "window": 1_050_000})
        recorder.finish()
        reading = next(
            json.loads(line)
            for line in (tmp_path / "c3.jsonl").read_text().splitlines()
            if line.strip() and json.loads(line).get("type") == "context"
        )
        assert reading["folded"] is True

    def test_a_turn_that_errored_still_records_its_context(self, db: Path, tmp_path, monkeypatch):
        """The one you most want to look at afterwards."""
        monkeypatch.setattr(conversations, "directory", lambda: tmp_path)
        recorder = _the_recorder_class()("c4")
        recorder.saw({"type": "context", "context": LEDGER})
        recorder.finish(error="cloud model returned 400")
        kinds = [
            json.loads(line).get("type")
            for line in (tmp_path / "c4.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert "context" in kinds
        assert "error" in kinds

    def test_a_turn_with_no_reading_writes_nothing(self, db: Path, tmp_path, monkeypatch):
        """An unknown window, or a turn that died before its first round."""
        monkeypatch.setattr(conversations, "directory", lambda: tmp_path)
        recorder = _the_recorder_class()("c5")
        recorder.finish()
        path = tmp_path / "c5.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        assert not [line for line in lines if line.strip() and json.loads(line).get("type") == "context"]


class TestReopeningTheConversation:
    def test_the_reading_comes_back_as_a_part(self, db: Path, tmp_path, monkeypatch):
        monkeypatch.setattr(conversations, "directory", lambda: tmp_path)
        recorder = _the_recorder_class()("c6")
        recorder.saw({"type": "delta", "role": "text", "text": "had a look"})
        recorder.saw({"type": "context", "context": LEDGER})
        recorder.saw({"type": "compacting", "used": 900_000, "window": 1_050_000})
        recorder.finish()

        parts = [part for turn in conversations.timeline("c6") for part in turn["parts"]]
        reading = next(part for part in parts if part["kind"] == "context")
        assert reading["context"]["used"] == 213_700
        assert reading["folded"] is True
