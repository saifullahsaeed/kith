"""The last rounds of a turn can still edit a file, verify it, and save the point.

The landing reserve holds back a few rounds so a turn cannot spend its whole budget
gathering and end with nothing written down. What it may use was a list — and the list had
`write_file` and not `edit_file`. So a turn that reached those rounds mid-implementation was
left holding only the wholesale rewrite, on existing source it had not fully read.

He noticed and stopped, and parked the task on a question:

    "the available file tool exposes read/write only, not an edit/patch operation, and
    rewriting these existing files wholesale would risk unrelated code loss. Please
    provide/enable an edit-capable tool"

Which is `edit_file`'s own docstring read back to us, correctly, by something we had quietly
disarmed. `commit` was missing too — from the one phase whose entire job is to land the work.
"""

from __future__ import annotations

from kith.services.agent_loop import _LANDING_TOOLS


class TestItCanChangeAFileSafely:
    def test_the_surgical_edit_is_available(self):
        assert "edit_file" in _LANDING_TOOLS
        assert "edit_files" in _LANDING_TOOLS

    def test_it_is_not_left_with_only_the_lossy_one(self):
        """`write_file` alone is the trap: he regenerates from what he remembers reading, so
        anything he did not re-emit is gone."""
        assert not ("write_file" in _LANDING_TOOLS and "edit_file" not in _LANDING_TOOLS)

    def test_write_file_stays_for_genuinely_new_files(self):
        """A handoff note is a new file, and that is what it is for."""
        assert "write_file" in _LANDING_TOOLS


class TestItCanCheckAndSave:
    def test_it_can_verify_what_it_just_wrote(self):
        """Claiming done without checking is the failure `_verify_done` exists for. Landing is
        exactly when the check belongs."""
        assert {"check_code", "run_tests", "diagnostics"} <= _LANDING_TOOLS

    def test_it_can_commit(self):
        """Absent from the one phase whose whole job is landing — part of why five hours of
        work ended with no commits at all."""
        assert "commit" in _LANDING_TOOLS
        assert "changes" in _LANDING_TOOLS


class TestItStillStopsGathering:
    def test_the_reserve_still_does_its_job(self):
        """Widening it to let him finish must not turn it back into an unbounded turn — the
        reserve exists because research expands to fill whatever it is given."""
        assert not ({"web_search", "fetch_url", "browse_page", "search_sources"} & _LANDING_TOOLS)

    def test_and_it_cannot_start_new_work(self):
        assert "add_task" not in _LANDING_TOOLS
        assert "create_project" not in _LANDING_TOOLS


class TestEveryNameIsReal:
    def test_nothing_in_the_list_is_a_tool_that_does_not_exist(self):
        """A name with no tool behind it is silently ignored, so the list would claim a
        capability the phase does not have — which is how this bug read from outside."""
        from kith.tools import registry

        assert not (_LANDING_TOOLS - set(registry.names()))
