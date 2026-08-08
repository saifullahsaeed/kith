"""What a turn actually cost, rather than a token count standing in for it.

The provider has been telling us all along. Every call goes out with
``usage: {include: true}`` — and the comment where that is set says it returns "real cost" —
and the parser read prompt_tokens, completion_tokens and the cache fields, and dropped the
rest on the floor. So every surface in the app reported spend as a token count.

That is not a small approximation. Measured on a real "say pong" turn: 38,116 prompt tokens
of which 20,700 were served from cache, 79 completion tokens of which 47 were reasoning, for
$0.002. A cached token and a fresh one differ by an order of magnitude, so the headline
number was off by whatever the cache happened to save that day.
"""

from __future__ import annotations

from kith.llm.openai_compat import _stats

#: The exact usage object OpenRouter returned for a one-word completion, copied from the
#: wire rather than invented. Fields we do not read are kept so this fails if the shape
#: changes underneath us.
REAL = {
    "prompt_tokens": 1526,
    "completion_tokens": 63,
    "total_tokens": 1589,
    "cost": 0.0001904,
    "is_byok": False,
    "prompt_tokens_details": {
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "audio_tokens": 0,
        "video_tokens": 0,
    },
    "cost_details": {
        "upstream_inference_cost": 0.0001904,
        "upstream_inference_prompt_cost": 0.0001526,
        "upstream_inference_completions_cost": 3.78e-05,
    },
    "completion_tokens_details": {"reasoning_tokens": 31, "image_tokens": 0, "audio_tokens": 0},
}


class TestReadingWhatTheProviderSent:
    def test_the_cost_is_taken_from_the_bill_not_estimated(self):
        assert _stats(REAL, 1.0)["costUsd"] == 0.0001904

    def test_reasoning_is_counted_separately(self):
        # 31 of 63 completion tokens on this call. A reasoning model's "response tokens" is
        # mostly thinking, and a total that hides it reads as text someone could go and find.
        assert _stats(REAL, 1.0)["reasoningTokens"] == 31

    def test_the_ordinary_counts_still_come_through(self):
        out = _stats(REAL, 2.0)
        assert out["promptTokens"] == 1526
        assert out["responseTokens"] == 63

    def test_a_provider_that_reports_no_cost_gives_zero_not_a_crash(self):
        # Ollama and strict OpenAI-compatible hosts send no cost field at all.
        out = _stats({"prompt_tokens": 10, "completion_tokens": 2}, 1.0)
        assert out["costUsd"] == 0.0
        assert out["reasoningTokens"] == 0

    def test_the_model_is_recorded_so_cost_is_attributable(self):
        # A bill spanning a model switch cannot be split back apart after the fact
        # unless each row says which model produced it — the token stats alone can't.
        assert _stats(REAL, 1.0, "openai/gpt-5.6-luna")["model"] == "openai/gpt-5.6-luna"

    def test_the_model_defaults_to_empty_when_unknown(self):
        # Old callers pass no model; the field is present but blank rather than absent,
        # so a reader never has to guess whether "no model" means unknown or unwritten.
        assert _stats(REAL, 1.0)["model"] == ""

    def test_no_usage_at_all_is_survivable(self):
        assert _stats(None, 1.0)["costUsd"] == 0.0


class TestTheLocalPathIsAttributableToo:
    def test_ollama_rows_carry_the_model_name(self):
        # The local transport records stats too; a row with no model is unattributable
        # whichever transport made it, so both must name their model.
        from kith.llm.ollama import _stats_from_done

        done = {"model": "qwen3:4b", "prompt_eval_count": 100, "eval_count": 20}
        assert _stats_from_done(done, "qwen3:4b")["model"] == "qwen3:4b"


class TestTheCacheRatioThatWasNotARatio:
    def _snapshot(self, **totals):
        from kith.services import agent_loop

        saved = dict(agent_loop._usage)
        try:
            agent_loop._usage.update(totals)
            return agent_loop.usage_snapshot()
        finally:
            agent_loop._usage.clear()
            agent_loop._usage.update(saved)

    def test_with_no_writes_it_declines_to_answer(self):
        """It used to divide by `writes or 1` and return the read count unchanged.

        So a field documented as "above 1 means reads are outrunning writes" reported 20,700
        — a raw count wearing a ratio's name, indistinguishable from a genuine 20,700-to-1.
        Most providers report no writes at all (OpenAI caches automatically and charges
        nothing to put something there), so this was the common case, not the edge.
        """
        snap = self._snapshot(promptTokens=38116, cachedTokens=20700, cacheWriteTokens=0)
        assert snap["cacheEfficiency"] is None
        assert snap["cachedTokens"] == 20700, "the count itself is still reported, just not as a ratio"

    def test_with_writes_it_is_a_real_ratio(self):
        snap = self._snapshot(promptTokens=1000, cachedTokens=800, cacheWriteTokens=400)
        assert snap["cacheEfficiency"] == 2.0

    def test_the_hit_rate_is_unaffected(self):
        snap = self._snapshot(promptTokens=38116, cachedTokens=20700, cacheWriteTokens=0)
        assert snap["cacheHitRate"] == 0.543
