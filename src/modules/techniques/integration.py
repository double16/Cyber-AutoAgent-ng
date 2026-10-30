"""Small workflow adapters; technique guidance never supplies execution receipts."""

import hashlib
import json
import logging
from pathlib import Path

from modules.techniques.context import methodology_context
from modules.techniques.triggers import RecommendationContext, phase_contexts, signal_adapters

logger = logging.getLogger(__name__)


def bind_task_techniques(runtime, plan, task, tools):
    service = getattr(runtime, "technique_service", None)
    if service is None:
        return tools
    from modules.handlers.utils import get_tool_name
    from modules.prompts.factory import get_module_loader
    from modules.tools.memory import _operation_output_root

    phase = next(phase for phase in plan.phases if phase.id == task.phase)
    from modules.operation_plugins.planning_contracts import load_phase_metadata_contracts

    contexts = phase_contexts(phase, task)
    contract = load_phase_metadata_contracts(runtime.config.module).get(phase.id)
    if not contexts and contract is not None:
        contexts = list(contract.technique_contexts)
    key = hashlib.sha256(
        f"{runtime.operation_id}:{task.task_uid}:{task.acceptance.basis.snapshot_hash}".encode()
    ).hexdigest()
    context = RecommendationContext(
        service,
        contexts,
        signal_adapters(get_module_loader(), runtime.config.module),
        Path(_operation_output_root()) / "techniques" / "recommendations" / f"{key}.json",
    )
    context.event_context = {"operation_id": runtime.operation_id, "task_uid": task.task_uid}
    context.lookup.purpose = task.objective
    context.lookup.capabilities = [get_tool_name(item) for item in tools]
    context.lookup.constraints = list(getattr(plan, "constraints", []))
    for item in tools:
        origin = getattr(item, "_cyber_technique_observation_origin",
                         context.tool_origins.get(get_tool_name(item), "unknown"))
        if origin not in {"source", "analysis", "guidance", "unknown"}:
            raise ValueError("Unknown technique observation origin")
        context.tool_origins[get_tool_name(item)] = origin
    context.lookup.subjects = [{"target_id": item.target_id, "kind": item.type, "value": item.value}
                               for item in getattr(plan, "targets", [])
                               if not getattr(task, "target_ids", []) or item.target_id in task.target_ids]
    context.seed(task)
    selected = service.tool(runtime.operation_id, task.task_uid, methodology_context(context.lookup))
    selected._cyber_technique_context = context
    return [item for item in tools if get_tool_name(item) != "get_technique"] + [selected]


def evaluate_task_techniques(runtime, task, reason, outcomes) -> None:
    service = getattr(runtime, "technique_service", None)
    if service is None:
        return
    try:
        from modules.tools.artifact import create_bounded_artifact_reader, resolve_operation_artifact_path

        references = set(task.evidence)
        for outcome in outcomes or []:
            references.update(outcome.artifact_refs)
        available, unavailable = [], []
        for reference in sorted(references):
            if not reference.startswith(("artifact:", "artifact_id:")):
                continue
            try:
                resolve_operation_artifact_path(reference)
                available.append(reference)
            except (OSError, ValueError):
                unavailable.append(reference)
        tools = (
            [
                create_bounded_artifact_reader(
                    max_reads=4,
                    context_window_tokens=runtime.prompt_token_limit,
                    allowed_artifact_refs=available,
                    max_reads_per_artifact=2,
                    max_lines_per_read=200,
                    max_output_chars=8000,
                )
            ]
            if available
            else []
        )
        context = json.dumps(
            {
                "available_evidence_refs": available,
                "unavailable_evidence_refs": unavailable,
                "objective": task.objective,
                "status": task.status,
                "reason": reason,
                "outcomes": [
                    {"tool": outcome.tool_name, "success": outcome.success, "output": outcome.output_summary}
                    for outcome in outcomes or []
                ],
            }
        )
        service.evaluate(runtime.operation_id, task.task_uid, context, tools)
    except Exception:
        # Preserve pending receipts for continuation; never fabricate neutral feedback on a model failure.
        service.events.emit(
            "technique_warning",
            operation_id=runtime.operation_id,
            task_uid=task.task_uid,
            stage="evaluation_or_export",
            diagnostic="Feedback or debug export remains pending",
            retry_pending=True,
        )
        logger.warning("Technique feedback or debug export remains pending for task %s", task.task_uid)


def reconcile_task_techniques(runtime, tasks) -> None:
    """Retry unevaluated receipts on continuation without counting duplicate uses."""
    if getattr(runtime, "technique_service", None) is None:
        return
    for task in tasks:
        if task.status in {"done", "partial_failure", "blocked", "superseded"}:
            evaluate_task_techniques(runtime, task, task.status_reason or "Task closed", [])


def recommendation_prompt(prompt: str, tools: list) -> str:
    """Replay recommendations only when the actual continuation permits technique lookup."""
    if "## Technique lookup recommendations\n" in prompt:
        return prompt
    for selected in tools:
        context = getattr(selected, "_cyber_technique_context", None)
        if context is not None:
            return prompt + context.render()
    return prompt
