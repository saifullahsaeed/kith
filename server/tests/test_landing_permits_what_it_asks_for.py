"""The landing directive and the landing toolset have to agree.

`_LANDING_TOOLS` is a hand-written allowlist and `_LANDING_DIRECTIVE` is a paragraph of English,
and keeping two things like that in step by hand does not work. The comments in `agent_loop.py`
record three separate occasions it was caught out of step, each fixed by adding one more name:

* `write_file` was on the list and `edit_file` was not, so a turn that reached its landing rounds
  mid-implementation was left holding only the lossy tool, on source it had not fully read. He
  noticed and stopped: "the available file tool exposes read/write only, not an edit/patch
  operation... Please provide/enable an edit-capable tool." That is `edit_file`'s own docstring
  read back to us by something we had quietly disarmed.
* `commit` was absent from the one phase whose whole job is "land it" — part of why five hours of
  work ended with no commits.
* `ask` was absent while the directive names it outright: "and `ask` if you need something from
  your person — it waits for the answer." So the one moment the harness tells him to put a
  question to his person was the one moment he could not, and a stuck turn could only stop.

Three of a kind is a mechanism problem, not three entry problems. This file will not fix the
mechanism — see ISSUE-012, which proposes naming the *gathering* tools instead, since the whole
stated purpose of the reserve is that gathering is over — but it does close the specific way it
keeps failing: a directive that commands a tool the toolset forbids.
"""

from __future__ import annotations

import re

import pytest

from kith import tools
from kith.services import agent_loop


@pytest.fixture
def registry(db) -> set[str]:
    """Every tool name that really exists.

    From the live registry, deliberately, and not from `_LANDING_TOOLS` — the first version of
    this file derived it from the list under test, so deleting an entry also deleted it from the
    set of things recognised as tools and the assertion quietly stopped looking for it. A guard
    caught that, but a check whose reference is the thing it is checking is not a check.
    """
    return {schema["function"]["name"] for schema in tools.host(db, language_server=False).schemas()}


def _tools_named_in(text: str, registry: set[str]) -> set[str]:
    """Every backticked word in the directive that is actually a tool.

    Backticks rather than bare words, because the directive is prose and "record" appears in it
    as a verb. Filtered against the registry so a backticked non-tool — `.kith/memory.md`, say —
    is not read as a broken promise.
    """
    return {name for name in re.findall(r"`([a-z_]+)`", text) if name in registry}


class TestTheDirectiveCannotAskForWhatLandingForbids:
    def test_every_tool_it_names_is_one_he_still_has(self, registry):
        named = _tools_named_in(agent_loop._LANDING_DIRECTIVE, registry)
        assert named, "the directive names no tools at all — this test has stopped checking"
        missing = named - agent_loop._LANDING_TOOLS
        assert not missing, (
            f"the landing directive tells him to use {sorted(missing)} and the landing toolset "
            "does not include them — this is the third-time-caught failure, mechanised"
        )

    def test_the_reserve_still_stops_the_thing_it_exists_to_stop(self):
        """The other direction. The reserve's whole stated purpose is that gathering is over, so
        a landing toolset that let a search through would not be a reserve at all."""
        gathering = {"web_search", "fetch_url", "browse_page", "search_sources"}
        assert not (gathering & agent_loop._LANDING_TOOLS)

    def test_a_landing_round_is_offered_only_those_tools(self, db, registry):
        """Driven rather than read.

        Asserting on the source of `_run_turn` would pin the shape of one line; what matters is
        what actually goes out on the wire once the reserve takes over, and that a person cannot
        be told to `ask` by a request that does not carry `ask`.
        """
        from kith.config import default_config

        offered: list[set[str]] = []
        asked: list[str] = []

        def keeps_going(convo, config, host, tools=None, tool_choice="auto", routing=None):
            offered.append({s["function"]["name"] for s in (tools or [])})
            asked.append(str((convo[-1] if convo else {}).get("content") or ""))
            yield {"type": "turn", "content": "thinking", "tool_calls": [], "stats": {}}

        original = agent_loop._stream_once
        agent_loop._stream_once = keeps_going
        try:
            list(
                agent_loop._run_turn(
                    [{"role": "user", "content": "go"}],
                    default_config(),
                    "host",
                    db,
                    tools.host(db, language_server=False),
                    max_rounds=2,
                    landing_reserve=2,
                )
            )
        finally:
            agent_loop._stream_once = original

        landing = [
            names for names, said in zip(offered, asked, strict=True) if said == agent_loop._LANDING_DIRECTIVE
        ]
        assert landing, "the reserve never took over, so nothing here was tested"
        extra = landing[0] - agent_loop._LANDING_TOOLS
        assert not extra, f"landing offered tools it is supposed to have taken away: {sorted(extra)}"
        # And the directive's own promises are on that request, not merely on the constant.
        assert _tools_named_in(agent_loop._LANDING_DIRECTIVE, registry) <= landing[0]
