"""Regression coverage for task-local dispatch batching and relevance ranking."""

import json
from types import SimpleNamespace

import pytest

from modules.techniques.matching import RankedSelection, flush_matching, queue_observation
from modules.techniques.triggers import RecommendationContext, TechniqueObservationHook


@pytest.fixture
def batch(scripted_service, tmp_path, monkeypatch):
    state = scripted_service
    state.publish()
    context = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "batch.json")
    context.defer_matching = True
    context.lookup.subjects = [{"id": "subject", "kind": "endpoint"}]
    calls = []
    results = {"matches": [{"trigger_id": "t1", "subject_id": "s1", "observation_reference": "o1"}]}

    def model(role, prompt, schema):
        calls.append((role, prompt))
        return schema.model_validate(results)

    monkeypatch.setattr(state.service, "_json", model)
    return SimpleNamespace(context=context, state=state, calls=calls, results=results)


@pytest.mark.parametrize("objective", [
    "Assess HTTP authorization", "Assess network listener access", "Assess source file permissions",
])
def test_batch_dispatch_once_and_continuation_cache(batch, objective):
    c = batch.context
    c.lookup.purpose = objective
    c.observe("first observation", "artifact:first")
    c.observe("second observation", "tool_output:second")
    assert not batch.calls
    c.flush()
    assert len(batch.calls) == 1
    assert objective in batch.calls[0][1]
    assert "first observation" in batch.calls[0][1] and "second observation" in batch.calls[0][1]
    c.flush()
    assert len(batch.calls) == 1
    restored = RecommendationContext(c.service, c.contexts, [], c.path)
    restored.defer_matching = True
    restored.lookup = c.lookup.model_copy(deep=True)
    restored.flush()
    assert len(batch.calls) == 1
    restored.lookup.purpose += " with different scope"
    restored.flush()
    assert len(batch.calls) == 2


def test_duplicates_keep_aliases_without_extra_match_and_new_content_recomputes(batch):
    c = batch.context
    c.observe("same content", "artifact:first")
    c.observe("same content", "tool_output:duplicate")
    c.flush()
    assert len(c.state["batch_observations"]) == 1
    assert c.recommended()[0]["observation_references"] == ["artifact:first", "tool_output:duplicate"]
    c.observe("same content", "artifact:third")
    c.flush()
    assert len(batch.calls) == 1
    assert "artifact:third" in c.recommended()[0]["observation_references"]
    c.observe("changed content", "artifact:first")
    c.flush()
    assert len(batch.calls) == 2


@pytest.mark.parametrize("matches", [
    [], [{"trigger_id": "unknown", "subject_id": "s1", "observation_reference": "o1"}],
    [{"trigger_id": "t1", "subject_id": "unknown", "observation_reference": "o1"}],
    [{"trigger_id": "t1", "subject_id": "s1", "observation_reference": "unknown"}],
])
def test_empty_invalid_or_unavailable_never_delivers_a_lead(batch, matches):
    batch.results["matches"] = matches
    c = batch.context
    c.observe("observation", "artifact:source")
    c.flush()
    assert not c.recommended()
    count = len(batch.calls)
    c.flush()
    assert len(batch.calls) == count


def test_rank_is_model_order_not_embedding_score(batch, monkeypatch):
    c = batch.context
    first = c.triggers[0]
    second = {**first, "trigger_id": "other", "revision_id": "second", "request": "Relevant method"}
    c.triggers.append(second)
    monkeypatch.setattr(c.service.store, "search_triggers", lambda *args: [
        {**first, "score": 0.99}, {**second, "score": 0.1},
    ])
    batch.results["matches"] = [
        {"trigger_id": "t2", "subject_id": "s1", "observation_reference": "o1"},
        {"trigger_id": "t1", "subject_id": "s1", "observation_reference": "o1"},
    ]
    c.observe("observation", "artifact:source")
    c.flush()
    assert [row["request"] for row in c.recommended()] == ["Relevant method", first["request"]]


def test_observation_bounds_and_guidance_exclusion(batch):
    c = batch.context
    c.observe("guidance", "artifact:guide", origin="guidance")
    c.observe("analysis", "artifact:analysis", origin="analysis")
    c.observe("input", "tool_input:1", semantic=False)
    assert not c.state.get("batch_observations")
    for index in range(9):
        c.observe(str(index), f"artifact:{index}", scope="shared")
    assert len(c.state["batch_observations"]) == 6
    assert c.state["batch_observations"][0]["excerpt"] == "8"
    c.observe("assigned evidence", "artifact:assigned", scope="subject")
    c.observe("assigned evidence", "artifact:alias", scope="subject")
    assert c.state["batch_observations"][0]["scope"] == "subject"
    assert c.state["batch_observations"][0]["sources"] == ["artifact:assigned", "artifact:alias"]


def test_no_subjects_candidates_and_hook_failures_preserve_execution(batch, monkeypatch):
    c = batch.context
    hook = TechniqueObservationHook(c)
    agent = SimpleNamespace(messages=[])
    hook.before_model(SimpleNamespace(agent=agent))
    assert not batch.calls
    c.observe("observation", "artifact:first")
    monkeypatch.setattr(c.service.store, "search_triggers", lambda *args: [])
    c.flush()
    assert not batch.calls and not c.recommended()
    c.lookup.subjects = []
    c.flush()
    monkeypatch.setattr(c, "flush", lambda: (_ for _ in ()).throw(ValueError("unavailable")))
    hook.before_model(SimpleNamespace(agent=agent))
    assert not agent.messages


def test_hook_flush_before_executor_appends_recommendations(batch):
    c = batch.context
    c.observe("observation", "artifact:first")
    agent = SimpleNamespace(messages=[])
    hook = TechniqueObservationHook(c)
    hook.before_model(SimpleNamespace(agent=agent))
    assert len(agent.messages) == 1
    assert "Technique lookup recommendations" in agent.messages[0]["content"][0]["text"]
    hook.before_model(SimpleNamespace(agent=agent))
    assert len(agent.messages) == 1
    assert len(batch.calls) == 1


def test_queue_promotes_shared_alias_to_subject(batch):
    c = batch.context
    queue_observation(c, "text", "shared", "source", "shared")
    queue_observation(c, "text", "assigned", "source", "subject")
    assert c.state["batch_observations"][0]["scope"] == "subject"


def test_ranked_schema_rejects_unknown_fields():
    with pytest.raises(ValueError):
        RankedSelection.model_validate({"matches": [], "explanation": "extra"})


def test_events_report_counts_not_source_text(batch):
    events = []
    batch.context.service.events.callback = events.append
    batch.context.observe("private observation", "artifact:first")
    flush_matching(batch.context)
    flush_matching(batch.context)
    assert any(row.get("status") == "batch_cache_hit" for row in events)
    assert "private observation" not in json.dumps(events)


def test_same_recorded_observations_reduce_calls_without_losing_lead(batch, tmp_path):
    c = batch.context
    c.defer_matching = False
    for index in range(3):
        c.observe(f"source observation {index}", f"artifact:{index}")
    baseline_calls = len(batch.calls)
    baseline_leads = {row["trigger_id"] for row in c.recommended()}
    assert baseline_calls == 3
    other = RecommendationContext(c.service, c.contexts, [], tmp_path / "batched.json")
    other.defer_matching = True
    other.lookup.subjects = c.lookup.subjects
    for index in range(3):
        other.observe(f"source observation {index}", f"artifact:{index}")
    other.flush()
    assert len(batch.calls) - baseline_calls == 1
    assert {row["trigger_id"] for row in other.recommended()} == baseline_leads


def test_unchanged_batch_skips_embedding_retrieval_and_catalog_change_recomputes(batch, monkeypatch):
    c = batch.context
    search = c.service.store.search_triggers
    queries = []

    def counted(query, contexts):
        queries.append(query)
        return search(query, contexts)

    monkeypatch.setattr(c.service.store, "search_triggers", counted)
    c.lookup.purpose = "Assigned methodology"
    c.observe("observation", "artifact:source")
    c.flush()
    c.flush()
    assert len(queries) == 1 and "Assigned methodology" in queries[0]
    c.triggers[0]["when"] += " with changed conditions"
    c.flush()
    assert len(queries) == 2


def test_table_drops_whole_optional_rows():
    from modules.techniques.matching import _table

    text = _table("rows", [{"id": "first", "value": "short"}, {"id": "last", "value": "x" * 500}], 100)
    assert "first" in text and "last" not in text and "rows[1]" in text


def test_module_signal_adapters_supply_candidates_but_do_not_override_semantic_relevance(batch, monkeypatch):
    c = batch.context
    c.adapters = [SimpleNamespace(
        detect=lambda text: ["method.signal"], sanitize=lambda text: text.replace("secret", "redacted"),
    )]
    c.triggers[0]["signals_any"] = ["method.signal"]
    monkeypatch.setattr(c.service.store, "search_triggers", lambda *args: [])
    batch.results["matches"] = []
    c.observe("secret observation", "artifact:source")
    c.flush()
    assert len(batch.calls) == 1
    assert not c.recommended()


def test_provider_failure_is_cached_without_retrying_unchanged_packet(batch, monkeypatch):
    calls = []

    def unavailable(*args):
        calls.append(args)
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(batch.context.service, "_json", unavailable)
    batch.context.observe("observation", "artifact:source")
    batch.context.flush()
    batch.context.flush()
    assert len(calls) == 2
    assert not batch.context.recommended()


def test_shared_observations_do_not_displace_assigned_evidence(batch):
    c = batch.context
    for index in range(6):
        c.observe(str(index), f"artifact:{index}", scope="subject")
    c.observe("shared", "artifact:shared", scope="shared")
    assert all(row["scope"] == "subject" for row in c.state["batch_observations"])
    assert len(c.state["batch_observations"]) == 6


def test_promoted_shared_observation_moves_ahead_of_other_shared_rows(batch):
    c = batch.context
    c.observe("older", "artifact:older", origin="unknown", scope="shared")
    c.observe("newer", "artifact:newer", scope="shared")
    c.observe("older", "artifact:assigned", origin="source", scope="subject")
    assert c.state["batch_observations"][0]["excerpt"] == "older"
    assert c.state["batch_observations"][0]["origin"] == "source"
