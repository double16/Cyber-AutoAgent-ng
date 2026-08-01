from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).parents[1] / "src" / "modules" / "operation_plugins"
MODULES = ("code_security", "context_navigator", "ctf", "threat_emulation", "web", "web_recon")
PROMPT_SECTIONS = (
    "<operation_intent>",
    "<access_and_scope>",
    "<module_execution_policy>",
    "<evidence_policy>",
    "<prohibited_actions>",
)


@pytest.mark.parametrize("module", MODULES)
def test_execution_prompt_has_module_policy_sections_without_workflow_ownership(module):
    prompt = (PLUGIN_ROOT / module / "execution_prompt.md").read_text(encoding="utf-8")

    for section in PROMPT_SECTIONS:
        assert section in prompt
    assert "store_finding" in prompt
    assert "store_observation" in prompt
    assert "Budget >" not in prompt
    assert "If YES: stop" not in prompt
    assert "Phase 1:" not in prompt
    assert "Python owns" not in prompt


@pytest.mark.parametrize("module", MODULES)
def test_termination_policy_is_evidence_based_and_budget_independent(module):
    policy = (PLUGIN_ROOT / module / "termination_policy.md").read_text(encoding="utf-8")
    normalized = " ".join(policy.split())

    assert "Module completion criteria" in policy
    assert "use `done` only" in normalized.lower()
    assert "use `partial_failure`" in normalized.lower()
    assert "use `blocked` only" in normalized.lower()
    assert "budget" in normalized.lower()
    assert "never a completion requirement" in normalized or "do not require arbitrary budget use" in normalized
    assert "95%" not in policy


def test_code_security_access_is_repository_only_and_static():
    prompt = (PLUGIN_ROOT / "code_security" / "execution_prompt.md").read_text(encoding="utf-8")

    assert "target is only the repository or codebase path" in prompt
    assert "Do not run the target application" in prompt
    assert "Do not access network targets" in prompt


def test_context_navigator_access_is_limited_to_granted_post_access_context():
    prompt = (PLUGIN_ROOT / "context_navigator" / "execution_prompt.md").read_text(encoding="utf-8")

    assert "Use only the post-access channel" in prompt
    assert "Do not pivot to another host" in prompt
    assert "Do not read sensitive data content" in prompt


@pytest.mark.parametrize("module", ("ctf", "web", "web_recon"))
def test_external_modules_explicitly_require_network_only_target_access(module):
    prompt = (PLUGIN_ROOT / module / "execution_prompt.md").read_text(encoding="utf-8")

    assert "network" in prompt
    assert "Local filesystem" in prompt
    assert "operation artifacts and tooling" in prompt


def test_threat_emulation_access_and_cleanup_are_explicit():
    prompt = (PLUGIN_ROOT / "threat_emulation" / "execution_prompt.md").read_text(encoding="utf-8")

    assert "explicitly authorized" in prompt
    assert "available tools, credentials, sessions" in prompt.lower()
    assert "Cleanup is explicit work" in prompt
    assert "never assume end-of-operation cleanup" in prompt


def test_web_recon_preserves_non_exploitation_and_three_endpoint_policy():
    prompt = (PLUGIN_ROOT / "web_recon" / "execution_prompt.md").read_text(encoding="utf-8")
    policy = (PLUGIN_ROOT / "web_recon" / "termination_policy.md").read_text(encoding="utf-8")
    normalized_prompt = " ".join(prompt.split())
    normalized_policy = " ".join(policy.split())

    assert "non-exploitative" in prompt
    assert "Do not weaponize weaknesses" in prompt
    assert "test credentials supplied by the user" in normalized_prompt
    assert "Do not guess credentials" in prompt
    assert "do not create a separate consolidation or reporting phase" in normalized_prompt
    assert "not a request for a separate consolidation" in normalized_policy
    assert "at least three endpoints" in policy


@pytest.mark.parametrize("module", ("web", "web_recon"))
def test_web_modules_preserve_explicit_url_scheme_and_port_scope(module):
    prompt = (PLUGIN_ROOT / module / "execution_prompt.md").read_text(encoding="utf-8")
    normalized = " ".join(prompt.split())

    assert "URL with a scheme and explicit port" in normalized
    assert "exact scheme, host, and port boundary" in normalized
    assert "enumerate other ports on the same host" in normalized
    assert "preserve explicit response status evidence" in normalized
    assert "Bare silent requests with no captured status are not sufficient evidence of absence" in normalized
    assert "HTTP" not in normalized


def test_ctf_policy_requires_artifact_backed_flag_without_worker_termination():
    prompt = (PLUGIN_ROOT / "ctf" / "execution_prompt.md").read_text(encoding="utf-8")
    policy = (PLUGIN_ROOT / "ctf" / "termination_policy.md").read_text(encoding="utf-8")

    assert "call `discover_flag_candidates` with that artifact" in prompt
    assert "do not\n  claim or perform task, phase, or operation termination" in prompt
    assert "Do not\ndeclare the overall operation successful" in policy
