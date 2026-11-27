"""Fail before inference when authoritative review input loses document records."""

import json

from strands.hooks import BeforeModelCallEvent, HookProvider


class ReviewInputIntegrityError(RuntimeError):
    """Incomplete input is an infrastructure failure, never an authored defect."""


class ReviewPrompt(str):
    """A prompt carrying controller-owned document integrity requirements."""


def bind_review_prompt(prompt, records):
    # Preserve DeferredReviewPrompt's independent investigation/finalization boundaries.
    bound = ReviewPrompt(prompt) if type(prompt) is str else prompt
    bound.required_review_records = tuple(json.dumps(record, ensure_ascii=False) for record in records)
    return bound


class ReviewInputIntegrityHook(HookProvider):
    def __init__(self, records):
        self.records = tuple(records)

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(BeforeModelCallEvent, self.before_model)

    def before_model(self, event):
        texts = [block["text"] for message in event.agent.messages if message.get("role") == "user"
                 for block in message.get("content", []) if isinstance(block.get("text"), str)]
        missing = [index for index, record in enumerate(self.records) if not any(record in text for text in texts)]
        if missing:
            raise ReviewInputIntegrityError(
                f"Review input integrity failed: {len(missing)} authored document records missing; "
                f"record indices={missing}. No semantic review is valid."
            )
