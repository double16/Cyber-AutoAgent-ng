import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from modules.techniques.documents import TechniqueDocument


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
    assert state.research_calls == ["JWT handling"]
    assert [role for role, _, _ in state.calls] == ["technique_judge", "technique_author", "technique_critic"]
    assert all(not tools for _, _, tools in state.calls)
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
    with pytest.raises(ValueError, match="authoritative"):
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
    with pytest.raises(ValueError, match="source metadata"):
        state.service.get("JWT")
    assert not state.service.repository.revisions()


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
            return json.dumps({"approved": False, "reason": "Fix internal-target.example controls"})
        return runner(role, prompt, tools)

    state.service.runner = run
    with pytest.raises(ValueError, match="3 attempts at critique") as error:
        state.service.get("JWT")
    assert "internal-target.example" not in str(error.value)
    assert "[operation-specific]" in str(error.value)
    assert "Fix [operation-specific] controls" in attempts[2]
    assert not state.service.repository.revisions()
    assert len(list((tmp_path / "techniques" / "builds").glob("*/*.json"))) == 3
