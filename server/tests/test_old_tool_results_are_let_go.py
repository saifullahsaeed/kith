"""Tool output from turns ago stops being replayed as though it were still true.

Nothing let go of it. There were two mechanisms and neither was about age: the fold, at 80% of the
window, which summarises what was *said*; and a character budget for tool results — which is only
reached as a fallback after the fold has been exhausted (`agent_loop`: `if not folded:`). Below 80%
nothing was dropped at all, and every turn re-inflated the lot, because `full_messages` rebuilds the
conversation from the transcript and in-turn trimming dies with the turn.

Measured on the real board. One conversation held **4.42M characters** of tool output across 124
turns and sat at 59% of a million-token window — 222k tokens of tool results plus 184k of file
reads, ~39% of the window, with the 80,000-character budget never once applying. Keeping the last
two turns whole is 135k characters, 3% of it.

Two reasons this is by turn and not by size, and the second is the one that matters:

* **Cost.** It is the difference between 3% and 100% of the largest thing in the window.
* **Correctness.** `full_messages` says so in its own docstring: "a tool result from hours ago is
  replayed as though it were still true, which it might not be." A file read forty turns ago and
  edited since is not context, it is misinformation. A pointer to the file as it stands now is
  strictly better than a stale copy of it.

Trimmed here, as the history is assembled, rather than in the loop under pressure — which is also
what keeps the prompt cache. A turn boundary is a fixed thing, so a result stubbed last turn is
stubbed identically this turn and the prefix still matches. Trimming under pressure rewrites the
middle of the array and moves the eviction point every round, which is the older mistake this
codebase has already paid for twice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith.services import conversations

BIG = "x" * 20_000


@pytest.fixture(autouse=True)
def transcripts(tmp_path, monkeypatch):
    """Transcripts under a temp root. `record_event` writes to the workspace's internal folder,
    which the autouse conftest fixtures redirect for the data dir but not for `configured_root`."""
    from kith.infra import workspace

    monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
    return tmp_path


def _a_conversation(db: Path, turns: int) -> str:
    """`turns` user messages, each followed by one big tool result."""
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


class TestOlderTurnsAreStubbed:
    def test_the_newest_turns_survive_whole(self, db: Path, transcripts):
        conversation_id = _a_conversation(db, 6)
        contents = _tool_contents(conversation_id)
        # The default is two turns, so the last two results are untouched.
        assert BIG in contents[-1]
        assert BIG in contents[-2]

    def test_everything_older_is_trimmed(self, db: Path, transcripts):
        conversation_id = _a_conversation(db, 6)
        for content in _tool_contents(conversation_id)[:-2]:
            assert BIG not in content
            assert len(content) < 5_000

    def test_a_stub_still_says_what_it_was(self, db: Path, transcripts):
        """A hole is worse than a summary: he has to be able to tell whether he already looked at
        something, or he looks again — which is the very cost this is meant to avoid."""
        conversation_id = _a_conversation(db, 6)
        oldest = _tool_contents(conversation_id)[0]
        assert "0-" in oldest, "the head of the result is kept"
        assert "read_file" in oldest or "f0.py" in oldest, "and what produced it"

    def test_it_says_the_trim_was_about_age(self, db: Path, transcripts):
        conversation_id = _a_conversation(db, 6)
        assert "turn" in _tool_contents(conversation_id)[0].lower()

    def test_a_short_conversation_is_untouched(self, db: Path, transcripts):
        conversation_id = _a_conversation(db, 2)
        assert all(BIG in content for content in _tool_contents(conversation_id))

    def test_a_small_result_is_never_stubbed(self, db: Path, transcripts):
        """Trimming 200 characters buys nothing and loses something."""
        conversation_id = conversations.start(db, "testing")["id"]
        for turn in range(4):
            conversations.record(db, conversation_id, "user", f"turn {turn}")
            conversations.record_event(
                conversation_id,
                "tool_call",
                {"id": f"c{turn}", "name": "shell", "arguments": {"command": "ls"}},
            )
            conversations.record_event(
                conversation_id, "tool_result", {"id": f"c{turn}", "name": "shell", "result": "exit 0"}
            )
        assert all("exit 0" in content for content in _tool_contents(conversation_id))


class TestTheSettingDecidesHowMany:
    def test_raising_it_keeps_more(self, db: Path, transcripts, monkeypatch):
        from kith.services import tuning

        real = tuning.value
        monkeypatch.setattr(tuning, "value", lambda key: 5 if key == "keep_tool_turns" else real(key))
        conversation_id = _a_conversation(db, 8)
        whole = [content for content in _tool_contents(conversation_id) if BIG in content]
        assert len(whole) == 5

    def test_zero_means_keep_everything(self, db: Path, transcripts, monkeypatch):
        """An escape hatch that has to work: somebody with a 2M-token window and a big bill for
        re-reads should be able to turn this off without editing code."""
        from kith.services import tuning

        real = tuning.value
        monkeypatch.setattr(tuning, "value", lambda key: 0 if key == "keep_tool_turns" else real(key))
        conversation_id = _a_conversation(db, 6)
        assert all(BIG in content for content in _tool_contents(conversation_id))


class TestThePairingStillHolds:
    def test_every_tool_message_still_follows_its_call(self, db: Path, transcripts):
        """`_to_openai` pairs a tool message to the assistant message immediately before it, by
        position. Trimming content must not disturb that, or a provider refuses the request."""
        conversation_id = _a_conversation(db, 6)
        messages = conversations.full_messages(conversation_id)
        for i, message in enumerate(messages):
            if message.get("role") == "tool":
                assert messages[i - 1].get("tool_calls"), f"message {i} lost its call"

    def test_the_stub_is_still_valid_json(self, db: Path, transcripts):
        """The content is a JSON-encoded result; whatever replaces it has to be too."""
        conversation_id = _a_conversation(db, 6)
        for content in _tool_contents(conversation_id):
            json.loads(content)


class TestItIsStableBetweenTurns:
    def test_the_same_history_trims_identically(self, db: Path, transcripts):
        """The prompt cache lives on this. A turn boundary is fixed, so a result stubbed last turn
        must be stubbed byte-identically this turn — otherwise the prefix moves and the whole
        conversation is re-billed uncached, which is the mistake `_compact_tool_history` made."""
        conversation_id = _a_conversation(db, 6)
        assert _tool_contents(conversation_id) == _tool_contents(conversation_id)

    def test_a_new_turn_does_not_disturb_the_older_stubs(self, db: Path, transcripts):
        conversation_id = _a_conversation(db, 6)
        before = _tool_contents(conversation_id)[:3]
        conversations.record(db, conversation_id, "user", "one more")
        after = _tool_contents(conversation_id)[:3]
        assert before == after
