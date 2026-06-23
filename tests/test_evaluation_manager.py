import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.evaluation import manager as mod
from modules.evaluation import evaluation as eval_mod
from modules.prompts import factory as prompts

class RecordingEmitter:
    def emit(self, event):
        pass


def test_register_filter_and_summary():
    manager = mod.EvaluationManager("OP_TEST", emitter=RecordingEmitter())

    manager.register_trace("t1", mod.TraceType.MAIN_AGENT, "s1", "Main", {"x": 1})
    manager.register_trace("t2", mod.TraceType.REPORT_GENERATION, "s2", "Report")
    manager.traces["t1"].evaluated = True
    manager.traces["t1"].evaluation_scores = {"score": 1.0}

    assert manager.get_trace_ids_by_type(mod.TraceType.MAIN_AGENT) == ["t1"]
    assert [trace.trace_id for trace in manager.get_unevaluated_traces()] == ["t2"]

    summary = manager.get_summary()
    assert summary["operation_id"] == "OP_TEST"
    assert summary["total_traces"] == 2
    assert summary["evaluated_traces"] == 1
    assert summary["evaluation_complete"] is False
    assert summary["by_type"]["main_agent"] == {"total": 1, "evaluated": 1}
    assert summary["traces"][0]["score_count"] == 1


async def _fake_scores(trace_id, _max_retries):
    if trace_id == "s1":
        return {"plain": 0.5, "tuple": (0.75, {"reason": "ok"}), "bad": "skip"}
    return {}


@pytest.mark.asyncio
async def test_evaluate_all_traces_normalizes_scores_and_marks_evaluated(monkeypatch):
    class FakeEvaluator:
        def __init__(self, emitter):
            self.emitter = emitter

        async def evaluate_trace(self, trace_id, _max_retries):
            return await _fake_scores(trace_id, _max_retries)

    monkeypatch.setattr(mod, "CyberAgentEvaluator", FakeEvaluator)
    manager = mod.EvaluationManager("OP_TEST", emitter=RecordingEmitter())
    manager.register_trace("t1", mod.TraceType.MAIN_AGENT, "s1", "Main")
    manager.register_trace("t2", mod.TraceType.SWARM_AGENT, "s2", "Swarm")

    results = await manager.evaluate_all_traces()

    assert results == {"t1": {"plain": 0.5, "tuple": 0.75}}
    assert manager.traces["t1"].evaluated is True
    assert manager.traces["t1"].evaluation_scores == {"plain": 0.5, "tuple": 0.75}
    assert manager.traces["t2"].evaluated is False


def test_wait_for_completion_without_thread_returns_true():
    manager = mod.EvaluationManager("OP_TEST", emitter=RecordingEmitter())

    assert manager.wait_for_completion(timeout=0) is True


def test_trigger_async_evaluation_runs_once(monkeypatch):
    manager = mod.EvaluationManager("OP_TEST", emitter=RecordingEmitter())
    calls = []

    async def fake_evaluate_all_traces():
        calls.append("called")
        return {}

    manager.evaluate_all_traces = fake_evaluate_all_traces
    manager.trigger_async_evaluation()

    assert manager.wait_for_completion(timeout=2) is True
    assert calls == ["called"]


def test_trigger_async_evaluation_skips_when_thread_alive(monkeypatch):
    manager = mod.EvaluationManager("OP_TEST", emitter=RecordingEmitter())
    manager._evaluation_thread = type("Thread", (), {"is_alive": lambda self: True})()

    manager.trigger_async_evaluation()

    assert manager._evaluation_complete.is_set() is False



class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else {"prompt": "remote prompt"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.body).encode("utf-8")


def test_langfuse_prompt_helpers_cache_seed_and_remote(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("ENABLE_LANGFUSE_PROMPTS", "true")
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", "test")
    monkeypatch.setattr(prompts, "_lf_is_docker", lambda: False)
    prompts._LF_CACHE.clear()
    monkeypatch.setattr(prompts, "_LF_SEEDED", False)

    calls = []

    def fake_urlopen(req, data=None, timeout=0):
        calls.append((req.full_url, data, timeout))
        if data:
            return FakeResponse(body={"id": "created"})
        return FakeResponse(body={"prompt": [{"content": "chat one"}, {"content": "chat two"}]})

    monkeypatch.setattr(prompts._urlreq, "urlopen", fake_urlopen)

    remote = prompts._lf_get_prompt(prompts.LF_SYSTEM_PROMPT_NAME, "test")
    assert remote["prompt"][0]["content"] == "chat one"
    assert prompts._lf_get_prompt(prompts.LF_SYSTEM_PROMPT_NAME, "test") is remote
    prompts._LF_CACHE[prompts._lf_ck(prompts.LF_SYSTEM_PROMPT_NAME, "test")]["ts"] = 0
    assert prompts._lf_cache_get(prompts.LF_SYSTEM_PROMPT_NAME, "test") is None

    created = prompts._lf_create_prompt_version(name="n", prompt_text="p", label="test")
    assert created["id"] == "created"
    assert prompts._lf_resolve_template_text("system_prompt.md") == "chat one\nchat two"
    assert prompts._lf_resolve_template_text("missing.md") == ""

    monkeypatch.setattr(prompts, "_lf_get_prompt", Mock(return_value=None))
    monkeypatch.setattr(prompts, "_lf_read_local_template", lambda _name: "local template")
    create = Mock(return_value={"id": "seed"})
    monkeypatch.setattr(prompts, "_lf_create_prompt_version", create)
    prompts._lf_ensure_seeded()
    assert create.called
    assert prompts._LF_SEEDED is True


def test_evaluator_setup_models_all_providers(monkeypatch):
    class FakeWrapper:
        def __init__(self, inner):
            self.inner = inner

    class FakeEvaluator(eval_mod.CyberAgentEvaluator):
        def __init__(self):
            self._emitter = SimpleNamespace(emit=Mock())

    manager = SimpleNamespace(
        provider="ollama",
        get_provider=lambda: manager.provider,
        get_server_config=lambda _provider: SimpleNamespace(
            evaluation=SimpleNamespace(llm=SimpleNamespace(model_id="eval-model")),
            embedding=SimpleNamespace(model_id="embed-model"),
        ),
        getenv=lambda name, default=None: {"OLLAMA_HOST": "http://ollama", "MEM0_EMBEDDING_MODEL": "bedrock/embed"}.get(name, default),
        get_default_region=lambda: "us-east-1",
    )
    monkeypatch.setattr(eval_mod, "get_config_manager", lambda: manager)
    monkeypatch.setattr(eval_mod, "LangchainLLMWrapper", FakeWrapper)
    monkeypatch.setattr(eval_mod, "LangchainEmbeddingsWrapper", FakeWrapper)
    for attr in ["ChatOllama", "OllamaEmbeddings", "ChatLiteLLM", "BedrockEmbeddings", "ChatGoogleGenerativeAI", "GoogleGenerativeAIEmbeddings", "ChatBedrock"]:
        monkeypatch.setattr(eval_mod, attr, lambda **kwargs: SimpleNamespace(kwargs=kwargs))

    evaluator = FakeEvaluator()
    for provider in ["ollama", "litellm", "gemini", "bedrock"]:
        manager.provider = provider
        evaluator.setup_models()
        assert evaluator.llm.inner.kwargs
        assert evaluator.embeddings.inner.kwargs

    manager.provider = "bad"
    with pytest.raises(ValueError):
        evaluator.setup_models()
