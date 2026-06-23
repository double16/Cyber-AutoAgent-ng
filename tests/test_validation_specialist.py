from modules.operation_plugins.web.tools import validation_specialist as mod


class FakeValidator:
    def __init__(self, response):
        self.response = response
        self.tasks = []
        self.cleaned = False

    def __call__(self, task):
        self.tasks.append(task)
        return self.response

    def cleanup(self):
        self.cleaned = True


def test_validation_specialist_parses_json_and_normalizes_bad_severity(monkeypatch):
    validator = FakeValidator(
        'prefix {"validation_status":"verified","confidence":95,"severity_max":"HIGH","failed_gates":[]} suffix')
    calls = []

    def agent_factory(**kwargs):
        calls.append(kwargs)
        return validator

    monkeypatch.setattr(mod.validation_specialist, "agent_factory", agent_factory, raising=False)
    monkeypatch.setenv("CYBER_OPERATION_ID", "OP1")

    result = mod.validation_specialist("SQL injection", ["/tmp/a"], claimed_severity="INVALID")

    assert result["validation_status"] == "verified"
    assert result["confidence"] == 95
    assert calls[0]["name"] == "Cyber-validation_specialist OP1"
    assert calls[0]["agent_type"] == "validation_specialist"
    assert "CLAIMED SEVERITY: HIGH" in validator.tasks[0]
    assert validator.cleaned is True


def test_validation_specialist_returns_hypothesis_when_agent_output_has_no_json(monkeypatch):
    validator = FakeValidator("not json")
    monkeypatch.setattr(
        mod.validation_specialist,
        "agent_factory",
        lambda **kwargs: validator,
        raising=False,
    )

    result = mod.validation_specialist("finding", ["artifact"], claimed_severity="LOW")

    assert result == {
        "validation_status": "hypothesis",
        "confidence": 40,
        "severity_max": "MEDIUM",
        "failed_gates": [1, 2, 3, 4, 5, 6, 7],
        "evidence_summary": "Could not parse validation results",
        "recommendation": "Manually review artifacts",
    }
    assert validator.cleaned is True


def test_validation_specialist_returns_error_on_exception(monkeypatch):
    def agent_factory(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod.validation_specialist, "agent_factory", agent_factory, raising=False)

    result = mod.validation_specialist("finding", ["artifact"])

    assert result["validation_status"] == "error"
    assert result["severity_max"] == "INFO"
    assert "boom" in result["evidence_summary"]


def test_validation_specialist_ignores_cleanup_errors(monkeypatch):
    class BrokenCleanup(FakeValidator):
        def cleanup(self):
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(
        mod.validation_specialist,
        "agent_factory",
        lambda **kwargs: BrokenCleanup('{"validation_status":"verified"}'),
        raising=False,
    )

    assert mod.validation_specialist("finding", ["artifact"]) == {"validation_status": "verified"}
