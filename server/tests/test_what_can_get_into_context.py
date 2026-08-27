"""Every door a large thing can walk through, and what stands in it.

Three were found the hard way in one night — a picture sent twice and never dropped, the
`content` argument of a write, and the arguments of an edit — each costing millions of
tokens before anyone looked. This is the sweep that asks the question of every tool at once,
plus the two answers that turned out to be wrong.

The bar is the one that matters: *justifiable* cost. Waste is worth removing for nothing.
A bound that costs quality has to earn it, and `fetch_url` below is the case where a bound
was in the wrong place and cost everything while saving nothing.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import ClassVar

import pytest

import kith.tools  # noqa: F401  — importing registers every tool
from kith.infra import workspace as sandbox
from kith.services import skills
from kith.tools.registry import all_tools


class TestNothingReturnsWithoutABound:
    """A handler that can return arbitrary bytes needs a cap, a pager, or a reason."""

    #: Tools whose output is bounded by the shape of the thing rather than by a number:
    #: a fixed set of rows, a single record, a status. Listed so the sweep stays a question
    #: about the rest.
    #: `publish` is here for the reason this class already warns about twice: it is matched on
    #: prose. Its delegates are `git.pull` and `git.push`, whose docstrings say "until somebody
    #: fetches" — and both return a single sentence built from `_last_line`, which is the bound.
    #: `check_remote` is the same shape and matches for the same reason — it delegates to
    #: `git.fetch`, whose whole return value is one sentence from `standing` or `_last_line`.
    SELF_LIMITING: ClassVar[set[str]] = {"create_project", "publish", "check_remote"}

    # `glob` is word-bounded because it is a common English substring — it sits inside "global",
    # and a tool's *docstring* is scanned along with its code (they arrive as one string), so a
    # comment that said "leaves it global" read as a call to `glob` and flagged a memory tool that
    # reads nothing in bulk. `\bglob\b` still matches the call `sandbox.glob(` and no longer
    # matches the prose. The rest are left bare on purpose: `fetch` has to keep matching
    # `fetch_url`, so a boundary there would blind the guard to a real bulk reader.
    BULK = re.compile(r"read_text|read_bytes|run_command|_capture|fetch|browse|\bglob\b|grep|requests\.")
    # `MAX_` rather than `_MAX_`: a bound is a bound whether the constant naming it is private
    # to its module or exported. `outline.MAX_BYTES` and `repomap.MAX_BUDGET_TOKENS` are as
    # real as `sandbox.files._MAX_WRITE`, and only the underscore told them apart.
    GUARD = re.compile(r"paging\.page|_clip|_bounded|\[:\s*\d|MAX_|_OUTPUT_LIMIT|\[:limit\]|hits\[:")

    @staticmethod
    def _reachable_source(handler) -> str:
        """The handler plus the bodies of what it delegates to, one level down.

        Most handlers are a single line — `return sandbox.glob(...)` — so reading only the
        handler asks the question of the wrong function. The first version of this test did
        exactly that and flagged six tools that are all properly bounded, which would have
        trained the next person to ignore it.
        """
        try:
            source = inspect.getsource(handler)
        except (OSError, TypeError):
            return ""
        # From `def` onwards — `getsource` on a decorated function includes the `@tool(...)`
        # block, so the *description* was being scanned as if it were code. Three tools whose
        # descriptions say "or grep it" were flagged for reading in bulk; none of them greps
        # anything. A test that matches prose is a test that gets ignored.
        body = re.search(r"^def \w+\(", source, re.M)
        if body:
            source = source[body.start() :]

        from kith.engine.code import outline as outline_service
        from kith.engine.code import repomap as repomap_service
        from kith.infra import websearch

        # `registry` is how kith/tools/skills.py spells the skills service — an alias, not a
        # different module. Getting that wrong made this test fail on an ImportError rather
        # than on a finding, which is its own kind of useless.
        modules = {
            "sandbox": sandbox,
            "websearch": websearch,
            "skills": skills,
            "registry": skills,
            "outline_service": outline_service,
            "repomap_service": repomap_service,
        }
        for module_name, attribute in re.findall(
            r"\b(sandbox|websearch|skills|registry|outline_service|repomap_service)\.(\w+)", source
        ):
            target = getattr(modules[module_name], attribute, None)
            if callable(target):
                try:
                    source += "\n" + inspect.getsource(target)
                except (OSError, TypeError):
                    continue
        return source

    def test_every_bulk_reader_is_capped_or_paged(self):
        loose = []
        for name, tool in sorted(all_tools().items()):
            if name in self.SELF_LIMITING:
                continue
            source = self._reachable_source(tool.run)
            paged = "PAGE_PARAMS" in str(tool.properties)
            if self.BULK.search(source) and not self.GUARD.search(source) and not paged:
                loose.append(name)
        assert not loose, f"these read bulk with no cap and no pager, so one call can fill a turn: {loose}"


class TestFetchingAPage:
    """The bound was in the wrong place, and it cost everything while saving nothing.

    `fetch_url` went through `run_command`, which clips output at 8,000 characters — so the
    *HTML* was cut at 8,000 bytes and only then converted to prose. On a modern page that is
    the middle of `<head>`. Measured: the Wikipedia article on prompt engineering, 470,144
    characters of markup, came back as 114 — its title and half a `<link>` tag.
    """

    def test_the_head_is_stripped_even_when_it_is_enormous(self):
        head = "<head>" + ('<link rel="dns-prefetch" href="//x">' * 400) + "</head>"
        markup = f"<html>{head}<body><p>The actual article text.</p></body></html>"
        assert len(head) > 8_000, "the fixture has to be bigger than the old clip to prove anything"

        text = sandbox.web._html_to_text(markup)

        assert "The actual article text." in text
        assert "dns-prefetch" not in text

    def test_the_prose_is_what_gets_clipped_not_the_markup(self):
        """A page of mostly markup should still yield a full budget of words."""
        body = " ".join(f"sentence number {n} of the article." for n in range(4_000))
        markup = f"<html><head>{'<meta>' * 3_000}</head><body><p>{body}</p></body></html>"

        text = sandbox.web._html_to_text(markup)

        # The old behaviour returned a few dozen characters here. The clip is 8,000.
        assert len(text) > 7_000
        assert "sentence number 1 of the article." in text

    def test_a_page_smaller_than_the_budget_arrives_whole(self):
        text = sandbox.web._html_to_text("<html><body><h1>Short</h1><p>All of it.</p></body></html>")
        assert "Short" in text and "All of it." in text
        assert "truncated" not in text

    def test_the_download_is_still_bounded(self):
        """Not context — bandwidth and parsing time. Fetching a 4GB file to throw it away is
        still a bad afternoon."""
        source = inspect.getsource(sandbox.fetch_url)
        assert "--max-filesize" in source
        assert sandbox.web._MAX_FETCH_BYTES > 0

    def test_a_command_still_has_its_output_clipped(self):
        """Splitting `_capture` out must not have taken the clip off `shell`, whose output
        goes to the model exactly as it is."""
        result = sandbox.run_command("printf 'x%.0s' $(seq 1 40000)")
        assert len(result.output) < 9_000
        assert "truncated" in result.output

    def test_a_command_is_still_permission_checked(self):
        """`_capture` holds the gate now. If the split had left it behind, every shell call
        would run unchecked and nothing would say so."""
        assert "permissions.require_command" in inspect.getsource(sandbox.shell._capture)


class TestReadingASkill:
    def test_a_normal_skill_arrives_untouched(self):
        body = "# How to do the thing\n\n" + ("A paragraph of instructions.\n\n" * 50)
        assert skills._bounded(body, skills.Path("/x/SKILL.md")) == body

    def test_an_enormous_one_is_capped_and_says_where_the_rest_is(self):
        body = "para\n\n" * 20_000
        out = skills._bounded(body, skills.Path("/x/SKILL.md"))
        assert len(out) < skills.MAX_INSTRUCTION_CHARS + 500
        assert "/x/SKILL.md" in out
        assert "more characters" in out

    def test_it_cuts_at_a_paragraph_rather_than_mid_sentence(self):
        """Half an instruction is worse than a missing one, because he would follow it."""
        body = "Do the thing carefully and completely.\n\n" * 2_000
        out = skills._bounded(body, skills.Path("/x/SKILL.md"))
        instructions = out.split("\n\n…[")[0]
        assert instructions.endswith("completely.")

    @pytest.mark.parametrize("skill", sorted(s.name for s in skills.installed()))
    def test_no_installed_skill_is_anywhere_near_the_ceiling(self, skill):
        """If one is, the ceiling is wrong rather than the skill — the instructions are the
        whole value and this must never be trimming a real one."""
        body = skills.read(skill)["instructions"]
        assert "more characters of this skill" not in body


class TestTheTranscriptStaysReadable:
    """The transcript is plain-text JSONL so you can grep it, open it, and read it in ten
    years. One grep hit that prints 345,000 characters of base64 is none of those.

    Not a context cost — nothing re-reads the transcript into a prompt — but a six-round turn
    that looked at one page came to 360KB, of which 344,943 was a single string, and that is
    bytes on disk forever for every image he ever opens.
    """

    def event(self) -> dict:
        return {
            "type": "tool_result",
            "id": "c1",
            "name": "read_file",
            "result": {
                "ok": True,
                "result": {
                    "path": "/w/rendered/page-1.png",
                    "bytes": 258_000,
                    "image": "data:image/png;base64," + "Q" * 344_000,
                },
            },
        }

    def test_the_bytes_do_not_reach_the_file(self):
        from kith.api.routes.chat import _readable

        line = json.dumps(_readable(self.event()))
        assert "QQQQ" not in line
        assert len(line) < 1_000

    def test_but_the_trail_still_says_which_image_and_that_he_saw_it(self):
        from kith.api.routes.chat import _readable

        kept = _readable(self.event())["result"]["result"]
        assert kept["path"] == "/w/rendered/page-1.png"
        assert kept["bytes"] == 258_000
        # The interface tests this field for truthiness to render "looked at it". Emptying it
        # would silently turn every past image read into an ordinary file read.
        assert kept["image"]

    def test_a_result_with_no_picture_is_recorded_exactly_as_it_came(self):
        from kith.api.routes.chat import _readable

        plain = {"type": "tool_result", "name": "shell", "result": {"ok": True, "result": "hello"}}
        assert _readable(plain) == plain

    def test_other_kinds_of_event_are_untouched(self):
        from kith.api.routes.chat import _readable

        for kind in ("tool_call", "stats"):
            event = {"type": kind, "name": "x", "arguments": {"a": 1}}
            assert _readable(event) == event
