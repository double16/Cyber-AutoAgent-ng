"""Research delivery preserves role contracts, including continuation and structured calls."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("shared_delivery", Path(__file__).with_name("delivery.py"))
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


@pytest.mark.parametrize("structured", [True, False])
def test_shared_delivery_preserves_arguments_and_avoids_duplicate_context(structured):
    calls = []

    def runner(role, prompt, *args, **kwargs):
        calls.append((role, prompt, args, kwargs))
        return "result"

    controller = SimpleNamespace(text_runner=runner, work_runner=runner,
                                 structured_runner=runner if structured else None)
    delivery.attach_guidance(controller, "Resolved generic procedure")
    tools, policy = [object()], object()
    assert controller.work_runner("task_executor", "Continue", tools, "System", policy) == "result"
    assert calls[-1][2] == (tools, "System", policy)
    assert calls[-1][1].endswith("Resolved generic procedure")
    controller.text_runner("task_prompt_builder", "Build")
    assert calls[-1][1] == "Build"
    controller.text_runner("task_executor", calls[0][1])
    assert calls[-1][1].count(delivery.MARKER) == 1
    if structured:
        controller.structured_runner("task_evaluator", "Decide", output_model=policy)
        assert calls[-1][3] == {"output_model": policy}
    else:
        assert controller.structured_runner is None


def test_empty_guidance_rejected_without_changing_runners():
    runner = object()
    controller = SimpleNamespace(text_runner=runner)
    with pytest.raises(ValueError, match="non-empty"):
        delivery.attach_guidance(controller, " ")
    assert controller.text_runner is runner
