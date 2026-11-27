"""Research-only advisory excerpt delivery with controller-owned snapshots."""

from functools import wraps

MARKER = "## Selected technique excerpt (advisory context)"


def attach_excerpt(controller, task_uid, revision, excerpt, purpose):
    """Preserve role contracts and record exactly what is offered to the executor."""
    if not excerpt.strip() or not purpose.strip():
        raise ValueError("Excerpt and methodology purpose must be non-empty")
    payload = MARKER + "\n" + excerpt
    service = controller.runtime.technique_service

    def wrap(runner):
        @wraps(runner)
        def run(role, prompt, *args, **kwargs):
            if role in {"task_executor", "task_evaluator"}:
                if MARKER not in prompt:
                    prompt += "\n\n" + payload
                if role == "task_executor":
                    service.repository.use(controller.runtime.operation_id, task_uid, revision, {
                        "generic_request": purpose,
                        "component_purpose": purpose,
                        "phase_contexts": ["vulnerability_verification"],
                        "delivery_mode": "research_selected_excerpt",
                        "delivered_documents": [payload],
                    })
            return runner(role, prompt, *args, **kwargs)

        return run

    controller.text_runner = wrap(controller.text_runner)
    controller.work_runner = wrap(controller.work_runner)
    if controller.structured_runner is not None:
        controller.structured_runner = wrap(controller.structured_runner)
