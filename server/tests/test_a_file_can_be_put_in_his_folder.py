"""The Files screen's Upload: a file from your machine, into his folder, and nowhere else.

The route is small and every rule in it is a way an upload goes wrong without anyone noticing:

* a name already taken is a question (409, with what is there), never a silent replace;
* a folder of ``..``, an absolute one, or a symlink out of his folder lands nowhere;
* a filename the browser sends as a path is cut to its name;
* too big is refused — before reading when the length is declared, while writing when it is not —
  and a refused upload leaves no half-file behind under any name;
* and the panel is told, so the folder you dropped into shows the file without a refresh.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from kith.api import auth
from kith.services import uploads


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """The real app, with every path pointed somewhere disposable — as the save route's tests do."""
    monkeypatch.setenv("KITH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    import kith.settings as live

    monkeypatch.setattr(live, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(live, "CONFIG_DB_PATH", tmp_path / "data" / "config.db")
    monkeypatch.setattr(live, "AGENT_DB_PATH", tmp_path / "data" / "agent.db")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    from kith.infra.db import config_store

    config_store.init(tmp_path / "data" / "config.db")
    from kith.app import create_app

    return create_app().test_client()


@pytest.fixture
def headers(tmp_path: Path):
    return {"X-Kith-Token": auth.token(tmp_path / "data")}


@pytest.fixture(autouse=True)
def asking_rather_than_bypassing(monkeypatch):
    """Ask mode, so the permission check is doing something — in bypass it allows everything."""
    from kith.infra import permissions

    monkeypatch.setattr(permissions, "mode", lambda: permissions.Mode.ASK)


@pytest.fixture
def published(monkeypatch) -> list[str]:
    said: list[str] = []
    monkeypatch.setattr(uploads.changes, "publish", said.append)
    return said


def send(client, headers, name: str, data: bytes, **form):
    body = {"file": (io.BytesIO(data), name), **form}
    return client.post(
        "/api/workspace/upload", data=body, headers=headers, content_type="multipart/form-data"
    )


def leftovers(folder: Path) -> list[str]:
    return [p.name for p in folder.rglob("*.uploading")]


class TestPuttingAFileIn:
    def test_it_lands_at_his_root_by_default(self, client, headers, never_the_real_workspace):
        answer = send(client, headers, "notes.txt", b"hello")
        assert answer.status_code == 200
        assert (never_the_real_workspace / "notes.txt").read_bytes() == b"hello"
        body = answer.get_json()
        assert (body["path"], body["name"], body["size"], body["type"]) == (
            "notes.txt",
            "notes.txt",
            5,
            "file",
        )

    def test_it_lands_in_the_folder_named(self, client, headers, never_the_real_workspace):
        (never_the_real_workspace / "src" / "assets").mkdir(parents=True)
        answer = send(client, headers, "logo.png", b"\x89PNG\0bytes", folder="src/assets")
        assert answer.status_code == 200
        assert answer.get_json()["path"] == "src/assets/logo.png"
        assert (never_the_real_workspace / "src" / "assets" / "logo.png").read_bytes() == b"\x89PNG\0bytes"

    def test_the_panel_is_told_a_file_changed(self, client, headers, published):
        send(client, headers, "notes.txt", b"hello")
        assert published == ["workspace"]

    def test_a_large_file_arrives_whole(self, client, headers, never_the_real_workspace):
        data = bytes(range(256)) * (3 * 1024 * 16)  # 12 MB, several chunks
        assert send(client, headers, "blob.bin", data).status_code == 200
        assert (never_the_real_workspace / "blob.bin").read_bytes() == data
        assert leftovers(never_the_real_workspace) == []


class TestATakenName:
    def test_it_is_a_409_with_the_file_that_is_there(
        self, client, headers, never_the_real_workspace, published
    ):
        (never_the_real_workspace / "notes.txt").write_bytes(b"the original")
        answer = send(client, headers, "notes.txt", b"new")
        assert answer.status_code == 409
        existing = answer.get_json()["existing"]
        assert (existing["path"], existing["size"]) == ("notes.txt", len(b"the original"))
        assert (never_the_real_workspace / "notes.txt").read_bytes() == b"the original"
        assert leftovers(never_the_real_workspace) == []
        assert published == []

    def test_overwrite_replaces_it(self, client, headers, never_the_real_workspace):
        (never_the_real_workspace / "notes.txt").write_bytes(b"the original")
        answer = send(client, headers, "notes.txt", b"new", overwrite="1")
        assert answer.status_code == 200
        assert (never_the_real_workspace / "notes.txt").read_bytes() == b"new"

    def test_a_folder_of_that_name_is_never_replaced(self, client, headers, never_the_real_workspace):
        (never_the_real_workspace / "src").mkdir()
        answer = send(client, headers, "src", b"x", overwrite="1")
        assert answer.status_code == 400
        assert (never_the_real_workspace / "src").is_dir()


class TestStayingInside:
    def test_a_folder_that_climbs_out_is_refused(self, client, headers, tmp_path, never_the_real_workspace):
        answer = send(client, headers, "escaped.txt", b"x", folder="../")
        assert answer.status_code == 403
        assert not (never_the_real_workspace.parent / "escaped.txt").exists()

    def test_an_absolute_folder_is_refused(self, client, headers, tmp_path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        assert send(client, headers, "escaped.txt", b"x", folder=str(target)).status_code == 403
        assert not (target / "escaped.txt").exists()

    def test_a_link_out_of_his_folder_is_refused_for_where_it_goes(
        self, client, headers, tmp_path, never_the_real_workspace
    ):
        outside = tmp_path / "outside"
        outside.mkdir()
        (never_the_real_workspace / "door").symlink_to(outside, target_is_directory=True)
        assert send(client, headers, "escaped.txt", b"x", folder="door").status_code == 403
        assert not (outside / "escaped.txt").exists()

    def test_a_filename_sent_as_a_path_is_cut_to_its_name(self, client, headers, never_the_real_workspace):
        answer = send(client, headers, "../../escaped.txt", b"x")
        assert answer.status_code == 200
        assert answer.get_json()["path"] == "escaped.txt"
        assert (never_the_real_workspace / "escaped.txt").exists()

    def test_his_own_records_folder_is_not_somewhere_to_put_files(
        self, client, headers, never_the_real_workspace
    ):
        (never_the_real_workspace / ".kith").mkdir(exist_ok=True)
        assert send(client, headers, "x.txt", b"x", folder=".kith").status_code == 403


class TestRefusals:
    def test_too_big_by_its_declared_length_is_refused_before_reading(
        self, client, headers, never_the_real_workspace, monkeypatch
    ):
        monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1024)
        answer = send(client, headers, "big.bin", b"x" * 200_000)
        assert answer.status_code == 413
        assert not (never_the_real_workspace / "big.bin").exists()

    def test_too_big_while_streaming_leaves_nothing_behind(self, never_the_real_workspace):
        with pytest.raises(uploads.TooLarge):
            uploads.save(
                io.BytesIO(b"x" * (3 * uploads.CHUNK_BYTES)), "big.bin", ".", max_bytes=uploads.CHUNK_BYTES
            )
        assert not (never_the_real_workspace / "big.bin").exists()
        assert leftovers(never_the_real_workspace) == []

    def test_a_folder_that_is_not_there_is_refused(self, client, headers):
        assert send(client, headers, "x.txt", b"x", folder="no/such/place").status_code == 400

    def test_no_file_is_refused(self, client, headers):
        answer = client.post(
            "/api/workspace/upload", data={"folder": "."}, headers=headers, content_type="multipart/form-data"
        )
        assert answer.status_code == 400

    def test_uploading_still_needs_the_token(self, client, never_the_real_workspace):
        answer = client.post(
            "/api/workspace/upload",
            data={"file": (io.BytesIO(b"x"), "x.txt")},
            content_type="multipart/form-data",
        )
        assert answer.status_code == 401
        assert not (never_the_real_workspace / "x.txt").exists()
