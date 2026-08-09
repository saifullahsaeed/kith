"""Every file he opens or changes is on the record, with the version he saw.

Two measured problems, one cause. His own compaction notes it: in one session he read
``ModelsSettings.tsx`` fifteen times and ``.kith/memory.md`` thirteen, and 54% of every read
he made was of a file he had already read. And `conversations.full_messages` replays an
hours-old read as though it were still true, which its own docstring admits is a deliberate
risk rather than a solved problem.

Both need the same missing fact: *which version of which file has he already seen*. Recording
it at the moment of the touch is the only place that fact exists — afterwards the transcript
has the bytes but not what they were bytes *of*.

The stamp is ``size:mtime_ns`` rather than a content hash, and that is a deliberate trade. A
read returns a window (``offset``/``limit``), so hashing what came back would answer "is this
window the same" and not "is the file the same"; hashing the file instead costs a full re-read
of every file he touches, on his most-called tool. A stat is O(1) and answers the real
question. It can only be wrong in one direction — a touch that rewrites a file to identical
content with a preserved mtime reads as unchanged — and every editor, including his own tools,
moves mtime.
"""

from __future__ import annotations

import pytest

from kith import tools
from kith.infra import workspace
from kith.services import session_context, touched


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace.paths.settings, "WORKSPACE_DIR", str(tmp_path))
    return tmp_path


class TestWhatGetsRecorded:
    def test_a_file_he_reads_is_on_the_record(self, db, workspace_root):
        (workspace_root / "notes.md").write_text("hello")

        with session_context.working_in("conv1"):
            touched.record(db, "read_file", {"path": "notes.md"})

        assert [row["path"] for row in touched.seen(db, "conv1")] == [str(workspace_root / "notes.md")]

    def test_a_tool_that_touches_no_file_records_nothing(self, db, workspace_root):
        with session_context.working_in("conv1"):
            touched.record(db, "web_search", {"query": "anything"})

        assert touched.seen(db, "conv1") == []

    def test_work_belonging_to_no_conversation_records_nothing(self, db, workspace_root):
        # A tick, a test, a script. There is no manifest for these to belong to, and inventing
        # one would put a scheduler's reads into whichever chat looked next.
        (workspace_root / "notes.md").write_text("hello")

        touched.record(db, "read_file", {"path": "notes.md"})

        assert touched.seen(db, "conv1") == []

    def test_a_file_that_is_not_there_is_still_recorded(self, db, workspace_root):
        # He tried, and that he tried is worth knowing — it is why he should not try again.
        with session_context.working_in("conv1"):
            touched.record(db, "read_file", {"path": "missing.md"})

        assert [row["path"] for row in touched.seen(db, "conv1")] == [str(workspace_root / "missing.md")]


class TestWhetherWhatHeSawIsStillTrue:
    def test_reading_the_same_unchanged_file_twice_is_not_stale(self, db, workspace_root):
        (workspace_root / "notes.md").write_text("hello")

        with session_context.working_in("conv1"):
            touched.record(db, "read_file", {"path": "notes.md"})
            touched.record(db, "read_file", {"path": "notes.md"})

        assert [row["stale"] for row in touched.seen(db, "conv1")] == [False]

    def test_a_read_before_an_edit_is_stale_afterwards(self, db, workspace_root):
        target = workspace_root / "notes.md"
        target.write_text("hello")

        with session_context.working_in("conv1"):
            touched.record(db, "read_file", {"path": "notes.md"})
            target.write_text("hello, and more")
            touched.record(db, "edit_file", {"path": "notes.md", "old": "x", "new": "y"})

        rows = touched.seen(db, "conv1")
        assert [row["stale"] for row in rows] == [False]
        assert rows[0]["action"] == "wrote"

    def test_a_file_changed_underneath_him_is_stale(self, db, workspace_root):
        # Nobody called a tool — you edited it in your own editor. The next turn should say so
        # rather than let him answer from what he read an hour ago.
        target = workspace_root / "notes.md"
        target.write_text("hello")
        with session_context.working_in("conv1"):
            touched.record(db, "read_file", {"path": "notes.md"})

        target.write_text("changed by someone else")

        assert [row["stale"] for row in touched.seen(db, "conv1")] == [True]


class TestItHappensOnEveryRealToolCall:
    def test_reading_a_file_through_run_tool_records_it(self, db, workspace_root):
        # Recorded from `run_tool` rather than from inside each file tool: that is the one place
        # every call already passes through, and it is five call sites today that would be six
        # the next time a file tool is added.
        (workspace_root / "notes.md").write_text("hello")

        with session_context.working_in("conv1"):
            answer = tools.run_tool("read_file", {"path": "notes.md"}, db)

        assert answer["ok"] is True
        assert [row["path"] for row in touched.seen(db, "conv1")] == [str(workspace_root / "notes.md")]

    def test_a_tool_that_fails_still_records_the_attempt(self, db, workspace_root):
        # He asked for a file that is not there. That he asked is the useful part — it is the
        # reason not to ask again — and the manifest shows it as missing.
        with session_context.working_in("conv1"):
            answer = tools.run_tool("read_file", {"path": "nope.md"}, db)

        assert answer["ok"] is False
        assert [row["version"] for row in touched.seen(db, "conv1")] == [""]

    def test_a_tool_refused_by_the_allow_list_records_nothing(self, db, workspace_root):
        # It never ran, so there is nothing to know about a file.
        (workspace_root / "notes.md").write_text("hello")

        with session_context.working_in("conv1"):
            answer = tools.run_tool("read_file", {"path": "notes.md"}, db, allow={"web_search"})

        assert answer["ok"] is False
        assert touched.seen(db, "conv1") == []


class TestOneCallCanTouchSeveralFiles:
    def test_an_edit_across_files_records_every_one(self, db, workspace_root):
        # `edit_files` is what he is told to reach for the moment a change touches more than
        # one place, so several paths in one call is the ordinary case here, not the exotic one.
        (workspace_root / "a.py").write_text("a")
        (workspace_root / "b.py").write_text("b")

        with session_context.working_in("conv1"):
            touched.record(
                db,
                "edit_files",
                {
                    "edits": [
                        {"path": "a.py", "old": "a", "new": "A"},
                        {"path": "b.py", "old": "b", "new": "B"},
                    ]
                },
            )

        assert sorted(row["path"] for row in touched.seen(db, "conv1")) == [
            str(workspace_root / "a.py"),
            str(workspace_root / "b.py"),
        ]

    def test_an_edit_with_a_malformed_edit_list_records_nothing(self, db, workspace_root):
        with session_context.working_in("conv1"):
            touched.record(db, "edit_files", {"edits": "not a list"})

        assert touched.seen(db, "conv1") == []


class TestOneRowPerFile:
    def test_a_path_appears_once_however_often_he_touches_it(self, db, workspace_root):
        (workspace_root / "a.md").write_text("a")
        (workspace_root / "b.md").write_text("b")

        with session_context.working_in("conv1"):
            for _ in range(5):
                touched.record(db, "read_file", {"path": "a.md"})
            touched.record(db, "read_file", {"path": "b.md"})

        assert len(touched.seen(db, "conv1")) == 2

    def test_each_conversation_keeps_its_own(self, db, workspace_root):
        (workspace_root / "a.md").write_text("a")
        (workspace_root / "b.md").write_text("b")

        with session_context.working_in("conv1"):
            touched.record(db, "read_file", {"path": "a.md"})
        with session_context.working_in("conv2"):
            touched.record(db, "read_file", {"path": "b.md"})

        assert [row["path"] for row in touched.seen(db, "conv2")] == [str(workspace_root / "b.md")]
