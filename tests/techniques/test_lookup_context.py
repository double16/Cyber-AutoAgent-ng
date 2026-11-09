"""Applicability and feedback remain useful across web and non-web operation modules."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.techniques.context import LookupContext, LookupDecision
from modules.techniques.documents import TechniqueDocument


@pytest.mark.parametrize("module,filename,phase,kind,observation", [
    ("code_security", "source-to-sink.md", "analysis", "symbol", "Controlled input reaches a bound SQL parameter"),
    ("context_navigator", "read-only-context.md", "reconnaissance", "host", "Configuration declares a dependency"),
    ("ctf", "evidence-hypotheses.md", "hypothesis_generation", "challenge", "An input changes recorded behavior"),
    ("threat_emulation", "marker-emulation.md", "execution", "trace", "Authorized marker and telemetry are defined"),
    ("web", "jwt-verification.md", "hypothesis_generation", "endpoint", "A captured response contains a token"),
])
def test_real_module_guidance_reuses_with_local_context_and_durable_feedback(
    scripted_service, module, filename, phase, kind, observation,
):
    state = scripted_service
    path = Path("src/modules/operation_plugins") / module / "techniques" / filename
    document = TechniqueDocument(path.read_text())
    revision = state.service.store.publish(document, f"module:{module}:{filename}", f"module:{module}")
    context = LookupContext(
        phase_contexts=[phase], purpose="Resolve the assigned investigation question",
        subjects=[{"kind": kind, "value": "assigned subject"}], capabilities=["runtime_added_reader"],
    )
    context.observe(observation, "artifact:captured.txt", "source", "subject")
    state.selection = revision
    original = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_judge" and "generic_request" in prompt:
            state.calls.append((role, prompt, tools))
            return json.dumps({
                "generic_request": document.metadata.title,
                "applicability": "supported", "unmet_prerequisites": [], "reason": "Evidence supports this method",
            })
        return original(role, prompt, tools)

    state.service.runner = runner
    events = []
    state.service.events.callback = events.append
    selected = state.service.tool("op", "task", context)
    assert set(selected.tool_spec["inputSchema"]["json"]["properties"]) == {"request"}
    rendered = selected(request="Help with the assigned investigation")
    assert document.body in rendered
    assert "Assessment: supported" in rendered
    assert "Current-task applicability" not in state.service.store.render(revision)
    assert not state.research_calls
    receipt = state.service.repository.pending_uses("op", "task")[0]
    delivered = json.loads(receipt["use_context"])
    assert delivered["generic_request"] == document.metadata.title
    assert delivered["phase_contexts"] == [phase]
    assert delivered["observations"] == []
    assert all(observation not in prompt for _, prompt, _ in state.calls)
    # Cross-module reuse and a second delivery must not overwrite the first receipt or count twice.
    context.purpose = "A different contribution"
    selected(request="Equivalent methodology")
    assert state.service.repository.pending_uses("op", "task") == [receipt]
    state.service.evaluate("op", "task", "The method resolved part of the task; other work remains")
    assert state.service.repository.get(revision)["score_count"] == 1
    evaluation = next(prompt for role, prompt, _ in state.calls if role == "technique_evaluator")
    assert "demonstrated contribution" in evaluation
    assert "Unrelated missing research" in evaluation
    assert document.metadata.title in evaluation
    assert next(row for row in events if row["type"] == "technique_resolved")["applicability"] == "supported"


def test_request_abstraction_precedes_research_and_phase_guides_author(scripted_service):
    state = scripted_service
    context = LookupContext(phase_contexts=["hypothesis_generation"], purpose="Investigate incidental route")
    context.observe("GET /private-app/oidc-client; server behavior unknown", "artifact:source", "source", "subject")
    original = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_judge" and "generic_request" in prompt:
            assert "/private-app/oidc-client" in prompt
            return json.dumps({
                "generic_request": "Conditional OIDC discovery hypothesis design using /.well-known/openid-configuration",
                "applicability": "conditional",
                "unmet_prerequisites": ["Establish discovery retrieval and attacker-controlled URL processing"],
                "reason": "An auth route name does not establish discovery or SSRF",
            })
        return original(role, prompt, tools)

    state.service.runner = runner
    returned = state.service.tool("op", "task", context)(request="Investigate /private-app/oidc-client")
    assert "Assessment: conditional" in returned and "Establish discovery retrieval" in returned
    assert len(state.research_calls) == 1
    assert state.research_calls[0].startswith(
        "Conditional OIDC discovery hypothesis design using /.well-known/openid-configuration",
    )
    assert "/private-app/oidc-client" not in state.research_calls[0]
    for role, prompt, _ in state.calls:
        if role in {"technique_author", "technique_critic"}:
            assert "/private-app/oidc-client" not in prompt
            assert 'Required phase contexts: ["hypothesis_generation"]' in prompt
    critic = next(prompt for role, prompt, _ in state.calls if role == "technique_critic")
    assert "Primary applicable advisory" in critic
    assert "Considered 1 fetched documents; selected 1" in critic
    assert "Public protocol metadata alone" in critic


@pytest.mark.parametrize("assessment,reason", [
    ("incompatible", "Task prohibits the requested action"),
    ("conditional", "Unknown method prerequisites"),
])
def test_invalid_applicability_stops_before_research(scripted_service, assessment, reason):
    state = scripted_service
    state.service.runner = lambda role, *_: json.dumps({"approved": True, "reason": "Preserved",
                               "references": ["original_request"], "subject_preserved": True,
        "mechanisms_preserved": True, "deliverables_preserved": True, "additions": [], "omissions": []}) \
        if role == "technique_request_reviewer" else json.dumps({
        "generic_request": "Source analysis", "applicability": assessment,
        "unmet_prerequisites": [], "reason": reason,
    })
    if assessment == "incompatible":
        assert state.service._prepare("Review source", LookupContext()).applicability == "uncertain"
    else:
        with pytest.raises(ValueError, match="assessment failed|explicit unmet prerequisites"):
            state.service.get("Review source", LookupContext())
    assert not state.research_calls


def test_unknown_provenance_is_conditional_and_unknown_phase_remains_usable(scripted_service):
    state = scripted_service
    revision = state.publish()
    original = state.service.runner
    state.selection = revision
    state.service.runner = lambda role, prompt, tools: json.dumps({
        "generic_request": "Token analysis", "applicability": "supported",
        "unmet_prerequisites": [], "reason": "Potentially useful method",
    }) if "generic_request" in prompt else original(role, prompt, tools)
    context = LookupContext(purpose="Review captured observations")
    context.observe("Potential token handling", "tool:runtime-added", "unknown", "subject")
    result = state.service.tool("op", "task", context)(request="Review token handling")
    assert "Assessment: supported" in result
    assert "Establish subject-specific prerequisites" not in result
    assert json.loads(state.service.repository.pending_uses("op", "task")[0]["use_context"])["phase_contexts"] == []


def test_phase_incompatible_selection_is_rejected_and_generation_cannot_publish_wrong_phase(scripted_service):
    state = scripted_service
    revision = state.publish()
    state.selection = revision
    with pytest.raises(ValueError, match="must support a requested phase context"):
        state.service.get("Source analysis", LookupContext(phase_contexts=["analysis"]))
    state.selection = None
    with pytest.raises(ValueError, match="must support a requested phase context"):
        state.service.get("Source analysis", LookupContext(phase_contexts=["analysis"]))
    assert len(state.service.repository.revisions()) == 1
    assert not any(role == "technique_critic" for role, _, _ in state.calls)


def test_final_source_provenance_is_validated_before_critic(scripted_service):
    state = scripted_service
    state.service.private_values = ("private-target",)
    original = state.service.runner

    def runner(role, prompt, tools):
        response = original(role, prompt, tools)
        if role == "technique_source_assessor":
            data = json.loads(response)
            data["reason"] = "Contains private-target in source provenance"
            return json.dumps(data)
        return response

    state.service.runner = runner
    with pytest.raises(ValueError, match="operation-specific identifiers"):
        state.service.get("Token analysis")
    assert not state.service.repository.revisions()
    assert not any(role == "technique_critic" for role, _, _ in state.calls)


def test_context_bounds_prioritize_subjects_and_ignore_analysis():
    context = LookupContext()
    for number in range(8):
        context.observe("x" * 3000, f"shared:{number}", "source", "shared")
    context.observe("Focused source", "subject", "source", "subject")
    context.observe("Focused source", "subject", "source", "subject")
    context.observe("Generated hypothesis", "analysis", "analysis", "subject")
    context.observe("Methodology", "guide", "guidance", "subject")
    context.observe(" ", "empty", "source", "subject")
    assert len(context.observations) == 6
    assert context.observations[0].source == "subject"
    assert all(len(row.excerpt) <= 2000 for row in context.observations)
    decision = LookupDecision(generic_request="Source analysis", applicability="supported",
                              unmet_prerequisites=[], reason="Observed input flow")
    assert "None identified" in decision.render()
    context.observe("Focused source", "subject", "unknown", "shared")
    assert context.observations[0].origin == "source" and context.observations[0].scope == "subject"
    initially_unknown = LookupContext()
    initially_unknown.observe("Initially unclassified", "new-source", "unknown", "shared")
    initially_unknown.observe("Initially unclassified", "new-source", "source", "subject")
    assert initially_unknown.observations[0].origin == "source"


def test_context_free_receipts_remain_compatible(store, markdown):
    with closing(sqlite3.connect(store.repository.path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(technique_uses)")}
    assert "use_context" in columns
    # Old callers still create valid context-free receipts and feedback stays idempotent.
    revision = store.publish(TechniqueDocument(markdown()), "module:legacy", "module:fixture")
    store.repository.use("op", "task", revision)
    assert store.repository.pending_uses("op", "task")[0]["use_context"] == "{}"
    assert store.repository.feedback("op", "task", revision, 1, "Useful")["score_count"] == 1
    assert store.repository.feedback("op", "task", revision, 1, "Repeated") is None


def test_binding_non_inventory_task_preserves_constraints_and_declared_origins(scripted_service, tmp_path, monkeypatch):
    from strands import tool

    from modules.techniques.integration import bind_task_techniques

    @tool
    def source_reader() -> str:
        """Read assigned source."""
        return "source"

    source_reader._cyber_technique_observation_origin = "source"
    phase = SimpleNamespace(id=1, technique_contexts=("analysis",), produces_hypotheses=False)
    task = SimpleNamespace(task_uid="source-task", objective="Trace assigned data flow", phase=1, kind="standard",
                           target_ids=["source"], evidence=[], acceptance=SimpleNamespace(basis=SimpleNamespace(
                               snapshot_hash="", item_ids=[], source_refs=[],
                           )))
    plan = SimpleNamespace(phases=[phase], constraints=["Inspect source without executing it"], targets=[
        SimpleNamespace(target_id="source", type="filesystem", value="/assigned/repository"),
        SimpleNamespace(target_id="other", type="network", value="tcp://service.test:9000"),
    ])
    runtime = SimpleNamespace(technique_service=scripted_service.service,
                              config=SimpleNamespace(module="code_security"), operation_id="op")
    monkeypatch.setattr("modules.tools.memory._operation_output_root", lambda: str(tmp_path))
    tools = bind_task_techniques(runtime, plan, task, [source_reader])
    bound = tools[-1]._cyber_technique_context
    assert bound.adapters == []
    assert bound.lookup.subjects == [{"target_id": "source", "kind": "filesystem", "value": "/assigned/repository"}]
    assert bound.lookup.constraints == ["Inspect source without executing it"]
    assert bound.tool_origins["source_reader"] == "source"
    assert "Inspect source without executing it" in bound.lookup.render()
    source_reader._cyber_technique_observation_origin = "invented"
    with pytest.raises(ValueError, match="Unknown technique observation origin"):
        bind_task_techniques(runtime, plan, task, [source_reader])


@pytest.mark.parametrize("subject,method_request", [
    ("endpoint", "CSRF token issuance endpoint hypothesis generation methodology"),
    ("endpoint", "Generic CSRF testing methodology for token issuance and validation endpoints"),
    ("service", "Network service trust boundary hypothesis generation"),
    ("source", "Source filesystem input flow hypothesis generation"),
])
def test_catalog_miss_researches_independently_of_applicability(scripted_service, subject, method_request):
    state = scripted_service
    state.publish()
    original = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_judge" and "generic_request" in prompt:
            assert "candidates[" not in prompt
            assert "revision_id:" not in prompt
            state.calls.append((role, prompt, tools))
            return json.dumps({
                "generic_request": method_request, "applicability": "conditional",
                "unmet_prerequisites": ["Establish mechanism prerequisites"], "reason": "Future checks are useful",
            })
        return original(role, prompt, tools)

    state.service.runner = runner
    result = state.service.tool("op", "task", LookupContext(
        phase_contexts=["hypothesis_generation"], subjects=[{"kind": subject}],
    ))(request=method_request)
    assert "Assessment: conditional" in result
    assert len(state.research_calls) == 1 and state.research_calls[0].startswith(method_request)


@pytest.mark.parametrize("kind", ["methodology_constraint"])
def test_referenced_conflict_stops_before_candidate_search(scripted_service, kind, monkeypatch):
    state = scripted_service
    context = LookupContext(methodology_constraints=["Read only"])
    context.observe("Input cannot reach sink", "artifact:source", "source", "subject")
    reference = "Read only"
    state.service.runner = lambda *_: json.dumps({
        "generic_request": "Source analysis", "applicability": "incompatible",
        "unmet_prerequisites": [], "reason": "Requested action contradicts supplied context",
        "conflict": {"kind": kind, "requirement": "Requires modifying the subject",
                     "context_reference": reference},
    })
    monkeypatch.setattr(state.service.store, "search", lambda *_a, **_k: pytest.fail("Must not search"))
    with pytest.raises(ValueError, match="restriction.*Requires modifying"):
        state.service._prepare("Source analysis", context)
    assert not state.research_calls


@pytest.mark.parametrize("reference,origin,scope", [
    ("absent", "source", "subject"),
    ("artifact:source", "unknown", "subject"),
    ("artifact:source", "source", "shared"),
])
def test_invalid_conflict_reference_exhausts_one_repair(scripted_service, reference, origin, scope):
    state = scripted_service
    context = LookupContext()
    context.observe("Potential contradiction", "artifact:source", origin, scope)
    calls = []

    def runner(_role, prompt, _tools):
        calls.append(prompt)
        return json.dumps({
            "generic_request": "Source analysis", "applicability": "incompatible",
            "unmet_prerequisites": [], "reason": "No reusable candidate",
            "conflict": {"kind": "methodology_constraint", "requirement": "Requires input",
                         "context_reference": reference},
        })

    state.service.runner = runner
    decision = state.service._prepare("Source analysis", context)
    assert decision.applicability == "uncertain"
    assert len(calls) == 4 and "Repair the assessment" in calls[1]
    assert not state.research_calls


def test_invalid_assessment_repairs_without_mislabeling_catalog_miss(scripted_service):
    state = scripted_service
    original = state.service.runner
    attempts = []

    def runner(role, prompt, tools):
        if role == "technique_judge" and "generic_request" in prompt:
            attempts.append(prompt)
            if len(attempts) == 1:
                return json.dumps({
                    "generic_request": "Source analysis", "applicability": "incompatible",
                    "unmet_prerequisites": [], "reason": "No candidates",
                })
        return original(role, prompt, tools)

    state.service.runner = runner
    result = state.service.tool("op", "task", LookupContext())(request="Source analysis")
    assert "Assessment: conditional" in result
    assert len(attempts) == 2 and len(state.research_calls) == 1


def test_non_incompatible_assessment_rejects_conflict(scripted_service):
    state = scripted_service
    state.service.runner = lambda *_: json.dumps({
        "generic_request": "Source analysis", "applicability": "supported",
        "unmet_prerequisites": [], "reason": "Useful",
        "conflict": {"kind": "methodology_constraint", "requirement": "Write", "context_reference": "Read only"},
    })
    with pytest.raises(ValueError, match="assessment failed"):
        state.service._prepare("Source analysis", LookupContext(methodology_constraints=["Read only"]))


def test_catalog_filled_after_lease_reuses_without_research(scripted_service, monkeypatch):
    state = scripted_service
    revision = state.publish()
    selections = iter([None, revision])
    monkeypatch.setattr(state.service, "_select", lambda *_: next(selections))
    result = state.service.tool("op", "task", LookupContext())(request="JWT handling")
    assert "Assessment: conditional" in result
    assert not state.research_calls


@pytest.mark.parametrize("subject", ["http", "network", "filesystem"])
def test_independent_confirmation_detects_guidance_vs_task_confusion(scripted_service, subject):
    state = scripted_service
    prompts = []

    def runner(role, prompt, tools):
        if role == "technique_request_reviewer":
            return json.dumps({"approved": True, "reason": "Preserved",
                               "references": ["original_request"], "subject_preserved": True,
        "mechanisms_preserved": True, "deliverables_preserved": True, "additions": [], "omissions": []})
        prompts.append((role, prompt))
        confirmation = role == "technique_applicability_confirmation"
        return json.dumps({
            "generic_request": "Trust boundary hypothesis guidance",
            "applicability": "conditional" if confirmation else "incompatible",
            "unmet_prerequisites": ["Inspect implementation"] if confirmation else [],
            "reason": "Future checks are useful" if confirmation else "Task requires terminal disposition",
            "conflict": None if confirmation else {
                "kind": "methodology_constraint", "requirement": "Future validation",
                "context_reference": "Hypothesis only; no execution",
            },
        })

    state.service.runner = runner
    decision = state.service._prepare("Hypothesis guidance", LookupContext(
            purpose="Hypothesis generation", subjects=[{"kind": subject}],
            methodology_constraints=["Hypothesis only; no execution"],
        ))
    assert decision.applicability == "uncertain"
    assert len(decision.receipt(LookupContext())["assessment_provenance"]["assessments"]) == 2
    assert len(prompts) == 2
    assert "explicit methodology" in prompts[1][1]
    assert not state.research_calls


def test_invalid_incompatibility_repair_reaches_confirmation_and_retains_diagnostics(scripted_service):
    state = scripted_service
    calls = []

    def runner(role, prompt, tools):
        calls.append((role, prompt))
        if role == "technique_applicability_confirmation":
            return json.dumps({"generic_request": "Source analysis", "applicability": "conditional",
                               "unmet_prerequisites": ["Read advisory"], "reason": "Guidance can help"})
        return json.dumps({"generic_request": "Source analysis", "applicability": "incompatible",
                           "unmet_prerequisites": [], "reason": "Missing source material"})

    state.service.runner = runner
    decision = state.service._prepare("Source analysis", LookupContext())
    assert decision.applicability == "uncertain" and decision.conflict is None
    assert len(calls) == 3
    assert "Rejected response" in calls[1][1] and "Missing source material" in calls[1][1]
    provenance = decision.receipt(LookupContext())["assessment_provenance"]
    assert len(provenance["assessments"]) == 3 and len(provenance["validation_errors"]) == 2
    assert "assessment_provenance" not in decision.model_json_schema()["properties"]


def test_malformed_confirmation_repairs_once_then_delivers_uncertainty(scripted_service):
    state = scripted_service
    calls = []

    def runner(role, prompt, tools):
        calls.append((role, prompt))
        if role == "technique_applicability_confirmation":
            return "malformed JSON"
        return json.dumps({"generic_request": "Source analysis", "applicability": "incompatible",
                           "unmet_prerequisites": [], "reason": "Requires writes",
                           "conflict": {"kind": "methodology_constraint", "requirement": "Modify files",
                                        "context_reference": "Read only"}})

    state.service.runner = runner
    decision = state.service._prepare("Source analysis", LookupContext(methodology_constraints=["Read only"]))
    assert decision.applicability == "uncertain"
    assert len(calls) == 3 and "Validation failure" in calls[-1][1]


def test_uncertain_guidance_is_delivered_and_receipted(scripted_service):
    state = scripted_service
    original = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_applicability_confirmation" or (role == "technique_judge" and "generic_request" in prompt):
            return json.dumps({"generic_request": "JWT handling", "applicability": "incompatible",
                               "unmet_prerequisites": [], "reason": "Missing observations"})
        return original(role, prompt, tools)

    state.service.runner = runner
    result = state.service.tool("op", "task", LookupContext())(request="JWT handling")
    assert "Assessment: uncertain" in result and "does not authorize execution" in result
    receipt = json.loads(state.service.repository.pending_uses("op", "task")[0]["use_context"])
    assert receipt["applicability"] == "uncertain" and receipt["assessment_provenance"]["validation_errors"]


@pytest.mark.parametrize("subject", ["HTTP endpoint", "TCP service", "filesystem symbol"])
def test_tool_forwards_only_methodology_context_and_keeps_ids_for_bookkeeping(
    scripted_service, monkeypatch, subject,
):
    state = scripted_service
    context = LookupContext(
        phase_contexts=["hypothesis_generation"], purpose="private task objective",
        subjects=[{"kind": subject, "value": "private assigned subject"}],
        capabilities=["private_runtime_tool"], constraints=["private operation constraint"],
        methodology_constraints=["Explain non-destructive controls"],
        module_technique_policy=[{"restriction": "Keep guidance reusable", "origin": "web"}],
    )
    context.observe("private observed result", "artifact:private", "source", "subject")
    forwarded = []

    def resolve(request, supplied, *, operation, task):
        forwarded.append((request, supplied, operation, task))
        return SimpleNamespace(render=lambda: "Reusable guidance")

    monkeypatch.setattr(state.service, "resolve_bundle", resolve)
    selected = state.service.tool("operation-correlation", "task-correlation", context)
    for request in ["Explain mechanism prerequisites", "Describe future validation"]:
        assert selected(request=request) == "Reusable guidance"
        supplied = forwarded[-1][1]
        assert not hasattr(supplied, "purpose")
        assert supplied.phase_contexts == context.phase_contexts
        assert supplied.methodology_constraints == context.methodology_constraints
        assert supplied.module_technique_policy == context.module_technique_policy
        assert not any(hasattr(supplied, name) for name in ("subjects", "capabilities", "constraints", "observations"))
        assert forwarded[-1][2:] == ("operation-correlation", "task-correlation")
        supplied.module_technique_policy[0]["restriction"] = "Changed copy"
        assert context.module_technique_policy[0]["restriction"] == "Keep guidance reusable"
    assert context.purpose == "private task objective"
    assert len(context.observations) == 1


@pytest.mark.parametrize("subject", ["HTTP endpoint", "TCP service", "filesystem symbol"])
@pytest.mark.parametrize("entrypoint", ["tool", "bundle", "get"])
@pytest.mark.parametrize("repair", [False, True])
def test_generation_inputs_ignore_task_context_across_all_entrypoints(
    scripted_service, monkeypatch, subject, entrypoint, repair,
):
    from modules.techniques.context import MethodologyContext

    state = scripted_service
    monkeypatch.setattr(state.service, "_candidates", lambda *_: [])
    monkeypatch.setattr(state.service.store, "search", lambda *args, **kwargs: [])
    state.approve = not repair
    dirty = LookupContext(
        phase_contexts=["hypothesis_generation"],
        methodology_constraints=["Describe negative controls"],
        purpose="PRIVATE_OBJECTIVE",
        subjects=[{"kind": subject, "value": "PRIVATE_SUBJECT"}],
        capabilities=["PRIVATE_CAPABILITY"], constraints=["PRIVATE_PLAN_CONSTRAINT"],
    )
    dirty.observe("PRIVATE_OBSERVATION", "artifact:PRIVATE_ARTIFACT", "source", "subject")
    clean = MethodologyContext(
        phase_contexts=["hypothesis_generation"],
        methodology_constraints=["Describe negative controls"],
    )
    assembled = []
    for context in [dirty, clean]:
        state.calls.clear()

        def run():
            if entrypoint == "tool":
                return state.service.tool("PRIVATE_OPERATION", "PRIVATE_TASK", context)("JWT handling")
            if entrypoint == "bundle":
                return state.service.resolve_bundle(
                    "JWT handling", context, operation="PRIVATE_OPERATION", task="PRIVATE_TASK",
                )
            return state.service.get("JWT handling", context)

        if repair:
            with pytest.raises(ValueError):
                run()
        else:
            run()
        inputs = [(role, prompt, [item.tool_spec["name"] for item in tools])
                  for role, prompt, tools in state.calls]
        assert "PRIVATE_" not in str(inputs)
        assembled.append(inputs)
    assert assembled[0] == assembled[1]
    assert any(role == "technique_author" for role, _, _ in assembled[0])
    if repair:
        assert sum(role == "technique_author" for role, _, _ in assembled[0]) == 3
