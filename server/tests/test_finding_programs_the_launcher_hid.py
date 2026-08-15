"""Finding a program when the environment we were handed is not the user's.

A macOS app launched from Finder, the Dock or Spotlight inherits `launchd`'s environment, not
a shell's — commonly four entries against the thirty-nine on a developer's terminal. Electron
passes that to the server unchanged, because there is nothing else it could pass.

Measured under `env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin`, which is that environment:

    git                          found      the Xcode shim lives in /usr/bin
    clangd                       found
    rg                           NOT FOUND
    pyright-langserver           NOT FOUND
    typescript-language-server   NOT FOUND

The language servers are meant to be optional and their absence is reported honestly, so that
half is only a degradation. **ripgrep is the half that was a bug**: `files.py` falls back to
`grep -E`, whose basic-regex semantics make `a|b` a literal string, and the comment above that
fallback already records it as "the bug that made the tool look broken most of the time". On a
developer's machine ripgrep is found; in every shipped copy it was not, so users only ever had
the broken path.

The subtle half of the fix, and the one worth a test of its own: finding the binary is not
enough. `grep` assembles a command *string* for a shell, so a bare `rg` would be
resolved a second time against the same broken `PATH`. It has to be the absolute path.
"""

from __future__ import annotations

import os
import shutil

import pytest

from kith.infra import executables

#: What a Finder-launched app actually gets.
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


@pytest.fixture(autouse=True)
def a_forgotten_resolution():
    """The answer is remembered for the life of the process, so each test starts fresh."""
    executables.forget()
    yield
    executables.forget()


class TestRecoveringAPath:
    def test_the_known_directories_are_added_when_they_exist(self, monkeypatch):
        monkeypatch.setenv("PATH", LAUNCHD_PATH)
        got = executables.search_path().split(os.pathsep)
        assert "/usr/bin" in got, "what we were given is kept"
        # At least one of the well-known locations exists on any real machine.
        assert len(got) > LAUNCHD_PATH.count(":") + 1, got

    def test_nothing_that_does_not_exist_is_added(self, monkeypatch):
        """Every entry is a directory that is really there — a `PATH` full of hopeful guesses
        makes every lookup slower and every failure harder to read."""
        monkeypatch.setenv("PATH", LAUNCHD_PATH)
        from pathlib import Path

        assert all(Path(one).is_dir() for one in executables.search_path().split(os.pathsep))

    def test_what_we_were_given_keeps_its_precedence(self, monkeypatch, tmp_path):
        """Anything deliberately set for this process still wins. A resolver that reordered
        `PATH` would quietly change which of two installed versions runs."""
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{LAUNCHD_PATH}")
        assert executables.search_path().split(os.pathsep)[0] == str(tmp_path)

    def test_there_are_no_duplicates(self, monkeypatch):
        monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}/usr/bin{os.pathsep}/bin")
        got = executables.search_path().split(os.pathsep)
        assert len(got) == len(set(got))

    def test_the_answer_is_remembered(self, monkeypatch):
        """It cannot change while the process runs, and asking the login shell is the
        expensive part — measured at about 130ms once."""
        monkeypatch.setenv("PATH", LAUNCHD_PATH)
        assert executables.search_path() is executables.search_path()


class TestWhenThereIsNoShellToAsk:
    def test_a_missing_shell_is_not_an_error(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/nonexistent/shell")
        monkeypatch.setenv("PATH", LAUNCHD_PATH)
        assert "/usr/bin" in executables.search_path()

    def test_a_shell_that_will_not_answer_is_not_an_error(self, monkeypatch):
        def refuse(*_args, **_kwargs):
            raise OSError("no")

        monkeypatch.setattr(executables.subprocess, "run", refuse)
        monkeypatch.setenv("PATH", LAUNCHD_PATH)
        assert "/usr/bin" in executables.search_path()

    def test_a_shell_that_prints_a_banner_does_not_poison_the_answer(self, monkeypatch, tmp_path):
        """The sentinel exists for version managers and motd output. Without it a login
        banner becomes a `PATH` entry."""

        class Done:
            stdout = f"Welcome!\nnvm: v20\n{executables._MARK[0]}{tmp_path}{executables._MARK[1]}\n"

        monkeypatch.setattr(executables.subprocess, "run", lambda *a, **k: Done())
        monkeypatch.setenv("PATH", LAUNCHD_PATH)
        got = executables.search_path().split(os.pathsep)
        assert str(tmp_path) in got
        assert not any("Welcome" in one for one in got)

    def test_an_unfenced_answer_is_ignored_rather_than_guessed_at(self, monkeypatch):
        class Done:
            stdout = "/some/path/without/markers\n"

        monkeypatch.setattr(executables.subprocess, "run", lambda *a, **k: Done())
        monkeypatch.setenv("PATH", LAUNCHD_PATH)
        assert "/some/path/without/markers" not in executables.search_path()


class TestWhatItRecovers:
    def test_it_finds_what_plain_which_cannot(self, monkeypatch):
        """The point of the exercise, against whatever this machine actually has installed.

        Skips rather than fails when the minimal PATH already finds everything — that is a
        machine with nothing installed outside /usr/bin, not a broken resolver.
        """
        monkeypatch.setenv("PATH", LAUNCHD_PATH)
        recovered = [
            name
            for name in ("node", "npm", "brew", "cargo", "go", "python3.13", "rg", "git")
            if executables.which(name) and not shutil.which(name)
        ]
        if not recovered:
            pytest.skip("nothing on this machine lives outside the minimal PATH")
        assert recovered

    def test_git_is_found_either_way(self, monkeypatch):
        """It was never actually broken — /usr/bin/git is the Xcode shim — and it must stay
        that way, because history is not optional the way a language server is."""
        monkeypatch.setenv("PATH", LAUNCHD_PATH)
        assert executables.which("git")

    def test_a_program_that_is_not_installed_is_still_not_found(self, monkeypatch):
        monkeypatch.setenv("PATH", LAUNCHD_PATH)
        assert executables.which("definitely-not-a-real-program-xyzzy") is None


class TestTheAbsolutePathReachesTheShell:
    def test_grep_names_ripgrep_by_its_full_path(self, monkeypatch, tmp_path):
        """Finding it and then writing `rg` into a shell command would look correct and change
        nothing, because the shell resolves argv[0] against the same broken PATH."""
        from kith.infra import workspace as ws

        monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        (tmp_path / "a.txt").write_text("hello\n")

        seen: list[str] = []

        def capture(command, **_kwargs):
            seen.append(command)

            class R:
                exit_code = 1
                output = ""

            return R()

        monkeypatch.setattr(ws.files, "run_command", capture)
        monkeypatch.setattr(
            executables, "which", lambda name: "/opt/somewhere/bin/rg" if name == "rg" else None
        )
        ws.grep("hello", ".")
        assert seen and seen[0].startswith("/opt/somewhere/bin/rg"), seen
