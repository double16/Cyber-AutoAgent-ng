import json
from types import SimpleNamespace

import pytest
import yaml
from qdrant_client import QdrantClient

from modules.techniques.documents import TechniqueDocument
from modules.techniques.repository import TechniqueRepository
from modules.techniques.service import TechniqueService
from modules.techniques.storage import TechniqueStore


@pytest.fixture
def markdown():
    def make(title="JWT handling", active=True, signals=None, triggers=True):
        metadata = {
            "title": title,
            "purpose": "Review token validation",
            "applicability": ["JWT"],
            "phases": ["hypothesis_generation"],
            "active": active,
            "sources": [{"url": "https://vendor.example/advisory", "title": "Advisory"}],
            "triggers": [
                {
                    "phases": ["hypothesis_generation"],
                    "when": "A JWT is observed",
                    "request": "JWT handling",
                    "signals_any": signals or ["http.jwt"],
                }
            ]
            if triggers
            else [],
        }
        return "---\n" + yaml.safe_dump(metadata) + "---\n\n# Guidance\nCheck assumptions and controls.\n"

    return make


class Embeddings:
    def embed_query(self, text):
        return [1.0, float("JWT" in text), 0.1]


@pytest.fixture
def store(tmp_path):
    client = QdrantClient(":memory:")
    repository = TechniqueRepository(str(tmp_path / "application.db"))
    result = TechniqueStore(client, Embeddings(), {"embedding_dimensions": 3, "embedding_model": "test"}, repository)
    yield result
    client.close()


@pytest.fixture
def scripted_service(store, markdown):
    state = SimpleNamespace(
        calls=[],
        selection=None,
        source_urls=["https://vendor.example/advisory"],
        approve=True,
        score=1,
        authoritative=True,
        bad_document=False,
    )

    def runner(role, prompt, tools):
        state.calls.append((role, prompt, tools))
        if role == "technique_judge":
            if "selected_urls" in prompt:
                return json.dumps(
                    {
                        "selected_urls": state.source_urls,
                        "reason": "Primary applicable advisory",
                        "authoritative_sufficient": state.authoritative,
                    }
                )
            return json.dumps({"selected_id": state.selection, "reason": "Applicability decision"})
        if role == "technique_author":
            document = TechniqueDocument(markdown())
            return (
                "invalid"
                if state.bad_document
                else json.dumps({"metadata": document.metadata.model_dump(), "body": document.body})
            )
        if role == "technique_critic":
            return json.dumps({"approved": state.approve, "reason": "Check controls"})
        if role == "technique_evaluator":
            return json.dumps({"score": state.score, "reason": "Guidance helped discriminate behavior"})
        if role == "technique_trigger_matcher":
            return json.dumps({"trigger_ids": [], "reason": "No semantic match"})
        raise AssertionError(role)

    research_calls = []

    def research(request):
        research_calls.append(request)
        return [
            {
                "url": "https://vendor.example/advisory",
                "title": "Advisory",
                "text": "Verified primary guidance",
                "retrieved_at": "2026-10-25",
                "truncated": False,
            }
        ]

    state.service = TechniqueService(store, runner, research)
    state.research_calls = research_calls
    state.publish = lambda active=True: store.publish(
        TechniqueDocument(markdown(active=active)), "module:web:jwt.md", "module:web"
    )
    return state
