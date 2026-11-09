"""Finite criteria and required materiality for new critic invocations."""

import pytest
from pydantic import ValidationError

from modules.techniques.coverage import Requirement
from modules.techniques.critique import BlockingIssue, CriticFindings
from modules.techniques.review_contract import ReviewContract


def test_contract_freezes_exact_requirements_without_author_enrichment():
    requirements = [Requirement(id="method", description="Establish a policy", kind="authored_deliverable")]
    frozen = ReviewContract.freeze(requirements)
    assert "Establish a policy" in frozen.render()
    assert "requirement:method" in frozen.criterion_ids
    assert "checklists" not in frozen.payload
    assert frozen == ReviewContract.freeze(requirements)
    requirements[0].description = "Write a complete test procedure"
    assert frozen.identifier != ReviewContract.freeze(requirements).identifier
    assert "Write a complete" not in frozen.payload


@pytest.mark.parametrize("missing", ["criterion_id", "material_impact"])
def test_native_blockers_require_material_criterion(missing):
    data = {"id": "issue", "category": "authored_content", "problem": "Ordinary access called a violation",
            "requested_correction": "Establish the entitlement restriction", "requirement_ids": ["method"],
            "source_urls": [], "criterion_id": "core:interpretation",
            "material_impact": "The method would report authorized access as a vulnerability"}
    del data[missing]
    with pytest.raises(ValidationError):
        BlockingIssue.model_validate(data)


def test_native_vote_cannot_override_findings():
    with pytest.raises(ValidationError):
        CriticFindings.model_validate({"approved": True, "reason": "Vote supplied instead of findings"})


def test_shared_assembler_adds_controller_sources(scripted_service, markdown):
    import json

    from modules.techniques.coverage import Attribution, SourceSelection, Support
    from modules.techniques.documents import TechniqueDocument
    from modules.techniques.service import AuthoredTechnique, assemble_document

    original = TechniqueDocument(markdown())
    authored = AuthoredTechnique(metadata=original.metadata.model_dump(), body=original.body)
    assert authored.metadata.sources == []
    source = {"url": "https://vendor.example/advisory", "title": "Retained advisory", "text": "Boundary facts"}
    selection = SourceSelection(selected_urls=[source["url"]],
                                attributions=[Attribution(url=source["url"], reason="Relevant facts")],
                                coverage=[Support(requirement_id="method", source_urls=[source["url"]],
                                                  explanation="Technical foundation", gaps=[])],
                                authoritative_sufficient=True, reason="Supported")
    document = assemble_document(authored, [source], [source], selection,
                                 [Requirement(id="method", description="Describe the method")])
    assert document.metadata.sources[0].url == source["url"]
    assert document.metadata.sources[0].selection_reason == "Relevant facts"
    assert document.body == original.body
    assert "requirements" in document.metadata.source_coverage
    json.dumps(document.metadata.model_dump())
    with pytest.raises(ValueError):
        assemble_document(authored, [], [], selection, [])


def test_exact_restrictions_are_part_of_the_shared_contract():
    from modules.techniques.context import MethodologyContext

    requirement = Requirement(id="method", description="Network comparison")
    policy = [{"module": "network", "restriction": "Use declared test peers only"}]
    context = MethodologyContext(methodology_constraints=["No destructive checks"], module_technique_policy=policy)
    contract = ReviewContract.for_context([requirement], context, [])
    assert "restriction:1" in contract.criterion_ids
    assert "restriction:2" in contract.criterion_ids
    assert "No destructive checks" in contract.render()
    assert "Use declared test peers only" in contract.render()
    fallback = ReviewContract.for_context([requirement], None, policy)
    assert "Use declared test peers only" in fallback.render()
    assert fallback.identifier != contract.identifier


@pytest.mark.parametrize("action,replacements,valid", [
    ("retained", ["current"], True), ("revised", ["current"], True), ("withdrawn", [], True),
    ("retained", [], False), ("revised", ["unknown"], False), ("withdrawn", ["current"], False),
])
def test_disposition_accounting_preserves_every_previous_blocker(action, replacements, valid):
    from types import SimpleNamespace

    from modules.techniques.critique import IssueDisposition, validate_issue_dispositions

    disposition = IssueDisposition(previous_issue_reference="previous-blocker:1", action=action,
                                   replacement_issue_ids=replacements, reason="Judged against the current draft")
    def operation():
        validate_issue_dispositions({"previous-blocker:1": {}}, [SimpleNamespace(id="current")], [disposition])
    if valid:
        operation()
    else:
        with pytest.raises(ValueError):
            operation()


def test_disposition_missing_duplicate_and_unknown_references_fail():
    from modules.techniques.critique import IssueDisposition, validate_issue_dispositions

    row = IssueDisposition(previous_issue_reference="previous-blocker:1", action="withdrawn", reason="Unsupported")
    for rows in ([], [row, row]):
        with pytest.raises(ValueError, match="every previous blocker"):
            validate_issue_dispositions({"previous-blocker:1": {}}, [], rows)
    with pytest.raises(ValueError):
        validate_issue_dispositions({}, [], [row])


def test_contract_rejects_duplicate_requirements_and_owns_restriction_ids():
    requirement = Requirement(id="method", description="Compare access")
    with pytest.raises(ValueError, match="unique controller IDs"):
        ReviewContract.freeze([requirement, requirement])
    contract = ReviewContract.freeze([], restrictions=[{"id": "injected", "obligation": "Read only"}])
    assert "restriction:1" in contract.criterion_ids
    assert "injected" not in contract.criterion_ids


@pytest.mark.parametrize("requirement_id", ["method", "core:custom-request", "requirement:original"])
def test_reference_catalogs_preserve_exact_request_ids(requirement_id):
    import json

    frozen = ReviewContract.freeze([Requirement(id=requirement_id, description="Describe comparison")])
    fields = json.loads(frozen.payload)["reference_fields"]
    assert fields["requirement_ids"] == {"allowed_values": [requirement_id], "empty_list_allowed": True}
    assert f"requirement:{requirement_id}" in fields["criterion_id"]["allowed_values"]
    assert "core:interpretation" in fields["criterion_id"]["allowed_values"]
    assert "Do not copy an acceptance criterion" in frozen.render()
    assert "also apply on correction" in frozen.render()


def test_native_reference_field_descriptions_match_controller_rules():
    from modules.techniques.critique import AssessorFindings

    for schema, definition in ((CriticFindings, "BlockingIssue"), (AssessorFindings, "MaterialAssessmentFinding")):
        field = schema.model_json_schema()["$defs"][definition]["properties"]["requirement_ids"]
        assert "request requirement IDs" in field["description"]
        assert "criterion_id" in field["description"]
        assert "[]" in field["description"]


def test_shared_contract_distinguishes_support_scope_and_explicit_placement():
    requirement = Requirement(id="body", description="Put source limitations in the body")
    contract = ReviewContract.freeze([requirement])
    rendered = contract.render()
    assert "Put source limitations in the body" in rendered
    assert "Model knowledge, reasoning and original methodology are valid content bases" in rendered
    assert "Missing citations alone are not defects" in rendered
    assert "concrete technical errors" in rendered
    assert "attribution in metadata counts" in rendered
    assert "body placement requires an explicit obligation" in rendered
    assert "requirement:body" in contract.criterion_ids
