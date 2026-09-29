"""Asking the same question of changed files is not thrashing.

The thrash-guard stops the model running an identical call over and over. It keyed on the
call alone, and the one workflow built on repetition is the design loop: the skill says run
the checker, fix what it names, run it again — so the third identical
`check_design.py screens/x.html` is the method, not a stall, and the file beneath it is
different every time.

Watched twice in one session. The guard refused, and the model's answer was to disguise the
command — wrapping the same script in `python3 - <<'PY'` to get a different signature. A guard
that teaches evasion is worse than no guard, because the evasion also escapes the cases the
guard was right about.

So a call's signature carries a write epoch, and anything that changes files moves it on.
"""

from __future__ import annotations

from kith.services import agent_loop


class TestWhatCountsAsAChange:
    def test_writing_a_file_does(self):
        assert "write_file" in agent_loop._CHANGES_FILES

    def test_editing_does(self):
        assert "edit_files" in agent_loop._CHANGES_FILES

    def test_shell_deliberately_does_not(self):
        """Including it would switch the guard off rather than widen it.

        A repeated shell command would bump the epoch itself, so every repeat would land under
        a fresh signature and running the same command forty times would never be caught.
        """
        assert "shell" not in agent_loop._CHANGES_FILES


class TestTheGuardItself:
    def test_a_third_identical_call_is_still_a_repeat(self):
        assert agent_loop._is_repeat("shell", seen=2) is True

    def test_the_first_retry_is_free(self):
        assert agent_loop._is_repeat("shell", seen=1) is False

    def test_a_signature_carries_the_epoch(self):
        """What makes check → edit → check three distinct questions rather than one repeated."""
        import json

        def sig(name: str, args: dict, epoch: int) -> str:
            return f"{name}:{json.dumps(args, sort_keys=True, default=str)}@{epoch}"

        call = ("shell", {"command": "python3 check_design.py a.html"})
        assert sig(*call, 0) != sig(*call, 1)
        assert sig(*call, 1) == sig(*call, 1)

    def test_the_epoch_is_in_the_loop_that_builds_signatures(self):
        """Guards the wiring, not just the constant — the two came apart once already."""
        import inspect

        source = inspect.getsource(agent_loop)
        assert "@{write_epoch}" in source
        assert "_CHANGES_FILES" in source


class TestAWriteIsNeverAStall:
    """Blocking a read costs a round; blocking a write loses the write.

    The refusal used to come back under a `note` key, which reads as an acknowledgement — so
    `update_design_system`, called twice in one turn, "reported success and wrote nothing".
    It was caught only because Kith grepped the file instead of believing the answer.
    """

    def test_a_repeated_write_still_runs(self):
        assert agent_loop._is_repeat("write_file", seen=5) is False
        assert agent_loop._is_repeat("edit_files", seen=5) is False

    def test_including_the_design_system_which_is_also_a_write(self):
        assert agent_loop._is_repeat("update_design_system", seen=5) is False

    def test_a_repeated_read_is_still_caught(self):
        assert agent_loop._is_repeat("grep", seen=2) is True

    def test_a_refusal_cannot_be_read_as_success(self):
        answer = agent_loop._run({"repeat": True, "name": "grep", "arguments": {}}, run=None)
        assert "error" in answer and "note" not in answer
        assert "nothing was done" in answer["error"]


class TestAPluginsStateIsAChangeToo:
    """`look → resize → look` was refused as a repeat: a resize changes the page, not a file."""

    def _run(self, *calls):
        last: dict[str, str] = {}
        return [agent_loop._plugin_moved(name, f"{name}:{args}", last) for name, args in calls]

    def test_a_different_call_to_the_plugin_moves_the_epoch(self):
        moved = self._run(
            ("plugin__browser__look", "{}"),
            ("plugin__browser__resize", '{"preset": "laptop"}'),
            ("plugin__browser__look", "{}"),
        )
        assert moved == [False, True, True]

    def test_the_same_call_again_does_not(self):
        """Otherwise `look` forty times would never be caught — the `shell` argument again."""
        look = ("plugin__browser__look", "{}")
        assert self._run(look, look, look) == [False, False, False]

    def test_other_tools_and_other_plugins_are_untouched(self):
        moved = self._run(
            ("plugin__browser__look", "{}"),
            ("grep", '{"pattern": "x"}'),
            ("plugin__sketchpad__draw", "{}"),
            ("plugin__browser__look", "{}"),
        )
        assert moved == [False, False, False, False]

    def test_the_built_in_browser_counts_its_acts_the_same_way(self):
        """One tool now, so its acts are its commands: a click between two reads is a change."""
        read = ("browser", '{"act": "read"}')
        moved = self._run(read, ("browser", '{"act": "click", "text": "Next"}'), read, read)
        assert moved == [False, True, True, False]
