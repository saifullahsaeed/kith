"""Saying where you are standing must not change what he is allowed to touch.

The CLI sends the directory it was run in, so a question about "this repo" reaches him with
the folder named instead of sending him searching the disk for it. That fact is carried in a
ContextVar, and there is already a ContextVar in this module that names a folder —
`working_from` — which `permissions` reads as *a folder he may write in*. Two variables, both
holding a path, one of them load-bearing for access.

Reusing the wrong one would not be a bug, it would be a hole: a client could name any directory
on the machine and have the agent treated as free to write in it. Worse, it would look like it
worked, because the only visible effect either way is a sentence in the prompt.

So this test is the boundary, written before there was a second caller to get it wrong.
"""

from __future__ import annotations

from pathlib import Path

from kith.kernel import session_context


def test_arriving_from_does_not_set_the_folder_he_may_write_in(tmp_path: Path):
    somewhere = tmp_path / "someone-elses-repo"
    somewhere.mkdir()

    with session_context.arriving_from(str(somewhere)):
        assert session_context.sent_from_directory() == str(somewhere)
        # The variable that grants access is untouched. `isolated_base` is what `permissions`
        # and `paths.base_dir` consult; if naming a directory in a request could set it, the
        # CLI would be an access-widening API.
        assert session_context.isolated_base() == ""


def test_the_two_variables_are_not_the_same_one(tmp_path: Path):
    """A ContextVar is identified by object, not by name — two built from the same string are
    two different variables, and one built once and shared is one. This asserts they are
    genuinely separate, which reading the code cannot tell you at a glance."""
    granted = tmp_path / "worktree"
    typed = tmp_path / "terminal"
    granted.mkdir()
    typed.mkdir()

    with (
        session_context.working_from(str(granted), mirror_of=str(tmp_path)),
        session_context.arriving_from(str(typed)),
    ):
        assert session_context.sent_from_directory() == str(typed)
        assert session_context.isolated_base() == str(granted)


def test_it_is_empty_for_a_request_from_the_window():
    """The window has no directory to name, and must not inherit one from a CLI turn."""
    assert session_context.sent_from_directory() == ""
    with session_context.arriving_from(""):
        assert session_context.sent_from_directory() == ""
    with session_context.arriving_from(None):
        assert session_context.sent_from_directory() == ""


def test_it_does_not_leak_past_the_turn(tmp_path: Path):
    with session_context.arriving_from(str(tmp_path)):
        pass
    assert session_context.sent_from_directory() == ""


def test_the_ambient_block_names_it(db: Path):
    """And the one thing it is for actually happens."""
    from kith.services import memory_context

    with session_context.arriving_from("/Users/someone/work/thing"):
        block = memory_context.presence_block(db)
    assert "/Users/someone/work/thing" in block
    assert "[Right now]" in block

    plain = memory_context.presence_block(db)
    assert "terminal" not in plain, "a request from the window must not claim one"
