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

    mod.flush_traces(telemetry)

    assert provider.calls == [10000]


def test_flush_traces_uses_global_provider_when_no_telemetry(monkeypatch):
    provider = FakeTracerProvider()
    monkeypatch.setattr(mod.trace, "get_tracer_provider", lambda: provider)

    mod.flush_traces(None)

    assert provider.calls == [10000]


def test_flush_traces_ignores_provider_without_force_flush(monkeypatch):
    monkeypatch.setattr(mod.trace, "get_tracer_provider", lambda: object())

    mod.flush_traces(None)


def test_flush_traces_warns_when_provider_reports_failure(monkeypatch):
    provider = FakeTracerProvider()
    provider.force_flush = lambda timeout_millis: False
    warnings = []
    monkeypatch.setattr(mod.trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(mod.logger, "warning", lambda *args: warnings.append(args))

    mod.flush_traces(None)

    assert warnings == [("OpenTelemetry trace flush timed out or failed",)]


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
