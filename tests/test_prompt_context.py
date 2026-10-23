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


@pytest.mark.parametrize("window,guidance,generation", [
    (8000, 800, 8192), (40000, 4000, 17024), (48000, 4096, 17408), (200000, 4096, 17408),
])
def test_builder_budget_separates_guidance_and_generation(window, guidance, generation):
    budget = context.TaskPromptBudget.resolve(window, 8192)
    assert budget.guidance_tokens == guidance
    assert budget.desired_generation_tokens == generation
    assert str(guidance) in budget.instructions()
    assert str(4 * guidance) in budget.instructions()


@pytest.mark.parametrize("window,profile", [(0, 8192), (48000, 0), (-1, 1)])
def test_invalid_builder_budget(window, profile):
    with pytest.raises(ValueError, match="positive"):
        context.TaskPromptBudget.resolve(window, profile)


def test_builder_generation_cap_and_explicit_ceiling():
    assert context.TaskPromptBudget.resolve(200000, 64000).desired_generation_tokens == 32768
    assert context.TaskPromptBudget.resolve(48000, 8192, 6000).desired_generation_tokens == 6000
    assert context.TaskPromptBudget.resolve(48000, 8192, 0).desired_generation_tokens == 17408


def test_builder_fit_preserves_discoveries_and_reduces_reasoning_after_optional_context():
    budget = context.TaskPromptBudget.resolve(8000, 8192)
    evidence = "## Retained inspection evidence\n" + "e" * 4500 + "\n"
    history = "## Task history\n" + "h" * 5000 + "\n"
    log = Mock()
    fitted, generation = budget.fit("## Task\nassigned\n" + evidence + history, "system", [], "model", log)
    assert evidence in fitted
    assert "h" * 5000 not in fitted
    assert budget.guidance_tokens + 1024 <= generation < budget.desired_generation_tokens
    assert log.call_args.args[0]["generation_allowance"] == generation
    assert log.call_args.args[0]["estimated_input_tokens"] + generation + budget.safety_tokens <= 8000


def test_builder_fit_counts_system_and_schema_and_rejects_impossible_capacity():
    budget = context.TaskPromptBudget.resolve(8000, 8192)
    log = Mock()
    with pytest.raises(context.PromptInputTooLarge):
        budget.fit("## Task\n" + "x" * 2000, "s" * 2000, [{"schema": "z" * 4000}], "model", log)
    with pytest.raises(context.PromptInputTooLarge, match="output ceiling"):
        context.TaskPromptBudget.resolve(48000, 8192, 1000).fit("task", "", [], "model", log)


def test_builder_does_not_restore_advisory_history_when_reducing_reasoning():
    budget = context.TaskPromptBudget.resolve(40000, 8192)
    prompt = "## Retained inspection evidence\n" + "e" * 23000 + "\n## Task history\n" + "h" * 2000
    fitted, generation = budget.fit(prompt, "system", [], "model", Mock())
    assert "e" * 23000 in fitted
    assert "h" * 2000 not in fitted
    assert budget.guidance_tokens + 1024 <= generation < budget.desired_generation_tokens


def test_builder_guidance_validation_counts_only_prompt():
    budget = context.TaskPromptBudget.resolve(8000, 8192)
    budget.validate({"prompt": "x" * 800, "tools": ["related"] * 1000}, "model")
    with pytest.raises(ValueError, match="concise complete replacement"):
        budget.validate({"prompt": "x" * 801}, "model")


@pytest.mark.parametrize("window,answer,generation", [
    (8000, 400, 4096), (40000, 2000, 9024), (48000, 2048, 9216),
])
def test_critic_budget_separates_answer_and_reasoning(window, answer, generation):
    budget = context.TaskPromptBudget.resolve(window, 4096, role="task_prompt_critic")
    assert budget.guidance_tokens == answer
    assert budget.desired_generation_tokens == generation
    assert "Critic output budget" in budget.instructions()
    assert str(answer) in budget.instructions()
    assert "five issues" in budget.instructions()


def test_critic_budget_validates_complete_output_and_capacity():
    budget = context.TaskPromptBudget.resolve(40000, 4096, role="task_prompt_critic")
    budget.validate({"issues": [], "repairable": True}, "model")
    with pytest.raises(ValueError, match="concise complete replacement"):
        budget.validate({"issues": [{"message": "x" * 2001}], "repairable": True}, "model")
    evidence = "## Retained inspection evidence\n" + "e" * 15900
    fitted, generation = budget.fit(evidence, "system", [{"schema": "output"}], "model", Mock())
    assert evidence in fitted
    assert generation == 9024
    with pytest.raises(context.PromptInputTooLarge, match="output ceiling"):
        context.TaskPromptBudget.resolve(40000, 4096, 1000, role="task_prompt_critic").fit(
            "task", "", [], "model", Mock(),
        )
    with pytest.raises(ValueError, match="Unsupported"):
        context.TaskPromptBudget.resolve(40000, 4096, role="unknown")
