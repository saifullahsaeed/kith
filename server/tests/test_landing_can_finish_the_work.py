"""The last rounds of a turn can still edit a file, verify it, and save the point.

The landing reserve holds back a few rounds so a turn cannot spend its whole budget gathering
and end with nothing written down. What it may use *was* an allowlist of nineteen names,
maintained by hand beside a paragraph of English that tells him what to do with them — and it
was caught out of step three times, each in production, each fixed by adding one more name:

* `write_file` was on the list and `edit_file` was not, so a turn that reached those rounds
  mid-implementation was left holding only the wholesale rewrite, on existing source it had not
  fully read. He noticed and parked the task on a question: "the available file tool exposes
  read/write only, not an edit/patch operation, and rewriting these existing files wholesale
  would risk unrelated code loss. Please provide/enable an edit-capable tool" — which is
  `edit_file`'s own docstring read back to us, correctly, by something we had quietly disarmed.
* `commit` was missing, from the one phase whose entire job is to land the work.
* `ask` was missing while `_LANDING_DIRECTIVE` names it outright.

Three of a kind is the mechanism, not the entries. Under an allowlist a newly built tool is
forbidden by default and nobody finds out until a turn needs it. So the list is inverted: the
reserve now names the small set it *takes away* — `_GATHERING_TOOLS` (going out to look) and
`_STARTING_TOOLS` (opening a new front) — and everything else is kept.

**These tests are written against a real landing round, not against the constants.** Asserting
`"edit_file" not in _NOT_WHILE_LANDING` would pass for any word at all, including a misspelling;
what matters is the toolset that actually goes out on the wire once the reserve takes over.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith import tools
from kith.config import default_config
from kith.services import agent_loop


@pytest.fixture
def offered_while_landing(db: Path, monkeypatch) -> set[str]:
    """The tool names a real landing round is handed.

    Driven through `_run_turn` with the whole budget given over to the reserve, so the request
    captured here is one the reserve has narrowed. Matched by the directive rather than by round
    number: the point is to look at the request that *carries* the instruction, since the failure
    this file is about is an instruction and a toolset disagreeing on the same request.
    """
    seen: list[tuple[set[str], str]] = []

    def keeps_going(convo, config, host, tools=None, tool_choice="auto", routing=None):
        seen.append(
            (
                {s["function"]["name"] for s in (tools or [])},
                str((convo[-1] if convo else {}).get("content") or ""),
            )
        )
        yield {"type": "turn", "content": "thinking", "tool_calls": [], "stats": {}}

    monkeypatch.setattr(agent_loop, "_stream_once", keeps_going)
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
    landing = [names for names, said in seen if said == agent_loop._LANDING_DIRECTIVE]
    assert landing, "the reserve never took over, so nothing in this file was tested"
    return landing[0]


class TestItCanChangeAFileSafely:
    def test_the_surgical_edit_is_available(self, offered_while_landing):
        assert {"edit_file", "edit_files"} <= offered_while_landing

    def test_it_is_not_left_with_only_the_lossy_one(self, offered_while_landing):
        """`write_file` alone is the trap: he regenerates from what he remembers reading, so
        anything he did not re-emit is gone."""
        assert not ("write_file" in offered_while_landing and "edit_file" not in offered_while_landing)

    def test_write_file_stays_for_genuinely_new_files(self, offered_while_landing):
        """A handoff note is a new file, and that is what it is for."""
        assert "write_file" in offered_while_landing


class TestItCanCheckAndSave:
    def test_it_can_verify_what_it_just_wrote(self, offered_while_landing):
        """Claiming done without checking is the failure `_verify_done` exists for. Landing is
        exactly when the check belongs.

        `diagnostics` is asserted differently from the other two, and the difference is a thing
        the old test could not see. It is a language-server tool, so a host started without one
        does not offer it at all — which means the allowlist named a tool that is only sometimes
        there, and asserting membership of the *list* said nothing about whether he had it. What
        landing is answerable for is not removing it; whether it exists is the host's business.
        """
        assert {"check_code", "run_tests"} <= offered_while_landing
        assert "diagnostics" not in agent_loop._NOT_WHILE_LANDING

    def test_it_can_commit(self, offered_while_landing):
        """Absent from the one phase whose whole job is landing — part of why five hours of
        work ended with no commits at all."""
        assert {"commit", "changes"} <= offered_while_landing


class TestItStillStopsGathering:
    def test_the_reserve_still_does_its_job(self, offered_while_landing):
        """Widening it to let him finish must not turn it back into an unbounded turn — the
        reserve exists because research expands to fill whatever it is given."""
        assert not ({"web_search", "fetch_url", "browse_page", "search_sources"} & offered_while_landing)

    def test_it_cannot_send_someone_else_to_look(self, offered_while_landing):
        """The most expensive kind of looking: an errand's cost is its own model calls."""
        assert "delegate_subtask" not in offered_while_landing

    def test_and_it_cannot_start_new_work(self, offered_while_landing):
        """The reserve's second job, and the one easiest to lose when inverting the list.

        A task, a project or a milestone is a commitment to do something *else*, later. Recording
        what this turn did — `update_task`, `check_item`, `add_deliverable` — is the opposite, and
        is what landing is for.
        """
        assert not ({"add_task", "create_project", "add_milestone"} & offered_while_landing)
        assert {"update_task", "check_item", "add_checklist_item", "add_deliverable"} <= offered_while_landing


class TestEveryNameIsReal:
    def test_nothing_removed_is_a_tool_that_does_not_exist(self):
        """A name with no tool behind it removes nothing, so the denylist would claim to hold
        back a capability it does not — which is the same class of lie the allowlist told when it
        claimed to grant one."""
        from kith.tools import registry

        assert not (agent_loop._NOT_WHILE_LANDING - set(registry.names()))


class TestHeCanStillPutAQuestion:
    """`_LANDING_DIRECTIVE` names `ask` outright — "and `ask` if you need something from your
    person — it waits for the answer" — and for a long time landing removed it.

    So the one moment the harness tells him to ask is the one moment he cannot, and a turn with
    a real question in it has nothing left to do but stop. From the other side that is
    indistinguishable from giving up. The standing preference is the opposite: stopping when
    there is genuinely nothing to go on is fine, a question is almost always better than a stop.

    Landing is the only door left that narrows the toolset — the `delegated` latch that used to
    be the other one is gone.
    """

    def test_ask_survives_landing(self, offered_while_landing):
        assert "ask" in offered_while_landing

    def test_the_directive_does_not_name_a_tool_it_takes_away(self, offered_while_landing):
        """The general form of the bug, so the next one is caught here and not in use.

        Three have shipped: `edit_file`, `add_task`, and `ask`. Each was a directive commanding
        what the toolset beside it forbade — worse than either mistake alone, because he can
        neither comply nor explain why without guessing.

        Asserted against the request rather than against the constant, and filtered through the
        offered set so a backticked non-tool in the prose is not read as a broken promise.
        """
        named = {word.strip("`.,") for word in agent_loop._LANDING_DIRECTIVE.split() if word.startswith("`")}
        # Only words that are tools somewhere; the directive also backticks file paths.
        from kith.tools import registry

        promised = named & set(registry.names())
        assert promised, "the directive names no tools at all — this test has stopped checking"
        missing = promised - offered_while_landing
        assert not missing, f"the landing directive tells him to use {sorted(missing)}, which it removes"
