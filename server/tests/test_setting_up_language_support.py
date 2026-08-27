"""Setting up language support from the settings window instead of through a conversation.

He could already notice a missing language server, offer, and install it once you approved.
That works and costs a turn. This is the other path — the settings page asks what the folder
is written in, what can serve it, and installs the one thing missing, for nothing.

**No permission prompt on this route, and that is the design rather than an oversight.** The
gate exists to stop *Kith* changing your machine without asking. A person clicking Install in
their own settings window is the asking, and a second dialog would be the app confirming that
you meant to press the button you just pressed. His route through `install_language_support`
still goes through the gate, because there the actor is him — and there the prompt now says
what it is for and how big it is, rather than showing a path and the word "write".
"""

from __future__ import annotations

import pytest

from kith.engine.code.lsp import install


@pytest.fixture
def app():
    """One per test, and the scope is the whole point.

    This was `scope="module"` — built once, because nothing here depends on which folder it
    was created with. That reasoning was right about the folder and wrong about everything
    else: `create_app` starts the scheduler, and the autouse fixture that stubs it is
    function-scoped. Pytest builds higher-scoped fixtures *first*, so a module-scoped app ran
    before its own protection and left a real `kith-scheduler` daemon running for the rest of
    the session — which four tests in two other files then failed on, in CI, having passed
    locally when this file was run alone.

    So: function scope, and it inherits every guard the suite already has.
    """
    from kith import create_app

    built = create_app()
    built.config.update(TESTING=True)
    return built


@pytest.fixture
def client(app):
    """A client that carries the token, so each test is one line rather than three."""
    from kith import settings
    from kith.api import auth

    raw = app.test_client()

    class Carrying:
        def get(self, url, **kwargs):
            return raw.get(url, headers={auth.HEADER: auth.token(settings.DATA_DIR)}, **kwargs)

        def post(self, url, **kwargs):
            return raw.post(url, headers={auth.HEADER: auth.token(settings.DATA_DIR)}, **kwargs)

    return Carrying()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A folder with real code in it, standing in as the place he is working."""
    from kith.infra import workspace as ws

    monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
    return tmp_path


class TestWhatItReports:
    def test_it_names_the_languages_it_found(self, project, client):
        for i in range(3):
            (project / f"m{i}.py").write_text("def f(): pass\n")
        body = client.get("/api/language-servers").get_json()
        assert [one["family"] for one in body["languages"]] == ["python"]
        assert body["languages"][0]["files"] == 3

    def test_it_says_which_folder_it_looked_at(self, project, client):
        """ "This needs pyright" is only useful next to which project."""
        (project / "a.py").write_text("def f(): pass\n")
        (project / "b.py").write_text("def g(): pass\n")
        assert client.get("/api/language-servers").get_json()["root"]

    def test_a_language_we_do_not_install_carries_its_own_command(self, project, client):
        """Go, Rust, Ruby and C install where their ecosystems say. Showing a button that
        cannot work would be worse than showing the line to type."""
        for i in range(3):
            (project / f"m{i}.go").write_text("package m\nfunc F() {}\n")
        found = next(
            one
            for one in client.get("/api/language-servers").get_json()["languages"]
            if one["family"] == "go"
        )
        assert found["installable"] is False
        assert "go install" in found["manual"]

    def test_an_installable_one_says_how_big_it_is(self, project, client):
        """ "Install a language server" and "install 148 MB" are different questions, and only
        one of them can be answered."""
        for i in range(3):
            (project / f"m{i}.php").write_text("<?php function f() {}\n")
        found = next(
            one
            for one in client.get("/api/language-servers").get_json()["languages"]
            if one["family"] == "php"
        )
        assert found["installable"] is True
        assert "MB" in found["size"]

    def test_one_stray_file_is_not_a_language(self, project, client):
        for i in range(4):
            (project / f"a{i}.ts").write_text("export function f() {}\n")
        (project / "setup.py").write_text("x = 1\n")
        families = [one["family"] for one in client.get("/api/language-servers").get_json()["languages"]]
        assert "python" not in families

    def test_a_folder_with_no_code_reports_nothing(self, project, client):
        (project / "notes.md").write_text("# hello\n")
        assert client.get("/api/language-servers").get_json()["languages"] == []


class TestInstalling:
    def test_a_language_we_do_not_install_is_refused(self, project, client):
        response = client.post("/api/language-servers/install", json={"family": "rust"})
        assert response.status_code == 400
        assert "rust" in response.get_json()["error"]

    def test_a_missing_family_is_refused(self, project, client):
        assert client.post("/api/language-servers/install", json={}).status_code == 400

    def test_nothing_a_caller_says_becomes_a_command(self, project, client, monkeypatch):
        """The family name selects a command from a closed table; it never becomes one."""
        seen: list[str] = []
        monkeypatch.setattr(install, "run", lambda family: (seen.append(family), (True, "ok"))[1])
        bad = client.post("/api/language-servers/install", json={"family": "python; rm -rf /"})
        assert bad.status_code == 400
        assert seen == [], "nothing was run"

    def test_a_failed_install_comes_back_as_an_error(self, project, client, monkeypatch):
        monkeypatch.setattr(install, "run", lambda _family: (False, "npm fell over"))
        response = client.post("/api/language-servers/install", json={"family": "python"})
        assert response.status_code == 400
        assert "npm fell over" in response.get_json()["error"]

    def test_a_good_install_says_where_it_went(self, project, client, monkeypatch):
        monkeypatch.setattr(install, "run", lambda _family: (True, "installed into /somewhere"))
        body = client.post("/api/language-servers/install", json={"family": "python"}).get_json()
        assert body["ok"] is True
        assert body["family"] == "python"


class TestThePromptHeAsksWith:
    """His own route still goes through the gate, and what it shows has to be answerable."""

    #: A path the gate actually cares about. The real prefix is under `~/.kith`, but in a
    #: test the data folder is a temp directory — and `/var/folders` is on the list of places
    #: not worth prompting about, correctly, which would make this pass without testing
    #: anything.
    ELSEWHERE = "/Users/somebody/.kith/language-servers"

    def test_it_says_what_the_install_is_for_and_how_big(self, project, monkeypatch):
        from pathlib import Path

        from kith.infra import permissions

        monkeypatch.setattr(install, "prefix", lambda: Path(self.ELSEWHERE))
        why = (
            f"he wants to install the typescript language server ({install.download_size('typescript')}) "
            f"from npm, into {install.prefix()} — delete that folder to undo it"
        )
        wanted = install.command_for("typescript")
        assert wanted is not None
        decision = permissions.check_command(wanted, project, purpose=why)
        assert not decision.allowed, "writing outside his folder should be gated"
        assert "language server" in decision.request.why
        assert "MB" in decision.request.why, "how big, because that is half the question"
        assert "undo" in decision.request.why

    def test_he_is_told_the_same_sentence_the_dialog_shows(self, project):
        """He is asked to tell his person what he wants and why. Handing him a different
        sentence than the one on their screen is how the two describe different things."""
        from kith.infra import permissions

        decision = permissions.check_command(
            f"rm -rf {self.ELSEWHERE}", project, purpose="he wants to tidy up"
        )
        assert not decision.allowed
        assert "he wants to tidy up" in decision.reason
        assert decision.request.why == "he wants to tidy up"

    def test_without_a_purpose_the_derived_reason_still_stands(self, project):
        """Every other caller passes nothing, and must go on reading as it always did."""
        from kith.infra import permissions

        decision = permissions.check_command(f"rm -rf {self.ELSEWHERE}", project)
        assert not decision.allowed
        assert decision.request.why
        assert "recursive or forced delete" in decision.request.why
