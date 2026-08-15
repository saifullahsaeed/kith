"""Finding a name by what it is, not by the characters that spell it.

`grep foo` returns the definition, every call, the word in a comment, a substring of `foobar`,
the string in a fixture, and every unrelated local variable that happens to be called `foo`.
Separating those is reading he pays for. Checked against this repository while building it:
searching `candidates` textually returns 13 lines, six of which are a local variable in
`connections/manager.py` that has nothing to do with `repomap.candidates`. Structurally it is
three — one definition, two calls, and no reading required to know which.

The assertions that matter are therefore in two directions, and the second is the one that
would rot quietly:

* **No false positives** — a name in a comment, a string, or a longer identifier is not a hit.
* **No false negatives** — every real call *is* found, including through an attribute chain
  and in the languages whose grammar puts the receiver first. A structural search that
  silently misses call sites is worse than grep, because its answer looks authoritative.
"""

from __future__ import annotations

import pytest

from kith.engine.code import search

PY_SOURCE = '''\
"""A module that mentions target in its docstring."""


def target(x):
    return x


def other():
    # target is named in this comment
    note = "target in a string"
    targeting = 1          # a longer name that contains it
    return target(2) + deep.chain.target(3) + note + str(targeting)


class Holder:
    def run(self):
        return target(4)
'''

OTHER_SOURCE = """\
def unrelated():
    target = 5      # a local variable, not the function
    return target
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "a.py").write_text(PY_SOURCE)
    (tmp_path / "b.py").write_text(OTHER_SOURCE)
    return tmp_path


class TestWhatItFinds:
    def test_the_definition_is_found_with_its_kind(self, project):
        found = search.find(project, "target")
        assert [one["path"] for one in found["definitions"]] == ["a.py"]
        assert found["definitions"][0]["kind"] == "def"

    def test_every_real_call_is_found(self, project):
        """Three in `other` — plain, through an attribute chain — and one in a method."""
        found = search.find(project, "target")
        lines = sorted(one["line"] for one in found["calls"])
        assert len(lines) == 3, found["calls"]

    def test_a_call_through_an_attribute_chain_counts(self, project):
        """`deep.chain.target(3)` is a call to `target`. Taking the first identifier would
        have called it a call to `deep`."""
        found = search.find(project, "target")
        assert any("deep.chain.target" in one["text"] for one in found["calls"])

    def test_each_hit_carries_the_line_it_is_on(self, project):
        found = search.find(project, "target")
        for one in found["definitions"] + found["calls"]:
            assert one["line"] >= 1 and one["text"], one


class TestWhatItRefusesToCount:
    def test_a_mention_in_a_comment_is_not_a_hit(self, project):
        found = search.find(project, "target")
        assert not any(one["text"].lstrip().startswith("#") for one in found["calls"])

    def test_a_mention_in_a_string_is_not_a_hit(self, project):
        found = search.find(project, "target")
        assert not any(one["text"] == '"target in a string"' for one in found["calls"])

    def test_a_longer_name_that_contains_it_is_not_a_hit(self, project):
        """`targeting` is not `target`. This is where substring search goes wrong."""
        found = search.find(project, "targeting")
        assert found["calls"] == []

    def test_a_local_variable_sharing_the_name_is_not_a_hit(self, project):
        """The measured case: six of thirteen textual hits for `candidates` in this repo are a
        local in an unrelated module."""
        found = search.find(project, "target")
        assert not any(one["path"] == "b.py" for one in found["definitions"] + found["calls"])


class TestSayingWhatItCouldNotDo:
    def test_it_reports_how_many_files_it_read(self, project):
        """A count is what makes "no hits" trustworthy rather than alarming."""
        found = search.find(project, "target")
        assert found["files_searched"] == 2

    def test_a_language_with_no_call_node_is_named_rather_than_silently_empty(self, tmp_path):
        """Dart's grammar here produces no call node, so reporting zero calls without saying
        so would be a confident wrong answer about somebody's code."""
        (tmp_path / "m.dart").write_text("void f(){ target(1); }\n")
        found = search.find(tmp_path, "target")
        assert "dart" in found["unsupported"]
        assert "not searchable" in search.render(found)

    def test_stopping_early_says_how_much_was_left(self, tmp_path, monkeypatch):
        """The branch where a wrong number would hide. Someone reading "3 definitions" and not
        the ceiling concludes a symbol is barely used; the count of unread files is what stops
        that being a silent lie."""
        monkeypatch.setattr(search, "MAX_HITS", 2)
        for i in range(6):
            (tmp_path / f"f{i}.py").write_text("def target():\n    pass\n")
        found = search.find(tmp_path, "target")
        assert found["files_unsearched"] > 0, found
        assert found["files_searched"] + found["files_unsearched"] == 6
        assert "not searched" in search.render(found)

    def test_nothing_found_says_how_hard_it_looked(self, project):
        found = search.find(project, "nonexistent")
        assert "No definition or call" in search.render(found)
        assert "2 source files" in search.render(found)

    def test_an_empty_name_is_refused(self, project):
        with pytest.raises(search.SearchError):
            search.find(project, "  ")

    def test_a_file_is_not_a_folder(self, project):
        with pytest.raises(search.SearchError) as caught:
            search.find(project / "a.py", "target")
        assert "folder" in str(caught.value)


class TestAcrossLanguages:
    """The node-type table was built by parsing, not from memory, and this is what keeps it
    honest. Java, Ruby and PHP are here specifically: they put the receiver first and the
    method name in a field, so a first-child rule returns `o` for `o.target(2)` in all three.
    """

    @pytest.mark.parametrize(
        ("suffix", "source"),
        [
            (".py", "target(1)\no.target(2)\n"),
            (".ts", "target(1);\no.target(2);\n"),
            (".go", "package m\nfunc f(){ target(1); o.target(2) }\n"),
            (".rs", "fn f(){ target(1); o.target(2); }\n"),
            (".java", "class C { void f(){ target(1); o.target(2); } }\n"),
            (".rb", "target(1)\no.target(2)\n"),
            (".php", "<?php target(1); $o->target(2);\n"),
            (".lua", "target(1)\no.target(2)\n"),
        ],
    )
    def test_both_a_plain_call_and_a_method_call_are_found(self, tmp_path, suffix, source):
        (tmp_path / f"m{suffix}").write_text(source)
        found = search.find(tmp_path, "target")
        assert len(found["calls"]) == 2, f"{suffix}: {found['calls']}"


class TestTheBlastRadius:
    """Before changing something: who depends on it, and is any of that covering me.

    "Four call sites" and "four call sites, three of them tests" are different facts about how
    safe a change is, and the second is the question actually being asked.
    """

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("tests/test_x.py", True),
            ("a/b_test.go", True),
            ("ui/x.test.ts", True),
            ("ui/x.spec.ts", True),
            ("__tests__/a.js", True),
            ("src/test.py", True),
            ("kith/tools/code.py", False),
            # The one that was wrong. `testing.py` *runs* tests and is production code; a bare
            # startswith("test") called it a test, and that made a covered symbol report as
            # uncovered.
            ("kith/engine/run/testing.py", False),
        ],
    )
    def test_what_counts_as_a_test_file(self, path, expected):
        assert search.is_test(path) is expected

    def test_calls_are_split_between_code_and_tests(self, tmp_path):
        (tmp_path / "a.py").write_text("def target(): pass\ndef caller(): return target()\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text("def test_it(): return target()\n")
        rendered = search.render(search.find(tmp_path, "target"))
        assert "1 in code, 1 in tests" in rendered
        assert "called from code" in rendered and "called from tests" in rendered

    def test_something_nothing_tests_is_named_as_such(self, tmp_path):
        (tmp_path / "a.py").write_text("def target(): pass\ndef caller(): return target()\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_other.py").write_text("def test_nothing(): pass\n")
        assert "nothing covering it" in search.render(search.find(tmp_path, "target"))

    def test_no_coverage_claim_is_made_when_no_test_was_in_scope(self, tmp_path):
        """The bug this guards. Searching a package directory rather than the folder above it
        looks at no tests at all, and reporting "0 in tests" there reads as "safe to delete"
        about something with four tests one directory over."""
        (tmp_path / "a.py").write_text("def target(): pass\ndef caller(): return target()\n")
        rendered = search.render(search.find(tmp_path, "target"))
        assert "nothing covering it" not in rendered
        assert "no tests were in scope" in rendered


class TestThroughTheTool:
    @pytest.fixture(autouse=True)
    def workspace_root(self, tmp_path, monkeypatch):
        from kith.infra import workspace as ws

        monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        return tmp_path

    def test_it_returns_counts_and_something_to_read(self, workspace_root):
        from kith.tools import code

        (workspace_root / "a.py").write_text(PY_SOURCE)
        out = code.find_symbol(workspace_root, {"name": "target"})
        assert out["definitions"] == 1
        assert out["calls"] == 3
        assert "defined" in out["found"] and "called" in out["found"]

    def test_an_empty_name_comes_back_as_an_error_not_a_crash(self, workspace_root):
        from kith.tools import code

        assert "error" in code.find_symbol(workspace_root, {"name": ""})
