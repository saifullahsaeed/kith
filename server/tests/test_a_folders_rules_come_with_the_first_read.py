"""A folder's CLAUDE.md and AGENTS.md arrive with the first file opened under it.

Measured on sadeef-v2 (a root CLAUDE.md, fifty-two AGENTS.md files): builders opened by reading
that chain by hand, out of thirty rounds, and nineteen of twenty ran out before writing. Claude
Code attaches a folder's instructions to the first read under it; so does this now.

Once per conversation for the conversation — its history replays old results, so a second copy
each turn is pure cost — and once per run for a workflow agent, whose memory is its own.
"""

from __future__ import annotations

import os

import pytest

from kith.infra import workspace
from kith.kernel import session_context
from kith.services import instructions, touched
from kith.tools import computer


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace.paths.settings, "WORKSPACE_DIR", str(tmp_path))
    top = tmp_path / "proj"
    (top / ".git").mkdir(parents=True)
    (top / "CLAUDE.md").write_text("ROOT CLAUDE RULES\n")
    (top / "AGENTS.md").write_text("ROOT AGENTS RULES\n")
    (top / "backend" / "app").mkdir(parents=True)
    (top / "backend" / "AGENTS.md").write_text("BACKEND RULES\n")
    (top / "backend" / "app" / "models.py").write_text("x = 1\n")
    (top / "backend" / "app" / "views.py").write_text("y = 2\n")
    (top / "docs").mkdir()
    (top / "docs" / "notes.md").write_text("notes\n")
    return top


def _read(db, *paths):
    out = computer.read_file(db, {"paths": [str(p) for p in paths]})
    return out if isinstance(out, str) else str(out)


class TestWhatComesWithARead:
    def test_the_chain_from_the_top_down_comes_with_the_first_read(self, db, repo):
        with session_context.a_turn(), session_context.working_in("conv1"):
            out = _read(db, repo / "backend" / "app" / "models.py")

        assert "x = 1" in out
        assert out.index("ROOT CLAUDE RULES") < out.index("ROOT AGENTS RULES") < out.index("BACKEND RULES")

    def test_a_second_read_in_the_same_folder_brings_nothing_again(self, db, repo):
        with session_context.a_turn(), session_context.working_in("conv1"):
            _read(db, repo / "backend" / "app" / "models.py")
            out = _read(db, repo / "backend" / "app" / "views.py")

        assert "RULES" not in out

    def test_a_new_folder_brings_only_what_it_adds(self, db, repo):
        with session_context.a_turn(), session_context.working_in("conv1"):
            _read(db, repo / "docs" / "notes.md")
            out = _read(db, repo / "backend" / "app" / "models.py")

        assert "BACKEND RULES" in out
        assert "ROOT AGENTS RULES" not in out

    def test_the_walk_stops_at_the_repository_top(self, db, repo):
        (repo.parent / "AGENTS.md").write_text("OUTSIDE THE REPO\n")
        with session_context.a_turn(), session_context.working_in("conv1"):
            out = _read(db, repo / "docs" / "notes.md")

        assert "OUTSIDE THE REPO" not in out

    def test_reading_the_rules_file_itself_does_not_attach_a_second_copy(self, db, repo):
        with session_context.a_turn(), session_context.working_in("conv1"):
            out = _read(db, repo / "AGENTS.md")

        assert out.count("ROOT AGENTS RULES") == 1

    def test_a_claude_md_that_copies_the_agents_md_is_shown_once(self, db, repo):
        (repo / "CLAUDE.md").write_text("ROOT AGENTS RULES\n")
        with session_context.a_turn(), session_context.working_in("conv1"):
            out = _read(db, repo / "docs" / "notes.md")

        assert out.count("ROOT AGENTS RULES") == 1

    def test_a_failed_read_attaches_nothing_and_the_next_one_still_does(self, db, repo):
        with session_context.a_turn(), session_context.working_in("conv1"):
            failed = computer.read_file(db, {"paths": []})
            out = _read(db, repo / "docs" / "notes.md")

        assert "RULES" not in str(failed)
        assert "ROOT AGENTS RULES" in out

    def test_a_write_into_a_folder_brings_its_rules(self, db, repo):
        with session_context.a_turn(), session_context.working_in("conv1"):
            out = computer.write_file(db, {"path": str(repo / "backend" / "new.py"), "content": "z = 3\n"})

        assert "BACKEND RULES" in str(out)


class TestHowLongItIsRemembered:
    def test_the_conversation_is_not_shown_them_again_next_turn(self, db, repo):
        with session_context.a_turn(), session_context.working_in("conv1"):
            _read(db, repo / "backend" / "app" / "models.py")
        with session_context.a_turn(), session_context.working_in("conv1"):
            out = _read(db, repo / "backend" / "app" / "views.py")

        assert "RULES" not in out

    def test_a_rules_file_that_changed_comes_back(self, db, repo):
        with session_context.a_turn(), session_context.working_in("conv1"):
            _read(db, repo / "backend" / "app" / "models.py")
        rules = repo / "backend" / "AGENTS.md"
        rules.write_text("BACKEND RULES, AMENDED\n")
        os.utime(rules, ns=(rules.stat().st_atime_ns, rules.stat().st_mtime_ns + 10**9))
        with session_context.a_turn(), session_context.working_in("conv1"):
            out = _read(db, repo / "backend" / "app" / "views.py")

        assert "BACKEND RULES, AMENDED" in out
        assert "ROOT AGENTS RULES" not in out

    def test_the_manifest_lists_them_as_opened(self, db, repo):
        with session_context.a_turn(), session_context.working_in("conv1"):
            _read(db, repo / "docs" / "notes.md")

        assert str(repo / "AGENTS.md") in [row["path"] for row in touched.seen(db, "conv1")]

    def test_an_agent_is_shown_them_even_when_its_handler_has_them(self, db, repo):
        # An agent is a conversation of its own, so its handler's ledger does not decide what it
        # is shown — and what it is shown is recorded as its own.
        with session_context.a_turn(), session_context.working_in("conv1"):
            _read(db, repo / "docs" / "notes.md")
        with session_context.a_turn(), session_context.working_in("agent1"):
            out = _read(db, repo / "docs" / "notes.md")

        assert "ROOT AGENTS RULES" in out
        assert str(repo / "AGENTS.md") in [row["path"] for row in touched.seen(db, "agent1")]


class TestSize:
    def test_a_huge_rules_file_is_cut_at_a_line_and_says_where_the_rest_is(self, db, repo, monkeypatch):
        monkeypatch.setattr(instructions, "FILE_CHARS", 200)
        (repo / "AGENTS.md").write_text("".join(f"rule {n}\n" for n in range(500)))
        with session_context.a_turn(), session_context.working_in("conv1"):
            out = _read(db, repo / "docs" / "notes.md")

        assert "rule 0" in out and "rule 499" not in out
        assert f"read `{repo / 'AGENTS.md'}` for the rest" in out
        row = next(r for r in touched.seen(db, "conv1") if r["path"] == str(repo / "AGENTS.md"))
        assert row["extent"].startswith("1-") and row["extent"].endswith("of 500")

    def test_what_does_not_fit_in_one_result_is_named_and_comes_next_time(self, db, repo, monkeypatch):
        monkeypatch.setattr(instructions, "RESULT_CHARS", 30)
        with session_context.a_turn(), session_context.working_in("conv1"):
            first = _read(db, repo / "backend" / "app" / "models.py")
            second = _read(db, repo / "backend" / "app" / "views.py")

        assert "ROOT CLAUDE RULES" in first and "BACKEND RULES" not in first
        assert str(repo / "backend" / "AGENTS.md") in first
        assert "ROOT AGENTS RULES" in second or "BACKEND RULES" in second
