"""One bounded recovery for explicitly truncated technique decisions."""

import logging
from copy import deepcopy
from dataclasses import replace

from strands.types.exceptions import MaxTokensReachedException

from modules.agents.native_structured import invoke_structured, retained_finalization_prompt
from modules.config.models.generation_budget import allocate_generation, model_generation_request, schema_answer_tokens
from modules.config.models.invocation import model_output_budget
from modules.handlers.conversation_budget import safe_estimate_tokens

logger = logging.getLogger(__name__)


def invoke_technique_structured(agent, prompt, schema, *, investigate=False, finalization_context="",
                                investigation_system_prompt=None):
    """Retry truncation once without replaying investigation or accepting partial output."""
    messages = deepcopy(agent.messages)
    try:
        return invoke_structured(
            agent, prompt, schema, investigate=investigate, finalization_context=finalization_context,
            investigation_system_prompt=investigation_system_prompt,
        )
    except MaxTokensReachedException as error:
        if investigate and not hasattr(error, "native_finalization_messages"):
            raise RuntimeError("Investigation truncated before finalization; evidence remains unresolved") from error
        tokens = safe_estimate_tokens(agent)
        if tokens is None:
            raise RuntimeError("Truncated technique decision has unknown input size") from error
        request = model_generation_request(agent.model, tokens, answer_tokens=schema_answer_tokens(
            schema.model_json_schema(), agent.model.config["model_id"],
        ))
        first = allocate_generation(request)
        retry = allocate_generation(replace(request, preferred_tokens=first.generation_tokens * 2))
        if retry.generation_tokens <= first.generation_tokens:
            raise RuntimeError("Truncated technique decision cannot fit a larger output allocation") from error
        logger.info("Technique truncation recovery initial_tokens=%d retry_tokens=%d",
                    first.generation_tokens, retry.generation_tokens)
        retained = retained_finalization_prompt(prompt, error)
        agent.messages = messages
        fallback = prompt + ("\n" + finalization_context if finalization_context else "")
        retry_prompt = (retained or fallback) + (
            "\nOutput was truncated. Return a concise complete decision using only the declared fields. "
            "Do not add requirements, repeat explanations or replay investigation."
        )
        with model_output_budget(agent.model._cyber_llm_role, retry.generation_tokens):
            try:
                return invoke_structured(agent, retry_prompt, schema, investigate=False)
            except (MaxTokensReachedException, ValueError) as exhausted:
                raise RuntimeError("Technique decision remains unresolved after one truncation recovery") from exhausted
