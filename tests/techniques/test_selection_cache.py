from types import SimpleNamespace

import pytest

from modules.techniques.context import MethodologyContext
from modules.techniques.selection_cache import cache_key, cached_review, save_review
from modules.techniques.selection_review import SelectionReview


@pytest.fixture
def cached(scripted_service, tmp_path):
    p = scripted_service
    p.selection = p.publish()
    p.service.exports = SimpleNamespace(directory=tmp_path / "exports")
    return p


def test_task_cache_reuses_review_and_isolates_request_phase_and_task(cached):
    p = cached
    context = MethodologyContext(phase_contexts=["hypothesis_generation"])
    with p.service.events.scope(operation_id="operation", task_uid="task"):
        p.service.select_existing("JWT handling", context)
        count = sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls)
        p.service.select_existing("JWT handling", context)
        assert sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls) == count
        p.service._selection_reviews.clear()
        p.service.select_existing("JWT handling", context)
        assert sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls) == count
        p.service._select("JWT signature validation", context)
        assert sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls) > count
        before = cache_key(p.service, "same")
        p.service.module_technique_policy = [{"restriction": "new"}]
        assert cache_key(p.service, "same") != before
    with p.service.events.scope(operation_id="operation", task_uid="different"):
        assert cache_key(p.service, "same") != before


def test_cache_without_task_is_disabled_and_corrupt_cache_is_a_miss(cached):
    service = cached.service
    assert cache_key(service, "fingerprint") is None
    assert cached_review(service, None) is None
    review = SelectionReview(reason="missing method", judgments=[{
        "requirement_id": "method", "verdict": "unmet", "explanation": "missing", "missing_capability": "method",
    }])
    save_review(service, None, review)
    assert not service._selection_reviews
    assert cached_review(service, "missing") is None
    path = service.exports.directory / "selection_cache" / "bad.json"
    path.parent.mkdir(parents=True)
    path.write_text("invalid")
    assert cached_review(service, "bad") is None
    service.exports = None
    assert cached_review(service, "missing") is None
    save_review(service, "memory", review)
    assert cached_review(service, "memory") == review


def test_cache_persistence_failure_is_optional(cached):
    service = cached.service
    service.exports.directory.parent.mkdir(parents=True, exist_ok=True)
    service.exports.directory.write_text("not a directory")
    review = SelectionReview(reason="missing", judgments=[{
        "requirement_id": "method", "verdict": "unmet", "explanation": "missing", "missing_capability": "method",
    }])
    save_review(service, "key", review)
    assert cached_review(service, "key") == review


def test_cached_selection_still_checks_lifecycle(cached, monkeypatch):
    p = cached
    context = MethodologyContext(phase_contexts=["hypothesis_generation"])
    with p.service.events.scope(operation_id="operation", task_uid="task"):
        assert p.service.select_existing("JWT handling", context)
        monkeypatch.setattr(p.service.repository, "get", lambda revision: {"active": False})
        assert p.service.select_existing("JWT handling", context) is None


def test_document_and_phase_changes_invalidate_selection(cached, monkeypatch):
    from modules.techniques.documents import TechniqueDocument

    p = cached
    context = MethodologyContext(phase_contexts=["hypothesis_generation"])
    with p.service.events.scope(operation_id="operation", task_uid="task"):
        assert p.service._select("JWT handling", context)
        first = sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls)
        original = p.service.store.document
        monkeypatch.setattr(p.service.store, "document", lambda revision: TechniqueDocument(
            original(revision).markdown + "\nAdditional controls.\n",
        ))
        assert p.service._select("JWT handling", context)
        assert sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls) == first + 1
        assert p.service._select("JWT handling", MethodologyContext())
        assert sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls) == first + 2


def test_explicit_miss_authors_then_existing_guidance_reuses_without_another_review(scripted_service):
    p = scripted_service
    with p.service.events.scope(operation_id="operation", task_uid="task"):
        revision, _markdown = p.service.get("JWT handling")
        assert p.service.repository.get(revision)["active"]
        assert len(p.research_calls) == 1
        assert sum(role == "technique_author" for role, _, _ in p.calls) == 1
        assert sum(role == "technique_critic" for role, _, _ in p.calls) == 1
        p.selection = revision
        assert p.service.get("JWT handling")[0] == revision
        reviewed = sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls)
        assert p.service.get("JWT handling")[0] == revision
        assert sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls) == reviewed
        assert len(p.research_calls) == 1


def test_output_schema_changes_invalidate_selection_cache(cached, monkeypatch):
    from pydantic import create_model

    import modules.techniques.service as service_module

    p = cached
    factory = service_module.selection_output_model
    with p.service.events.scope(operation_id="operation", task_uid="task"):
        assert p.service._select("JWT handling", MethodologyContext())
        count = sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls)
        monkeypatch.setattr(
            service_module, "selection_output_model",
            lambda requirements: create_model("UpdatedSelectionReview", __base__=factory(requirements)),
        )
        assert p.service._select("JWT handling", MethodologyContext())
        assert sum("COMPLETE technique" in prompt for _, prompt, _ in p.calls) == count + 1
