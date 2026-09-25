"""Tasks, and working one in depth: thread, checklist, deliverables."""

from __future__ import annotations

from pathlib import Path

from kith.domain import stall, tooling
from kith.domain.enums import TASK_ACTIVE, TASK_PRIORITIES, TASK_SETTLED, TASK_STATUSES
from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.services import board_sync, project_binding, showing
from kith.services.tasks import (
    FILED_THIS_TURN,
    PLANNED_THIS_TURN,
    _mirror_brief,
    _out_of_scope,
    _reopen_if_finished,
    _update_task,
)
from kith.tools import paging
from kith.tools.paging import PAGE_PARAMS
from kith.tools.params import INT, STR
from kith.tools.registry import tool

#: Key in `session_context.turn_notes()` holding the ids of tasks filed during this turn.
#:
#: A set rather than a flag because a turn can file several, and approval is asked about one at
#: a time. Lives in the turn's scratch rather than on the row: "was this filed a moment ago"
#: is a fact about *now*, and a column would still say yes tomorrow.


#: Shortest description that counts as a real "definition of done" on a task that belongs to a
#: project. Trivial standalone errands need none, the same way _verify_done only gates a task that
#: carries a written brief.
_MIN_DONE_CHARS = 24


@tool(
    "add_task",
    "Record a task to pursue — a real unit of work, and an OUTCOME, not an activity. A task is "
    "the thing produced (\"the seed script runs and loads fixtures\"), with a 'description' saying "
    "how you'll KNOW it's done — ideally something runnable (a command that exits 0, a test that "
    'passes, a file that exists). "Verify/inspect/consolidate X" is a done-condition, not a task '
    "of its own. A task under a project MUST carry such a description. 'priority' is high for "
    "what matters most — it is the only ordering there is. Every task starts in 'planning' and "
    "nothing is pickable until your person has approved a plan for it — draft one with the "
    "planning-a-task skill, with the whole checklist written, then hand it over. There are no due "
    "dates: a date you set yourself is not a deadline anyone agreed to. About to file several "
    "tasks at once? plan_work writes the tasks and every checklist item in a single call.",
    {
        "goal": {**STR, "description": "Short title — the outcome, not a verb like 'verify X'."},
        "description": {
            **STR,
            "description": "How you'll know it's done — required for a task under a project; a "
            "runnable check beats prose.",
        },
        "priority": {**STR, "enum": list(TASK_PRIORITIES), "description": "low | normal | high."},
        "status": {**STR, "enum": list(TASK_STATUSES), "description": "Defaults to 'planning'."},
        "project_id": {**INT, "description": "Optional: the project this task belongs to."},
    },
    required=("goal",),
)
def add_task(path: Path, args: dict):
    from kith.services import tuning

    goal = (args.get("goal") or "").strip()
    description = (args.get("description") or "").strip()
    project_id = args.get("project_id")
    foreign = _out_of_scope(path, project_id)
    if foreign is not None:
        return foreign
    scoped = bool(project_id)

    # A task that belongs to real work needs a checkable finish line. Without one, "Verify the
    # Prisma foundation" can never be objectively done, so it grinds forever. Trivial standalone
    # errands are left alone — the same proportionality _verify_done uses on the closing side.
    if scoped and len(description) < _MIN_DONE_CHARS:
        return {
            "ok": False,
            "error": (
                "This task belongs to real work but has no checkable finish line. Add a "
                "'description' saying how you'll KNOW it's done — ideally something runnable (a "
                "command that exits 0, a test that passes, a file that exists), not a bare verb "
                "like 'verify' or 'inspect'. Then file it again."
            ),
        }

    # Don't file a second copy of work already open. The 106≈110 / 107≈111 duplicates were the
    # loop re-decomposing the same stuck piece of work; merge into the existing task rather than
    # grow the pile. Scoped to the same project so unrelated look-alikes aren't collapsed.
    new_sig = stall.signature(f"{goal} {description}")
    for existing in repo.tasks.list_tasks(path):
        if existing.get("status") in TASK_SETTLED or existing.get("project_id") != project_id:
            continue
        prior = stall.signature(f"{existing['goal']} {existing.get('description') or ''}")
        if stall.similar(new_sig, prior, threshold=tuning.value("prose_match")):
            return {
                "ok": True,
                "id": existing["id"],
                "duplicate": True,
                "note": f"Merged into existing open task #{existing['id']} — near-identical goal.",
            }

    # Everything starts in `planning`. The old split — a standalone errand landing straight in a
    # ready-to-pick column — has nowhere to go, because nothing is pickable until a person has
    # approved a plan for it.
    status = args.get("status") or "planning"
    # A task cannot be born approved, and the reason is stronger than "the gate would refuse it":
    # the plan lives at `.kith/work/task-<id>.md`, and the id does not exist until this row does.
    # There is no order of operations in which a brand-new task already has an approved plan, so
    # `approved` here is always a mistake — and left unhandled it was a way straight past
    # `_verify_approvable`, which only guards `update_task`.
    #
    # Filed rather than refused: the work is still wanted, it just starts where everything starts.
    born_approved = status == "approved"
    if born_approved:
        status = "planning"
    made = repo.tasks.add_task(
        path,
        goal,
        args.get("priority") or "normal",
        description,
        status,
        "kith",
        project_id,
    )
    # Filing work under a project is working on that project.
    project_binding.adopt(path, made.get("project_id"))
    # Remembered so `_verify_approvable` can refuse to approve it in the same breath — see the
    # note there. Written here rather than inferred from `created_at` because the question is
    # "was this filed in *this* turn", and a timestamp comparison would need a turn-start time
    # nothing records and would answer differently on a slow turn.
    session_context.turn_notes().setdefault(FILED_THIS_TURN, set()).add(made.get("id"))
    _mirror_brief(path, made.get("id"))
    # And something actionable is a reason to get on with it. The loop only wakes for a session
    # that is working, so before this a task filed in a conversation sat there until someone
    # found the button — you asked for a thing, he wrote it down, and you both waited.
    if status in TASK_ACTIVE:
        reopened = _reopen_if_finished(path, made.get("project_id"))
        if reopened:
            return {**made, "note": reopened}
    if born_approved:
        return {
            **made,
            "note": (
                "Filed in 'planning', not 'approved' — a task cannot start approved, because its "
                "plan lives at .kith/work/task-"
                f"{made.get('id')}.md and there was no id to write it against until now. Write the "
                "plan and the whole checklist, then set 'planning' for them to look at."
            ),
        }
    return made


@tool(
    "take_in_shared_tasks",
    "Take into your board the tasks sitting in this project's `.kith/` folder — work somebody "
    "else did and pushed. `list_tasks` tells you when there is any. Nothing is deleted and "
    "nothing of yours is overwritten by something older: where both changed, the newer one "
    "wins, and a brief with no date on it never does. Run `publish` with direction 'in' first, "
    "or you are reading a folder nobody has fetched.",
    {},
    required=(),
)
def take_in_shared_tasks(path: Path, args: dict):
    return board_sync.take_it_in(path)


@tool(
    "list_tasks",
    "Your tasks, highest priority first. Pass `ids` to open those in full instead — their "
    "description, checklist, comment thread and deliverables — several at once. Without "
    "`ids` you get one page of summaries; check 'more' before assuming you have seen them all.",
    {
        "ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Open these in full rather than listing.",
        },
        "status": {**STR, "enum": list(TASK_STATUSES)},
        **PAGE_PARAMS,
    },
    required=(),
)
def list_tasks(path: Path, args: dict):
    """The board, or a few rows of it opened up.

    `view_task` was a separate tool for the second case: 259 characters of schema, and a
    decision for the model to make, to say what an argument says. Merged here it also becomes
    plural, which is the half that changes behaviour — reviewing a project's work in flight means
    opening the three tasks on it, and that was three rounds. See `domain.tooling.many`.

    **A bare listing stays a listing.** The two were separate for a real reason: a page of
    tasks with every checklist and comment thread attached is a page that costs what reading
    the board used to cost for the whole project. `ids` is the only thing that opens them.
    """
    wanted = [int(one) for one in tooling.many(args, "ids", "id") if str(one).strip()]
    if wanted:
        found = [(one, repo.tasks.task_detail(path, one)) for one in wanted]
        if len(wanted) == 1:
            # **Byte-identical to what `view_task` returned**, which is the rule
            # `domain.tooling.many` already states for every plural tool here: "single-path
            # results are byte-identical to before so no renderer needed a branch". Opening one
            # task is the single path, it is what every replayed `view_task` call asks for, and
            # the interface sniffs result *shapes* rather than tool names — so wrapping it in a
            # list would have quietly changed which card draws a task somebody opened.
            return found[0][1] or {"note": "No such task."}
        out: dict = {"items": [detail for _, detail in found if detail]}
        missing = [one for one, detail in found if not detail]
        if missing:
            # Named rather than silently absent: asking for three and reasoning about two is
            # the failure this costs nothing to prevent.
            out["missing"] = missing
        return out
    out = paging.page(repo.tasks.list_tasks(path, args.get("status")), args)
    waiting = board_sync.waiting_here(path)
    if waiting:
        out["from_the_folder"] = waiting
    return out


@tool(
    "update_task",
    "Update a task — move it between planning/approved/working/done/dropped, or change its goal, "
    "description, priority, or which project it belongs to. Every task starts in "
    "'planning' while you draft its plan. 'approved' is NOT yours to set: your person approves a "
    "plan in chat, and this refuses it without a plan file and a checklist. Move it to 'working' "
    "when you start, 'done' when it is genuinely finished, 'dropped' if you are giving up. "
    "There is no column meaning 'your turn' — if you are blocked or want something confirmed, use "
    "`ask` in chat, which waits for the answer. A project is never closed for you, however many "
    "of its tasks are done — keep tasks honest and tell them when it looks finished. Once a task "
    "is 'done' or 'dropped', leave it alone. "
    "Moving a task with a written brief to 'done' REQUIRES `verification` — see below.",
    {
        "id": INT,
        "status": {**STR, "enum": list(TASK_STATUSES)},
        "goal": STR,
        "description": STR,
        "priority": {**STR, "enum": list(TASK_PRIORITIES)},
        "project_id": {**INT, "description": "Move it under this project (or omit)."},
        "verification": {
            "type": "array",
            "description": (
                "Required to set status='done' on a task that has a written brief. Re-read that "
                "brief and add ONE entry per separate thing it asked for — not a summary of what "
                "you did. Mark met=false for anything you didn't deliver; that hands the task "
                "back to your person instead of closing it, which is the honest outcome."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {**STR, "description": "One thing the brief asked for, in its words."},
                    "met": {"type": "boolean", "description": "Did you actually deliver this? Be honest."},
                    "evidence": {
                        **STR,
                        "description": (
                            "How you know — the file, the row count, the run you did. If not met, why not. "
                            "Restating the requirement is not evidence."
                        ),
                    },
                },
                "required": ["requirement", "met", "evidence"],
            },
        },
    },
    required=("id",),
)
def update_task(path: Path, args: dict):
    return _update_task(path, args)


@tool(
    "plan_work",
    "File several tasks on a project in a single call, each with its checklist. Use this INSTEAD "
    "of add_task followed by checklist for each one — it is the same writes, the same rules, and "
    "one round rather than a dozen. Plan shallowly: the next two to five tasks, not the whole "
    "project. Every task needs a done-condition you could check — a command that exits 0, a test "
    "that passes, a file that exists. Nothing here starts pickable; it lands in planning for your "
    "person to approve.",
    {
        "project_id": {**INT, "description": "The project this belongs to."},
        "tasks": {
            "type": "array",
            "description": "Two to five tasks — the next stretch of work, in the order you would do them.",
            "items": {
                "type": "object",
                "properties": {
                    "goal": {**STR, "description": "The outcome, not a verb."},
                    "description": {**STR, "description": "How you'll know it's done."},
                    "priority": {**STR, "description": "low | normal | high."},
                    "checklist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The concrete steps, in order.",
                    },
                },
                "required": ["goal", "description"],
            },
        },
    },
    required=("project_id", "tasks"),
)
def plan_work(path: Path, args: dict):
    """Several tasks and their checklists, in one call.

    **Why this exists, and it is not convenience.** Planning cost a dozen tool calls — a task
    each, then every checklist item — and a tool that costs a dozen calls
    competes, every round, with `shell`, which costs one and can do anything. Measured over two
    sessions: told to plan, it plans well; left alone it does the work inline and files nothing.
    That is the same economics that stopped it reaching for `delegate_subtask`, and it was fixed
    there the same way — by making the cheap thing and the right thing the same thing.

    **It writes nothing itself.** Every task goes through :func:`add_task` and every item through
    :func:`add_checklist_item`, so the done-condition rule, the duplicate merge and the planning
    gate all apply exactly as they do to a task filed by hand. A second
    write path would be a second place for those rules to be wrong, and the rules are the reason
    the board is worth anything.

    Partial results are returned rather than rolled back. Four tasks written and the fifth
    refused for a missing done-condition is four tasks of real work plus one thing to fix; a
    rollback would throw away the four and tell it to do all five again.
    """
    tasks = args.get("tasks")

    # Checked rather than trusted. `required=` is a hint to the model, not a gate — the argument
    # can arrive missing, as a string, or as null, and the type checker is right that an
    # unvalidated one reaches `int()` and raises a TypeError the caller cannot read.
    try:
        project_id = int(args["project_id"])
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "error": "Say which project this belongs to — `project_id` is its number."}

    if not isinstance(tasks, list) or not tasks:
        return {"ok": False, "error": "Say which tasks to file — at least one."}
    # Once for the batch rather than once per task: every one of them would be refused for the
    # same reason, and five copies of "that is another conversation's project" is noise.
    foreign = _out_of_scope(path, project_id)
    if foreign is not None:
        return foreign

    written: list[dict] = []
    refused: list[dict] = []
    for one in tasks:
        if not isinstance(one, dict):
            continue
        answer = add_task(
            path,
            {
                "goal": one.get("goal"),
                "description": one.get("description"),
                "priority": one.get("priority"),
                "project_id": project_id,
            },
        )
        if not answer.get("ok", True) or not answer.get("id"):
            refused.append({"goal": one.get("goal"), "why": answer.get("error")})
            continue
        task_id = answer["id"]
        steps = one.get("checklist")
        wanted = [str(step or "").strip() for step in (steps if isinstance(steps, list) else [])]
        if any(wanted):
            # One call now the tool is plural, rather than one per step.
            checklist(path, {"id": task_id, "add": [text for text in wanted if text]})
        written.append({"id": task_id, "goal": one.get("goal"), "steps": len(steps or [])})

    answer: dict = {"ok": bool(written), "project_id": project_id, "tasks": written}
    if refused:
        # Named individually. "3 of 5 filed" sends it back to re-read the board to work out
        # which two; the two it has to fix are the only part it needs.
        answer["refused"] = refused
        answer["note"] = f"{len(written)} filed, {len(refused)} refused — fix those and file them again."
    return answer


@tool(
    "checklist",
    "A task's checklist. Add steps and tick them off in one call: `add` is what you are "
    "writing, `items` the ids you are ticking. Filing a task now? plan_work takes its "
    "checklist inline.",
    {
        "id": {**INT, "description": "Task id. Needed to add."},
        "add": {"type": "array", "items": {"type": "string"}, "description": "Steps to append."},
        "items": {"type": "array", "items": {"type": "integer"}, "description": "Item ids to tick."},
        "done": {"type": "boolean", "description": "Sets `items`. Default true; false unticks."},
    },
    required=(),
)
def checklist(path: Path, args: dict):
    """One tool for two things that were never really different.

    `add_checklist_item` and `check_item` were 747 characters of schema for 328 calls that
    differed by a boolean, and both were resolutely singular: 165 adds and 163 ticks, one item
    at a time. The plural shape is the half that changes behaviour — see `domain.tooling.many`,
    where the same move is recorded as having worked when a persona paragraph asking for it
    did not.

    `done` stays a boolean applying to `items`, rather than separate `tick`/`untick` lists,
    because `aliases.Retired` can rename an argument but cannot route on its *value*: an
    archived `check_item(item_id=5, done=false)` has to keep working, and it only can if
    `done` means the same thing here as it did there.
    """
    added = [
        repo.tasks.add_checklist_item(path, int(args["id"]), text)
        for text in tooling.many(args, "add", "text")
        if str(text).strip()
    ]
    if added:
        # Noted for `_verify_approvable`. Half of what gets approved is the checklist, so
        # writing one is the moment the plan becomes something a person has not seen yet —
        # regardless of how old the task row is. Recorded here rather than inferred from a
        # timestamp because "this turn" is not a duration: a turn can run for an hour, and two
        # minutes ago is still inside it.
        session_context.turn_notes().setdefault(PLANNED_THIS_TURN, set()).add(int(args["id"]))

    state = args.get("done", True)
    ticked = [
        repo.tasks.set_checklist_item(path, int(item), state)
        for item in tooling.many(args, "items", "item_id")
        if str(item).strip()
    ]

    # One task to mirror, whichever half of the call found it. A tick carries its task on the
    # row it returns; an add was given it as an argument.
    touched = args.get("id") or next((t.get("task_id") for t in ticked if t), None)
    if touched:
        _mirror_brief(path, int(touched))
    # A single path returns exactly what the tool it replaced returned — the rule
    # `domain.tooling.many` states for every plural tool here, and the reason it matters is
    # that the interface picks which card to draw by sniffing the result's shape rather than
    # the tool's name. Wrapping one added step in a list would silently change how a step
    # someone just wrote is rendered.
    if added and ticked:
        return {"added": added, "checked": ticked}
    if added:
        return added[0] if len(added) == 1 else {"added": added}
    return ticked[0] if len(ticked) == 1 else {"checked": ticked}


@tool(
    "add_deliverable",
    "Attach a deliverable to a task — the actual output it produced, so your person "
    "can collect it. Use kind 'file' with a path in your sandbox for a document you "
    "wrote, 'link' for a URL, or 'text' for a written result. Do this when you finish "
    "real work.",
    {
        "id": {**INT, "description": "The task id."},
        "kind": {**STR, "enum": ["text", "file", "link"]},
        "title": STR,
        "content": {**STR, "description": "The text, a sandbox file path, or a URL."},
    },
    required=("id", "title", "content"),
)
def add_deliverable(path: Path, args: dict):
    saved = repo.tasks.add_deliverable(
        path, args["id"], args.get("kind") or "text", args["title"], args["content"]
    )
    _mirror_brief(path, args["id"])
    # Something finished and collectable is worth knowing about — it is the whole point of
    # having handed him the work. Notified separately from the task's own comments, which is
    # why this is here rather than left to whatever note he happens to write alongside it.
    detail = repo.tasks.task_detail(path, args["id"])
    goal = (detail or {}).get("goal") or f"task #{args['id']}"
    repo.messages.add_message(
        path,
        f"Finished something on “{goal}”: {args['title']}",
        link=f"/tasks/{args['id']}",
        kind="delivered",
    )
    return saved


@tool(
    "show_canvas",
    "Open a task's canvas on their screen: every HTML design attached to the task, side by "
    "side, where they can look, compare, and open one to edit. Use it the moment you have "
    "finished a design, and whenever they ask to see something you made — a paragraph "
    "describing four screens is not showing them four screens. Takes the task the designs are "
    "attached to.",
    {
        "id": {**INT, "description": "The task whose designs to show."},
        "note": {
            **STR,
            "description": "One line to read beside them — what you want their eye on. Optional.",
        },
    },
    required=("id",),
)
def show_canvas(path: Path, args: dict):
    """Put the canvas in front of them.

    Called `show_board` until the ```html fence stopped being a live page: "canvas" is the word
    he and his person both reach for, and it now names exactly one thing. Internally the
    surface is still the board (`showing.show("board", …)`, `boards/*.board.json`) — only the
    names he and they read changed.

    He could make designs and never show one. Asked to, he went looking for the word "board" —
    `find -iname "*board*"`, `git log | grep board`, two plugin reads — because the surface that
    draws them belongs to the interface and nothing told him it could be opened. Three commands
    to not find a thing already one click away on the screen in front of his person.

    Returns what is actually on the board rather than an acknowledgement, for the same reason
    `add_deliverable` returns the row: a tool that answers "ok" teaches him nothing about what
    happened, and the titles are what his next sentence needs to name.
    """
    task_id = int(args["id"])
    detail = repo.tasks.task_detail(path, task_id) or {}
    designs = [
        one
        for one in (detail.get("deliverables") or [])
        if one.get("kind") == "file" and str(one.get("content", "")).lower().endswith((".html", ".htm"))
    ]
    if not designs:
        # Said plainly rather than opening an empty board, which would look broken and be his
        # fault in a way he could not see.
        return {
            "shown": False,
            "why": f"task #{task_id} has no design files attached — attach them with add_deliverable first",
        }

    showing.show("board", task_id, str(args.get("note") or ""))
    return {
        "shown": True,
        "task": detail.get("goal") or f"task #{task_id}",
        # Read back by the reply's inline canvas, which resolves each design's assets and
        # system against the project without first fetching the task to learn where it lives.
        "project_id": detail.get("project_id"),
        "designs": [{"title": one.get("title"), "file": one.get("content")} for one in designs],
    }
