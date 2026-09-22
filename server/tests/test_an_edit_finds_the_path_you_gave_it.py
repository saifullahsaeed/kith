"""`edit_files` ignored a `path` sitting in its own arguments, and it was the single most
expensive defect in the tool surface.

Measured across 120 real conversation transcripts: `edit_files` is the second most-used tool
in the system at 2,392 calls, and **682 of them failed — 29%**. Of those, **566 (83%) were one
message: "edit N has no path"**. Sampling the arguments that produced it, most carried a
perfectly good `path` at the top level:

    {"path": "app/rag/store.py", "edits": [{"old": "...", "new": "..."}]}

which is the most natural way to say "edit this file, here are the changes" and is the shape
the retired `edit_file` taught. The handler's fallback reads

    raw = args.get("edits")
    if raw is None and args.get("path"):

so the top-level path was consulted **only when `edits` was absent entirely**. Send both and it
was ignored, every entry came up pathless, and the whole batch was refused.

Every one of those was a wasted round: the model re-read the file and tried again. At this
install's median round cost that is the most expensive thing measured all session, and none of
it was visible, because `turn_log` records tool *names* and never whether the call worked.

The fix is that a top-level `path` is the default for any edit that does not name its own. An
edit with no path anywhere is still refused — there is nothing to infer from.
"""

from __future__ import annotations

from pathlib import Path

import kith.tools  # noqa: F401
from kith.tools.registry import require


def _file(tmp_path: Path, name: str, body: str) -> Path:
    one = tmp_path / name
    one.write_text(body)
    return one


class TestATopLevelPathIsTheDefault:
    def test_one_edit_beside_a_top_level_path(self, db, never_the_real_workspace, tmp_path):
        one = _file(never_the_real_workspace, "store.py", "alpha\nbeta\n")
        out = require("edit_files").run(db, {"path": str(one), "edits": [{"old": "alpha", "new": "ALPHA"}]})
        assert "error" not in out, out
        assert one.read_text() == "ALPHA\nbeta\n"

    def test_several_edits_all_inheriting_it(self, db, never_the_real_workspace):
        one = _file(never_the_real_workspace, "store.py", "alpha\nbeta\ngamma\n")
        out = require("edit_files").run(
            db,
            {
                "path": str(one),
                "edits": [{"old": "alpha", "new": "A"}, {"old": "gamma", "new": "G"}],
            },
        )
        assert "error" not in out, out
        assert one.read_text() == "A\nbeta\nG\n"

    def test_an_edit_that_names_its_own_path_keeps_it(self, db, never_the_real_workspace):
        """Inheriting must not overwrite. A batch spanning two files where only one entry is
        explicit is the case that would silently write to the wrong file."""
        a = _file(never_the_real_workspace, "a.py", "one\n")
        b = _file(never_the_real_workspace, "b.py", "two\n")
        out = require("edit_files").run(
            db,
            {
                "path": str(a),
                "edits": [{"old": "one", "new": "ONE"}, {"path": str(b), "old": "two", "new": "TWO"}],
            },
        )
        assert "error" not in out, out
        assert a.read_text() == "ONE\n"
        assert b.read_text() == "TWO\n"


class TestWhatIsStillRefused:
    def test_an_edit_with_no_path_anywhere_is_still_an_error(self, db, never_the_real_workspace):
        """There is nothing to infer from, and guessing a file to write to is the one mistake
        worth failing a round over."""
        # Raised, not returned: `run_tool` is what turns a WorkspaceError into something the
        # model reads. Either way the call does not write anything.
        import pytest

        from kith.infra.workspace.base import WorkspaceError

        with pytest.raises(WorkspaceError, match="no path"):
            require("edit_files").run(db, {"edits": [{"old": "a", "new": "b"}]})

    def test_the_old_single_shape_still_works(self, db, never_the_real_workspace):
        """`path`/`old`/`new` with no `edits` at all — what a replayed `edit_file` arrives as."""
        one = _file(never_the_real_workspace, "store.py", "alpha\n")
        out = require("edit_files").run(db, {"path": str(one), "old": "alpha", "new": "A"})
        assert "error" not in out, out
        assert one.read_text() == "A\n"
