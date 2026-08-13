"""Conversations, and the promise that nothing is lost.

The transcript is append-only plain text because that is what makes "I don't want to lose
any info" a property of the format rather than a hope. These tests are mostly about the
file: that it survives a mangled line, that the index can be wrong without the words
being wrong, and that removing a conversation from a list does not delete an afternoon.
"""

from __future__ import annotations

import json

import pytest

from kith.infra.db import repositories as repo
from kith.services import conversations


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    """Transcripts under a temp folder, not the real ones.

    DATA_DIR as well as WORKSPACE_DIR, because his records moved out of the workspace and
    next to the databases — `.kith` inside a folder now means *that project's* memory, so it
    could not also mean the transcript of every conversation he has ever had. Patching only
    the workspace left this reading the real transcript folder, and the test that noticed was
    the one asserting a count: it saw 72 files instead of 2.
    """
    from kith.infra import workspace as module

    for target in ("kith.settings.WORKSPACE_DIR", "kith.settings.DATA_DIR"):
        monkeypatch.setattr(target, str(tmp_path))
    monkeypatch.setattr(module.paths.settings, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(module.paths.settings, "DATA_DIR", str(tmp_path))
    # The migration from the old location runs once per process; reset it so each test
    # starts with nothing carried in from a previous one's temp folder.
    monkeypatch.setattr(module.paths, "_migrated", False, raising=False)
    yield tmp_path


class TestStarting:
    def test_a_conversation_gets_an_id_a_session_and_a_file(self, db):
        opened = conversations.start(db, "Hello there")
        assert opened["id"]
        assert opened["sessionId"].startswith("kith-")
        assert conversations.transcript_path(opened["id"]).exists()

    def test_the_id_sorts_chronologically_by_name(self, db):
        """So the folder reads in order in Finder, not just through the app."""
        first = conversations.start(db, "one")["id"]
        second = conversations.start(db, "two")["id"]
        assert sorted([second, first]) == [first, second]

    def test_the_session_id_is_per_conversation(self, db):
        """One id per install had unrelated chats fighting over the same warm cache."""
        a = conversations.start(db, "a")
        b = conversations.start(db, "b")
        assert a["sessionId"] != b["sessionId"]

    def test_the_title_comes_from_the_first_thing_said(self, db):
        assert conversations.start(db, "Fix the invoice script")["title"] == "Fix the invoice script"

    def test_a_long_first_message_is_cut_on_a_word(self, db):
        long = "Please look into the quarterly numbers and tell me what changed since June"
        title = conversations.title_from(long)
        assert len(title) <= conversations.TITLE_CHARS + 1
        assert not title.rstrip("…").endswith(" ")


class TestRecording:
    def test_messages_land_in_the_transcript_and_come_back(self, db):
        opened = conversations.start(db)
        conversations.record(db, opened["id"], "user", "what is 2+2")
        conversations.record(db, opened["id"], "assistant", "4")
        assert conversations.messages(opened["id"]) == [
            {"role": "user", "content": "what is 2+2"},
            {"role": "assistant", "content": "4"},
        ]

    def test_a_placeholder_title_is_replaced_by_the_first_real_message(self, db):
        opened = conversations.start(db)
        assert opened["title"] == "New conversation"
        conversations.record(db, opened["id"], "user", "Draft the update")
        assert conversations.get(db, opened["id"])["title"] == "Draft the update"

    def test_a_later_message_does_not_rename_it(self, db):
        opened = conversations.start(db, "First subject")
        conversations.record(db, opened["id"], "user", "something else entirely")
        assert conversations.get(db, opened["id"])["title"] == "First subject"

    def test_events_are_kept_but_not_counted_as_messages(self, db):
        """ "12 messages" should mean what a person would count."""
        opened = conversations.start(db)
        conversations.record(db, opened["id"], "user", "go")
        conversations.record_event(opened["id"], "tool_call", {"name": "shell"})
        assert conversations.get(db, opened["id"])["messages"] == 1
        assert any(entry["type"] == "tool_call" for entry in conversations.read(opened["id"]))

    def test_tool_results_are_not_replayed_as_conversation(self, db):
        """A tool result from an hour ago is not a fact about now."""
        opened = conversations.start(db)
        conversations.record(db, opened["id"], "user", "go")
        conversations.record_event(opened["id"], "tool_result", {"result": "stale"})
        assert [m["content"] for m in conversations.messages(opened["id"])] == ["go"]

    def test_recording_without_an_id_is_a_no_op_not_a_crash(self, db):
        conversations.record(db, "", "user", "nowhere")  # must not raise


class TestFullMessagesReplaysToolHistoryToo:
    """`full_messages` is what a real turn replays — `messages` is what the client shows
    and what a brand-new conversation starts from. They have to agree with each other for
    everything that isn't a tool call, and only diverge for exactly that."""

    def test_a_conversation_with_no_tool_calls_matches_messages(self, db):
        opened = conversations.start(db)
        conversations.record(db, opened["id"], "user", "hi")
        conversations.record(db, opened["id"], "assistant", "hello")
        assert conversations.full_messages(opened["id"]) == conversations.messages(opened["id"])

    def test_a_call_and_its_result_become_an_assistant_and_a_tool_message(self, db):
        opened = conversations.start(db, "go")
        conversations.record(db, opened["id"], "user", "read config.py")
        conversations.record_event(
            opened["id"], "tool_call", {"id": "c0", "name": "read_file", "arguments": {"path": "config.py"}}
        )
        conversations.record_event(
            opened["id"],
            "tool_result",
            {"id": "c0", "name": "read_file", "result": {"ok": True, "result": "DEBUG=True"}},
        )
        conversations.record(db, opened["id"], "assistant", "It's set to debug mode.")

        assert conversations.full_messages(opened["id"]) == [
            {"role": "user", "content": "read config.py"},
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "config.py"}}}],
            },
            {
                "role": "tool",
                "tool_name": "read_file",
                "content": json.dumps({"ok": True, "result": "DEBUG=True"}),
            },
            {"role": "assistant", "content": "It's set to debug mode."},
        ]

    def test_calls_batched_before_any_result_still_pair_one_to_one_in_order(self, db):
        """Parallel-safe tools fire every `tool_call` before any `tool_result` lands — the
        reconstruction still pairs by id, one call to its own result, in the order the
        results actually arrived."""
        opened = conversations.start(db, "go")
        conversations.record(db, opened["id"], "user", "search two things")
        for call_id, query in (("c0", "roadmap"), ("c1", "pricing")):
            conversations.record_event(
                opened["id"],
                "tool_call",
                {"id": call_id, "name": "web_search", "arguments": {"query": query}},
            )
        for call_id, query in (("c0", "roadmap"), ("c1", "pricing")):
            conversations.record_event(
                opened["id"], "tool_result", {"id": call_id, "name": "web_search", "result": f"about {query}"}
            )

        queries = [
            m["tool_calls"][0]["function"]["arguments"]["query"]
            for m in conversations.full_messages(opened["id"])
            if m.get("tool_calls")
        ]
        assert queries == ["roadmap", "pricing"]

    def test_a_repeated_identical_read_is_not_collapsed(self, db):
        """Dedup is a live, in-memory trick that never touches the transcript — replaying
        gets the real content both times, not a pointer to itself."""
        opened = conversations.start(db, "go")
        conversations.record(db, opened["id"], "user", "read it twice")
        for call_id in ("c0", "c1"):
            conversations.record_event(
                opened["id"], "tool_call", {"id": call_id, "name": "read_file", "arguments": {"path": "a.py"}}
            )
            conversations.record_event(
                opened["id"], "tool_result", {"id": call_id, "name": "read_file", "result": "same content"}
            )

        tool_messages = [m for m in conversations.full_messages(opened["id"]) if m.get("role") == "tool"]
        assert len(tool_messages) == 2
        assert all(m["content"] == json.dumps("same content") for m in tool_messages)

    def test_a_call_with_no_recorded_result_is_dropped(self, db):
        """The turn crashed mid-call. Emitting it with nothing to pair it to would make a
        provider reject the whole replayed request."""
        opened = conversations.start(db, "go")
        conversations.record(db, opened["id"], "user", "run it")
        conversations.record_event(
            opened["id"], "tool_call", {"id": "c0", "name": "shell", "arguments": {"command": "sleep 999"}}
        )

        assert conversations.full_messages(opened["id"]) == [{"role": "user", "content": "run it"}]

    def test_an_id_reused_by_a_later_turn_does_not_pair_with_the_earlier_orphan(self, db):
        """The agent loop's own call ids reset to c0 at the start of every real turn, so a
        crashed turn's orphaned "c0" must not be mistaken for a later turn's "c0"."""
        opened = conversations.start(db, "go")
        conversations.record(db, opened["id"], "user", "first — will crash")
        conversations.record_event(
            opened["id"], "tool_call", {"id": "c0", "name": "shell", "arguments": {"command": "one"}}
        )
        conversations.record(db, opened["id"], "user", "second — reuses c0")
        conversations.record_event(
            opened["id"], "tool_call", {"id": "c0", "name": "shell", "arguments": {"command": "two"}}
        )
        conversations.record_event(
            opened["id"], "tool_result", {"id": "c0", "name": "shell", "result": "two ran"}
        )

        pairs = [m for m in conversations.full_messages(opened["id"]) if m.get("tool_calls")]
        assert len(pairs) == 1
        assert pairs[0]["tool_calls"][0]["function"]["arguments"] == {"command": "two"}

    def test_reasoning_and_stats_are_not_replayed(self, db):
        opened = conversations.start(db, "go")
        conversations.record(db, opened["id"], "user", "go")
        conversations.record_event(opened["id"], "reasoning", {"text": "thinking..."})
        conversations.record_event(opened["id"], "stats", {"stats": {"promptTokens": 5}})
        conversations.record(db, opened["id"], "assistant", "done")

        assert conversations.full_messages(opened["id"]) == [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "done"},
        ]


class TestTheFileSurvivesThings:
    def test_a_mangled_line_costs_that_line_only(self, db):
        """What a crash mid-append looks like. The whole reason this is JSONL."""
        opened = conversations.start(db)
        conversations.record(db, opened["id"], "user", "before")
        with conversations.transcript_path(opened["id"]).open("a") as handle:
            handle.write('{"type": "message", "role": "user", "content": "trunca\n')
        conversations.record(db, opened["id"], "assistant", "after")
        contents = [m["content"] for m in conversations.messages(opened["id"])]
        assert contents == ["before", "after"]

    def test_reading_a_conversation_with_no_file_is_empty_not_an_error(self):
        assert conversations.read("nope-not-here") == []

    def test_removing_it_from_the_list_keeps_the_words(self, db):
        opened = conversations.start(db, "keep me")
        conversations.record(db, opened["id"], "user", "the work")
        conversations.delete(db, opened["id"])
        with pytest.raises(KeyError):
            conversations.get(db, opened["id"])
        assert conversations.messages(opened["id"]) == [{"role": "user", "content": "the work"}]

    def test_purging_is_a_separate_deliberate_act(self, db):
        opened = conversations.start(db, "goodbye")
        conversations.delete(db, opened["id"], keep_file=False)
        assert not conversations.transcript_path(opened["id"]).exists()


class TestListing:
    def test_newest_first(self, db):
        first = conversations.start(db, "older")["id"]
        second = conversations.start(db, "newer")["id"]
        conversations.record(db, second, "user", "touch")
        assert next(row["id"] for row in conversations.recent(db)) == second
        assert first in [row["id"] for row in conversations.recent(db)]

    def test_storage_reports_what_is_on_disk(self, db):
        conversations.start(db, "one")
        conversations.start(db, "two")
        report = conversations.storage()
        assert report["files"] == 2
        assert report["bytes"] > 0


class TestWhereYouLeftIt:
    """Each row says what came of the conversation, not how it opened.

    The sidebar carried the first thing the person typed and a count of the messages since,
    so a hundred and fifty rows read "hey · 80 msg" — the opening of a session and its
    volume, neither of which is what anyone opens a history list to find out. The index row
    keeps his last word now, and these are the two properties that makes it worth anything:
    it is the *last* one, and it is a readable line rather than whatever character his reply
    happened to begin with.
    """

    def test_his_last_word_is_the_one_kept(self, db):
        chat = conversations.start(db, "hey")["id"]
        conversations.record(db, chat, "assistant", "Started on the auth flow.")
        conversations.record(db, chat, "user", "and the tests?")
        conversations.record(db, chat, "assistant", "Pushed b432a34 to origin/main. CI is green.")

        row = next(one for one in conversations.recent(db) if one["id"] == chat)
        assert row["lastSaid"] == "Pushed b432a34 to origin/main. CI is green."

    def test_what_you_typed_is_not_what_is_shown(self, db):
        chat = conversations.start(db, "hey")["id"]
        conversations.record(db, chat, "user", "hey")
        conversations.record(db, chat, "assistant", "Rewrote the fold cadence — tests green.")

        row = next(one for one in conversations.recent(db) if one["id"] == chat)
        assert row["title"] == "hey", "the title is still your words; the row just stops leading with it"
        assert row["lastSaid"] == "Rewrote the fold cadence — tests green."

    @pytest.mark.parametrize(
        ("reply", "expected"),
        [
            ("```python\nprint(1)\n```\nDone — it prints.", "Done — it prints."),
            ("## What changed\n\nThree files.", "What changed"),
            ("- Fixed the migration\n- Ran the suite", "Fixed the migration"),
            ("**Green.** All 412 tests pass.", "Green. All 412 tests pass."),
        ],
    )
    def test_it_is_the_first_readable_line_not_the_first_line(self, db, reply, expected):
        """A row reading "```python" is worse than the message count it replaced."""
        chat = conversations.start(db, "go")["id"]
        conversations.record(db, chat, "assistant", reply)
        assert conversations.recent(db)[0]["lastSaid"] == expected

    def test_a_reply_with_nothing_readable_leaves_the_last_one_standing(self, db):
        chat = conversations.start(db, "go")["id"]
        conversations.record(db, chat, "assistant", "Here it is.")
        conversations.record(db, chat, "assistant", "```\ndiff --git a/x b/x\n```")
        assert conversations.recent(db)[0]["lastSaid"] == "Here it is."

    def test_a_long_reply_is_cut_to_a_line(self, db):
        chat = conversations.start(db, "go")["id"]
        conversations.record(db, chat, "assistant", "word " * 200)
        said = conversations.recent(db)[0]["lastSaid"]
        assert len(said) <= conversations.OUTCOME_CHARS + 1
        assert said.endswith("…")

    def test_a_conversation_from_before_the_column_is_read_out_of_its_transcript(self, db):
        """A hundred and fifty existing rows have no last word stored. They have one on disk."""
        chat = conversations.start(db, "an old one")["id"]
        conversations.record(db, chat, "assistant", "Left it at the checkpoint.")
        # As the migration leaves them: indexed, transcript intact, column blank.
        repo.conversations.set_last_said(db, chat, "")

        assert conversations.recent(db)[0]["lastSaid"] == "Left it at the checkpoint."
        assert repo.conversations.get(db, chat)["last_said"] == "Left it at the checkpoint.", (
            "read once and written back, or every listing re-reads every transcript"
        )

    def test_reading_your_history_does_not_reorder_it(self, db):
        older = conversations.start(db, "older")["id"]
        conversations.record(db, older, "assistant", "Finished that.")
        newer = conversations.start(db, "newer")["id"]
        conversations.record(db, newer, "assistant", "And this.")
        repo.conversations.set_last_said(db, older, "")
        was = repo.conversations.get(db, older)["updated_at"]

        listed = [row["id"] for row in conversations.recent(db)]

        assert listed[0] == newer, "backfilling an old row pushed it to the top of the sidebar"
        assert repo.conversations.get(db, older)["updated_at"] == was


class TestSearchingWhatWasSaid:
    """Finding a conversation by what happened in it, not by how it opened.

    Nothing could do this. The control panel's box filters whichever tab is on screen, and
    the tab that held messages is gone — so the record kept most carefully was the one
    thing with no way in. Titles are generated from a first message, which means without
    this a conversation is findable by its opening line and by nothing else in it.
    """

    def test_a_word_from_the_middle_of_a_conversation_finds_it(self, db):
        first = conversations.start(db, "planning the week")
        conversations.record(db, first["id"], "user", "can you look at the invoice from Acme")
        conversations.start(db, "something unrelated")

        hits = conversations.search(db, "acme")

        assert [hit["conversationId"] for hit in hits] == [first["id"]]
        assert "Acme" in hits[0]["snippet"]

    def test_it_says_who_said_it(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "assistant", "I found the missing receipt")

        hits = conversations.search(db, "receipt")

        # "Did I ask for that or did he offer it" is most of why anyone is looking.
        assert hits[0]["role"] == "assistant"

    def test_case_does_not_matter(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "Check the QUARTERLY numbers")
        assert len(conversations.search(db, "quarterly")) == 1

    def test_one_hit_per_conversation(self, db):
        one = conversations.start(db, "hello")
        for _ in range(5):
            conversations.record(db, one["id"], "user", "budget budget budget")

        # Five matches from one conversation would bury every other conversation that also
        # has one, and the list exists to get you to the right conversation.
        assert len(conversations.search(db, "budget")) == 1

    def test_newest_conversation_first(self, db):
        older = conversations.start(db, "older")
        conversations.record(db, older["id"], "user", "the mango report")
        newer = conversations.start(db, "newer")
        conversations.record(db, newer["id"], "user", "the mango report again")

        hits = conversations.search(db, "mango")

        assert [hit["conversationId"] for hit in hits] == [newer["id"], older["id"]]

    def test_an_empty_query_finds_nothing_rather_than_everything(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "anything")
        assert conversations.search(db, "   ") == []

    def test_the_limit_is_respected(self, db):
        for index in range(6):
            one = conversations.start(db, f"chat {index}")
            conversations.record(db, one["id"], "user", "shared word")
        assert len(conversations.search(db, "shared", limit=3)) == 3

    def test_reasoning_and_tool_calls_are_not_searched(self, db):
        one = conversations.start(db, "hello")
        conversations.record_event(
            one["id"], "tool_call", {"name": "shell", "arguments": {"command": "rg zebra"}}
        )

        # Matching these would answer "where did we talk about X" with a stack trace, and
        # the tool call is not something either of you said.
        assert conversations.search(db, "zebra") == []

    def test_a_missing_transcript_is_skipped_rather_than_raising(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "findable")
        conversations.transcript_path(one["id"]).unlink()

        # The index can outlive the file — that is the whole reason the files are the
        # record. A search must not die because one row points at nothing.
        assert conversations.search(db, "findable") == []

    def test_a_mangled_line_does_not_stop_the_search(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "before the damage")
        with conversations.transcript_path(one["id"]).open("a") as handle:
            handle.write("{not json at all\n")
        conversations.record(db, one["id"], "user", "after the damage")

        assert len(conversations.search(db, "after the damage")) == 1


class TestSnippets:
    def test_a_long_line_is_cut_around_the_match(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "x" * 400 + " needle " + "y" * 400)

        snippet = conversations.search(db, "needle")[0]["snippet"]

        assert "needle" in snippet
        assert len(snippet) < 250
        assert snippet.startswith("…") and snippet.endswith("…")

    def test_newlines_are_flattened(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "first line\n\nthen the keyword here")

        snippet = conversations.search(db, "keyword")[0]["snippet"]

        # A result row is one line high. A snippet with newlines in it either breaks the
        # row or gets silently clipped, and the offset of the match moves when whitespace
        # collapses — which put the window in the wrong place until it was re-found.
        assert "\n" not in snippet
        assert "keyword" in snippet
