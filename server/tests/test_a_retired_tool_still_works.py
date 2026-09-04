"""A tool that was merged away still answers under its old name.

Sixteen tools were schemas paid for on every round to offer choices that were really
parameters: one edit or several, a one-off reminder or a repeating one, a page that needs a
browser or one that does not. Merging them is worth about 2,700 prompt tokens a round and,
more than that, it drops the number of near-identical names the model has to choose between.

The cost of removing a tool, though, is paid by the model in rounds — it reaches for
`edit_file` because it has reached for `edit_file` a thousand times, gets "unknown tool", and
spends a round finding out. That cost is avoidable, because the merges were chosen so the
surviving tool can answer the retired one's question: the arguments carry over, sometimes
renamed, sometimes with the thing the old tool implied spelled out. So `run_tool` completes
the call instead of correcting it, and says which tool did it.

**What these tests are really protecting is that the translation answers the same question.**
A redirect that quietly runs something else is worse than "unknown tool", because it looks
like it worked. So each one below asserts the *effect* — the edit landed, the listing has the
file in it, the fetch translated to the right direction — rather than that a redirect happened.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith import tools
from kith.infra import workspace as ws
from kith.tools import registry
from kith.tools.aliases import RETIRED, Retired, translate


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
    return tmp_path


class TestTheMapItself:
    @pytest.mark.parametrize("old,gone", sorted(RETIRED.items(), key=lambda pair: pair[0]))
    def test_what_took_over_actually_exists(self, old: str, gone: Retired) -> None:
        """A retirement pointing at a tool that is not registered is a dead end that reads as
        a working redirect."""
        assert registry.get(gone.now) is not None, f"{old} redirects to missing {gone.now}"

    @pytest.mark.parametrize("old", sorted(RETIRED))
    def test_the_retired_name_is_really_gone(self, old: str) -> None:
        """An entry for a tool that still exists is at best dead weight and at worst a
        hijack — which is what it was, before `run_tool` learned to check."""
        assert registry.get(old) is None, f"{old} is still registered; drop its RETIRED entry"

    @pytest.mark.parametrize("old,gone", sorted(RETIRED.items(), key=lambda pair: pair[0]))
    def test_every_translated_argument_is_one_the_survivor_declares(self, old: str, gone: Retired) -> None:
        """A rename or an `add` pointing at a parameter that does not exist is a translation
        that silently drops what it was carrying.

        This is the check that was missing when `link_folder`'s unlink shipped broken: with no
        folder, `translate` produced *no* `directory` key at all, `update_project` gates on the
        key being present, and the call reported success with the folder still linked. The
        entry looked right because every key it did name was real.
        """
        survivor = registry.require(gone.now)
        declared = set(survivor.properties)
        named = set(gone.rename.values()) | set(gone.add)
        assert named <= declared, f"{old} -> {gone.now} names {named - declared}, which it has no"

    @pytest.mark.parametrize("old,gone", sorted(RETIRED.items(), key=lambda pair: pair[0]))
    def test_the_old_name_reaches_the_new_tool(self, old: str, gone: Retired, db: Path) -> None:
        """Dispatched for real, not asserted about.

        Every entry was hand-written and only five of the sixteen were ever called end to end.
        A wrong `now`, a typo in a rename, or an `add` the survivor rejects all look fine until
        something dispatches them — so this dispatches all of them. Arguments are deliberately
        thin: the point is that the call *arrives*, and a tool complaining about a missing
        argument has already proved that.
        """
        result = tools.run_tool(old, {}, db)

        assert isinstance(result, dict)
        assert "unknown tool" not in str(result.get("error", "")), result
        # The note is the receipt that the redirect happened rather than something coincidental.
        said = str(result.get("result", "")) + str(result.get("error", ""))
        assert gone.now in said, f"{old} did not report reaching {gone.now}: {result}"

    def test_a_live_tool_is_never_hijacked(self, db: Path, monkeypatch) -> None:
        """The guard, tested directly. A retirement written before its merge landed used to
        take over the tool it named — it cost a fifth of the suite in one commit."""
        monkeypatch.setitem(RETIRED, "read_file", Retired("glob"))
        result = tools.run_tool("read_file", {"paths": ["nothing-here.txt"]}, db)
        assert "glob" not in str(result), "a registered tool must win over a RETIRED entry"


class TestTheCallCompletes:
    def test_edit_file_still_edits(self, db: Path, workspace_root) -> None:
        (workspace_root / "m.py").write_text("def keep():\n    pass\n")
        result = tools.run_tool(
            "edit_file",
            {"path": "m.py", "old": "def keep():\n    pass", "new": "def keep():\n    return 1"},
            db,
        )
        assert result["ok"] is True, result
        assert (workspace_root / "m.py").read_text() == "def keep():\n    return 1\n"

    def test_and_says_which_tool_did_it(self, db: Path, workspace_root) -> None:
        """Silence would work for ever and teach nothing; the note is how it stops paying a
        translation on every edit."""
        (workspace_root / "m.py").write_text("x = 1\n")
        result = tools.run_tool("edit_file", {"path": "m.py", "old": "x = 1", "new": "x = 2"}, db)
        assert "edit_files" in result["result"]["note"]

    def test_list_files_still_lists(self, db: Path, workspace_root) -> None:
        (workspace_root / "here.txt").write_text("hi")
        result = tools.run_tool("list_files", {}, db)
        assert result["ok"] is True, result
        assert "here.txt" in str(result["result"])

    def test_history_translates_its_limit(self, db: Path) -> None:
        """`history(limit=…)` and `changes(commits=…)` are the same number under two names,
        and an explicit one must beat the default `add` supplies."""
        assert translate(RETIRED["history"], {"limit": 5}) == {"commits": 5}
        assert translate(RETIRED["history"], {}) == {"commits": 20}

    def test_check_remote_spells_out_what_it_implied(self, db: Path) -> None:
        """It never had to say `direction`, because being itself was saying it."""
        assert translate(RETIRED["check_remote"], {}) == {"direction": "check"}

    def test_a_retired_name_is_not_refused_by_a_narrowed_toolset(self, db: Path, workspace_root) -> None:
        """The allow-list is asked about the surviving name, not the retired one. Otherwise
        the merge invents a restriction nobody chose: `edit_file` forbidden in a phase whose
        whole point is that `edit_files` is allowed."""
        (workspace_root / "m.py").write_text("x = 1\n")
        result = tools.run_tool(
            "edit_file",
            {"path": "m.py", "old": "x = 1", "new": "x = 2"},
            db,
            allow={"edit_files"},
        )
        assert result["ok"] is True, result

    def test_but_a_narrowed_toolset_still_holds(self, db: Path, workspace_root) -> None:
        """And the other direction: retirement is not a way around the reserve."""
        (workspace_root / "m.py").write_text("x = 1\n")
        result = tools.run_tool(
            "edit_file",
            {"path": "m.py", "old": "x = 1", "new": "x = 2"},
            db,
            allow={"read_file"},
        )
        assert result["ok"] is False
        assert (workspace_root / "m.py").read_text() == "x = 1\n", "and nothing was written"
