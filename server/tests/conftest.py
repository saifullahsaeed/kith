"""Shared fixtures.

Every test gets a real SQLite database built by the real migrations, in a temp
directory. Not mocks: the things that actually broke this codebase were schema and
transaction behaviour, and a mocked database cannot fail the way those did.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

import kith.settings
from kith.infra.db import config_store
from kith.infra.db.migrations import init


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A fresh, fully-migrated agent database."""
    path = tmp_path / "agent.db"
    init(path)
    return path


@pytest.fixture
def config_db(tmp_path: Path) -> Path:
    """A fresh config database, seeded exactly as a first run would be."""
    path = tmp_path / "config.db"
    config_store.init(path)
    return path


@pytest.fixture(autouse=True)
def never_the_real_database(tmp_path_factory, monkeypatch):
    """No test can reach `server/data/agent.db`, whatever it forgets to patch.

    This is not hygiene. The suite was running real agent turns against the real board, and
    the real tick log has the receipts:

        09:36:37  start  27 tools  759,681 tokens in   "Completed the inspection step for task #110."
        09:25:06  start  12 tools  265,822 tokens in   "Created work/task-110.md and added a four-s…"

    `test_a_new_step_clears_a_stale_cancel` calls `runner._safe_tick(forced=True)` with no
    temp database and no stubbed model. It therefore picked the top of the *real* board and
    worked it: a million prompt tokens of somebody's money per suite run, files written into
    the workspace, and task #110 advanced by a test asserting something about a cancel flag.
    It also explains a 145-second test.

    Chasing the call sites is the wrong fix — `test_stopping_a_step.py` alone builds six
    runners and patches the path once, and the next one written will forget too. Thirteen
    modules do `from kith.settings import AGENT_DB_PATH`, which copies the value, so patching
    `kith.settings` alone does nothing; every holder gets its own patch. A test that wants its
    own database still monkeypatches over this, which is what every existing one already does.
    """
    safe = tmp_path_factory.mktemp("never-real") / "agent.db"
    init(safe)
    for name, module in list(sys.modules.items()):
        if name.startswith("kith.") and hasattr(module, "AGENT_DB_PATH"):
            monkeypatch.setattr(module, "AGENT_DB_PATH", safe, raising=False)
    yield safe


#: The real data folder, captured before any fixture redirects it. Tests that are *about*
#: where data lives need the true answer, and by the time they run the attribute is a
#: temp directory.
REAL_DATA_DIR = Path(kith.settings.DATA_DIR)


@pytest.fixture(scope="session")
def _safe_data_dir(tmp_path_factory) -> Path:
    """One temp data folder for the whole session, with the skills copied into it.

    Session-scoped for the copy: the installed skills are a few megabytes, and doing that
    per test would cost more than the rest of the suite. Sharing one folder across tests is
    not a regression — they already shared one, it was just the real one.

    Copied rather than symlinked, and that distinction is load-bearing. A symlink resolves to
    its target, so the permission layer saw the real `server/data/skills`, decided it was
    outside the workspace, and refused to describe writing a skill at all. The copy keeps
    every resolved path inside the temp tree, which is the property the redirect is for.
    """
    safe = tmp_path_factory.mktemp("never-real-data")
    real_skills = REAL_DATA_DIR / "skills"
    if real_skills.is_dir():
        shutil.copytree(real_skills, safe / "skills")
    return safe


@pytest.fixture(autouse=True)
def never_the_real_data_folder(_safe_data_dir, monkeypatch):
    """No test can write into `server/data`, which is the user's data, not the suite's.

    The sibling fixture above isolates the database and stops there, and that turned out to
    be half the problem: `workspace.internal()` does not derive from `AGENT_DB_PATH` at all.
    It reads `settings.DATA_DIR`, so a redirected database and a real transcript folder are
    entirely consistent states. Every test that opened a conversation therefore wrote a real
    `.jsonl` beside the real databases.

    It is not theoretical and it is not small. Emptying the folder and running `./check` once
    put 54 transcripts straight back into it, all stamped within the same second. On a
    machine that had been running the suite for weeks there were 4,491. They are invisible
    while they accumulate — nothing fails, the folder is just quietly not the user's any more.

    Redirected wholesale rather than per-subfolder. `DATA_DIR` is also where `api.token` and
    the config database live, and picking off `conversations/` would leave the next writer to
    be discovered the same way this one was — by noticing litter.
    """
    monkeypatch.setattr(kith.settings, "DATA_DIR", _safe_data_dir)
    # `kith.config` copies the value at import, as it does with the database path.
    for name, module in list(sys.modules.items()):
        if name.startswith("kith.") and hasattr(module, "DATA_DIR"):
            monkeypatch.setattr(module, "DATA_DIR", _safe_data_dir, raising=False)
    yield _safe_data_dir


@pytest.fixture(autouse=True)
def never_the_real_config(tmp_path_factory, monkeypatch):
    """No test can read `server/data/config.db`, which holds the developer's API key.

    The third of these, and it was missing for the same reason the other two were: the value it
    guards is computed at import and copied by whoever imports it. `never_the_real_database`
    redirects `AGENT_DB_PATH` and `never_the_real_data_folder` redirects `DATA_DIR`, but
    `CONFIG_DB_PATH` is `DATA_DIR / "config.db"` resolved once at import of `kith.settings` —
    so patching `DATA_DIR` afterwards does nothing to it, and the suite has been reading the
    real file all along.

    Two things follow from that, and the second is why this is not merely hygiene:

    * **It can see the key.** `SELECT key FROM settings` on a developer's config database
      returns `api_key`, `base_url`, `model`, `model_capabilities`. Any test that reaches
      `default_config()` without patching the path itself — most of them — resolves against
      whatever that developer happens to have configured, so a suite that passes here can fail
      on a machine with a different model selected.
    * **It is why a clean checkout fails.** There is no `server/data/config.db` on a fresh
      clone, and `config_store.load_settings` on a database that was never `init`-ed raises
      `sqlite3.OperationalError: no such table: settings`. That is the "~200 tests fail on
      environment" that `./check`'s own header describes and that `./check lint` exists to work
      around.

    Initialised rather than merely redirected, because an empty path is the clean-checkout
    failure with extra steps.
    """
    path = tmp_path_factory.mktemp("never-real-config") / "config.db"
    config_store.init(path)
    for name, module in list(sys.modules.items()):
        if name.startswith("kith.") and hasattr(module, "CONFIG_DB_PATH"):
            monkeypatch.setattr(module, "CONFIG_DB_PATH", path, raising=False)
    yield path


@pytest.fixture(autouse=True)
def never_a_stale_permission_cache():
    """No test inherits another's idea of which folders are linked projects.

    `permissions._linked` caches `linked_directories(AGENT_DB_PATH)` for `_LINKED_TTL` — five
    seconds — so that a permission check on every write does not re-query the database. Nothing
    reset it between tests, and each test gets its own database, so a test that ran within five
    seconds of one that populated the cache was answered from the *previous* test's projects.

    The failure is a refusal, not an error: writing inside your own linked project is allowed by
    `_inside_linked_project`, and a stale cache says the folder is not one. Reproduced by running
    `test_work_lands_in_the_linked_folder.py` immediately before `test_checkpoints.py` — ten
    checkpoint tests refuse with "he wants to write to something outside his workspace".

    **It is timing-dependent, which is the worst part.** Five seconds is long enough to cover a
    neighbouring file and short enough to expire while someone is watching a slow run, so the
    same commit passes or fails depending on how fast the tests before it happened to be. Any
    "works on my machine" in this suite should be suspected of being this.
    """
    from kith.infra import permissions

    permissions.forget_linked_projects()
    yield
    permissions.forget_linked_projects()


@pytest.fixture(autouse=True)
def no_stray_background_threads(monkeypatch):
    """No test starts a background thread unless it asks for one. None of them ask.

    `scheduler.start()` runs a daemon thread that wakes every thirty seconds for the rest of
    the process. Nothing stops one, so a thread started in one test goes on firing through the
    next — reading `AGENT_DB_PATH` out of the module, which the following test monkeypatches at
    its own temp database. It would duly start firing someone else's fixtures.

    This guarded the autonomy loop first, and the loop is gone; the hazard is not. It belonged
    to *having a timer at all*, not to what the timer did, which is why this fixture outlived
    the thing that earned it.

    The first attempt stopped the threads in teardown, and that was wrong in a way worth
    recording, because it made things *worse* rather than merely slow:

    * Autouse fixtures are set up first, so they tear down LAST — after `monkeypatch` has
      already restored `AGENT_DB_PATH`. In that window a still-running timer is pointed at
      the real `server/data/agent.db`, and firing a reminder reaches `stream_agent`, which is
      a real network call.
    * Measured: nine of thirteen joins hit the full two-second timeout with the thread still
      alive — because it was busy doing exactly that. It cost 4 seconds of teardown on every
      test in the file, and turned a twenty-second suite into one that did not finish.

    Stopping a thread you should never have started is the wrong end of the problem. Nothing
    in the suite asserts the thread exists, so it simply does not start.
    """
    from kith.services import scheduler

    monkeypatch.setattr(scheduler, "start", lambda resume: None)
    yield


@pytest.fixture(autouse=True)
def isolated_tuning(tmp_path_factory):
    """Every test resolves tunables against its own empty database.

    Without this, the loop-detection and agent-loop tests would read whichever values
    the developer running them happens to have saved — so a thoughtful change to, say,
    the stall threshold would break the suite on one machine and not another.
    """
    from kith.infra.db import config_store
    from kith.services import tuning

    path = tmp_path_factory.mktemp("tuning") / "config.db"
    config_store.init(path)
    tuning.use_database(path)
    yield path
    tuning.use_database(None)
