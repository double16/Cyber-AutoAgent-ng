"""Technique schemas use the native bridge while retaining controller-owned decisions."""

import io
import json
from copy import deepcopy

import pytest
from strands import Agent, tool
from strands.models.model import Model

from modules.agents.native_structured import _UNSUPPORTED_NATIVE
from modules.config.models.structured_output import StructuredOutputValidationError, validate_structured_json
from modules.techniques import roles
from modules.techniques.coverage import Requirement, select_sources
from modules.techniques.diagnostics import DiagnosticSession
from modules.techniques.service import AuthoredOutput, AuthoredTechnique, Decision, Feedback


class TechniqueModel(Model):
    def __init__(self, payloads, provider="ollama"):
        self.config = {"model_id": "technique-fixture"}
        self._cyber_provider = provider
        self._cyber_native_structured_output = provider != "bedrock"
        self.payloads = iter(payloads)
        self.calls = []
        self.investigated = False

    def get_config(self):
        return self.config

    def update_config(self, **kwargs):
        self.config.update(kwargs)

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        self.calls.append(("native", output_model, deepcopy(prompt), system_prompt))
        payload = next(self.payloads)
        if isinstance(payload, Exception):
            raise payload
        yield {"metadata": {"usage": {"inputTokens": 12, "outputTokens": 8, "totalTokens": 20},
                            "metrics": {"latencyMs": 1}}}
        yield {"output": validate_structured_json(output_model, payload if isinstance(payload, str) else json.dumps(payload))}

    async def stream(self, messages, tool_specs=None, system_prompt=None, tool_choice=None, **kwargs):
        self.calls.append(("tool", deepcopy(tool_specs), deepcopy(messages), system_prompt))
        yield {"messageStart": {"role": "assistant"}}
        if tool_specs and tool_specs[0]["name"] == "inspect_evidence":
            if self.investigated:
                yield {"contentBlockDelta": {"delta": {"text": "Read-only evidence shows contribution."},
                                             "contentBlockIndex": 0}}
                yield {"contentBlockStop": {"contentBlockIndex": 0}}
                yield {"messageStop": {"stopReason": "end_turn"}}
                return
            self.investigated = True
            payload, name = {}, "inspect_evidence"
        else:
            payload, name = next(self.payloads), tool_specs[0]["name"]
        yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "result", "name": name}},
                                     "contentBlockIndex": 0}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(payload)}},
                                     "contentBlockIndex": 0}}
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "tool_use"}}


@pytest.fixture
def runner_factory(monkeypatch):
    _UNSUPPORTED_NATIVE.clear()
    monkeypatch.setattr(roles, "require_prompt_token_limit", lambda *_: 40_000)
    monkeypatch.setattr(roles, "model_uses_server_side_state", lambda _: False)
    # Use the real SDK and prompt-budget hook with an offline model.
    monkeypatch.setattr(roles, "create_agent_with_stateful_retry", lambda kwargs, _: Agent(**kwargs))

    def build(payloads, provider="ollama", diagnostics=None):
        model = TechniqueModel(payloads, provider)
        monkeypatch.setattr(roles, "create_strands_model", lambda *_: model)
        return roles.TechniqueRoleRunner(provider, "technique-fixture", diagnostics=diagnostics), model

    yield build
    _UNSUPPORTED_NATIVE.clear()


@pytest.mark.parametrize("provider", ["ollama", "litellm", "gemini", "bedrock"],
                         ids=["local", "gateway", "google", "aws"])
def test_technique_structured_transport_keeps_hooks_schema_and_usage(runner_factory, provider, tmp_path):
    session = DiagnosticSession(stderr=io.StringIO())
    session.open(str(tmp_path))
    runner, model = runner_factory([{"selected_id": None, "reason": "No reusable method"}], provider, session)
    result = runner.structured("technique_judge", "Review source/filesystem applicability", Decision, [])
    session.close()
    assert result.selected_id is None
    assert len(model.calls) == 1
    assert model.calls[0][0] == ("tool" if provider == "bedrock" else "native")
    records = [json.loads(line) for line in session.path.read_text().splitlines()]
    before = next(row for row in records if row["type"] == "model_input")
    assert before["schema_name"] == "Decision" and len(before["schema_id"]) == 64
    assert before["prompt_limit"] == 40_000
    assert "source/filesystem" in str(before["messages"])
    assert next(row for row in records if row["type"] == "structured_output")["validation_outcome"] == "validated"
    if provider != "bedrock":
        assert next(row for row in records if row["type"] == "role_usage")["total"] == 20


@pytest.mark.parametrize("failure,fallback", [
    (NotImplementedError("native unavailable"), True),
    (ValueError("unsupported schema keyword"), False),
    (ConnectionError("offline"), False),
])
def test_native_fallback_only_for_transport_unavailability(runner_factory, tmp_path, failure, fallback):
    session = DiagnosticSession(stderr=io.StringIO())
    session.open(str(tmp_path))
    runner, model = runner_factory([failure, {"selected_id": None, "reason": "No match"}], diagnostics=session)
    if fallback:
        assert runner.structured("technique_judge", "Select", Decision, []).selected_id is None
        assert [row[0] for row in model.calls] == ["native", "tool"]
    else:
        with pytest.raises(type(failure), match=str(failure)):
            runner.structured("technique_judge", "Select", Decision, [])
        assert [row[0] for row in model.calls] == ["native"]
    session.close()
    rows = [json.loads(line) for line in session.path.read_text().splitlines()]
    assert any(row.get("fallback_reason") == "native_unsupported" for row in rows) is fallback


def test_author_schema_excludes_controller_fields_and_preserves_markdown(scripted_service, runner_factory):
    service = scripted_service.service
    body = '# Guidance\n```javascript\nconst value = "quoted";\n```\n\\path\n'
    metadata = {"title": "Source review", "purpose": "Trace input", "applicability": ["Source available"],
                "phases": ["analysis"], "triggers": []}
    runner, model = runner_factory([{"metadata": metadata, "body": body}])
    service.runner = runner
    authored = service._json("technique_author", "Write read-only guidance", AuthoredTechnique)
    assert authored.body == body and authored.metadata.sources == []
    assert authored.metadata.active is True
    assert model.calls[0][1] is AuthoredOutput
    fields = AuthoredOutput.model_json_schema()["$defs"]["AuthorMetadata"]["properties"]
    assert set(fields) == {"title", "purpose", "applicability", "phases", "triggers"}


@pytest.mark.parametrize("subject", ["https://assigned.invalid", "tcp://assigned.invalid:443", "/src/handler.py"])
def test_feedback_investigates_once_and_finalizes_without_tools(scripted_service, runner_factory, subject):
    inspected = []

    @tool
    def inspect_evidence() -> str:
        """Read assigned evidence."""
        inspected.append(subject)
        return "A useful prerequisite was established."

    runner, model = runner_factory([{"score": 1, "reason": "Evidence established a useful prerequisite"}])
    scripted_service.service.runner = runner
    feedback = scripted_service.service._json("technique_evaluator", f"Evaluate contribution for {subject}",
                                              Feedback, [inspect_evidence])
    assert feedback.score == 1 and inspected == [subject]
    assert [row[0] for row in model.calls] == ["tool", "tool", "native"]
    assert "A useful prerequisite" in str(model.calls[-1][2])
    assert "Investigation is closed" in str(model.calls[-1][2])


def test_structured_prompt_limit_includes_schema(monkeypatch):
    monkeypatch.setattr(roles, "require_prompt_token_limit", lambda *_: 1)
    seen = []

    def estimate(*args, **kwargs):
        seen.extend(kwargs["tool_specs"])
        return 2

    monkeypatch.setattr(roles, "estimate_prompt_tokens", estimate)
    with pytest.raises(ValueError, match="configured prompt limit"):
        roles.TechniqueRoleRunner("ollama", "fixture").structured("technique_judge", "Select", Decision, [])
    assert seen == [{"output_schema": Decision.model_json_schema()}]


@pytest.mark.parametrize("missing", [None, {"unknown": "value"}])
def test_missing_or_invalid_sdk_output_never_falls_back_to_text(monkeypatch, missing):
    from types import SimpleNamespace

    monkeypatch.setattr(roles, "require_prompt_token_limit", lambda *_: 40_000)
    monkeypatch.setattr(roles, "create_strands_model", lambda *_: object())
    monkeypatch.setattr(roles, "model_uses_server_side_state", lambda _: False)
    monkeypatch.setattr(roles, "create_agent_with_stateful_retry", lambda *_: SimpleNamespace())
    monkeypatch.setattr(roles, "invoke_structured", lambda *_args, **_kwargs: SimpleNamespace(structured_output=missing))
    with pytest.raises(ValueError):
        roles.TechniqueRoleRunner("ollama", "fixture").structured("technique_judge", "Select", Decision, [])


def test_invalid_native_content_is_logged_redacted_without_tool_fallback(runner_factory, tmp_path):
    session = DiagnosticSession(stderr=io.StringIO())
    session.private_values = ("private-subject",)
    session.open(str(tmp_path))
    runner, model = runner_factory([{"selected_id": None, "reason": "private-subject", "extra": True}],
                                   diagnostics=session)
    with pytest.raises(StructuredOutputValidationError):
        runner.structured("technique_judge", "Select", Decision, [])
    session.close()
    raw = session.path.read_text()
    assert "private-subject" not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    failure = next(row for row in rows if row["type"] == "model_failed")
    assert failure["rejected_response"] and failure["validation_errors"]
    assert len(model.calls) == 1


def test_all_typed_service_stages_use_structured_boundary(scripted_service):
    state = scripted_service
    legacy = state.service.runner
    calls = []

    class TypedRunner:
        def __call__(self, *args):
            pytest.fail("Typed calls must not use the legacy text path")

        def structured(self, role, prompt, schema, tools):
            calls.append((role, schema, prompt))
            payload = json.loads(legacy(role, prompt + json.dumps(schema.model_json_schema()), tools))
            if schema is AuthoredOutput:
                payload["metadata"] = {key: value for key, value in payload["metadata"].items()
                                       if key in schema.model_fields["metadata"].annotation.model_fields}
            return schema.model_validate(payload)

    state.service.runner = TypedRunner()
    bundle = state.service.resolve_bundle("Review JWT", operation="op", task="task")
    state.service.evaluate("op", "task", "Read-only source analysis established a prerequisite")
    state.selection = bundle.delivered[0].revision_id
    state.service.get("Reuse JWT")
    assert {role for role, _, _ in calls} >= {
        "technique_planner", "technique_plan_reviewer", "technique_judge", "technique_author",
        "technique_critic", "technique_evaluator",
    }
    assert all("Return only JSON matching this schema" not in prompt for _, _, prompt in calls)


def test_feedback_correction_retains_investigation(scripted_service, runner_factory):
    @tool
    def inspect_evidence() -> str:
        """Read evidence."""
        return "Observed prerequisite"

    runner, model = runner_factory([{"score": 9, "reason": "Invalid score"},
                                   {"score": 1, "reason": "Prerequisite clarified"}])
    service = scripted_service.service
    service.runner = runner
    with pytest.raises(StructuredOutputValidationError) as caught:
        service._json("technique_evaluator", "Evaluate", Feedback, [inspect_evidence])
    repaired = service._repair_prompt("Evaluate", caught.value)
    result = service._json("technique_evaluator", repaired, Feedback)
    assert result.score == 1
    assert [row[0] for row in model.calls] == ["tool", "tool", "native", "native"]
    assert "Observed prerequisite" in repaired and "score" in repaired


@pytest.mark.parametrize("recover", [False, True])
def test_native_source_selection_corrects_malformed_arrays_with_retained_coverage(
    scripted_service, runner_factory, recover,
):
    urls = ["https://vendor.example/one", "https://vendor.example/two"]

    def payload(selected):
        return {"selected_urls": selected, "attributions": [{"url": url, "reason": url} for url in selected],
                "coverage": [{"requirement_id": "method", "source_urls": selected,
                              "explanation": "Supported mechanism", "gaps": []}],
                "reason": "Authoritative method support", "authoritative_sufficient": True}

    malformed = json.dumps(payload(urls)).replace('"gaps": []}', '"gaps": []"}')
    runner, model = runner_factory([payload(urls[:1]), malformed, payload(urls) if recover else malformed])
    service = scripted_service.service
    service.runner = runner
    documents = [{"url": url, "title": url, "text": "Method guidance"} for url in urls]
    requirements = [Requirement(id="method", description="Reusable method")]
    if recover:
        assert select_sources(service, "Review prerequisites", requirements, documents).selected_urls == urls
    else:
        with pytest.raises(StructuredOutputValidationError):
            select_sources(service, "Review prerequisites", requirements, documents)
    assert len(model.calls) == 3 and all(row[0] == "native" for row in model.calls)
    correction = str(model.calls[-1][2])
    assert "Validation failure" in correction and "json_invalid" in correction
    assert "Authoritative method support" in correction and urls[0] in correction
    assert malformed in model.calls[-1][2][0]["content"][0]["text"]


def test_native_author_correction_preserves_rejected_draft_and_research(scripted_service, runner_factory):
    state = scripted_service
    legacy = state.service.runner
    original = json.loads(legacy("technique_author", "", []))
    original["metadata"] = {key: value for key, value in original["metadata"].items()
                            if key in AuthoredOutput.model_fields["metadata"].annotation.model_fields}
    bad = {"metadata": original["metadata"], "body": {"invalid": "draft"}}
    native, model = runner_factory([bad, original])

    class TypedRunner:
        def structured(self, role, prompt, schema, tools):
            if role == "technique_author":
                return native.structured(role, prompt, schema, tools)
            return schema.model_validate_json(legacy(role, prompt + json.dumps(schema.model_json_schema()), tools))

    state.service.runner = TypedRunner()
    revision, rendered = state.service.get("Reusable source review")
    assert revision and original["body"] in rendered
    assert len(state.research_calls) == 1 and len(model.calls) == 2
    correction = model.calls[-1][2][0]["content"][0]["text"]
    assert json.dumps(bad) in correction and "body" in correction and "string_type" in correction


@pytest.mark.parametrize("provider", ["ollama", "bedrock"], ids=["native", "schema-tool"])
def test_phase_alignment_and_coverage_review_use_real_structured_transport(runner_factory, provider):
    from modules.techniques.alignment import AlignmentOutput
    from modules.techniques.coverage import SourceSelection

    alignment = {"adjusted_request": "Develop CVE applicability hypotheses", "reason": "Hypothesis phase"}
    assessment = {
        "selected_urls": ["https://vendor.example/advisory"],
        "attributions": [{"url": "https://vendor.example/advisory", "reason": "Known mechanism"}],
        "coverage": [{"requirement_id": "method", "source_urls": ["https://vendor.example/advisory"],
                      "explanation": "Supports a hypothesis", "gaps": [], "future_validation": ["Check version"]}],
        "authoritative_sufficient": True, "reason": "Enough support for guidance",
    }
    runner, model = runner_factory([alignment, assessment], provider)
    assert runner.structured("technique_request_aligner", "Align hypothesis guidance", AlignmentOutput,
                             []).adjusted_request == alignment["adjusted_request"]
    assert runner.structured("technique_coverage_reviewer", "Review factual support", SourceSelection, []).sufficient
    assert len(model.calls) == 2
