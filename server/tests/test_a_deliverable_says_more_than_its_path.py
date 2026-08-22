"""What a produced thing can tell you about itself.

A deliverable row carried a title and a path. On a task with seven of them — the case this came
from — that is seven lines differing only in a filename, with no way to tell the document he wrote
this morning from the one from three weeks ago, or a finished specification from a 2KB stub. The
date was in the database the whole time and was never sent to the page.

Size and modified time are not in the database, and should not be: they are facts about the file,
not about the row. He can rewrite `docs/V2_CORE_ARCHITECTURE.md` ten times without touching the
deliverable that points at it, and a stored size would be wrong from the first of those. So they
are read from disk when the page asks.
"""

from __future__ import annotations

import pytest

from kith.api.routes.tasks import _describe_files


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A folder of our own, reached by standing in for the resolver rather than the workspace.

    `_describe_files` is being tested here, not `locate` — which has its own tests, resolves
    against a root fixed well before any of this runs, and would need the real workspace moved to
    exercise. What matters here is what happens *after* a path becomes a file: that it is
    measured, that a miss is left absent, and that a text deliverable is not touched.

    The identity check below is what holds the other half — that this uses the same resolver the
    raw endpoint and the preview use, rather than a second copy of the rule.
    """
    from kith.api.routes import tasks

    monkeypatch.setattr(tasks, "wanted", lambda path, project: str(tmp_path / path))
    return tmp_path


class TestWhatAFileCanSay:
    def test_a_file_reports_its_size_and_when_it_changed(self, workspace):
        (workspace / "spec.md").write_text("x" * 1234)
        detail = {"project_id": None, "deliverables": [{"kind": "file", "content": "spec.md"}]}

        _describe_files(detail)

        assert detail["deliverables"][0]["bytes"] == 1234
        assert detail["deliverables"][0]["modified"]

    def test_a_file_that_is_not_there_says_nothing_rather_than_zero(self, workspace):
        """He moved it, or renamed it. "0 B" would read as an empty document, which is a
        different problem from a missing one and would send you looking for the wrong thing."""
        detail = {"project_id": None, "deliverables": [{"kind": "file", "content": "gone.docx"}]}

        _describe_files(detail)

        assert "bytes" not in detail["deliverables"][0]
        assert "modified" not in detail["deliverables"][0]

    def test_text_and_link_deliverables_are_left_alone(self, workspace):
        """Neither is a file, so neither has a size on disk to report."""
        detail = {
            "project_id": None,
            "deliverables": [
                {"kind": "text", "content": "some prose"},
                {"kind": "link", "content": "https://example.com"},
            ],
        }

        _describe_files(detail)

        assert all("bytes" not in one for one in detail["deliverables"])

    def test_a_task_with_no_deliverables_is_fine(self, workspace):
        detail: dict = {"project_id": None}
        _describe_files(detail)  # must not raise


class TestItUsesTheOneResolver:
    def test_the_path_is_anchored_the_same_way_the_viewer_anchors_it(self):
        """`routes/workspace.wanted` is shared rather than copied.

        A deliverable's path belongs to its project, not to whichever session is open, and the
        rule for turning one into a file already existed for the raw endpoint and the preview. A
        second copy here is how two places come to disagree about where a file is — which is the
        failure the download had: it resolved against the global root, found nothing, and saved
        the path as a text file.
        """
        from kith.api.routes import tasks, workspace

        assert tasks.wanted is workspace.wanted
