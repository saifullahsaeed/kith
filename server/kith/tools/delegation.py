"""Sending someone else to look, so the looking does not land in this conversation.

Finding out where something lives is expensive in exactly the way that hurts most. Twelve
greps, eight outlines and four partial reads to answer *which file throws this* is a couple
of thousand tokens of answer buried in forty thousand tokens of search — and once it is in
the turn it is in every later round of the turn, re-sent and re-billed, crowding out the work
it was supposed to enable. The finding is small. The finding out is not.

So the finding out happens somewhere else. `delegate_subtask` runs a whole second agent loop
on a private message list that nobody ever sees, and hands back the last thing it said. The
greps are real, the files are really read, and none of it exists afterwards.

Three things make this safe rather than merely clever, and each is enforced here rather than
asked for in the tool description:

**It cannot change anything.** The sub-agent is given :data:`GIVEN` — reading, searching,
looking things up — and `run_tool` enforces that set as well as showing it, so naming
`write_file` anyway gets a refusal rather than a write. This is not only about damage: the
main agent holds the plan, and a worker editing files in the dark is a main agent that no
longer knows what its own codebase looks like. The sub-agent finds out; the main agent acts.

**It cannot delegate.** `delegate_subtask` is not in its set, so the one call that could turn
one worker into an unbounded tree of them is the one call it does not have.

**It cannot run forever.** `subtask_rounds` rounds, then `_final_answer` makes it report with
what it has. The landing reserve is switched off for it, which is a real decision and not a
detail — see :func:`_scratchpad`.

**Deliberately not built: a verifier role.** The obvious second worker runs the tests and the
linter after an edit, and it is left out for two reasons that both point the same way. It
would have side effects, which is what lets several of these run in one round; and running
the tests on work you just did is not a thing to hear about second-hand — the main agent made
the change, so the main agent should read the failure. A role parameter can be added the day
something needs one. Adding it now would mean a schema the model has to choose between on
every round, for a choice with one real option.
"""

from __future__ import annotations

import threading
import uuid
from contextvars import copy_context
from pathlib import Path

from kith.kernel import session_context, stopping
from kith.services import errands, tuning
from kith.tools.params import BOOL, STR
from kith.tools.registry import tool

#: What a sub-agent may do: read, search, look up. Nothing here writes a file, starts a
#: process, files anything on the board, or reaches a person.
#:
#: A list rather than a rule, and an exhaustive one — see :data:`WITHHELD` for the other half
#: and `tests/test_a_sub_agent_cannot_reach_past_reading.py` for what keeps them exhaustive.
#: A whitelist is the right direction for the mistake that will actually be made: a new read
#: tool that nobody adds here is a sub-agent that says it could not find something, which is
#: visible and annoying. A new *write* tool that nobody removes from a blacklist is a sub-agent
#: quietly editing files, which is neither.
GIVEN = frozenset(
    {
        # Finding code without reading it.
        "grep",
        "glob",
        "list_files",
        "outline",
        "repo_map",
        "find_symbol",
        "definition",
        "references",
        # Reading it.
        "read_file",
        "diagnostics",
        "check_code",
        # What the repository has been doing.
        "changes",
        "history",
        # The open web. Read-only, and the reason one tool covers "find where this lives" and
        # "find out how this library works" instead of two.
        "web_search",
        "fetch_url",
        "browse_page",
        # What Kith already knows, which is often where the answer is and is always cheaper
        # than going and looking again.
        "recall",
        "read_journal",
        "read_source",
        "search_sources",
        "read_skill",
        # The board, read. A scout asked "has any of this been done before" needs to be able
        # to look, and none of these change anything.
        "list_projects",
        "list_tasks",
        "view_task",
    }
)

#: What a sub-agent is deliberately not given, and why. Kept as a real list rather than "the
#: rest of the registry" so that adding a tool forces the question — the test pairs this with
#: :data:`GIVEN` and fails on any name in neither.
WITHHELD = frozenset(
    {
        # Changes files. The whole boundary.
        "write_file",
        "edit_file",
        "edit_files",
        "delete_file",
        "rename_symbol",
        # Changes the record. A worker's findings are provisional until the main agent has
        # read them; committing, publishing or remembering them makes them permanent first.
        "commit",
        "publish",
        "remember",
        "forget",
        "journal",
        "set_memory_level",
        "link_folder",
        # The board, written. A sub-agent that files tasks is a sub-agent making plans, and
        # the plan is the one thing that must stay in the conversation a person can see.
        "add_task",
        "add_milestone",
        "add_checklist_item",
        "add_deliverable",
        "check_item",
        "create_project",
        "order_milestones",
        "unlink_milestones",
        "update_milestone",
        "update_project",
        "update_task",
        "take_in_shared_tasks",
        # Time. Nothing a scout finds is a reason to wake someone up later.
        "schedule",
        "cancel_schedule",
        "list_schedules",
        "set_reminder",
        "cancel_reminder",
        "list_reminders",
        # Runs things. Withheld for two reasons at once: they have side effects, and side
        # effects are exactly what would stop several of these sharing a round. `run_tests`
        # is the one that will keep being asked for — see the module docstring.
        "shell",
        "start_process",
        "stop_process",
        "check_process",
        "run_tests",
        "install_language_support",
        # Reaches a person. `ask` holds a turn open until someone answers, and nobody is
        # watching a scratchpad — the question would be asked of an empty room while the main
        # turn waits on a tool call that never returns. A worker that needs something it
        # cannot get should say so in its report and let the agent holding the conversation
        # decide whether that is worth interrupting anyone for.
        "ask",
        "reach_out",
        # Itself. One worker cannot become a tree of them.
        "delegate_subtask",
    }
)

#: What the worker is told it is. Appended to the persona rather than replacing it, and that
#: is the cheap choice as well as the right one: `llm/caching` treats `config.system` as the
#: stable head, so a sub-agent whose system prompt *starts* with the same persona reads the
#: same cached prefix every other request in the install reads, instead of writing its own.
BRIEF = (
    "You are working as an isolated sub-agent right now. Someone — you, a moment ago, in the "
    "conversation this came from — handed you one thing to find out, and the only part of "
    "this that survives is your final message. Everything else here (what you grep, what you "
    "read, what turns out to be a dead end) is discarded the moment you finish, and is never "
    "seen by anyone.\n\n"
    "That is the whole point, so use it: look at as much as you need to actually know the "
    "answer, and do not summarise as you go.\n\n"
    "You can read, search and look things up. You cannot change a file, run a command, file "
    "anything, or ask anyone a question — if the objective needs one of those, say so in your "
    "report instead of trying.\n\n"
    "You are describing what is THERE, not what anybody has done. You cannot change a single "
    "byte, so every sentence you write is a statement about the code as you found it. Never "
    "write that something has been implemented, added, upgraded, fixed or delivered — write "
    "what exists and where. This matters more than it sounds: your report is read by an agent "
    'holding a task list, and a line like "dynamic field inspection is fully implemented" '
    "is indistinguishable from evidence that it just built one. That has already happened and "
    "closed three tasks nobody had done.\n\n"
    "Your report is the deliverable, and it is read by an agent that has none of your context "
    "and cannot see anything you looked at. So:\n"
    "- Answer the objective first, in a sentence.\n"
    "- Then the evidence, with exact `path:line` for every claim.\n"
    "- Quote the few lines that matter rather than describing them.\n"
    "- Say plainly what you could NOT establish. A confident guess is worse than a gap here, "
    "because nothing downstream can tell them apart.\n"
    "No preamble, no restating the objective, no offers to do more."
)


@tool(
    "delegate_subtask",
    "Send an isolated sub-agent to find something out. It reads and searches in a private "
    "scratchpad that never enters this conversation, and comes back with a few hundred words. "
    "WHAT IT SAVES YOU IS CONTEXT, NOT ROUNDS. Doing it yourself is already cheap in rounds — "
    "read_file and outline take a list of paths, grep takes a list of patterns — but twelve "
    "files you open yourself are in your window for the rest of the turn and re-sent on every "
    "round after it, and twelve files an errand opens cost you its summary and nothing else. "
    "REACH FOR ONE WHEN: you are about to open more than three or four files you have not read "
    "before just to find out where something lives; you need to understand a subsystem you do "
    "not already know; or the answer is one paragraph buried in long documentation. Give it "
    "one self-contained objective in plain language with any paths or names you have, since it "
    "starts knowing nothing about what you are doing. It can read, search and browse; it "
    "cannot change anything, run anything, or ask you a question — send the finding out and "
    "keep the doing. Send several in ONE round to cover several areas: they run in parallel, "
    "and sent in separate rounds they do not.",
    {
        "objective": {
            **STR,
            "description": (
                "What to find out, and enough context to start: what you already know, where "
                "to look, and what a useful answer would contain."
            ),
        },
        "wait": {
            **BOOL,
            "description": (
                "Whether to wait for the answer. True by default, and usually right — you "
                "asked because you need the answer to decide what to do next. Pass false only "
                "when the errand is long AND you have real work to get on with meanwhile: it "
                "returns immediately, and the findings arrive between rounds when they are "
                "ready. You will not finish the turn without them either way."
            ),
        },
    },
    required=("objective",),
)
def delegate_subtask(path: Path, args: dict):
    objective = str(args.get("objective") or "").strip()
    if not objective:
        return {"error": "A sub-agent needs an objective — say what you want found out."}

    # One id for this errand, so the panel can tell three scouts sent in the same round apart.
    # A conversation carried through by hand rather than read off the context inside `_Watched`:
    # a backgrounded errand runs on a thread of its own, and `current()` read from in there is
    # read at the wrong moment.
    conversation = session_context.current()
    errand_id = uuid.uuid4().hex[:8]

    if not _wanted(args.get("wait")):
        if not errands.sent(conversation, errand_id, objective):
            return {
                "error": (
                    f"You already have {errands.MAX_OUT} errands out. Wait for those to come "
                    "back before sending more, or send this one with wait=true."
                )
            }
        # The context is copied here, on the thread that has one, for the same reason the round
        # loop copies it before handing work to its pool: a thread starts on the defaults, and
        # every tool the worker runs resolves its paths, its project and its permissions through
        # `session_context`. A scout that lost it would read the wrong folder.
        carried = copy_context()
        worker = threading.Thread(
            target=lambda: carried.run(
                _run_errand,
                path,
                objective,
                conversation,
                errand_id,
                # A backgrounded errand can outlive the turn that sent it — `wait_for_all` gives
                # up after `errands.WAIT_SECONDS` — and once that happens nothing else would
                # ever tell it to stop, because the turn's stop switch is disarmed on the way
                # out. See `errands.abandoned`.
                lambda: stopping.asked_to_stop(conversation) or errands.abandoned(conversation, errand_id),
            ),
            name=f"errand-{errand_id}",
            daemon=True,
        )
        worker.start()
        return {
            "sent": objective,
            "note": (
                "The errand is out. Carry on with something else — its findings will arrive "
                "between rounds, and this turn will not end before they do."
            ),
        }

    # Waited on, so it cannot be abandoned — the caller is this frame, and it is still here.
    return _run_errand(path, objective, conversation, errand_id, lambda: stopping.asked_to_stop(conversation))


def _wanted(value) -> bool:
    """Whether to wait, from whatever the model actually sent.

    True unless it clearly said otherwise. A missing argument, `None`, and the string "true"
    all mean wait — models send booleans as strings often enough that reading `"false"` as
    truthy would silently background an errand the caller meant to wait for, which is the one
    failure here that looks like the tool ignoring its own contract.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "0", "")
    return bool(value)


def _run_errand(path: Path, objective: str, conversation: str, errand_id: str, give_up) -> dict:
    """One errand, start to finish. The same code whether it is waited on or not.

    ``give_up`` is asked between the worker's events and is the only way this stops early. A
    predicate rather than a flag because the two callers have different reasons to stop: a
    waited-on errand ends when someone presses Stop, and a backgrounded one ends for that *or*
    because the turn that sent it has already gone.

    Split out so that backgrounding is only a question of *who calls this* — this thread or a
    thread of its own. Two copies of the worker setup, one of which is exercised far less than
    the other, is how the rarely-taken path drifts until it is quietly broken.
    """
    # Imported here, not at module scope. `kith.tools.host` lives in this package's `__init__`,
    # which imports this module to register the tool, so the edge only exists at call time.
    # `agent_loop` is the other direction of the same knot: it is handed a `ToolHost` precisely
    # so it never imports the tool layer, and this is the one tool that needs the loop back.
    from kith.config import default_config, ollama_host
    from kith.services.agent_loop import stream_agent
    from kith.tools import host as tool_host

    config = default_config()
    report = _Watched(objective, conversation, errand_id)
    for event in stream_agent(
        _scratchpad(config.system or "", objective),
        config,
        ollama_host(),
        path,
        # No MCP. `tool_schemas` adds every MCP tool regardless of `only`, so a scout built
        # from the ordinary host would be *shown* tools that `run_tool` then refuses — and an
        # arbitrary server's tools have not been through the read-only classification above,
        # so showing them is not the fix either.
        tool_host(path, mcp=[]),
        max_rounds=int(tuning.value("subtask_rounds")),
        allow=set(GIVEN),
        # The parent's conversation, which is not about recording anything — nothing here
        # reaches the transcript. It is what keeps this on the same upstream as the turn that
        # called it, so the persona it shares with that turn is a cache read and not a write.
        conversation_id=session_context.current(),
        # See `frozen.begin`: a worker's output is its final message, so rounds spent being
        # told to write files it cannot write are rounds thrown away.
        landing_reserve=0,
    ):
        report.saw(event)
        # Between the worker's own events, which is the only place an errand can be interrupted
        # without abandoning a tool call half-done — the same granularity the route already
        # applies to the turn. Before this, Stop did nothing for the length of an errand: the
        # parent loop emits no events while a tool call is running, so its own check could not
        # fire, and a person clicking Stop watched four scouts carry on regardless.
        if give_up():
            report.stopped()
            break
    answer = report.done()
    report.finished(answer)
    errands.deliver(conversation, errand_id, answer)
    return answer


def _scratchpad(persona: str, objective: str) -> list[dict]:
    """The worker's entire conversation: who it is, and what it was sent for.

    Two messages, and nothing carried over from the turn that called it. That is the contract
    in both directions — the caller's context does not leak in, which is what makes this
    cheap, and so the objective has to stand on its own, which is what the tool description
    asks for and what a vague one fails at.
    """
    return [
        {"role": "system", "content": f"{persona}\n\n{BRIEF}" if persona else BRIEF},
        {"role": "user", "content": objective},
    ]


class _Report:
    """What comes back, assembled from what the worker's loop emitted.

    The text is deliberately *not* everything it said. A round that calls tools usually opens
    with a line of narration — "let me check the ledger first" — and concatenating those with
    the final answer produces a report that reads like a transcript of someone thinking. So
    the buffer is cleared every time a tool call goes out, which leaves exactly the text after
    the last tool result: the report, and only the report.

    **And the stretch before it is kept, because clearing alone lost four real reports.**
    Measured 2026-08-20: four errands on the Sadeef AI codebase did the work — 26, 27, 24 and
    43 tool calls, `read_file` twelve to thirty-four times each — and every one came back
    "The sub-agent finished without reporting anything." No error, nothing stopped. Two ways
    that happens and both were live:

    * `_final_answer` forces an answer with `tool_choice="none"` and scrubs tool markup on the
      way out, because a model part-way through a tool-using turn narrates calls as prose. A
      worker that narrates one *instead of* answering has its whole report scrubbed to "".
    * The worker says its report and then makes one more call. The clear is correct for
      narration and catastrophic for a finished report, and nothing here could tell them apart.

    So the last non-empty stretch is held as a fallback. Losing a paragraph of narration is a
    cosmetic cost; losing forty-three calls of work is the errand failing silently, which is
    worse than it failing loudly — the caller reads "nothing found" as an answer.
    """

    def __init__(self) -> None:
        self.text = ""
        #: The last non-empty thing it said before a tool call cleared the buffer. The fallback
        #: when the forced final answer comes back empty.
        self.said_before = ""
        self.calls: list[str] = []
        self.error = ""
        self.cut_short = False

    def stopped(self) -> None:
        """Someone pressed Stop while this was out."""
        self.cut_short = True

    def saw(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "delta" and event.get("role") == "text":
            self.text += str(event.get("text") or "")
        elif kind == "tool_call":
            if self.text.strip():
                self.said_before = self.text
            self.text = ""
            self.calls.append(str(event.get("name") or ""))
        elif kind == "error":
            self.error = str(event.get("message") or "")

    def done(self) -> dict:
        """The one thing that enters the caller's context, so it is kept to what it needs.

        `looked_at` is there for a reason beyond interest: a report that reads confidently off
        two greps and a report that reads confidently off eleven files are not equally worth
        believing, and the caller cannot tell them apart from the prose.
        """
        findings = self.text.strip() or self.said_before.strip()
        answer: dict = {
            # Named on the result, not only asked for in the brief, because the brief is advice
            # to the worker and this is a fact about the value. A scout's report is confident
            # prose with exact file:line citations, which is the precise shape `update_task`'s
            # `verification` argument wants — so on 2026-08-19 three errands describing existing
            # Odoo code became the evidence for closing three tasks, with eleven checklist items
            # ticked, three deliverables filed, and not one file edited. The report was accurate.
            # What was missing was the one thing a summary cannot carry: that it describes the
            # code as found.
            "describes": "the code as it already is — not work anyone has done",
            "findings": findings or _nothing_came_back(self.calls),
        }
        if self.calls:
            answer["looked_at"] = _tally(self.calls)
        if self.cut_short:
            # Not an error — a decision. Whatever it had read by then is still worth handing
            # back, and saying *why* it is short is the difference between "this is partial
            # because you stopped me" and the model concluding the codebase has nothing in it.
            answer["note"] = "Stopped part-way through, on request. What is above is partial."
        if self.error:
            # Alongside the findings rather than instead of them. A worker that read nine files
            # and then lost the provider on its last round still knows eight things worth
            # having, and throwing them away costs the whole delegation.
            answer["error"] = self.error
            answer["note"] = "The sub-agent was cut short — treat what it did report as partial."
        return answer


class _Watched(_Report):
    """A report that also says, as it happens, what the worker is doing.

    The one thing in the Work panel with no second copy anywhere. That panel used to be a
    round-by-round list of tool calls and it was deliberately deleted — it was the same list
    the thread already renders, four hundred pixels away from where you read it. This is the
    exception the deletion left room for: a worker's greps and reads are thrown away on
    purpose and never reach the thread, so a panel that does not show them means nobody ever
    sees them. Without it, sending three scouts out looks like the interface hanging.

    Published rather than yielded, and that is what keeps it cheap. The feed is a live push
    that never touches the transcript, so nothing here is stored, re-sent to a model, or
    billed — and the tool contract is unchanged: a handler still returns one value.

    A feed line must never be the thing that takes a turn down, so every publish is guarded.
    """

    def __init__(self, objective: str, conversation: str, errand_id: str) -> None:
        super().__init__()
        self.objective = objective
        self.conversation = conversation
        self.id = errand_id
        self._say("running", objective)

    def _say(self, state: str, text: str, tool: str = "", arguments: dict | None = None) -> None:
        try:
            from kith.services.activity import feed, short_args

            feed.publish(
                "errand",
                text,
                tool=tool or None,
                args=short_args(arguments or {}) or None,
                errand={"id": self.id, "state": state, "objective": self.objective},
                conversation=self.conversation,
            )
        except Exception:
            pass

    def saw(self, event: dict) -> None:
        super().saw(event)
        if event.get("type") == "tool_call":
            name = str(event.get("name") or "")
            # `tool` and `args` rather than a formatted sentence, so the interface picks the
            # wording. It already holds the only table of phrases there is, and a second one
            # here would be a second thing to keep in step with the registry.
            self._say("step", name, tool=name, arguments=event.get("arguments") or {})

    def finished(self, answer: dict) -> None:
        """The closing line, carrying what the errand cost rather than what it found.

        The findings themselves stay out of the feed on purpose. They are already going into
        the conversation as the tool result, rendered properly, and a second copy in a 400px
        column would be a paragraph squeezed into a gutter — the panel's job here is to say
        that the looking is over and how much of it there was.
        """
        self._say("done", str(answer.get("looked_at") or "nothing to report"))


def _nothing_came_back(calls: list[str]) -> str:
    """What to say when the worker really did produce no prose at all.

    Naming the work it did is the point. "Nothing found" and "it looked at forty-three things
    and then said nothing" are the same string to a caller that cannot see inside, and the first
    one reads as an answer — which is how a silent failure becomes a conclusion.
    """
    if not calls:
        return "The sub-agent made no calls and reported nothing. Treat this as a failure, not as an answer."
    return (
        f"The sub-agent made {len(calls)} calls and then reported nothing, so this is a failure "
        "rather than a finding — do not read it as 'there is nothing there'. Ask again with a "
        "narrower objective, or look yourself."
    )


def _tally(calls: list[str]) -> str:
    """`grep x3, read_file x5` — in the order they were first reached for."""
    counted: dict[str, int] = {}
    for name in calls:
        counted[name] = counted.get(name, 0) + 1
    return ", ".join(name if n == 1 else f"{name} x{n}" for name, n in counted.items())
