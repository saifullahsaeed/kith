"""What he may do on your computer without asking.

Until now the answer was "anything", because the answer did not matter: he lived in a
Docker container with no host mounts, so the wall was the container and there was nothing
of yours behind it. Running on the real machine removes that wall entirely — the same
``rm -rf`` that used to cost him a scratch directory now costs you a folder — so the wall
has to move into the code.

Three modes, and the shape of them is deliberate:

* **ask** — inside the workspace he is unrestricted. Outside it, or anything destructive
  anywhere, needs a yes from you. This is the default because it is the only setting where
  walking away from a running turn is a reasonable thing to do.
* **auto** — the same boundary, minus the prompt for ordinary work outside the workspace.
  The genuinely dangerous set still asks, because "auto" should mean "stop interrupting
  me", not "stop protecting me".
* **bypass** — no gate at all. Named honestly: it is what the container used to make safe
  and nothing makes safe now.

Approval blocks. A gated action records a pending request and waits on your answer: allow it
and the call proceeds, refuse it and the call fails as it always did.

It did not, for a long time, and the reasoning was that holding a tool call open means waiting
for a click that may never come, on a turn you may have walked away from. Both halves of that
stopped being true — a turn runs on its own thread and survives the window closing, and it can
be rejoined from wherever you come back to, so the prompt is reachable rather than lost.

What the old shape cost was that the popup and the turn had nothing to do with one another. The
call had already failed and the turn had moved past it by the time you saw the question, so
clicking Allow granted the permission for next time and the thing you allowed never happened.
That reads as the button not working.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Literal

from kith import settings
from kith.kernel import changes, live_turns, session_context

#: How each request was answered, by id. Separate from `Request` because that is frozen —
#: it describes what he wanted, which does not change, and the verdict is a different fact.
#:
#: A verdict only has to survive from `approve`/`deny` until the parked call reads it, and
#: `_wait_for` drops its own on the way past. What is left are the ones nobody came back for —
#: a waiter that had already timed out, or a request answered with no call parked on it — and
#: nothing removed those, so this grew for the life of the process. `_remember_verdict` bounds
#: it. Evicting a verdict is safe in the one direction that matters: a reader that finds
#: nothing treats it as a refusal.
_answered: dict[str, bool] = {}

MODE_KEY = "permission_mode"
GRANTS_KEY = "permission_grants"

#: How many pending requests to keep. They are questions awaiting an answer, not a log;
#: past a handful the oldest are stale and the list becomes noise.
MAX_PENDING = 12

#: How many verdicts to hold. Generous against `MAX_PENDING` because a verdict may be written
#: for a request that has already been evicted from `_pending`, and the reader deserves to find
#: it; small enough that it cannot grow without bound.
_MAX_ANSWERED = MAX_PENDING * 8


class Mode(StrEnum):
    ASK = "ask"
    AUTO = "auto"
    BYPASS = "bypass"


Kind = Literal["read", "write", "delete", "command"]


#: Commands that can cost you something you cannot get back, wherever they run. Matched on
#: the command text because that is all we have — he writes shell, not structured actions.
#: Deliberately broad: a false prompt costs one click, a false pass costs a disk.
_DANGEROUS = (
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf]", re.I), "a recursive or forced delete"),
    (re.compile(r"\bsudo\b|\bdoas\b", re.I), "running as administrator"),
    (re.compile(r"\b(mkfs|fdisk|diskutil|newfs)\b", re.I), "formatting a disk"),
    (re.compile(r"\bdd\b[^|]*\bof=", re.I), "writing raw blocks to a device"),
    (re.compile(r"\b(shutdown|reboot|halt)\b", re.I), "shutting the machine down"),
    (re.compile(r"\b(launchctl|systemctl|defaults\s+write)\b", re.I), "changing system settings"),
    (re.compile(r"\bcurl\b[^|]*\|\s*(ba)?sh|\bwget\b[^|]*\|\s*(ba)?sh", re.I), "piping the web into a shell"),
    (re.compile(r"\bchmod\s+(-R\s+)?777\b", re.I), "making files world-writable"),
    (re.compile(r"\bgit\s+push\b", re.I), "pushing to a remote"),
    (re.compile(r"\bkillall\b|\bpkill\b", re.I), "killing other programs"),
    (re.compile(r":\(\)\s*\{.*\}\s*;", re.I), "a fork bomb"),
    (re.compile(r"\b(security|keychain)\b.*\b(find|dump)", re.I), "reading your keychain"),
)

# --------------------------------------------------------------------------- #
# What a shell command is about to touch
# --------------------------------------------------------------------------- #
#
# This exists because of a real one. Asked to delete a file from the Desktop, he wrote
# ``rm -- "$HOME/Desktop/thing.zip"`` and it ran: in Ask mode, on a file outside his
# folder, with no prompt, unrecoverably. Three separate things had to be wrong for that,
# and the third was the one that mattered — the dangerous list above is the *only* gate on
# a shell command, so anything it does not match runs with no reference to where it points.
# ``rm`` slipped it because the pattern insists on ``-r`` or ``-f``, and a plain ``rm`` of
# one named file is exactly as unrecoverable as a forced one.
#
# So commands that destroy things are now read for the paths they name, and any path
# outside the workspace goes through the same check a tool call would get — same prompt,
# same grants, same auto-mode rules. Deliberately limited to destructive commands: a
# gate that fires on every ``/usr/bin/env`` in a script is a gate people turn off, and
# then nothing is protected. Reads through the shell stay ungated, which is a real
# remaining gap and named as one rather than papered over.
#
# It reads literals, so ``$dir`` computed at runtime, ``$(...)``, and a path assembled in
# a variable are all invisible to it. That is a floor, not a ceiling: the shapes it does
# catch — an absolute path, ``~/…``, ``$HOME/…`` — are the shapes he actually writes.

#: Commands whose whole point is to destroy or overwrite something.
_DELETING = re.compile(r"\b(rm|rmdir|unlink|shred|srm)\b", re.I)
_WRITING = re.compile(
    # `>` and `>>` as redirection, but not `2>&1`: requiring a non-`&` after the arrow keeps
    # the most common shell idiom in the world from reading as a write to a file.
    r"\b(mv|cp|tee|touch|mkdir|install|ln|chmod|chown|chgrp|truncate|dd)\b"
    r"|\bsed\s+-i\b|>>?\s*(?![&\s])",
    re.I,
)

#: Path-shaped literals: absolute, or anchored at home. The left edge has to be the start
#: of the string or a separator, or every relative ``a/b`` would contribute a phantom
#: ``/b``.
_PATH_LITERAL = re.compile(
    r"(?:^|(?<=[\s'\"=:(]))((?:~|\$HOME|\$\{HOME\})?/[^\s'\"`;|&<>()]*)",
)

#: Paths a destructive command may name without asking. Scratch space and the null device
#: are not "your files" in any sense worth a prompt, and prompting for them is how the
#: whole mechanism gets resented.
_UNREMARKABLE_PREFIXES = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders", "/dev")

#: Where the programs live. Ignored for *writes* only, and for one specific reason: the
#: check reads every path in a command, so ``python3 /usr/bin/thing.py > out.txt`` would
#: otherwise prompt about ``/usr/bin/thing.py`` — a path being read, in a command whose
#: write goes to a relative file. Interpreter and binary paths appear in almost every real
#: command, so that false prompt would be the common case, and a gate that cries wolf on
#: ordinary work is one people switch off. Deletes still count these: ``rm /usr/local/bin/x``
#: is worth a question no matter how it is spelled.
_PROGRAM_PREFIXES = (
    "/usr",
    "/bin",
    "/sbin",
    "/opt",
    "/System",
    "/Library/Frameworks",
    "/Applications",
)


def _skills_root() -> str:
    """Where installed skills live, or "" if that cannot be determined.

    Their bundled scripts are things a command *runs*, exactly like an interpreter, and they
    sit outside the workspace by design — a skill is a capability, not work product. Found by
    running it: asked for a spreadsheet, he read the xlsx skill, followed it, and the command
    was refused because the skill's own ``scripts/recalc.py`` counted as an out-of-folder
    write. The skill told him to run it and the person installed it deliberately; prompting
    there is the gate crying wolf about the feature working correctly.

    Defensive, and no longer lazy: this used to call `services.skills.root()` through a
    function-body import, which was a gate at the bottom of the tree reaching to the top to
    ask where a folder is. `settings.skills_dir()` answers the same question one rank below
    everything. The `try` stays — a permission check must not fail because a lookup did.
    """
    try:
        return str(settings.skills_dir())
    except Exception:
        return ""


def paths_named(command: str, home: Path | None = None) -> list[Path]:
    """Every path literal in a command, resolved as far as text allows."""
    base = home or Path.home()
    found: list[Path] = []
    for raw in _PATH_LITERAL.findall(command):
        text = raw.rstrip("/") or "/"
        for prefix in ("${HOME}", "$HOME", "~"):
            if text.startswith(prefix):
                text = str(base) + text[len(prefix) :]
                break
        if not text.startswith("/"):
            continue
        candidate = Path(os.path.normpath(text))
        # A bare "/" is never a target anyone meant, and it turns up constantly: Python's
        # pathlib spells joins as `Path.home() / "Kith"`, and a heredoc full of that gives a
        # space-slash-space that reads as the filesystem root. It refused a command whose
        # actual paths were all relative and inside his folder. The genuinely alarming ways to
        # name the root — `rm -rf /`, `chmod 777 /` — are matched by the dangerous list
        # instead, which is where a blast radius that size belongs.
        if candidate == Path("/"):
            continue
        if candidate not in found:
            found.append(candidate)
    return found


def command_intent(command: str) -> Kind | None:
    """``"delete"``, ``"write"``, or None when nothing in it destroys anything."""
    if _DELETING.search(command):
        return "delete"
    if _WRITING.search(command):
        return "write"
    return None


#: Directories worth naming in a prompt even in auto mode: a write here is not "outside the
#: workspace" in the boring sense, it is somewhere that changes how your machine behaves.
_SENSITIVE_DIRS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".config/gh",
    "Library/Keychains",
    "Library/LaunchAgents",
    "Library/LaunchDaemons",
)


@dataclass(frozen=True)
class Request:
    """One thing he wanted to do and could not."""

    id: str
    kind: Kind
    what: str
    why: str
    #: What approving this grants, decided by the check that raised it.
    #:
    #: Carried rather than re-derived. `approve` used to rebuild it from `why` by splitting on
    #: the word "involves", which only works while `why` is the sentence the check wrote — and
    #: `_refuse` replaces `why` with a caller's `purpose` whenever one is given. A dangerous
    #: command approved through a caller that supplies one was stored under the purpose text
    #: and never matched the check's signature again, so "always allow" silently did nothing.
    signature: str = ""
    at: float = field(default_factory=time.time)
    #: Set by `approve` or `deny`. The tool call that raised this request is parked on it.
    #: Only ever `.set()`, never reassigned — this dataclass is frozen, and the verdict itself
    #: lives in `_answered` for that reason.
    settled: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "what": self.what, "why": self.why, "at": self.at}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    #: Set when refused: what to tell him, in words he can repeat to you.
    reason: str = ""
    request: Request | None = None


class Denied(RuntimeError):
    """A refusal he can act on: it names the thing and says to ask.

    A RuntimeError rather than a special return value, so no call site can forget to
    check — every path into the machine goes through one of a dozen functions, and the
    one that quietly skipped the check would be the one that mattered.
    """

    def __init__(self, decision: Decision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


# --------------------------------------------------------------------------- #
# State. Grants persist; pending requests do not — an unanswered question from
# a previous run is not a question any more.
# --------------------------------------------------------------------------- #

_pending: dict[str, Request] = {}
_session_grants: set[str] = set()
_next_id = 0

#: One lock over every mutable thing in this module.
#:
#: There was none, and this is a gate: `require_path` and `require_command` run on tool
#: threads — several at once, since a round's network-bound calls go out through a pool — while
#: `approve`, `deny` and `revoke` run on Flask request threads. Five pieces of shared state were
#: being read-modify-written from both with nothing between them. `_next_id += 1` is the
#: clearest: two refusals racing there get the same `p<n>`, so the second overwrites the first
#: in `_pending` and the first turn waits out its full fifteen-minute deadline for an answer
#: that can no longer reach it.
#:
#: **Re-entrant, because the reads nest.** `granted()` holds it and calls `always_grants()`,
#: which reads the settings store; `check_path` holds it and asks `_inside_linked_project`. A
#: plain `Lock` would deadlock on the first of those.
#:
#: **Never held across a wait.** `_wait_for` blocks on `request.settled` for up to fifteen
#: minutes, and the thread that would set that event is the one calling `approve`. Holding the
#: lock across the wait would deadlock the gate against the only thing that can open it. Same
#: for `_tell_them`, which writes a message and posts a desktop notification: slow, and it
#: cannot be allowed to serialise every other permission check behind it.
_state = threading.RLock()


def _remember_verdict(request_id: str, allowed: bool) -> None:
    """Record how a request was answered. Caller holds `_state`."""
    _answered[request_id] = allowed
    while len(_answered) > _MAX_ANSWERED:
        _answered.pop(next(iter(_answered)))


def _store():
    """The database, and deliberately not `settings.json`.

    Every other adjustable value moved to a file, and this one did not follow on purpose.
    The whole case for that file is that it can be edited by hand when the app will not
    start — and that **Kith can edit it himself**, with the file tools he already has.

    Which is exactly why the gate must not live in it. The permission mode and the
    always-approved list are the things standing between him and the rest of the machine,
    and a safety control the guarded party can rewrite is not a control. He has no
    database tool and no reason for one; that asymmetry is the point rather than an
    accident of where this was first written.

    So: if you are here to tidy up the last two settings that are not in the file, this is
    the note saying don't.
    """
    from kith.infra.db import config_store
    from kith.settings import CONFIG_DB_PATH

    return CONFIG_DB_PATH, config_store


#: The two settings this module reads, cached. Same argument as `_linked` below, and measured
#: rather than assumed: `config_store.load_settings` costs ~286µs, against ~7µs for the path
#: resolve every check already does — 40x — and `check_path` runs on every file operation and
#: once per path in every destructive command. `linked_project_roots`, which is cached, is only
#: ~2.9x a settings read, so the uncached pair was the larger cost of the two.
#:
#: **Invalidated on every write rather than left to the TTL**, because one of these two values is
#: the master switch. Switching bypass → ask has to bite now, not in five seconds; the TTL is a
#: backstop for a database edited from outside this process, which is not a supported path.
_settings_cache: tuple[float, dict] | None = None
_SETTINGS_TTL = 5.0


def forget_settings() -> None:
    """Drop the settings cache. Called by every writer here, and by tests."""
    global _settings_cache
    with _state:
        _settings_cache = None


def _settings() -> dict:
    global _settings_cache
    now = time.monotonic()
    with _state:
        if _settings_cache is not None and now - _settings_cache[0] < _SETTINGS_TTL:
            return _settings_cache[1]
    path, store = _store()
    loaded = dict(store.load_settings(path))
    # Read outside the lock, stored under it — same shape as `linked_project_roots`: two threads
    # arriving together do the read twice and agree, where holding the lock across the query
    # would serialise every file operation behind it.
    with _state:
        _settings_cache = (now, loaded)
        return loaded


def mode() -> Mode:
    raw = str(_settings().get(MODE_KEY) or Mode.ASK)
    try:
        return Mode(raw)
    except ValueError:
        return Mode.ASK


def set_mode(value: str) -> Mode:
    chosen = Mode(value)
    path, store = _store()
    store.update_settings(path, {MODE_KEY: str(chosen)})
    forget_settings()
    # The header shows this, and a second window showing the old mode is a second window that
    # will surprise someone.
    changes.publish("permission")
    return chosen


def always_grants() -> set[str]:
    raw = _settings().get(GRANTS_KEY) or ""
    return {line for line in str(raw).splitlines() if line.strip()}


def _remember_always(signature: str) -> None:
    path, store = _store()
    store.update_settings(path, {GRANTS_KEY: "\n".join(sorted(always_grants() | {signature}))})
    forget_settings()


def granted(signature: str) -> bool:
    """Has this exact thing been allowed before?

    A path grant covers what is *inside* it, so approving a folder approves its contents —
    approving ``~/Downloads`` and then being asked again for every file in it is the kind of
    gate people turn off entirely.

    Containment is asked of the path, not of the string. `startswith` on the text says yes to
    every sibling that merely begins the same way: a grant on `~/Documents` covered
    `~/Documents-private`, and a grant on `notes.txt` covered `notes.txt.bak`. Neither is
    inside anything that was approved.
    """
    with _state:
        if signature in _session_grants:
            return True
        allowed = _session_grants | always_grants()
    for grant in allowed:
        if grant == signature:
            return True
        if grant.startswith("path:") and signature.startswith("path:") and _covers(grant[5:], signature[5:]):
            return True
    return False


def _under(target: Path, roots: tuple[str, ...]) -> bool:
    """Is `target` one of these directories, or inside one?

    The same question `_covers` asks, for the fixed prefix lists. Also by path rather than by
    text: `str(target).startswith(("/tmp", "/usr"))` skipped `/tmpfoo` and `/usrdata` too, and
    for these lists a false match means the path check is not run at all.
    """
    for root in roots:
        if not root:
            continue
        try:
            if target.is_relative_to(Path(root)):
                return True
        except (OSError, ValueError):
            continue
    return False


def _covers(grant: str, wanted: str) -> bool:
    """Is `wanted` the granted path, or inside it?

    Both are already resolved absolute paths — `check_path` builds every signature from
    `_resolve`. Never raises: a grant that cannot be read as a path grants nothing, which is
    the safe answer and keeps a malformed row from taking the gate down.
    """
    if not grant:
        # An empty grant would otherwise be a prefix of everything.
        return False
    try:
        return Path(wanted).is_relative_to(Path(grant))
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #


def check_path(kind: Kind, target: Path, root: Path, purpose: str = "") -> Decision:
    """Is he allowed to touch this path?"""
    if mode() is Mode.BYPASS:
        return Decision(True)

    resolved = _resolve(target)
    inside = _inside(resolved, root)
    sensitive = _is_sensitive(resolved)

    if inside and not sensitive:
        return Decision(True)  # his own workspace — unrestricted, by design

    # A folder you linked to a project is his too, for as long as that project is active.
    #
    # This is what makes linking mean anything. Without it, pointing him at
    # ~/Documents/my-app would prompt on every single file he writes there — a hundred clicks
    # to do the thing you just asked for, which is not a safety feature, it is a reason to
    # switch the gate off entirely and lose the protection everywhere.
    #
    # And it is *narrower* than what auto mode already does, not wider: auto allows any
    # non-sensitive write anywhere outside the workspace, with no record of why. This grants
    # one named folder, because you named it, and takes the grant back when the project
    # closes.
    if not sensitive and _inside_linked_project(resolved):
        return Decision(True)

    signature = f"path:{resolved}"
    if granted(signature):
        return Decision(True)

    # Auto stops prompting for ordinary work outside the workspace, but not for the
    # places that decide how your machine behaves.
    if mode() is Mode.AUTO and not sensitive and kind != "delete":
        return Decision(True)

    # A skill is not ordinary work product, and "outside his workspace" is a true but
    # useless thing to say about one. He has a tool for authoring skills and a skill that
    # tells him how; the write it ends in landed here as an anonymous out-of-folder path,
    # so in ask-mode the prompt asked about a file in a directory nobody recognises. The
    # gate itself is right to stay — a skill steers him, so installing one is a bigger
    # decision than saving a report, and it should be a decision rather than a default —
    # but it has to say what it is, or you are approving a path instead of a capability.
    skills_dir = _skills_root()
    if skills_dir and _under(resolved, (skills_dir,)) and kind in {"write", "delete"}:
        name = _skill_named(resolved, skills_dir)
        doing = "change" if kind == "write" else "remove"
        return _refuse(
            kind,
            str(resolved),
            f"he wants to {doing} {name} — one of his own skills, which is how he decides "
            "what he knows how to do",
            signature,
        )

    where = "somewhere sensitive" if sensitive else "outside his workspace"
    verb = {"read": "read", "write": "write to", "delete": "delete", "command": "use"}[kind]
    return _refuse(kind, str(resolved), f"he wants to {verb} something {where}", signature, purpose)


def _skill_named(target: Path, skills_dir: str) -> str:
    """ "the `x` skill" for a path inside a skill, or a general phrase when it is the root."""
    rest = str(target)[len(skills_dir) :].strip("/")
    first = rest.split("/", 1)[0] if rest else ""
    return f"the `{first}` skill" if first else "his skills folder"


def check_command(command: str, root: Path, purpose: str = "") -> Decision:
    """Is he allowed to run this?"""
    if mode() is Mode.BYPASS:
        return Decision(True)

    for pattern, description in _DANGEROUS:
        if pattern.search(command):
            signature = f"cmd:{description}"
            if granted(signature):
                return Decision(True)
            return _refuse(
                "command",
                command.strip()[:200],
                f"that command involves {description}",
                signature,
                purpose,
            )

    # A command that destroys something gets read for what it points at, and anything
    # outside his folder goes through the ordinary path check. See the note above
    # _DELETING for why this is limited to destructive commands and what it cannot see.
    intent = command_intent(command)
    if intent is not None:
        skip = _UNREMARKABLE_PREFIXES
        if intent == "write":
            skip = skip + _PROGRAM_PREFIXES
            # Deletes deliberately do not get this: `rm` inside a skill's folder is someone
            # uninstalling a capability sideways, and that is worth a question.
            installed_skills = _skills_root()
            if installed_skills:
                skip = (*skip, installed_skills)
        for target in paths_named(command):
            if _under(target, skip):
                continue
            decision = check_path(intent, target, root, purpose)
            if not decision.allowed:
                return decision

    # Everything else runs, with the workspace as its working directory.
    return Decision(True)


def _refuse(kind: Kind, what: str, why: str, signature: str, purpose: str = "") -> Decision:
    """Park a refusal and describe it.

    `purpose` replaces the derived `why` when a caller knows something the check cannot
    work out for itself. The check reads a command and reports what it *does* — "he wants
    to write to something outside his workspace" — which is accurate and, in front of a
    person deciding, close to useless: what they see is a path they have to interpret.
    A caller that already knows it is fetching a language server can say so, and then the
    dialog reads like a question rather than a hex dump.

    Only ever supplied in code, never from anything a model composed. It is shown to a
    person about to grant something, which makes it exactly the wrong place to let a
    caller write its own justification.
    """
    global _next_id
    with _state:
        _next_id += 1
        request = Request(id=f"p{_next_id}", kind=kind, what=what, why=purpose or why, signature=signature)
        _pending[request.id] = request
        while len(_pending) > MAX_PENDING:
            evicted = _pending.pop(next(iter(_pending)))
            # The verdict goes with the question. `_answered` is keyed by request id and had
            # nothing removing anything from it, so it grew for the life of the process.
            _answered.pop(evicted.id, None)
    # Something is waiting on a person. Published outside the lock, and before the turn parks
    # itself in `_wait_for` — the interface had no way to learn this except by asking every 2.5
    # seconds whether a request had appeared.
    changes.publish("permission")
    return Decision(
        False,
        reason=(
            # The same words the dialog shows. He is asked to tell his person what he wants and
            # why, so handing him a different sentence than the one on their screen is how the
            # two of them end up describing different things to each other.
            f"Not allowed yet: {purpose or why}. Tell your person what you want to do and why, and "
            f"ask them to allow it — there is an Allow button on this message. Don't try to work "
            f"around it."
        ),
        request=request,
    )


#: How long a refused action waits to be allowed before giving up. The same figure a question
#: waits, and for the same reason: long enough to walk away from and come back to, short enough
#: that a forgotten window does not hold a thread until the process dies.
_DEADLINE_SECONDS = 15 * 60


def _wait_for(decision: Decision) -> None:
    """Hold the tool call until the request is answered, then let it through or refuse it.

    This used to refuse immediately and say "ask them to allow it", and the reasoning for that
    is written at the top of this module: holding a tool call open means waiting for a click
    that may never come, on a turn you may have walked away from. Both halves stopped being
    true — a turn runs on its own thread and survives the window closing, and it can now be
    rejoined from wherever you come back to, so the prompt is reachable again rather than lost.

    What it fixes is that the popup and the turn had nothing to do with each other. You clicked
    Allow on a request the turn had already given up on and moved past, so the permission was
    granted for next time and the thing you allowed did not happen.

    Denial and the deadline both still raise, because the caller's contract is unchanged: this
    either returns because the action may proceed, or it raises `Denied`.
    """
    request = decision.request
    if request is None:
        raise Denied(decision)

    # Only when somebody is there to answer.
    #
    # "A click that may never come" is still exactly right when nothing is watching — a
    # reminder firing at four in the morning, a scheduled continuation, a test. Those refuse
    # immediately as they always did, because waiting would park a thread on a prompt drawn on
    # nobody's screen. The whole suite hung on this before the guard existed, which is the same
    # failure a background job would have hit in the small hours.

    # Asked as two questions, because it was one and the one was a proxy.
    #
    # `live_turns.current` used to carry the whole weight, on the reasoning that a turn nobody
    # requested has no live turn either. That held only because the scheduler ran its turns
    # outside the machinery entirely — and it no longer does: `begin_turn` is now the only way
    # a turn starts, so a reminder firing at four in the morning has a live turn exactly like a
    # typed message does. Left alone, this line would have read "somebody is watching" off a
    # turn drawn on nobody's screen and parked the scheduler's timer thread on it for fifteen
    # minutes — the precise failure the guard was added to prevent, reintroduced by fixing
    # something else. The proxy did not break; what it stood for moved.
    #
    # So the honest question first. `nobody_watching()` is opened by the scheduler and by
    # nothing else, and it rides into the turn on the copied context, which makes it the one
    # thing here that actually knows whether a person is present.
    if session_context.unattended():
        # Refused at once, which is the same answer as before. What changes is that it is now
        # said honestly and said out loud.
        #
        # The reason handed back named an Allow button on a message drawn on nobody's screen,
        # and then told him not to work around it — so the one instruction he could actually
        # follow was to stop, with no way to report what he had stopped for. And nothing was
        # written down at all: `_tell_them` sits below this, deliberately, because a refusal
        # nobody is waiting on is not an interruption worth making. True of the interrupting;
        # false of the recording. You came back in the morning to a turn that had quietly not
        # done something and no trace of what.
        #
        # The shape is `ask`'s, which is the mechanism this codebase already chose for "someone
        # else's turn" — see `domain/enums`, where `waiting` and `review` were deleted in favour
        # of it. `ask` answers an unanswered question with "carry on with what you have, and say
        # which way you went and why". A refusal is the same situation and now gets the same
        # sentence, so the two gates stop disagreeing about what an absent person means.
        _tell_them(decision.request, unattended=True)
        raise Denied(
            replace(
                decision,
                reason=(
                    f"Not allowed, and nobody was there to ask: {decision.request.why if decision.request else ''}. "
                    f"Nothing is waiting on you — carry on without it and say plainly what you "
                    f"skipped and why. It is on their alerts; they can allow it when they next look."
                ),
            )
        )

    # And still the live turn, for everything that has a conversation but no turn at all: a
    # checkpoint taken by a test, a tool called from a script. `live_turns.current` is "a turn
    # is running and can be watched", which is the condition under which the card appears.
    if not live_turns.current(session_context.current()):
        raise Denied(decision)

    # Announced only now, and only once. A refusal nobody is waiting on is not an interruption
    # worth making, and firing one from every unattended refusal put a database write and a
    # desktop notification on paths that had neither before.
    #
    # "Only once" is load-bearing and was not true for two days: the commit that moved this below
    # the guard added it here and left the original above, so the move was an add. An attended
    # refusal raised two alerts for one thing to approve — which reads as two things to approve —
    # and an unattended one still raised the alert the guard exists to suppress.
    _tell_them(request)

    # Deliberately outside the lock: this blocks for up to fifteen minutes and the thread that
    # ends the wait is the one in `approve`, which needs the lock to do it.
    if not request.settled.wait(timeout=_DEADLINE_SECONDS):
        raise Denied(decision)
    with _state:
        # Read and dropped together: this is the only reader of its own verdict, and leaving it
        # behind is what made `_answered` grow for the life of the process.
        answered = _answered.pop(request.id, None)
    if not answered:
        raise Denied(decision)


def _tell_them(request: Request | None, unattended: bool = False) -> None:
    """Raise the badge and the desktop notification for something he cannot do yet.

    `unattended` changes the sentence, not whether one is written. There is nothing to approve
    in the moment — the turn already refused and moved on — so this is a record of something he
    wanted and could not have, phrased as that rather than as a request waiting on you.

    The prompt sits in the conversation, so without this it is only visible if you happen to be
    looking at that chat — and now that the gate waits rather than failing forward, not seeing
    it means a turn parked for fifteen minutes rather than a request quietly queued.

    Linked to the conversation when there is one. A refusal outside a conversation is not
    announced at all: nothing is waiting on it, because unattended work refuses at once.

    Best-effort. A notification that cannot be delivered must never take down the call that
    produced it.
    """
    try:
        from kith.infra.db import repositories as repo
        from kith.settings import AGENT_DB_PATH

        conversation_id = session_context.current()
        if not conversation_id or request is None:
            return
        said = (
            f"While you were away I wanted to {request.kind} {request.what}, and couldn't. "
            f"I carried on without it."
            if unattended
            else f"I need your say-so before I can {request.kind} {request.what}."
        )
        repo.messages.add_message(
            AGENT_DB_PATH,
            said,
            link=f"/chat/{conversation_id}",
            kind="asked",
        )
    except Exception:
        # The refusal stands either way. This is the note telling them a decision is waiting,
        # and a failed write must not turn "I need your say-so" into an unhandled exception on
        # the thread that was already refusing something.
        pass


def require_path(kind: Kind, target: Path, root: Path) -> None:
    decision = check_path(kind, target, root)
    if not decision.allowed:
        _wait_for(decision)


def require_command(command: str, root: Path, purpose: str = "") -> None:
    decision = check_command(command, root, purpose)
    if not decision.allowed:
        _wait_for(decision)


def release_waiting() -> None:
    """Stop waiting on every open request, without allowing any. Called when a turn is stopped —
    a turn parked on a permission is not reading the stop switch."""
    with _state:
        waiting = list(_pending.values())
        for request in waiting:
            _remember_verdict(request.id, False)
    # Woken outside the lock: each `set()` releases a thread that will immediately want it.
    for request in waiting:
        request.settled.set()
    if waiting:
        changes.publish("permission")


# --------------------------------------------------------------------------- #
# Answering
# --------------------------------------------------------------------------- #


def pending() -> list[dict]:
    with _state:
        return [request.public() for request in _pending.values()]


def approve(request_id: str, scope: str = "session") -> dict:
    """Allow a pending request. ``once``/``session`` last until restart; ``always`` persists.

    Taking the request out of `_pending` and recording the verdict happen together, so two
    approvals of the same id cannot both succeed — the second finds nothing and raises, which
    is what the interface already expects.
    """
    with _state:
        request = _pending.pop(request_id, None)
        if request is None:
            raise KeyError(request_id)
        signature = _signature(request)
        if scope == "always":
            _remember_always(signature)
        _session_grants.add(signature)
        _remember_verdict(request.id, True)
    # Outside: waking the waiter hands it a thread that wants this lock immediately.
    request.settled.set()
    # And the card goes. Hearing when one opens but not when it closes leaves a dialog on screen
    # for something that has already been allowed.
    changes.publish("permission")
    return request.public()


def deny(request_id: str) -> dict:
    with _state:
        request = _pending.pop(request_id, None)
        if request is None:
            raise KeyError(request_id)
        _remember_verdict(request.id, False)
    request.settled.set()
    changes.publish("permission")
    return request.public()


def revoke_all() -> None:
    path, store = _store()
    store.update_settings(path, {GRANTS_KEY: ""})
    forget_settings()
    with _state:
        _session_grants.clear()
    changes.publish("permission")


def revoke(signature: str) -> bool:
    """Take back one standing permission. False if it was not there.

    All-or-nothing was the only option, and that is not how anyone actually feels about
    these: you want to keep "he may read my Documents" and drop the one folder you
    approved in a hurry last week. Forcing the choice between all of them and none of
    them means people keep the ones they would rather not.
    """
    wanted = str(signature or "").strip()
    if not wanted:
        return False
    remaining = always_grants()
    found = wanted in remaining
    if found:
        remaining.discard(wanted)
        path, store = _store()
        store.update_settings(path, {GRANTS_KEY: "\n".join(sorted(remaining))})
        forget_settings()
    # A grant can be standing, session-only, or both; dropping it should mean dropping it.
    with _state:
        if wanted in _session_grants:
            _session_grants.discard(wanted)
            found = True
    if found:
        changes.publish("permission")
    return found


def _signature(request: Request) -> str:
    """What approving this request grants.

    The check that raised it already worked this out — see `Request.signature`. The fallback is
    for a request built without one, which only a test does.
    """
    if request.signature:
        return request.signature
    return (
        f"path:{request.what}" if request.kind != "command" else f"cmd:{request.why.split('involves ')[-1]}"
    )


# --------------------------------------------------------------------------- #


def _resolve(target: Path) -> Path:
    """Settle ``..`` and symlinks before comparing, never after."""
    try:
        return Path(target).expanduser().resolve()
    except OSError:
        return Path(target).expanduser()


def _inside(resolved: Path, root: Path) -> bool:
    try:
        return resolved.is_relative_to(_resolve(root))
    except ValueError:
        return False


def _is_sensitive(resolved: Path) -> bool:
    home = Path.home().resolve()
    return any(_inside(resolved, home / name) for name in _SENSITIVE_DIRS)


def _session_snapshot() -> list[str]:
    """The session grants, copied under the lock so the caller cannot iterate a live set."""
    with _state:
        return sorted(_session_grants)


def snapshot() -> dict:
    """Everything the interface needs to show the current stance."""
    return {
        "mode": str(mode()),
        "modes": [str(one) for one in Mode],
        "pending": pending(),
        "grants": sorted(always_grants()),
        "sessionGrants": _session_snapshot(),
    }


#: Linked project folders, cached. `check_path` runs on every file operation and every path
#: in every destructive command, so a database query per call would put SQLite in the hot
#: path of ordinary work. Linking happens by hand, a few times a day at most, so a short
#: window of staleness costs nothing — and `forget_linked_projects` closes it the moment
#: something actually changes.
_linked: tuple[float, tuple[Path, ...]] | None = None
_LINKED_TTL = 5.0


def forget_linked_projects() -> None:
    """Drop the cache. Called when a project's folder or status changes."""
    global _linked
    with _state:
        _linked = None


def linked_project_roots() -> tuple[Path, ...]:
    """The folders he may treat as his own because a project points at them.

    Public because permission is not the only question a caller has about these folders.
    `paths.display` needs them too: a path inside one has somewhere to be relative *to*, and
    the absence of that knowledge in the formatting layer is what made `glob` fail 61% of the
    time outside his own folder while the permission layer was happily letting it run.
    """
    global _linked
    now = time.monotonic()
    with _state:
        if _linked is not None and now - _linked[0] < _LINKED_TTL:
            return _linked[1]
    roots: list[Path] = []
    try:
        from kith.infra.db import repositories as repo
        from kith.settings import AGENT_DB_PATH

        for directory in repo.projects.linked_directories(AGENT_DB_PATH):
            try:
                roots.append(_resolve(Path(directory)))
            except OSError:
                continue
    except Exception:
        # Imported by nearly everything and consulted on every write: a permission check
        # must not fail because a lookup did. No linked folders is the safe answer.
        roots = []
    # Filled under the lock, but the lookup above is not: it opens the database, and holding a
    # gate's lock across that would serialise every file operation behind one query. Two threads
    # arriving together do the read twice and agree on the answer, which costs a query and
    # cannot be wrong — where a lock held across the I/O could stall a whole round.
    with _state:
        _linked = (now, tuple(roots))
        return _linked[1]


def _inside_linked_project(resolved: Path) -> bool:
    return any(_inside(resolved, root) for root in linked_project_roots())
