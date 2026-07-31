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


class TestReadingIsWindowedAndAlwaysContinuable:
    """He read the same two files five times in one step and got the same half each time.

    `read_file` caps output twice: a 400-line window, then a byte budget. Only the first one
    used to tell him how to continue. So a short file over the byte budget — a 79-line
    stylesheet of 16,000 characters — came back as half its content and
    "[truncated, 16078 chars total]": no offset, no next step, and the line window it *would*
    have suggested one from never tripped. From his side the rest of the file was unreachable,
    so he asked again. And again.

    It also sheared mid-line, which is its own problem: half a CSS rule is not something
    anyone can reason about.
    """

    def test_a_short_file_over_the_byte_budget_says_how_to_continue(self, tmp_path, monkeypatch):
        from kith.infra import workspace

        monkeypatch.setattr(workspace.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        # 60 lines of 300 characters: far inside the line window, far outside the byte budget.
        (tmp_path / "big.css").write_text("\n".join(f".rule-{n} {{ {'x' * 290} }}" for n in range(60)))

        first = workspace.read_file("big.css")

        assert "offset=" in first, "the only way out of a byte-clipped read is an offset"
        assert "of 60" in first

    def test_the_offset_it_gives_actually_reaches_the_rest(self, tmp_path, monkeypatch):
        import re

        from kith.infra import workspace

        monkeypatch.setattr(workspace.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        (tmp_path / "big.css").write_text("\n".join(f"line-{n} {'x' * 290}" for n in range(60)))

        seen: set[int] = set()
        offset = 1
        for _ in range(10):
            chunk = workspace.read_file("big.css", offset=offset)
            seen.update(int(m) for m in re.findall(r"^\s*(\d+)\t", chunk, re.M))
            match = re.search(r"offset=(\d+)", chunk)
            if not match:
                break
            offset = int(match.group(1))

        # Following the offsets has to reach every line. If it does not, "the rest" is a lie
        # and the loop he was stuck in is still available.
        assert seen == set(range(1, 61))

    def test_it_stops_on_a_line_boundary(self, tmp_path, monkeypatch):
        from kith.infra import workspace

        monkeypatch.setattr(workspace.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        source = [f"{n}:{'y' * 290}" for n in range(60)]
        (tmp_path / "big.txt").write_text("\n".join(source))

        body = workspace.read_file("big.txt")
        content = [line for line in body.splitlines() if not line.startswith("…")]

        # Every line that came back is byte-identical to the file's line. Slicing the finished
        # string sheared the last one mid-content — half a CSS rule, half a JSX attribute —
        # which is worse than not sending it, because it looks like the file says that.
        assert content, "something should have come back"
        for rendered in content:
            number, _, text = rendered.partition("\t")
            assert text == source[int(number.strip()) - 1]

    def test_a_file_that_fits_gets_no_footer(self, tmp_path, monkeypatch):
        from kith.infra import workspace

        monkeypatch.setattr(workspace.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        (tmp_path / "small.txt").write_text("one\ntwo\nthree\n")

        body = workspace.read_file("small.txt")

        assert "offset=" not in body
        assert "three" in body

    def test_the_line_window_still_reports_itself(self, tmp_path, monkeypatch):
        from kith.infra import workspace

        monkeypatch.setattr(workspace.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        (tmp_path / "many.txt").write_text("\n".join(f"line {n}" for n in range(1000)))

        body = workspace.read_file("many.txt", limit=10)

        assert "of 1000" in body
        assert "offset=11" in body
