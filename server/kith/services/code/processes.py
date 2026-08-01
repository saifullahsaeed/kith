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

    def start(self, command: str, name: str, cwd: str | Path | None = None) -> dict[str, Any]:
        from kith.infra import workspace as sandbox
        from kith.services import permissions

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
            existing = self._running.get(wanted)
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

            log = _log_path(wanted)
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
                    env={**os.environ, "KITH_WORKSPACE": str(here)},
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
            self._running[wanted] = Background(
                name=wanted,
                command=command,
                cwd=str(here),
                process=process,
                log=log,
                started=time.time(),
                pgid=pgid,
            )

        # A command that is going to fail — a typo, a missing binary, a port in use — usually
        # does it immediately. Waiting a moment turns "started" into a truthful answer rather
        # than an optimistic one.
        time.sleep(0.4)
        return self.check(wanted)

    def check(self, name: str = "") -> dict[str, Any]:
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
                        for one in sorted(self._running.values(), key=lambda p: p.started)
                    ]
                }
            found = self._running.get(_clean_name(name))
            if found is None:
                known = ", ".join(sorted(self._running)) or "nothing"
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

    def stop(self, name: str) -> dict[str, Any]:
        with self._lock:
            found = self._running.get(_clean_name(name))
            if found is None:
                known = ", ".join(sorted(self._running)) or "nothing"
                raise ProcessError(f"no background process called `{name}` — running: {known}")
            self._running.pop(found.name, None)

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


def _clean_name(name: str) -> str:
    wanted = "".join(c if c.isalnum() or c in "-_." else "-" for c in str(name or "").strip())
    wanted = wanted.strip("-").lower()
    if not wanted:
        raise ProcessError("a background process needs a name — something like 'dev-server'")
    return wanted[:40]


def _log_path(name: str) -> Path:
    from kith.infra import workspace as sandbox

    folder = Path(sandbox.internal()) / "processes"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{name}.log"


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
