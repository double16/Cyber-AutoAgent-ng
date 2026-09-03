import json

from modules.prompts import factory


def test_langfuse_helpers_and_cache_cover_disabled_expired_and_chat_prompts(monkeypatch):
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("ENABLE_LANGFUSE_PROMPTS", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    factory._LF_CACHE.clear()
    factory._lf_cache_set("name", "label", {"prompt": "cached"})
    assert factory._lf_cache_get("name", "label") == {"prompt": "cached"}
    factory._LF_CACHE[factory._lf_ck("name", "label")]["ts"] = 0
    assert factory._lf_cache_get("name", "label") is None
    monkeypatch.setattr(
        factory,
        "_lf_get_prompt",
        lambda _name, _label: {"prompt": [{"content": "first"}, {"content": "second"}]},
    )
    assert factory._lf_resolve_prompt_by_name("cyber/test", label="test") == "first\nsecond"
    assert factory._lf_auth_header().startswith("Basic ")


def test_overlay_rendering_handles_directives_expiry_and_invalid_json(tmp_path):
    config = {"base_dir": str(tmp_path), "target_name": "target"}
    path = factory._get_overlay_file(config, "OP1")
    assert path is not None
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "payload": {"directives": ["focus evidence"], "max_retries": 2},
                "origin": "critic",
                "budget_progress": 10,
                "expires_after_progress": 20,
            }
        ),
        encoding="utf-8",
    )
    block = factory._render_overlay_block(config, "OP1", 15)
    assert "origin=critic" in block
    assert "focus evidence" in block
    assert "max_retries: 2" in block
    assert factory._render_overlay_block(config, "OP1", 30) == ""
    assert not path.exists()

    path.write_text("not-json", encoding="utf-8")
    assert factory._load_overlay_json(path) is None
    assert not path.exists()


def test_module_loader_inherits_prompts_and_applies_base_allowlist(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    parent = root / "parent"
    child = root / "child"
    for directory in (parent, child):
        (directory / "tools").mkdir(parents=True)
    (parent / "module.yaml").write_text("name: parent\n", encoding="utf-8")
    (parent / "execution_prompt.md").write_text("parent prompt", encoding="utf-8")
    (parent / "tools" / "shared.py").write_text("", encoding="utf-8")
    (child / "module.yaml").write_text("extend: [parent]\ntools: [child_tool, shared]\n", encoding="utf-8")
    (child / "tools" / "child_tool.py").write_text("", encoding="utf-8")
    (child / "tools" / "ignored.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("CYBER_PLUGIN_PATH", str(root))
    monkeypatch.setattr(factory, "_lf_enabled", lambda: False)
    loader = factory.ModulePromptLoader()

    assert loader.load_module_execution_prompt("child") == "parent prompt"
    tools, missing = loader.discover_module_tools("child")
    assert [tool.rsplit("/", 1)[-1] for tool in tools] == ["child_tool.py", "shared.py"]
    assert missing == []


def test_system_prompt_and_role_prompt_cover_template_fallbacks(monkeypatch):
    monkeypatch.setattr(factory, "load_prompt_template", lambda _name: "")
    prompt = factory.get_system_prompt("target", "objective", "OP")
    assert "System prompt template missing" in prompt

    executor = "before <tools_and_capabilities>tools</tools_and_capabilities> after"
    planner = factory.get_role_system_prompt(executor, "plan_creator")
    assert "tools" not in planner
    assert "role_boundary" in planner
    assert factory.get_role_system_prompt(planner, "plan_creator") == planner


def test_report_formatting_helpers_cover_locations_markers_and_tool_filtering():
    evidence = [
        {"category": "finding", "severity": "high", "content": "first"},
        {"category": "finding", "severity": "high", "content": "second", "location": "/two"},
        {"category": "finding", "severity": "high", "content": "third", "location": "/three"},
        {
            "category": "finding",
            "severity": "LOW",
            "validation_status": "verified",
            "parsed": {
                "vulnerability": "Reflected XSS",
                "where": "/search",
                "impact": "script execution",
                "evidence": "artifact:proof",
                "steps": "open 1. inject 2. observe",
                "remediation": "encode output",
            },
        },
        {"category": "observation", "content": "service banner"},
    ]
    table = factory.generate_findings_summary_table(evidence)
    assert "Multiple" in table
    assert "Reflected XSS" in table
    report = factory.format_evidence_for_report(evidence)
    assert "**Status:** Verified" in report
    assert "Reproduction Steps" in report
    assert "Observation" in report
    assert factory.safe_truncate("abcdef", 3) == "abc"
    assert factory.safe_truncate("abcdef", 5) == "ab..."
    assert factory._indent_text("one\ntwo", 2) == "  one\n  two"
    assert factory._extract_domain_lens("DOMAIN_LENS:\noverview: focus\nanalysis: assess\n</x>") == {
        "overview": "focus",
        "analysis": "assess",
    }
    assert factory.format_tools_summary(["shell", "http_request", "http_request", "store_finding"]) == (
        "- shell\n- http_request"
    )
    assert factory.format_tools_summary({"nmap:80": 2, "memory_list": 1}) == "- nmap"


def test_prompt_factory_remote_and_module_helpers_handle_missing_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(factory, "_lf_enabled", lambda: False)
    assert factory._lf_get_prompt("name", "label") is None
    assert factory._lf_create_prompt_version(name="name", prompt_text="text", label="label") is None
    assert factory._lf_resolve_template_text("unknown.md") == ""
    assert factory._get_overlay_file(None, "OP") is None
    assert factory._format_overlay_directives(["first", "", 2]) == ["first", "2"]
    assert factory._format_overlay_directives("directive") == ["directive"]
    assert factory._read_module_yaml_for_tags(tmp_path) == []
    assert factory.get_report_generation_prompt("target", "objective", "evidence", ["shell"]).startswith(
        "Generate a concise"
    )


def test_report_generation_prompt_substitutes_template_and_falls_back_on_template_error(monkeypatch):
    monkeypatch.setattr(
        factory,
        "load_prompt_template",
        lambda _name: "{{target}}|{{objective}}|{{evidence}}|{{tools_used}}",
    )
    assert factory.get_report_generation_prompt("host", "assess", "proof", ["shell"]) == "host|assess|proof|- shell"

    class BrokenTemplate:
        def replace(self, *_args, **_kwargs):
            raise RuntimeError("broken template")

    monkeypatch.setattr(factory, "load_prompt_template", lambda _name: BrokenTemplate())
    fallback = factory.get_report_generation_prompt("host", "assess", "proof")
    assert fallback.startswith("Generate a concise security assessment report")


def test_langfuse_http_helpers_cover_non_200_and_success_responses(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("ENABLE_LANGFUSE_PROMPTS", "true")

    class Response:
        def __init__(self, status, payload):
            self.status = status
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    responses = iter([Response(404, {}), Response(200, []), Response(201, {"version": 1})])
    monkeypatch.setattr(factory._urlreq, "urlopen", lambda *_args, **_kwargs: next(responses))
    factory._LF_CACHE.clear()
    assert factory._lf_get_prompt("missing", "production") is None
    assert factory._lf_get_prompt("not-a-dict", "production") is None
    assert factory._lf_create_prompt_version(name="name", prompt_text="text", label="production") == {"version": 1}

    module = tmp_path / "module"
    module.mkdir()
    (module / "module.yaml").write_text("name: demo\ncapabilities: [web:http, auth, 3]\n", encoding="utf-8")
    assert factory._read_module_yaml_for_tags(module) == ["module:demo", "capability:web", "capability:auth", "capability:3"]


def test_langfuse_seeding_and_successful_prompt_fetch(monkeypatch):
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("ENABLE_LANGFUSE_PROMPTS", "true")
    responses = iter([{"prompt": "remote prompt"}])
    monkeypatch.setattr(factory, "_lf_get_prompt", lambda *_args: next(responses))
    factory._LF_CACHE.clear()
    assert factory._lf_get_prompt("name", "label") == {"prompt": "remote prompt"}

    monkeypatch.setattr(factory, "_LF_SEEDED", False)
    monkeypatch.setattr(factory, "_LF_TEMPLATE_TO_NAME", {"system_prompt.md": "cyber/system"})
    monkeypatch.setattr(factory, "_lf_read_local_template", lambda _name: "local prompt")
    created = []
    monkeypatch.setattr(factory, "_lf_create_prompt_version", lambda **kwargs: created.append(kwargs) or {"ok": True})
    monkeypatch.setattr(factory, "_lf_get_prompt", lambda *_args: None)
    factory._lf_ensure_seeded()
    assert created[0]["name"] == "cyber/system"


def test_overlay_and_domain_lens_helpers_cover_unserializable_and_termination_values():
    class Unserializable:
        def __str__(self):
            return "fallback-value"

        def __repr__(self):
            raise TypeError("cannot represent")

    assert factory._format_overlay_directives({"payload": Unserializable()}) == [
        "payload: fallback-value"
    ]
    assert factory._extract_domain_lens("DOMAIN_LENS:\noverview: value\nnext_section:\nignored: value") == {
        "overview": "value"
    }


def test_prompt_loading_handles_remote_errors_and_local_missing(monkeypatch):
    monkeypatch.setattr(factory, "_lf_enabled", lambda: True)
    monkeypatch.setattr(factory, "_lf_ensure_seeded", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(factory, "_lf_resolve_template_text", lambda _name: "")
    assert factory.load_prompt_template("definitely_missing.md") == ""

    monkeypatch.setattr(factory, "_lf_enabled", lambda: False)
    assert factory._extract_domain_lens("") == {}
    assert factory._extract_domain_lens("plain text") == {}
    assert factory._lf_module_prompt_name("a/b", "invalid") == "cyber/module/a_b/execution_prompt"


def test_overlay_block_uses_note_for_empty_directives(tmp_path):
    config = {"base_dir": str(tmp_path), "target_name": "target"}
    path = factory._get_overlay_file(config, "OP2")
    assert path is not None
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"payload": {}, "note": "review", "budget_progress": 2}), encoding="utf-8")
    assert "review" in factory._render_overlay_block(config, "OP2", 3)
    assert factory._extract_domain_lens("DOMAIN_LENS:\noverview: \n") == {}
