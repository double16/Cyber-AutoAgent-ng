from unittest.mock import Mock

import pytest

from modules.agents import prompt_context as context


@pytest.fixture(autouse=True)
def predictable_estimator(monkeypatch):
    def estimate(_model, messages=None, system_prompt=None, tool_specs=None, extra_content=None):
        if extra_content is not None:
            return len(extra_content)
        return len(messages[0]["content"][0]["text"]) + len(system_prompt or "") + len(str(tool_specs or ""))
    monkeypatch.setattr(context, "estimate_prompt_tokens", estimate)


def fit(prompt, limit=1000, system="", schemas=None, output=100):
    log = Mock()
    value = context.fit_task_prompt(prompt, system, schemas or [], "model", limit, output, log)
    return value, log.call_args.args[0]


def test_small_prompt_is_unchanged():
    text = "instructions\n## Task\nimmutable\n## Task history\nrecent\n"
    result, event = fit(text)
    assert result == text
    assert event["reductions"] == []
    assert event["target_tokens"] == 720
    assert event["section_tokens"]["## Task"] > 0


@pytest.mark.parametrize("subject", ["http://example.test:8080", "tcp://example.test:22", "/src/application"])
def test_optional_context_reduced_before_required_task(subject):
    required = f"## Task\n{subject} acceptance immutable\n"
    result, event = fit("instructions\n## Task history\n" + "h" * 700 + "\n"
                        + "## Eligible Semantic Memories\n" + "m" * 700 + "\n" + required)
    assert required in result
    assert event["reductions"] == ["Task history", "Eligible Semantic Memories"]
    assert event["estimated_tokens_after"] <= event["target_tokens"]


def test_dynamic_catalog_retains_every_tool_name():
    catalog = "## Candidate optional tools\noptional_tools[2]{name,description}:\n"
    catalog += "  new_mcp_tool," + "x" * 800 + "\n  another_tool," + "y" * 800 + "\n"
    result, event = fit("## Task\nimmutable\n" + catalog)
    assert "new_mcp_tool" in result and "another_tool" in result
    assert "descriptions" in event["reductions"][0]
    assert "immutable" in result


def test_remaining_headroom_does_not_reject_input_that_fits():
    result, event = fit("r" * 810)
    assert len(result) == 810
    assert event["target_tokens"] < event["estimated_tokens_after"] <= event["input_limit"]


@pytest.mark.parametrize("system,schemas,output", [("s" * 900, [], 100), ("", [{"schema": "s" * 900}], 100),
                                                   ("", [], 1000)])
def test_required_overflow_accounts_for_system_schemas_and_output(system, schemas, output):
    log = Mock()
    with pytest.raises(context.PromptInputTooLarge, match="PROMPT_REQUIRED_INPUT_TOO_LARGE"):
        context.fit_task_prompt("## Task\nimmutable", system, schemas, "model", 1000, output, log)
    assert log.call_count == 1


def test_optional_evaluator_narrative_is_removed_whole():
    mandatory = "## Frozen acceptance results\nimmutable evidence refs\n"
    prompt = mandatory + "## Task worker final context\n" + "large narration" * 300
    event = Mock()
    result = context.fit_task_prompt(
        prompt, "system", [{"output_schema": "schema"}], "model", 1000, 100, event,
        optional_headings=("Task worker final context",),
    )
    assert mandatory in result and "large narration" not in result
    assert event.call_args.args[0]["reductions"] == ["Task worker final context"]
