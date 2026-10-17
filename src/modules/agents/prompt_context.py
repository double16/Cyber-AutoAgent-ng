"""Bound optional task prompt context before making a model request."""

import re
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from modules.config.models.factory import get_model_id_from_agent
from modules.handlers.conversation_budget import (
    _get_agent_input_context,
    estimate_prompt_tokens,
    get_agent_prompt_limits,
)


class PromptInputTooLarge(ValueError):
    """Required prompt content cannot fit in the model's input allowance."""


def fit_task_prompt(
    prompt: str,
    system_prompt: str,
    tool_specs: list[dict],
    model_id: str,
    context_window: int,
    output_tokens: int,
    log: Callable[[dict], None],
    optional_headings: tuple[str, ...] = ("Task history", "Eligible Semantic Memories"),
    *,
    estimate_input: Callable[[str], int] | None = None,
) -> str:
    """Reduce optional sections in a fixed order; keep required content byte-for-byte."""

    sections = re.split(r"(?m)(?=^## )", prompt)
    input_limit = max(0, context_window - output_tokens)
    target = int(input_limit * 0.8)

    def estimate(text: str) -> int:
        if estimate_input is not None:
            return estimate_input(text)
        return estimate_prompt_tokens(
            model_id, messages=[{"role": "user", "content": [{"text": text}]}],
            system_prompt=system_prompt, tool_specs=tool_specs,
        )

    before = estimate(prompt)
    section_tokens = {
        section.split("\n", 1)[0] if section.startswith("## ") else "instructions":
            estimate_prompt_tokens(model_id, extra_content=section)
        for section in sections
    }
    reductions = []
    # History and eligible memories are advisory; selected memories are retained separately.
    for heading in optional_headings:
        if estimate("".join(sections)) <= target:
            break
        for index, section in enumerate(sections):
            if section.startswith(f"## {heading}\n"):
                sections[index] = f"## {heading}\nOmitted to fit input capacity.\n\n"
                reductions.append(heading)
    if estimate("".join(sections)) > target:
        for index, section in enumerate(sections):
            if section.startswith(("## Available core tools\n", "## Candidate optional tools\n",
                                   "## Candidate shell commands\n", "## Controller-supplied task tools\n")):
                lines = section.splitlines()
                # Catalog rows use controller-generated TOON: preserve every runtime name.
                sections[index] = "\n".join(
                    line.split(",", 1)[0] if line.startswith("  ") else (
                        re.sub(r"\{[^}]+\}", "{command}" if "shell_commands[" in line else "{name}", line)
                        if re.match(r"\w+\[\d+\]", line) else line
                    ) for line in lines
                ) + "\n\n"
                reductions.append(lines[0].removeprefix("## ") + " descriptions")
    fitted = "".join(sections)
    after = estimate(fitted)
    log({"estimated_tokens_before": before, "estimated_tokens_after": after,
         "input_limit": input_limit, "target_tokens": target,
         "section_tokens": section_tokens, "reductions": reductions})
    if after > input_limit:
        raise PromptInputTooLarge(
            f"PROMPT_REQUIRED_INPUT_TOO_LARGE: required input estimates {after} tokens; "
            f"available input is {input_limit} after reserving {output_tokens} output tokens."
        )
    return fitted


def attach_live_prompt_fitter(
    agent: Any,
    prompt: str,
    log: Callable[[dict], None],
    optional_headings: tuple[str, ...],
) -> None:
    """Fit only this invocation's request text against the SDK's final live model input."""

    current_prompt = prompt

    def fit(live_agent: Any) -> None:
        nonlocal current_prompt
        context = _get_agent_input_context(live_agent)
        context_limit, output_tokens = get_agent_prompt_limits(live_agent)
        if context_limit is None:
            return
        for message_index, message in enumerate(context.messages or []):
            if message.get("role") != "user":
                continue
            for block_index, block in enumerate(message.get("content", [])):
                if block.get("text") != current_prompt:
                    continue
                model_id = get_model_id_from_agent(live_agent)

                def estimate(candidate: str) -> int:
                    messages = deepcopy(context.messages)
                    messages[message_index]["content"][block_index]["text"] = candidate
                    return estimate_prompt_tokens(
                        model_id, messages, context.system_prompt, context.tool_specs,
                    )

                fitted = fit_task_prompt(
                    current_prompt, context.system_prompt, context.tool_specs or [], model_id,
                    context_limit, output_tokens, log, optional_headings, estimate_input=estimate,
                )
                block["text"] = fitted
                current_prompt = fitted
                return

    agent._cyber_prompt_fitter = fit
