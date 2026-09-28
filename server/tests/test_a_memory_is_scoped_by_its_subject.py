"""A memory is filed by what it is about, not only by where the chat was bound.

`remember` used to stamp a memory with the conversation's binding and nothing else. So a fact
settled in a quick unbound chat about one project became global and joined every chat's pool:
measured on the live database, a 2,374-char design-system memory rode in every prompt of every
chat until it was rescoped by hand, and three more (the Sadeef One tokens, a missile-sim note, a
Harrier codename note) were global for the same reason.

The rule now: content leads. A note that names a project, or points at its folder, is filed there
even against the binding. The binding stays the strong default when the content says nothing, and
the model can always override with `scope`. These are the exact shapes measured wrong, plus the
overrides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra.db import repositories as repo
from kith.services import embeddings, project_binding, remembering
from kith.tools import memory as memory_tools


@pytest.fixture
def projects(db: Path) -> dict:
    """The measured projects: two named distinctly, one identified only by its folder."""
    one = repo.projects.add_project(db, "Sadeef One", "The Sadeef ONE ERP", "/Users/x/Desktop/sadeef-v2")
    harrier = repo.projects.add_project(db, "Harrier", "A code-review product", "/Users/x/harrier")
    sim = repo.projects.add_project(
        db,
        "Mission planner: flat map, multiple missiles",
        "A mission planning surface",
        "/Users/x/Kith/missile-sim",
    )
    return {"one": one["id"], "harrier": harrier["id"], "sim": sim["id"]}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test here should reach Ollama. The name/folder path never needs it; the two that
    exercise the meaning path stub it explicitly."""
    monkeypatch.setattr(embeddings, "embed", lambda _text: None)
    monkeypatch.setattr(embeddings, "closest_project", lambda *a, **k: None)


class TestContentOverridesTheBinding:
    def test_a_note_that_names_a_project_goes_there(self, db: Path, projects: dict):
        """ "Sadeef One design tokens…" names its project. Even bound to Harrier, it belongs to
        Sadeef One — where it was typed is not what it is about."""
        scope = remembering.scope_for(
            db,
            "Sadeef One design tokens live in theme.css: --bg #f3f3f3, --accent teal.",
            bound_project_id=projects["harrier"],
        )
        assert scope.project_id == projects["one"]

    def test_a_note_that_points_at_a_folder_goes_there(self, db: Path, projects: dict):
        """The missile-sim note names no project but points straight at one's folder."""
        scope = remembering.scope_for(
            db,
            "The sim lives at ~/Kith/missile-sim (git repo, committed), physics in core/.",
            bound_project_id=projects["one"],
        )
        assert scope.project_id == projects["sim"]

    def test_a_single_word_codename_still_files_it(self, db: Path, projects: dict):
        scope = remembering.scope_for(
            db,
            "Product name for the codebase-review agent project: HARRIER. Locked over Censor.",
            bound_project_id=None,
        )
        assert scope.project_id == projects["harrier"]


class TestTheBindingIsTheDefault:
    def test_a_note_about_nothing_in_particular_keeps_the_binding(self, db: Path, projects: dict):
        scope = remembering.scope_for(
            db, "The release cuts a version when the package file changes.", bound_project_id=projects["one"]
        )
        assert scope.project_id == projects["one"]

    def test_an_unbound_note_about_nothing_is_global(self, db: Path, projects: dict):
        scope = remembering.scope_for(
            db, "The person prefers to be told what was checked, not just the conclusion.", None
        )
        assert scope.project_id is None

    def test_a_folder_word_too_short_is_not_evidence(self, db: Path):
        """A three-letter folder must not pull an unrelated note in. Below the guard, it is noise."""
        tiny = repo.projects.add_project(db, "X", "", "/Users/x/abc")
        scope = remembering.scope_for(db, "Something about abc happening in the yard.", None)
        assert scope.project_id is None and tiny  # the short folder did not fire


class TestTheModelCanOverride:
    def test_scope_global_forces_global_over_a_name_match(self, db: Path, projects: dict):
        scope = remembering.scope_for(
            db, "Sadeef One design tokens: --accent teal.", projects["harrier"], scope="global"
        )
        assert scope.project_id is None

    def test_scope_project_forces_the_binding(self, db: Path, projects: dict):
        scope = remembering.scope_for(
            db, "Harrier is a code-review product.", projects["one"], scope="project"
        )
        assert scope.project_id == projects["one"]

    def test_a_bare_project_id_files_it_there(self, db: Path, projects: dict):
        scope = remembering.scope_for(db, "Anything.", None, scope=str(projects["sim"]))
        assert scope.project_id == projects["sim"]

    def test_an_id_that_does_not_exist_falls_back_to_global(self, db: Path):
        scope = remembering.scope_for(db, "Anything.", None, scope="9999")
        assert scope.project_id is None
        assert "9999" in scope.why


class TestTwoProjectsMentioned:
    def test_the_binding_breaks_the_tie_when_it_is_one_of_them(self, db: Path, projects: dict):
        scope = remembering.scope_for(
            db,
            "The Sadeef One tokens and the Harrier report share a font.",
            bound_project_id=projects["harrier"],
        )
        assert scope.project_id == projects["harrier"]

    def test_meaning_breaks_the_tie_among_only_those_mentioned(self, db: Path, projects: dict, monkeypatch):
        monkeypatch.setattr(embeddings, "closest_project", lambda *a, **k: projects["one"])
        scope = remembering.scope_for(
            db, "The Sadeef One tokens and the Harrier report share a font.", bound_project_id=None
        )
        assert scope.project_id == projects["one"]


class TestTheMeaningPathIsLastAndGated:
    def test_it_is_consulted_only_when_name_and_folder_are_silent(
        self, db: Path, projects: dict, monkeypatch
    ):
        seen: list = []

        def _spy(content, vec, candidate_projects, bar, margin):
            seen.append((bar, margin))
            return projects["sim"]

        monkeypatch.setattr(embeddings, "closest_project", _spy)
        scope = remembering.scope_for(db, "A note that names no project and no folder.", None)
        assert scope.project_id == projects["sim"]
        assert seen == [(remembering._SIMILAR_ENOUGH, remembering._CLEAR_MARGIN)]

    def test_when_meaning_is_unsure_the_binding_wins(self, db: Path, projects: dict):
        # closest_project returns None (stubbed in the autouse fixture) → keep the binding.
        scope = remembering.scope_for(db, "A note that names no project and no folder.", projects["harrier"])
        assert scope.project_id == projects["harrier"]


class TestTheToolReportsWhereItWent:
    def test_it_files_by_content_and_says_so(self, db: Path, projects: dict, monkeypatch):
        monkeypatch.setattr(project_binding, "bound_project", lambda _p: projects["one"])

        made = memory_tools.remember(
            db, {"content": "Product name locked: HARRIER, a code-review product for teams."}
        )

        assert made["project_id"] == projects["harrier"]  # content beat the binding
        assert made["scope"]["project_id"] == projects["harrier"]
        assert "to_change" in made["scope"]

    def test_a_global_memory_is_reported_as_global(self, db: Path, monkeypatch):
        monkeypatch.setattr(project_binding, "bound_project", lambda _p: None)

        made = memory_tools.remember(db, {"content": "The person likes concise reports over verbose ones."})

        assert made["project_id"] is None
        assert made["scope"]["project_id"] is None
        assert "every chat" in made["scope"]["to_change"]
