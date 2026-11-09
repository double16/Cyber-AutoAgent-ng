"""Provider-neutral validation diagnostics retained for controller-owned correction."""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ValidationError

from modules.utils.redaction import bounded_redacted_text


class StructuredOutputValidationError(ValueError):
    """Keep rejected content internal; expose only bounded, redacted diagnostics as text."""

    def __init__(self, error: Exception, rejected_response: str | None = None) -> None:
        self.rejected_response = rejected_response
        self.validation_errors = (
            [{"loc": list(item["loc"]), "type": item["type"], "msg": item["msg"]}
             for item in error.errors(include_input=False, include_context=False, include_url=False)]
            if isinstance(error, ValidationError)
            else [{"loc": [], "type": type(error).__name__, "msg": str(error)}]
        )
        self.response_fingerprint = None
        if rejected_response is not None:
            try:
                canonical = json.dumps(json.loads(rejected_response), sort_keys=True, ensure_ascii=False)
            except (ValueError, TypeError):
                canonical = rejected_response
            self.response_fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.failure_fingerprint = hashlib.sha256(
            json.dumps(self.validation_errors, sort_keys=True).encode("utf-8")
        ).hexdigest()
        detail = "; ".join(
            f"{'.'.join(map(str, item['loc'])) or 'response'}: {item['msg']} [{item['type']}]"
            for item in self.validation_errors
        )
        super().__init__(
            f"Failed to parse or validate native structured content: {bounded_redacted_text(detail, 1000)}"
        )


def validate_structured_json(output_model: type[BaseModel], content: str) -> Any:
    """Validate exact provider text without losing the response needed to correct it."""
    try:
        return output_model.model_validate_json(content)
    except (ValueError, TypeError) as error:
        raise StructuredOutputValidationError(error, content) from None
