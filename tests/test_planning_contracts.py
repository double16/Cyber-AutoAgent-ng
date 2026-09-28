from types import SimpleNamespace

import pytest

from modules.operation_plugins import planning_contracts as contracts
from modules.operation_plugins.planning_contracts import (
    load_controller_owned_phase_contracts,
    load_phase_metadata_contracts,
    load_phase_task_contract,
    validate_phase_task_proposals,
)
from modules.tools.memory import (
    _TASK_PROPOSAL_INPUT_SCHEMA,
    OperationPlan,
    OperationTarget,
    PlanPhase,
    TaskProposal,
    _proposal_acceptance_contract,
)


def _proposal(workstream, role="mapping", depends=(), reason=None, output_kind="artifact"):
    return SimpleNamespace(
        workstream=workstream,
        task_role=role,
        depends_on_workstreams=list(depends),
        inapplicability_reason=reason,
        output_kind=output_kind,
    )


def test_enabled_web_modules_declare_credential_coverage_phase_contracts():
    assert load_phase_task_contract("web", 1) is not None
    assert load_phase_task_contract("web_recon", 1) is not None
    assert load_phase_task_contract("ctf", 1) is not None
    assert load_phase_task_contract("web", 3) is not None
    assert load_phase_task_contract("web_recon", 2) is not None
    assert load_phase_task_contract("ctf", 2) is None
    assert load_phase_task_contract("code_security", 1) is None
    assert load_phase_task_contract("context_navigator", 1) is None
    assert load_phase_task_contract("threat_emulation", 1) is None


def test_planning_contract_loaders_ignore_empty_and_unknown_modules():
    assert load_phase_metadata_contracts("") == {}
    assert load_phase_metadata_contracts("unknown_module") == {}
    assert contracts.load_phase_task_contracts("") == {}
    assert contracts.load_phase_task_contracts("unknown_module") == {}


def test_controller_owned_phase_contract_loader_rejects_duplicate_kinds(monkeypatch):
    contract = contracts.PhaseTaskContract(
        module="fixture",
        phase_id=1,
        mode="controller_owned",
        min_mapping_tasks=0,
        mapping_workstreams=frozenset(),
        controller_owned_phase_kind="credential_provisioning",
    )
    duplicate = contracts.PhaseTaskContract(
        module="fixture",
        phase_id=2,
        mode="controller_owned",
        min_mapping_tasks=0,
        mapping_workstreams=frozenset(),
        controller_owned_phase_kind="credential_provisioning",
    )
    monkeypatch.setattr(contracts, "load_phase_task_contracts", lambda _module: {1: contract, 2: duplicate})

    with pytest.raises(ValueError, match="Duplicate controller-owned"):
        load_controller_owned_phase_contracts("fixture")


def test_web_phase_metadata_requires_inventory_snapshot_task_creation():
    contracts_by_phase = load_phase_metadata_contracts("web")
    recon_contracts_by_phase = load_phase_metadata_contracts("web_recon")

    assert contracts_by_phase[2].task_creation_mode == "snapshot_dependent"
    assert contracts_by_phase[3].task_creation_mode == "snapshot_dependent"
    assert [recon_contracts_by_phase[phase_id].task_creation_mode for phase_id in range(2, 6)] == [
        "snapshot_dependent",
        "snapshot_dependent",
        "snapshot_dependent",
        "snapshot_dependent",
    ]


def test_web_phase_three_declares_controller_owned_baseline_workstream():
    contract = load_phase_task_contract("web", 3)

    assert contract.controller_mapping_workstreams == {"unauthenticated_baseline"}
    assert contract.exclude_public_static_assets is True


def test_web_recon_access_context_phase_excludes_confirmed_public_static_assets():
    contract = load_phase_task_contract("web_recon", 2)

    assert contract.exclude_public_static_assets is True


def test_public_static_asset_exclusion_contract_requires_boolean():
    with pytest.raises(ValueError, match="exclude_public_static_assets must be a boolean"):
        contracts._parse_contract("fixture", {
            "phase_id": 1,
            "mode": "fanout",
            "min_mapping_tasks": 1,
            "mapping_workstreams": ["mapping"],
            "exclude_public_static_assets": "true",
        })


def test_web_declares_dependency_based_credential_provisioning_contract():
    contract = load_phase_task_contract("web", 2)

    assert contract.mode == "controller_owned"
    assert contract.controller_owned_phase_kind == "credential_provisioning"
    assert contract.prerequisite_workstreams == {"inventory_synthesis", "auth_workflow"}
    assert contract.registration_attribute == "registration"
    assert contract.identities_per_role == 2


def test_controller_owned_phase_contract_requires_credential_metadata():
    with pytest.raises(ValueError, match="controller-owned phase contract"):
        contracts._parse_contract(
            "fixture",
            {
                "phase_id": 2,
                "mode": "controller_owned",
                "controller_owned_phase_kind": "credential_provisioning",
                "prerequisite_workstreams": ["inventory_synthesis"],
            },
        )


@pytest.mark.parametrize("phase_id", [None, 0, -1, "2"])
def test_controller_owned_phase_contract_requires_positive_integer_phase_id(phase_id):
    with pytest.raises(ValueError, match="phase_id"):
        contracts._parse_contract(
            "fixture",
            {
                "phase_id": phase_id,
                "mode": "controller_owned",
                "controller_owned_phase_kind": "credential_provisioning",
                "prerequisite_workstreams": ["inventory_synthesis"],
                "registration_attribute": "registration",
                "identities_per_role": 2,
            },
        )


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


def test_controller_owned_synthesis_proposal_requires_no_runtime_method():
    proposal = TaskProposal.model_validate(
        {
            "title": "Synthesize canonical inventory",
            "objective": "Merge the completed mapping outputs into the canonical inventory manifest",
            "methods": [],
            "limits": {"max_requests": 1},
            "criteria": [{"description": "Store the canonical inventory manifest"}],
            "workstream": "inventory_synthesis",
            "task_role": "synthesis",
            "depends_on_workstreams": ["entrypoint_technology", "bounded_crawl", "client_side_api"],
            "output_kind": "inventory_manifest",
        }
    )

    acceptance = _proposal_acceptance_contract(
        proposal,
        OperationPlan(
            objective="Assess",
            current_phase=1,
            total_phases=1,
            phases=[PlanPhase(id=1, title="Mapping", status="active")],
        ),
        phase_task_contract=load_phase_task_contract("web", 1),
    )

    assert proposal.methods == []
    assert acceptance.basis.procedure.methods == ("controller_synthesis",)
    assert acceptance.criteria[0].execution_requirements == ()


def test_controller_owned_synthesis_rejects_invented_runtime_method():
    proposal = TaskProposal.model_validate(
        {
            "title": "Synthesize canonical inventory",
            "objective": "Merge the completed mapping outputs into the canonical inventory manifest",
            "methods": ["analyze"],
            "limits": {"max_requests": 1},
            "criteria": [{"description": "Store the canonical inventory manifest"}],
            "workstream": "inventory_synthesis",
            "task_role": "synthesis",
            "output_kind": "inventory_manifest",
        }
    )

    with pytest.raises(ValueError, match="controller_synthesis is reserved"):
        _proposal_acceptance_contract(
            proposal,
            OperationPlan(
                objective="Assess",
                current_phase=1,
                total_phases=1,
                phases=[PlanPhase(id=1, title="Mapping", status="active")],
            ),
            phase_task_contract=load_phase_task_contract("web", 1),
        )


def test_non_inventory_synthesis_requires_an_explicit_executor_model():
    proposal = TaskProposal.model_validate(
        {
            "title": "Analyze exploit chain",
            "objective": "Correlate verified findings into one bounded attack path",
            "methods": [],
            "limits": {"max_requests": 1},
            "criteria": [{"description": "Store one bounded chain analysis artifact"}],
            "task_role": "synthesis",
        }
    )

    with pytest.raises(ValueError, match="controller_synthesis is reserved"):
        _proposal_acceptance_contract(
            proposal,
            OperationPlan(
                objective="Assess",
                current_phase=1,
                total_phases=1,
                phases=[PlanPhase(id=1, title="Exploit chain analysis", status="active")],
            ),
        )


def test_executor_owned_synthesis_retains_its_declared_execution_method():
    proposal = TaskProposal.model_validate(
        {
            "title": "Synthesize challenge surface",
            "objective": "Analyze completed CTF mapping artifacts into one challenge surface record",
            "methods": ["analyze"],
            "limits": {"max_requests": 1},
            "criteria": [{"description": "Store one bounded challenge surface artifact"}],
            "workstream": "challenge_surface_synthesis",
            "task_role": "synthesis",
            "output_kind": "artifact",
        }
    )

    acceptance = _proposal_acceptance_contract(
        proposal,
        OperationPlan(
            objective="Assess",
            current_phase=1,
            total_phases=1,
            phases=[PlanPhase(id=1, title="Challenge surface", status="active")],
            targets=[OperationTarget(target_id="target-1", type="network", value="ctf.test")],
        ),
        phase_task_contract=load_phase_task_contract("ctf", 1),
    )

    assert acceptance.basis.procedure.methods == ("analyze",)
    assert acceptance.criteria[0].execution_requirements


def test_executor_owned_synthesis_requires_a_runtime_method():
    proposal = TaskProposal.model_validate(
        {
            "title": "Synthesize challenge surface",
            "objective": "Analyze completed CTF mapping artifacts into one challenge surface record",
            "methods": [],
            "limits": {"max_requests": 1},
            "criteria": [{"description": "Store one bounded challenge surface artifact"}],
            "workstream": "challenge_surface_synthesis",
            "task_role": "synthesis",
            "output_kind": "artifact",
        }
    )

    with pytest.raises(ValueError, match="executor-owned synthesis requires"):
        _proposal_acceptance_contract(
            proposal,
            OperationPlan(
                objective="Assess",
                current_phase=1,
                total_phases=1,
                phases=[PlanPhase(id=1, title="Challenge surface", status="active")],
            ),
            phase_task_contract=load_phase_task_contract("ctf", 1),
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


def test_web_recon_contract_requires_controller_owned_inventory_synthesis():
    validate_phase_task_proposals(
        load_phase_task_contract("web_recon", 1),
        [
            _proposal("service_entrypoints"),
            _proposal("technology_trust_boundary"),
            _proposal("access_context_session"),
            _proposal(
                "inventory_synthesis",
                role="synthesis",
                depends=("service_entrypoints", "technology_trust_boundary", "access_context_session"),
                output_kind="inventory_manifest",
            ),
        ],
    )


def test_web_recon_contract_accepts_distinct_read_only_mapping_workstreams():
    validate_phase_task_proposals(
        load_phase_task_contract("web_recon", 1),
        [
            _proposal("service_entrypoints"),
            _proposal("technology_trust_boundary"),
            _proposal("safe_read_only_verification"),
            _proposal(
                "inventory_synthesis",
                role="synthesis",
                depends=("service_entrypoints", "technology_trust_boundary", "safe_read_only_verification"),
                output_kind="inventory_manifest",
            ),
        ],
    )


@pytest.mark.parametrize(
    ("module", "workstreams"),
    [
        (
            "web",
            ("unauthenticated_baseline", "authenticated_credential_coverage", "authorization_comparison"),
        ),
        (
            "web_recon",
            ("unauthenticated_posture", "authenticated_access_coverage", "read_only_authorization_comparison"),
        ),
    ],
)
def test_credential_coverage_phase_contracts_accept_their_declared_workstreams(module, workstreams):
    phase_id = 3 if module == "web" else 2
    validate_phase_task_proposals(load_phase_task_contract(module, phase_id), [_proposal(workstream) for workstream in workstreams])


def test_web_contract_accepts_case_and_separator_variants_of_declared_workstreams():
    contract = load_phase_task_contract("web", 3)

    validate_phase_task_proposals(
        contract,
        [
            _proposal("Unauthenticated Baseline"),
            _proposal("authenticated-credential coverage"),
            _proposal("AUTHORIZATION__COMPARISON"),
        ],
    )


def test_web_contract_normalizes_synthesis_workstream_dependencies():
    validate_phase_task_proposals(
        load_phase_task_contract("web", 1),
        [
            _proposal("Entrypoint Technology"),
            _proposal("bounded-crawl"),
            _proposal("CLIENT__SIDE API"),
            _proposal(
                "Inventory Synthesis",
                role="synthesis",
                depends=("entrypoint-technology", "BOUNDED CRAWL", "client_side-api"),
                output_kind="inventory_manifest",
            ),
        ],
    )


@pytest.mark.parametrize("module", ("web", "web_recon"))
def test_credential_coverage_phase_contracts_reject_missing_or_unknown_workstreams(module):
    contract = load_phase_task_contract(module, 3 if module == "web" else 2)
    with pytest.raises(ValueError, match="at least 3"):
        validate_phase_task_proposals(contract, [_proposal("unauthenticated_baseline")])
    with pytest.raises(ValueError, match="not declared"):
        validate_phase_task_proposals(
            contract,
            [_proposal("unauthenticated_baseline"), _proposal("authenticated_credential_coverage"), _proposal("raw_credentials")],
        )


@pytest.mark.parametrize("workstream", ("authorization/comparison", "raw credentials"))
def test_web_contract_rejects_unknown_workstream_variants(workstream):
    contract = load_phase_task_contract("web", 3)

    with pytest.raises(ValueError, match="not declared"):
        validate_phase_task_proposals(
            contract,
            [
                _proposal("unauthenticated_baseline"),
                _proposal("authenticated_credential_coverage"),
                _proposal(workstream),
            ],
        )


def test_contract_loader_rejects_normalization_collisions():
    with pytest.raises(ValueError, match="normalization collisions"):
        contracts._parse_contract(
            "fixture",
            {
                "phase_id": 1,
                "mode": "fanout",
                "min_mapping_tasks": 1,
                "mapping_workstreams": ["auth coverage", "auth_coverage"],
            },
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
        (
            {
                "phase_id": 1,
                "mode": "fanout_with_synthesis",
                "min_mapping_tasks": 1,
                "mapping_workstreams": ["a"],
                "synthesis_workstream": "s",
                "synthesis_execution": "runtime",
            },
            "synthesis_execution",
        ),
    ],
)
def test_contract_parser_rejects_invalid_declarations(raw, message):
    with pytest.raises(ValueError, match=message):
        contracts._parse_contract("fixture", raw)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"phase_id": 1, "mode": "fanout", "min_mapping_tasks": 0, "mapping_workstreams": ["a"]}, "min_mapping_tasks"),
        ({"phase_id": 1, "mode": "fanout", "min_mapping_tasks": 1, "mapping_workstreams": [""]}, "mapping_workstreams"),
        (
            {
                "phase_id": 1,
                "mode": "fanout",
                "min_mapping_tasks": 1,
                "mapping_workstreams": ["a"],
                "allow_direct_single_step": True,
            },
            "direct_single_step_workstreams",
        ),
    ],
)
def test_contract_parser_rejects_remaining_invalid_collection_shapes(raw, message):
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
