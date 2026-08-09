"""One read of a source file should be one call.

`read_file` windowed by lines (400) and by bytes (8,000), and shared that byte figure with
`shell`. They are different problems: clipping arbitrary command output is sensible, because
the interesting part of a build log is the end and the rest is noise, while a source file is
not noise.

8,000 characters is about 160 lines — under a typical React component. Measured on a real
project: a 271-line page returned 160 lines and a 172-line page returned 158, so reading it
whole took *two* calls and the second fetched fourteen lines. A second call is a second
round, and a round re-sends the ~20,000-token prompt floor. Twenty thousand tokens for
fourteen lines of TSX, on a tool called 201 times in two days.
"""

from __future__ import annotations

import pytest

from kith.infra import workspace


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
    return tmp_path


def a_file(root, name: str, lines: int, width: int = 60) -> int:
    body = "\n".join(f"const value{i} = {'x' * width};" for i in range(lines))
    (root / name).write_text(body)
    return lines


class TestFinishingRatherThanComingBack:
    def test_a_typical_component_arrives_in_one_call(self, workspace_root):
        """The exact shape that was costing two: ~270 lines of TSX."""
        a_file(workspace_root, "Login.tsx", 271)

        out = workspace.read_file("Login.tsx")

        assert "read with offset=" not in out, "still asking for a second call"
        assert "const value270" in out, "the last line never arrived"

    def test_a_file_just_over_the_limit_is_finished_not_cut(self, workspace_root):
        """Stopping fourteen lines short costs a whole round to recover."""
        a_file(workspace_root, "StatusPage.tsx", 172)

        out = workspace.read_file("StatusPage.tsx")

        assert "read with offset=" not in out
        assert "const value171" in out

    def test_a_genuinely_large_file_still_stops(self, workspace_root):
        """The overshoot is an allowance, not the removal of the limit."""
        a_file(workspace_root, "huge.ts", 2_000)

        out = workspace.read_file("huge.ts")

        assert "read with offset=" in out
        assert len(out) < workspace.files._READ_LIMIT + workspace.files._READ_OVERSHOOT + 500

    def test_a_truncated_read_still_says_where_to_continue(self, workspace_root):
        a_file(workspace_root, "huge.ts", 2_000)

        out = workspace.read_file("huge.ts")

        assert "offset=" in out
        offset = int(out.rsplit("offset=", 1)[1].split()[0].rstrip("]"))
        rest = workspace.read_file("huge.ts", offset=offset)
        assert f"{offset:6d}\t" in rest, "the offset it gave does not start where it stopped"

    def test_the_two_halves_meet_with_no_gap(self, workspace_root):
        """The reason the offset is quoted at all — a missing line is invisible."""
        a_file(workspace_root, "huge.ts", 2_000)
        first = workspace.read_file("huge.ts")
        offset = int(first.rsplit("offset=", 1)[1].split()[0].rstrip("]"))

        assert f"const value{offset - 2} " in first, "the line before the offset is missing"
        assert f"const value{offset - 1} " in workspace.read_file("huge.ts", offset=offset)


class TestTheLimitsAreSeparate:
    def test_reading_is_allowed_more_than_a_command_prints(self):
        assert workspace.files._READ_LIMIT > workspace.base._OUTPUT_LIMIT

    def test_an_explicit_window_is_still_honoured(self, workspace_root):
        a_file(workspace_root, "f.ts", 500)
        out = workspace.read_file("f.ts", offset=10, limit=5)
        assert "const value9 " in out and "const value13 " in out
        assert "const value14 " not in out

    def test_shell_output_is_still_clipped_at_its_own_limit(self, workspace_root):
        """Raising the read budget must not raise what a command may dump."""
        result = workspace.run_command("for i in $(seq 1 4000); do echo 'a line of output'; done")
        assert len(result.output) <= workspace.base._OUTPUT_LIMIT + 200
