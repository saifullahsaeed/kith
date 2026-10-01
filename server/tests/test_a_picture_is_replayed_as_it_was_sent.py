"""A picture a turn looked at is sent again, byte for byte, by every turn after it.

The transcript dropped the bytes and kept the path, so the replay had the tool result and not the
picture message that followed it. The prompt changed right there and the cache stopped: on
2026-10-01 three of nine messages in one conversation re-billed 6–10k tokens each, every time at
the first screenshot of the turn before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.api.routes.chat import _readable
from kith.services import conversations

URI = "data:image/png;base64," + "iVBORw0KGgo" * 50


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    from kith import settings
    from kith.infra import workspace

    monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")


def _looked(db: Path, uri: str = URI) -> str:
    conversation_id = conversations.start(db, "testing")["id"]
    conversations.record(db, conversation_id, "user", "look")
    call = {"type": "tool_call", "id": "c1", "name": "read_file", "arguments": {"paths": ["shot.png"]}}
    conversations.record_event(conversation_id, "tool_call", call)
    result = {"type": "tool_result", "id": "c1", "name": "read_file", "result": {"ok": True, "result": {"path": "shot.png", "image": uri}}}
    conversations.record_event(conversation_id, "tool_result", _readable(result))
    conversations.record(db, conversation_id, "assistant", "seen")
    conversations.record(db, conversation_id, "user", "next")
    return conversation_id


def test_the_picture_comes_back_after_its_result(db: Path):
    messages = conversations.full_messages(_looked(db))
    tool = next(i for i, m in enumerate(messages) if m.get("role") == "tool")
    picture = messages[tool + 1]
    assert picture["content"] == conversations.picture_message([{"path": "shot.png", "image": URI}])["content"]


def test_the_transcript_still_holds_no_bytes(db: Path):
    conversation_id = _looked(db)
    assert all("base64" not in str(e) for e in conversations.read(conversation_id))


def test_a_replayed_picture_is_not_the_person_speaking(db: Path):
    from kith.services.history import _theirs

    messages = conversations.full_messages(_looked(db))
    assert [m["content"] for m in messages if _theirs(m)] == ["look", "next"]
