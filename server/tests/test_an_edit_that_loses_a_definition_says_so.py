"""Noticing at the moment it happens that a write removed a definition.

Two incidents in this repository, both shipped green:

* A rewrite truncated a module at `data_dir()` and took `tuning.routing()` with it. Nothing
  failed until something asked for a routing decision.
* `sandbox.docker_available()` was deleted with one call site missed, so for weeks the model
  was handed ``AttributeError: module … has no attribute 'docker_available'`` instead of the
  message that branch was supposed to produce.

Both are one shape: a definition stopped existing and nobody noticed. A suite is bad at this —
sometimes the code that would have caught it is the code that was deleted — and a parser is
good at it, because the question is purely structural.

The design decision under test is **which direction is reported**. Only losses. An edit that
adds definitions is doing its job, and saying so would put a line on every write for no
information; that cost is what would eventually get the whole thing turned off.
"""

from __future__ import annotations

import pytest

from kith.engine.code import verify
from kith.infra import workspace as ws
from kith.tools import computer

MODULE = """\
def keep():
    pass


def doomed():
    pass


class Thing:
    def method(self):
        pass
"""


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
    return tmp_path


class TestWhatCountsAsLost:
    def test_the_truncation_incident(self):
        """The real one: a rewrite that stops early takes everything after it."""
        before = b"def data_dir():\n    return 1\n\ndef routing():\n    return 2\n"
        after = b"def data_dir():\n    return 1\n"
        assert verify.lost(before, after, "python") == ["routing"]

    def test_a_nested_definition_is_lost_with_its_class(self):
        before = MODULE.encode()
        after = b"def keep():\n    pass\n"
        assert verify.lost(before, after, "python") == ["doomed", "Thing", "Thing.method"]

    def test_a_rename_reads_as_losing_the_old_name(self):
        """Reported rather than judged. Whether that was intended is the caller's business —
        a rule here about acceptable deletions would be this module guessing at intent."""
        before = MODULE.encode()
        assert verify.lost(before, before.replace(b"def doomed", b"def renamed"), "python") == ["doomed"]

    def test_one_of_two_same_named_methods_disappearing_is_a_loss(self):
        """A set difference would call this unchanged. Counting is what catches "the duplicate
        I meant to keep"."""
        before = b"class A:\n    def run(self): pass\nclass B:\n    def run(self): pass\n"
        after = b"class A:\n    def run(self): pass\n"
        assert verify.lost(before, after, "python") == ["B", "B.run"]

    def test_adding_definitions_is_not_a_loss(self):
        before = MODULE.encode()
        assert verify.lost(before, before + b"\n\ndef extra():\n    pass\n", "python") == []

    def test_changing_a_body_is_not_a_loss(self):
        before = MODULE.encode()
        after = MODULE.replace("def keep():\n    pass", "def keep():\n    return 1").encode()
        assert verify.lost(before, after, "python") == []


class TestThroughTheWriteTools:
    def test_an_edit_that_deletes_a_function_says_which(self, workspace_root):
        (workspace_root / "m.py").write_text(MODULE)
        out = computer.edit_file(
            workspace_root, {"path": "m.py", "old": "def doomed():\n    pass\n", "new": ""}
        )
        assert "removed 1 definition" in out
        assert "doomed" in out

    def test_an_edit_that_only_changes_a_body_says_nothing(self, workspace_root):
        (workspace_root / "m.py").write_text(MODULE)
        out = computer.edit_file(
            workspace_root,
            {"path": "m.py", "old": "def keep():\n    pass", "new": "def keep():\n    return 1"},
        )
        assert "removed" not in out

    def test_overwriting_a_file_with_less_than_it_had_says_so(self, workspace_root):
        """`write_file` is the tool the truncation incident actually went through."""
        (workspace_root / "m.py").write_text(MODULE)
        out = computer.write_file(workspace_root, {"path": "m.py", "content": "def keep():\n    pass\n"})
        assert "removed 3 definition" in out, out

    def test_a_brand_new_file_says_nothing(self, workspace_root):
        out = computer.write_file(workspace_root, {"path": "new.py", "content": "def fresh():\n    pass\n"})
        assert "removed" not in out

    def test_a_file_that_is_not_source_is_not_parsed_at_all(self, workspace_root):
        (workspace_root / "notes.md").write_text("# heading\n\ndef looks_like_code(): pass\n")
        out = computer.write_file(workspace_root, {"path": "notes.md", "content": "# gone\n"})
        assert "removed" not in out

    def test_the_write_still_happens_and_is_still_reported(self, workspace_root):
        """The note is an addition to the result, never a replacement for it — he still needs
        the diff to see what changed."""
        (workspace_root / "m.py").write_text(MODULE)
        out = computer.edit_file(
            workspace_root, {"path": "m.py", "old": "def doomed():\n    pass\n", "new": ""}
        )
        assert "1 replacement in m.py" in out, "the ordinary result survives"
        assert "doomed" not in (workspace_root / "m.py").read_text(), "and the edit landed"

    def test_a_parser_failure_cannot_fail_the_write(self, workspace_root, monkeypatch):
        """This is a remark about a write that already succeeded. If it can turn a good edit
        into a failed tool call, it is worse than not having it."""
        (workspace_root / "m.py").write_text(MODULE)

        def explode(*_args, **_kwargs):
            raise RuntimeError("parser fell over")

        monkeypatch.setattr(verify, "lost", explode)
        out = computer.edit_file(
            workspace_root, {"path": "m.py", "old": "def doomed():\n    pass\n", "new": ""}
        )
        assert "1 replacement in m.py" in out
        assert "doomed" not in (workspace_root / "m.py").read_text()
