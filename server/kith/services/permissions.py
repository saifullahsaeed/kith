"""What he may do on your computer without asking.

Until now the answer was "anything", because the answer did not matter: he lived in a
Docker container with no host mounts, so the wall was the container and there was nothing
of yours behind it. Running on the real machine removes that wall entirely — the same
``rm -rf`` that used to cost him a scratch directory now costs you a folder — so the wall
has to move into the code.

Three modes, and the shape of them is deliberate:

* **ask** — inside the workspace he is unrestricted. Outside it, or anything destructive
  anywhere, needs a yes from you. This is the default because it is the only setting where
  leaving him running unattended is a reasonable thing to do.
* **auto** — the same boundary, minus the prompt for ordinary work outside the workspace.
  The genuinely dangerous set still asks, because "auto" should mean "stop interrupting
  me", not "stop protecting me".
* **bypass** — no gate at all. Named honestly: it is what the container used to make safe
  and nothing makes safe now.

Approval does not block. A gated action fails with an explanation, records a pending
request, and he is told to ask — so he says so in his own words, you click Allow, and he
tries again. The alternative was holding a tool call open on a background thread waiting
for a click that may never come, on a tick that may be running at 4am.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

MODE_KEY = "permission_mode"
GRANTS_KEY = "permission_grants"

#: How many pending requests to keep. They are questions awaiting an answer, not a log;
#: past a handful the oldest are stale and the list becomes noise.
MAX_PENDING = 12


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
    at: float = field(default_factory=time.time)

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


def _store():
    from kith.config import CONFIG_DB_PATH
    from kith.infra.db import config_store

    return CONFIG_DB_PATH, config_store


def mode() -> Mode:
    path, store = _store()
    raw = str(store.load_settings(path).get(MODE_KEY) or Mode.ASK)
    try:
        return Mode(raw)
    except ValueError:
        return Mode.ASK


def set_mode(value: str) -> Mode:
    chosen = Mode(value)
    path, store = _store()
    store.update_settings(path, {MODE_KEY: str(chosen)})
    return chosen


def always_grants() -> set[str]:
    path, store = _store()
    raw = store.load_settings(path).get(GRANTS_KEY) or ""
    return {line for line in str(raw).splitlines() if line.strip()}


def _remember_always(signature: str) -> None:
    path, store = _store()
    store.update_settings(path, {GRANTS_KEY: "\n".join(sorted(always_grants() | {signature}))})


def granted(signature: str) -> bool:
    """Has this exact thing been allowed before?

    Path grants match by prefix so approving a folder approves what is in it — approving
    ``~/Downloads`` and then being asked again for every file in it is the kind of gate
    people turn off entirely.
    """
    if signature in _session_grants:
        return True
    for grant in _session_grants | always_grants():
        if grant.startswith("path:") and signature.startswith("path:"):
            if signature[5:].startswith(grant[5:]):
                return True
        elif grant == signature:
            return True
    return False


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #


def check_path(kind: Kind, target: Path, root: Path) -> Decision:
    """Is he allowed to touch this path?"""
    if mode() is Mode.BYPASS:
        return Decision(True)

    resolved = _resolve(target)
    inside = _inside(resolved, root)
    sensitive = _is_sensitive(resolved)

    if inside and not sensitive:
        return Decision(True)  # his own workspace — unrestricted, by design

    signature = f"path:{resolved}"
    if granted(signature):
        return Decision(True)

    # Auto stops prompting for ordinary work outside the workspace, but not for the
    # places that decide how your machine behaves.
    if mode() is Mode.AUTO and not sensitive and kind != "delete":
        return Decision(True)

    where = "somewhere sensitive" if sensitive else "outside his workspace"
    verb = {"read": "read", "write": "write to", "delete": "delete", "command": "use"}[kind]
    return _refuse(kind, str(resolved), f"he wants to {verb} something {where}", signature)


def check_command(command: str, root: Path) -> Decision:
    """Is he allowed to run this?"""
    if mode() is Mode.BYPASS:
        return Decision(True)

    for pattern, description in _DANGEROUS:
        if pattern.search(command):
            signature = f"cmd:{description}"
            if granted(signature):
                return Decision(True)
            return _refuse(
                "command", command.strip()[:200], f"that command involves {description}", signature
            )

    # Everything else runs, with the workspace as its working directory. Reaching outside
    # from inside a shell is not something a regex can see, which is exactly why the
    # dangerous list above is broad and why bypass mode is named the way it is.
    return Decision(True)


def _refuse(kind: Kind, what: str, why: str, signature: str) -> Decision:
    global _next_id
    _next_id += 1
    request = Request(id=f"p{_next_id}", kind=kind, what=what, why=why)
    _pending[request.id] = request
    while len(_pending) > MAX_PENDING:
        _pending.pop(next(iter(_pending)))
    return Decision(
        False,
        reason=(
            f"Not allowed yet: {why}. Tell your person what you want to do and why, and ask them "
            f"to allow it — there is an Allow button on this message. Don't try to work around it."
        ),
        request=request,
    )


def require_path(kind: Kind, target: Path, root: Path) -> None:
    decision = check_path(kind, target, root)
    if not decision.allowed:
        raise Denied(decision)


def require_command(command: str, root: Path) -> None:
    decision = check_command(command, root)
    if not decision.allowed:
        raise Denied(decision)


# --------------------------------------------------------------------------- #
# Answering
# --------------------------------------------------------------------------- #


def pending() -> list[dict]:
    return [request.public() for request in _pending.values()]


def approve(request_id: str, scope: str = "session") -> dict:
    """Allow a pending request. ``once``/``session`` last until restart; ``always`` persists."""
    request = _pending.pop(request_id, None)
    if request is None:
        raise KeyError(request_id)
    signature = _signature(request)
    if scope == "always":
        _remember_always(signature)
    _session_grants.add(signature)
    return request.public()


def deny(request_id: str) -> dict:
    request = _pending.pop(request_id, None)
    if request is None:
        raise KeyError(request_id)
    return request.public()


def revoke_all() -> None:
    path, store = _store()
    store.update_settings(path, {GRANTS_KEY: ""})
    _session_grants.clear()


def _signature(request: Request) -> str:
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


def snapshot() -> dict:
    """Everything the interface needs to show the current stance."""
    return {
        "mode": str(mode()),
        "modes": [str(one) for one in Mode],
        "pending": pending(),
        "grants": sorted(always_grants()),
        "sessionGrants": sorted(_session_grants),
    }
