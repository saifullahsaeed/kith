"""His computer — which is now your computer.

This replaces the Docker sandbox. The container was a real machine of his own with root
inside it and no way out, and the trade it made was total: nothing of yours was reachable,
and neither was anything of yours he might have been useful with. He could not open a file
you pointed at, could not use the tools you already have installed, and everything he made
had to be copied out through ``docker cp`` before you could see it. Dropping it also drops
a dependency nobody should have to install to run a desktop app.

So he works here instead, in one folder you choose (``~/Kith`` by default), with your
shell, your PATH, and your installed programs. What used to be enforced by a container
boundary is now enforced by :mod:`kith.infra.permissions`: inside the workspace he is
unrestricted, and outside it — or anything destructive anywhere — needs your yes.

Two things worth knowing:

**Every path goes through :func:`resolve`, and every command through the permission
check.** There is no second way in. A relative path anchors in the workspace; an absolute
one is honoured but gated, because "read /Users/you/Documents/thing.pdf" is a reasonable
request and should be answerable with a click rather than impossible.

**Commands run under a login shell** (``bash -lc``) with the workspace as the working
directory. That is what makes "use the tools already on this machine" true — his ``python3``
is your python3, his ``git`` is your git — and it is the whole point of moving him here.

---

One file of 1,748 lines until it was six, along seams its own banner comments had already
drawn — History, Checkpoints, Where we are, Doing things, The web. The dependencies run one
way, which is what made the split safe rather than merely tidy: `base` knows nothing, `paths`
knows `base`, `git` knows `paths`, and so on up to `files`, which is the only one large enough
to still be worth arguing about and is kept whole because reading a file, changing it and
listing the folder it sits in are one job from three angles.

Everything is re-exported here. Sixty-eight call sites say `workspace.read_file` and none of
them has to know there is a package behind the name.
"""

from __future__ import annotations

from .base import ExecResult, SandboxError, WorkspaceError
from .checkpoints import restore_to_sha

# Underscored and re-exported anyway, which is the honest description of them: `tools/computer`
# asks whether a suffix is one the model can see, and `engine/run/processes` wants the same
# non-interactive environment a foreground command gets. Both were reaching into this module
# before it was a package, so the name was never really private — only unadvertised.
from .files import _IMAGE_SUFFIXES as _IMAGE_SUFFIXES
from .files import (
    check_code,
    edit_file,
    edit_files,
    glob,
    grep,
    kind_of,
    list_dir,
    list_files,
    make_dir,
    media_file,
    move,
    read_file,
    read_image,
    read_raw,
    remove,
    trash_path,
    write_file,
)
from .git import commit_all, diff, ensure_repo, has_git, log
from .paths import (
    DEFAULT_ROOT,
    INTERNAL_DIR,
    ROOT_KEY,
    base_dir,
    configured_root,
    internal,
    locate,
    resolve,
    root,
    set_root,
    status,
)
from .shell import _NON_INTERACTIVE as _NON_INTERACTIVE
from .shell import run_command
from .web import browse_page, fetch_url

__all__ = [
    "DEFAULT_ROOT",
    "INTERNAL_DIR",
    "ROOT_KEY",
    "ExecResult",
    "SandboxError",
    "WorkspaceError",
    "base_dir",
    "browse_page",
    "check_code",
    "commit_all",
    "configured_root",
    "diff",
    "edit_file",
    "edit_files",
    "ensure_repo",
    "fetch_url",
    "glob",
    "grep",
    "has_git",
    "internal",
    "kind_of",
    "list_dir",
    "list_files",
    "locate",
    "log",
    "make_dir",
    "media_file",
    "move",
    "read_file",
    "read_image",
    "read_raw",
    "remove",
    "resolve",
    "restore_to_sha",
    "root",
    "run_command",
    "set_root",
    "status",
    "trash_path",
    "write_file",
]
