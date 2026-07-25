"""Deterministic operation-health scoring for workflow progress telemetry."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, Mapping, Optional, Sequence

from modules.tools.memory import OperationPlan, PlanPhase, Task

HEALTH_VERSION = "1"

TASK_QUALITY = {
    "done": 1.0,
    "active": 0.85,
    "pending": 0.65,
    "partial_failure": 0.25,
    "blocked": 0.10,
}

PHASE_QUALITY = {
    "done": 1.0,
    "active": 0.85,
    "pending": 0.65,
    "partial_failure": 0.20,
    "blocked": 0.10,
}

UNKNOWN_QUALITY = 0.10
EMPTY_ACTIVE_PHASE_TASK_QUALITY = 0.65
CURRENT_PHASE_WEIGHT = 1.25
COMPLETED_PHASE_WEIGHT = 1.0
FUTURE_PHASE_WEIGHT = 0.25
PREDICTION_PENALTY_WEIGHT = 0.20
COVERAGE_FEASIBILITY_MAX_PENALTY = 0.50
DEFAULT_INCOMPLETE_HEALTH_CAP = 0.99


def _normalize_health_cap(value: Any) -> float:
    """Return a finite shared health ceiling within the normalized score range."""

    try:
        cap = float(value)
    except (TypeError, ValueError):
        return DEFAULT_INCOMPLETE_HEALTH_CAP
    if not math.isfinite(cap):
        return DEFAULT_INCOMPLETE_HEALTH_CAP
    return max(0.0, min(1.0, cap))


def health_band(score: float) -> str:
    """Return the stable display band for a normalized health score."""

    if score >= 0.90:
        return "excellent"
    if score >= 0.75:
        return "good"
    if score >= 0.50:
        return "degraded"
    return "poor"


def _task_weight(task: Task) -> int:
    """Weight frozen coverage tasks by their assigned inventory units."""

    return max(1, len(task.acceptance.basis.item_ids))


def _task_quality(tasks: Sequence[Task], *, neutral_unfinished: bool = False) -> float:
    weighted_score = 0.0
    total_weight = 0
    for task in tasks:
        weight = _task_weight(task)
        status = str(task.status)
        quality = 1.0 if neutral_unfinished and status in {"active", "pending"} else TASK_QUALITY.get(
            status,
            UNKNOWN_QUALITY,
        )
        weighted_score += quality * weight
        total_weight += weight
    if total_weight <= 0:
        return EMPTY_ACTIVE_PHASE_TASK_QUALITY
    return weighted_score / total_weight


def _phase_weight(phase: PlanPhase, current_phase: int) -> float:
    if phase.id == current_phase:
        return CURRENT_PHASE_WEIGHT
    if phase.status == "pending":
        return FUTURE_PHASE_WEIGHT
    return COMPLETED_PHASE_WEIGHT


def _normalize_prediction(
    prediction: Optional[Mapping[str, Any]],
    actual_tasks: int,
) -> Optional[Dict[str, Any]]:
    if not isinstance(prediction, Mapping):
        return None
    try:
        expected_tasks = int(prediction.get("expected_tasks", 0))
        target_phase = int(prediction.get("target_phase", 0))
        source_phase = int(prediction.get("source_phase", 0))
    except (TypeError, ValueError):
        return None
    if expected_tasks <= 0 or target_phase <= 0 or source_phase <= 0:
        return None
    return {
        "available": True,
        "source_phase": source_phase,
        "target_phase": target_phase,
        "expected_tasks": expected_tasks,
        "actual_tasks": actual_tasks,
        "coverage": round(min(1.0, actual_tasks / expected_tasks), 4),
        "confidence": str(prediction.get("confidence") or "low"),
        "basis": str(prediction.get("basis") or "previous_phase"),
    }


def _phase_health(
    phase: PlanPhase,
    tasks: Sequence[Task],
    *,
    current_phase: int,
    prediction: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    future_phase = phase.status == "pending" and phase.id != current_phase
    inconsistent = phase.status == "done" and (not tasks or any(task.status != "done" for task in tasks))

    if future_phase:
        task_score = 1.0
        phase_score = 1.0
    else:
        task_score = _task_quality(tasks, neutral_unfinished=phase.id == current_phase)
        phase_status_score = PHASE_QUALITY.get(str(phase.status), UNKNOWN_QUALITY)
        phase_score = (0.75 * task_score) + (0.25 * phase_status_score)

    normalized_prediction = _normalize_prediction(prediction, len(tasks))
    if normalized_prediction is not None and not future_phase:
        coverage = float(normalized_prediction["coverage"])
        phase_score *= (1.0 - PREDICTION_PENALTY_WEIGHT) + (PREDICTION_PENALTY_WEIGHT * coverage)

    phase_score = max(0.0, min(1.0, phase_score))
    return {
        "phase_id": phase.id,
        "status": phase.status,
        "score": round(phase_score, 4),
        "task_score": round(task_score, 4),
        "task_count": len(tasks),
        "task_status_counts": dict(sorted(Counter(str(task.status) for task in tasks).items())),
        "phase_inconsistent": inconsistent,
        "prediction": normalized_prediction,
    }


def _coverage_feasibility(
    plan: OperationPlan,
    tasks: Sequence[Task],
    *,
    prediction: Optional[Mapping[str, Any]],
    progress_percent: Any,
    assessment_active: bool,
) -> Dict[str, Any]:
    """Estimate whether remaining assessment coverage fits the remaining budget."""

    unavailable = {
        "available": False,
        "feasible": True,
        "budget_remaining": None,
        "completed_work": 0,
        "remaining_work": 0,
        "required_budget_ratio": None,
        "shortfall": None,
        "phase_confidence": 0.0,
        "penalty_fraction": 0.0,
        "penalty_applied": False,
    }
    if not assessment_active:
        return unavailable
    try:
        utilization = float(progress_percent) / 100.0
    except (TypeError, ValueError):
        return unavailable
    if not math.isfinite(utilization):
        return unavailable

    applicable_phases = [
        phase
        for phase in sorted(plan.phases, key=lambda item: item.id)
        if phase.status != "not_applicable"
    ]
    current_index = next(
        (index for index, phase in enumerate(applicable_phases) if int(phase.id) == int(plan.current_phase)),
        None,
    )
    if current_index is None:
        return unavailable
    phase_confidence = 0.0
    if len(applicable_phases) > 1:
        phase_confidence = current_index / (len(applicable_phases) - 1)

    completed_work = 0
    remaining_work = 0
    for task in tasks:
        weight = _task_weight(task)
        if str(task.status) == "done":
            completed_work += weight
        else:
            remaining_work += weight

    if isinstance(prediction, Mapping) and prediction.get("available"):
        expected_tasks = max(0, int(prediction.get("expected_tasks", 0) or 0))
        actual_tasks = max(0, int(prediction.get("actual_tasks", 0) or 0))
        remaining_work += max(0, expected_tasks - actual_tasks)

    total_work = completed_work + remaining_work
    if total_work <= 0:
        return unavailable

    budget_remaining = max(0.0, min(1.0, 1.0 - max(0.0, utilization)))
    required_budget_ratio = remaining_work / total_work
    shortfall = 0.0
    if required_budget_ratio > 0:
        shortfall = max(0.0, required_budget_ratio - budget_remaining) / required_budget_ratio
    penalty_fraction = COVERAGE_FEASIBILITY_MAX_PENALTY * phase_confidence * (shortfall**2)
    return {
        "available": True,
        "feasible": shortfall == 0.0,
        "budget_remaining": round(budget_remaining, 4),
        "completed_work": completed_work,
        "remaining_work": remaining_work,
        "required_budget_ratio": round(required_budget_ratio, 4),
        "shortfall": round(shortfall, 4),
        "phase_confidence": round(phase_confidence, 4),
        "penalty_fraction": round(penalty_fraction, 4),
        "penalty_applied": penalty_fraction > 0,
    }


def compute_operation_health(
    plan: Optional[OperationPlan],
    tasks: Sequence[Task],
    *,
    predictions: Optional[Mapping[int, Mapping[str, Any]]] = None,
    budget: Optional[Mapping[str, Any]] = None,
    incomplete_health_cap: Any = DEFAULT_INCOMPLETE_HEALTH_CAP,
) -> Dict[str, Any]:
    """Compute a point-in-time operation health snapshot against an optimal score of one."""

    if plan is None:
        return {
            "health_version": HEALTH_VERSION,
            "status": "unavailable",
            "reason": "plan_unavailable",
        }

    predictions = predictions or {}
    health_cap = _normalize_health_cap(incomplete_health_cap)
    tasks_by_phase: Dict[int, list[Task]] = {}
    for task in tasks:
        tasks_by_phase.setdefault(task.phase, []).append(task)

    phase_rows = []
    weighted_score = 0.0
    total_weight = 0.0
    selected_prediction = None
    for phase in sorted(plan.phases, key=lambda item: item.id):
        if phase.status == "not_applicable":
            continue
        phase_tasks = tasks_by_phase.get(phase.id, [])
        row = _phase_health(
            phase,
            phase_tasks,
            current_phase=plan.current_phase,
            prediction=predictions.get(phase.id),
        )
        phase_rows.append(row)
        weight = _phase_weight(phase, plan.current_phase)
        weighted_score += float(row["score"]) * weight
        total_weight += weight
        if row["prediction"] is not None and (
            selected_prediction is None or phase.id == plan.current_phase
        ):
            selected_prediction = row["prediction"]

    if total_weight <= 0:
        return {
            "health_version": HEALTH_VERSION,
            "status": "unavailable",
            "reason": "no_applicable_phases",
        }

    score = max(0.0, min(1.0, weighted_score / total_weight))
    budget_data = budget or {}
    current_row = next((row for row in phase_rows if row["phase_id"] == plan.current_phase), None)
    coverage_feasibility = _coverage_feasibility(
        plan,
        tasks,
        prediction=current_row.get("prediction") if current_row else None,
        progress_percent=budget_data.get("progress_percent"),
        assessment_active=bool(budget_data.get("assessment_active", True)),
    )
    if coverage_feasibility["penalty_applied"]:
        score *= 1.0 - float(coverage_feasibility["penalty_fraction"])
    termination_reason = str(budget_data.get("termination_reason") or "").strip() or None
    termination_limit = str(budget_data.get("termination_limit") or "").strip() or None
    if termination_reason == "budget_limit":
        score = min(score, health_cap)
    actionable_statuses = {"active", "pending"}
    unresolved_tasks = [task for task in tasks if str(task.status) in actionable_statuses]
    incomplete_phase_ids = sorted(
        {int(task.phase) for task in unresolved_tasks}
        | {int(task.phase) for task in tasks if str(task.status) in {"partial_failure", "blocked"}}
        | {int(phase.id) for phase in plan.phases if phase.status in {"partial_failure", "blocked"}}
    )
    phase_inconsistent = any(bool(row["phase_inconsistent"]) for row in phase_rows)
    completion_feasible = not incomplete_phase_ids and not phase_inconsistent
    cap_incomplete_phase_ids = {
        int(task.phase)
        for task in unresolved_tasks
        if int(task.phase) != int(plan.current_phase)
    } | {
        int(task.phase)
        for task in tasks
        if str(task.status) in {"partial_failure", "blocked"}
    } | {
        int(phase.id)
        for phase in plan.phases
        if phase.status in {"partial_failure", "blocked"}
    }
    health_cap_reason = None
    if cap_incomplete_phase_ids or phase_inconsistent:
        score = min(score, health_cap)
        health_cap_reason = "incomplete_coverage"
    score = max(0.0, min(1.0, score))
    task_counts = Counter(str(task.status) for task in tasks)
    return {
        "health_version": HEALTH_VERSION,
        "status": "available",
        "score": round(score, 4),
        "band": health_band(score),
        "optimal_score": 1.0,
        "delta_from_optimal": round(score - 1.0, 4),
        "current_phase": current_row,
        "phase_count": len(plan.phases),
        "applicable_phase_count": len(phase_rows),
        "task_status_counts": dict(sorted(task_counts.items())),
        "deferred_count": int(task_counts.get("pending", 0)),
        "failure_count": int(task_counts.get("partial_failure", 0) + task_counts.get("blocked", 0)),
        "phase_inconsistent": phase_inconsistent,
        "completion_feasible": completion_feasible,
        "unresolved_task_count": len(unresolved_tasks),
        "incomplete_phase_ids": incomplete_phase_ids,
        "health_cap_reason": health_cap_reason,
        "health_cap": round(health_cap, 4),
        "prediction": selected_prediction or {"available": False},
        "coverage_feasibility": coverage_feasibility,
        "feasibility": {
            **coverage_feasibility,
            "termination_reason": termination_reason,
            "termination_limit": termination_limit,
        },
    }
