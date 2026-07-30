"""The connection type: what it knows about itself, and what it refuses to leak.

Pure unit tests, because that purity is the point of the type. Everything here runs
without a network, a database, or a config file — which is exactly what made it worth
extracting from the four loose values it replaced.
"""

from __future__ import annotations

from kith.domain.connection import OLLAMA_DEFAULT_URL, Connection, ModelInfo, ProviderKind


class TestInfer:
    """Reading a stored config back, where no provider name was ever saved."""

    def test_openrouter_recognised_by_host(self):
        conn = Connection.infer("https://openrouter.ai/api/v1", "sk-or-x", "some/model")
        assert conn.kind is ProviderKind.OPENROUTER
        assert conn.supports_web_plugin

    def test_other_cloud_host_is_generic(self):
        conn = Connection.infer("https://integrate.api.nvidia.com/v1", "nvapi-x", "m")
        assert conn.kind is ProviderKind.OPENAI_COMPATIBLE
        # The `web` plugin is an OpenRouter extension; other hosts reject the whole
        # request rather than ignoring it, so this flag gates real behaviour.
        assert not conn.supports_web_plugin

    def test_no_key_means_local(self):
        # A base URL without a key cannot authenticate anywhere, so it is not cloud.
        assert Connection.infer("https://openrouter.ai/api/v1", "", "m").is_local
        assert Connection.infer("", "", "qwen3:8b").is_local

    def test_local_falls_back_to_the_default_address(self):
        assert Connection.local(model="m").endpoint == OLLAMA_DEFAULT_URL

    def test_endpoint_loses_its_trailing_slash(self):
        # Callers append "/chat/completions"; a double slash 404s on some hosts.
        assert Connection.infer("https://x.test/v1/", "k", "m").endpoint == "https://x.test/v1"


class TestProblems:
    """Structural completeness only — never reachability."""

    def test_a_local_connection_needs_only_a_model(self):
        assert Connection.local(model="qwen3:8b").is_complete
        assert Connection.local().problems() == ["Choose a model."]

    def test_cloud_needs_a_url_and_a_key(self):
        bare = Connection(kind=ProviderKind.OPENAI_COMPATIBLE, model="m")
        assert "This provider needs a base URL." in bare.problems()
        assert "This provider needs an API key." in bare.problems()

    def test_a_working_looking_connection_has_no_problems(self):
        assert Connection.openrouter(api_key="sk-or-x", model="some/model").is_complete


class TestSecrets:
    """A key must not escape through the two accidental exits: JSON and logs."""

    def test_public_says_whether_a_key_is_set_not_what_it_is(self):
        payload = Connection.openrouter(api_key="sk-or-secret", model="m").public()
        assert payload["apiKeySet"] is True
        assert "sk-or-secret" not in str(payload)

    def test_repr_is_safe_to_log(self):
        # Connections end up in tracebacks and log lines; the default dataclass repr
        # would print the key there.
        assert "sk-or-secret" not in repr(Connection.openrouter(api_key="sk-or-secret"))


class TestImmutability:
    def test_choosing_a_model_leaves_the_original_alone(self):
        original = Connection.openrouter(api_key="k")
        assert original.with_model("a/b").model == "a/b"
        # A half-edited candidate must never be mistaken for the live configuration.
        assert original.model == ""


class TestModelInfo:
    def test_unknown_pricing_is_not_free_pricing(self):
        assert not ModelInfo(id="m").is_free
        assert ModelInfo(id="m", prompt_per_mtok=0.0, completion_per_mtok=0.0).is_free

    def test_label_falls_back_to_the_id(self):
        assert ModelInfo(id="a/b").label == "a/b"
        assert ModelInfo(id="a/b", name="Pretty").label == "Pretty"
