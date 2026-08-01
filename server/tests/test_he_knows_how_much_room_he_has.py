"""How much room the model has, known at runtime rather than discovered by a 400.

`context_length` was read from the catalogue when a model was chosen — used once, for the
picker's "usable context" concern — and then thrown away. Nothing knew it afterwards, so the
only way to find the limit was to exceed it: the provider returns HTTP 400, the transport
yields a generic "Cloud model returned 400", and the turn dies with no compaction, no retry
and nothing said about why.

The number is a fact about the model, not a setting, which is why it resolves on Config and
is not in the tuning registry. 0 means "nobody knows" and has to stay expressible: a window
guessed too high never fires the guard while every turn 400s, and guessed too low it
truncates work that would have fitted.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace

import pytest

from kith.config import CONTEXT_KEY, Config, default_config, merge_overrides

#: The manager *module*. `from kith.services.connections import manager` gives the
#: ConnectionManager instance the package re-exports under that name, not the module — the
#: same shadowing that broke `create_app` at startup an hour ago, walked into twice.
MANAGER = sys.modules["kith.services.connections.manager"]


@pytest.fixture
def cloud(config_db, monkeypatch):
    """A stored cloud connection whose settings we can rewrite per test."""
    import kith.config as config_module
    from kith.infra.db import config_store

    monkeypatch.setattr(config_module, "CONFIG_DB_PATH", config_db)
    for key in ("KITH_BASE_URL", "KITH_MODEL", "KITH_NUM_CTX"):
        monkeypatch.delenv(key, raising=False)

    def store(**settings):
        config_store.update_settings(config_db, settings)
        return default_config()

    store(base_url="https://openrouter.ai/api/v1", model="acme/big")
    return store


class TestWhereTheNumberComesFrom:
    def test_a_cloud_model_uses_what_the_catalogue_said(self, cloud):
        config = cloud(**{CONTEXT_KEY: json.dumps({"model": "acme/big", "context": 200_000})})
        assert config.context_window == 200_000

    def test_a_local_model_uses_the_number_we_actually_send(self, cloud):
        """Not a fallback. For Ollama we *send* num_ctx, so it is the window by
        construction — more authoritative than any catalogue could be."""
        config = cloud(base_url="", num_ctx=8_192)
        assert config.context_window == 8_192

    def test_nothing_recorded_means_nothing_claimed(self, cloud):
        assert cloud().context_window == 0

    def test_a_corrupt_record_means_nothing_claimed(self, cloud):
        assert cloud(**{CONTEXT_KEY: "not json at all"}).context_window == 0

    def test_a_negative_or_nonsense_context_is_refused(self, cloud):
        for value in (-1, "big", None):
            config = cloud(**{CONTEXT_KEY: json.dumps({"model": "acme/big", "context": value})})
            assert config.context_window == 0


class TestItCannotOutliveTheModelItDescribes:
    """The reason the record is a pair rather than a number.

    `PATCH /api/config` writes `model` without going through adoption, so a bare figure would
    survive a model switch. A stale window is worse than none in both directions: left high
    the guard never fires and every turn 400s; left low it truncates work that fitted.
    """

    def test_a_window_for_another_model_is_not_used(self, cloud):
        config = cloud(
            model="acme/small",
            **{CONTEXT_KEY: json.dumps({"model": "acme/big", "context": 200_000})},
        )
        assert config.context_window == 0, "a 200k window survived a switch to another model"

    def test_and_it_comes_back_when_the_model_comes_back(self, cloud):
        stored = json.dumps({"model": "acme/big", "context": 200_000})
        assert cloud(model="acme/small", **{CONTEXT_KEY: stored}).context_window == 0
        assert cloud(model="acme/big").context_window == 200_000

    def test_a_record_with_no_model_is_not_trusted(self, cloud):
        """The shape written before the pairing existed. It must read as unknown rather than
        as a confident number for whatever model happens to be selected now."""
        assert cloud(**{CONTEXT_KEY: json.dumps({"context": 200_000})}).context_window == 0


class TestItSurvivesTheRequestPath:
    """`merge_overrides` rebuilds Config field by field, so a field left out of it is not
    inherited — it silently resets to the dataclass default on every chat request. The
    window would then be right for a tick and zero for chat, which is the harder to notice."""

    def base(self, **kwargs) -> Config:
        return replace(default_config(), **kwargs)

    def test_a_chat_request_keeps_the_window(self):
        merged = merge_overrides(self.base(base_url="https://x", context_window=200_000), {})
        assert merged.context_window == 200_000

    def test_an_unrelated_override_does_not_drop_it(self):
        merged = merge_overrides(self.base(base_url="https://x", context_window=200_000), {"effort": "high"})
        assert merged.context_window == 200_000

    def test_overriding_num_ctx_locally_moves_the_window_with_it(self):
        """On the local path the window *is* num_ctx, because that is the number sent. A
        caller who overrides it for one request has changed the window for that request."""
        merged = merge_overrides(self.base(base_url="", num_ctx=40_960), {"numCtx": 8_192})
        assert merged.num_ctx == 8_192
        assert merged.context_window == 8_192

    def test_overriding_num_ctx_on_a_cloud_model_does_not(self):
        """num_ctx is not sent to a cloud endpoint, so it says nothing about the window."""
        merged = merge_overrides(self.base(base_url="https://x", context_window=200_000), {"numCtx": 8_192})
        assert merged.context_window == 200_000


class TestTheBackfill:
    def test_it_writes_the_pair_that_the_reader_expects(self, config_db, monkeypatch):
        """Adoption and the backfill must agree on the shape, or one of them writes a record
        the other silently reads as unknown."""
        import kith.config as config_module
        from kith.infra.db import config_store

        monkeypatch.setattr(config_module, "CONFIG_DB_PATH", config_db)
        for key in ("KITH_BASE_URL", "KITH_MODEL"):
            monkeypatch.delenv(key, raising=False)
        config_store.update_settings(
            config_db,
            {
                "base_url": "https://openrouter.ai/api/v1",
                "model": "acme/big",
                CONTEXT_KEY: json.dumps({"model": "acme/big", "context": 128_000}),
            },
        )
        assert default_config().context_window == 128_000

    def test_the_writer_and_the_reader_share_one_constant(self):
        """Not two strings that happen to match today. A key defined twice is a key that
        drifts once, and the failure is silent: the writer stores a record the reader reads
        as unknown, and the guard is simply off forever."""
        assert MANAGER.CONTEXT_KEY is CONTEXT_KEY

    def test_the_backfill_leaves_a_record_the_reader_accepts(self, config_db, monkeypatch):
        """End to end, with the catalogue stubbed: what the backfill writes must be what
        `default_config` reads back, or the two halves are correct and useless."""
        import kith.config as config_module
        from kith.domain.connection import ModelInfo
        from kith.infra.db import config_store
        from kith.services.connections import providers

        monkeypatch.setattr(config_module, "CONFIG_DB_PATH", config_db)
        for key in ("KITH_BASE_URL", "KITH_MODEL"):
            monkeypatch.delenv(key, raising=False)
        config_store.update_settings(
            config_db, {"base_url": "https://openrouter.ai/api/v1", "model": "acme/big", "api_key": "k"}
        )
        assert default_config().context_window == 0, "nothing stored yet"

        class Catalogue:
            def list_models(self, _connection):
                return [ModelInfo(id="acme/big", context=250_000)]

        monkeypatch.setattr(providers, "for_kind", lambda _kind: Catalogue())
        monkeypatch.setattr(MANAGER.threading, "Thread", _RunsImmediately)
        MANAGER.backfill_context_window_async(config_db)

        assert default_config().context_window == 250_000


class _RunsImmediately:
    """A Thread stand-in that runs on `start()`, so the test does not race the backfill."""

    def __init__(self, target=None, name="", daemon=False):
        self._target = target

    def start(self):
        self._target()
