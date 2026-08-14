"""The LSP client: framing, unsolicited traffic, and servers that misbehave.

Most of this runs against `fake_language_server.py` rather than a real one, because the
things that break a client are the things a good server never does — asking a question back
during startup and blocking on the answer, publishing diagnostics twice, splitting a frame
across two writes, dying mid-handshake. pyright does none of them, so pyright cannot test
for them.

The class at the bottom does run against real pyright, and skips when it is not installed.
Both matter: the fake proves the protocol handling, the real one proves the protocol handling
was of the right protocol.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from kith.engine.code.lsp import client as lsp
from kith.engine.code.lsp.client import LanguageServer, LSPError

FAKE = Path(__file__).parent / "fake_language_server.py"


def fake(behaviour: str, root: Path) -> LanguageServer:
    return LanguageServer([sys.executable, str(FAKE), behaviour], root, label=f"fake-{behaviour}")


@pytest.fixture
def project(tmp_path):
    (tmp_path / "sample.py").write_text("def alpha(x):\n    return x\n\nalpha(1)\n")
    return tmp_path


class TestTheHandshake:
    def test_it_starts_and_learns_what_the_server_can_do(self, project):
        server = fake("normal", project)
        try:
            server.start(timeout=10)
            assert server.alive
            assert server.capabilities.get("renameProvider") is True
            assert server.server_info.get("name") == "fake-normal"
        finally:
            server.stop()

    def test_a_question_from_the_server_is_answered(self, project):
        """pyright sends `workspace/configuration` during initialize and blocks on the reply.

        A client that treats every inbound message as a notification hangs here, and from the
        outside that is indistinguishable from a server that failed to start.
        """
        server = fake("asks", project)
        try:
            server.start(timeout=10)
            assert server.alive, "the server was still waiting for an answer it never got"
        finally:
            server.stop()

    def test_a_frame_split_across_two_writes_is_read_whole(self, project):
        """A short read desynchronises the stream for every message after it, which presents
        as bizarre intermittent parse failures rather than as an obvious bug."""
        server = fake("split", project)
        try:
            server.start(timeout=10)
            assert server.capabilities.get("definitionProvider") is True
        finally:
            server.stop()

    def test_a_server_that_dies_on_startup_raises_rather_than_hanging(self, project):
        server = fake("crash", project)
        with pytest.raises(LSPError):
            server.start(timeout=6)
        server.stop()

    def test_a_server_that_never_answers_times_out(self, project):
        server = fake("silent", project)
        try:
            server.start(timeout=10)
            with pytest.raises(LSPError) as caught:
                server.request("textDocument/definition", {}, timeout=1.0)
            assert "did not answer" in str(caught.value)
        finally:
            server.stop()

    def test_a_stray_log_line_on_stdout_is_stepped_over(self, project):
        """Servers do print things that are not messages. Skipping unknown header lines is
        what keeps one of them from ending the conversation."""
        server = fake("noise", project)
        try:
            server.start(timeout=10)
            assert server.alive
            assert server.capabilities.get("renameProvider") is True
        finally:
            server.stop()

    def test_an_unreadable_length_fails_fast_instead_of_hanging(self, project):
        """A byte-framed stream cannot be resynchronised without a valid length — the body
        would be read as the next message's headers, and everything after it is garbage. The
        earlier version treated it as a zero-length message and hung until the timeout."""
        import time

        server = fake("garbage", project)
        started = time.monotonic()
        try:
            with pytest.raises(LSPError):
                server.start(timeout=10)
            assert time.monotonic() - started < 5, "it waited out the timeout instead of failing"
        finally:
            server.stop()

    def test_a_request_to_a_stopped_server_is_a_clear_error(self, project):
        server = fake("normal", project)
        server.start(timeout=10)
        server.stop()
        with pytest.raises(LSPError) as caught:
            server.request("textDocument/definition", {}, timeout=2)
        assert "not running" in str(caught.value)


class TestDiagnostics:
    def test_they_arrive_unsolicited_after_opening_a_file(self, project):
        server = fake("normal", project)
        try:
            server.start(timeout=10)
            uri = server.open_document(project / "sample.py")
            found = server.diagnostics(uri, timeout=5)
            assert [one["message"] for one in found] == ["first answer"]
        finally:
            server.stop()

    def test_a_second_opinion_replaces_the_first(self, project):
        """Servers publish a fast approximate answer then a better one. Returning the first
        means reporting errors that resolve themselves a quarter of a second later."""
        server = fake("chatty", project)
        try:
            server.start(timeout=10)
            uri = server.open_document(project / "sample.py")
            found = lsp.readable_diagnostics(uri, server.diagnostics(uri, timeout=5))
            assert [one["message"] for one in found] == ["second answer"]
        finally:
            server.stop()

    def test_a_server_that_publishes_nothing_returns_nothing_rather_than_hanging(self, project):
        server = fake("silent", project)
        try:
            server.start(timeout=10)
            uri = server.open_document(project / "sample.py")
            assert server.diagnostics(uri, timeout=1.0) == []
        finally:
            server.stop()

    def test_reopening_a_changed_file_is_a_change_not_a_second_open(self, project):
        server = fake("normal", project)
        try:
            server.start(timeout=10)
            first = server.open_document(project / "sample.py")
            (project / "sample.py").write_text("def alpha(x):\n    return x + 1\n")
            second = server.open_document(project / "sample.py")
            assert first == second
            assert server.diagnostics(second, timeout=5) is not None
        finally:
            server.stop()


class TestReadingWhatComesBack:
    """The answer shapes. Every one of these is a legal reply, and which you get depends on
    the server rather than on the request."""

    def test_a_bare_location_and_a_list_of_them_read_the_same(self):
        one = {"uri": "file:///tmp/a.py", "range": {"start": {"line": 3, "character": 2}}}
        assert lsp.locations(one) == lsp.locations([one])

    def test_a_location_link_is_understood(self):
        link = {
            "targetUri": "file:///tmp/b.py",
            "targetSelectionRange": {"start": {"line": 9, "character": 4}},
        }
        assert lsp.locations(link) == [{"path": "/tmp/b.py", "line": 10, "column": 5}]

    def test_nothing_found_is_an_empty_list_not_a_crash(self):
        assert lsp.locations(None) == []

    def test_lines_and_columns_come_back_one_based(self):
        """LSP counts from zero and every human interface counts from one. Getting this
        wrong is an off-by-one in every file reference he ever reads."""
        found = lsp.locations({"uri": "file:///x.py", "range": {"start": {"line": 0, "character": 0}}})
        assert found == [{"path": "/x.py", "line": 1, "column": 1}]
        assert lsp.position(1, 1) == {"line": 0, "character": 0}

    def test_a_workspace_edit_is_understood_in_both_shapes(self):
        edit = {
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
            "newText": "z",
        }
        by_changes = {"changes": {"file:///tmp/a.py": [edit]}}
        by_documents = {"documentChanges": [{"textDocument": {"uri": "file:///tmp/a.py"}, "edits": [edit]}]}
        assert lsp.edits_from_workspace_edit(by_changes) == lsp.edits_from_workspace_edit(by_documents)

    def test_a_file_creation_in_a_workspace_edit_is_skipped_not_crashed_on(self):
        edit = {"documentChanges": [{"kind": "create", "uri": "file:///tmp/new.py"}]}
        assert lsp.edits_from_workspace_edit(edit) == []

    def test_edits_apply_back_to_front(self):
        """Every range is stated against the original document, so applying forwards
        invalidates every range after the first."""
        text = "aaa bbb ccc"
        edits = [
            {
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}},
                "newText": "XXXX",
            },
            {
                "range": {"start": {"line": 0, "character": 8}, "end": {"line": 0, "character": 11}},
                "newText": "Y",
            },
        ]
        assert lsp.apply_text_edits(text, edits) == "XXXX bbb Y"

    def test_edits_across_several_lines_land_in_the_right_places(self):
        text = "one\ntwo\nthree\n"
        edits = [
            {
                "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 3}},
                "newText": "TWO",
            },
        ]
        assert lsp.apply_text_edits(text, edits) == "one\nTWO\nthree\n"

    def test_diagnostics_sort_errors_before_warnings(self):
        raw = [
            {"severity": 2, "range": {"start": {"line": 0, "character": 0}}, "message": "a warning"},
            {"severity": 1, "range": {"start": {"line": 9, "character": 0}}, "message": "an error"},
        ]
        assert [one["severity"] for one in lsp.readable_diagnostics("", raw)] == ["error", "warning"]


class TestFindingASymbolWithoutACursor:
    """The tools take a name because a model has a name and does not have a cursor."""

    def test_it_finds_the_first_occurrence(self):
        assert lsp.find_symbol_position("x = 1\nvalue = 2\n", "value") == (2, 1)

    def test_near_line_picks_the_closest_one(self):
        text = "thing = 1\n\n\n\n\nthing = 2\n"
        assert lsp.find_symbol_position(text, "thing", near_line=6) == (6, 1)
        assert lsp.find_symbol_position(text, "thing", near_line=1) == (1, 1)

    def test_it_matches_whole_words_only(self):
        """Asking about `user` must not land inside `username`."""
        assert lsp.find_symbol_position("username = 1\nuser = 2\n", "user") == (2, 1)

    def test_a_name_that_is_not_there_is_none(self):
        assert lsp.find_symbol_position("x = 1\n", "absent") is None


class TestUriRoundTrip:
    def test_a_path_survives_the_trip(self, tmp_path):
        path = tmp_path / "a file with spaces.py"
        path.write_text("x = 1\n")
        assert lsp.from_uri(lsp.to_uri(path)) == str(path.resolve())

    def test_a_uri_starts_with_the_scheme(self, tmp_path):
        assert lsp.to_uri(tmp_path).startswith("file://")


pyright = shutil.which("pyright-langserver") or str(Path(sys.executable).parent / "pyright-langserver")


@pytest.mark.skipif(not Path(pyright).is_file(), reason="pyright is not installed here")
class TestAgainstRealPyright:
    """The fake proves the protocol handling; this proves it was the right protocol."""

    @pytest.fixture
    def python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
        (tmp_path / "helpers.py").write_text("def greet(name: str) -> str:\n    return 'hi ' + name\n")
        (tmp_path / "app.py").write_text(
            "from helpers import greet\n\n"
            "def main():\n"
            "    print(greet('world'))\n"
            "    print(greet(42))\n"
            "\n"
            "# greet is mentioned in this comment\n"
            "TEXT = 'greet appears in a string too'\n"
        )
        return tmp_path

    @pytest.fixture
    def server(self, python_project):
        one = LanguageServer([pyright, "--stdio"], python_project, label="pyright")
        one.start(timeout=45)
        yield one
        one.stop()

    def test_it_finds_a_real_type_error_on_the_right_line(self, server, python_project):
        uri = server.open_document(python_project / "app.py")
        found = lsp.readable_diagnostics(uri, server.diagnostics(uri, timeout=30))
        errors = [one for one in found if one["severity"] == "error"]
        assert errors, "pyright should object to greet(42)"
        assert any(one["line"] == 5 for one in errors)

    def test_a_reference_query_is_answered_at_all(self, server, python_project):
        """Transport only. *Whether the answer is complete* is a readiness question, and
        readiness is the semantics layer's problem — pyright answers this one before it has
        finished scanning the project, with a well-formed empty list. See
        `test_the_semantic_tools.py` for the assertions about the content."""
        uri = server.open_document(python_project / "helpers.py")
        spot = lsp.find_symbol_position((python_project / "helpers.py").read_text(), "greet")
        assert spot is not None
        answer = server.request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": lsp.position(*spot),
                "context": {"includeDeclaration": False},
            },
            timeout=30,
        )
        assert isinstance(lsp.locations(answer), list)

    def test_definition_crosses_a_file(self, server, python_project):
        uri = server.open_document(python_project / "app.py")
        found = lsp.locations(
            server.request(
                "textDocument/definition",
                {"textDocument": {"uri": uri}, "position": lsp.position(4, 11)},
                timeout=30,
            )
        )
        assert found and found[0]["path"].endswith("helpers.py")
