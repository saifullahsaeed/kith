"""Installing a plugin from a repository, and the one property that makes it defensible.

A plugin is a folder. Everything about installing one — the review that writes nothing, the
staging rename, the three-step transaction — is built on that and none of it changes here.
`sources.resolve` turns a reference into a folder and hands it on, so this is one seam rather
than a second install path.

**The property: a reference is resolved to a commit before anything is downloaded.** It is the
same rule `_is_pinned` already enforces for `npx -y pkg` one layer down, and for the same reason:
a grant is given to a specific program, and `main` is not one. So the review names a commit, the
install downloads that commit, and the row records it — and "what did I agree to" has an answer
that does not change when somebody pushes.

Nothing here runs `git`. A tarball over HTTPS has no hooks, no submodules and no credential
helper, and it does not need git installed. What it does have is somebody else's archive being
unpacked onto this disk, which is why the extraction is bounded and filtered and why the last
tests in this file are about a tar that is trying something.
"""

from __future__ import annotations

import io
import json
import tarfile
import urllib.error
from pathlib import Path

import pytest

from kith.domain.plugins import PluginError
from kith.infra import permissions
from kith.services.plugins import install as installer
from kith.services.plugins import registry, sources

SHA = "a" * 40
MANIFEST = {
    "manifest": 1,
    "id": "notes",
    "name": "Notes",
    "version": "1.0.0",
    "description": "Somebody else's plugin.",
}


@pytest.fixture(autouse=True)
def somewhere_to_install(tmp_path: Path, config_db: Path, monkeypatch):
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)
    registry.forget_cache()
    permissions.revoke_all()
    yield
    registry.forget_cache()
    permissions.revoke_all()


# --------------------------------------------------------------------------- #
# Reading a reference. No network in any of this.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text, expected",
    [
        ("kith/notes", ("kith", "notes", "", "")),
        ("kith/notes@v1.2.0", ("kith", "notes", "", "v1.2.0")),
        ("kith/notes@" + SHA, ("kith", "notes", "", SHA)),
        ("github:kith/notes", ("kith", "notes", "", "")),
        ("kith/notes/examples/board", ("kith", "notes", "examples/board", "")),
        ("kith/notes/examples/board@main", ("kith", "notes", "examples/board", "main")),
        ("https://github.com/kith/notes", ("kith", "notes", "", "")),
        ("https://github.com/kith/notes.git", ("kith", "notes", "", "")),
        ("https://github.com/kith/notes/", ("kith", "notes", "", "")),
        # GitHub's own URL for a folder, which is what somebody copies when the plugin is not at
        # the root of the repository.
        (
            "https://github.com/kith/notes/tree/main/examples/board",
            ("kith", "notes", "examples/board", "main"),
        ),
        ("git@github.com:kith/notes.git", ("kith", "notes", "", "")),
    ],
)
def test_the_spellings_people_actually_have_in_their_hands(text, expected):
    assert sources._parse(text) == expected


@pytest.mark.parametrize("text", ["notarepo", "kith/notes@bad ref", "-/x", "kith/note s"])
def test_a_reference_it_cannot_read_is_refused_rather_than_guessed_at(text):
    with pytest.raises(PluginError):
        sources._parse(text)


def test_a_path_that_climbs_out_of_the_repository_is_refused():
    with pytest.raises(PluginError, match="not a path inside"):
        sources._parse("kith/notes/../../etc")


def test_a_folder_on_this_machine_always_wins(tmp_path: Path, monkeypatch):
    """`owner/repo` and a relative path are the same string, so the filesystem is the tie-break.

    Without this, running Kith from a directory that happens to contain `examples/sketchpad`
    would send somebody's local folder name to GitHub.
    """
    (tmp_path / "kith" / "notes").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert sources.looks_remote("kith/notes") is False
    assert sources.looks_remote("kith/nothing-here") is True
    assert sources.looks_remote("/absolute/kith/notes") is False
    assert sources.looks_remote("./kith/notes") is False


def test_a_local_folder_resolves_to_itself_and_touches_nothing(tmp_path: Path, monkeypatch):
    def nothing(*a, **k):
        raise AssertionError("a folder must not reach the network")

    monkeypatch.setattr(sources, "_get", nothing)
    (tmp_path / "here").mkdir()

    found = sources.resolve(tmp_path / "here")

    assert found.origin == "folder"
    assert found.folder == tmp_path / "here"
    assert found.revision == ""


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #


def a_tarball(files: dict[str, str], *, root: str = f"kith-notes-{SHA[:7]}") -> bytes:
    """What `api.github.com/.../tarball/<sha>` hands back: everything under one wrapper folder."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, body in files.items():
            raw = body.encode()
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return buffer.getvalue()


@pytest.fixture
def github(monkeypatch):
    """GitHub, as the two requests this makes. Records what was asked for."""
    asked: list[str] = []
    state = {"tar": a_tarball({"kith.plugin.json": json.dumps(MANIFEST)}), "sha": SHA}

    def fake(url: str, *, accept: str, what: str) -> bytes:
        asked.append(url)
        if "/commits/" in url:
            return state["sha"].encode()
        return state["tar"]

    monkeypatch.setattr(sources, "_get", fake)
    return {"asked": asked, "state": state}


def test_it_resolves_a_commit_before_it_downloads_anything(github):
    found = sources.resolve("kith/notes@main")

    assert github["asked"][0].endswith("/repos/kith/notes/commits/main")
    assert github["asked"][1].endswith(f"/repos/kith/notes/tarball/{SHA}")
    assert found.revision == SHA


def test_no_branch_asked_for_means_the_default_branch(github):
    sources.resolve("kith/notes")

    assert github["asked"][0].endswith("/commits/HEAD")


def test_the_wrapper_folder_github_adds_is_lifted_off(github):
    found = sources.resolve("kith/notes")

    assert (found.folder / "kith.plugin.json").is_file()
    assert found.folder.name.endswith(SHA)


def test_a_plugin_in_a_subdirectory(github, monkeypatch):
    github["state"]["tar"] = a_tarball(
        {"examples/board/kith.plugin.json": json.dumps(MANIFEST), "README.md": "hello"}
    )

    found = sources.resolve("kith/notes/examples/board")

    assert (found.folder / "kith.plugin.json").is_file()
    assert found.subdirectory == "examples/board"


def test_a_subdirectory_that_is_not_there_says_so(github):
    with pytest.raises(PluginError, match="has no 'examples/nope'"):
        sources.resolve("kith/notes/examples/nope")


def test_the_second_reference_to_the_same_commit_is_not_downloaded_again(github):
    sources.resolve("kith/notes@main")
    sources.resolve("kith/notes@main")

    # Two commit lookups — the ref may have moved, and pretending otherwise is how a cache
    # becomes a lie — but only one download, because a commit id cannot mean two things.
    assert sum(1 for url in github["asked"] if "/tarball/" in url) == 1


def test_a_commit_github_will_not_name_is_not_installable(monkeypatch):
    """Offline, rate-limited, private without a token — the honest outcome is no install, not an
    install of something that cannot be identified afterwards."""

    def refuse(url, *, accept, what):
        raise PluginError("Could not reach GitHub for kith/notes.")

    monkeypatch.setattr(sources, "_get", refuse)

    with pytest.raises(PluginError, match="Could not reach GitHub"):
        sources.resolve("kith/notes")


def test_an_answer_that_is_not_a_commit_id_is_refused(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda url, *, accept, what: b"<html>sorry</html>")

    with pytest.raises(PluginError, match="did not say which commit"):
        sources.resolve("kith/notes")


# --------------------------------------------------------------------------- #
# Somebody else's archive, landing on this disk
# --------------------------------------------------------------------------- #


def test_a_tar_that_tries_to_write_outside_its_own_folder_is_refused(github, tmp_path: Path):
    """`filter="data"` is the refusal, and it is the extractor's rather than ours — the list of
    things that can be smuggled through a tar is longer than anything written here would be."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        raw = b"pwned"
        info = tarfile.TarInfo("../../escaped.txt")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    github["state"]["tar"] = buffer.getvalue()

    # `tarfile.OutsideDestinationError` by name, so this keeps meaning something if the filter
    # ever stops being the thing that refuses.
    with pytest.raises(tarfile.FilterError):
        sources.resolve("kith/notes")
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_an_archive_that_unpacks_to_more_than_a_plugin_could_be_is_refused(github, monkeypatch):
    monkeypatch.setattr(sources, "MAX_DOWNLOAD_BYTES", 64)
    github["state"]["tar"] = a_tarball({"big.bin": "x" * 5_000})

    with pytest.raises(PluginError, match="unpacks to more than"):
        sources.resolve("kith/notes")


def test_an_archive_with_absurdly_many_files_is_refused(github, monkeypatch):
    monkeypatch.setattr(sources, "MAX_FILES", 3)
    github["state"]["tar"] = a_tarball({f"f{n}.txt": "x" for n in range(10)})

    with pytest.raises(PluginError, match="more than"):
        sources.resolve("kith/notes")


def test_githubs_own_refusals_come_back_as_sentences_kith_wrote(monkeypatch):
    """Never the response body. The rule `inspect` states for a manifest holds for a remote
    server too: a sentence this app speaks is a sentence this app wrote."""
    made = urllib.error.HTTPError("u", 404, "Not Found", {}, None)  # pyright: ignore[reportArgumentType]

    assert "is private" in sources._why(made, "kith/notes")

    limited = urllib.error.HTTPError("u", 403, "Forbidden", {"X-RateLimit-Remaining": "0"}, None)  # pyright: ignore[reportArgumentType]

    assert "rate-limiting" in sources._why(limited, "kith/notes")
    assert "GITHUB_TOKEN" in sources._why(limited, "kith/notes")


# --------------------------------------------------------------------------- #
# And the install itself
# --------------------------------------------------------------------------- #


def test_the_review_names_the_commit_it_read(github, config_db: Path):
    report = installer.inspect("kith/notes@main", config_db)

    assert report["origin"] == "github"
    assert report["repository"] == "kith/notes"
    assert report["revision"] == SHA
    assert report["from"] == f"kith/notes at {SHA[:12]}"
    assert report["installable"] is True


def test_reviewing_it_does_not_install_it(github, config_db: Path):
    installer.inspect("kith/notes@main", config_db)

    assert registry.rows(config_db) == {}


def test_installing_it_records_where_it_came_from(github, config_db: Path):
    installer.install(config_db, "kith/notes@main")

    row = registry.row(config_db, "notes") or {}
    assert row["origin"] == "github"
    assert row["revision"] == SHA
    # What was *typed*, not the cache path it landed in: `@main` is still a thing somebody can
    # hand back to get whatever `main` says next time, where a path under `.fetched` is gone at
    # the next app start.
    assert row["source"] == "kith/notes@main"


def test_one_install_resolves_the_reference_once(github, config_db: Path):
    """`install` calls `inspect`, and both used to resolve. Two resolutions of `@main` is two
    chances for the answer to differ — a review of one commit and an install of another, which
    is the gap pinning exists to close."""
    installer.install(config_db, "kith/notes@main")

    assert sum(1 for url in github["asked"] if "/commits/" in url) == 1


def test_a_plugin_from_github_is_a_plugin_like_any_other(github, config_db: Path):
    installer.install(config_db, "kith/notes@main")

    plugin = registry.get(config_db, "notes")
    assert plugin is not None
    assert plugin.name == "Notes"


def test_a_repository_with_no_manifest_in_it_says_which_repository(github, config_db: Path):
    github["state"]["tar"] = a_tarball({"README.md": "hello"})

    with pytest.raises(PluginError, match="kith/notes at"):
        installer.inspect("kith/notes", config_db)


def test_the_download_cache_is_swept(github, config_db: Path):
    sources.resolve("kith/notes")
    assert list((registry.root() / sources.FETCHED).iterdir())

    done = installer.sweep(config_db)

    assert list((registry.root() / sources.FETCHED).iterdir()) == []
    assert any(one["kind"] == "fetched" for one in done)
