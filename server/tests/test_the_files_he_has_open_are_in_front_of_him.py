"""The files this conversation has touched are listed in the prompt, with what he can trust.

Recording the touches (see `test_he_knows_which_files_he_has_touched`) only makes the fact
available. This puts it where it changes behaviour: a short manifest at the tail of the
request saying what he has already opened and which of it has moved since.

It goes at the **tail** because that is the one place in the request that is free. The block
is built once per turn, after the whole history, so it is new bytes on the turn's first call
and part of the cached prefix for every round after it — the prefix ahead of it is never
disturbed, which is the property `caching` and `compaction` exist to protect.
"""

from __future__ import annotations

import pytest

from kith import tools
from kith.config import default_config
from kith.infra import workspace
from kith.kernel import session_context
from kith.services import touched
from kith.services.turn.prompt import _build_messages


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace.paths.settings, "WORKSPACE_DIR", str(tmp_path))
    return tmp_path


def _touch(db, name, tool="read_file"):
    with session_context.working_in("conv1"):
        touched.record(db, tool, {"path": name})


class TestWhatTheBlockSays:
    def test_a_file_he_read_is_listed_as_still_current(self, db, workspace_root):
        (workspace_root / "notes.md").write_text("hello")
        _touch(db, "notes.md")

        block = touched.manifest(db, "conv1")

        assert "notes.md" in block
        assert "read" in block

    def test_a_file_that_moved_under_him_is_called_out(self, db, workspace_root):
        target = workspace_root / "notes.md"
        target.write_text("hello")
        _touch(db, "notes.md")
        target.write_text("someone else changed this")

        block = touched.manifest(db, "conv1")

        assert "changed since" in block.lower()

    def test_a_file_that_was_not_there_says_so(self, db, workspace_root):
        _touch(db, "ghost.md")

        assert "not there" in touched.manifest(db, "conv1").lower()

    def test_a_file_he_wrote_is_distinguished_from_one_he_read(self, db, workspace_root):
        (workspace_root / "made.md").write_text("x")
        _touch(db, "made.md", tool="write_file")

        assert "wrote" in touched.manifest(db, "conv1")

    def test_a_file_he_deleted_reads_as_deleted_not_as_missing(self, db, workspace_root):
        # It is gone because he removed it, and "not there when you looked" describes a failed
        # read. Told that about his own delete he may well go looking for it again.
        # Through the real tool, so the ordering is the real one: the file is gone by the time
        # the touch is recorded, which is exactly why it looked like a failed read.
        (workspace_root / "gone.md").write_text("x")
        with session_context.working_in("conv1"):
            answer = tools.run_tool("delete_file", {"path": "gone.md"}, db)
        assert answer["ok"] is True

        block = touched.manifest(db, "conv1")

        assert "deleted" in block.lower()
        assert "not there when you looked" not in block

    def test_a_file_under_his_own_folder_is_shortened_too(self, db, workspace_root, monkeypatch):
        # `resolve` anchors relative paths in the *base*, which is a linked project folder when
        # there is one — so a file under his own workspace root arrives absolute and stays that
        # way. Two roots, both his, and only one of them was being shortened.
        elsewhere = workspace_root / "linked"
        elsewhere.mkdir()
        monkeypatch.setattr(workspace.paths, "base_dir", lambda: elsewhere)
        (workspace_root / "mine.md").write_text("x")
        _touch(db, str(workspace_root / "mine.md"))

        block = touched.manifest(db, "conv1")

        assert "- mine.md" in block
        assert str(workspace_root) not in block

    def test_he_is_told_not_to_reopen_what_is_current(self, db, workspace_root):
        # The block exists to stop the repeat reads, so it has to actually say so. Listing the
        # files without the instruction is a list he can read and still ignore.
        (workspace_root / "notes.md").write_text("hello")
        _touch(db, "notes.md")

        assert "again" in touched.manifest(db, "conv1").lower()


class TestItRidesAtTheTailOfTheRequest:
    def _prompt(self, conversation_id):
        return _build_messages(
            [{"role": "user", "content": "what did you find"}], default_config(), conversation_id
        )

    def test_the_manifest_is_in_the_last_message(self, never_the_real_database, workspace_root):
        db = never_the_real_database
        (workspace_root / "notes.md").write_text("hello")
        _touch(db, "notes.md")

        prompt = self._prompt("conv1")

        assert "notes.md" in str(prompt[-1]["content"])
        assert not any("notes.md" in str(m.get("content") or "") for m in prompt[:-1])

    def test_the_tail_block_is_marked_so_the_ledger_can_cost_it(
        self, never_the_real_database, workspace_root
    ):
        # Everything cheap to add goes in this block, so this block is where things will be
        # added. Unmarked it is counted as part of the system prompt, and a block that has
        # grown to ten thousand tokens looks exactly like a large persona on the meter.
        # The marker never reaches a provider: `_to_openai` rebuilds every message from role
        # and content alone.
        db = never_the_real_database
        (workspace_root / "notes.md").write_text("hello")
        _touch(db, "notes.md")

        prompt = self._prompt("conv1")

        assert prompt[-1].get("_live") is True
        assert [m for m in prompt[:-1] if m.get("_live")] == []

    def test_touching_another_file_leaves_the_prefix_byte_identical(
        self, never_the_real_database, workspace_root
    ):
        # The property the whole design rests on. The manifest changes on every turn that
        # opens a file; if that edited anything but the last message, every round after it
        # would re-bill uncached — which is the bug `compaction` and `caching` both exist to
        # avoid, reintroduced by a feature meant to be free.
        db = never_the_real_database
        (workspace_root / "a.md").write_text("a")
        (workspace_root / "b.md").write_text("b")
        _touch(db, "a.md")
        before = self._prompt("conv1")

        _touch(db, "b.md")
        after = self._prompt("conv1")

        assert before[:-1] == after[:-1]
        assert before[-1] != after[-1]


class TestWhatItCosts:
    def test_a_conversation_that_has_touched_nothing_adds_nothing(self, db, workspace_root):
        assert touched.manifest(db, "conv1") == ""

    def test_work_belonging_to_no_conversation_adds_nothing(self, db, workspace_root):
        assert touched.manifest(db, "") == ""

    def test_paths_are_shown_relative_to_where_he_works(self, db, workspace_root):
        # An absolute path is most of a line and none of the meaning. Every one of these is
        # carried on every turn for the rest of the conversation.
        (workspace_root / "src").mkdir()
        (workspace_root / "src" / "app.py").write_text("x")
        _touch(db, "src/app.py")

        block = touched.manifest(db, "conv1")

        assert "src/app.py" in block
        assert str(workspace_root) not in block

    def test_a_long_session_keeps_the_newest_and_says_what_it_dropped(self, db, workspace_root):
        # Unbounded, this is the one block that grows for the whole life of a conversation.
        for index in range(touched.MANIFEST_LIMIT + 5):
            (workspace_root / f"f{index}.md").write_text("x")
            _touch(db, f"f{index}.md")

        block = touched.manifest(db, "conv1")

        assert f"f{touched.MANIFEST_LIMIT + 4}.md" in block  # newest kept
        assert "f0.md" not in block  # oldest dropped
        assert "5 older" in block  # and the drop is stated, not silent
