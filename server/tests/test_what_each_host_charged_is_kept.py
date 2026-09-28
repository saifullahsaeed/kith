"""What each host charged is kept, and the history that predates keeping it is read back once.

Every round has written its cost and its host to the transcript since the `stats` event carried
them — 36,166 rounds with a host named, on this install, before anything summed them. So the
record starts by reading those, the first time a ranking asks for it and finds it empty, and from
then on each round is added as it happens. The two must never count one round twice: a round
that arrives before the record exists is already in its transcript, and is left to the fill.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import kith.settings
from kith.infra.db import repositories as repo
from kith.services import host_costs

MODEL = "deepseek/deepseek-v4.1-flash"


@pytest.fixture
def record(db, monkeypatch) -> Path:
    """The install's database, as the record reads it: through `settings`, at call time."""
    monkeypatch.setattr(kith.settings, "AGENT_DB_PATH", db)
    return db


def _stats(provider: str, prompt: int, cached: int, out: int, cost: float, model: str = MODEL) -> dict:
    """A round's `stats` exactly as `llm/openai_compat._stats` builds it."""
    return {
        "model": model,
        "provider": provider,
        "finishReason": "tool_calls",
        "generationId": "gen-1790541323-EcZrYhXJWAH1yZNAI7Q4",
        "promptTokens": prompt,
        "responseTokens": out,
        "reasoningTokens": out // 2,
        "cachedTokens": cached,
        "cacheWriteTokens": 0,
        "costUsd": cost,
        "tokensPerSecond": 149.3,
        "totalSeconds": 13.67,
        "loadSeconds": 0.0,
        "uncachedTokens": prompt - cached,
    }


def _transcript(directory: Path, name: str, lines: list[dict | str]) -> None:
    """Written the way `conversations.record_event` writes it: one `json.dumps` per line."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{name}.jsonl").open("w") as handle:
        for line in lines:
            handle.write((line if isinstance(line, str) else json.dumps(line)) + "\n")


def _event(stats: dict) -> dict:
    return {"type": "stats", "at": "2026-09-27T20:35:35.774474+00:00", "stats": stats}


def _real_shaped(directory: Path) -> None:
    """Two conversations the way they sit on disk, with the lines a fill has to step over."""
    _transcript(
        directory,
        "20260927-203500000-aaaaaa",
        [
            {
                "type": "message",
                "at": "2026-09-27T20:35:00+00:00",
                "role": "user",
                "content": "show me the stats",
            },
            _event(_stats("DeepSeek", 20693, 0, 2042, 0.00432915)),
            {
                "type": "tool_result",
                "at": "2026-09-27T20:35:40+00:00",
                "name": "read_file",
                "result": {"content": '{"type": "stats", "stats": {"provider": "Fake"}} quoted in a file'},
            },
            _event(_stats("DeepSeek", 24380, 22656, 688, 0.000739368)),
            _event(_stats("Together", 30000, 25000, 900, 0.0019)),
            # A round from before the host was recorded, a local model, and another model.
            _event({"promptTokens": 5000, "costUsd": 0.01}),
            _event(_stats("", 8000, 0, 100, 0.0)),
            _event(_stats("Google", 9000, 1000, 300, 0.004, model="google/gemini-3.7-flash")),
            # A crash mid-write costs the last line, and only the last line.
            '{"type": "stats", "at": "2026-09-27T20:40:00+00:00", "stats": {"model": "deepseek/deeps',
        ],
    )
    _transcript(
        directory, "20260928-090000000-bbbbbb", [_event(_stats("DeepSeek", 40000, 38000, 1200, 0.0011))]
    )


class TestTheHistoryIsReadBackOnce:
    def test_real_shaped_lines_are_summed_per_host(self, record, tmp_path):
        _real_shaped(tmp_path / "conversations")
        assert host_costs.fill(tmp_path / "conversations") == 5
        rows = {row["provider"]: row for row in repo.host_costs.for_model(record, MODEL)}
        assert set(rows) == {"DeepSeek", "Together"}
        assert rows["DeepSeek"]["rounds"] == 3
        assert rows["DeepSeek"]["prompt_tokens"] == 20693 + 24380 + 40000
        assert rows["DeepSeek"]["cached_tokens"] == 22656 + 38000
        assert rows["DeepSeek"]["completion_tokens"] == 2042 + 688 + 1200
        assert rows["DeepSeek"]["cost_usd"] == pytest.approx(0.00432915 + 0.000739368 + 0.0011)
        assert repo.host_costs.for_model(record, "google/gemini-3.7-flash")[0]["rounds"] == 1

    def test_the_first_ranking_to_ask_is_what_fills_it(self, record, tmp_path, monkeypatch):
        _real_shaped(tmp_path / "conversations")
        monkeypatch.setattr(host_costs, "_transcripts", lambda: tmp_path / "conversations")
        bills = {one.provider: one for one in host_costs.bills(MODEL)}
        assert bills["Together"].rounds == 1
        assert bills["DeepSeek"].cost_usd == pytest.approx(0.00432915 + 0.000739368 + 0.0011)

    def test_a_filled_record_is_never_filled_over(self, record, tmp_path, monkeypatch):
        """Rounds observed since the fill are in the transcripts too. Reading them again over
        the top would count every one of them twice."""
        _real_shaped(tmp_path / "conversations")
        monkeypatch.setattr(host_costs, "_transcripts", lambda: tmp_path / "conversations")
        host_costs.fill()
        host_costs.observe(_stats("DeepSeek", 50000, 49000, 700, 0.0009))
        assert host_costs.fill() == 0
        assert {one.provider: one.rounds for one in host_costs.bills(MODEL)} == {"DeepSeek": 4, "Together": 1}

    def test_no_transcripts_is_an_empty_record_and_not_an_error(self, record, tmp_path):
        assert host_costs.fill(tmp_path / "nothing-here") == 0
        assert host_costs.bills(MODEL) == []


class TestEachRoundIsAdded:
    def _started(self, record, tmp_path):
        _transcript(tmp_path / "conversations", "c", [_event(_stats("DeepSeek", 1000, 900, 10, 0.0001))])
        host_costs.fill(tmp_path / "conversations")

    def test_a_round_is_added_to_its_host(self, record, tmp_path):
        self._started(record, tmp_path)
        host_costs.observe(_stats("DeepSeek", 2000, 1800, 20, 0.0002))
        host_costs.observe(_stats("Morph", 3000, 1000, 30, 0.0009))
        rows = {row["provider"]: row for row in repo.host_costs.for_model(record, MODEL)}
        assert (rows["DeepSeek"]["rounds"], rows["DeepSeek"]["prompt_tokens"]) == (2, 3000)
        assert rows["DeepSeek"]["cost_usd"] == pytest.approx(0.0003)
        assert (rows["Morph"]["rounds"], rows["Morph"]["cached_tokens"]) == (1, 1000)

    def test_a_round_before_the_record_exists_is_left_to_the_fill(self, record, tmp_path, monkeypatch):
        """It is in its transcript already. Counted here as well, the fill would count it again
        — or, worse, find the record no longer empty and never read the history at all."""
        host_costs.observe(_stats("DeepSeek", 2000, 1800, 20, 0.0002))
        assert not repo.host_costs.started(record)
        _transcript(tmp_path / "conversations", "c", [_event(_stats("DeepSeek", 2000, 1800, 20, 0.0002))])
        monkeypatch.setattr(host_costs, "_transcripts", lambda: tmp_path / "conversations")
        assert [one.rounds for one in host_costs.bills(MODEL)] == [1]

    def test_a_round_with_no_host_is_not_a_bill(self, record, tmp_path):
        """A local model or a plain OpenAI-compatible endpoint names no host."""
        self._started(record, tmp_path)
        host_costs.observe(_stats("", 2000, 0, 20, 0.0))
        host_costs.observe({"promptTokens": 10})
        assert [row["rounds"] for row in repo.host_costs.for_model(record, MODEL)] == [1]

    def test_a_failure_to_record_never_reaches_the_turn(self, record, monkeypatch):
        def broken(*_args, **_kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(repo.host_costs, "add", broken)
        host_costs.observe(_stats("DeepSeek", 2000, 1800, 20, 0.0002))
