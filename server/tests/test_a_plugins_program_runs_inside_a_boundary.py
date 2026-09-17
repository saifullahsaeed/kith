"""The boundary a plugin's program runs inside, measured against the real kernel.

**This is the only enforcement in the whole plugin design**, so it is the one thing that has to
be checked rather than argued. Everything else — the manifest, the grants, the review screen —
is bookkeeping around this: if the kernel does not refuse, nothing does.

So these tests spawn real processes under real profiles. They are slower than the rest of the
suite and that is the correct trade; a mocked `sandbox-exec` would assert that this file's own
string formatting is unchanged, which is not the property anybody cares about.

Two things are tested as carefully as the protection itself:

* **What it does not protect.** A confined plugin can still send everything it can read
  anywhere it likes. The review screen says so in those words, and the test below pins that the
  network is open by default so nobody can quietly come to believe otherwise.
* **That real runtimes still work.** A boundary that breaks `npx` and `uvx` is a boundary
  nobody will leave switched on, which protects nothing. The cwd test is the one that matters
  and it is the one that was found by measurement rather than by design.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from kith.infra import confinement

pytestmark = pytest.mark.skipif(
    not confinement.available(), reason="this machine cannot confine a subprocess"
)


@dataclass(frozen=True)
class Reach:
    """The shape `domain.plugins.Reach` has, without importing a plugin to get one."""

    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ("plugin:state",)
    network: bool = True


@pytest.fixture
def confined(tmp_path: Path, monkeypatch):
    """A plugin whose boundary is built and compiled, ready to run something inside."""
    monkeypatch.setenv("KITH_DATA_DIR", str(tmp_path / "data"))
    # **Under the data directory, the way production lays it out.** `plugins_dir()` defaults to
    # `DATA_DIR / "plugins"` and the profile closes `DATA_DIR` — so a plugin's folder is inside
    # a denied root and is readable only through the exception. A fixture that put it elsewhere
    # would place it outside every deny, and the write test below would pass by testing nothing,
    # which is exactly what it did first time round.
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "data" / "plugins"))
    monkeypatch.setattr("kith.settings.DATA_DIR", tmp_path / "data")

    readable = tmp_path / "readable"
    readable.mkdir()
    (readable / "note.txt").write_text("the plugin may read this")

    def build(network: bool = True, read: tuple[str, ...] = (), runtime: str = "") -> Path:
        resolved = confinement.resolve(Reach(read=read or (str(readable),), network=network), "probe")
        # The runtime defaults to this suite's own interpreter, which lives in a virtualenv
        # under `$HOME` — exactly the case `runtime_root` exists for, and the reason these
        # tests would otherwise pass only on a machine whose python is in `/usr/bin`.
        confinement.write_profile("probe", resolved, runtime=runtime or sys.executable)
        return readable

    return build


@pytest.fixture
def inside_home():
    """A real folder under the real home directory, removed afterwards.

    Unavoidable, and the reason is the point: `tmp_path` is under `/private/var` on macOS,
    which is outside everything this profile denies. A boundary tested only there is a boundary
    tested only where it does nothing.
    """
    import shutil
    import uuid

    place = Path.home() / f".kith-confinement-test-{uuid.uuid4().hex[:8]}" / "notes"
    place.mkdir(parents=True)
    (place / "note.txt").write_text("granted")
    try:
        yield place
    finally:
        shutil.rmtree(place.parent, ignore_errors=True)


def under(argv: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run something as a plugin's program would be run — confined, constructed env, own cwd."""
    home = confinement.home_for("probe")
    home.mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    return subprocess.run(
        confinement.confine(argv, "probe"),
        env=confinement.environment("probe", {}),
        cwd=str(cwd or home),
        capture_output=True,
        text=True,
        timeout=60,
    )


# --------------------------------------------------------------------------- #
# What it protects
# --------------------------------------------------------------------------- #


@pytest.fixture
def a_secret_in_your_home():
    """A file in the real home directory that the boundary must refuse.

    **The tests below need the file to exist, and that is not a detail.** They assert the kernel
    said *operation not permitted*; a path that is simply absent fails with *no such file or
    directory* instead, which is `cat` reporting the filesystem rather than the sandbox refusing
    anything. Both readings are a non-zero exit, so a test written against the exit code alone
    would pass on a machine with no `~/.ssh` while proving nothing at all.

    That is exactly what happened: every developer's Mac has an `~/.ssh/config`, a fresh CI runner
    has no `~/.ssh` whatsoever, so this passed locally and had never once passed in CI.

    Creates nothing it does not have to, and removes only what it created — on a machine where the
    file is real this fixture touches nothing.
    """
    secret = Path.home() / ".ssh" / "config"
    if secret.exists():
        yield secret
        return

    made_dir = not secret.parent.exists()
    secret.parent.mkdir(mode=0o700, exist_ok=True)
    secret.write_text("# placeholder, so the kernel has something to refuse\n")
    try:
        yield secret
    finally:
        secret.unlink(missing_ok=True)
        if made_dir:
            secret.parent.rmdir()


def test_it_cannot_read_your_keys(confined, a_secret_in_your_home):
    confined()
    answer = under(["/bin/cat", str(a_secret_in_your_home)])

    assert answer.returncode != 0
    assert "operation not permitted" in answer.stderr.lower()


def test_it_cannot_list_your_home_folder(confined):
    confined()
    answer = under(["/bin/ls", str(Path.home())])

    assert answer.returncode != 0
    assert "operation not permitted" in answer.stderr.lower()


def test_it_cannot_write_outside_what_it_was_granted(confined):
    confined()
    target = Path.home() / "kith-confinement-should-never-exist.txt"
    under(["/bin/sh", "-c", f"echo nope > {target}"])

    assert not target.exists()


def test_a_child_it_spawns_inherits_the_boundary(confined, a_secret_in_your_home):
    """The property a per-call gate could never have. Whatever the server shells out to is
    inside the same walls, including work it does at `initialize` or on a timer."""
    confined()
    answer = under(["/bin/sh", "-c", f'cat "{a_secret_in_your_home}"'])

    assert "operation not permitted" in (answer.stdout + answer.stderr).lower()


def test_it_can_read_what_it_asked_for(confined):
    readable = confined()
    answer = under(["/bin/cat", str(readable / "note.txt")])

    assert answer.returncode == 0
    assert "may read this" in answer.stdout


def test_it_can_read_a_granted_folder_that_is_inside_your_home(inside_home, confined):
    """**The case the boundary exists for, and the one the tests above cannot reach.**

    Every other path here is `tmp_path`, which macOS puts under `/private/var` — outside the
    deny, so those tests pass whether or not a grant inside `$HOME` works. It did not. A `deny`
    is not overridable by a later `allow` in either order, so the first shape of this profile
    left a plugin granted `~/Documents/Notes` unable to read `~/Documents/Notes` — wrong for
    every case that needs a grant, and right only for the ones that need none.

    So this one grants a real folder under the real home directory, which is the only way to
    tell the two shapes apart.
    """
    confined(read=(str(inside_home),))

    reading = under(["/bin/cat", str(inside_home / "note.txt")])
    assert reading.returncode == 0, reading.stderr
    assert "granted" in reading.stdout

    # And the rest of the home folder is still shut, which is the half that makes it a boundary.
    assert under(["/bin/ls", str(Path.home())]).returncode != 0


def test_a_granted_folder_inside_your_home_is_writable_when_asked(inside_home, tmp_path, monkeypatch):
    monkeypatch.setenv("KITH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setattr("kith.settings.DATA_DIR", tmp_path / "data")
    resolved = confinement.resolve(Reach(write=(str(inside_home),)), "probe")
    confinement.write_profile("probe", resolved, runtime=sys.executable)

    answer = under(["/bin/sh", "-c", f'echo written > "{inside_home}/w.txt"'])

    assert answer.returncode == 0, answer.stderr
    assert (inside_home / "w.txt").read_text().strip() == "written"
    # Its sibling is not.
    assert under(["/bin/sh", "-c", f'echo no > "{inside_home.parent}/sibling.txt"']).returncode != 0


def test_it_can_write_its_own_storage(confined):
    confined()
    home = confinement.home_for("probe")
    answer = under(["/bin/sh", "-c", f'echo kept > "{home}/state/x.txt"'])

    assert answer.returncode == 0
    assert (home / "state" / "x.txt").read_text().strip() == "kept"


def test_a_server_can_read_its_own_code(confined, tmp_path: Path):
    """**A program that cannot read itself cannot run**, and this was broken.

    The profile denies the whole data directory, and a plugin's server script lives inside it —
    so `node server/index.mjs` could not read `server/index.mjs`. It arrived as a
    module-resolution error naming a file that is plainly there, and it never surfaced until a
    plugin actually bundled a server, because the examples before it ran no program at all.
    """
    confined()
    folder = confinement.folder_for("probe")
    (folder / "server").mkdir(parents=True, exist_ok=True)
    (folder / "server" / "index.mjs").write_text("console.log('server ok')")

    answer = under(["/bin/cat", str(folder / "server" / "index.mjs")])

    assert answer.returncode == 0, answer.stderr
    assert "server ok" in answer.stdout


def test_but_a_server_cannot_rewrite_its_own_code(confined):
    """Read, not write. A plugin that can edit its own program between restarts is a plugin
    whose reviewed code and running code are different things — the same argument as the
    profile living outside the write set."""
    confined()
    folder = confinement.folder_for("probe")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "kith.plugin.json"
    target.write_text('{"manifest": 1}')

    answer = under(["/bin/sh", "-c", f'echo tampered > "{target}"'])

    assert answer.returncode != 0
    assert target.read_text() == '{"manifest": 1}'


def test_it_cannot_rewrite_its_own_cage(confined):
    """A profile the prisoner can edit between restarts is not a profile."""
    confined()
    profile = confinement.PROFILES
    from kith import settings

    target = settings.DATA_DIR / profile / "probe.sb"
    answer = under(["/bin/sh", "-c", f'echo "(allow default)" > "{target}"'])

    assert answer.returncode != 0
    assert "(deny file-read-data" in target.read_text()


def test_the_environment_is_constructed_rather_than_inherited(monkeypatch, tmp_path: Path):
    """The highest-value line in the module, and the one that works on every platform.

    The existing code hands every server `{**os.environ}` — the person's whole environment,
    to a subprocess they installed to read their notes.
    """
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "should-not-travel")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/should-not-travel")

    built = confinement.environment("probe", {"NOTES_TOKEN": "given"})

    assert "AWS_SECRET_ACCESS_KEY" not in built
    assert "SSH_AUTH_SOCK" not in built
    # Only what it needs, plus what was declared and typed in by a person.
    assert built["NOTES_TOKEN"] == "given"
    assert set(built) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "npm_config_cache",
        "XDG_CACHE_HOME",
        "NOTES_TOKEN",
    }


def test_the_network_can_be_shut_off(confined):
    confined(network=False)
    answer = under(["/usr/bin/curl", "-s", "-m", "5", "https://example.com"])

    assert answer.returncode != 0


# --------------------------------------------------------------------------- #
# What it does NOT protect — pinned, so nobody comes to believe otherwise
# --------------------------------------------------------------------------- #


def test_by_default_it_can_still_send_everything_it_reads_anywhere(confined):
    """A plugin granted a folder and left with network access can post every file in it.

    Every guarantee above is satisfied while that happens. This is the sentence the review
    screen carries in the app's own voice — "it will be able to see these files, and could send
    them anywhere" — and it is asserted here so the claim and the behaviour cannot drift.
    """
    readable = confined(network=True)
    resolved = confinement.resolve(Reach(read=(str(readable),), network=True), "probe")

    assert resolved.network is True
    assert "(deny network*)" not in confinement.profile(resolved, confinement.home_for("probe"))


# --------------------------------------------------------------------------- #
# Real runtimes still work, which is what keeps it switched on
# --------------------------------------------------------------------------- #


def test_a_python_server_starts_from_its_own_directory(confined):
    """**The finding that changed the design, and the reason `cwd` is not hygiene.**

    `sys.path[0]` is the working directory, so a Python process whose cwd is inside the denied
    subtree raises `PermissionError` in `_path_importer_cache` during import bootstrap — before
    any of its own code runs. `PYTHONNOUSERSITE=1`, `-S` and allow-listing user site-packages
    all fail to fix it. Only the cwd does, and this process runs from inside the repository,
    which is under `$HOME`.
    """
    confined()
    working = under([sys.executable, "-c", "print('python ok')"])
    assert working.returncode == 0, working.stderr
    assert "python ok" in working.stdout

    # The same interpreter, the same profile, started from where Kith itself runs.
    broken = under([sys.executable, "-c", "print('python ok')"], cwd=Path.cwd())
    assert broken.returncode != 0
    assert "PermissionError" in broken.stderr


def test_a_node_server_starts(confined):
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    confined()

    answer = under([node, "-e", "console.log('node ok')"])
    assert answer.returncode == 0, answer.stderr
    assert "node ok" in answer.stdout


# --------------------------------------------------------------------------- #
# What a manifest may never ask for
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "asked",
    ["~", "~/.ssh", "~/Documents", "~/Library/Keychains", "/"],
    ids=["home", "keys", "all-documents", "keychain", "root"],
)
def test_a_manifest_cannot_reach_for_these(asked, tmp_path: Path, monkeypatch):
    """Not a safety net over the review screen — this is what stops a one-click manifest grant
    from being a strictly *wider* door than the per-path prompt it would bypass."""
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))

    with pytest.raises(confinement.ConfinementError):
        confinement.resolve(Reach(read=(asked,)), "probe")


def test_a_manifest_cannot_reach_into_kiths_own_storage(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setattr("kith.settings.DATA_DIR", tmp_path / "data")

    with pytest.raises(confinement.ConfinementError, match="Kith's own storage"):
        confinement.resolve(Reach(read=(str(tmp_path / "data"),)), "probe")


def test_a_boundary_that_would_not_compile_is_refused_at_install(tmp_path: Path, monkeypatch):
    """With the person present, rather than at first start where it looks like a broken server."""
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setattr("kith.settings.DATA_DIR", tmp_path / "data")

    resolved = confinement.resolve(Reach(), "probe")
    path = confinement.write_profile("probe", resolved)
    path.write_text("(version 1)\n(this is not a profile")

    with pytest.raises(confinement.ConfinementError, match="would not compile"):
        # Rebuilt from the broken text on disk rather than from `resolved`, which is what a
        # hand-edited profile would look like.
        monkeypatch.setattr(confinement, "profile", lambda *a: path.read_text())
        confinement.write_profile("probe", resolved)


def test_an_unconfinable_machine_says_so_rather_than_pretending(monkeypatch):
    """`confine` hands the command back unchanged — and the *signature* records `open`, so
    consent given for a confined program does not survive the confinement disappearing."""
    monkeypatch.setattr(confinement, "_available", False)

    assert confinement.confine(["node", "x.js"], "probe") == ["node", "x.js"]
