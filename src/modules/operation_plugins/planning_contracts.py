"""Declarative, module-owned task fan-out contracts.

The workflow controller consumes these contracts as structured planning policy.
They deliberately describe task metadata rather than inferring workstreams from
model-authored titles or objectives.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_VALID_MODES = frozenset({"fanout", "fanout_with_synthesis"})
_VALID_ROLES = frozenset({"mapping", "synthesis", "direct_single_step"})
_VALID_SYNTHESIS_EXECUTIONS = frozenset({"controller"})


@dataclass(frozen=True)
class PhaseTaskContract:
    """Validated task fan-out rules for one module phase."""

    module: str
    phase_id: int
    mode: str
    min_mapping_tasks: int
    mapping_workstreams: frozenset[str]
    synthesis_workstream: str | None = None
    synthesis_output_kind: str = "artifact"
    synthesis_execution: str | None = None
    allow_direct_single_step: bool = False
    direct_single_step_workstreams: frozenset[str] = frozenset()


def load_phase_task_contract(module: str, phase_id: int) -> PhaseTaskContract | None:
    """Load an explicitly declared phase contract without inheriting parent policy."""

    normalized_module = str(module or "").strip()
    if not normalized_module:
        return None
    manifest_path = Path(__file__).resolve().parent / normalized_module / "module.yaml"
    if not manifest_path.is_file():
        return None
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid module planning contract for {normalized_module}") from error
    planning = payload.get("planning")
    if not isinstance(planning, dict):
        return None
    contracts = planning.get("phase_task_contracts")
    if not isinstance(contracts, list):
        raise ValueError(f"planning.phase_task_contracts must be a list for {normalized_module}")
    matching = [item for item in contracts if isinstance(item, dict) and item.get("phase_id") == phase_id]
    if not matching:
        return None
    if len(matching) != 1:
        raise ValueError(f"Duplicate planning contract for module={normalized_module} phase={phase_id}")
    return _parse_contract(normalized_module, matching[0])


def validate_phase_task_proposals(contract: PhaseTaskContract, proposals: list[Any]) -> None:
    """Validate proposal metadata against a module contract.

    Proposal objects intentionally use only structured fields supplied to
    ``create_tasks``; no model-authored prose participates in this decision.
    """

    if contract.allow_direct_single_step and len(proposals) == 1:
        proposal = proposals[0]
        if proposal.task_role == "direct_single_step":
            if proposal.workstream not in contract.direct_single_step_workstreams:
                raise ValueError("direct_single_step proposal uses an unsupported workstream")
            if not proposal.inapplicability_reason:
                raise ValueError("direct_single_step proposal requires inapplicability_reason")
            return

    mapping = [proposal for proposal in proposals if proposal.task_role == "mapping"]
    synthesis = [proposal for proposal in proposals if proposal.task_role == "synthesis"]
    invalid_roles = [proposal.task_role for proposal in proposals if proposal.task_role not in _VALID_ROLES]
    if invalid_roles or len(mapping) + len(synthesis) != len(proposals):
        raise ValueError("phase task contract permits only mapping and synthesis task roles")
    if len(mapping) < contract.min_mapping_tasks:
        raise ValueError(
            f"phase task contract requires at least {contract.min_mapping_tasks} distinct mapping tasks"
        )
    workstreams = [proposal.workstream for proposal in mapping]
    if any(workstream not in contract.mapping_workstreams for workstream in workstreams):
        raise ValueError("mapping proposal uses a workstream not declared by the active module contract")
    if len(set(workstreams)) != len(workstreams):
        raise ValueError("phase task contract requires distinct mapping workstreams")
    if any(proposal.depends_on_workstreams for proposal in mapping):
        raise ValueError("mapping tasks must not declare workstream dependencies")

    if contract.mode == "fanout":
        if synthesis:
            raise ValueError("active module contract does not allow a synthesis task in this phase")
        return

    if len(synthesis) != 1:
        raise ValueError("phase task contract requires exactly one synthesis task")
    synthesis_proposal = synthesis[0]
    if synthesis_proposal.workstream != contract.synthesis_workstream:
        raise ValueError("synthesis proposal uses the wrong workstream")
    if synthesis_proposal.output_kind != contract.synthesis_output_kind:
        raise ValueError(
            f"synthesis proposal requires output_kind={contract.synthesis_output_kind}"
        )
    if set(synthesis_proposal.depends_on_workstreams) != set(workstreams):
        raise ValueError("synthesis task must depend on every submitted mapping workstream")


def _parse_contract(module: str, raw: dict[str, Any]) -> PhaseTaskContract:
    phase_id = raw.get("phase_id")
    mode = raw.get("mode")
    min_mapping_tasks = raw.get("min_mapping_tasks")
    mapping_workstreams = raw.get("mapping_workstreams")
    if not isinstance(phase_id, int) or phase_id <= 0:
        raise ValueError(f"planning contract phase_id must be a positive integer for {module}")
    if mode not in _VALID_MODES:
        raise ValueError(f"planning contract mode is invalid for {module}")
    if not isinstance(min_mapping_tasks, int) or min_mapping_tasks < 1:
        raise ValueError(f"planning contract min_mapping_tasks is invalid for {module}")
    if not isinstance(mapping_workstreams, list) or not all(
        isinstance(item, str) and item.strip() for item in mapping_workstreams
    ):
        raise ValueError(f"planning contract mapping_workstreams is invalid for {module}")
    normalized_workstreams = frozenset(item.strip() for item in mapping_workstreams)
    if len(normalized_workstreams) < min_mapping_tasks:
        raise ValueError(f"planning contract has fewer workstreams than its minimum for {module}")
    synthesis_workstream = raw.get("synthesis_workstream")
    synthesis_output_kind = raw.get("synthesis_output_kind", "artifact")
    synthesis_execution = raw.get("synthesis_execution")
    if mode == "fanout_with_synthesis":
        if not isinstance(synthesis_workstream, str) or not synthesis_workstream.strip():
            raise ValueError(f"synthesis_workstream is required for {module}")
        if synthesis_output_kind not in {"artifact", "inventory_manifest"}:
            raise ValueError(f"synthesis_output_kind is invalid for {module}")
        if synthesis_execution not in _VALID_SYNTHESIS_EXECUTIONS:
            raise ValueError(f"synthesis_execution must be controller for {module}")
    else:
        synthesis_workstream = None
        synthesis_execution = None
    allow_direct = bool(raw.get("allow_direct_single_step", False))
    direct_workstreams = raw.get("direct_single_step_workstreams", [])
    if not isinstance(direct_workstreams, list) or not all(
        isinstance(item, str) and item.strip() for item in direct_workstreams
    ):
        raise ValueError(f"direct_single_step_workstreams is invalid for {module}")
    normalized_direct_workstreams = frozenset(item.strip() for item in direct_workstreams)
    if allow_direct and not normalized_direct_workstreams:
        raise ValueError(f"direct_single_step_workstreams is required for {module}")
    return PhaseTaskContract(
        module=module,
        phase_id=phase_id,
        mode=mode,
        min_mapping_tasks=min_mapping_tasks,
        mapping_workstreams=normalized_workstreams,
        synthesis_workstream=synthesis_workstream.strip() if synthesis_workstream else None,
        synthesis_output_kind=synthesis_output_kind,
        synthesis_execution=synthesis_execution,
        allow_direct_single_step=allow_direct,
        direct_single_step_workstreams=normalized_direct_workstreams,
    )
