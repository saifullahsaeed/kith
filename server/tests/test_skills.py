"""Skills, and the two things that make them worth having.

**They follow a standard nobody here invented.** A skill written for Claude Code, Cursor or
Gemini CLI has to work unmodified, and one written here has to work there. That is only true
if the parser accepts what the format actually permits rather than what is convenient, so a
good half of these tests are shapes taken from real skills: folded scalars, list-form
``allowed-tools``, frontmatter fields Kith has no concept of, eighty bundled files.

**Progressive disclosure is what makes them affordable**, and it is easy to lose without
noticing. The prompt must carry names and descriptions and nothing else; the body must arrive
only when asked for; and the index must land in the cached prefix rather than in front of the
volatile clock. Each of those is a test, because getting any of them wrong turns "install ten
skills" from a thousand cached tokens into forty thousand live ones and nothing visibly
breaks.
"""

from __future__ import annotations

import textwrap

import pytest

from kith.services import skills


@pytest.fixture(autouse=True)
def skills_dir(tmp_path, monkeypatch):
    """A skills folder of our own — never the real one."""
    place = tmp_path / "skills"
    place.mkdir()
    monkeypatch.setenv(skills.DIR_KEY, str(place))
    return place


def write(place, name, frontmatter: str, body: str = "Do the thing.") -> None:
    directory = place / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / skills.MANIFEST).write_text(f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n\n{body}\n")


class TestReadingTheFormat:
    def test_the_minimal_shape(self, skills_dir):
        write(skills_dir, "tidy-notes", "name: tidy-notes\ndescription: Tidies notes. Use for notes.")
        [skill] = skills.installed()
        assert skill.name == "tidy-notes"
        assert skill.description.startswith("Tidies notes")

    def test_every_optional_field(self, skills_dir):
        write(
            skills_dir,
            "full-house",
            """
            name: full-house
            description: Everything at once. Use when testing.
            license: Apache-2.0
            compatibility: Requires git and network access
            metadata:
              author: someone
              version: "2.1"
            allowed-tools: Bash(git:*) Read
            """,
        )
        [skill] = skills.installed()
        assert skill.license == "Apache-2.0"
        assert skill.compatibility.startswith("Requires git")
        assert skill.metadata == {"author": "someone", "version": "2.1"}
        assert skill.allowed_tools == ("Bash(git:*)", "Read")

    def test_a_folded_description(self, skills_dir):
        # Real skills write long descriptions this way, and it is why this uses a YAML parser
        # rather than a regex: a hand-rolled reader returns ">" or the first line.
        write(
            skills_dir,
            "folded",
            """
            name: folded
            description: >
              Extracts things from other things, carefully and at length,
              across more than one line of frontmatter.
            """,
        )
        [skill] = skills.installed()
        assert "across more than one line" in skill.description
        assert ">" not in skill.description

    def test_allowed_tools_as_a_yaml_list(self, skills_dir):
        # The spec says space-separated string; Claude Code also accepts a list, so real
        # skills use both and either has to load.
        write(
            skills_dir,
            "listy",
            """
            name: listy
            description: Uses a list. Use when testing lists.
            allowed-tools:
              - Read
              - Grep
            """,
        )
        [skill] = skills.installed()
        assert skill.allowed_tools == ("Read", "Grep")

    def test_unknown_fields_are_kept_not_rejected(self, skills_dir):
        # Claude Code defines a dozen fields beyond the standard and more will exist. A skill
        # using them must still load, minus the behaviour Kith has no concept of — rejecting
        # what we do not implement is how "any skill works" stops being true.
        write(
            skills_dir,
            "future",
            """
            name: future
            description: From a later version. Use when testing.
            context: fork
            model: opus
            effort: high
            paths: "src/**/*.ts"
            """,
        )
        [skill] = skills.installed()
        assert skill.name == "future"
        assert sorted(skill.extra) == ["context", "effort", "model", "paths"]

    def test_a_missing_name_falls_back_to_the_folder(self, skills_dir):
        write(skills_dir, "from-folder", "description: No name field. Use when testing.")
        [skill] = skills.installed()
        assert skill.name == "from-folder"

    def test_a_missing_description_falls_back_to_the_first_paragraph(self, skills_dir):
        write(
            skills_dir,
            "no-desc",
            "name: no-desc",
            body="# Heading\n\nThis is what it is for.\n\nAnd more detail after.",
        )
        [skill] = skills.installed()
        assert skill.description == "This is what it is for."

    def test_dashes_in_the_body_do_not_end_the_frontmatter(self, skills_dir):
        directory = skills_dir / "dashy"
        directory.mkdir()
        (directory / skills.MANIFEST).write_text(
            "---\nname: dashy\ndescription: Has a rule. Use when testing.\n---\n\nBefore\n\n---\n\nAfter\n"
        )
        assert [s.name for s in skills.installed()] == ["dashy"]
        # Splitting on every --- would truncate the instructions at the first horizontal rule.
        body = skills.read("dashy")["instructions"]
        assert "Before" in body and "After" in body


class TestRefusals:
    @pytest.mark.parametrize(
        "name",
        ["Upper-Case", "-leading", "trailing-", "double--hyphen", "has space", "has_underscore"],
    )
    def test_invalid_names(self, skills_dir, name):
        directory = skills_dir / "holder"
        directory.mkdir()
        (directory / skills.MANIFEST).write_text(f"---\nname: {name}\ndescription: x. Use when x.\n---\n")
        faults = skills.validate(directory)
        assert faults, f"{name} should be refused"

    def test_a_name_that_does_not_match_the_folder(self, skills_dir):
        write(skills_dir, "on-disk", "name: in-frontmatter\ndescription: Mismatched. Use when testing.")
        faults = skills.validate(skills_dir / "on-disk")
        # The spec requires these to match. A mismatch is how a skill ends up findable by one
        # tool and invisible to another.
        assert any("does not match the folder" in f for f in faults)

    def test_no_manifest_at_all(self, skills_dir):
        (skills_dir / "empty").mkdir()
        assert skills.validate(skills_dir / "empty") == [
            f"no {skills.MANIFEST} — a skill is a folder with a {skills.MANIFEST} in it."
        ]

    def test_unclosed_frontmatter(self, skills_dir):
        directory = skills_dir / "broken"
        directory.mkdir()
        (directory / skills.MANIFEST).write_text("---\nname: broken\ndescription: x\n\nno closing fence\n")
        assert any("not closed" in f for f in skills.validate(directory))

    def test_no_description(self, skills_dir):
        directory = skills_dir / "silent"
        directory.mkdir()
        (directory / skills.MANIFEST).write_text("---\nname: silent\n---\n")
        assert any("no description" in f for f in skills.validate(directory))

    def test_an_over_long_description_is_trimmed_rather_than_refused(self, skills_dir):
        # Anthropic's own claude-api skill overruns the published 1024 limit. Rejecting a
        # working official skill over 44 characters is not correctness, it is uselessness —
        # and trimming still serves the reason the limit exists, since this text is billed on
        # every request.
        write(skills_dir, "verbose", f"name: verbose\ndescription: {'x' * 1200}")
        faults = skills.validate(skills_dir / "verbose")
        assert all("(Warning only.)" in f for f in faults)
        [skill] = skills.installed()
        assert len(skill.description) == skills.MAX_DESCRIPTION

    def test_one_broken_skill_does_not_hide_the_others(self, skills_dir):
        write(skills_dir, "good-one", "name: good-one\ndescription: Fine. Use when fine.")
        (skills_dir / "bad-one").mkdir()
        (skills_dir / "bad-one" / skills.MANIFEST).write_text("no frontmatter here")

        names = [s.name for s in skills.installed()]

        # Letting one bad folder raise would take the whole index with it, and the failure
        # would look like the feature not existing.
        assert names == ["good-one"]
        assert [p["name"] for p in skills.problems()] == ["bad-one"]


class TestProgressiveDisclosure:
    """The part that keeps this affordable. Each of these can break silently."""

    def test_the_prompt_carries_descriptions_and_not_bodies(self, skills_dir):
        write(
            skills_dir,
            "heavy",
            "name: heavy\ndescription: Does heavy things. Use for heavy things.",
            body="SECRET_BODY_MARKER " * 500,
        )
        text = skills.index()
        assert "Does heavy things" in text
        # The whole point. Inlining bodies is the obvious implementation and it is the one
        # that costs 20x on every request whether the skill is relevant or not.
        assert "SECRET_BODY_MARKER" not in text

    def test_nothing_installed_costs_nothing(self, skills_dir):
        # Not even a heading explaining a feature that has nothing in it.
        assert skills.index() == ""

    def test_the_index_is_stable_between_calls(self, skills_dir):
        write(skills_dir, "steady", "name: steady\ndescription: Steady. Use when steady.")
        # It sits inside the cached prefix, so a count or a timestamp in here would move the
        # seam and invalidate the persona in front of it on every single request.
        assert skills.index() == skills.index()

    def test_the_body_arrives_only_when_asked_for(self, skills_dir):
        write(
            skills_dir,
            "asked",
            "name: asked\ndescription: Ask me. Use when asking.",
            body="THE ACTUAL STEPS",
        )
        assert "THE ACTUAL STEPS" not in skills.index()
        assert "THE ACTUAL STEPS" in skills.read("asked")["instructions"]

    def test_resources_are_named_not_read(self, skills_dir):
        write(skills_dir, "bundled", "name: bundled\ndescription: Has files. Use when testing.")
        (skills_dir / "bundled" / "references").mkdir()
        (skills_dir / "bundled" / "references" / "REFERENCE.md").write_text("REFERENCE_CONTENT")
        (skills_dir / "bundled" / "scripts").mkdir()
        (skills_dir / "bundled" / "scripts" / "run.py").write_text("print('hi')")

        answer = skills.read("bundled")

        assert "references/REFERENCE.md" in answer["resources"]
        assert "scripts/run.py" in answer["resources"]
        # Knowing a file exists is what lets him decide whether to spend context on it.
        # Reading them all to find out would be the opposite of the point.
        assert "REFERENCE_CONTENT" not in str(answer)

    def test_a_huge_bundle_is_not_listed_in_full(self, skills_dir):
        write(skills_dir, "many", "name: many\ndescription: Many files. Use when testing.")
        assets = skills_dir / "many" / "assets"
        assets.mkdir()
        for index in range(90):
            (assets / f"template-{index:03}.txt").write_text("x")

        answer = skills.read("many")

        # Real skills ship eighty-plus assets. Ninety paths is several hundred tokens spent
        # naming template files he will never open.
        assert len(answer["resources"]) == skills.MAX_LISTED_RESOURCES
        assert answer["moreResources"] == 90 - skills.MAX_LISTED_RESOURCES

    def test_the_directory_is_absolute_so_he_need_not_guess(self, skills_dir):
        write(skills_dir, "located", "name: located\ndescription: Somewhere. Use when testing.")
        answer = skills.read("located")
        assert answer["directory"] == str(skills_dir / "located")

    def test_an_unknown_name_says_what_is_available(self, skills_dir):
        write(skills_dir, "real-one", "name: real-one\ndescription: Real. Use when real.")
        with pytest.raises(skills.SkillError) as caught:
            skills.read("imaginary")
        # A bare "not found" costs him a second wasted call to work out what he does have.
        assert "real-one" in str(caught.value)


class TestTheIndexLandsInTheCachedPrefix:
    def test_it_is_part_of_the_stable_head(self, skills_dir):
        from kith.llm import caching

        write(skills_dir, "cached", "name: cached\ndescription: In the prefix. Use when testing.")
        persona = "PERSONA TEXT " * 800
        system = persona + skills.index()
        prompt = system + "\n\nThe time is 04:00. You feel fine.\n"

        head = caching.stable_head(prompt, system)

        # If the index were appended after the clock instead, every request would rewrite the
        # whole prefix — the optimisation would be silently gone with nothing to show it.
        assert head == len(system)
        assert "In the prefix" in prompt[:head]
        assert "The time is" not in prompt[:head]


class TestInstalling:
    def test_a_valid_folder_is_copied_in(self, skills_dir, tmp_path):
        source = tmp_path / "elsewhere" / "importable"
        source.mkdir(parents=True)
        (source / skills.MANIFEST).write_text(
            "---\nname: importable\ndescription: Comes from elsewhere. Use when testing.\n---\n\nSteps.\n"
        )

        installed = skills.install(source)

        assert installed.name == "importable"
        assert (skills_dir / "importable" / skills.MANIFEST).is_file()

    def test_bundled_files_come_too(self, skills_dir, tmp_path):
        source = tmp_path / "with-files"
        (source / "scripts").mkdir(parents=True)
        (source / skills.MANIFEST).write_text(
            "---\nname: with-files\ndescription: Bundles things. Use when testing.\n---\n"
        )
        (source / "scripts" / "go.sh").write_text("echo go")

        skills.install(source)

        assert (skills_dir / "with-files" / "scripts" / "go.sh").read_text() == "echo go"

    def test_an_invalid_folder_leaves_nothing_behind(self, skills_dir, tmp_path):
        source = tmp_path / "Bad-Name"
        source.mkdir()
        (source / skills.MANIFEST).write_text("---\nname: Bad-Name\ndescription: x. Use when x.\n---\n")

        with pytest.raises(skills.SkillError):
            skills.install(source)

        # Validated before copying, so a refusal is not a half-installed skill.
        assert list(skills_dir.iterdir()) == []

    def test_installing_over_an_existing_one_is_refused_by_default(self, skills_dir, tmp_path):
        write(skills_dir, "already", "name: already\ndescription: Here first. Use when testing.")
        source = tmp_path / "already"
        source.mkdir()
        (source / skills.MANIFEST).write_text(
            "---\nname: already\ndescription: Second copy. Use when testing.\n---\n"
        )

        with pytest.raises(skills.SkillError) as caught:
            skills.install(source)
        assert "already installed" in str(caught.value)

        replaced = skills.install(source, overwrite=True)
        assert replaced.description.startswith("Second copy")

    def test_a_bare_manifest_says_what_to_do_instead(self, skills_dir, tmp_path):
        loose = tmp_path / skills.MANIFEST
        loose.write_text("---\nname: loose\ndescription: x. Use when x.\n---\n")
        with pytest.raises(skills.SkillError) as caught:
            skills.install(loose)
        assert "folder named after the skill" in str(caught.value)


class TestRemoving:
    def test_it_goes_to_the_trash_rather_than_being_destroyed(self, skills_dir, tmp_path, monkeypatch):
        from kith.infra import workspace

        home = tmp_path / "home"
        (home / ".Trash").mkdir(parents=True)
        monkeypatch.setattr(workspace.paths.Path, "home", staticmethod(lambda: home), raising=False)
        write(skills_dir, "goodbye", "name: goodbye\ndescription: Leaving. Use when testing.")

        skills.remove("goodbye")

        assert not (skills_dir / "goodbye").exists()
        # Someone who spent a fortnight editing a skill should be able to get it back.
        assert (home / ".Trash" / "goodbye" / skills.MANIFEST).is_file()

    @pytest.mark.parametrize("name", ["", "  ", "../escape", ".hidden", "nested/path"])
    def test_a_name_that_is_not_a_name(self, skills_dir, name):
        with pytest.raises(skills.SkillError):
            skills.remove(name)

    def test_removing_something_that_is_not_there(self, skills_dir):
        with pytest.raises(skills.SkillError) as caught:
            skills.remove("never-existed")
        assert "no skill called" in str(caught.value)
