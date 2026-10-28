import json
from types import SimpleNamespace

import pytest
import yaml
from qdrant_client import QdrantClient

from modules.techniques.documents import TechniqueDocument
from modules.techniques.repository import TechniqueRepository
from modules.techniques.service import TechniqueService
from modules.techniques.storage import TechniqueStore


def critique_result(approved=False, category="authored_content", reason="Check controls",
                    urls=None):
    return json.dumps({
        "approved": approved, "category": category, "reason": reason,
        "issues": [] if approved else [{
            "id": "controls", "category": category, "problem": reason,
            "requested_correction": "Describe the missing control and its interpretation",
            "requirement_ids": ["method"], "source_urls": urls or [],
            "draft_excerpt": "", "absence_of_support": "Retained guidance does not establish this claim",
        }],
    })


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
        if role == "technique_source_assessor":
            return json.dumps({"selected_urls": state.source_urls,
                               "attributions": [{"url": url, "reason": "Primary applicable advisory"}
                                                for url in state.source_urls],
                               "reason": "Primary applicable advisory",
                               "authoritative_sufficient": state.authoritative})
        if role in {"technique_requirement_assessor", "technique_requirement_reviewer"}:
            if role == "technique_requirement_reviewer":
                return prompt.split("Prior requirement assessment (untrusted): ", 1)[1].split("\n", 1)[0]
            assigned = json.loads(prompt.split("Assigned requirement: ", 1)[1].split("\n", 1)[0])
            return json.dumps({"requirement_id": assigned["id"], "source_urls": state.source_urls,
                               "explanation": "Primary method support", "gaps": []})
        if role == "technique_request_aligner":
            request = prompt.split("Original request: ", 1)[1].split("\n", 1)[0]
            return json.dumps({"adjusted_request": request, "reason": "Explicit guidance purpose preserved"})
        if role == "technique_coverage_reviewer":
            return prompt.split("Retained assessment:\n", 1)[1].split("\nReturn only JSON", 1)[0]
        if role == "technique_request_reviewer":
            return json.dumps({"approved": True, "reason": "Request preserved",
                               "references": ["original_request"], "subject_preserved": True,
                               "mechanisms_preserved": True,
                               "deliverables_preserved": True, "additions": [], "omissions": []})
        if role == "technique_plan_reviewer":
            return json.dumps({"approved": True, "reason": "Request preserved"})
        if role == "technique_planner":
            return json.dumps({"components": [{"id": "method", "request": "JWT handling",
                "purpose": "Review token validation",
                "request_excerpt": prompt.split("Original request: ", 1)[1].split("\n", 1)[0], "requirements": [
                    {"id": "method", "description": "JWT handling"}]}]})
        if role == "technique_judge":
            if "generic_request" in prompt:
                return json.dumps({
                    "generic_request": "JWT handling",
                    "applicability": "conditional", "unmet_prerequisites": ["Establish token handling"],
                    "reason": "Applicability decision",
                })
            if "selected_urls" in prompt:
                return json.dumps(
                    {
                        "selected_urls": state.source_urls,
                        "attributions": [{"url": url, "reason": "Primary applicable advisory"}
                                         for url in state.source_urls],
                        "coverage": [{"requirement_id": "method", "source_urls": state.source_urls,
                                      "explanation": "Primary method support", "gaps": []}],
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
            return critique_result(state.approve)
        if role == "technique_critique_assessor":
            return json.dumps({"approved": True, "reason": "Concrete correction supported by retained material"})
        if role == "technique_attribution_repair":
            return json.dumps({"attributions": [{"url": state.source_urls[0],
                                                "reason": "Accurate source scope"}]})
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


def assessment_output(role, prompt, payload):
    """Project a semantic test fixture onto the requested internal output contract."""
    if role == "technique_source_assessor":
        payload = {key: value for key, value in payload.items() if key != "coverage"}
    elif role in {"technique_requirement_assessor", "technique_requirement_reviewer"}:
        assigned = json.loads(prompt.split("Assigned requirement: ", 1)[1].split("\n", 1)[0])
        payload = next(row for row in payload["coverage"] if row["requirement_id"] == assigned["id"])
    return json.dumps(payload)
