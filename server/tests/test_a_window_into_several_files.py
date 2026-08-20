"""`read_file` takes a list, and a window means the same thing in every file on it.

Seen in a real turn: four paths and `offset: 1, limit: 120`, refused with "offset, limit
describes a window into one file, and you asked for 4". The refusal was deliberate and half of
it was over-cautious. "Lines 1 to 120 of each of these" is precisely what somebody means by
that call, and it is the natural thing to try — which is what happened, and it cost a full round
at around a hundred and twenty thousand tokens to be told no.

The description made it worse by promising the three "only apply when you ask for one", which
reads as *ignored*, not *rejected*. So the model tried the reasonable thing and was refused by a
rule the text it had been given did not state.

`symbol` stays single, and the difference is real rather than a compromise: a window is a
*position* and means the same thing everywhere, while a symbol is a *name*, and a name defined in
three of four files has no single answer. Returning the first found would be the thing the
original comment described — an answer that looks right and is arbitrary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.tools.computer import read_file


@pytest.fixture
def three_files(tmp_path: Path, monkeypatch) -> list[str]:
    from kith.infra import permissions
    from kith.infra.workspace import paths

    monkeypatch.setattr(paths, "configured_root", lambda: tmp_path)
    monkeypatch.setattr(permissions, "require_path", lambda *a, **k: None)
    made = []
    for name in ("a", "b", "c"):
        doc = tmp_path / f"{name}.txt"
        doc.write_text("".join(f"{name} line {i}\n" for i in range(1, 40)))
        made.append(str(doc))
    return made


class TestAWindowAppliesToEachOfThem:
    def test_every_file_comes_back_windowed(self, three_files):
        out = read_file(Path(), {"paths": three_files, "offset": 5, "limit": 3})
        assert isinstance(out, str)
        for name in ("a", "b", "c"):
            assert f"{name} line 5" in out
            assert f"{name} line 8" not in out, "the limit held for every file, not just the first"

    def test_each_still_gets_its_own_heading(self, three_files):
        out = read_file(Path(), {"paths": three_files, "offset": 5, "limit": 3})
        assert out.count("=====") == 6, "one opening and closing marker per file"

    def test_and_each_says_how_to_read_the_rest(self, three_files):
        """A window with no way onwards is a dead end repeated three times."""
        out = read_file(Path(), {"paths": three_files, "offset": 5, "limit": 3})
        assert out.count("offset=8") == 3

    def test_no_window_still_reads_them_whole(self, three_files):
        out = read_file(Path(), {"paths": three_files})
        assert "a line 39" in out and "c line 39" in out

    def test_a_window_on_one_file_is_unchanged(self, three_files):
        out = read_file(Path(), {"paths": [three_files[0]], "offset": 2, "limit": 2})
        assert "a line 2" in out and "a line 4" not in out


class TestASymbolStillNeedsOneFile:
    def test_it_is_refused_across_several(self, three_files):
        out = read_file(Path(), {"paths": three_files, "symbol": "server_for"})
        assert isinstance(out, dict) and "symbol" in out["error"]

    def test_the_refusal_says_why_rather_than_only_no(self, three_files):
        """ "A name defined in more than one of them has no right answer" is actionable. "Not
        supported" sends somebody looking for a flag."""
        said = read_file(Path(), {"paths": three_files, "symbol": "server_for"})["error"]
        assert "more than one" in said and "arbitrary" in said

    def test_and_points_at_what_does_work(self, three_files):
        """The old refusal named `offset`/`limit` as part of the problem, so a model reading it
        learned the opposite of what is now true."""
        said = read_file(Path(), {"paths": three_files, "symbol": "server_for"})["error"]
        assert "window each file" in said

    def test_a_symbol_with_one_file_is_untouched(self, three_files):
        out = read_file(Path(), {"paths": [three_files[0]], "symbol": "nothing_defined_here"})
        assert isinstance(out, str)


class TestTheDescriptionMatchesTheBehaviour:
    def test_it_no_longer_says_the_window_applies_to_one_only(self):
        """The sentence that caused this. It said the three "only apply when you ask for one",
        which a reader takes as harmless — and the call was rejected."""
        from kith.tools import registry

        described = registry.get("read_file").description
        assert "only apply when you ask for one" not in described
        assert "window every file you asked for" in described
        assert "`symbol` is the exception" in described
