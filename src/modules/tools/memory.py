#!/usr/bin/env python3
"""
Tool for managing semantic memories with Qdrant and workflow state with SQLite.

This module provides Qdrant-backed semantic memory and SQLite-backed workflow
state with explicit target and operation scoping.

Key Features:
------------
1. Memory Management:
   • store: Add new memories with automatic ID generation and metadata
   • delete: Remove existing memories using memory IDs
   • list: Retrieve all memories for a user or agent
   • get: Retrieve specific memories by memory ID
   • retrieve: Perform semantic search across all memories

2. Task Management:
   • Work is grouped into cohesive tasks with finite acceptance manifests per phase.

3. Safety Features:
   • Content previews before storage
   • Warning messages before deletion

4. Advanced Capabilities:
   • Automatic memory ID generation
   • Structured memory storage with metadata
   • Semantic search with relevance filtering
   • Rich output formatting
   • Support for both user and agent memories
   • Local filesystem or service-hosted Qdrant storage

5. Error Handling:
   • Memory ID validation
   • Parameter validation
   • Graceful API error handling
   • Clear error messages

6. Configurable Components:
   • Embedder (AWS Bedrock, Ollama, OpenAI)
   • LLM (AWS Bedrock, Ollama, OpenAI)
   • Vector Store (Qdrant)
"""

import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Iterable
from functools import wraps
from dataclasses import dataclass, field, replace
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Literal, Optional, Protocol, Tuple
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import litellm
import yaml
from langchain_aws import BedrockEmbeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_ollama import OllamaEmbeddings
from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PositiveInt,
    TypeAdapter,
    ValidationError,
    conlist,
    model_validator,
)
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from strands import tool

from modules.config.system.logger import get_logger
from modules.config.types import get_default_base_dir
from modules.handlers.utils import filter_none_values, sanitize_toon_value
from modules.storage import SQLiteMigrationRunner
from modules.tools.semantic_enum import normalize_semantic_enum
from modules.tools.artifact_references import (
    normalize_artifact_reference_token,
    split_delimited_reference_values,
)

# Set up logging
logger = get_logger("Tools.Memory")


class TaskEvidenceSnapshotVerificationError(ValueError):
    """Raised when a task-owned evidence snapshot cannot be verified."""


@dataclass(frozen=True)
class SecretExposure:
    """A secret-shaped value found in a target-owned evidence artifact.

    The value itself is deliberately excluded from the returned record. The
    artifact remains the sole location that retains the value while the digest
    lets independent validation prove the same exposure without copying it to
    workflow state, telemetry, or reports.
    """

    kind: str
    digest: str


_SECRET_EXPOSURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("connection_string", re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s\"']+", re.I)),
    ("jwt", re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b")),
    ("provider_api_key", re.compile(r"\b(?:AIza[\w-]{20,}|sk-[\w-]{20,}|AKIA[0-9A-Z]{16})\b")),
    ("named_secret", re.compile(
        r"(?im)\b(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)\b\s*[:=]\s*[\"']?([^\s\"',}]{8,})"
    )),
)


def detect_secret_exposures(artifact_ref: str) -> List[SecretExposure]:
    """Return broad, redaction-safe secret matches from one canonical artifact.

    Callers must establish that the artifact is target-owned before using these
    matches to create a finding candidate. This helper intentionally makes no
    protocol or target-scope inference.
    """

    reference = canonical_artifact_reference(artifact_ref)
    try:
        text = Path(_artifact_path_from_ref(reference)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    matches: set[tuple[str, str]] = set()
    for kind, pattern in _SECRET_EXPOSURE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1) if kind == "named_secret" and match.lastindex else match.group(0)
            value = value.strip()
            if len(value) < 8 or re.fullmatch(r"(?:redacted|masked|example|changeme|\*+)", value, re.I):
                continue
            matches.add((kind, hashlib.sha256(value.encode("utf-8")).hexdigest()))
    return [SecretExposure(kind=kind, digest=digest) for kind, digest in sorted(matches)]

# Global configuration and client
_MEMORY_CONFIG: Optional[Dict[str, str]] = None
_MEMORY_CLIENT: Optional["QdrantMemoryClient"] = None
_DATABASE_STORE: Optional["ApplicationStore"] = None
_MEMORY_EVENT_EMITTER: Optional[Callable[[Dict[str, Any]], None]] = None

# Local Qdrant clients share one outputs-backed database within this process.
_QDRANT_WRITE_LOCK = threading.Lock()


PlanStatus = Literal["active", "pending", "done", "partial_failure", "blocked", "not_applicable"]
TaskStatus = Literal["active", "pending", "done", "partial_failure", "blocked", "superseded"]
TargetType = Literal["network", "network_range", "filesystem"]
TargetScope = Literal["all", "subset"]
AcceptanceMode = Literal["outcome", "coverage"]
AcceptanceBasisKind = Literal["procedure", "snapshot"]
ProcedureOutputKind = Literal["artifact", "inventory_manifest"]
DISCOVERY_PROCEDURE_LIMIT_KEYS = (
    "max_duration_minutes",
    "max_requests",
    "max_items",
    "max_depth",
)
EvidenceRequirementKind = Literal[
    "artifact",
    "inventory_manifest",
    "durable_evidence",
    "observation",
    "finding_candidate",
    "verified_finding",
    "memory",
]
AcceptanceResultStatus = Literal[
    "satisfied",
    "assessed_negative",
    "inaccessible",
    "excluded",
    "duplicate",
]
AcceptanceDisposition = Literal[
    "no_vulnerability",
    "observation",
    "finding_candidate",
    "existing_finding",
]


def _normalize_semantic_enum(
    value: Any,
    *,
    aliases: Dict[str, str],
    field_name: str,
) -> Any:
    """Normalize a model-facing enum alias while keeping unknown values invalid."""

    return normalize_semantic_enum(
        value,
        aliases=aliases,
        field_name=field_name,
        logger=logger,
    )


def _normalize_acceptance_status_alias(value: Any) -> Any:
    return _normalize_semantic_enum(value, aliases={
        "complete": "satisfied",
        "completed": "satisfied",
        "success": "satisfied",
        "successful": "satisfied",
        "negative": "assessed_negative",
        "no_finding": "assessed_negative",
        "no_vulnerability": "assessed_negative",
        "not_vulnerable": "assessed_negative",
        "unreachable": "inaccessible",
        "not_accessible": "inaccessible",
        "out_of_scope": "excluded",
        "duplicated": "duplicate",
    }, field_name="acceptance_status")


def _normalize_acceptance_disposition_alias(value: Any) -> Any:
    return _normalize_semantic_enum(value, aliases={
        "assessed_negative": "no_vulnerability",
        "negative": "no_vulnerability",
        "no_finding": "no_vulnerability",
        "no_vuln": "no_vulnerability",
        "not_vulnerable": "no_vulnerability",
        "informational": "observation",
        "finding": "finding_candidate",
        "candidate": "finding_candidate",
        "existing": "existing_finding",
        "duplicate_finding": "existing_finding",
    }, field_name="acceptance_disposition")


def _normalize_finding_validation_outcome(value: Any) -> Any:
    return _normalize_semantic_enum(value, aliases={
        "verified": "confirmed",
        "valid": "confirmed",
        "reproduced": "confirmed",
        "not_verified": "not_confirmed",
        "unverified": "not_confirmed",
        "invalid": "not_confirmed",
        "not_reproduced": "not_confirmed",
    }, field_name="finding_validation_outcome")


def _normalize_objective_validation_outcome(value: Any) -> Any:
    return _normalize_semantic_enum(value, aliases={
        "verified": "confirmed",
        "valid": "confirmed",
        "success": "confirmed",
        "failed": "rejected",
        "invalid": "rejected",
        "not_confirmed": "rejected",
        "uncertain": "inconclusive",
        "pending": "inconclusive",
    }, field_name="objective_validation_outcome")


def _normalize_evidence_strategy(value: Any) -> Any:
    return _normalize_semantic_enum(value, aliases={
        "single": "direct",
        "primary": "direct",
        "comparison": "differential",
        "comparative": "differential",
        "negative_control": "differential",
    }, field_name="evidence_strategy")


def _normalize_task_status(value: Any) -> Any:
    return _normalize_semantic_enum(value, aliases={
        "complete": "done",
        "completed": "done",
        "success": "done",
        "successful": "done",
        "partial": "partial_failure",
        "failed_partial": "partial_failure",
        "failed": "blocked",
        "stalled": "blocked",
        "replaced": "superseded",
        "supersede": "superseded",
        "in_progress": "active",
        "queued": "pending",
    }, field_name="task_status")


def _normalize_plan_status(value: Any) -> Any:
    return _normalize_semantic_enum(value, aliases={
        "complete": "done",
        "completed": "done",
        "success": "done",
        "partial": "partial_failure",
        "failed_partial": "partial_failure",
        "failed": "blocked",
        "stalled": "blocked",
        "in_progress": "active",
        "queued": "pending",
        "not_applicable": "not_applicable",
        "n_a": "not_applicable",
        "skipped": "not_applicable",
    }, field_name="phase_status")


NormalizedAcceptanceResultStatus = Annotated[AcceptanceResultStatus, BeforeValidator(_normalize_acceptance_status_alias)]
NormalizedAcceptanceDisposition = Annotated[
    AcceptanceDisposition,
    BeforeValidator(_normalize_acceptance_disposition_alias),
]
NormalizedFindingValidationOutcome = Annotated[
    Literal["confirmed", "not_confirmed"],
    BeforeValidator(_normalize_finding_validation_outcome),
]
NormalizedEvidenceStrategy = Annotated[
    Literal["direct", "differential"],
    BeforeValidator(_normalize_evidence_strategy),
]
NormalizedTaskStatus = Annotated[TaskStatus, BeforeValidator(_normalize_task_status)]
NormalizedPlanStatus = Annotated[PlanStatus, BeforeValidator(_normalize_plan_status)]
NonEmptyArtifactRefs = conlist(str, min_length=1)
TERMINAL_PLAN_STATUSES = ("done", "partial_failure", "blocked", "not_applicable")
TERMINAL_ACCEPTANCE_STATUSES = (
    "satisfied",
    "assessed_negative",
    "inaccessible",
    "excluded",
    "duplicate",
)

TASK_ACCEPTANCE_MEMORY_MAX_CHARS = 4000
TASK_ACCEPTANCE_MEMORY_SUMMARY_MAX_CHARS = 500
TASK_ACCEPTANCE_MEMORY_MAX_EVIDENCE_REFS = 20


@dataclass(frozen=True)
class _MemoryStoreResult:
    """The durable identity and creation state of one semantic memory."""

    created: bool
    memory_id: str

INVENTORY_MANIFEST_SCHEMA_VERSION = 1
INVENTORY_MANIFEST_ITEM_KINDS = ("endpoint", "parameter", "workflow", "service", "technology")
INVENTORY_MANIFEST_EXAMPLE = {
    "schema_version": INVENTORY_MANIFEST_SCHEMA_VERSION,
    "items": [
        {
            "id": "endpoint-1",
            "target_id": "target-1",
            "kind": "endpoint",
            "value": "https://target.example/login",
            "attributes": {
                "interaction": {
                    "interface": "http",
                    "operations": ["POST"],
                    "inputs": [
                        {"name": "username", "location": "body"},
                        {"name": "password", "location": "body"},
                    ],
                    "success_signals": ["authenticated session"],
                    "failure_signals": ["401 response"],
                    "evidence_refs": ["artifact:artifacts/login-form.html"],
                }
            },
        }
    ],
    "unassessed_gaps": [],
    "extraction": {
        "source_artifact_count": 0,
        "candidate_count": 0,
        "added_count": 0,
    },
}


def inventory_manifest_contract_text() -> str:
    """Return the canonical model-facing contract for version-1 inventory artifacts."""

    kinds = "|".join(INVENTORY_MANIFEST_ITEM_KINDS)
    example = json.dumps(INVENTORY_MANIFEST_EXAMPLE, sort_keys=True)
    return (
        "An inventory_manifest evidence reference is dereferenced and validated as JSON. "
        f"It must use this shape: {example}. schema_version must be {INVENTORY_MANIFEST_SCHEMA_VERSION}; "
        "items must be a non-empty list with unique IDs; every item requires non-empty id, target_id, value, "
        f"kind ({kinds}), and an optional attributes object; unassessed_gaps must be a list. "
        "The extraction summary is controller-owned and may be omitted by the producer. "
        "Put item metadata in attributes, not in a metadata field. Optional attributes.interaction is protocol-neutral "
        "and may contain interface, operations, inputs, success_signals, failure_signals, and evidence_refs. Each input "
        "requires a name and may include location and type. Use HTTP operations and input locations only for HTTP "
        "items; filesystem, code-security, service, workflow, and other modules should use their native operations. "
        "Workflow maps, reports, "
        "and arbitrary JSON outputs are artifact evidence, not inventory_manifest evidence."
    )


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
class EvidenceRequirement:
    """One machine-verifiable evidence requirement for an acceptance criterion."""

    kind: EvidenceRequirementKind
    min_count: int = 1

    def __post_init__(self) -> None:
        if self.kind not in (
            "artifact",
            "inventory_manifest",
            "durable_evidence",
            "observation",
            "finding_candidate",
            "verified_finding",
            "memory",
        ):
            raise ValueError("unsupported acceptance evidence requirement kind")
        if not isinstance(self.min_count, int) or self.min_count <= 0:
            raise ValueError("acceptance evidence requirement min_count must be a positive int")

    @staticmethod
    def from_obj(obj: Any) -> "EvidenceRequirement":
        if isinstance(obj, EvidenceRequirement):
            return obj
        if not isinstance(obj, dict):
            raise ValueError("acceptance evidence requirement must be an object/dict")
        return EvidenceRequirement(kind=str(obj.get("kind", "")), min_count=int(obj.get("min_count", 1)))

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "min_count": self.min_count}


@dataclass(frozen=True)
class ExecutionRequirement:
    """One controller-owned proof that task execution reached a frozen subject."""

    id: str
    description: str
    subject_ref: str

    def __post_init__(self) -> None:
        normalized_id = re.sub(r"\s+", "-", str(self.id or "").strip().lower())
        if not normalized_id:
            raise ValueError("execution requirement id required")
        if not str(self.description or "").strip():
            raise ValueError("execution requirement description required")
        if not str(self.subject_ref or "").strip():
            raise ValueError("execution requirement subject_ref required")
        object.__setattr__(self, "id", normalized_id)
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "subject_ref", str(self.subject_ref).strip())

    @staticmethod
    def from_obj(obj: Any) -> "ExecutionRequirement":
        if isinstance(obj, ExecutionRequirement):
            return obj
        if not isinstance(obj, dict):
            raise ValueError("execution requirement must be an object/dict")
        return ExecutionRequirement(
            id=str(obj.get("id", "")),
            description=str(obj.get("description", "")),
            subject_ref=str(obj.get("subject_ref", "")),
        )

    def to_dict(self) -> Dict[str, str]:
        return {"id": self.id, "description": self.description, "subject_ref": self.subject_ref}


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One immutable, independently reportable task completion criterion."""

    id: str
    description: str
    evidence_requirements: Tuple[EvidenceRequirement, ...]
    execution_requirements: Tuple[ExecutionRequirement, ...] = ()

    def __post_init__(self) -> None:
        normalized_id = re.sub(r"\s+", "-", str(self.id or "").strip().lower())
        if not normalized_id:
            raise ValueError("acceptance criterion id required")
        if not str(self.description or "").strip():
            raise ValueError("acceptance criterion description required")
        moving_scope = re.search(
            r"\b(?:all reachable|all discovered|across the application|key workflows)\b",
            str(self.description),
            re.IGNORECASE,
        )
        if moving_scope:
            raise ValueError("acceptance criterion uses moving scope with words like 'all', 'across', 'key workflows'; reference the finite basis instead")
        requirements = tuple(EvidenceRequirement.from_obj(item) for item in self.evidence_requirements)
        execution_requirements = tuple(
            ExecutionRequirement.from_obj(item) for item in self.execution_requirements
        )
        if not requirements:
            raise ValueError("acceptance criterion evidence_requirements required")
        object.__setattr__(self, "id", normalized_id)
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "evidence_requirements", requirements)
        if len({item.id for item in execution_requirements}) != len(execution_requirements):
            raise ValueError("acceptance criterion execution requirement IDs must be unique")
        object.__setattr__(self, "execution_requirements", execution_requirements)

    @staticmethod
    def from_obj(obj: Any) -> "AcceptanceCriterion":
        if isinstance(obj, AcceptanceCriterion):
            return obj
        if not isinstance(obj, dict):
            raise ValueError("acceptance criterion must be an object/dict")
        return AcceptanceCriterion(
            id=str(obj.get("id", "")),
            description=str(obj.get("description", "")),
            evidence_requirements=obj.get("evidence_requirements", []),
            execution_requirements=obj.get("execution_requirements", []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "evidence_requirements": [requirement.to_dict() for requirement in self.evidence_requirements],
            "execution_requirements": [requirement.to_dict() for requirement in self.execution_requirements],
        }


ACCEPTANCE_SOURCE_REF_PATTERN = re.compile(
    r"^(?:target|plan|task|memory|artifact|finding):\S+$"
)


@dataclass(frozen=True)
class DiscoveryProcedure:
    """Deterministic limits for producing a finite inventory snapshot."""

    methods: Tuple[str, ...]
    limits: Dict[str, int]
    stop_condition: str
    gap_policy: str
    output_kind: ProcedureOutputKind

    def __post_init__(self) -> None:
        methods = _normalize_non_empty_strings(self.methods, "discovery procedure methods")
        if not isinstance(self.limits, dict) or not self.limits:
            raise ValueError("discovery procedure limits required")
        allowed_limits = set(DISCOVERY_PROCEDURE_LIMIT_KEYS)
        invalid = sorted(set(self.limits) - allowed_limits)
        if invalid:
            raise ValueError(f"unsupported discovery procedure limits: {', '.join(invalid)}")
        limits = {}
        for name, value in self.limits.items():
            if not isinstance(value, int) or value <= 0:
                raise ValueError("discovery procedure limits must be positive integers")
            limits[name] = value
        if self.stop_condition != "first_limit_reached":
            raise ValueError("discovery procedure stop_condition must be first_limit_reached")
        if self.gap_policy != "record_unassessed":
            raise ValueError("discovery procedure gap_policy must be record_unassessed")
        if self.output_kind not in ("artifact", "inventory_manifest"):
            raise ValueError("discovery procedure output_kind must be artifact or inventory_manifest")
        object.__setattr__(self, "methods", methods)
        object.__setattr__(self, "limits", limits)

    @staticmethod
    def from_obj(obj: Any) -> "DiscoveryProcedure":
        if isinstance(obj, DiscoveryProcedure):
            return obj
        if not isinstance(obj, dict):
            raise ValueError("acceptance basis procedure must be an object/dict")
        return DiscoveryProcedure(
            methods=obj.get("methods", []),
            limits=obj.get("limits", {}),
            stop_condition=str(obj.get("stop_condition", "")),
            gap_policy=str(obj.get("gap_policy", "")),
            output_kind=str(obj.get("output_kind", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "methods": list(self.methods),
            "limits": self.limits,
            "stop_condition": self.stop_condition,
            "gap_policy": self.gap_policy,
            "output_kind": self.output_kind,
        }


@dataclass(frozen=True)
class AcceptanceBasis:
    """Finite source or bounded procedure used to define task completion scope."""

    kind: AcceptanceBasisKind
    description: str
    source_refs: Tuple[str, ...]
    procedure: Optional[DiscoveryProcedure] = None
    snapshot_hash: str = ""
    item_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        description = str(self.description or "").strip()
        if not description:
            raise ValueError("acceptance basis description required")
        if self.kind not in ("procedure", "snapshot"):
            raise ValueError("acceptance basis kind must be procedure or snapshot")
        source_refs = _normalize_non_empty_strings(self.source_refs, "acceptance basis source_refs")
        invalid_refs = [ref for ref in source_refs if not ACCEPTANCE_SOURCE_REF_PATTERN.fullmatch(ref)]
        if invalid_refs:
            raise ValueError(
                "acceptance basis source_refs must use target:, plan:, task:, memory:, artifact:, or finding: "
                f"references; invalid: {', '.join(invalid_refs)}"
            )
        procedure = DiscoveryProcedure.from_obj(self.procedure) if self.procedure is not None else None
        if self.kind == "procedure":
            if procedure is None:
                raise ValueError("procedure acceptance basis requires procedure limits")
            if any(not ref.startswith(("target:", "plan:")) for ref in source_refs):
                raise ValueError("procedure acceptance basis may reference only target: and plan: sources")
        elif procedure is not None:
            raise ValueError("snapshot acceptance basis must not contain procedure")
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "source_refs", source_refs)
        object.__setattr__(self, "procedure", procedure)
        object.__setattr__(self, "snapshot_hash", str(self.snapshot_hash or "").strip())
        item_ids = tuple(dict.fromkeys(str(item).strip() for item in self.item_ids if str(item).strip()))
        if self.kind != "snapshot" and item_ids:
            raise ValueError("procedure acceptance basis must not contain item_ids")
        object.__setattr__(self, "item_ids", item_ids)

    @staticmethod
    def from_obj(obj: Any) -> "AcceptanceBasis":
        if isinstance(obj, AcceptanceBasis):
            return obj
        if not isinstance(obj, dict):
            raise ValueError("acceptance basis must be an object/dict")
        return AcceptanceBasis(
            kind=str(obj.get("kind", "")),
            description=str(obj.get("description", "")),
            source_refs=obj.get("source_refs", []),
            procedure=obj.get("procedure"),
            snapshot_hash=str(obj.get("snapshot_hash", "")),
            item_ids=obj.get("item_ids", []),
        )

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "kind": self.kind,
            "description": self.description,
            "source_refs": list(self.source_refs),
        }
        if self.procedure is not None:
            result["procedure"] = self.procedure.to_dict()
        if self.snapshot_hash:
            result["snapshot_hash"] = self.snapshot_hash
        if self.item_ids:
            result["item_ids"] = list(self.item_ids)
        return result


@dataclass(frozen=True)
class AcceptanceContract:
    """Frozen task acceptance manifest owned by the workflow controller."""

    mode: AcceptanceMode
    basis: AcceptanceBasis
    criteria: Tuple[AcceptanceCriterion, ...]
    frozen_at: str = ""
    manifest_hash: str = ""

    def __post_init__(self) -> None:
        if self.mode not in ("outcome", "coverage"):
            raise ValueError("acceptance mode must be outcome or coverage")
        basis = AcceptanceBasis.from_obj(self.basis)
        criteria = tuple(AcceptanceCriterion.from_obj(item) for item in self.criteria)
        if not criteria:
            raise ValueError("acceptance criteria required")
        criterion_ids = [criterion.id for criterion in criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("acceptance criterion ids must be unique")
        if self.mode == "coverage" and basis.kind != "snapshot":
            raise ValueError("coverage acceptance mode requires a snapshot basis")
        if basis.kind == "procedure":
            requirement_kinds = {
                requirement.kind
                for criterion in criteria
                for requirement in criterion.evidence_requirements
            }
            if basis.procedure.output_kind == "inventory_manifest" and "inventory_manifest" not in requirement_kinds:
                raise ValueError("inventory_manifest procedure requires inventory_manifest evidence")
            if basis.procedure.output_kind == "artifact":
                if "inventory_manifest" in requirement_kinds:
                    raise ValueError("artifact procedure must not require inventory_manifest evidence")
                if "artifact" not in requirement_kinds:
                    raise ValueError("artifact procedure requires artifact evidence")
        frozen_at = str(self.frozen_at or datetime.now().isoformat()).strip()
        canonical = {
            "mode": self.mode,
            "basis": basis.to_dict(),
            "criteria": [criterion.to_dict() for criterion in criteria],
        }
        manifest_hash = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if self.manifest_hash and self.manifest_hash != manifest_hash:
            raise ValueError("acceptance manifest hash does not match contract")
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "criteria", criteria)
        object.__setattr__(self, "frozen_at", frozen_at)
        object.__setattr__(self, "manifest_hash", manifest_hash)

    @staticmethod
    def from_obj(obj: Any) -> "AcceptanceContract":
        if isinstance(obj, AcceptanceContract):
            return obj
        if not isinstance(obj, dict):
            raise ValueError("acceptance contract must be an object/dict")
        return AcceptanceContract(
            mode=str(obj.get("mode", "")),
            basis=AcceptanceBasis.from_obj(obj.get("basis")),
            criteria=obj.get("criteria", []),
            frozen_at=str(obj.get("frozen_at", "")),
            manifest_hash=str(obj.get("manifest_hash", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "basis": self.basis.to_dict(),
            "criteria": [criterion.to_dict() for criterion in self.criteria],
            "frozen_at": self.frozen_at,
            "manifest_hash": self.manifest_hash,
        }


def _normalize_non_empty_strings(value: Any, field_name: str) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    normalized = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if not text:
            raise ValueError(f"{field_name} must contain non-empty strings")
        if text not in seen:
            normalized.append(text)
            seen.add(text)
    if not normalized:
        raise ValueError(f"{field_name} required")
    return tuple(normalized)


@dataclass(frozen=True)
class CoverageResult:
    """One terminal disposition for one item in a frozen inventory manifest."""

    item_id: str
    status: AcceptanceResultStatus
    evidence_refs: Tuple[str, ...]

    def __post_init__(self) -> None:
        item_id = str(self.item_id or "").strip()
        if not item_id:
            raise ValueError("coverage result item_id required")
        normalized_status = _normalize_acceptance_status_alias(self.status)
        if normalized_status not in TERMINAL_ACCEPTANCE_STATUSES:
            raise ValueError("coverage result status must be terminal")
        evidence_refs = _normalize_non_empty_strings(self.evidence_refs, "coverage result evidence_refs")
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "status", normalized_status)
        object.__setattr__(self, "evidence_refs", evidence_refs)

    @staticmethod
    def from_obj(obj: Any) -> "CoverageResult":
        if isinstance(obj, CoverageResult):
            return obj
        if not isinstance(obj, dict):
            raise ValueError("coverage result must be an object/dict")
        return CoverageResult(
            item_id=str(obj.get("item_id", "")),
            status=str(obj.get("status", "")),
            evidence_refs=obj.get("evidence_refs", []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "status": self.status,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class AcceptanceResult:
    """Executor-recorded result for one criterion in a frozen manifest."""

    criterion_id: str
    status: AcceptanceResultStatus
    disposition: AcceptanceDisposition
    summary: str
    evidence_refs: Tuple[str, ...]
    coverage: Tuple[CoverageResult, ...] = ()

    def __post_init__(self) -> None:
        criterion_id = re.sub(r"\s+", "-", str(self.criterion_id or "").strip().lower())
        if not criterion_id:
            raise ValueError("acceptance result criterion_id required")
        normalized_status = _normalize_acceptance_status_alias(self.status)
        normalized_disposition = _normalize_acceptance_disposition_alias(self.disposition)
        if normalized_status not in TERMINAL_ACCEPTANCE_STATUSES:
            raise ValueError(
                "acceptance result status must be one of: " + "|".join(TERMINAL_ACCEPTANCE_STATUSES)
            )
        if normalized_disposition not in (
            "no_vulnerability",
            "observation",
            "finding_candidate",
            "existing_finding",
        ):
            raise ValueError("acceptance result disposition is invalid")
        summary = str(self.summary or "").strip()
        if not summary:
            raise ValueError("acceptance result summary required")
        evidence_refs = _normalize_non_empty_strings(self.evidence_refs, "acceptance result evidence_refs")
        coverage = tuple(CoverageResult.from_obj(item) for item in self.coverage)
        coverage_ids = [item.item_id for item in coverage]
        if len(coverage_ids) != len(set(coverage_ids)):
            raise ValueError("coverage result item_id values must be unique")
        object.__setattr__(self, "criterion_id", criterion_id)
        object.__setattr__(self, "status", normalized_status)
        object.__setattr__(self, "disposition", normalized_disposition)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "coverage", coverage)

    @staticmethod
    def from_obj(obj: Any) -> "AcceptanceResult":
        if isinstance(obj, AcceptanceResult):
            return obj
        if not isinstance(obj, dict):
            raise ValueError("acceptance result must be an object/dict")
        return AcceptanceResult(
            criterion_id=str(obj.get("criterion_id", "")),
            status=str(obj.get("status", "")),
            disposition=str(obj.get("disposition", "")),
            summary=str(obj.get("summary", "")),
            evidence_refs=obj.get("evidence_refs", []),
            coverage=obj.get("coverage", []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "status": self.status,
            "disposition": self.disposition,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "coverage": [item.to_dict() for item in self.coverage],
        }


@dataclass(frozen=True)
class Task:
    """A single unit of work tied to an execution-prompt phase.

    Stored as a memory item with metadata.category == "task".
    Updates are written as new memories sharing the same task_uid.
    """

    task_uid: str
    title: str
    objective: str
    acceptance: AcceptanceContract
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
    replacement_of: Optional[str] = None
    supersedes_criteria: List[str] = field(default_factory=list)
    recovery_context: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.task_uid, str) or not self.task_uid.strip():
            raise ValueError("task_uid must be a non-empty string")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title must be a non-empty string")
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("objective must be a non-empty string")
        normalized_status = _normalize_task_status(self.status)
        if normalized_status not in {"active", "pending", "done", "partial_failure", "blocked", "superseded"}:
            raise ValueError("task status is invalid")
        object.__setattr__(self, "status", normalized_status)
        object.__setattr__(self, "acceptance", AcceptanceContract.from_obj(self.acceptance))
        if not isinstance(self.recovery_context, dict):
            raise ValueError("recovery_context must be an object")
        object.__setattr__(self, "recovery_context", dict(self.recovery_context))
        if not isinstance(self.phase, int) or self.phase <= 0:
            raise ValueError("phase must be a positive int")
        if self.status not in ("active", "pending", "done", "partial_failure", "blocked", "superseded"):
            raise ValueError("status must be one of: active|pending|done|partial_failure|blocked|superseded")
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
            acceptance=AcceptanceContract.from_obj(obj.get("acceptance")),
            evidence=_normalize_evidence(obj.get("evidence", None)),
            phase=int(obj.get("phase")),
            status=str(obj.get("status", "pending")),
            status_reason=str(obj.get("status_reason", "")),
            created_at=obj.get("created_at"),
            updated_at=obj.get("updated_at"),
            kind=str(obj.get("kind", "standard") or "standard"),
            reference_id=obj.get("reference_id"),
            replacement_of=obj.get("replacement_of"),
            supersedes_criteria=_normalize_target_ids(obj.get("supersedes_criteria", [])),
            recovery_context=dict(obj.get("recovery_context") or {}),
            target_scope=str(obj.get("target_scope", "all") or "all"),
            target_ids=_normalize_target_ids(obj.get("target_ids", [])),
        )

    def to_dict(self) -> Dict[str, Any]:
        return filter_none_values({
            "task_uid": self.task_uid,
            "title": self.title,
            "objective": self.objective,
            "acceptance": self.acceptance.to_dict(),
            "evidence": self.evidence,
            "phase": self.phase,
            "status": self.status,
            "status_reason": self.status_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "kind": self.kind,
            "reference_id": self.reference_id,
            "replacement_of": self.replacement_of,
            "supersedes_criteria": self.supersedes_criteria,
            "recovery_context": self.recovery_context,
            "target_scope": self.target_scope,
            "target_ids": self.target_ids,
        })

    @staticmethod
    def toon_format() -> str:
        return f"task[1]{{{Task.csv_format()}}}"

    @staticmethod
    def csv_format() -> str:
        return (
            "title,objective,acceptance_mode,acceptance_criteria,evidence,phase,status,status_reason,kind,"
            "reference_id,replacement_of,supersedes_criteria,target_scope,target_ids"
        )

    def to_toon(self, include_format=True) -> str:
        title = sanitize_toon_value(self.title)
        objective = sanitize_toon_value(self.objective)
        acceptance_mode = sanitize_toon_value(self.acceptance.mode)
        acceptance_criteria = "|".join(
            sanitize_toon_value(criterion.id) for criterion in self.acceptance.criteria
        )
        evidence = "|".join(sanitize_toon_value(e) for e in self.evidence)
        status = sanitize_toon_value(self.status)
        status_reason = sanitize_toon_value(self.status_reason)
        kind = sanitize_toon_value(self.kind)
        reference_id = sanitize_toon_value(self.reference_id)
        replacement_of = sanitize_toon_value(self.replacement_of)
        supersedes_criteria = "|".join(sanitize_toon_value(item) for item in self.supersedes_criteria)
        target_ids = "|".join(sanitize_toon_value(target_id) for target_id in self.target_ids)
        lines = []
        if include_format:
            lines.append(f"{self.toon_format()}:")
        lines.append(
            f"  {title},{objective},{acceptance_mode},{acceptance_criteria},{evidence},{self.phase},{status},"
            f"{status_reason},{kind},"
            f"{reference_id},{replacement_of},{supersedes_criteria},{self.target_scope},{target_ids}"
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
    requires_finding_candidates: bool = False
    task_creation_mode: Literal[
        "standard",
        "snapshot_dependent",
        "finding_dependent",
        "finding_validation",
    ] = "standard"

    def __post_init__(self) -> None:
        if not isinstance(self.id, int) or self.id < 0:
            raise ValueError("phase.id must be a positive int")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("phase.title must be a non-empty string")
        normalized_status = _normalize_plan_status(self.status)
        if normalized_status not in ("active", "pending", "done", "partial_failure", "blocked", "not_applicable"):
            raise ValueError(
                "phase.status must be one of: active|pending|done|partial_failure|blocked|not_applicable"
            )
        object.__setattr__(self, "status", normalized_status)
        if self.criteria is None:
            object.__setattr__(self, "criteria", "")  # type: ignore[misc]
        if not isinstance(self.criteria, str):
            raise ValueError("phase.criteria must be a string")
        if not isinstance(self.requires_finding_candidates, bool):
            raise ValueError("phase.requires_finding_candidates must be a boolean")
        valid_modes = {"standard", "snapshot_dependent", "finding_dependent", "finding_validation"}
        if self.task_creation_mode not in valid_modes:
            raise ValueError(f"phase.task_creation_mode must be one of: {', '.join(sorted(valid_modes))}")

    @staticmethod
    def from_obj(obj: Any) -> "PlanPhase":
        if not isinstance(obj, dict):
            raise ValueError("phase must be an object/dict")
        return PlanPhase(
            id=int(obj.get("id")),
            title=str(obj.get("title", "")),
            status=str(obj.get("status", "pending")),  # validated in __post_init__
            criteria=str(obj.get("criteria", "")) if obj.get("criteria") is not None else "",
            requires_finding_candidates=obj.get("requires_finding_candidates", False),
            task_creation_mode=str(
                obj.get("task_creation_mode")
                or ("finding_dependent" if obj.get("requires_finding_candidates", False) else "standard")
            ),
        )

    @staticmethod
    def toon_format() -> str:
        return f"plan_phases[1]{{{PlanPhase.csv_format()}}}"

    @staticmethod
    def csv_format() -> str:
        return "id,title,status,criteria,requires_finding_candidates,task_creation_mode"

    def to_toon(self, include_format=True) -> str:
        title = sanitize_toon_value(self.title)
        status = sanitize_toon_value(self.status)
        criteria = sanitize_toon_value(self.criteria)
        lines = []
        if include_format:
            lines.append(f"{self.toon_format()}:")
        lines.append(
            f"  {self.id},{title},{status},{criteria},{str(self.requires_finding_candidates).lower()},"
            f"{self.task_creation_mode}"
        )
        return "\n".join(lines).strip()

    def to_dict(self) -> Dict[str, Any]:
        return filter_none_values({
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "criteria": self.criteria,
            "requires_finding_candidates": self.requires_finding_candidates,
            "task_creation_mode": self.task_creation_mode,
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
    """Return the operation-independent local Qdrant database directory."""
    output_dir = os.environ.get("CYBER_AGENT_OUTPUT_DIR") or (config or {}).get(
        "output_dir", get_default_base_dir()
    )
    return os.path.join(output_dir, "qdrant")


def _normalize_model_metric_rows(rows: Any) -> List[Dict[str, Any]]:
    """Validate report-compatible per-model metrics before durable append."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("model metric capture requires at least one model row")

    normalized_rows: List[Dict[str, Any]] = []
    seen_models: set[tuple[str, str]] = set()
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            raise ValueError("model metric rows must be objects")
        provider = str(raw_row.get("provider") or "").strip()
        model = str(raw_row.get("model") or "").strip()
        if not provider or not model:
            raise ValueError("model metric rows require provider and model")
        identity = (provider, model)
        if identity in seen_models:
            raise ValueError("model metric capture contains duplicate provider/model rows")
        seen_models.add(identity)

        def nonnegative_int(field: str, *, optional: bool = False) -> Optional[int]:
            value = raw_row.get(field)
            if value is None and optional:
                return None
            if isinstance(value, bool):
                raise ValueError(f"model metric {field} must be a non-negative integer")
            try:
                converted = int(value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"model metric {field} must be a non-negative integer") from error
            if converted < 0:
                raise ValueError(f"model metric {field} must be a non-negative integer")
            return converted

        def nonnegative_float(field: str) -> float:
            value = raw_row.get(field)
            if isinstance(value, bool):
                raise ValueError(f"model metric {field} must be a non-negative number")
            try:
                converted = float(value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"model metric {field} must be a non-negative number") from error
            if converted < 0:
                raise ValueError(f"model metric {field} must be a non-negative number")
            return converted

        input_tokens = nonnegative_int("input_tokens")
        output_tokens = nonnegative_int("output_tokens")
        total_tokens = nonnegative_int("total_tokens")
        if total_tokens != input_tokens + output_tokens:
            raise ValueError("model metric total_tokens must equal input_tokens plus output_tokens")
        raw_categories = raw_row.get("correction_categories", {})
        if raw_categories is None:
            raw_categories = {}
        if not isinstance(raw_categories, dict):
            raise ValueError("model metric correction_categories must be an object")
        correction_categories = {}
        for category, count in raw_categories.items():
            normalized_category = str(category).strip()
            if not normalized_category:
                raise ValueError("model metric correction_categories keys must be non-empty strings")
            if isinstance(count, bool):
                raise ValueError("model metric correction_categories values must be non-negative integers")
            try:
                normalized_count = int(count)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "model metric correction_categories values must be non-negative integers"
                ) from error
            if normalized_count < 0:
                raise ValueError("model metric correction_categories values must be non-negative integers")
            correction_categories[normalized_category] = normalized_count
        if sum(correction_categories.values()) > nonnegative_int("correction_loops"):
            raise ValueError("model metric correction_categories cannot exceed correction_loops")
        normalized_rows.append(
            {
                "provider": provider,
                "model": model,
                "context_window_tokens": nonnegative_int("context_window_tokens", optional=True),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": nonnegative_int("cache_read_tokens"),
                "cache_write_tokens": nonnegative_int("cache_write_tokens"),
                "total_tokens": total_tokens,
                "cost": nonnegative_float("cost"),
                "inference_time_ms": nonnegative_float("inference_time_ms"),
                "model_calls": nonnegative_int("model_calls"),
                "correction_loops": nonnegative_int("correction_loops"),
                "correction_categories": dict(sorted(correction_categories.items())),
                "efficiency": nonnegative_float("efficiency"),
            }
        )
    return normalized_rows


class ApplicationStore(Protocol):
    """Backend-neutral application-state contract used by workflow callers."""

    db_path: str
    logical_target: str
    read_only: bool

    def ensure_operation(self, operation_id: str) -> None: ...

    def has_operation(self, operation_id: str) -> bool: ...

    def store_plan(self, operation_id: str, plan: OperationPlan) -> None: ...

    def patch_plan(
        self,
        operation_id: str,
        *,
        phase_status_updates: Optional[Dict[int, str]] = None,
        current_phase: Optional[int] = None,
        assessment_complete: Optional[bool] = None,
    ) -> OperationPlan: ...

    def get_plan(self, operation_id: str) -> Optional[OperationPlan]: ...

    def store_task(self, operation_id: str, task: Task) -> None: ...

    def patch_task(
        self,
        operation_id: str,
        task_uid: str,
        *,
        status: Optional[str] = None,
        status_reason: Optional[str] = None,
        phase: Optional[int] = None,
        evidence_additions: Iterable[str] = (),
        evidence_replacement: Optional[Iterable[str]] = None,
        recovery_context_updates: Optional[Dict[str, Any]] = None,
        recovery_context_removals: Iterable[str] = (),
    ) -> Task: ...

    def get_tasks(self, operation_id: str) -> List[Task]: ...

    def append_operation_model_metrics(
        self, operation_id: str, captured_at: str, rows: List[Dict[str, Any]]
    ) -> None: ...

    def list_operation_model_metrics(self, operation_id: str) -> List[Dict[str, Any]]: ...

    def store_finding_evidence_receipt(
        self,
        operation_id: str,
        receipt_uid: str,
        source_task_uid: str,
        artifact_ref: str,
        marker: str,
        artifact_fingerprint: str,
    ) -> None: ...

    def get_finding_evidence_receipts(
        self, operation_id: str, receipt_uids: List[str]
    ) -> List[Dict[str, str]]: ...

    def rebind_finding_verification_task(
        self,
        operation_id: str,
        finding_uid: str,
        expected_task_uid: str,
        replacement_task_uid: str,
    ) -> bool: ...


class SQLiteApplicationStore:
    """SQLite persistence for application workflow state.

    Every operation-owned query is scoped by the exact logical target supplied
    by the user and by operation ID. The database itself is shared by all
    targets and operations.
    """

    _RUNTIME_RECOVERABLE_METHODS = frozenset({
        "ensure_operation",
        "has_operation",
        "append_operation_model_metrics",
        "list_operation_model_metrics",
        "store_plan",
        "get_plan",
        "store_task",
        "patch_task",
        "get_tasks",
        "store_acceptance_results",
        "get_acceptance_results",
        "has_acceptance_memory_publication",
        "mark_acceptance_memory_published",
        "store_preflight_results",
        "list_preflight_results",
        "get_finding_by_fingerprint",
        "get_finding",
        "list_findings",
        "store_finding_candidate",
        "store_finding_evidence_receipt",
        "get_finding_evidence_receipts",
        "link_finding_source_task",
        "rebind_finding_verification_task",
        "store_finding_validation",
        "update_finding_taxonomy_annotation",
        "update_finding_attack_enrichment",
        "resolve_finding",
        "get_objective_candidate",
        "get_objective_candidate_by_fingerprint",
        "list_objective_candidates",
        "store_objective_candidate",
        "store_objective_validation",
    })

    def __getattribute__(self, name: str) -> Any:
        """Wrap public persistence calls so a live database error gets one safe retry."""

        value = object.__getattribute__(self, name)
        recoverable = object.__getattribute__(self, "_RUNTIME_RECOVERABLE_METHODS")
        if name not in recoverable or not callable(value):
            return value

        @wraps(value)
        def recovered_call(*args: Any, **kwargs: Any) -> Any:
            retry = object.__getattribute__(self, "_call_with_runtime_recovery")
            return retry(lambda: value(*args, **kwargs))

        return recovered_call

    def __init__(self, db_path: str, logical_target: str, read_only: bool = False):
        self.db_path = db_path
        self.logical_target = logical_target
        self.read_only = read_only
        self._lock = threading.RLock()
        self._runtime_recovery_in_progress = False
        if self.read_only:
            if not os.path.isfile(self.db_path):
                raise FileNotFoundError(f"Application database does not exist: {self.db_path}")
        else:
            self._initialize_writable_database()

    def _recover_runtime_database(self, error: Exception) -> bool:
        """Attempt one fail-closed repair after a live SQLite failure."""

        if self.read_only or self._runtime_recovery_in_progress:
            return False
        self._runtime_recovery_in_progress = True
        try:
            backup = self._backup_database_for_recovery()
            recovered = self._recover_database()
            integral = recovered and self._sqlite_integrity_check(self.db_path).lower() == "ok"
            logger.warning(
                "SQLite runtime recovery database=%s backup=%s recovered=%s error=%s",
                Path(self.db_path).name,
                backup.name,
                integral,
                type(error).__name__,
            )
            return integral
        except (OSError, sqlite3.DatabaseError) as recovery_error:
            logger.error(
                "SQLite runtime recovery failed database=%s original=%s recovery=%s",
                Path(self.db_path).name,
                type(error).__name__,
                type(recovery_error).__name__,
            )
            return False
        finally:
            self._runtime_recovery_in_progress = False

    def _call_with_runtime_recovery(self, operation: Callable[[], Any]) -> Any:
        """Retry one store operation after a successful runtime recovery."""

        try:
            return operation()
        except (sqlite3.DatabaseError, OSError) as error:
            if not self._recover_runtime_database(error):
                raise
            return operation()

    @staticmethod
    def _sqlite_integrity_check(db_path: str) -> str:
        """Return SQLite's integrity result, raising when the database cannot be inspected."""

        with sqlite3.connect(db_path) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0] if row else "missing integrity result").strip()

    @staticmethod
    def _recovery_backup_path(db_path: str) -> Path:
        source = Path(db_path)
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        return source.with_name(f"{source.stem}.corrupt-{timestamp}{source.suffix}")

    def _backup_database_for_recovery(self) -> Path:
        """Preserve the database and sidecars before an automatic recovery attempt."""

        source = Path(self.db_path)
        backup = self._recovery_backup_path(self.db_path)
        if source.exists():
            shutil.copy2(source, backup)
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(f"{source}{suffix}")
            if sidecar.exists():
                shutil.copy2(sidecar, Path(f"{backup}{suffix}"))
        return backup

    def _recover_database(self) -> bool:
        """Recover the current database through sqlite3 .recover and atomically validate the result."""

        source = Path(self.db_path)
        with tempfile.NamedTemporaryFile(
            prefix=f"{source.stem}.recovered-", suffix=source.suffix, dir=source.parent, delete=False
        ) as temporary:
            recovered_path = Path(temporary.name)
        try:
            recovered_sql = subprocess.run(
                ["sqlite3", str(source), ".recover"],
                check=False,
                capture_output=True,
                text=True,
            )
            if recovered_sql.returncode != 0 or not recovered_sql.stdout.strip():
                return False
            restored = subprocess.run(
                ["sqlite3", str(recovered_path)],
                input=recovered_sql.stdout,
                check=False,
                capture_output=True,
                text=True,
            )
            if restored.returncode != 0:
                return False
            SQLiteMigrationRunner(str(recovered_path)).migrate()
            with sqlite3.connect(recovered_path) as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if self._sqlite_integrity_check(str(recovered_path)).lower() != "ok":
                return False
            for suffix in ("-wal", "-shm", "-journal"):
                Path(f"{source}{suffix}").unlink(missing_ok=True)
            os.replace(recovered_path, source)
            return True
        finally:
            recovered_path.unlink(missing_ok=True)

    def _replace_with_fresh_database(self) -> None:
        """Create and atomically install a fresh migrated database after failed recovery."""

        source = Path(self.db_path)
        with tempfile.NamedTemporaryFile(
            prefix=f"{source.stem}.fresh-", suffix=source.suffix, dir=source.parent, delete=False
        ) as temporary:
            fresh_path = Path(temporary.name)
        try:
            SQLiteMigrationRunner(str(fresh_path)).migrate()
            with sqlite3.connect(fresh_path) as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if self._sqlite_integrity_check(str(fresh_path)).lower() != "ok":
                raise RuntimeError("fresh SQLite database failed integrity check")
            for suffix in ("-wal", "-shm", "-journal"):
                Path(f"{source}{suffix}").unlink(missing_ok=True)
            os.replace(fresh_path, source)
        finally:
            fresh_path.unlink(missing_ok=True)

    def _initialize_writable_database(self) -> None:
        """Migrate, validate, and recover the writable store before an operation can use it."""

        database_exists = Path(self.db_path).exists()
        try:
            SQLiteMigrationRunner(self.db_path).migrate()
            integrity = self._sqlite_integrity_check(self.db_path)
            if integrity.lower() == "ok":
                return
            raise sqlite3.DatabaseError(f"integrity_check={integrity}")
        except (OSError, sqlite3.DatabaseError) as error:
            if not database_exists:
                raise
            backup = self._backup_database_for_recovery()
            recovered = self._recover_database()
            if not recovered:
                self._replace_with_fresh_database()
            logger.warning(
                "SQLite initialization recovery database=%s backup=%s recovered=%s error=%s",
                Path(self.db_path).name,
                backup.name,
                recovered,
                type(error).__name__,
            )
            if self._sqlite_integrity_check(self.db_path).lower() != "ok":
                raise RuntimeError("SQLite initialization recovery did not produce an integral database")

    def _connect(self) -> sqlite3.Connection:
        """Open the application database, enforcing configured access."""
        if self.read_only:
            conn = sqlite3.connect(f"{Path(self.db_path).resolve().as_uri()}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def ensure_operation(self, operation_id: str) -> None:
        """Register an operation in this logical-target scope."""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO operations(logical_target, operation_id, created_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(logical_target, operation_id) DO NOTHING",
                    (self.logical_target, operation_id, datetime.now().isoformat()),
                )

    def has_operation(self, operation_id: str) -> bool:
        """Return whether this exact target and operation are registered."""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM operations WHERE logical_target = ? AND operation_id = ?",
                    (self.logical_target, operation_id),
                ).fetchone()
        return row is not None

    def _register_operation(self, conn: sqlite3.Connection, operation_id: str) -> None:
        conn.execute(
            "INSERT INTO operations(logical_target, operation_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(logical_target, operation_id) DO NOTHING",
            (self.logical_target, operation_id, datetime.now().isoformat()),
        )

    def append_operation_model_metrics(
        self,
        operation_id: str,
        captured_at: str,
        rows: List[Dict[str, Any]],
    ) -> None:
        """Append one immutable, per-model metrics capture for an operation."""
        normalized_rows = _normalize_model_metric_rows(rows)
        normalized_captured_at = str(captured_at).strip()
        if not normalized_captured_at:
            raise ValueError("model metric capture timestamp is required")
        with self._lock:
            with self._connect() as conn:
                self._register_operation(conn, operation_id)
                conn.executemany(
                    """
                    INSERT INTO operation_model_metrics (
                        logical_target, operation_id, captured_at, provider, model, context_window_tokens,
                        input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, total_tokens, cost,
                        inference_time_ms, model_calls, correction_loops, correction_categories, efficiency
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            self.logical_target,
                            operation_id,
                            normalized_captured_at,
                            row["provider"],
                            row["model"],
                            row["context_window_tokens"],
                            row["input_tokens"],
                            row["output_tokens"],
                            row["cache_read_tokens"],
                            row["cache_write_tokens"],
                            row["total_tokens"],
                            row["cost"],
                            row["inference_time_ms"],
                            row["model_calls"],
                            row["correction_loops"],
                            json.dumps(row["correction_categories"], sort_keys=True),
                            row["efficiency"],
                        )
                        for row in normalized_rows
                    ],
                )

    def list_operation_model_metrics(self, operation_id: str) -> List[Dict[str, Any]]:
        """Return every persisted model-metrics capture in report display order."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT captured_at, provider, model, context_window_tokens, input_tokens, output_tokens,
                           cache_read_tokens, cache_write_tokens, total_tokens, cost, inference_time_ms,
                           model_calls, correction_loops, correction_categories, efficiency
                    FROM operation_model_metrics
                    WHERE logical_target = ? AND operation_id = ?
                    ORDER BY captured_at, provider, model
                    """,
                    (self.logical_target, operation_id),
                ).fetchall()
        return [
            {
                "captured_at": row[0],
                "provider": row[1],
                "model": row[2],
                "context_window_tokens": row[3],
                "input_tokens": int(row[4]),
                "output_tokens": int(row[5]),
                "cache_read_tokens": int(row[6]),
                "cache_write_tokens": int(row[7]),
                "total_tokens": int(row[8]),
                "cost": float(row[9]),
                "inference_time_ms": float(row[10]),
                "model_calls": int(row[11]),
                "correction_loops": int(row[12]),
                "correction_categories": json.loads(row[13]) if row[13] else {},
                "efficiency": float(row[14]),
            }
            for row in rows
        ]

    def store_plan(self, operation_id: str, plan: OperationPlan):
        """Store or update a plan."""
        plan_dict = plan.to_dict()
        now = datetime.now().isoformat()
        if not plan.created_at:
            plan_dict["created_at"] = now
        plan_dict["updated_at"] = now

        with self._lock:
            with self._connect() as conn:
                self._register_operation(conn, operation_id)
                conn.execute("""
                    INSERT INTO plans (
                        logical_target, operation_id, objective, current_phase, total_phases,
                        assessment_complete, plan_data, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(logical_target, operation_id) DO UPDATE SET
                        objective=excluded.objective,
                        current_phase=excluded.current_phase,
                        total_phases=excluded.total_phases,
                        assessment_complete=excluded.assessment_complete,
                        plan_data=excluded.plan_data,
                        updated_at=excluded.updated_at
                """, (
                    self.logical_target,
                    operation_id,
                    plan.objective,
                    plan.current_phase,
                    plan.total_phases,
                    plan.assessment_complete,
                    json.dumps(plan_dict),
                    plan_dict["created_at"],
                    plan_dict["updated_at"]
                ))

    def patch_plan(
        self,
        operation_id: str,
        *,
        phase_status_updates: Optional[Dict[int, str]] = None,
        current_phase: Optional[int] = None,
        assessment_complete: Optional[bool] = None,
    ) -> OperationPlan:
        """Atomically update controller-owned plan progress without replacing plan content."""

        updates = {int(phase_id): str(status) for phase_id, status in (phase_status_updates or {}).items()}
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT plan_data FROM plans WHERE logical_target = ? AND operation_id = ?",
                    (self.logical_target, operation_id),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Unknown operation plan: {operation_id}")
                plan = OperationPlan.from_obj(json.loads(row[0]))
                known_phase_ids = {phase.id for phase in plan.phases}
                unknown_phase_ids = sorted(set(updates) - known_phase_ids)
                if unknown_phase_ids:
                    raise ValueError(f"Unknown plan phase IDs: {unknown_phase_ids}")
                phases = [
                    replace(phase, status=updates.get(phase.id, phase.status))
                    for phase in plan.phases
                ]
                patched = replace(
                    plan,
                    phases=phases,
                    current_phase=current_phase if current_phase is not None else plan.current_phase,
                    assessment_complete=(
                        assessment_complete if assessment_complete is not None else plan.assessment_complete
                    ),
                    updated_at=datetime.now().isoformat(),
                )
                conn.execute(
                    "UPDATE plans SET current_phase = ?, assessment_complete = ?, plan_data = ?, updated_at = ? "
                    "WHERE logical_target = ? AND operation_id = ?",
                    (
                        patched.current_phase,
                        patched.assessment_complete,
                        json.dumps(patched.to_dict()),
                        patched.updated_at,
                        self.logical_target,
                        operation_id,
                    ),
                )
        return patched

    def get_plan(self, operation_id: str) -> Optional[OperationPlan]:
        """Retrieve a plan by operation_id."""
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT plan_data FROM plans WHERE logical_target = ? AND operation_id = ?",
                    (self.logical_target, operation_id),
                )
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
            with self._connect() as conn:
                self._register_operation(conn, operation_id)
                existing = conn.execute(
                    "SELECT acceptance_contract FROM tasks "
                    "WHERE logical_target = ? AND operation_id = ? AND task_uid = ?",
                    (self.logical_target, operation_id, task.task_uid),
                ).fetchone()
                if existing:
                    existing_contract = AcceptanceContract.from_obj(json.loads(existing[0]))
                    contract_changed = existing_contract.manifest_hash != task.acceptance.manifest_hash
                    if contract_changed:
                        raise ValueError("acceptance contract is immutable after task creation")
                conn.execute("""
                    INSERT INTO tasks (
                        logical_target, task_uid, operation_id, title, objective, acceptance_contract, phase,
                        status, status_reason, evidence,
                        created_at, updated_at, kind, reference_id, replacement_of, supersedes_criteria, recovery_context,
                        target_scope, target_ids
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(logical_target, operation_id, task_uid) DO UPDATE SET
                        title=excluded.title,
                        objective=excluded.objective,
                        acceptance_contract=excluded.acceptance_contract,
                        phase=excluded.phase,
                        status=excluded.status,
                        status_reason=excluded.status_reason,
                        evidence=excluded.evidence,
                        kind=excluded.kind,
                        reference_id=excluded.reference_id,
                        replacement_of=excluded.replacement_of,
                        supersedes_criteria=excluded.supersedes_criteria,
                        recovery_context=excluded.recovery_context,
                        target_scope=excluded.target_scope,
                        target_ids=excluded.target_ids,
                        updated_at=excluded.updated_at
                """, (
                    self.logical_target,
                    task.task_uid,
                    operation_id,
                    task.title,
                    task.objective,
                    json.dumps(task.acceptance.to_dict()),
                    task.phase,
                    task.status,
                    task.status_reason,
                    json.dumps(task.evidence),
                    task_dict["created_at"],
                    task_dict["updated_at"],
                    task.kind,
                    task.reference_id,
                    task.replacement_of,
                    json.dumps(task.supersedes_criteria),
                    json.dumps(task.recovery_context, sort_keys=True),
                    task.target_scope,
                    json.dumps(task.target_ids),
                ))

    def patch_task(
        self,
        operation_id: str,
        task_uid: str,
        *,
        status: Optional[str] = None,
        status_reason: Optional[str] = None,
        phase: Optional[int] = None,
        evidence_additions: Iterable[str] = (),
        evidence_replacement: Optional[Iterable[str]] = None,
        recovery_context_updates: Optional[Dict[str, Any]] = None,
        recovery_context_removals: Iterable[str] = (),
    ) -> Task:
        """Atomically update mutable task fields without replacing unrelated task state."""

        additions = [str(item) for item in evidence_additions if str(item)]
        updates = dict(recovery_context_updates or {})
        removals = {str(item) for item in recovery_context_removals if str(item)}
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._register_operation(conn, operation_id)
                row = conn.execute(
                    "SELECT title, objective, acceptance_contract, phase, status, status_reason, evidence, "
                    "created_at, kind, reference_id, replacement_of, supersedes_criteria, recovery_context, "
                    "target_scope, target_ids FROM tasks "
                    "WHERE logical_target = ? AND operation_id = ? AND task_uid = ?",
                    (self.logical_target, operation_id, task_uid),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Unknown task_uid for operation: {task_uid}")

                evidence = (
                    list(dict.fromkeys(str(item) for item in evidence_replacement if str(item)))
                    if evidence_replacement is not None
                    else list(json.loads(row[6] or "[]"))
                )
                if evidence_replacement is None:
                    for reference in additions:
                        if reference not in evidence:
                            evidence.append(reference)
                recovery_context = dict(json.loads(row[12] or "{}"))
                for key in removals:
                    recovery_context.pop(key, None)
                recovery_context.update(updates)
                now = datetime.now().isoformat()
                next_phase = int(phase) if phase is not None else int(row[3])
                next_status = str(status) if status is not None else str(row[4])
                next_reason = str(status_reason) if status_reason is not None else str(row[5] or "")
                conn.execute(
                    "UPDATE tasks SET phase = ?, status = ?, status_reason = ?, evidence = ?, "
                    "recovery_context = ?, updated_at = ? "
                    "WHERE logical_target = ? AND operation_id = ? AND task_uid = ?",
                    (
                        next_phase,
                        next_status,
                        next_reason,
                        json.dumps(evidence),
                        json.dumps(recovery_context, sort_keys=True),
                        now,
                        self.logical_target,
                        operation_id,
                        task_uid,
                    ),
                )
        return Task(
            title=row[0],
            objective=row[1],
            acceptance=AcceptanceContract.from_obj(json.loads(row[2])),
            phase=next_phase,
            status=next_status,
            status_reason=next_reason,
            evidence=evidence,
            task_uid=task_uid,
            created_at=row[7],
            updated_at=now,
            kind=row[8] or "standard",
            reference_id=row[9],
            replacement_of=row[10],
            supersedes_criteria=json.loads(row[11] or "[]"),
            recovery_context=recovery_context,
            target_scope=row[13] or "all",
            target_ids=json.loads(row[14] or "[]"),
        )

    def get_tasks(self, operation_id: str) -> List[Task]:
        """Retrieve all tasks for an operation."""
        tasks = []
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT title, objective, acceptance_contract, phase, status, status_reason, evidence, task_uid, "
                    "created_at, updated_at, kind, reference_id, replacement_of, supersedes_criteria, recovery_context, "
                    "target_scope, target_ids "
                    "FROM tasks WHERE logical_target = ? AND operation_id = ?",
                    (self.logical_target, operation_id),
                )
                for row in cursor:
                    tasks.append(
                        Task(
                            title=row[0],
                            objective=row[1],
                            acceptance=AcceptanceContract.from_obj(json.loads(row[2])),
                            phase=row[3],
                            status=row[4],
                            status_reason=row[5],
                            evidence=json.loads(row[6]),
                            task_uid=row[7],
                            created_at=row[8],
                            updated_at=row[9],
                            kind=row[10] or "standard",
                            reference_id=row[11],
                            replacement_of=row[12],
                            supersedes_criteria=json.loads(row[13] or "[]"),
                            recovery_context=json.loads(row[14] or "{}"),
                            target_scope=row[15] or "all",
                            target_ids=json.loads(row[16] or "[]"),
                        )
                    )
        return tasks

    def store_acceptance_results(
        self,
        operation_id: str,
        task_uid: str,
        results: List[AcceptanceResult],
    ) -> None:
        """Atomically upsert executor results for a frozen task manifest."""

        now = datetime.now().isoformat()
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._register_operation(conn, operation_id)
                conn.executemany(
                    """
                    INSERT INTO task_acceptance_results (
                        logical_target, operation_id, task_uid, criterion_id, status, disposition, summary,
                        evidence_refs, coverage, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(logical_target, operation_id, task_uid, criterion_id) DO NOTHING
                    """,
                    [
                        (
                            self.logical_target,
                            operation_id,
                            task_uid,
                            result.criterion_id,
                            result.status,
                            result.disposition,
                            result.summary,
                            json.dumps(result.evidence_refs),
                            json.dumps([item.to_dict() for item in result.coverage]),
                            now,
                        )
                        for result in results
                    ],
                )

    def get_acceptance_results(self, operation_id: str, task_uid: str) -> List[AcceptanceResult]:
        """Return the current acceptance ledger for one task."""

        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT criterion_id, status, disposition, summary, evidence_refs, coverage "
                    "FROM task_acceptance_results WHERE logical_target = ? "
                    "AND operation_id = ? AND task_uid = ? "
                    "ORDER BY criterion_id",
                    (self.logical_target, operation_id, task_uid),
                ).fetchall()
        return [
            AcceptanceResult(
                criterion_id=row[0],
                status=row[1],
                disposition=row[2],
                summary=row[3],
                evidence_refs=json.loads(row[4]),
                coverage=json.loads(row[5]),
            )
            for row in rows
        ]

    def has_acceptance_memory_publication(
        self,
        operation_id: str,
        task_uid: str,
        publication_key: str,
    ) -> bool:
        """Return whether this immutable acceptance ledger was published to operation memory."""

        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT publication_key FROM task_acceptance_memory_publications "
                    "WHERE logical_target = ? AND operation_id = ? AND task_uid = ?",
                    (self.logical_target, operation_id, task_uid),
                ).fetchone()
        return row is not None and row[0] == publication_key

    def mark_acceptance_memory_published(
        self,
        operation_id: str,
        task_uid: str,
        publication_key: str,
    ) -> None:
        """Record successful publication for replay-safe acceptance handling."""

        with self._lock:
            with self._connect() as conn:
                self._register_operation(conn, operation_id)
                conn.execute(
                    """
                    INSERT INTO task_acceptance_memory_publications (
                        logical_target, operation_id, task_uid, publication_key, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(logical_target, operation_id, task_uid) DO UPDATE SET
                        publication_key=excluded.publication_key,
                        updated_at=excluded.updated_at
                    """,
                    (self.logical_target, operation_id, task_uid, publication_key, datetime.now().isoformat()),
                )

    def store_preflight_results(self, operation_id: str, results: List[Dict[str, Any]]) -> None:
        """Persist immutable preflight facts for one operation's executable targets."""

        now = datetime.now().isoformat()
        with self._lock:
            with self._connect() as conn:
                self._register_operation(conn, operation_id)
                for result in results:
                    target_id = str(result.get("target_id") or "").strip()
                    if not target_id:
                        raise ValueError("preflight result requires target_id")
                    conn.execute(
                        """
                        INSERT INTO operation_preflight_results (
                            logical_target, operation_id, target_id, target, target_type, status, checks, reason,
                            resolved_addresses, has_global_address, has_private_or_reserved_address,
                            route_reachable, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(logical_target, operation_id, target_id) DO NOTHING
                        """,
                        (
                            self.logical_target,
                            operation_id,
                            target_id,
                            str(result.get("target") or ""),
                            str(result.get("target_type") or ""),
                            str(result.get("status") or "skip"),
                            json.dumps(list(result.get("checks") or [])),
                            str(result.get("reason") or ""),
                            json.dumps(list(result.get("resolved_addresses") or [])),
                            bool(result.get("has_global_address")),
                            bool(result.get("has_private_or_reserved_address")),
                            bool(result.get("route_reachable")),
                            now,
                        ),
                    )

    def list_preflight_results(self, operation_id: str) -> List[Dict[str, Any]]:
        """Return the original persisted preflight facts for an operation."""

        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT target_id, target, target_type, status, checks, reason, resolved_addresses,
                           has_global_address, has_private_or_reserved_address, route_reachable, recorded_at
                    FROM operation_preflight_results
                    WHERE logical_target = ? AND operation_id = ? ORDER BY target_id
                    """,
                    (self.logical_target, operation_id),
                ).fetchall()
        return [
            {
                "target_id": row[0],
                "target": row[1],
                "target_type": row[2],
                "status": row[3],
                "checks": json.loads(row[4]),
                "reason": row[5],
                "resolved_addresses": json.loads(row[6]),
                "has_global_address": bool(row[7]),
                "has_private_or_reserved_address": bool(row[8]),
                "route_reachable": bool(row[9]),
                "recorded_at": row[10],
            }
            for row in rows
        ]

    def get_finding_by_fingerprint(self, operation_id: str, fingerprint: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT finding_uid, candidate_data, verification_task_uid, validation_data, resolution "
                    "FROM finding_records WHERE logical_target = ? AND operation_id = ? AND fingerprint = ?",
                    (self.logical_target, operation_id, fingerprint),
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
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT fingerprint, candidate_data, verification_task_uid, validation_data, resolution "
                    "FROM finding_records WHERE logical_target = ? AND operation_id = ? AND finding_uid = ?",
                    (self.logical_target, operation_id, finding_uid),
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

    def list_findings(self, operation_id: str) -> List[Dict[str, Any]]:
        """Return finding records for deterministic workflow scheduling decisions."""

        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT finding_uid, fingerprint, candidate_data, verification_task_uid, "
                    "validation_data, resolution FROM finding_records "
                    "WHERE logical_target = ? AND operation_id = ? "
                    "ORDER BY created_at, finding_uid",
                    (self.logical_target, operation_id),
                ).fetchall()
        return [
            {
                "finding_uid": row[0],
                "fingerprint": row[1],
                "candidate_data": json.loads(row[2]),
                "verification_task_uid": row[3],
                "validation_data": json.loads(row[4]) if row[4] else None,
                "resolution": row[5],
            }
            for row in rows
        ]

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
            with self._connect() as conn:
                self._register_operation(conn, operation_id)
                conn.execute(
                    "INSERT INTO finding_records "
                    "(logical_target, finding_uid, operation_id, fingerprint, candidate_data, "
                    "verification_task_uid, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.logical_target,
                        finding_uid,
                        operation_id,
                        fingerprint,
                        json.dumps(candidate_data),
                        verification_task_uid,
                        now,
                        now,
                    ),
                )

    def store_finding_evidence_receipt(
        self,
        operation_id: str,
        receipt_uid: str,
        source_task_uid: str,
        artifact_ref: str,
        marker: str,
        artifact_fingerprint: str,
    ) -> None:
        """Persist one task-bound, artifact-backed finding-evidence receipt."""

        with self._lock:
            with self._connect() as conn:
                self._register_operation(conn, operation_id)
                conn.execute(
                    "INSERT INTO finding_evidence_receipts "
                    "(logical_target, operation_id, receipt_uid, source_task_uid, artifact_ref, marker, "
                    "artifact_fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.logical_target,
                        operation_id,
                        receipt_uid,
                        source_task_uid,
                        artifact_ref,
                        marker,
                        artifact_fingerprint,
                        datetime.now().isoformat(),
                    ),
                )

    def get_finding_evidence_receipts(
        self, operation_id: str, receipt_uids: List[str]
    ) -> List[Dict[str, str]]:
        """Load receipts scoped to this target and operation in caller-supplied order."""

        if not receipt_uids:
            return []
        placeholders = ", ".join("?" for _ in receipt_uids)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT receipt_uid, source_task_uid, artifact_ref, marker, artifact_fingerprint "
                    "FROM finding_evidence_receipts WHERE logical_target = ? AND operation_id = ? "
                    f"AND receipt_uid IN ({placeholders})",
                    (self.logical_target, operation_id, *receipt_uids),
                ).fetchall()
        by_uid = {
            str(row[0]): {
                "receipt_uid": str(row[0]),
                "source_task_uid": str(row[1]),
                "artifact_ref": str(row[2]),
                "marker": str(row[3]),
                "artifact_fingerprint": str(row[4]),
            }
            for row in rows
        }
        return [by_uid[receipt_uid] for receipt_uid in receipt_uids if receipt_uid in by_uid]

    def link_finding_source_task(self, operation_id: str, finding_uid: str, task_uid: str) -> None:
        """Durably associate an idempotent finding candidate with a source task."""

        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT candidate_data FROM finding_records "
                    "WHERE logical_target = ? AND operation_id = ? AND finding_uid = ?",
                    (self.logical_target, operation_id, finding_uid),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Unknown finding_uid for source-task link: {finding_uid}")
                candidate_data = json.loads(row[0])
                source_task_uids = list(candidate_data.get("source_task_uids", []))
                if task_uid not in source_task_uids:
                    source_task_uids.append(task_uid)
                    candidate_data["source_task_uids"] = source_task_uids
                    receipts = candidate_data.setdefault("source_task_receipts", [])
                    if not any(
                        isinstance(receipt, dict) and receipt.get("task_uid") == task_uid
                        for receipt in receipts
                    ):
                        receipts.append(
                            {
                                "task_uid": task_uid,
                                "finding_uid": finding_uid,
                                "status": "persisted",
                                "evidence_refs": list(
                                    dict.fromkeys(
                                        [
                                            *candidate_data.get("artifacts", []),
                                            f"finding:{finding_uid}",
                                        ]
                                    )
                                ),
                            }
                        )
                    conn.execute(
                        "UPDATE finding_records SET candidate_data = ?, updated_at = ? "
                        "WHERE logical_target = ? AND operation_id = ? AND finding_uid = ?",
                        (
                            json.dumps(candidate_data),
                            datetime.now().isoformat(),
                            self.logical_target,
                            operation_id,
                            finding_uid,
                        ),
                    )

    def rebind_finding_verification_task(
        self,
        operation_id: str,
        finding_uid: str,
        expected_task_uid: str,
        replacement_task_uid: str,
    ) -> bool:
        """Atomically transfer an unresolved finding's verification-task ownership."""

        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "UPDATE finding_records SET verification_task_uid = ?, updated_at = ? "
                    "WHERE logical_target = ? AND operation_id = ? AND finding_uid = ? "
                    "AND verification_task_uid = ? AND resolution IS NULL AND validation_data IS NULL",
                    (
                        replacement_task_uid,
                        datetime.now().isoformat(),
                        self.logical_target,
                        operation_id,
                        finding_uid,
                        expected_task_uid,
                    ),
                )
        return cursor.rowcount == 1

    def store_finding_validation(
        self,
        operation_id: str,
        finding_uid: str,
        validation_data: Dict[str, Any],
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE finding_records SET validation_data = ?, updated_at = ? "
                    "WHERE logical_target = ? AND operation_id = ? AND finding_uid = ?",
                    (
                        json.dumps(validation_data),
                        datetime.now().isoformat(),
                        self.logical_target,
                        operation_id,
                        finding_uid,
                    ),
                )

    def update_finding_taxonomy_annotation(
        self,
        operation_id: str,
        finding_uid: str,
        annotation: Dict[str, Any],
    ) -> bool:
        """Atomically attach one taxonomy annotation to an unresolved finding candidate."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT candidate_data FROM finding_records "
                    "WHERE logical_target = ? AND operation_id = ? AND finding_uid = ?",
                    (self.logical_target, operation_id, finding_uid),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Unknown finding_uid for taxonomy annotation: {finding_uid}")
                candidate_data = json.loads(row[0])
                existing = candidate_data.get("taxonomy_annotation")
                if isinstance(existing, dict) and existing.get("status") == "completed":
                    return False
                candidate_data["taxonomy"] = annotation.get("taxonomy", {"cwe": [], "mitre_attack": []})
                candidate_data["taxonomy_annotation"] = annotation
                conn.execute(
                    "UPDATE finding_records SET candidate_data = ?, updated_at = ? "
                    "WHERE logical_target = ? AND operation_id = ? AND finding_uid = ?",
                    (
                        json.dumps(candidate_data),
                        datetime.now().isoformat(),
                        self.logical_target,
                        operation_id,
                        finding_uid,
                    ),
                )
        return True

    def update_finding_attack_enrichment(
        self,
        operation_id: str,
        finding_uid: str,
        enrichment: Dict[str, Any],
    ) -> bool:
        """Persist final ATT&CK enrichment and merge it into the finding taxonomy."""

        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT candidate_data FROM finding_records "
                    "WHERE logical_target = ? AND operation_id = ? AND finding_uid = ?",
                    (self.logical_target, operation_id, finding_uid),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Unknown finding_uid for ATT&CK enrichment: {finding_uid}")
                candidate_data = json.loads(row[0])
                existing_enrichment = candidate_data.get("final_attack_enrichment")
                if isinstance(existing_enrichment, dict) and existing_enrichment.get("status") == "completed":
                    return False

                candidate_data["final_attack_enrichment"] = enrichment
                if enrichment.get("status") == "completed":
                    taxonomy = dict(candidate_data.get("taxonomy") or {})
                    taxonomy.setdefault("cwe", [])
                    existing_attack = taxonomy.get("mitre_attack")
                    existing_attack = existing_attack if isinstance(existing_attack, list) else []
                    enrichment_taxonomy = enrichment.get("taxonomy")
                    enrichment_taxonomy = enrichment_taxonomy if isinstance(enrichment_taxonomy, dict) else {}
                    proposed_attack = enrichment_taxonomy.get("mitre_attack")
                    proposed_attack = proposed_attack if isinstance(proposed_attack, list) else []
                    merged: Dict[str, Dict[str, Any]] = {}
                    for mapping in [*existing_attack, *proposed_attack]:
                        if not isinstance(mapping, dict) or not str(mapping.get("id") or "").strip():
                            continue
                        identifier = str(mapping["id"]).upper()
                        current = merged.get(identifier)
                        if current is None or float(mapping.get("confidence", 0.0)) > float(
                            current.get("confidence", 0.0)
                        ):
                            merged[identifier] = dict(mapping)
                        elif float(mapping.get("confidence", 0.0)) == float(current.get("confidence", 0.0)):
                            evidence = [
                                *list(current.get("evidence") or []),
                                *list(mapping.get("evidence") or []),
                            ]
                            current["evidence"] = list(dict.fromkeys(evidence))
                    taxonomy["mitre_attack"] = [merged[identifier] for identifier in sorted(merged)]
                    if enrichment_taxonomy.get("provenance"):
                        taxonomy["provenance"] = enrichment_taxonomy["provenance"]
                    candidate_data["taxonomy"] = taxonomy

                conn.execute(
                    "UPDATE finding_records SET candidate_data = ?, updated_at = ? "
                    "WHERE logical_target = ? AND operation_id = ? AND finding_uid = ?",
                    (
                        json.dumps(candidate_data),
                        datetime.now().isoformat(),
                        self.logical_target,
                        operation_id,
                        finding_uid,
                    ),
                )
        return True

    def resolve_finding(self, operation_id: str, finding_uid: str, resolution: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE finding_records SET resolution = ?, updated_at = ? "
                    "WHERE logical_target = ? AND operation_id = ? AND finding_uid = ?",
                    (resolution, datetime.now().isoformat(), self.logical_target, operation_id, finding_uid),
                )

    def get_objective_candidate(self, operation_id: str, candidate_uid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT fingerprint, candidate_data, verification_task_uid, validation_data, resolution "
                    "FROM objective_validation_records "
                    "WHERE logical_target = ? AND operation_id = ? AND candidate_uid = ?",
                    (self.logical_target, operation_id, candidate_uid),
                ).fetchone()
        if not row:
            return None
        return {
            "candidate_uid": candidate_uid,
            "fingerprint": row[0],
            "candidate_data": json.loads(row[1]),
            "verification_task_uid": row[2],
            "validation_data": json.loads(row[3]) if row[3] else None,
            "resolution": row[4],
        }

    def get_objective_candidate_by_fingerprint(
        self,
        operation_id: str,
        fingerprint: str,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT candidate_uid FROM objective_validation_records "
                    "WHERE logical_target = ? AND operation_id = ? AND fingerprint = ?",
                    (self.logical_target, operation_id, fingerprint),
                ).fetchone()
        return self.get_objective_candidate(operation_id, row[0]) if row else None

    def list_objective_candidates(self, operation_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT candidate_uid FROM objective_validation_records "
                    "WHERE logical_target = ? AND operation_id = ? "
                    "ORDER BY created_at, candidate_uid",
                    (self.logical_target, operation_id),
                ).fetchall()
        return [self.get_objective_candidate(operation_id, row[0]) for row in rows]

    def store_objective_candidate(
        self,
        operation_id: str,
        candidate_uid: str,
        fingerprint: str,
        candidate_data: Dict[str, Any],
        verification_task_uid: str,
    ) -> None:
        now = datetime.now().isoformat()
        with self._lock:
            with self._connect() as conn:
                self._register_operation(conn, operation_id)
                conn.execute(
                    "INSERT INTO objective_validation_records "
                    "(logical_target, candidate_uid, operation_id, fingerprint, candidate_data, "
                    "verification_task_uid, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.logical_target,
                        candidate_uid,
                        operation_id,
                        fingerprint,
                        json.dumps(candidate_data),
                        verification_task_uid,
                        now,
                        now,
                    ),
                )

    def store_objective_validation(
        self,
        operation_id: str,
        candidate_uid: str,
        validation_data: Dict[str, Any],
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE objective_validation_records SET validation_data = ?, updated_at = ? "
                    "WHERE logical_target = ? AND operation_id = ? AND candidate_uid = ?",
                    (
                        json.dumps(validation_data),
                        datetime.now().isoformat(),
                        self.logical_target,
                        operation_id,
                        candidate_uid,
                    ),
                )

    def resolve_objective_candidate(self, operation_id: str, candidate_uid: str, resolution: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE objective_validation_records SET resolution = ?, updated_at = ? "
                    "WHERE logical_target = ? AND operation_id = ? AND candidate_uid = ?",
                    (resolution, datetime.now().isoformat(), self.logical_target, operation_id, candidate_uid),
                )


def _application_database_path(config: Optional[Dict[str, Any]] = None) -> str:
    """Return the output-root application database path."""
    resolved = config or {}
    output_dir = os.environ.get("CYBER_AGENT_OUTPUT_DIR") or resolved.get("output_dir", get_default_base_dir())
    return os.path.join(output_dir, "cyber_autoagent.db")


def get_application_database_path(config: Optional[Dict[str, Any]] = None) -> str:
    """Return the resolved application database path without opening it."""
    return _application_database_path(config or _MEMORY_CONFIG)


def require_existing_operation(
    *,
    output_dir: str,
    logical_target: str,
    operation_id: str,
) -> str:
    """Validate that an exact target/operation exists without creating it."""
    path = _application_database_path({"output_dir": output_dir})
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Application database does not exist: {path}")
    SQLiteMigrationRunner(path).migrate()
    store = create_application_store(path, logical_target=logical_target, read_only=True)
    if not store.has_operation(operation_id):
        raise FileNotFoundError(
            f"Persisted operation does not exist for target {logical_target!r}: {operation_id}"
        )
    return path


def create_application_store(
    db_path: str,
    *,
    logical_target: str,
    read_only: bool = False,
) -> ApplicationStore:
    """Construct the configured application-state backend.

    SQLite is the only backend today; this boundary allows a remote backend to
    be selected later without changing workflow and memory callers.
    """
    return SQLiteApplicationStore(db_path, logical_target=logical_target, read_only=read_only)


def _get_database_store(read_only: Optional[bool] = None) -> ApplicationStore:
    """Get the application store for the current logical-target context."""
    global _DATABASE_STORE
    configured_read_only = bool((_MEMORY_CONFIG or {}).get("read_only", False))
    requested_read_only = configured_read_only if read_only is None else read_only
    db_path = _application_database_path(_MEMORY_CONFIG)
    logical_target = str(
        (_MEMORY_CONFIG or {}).get("logical_target")
        or (_MEMORY_CONFIG or {}).get("target_name")
        or "default_target"
    )
    if (
        _DATABASE_STORE is None
        or _DATABASE_STORE.db_path != db_path
        or _DATABASE_STORE.logical_target != logical_target
        or _DATABASE_STORE.read_only != requested_read_only
    ):
        print(f"[+] Application Database: {db_path}")
        _DATABASE_STORE = create_application_store(
            db_path,
            logical_target=logical_target,
            read_only=requested_read_only,
        )
    return _DATABASE_STORE


def persist_operation_model_metrics(
    rows: List[Dict[str, Any]],
    captured_at: str,
    operation_id: Optional[str] = None,
) -> None:
    """Append one normal-assessment model-metrics capture to the application store."""
    _get_database_store().append_operation_model_metrics(operation_id or _operation_id(), captured_at, rows)


def list_persisted_operation_model_metrics(operation_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read every model-metrics capture for the current logical target and operation."""
    return _get_database_store().list_operation_model_metrics(operation_id or _operation_id())


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
_RE_HOST_PORT_TARGET = re.compile(
    r"(?<![a-zA-Z0-9.:-])(?:\[[0-9A-Fa-f:.%]+\]|(?:[a-zA-Z0-9-]+\.)*[a-zA-Z0-9-]+):\d{1,5}\b"
)
_RE_FQDN_TARGET = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")
_RE_IPV6_TARGET = re.compile(r"(?<!\S)(?=[0-9A-Fa-f:]*:)[0-9A-Fa-f:]+(?:/\d{1,3})?(?=$|\s|[,;)])")
_RE_QUOTED_PATH_TARGET = re.compile(r'["\']((?:/|\./|\.\./)[^"\']+)["\']')
_RE_BARE_TARGET_CONTEXT = re.compile(
    r"\b(?:target|host|endpoint|system|against|assess|scan|test|verify)\s+([a-zA-Z0-9][a-zA-Z0-9_.-]+)\b",
    re.IGNORECASE,
)
_RE_CONTEXTUAL_FQDN_TARGET = re.compile(
    r"\b(?:target|host|endpoint|system|against|assess|scan|test|verify|at)\s+"
    r"((?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,})\b",
    re.IGNORECASE,
)


def _classify_target_literal(value: str, *, allow_bare_hostname: bool = False) -> Optional[TargetType]:
    stripped = value.strip().strip(".,;)")
    if not stripped:
        return None
    if stripped.startswith(("/", "./", "../")):
        return "filesystem"
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
    stripped = value.strip().strip(".,;)")
    if target_type == "filesystem":
        return os.path.realpath(os.path.expanduser(stripped))
    return stripped


def _target_candidates_from_text(text: str, *, allow_bare_hostname: bool, source: str) -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    patterns = (
        _RE_URL_PATTERN,
        _RE_CIDR_TARGET,
        _RE_HOST_PORT_TARGET,
        _RE_IP_TARGET,
        _RE_IPV6_TARGET,
    )
    for pattern in patterns:
        candidates.extend((match.group(0), source) for match in pattern.finditer(text or ""))

    if source == "objective":
        candidates.extend((match.group(1), source) for match in _RE_CONTEXTUAL_FQDN_TARGET.finditer(text or ""))
    else:
        candidates.extend((match.group(0), source) for match in _RE_FQDN_TARGET.finditer(text or ""))
        candidates.extend((match.group(0), source) for match in _RE_PATH_PATTERN.finditer(text or ""))
        candidates.extend((match.group(1), source) for match in _RE_QUOTED_PATH_TARGET.finditer(text or ""))
    if allow_bare_hostname and source != "objective":
        candidates.extend((match.group(1), source) for match in _RE_BARE_TARGET_CONTEXT.finditer(text or ""))
    return candidates


def resolve_operation_targets(logical_target: str, objective: str = "") -> List[OperationTarget]:
    """Resolve executable targets while keeping the CLI target as logical naming.

    Objective network literals win. Paths in an objective are remote-resource hints, not local filesystem targets.
    If the objective has no executable network literal, the logical target is used as a fallback and may be a bare
    hostname or path.
    """

    raw_candidates = _target_candidates_from_text(objective or "", allow_bare_hostname=False, source="objective")
    has_objective_targets = any(_classify_target_literal(value) for value, _ in raw_candidates)
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
            allow_bare_hostname=source == "logical_target_fallback",
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
    configured_mode = (_MEMORY_CONFIG or {}).get("memory_mode")
    return str(configured_mode or os.getenv("CYBER_MEMORY_MODE", "operation")).lower() == "shared"


def _ensure_memory_client() -> "QdrantMemoryClient":
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


def _proof_pack_path_from_value(value: Any) -> str:
    """Resolve one proof-pack file only when it belongs to this operation's root."""

    reference = str(value or "").strip()
    if not reference:
        raise ValueError("proof-pack path is required")
    if reference.startswith(("artifact:", "artifact_id:")):
        return _artifact_path_from_ref(reference)

    root = os.path.realpath(_operation_output_root())
    candidate = reference if os.path.isabs(reference) else os.path.join(root, reference)
    resolved = os.path.realpath(candidate)
    if os.path.commonpath([root, resolved]) != root:
        raise ValueError(f"Proof-pack path is outside the current operation output: {reference}")
    if not os.path.isfile(resolved):
        raise ValueError(f"Proof-pack file does not exist: {reference}")
    return resolved


def _has_valid_proof_pack(finding: Any) -> bool:
    """Validate proof_pack structure and artifact existence (fail-closed).

    Expectations:
    - proof_pack is a dict with key 'artifacts': List[str] of file paths (absolute or relative)
    - Optional 'rationale': short string tying artifacts to impact
    - Every usable proof path MUST resolve inside the current operation output and exist at validation time

    Notes:
    - No content parsing or domain heuristics are used here; operation-local file presence only
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
                try:
                    _proof_pack_path_from_value(e_str)
                except ValueError:
                    pass
                else:
                    return True
                matches = _RE_PROOF_PACK_FILE_PATTERN.findall(e_str)
                file_paths = [path.strip() for paths in matches for path in paths.split(",")]
                for path in file_paths:
                    try:
                        _proof_pack_path_from_value(path)
                    except ValueError:
                        continue
                    else:
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


def _memory_result_items(result: Any) -> List[Dict[str, Any]]:
    """Normalize supported memory result envelopes into memory records."""

    if isinstance(result, dict):
        nested = result.get("results")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if result.get("id"):
            return [result]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    return []


def _memory_id_from_result(result: Any) -> str:
    """Return the first non-empty memory ID in a storage result."""

    for item in _memory_result_items(result):
        memory_id = str(item.get("id") or "").strip()
        if memory_id:
            return memory_id
    return ""


def _exact_memory_match(result: Any, content: str) -> Optional[Dict[str, Any]]:
    """Return an exact cleaned-content match from a memory search result."""

    for item in _memory_result_items(result):
        try:
            candidate = _clean_memory_text(item.get("memory"), "memory")
        except ValueError:
            continue
        if candidate == content:
            return item
    return None


def _search_memory_entry(client: Any, content: str, user_id: str, operation_id: str, category: str) -> Any:
    """Search for a category-fixed memory in the configured memory scope."""

    return client.search(
        query=content,
        user_id=user_id,
        run_id=None if memory_is_cross_operation() else operation_id,
        limit=5,
        filters={"category": category},
    )


def _store_memory_entry(
    content: str,
    category: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> _MemoryStoreResult:
    """Store one category-fixed semantic memory with duplicate protection."""

    cleaned_content = _clean_memory_text(content, "content")
    cleaned_metadata = _clean_metadata(metadata)
    cleaned_metadata["category"] = category
    op_id = _operation_id()
    cleaned_metadata["operation_id"] = op_id
    client = _ensure_memory_client()
    user_id = _user_id()

    try:
        search = _search_memory_entry(client, cleaned_content, user_id, op_id, category)
    except Exception:
        search = {}
        logger.debug("Unable to check for memory duplicate", exc_info=True)

    existing = _exact_memory_match(search, cleaned_content)
    existing_id = _memory_id_from_result(existing)
    if existing_id:
        return _MemoryStoreResult(created=False, memory_id=existing_id)

    stored = client.store_memory(cleaned_content, user_id, _agent_id(), cleaned_metadata)
    memory_id = _memory_id_from_result(stored)
    if not memory_id:
        try:
            recovered = _search_memory_entry(client, cleaned_content, user_id, op_id, category)
        except Exception as error:
            raise RuntimeError("Memory was stored, but its durable ID could not be recovered") from error
        memory_id = _memory_id_from_result(_exact_memory_match(recovered, cleaned_content))
    if not memory_id:
        raise RuntimeError("Memory was stored, but the backend did not return a durable ID")
    _emit_memory_added(memory_id, category, cleaned_content)
    return _MemoryStoreResult(created=True, memory_id=memory_id)


def set_memory_event_emitter(emitter: Optional[Callable[[Dict[str, Any]], None]]) -> None:
    """Set the callback used to publish successful durable memory writes."""

    global _MEMORY_EVENT_EMITTER
    _MEMORY_EVENT_EMITTER = emitter


def _emit_memory_added(memory_id: str, category: str, content: str) -> None:
    """Publish one best-effort event after a new memory receives a durable ID."""

    emitter = _MEMORY_EVENT_EMITTER
    if not callable(emitter):
        return
    preview_limit = 240
    preview = content[:preview_limit]
    if len(content) > preview_limit:
        preview += "..."
    try:
        emitter(
            {
                "type": "memory_added",
                "memory_id": memory_id,
                "memory_ref": f"memory:{memory_id}",
                "category": category,
                "content_preview": preview,
                "content_length": len(content),
            }
        )
    except Exception:
        logger.debug("Unable to emit memory-added event", exc_info=True)


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


def _validated_artifact_paths(
    artifacts: Any,
    *,
    require_one: bool = False,
    allow_delimited_strings: bool = False,
) -> List[str]:
    validated: List[str] = []
    for raw_path in split_delimited_reference_values(
        artifacts,
        allow_delimited_strings=allow_delimited_strings,
    ):
        reference = canonical_artifact_reference(raw_path)
        if reference not in validated:
            validated.append(reference)
    if require_one and not validated:
        raise ValueError("At least one existing artifact is required")
    return validated


def _validate_finding_validation_input_shape(
    reproduction_steps: Any,
    evidence_artifacts: Any,
    control_artifacts: Any,
    validation_manifest: Any,
) -> None:
    """Reject malformed validation payloads before interpreting values as paths."""

    if not isinstance(reproduction_steps, list) or not all(
        isinstance(step, str) for step in reproduction_steps
    ):
        raise ValueError("reproduction_steps must be an array of strings")
    for field_name, references in (
        ("evidence_artifacts", evidence_artifacts),
        ("control_artifacts", control_artifacts),
    ):
        if references is not None and not (
            isinstance(references, str)
            or (
                isinstance(references, list)
                and all(isinstance(reference, str) for reference in references)
            )
        ):
            raise ValueError(f"{field_name} must be an artifact reference string or array of strings")
    if validation_manifest is not None and not isinstance(validation_manifest, str):
        raise ValueError("validation_manifest must be an artifact reference string")


def _artifact_fingerprints(references: List[str]) -> Dict[str, str]:
    """Snapshot evidence identity without assuming an HTTP or tool output format."""

    fingerprints: Dict[str, str] = {}
    for reference in references:
        path = Path(_artifact_path_from_ref(reference))
        try:
            fingerprints[reference] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise ValueError(f"Unable to fingerprint finding artifact: {reference}") from error
    return fingerprints


def _json_pointer_value(payload: Any, pointer: str) -> Any:
    """Resolve one RFC 6901 JSON Pointer without evaluating model-authored code."""

    if pointer == "":
        return payload
    if not pointer.startswith("/"):
        raise ValueError("json_value evidence assertion pointer must be empty or start with /")
    current = payload
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (IndexError, TypeError, ValueError) as error:
                raise ValueError(f"json_value evidence assertion pointer does not exist: {pointer}") from error
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ValueError(f"json_value evidence assertion pointer does not exist: {pointer}")
    return current


def _assertion_matches_artifact(assertion: Dict[str, Any], reference: str) -> bool:
    """Evaluate one canonical, data-only assertion against an artifact."""

    path = Path(_artifact_path_from_ref(reference))
    assertion_type = str(assertion.get("type") or ("literal_text" if "marker" in assertion else ""))
    try:
        if assertion_type == "literal_text":
            value = str(assertion.get("value", assertion.get("marker", "")))
            return bool(value) and value in path.read_text(encoding="utf-8", errors="replace")
        if assertion_type == "byte_sequence":
            encoding = assertion["encoding"]
            expected = (
                bytes.fromhex(assertion["value"])
                if encoding == "hex"
                else base64.b64decode(assertion["value"], validate=True)
            )
            return bool(expected) and expected in path.read_bytes()
        if assertion_type == "json_value":
            actual = _json_pointer_value(json.loads(path.read_text(encoding="utf-8")), assertion["pointer"])
            operator = assertion["operator"]
            if operator == "exists":
                return True
            if operator == "equals":
                return actual == assertion.get("expected")
            expected = assertion.get("expected")
            return (
                expected in actual
                if isinstance(actual, (str, list, dict))
                else False
            )
        if assertion_type == "secret_exposure":
            kind = str(assertion.get("kind") or "")
            digest = str(assertion.get("digest") or "")
            return any(
                exposure.kind == kind and exposure.digest == digest
                for exposure in detect_secret_exposures(reference)
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return False
    return False


def _canonical_assertion_predicate(assertion: Dict[str, Any]) -> str:
    """Return an artifact-independent identity for a canonical evidence assertion."""

    predicate = {key: value for key, value in assertion.items() if key not in {"artifact", "marker"}}
    if not predicate.get("type") and assertion.get("marker"):
        predicate = {"type": "literal_text", "value": str(assertion["marker"])}
    return json.dumps(
        predicate,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validated_evidence_assertions(
    assertions: Any,
    allowed_artifacts: List[str],
    *,
    require_one: bool = False,
) -> List[Dict[str, Any]]:
    """Normalize and validate typed evidence predicates against cited artifacts."""

    if assertions is None:
        assertions = []
    if not isinstance(assertions, list):
        raise ValueError("evidence_assertions must be a list")
    validated: List[Dict[str, str]] = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            raise ValueError("each evidence assertion must be an object")
        reference = _validated_artifact_paths([assertion.get("artifact")], require_one=True)[0]
        if reference not in allowed_artifacts:
            raise ValueError("evidence assertion artifact must be among the cited artifacts")
        assertion_type = str(assertion.get("type") or ("literal_text" if "marker" in assertion else "")).strip()
        if assertion_type not in {"literal_text", "byte_sequence", "json_value", "secret_exposure"}:
            raise ValueError(
                "evidence assertion type must be literal_text, byte_sequence, json_value, or secret_exposure"
            )
        normalized: Dict[str, Any] = {"artifact": reference, "type": assertion_type}
        if assertion_type == "literal_text":
            value = _clean_memory_text(assertion.get("value", assertion.get("marker")), "evidence assertion value")
            if not value:
                raise ValueError("literal_text evidence assertion value must not be empty")
            normalized["value"] = value
            # Retain the legacy field in persisted records and receipts during the additive transition.
            normalized["marker"] = value
        elif assertion_type == "byte_sequence":
            encoding = str(assertion.get("encoding") or "").lower().strip()
            value = str(assertion.get("value") or "").strip()
            if encoding not in {"hex", "base64"} or not value:
                raise ValueError("byte_sequence evidence assertion requires value and encoding hex or base64")
            normalized.update({"encoding": encoding, "value": value})
        elif assertion_type == "json_value":
            pointer = str(assertion.get("pointer") or "")
            operator = str(assertion.get("operator") or "").lower().strip()
            if operator not in {"exists", "equals", "contains"}:
                raise ValueError("json_value evidence assertion operator must be exists, equals, or contains")
            if operator != "exists" and "expected" not in assertion:
                raise ValueError(f"json_value {operator} evidence assertion requires expected")
            normalized.update({"pointer": pointer, "operator": operator})
            if "expected" in assertion:
                normalized["expected"] = assertion["expected"]
        else:
            kind = str(assertion.get("kind") or "").strip()
            digest = str(assertion.get("digest") or "").lower().strip()
            if not kind or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("secret_exposure evidence assertion requires kind and SHA-256 digest")
            normalized.update({"kind": kind, "digest": digest})
        if not _assertion_matches_artifact(normalized, reference):
            raise ValueError(f"evidence assertion was not satisfied by {reference}")
        if normalized not in validated:
            validated.append(normalized)
    if require_one and not validated:
        raise ValueError("At least one evidence assertion is required")
    return validated


def _matching_evidence_assertions(
    candidate_assertions: Any,
    validation_assertions: Any,
    validation_artifacts: List[str],
    validation_fingerprints: Any,
) -> bool:
    """Return whether validation re-proved every candidate evidence predicate."""

    try:
        candidate_predicates = {
            _canonical_assertion_predicate(item)
            for item in _validated_evidence_assertions(candidate_assertions, [
                str(item.get("artifact")) for item in candidate_assertions if isinstance(item, dict)
            ], require_one=True)
        }
        validated = _validated_evidence_assertions(
            validation_assertions, validation_artifacts, require_one=True
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    if not candidate_predicates or candidate_predicates != {
        _canonical_assertion_predicate(item) for item in validated
    }:
        return False
    expected_fingerprints = validation_fingerprints if isinstance(validation_fingerprints, dict) else {}
    try:
        return expected_fingerprints == _artifact_fingerprints(validation_artifacts)
    except ValueError:
        return False


@tool(
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "artifacts": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
            },
            "required": ["content"],
        }
    }
)
def store_observation(
    content: str,
    artifacts: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Store one operation-specific fact, failed attempt, or informational result.

    Observations may be reportable but are never promoted into findings based on severity.
    The returned memory_ref is valid observation evidence for record_task_acceptance.
    """

    merged = _clean_metadata(metadata)
    source_task = _active_finding_source_task(_get_database_store(), _operation_id())
    try:
        plan = _get_active_plan()
    except (RuntimeError, ValueError):
        plan = None
    if source_task is not None and plan is not None:
        selected_targets = _selected_task_targets(plan, source_task)
        if len(selected_targets) == 1:
            target = selected_targets[0]
            supplied_location = str(merged.get("target") or merged.get("location") or "").strip()
            if supplied_location and "://" in supplied_location and target.type == "network":
                submitted = urlsplit(supplied_location)
                registered = urlsplit(target.value)
                try:
                    matches = (
                        submitted.scheme.lower() == registered.scheme.lower()
                        and submitted.hostname is not None
                        and registered.hostname is not None
                        and submitted.hostname.lower().rstrip(".") == registered.hostname.lower().rstrip(".")
                        and submitted.port == registered.port
                    )
                except ValueError:
                    matches = False
                if not matches:
                    raise ValueError(
                        "observation target/location must match the active task's assigned target boundary"
                    )
            merged["target_id"] = target.target_id
            merged["target"] = target.value
            merged.pop("location", None)
    if artifacts:
        merged["artifacts"] = _validated_artifact_paths(artifacts, allow_delimited_strings=True)
    result = _store_memory_entry(content, "observation", merged)
    return json.dumps(
        {
            "stored": True,
            "created": result.created,
            "memory_ref": f"memory:{result.memory_id}",
        }
    )


@tool
def store_knowledge(content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Store one reusable technique, lesson, or durable internal note.

    Knowledge remains retrievable but is excluded from security assessment reports.
    The returned memory_ref may be supplied as durable evidence for record_task_acceptance.
    """

    result = _store_memory_entry(content, "knowledge", metadata)
    return json.dumps(
        {
            "stored": True,
            "created": result.created,
            "memory_ref": f"memory:{result.memory_id}",
        }
    )


def _finding_fingerprint(title: str, claim: str, target: str, technique: str) -> str:
    """Return a stable finding identity independent of model-authored title wording.

    The subject extraction keeps distinct leaked-secret classes separate while
    merging duplicate descriptions of the same exposure at one affected endpoint.
    """

    del title
    normalized_claim = re.sub(r"\s+", " ", claim.strip().lower())
    subject_patterns = {
        "database_connection": r"(?:postgres(?:ql)?|mysql|mssql|mongo(?:db)?|database).{0,40}(?:uri|url|connection|string)|(?:uri|url|connection|string).{0,40}(?:database|postgres(?:ql)?|mysql|mssql|mongo(?:db)?)",
        "third_party_api_key": r"(?:google|maps|aws|azure|stripe|api)[\s_-]*(?:api[\s_-]*)?key|api[\s_-]*key",
        "session_or_auth_secret": r"(?:jwt|session|cookie|oauth|auth(?:entication)?)[\s_-]*(?:secret|token|key)",
        "private_key": r"(?:private|ssh|rsa)[\s_-]*key",
    }
    subjects = sorted(name for name, pattern in subject_patterns.items() if re.search(pattern, normalized_claim))
    evidence_subject = ",".join(subjects) or normalized_claim
    normalized = "|".join(
        re.sub(r"\s+", " ", value.strip().lower()) for value in (target, technique, evidence_subject)
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _active_finding_source_task_uid(store: Any, operation_id: str) -> str:
    """Return the sole active non-verification task when a workflow executor owns the call."""

    task = _active_finding_source_task(store, operation_id)
    return task.task_uid if task is not None else ""


def _active_finding_source_task(store: Any, operation_id: str) -> Optional[Task]:
    """Return the sole active non-verification task when a workflow executor owns the call."""

    active = [
        task
        for task in store.get_tasks(operation_id)
        if task.status == "active" and task.kind not in {"finding_validation", "objective_validation"}
    ]
    return active[0] if len(active) == 1 else None


def _effective_url_port(parsed: Any) -> Optional[int]:
    """Return an explicit or conventional URL port without assuming HTTP-only targets."""

    try:
        if parsed.port is not None:
            return parsed.port
    except ValueError:
        return None
    return {"http": 80, "https": 443}.get(str(parsed.scheme or "").lower())


def _selected_finding_targets(plan: OperationPlan, task: Optional[Task]) -> List[OperationTarget]:
    """Return operation targets assigned to a finding-producing task."""

    selected_ids = set(task.target_ids) if task is not None and task.target_scope == "subset" else set()
    return [target for target in plan.targets if not selected_ids or target.target_id in selected_ids]


def _url_target_match(submitted: Any, target: OperationTarget) -> bool:
    registered = urlsplit(str(target.value or "").strip())
    try:
        return bool(
            submitted.scheme
            and registered.scheme
            and submitted.hostname
            and registered.hostname
            and submitted.scheme.lower() == registered.scheme.lower()
            and submitted.hostname.lower().rstrip(".") == registered.hostname.lower().rstrip(".")
            and _effective_url_port(submitted) == _effective_url_port(registered)
        )
    except ValueError:
        return False


def _single_edit_hostname_match(left: str, right: str) -> bool:
    """Recognize one conservative hostname typo without granting execution authority."""

    left = str(left or "").lower().rstrip(".")
    right = str(right or "").lower().rstrip(".")
    if not left or not right or min(len(left), len(right)) < 5 or abs(len(left) - len(right)) > 1:
        return False
    if left == right:
        return True
    if len(left) == len(right):
        differences = [index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]]
        if len(differences) == 1:
            return True
        return (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and left[differences[0]] == right[differences[1]]
            and left[differences[1]] == right[differences[0]]
        )
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = long_index = differences = 0
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
        else:
            differences += 1
            if differences > 1:
                return False
        long_index += 1
    return True


def _url_target_typo_match(submitted: Any, target: OperationTarget) -> bool:
    """Match one unique service hostname typo while preserving scheme and port."""

    registered = urlsplit(str(target.value or "").strip())
    try:
        return bool(
            submitted.scheme
            and registered.scheme
            and submitted.hostname
            and registered.hostname
            and submitted.scheme.lower() == registered.scheme.lower()
            and _effective_url_port(submitted) == _effective_url_port(registered)
            and _single_edit_hostname_match(submitted.hostname, registered.hostname)
        )
    except ValueError:
        return False


def _path_is_within(candidate: str, root: str) -> bool:
    """Return whether a resolved filesystem location is equal to or below an assigned root."""

    try:
        return os.path.commonpath([candidate, root]) == root
    except ValueError:
        return False


def _canonicalize_finding_target(
    target_value: str,
    plan: Optional[OperationPlan],
    source_task: Optional[Task],
) -> Tuple[str, List[str]]:
    """Resolve a finding location without changing explicitly supplied target identity."""

    if not isinstance(plan, OperationPlan):
        return target_value, []
    value = str(target_value or "").strip()
    selected = _selected_finding_targets(plan, source_task)
    allowed = ", ".join(f"{target.target_id}={target.value}" for target in selected)
    logical_matches = [target for target in selected if value == target.target_id]
    if len(logical_matches) == 1:
        return logical_matches[0].value, [logical_matches[0].target_id]

    submitted = urlsplit(value)
    if "://" in value and submitted.scheme:
        matches = [target for target in selected if _url_target_match(submitted, target)]
        for target in selected:
            if target in matches or not submitted.hostname:
                continue
            if target.type == "network" and submitted.hostname.lower().rstrip(".") == target.value.lower().rstrip("."):
                matches.append(target)
            elif target.type == "network_range":
                try:
                    if ipaddress.ip_address(submitted.hostname) in ipaddress.ip_network(target.value, strict=False):
                        matches.append(target)
                except ValueError:
                    continue
        typo_corrected = False
        if not matches:
            typo_matches = [target for target in selected if _url_target_typo_match(submitted, target)]
            if len(typo_matches) == 1:
                matches = typo_matches
                typo_corrected = True
        if len(matches) != 1:
            raise ValueError(
                "finding target authority must match exactly one assigned target; "
                f"submitted={value}; allowed targets: {allowed}"
            )
        registered = urlsplit(matches[0].value)
        if typo_corrected:
            logger.warning(
                "Canonicalized unique finding hostname typo submitted=%s assigned=%s target_id=%s",
                submitted.hostname,
                registered.hostname,
                matches[0].target_id,
            )
        if not registered.scheme:
            return urlunsplit(
                (submitted.scheme.lower(), submitted.netloc, submitted.path or "/", submitted.query, "")
            ), [matches[0].target_id]
        canonical = urlunsplit(
            (registered.scheme.lower(), registered.netloc, submitted.path or "/", submitted.query, "")
        )
        return canonical, [matches[0].target_id]

    service_targets = [
        target for target in selected
        if "://" in str(target.value) and urlsplit(str(target.value)).scheme
    ]
    if value.startswith("/") and len(service_targets) == 1 and len(selected) == 1:
        registered = str(service_targets[0].value).rstrip("/") + "/"
        return urljoin(registered, value), [service_targets[0].target_id]

    filesystem_matches = []
    for target in selected:
        if target.type != "filesystem":
            continue
        root = os.path.realpath(target.value)
        candidate = os.path.realpath(value if os.path.isabs(value) else os.path.join(root, value))
        if _path_is_within(candidate, root):
            filesystem_matches.append((target, candidate))
    if len(filesystem_matches) == 1:
        return filesystem_matches[0][1], [filesystem_matches[0][0].target_id]
    if len(service_targets) == 1 and len(selected) == 1 and not filesystem_matches:
        registered = str(service_targets[0].value).rstrip("/") + "/"
        return urljoin(registered, value), [service_targets[0].target_id]

    try:
        submitted_ip = ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        submitted_ip = None
    network_matches = []
    for target in selected:
        if target.type == "network_range" and submitted_ip is not None:
            try:
                if submitted_ip in ipaddress.ip_network(target.value, strict=False):
                    network_matches.append(target)
            except ValueError:
                continue
        elif target.type == "network" and value.rstrip("/") == target.value.rstrip("/"):
            network_matches.append(target)
    if len(network_matches) == 1:
        return value, [network_matches[0].target_id]
    if filesystem_matches or network_matches or value.startswith("/"):
        raise ValueError(f"finding target is ambiguous within assigned targets: {allowed}")
    raise ValueError(
        "finding target must be an assigned logical target, an in-scope absolute location, or an unambiguous "
        f"relative location; submitted={value}; allowed targets: {allowed}"
    )


def _finding_tool_result(finding_uid: str, verification_task_uid: str, status: str) -> str:
    return json.dumps(
        {
            "finding_ref": f"finding:{finding_uid}",
            "finding_uid": finding_uid,
            "status": status,
            "verification_task_ref": f"task:{verification_task_uid}",
            "verification_task_uid": verification_task_uid,
        },
        sort_keys=True,
    )


def _record_source_task_finding_receipt(
    source_task: Optional[Task],
    candidate: Dict[str, Any],
    finding_uid: str,
) -> None:
    """Record candidate persistence without treating it as task-acceptance evidence.

    A persisted candidate is useful progress, but it is not proof that the source
    task's frozen criterion has been satisfied.  Keep this receipt inside the
    candidate record so a later validation failure can be reported without
    rewriting an immutable acceptance ledger.
    """

    if source_task is None:
        return
    receipts = candidate.setdefault("source_task_receipts", [])
    if not any(item.get("task_uid") == source_task.task_uid for item in receipts if isinstance(item, dict)):
        receipts.append(
            {
                "task_uid": source_task.task_uid,
                "finding_uid": finding_uid,
                "status": "persisted",
                "evidence_refs": list(dict.fromkeys([*candidate["artifacts"], f"finding:{finding_uid}"])),
            }
        )


@tool(
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "claim": {"type": "string"},
                "severity": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]},
                "target": {"type": "string"},
                "technique": {"type": "string"},
                "expected_result": {"type": "string"},
                "observed_result": {"type": "string"},
                "reproduction_steps": {"type": "array", "items": {"type": "string"}},
                "artifacts": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "evidence_assertions": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "artifact": {"type": "string"},
                            "type": {
                                "type": "string",
                                "enum": ["literal_text", "byte_sequence", "json_value", "secret_exposure"],
                            },
                            "marker": {"type": "string"},
                            "value": {},
                            "encoding": {"type": "string", "enum": ["hex", "base64"]},
                            "pointer": {"type": "string"},
                            "operator": {"type": "string", "enum": ["exists", "equals", "contains"]},
                            "expected": {},
                            "kind": {"type": "string"},
                            "digest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
                        },
                        "oneOf": [
                            {"required": ["artifact", "marker"]},
                            {
                                "properties": {"type": {"const": "literal_text"}},
                                "required": ["artifact", "type", "value"],
                            },
                            {
                                "properties": {"type": {"const": "byte_sequence"}},
                                "required": ["artifact", "type", "encoding", "value"],
                            },
                            {
                                "properties": {"type": {"const": "json_value"}},
                                "required": ["artifact", "type", "pointer", "operator"],
                            },
                            {
                                "properties": {"type": {"const": "secret_exposure"}},
                                "required": ["artifact", "type", "kind", "digest"],
                            },
                        ],
                    },
                    "description": (
                        "Typed positive predicates observed in cited artifacts. Legacy marker is literal_text."
                    ),
                },
            },
            "required": [
                "title", "claim", "severity", "target", "technique", "expected_result", "observed_result",
                "reproduction_steps", "artifacts", "evidence_assertions",
            ],
        }
    }
)
def store_finding(
    title: str,
    claim: str,
    severity: str,
    target: str,
    technique: str,
    expected_result: str,
    observed_result: str,
    reproduction_steps: List[str],
    artifacts: NonEmptyArtifactRefs,
    evidence_assertions: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Submit one finding candidate and create its dedicated verification task.

    This tool never creates a verified finding directly. Each candidate must include typed positive-evidence assertions
    satisfied by its cited artifacts; a separate controller-owned task must independently reproduce every assertion
    before the finding can affect confirmed risk totals. Taxonomy classification is performed by a separate, read-only
    workflow agent after this candidate is persisted. The candidate location is bound to its assigned service, network,
    or filesystem target before persistence.
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
    candidate["artifacts"] = _validated_artifact_paths(
        artifacts,
        require_one=True,
        allow_delimited_strings=True,
    )
    candidate["artifact_fingerprints"] = _artifact_fingerprints(candidate["artifacts"])
    op_id = _operation_id()
    store = _get_database_store()
    source_task = _active_finding_source_task(store, op_id)
    candidate["evidence_assertions"] = _validated_evidence_assertions(
        evidence_assertions, candidate["artifacts"], require_one=True
    )
    source_task_uid = source_task.task_uid if source_task is not None else ""
    candidate["evidence_receipts"] = []
    if source_task is not None:
        for assertion in candidate["evidence_assertions"]:
            receipt_uid = str(uuid.uuid4())
            store.store_finding_evidence_receipt(
                op_id,
                receipt_uid,
                source_task.task_uid,
                assertion["artifact"],
                str(assertion.get("marker") or _canonical_assertion_predicate(assertion)),
                candidate["artifact_fingerprints"][assertion["artifact"]],
            )
            candidate["evidence_receipts"].append(f"finding_evidence:{receipt_uid}")
    try:
        active_plan = _get_active_plan()
    except ValueError:
        active_plan = None
    candidate["target"], bound_target_ids = _canonicalize_finding_target(
        candidate["target"], active_plan, source_task
    )
    fingerprint = _finding_fingerprint(
        candidate["title"], candidate["claim"], candidate["target"], candidate["technique"]
    )
    existing = store.get_finding_by_fingerprint(op_id, fingerprint)
    if existing:
        if source_task_uid:
            store.link_finding_source_task(op_id, existing["finding_uid"], source_task_uid)
            logger.info(
                "Linked idempotent finding candidate %s to source task %s",
                existing["finding_uid"],
                source_task_uid,
            )
        return _finding_tool_result(
            existing["finding_uid"],
            existing["verification_task_uid"],
            existing.get("resolution") or "pending_validation",
        )

    finding_uid = str(uuid.uuid4())
    task_uid = str(uuid.uuid4())
    candidate["finding_uid"] = finding_uid
    candidate["validation_status"] = "pending"
    candidate["source_task_uids"] = [source_task_uid] if source_task_uid else []
    candidate["source_task_receipts"] = []
    _record_source_task_finding_receipt(source_task, candidate, finding_uid)
    content = (
        f"[VULNERABILITY] {candidate['title']} [WHERE] {candidate['target']} "
        f"[IMPACT] {candidate['claim']} [EVIDENCE] {candidate['observed_result']} "
        f"[STEPS] {'; '.join(candidate['reproduction_steps'])}"
    )
    _store_memory_entry(content, "finding_candidate", candidate)

    current_phase = _get_plan_current_phase()
    verification_phase = _finding_validation_task_phase(active_plan, current_phase)
    target_ids = bound_target_ids or _target_ids_for_literal(candidate["target"])
    target_scope: TargetScope = "subset" if target_ids else "all"
    candidate["verification_packet"] = {
        "version": 1,
        "finding_uid": finding_uid,
        "confirmation_guard_catalog_version": 1,
        "confirmation_requirements": _finding_confirmation_requirements(candidate),
        "source_task": {
            "task_uid": source_task_uid,
            "title": source_task.title if source_task is not None else "",
            "objective": source_task.objective if source_task is not None else "",
        },
        "target": candidate["target"],
        "target_scope": target_scope,
        "target_ids": target_ids,
        "claim": candidate["claim"],
        "technique": candidate["technique"],
        "expected_result": candidate["expected_result"],
        "observed_result": candidate["observed_result"],
        "reproduction_steps": candidate["reproduction_steps"],
        "evidence_assertions": candidate["evidence_assertions"],
        "artifacts": candidate["artifacts"],
        "artifact_fingerprints": candidate["artifact_fingerprints"],
    }
    task = Task(
        task_uid=task_uid,
        title=f"Verify finding: {candidate['title']}",
        objective=(
            f"Independently verify finding candidate {finding_uid} against {candidate['target']}. "
            "Re-test the target to reproduce the reported finding behavior, capture required evidence "
            "in fresh direct or differential artifacts, call record_finding_validation with the outcome, "
            "and stop."
        ),
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="snapshot",
                description=f"Finding candidate {finding_uid}",
                source_refs=[f"finding:{finding_uid}"],
            ),
            criteria=[
                AcceptanceCriterion(
                    id=f"verify-finding:{finding_uid}",
                    description="Record an evidence-backed independent validation outcome for the finding candidate.",
                    evidence_requirements=[EvidenceRequirement(kind="artifact", min_count=1)],
                )
            ],
        ),
        evidence=candidate["artifacts"],
        phase=verification_phase,
        status="pending",
        kind="finding_validation",
        reference_id=finding_uid,
        target_scope=target_scope,
        target_ids=target_ids,
    )
    store.store_finding_candidate(op_id, finding_uid, fingerprint, candidate, task_uid)
    _ensure_memory_client().store_task(task=task, user_id=_user_id())
    return _finding_tool_result(finding_uid, task_uid, "pending_validation")


def _load_finding_validation_guards() -> List[Dict[str, Any]]:
    """Load the small, declarative catalog of unambiguous validation contradictions."""

    try:
        payload = yaml.safe_load(_FINDING_VALIDATION_GUARDS_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError("finding validation guard catalog is unavailable or invalid") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("finding validation guard catalog must declare version 1")
    rules = payload.get("rules")
    if not isinstance(rules, list):
        raise ValueError("finding validation guard catalog rules must be a list")
    validated = []
    for item in rules:
        if not isinstance(item, dict):
            raise ValueError("finding validation guard rules must be objects")
        rule_id = str(item.get("id") or "").strip()
        claim_terms = item.get("claim_terms")
        all_of = item.get("contradiction_all_of")
        any_of = item.get("contradiction_any_of")
        json_shape = str(item.get("contradiction_json_shape") or "").strip()
        confirmation_requirement = str(item.get("confirmation_requirement") or "").strip()
        if (
            not rule_id
            or not isinstance(claim_terms, list)
            or not all(isinstance(term, str) and term.strip() for term in claim_terms)
            or (not confirmation_requirement and sum(bool(value) for value in (all_of, any_of, json_shape)) != 1)
            or (
                confirmation_requirement
                and confirmation_requirement
                not in {"response_comparison", "rate_limit_probe", "secret_exposure_revalidation"}
            )
            or (json_shape and json_shape not in {"flat_json_object"})
        ):
            raise ValueError(f"finding validation guard rule {rule_id or '<unknown>'} is invalid")
        markers = all_of or any_of
        if markers and (
            not isinstance(markers, list)
            or not all(isinstance(marker, str) and marker.strip() for marker in markers)
        ):
            raise ValueError(f"finding validation guard rule {rule_id} has invalid contradiction markers")
        validated.append({
            "id": rule_id,
            "claim_terms": [term.lower().strip() for term in claim_terms],
            "contradiction_all_of": [marker.lower().strip() for marker in all_of or []],
            "contradiction_any_of": [marker.lower().strip() for marker in any_of or []],
            "contradiction_json_shape": json_shape,
            "confirmation_requirement": confirmation_requirement,
        })
    return validated


def _finding_validation_contradictions(
    candidate: Dict[str, Any], evidence_artifacts: List[str]
) -> List[str]:
    """Return catalog rule IDs contradicted by every cited confirmation artifact."""

    if not evidence_artifacts:
        return []

    claim_text = " ".join(
        str(candidate.get(field) or "") for field in ("title", "claim", "technique")
    ).lower()
    artifact_texts = []
    for reference in evidence_artifacts:
        try:
            artifact_texts.append(Path(_artifact_path_from_ref(reference)).read_text(encoding="utf-8", errors="replace").lower())
        except (OSError, ValueError):
            return []
    contradictions = []
    for rule in _load_finding_validation_guards():
        if not any(term in claim_text for term in rule["claim_terms"]):
            continue
        markers_all = rule["contradiction_all_of"]
        markers_any = rule["contradiction_any_of"]
        json_shape = rule["contradiction_json_shape"]
        if not markers_all and not markers_any and not json_shape:
            continue
        if json_shape == "flat_json_object":
            matches = all(_is_flat_json_object(text) for text in artifact_texts)
        else:
            matches = all(
                all(marker in text for marker in markers_all)
                if markers_all
                else any(marker in text for marker in markers_any)
                for text in artifact_texts
            )
        if matches:
            contradictions.append(rule["id"])
    return contradictions


def _is_flat_json_object(text: str) -> bool:
    """Return whether text is a JSON object whose values contain no nested objects or arrays."""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and bool(payload) and all(
        not isinstance(value, (dict, list)) for value in payload.values()
    )


def _finding_confirmation_requirements(candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return opt-in positive confirmation requirements matched by a finding claim."""

    claim_text = " ".join(
        str(candidate.get(field) or "") for field in ("title", "claim", "technique")
    ).lower()
    requirements = []
    for rule in _load_finding_validation_guards():
        requirement = rule.get("confirmation_requirement")
        if requirement and any(term in claim_text for term in rule["claim_terms"]):
            requirements.append({"id": rule["id"], "kind": str(requirement)})
    return requirements


def _frozen_finding_confirmation_requirements(candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the candidate's immutable confirmation requirements, with a legacy fallback."""

    packet = candidate.get("verification_packet")
    frozen = packet.get("confirmation_requirements") if isinstance(packet, dict) else None
    if isinstance(frozen, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"].strip()
        and isinstance(item.get("kind"), str)
        and item["kind"].strip()
        for item in frozen
    ):
        return [
            {"id": str(item["id"]), "kind": str(item["kind"])}
            for item in frozen
        ]
    return _finding_confirmation_requirements(candidate)


def _load_finding_validation_binding(
    store: Any,
    operation_id: str,
    finding_uid: str,
    verification_task_uid: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """Load one finding only when its persisted task ownership is exact."""

    record = store.get_finding(operation_id, finding_uid)
    if not record:
        raise ValueError("Unknown finding_uid for the current operation")
    candidate_data = record.get("candidate_data")
    candidate = candidate_data if isinstance(candidate_data, dict) else {}

    stored_task_uid = str(record.get("verification_task_uid") or "").strip()
    candidate_uid = str(candidate.get("finding_uid") or "").strip()
    packet = candidate.get("verification_packet")
    packet_uid = str(packet.get("finding_uid") or "").strip() if isinstance(packet, dict) else ""
    if (
        (stored_task_uid and stored_task_uid != verification_task_uid)
        or (candidate_uid and candidate_uid != finding_uid)
        or (packet_uid and packet_uid != finding_uid)
    ):
        raise ValueError(
            "Finding validation binding mismatch: "
            f"requested_finding_uid={finding_uid}, stored_finding_uid={candidate_uid or finding_uid}, "
            f"packet_finding_uid={packet_uid or '<legacy>'}, "
            f"expected_verification_task_uid={verification_task_uid}, "
            f"stored_verification_task_uid={stored_task_uid or '<missing>'}"
        )
    return record, candidate, _frozen_finding_confirmation_requirements(candidate)


def finding_validation_manifest_schema(requirements: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the model-facing JSON shape for the candidate's required checks."""

    checks: Dict[str, Any] = {}
    for requirement in requirements:
        rule_id = str(requirement["id"])
        kind = str(requirement["kind"])
        if kind == "response_comparison":
            checks[rule_id] = {
                "known_existing_artifact": "artifact:<operation-local response artifact>",
                "known_nonexistent_artifact": "artifact:<operation-local response artifact>",
            }
        elif kind == "rate_limit_probe":
            checks[rule_id] = {
                "attempts": [
                    {
                        "sequence": 1,
                        "response_artifact": "artifact:<operation-local response artifact>",
                    }
                ],
            }
        elif kind == "secret_exposure_revalidation":
            checks[rule_id] = {
                "reexposure_artifact": "artifact:<fresh operation-local exposure artifact>",
            }
    return {"checks": checks}


def _confirmation_manifest_error(message: str, requirements: List[Dict[str, Any]]) -> ValueError:
    """Return a schema error that gives the executor a complete repair target."""

    shape = json.dumps(finding_validation_manifest_schema(requirements), sort_keys=True)
    return ValueError(f"{message}. Expected validation_manifest JSON shape: {shape}")


def _required_manifest_reference(
    check: Dict[str, Any],
    field_name: str,
    requirements: List[Dict[str, Any]],
) -> str:
    """Return one non-empty manifest artifact reference with actionable schema errors."""

    reference = check.get(field_name)
    if not isinstance(reference, str) or not reference.strip():
        raise _confirmation_manifest_error(
            f"validation_manifest is missing required field: {field_name}", requirements
        )
    return reference.strip()


def _resolve_confirmation_artifact_reference(
    reference: str,
    requirements: List[Dict[str, Any]],
    field_name: str,
) -> str:
    """Resolve a manifest artifact reference or give the executor its repair target."""

    try:
        return _validated_artifact_paths([reference], require_one=True)[0]
    except ValueError as error:
        raise _confirmation_manifest_error(
            f"validation_manifest {field_name} reference is invalid: {error}", requirements
        ) from error


def _read_json_artifact(reference: str, requirements: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    canonical = _resolve_confirmation_artifact_reference(reference, requirements, "reference")
    try:
        payload = json.loads(Path(_artifact_path_from_ref(canonical)).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _confirmation_manifest_error(
            f"validation_manifest must be a readable JSON artifact: {error}", requirements
        ) from error
    if not isinstance(payload, dict):
        raise _confirmation_manifest_error("validation_manifest must be a JSON object", requirements)
    return canonical, payload


def _response_signature(reference: str, requirements: List[Dict[str, Any]]) -> Dict[str, str]:
    """Derive a stable response signature from one resolved response artifact."""

    canonical = _resolve_confirmation_artifact_reference(reference, requirements, "response artifact")
    try:
        text = Path(_artifact_path_from_ref(canonical)).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise _confirmation_manifest_error(
            f"validation response artifact is unreadable: {canonical}: {error}", requirements
        ) from error
    status_match = re.search(r"\bHTTP(?:/\d(?:\.\d)?)?\s+(\d{3})\b|\bstatus(?:_code)?\s*[:=]\s*(\d{3})\b", text, re.I)
    status = next((value for value in status_match.groups() if value), "") if status_match else ""
    body = re.split(r"\r?\n\r?\n", text, maxsplit=1)[-1]
    return {
        "artifact": canonical,
        "status": status,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def _validate_confirmation_manifest(candidate: Dict[str, Any], reference: str) -> Dict[str, Any]:
    """Validate narrow, artifact-derived positive predicates for matched claim families."""

    requirements = _frozen_finding_confirmation_requirements(candidate)
    manifest_ref, manifest = _read_json_artifact(reference, requirements)
    checks = manifest.get("checks")
    if not isinstance(checks, dict):
        raise _confirmation_manifest_error("validation_manifest must contain a checks object", requirements)
    derived: Dict[str, Any] = {}
    for requirement in requirements:
        rule_id = requirement["id"]
        check = checks.get(rule_id)
        if not isinstance(check, dict):
            raise _confirmation_manifest_error(
                f"validation_manifest is missing required check: checks.{rule_id}", requirements
            )
        if requirement["kind"] == "response_comparison":
            existing = _response_signature(
                _required_manifest_reference(check, "known_existing_artifact", requirements), requirements
            )
            nonexistent = _response_signature(
                _required_manifest_reference(check, "known_nonexistent_artifact", requirements), requirements
            )
            if (existing["status"], existing["body_sha256"]) == (
                nonexistent["status"], nonexistent["body_sha256"],
            ):
                raise ValueError("user_enumeration confirmation requires materially different response signatures")
            derived[rule_id] = {"known_existing": existing, "known_nonexistent": nonexistent}
        elif requirement["kind"] == "rate_limit_probe":
            attempts = check.get("attempts")
            if not isinstance(attempts, list) or len(attempts) < 10:
                raise _confirmation_manifest_error(
                    "lack_of_rate_limiting confirmation requires at least 10 recorded attempts", requirements
                )
            normalized_attempts = []
            for expected_index, attempt in enumerate(attempts, 1):
                sequence = attempt.get("sequence") if isinstance(attempt, dict) else None
                if (
                    not isinstance(sequence, int)
                    or isinstance(sequence, bool)
                    or sequence != expected_index
                ):
                    raise _confirmation_manifest_error(
                        "rate-limit attempts must be ordered sequentially from 1", requirements
                    )
                signature = _response_signature(
                    _required_manifest_reference(attempt, "response_artifact", requirements), requirements
                )
                try:
                    artifact_text = Path(_artifact_path_from_ref(signature["artifact"])).read_text(
                        encoding="utf-8", errors="replace"
                    ).lower()
                except OSError as error:
                    raise _confirmation_manifest_error(
                        f"validation response artifact is unreadable: {signature['artifact']}: {error}", requirements
                    ) from error
                if signature["status"] == "429" or any(
                    marker in artifact_text for marker in ("too many requests", "rate limit", "account locked", "locked out")
                ):
                    raise ValueError("rate-limit probe contains throttling or lockout evidence")
                normalized_attempts.append(signature)
            derived[rule_id] = {"attempt_count": len(normalized_attempts), "attempts": normalized_attempts}
        elif requirement["kind"] == "secret_exposure_revalidation":
            reexposure_ref = _required_manifest_reference(check, "reexposure_artifact", requirements)
            canonical_reexposure_ref = _resolve_confirmation_artifact_reference(
                reexposure_ref, requirements, "reexposure_artifact"
            )
            candidate_assertions = list(candidate.get("evidence_assertions") or [])
            secret_assertions = [
                {**assertion, "artifact": canonical_reexposure_ref}
                for assertion in candidate_assertions
                if isinstance(assertion, dict) and assertion.get("type") == "secret_exposure"
            ]
            if not secret_assertions or not all(
                _assertion_matches_artifact(assertion, canonical_reexposure_ref)
                for assertion in secret_assertions
            ):
                raise _confirmation_manifest_error(
                    "secret exposure revalidation requires the same exposure in a fresh artifact", requirements
                )
            derived[rule_id] = {"reexposure_artifact": canonical_reexposure_ref}
    return {
        "manifest": manifest_ref,
        "requirements": requirements,
        "derived": derived,
    }


@tool(
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "finding_uid": {"type": "string", "description": "Finding candidate identifier."},
                "outcome": {
                    "type": "string",
                    "enum": ["confirmed", "not_confirmed"],
                    "description": "Validation outcome.",
                },
                "summary": {"type": "string", "description": "Validation summary."},
                "reproduction_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Steps used to validate the finding.",
                },
                "evidence_strategy": {
                    "type": "string",
                    "enum": ["direct", "differential"],
                    "default": "direct",
                    "description": "Evidence strategy used for validation.",
                },
                "evidence_artifacts": {"type": "array", "items": {"type": "string"}, "default": None},
                "control_artifacts": {"type": "array", "items": {"type": "string"}, "default": None},
                "validation_manifest": {
                    "type": "string",
                    "description": (
                        "JSON artifact with claim-specific validation evidence when required. Its required "
                        "checks shape is supplied by the bound finding-validation tool and task context."
                    ),
                },
            },
            "required": ["finding_uid", "outcome", "summary", "reproduction_steps"],
        }
    }
)
def record_finding_validation(
    finding_uid: str,
    outcome: str,
    summary: str,
    reproduction_steps: List[str],
    evidence_strategy: str = "direct",
    evidence_artifacts: Optional[List[str]] = None,
    control_artifacts: Optional[List[str]] = None,
    evidence_assertions: Optional[List[Dict[str, str]]] = None,
    validation_manifest: Optional[str] = None,
    *,
    expected_verification_task_uid: Optional[str] = None,
) -> str:
    """Record a validation outcome, deterministically re-proving candidate evidence for confirmations."""

    _validate_finding_validation_input_shape(
        reproduction_steps,
        evidence_artifacts,
        control_artifacts,
        validation_manifest,
    )

    op_id = _operation_id()
    store = _get_database_store()
    provisional_record = store.get_finding(op_id, finding_uid)
    if not provisional_record:
        raise ValueError("Unknown finding_uid for the current operation")
    expected_task_uid = str(expected_verification_task_uid or provisional_record["verification_task_uid"] or "").strip()
    active = [task for task in store.get_tasks(op_id) if task.status == "active"]
    if (
        not expected_task_uid
        or expected_task_uid != provisional_record["verification_task_uid"]
        or len(active) != 1
        or active[0].task_uid != expected_task_uid
    ):
        raise ValueError("Finding validation may only be recorded by its active verification task")

    normalized_outcome = _normalize_finding_validation_outcome(outcome)
    if normalized_outcome not in {"confirmed", "not_confirmed"}:
        raise ValueError("outcome must be confirmed or not_confirmed")

    strategy = _normalize_evidence_strategy(evidence_strategy)
    if strategy not in {"direct", "differential"}:
        raise ValueError("evidence_strategy must be direct or differential")

    evidence = _validated_artifact_paths(
        evidence_artifacts,
        require_one=normalized_outcome == "confirmed",
        allow_delimited_strings=True,
    )
    controls = _validated_artifact_paths(control_artifacts, allow_delimited_strings=True)
    if normalized_outcome == "confirmed" and strategy == "differential" and not controls:
        raise ValueError("Differential confirmation requires at least one negative-control artifact")
    record, candidate, requirements = _load_finding_validation_binding(
        store, op_id, finding_uid, expected_task_uid
    )
    manifest_attestation: Dict[str, Any] = {}
    if normalized_outcome == "confirmed":
        if requirements:
            if not validation_manifest:
                required = ", ".join(item["id"] for item in requirements)
                raise ValueError(f"confirmed validation requires validation_manifest for: {required}")
            manifest_attestation = _validate_confirmation_manifest(
                candidate, validation_manifest
            )
            evidence = list(dict.fromkeys([*evidence, manifest_attestation["manifest"]]))
        candidate_assertions = candidate.get("evidence_assertions")
        assertions = []
        for candidate_assertion in candidate_assertions or []:
            if not isinstance(candidate_assertion, dict):
                raise ValueError("finding candidate has no valid positive evidence assertions")
            matching_artifact = next(
                (
                    reference
                    for reference in evidence
                    if _assertion_matches_artifact(candidate_assertion, reference)
                ),
                None,
            )
            if matching_artifact is None:
                raise ValueError(
                    "confirmed validation evidence did not reproduce candidate assertion: "
                    + _canonical_assertion_predicate(candidate_assertion)
                )
            reproduced = dict(candidate_assertion)
            reproduced["artifact"] = matching_artifact
            assertions.append(reproduced)
        if not _matching_evidence_assertions(
            candidate_assertions,
            assertions,
            evidence,
            _artifact_fingerprints(evidence),
        ):
            raise ValueError("confirmed validation assertions must re-prove every candidate evidence assertion")
        contradictions = _finding_validation_contradictions(record.get("candidate_data") or {}, evidence)
        if contradictions:
            raise ValueError(
                "confirmed finding validation is contradicted by cited evidence: " + ", ".join(contradictions)
            )
    else:
        assertions = []

    validation = {
        "finding_uid": finding_uid,
        "outcome": normalized_outcome,
        "summary": _clean_memory_text(summary, "summary"),
        "reproduction_steps": [_clean_memory_text(step, "reproduction step") for step in reproduction_steps],
        "evidence_strategy": strategy,
        "evidence_artifacts": evidence,
        "control_artifacts": controls,
        "evidence_assertions": assertions,
        "evidence_artifact_fingerprints": _artifact_fingerprints(evidence),
        "validation_manifest_attestation": manifest_attestation,
        "validation_status": "submitted",
    }
    if not validation["reproduction_steps"]:
        raise ValueError("reproduction_steps requires at least one step")
    _store_memory_entry(validation["summary"], "finding_validation", validation)
    store.store_finding_validation(op_id, finding_uid, validation)
    task = active[0]
    criterion = task.acceptance.criteria[0]
    acceptance_evidence = list(dict.fromkeys([*evidence, *controls, f"finding:{finding_uid}"]))
    acceptance = AcceptanceResult(
        criterion_id=criterion.id,
        status="satisfied" if normalized_outcome == "confirmed" else "assessed_negative",
        disposition="existing_finding" if normalized_outcome == "confirmed" else "no_vulnerability",
        summary=validation["summary"],
        evidence_refs=tuple(acceptance_evidence),
    )
    acceptance_response = json.loads(_record_task_acceptance(task.task_uid, [acceptance]))
    return json.dumps(
        {
            "complete": True,
            "finding_uid": finding_uid,
            "outcome": normalized_outcome,
            "acceptance": acceptance_response,
        },
        sort_keys=True,
    )


def build_record_finding_validation_tool(task: Task) -> Any:
    """Bind finding validation to the candidate assigned to one verification task."""

    if task.kind != "finding_validation" or not task.reference_id:
        raise ValueError("record_finding_validation requires a bound finding-validation task")
    finding_uid = task.reference_id
    store = _get_database_store()
    record = store.get_finding(_operation_id(), finding_uid)
    if record is None:
        requirements: List[Dict[str, Any]] = []
        logger.warning(
            "Finding validation tool built before its candidate was available task=%s finding=%s",
            task.task_uid,
            finding_uid,
        )
    else:
        _record, _candidate, requirements = _load_finding_validation_binding(
            store, _operation_id(), finding_uid, task.task_uid
        )
    logger.info(
        "Bound finding validation tool task=%s finding=%s requirements=%s",
        task.task_uid,
        finding_uid,
        ",".join(str(item["id"]) for item in requirements) or "none",
    )
    requirement_ids = [str(item["id"]) for item in requirements]
    manifest_description = "JSON artifact reference with claim-specific validation evidence."
    if requirement_ids:
        manifest_shape = json.dumps(finding_validation_manifest_schema(requirements), sort_keys=True)
        manifest_description = (
            "Required for confirmed outcomes. Provide a previously written artifact reference, never inline JSON. "
            f"It must contain these required checks: {', '.join(requirement_ids)}. "
            f"Manifest JSON shape: {manifest_shape}"
        )

    def record_bound_finding_validation(
        outcome: str,
        summary: str,
        reproduction_steps: List[str],
        evidence_strategy: str = "direct",
        evidence_artifacts: Optional[List[str]] = None,
        control_artifacts: Optional[List[str]] = None,
        validation_manifest: Optional[str] = None,
    ) -> str:
        return record_finding_validation(
            finding_uid,
            outcome,
            summary,
            reproduction_steps,
            evidence_strategy,
            evidence_artifacts,
            control_artifacts,
            None,
            validation_manifest,
            expected_verification_task_uid=task.task_uid,
        )

    record_bound_finding_validation.__name__ = "record_finding_validation"
    record_bound_finding_validation.__doc__ = """Record the independent outcome for this assigned finding candidate.

The controller binds this tool to the only finding and verification task assigned to this task. Supply fresh evidence
artifacts and an outcome; Python re-proves the candidate's immutable markers and records frozen task acceptance.
For confirmed outcomes, provide any required validation manifest as an existing artifact reference, never inline JSON.
"""
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": ["confirmed", "not_confirmed"]},
            "summary": {"type": "string"},
            "reproduction_steps": {"type": "array", "items": {"type": "string"}},
            "evidence_strategy": {"type": "string", "enum": ["direct", "differential"]},
            "evidence_artifacts": {"type": "array", "items": {"type": "string"}},
            "control_artifacts": {"type": "array", "items": {"type": "string"}},
            "validation_manifest": {"type": "string", "description": manifest_description},
        },
        "required": ["outcome", "summary", "reproduction_steps"],
    }
    if requirement_ids:
        input_schema["allOf"] = [{
            "if": {"properties": {"outcome": {"const": "confirmed"}}, "required": ["outcome"]},
            "then": {"required": ["validation_manifest"]},
        }]
    return tool(
        record_bound_finding_validation,
        inputSchema={"json": input_schema},
    )


def finalize_finding_validation(task: Task, evaluator_status: str, evaluator_reason: str) -> Optional[str]:
    """Materialize the evaluator-approved resolution for a verification task."""

    if task.kind != "finding_validation" or not task.reference_id:
        return None
    op_id = _operation_id()
    store = _get_database_store()
    record = store.get_finding(op_id, task.reference_id)
    if not record or record.get("resolution"):
        return record.get("resolution") if record else None
    record, candidate, requirements = _load_finding_validation_binding(
        store, op_id, task.reference_id, task.task_uid
    )
    validation = record.get("validation_data")
    confirmed = (
        evaluator_status == "done"
        and validation
        and validation.get("outcome") == "confirmed"
        and _matching_evidence_assertions(
            candidate.get("evidence_assertions"),
            validation.get("evidence_assertions"),
            validation.get("evidence_artifacts", []),
            validation.get("evidence_artifact_fingerprints"),
        )
        and (
            not requirements
            or bool(validation.get("validation_manifest_attestation"))
        )
    )
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
                "candidate_evidence_assertions": candidate["evidence_assertions"],
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


def finding_validation_submitted(task: Task) -> bool:
    """Return whether a verification task has durable validation data."""

    if task.kind != "finding_validation" or not task.reference_id:
        return True
    record = _get_database_store().get_finding(_operation_id(), task.reference_id)
    return bool(record and record.get("validation_data"))


def finding_validation_outcome(task: Task) -> Optional[str]:
    """Return the durable outcome for a submitted finding-validation task."""

    if task.kind != "finding_validation" or not task.reference_id:
        return None
    record = _get_database_store().get_finding(_operation_id(), task.reference_id)
    validation = record.get("validation_data") if record else None
    return str(validation.get("outcome")) if isinstance(validation, dict) else None


def _objective_candidate_fingerprint(objective_type: str, candidate_value: str) -> str:
    normalized = f"{objective_type.strip().lower()}|{candidate_value.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _objective_constraints(objective_type: str) -> Dict[str, Any]:
    """Extract deterministic constraints for supported objective types from the operation plan."""

    plan = _get_database_store().get_plan(_operation_id())
    objective = plan.objective if plan else ""
    constraints: Dict[str, Any] = {}
    if objective_type == "flag":
        length_match = re.search(r"\blength\s+(?:is\s+)?(\d+)\b", objective, flags=re.IGNORECASE)
        if length_match:
            constraints["exact_length"] = int(length_match.group(1))
        format_match = re.search(
            r"\bflag\s+format\s+(?:is|:)\s*[:=]?\s*([^\s,;]+)",
            objective,
            flags=re.IGNORECASE,
        )
        if format_match:
            constraints["format_template"] = format_match.group(1).rstrip(".")
    return constraints


def _objective_constraint_failures(candidate_value: str, constraints: Dict[str, Any]) -> List[str]:
    failures = []
    exact_length = constraints.get("exact_length")
    if isinstance(exact_length, int) and len(candidate_value) != exact_length:
        failures.append(f"candidate length {len(candidate_value)} does not equal required length {exact_length}")
    template = str(constraints.get("format_template") or "").strip()
    if template:
        pattern = re.escape(template).replace(r"\.\.\.", ".+")
        if re.fullmatch(pattern, candidate_value) is None:
            failures.append(f"candidate does not match required format {template}")
    return failures


def _objective_evidence_contains_candidate(candidate_value: str, evidence_artifacts: List[str]) -> bool:
    """Return whether an objective candidate appears in at least one bounded text artifact."""

    for artifact_ref in evidence_artifacts:
        try:
            with open(_artifact_path_from_ref(artifact_ref), "r", encoding="utf-8", errors="replace") as artifact_file:
                if candidate_value in artifact_file.read(1_000_000):
                    return True
        except OSError:
            continue
    return False


@tool(
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "objective_type": {
                    "type": "string",
                    "enum": ["flag"],
                    "description": "Canonical objective result type.",
                },
                "candidate_value": {"type": "string", "description": "Exact candidate value."},
                "summary": {"type": "string", "description": "How the candidate was obtained."},
                "reproduction_steps": {"type": "array", "items": {"type": "string"}},
                "evidence_artifacts": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "objective_type",
                "candidate_value",
                "summary",
                "reproduction_steps",
                "evidence_artifacts",
            ],
        }
    }
)
def store_objective_candidate(
    objective_type: str,
    candidate_value: str,
    summary: str,
    reproduction_steps: List[str],
    evidence_artifacts: List[str],
) -> str:
    """Store an operation-objective candidate and create an independent validation task."""

    normalized_type = str(objective_type or "").strip().lower()
    if normalized_type != "flag":
        raise ValueError("objective_type must be flag")
    value = _clean_memory_text(candidate_value, "candidate_value")
    steps = [_clean_memory_text(step, "reproduction step") for step in reproduction_steps]
    if not steps:
        raise ValueError("reproduction_steps requires at least one step")
    artifacts = _validated_artifact_paths(evidence_artifacts, require_one=True, allow_delimited_strings=True)
    fingerprint = _objective_candidate_fingerprint(normalized_type, value)
    op_id = _operation_id()
    store = _get_database_store()
    existing = store.get_objective_candidate_by_fingerprint(op_id, fingerprint)
    if existing:
        return json.dumps(
            {
                "candidate_ref": f"objective_candidate:{existing['candidate_uid']}",
                "candidate_uid": existing["candidate_uid"],
                "status": existing.get("resolution") or "pending_validation",
                "verification_task_ref": f"task:{existing['verification_task_uid']}",
                "verification_task_uid": existing["verification_task_uid"],
            },
            sort_keys=True,
        )

    constraints = _objective_constraints(normalized_type)
    constraint_failures = _objective_constraint_failures(value, constraints)
    if constraint_failures:
        raise ValueError(
            "candidate_value does not satisfy objective constraints: " + "; ".join(constraint_failures)
        )
    if not _objective_evidence_contains_candidate(value, artifacts):
        raise ValueError("candidate_value does not appear in the supplied evidence_artifacts")

    candidate_uid = str(uuid.uuid4())
    task_uid = str(uuid.uuid4())
    candidate = {
        "candidate_uid": candidate_uid,
        "validation_type": "objective",
        "objective_type": normalized_type,
        "candidate_value": value,
        "summary": _clean_memory_text(summary, "summary"),
        "reproduction_steps": steps,
        "artifacts": artifacts,
        "constraints": constraints,
        "validation_status": "pending",
    }
    _store_memory_entry(candidate["summary"], "objective_candidate", candidate)
    current_phase = _get_plan_current_phase()
    task = Task(
        task_uid=task_uid,
        title=f"Validate {normalized_type} objective candidate",
        objective=(
            f"Independently validate objective candidate {candidate_uid}. Confirm that the exact value appears in "
            "the supplied artifact, is reproducibly extracted, satisfies the recorded objective constraints, and "
            "call record_objective_validation with the outcome."
        ),
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="snapshot",
                description=f"Objective candidate {candidate_uid}",
                source_refs=artifacts,
            ),
            criteria=[
                AcceptanceCriterion(
                    id=f"validate-objective:{candidate_uid}",
                    description="Record an independent, evidence-backed objective validation outcome.",
                    evidence_requirements=[EvidenceRequirement(kind="artifact", min_count=1)],
                )
            ],
        ),
        evidence=artifacts,
        phase=current_phase,
        status="pending",
        kind="objective_validation",
        reference_id=candidate_uid,
    )
    store.store_objective_candidate(op_id, candidate_uid, fingerprint, candidate, task_uid)
    _ensure_memory_client().store_task(task=task, user_id=_user_id())
    return json.dumps(
        {
            "candidate_ref": f"objective_candidate:{candidate_uid}",
            "candidate_uid": candidate_uid,
            "status": "pending_validation",
            "verification_task_ref": f"task:{task_uid}",
            "verification_task_uid": task_uid,
        },
        sort_keys=True,
    )


@tool(
    inputSchema={
        "json": {
            "type": "object",
            "properties": {
                "candidate_uid": {"type": "string"},
                "outcome": {"type": "string", "enum": ["confirmed", "rejected", "inconclusive"]},
                "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                "summary": {"type": "string"},
                "evidence_artifacts": {"type": "array", "items": {"type": "string"}},
                "validator": {"type": "string"},
            },
            "required": ["candidate_uid", "outcome", "confidence", "summary", "evidence_artifacts", "validator"],
        }
    }
)
def record_objective_validation(
    candidate_uid: str,
    outcome: str,
    confidence: int,
    summary: str,
    evidence_artifacts: List[str],
    validator: str,
) -> str:
    """Record the result of the active objective-validation task without changing finding status."""

    op_id = _operation_id()
    store = _get_database_store()
    record = store.get_objective_candidate(op_id, candidate_uid)
    if not record:
        raise ValueError("Unknown objective candidate for the current operation")
    active = [task for task in store.get_tasks(op_id) if task.status == "active"]
    if len(active) != 1 or active[0].task_uid != record["verification_task_uid"]:
        raise ValueError("Objective validation may only be recorded by its active verification task")
    normalized_outcome = _normalize_objective_validation_outcome(outcome)
    if normalized_outcome not in {"confirmed", "rejected", "inconclusive"}:
        raise ValueError("outcome must be confirmed, rejected, or inconclusive")
    if isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise ValueError("confidence must be an integer from 0 through 100")
    evidence = _validated_artifact_paths(evidence_artifacts, require_one=True, allow_delimited_strings=True)
    candidate = record["candidate_data"]
    failures = _objective_constraint_failures(candidate["candidate_value"], candidate.get("constraints", {}))
    if normalized_outcome == "confirmed" and not _objective_evidence_contains_candidate(
        candidate["candidate_value"], evidence
    ):
        failures.append("candidate value does not appear in the supplied evidence artifacts")
    if normalized_outcome == "confirmed" and confidence < 80:
        failures.append("confirmed objective confidence must be at least 80")
    effective_outcome = "rejected" if failures else normalized_outcome
    clean_summary = _clean_memory_text(summary, "summary")
    if failures:
        clean_summary = f"{clean_summary} Deterministic checks: {'; '.join(failures)}."
    validation = {
        "candidate_uid": candidate_uid,
        "validation_type": "objective",
        "objective_type": candidate["objective_type"],
        "outcome": effective_outcome,
        "confidence": confidence,
        "summary": clean_summary,
        "evidence_artifacts": evidence,
        "validator": _clean_memory_text(validator, "validator"),
        "constraint_failures": failures,
        "validation_status": "submitted",
    }
    _store_memory_entry(clean_summary, "objective_validation", {**candidate, **validation})
    store.store_objective_validation(op_id, candidate_uid, validation)
    task = active[0]
    acceptance = AcceptanceResult(
        criterion_id=task.acceptance.criteria[0].id,
        status="satisfied" if effective_outcome == "confirmed" else "assessed_negative",
        disposition="observation",
        summary=clean_summary,
        evidence_refs=tuple(dict.fromkeys(evidence)),
    )
    acceptance_response = json.loads(_record_task_acceptance(task.task_uid, [acceptance]))
    return json.dumps(
        {
            "complete": True,
            "candidate_uid": candidate_uid,
            "requested_outcome": normalized_outcome,
            "outcome": effective_outcome,
            "confidence": confidence,
            "acceptance": acceptance_response,
        },
        sort_keys=True,
    )


def build_record_objective_validation_tool(task: Task) -> Any:
    """Bind objective validation to the exact candidate owned by one task."""

    if task.kind != "objective_validation" or not task.reference_id:
        raise ValueError("record_objective_validation requires a bound objective-validation task")
    candidate_uid = task.reference_id
    record = _get_database_store().get_objective_candidate(_operation_id(), candidate_uid)
    if record is None:
        logger.warning(
            "Objective validation tool built before its candidate was available task=%s candidate=%s",
            task.task_uid,
            candidate_uid,
        )
    elif str(record.get("verification_task_uid") or "") != task.task_uid:
        raise ValueError(
            "Objective validation binding mismatch: "
            f"candidate_uid={candidate_uid}, expected_verification_task_uid={task.task_uid}, "
            f"stored_verification_task_uid={str(record.get('verification_task_uid') or '')}"
        )
    logger.info("Bound objective validation tool task=%s candidate=%s", task.task_uid, candidate_uid)

    def record_bound_objective_validation(
        outcome: str,
        confidence: int,
        summary: str,
        evidence_artifacts: List[str],
        validator: str,
    ) -> str:
        return record_objective_validation(
            candidate_uid,
            outcome,
            confidence,
            summary,
            evidence_artifacts,
            validator,
        )

    record_bound_objective_validation.__name__ = "record_objective_validation"
    record_bound_objective_validation.__doc__ = (
        "Record the outcome for this controller-bound objective candidate. "
        "The candidate identifier is owned by the controller and is not an input."
    )
    return tool(
        record_bound_objective_validation,
        inputSchema={
            "json": {
                "type": "object",
                "properties": {
                    "outcome": {"type": "string", "enum": ["confirmed", "rejected", "inconclusive"]},
                    "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                    "summary": {"type": "string"},
                    "evidence_artifacts": {"type": "array", "items": {"type": "string"}},
                    "validator": {"type": "string"},
                },
                "required": ["outcome", "confidence", "summary", "evidence_artifacts", "validator"],
            }
        },
    )


def objective_validation_submitted(task: Task) -> bool:
    if task.kind != "objective_validation" or not task.reference_id:
        return True
    record = _get_database_store().get_objective_candidate(_operation_id(), task.reference_id)
    return bool(record and record.get("validation_data"))


def objective_validation_outcome(task: Task) -> Optional[str]:
    if task.kind != "objective_validation" or not task.reference_id:
        return None
    record = _get_database_store().get_objective_candidate(_operation_id(), task.reference_id)
    validation = record.get("validation_data") if record else None
    return str(validation.get("outcome")) if isinstance(validation, dict) else None


def finalize_objective_validation(task: Task, evaluator_status: str, evaluator_reason: str) -> Optional[str]:
    if task.kind != "objective_validation" or not task.reference_id:
        return None
    op_id = _operation_id()
    store = _get_database_store()
    record = store.get_objective_candidate(op_id, task.reference_id)
    if not record or record.get("resolution"):
        return record.get("resolution") if record else None
    candidate = record["candidate_data"]
    validation = record.get("validation_data")
    confirmed = evaluator_status == "done" and validation and validation.get("outcome") == "confirmed"
    resolution = "objective_verified" if confirmed else "objective_rejected"
    category = "objective_result" if confirmed else "objective_validation_failure"
    reason = evaluator_reason or (validation.get("summary") if validation else "Objective validation was incomplete")
    metadata = dict(candidate)
    if validation:
        metadata.update(validation)
    metadata.update(
        {
            "category": category,
            "validation_type": "objective",
            "validation_status": "verified" if confirmed else "failed",
            "validation_reason": reason,
        }
    )
    _store_memory_entry(candidate["candidate_value"], category, metadata)
    store.resolve_objective_candidate(op_id, task.reference_id, resolution)
    if not confirmed:
        follow_up_reference = f"objective-search:{candidate['objective_type']}"
        existing_follow_up = next(
            (
                existing
                for existing in store.get_tasks(op_id)
                if existing.reference_id == follow_up_reference and existing.status in {"active", "pending"}
            ),
            None,
        )
        if existing_follow_up is None:
            evidence = list(
                dict.fromkeys(
                    (validation or {}).get("evidence_artifacts", [])
                    or candidate.get("artifacts", [])
                )
            )
            follow_up = Task(
                task_uid=str(uuid.uuid4()),
                title=f"Continue {candidate['objective_type']} retrieval after rejected candidate",
                objective=(
                    f"Find a different {candidate['objective_type']} candidate that satisfies the operation objective. "
                    f"Do not resubmit rejected candidate {task.reference_id}; preserve its rejection evidence and test "
                    "a changed retrieval hypothesis."
                ),
                acceptance=AcceptanceContract(
                    mode="outcome",
                    basis=AcceptanceBasis(
                        kind="snapshot",
                        description=f"Rejected {candidate['objective_type']} candidate {task.reference_id}",
                        source_refs=evidence or [f"task:{task.task_uid}"],
                    ),
                    criteria=[AcceptanceCriterion(
                        id=f"continue-objective-search:{candidate['objective_type']}",
                        description="Record a different objective candidate or an evidence-backed terminal constraint.",
                        evidence_requirements=[EvidenceRequirement(kind="durable_evidence", min_count=1)],
                    )],
                ),
                evidence=evidence,
                phase=task.phase,
                status="pending",
                kind="standard",
                reference_id=follow_up_reference,
                target_scope=task.target_scope,
                target_ids=task.target_ids,
            )
            _ensure_memory_client().store_task(task=follow_up, user_id=_user_id())
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


class _StrictTaskWireModel(BaseModel):
    """Closed model-facing schema for task creation payloads."""

    model_config = ConfigDict(extra="forbid")


class TaskProposalLimits(_StrictTaskWireModel):
    max_duration_minutes: Optional[PositiveInt] = None
    max_requests: Optional[PositiveInt] = None
    max_items: Optional[PositiveInt] = None
    max_depth: Optional[PositiveInt] = None
    description: Optional[str] = Field(default=None, description="Allowed but ignored model-facing field")


DEFAULT_TASK_PROPOSAL_LIMITS = {
    "max_requests": 50,
    "max_duration_minutes": 10,
}


class TaskProposalCriterion(_StrictTaskWireModel):
    description: str = Field(min_length=1, description="Finite result required from the declared basis")


class TaskProposal(_StrictTaskWireModel):
    """Small model-facing task proposal compiled into an immutable acceptance contract by Python."""

    title: str = Field(min_length=1)
    objective: str = Field(min_length=1, validation_alias=AliasChoices("objective", "description"))
    basis_description: Optional[str] = Field(default=None, description="Finite boundary; defaults to objective")
    methods: List[str] = Field(default_factory=list, description="Procedure methods; use [] for snapshot proposals")
    limits: TaskProposalLimits = Field(
        default_factory=lambda: TaskProposalLimits(**DEFAULT_TASK_PROPOSAL_LIMITS),
        validation_alias=AliasChoices("limits", "limit"),
        description="Optional procedure bounds; defaults to 50 requests and 10 minutes",
    )
    snapshot_refs: List[str] = Field(
        default_factory=list,
        description="Existing task, memory, artifact, or finding references; use [] for procedure proposals",
    )
    finding_refs: List[str] = Field(
        default_factory=list,
        description="Canonical persisted finding references required by finding-dependent work",
    )
    output_kind: ProcedureOutputKind = Field(default="artifact", description="Procedure deliverable type")
    criteria: List[TaskProposalCriterion] = Field(min_length=1, max_length=1)
    target_ids: List[str] = Field(default_factory=list)
    replacement_of: Optional[str] = Field(
        default=None,
        description="UID of a failed task whose acceptance work this task replaces",
    )
    supersedes_criteria: List[str] = Field(
        default_factory=list,
        description="Parent acceptance criterion IDs resolved by this replacement task",
    )
    workstream: Optional[str] = Field(
        default=None,
        description="Module-declared planning workstream when the active phase has a task contract",
    )
    task_role: str = Field(default="mapping", description="Planning role: mapping, synthesis, or direct_single_step")
    depends_on_workstreams: List[str] = Field(
        default_factory=list,
        description="Declared workstream prerequisites for a synthesis task",
    )
    inapplicability_reason: Optional[str] = Field(
        default=None,
        description="Concrete reason required for a direct single-step planning exception",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_inapplicable_snapshot_fields(cls, value: Any) -> Any:
        """Let an unambiguous snapshot reference own fields that cannot apply to it."""

        if not isinstance(value, dict) or not value.get("snapshot_refs"):
            normalized = dict(value) if isinstance(value, dict) else value
        else:
            normalized = dict(value)
        if not isinstance(normalized, dict):
            return normalized

        aliases = {
            "limit": "limits",
            "method": "methods",
            "methods_list": "methods",
            "snapshot_ref": "snapshot_refs",
            "criterion": "criteria",
            "target_id": "target_ids",
            "workstream_dependencies": "depends_on_workstreams",
        }
        for alias, canonical in aliases.items():
            if alias in normalized and canonical not in normalized:
                normalized[canonical] = normalized.pop(alias)
                logger.info("Normalized task proposal field alias %s -> %s", alias, canonical)

        # ``name`` is a common, unambiguous title alias. Preserve it long
        # enough to normalize older model responses instead of spending a
        # task-creator correction solely on a missing title.
        if not normalized.get("title") and isinstance(normalized.get("name"), str):
            normalized["title"] = normalized["name"].strip()
            logger.info("Normalized task proposal field alias name -> title")

        # Remove extra fields that models often add.
        for extra in ["name", "status", "work_type", "methods_description"]:
            if extra in normalized:
                normalized.pop(extra)

        if "description" in normalized:
            if "objective" not in normalized:
                normalized["objective"] = normalized["description"]
                logger.info("Normalized task proposal field alias description -> objective")
            # Only pop if objective is now present (it should be)
            if "objective" in normalized:
                normalized.pop("description")

        # Normalize a common local-model shorthand while keeping the canonical
        # wire representation an object.
        limits = normalized.get("limits")
        if isinstance(limits, list):
            logger.info("Normalizing task proposal limits -> dict")
            # For procedure proposals, a list (usually empty hallucination) should use defaults
            if not normalized.get("snapshot_refs"):
                normalized["limits"] = dict(DEFAULT_TASK_PROPOSAL_LIMITS)
            else:
                normalized["limits"] = {}
        elif isinstance(limits, int) and not isinstance(limits, bool):
            normalized["limits"] = {"max_requests": limits}
            logger.info("Normalized scalar task proposal limits -> max_requests")
        elif isinstance(limits, dict):
            # Remove common hallucinations in limits
            for extra_limit in ["discovery_procedure_limits", "scope"]:
                if extra_limit in limits:
                    limits.pop(extra_limit)
                    logger.info("Removed hallucinated limit field: %s", extra_limit)
            if not limits and normalized.get("methods") and not normalized.get("snapshot_refs"):
                normalized["limits"] = dict(DEFAULT_TASK_PROPOSAL_LIMITS)
                logger.info("Normalized empty procedure proposal limits to bounded defaults")

        # Handle criteria as list of strings
        criteria = normalized.get("criteria")
        if isinstance(criteria, list):
            new_criteria = []
            for item in criteria:
                if isinstance(item, str):
                    new_criteria.append({"description": item})
                else:
                    new_criteria.append(item)
            normalized["criteria"] = new_criteria

        output_aliases = {
            "report": "artifact",
            "evidence": "artifact",
            "vulnerability_report": "artifact",
            "inventory": "inventory_manifest",
            "manifest": "inventory_manifest",
        }
        output_kind = normalized.get("output_kind")
        if isinstance(output_kind, str):
            canonical_output_kind = output_aliases.get(output_kind.strip().lower())
            if canonical_output_kind is not None:
                normalized["output_kind"] = canonical_output_kind
                logger.info(
                    "Normalized task proposal output_kind alias %s -> %s",
                    output_kind,
                    canonical_output_kind,
                )

        if not normalized.get("snapshot_refs"):
            return normalized
        normalized.pop("limit", None)
        normalized["limits"] = {}
        if "output_kind" in normalized:
            normalized.pop("output_kind")
            logger.info("Normalized inapplicable output_kind from snapshot task proposal")

        return normalized

    @model_validator(mode="after")
    def validate_proposal(self) -> "TaskProposal":
        if not self.title.strip():
            raise ValueError("title required")
        if not self.objective.strip():
            raise ValueError("objective required")
        if self.basis_description is not None and not self.basis_description.strip():
            raise ValueError("basis_description must be non-empty when provided")
        if self.workstream is not None and not self.workstream.strip():
            raise ValueError("workstream must be non-empty when supplied")
        if self.task_role not in {"mapping", "synthesis", "direct_single_step"}:
            raise ValueError("task_role must be mapping, synthesis, or direct_single_step")
        if any(not item.strip() for item in self.depends_on_workstreams):
            raise ValueError("depends_on_workstreams must not contain empty values")
        if self.inapplicability_reason is not None and not self.inapplicability_reason.strip():
            raise ValueError("inapplicability_reason must be non-empty when supplied")
        snapshot_fields = bool(self.snapshot_refs)
        if self.methods and snapshot_fields:
            raise ValueError("proposal must not mix procedure and snapshot fields")
        if self.task_role == "synthesis" and self.methods:
            raise ValueError("controller-owned synthesis proposal must leave methods empty")
        if snapshot_fields:
            if self.output_kind != "artifact":
                raise ValueError("snapshot proposal must not set output_kind")
        else:
            if not self.methods and self.task_role != "synthesis":
                raise ValueError("proposal requires procedure methods or snapshot_refs")
            if not any(getattr(self.limits, key) is not None for key in DISCOVERY_PROCEDURE_LIMIT_KEYS):
                raise ValueError("procedure proposal requires at least one discovery procedure limit")
            moving_scope = " ".join(
                [
                    self.title,
                    self.objective,
                    self.effective_basis_description,
                    *(criterion.description for criterion in self.criteria),
                ]
            )
            consumes_inventory = re.search(
                r"\b(?:all|every|remaining)\s+(?:identified\s+)?"
                r"(?:endpoints?|parameters?|items?|workflows?|services?|technologies?)\b"
                r"[^.]{0,160}\b(?:inventory|manifest)\b|"
                r"\b(?:from|in|within)\s+(?:the\s+)?(?:initial\s+|frozen\s+)?"
                r"(?:endpoint\s+)?(?:inventory|manifest)\b",
                moving_scope,
                re.IGNORECASE,
            )
            if consumes_inventory and self.output_kind != "inventory_manifest":
                raise ValueError(
                    "procedure proposal cannot consume an inventory-wide moving collection; use canonical "
                    "snapshot_refs"
                )
        return self

    @property
    def inferred_basis_kind(self) -> AcceptanceBasisKind:
        return "snapshot" if self.snapshot_refs else "procedure"

    @property
    def effective_basis_description(self) -> str:
        return (self.basis_description or self.objective).strip()


_TASK_PROPOSAL_FIELD_CORRECTIONS = {
    "title": ('non-empty string', '"title":"Assess login"'),
    "objective": ('non-empty string', '"objective":"Test the assigned login route"'),
    "basis_description": ('non-empty string when supplied', '"basis_description":"Bounded login assessment"'),
    "methods": ('required array; non-empty for procedures and [] for snapshots', '"methods":["http testing"]'),
    "limits": ('required object; positive bounds for procedures and {} for snapshots', '"limits":{"max_requests":50}'),
    "snapshot_refs": (
        'required array; [] for procedures or canonical references for snapshots',
        '"snapshot_refs":["artifact:artifacts/inventory.json"]',
    ),
    "output_kind": ('artifact or inventory_manifest', '"output_kind":"artifact"'),
    "criteria": ('array containing exactly one description object', '"criteria":[{"description":"Store evidence"}]'),
    "target_ids": ('array of exact registered target IDs', '"target_ids":["target-1"]'),
    "replacement_of": ('existing failed task UID when replacing work', '"replacement_of":"task-uid"'),
    "supersedes_criteria": (
        'array of parent acceptance criterion IDs when replacing work',
        '"supersedes_criteria":["criterion-1"]',
    ),
    "workstream": ('module-declared non-empty workstream', '"workstream":"bounded_crawl"'),
    "task_role": ('mapping, synthesis, or direct_single_step', '"task_role":"mapping"'),
    "depends_on_workstreams": ('array of declared prerequisite workstreams', '"depends_on_workstreams":["bounded_crawl"]'),
    "inapplicability_reason": ('non-empty reason for direct_single_step only', '"inapplicability_reason":"flag is exposed by the root response"'),
}


def _compact_task_proposal_validation_error(error: ValidationError) -> str:
    """Return one compact correction contract covering every invalid proposal field."""

    diagnostics = []
    for detail in error.errors(include_url=False, include_context=False, include_input=True):
        location = list(detail.get("loc", ()))
        proposal_index = location.pop(0) if location and isinstance(location[0], int) else "?"
        field_path = ".".join(str(part) for part in location) or "proposal"
        field_name = str(location[0]) if location else "proposal"
        requirement, example = _TASK_PROPOSAL_FIELD_CORRECTIONS.get(
            field_name,
            (str(detail.get("msg", "invalid value")), "use the canonical create_tasks schema"),
        )
        received = detail.get("input")
        received_shape = "missing" if detail.get("type") == "missing" else type(received).__name__
        diagnostic = (
            f"proposal[{proposal_index}].{field_path}: requires {requirement}; "
            f"received={received_shape}; example={example}"
        )
        if diagnostic not in diagnostics:
            diagnostics.append(diagnostic)
    return "Task proposal validation failed: " + "; ".join(diagnostics)


TaskProposalList = List[TaskProposal]


@dataclass
class TaskProposalRepairGuard:
    """Restore previously valid proposals before a corrective task-creation mutation."""

    baseline: List[Optional[TaskProposal]] = field(default_factory=list)
    valid_indexes: set[int] = field(default_factory=set)

    def capture(self, tasks: Any) -> None:
        """Remember individually valid proposals from a rejected batch without guessing repairs."""

        if not isinstance(tasks, list):
            return
        if self.valid_indexes and len(tasks) != len(self.baseline):
            return
        baseline = list(self.baseline) if self.baseline else [None] * len(tasks)
        valid_indexes = set(self.valid_indexes)
        for index, task in enumerate(tasks):
            if index in valid_indexes:
                continue
            try:
                proposal = TypeAdapter(TaskProposal).validate_python(task)
            except ValidationError:
                proposal = None
            baseline[index] = proposal
            if proposal is not None:
                valid_indexes.add(index)
        self.baseline = baseline
        self.valid_indexes = valid_indexes

    def restore(self, tasks: TaskProposalList) -> TaskProposalList:
        """Prevent an LLM correction from changing proposals that were already valid."""

        if not self.valid_indexes:
            return tasks
        if len(tasks) != len(self.baseline):
            raise ValueError("task-creator repair must preserve the original proposal count and order")
        restored = list(tasks)
        for index in self.valid_indexes:
            baseline = self.baseline[index]
            if baseline is not None:
                restored[index] = baseline
        return restored


_TASK_PROPOSAL_INPUT_SCHEMA = {
    "json": {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "Submit task proposals using canonical field names. Methods and snapshot_refs default to [] and "
            "procedure limits default to 50 requests and 10 minutes; snapshot proposals use limits={}."
        ),
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/$defs/TaskProposal"},
            }
        },
        "required": ["tasks"],
        "$defs": {
            "TaskProposal": {
                "type": "object",
                "additionalProperties": False,
                "examples": [
                    {
                        "title": "Assess frozen inventory",
                        "objective": "Assess the assigned frozen inventory unit",
                        "methods": [],
                        "limits": {},
                        "snapshot_refs": ["artifact:artifacts/inventory_manifest.json"],
                        "criteria": [{"description": "Assess the assigned frozen inventory unit"}],
                        "target_ids": ["target-1"],
                    },
                    {
                        "title": "Build surface inventory",
                        "objective": "Map the assigned target",
                        "basis_description": "Bounded surface discovery",
                        "methods": ["crawl"],
                        "limits": {"max_requests": 500, "max_depth": 3},
                        "snapshot_refs": [],
                        "output_kind": "inventory_manifest",
                        "criteria": [{"description": "Store the finite inventory manifest"}],
                        "target_ids": ["target-1"],
                    },
                ],
                "required": ["title", "objective", "criteria"],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                    "basis_description": {"type": ["string", "null"]},
                    "methods": {"type": "array", "items": {"type": "string"}, "default": []},
                    "limits": {
                        "type": "object",
                        "additionalProperties": False,
                        "default": DEFAULT_TASK_PROPOSAL_LIMITS,
                        "properties": {
                            **{
                                key: {"type": "integer", "exclusiveMinimum": 0}
                                for key in DISCOVERY_PROCEDURE_LIMIT_KEYS
                            },
                            "description": {"type": "string"},
                        },
                    },
                    "snapshot_refs": {"type": "array", "items": {"type": "string"}, "default": []},
                    "finding_refs": {"type": "array", "items": {"type": "string"}, "default": []},
                    "output_kind": {"type": "string", "enum": ["artifact", "inventory_manifest"]},
                    "criteria": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 1,
                        "items": {"$ref": "#/$defs/TaskProposalCriterion"},
                    },
                    "target_ids": {"type": "array", "items": {"type": "string"}},
                    "replacement_of": {"type": ["string", "null"]},
                    "supersedes_criteria": {"type": "array", "items": {"type": "string"}, "default": []},
                    "workstream": {"type": ["string", "null"]},
                    "task_role": {"type": "string", "enum": ["mapping", "synthesis", "direct_single_step"], "default": "mapping"},
                    "depends_on_workstreams": {"type": "array", "items": {"type": "string"}, "default": []},
                    "inapplicability_reason": {"type": ["string", "null"]},
                },
            },
            "TaskProposalCriterion": {
                "type": "object",
                "additionalProperties": False,
                "required": ["description"],
                "properties": {"description": {"type": "string", "minLength": 1}},
            },
        },
    }
}


def _get_active_plan() -> OperationPlan:
    client = _ensure_memory_client()
    user_id = _user_id()

    plan = client.get_active_plan(user_id=user_id, operation_id=_operation_id())
    if not plan:
        raise ValueError("no_active_plan")
    return plan


def _get_plan_current_phase() -> int:
    return int(_get_active_plan().current_phase)


def _finding_validation_task_phase(plan: Optional[OperationPlan], current_phase: int) -> int:
    """Choose the planned owner for a controller-created finding-validation task."""

    if plan is None:
        return current_phase
    ordered_phases = sorted(plan.phases, key=lambda phase: phase.id)
    current_index = next(
        (index for index, phase in enumerate(ordered_phases) if phase.id == current_phase),
        None,
    )
    if current_index is None:
        return current_phase
    validation_indices = [
        index
        for index, phase in enumerate(ordered_phases)
        if phase.task_creation_mode == "finding_validation"
    ]
    if current_index in validation_indices or any(index < current_index for index in validation_indices):
        return current_phase

    next_finding_dependent_index = next(
        (
            index
            for index, phase in enumerate(ordered_phases[current_index + 1 :], start=current_index + 1)
            if phase.task_creation_mode == "finding_dependent"
            or (phase.task_creation_mode == "standard" and phase.requires_finding_candidates)
        ),
        None,
    )
    future_validation_index = next(
        (
            index
            for index, phase in enumerate(ordered_phases[current_index + 1 :], start=current_index + 1)
            if phase.task_creation_mode == "finding_validation"
            and (next_finding_dependent_index is None or index < next_finding_dependent_index)
        ),
        None,
    )
    return ordered_phases[future_validation_index].id if future_validation_index is not None else current_phase


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
    target_ids: List[str],
    plan: Optional[OperationPlan],
    proposal: Optional[TaskProposal] = None,
) -> Tuple[TargetScope, List[str]]:
    if plan is None or not plan.targets:
        return "all", []
    valid_ids = {target.target_id for target in plan.targets}
    if any(target_id.lower() in {"target", "target-id", "placeholder", "none"} for target_id in target_ids):
        raise ValueError("target_ids must reference concrete operation target IDs")
    invalid_ids = [target_id for target_id in target_ids if target_id not in valid_ids]
    if invalid_ids:
        raise ValueError(f"target_ids contain unknown operation target IDs: {', '.join(invalid_ids)}")
    selected_targets = [
        target for target in plan.targets if not target_ids or target.target_id in set(target_ids)
    ]
    if proposal is not None:
        _validate_proposal_service_scope(proposal, selected_targets)
    if target_ids:
        return "subset", target_ids
    return "all", []


_PORT_RANGE_PATTERN = re.compile(r"(?<!\d)(\d{1,5})\s*-\s*(\d{1,5})(?!\d)")
_PORT_NUMBER_PATTERN = re.compile(r"(?<![\d-])(\d{1,5})(?![\d-])")
_PORT_CONTEXT_PATTERN = re.compile(r"\bports?\b(?P<tail>[^.;\n]{0,96})", re.IGNORECASE)
_PORT_SELECTOR_PATTERN = re.compile(r"(?<!\w)(?:-p|--ports?)(?:\s*=\s*|\s+)([^\s,;)]*)", re.IGNORECASE)
_BARE_PORT_SELECTOR_PATTERN = re.compile(
    r"(?<!\w)(?:-p|--ports?)(?![-=]|\s+\d)(?=\s|$|[,)])", re.IGNORECASE
)
_BROAD_PORT_SELECTOR_PATTERN = re.compile(r"(?<!\w)(?:-p|--ports?)-(?=\s|$|[,)])", re.IGNORECASE)
_BROAD_PORT_SCOPE_PATTERN = re.compile(
    r"\b(?:all|every|remaining|other)\s+ports?\b|"
    r"\b(?:host[- ]wide|all[- ]port|full[- ]port)\s+(?:scan|scanning|enumeration|discovery)\b|"
    r"\b(?:port|ports)\s+(?:scan|scanning|enumeration|enumerate|discovery|sweep)\b",
    re.IGNORECASE,
)


def _explicit_target_port(target: OperationTarget) -> Optional[int]:
    """Return a registered service port, if the target has one."""

    value = str(target.value or "").strip()
    parsed = urlsplit(value if "://" in value else f"//{value}")
    try:
        return parsed.port
    except ValueError as error:
        raise ValueError(f"invalid registered target boundary for {target.target_id}: {value}") from error


def _explicit_target_host(target: OperationTarget) -> Optional[str]:
    value = str(target.value or "").strip()
    parsed = urlsplit(value if "://" in value else f"//{value}")
    return parsed.hostname


def _proposal_scope_text(proposal: TaskProposal) -> str:
    return " ".join(
        [
            proposal.title,
            proposal.objective,
            proposal.basis_description or proposal.objective,
            *(criterion.description for criterion in proposal.criteria),
        ]
    )


def _proposal_port_references(proposal: TaskProposal) -> Tuple[List[int], List[Tuple[int, int]], bool]:
    """Extract explicit port references from proposal scope text."""

    text = _proposal_scope_text(proposal)
    numbers: List[int] = []
    ranges: List[Tuple[int, int]] = []

    if _BROAD_PORT_SELECTOR_PATTERN.search(text):
        return numbers, ranges, True

    for match in _PORT_CONTEXT_PATTERN.finditer(text):
        tail = match.group("tail")
        for range_match in _PORT_RANGE_PATTERN.finditer(tail):
            ranges.append((int(range_match.group(1)), int(range_match.group(2))))
        range_spans = [match.span() for match in _PORT_RANGE_PATTERN.finditer(tail)]
        for number_match in _PORT_NUMBER_PATTERN.finditer(tail):
            if not any(start <= number_match.start() < end for start, end in range_spans):
                numbers.append(int(number_match.group(1)))

    for match in _PORT_SELECTOR_PATTERN.finditer(text):
        selector = match.group(1)
        if selector == "-":
            return numbers, ranges, True
        for range_match in _PORT_RANGE_PATTERN.finditer(selector):
            ranges.append((int(range_match.group(1)), int(range_match.group(2))))
        range_spans = [match.span() for match in _PORT_RANGE_PATTERN.finditer(selector)]
        for number_match in _PORT_NUMBER_PATTERN.finditer(selector):
            if not any(start <= number_match.start() < end for start, end in range_spans):
                numbers.append(int(number_match.group(1)))

    if _BARE_PORT_SELECTOR_PATTERN.search(text):
        return numbers, ranges, True
    return numbers, ranges, False


def _validate_proposal_service_scope(proposal: TaskProposal, selected_targets: List[OperationTarget]) -> None:
    """Enforce exact registered ports for proposals scoped to explicit service targets."""

    target_ports = {
        (str(target.value), port)
        for target in selected_targets
        if (port := _explicit_target_port(target)) is not None
    }
    if not target_ports:
        return

    text = _proposal_scope_text(proposal)
    if _BROAD_PORT_SCOPE_PATTERN.search(text):
        raise ValueError(
            "explicit service targets permit only their registered port; broad or omitted-port enumeration is not allowed"
        )

    numbers, ranges, ambiguous_selector = _proposal_port_references(proposal)
    allowed_ports = {port for _value, port in target_ports}
    allowed_ports_by_host: Dict[str, set[int]] = {}
    for target in selected_targets:
        port = _explicit_target_port(target)
        host = _explicit_target_host(target)
        if port is not None and host:
            allowed_ports_by_host.setdefault(host.lower(), set()).add(port)
    for host, allowed_host_ports in allowed_ports_by_host.items():
        host_pattern = re.compile(
            rf"(?<![\w.-])(?:[a-z][\w+.-]*://)?{re.escape(host)}:(\d{{1,5}})",
            re.IGNORECASE,
        )
        invalid_host_ports = sorted(
            {
                int(match.group(1))
                for match in host_pattern.finditer(text)
                if int(match.group(1)) not in allowed_host_ports
            }
        )
        if invalid_host_ports:
            raise ValueError(
                f"task scope uses ports {invalid_host_ports} for explicit host {host}; "
                f"allowed ports are {sorted(allowed_host_ports)}"
            )
    invalid_numbers = sorted({number for number in numbers if number not in allowed_ports})
    invalid_ranges = [item for item in ranges if item[0] > item[1] or any(port not in allowed_ports for port in range(item[0], item[1] + 1))]
    if ambiguous_selector:
        raise ValueError(
            f"explicit service targets require an exact port selector; allowed ports are {sorted(allowed_ports)}"
        )
    if invalid_numbers or invalid_ranges:
        details = []
        if invalid_numbers:
            details.append(f"ports {invalid_numbers}")
        if invalid_ranges:
            details.append(f"ranges {invalid_ranges}")
        raise ValueError(
            f"task scope exceeds explicit service target boundary ({'; '.join(details)}); "
            f"allowed ports are {sorted(allowed_ports)}"
        )


_EXPLICIT_SERVICE_URL_REFERENCE_PATTERN = re.compile(
    r"(?<![\w+.-])[a-z][a-z0-9+.-]*://[^\s\"'<>`]+",
    re.IGNORECASE,
)


_SCOPE_HOSTNAME_PATTERN = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?"
)
_FINDING_VALIDATION_GUARDS_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "system" / "finding_validation_guards.yaml"
)


def _is_valid_bare_scope_host_port(reference: str) -> bool:
    """Return whether a scheme-less reference contains an intentional host:port literal."""

    parsed = urlsplit(f"//{reference}")
    host = (parsed.hostname or "").rstrip(".")
    if not host:
        return False
    if reference.startswith("["):
        try:
            return isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
        except ValueError:
            return False
    return bool(_SCOPE_HOSTNAME_PATTERN.fullmatch(host))


def _bare_scope_host_is_assigned(reference: str, targets: List[OperationTarget]) -> bool:
    """Return whether a bare host:port token names an assigned host or in-scope IP."""

    parsed = urlsplit(f"//{reference}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or not _is_valid_bare_scope_host_port(reference):
        return False
    for target in targets:
        assigned_host = (_explicit_target_host(target) or "").lower().rstrip(".")
        if assigned_host and host == assigned_host:
            return True
        if target.type == "network" and host == str(target.value).lower().rstrip("."):
            return True
        if target.type == "network_range":
            try:
                if ipaddress.ip_address(host) in ipaddress.ip_network(target.value, strict=False):
                    return True
            except ValueError:
                continue
    return False


def _selected_task_targets(plan: OperationPlan, task: Task) -> List[OperationTarget]:
    """Return only the executable targets assigned to a task."""

    if task.target_scope == "subset":
        selected_ids = set(task.target_ids)
        return [target for target in plan.targets if target.target_id in selected_ids]
    return list(plan.targets)


def _explicit_service_references(
    text: str,
    *,
    include_bare_host_ports: bool = True,
    assigned_targets: Optional[List[OperationTarget]] = None,
) -> List[str]:
    """Extract URL or host:port literals without interpreting artifact paths as targets."""

    references = []
    seen = set()
    url_matches = list(_EXPLICIT_SERVICE_URL_REFERENCE_PATTERN.finditer(text or ""))
    for match in url_matches:
        reference = match.group(0).rstrip(".,;:)]}")
        if reference and reference not in seen:
            references.append(reference)
            seen.add(reference)
    if not include_bare_host_ports:
        return references
    for match in _RE_HOST_PORT_TARGET.finditer(text or ""):
        if any(start <= match.start() and match.end() <= end for start, end in (item.span() for item in url_matches)):
            continue
        reference = match.group(0).rstrip(".,;:)]}")
        if (
            reference
            and _bare_scope_host_is_assigned(reference, assigned_targets or [])
            and reference not in seen
        ):
            references.append(reference)
            seen.add(reference)
    return references


def task_service_scope_validation_details(plan: OperationPlan, task: Task, text: str) -> List[Dict[str, Any]]:
    """Return structured service-boundary violations for task text.

    A task assigned to an explicit service may mention a route or query, but every explicit URL or host:port
    literal must retain an assigned host and port. URL literals must retain the registered scheme as well.
    Bare host:port literals are accepted for an explicitly registered URL because they do not assert a different
    scheme. Filesystem and artifact prose is deliberately ignored.
    """

    selected_targets = _selected_task_targets(plan, task)
    service_targets = []
    for target in selected_targets:
        host = _explicit_target_host(target)
        port = _explicit_target_port(target)
        if host and port is not None:
            value = str(target.value).strip()
            parsed = urlsplit(value if "://" in value else f"//{value}")
            service_targets.append(
                {
                    "target_id": target.target_id,
                    "value": str(target.value),
                    "scheme": parsed.scheme.lower() or None,
                    "host": host.lower().rstrip("."),
                    "port": port,
                }
            )
    if not service_targets:
        return []

    technology_only = all(
        criterion.id == "validate-the-assigned-technology" for criterion in task.acceptance.criteria
    )
    violations = []
    for reference in _explicit_service_references(
        text,
        include_bare_host_ports=not technology_only,
        assigned_targets=selected_targets,
    ):
        parsed = urlsplit(reference if "://" in reference else f"//{reference}")
        try:
            port = parsed.port
        except ValueError:
            violations.append({
                "literal": reference,
                "scheme": parsed.scheme.lower() or None,
                "host": (parsed.hostname or "").lower().rstrip("."),
                "port": None,
                "reason": "invalid_port",
                "allowed_targets": service_targets,
            })
            continue
        host = (parsed.hostname or "").lower().rstrip(".")
        scheme = parsed.scheme.lower() or None
        matches = [
            target
            for target in service_targets
            if target["host"] == host
            and target["port"] == port
            and (scheme is None or target["scheme"] is None or target["scheme"] == scheme)
        ]
        if not matches:
            violations.append({
                "literal": reference,
                "scheme": scheme,
                "host": host,
                "port": port,
                "reason": "boundary_mismatch",
                "allowed_targets": service_targets,
            })
    return violations


def task_service_scope_violations(plan: OperationPlan, task: Task, text: str) -> List[str]:
    """Return human-readable exact service-boundary violations for task text."""

    messages = []
    for violation in task_service_scope_validation_details(plan, task, text):
        allowed = ", ".join(
            f"{target['target_id']}={target['value']}" for target in violation["allowed_targets"]
        )
        if violation["reason"] == "invalid_port":
            messages.append(f"invalid service reference `{violation['literal']}`; allowed targets: {allowed}")
        else:
            messages.append(
                f"service reference `{violation['literal']}` differs from assigned scheme/host/port boundary; "
                f"allowed targets: {allowed}"
            )
    return messages


def resolve_bound_executable_target(requested_target: str) -> str:
    """Resolve a tool target from the active task's registered target boundary."""

    requested = str(requested_target or "").strip()
    try:
        plan = _get_active_plan()
        active = [task for task in _get_database_store().get_tasks(_operation_id()) if task.status == "active"]
    except Exception:
        return requested
    if len(active) != 1 or not plan.targets:
        return requested
    task = active[0]
    selected = (
        [target for target in plan.targets if target.target_id in set(task.target_ids)]
        if task.target_scope == "subset"
        else list(plan.targets)
    )
    if len(selected) == 1:
        registered = selected[0].value
        if requested and requested.rstrip("/") != registered.rstrip("/"):
            logger.info(
                "Canonicalized tool target task_uid=%s requested=%s registered=%s",
                task.task_uid,
                requested,
                registered,
            )
        return registered
    matching = [target.value for target in selected if target.value.rstrip("/") == requested.rstrip("/")]
    if len(matching) == 1:
        return matching[0]
    raise ValueError("Tool target must match one executable target bound to the active task")


_RE_URL_PATTERN = re.compile(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*')
_RE_PATH_PATTERN = re.compile(r'(?:(?<=^)|(?<=\s))(?:/|\./|\.\./)[a-zA-Z0-9._\-/]+')

# Regex for UUID: 8-4-4-4-12 hex chars
_RE_UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
# Regex for numeric IDs: one or more digits, possibly preceded by = or /
_RE_NUMERIC_ID = re.compile(r'(?<=/|=)\d+(?=$|/|&|\s)')


def _artifact_path_from_ref(reference: str) -> str:
    """Resolve an operation-local artifact reference without escaping its root.

    Bare relative paths are accepted for compatibility with artifact-producing
    tools and resolve from ``artifacts/`` before the operation root. Explicit
    ``artifact:`` references retain their exact operation-relative meaning.
    """

    text = normalize_artifact_reference_token(reference)
    if text.startswith("artifact_id:"):
        artifact_id = text.split(":", 1)[1]
        if not artifact_id or artifact_id != os.path.basename(artifact_id):
            raise ValueError("artifact_id must contain one artifact filename")
        text = f"artifact:artifacts/{artifact_id}"
    raw_path = text.removeprefix("artifact:")
    root = os.path.realpath(_operation_output_root())
    if os.path.isabs(raw_path):
        candidates = [raw_path]
    elif text.startswith("artifact:") or raw_path.startswith("artifacts/"):
        candidates = [os.path.join(root, raw_path)]
    else:
        candidates = [os.path.join(root, "artifacts", raw_path), os.path.join(root, raw_path)]

    for candidate in candidates:
        resolved = os.path.realpath(candidate)
        if os.path.commonpath([root, resolved]) != root:
            raise ValueError(
                f"Artifact is outside the current operation output {root}: {reference}. "
                "Use a path relative to the current operation output with the artifact: prefix."
            )
        if os.path.isfile(resolved):
            return resolved
    raise ValueError(f"Artifact does not exist: {reference}")


def _is_inventory_manifest_candidate(reference: str) -> bool:
    """Identify artifacts that should receive inventory-manifest validation."""

    if not reference.startswith("artifact:"):
        return False
    try:
        path = _artifact_path_from_ref(reference)
    except ValueError:
        return False
    basename = os.path.basename(path).lower()
    if "inventory" in basename or "manifest" in basename:
        return True
    try:
        with open(path, "r", encoding="utf-8") as artifact_file:
            payload = json.load(artifact_file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and bool({"schema_version", "items", "unassessed_gaps"} & payload.keys())


def canonical_artifact_reference(reference: str) -> str:
    """Return one current-operation artifact as a stable, portable reference."""

    path = _artifact_path_from_ref(reference)
    relative = os.path.relpath(path, _operation_output_root()).replace(os.sep, "/")
    return f"artifact:{relative}"


_CANONICAL_ACCEPTANCE_EVIDENCE_HELP = (
    "Acceptance evidence references must use one of: artifact:artifacts/<file>, artifact_id:<id>, memory:<id>, "
    "or finding:<id>. Bare current-operation relative artifact paths are accepted for compatibility and resolve "
    "from artifacts/ first. Raw URLs, shell commands, tool IDs, and inline output are invalid. "
    "Example: artifact:artifacts/http_response.txt"
)

_ACCEPTANCE_EVIDENCE_MEMORY_HELP = (
    "Acceptance evidence memory does not exist in this operation: {reference}. "
    "Memory evidence is operation-scoped; do not reuse a memory ID from another operation or from "
    "cross-operation learning. Store the observation with store_observation(...) and pass its returned "
    "memory_ref (for example, memory:<id>) in evidence_refs. If the evidence is a file, save it in the "
    "current operation and use artifact:artifacts/<file> instead."
)


def _acceptance_evidence_reference_error() -> ValueError:
    """Return the shared correction-ready acceptance evidence error."""

    return ValueError(_CANONICAL_ACCEPTANCE_EVIDENCE_HELP)


def _acceptance_evidence_memory_error(reference: str) -> ValueError:
    """Return actionable guidance for a memory reference outside this operation."""

    return ValueError(_ACCEPTANCE_EVIDENCE_MEMORY_HELP.format(reference=reference))


def _canonical_evidence_reference(reference: str) -> str:
    text = str(reference or "").strip()
    if text.startswith(("memory:", "finding:")):
        return text
    try:
        return canonical_artifact_reference(text)
    except ValueError as error:
        if text.startswith(("artifact:", "artifact_id:")) or os.path.isabs(text) or text.startswith("artifacts/"):
            raise
        raise _acceptance_evidence_reference_error() from error


class _InventoryLinkParser(HTMLParser):
    """Collect navigation and form destinations without treating static assets as endpoints."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: List[str] = []
        self.forms: List[Dict[str, Any]] = []
        self._active_form: Optional[Dict[str, Any]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        normalized_tag = tag.lower()
        values = dict(attrs)
        if normalized_tag == "form":
            action = str(values.get("action") or "").strip() or "/"
            method = str(values.get("method") or "GET").strip().upper()
            self.references.append(action)
            self._active_form = {"action": action, "method": method, "inputs": []}
            self.forms.append(self._active_form)
            return
        if normalized_tag in {"input", "textarea", "select", "button"} and self._active_form is not None:
            name = str(values.get("name") or "").strip()
            if name:
                input_type = str(values.get("type") or normalized_tag).strip().lower()
                self._active_form["inputs"].append({"name": name, "location": "body", "type": input_type})
            return
        attribute_name = "href" if normalized_tag == "a" else ""
        if not attribute_name:
            return
        value = str(values.get(attribute_name) or "").strip()
        if value:
            self.references.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form":
            self._active_form = None


def _inventory_target_bases() -> Dict[str, str]:
    try:
        targets = _get_active_plan().targets
    except Exception:
        return {}
    bases = {}
    for target in targets:
        parsed = urlsplit(target.value)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            bases[target.target_id] = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "/", "", ""))
    return bases


def _deterministic_inventory_candidates() -> Tuple[List[Dict[str, Any]], int]:
    """Extract normalized in-scope routes from current-operation HTML artifacts."""

    bases = _inventory_target_bases()
    if not bases:
        return [], 0
    artifact_dir = os.path.join(_operation_output_root(), "artifacts")
    if not os.path.isdir(artifact_dir):
        return [], 0
    candidates: Dict[Tuple[str, str], Dict[str, Any]] = {}
    source_count = 0
    for filename in sorted(os.listdir(artifact_dir))[:200]:
        path = os.path.join(artifact_dir, filename)
        if not os.path.isfile(path) or os.path.getsize(path) > 2_000_000:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as artifact_file:
                content = artifact_file.read()
        except OSError:
            continue
        if "<a" not in content.lower() and "<form" not in content.lower():
            continue
        parser = _InventoryLinkParser()
        try:
            parser.feed(content)
        except Exception:
            continue
        if not parser.references:
            continue
        source_count += 1
        for target_id, base in bases.items():
            base_parts = urlsplit(base)
            for reference in parser.references:
                if reference.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                absolute = urlsplit(urljoin(base, reference))
                if absolute.scheme.lower() != base_parts.scheme or absolute.netloc.lower() != base_parts.netloc.lower():
                    continue
                path_value = re.sub(r"/{2,}", "/", absolute.path or "/")
                query_pairs = parse_qsl(absolute.query, keep_blank_values=True)
                query = "&".join(
                    f"{name}={value}" if value else name
                    for name, value in query_pairs
                )
                value = urlunsplit((absolute.scheme.lower(), absolute.netloc.lower(), path_value, query, ""))
                route_key = urlunsplit((absolute.scheme.lower(), absolute.netloc.lower(), path_value.rstrip("/") or "/", "", ""))
                attributes: Dict[str, Any] = {"discovered_by": "html_link_extraction"}
                if query_pairs:
                    attributes["query_parameters"] = sorted({name for name, _value in query_pairs})
                matching_forms = [
                    form
                    for form in parser.forms
                    if urlsplit(urljoin(base, str(form["action"]))).path == absolute.path
                ]
                if matching_forms:
                    operations = sorted({str(form["method"]).upper() for form in matching_forms})
                    inputs = []
                    for form in matching_forms:
                        inputs.extend(form["inputs"])
                    attributes["interaction"] = {
                        "interface": "http",
                        "operations": operations,
                        "inputs": list({json.dumps(item, sort_keys=True): item for item in inputs}.values()),
                        "evidence_refs": [canonical_artifact_reference(path)],
                    }
                candidates[(target_id, route_key)] = {
                    "target_id": target_id,
                    "kind": "endpoint",
                    "value": value,
                    "attributes": attributes,
                }
    return list(candidates.values()), source_count


def _reconcile_inventory_manifest(path: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
    candidates, source_count = _deterministic_inventory_candidates()
    existing_routes = {
        (str(item["target_id"]), (_normalized_route(str(item["value"])) or ("", ""))[0])
        for item in manifest["items"]
        if str(item.get("kind")) in {"endpoint", "parameter"}
    }
    added = 0
    existing_ids = {str(item["id"]) for item in manifest["items"]}
    for candidate in candidates:
        route = _normalized_route(candidate["value"])
        key = (candidate["target_id"], route[0] if route else "")
        if not route or key in existing_routes:
            continue
        digest = hashlib.sha256(f"{key[0]}:{key[1]}".encode("utf-8")).hexdigest()[:12]
        item_id = f"auto-endpoint-{digest}"
        suffix = 2
        while item_id in existing_ids:
            item_id = f"auto-endpoint-{digest}-{suffix}"
            suffix += 1
        manifest["items"].append({"id": item_id, **candidate})
        existing_ids.add(item_id)
        existing_routes.add(key)
        added += 1
    manifest["extraction"] = {
        "source_artifact_count": source_count,
        "candidate_count": len(candidates),
        "added_count": added,
    }
    if added:
        _write_inventory_manifest_atomically(path, manifest)
    return manifest


def _write_inventory_manifest_atomically(path: str, manifest: Dict[str, Any]) -> None:
    temporary_path = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, indent=2, sort_keys=True)
        manifest_file.write("\n")
    os.replace(temporary_path, path)


def _is_out_of_scope_inventory_item(item: Any, target_values: Dict[str, str]) -> bool:
    """Return whether one URL-bearing inventory item is outside its registered target boundary."""

    if not isinstance(item, dict) or str(item.get("kind", "")).strip() not in {"endpoint", "parameter"}:
        return False
    parsed = urlsplit(str(item.get("value", "")).strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    registered_target = target_values.get(str(item.get("target_id", "")).strip())
    if not registered_target:
        return False
    boundary = urlsplit(registered_target)
    if boundary.scheme.lower() not in {"http", "https"} or not boundary.netloc:
        return False
    return parsed.scheme.lower() != boundary.scheme.lower() or parsed.netloc.lower() != boundary.netloc.lower()


def _normalize_inventory_target_ids(items: Any, target_values: Dict[str, str]) -> bool:
    """Replace an unambiguous raw target value with its controller-owned logical target ID."""

    if not isinstance(items, list) or not target_values:
        return False
    value_to_ids: Dict[str, List[str]] = {}
    for target_id, value in target_values.items():
        value_to_ids.setdefault(str(value).strip(), []).append(target_id)
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        supplied = str(item.get("target_id") or "").strip()
        matches = value_to_ids.get(supplied, [])
        if supplied not in target_values and len(matches) == 1:
            item["target_id"] = matches[0]
            changed = True
    return changed


def _normalize_inventory_interaction(item_id: str, attributes: Dict[str, Any]) -> bool:
    """Validate and normalize optional protocol-neutral interaction metadata in place."""

    interaction = attributes.get("interaction")
    if interaction is None:
        return False
    if not isinstance(interaction, dict):
        raise ValueError(f"inventory manifest item {item_id} attributes.interaction must be an object")
    allowed = {
        "interface",
        "operations",
        "inputs",
        "success_signals",
        "failure_signals",
        "evidence_refs",
    }
    unknown = sorted(set(interaction) - allowed)
    if unknown:
        raise ValueError(
            f"inventory manifest item {item_id} attributes.interaction has unsupported fields: {', '.join(unknown)}"
        )
    changed = False
    interface = interaction.get("interface")
    if interface is not None and not str(interface).strip():
        raise ValueError(f"inventory manifest item {item_id} interaction.interface must be non-empty")
    if interface is not None and interface != str(interface).strip():
        interaction["interface"] = str(interface).strip()
        changed = True
    for field_name in ("operations", "success_signals", "failure_signals", "evidence_refs"):
        values = interaction.get(field_name)
        if values is None:
            continue
        if not isinstance(values, list) or not all(str(value).strip() for value in values):
            raise ValueError(f"inventory manifest item {item_id} interaction.{field_name} must be non-empty strings")
        normalized_values = list(dict.fromkeys(str(value).strip() for value in values))
        if values != normalized_values:
            interaction[field_name] = normalized_values
            changed = True
    inputs = interaction.get("inputs")
    if inputs is not None:
        if not isinstance(inputs, list):
            raise ValueError(f"inventory manifest item {item_id} interaction.inputs must be a list")
        normalized_inputs = []
        for input_index, input_item in enumerate(inputs):
            if not isinstance(input_item, dict) or not str(input_item.get("name") or "").strip():
                raise ValueError(
                    f"inventory manifest item {item_id} interaction.inputs[{input_index}] requires a name"
                )
            unknown_input_fields = sorted(set(input_item) - {"name", "location", "type"})
            if unknown_input_fields:
                raise ValueError(
                    f"inventory manifest item {item_id} interaction.inputs[{input_index}] has unsupported fields: "
                    + ", ".join(unknown_input_fields)
                )
            normalized_input = {"name": str(input_item["name"]).strip()}
            for field_name in ("location", "type"):
                if field_name in input_item:
                    value = str(input_item[field_name]).strip()
                    if not value:
                        raise ValueError(
                            f"inventory manifest item {item_id} interaction.inputs[{input_index}].{field_name} "
                            "must be non-empty"
                        )
                    normalized_input[field_name] = value
            normalized_inputs.append(normalized_input)
        if inputs != normalized_inputs:
            interaction["inputs"] = normalized_inputs
            changed = True
    return changed


def _load_inventory_manifest(reference: str, *, reconcile: bool = False) -> Tuple[Dict[str, Any], str]:
    path = _artifact_path_from_ref(reference)
    try:
        with open(path, "rb") as manifest_file:
            raw_manifest = manifest_file.read()
        manifest = json.loads(raw_manifest)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid inventory manifest: {reference}") from error
    if not isinstance(manifest, dict):
        canonical_input = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        artifact_digest = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
        raise ValueError(
            f"inventory manifest validation failed artifact_sha256={artifact_digest}: root must be an object"
        )
    try:
        target_values = {target.target_id: target.value for target in _get_active_plan().targets}
    except Exception:
        target_values = {}
    items = manifest.get("items")
    normalized_root = False
    if _normalize_inventory_target_ids(items, target_values):
        normalized_root = True
    removed_count = 0
    if isinstance(items, list):
        filtered_items = [item for item in items if not _is_out_of_scope_inventory_item(item, target_values)]
        removed_count = len(items) - len(filtered_items)
        if removed_count:
            manifest["items"] = filtered_items
            items = filtered_items
            normalized_root = True
    gaps = manifest.get("unassessed_gaps")
    missing_schema_version = "schema_version" not in manifest
    compatibility_shape = (
        isinstance(items, list)
        and bool(items)
        and isinstance(gaps, list)
        and all(
            isinstance(item, dict)
            and all(bool(str(item.get(field) or "").strip()) for field in ("id", "target_id", "kind", "value"))
            for item in items
        )
    )
    if missing_schema_version and compatibility_shape:
        manifest["schema_version"] = INVENTORY_MANIFEST_SCHEMA_VERSION
        normalized_root = True
    if normalized_root:
        _write_inventory_manifest_atomically(path, manifest)
    if removed_count:
        logger.info(
            "Removed out-of-scope inventory items reference=%s removed_count=%d",
            canonical_artifact_reference(reference),
            removed_count,
        )
    if missing_schema_version and compatibility_shape:
        logger.info(
            "Inferred inventory manifest schema_version=%s reference=%s",
            INVENTORY_MANIFEST_SCHEMA_VERSION,
            canonical_artifact_reference(reference),
        )
    canonical_input = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    artifact_digest = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
    diagnostics = []
    if not missing_schema_version and manifest.get("schema_version") != INVENTORY_MANIFEST_SCHEMA_VERSION:
        diagnostics.append(
            f"inventory manifest schema_version must be {INVENTORY_MANIFEST_SCHEMA_VERSION} at $.schema_version"
        )
    if not isinstance(items, list) or not items:
        diagnostics.append("inventory manifest items must be a non-empty list at $.items")
    if not isinstance(gaps, list):
        diagnostics.append("inventory manifest unassessed_gaps must be a list at $.unassessed_gaps")
    if diagnostics:
        raise ValueError(
            f"inventory manifest validation failed artifact_sha256={artifact_digest}: " + "; ".join(diagnostics)
        )
    allowed_kinds = set(INVENTORY_MANIFEST_ITEM_KINDS)
    item_ids = []
    normalized_inventory = False
    validated_items = []
    for item_index, item in enumerate(items):
        if not isinstance(item, dict):
            diagnostics.append(f"inventory manifest items must be objects; invalid item at $.items[{item_index}]")
            continue
        item_id = str(item.get("id", "")).strip()
        target_id = str(item.get("target_id", "")).strip()
        kind = str(item.get("kind", "")).strip()
        value = str(item.get("value", "")).strip()
        attributes = item.get("attributes", {})
        if not item_id or not target_id or not value or kind not in allowed_kinds:
            diagnostics.append(
                f"inventory manifest item at $.items[{item_index}] requires id, target_id, supported kind, and value"
            )
            continue
        if not isinstance(attributes, dict):
            diagnostics.append(
                f"inventory manifest item {item_id} field attributes at $.items[{item_index}].attributes "
                "must be an object"
            )
            continue
        try:
            if _normalize_inventory_interaction(item_id, attributes):
                normalized_inventory = True
        except ValueError as error:
            diagnostics.append(str(error))
            continue
        item_ids.append(item_id)
        validated_items.append((item_index, item))
    if len(item_ids) != len(set(item_ids)):
        diagnostics.append("inventory manifest item ids must be unique at $.items")
    normalized_values = []
    for item_index, item in validated_items:
        kind = str(item["kind"])
        if kind not in {"endpoint", "parameter"}:
            continue
        value = str(item["value"])
        if kind == "parameter" and _normalized_route(value) is None:
            continue
        try:
            normalized_value = _canonical_inventory_url(value, target_values.get(str(item["target_id"])))
        except ValueError as error:
            diagnostics.append(
                f"inventory manifest item {item['id']} field value at $.items[{item_index}].value is invalid: {error}"
            )
            continue
        if normalized_value != value:
            normalized_values.append((item, normalized_value))
    try:
        valid_target_ids = {target.target_id for target in _get_active_plan().targets}
    except Exception:
        valid_target_ids = set()
    manifest_target_ids = {str(item["target_id"]) for _item_index, item in validated_items}
    if valid_target_ids and not manifest_target_ids.issubset(valid_target_ids):
        invalid_target_ids = sorted(manifest_target_ids - valid_target_ids)
        diagnostics.append(f"inventory manifest contains unknown target IDs: {', '.join(invalid_target_ids)}")
    if diagnostics:
        raise ValueError(
            f"inventory manifest validation failed artifact_sha256={artifact_digest}: " + "; ".join(diagnostics)
        )
    for item, normalized_value in normalized_values:
        item["value"] = normalized_value
        normalized_inventory = True
    endpoint_keys = set()
    deduplicated_items = []
    duplicate_endpoint_count = 0
    for item in manifest["items"]:
        if str(item.get("kind")) != "endpoint":
            deduplicated_items.append(item)
            continue
        route = _normalized_route(str(item.get("value") or ""))
        key = (str(item.get("target_id") or ""), route[0] if route else "")
        if route and key in endpoint_keys:
            duplicate_endpoint_count += 1
            normalized_inventory = True
            continue
        if route:
            endpoint_keys.add(key)
        deduplicated_items.append(item)
    if duplicate_endpoint_count:
        manifest["items"] = deduplicated_items
        logger.info(
            "Removed duplicate inventory endpoints reference=%s duplicate_count=%d",
            canonical_artifact_reference(reference),
            duplicate_endpoint_count,
        )
    if reconcile:
        manifest = _reconcile_inventory_manifest(path, manifest)
    elif normalized_inventory:
        _write_inventory_manifest_atomically(path, manifest)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return manifest, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_inventory_url(value: str, registered_target: Optional[str]) -> str:
    """Canonicalize one HTTP inventory route and enforce its registered service boundary."""

    raw_value = str(value or "").strip().replace(r'\"', '"')
    raw_value = re.sub(r"^charset=[^\s;]+\s+(?=https?://)", "", raw_value, flags=re.IGNORECASE)
    if raw_value.startswith('"') and raw_value.endswith('"') and len(raw_value) >= 2:
        raw_value = raw_value[1:-1].strip()
    parsed = urlsplit(raw_value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"inventory endpoint must be an absolute HTTP(S) URL: {value}")
    if registered_target:
        boundary = urlsplit(registered_target)
        if boundary.scheme.lower() in {"http", "https"} and boundary.netloc:
            if parsed.scheme.lower() != boundary.scheme.lower() or parsed.netloc.lower() != boundary.netloc.lower():
                raise ValueError(
                    "inventory endpoint does not preserve the registered target scheme/host/port boundary"
                )
    cleaned_segments = []
    for segment in (parsed.path or "/").split("/"):
        cleaned = segment.strip('"')
        if not cleaned or cleaned == ".":
            continue
        cleaned_segments.append(cleaned)
    path = "/" + "/".join(cleaned_segments)
    if parsed.path.endswith("/") and path != "/":
        path += "/"
    if any(character in path or character in parsed.query for character in ('"', "\\")):
        raise ValueError("inventory endpoint contains unsupported raw quote or backslash route syntax")
    path = re.sub(r"/{2,}", "/", path)
    query = parsed.query.rstrip("&")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def _task_evidence_artifact_paths(task: Task) -> set[str]:
    paths = set()
    for evidence in task.evidence:
        raw_path = str(evidence).removeprefix("artifact:")
        root = _operation_output_root()
        candidate = raw_path if os.path.isabs(raw_path) else os.path.join(root, raw_path)
        resolved = os.path.realpath(candidate)
        if os.path.commonpath([root, resolved]) == root:
            paths.add(resolved)
    return paths


def _freeze_and_validate_acceptance(contract: AcceptanceContract, existing_tasks: List[Task]) -> AcceptanceContract:
    basis = contract.basis
    if basis.kind == "procedure":
        try:
            plan = _get_active_plan()
        except Exception:
            return contract
        valid_target_refs = {f"target:{target.target_id}" for target in plan.targets}
        valid_plan_refs = {f"plan:phase-{phase.id}" for phase in plan.phases}
        invalid_refs = []
        for reference in basis.source_refs:
            if reference.startswith("target:") and plan.targets and reference not in valid_target_refs:
                invalid_refs.append(reference)
            if reference.startswith("plan:") and reference not in valid_plan_refs:
                invalid_refs.append(reference)
        if invalid_refs:
            raise ValueError(f"Procedure acceptance basis contains unknown source references: {', '.join(invalid_refs)}")
        return contract
    op_id = _operation_id()
    store = _get_database_store()
    artifact_refs = [ref for ref in basis.source_refs if ref.startswith("artifact:")]
    for reference in basis.source_refs:
        prefix, value = reference.split(":", 1)
        if prefix == "artifact":
            artifact_path = _artifact_path_from_ref(reference)
            producers = [task for task in existing_tasks if artifact_path in _task_evidence_artifact_paths(task)]
            if producers and any(task.status != "done" for task in producers):
                raise ValueError(f"Acceptance basis producer task is not done: {reference}")
        elif prefix == "task":
            task = next((item for item in existing_tasks if item.task_uid == value), None)
            if task is None or task.status != "done":
                raise ValueError(f"Acceptance basis task is missing or not done: {reference}")
        elif prefix == "memory":
            memory = _ensure_memory_client().get_memory_by_id(value)
            metadata = (memory or {}).get("metadata", {}) if isinstance(memory, dict) else {}
            if not memory or metadata.get("operation_id", op_id) != op_id:
                raise ValueError(f"Acceptance basis memory does not exist in this operation: {reference}")
        elif prefix == "finding":
            if store.get_finding(op_id, value) is None:
                raise ValueError(f"Acceptance basis finding does not exist: {reference}")
        else:
            raise ValueError("snapshot acceptance basis may reference only task, memory, artifact, or finding sources")
    if contract.mode != "coverage":
        return contract
    if len(artifact_refs) != 1:
        raise ValueError("coverage acceptance basis requires exactly one inventory manifest artifact")
    _manifest, snapshot_hash = _load_inventory_manifest(artifact_refs[0])
    frozen_basis = AcceptanceBasis(
        kind="snapshot",
        description=basis.description,
        source_refs=basis.source_refs,
        snapshot_hash=snapshot_hash,
        item_ids=basis.item_ids,
    )
    manifest_ids = {str(item["id"]) for item in _manifest["items"]}
    if basis.item_ids and not set(basis.item_ids).issubset(manifest_ids):
        unknown = sorted(set(basis.item_ids) - manifest_ids)
        raise ValueError(f"Coverage acceptance basis contains unknown item IDs: {unknown}")
    return AcceptanceContract(mode=contract.mode, basis=frozen_basis, criteria=contract.criteria)


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


def _task_proposal_criterion_ids(criteria: List[TaskProposalCriterion]) -> List[str]:
    """Generate short task-scoped IDs; descriptions retain the semantic meaning."""

    return [f"criterion-{position}" for position, _criterion in enumerate(criteria, start=1)]


@dataclass(frozen=True)
class _NormalizedTaskProposal:
    """Controller-owned task proposal values after mode-specific fields are discarded."""

    proposal: TaskProposal
    basis_kind: AcceptanceBasisKind
    limits: Optional[Dict[str, int]]


def _normalize_task_proposal(proposal: TaskProposal) -> _NormalizedTaskProposal:
    """Discard required wire fields that do not apply to the inferred proposal mode."""

    basis_kind = proposal.inferred_basis_kind
    limits = proposal.limits.model_dump(exclude_none=True) if basis_kind == "procedure" else None
    return _NormalizedTaskProposal(proposal=proposal, basis_kind=basis_kind, limits=limits)


def _proposal_procedure_methods(proposal: TaskProposal) -> List[str]:
    """Return deterministic internal methods for controller-owned synthesis."""

    if proposal.task_role == "synthesis" and not proposal.methods:
        return ["controller_synthesis"]
    return proposal.methods


def canonical_procedure_methods(
    methods: Iterable[str],
    targets: Iterable[OperationTarget] = (),
) -> List[str]:
    """Normalize procedure methods using the structured target boundary.

    HTTP(S) service targets use ``crawl`` for route and resource discovery;
    network targets retain ``enumerate`` for host, port, and service discovery.
    The conversion is intentionally based only on structured target values and
    never on task titles or free-form objective wording.
    """

    target_values = [str(target.value).strip() for target in targets]
    all_http_service_targets = bool(target_values) and all(
        (parsed := urlsplit(value)).scheme.lower() in {"http", "https"} and bool(parsed.netloc)
        for value in target_values
    )
    aliases = {
        "analyze": "analyze",
        "analysis": "analyze",
        "audit": "analyze",
        "inspect": "analyze",
        "review": "analyze",
        "source_analysis": "analyze",
        "source_review": "analyze",
        "crawl": "crawl",
        "spider": "crawl",
        "spidering": "crawl",
        "web_spider": "crawl",
        "web_spidering": "crawl",
        "discover": "crawl",
        "discovery": "crawl",
        "enumeration": "enumerate",
        "scan": "enumerate",
        "http_request": "request",
        "web_inspect": "request",
        "web_recon": "request",
        "probe": "request",
        "request": "request",
        "compare": "compare",
        "comparison": "compare",
        "execute": "execute",
        "exploit": "execute",
        "test": "execute",
        "validate": "execute",
    }
    normalized = []
    for method in methods:
        value = re.sub(r"[^a-z0-9]+", "_", str(method or "").strip().lower()).strip("_")
        canonical = aliases.get(value, value)
        if canonical == "enumerate" and all_http_service_targets:
            canonical = "crawl"
        if canonical and canonical not in normalized:
            normalized.append(canonical)
    return normalized


def _proposal_execution_requirements(
    proposal: TaskProposal,
    plan: OperationPlan,
    criterion_id: str,
) -> Tuple[ExecutionRequirement, ...]:
    """Derive narrow, controller-owned execution obligations for procedure work."""

    if proposal.inferred_basis_kind != "procedure":
        return ()
    if proposal.task_role == "synthesis":
        return ()
    selected_ids = proposal.target_ids or [target.target_id for target in plan.targets]
    selected_targets = [target for target in plan.targets if target.target_id in selected_ids]
    routes = _procedure_proposal_endpoint_routes(proposal, selected_targets)
    subjects = routes or [f"target:{target_id}" for target_id in selected_ids]
    canonical_methods = canonical_procedure_methods(proposal.methods, selected_targets)
    method_text = ", ".join(dict.fromkeys(canonical_methods))
    return tuple(
        ExecutionRequirement(
            id=f"{criterion_id}-execution-{index}",
            description=f"Produce execution evidence for {method_text} against {subject}.",
            subject_ref=subject,
        )
        for index, subject in enumerate(subjects, start=1)
    )


def _proposal_acceptance_contract(proposal: TaskProposal, plan: OperationPlan) -> AcceptanceContract:
    """Compile a small task proposal into the full immutable acceptance contract."""

    normalized = _normalize_task_proposal(proposal)
    criterion_ids = _task_proposal_criterion_ids(proposal.criteria)
    criteria = [
        AcceptanceCriterion(
            id=criterion_id,
            description=criterion.description,
            evidence_requirements=[EvidenceRequirement(
                kind=(
                    proposal.output_kind
                    if normalized.basis_kind == "procedure"
                    else "durable_evidence"
                ),
                min_count=1,
            )],
            execution_requirements=_proposal_execution_requirements(proposal, plan, criterion_id),
        )
        for criterion_id, criterion in zip(criterion_ids, proposal.criteria)
    ]
    if normalized.basis_kind == "procedure":
        selected_target_ids = proposal.target_ids or [target.target_id for target in plan.targets]
        source_refs = [
            *(f"target:{target_id}" for target_id in selected_target_ids),
            f"plan:phase-{plan.current_phase}",
        ]
        basis = AcceptanceBasis(
            kind="procedure",
            description=proposal.effective_basis_description,
            source_refs=source_refs,
            procedure=DiscoveryProcedure(
                methods=_proposal_procedure_methods(proposal),
                limits=normalized.limits or {},
                stop_condition="first_limit_reached",
                gap_policy="record_unassessed",
                output_kind=proposal.output_kind,
            ),
        )
        mode: AcceptanceMode = "outcome"
    else:
        basis = AcceptanceBasis(
            kind="snapshot",
            description=proposal.effective_basis_description,
            source_refs=proposal.snapshot_refs,
        )
        mode = "coverage" if _proposal_inventory_snapshot(proposal) is not None else "outcome"
        if mode == "coverage":
            proposal_intent = "; ".join(criterion.description.strip() for criterion in proposal.criteria)
            criteria = [AcceptanceCriterion(
                id="assess-the-assigned-endpoint",
                description=(
                    "Assess the assigned endpoint and record one evidence-backed terminal disposition for this "
                    f"proposal intent: {proposal_intent}"
                ),
                evidence_requirements=[EvidenceRequirement(kind="durable_evidence", min_count=1)],
            )]
    return AcceptanceContract(mode=mode, basis=basis, criteria=criteria)


def _coverage_acceptance_criterion(group_kind: str, proposal_intent: str) -> AcceptanceCriterion:
    """Return the controller-owned criterion for one compiled inventory work unit."""

    criterion_types = {
        "endpoint": (
            "assess-the-assigned-endpoint",
            "Assess the assigned endpoint and record one evidence-backed terminal disposition",
        ),
        "parameter": (
            "assess-the-assigned-parameter",
            "Assess the assigned parameter and record one evidence-backed terminal disposition",
        ),
        "workflow": (
            "assess-the-assigned-workflow",
            "Assess the assigned workflow and record one evidence-backed terminal disposition",
        ),
        "service": (
            "assess-the-assigned-service",
            "Assess the assigned service and record one evidence-backed terminal disposition",
        ),
        "technology": (
            "validate-the-assigned-technology",
            "Validate the assigned technology observation and record one evidence-backed terminal disposition",
        ),
    }
    criterion_id, description = criterion_types.get(
        group_kind,
        (
            "inspect-the-assigned-resource",
            "Inspect the assigned inventory resource and record one evidence-backed terminal disposition",
        ),
    )
    intent = str(proposal_intent or "").strip()
    if intent:
        description = f"{description} for this proposal intent: {intent}"
    return AcceptanceCriterion(
        id=criterion_id,
        description=description,
        evidence_requirements=[EvidenceRequirement(kind="durable_evidence", min_count=1)],
    )


def _phase_specific_coverage_criterion(
    group_kind: str,
    proposal_intent: str,
    phase_title: str,
    phase_objective: str,
    route_label: str,
    item_ids: List[str],
) -> AcceptanceCriterion:
    """Bind every expanded route task to its active phase's distinct work."""

    criterion = _coverage_acceptance_criterion(group_kind, proposal_intent)
    phase_label = str(phase_title or "active phase").strip()
    phase_work = _route_scoped_phase_objective(phase_objective)
    if not phase_work:
        return criterion
    item_scope = ", ".join(sorted(str(item_id) for item_id in item_ids))
    return AcceptanceCriterion(
        id=criterion.id,
        description=(
            f"For phase '{phase_label}', assigned route {route_label}, and frozen item IDs {item_scope}: "
            f"{phase_work}. {criterion.description} for this assigned route only."
        ),
        evidence_requirements=criterion.evidence_requirements,
    )


_INVENTORY_WIDE_PHASE_SCOPE_PATTERNS = (
    re.compile(r"\ball\s+(?:discovered|reachable)\b", re.I),
    re.compile(r"\b(?:all|every)\s+(?:discovered\s+)?(?:entities|endpoints|items|routes|workflows)\b", re.I),
    re.compile(r"\b(?:across|throughout)\s+(?:the\s+)?(?:baseline\s+)?(?:inventory|application)\b", re.I),
    re.compile(r"\b(?:the\s+)?entire\s+(?:baseline\s+)?(?:inventory|application)\b", re.I),
    re.compile(r"\bin\s+(?:the\s+)?baseline\s+inventory\b", re.I),
    re.compile(r"\bfrom\s+the\s+baseline\s+inventory\b", re.I),
    re.compile(r"\b(?:across|throughout)\s+key\s+workflows\b", re.I),
    re.compile(r"\bkey\s+workflows\b", re.I),
)


def _route_scoped_phase_objective(phase_objective: str) -> str:
    """Remove inventory-wide completion claims while retaining phase-specific semantics."""

    scoped = str(phase_objective or "").strip()
    for pattern in _INVENTORY_WIDE_PHASE_SCOPE_PATTERNS:
        scoped = pattern.sub("", scoped)
    scoped = re.sub(r"\s+", " ", scoped)
    scoped = re.sub(r"\bmap\s+for\b", "Map", scoped, flags=re.I)
    scoped = re.sub(r"\b(?:for|of|in|to)\s*(?=[,.;:]|$)", "", scoped, flags=re.I)
    scoped = re.sub(r"\s+([,.;:])", r"\1", scoped).strip(" ,.;:")
    return scoped


def _is_generic_snapshot_proposal(proposal: TaskProposal) -> bool:
    """Return whether a proposal omits any phase-specific snapshot-work intent."""

    objective = re.sub(r"\s+", " ", proposal.objective.strip().lower())
    criterion = re.sub(r"\s+", " ", proposal.criteria[0].description.strip().lower())
    generic_values = {
        "assess frozen inventory",
        "assess each frozen inventory unit",
        "assess the assigned frozen inventory unit",
        "assess the assigned endpoint",
    }
    return objective in generic_values or criterion in generic_values


def _task_inventory_artifact_refs(task: Task) -> List[str]:
    references = [canonical_artifact_reference(path) for path in sorted(_task_evidence_artifact_paths(task))]
    store = _get_database_store()
    list_results = getattr(store, "list_task_acceptance_results", None)
    results = list_results(task.task_uid) if callable(list_results) else store.get_acceptance_results(
        _operation_id(), task.task_uid
    )
    for result in results:
        references.extend(ref for ref in result.evidence_refs if ref.startswith("artifact:"))
    valid = []
    for reference in dict.fromkeys(references):
        try:
            _load_inventory_manifest(reference)
        except ValueError:
            continue
        valid.append(reference)
    return valid


def _resolve_proposal_snapshot_refs(proposal: TaskProposal, existing_tasks: List[Task]) -> TaskProposal:
    if proposal.inferred_basis_kind != "snapshot":
        return proposal
    references = []
    for reference in proposal.snapshot_refs:
        if not reference.startswith("task:"):
            references.append(
                canonical_artifact_reference(reference)
                if reference.startswith(("artifact:", "artifact_id:")) or os.path.isabs(reference)
                else reference
            )
            continue
        task_uid = reference.split(":", 1)[1]
        producer = next(
            (task for task in existing_tasks if task.task_uid == task_uid and task.status == "done"),
            None,
        )
        inventory_refs = _task_inventory_artifact_refs(producer) if producer is not None else []
        references.extend(inventory_refs or [reference])
    references = list(dict.fromkeys(references))
    if not references:
        raise ValueError("snapshot proposal requires snapshot_refs")
    inventory_refs = [reference for reference in references if _inventory_snapshot(reference) is not None]
    if inventory_refs and (len(inventory_refs) != 1 or len(references) != 1):
        raise ValueError(
            "inventory snapshot proposal requires exactly one canonical inventory snapshot; "
            f"eligible_refs={inventory_refs}"
        )
    return proposal.model_copy(update={"snapshot_refs": references})


def _inventory_snapshot(reference: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """Return a validated inventory snapshot, or None for another snapshot type."""

    if not reference.startswith("artifact:"):
        return None
    try:
        return _load_inventory_manifest(reference)
    except ValueError:
        return None


def _proposal_inventory_snapshot(proposal: TaskProposal) -> Optional[Tuple[Dict[str, Any], str]]:
    if proposal.inferred_basis_kind != "snapshot" or len(proposal.snapshot_refs) != 1:
        return None
    return _inventory_snapshot(proposal.snapshot_refs[0])


def _normalized_route(value: str) -> Optional[Tuple[str, str]]:
    """Return a stable route key and display URL for an absolute HTTP(S) value."""

    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    netloc = parsed.netloc.lower()
    route_url = urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))
    return route_url, route_url


_PROPOSAL_RELATIVE_ENDPOINT_PATTERN = re.compile(
    r"(?:\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+|"
    r"\b(?:endpoint|route|path)\s*(?:is|at|:)?\s*)"
    r"(?P<path>/[^\s\"'<>`]+)",
    re.IGNORECASE,
)
_PROPOSAL_WORKFLOW_WORDING_PATTERN = re.compile(r"\b(?:workflow|flow)\b", re.IGNORECASE)


def _normalized_http_route_path(value: str) -> Optional[str]:
    """Return a normalized HTTP route path without scheme, authority, or query."""

    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return path.rstrip("/") or "/"


def _procedure_proposal_endpoint_routes(
    proposal: TaskProposal,
    selected_targets: List[OperationTarget],
) -> List[str]:
    """Return distinct HTTP endpoint routes explicitly named by a procedure proposal."""

    http_targets = [
        target
        for target in selected_targets
        if (parsed := urlsplit(str(target.value).strip())).scheme.lower() in {"http", "https"}
        and parsed.netloc
    ]
    if not http_targets:
        return []

    text = _proposal_scope_text(proposal)
    routes = set()
    for reference in _explicit_service_references(
        text,
        include_bare_host_ports=False,
        assigned_targets=http_targets,
    ):
        normalized = _normalized_http_route_path(reference)
        if normalized is not None:
            routes.add(normalized)
    for match in _PROPOSAL_RELATIVE_ENDPOINT_PATTERN.finditer(text):
        raw_path = match.group("path").rstrip(".,;)]}")
        if not raw_path:
            continue
        normalized = re.sub(r"/{2,}", "/", urlsplit(raw_path).path or "/")
        routes.add(normalized.rstrip("/") or "/")
    return sorted(routes)


def _validate_procedure_proposal_route_atomicity(
    proposal: TaskProposal,
    selected_targets: List[OperationTarget],
) -> None:
    """Reject accidental multi-route HTTP work while allowing declared workflows."""

    if proposal.inferred_basis_kind != "procedure":
        return
    routes = _procedure_proposal_endpoint_routes(proposal, selected_targets)
    if len(routes) <= 1 or _PROPOSAL_WORKFLOW_WORDING_PATTERN.search(_proposal_scope_text(proposal)):
        return
    raise ValueError(
        "procedure proposal contains multiple distinct endpoint routes: "
        + ", ".join(routes)
        + ". Resubmit separate route-scoped proposals. Use workflow or flow wording only for a genuine ordered "
        "multi-step workflow."
    )


def _coverage_route_groups(
    manifest: Dict[str, Any],
    *,
    prompt_token_limit: int,
) -> List[Tuple[str, str, str, List[str]]]:
    """Group typed inventory units without representing non-endpoints as routes."""

    char_cap = max(1_000, int(prompt_token_limit or 48_000) * 4 // 5)
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    endpoints_by_id = {
        str(item["id"]): item
        for item in manifest["items"]
        if item["kind"] == "endpoint"
    }
    endpoint_route_keys = {
        (str(item["target_id"]), route[0])
        for item in endpoints_by_id.values()
        if (route := _normalized_route(str(item["value"]))) is not None
    }
    for item in manifest["items"]:
        item_id = str(item["id"])
        kind = str(item["kind"])
        if kind == "parameter":
            endpoint_id = str(item.get("attributes", {}).get("endpoint_id", ""))
            endpoint = endpoints_by_id.get(endpoint_id)
            route = _normalized_route(str(endpoint["value"])) if endpoint else None
            if route is None:
                parameter_route = _normalized_route(str(item["value"]))
                if parameter_route and (str(item["target_id"]), parameter_route[0]) in endpoint_route_keys:
                    # Older recon manifests may encode a parameter as a URL with
                    # a query string instead of recording endpoint_id.
                    route = parameter_route
            if route is None:
                # A parameter without an owning endpoint is useful inventory metadata,
                # but cannot be assessed safely as an independent network target.
                continue
        else:
            route = _normalized_route(str(item["value"])) if kind == "endpoint" else None
        if route is not None:
            route_key, label = route
            group_key = (str(item["target_id"]), f"route:{route_key}")
            task_kind = "endpoint"
        else:
            group_key = (str(item["target_id"]), f"{kind}:{item_id}")
            label = str(item["value"])
            task_kind = kind
        group = groups.setdefault(
            group_key,
            {"label": label, "kind": task_kind, "ids": [], "chars": 0},
        )
        group["ids"].append(item_id)
        group["chars"] += len(json.dumps(item, sort_keys=True, separators=(",", ":")))
        if group["chars"] > char_cap:
            raise ValueError(f"Inventory route exceeds the resolved context safety limit: {group['label']}")
    return [
        (target_id, str(group["kind"]), str(group["label"]), list(group["ids"]))
        for (target_id, _group_key), group in groups.items()
    ]


def _completed_coverage_item_ids(existing_tasks: List[Task], snapshot_hash: str, phase: int) -> set[str]:
    completed: set[str] = set()
    store = _get_database_store()
    list_results = getattr(store, "list_task_acceptance_results", None)
    for task in existing_tasks:
        if (
            task.phase != phase
            or task.acceptance.mode != "coverage"
            or task.acceptance.basis.snapshot_hash != snapshot_hash
        ):
            continue
        results = list_results(task.task_uid) if callable(list_results) else store.get_acceptance_results(
            _operation_id(), task.task_uid
        )
        for result in results:
            completed.update(item.item_id for item in result.coverage)
    return completed


def _frozen_task_identity(
    title: str,
    objective: str,
    acceptance: AcceptanceContract,
    target_scope: TargetScope,
    target_ids: List[str],
    phase: int,
) -> str:
    """Return the deterministic work identity used to deduplicate compiled tasks."""

    acceptance_identity: Dict[str, Any]
    if acceptance.mode == "coverage":
        acceptance_identity = {
            "basis_kind": acceptance.basis.kind,
            "item_ids": sorted(acceptance.basis.item_ids),
            "snapshot_hash": acceptance.basis.snapshot_hash,
            "source_refs": sorted(acceptance.basis.source_refs),
            "criteria": [criterion.to_dict() for criterion in acceptance.criteria],
            "objective": objective.strip(),
        }
    else:
        acceptance_identity = {
            "mode": acceptance.mode,
            "basis": acceptance.basis.to_dict(),
            "criteria": [criterion.to_dict() for criterion in acceptance.criteria],
            "objective": objective.strip(),
            "title": title.strip(),
        }
    return json.dumps(
        {
            "acceptance": acceptance_identity,
            "target_ids": sorted(target_ids),
            "target_scope": target_scope,
            "phase": phase,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _semantic_cross_phase_task_identity(
    title: str,
    objective: str,
    acceptance: AcceptanceContract,
    target_scope: TargetScope,
    target_ids: List[str],
) -> str:
    """Return an exact work identity while ignoring controller-owned phase references."""

    acceptance_identity = acceptance.to_dict()
    acceptance_identity.pop("frozen_at", None)
    acceptance_identity.pop("manifest_hash", None)
    basis = acceptance_identity.get("basis", {})
    basis["source_refs"] = [
        reference
        for reference in basis.get("source_refs", [])
        if not str(reference).startswith("plan:phase-")
    ]
    return json.dumps(
        {
            "title": title.strip(),
            "objective": objective.strip(),
            "acceptance": acceptance_identity,
            "target_ids": sorted(target_ids),
            "target_scope": target_scope,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _create_tasks_from_proposals(
    tasks: TaskProposalList,
    *,
    prompt_token_limit: int,
    coverage_item_ids: Optional[set[str]] = None,
    expected_snapshot_ref: Optional[str] = None,
    phase_title: str = "",
    phase_objective: str = "",
    required_finding_refs: Optional[set[str]] = None,
    finding_ref_aliases: Optional[Dict[str, str]] = None,
    phase_task_contract: Any = None,
    proposal_preflight_validator: Optional[Callable[[List[TaskProposal]], None]] = None,
    reject_duplicate_proposals: bool = False,
) -> str:
    """Create pending tasks for the active phase from concise task proposals.

    Procedure example:
    {"tasks": [{"title": "Build surface inventory", "objective": "Map the assigned target",
    "basis_description": "Bounded crawl", "methods": ["crawl"], "limits": {"max_requests": 500},
    "snapshot_refs": [],
    "output_kind": "inventory_manifest", "criteria": [{"description":
    "Execute the bounded procedure and store its manifest"}], "target_ids": ["target-1"]}]}

    Python assigns the active phase and pending status, infers target scope, and compiles the proposal into an
    immutable acceptance contract. Criterion IDs, procedure stop conditions, gap policy, output kind, and source
    references are deterministic. Inventory snapshot proposals become route-scoped coverage tasks.
    """

    if not tasks:
        raise ValueError("must have at least one task")
    try:
        proposals = TypeAdapter(TaskProposalList).validate_python(tasks)
    except ValidationError as error:
        raise ValueError(_compact_task_proposal_validation_error(error)) from error
    if phase_task_contract is not None:
        from modules.operation_plugins.planning_contracts import validate_phase_task_proposals

        validate_phase_task_proposals(phase_task_contract, proposals)
    if proposal_preflight_validator is not None:
        proposal_preflight_validator(proposals)
    if coverage_item_ids is not None and len(proposals) != 1:
        raise ValueError(
            "controller-bound inventory batch requires exactly one snapshot proposal; "
            "Python expands that proposal across every assigned route group"
        )

    client = _ensure_memory_client()
    user_id = _user_id()
    op_id = _operation_id()
    plan = _get_active_plan()
    current_phase = plan.current_phase

    existing_tasks = _get_database_store().get_tasks(op_id)
    staged_tasks: List[Task] = []
    duplicate_count = 0
    snapshot_exhausted = False
    unresolved_gaps: List[str] = []

    for proposal_index, proposal in enumerate(proposals):
        proposal = _resolve_proposal_snapshot_refs(proposal, existing_tasks)
        title = proposal.title.strip()
        objective = proposal.objective.strip()
        finding_refs = list(dict.fromkeys(
            str(reference).strip() for reference in proposal.finding_refs if str(reference).strip()
        ))
        if required_finding_refs is not None:
            aliases = finding_ref_aliases or {}
            canonical_by_bare_ref = {
                reference.removeprefix("finding:"): reference
                for reference in required_finding_refs
                if reference.startswith("finding:")
            }
            if not finding_refs and len(required_finding_refs) == 1:
                finding_refs = [next(iter(required_finding_refs))]
            finding_refs = [
                aliases.get(reference, canonical_by_bare_ref.get(reference, reference))
                for reference in finding_refs
            ]
            finding_refs = list(dict.fromkeys(finding_refs))
            if not finding_refs:
                raise ValueError("finding-dependent task proposal requires at least one canonical finding_refs entry")
            invalid_finding_refs = sorted(set(finding_refs) - required_finding_refs)
            if invalid_finding_refs:
                raise ValueError(
                    "finding-dependent task proposal includes unavailable finding_refs: "
                    + ", ".join(invalid_finding_refs)
                )
        replacement_of = str(proposal.replacement_of or "").strip() or None
        supersedes_criteria = list(dict.fromkeys(
            str(criterion_id).strip() for criterion_id in proposal.supersedes_criteria if str(criterion_id).strip()
        ))
        if replacement_of is not None:
            parent = next((task for task in existing_tasks if task.task_uid == replacement_of), None)
            if parent is None:
                raise ValueError(f"replacement_of references unknown task {replacement_of}")
            if parent.phase != current_phase:
                raise ValueError("replacement task must remain in the parent task's phase")
            if parent.status not in {"partial_failure", "blocked"}:
                raise ValueError("replacement_of must reference a partial_failure or blocked task")
            parent_criteria = {criterion.id for criterion in parent.acceptance.criteria}
            if not supersedes_criteria or not set(supersedes_criteria).issubset(parent_criteria):
                allowed_criteria = ", ".join(sorted(parent_criteria)) or "none"
                raise ValueError(
                    "supersedes_criteria must identify existing acceptance criteria on the parent task; "
                    f"allowed criterion IDs: {allowed_criteria}"
                )
        elif supersedes_criteria:
            raise ValueError("supersedes_criteria requires replacement_of")
        proposal_created_count = 0
        proposal_duplicate_count = 0
        target_scope, target_ids = _validate_task_target_scope(
            target_ids=_normalize_target_ids(proposal.target_ids),
            plan=plan,
            proposal=proposal,
        )
        selected_targets = [
            target for target in plan.targets if not target_ids or target.target_id in set(target_ids)
        ]
        planning_context = {}
        if proposal_preflight_validator is not None:
            planning_context["task_preflight_validated"] = True
        if phase_task_contract is not None:
            planning_context["phase_task_contract"] = {
                "module": phase_task_contract.module,
                "phase_id": phase_task_contract.phase_id,
                "workstream": proposal.workstream,
                "task_role": proposal.task_role,
                "depends_on_workstreams": list(proposal.depends_on_workstreams),
                "inapplicability_reason": proposal.inapplicability_reason,
            }
        _validate_procedure_proposal_route_atomicity(proposal, selected_targets)
        acceptance = _freeze_and_validate_acceptance(
            _proposal_acceptance_contract(proposal, plan),
            [*existing_tasks, *staged_tasks],
        )
        if coverage_item_ids is not None and acceptance.mode != "coverage":
            raise ValueError("task-creation batch requires a snapshot-based proposal")
        if coverage_item_ids is not None and phase_objective and _is_generic_snapshot_proposal(proposal):
            raise ValueError(
                "snapshot proposal must describe the active phase's distinct objective; "
                "generic endpoint assessment is not allowed"
            )

        acceptance_groups = [(title, objective, acceptance, target_scope, target_ids)]
        if acceptance.mode == "coverage":
            artifact_ref = next(ref for ref in acceptance.basis.source_refs if ref.startswith("artifact:"))
            if expected_snapshot_ref is not None and artifact_ref != expected_snapshot_ref:
                raise ValueError(
                    "task-creation batch requires snapshot reference "
                    f"{expected_snapshot_ref}; received {artifact_ref}"
                )
            manifest, snapshot_hash = _load_inventory_manifest(artifact_ref)
            completed_ids = _completed_coverage_item_ids(existing_tasks, snapshot_hash, current_phase)
            route_groups = []
            title_prefixes = {
                "endpoint": "Assess endpoint",
                "workflow": "Assess workflow",
                "service": "Assess service",
                "technology": "Validate technology",
                "parameter": "Assess parameter",
            }
            proposal_intent = "; ".join(
                criterion.description.strip() for criterion in proposal.criteria
            )
            for group_target_id, group_kind, route_label, item_ids in _coverage_route_groups(
                manifest,
                prompt_token_limit=prompt_token_limit,
            ):
                if coverage_item_ids is not None:
                    group_ids = set(item_ids)
                    if group_ids.isdisjoint(coverage_item_ids):
                        continue
                    if not group_ids.issubset(coverage_item_ids):
                        raise ValueError(
                            "task-creation batch split an atomic inventory route group: "
                            f"{route_label}"
                        )
                remaining_ids = [item_id for item_id in item_ids if item_id not in completed_ids]
                if not remaining_ids:
                    continue
                group_acceptance = AcceptanceContract(
                    mode="coverage",
                    basis=AcceptanceBasis(
                        kind="snapshot",
                        description=f"Frozen inventory entries for {route_label}",
                        source_refs=acceptance.basis.source_refs,
                        snapshot_hash=acceptance.basis.snapshot_hash,
                        item_ids=remaining_ids,
                    ),
                    criteria=[_phase_specific_coverage_criterion(
                        group_kind,
                        proposal_intent,
                        phase_title,
                        phase_objective,
                        route_label,
                        remaining_ids,
                    )],
                )
                phase_label = str(phase_title or title).strip()
                phase_work = _route_scoped_phase_objective(phase_objective) or objective.strip()
                group_title = f"{title_prefixes[group_kind]} {route_label} [{group_target_id}]"
                group_objective = (
                    f"{objective.rstrip('.')} Scope this objective to {route_label} and its frozen inventory entries."
                )
                if phase_title or phase_objective:
                    group_title = f"{phase_label}: {group_title}"
                    group_objective = (
                        f"For assigned route {route_label} and frozen item IDs {', '.join(remaining_ids)}, "
                        f"perform this phase-specific work: {phase_work.rstrip('.')}. "
                        "Produce an evidence-backed terminal disposition for this assigned route only."
                    )
                route_groups.append((
                    group_title,
                    group_objective,
                    group_acceptance,
                    "subset",
                    [group_target_id],
                ))
            acceptance_groups = route_groups
            if not acceptance_groups and coverage_item_ids is not None:
                snapshot_exhausted = True
                logger.info(
                    "Task proposal batch exhausted proposal_index=%d title=%s assigned_item_count=%d",
                    proposal_index,
                    title,
                    len(coverage_item_ids),
                )
                continue
            if not acceptance_groups:
                snapshot_exhausted = True
                unresolved_gaps.extend(str(gap).strip() for gap in manifest["unassessed_gaps"] if str(gap).strip())
                if unresolved_gaps:
                    if any(
                        task.title == "Refine exhausted inventory"
                        and task.status in {"active", "pending"}
                        for task in [*existing_tasks, *staged_tasks]
                    ):
                        duplicate_count += 1
                        proposal_duplicate_count += 1
                        logger.info(
                            "Task proposal fan-out proposal_index=%d title=%s expanded_count=1 "
                            "created_count=0 duplicate_count=1",
                            proposal_index,
                            title,
                        )
                        continue
                    refinement_target_ids = target_ids or [target.target_id for target in plan.targets]
                    refinement_acceptance = AcceptanceContract(
                        mode="outcome",
                        basis=AcceptanceBasis(
                            kind="procedure",
                            description="Resolve the explicit gaps in the exhausted frozen inventory",
                            source_refs=[
                                *(f"target:{target_id}" for target_id in refinement_target_ids),
                                f"plan:phase-{current_phase}",
                            ],
                            procedure=DiscoveryProcedure(
                                methods=["crawl", "targeted_discovery"],
                                limits={"max_requests": 500, "max_depth": 4},
                                stop_condition="first_limit_reached",
                                gap_policy="record_unassessed",
                                output_kind="inventory_manifest",
                            ),
                        ),
                        criteria=[AcceptanceCriterion(
                            id="refine-exhausted-inventory",
                            description="Store a replacement finite inventory that addresses each explicit gap.",
                            evidence_requirements=[EvidenceRequirement(kind="inventory_manifest", min_count=1)],
                        )],
                    )
                    acceptance_groups = [(
                        "Refine exhausted inventory",
                        "Perform bounded follow-up discovery for these unresolved gaps: "
                        + "; ".join(dict.fromkeys(unresolved_gaps)),
                        refinement_acceptance,
                        target_scope,
                        target_ids,
                    )]
                else:
                    duplicate_count += 1
                    proposal_duplicate_count += 1
                    logger.info(
                        "Task proposal fan-out proposal_index=%d title=%s expanded_count=0 "
                        "created_count=0 duplicate_count=1",
                        proposal_index,
                        title,
                    )
                    continue

        proposal_expanded_count = len(acceptance_groups)
        for group_title, group_objective, group_acceptance, group_target_scope, group_target_ids in acceptance_groups:
            group_identity = _frozen_task_identity(
                group_title,
                group_objective,
                group_acceptance,
                group_target_scope,
                group_target_ids,
                current_phase,
            )
            if any(
                _frozen_task_identity(
                    task.title,
                    task.objective,
                    task.acceptance,
                    task.target_scope,
                    task.target_ids,
                    task.phase,
                ) == group_identity
                for task in [*existing_tasks, *staged_tasks]
                if task.status in {"active", "pending", "done"}
            ):
                duplicate_count += 1
                proposal_duplicate_count += 1
                logger.info("Skipped duplicate task proposal after frozen acceptance expansion: %s", group_title)
                continue
            cross_phase_identity = _semantic_cross_phase_task_identity(
                group_title,
                group_objective,
                group_acceptance,
                group_target_scope,
                group_target_ids,
            )
            if any(
                _semantic_cross_phase_task_identity(
                    task.title,
                    task.objective,
                    task.acceptance,
                    task.target_scope,
                    task.target_ids,
                ) == cross_phase_identity
                for task in [*existing_tasks, *staged_tasks]
                if task.status in {"active", "pending", "done"}
            ):
                duplicate_count += 1
                proposal_duplicate_count += 1
                logger.info("Skipped semantic cross-phase duplicate task proposal: %s", group_title)
                continue
            staged_tasks.append(Task(
                task_uid=str(uuid.uuid4()),
                title=group_title,
                objective=group_objective,
                acceptance=group_acceptance,
                evidence=finding_refs,
                phase=current_phase,
                status="pending",
                target_scope=group_target_scope,
                target_ids=group_target_ids,
                replacement_of=replacement_of,
                supersedes_criteria=supersedes_criteria,
                recovery_context=planning_context,
            ))
            proposal_created_count += 1
        logger.info(
            "Task proposal fan-out proposal_index=%d title=%s objective=%s expanded_count=%d "
            "created_count=%d duplicate_count=%d",
            proposal_index,
            title,
            objective,
            proposal_expanded_count,
            proposal_created_count,
            proposal_duplicate_count,
        )

    if reject_duplicate_proposals and duplicate_count:
        raise ValueError(
            "task_preflight:duplicate_task: generated proposal duplicates existing durable workflow work"
        )

    for task in staged_tasks:
        client.store_task(task=task, user_id=user_id)

    response: Dict[str, Any] = {
        "complete": True,
        "created_count": len(staged_tasks),
        "duplicate_count": duplicate_count,
    }
    if snapshot_exhausted:
        response["snapshot_exhausted"] = True
        response["unresolved_gaps"] = list(dict.fromkeys(unresolved_gaps))
    return json.dumps(response, sort_keys=True)


@tool(inputSchema=_TASK_PROPOSAL_INPUT_SCHEMA)
def create_tasks(tasks: TaskProposalList) -> str:
    """Create pending tasks from concise proposals.

    Procedure example:
    {"tasks": [{"title": "Build surface inventory", "objective": "Map the assigned target",
    "methods": ["crawl"], "limits": {"max_requests": 500}, "snapshot_refs": [],
    "output_kind": "inventory_manifest",
    "criteria": [{"description": "Store the finite inventory"}], "target_ids": ["target-1"]}]}

    Snapshot example:
    {"tasks": [{"title": "Assess frozen inventory", "objective": "Assess each frozen inventory unit",
    "methods": [], "limits": {}, "snapshot_refs": ["artifact:artifacts/inventory.json"],
    "criteria": [{"description": "Assess the assigned frozen inventory unit"}],
    "target_ids": ["target-1"]}]}

    Python infers the basis kind, defaults basis_description to objective, assigns phase and status, and compiles
    criterion IDs, evidence requirements, source references, route-scoped coverage tasks, and procedure policies.
    Snapshot limits and output_kind are ignored because they do not apply.
    """

    prompt_token_limit = int((_MEMORY_CONFIG or {}).get("prompt_token_limit") or 48_000)
    return _create_tasks_from_proposals(tasks, prompt_token_limit=prompt_token_limit)


def build_create_tasks_tool(
    prompt_token_limit: int = 48_000,
    *,
    coverage_item_ids: Optional[set[str]] = None,
    expected_snapshot_ref: Optional[str] = None,
    phase_title: str = "",
    phase_objective: str = "",
    required_finding_refs: Optional[set[str]] = None,
    finding_ref_aliases: Optional[Dict[str, str]] = None,
    phase_task_contract: Any = None,
    proposal_preflight_validator: Optional[Callable[[List[TaskProposal]], None]] = None,
    reject_duplicate_proposals: bool = False,
    repair_guard: Optional[TaskProposalRepairGuard] = None,
    invocation_observer: Optional[Callable[[Dict[str, Any], Any, Optional[Exception]], None]] = None,
) -> Any:
    """Build a task-creator-local tool that permits exactly one successful mutation."""

    completed = False

    @tool(name="create_tasks", inputSchema=_TASK_PROPOSAL_INPUT_SCHEMA)
    def create_tasks_once(tasks: TaskProposalList) -> str:
        """Create the active phase's durable tasks and stop after the first successful call."""

        nonlocal completed
        tool_input = {"tasks": tasks}
        try:
            if completed:
                raise ValueError("Task creation already completed for this role run")
            if repair_guard is not None:
                tasks = repair_guard.restore(tasks)
                tool_input = {"tasks": tasks}
            result = _create_tasks_from_proposals(
                tasks,
                prompt_token_limit=prompt_token_limit,
                coverage_item_ids=coverage_item_ids,
                expected_snapshot_ref=expected_snapshot_ref,
                phase_title=phase_title,
                phase_objective=phase_objective,
                required_finding_refs=required_finding_refs,
                finding_ref_aliases=finding_ref_aliases,
                phase_task_contract=phase_task_contract,
                proposal_preflight_validator=proposal_preflight_validator,
                reject_duplicate_proposals=reject_duplicate_proposals,
            )
        except Exception as error:
            if invocation_observer is not None:
                invocation_observer(tool_input, None, error)
            raise
        if invocation_observer is not None:
            invocation_observer(tool_input, result, None)
        if int(json.loads(result).get("created_count", 0)) > 0:
            completed = True
        return result

    create_tasks_once.__name__ = "create_tasks"
    return create_tasks_once


def _evidence_reference_kind(reference: str, expected_kind: EvidenceRequirementKind) -> bool:
    op_id = _operation_id()
    if reference.startswith("artifact:"):
        if expected_kind == "durable_evidence":
            _artifact_path_from_ref(reference)
            return True
        if expected_kind not in {"artifact", "inventory_manifest"}:
            return False
        if expected_kind == "inventory_manifest":
            _load_inventory_manifest(reference)
        else:
            _artifact_path_from_ref(reference)
        return True
    if reference.startswith("memory:"):
        memory_id = reference.split(":", 1)[1]
        memory = _ensure_memory_client().get_memory_by_id(memory_id)
        metadata = (memory or {}).get("metadata", {}) if isinstance(memory, dict) else {}
        if not memory or metadata.get("operation_id", op_id) != op_id:
            raise _acceptance_evidence_memory_error(reference)
        category = str(metadata.get("category", ""))
        return expected_kind in {"memory", "durable_evidence"} or (
            expected_kind == "observation" and category == "observation"
        )
    if reference.startswith("finding:"):
        finding_uid = reference.split(":", 1)[1]
        record = _get_database_store().get_finding(op_id, finding_uid)
        if record is None:
            raise ValueError(f"Acceptance evidence finding does not exist: {reference}")
        if expected_kind in {"finding_candidate", "durable_evidence"}:
            return True
        return expected_kind == "verified_finding" and record.get("resolution") == "verified"
    raise _acceptance_evidence_reference_error()


def _validate_acceptance_result_evidence(
    task: Task,
    criterion: AcceptanceCriterion,
    result: AcceptanceResult,
) -> None:
    references = list(result.evidence_refs)
    for coverage_item in result.coverage:
        references.extend(coverage_item.evidence_refs)
    references = list(dict.fromkeys(references))
    for reference in references:
        if reference.startswith("artifact:"):
            try:
                _artifact_path_from_ref(reference)
            except ValueError as error:
                requires_inventory = any(
                    requirement.kind == "inventory_manifest" for requirement in criterion.evidence_requirements
                )
                if requires_inventory and "does not exist" in str(error).lower():
                    raise ValueError(
                        "The submitted manifest file does not exist. Do not retry acceptance. Create a validated "
                        "inventory manifest first, then submit its returned artifact reference."
                    ) from error
                raise
        elif reference.startswith("memory:"):
            _evidence_reference_kind(reference, "memory")
        elif reference.startswith("finding:"):
            finding_uid = reference.split(":", 1)[1]
            if _get_database_store().get_finding(_operation_id(), finding_uid) is None:
                raise ValueError(f"Acceptance evidence finding does not exist: {reference}")
        else:
            raise _acceptance_evidence_reference_error()
    for requirement in criterion.evidence_requirements:
        if requirement.kind == "inventory_manifest":
            matching = 0
            rejected = []
            for reference in references:
                if not _is_inventory_manifest_candidate(reference):
                    continue
                try:
                    _load_inventory_manifest(reference, reconcile=task.acceptance.basis.kind == "procedure")
                except ValueError as error:
                    rejected.append(f"{reference}: {error}")
                else:
                    matching += 1
            if matching < requirement.min_count:
                if rejected:
                    diagnostic = f" Rejected candidates: {'; '.join(rejected)}."
                else:
                    diagnostic = (
                        " No inventory-manifest candidate artifact reference was supplied; "
                        "generic artifact evidence is not counted for this requirement."
                    )
                raise ValueError(
                    f"Acceptance criterion {criterion.id} requires {requirement.min_count} "
                    f"inventory_manifest evidence reference(s); received {matching}.{diagnostic} "
                    "Repair the identified item fields in the same artifact, preserve its artifact:artifacts/... "
                    "reference, and resubmit. Required root fields: schema_version=1, non-empty items, and "
                    "unassessed_gaps list."
                )
            continue
        matching = sum(
            1 for reference in references if _evidence_reference_kind(reference, requirement.kind)
        )
        if matching < requirement.min_count:
            raise ValueError(
                f"Acceptance criterion {criterion.id} requires {requirement.min_count} "
                f"{requirement.kind} evidence reference(s); received {matching}"
            )
    if task.acceptance.mode != "coverage":
        if result.coverage:
            raise ValueError("coverage results are allowed only for coverage acceptance mode")
        return
    artifact_ref = next(
        reference for reference in task.acceptance.basis.source_refs if reference.startswith("artifact:")
    )
    manifest, snapshot_hash = _load_inventory_manifest(artifact_ref)
    if snapshot_hash != task.acceptance.basis.snapshot_hash:
        raise ValueError("Acceptance basis inventory manifest changed after task creation")
    expected_ids = set(task.acceptance.basis.item_ids) or {str(item["id"]) for item in manifest["items"]}
    actual_ids = {item.item_id for item in result.coverage}
    if expected_ids != actual_ids:
        missing = sorted(expected_ids - actual_ids)
        unknown = sorted(actual_ids - expected_ids)
        raise ValueError(f"Coverage ledger mismatch; missing={missing}, unknown={unknown}")


def _acceptance_evidence_relevance_error(task: Task, result: AcceptanceResult) -> str:
    """Reject a manifest used as proof for one frozen inventory endpoint.

    This is intentionally based on the inventory item kind and frozen item IDs rather
    than HTTP URL syntax, so inventories from other modules retain the same guard.
    """

    if task.acceptance.mode != "coverage" or task.acceptance.basis.kind != "snapshot":
        return ""
    expected_ids = set(task.acceptance.basis.item_ids)
    if not expected_ids:
        return ""
    source_endpoint_ids = set()
    for source_ref in task.acceptance.basis.source_refs:
        try:
            manifest, _snapshot_hash = _load_inventory_manifest(source_ref)
        except ValueError:
            continue
        source_endpoint_ids.update(
            str(item.get("id") or "")
            for item in manifest.get("items", [])
            if isinstance(item, dict) and str(item.get("kind") or "") == "endpoint"
        )
    if not source_endpoint_ids.intersection(expected_ids):
        return ""
    inventory_refs = []
    for reference in result.evidence_refs:
        try:
            _load_inventory_manifest(reference)
        except ValueError:
            continue
        inventory_refs.append(reference)
    if not inventory_refs:
        return ""
    return (
        "Acceptance evidence is not relevant to the frozen inventory subject. "
        "An inventory manifest cannot prove a single endpoint assessment: "
        + ", ".join(sorted(set(inventory_refs)))
        + ". Do not retry acceptance with this manifest. Retain or create durable evidence for the assigned "
        "subject, then submit acceptance with that returned reference."
    )


def _snapshot_task_artifact_reference(task: Task, reference: str) -> str:
    """Copy acceptance artifacts to immutable, task-owned current-operation storage."""

    if not reference.startswith("artifact:"):
        return reference
    try:
        source = Path(_artifact_path_from_ref(reference))
    except ValueError as error:
        raise TaskEvidenceSnapshotVerificationError(
            "TASK_EVIDENCE_SNAPSHOT_SOURCE_UNAVAILABLE: source artifact is unavailable "
            f"task_uid={task.task_uid} source_reference={reference}"
        ) from error
    operation_root = Path(_operation_output_root())
    task_segment = hashlib.sha256(task.task_uid.encode("utf-8")).hexdigest()[:16]
    destination_dir = operation_root / "task_evidence" / task_segment
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise TaskEvidenceSnapshotVerificationError(
            "TASK_EVIDENCE_SNAPSHOT_DESTINATION_UNVERIFIABLE: unable to prepare snapshot destination "
            f"task_uid={task.task_uid} operation_root={operation_root} destination_dir={destination_dir}"
        ) from error
    try:
        source_content = source.read_bytes()
    except OSError as error:
        raise TaskEvidenceSnapshotVerificationError(
            "TASK_EVIDENCE_SNAPSHOT_SOURCE_UNAVAILABLE: unable to prepare source snapshot "
            f"task_uid={task.task_uid} operation_root={operation_root} source_reference={reference}"
        ) from error
    digest = hashlib.sha256(source_content).hexdigest()[:16]
    destination = destination_dir / f"{source.stem}-{digest}{source.suffix}"
    logger.info(
        "Creating task-evidence snapshot task_uid=%s source=%s operation_root=%s destination=%s",
        task.task_uid,
        reference,
        operation_root,
        destination,
    )
    if not destination.exists():
        try:
            shutil.copy2(source, destination)
        except OSError as error:
            raise TaskEvidenceSnapshotVerificationError(
                "TASK_EVIDENCE_SNAPSHOT_DESTINATION_UNVERIFIABLE: unable to copy snapshot "
                f"task_uid={task.task_uid} operation_root={operation_root} destination={destination}"
            ) from error
        try:
            if not destination.is_file():
                raise TaskEvidenceSnapshotVerificationError(
                    "TASK_EVIDENCE_SNAPSHOT_DESTINATION_UNVERIFIABLE: copied snapshot is not a regular file "
                    f"task_uid={task.task_uid} operation_root={operation_root} destination={destination}"
                )
            copied_digest = hashlib.sha256(destination.read_bytes()).hexdigest()[:16]
        except OSError as error:
            raise TaskEvidenceSnapshotVerificationError(
                "TASK_EVIDENCE_SNAPSHOT_DESTINATION_UNVERIFIABLE: unable to read copied snapshot "
                f"task_uid={task.task_uid} operation_root={operation_root} destination={destination}"
            ) from error
        if copied_digest != digest:
            raise TaskEvidenceSnapshotVerificationError(
                "TASK_EVIDENCE_SNAPSHOT_DESTINATION_UNVERIFIABLE: copied snapshot digest mismatch "
                f"task_uid={task.task_uid} operation_root={operation_root} destination={destination}"
            )
    try:
        return canonical_artifact_reference(str(destination))
    except ValueError as error:
        raise TaskEvidenceSnapshotVerificationError(
            "TASK_EVIDENCE_SNAPSHOT_DESTINATION_UNVERIFIABLE: copied snapshot is unavailable "
            f"task_uid={task.task_uid} operation_root={operation_root} destination={destination}"
        ) from error


def _snapshot_task_acceptance_artifacts(task: Task, results: List[AcceptanceResult]) -> List[AcceptanceResult]:
    """Return ledger results whose artifact evidence cannot be overwritten by later tasks."""

    references: Dict[str, str] = {}

    def snapshot(reference: str) -> str:
        if reference not in references:
            references[reference] = _snapshot_task_artifact_reference(task, reference)
        return references[reference]

    return [
        AcceptanceResult(
            criterion_id=result.criterion_id,
            status=result.status,
            disposition=result.disposition,
            summary=result.summary,
            evidence_refs=tuple(snapshot(reference) for reference in result.evidence_refs),
            coverage=tuple(
                CoverageResult(
                    item_id=item.item_id,
                    status=item.status,
                    evidence_refs=tuple(snapshot(reference) for reference in item.evidence_refs),
                )
                for item in result.coverage
            ),
        )
        for result in results
    ]


_CONFIRMED_SECURITY_CLAIM = re.compile(
    r"\b(?:confirmed|demonstrated|verified|exploitable)\b.{0,80}"
    r"\b(?:vulnerabilit|injection|execution|inclusion|bypass|impact|exposure)\b|"
    r"\b(?:vulnerabilit|injection|execution|inclusion|bypass)\b.{0,80}"
    r"\b(?:confirmed|demonstrated|verified|exploitable)\b",
    re.IGNORECASE,
)
_NEGATED_SECURITY_CLAIM = re.compile(
    r"\b(?:not|no|without|failed to|unable to)\b.{0,40}"
    r"\b(?:confirm|demonstrat|verify|exploit|vulnerab)\w*\b",
    re.IGNORECASE,
)


def _validate_acceptance_disposition(result: AcceptanceResult) -> None:
    references = set(result.evidence_refs)
    finding_refs = {reference for reference in references if reference.startswith("finding:")}
    if result.disposition in {"finding_candidate", "existing_finding"}:
        if not finding_refs:
            raise ValueError(
                f"Acceptance disposition {result.disposition} requires a finding:<id> evidence reference"
            )
        for reference in finding_refs:
            _evidence_reference_kind(reference, "finding_candidate")
        return
    if _CONFIRMED_SECURITY_CLAIM.search(result.summary) and not _NEGATED_SECURITY_CLAIM.search(result.summary):
        raise ValueError(
            "Acceptance summaries that claim confirmed security behavior require finding_candidate or "
            "existing_finding disposition and a finding:<id> reference"
        )


def _record_task_acceptance(task_uid: str, results: List[AcceptanceResult]) -> str:
    """Persist acceptance results against the controller-selected task."""
    normalized_uid = str(task_uid or "").strip()
    if not normalized_uid:
        raise ValueError("task_uid required")
    if not results:
        raise ValueError("results requires at least one acceptance result")
    normalized_results = []
    for raw_result in results:
        result = AcceptanceResult.from_obj(raw_result)
        canonical_evidence = tuple(_canonical_evidence_reference(reference) for reference in result.evidence_refs)
        canonical_coverage = tuple(
            CoverageResult(
                item_id=item.item_id,
                status=item.status,
                evidence_refs=tuple(_canonical_evidence_reference(reference) for reference in item.evidence_refs),
            )
            for item in result.coverage
        )
        normalized_results.append(
            AcceptanceResult(
                criterion_id=result.criterion_id,
                status=result.status,
                disposition=result.disposition,
                summary=result.summary,
                evidence_refs=canonical_evidence,
                coverage=canonical_coverage,
            )
        )
    if len({result.criterion_id for result in normalized_results}) != len(normalized_results):
        raise ValueError("results must contain unique criterion_id values")

    op_id = _operation_id()
    store = _get_database_store()
    task = next((item for item in store.get_tasks(op_id) if item.task_uid == normalized_uid), None)
    if task is None:
        raise ValueError("Unknown task_uid for the current operation")
    if task.status != "active":
        raise ValueError("Acceptance results may be recorded only for the active task")
    known_ids = {criterion.id for criterion in task.acceptance.criteria}
    result_ids = {result.criterion_id for result in normalized_results}
    if result_ids != known_ids:
        missing_ids = sorted(known_ids - result_ids)
        unknown_ids = sorted(result_ids - known_ids)
        raise ValueError(f"Acceptance results must exactly match frozen criteria; missing={missing_ids}, unknown={unknown_ids}")

    for result in normalized_results:
        relevance_error = _acceptance_evidence_relevance_error(task, result)
        if relevance_error:
            raise ValueError(relevance_error)

    existing = store.get_acceptance_results(op_id, task.task_uid)
    if existing:
        if {result.criterion_id for result in existing} != known_ids:
            raise ValueError("Existing acceptance ledger is incomplete and cannot be modified")
        _store_task_acceptance_evidence(task, existing)
        memory_published, memory_created, memory_warning = _publish_task_acceptance_memory(
            task,
            existing,
        )
        return _task_acceptance_response(
            complete=True,
            recorded_count=len(existing),
            required_count=len(known_ids),
            replayed=True,
            memory_published=memory_published,
            memory_created=memory_created,
            memory_warning=memory_warning,
        )

    criteria = {criterion.id: criterion for criterion in task.acceptance.criteria}
    for result in normalized_results:
        _validate_acceptance_disposition(result)
        _validate_acceptance_result_evidence(task, criteria[result.criterion_id], result)

    normalized_results = _snapshot_task_acceptance_artifacts(task, normalized_results)

    store.store_acceptance_results(op_id, task.task_uid, normalized_results)
    recorded_results = store.get_acceptance_results(op_id, task.task_uid)
    expected_results = {
        result.criterion_id: json.dumps(result.to_dict(), sort_keys=True)
        for result in normalized_results
    }
    persisted_results = {
        result.criterion_id: json.dumps(result.to_dict(), sort_keys=True)
        for result in recorded_results
    }
    if persisted_results and persisted_results != expected_results:
        raise ValueError("Acceptance results were already recorded with conflicting immutable evidence")
    _store_task_acceptance_evidence(task, recorded_results)
    recorded_ids = {result.criterion_id for result in recorded_results}
    memory_published, memory_created, memory_warning = _publish_task_acceptance_memory(
        task,
        recorded_results,
    )
    return _task_acceptance_response(
        complete=recorded_ids == known_ids,
        recorded_count=len(recorded_ids),
        required_count=len(known_ids),
        replayed=False,
        memory_published=memory_published,
        memory_created=memory_created,
        memory_warning=memory_warning,
    )


def _task_acceptance_response(
    *,
    complete: bool,
    recorded_count: int,
    required_count: int,
    replayed: bool,
    memory_published: bool,
    memory_created: bool,
    memory_warning: str,
) -> str:
    payload: Dict[str, Any] = {
        "complete": complete,
        "recorded_count": recorded_count,
        "required_count": required_count,
        "replayed": replayed,
        "memory_published": memory_published,
        "memory_created": memory_created,
    }
    if memory_warning:
        payload["memory_warning"] = memory_warning
    return json.dumps(payload, sort_keys=True)


def _bounded_acceptance_memory_text(value: Any, limit: int) -> str:
    text = _clean_memory_text(value, "acceptance memory text")
    if len(text) <= limit:
        return text
    marker = "... [truncated]"
    return text[: limit - len(marker)].rstrip() + marker


def _task_acceptance_memory_payload(
    task: Task,
    results: List[AcceptanceResult],
) -> Tuple[str, Dict[str, Any], str]:
    """Build one bounded, deterministic downstream memory for an immutable acceptance ledger."""

    canonical_results = [result.to_dict() for result in sorted(results, key=lambda item: item.criterion_id)]
    result_digest = hashlib.sha256(
        json.dumps(canonical_results, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    publication_key = f"task_acceptance:{task.task_uid}:{result_digest}"
    sections = [
        f'Task acceptance for "{task.title}".',
        f"Objective: {_bounded_acceptance_memory_text(task.objective, TASK_ACCEPTANCE_MEMORY_SUMMARY_MAX_CHARS)}.",
    ]
    evidence_refs: List[str] = []
    status_metadata = []
    for result in sorted(results, key=lambda item: item.criterion_id):
        status_metadata.append(f"{result.criterion_id}:{result.status}:{result.disposition}")
        section = (
            f"Criterion {result.criterion_id} [{result.status}; {result.disposition}]: "
            f"{_bounded_acceptance_memory_text(result.summary, TASK_ACCEPTANCE_MEMORY_SUMMARY_MAX_CHARS)}"
        )
        if result.coverage:
            coverage_counts: Dict[str, int] = {}
            for item in result.coverage:
                coverage_counts[item.status] = coverage_counts.get(item.status, 0) + 1
            coverage_summary = ", ".join(
                f"{status}={count}" for status, count in sorted(coverage_counts.items())
            )
            section += f" Coverage: {coverage_summary}."
        sections.append(section)
        evidence_refs.extend(result.evidence_refs)
    unique_evidence = list(dict.fromkeys(evidence_refs))
    included_evidence = unique_evidence[:TASK_ACCEPTANCE_MEMORY_MAX_EVIDENCE_REFS]
    if included_evidence:
        sections.append("Evidence: " + ", ".join(included_evidence) + ".")
    omitted_evidence = len(unique_evidence) - len(included_evidence)
    if omitted_evidence:
        sections.append(f"Additional evidence references omitted: {omitted_evidence}.")
    content = _bounded_acceptance_memory_text(" ".join(sections), TASK_ACCEPTANCE_MEMORY_MAX_CHARS)
    metadata = {
        "source": "task_acceptance",
        "publication_key": publication_key,
        "task_uid": task.task_uid,
        "task_phase": task.phase,
        "acceptance_manifest_hash": task.acceptance.manifest_hash,
        "criterion_statuses": "|".join(status_metadata),
    }
    if task.target_ids:
        metadata["target_ids"] = "|".join(task.target_ids)
    return content, metadata, publication_key


def _publish_task_acceptance_memory(
    task: Task,
    results: List[AcceptanceResult],
) -> Tuple[bool, bool, str]:
    """Best-effort publish accepted task information as one replay-safe observation."""

    content, metadata, publication_key = _task_acceptance_memory_payload(task, results)
    store = _get_database_store()
    op_id = _operation_id()
    if store.has_acceptance_memory_publication(op_id, task.task_uid, publication_key):
        return True, False, ""
    try:
        created = _store_memory_entry(content, "observation", metadata).created
        store.mark_acceptance_memory_published(op_id, task.task_uid, publication_key)
        return True, created, ""
    except Exception as error:
        warning = _bounded_acceptance_memory_text(str(error).strip() or error.__class__.__name__, 500)
        logger.warning(
            "Unable to publish task acceptance memory task_uid=%s: %s",
            task.task_uid,
            warning,
        )
        return False, False, warning


def _store_task_acceptance_evidence(task: Task, results: List[AcceptanceResult]) -> None:
    """Replace task evidence from the immutable ledger without losing task metadata."""

    evidence = []
    for result in results:
        evidence.extend(result.evidence_refs)
        for coverage_item in result.coverage:
            evidence.extend(coverage_item.evidence_refs)
    canonical_evidence = list(dict.fromkeys(evidence))
    current_task = next(
        (item for item in _get_database_store().get_tasks(_operation_id()) if item.task_uid == task.task_uid),
        task,
    )
    if current_task.evidence == canonical_evidence:
        return
    _ensure_memory_client().patch_task(
        task_uid=task.task_uid,
        evidence_replacement=canonical_evidence,
        user_id=_user_id(),
    )


def _source_task_finding_refs(task_uid: str) -> List[str]:
    """Return canonical finding references durably linked to one executor task."""

    store = _get_database_store()
    list_findings = getattr(store, "list_findings", None)
    if not callable(list_findings):
        return []
    references = []
    for record in list_findings(_operation_id()):
        candidate_data = record.get("candidate_data", {}) if isinstance(record, dict) else {}
        if task_uid in candidate_data.get("source_task_uids", []):
            references.append(f"finding:{record['finding_uid']}")
    return list(dict.fromkeys(references))


def _bind_acceptance_finding_reference(
    task_uid: str,
    disposition: AcceptanceDisposition,
    evidence_refs: List[str],
) -> List[str]:
    """Bind a candidate disposition to the current task's durable finding relation."""

    if disposition != "finding_candidate":
        return list(evidence_refs)
    linked_refs = _source_task_finding_refs(task_uid)
    if not linked_refs:
        raise ValueError(
            "Acceptance disposition finding_candidate requires a finding created by this task. "
            "Call store_finding first, retain its returned canonical finding:<id> reference, "
            "then retry record_task_acceptance."
        )
    supplied_refs = [reference for reference in evidence_refs if str(reference).startswith("finding:")]
    if len(linked_refs) == 1:
        selected_ref = linked_refs[0]
    else:
        matching_refs = [reference for reference in supplied_refs if reference in linked_refs]
        if len(set(matching_refs)) != 1:
            raise ValueError(
                "Acceptance disposition finding_candidate is ambiguous; use exactly one current-task reference: "
                + ", ".join(linked_refs)
            )
        selected_ref = matching_refs[0]
    normalized = [reference for reference in evidence_refs if not str(reference).startswith("finding:")]
    normalized.append(selected_ref)
    if supplied_refs != [selected_ref]:
        logger.info("Auto-bound task acceptance to %s for task %s", selected_ref, task_uid)
    return normalized


def _task_execution_receipts(task: Task, criterion: AcceptanceCriterion) -> Dict[str, List[str]]:
    """Return validated controller receipts persisted in the task recovery context."""

    raw_receipts = task.recovery_context.get("execution_evidence_receipts", {})
    if not isinstance(raw_receipts, dict):
        return {}
    receipts: Dict[str, List[str]] = {}
    allowed_ids = {requirement.id for requirement in criterion.execution_requirements}
    for requirement_id, references in raw_receipts.items():
        if requirement_id not in allowed_ids or not isinstance(references, list):
            continue
        canonical = []
        for reference in references:
            try:
                normalized = _canonical_evidence_reference(reference)
            except ValueError:
                continue
            if not normalized.startswith(("artifact:", "artifact_id:")):
                continue
            if normalized not in canonical:
                canonical.append(normalized)
        if canonical:
            receipts[requirement_id] = canonical
    return receipts


def build_record_task_acceptance_tool(
    task_uid: str,
    task: Optional[Task] = None,
    execution_evidence_resolver: Optional[
        Callable[[Task, AcceptanceCriterion], Dict[str, List[str]]]
    ] = None,
) -> Any:
    """Build a model-facing acceptance tool bound to one controller-selected task."""

    normalized_uid = str(task_uid or "").strip()
    if not normalized_uid:
        raise ValueError("task_uid required when binding record_task_acceptance")

    task = task or next(
        (item for item in _get_database_store().get_tasks(_operation_id()) if item.task_uid == normalized_uid),
        None,
    )
    if task is None:
        raise ValueError("Unknown task_uid for the current operation")
    if len(task.acceptance.criteria) != 1:
        raise ValueError("record_task_acceptance requires exactly one frozen criterion")
    criterion_id = task.acceptance.criteria[0].id
    criterion = task.acceptance.criteria[0]
    required_evidence = ", ".join(
        f"{requirement.kind}>={requirement.min_count}"
        for requirement in criterion.evidence_requirements
    ) or "none"
    assigned_scope = ", ".join(task.target_ids) if task.target_ids else task.target_scope
    evidence_example_by_kind = {
        "inventory_manifest": "artifact:artifacts/inventory_manifest.json",
        "artifact": "artifact:artifacts/task-result.txt",
        "durable_evidence": "artifact:artifacts/task-result.txt",
        "memory": "memory:<returned-memory-id>",
        "observation": "memory:<returned-observation-id>",
        "finding_candidate": "finding:<returned-finding-id>",
        "verified_finding": "finding:<verified-finding-id>",
    }
    primary_evidence_kind = (
        criterion.evidence_requirements[0].kind if criterion.evidence_requirements else "durable_evidence"
    )
    example_disposition = (
        "finding_candidate"
        if primary_evidence_kind == "finding_candidate"
        else "existing_finding"
        if primary_evidence_kind == "verified_finding"
        else "observation"
    )
    task_specific_example = {
        "status": "satisfied",
        "disposition": example_disposition,
        "summary": f"Concrete terminal result for {task.title}",
        "evidence_refs": [
            evidence_example_by_kind.get(primary_evidence_kind, "artifact:artifacts/task-result.txt")
        ],
    }
    coverage_item_ids = task.acceptance.basis.item_ids
    if task.acceptance.mode == "coverage" and not coverage_item_ids:
        artifact_ref = next(
            reference for reference in task.acceptance.basis.source_refs if reference.startswith("artifact:")
        )
        manifest, _snapshot_hash = _load_inventory_manifest(artifact_ref)
        coverage_item_ids = tuple(str(item["id"]) for item in manifest["items"])
    eligible_evidence_refs = []
    for reference in task.evidence:
        try:
            canonical = _canonical_evidence_reference(reference)
        except ValueError:
            continue
        if canonical not in eligible_evidence_refs:
            eligible_evidence_refs.append(canonical)
    for reference in task.acceptance.basis.source_refs:
        try:
            canonical = _canonical_evidence_reference(reference)
        except ValueError:
            continue
        if canonical not in eligible_evidence_refs:
            eligible_evidence_refs.append(canonical)
    eligible_evidence_refs = eligible_evidence_refs[:8]

    def record_task_acceptance(
        status: str,
        disposition: str,
        summary: str,
        evidence_refs: List[str],
    ) -> str:
        status = _normalize_acceptance_status_alias(status)
        disposition = _normalize_acceptance_disposition_alias(disposition)
        evidence_refs = split_delimited_reference_values(evidence_refs, allow_delimited_strings=True)
        current_task = next(
            (item for item in _get_database_store().get_tasks(_operation_id()) if item.task_uid == normalized_uid),
            task,
        )
        receipts = _task_execution_receipts(current_task, criterion)
        if execution_evidence_resolver is not None:
            resolved = execution_evidence_resolver(current_task, criterion)
            for requirement_id, references in resolved.items():
                if requirement_id not in {item.id for item in criterion.execution_requirements}:
                    continue
                receipts[requirement_id] = list(
                    dict.fromkeys([*receipts.get(requirement_id, []), *references])
                )
        missing_requirements = [
            requirement for requirement in criterion.execution_requirements if not receipts.get(requirement.id)
        ]
        if missing_requirements:
            missing_text = "; ".join(
                f"{requirement.id} ({requirement.description})" for requirement in missing_requirements
            )
            if execution_evidence_resolver is not None:
                pending_submission = {
                    "status": status,
                    "disposition": disposition,
                    "summary": summary,
                    "evidence_refs": list(evidence_refs),
                    "missing_requirement_ids": [requirement.id for requirement in missing_requirements],
                }
                memory_client = _ensure_memory_client()
                patch_task = getattr(memory_client, "patch_task", None)
                if callable(patch_task):
                    patch_task(
                        task_uid=current_task.task_uid,
                        recovery_context_updates={"pending_controller_acceptance": pending_submission},
                        user_id=_user_id(),
                    )
                else:
                    context = dict(current_task.recovery_context)
                    context["pending_controller_acceptance"] = pending_submission
                    _get_database_store().store_task(
                        _operation_id(),
                        replace(current_task, recovery_context=context),
                    )
                raise ValueError(
                    "Acceptance is incomplete: execution evidence is required before acceptance. "
                    "Missing execution requirements: "
                    + missing_text
                    + ". Do not retry record_task_acceptance. The controller retained this acceptance submission "
                    "and will first reconcile completed tool outcomes from this cycle; it will run one bounded "
                    "task-local execution-evidence repair only if proof is still missing."
                )
            raise ValueError(
                "Execution evidence is required before acceptance. Missing execution requirements: "
                + missing_text
                + ". Do not retry acceptance yet. Complete the exact missing execution prerequisite and retain "
                "its current-operation artifact; the controller will attach the proof automatically."
            )
        if not evidence_refs:
            eligible = ", ".join(eligible_evidence_refs) or "none"
            raise ValueError(
                "acceptance result evidence_refs required; eligible_evidence_refs=" + eligible
            )
        try:
            evidence_refs = [_canonical_evidence_reference(reference) for reference in evidence_refs]
        except ValueError as error:
            requires_inventory = any(
                requirement.kind == "inventory_manifest" for requirement in criterion.evidence_requirements
            )
            if requires_inventory and "does not exist" in str(error).lower():
                raise ValueError(
                    "The submitted manifest file does not exist. Do not retry acceptance. Create a validated "
                    "inventory manifest first, then submit its returned artifact reference."
                ) from error
            unavailable_source = next(
                (
                    reference
                    for reference in evidence_refs
                    if str(reference).startswith("artifact:") and "does not exist" in str(error).lower()
                ),
                "",
            )
            if unavailable_source:
                raise TaskEvidenceSnapshotVerificationError(
                    "TASK_EVIDENCE_SNAPSHOT_SOURCE_UNAVAILABLE: source artifact is unavailable "
                    f"task_uid={current_task.task_uid} source_reference={unavailable_source}"
                ) from error
            raise
        execution_evidence_refs = [
            reference
            for requirement in criterion.execution_requirements
            for reference in receipts.get(requirement.id, [])
        ]
        evidence_refs = list(dict.fromkeys([*evidence_refs, *execution_evidence_refs]))
        evidence_refs = _bind_acceptance_finding_reference(normalized_uid, disposition, evidence_refs)
        coverage = tuple(
            CoverageResult(item_id=item_id, status=status, evidence_refs=tuple(evidence_refs))
            for item_id in coverage_item_ids
        ) if task.acceptance.mode == "coverage" else ()
        result = AcceptanceResult(
            criterion_id=criterion_id,
            status=status,
            disposition=disposition,
            summary=summary,
            evidence_refs=tuple(evidence_refs),
            coverage=coverage,
        )
        return _record_task_acceptance(normalized_uid, [result])

    record_task_acceptance.__doc__ = f"""Record evidence-backed terminal results for the assigned task.

    Bound task: {task.title}
    Assigned scope: {assigned_scope}
    Frozen criterion: {criterion.id} — {criterion.description}
    Required evidence: {required_evidence}

    Input shape: {{"status": "satisfied|assessed_negative|inaccessible|excluded|duplicate",
    "disposition": "no_vulnerability|observation|finding_candidate|existing_finding",
    "summary": "Observed result", "evidence_refs": ["memory:<id>",
    "artifact:artifacts/<file>", "finding:<id>"]}}

    Canonical evidence_refs syntax: artifact:artifacts/<file>, artifact_id:<id>, memory:<id>, or finding:<id>.
    Raw URLs, shell commands, tool IDs, and inline output are invalid. Example: artifact:artifacts/http_response.txt.

    Use finding_candidate or existing_finding whenever the summary claims confirmed security behavior. The controller
    automatically binds finding_candidate to the sole finding created by this task. If the task created multiple
    candidates, supply exactly one returned finding_ref. existing_finding always requires an explicit finding reference.
    Use observation for useful non-finding results and no_vulnerability for negative results.

    Task identity, its single criterion, and frozen coverage item IDs are supplied by the workflow controller.
    Successfully recorded results are immutable. Their concrete summaries and evidence references are automatically
    published as one operation observation for later task-prompt selection. A publication warning does not invalidate
    the acceptance ledger.

    Inventory artifact contract: {inventory_manifest_contract_text()}

    Valid payload example for this bound task: {json.dumps(task_specific_example, sort_keys=True)}
    """
    return tool(
        record_task_acceptance,
        inputSchema={
            "json": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "satisfied",
                            "assessed_negative",
                            "inaccessible",
                            "excluded",
                            "duplicate",
                        ],
                        "description": (
                            f"Terminal result for frozen criterion {criterion.id}. "
                            "Use only a canonical advertised value."
                        ),
                    },
                    "disposition": {
                        "type": "string",
                        "enum": [
                            "no_vulnerability",
                            "observation",
                            "finding_candidate",
                            "existing_finding",
                        ],
                        "description": (
                            "Security disposition. Confirmed security behavior requires finding_candidate or "
                            "existing_finding; negative and informational results use no_vulnerability or observation."
                        ),
                    },
                    "summary": {
                        "type": "string",
                        "description": f"Concrete observed result for: {criterion.description}",
                    },
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": (
                            f"Durable references satisfying {required_evidence}. Use artifact:, memory:, or finding:; "
                            "raw commands, URLs, tool IDs, and inline output are invalid."
                        ),
                    },
                },
                "required": ["status", "disposition", "summary", "evidence_refs"],
            }
        },
    )


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
def memory_list() -> str:
    """List operation findings, validation records, observations, and knowledge."""
    try:
        client = _ensure_memory_client()

        # Keep list/retrieve output bounded for prompts and reports.
        try:
            list_limit = int(os.getenv("MEMORY_LIST_LIMIT", "100"))
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
def memory_retrieve(
    query: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Semantic search across memories.

    REQUIRED:
    - query: natural language query

    OPTIONAL:
    - metadata: filter dict applied to metadata (e.g., {"category": "finding", "status": "verified"}).

    CROSS-SESSION LEARNING:
        - memory_retrieve: Scoped according to the configured memory mode

        Cross-Learning Query Examples:
        - Learn from past: memory_retrieve(query="SQLi techniques")
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


class _LiteLLMEmbeddings:
    """Minimal embedding adapter for providers routed through LiteLLM."""

    def __init__(self, model: str) -> None:
        if not model:
            raise ValueError("A LiteLLM embedding model is required")
        self.model = model

    def embed_query(self, text: str) -> List[float]:
        response = litellm.embedding(model=self.model, input=[text])
        data = response.data if hasattr(response, "data") else response["data"]
        item = data[0]
        embedding = item.embedding if hasattr(item, "embedding") else item["embedding"]
        return list(embedding)


class QdrantMemoryClient:
    """Qdrant-backed semantic memory with mandatory target scoping."""

    COLLECTION_NAME = "cyber_autoagent_memories"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        has_existing_memories: bool = False,
        silent: bool = False,
    ) -> None:
        self.config = dict(config or {})
        self.has_existing_memories = has_existing_memories
        self.silent = silent
        self.collection_name = str(self.config.get("collection_name", self.COLLECTION_NAME))
        self.embedding_dimensions = int(self.config.get("embedding_dimensions", 1024))
        self.target_values = self._target_values(self.config)
        self.operation_id = str(self.config.get("operation_id") or "").strip()
        if not self.operation_id:
            raise ValueError("Qdrant memory requires an operation ID")
        self.memory_mode = self._memory_mode(self.config.get("memory_mode", "operation"))
        self.embeddings = self.config.get("embeddings") or self._build_embeddings()
        self.qdrant_url = str(os.getenv("QDRANT_URL", "")).strip()
        self.qdrant = self._build_client()
        self._ensure_collection()
        if not silent:
            location = os.getenv("QDRANT_URL") or _get_memory_base_path(self.config)
            print(f"[+] Memory Backend: Qdrant ({'service' if os.getenv('QDRANT_URL') else 'local'})")
            print(f"    Store Location: {location}")
            print(f"    Query Scope: {self.memory_mode}")

    @staticmethod
    def _memory_mode(value: Any) -> str:
        mode = str(value or "operation").strip().lower()
        if mode not in {"operation", "shared"}:
            raise ValueError("memory_mode must be one of: operation, shared")
        return mode

    @staticmethod
    def _target_values(config: Dict[str, Any]) -> List[str]:
        raw_values = config.get("target_values")
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        values = [str(value).strip() for value in (raw_values or []) if str(value).strip()]
        if not values:
            fallback = str(config.get("target_value") or config.get("target_name") or "").strip()
            if fallback and fallback != "default_target":
                values = [fallback]
        if not values:
            raise ValueError("Qdrant memory requires at least one canonical OperationTarget value")
        return list(dict.fromkeys(values))

    def _build_embeddings(self) -> Any:
        provider = str(self.config.get("embedding_provider") or os.getenv("CYBER_AGENT_PROVIDER", "bedrock")).lower()
        model = str(self.config.get("embedding_model") or os.getenv("CYBER_AGENT_EMBEDDING_MODEL", ""))
        if provider == "ollama":
            return OllamaEmbeddings(
                model=model,
                base_url=str(self.config.get("ollama_base_url") or os.getenv("OLLAMA_HOST", "http://localhost:11434")),
            )
        if provider == "gemini":
            return GoogleGenerativeAIEmbeddings(model=model)
        if provider == "litellm":
            return _LiteLLMEmbeddings(model)
        if model.startswith("bedrock/"):
            model = model.split("/", 1)[1]
        return BedrockEmbeddings(
            model_id=model,
            region_name=str(self.config.get("aws_region") or os.getenv("AWS_REGION", "us-east-1")),
        )

    def _build_client(self) -> QdrantClient:
        if self.qdrant_url:
            return QdrantClient(url=self.qdrant_url, api_key=os.getenv("QDRANT_API_KEY") or None)
        path = _get_memory_base_path(self.config)
        os.makedirs(path, exist_ok=True)
        return QdrantClient(path=path)

    def _ensure_collection(self) -> None:
        if not self.qdrant.collection_exists(self.collection_name):
            self.qdrant.create_collection(
                collection_name=self.collection_name,
                vectors_config=qdrant_models.VectorParams(
                    size=self.embedding_dimensions,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
        if not self.qdrant_url:
            return
        for field_name in ("target_values", "operation_id", "metadata.category", "metadata.status"):
            try:
                self.qdrant.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            except Exception as error:  # Local mode may report an existing or unsupported index.
                logger.debug("Qdrant payload index %s was not created: %s", field_name, error)

    def _scope_filter(
        self,
        metadata: Optional[Dict[str, Any]] = None,
        operation_id: Optional[str] = None,
    ) -> qdrant_models.Filter:
        target_values = [str(value).strip() for value in self.target_values if str(value).strip()]
        if not target_values:
            raise ValueError("Qdrant memory retrieval requires at least one canonical OperationTarget value")
        must: List[Any] = [
            qdrant_models.FieldCondition(
                key="target_values",
                match=qdrant_models.MatchAny(any=target_values),
            )
        ]
        effective_operation = None
        if self.memory_mode == "operation":
            effective_operation = operation_id or self.operation_id
        if effective_operation:
            must.append(
                qdrant_models.FieldCondition(
                    key="operation_id",
                    match=qdrant_models.MatchValue(value=effective_operation),
                )
            )
        for key, value in (metadata or {}).items():
            if isinstance(value, list):
                match: Any = qdrant_models.MatchAny(any=value)
            elif isinstance(value, (str, int, bool)):
                match = qdrant_models.MatchValue(value=value)
            else:
                raise ValueError(f"Unsupported Qdrant metadata filter value for {key}")
            must.append(qdrant_models.FieldCondition(key=f"metadata.{key}", match=match))
        return qdrant_models.Filter(must=must)

    @staticmethod
    def _point_to_memory(point: Any) -> Dict[str, Any]:
        payload = dict(point.payload or {})
        return {
            "id": str(point.id),
            "memory": str(payload.get("memory", "")),
            "metadata": dict(payload.get("metadata") or {}),
            "created_at": str(payload.get("created_at", "")),
            "operation_id": str(payload.get("operation_id", "")),
            "target_values": list(payload.get("target_values") or []),
            "score": getattr(point, "score", None),
        }

    def _memory_matches_target_values(self, memory: Dict[str, Any]) -> bool:
        """Return whether a retrieved memory shares at least one current target value."""

        stored_values = memory.get("target_values") if isinstance(memory, dict) else None
        if not isinstance(stored_values, list):
            return False
        current_values = {str(value).strip() for value in self.target_values if str(value).strip()}
        return bool(current_values.intersection(str(value).strip() for value in stored_values if str(value).strip()))

    def store_memory(
        self,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        content = str(content or "").strip()
        if not content:
            raise ValueError("memory content is required")
        memory_id = str(uuid.uuid4())
        resolved_metadata = dict(metadata or {})
        resolved_metadata["operation_id"] = self.operation_id
        payload = {
            "memory": content,
            "metadata": resolved_metadata,
            "created_at": datetime.now().isoformat(),
            "operation_id": self.operation_id,
            "target_values": self.target_values,
            "user_id": _user_id(user_id),
            "agent_id": agent_id or _user_id(user_id),
            "active": True,
        }
        vector = self.embeddings.embed_query(content)
        if len(vector) != self.embedding_dimensions:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.embedding_dimensions}, received {len(vector)}"
            )
        with _QDRANT_WRITE_LOCK:
            self.qdrant.upsert(
                collection_name=self.collection_name,
                points=[qdrant_models.PointStruct(id=memory_id, vector=vector, payload=payload)],
                wait=True,
            )
        return {"results": [{"id": memory_id, "memory": content, "metadata": resolved_metadata}]}

    def list_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        *,
        limit: Optional[int] = None,
        page: int = 1,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        del user_id, agent_id
        effective_limit = max(int(limit or os.getenv("MEMORY_LIST_LIMIT", "100")), 1)
        effective_page = max(int(page), 1)
        points, _ = self.qdrant.scroll(
            collection_name=self.collection_name,
            scroll_filter=self._scope_filter(operation_id=run_id),
            limit=effective_limit * effective_page,
            with_payload=True,
            with_vectors=False,
        )
        start = (effective_page - 1) * effective_limit
        page_points = points[start:start + effective_limit]
        memories = [self._point_to_memory(point) for point in page_points]
        return [
            memory
            for point, memory in zip(page_points, memories)
            if bool((point.payload or {}).get("active", True)) and self._memory_matches_target_values(memory)
        ]

    def get_memory_by_id(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        del user_id, agent_id
        points = self.qdrant.retrieve(
            collection_name=self.collection_name,
            ids=[str(memory_id)],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        memory = self._point_to_memory(points[0])
        allowed = self._memory_matches_target_values(memory)
        if self.memory_mode == "operation":
            allowed = allowed and memory["operation_id"] == self.operation_id
        return memory if allowed else None

    def search_memories(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.search(query, limit=20, user_id=user_id, agent_id=agent_id, run_id=run_id)

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
        del user_id, agent_id
        vector = self.embeddings.embed_query(str(query or ""))
        result = self.qdrant.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=self._scope_filter(filters, run_id),
            limit=max(int(limit), 1),
            with_payload=True,
            with_vectors=False,
        )
        return [
            memory
            for point in result.points
            if bool((point.payload or {}).get("active", True))
            for memory in [self._point_to_memory(point)]
            if self._memory_matches_target_values(memory)
        ]

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
        op_id = _operation_id(operation_id)

        # Only successful phase and task states may complete an assessment.  Terminal failures are
        # retained for reporting, but must not be converted into a completed operation.
        tasks = _get_database_store().get_tasks(op_id)
        all_done = all(p.status in {"done", "not_applicable"} for p in plan.phases)
        all_tasks_done = all(task.status in {"done", "superseded"} for task in tasks)
        actionable_tasks = [task for task in tasks if task.status in {"active", "pending"}]
        add_completion_reminder = False
        if all_done and all_tasks_done and not plan.assessment_complete:
            plan.assessment_complete = True
            add_completion_reminder = True
            logger.info("All phases complete - set assessment_complete=true")
        elif actionable_tasks or not all_done or not all_tasks_done:
            plan.assessment_complete = False

        result = {}

        # Warn if extending plan after marking complete
        try:
            prev_plan = _get_database_store().get_plan(op_id)
            if prev_plan:
                new_total = int(plan.total_phases)
                if prev_plan.assessment_complete and new_total > int(prev_plan.total_phases):
                    result["_reminder"] = (
                        f"Adding phases ({prev_plan.total_phases} → {new_total}) after assessment_complete=true. "
                        "Consider allowing workflow completion and report generation instead."
                    )
        except Exception as e:
            logger.debug(f"Could not check previous plan for extension: {e}")

        _get_database_store().store_plan(op_id, plan)

        result["status"] = "success"
        result["plan"] = plan.to_toon()
        result["operation_id"] = op_id

        if add_completion_reminder:
            result["_reminder"] = (
                "All phases complete. Python workflow will evaluate completion and generate the report."
            )

        return result

    def patch_plan(
        self,
        *,
        phase_status_updates: Optional[Dict[int, str]] = None,
        current_phase: Optional[int] = None,
        assessment_complete: Optional[bool] = None,
        operation_id: Optional[str] = None,
    ) -> OperationPlan:
        """Patch controller-owned plan progress while retaining immutable plan content."""

        op_id = _operation_id(operation_id)
        store = _get_database_store()
        patch_plan = getattr(store, "patch_plan", None)
        if callable(patch_plan):
            return patch_plan(
                op_id,
                phase_status_updates=phase_status_updates,
                current_phase=current_phase,
                assessment_complete=assessment_complete,
            )
        current = store.get_plan(op_id)
        if current is None:
            raise ValueError(f"Unknown operation plan: {op_id}")
        updates = {int(phase_id): str(status) for phase_id, status in (phase_status_updates or {}).items()}
        patched = replace(
            current,
            phases=[replace(phase, status=updates.get(phase.id, phase.status)) for phase in current.phases],
            current_phase=current_phase if current_phase is not None else current.current_phase,
            assessment_complete=(
                assessment_complete if assessment_complete is not None else current.assessment_complete
            ),
        )
        store.store_plan(op_id, patched)
        return patched

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
            return _get_database_store().get_plan(op_id)
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
        tasks = _get_database_store().get_tasks(op_id)
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
                all_tasks = _get_database_store().get_tasks(op_id)
                for t in all_tasks:
                    if t.task_uid != task.task_uid and t.status == "active":
                        self.patch_task(
                            task_uid=t.task_uid,
                            status="pending",
                            status_reason="demoted",
                            user_id=user_id,
                        )
            except Exception as e:
                logger.debug("Could not enforce single active task: %s", e)

        _get_database_store().store_task(op_id, task)

    def patch_task(
            self,
            *,
            task_uid: str,
            status: Optional[str] = None,
            status_reason: Optional[str] = None,
            phase: Optional[int] = None,
            evidence_additions: Iterable[str] = (),
            evidence_replacement: Optional[Iterable[str]] = None,
            recovery_context_updates: Optional[Dict[str, Any]] = None,
            recovery_context_removals: Iterable[str] = (),
            user_id: Optional[str] = None,
    ) -> Task:
        """Patch one persisted task without replacing independent task state."""

        del user_id
        operation_id = _operation_id()
        store = _get_database_store()
        patch_task = getattr(store, "patch_task", None)
        if callable(patch_task):
            return patch_task(
                operation_id,
                task_uid,
                status=status,
                status_reason=status_reason,
                phase=phase,
                evidence_additions=evidence_additions,
                evidence_replacement=evidence_replacement,
                recovery_context_updates=recovery_context_updates,
                recovery_context_removals=recovery_context_removals,
            )
        current = next((item for item in store.get_tasks(operation_id) if item.task_uid == task_uid), None)
        if current is None:
            raise ValueError(f"Unknown task_uid for operation: {task_uid}")
        evidence = (
            list(dict.fromkeys(str(item) for item in evidence_replacement if str(item)))
            if evidence_replacement is not None
            else list(current.evidence)
        )
        if evidence_replacement is None:
            for reference in evidence_additions:
                if reference and reference not in evidence:
                    evidence.append(reference)
        context = dict(current.recovery_context)
        for key in recovery_context_removals:
            context.pop(key, None)
        context.update(recovery_context_updates or {})
        updated = replace(
            current,
            status=status if status is not None else current.status,
            status_reason=status_reason if status_reason is not None else current.status_reason,
            phase=phase if phase is not None else current.phase,
            evidence=evidence,
            recovery_context=context,
        )
        store.store_task(operation_id, updated)
        return updated

    def advance_task_in_phase(
            self,
            *,
            user_id: str,
            phase: int,
            new_status: Literal["done", "partial_failure", "blocked", "superseded"],
            new_status_reason: Optional[str] = None,
            task_uid: Optional[str] = None,
    ) -> Tuple[Optional[Task], Optional[Task]]:
        """Update a task in a given phase and activate the next pending task in that phase."""
        op_id = _operation_id()
        phase_tasks = _get_database_store().get_tasks(op_id)
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
            updated = self.patch_task(
                task_uid=target.task_uid,
                status=new_status,
                status_reason=new_status_reason,
                user_id=user_id,
            )

        # After updating, find next pending
        next_active: Optional[Task] = None
        if new_status in ("done", "partial_failure", "blocked", "superseded"):
            # Check for another active (shouldn't be any)
            still_active = [t for t in phase_tasks if t.status == "active" and t.task_uid != (target.task_uid if target else None)]
            if not still_active:
                pendings = [t for t in phase_tasks if t.status == "pending" and t.task_uid != (target.task_uid if target else None)]
                if pendings:
                    # Sort pendings by created_at (asc) to pick the oldest pending as next
                    pendings.sort(key=lambda x: x.created_at or "")
                    next_active = self.patch_task(
                        task_uid=pendings[0].task_uid,
                        status="active",
                        status_reason="activated",
                        user_id=user_id,
                    )

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
        phase_tasks = _get_database_store().get_tasks(op_id)
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
        next_active = self.patch_task(
            task_uid=pendings[0].task_uid,
            status="active",
            status_reason="activated",
            user_id=user_id,
        )
        return next_active, True

    def list_tasks(
            self,
            *,
            user_id: Optional[str] = None,
            phase: Optional[int] = None,
            status: Optional[List[str]] = None,
            operation_id: Optional[str] = None,
    ) -> List[Task]:
        """List tasks for a phase and, when provided, an explicit operation."""
        tasks = _get_database_store().get_tasks(_operation_id(operation_id))
        result = []
        for t in tasks:
            if phase is not None and int(t.phase) != int(phase):
                continue
            if not status or t.status in status:
                result.append(t)
        return result

    def list_task_acceptance_results(
            self,
            task_uid: str,
            operation_id: Optional[str] = None,
    ) -> List[AcceptanceResult]:
        """Return the frozen-manifest result ledger for one task and operation."""

        return _get_database_store().get_acceptance_results(_operation_id(operation_id), task_uid)

    def list_finding_records(self, operation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return finding records for an explicit or current operation."""

        return _get_database_store().list_findings(_operation_id(operation_id))

    def rebind_finding_verification_task(
        self,
        finding_uid: str,
        expected_task_uid: str,
        replacement_task_uid: str,
    ) -> bool:
        """Transfer an unresolved finding's verification ownership to its replacement task."""

        return _get_database_store().rebind_finding_verification_task(
            _operation_id(),
            finding_uid,
            expected_task_uid,
            replacement_task_uid,
        )

    def list_objective_validation_records(self) -> List[Dict[str, Any]]:
        """Return objective-validation records for the current operation."""

        return _get_database_store().list_objective_candidates(_operation_id())

    def store_preflight_results(self, results: List[Dict[str, Any]], operation_id: Optional[str] = None) -> None:
        """Persist preflight facts once so later workflow stages do not re-resolve targets."""

        _get_database_store().store_preflight_results(operation_id or _operation_id(), results)

    def list_preflight_results(self, operation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return persisted preflight facts for an explicit or current operation."""

        return _get_database_store().list_preflight_results(_operation_id(operation_id))

    def update_finding_taxonomy_annotation(
        self,
        finding_uid: str,
        annotation: Dict[str, Any],
    ) -> bool:
        """Persist one controller-owned taxonomy annotation for a finding candidate."""

        return _get_database_store().update_finding_taxonomy_annotation(
            _operation_id(),
            finding_uid,
            annotation,
        )

    def update_finding_attack_enrichment(
        self,
        finding_uid: str,
        enrichment: Dict[str, Any],
    ) -> bool:
        """Persist one controller-owned final ATT&CK enrichment result."""

        return _get_database_store().update_finding_attack_enrichment(
            _operation_id(),
            finding_uid,
            enrichment,
        )

    def get_memory_overview(self, user_id: Optional[str] = None) -> Dict:
        """Get an overview of stored memories."""
        user_id = _user_id(user_id)
        op_id = _operation_id()

        try:
            # Get all memories visible under the current Qdrant scope.
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
            plan = _get_database_store().get_plan(op_id)
            if plan:
                categories["plan"] = categories.get("plan", 0) + 1
                total_count += 1
            
            tasks = _get_database_store().get_tasks(op_id)
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
    logical_target: Optional[str] = None,
) -> None:
    """Initialize the memory system with custom configuration.

    Args:
        config: Optional Qdrant and embedding configuration dictionary
        operation_id: Unique operation identifier
        target_name: Sanitized target name for organizing memory by target
        has_existing_memories: Whether memories already existed before initialization
        silent: If True, suppress initialization output (used during report generation)
        logical_target: Exact target string entered by the user for database scoping
    """
    global _MEMORY_CONFIG, _MEMORY_CLIENT

    # Create enhanced config with operation context
    enhanced_config = config.copy() if config else {}
    enhanced_config["operation_id"] = (
            operation_id or os.environ.get("CYBER_OPERATION_ID", f"OP_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    )
    enhanced_config["target_name"] = target_name or os.environ.get("CYBER_TARGET_NAME", "default_target")
    enhanced_config["logical_target"] = (
        logical_target
        or enhanced_config.get("logical_target")
        or os.environ.get("CYBER_LOGICAL_TARGET")
        or enhanced_config["target_name"]
    )
    enhanced_config["memory_mode"] = str(
        enhanced_config.get("memory_mode") or os.environ.get("CYBER_MEMORY_MODE", "operation")
    ).lower()
    enhanced_config["output_dir"] = enhanced_config.get(
        "output_dir", os.environ.get("CYBER_AGENT_OUTPUT_DIR", get_default_base_dir())
    )
    enhanced_config["read_only"] = bool(
        enhanced_config.get("read_only", os.environ.get("CYBER_MEMORY_READ_ONLY", "").lower() == "true")
    )
    if enhanced_config["target_name"] == "default_target":
        enhanced_config["user_id"] = f'"cyber-agent-{enhanced_config["operation_id"]}"'
    else:
        enhanced_config["user_id"] = f'"cyber-agent-{enhanced_config["target_name"]}"'

    _MEMORY_CONFIG = enhanced_config
    os.environ["CYBER_OPERATION_ID"] = enhanced_config["operation_id"]
    _MEMORY_CLIENT = QdrantMemoryClient(enhanced_config, has_existing_memories, silent)
    logger.info(
        "Memory system initialized for operation %s, target: %s, user: %s",
        enhanced_config["operation_id"],
        enhanced_config["target_name"],
        enhanced_config["user_id"],
    )


def get_memory_client(silent: bool = False) -> QdrantMemoryClient:
    """Get the memory client for the authoritative environment operation.

    Args:
        silent: If True, suppress initialization output (used during report generation)

    Returns:
        The memory client instance or None if initialization fails
    """
    global _MEMORY_CLIENT
    authoritative_operation_id = os.environ.get("CYBER_OPERATION_ID")
    if _MEMORY_CLIENT is None:
        initialize_memory_system(silent=silent)
    elif authoritative_operation_id:
        configured_operation_id = (_MEMORY_CONFIG or {}).get("operation_id")
        if configured_operation_id != authoritative_operation_id:
            logger.info(
                "Reinitializing memory client for CYBER_OPERATION_ID=%s (was configured for %s)",
                authoritative_operation_id,
                configured_operation_id or "unset",
            )
            existing_config = dict(_MEMORY_CONFIG or {})
            target_name = existing_config.get("target_name")
            logical_target = existing_config.get("logical_target")
            has_existing_memories = bool(getattr(_MEMORY_CLIENT, "has_existing_memories", True))
            initialize_memory_system(
                config=existing_config,
                operation_id=authoritative_operation_id,
                target_name=target_name,
                has_existing_memories=has_existing_memories,
                silent=silent,
                logical_target=logical_target,
            )
    return _MEMORY_CLIENT


def clear_memory_client() -> None:
    global _MEMORY_CLIENT, _MEMORY_CONFIG, _DATABASE_STORE, _MEMORY_EVENT_EMITTER
    _MEMORY_CLIENT = None
    _MEMORY_CONFIG = None
    _DATABASE_STORE = None
    _MEMORY_EVENT_EMITTER = None
