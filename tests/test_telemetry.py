from types import SimpleNamespace

from modules.utils import telemetry as mod


class FakeTracerProvider:
    def __init__(self):
        self.calls = []

    def force_flush(self, timeout_millis):
        self.calls.append(timeout_millis)


def test_flush_traces_uses_telemetry_provider(monkeypatch):
    provider = FakeTracerProvider()
    telemetry=SimpleNamespace(tracer_provider=provider)
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", sleeps.append)

    mod.flush_traces(telemetry)

    assert provider.calls == [10000]
    assert sleeps == [2]


def test_flush_traces_uses_global_provider_when_no_telemetry(monkeypatch):
    provider = FakeTracerProvider()
    monkeypatch.setattr(mod.trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)

    mod.flush_traces(None)

    assert provider.calls == [10000]


def test_flush_traces_ignores_provider_without_force_flush(monkeypatch):
    monkeypatch.setattr(mod.trace, "get_tracer_provider", lambda: object())
    sleep = []
    monkeypatch.setattr(mod.time, "sleep", sleep.append)

    mod.flush_traces(None)

    assert sleep == []


def test_flush_traces_logs_force_flush_errors(monkeypatch):
    class BrokenProvider:
        def force_flush(self, timeout_millis):
            raise RuntimeError("boom")

    warnings = []
    monkeypatch.setattr(mod.trace, "get_tracer_provider", lambda: BrokenProvider())
    monkeypatch.setattr(mod.logger, "warning", lambda *args: warnings.append(args))

    mod.flush_traces(None)

    assert warnings
    assert warnings[0][0] == "Error flushing traces: %s"
    assert isinstance(warnings[0][1], RuntimeError)
