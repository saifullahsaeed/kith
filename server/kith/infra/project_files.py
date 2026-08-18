"""His own folder *inside* someone's project, so the work survives being handed over.

In `infra/` because that is what it is: paths and files on a disk. It imports nothing from
kith at all — it never did — and sat in `services/` only because the first thing to need it
was a service. `infra/db/repositories/tasks.py` reaching up for it was one of the tree's
upward edges, and the whole fix was noticing which folder it belonged in.

Everything he produces about a project used to land in one of two wrong places. Before
`base_dir` was fixed it went to `~/Kith/work/` — his private scratch space, invisible to
anyone who cloned the repository, which is how a real project ended up with a memory file
whose own references (`work/srs-review.md`) pointed at nothing a second person could reach.
After that fix it went to the project *root*, which is worse in a different way: his
screenshots and review notes sitting beside somebody's `src/`.

So: `<project>/.kith/`. One folder, obviously his, that commits with the repository. Another
person's Kith clones the repo and finds the memory, the briefs and the working notes already
there — which is the whole point, and is why `.kith/` is *not* in the gitignore any more.

    .kith/
      memory.md          what he knows about this project        (committed)
      README.md          what this folder is, for a human        (committed)
      tasks/07-name.md   a readable brief per task               (committed)
      work/notes.md      working files, findings, drafts         (committed)
      scratch/shot.png   screenshots and throwaways              (ignored)
      .gitignore         what must never be committed            (committed)

**The briefs are a mirror, not the board.** Status, priority and ordering stay in the
database, which is the one writer. A brief is a readable record of what a task *is* — enough
for a person, or another Kith, to pick the thread up — and it is written whenever the task
changes rather than being a second place to change it. Two writers on one state is the
failure this project's own thin-shell rule exists to avoid.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kith.domain import keys

#: The folder inside a project that belongs to him.
KITH_DIR = ".kith"

#: What lives where. `scratch` is the only one the gitignore excludes: it is screenshots, and
#: PNGs in a git history are pure weight.
WORK = "work"
TASKS = "tasks"
SCRATCH = "scratch"

README = """\
# .kith

This folder belongs to Kith — an AI that has been working on this project. It is committed
on purpose: clone the repository and it comes with you, so a later session (or somebody
else's Kith) can pick up where this one left off instead of starting from nothing.

- **memory.md** — what has been learned about this project. Read every time he works here,
  so it is deliberately short. Anything under "Working here" is a standing instruction.
- **tasks/** — a readable brief per task: the goal, how you would know it is done, the
  checklist, the notes. A record, not the live board — status and ordering live in Kith's
  own database on whichever machine is driving.
- **work/** — working files. Findings, drafts, reviews, notes-to-self mid-job.
- **scratch/** — screenshots and throwaways. Gitignored; nothing here is meant to last.

It is all plain text and safe to read, edit or delete. Deleting `memory.md` costs the
project's accumulated knowledge, not its code.
"""


#: What must never leave the machine, written where the folder it governs can be read.
#:
#: This existed only as a sentence until now. The README above has always told the reader
#: "**scratch/** — screenshots and throwaways. Gitignored", and nothing anywhere wrote a
#: gitignore, so the promise was decoration. Checked against a real project: four scripts from
#: `.kith/scratch/` are committed. They turned out to be clean, which was luck and not design —
#: `tools/computer.py` instructs him to put scripts that touch "a system, someone's account"
#: into exactly that folder, so it is the one directory here guaranteed to meet credentials.
#:
#: A denylist, not an allowlist, and deliberately: the whole point of `.kith/` is that a second
#: person clones the repository and finds the work already there, so anything new he writes has
#: to be shared by default. An allowlist would silently drop the next kind of file somebody
#: invents, and nobody would notice until they needed it.
#:
#: The credential patterns are shape-based for the same reason `domain/secrets` is: a file
#: called `notes.md` can hold a key and a file called `config.example` usually does not, but
#: `.env` and `id_rsa` are what they say they are, and blocking them costs nothing.
GITIGNORE = """\
# Written by Kith. This folder is committed on purpose — see README.md — so this
# says what must NOT be, rather than what may.

# Throwaways. Screenshots, one-off scripts, anything run once and forgotten. He is told
# to put scripts that touch a system or an account here, so this line is load-bearing.
scratch/

# Credentials, by shape rather than by name.
*.env
.env*
*.key
*.pem
*.p12
*.pfx
id_rsa*
*credentials*
*secret*

# Transcripts, if one is ever written here. They carry whole tool results verbatim —
# measured on this machine: 143 plaintext copies of one API key across two of them.
conversations/
offload/
"""


def kith_dir(project_dir: str | Path) -> Path:
    return Path(project_dir) / KITH_DIR


def ensure(project_dir: str | Path) -> Path:
    """Make the folder and its README. Returns the folder.

    The README is for the person who finds `.kith/` in a diff and wonders what it is. A
    committed folder nobody can explain is a folder somebody deletes.
    """
    here = kith_dir(project_dir)
    here.mkdir(parents=True, exist_ok=True)
    readme = here / "README.md"
    if not readme.exists():
        readme.write_text(README)
    rules = here / ".gitignore"
    if not rules.exists():
        rules.write_text(GITIGNORE)
    return here


def neutered_by(project_dir: str | Path) -> str:
    """The rule in the project's own .gitignore that stops the one above from being read.

    Git does not descend into an ignored directory, so a root-level `.kith/` makes everything
    in `GITIGNORE` dead letter — including the line protecting `scratch/`. And the failure is
    not that nothing is committed: rules only apply to files git is not already tracking, so a
    project that added `.kith/` *after* the folder existed keeps committing all of it while
    reading as though it commits none. That is the live state of one project here — the rule is
    on line 27 and 102 files are tracked past it.

    Reported rather than repaired. The root gitignore is the person's file, deleting a line from
    it changes what their repository does, and a folder called `.kith` that edits their ignore
    rules on its own is exactly the behaviour that gets a folder called `.kith` deleted.
    """
    root = Path(project_dir) / ".gitignore"
    try:
        lines = root.read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        rule = line.split("#", 1)[0].strip().rstrip("/")
        if rule.lstrip("/") == KITH_DIR:
            return line.strip()
    return ""


def folder_for(project_dir: str | Path, kind: str) -> Path:
    """One of `work`, `tasks`, `scratch`, created on demand."""
    if kind not in (WORK, TASKS, SCRATCH):
        raise ValueError(f"unknown kind {kind!r}")
    here = ensure(project_dir) / kind
    here.mkdir(parents=True, exist_ok=True)
    return here


def slug(text: str, limit: int = 42) -> str:
    """A filename-safe stub of a task's goal, so a directory listing is readable.

    `07-build-the-dashboard-shell.md` tells you what it is; `07.md` makes you open it.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return cleaned[:limit].strip("-") or "task"


def plan_path(project_dir: str | Path, task_id: int) -> Path:
    """Where the `planning-a-task` skill files a task's plan.

    Built from `kith_dir` rather than `folder_for` on purpose: `folder_for` creates the folder,
    and asking whether a plan exists must not be what brings `.kith/work/` into being.
    """
    return kith_dir(project_dir) / WORK / f"task-{int(task_id)}.md"


def read_plan(project_dir: str | Path, task_id: int) -> str:
    """The plan filed for this task, or "" if there is none.

    Best-effort by design, and the design is the skill's: the path is prose guidance to a model,
    not an enforced location, so a plan filed anywhere else does not show. Silence beats an error
    here — nothing about a task should fail to load because its plan is missing or unreadable.
    """
    try:
        doc = plan_path(project_dir, task_id)
        return doc.read_text(encoding="utf-8") if doc.is_file() else ""
    except OSError:
        return ""


def brief_path(project_dir: str | Path, task_id: int, goal: str, key: str = "") -> Path:
    """Where a task's brief lives. Named by its key once it has one.

    The number was fine while one machine wrote here. It is not once two do: both would write
    `108-something.md` for two different tasks, and git would merge them into one file — a
    conflict on the same *name* for things that were never the same thing. The key is unique
    without anyone coordinating, so the collision cannot happen. See `domain/keys`.

    Falls back to the number, and that is not only for old files: a task written before its key
    existed keeps the name it already has, so the 57 briefs already committed in one project
    here stay exactly where they are and keep reading.
    """
    stem = str(key or "").strip().lower() or f"{int(task_id):02d}"
    return folder_for(project_dir, TASKS) / f"{stem}-{slug(goal)}.md"


def write_brief(project_dir: str | Path, task: dict[str, Any]) -> Path | None:
    """Mirror one task to a readable file. Returns the path, or None if it could not be.

    Rewritten wholesale each time rather than patched: the database is the source and this is
    a projection of it, so there is nothing here worth merging and a partial update would be
    a third state to reason about.

    Never raises. A brief that fails to write must not take down the task change that
    triggered it — the board is the thing that matters and this is the copy.
    """
    try:
        task_id = int(task.get("id") or 0)
        if not task_id:
            return None
        key = str(task.get("key") or "").strip().lower()
        path = brief_path(project_dir, task_id, str(task.get("goal") or ""), key)
        # An old brief under a different slug — the goal was reworded — would otherwise sit
        # there for ever alongside the new one, and a directory of stale duplicates is worse
        # than no directory. Both names are swept: a task that had a brief before it had a key
        # has one under its number too, and leaving that behind would turn one task into two.
        for stem in {f"{task_id:02d}", key} - {""}:
            for stale in path.parent.glob(f"{stem}-*.md"):
                if stale != path:
                    stale.unlink(missing_ok=True)
        path.write_text(_brief(task))
        return path
    except (OSError, ValueError, TypeError):
        return None


def _filed_by(task: dict[str, Any]) -> str:
    """ "saif@example.com (Kith)", or "" when nobody was recorded.

    Blank rather than a guess for the tasks written before `account` existed — see
    `v39_task_account`. "unknown (Kith)" reads like an answer and is not one.
    """
    account = str(task.get("account") or "").strip()
    if not account:
        return ""
    who = str(task.get("created_by") or "").strip()
    return f"{account} ({'Kith' if who == 'kith' else 'you'})" if who else account


def _brief(task: dict[str, Any]) -> str:
    """One task as markdown, written for someone who has not seen the board."""
    goal = str(task.get("goal") or "").strip() or "(untitled)"
    lines = [f"# {goal}", ""]

    # `account` before `created_by` because with two people sharing this folder "whose" is the
    # question and "his idea or theirs" is the qualifier, not the other way round. The brief is
    # the half that actually travels — the database stays on whichever machine is driving — so
    # if the name is not written here it is not written anywhere a second person can read.
    facts = [
        ("Status", task.get("status")),
        ("Priority", task.get("priority")),
        ("Filed by", _filed_by(task)),
        # When, so that two records of one task can be told apart. Without it a merge has no
        # rule: a brief that has sat unchanged since before a status moved would look exactly
        # like one somebody just pushed, and adopting it would silently undo the newer fact.
        # Measured on a real folder — two briefs said `doing` and `review` for tasks the board
        # had marked `done` weeks ago, and one of those is not a status any more.
        ("Updated", task.get("updated_at")),
    ]
    said = [f"**{label}:** {value}" for label, value in facts if value]
    if said:
        lines += [" · ".join(said), ""]

    done = (task.get("description") or "").strip()
    if done:
        lines += ["## How you know it is done", "", done, ""]

    checklist = task.get("checklist") or []
    if checklist:
        lines += ["## Checklist", ""]
        for item in checklist:
            mark = "x" if item.get("done") else " "
            lines.append(f"- [{mark}] {str(item.get('text') or '').strip()}")
        lines.append("")

        # The notes section came from the task's comment thread, which is gone. Progress lives in
        # `.kith/work/task-<id>.md` now — a file, next to this one, that he already reads every turn.
        lines.append("")

    delivered = task.get("deliverables") or []
    if delivered:
        lines += ["## Delivered", ""]
        for one in delivered:
            title = str(one.get("title") or "").strip()
            where = str(one.get("path") or "").strip()
            lines.append(f"- {title}" + (f" — `{where}`" if where else ""))
        lines.append("")

    lines += [
        "---",
        "",
        "Mirrored from Kith's board. Edit the task in Kith rather than here — this file is "
        "rewritten whenever the task changes.",
    ]
    return "\n".join(lines) + "\n"


# -- reading them back ------------------------------------------------------- #
#
# The briefs have only ever been written. Whether they can be *read* — whether what is in the
# folder is enough to reconstruct the board rather than merely describe it — has never been
# tested, and the whole question of making the files authoritative rests on the answer.
#
# So this exists to be checked against the database before anything is trusted to it. Not a
# second writer, not yet a source of truth: a way to find out what the folder is missing, on a
# real project, before betting a task board on a guess.


def read_brief(doc: str | Path) -> dict[str, Any]:
    """One brief back into the shape `write_brief` was handed. Empty dict if it is not one.

    Deliberately forgiving about everything except the id. A person is invited to edit these —
    the folder is theirs once it is committed — so a hand-written heading, a reordered fact line
    or a stray blank must not make a task vanish. What cannot be forgiven is the identity: a
    brief whose id cannot be read is a file, not a task.
    """
    path = Path(doc)
    stem = re.match(r"([0-9a-fA-F]+)-", path.name)
    if not stem:
        return {}
    found = stem.group(1)
    key = found.lower() if keys.looks_like_a_key(found) else ""
    if not key and not found.isdigit():
        return {}  # hex, but neither a key nor a number: not one of ours
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    out: dict[str, Any] = {"key": key, "checklist": [], "deliverables": []}
    if not key:
        out["id"] = int(found)
    title = re.search(r"^#\s+(.+)$", text, re.M)
    if title:
        out["goal"] = title.group(1).strip()

    for label, key in (
        ("Status", "status"),
        ("Priority", "priority"),
        ("Filed by", "filed_by"),
        ("Updated", "updated_at"),
    ):
        found = re.search(rf"\*\*{label}:\*\*\s*([^·\n]+)", text)
        if found:
            out[key] = found.group(1).strip()
    if out.get("filed_by"):
        # "saif@example.com (Kith)" — split back into the two facts it was made from.
        pair = re.match(r"(.+?)\s*\((Kith|you)\)\s*$", str(out.pop("filed_by")))
        if pair:
            out["account"] = pair.group(1).strip()
            out["created_by"] = "kith" if pair.group(2) == "Kith" else "user"

    done = re.search(r"^## How you know it is done\s*\n+(.*?)(?=\n## |\n---|\Z)", text, re.M | re.S)
    if done:
        out["description"] = done.group(1).strip()

    for mark, item in re.findall(r"^- \[([ xX])\]\s*(.+)$", text, re.M):
        out["checklist"].append({"text": item.strip(), "done": mark.lower() == "x"})

    delivered = re.search(r"^## Delivered\s*\n+(.*?)(?=\n## |\n---|\Z)", text, re.M | re.S)
    if delivered:
        for line in delivered.group(1).splitlines():
            one = re.match(r"^-\s+(.*?)(?:\s+—\s+`(.+)`)?\s*$", line.strip())
            if one and one.group(1):
                out["deliverables"].append({"title": one.group(1).strip(), "path": one.group(2) or ""})
    return out


def read_board(project_dir: str | Path) -> dict[Any, dict[str, Any]]:
    """Every task this folder knows about, keyed by whatever names it.

    A key when it has one, the old number when it does not. Deliberately mixed rather than
    normalised to one or the other: a folder mid-migration genuinely holds both, and pretending
    otherwise would mean either inventing keys for old briefs or throwing new ones away.
    """
    folder = kith_dir(project_dir) / TASKS
    if not folder.is_dir():
        return {}
    board: dict[Any, dict[str, Any]] = {}
    for doc in sorted(folder.glob("*.md")):
        task = read_brief(doc)
        if task:
            board[task.get("key") or int(task["id"])] = task
    return board


def forget_brief(project_dir: str | Path, task_id: int, key: str = "") -> bool:
    """Remove a task's brief. True if there was one.

    The half of `write_brief` that was never written, and its absence is why the folder is
    append-only in practice: `write_brief` deletes a stale *slug* for the same id — the goal was
    reworded — and nothing has ever deleted a brief because its task was gone. Measured on a real
    project, 17 of 57 briefs name tasks that no longer exist anywhere.

    Harmless clutter while the database is the board, and the exact opposite once the files are:
    a brief nobody deletes becomes a task nobody can close.
    """
    try:
        folder = kith_dir(project_dir) / TASKS
        gone = False
        # Both names, for the same reason `write_brief` sweeps both: a task from before keys
        # existed may still be filed under its number.
        for stem in {f"{int(task_id):02d}", str(key or "").strip().lower()} - {""}:
            for doc in folder.glob(f"{stem}-*.md"):
                doc.unlink(missing_ok=True)
                gone = True
        return gone
    except (OSError, ValueError, TypeError):
        return False


def reconcile(project_dir: str | Path, board: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """What the folder and the board disagree about. Neither is corrected; both are described.

    Takes the board as a plain dict rather than reaching for a database, because this module
    imports nothing from kith and is not about to start — and because the interesting comparison
    is between two *records*, which is a question about data, not about storage.

    Written to answer one question before anything was bet on it: can the folder carry the board?
    Run against a real project, the answer was yes and came with two surprises — nothing on the
    board was missing from the folder, and the folder held 29 tasks the board did not, 17 of them
    deleted long ago. `only_in_files` is the one worth watching: it is ghosts.
    """

    # Both sides named the same way before anything is compared. A folder mid-migration holds
    # briefs filed under a number and briefs filed under a key, and the board knows both for
    # every task — so comparing one naming against the other would report every task twice, once
    # as a ghost and once as a gap.
    def _named(task: dict[str, Any], fallback: Any) -> Any:
        return str(task.get("key") or "").strip().lower() or fallback

    theirs = {_named(task, int(handle)): task for handle, task in board.items()}
    by_id = {int(handle): _named(task, int(handle)) for handle, task in board.items()}

    ours: dict[Any, dict[str, Any]] = {}
    for handle, task in read_board(project_dir).items():
        # A brief still filed under its number belongs to whatever the board calls that number.
        ours[task.get("key") or by_id.get(handle, handle)] = task

    disagree = []
    for name in sorted(set(ours) & set(theirs), key=str):
        for field in ("goal", "status", "priority"):
            mine = str(ours[name].get(field) or "").strip()
            yours = str(theirs[name].get(field) or "").strip()
            if mine and yours and mine != yours:
                disagree.append({"id": name, "field": field, "board": yours, "file": mine})
    return {
        "only_in_files": sorted(set(ours) - set(theirs), key=str),
        "only_on_board": sorted(set(theirs) - set(ours), key=str),
        "disagree": disagree,
    }


#: What git leaves in a file it could not merge. Checked for literally rather than parsed,
#: because the whole point is that this text is not valid content of anything.
_CONFLICT = "<<<<<<< "


def unsettled(project_dir: str | Path) -> str:
    """Why this folder must not be read right now, or "" if it may be.

    `read_brief` is forgiving on purpose — the folder is theirs once it is committed, so a
    hand-edited heading must not make a task vanish. That tolerance is exactly what makes an
    unmerged folder dangerous: git leaves

        <<<<<<< HEAD
        **Status:** done
        =======
        **Status:** working
        >>>>>>> origin/main

    in the file, and a forgiving reader takes the first `**Status:**` it finds and imports
    `done`. Silently, and the conflict is resolved by having been read — the one outcome here
    that nobody can unwind, because the losing side is gone before anyone saw there was a
    disagreement.

    So this is asked before any brief is read, and it answers in words rather than a boolean:
    the caller is a turn and the person needs to know *which* file to go and look at.
    """
    project = Path(project_dir)
    for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD"):
        if (project / ".git" / marker).exists():
            return f"this repository is in the middle of something ({marker.split('_')[0].lower()})"
    folder = kith_dir(project) / TASKS
    if not folder.is_dir():
        return ""
    for doc in sorted(folder.glob("*.md")):
        try:
            if _CONFLICT in doc.read_text(encoding="utf-8"):
                return f"`{doc.name}` still has conflict markers in it"
        except OSError:
            continue
    return ""


def last_changed(project_dir: str | Path) -> float:
    """When a brief in this folder was last written, as an epoch time. 0 for none.

    So that "nothing came in" and "nobody fetched" stop looking alike. They are the same silence
    and they mean opposite things — the first is agreement and the second is a day of somebody
    else's work you have not seen.
    """
    folder = kith_dir(project_dir) / TASKS
    if not folder.is_dir():
        return 0.0
    newest = 0.0
    for doc in folder.glob("*.md"):
        try:
            newest = max(newest, doc.stat().st_mtime)
        except OSError:
            continue
    return newest
