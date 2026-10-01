"""`check_process` can wait for a background run to finish, so waiting is one call, not a poll.

Looking was its only verb. Between 2026-09-24 and 10-01 one test run, `diag`, was looked at sixteen
times with nothing changed between looks, and every look was a full round re-sending the turn.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from kith.engine.run import processes as process_service
from kith.tools import code


@pytest.fixture(autouse=True)
def a_clean_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(process_service, "_LOG_DIR", tmp_path / "processes", raising=False)
    process_service.processes._running.clear()
    yield
    for name in list(process_service.processes._running):
        try:
            process_service.processes.stop(name)
        except Exception:
            pass
    process_service.processes._running.clear()


def _check(**args) -> dict:
    return code.check_process(Path("."), args)


class TestWaiting:
    def test_it_returns_when_the_run_ends_with_everything_it_printed(self):
        process_service.processes.start("sleep 1; echo first; echo second", "short")

        started = time.monotonic()
        result = _check(name="short", wait=20)

        assert time.monotonic() - started < 10, "it waited out the whole wait instead of the run"
        assert result["alive"] is False and result["exitCode"] == 0
        assert "first" in result["output"] and "second" in result["output"]

    def test_it_gives_up_at_the_wait_and_says_it_is_still_going(self):
        process_service.processes.start("sleep 30", "long")

        started = time.monotonic()
        result = _check(name="long", wait=1)

        assert 0.9 <= time.monotonic() - started < 5
        assert result["alive"] is True

    def test_without_wait_it_looks_now(self):
        process_service.processes.start("sleep 30", "long")

        started = time.monotonic()
        result = _check(name="long")

        assert time.monotonic() - started < 1
        assert result["alive"] is True

    def test_the_wait_is_capped(self, monkeypatch):
        asked: list[float] = []
        monkeypatch.setattr(process_service.processes, "wait", lambda name, seconds: asked.append(seconds))
        process_service.processes.start("sleep 30", "long")

        _check(name="long", wait=100_000)

        assert asked == [code._MOST_WAIT]
