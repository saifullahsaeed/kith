"""An edit whose `old` is stale is refused with the stretch of the file it most resembles.

Between 2026-09-24 and 10-01, 57 `edit_files` batches were refused with "that exact text is not
in …", usually over a line an earlier edit or a formatter had changed. The refusal sent him to
read the file again and then send the batch again: two full rounds for one stale line. Showing
the closest stretch as it reads now makes the next round the corrected edit.

The batch stays all-or-nothing — that is pinned in `test_editing_several_things_at_once`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra import workspace
from kith.infra.workspace import WorkspaceError


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
    return tmp_path


SOURCE = """def total(items):
    subtotal = sum(item.price for item in items)
    return round(subtotal * 1.15, 2)


def label(item):
    return item.name.title()
"""


class TestTheRefusal:
    def test_it_shows_the_closest_stretch_and_where_it_is(self, workspace_root: Path):
        (workspace_root / "cart.py").write_text(SOURCE)

        with pytest.raises(WorkspaceError) as caught:
            workspace.edit_files(
                [
                    {
                        "path": "cart.py",
                        "old": "    subtotal = sum(i.price for i in items)\n    return round(subtotal * 1.15, 2)",
                        "new": "    return 0",
                    }
                ]
            )

        message = str(caught.value)
        assert "none were applied" in message
        assert "lines 2-3" in message
        assert "subtotal = sum(item.price for item in items)" in message

    def test_nothing_alike_gets_the_plain_refusal(self, workspace_root: Path):
        (workspace_root / "cart.py").write_text(SOURCE)

        with pytest.raises(WorkspaceError) as caught:
            workspace.edit_files([{"path": "cart.py", "old": "class Basket:\n    pass", "new": "x"}])

        message = str(caught.value)
        assert "read the part you mean to change" in message
        assert "closest" not in message

    def test_the_file_is_untouched(self, workspace_root: Path):
        path = workspace_root / "cart.py"
        path.write_text(SOURCE)

        with pytest.raises(WorkspaceError):
            workspace.edit_files(
                [
                    {"path": "cart.py", "old": "def label(item):", "new": "def name(item):"},
                    {"path": "cart.py", "old": "return item.nme.title()", "new": "return item.name"},
                ]
            )

        assert path.read_text() == SOURCE
