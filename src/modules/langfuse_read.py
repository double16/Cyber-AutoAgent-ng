"""Read Langfuse v4 observations and reconstruct application trace views."""

import json
from collections import defaultdict
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any


def _get(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def read_observations(
    client: Any,
    *,
    session_id: str | None = None,
    observation_id: str | None = None,
    from_start_time: datetime | None = None,
    to_start_time: datetime | None = None,
) -> list[Any]:
    """Read every cursor page in a fixed time window (Unix epoch through now by default).

    Fail closed on API or pagination errors: incomplete evidence must not silently
    become a complete export or evaluation input.
    """
    end = to_start_time or datetime.now(UTC)
    start = from_start_time or datetime(1970, 1, 1, tzinfo=UTC)
    if start >= end:
        raise ValueError("Langfuse start time must precede end time")
    request: dict[str, Any] = {
        "fields": "core,basic,time,io,metadata,model,usage,prompt,metrics,trace_context",
        "expand_metadata": "attributes",
        "limit": 1000,
        "from_start_time": start,
        "to_start_time": end,
    }
    filters = []
    for column, value in (("sessionId", session_id), ("id", observation_id)):
        if value is not None:
            filters.append({"type": "string", "column": column, "operator": "=", "value": value})
    if not filters:
        raise ValueError("A session or observation ID is required")
    request["filter"] = json.dumps(filters)
    observations = {}
    cursors = set()
    while True:
        response = client.api.observations.get_many(**request)
        rows = _get(response, "data")
        if not isinstance(rows, list):
            raise ValueError("Langfuse returned an invalid observations response")
        for row in rows:
            if hasattr(row, "model_dump"):
                values = row.model_dump()
            elif isinstance(row, dict):
                values = dict(row)
            else:
                values = vars(row).copy()
            for field in ("input", "output"):
                values[field] = _decode(values.get(field))
            observations[values["id"]] = SimpleNamespace(**values)
        cursor = _get(_get(response, "meta"), "cursor")
        if not cursor:
            return list(observations.values())
        if cursor in cursors:
            raise ValueError("Langfuse returned a repeated pagination cursor")
        cursors.add(cursor)
        request["cursor"] = cursor


def read_session_traces(client: Any, session_id: str, **window: Any) -> list[Any]:
    """Group observations by trace ID, including traces without an exported root."""
    groups = defaultdict(list)
    for observation in read_observations(client, session_id=session_id, **window):
        if _get(observation, "trace_id"):
            groups[observation.trace_id].append(observation)
    traces = []
    for trace_id, observations in groups.items():
        observations.sort(key=lambda row: str(_get(row, "start_time", "")))
        root = next((row for row in observations if _get(row, "is_root_observation")), observations[0])
        traces.append(SimpleNamespace(
            id=trace_id,
            session_id=session_id,
            name=_get(root, "trace_name") or _get(root, "name"),
            timestamp=_get(root, "start_time"),
            metadata=_get(root, "metadata") or {},
            input=_get(root, "input"),
            output=_get(root, "output"),
            observations=observations,
        ))
    return traces
