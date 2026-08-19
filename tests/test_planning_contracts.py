from types import SimpleNamespace

import pytest

from modules.operation_plugins import planning_contracts as contracts
from modules.operation_plugins.planning_contracts import (
    load_phase_task_contract,
    validate_phase_task_proposals,
)
from modules.tools.memory import TaskProposal, _TASK_PROPOSAL_INPUT_SCHEMA


def _proposal(workstream, role="mapping", depends=(), reason=None, output_kind="artifact"):
    return SimpleNamespace(
        workstream=workstream,
        task_role=role,
        depends_on_workstreams=list(depends),
        inapplicability_reason=reason,
        output_kind=output_kind,
    )


def test_only_enabled_modules_declare_phase_one_fanout_contracts():
    assert load_phase_task_contract("web", 1) is not None
    assert load_phase_task_contract("web_recon", 1) is not None
    assert load_phase_task_contract("ctf", 1) is not None
    assert load_phase_task_contract("code_security", 1) is None
    assert load_phase_task_contract("context_navigator", 1) is None
    assert load_phase_task_contract("threat_emulation", 1) is None


def test_web_contract_accepts_distinct_mapping_tasks_and_inventory_synthesis():
    contract = load_phase_task_contract("web", 1)

    validate_phase_task_proposals(
        contract,
        [
            _proposal("entrypoint_technology"),
            _proposal("bounded_crawl"),
            _proposal("client_side_api"),
            _proposal(
                "inventory_synthesis",
                role="synthesis",
                depends=("entrypoint_technology", "bounded_crawl", "client_side_api"),
                output_kind="inventory_manifest",
            ),
        ],
    )


@pytest.mark.parametrize(
    "proposals,message",
    [
        ([_proposal("entrypoint_technology")], "at least 3"),
        (
            [
                _proposal("entrypoint_technology"),
                _proposal("entrypoint_technology"),
                _proposal("bounded_crawl"),
                _proposal(
                    "inventory_synthesis",
                    role="synthesis",
                    depends=("entrypoint_technology", "bounded_crawl"),
                    output_kind="inventory_manifest",
                ),
            ],
            "distinct mapping workstreams",
        ),
        (
            [
                _proposal("entrypoint_technology"),
                _proposal("bounded_crawl"),
                _proposal("client_side_api"),
            ],
            "exactly one synthesis",
        ),
    ],
)
def test_web_contract_rejects_incomplete_or_overlapping_fanout(proposals, message):
    with pytest.raises(ValueError, match=message):
        validate_phase_task_proposals(load_phase_task_contract("web", 1), proposals)


def test_web_recon_contract_rejects_synthesis_task():
    with pytest.raises(ValueError, match="does not allow a synthesis"):
        validate_phase_task_proposals(
            load_phase_task_contract("web_recon", 1),
            [
                _proposal("service_entrypoints"),
                _proposal("technology_trust_boundary"),
                _proposal("access_context_session"),
                _proposal("coverage", role="synthesis"),
            ],
        )


def test_web_recon_contract_accepts_distinct_read_only_mapping_workstreams():
    validate_phase_task_proposals(
        load_phase_task_contract("web_recon", 1),
        [
            _proposal("service_entrypoints"),
            _proposal("technology_trust_boundary"),
            _proposal("safe_read_only_verification"),
        ],
    )


def test_ctf_contract_accepts_documented_direct_single_step_exception():
    validate_phase_task_proposals(
        load_phase_task_contract("ctf", 1),
        [_proposal("flag_path", role="direct_single_step", reason="The root response contains the required flag.")],
    )


def test_ctf_contract_rejects_undocumented_direct_single_step_exception():
    with pytest.raises(ValueError, match="requires inapplicability_reason"):
        validate_phase_task_proposals(
            load_phase_task_contract("ctf", 1),
            [_proposal("flag_path", role="direct_single_step")],
        )


def test_ctf_contract_rejects_direct_single_step_for_unsupported_workstream():
    with pytest.raises(ValueError, match="unsupported workstream"):
        validate_phase_task_proposals(
            load_phase_task_contract("ctf", 1),
            [_proposal("challenge_hints", role="direct_single_step", reason="Direct flag")],
        )


def test_ctf_contract_accepts_mapping_and_artifact_synthesis():
    validate_phase_task_proposals(
        load_phase_task_contract("ctf", 1),
        [
            _proposal("challenge_hints"),
            _proposal("endpoint_capabilities"),
            _proposal("flag_path"),
            _proposal(
                "challenge_surface_synthesis",
                role="synthesis",
                depends=("challenge_hints", "endpoint_capabilities", "flag_path"),
            ),
        ],
    )


def test_synthesis_must_depend_on_every_mapping_workstream():
    with pytest.raises(ValueError, match="depend on every submitted"):
        validate_phase_task_proposals(
            load_phase_task_contract("web", 1),
            [
                _proposal("entrypoint_technology"),
                _proposal("bounded_crawl"),
                _proposal("client_side_api"),
                _proposal(
                    "inventory_synthesis",
                    role="synthesis",
                    depends=("entrypoint_technology",),
                    output_kind="inventory_manifest",
                ),
            ],
        )


@pytest.mark.parametrize(
    "raw,message",
    [
        ({"phase_id": 0, "mode": "fanout", "min_mapping_tasks": 1, "mapping_workstreams": ["a"]}, "phase_id"),
        ({"phase_id": 1, "mode": "invalid", "min_mapping_tasks": 1, "mapping_workstreams": ["a"]}, "mode"),
        ({"phase_id": 1, "mode": "fanout", "min_mapping_tasks": 2, "mapping_workstreams": ["a"]}, "fewer workstreams"),
        ({"phase_id": 1, "mode": "fanout_with_synthesis", "min_mapping_tasks": 1, "mapping_workstreams": ["a"]}, "synthesis_workstream"),
        (
            {
                "phase_id": 1,
                "mode": "fanout_with_synthesis",
                "min_mapping_tasks": 1,
                "mapping_workstreams": ["a"],
                "synthesis_workstream": "s",
                "synthesis_output_kind": "unknown",
            },
            "synthesis_output_kind",
        ),
    ],
)
def test_contract_parser_rejects_invalid_declarations(raw, message):
    with pytest.raises(ValueError, match=message):
        contracts._parse_contract("fixture", raw)


def test_task_proposal_schema_exposes_planning_metadata():
    proposal = TaskProposal.model_validate(
        {
            "title": "Map bounded crawler output",
            "objective": "Persist bounded crawler output for the assigned target",
            "methods": ["crawl"],
            "limits": {"max_requests": 10},
            "criteria": [{"description": "Store the bounded crawl artifact"}],
            "workstream": "bounded_crawl",
            "task_role": "mapping",
        }
    )

    properties = _TASK_PROPOSAL_INPUT_SCHEMA["json"]["$defs"]["TaskProposal"]["properties"]
    assert proposal.workstream == "bounded_crawl"
    assert proposal.task_role == "mapping"
    assert {"workstream", "task_role", "depends_on_workstreams", "inapplicability_reason"} <= set(properties)
