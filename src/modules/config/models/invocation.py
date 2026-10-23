"""Invocation-local model settings without changing shared role profiles."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_OUTPUT_OVERRIDE: ContextVar[tuple[str, int, str | None, int | None, int | None] | None] = ContextVar(
    "model_output_override", default=None,
)


def invocation_output_tokens(role: str) -> int | None:
    """Return the override only for the role that owns this invocation."""

    value = _OUTPUT_OVERRIDE.get()
    return value[1] if value is not None and value[0] == role else None


def invocation_reasoning(role: str) -> str | None:
    """Return a recovery reasoning override for its owning role only."""

    value = _OUTPUT_OVERRIDE.get()
    return value[2] if value is not None and value[0] == role else None


def invocation_minimum_output_tokens(role: str) -> int | None:
    """Return the complete-answer floor for live tool-result fitting."""
    value = _OUTPUT_OVERRIDE.get()
    return value[3] if value is not None and value[0] == role else None


def invocation_answer_tokens(role: str) -> int | None:
    """Return the answer floor independently of provider reasoning capacity."""
    value = _OUTPUT_OVERRIDE.get()
    return value[4] if value is not None and value[0] == role else None


@contextmanager
def model_output_budget(
    role: str, tokens: int, reasoning: str | None = None, *, minimum_tokens: int | None = None,
    answer_tokens: int | None = None,
) -> Iterator[None]:
    """Scope a positive output allowance to this context, including nested calls."""

    if tokens <= 0:
        raise ValueError("Model output allowance must be positive")
    if minimum_tokens is not None and not 0 < minimum_tokens <= tokens:
        raise ValueError("Minimum output allowance must be positive and fit the generation allowance")
    if answer_tokens is not None and not 0 < answer_tokens <= tokens:
        raise ValueError("Answer allowance must be positive and fit the generation allowance")
    token = _OUTPUT_OVERRIDE.set((role, tokens, reasoning, minimum_tokens, answer_tokens))
    try:
        yield
    finally:
        _OUTPUT_OVERRIDE.reset(token)
