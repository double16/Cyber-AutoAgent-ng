"""Resume durable fixture state without reseeding or overwriting prior failures."""

import importlib.util
import json
from pathlib import Path

import pytest

BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("resume_fixture", BASE / "resume.py")
resume = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resume)
integration = resume.integration


def test_continuation_preserves_prior_ledger_and_handles_failure(tmp_path, monkeypatch):
    integration.save(tmp_path / "requests.json", {"requests": 10, "limit": 10})
    integration.save(tmp_path / "results.json", [{"error": "Preserved prior failure"}])
    with integration.environment(tmp_path):
        integration.seed(tmp_path)
        state = integration.workflow.WorkflowStateStore(integration.OPERATION)
        state.patch_task("selector", status="active")
        controller = integration.controller_for(tmp_path)
        before = integration.snapshot(controller, "selector")["binding"]
    calls = []

    def perform(root, uid, audit_root):
        assert root == tmp_path and audit_root == tmp_path / "continuation"
        calls.append(uid)
        if uid == "mechanisms":
            raise OSError("Preserved continuation failure")
        return {"task_uid": uid, "final": {"task": {"status": "partial_failure"}}}

    monkeypatch.setattr(integration, "perform", perform)
    result = resume.continue_operation(tmp_path)
    assert calls == ["selector", "mechanisms", "refutation"]
    assert "Preserved continuation failure" in result[1]["error"]
    assert json.loads((tmp_path / "results.json").read_text()) == [{"error": "Preserved prior failure"}]
    audit = tmp_path / "continuation"
    assert json.loads((audit / "requests.json").read_text()) == {"requests": 0, "limit": 86}
    assert json.loads((audit / "before.json").read_text())["selector"]["binding"] == before
    assert (audit / "state-before.db").exists()
    assert (audit / "prior-results.json").exists()
    with pytest.raises(FileExistsError):
        resume.continue_operation(tmp_path)
