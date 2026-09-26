"""One agent a workflow sent, from brief to report.

A workflow script (`tools/workflow`) decides *what* to send and in what order; this is what
running one of them means. It used to be the body of three tools the model could reach for on
any round — `delegate_subtask`, `send_builder` and `follow_up` — and the record says the model
never chose to: 25 scouts in all history, every one right after being told to, and zero
builders across two runs whose prompts named parallel work. So the choice moved to the person
(`/workflow`) and the decomposition moved into code, and what is left here is the part that was
always right.

Two kinds, and the difference between them is entirely in the tables below — what each may
use, what it is told, how many rounds it gets, and which model it runs on:

**A scout reads.** It is given :data:`GIVEN` and `run_tool` enforces that set as well as showing
it, so naming `write_file` anyway gets a refusal rather than a write.

**A builder edits a private copy.** :data:`GIVEN` plus :data:`WRITING`, pinned to its own `git
worktree` by one context variable (`session_context.working_from`), so every path it touches is
bent into the copy and no tool knows it moved. What comes back is a unified diff, and the agent
holding the conversation applies it — a changed worktree left for someone to integrate is not
an option for a program that often runs with nobody there.

**Neither can start a workflow**, so one run cannot become a tree of them, and neither can ask
anyone anything — nobody is watching an agent's scratchpad.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from kith.kernel import session_context, stopping
from kith.services import tuning

#: What a scout may do: read, search, look up. Nothing here writes a file, starts a
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
        "repo_map",
        "find_symbol",
        # Reading it.
        "read_file",
        "check_code",
        # What the repository has been doing — uncommitted, and the recorded history with
        # `commits`.
        "changes",
        # The open web. Read-only, and the reason one tool covers "find where this lives" and
        # "find out how this library works" instead of two.
        "web_search",
        "browse_page",
        # What Kith already knows, which is often where the answer is and is always cheaper
        # than going and looking again.
        "recall",
        "read_journal",
        "read_source",
        "search_sources",
        "read_skill",
        # The design system, read. A builder told to implement a screen needs to know which
        # palette and which components the product already has, for the same reason it needs
        # `read_skill`: the alternative is inventing a second one.
        "read_design_system",
        # And the icons it is drawn with, for the same reason: a builder with no set to reach
        # for draws its own, and hand-written path data is the part of a screen models get wrong.
        "find_icons",
        # The board, read. A scout asked "has any of this been done before" needs to be able
        # to look, and none of these change anything.
        "list_projects",
        "list_tasks",
    }
)

#: What a **builder** adds to :data:`GIVEN`, and the entire difference between the two roles.
#:
#: Four tools, and the smallness is the design. A builder is a worker that can change files in
#: its own copy of the repository and hand back a patch; it is not a second agent with a life
#: of its own. Everything in :data:`WITHHELD` is withheld from it too — it cannot commit,
#: publish, run a command, file a task, remember anything, ask anyone, or send a worker of its
#: own. The only new verb is "edit".
#:
#: **Why no `shell` and no `run_tests`, when a builder obviously wants both.** They have side
#: effects outside the worktree — a package installed, a port bound, a database migrated — and
#: side effects are precisely what would stop two builders sharing a round. The parallelism is
#: worth more than the convenience, and the main agent can run the tests on the patch it just
#: applied — the agent that made the change should read the failure.
WRITING = frozenset(
    {
        "write_file",
        "edit_files",
        "delete_file",
        "rename_symbol",
        # A design system is a file in the worktree like any other, and a builder that has just
        # been made to draw the same card twice should be able to say so in the one place that
        # stops it happening a third time.
        "update_design_system",
    }
)

#: What an agent is deliberately not given, and why. Kept as a real list rather than "the
#: rest of the registry" so that adding a tool forces the question — the test pairs this with
#: :data:`GIVEN` and :data:`WRITING` and fails on any name in none of the three.
#:
#: Withheld from **every** role. There is no tool a builder may use that a scout may not except
#: the four in :data:`WRITING`, which is what keeps one list to read instead of one per role.
WITHHELD = frozenset(
    {
        # Changes the record. A worker's findings are provisional until the main agent has
        # read them; committing, publishing or remembering them makes them permanent first.
        #
        # `publish` also covers what `check_remote` used to be its own name for: fetching is
        # read-ish in intent — it never merges — but it rewrites this repository's remote refs,
        # and a scout has no reason to change what the main agent will see when it next looks
        # at how far behind the folder is.
        "commit",
        "publish",
        # Reaches a person, which is the line a scout does not cross. `show_canvas` puts a
        # design on their screen and interrupts what they were looking at — the main agent
        # decides when that is worth doing, having read what the worker found.
        "show_canvas",
        "remember",
        "forget",
        "journal",
        "set_memory_level",
        # What a plugin is holding. Read-only, and withheld anyway — which is the one entry
        # here that is not about damage.
        #
        # A plugin's store is keyed on the conversation, and a scout's slot is its own: the
        # answer it would get is empty, and an empty answer to a question that looks answerable
        # is worse than not being offered the question. It also costs a schema on every round of
        # the scout's turn to say so. The main agent has the state and the surface in front of
        # it; the scout has a search to do.
        "plugin_state",
        # The board, written. A sub-agent that files tasks is a sub-agent making plans, and
        # the plan is the one thing that must stay in the conversation a person can see.
        "add_task",
        "plan_work",
        "checklist",
        "add_deliverable",
        "create_project",
        "update_project",
        "update_task",
        "take_in_shared_tasks",
        # Time. Nothing a scout finds is a reason to wake someone up later.
        "schedule",
        "cancel_schedule",
        "list_schedules",
        # Runs things. Withheld for two reasons at once: they have side effects, and side
        # effects are exactly what would stop several of these sharing a round. `run_tests`
        # is the one that will keep being asked for, and the answer is that the agent holding
        # the conversation runs them on the patch it applies — the one who made the change
        # should read the failure.
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
        # Starting, watching or stopping a workflow. One run cannot become a tree of them, and
        # the opt-in that permits one belongs to the person, which no agent is.
        "run_workflow",
        "check_workflow",
        "stop_workflow",
    }
)

#: What a **scout** is told it is. Appended to the persona rather than replacing it, and that
#: is the cheap choice as well as the right one: `llm/caching` treats `config.system` as the
#: stable head, so a sub-agent whose system prompt *starts* with the same persona reads the
#: same cached prefix every other request in the install reads, instead of writing its own.
SCOUT_BRIEF = (
    "You are working as an isolated sub-agent right now. A workflow — a script you wrote a "
    "moment ago, in the conversation this came from — handed you one thing to find out, and "
    "the only part of "
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


#: What a **builder** is told it is. The scout's brief inverted at exactly one point, and that
#: point is the dangerous one.
#:
#: A scout is forbidden from writing that anything has been implemented, because it cannot
#: change a byte and a report that reads like a changelog got three tasks closed that nobody
#: had done (see :data:`SCOUT_BRIEF`). A builder *did* change things, so the same sentence is
#: now true — and the failure mode flips with it: the risk is no longer a finder describing
#: found code as built, it is a builder describing intended code as built. Its patch is the
#: only evidence that survives, so the brief points at the patch rather than at prose.
BUILDER_BRIEF = (
    "You are working as an isolated builder right now. A workflow — a script you wrote a "
    "moment ago, in the conversation this came from — handed you one thing to change, and "
    "you are working in a "
    "private copy of the repository that nobody else can see. Everything you read on the way "
    "is discarded when you finish. What survives is your patch and a few hundred words.\n\n"
    "So read as much as you need, and do not summarise as you go.\n\n"
    "You can read, search, look things up, and edit files in your copy. You cannot commit, "
    "push, run a command, run the tests, file anything, remember anything, or ask anyone a "
    "question. If the change needs one of those, make the edits you can and say plainly in "
    "your report what still needs running or deciding.\n\n"
    "Your edits ARE the deliverable — the patch is taken from your copy automatically, so "
    "never paste code into your report and never describe an edit you did not actually make. "
    "An edit you only described is the one failure that cannot be caught downstream: the agent "
    "reading you sees confident prose and an empty patch, and has no way to tell that from a "
    "change that was genuinely unnecessary.\n\n"
    "Your report is read by an agent that has none of your context and has not seen your "
    "copy. So:\n"
    "- Say what you changed and why, in a sentence or two.\n"
    "- Name every file you touched, with `path:line` for anything subtle.\n"
    "- Say plainly what you could NOT do, and what still needs running, testing or deciding.\n"
    "- If you changed nothing, say so and say why — that is a real answer, not a failure.\n"
    "No preamble, no restating the objective, no offers to do more."
)


#: What each role is: what it may do, what it is told, how long it gets, and what it runs on.
#: A table rather than branches, so adding a role is data and reading the difference between
#: two of them is one glance rather than a diff of two near-identical functions.
ROLES: dict[str, dict] = {
    "scout": {
        "given": lambda: set(GIVEN),
        "brief": lambda: SCOUT_BRIEF,
        "rounds": lambda: int(tuning.value("subtask_rounds")),
        "model": lambda: str(tuning.value("scout_model") or "").strip(),
    },
    "builder": {
        "given": lambda: set(GIVEN) | set(WRITING),
        "brief": lambda: BUILDER_BRIEF,
        "rounds": lambda: int(tuning.value("builder_rounds")),
        # Always the main model, and not a knob. A builder writes code that is kept, and a
        # cheap model's patch costs more to review than it saved to produce.
        "model": lambda: "",
    },
}


class WorkerError(RuntimeError):
    """An agent could not be started at all, with a reason worth reading."""


def open_copy(agent_id: str) -> str:
    """A private checkout for one builder, or :class:`WorkerError` saying why there is none.

    Refused rather than run without isolation. A builder that cannot be given its own copy would
    have to edit the real tree, and "it could not be isolated, so it changed your files instead"
    is the one outcome builders exist to prevent.

    Tidying happens here rather than on a timer, and here is the only place it can honestly
    happen: there is no housekeeping pass in this application, and opening a copy is the moment
    that both cares about the answer and knows where the copies live.
    """
    from kith.infra.workspace import worktrees
    from kith.infra.workspace.base import WorkspaceError

    worktrees.prune()
    try:
        made = worktrees.open_for(agent_id)
    except WorkspaceError as exc:
        raise WorkerError(str(exc)) from None
    if made is None:
        raise WorkerError(
            "There is no repository here to make a private copy of, so a builder has nowhere "
            "safe to work. Send a scout to find out what the change should be instead."
        )
    return str(made)


def run_agent(
    path: Path,
    kind: str,
    objective: str,
    conversation: str,
    *,
    worktree: str = "",
    give_up: Callable[[], bool] | None = None,
    watch: Callable[[dict], None] | None = None,
    carried: list[dict] | None = None,
    tools: set[str] | None = None,
    rounds: int | None = None,
) -> tuple[dict, _Report]:
    """One agent, start to finish. Returns its answer and the report it was assembled from.

    ``give_up`` is asked between the agent's events and is the only way this stops early —
    between events is the only place a loop can be interrupted without abandoning a tool call
    half-done. ``watch`` sees every event as it happens; a workflow uses it to put the agent's
    steps in the Work panel, since they are thrown away on purpose and reach nothing else.

    ``carried``, ``tools`` and ``rounds`` exist for one caller: the repair round a workflow runs
    when an agent was asked for JSON and did not end with any. It is handed the agent's brief,
    objective and report plus one more question, with no tools and a single round.
    """
    # Imported here, not at module scope. `kith.tools.host` lives in this package's `__init__`,
    # which imports the workflow tool, which imports this module, so the edge only exists at
    # call time. `agent_loop` is the other direction of the same knot: it is handed a `ToolHost`
    # precisely so it never imports the tool layer, and this is where the loop is needed back.
    from kith.config import default_config, merge_overrides, ollama_host
    from kith.infra.workspace import worktrees
    from kith.services.agent_loop import stream_agent
    from kith.tools import host as tool_host

    shape = ROLES.get(kind) or ROLES["scout"]
    give_up = give_up or (lambda: stopping.asked_to_stop(conversation))

    config = default_config()
    chosen = shape["model"]()
    if chosen:
        config = merge_overrides(config, {"model": chosen})

    started = _scratchpad(config.system or "", shape["brief"](), objective) if carried is None else carried
    report = _Report()
    began = time.monotonic()

    # Everything below happens with a builder pinned to its own copy. This single `with` is the
    # entire isolation mechanism: `paths.base_dir` reads it, every path in the application goes
    # through `paths.resolve` to get there, and `permissions` reads it to know the copy is a
    # folder it may write in. For a scout `worktree` is "" and this is a no-op, which is the
    # honest shape — an agent that cannot write needs nowhere private to write in.
    # `mirror_of` is not optional: without it an absolute path into the real project is honoured
    # as written, and a builder edits your files while its copy sits empty. See
    # `paths._bent_into_the_copy`.
    with session_context.working_from(worktree, mirror_of=str(_source_of(worktree)) if worktree else ""):
        for event in stream_agent(
            started,
            config,
            ollama_host(),
            path,
            # No MCP and no plugins. `tool_schemas` appends both regardless of `only`, and the
            # loop allows whatever it offered — so an agent was being handed every plugin
            # command, and a builder asked for a mockup opened a real browser window with one.
            # Neither has been through the classification above.
            tool_host(path, mcp=[], plugin=[]),
            max_rounds=rounds if rounds is not None else shape["rounds"](),
            allow=tools if tools is not None else shape["given"](),
            # The parent's conversation, which is not about recording anything — nothing here
            # reaches the transcript. It keeps this on the same upstream as the conversation that
            # sent it, so the persona it shares with that conversation is a cache read.
            conversation_id=conversation,
            # See `frozen.begin`: an agent's output is its final message, so rounds spent being
            # told to write files and file deliverables are rounds thrown away.
            landing_reserve=0,
        ):
            report.saw(event)
            if watch is not None:
                try:
                    watch(event)
                except Exception:
                    pass  # a progress line must never be the thing that takes an agent down
            if give_up():
                report.stopped()
                break

        answer = report.done()
        if kind == "builder":
            # A builder did change things, so the scout's "this describes the code as found"
            # would be false of its report — the one sentence that must never be.
            answer.pop("describes", None)
            if worktree:
                answer["patch"] = _patch(worktrees.diff_in(Path(worktree)), answer)

    _record_cost(path, kind, objective, report, began)
    return answer, report


def _record_cost(path: Path, role: str, objective: str, report: _Report, began: float) -> None:
    """Put this worker's turn on the flight recorder.

    ``mode`` is the role — ``scout`` or ``builder`` — rather than a single ``worker``, because
    the two have different shapes and averaging them hides both: a scout reads and reports, a
    builder edits in a worktree and runs tests, and "what does a turn cost" has two answers.
    The column already carries one value per kind of turn (``chat`` is the only other one still
    written; ``breakout``, ``reply`` and ``start`` are the dead tick's, last seen 2026-08-07).

    ``context`` is left None, unlike the chat route's row. The reading it would carry comes from
    ``report.last_reading``, which is keyed on a conversation id — and a worker deliberately
    runs on its *parent's* id so the persona stays a cache read, so asking here would return the
    parent's ledger and file it against the child. A wrong reading is worse than none: the whole
    point of the column is to be able to trust what was in the window.

    Wrapped in the same ``except`` the chat route uses, and for the same reason it gives:
    accounting must never retroactively fail work that already happened and already reported.
    """
    from kith.infra.db import repositories as repo
    from kith.kernel import clock

    try:
        repo.messages.add_turn_log(
            path,
            clock.now_iso(),
            role,
            objective.strip()[:80] or None,
            report.calls,
            report.tokens_in,
            report.tokens_out,
            round(time.monotonic() - began, 2),
            f"error: {report.error}" if report.error else ("stopped" if report.cut_short else "reported"),
            tokens_uncached=report.tokens_uncached,
            failed=report.failed,
        )
    except Exception:
        pass


def _source_of(worktree: str) -> Path | str:
    """The repository a copy was made from, asked of git rather than remembered.

    Read back from the worktree itself (``rev-parse --path-format=absolute --git-common-dir``
    names the original's ``.git``) instead of being threaded down from `_send`. A resumed
    worker is the reason: it is handed only the path its copy sits at, out of a database row
    written in a previous turn, and it needs the same answer as the turn that made it.

    Returns "" when it cannot be established, which turns the rewrite off rather than guessing —
    a wrong original would bend paths into a copy of the wrong repository, which is worse than
    not bending them at all.
    """
    from kith.infra.workspace.git import _git

    if not worktree:
        return ""
    try:
        found = _git(
            "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=Path(worktree), timeout=20
        )
    except Exception:
        return ""
    if found.exit_code != 0:
        return ""
    common = Path(found.output.strip().splitlines()[0].strip() or "")
    return common.parent if common.name == ".git" else ""


def _patch(diff: str, answer: dict) -> str:
    """The builder's changes, or a sentence saying there were none.

    An empty diff is not an error and must not read as one — "nothing needed changing" is a
    real answer to a real objective. But two other things produce exactly the same empty diff,
    and from here all three are indistinguishable, so the note names all three and points at
    the only thing that separates them: what the builder itself said it did.

    The third one is not hypothetical and was missing from the first version of this note. A
    builder given a wide objective spent all sixteen of its rounds reading and reported, in
    its own words, "I made zero edits — I ran out of budget before writing a single
    docstring." A note offering only "nothing needed changing" or "it made it up" would have
    had the agent reading it misdiagnose an honest report of a budget overrun.
    """
    if diff.strip():
        return diff
    answer["note"] = (
        "The builder changed nothing in its copy. Three things look like this and its own "
        "report above is the only way to tell them apart: nothing needed changing, which is a "
        "real answer; it ran out of rounds before it started writing, which means send it "
        "again at less of the work; or it described an edit it never made, which means send "
        "it again and check the patch rather than the prose."
    )
    return ""


def follow_on(kind: str, objective: str, said: str, ask: str) -> list[dict]:
    """An agent's conversation so far — brief, objective, report — with one more question on the end.

    For the repair round a workflow runs when an agent asked for JSON ended without any: the
    agent is handed back exactly what it was told and what it said, and asked only for the
    object. Built here, not by the caller, so the brief and persona match the original run and
    the repair reads the same cached prefix.
    """
    from kith.config import default_config

    shape = ROLES.get(kind) or ROLES["scout"]
    return [
        *_scratchpad(default_config().system or "", shape["brief"](), objective),
        {"role": "assistant", "content": said},
        {"role": "user", "content": ask},
    ]


def _scratchpad(persona: str, brief: str, objective: str) -> list[dict]:
    """The worker's entire conversation: who it is, and what it was sent for.

    Two messages, and nothing carried over from the turn that called it. That is the contract
    in both directions — the caller's context does not leak in, which is what makes this
    cheap, and so the objective has to stand on its own, which is what the tool description
    asks for and what a vague one fails at.
    """
    return [
        {"role": "system", "content": f"{persona}\n\n{brief}" if persona else brief},
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
        #: What the errand cost, for the flight recorder. Collected here rather than by the
        #: caller because this is already the one object reading every event of the turn.
        #:
        #: Until this existed a worker's turn was invisible: `add_turn_log` had exactly one
        #: call site, the chat route, and the loop a worker runs is reached from here instead.
        #: Measured on the live database the day it was added — 63 `delegate_subtask` calls and
        #: 14 `send_builder` across seven weeks, every one of them spawning a turn that spent
        #: tokens and seconds and left no row. So the two questions the recorder exists to
        #: answer, what does he cost and which tools are failing, were being answered from
        #: chat turns alone while a whole second population of turns went uncounted.
        self.rounds = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.tokens_uncached = 0
        #: What the provider said this agent cost, summed over its requests. For the workflow's
        #: closing note, which is the one place a person sees what a fan-out spent.
        self.cost = 0.0
        self.failed: list[str] = []

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
        elif kind == "tool_result":
            # `call_failed` rather than a truthiness check, for the reason the chat route gives
            # at its own copy of this line: several handlers report their own failure inside a
            # successful envelope, and those are the ones worth catching.
            from kith.domain import tooling

            if tooling.call_failed(event.get("result")):
                self.failed.append(str(event.get("name") or ""))
        elif kind == "stats":
            stats = event.get("stats") or {}
            self.rounds += 1
            self.tokens_in += int(stats.get("promptTokens") or 0)
            self.tokens_out += int(stats.get("responseTokens") or 0)
            self.tokens_uncached += int(stats.get("uncachedTokens") or 0)
            self.cost += float(stats.get("costUsd") or 0.0)
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
