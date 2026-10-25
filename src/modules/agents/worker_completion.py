"""Secret-free controller facts retained across credential worker turns."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerCompletion:
    """Functional completion is independent of reusable flow publication."""

    operation_id: str
    task_uid: str
    purpose: str
    target: str
    role: str
    credential_id: str
    evidence_refs: tuple[str, ...] = ()
    producer_id: str = ""
    flow_id: str = ""
    mapping_approved: bool = False
    functional_success: bool = True

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_refs"] = list(self.evidence_refs)
        return result

    @classmethod
    def from_dict(cls, value: Any) -> "WorkerCompletion":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid worker completion fields")
        for field in ("operation_id", "task_uid", "target", "credential_id", "purpose"):
            if not isinstance(value[field], str) or not value[field].strip():
                raise ValueError("Worker completion identity is required")
        if value["purpose"] not in {"registration", "authentication"}:
            raise ValueError("Invalid worker completion purpose")
        if any(not isinstance(value[field], str) for field in ("role", "producer_id", "flow_id")):
            raise ValueError("Invalid worker completion metadata")
        if value["functional_success"] is not True or type(value["mapping_approved"]) is not bool:
            raise ValueError("Invalid worker completion status")
        refs = value["evidence_refs"]
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
            raise ValueError("Invalid worker completion evidence")
        if value["purpose"] == "registration" and not value["producer_id"]:
            raise ValueError("Registration completion requires a task-local producer")
        return cls(**{**value, "evidence_refs": tuple(refs)})
