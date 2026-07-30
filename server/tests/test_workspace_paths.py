"""The guard on every workspace path that arrives from the interface.

Its whole job is that a path from outside cannot name anything outside his home. Worth
its own file because the first version passed the obvious cases and still let
``/etc/passwd`` through: it stripped slashes *before* checking for a leading one, so an
absolute path was quietly rewritten as a relative one. That was harmless only because
another function happened to anchor relative paths at his home — a guarantee by
accident is not a guarantee.
"""

from __future__ import annotations

import pytest

from kith.api.routes.workspace import _relative
from kith.infra import workspace


class TestRefusals:
    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "/",
            "~/secrets",
            "~",
        ],
    )
    def test_absolute_and_home_relative_paths(self, path):
        # Refused, not repaired: rewriting these to look relative is how the check
        # stopped being one.
        with pytest.raises(ValueError, match="absolute"):
            _relative(path)

    @pytest.mark.parametrize(
        "path",
        [
            "../etc",
            "../../etc/passwd",
            "work/../../etc",
            "a/../../b",
            "./../x",
        ],
    )
    def test_traversal_in_any_position(self, path):
        with pytest.raises(ValueError, match="outside"):
            _relative(path)

    @pytest.mark.parametrize("path", [".", "./", "", "   ", None, "./."])
    def test_his_home_itself(self, path):
        # Renaming or deleting the workspace root is never what someone meant.
        with pytest.raises(ValueError):
            _relative(path)


class TestAccepted:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("work/notes.md", "work/notes.md"),
            ("  work/notes.md  ", "work/notes.md"),
            ("./work/notes.md", "work/notes.md"),
            ("work//notes.md", "work/notes.md"),
            ("report.pdf", "report.pdf"),
            ("a/b/c/d.txt", "a/b/c/d.txt"),
            # A name that merely contains dots is not traversal.
            ("work/..hidden.md", "work/..hidden.md"),
            ("v1.2.3/notes.md", "v1.2.3/notes.md"),
        ],
    )
    def test_normalised_and_kept(self, given, expected):
        assert _relative(given) == expected

    def test_spaces_and_unicode_survive(self):
        # He writes filenames with spaces; quoting is the shell layer's problem, not a
        # reason to refuse them here.
        assert _relative("work/my report — final.md") == "work/my report — final.md"


class TestTheContainersOldHome:
    """`/home/kith` was his home for the life of the Docker sandbox.

    It is in fourteen rows of his own memory, in notes he wrote, in messages he sent, and in
    every task working-file path he was ever handed. On macOS `/home` is an autofs mount, so
    creating `/home/kith` fails with "Operation not supported" — and that is how this
    surfaced: he was given `/home/kith/work/task-41.md`, could not create it, and reported
    himself blocked on a task he was perfectly capable of doing.
    """

    def test_the_old_home_means_his_folder(self):
        assert workspace.resolve("/home/kith") == str(workspace.root())

    def test_a_path_under_it_is_rewritten(self):
        assert workspace.resolve("/home/kith/work/task-41.md") == str(
            workspace.root() / "work" / "task-41.md"
        )

    def test_a_trailing_slash_does_not_produce_a_double_one(self):
        assert workspace.resolve("/home/kith/") == str(workspace.root())

    def test_a_similarly_named_path_is_not_swallowed(self):
        """`/home/kithara` is somebody else's directory, not a prefix match."""
        assert workspace.resolve("/home/kithara/x") == "/home/kithara/x"

    def test_other_absolute_paths_are_untouched(self):
        assert workspace.resolve("/etc/hosts") == "/etc/hosts"
