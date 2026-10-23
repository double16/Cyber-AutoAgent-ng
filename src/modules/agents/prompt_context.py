"""Bound optional task prompt context before making a model request."""

import json
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from modules.agents.review_contracts import REVIEW_CHECKS, REVIEW_ROLES
from modules.config.models.factory import get_model_id_from_agent
from modules.config.models.generation_budget import GenerationRequest, allocate_generation, set_generation_tokens
from modules.handlers.conversation_budget import (
    _get_agent_input_context,
    estimate_prompt_tokens,
    get_agent_prompt_limits,
)


class PromptInputTooLarge(ValueError):
    """Required prompt content cannot fit in the model's input allowance."""

    def __init__(self, message: str, fitted_prompt: str | None = None):
        super().__init__(message)
        self.fitted_prompt = fitted_prompt


@dataclass(frozen=True)
class ReviewBudget:
    """Separate task guidance from reasoning and serialized output capacity."""

    context_window: int
    guidance_tokens: int
    desired_generation_tokens: int
    safety_tokens: int
    output_ceiling: int | None
    role: str = "task_prompt_builder"

    @classmethod
    def resolve(
        cls, context_window: int, profile_tokens: int, ceiling: int | None = None,
        *, role: str = "task_prompt_builder", required_answer_tokens: int = 0,
    ) -> "ReviewBudget":
        if context_window <= 0 or profile_tokens <= 0:
            raise ValueError("Context window and profile tokens must be positive")
        if role not in REVIEW_ROLES | {"task_prompt_builder"}:
            raise ValueError(f"Unsupported task prompt budget role: {role}")
        guidance = (min(4096, context_window // 10) if role == "task_prompt_builder"
                    else min(2048, context_window // 20))
        guidance = max(guidance, required_answer_tokens)
        desired = min(32768, max(profile_tokens, 4 * guidance + 1024))
        if ceiling is not None and ceiling > 0:
            desired = min(desired, ceiling)
        return cls(context_window, guidance, desired, max(512, context_window // 50), ceiling, role)

    def instructions(self) -> str:
        if self.role in REVIEW_ROLES - {"task_prompt_critic"}:
            authority = ("The operation objective defines plan coverage." if self.role == "plan_critic" else
                         "The operation objective is background and cannot expand the assigned review unit.")
            return (
                "## Review output budget\n"
                f"Keep the complete JSON decision within {self.guidance_tokens} estimated tokens. "
                f"Decide each applicable check once: {REVIEW_CHECKS[self.role]}. {authority} "
                "Preserve actionable semantic feedback: identify the deficient criterion, field, or claim, "
                "why the available evidence is insufficient or contradictory, and the required correction. "
                "Return concise global feedback; include every schema-required record decision. "
                "Reference evidence instead of copying it. Do not reopen settled checks, execute assessment "
                "work, or write an actor replacement. Return one complete result matching the supplied schema.\n\n"
            )
        if self.role == "task_prompt_critic":
            return (
                "## Critic output budget\n"
                f"Keep the complete serialized critique within {self.guidance_tokens} estimated tokens. "
                "Review once and return only concrete defects: at most five issues, each message at most "
                "500 characters. Approve with all five checks passing and issue=null. Do not copy supporting evidence, "
                "write a replacement draft, or reopen resolved checks. Return one complete JSON object.\n\n"
            )
        return (
            "## Builder output budget\n"
            f"Keep the prompt field within {self.guidance_tokens} estimated tokens "
            f"(aim below {4 * self.guidance_tokens} characters). "
            "This limit covers task-specific guidance only; selections are separate. "
            "Use supplied operation discoveries to customize concrete actions, decision points, expected artifacts, "
            "and unresolved questions. Reference evidence instead of copying it. "
            "Python appends authoritative policies, acceptance, persistence rules, and evidence context. "
            "Do not repeat them or draft multiple alternatives. Return one complete JSON object.\n\n"
        )

    def validate(self, payload: dict[str, Any], model_id: str) -> None:
        content = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                   if self.role in REVIEW_ROLES else payload["prompt"])
        tokens = estimate_prompt_tokens(model_id, extra_content=content)
        if tokens > self.guidance_tokens:
            raise ValueError(
                f"Task prompt output estimates {tokens} tokens; limit is {self.guidance_tokens}. "
                "Return a concise complete replacement; preserve task-specific discoveries and relevant decisions."
            )

    def fit(
        self, prompt: str, system_prompt: str, tool_specs: list[dict], model_id: str,
        log: Callable[[dict], None],
        optional_headings: tuple[str, ...] = ("Task history", "Eligible Semantic Memories"),
    ) -> tuple[str, int]:
        minimum = self.guidance_tokens + 1024
        if self.desired_generation_tokens < minimum:
            raise PromptInputTooLarge("PROMPT_REQUIRED_INPUT_TOO_LARGE: output ceiling cannot fit task prompt answer")
        prompt = self.instructions() + prompt
        args = (prompt, system_prompt, tool_specs, model_id, self.context_window)
        # Reduce advisory history and catalog descriptions before reducing reasoning. Keep discoveries.
        try:
            fitted = fit_task_prompt(*args, self.desired_generation_tokens + self.safety_tokens, log, optional_headings)
        except PromptInputTooLarge as error:
            # Preserve the first pass's reductions instead of restoring advisory context at reasoning's expense.
            reduced_args = (error.fitted_prompt or prompt, system_prompt, tool_specs, model_id, self.context_window)
            fitted = fit_task_prompt(*reduced_args, minimum + self.safety_tokens, log, optional_headings)
        input_tokens = estimate_prompt_tokens(
            model_id, messages=[{"role": "user", "content": [{"text": fitted}]}],
            system_prompt=system_prompt, tool_specs=tool_specs,
        )
        generation = allocate_generation(GenerationRequest(
            self.context_window, input_tokens, self.desired_generation_tokens,
            self.guidance_tokens, 1024, self.output_ceiling,
        )).generation_tokens
        log({"context_window": self.context_window, "guidance_limit": self.guidance_tokens,
             "estimated_input_tokens": input_tokens, "generation_allowance": generation,
             "desired_generation_allowance": self.desired_generation_tokens, "safety_tokens": self.safety_tokens,
             "output_ceiling": self.output_ceiling})
        return fitted, generation


TaskPromptBudget = ReviewBudget  # Compatibility for existing builder policy callers.


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
                                   "## Candidate shell commands\n", "## Controller-supplied task tools\n",
                                   "## Automatically supplied core tools\n", "## Automatically supplied task tools\n",
                                   "## Selectable optional tools\n")):
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
            f"available input is {input_limit} after reserving {output_tokens} output tokens.",
            fitted_prompt=fitted,
        )
    return fitted


def attach_live_prompt_fitter(
    agent: Any,
    prompt: str,
    log: Callable[[dict], None],
    optional_headings: tuple[str, ...],
    *,
    reserve_safety: bool = False,
    minimum_output_tokens: int | None = None,
) -> None:
    """Fit only this invocation's request text against the SDK's final live model input."""

    current_prompt = prompt

    def fit(live_agent: Any) -> None:
        nonlocal current_prompt
        context = _get_agent_input_context(live_agent)
        context_limit, output_tokens = get_agent_prompt_limits(live_agent)
        if context_limit is None:
            return
        if reserve_safety and not isinstance(getattr(live_agent.model, "_cyber_preferred_output_tokens", None), int):
            context_limit -= max(512, context_limit // 50)
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

                try:
                    fitted = fit_task_prompt(
                        current_prompt, context.system_prompt, context.tool_specs or [], model_id,
                        context_limit, output_tokens, log, optional_headings, estimate_input=estimate,
                    )
                except PromptInputTooLarge as error:
                    if minimum_output_tokens is None or minimum_output_tokens >= output_tokens:
                        raise
                    fitted = fit_task_prompt(
                        error.fitted_prompt or current_prompt, context.system_prompt, context.tool_specs or [],
                        model_id, context_limit, minimum_output_tokens, log, optional_headings,
                        estimate_input=estimate,
                    )
                    generation = min(output_tokens, context_limit - estimate(fitted))
                    set_generation_tokens(live_agent.model, generation)
                    log({"generation_allowance": generation, "minimum_generation_allowance": minimum_output_tokens})
                block["text"] = fitted
                current_prompt = fitted
                return

    agent._cyber_prompt_fitter = fit
