from modules.handlers.operation_health import compute_operation_health, health_band
from modules.tools.memory import (
    AcceptanceBasis,
    AcceptanceContract,
    AcceptanceCriterion,
    EvidenceRequirement,
    OperationPlan,
    PlanPhase,
    Task,
)


def _acceptance(item_ids=()):
    if item_ids:
        return AcceptanceContract(
            mode="coverage",
            basis=AcceptanceBasis(
                kind="snapshot",
                description="Frozen test inventory",
                source_refs=["artifact:artifacts/inventory.json"],
                snapshot_hash="snapshot-hash",
                item_ids=item_ids,
            ),
            criteria=[
                AcceptanceCriterion(
                    id="coverage",
                    description="Assess the frozen test inventory",
                    evidence_requirements=[EvidenceRequirement(kind="artifact")],
                )
            ],
        )
    return AcceptanceContract(
        mode="outcome",
        basis=AcceptanceBasis(
            kind="procedure",
            description="Bounded test procedure",
            source_refs=["target:target-1", "plan:phase-1"],
            procedure={
                "methods": ["test"],
                "limits": {"max_items": 1},
                "stop_condition": "first_limit_reached",
                "gap_policy": "record_unassessed",
                "output_kind": "artifact",
            },
        ),
        criteria=[
            AcceptanceCriterion(
                id="outcome",
                description="Complete the bounded test procedure",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            )
        ],
    )


def _task(uid, phase, status, item_ids=()):
    return Task(
        task_uid=uid,
        title=f"Task {uid}",
        objective="Complete the test objective",
        acceptance=_acceptance(item_ids),
        phase=phase,
        status=status,
    )


def _plan(*phases, current_phase=1):
    return OperationPlan(
        objective="Test operation health",
        current_phase=current_phase,
        total_phases=len(phases),
        phases=list(phases),
    )


def test_all_successful_work_is_perfect_health():
    plan = _plan(PlanPhase(id=1, title="Done", status="done"))

    health = compute_operation_health(plan, [_task("done", 1, "done")])

    assert health["status"] == "available"
    assert health["score"] == 1.0
    assert health["band"] == "excellent"
    assert health["phase_inconsistent"] is False


def test_superseded_task_is_neutral_terminal_work():
    plan = _plan(PlanPhase(id=1, title="Done", status="done"))

    health = compute_operation_health(plan, [_task("replaced", 1, "superseded")])

    assert health["score"] == 1.0
    assert health["phase_inconsistent"] is False
    assert health["failure_count"] == 0
    assert health["completion_feasible"] is True


def test_done_phase_with_unfinished_task_is_not_perfect_and_is_inconsistent():
    plan = _plan(PlanPhase(id=1, title="Done", status="done"))

    health = compute_operation_health(plan, [_task("pending", 1, "pending")])

    assert health["score"] == 0.99
    assert health["band"] == "excellent"
    assert health["phase_inconsistent"] is True
    assert health["deferred_count"] == 1
    assert health["completion_feasible"] is False
    assert health["unresolved_task_count"] == 1
    assert health["incomplete_phase_ids"] == [1]
    assert health["health_cap_reason"] == "incomplete_coverage"
    assert health["health_cap"] == 0.99


def test_current_phase_active_and_pending_tasks_are_score_neutral_without_coverage_cap():
    plan = _plan(PlanPhase(id=1, title="Active", status="active"))
    health = compute_operation_health(
        plan,
        [
            _task("active", 1, "active"),
            _task("pending", 1, "pending"),
        ],
    )

    assert health["current_phase"]["task_score"] == 1.0
    assert health["score"] > 0.49
    assert health["health_cap_reason"] is None
    assert health["completion_feasible"] is False
    assert health["unresolved_task_count"] == 2
    assert health["incomplete_phase_ids"] == [1]


def test_noncurrent_pending_task_still_triggers_incomplete_coverage_cap():
    plan = _plan(
        PlanPhase(id=1, title="Prior", status="done"),
        PlanPhase(id=2, title="Current", status="active"),
        current_phase=2,
    )
    health = compute_operation_health(plan, [_task("pending", 1, "pending")])

    assert health["score"] > 0.49
    assert health["health_cap_reason"] == "incomplete_coverage"


def test_failure_statuses_penalize_health_more_than_deferred_work():
    plan = _plan(PlanPhase(id=1, title="Active", status="active"))

    pending = compute_operation_health(plan, [_task("pending", 1, "pending")])
    partial = compute_operation_health(plan, [_task("partial", 1, "partial_failure")])
    blocked = compute_operation_health(plan, [_task("blocked", 1, "blocked")])

    assert partial["score"] < pending["score"]
    assert blocked["score"] < pending["score"]
    assert partial["failure_count"] == 1
    assert blocked["failure_count"] == 1
    assert partial["completion_feasible"] is False
    assert blocked["completion_feasible"] is False


def test_future_pending_phase_does_not_reduce_health():
    plan = _plan(
        PlanPhase(id=1, title="Done", status="done"),
        PlanPhase(id=2, title="Future", status="pending"),
        current_phase=1,
    )

    health = compute_operation_health(plan, [_task("done", 1, "done")])

    assert health["score"] == 1.0


def test_empty_active_phase_is_degraded_and_empty_done_phase_is_inconsistent():
    active = compute_operation_health(
        _plan(PlanPhase(id=1, title="Active", status="active")),
        [],
    )
    done = compute_operation_health(
        _plan(PlanPhase(id=1, title="Done", status="done")),
        [],
    )

    assert active["band"] == "degraded"
    assert done["phase_inconsistent"] is True
    assert done["score"] < 1.0


def test_not_applicable_only_plan_and_missing_plan_are_unavailable():
    no_plan = compute_operation_health(None, [])
    not_applicable = compute_operation_health(
        _plan(PlanPhase(id=1, title="Skipped", status="not_applicable")),
        [],
    )

    assert no_plan == {
        "health_version": "1",
        "status": "unavailable",
        "reason": "plan_unavailable",
    }
    assert not_applicable["status"] == "unavailable"
    assert not_applicable["reason"] == "no_applicable_phases"


def test_coverage_task_weight_reflects_number_of_inventory_items():
    plan = _plan(PlanPhase(id=1, title="Active", status="active"))
    tasks = [
        _task("large-failure", 1, "partial_failure", ("a", "b", "c")),
        _task("small-success", 1, "done"),
    ]

    health = compute_operation_health(plan, tasks)

    assert health["current_phase"]["task_score"] == 0.4375


def test_manifest_prediction_reports_coverage_and_penalizes_missing_fanout():
    plan = _plan(
        PlanPhase(id=1, title="Inventory", status="done"),
        PlanPhase(id=2, title="Assessment", status="active"),
        current_phase=2,
    )
    tasks = [_task("inventory", 1, "done"), _task("route-1", 2, "done")]
    prediction = {
        2: {
            "source_phase": 1,
            "target_phase": 2,
            "expected_tasks": 2,
            "confidence": "high",
            "basis": "inventory_manifest_fanout",
        }
    }

    predicted = compute_operation_health(plan, tasks, predictions=prediction)
    unpredicted = compute_operation_health(plan, tasks)

    assert predicted["prediction"]["coverage"] == 0.5
    assert predicted["prediction"]["actual_tasks"] == 1
    assert predicted["score"] < unpredicted["score"]


def test_invalid_prediction_is_ignored_and_health_bands_are_stable():
    plan = _plan(PlanPhase(id=1, title="Active", status="active"))
    health = compute_operation_health(plan, [], predictions={1: {"expected_tasks": "bad"}})

    assert health["prediction"] == {"available": False}
    assert health_band(0.90) == "excellent"
    assert health_band(0.75) == "good"
    assert health_band(0.50) == "degraded"
    assert health_band(0.49) == "poor"


def test_health_ignores_reporting_budget_headroom():
    health = compute_operation_health(
        _plan(PlanPhase(id=1, title="Done", status="done")),
        [_task("done", 1, "done")],
        budget={
            "max_tokens": 10_000,
            "used_tokens": 4_000,
            "estimated_reporting_tokens": 2_000,
            "max_cost": 10.0,
            "used_cost": 2.0,
            "estimated_reporting_cost": 1.0,
        },
    )

    assert health["score"] == 1.0
    assert health["feasibility"]["feasible"] is True
    assert "token_headroom" not in health["feasibility"]
    assert "estimated_reporting_tokens" not in health["feasibility"]


def test_health_does_not_penalize_insufficient_reporting_token_or_cost_headroom():
    plan = _plan(PlanPhase(id=1, title="Done", status="done"))
    tasks = [_task("done", 1, "done")]

    token_health = compute_operation_health(
        plan,
        tasks,
        budget={"max_tokens": 5_000, "used_tokens": 4_000, "estimated_reporting_tokens": 2_000},
    )
    cost_health = compute_operation_health(
        plan,
        tasks,
        budget={"max_cost": 2.0, "used_cost": 1.5, "estimated_reporting_cost": 1.0},
    )

    assert token_health["feasibility"]["feasible"] is True
    assert cost_health["feasibility"]["feasible"] is True
    assert token_health["score"] == 1.0
    assert cost_health["score"] == 1.0


def test_health_without_token_or_cost_budget_does_not_invent_a_reserve():
    health = compute_operation_health(
        _plan(PlanPhase(id=1, title="Done", status="done")),
        [_task("done", 1, "done")],
    )

    assert health["score"] == 1.0
    assert health["feasibility"]["available"] is False
    assert health["feasibility"]["feasible"] is True


def test_duration_budget_termination_caps_final_health_without_duration_reserve():
    health = compute_operation_health(
        _plan(PlanPhase(id=1, title="Done", status="done")),
        [_task("done", 1, "done")],
        budget={"termination_reason": "budget_limit", "termination_limit": "duration"},
    )

    assert health["score"] == 0.99
    assert health["band"] == "excellent"
    assert health["health_cap"] == 0.99
    assert health["feasibility"]["termination_limit"] == "duration"
    assert "duration_headroom" not in health["feasibility"]


def test_custom_health_cap_is_shared_by_incomplete_coverage_and_budget_limit():
    plan = _plan(PlanPhase(id=1, title="Done", status="done"))
    tasks = [_task("pending", 1, "pending")]

    incomplete = compute_operation_health(plan, tasks, incomplete_health_cap=0.75)
    budget_only = compute_operation_health(
        plan,
        [_task("done", 1, "done")],
        budget={"termination_reason": "budget_limit", "termination_limit": "tokens"},
        incomplete_health_cap=0.75,
    )
    combined = compute_operation_health(
        plan,
        tasks,
        budget={"termination_reason": "budget_limit", "termination_limit": "tokens"},
        incomplete_health_cap=0.75,
    )

    assert incomplete["score"] == 0.75
    assert budget_only["score"] == 0.75
    assert combined["score"] == 0.75
    assert incomplete["health_cap"] == budget_only["health_cap"] == combined["health_cap"] == 0.75
    assert combined["feasibility"]["termination_reason"] == "budget_limit"


def test_health_cap_never_raises_a_naturally_low_failure_score():
    health = compute_operation_health(
        _plan(PlanPhase(id=1, title="Active", status="active")),
        [_task("blocked", 1, "blocked")],
        incomplete_health_cap=0.99,
    )

    assert health["score"] < 0.50
    assert health["health_cap"] == 0.99


def test_reporting_estimates_do_not_reduce_budget_limited_health():
    health = compute_operation_health(
        _plan(PlanPhase(id=1, title="Done", status="done")),
        [_task("done", 1, "done")],
        budget={
            "max_tokens": 5_000,
            "used_tokens": 4_000,
            "estimated_reporting_tokens": 2_000,
            "termination_reason": "budget_limit",
            "termination_limit": "tokens",
        },
        incomplete_health_cap=0.99,
    )

    assert health["score"] == 0.99
    assert health["health_cap"] == 0.99


def test_health_cap_normalizes_out_of_range_and_invalid_values():
    plan = _plan(PlanPhase(id=1, title="Done", status="done"))
    tasks = [_task("pending", 1, "pending")]

    assert compute_operation_health(plan, tasks, incomplete_health_cap=-1)["health_cap"] == 0.0
    assert compute_operation_health(plan, tasks, incomplete_health_cap=2)["health_cap"] == 1.0
    assert compute_operation_health(plan, tasks, incomplete_health_cap="invalid")["health_cap"] == 0.99
    assert compute_operation_health(plan, tasks, incomplete_health_cap=float("nan"))["health_cap"] == 0.99


def test_phase_one_never_applies_coverage_feasibility_penalty():
    plan = _plan(
        PlanPhase(id=1, title="Discovery", status="active"),
        PlanPhase(id=2, title="Assessment", status="pending"),
        current_phase=1,
    )
    tasks = [_task("pending", 1, "pending")]

    health = compute_operation_health(plan, tasks, budget={"progress_percent": 99})

    assert health["coverage_feasibility"]["available"] is True
    assert health["coverage_feasibility"]["feasible"] is False
    assert health["feasibility"]["feasible"] is False
    assert health["coverage_feasibility"]["phase_confidence"] == 0.0
    assert health["coverage_feasibility"]["shortfall"] == 0.99
    assert health["coverage_feasibility"]["penalty_fraction"] == 0.0
    assert health["coverage_feasibility"]["penalty_applied"] is False


def test_coverage_feasibility_penalty_grows_by_phase_position():
    phases = [
        PlanPhase(id=1, title="One", status="done"),
        PlanPhase(id=2, title="Two", status="pending"),
        PlanPhase(id=3, title="Three", status="pending"),
        PlanPhase(id=4, title="Four", status="pending"),
    ]
    tasks = [_task("done", 1, "done"), _task("remaining", 2, "pending")]
    penalties = []
    confidences = []
    for current_phase in (2, 3, 4):
        current_phases = [
            PlanPhase(
                id=phase.id,
                title=phase.title,
                status="active" if phase.id == current_phase else phase.status,
            )
            for phase in phases
        ]
        health = compute_operation_health(
            _plan(*current_phases, current_phase=current_phase),
            tasks,
            budget={"progress_percent": 75},
        )
        penalties.append(health["coverage_feasibility"]["penalty_fraction"])
        confidences.append(health["coverage_feasibility"]["phase_confidence"])

    assert confidences == [0.3333, 0.6667, 1.0]
    assert penalties[0] < penalties[1] < penalties[2]


def test_coverage_feasibility_uses_quadratic_shortfall_with_half_maximum_reduction():
    plan = _plan(
        PlanPhase(id=1, title="Discovery", status="done"),
        PlanPhase(id=2, title="Assessment", status="active"),
        current_phase=2,
    )
    tasks = [_task("done", 1, "done"), _task("remaining", 2, "pending")]

    partial = compute_operation_health(plan, tasks, budget={"progress_percent": 75})
    full = compute_operation_health(plan, tasks, budget={"progress_percent": 100})
    ample = compute_operation_health(plan, tasks, budget={"progress_percent": 25})

    assert partial["coverage_feasibility"]["shortfall"] == 0.5
    assert partial["feasibility"]["feasible"] is False
    assert partial["coverage_feasibility"]["penalty_fraction"] == 0.125
    assert full["coverage_feasibility"]["penalty_fraction"] == 0.5
    assert ample["coverage_feasibility"]["penalty_fraction"] == 0.0
    assert ample["feasibility"]["feasible"] is True
    assert full["score"] < partial["score"] < ample["score"]


def test_coverage_feasibility_counts_inventory_weight_and_missing_predicted_fanout():
    plan = _plan(
        PlanPhase(id=1, title="Inventory", status="done"),
        PlanPhase(id=2, title="Assessment", status="active"),
        current_phase=2,
    )
    tasks = [
        _task("inventory", 1, "done"),
        _task("route", 2, "pending", ("a", "b", "c")),
    ]
    prediction = {
        2: {
            "source_phase": 1,
            "target_phase": 2,
            "expected_tasks": 4,
            "confidence": "high",
            "basis": "inventory_manifest_fanout",
        }
    }

    health = compute_operation_health(
        plan,
        tasks,
        predictions=prediction,
        budget={"progress_percent": 50},
    )

    assert health["coverage_feasibility"]["completed_work"] == 1
    assert health["coverage_feasibility"]["remaining_work"] == 6
    assert health["coverage_feasibility"]["required_budget_ratio"] == 0.8571


def test_coverage_feasibility_is_unavailable_without_progress_or_work():
    plan = _plan(
        PlanPhase(id=1, title="Discovery", status="done"),
        PlanPhase(id=2, title="Assessment", status="active"),
        current_phase=2,
    )

    no_progress = compute_operation_health(plan, [_task("pending", 2, "pending")])
    no_work = compute_operation_health(plan, [], budget={"progress_percent": 50})
    reporting = compute_operation_health(
        plan,
        [_task("pending", 2, "pending")],
        budget={"progress_percent": 99, "assessment_active": False},
    )

    assert no_progress["coverage_feasibility"]["available"] is False
    assert no_progress["feasibility"]["feasible"] is True
    assert no_work["coverage_feasibility"]["available"] is False
    assert reporting["coverage_feasibility"]["available"] is False
    assert reporting["coverage_feasibility"]["penalty_applied"] is False
