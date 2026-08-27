"""His history: whether the workspace is a repo, and what has happened in it.

Split from the checkpoints beside it because the two answer different questions. This one is
about the record he keeps deliberately — a commit is a claim that something is a coherent
change — and the other is about the net underneath him.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from kith.infra import executables, permissions

from .base import ExecResult, WorkspaceError, _clip
from .paths import base_dir, resolve, root

#
# None of his four projects had any. He would say "I redesigned the UI" and there was no way
# for him to check what he had actually changed, and no way for anyone else to review a night
# of edits nobody watched land. For an agent that rewrites files a page at a time, that is the gap worth
# closing before any of the others.
#
# One repository at the workspace root rather than one per project. His projects are database
# rows, not directories — he makes folders as he goes — so per-project would need a mapping
# that does not exist, and would miss everything he writes outside one. A single repo covers
# all of it and `git diff` still works per directory.

#: Never versioned: build output, dependencies, and his own bookkeeping. Without this the
#: first commit is 60MB of node_modules and every diff afterwards is unreadable.
#: Written only into *his own* folder, never into a project — see `ensure_repo`. A project has
#: its own opinion about what to ignore and did not ask for ours.
#:
#: `.kith/scratch/` and not `.kith/`. The whole folder used to be ignored, which was right when
#: it held nothing but his private bookkeeping and is wrong now that it holds the project's
#: memory and its task briefs: those are the things that are *supposed* to travel with the
#: repository so somebody else's Kith can pick the work up from a clone. Only the scratch
#: subfolder is excluded, because it is screenshots and PNGs do not belong in history.
_GITIGNORE = """\
node_modules/
dist/
build/
.venv/
venv/
__pycache__/
*.pyc
.DS_Store
.kith/scratch/
"""

#: Committed as, so a commit works on a machine where git has no global identity. Without
#: these git refuses with "please tell me who you are" and the history silently never starts.
_GIT_AUTHOR = ("Kith", "kith@localhost")


def _git(
    *args: str,
    check: bool = False,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> ExecResult:
    """One git command where the work is happening, with an identity of its own.

    Deliberately not through :func:`run_command`: that asks the permission layer, and these
    are the app's own bookkeeping rather than something he decided to run. The identity is
    passed per-invocation so nothing depends on, or alters, the machine's git config.

    ``base_dir()`` and not ``root()``, and the difference cost five hours of unsaved work.
    Every git tool ran in *his own folder* whatever project he was on — so working in a
    codebase on the Desktop, `changes` reported an empty diff, `commit` committed nothing and
    `history` showed nothing. He used `commit` once in five hours and `history` never, which
    was the correct response to three tools that did nothing. The repository ended with no
    commits at all against 52 changed files.

    The same mistake as `base_dir` itself, one layer along: that fix taught *paths* to follow
    the project and nobody checked git.

    ``cwd`` defaults to ``base_dir()`` for every existing caller, but a checkpoint restore
    has to target the repo a checkpoint was actually *taken* in, which may not be
    ``base_dir()`` any more by the time someone restores it — so it takes an explicit
    override rather than trusting the ambient session.

    ``env`` is for the checkpoint machinery's shadow index (``GIT_INDEX_FILE``); it must be
    built as ``{**os.environ, ...}`` by the caller, never a bare dict, or git won't resolve
    on ``PATH``. ``None`` (the default) means "inherit the real environment," identical to
    every call site before this parameter existed.
    """
    here = cwd or base_dir()
    proc = subprocess.run(
        [
            # Resolved rather than named. `/usr/bin/git` happens to be on the minimal `PATH` a
            # GUI-launched app inherits, so this one was never actually broken — but relying on
            # that is relying on Apple shipping a shim, and someone with a newer git in
            # Homebrew should get theirs rather than the one that came with Xcode.
            executables.which("git") or "git",
            "-c",
            f"user.name={_GIT_AUTHOR[0]}",
            "-c",
            f"user.email={_GIT_AUTHOR[1]}",
            *args,
        ],
        capture_output=True,
        cwd=str(here),
        timeout=timeout,
        env=env,
    )
    out = proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")
    if check and proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {out.strip()[:300]}")
    return ExecResult(exit_code=proc.returncode, output=out)


def has_git() -> bool:
    return executables.which("git") is not None


def _repo_root(here: Path) -> Path | None:
    """The repository this folder is inside, if any — walking up, not just at `here`.

    Checking only `here/.git` is what would make `ensure_repo` initialise a *second*
    repository inside an existing checkout the moment he worked in a subfolder. Nothing would
    fail; the commits would simply go somewhere nobody looks.
    """
    for folder in [here, *here.parents]:
        if (folder / ".git").exists():
            return folder
    return None


def ensure_repo() -> bool:
    """Make where he is working a repository if it is not one. True when history is available.

    Walks up before initialising. A project usually *is* already a repository, often with its
    root a level or two above the folder in hand — and `git init` inside an existing checkout
    creates a nested repository that swallows the work silently, which is a far worse outcome
    than not having history.
    """
    if not has_git():
        return False
    here = base_dir()
    if not _repo_root(here):
        if _git("init", "-q").exit_code != 0:
            return False
    # Only ever in his own folder. Dropping an opinionated `.gitignore` into somebody's Django
    # project because it happened not to have one is not tidying up, it is deciding something
    # that was not ours to decide — and it would be the first thing they saw in the diff.
    if here == root():
        ignore = here / ".gitignore"
        if not ignore.exists():
            ignore.write_text(_GITIGNORE)
    return True


def commit_all(message: str) -> str:
    """Commit whatever changed, and say what. Empty string when there was nothing.

    Called by him, deliberately, and never on a timer. The first version committed at the end
    of every turn, which was wrong twice over: a turn is not a unit of work — a task takes many,
    so most of those commits would be mid-edit states that do not build — and a commit is a
    claim that something is a coherent step, which is a judgement, not something a clock can
    make. A history committed on a schedule is a keystroke log, and the point of having one is
    to be able to read it.

    Best-effort about *failing*: not being able to record history must never take down the work
    that was just done, so this returns a description rather than raising.
    """
    if not ensure_repo():
        return ""
    _git("add", "-A")
    staged = _git("diff", "--cached", "--stat")
    if not staged.output.strip():
        return ""
    subject = " ".join(str(message or "work").split())[:72] or "work"
    result = _git("commit", "-q", "-m", subject)
    if result.exit_code != 0:
        return ""
    return staged.output.strip().splitlines()[-1].strip()


def diff(path: str | None = None, staged: bool = False) -> str:
    """What has changed and is not yet committed."""
    if not ensure_repo():
        return "git is not available on this machine, so there is no history to compare against."
    args = ["diff", "--cached"] if staged else ["diff"]
    if path:
        target = Path(resolve(path))
        permissions.require_path("read", target, root())
        args += ["--", str(target)]
    out = _git(*args).output.strip()
    if not out:
        # An untracked file shows in neither diff, and "no changes" would be a lie.
        fresh = _git("ls-files", "--others", "--exclude-standard").output.strip()
        if fresh:
            return "Nothing changed in tracked files. Not yet tracked:\n" + _clip(fresh)
        return "Nothing has changed since the last commit."
    return _clip(out)


def log(limit: int = 20) -> str:
    """Recent history, one line each."""
    if not ensure_repo():
        return "git is not available on this machine."
    out = _git("log", f"-{max(1, min(limit, 200))}", "--format=%h %ad %s", "--date=format:%d %b %H:%M").output
    return _clip(out.strip()) or "No history yet."


def _nowhere_to_send(verb: str) -> str:
    """Why this folder cannot exchange anything with anybody, or "" if it can.

    Both `push` and `pull` opened by asking `has_git()`, and `has_git()` answers whether the git
    *binary* is installed — so the guard whose message says "there is no repository here" fired
    only on a machine with no git at all. A plain folder fell straight through it to the remote
    check and was told **"this repository has no remote"**, which is a sentence about a
    repository that does not exist. Somebody reading that goes looking for `git remote add`
    when what they need is `git init`.

    The two are different questions and both are worth answering separately, because the answers
    lead to different actions.
    """
    if not has_git():
        return f"git is not installed on this machine, so there is nothing to {verb} with."
    if _repo_root(base_dir()) is None:
        return f"This folder is not a git repository, so there is nothing to {verb}."
    if not _git("remote").output.strip():
        return (
            "This repository has no remote, so the work stays on this machine."
            if verb == "push"
            else "This repository has no remote, so there is nowhere to pull from."
        )
    return ""


def push() -> str:
    """Send committed work to wherever this repository came from. A sentence about what happened.

    Committing is what makes work durable on this machine; pushing is what makes it exist for
    anybody else, which is the entire mechanism `.kith/` relies on. It has been going through a
    raw `git push` in a shell command until now — 15 such commits in one project here — which
    means every way it can fail has been arriving as shell output for a model to interpret:
    no remote, no upstream, nothing ahead, rejected because somebody else pushed first.

    That last one is not an error, it is the normal condition of two people working, and it has
    a different answer from the rest: pull, look at what came back, and push again. Said in words
    rather than left as `! [rejected] main -> main (fetch first)`, because the difference between
    "this failed" and "somebody else got there first" decides what happens next.

    Never raises, for the same reason `commit_all` does not: failing to publish must not take
    down the work that was published.
    """
    nowhere = _nowhere_to_send("push")
    if nowhere:
        return nowhere

    branch = _git("rev-parse", "--abbrev-ref", "HEAD").output.strip() or "HEAD"
    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.exit_code != 0:
        # First push of a branch. `-u` so the next one needs no argument, which is also what
        # makes "nothing to push" answerable afterwards.
        done = _git("push", "-u", "origin", branch)
        return (
            f"Pushed {branch} and set it to track origin/{branch}."
            if done.exit_code == 0
            else f"Couldn't push {branch}: {_last_line(done.output)}"
        )

    ahead = _git("rev-list", "--count", "@{u}..HEAD").output.strip()
    if ahead in ("", "0"):
        return f"Nothing to push — {branch} is level with {upstream.output.strip()}."

    done = _git("push")
    if done.exit_code == 0:
        return f"Pushed {ahead} commit{'s' if ahead != '1' else ''} on {branch}."
    said = done.output
    if "rejected" in said or "fetch first" in said or "behind" in said:
        return (
            f"{branch} has {ahead} commit{'s' if ahead != '1' else ''} to send and the remote has "
            "moved on — somebody else pushed first. Pull, look at what came back, then push again."
        )
    return f"Couldn't push {branch}: {_last_line(said)}"


def _last_line(output: str) -> str:
    """The line of git's output worth repeating. Its errors put the reason last."""
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    return lines[-1] if lines else "no reason given"


def pull() -> str:
    """Bring in what other people have pushed. A sentence about what happened.

    The other half of `push`, and the half the folder actually depends on: `.kith/` is how a
    second person's work reaches this machine, and nothing arrives until somebody fetches. Git
    is not a sync daemon. Until this existed the advice "pull from git first" was a sentence
    with no operation behind it — which is worse than saying nothing, because it reads as though
    the thing can be done.

    `--ff-only`, deliberately. A merge that has to be resolved is a person's judgement, and a
    merge made without one leaves conflict markers in `.kith/tasks/` — which `unsettled` then
    refuses to read, so the board silently stops updating and the reason is three steps away.
    Refusing up front and saying the branches have diverged puts the decision where it belongs.
    """
    nowhere = _nowhere_to_send("pull")
    if nowhere:
        return nowhere
    if _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").exit_code != 0:
        return "This branch does not track anything yet, so there is nothing to pull."

    dirty = _git("status", "--porcelain").output.strip()
    if dirty:
        return (
            "There are uncommitted changes here, so pulling could leave a half-merged folder. "
            "Commit or set them aside first."
        )

    done = _git("pull", "--ff-only")
    if done.exit_code == 0:
        said = done.output.strip()
        return "Already up to date." if "up to date" in said.lower() else f"Pulled. {_last_line(said)}"
    if "diverge" in done.output or "not possible to fast-forward" in done.output:
        return (
            "This branch and the remote have both moved on, so the two histories have to be "
            "reconciled by hand — that is a judgement, and a merge made without one leaves "
            "conflict markers in files I would then refuse to read."
        )
    return f"Couldn't pull: {_last_line(done.output)}"


def fetch(project_dir: str | Path | None = None) -> str:
    """Ask the remote what it has, without touching the working tree. A sentence.

    The other half of :func:`standing`, and the half that costs a network round trip. Split from
    `pull` on purpose: a fetch cannot conflict, cannot half-merge and cannot lose an edit, so it
    is safe to run in order to *find out* — whereas `pull --ff-only` refuses outright when there
    are uncommitted changes, which is exactly the state somebody mid-job is in. Asking "has
    anything arrived" should not require putting your work down first.

    After this, `standing` is telling the truth rather than reporting the last time somebody
    looked, which is the whole reason it exists as its own operation.
    """
    root = _repo_root(Path(project_dir)) if project_dir else _repo_root(base_dir())
    if root is None:
        return "This folder is not in a git repository, so there is nothing to check."
    if _git("remote", cwd=root).output.strip() == "":
        return "This repository has no remote, so there is nowhere to check."
    done = _git("fetch", "--all", "--quiet", cwd=root, timeout=60)
    if done.exit_code != 0:
        return f"Couldn't reach the remote: {_last_line(done.output)}"
    said = standing(root)
    return said or "Fetched. Nothing new — you are level with the remote."


#: How long a `standing` reading is reused before the files are stat'd again.
#:
#: The prompt's present-state block is rebuilt once per turn, and a turn can be one message or
#: forty tool calls — so this is not "per round", but it is often enough that four `git` processes
#: per reading is worth not paying twice in the same minute. Short enough that a fetch he just ran
#: shows up in the next thing he is told.
_STANDING_TTL = 45.0

#: Keyed by repository root. Small and unbounded, because the number of projects on one machine is
#: a handful — this is not a cache that needs eviction, it is one that needs a clock.
_standing_cache: dict[str, tuple[float, str]] = {}


def standing(project_dir: str | Path) -> str:
    """How this checkout sits against its remote, read locally. "" when there is nothing to say.

    **No network.** Everything here is a stat or a rev-walk over refs that are already on disk,
    so it can sit in the system prompt and be paid for every turn. That is also its honest
    limit: `HEAD..@{u}` counts what the last *fetch* brought down, not what the remote has now.
    A repository nobody has fetched in a week reads as "level with the remote" and is nothing of
    the kind.

    Which is why the age of the last fetch is said out loud beside the count. Those two numbers
    mean opposite things on their own — "0 behind" is agreement if somebody looked this morning
    and is no information at all if nobody has looked since Tuesday — and `board_sync.waiting_here`
    has the same hole one layer up, where it reads a folder that nothing ever refreshes. Local
    mtimes cannot close it: a checkout rewrites them, so a file that arrived from somebody else
    last week looks like it was written just now.

    Silent when there is nothing worth a line — no repository, no remote, no upstream branch. A
    solo project on a laptop has no remote and should not be told about one every turn.
    """
    root = _repo_root(Path(project_dir))
    if root is None or not has_git():
        return ""
    key = str(root)
    hit = _standing_cache.get(key)
    if hit and (time.time() - hit[0]) < _STANDING_TTL:
        return hit[1]
    said = _standing(root)
    _standing_cache[key] = (time.time(), said)
    return said


def _standing(root: Path) -> str:
    """The uncached reading. Split so the cache above is the only thing holding a clock."""
    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=root, timeout=15)
    if upstream.exit_code != 0:
        # No upstream: either a branch nobody has pushed, or a repository with no remote at all.
        # The first is worth a word — work that exists on one machine only — and the second is
        # not, because there is nothing anyone could do about it.
        if _git("remote", cwd=root, timeout=15).output.strip() == "":
            return ""
        branch = (
            _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root, timeout=15).output.strip() or "this branch"
        )
        return f"`{branch}` does not track a remote branch, so nothing here has been shared yet."

    counts = _git("rev-list", "--left-right", "--count", "@{u}...HEAD", cwd=root, timeout=15)
    behind, ahead = 0, 0
    parts = counts.output.split()
    if counts.exit_code == 0 and len(parts) == 2:
        try:
            behind, ahead = int(parts[0]), int(parts[1])
        except ValueError:
            behind = ahead = 0

    said: list[str] = []
    if behind:
        said.append(
            f"**{behind} commit{'s' if behind != 1 else ''} are waiting in {upstream.output.strip()} "
            f"that this folder has not taken in** — `publish` with direction 'in' before you trust "
            f"what is here, including `.kith/`"
        )
    if ahead:
        said.append(f"{ahead} commit{'s' if ahead != 1 else ''} here have not been pushed")

    quiet = _fetched_ago(root)
    if not said:
        # Level and recently checked is silence — this line is paid for on every turn of every
        # project, and "all is well" is not worth that. Level and *not* recently checked is a
        # different thing entirely, and it cannot borrow the phrasing below: there is no count
        # printed for "that count may be out of date" to refer to.
        return "" if not quiet else f"Git: nothing has arrived, but {quiet.split(', so')[0]}."
    if quiet:
        said.append(quiet)
    return "Git: " + "; ".join(said) + "."


#: Past this, "nobody has fetched" stops being a detail and starts being the reason the numbers
#: above are wrong. Six hours rather than a day: somebody else's morning of work is already
#: invisible by lunchtime.
_STALE_FETCH_SECONDS = 6 * 3600


def _fetched_ago(root: Path) -> str:
    """How long since anything was fetched here, said only when it is long enough to matter.

    `FETCH_HEAD`'s mtime, which git rewrites on every fetch and on every pull. It is missing
    entirely in a repository that has only ever been cloned and never fetched since — which is
    the longest-stale case there is, and the one a naive "no file, no problem" would report as
    fine.
    """
    since_clone = False
    marker = root / ".git" / "FETCH_HEAD"
    try:
        age = time.time() - marker.stat().st_mtime
    except OSError:
        # No `FETCH_HEAD` at all: nothing has been fetched since this was cloned. The longest-
        # stale case there is, and the one a naive "no file, no problem" reports as fine. Dated
        # from `.git/HEAD`, which the clone wrote.
        since_clone = True
        try:
            age = time.time() - (root / ".git" / "HEAD").stat().st_mtime
        except OSError:
            return ""
    if age < _STALE_FETCH_SECONDS:
        # Freshly cloned counts as freshly fetched, because it is. The line is about a reading
        # having gone stale, and a clone from ten minutes ago has not.
        return ""
    if since_clone:
        return f"nothing has been fetched here since the clone {_ago(age)} ago"
    return f"nobody has fetched this repository in {_ago(age)}, so that count may be out of date"


def _ago(seconds: float) -> str:
    """Rounded, and never precise: the difference that matters is hours against days."""
    if seconds < 5400:
        return f"{max(1, int(seconds // 60))} minutes"
    if seconds < 172800:
        return f"{int(seconds // 3600)} hours"
    return f"{int(seconds // 86400)} days"
