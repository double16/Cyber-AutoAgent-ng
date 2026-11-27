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
        "no_security_checks_reason": "No separately enumerated checks in the scripted draft",
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
        grounding="knowledge_allowed",
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
                return json.dumps(
                    support_record(
                        json.loads(prompt.split("Prior requirement assessment (untrusted): ", 1)[1].split("\n", 1)[0])
                    )
                )
            assigned = json.loads(prompt.split("Assigned requirement: ", 1)[1].split("\n", 1)[0])
            return json.dumps(
                {
                    "requirement_id": assigned["id"],
                    "source_urls": state.source_urls,
                    "explanation": "Primary method support",
                    "gaps": [],
                    "authority_sufficient": state.authoritative,
                    "claim_support": [
                        {
                            "claim": "Primary technical method support",
                            "source_urls": state.source_urls,
                            "authority_basis": "Primary applicable advisory",
                        }
                    ],
                }
            )
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
            return json.dumps(plan_payload({"components": [{"id": "method", "request": "JWT handling",
                "purpose": "Review token validation",
                "request_excerpt": prompt.split("Original request: ", 1)[1].split("\n", 1)[0], "requirements": [
                    {"id": "method", "description": "JWT handling", "grounding": state.grounding}]}]}))
        if role == "technique_judge":
            if "generic_request" in prompt:
                return json.dumps({
                    "generic_request": "JWT handling", "grounding": state.grounding,
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
            result = json.loads(critique_result(state.approve))
            if '"id": "requirement-1"' in prompt:
                for issue in result["issues"]:
                    issue["requirement_ids"] = ["requirement-1"]
            return json.dumps(result)
        if role == "technique_critique_assessor":
            return json.dumps({"approved": True, "reason": "Concrete correction supported by retained material"})
        if role == "technique_attribution_repair":
            return json.dumps({"attributions": [{"url": state.source_urls[0],
                                                "reason": "Accurate source scope"}]})
        if role == "technique_feedback_reviewer":
            assessment = next(json.loads(line) for line in prompt.splitlines()
                              if line.startswith('{"neutral_basis":'))
            return json.dumps({"assessment": assessment, "material_errors": []})
        if role == "technique_evaluator":
            import re

            guidance_ref = re.search(r'guidance:[a-f0-9]+:[0-9]+', prompt)
            result_ref = re.search(r'result:[a-f0-9]+:[0-9]+', prompt)
            mappings = ([{"guidance_references": [guidance_ref[0]],
                          "result_references": [result_ref[0]], "explanation": "Controlled application"}]
                        if guidance_ref and result_ref else [])
            return json.dumps({"neutral_basis": "contribution_unestablished" if state.score == 0 else None,
                               "evidence_mappings": mappings,
                               "methodological_purpose": "Discriminate behavior",
                               "observed_use": "Controlled comparison",
                               "helpful_contribution": state.score == 1,
                               "contribution": "Clarified controls" if state.score == 1 else "",
                               "misleading_or_harmful": state.score == -1,
                               "harmful_effect": "Misleading control" if state.score == -1 else "",
                               "reason": "Guidance helped discriminate behavior"})
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
        if len(set(payload["selected_urls"])) > 1 and "Current document URL: " in prompt:
            current = json.loads(prompt.split("Current document URL: ", 1)[1].split("\n", 1)[0])["url"]
            payload["selected_urls"] = [url for url in payload["selected_urls"] if url == current]
            payload["attributions"] = [row for row in payload["attributions"] if row["url"] == current]
    elif role in {"technique_requirement_assessor", "technique_requirement_reviewer"}:
        assigned = json.loads(prompt.split("Assigned requirement: ", 1)[1].split("\n", 1)[0])
        authority = payload.get("authoritative_sufficient", True)
        payload = dict(next(row for row in payload["coverage"] if row["requirement_id"] == assigned["id"]))
        if payload.get("authority_sufficient") is None:
            payload["authority_sufficient"] = authority
        if not payload.get("claim_support"):
            payload["claim_support"] = (
                [
                    {
                        "claim": payload["explanation"],
                        "source_urls": payload["source_urls"],
                        "authority_basis": "Fixture source authority",
                    }
                ]
                if payload["source_urls"]
                else []
            )
    return json.dumps(payload)


def plan_payload(payload):
    """Project legacy fixtures onto the semantic planner-only contract."""
    return {"components": [
        {"request": component["request"], "input_references": ["original_request:1"],
         "requirements": [
             {"description": row["description"], "kind": row.get("kind", "factual_support"),
              "grounding": row.get("grounding", "knowledge_allowed"),
              "input_references": ["original_request:1"]}
             for row in component["requirements"]]}
        for component in payload["components"]]}


def support_record(payload):
    """Supply explicit authority judgments for older semantic transport fixtures."""
    if not isinstance(payload, dict) or "requirement_id" not in payload:
        return payload
    payload = dict(payload)
    if payload.get("authority_sufficient") is None:
        payload["authority_sufficient"] = bool(payload.get("source_urls"))
    if not payload.get("claim_support"):
        payload["claim_support"] = (
            [
                {
                    "claim": payload.get("explanation", "Fixture technical fact"),
                    "source_urls": payload["source_urls"],
                    "authority_basis": "Primary fixture source",
                }
            ]
            if payload.get("source_urls")
            else []
        )
    return payload


def publication_payload(payload, prompt, tools=()):
    """Migrate old scripted semantic fixtures; production accepts only the new schema."""
    if not isinstance(payload, dict) or "approved" not in payload:
        return payload

    import re

    if payload["approved"] == bool(payload.get("issues", [])):
        raise ValueError("Approval requires no issues; rejection requires concrete structured issues")
    references = re.findall(r'"reference": "(d[a-f0-9]+:b[0-9]+)"', prompt)
    issues = []
    for row in payload.get("issues", []):
        criterion = row.get("criterion_id")
        if not criterion:
            ids = row.get("requirement_ids", [])
            criterion = f"requirement:{ids[0]}" if ids else "core:correctness"
        if row.get("category") == "source_attribution":
            criterion = "core:attribution"
        elif row.get("category") == "research_coverage":
            criterion = "source:" + (row.get("requirement_ids") or ["method"])[0]
        if criterion == "core:technical_support":
            criterion = "core:correctness"
        passages = list(row.get("passage_ids", []))
        if row.get("source_urls") and row.get("absence_of_support") and not passages and tools:
            for url in row["source_urls"]:
                result = tools[0](url=url, start=0, length=4000)
                passages.append(result["passage_id"])
        issues.append({"criterion_id": criterion,
                       "problem": row["problem"], "material_impact": row.get("material_impact") or row["problem"],
                       "requested_correction": row["requested_correction"],
                       "draft_references": row.get("draft_references", []) or
                           ([references[0]] if not criterion.startswith("requirement:") and references else []),
                       "passage_ids": passages})
    return {"summary": payload.get("reason", "Scripted review"), "issues": issues,
            "suggestions": [row["improvement"] if isinstance(row, dict) else row
                            for row in payload.get("suggestions", [])]}


@pytest.fixture(autouse=True)
def migrate_legacy_publication_fixtures(monkeypatch):
    """Old text fixtures describe semantics, not the removed production wire contract."""
    from modules.techniques.publication_review import PublicationReview

    original = TechniqueService._json_output

    def output(self, role, prompt, schema, tools):
        if schema is not PublicationReview or callable(getattr(self.runner, "structured", None)):
            return original(self, role, prompt, schema, tools)
        instruction = "Return only JSON matching this schema:\n" + json.dumps(schema.model_json_schema())
        response = self.runner(role, prompt + "\n" + instruction, tools or [])
        try:
            return schema.model_validate(publication_payload(json.loads(response), str(prompt), tools or []))
        except ValueError as error:
            error.rejected_response = response
            raise

    monkeypatch.setattr(TechniqueService, "_json_output", output)
