"""Research-only shared guidance delivery; production role behavior remains unchanged."""

from functools import wraps

MARKER = "## Catalog methodology supplied as advisory context"


def attach_guidance(controller, guidance):
    """Supply resolved methodology as advisory user data to the executor and evaluator."""
    if not guidance.strip():
        raise ValueError("Resolved guidance must be non-empty")

    def prompt_with_guidance(role, prompt):
        if role not in {"task_executor", "task_evaluator"} or MARKER in prompt:
            return prompt
        return f"{prompt}\n\n{MARKER}\n\n{guidance}"

    def wrap(runner):
        @wraps(runner)
        def run(role, prompt, *args, **kwargs):
            return runner(role, prompt_with_guidance(role, prompt), *args, **kwargs)

        return run

    controller.text_runner = wrap(controller.text_runner)
    controller.work_runner = wrap(controller.work_runner)
    if controller.structured_runner is not None:
        controller.structured_runner = wrap(controller.structured_runner)
