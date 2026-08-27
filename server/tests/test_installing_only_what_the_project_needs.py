"""Getting a language server for the language in front of him, and nothing else.

The rule: nothing is installed until something needs it, and then only that. A hundred
megabytes of pyright is worth it on a Python project and is waste everywhere else, so the
trigger is the code in the folder rather than a setup step. Measured on this repository —
`kith/` is 177 Python files and nothing else, so exactly one family is wanted.

Three things this has to get right, and the third is the one that rots quietly:

* **Only what is needed.** A stray file of some other language is not a reason to fetch a
  second server.
* **Nowhere but our own folder.** Never the signed `.app` — writing there breaks the signature
  — and never globally, because `npm i -g` on someone's machine to make our feature work is a
  decision that was not ours.
* **Never a command a model composed.** The table is literals. A tool that took a package name
  would be remote code execution wearing a helpful hat, and the model reads repositories that
  can ask it for things.
"""

from __future__ import annotations

import pytest

from kith.engine.code.lsp import install


class TestWhatAProjectNeeds:
    def test_a_python_project_wants_only_python(self, tmp_path):
        for i in range(3):
            (tmp_path / f"m{i}.py").write_text("def f(): pass\n")
        assert install.wanted_for(tmp_path, lambda _f: False) == ["python"]

    def test_a_typescript_project_wants_only_typescript(self, tmp_path):
        for i in range(3):
            (tmp_path / f"m{i}.ts").write_text("export function f() {}\n")
        assert install.wanted_for(tmp_path, lambda _f: False) == ["typescript"]

    def test_a_mixed_project_asks_for_the_bigger_language_first(self, tmp_path):
        """If only one thing is going to be installed it should be the one that matters."""
        for i in range(6):
            (tmp_path / f"a{i}.ts").write_text("export function f() {}\n")
        for i in range(2):
            (tmp_path / f"b{i}.py").write_text("def f(): pass\n")
        assert install.wanted_for(tmp_path, lambda _f: False)[0] == "typescript"

    def test_one_stray_file_is_not_a_language(self, tmp_path):
        """A single `setup.py` in a TypeScript repository is not a reason to fetch pyright."""
        for i in range(4):
            (tmp_path / f"a{i}.ts").write_text("export function f() {}\n")
        (tmp_path / "setup.py").write_text("x = 1\n")
        assert "python" not in install.wanted_for(tmp_path, lambda _f: False)

    def test_a_language_already_served_is_not_wanted(self, tmp_path):
        for i in range(3):
            (tmp_path / f"m{i}.py").write_text("def f(): pass\n")
        assert install.wanted_for(tmp_path, lambda family: family == "python") == []

    def test_a_language_we_do_not_install_is_not_offered(self, tmp_path):
        """Go, Rust, Ruby and C come from their own package managers. Each installs where its
        ecosystem says, and redirecting four of them into our folder to save one command is
        fighting the tools rather than using them."""
        for i in range(3):
            (tmp_path / f"m{i}.go").write_text("package m\nfunc F() {}\n")
        assert install.wanted_for(tmp_path, lambda _f: False) == []
        assert install.installable("go") is None
        assert install.installable("rust") is None

    def test_a_folder_with_no_code_wants_nothing(self, tmp_path):
        (tmp_path / "notes.md").write_text("# hello\n")
        assert install.wanted_for(tmp_path, lambda _f: False) == []


class TestWhereItWouldGo:
    def test_the_prefix_is_inside_the_data_folder(self):
        """Never the application bundle: the server is codesigned, and writing inside it
        invalidates the signature. Never global either.

        Read through the module rather than from a second import of `DATA_DIR`. The suite
        redirects the data folder so no test can touch the real one, and a module-level `from
        settings import DATA_DIR` here would capture the value before that happens — the same
        binding trap this codebase has already been caught by once.
        """
        assert install.prefix().parent == install.DATA_DIR
        assert "language-servers" in install.prefix().name

    def test_the_command_targets_that_prefix_and_nothing_else(self):
        command = install.command_for("python")
        assert command is not None
        assert "--prefix" in command and str(install.prefix()) in command
        assert " -g" not in command and "--global" not in command

    def test_the_command_is_built_from_the_table_not_from_input(self):
        """Every package name is a literal in `_NPM_PACKAGES`. Nothing a caller says reaches
        the command line."""
        assert install.command_for("python").endswith("pyright")
        assert install.command_for("nonsense-language") is None

    @pytest.mark.parametrize("family", ["python", "typescript", "javascript", "tsx", "php"])
    def test_every_installable_family_has_a_command(self, family):
        assert install.command_for(family)


class TestRefusingRatherThanGuessing:
    def test_a_family_we_do_not_install_is_refused_by_name(self):
        worked, said = install.run("rust")
        assert not worked
        assert "rust" in said

    def test_a_missing_npm_says_what_is_missing(self, monkeypatch):
        from kith.infra import executables

        monkeypatch.setattr(executables, "which", lambda _name: None)
        worked, said = install.run("python")
        assert not worked
        assert "npm" in said


class TestTheToolIsOfferedOnlyWhenItCouldHelp:
    """The pair has to move in opposite directions or it is incoherent — a tool offering to
    install a language server on a machine that has one is a schema paid every round to
    suggest something with no effect, and one hidden whenever servers are missing could never
    fix the thing it exists for.
    """

    def test_it_is_offered_when_no_server_is_available(self):
        from kith.tools import tool_schemas

        names = {one["function"]["name"] for one in tool_schemas(language_server=False)}
        assert "install_language_support" in names
        assert "references" not in names, "and the semantic tools are hidden"

    def test_it_is_hidden_when_a_server_is_available(self):
        from kith.tools import tool_schemas

        names = {one["function"]["name"] for one in tool_schemas(language_server=True)}
        assert "install_language_support" not in names
        assert "references" in names

    def test_nobody_having_an_opinion_hides_neither(self):
        from kith.tools import tool_schemas

        names = {one["function"]["name"] for one in tool_schemas(language_server=None)}
        assert {"install_language_support", "references"} <= names


class TestThroughTheTool:
    @pytest.fixture(autouse=True)
    def workspace_root(self, tmp_path, monkeypatch):
        from kith.infra import workspace as ws

        monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        return tmp_path

    def test_without_confirmation_it_only_reports(self, workspace_root, monkeypatch):
        """Looking must never install. The first call is what he shows a person."""
        from kith.tools import semantics

        for i in range(3):
            (workspace_root / f"m{i}.py").write_text("def f(): pass\n")
        monkeypatch.setattr(install, "wanted_for", lambda *_a, **_k: ["python"])

        def explode(*_a, **_k):
            raise AssertionError("nothing may be installed without confirmation")

        monkeypatch.setattr(install, "run", explode)
        out = semantics.install_language_support(workspace_root, {"path": "."})
        assert out["needed"] == ["python"]
        assert "npm install" in out["note"], "the exact command, so it can be shown"

    def test_confirming_something_the_project_does_not_want_is_refused(self, workspace_root, monkeypatch):
        from kith.tools import semantics

        (workspace_root / "m.py").write_text("def f(): pass\n")
        monkeypatch.setattr(install, "wanted_for", lambda *_a, **_k: ["python"])
        out = semantics.install_language_support(workspace_root, {"path": ".", "confirm": "rust"})
        assert "error" in out

    def test_nothing_missing_says_so_plainly(self, workspace_root, monkeypatch):
        from kith.tools import semantics

        (workspace_root / "m.py").write_text("def f(): pass\n")
        monkeypatch.setattr(install, "wanted_for", lambda *_a, **_k: [])
        out = semantics.install_language_support(workspace_root, {"path": "."})
        assert out["needed"] == []
        assert "Nothing to install" in out["note"]
