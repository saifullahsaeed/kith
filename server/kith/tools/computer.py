"""Working on the machine: files, search, and the shell."""

from __future__ import annotations

from pathlib import Path

from kith.domain.tooling import many
from kith.infra import workspace as sandbox
from kith.tools.params import INT, LIST_STR, STR
from kith.tools.registry import tool


def _shell(command: str) -> dict:
    """Run it, and say something if the command was carrying a credential.

    A remark rather than a refusal. Putting a key in a command is sometimes the only way to do
    a thing once, and a gate here would be one more thing to work around — but 136 commands in
    one conversation each carried the same Odoo key, and every one of them is now in a
    transcript on disk. The note points at the fix, which is to write the connection to a file
    once instead of retyping it.

    Said after the command has already run, for the same reason the post-edit note is: this is
    an observation about what happened, and it must not turn a working command into a failure.
    """
    from kith.domain import secrets

    result = sandbox.run_command(command)
    out = {"exitCode": result.exit_code, "output": result.output}
    if secrets.carries_a_secret(command):
        out["note"] = secrets.ADVICE
    return out


@tool(
    "shell",
    "Run a shell command on your person's computer, from your own folder. Returns combined "
    "stdout/stderr and the exit code. Their shell, their PATH, their installed programs — so "
    "build and run things, use the tools already there, poke around. You are NOT root and "
    "this is not a private box: anything destructive, and anything outside your folder, needs "
    "their yes and will be refused until they give it. To delete something use delete_file, "
    "not `rm` — it goes to the Trash and `rm` cannot be undone by anyone. "
    "This WAITS for the command to finish, so only use it for things that finish: a server or "
    "a watcher goes to start_process, and a test suite to run_tests. Don't reach for "
    "`nohup … &` — it is refused here, because it would hand you a pid and nothing else. "
    "Nothing can answer a prompt either, so pass the flag that avoids the question "
    "(`-y`, `--yes`, `--no-input`) rather than hoping. "
    "\n\n"
    "WRITE A SCRIPT INSTEAD OF PIPING ONE IN, whenever you are going to do the same kind of "
    "thing more than once, or anything that CHANGES a live system — a database, an accounting "
    "system, someone's account. Put it in `.kith/scratch/` and run it by name. "
    "`python3 - <<'PY'` runs the moment you send it: there is nothing to read first, nothing "
    "to check, and nothing to run again. A file can be looked at before it runs, fixed and "
    "re-run, and shown to them. "
    "Put the connection and credentials in that file ONCE and import it afterwards. Repeating "
    "them in every command retypes the same setup over and over and writes the key into the "
    "record every single time.",
    {"command": {**STR, "description": "The shell command to run."}},
    required=("command",),
)
def shell(path: Path, args: dict):
    return _shell(args["command"])


@tool(
    "read_file",
    "Read files from your computer (relative paths are under your own folder). `paths` is a "
    "LIST — when you know you need four files, ask for all four here rather than calling this "
    "four times: it is one round instead of four, at the same cost in tokens. Output is "
    "line-numbered. A screenshot or image is shown to you as a picture instead, so you can "
    "judge what you actually made. For anything big, don't read it whole — pass `symbol` to "
    "get one function or class by name, or grep to find the line you want and read a window "
    "with `offset`/`limit`, which window every file you asked for. `symbol` is the exception "
    "and needs a single path — a name can be defined in several of them and there would be no "
    "right answer. A read without a range returns the first 400 lines and tells you if there's "
    "more. "
    "If you are about to list four files you have not read before just to work out where "
    "something lives, that is an errand: delegate_subtask reads them somewhere else and hands "
    "you the answer, so your window ends up holding the answer instead of the files.",
    {
        "paths": {
            **LIST_STR,
            "description": "The files to read. Ask for every one you already know you need.",
        },
        "symbol": {
            **STR,
            "description": (
                "Read just this definition — 'server_for', or 'Manager.server_for' when several "
                "classes have one by that name. You do not need to know where it is, and it "
                "gets you the whole thing rather than a window that might stop halfway. If you "
                "also pass offset/limit they are used only if the name is not found."
            ),
        },
        "offset": {**INT, "description": "1-based line to start at (optional)."},
        "limit": {**INT, "description": "How many lines to return (optional; default 400)."},
    },
    required=("paths",),
)
def read_file(path: Path, args: dict):
    wanted_all = many(args, "paths", "path")
    if not wanted_all:
        return {"error": "Nothing to read — `paths` is a list of files to open."}
    if len(wanted_all) > 1:
        return _read_several(wanted_all, args)
    wanted = wanted_all[0]
    # A screenshot asked for by name should be looked at, not decoded as text. He was taking
    # Playwright captures at 1440 and 390 all day and never seeing one of them, because this
    # function read bytes as UTF-8 and reported "not text". Routed rather than given a separate
    # tool name so "read the screenshot" simply works.
    if Path(str(wanted)).suffix.lower() in sandbox._IMAGE_SUFFIXES:
        from kith.config import model_capabilities

        if not model_capabilities().get("images"):
            return {
                "path": str(wanted),
                "note": "That is an image and this model cannot see images. Check it another "
                "way — its dimensions, or the DOM you rendered it from.",
            }
        return sandbox.read_image(str(wanted))
    symbol = str(args.get("symbol") or "").strip()
    if symbol:
        return _read_symbol(wanted, symbol, args.get("offset"), args.get("limit"))
    return sandbox.read_file(wanted, args.get("offset"), args.get("limit"))


def _read_several(wanted: list[str], args: dict):
    """Several files as one result, each under its own heading.

    One string rather than a list of them, because that is what the single-file case already
    returns and every reader of a `read_file` result — the model, the transcript, the code
    block in the interface — already knows that shape. A second shape for the same tool would
    mean each of them growing a branch.

    ``offset`` and ``limit`` are applied to **each** file. All three used to be refused here on
    the grounds that a window into one file cannot mean four, and that was over-cautious for two
    of them: "lines 1 to 120 of each of these" is exactly what someone means by it, and it is
    the natural thing to try — which is what happened, and it cost a whole round at a hundred
    and twenty thousand tokens to be told no. The description made that worse by promising they
    "only apply when you ask for one", which reads as *ignored*, not *rejected*.

    ``symbol`` is still refused, and the difference is real rather than a compromise. A window
    is a position and means the same thing in every file; a symbol is a *name*, and a name that
    exists in three of the four has no single answer — returning the first one found is wrong in
    the way the old comment described, a correct-looking answer to a question nobody asked. Ask
    for the one file you want it from, or grep for where it is.
    """
    symbol = str(args.get("symbol") or "").strip()
    if symbol:
        return {
            "error": (
                f"`symbol` finds one definition by name and you asked for {len(wanted)} files — "
                f"if `{symbol}` is defined in more than one of them, any answer I gave would look "
                "right and be arbitrary. Ask for the file you want it from, or grep for it. "
                "`offset`/`limit` do work across several: they window each file."
            )
        }
    blocks = []
    pictures = []
    for one in wanted:
        try:
            body = read_file(
                Path(),
                {"paths": [one], "offset": args.get("offset"), "limit": args.get("limit")},
            )
        except Exception as exc:  # a bad path in a batch must not lose the good ones
            body = f"{type(exc).__name__}: {exc}"
        if isinstance(body, dict):
            # An image, or a per-file refusal.
            #
            # The image used to be flattened to its `note` here — "Look at the image below and
            # describe what you actually see" — and the data URI thrown away with the dict. So
            # the note was the only thing that survived, there was no image below it, and three
            # screenshots read in one call arrived as three copies of that sentence. He said so
            # himself, in the middle of a UI review: "the screenshots are saved but I need to
            # actually view them as images".
            #
            # It is the same bug `agent_loop._image_from` was written to fix for a single file,
            # reintroduced by the batch path — and `35-how-you-spend-a-round.md` tells him to
            # batch, so the persona routes him straight into it. Pictures come out here and
            # travel as real image parts; the text keeps a line saying one was in the batch.
            if _is_data_uri(body.get("image")):
                pictures.append({"path": one, "bytes": body.get("bytes"), "image": body["image"]})
                body = str(body.get("note") or "(an image, shown to you as a picture)")
            else:
                body = str(body.get("note") or body.get("error") or body)
        blocks.append(f"===== {one} =====\n{body}")
    text = "\n\n".join(blocks)
    # A plain string when there is nothing to look at, so every batch that reads code is shaped
    # exactly as it was before this.
    return {"text": text, "images": pictures} if pictures else text


def _is_data_uri(value) -> bool:
    return isinstance(value, str) and value.startswith("data:image/")


def _read_symbol(wanted: str, symbol: str, offset=None, limit=None) -> str:
    """One definition, read through the same reader as everything else.

    `locate` answers *where*, and `read_file` does the reading — so the permission check, the
    numbering, the output budget and the "there is more, ask with offset=" sentence are the
    ones already in use rather than a second set of them here.

    The header exists because a window with no context is disorienting: a method arriving as
    lines 180-210 of a file whose length he does not know could be most of it or a rounding
    error, and that changes whether reading the rest is worth a round.

    **`offset`/`limit` are a fallback, and used to be ignored.** Watched across 1,286 tool
    calls of real work: he passed a line range alongside the name on *all seventy-four* symbol
    reads, having worked one out from an outline first. That is not redundancy, it is hedging —
    and the hedge was worth honouring, because the four names that missed returned a refusal
    when he had already handed us a usable second answer.

    It is also evidence for the feature rather than against it. Asked for `ConnectionCard` he
    guessed lines 95-260; the definition is 101-361. His window would have stopped a hundred
    lines into a component, and silently — which is the failure the name exists to prevent.
    """
    from kith.engine.code import excerpt, outline
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

    target = Path(sandbox.resolve(wanted))
    permissions.require_path("read", target, sandbox.root())
    try:
        span = excerpt.locate(target, symbol)
    except (excerpt.ExcerptError, outline.OutlineError) as exc:
        # Every one of these messages names what to do instead — the definitions that do
        # exist, the ones that matched, or "grep it" — so it is kept whether or not there is
        # a window to fall back to.
        if offset is None and limit is None:
            return str(exc)
        window = sandbox.read_file(wanted, offset, limit)
        return f"{exc}\n\nReading the lines you asked for instead:\n{window}"
    body = sandbox.read_file(wanted, span.line, span.count)
    return f"{span.qualified} — {span.kind}, lines {span.line}-{span.end_line} of {span.of_lines}\n{body}"


@tool(
    "write_file",
    "Write (or overwrite) a file on your computer, creating parent folders as needed. For a "
    "file that already exists, use edit_files instead: rewriting a whole file to alter one "
    "line costs you the file again in output, silently loses anything you did not retype, "
    "and flattens its formatting a little more each time.",
    {"path": STR, "content": STR},
    required=("path", "content"),
)
def write_file(path: Path, args: dict):
    return _reporting_lost_definitions(
        args["path"], lambda: sandbox.write_file(args["path"], args.get("content") or "")
    )


def _reporting_lost_definitions(wanted: str | list[str], write):
    """Run a write, and say so if it removed definitions that were there before.

    Wired here rather than in `infra/workspace/files.py` because it cannot be wired there:
    `infra` is rank 2 and `engine` is rank 3, so the writer is *below* the parser and may not
    import it. That is the layering doing its job — the reader and writer of files stay free of
    anything that understands code, and the adapter above, which is allowed to know about both,
    joins them.

    Failure here is swallowed on purpose. This is a remark about a write that already
    succeeded, and a parser problem must not turn a good edit into a failed tool call.

    It runs on every write, so the cost was measured rather than assumed: about 2.5ms per edit
    on a 1,200-line Python file — one extra read and two parses — and nothing at all for a file
    that is not source, which `readable` rejects before parsing anything. Against a tool call
    inside a network round trip, that is not a number worth optimising.

    **Takes several paths because a batch is now the only way to edit.** When `edit_file` was
    its own tool this watched one file and `edit_files` watched none, so the check that catches
    "your `old` swallowed the three functions below it" was the one thing you lost by editing
    two files instead of one. The plural is the same walk, once per distinct path, and a dict
    result carries the remark in `note` rather than glued onto a string.
    """
    from kith.engine.code import verify

    paths = [wanted] if isinstance(wanted, str) else list(dict.fromkeys(wanted))
    before: dict[Path, tuple] = {}
    for one in paths:
        try:
            target = Path(sandbox.resolve(one))
            was = verify.readable(target)
        except Exception:
            continue
        if was is not None:
            before[target] = was

    result = write()

    gone: list[str] = []
    for target, was in before.items():
        try:
            now = verify.readable(target)
            if now is None:
                continue
            gone.extend(verify.lost(was[0], now[0], was[1]))
        except Exception:
            continue
    if not gone:
        return result

    shown = ", ".join(gone[:8]) + (f", … and {len(gone) - 8} more" if len(gone) > 8 else "")
    remark = f"[this removed {len(gone)} definition(s): {shown} — intended?]"
    if isinstance(result, str):
        return f"{result}\n\n{remark}"
    if isinstance(result, dict):
        return {**result, "note": " ".join(filter(None, (result.get("note"), remark)))}
    return result


#: The fields one edit is made of, shared by the batch's items and by the single-edit
#: shorthand beside it so the two cannot describe different rules.
_EDIT = {
    "path": STR,
    "old": {**STR, "description": "The exact text to replace, copied verbatim."},
    "new": {**STR, "description": "What to put in its place."},
    "replace_all": {
        "type": "boolean",
        "description": "Replace every occurrence in that file instead of failing on ambiguity.",
    },
}


@tool(
    "edit_files",
    "Change files by replacing exact pieces of text — one edit or twenty, in one call. `old` "
    "must appear EXACTLY once in its file, whitespace and indentation included: copy it "
    "verbatim from a read you did THIS turn, not from memory. If it appears more than once you "
    "are told how many, and you either include surrounding lines to pin down the one you mean "
    "or pass replace_all. All or nothing — a batch that fails leaves every file untouched and "
    "names the edit that was wrong. Edits to the same file apply in the order given, so a later "
    "one can build on an earlier one. You get back a combined diff: read it, that is how you "
    "check you changed what you intended.",
    {
        "edits": {
            "type": "array",
            "description": "The edits to apply, in order.",
            "items": {
                "type": "object",
                "properties": dict(_EDIT),
                "required": ["path", "old", "new"],
            },
        }
    },
    required=("edits",),
)
def edit_files(path: Path, args: dict):
    """One tool for one edit and for twenty, and it accepts either shape.

    There were two tools here, `edit_file` and `edit_files`, and the second was the first with
    a list around it. That is a parameter, not a capability: the model paid two schemas for it,
    had to choose between them every time it changed a file, and the choice carried a real
    trap — `edit_file` reported definitions its edit had swallowed and `edit_files` did not, so
    the safer tool was the one that could only do the smaller job.

    **The single-edit shape is accepted but not advertised, and that split is deliberate.**
    `path`/`old`/`new` at the top level is what a call to the retired `edit_file` arrives as
    once `aliases.RETIRED` has translated it, so the handler has to understand it. Declaring it
    in the schema as well would mean carrying the four fields twice — once inside `edits.items`
    and once beside it — to describe a shape nothing is ever shown. That duplication was the
    whole of the merge's token saving, spent on a second way to say the same thing.
    """
    raw = args.get("edits")
    if raw is None and args.get("path"):
        raw = [{key: args[key] for key in _EDIT if key in args}]
    if not isinstance(raw, list):
        return {"error": "pass `edits` as a list of {path, old, new}, or path/old/new for one"}
    edits = [one for one in raw if isinstance(one, dict)]
    if not edits:
        return {"error": "no edits given — pass at least one {path, old, new}"}
    return _reporting_lost_definitions(
        [str(one.get("path") or "") for one in edits],
        lambda: sandbox.edit_files(edits),
    )


@tool(
    "delete_file",
    "Put a file or folder in the Trash. Use this rather than `rm` in the shell — it goes "
    "to the Trash, so your person can get it back if you were wrong about which one they "
    "meant, and `rm` cannot be undone by anyone.",
    {"path": STR},
    required=("path",),
)
def delete_file(path: Path, args: dict):
    # This tool exists because it did not, and its absence had a cost: asked to delete a
    # file from the Desktop, the only route available was `shell` with `rm`, which is both
    # the least supervised path in the system and the one that destroys rather than
    # recovers. A first-class action means the permission check applies and the Trash does
    # the rest.
    return {"trashed": args["path"], "where": sandbox.remove(args["path"])}


@tool(
    "check_code",
    "Run whatever this project is checked with — TypeScript, ruff, or its build — and get back "
    "only what is wrong. Do this before you say something is done. It works out what to run "
    "from what is in the folder, so you do not have to know. If you changed something "
    'visual, look at a screenshot of it too — you can see images, and "the build passed" is not the same as "it looks right".',
    {"path": {**STR, "description": "The project folder (default: your whole folder)."}},
    required=(),
)
def check_code(path: Path, args: dict):
    return sandbox.check_code(args.get("path") or ".")


@tool(
    "glob",
    "Find files by name, newest first — 'where are the tests', 'which components exist'. Use "
    "`**/*.tsx` style patterns, or omit the pattern to just list a folder. grep searches inside "
    "files; this searches their names.",
    {
        "pattern": {
            **STR,
            "description": "A glob like '**/*.py' or 'test_*.py'. Omit to list the folder.",
        },
        "path": {**STR, "description": "Folder to search under (default: your whole folder)."},
    },
    required=(),
)
def glob(path: Path, args: dict):
    """Listing a directory is a glob with no pattern, so it is not a second tool.

    `list_files` was that second tool, and the pair cost a schema and a decision to answer one
    question — "what is in here" — in two ways that differ only in whether you already know
    what you are looking for. A model that wants the listing now omits the pattern; one that
    still sends `*` or `.` gets the listing too, because those are the same request typed by
    someone expecting the old tool.
    """
    pattern = str(args.get("pattern") or "").strip()
    where = args.get("path") or "."
    # `**` and `**/*` are deliberately NOT here. They ask for everything underneath, and
    # answering them with a one-level listing would drop the recursion silently — the failure
    # this whole merge is meant not to introduce.
    if pattern in ("", "*", ".", "./"):
        return sandbox.list_files(where)
    return sandbox.glob(pattern, where)


@tool(
    "changes",
    "What has changed in your folder. By default the diff of everything since your last "
    "`commit` — check it before you claim something is done: it catches the edit you made and "
    "forgot, and if it is longer than you expected you have work you have not recorded. Pass a "
    "path to narrow it, or `commits` for the last N recorded points instead, which is how you "
    "pick up where you left off.",
    {
        "path": {**STR, "description": "Optional file or folder to limit the diff to."},
        "commits": {
            **INT,
            "description": "Show the last N recorded commits instead of the uncommitted diff.",
        },
    },
    required=(),
)
def changes(path: Path, args: dict):
    """Uncommitted and committed are one question — "what changed here" — asked over two
    windows, so they are one tool with an argument rather than `changes` and `history`."""
    commits = args.get("commits")
    if commits:
        return sandbox.log(int(commits))
    return sandbox.diff(args.get("path") or None)


@tool(
    "publish",
    "Send your committed work to where the project came from, and bring down what other people "
    "have pushed. Do this after a commit somebody else needs, and before picking up work in a "
    "project other people touch — nothing arrives on its own, so a board that looks quiet may "
    "only be unfetched. 'check' only asks the remote what it has: it fetches without merging, "
    "so it is the one that is safe mid-job and the one that works with a dirty tree.",
    {
        "direction": {
            **STR,
            "enum": ["out", "in", "both", "check"],
            "description": "'out' pushes, 'in' pulls, 'both' pulls then pushes, "
            "'check' only fetches.",
        }
    },
    required=(),
)
def publish(path: Path, args: dict):
    """Pulling before pushing, when both are asked for: a push from behind is rejected, and
    being told to pull afterwards is a round spent learning what the order already knew.

    **'check' is here because reading the remote is separate from taking it in.** What the
    system prompt tells him about a project's folder is read locally — the commit count comes
    from refs the last fetch left on disk, so a repository nobody has fetched in a week reports
    "level with the remote" and is nothing of the kind. `workspace.standing` says how long it
    has been precisely because of that. `pull` cannot serve here: it refuses outright when the
    tree is dirty, which is the state somebody mid-job is always in, so "has anything arrived"
    would mean putting the work down first. It was its own tool, `check_remote`, and a fourth
    direction on the tool that already owns talking to the remote is the same thing without a
    second schema or a second decision.
    """
    which = str(args.get("direction") or "both").strip().lower()
    if which == "check":
        return {"remote": sandbox.fetch()}
    said = {}
    if which in ("in", "both"):
        said["in"] = sandbox.pull()
    if which in ("out", "both"):
        said["out"] = sandbox.push()
    return said


@tool(
    "commit",
    "Save a point in your folder's history, with a message saying what you did. Do this when "
    "something works — a feature finished, a bug fixed, a checker passing — not on every step "
    "and not mid-change. A commit is a claim that this is a coherent point worth coming back "
    "to, so make it when that is true: it is how you can undo a wrong turn, and how your "
    "person can review what you did while they were away. Check `changes` first if you are not "
    "sure what you are about to record.",
    {
        "message": {
            **STR,
            "description": "What this change does, in one line. Written for someone reading "
            "the history later, not for you now.",
        }
    },
    required=("message",),
)
def commit(path: Path, args: dict):
    summary = sandbox.commit_all(args.get("message") or "")
    if not summary:
        return {"committed": False, "note": "Nothing had changed, so there was nothing to record."}
    return {"committed": True, "changed": summary}


@tool(
    "grep",
    "Search files for a pattern (ripgrep) and get back matching lines with "
    "file:line numbers — your way to find the needle without loading whole "
    "haystacks into your head. Then read_file just that slice. Supports a glob "
    "filter like '*.py'. "
    "Grepping around a codebase you do not know, to build a picture rather than to find a "
    "line you already expect, is an errand: delegate_subtask does that searching without any "
    "of it landing here. "
    "If the thing you are looking for is the NAME of a function, class or method, use "
    "find_symbol instead: this matches characters, so it also returns the word in comments, "
    "in strings, inside longer names, and every unrelated variable that happens to share it — "
    "and you pay for reading all of that to find out which is which.",
    {
        "patterns": {
            **LIST_STR,
            "description": (
                "Regexes or literals to search for. A LIST — when you have three things to "
                "look for, put all three here rather than grepping three times."
            ),
        },
        "path": {**STR, "description": "File or directory to search (default: your whole folder)."},
        "glob": {**STR, "description": "Optional filename filter, e.g. '*.md'."},
    },
    required=("patterns",),
)
def grep(path: Path, args: dict):
    wanted = many(args, "patterns", "pattern")
    if not wanted:
        return {"error": "Nothing to search for — `patterns` is a list of things to look for."}
    where, only = args.get("path") or ".", args.get("glob")
    if len(wanted) == 1:
        return sandbox.grep(wanted[0], where, only)
    # Headed and joined, for the same reason `_read_several` joins: one shape out of one tool.
    return "\n\n".join(f"===== {one} =====\n{sandbox.grep(one, where, only)}" for one in wanted)
