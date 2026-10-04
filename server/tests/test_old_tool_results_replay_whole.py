"""Old tool output is replayed exactly as it was sent, until the fold.

It used to be stubbed one turn per message. Each stub rewrote the history at that turn, and the
host's cache only hits at the length of a request it has already been sent — so every message
re-billed the whole conversation past the ~16k-token head: 86k of 102k fresh for a one-line reply
(2026-10-01). Claude Code's answer, and now this one: leave history alone, and let the fold at
`fold_at_tokens` be the single moment it changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.services import conversations

BIG = "x" * 20_000


@pytest.fixture(autouse=True)
def transcripts(tmp_path, monkeypatch):
    from kith.infra import workspace

    monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
    return tmp_path


def _a_conversation(db: Path, turns: int) -> str:
    conversation_id = conversations.start(db, "testing")["id"]
    for turn in range(turns):
        conversations.record(db, conversation_id, "user", f"turn {turn}")
        conversations.record_event(
            conversation_id,
            "tool_call",
            {"id": f"c{turn}", "name": "read_file", "arguments": {"path": f"f{turn}.py"}},
        )
        conversations.record_event(
            conversation_id, "tool_result", {"id": f"c{turn}", "name": "read_file", "result": f"{turn}-{BIG}"}
        )
    return conversation_id


def _tool_contents(conversation_id: str) -> list[str]:
    return [m["content"] for m in conversations.full_messages(conversation_id) if m.get("role") == "tool"]


def test_old_results_stay_whole(db: Path):
    assert all(BIG in content for content in _tool_contents(_a_conversation(db, 8)))


def test_a_new_message_leaves_the_history_already_sent_unchanged(db: Path):
    conversation_id = _a_conversation(db, 6)
    before = conversations.full_messages(conversation_id)
    conversations.record(db, conversation_id, "user", "one more")
    assert conversations.full_messages(conversation_id)[: len(before)] == before
