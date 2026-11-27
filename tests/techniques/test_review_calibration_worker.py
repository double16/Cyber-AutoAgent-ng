"""Worker execution retains complete inputs and does not leak adjudication into model prompts."""

import asyncio
import json
from pathlib import Path

import pytest

from modules.techniques.calibration_budget import CalibrationBudget
from modules.techniques.critique import Critique, IssueAssessment, ReviewSnapshot
from modules.techniques.review_calibration_worker import main, run_job


@pytest.fixture
def job(tmp_path, markdown):
    budget = tmp_path / "budget.json"
    CalibrationBudget(budget)
    return {"root": str(tmp_path), "budget": str(budget), "model": "offline-model",
            "budget_module": str(Path("src/modules/techniques/calibration_budget.py").resolve()),
            "offline_module": str(Path("src/modules/techniques/review_calibration_offline.py").resolve()),
            "case": {"id": "case", "request": "Review the declared method", "draft": markdown(),
                     "requirements": [{"id": "method", "description": "Review access", "kind": "authored_deliverable"}],
                     "sources": [{"url": "https://vendor.example/advisory", "title": "Advisory", "text": "Boundary facts"}],
                     "adjudication_reason": "HUMAN_LABEL_MUST_NOT_REACH_MODEL"}}


def test_worker_dispatches_complete_input_and_restores_transport(job):
    class Client:
        async def chat(self, **kwargs):
            return {"response": "scripted"}

    class Runner:
        def __init__(self, *args, **kwargs):
            pass

        def structured(self, role, prompt, schema, tools):
            assert "HUMAN_LABEL_MUST_NOT_REACH_MODEL" not in str(prompt)
            asyncio.run(Client().chat(messages=[{"role": "user", "content": str(prompt)}]))
            if role == "technique_critic":
                return Critique(approved=True, reason="Offline contract test", no_security_checks_reason="Scripted test")
            return IssueAssessment(critic_supported=True, reason="Offline contract test")

    original = Client.chat
    result = run_job(job, runner_factory=Runner, transport=Client)
    assert result["status"] == "completed"
    assert result["counts"] == {"limit": 30, "roles": 2, "requests": 2}
    assert Client.chat is original
    assert len(list(Path(job["root"]).glob("provider-request-*.json"))) == 2


def test_worker_refuses_incomplete_provider_input_before_sending(job):
    class Client:
        async def chat(self, **kwargs):
            pytest.fail("A truncated draft must not be sent")

    class Runner:
        def __init__(self, *args, **kwargs):
            pass

        def structured(self, *args, **kwargs):
            asyncio.run(Client().chat(messages=[{"role": "user", "content": "Draft omitted"}]))

    result = run_job(job, runner_factory=Runner, transport=Client)
    assert result["status"] == "unresolved"
    assert result["counts"]["requests"] == 0
    assert "lost authored document" in result["error"]


@pytest.mark.parametrize("mode", ["native", "tool"])
def test_actual_sdk_offline_worker_exercises_each_wire_mode(job, mode):
    job.update(offline=True, wire_mode=mode)
    result = run_job(job)
    assert result["status"] == "offline_contract_passed"
    assert result["counts"]["requests"] == 0
    assert result["scripted_model_dispatches"] == 4
    assert result["semantic_judgment"] == "not_performed"


def test_worker_main_reads_explicit_job_and_returns_result(job, monkeypatch):
    path = Path(job["root"]) / "job.json"
    path.write_text(json.dumps(job))
    monkeypatch.setattr("modules.techniques.review_calibration_worker.run_job", lambda data: data)
    assert main([str(path)]) == 0


def test_offline_model_rejects_lost_records_and_unknown_mode():
    from modules.techniques.review_calibration_offline import OfflineReviewModel, configure_offline_roles

    model = OfflineReviewModel(("required-record",), "ref", "native")
    model.update_config(temperature=0)
    assert model.get_config()["temperature"] == 0
    with pytest.raises(RuntimeError, match="lost"):
        model.verify([])
    with pytest.raises(ValueError, match="Unknown"):
        with configure_offline_roles(None, (), "ref", "unknown"):
            pass
    snapshot = ReviewSnapshot("Complete body")
    assert snapshot.body_references


@pytest.mark.parametrize("wire_mode", ["native", "tool"])
def test_single_worker_preflight_uses_one_role_and_no_provider(job, wire_mode):
    result = run_job({**job, "offline": True, "review_mode": "single", "wire_mode": wire_mode})
    assert result["status"] == "offline_contract_passed"
    assert result["counts"]["roles"] == 1
    assert result["counts"]["requests"] == 0
    assert result["scripted_model_dispatches"] > 0
    assert result["semantic_judgment"] == "not_performed"


def test_worker_rejects_unknown_review_mode(job):
    with pytest.raises(ValueError, match="Unknown review_mode"):
        run_job({**job, "review_mode": "unknown"})
