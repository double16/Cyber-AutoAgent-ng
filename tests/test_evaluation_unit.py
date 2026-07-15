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


def test_evaluation_usage_callback_accumulates_tokens_and_deduplicates_runs():
    updates = []
    callback = mod.EvaluationUsageCallback("provider/evaluator", "litellm", updates.append)
    response = SimpleNamespace(
        llm_output={
            "token_usage": {
                "prompt_tokens": 1_000,
                "completion_tokens": 500,
                "cache_read_input_tokens": 800,
                "cache_creation_input_tokens": 200,
            }
        },
        generations=[],
    )

    callback.on_llm_end(response, run_id="run-1")
    callback.on_llm_end(response, run_id="run-1")
    callback.on_llm_end(response, run_id="run-2")

    assert updates == [
        {
            "modelId": "provider/evaluator",
            "providerId": "litellm",
            "inputTokens": 1_000,
            "outputTokens": 500,
            "cacheReadTokens": 800,
            "cacheWriteTokens": 200,
        },
        {
            "modelId": "provider/evaluator",
            "providerId": "litellm",
            "inputTokens": 2_000,
            "outputTokens": 1_000,
            "cacheReadTokens": 1_600,
            "cacheWriteTokens": 400,
        },
    ]


def test_evaluation_usage_callback_ignores_missing_usage():
    callback_fn = Mock()
    callback = mod.EvaluationUsageCallback("provider/evaluator", "litellm", callback_fn)

    callback.on_llm_end(SimpleNamespace(llm_output={}, generations=[]), run_id="run-1")

    callback_fn.assert_not_called()


def test_evaluation_usage_callback_reads_message_metadata():
    updates = []
    message = SimpleNamespace(
        usage_metadata={
            "input_tokens": 7,
            "output_tokens": 3,
            "input_token_details": {"cache_read": 5, "cache_creation": 2},
        }
    )
    response = SimpleNamespace(
        llm_output=None,
        generations=[[SimpleNamespace(message=message)]],
    )
    callback = mod.EvaluationUsageCallback("unpriced-model", "gemini", updates.append)

    callback.on_llm_end(response, run_id="run-1")

    assert updates == [
        {
            "modelId": "unpriced-model",
            "providerId": "gemini",
            "inputTokens": 7,
            "outputTokens": 3,
            "cacheReadTokens": 5,
            "cacheWriteTokens": 2,
        }
    ]


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
    ev.report_path = None
    ev._evaluation_operation_id = None
    ev._evaluation_step_index = 0
    ev._evaluation_step_total = 0
    ev._current_evaluation_scope = None
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
async def test_evaluate_operation_traces_runs_at_most_operation_and_report(monkeypatch):
    ev = evaluator(monkeypatch)
    calls = []
    ev.all_metrics = [SimpleNamespace(name="operation_one"), SimpleNamespace(name="operation_two")]
    ev.evidence_quality = SimpleNamespace(name="evidence_quality")
    ev.goal_accuracy = SimpleNamespace(name="goal_accuracy")
    ev.topic_adherence = SimpleNamespace(name="topic_adherence")
    traces = [
        SimpleNamespace(
            id="executor",
            metadata={"attributes": {"langfuse.agent.type": "task_executor"}},
        ),
        SimpleNamespace(
            id="evaluator",
            metadata={"attributes": {"langfuse.agent.type": "task_evaluator"}},
        ),
    ]

    async def fake_find(operation_id):
        calls.append(operation_id)
        return traces

    async def fake_eval(_trace, metric_scope=None):
        calls.append((metric_scope, ev._evaluation_step_total, ev._current_evaluation_scope))
        return {f"{metric_scope}/score": 0.8}

    ev._find_operation_traces = fake_find
    ev._evaluate_single_trace = fake_eval
    ev._build_operation_evaluation_trace = Mock(return_value=SimpleNamespace(id="operation"))
    ev._build_report_evaluation_trace = Mock(return_value=SimpleNamespace(id="report"))

    assert await ev.evaluate_operation_traces("OP") == {
        "operation": {"operation/score": 0.8},
        "report": {"report/score": 0.8},
    }
    assert calls == [
        "OP",
        ("operation", 5, "operation"),
        ("report", 5, "report"),
    ]
    assert ev._evaluation_step_total == 0
    assert ev._current_evaluation_scope is None

    ev._find_operation_traces = lambda _operation_id: _empty()
    assert await ev.evaluate_operation_traces("MISSING") == {}


@pytest.mark.asyncio
async def test_evaluate_operation_traces_cleans_up_progress_after_scope_failure(monkeypatch):
    ev = evaluator(monkeypatch)
    ev.all_metrics = [SimpleNamespace(name="operation_metric")]
    trace = SimpleNamespace(
        id="executor",
        metadata={"attributes": {"agent.role": "task_executor"}},
    )
    ev._find_operation_traces = lambda _operation_id: _sample([trace])
    ev._build_operation_evaluation_trace = Mock(return_value=SimpleNamespace(id="operation"))
    ev._build_report_evaluation_trace = Mock(return_value=None)

    async def fail_evaluation(_trace, metric_scope=None):
        assert metric_scope == "operation"
        assert ev._evaluation_step_total == 1
        raise RuntimeError("metric provider unavailable")

    ev._evaluate_single_trace = fail_evaluation

    assert await ev.evaluate_operation_traces("OP") == {}
    assert ev._evaluation_operation_id is None
    assert ev._evaluation_step_total == 0
    assert ev._current_evaluation_scope is None


@pytest.mark.asyncio
async def test_evaluate_operation_traces_skips_when_no_execution_or_report_artifacts(monkeypatch):
    ev = evaluator(monkeypatch)
    evaluator_trace = SimpleNamespace(
        id="evaluator",
        metadata={"attributes": {"agent.role": "task_evaluator"}},
    )
    ev._find_operation_traces = lambda _operation_id: _sample([evaluator_trace])
    ev._build_operation_evaluation_trace = Mock()
    ev._build_report_evaluation_trace = Mock(return_value=None)

    assert await ev.evaluate_operation_traces("OP") == {}
    ev._build_operation_evaluation_trace.assert_not_called()


async def _empty():
    return []


@pytest.mark.asyncio
async def test_evaluate_trace_returns_operation_or_report_fallback(monkeypatch):
    ev = evaluator(monkeypatch)
    ev.evaluate_operation_traces = Mock()

    async def results_with_main(_trace_id):
        return {
            "report": {"report/score": 0.3},
            "operation": {"operation/score": 0.9},
        }

    ev.evaluate_operation_traces = results_with_main
    assert await ev.evaluate_trace("OP") == {"operation/score": 0.9}

    async def results_without_main(_trace_id):
        return {"report": {"report/score": 0.4}}

    ev.evaluate_operation_traces = results_without_main
    assert await ev.evaluate_trace("OP") == {"report/score": 0.4}


def test_select_execution_traces_uses_roles_and_legacy_fallback(monkeypatch):
    ev = evaluator(monkeypatch)
    traces = [
        SimpleNamespace(id="1", metadata={"attributes": {"agent.role": "task_executor"}}),
        SimpleNamespace(id="2", metadata={"attributes": {"langfuse.agent.type": "swarm_agent"}}),
        SimpleNamespace(id="3", metadata={"attributes": {"agent.role": "phase_evaluator"}}),
        SimpleNamespace(id="4", metadata={"attributes": {"agent.role": "plan_critic"}}),
    ]

    assert [trace.id for trace in ev._select_execution_traces(traces)] == ["1", "2"]
    assert ev._select_execution_traces([traces[2]]) == []
    assert ev._select_execution_traces([traces[3]]) == []
    legacy = SimpleNamespace(id="legacy", metadata={})
    assert ev._select_execution_traces([legacy]) == [legacy]


def test_build_report_evaluation_trace_reads_assembled_report(monkeypatch, tmp_path):
    ev = evaluator(monkeypatch)
    report_path = tmp_path / "security_assessment_report.md"
    report_path.write_text("# Report\nEvidence-backed result", encoding="utf-8")
    ev.report_path = str(report_path)
    ev.trace_parser = SimpleNamespace(_extract_objective=lambda _trace: "Assess target")
    ev._score_host_trace_id = Mock(return_value="report-evaluation")

    trace = ev._build_report_evaluation_trace("OP", [SimpleNamespace(id="source")])

    assert trace.id == "report-evaluation"
    assert trace.output.startswith("# Report")
    assert trace.metadata["attributes"]["evaluation.scope"] == "report"


def test_score_host_trace_uses_stable_dedicated_langfuse_trace(monkeypatch):
    ev = evaluator(monkeypatch)
    span = SimpleNamespace(update_trace=Mock(), end=Mock())
    ev.langfuse = SimpleNamespace(
        create_trace_id=Mock(return_value="stable-trace"),
        start_span=Mock(return_value=span),
        flush=Mock(),
    )

    trace_id = ev._score_host_trace_id(
        "OP",
        "operation_evaluation",
        input_data="objective",
        output_data="result",
        fallback_trace_id="fallback",
    )

    assert trace_id == "stable-trace"
    ev.langfuse.create_trace_id.assert_called_once_with(seed="OP:operation_evaluation")
    span.update_trace.assert_called_once()
    span.end.assert_called_once()
    ev.langfuse.flush.assert_called_once()


def test_score_host_trace_falls_back_when_langfuse_trace_creation_fails(monkeypatch):
    ev = evaluator(monkeypatch)
    ev.langfuse = SimpleNamespace(create_trace_id=Mock(side_effect=RuntimeError("unavailable")))

    assert ev._score_host_trace_id(
        "OP",
        "operation_evaluation",
        input_data="objective",
        output_data="result",
        fallback_trace_id="fallback",
    ) == "fallback"


def test_build_operation_evaluation_trace_deduplicates_observations(monkeypatch):
    ev = evaluator(monkeypatch)
    shared = SimpleNamespace(id="shared", type="SPAN")
    unique = SimpleNamespace(id="unique", type="GENERATION")
    traces = [SimpleNamespace(id="one"), SimpleNamespace(id="two")]
    ev.trace_parser = SimpleNamespace(
        _extract_objective=lambda _trace: "Assess target",
        _fetch_observations=Mock(side_effect=[[shared], [shared, unique]]),
        _extract_final_output=Mock(side_effect=["first", "second"]),
    )
    ev._score_host_trace_id = Mock(return_value="operation-evaluation")

    trace = ev._build_operation_evaluation_trace("OP", traces)

    assert trace.id == "operation-evaluation"
    assert [observation.id for observation in trace.observations] == ["shared", "unique"]
    assert trace.output == "first\n\nsecond"
    assert trace.metadata["attributes"]["evaluation.source_trace_count"] == 2


def test_build_report_evaluation_trace_skips_missing_report(monkeypatch):
    ev = evaluator(monkeypatch)
    ev.report_path = "/missing/security_assessment_report.md"

    assert ev._build_report_evaluation_trace("OP", []) is None


def test_report_metric_scope_excludes_operation_only_metrics(monkeypatch):
    ev = evaluator(monkeypatch)
    ev.evidence_quality = SimpleNamespace(name="evidence_quality")
    ev.goal_accuracy = SimpleNamespace(name="goal_accuracy")
    ev.topic_adherence = SimpleNamespace(name="topic_adherence")
    ev.all_metrics = [SimpleNamespace(name="tool_selection"), ev.evidence_quality]

    assert [metric.name for metric in ev._metrics_for_scope("report")] == [
        "evidence_quality",
        "goal_accuracy",
        "topic_adherence",
    ]
    assert ev._metrics_for_scope("operation") is ev.all_metrics


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
    ev._evaluation_operation_id = "OP_TEST"
    ev._evaluation_step_total = 4
    sample = SingleTurnSample(user_input="target", response="done", retrieved_contexts=[])

    assert await ev._evaluate_all_metrics(sample) == {
        "good": 0.75,
        "none": 0.0,
        "multi_only": 0.0,
        "bad": 0.0,
    }
    assert not any(event["type"] in {"tool_start", "tool_end"} for event in ev._emitter.events)
    completed = [event for event in ev._emitter.events if event["type"] == "evaluation_step_complete"]
    assert [(event["evaluation_metric"], event["status"]) for event in completed] == [
        ("good", "completed"),
        ("none", "failed"),
        ("multi_only", "skipped"),
        ("bad", "failed"),
    ]


@pytest.mark.asyncio
async def test_evaluate_all_metrics_emits_indexed_progress_for_selected_metrics(monkeypatch):
    ev = evaluator(monkeypatch)

    class Metric:
        def __init__(self, name, score):
            self.name = name
            self.score = score

        async def single_turn_ascore(self, _data):
            return self.score

    selected = [Metric("evidence_quality", 0.8), Metric("goal_accuracy", 0.6)]
    ev.all_metrics = [Metric("not_selected", 1.0)]
    ev._evaluation_operation_id = "OP_TEST"
    ev._evaluation_step_total = 2
    ev._current_evaluation_scope = "report"
    sample = SingleTurnSample(user_input="target", response="done", retrieved_contexts=[])

    assert await ev._evaluate_all_metrics(sample, metrics=selected) == {
        "evidence_quality": 0.8,
        "goal_accuracy": 0.6,
    }
    progress = [event for event in ev._emitter.events if event["type"] == "progress_update"]
    assert [event["evaluation_step_index"] for event in progress] == [1, 2]
    assert all(event["evaluation_step_total"] == 2 for event in progress)
    assert all(event["operation_stage"] == "ragas_evaluation" for event in progress)
    assert all(event["evaluation_scope"] == "report" for event in progress)
    assert progress[0]["evaluation_step_label"] == "Report: Evidence Quality"


def test_evaluation_progress_is_best_effort(monkeypatch):
    ev = evaluator(monkeypatch)
    ev._emitter = SimpleNamespace(emit=Mock(side_effect=RuntimeError("disconnected")))
    ev._evaluation_step_total = 1

    ev._emit_evaluation_progress(SimpleNamespace(name="goal_accuracy"))

    assert ev._evaluation_step_index == 1


def test_evaluation_progress_is_not_emitted_outside_a_scheduled_run(monkeypatch):
    ev = evaluator(monkeypatch)

    ev._emit_evaluation_progress(SimpleNamespace(name="goal_accuracy"))

    assert ev._emitter.events == []


def test_evaluation_preparation_progress_is_unindexed(monkeypatch):
    ev = evaluator(monkeypatch)
    ev._evaluation_operation_id = "OP_TEST"
    ev._evaluation_step_index = 2
    ev._evaluation_step_total = 5
    ev._current_evaluation_scope = "report"

    ev._emit_evaluation_preparation_progress("reference_topics")

    assert ev._emitter.events == [
        {
            "type": "progress_update",
            "step": "RAGAS_PREPARATION",
            "operation_stage": "ragas_evaluation",
            "operation": "OP_TEST",
            "evaluation_step_kind": "reference_topics",
            "evaluation_scope": "report",
            "evaluation_step_label": "Report: Generate Reference Topics",
        }
    ]
    assert ev._evaluation_step_index == 2
    assert ev._evaluation_step_total == 5


def test_evaluation_preparation_completion_is_semantic(monkeypatch):
    ev = evaluator(monkeypatch)
    ev._evaluation_operation_id = "OP_TEST"
    ev._current_evaluation_scope = "operation"

    ev._emit_evaluation_preparation_progress("rubric_judge")
    ev._emit_evaluation_preparation_progress("rubric_judge", "failed")

    assert ev._emitter.events[0]["evaluation_step_label"] == "Operation: Run Rubric Judge"
    assert ev._emitter.events[1] == {
        "type": "evaluation_step_complete",
        "operation_id": "OP_TEST",
        "operation_stage": "ragas_evaluation",
        "evaluation_scope": "operation",
        "evaluation_step_kind": "rubric_judge",
        "status": "failed",
        "message": "Rubric Judge failed",
    }


def test_evaluation_preparation_progress_is_best_effort_and_scheduled(monkeypatch):
    ev = evaluator(monkeypatch)
    ev._emit_evaluation_preparation_progress("reference_topics")
    assert ev._emitter.events == []

    ev._evaluation_operation_id = "OP_TEST"
    ev._emitter = SimpleNamespace(emit=Mock(side_effect=RuntimeError("disconnected")))
    ev._emit_evaluation_preparation_progress("reference_topics")


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
    ev._evaluation_operation_id = "OP_TEST"
    ev._current_evaluation_scope = "operation"
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
    assert not any(event["type"] in {"tool_start", "tool_end"} for event in ev._emitter.events)
    preparation_kinds = [
        event["evaluation_step_kind"]
        for event in ev._emitter.events
        if event["type"] == "progress_update"
    ]
    assert preparation_kinds == ["evaluation_policy", "rubric_judge"]
    completions = [
        event for event in ev._emitter.events if event["type"] == "evaluation_step_complete"
    ]
    assert [(event["evaluation_step_kind"], event["status"]) for event in completions] == [
        ("evaluation_policy", "completed"),
        ("rubric_judge", "completed"),
    ]


@pytest.mark.asyncio
async def test_policy_and_rubric_failures_emit_semantic_status(monkeypatch):
    ev = evaluator(monkeypatch)
    ev._evaluation_operation_id = "OP_TEST"
    ev._current_evaluation_scope = "operation"
    ev._last_parsed_trace = SimpleNamespace(
        tool_calls=[SimpleNamespace(success=True)],
        metadata={},
        objective="Assess",
        target="target",
    )
    ev.trace_parser = SimpleNamespace(
        count_current_evidence_findings=lambda _parsed: 1,
        count_evidence_findings=lambda _calls: 1,
    )
    ev._chat_model = SimpleNamespace(invoke=Mock(return_value=SimpleNamespace(content="not-json")))
    data = SimpleNamespace(user_input="objective", retrieved_contexts=[], reference_topics=[])

    assert await ev._infer_evaluation_policy(data) == {}
    assert ev._emitter.events[-1]["status"] == "failed"

    ev._chat_model = SimpleNamespace(invoke=Mock(side_effect=RuntimeError("judge unavailable")))
    assert await ev._rubric_judge_scores(data) == {}
    assert ev._emitter.events[-1] == {
        "type": "evaluation_step_complete",
        "operation_id": "OP_TEST",
        "operation_stage": "ragas_evaluation",
        "evaluation_scope": "operation",
        "evaluation_step_kind": "rubric_judge",
        "status": "failed",
        "message": "Rubric judge failed",
    }


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
    ev._evaluation_operation_id = "OP_TEST"
    ev._current_evaluation_scope = "operation"
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
    statuses = [
        event["status"]
        for event in ev._emitter.events
        if event["type"] == "evaluation_step_complete"
        and event["evaluation_step_kind"] == "evaluation_data"
    ]
    assert statuses == ["skipped", "completed"]
    assert not any(event["type"] in {"tool_start", "tool_end"} for event in ev._emitter.events)


@pytest.mark.asyncio
async def test_create_evaluation_data_reports_parse_and_sample_failures(monkeypatch):
    ev = evaluator(monkeypatch)
    ev._evaluation_operation_id = "OP_TEST"
    ev.trace_parser = SimpleNamespace(parse_trace=Mock(return_value=None))

    assert await ev._create_evaluation_data(SimpleNamespace(id="bad-trace")) is None
    assert ev._emitter.events[-1]["status"] == "failed"

    parsed = SimpleNamespace(trace_id="trace", messages=[], tool_calls=[], metadata={})

    async def fail_sample(_parsed):
        raise RuntimeError("sample failed")

    ev.trace_parser = SimpleNamespace(
        parse_trace=Mock(return_value=parsed),
        count_memory_operations=Mock(return_value=0),
        count_evidence_findings=Mock(return_value=0),
        create_evaluation_sample=Mock(side_effect=fail_sample),
    )
    with pytest.raises(RuntimeError, match="sample failed"):
        await ev._create_evaluation_data(SimpleNamespace(id="trace"))
    assert ev._emitter.events[-1]["message"] == "Unable to prepare evaluation sample"


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
