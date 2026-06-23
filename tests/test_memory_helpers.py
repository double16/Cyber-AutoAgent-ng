import json

from modules.tools import memory as mod


def test_normalize_evidence_and_identifier_defaults(monkeypatch):
    assert mod._normalize_evidence(None) == []
    assert mod._normalize_evidence([" a ", None, {"b": 2, "a": 1}]) == [
        "a",
        '{"a": 1, "b": 2}',
    ]
    assert mod._normalize_evidence(" item ") == ["item"]

    monkeypatch.setattr(mod, "_MEMORY_CONFIG", {"user_id": "u1", "operation_id": "op1"})
    assert mod._user_id() == "u1"
    assert mod._agent_id("agent") == "agent"
    assert mod._agent_id() is None
    assert mod._operation_id() == "op1"
    assert mod._sanitize_toon_value("a,b\nc") == "a;b c"


def test_active_task_message_for_none_active_and_closed_task():
    closed = mod.Task(
        task_uid="t1",
        title="Closed",
        objective="Do it",
        phase=1,
        status="done",
        evidence=["proof"],
    )

    message = mod.active_task_message(active_task=None, closed_task=closed, current_phase=2)

    assert '<active_task phase="2" status="none">' in message
    payload = json.loads(message.split("\n", 1)[1].split("\n</active_task>", 1)[0])
    assert payload["task"] is None
    assert payload["closed"] == {"task_uid": "t1", "status": "done"}


def test_active_task_message_for_active_task_and_confidence():
    active = mod.Task(
        task_uid="t2",
        title="Active",
        objective="Test auth",
        phase=3,
        status="active",
        status_reason="next",
    )

    message = mod.active_task_message(active_task=active, activated=False)

    assert '<active_task phase="3" status="active">' in message
    assert '"activated": false' in message
    assert '"task_uid": "t2"' in message
    assert mod.normalize_confidence("88%") == "88.0%"
    assert mod.normalize_confidence("bad") == "0.0%"
    assert mod.normalize_confidence(150, cap_to=90) == "90.0%"
    assert mod.normalize_confidence(-5) == "0.0%"


def test_has_valid_proof_pack_finds_existing_paths(monkeypatch, tmp_path):
    proof = tmp_path / "proof.txt"
    proof.write_text("ok")

    assert mod._has_valid_proof_pack({"proof_pack": {"artifacts": [str(proof)]}}) is True
    assert mod._has_valid_proof_pack(f"artifact path: {proof}") is True
    assert mod._has_valid_proof_pack({"proof_pack": {"artifacts": [str(tmp_path / "missing")]}}) is False

    monkeypatch.setattr(mod.os.path, "exists", lambda path: (_ for _ in ()).throw(OSError("bad")))
    assert mod._has_valid_proof_pack({"artifact": "anything"}) is False


def test_memory_base_path_respects_isolation_modes(monkeypatch, tmp_path):
    config = {
        "target_name": "target",
        "operation_id": "OP1",
        "output_dir": str(tmp_path),
    }

    monkeypatch.delenv("CYBER_AGENT_OUTPUT_DIR", raising=False)
    monkeypatch.setenv("MEMORY_ISOLATION", "operation")
    assert mod._get_memory_base_path(config).endswith("target/memory/OP1")

    monkeypatch.setenv("MEMORY_ISOLATION", "shared")
    assert mod._get_memory_base_path(config).endswith("target/memory")

    assert mod._get_memory_base_path({"vector_store": {"config": {"path": "/custom"}}}) == "/custom"
