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
