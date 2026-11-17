import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("comparison_probe", Path(__file__).with_name("run.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("mode", ["shared", "vulnerable", "unknown"])
def test_views_preserve_same_observations_without_computed_classification(mode):
    records = probe.observations(probe.fixture(), mode)
    flat = probe.render(records, False)
    comparison = probe.render(records, True)
    assert len(records) == 6
    for text in (flat, comparison):
        for ref in ("identity", "policy", "request-0", "request-1", "request-2", "request-3"):
            assert ref in text
        assert "not authorized" in text
        assert "classification" not in text
    assert "comparisons[4]" in comparison
    assert "recorded_policy" in comparison and "recorded_response" in comparison
    assert "expected_outcome" not in comparison


@pytest.mark.parametrize("failed", [False, True])
def test_probe_records_every_arm_and_preserves_failures(tmp_path, monkeypatch, failed):
    import json
    from types import SimpleNamespace

    module = probe.fixture()
    monkeypatch.setattr(probe, "fixture", lambda: module)
    monkeypatch.setattr(probe, "load_dotenv", lambda *args: None)
    monkeypatch.chdir(tmp_path)
    calls = []

    def structured(role, prompt, schema, tools):
        calls.append(prompt)
        if failed:
            raise RuntimeError("Unavailable model")
        return schema(classification="inconclusive", evidence_ids=["identity", "policy", "request-1"],
                      explanation="Scripted transport specimen")

    monkeypatch.setattr(probe, "TechniqueRoleRunner", lambda *args: SimpleNamespace(structured=structured))
    probe.main()
    root = tmp_path / "outputs/technique_review_trials/comparison-effect-20261117"
    results = json.loads((root / "results.json").read_text())
    assert len(calls) == len(results) == 6
    assert [row["arm"] for row in results] == ["flat", "comparison"] * 3
    assert all(("unresolved" in row) is failed for row in results)
    assert len(list(root.glob("*-prompt.txt"))) == 6
    with pytest.raises(FileExistsError):
        probe.main()


@pytest.mark.parametrize("mode", ["shared", "vulnerable", "unknown"])
def test_effect_fields_separate_metadata_and_returned_objects_without_judgment(mode):
    records = probe.observations(probe.fixture(), mode)
    text = probe.render_effects(records)
    assert "response_error" in text and "returned_object" in text
    assert "not authorized" in text and "record-A" in text and "record-B" in text
    assert "classification" not in text and "expected_outcome" not in text
    assert "identity" in text and "policy" in text
    if mode == "unknown":
        assert "Policy record unavailable" in text


@pytest.mark.parametrize("provenance", [False, True])
def test_effect_probe_runs_three_cases(tmp_path, monkeypatch, provenance):
    import json
    from types import SimpleNamespace

    module = probe.fixture()
    monkeypatch.setattr(probe, "fixture", lambda: module)
    monkeypatch.setattr(probe, "load_dotenv", lambda *args: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(probe, "TechniqueRoleRunner", lambda *args: SimpleNamespace(
        structured=lambda role, prompt, schema, tools: schema(
            classification="inconclusive", evidence_ids=["identity", "policy", "request-1"],
            explanation="Scripted transport specimen",
        ),
    ))
    probe.main(effects=True, provenance=provenance)
    arm = "effects-provenance" if provenance else "effects"
    path = tmp_path / "outputs/technique_review_trials/comparison-effect-20261117" / arm / "results.json"
    assert [row["arm"] for row in json.loads(path.read_text())] == [arm] * 3


def test_provenance_view_declares_every_join_source():
    text = probe.render_effects(probe.observations(probe.fixture(), "shared"), provenance=True)
    assert "source_refs" in text
    for index in range(4):
        assert f'["identity"; "policy"; "request-{index}"]' in text


def test_bound_comparisons_have_distinct_ids_and_explicit_source_links():
    text = probe.render_effects(probe.observations(probe.fixture(), "shared"), bound=True)
    assert "comparison-0" in text and "source_refs" in text
    assert '["identity"; "policy"; "request-0"]' in text


def test_bound_probe_resolves_only_declared_source_links(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    module = probe.fixture()
    monkeypatch.setattr(probe, "fixture", lambda: module)
    monkeypatch.setattr(probe, "load_dotenv", lambda *args: None)
    monkeypatch.chdir(tmp_path)

    def structured(role, prompt, schema, tools):
        with pytest.raises(ValueError):
            schema(classification="inconclusive", evidence_ids=["unknown"], explanation="Invalid reference")
        return schema(classification="inconclusive", evidence_ids=["comparison-1", "identity"],
                      explanation="Scripted transport specimen")

    monkeypatch.setattr(probe, "TechniqueRoleRunner", lambda *args: SimpleNamespace(structured=structured))
    probe.main(bound=True)
    results = json.loads((tmp_path / (
        "outputs/technique_review_trials/comparison-effect-20261117/effects-bound/results.json"
    )).read_text())
    assert all(row["resolved_evidence_ids"] == ["identity", "policy", "request-1"] for row in results)
    assert all(row["discovery_evidence"] for row in results)


def test_access_contract_changes_label_meaning_without_computing_verdict(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    module = probe.fixture()
    monkeypatch.setattr(probe, "fixture", lambda: module)
    monkeypatch.setattr(probe, "load_dotenv", lambda *args: None)
    monkeypatch.chdir(tmp_path)

    def structured(role, prompt, schema, tools):
        with pytest.raises(ValueError):
            schema(classification="violation", evidence_ids=["policy"], explanation="Ambiguous label")
        return schema(classification="entitlements_unavailable", evidence_ids=["identity", "policy", "request-1"],
                      explanation="Scripted output schema specimen")

    monkeypatch.setattr(probe, "TechniqueRoleRunner", lambda *args: SimpleNamespace(structured=structured))
    probe.main(narrow=True)
    path = tmp_path / "outputs/technique_review_trials/comparison-effect-20261117/access-contract/results.json"
    results = json.loads(path.read_text())
    assert len(results) == 6
    assert all(row["normalized_classification"] == "inconclusive" for row in results)
    assert [row["arm"] for row in results] == ["access-flat", "access-effects"] * 3


def test_real_role_transport_uses_workflow_boundary(monkeypatch):
    from types import SimpleNamespace

    import strands

    from modules.agents import native_structured
    from modules.config.models import factory

    captured = {}
    monkeypatch.setattr(strands, "Agent", lambda **kwargs: captured.update(kwargs) or SimpleNamespace())
    monkeypatch.setattr(factory, "create_strands_model", lambda *args: "fixture-model")
    monkeypatch.setattr(factory, "require_prompt_token_limit", lambda *args: 48000)
    payload = {"classification": "inconclusive", "evidence_ids": ["policy"], "explanation": "Unavailable policy"}

    def invoke(agent, prompt, schema, investigate):
        assert agent._prompt_token_limit == 48000
        assert not investigate
        return SimpleNamespace(structured_output=payload)

    monkeypatch.setattr(native_structured, "invoke_structured", invoke)
    assert probe.task_decision("task_evaluator", "Data", probe.fixture().Decision, []).model_dump() == payload
    assert "Evaluator Role Boundary" in captured["system_prompt"]
    assert "specialist in reusable security techniques" not in captured["system_prompt"]


def test_execution_role_main_uses_task_transport(tmp_path, monkeypatch):
    import json

    module = probe.fixture()
    monkeypatch.setattr(probe, "fixture", lambda: module)
    monkeypatch.setattr(probe, "load_dotenv", lambda *args: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(probe, "task_decision", lambda role, prompt, schema, tools: schema(
        classification="inconclusive", evidence_ids=["identity", "policy", "request-1"], explanation="Specimen",
    ))
    probe.main(execution_role=True)
    path = tmp_path / "outputs/technique_review_trials/comparison-effect-20261117/execution-role/results.json"
    assert len(json.loads(path.read_text())) == 6
