"""Reading, writing, editing and listing files.

The largest module in here and deliberately still one: reading a file, changing it and
listing the folder it sits in are the same job from three angles, and `edit_file` shares its
tolerant matching with nothing else.
"""

from __future__ import annotations

import mimetypes
import shlex
import shutil
import subprocess
from pathlib import Path

from kith.services import permissions

from .base import _EXEC_TIMEOUT, WorkspaceError, _clip
from .checkpoints import _checkpoint_before_change
from .paths import INTERNAL_DIR, resolve, root
from .shell import run_command

#: What one `read_file` may return, separately from what a *command* may print.
#:
#: They shared the 8,000 and should not: clipping arbitrary command output there is sensible,
#: because the interesting part of a build log is at the end and the rest is noise. A source
#: file is not noise, and 8,000 characters is about 160 lines — under a typical React
#: component. Measured on a real project: a 271-line page returned 160 lines, and a 172-line
#: page returned 158, so reading it whole cost *two* calls where the second fetched fourteen
#: lines. A second call is a second round, and a round re-sends the ~20,000-token prompt
#: floor — twenty thousand tokens to collect fourteen lines of TSX.
_READ_LIMIT = 16_000

#: How far past the limit to go rather than force another call.
#:
#: The cost of stopping is not the characters saved, it is the round the caller must spend to
#: ask again. Overshooting by half a limit is always cheaper than that, so a file that is
#: nearly finished gets finished.
_READ_OVERSHOOT = 8_000
_MAX_WRITE = 5_000_000
_READ_DEFAULT_LINES = 400
_MAX_UI_READ = 2_000_000


#: Image types worth handing to a vision model. Anything else is bytes as far as this is
#: concerned and gets the ordinary "not text" refusal.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

#: Cap on an image handed to the model. A screenshot at 1440 is well under this; a 20MB
#: capture is a payload nobody meant to send and the message says how to shrink it.
_MAX_IMAGE_BYTES = 3_000_000


def read_image(path: str) -> dict:
    """An image, as a data URI the model can actually look at.

    This exists because of something the record made obvious. He spent hours redesigning a UI,
    took Playwright screenshots at 1440 and 390 on every pass, attached them as deliverables —
    and could not see a single one of them, because ``read_file`` decodes as text. His model
    takes images. He was working blind on the one kind of task where looking is the whole job.

    Returned as data rather than text because a tool result is a JSON string and cannot carry
    an image part; the agent loop turns this into a message the model can see. See
    :mod:`kith.services.agent_loop`.
    """
    import base64
    import mimetypes

    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.is_file():
        raise WorkspaceError(f"there's no {path}")
    size = target.stat().st_size
    if size > _MAX_IMAGE_BYTES:
        raise WorkspaceError(
            f"{path} is {size:,} bytes, over the {_MAX_IMAGE_BYTES:,} limit for looking at an "
            "image. Shrink it first — a screenshot does not need to be full resolution to be "
            "judged."
        )
    kind = mimetypes.guess_type(target.name)[0] or "image/png"
    encoded = base64.b64encode(target.read_bytes()).decode()
    return {
        "path": str(target),
        "bytes": size,
        # The loop looks for this key. Named plainly so a reader of a transcript can see why a
        # picture appeared in the conversation.
        "image": f"data:{kind};base64,{encoded}",
        "note": "Look at the image below and describe or judge what you actually see.",
    }


def read_file(path: str, offset: int | None = None, limit: int | None = None) -> str:
    """Read a file, line-numbered and windowed, so it composes with grep and cannot
    dump a huge file into context."""
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.exists():
        raise WorkspaceError(f"there's no {path}")
    if target.is_dir():
        raise WorkspaceError(f"{path} is a folder, not a file")
    start = max(1, offset or 1)
    count = limit if (limit and limit > 0) else _READ_DEFAULT_LINES
    end = start + count - 1
    try:
        lines = target.read_text(errors="replace").splitlines()
    except OSError as exc:
        raise WorkspaceError(f"cannot read {path}: {exc}") from None
    window = lines[start - 1 : end]

    # Fill up to the output budget a whole line at a time, and remember which line we stopped
    # on. Two things were wrong with slicing the finished string instead.
    #
    # It cut mid-line — a CSS rule or a JSX attribute sheared in half, which is not something
    # anyone can reason about. And worse, the "read with offset=" hint below only fired when
    # the *line* window ran out, so a short file over the byte budget produced a dead end:
    # "[truncated, 16078 chars total]" with no offset and no next step. Watched him hit
    # exactly that — a 79-line stylesheet, asked for whole, half returned, no way to ask for
    # the rest — so he read the same two files five times in one step and got the same first
    # half every time.
    # How much this read may return. The whole rest of the file, when finishing it costs less
    # than the round the caller would otherwise spend coming back for the remainder.
    numbered_window = [f"{start + i:6d}\t{line}" for i, line in enumerate(window)]
    whole = sum(len(one) + 1 for one in numbered_window)
    budget = _READ_LIMIT + _READ_OVERSHOOT if whole <= _READ_LIMIT + _READ_OVERSHOOT else _READ_LIMIT

    rendered: list[str] = []
    used = 0
    for numbered in numbered_window:
        if rendered and used + len(numbered) + 1 > budget:
            break
        rendered.append(numbered)
        used += len(numbered) + 1
    shown = len(rendered)
    body = "\n".join(rendered)

    last = start + shown - 1
    if shown < len(window) or len(lines) > end:
        # Whichever limit bit, the sentence is the same and it always carries the offset.
        reason = "output limit" if shown < len(window) else f"{count}-line window"
        body += (
            f"\n… [showing lines {start}-{last} of {len(lines)} — stopped at the {reason}; "
            f"read with offset={last + 1} for the rest]"
        )
    return body


#: Bytes the viewer will stream for one picture or document. Larger than the text limit
#: on purpose: a full-page screenshot at 2x is routinely over a megabyte, and the whole
#: point is to see it. Still a limit, because the browser holds all of it in memory.
_MAX_MEDIA_BYTES = 40_000_000


def media_file(path: str) -> tuple[Path, str]:
    """A file to be served as-is, and the type to send it as.

    For the things the viewer can show without decoding them as text — a screenshot, a
    PDF. Returns the resolved path rather than the bytes so the response can stream it
    and answer range requests, which is how a PDF viewer reads a document: it wants the
    trailer first, not the whole file.

    Same gate as every other read. Serving raw bytes over HTTP is exactly the shape of
    bug that turns a file viewer into "read any file on this machine", so the permission
    check is the first thing that happens and the path is resolved before it.
    """
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.is_file():
        raise WorkspaceError(f"there's no {path}")
    size = target.stat().st_size
    if size > _MAX_MEDIA_BYTES:
        raise WorkspaceError(
            f"{path} is {size:,} bytes, too big to show here (max {_MAX_MEDIA_BYTES:,}) — "
            "open it in another application instead"
        )
    kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return target, kind


def read_raw(path: str, max_bytes: int = _MAX_UI_READ) -> str:
    """The file exactly as it is, for the viewer — no line numbers, no window."""
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.exists():
        raise WorkspaceError(f"there's no {path}")
    size = target.stat().st_size
    if size > max_bytes:
        raise WorkspaceError(f"that file is too big to open here ({size} bytes; max {max_bytes})")
    try:
        return target.read_text()
    except UnicodeDecodeError as exc:
        raise WorkspaceError("that looks like a binary file, not text") from exc
    except OSError as exc:
        raise WorkspaceError(f"cannot read {path}: {exc}") from None


def grep(pattern: str, path: str = ".", glob: str | None = None, max_matches: int = 60) -> str:
    """Search with ripgrep if it is installed, grep if it is not.

    Falling back matters more here than it did in the container: there we shipped the
    image and knew rg was in it. On your machine it is whatever you happen to have.
    """
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if shutil.which("rg"):
        args = ["rg", "--line-number", "--no-heading", "--color", "never", "--max-columns", "300"]
        if glob:
            args += ["--glob", glob]
        args += ["-e", pattern, str(target)]
    else:
        args = ["grep", "-rIn", "--color=never"]
        if glob:
            args += [f"--include={glob}"]
        args += ["-e", pattern, str(target)]
    result = run_command(f"{shlex.join(args)} 2>/dev/null | head -n {int(max_matches)}", timeout=60)
    out = result.output.strip()
    if not out:
        return f"No matches for {pattern!r} under {path}."
    lines = out.splitlines()
    tail = (
        f"\n… [showing first {max_matches} matches; narrow the pattern or set a path for the rest]"
        if len(lines) >= max_matches
        else ""
    )
    return _clip(out + tail)


def write_file(path: str, content: str) -> str:
    data = content.encode()
    if len(data) > _MAX_WRITE:
        raise WorkspaceError(f"content too large ({len(data)} bytes; max {_MAX_WRITE})")
    target = Path(resolve(path))
    permissions.require_path("write", target, root())
    _checkpoint_before_change("write_file")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        raise WorkspaceError(f"cannot write {path}: {exc}") from None
    return f"wrote {len(data)} bytes to {target}"


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _reindent(text: str, base: str, strip: str) -> str:
    """Re-base every line of ``text`` from the copy's indentation onto the file's.

    Used only by the whitespace-tolerant path. When the model's copy of a block was indented
    differently from the file, its replacement is indented to match its copy rather than the
    file, so pasting it verbatim would land at the wrong depth.

    Only the *base* indentation is exchanged. Indentation relative to that base is structure —
    the body of the function inside the block being replaced — and flattening it produces
    syntactically broken Python, which an earlier version of this did by reaching for
    ``lstrip``.
    """
    out = []
    for line in text.split("\n"):
        if not line.strip():
            out.append(line)
            continue
        if not strip:
            body = line
        elif line.startswith(strip):
            body = line[len(strip) :]
        else:
            # Shallower than the copy's own base — nothing sensible to subtract.
            body = line.lstrip()
        out.append(base + body)
    return "\n".join(out)


def _tolerant_span(before: str, old: str) -> tuple[int, int, str] | None:
    """Find ``old`` in ``before`` ignoring each line's leading and trailing whitespace.

    The exact matcher refuses rather than guesses, and that is right — but it also refuses on
    a class of near-miss that is never actually ambiguous: the model copied the block
    correctly and got the indentation wrong, or the file uses tabs where the copy used
    spaces. Every one of those costs a whole round to rediscover, and the model's usual
    recovery is to re-read the file and try again with the same mistake.

    So: match line by line on stripped content, and accept **only** when exactly one window
    matches. Two candidates is genuine ambiguity and still refuses. The caller is told the
    match was tolerant rather than exact, because an edit that landed somewhere slightly
    different from where it was aimed is something a person reviewing the diff should see.

    Returns the character span to replace and the file's own indentation at that point.
    """
    old_lines = old.split("\n")
    # A single-line `old` with no exact match is not worth guessing at: one stripped line
    # matches far too easily, and the failure mode is an edit landing on the wrong line.
    if len(old_lines) < 2:
        return None
    wanted = [line.strip() for line in old_lines]

    lines = before.split("\n")
    # Offsets of each line's start, so a line window converts back to a character span.
    starts, at = [], 0
    for line in lines:
        starts.append(at)
        at += len(line) + 1

    hits = []
    for i in range(len(lines) - len(wanted) + 1):
        if all(lines[i + j].strip() == wanted[j] for j in range(len(wanted))):
            hits.append(i)
            if len(hits) > 1:
                return None  # ambiguous — fall through to the honest refusal
    if len(hits) != 1:
        return None

    i = hits[0]
    start = starts[i]
    end = starts[i + len(wanted) - 1] + len(lines[i + len(wanted) - 1])
    return start, end, _indent_of(lines[i])


def _apply_edit(before: str, old: str, new: str, path: str, replace_all: bool) -> tuple[str, int, bool]:
    """One edit against text already in hand. Returns the result, how many, and whether
    the match had to fall back to whitespace-tolerant matching.

    Split out of ``edit_file`` so a batch can apply several edits to one file in memory
    before anything is written — which is what makes the batch atomic.
    """
    if not old:
        raise WorkspaceError("old must be the exact text to replace — an empty string matches nothing")
    if old == new:
        raise WorkspaceError("old and new are identical, so there is nothing to change")

    found = before.count(old)
    if found > 1 and not replace_all:
        raise WorkspaceError(
            f"that text appears {found} times in {path}, so which one is ambiguous. Include "
            "more surrounding lines to pin down the one you mean, or pass replace_all to "
            "change every occurrence."
        )
    if found:
        after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
        return after, (found if replace_all else 1), False

    span = _tolerant_span(before, old)
    if span is None:
        raise WorkspaceError(
            f"that exact text is not in {path}. Whitespace and indentation count — read the "
            "part you mean to change and copy it verbatim."
        )
    start, end, indent = span
    shifted = _reindent(new, indent, _indent_of(old.split("\n")[0]))
    return before[:start] + shifted + before[end:], 1, True


def edit_file(path: str, old: str, new: str, replace_all: bool = False) -> str:
    """Replace an exact string in a file. Returns a diff of what changed.

    This exists because ``write_file`` was the only way to change anything, and rewriting a
    whole file to alter one line has three costs that all showed up in his work.
    It is expensive — a 12KB component is ~3,300 output tokens per edit. It is lossy: he
    regenerates from what he remembers reading, so anything he did not re-emit is gone, and
    while ``read_file`` was truncating without a way to continue, "anything he did not read"
    was in that category too. And it degrades formatting, because a model paying by the token
    to re-emit a file compresses it: his App.jsx ended up 55 lines averaging 220 characters,
    with one JSX line of 3,262.

    Exact string matching, no regex and no fuzzy fallback, and it refuses rather than guesses:

    * **Not found** is an error, not a no-op. A silent no-op reads as success and he moves on
      believing the change landed.
    * **Ambiguous** is an error too. If the string appears four times, replacing the first is
      a coin flip on which one he meant; the message says how many and what to do about it.

    Both refusals name the fix, because the caller is a model that will otherwise retry the
    identical call.

    One concession to reality, added later and deliberately narrow: if the text is not there
    verbatim but exactly one block matches it line-for-line ignoring indentation, that block
    is edited and the result says the match was tolerant. See ``_tolerant_span`` — two
    candidates still refuse, and a one-line ``old`` never takes this path.
    """
    target = Path(resolve(path))
    permissions.require_path("write", target, root())
    _checkpoint_before_change("edit_file")
    if not target.is_file():
        raise WorkspaceError(f"there's no {path} to edit")
    try:
        before = target.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkspaceError(f"cannot read {path} to edit it: {exc}") from None

    after, replacements, tolerant = _apply_edit(before, old, new, path, replace_all)
    data = after.encode()
    if len(data) > _MAX_WRITE:
        raise WorkspaceError(f"the result would be too large ({len(data)} bytes; max {_MAX_WRITE})")
    try:
        target.write_text(after)
    except OSError as exc:
        raise WorkspaceError(f"cannot write {path}: {exc}") from None

    report = _diff(path, before, after, old, replacements)
    return _NOTE_TOLERANT + report if tolerant else report


#: Prefixed to a diff whose match was whitespace-tolerant rather than exact. Worth saying out
#: loud: the edit landed where it was aimed, but not at the indentation it was aimed with.
_NOTE_TOLERANT = (
    "(matched ignoring indentation — your copy's whitespace did not match the file's, so the "
    "replacement was re-indented to fit. Check the diff.)\n"
)


def edit_files(edits: list[dict]) -> dict:
    """Apply several edits as one all-or-nothing change. Returns a combined diff.

    One edit per model round is the wrong unit for the work that actually happens. Renaming a
    helper used in eight places is eight rounds, and a round is not cheap: the whole prompt
    goes back over the wire each time, against a turn that gets forty of them. Batching a
    refactor into one call is the difference between finishing it and running out of room
    halfway through, which is a failure mode this project has watched happen.

    **Atomic, and that is the point.** Every edit is resolved, permission-checked and applied
    in memory first; nothing touches the disk until all of them have succeeded. A batch that
    fails on its sixth edit leaves the first five unwritten, because the alternative — a
    half-applied refactor across five files, reported as an error — is a worse place to be
    than not having started. It is also the state a model is least able to reason its way out
    of, since the error says what went wrong with edit six and nothing about the five that
    landed.

    **Edits to the same file compose in order.** They are applied to the running text, so an
    edit may legitimately depend on one before it, and an edit whose target a previous edit
    destroyed fails at that point rather than silently matching something else.
    """
    if not edits:
        raise WorkspaceError("no edits given — pass at least one {path, old, new}")

    # Resolve and permission-check everything before reading anything, so a batch that is
    # going to be refused is refused before it has half-read the disk.
    prepared = []
    for i, edit in enumerate(edits, start=1):
        raw = str(edit.get("path") or "").strip()
        if not raw:
            raise WorkspaceError(f"edit {i} has no path")
        target = Path(resolve(raw))
        permissions.require_path("write", target, root())
        prepared.append((i, raw, target, edit))

    # Once for the whole batch, not once per edit — the snapshot is "before any of the
    # batch," matching the batch's own atomicity: nothing here is written until every edit
    # has succeeded, so there is no in-between state worth a checkpoint of its own.
    _checkpoint_before_change("edit_files")

    texts: dict[Path, str] = {}
    originals: dict[Path, str] = {}
    counts: dict[Path, int] = {}
    tolerant_at: list[int] = []

    for i, raw, target, edit in prepared:
        if target not in texts:
            if not target.is_file():
                raise WorkspaceError(f"edit {i}: there's no {raw} to edit")
            try:
                texts[target] = target.read_text()
            except (OSError, UnicodeDecodeError) as exc:
                raise WorkspaceError(f"edit {i}: cannot read {raw} to edit it: {exc}") from None
            originals[target] = texts[target]
            counts[target] = 0
        try:
            after, made, tolerant = _apply_edit(
                texts[target],
                str(edit.get("old") or ""),
                str(edit.get("new") or ""),
                raw,
                bool(edit.get("replace_all")),
            )
        except WorkspaceError as exc:
            # Which edit, out of how many — a bare message about text not being found is
            # unactionable when six edits went out together.
            raise WorkspaceError(f"edit {i} of {len(prepared)} failed, so none were applied: {exc}") from None
        texts[target] = after
        counts[target] += made
        if tolerant:
            tolerant_at.append(i)

    for target, after in texts.items():
        data = after.encode()
        if len(data) > _MAX_WRITE:
            raise WorkspaceError(f"{target} would be too large ({len(data)} bytes; max {_MAX_WRITE})")

    written = []
    for target, after in texts.items():
        try:
            target.write_text(after)
        except OSError as exc:
            # Half-written is the one state the all-or-nothing promise cannot cover: the
            # earlier files are already on disk. Say so plainly rather than reporting a
            # clean failure the caller would take to mean nothing changed.
            done = ", ".join(str(p) for p in written) or "none"
            raise WorkspaceError(
                f"cannot write {target}: {exc}. Already written: {done}. The change is "
                "partly applied — check `changes` before doing anything else."
            ) from None
        written.append(target)

    diffs = [_diff(str(target), originals[target], texts[target], "", counts[target]) for target in texts]
    result = {
        "files": len(texts),
        "replacements": sum(counts.values()),
        "diff": _clip("\n".join(diffs)),
    }
    if tolerant_at:
        result["note"] = (
            f"edit{'' if len(tolerant_at) == 1 else 's'} {', '.join(map(str, tolerant_at))} "
            "matched ignoring indentation and were re-indented to fit — check the diff."
        )
    return result


#: A diff line longer than this is not readable as a line, so the change is shown as a
#: character window instead. His App.jsx has a 3,262-character line of JSX; a one-word edit to
#: it produced a 10,931-character unified diff in which the changed line was truncated *before*
#: the change — a page of context that showed nothing.
_DIFF_LINE = 220


def _window(before: str, after: str, old: str, path: str, replacements: int) -> str:
    """The change as a character window, for a file whose lines are too long to diff.

    Exact rather than guessed: the offset of the replaced text is known, so the window is
    centred on it and the line number is reported so the position is not lost.
    """
    at = before.find(old)
    line_no = before.count("\n", 0, at) + 1
    pad = 90
    lo = max(0, at - pad)
    was = before[lo : at + len(old) + pad].replace("\n", "⏎")
    # The same span in the new text: everything before the change is identical, so the offset
    # holds and only the replaced length differs.
    now = after[lo : at + len(old) + pad + 200].replace("\n", "⏎")
    lead = "…" if lo > 0 else ""
    return (
        f"{replacements} replacement{'' if replacements == 1 else 's'} in {path}, line {line_no}"
        f" (lines here are too long to diff, so this is the changed region)\n"
        f"- {lead}{was}…\n"
        f"+ {lead}{now}…"
    )


def _diff(path: str, before: str, after: str, old: str, replacements: int) -> str:
    """A unified diff of one edit, clipped.

    Returned rather than "ok" on purpose: the diff is the only way he can see that what he
    changed is what he meant to change, and it is the thing worth putting in front of a person
    reviewing an edit you did not watch. Three lines of context — enough to place the change, not
    enough to re-send the file he already has.
    """
    import difflib

    return_window = max((len(line) for line in before.splitlines()), default=0) > _DIFF_LINE
    if return_window and replacements == 1:
        return _window(before, after, old, path, replacements)

    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
            n=3,
        )
    )
    made = f"{replacements} replacement{'' if replacements == 1 else 's'} in {path}"
    if not lines:
        # replace() found the text and the result is identical — a no-change edit that is not
        # worth reporting as a success without saying so.
        return f"{made}, but the file is unchanged"
    return made + "\n" + _clip("".join(lines))


#: What a project is checked with, in the order the presence of a file decides it. Detection
#: rather than configuration: he should not have to be told what a project is, and the answer
#: is sitting in the directory.
_CHECKERS = (
    ("tsconfig.json", "npx tsc --noEmit", "TypeScript"),
    ("pyproject.toml", "ruff check .", "Ruff"),
    ("ruff.toml", "ruff check .", "Ruff"),
    ("package.json", "npm run --silent build", "the project's build"),
)


def check_code(path: str = ".") -> dict:
    """Run whatever this project is checked with, and report only what is wrong.

    He *could* shell out for this, and mostly did — he ran `npm run build` before claiming
    things, which is better discipline than most. But "mostly" is the problem: a check he has
    to remember is a check that is skipped on the round where it mattered. Detected from the
    directory so there is nothing to configure and nothing to get wrong.
    """
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.is_dir():
        target = target.parent
    for marker, command, label in _CHECKERS:
        if not (target / marker).is_file():
            continue
        proc = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            cwd=str(target),
            timeout=_EXEC_TIMEOUT,
        )
        out = (proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")).strip()
        return {
            "ran": command,
            "checker": label,
            "clean": proc.returncode == 0,
            # On success the output is noise — a build log nobody reads. On failure it is the
            # entire point, so it is kept.
            "problems": "" if proc.returncode == 0 else _clip(out),
        }
    return {
        "ran": "",
        "clean": True,
        "problems": "",
        "note": f"Nothing in {path} says how it is checked — no tsconfig.json, pyproject.toml or package.json.",
    }


def glob(pattern: str, path: str = ".") -> str:
    """Files matching a name pattern, newest first.

    ``grep`` finds text and ``list_files`` shows one directory; neither answers "where are the
    test files" or "which components exist". Newest first because the file he wants is usually
    the one most recently touched.
    """
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.is_dir():
        raise WorkspaceError(f"{path} is not a folder to search in")
    wanted = str(pattern or "").strip() or "*"
    skip = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build"}
    found = [
        item
        for item in target.rglob(wanted)
        if item.is_file() and not (skip & set(item.relative_to(target).parts))
    ]
    if not found:
        return f"Nothing under {path} matches {wanted}"
    found.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    shown = found[:200]
    lines = [str(item.relative_to(root())) for item in shown]
    body = "\n".join(lines)
    if len(found) > len(shown):
        body += f"\n… [{len(found) - len(shown)} more; narrow the pattern]"
    return _clip(body)


def list_files(path: str = ".") -> str:
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.exists():
        raise WorkspaceError(f"there's no {path}")
    return _clip(run_command(f"ls -la {shlex.quote(str(target))}").output)


def list_dir(path: str = ".") -> list[dict]:
    """One level, structured, for the file browser — with modification times, because
    "what did he touch last" is how anyone finds work in progress."""
    target = Path(resolve(path))
    permissions.require_path("read", target, root())
    if not target.is_dir():
        raise WorkspaceError(f"cannot list {path}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda item: item.name):
        # His own bookkeeping is not his work; it would only be clutter in the browser.
        if child.name == INTERNAL_DIR:
            continue
        try:
            info = child.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": info.st_size if child.is_file() else 0,
                "modified": int(info.st_mtime),
            }
        )
    return entries


def make_dir(path: str) -> None:
    target = Path(resolve(path))
    permissions.require_path("write", target, root())
    _checkpoint_before_change("make_dir")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"cannot create {path}: {exc}") from None


def move(source: str, destination: str) -> None:
    src, dst = Path(resolve(source)), Path(resolve(destination))
    permissions.require_path("write", src, root())
    permissions.require_path("write", dst, root())
    _checkpoint_before_change("move")
    if dst.exists():
        raise WorkspaceError(f"{dst.name} already exists here.")
    try:
        src.rename(dst)
    except OSError as exc:
        raise WorkspaceError(f"cannot rename {source}: {exc}") from None


def remove(path: str) -> str:
    """Put a file or folder in the Trash. Refuses the workspace root itself.

    The Trash rather than ``unlink``, and this was ``unlink`` until it mattered. Asked to
    delete a file from the Desktop, he did it with ``rm`` and the file was simply gone —
    no prompt, and nothing to undo. The prompt is fixed separately, in
    :mod:`kith.services.permissions`; this fixes the other half, which is that a delete
    anyone can get wrong should not be the one operation on the machine with no way back.
    macOS has a recoverable delete and every other app on the machine uses it.

    Returns where it went, so the answer can say "in the Trash" and mean it.
    """
    target = Path(resolve(path))
    if target.resolve() == root().resolve():
        raise WorkspaceError("that's his whole folder — not that.")
    permissions.require_path("delete", target, root())
    _checkpoint_before_change("remove")
    if not target.exists() and not target.is_symlink():
        raise WorkspaceError(f"there is nothing at {path}.")
    return trash_path(target)


def trash_path(target: Path) -> str:
    """Move an absolute path to the Trash, and say where it went.

    Separate from :func:`remove` because two different callers need the Trash and only one of
    them is him. His file operations go through ``remove``, which resolves the path against
    his workspace and asks permission first. This one is for things the app itself owns and
    is putting away — an uninstalled skill folder — where there is no path to resolve and
    nobody to ask. Both end up recoverable, which is the part that matters.
    """
    bin_ = Path.home() / ".Trash"
    if not bin_.is_dir():
        # Not macOS, or a home directory without one. Say what happened rather than
        # reporting "moved to the Trash" about a file that is gone for good.
        try:
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        except OSError as exc:
            raise WorkspaceError(f"cannot delete {target.name}: {exc}") from None
        return "deleted permanently (this machine has no Trash)"

    destination = _free_name(bin_, target.name)
    try:
        # move, not rename: the Trash can be on a different volume from the file.
        shutil.move(str(target), str(destination))
    except OSError as exc:
        raise WorkspaceError(f"cannot move {target.name} to the Trash: {exc}") from None
    return f"in the Trash as {destination.name}"


def _free_name(folder: Path, name: str) -> Path:
    """``report.md``, then ``report 2.md`` — Finder's own convention, so a Trash full of
    same-named files reads the way people expect it to."""
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, dot, suffix = name.partition(".")
    for index in range(2, 1000):
        candidate = folder / f"{stem} {index}{dot}{suffix}"
        if not candidate.exists():
            return candidate
    raise WorkspaceError(f"the Trash already has a thousand things called {name}.")


def kind_of(path: str) -> str:
    """``"file"``, ``"dir"``, or ``""`` when there is nothing there."""
    target = Path(resolve(path))
    if target.is_dir():
        return "dir"
    return "file" if target.exists() else ""
