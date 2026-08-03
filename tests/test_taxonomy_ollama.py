"""Opt-in live Ollama compatibility tests for taxonomy annotation responses."""

import pytest

from modules.agents.multi_agent_workflow import MultiAgentWorkflowController, extract_json_object


@pytest.mark.ollama
@pytest.mark.parametrize(
    ("candidate", "expected_cwe"),
    [
        (
            {
                "title": "Command Injection via ip_address parameter",
                "claim": "The /ping parameter executed `; cat /FLAG.txt`.",
                "observed_result": "The response contained the contents of /FLAG.txt.",
                "technique": "command_injection",
                "artifacts": ["artifact:artifacts/command-injection.txt"],
            },
            "CWE-78",
        ),
        (
            {
                "title": "SQL Injection in product search",
                "claim": "A quoted search term changed the database query result.",
                "observed_result": "A UNION payload returned controlled database output.",
                "technique": "sql_injection",
                "artifacts": ["artifact:artifacts/sql-injection.txt"],
            },
            "CWE-89",
        ),
        (
            {
                "title": "Stored cross-site scripting in profile name",
                "claim": "A script stored in the profile name executed for another user.",
                "observed_result": "The browser executed the stored script on profile view.",
                "technique": "stored_xss",
                "artifacts": ["artifact:artifacts/stored-xss.txt"],
            },
            "CWE-79",
        ),
        (
            {
                "title": "Unclassified operational observation",
                "claim": "The service returned a normal health-check response.",
                "observed_result": "No security behavior was observed.",
                "technique": "",
                "artifacts": ["artifact:artifacts/health-check.txt"],
            },
            None,
        ),
    ],
)
def test_ollama_taxonomy_annotation_returns_catalog_grounded_json(
    ollama_taxonomy_client,
    monkeypatch,
    candidate,
    expected_cwe,
):
    """Exercise the production prompt, parser, normalizer, and semantic validator."""

    monkeypatch.setenv("CYBER_TAXONOMY_OFFLINE", "true")
    client, model = ollama_taxonomy_client
    controller = object.__new__(MultiAgentWorkflowController)
    system_prompt, prompt = controller._taxonomy_annotation_prompt(candidate, "ollama-taxonomy-test")
    response = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        options={"temperature": 0},
        stream=False,
    )

    proposal = extract_json_object(response.message.content)
    controller._validate_taxonomy_annotation_proposal(proposal, candidate["artifacts"])

    cwe_ids = {mapping["id"] for mapping in proposal["cwe"]}
    if expected_cwe is None:
        assert not cwe_ids
    else:
        assert expected_cwe in cwe_ids
