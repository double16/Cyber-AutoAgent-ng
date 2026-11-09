"""Provider-call caps include retries and are restored after isolated instrumentation."""

import asyncio
import json
import stat

import pytest

from modules.techniques.calibration_budget import CalibrationBudget, CalibrationBudgetExhausted


@pytest.mark.parametrize("limit", [0, 31, True, "30"])
def test_invalid_allowance_is_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        CalibrationBudget(tmp_path / "budget.json", limit)


def test_persistent_budget_and_unknown_counter(tmp_path):
    path = tmp_path / "budget.json"
    budget = CalibrationBudget(path, 2)
    assert budget.reserve("roles") == 1
    assert CalibrationBudget(path, 2).counts()["roles"] == 1
    with pytest.raises(ValueError, match="different limit"):
        CalibrationBudget(path, 3)
    with pytest.raises(ValueError, match="Unknown"):
        budget.reserve("tokens")
    budget.reserve("requests")
    budget.reserve("requests")
    for kind in ("requests", "roles"):
        with pytest.raises(CalibrationBudgetExhausted):
            budget.reserve(kind)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_transport_retry_counts_before_dispatch_and_restores_method(tmp_path):
    class Client:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Connection failed")
            return kwargs["value"]

    original = Client.chat
    budget = CalibrationBudget(tmp_path / "budget.json", 2)
    client = Client()
    with budget.instrument(Client, "chat", tmp_path):
        with pytest.raises(RuntimeError, match="Connection failed"):
            asyncio.run(client.chat(value=1))
        assert asyncio.run(client.chat(value=2)) == 2
        with pytest.raises(CalibrationBudgetExhausted):
            asyncio.run(client.chat(value=3))
    assert Client.chat is original
    assert client.calls == 2
    assert budget.counts()["requests"] == 2
    assert json.loads((tmp_path / "provider-request-02.json").read_text())["kwargs"] == {"value": 2}


def test_missing_document_stops_before_reserving_or_dispatching(tmp_path):
    class Client:
        async def chat(self, **kwargs):
            pytest.fail("No request may be sent")

    def reject(request):
        raise RuntimeError("Document incomplete")

    budget = CalibrationBudget(tmp_path / "budget.json")
    original = Client.chat
    with budget.instrument(Client, "chat", tmp_path, validate_request=reject):
        with pytest.raises(RuntimeError, match="Document incomplete"):
            asyncio.run(Client().chat())
    assert budget.counts()["requests"] == 0
    assert Client.chat is original
