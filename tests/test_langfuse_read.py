import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langfuse.api.commons.types.observation_v2 import ObservationV2

from modules.langfuse_read import read_observations, read_session_traces


def client_for(*pages):
    return SimpleNamespace(api=SimpleNamespace(observations=SimpleNamespace(get_many=Mock(side_effect=pages))))


def test_cursor_pagination_decodes_io_deduplicates_and_keeps_window_fixed():
    row = {"id": "one", "type": "GENERATION", "input": '[{"role":"user","content":"hello"}]', "output": "plain"}
    client = client_for(
        {"data": [row], "meta": {"cursor": "next"}},
        {"data": [row, {"id": "two", "output": '{"ok":true}'}]},
    )
    rows = read_observations(client, session_id="session")
    assert len(rows) == 2
    assert rows[0].input == [{"role": "user", "content": "hello"}]
    assert rows[0].output == "plain"
    assert rows[1].output == {"ok": True}
    first, second = client.api.observations.get_many.call_args_list
    assert second.kwargs["cursor"] == "next"
    assert first.kwargs["from_start_time"] == second.kwargs["from_start_time"]
    assert first.kwargs["from_start_time"] == datetime(1970, 1, 1, tzinfo=UTC)
    assert json.loads(first.kwargs["filter"]) == [
        {"column": "sessionId", "type": "string", "operator": "=", "value": "session"}
    ]
    assert "io" in first.kwargs["fields"]


def test_sdk_models_observation_filter_and_explicit_window():
    row = ObservationV2(
        id="one", trace_id="trace", project_id="project", type="GENERATION",
        start_time=datetime.now(UTC), input=None, output="42",
    )
    client = client_for(SimpleNamespace(data=[row], meta=SimpleNamespace(cursor=None)))
    end = datetime.now(UTC)
    assert read_observations(
        client, observation_id="one", from_start_time=end - timedelta(days=30), to_start_time=end,
    )[0].output == 42
    request = client.api.observations.get_many.call_args.kwargs
    assert json.loads(request["filter"])[0]["column"] == "id"
    assert request["to_start_time"] == end


def test_grouping_prefers_logical_root_and_includes_rootless_traces():
    client = client_for({"data": [
        dict(id="child", trace_id="trace", type="GENERATION", start_time="1", input="{}"),
        dict(id="root", trace_id="trace", type="SPAN", start_time="2", is_root_observation=True,
             parent_observation_id="external", name="root", trace_name="operation", metadata={"role": "worker"}),
        dict(id="orphan", trace_id="rootless", type="SPAN", start_time="3", name="orphan"),
        dict(id="no-trace", type="EVENT"),
    ]})
    traces = read_session_traces(client, "session")
    assert [trace.id for trace in traces] == ["trace", "rootless"]
    assert traces[0].name == "operation"
    assert traces[0].metadata == {"role": "worker"}
    assert traces[0].session_id == "session"
    assert len(traces[0].observations) == 2
    assert traces[1].name == "orphan"


@pytest.mark.parametrize("failure", [RuntimeError("network"), ValueError("authentication")])
def test_pagination_failure_is_not_returned_as_partial_evidence(failure):
    client = client_for({"data": [{"id": "one"}], "meta": {"cursor": "next"}}, failure)
    with pytest.raises(type(failure), match=str(failure)):
        read_session_traces(client, "session")


def test_repeated_cursor_is_rejected():
    client = client_for(*[{"data": [], "meta": {"cursor": "same"}}] * 2)
    with pytest.raises(ValueError, match="repeated"):
        read_observations(client, session_id="session")


@pytest.mark.parametrize("response", [None, {}, {"data": None}, {"data": "invalid"}])
def test_invalid_api_response_is_not_treated_as_empty_session(response):
    with pytest.raises(ValueError, match="invalid observations response"):
        read_session_traces(client_for(response), "session")


def test_empty_results_and_namespace_rows():
    assert read_session_traces(client_for({"data": []}), "empty") == []
    row = SimpleNamespace(id="one", output="null")
    assert read_observations(client_for({"data": [row]}), observation_id="one")[0].output is None


def test_unscoped_and_invalid_windows_are_rejected():
    with pytest.raises(ValueError, match="ID is required"):
        read_observations(client_for())
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="precede"):
        read_observations(client_for(), session_id="s", from_start_time=now, to_start_time=now)
