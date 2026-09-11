#!/usr/bin/env python3
"""
Evaluation Manager for Cyber-AutoAgent
======================================

Coordinates bounded multi-agent evaluation for one operation.

This module provides:
- Tracking of operation trace registrations
- One combined execution evaluation per operation
- One assembled-report evaluation when a report exists
"""

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from modules.config.system.logger import get_logger

from ..handlers.events import EventEmitter, get_emitter
from .evaluation import CyberAgentEvaluator

logger = get_logger("Evaluation.Manager")


_GOAL_ACHIEVED_ACCEPTANCE_STATUSES = frozenset({"satisfied", "assessed_negative", "duplicate"})
_GOAL_ARCHIVED_TASK_STATUSES = frozenset({"replanned", "superseded"})


def _goal_value(value: Any, name: str, default: Any = None) -> Any:
    """Read one field from either a persisted model or a serialized test value."""

    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def build_goal_contract_facts(
    plan: Any,
    tasks: list[Any],
    acceptance_results_by_task: dict[str, list[Any]],
) -> dict[str, Any]:
    """Build controller-owned, evidence-backed goal-attainment facts for evaluation.

    One outcome criterion, or one frozen coverage inventory item, is one goal
    unit. Archived work is not current operation scope. A unit is achieved only
    when its owning task is done and its immutable acceptance ledger records a
    successful or valid-negative terminal result.
    """

    achieved_units = 0
    applicable_units = 0
    excluded_units = 0
    eligible_task_count = 0
    unachieved_reasons: dict[str, int] = {}

    def record_unit(result: Any, task_status: str) -> None:
        nonlocal achieved_units, applicable_units, excluded_units
        result_status = str(_goal_value(result, "status", "")).strip()
        if result_status == "excluded":
            excluded_units += 1
            return
        applicable_units += 1
        if task_status != "done":
            reason = f"task_status:{task_status or 'unknown'}"
        elif result is None:
            reason = "missing_acceptance_result"
        elif result_status in _GOAL_ACHIEVED_ACCEPTANCE_STATUSES:
            achieved_units += 1
            return
        elif result_status == "inaccessible":
            reason = "inaccessible"
        else:
            reason = f"acceptance_status:{result_status or 'missing'}"
        unachieved_reasons[reason] = unachieved_reasons.get(reason, 0) + 1

    for task in tasks:
        task_status = str(_goal_value(task, "status", "")).strip()
        if task_status in _GOAL_ARCHIVED_TASK_STATUSES:
            continue
        eligible_task_count += 1
        task_uid = str(_goal_value(task, "task_uid", "")).strip()
        results = acceptance_results_by_task.get(task_uid, [])
        results_by_criterion = {
            str(_goal_value(result, "criterion_id", "")).strip(): result
            for result in results
        }
        acceptance = _goal_value(task, "acceptance")
        mode = str(_goal_value(acceptance, "mode", "")).strip()
        if mode == "coverage":
            basis = _goal_value(acceptance, "basis")
            item_ids = _goal_value(basis, "item_ids", ()) or ()
            coverage_by_item = {
                str(_goal_value(item, "item_id", "")).strip(): item
                for result in results
                for item in (_goal_value(result, "coverage", ()) or ())
            }
            for item_id in item_ids:
                record_unit(coverage_by_item.get(str(item_id).strip()), task_status)
            continue

        for criterion in _goal_value(acceptance, "criteria", ()) or ():
            criterion_id = str(_goal_value(criterion, "id", "")).strip()
            record_unit(results_by_criterion.get(criterion_id), task_status)

    return {
        "goal_contract_attainment": {
            "version": 1,
            "achieved_units": achieved_units,
            "applicable_units": applicable_units,
            "excluded_units": excluded_units,
            "eligible_task_count": eligible_task_count,
            "unachieved_reasons": dict(sorted(unachieved_reasons.items())),
            "assessment_complete": bool(_goal_value(plan, "assessment_complete", False)),
        }
    }


class TraceType(Enum):
    """Types of traces that can be evaluated."""

    MAIN_AGENT = "main_agent"
    REPORT_GENERATION = "report_generation"
    SWARM_AGENT = "swarm_agent"


@dataclass
class TraceInfo:
    """Information about a trace to be evaluated."""

    trace_id: str
    trace_type: TraceType
    session_id: str
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)
    evaluated: bool = False
    evaluation_scores: dict[str, float] | None = None


class EvaluationManager:
    """
    Manages bounded evaluation for traces belonging to one operation.
    """

    def __init__(
        self,
        operation_id: str,
        emitter: EventEmitter | None = None,
        report_path: str | None = None,
        operation_objective: str | None = None,
        usage_callback: Callable[[dict[str, Any]], None] | None = None,
        progress_callback: Callable[[], None] | None = None,
        finding_records: list[dict[str, Any]] | None = None,
        operation_facts: dict[str, Any] | None = None,
    ):
        """
        Initialize the evaluation manager.

        Args:
            operation_id: The operation ID to manage evaluations for
        """
        self.operation_id = operation_id
        self.report_path = report_path
        self.operation_objective = operation_objective
        self.finding_records = list(finding_records or [])
        self.operation_facts = dict(operation_facts or {})
        self.traces: dict[str, TraceInfo] = {}
        self.evaluator: CyberAgentEvaluator | None = None
        self._lock = threading.Lock()
        self._evaluation_thread: threading.Thread | None = None
        self._evaluation_complete = threading.Event()
        self._emitter = emitter or get_emitter(operation_id=operation_id)
        self._usage_callback = usage_callback
        self._progress_callback = progress_callback
        self.last_failed_metrics: dict[str, str] = {}
        self.last_skipped_metrics: set[str] = set()
        self.last_scope_errors: dict[str, str] = {}

    def register_trace(
        self,
        trace_id: str,
        trace_type: TraceType,
        session_id: str,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Register a trace for evaluation.

        Args:
            trace_id: The unique trace ID
            trace_type: Type of trace (main_agent, report_generation, etc.)
            session_id: Session ID associated with the trace
            name: Human-readable name for the trace
            metadata: Optional metadata about the trace
        """
        with self._lock:
            self.traces[trace_id] = TraceInfo(
                trace_id=trace_id,
                trace_type=trace_type,
                session_id=session_id,
                name=name,
                metadata=metadata or {},
            )
            logger.info(
                "Registered trace for evaluation: %s (%s) - %s",
                trace_id,
                trace_type.value,
                name,
            )

    def get_trace_ids_by_type(self, trace_type: TraceType) -> list[str]:
        """
        Get all trace IDs of a specific type.

        Args:
            trace_type: The type of traces to retrieve

        Returns:
            List of trace IDs matching the specified type
        """
        with self._lock:
            return [
                trace_id
                for trace_id, info in self.traces.items()
                if info.trace_type == trace_type
            ]

    def get_unevaluated_traces(self) -> list[TraceInfo]:
        """
        Get all traces that haven't been evaluated yet.

        Returns:
            List of TraceInfo objects for unevaluated traces
        """
        with self._lock:
            return [info for info in self.traces.values() if not info.evaluated]

    async def evaluate_all_traces(self) -> dict[str, dict[str, float]]:
        """
        Evaluate all registered traces.

        Returns:
            Dictionary mapping trace IDs to their evaluation scores
        """
        # Initialize evaluator if not already done
        if not self.evaluator:
            evaluator_kwargs = {
                "emitter": self._emitter,
                "report_path": self.report_path,
                "finding_records": self.finding_records,
                "usage_callback": self._usage_callback,
                "progress_callback": self._progress_callback,
            }
            if self.operation_objective:
                evaluator_kwargs["operation_objective"] = self.operation_objective
            if self.operation_facts:
                evaluator_kwargs["operation_facts"] = self.operation_facts
            self.evaluator = CyberAgentEvaluator(**evaluator_kwargs)

        results = {}
        unevaluated = self.get_unevaluated_traces()

        if not unevaluated:
            logger.info(
                "No unevaluated traces found for operation %s", self.operation_id
            )
            return results

        logger.info(
            "Starting evaluation of %d traces for operation %s",
            len(unevaluated),
            self.operation_id,
        )

        # The evaluator performs one bounded operation aggregate and, when present,
        # one assembled-report evaluation. Invoke it once per operation, not once
        # for every registered role trace.
        try:
            scores = await self.evaluator.evaluate_trace(
                trace_id=self.operation_id,
                _max_retries=5,
            )
            numeric_scores = {}
            for key, value in (scores or {}).items():
                if isinstance(value, tuple) and value:
                    value = value[0]
                if isinstance(value, (int, float)):
                    numeric_scores[key] = float(value)

            self.last_failed_metrics = dict(getattr(self.evaluator, "last_failed_metrics", {}))
            self.last_skipped_metrics = set(getattr(self.evaluator, "last_skipped_metrics", set()))
            self.last_scope_errors = dict(getattr(self.evaluator, "last_scope_errors", {}))

            if numeric_scores:
                with self._lock:
                    for trace_info in unevaluated:
                        trace_info.evaluated = True
                        trace_info.evaluation_scores = numeric_scores
                results[self.operation_id] = numeric_scores
            else:
                logger.warning("No scores returned for operation %s", self.operation_id)
        except Exception as error:
            logger.error(
                "Error evaluating operation %s: %s",
                self.operation_id,
                error,
                exc_info=True,
            )

        logger.info(
            "Completed evaluation of operation %s: %d/%d traces evaluated successfully",
            self.operation_id,
            len(unevaluated) if results else 0,
            len(unevaluated),
        )

        return results

    def trigger_async_evaluation(self) -> None:
        """
        Trigger evaluation in a background thread.

        This method starts the evaluation process asynchronously and returns
        immediately.
        """
        if self._evaluation_thread and self._evaluation_thread.is_alive():
            logger.warning(
                "Evaluation already in progress for operation %s", self.operation_id
            )
            return

        def run_evaluation():
            """Run the evaluation in a separate thread."""
            try:
                logger.info(
                    "Starting async evaluation for operation %s", self.operation_id
                )
                asyncio.run(self.evaluate_all_traces())
                self._evaluation_complete.set()
            except Exception as e:
                logger.error(
                    "Error in async evaluation for operation %s: %s",
                    self.operation_id,
                    str(e),
                    exc_info=True,
                )
                self._evaluation_complete.set()

        self._evaluation_thread = threading.Thread(
            target=run_evaluation,
            name=f"evaluation-{self.operation_id}",
        )
        self._evaluation_thread.daemon = True
        self._evaluation_thread.start()

    def wait_for_completion(self, timeout: float | None = None) -> bool:
        """
        Wait for evaluation to complete.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if evaluation completed, False if timeout reached
        """
        if not self._evaluation_thread:
            return True

        return self._evaluation_complete.wait(timeout=timeout)

    def get_summary(self) -> dict[str, Any]:
        """
        Get a summary of the evaluation status.

        Returns:
            Dictionary containing evaluation summary information
        """
        with self._lock:
            total_traces = len(self.traces)
            evaluated_traces = sum(1 for t in self.traces.values() if t.evaluated)

            # Group by trace type
            by_type = {}
            for trace_info in self.traces.values():
                trace_type = trace_info.trace_type.value
                if trace_type not in by_type:
                    by_type[trace_type] = {"total": 0, "evaluated": 0}
                by_type[trace_type]["total"] += 1
                if trace_info.evaluated:
                    by_type[trace_type]["evaluated"] += 1

            return {
                "operation_id": self.operation_id,
                "total_traces": total_traces,
                "evaluated_traces": evaluated_traces,
                "evaluation_complete": evaluated_traces == total_traces,
                "by_type": by_type,
                "traces": [
                    {
                        "trace_id": info.trace_id,
                        "type": info.trace_type.value,
                        "name": info.name,
                        "evaluated": info.evaluated,
                        "score_count": len(info.evaluation_scores)
                        if info.evaluation_scores
                        else 0,
                    }
                    for info in self.traces.values()
                ],
            }
