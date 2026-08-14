"""diagnostics, references, definition, rename — and doing without them.

Two halves. The first runs with no language server at all, which is the state most machines
are in and therefore the one that has to behave well: readable sentences, no exceptions, and
— the part that costs real money if it is wrong — the four tool schemas left out of the
prompt entirely rather than carried on every round to advertise a capability that will always
answer "not installed".

The second runs against real pyright and asserts the thing that justifies the whole layer:
that `references` and `rename` know the difference between a use of a name and the same
letters in a comment or a string. A regex does not, and this project has a scar from finding
that out the hard way.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from kith.engine.code.lsp.manager import CANDIDATES, Manager, Unavailable
from kith.tools import registry, tool_schemas
from kith.tools.semantics import NEEDS_A_LANGUAGE_SERVER

PYRIGHT = shutil.which("pyright-langserver") or str(Path(sys.executable).parent / "pyright-langserver")
HAVE_PYRIGHT = Path(PYRIGHT).is_file()


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    from kith.infra import workspace

    monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def fresh_manager(monkeypatch):
    """A manager of its own, so a test never inherits another test's warm server.

    Reached through `sys.modules` because `from kith.engine.code.lsp import manager` gives the
    *instance* — the package re-exports it under the same name as its module, so the module
    is only reachable this way. `conftest.py` has the same note about `kith.autonomy.runner`;
    it is the second time this shape has caught something in this codebase.

    Every module that did `from …manager import manager` holds its own reference, so each one
    is patched — the same reason `never_the_real_database` sweeps for `AGENT_DB_PATH`.
    """
    import sys

    one = Manager()
    for name, module in list(sys.modules.items()):
        if name.startswith("kith.") and isinstance(getattr(module, "manager", None), Manager):
            monkeypatch.setattr(module, "manager", one, raising=False)
    yield one
    one.shutdown()


class TestFindingAServer:
    def test_the_project_root_is_the_nearest_marker(self, tmp_path, fresh_manager):
        (tmp_path / "outer").mkdir()
        (tmp_path / "outer" / ".git").mkdir()
        (tmp_path / "outer" / "inner").mkdir()
        (tmp_path / "outer" / "inner" / "package.json").write_text("{}")
        deep = tmp_path / "outer" / "inner" / "a.ts"
        deep.write_text("export const x = 1;\n")

        # The nearest one, not the outermost: in a monorepo the outer .git is the wrong root
        # and gives the server the wrong tsconfig.
        assert fresh_manager.project_root(deep) == (tmp_path / "outer" / "inner").resolve()

    def test_a_loose_file_gets_its_own_folder_as_the_root(self, tmp_path, fresh_manager):
        loose = tmp_path / "scratch.py"
        loose.write_text("x = 1\n")
        assert fresh_manager.project_root(loose) == tmp_path.resolve()

    def test_a_project_server_beats_one_on_the_path(self, tmp_path, fresh_manager, monkeypatch):
        """The project's own version is the one its config is written for."""
        binary = tmp_path / "node_modules" / ".bin" / "typescript-language-server"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/typescript-language-server")

        found = fresh_manager.find_binary("typescript", tmp_path)

        assert found is not None
        assert found[1] == str(binary)

    def test_a_venv_server_is_found(self, tmp_path, fresh_manager, monkeypatch):
        binary = tmp_path / ".venv" / "bin" / "pyright-langserver"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        found = fresh_manager.find_binary("python", tmp_path)
        assert found is not None and found[1] == str(binary)

    def test_a_language_with_no_server_installed_is_simply_unavailable(
        self, tmp_path, fresh_manager, monkeypatch
    ):
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        monkeypatch.setattr(fresh_manager, "find_binary", lambda *_a, **_k: None)
        (tmp_path / "a.rs").write_text("fn main() {}\n")
        assert fresh_manager.available_for(tmp_path / "a.rs") is False

    def test_every_candidate_names_something_installable(self):
        """The message someone reads is the only route out of "it doesn't work"."""
        for family, options in CANDIDATES.items():
            assert options, f"{family} has no candidates"
            for one in options:
                assert one.install.strip(), f"{one.binary} has no install hint"

    def test_a_typescript_project_is_not_served_by_a_python_server(
        self, tmp_path, fresh_manager, monkeypatch
    ):
        """The narrow question, and it is the one that costs tokens to get wrong.

        pyright lives in Kith's own environment, so "is any server installed" is always yes —
        and a pure-TypeScript project would be offered four semantic tools on every round
        that could only ever answer "no typescript language server installed".
        """
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "a.ts").write_text("export const x = 1;\n")
        monkeypatch.setattr(
            fresh_manager,
            "find_binary",
            lambda family, _root: ("stub", "/usr/bin/pyright") if family == "python" else None,
        )

        assert fresh_manager.any_available(tmp_path) is False

    def test_a_python_project_with_a_python_server_is_served(self, tmp_path, fresh_manager, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        monkeypatch.setattr(
            fresh_manager,
            "find_binary",
            lambda family, _root: ("stub", "/usr/bin/pyright") if family == "python" else None,
        )

        assert fresh_manager.any_available(tmp_path) is True

    def test_a_folder_with_no_marker_asks_about_everything(self, tmp_path, fresh_manager, monkeypatch):
        """A directory of loose scripts is not evidence either way."""
        (tmp_path / "scratch.py").write_text("x = 1\n")
        monkeypatch.setattr(
            fresh_manager,
            "find_binary",
            lambda family, _root: ("stub", "/usr/bin/gopls") if family == "go" else None,
        )

        assert fresh_manager.any_available(tmp_path) is True

    def test_a_file_we_cannot_even_parse_says_so_plainly(self, tmp_path, fresh_manager):
        (tmp_path / "notes.xyz").write_text("hello")
        message = fresh_manager.how_to_get_one(tmp_path / "notes.xyz")
        assert ".xyz" in message or "don't know" in message


class TestWithNoLanguageServer:
    """The ordinary state of most machines, and it has to be an ordinary state."""

    @pytest.fixture(autouse=True)
    def nothing_installed(self, fresh_manager, monkeypatch):
        monkeypatch.setattr(fresh_manager, "find_binary", lambda *_a, **_k: None)
        return fresh_manager

    def test_the_schemas_are_left_out_of_the_prompt_entirely(self):
        """A tool schema is carried on every round of every turn. Paying that to advertise
        four tools that can only answer "not installed" is the exact waste the toolset lists
        exist to prevent."""
        with_server = {one["function"]["name"] for one in tool_schemas(language_server=True)}
        without = {one["function"]["name"] for one in tool_schemas(language_server=False)}

        assert with_server >= NEEDS_A_LANGUAGE_SERVER
        assert not (NEEDS_A_LANGUAGE_SERVER & without)
        # And nothing else was dropped along with them.
        assert with_server - without == set(NEEDS_A_LANGUAGE_SERVER)

    def test_not_filtering_is_the_default(self):
        """Every caller with no opinion must keep getting everything."""
        names = {one["function"]["name"] for one in tool_schemas()}
        assert names >= NEEDS_A_LANGUAGE_SERVER

    def test_calling_one_anyway_explains_rather_than_raising(self, workspace_root, tmp_path):
        """The model may still name a tool it saw earlier in the conversation. An exception
        ends the round; a sentence lets it try grep instead."""
        (workspace_root / "a.py").write_text("def f():\n    pass\n")

        for name, args in [
            ("diagnostics", {"path": "a.py"}),
            ("references", {"path": "a.py", "symbol": "f"}),
            ("definition", {"path": "a.py", "symbol": "f"}),
            ("rename_symbol", {"path": "a.py", "symbol": "f", "new_name": "g"}),
        ]:
            result = registry.get(name).run(tmp_path / "agent.db", args)
            assert "unavailable" in result, f"{name} did not degrade gracefully: {result}"
            assert "outline" in result["unavailable"] or "install" in result["unavailable"].lower()

    def test_the_file_is_not_touched_by_a_rename_that_cannot_happen(self, workspace_root, tmp_path):
        (workspace_root / "a.py").write_text("def f():\n    pass\n")
        registry.get("rename_symbol").run(
            tmp_path / "agent.db", {"path": "a.py", "symbol": "f", "new_name": "g"}
        )
        assert (workspace_root / "a.py").read_text() == "def f():\n    pass\n"


class TestWhenTheServerWillNotStart:
    def test_it_is_reported_once_and_not_retried_forever(self, tmp_path, fresh_manager, monkeypatch):
        """A server that crashes on startup crashes every time. Retrying on each tool call
        turns one slow failure into a slow failure per call."""
        broken = tmp_path / "node_modules" / ".bin" / "typescript-language-server"
        broken.parent.mkdir(parents=True)
        broken.write_text("#!/bin/sh\nexit 1\n")
        broken.chmod(0o755)
        (tmp_path / "a.ts").write_text("export const x = 1;\n")

        attempts = []
        real = fresh_manager.find_binary

        def counting(family, root):
            attempts.append(family)
            return real(family, root)

        monkeypatch.setattr(fresh_manager, "find_binary", counting)

        for _ in range(3):
            with pytest.raises(Unavailable):
                fresh_manager.for_file(tmp_path / "a.ts", start_timeout=4)

        assert len(attempts) == 1, f"it tried to start the broken server {len(attempts)} times"

    def test_the_failure_is_forgotten_after_a_while(self, tmp_path, fresh_manager, monkeypatch):
        """It has to expire on its own. The usual reason a server will not start is that it
        was not really installed, and the usual fix is to install it — so remembering the
        failure forever means that fix needs an app restart, which nobody would guess.

        There is deliberately no "clear the failures" tool or button. He has a shell, the
        message names the command, and three minutes later it simply works.
        """
        import sys
        import time

        # The module, not the singleton that shares its name — the same trap conftest
        # documents for `kith.autonomy.runner`, and the third time it has bitten here.
        module = sys.modules["kith.engine.code.lsp.manager"]

        binary = tmp_path / "node_modules" / ".bin" / "typescript-language-server"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nexit 1\n")
        binary.chmod(0o755)
        (tmp_path / "a.ts").write_text("export const x = 1;\n")

        with pytest.raises(Unavailable):
            fresh_manager.for_file(tmp_path / "a.ts", start_timeout=4)
        assert fresh_manager._broken, "it should remember, briefly"

        # Wind the clock past the window rather than sleeping through it.
        later = time.monotonic() + module.BROKEN_FOR + 1
        monkeypatch.setattr(module.time, "monotonic", lambda: later)

        with pytest.raises(Unavailable):
            fresh_manager.for_file(tmp_path / "a.ts", start_timeout=4)
        # It tried again rather than replaying the remembered refusal.
        assert fresh_manager._broken


@pytest.mark.skipif(not HAVE_PYRIGHT, reason="pyright is not installed here")
class TestAgainstRealPyright:
    @pytest.fixture
    def project(self, tmp_path, monkeypatch):
        from kith.infra import workspace

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
        monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
        return tmp_path

    def test_diagnostics_find_the_type_error(self, project, fresh_manager, tmp_path):
        result = registry.get("diagnostics").run(tmp_path / "agent.db", {"path": "app.py"})

        assert result.get("errors", 0) >= 1, result
        assert any(one["line"] == 5 for one in result["problems"])

    def test_a_clean_file_says_so(self, project, fresh_manager, tmp_path):
        result = registry.get("diagnostics").run(tmp_path / "agent.db", {"path": "helpers.py"})
        assert result.get("errors") == 0, result

    def test_references_skip_the_comment_and_the_string(self, project, fresh_manager, tmp_path):
        """`greet` appears five times in app.py. Three are uses. grep cannot tell."""
        result = registry.get("references").run(
            tmp_path / "agent.db", {"path": "helpers.py", "symbol": "greet"}
        )

        lines = sorted(one["line"] for one in result["references"])
        assert lines == [1, 4, 5], f"expected the import and two calls, got {result}"

    def test_definition_crosses_a_file(self, project, fresh_manager, tmp_path):
        result = registry.get("definition").run(
            tmp_path / "agent.db", {"path": "app.py", "symbol": "greet", "near_line": 4}
        )
        assert result["found"] is True
        assert result["definitions"][0]["path"].endswith("helpers.py")
        assert result["definitions"][0]["line"] == 1

    def test_a_symbol_that_is_not_in_the_file_says_which_file_to_try(self, project, fresh_manager, tmp_path):
        result = registry.get("references").run(
            tmp_path / "agent.db", {"path": "helpers.py", "symbol": "nonexistent"}
        )
        assert "unavailable" in result
        assert "grep" in result["unavailable"]

    def test_rename_changes_the_uses_and_leaves_the_prose_alone(self, project, fresh_manager, tmp_path):
        """The whole argument for this over a search and replace, in one assertion."""
        result = registry.get("rename_symbol").run(
            tmp_path / "agent.db",
            {"path": "helpers.py", "symbol": "greet", "new_name": "welcome"},
        )

        assert result.get("renamed", 0) >= 3, result
        app = (project / "app.py").read_text()
        assert "from helpers import welcome" in app
        assert "print(welcome('world'))" in app
        assert "# greet is mentioned in this comment" in app, "it rewrote a comment"
        assert "'greet appears in a string too'" in app, "it rewrote a string literal"
        assert (project / "helpers.py").read_text().startswith("def welcome(")

    def test_renaming_to_the_same_name_does_nothing(self, project, fresh_manager, tmp_path):
        before = (project / "helpers.py").read_text()
        result = registry.get("rename_symbol").run(
            tmp_path / "agent.db", {"path": "helpers.py", "symbol": "greet", "new_name": "greet"}
        )
        assert result["renamed"] == 0
        assert (project / "helpers.py").read_text() == before

    def test_the_server_is_reused_rather_than_restarted(self, project, fresh_manager, tmp_path):
        """Starting pyright costs seconds. Paying that per tool call would make the whole
        layer slower than the grep it replaces."""
        registry.get("diagnostics").run(tmp_path / "agent.db", {"path": "app.py"})
        running = fresh_manager.running()
        registry.get("diagnostics").run(tmp_path / "agent.db", {"path": "helpers.py"})

        assert len(fresh_manager.running()) == len(running) == 1

    def test_an_edit_is_seen_by_the_next_question(self, project, fresh_manager, tmp_path):
        """The server holds its own copy. A stale one produces confidently wrong lines."""
        first = registry.get("diagnostics").run(tmp_path / "agent.db", {"path": "app.py"})
        assert first["errors"] >= 1

        (project / "app.py").write_text((project / "app.py").read_text().replace("greet(42)", "greet('42')"))
        after = registry.get("diagnostics").run(tmp_path / "agent.db", {"path": "app.py"})

        assert after["errors"] == 0, f"it answered from a stale copy: {after}"
