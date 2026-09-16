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
        # The board, read. A scout asked "has any of this been done before" needs to be able
        # to look, and none of these change anything.
        "list_projects",
        "list_tasks",
        "view_task",
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
#: applied, which is the reading `tools/delegation`'s module docstring has always argued for:
#: the agent that made the change should read the failure.
WRITING = frozenset(
    {
        "write_file",
        "edit_files",
        "delete_file",
        "rename_symbol",
    }
)

#: What a sub-agent is deliberately not given, and why. Kept as a real list rather than "the
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
        "add_milestone",
        "add_checklist_item",
        "add_deliverable",
        "check_item",
        "create_project",
        "update_milestone",
        "update_project",
        "update_task",
        "take_in_shared_tasks",
        # Time. Nothing a scout finds is a reason to wake someone up later.
        "schedule",
        "cancel_schedule",
        "list_schedules",
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
        # Itself, in all three of its spellings. One worker cannot become a tree of them, and
        # `follow_up` is on this list for a second reason as well as that one: a worker has no
        # id to follow up, because ids are handed to the agent holding the conversation.
        "delegate_subtask",
        "send_builder",
        "follow_up",
    }
)

#: What a **scout** is told it is. Appended to the persona rather than replacing it, and that
#: is the cheap choice as well as the right one: `llm/caching` treats `config.system` as the
#: stable head, so a sub-agent whose system prompt *starts* with the same persona reads the
#: same cached prefix every other request in the install reads, instead of writing its own.
SCOUT_BRIEF = (
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
    "You are working as an isolated builder right now. Someone — you, a moment ago, in the "
    "conversation this came from — handed you one thing to change, and you are working in a "
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


@tool(
    "delegate_subtask",
    "Send an isolated sub-agent to find something out. It reads and searches in a private "
    "scratchpad that never enters this conversation, and comes back with a few hundred words. "
    "WHAT IT SAVES YOU IS CONTEXT, NOT ROUNDS. Doing it yourself is already cheap in rounds — "
    "read_file and repo_map take a list of paths, grep takes a list of patterns — but twelve "
    "files you open yourself are in your window for the rest of the turn and re-sent on every "
    "round after it, and twelve files an errand opens cost you its summary and nothing else. "
    "REACH FOR ONE WHEN: you are about to open more than three or four files you have not read "
    "before just to find out where something lives; you need to understand a subsystem you do "
    "not already know; or the answer is one paragraph buried in long documentation. Give it "
    "one self-contained objective in plain language with any paths or names you have, since it "
    "starts knowing nothing about what you are doing. It can read, search and browse; it "
    "cannot change anything, run anything, or ask you a question — send the finding out and "
    "keep the doing. Send several in ONE round to cover several areas: they run in parallel, "
    "and sent in separate rounds they do not. A report that answers most of what you asked is a "
    "follow_up, not a fresh errand.",
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
    return _send(path, "scout", args)


@tool(
    "send_builder",
    "Send an isolated builder to make a change, and get back a patch. It works in a private "
    "copy of the repository that nobody else touches, and hands back a unified diff plus a "
    "few hundred words — the diff is taken from its copy automatically, and YOU apply it. "
    "USE ONE WHEN the change is well understood but long: a rename across eleven files, the "
    "same fix in six call sites, a migration written from a schema you have already read. "
    "DO NOT use one to decide what to do — it starts knowing nothing about your plan, so a "
    "vague objective comes back as a confident patch built on a guess. Say exactly what to "
    "change, in which files, and what done looks like. It can read, search and edit; it "
    "cannot commit, run anything, run the tests, or ask you a question, so anything that "
    "needs running comes back to you. Send several in ONE round for separate areas: each gets "
    "its own copy, so they run in parallel and cannot collide — but two builders given "
    "overlapping files produce two patches that will not both apply. A patch that is nearly "
    "right is a follow_up, not a second builder: it still has its copy and its brief.",
    {
        "objective": {
            **STR,
            "description": (
                "What to change, precisely: the files, the change, and what done looks like. "
                "It has none of your context and cannot ask, so anything you leave out it "
                "will invent."
            ),
        },
        "wait": {
            **BOOL,
            "description": (
                "Whether to wait for the patch. True by default. Pass false only when the "
                "change is long AND you have unrelated work to get on with: it returns "
                "immediately and the patch arrives between rounds."
            ),
        },
    },
    required=("objective",),
)
def send_builder(path: Path, args: dict):
    return _send(path, "builder", args)


@tool(
    "follow_up",
    "Ask a worker you already sent one more thing, without making it start over. It still has "
    "its brief and its own last report, and a builder still has the copy of the repository it "
    "edited — so 'your patch misses the test file' costs it a couple of rounds instead of the "
    "whole search again. Use the id that came back with the report. What it does NOT still "
    "have is every file it read on the way: those are gone, which is the point of an errand. "
    "So a follow-up that needs the same ground covered again is cheaper sent as a fresh "
    "errand with a sharper objective.",
    {
        "worker_id": {**STR, "description": "The id that came back with the report."},
        "ask": {
            **STR,
            "description": (
                "The one more thing. Self-contained, like the original: it knows what it was "
                "sent to do and what it said, and nothing about the rounds you have had since."
            ),
        },
    },
    required=("worker_id", "ask"),
)
def follow_up(path: Path, args: dict):
    from kith.infra.db import repositories as repo

    worker_id = str(args.get("worker_id") or "").strip()
    ask = str(args.get("ask") or "").strip()
    if not worker_id or not ask:
        return {"error": "Say which worker, and what else you want from it."}

    row = repo.workers.get(path, worker_id)
    if row is None:
        return {"error": f"No worker {worker_id}. The id comes back with the report."}
    if str(row.get("state")) == "out":
        return {"error": f"Worker {worker_id} has not reported yet — wait for it."}
    if str(row.get("state")) == "spent":
        return {
            "error": (
                f"Worker {worker_id} has been cleared away, so there is nothing left to ask. "
                "Send a fresh one with what you know now."
            )
        }

    carried = repo.workers.scratchpad(path, worker_id)
    if not carried:
        return {"error": f"Worker {worker_id} kept nothing that can be resumed."}

    role = str(row.get("role") or "scout")
    worktree = str(row.get("worktree") or "")
    conversation = session_context.current()
    return _run_worker(
        path,
        role=role,
        objective=ask,
        conversation=conversation,
        worker_id=worker_id,
        worktree=worktree,
        give_up=lambda: stopping.asked_to_stop(conversation),
        carried=[*carried, {"role": "user", "content": ask}],
    )


def _send(path: Path, role: str, args: dict):
    """Open a worker of one role and run it, waited on or not. The two tools' whole bodies.

    One function rather than one per role, because the difference between a scout and a
    builder is entirely in the tables above — the set it is given, the brief it is told, the
    model it runs on, how many rounds it gets, and whether it needs a copy of the tree. None
    of that is control flow, so none of it should be a second copy of this.
    """
    from kith.infra.db import repositories as repo
    from kith.infra.workspace import worktrees
    from kith.infra.workspace.base import WorkspaceError

    objective = str(args.get("objective") or "").strip()
    if not objective:
        return {"error": "A sub-agent needs an objective — say what you want done."}

    # One id per worker, so the panel can tell three sent in the same round apart, and so the
    # report carries something `follow_up` can name. A conversation carried through by hand
    # rather than read off the context inside `_Watched`: a backgrounded worker runs on a
    # thread of its own, and `current()` read from in there is read at the wrong moment.
    conversation = session_context.current()
    worker_id = uuid.uuid4().hex[:8]

    worktree = ""
    if role == "builder":
        # Tidying happens here rather than on a timer, and here is the only place it can
        # honestly happen: there is no housekeeping pass in this application, and a scheduler
        # tick that swept worktrees would be a second mechanism built for one caller. Opening
        # a builder is the moment that both cares about the answer and knows the database.
        for stale in worktrees.prune():
            repo.workers.spend(path, stale)
        try:
            made = worktrees.open_for(worker_id)
        except WorkspaceError as exc:
            return {"error": str(exc)}
        if made is None:
            # Refused rather than run without isolation. A builder that cannot be given its
            # own copy would have to edit the real tree, and "he could not be isolated, so he
            # changed your files instead" is the one outcome this design exists to prevent.
            return {
                "error": (
                    "There is no repository here to make a private copy of, so a builder has "
                    "nowhere safe to work. Make the change yourself, or delegate_subtask to "
                    "find out what it should be."
                )
            }
        worktree = str(made)

    repo.workers.start(path, worker_id, conversation, role, objective, worktree)

    if not _wanted(args.get("wait")):
        if not errands.sent(conversation, worker_id, objective):
            return {
                "error": (
                    f"You already have {errands.MAX_OUT} errands out. Wait for those to come "
                    "back before sending more, or send this one with wait=true."
                )
            }
        # The context is copied here, on the thread that has one, for the same reason the round
        # loop copies it before handing work to its pool: a thread starts on the defaults, and
        # every tool the worker runs resolves its paths, its project and its permissions through
        # `session_context`. A worker that lost it would read the wrong folder.
        carried_context = copy_context()
        worker = threading.Thread(
            target=lambda: carried_context.run(
                _run_worker,
                path,
                role,
                objective,
                conversation,
                worker_id,
                worktree,
                # A backgrounded worker can outlive the turn that sent it — `wait_for_all` gives
                # up after `errands.WAIT_SECONDS` — and once that happens nothing else would
                # ever tell it to stop, because the turn's stop switch is disarmed on the way
                # out. See `errands.abandoned`.
                lambda: stopping.asked_to_stop(conversation) or errands.abandoned(conversation, worker_id),
            ),
            name=f"worker-{worker_id}",
            daemon=True,
        )
        worker.start()
        return {
            "worker_id": worker_id,
            "sent": objective,
            "note": (
                "The errand is out. Carry on with something else — its findings will arrive "
                "between rounds, and this turn will not end before they do."
            ),
        }

    # Waited on, so it cannot be abandoned — the caller is this frame, and it is still here.
    return _run_worker(
        path,
        role,
        objective,
        conversation,
        worker_id,
        worktree,
        lambda: stopping.asked_to_stop(conversation),
    )


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


def _run_worker(
    path: Path,
    role: str,
    objective: str,
    conversation: str,
    worker_id: str,
    worktree: str = "",
    give_up=None,
    carried: list[dict] | None = None,
) -> dict:
    """One worker, start to finish. The same code whether it is waited on, backgrounded, or resumed.

    ``give_up`` is asked between the worker's events and is the only way this stops early. A
    predicate rather than a flag because the callers have different reasons to stop: a
    waited-on worker ends when someone presses Stop, and a backgrounded one ends for that *or*
    because the turn that sent it has already gone.

    ``carried`` is a resumed worker's stored scratchpad with the new question already on the
    end; None means a fresh one, which gets :func:`_scratchpad` instead. That one parameter is
    the whole of "a worker persists": there is no thread to revive, because a worker was never
    a thread — it is a message list and this function.

    Split out so that backgrounding and resuming are only a question of *who calls this*. Two
    copies of the worker setup, one of which is exercised far less than the other, is how the
    rarely-taken path drifts until it is quietly broken.
    """
    # Imported here, not at module scope. `kith.tools.host` lives in this package's `__init__`,
    # which imports this module to register the tool, so the edge only exists at call time.
    # `agent_loop` is the other direction of the same knot: it is handed a `ToolHost` precisely
    # so it never imports the tool layer, and this is the one tool that needs the loop back.
    from kith.config import default_config, merge_overrides, ollama_host
    from kith.infra.db import repositories as repo
    from kith.infra.workspace import worktrees
    from kith.services.agent_loop import stream_agent
    from kith.tools import host as tool_host

    shape = ROLES.get(role) or ROLES["scout"]
    give_up = give_up or (lambda: stopping.asked_to_stop(conversation))

    config = default_config()
    chosen = shape["model"]()
    if chosen:
        config = merge_overrides(config, {"model": chosen})

    started = _scratchpad(config.system or "", shape["brief"](), objective) if carried is None else carried
    report = _Watched(objective, conversation, worker_id, role)

    # Everything below happens with the worker pinned to its own copy of the repository, when
    # it has one. This single `with` is the entire isolation mechanism: `paths.base_dir` reads
    # it, every path in the application goes through `paths.resolve` to get there, and
    # `permissions` reads it to know the copy is a folder he may write in. No tool knows it
    # moved. For a scout `worktree` is "" and this is a no-op, which is the honest shape —
    # a worker that cannot write needs nowhere private to write in.
    # `mirror_of` is not optional here and the comment is the receipt for why: without it an
    # absolute path into the real project is honoured as written, and a builder edits your
    # files while its copy sits empty. See `paths._bent_into_the_copy`.
    with session_context.working_from(worktree, mirror_of=str(_source_of(worktree)) if worktree else ""):
        for event in stream_agent(
            started,
            config,
            ollama_host(),
            path,
            # No MCP. `tool_schemas` adds every MCP tool regardless of `only`, so a scout built
            # from the ordinary host would be *shown* tools that `run_tool` then refuses — and an
            # arbitrary server's tools have not been through the read-only classification above,
            # so showing them is not the fix either.
            tool_host(path, mcp=[]),
            max_rounds=shape["rounds"](),
            allow=shape["given"](),
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
        answer["worker_id"] = worker_id
        if role == "builder" and worktree:
            answer["patch"] = _patch(worktrees.diff_in(Path(worktree)), answer)

    repo.workers.reported(path, worker_id, _keeping(started, answer), str(answer.get("findings") or ""), 1)
    report.finished(answer)
    errands.deliver(conversation, worker_id, answer)
    return answer


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


def _keeping(started: list[dict], answer: dict) -> list[dict]:
    """The scratchpad worth storing for a follow-up: the brief, the objective, and the report.

    **Not the rounds in between, and that is a real limit rather than an oversight.** The loop
    copies the message list it is handed (`agent_loop._run_turn`: ``convo = list(messages)``),
    so what the worker actually read is not reachable from out here without changing the loop's
    contract for every caller — and the files it opened are exactly what delegation exists to
    keep out of anybody's context. Storing them would put the expensive thing back.

    So a resumed worker knows what it was sent to do and what it concluded, not how it got
    there. That is weak for a scout — a follow-up needing the same ground covered is better
    sent as a fresh errand, which is what `follow_up`'s description says — and strong for a
    builder, because a builder's real state is not in its transcript at all. It is the edits
    sitting in its copy of the repository, which are still there, and which it can read back
    with the same tools it wrote them with.
    """
    kept = [message for message in started if message.get("role") in ("system", "user")][:2]
    return [*kept, {"role": "assistant", "content": str(answer.get("findings") or "")}]


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

    def __init__(self, objective: str, conversation: str, errand_id: str, role: str = "scout") -> None:
        super().__init__()
        self.objective = objective
        self.conversation = conversation
        self.id = errand_id
        #: Which kind is out. Carried on the feed line rather than inferred from the objective,
        #: because the panel's whole job here is to say what is happening to your files: "2 out"
        #: reads the same whether both are reading or both are editing your repository, and only
        #: one of those is worth interrupting.
        self.role = role
        self._say("running", objective)

    def _say(self, state: str, text: str, tool: str = "", arguments: dict | None = None) -> None:
        try:
            from kith.services.activity import feed, short_args

            feed.publish(
                "errand",
                text,
                tool=tool or None,
                args=short_args(arguments or {}) or None,
                errand={"id": self.id, "state": state, "objective": self.objective, "role": self.role},
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
