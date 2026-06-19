import pytest

from modules.evaluation import manager as mod


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
