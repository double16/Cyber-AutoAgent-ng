"""Excerpt delivery and receipts remain independent of observed contribution."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location("excerpt_delivery", Path(__file__).with_name("delivery.py"))
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


@pytest.mark.parametrize("structured", [False, True])
def test_delivery_preserves_roles_continuation_and_snapshots(structured):
    calls = []

    def runner(role, prompt, *args, **kwargs):
        calls.append((role, prompt, args, kwargs))
        return "result"

    repository = Mock()
    controller = SimpleNamespace(text_runner=runner, work_runner=runner,
                                 structured_runner=runner if structured else None,
                                 runtime=SimpleNamespace(operation_id="operation", technique_service=SimpleNamespace(
                                     repository=repository)))
    delivery.attach_excerpt(controller, "task", "revision", "Frozen excerpt", "Comparison methodology")
    tool, policy = object(), object()
    assert controller.work_runner("task_executor", "Start", [tool], "System", policy) == "result"
    assert calls[-1][2] == ([tool], "System", policy)
    assert calls[-1][1].count(delivery.MARKER) == 1
    receipt = repository.use.call_args.args
    assert receipt[:3] == ("operation", "task", "revision")
    assert receipt[3]["delivered_documents"] == [delivery.MARKER + "\nFrozen excerpt"]
    assert "score" not in receipt[3]
    controller.work_runner("task_executor", calls[-1][1])
    assert calls[-1][1].count(delivery.MARKER) == 1
    for role in ("task_prompt_builder", "task_prompt_critic"):
        controller.text_runner(role, "Unchanged")
        assert calls[-1][1] == "Unchanged"
    before = repository.use.call_count
    controller.text_runner("task_evaluator", "Evaluate")
    assert calls[-1][1].endswith("Frozen excerpt")
    assert repository.use.call_count == before
    if structured:
        controller.structured_runner("task_evaluator", "Evaluate", output_model=policy)
        assert calls[-1][3] == {"output_model": policy}


@pytest.mark.parametrize("excerpt,purpose", [(" ", "Purpose"), ("Excerpt", " ")])
def test_empty_inputs_do_not_mutate_runners(excerpt, purpose):
    runner = object()
    controller = SimpleNamespace(text_runner=runner)
    with pytest.raises(ValueError, match="non-empty"):
        delivery.attach_excerpt(controller, "task", "revision", excerpt, purpose)
    assert controller.text_runner is runner
