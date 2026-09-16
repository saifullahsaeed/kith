"""Where a plugin comes from, when it does not come from a folder on this machine.

**One seam, and everything downstream still works on folders.** `inspect` parses a directory and
`install` copies one; neither learns what GitHub is. A reference resolves to a directory here,
and the two of them go on doing exactly what they did — which is what keeps the review screen,
the staging rename and the three-step transaction unchanged by this.

The rule this module exists to hold is the one `domain/plugins._is_pinned` already states for a
runner argument: **the thing that gets installed has to be the thing that was reviewed.** A
branch name is not that. So a reference is resolved to a commit id *before* anything is
downloaded, the download is of that commit, and the id goes on the row — so "what is installed"
has an answer that does not change when somebody pushes to `main`.

Nothing here runs `git`. A tarball over HTTPS has no hooks, no submodules, no credential helper
and no `.git` directory to carry into the install, and it does not need git on the machine.
"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from kith.domain.plugins import PluginError

#: Where a downloaded tree is kept between the review and the install.
#:
#: Dot-prefixed and under the plugins root, for the two reasons `registry.STAGING` gives: nothing
#: walks directories to find plugins, and `sweep()` has one place to look. It is a cache and
#: nothing else — every entry is named by a commit id, so a miss costs a download and never
#: correctness.
FETCHED = ".fetched"

#: Ceilings on what one reference may drag in.
#:
#: A plugin is a manifest, some skills and a little UI. Nothing legitimate here is tens of
#: megabytes, and an unbounded read from a URL somebody typed is the whole of a denial of service
#: against the machine Kith is running on.
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_FILES = 5_000
TIMEOUT_SECONDS = 30.0

_API = "https://api.github.com"
#: GitHub's own rules: 1-39 characters, alphanumerics and hyphens for an owner; a repository may
#: also hold dots and underscores. Matched rather than trusted, because both halves are pasted
#: into a URL.
_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
#: A ref is a branch, a tag or a commit id. Bounded to what git itself accepts, minus everything
#: that would change the meaning of the URL it lands in.
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


@dataclass(frozen=True)
class Source:
    """A folder to install from, and an honest record of where it came from."""

    #: Exactly what the person typed. This is what the row records and what re-fetching uses, so
    #: `kith/notes@main` stays a request for the newest `main` rather than being frozen to the
    #: commit it resolved to this time.
    ref: str
    folder: Path
    #: `folder` or `github`.
    origin: str = "folder"
    #: The commit id, when there is one. Full, not abbreviated: this is the answer to "what is
    #: actually installed", and an abbreviation is one that stops being unique.
    revision: str = ""
    #: `owner/repo`, for a screen that wants to say where this is from without re-parsing.
    repository: str = ""
    #: The path inside the repository, when the plugin is not at its root.
    subdirectory: str = ""

    def described(self) -> str:
        """One line naming the source, for the review screen. Never a sentence this app speaks."""
        if self.origin != "github":
            return str(self.folder)
        inside = f"/{self.subdirectory}" if self.subdirectory else ""
        return f"{self.repository}{inside} at {self.revision[:12]}"


def looks_remote(ref: str) -> bool:
    """Whether this reference is something to fetch rather than a folder to read.

    **A folder on disk always wins, and that is the tie-break for the shorthand.** `owner/repo`
    and a relative path are the same string, so this asks the filesystem first: anything that
    resolves to a directory here is a directory, and the shorthand only applies to what is left.
    An explicit `github:` or a URL never consults the filesystem at all.
    """
    text = str(ref).strip()
    if not text:
        return False
    if text.startswith(("github:", "https://github.com/", "http://github.com/", "git@github.com:")):
        return True
    if text.startswith(("/", "~", ".")) or "\\" in text:
        return False
    if Path(text).expanduser().is_dir():
        return False
    parts = text.split("@")[0].split("/")
    return len(parts) >= 2 and bool(_OWNER.match(parts[0])) and bool(_REPO.match(parts[1]))


def resolve(ref: str | Path) -> Source:
    """Turn whatever was typed into a folder that can be parsed as a plugin.

    A local folder passes straight through and touches no network, so every existing caller —
    and every test that builds a plugin under `tmp_path` — behaves exactly as it did.
    """
    text = str(ref).strip()
    if not looks_remote(text):
        folder = Path(text).expanduser()
        return Source(ref=str(folder), folder=folder)
    owner, repo, subdirectory, wanted = _parse(text)
    revision = _commit(owner, repo, wanted)
    tree = _download(owner, repo, revision)
    folder = tree / subdirectory if subdirectory else tree
    if not folder.is_dir():
        raise PluginError(
            f"{owner}/{repo} at {revision[:12]} has no {subdirectory!r} in it."
            if subdirectory
            else f"{owner}/{repo} at {revision[:12]} unpacked to nothing."
        )
    return Source(
        ref=text,
        folder=folder,
        origin="github",
        revision=revision,
        repository=f"{owner}/{repo}",
        subdirectory=subdirectory,
    )


def sweep() -> int:
    """Drop the download cache. Called from `install.sweep` at app start.

    Everything in it is named by a commit id and reproducible from the network, so there is no
    thirty-day bargain to strike here the way there is with a plugin's state: this is the one
    thing under the plugins root that really is a cache.
    """
    place = _cache_root()
    if not place.is_dir():
        return 0
    count = 0
    for entry in place.iterdir():
        shutil.rmtree(entry, ignore_errors=True)
        count += 1
    return count


# --------------------------------------------------------------------------- #
# Reading a reference
# --------------------------------------------------------------------------- #


def _parse(text: str) -> tuple[str, str, str, str]:
    """`owner`, `repo`, the path inside it, and the branch/tag/commit asked for.

    Four spellings, because all four are things people actually have in their hands: the URL from
    the address bar, the URL of a subdirectory (GitHub's own `/tree/<ref>/<path>`), the
    `github:owner/repo` form a manifest or a README would use, and the bare `owner/repo` that
    everything from `go get` to `gh repo clone` accepts.
    """
    body = text
    for prefix in ("github:", "https://github.com/", "http://github.com/", "git@github.com:"):
        if body.startswith(prefix):
            body = body[len(prefix) :]
            break
    body = body.strip("/")

    wanted = ""
    # Split on the *last* `@`, so a `git@github.com:` form that slipped through, or a ref with an
    # `@` in it, cannot take the owner with it.
    if "@" in body:
        body, _, wanted = body.rpartition("@")

    segments = [one for one in body.split("/") if one]
    # `/tree/<ref>/<path>` — GitHub's own URL for a folder, and the one somebody copies when the
    # plugin is not at the root of the repository.
    if len(segments) >= 3 and segments[2] == "tree":
        owner, repo = segments[0], segments[1]
        rest = segments[3:]
        if rest and not wanted:
            wanted, rest = rest[0], rest[1:]
        subdirectory = "/".join(rest)
    elif len(segments) >= 2:
        owner, repo = segments[0], segments[1]
        subdirectory = "/".join(segments[2:])
    else:
        raise PluginError(
            f"{text!r} is not a GitHub reference. It can be `owner/repo`, `owner/repo@v1.2.0`, "
            f"`owner/repo/path/to/the/plugin`, or the URL from the address bar."
        )

    repo = repo.removesuffix(".git")
    if not _OWNER.match(owner) or not _REPO.match(repo):
        raise PluginError(f"{owner}/{repo} is not a GitHub owner and repository.")
    if wanted and not _REF.match(wanted):
        raise PluginError(f"{wanted!r} is not a branch, tag or commit.")
    # `..` would climb out of the unpacked tree; the tar filter refuses it on the way in as well,
    # and this refuses it before a URL is built. Two checks, because this one can say why.
    if any(part in ("..", "") for part in subdirectory.split("/") if subdirectory):
        raise PluginError(f"{subdirectory!r} is not a path inside a repository.")
    return owner, repo, subdirectory, wanted


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #


def _commit(owner: str, repo: str, wanted: str) -> str:
    """The commit id behind a branch, a tag, or the default branch. One request.

    **Resolved before anything is downloaded, and refused rather than guessed.** Installing from
    `main` without pinning is the same failure `_is_pinned` refuses for `npx`: a grant given to
    code nobody reviewed, against a name that means something different tomorrow. If this cannot
    be answered — offline, rate-limited, private without a token — the honest outcome is no
    install, not an install of something unidentifiable.
    """
    where = wanted or "HEAD"
    body = _get(
        f"{_API}/repos/{owner}/{repo}/commits/{where}",
        accept="application/vnd.github.sha",
        what=f"{owner}/{repo}",
    )
    sha = body.decode("utf-8", "replace").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise PluginError(f"GitHub did not say which commit {owner}/{repo}@{where} is.")
    return sha


def _download(owner: str, repo: str, revision: str) -> Path:
    """The repository at one commit, unpacked, cached by commit id.

    Committed with a rename the way an install is, for the same reason: a half-extracted tree
    that a later run mistakes for a complete one is a plugin installed from a truncated download.
    """
    destination = _cache_root() / f"{owner}-{repo}-{revision}"
    if destination.is_dir():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)

    archive = _get(
        f"{_API}/repos/{owner}/{repo}/tarball/{revision}",
        accept="application/vnd.github+json",
        what=f"{owner}/{repo}",
    )
    staging = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".unpacking-"))
    try:
        _unpack(archive, staging)
        # GitHub wraps everything in one `{owner}-{repo}-{sha}` directory. Lifted out, so the
        # path a person reviewed is the path the plugin is at rather than one with a commit id
        # in the middle of it.
        inside = [one for one in staging.iterdir() if not one.name.startswith(".")]
        root = inside[0] if len(inside) == 1 and inside[0].is_dir() else staging
        os.replace(root, destination)
    except FileNotFoundError:
        raise PluginError(f"{owner}/{repo} at {revision[:12]} unpacked to nothing.") from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return destination


def _unpack(archive: bytes, into: Path) -> None:
    """Extract a tarball from the internet, with the two bounds that makes it safe to do.

    `filter="data"` is the refusal: absolute paths, `..`, symlinks pointing out of the tree,
    device nodes and setuid bits are all rejected by the extractor rather than by anything here.
    The count and size ceilings are the other half — a tar that is *valid* can still be a
    thousand nested gigabytes, and a bound is cheaper than recovering a full disk.
    """
    import io

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        total = 0
        members = []
        for member in tar:
            if len(members) >= MAX_FILES:
                raise PluginError(f"That repository has more than {MAX_FILES:,} files in it.")
            total += max(member.size, 0)
            if total > MAX_DOWNLOAD_BYTES:
                raise PluginError(
                    f"That repository unpacks to more than "
                    f"{MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB. A plugin is a manifest and a "
                    f"little UI; something else is going on here."
                )
            members.append(member)
        tar.extractall(into, members=members, filter="data")


def _get(url: str, *, accept: str, what: str) -> bytes:
    """One GET, bounded, with whatever token is in the environment.

    A token is read from the environment and never from a manifest or a row, for the reason
    `domain/plugins.ServerSpec` gives about `env`: a credential that can be written down
    somewhere a plugin can read has already leaked. This one is the *person's* GitHub token, not
    a plugin's, so it is not offered a place to live in Kith at all.
    """
    headers = {
        "Accept": accept,
        "User-Agent": "kith",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("KITH_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=TIMEOUT_SECONDS
        ) as response:
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.HTTPError as refused:
        raise PluginError(_why(refused, what)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as refused:
        raise PluginError(f"Could not reach GitHub for {what} ({refused}).") from None
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise PluginError(
            f"{what} is larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB, which is not a plugin."
        )
    return body


def _why(refused: urllib.error.HTTPError, what: str) -> str:
    """GitHub's status code as a sentence somebody can act on.

    Written here, in code, and never taken from the response body — the rule `install.inspect`
    states for a manifest applies to a remote server just as well: a sentence this app speaks is
    a sentence this app wrote.
    """
    if refused.code == 404:
        return (
            f"GitHub has no {what}, or it is private. A private repository needs a "
            f"`GITHUB_TOKEN` in Kith's environment that can read it."
        )
    if refused.code in (401, 403):
        if refused.headers.get("X-RateLimit-Remaining") == "0":
            return (
                "GitHub is rate-limiting this machine. Setting a `GITHUB_TOKEN` in Kith's "
                "environment raises the limit from 60 requests an hour to 5,000."
            )
        return f"GitHub refused access to {what}. A `GITHUB_TOKEN` that can read it would help."
    return f"GitHub answered {refused.code} for {what}."


def _cache_root() -> Path:
    from kith.services.plugins import registry

    return registry.root() / FETCHED
