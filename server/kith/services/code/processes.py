"""Long-running things he started, and can still see.

The shell tool's advice was `nohup … &`, which returns straight away and hands back nothing.
After that he is blind: no output, no exit code, no way to tell a dev server that is serving
from one that died on a port collision, and no way to stop it. So "run the app and check it
works" was not actually available to him, and neither was "start the watcher, make the edit,
see what it says" — which is how anyone actually works on a front end.

**Output goes to a file, not a pipe.** A pipe has a buffer, and a process that fills it while
nobody is reading blocks forever — the classic way a subprocess deadlocks. A file cannot
block, survives nobody reading it for ten minutes, and can be read from an offset, which is
what makes "what has it said *since last time*" answerable at all. That question is the whole
point: a dev server prints a banner once and then a line per request, and re-reading the
banner every time is how a watcher fills a context.

**Named, not numbered.** A model handles `dev-server` far better than it handles `p3`, and
the name is also the thing that stops it starting four copies: starting a name that is
already running says so instead.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Most processes at once. Past a handful something has gone wrong — a loop starting servers,
#: or work abandoned without stopping anything. Real machines have finite ports and memory.
MAX_RUNNING = 6

#: Most output to return from one check. A build tool in a loop can produce megabytes; what is
#: wanted is the last thing it said, not all of it.
MAX_CHUNK = 4_000

#: How long to wait for a process to die politely before killing it.
STOP_GRACE = 3.0


class ProcessError(Exception):
    """The process could not be started, found, or stopped."""


@dataclass
class Background:
    name: str
    command: str
    cwd: str
    process: subprocess.Popen
    log: Path
    started: float
    #: The process group, captured at spawn rather than looked up when stopping.
    #:
    #: Looking it up later does not work, and the way it fails is silent. `poll()` reaps an
    #: exited child, after which its pid no longer exists and `os.getpgid` raises — so for
    #: exactly the case that matters (the shell exited, its children did not) the lookup
    #: returned nothing and the kill was skipped. `start_new_session=True` makes the child
    #: its own group leader, so this is its pid, and it stays valid while any member lives.
    pgid: int = 0
    #: How far into the log the last check read. This is what makes each check show only
    #: what is new.
    read_to: int = 0
    #: The conversation that started it, so finishing can be reported back there. Empty for one
    #: started outside a chat — a script, a test, setup for something else — which has nowhere to
    #: report to and is left alone.
    conversation_id: str = ""
    #: Whether its ending has already been told to that conversation. The watcher runs every thirty
    #: seconds and a finished process stays finished, so without this it would wake the chat for
    #: ever.
    _reported: bool = field(default=False, repr=False)
    _stopped: bool = field(default=False, repr=False)

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    @property
    def exit_code(self) -> int | None:
        return self.process.poll()

    def tail(self, limit: int = MAX_CHUNK) -> tuple[str, int]:
        """The end of the log, regardless of what has already been read.

        For a process that has *finished*, "nothing new since you last looked" is a true
        statement and a useless one — there is never going to be anything new, and the
        question being asked is why it stopped. `start` reads the first output itself, so
        without this a server that died on a port collision reported an empty string to the
        very next check.
        """
        try:
            size = self.log.stat().st_size
        except OSError:
            return "", 0
        start = max(0, size - limit)
        try:
            with self.log.open("rb") as handle:
                handle.seek(start)
                chunk = handle.read(limit)
        except OSError:
            return "", 0
        self.read_to = size
        return chunk.decode(errors="replace"), start

    def new_output(self, limit: int = MAX_CHUNK) -> tuple[str, int]:
        """Whatever it has printed since the last read, and how much was skipped."""
        try:
            size = self.log.stat().st_size
        except OSError:
            return "", 0
        if size <= self.read_to:
            return "", 0
        skipped = 0
        start = self.read_to
        if size - start > limit:
            # Show the newest, and say how much was dropped. The end of a log is where the
            # error is; the beginning is where the banner is.
            skipped = size - start - limit
            start = size - limit
        try:
            with self.log.open("rb") as handle:
                handle.seek(start)
                chunk = handle.read(limit)
        except OSError:
            return "", 0
        self.read_to = size
        return chunk.decode(errors="replace"), skipped


class Processes:
    """Everything started in the background, for the life of this process."""

    def __init__(self) -> None:
        self._running: dict[str, Background] = {}
        self._lock = threading.RLock()

    def _find(self, name: str, conversation_id: str | None = None) -> Background | None:
        """The process this conversation means by that name.

        Keyed by name alone until now, which made `run-tests` one global slot: the second
        conversation to run its suite was refused and pointed at a process belonging to a chat it
        cannot see. Two conversations are two pieces of work and may each have a `run-tests`.

        Falls back to a name match in any conversation when there is no session to narrow by — a
        script, a test, the desktop shell stopping things on the way out. Narrowing to nothing there
        would make those callers unable to reach anything.
        """
        wanted = _clean_name(name)
        chat = _current_conversation() if conversation_id is None else conversation_id
        if chat:
            mine = self._running.get(_key(chat, wanted))
            if mine is not None:
                return mine
        return next((one for one in self._running.values() if one.name == wanted), None)

    def mine(self, conversation_id: str | None = None) -> list[Background]:
        """Every process this conversation started, oldest first, or all of them outside one."""
        chat = _current_conversation() if conversation_id is None else conversation_id
        everything = sorted(self._running.values(), key=lambda p: p.started)
        if not chat:
            return everything
        return [one for one in everything if one.conversation_id == chat]

    def start(self, command: str, name: str, cwd: str | Path | None = None) -> dict[str, Any]:
        from kith.infra import permissions
        from kith.infra import workspace as sandbox

        wanted = _clean_name(name)
        command = str(command or "").strip()
        if not command:
            raise ProcessError("no command to run")

        # Same gate as `shell`: this runs the same kinds of thing, and running it in the
        # background is not a reason for it to be less supervised. If anything the reverse —
        # nobody sees the output at the moment it happens.
        permissions.require_command(command, sandbox.root())

        here = Path(str(cwd)) if cwd else sandbox.base_dir()

        with self._lock:
            self._reap()
            existing = self._running.get(_key(_current_conversation(), wanted))
            if existing is not None and existing.running:
                raise ProcessError(
                    f"`{wanted}` is already running (started {_ago(existing.started)}). "
                    "Check it with check_process, or stop it first."
                )
            if len(self._running) >= MAX_RUNNING:
                raise ProcessError(
                    f"{MAX_RUNNING} background processes are already running "
                    f"({', '.join(sorted(self._running))}). Stop one before starting another."
                )

            log = _log_path(wanted, _current_conversation())
            try:
                handle = log.open("wb")
            except OSError as exc:
                raise ProcessError(f"cannot open a log for {wanted}: {exc}") from None
            try:
                process = subprocess.Popen(
                    ["bash", "-lc", command],
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    cwd=str(here),
                    # Same non-interactive env `shell` gets, and for the sharper version of
                    # the same reason: `shell` blocking on a stray prompt at least times out
                    # and says so. A background process blocked on one looks identical to a
                    # healthy slow one — `alive: true`, nothing new to read — forever, with
                    # nothing to tell them apart.
                    env={**os.environ, "KITH_WORKSPACE": str(here), **sandbox._NON_INTERACTIVE},
                    # Its own process group, so stopping it takes the whole tree. A dev server
                    # started through `npm run dev` is a shell that spawns node: killing the
                    # shell alone leaves node holding the port, and the next start fails with
                    # something that looks nothing like the actual cause.
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                handle.close()
                raise ProcessError(f"could not start {wanted}: {exc}") from None
            finally:
                with contextlib.suppress(Exception):
                    handle.close()

            try:
                pgid = os.getpgid(process.pid)
            except OSError:
                pgid = process.pid  # start_new_session makes it its own leader anyway
            self._running[_key(_current_conversation(), wanted)] = Background(
                name=wanted,
                command=command,
                cwd=str(here),
                process=process,
                log=log,
                started=time.time(),
                pgid=pgid,
                conversation_id=_current_conversation(),
            )

        # A command that is going to fail — a typo, a missing binary, a port in use — usually
        # does it immediately. Waiting a moment turns "started" into a truthful answer rather
        # than an optimistic one.
        time.sleep(0.4)
        _changed(_current_conversation())
        return self.check(wanted)

    def check(self, name: str = "", conversation_id: str | None = None) -> dict[str, Any]:
        """What a process has said since last time, or a list of them all."""
        with self._lock:
            self._reap()
            if not name:
                return {
                    "running": [
                        {
                            "name": one.name,
                            "command": one.command,
                            "alive": one.running,
                            "for": _ago(one.started),
                        }
                        for one in self.mine(conversation_id)
                    ]
                }
            found = self._find(name, conversation_id)
            if found is None:
                known = ", ".join(one.name for one in self.mine(conversation_id)) or "nothing"
                raise ProcessError(f"no background process called `{name}` — running: {known}")

            alive = found.running
            output, skipped = found.new_output()
            replayed = False
            if not alive and not output:
                # Finished, and already read. Show the end of the log rather than nothing:
                # there is never going to be anything new, and the question is why it stopped.
                output, skipped = found.tail()
                replayed = True

            result: dict[str, Any] = {
                "name": found.name,
                "command": found.command,
                "alive": alive,
                "for": _ago(found.started),
                "output": output,
            }
            if skipped:
                result["skipped"] = f"{skipped} earlier characters not shown"
            if not alive:
                result["exitCode"] = found.exit_code
                ending = "finished" if found.exit_code == 0 else f"exited with code {found.exit_code}"
                result["note"] = f"it has {ending} — {'the end of' if replayed else 'this is'} its output"
            elif not output:
                result["note"] = "still running; nothing new since you last looked"
            return result

    def elapsed(self, name: str) -> float | None:
        """How long a named process has been running, in seconds — or None if nothing by
        that name exists. A raw number rather than `check`'s formatted `for` string, for a
        caller that wants to do arithmetic with it (`testing.py` scales how long its next
        wait is by this) rather than show it to someone."""
        with self._lock:
            found = self._find(name)
            return (time.time() - found.started) if found else None

    def full_output(self, name: str) -> str:
        """The complete log for a named process, start to finish.

        `check`'s `output` is deliberately partial — only what is new, or only the tail of
        what finished — because that is what watching a running thing wants. Parsing a
        finished one wants the opposite: a failure can be anywhere in the log, not only in
        its newest chunk, so nothing here is capped.
        """
        with self._lock:
            found = self._find(name)
            if found is None:
                known = ", ".join(sorted(self._running)) or "nothing"
                raise ProcessError(f"no background process called `{name}` — running: {known}")
            try:
                return found.log.read_text(errors="replace")
            except OSError:
                return ""

    def stop(self, name: str) -> dict[str, Any]:
        with self._lock:
            found = self._find(name)
            if found is None:
                known = ", ".join(one.name for one in self.mine()) or "nothing"
                raise ProcessError(f"no background process called `{name}` — running: {known}")
            self._running.pop(_key(found.conversation_id, found.name), None)

        already = not found.running
        # Terminated either way. "The direct child has exited" is not the same as "nothing is
        # left running" — a shell that backgrounds something and returns leaves the something
        # behind, still holding whatever it held, and the early return here meant `stop` did
        # nothing at all in exactly that case.
        _terminate(found.process, found.pgid)
        if already:
            tail, _ = found.new_output(limit=800)
            return {
                "stopped": found.name,
                "note": "it had already exited; anything it left running has been cleaned up",
                "exitCode": found.exit_code,
                **({"lastOutput": tail} if tail.strip() else {}),
            }
        tail, _ = found.new_output(limit=800)
        return {
            "stopped": found.name,
            "ranFor": _ago(found.started),
            **({"lastOutput": tail} if tail.strip() else {}),
        }

    def stop_all(self) -> None:
        """Used on shutdown. A dev server outliving the app that started it is a port nobody
        can explain still being held."""
        with self._lock:
            everything = list(self._running.values())
            self._running.clear()
        for one in everything:
            with contextlib.suppress(Exception):
                _terminate(one.process, one.pgid)

    def _reap(self) -> None:
        """Forget processes that finished a while ago.

        Not immediately: the whole point is being able to ask what happened, and a build that
        failed two seconds ago is precisely what he wants to read. Kept until something else
        needs the slot.
        """
        if len(self._running) < MAX_RUNNING:
            return
        for name, one in list(self._running.items()):
            if not one.running and time.time() - one.started > 60:
                self._running.pop(name, None)


def _group_is_empty(pgid: int) -> bool:
    """Is there nothing left in this process group?

    Signal 0 delivers nothing and only checks. `ProcessLookupError` is the answer we want;
    `PermissionError` means something is there but is not ours, which counts as still alive.
    """
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _terminate(process: subprocess.Popen, pgid: int) -> None:
    """Stop a process and everything it started.

    Waiting on the direct child is not enough, and this cost a leaked dev server to learn.
    `npm run dev` is bash → npm → node → vite; SIGTERM to the group killed bash, `wait`
    returned because bash was the process object we held, and the function reported success
    while node carried on holding port 8610. The survivor was found with `lsof` after the
    stop said it had stopped.

    So the group is what gets waited on, not the child, and SIGKILL follows if anything is
    still there. Only ever this group — a `pkill vite` would also have killed the unrelated
    dev server the person had running for their own project in another window, which very
    nearly happened while testing this.
    """
    if not pgid:
        return

    with contextlib.suppress(Exception):
        os.killpg(pgid, signal.SIGTERM)

    deadline = time.monotonic() + STOP_GRACE
    while time.monotonic() < deadline:
        if _group_is_empty(pgid):
            with contextlib.suppress(Exception):
                process.wait(timeout=0.1)
            return
        time.sleep(0.05)

    with contextlib.suppress(Exception):
        os.killpg(pgid, signal.SIGKILL)
    # Reap the direct child so it does not sit as a zombie for the life of the app.
    with contextlib.suppress(Exception):
        process.wait(timeout=1)


def _key(conversation_id: str, name: str) -> str:
    """The registry key: which conversation, and what it called the thing.

    A string rather than a tuple because it is also what the log filename is derived from, and one
    shape for both is one fewer thing to keep in step.
    """
    return f"{conversation_id}\x00{name}"


def _clean_name(name: str) -> str:
    wanted = "".join(c if c.isalnum() or c in "-_." else "-" for c in str(name or "").strip())
    wanted = wanted.strip("-").lower()
    if not wanted:
        raise ProcessError("a background process needs a name — something like 'dev-server'")
    return wanted[:40]


def _log_path(name: str, conversation_id: str = "") -> Path:
    """Where a process's output is kept.

    Scoped by conversation for the same reason the registry is: two chats may each have a
    `run-tests`, and one filename between them means each reads the other's output — the same leak
    as the shared registry slot, one layer down and harder to spot.

    A conversation id is already filename-safe (`20260812-144708094-4fdf58`). Unscoped keeps the bare
    name, so a process started outside a chat still lands where it always did.
    """
    from kith.infra import workspace as sandbox

    folder = Path(sandbox.internal()) / "processes"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{conversation_id}-{name}" if conversation_id else name
    return folder / f"{stem}.log"


def _ago(started: float) -> str:
    seconds = max(0, int(time.time() - started))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


#: One per process, like the language-server manager and for the same reason: two registries
#: would each think they owned the port.
processes = Processes()


def _changed(conversation_id: str = "") -> None:
    """Tell the interface a background task started or ended. Swallowed: a note about a process, not
    a reason to fail starting one."""
    try:
        from kith.kernel import changes

        changes.publish("process", conversation_id)
    except Exception:
        pass


def _current_conversation() -> str:
    """The chat this is being started from, or "" outside one.

    Read here rather than passed in: `start_process` is one of sixty tool handlers called as
    `run(path, args)`, and threading a conversation id through all of them to reach one is the
    shape this codebase already rejected for `session_context` generally.
    """
    try:
        from kith.kernel import session_context

        return session_context.current() or ""
    except Exception:
        return ""


def finished_since_last_look() -> list[str]:
    """Report every background process that has finished, to the chat that started it.

    Returns the conversations woken, in order. Called by the scheduler's timer — the same one that
    asks whether a reminder is due, because this is the same question in a different coat: something
    completed, and the conversation it belongs to should hear about it.

    Waking rather than waiting, deliberately. Holding the tool call open until the command returned
    was the other option and is worse for exactly the case this is for: a half-hour test suite would
    hold the conversation for half an hour, and going and doing something else is the whole point of
    putting it in the background.

    A stopped process is not reported. You already know how that ended — you stopped it.
    """
    from kith.services import scheduler

    by_chat: dict[str, list[str]] = {}
    for background in list(processes._running.values()):
        if background._reported or background._stopped or background.running:
            continue
        background._reported = True
        if not background.conversation_id:
            continue  # nowhere to report to
        code = background.exit_code
        tail, _ = background.tail()
        how = "finished" if code == 0 else f"failed with exit code {code}"
        note = f"The background task `{background.name}` ({background.command}) {how}."
        if tail.strip():
            note += f" Its last output:\n\n```\n{tail.strip()[-2000:]}\n```"
        by_chat.setdefault(background.conversation_id, []).append(note)

    if by_chat:
        # Before the turns, so the panel drops the finished task from its list at the same moment the
        # conversation starts talking about it.
        for conversation_id in by_chat:
            _changed(conversation_id)
    for conversation_id, notes in by_chat.items():
        scheduler._continue(conversation_id, notes)
    return list(by_chat)
