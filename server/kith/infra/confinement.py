"""A boundary around a program that is not Kith, built rather than checked.

**Say what this protects before anything else, because the temptation is to overstate it.**

*Protects, kernel-enforced, measured on this machine:* the subprocess sees only the paths its
manifest named plus its own storage; the rest of `$HOME` returns EPERM for reads and writes. It
cannot read `~/.ssh` or `~/.aws`, cannot read Kith's own databases, cannot list `$HOME`. Children
it spawns inherit the confinement. It receives a **constructed** environment — six variables plus
whatever credentials were declared — instead of the launcher's whole one, so it never sees
`AWS_SECRET_ACCESS_KEY`, `GITHUB_TOKEN` or `SSH_AUTH_SOCK` by inheritance. It runs with an
explicit working directory in its own folder. All of that holds for the life of the process,
including work it does at `initialize`, on a timer, or in its stdout reader — code paths that
never enter `run_tool` and that no per-call gate could reach.

*Does not protect:* **where the data goes.** A plugin granted `~/Documents/Notes` with network
access can post every note anywhere, and every guarantee above is satisfied while it happens. Nor
*how much* — disk, CPU and file descriptors are unbounded within reach. Nor any single call's
arguments: a plugin granted a folder can be asked to delete a file in it and nothing prompts.

Every screen and every comment is written against that second paragraph. The review screen says
"it will be able to see these files, and could send them anywhere" — not "this plugin is
sandboxed".

## Why the profile is shaped the way it is

Three lines exist because something failed, and each carries its failure as a comment in the
generated file.

**`(allow default)` and a deny-list over `$HOME`, not an allow-list over the filesystem.**
`(deny default)` aborts real runtimes: a hand-built allowlist has to enumerate dyld, mach-lookup
and sysctl before a binary will even start, and `(import "bsd.sb")` denies `process-exec`
outright. So everything outside the denies stays readable — which is this design's principal
structural weakness and is stated on the review screen rather than buried here.

**`(allow file-read-metadata)` is required.** Denying `file-read*` over `$HOME` breaks npm with
`EPERM: operation not permitted, lstat '/Users/...'` — real runtimes stat their home even when
they never read it. Metadata only; `readdir` on `$HOME` still returns EPERM, which is the
property that matters.

**The working directory is load-bearing, not hygiene.** Measured here: `python3` fails under a
deny-`$HOME` profile whenever the *cwd* is inside the denied subtree, because `sys.path[0]` is
the cwd and `_path_importer_cache` raises `PermissionError` during import bootstrap.
`PYTHONNOUSERSITE=1`, `-S` and allow-listing user site-packages all fail to fix it; only an
explicit cwd does. `StdioServer.start()` had no `cwd` argument, so every Python, `uvx` and
`pipx` server would have broken under confinement with a traceback naming nothing relevant.

**A deny cannot be undone by a later allow, in either order.** Measured, after assuming the
opposite for one commit: `(deny file-read-data (subpath "$HOME"))` followed by
`(allow file-read* (subpath "$HOME/Notes"))` refuses the read, and swapping them changes
nothing. These rules are not last-match-wins.

That is not a detail — it broke the boundary for every case a boundary is *for*. Everything
worth granting lives inside `$HOME`, so broad-deny-then-narrow-allow left a plugin granted
`~/Documents/Notes` unable to read `~/Documents/Notes`, while passing every test that used a
temporary directory, because macOS puts those under `/private/var`, outside the deny. So each
deny carries its own exceptions — `require-all` over the closed root with a `require-not` per
granted path — and the tests grant a real folder under the real home directory, which is the
only place the two shapes can be told apart.

**The profile lives outside the plugin's write set.** A cage the prisoner can rewrite between
restarts is not a cage.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kith import settings

#: macOS's sandbox wrapper. The only enforcement in this design, and the reason `available()`
#: is carried into the grant signature rather than assumed.
SANDBOX_EXEC = "/usr/bin/sandbox-exec"

#: Where generated profiles live. Inside the data directory, which the profile itself denies,
#: with no re-allow for this folder.
PROFILES = "confinement"

#: Reach forms a manifest may use. Resolved by Kith, never used verbatim.
PLUGIN_STATE = "plugin:state"
PLUGIN_CACHE = "plugin:cache"
WORKSPACE = "workspace"

_available: bool | None = None


class ConfinementError(RuntimeError):
    """A boundary could not be built, with a reason worth showing someone."""


def available() -> bool:
    """Can this machine confine a subprocess at all?

    Cached for the process. `False` is carried into the grant signature as `open` rather than
    being papered over, so trust given under a boundary does not silently survive the boundary's
    disappearance — an OS that loses `sandbox-exec` re-asks instead of quietly running unconfined.
    """
    global _available
    if _available is None:
        _available = os.access(SANDBOX_EXEC, os.X_OK)
    return _available


@dataclass(frozen=True)
class Resolved:
    """A reach, with every path resolved to somewhere real."""

    read: tuple[Path, ...] = ()
    write: tuple[Path, ...] = ()
    network: bool = True

    def public(self) -> dict:
        return {
            "read": [str(p) for p in self.read],
            "write": [str(p) for p in self.write],
            "network": self.network,
        }


def home_for(plugin_id: str) -> Path:
    """A plugin's own directory: its `HOME`, its working directory, its writable storage."""
    return settings.plugins_dir() / plugin_id / ".home"


def resolve(reach, plugin_id: str, *, workspace_root: Path | None = None) -> Resolved:
    """Turn a manifest's reach into real paths, refusing what must never be granted this way.

    The refusals are not a safety net over the review screen — they are what stops a one-click
    manifest grant from being a strictly *wider* door than the per-path prompt it would bypass.
    A plugin that wants `~/.ssh` does not get to ask for it in a JSON file.
    """
    home = home_for(plugin_id)
    read = tuple(_paths(getattr(reach, "read", ()), plugin_id, home, workspace_root))
    write = tuple(_paths(getattr(reach, "write", ()), plugin_id, home, workspace_root))
    return Resolved(read=read, write=write, network=bool(getattr(reach, "network", True)))


def _paths(declared, plugin_id: str, home: Path, workspace_root: Path | None) -> list[Path]:
    out: list[Path] = []
    for raw in declared or ():
        text = str(raw).strip()
        if not text:
            continue
        # Refused before resolution, because these characters would need escaping into a
        # profile and refusing is cheaper than escaping with no legitimate use to weigh it
        # against.
        if any(ch in text for ch in ('"', "\\", "\n", "\r", "\0")):
            raise ConfinementError(f"{text!r} is not a path a plugin may ask for.")
        if text == PLUGIN_STATE:
            out.append(home / "state")
            continue
        if text == PLUGIN_CACHE:
            out.append(home / "cache")
            continue
        if text == WORKSPACE:
            if workspace_root is not None:
                out.append(workspace_root.resolve())
            continue
        target = Path(text).expanduser().resolve()
        _refuse_if_forbidden(target, text)
        out.append(target)
    return out


def _refuse_if_forbidden(target: Path, spelled: str) -> None:
    """What a manifest may never reach for, whatever a person would click.

    Everything here is a place where a granted path would be *broader* than what it bypasses:
    the sensitive directories the permission layer refuses outright, the roots the workspace
    picker refuses to accept, Kith's own data, and the skills folder — which decides what he
    knows how to do.
    """
    from kith.infra import permissions

    home = Path.home().resolve()
    if target == home or target == Path("/"):
        raise ConfinementError(
            f"{spelled!r} is your whole home folder. A plugin cannot ask for that in a manifest."
        )
    if permissions._is_sensitive(target):
        raise ConfinementError(f"{spelled!r} is somewhere sensitive, and cannot be granted this way.")
    data = settings.DATA_DIR.resolve()
    if target == data or data in target.parents:
        plugins = settings.plugins_dir().resolve()
        if not (plugins in target.parents or target == plugins):
            raise ConfinementError(f"{spelled!r} is inside Kith's own storage.")
    skills = settings.skills_dir().resolve()
    if target == skills or skills in target.parents:
        raise ConfinementError(f"{spelled!r} is your skills folder, which decides what he knows how to do.")
    for refused in ("Desktop", "Documents", "Downloads", "Library", "Movies", "Music", "Pictures"):
        if target == home / refused:
            raise ConfinementError(
                f"{spelled!r} is all of your {refused}. Name the folder inside it that this "
                f"plugin actually needs."
            )


def runtime_root(command: str) -> Path | None:
    """The smallest tree that has to be readable for `command` to start at all, or None.

    **A program that cannot read itself cannot run**, and this is not a loosening for
    convenience — it is a precondition, found by measurement. A Python interpreter inside a
    virtualenv reads `pyvenv.cfg` from the environment root before any of its own code runs, so
    a venv under `$HOME` dies in `init_import_site` with
    `PermissionError: ... '/Users/x/project/.venv/pyvenv.cfg'` — a failure whose message names
    a file nobody was trying to open.

    Only bites for a runtime under `$HOME`; `/usr/bin/python3` and `/opt/homebrew/bin/node` are
    outside the deny and unaffected. `uvx`, `pipx` and any project virtualenv are not.

    The scope is the smallest thing that works: `<env>/bin/python` grants `<env>`, because that
    is the unit a virtualenv actually is, and anything else grants the containing directory. It
    is **read** only, and it is the binary the person approved running in the first place.
    """
    try:
        # `absolute()` and `normpath`, deliberately **not** `resolve()`. A virtualenv's
        # `bin/python` is a symlink to the real interpreter, which usually lives outside `$HOME`
        # — so resolving it walks straight out of the environment and this function returns
        # None for the exact case it exists to handle. What has to be readable is the path as
        # *invoked*, because that is where the interpreter looks for `pyvenv.cfg`. The symlink's
        # target is outside the deny already and needs nothing.
        invoked = Path(os.path.normpath(Path(command).expanduser().absolute()))
    except (OSError, ValueError):
        return None
    if not invoked.is_file():
        return None
    home = Path.home().resolve()
    if home not in invoked.parents:
        return None  # outside the deny already; granting it would widen for nothing
    parent = invoked.parent
    root = parent.parent if parent.name in ("bin", "Scripts") else parent
    # Never the home folder itself, however oddly a runtime is laid out — that would silently
    # undo the whole profile to make one badly-placed binary start.
    return None if root in (home, home.parent, Path("/")) else root


def profile(resolved: Resolved, home: Path, runtime: str = "") -> str:
    """The `sandbox-exec` profile for one boundary.

    **A deny carries its own exceptions, because a later allow cannot undo it.** Measured on
    this machine: with `(deny file-read-data (subpath "$HOME"))` followed by
    `(allow file-read* (subpath "$HOME/Notes"))`, the read is refused — and swapping the order
    changes nothing. Rules here are not last-match-wins, which is the opposite of what the
    obvious reading suggests and the opposite of what this file assumed for one commit.

    The consequence was not subtle: a plugin granted `~/Documents/Notes` could not read
    `~/Documents/Notes`. Everything a boundary is *for* lives inside `$HOME`, so the
    broad-deny-then-narrow-allow shape was wrong for every case that matters and right only for
    the ones that need no grant at all.

    So each deny is written as `require-all` over the denied root with a `require-not` per
    granted path. Verified: the granted subpath reads and writes, `~/.ssh` is refused, `ls
    $HOME` is refused, and a write elsewhere under `$HOME` is refused.

    The rest of the shape, and why:

    **`(allow default)` and a deny-list, not an allow-list.** `(deny default)` aborts real
    runtimes before `main()` — a hand-built allowlist has to enumerate dyld, mach-lookup and
    sysctl first, and `(import "bsd.sb")` denies `process-exec` outright. So everything outside
    these denies stays readable, which is this design's principal structural weakness and is
    said on the review screen rather than buried here.

    **`(allow file-read-metadata)`** is required: denying `file-read*` over `$HOME` breaks npm
    with `EPERM: ... lstat '/Users/...'`, because real runtimes stat their home even when they
    never read it. Metadata only — `readdir` on `$HOME` still returns EPERM, which is the
    property that matters.
    """
    real_home = Path.home().resolve()
    data = settings.DATA_DIR.resolve()

    tree = runtime_root(runtime) if runtime else None
    # Everything this program may read, and everything it may write. The write set is a subset
    # of the read set by construction — a program that may write a file may look at it.
    writable = [home, *resolved.write]
    readable = [*writable, *resolved.read] + ([tree] if tree is not None else [])
    # The roots the boundary closes. A granted path may sit inside any of them, which is
    # exactly why each deny carries its exceptions rather than being followed by an allow.
    closed = [real_home, data, Path("/Volumes")]

    lines = [
        "(version 1)",
        "",
        ";; A deny-list over the private directories, not an allow-list over the filesystem.",
        ";; `(deny default)` aborts real runtimes before they reach main(): a hand-built",
        ";; allowlist has to enumerate dyld, mach-lookup and sysctl first, and (import",
        ';; "bsd.sb") denies process-exec outright. Everything outside these denies stays',
        ";; readable, which is this design's principal weakness and is stated on the review",
        ";; screen rather than hidden here.",
        "(allow default)",
        "",
        ";; Each deny carries its exceptions. Measured: a later `allow` does NOT override an",
        ";; earlier `deny` here, in either order — so the broad-deny-then-narrow-allow shape",
        ";; would leave a plugin unable to read the very folder it was granted.",
    ]
    for root in closed:
        lines.append("")
        lines.append(_deny("file-read-data", root, readable))
        lines.append(_deny("file-write*", root, writable))

    lines += [
        "",
        ";; Measured: denying file-read* over $HOME breaks npm with",
        ";; `EPERM: operation not permitted, lstat '/Users/...'` — real runtimes stat their",
        ";; home even when they never read it. Metadata only; readdir on $HOME still EPERMs.",
        "(allow file-read-metadata)",
    ]
    if not resolved.network:
        lines += ["", ";; This plugin declared that it needs no network.", "(deny network*)"]
    return "\n".join(lines) + "\n"


def _deny(what: str, root: Path, exceptions) -> str:
    """One deny over `root`, carved out for every path granted inside it.

    An exception naming somewhere outside `root` is a no-op rather than a mistake, so the list
    is not filtered — someone comparing this profile against the review screen should see the
    same paths in both, and silently dropping the ones that happen to sit elsewhere would make
    that comparison lie.
    """
    carved = [f'(require-not (subpath "{path}"))' for path in exceptions]
    if not carved:
        return f'(deny {what} (subpath "{root}"))'
    inner = "\n           ".join(carved)
    return f'(deny {what}\n  (require-all (subpath "{root}")\n           {inner}))'


def write_profile(plugin_id: str, resolved: Resolved, runtime: str = "") -> Path:
    """Generate the profile and prove it compiles, before anyone depends on it.

    Compile-checked by running it over `/usr/bin/true`, so a malformed profile fails at the
    review with the person present rather than at first start with nobody watching — where it
    would look like the server being broken.
    """
    home = home_for(plugin_id)
    for folder in (home, home / "state", home / "cache", home / "tmp"):
        folder.mkdir(parents=True, exist_ok=True)

    place = settings.DATA_DIR / PROFILES
    place.mkdir(parents=True, exist_ok=True)
    path = place / f"{plugin_id}.sb"
    path.write_text(profile(resolved, home, runtime))

    if available():
        proof = subprocess.run(
            [SANDBOX_EXEC, "-f", str(path), "/usr/bin/true"], capture_output=True, timeout=20
        )
        if proof.returncode != 0:
            detail = (proof.stderr or b"").decode(errors="replace").strip().splitlines()
            raise ConfinementError(
                f"the boundary for {plugin_id!r} would not compile: "
                f"{detail[0] if detail else 'sandbox-exec refused it'}"
            )
    return path


def confine(argv: list[str], plugin_id: str) -> list[str]:
    """Wrap a command so it runs inside its boundary, or hand it back unchanged.

    Unchanged when this machine cannot confine — and that fact is carried into the grant
    signature as `open`, never swallowed. A person who granted a confined plugin has not
    granted an unconfined one.
    """
    path = settings.DATA_DIR / PROFILES / f"{plugin_id}.sb"
    if not available() or not path.is_file():
        return list(argv)
    return [SANDBOX_EXEC, "-f", str(path), *argv]


def environment(plugin_id: str, declared: dict[str, str]) -> dict[str, str]:
    """A constructed environment, not an inherited one.

    **This is the highest-value line in the file and it is platform-independent** — it applies
    on Linux and in a container where `available()` is False. The existing code hands every MCP
    server `{**os.environ}`: the person's whole environment, AWS keys and `GITHUB_TOKEN` and
    `SSH_AUTH_SOCK` included, to a subprocess they installed to read their notes.

    `npm_config_cache` is not a nicety. A confined process that takes EPERM part-way through an
    install corrupts the shared `~/.npm` cache badly enough to break the person's own
    *unconfined* `npx` afterwards, which is a failure that outlives the plugin.
    """
    home = home_for(plugin_id)
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home / "tmp"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "npm_config_cache": str(home / "cache" / "npm"),
        "XDG_CACHE_HOME": str(home / "cache"),
        **{str(k): str(v) for k, v in (declared or {}).items()},
    }


def forget(plugin_id: str) -> None:
    """Drop a plugin's profile. Its storage is not touched — that is the person's data."""
    path = settings.DATA_DIR / PROFILES / f"{plugin_id}.sb"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
