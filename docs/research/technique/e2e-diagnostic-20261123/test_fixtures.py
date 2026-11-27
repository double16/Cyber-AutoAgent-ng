"""Fixture truth and grading stay independent of model output wording."""

import pytest
from fixtures import CASES, Fixture


@pytest.mark.parametrize("kind,vulnerable", CASES)
def test_owned_fixture_transport_and_semantic_grade(tmp_path, kind, vulnerable):
    fixture = Fixture(tmp_path / "fixture", kind, vulnerable)
    try:
        policy = fixture.context("policy")
        fixture.context("interface")
        fixture.context("identity")
        principal = "anonymous" if kind == "http" else "first"
        baseline = fixture.probe(principal, "red" if kind == "network" else "inside")
        variation = fixture.probe(principal, "blue" if kind == "network" else "outside")
        missing = fixture.probe(principal, "missing")
        expected = "violation" if vulnerable else "no_violation_observed"
        ids = [policy["id"], baseline["id"], variation["id"], missing["id"]]
        assert fixture.grade(expected, ids)["supported_correct"]
        assert fixture.grade(expected, ids)["inert_control_executed"]
        assert not fixture.grade("inconclusive", ids)["supported_correct"]
        assert not fixture.grade(expected, [baseline["id"]])["supported_correct"]
        assert not fixture.grade(expected, [*ids, "invented"])["supported_correct"]
        if kind == "http":
            assert variation["response"]["location"].startswith("https://owned-other") is vulnerable
        elif kind == "network":
            assert ("record" in variation["response"]) is vulnerable
            assert variation["response"]["status"] == "ok"
            own = fixture.probe("second", "blue")
            assert own["response"]["record"]["owner"] == "blue"
        else:
            assert (variation["response"].get("content") == "owned-outside-marker") is vulnerable
        assert "unobserved" in fixture.source()
        with pytest.raises(ValueError):
            fixture.context("unknown")
        with pytest.raises(ValueError):
            fixture.probe("invented", "inside")
        fixture.probes = 16
        with pytest.raises(ValueError, match="cap"):
            fixture.probe(principal, "red" if kind == "network" else "inside")
    finally:
        fixture.close()


def test_unknown_fixture_kind_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="kind"):
        Fixture(tmp_path, "unknown", False)
