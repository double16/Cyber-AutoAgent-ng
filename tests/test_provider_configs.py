import asyncio
from types import SimpleNamespace

import pytest
import requests

from modules.config.providers import litellm_config
from modules.config.providers.ollama_config import (
    get_ollama_host,
    get_ollama_keep_alive,
    get_ollama_options,
    get_ollama_timeout,
)
from modules.config.types import (
    EmbeddingConfig,
    LLMConfig,
    MemoryLLMConfig,
    ModelProvider,
)


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


def test_loop_local_litellm_logging_worker_uses_distinct_workers_per_lifecycle():
    created_workers = []

    class FakeWorker:
        def __init__(self, timeout, max_queue_size):
            self.timeout = timeout
            self.max_queue_size = max_queue_size
            self.loop = asyncio.get_running_loop()
            self.calls = 0
            self.started = 0
            self.enqueued = 0
            self.cleared = 0
            created_workers.append(self)

        def ensure_initialized_and_enqueue(self, async_coroutine):
            self.calls += 1
            async_coroutine.close()

        def start(self):
            self.started += 1

        def enqueue(self, coroutine):
            self.enqueued += 1
            coroutine.close()

        async def stop(self):
            return None

        async def flush(self):
            return None

        async def clear_queue(self):
            self.cleared += 1

    proxy = litellm_config._LoopLocalLiteLLMLoggingWorker(
        SimpleNamespace(timeout=3.0, max_queue_size=7),
        FakeWorker,
    )

    async def record_once():
        async def noop():
            return None

        proxy.ensure_initialized_and_enqueue(noop())
        proxy.start()
        proxy.enqueue(noop())
        await proxy.clear_queue()
        await proxy.flush()
        await proxy.stop()

    asyncio.run(record_once())
    asyncio.run(record_once())

    assert len(created_workers) == 2
    assert created_workers[0] is not created_workers[1]
    assert [worker.calls for worker in created_workers] == [1, 1]
    assert [worker.started for worker in created_workers] == [1, 1]
    assert [worker.enqueued for worker in created_workers] == [1, 1]
    assert [worker.cleared for worker in created_workers] == [2, 1]
    assert all(worker.timeout == 3.0 for worker in created_workers)
    assert all(worker.max_queue_size == 7 for worker in created_workers)


def test_loop_local_litellm_logging_worker_retires_stopped_worker_before_reuse():
    created_workers = []

    class FakeWorker:
        def __init__(self, timeout, max_queue_size):
            self.stopped = 0
            self.cleared = 0
            created_workers.append(self)

        def ensure_initialized_and_enqueue(self, async_coroutine):
            async_coroutine.close()

        async def stop(self):
            self.stopped += 1

        async def flush(self):
            return None

        async def clear_queue(self):
            self.cleared += 1

    proxy = litellm_config._LoopLocalLiteLLMLoggingWorker(
        SimpleNamespace(timeout=3.0, max_queue_size=7),
        FakeWorker,
    )

    async def record_once():
        async def noop():
            return None

        proxy.ensure_initialized_and_enqueue(noop())
        await proxy.clear_queue()
        await proxy.stop()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(record_once())
        loop.run_until_complete(record_once())
    finally:
        loop.close()

    assert len(created_workers) == 2
    assert [worker.cleared for worker in created_workers] == [2, 1]
    assert [worker.stopped for worker in created_workers] == [2, 1]


def test_loop_local_litellm_logging_worker_closes_coroutine_without_running_loop():
    closed = []

    class Closeable:
        def close(self):
            closed.append(True)

    original = SimpleNamespace(timeout=1.0, max_queue_size=2, sentinel="fallback")
    proxy = litellm_config._LoopLocalLiteLLMLoggingWorker(original, object)

    proxy.ensure_initialized_and_enqueue(Closeable())
    assert closed == [True]
    assert proxy.sentinel == "fallback"


def test_loop_local_litellm_logging_worker_flush_on_exit_delegates():
    calls = []
    original = SimpleNamespace(timeout=1.0, max_queue_size=2, _flush_on_exit=lambda: calls.append("flushed"))
    proxy = litellm_config._LoopLocalLiteLLMLoggingWorker(original, object)

    proxy._flush_on_exit()

    assert calls == ["flushed"]


def test_configure_litellm_runtime_installs_logging_worker_proxy():
    from litellm.litellm_core_utils import logging_worker

    original_worker = logging_worker.GLOBAL_LOGGING_WORKER
    try:
        logging_worker.GLOBAL_LOGGING_WORKER = logging_worker.LoggingWorker()

        litellm_config.configure_litellm_runtime()
        first_proxy = logging_worker.GLOBAL_LOGGING_WORKER
        litellm_config.configure_litellm_runtime()

        assert first_proxy._caa_loop_local_proxy is True
        assert logging_worker.GLOBAL_LOGGING_WORKER is first_proxy
        assert litellm_config.litellm.drop_params is True
        assert litellm_config.litellm.modify_params is True
        assert litellm_config.litellm.num_retries == 5
        assert litellm_config.litellm.respect_retry_after_header is True
    finally:
        logging_worker.GLOBAL_LOGGING_WORKER = original_worker


def test_get_context_window_fallbacks_returns_none():
    assert litellm_config.get_context_window_fallbacks("litellm") is None


def test_align_litellm_defaults_ignores_missing_or_invalid_llm():
    defaults = {"llm": object()}
    litellm_config.align_litellm_defaults(defaults, Env())
    assert isinstance(defaults["llm"], object)

    defaults = _defaults()
    defaults["llm"].model_id = ""
    litellm_config.align_litellm_defaults(defaults, Env())
    assert defaults["llm"].model_id == ""


def test_align_litellm_defaults_handles_known_limit_without_cap_and_lookup_error(monkeypatch):
    defaults = _defaults()
    defaults["llm"].max_tokens = 100
    monkeypatch.setattr(litellm_config.litellm, "get_max_tokens", lambda model: 4096)

    litellm_config.align_litellm_defaults(defaults, Env())
    assert defaults["llm"].max_tokens == 100

    defaults = _defaults()
    monkeypatch.setattr(litellm_config.litellm, "get_max_tokens", lambda model: (_ for _ in ()).throw(ValueError()))
    litellm_config.align_litellm_defaults(defaults, Env())
    assert defaults["llm"].max_tokens == 9000


def test_align_litellm_defaults_embedding_dependency_errors(monkeypatch):
    defaults = _defaults()
    monkeypatch.setattr(litellm_config.litellm, "get_max_tokens", lambda model: None)
    monkeypatch.setattr(litellm_config.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(ImportError, match="google-genai"):
        litellm_config.align_litellm_defaults(defaults, Env({"CYBER_AGENT_EMBEDDING_MODEL": "models/text-embedding-004"}))

    defaults = _defaults()
    with pytest.raises(ImportError, match="sentence-transformers"):
        litellm_config.align_litellm_defaults(defaults, Env({"CYBER_AGENT_EMBEDDING_MODEL": "multi-qa-MiniLM-L6-cos-v1"}))


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


@pytest.mark.parametrize(
    ("model", "dimensions"),
    [
        ("custom-ada-002", 1536),
        ("custom-text-embedding-004", 768),
        ("custom-MiniLM", 384),
        ("custom-titan-v2", 1024),
        ("custom-unknown", 1536),
    ],
)
def test_align_litellm_defaults_infers_other_unknown_embedding_dimensions(monkeypatch, model, dimensions):
    defaults = _defaults()
    monkeypatch.setattr(litellm_config.litellm, "get_max_tokens", lambda model: None)

    litellm_config.align_litellm_defaults(defaults, Env({"CYBER_AGENT_EMBEDDING_MODEL": model}))

    assert defaults["embedding"].dimensions == dimensions


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


def test_get_ollama_host_prefers_explicit_environment_value(monkeypatch):
    monkeypatch.setattr("modules.config.providers.ollama_config.os.path.exists", lambda _path: True)

    assert get_ollama_host(Env({"OLLAMA_HOST": "http://configured:11434"})) == "http://configured:11434"


def test_get_ollama_host_uses_native_default_outside_docker(monkeypatch):
    monkeypatch.setattr("modules.config.providers.ollama_config.os.path.exists", lambda _path: False)

    assert get_ollama_host(Env()) == "http://localhost:11434"


def test_get_ollama_host_finds_second_docker_candidate(monkeypatch):
    calls = []
    monkeypatch.setattr("modules.config.providers.ollama_config.os.path.exists", lambda _path: True)

    def get(url, timeout):
        calls.append((url, timeout))
        return SimpleNamespace(status_code=503 if "localhost" in url else 200)

    monkeypatch.setattr("modules.config.providers.ollama_config.requests.get", get)

    assert get_ollama_host(Env()) == "http://host.docker.internal:11434"
    assert calls == [
        ("http://localhost:11434/api/version", 2),
        ("http://host.docker.internal:11434/api/version", 2),
    ]


def test_get_ollama_host_falls_back_after_docker_connection_errors(monkeypatch):
    monkeypatch.setattr("modules.config.providers.ollama_config.os.path.exists", lambda _path: True)
    monkeypatch.setattr(
        "modules.config.providers.ollama_config.requests.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.ConnectionError("unavailable")),
    )

    assert get_ollama_host(Env()) == "http://host.docker.internal:11434"
