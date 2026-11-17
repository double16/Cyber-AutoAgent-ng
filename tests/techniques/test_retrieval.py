from types import SimpleNamespace

import pytest
from conftest import review_response
from pydantic import ValidationError

from modules.techniques.benchmark import RetrievalCase, evaluate
from modules.techniques.documents import TechniqueDocument
from modules.techniques.retrieval import RetrievalSettings, search_text


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_retrieval_overrides_preserve_defaults(monkeypatch, value):
    for name in ("TECHNIQUE_DOCUMENT_LIMIT", "TECHNIQUE_TRIGGER_LIMIT", "TECHNIQUE_SEARCH_REPRESENTATION"):
        monkeypatch.setenv(name, value)
    assert RetrievalSettings.from_environment() == RetrievalSettings()


def test_settings_and_search_representations(markdown, monkeypatch):
    document = TechniqueDocument(markdown())
    assert search_text(document, "full") == document.markdown
    summary = search_text(document, "summary")
    assert "JWT handling" in summary
    assert "vendor.example" not in summary
    with pytest.raises(ValueError):
        search_text(document, "unknown")
    monkeypatch.setenv("TECHNIQUE_TRIGGER_LIMIT", "35")
    assert RetrievalSettings.from_environment().trigger_limit == 35
    monkeypatch.setenv("TECHNIQUE_TRIGGER_LIMIT", "0")
    with pytest.raises(ValidationError):
        RetrievalSettings.from_environment()
    with pytest.raises(ValidationError):
        RetrievalSettings(representation="unknown")


def test_labeled_retrieval_metrics_and_catalog_miss(store, markdown):
    revision = store.publish(TechniqueDocument(markdown()), "jwt", "test")
    cases = [
        RetrievalCase(request="JWT", phases=["hypothesis_generation"], relevant_lineages=["jwt"]),
        RetrievalCase(request="Filesystem permissions", phases=["execution"], relevant_lineages=[]),
    ]
    rows = evaluate(store, cases, limits=(1, 10))
    assert rows[0]["ranked_lineages"] == ["jwt"]
    assert rows[0]["recall"] == rows[0]["precision"] == rows[0]["reciprocal_rank"] == 1
    assert rows[1]["catalog_miss"] and rows[1]["recall"] is None
    assert rows[1]["precision"] == 0
    assert store.repository.get(revision)
    with pytest.raises(ValueError):
        evaluate(store, [])
    with pytest.raises(ValueError):
        evaluate(store, cases, limits=(0,))


@pytest.mark.parametrize("representation", ["full", "summary"])
def test_ranked_catalog_with_paraphrases_and_decoys(store, markdown, monkeypatch, representation):
    """Exercise Qdrant ranking; fixed vectors test routing, not embedding-model quality."""
    import re

    from modules.techniques.storage import TechniqueStore

    class ConceptEmbeddings:
        def embed_query(self, text):
            words = set(re.findall(r"[a-z]+", text.lower()))
            return [1.0, float(bool(words & {"shell", "subprocess", "command"})),
                    float(bool(words & {"network", "tcp", "socket"})),
                    float(bool(words & {"filesystem", "symlink", "path"}))]

    monkeypatch.setenv("TECHNIQUE_SEARCH_REPRESENTATION", representation)
    ranked_store = TechniqueStore(
        store.qdrant, ConceptEmbeddings(),
        {"embedding_dimensions": 4, "embedding_model": "routing-fixture"}, store.repository,
    )
    for index in range(300):
        ranked_store.publish(TechniqueDocument(markdown(title=f"Generic decoy {index}")), f"d:{index}", "test")
    for lineage, title in [
        ("http", "HTTP command shell injection"),
        ("network", "TCP network service inspection"),
        ("source", "Filesystem symlink path traversal"),
    ]:
        ranked_store.publish(TechniqueDocument(markdown(title=title)), lineage, "test")
    cases = [
        RetrievalCase(request="subprocess controls", phases=["hypothesis_generation"],
                      relevant_lineages=["http"]),
        RetrievalCase(request="socket inspection", phases=["hypothesis_generation"],
                      relevant_lineages=["network"]),
        RetrievalCase(request="symlink boundaries", phases=["hypothesis_generation"],
                      relevant_lineages=["source"]),
    ]
    rows = evaluate(ranked_store, cases)
    assert all(row["recall"] == row["reciprocal_rank"] == 1 for row in rows)
    assert all(row["elapsed_seconds"] >= 0 for row in rows)
    triggers = ranked_store.search_triggers("socket inspection", ["execution"])
    assert triggers == []


def test_configured_limits_and_separate_representation_indexes(store, markdown, monkeypatch):
    from modules.techniques.storage import TechniqueStore

    store.publish(TechniqueDocument(markdown()), "one", "test")
    store.publish(TechniqueDocument(markdown(title="Another")), "two", "test")
    monkeypatch.setenv("TECHNIQUE_DOCUMENT_LIMIT", "1")
    monkeypatch.setenv("TECHNIQUE_TRIGGER_LIMIT", "1")
    monkeypatch.setenv("TECHNIQUE_SEARCH_REPRESENTATION", "summary")
    alternate = TechniqueStore(
        store.qdrant, store.embeddings, {"embedding_dimensions": 3, "embedding_model": "test"},
        store.repository,
    )
    assert alternate.index != store.index
    assert len(alternate.search("JWT", active=True)) == 1
    assert len(alternate.search_triggers("JWT", ["hypothesis_generation"])) == 1
    assert len(alternate.search("JWT", active=True, limit=2)) == 2


@pytest.mark.parametrize("confirmation", ["accept", "reject", "invalid"])
def test_selection_reviews_complete_document_and_can_abstain(scripted_service, confirmation):

    state = scripted_service
    revision = state.publish()
    prompts = []
    events = []
    state.service.events.callback = events.append

    def judge(role, prompt, schema):
        prompts.append(prompt)
        selected = {"accept": revision, "reject": None, "invalid": "unknown"}[confirmation]
        result = review_response(prompt, selected is not None)
        if confirmation == "invalid":
            result.judgments[0].passages[0].passage_id = -1
        return result

    state.service._json = judge
    if confirmation == "invalid":
        with pytest.raises(ValueError, match="quote the submitted revision"):
            state.service._select("JWT")
    else:
        assert state.service._select("JWT") == (revision if confirmation == "accept" else None)
        assert events[-1]["type"] == "technique_selection"
        assert events[-1]["rejected_ids"] == ([] if confirmation == "accept" else [revision])
    assert len(prompts) == (2 if confirmation == "invalid" else 1)
    assert "Check assumptions and controls" in prompts[0]
    from modules.techniques.review_integrity import ReviewInputIntegrityHook

    ReviewInputIntegrityHook(prompts[0].required_review_records).before_model(SimpleNamespace(
        agent=SimpleNamespace(messages=[{"role": "user", "content": [{"text": prompts[0]}]}]),
    ))
