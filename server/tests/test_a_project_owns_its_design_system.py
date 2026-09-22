"""A design system that belongs to a product, and can be changed rather than only obeyed.

Resolution has been project-aware since the surface shipped — see
`test_which_design_system_a_screen_is_in.py`, which covers the search order over HTTP. What is
new here is everything on the writing side, and the two things a system now holds that are not
values:

* **components** — the inventory of parts. The reason the fourth screen looks like the first.
* **conventions** — the prose beside the values, which is where the judgment lives.

The case worth reading first is `TestSharedBrand`: the owner's brand lives inside an installed
skill, authored for decks long before screens existed, and a project adding a colour to it must
not reach in and edit the deck's brand. It gets its own copy, seeded from that one, and that is
the only place in this feature where a file is ever duplicated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith.services import design_system as systems

SADEEF = {
    "name": "Sadeef",
    "tokens": {"colors": {"accent": "59C5C7", "ink": "343C42"}},
    "radii_px": {"card": 20},
}

BUTTON = (
    '<button data-k-component="button.primary" '
    'style="padding:12px 24px; background:var(--ds-accent)">Save changes</button>'
)


@pytest.fixture(autouse=True)
def writes_are_allowed(monkeypatch):
    """The gate is tested in `test_a_gate_nobody_is_at.py`; here it would only park the thread."""
    from kith.infra import permissions

    monkeypatch.setattr(permissions, "mode", lambda: permissions.Mode.BYPASS)


@pytest.fixture
def project(tmp_path: Path) -> int:
    from kith.infra.db import repositories as repo
    from kith.settings import AGENT_DB_PATH

    folder = tmp_path / "vigil"
    folder.mkdir(parents=True, exist_ok=True)
    made = repo.projects.add_project(AGENT_DB_PATH, "Vigil", directory=str(folder))
    return made["id"] if isinstance(made, dict) else made


def _folder_of(project_id: int) -> Path:
    from kith.infra.db import repositories as repo
    from kith.settings import AGENT_DB_PATH

    return Path(repo.projects.get_project(AGENT_DB_PATH, project_id)["directory"])


class TestStartingOne:
    def test_a_project_with_no_system_gets_one_where_it_can_be_found_again(self, project):
        systems.save(project, name="Vigil", colors={"ink": "#1b1f23", "accent": "#2f6f4f"})

        written = _folder_of(project) / systems.CANONICAL
        assert written.is_file(), "a new system lands in the project's own design/ folder"
        assert systems.resolve(project).name == "Vigil"

    def test_a_system_needs_a_colour_to_be_a_system(self, project):
        with pytest.raises(systems.SystemError, match="at least one colour"):
            systems.save(project, name="Empty")

    def test_the_written_file_is_the_flat_shape_and_not_the_profile_one(self, project):
        systems.save(project, colors={"ink": "#111111"})
        doc = json.loads((_folder_of(project) / systems.CANONICAL).read_text())
        assert "colors" in doc and "tokens" not in doc


class TestMerging:
    def test_adding_one_colour_keeps_the_rest(self, project):
        systems.save(project, colors={"ink": "#111111", "panel": "#ffffff"})
        systems.save(project, colors={"accent": "#2f6f4f"})
        assert sorted(systems.resolve(project).colors) == ["accent", "ink", "panel"]

    def test_adding_a_component_does_not_drop_the_colours(self, project):
        systems.save(project, colors={"ink": "#111111"})
        systems.save(project, components={"button.primary": {"html": BUTTON, "use": "The one commit."}})

        after = systems.resolve(project)
        assert after.colors == {"ink": "#111111"}
        assert after.components["button.primary"].use == "The one commit."

    def test_null_removes_one(self, project):
        systems.save(project, colors={"ink": "#111111", "old": "#ff0000"})
        systems.save(project, colors={"old": None})
        assert sorted(systems.resolve(project).colors) == ["ink"]

    def test_replace_is_how_you_clear_a_section(self, project):
        systems.save(project, colors={"ink": "#111111"}, components={"a": {"html": BUTTON}})
        systems.save(project, colors={"ink": "#111111"}, components={}, replace=True)
        assert systems.resolve(project).components == {}


class TestTheInventory:
    def test_a_component_carrying_a_script_is_refused_with_its_name(self, project):
        with pytest.raises(systems.SystemError, match="evil"):
            systems.save(
                project,
                colors={"ink": "#111111"},
                components={"evil": {"html": "<div><script>fetch('/x')</script></div>"}},
            )

    def test_an_inline_handler_is_refused_too(self, project):
        with pytest.raises(systems.SystemError, match="sneaky"):
            systems.save(
                project,
                colors={"ink": "#111111"},
                components={"sneaky": {"html": '<div onclick="steal()">hi</div>'}},
            )

    def test_a_component_reaching_the_network_is_refused(self, project):
        # A design cannot reach the network by construction; a part that tries is a part that
        # would arrive with a hole in it.
        with pytest.raises(systems.SystemError, match="remote"):
            systems.save(
                project,
                colors={"ink": "#111111"},
                components={"remote": {"html": '<img src="https://example.com/logo.png">'}},
            )

    def test_a_refusal_names_every_part_it_refused(self, project):
        with pytest.raises(systems.SystemError) as raised:
            systems.save(
                project,
                colors={"ink": "#111111"},
                components={
                    "one": {"html": "<script>x</script>"},
                    "two": {"html": '<div onclick="y">'},
                },
            )
        assert "one" in str(raised.value) and "two" in str(raised.value)

    def test_a_refused_component_does_not_half_write_the_system(self, project):
        systems.save(project, colors={"ink": "#111111"})
        with pytest.raises(systems.SystemError):
            systems.save(project, components={"evil": {"html": "<script>x</script>"}})
        # The colour survived and nothing partial landed beside it.
        assert systems.resolve(project).colors == {"ink": "#111111"}
        assert systems.resolve(project).components == {}


class TestConventions:
    def test_prose_lands_beside_the_values_and_comes_back_with_them(self, project):
        systems.save(project, colors={"ink": "#111111"}, conventions="# How\n\nDense, not airy.\n")

        found = systems.resolve(project)
        assert "Dense, not airy." in found.conventions
        assert found.conventions_path == _folder_of(project) / ".kith" / "design" / "conventions.md"

    def test_they_are_capped_because_they_are_read_every_time(self, project):
        with pytest.raises(systems.SystemError, match="budget"):
            systems.save(project, colors={"ink": "#111111"}, conventions="x" * (systems.MAX_CONVENTIONS + 1))

    def test_conventions_beside_a_loose_system_file_are_still_found(self, project, tmp_path):
        # Not every system lives in design/. One written as `design-system.json` takes its
        # conventions from `design-system.md` beside it, so one search decides both.
        folder = _folder_of(project)
        (folder / "design-system.json").write_text(json.dumps({"name": "Loose", "colors": {"ink": "#111"}}))
        (folder / "design-system.md").write_text("Borders, never shadows.")
        assert "Borders" in systems.resolve(project).conventions


class TestSharedBrand:
    """A brand inside a skill belongs to what it was authored for."""

    @pytest.fixture
    def brand(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
        target = tmp_path / "skills" / "sadeef-design-engine" / "brand" / "profile.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(SADEEF), encoding="utf-8")
        return target

    def test_it_resolves_and_says_it_is_shared(self, brand, project):
        found = systems.resolve(project)
        assert found.name == "Sadeef" and found.shared

    def test_editing_it_writes_the_project_a_copy_instead(self, brand, project):
        before = brand.read_text()
        systems.save(project, colors={"warning": "#b3261e"})

        assert brand.read_text() == before, "the deck's brand is not edited in place"
        assert (_folder_of(project) / systems.CANONICAL).is_file()

    def test_the_copy_is_seeded_rather_than_started_empty(self, brand, project):
        # The project wanted to add to that palette, not to abandon it.
        systems.save(project, colors={"warning": "#b3261e"})

        after = systems.resolve(project)
        assert not after.shared
        assert after.colors["warning"] == "#b3261e"
        assert after.colors["accent"] == "#59C5C7", "the brand's own colours came along"


class TestTheTools:
    def test_reading_with_no_system_says_what_to_do_about_it(self, tmp_path):
        from kith.tools.design import read_design_system

        answer = read_design_system(tmp_path / "agent.db", {})
        assert answer["system"] == "none"
        assert "update_design_system" in answer["note"]

    def test_the_inventory_comes_back_as_a_list_he_can_compose_from(self, tmp_path, project):
        from kith.tools.design import read_design_system

        systems.save(project, colors={"ink": "#111111"}, components={"button.primary": {"html": BUTTON}})
        answer = read_design_system(tmp_path / "agent.db", {"project": project})

        assert isinstance(answer["components"], list)
        assert answer["components"][0]["name"] == "button.primary"

    def test_an_empty_inventory_says_what_belongs_in_it(self, tmp_path, project):
        from kith.tools.design import read_design_system

        systems.save(project, colors={"ink": "#111111"})
        answer = read_design_system(tmp_path / "agent.db", {"project": project})
        assert "components_note" in answer and "conventions_note" in answer

    def test_a_bad_write_answers_rather_than_raising(self, tmp_path, project):
        # A tool that raises is a turn that ends; a tool that answers is one he can correct.
        from kith.tools.design import update_design_system

        answer = update_design_system(
            tmp_path / "agent.db",
            {"project": project, "colors": {"ink": "#111"}, "components": {"evil": {"html": "<script>x</script>"}}},
        )
        assert "error" in answer and "evil" in answer["error"]


class TestAnAdaptedProfile:
    """A brand profile is a format this surface reads and does not author."""

    def test_a_profile_in_the_workspace_is_not_rewritten_in_place(self, project, never_the_real_workspace):
        profile = never_the_real_workspace / "brand" / "profile.json"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(json.dumps(SADEEF), encoding="utf-8")
        before = profile.read_text()

        systems.save(project, colors={"warning": "#b3261e"})

        # Writing the conversion back would reshape the deck's own file, and its type scale
        # would return in pixels — the one number that cannot survive the round trip.
        assert profile.read_text() == before
        assert (_folder_of(project) / systems.CANONICAL).is_file()

    def test_and_the_copy_still_carries_the_brand(self, project, never_the_real_workspace):
        profile = never_the_real_workspace / "brand" / "profile.json"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(json.dumps(SADEEF), encoding="utf-8")

        systems.save(project, colors={"warning": "#b3261e"})
        after = systems.resolve(project)
        assert after.colors["accent"] == "#59C5C7" and after.colors["warning"] == "#b3261e"


class TestWhereItCameFrom:
    """The path does not say which layer answered, and that is the question people have.

    Asked to report on a project's system, Kith read back `~/Kith/design-system.json` and then
    had to say out loud that he could not tell whether it was the project's own or the ambient
    default he had fallen through to. Everything he needed was in the resolver and none of it
    reached him.
    """

    def test_a_project_with_its_own_says_so(self, project):
        systems.save(project, colors={"ink": "#111111"})
        assert systems.resolve(project).scope == "project"

    def test_one_falling_back_to_the_workspace_says_that_instead(self, project, never_the_real_workspace):
        (never_the_real_workspace / "design-system.json").write_text(
            json.dumps({"name": "His", "colors": {"ink": "#111111"}}), encoding="utf-8"
        )
        assert systems.resolve(project).scope == "workspace"

    def test_and_a_brand_from_a_skill_is_named_as_one(self, project, tmp_path, monkeypatch):
        monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
        target = tmp_path / "skills" / "deck" / "brand" / "profile.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(SADEEF), encoding="utf-8")
        assert systems.resolve(project).scope == "skill"

    def test_nothing_anywhere_is_its_own_answer(self, project):
        assert systems.resolve(project).scope == "none"

    def test_the_tool_says_it_in_words_he_can_act_on(self, tmp_path, project, never_the_real_workspace):
        from kith.tools.design import read_design_system

        (never_the_real_workspace / "design-system.json").write_text(
            json.dumps({"name": "His", "colors": {"ink": "#111111"}}), encoding="utf-8"
        )
        answer = read_design_system(tmp_path / "agent.db", {"project": project})
        assert "NOT this project's own" in answer["where"]


class TestInheritedIsNotOwned:
    """The bug the first real use found, and the reason `_target` reads `scope`.

    A project with no system of its own resolves the workspace's, because resolution falls
    through — correct for reading, catastrophic for writing. Editing in place meant a project
    adding its first colour renamed his workspace system to that product and merged thirteen
    colours and eleven components into the file every other project inherits.
    """

    def test_a_project_adding_to_an_inherited_system_gets_its_own(self, project, never_the_real_workspace):
        his = never_the_real_workspace / "design-system.json"
        his.write_text(json.dumps({"name": "Sadeef", "colors": {"accent": "#59C5C7"}}), encoding="utf-8")

        systems.save(project, name="Mission planner", colors={"amber": "#ffc04a"})

        assert json.loads(his.read_text())["name"] == "Sadeef", "his workspace system is untouched"
        assert (_folder_of(project) / systems.CANONICAL).is_file()
        assert systems.resolve(project).scope == "project"

    def test_and_the_copy_keeps_what_it_inherited_when_it_is_not_renamed(
        self, project, never_the_real_workspace
    ):
        """Adding a colour to the house brand carries the house brand along.

        Renaming is the case that does not — see `TestNamingItMeansItIsItsOwn`, which is the
        rule this one is the other half of.
        """
        (never_the_real_workspace / "design-system.json").write_text(
            json.dumps({"name": "Sadeef", "colors": {"accent": "#59C5C7"}}), encoding="utf-8"
        )
        systems.save(project, colors={"amber": "#ffc04a"})

        after = systems.resolve(project)
        assert after.colors["accent"] == "#59C5C7" and after.colors["amber"] == "#ffc04a"

    def test_with_no_project_the_workspace_system_is_still_edited_in_place(self, never_the_real_workspace):
        his = never_the_real_workspace / "design-system.json"
        his.write_text(json.dumps({"name": "Sadeef", "colors": {"accent": "#59C5C7"}}), encoding="utf-8")

        systems.save(None, colors={"warning": "#b3261e"})
        assert sorted(json.loads(his.read_text())["colors"]) == ["accent", "warning"]

    def test_a_second_edit_lands_in_the_project_file_it_already_made(self, project, never_the_real_workspace):
        (never_the_real_workspace / "design-system.json").write_text(
            json.dumps({"name": "Sadeef", "colors": {"accent": "#59C5C7"}}), encoding="utf-8"
        )
        systems.save(project, colors={"amber": "#ffc04a"})
        systems.save(project, colors={"line": "#23292e"})

        assert sorted(systems.resolve(project).colors) == ["accent", "amber", "line"]


class TestTheFolderDecides:
    """A design belongs to the folder it is drawn in, not to whatever the conversation is on.

    The first use outside a bound conversation found this. Asked to design for one product
    while the conversation was bound to another, `update_design_system` would have written the
    new product's palette into the other project's system — the same wrong-file failure
    copy-on-write exists to prevent, arriving by a different road. Kith noticed and refused to
    use the tool, writing the JSON by hand instead, which is the right call and not one it
    should have to make.
    """

    @pytest.fixture
    def two_projects(self, tmp_path: Path):
        from kith.infra.db import repositories as repo
        from kith.settings import AGENT_DB_PATH

        made = {}
        for name in ("sprout", "oregon"):
            folder = tmp_path / name
            folder.mkdir(parents=True, exist_ok=True)
            row = repo.projects.add_project(AGENT_DB_PATH, name, directory=str(folder))
            made[name] = (row["id"] if isinstance(row, dict) else row, folder)
        return made

    def test_the_folder_names_its_project(self, two_projects):
        _, folder = two_projects["oregon"]
        assert systems.project_of_folder(folder) == two_projects["oregon"][0]

    def test_a_folder_inside_one_counts_as_that_one(self, two_projects):
        _, folder = two_projects["oregon"]
        assert systems.project_of_folder(folder / ".kith" / "screens") == two_projects["oregon"][0]

    def test_the_innermost_project_wins(self, tmp_path):
        from kith.infra.db import repositories as repo
        from kith.settings import AGENT_DB_PATH

        outer = tmp_path / "workspace"
        inner = outer / "trials" / "oregon"
        inner.mkdir(parents=True, exist_ok=True)
        repo.projects.add_project(AGENT_DB_PATH, "Workspace", directory=str(outer))
        made = repo.projects.add_project(AGENT_DB_PATH, "Oregon", directory=str(inner))
        want = made["id"] if isinstance(made, dict) else made

        assert systems.project_of_folder(inner) == want

    def test_a_folder_in_no_project_is_no_answer(self, tmp_path, two_projects):
        assert systems.project_of_folder(tmp_path / "somewhere-else") is None

    def test_the_tool_prefers_the_folder_over_the_binding(self, tmp_path, two_projects, monkeypatch):
        from kith.services import project_binding
        from kith.tools.design import update_design_system

        sprout_id, _ = two_projects["sprout"]
        oregon_id, oregon = two_projects["oregon"]

        # The conversation is on Sprout; the work is happening in Oregon's folder.
        monkeypatch.setattr(project_binding, "bound_project", lambda _path: sprout_id)
        monkeypatch.setattr("kith.infra.workspace.base_dir", lambda: oregon)

        update_design_system(tmp_path / "agent.db", {"name": "Oregon Wool", "colors": {"brick": "#8c3b2e"}})

        assert systems.resolve(oregon_id).name == "Oregon Wool"
        assert not systems.resolve(sprout_id).found, "Sprout's system was not touched"


class TestNamingItMeansItIsItsOwn:
    """A new product must not inherit the palette of the folder above it.

    Seeding from what a project resolved is right when the caller is adding a colour to the
    house brand, and wrong on the first call for a product with an identity of its own. The
    third trial found it: a finance back-office tool was handed twenty-one sage, sand and aqua
    tokens and a display serif, because that is what the workspace above it happened to use.

    Naming the system is the signal. No name means "add to what I have"; a name means "this is
    a different system".
    """

    def test_naming_it_starts_clean(self, project, never_the_real_workspace):
        (never_the_real_workspace / "design-system.json").write_text(
            json.dumps({"name": "Sadeef", "colors": {"sage": "#9caf88", "sand": "#d8cfc0"}}),
            encoding="utf-8",
        )
        systems.save(project, name="Ledgerline", colors={"navy": "#1F3A5F"})

        after = systems.resolve(project)
        assert after.name == "Ledgerline"
        assert sorted(after.colors) == ["navy"], "the workspace palette did not come along"

    def test_not_naming_it_still_adds_to_what_was_inherited(self, project, never_the_real_workspace):
        (never_the_real_workspace / "design-system.json").write_text(
            json.dumps({"name": "Sadeef", "colors": {"sage": "#9caf88"}}), encoding="utf-8"
        )
        systems.save(project, colors={"warning": "#b3261e"})

        assert sorted(systems.resolve(project).colors) == ["sage", "warning"]

    def test_a_later_edit_to_a_named_system_still_merges(self, project, never_the_real_workspace):
        # Only the *first* call decides. After that the project has its own file and an edit is
        # an edit.
        (never_the_real_workspace / "design-system.json").write_text(
            json.dumps({"name": "Sadeef", "colors": {"sage": "#9caf88"}}), encoding="utf-8"
        )
        systems.save(project, name="Ledgerline", colors={"navy": "#1F3A5F"})
        systems.save(project, name="Ledgerline", colors={"amber": "#b45309"})

        assert sorted(systems.resolve(project).colors) == ["amber", "navy"]
