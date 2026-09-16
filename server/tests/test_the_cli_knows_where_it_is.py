"""Standing in a directory is how the CLI decides which conversation and which project.

Every one of these is a case where getting it wrong puts a message somewhere real, with
nothing on screen to say so — which is why the resolution is tested rather than trusted to
read correctly.
"""

from __future__ import annotations

import sys

import pytest

from kith.cli import context


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """The sticky file, per test. The real one records this developer's own sessions."""
    monkeypatch.setattr(context, "STATE_PATH", tmp_path / "cli" / "sessions.json")


def _project(identifier: int, name: str, directory: str | None) -> dict:
    return {"id": identifier, "name": name, "directory": directory, "status": "active"}


# --------------------------------------------------------------------------- #
# Which project


def test_the_deepest_matching_project_wins(tmp_path):
    """Projects nest: a repository, and a service inside it with its own project.

    First-match would be right about half the time, depending on an ordering the server never
    promised — and the half it was wrong, a message about the service would be filed under the
    umbrella project with its memory and its board.
    """
    umbrella = tmp_path / "repo"
    service = umbrella / "services" / "api"
    service.mkdir(parents=True)

    projects = [
        _project(1, "Repo", str(umbrella)),
        _project(2, "API", str(service)),
    ]
    assert context.resolve_project(projects, service)["name"] == "API"
    assert context.resolve_project(list(reversed(projects)), service)["name"] == "API"
    assert context.resolve_project(projects, umbrella)["name"] == "Repo"


def test_a_project_with_no_folder_matches_nothing(tmp_path):
    """`directory` is None for research and shortlists — work that is not code.

    An empty string treated as a path is a prefix of every path, so a project with no folder
    would otherwise match every directory on the machine.
    """
    somewhere = tmp_path / "anywhere"
    somewhere.mkdir()
    assert context.resolve_project([_project(1, "Reading", None)], somewhere) is None
    assert context.resolve_project([_project(1, "Reading", "")], somewhere) is None


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="case-insensitive filesystems only")
def test_a_project_folder_matches_whatever_case_it_was_written_in(tmp_path):
    """Found in the wild: a project recorded at `…/Desktop/Personal/…` and a shell sitting in
    `…/Desktop/personal/…`. One directory on disk, two strings, and a string comparison finds
    no project at all — so the conversation starts unbound and nothing says why."""
    actual = tmp_path / "Personal" / "work"
    actual.mkdir(parents=True)
    recorded = str(tmp_path / "personal" / "work")

    found = context.resolve_project([_project(1, "Work", recorded)], actual)
    assert found is not None and found["name"] == "Work"


def test_a_pin_beats_the_folders(tmp_path):
    here = tmp_path / "repo"
    here.mkdir()
    projects = [_project(1, "Guessed", str(here)), _project(2, "Chosen", None)]
    context.pin(here, 2)
    assert context.resolve_project(projects, here)["name"] == "Chosen"


# --------------------------------------------------------------------------- #
# Which directory


def test_the_root_is_the_nearest_marker_not_the_home_directory(tmp_path):
    repo = tmp_path / "repo"
    deep = repo / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (repo / ".git").mkdir()
    assert context.project_root(deep) == repo.resolve()


def test_a_directory_with_no_marker_is_its_own_root(tmp_path):
    """Falling back to `$HOME` would make one sticky session shared by every unrelated command
    run outside a repository — the one way this could put a message in the wrong conversation
    without anyone touching a flag."""
    loose = tmp_path / "loose"
    loose.mkdir()
    assert context.project_root(loose) == loose.resolve()


# --------------------------------------------------------------------------- #
# Which conversation


def test_a_remembered_conversation_survives_a_round_trip(tmp_path):
    here = tmp_path / "repo"
    here.mkdir()
    context.remember(here, "20260916-120000000-abcdef", 7)
    assert context.remembered(here)["conversationId"] == "20260916-120000000-abcdef"
    assert context.remembered(here)["projectId"] == 7
    context.forget(here)
    assert context.remembered(here) == {}


def test_two_directories_keep_separate_conversations(tmp_path):
    """The whole scenario: one agent on the frontend, another on the backend, same machine."""
    frontend, backend = tmp_path / "ui", tmp_path / "api"
    frontend.mkdir()
    backend.mkdir()
    context.remember(frontend, "conv-ui", 1)
    context.remember(backend, "conv-api", 2)
    assert context.remembered(frontend)["conversationId"] == "conv-ui"
    assert context.remembered(backend)["conversationId"] == "conv-api"


def test_a_corrupt_state_file_is_ignored_rather_than_fatal(tmp_path, monkeypatch):
    """Bookkeeping must never be able to stop a message being sent."""
    path = tmp_path / "cli" / "sessions.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    monkeypatch.setattr(context, "STATE_PATH", path)
    assert context.remembered(tmp_path) == {}
    context.remember(tmp_path, "conv", None)
    assert context.remembered(tmp_path)["conversationId"] == "conv"


# --------------------------------------------------------------------------- #
# Prefixes


def test_an_id_prefix_expands_when_it_is_unambiguous():
    known = ["20260916-143022891-a3f9c1", "20260915-091204338-7be220"]
    assert context.resolve_id("20260916", known) == known[0]
    assert context.resolve_id(known[1], known) == known[1]


def test_an_ambiguous_prefix_refuses_rather_than_picking_the_newest():
    """Picking would be right most of the time. The times it was wrong, the message would land
    in a stranger's conversation and the only evidence would be in Kith's transcript."""
    known = ["20260916-1-aaaaaa", "20260916-2-bbbbbb"]
    from kith.cli.errors import Failure

    with pytest.raises(Failure) as refused:
        context.resolve_id("20260916", known)
    assert "matches 2" in str(refused.value)


def test_an_unknown_prefix_says_so():
    from kith.cli.errors import Failure

    with pytest.raises(Failure):
        context.resolve_id("nope", ["20260916-1-aaaaaa"])
