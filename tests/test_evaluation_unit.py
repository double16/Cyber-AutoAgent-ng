from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.evaluation import evaluation as mod

SingleTurnSample = mod.SingleTurnSample


class RecordingEmitter:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FakeConfigManager:
    def __init__(self, cfg=None):
        self.cfg = cfg or SimpleNamespace(
            max_wait_secs=0,
            poll_interval_secs=0,
            min_tool_calls=1,
            min_evidence=1,
            rubric_enabled=True,
            skip_if_insufficient_evidence=False,
            rubric_profile="strict",
            judge_system_prompt="",
            judge_user_template="",
            judge_temperature=0.1,
            judge_max_tokens=128,
            summary_max_chars=2000,
        )

    def get_provider(self):
        return "litellm"

    def get_server_config(self, _provider):
        return SimpleNamespace(evaluation=self.cfg)


def evaluator(monkeypatch, cfg=None):
    monkeypatch.setattr(mod, "get_config_manager", lambda: FakeConfigManager(cfg))
    ev = mod.CyberAgentEvaluator.__new__(mod.CyberAgentEvaluator)
    ev._emitter = RecordingEmitter()
    ev.langfuse = SimpleNamespace(api=SimpleNamespace(trace=Mock()))
    ev._last_eval_summary_sha256 = ""
    ev._last_eval_stats = {}
    ev.all_metrics = []
    return ev


@pytest.mark.asyncio
async def test_find_operation_traces_matches_session_metadata_and_name(monkeypatch):
    ev = evaluator(monkeypatch)
    traces = [
        SimpleNamespace(id="1", session_id="OP1", name="other", metadata={}),
        SimpleNamespace(id="2", session_id="x", name="other", metadata={"session_id": "OP1"}),
        SimpleNamespace(id="3", session_id="x", name="other", metadata={"attributes": {"operation.id": "OP1"}}),
        SimpleNamespace(id="4", session_id="x", name="trace OP1", metadata={}),
        SimpleNamespace(id="5", session_id="x", name="miss", metadata={}),
    ]
    ev.langfuse.api.trace.list.return_value = SimpleNamespace(data=traces)

    found = await ev._find_operation_traces("OP1")

    assert [trace.id for trace in found] == ["1", "2", "3", "4"]


@pytest.mark.asyncio
async def test_evaluate_operation_traces_handles_empty_and_per_trace_errors(monkeypatch):
    ev = evaluator(monkeypatch)
    calls = []

    async def fake_find(operation_id):
        calls.append(operation_id)
        return [SimpleNamespace(name="ok"), SimpleNamespace(name="bad")]

    async def fake_eval(trace):
        if trace.name == "bad":
            raise RuntimeError("boom")
        return {"score": 0.8}

    ev._find_operation_traces = fake_find
    ev._evaluate_single_trace = fake_eval

    assert await ev.evaluate_operation_traces("OP") == {"ok": {"score": 0.8}}
    assert calls == ["OP"]

    ev._find_operation_traces = lambda _operation_id: _empty()
    assert await ev.evaluate_operation_traces("MISSING") == {}


async def _empty():
    return []


@pytest.mark.asyncio
async def test_evaluate_trace_returns_main_trace_or_first_fallback(monkeypatch):
    ev = evaluator(monkeypatch)
    ev.evaluate_operation_traces = Mock()

    async def results_with_main(_trace_id):
        return {
            "Report": {"report": 0.3},
            "Security Assessment Main": {"main": 0.9},
        }

    ev.evaluate_operation_traces = results_with_main
    assert await ev.evaluate_trace("OP") == {"main": 0.9}

    async def results_without_main(_trace_id):
        return {"Other": {"other": 0.4}}

    ev.evaluate_operation_traces = results_without_main
    assert await ev.evaluate_trace("OP") == {"other": 0.4}


@pytest.mark.asyncio
async def test_evaluate_all_metrics_single_turn_success_skip_and_error(monkeypatch):
    ev = evaluator(monkeypatch)

    class GoodMetric:
        name = "good"

        async def single_turn_ascore(self, _data):
            return 0.75

    class NoneMetric:
        name = "none"

        async def single_turn_ascore(self, _data):
            return None

    class MultiOnly:
        name = "multi_only"

        async def multi_turn_ascore(self, _data):
            return 1.0

    class BadMetric:
        name = "bad"

        async def single_turn_ascore(self, _data):
            raise RuntimeError("fail")

    ev.all_metrics = [GoodMetric(), NoneMetric(), MultiOnly(), BadMetric()]
    sample = SingleTurnSample(user_input="target", response="done", retrieved_contexts=[])

    assert await ev._evaluate_all_metrics(sample) == {
        "good": 0.75,
        "none": 0.0,
        "multi_only": 0.0,
        "bad": 0.0,
    }
    assert any(event["type"] == "tool_start" for event in ev._emitter.events)


@pytest.mark.asyncio
async def test_upload_scores_prefers_v4_and_falls_back_to_legacy(monkeypatch):
    ev = evaluator(monkeypatch)
    created = []
    ev._last_eval_summary_sha256 = "abc"
    ev._last_eval_stats = {"tool_calls_count": 2}
    ev.langfuse = SimpleNamespace(
        scores=SimpleNamespace(create=lambda **kwargs: created.append(("v4", kwargs))),
        flush=Mock(),
    )

    await ev._upload_scores_to_langfuse("trace", {"rubric/overall_quality": (0.6, {"rationale": "ok"})})

    assert created[0][0] == "v4"
    assert created[0][1]["metadata"]["metric_category"] == "rubric_judge"
    ev.langfuse.flush.assert_called_once()

    legacy = []
    ev.langfuse = SimpleNamespace(
        scores=SimpleNamespace(create=Mock(side_effect=RuntimeError("nope"))),
        score=lambda **kwargs: legacy.append(kwargs),
        shutdown=Mock(),
    )

    await ev._upload_scores_to_langfuse("trace", {"evidence_quality": 0.5})

    assert legacy[0]["name"] == "evidence_quality"
    ev.langfuse.shutdown.assert_called_once()


def test_metric_category_and_chat_helpers(monkeypatch):
    ev = evaluator(monkeypatch)
    ev._chat_model = SimpleNamespace(invoke=Mock(return_value=SimpleNamespace(content=["a", "b"])))

    assert ev._get_metric_category("tool_selection_accuracy") == "cybersecurity_specific"
    assert ev._get_metric_category("penetration_test_quality") == "agent_performance"
    assert ev._get_metric_category("rubric/methodology") == "rubric_judge"
    assert ev._get_metric_category("answer_relevancy") == "response_quality"
    assert ev._get_metric_category("unknown") == "general"
    assert ev._chat_invoke("sys", "user") == "a b"

    ev._chat_model = SimpleNamespace(invoke=Mock(side_effect=[RuntimeError("typed"), SimpleNamespace(content="fallback")]))
    assert ev._chat_invoke("sys", "user") == "fallback"


@pytest.mark.asyncio
async def test_infer_policy_and_rubric_judge(monkeypatch):
    ev = evaluator(monkeypatch)
    ev.trace_parser = SimpleNamespace(
        count_current_evidence_findings=lambda _parsed: 2,
        count_evidence_findings=lambda _calls: 2,
    )
    ev._last_parsed_trace = SimpleNamespace(
        tool_calls=[SimpleNamespace(success=True), SimpleNamespace(success=False)],
        metadata={"attributes": {"agent.role": "main", "agent.name": "agent"}},
        objective="Assess target",
        target="https://example.com",
    )

    class FakeChat:
        def __init__(self):
            self.calls = 0

        def invoke(self, _msgs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content='{"caps": {"evidence_quality": 0.7}, "disable": ["x"]}')
            return SimpleNamespace(
                content='{"scores": {"methodology": 0.5, "tooling": 0.6, "evidence": 0.7, "outcome": 0.8}, "overall": 0.65, "rationale": "ok", "insufficient_evidence": false}'
            )

        def bind(self, **_kwargs):
            return self

    ev._chat_model = FakeChat()
    data = SimpleNamespace(user_input="objective", retrieved_contexts=["ctx"], reference_topics=["topic"])

    assert await ev._infer_evaluation_policy(data) == {
        "caps": {"evidence_quality": 0.7},
        "disable": ["x"],
    }
    rubric = await ev._rubric_judge_scores(data)
    assert rubric["rubric/overall_quality"][0] == 0.65
    assert rubric["rubric/methodology"][1]["dimension"] == "methodology"


def test_synthesize_context_summary_and_topics(monkeypatch):
    ev = evaluator(monkeypatch)
    ev._chat_model = SimpleNamespace(
        invoke=Mock(
            side_effect=[
                SimpleNamespace(content="Objective: assess\nEvidence: shell output"),
                SimpleNamespace(content='["reconnaissance", "injection testing"]'),
                SimpleNamespace(content="not-json"),
            ]
        )
    )
    parsed = SimpleNamespace(
        objective="Assess",
        target="https://example.com",
        messages=[{"role": "user", "content": "go"}],
        tool_calls=[SimpleNamespace(name="shell", input="curl", output="200")],
    )

    summary = ev._synthesize_context_summary(parsed)
    assert summary.startswith("Objective:")
    assert ev._synthesize_topics(parsed, summary) == ["reconnaissance", "injection testing"]
    assert ev._synthesize_topics(parsed, summary) == []


@pytest.mark.asyncio
async def test_create_evaluation_data_success_and_insufficient_evidence(monkeypatch):
    cfg = SimpleNamespace(
        max_wait_secs=0,
        poll_interval_secs=0,
        min_tool_calls=3,
        min_evidence=2,
        rubric_enabled=False,
        skip_if_insufficient_evidence=True,
        rubric_profile="default",
        judge_system_prompt="",
        judge_user_template="",
        judge_temperature=0.0,
        judge_max_tokens=128,
        summary_max_chars=2000,
    )
    ev = evaluator(monkeypatch, cfg)
    parsed = SimpleNamespace(
        trace_id="trace",
        messages=[{"role": "assistant", "content": "done"}],
        tool_calls=[SimpleNamespace(name="shell", input="id", output="uid")],
        metadata={},
        objective="Assess",
        target="target",
    )
    async def make_sample(_parsed):
        return SingleTurnSample(user_input="Assess", response="", retrieved_contexts=[])

    ev.trace_parser = SimpleNamespace(
        parse_trace=Mock(return_value=parsed),
        count_memory_operations=Mock(return_value=1),
        count_evidence_findings=Mock(return_value=0),
        create_evaluation_sample=Mock(side_effect=make_sample),
    )
    ev._chat_model = SimpleNamespace(invoke=Mock(return_value=SimpleNamespace(content="context")))
    ev._synthesize_topics = Mock(return_value=["topic"])

    assert await ev._create_evaluation_data(SimpleNamespace(id="trace")) is None

    cfg.min_tool_calls = 1
    cfg.min_evidence = 0
    result = await ev._create_evaluation_data(SimpleNamespace(id="trace"))

    assert result.response == "context"
    assert result.retrieved_contexts == ["context"]
    ev._synthesize_topics.assert_called()
    assert ev._last_eval_stats == {"memory_ops": 1, "evidence_count": 0, "tool_calls_count": 1}


async def _sample(sample):
    return sample


@pytest.mark.asyncio
async def test_evaluate_single_trace_applies_policy_caps_and_uploads(monkeypatch):
    ev = evaluator(monkeypatch)
    metric = SimpleNamespace(name="metric", init=Mock())
    ev.all_metrics = [metric]
    ev._create_evaluation_data = Mock(side_effect=lambda _trace: _sample(SimpleNamespace()))
    ev._evaluate_all_metrics = Mock(side_effect=lambda _data: _sample({"keep": 0.9, "drop": 0.8, "tuple": (0.9, {"m": 1})}))
    ev._rubric_judge_scores = Mock(side_effect=lambda _data: _sample({"rubric/overall_quality": 0.7}))
    ev._infer_evaluation_policy = Mock(side_effect=lambda _data: _sample({"caps": {"keep": 0.5, "tuple": 0.4}, "disable": ["drop"]}))
    uploaded = []
    ev._upload_scores_to_langfuse = Mock(side_effect=lambda trace_id, scores: uploaded.append((trace_id, scores)) or _sample(None))

    scores = await ev._evaluate_single_trace(SimpleNamespace(id="trace-id"))

    assert scores["keep"] == 0.5
    assert scores["tuple"] == (0.4, {"m": 1})
    assert "drop" not in scores
    assert uploaded[0][0] == "trace-id"
