"""Changing part of a file, instead of rewriting all of it.

`write_file` was the only way to alter anything, and rewriting a whole file to change one
line cost three things at once. Output tokens — a 12KB component is about 3,300 per edit.
Fidelity, because he regenerates from what he remembers reading, so anything he did not
retype is gone; and while `read_file` was truncating with no way to continue, "anything he
did not read" was in that category too. And formatting: a model paying by the token to
re-emit a file compresses it, which is how his App.jsx became 55 lines averaging 220
characters with one line of JSX at 3,262.

The design decision worth testing is that it *refuses* rather than guesses. Not-found is an
error and not a silent no-op, because a no-op reads as success and he moves on believing the
change landed. Ambiguous is an error too: if the string appears four times, replacing the
first is a coin flip on which one he meant. Both messages have to name the fix, because the
caller is a model that will otherwise retry the identical call.
"""

from __future__ import annotations

import pytest

from kith.infra import workspace as ws


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
    return tmp_path


class TestItRefusesRatherThanGuesses:
    def test_text_that_is_not_there(self, workspace_root):
        (workspace_root / "a.py").write_text("x = 1\n")
        with pytest.raises(ws.WorkspaceError) as caught:
            ws.edit_file("a.py", "y = 2", "y = 3")
        # Naming whitespace matters: it is the single most common reason a match misses.
        assert "not in" in str(caught.value)
        assert "indentation" in str(caught.value)

    def test_an_ambiguous_match_says_how_many(self, workspace_root):
        (workspace_root / "a.py").write_text("total = 0\ntotal = 1\ntotal = 2\n")
        with pytest.raises(ws.WorkspaceError) as caught:
            ws.edit_file("a.py", "total", "count")
        assert "3 times" in str(caught.value)
        # And what to do about it, or he retries the same call.
        assert "replace_all" in str(caught.value)

    def test_nothing_is_written_when_it_refuses(self, workspace_root):
        (workspace_root / "a.py").write_text("total = 0\ntotal = 1\n")
        with pytest.raises(ws.WorkspaceError):
            ws.edit_file("a.py", "total", "count")
        assert (workspace_root / "a.py").read_text() == "total = 0\ntotal = 1\n"

    def test_an_empty_old_string(self, workspace_root):
        (workspace_root / "a.py").write_text("x = 1\n")
        with pytest.raises(ws.WorkspaceError):
            ws.edit_file("a.py", "", "y = 2")

    def test_a_file_that_does_not_exist(self, workspace_root):
        with pytest.raises(ws.WorkspaceError) as caught:
            ws.edit_file("nope.py", "a", "b")
        assert "no nope.py" in str(caught.value)


class TestAnEditTheFileAlreadySatisfies:
    """`old == new` used to be refused, alongside not-found and ambiguous. It is not the same
    kind of thing, and treating it as one cost 72 of the 96 `edit_file` failures over
    2026-08-10 to 2026-08-12.

    Not-found means the state he asked for was **not** reached, so reporting success would be a
    lie. `old == new` means it is already true. The file says what he wants it to say; there
    are simply no bytes to move. That is worth reporting as done — and it is what he does when
    he has already made the change earlier in a turn and lost track of it, which is what a
    460,000-token transcript for a working memory produces.

    What the original refusal was protecting still holds: a no-op must never read as a
    *change*. So this is a loud no-op — the report says no bytes moved and why — never a silent
    one.
    """

    def test_it_is_not_an_error(self, workspace_root):
        (workspace_root / "a.py").write_text("x = 1\n")
        report = ws.edit_file("a.py", "x = 1", "x = 1")
        assert "already" in report

    def test_the_file_is_left_exactly_as_it_was(self, workspace_root):
        (workspace_root / "a.py").write_text("x = 1\n")
        ws.edit_file("a.py", "x = 1", "x = 1")
        assert (workspace_root / "a.py").read_text() == "x = 1\n"

    def test_it_says_no_bytes_moved_rather_than_claiming_a_replacement(self, workspace_root):
        (workspace_root / "a.py").write_text("x = 1\n")
        report = ws.edit_file("a.py", "x = 1", "x = 1")
        # The one thing this must not do is read like a change landed. "1 replacement" is
        # exactly the phrase that would make him believe one did.
        assert "1 replacement" not in report

    def test_text_that_is_not_there_at_all_is_still_an_error(self, workspace_root):
        # The distinction this whole class rests on: satisfied is not the same as unappliable,
        # and softening one must not soften the other. `old == new` on text the file does not
        # contain is not-found wearing a disguise — answering "already reads that way" to it is
        # exactly the silent success the original refusal existed to prevent.
        (workspace_root / "a.py").write_text("x = 1\n")
        with pytest.raises(ws.WorkspaceError) as caught:
            ws.edit_file("a.py", "y = 2", "y = 2")
        assert "not in" in str(caught.value)

    def test_a_match_only_the_tolerant_path_would_find_is_not_satisfied(self, workspace_root):
        # The file has two spaces, he sent four. It does *not* already read that way, so
        # "satisfied" would be untrue — and the only change this edit could make is a pure
        # re-indentation of text he believed was already right, which he never meant to ask for.
        (workspace_root / "a.py").write_text("if x:\n  deep()\n")
        with pytest.raises(ws.WorkspaceError):
            ws.edit_file("a.py", "    deep()", "    deep()")
        assert (workspace_root / "a.py").read_text() == "if x:\n  deep()\n"


class TestItChangesExactlyWhatWasAsked:
    def test_one_occurrence(self, workspace_root):
        (workspace_root / "a.py").write_text("def go():\n    return 1\n")
        ws.edit_file("a.py", "return 1", "return 2")
        assert (workspace_root / "a.py").read_text() == "def go():\n    return 2\n"

    def test_replace_all_when_asked(self, workspace_root):
        (workspace_root / "a.py").write_text("a = 1\na = 1\na = 1\n")
        out = ws.edit_file("a.py", "a = 1", "a = 2", replace_all=True)
        assert (workspace_root / "a.py").read_text() == "a = 2\na = 2\na = 2\n"
        assert "3 replacements" in out

    def test_whitespace_is_part_of_the_match(self, workspace_root):
        (workspace_root / "a.py").write_text("if x:\n  deep()\n")
        # Asking for more indentation than the file has must miss rather than be helpfully
        # corrected. Fuzzy matching here would edit a line he did not mean and report success.
        # (Asking for *less* legitimately matches — four spaces really are inside eight — which
        # is why the uniqueness check carries as much weight as this one.)
        with pytest.raises(ws.WorkspaceError):
            ws.edit_file("a.py", "    deep()", "    shallow()")
        ws.edit_file("a.py", "  deep()", "  shallow()")
        assert "shallow" in (workspace_root / "a.py").read_text()

    def test_it_is_not_a_regex(self, workspace_root):
        (workspace_root / "a.py").write_text("price = cost * 1.2  # .*\n")
        # A regex engine would match everything here. Exact means exact.
        ws.edit_file("a.py", ".*", "REGEX")
        assert (workspace_root / "a.py").read_text() == "price = cost * 1.2  # REGEX\n"

    def test_multiline_replacement(self, workspace_root):
        (workspace_root / "a.py").write_text("def go():\n    a()\n    b()\n    return\n")
        ws.edit_file("a.py", "    a()\n    b()\n", "    b()\n    a()\n")
        assert (workspace_root / "a.py").read_text() == "def go():\n    b()\n    a()\n    return\n"

    def test_the_rest_of_the_file_is_untouched(self, workspace_root):
        body = "\n".join(f"line {n}" for n in range(500))
        (workspace_root / "big.txt").write_text(body)
        ws.edit_file("big.txt", "line 250", "CHANGED")
        after = (workspace_root / "big.txt").read_text().splitlines()
        # The whole point over write_file: 499 lines he never mentioned are still exactly as
        # they were, including any he never read.
        assert after[250] == "CHANGED"
        assert after[:250] == [f"line {n}" for n in range(250)]
        assert after[251:] == [f"line {n}" for n in range(251, 500)]


class TestTheDiffItGivesBack:
    def test_a_normal_file_gets_a_unified_diff(self, workspace_root):
        (workspace_root / "a.py").write_text("def go():\n    return 1\n")
        out = ws.edit_file("a.py", "return 1", "return 2")
        assert "-    return 1" in out
        assert "+    return 2" in out

    def test_a_long_line_file_gets_the_changed_region_instead(self, workspace_root):
        # A one-word edit to a 3,000-character line produced a 10,931-character unified diff
        # in which the changed line was truncated before the change itself — a page of context
        # showing nothing, which he pays for.
        long_line = "<div>" + "".join(f"<span>item {n}</span>" for n in range(200)) + "</div>"
        (workspace_root / "App.jsx").write_text(f"const a = 1\n{long_line}\nconst b = 2\n")

        out = ws.edit_file("App.jsx", "<span>item 100</span>", "<span>ITEM</span>")

        assert len(out) < 1_000, f"the diff should be small, got {len(out)}"
        assert "line 2" in out, "it has to say where"
        assert "ITEM" in out, "and show the change"

    def test_the_diff_says_how_many_it_changed(self, workspace_root):
        (workspace_root / "a.py").write_text("x\nx\n")
        assert "2 replacements" in ws.edit_file("a.py", "x", "y", replace_all=True)


class TestPermissions:
    def test_editing_outside_his_folder_needs_a_yes(self, workspace_root, tmp_path, monkeypatch):
        from kith.infra import permissions
        from kith.infra.db import config_store

        db = tmp_path / "config.db"
        config_store.init(db)
        monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", db, raising=False)
        permissions.revoke_all()

        outside = tmp_path.parent / "not-his.txt"
        outside.write_text("theirs\n")

        # Same gate as read and write: an edit is a write, and the path is checked exactly
        # rather than by scanning a command for something that looks like a path.
        with pytest.raises(Exception) as caught:
            ws.edit_file(str(outside), "theirs", "mine")
        assert "not allowed" in str(caught.value).lower() or "allow" in str(caught.value).lower()
        assert outside.read_text() == "theirs\n"
