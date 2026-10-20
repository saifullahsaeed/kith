"""Agents: conversations started by other conversations, and the mail between them.

An agent is a whole chat session. It has the persona, the tools, the skills, the plugins and the
settings every chat has, its own transcript, its own turns, and everything else that is keyed by
conversation. What makes it an agent is one column — `parent_id`, the session that started it —
and one rule that follows from it: **it talks to its handler and to its own agents, and to nobody
else.** The person talks to the chat they opened, which is the *conductor* of whatever tree grows
under it.

Everything said between two sessions goes through :func:`post`, as a row in `agent_mail`:

- downward, a ``task`` (what an agent was started with) or a ``message`` (anything after);
- upward, a ``report`` (an agent's turn ended and nothing under it is still busy), a ``question``
  (`ask` inside an agent), or a ``note`` (`reach_out` inside an agent).

:func:`deliver` hands mail to its recipient: taken at the next round boundary of a turn already
running — the hook steering uses — or as the opening of a new turn when the recipient is idle.
Mail is persisted, so a report in flight when the process dies is delivered when it comes back
(:func:`recover`).

The turn machinery itself lives in the chat route; this module is handed the two things it needs
from there by :func:`install` — start a turn from a conversation's mail, stop a conversation's
turn — the way the scheduler is handed `continue_conversation`. A service may not import a route.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from kith.infra.db import repositories as repo
from kith.infra.workspace import worktrees
from kith.infra.workspace.base import WorkspaceError
from kith.infra.workspace.paths import base_dir
from kith.kernel import changes, live_turns, session_context
from kith.services import conversations, tuning
from kith.settings import AGENT_DB_PATH

#: The lifecycle an agent's row carries, which is what has to survive a restart.
WORKING = "working"  # a turn is running, or is about to, or was when the process died
WAITING = "waiting"  # its own turn is over and its agents are still busy
DONE = "done"  # it reported; idle, and can be given more work
STOPPED = "stopped"  # its handler stopped it; a message resumes it
CLOSED = "closed"  # cleaned up — worktree and branch removed; a record only

#: What a handler is called in its agents' transcripts when it is the person's own chat.
CONDUCTOR = "conductor"

#: How long `ask` inside an agent waits for its handler. Long, because the answer may have to go
#: all the way up the chain to the person and come back; bounded, so a thread parked on a handler
#: that was closed or forgotten is not parked forever.
ASK_WAIT_SECONDS = 6 * 60 * 60

#: What an agent is told when Kith comes back up and finds it was working.
RESTART_NOTE = (
    "Kith restarted while you were working, and your turn was cut off. Your conversation above "
    "is intact up to the last step that finished — anything after it did not happen. Check where "
    "things stand (your worktree, `git status`) and carry on from there."
)

#: Set by :func:`install`. Start a turn in a conversation from its waiting mail (True when one
#: started), and stop the turn running in a conversation.
_start_turn: Callable[[str], bool] | None = None
_stop_turn: Callable[[str], bool] | None = None


def install(start_turn: Callable[[str], bool], stop_turn: Callable[[str], bool]) -> None:
    """Hand this module the two things it needs from the turn machinery. Called at boot."""
    global _start_turn, _stop_turn
    _start_turn, _stop_turn = start_turn, stop_turn


# --------------------------------------------------------------------------- #
# Reading the tree
# --------------------------------------------------------------------------- #


def row(conversation_id: str) -> dict | None:
    return repo.conversations.get(AGENT_DB_PATH, conversation_id) if conversation_id else None


def is_agent(conversation_id: str) -> bool:
    found = row(conversation_id)
    return bool(found and found.get("parent_id"))


def label(found: dict | None) -> str:
    """What a session is called by the sessions next to it: its agent name, or the conductor."""
    if not found:
        return "Kith"
    return str(found.get("agent_name") or "") if found.get("parent_id") else CONDUCTOR


def children(conversation_id: str, *, closed: bool = False) -> list[dict]:
    """The agents one session started, oldest first. Closed ones only when asked for."""
    rows = repo.conversations.children(AGENT_DB_PATH, conversation_id)
    return rows if closed else [one for one in rows if one.get("agent_state") != CLOSED]


def subtree(conversation_id: str) -> list[dict]:
    """Every agent under a session, deepest first — the order things are stopped and closed in."""
    out: list[dict] = []
    for child in children(conversation_id):
        out.extend(subtree(str(child["id"])))
        out.append(child)
    return out


def chain(conversation_id: str) -> list[dict]:
    """The sessions from the conductor down to this one, inclusive."""
    out: list[dict] = []
    seen: set[str] = set()
    here = row(conversation_id)
    while here and str(here["id"]) not in seen:
        seen.add(str(here["id"]))
        out.append(here)
        here = row(str(here.get("parent_id") or ""))
    return list(reversed(out))


def root_of(conversation_id: str) -> str:
    found = chain(conversation_id)
    return str(found[0]["id"]) if found else conversation_id


def find_child(handler_id: str, name: str) -> dict | None:
    wanted = str(name or "").strip().casefold()
    return next((one for one in children(handler_id) if str(one.get("agent_name") or "").casefold() == wanted), None)


def asking(conversation_id: str) -> bool:
    with _asking_lock:
        asked = _asking.get(conversation_id)
    return bool(asked and not asked.event.is_set())


def busy(found: dict) -> bool:
    """Whether an agent still has something happening, or about to."""
    conversation_id = str(found["id"])
    return (
        live_turns.current(conversation_id) is not None
        or asking(conversation_id)
        or found.get("agent_state") in (WORKING, WAITING)
        or bool(repo.agent_mail.waiting(AGENT_DB_PATH, conversation_id))
    )


def _busy_below(conversation_id: str) -> bool:
    return any(busy(child) for child in children(conversation_id) if child.get("agent_state") != STOPPED)


def tree(conversation_id: str) -> list[dict]:
    """The agents under a session, nested, as the interface draws them. Closed ones included."""
    out = []
    for child in repo.conversations.children(AGENT_DB_PATH, conversation_id):
        child_id = str(child["id"])
        out.append(
            {
                "id": child_id,
                "name": child.get("agent_name") or "",
                "state": child.get("agent_state") or "",
                "working": live_turns.current(child_id) is not None,
                "asking": asking(child_id),
                "model": child.get("agent_model") or "",
                "branch": child.get("agent_branch") or "",
                "worktree": child.get("agent_worktree") or "",
                "folder": child.get("agent_folder") or "",
                "lastSaid": child.get("last_said") or "",
                "createdAt": child.get("created_at") or "",
                "updatedAt": child.get("updated_at") or "",
                "agents": tree(child_id),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Starting agents
# --------------------------------------------------------------------------- #

#: The longest agent name. A name is how its handler addresses it, not a description.
NAME_CHARS = 40


def start(handler_id: str, specs: list[dict]) -> dict:
    """Start agents under ``handler_id``. One entry per spec: what was started, or why not."""
    folder = Path(session_context.isolated_base() or base_dir())
    handler = row(handler_id) or {}
    project_id = repo.conversations.project_of(AGENT_DB_PATH, handler_id)
    started: list[dict] = []
    wants_own = any(str(spec.get("workspace") or "own") == "own" for spec in specs)
    left_out = worktrees.uncommitted(folder) if wants_own else []
    for spec in specs:
        name = " ".join(str(spec.get("name") or "").split())[:NAME_CHARS]
        task = str(spec.get("task") or "").strip()
        if not name or not task:
            started.append({"name": name, "error": "every agent needs a name and a task"})
            continue
        if find_child(handler_id, name):
            started.append(
                {"name": name, "error": "you already have an open agent with that name — message it, or pick another"}
            )
            continue
        try:
            started.append(_start_one(handler_id, handler, folder, project_id, name, task, spec))
        except WorkspaceError as exc:
            started.append({"name": name, "error": str(exc)})
    changes.publish("agents", root_of(handler_id))
    answer: dict = {"agents": started}
    if left_out:
        shown = ", ".join(left_out[:8]) + (f" and {len(left_out) - 8} more" if len(left_out) > 8 else "")
        answer["note"] = (
            f"Your checkout has uncommitted changes ({shown}). Agents with their own worktree "
            "started from your last commit and do not have them. If they need them, commit and "
            "tell them to `git merge` your branch."
        )
    return answer


def _start_one(
    handler_id: str, handler: dict, folder: Path, project_id: int | None, name: str, task: str, spec: dict
) -> dict:
    made = conversations.start(AGENT_DB_PATH, task)
    agent_id = str(made["id"])
    repo.conversations.rename(AGENT_DB_PATH, agent_id, name)
    if project_id:
        repo.conversations.set_project(AGENT_DB_PATH, agent_id, project_id)
    fields: dict = {
        "parent_id": handler_id,
        "agent_name": name,
        "agent_state": WORKING,
        "agent_model": str(spec.get("model") or "").strip(),
    }
    workspace = str(spec.get("workspace") or "own")
    opened = None
    if workspace == "own":
        try:
            opened = worktrees.open_branch(agent_id, folder, name)
        except WorkspaceError:
            fields["agent_state"] = CLOSED
            repo.conversations.set_agent(AGENT_DB_PATH, agent_id, **fields)
            raise
    sources = [line for line in str(handler.get("agent_source") or "").splitlines() if line.strip()]
    if opened is not None:
        fields.update(
            agent_worktree=str(opened.copy),
            agent_folder=str(opened.folder),
            agent_branch=opened.branch,
            agent_base=opened.base,
            agent_source="\n".join([*sources, str(opened.source)]),
        )
    else:
        # Sharing the handler's folder — by choice, or because there is no repository to branch.
        # An agent's handler that is itself in a worktree shares that worktree, pinned the same way.
        workspace = "shared"
        fields.update(
            agent_worktree=str(handler.get("agent_worktree") or ""),
            agent_folder=str(folder),
            agent_source="\n".join(sources),
        )
    repo.conversations.set_agent(AGENT_DB_PATH, agent_id, **fields)
    post(agent_id, handler_id, "task", task)
    return {
        "name": name,
        "id": agent_id,
        "workspace": workspace,
        "folder": fields["agent_folder"],
        **({"branch": opened.branch} if opened is not None else {}),
    }


@contextlib.contextmanager
def pinned(conversation_id: str) -> Iterator[None]:
    """Run a block in an agent's own folder: its worktree, when it has one. A no-op otherwise."""
    found = row(conversation_id)
    worktree = str((found or {}).get("agent_worktree") or "")
    if not found or not found.get("parent_id") or not worktree:
        yield
        return
    sources = [line for line in str(found.get("agent_source") or "").splitlines() if line.strip()]
    with session_context.working_from(worktree, mirror_of=sources, base=str(found.get("agent_folder") or "") or None):
        yield


def model_for(alias: str) -> str:
    """The model an agent runs on: "" for the install's own, else an id.

    `main` is the install's model, `strong` and `fast` are the two settings that name others, and
    anything else is taken as a model id. A setting left blank falls back to the main model.
    """
    text = str(alias or "").strip()
    if text in ("", "main"):
        return ""
    if text == "strong":
        return str(tuning.value("strong_model") or "").strip()
    if text == "fast":
        return str(tuning.value("scout_model") or "").strip()
    return text


# --------------------------------------------------------------------------- #
# Mail
# --------------------------------------------------------------------------- #

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(conversation_id: str) -> threading.Lock:
    """One per conversation: "is a turn running — if not, start one" has to be one step."""
    with _locks_guard:
        return _locks.setdefault(conversation_id, threading.Lock())


def post(to_id: str, from_id: str, kind: str, body: str) -> str:
    """Leave mail for a session and hand it over if it can be. Returns what delivery did."""
    repo.agent_mail.post(AGENT_DB_PATH, to_id, from_id, kind, body)
    changes.publish("agents", to_id)
    return deliver(to_id)


def deliver(conversation_id: str) -> str:
    """Hand a session its mail. Returns ``live``, ``started``, ``held``, ``nothing`` or ``gone``.

    ``live``: a turn is running, and takes the mail at its next round boundary (or, if it is
    ending, at :func:`turn_ended`). ``held``: a stopped or closed agent keeps its mail until it is
    resumed. Otherwise a turn starts with the mail as its opening.
    """
    found = row(conversation_id)
    if found is None:
        return "gone"
    if found.get("parent_id") and found.get("agent_state") in (STOPPED, CLOSED):
        return "held"
    with _lock(conversation_id):
        if live_turns.current(conversation_id) is not None:
            return "live"
        if not repo.agent_mail.waiting(AGENT_DB_PATH, conversation_id):
            return "nothing"
        if _start_turn is None:
            return "nothing"
        if found.get("parent_id") and found.get("agent_state") != WORKING:
            repo.conversations.set_agent(AGENT_DB_PATH, conversation_id, agent_state=WORKING)
        return "started" if _start_turn(conversation_id) else "nothing"


def take(conversation_id: str) -> list[dict]:
    """Deliver a session's waiting mail into its transcript, and return it as messages.

    Called by the turn — at the start of one opened by mail, and at each round boundary. From
    its handler, mail is recorded as ``user``: the handler is that agent's person. From its own
    agents (and from Kith), as ``system``: a wake, like a finished background task. Either way
    with a ``from`` naming the sender, so the interface can say who it was.
    """
    rows = repo.agent_mail.take(AGENT_DB_PATH, conversation_id)
    if not rows:
        return []
    out = []
    for mail in rows:
        role, text, sender = _render(conversation_id, mail)
        conversations.record(AGENT_DB_PATH, conversation_id, role, text, extra={"from": sender})
        out.append({"role": role, "content": text})
    changes.publish("agents", conversation_id)
    return out


_HEADINGS = {
    "task": "Your task, from {who}",
    "message": "From {who}",
    "report": "Report from your agent “{who}”",
    "question": "Question from your agent “{who}” — it is waiting for your answer. Reply with "
    "`message_agents`; answer it yourself if you can, and `ask` up the chain only if you cannot",
    "note": "Note from your agent “{who}”",
    "resume": "Kith",
}


def _render(conversation_id: str, mail: dict) -> tuple[str, str, dict]:
    from_id = str(mail.get("from_id") or "")
    sender = row(from_id) if from_id else None
    who = label(sender)
    kind = str(mail.get("kind") or "message")
    me = row(conversation_id) or {}
    from_handler = bool(from_id) and from_id == str(me.get("parent_id") or "")
    heading = _HEADINGS.get(kind, "From {who}").format(who=who)
    text = f"[{heading}]\n\n{str(mail.get('body') or '').strip()}"
    return ("user" if from_handler else "system"), text, {"id": from_id, "name": who, "kind": kind}


# --------------------------------------------------------------------------- #
# The end of a turn
# --------------------------------------------------------------------------- #

#: Agents whose turn was stopped by their handler, so the end of it is not a report.
_quiet: set[str] = set()


def turn_ended(conversation_id: str, *, stopped: bool = False, failed: str = "") -> None:
    """What happens when any turn ends: mail that arrived meanwhile, then — in an agent — the report.

    Called after the turn has released its readers, so a delivery here sees no turn running.
    """
    if deliver(conversation_id) in ("started", "live"):
        return
    found = row(conversation_id)
    if not found or not found.get("parent_id"):
        return
    state = found.get("agent_state")
    if state == CLOSED:
        return
    if conversation_id in _quiet or state == STOPPED:
        _quiet.discard(conversation_id)
        repo.conversations.set_agent(AGENT_DB_PATH, conversation_id, agent_state=STOPPED)
        changes.publish("agents", conversation_id)
        return
    if _busy_below(conversation_id):
        repo.conversations.set_agent(AGENT_DB_PATH, conversation_id, agent_state=WAITING)
        changes.publish("agents", conversation_id)
        return
    _report(found, stopped=stopped, failed=failed)


def _report(found: dict, *, stopped: bool, failed: str) -> None:
    conversation_id = str(found["id"])
    said = _last_said(conversation_id)
    notes: list[str] = []
    worktree, branch = str(found.get("agent_worktree") or ""), str(found.get("agent_branch") or "")
    if worktree and branch and Path(worktree).is_dir():
        left = worktrees.commit_leftovers(
            Path(worktree), f"{found.get('agent_name') or 'agent'}: work left uncommitted when it reported"
        )
        standing = worktrees.status(Path(worktree), str(found.get("agent_base") or ""))
        line = f"Branch `{branch}`: {standing['ahead']} commit(s) since it started"
        if left:
            line += f" (including {len(left)} file(s) it had left uncommitted, committed for it)"
        notes.append(line + f". Review with `git log -p HEAD..{branch}`, then `git merge {branch}`.")
    if failed:
        notes.append(f"Its turn ended on an error: {failed}")
    if stopped:
        notes.append("The person stopped its turn before it finished.")
    body = said or "(It ended its turn without saying anything.)"
    if notes:
        body += "\n\n---\n" + "\n".join(notes)
    # Posted before the state moves to done, and the order matters: a handler whose own turn is
    # ending right now checks "is anything below me busy" — it must see either this mail or a
    # busy agent, never a done agent with no report.
    post(str(found["parent_id"]), conversation_id, "report", body)
    repo.conversations.set_agent(AGENT_DB_PATH, conversation_id, agent_state=DONE)
    changes.publish("agents", conversation_id)


def _last_said(conversation_id: str) -> str:
    """The closing words of the agent's last turn — the round it ended on, not the whole turn."""
    for message in reversed(conversations.full_messages(conversation_id)):
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            return str(message.get("content") or "").strip()
        if message.get("role") == "user":
            break
    return ""


# --------------------------------------------------------------------------- #
# Asking up the chain
# --------------------------------------------------------------------------- #


@dataclass
class _Asked:
    event: threading.Event
    answer: str | None = None


_asking: dict[str, _Asked] = {}
_asking_lock = threading.Lock()


def ask_up(conversation_id: str, questions: list) -> dict:
    """`ask`, inside an agent: put the questions to its handler and wait for the answer."""
    found = row(conversation_id) or {}
    handler_id = str(found.get("parent_id") or "")
    handler = label(row(handler_id))
    text = _questions_text(questions)
    if not handler_id or not text:
        return {"ok": False, "error": "ask needs at least one question"}
    asked = _Asked(event=threading.Event())
    with _asking_lock:
        previous = _asking.get(conversation_id)
        _asking[conversation_id] = asked
    if previous is not None:
        previous.event.set()
    changes.publish("agents", conversation_id)
    try:
        post(handler_id, conversation_id, "question", text)
        if not asked.event.wait(timeout=ASK_WAIT_SECONDS):
            return {
                "ok": True,
                "answered": False,
                "note": f"No answer came from {handler}. Carry on with what you have, and say which way you went and why.",
            }
        if asked.answer is None:
            return {"ok": True, "answered": False, "note": "You were stopped before this was answered."}
        return {"ok": True, "answered": True, "from": handler, "answer": asked.answer}
    finally:
        with _asking_lock:
            if _asking.get(conversation_id) is asked:
                del _asking[conversation_id]
        changes.publish("agents", conversation_id)


def _questions_text(questions: list) -> str:
    lines = []
    for number, one in enumerate(questions or [], start=1):
        if isinstance(one, str):
            question, options = one, []
        else:
            question = str((one or {}).get("question") or "").strip()
            options = [
                str(option.get("label") if isinstance(option, dict) else option).strip()
                for option in (one or {}).get("options") or []
            ]
        if not question:
            continue
        lines.append(f"{number}. {question}")
        if options:
            lines.append("   Options: " + " | ".join(option for option in options if option))
    return "\n".join(lines)


def release(conversation_id: str) -> None:
    """Let go of an agent's open question unanswered — it is being stopped."""
    with _asking_lock:
        asked = _asking.get(conversation_id)
    if asked is not None:
        asked.answer = None
        asked.event.set()


def note_up(conversation_id: str, text: str) -> dict:
    """`reach_out`, inside an agent: tell its handler, without waiting for anything back."""
    found = row(conversation_id) or {}
    handler_id = str(found.get("parent_id") or "")
    if not handler_id or not str(text or "").strip():
        return {"ok": False, "error": "nothing to say"}
    post(handler_id, conversation_id, "note", str(text).strip())
    return {"ok": True, "to": label(row(handler_id))}


# --------------------------------------------------------------------------- #
# What a handler does to its agents
# --------------------------------------------------------------------------- #


def message(handler_id: str, name: str, text: str) -> dict:
    """Say something to one of your agents: answers its open question, or steers or re-tasks it."""
    child = find_child(handler_id, name)
    if child is None:
        return {"agent": name, "error": f"you have no open agent called {name!r}"}
    if not str(text or "").strip():
        return {"agent": name, "error": "nothing to say"}
    child_id = str(child["id"])
    with _asking_lock:
        asked = _asking.get(child_id)
    if asked is not None and not asked.event.is_set():
        asked.answer = str(text).strip()
        asked.event.set()
        return {"agent": name, "delivered": "answered the question it was waiting on"}
    if child.get("agent_state") == STOPPED:
        repo.conversations.set_agent(AGENT_DB_PATH, child_id, agent_state=WORKING)
    how = post(child_id, handler_id, "message", str(text).strip())
    return {
        "agent": name,
        "delivered": {
            "live": "it is working — it gets this at its next step",
            "started": "it was idle — this started it again",
        }.get(how, how),
    }


def _targets(handler_id: str, names: list[str]) -> tuple[list[dict], list[str]]:
    wanted = [str(name or "").strip() for name in names or [] if str(name or "").strip()]
    if not wanted or any(name.casefold() == "all" for name in wanted):
        return children(handler_id), []
    found, missing = [], []
    for name in wanted:
        child = find_child(handler_id, name)
        if child is None:
            missing.append(name)
        else:
            found.append(child)
    return found, missing


def stop(handler_id: str, names: list[str]) -> dict:
    """Stop agents' turns, and everything under them. They stay resumable with a message."""
    targets, missing = _targets(handler_id, names)
    stopped = []
    for target in targets:
        for one in [*subtree(str(target["id"])), target]:
            _halt(str(one["id"]))
        stopped.append(str(target.get("agent_name") or ""))
    changes.publish("agents", root_of(handler_id))
    return {"stopped": stopped, **({"unknown": missing} if missing else {})}


def _halt(conversation_id: str) -> None:
    found = row(conversation_id)
    if not found or found.get("agent_state") == CLOSED:
        return
    repo.conversations.set_agent(AGENT_DB_PATH, conversation_id, agent_state=STOPPED)
    release(conversation_id)
    if live_turns.current(conversation_id) is not None:
        _quiet.add(conversation_id)
        if _stop_turn is not None:
            _stop_turn(conversation_id)


def close(handler_id: str, names: list[str], *, force: bool = False) -> dict:
    """Clean up agents for good: stop them, remove their worktrees and branches, close the records.

    Refused — per agent named — while anything in its subtree is still busy, or when a branch in
    it has commits that are not in the closing session's checkout, unless forced. Merging is the
    caller's job; this only refuses to throw unmerged work away by default.
    """
    folder = Path(session_context.isolated_base() or base_dir())
    targets, missing = _targets(handler_id, names)
    closed, refused = [], []
    for target in targets:
        name = str(target.get("agent_name") or "")
        group = [*subtree(str(target["id"])), target]
        if not force:
            working = [label(one) for one in group if busy(one) and one.get("agent_state") != STOPPED]
            if working:
                refused.append({"agent": name, "why": f"still working: {', '.join(working)} — stop first, or force"})
                continue
            unmerged = [
                str(one.get("agent_branch"))
                for one in group
                if one.get("agent_branch") and not worktrees.merged(str(one.get("agent_branch")), folder)
            ]
            if unmerged:
                refused.append(
                    {
                        "agent": name,
                        "why": f"not merged into your checkout: {', '.join(unmerged)} — merge them, or force to discard",
                    }
                )
                continue
        for one in group:
            _close_one(one)
        closed.append(name)
    changes.publish("agents", root_of(handler_id))
    return {
        "closed": closed,
        **({"refused": refused} if refused else {}),
        **({"unknown": missing} if missing else {}),
    }


def _close_one(found: dict) -> None:
    conversation_id = str(found["id"])
    _halt(conversation_id)
    repo.conversations.set_agent(AGENT_DB_PATH, conversation_id, agent_state=CLOSED)
    branch = str(found.get("agent_branch") or "")
    worktree = str(found.get("agent_worktree") or "")
    # Only an agent with a branch owns its worktree. One that shared its handler's folder names the
    # handler's worktree here, and must never remove it.
    if branch and worktree:
        worktrees.close(Path(worktree), branch)
    repo.agent_mail.take(AGENT_DB_PATH, conversation_id)


# --------------------------------------------------------------------------- #
# Restart
# --------------------------------------------------------------------------- #


def recover() -> int:
    """After a restart: resume every agent that was working, and deliver everything undelivered.

    Returns how many sessions were handed something.
    """
    for found in repo.conversations.agents_in(AGENT_DB_PATH, (WORKING,)):
        conversation_id = str(found["id"])
        if live_turns.current(conversation_id) is None and not repo.agent_mail.waiting(AGENT_DB_PATH, conversation_id):
            repo.agent_mail.post(AGENT_DB_PATH, conversation_id, "", "resume", RESTART_NOTE)
    handed = 0
    for conversation_id in repo.agent_mail.addressees(AGENT_DB_PATH):
        if deliver(conversation_id) in ("started", "live"):
            handed += 1
    return handed


# --------------------------------------------------------------------------- #
# What a session is told
# --------------------------------------------------------------------------- #


def directive(conversation_id: str) -> str:
    """The framing an agent gets where a chat gets `CHAT_DIRECTIVE`; "" for a chat."""
    found = row(conversation_id)
    if not found or not found.get("parent_id"):
        return ""
    handler = label(row(str(found["parent_id"])))
    line = " → ".join(label(one) for one in chain(conversation_id)[:-1])
    me = str(found.get("agent_name") or "agent")
    where = str(found.get("agent_folder") or "")
    if found.get("agent_branch"):
        place = (
            f"You work in your own git worktree, {where}, on branch `{found['agent_branch']}`. "
            f"Commit your work there as you go; {handler} merges your branch. Nobody else writes in "
            "it, and you do not write in anyone else's checkout."
        )
    else:
        place = (
            f"You share {handler}'s folder, {where}. Others may be working in it too, so change "
            "only what your task is about."
        )
    return (
        f"You are working as an agent: “{me}”, started by {handler} (the chain is {line} → you). "
        f"You cannot talk to the person — {handler} is your person here. Its messages are your "
        f"instructions. `ask` sends your question to {handler} and waits for the answer. When your "
        f"turn ends, your last message goes to {handler} as your report — so end with what you did, "
        "what you checked and how, and what is still open. If you start agents of your own, they "
        "report to you, and your report goes up once they have.\n\n"
        f"{place}\n\n"
        "1. DO THE WORK. Read the files, run the commands, make the change, check it works.\n"
        f"2. ASK WHEN IT WOULD CHANGE WHAT YOU BUILD, and ask when you are BLOCKED — `ask` reaches "
        f"{handler}. A guess dressed as a decision costs the whole task.\n"
        "3. A CONDITION YOU STATE IS A CONDITION YOU CHECK. 'I still need to confirm Y' is your "
        "next action, not something to put in the report.\n"
        "4. The project board belongs to the conductor's conversation: do not create projects or "
        "tasks unless your task says to."
    )


def agents_block(conversation_id: str) -> str:
    """The `[Your agents]` block of the present state, or "" for a session with none open."""
    if not conversation_id:
        return ""
    kids = children(conversation_id)
    if not kids:
        return ""
    lines = ["[Your agents — `message_agents` to talk to them; their reports arrive on their own]"]
    for child in kids:
        child_id = str(child["id"])
        if asking(child_id):
            state = "waiting for your answer"
        elif live_turns.current(child_id) is not None:
            state = "working"
        else:
            state = {
                WORKING: "working",
                WAITING: "waiting on its own agents",
                DONE: "reported, idle",
                STOPPED: "stopped",
            }.get(str(child.get("agent_state") or ""), str(child.get("agent_state") or ""))
        line = f"- {child.get('agent_name')}: {state}"
        if child.get("agent_branch"):
            line += f", branch `{child['agent_branch']}`"
        under = len(subtree(child_id))
        if under:
            line += f", {under} agent(s) under it"
        said = " ".join(str(child.get("last_said") or "").split())
        if said:
            line += f" — last said: {said[:160]}"
        lines.append(line)
    return "\n".join(lines)
