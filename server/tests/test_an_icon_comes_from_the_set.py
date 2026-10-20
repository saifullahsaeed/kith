"""Icons come from one set, looked up by what they are for.

Forty-three screens were drawn before this existed and every icon in them was a Unicode stand-in
(`⌂` home, `▦` schedule, `☰` more, `♧` bell) or path data typed by hand. Both look like what
they are. These tests hold the three things that stop that: the lookup answers the words a nav
bar uses with the icon a person means, the markup is something both renderers draw the same,
and the checker names a stand-in when it sees one.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from kith import settings
from kith.services import icons
from kith.tools.design import find_icons

#: The SVG elements the board's parser keeps (`ui/src/lib/design/parse.ts`). An icon made of
#: anything else would render in the checker and vanish on the board.
PARSER_KEEPS = {"svg", "path", "circle", "ellipse", "line", "polyline", "polygon", "rect"}


class TestTheLookup:
    @pytest.mark.parametrize(
        ("asked", "first"),
        [
            ("home", "house"),
            ("patients", "user-round"),
            ("more", "ellipsis"),
            ("notifications", "bell"),
            ("logout", "log-out"),
            ("close", "x"),
            ("calendar", "calendar"),
            ("user plus", "user-plus"),
            ("chevron-down", "chevron-down"),
        ],
    )
    def test_the_first_answer_is_the_icon_a_person_means(self, asked, first):
        # The first answer is the one that gets pasted, so it is the one that has to be right.
        assert icons.search(asked)[0] == first

    def test_every_alias_names_an_icon_that_exists(self):
        assert [name for name in icons.ALIASES.values() if name not in icons.icons()] == []

    def test_every_word_has_to_land(self):
        # "heart rate" must not come back as every icon with a heart in it.
        assert all("heart" in name or "activity" in name for name in icons.search("heart rate"))

    def test_nothing_is_an_answer(self):
        assert icons.search("zzqx") == []


class TestTheMarkup:
    def test_it_takes_the_colour_of_the_text_around_it(self):
        svg = icons.markup("bell", size=16)
        assert svg is not None
        assert 'stroke="currentColor"' in svg and 'fill="none"' in svg
        assert 'width="16"' in svg and 'viewBox="0 0 24 24"' in svg

    def test_every_icon_in_the_set_is_made_of_elements_the_board_draws(self):
        used = set()
        for name in icons.icons():
            used.update(re.findall(r"<([a-z]+)", icons.markup(name) or ""))
        assert used <= PARSER_KEEPS, used - PARSER_KEEPS


class TestTheTool:
    def test_several_at_once(self, tmp_path: Path):
        answer = find_icons(tmp_path, {"names": ["home", "bell", "zzqx"], "size": 24})
        by_query = {one["for"]: one for one in answer["icons"]}
        assert by_query["home"]["icon"] == "house"
        assert 'width="24"' in by_query["bell"]["svg"]
        assert by_query["bell"]["alternatives"]
        assert by_query["zzqx"]["icon"] is None

    def test_a_bare_string_is_one_name(self, tmp_path: Path):
        answer = find_icons(tmp_path, {"names": "search"})
        assert answer["icons"][0]["icon"] == "search"

    def test_asking_for_nothing_says_what_to_ask(self, tmp_path: Path):
        assert "error" in find_icons(tmp_path, {"names": []})

    def test_a_size_is_kept_sensible(self, tmp_path: Path):
        svg = find_icons(tmp_path, {"names": ["bell"], "size": 900})["icons"][0]["svg"]
        assert 'width="64"' in svg


@pytest.fixture
def checker():
    script = settings.SERVER_ROOT / "skills" / "designing-a-screen" / "scripts" / "check_design.py"
    module = sys.modules.get("check_design_under_test")
    try:
        if module is None:
            spec = importlib.util.spec_from_file_location("check_design_under_test", script)
            assert spec is not None and spec.loader is not None, script
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[spec.name] = module
        # Asked every time, not only on first import: another test imports the module without
        # needing a browser, and a cached module says nothing about whether this one can render.
        module._playwright()
    except (Exception, SystemExit) as exc:  # playwright missing is a real answer, not a hidden skip
        pytest.skip(f"the checker cannot render here: {exc}")
    return module


def test_the_checker_names_a_stand_in_and_passes_a_real_icon(checker, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(checker, "SHOTS", tmp_path / "shots")
    faked = tmp_path / "faked.html"
    faked.write_text(
        '<body data-k-frame="mobile"><nav data-k="nav"><span>⌂</span> Home</nav><p>See all →</p></body>',
        encoding="utf-8",
    )
    assert checker.check(faked) == 1
    out = capsys.readouterr().out
    assert "⌂ (nav)" in out and "find_icons" in out
    assert "→" not in out.split("icons", 1)[1].split("\n", 1)[0]  # punctuation, not an icon

    real = tmp_path / "real.html"
    real.write_text(
        f'<body data-k-frame="mobile"><nav data-k="nav">{icons.markup("house")} Home</nav></body>',
        encoding="utf-8",
    )
    checker.check(real)
    assert "icons    ok" in capsys.readouterr().out
