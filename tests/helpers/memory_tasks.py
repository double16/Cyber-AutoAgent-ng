"""Helpers for tests that need legacy plan/task wrapper behavior."""

from __future__ import annotations

import json
from typing import Any, Literal

from strands import ToolContext


def active_task_message(
    memory: Any,
    active_task: Any | None = None,
    activated: bool = True,
    closed_task: Any | None = None,
    current_phase: int | None = None,
) -> str:
    closed_info = {"closed": {"task_uid": closed_task.task_uid, "status": closed_task.status}} if closed_task else {}

    if active_task is None:
        return f"""<active_task phase="{current_phase}" status="none">
{json.dumps({"task": None, "activated": False} | closed_info)}
</active_task>
"""
    return f"""<active_task phase="{active_task.phase}" status="{active_task.status}">
{json.dumps({"task": active_task.to_dict()} | closed_info | {"activated": activated}, indent=2, sort_keys=True)}
</active_task>
"""


def store_plan(memory: Any, plan: Any, tool_context: ToolContext = None) -> str:
    client = memory._ensure_memory_client()
    user_id = memory._user_id()
    op_id = None if memory.memory_is_cross_operation() else memory._operation_id()

    if isinstance(plan, str):
        plan = plan.strip()
        try:
            try:
                plan_obj = memory.OperationPlan.from_obj(json.loads(plan))
            except ValueError:
                if plan.endswith("}}"):
                    plan_obj = memory.OperationPlan.from_obj(json.loads(plan[0:-1]))
                else:
                    raise
        except ValueError as error:
            raise ValueError(
                "store_plan requires JSON object/dict with fields: objective, current_phase, total_phases, phases. "
                f"Got string that is not valid JSON: {error!s}"
            ) from error
    elif isinstance(plan, dict):
        plan_obj = memory.OperationPlan.from_obj(plan)
    elif isinstance(plan, memory.OperationPlan):
        plan_obj = plan
    else:
        plan_obj = None
    if not plan_obj:
        raise ValueError(
            f"store_plan content must be object/dict or JSON string, got {type(plan).__name__}"
        )

    prev_plan = client.get_active_plan(user_id=user_id)
    if (
        not plan_obj.assessment_complete
        and prev_plan
        and plan_obj.current_phase != prev_plan.current_phase
        and tool_context
        and tool_context.agent
        and hasattr(tool_context.agent, "callback_handler")
    ):
        active_task, _activated = client.get_or_activate_next_task_in_phase(
            user_id=user_id,
            phase=prev_plan.current_phase,
        )
        budget_progress = getattr(tool_context.agent.callback_handler, "get_budget_progress", lambda: 0)()
        if active_task and budget_progress < 90:
            raise ValueError(
                "Cannot advance phase due to activate tasks remaining.\n"
                "**MANDATORY ACTION**: Continue by executing this active task:\n"
                + active_task_message(memory, active_task)
            )

    results = client.store_plan(plan=plan_obj, user_id=user_id, operation_id=op_id)
    result_str = results.get("plan", "")
    if "_reminder" in results:
        result_str += "\n" + results["_reminder"]
    return result_str


def list_uncompleted_tasks(memory: Any, phase: int | None = None) -> str:
    client = memory._ensure_memory_client()
    user_id = memory._user_id()
    try:
        current_phase = phase if phase is not None else memory._get_plan_current_phase()
        return memory.Task.list_to_toon(
            client.list_tasks(user_id=user_id, phase=current_phase, status=["pending", "active"])
        )
    except ValueError:
        return "No active plan."


def activate_next_task_message(memory: Any, phase: int) -> str:
    client = memory._ensure_memory_client()
    task, activated = client.get_or_activate_next_task_in_phase(user_id=memory._user_id(), phase=phase)
    return active_task_message(memory, task, activated, current_phase=phase)


def mark_task_done(
    memory: Any,
    status: Literal["done", "partial_failure", "blocked"],
    task_uid: str | None = None,
    reason: str | None = None,
    phase: int | None = None,
) -> str:
    client = memory._ensure_memory_client()
    user_id = memory._user_id()
    try:
        current_phase = phase if phase is not None else memory._get_plan_current_phase()
    except ValueError:
        return active_task_message(memory)

    if status not in ["done", "partial_failure", "blocked"]:
        status = "done"

    updated, next_active = client.advance_task_in_phase(
        user_id=user_id,
        phase=current_phase,
        new_status=status,
        new_status_reason=reason,
        task_uid=task_uid,
    )
    return active_task_message(memory, next_active, next_active is not None, updated, current_phase=current_phase)
