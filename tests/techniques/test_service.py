import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from conftest import critique_result
from pydantic import ValidationError

from modules.techniques.documents import TechniqueDocument


@pytest.mark.parametrize("first_fails", [False, True])
def test_bound_lookup_serializes_calls_and_releases_after_failure(scripted_service, monkeypatch, first_fails):
    first_entered, release_first, second_started, second_entered = (Event() for _ in range(4))
    order = []

    def resolve(request, *args, **kwargs):
        order.append(request)
        if request == "first":
            first_entered.set()
            assert release_first.wait(5)
            if first_fails:
                raise ValueError("Research unavailable")
        else:
            second_entered.set()
        return SimpleNamespace(render=lambda: request)

    monkeypatch.setattr(scripted_service.service, "resolve_bundle", resolve)
    lookup = scripted_service.service.tool("operation", "task")

    def second():
        second_started.set()
        return lookup(request="second")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(lookup, request="first")
        try:
            assert first_entered.wait(5)
            second_result = pool.submit(second)
            assert second_started.wait(5)
            assert not second_entered.wait(0.1)
        finally:
            release_first.set()
        if first_fails:
            with pytest.raises(ValueError, match="Research unavailable"):
                first_result.result(timeout=5)
        else:
            assert first_result.result(timeout=5) == "first"
        assert second_result.result(timeout=5) == "second"
    assert order == ["first", "second"]


def test_distinct_task_bindings_can_resolve_concurrently(scripted_service, monkeypatch):
    entered = Barrier(2)

    def resolve(request, *args, **kwargs):
        entered.wait(timeout=5)
        return SimpleNamespace(render=lambda: request)

    monkeypatch.setattr(scripted_service.service, "resolve_bundle", resolve)
    first = scripted_service.service.tool("operation", "first-task")
    second = scripted_service.service.tool("operation", "second-task")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(first, request="first"), pool.submit(second, request="second")]
        assert [future.result(timeout=5) for future in futures] == ["first", "second"]


def test_runtime_tool_streams_serialize_without_blocking_the_event_loop(scripted_service, monkeypatch):
    first_entered, release_first, second_entered = (Event() for _ in range(3))

    def resolve(request, *args, **kwargs):
        if request == "first":
            first_entered.set()
            assert release_first.wait(5)
        else:
            second_entered.set()
        return SimpleNamespace(render=lambda: request)

    monkeypatch.setattr(scripted_service.service, "resolve_bundle", resolve)
    lookup = scripted_service.service.tool("operation", "task")

    async def invoke(request):
        return [event async for event in lookup.stream({
            "toolUseId": request, "name": "get_technique", "input": {"request": request},
        }, {})]

    async def run():
        first = asyncio.create_task(invoke("first"))
        try:
            assert await asyncio.to_thread(first_entered.wait, 5)
            second = asyncio.create_task(invoke("second"))
            assert not await asyncio.to_thread(second_entered.wait, 0.1)
        finally:
            release_first.set()
        return await asyncio.gather(first, second)

    results = asyncio.run(run())
    assert second_entered.is_set()
    assert all(events[-1].tool_result["status"] == "success" for events in results)


def test_active_match_reuses_complete_document_without_research(scripted_service):
    state = scripted_service
    revision = state.publish()
    state.selection = revision
    selected, markdown = state.service.get("JWT handling")
    assert selected == revision
    assert "Check assumptions and controls" in markdown
    assert not state.research_calls
    assert all(role == "technique_judge" for role, _, _ in state.calls)


def test_missing_technique_runs_separate_research_selection_author_and_critic(scripted_service):
    state = scripted_service
    revision, markdown = state.service.get("JWT handling")
    assert "revision_id:" in markdown
    assert state.service.repository.get(revision)["active"]
    assert len(state.research_calls) == 1 and state.research_calls[0].startswith("JWT handling\n")
    assert [role for role, _, _ in state.calls] == ["technique_source_assessor", "technique_requirement_assessor", "technique_author", "technique_critic"]
    assert all(bool(tools) == (role == "technique_critic") for role, _, tools in state.calls)
    author_prompt = next(prompt for role, prompt, _ in state.calls if role == "technique_author")
    critic_prompt = next(prompt for role, prompt, _ in state.calls if role == "technique_critic")
    assert "Return only JSON" in author_prompt
    assert "body is Markdown without YAML frontmatter" in author_prompt
    assert "Return Markdown only" not in critic_prompt
    assert "Return only JSON" in critic_prompt


@pytest.mark.parametrize("query", ["", " ", "x" * 2001, "password=secret-value"])
def test_invalid_requests_never_reach_research(scripted_service, query):
    with pytest.raises(ValueError):
        scripted_service.service.get(query)
    assert not scripted_service.calls
    assert not scripted_service.research_calls


def test_unknown_revision_and_source_selections_are_rejected(scripted_service):
    state = scripted_service
    state.publish()
    state.selection = "not-a-candidate"
    with pytest.raises(ValueError, match="unknown revision"):
        state.service.get("JWT")
    state.selection = None
    state.source_urls = ["https://unfetched.example/advisory"]
    with pytest.raises(ValueError, match="not fetched"):
        state.service.get("JWT")


def test_insufficient_source_and_rejected_critique_do_not_publish(scripted_service):
    state = scripted_service
    state.authoritative = False
    with pytest.raises(ValueError, match="coverage exhausted"):
        state.service.get("JWT")
    assert not state.service.repository.revisions()
    state.authoritative = True
    state.approve = False
    with pytest.raises(ValueError, match="3 attempts at critique"):
        state.service.get("JWT")
    assert len([role for role, _, _ in state.calls if role == "technique_author"]) == 3
    assert not state.service.repository.revisions()


def test_invalid_authored_document_and_sources_do_not_publish(scripted_service, markdown):
    state = scripted_service
    state.bad_document = True
    with pytest.raises(ValueError):
        state.service.get("JWT")
    state.bad_document = False
    original = state.service.runner
    state.service.runner = lambda role, prompt, tools: (
        json.dumps(
            {
                "metadata": TechniqueDocument(markdown()).metadata.model_dump()
                | {"sources": [{"url": "https://invented.example/advisory", "title": "Invented"}]},
                "body": "Guidance",
            }
        )
        if role == "technique_author"
        else original(role, prompt, tools)
    )
    revision, _ = state.service.get("JWT")
    assert state.service.store.document(revision).metadata.sources[0].url == state.source_urls[0]


def test_inactive_example_is_used_as_negative_context_and_rebuilt(scripted_service):
    state = scripted_service
    old = state.publish(active=False)
    original = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_judge" and "inactive technique address" in prompt:
            return json.dumps({"selected_id": old, "reason": "Same method"})
        return original(role, prompt, tools)

    state.service.runner = runner
    new, _ = state.service.get("JWT")
    assert old != new
    assert state.service.repository.get(old)["inactive_reason"] == "superseded"
    assert state.service.repository.get(new)["lineage"] == state.service.repository.get(old)["lineage"]
    assert any("negative_or_inactive" in prompt for role, prompt, _ in state.calls if role == "technique_author")


def test_bound_tool_records_use_and_independent_evaluator_scores_once(scripted_service):
    state = scripted_service
    revision = state.publish()
    state.selection = revision
    selected_tool = state.service.tool("op", "task")
    assert set(selected_tool.tool_spec["inputSchema"]["json"]["properties"]) == {"request"}
    assert "revision_id" in selected_tool(request="JWT handling")
    selected_tool(request="JWT handling")
    assert len(state.service.repository.pending_uses("op", "task")) == 1
    state.service.evaluate("op", "task", "A controlled test ruled out the suspected weakness")
    state.service.evaluate("op", "task", "Retry")
    assert state.service.repository.get(revision)["score_count"] == 1
    assert [role for role, _, _ in state.calls].count("technique_evaluator") == 1
    state.service.tool()(request="JWT handling")
    assert state.service.repository.get(revision)["score_count"] == 1


def test_strict_model_outputs_do_not_accept_truthy_strings(scripted_service):
    state = scripted_service
    state.service.runner = lambda *args: json.dumps({"approved": "yes", "reason": "bad"})
    from modules.techniques.service import Critique

    with pytest.raises(ValidationError):
        state.service._json("technique_critic", "Prompt", Critique)


def test_module_chain_imports_once_handles_missing_and_rejects_symlink_escape(scripted_service, markdown, tmp_path):
    root = tmp_path / "module"
    directory = root / "techniques"
    directory.mkdir(parents=True)
    (directory / "one.md").write_text(markdown())
    loader = SimpleNamespace(
        _inheritance_chain=lambda name: [name, "missing"],
        _find_module_dir=lambda name: root if name == "module" else None,
    )
    service = scripted_service.service
    service.synchronize_module(loader, "module")
    service.synchronize_module(loader, "module")
    assert len(service.repository.revisions()) == 1
    outside = tmp_path / "outside.md"
    outside.write_text(markdown())
    (directory / "escape.md").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        service.synchronize_module(loader, "module")


def test_every_bundled_starter_is_valid_and_has_external_authoring_prompt():
    root = Path("src/modules/operation_plugins")
    files = list(root.glob("*/techniques/*.md"))
    assert len(files) >= 6
    for path in files:
        document = TechniqueDocument(path.read_text())
        assert document.metadata.triggers
        assert (path.parent.parent / "docs" / "technique-generation-prompt.md").is_file()


def test_build_lease_is_released_after_error_and_excludes_other_owners(scripted_service):
    service = scripted_service.service
    with service._build_lease("JWT"):
        with pytest.raises(RuntimeError, match="being built"):
            with service._build_lease("JWT"):
                pass
    with service._build_lease("JWT"):
        pass


def test_regeneration_rejects_unchanged_inactive_generated_revision(scripted_service):
    state = scripted_service
    revision, _ = state.service.get("JWT handling")
    state.score = -1
    for task in ["one", "two", "three"]:
        state.service.repository.use("op", task, revision)
        state.service.evaluate("op", task, "Not useful")
    original = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_judge" and "inactive technique address" in prompt:
            return json.dumps({"selected_id": revision, "reason": "Same method"})
        return original(role, prompt, tools)

    state.service.runner = runner
    with pytest.raises(ValueError, match="repeats an inactive revision"):
        state.service.get("JWT handling")
    assert not state.service.repository.get(revision)["active"]


@pytest.mark.parametrize(
    ("failure", "stage", "reason"),
    [
        ("not JSON", "author_json", "Invalid JSON"),
        ('{"metadata": {}, "body": "guidance"}', "author_schema", "metadata.title"),
        ('{"metadata": {}, "body": ""}', "author_schema", "body"),
        ("empty_body", "document_validation", "body is empty"),
        ("oversized", "document_validation", "48,000"),
        ("inactive", "document_validation", "must be active"),
        ("private", "document_validation", "operation-specific"),
    ],
)
def test_invalid_author_responses_are_diagnosed_and_never_critiqued(scripted_service, tmp_path, failure, stage, reason):
    from modules.techniques.debug import TechniqueExports

    state = scripted_service
    runner = state.service.runner
    payload = json.loads(runner("technique_author", "", []))
    if failure == "empty_body":
        payload["body"] = "   "
    elif failure == "oversized":
        payload["body"] = "x" * 129_000
    elif failure == "inactive":
        payload["metadata"]["active"] = False
    elif failure == "private":
        payload["body"] = "internal-target.example"
        state.service.private_values = ("internal-target.example",)
    else:
        payload = None
    response = json.dumps(payload) if payload is not None else failure
    state.service.runner = lambda role, prompt, tools: (
        response if role == "technique_author" else runner(role, prompt, tools)
    )
    state.service.exports = TechniqueExports(str(tmp_path))
    events = []
    state.service.events.callback = events.append
    with pytest.raises(ValueError, match=f"3 attempts at {stage}") as error:
        state.service.get("generic guidance")
    assert reason in str(error.value)
    assert "internal-target.example" not in str(error.value)
    assert not state.service.repository.revisions()
    assert not any(role == "technique_critic" for role, _, _ in state.calls)
    paths = sorted((tmp_path / "techniques" / "builds").glob("*/*.json"))
    assert len(paths) == 3
    record = json.loads(paths[-1].read_text())
    assert record["stage"] == stage
    assert record["attempt"] == 3
    assert record["response"] == response[:128_000]
    assert record["truncated"] == (len(response) > 128_000)
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert paths[0].parent.stat().st_mode & 0o777 == 0o700
    assert events[-1]["status"] == "failed"
    assert events[-1]["stage"] == stage
    assert all("response" not in event and "document" not in event for event in events)


def test_schema_feedback_never_exposes_invalid_values(scripted_service):
    state = scripted_service
    runner = state.service.runner
    payload = json.loads(runner("technique_author", "", []))
    payload["body"] = {"sensitive": "RAW-INVALID-VALUE"}
    state.service.runner = lambda role, prompt, tools: (
        json.dumps(payload) if role == "technique_author" else runner(role, prompt, tools)
    )
    with pytest.raises(ValueError, match="body: string_type") as error:
        state.service.get("JWT")
    assert "RAW-INVALID-VALUE" not in str(error.value)


def test_author_repairs_json_and_preserves_original_markdown(scripted_service, tmp_path):
    from modules.techniques.debug import TechniqueExports

    state = scripted_service
    runner = state.service.runner
    payload = json.loads(runner("technique_author", "", []))
    body = '# Guidance\n\n\x60\x60\x60javascript\nconst value = "quoted";\n\x60\x60\x60\n'
    payload["body"] = body
    prompts = []
    state.service.exports = TechniqueExports(str(tmp_path))

    def run(role, prompt, tools):
        if role == "technique_author":
            prompts.append(prompt)
            return "invalid draft" if len(prompts) == 1 else json.dumps(payload)
        return runner(role, prompt, tools)

    state.service.runner = run
    _, rendered = state.service.get("JWT")
    assert body.strip() in rendered
    assert "invalid draft" in prompts[1] and "Invalid JSON" in prompts[1]
    from modules.techniques.roles import SYSTEM

    assembled = SYSTEM + prompts[1]
    assert "return JSON metadata and a Markdown body" in assembled
    assert "The body is Markdown without YAML frontmatter" in assembled
    assert "Return Markdown only" not in assembled
    assert [role for role, _, _ in state.calls].count("technique_critic") == 1
    assert len(list((tmp_path / "techniques" / "builds").glob("*/*.json"))) == 1


def test_mixed_failures_report_final_stage_and_redact_critic_reason(scripted_service, tmp_path):
    from modules.techniques.debug import TechniqueExports

    state = scripted_service
    runner = state.service.runner
    attempts = []
    state.service.private_values = ("internal-target.example",)
    state.service.exports = TechniqueExports(str(tmp_path))

    def run(role, prompt, tools):
        if role == "technique_author":
            attempts.append(prompt)
            return "bad JSON" if len(attempts) == 1 else runner(role, prompt, tools)
        if role == "technique_critic":
            return critique_result(reason="Fix internal-target.example controls")
        return runner(role, prompt, tools)

    state.service.runner = run
    with pytest.raises(ValueError, match="3 attempts at critique") as error:
        state.service.get("JWT")
    assert "internal-target.example" not in str(error.value)
    assert "[operation-specific]" in str(error.value)
    assert "Fix [operation-specific] controls" in attempts[2]
    assert not state.service.repository.revisions()
    assert len(list((tmp_path / "techniques" / "builds").glob("*/*.json"))) == 3


@pytest.mark.parametrize("category,expected_authors,expected_research", [
    ("authored_content", 2, 1),
    ("request_coverage", 2, 1),
    ("source_attribution", 1, 1),
    ("research_coverage", 1, 2),
])
def test_critique_routes_repairs_to_owning_stage(scripted_service, category, expected_authors, expected_research):
    state = scripted_service
    original = state.service.runner
    critiques = []
    selections = []

    def research(request):
        state.research_calls.append(request)
        state.source_urls = [f"https://vendor.example/source-{len(state.research_calls)}"]
        return [{"url": state.source_urls[0], "title": "Advisory", "text": "Primary support"}]

    def runner(role, prompt, tools):
        if role == "technique_critic":
            critiques.append(prompt)
            return critique_result(len(critiques) > 1, category, "Fix coverage", state.source_urls)
        if role == "technique_source_assessor":
            selections.append(prompt)
        return original(role, prompt, tools)

    state.service.research = research
    state.service.runner = runner
    revision, markdown = state.service.get("JWT handling")
    assert revision and "Check assumptions" in markdown
    assert len([row for row in state.calls if row[0] == "technique_author"]) == expected_authors
    assert len(state.research_calls) == expected_research
    assert len(critiques) == 2
    if category == "source_attribution":
        assert len(selections) == 1
        assert any(role == "technique_attribution_repair" and "Fix coverage" in prompt
                   for role, prompt, _ in state.calls)
    if category in {"source_attribution", "research_coverage"}:
        assert critiques[0].split("# Guidance")[1] == critiques[1].split("# Guidance")[1]


@pytest.mark.parametrize("category,message", [
    ("source_attribution", "after one repair"),
    ("research_coverage", "Research coverage exhausted"),
])
def test_owned_repair_exhaustion_is_explicit_without_reauthoring(scripted_service, category, message):
    state = scripted_service
    original = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_critic":
            return critique_result(False, category, "Unsupported requirement", state.source_urls)
        return original(role, prompt, tools)

    state.service.runner = runner
    with pytest.raises(ValueError, match=message):
        state.service.get("JWT handling")
    assert [role for role, _, _ in state.calls].count("technique_author") == 1
    assert not state.service.repository.revisions()


def test_attribution_repair_cannot_replace_coverage(scripted_service):
    state = scripted_service
    original = state.service.runner
    repairs = []

    def runner(role, prompt, tools):
        if role == "technique_critic":
            return critique_result(False, "source_attribution", "Inaccurate support", state.source_urls)
        if role == "technique_attribution_repair":
            repairs.append(prompt)
            return json.dumps({"attributions": [{"url": state.source_urls[0], "reason": "Fixed"}],
                               "coverage": [{"requirement_id": "method"}]})
        return original(role, prompt, tools)

    state.service.runner = runner
    with pytest.raises(ValueError, match="Attribution repair invalid after one correction"):
        state.service.get("JWT handling")
    assert len(repairs) == 2
    assert len(state.research_calls) == 1
    assert not state.service.repository.revisions()


def test_repairs_on_third_author_attempt_still_receive_exact_final_critique(scripted_service):
    state = scripted_service
    original = state.service.runner
    authors = []
    critics = []

    def runner(role, prompt, tools):
        if role == "technique_author":
            authors.append(prompt)
            if len(authors) < 3:
                return "invalid JSON"
        if role == "technique_critic":
            critics.append(prompt)
            return critique_result(len(critics) > 1, "source_attribution", "Clarify", state.source_urls)
        return original(role, prompt, tools)

    state.service.runner = runner
    _, markdown = state.service.get("JWT handling")
    assert len(authors) == 3 and len(critics) == 2
    assert "Check assumptions" in markdown


def test_controller_ignores_author_provenance_and_critiques_assembled_metadata(scripted_service):
    state = scripted_service
    original = state.service.runner
    critiques = []

    def runner(role, prompt, tools):
        result = original(role, prompt, tools)
        if role == "technique_author":
            payload = json.loads(result)
            payload["metadata"]["sources"] = [{"url": "invalid author provenance"}]
            payload["metadata"]["source_coverage"] = "Invented coverage"
            return json.dumps(payload)
        if role == "technique_critic":
            critiques.append(prompt)
        return result

    state.service.runner = runner
    _, markdown = state.service.get("JWT handling")
    assert "invalid author provenance" not in markdown and "Invented coverage" not in markdown
    assert "selection_reason: Primary applicable advisory" in markdown
    assert "selection_reason: Primary applicable advisory" in critiques[0]


def test_explicit_component_requirements_reach_initial_research_and_assembled_roles(scripted_service):
    from modules.techniques.coverage import Requirement
    from modules.techniques.roles import SYSTEM

    state = scripted_service
    _, markdown = state.service.get("Token review", requirements=[
        Requirement(id="method", description="Preserve verification and requested CVE version analysis"),
    ])
    assert "requested CVE version analysis" in state.research_calls[0]
    for role in ("technique_author", "technique_critic"):
        assembled = SYSTEM + next(prompt for selected, prompt, _ in state.calls if selected == role)
        assert "requested CVE version analysis" in assembled
        assert "advisory" in assembled and "untrusted data" in assembled
    assert "requested CVE version analysis" in markdown
