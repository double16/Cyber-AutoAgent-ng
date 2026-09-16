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

_VALID_MODES = frozenset({"controller_owned", "fanout", "fanout_with_synthesis"})
_VALID_ROLES = frozenset({"mapping", "synthesis", "direct_single_step"})
_VALID_SYNTHESIS_EXECUTIONS = frozenset({"controller", "executor"})
_VALID_TASK_CREATION_MODES = frozenset({
    "standard",
    "snapshot_dependent",
    "hypothesis_dependent",
    "finding_dependent",
    "finding_validation",
})


@dataclass(frozen=True)
class PhaseTaskContract:
    """Validated task fan-out rules for one module phase."""

    module: str
    phase_id: int
    mode: str
    min_mapping_tasks: int
    mapping_workstreams: frozenset[str]
    controller_mapping_workstreams: frozenset[str] = frozenset()
    synthesis_workstream: str | None = None
    synthesis_output_kind: str = "artifact"
    synthesis_execution: str | None = None
    allow_direct_single_step: bool = False
    direct_single_step_workstreams: frozenset[str] = frozenset()
    controller_owned_phase_kind: str = ""
    prerequisite_workstreams: frozenset[str] = frozenset()
    registration_attribute: str = ""
    identities_per_role: int = 0


@dataclass(frozen=True)
class PhaseMetadataContract:
    """Module-owned workflow metadata required for a numbered phase."""

    module: str
    phase_id: int
    task_creation_mode: str


def load_phase_metadata_contracts(module: str) -> dict[int, PhaseMetadataContract]:
    """Load optional phase metadata rules without inheriting parent policy."""

    normalized_module = str(module or "").strip()
    if not normalized_module:
        return {}
    manifest_path = Path(__file__).resolve().parent / normalized_module / "module.yaml"
    if not manifest_path.is_file():
        return {}
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid module planning contract for {normalized_module}") from error
    planning = payload.get("planning")
    if not isinstance(planning, dict):
        return {}
    raw_contracts = planning.get("phase_metadata_contracts", [])
    if not isinstance(raw_contracts, list):
        raise ValueError(f"planning.phase_metadata_contracts must be a list for {normalized_module}")
    contracts = [_parse_metadata_contract(normalized_module, item) for item in raw_contracts]
    if len({contract.phase_id for contract in contracts}) != len(contracts):
        raise ValueError(f"Duplicate phase metadata contract for module={normalized_module}")
    return {contract.phase_id: contract for contract in contracts}


def load_phase_task_contract(module: str, phase_id: int) -> PhaseTaskContract | None:
    """Load an explicitly declared phase contract without inheriting parent policy."""

    return load_phase_task_contracts(module).get(phase_id)


def load_phase_task_contracts(module: str) -> dict[int, PhaseTaskContract]:
    """Load every declared phase contract for module-owned dependency resolution."""

    normalized_module = str(module or "").strip()
    if not normalized_module:
        return {}
    manifest_path = Path(__file__).resolve().parent / normalized_module / "module.yaml"
    if not manifest_path.is_file():
        return {}
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid module planning contract for {normalized_module}") from error
    planning = payload.get("planning")
    if not isinstance(planning, dict):
        return {}
    contracts = planning.get("phase_task_contracts")
    if not isinstance(contracts, list):
        raise ValueError(f"planning.phase_task_contracts must be a list for {normalized_module}")
    parsed = [_parse_contract(normalized_module, item) for item in contracts]
    if len({contract.phase_id for contract in parsed}) != len(parsed):
        raise ValueError(f"Duplicate planning contract for module={normalized_module}")
    return {contract.phase_id: contract for contract in parsed}


def load_controller_owned_phase_contracts(module: str) -> tuple[PhaseTaskContract, ...]:
    """Return typed controller-owned phases declared alongside task contracts."""

    contracts = tuple(
        contract
        for contract in load_phase_task_contracts(module).values()
        if contract.mode == "controller_owned"
    )
    if len({contract.controller_owned_phase_kind for contract in contracts}) != len(contracts):
        raise ValueError(f"Duplicate controller-owned phase contract for module={module}")
    return contracts


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
    if not isinstance(raw, dict):
        raise ValueError(f"planning contract must be an object for {module}")
    phase_id = raw.get("phase_id")
    mode = raw.get("mode")
    if not isinstance(phase_id, int) or phase_id <= 0:
        raise ValueError(f"planning contract phase_id must be a positive integer for {module}")
    if mode not in _VALID_MODES:
        raise ValueError(f"planning contract mode is invalid for {module}")
    controller_owned_phase_kind = str(raw.get("controller_owned_phase_kind") or "").strip()
    if mode == "controller_owned":
        prerequisite_workstreams = raw.get("prerequisite_workstreams")
        registration_attribute = str(raw.get("registration_attribute") or "").strip()
        identities_per_role = raw.get("identities_per_role")
        if (
            not controller_owned_phase_kind
            or not isinstance(prerequisite_workstreams, list)
            or not all(isinstance(item, str) and item.strip() for item in prerequisite_workstreams)
            or not registration_attribute
            or not isinstance(identities_per_role, int)
            or identities_per_role < 1
        ):
            raise ValueError(f"controller-owned phase contract is invalid for {module}")
        return PhaseTaskContract(
            module=module,
            phase_id=phase_id,
            mode=mode,
            min_mapping_tasks=0,
            mapping_workstreams=frozenset(),
            controller_owned_phase_kind=controller_owned_phase_kind,
            prerequisite_workstreams=frozenset(item.strip() for item in prerequisite_workstreams),
            registration_attribute=registration_attribute,
            identities_per_role=identities_per_role,
        )

    min_mapping_tasks = raw.get("min_mapping_tasks")
    mapping_workstreams = raw.get("mapping_workstreams")
    if not isinstance(min_mapping_tasks, int) or min_mapping_tasks < 1:
        raise ValueError(f"planning contract min_mapping_tasks is invalid for {module}")
    if not isinstance(mapping_workstreams, list) or not all(
        isinstance(item, str) and item.strip() for item in mapping_workstreams
    ):
        raise ValueError(f"planning contract mapping_workstreams is invalid for {module}")
    normalized_workstreams = frozenset(item.strip() for item in mapping_workstreams)
    if len(normalized_workstreams) < min_mapping_tasks:
        raise ValueError(f"planning contract has fewer workstreams than its minimum for {module}")
    controller_mapping_workstreams = raw.get("controller_mapping_workstreams", [])
    if not isinstance(controller_mapping_workstreams, list) or not all(
        isinstance(item, str) and item.strip() for item in controller_mapping_workstreams
    ):
        raise ValueError(f"controller_mapping_workstreams is invalid for {module}")
    normalized_controller_workstreams = frozenset(item.strip() for item in controller_mapping_workstreams)
    if not normalized_controller_workstreams.issubset(normalized_workstreams):
        raise ValueError(f"controller_mapping_workstreams must be declared mapping workstreams for {module}")
    synthesis_workstream = raw.get("synthesis_workstream")
    synthesis_output_kind = raw.get("synthesis_output_kind", "artifact")
    synthesis_execution = raw.get("synthesis_execution")
    if mode == "fanout_with_synthesis":
        if not isinstance(synthesis_workstream, str) or not synthesis_workstream.strip():
            raise ValueError(f"synthesis_workstream is required for {module}")
        if synthesis_output_kind not in {"artifact", "inventory_manifest"}:
            raise ValueError(f"synthesis_output_kind is invalid for {module}")
        if synthesis_execution not in _VALID_SYNTHESIS_EXECUTIONS:
            raise ValueError(
                f"synthesis_execution must be one of: {', '.join(sorted(_VALID_SYNTHESIS_EXECUTIONS))} "
                f"for {module}"
            )
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
        controller_mapping_workstreams=normalized_controller_workstreams,
        synthesis_workstream=synthesis_workstream.strip() if synthesis_workstream else None,
        synthesis_output_kind=synthesis_output_kind,
        synthesis_execution=synthesis_execution,
        allow_direct_single_step=allow_direct,
        direct_single_step_workstreams=normalized_direct_workstreams,
    )


def _parse_metadata_contract(module: str, raw: Any) -> PhaseMetadataContract:
    if not isinstance(raw, dict):
        raise ValueError(f"phase metadata contract must be an object for {module}")
    phase_id = raw.get("phase_id")
    task_creation_mode = raw.get("task_creation_mode")
    if not isinstance(phase_id, int) or phase_id <= 0:
        raise ValueError(f"phase metadata contract phase_id must be a positive integer for {module}")
    if task_creation_mode not in _VALID_TASK_CREATION_MODES:
        raise ValueError(f"phase metadata contract task_creation_mode is invalid for {module}")
    return PhaseMetadataContract(
        module=module,
        phase_id=phase_id,
        task_creation_mode=task_creation_mode,
    )
