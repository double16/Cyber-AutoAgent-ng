"""Fixture integrity and lossless projection checks for the experimental renderer."""

import hashlib
import json
from pathlib import Path

import pytest
from candidate import render

from modules.handlers.utils import sanitize_toon_value
from modules.tools.hypotheses import HypothesisSet

CASES = json.loads((Path(__file__).parent / "cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_projection_preserves_procedure_and_declared_identity_without_inference(case):
    publication = HypothesisSet.model_validate(case["publication"])
    original = publication.model_dump_json()
    digest = hashlib.sha256(original.encode()).hexdigest()
    text = render(publication, {"artifact_ref": "artifact:frozen.json", "sha256": digest})
    assert publication.model_dump_json() == original
    assert digest in text and text.endswith("\n")
    h = publication.subjects[0].hypotheses[0]
    for key in ("safe_test_steps", "required_evidence"):
        for index, value in enumerate(getattr(h, key), 1):
            assert f"{key},{index},{sanitize_toon_value(value)}" in text
    declaration = text.split("authentication_requirement[1]{comparison,roles}:\n", 1)[1].splitlines()[0]
    assert declaration == "  " + h.authentication_requirement.comparison + "," + sanitize_toon_value(
        json.dumps(h.authentication_requirement.roles),
    )
    assert text.index("safe_test_steps,1,") < text.index("authentication_requirement[1]")
    if case["name"] == "http_missing_admin_baseline":
        assert "authenticated admin session" in text
        assert declaration == "  none,[]"


def test_no_current_records_do_not_invent_a_procedure():
    raw = CASES[0]["publication"]
    publication = HypothesisSet.model_validate(raw)
    publication.subjects[0].hypotheses = []
    text = render(publication, {"artifact_ref": "artifact:frozen.json", "sha256": "digest"})
    assert "### Hypothesis" not in text
    assert "authentication_requirement[1]" not in text
    assert text.endswith("\n")


def test_frozen_views_reproduce_inputs_before_production_contract_clarification():
    from replay import views

    contracts = json.loads(Path(__file__).with_name("contracts.json").read_text())
    publication = HypothesisSet.model_validate(CASES[0]["publication"])
    baseline, candidate = views(publication)
    assert hashlib.sha256(baseline.encode()).hexdigest() == contracts["first_baseline_input_sha256"]
    assert hashlib.sha256(candidate.encode()).hexdigest() == contracts["first_candidate_input_sha256"]
