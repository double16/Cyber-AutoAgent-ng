"""Deterministic operation-health scoring for workflow progress telemetry."""

from __future__ import annotations

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
BUDGET_INFEASIBILITY_FACTOR = 0.70
HARD_BUDGET_HEALTH_CAP = 0.49


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


def _task_quality(tasks: Sequence[Task]) -> float:
    weighted_score = 0.0
    total_weight = 0
    for task in tasks:
        weight = _task_weight(task)
        weighted_score += TASK_QUALITY.get(str(task.status), UNKNOWN_QUALITY) * weight
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
        task_score = _task_quality(tasks)
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


def compute_operation_health(
    plan: Optional[OperationPlan],
    tasks: Sequence[Task],
    *,
    predictions: Optional[Mapping[int, Mapping[str, Any]]] = None,
    budget: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute a point-in-time operation health snapshot against an optimal score of one."""

    if plan is None:
        return {
            "health_version": HEALTH_VERSION,
            "status": "unavailable",
            "reason": "plan_unavailable",
        }

    predictions = predictions or {}
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
    max_tokens = budget_data.get("max_tokens")
    max_cost = budget_data.get("max_cost")
    used_tokens = max(0, int(budget_data.get("used_tokens", 0) or 0))
    used_cost = max(0.0, float(budget_data.get("used_cost", 0.0) or 0.0))
    estimated_tokens = max(0, int(budget_data.get("estimated_reporting_tokens", 0) or 0))
    estimated_cost = max(0.0, float(budget_data.get("estimated_reporting_cost", 0.0) or 0.0))
    token_headroom = max(0, int(max_tokens) - used_tokens) if isinstance(max_tokens, int) and max_tokens > 0 else None
    cost_headroom = (
        max(0.0, float(max_cost) - used_cost)
        if isinstance(max_cost, (int, float)) and float(max_cost) > 0
        else None
    )
    token_feasible = token_headroom is None or token_headroom >= estimated_tokens
    cost_feasible = cost_headroom is None or cost_headroom >= estimated_cost
    feasible = token_feasible and cost_feasible
    if not feasible:
        score *= BUDGET_INFEASIBILITY_FACTOR
    termination_reason = str(budget_data.get("termination_reason") or "").strip() or None
    termination_limit = str(budget_data.get("termination_limit") or "").strip() or None
    if termination_reason == "budget_limit":
        score = min(score, HARD_BUDGET_HEALTH_CAP)
    score = max(0.0, min(1.0, score))
    task_counts = Counter(str(task.status) for task in tasks)
    current_row = next((row for row in phase_rows if row["phase_id"] == plan.current_phase), None)
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
        "phase_inconsistent": any(bool(row["phase_inconsistent"]) for row in phase_rows),
        "prediction": selected_prediction or {"available": False},
        "feasibility": {
            "available": bool(max_tokens or max_cost or estimated_tokens or estimated_cost or termination_reason),
            "feasible": feasible,
            "token_headroom": token_headroom,
            "cost_headroom": round(cost_headroom, 6) if cost_headroom is not None else None,
            "estimated_reporting_tokens": estimated_tokens,
            "estimated_reporting_cost": round(estimated_cost, 6),
            "termination_reason": termination_reason,
            "termination_limit": termination_limit,
        },
    }
