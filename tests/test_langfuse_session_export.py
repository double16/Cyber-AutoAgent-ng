import json
from types import SimpleNamespace

import yaml

from modules import langfuse_session_export as exporter


def _generation(*, observation_id, start_time, input_data, output):
    return SimpleNamespace(
        id=observation_id,
        type="GENERATION",
        start_time=start_time,
        input=input_data,
        output=output,
    )


def _client(session, traces):
    return SimpleNamespace(
        api=SimpleNamespace(
            sessions=SimpleNamespace(get=lambda _session_id: session),
            trace=SimpleNamespace(get=lambda trace_id: traces[trace_id]),
        )
    )


def test_exports_ordered_prompt_review_content_and_excludes_tool_outputs():
    later_generation = _generation(
        observation_id="g2",
        start_time="2026-01-02T00:00:00Z",
        input_data=[{"role": "user", "content": "Use the previous result."}],
        output={"content": [{"text": "Final answer"}]},
    )
    first_generation = _generation(
        observation_id="g1",
        start_time="2026-01-01T00:00:00Z",
        input_data=[
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Assess the login flow."},
            {"role": "assistant", "content": "Ignored previous answer"},
        ],
        output={
            "content": [
                {"reasoningContent": {"reasoningText": {"text": "First inspect authentication."}}},
                {"toolUse": {"name": "http_request", "input": {"url": "https://example.test/login"}}},
                {"text": "I will inspect the login flow."},
            ]
        },
    )
    early_trace = SimpleNamespace(
        id="trace-1",
        name="executor",
        timestamp="2026-01-01T00:00:00Z",
        metadata={"attributes": {"langfuse.agent.type": "task_executor"}},
        observations=[later_generation, first_generation, SimpleNamespace(type="TOOL", output="secret output")],
    )
    later_trace = SimpleNamespace(
        id="trace-2",
        name="critic",
        timestamp="2026-01-02T00:00:00Z",
        metadata={},
        observations=[],
    )
    session = SimpleNamespace(traces=[SimpleNamespace(id="trace-2"), SimpleNamespace(id="trace-1")])

    packet = exporter.LangfuseSessionExporter(
        _client(session, {"trace-1": early_trace, "trace-2": later_trace})
    ).export("session-1")

    assert packet["session_id"] == "session-1"
    assert [trace["trace_id"] for trace in packet["traces"]] == ["trace-1", "trace-2"]
    generation = packet["traces"][0]["generations"][0]
    assert generation["prompts"] == [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Assess the login flow."},
    ]
    assert generation["recorded_reasoning"] == ["First inspect authentication."]
    assert generation["response"] == "I will inspect the login flow."
    assert generation["tool_decisions"] == [
        {"name": "http_request", "arguments": {"url": "https://example.test/login"}}
    ]
    assert "secret output" not in str(packet)
    assert [item["generation_id"] for item in packet["traces"][0]["generations"]] == ["g1", "g2"]


def test_redacts_sensitive_data_in_text_and_nested_tool_arguments():
    packet = exporter.LangfuseSessionExporter(
        _client(
            SimpleNamespace(traces=[SimpleNamespace(id="trace-1")]),
            {
                "trace-1": SimpleNamespace(
                    id="trace-1",
                    name="trace",
                    metadata={},
                    observations=[
                        _generation(
                            observation_id="g1",
                            start_time="1",
                            input_data=[
                                {
                                    "role": "user",
                                    "content": "Authorization: Bearer token-value API_KEY=abc123",
                                }
                            ],
                            output={
                                "content": [
                                    {"text": "Use https://alice:password@example.test."},
                                    {
                                        "toolUse": {
                                            "name": "request",
                                            "input": {"headers": {"Authorization": "Bearer hidden"}},
                                        }
                                    },
                                ]
                            },
                        )
                    ],
                )
            },
        )
    ).export("session-1")

    serialized = str(packet)
    assert "token-value" not in serialized
    assert "abc123" not in serialized
    assert "password" not in serialized
    assert "hidden" not in serialized
    assert exporter.REDACTED in serialized


def test_marks_unavailable_traces_but_exports_available_content():
    trace = SimpleNamespace(id="available", name="trace", metadata={}, observations=[])
    client = _client(
        SimpleNamespace(traces=[SimpleNamespace(id="available"), SimpleNamespace(id="missing")]),
        {"available": trace},
    )
    original_get = client.api.trace.get

    def get(trace_id):
        if trace_id == "missing":
            raise RuntimeError("not found")
        return original_get(trace_id)

    client.api.trace.get = get
    packet = exporter.LangfuseSessionExporter(client).export("session-1")

    assert packet["traces"][0]["trace_id"] == "available"
    assert packet["unavailable_traces"] == [{"trace_id": "missing", "error": "not found"}]


def test_main_uses_configured_host_localhost_fallback_and_file_output(monkeypatch, capsys, tmp_path):
    captured_hosts = []

    class FakeLangfuse:
        def __init__(self, **kwargs):
            captured_hosts.append(kwargs["base_url"])
            self.api = SimpleNamespace()

    monkeypatch.setattr(exporter, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(exporter.LangfuseSessionExporter, "export", lambda _self, _session_id: {"ok": True})
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example")

    destination = tmp_path / "review.json"
    assert exporter.main(["--session-id", "session-1", "--output", str(destination)]) == 0
    assert captured_hosts == ["https://langfuse.example"]
    assert capsys.readouterr().out == ""
    assert json.loads(destination.read_text(encoding="utf-8")) == {"ok": True}

    yaml_destination = tmp_path / "review.json"
    assert exporter.main(
        ["--session-id", "session-override", "--output", str(yaml_destination), "--format", "yaml"]
    ) == 0
    assert yaml_destination.read_text(encoding="utf-8") == "ok: true\n"

    monkeypatch.delenv("LANGFUSE_HOST")
    assert exporter.main(["--session-id", "session-2"]) == 0
    assert captured_hosts[-1] == exporter.DEFAULT_LANGFUSE_HOST
    assert yaml.safe_load(capsys.readouterr().out) == {"ok": True}


def test_output_format_prefers_flag_then_extension_and_falls_back_to_yaml(tmp_path):
    packet = {"message": "hello"}

    assert exporter._resolve_output_format(None, None) == "yaml"
    assert exporter._resolve_output_format("review.unknown", None) == "yaml"
    assert exporter._resolve_output_format("review.json", None) == "json"
    assert exporter._resolve_output_format("review.yml", None) == "yaml"
    assert exporter._resolve_output_format("review.yaml", "json") == "json"

    json_destination = tmp_path / "review.json"
    exporter._write_packet(packet, str(json_destination), exporter._resolve_output_format(str(json_destination), None))
    assert json.loads(json_destination.read_text(encoding="utf-8")) == packet

    yaml_destination = tmp_path / "review.yaml"
    exporter._write_packet(packet, str(yaml_destination), exporter._resolve_output_format(str(yaml_destination), "yaml"))
    assert yaml.safe_load(yaml_destination.read_text(encoding="utf-8")) == packet


def test_main_requires_credentials(monkeypatch, capsys):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    assert exporter.main(["--session-id", "session-1"]) == 2
    assert "LANGFUSE_PUBLIC_KEY" in capsys.readouterr().err


def test_export_fails_when_session_has_no_retrievable_traces():
    empty_client = _client(SimpleNamespace(traces=[]), {})

    try:
        exporter.LangfuseSessionExporter(empty_client).export("empty-session")
    except exporter.ExportError as error:
        assert "has no traces" in str(error)
    else:
        raise AssertionError("Expected an ExportError for an empty session")
