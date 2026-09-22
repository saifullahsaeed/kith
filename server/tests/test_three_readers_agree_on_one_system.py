"""The same file, read by every reader, has to mean the same thing.

There are three readers of a design system and there is no way to get to one. The service
resolves it for the route and validates what Kith writes; `ui/src/lib/design/system.ts` reads
it in the browser, where it cannot import Python; and `check_design.py` reads it as a standalone
script handed to the model as a path, which cannot import `kith` without failing in exactly the
situation it exists for.

So the duplication is load-bearing, and this is what keeps it honest: one fixture, read by the
two Python readers, asserted identical. The TypeScript one is held to the same fixture by
`ui/src/lib/design/system.test.ts`.

The failure this prevents is quiet. A screen renders against the editor's idea of the palette
and is measured against the checker's, so a disagreement does not error — it produces a screen
that looks right in the app and fails a check for a reason nobody can see, or worse, passes one
it should not.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from kith import settings
from kith.services import design_system as systems

FIXTURE = {
    "name": "Vigil",
    "colors": {"ink": "1b1f23", "panel": "#ffffff", "accent": "rgb(47, 111, 79)"},
    "text": {"hero": {"size": 44, "weight": 700}, "body": {"size": 16}},
    "radius": {"card": 12},
    "space": [4, 8, 12, 16, 24, 40],
    "components": {
        "button.primary": {"html": '<button data-k-component="button.primary">Save</button>', "use": "Commit."},
        "bad name!": {"html": "<div>never</div>"},
        "scripted": {"html": "<div><script>x</script></div>"},
    },
}


@pytest.fixture
def checker():
    """`check_design.py`, imported by path the way nothing else imports it.

    Function-scoped despite the import being the slow part: a module-scoped fixture cannot
    coexist with the function-scoped autouse isolation in `conftest.py`, and the second run is
    free anyway because `sys.modules` holds it.
    """
    script = settings.SERVER_ROOT / "skills" / "designing-a-screen" / "scripts" / "check_design.py"
    if "check_design_under_test" in sys.modules:
        return sys.modules["check_design_under_test"]
    spec = importlib.util.spec_from_file_location("check_design_under_test", script)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # playwright missing is a real answer, not a hidden skip
        pytest.skip(f"the checker could not be imported: {exc}")
    sys.modules[spec.name] = module
    return module


@pytest.fixture
def written(tmp_path: Path) -> Path:
    target = tmp_path / "product" / "design" / "system.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(FIXTURE), encoding="utf-8")
    (tmp_path / "product" / "screens").mkdir(parents=True, exist_ok=True)
    return tmp_path / "product" / "screens" / "home.html"


def test_they_look_in_the_same_places_in_the_same_order(checker):
    assert tuple(checker._SYSTEM_FILES) == systems.NAMES


def test_they_resolve_the_same_file(checker, written, monkeypatch):
    from kith.infra.db import repositories as repo

    made = repo.projects.add_project(settings.AGENT_DB_PATH, "Vigil", directory=str(written.parent.parent))
    pid = made["id"] if isinstance(made, dict) else made

    assert Path(checker._system(written)["file"]) == systems.resolve(pid).path


class TestTheyAgreeOnTheValues:
    @pytest.fixture
    def pair(self, checker, written):
        return checker._system(written), systems.parse(json.dumps(FIXTURE))

    def test_on_the_name(self, pair):
        script, service = pair
        assert script["name"] == service.name == "Vigil"

    def test_on_a_bare_hex_becoming_a_colour(self, pair):
        script, service = pair
        assert script["colors"]["ink"] == service.colors["ink"] == "#1b1f23"

    def test_on_the_spacing_scale_the_screen_is_measured_against(self, pair):
        script, service = pair
        assert script["space"] == service.space == [4, 8, 12, 16, 24, 40]

    def test_on_which_components_exist(self, pair):
        """The refusals have to match, or a part one reader stored is one the other ignores."""
        script, service = pair
        assert set(script["components"]) >= {"button.primary"}
        assert set(service.components) == {"button.primary"}

    def test_the_service_drops_what_it_will_not_store(self, pair):
        _, service = pair
        assert "bad name!" not in service.components
        assert "scripted" not in service.components


class TestFontsSurviveTheRender:
    """A font token that is not valid CSS takes the whole declaration with it.

    Found by looking at a screenshot. A mobile screen's 44px/600 hero number rendered at body
    size, and so did every other role, because the emitter wrapped the font stack in quotes —
    `"ui-rounded, "SF Pro Rounded", ..."` — which is not a font value. `font: 600 44px/1
    var(--ds-font-display)` referring to it is an invalid shorthand, and a browser drops the
    whole declaration rather than the bad part, so the size and the weight went too.

    It was invisible in the worst way: the checker reported the design clean, and the picture
    the model was told to judge it by was a picture of a screen with no type scale in it. Every
    design using a font token in a shorthand had been rendered that way.
    """

    def test_a_written_stack_is_passed_through_as_written(self, checker):
        stack = 'ui-rounded, "SF Pro Rounded", system-ui, sans-serif'
        assert checker._font_stack(stack) == stack

    def test_a_bare_family_is_quoted_and_given_a_fallback(self, checker):
        assert checker._font_stack("Urbanist") == '"Urbanist", system-ui, sans-serif'

    def test_a_profile_family_and_stack_are_joined(self, checker):
        assert checker._font_stack({"family": "Urbanist", "stack": "system-ui, sans-serif"}) == (
            '"Urbanist", system-ui, sans-serif'
        )

    def test_a_family_that_is_already_a_stack_is_not_quoted_again(self, checker):
        assert checker._font_stack({"family": '"SF Mono", Menlo', "stack": "monospace"}) == (
            '"SF Mono", Menlo, monospace'
        )

    def test_nothing_in_is_nothing_out(self, checker):
        assert checker._font_stack("") == "" and checker._font_stack({}) == ""

    def test_what_reaches_the_page_has_balanced_quotes(self, checker, tmp_path):
        """The property that actually matters, asserted on the rendered head."""
        system = {
            "name": "Sprout",
            "colors": {"ink": "#1E2A22"},
            "fonts": {"display": 'ui-rounded, "SF Pro Rounded", system-ui, sans-serif'},
            "text": {"number": {"size": 44, "weight": 600}},
        }
        target = tmp_path / "design" / "system.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(system), encoding="utf-8")

        rendered = checker._with_tokens("<div>hi</div>", checker._read_system(target))
        line = next(one for one in rendered.split(";") if "--ds-font-display" in one)
        assert line.count('"') % 2 == 0, f"unbalanced quotes in {line!r}"
        assert '"ui-rounded' not in line, "the whole stack was quoted as if it were one family"
