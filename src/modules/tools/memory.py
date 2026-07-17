#!/usr/bin/env python3
"""
Tool for managing memories using Mem0 (store, delete, list, get, and retrieve)

This module provides comprehensive memory management capabilities using
Mem0 as the backend. It handles all aspects of memory management with
a user-friendly interface and proper error handling.

Key Features:
------------
1. Memory Management:
   • store: Add new memories with automatic ID generation and metadata
   • delete: Remove existing memories using memory IDs
   • list: Retrieve all memories for a user or agent
   • get: Retrieve specific memories by memory ID
   • retrieve: Perform semantic search across all memories

2. Task Management:
   • Work is broken into small tasks per phase with activation managed by these tools.

3. Safety Features:
   • Content previews before storage
   • Warning messages before deletion

4. Advanced Capabilities:
   • Automatic memory ID generation
   • Structured memory storage with metadata
   • Semantic search with relevance filtering
   • Rich output formatting
   • Support for both user and agent memories
   • Multiple vector database backends (OpenSearch, Mem0 Platform, FAISS)

5. Error Handling:
   • Memory ID validation
   • Parameter validation
   • Graceful API error handling
   • Clear error messages

6. Configurable Components:
   • Embedder (AWS Bedrock, Ollama, OpenAI)
   • LLM (AWS Bedrock, Ollama, OpenAI)
   • Vector Store (FAISS, OpenSearch, Mem0 Platform)
"""

import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

import boto3
from mem0 import Memory as Mem0Memory
from mem0 import MemoryClient
from opensearchpy import AWSV4SignerAuth, RequestsHttpConnection
from rapidfuzz import fuzz
from strands import tool

from modules.config.manager import MEM0_PROVIDER_MAP, get_config_manager
from modules.config.system.logger import get_logger
from modules.config.types import get_default_base_dir
from modules.handlers.utils import filter_none_values, sanitize_toon_value

# Set up logging
logger = get_logger("Tools.Memory")

# Global configuration and client
_MEMORY_CONFIG: Optional[Dict[str, str]] = None
_MEMORY_CLIENT: Optional["Mem0ServiceClient"] = None
_PLAN_STORE: Optional["PlanStore"] = None

# Thread lock for FAISS write safety (prevents corruption during concurrent writes)
_FAISS_WRITE_LOCK = threading.Lock()


PlanStatus = Literal["active", "pending", "done", "partial_failure", "blocked"]
TaskStatus = Literal["active", "pending", "done", "partial_failure", "blocked"]
TargetType = Literal["network", "network_range", "filesystem"]
TargetScope = Literal["all", "subset"]
TERMINAL_PLAN_STATUSES = ("done", "partial_failure", "blocked")


@dataclass(frozen=True)
class OperationTarget:
    """One executable target literal authorized for this operation."""

    target_id: str
    value: str
    type: TargetType
    source: str = "objective"

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or not self.target_id.strip():
            raise ValueError("target_id must be a non-empty string")
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("target value must be a non-empty string")
        if self.type not in ("network", "network_range", "filesystem"):
            raise ValueError("target type must be one of: network|network_range|filesystem")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("target source must be a non-empty string")

    @staticmethod
    def from_obj(obj: Any) -> "OperationTarget":
        if isinstance(obj, OperationTarget):
            return obj
        if not isinstance(obj, dict):
            raise ValueError("operation target must be an object/dict")
        return OperationTarget(
            target_id=str(obj.get("target_id", "")),
            value=str(obj.get("value", "")),
            type=str(obj.get("type", "network")),
            source=str(obj.get("source", "objective") or "objective"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "value": self.value,
            "type": self.type,
            "source": self.source,
        }

    @staticmethod
    def csv_format() -> str:
        return "target_id,value,type,source"

    def to_toon(self, include_format=True) -> str:
        lines = []
        if include_format:
            lines.append(f"operation_targets[1]{{{OperationTarget.csv_format()}}}:")
        lines.append(
            "  "
            + ",".join(
                [
                    sanitize_toon_value(self.target_id),
                    sanitize_toon_value(self.value),
                    sanitize_toon_value(self.type),
                    sanitize_toon_value(self.source),
                ]
            )
        )
        return "\n".join(lines).strip()


@dataclass(frozen=True)
class Task:
    """A single unit of work tied to an execution-prompt phase.

    Stored as a memory item with metadata.category == "task".
    Updates are written as new memories sharing the same task_uid.
    """

    task_uid: str
    title: str
    objective: str
    phase: int
    status: TaskStatus
    status_reason: Optional[str] = None
    evidence: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    kind: str = "standard"
    reference_id: Optional[str] = None
    target_scope: TargetScope = "all"
    target_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.task_uid, str) or not self.task_uid.strip():
            raise ValueError("task_uid must be a non-empty string")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title must be a non-empty string")
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("objective must be a non-empty string")
        if not isinstance(self.phase, int) or self.phase <= 0:
            raise ValueError("phase must be a positive int")
        if self.status not in ("active", "pending", "done", "partial_failure", "blocked"):
            raise ValueError("status must be one of: active|pending|done|partial_failure|blocked")
        if self.target_scope not in ("all", "subset"):
            raise ValueError("target_scope must be all or subset")
        if not isinstance(self.target_ids, list):
            raise ValueError("target_ids must be a list")
        normalized_ids = []
        for target_id in self.target_ids:
            target_id_text = str(target_id).strip()
            if target_id_text:
                normalized_ids.append(target_id_text)
        object.__setattr__(self, "target_ids", normalized_ids)
        if self.target_scope == "subset" and not self.target_ids:
            raise ValueError("target_ids required when target_scope is subset")

    @staticmethod
    def from_obj(obj: Any) -> "Task":
        if not isinstance(obj, dict):
            raise ValueError("task must be an object/dict")
        return Task(
            task_uid=str(obj.get("task_uid", "")),
            title=str(obj.get("title", "")),
            objective=str(obj.get("objective", "")),
            evidence=_normalize_evidence(obj.get("evidence", None)),
            phase=int(obj.get("phase")),
            status=str(obj.get("status", "pending")),
            status_reason=str(obj.get("status_reason", "")),
            created_at=obj.get("created_at"),
            updated_at=obj.get("updated_at"),
            kind=str(obj.get("kind", "standard") or "standard"),
            reference_id=obj.get("reference_id"),
            target_scope=str(obj.get("target_scope", "all") or "all"),
            target_ids=_normalize_target_ids(obj.get("target_ids", [])),
        )

    def to_dict(self) -> Dict[str, Any]:
        return filter_none_values({
            "task_uid": self.task_uid,
            "title": self.title,
            "objective": self.objective,
            "evidence": self.evidence,
            "phase": self.phase,
            "status": self.status,
            "status_reason": self.status_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "kind": self.kind,
            "reference_id": self.reference_id,
            "target_scope": self.target_scope,
            "target_ids": self.target_ids,
        })

    @staticmethod
    def toon_format() -> str:
        return f"task[1]{{{Task.csv_format()}}}"

    @staticmethod
    def csv_format() -> str:
        return "title,objective,evidence,phase,status,status_reason,kind,reference_id,target_scope,target_ids"

    def to_toon(self, include_format=True) -> str:
        title = sanitize_toon_value(self.title)
        objective = sanitize_toon_value(self.objective)
        evidence = "|".join(sanitize_toon_value(e) for e in self.evidence)
        status = sanitize_toon_value(self.status)
        status_reason = sanitize_toon_value(self.status_reason)
        kind = sanitize_toon_value(self.kind)
        reference_id = sanitize_toon_value(self.reference_id)
        target_ids = "|".join(sanitize_toon_value(target_id) for target_id in self.target_ids)
        lines = []
        if include_format:
            lines.append(f"{self.toon_format()}:")
        lines.append(
            f"  {title},{objective},{evidence},{self.phase},{status},{status_reason},{kind},"
            f"{reference_id},{self.target_scope},{target_ids}"
        )
        return "\n".join(lines).strip()

    @staticmethod
    def list_to_toon(tasks: List["Task"]) -> str:
        lines = [task.to_toon(include_format=False) for task in tasks]
        return f"task[{len(tasks)}]{{"+Task.csv_format()+"}:\n"+"\n".join(lines).strip()


@dataclass(frozen=True)
class PlanPhase:
    id: int
    title: str
    status: PlanStatus
    criteria: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, int) or self.id < 0:
            raise ValueError("phase.id must be a positive int")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("phase.title must be a non-empty string")
        if self.status not in ("active", "pending", "done", "partial_failure", "blocked"):
            raise ValueError("phase.status must be one of: active|pending|done|partial_failure|blocked")
        if self.criteria is None:
            object.__setattr__(self, "criteria", "")  # type: ignore[misc]
        if not isinstance(self.criteria, str):
            raise ValueError("phase.criteria must be a string")

    @staticmethod
    def from_obj(obj: Any) -> "PlanPhase":
        if not isinstance(obj, dict):
            raise ValueError("phase must be an object/dict")
        return PlanPhase(
            id=int(obj.get("id")),
            title=str(obj.get("title", "")),
            status=str(obj.get("status", "pending")),  # validated in __post_init__
            criteria=str(obj.get("criteria", "")) if obj.get("criteria") is not None else "",
        )

    @staticmethod
    def toon_format() -> str:
        return f"plan_phases[1]{{{PlanPhase.csv_format()}}}"

    @staticmethod
    def csv_format() -> str:
        return "id,title,status,criteria"

    def to_toon(self, include_format=True) -> str:
        title = sanitize_toon_value(self.title)
        status = sanitize_toon_value(self.status)
        criteria = sanitize_toon_value(self.criteria)
        lines = []
        if include_format:
            lines.append(f"{self.toon_format()}:")
        lines.append(f"  {self.id},{title},{status},{criteria}")
        return "\n".join(lines).strip()

    def to_dict(self) -> Dict[str, Any]:
        return filter_none_values({
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "criteria": self.criteria,
        })


@dataclass
class OperationPlan:
    objective: str
    current_phase: int
    total_phases: int
    phases: List[PlanPhase] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    targets: List[OperationTarget] = field(default_factory=list)
    assessment_complete: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("objective must be a non-empty string")
        if not isinstance(self.current_phase, int) or self.current_phase <= 0:
            raise ValueError("current_phase must be a positive int")
        if not isinstance(self.total_phases, int) or self.total_phases <= 0:
            raise ValueError("total_phases must be a positive int")
        if not isinstance(self.phases, list) or not self.phases:
            raise ValueError("phases must be a non-empty list")
        for p in self.phases:
            if not isinstance(p, PlanPhase):
                raise ValueError("phases must contain PlanPhase objects")
        targets = [OperationTarget.from_obj(target) for target in self.targets]
        self.targets = targets
        constraints: Any = self.constraints
        if constraints is None:
            constraints = []
        elif isinstance(constraints, str):
            constraints = [constraints]
        elif isinstance(constraints, tuple):
            constraints = list(constraints)
        elif not isinstance(constraints, list):
            raise ValueError("constraints must be a string, list, tuple, or null")

        normalized_constraints = []
        for constraint in constraints:
            if constraint is None or isinstance(constraint, (dict, list, tuple, set)):
                raise ValueError("constraints must contain values coercible to non-empty strings")
            try:
                normalized_constraint = str(constraint).strip()
            except Exception as error:
                raise ValueError("constraints must contain values coercible to non-empty strings") from error
            if not normalized_constraint:
                raise ValueError("constraints must contain values coercible to non-empty strings")
            normalized_constraints.append(normalized_constraint)
        self.constraints = normalized_constraints

        # enforce consistency
        if self.total_phases != len(self.phases):
            raise ValueError("total_phases must equal len(phases)")

        # current_phase must match an existing phase id
        phase_ids = {p.id for p in self.phases}
        if self.current_phase not in phase_ids:
            raise ValueError("current_phase must match one of the phase ids")

        # at most one active phase
        active_count = sum(1 for p in self.phases if p.status == "active")
        if active_count > 1:
            raise ValueError("only one phase may have status='active'")

    @staticmethod
    def from_obj(obj: Any) -> "OperationPlan":
        if isinstance(obj, OperationPlan):
            return obj
        if not isinstance(obj, dict):
            raise ValueError("plan content must be an object/dict")

        phases_raw = obj.get("phases")
        if not isinstance(phases_raw, list):
            raise ValueError("phases must be a list")

        phases = [PlanPhase.from_obj(p) for p in phases_raw]
        phases.sort(key=lambda p: p.id)
        return OperationPlan(
            objective=str(obj.get("objective", "")),
            current_phase=int(obj.get("current_phase", 1)),
            total_phases=len(phases),
            phases=phases,
            constraints=obj.get("constraints", []),
            targets=obj.get("targets", []),
            assessment_complete=bool(obj.get("assessment_complete", False)),
            created_at=obj.get("created_at"),
            updated_at=obj.get("updated_at"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return filter_none_values({
            "objective": self.objective,
            "current_phase": self.current_phase,
            "total_phases": self.total_phases,
            "phases": [p.to_dict() for p in self.phases],
            "constraints": self.constraints,
            "targets": [target.to_dict() for target in self.targets],
            "assessment_complete": self.assessment_complete,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })

    @staticmethod
    def toon_format() -> str:
        return "plan_overview[1]{objective,current_phase,total_phases}"

    def constraints_to_toon(self) -> str:
        lines = [f"plan_constraints[{len(self.constraints)}]{{constraint}}:"]
        lines.extend(f"  {sanitize_toon_value(constraint)}" for constraint in self.constraints)
        return "\n".join(lines)

    def to_toon(self, include_format=True) -> str:
        objective = sanitize_toon_value(self.objective)
        overview_lines = []
        if include_format:
            overview_lines.append(f"{self.toon_format()}:")
        overview_lines.append(f" {objective},{self.current_phase},{self.total_phases}")
        constraint_lines = self.constraints_to_toon().splitlines()
        target_lines = [f"operation_targets[{len(self.targets)}]{{{OperationTarget.csv_format()}}}:"]
        for target in self.targets:
            target_lines.append(target.to_toon(include_format=False))
        phase_lines = [f"plan_phases[{len(self.phases)}]{{id,title,status,criteria}}:"]
        for phase in self.phases:
            phase_lines.append(
                "  "
                + ",".join(
                    [
                        sanitize_toon_value(phase.id),
                        sanitize_toon_value(phase.title),
                        sanitize_toon_value(phase.status),
                        sanitize_toon_value(phase.criteria),
                    ]
                )
            )
        return "\n".join([*overview_lines, *constraint_lines, *target_lines, *phase_lines]).strip()


def _get_memory_base_path(config: Optional[Dict] = None) -> str:
    """Determine the base path for memory storage (FAISS, SQLite)."""
    # Use provided path or create unified output structure path
    if config and config.get("vector_store", {}).get("config", {}).get("path"):
        return config["vector_store"]["config"]["path"]

    # Create memory path using unified output structure
    target_name = (config or {}).get("target_name", "default_target")
    operation_id = (config or {}).get("operation_id", "default_operation")

    # Get output directory from environment or config
    output_dir = os.environ.get("CYBER_AGENT_OUTPUT_DIR") or (config or {}).get(
        "output_dir", get_default_base_dir()
    )

    # Memory isolation strategy (controlled via MEMORY_ISOLATION env var)
    # Options: "operation" (per-operation, safe for parallel) | "shared" (per-target, cross-learning)
    isolation_mode = os.environ.get("MEMORY_ISOLATION", "operation")

    if isolation_mode == "shared":
        # Shared per-target store (enables automatic cross-learning but parallel-unsafe)
        memory_base_path = os.path.join(output_dir, target_name, "memory")
        logger.debug("Memory mode: SHARED per-target at %s", memory_base_path)
    else:
        # Per-operation isolation (parallel-safe, explicit cross-learning needed)
        memory_base_path = os.path.join(output_dir, target_name, "memory", operation_id)
        logger.debug("Memory mode: ISOLATED per-operation at %s", memory_base_path)

    return memory_base_path


class PlanStore:
    """Persistence for OperationPlan and Task using SQLite.

    This replaces the use of Mem0 for storing plans and tasks, providing
    a simpler and more reliable local storage.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._bootstrap()

    def _bootstrap(self):
        """Initialize the database schema if it doesn't exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS plans (
                        operation_id TEXT PRIMARY KEY,
                        objective TEXT,
                        current_phase INTEGER,
                        total_phases INTEGER,
                        assessment_complete BOOLEAN,
                        plan_data TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        task_uid TEXT PRIMARY KEY,
                        operation_id TEXT,
                        title TEXT,
                        objective TEXT,
                        phase INTEGER,
                        status TEXT,
                        status_reason TEXT,
                        evidence TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_operation_id ON tasks(operation_id)")
                task_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(tasks)")
                }
                if "kind" not in task_columns:
                    conn.execute("ALTER TABLE tasks ADD COLUMN kind TEXT DEFAULT 'standard'")
                if "reference_id" not in task_columns:
                    conn.execute("ALTER TABLE tasks ADD COLUMN reference_id TEXT")
                if "target_scope" not in task_columns:
                    conn.execute("ALTER TABLE tasks ADD COLUMN target_scope TEXT DEFAULT 'all'")
                if "target_ids" not in task_columns:
                    conn.execute("ALTER TABLE tasks ADD COLUMN target_ids TEXT DEFAULT '[]'")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS finding_records (
                        finding_uid TEXT PRIMARY KEY,
                        operation_id TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        candidate_data TEXT NOT NULL,
                        verification_task_uid TEXT NOT NULL,
                        validation_data TEXT,
                        resolution TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(operation_id, fingerprint)
                    )
                """)

    def store_plan(self, operation_id: str, plan: OperationPlan):
        """Store or update a plan."""
        plan_dict = plan.to_dict()
        now = datetime.now().isoformat()
        if not plan.created_at:
            plan_dict["created_at"] = now
        plan_dict["updated_at"] = now

        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO plans (operation_id, objective, current_phase, total_phases, assessment_complete, plan_data, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(operation_id) DO UPDATE SET
                        objective=excluded.objective,
                        current_phase=excluded.current_phase,
                        total_phases=excluded.total_phases,
                        assessment_complete=excluded.assessment_complete,
                        plan_data=excluded.plan_data,
                        updated_at=excluded.updated_at
                """, (
                    operation_id,
                    plan.objective,
                    plan.current_phase,
                    plan.total_phases,
                    plan.assessment_complete,
                    json.dumps(plan_dict),
                    plan_dict["created_at"],
                    plan_dict["updated_at"]
                ))

    def get_plan(self, operation_id: str) -> Optional[OperationPlan]:
        """Retrieve a plan by operation_id."""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT plan_data FROM plans WHERE operation_id = ?", (operation_id,))
                row = cursor.fetchone()
                if row:
                    return OperationPlan.from_obj(json.loads(row[0]))
        return None

    def store_task(self, operation_id: str, task: Task):
        """Store or update a task."""
        task_dict = task.to_dict()
        now = datetime.now().isoformat()
        if not task.created_at:
            task_dict["created_at"] = now
        task_dict["updated_at"] = now

        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO tasks (
                        task_uid, operation_id, title, objective, phase, status, status_reason, evidence,
                        created_at, updated_at, kind, reference_id, target_scope, target_ids
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_uid) DO UPDATE SET
                        title=excluded.title,
                        objective=excluded.objective,
                        phase=excluded.phase,
                        status=excluded.status,
                        status_reason=excluded.status_reason,
                        evidence=excluded.evidence,
                        kind=excluded.kind,
                        reference_id=excluded.reference_id,
                        target_scope=excluded.target_scope,
                        target_ids=excluded.target_ids,
                        updated_at=excluded.updated_at
                """, (
                    task.task_uid,
                    operation_id,
                    task.title,
                    task.objective,
                    task.phase,
                    task.status,
                    task.status_reason,
                    json.dumps(task.evidence),
                    task_dict["created_at"],
                    task_dict["updated_at"],
                    task.kind,
                    task.reference_id,
                    task.target_scope,
                    json.dumps(task.target_ids),
                ))

    def get_tasks(self, operation_id: str) -> List[Task]:
        """Retrieve all tasks for an operation."""
        tasks = []
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT title, objective, phase, status, status_reason, evidence, task_uid, "
                    "created_at, updated_at, kind, reference_id, target_scope, target_ids "
                    "FROM tasks WHERE operation_id = ?",
                    (operation_id,),
                )
                for row in cursor:
                    tasks.append(
                        Task(
                            title=row[0],
                            objective=row[1],
                            phase=row[2],
                            status=row[3],
                            status_reason=row[4],
                            evidence=json.loads(row[5]),
                            task_uid=row[6],
                            created_at=row[7],
                            updated_at=row[8],
                            kind=row[9] or "standard",
                            reference_id=row[10],
                            target_scope=row[11] or "all",
                            target_ids=json.loads(row[12] or "[]"),
                        )
                    )
        return tasks

    def get_finding_by_fingerprint(self, operation_id: str, fingerprint: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT finding_uid, candidate_data, verification_task_uid, validation_data, resolution "
                    "FROM finding_records WHERE operation_id = ? AND fingerprint = ?",
                    (operation_id, fingerprint),
                ).fetchone()
        if not row:
            return None
        return {
            "finding_uid": row[0],
            "candidate_data": json.loads(row[1]),
            "verification_task_uid": row[2],
            "validation_data": json.loads(row[3]) if row[3] else None,
            "resolution": row[4],
        }

    def get_finding(self, operation_id: str, finding_uid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT fingerprint, candidate_data, verification_task_uid, validation_data, resolution "
                    "FROM finding_records WHERE operation_id = ? AND finding_uid = ?",
                    (operation_id, finding_uid),
                ).fetchone()
        if not row:
            return None
        return {
            "finding_uid": finding_uid,
            "fingerprint": row[0],
            "candidate_data": json.loads(row[1]),
            "verification_task_uid": row[2],
            "validation_data": json.loads(row[3]) if row[3] else None,
            "resolution": row[4],
        }

    def store_finding_candidate(
        self,
        operation_id: str,
        finding_uid: str,
        fingerprint: str,
        candidate_data: Dict[str, Any],
        verification_task_uid: str,
    ) -> None:
        now = datetime.now().isoformat()
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO finding_records "
                    "(finding_uid, operation_id, fingerprint, candidate_data, verification_task_uid, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        finding_uid,
                        operation_id,
                        fingerprint,
                        json.dumps(candidate_data),
                        verification_task_uid,
                        now,
                        now,
                    ),
                )

    def store_finding_validation(
        self,
        operation_id: str,
        finding_uid: str,
        validation_data: Dict[str, Any],
    ) -> None:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE finding_records SET validation_data = ?, updated_at = ? "
                    "WHERE operation_id = ? AND finding_uid = ?",
                    (json.dumps(validation_data), datetime.now().isoformat(), operation_id, finding_uid),
                )

    def resolve_finding(self, operation_id: str, finding_uid: str, resolution: str) -> None:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE finding_records SET resolution = ?, updated_at = ? "
                    "WHERE operation_id = ? AND finding_uid = ?",
                    (resolution, datetime.now().isoformat(), operation_id, finding_uid),
                )


def _get_plan_store() -> PlanStore:
    """Get or initialize the global plan store."""
    global _PLAN_STORE
    if _PLAN_STORE is None:
        base_path = _get_memory_base_path(_MEMORY_CONFIG)
        db_path = os.path.join(base_path, "plan_storage.db")
        print(f"[+] Plan Storage: {db_path}")
        _PLAN_STORE = PlanStore(db_path)
    return _PLAN_STORE


def _normalize_evidence(val: Any) -> List[str]:
    if val is None:
        return []

    def _to_s(x: Any) -> str:
        if x is None:
            return ""
        if isinstance(x, str):
            return x.strip()
        # Prefer stable JSON for dict-like evidence
        if isinstance(x, dict):
            try:
                return json.dumps(x, sort_keys=True)
            except Exception:
                return str(x).strip()
        return str(x).strip()

    if isinstance(val, list):
        out: List[str] = []
        for x in val:
            s = _to_s(x)
            if s:
                out.append(s)
        return out

    s = _to_s(val)
    return [s] if s else []


def _normalize_target_ids(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,|\s]+", value)
    elif isinstance(value, list):
        parts = value
    else:
        parts = [value]
    return [str(part).strip() for part in parts if str(part).strip()]


_RE_CIDR_TARGET = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b")
_RE_IP_TARGET = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_RE_HOST_PORT_TARGET = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}:\d{1,5}\b")
_RE_FQDN_TARGET = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")
_RE_QUOTED_PATH_TARGET = re.compile(r'["\']((?:/|\./|\.\./)[^"\']+)["\']')
_RE_BARE_TARGET_CONTEXT = re.compile(
    r"\b(?:target|host|endpoint|system|against|assess|scan|test|verify)\s+([a-zA-Z0-9][a-zA-Z0-9_.-]+)\b",
    re.IGNORECASE,
)


def _classify_target_literal(value: str, *, allow_bare_hostname: bool = False) -> Optional[TargetType]:
    stripped = value.strip().strip(".,;:)")
    if not stripped:
        return None
    if os.path.exists(os.path.expanduser(stripped)):
        return "filesystem"
    try:
        ipaddress.ip_network(stripped, strict=False)
        return "network_range" if "/" in stripped else "network"
    except ValueError:
        pass
    if _RE_URL_PATTERN.fullmatch(stripped) or _RE_HOST_PORT_TARGET.fullmatch(stripped) or _RE_FQDN_TARGET.fullmatch(stripped):
        return "network"
    if allow_bare_hostname and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", stripped):
        return "filesystem" if os.path.exists(os.path.expanduser(stripped)) else "network"
    return None


def _canonical_target_value(value: str, target_type: TargetType) -> str:
    stripped = value.strip().strip(".,;:)")
    if target_type == "filesystem":
        return os.path.realpath(os.path.expanduser(stripped))
    return stripped


def _target_candidates_from_text(text: str, *, allow_bare_hostname: bool, source: str) -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    for pattern in (_RE_URL_PATTERN, _RE_CIDR_TARGET, _RE_IP_TARGET, _RE_HOST_PORT_TARGET, _RE_FQDN_TARGET):
        candidates.extend((match.group(0), source) for match in pattern.finditer(text or ""))
    candidates.extend((match.group(0), source) for match in _RE_PATH_PATTERN.finditer(text or ""))
    candidates.extend((match.group(1), source) for match in _RE_QUOTED_PATH_TARGET.finditer(text or ""))
    if allow_bare_hostname:
        candidates.extend((match.group(1), source) for match in _RE_BARE_TARGET_CONTEXT.finditer(text or ""))
    return candidates


def resolve_operation_targets(logical_target: str, objective: str = "") -> List[OperationTarget]:
    """Resolve executable targets while keeping the CLI target as logical naming.

    Objective literals win. If the objective has no executable target literals, the logical target is used as a
    fallback and may be a bare hostname or path.
    """

    raw_candidates = _target_candidates_from_text(objective or "", allow_bare_hostname=True, source="objective")
    has_objective_targets = any(_classify_target_literal(value, allow_bare_hostname=True) for value, _ in raw_candidates)
    if not has_objective_targets:
        raw_candidates = _target_candidates_from_text(
            logical_target or "",
            allow_bare_hostname=True,
            source="logical_target_fallback",
        )
        if not raw_candidates and str(logical_target or "").strip():
            raw_candidates = [(str(logical_target), "logical_target_fallback")]

    targets: List[OperationTarget] = []
    seen: set[str] = set()
    for value, source in raw_candidates:
        target_type = _classify_target_literal(
            value,
            allow_bare_hostname=source == "logical_target_fallback" or source == "objective",
        )
        if not target_type:
            continue
        canonical = _canonical_target_value(value, target_type)
        if canonical.lower() in {"http", "https"}:
            continue
        if target_type == "network" and any(
            target.type == "network_range" and target.value.startswith(f"{canonical}/") for target in targets
        ):
            continue
        if target_type == "network" and any(
            target.type == "network" and canonical in target.value and canonical != target.value for target in targets
        ):
            continue
        key = f"{target_type}:{canonical.lower()}"
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            OperationTarget(
                target_id=f"target-{len(targets) + 1}",
                value=canonical,
                type=target_type,
                source=source,
            )
        )
    return targets


def _user_id(user_id: Optional[str] = None) -> str:
    if user_id:
        return user_id
    return (_MEMORY_CONFIG or {}).get("user_id", "cyber-agent")


def _agent_id(agent_id: Optional[str] = None) -> Optional[str]:
    return agent_id


def _operation_id(operation_id: Optional[str] = None) -> str:
    return operation_id or (_MEMORY_CONFIG or {}).get("operation_id", os.getenv("CYBER_OPERATION_ID", "default_operation"))


def memory_create_time(m: Dict[str, Any]) -> str:
    """Best-effort created_at extraction (metadata preferred, then top-level)."""
    meta = m.get("metadata", {})
    return str(m.get("created_at", meta.get("created_at", "")))


def memory_is_cross_operation() -> bool:
    return os.getenv("MEMORY_ISOLATION", "operation").lower() == "shared"


def _ensure_memory_client() -> "Mem0ServiceClient":
    """Ensure the global memory client is initialized and return it."""
    global _MEMORY_CLIENT
    if _MEMORY_CLIENT is None:
        # Always use silent mode for auto-init to prevent unwanted console output
        initialize_memory_system(silent=True)
    if _MEMORY_CLIENT is None:
        raise RuntimeError("Memory client could not be initialized")
    return _MEMORY_CLIENT


def normalize_confidence(conf_val: Any, cap_to: float | None = None) -> str:
    """Normalize confidence to a percentage string, optionally capping at cap_to."""
    try:
        if isinstance(conf_val, str) and conf_val.strip().endswith("%"):
            num = float(conf_val.strip().rstrip("%"))
        else:
            num = float(conf_val)
    except Exception:
        num = 0.0
    if cap_to is not None:
        num = min(num, cap_to)
    num = max(0.0, min(100.0, num))
    return f"{num:.1f}%"


_RE_PROOF_PACK_FILE_PATTERN = re.compile(r"artifact(?:\s+paths?)?:\s*(\S+)", re.IGNORECASE)

def _has_valid_proof_pack(finding: Any) -> bool:
    """Validate proof_pack structure and artifact existence (fail-closed).

    Expectations:
    - proof_pack is a dict with key 'artifacts': List[str] of file paths (absolute or relative)
    - Optional 'rationale': short string tying artifacts to impact
    - Every listed artifact path MUST exist at validation time

    Notes:
    - No content parsing or domain heuristics are used here; presence of files only
    - Any exception or malformed input results in False (fail-closed)
    """
    try:
        stack = [finding]
        while stack:
            e = stack.pop()
            if isinstance(e, list):
                stack.extend(e)
            elif isinstance(e, dict):
                stack.extend(e.values())
            else:
                e_str = str(e)
                if os.path.exists(e_str):
                    return True
                matches = _RE_PROOF_PACK_FILE_PATTERN.findall(e_str)
                file_paths = [path.strip() for paths in matches for path in paths.split(",")]
                for path in file_paths:
                    if os.path.exists(path):
                        return True
    except Exception:
        return False

    return False

def _clean_memory_text(value: Any, field_name: str) -> str:
    """Return compact, control-character-free memory text."""

    cleaned = str(value or "").replace("\x00", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    return cleaned


def _clean_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        cleaned[key] = _clean_memory_text(value, key) if isinstance(value, str) else value
    return cleaned


def _store_memory_entry(content: str, category: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Store one category-fixed semantic memory with duplicate protection."""

    cleaned_content = _clean_memory_text(content, "content")
    cleaned_metadata = _clean_metadata(metadata)
    cleaned_metadata["category"] = category
    op_id = _operation_id()
    cleaned_metadata["operation_id"] = op_id
    client = _ensure_memory_client()
    user_id = _user_id()

    try:
        search = client.mem0.search(
            query=cleaned_content,
            user_id=user_id,
            run_id=op_id,
            limit=1,
            filters={"category": category},
        )
    except Exception:
        search = {}
        logger.debug("Unable to check for memory duplicate", exc_info=True)

    if search.get("results"):
        best = search["results"][0]
        if best.get("score", 1.0) < 0.1:
            existing_patterns = set(_extract_sensitive_patterns(best.get("memory", "")))
            if existing_patterns == set(_extract_sensitive_patterns(cleaned_content)):
                return

    client.store_memory(cleaned_content, user_id, _agent_id(), cleaned_metadata)


def _operation_output_root() -> str:
    output_dir = os.environ.get("CYBER_AGENT_OUTPUT_DIR") or (_MEMORY_CONFIG or {}).get(
        "output_dir", get_default_base_dir()
    )
    return os.path.realpath(
        os.path.join(
            output_dir,
            (_MEMORY_CONFIG or {}).get("target_name", "default_target"),
            _operation_id(),
        )
    )


def _validated_artifact_paths(artifacts: Any, *, require_one: bool = False) -> List[str]:
    root = _operation_output_root()
    validated: List[str] = []
    for raw_path in _normalize_evidence(artifacts):
        candidate = raw_path if os.path.isabs(raw_path) else os.path.join(root, raw_path)
        resolved = os.path.realpath(candidate)
        if os.path.commonpath([root, resolved]) != root:
            raise ValueError(f"Artifact is outside the current operation output: {raw_path}")
        if not os.path.isfile(resolved):
            raise ValueError(f"Artifact does not exist: {raw_path}")
        validated.append(resolved)
    if require_one and not validated:
        raise ValueError("At least one existing artifact is required")
    return validated


@tool
def store_observation(
    content: str,
    artifacts: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Store one operation-specific fact, failed attempt, or informational result.

    Observations may be reportable but are never promoted into findings based on severity.
    """

    merged = _clean_metadata(metadata)
    if artifacts:
        merged["artifacts"] = _validated_artifact_paths(artifacts)
    _store_memory_entry(content, "observation", merged)
    return "Observation stored."


@tool
def store_knowledge(content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Store one reusable technique, lesson, or durable internal note.

    Knowledge remains retrievable but is excluded from security assessment reports.
    """

    _store_memory_entry(content, "knowledge", metadata)
    return "Knowledge stored."


def _finding_fingerprint(title: str, claim: str, target: str, technique: str) -> str:
    normalized = "|".join(
        re.sub(r"\s+", " ", value.strip().lower()) for value in (title, claim, target, technique)
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@tool
def store_finding(
    title: str,
    claim: str,
    severity: str,
    target: str,
    technique: str,
    expected_result: str,
    observed_result: str,
    reproduction_steps: List[str],
    artifacts: Optional[List[str]] = None,
) -> str:
    """Submit one finding candidate and create its dedicated verification task.

    This tool never creates a verified finding directly. Every distinct candidate is verified by a separate,
    same-phase task before it can affect confirmed risk totals.
    """

    candidate = {
        "title": _clean_memory_text(title, "title"),
        "claim": _clean_memory_text(claim, "claim"),
        "severity": str(severity or "MEDIUM").upper(),
        "target": _clean_memory_text(target, "target"),
        "technique": _clean_memory_text(technique, "technique"),
        "expected_result": _clean_memory_text(expected_result, "expected_result"),
        "observed_result": _clean_memory_text(observed_result, "observed_result"),
        "reproduction_steps": [_clean_memory_text(step, "reproduction step") for step in reproduction_steps],
    }
    if candidate["severity"] not in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}:
        raise ValueError("severity must be one of CRITICAL, HIGH, MEDIUM, LOW, INFO")
    if not candidate["reproduction_steps"]:
        raise ValueError("reproduction_steps requires at least one step")
    weak_evidence_terms = {
        "assume",
        "assumed",
        "hypothetical",
        "likely",
        "maybe",
        "suspected",
        "unread",
        "untested",
        "not tested",
        "not observed",
    }
    observed_lower = candidate["observed_result"].lower()
    if any(term in observed_lower for term in weak_evidence_terms):
        raise ValueError("observed_result must describe concrete observed evidence, not assumptions")
    candidate["artifacts"] = _validated_artifact_paths(artifacts, require_one=True)

    fingerprint = _finding_fingerprint(
        candidate["title"], candidate["claim"], candidate["target"], candidate["technique"]
    )
    op_id = _operation_id()
    store = _get_plan_store()
    existing = store.get_finding_by_fingerprint(op_id, fingerprint)
    if existing:
        return json.dumps(
            {
                "finding_uid": existing["finding_uid"],
                "verification_task_uid": existing["verification_task_uid"],
                "status": existing.get("resolution") or "pending_validation",
            },
            sort_keys=True,
        )

    finding_uid = str(uuid.uuid4())
    task_uid = str(uuid.uuid4())
    candidate["finding_uid"] = finding_uid
    candidate["validation_status"] = "pending"
    content = (
        f"[VULNERABILITY] {candidate['title']} [WHERE] {candidate['target']} "
        f"[IMPACT] {candidate['claim']} [EVIDENCE] {candidate['observed_result']} "
        f"[STEPS] {'; '.join(candidate['reproduction_steps'])}"
    )
    _store_memory_entry(content, "finding_candidate", candidate)

    current_phase = _get_plan_current_phase()
    target_ids = _target_ids_for_literal(candidate["target"])
    target_scope: TargetScope = "subset" if target_ids else "all"
    task = Task(
        task_uid=task_uid,
        title=f"Verify finding: {candidate['title']}",
        objective=(
            f"Independently verify finding candidate {finding_uid} against {candidate['target']}. "
            "Reproduce the claimed behavior, capture direct or differential artifacts, call "
            "record_finding_validation with the outcome, and stop."
        ),
        evidence=candidate["artifacts"],
        phase=current_phase,
        status="pending",
        kind="finding_validation",
        reference_id=finding_uid,
        target_scope=target_scope,
        target_ids=target_ids,
    )
    store.store_finding_candidate(op_id, finding_uid, fingerprint, candidate, task_uid)
    _ensure_memory_client().store_task(task=task, user_id=_user_id())
    return json.dumps(
        {"finding_uid": finding_uid, "verification_task_uid": task_uid, "status": "pending_validation"},
        sort_keys=True,
    )


@tool
def record_finding_validation(
    finding_uid: str,
    outcome: str,
    summary: str,
    reproduction_steps: List[str],
    evidence_strategy: str = "direct",
    evidence_artifacts: Optional[List[str]] = None,
    control_artifacts: Optional[List[str]] = None,
) -> str:
    """Record the result of the active, dedicated finding-verification task."""

    op_id = _operation_id()
    store = _get_plan_store()
    record = store.get_finding(op_id, finding_uid)
    if not record:
        raise ValueError("Unknown finding_uid for the current operation")
    active = [task for task in store.get_tasks(op_id) if task.status == "active"]
    if len(active) != 1 or active[0].task_uid != record["verification_task_uid"]:
        raise ValueError("Finding validation may only be recorded by its active verification task")

    normalized_outcome = str(outcome).strip().lower()
    if normalized_outcome not in {"confirmed", "not_confirmed"}:
        raise ValueError("outcome must be confirmed or not_confirmed")
    strategy = str(evidence_strategy).strip().lower()
    if strategy not in {"direct", "differential"}:
        raise ValueError("evidence_strategy must be direct or differential")
    evidence = _validated_artifact_paths(evidence_artifacts, require_one=normalized_outcome == "confirmed")
    controls = _validated_artifact_paths(control_artifacts)
    if normalized_outcome == "confirmed" and strategy == "differential" and not controls:
        raise ValueError("Differential confirmation requires at least one negative-control artifact")

    validation = {
        "finding_uid": finding_uid,
        "outcome": normalized_outcome,
        "summary": _clean_memory_text(summary, "summary"),
        "reproduction_steps": [_clean_memory_text(step, "reproduction step") for step in reproduction_steps],
        "evidence_strategy": strategy,
        "evidence_artifacts": evidence,
        "control_artifacts": controls,
        "validation_status": "submitted",
    }
    if not validation["reproduction_steps"]:
        raise ValueError("reproduction_steps requires at least one step")
    _store_memory_entry(validation["summary"], "finding_validation", validation)
    store.store_finding_validation(op_id, finding_uid, validation)
    return "Finding validation recorded for evaluator review."


def finalize_finding_validation(task: Task, evaluator_status: str, evaluator_reason: str) -> Optional[str]:
    """Materialize the evaluator-approved resolution for a verification task."""

    if task.kind != "finding_validation" or not task.reference_id:
        return None
    op_id = _operation_id()
    store = _get_plan_store()
    record = store.get_finding(op_id, task.reference_id)
    if not record or record.get("resolution"):
        return record.get("resolution") if record else None

    candidate = record["candidate_data"]
    validation = record.get("validation_data")
    confirmed = evaluator_status == "done" and validation and validation.get("outcome") == "confirmed"
    if confirmed:
        metadata = dict(candidate)
        metadata.update(validation)
        metadata.update(
            {
                "category": "finding",
                "status": "verified",
                "validation_status": "verified",
                "artifacts": validation["evidence_artifacts"],
                "negative_control_artifacts": validation["control_artifacts"],
            }
        )
        _store_memory_entry(candidate["claim"], "finding", metadata)
        resolution = "verified"
    else:
        reason = evaluator_reason or (
            validation.get("summary") if validation else "Verification was not completed"
        )
        metadata = dict(candidate)
        metadata.update(
            {
                "validation_status": "failed",
                "validation_reason": reason,
                "claimed_severity": candidate["severity"],
            }
        )
        if validation:
            metadata["validation"] = validation
        _store_memory_entry(candidate["claim"], "validation_failure", metadata)
        resolution = "validation_failure"
    store.resolve_finding(op_id, task.reference_id, resolution)
    return resolution


def get_plan() -> str:
    """Get the most recent active plan.
    Returns the plan or null if none found.
    """
    client = _ensure_memory_client()
    user_id = _user_id()
    op_id = None if memory_is_cross_operation() else _operation_id()
    logger.debug(f"get_active_plan(user_id={user_id}, operation_id={op_id})")
    plan = client.get_active_plan(user_id=user_id, operation_id=op_id)
    return plan.to_toon() if plan is not None else "No active plan."


@dataclass
class TaskCreate:
    title: str
    objective: str
    phase: Any = 0
    status: TaskStatus = "pending"
    evidence: Any = field(default_factory=list)
    target_scope: TargetScope = "all"
    target_ids: Any = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("title required")
        if not self.objective.strip():
            raise ValueError("objective required")
        if self.status not in ("active", "pending"):
            self.status = "pending"
            # raise ValueError("status must be one of: active|pending")
        self.phase = _normalize_task_phase(self.phase)
        # Coerce evidence to List[str] to tolerate dict/list-of-dict inputs from models
        self.evidence = _normalize_evidence(self.evidence)
        self.target_scope = str(self.target_scope or "all").strip().lower()
        if self.target_scope not in ("all", "subset"):
            raise ValueError("target_scope must be all or subset")
        self.target_ids = _normalize_target_ids(self.target_ids)
        if self.target_scope == "subset" and not self.target_ids:
            raise ValueError("target_ids required when target_scope is subset")

    @staticmethod
    def from_obj(obj: Any) -> "TaskCreate":
        if obj.__class__.__name__ == "TaskCreate":
            return obj
        if not isinstance(obj, dict):
            raise ValueError("task must be an object/dict")
        return TaskCreate(
            title=str(obj.get("title", "")),
            objective=str(obj.get("objective", "")),
            evidence=obj.get("evidence", None),
            phase=obj.get("phase", 0),
            status=str(obj.get("status", "pending")),
            target_scope=str(obj.get("target_scope", "all") or "all"),
            target_ids=obj.get("target_ids", []),
        )


def _normalize_task_phase(value: Any) -> int:
    """Coerce a task phase, reserving zero for omitted or malformed values."""

    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _get_active_plan() -> OperationPlan:
    client = _ensure_memory_client()
    user_id = _user_id()

    plan = client.get_active_plan(user_id=user_id, operation_id=_operation_id())
    if not plan:
        raise ValueError("no_active_plan")
    return plan


def _get_plan_current_phase() -> int:
    return int(_get_active_plan().current_phase)


def _target_ids_for_literal(target_value: str) -> List[str]:
    try:
        plan = _get_active_plan()
    except ValueError:
        return []
    normalized = str(target_value or "").strip().strip(".,;:)")
    matches = []
    for target in plan.targets:
        if target.value == normalized or target.value.rstrip("/") == normalized.rstrip("/"):
            matches.append(target.target_id)
    return matches


def _validate_task_target_scope(
    *,
    target_scope: TargetScope,
    target_ids: List[str],
    plan: Optional[OperationPlan],
    title: str,
    objective: str,
) -> Tuple[TargetScope, List[str]]:
    if plan is None or not plan.targets:
        return "all", []
    valid_ids = {target.target_id for target in plan.targets}
    if any(target_id.lower() in {"target", "target-id", "placeholder", "none"} for target_id in target_ids):
        raise ValueError("target_ids must reference concrete operation target IDs")
    invalid_ids = [target_id for target_id in target_ids if target_id not in valid_ids]
    if invalid_ids:
        raise ValueError(f"target_ids contain unknown operation target IDs: {', '.join(invalid_ids)}")
    if target_scope == "subset":
        return "subset", target_ids

    combined = f"{title}\n{objective}"
    matched_ids = [
        target.target_id
        for target in plan.targets
        if target.value and target.value in combined
    ]
    if len(matched_ids) == 1:
        return "subset", matched_ids
    if target_ids:
        return "subset", target_ids
    return "all", []


_RE_URL_PATTERN = re.compile(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*')
_RE_PATH_PATTERN = re.compile(r'(?:(?<=^)|(?<=\s))(?:/|\./|\.\./)[a-zA-Z0-9._\-/]+')

# Regex for UUID: 8-4-4-4-12 hex chars
_RE_UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
# Regex for numeric IDs: one or more digits, possibly preceded by = or /
_RE_NUMERIC_ID = re.compile(r'(?<=/|=)\d+(?=$|/|&|\s)')


def _normalize_id(text: str) -> str:
    """Replace UUIDs and numeric IDs with a placeholder."""
    # Replace UUIDs first
    text = _RE_UUID.sub(':id', text)
    # Replace numeric IDs
    text = _RE_NUMERIC_ID.sub(':id', text)
    return text


def _extract_sensitive_patterns(text: str) -> List[str]:
    """Extract URLs and potential file paths from text for strict matching."""
    # URL regex
    urls = _RE_URL_PATTERN.findall(text)

    # Simple file path heuristic: looks for strings starting with / or ./ or ../
    # and containing characters common in paths.
    paths = _RE_PATH_PATTERN.findall(text)

    # Normalize IDs in all extracted patterns
    all_patterns = [_normalize_id(p) for p in urls + paths]

    return sorted(list(set(all_patterns)))


@tool
def create_tasks(tasks: List[TaskCreate]) -> str:
    """Create one or more tasks.

    Required input shape:
    {"tasks": [{"title": "Short actionable title",
    "objective": "Action, target, context, and completion condition", "phase": 1,
    "status": "pending", "evidence": ["Expected artifact or completion signal"]}]}

    Every task requires non-empty "title" and "objective" strings. "phase", "status", and
    "evidence" are optional.
    Use status="pending" for new work. Never use unsupported "description" or "context" fields.

    Rules:
    - Valid active or future plan phases are preserved.
    - If status="active", any other active task in the same operation is demoted to pending.
    - If you identified N candidates, create N tasks (do not merge).

    Returns:
        Simple task creation confirmation.
    """

    # validate input, TaskCreate has post init validation
    if not tasks:
        raise ValueError("must have at least one task")
    tasks = [TaskCreate.from_obj(task) for task in tasks]

    client = _ensure_memory_client()
    user_id = _user_id()
    op_id = _operation_id()

    try:
        plan = _get_active_plan()
        current_phase = plan.current_phase
        valid_phase_ids = {phase.id for phase in plan.phases}
    except Exception:
        plan = None
        current_phase = 1
        valid_phase_ids = None

    existing_tasks = _get_plan_store().get_tasks(op_id)

    for new_task in tasks:
        # Default omitted phases to the active phase. Preserve every explicit,
        # valid plan phase, including future phases.
        requested_phase = new_task.phase
        if (
            valid_phase_ids is None
            or requested_phase not in valid_phase_ids
            or requested_phase < current_phase
        ):
            eff_phase = current_phase
        else:
            eff_phase = requested_phase

        title = str(new_task.title).strip()
        objective = str(new_task.objective).strip()
        target_scope, target_ids = _validate_task_target_scope(
            target_scope=new_task.target_scope,
            target_ids=new_task.target_ids,
            plan=plan,
            title=title,
            objective=objective,
        )

        # look for duplicate
        duplicate_task = None
        new_patterns = set(_extract_sensitive_patterns(title) + _extract_sensitive_patterns(objective))

        for et in existing_tasks:
            # If sensitive patterns (URLs/paths) are present, they must match exactly
            et_patterns = set(_extract_sensitive_patterns(et.title) + _extract_sensitive_patterns(et.objective))
            if new_patterns != et_patterns:
                continue

            title_score = fuzz.ratio(et.title.lower(), title.lower())
            objective_score = fuzz.ratio(et.objective.lower(), objective.lower())
            if title_score >= 90 and objective_score >= 90:
                duplicate_task = et
                break

        if duplicate_task:
            continue

        task_uid = str(uuid.uuid4())
        task = Task(
            task_uid=task_uid,
            title=title,
            objective=objective,
            evidence=new_task.evidence,
            phase=eff_phase,
            status=new_task.status,
            target_scope=target_scope,
            target_ids=target_ids,
        )

        client.store_task(task=task, user_id=user_id)
        existing_tasks.append(task)

    # Keep the output simple, giving too much info may be interpreted as instructions.
    return "Tasks created."


def _memory_list_markdown(memories: List[Dict[str, Any]]) -> str:
    if not memories:
        return ""
    memories.sort(key=memory_create_time, reverse=True)
    result = ""
    for m in memories:
        memory = m.get("memory", "")
        if not memory:
            continue
        result += f"- {memory}\n"
    return result


@tool
def mem0_list() -> str:
    """List operation findings, validation records, observations, and knowledge."""
    try:
        client = _ensure_memory_client()

        # Respect MEM0_LIST_LIMIT if set, default to 100 (matches retrieve/report limits)
        try:
            list_limit = int(os.getenv("MEM0_LIST_LIMIT", "100"))
        except Exception:
            list_limit = 100

        user_id = _user_id()
        agent_id = _agent_id()

        # Scope to current operation unless cross_operation=True
        cross_operation = memory_is_cross_operation()
        op_id = None if cross_operation else _operation_id()
        memories = client.list_memories(
            user_id, agent_id, limit=list_limit, run_id=op_id
        )

        # Debug logging to understand the response structure
        logger.debug("Memory list raw response type: %s, response: %s", type(memories), memories)

        results_list = memories or []
        logger.debug("memories is list with %d items", len(memories))

        if not results_list:
            return ""
        return _memory_list_markdown(results_list)
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def mem0_retrieve(
    query: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Semantic search across memories.

    REQUIRED:
    - query: natural language query

    OPTIONAL:
    - metadata: filter dict applied to metadata (e.g., {"category": "finding", "status": "verified"}).

    CROSS-SESSION LEARNING:
        - mem0_retrieve: Scoped to the current operation by default

        Cross-Learning Query Examples:
        - Learn from past: mem0_retrieve(query="SQLi techniques")
        - Skip verified: metadata={"status": "verified"} to find verified findings
        - Learn techniques: metadata={"category": "knowledge"}
        - Avoid failures: query for failed_technique or blocker in metadata

    Returns a list of memories.
    """
    try:
        if not query:
            raise ValueError("query is required")

        cross_operation = memory_is_cross_operation()
        op_id = None if cross_operation else _operation_id()

        user_id = _user_id()
        agent_id = _agent_id()

        # Debug: Log retrieval parameters
        logger.debug(
            "RETRIEVE query='%s', metadata_filters=%s, user_id=%s, run_id=%s, cross_operation=%s",
            query,
            metadata,
            user_id,
            op_id,
            cross_operation
        )

        # Use search() directly to support metadata filters (e.g., category, status)
        client = _ensure_memory_client()
        memories = client.search(
            query=query,
            filters=metadata,  # Pass metadata as filters for category/status filtering
            limit=100,
            user_id=user_id,
            agent_id=agent_id,
            run_id=op_id,
        )

        results_list = memories or []

        # Debug: Verify categories in retrieved memories
        if results_list:
            categories = {}
            for m in results_list:
                cat = m.get("metadata", {}).get("category", "MISSING")
                categories[cat] = categories.get(cat, 0) + 1
            logger.info(
                "RETRIEVE complete: %d memories, categories=%s",
                len(results_list),
                categories
            )
        else:
            logger.warning("RETRIEVE returned 0 results for query='%s'", query)
        return _memory_list_markdown(results_list)
    except Exception as e:
        return f"Error: {str(e)}"


class Mem0ServiceClient:
    """Lightweight client for Mem0 operations (store, search, list).

    Supports FAISS, OpenSearch, or Mem0 Platform based on environment.
    """

    @staticmethod
    def _remove_inactive(payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if payload is None:
            return []
        if not isinstance(payload, list):
            return payload
        payload[:] = [
            memory
            for memory in payload
            if not isinstance(memory, dict) or bool(memory.get("metadata", {}).get("active", True))
        ]
        return payload

    @staticmethod
    def _coerce_entry(entry: Any) -> Dict[str, Any]:
        """Ensure every entry behaves like a memory dict."""
        if isinstance(entry, dict):
            return entry
        if isinstance(entry, str):
            return {"memory": entry, "metadata": {}}
        if entry is None:
            return {"memory": "", "metadata": {}}
        # Fallback stringify for unexpected types (lists/tuples/etc.)
        try:
            text = (
                json.dumps(entry)
                if isinstance(entry, (list, tuple, set))
                else str(entry)
            )
        except Exception:  # pragma: no cover - defensive conversion
            text = str(entry)
        return {"memory": text, "metadata": {}}

    @staticmethod
    def _normalise_results_list(payload: Any) -> List[Dict[str, Any]]:
        """Best-effort conversion of Mem0 responses to a list of memory dicts."""
        if payload is None:
            return []
        if isinstance(payload, list):
            return Mem0ServiceClient._remove_inactive([Mem0ServiceClient._coerce_entry(entry) for entry in payload])
        if isinstance(payload, dict):
            for key in ("results", "memories", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return Mem0ServiceClient._remove_inactive([Mem0ServiceClient._coerce_entry(entry) for entry in value])
        return []

    @staticmethod
    def get_default_config(server: str = "bedrock") -> Dict:
        """Get default configuration from ConfigManager."""
        config_manager = get_config_manager()
        mem0_config = config_manager.get_mem0_service_config(server)

        # Add RequestsHttpConnection for OpenSearch if needed
        if mem0_config["vector_store"]["provider"] == "opensearch":
            mem0_config["vector_store"]["config"]["connection_class"] = (
                RequestsHttpConnection
            )

        return mem0_config

    def __init__(
        self,
        config: Optional[Dict] = None,
        has_existing_memories: bool = False,
        silent: bool = False,
    ):
        """Initialize the Mem0 service client.

        Args:
            config: Optional configuration dictionary to override defaults.
                   If provided, it will be merged with the default configuration.
            has_existing_memories: Whether memories already existed before initialization
            silent: If True, suppress initialization output (used during report generation)

        The client will use one of three backends based on environment variables:
        1. Mem0 Platform if MEM0_API_KEY is set
        2. OpenSearch if OPENSEARCH_HOST is set
        3. FAISS (default) if neither MEM0_API_KEY nor OPENSEARCH_HOST is set
        """
        self.region = None  # Initialize region attribute
        self.has_existing_memories = has_existing_memories  # Store existing memory info
        self.silent = silent  # Store silent flag for use in initialization methods
        self.mem0 = self._initialize_client(config)
        self.config = config  # Store config for later use

        # Display memory overview if existing memories are detected (unless silent)
        if not silent:
            self._display_startup_overview()

    def _initialize_client(self, config: Optional[Dict] = None) -> Any:
        """Initialize the appropriate Mem0 client based on environment variables.

        Args:
            config: Optional configuration dictionary to override defaults.

        Returns:
            An initialized Mem0 client (MemoryClient or Mem0Memory instance).
        """
        if os.environ.get("MEM0_API_KEY"):
            # Ensure base path exists for SQLite
            _get_memory_base_path(config)
            if not self.silent:
                print("[+] Memory Backend: Mem0 Platform (cloud)")
                print(
                    f"    API Key: {'*' * 8}{os.environ.get('MEM0_API_KEY', '')[-4:]}"
                )
            logger.debug("Using Mem0 Platform backend (MemoryClient)")
            return MemoryClient()

        # Determine provider type based on environment
        # When OpenSearch is enabled we default to Bedrock for AWS compatibility,
        # otherwise align with the active CYBER_AGENT_PROVIDER (fallback to Ollama)
        active_provider = os.environ.get("CYBER_AGENT_PROVIDER", "ollama").lower()
        if os.environ.get("OPENSEARCH_HOST"):
            server_type = "bedrock"
        elif active_provider in ("litellm", "bedrock", "ollama"):
            server_type = active_provider
        elif active_provider == "gemini":
            server_type = "gemini"
        else:
            server_type = "ollama"

        if os.environ.get("OPENSEARCH_HOST"):
            merged_config = self._merge_config(config, server_type)
            self._realign_provider_configs(merged_config)
            config_manager = get_config_manager()

            # Resolve provider labels
            def _provider_label(p: str) -> str:
                mapping = {
                    "aws_bedrock": "AWS Bedrock",
                    "ollama": "Ollama",
                    "azure_openai": "Azure OpenAI",
                    "openai": "OpenAI",
                    "anthropic": "Anthropic",
                    "cohere": "Cohere",
                    "gemini": "Google Gemini",
                    "huggingface": "Hugging Face",
                    "sagemaker": "Amazon SageMaker",
                    "groq": "Groq",
                }
                return mapping.get(p, p or "unknown")

            embedder_cfg = merged_config.get("embedder", {})
            llm_cfg = merged_config.get("llm", {})
            embedder_provider = embedder_cfg.get("provider", "")
            llm_provider = llm_cfg.get("provider", "")
            embedder_model = embedder_cfg.get("config", {}).get("model")
            llm_model = llm_cfg.get("config", {}).get("model")
            # Prefer dims from vector_store config if present
            dims = (
                merged_config.get("vector_store", {})
                .get("config", {})
                .get("embedding_model_dims", 1024)
            )
            embedder_region = (
                embedder_cfg.get("config", {}).get("aws_region")
                or config_manager.get_default_region()
            )

            if not self.silent:
                print("[+] Memory Backend: OpenSearch")
                print(f"    Host: {os.environ.get('OPENSEARCH_HOST')}")
                # Only show region for AWS-based providers
                if embedder_provider == "aws_bedrock" or llm_provider == "aws_bedrock":
                    print(f"    Region: {embedder_region}")
                print(
                    f"    Embedder: {_provider_label(embedder_provider)} - {embedder_model} ({dims} dims)"
                )
                print(f"    LLM: {_provider_label(llm_provider)} - {llm_model}")
            logger.debug("Using OpenSearch backend (Mem0Memory with OpenSearch)")
            return self._initialize_opensearch_client(config, server_type)

        # FAISS backend
        logger.debug("Using FAISS backend (Mem0Memory with FAISS)")
        return self._initialize_faiss_client(
            config, server_type, self.has_existing_memories
        )

    def _initialize_opensearch_client(
        self, config: Optional[Dict] = None, server: str = "bedrock"
    ) -> Mem0Memory:
        """Initialize a Mem0 client with OpenSearch backend."""
        merged_config = self._merge_config(config, server)
        
        # Ensure base path exists for SQLite
        _get_memory_base_path(merged_config)
        self._realign_provider_configs(merged_config)
        config_manager = get_config_manager()
        config_region = (
            merged_config.get("embedder", {}).get("config", {}).get("aws_region")
        )
        self.region = (
            config_region
            or os.environ.get("AWS_REGION")
            or config_manager.get_default_region()
        )

        if not os.environ.get("AWS_REGION"):
            os.environ["AWS_REGION"] = self.region

        # Set up AWS credentials
        session = boto3.Session()
        credentials = session.get_credentials()
        auth = AWSV4SignerAuth(credentials, self.region, "es")

        # Prepare configuration
        merged_config["vector_store"]["config"].update(
            {"http_auth": auth, "host": os.environ["OPENSEARCH_HOST"]}
        )

        return Mem0Memory.from_config(config_dict=merged_config)

    def _initialize_faiss_client(
        self,
        config: Optional[Dict] = None,
        server: str = "ollama",
        has_existing_memories: bool = False,
    ) -> Mem0Memory:
        """Initialize a Mem0 client with FAISS backend.

        Args:
            config: Optional configuration dictionary to override defaults.
            server: Server type for configuration.

        Returns:
            An initialized Mem0Memory instance configured for FAISS.

        Raises:
            ImportError: If faiss-cpu package is not installed.
        """

        merged_config = self._merge_config(config, server)

        # Initialize store existence flag
        store_existed_before = False

        faiss_path = _get_memory_base_path(merged_config)
        store_existed_before = os.path.exists(faiss_path)

        # Ensure the memory directory exists
        os.makedirs(faiss_path, exist_ok=True)

        merged_config["vector_store"]["config"]["path"] = faiss_path

        # Display FAISS configuration (unless silent mode for report generation)
        if not self.silent:
            print("[+] Memory Backend: FAISS (local)")
            print(f"    Store Location: {faiss_path}")

            # Display embedder/LLM configuration
            def _provider_label(p: str) -> str:
                mapping = {
                    "aws_bedrock": "AWS Bedrock",
                    "ollama": "Ollama",
                    "azure_openai": "Azure OpenAI",
                    "openai": "OpenAI",
                    "anthropic": "Anthropic",
                    "cohere": "Cohere",
                    "gemini": "Google Gemini",
                    "huggingface": "Hugging Face",
                    "sagemaker": "Amazon SageMaker",
                    "groq": "Groq",
                    "litellm": "LiteLLM",
                }
                return mapping.get(p, p or "unknown")

            embedder_config = merged_config.get("embedder", {})
            llm_config = merged_config.get("llm", {})
            embedder_provider = embedder_config.get("provider", "")
            llm_provider = llm_config.get("provider", "")
            embedder_model = embedder_config.get("config", {}).get("model")
            llm_model = llm_config.get("config", {}).get("model")
            # Prefer dims from vector_store config if present
            dims = (
                merged_config.get("vector_store", {})
                .get("config", {})
                .get("embedding_model_dims", 1024)
            )

            # Derive region only for AWS-based providers
            config_manager = get_config_manager()
            embedder_region = embedder_config.get("config", {}).get(
                "aws_region", config_manager.get_default_region()
            )

            # Show region only when relevant
            if embedder_provider == "aws_bedrock" or llm_provider == "aws_bedrock":
                print(f"    Region: {embedder_region}")

            # Pretty print providers
            print(
                f"    Embedder: {_provider_label(embedder_provider)} - {embedder_model} ({dims} dims)"
            )

            # If using LiteLLM for LLM, try to extract actual provider from model prefix for display
            display_llm_provider = _provider_label(llm_provider)
            if (
                llm_provider in ("", "litellm")
                and isinstance(llm_model, str)
                and "/" in llm_model
            ):
                prefix = llm_model.split("/", 1)[0].lower()
                display_llm_provider = _provider_label(
                    {
                        "bedrock": "aws_bedrock",
                        "ollama": "ollama",
                        "azure": "azure_openai",
                        "openai": "openai",
                        "anthropic": "anthropic",
                        "cohere": "cohere",
                        "gemini": "gemini",
                        "sagemaker": "sagemaker",
                        "groq": "groq",
                        "xai": "huggingface",
                        "mistral": "huggingface",
                    }.get(prefix, llm_provider)
                )

            print(f"    LLM: {display_llm_provider} - {llm_model}")

            # Display appropriate message based on whether store existed before initialization
            # Use has_existing_memories parameter which includes proper file size validation
            if has_existing_memories or store_existed_before:
                print(f"    Loading existing FAISS store from: {faiss_path}")
                print("    Memory will persist across operations for this target")
            else:
                # For fresh starts, just show the persistence message
                print("    Memory will persist across operations for this target")

        logger.debug("Initializing Mem0Memory with config: %s", merged_config)
        try:
            mem0_client = Mem0Memory.from_config(config_dict=merged_config)
            logger.debug("Mem0Memory client initialized successfully")
            return mem0_client
        except Exception as e:
            # Check if this is an Ollama network error (model may already exist locally)
            error_msg = str(e)
            if "connection reset" in error_msg or "pull model manifest" in error_msg:
                logger.warning(
                    "Ollama network error during model pull - model may already exist locally, retrying initialization..."
                )
                # Retry once without forcing model pull (Mem0 will use existing local model)
                try:
                    mem0_client = Mem0Memory.from_config(config_dict=merged_config)
                    logger.info(
                        "Mem0Memory initialized successfully on retry (using existing local model)"
                    )
                    return mem0_client
                except Exception as retry_error:
                    logger.error("Retry failed: %s", retry_error)
                    raise retry_error
            elif (
                "Unknown provider in model" in error_msg
                or "Unsupported LLM provider" in error_msg
            ):
                logger.warning(
                    "Mem0 provider mismatch detected (%s). Applying OpenAI-compatible fallback.",
                    error_msg,
                )
                self._realign_provider_configs(merged_config, force_openai=True)
                try:
                    mem0_client = Mem0Memory.from_config(config_dict=merged_config)
                    logger.info(
                        "Mem0Memory initialized successfully after provider fallback"
                    )
                    return mem0_client
                except Exception as retry_error:
                    logger.error("Provider fallback failed: %s", retry_error)
                    raise retry_error
            else:
                logger.error("Failed to initialize Mem0Memory client: %s", e)
                raise

    def _merge_config(
        self, config: Optional[Dict] = None, server: str = "bedrock"
    ) -> Dict:
        """Merge user-provided configuration with default configuration.

        Args:
            config: Optional configuration dictionary to override defaults.
            server: Server type for configuration.

        Returns:
            A merged configuration dictionary.
        """
        merged_config = self.get_default_config(server).copy()
        if not config:
            return merged_config

        # Deep merge the configs
        for key, value in config.items():
            if (
                key in merged_config
                and isinstance(value, dict)
                and isinstance(merged_config[key], dict)
            ):
                merged_config[key].update(value)
            else:
                merged_config[key] = value

        return merged_config

    @staticmethod
    def _split_model_identifier(model_id: Any) -> Tuple[str, str]:
        if not isinstance(model_id, str):
            return "", ""
        if "/" in model_id:
            prefix, remainder = model_id.split("/", 1)
            return prefix.lower(), remainder
        return "", model_id

    def _inject_azure_defaults(
        self, section_config: Dict[str, Any], deployment: str
    ) -> None:
        section_config["azure_kwargs"] = {
            "api_key": os.getenv("AZURE_API_KEY", ""),
            "azure_deployment": deployment,
            "azure_endpoint": os.getenv("AZURE_API_BASE", ""),
            "api_version": os.getenv("AZURE_API_VERSION", ""),
        }
        azure_kwargs = section_config["azure_kwargs"]
        if not all(azure_kwargs.values()):
            logger.warning(
                "Azure OpenAI credentials appear incomplete. Values set: endpoint=%s, deployment=%s",
                azure_kwargs.get("azure_endpoint"),
                azure_kwargs.get("azure_deployment"),
            )

    def _realign_provider_configs(
        self, merged_config: Dict[str, Any], *, force_openai: bool = False
    ) -> None:
        """Ensure Mem0 provider sections match the selected model identifiers."""
        if force_openai and not os.getenv("OPENAI_API_KEY"):
            logger.warning(
                "Skipping OpenAI provider fallback because OPENAI_API_KEY is not set"
            )
            force_openai = False
        for section_key in ("embedder", "llm"):
            section = merged_config.get(section_key)
            if not isinstance(section, dict):
                continue
            config_section = section.setdefault("config", {})
            model_id = config_section.get("model")
            provider = (section.get("provider") or "").lower()

            if force_openai and section_key == "llm":
                section["provider"] = "openai"
                if not isinstance(model_id, str) or "/" in model_id or not model_id:
                    config_section["model"] = os.getenv(
                        "MEM0_FALLBACK_LLM_MODEL", "gpt-4o-mini"
                    )
                continue

            if not isinstance(model_id, str):
                continue
            prefix, remainder = self._split_model_identifier(model_id)
            if not prefix:
                continue
            mapped_provider = MEM0_PROVIDER_MAP.get(prefix)
            if not mapped_provider:
                continue

            if mapped_provider == provider:
                if mapped_provider == "azure_openai" and remainder:
                    config_section["model"] = remainder
                    self._inject_azure_defaults(config_section, remainder)
                continue

            if provider not in ("aws_bedrock", "", "ollama", "litellm"):
                continue

            section["provider"] = mapped_provider
            if remainder:
                config_section["model"] = remainder
            if mapped_provider == "azure_openai":
                self._inject_azure_defaults(
                    config_section, remainder or config_section.get("model", "")
                )
            logger.warning(
                "Aligned Mem0 %s provider from '%s' to '%s' for model '%s'",
                section_key,
                provider or "unknown",
                mapped_provider,
                model_id,
            )

    def store_memory(
        self,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ):
        """Store a memory in Mem0 with native operation scoping via run_id.

        Uses run_id for mem0's native operation isolation instead of manual metadata filtering.
        This provides O(log n) indexed lookups vs O(n) local filtering.
        """
        user_id = _user_id(user_id)
        if not user_id and not agent_id:
            raise ValueError("Either user_id or agent_id must be provided")

        # Default agent_id to user_id to avoid null actor attribution in some backends
        if user_id and not agent_id:
            agent_id = user_id

        metadata = metadata or {}

        # Get operation ID for native session scoping
        op_id = _operation_id()

        messages = [{"role": "user", "content": content}]
        try:
            # For cybersecurity findings, use infer=False to ensure all data is stored
            # regardless of mem0's fact filtering (critical for security assessments)
            # Use session_id=operation_id for mem0's native operation isolation
            add_kwargs = {
                "messages": messages,
                "user_id": user_id,
                "agent_id": agent_id,
                "metadata": metadata,
                "infer": False,
            }

            # Add run_id for native operation scoping (mem0 1.0.0 API)
            if op_id:
                add_kwargs["run_id"] = op_id
                metadata["operation_id"] = op_id

            # Debug: Log metadata BEFORE storage
            logger.debug(
                "BEFORE mem0.add() - category=%s, metadata=%s",
                metadata.get("category") if metadata else "none",
                metadata
            )

            # Use thread lock for FAISS write safety (prevents index corruption
            # during concurrent writes from swarm agents)
            with _FAISS_WRITE_LOCK:
                result = self.mem0.add(**add_kwargs)

            # Debug: Verify what was actually stored
            logger.info(
                "Memory stored successfully - run_id=%s, category=%s, result=%s",
                op_id or "none",
                metadata.get("category") if metadata else "none",
                result
            )

            return result
        except Exception as e:
            logger.error("Critical error storing memory: %s", str(e), exc_info=True)
            logger.error("Exception type: %s", type(e).__name__)
            logger.error("Exception args: %s", e.args)
            raise RuntimeError(f"Memory storage failed: {str(e)}") from e

    def list_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        *,
        limit: Optional[int] = None,
        page: int = 1,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List memories for a user/agent with safe defaults and pagination.

        Args:
            user_id: User identifier
            agent_id: Agent identifier
            limit: Maximum number of memories to return
            page: Page number for pagination
            run_id: Operation/session ID for scoping (None = all operations)

        Falls back gracefully if backend doesn't support limit/page/run_id.
        """
        user_id = _user_id(user_id)
        if not user_id and not agent_id:
            raise ValueError("Either user_id or agent_id must be provided")

        logger.debug(
            "Calling mem0.get_all with user_id=%s, agent_id=%s, run_id=%s",
            user_id, agent_id, run_id
        )

        # Determine effective limit from env or passed arg (default 100 for report consistency)
        try:
            default_limit = int(os.getenv("MEM0_LIST_LIMIT", "100"))
        except Exception:
            default_limit = 100
        eff_limit = (
            int(limit) if isinstance(limit, int) and limit > 0 else default_limit
        )

        # Build base kwargs
        base_kwargs = {}
        if user_id:
            base_kwargs["user_id"] = user_id
        if agent_id:
            base_kwargs["agent_id"] = agent_id
        if run_id:
            base_kwargs["run_id"] = run_id

        # Try variants: with limit/page, with limit only, then no args
        # Normalize and slice to eff_limit as a last resort
        try:
            try:
                result = self.mem0.get_all(
                    **base_kwargs, limit=eff_limit, page=page
                )
            except TypeError:
                try:
                    result = self.mem0.get_all(
                        **base_kwargs, limit=eff_limit
                    )
                except TypeError:
                    try:
                        result = self.mem0.get_all(**base_kwargs)
                    except TypeError as te:
                        if "run_id" in base_kwargs:
                            no_run_id = base_kwargs.copy()
                            no_run_id.pop("run_id")
                            result = self.mem0.get_all(**no_run_id)
                        else:
                            raise te
            logger.debug("mem0.get_all returned type: %s", type(result))
            # Normalize structures
            normalised = self._normalise_results_list(result)
            return normalised[:eff_limit]
        except Exception as e:
            logger.error("Error in mem0.get_all: %s", e)
            raise

    def get_memory_by_id(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a single memory by its Mem0 ID.

        Mem0 backends have used slightly different ``get`` signatures, so this
        method tries the direct ID form first and then keyword variants. The
        returned value is normalized to the same dictionary shape used by list
        and search helpers.
        """

        memory_id = str(memory_id or "").strip()
        if not memory_id:
            return None
        if not hasattr(self.mem0, "get"):
            raise AttributeError("Mem0 backend does not support get")

        base_kwargs: Dict[str, Any] = {}
        user_id = _user_id(user_id)
        if user_id:
            base_kwargs["user_id"] = user_id
        if agent_id:
            base_kwargs["agent_id"] = agent_id

        attempts = [
            lambda: self.mem0.get(memory_id),
            lambda: self.mem0.get(memory_id=memory_id, **base_kwargs),
            lambda: self.mem0.get(id=memory_id, **base_kwargs),
        ]
        result: Any = None
        last_type_error: Optional[TypeError] = None
        for attempt in attempts:
            try:
                result = attempt()
                break
            except TypeError as error:
                last_type_error = error
        else:
            if last_type_error:
                raise last_type_error

        entries = self._normalise_results_list(result)
        if entries:
            return entries[0]
        if isinstance(result, dict):
            entries = self._remove_inactive([self._coerce_entry(result)])
            if entries:
                return entries[0]
        if isinstance(result, str):
            return self._coerce_entry(result)
        return None

    def search_memories(
            self,
            query: str,
            user_id: Optional[str] = None,
            agent_id: Optional[str] = None,
            run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search memories using semantic search."""
        user_id = _user_id(user_id)
        if not user_id and not agent_id:
            raise ValueError("Either user_id or agent_id must be provided")

        # Delegate to the compatibility search helper for normalized results
        return self.search(
            query=query,
            filters=None,
            limit=20,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
        )

    def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        *,
            user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Compatibility wrapper providing Mem0-style search with filter support.

        Args:
            query: Semantic search query
            filters: Metadata filters (legacy - use run_id for operation scoping)
            limit: Maximum results to return (default 100 for report consistency)
            user_id: User identifier
            agent_id: Agent identifier
            run_id: Run/operation ID for native mem0 scoping (recommended)

        Returns:
            List of memory dictionaries with 'memory' and 'metadata' fields
        """

        user_id = _user_id(user_id)
        filters = filters or {}
        top_k = max(int(limit or 100), 1)

        # Try native Mem0 search first (covers FAISS/OpenSearch/Platform backends)
        if hasattr(self.mem0, "search"):
            search_kwargs: Dict[str, Any] = {"user_id": user_id}
            if agent_id:
                search_kwargs["agent_id"] = agent_id

            # Prefer run_id for operation scoping (mem0 1.0.0 API)
            if run_id:
                search_kwargs["run_id"] = run_id
                logger.debug("Using run_id=%s for native operation scoping", run_id)

            # Pass filters to mem0's native search (supports advanced operators like "in")
            if filters:
                search_kwargs["filters"] = filters

            for size_kw in ("top_k", "limit"):
                try:
                    search_kwargs[size_kw] = top_k
                    results = self.mem0.search(query=query, **search_kwargs)
                    normalised = self._normalise_results_list(results)
                    if normalised:
                        return normalised[:top_k]
                except TypeError:
                    search_kwargs.pop(size_kw, None)
                except Exception as exc:  # pragma: no cover - backend specific
                    logger.debug("Native Mem0 search failed (%s): %s", size_kw, exc)
                    break

        # Fallback: list memories and apply lightweight filtering locally
        try:
            # Pass run_id to list_memories for consistent scoping
            all_memories = self.list_memories(
                user_id=user_id, agent_id=agent_id, run_id=run_id
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("Fallback memory listing failed during search: %s", exc)
            return []

        # If run_id was provided but list_memories didn't filter (backend limitation),
        # apply local filtering by operation_id in metadata
        if run_id:
            all_memories = [
                e for e in all_memories
                if e.get("metadata", {}).get("operation_id") == run_id
                or e.get("run_id") == run_id
            ]

        def _matches_filters(entry: Dict[str, Any]) -> bool:
            """Match filters with support for simple list values (FAISS-compatible)."""
            metadata = entry.get("metadata", {}) or {}
            for key, value in filters.items():
                meta_val = metadata.get(key)
                # Handle list filter values (e.g., {"category": ["finding", "observation"]})
                if isinstance(value, list):
                    if meta_val not in value:
                        return False
                elif str(meta_val) != str(value):
                    return False
            return True

        if query:
            terms = [term.lower() for term in re.split(r"\s+", query) if term]
        else:
            terms = []

        results: List[Dict[str, Any]] = []
        for entry in all_memories:
            if filters and not _matches_filters(entry):
                continue

            if terms:
                text = " ".join(
                    str(part)
                    for part in (
                        entry.get("memory"),
                        entry.get("content"),
                        json.dumps(entry.get("metadata", {}), default=str),
                    )
                    if part
                ).lower()
                if not all(term in text for term in terms):
                    continue

            results.append(entry)
            if len(results) >= top_k:
                break

        return results

    def _display_startup_overview(self) -> None:
        """Display memory overview at startup if memories exist."""
        try:
            # Ensure _PLAN_STORE is initialized
            _get_plan_store()
            
            # For Mem0 Platform & OpenSearch - always display (remote backends)
            # For FAISS - only if memories existed before init
            should_display = (
                os.environ.get("MEM0_API_KEY")
                or os.environ.get("OPENSEARCH_HOST")
                or self.has_existing_memories
            )

            if not should_display:
                return

            # Get and display overview
            overview = self.get_memory_overview(user_id=_user_id())

            if overview.get("error"):
                print(
                    f"    Warning: Could not retrieve memory overview: {overview['error']}"
                )
                return

            if not overview.get("has_memories"):
                print("    No existing memories found - starting fresh")
                return

            # Display overview
            total = overview.get("total_count", 0)
            categories = overview.get("categories", {})
            recent_findings = overview.get("recent_findings", [])

            print(f"    Found {total} existing memories:")

            # Show category breakdown
            if categories:
                category_parts = [
                    f"{count} {category}" for category, count in categories.items()
                ]
                print(f"      Categories: {', '.join(category_parts)}")

            # Show recent findings
            if recent_findings:
                print("      Recent findings:")
                for i, finding in enumerate(recent_findings[:3], 1):
                    content = finding.get("content", "")
                    if len(content) > 80:
                        content = content[:77] + "..."
                    print(f"        {i}. {content}")

            print("    Memory will be loaded as first action to avoid duplicate work")

        except Exception as e:
            logger.debug("Could not display startup memory overview: %s", str(e))
            print(f"    Note: Could not check existing memories: {str(e)}")

    def store_plan(
        self,
        plan: OperationPlan,
        user_id: Optional[str] = None,
        operation_id: Optional[str] = None
    ) -> Dict:
        """Store a strategic plan.

        Args:
            plan: The strategic plan with required fields

        Returns:
            Status result
        """
        # Check if all phases complete and add workflow reminder
        all_done = all(p.status in TERMINAL_PLAN_STATUSES for p in plan.phases)
        add_completion_reminder = False
        if all_done and not plan.assessment_complete:
            plan.assessment_complete = True
            add_completion_reminder = True
            logger.info("All phases complete - set assessment_complete=true")

        op_id = _operation_id(operation_id)

        result = {}

        # Warn if extending plan after marking complete
        try:
            prev_plan = _get_plan_store().get_plan(op_id)
            if prev_plan:
                new_total = int(plan.total_phases)
                if prev_plan.assessment_complete and new_total > int(prev_plan.total_phases):
                    result["_reminder"] = (
                        f"Adding phases ({prev_plan.total_phases} → {new_total}) after assessment_complete=true. "
                        "Consider allowing workflow completion and report generation instead."
                    )
        except Exception as e:
            logger.debug(f"Could not check previous plan for extension: {e}")

        _get_plan_store().store_plan(op_id, plan)

        result["status"] = "success"
        result["plan"] = plan.to_toon()
        result["operation_id"] = op_id

        if add_completion_reminder:
            result["_reminder"] = (
                "All phases complete. Python workflow will evaluate completion and generate the report."
            )

        return result

    def get_active_plan(
            self,
            user_id: Optional[str] = None,
            operation_id: Optional[str] = None
    ) -> Optional[OperationPlan]:
        """Get the most recent plan.

        Args:
            user_id: User ID (ignored)
            operation_id: Optional operation ID to scope plan selection

        Returns:
            Most recent active plan or None if no plans found
        """
        op_id = _operation_id(operation_id)

        try:
            return _get_plan_store().get_plan(op_id)
        except Exception as e:
            logger.error(f"Error retrieving active plan: {e}")
            return None

    def _select_latest_by_uid(
            self, entries: List[Dict[str, Any]], uid_key: str
    ) -> Dict[str, Dict[str, Any]]:
        """Group entries by uid_key and keep the newest by created_at."""
        latest: Dict[str, Dict[str, Any]] = {}
        for e in entries or []:
            meta = e.get("metadata", {}) or {}
            uid = str(meta.get(uid_key, "") or "")
            if not uid:
                continue
            prev = latest.get(uid)
            if not prev:
                latest[uid] = e
                continue
            if memory_create_time(e) >= memory_create_time(prev):
                latest[uid] = e
        return latest

    def _list_tasks_latest(
            self,
            *,
            user_id: str,
            run_id: Optional[str],
    ) -> List[Task]:
        """Return latest-version task objects for a run_id (operation)"""
        op_id = _operation_id(run_id)
        tasks = _get_plan_store().get_tasks(op_id)
        # Sort by created_at desc
        tasks.sort(key=lambda x: x.created_at or "", reverse=True)
        return tasks

    def _task_from_memory(self, mem: Dict[str, Any]) -> Optional[Task]:
        meta = (mem.get("metadata", {}) or {})
        try:
            return Task.from_obj(meta)
        except Exception:
            return None

    def store_task(
            self,
            *,
            task: Task,
            user_id: Optional[str] = None,
    ):
        """Store (or update) a task."""
        op_id = _operation_id()

        # Enforce only one active task per operation by demoting any existing active task
        if task.status == 'active':
            try:
                all_tasks = _get_plan_store().get_tasks(op_id)
                for t in all_tasks:
                    if t.task_uid != task.task_uid and t.status == "active":
                        # Demote current active task
                        demoted = Task(
                            task_uid=t.task_uid,
                            title=t.title,
                            objective=t.objective,
                            evidence=t.evidence,
                            phase=t.phase,
                            status="pending",
                            status_reason="demoted",
                            created_at=t.created_at,
                            kind=t.kind,
                            reference_id=t.reference_id,
                        )
                        _get_plan_store().store_task(op_id, demoted)
            except Exception as e:
                logger.debug("Could not enforce single active task: %s", e)

        _get_plan_store().store_task(op_id, task)

    def advance_task_in_phase(
            self,
            *,
            user_id: str,
            phase: int,
            new_status: Literal["done", "partial_failure", "blocked"],
            new_status_reason: Optional[str] = None,
            task_uid: Optional[str] = None,
    ) -> Tuple[Optional[Task], Optional[Task]]:
        """Update a task in a given phase and activate the next pending task in that phase."""
        op_id = _operation_id()
        phase_tasks = _get_plan_store().get_tasks(op_id)
        phase_tasks = [t for t in phase_tasks if int(t.phase) == int(phase)]

        # Pick target task: explicit uid, else current active
        target: Optional[Task] = None
        if task_uid:
            for t in phase_tasks:
                if t.task_uid == task_uid:
                    target = t
                    break

        if target is None:
            for t in phase_tasks:
                if t.status == "active":
                    target = t
                    break

        updated: Optional[Task] = None
        if target:
            updated = Task(
                task_uid=target.task_uid,
                title=target.title,
                objective=target.objective,
                evidence=target.evidence,
                phase=target.phase,
                status=new_status,
                status_reason=new_status_reason,
                created_at=target.created_at,
                kind=target.kind,
                reference_id=target.reference_id,
            )
            self.store_task(task=updated, user_id=user_id)

        # After updating, find next pending
        next_active: Optional[Task] = None
        if new_status in ("done", "partial_failure", "blocked"):
            # Check for another active (shouldn't be any)
            still_active = [t for t in phase_tasks if t.status == "active" and t.task_uid != (target.task_uid if target else None)]
            if not still_active:
                pendings = [t for t in phase_tasks if t.status == "pending" and t.task_uid != (target.task_uid if target else None)]
                if pendings:
                    # Sort pendings by created_at (asc) to pick the oldest pending as next
                    pendings.sort(key=lambda x: x.created_at or "")
                    p = pendings[0]
                    next_active = Task(
                        task_uid=p.task_uid,
                        title=p.title,
                        objective=p.objective,
                        evidence=p.evidence,
                        phase=p.phase,
                        status="active",
                        status_reason="activated",
                        created_at=p.created_at,
                        kind=p.kind,
                        reference_id=p.reference_id,
                    )
                    self.store_task(task=next_active, user_id=user_id)

        return updated, next_active

    def get_or_activate_next_task_in_phase(
            self,
            *,
            user_id: Optional[str] = None,
            phase: int,
    ) -> Tuple[Optional[Task], bool]:
        """Return the active task for a phase, or promote the next pending task to active."""
        user_id = _user_id(user_id)
        op_id = _operation_id()
        phase_tasks = _get_plan_store().get_tasks(op_id)
        phase_tasks = [t for t in phase_tasks if int(t.phase) == int(phase)]

        # Prefer existing active
        for t in phase_tasks:
            if t.status == "active":
                return t, False

        # Otherwise promote earliest-created pending
        pendings = [t for t in phase_tasks if t.status == "pending"]
        if not pendings:
            return None, False

        pendings.sort(key=lambda x: x.created_at or "")
        p = pendings[0]

        next_active = Task(
            task_uid=p.task_uid,
            title=p.title,
            objective=p.objective,
            evidence=p.evidence,
            phase=p.phase,
            status="active",
            status_reason="activated",
            created_at=p.created_at,
            kind=p.kind,
            reference_id=p.reference_id,
        )

        self.store_task(task=next_active, user_id=user_id)
        return next_active, True

    def list_tasks(
            self,
            *,
            user_id: Optional[str] = None,
            phase: Optional[int] = None,
            status: Optional[List[str]] = None,
    ) -> List[Task]:
        """List tasks for a phase."""
        tasks = _get_plan_store().get_tasks(_operation_id())
        result = []
        for t in tasks:
            if phase is not None and int(t.phase) != int(phase):
                continue
            if not status or t.status in status:
                result.append(t)
        return result

    def get_memory_overview(self, user_id: Optional[str] = None) -> Dict:
        """Get an overview of stored memories."""
        user_id = _user_id(user_id)
        op_id = _operation_id()

        try:
            # Get all memories for the user from Mem0
            raw_memories = self.list_memories(user_id=user_id)

            # Analyze memories
            total_count = len(raw_memories)
            categories = {}
            recent_findings = []

            for memory in raw_memories:
                metadata = memory.get("metadata", {})
                category = metadata.get("category", "general")
                categories[category] = categories.get(category, 0) + 1

                if category == "finding":
                    recent_findings.append({
                        "content": (
                            memory.get("memory", "")[:100] + "..."
                            if len(memory.get("memory", "")) > 100
                            else memory.get("memory", "")
                        ),
                        "created_at": memory_create_time(memory),
                    })

            # Add Plan and Task counts from SQLite
            plan = _get_plan_store().get_plan(op_id)
            if plan:
                categories["plan"] = categories.get("plan", 0) + 1
                total_count += 1
            
            tasks = _get_plan_store().get_tasks(op_id)
            if tasks:
                categories["task"] = categories.get("task", 0) + len(tasks)
                total_count += len(tasks)

            # Sort recent findings
            recent_findings.sort(key=memory_create_time, reverse=True)

            return {
                "total_count": total_count,
                "categories": categories,
                "recent_findings": recent_findings[:10],
                "has_memories": total_count > 0,
            }

        except Exception as e:
            logger.error("Error getting memory overview: %s", str(e))
            return {
                "total_count": 0,
                "categories": {},
                "recent_findings": [],
                "has_memories": False,
                "error": str(e),
            }


def initialize_memory_system(
    config: Optional[Dict] = None,
    operation_id: Optional[str] = None,
    target_name: Optional[str] = None,
    has_existing_memories: bool = False,
    silent: bool = False,
) -> None:
    """Initialize the memory system with custom configuration.

    Args:
        config: Optional configuration dictionary with embedder, llm, vector_store settings
        operation_id: Unique operation identifier
        target_name: Sanitized target name for organizing memory by target
        has_existing_memories: Whether memories already existed before initialization
        silent: If True, suppress initialization output (used during report generation)
    """
    global _MEMORY_CONFIG, _MEMORY_CLIENT

    # Create enhanced config with operation context
    enhanced_config = config.copy() if config else {}
    enhanced_config["operation_id"] = (
            operation_id or os.environ.get("CYBER_OPERATION_ID", f"OP_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    )
    enhanced_config["target_name"] = target_name or os.environ.get("CYBER_TARGET_NAME", "default_target")
    if enhanced_config["target_name"] == "default_target":
        enhanced_config["user_id"] = f'"cyber-agent-{enhanced_config["operation_id"]}"'
    else:
        enhanced_config["user_id"] = f'"cyber-agent-{enhanced_config["target_name"]}"'

    _MEMORY_CONFIG = enhanced_config
    _MEMORY_CLIENT = Mem0ServiceClient(enhanced_config, has_existing_memories, silent)
    logger.info(
        "Memory system initialized for operation %s, target: %s, user: %s",
        enhanced_config["operation_id"],
        enhanced_config["target_name"],
        enhanced_config["user_id"],
    )


def get_memory_client(silent: bool = False) -> Mem0ServiceClient:
    """Get the current memory client, initializing if needed.

    Args:
        silent: If True, suppress initialization output (used during report generation)

    Returns:
        The memory client instance or None if initialization fails
    """
    global _MEMORY_CLIENT
    if _MEMORY_CLIENT is None:
        initialize_memory_system(silent=silent)
    return _MEMORY_CLIENT


def clear_memory_client() -> None:
    global _MEMORY_CLIENT
    _MEMORY_CLIENT = None
