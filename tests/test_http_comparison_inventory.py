"""HTTP assertions survive inventory production and bind only to selected task identities."""

import copy
import json
from dataclasses import replace

import pytest

from modules.agents import multi_agent_workflow as workflow
from modules.operation_plugins.web.comparison import bind_comparison_contract, inventory_comparison_contract
from modules.tools import recon_inventory_manifest as recon
from tests.test_authentication_comparisons import matrix_case as matrix_case
from tests.test_controller_comparison import capture_case as capture_case
from tests.test_controller_comparison import explicit_case
from tests.test_worker_flows import worker_case as worker_case


def template_case(comparison="none"):
    contract, _captures = explicit_case()
    contract["comparison"] = comparison
    contract["assertions"][1]["identity"] = "account:1"
    return contract


@pytest.mark.parametrize("comparison,selectors,roles,anonymous,expected", [
    ("none", ["anonymous", "role:member"], {"a": "member"}, True, ["anonymous", "a"]),
    ("account", ["role:member", "role:member"], {"a": "member", "b": "member"}, False, ["a", "b", "a", "b"]),
    ("account", ["account:1", "account:2"], {"a": "member", "b": "member"}, False, ["a", "b"]),
    ("role", ["role:member", "role:👑"], {"a": "member", "b": "👑"}, False, ["a", "b"]),
    ("none", ["anonymous", "role:missing"], {"a": "member"}, True, None),
    ("none", ["anonymous", "account:2"], {"a": "member"}, True, None),
    ("none", ["anonymous", "account:1"], {"a": "member"}, False, None),
])
def test_assertion_selectors_bind_to_exact_authorized_identities(comparison, selectors, roles, anonymous, expected):
    contract = template_case(comparison)
    for assertion, selector in zip(contract["assertions"], selectors, strict=True):
        assertion["identity"] = selector
    result = bind_comparison_contract(
        contract, comparison=comparison, credential_roles=roles, include_anonymous=anonymous,
    )
    if expected is None:
        assert result is None
    else:
        assert [assertion["identity"] for assertion in result["assertions"]] == expected
    assert bind_comparison_contract(contract, comparison="tenant", credential_roles=roles,
                                    include_anonymous=anonymous) is None
    assert bind_comparison_contract(None, comparison=comparison, credential_roles=roles,
                                    include_anonymous=anonymous) is None


@pytest.mark.parametrize("defect", [None, "wrong_url", "wrong_method", "inferred", "missing_provenance", "duplicate"])
def test_per_interaction_contract_requires_observed_matching_request(defect):
    contract = template_case()
    item = {"id": "endpoint", "kind": "endpoint", "value": contract["url"], "attributes": {"interaction": {
        "interface": "http", "operations": ["GET"], "operation_provenance": "observed",
        "extensions": {"http_comparison": contract},
    }}}
    interaction = item["attributes"]["interaction"]
    if defect == "wrong_url":
        contract["url"] += "-wrong"
    elif defect == "wrong_method":
        contract["method"] = "HEAD"
    elif defect == "inferred":
        interaction["operation_provenance"] = "inferred"
    elif defect == "missing_provenance":
        interaction.pop("operation_provenance")
    items = [item]
    ids = ["endpoint"]
    if defect == "duplicate":
        items.append({**copy.deepcopy(item), "id": "another"})
        ids.append("another")
    if defect is None:
        assert inventory_comparison_contract(items, ids) == contract
        assert inventory_comparison_contract(items, ["unassigned"]) is None
        assert inventory_comparison_contract([{**item, "kind": "parameter"}], ids) is None
    else:
        with pytest.raises(ValueError):
            inventory_comparison_contract(items, ids)


def test_production_inventory_pipeline_preserves_contract_through_activation(capture_case, tmp_path, monkeypatch):
    controller, task, store, plan, phase, _calls, manifest = capture_case
    contract = template_case()
    contract["url"] = manifest["items"][0]["value"]
    (tmp_path / "resource-proof").write_text("Observed suitable application operation")
    manifest["items"][0]["attributes"]["interaction"].update({
        "operation_provenance": "observed", "extensions": {"http_comparison": contract},
    })
    source = tmp_path / "mapping.json"
    source.write_text(json.dumps(manifest))
    recon.consolidate_recon_artifacts([str(source)], str(tmp_path / "merged.json"), target_id="target",
                                     target=plan.targets[0].value)
    _inventory, digest = workflow._load_inventory_manifest("artifact:merged.json")
    task = replace(task, task_uid="pending-comparison", status="pending", acceptance=replace(
        task.acceptance, manifest_hash="", basis=replace(task.acceptance.basis, snapshot_hash=digest,
                                                        source_refs=["artifact:merged.json"]),
    ), recovery_context={**task.recovery_context, "phase_task_contract": {
        **task.recovery_context["phase_task_contract"], "execution_owner": "executor",
    }})
    store.store_task("OP_AUTH", task)
    frozen_hash = task.acceptance.manifest_hash
    active = controller._activate_task(task)
    assert active.recovery_context["phase_task_contract"]["execution_owner"] == "controller"
    binding = active.recovery_context["http_comparison_contract"]
    assert binding["template"] == contract and binding["prerequisite_digests"]
    assert active.acceptance.manifest_hash == frozen_hash
    monkeypatch.setattr(workflow, "authenticated_http_request", lambda *_args, **_kwargs: json.dumps({
        "status_code": 200, "body": "resource id valid", "headers": {"Content-Type": "text/plain"},
    }))
    monkeypatch.setattr(controller, "_current_unauthenticated_response", lambda *_args: {
        "status": 403, "body": "policy denied", "headers": {"Content-Type": "text/plain"},
        "url": contract["url"], "method": "GET", "outcome": "response",
    })
    monkeypatch.setattr(controller, "_evaluate_task", lambda *_args, **_kwargs: pytest.fail(
        "explicit assertions matched",
    ))
    controller._run_task_in_trace(plan, phase, active)
    updated = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == active.task_uid)
    assert updated.status == "done", updated.status_reason
    assert updated.recovery_context["completion_review"]["mode"] == "assertions"


@pytest.mark.parametrize("interface", ["http", "ssh", "filesystem"])
def test_generic_interaction_extensions_preserve_protocol_metadata(interface):
    from modules.tools.memory import _normalize_inventory_interaction

    attributes = {"interaction": {"interface": interface, "operations": ["read"],
                                   "operation_provenance": "observed", "extensions": {"adapter": {"value": 1}}}}
    assert not _normalize_inventory_interaction("subject", attributes)
    for change in ({"operation_provenance": "guessed"}, {"extensions": []}):
        with pytest.raises(ValueError):
            _normalize_inventory_interaction("subject", {"interaction": {**attributes["interaction"], **change}})


def test_conflicting_assertions_cannot_be_merged_into_an_invented_policy():
    first = {"http_comparison": template_case()}
    second = copy.deepcopy(first)
    second["http_comparison"]["assertions"][0]["status"] = 404
    merged = recon._merge_inventory_value(first, second)
    assert merged == {"http_comparison_conflict": True}
    assert recon._merge_inventory_value(merged, first) == merged


@pytest.mark.parametrize("reverse", [False, True])
def test_consolidation_keeps_observed_assertions_and_inferred_methods_separate(reverse):
    contract = template_case()
    observed = {"schema_version": 1, "unassessed_gaps": [], "items": [{
        "id": "endpoint", "target_id": "target", "kind": "endpoint", "value": contract["url"],
        "attributes": {"interaction": {
            "interface": "http", "operations": ["GET"], "operation_provenance": "observed",
            "extensions": {"http_comparison": contract},
        }},
    }]}
    inferred = copy.deepcopy(observed)
    inferred["items"][0]["attributes"]["interaction"] = {
        "interface": "http", "operations": ["GET", "DELETE"], "operation_provenance": "inferred",
    }
    sources = [("artifact:bundle", inferred), ("artifact:network", observed)]
    if reverse:
        sources.reverse()
    merged = recon._merge_inventory_manifests(sources)
    item = merged["items"][0]
    assert item["attributes"]["interaction"]["operation_provenance_by_operation"] == {
        "GET": "observed", "DELETE": "inferred",
    }
    assert item["attributes"]["interaction"]["operation_provenance"] == "inferred"
    # Re-ingestion validates the observed GET despite a conservatively inferred aggregate.
    recon._canonicalize_inventory_manifest_source(merged, target_id="target")
    assert inventory_comparison_contract(merged["items"], ["endpoint"]) == contract
    interaction = item["attributes"]["interaction"]
    interaction["extensions"]["http_comparison"]["method"] = "HEAD"
    interaction["operations"].append("HEAD")
    interaction["operation_provenance_by_operation"]["HEAD"] = "inferred"
    with pytest.raises(ValueError, match="observed operation"):
        inventory_comparison_contract(merged["items"], ["endpoint"])


@pytest.mark.parametrize("reverse", [False, True])
def test_unknown_method_provenance_remains_unknown_after_merging_observations(reverse):
    first = {"interaction": {"operations": ["GET"]}}
    second = {"interaction": {"operations": ["POST"], "operation_provenance": "observed"}}
    sources = [("artifact:first", first), ("artifact:second", second)]
    if reverse:
        sources.reverse()
    merged = recon._merge_inventory_manifests([
        (reference, {"items": [{"id": "endpoint", "kind": "endpoint", "target_id": "target",
                                "value": "https://target.test/resource", "attributes": attributes}]})
        for reference, attributes in sources
    ])
    interaction = merged["items"][0]["attributes"]["interaction"]
    assert interaction["operation_provenance_by_operation"] == {"GET": "unknown", "POST": "observed"}
    assert "operation_provenance" not in interaction


def test_single_role_assertion_applies_to_both_selected_accounts():
    contract = template_case("account")
    contract["assertions"] = [{**contract["assertions"][1], "identity": "role:member"}]
    bound = bind_comparison_contract(contract, comparison="account", credential_roles={"a": "member", "b": "member"},
                                     include_anonymous=False)
    assert [assertion["identity"] for assertion in bound["assertions"]] == ["a", "b"]


def test_explicit_assertions_recheck_prerequisite_digests(capture_case, tmp_path, monkeypatch):
    controller, task, store, plan, phase, calls, manifest = capture_case
    contract = template_case()
    contract["url"] = manifest["items"][0]["value"]
    proof = tmp_path / "resource-proof"
    proof.write_text("Original suitable-operation proof")
    manifest["items"][0]["attributes"]["interaction"].update({
        "operation_provenance": "observed", "extensions": {"http_comparison": contract},
    })
    (tmp_path / "inventory.json").write_text(json.dumps(manifest))
    _manifest, digest = workflow._load_inventory_manifest("artifact:inventory.json")
    task = replace(task, task_uid="digest-comparison", status="pending", acceptance=replace(
        task.acceptance, manifest_hash="", basis=replace(task.acceptance.basis, snapshot_hash=digest),
    ))
    store.store_task("OP_AUTH", task)
    active = controller._activate_task(task)
    # Freeze the original proof, then replace it before execution. Matching packets cannot restore that proof.
    proof.write_text("Changed proof")
    monkeypatch.setattr(workflow, "authenticated_http_request", lambda *_args, **_kwargs: json.dumps({
        "status_code": 200, "body": "resource id valid", "headers": {"Content-Type": "text/plain"},
    }))
    monkeypatch.setattr(controller, "_current_unauthenticated_response", lambda *_args: {
        "status": 403, "body": "policy denied", "headers": {"Content-Type": "text/plain"},
        "url": contract["url"], "method": "GET", "outcome": "response",
    })
    result = controller._run_controller_web_authenticated_coverage(plan, phase, active)
    assert result.status == "needs_semantic_review"
    assert store.get_acceptance_results("OP_AUTH", task.task_uid) == []
    assert calls == []


def test_inferred_and_observed_methods_remain_distinct():
    inferred = recon._record("https://target.test/path")
    observed = recon._record("https://target.test/path", method="POST")
    assert inferred["method"] == "GET" and inferred["operation_provenance"] == "inferred"
    manifest = recon.records_to_inventory_manifest([observed], target_id="target")
    endpoint = next(item for item in manifest["items"] if item["kind"] == "endpoint")
    assert endpoint["attributes"]["interaction"]["operation_provenance"] == "observed"
    assert endpoint["attributes"]["interaction"]["operations"] == ["POST"]


@pytest.mark.parametrize("case,expected", [
    ("absent", "assertions_absent"), ("invalid", "assertions_invalid"),
    ("binding", "identity_assertion_binding_unavailable"),
    ("prerequisite", "prerequisite_evidence_unavailable_or_changed"),
    ("response", "response_assertions_unmatched"), ("capture", "capture_incomplete"),
])
def test_semantic_fallback_records_specific_reason(capture_case, tmp_path, case, expected):
    controller, task, store, _plan, _phase, _calls, manifest = capture_case
    contract = template_case()
    contract["url"] = manifest["items"][0]["value"]
    if case != "prerequisite":
        (tmp_path / "resource-proof").write_text("Known operation proof")
    if case == "binding":
        contract["comparison"] = "account"
    interaction = manifest["items"][0]["attributes"]["interaction"]
    if case != "absent":
        interaction.update({"operation_provenance": "observed", "extensions": {"http_comparison": contract}})
    if case == "invalid":
        interaction["operation_provenance"] = "inferred"
    _contract, captures = explicit_case()
    for response in [*captures["responses"], *captures["unauthenticated_responses"]]:
        response["url"] = contract["url"]
    if case == "response":
        captures["responses"][0]["status"] = 404
    if case == "capture":
        captures["coverage_gaps"] = ["No response captured"]
    result = controller._controller_comparison_review(
        task, manifest, captures, "artifact:observed.html", ("credential",),
    )
    assert result.status == "needs_semantic_review"
    saved = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == task.task_uid)
    assert saved.recovery_context["completion_review"]["reason"] == expected


def test_unknown_provenance_types_are_rejected_as_invalid_metadata():
    from modules.tools.memory import _normalize_inventory_interaction

    for value in ([], {}, None, 123):
        with pytest.raises(ValueError, match="observed\\|inferred"):
            _normalize_inventory_interaction("item", {"interaction": {"operation_provenance": value}})


def test_invalid_extension_source_is_reported_without_discarding_valid_inventory(capture_case, tmp_path):
    _controller, _task, _store, plan, _phase, _calls, manifest = capture_case
    malformed = copy.deepcopy(manifest)
    malformed["items"][0]["attributes"]["interaction"]["extensions"] = []
    invalid = tmp_path / "invalid-metadata.json"
    valid = tmp_path / "valid-metadata.json"
    invalid.write_text(json.dumps(malformed))
    valid.write_text(json.dumps(manifest))
    result = recon.consolidate_recon_artifacts([str(invalid), str(valid)], str(tmp_path / "retained.json"),
                                              target_id="target", target=plan.targets[0].value)
    assert len(result["skipped_artifacts"]) == 1
    assert "extensions must be an object" in result["skipped_artifacts"][0]["reason"]
    assert len(result["processed_artifacts"]) == 1
    retained = json.loads((tmp_path / "retained.json").read_text())
    assert retained["items"][0]["id"] == "endpoint"
