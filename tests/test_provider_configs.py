from modules.config.providers import litellm_config
from modules.config.providers.ollama_config import (
    get_ollama_keep_alive,
    get_ollama_options,
    get_ollama_timeout,
)
from modules.config.types import EmbeddingConfig, LLMConfig, MemoryLLMConfig, ModelProvider


class Env:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key):
        return self.values.get(key, "")


def _defaults():
    return {
        "llm": LLMConfig(
            provider=ModelProvider.LITELLM,
            model_id="openai/gpt-test",
            max_tokens=9000,
            temperature=0.2,
        ),
        "memory_llm": MemoryLLMConfig(
            provider=ModelProvider.AWS_BEDROCK,
            model_id="old-memory",
            max_tokens=1000,
            temperature=0.1,
        ),
        "evaluation_llm": LLMConfig(
            provider=ModelProvider.AWS_BEDROCK,
            model_id="old-eval",
            max_tokens=500,
            temperature=0.3,
        ),
        "swarm_llm": LLMConfig(
            provider=ModelProvider.AWS_BEDROCK,
            model_id="old-swarm",
            max_tokens=700,
            temperature=0.4,
        ),
        "embedding": EmbeddingConfig(
            provider=ModelProvider.AWS_BEDROCK,
            model_id="old-embedding",
            dimensions=1024,
        ),
    }


def test_split_litellm_model_id_handles_prefix_variant_and_models_alias():
    assert litellm_config.split_litellm_model_id("bedrock/claude:us") == (
        "bedrock",
        "claude",
        "claude:us",
    )
    assert litellm_config.split_litellm_model_id("models/gemini-pro") == (
        "gemini",
        "gemini-pro",
        "gemini-pro",
    )
    assert litellm_config.split_litellm_model_id("plain-model:v1") == (
        "",
        "plain-model",
        "plain-model:v1",
    )
    assert litellm_config.split_litellm_model_id(None) == ("", "", "")


def test_align_litellm_defaults_caps_tokens_and_aligns_related_llms(monkeypatch):
    defaults = _defaults()
    monkeypatch.setattr(litellm_config.litellm, "get_max_tokens", lambda model: 4096)
    monkeypatch.setattr(litellm_config.importlib.util, "find_spec", lambda name: object())

    litellm_config.align_litellm_defaults(defaults, Env())

    assert defaults["llm"].max_tokens == 4096
    assert defaults["llm"].parameters["max_tokens"] == 4096
    assert defaults["memory_llm"].model_id == "openai/gpt-test"
    assert defaults["memory_llm"].provider is ModelProvider.LITELLM
    assert defaults["evaluation_llm"].model_id == "openai/gpt-test"
    assert defaults["swarm_llm"].max_tokens == 4096
    assert defaults["embedding"].model_id == "openai/text-embedding-3-small"
    assert defaults["embedding"].dimensions == 1536


def test_align_litellm_defaults_uses_ollama_memory_model_for_ollama_embedding(monkeypatch):
    defaults = _defaults()
    monkeypatch.setattr(litellm_config.litellm, "get_max_tokens", lambda model: None)

    litellm_config.align_litellm_defaults(
        defaults, Env({"CYBER_AGENT_EMBEDDING_MODEL": "ollama/mxbai-embed-large:latest"})
    )

    assert defaults["memory_llm"].model_id == "ollama/llama3.2:3b"
    assert defaults["embedding"].model_id == "ollama/mxbai-embed-large:latest"
    assert defaults["embedding"].dimensions == 1024


def test_align_litellm_defaults_infers_unknown_embedding_dimensions(monkeypatch):
    defaults = _defaults()
    monkeypatch.setattr(litellm_config.litellm, "get_max_tokens", lambda model: None)

    litellm_config.align_litellm_defaults(
        defaults, Env({"CYBER_AGENT_EMBEDDING_MODEL": "custom-3-large"})
    )

    assert defaults["embedding"].model_id == "custom-3-large"
    assert defaults["embedding"].dimensions == 3072


def test_ollama_timeout_keep_alive_and_options(monkeypatch):
    warnings = []
    monkeypatch.setattr(
        "modules.config.providers.ollama_config.logger.warning",
        lambda *args: warnings.append(args),
    )

    assert get_ollama_timeout(Env({"OLLAMA_TIMEOUT": "15.5"})) == 15.5
    assert get_ollama_timeout(Env({"OLLAMA_TIMEOUT": "bad"})) == 120
    assert warnings
    assert get_ollama_keep_alive(Env({"OLLAMA_KEEP_ALIVE": "5m"})) == "5m"
    assert get_ollama_keep_alive(Env()) == "30m"
    assert get_ollama_options(Env({"OLLAMA_CONTEXT_LENGTH": "4096"})) == {"num_ctx": 4096}
    assert get_ollama_options(Env({"OLLAMA_CONTEXT_LENGTH": "1024"})) == {}
    assert get_ollama_options(Env({"OLLAMA_CONTEXT_LENGTH": "bad"})) == {}
