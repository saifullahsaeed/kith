"""Naming a definition and getting that definition, instead of the file it lives in.

The advice was already "outline first, then read_file with offset and limit", and it is right.
It also costs two rounds and asks him to do arithmetic on line numbers he has just read — so
when he is in a hurry, which is most of the time, he reads the whole file instead. Measured on
`engine/code/lsp/client.py`: the file is ~7,255 tokens, and `LanguageServer.request` inside it
is ~339. A default read would not even have finished the file, because it stops at 400 lines
of 694 and says so.

So the assertions here are about *the right span* and *what happens when the name is wrong*.
The second matters more than it looks. A miss that says only "not found" sends him back to
reading the whole file, which is the thing this exists to avoid, so every failure has to carry
the list of what he could have asked for.
"""

from __future__ import annotations

import pytest

from kith.engine.code import excerpt, outline
from kith.infra import workspace as ws
from kith.tools import computer

PY_SOURCE = '''\
"""A module."""

CONSTANT = 1


def top_level(a, b=2):
    """Doc."""
    return a + b


class Thing:
    """A class."""

    def method(self, x):
        return x

    def shared(self):
        return 1


class Other:
    def shared(self):
        return 2


async def last_in_file():
    """Nothing follows this, which is where guessing the end from the next symbol fails."""
    pass
'''


@pytest.fixture
def module(tmp_path):
    path = tmp_path / "m.py"
    path.write_text(PY_SOURCE)
    return path


class TestFindingTheDefinition:
    def test_a_top_level_function_is_found_by_its_bare_name(self, module):
        span = excerpt.locate(module, "top_level")
        assert span.qualified == "top_level"
        assert PY_SOURCE.splitlines()[span.line - 1].startswith("def top_level")

    def test_the_span_covers_the_whole_definition_and_stops(self, module):
        """The end is the point. A start line alone is what `outline` already gives."""
        span = excerpt.locate(module, "top_level")
        body = PY_SOURCE.splitlines()[span.line - 1 : span.end_line]
        assert "return a + b" in "\n".join(body), "the whole body"
        assert not any("class Thing" in one for one in body), "and nothing after it"

    def test_a_method_is_reached_by_its_qualified_name(self, module):
        span = excerpt.locate(module, "Thing.method")
        assert span.qualified == "Thing.method"
        assert "def method" in PY_SOURCE.splitlines()[span.line - 1]

    def test_a_unique_method_is_reached_by_its_bare_name_too(self, module):
        """`method` is only defined once here, and having read an outline that is what he
        would type."""
        assert excerpt.locate(module, "method").qualified == "Thing.method"

    def test_the_last_definition_in_a_file_ends_at_its_own_end(self, module):
        """The case that rules out deriving the end from the next symbol's start: there is no
        next symbol, and the file's last line is not the definition's last line."""
        span = excerpt.locate(module, "last_in_file")
        assert span.end_line < span.of_lines or span.count < span.of_lines
        body = "\n".join(PY_SOURCE.splitlines()[span.line - 1 : span.end_line])
        assert "pass" in body

    def test_it_reports_how_big_the_file_was(self, module):
        """So he can tell reading a method from reading most of a module."""
        span = excerpt.locate(module, "Thing.method")
        assert span.of_lines == len(PY_SOURCE.splitlines()) + 1
        assert span.count < span.of_lines


class TestWhenTheNameIsWrong:
    def test_an_ambiguous_bare_name_lists_the_candidates(self, module):
        """`shared` is defined on two classes. Guessing one would be a confident wrong answer
        about somebody's code."""
        with pytest.raises(excerpt.ExcerptError) as caught:
            excerpt.locate(module, "shared")
        message = str(caught.value)
        assert "ambiguous" in message
        assert "Thing.shared" in message and "Other.shared" in message, "both, by the name to retry with"

    def test_a_name_that_is_not_there_lists_what_is(self, module):
        with pytest.raises(excerpt.ExcerptError) as caught:
            excerpt.locate(module, "nonexistent")
        message = str(caught.value)
        assert "top_level" in message and "Thing.method" in message

    def test_an_empty_name_is_refused(self, module):
        with pytest.raises(excerpt.ExcerptError):
            excerpt.locate(module, "   ")

    def test_a_file_it_cannot_parse_says_so_in_outline_s_words(self, tmp_path):
        """Not a second vocabulary for the same refusal — `source_of` is shared, so 'I can't
        read the shape of .txt files' arrives here too."""
        path = tmp_path / "notes.txt"
        path.write_text("just words")
        with pytest.raises(outline.OutlineError):
            excerpt.locate(path, "anything")


class TestThroughTheTool:
    """`read_file` with `symbol`, which is how he actually reaches this.

    An argument on the existing tool rather than a tool of its own, deliberately: a schema is
    paid on every round of every turn, and `engine/code/__init__.py`'s fourth rule says an
    analysis is not automatically a tool.
    """

    @pytest.fixture(autouse=True)
    def workspace_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        return tmp_path

    def test_it_returns_the_definition_under_a_header_that_places_it(self, workspace_root):
        (workspace_root / "m.py").write_text(PY_SOURCE)
        out = computer.read_file(workspace_root, {"path": "m.py", "symbol": "Thing.method"})
        head, _, body = out.partition("\n")
        assert head.startswith("Thing.method"), head
        whole = excerpt.locate(workspace_root / "m.py", "Thing.method").of_lines
        assert f"of {whole}" in head, "how big the file was, so he can judge whether to read the rest"
        assert "def method" in body
        assert "def top_level" not in body, "only the definition asked for"

    def test_the_body_keeps_the_file_s_own_line_numbers(self, workspace_root):
        """Read through `sandbox.read_file`, so the numbers are absolute and can be handed
        straight to `edit_file` or a follow-up read."""
        (workspace_root / "m.py").write_text(PY_SOURCE)
        out = computer.read_file(workspace_root, {"path": "m.py", "symbol": "Thing.method"})
        span = excerpt.locate(workspace_root / "m.py", "Thing.method")
        assert f"{span.line:6d}\t" in out

    def test_a_bad_name_comes_back_as_help_not_a_failure(self, workspace_root):
        (workspace_root / "m.py").write_text(PY_SOURCE)
        out = computer.read_file(workspace_root, {"path": "m.py", "symbol": "nope"})
        assert "top_level" in out, "what he could have asked for"

    def test_without_a_symbol_it_reads_the_file_as_before(self, workspace_root):
        (workspace_root / "m.py").write_text(PY_SOURCE)
        out = computer.read_file(workspace_root, {"path": "m.py"})
        assert "def top_level" in out and "class Other" in out


class TestQualifyingNames:
    def test_nesting_comes_from_the_depth_column(self, module):
        """`Symbol` has no parent link. The dotted path is rebuilt from the order and depth of
        the walk, which is why this is a function over symbols rather than a parser change."""
        source = module.read_bytes()
        names = [q for q, _ in excerpt.qualify(outline.of_source(source, "python"))]
        assert "Thing.method" in names
        assert "Other.shared" in names
        assert "top_level" in names, "a top-level name stays bare"

    def test_a_sibling_does_not_inherit_the_previous_scope(self, module):
        """The bug this shape invites: leaving `Thing` on the stack so `Other.shared` comes
        out as `Thing.Other.shared`."""
        names = [q for q, _ in excerpt.qualify(outline.of_source(module.read_bytes(), "python"))]
        assert "Thing.Other.shared" not in names
        assert not any(one.count(".") > 1 for one in names), names
