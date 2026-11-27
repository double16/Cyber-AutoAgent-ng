"""Frozen-input boundaries and isolated production generation."""

import hashlib
import json

import pytest

from modules.techniques.calibration_budget import CalibrationBudget
from modules.techniques.generation_pilot import FrozenResearch, run_generation


def subject():
    case = {"id": "method", "family": "http", "request": "JWT handling",
            "requirements": [{"id": "method", "description": "JWT handling", "kind": "authored_deliverable"}],
            "sources": [{"url": "https://vendor.example/advisory", "title": "Advisory",
                         "text": "Signature validation enforces token integrity."}]}
    case["input_sha256"] = hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return case


def test_frozen_research_returns_independent_records_without_new_evidence():
    research = FrozenResearch(subject()["sources"])
    first = research.batch("query", set())
    first[0]["text"] = "changed"
    assert research.batch("query", set())[0]["text"] != "changed"
    assert research.batch("query", {first[0]["url"]}) == []


@pytest.mark.parametrize("defect", ["draft", "hash"])
def test_invalid_inputs_fail_before_creating_storage(tmp_path, defect):
    case = subject()
    case["draft" if defect == "draft" else "input_sha256"] = "invalid"
    with pytest.raises(ValueError):
        run_generation(case, tmp_path / "never", None, None)
    assert not (tmp_path / "never").exists()


def test_production_generation_is_isolated_and_keeps_human_judgment_pending(tmp_path, scripted_service):
    budget = CalibrationBudget(tmp_path / "budget.json")
    outcome = run_generation(subject(), tmp_path / "trial", scripted_service.service.runner, budget)
    assert outcome["status"] == "generated_pending_human_adjudication", outcome
    assert outcome["human_adjudication"] == "pending"
    assert (tmp_path / "trial/pilot.db").exists()
    assert (tmp_path / "trial/generated.md").exists()
    assert outcome["counts"]["roles"] > 0


def test_failures_remain_in_the_denominator(tmp_path):
    def fail(*args):
        raise RuntimeError("Unavailable model")

    outcome = run_generation(subject(), tmp_path / "trial", fail, CalibrationBudget(tmp_path / "budget.json"))
    assert outcome["status"] == "unresolved"
    assert outcome["human_adjudication"] == "pending"
    assert not (tmp_path / "trial/generated.md").exists()


def test_structured_runner_uses_production_schema_adapter(tmp_path, scripted_service):
    from types import SimpleNamespace

    def structured(role, prompt, schema, tools):
        payload = json.loads(scripted_service.service.runner(role, prompt, tools))
        if role == "technique_author":
            allowed = schema.model_fields["metadata"].annotation.model_fields
            payload["metadata"] = {key: value for key, value in payload["metadata"].items() if key in allowed}
        if schema.__name__ == "PublicationReview":
            from conftest import publication_payload

            payload = publication_payload(payload, str(prompt), tools)
        return schema.model_validate(payload)

    outcome = run_generation(subject(), tmp_path / "trial", SimpleNamespace(structured=structured),
                             CalibrationBudget(tmp_path / "budget.json"))
    assert outcome["status"] == "generated_pending_human_adjudication", outcome


def test_provider_attempts_are_counted_and_transport_restored(tmp_path, scripted_service):
    import asyncio

    class Client:
        async def send(self, **kwargs):
            return kwargs

    original = Client.send

    def runner(*args):
        asyncio.run(Client().send(request="scripted"))
        return scripted_service.service.runner(*args)

    outcome = run_generation(subject(), tmp_path / "trial", runner,
                             CalibrationBudget(tmp_path / "budget.json"), transport=(Client, "send"))
    assert outcome["status"] == "generated_pending_human_adjudication"
    assert outcome["counts"]["requests"] == outcome["counts"]["roles"]
    assert Client.send is original
    assert len(list((tmp_path / "trial").glob("provider-request-*.json"))) == outcome["counts"]["requests"]


def test_rejected_author_and_exact_review_subject_exports_are_retained(tmp_path, scripted_service):
    state = scripted_service
    original = state.service.runner
    authors = []

    def runner(role, prompt, tools):
        if role == "technique_author":
            authors.append(prompt)
            if len(authors) == 1:
                return "invalid authored output"
        return original(role, prompt, tools)

    outcome = run_generation(subject(), tmp_path / "trial", runner, CalibrationBudget(tmp_path / "budget.json"))
    assert outcome["status"] == "generated_pending_human_adjudication"
    builds = tmp_path / "trial/techniques/builds"
    rejected = list(builds.glob("*/attempt-*.json"))
    reviewed = list(builds.glob("*/review-*.json"))
    assert rejected and reviewed
    assert json.loads(rejected[0].read_text())["response"] == "invalid authored output"
    assert "document" in json.loads(reviewed[0].read_text())


def test_provider_failure_is_counted_without_restarting_budget(tmp_path):
    import asyncio

    class Client:
        async def send(self, **kwargs):
            raise RuntimeError("Provider failed")

    original = Client.send

    def runner(*args):
        asyncio.run(Client().send())

    budget = CalibrationBudget(tmp_path / "budget.json")
    outcome = run_generation(subject(), tmp_path / "trial", runner, budget, transport=(Client, "send"))
    assert outcome["status"] == "unresolved"
    assert outcome["counts"]["requests"] == 1
    assert Client.send is original
    assert budget.counts()["requests"] == 1


@pytest.mark.parametrize("mode", ["native", "tool"])
def test_full_generation_through_scripted_sdk(tmp_path, scripted_service, mode):
    from modules.techniques import roles
    from modules.techniques.review_calibration_offline import OfflineReviewModel, configure_offline_roles
    from modules.techniques.service import AuthoredOutput

    payloads = []

    def capture(role, prompt, tools):
        response = scripted_service.service.runner(role, prompt, tools)
        payload = json.loads(response)
        if role == "technique_author":
            fields = AuthoredOutput.model_fields["metadata"].annotation.model_fields
            payload["metadata"] = {key: value for key, value in payload["metadata"].items() if key in fields}
        if role == "technique_critique_assessor":
            payload = {"critic_supported": True, "reason": "Scripted contract check only",
                       "interpretation_reviews": [], "findings": []}
        if role == "technique_critic":
            from conftest import publication_payload

            payload = publication_payload(payload, str(prompt), tools)
        payloads.append(payload)
        return response

    recorded = run_generation(subject(), tmp_path / "recorded", capture,
                              CalibrationBudget(tmp_path / "record-budget.json"))
    assert recorded["status"] == "generated_pending_human_adjudication"

    class GenerationModel(OfflineReviewModel):
        def payload(self, properties):
            payload = payloads.pop(0)
            return {key: value for key, value in payload.items() if key in properties}

    model = GenerationModel((), "unused", mode)
    with configure_offline_roles(roles, (), "unused", mode):
        roles.create_strands_model = lambda *_args: model
        runner = roles.TechniqueRoleRunner("ollama", "offline-generation-contract")
        result = run_generation(subject(), tmp_path / "sdk", runner,
                                CalibrationBudget(tmp_path / "sdk-budget.json"))
    assert result["status"] == "generated_pending_human_adjudication", result
    assert not payloads
    assert result["counts"]["requests"] == 0
    assert model.calls >= result["counts"]["roles"]


def test_diagnostic_session_is_opened_and_closed_with_trial(tmp_path, scripted_service):
    from modules.techniques.diagnostics import DiagnosticSession

    diagnostics = DiagnosticSession(quiet=True)
    result = run_generation(subject(), tmp_path / "trial", scripted_service.service.runner,
                            CalibrationBudget(tmp_path / "budget.json"), diagnostics=diagnostics)
    assert result["status"] == "generated_pending_human_adjudication"
    assert list((tmp_path / "trial/techniques/logs").glob("*.jsonl"))
