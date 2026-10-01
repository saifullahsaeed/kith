"""⌘K finds a file by its name or by a line in it, anywhere in his folder.

The palette used to match names only, and only in folders something had already listed — so the
question it was most often asked, "which file says this", had no answer. `/api/workspace/search`
is that answer, and these pin the parts of it that fail quietly rather than loudly:

* **Two engines, one behaviour.** ripgrep when it is installed, Python when it is not — and on a
  Finder-launched app it usually is not. The core cases run against both, so a skip rule or a
  case rule that holds in one and not the other is a failing test rather than a search that
  gives different answers on two machines.
* **Never out of his folder** — by `..`, by an absolute path, or by a symlink inside it.
* **Bounded.** A count and a clock, and `truncated` when either cut it short.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from kith.api import auth
from kith.infra import executables
from kith.services import workspace_search
from kith.services.workspace_search import search

REAL_RG = executables.which("rg")


@pytest.fixture(params=["python", "rg"])
def engine(request, monkeypatch):
    """Each core case once per engine. ripgrep's run is skipped where it is not installed."""
    if request.param == "python":
        monkeypatch.setattr(workspace_search, "_ripgrep", lambda: None)
    elif not REAL_RG:
        pytest.skip("ripgrep is not installed here")
    return request.param


@pytest.fixture
def folder(never_the_real_workspace: Path) -> Path:
    """A small workspace shaped like a real one: source, dependencies, a build, a dotfolder."""
    root = never_the_real_workspace
    (root / "src").mkdir()
    (root / "src" / "retry.py").write_text(
        "import time\n\n    def Backoff(attempt):  \n        return 2 ** attempt\n"
    )
    (root / "src" / "notes.md").write_text("Nothing about it here.\n")
    (root / "backoff-plan").mkdir()
    (root / "node_modules" / "lib").mkdir(parents=True)
    (root / "node_modules" / "lib" / "index.js").write_text("// backoff in a dependency\n")
    (root / "dist").mkdir()
    (root / "dist" / "bundle.js").write_text("backoff()\n")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "secret.txt").write_text("backoff, hidden\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("backoff in git\n")
    return root


def kinds(found) -> list[tuple[str, str, int | None]]:
    return [(r["kind"], r["path"], r.get("line")) for r in found.results]


class TestWhatItFinds:
    def test_a_name_and_a_line_both_count_and_names_come_first(self, folder, engine):
        found = search("backoff")
        assert kinds(found) == [("name", "backoff-plan", None), ("content", "src/retry.py", 3)]
        assert found.engine == engine

    def test_a_folder_found_by_name_says_it_is_a_folder(self, folder, engine):
        hit = search("backoff-plan").results[0]
        assert (hit["type"], hit["kind"], hit["name"]) == ("dir", "name", "backoff-plan")

    def test_case_does_not_matter_either_way(self, folder, engine):
        assert kinds(search("BACKOFF(")) == [("content", "src/retry.py", 3)]

    def test_the_line_is_sent_trimmed_with_its_file_s_size_and_date(self, folder, engine):
        hit = search("def backoff").results[0]
        assert hit["text"] == "def Backoff(attempt):"
        assert hit["name"] == "retry.py"
        assert hit["size"] == (folder / "src" / "retry.py").stat().st_size
        assert hit["modified"] == int((folder / "src" / "retry.py").stat().st_mtime)

    def test_the_query_is_one_string_not_a_pattern(self, folder, engine):
        (folder / "dots.txt").write_text("a.b\naxb\n")
        assert kinds(search("a.b")) == [("content", "dots.txt", 1)]

    def test_nothing_to_look_for_finds_nothing(self, folder, engine):
        assert search("   ").results == []


class TestWhatItSkips:
    def test_dependencies_builds_git_and_hidden_folders_are_not_searched(self, folder, engine):
        paths = {r["path"] for r in search("backoff").results}
        assert not any(p.startswith(("node_modules", "dist", ".git", ".hidden")) for p in paths)

    def test_hidden_folders_are_searched_when_asked_but_git_never_is(self, folder, engine):
        paths = {r["path"] for r in search("backoff", hidden=True).results}
        assert ".hidden/secret.txt" in paths
        assert ".git/config" not in paths

    def test_a_query_that_starts_with_a_dot_is_asking_for_hidden_files(self, folder, engine):
        (folder / ".env").write_text("KEY=1\n")
        assert ("name", ".env", None) in kinds(search(".env"))

    def test_a_binary_file_is_not_read(self, folder, engine):
        (folder / "image.bin").write_bytes(b"\x89PNG\0\0backoff\0")
        assert "image.bin" not in {r["path"] for r in search("backoff").results}

    def test_a_file_over_a_megabyte_is_not_read(self, folder, engine):
        (folder / "big.log").write_text("backoff\n" + "x" * (workspace_search.MAX_FILE_BYTES + 10))
        assert "big.log" not in {r["path"] for r in search("backoff").results}

    def test_a_link_out_of_his_folder_is_not_followed(self, folder, engine, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "elsewhere.txt").write_text("backoff from outside\n")
        (folder / "linked").symlink_to(outside, target_is_directory=True)
        (folder / "linked-file.txt").symlink_to(outside / "elsewhere.txt")
        texts = [r.get("text") or "" for r in search("backoff").results]
        assert not any("outside" in t for t in texts)


class TestBounds:
    def test_it_stops_at_the_limit_and_says_so(self, folder, engine):
        for i in range(8):
            (folder / f"many-{i}.txt").write_text("needle\n")
        found = search("needle", limit=3)
        assert len(found.results) == 3
        assert found.truncated

    def test_a_search_that_finishes_is_not_truncated(self, folder, engine):
        assert not search("backoff").truncated

    def test_one_file_cannot_be_the_whole_answer(self, folder, engine):
        (folder / "loud.txt").write_text("needle\n" * 40)
        found = search("needle")
        assert len(found.results) == workspace_search.PER_FILE

    def test_it_stops_at_the_clock_and_says_so(self, folder, monkeypatch):
        """Time made to jump a second per look, so the budget is gone before the walk starts."""
        monkeypatch.setattr(workspace_search, "_ripgrep", lambda: None)
        ticks = iter(range(10_000))
        monkeypatch.setattr(workspace_search.time, "monotonic", lambda: float(next(ticks)))
        found = search("backoff")
        assert found.truncated

    def test_a_long_line_is_cut_around_the_match(self, folder, engine):
        (folder / "wide.txt").write_text("x" * 5000 + " needle " + "y" * 5000 + "\n")
        text = search("needle").results[0]["text"]
        assert "needle" in text
        assert len(text) <= workspace_search.LINE_WIDTH + 2


class TestStayingInside:
    def test_a_folder_above_his_is_refused(self, folder):
        with pytest.raises(workspace_search.SearchError):
            search("x", under="../")

    def test_an_absolute_folder_is_refused(self, folder):
        with pytest.raises(workspace_search.SearchError):
            search("x", under="/etc")

    def test_a_folder_inside_narrows_it(self, folder, engine):
        assert [r["path"] for r in search("backoff", under="src").results] == ["src/retry.py"]


class TestRipgrepIsReadNotTrusted:
    """The ripgrep path's parsing, run against a stand-in binary so it is tested on any machine."""

    def _stub(self, tmp_path: Path, lines: list[dict], code: int = 0) -> str:
        script = tmp_path / "rg"
        payload = "\n".join(json.dumps(line) for line in lines)
        script.write_text(
            f"#!{sys.executable}\nimport sys\nsys.stdout.write({payload!r} + '\\n')\nsys.exit({code})\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return str(script)

    def test_its_matches_become_results(self, folder, tmp_path, monkeypatch):
        target = str((folder / "src" / "retry.py").resolve())
        rg = self._stub(
            tmp_path,
            [
                {"type": "begin", "data": {"path": {"text": target}}},
                {
                    "type": "match",
                    "data": {
                        "path": {"text": target},
                        "lines": {"text": "  hello Needle  \n"},
                        "line_number": 7,
                    },
                },
                {"type": "end", "data": {}},
            ],
        )
        monkeypatch.setattr(workspace_search, "_ripgrep", lambda: rg)
        found = search("needle")
        assert found.engine == "rg"
        assert kinds(found) == [("content", "src/retry.py", 7)]
        assert found.results[0]["text"] == "hello Needle"

    def test_a_match_it_reports_outside_his_folder_is_dropped(self, folder, tmp_path, monkeypatch):
        rg = self._stub(
            tmp_path,
            [
                {
                    "type": "match",
                    "data": {"path": {"text": "/etc/hosts"}, "lines": {"text": "x"}, "line_number": 1},
                }
            ],
        )
        monkeypatch.setattr(workspace_search, "_ripgrep", lambda: rg)
        assert search("needle").results == []

    def test_a_ripgrep_that_will_not_run_falls_back_to_python(self, folder, monkeypatch, tmp_path):
        monkeypatch.setattr(workspace_search, "_ripgrep", lambda: str(tmp_path / "no-such-rg"))
        found = search("def backoff")
        assert found.engine == "python"
        assert kinds(found) == [("content", "src/retry.py", 3)]

    def test_a_ripgrep_that_errors_with_nothing_falls_back_to_python(self, folder, monkeypatch, tmp_path):
        rg = self._stub(tmp_path, [], code=2)
        monkeypatch.setattr(workspace_search, "_ripgrep", lambda: rg)
        found = search("def backoff")
        assert found.engine == "python"
        assert kinds(found) == [("content", "src/retry.py", 3)]


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


class TestTheRoute:
    def test_it_answers_with_results_and_whether_it_stopped(self, client, headers, folder):
        body = client.get("/api/workspace/search?q=backoff", headers=headers).get_json()
        assert [r["path"] for r in body["results"]] == ["backoff-plan", "src/retry.py"]
        assert body["truncated"] is False
        assert body["engine"] in ("rg", "python")

    def test_limit_is_honoured(self, client, headers, folder):
        body = client.get("/api/workspace/search?q=backoff&limit=1", headers=headers).get_json()
        assert len(body["results"]) == 1
        assert body["truncated"] is True

    def test_a_path_that_climbs_out_is_a_400(self, client, headers, folder):
        assert client.get("/api/workspace/search?q=x&path=../", headers=headers).status_code == 400

    def test_a_limit_that_is_not_a_number_is_a_400(self, client, headers, folder):
        assert client.get("/api/workspace/search?q=x&limit=lots", headers=headers).status_code == 400

    def test_searching_still_needs_the_token(self, client, folder):
        assert client.get("/api/workspace/search?q=backoff").status_code == 401
