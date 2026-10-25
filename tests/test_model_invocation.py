"""Isolation and cleanup of invocation-local model settings."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from modules.config.models.invocation import (
    invocation_answer_tokens,
    invocation_output_tokens,
    invocation_reasoning,
    model_output_budget,
)


def test_overrides_are_role_scoped_nested_and_restored_after_errors():
    assert invocation_output_tokens("builder") is None
    assert invocation_reasoning("builder") is None
    with model_output_budget("builder", 17024):
        assert invocation_output_tokens("builder") == 17024
        assert invocation_output_tokens("critic") is None
        assert invocation_reasoning("critic") is None
        with pytest.raises(RuntimeError), model_output_budget("builder", 12000, "none"):
            assert invocation_output_tokens("builder") == 12000
            assert invocation_reasoning("builder") == "none"
            raise RuntimeError("failure")
        assert invocation_output_tokens("builder") == 17024
        assert invocation_reasoning("builder") is None
    assert invocation_output_tokens("builder") is None


def test_nonpositive_override_is_rejected():
    with pytest.raises(ValueError, match="positive"), model_output_budget("builder", 0):
        pytest.fail("invalid override entered")


@pytest.mark.parametrize("answer", [0, -1, 2001])
def test_invalid_answer_floor_is_rejected(answer):
    with pytest.raises(ValueError, match="Answer allowance"), model_output_budget("builder", 2000, answer_tokens=answer):
        pytest.fail("invalid answer floor entered")


def test_answer_floor_is_role_scoped_and_restored():
    assert invocation_answer_tokens("builder") is None
    with model_output_budget("builder", 4000, answer_tokens=2000):
        assert invocation_answer_tokens("builder") == 2000
        assert invocation_answer_tokens("critic") is None
    assert invocation_answer_tokens("builder") is None


def test_concurrent_invocations_are_isolated():
    barrier = Barrier(2)

    def run(tokens):
        with model_output_budget("builder", tokens):
            barrier.wait(timeout=5)
            return invocation_output_tokens("builder")

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(run, [10000, 20000])) == [10000, 20000]
    assert invocation_output_tokens("builder") is None
