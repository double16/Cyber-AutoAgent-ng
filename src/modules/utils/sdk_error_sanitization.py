"""Sanitize framework exception messages before they enter workflow state."""

import re
from typing import Any

_SDK_EXCEPTION_MODULE_PREFIXES = ("strands",)
_SDK_EXCEPTION_NAMES = frozenset({"MaxTokensReachedException"})
_URL_PATTERN = re.compile(r"(?i)\b(?:https?|ftp)://[^\s<>{}\[\]\"']+")
_REDACTED_URL = "[sdk-url-omitted]"


def _contains_sdk_exception(error: BaseException) -> bool:
    """Return whether an exception or its cause/context originated in an SDK."""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        error_type = type(current)
        if error_type.__name__ in _SDK_EXCEPTION_NAMES or error_type.__module__.startswith(
            _SDK_EXCEPTION_MODULE_PREFIXES
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def sanitize_sdk_error(error: Any) -> str:
    """Return an exception message with SDK-provided URLs removed.

    Only exceptions from known SDK namespaces, or exceptions wrapping one, are
    sanitized. This keeps target URLs in ordinary application errors intact.
    """

    if not isinstance(error, BaseException) or not _contains_sdk_exception(error):
        return str(error or "")
    current: BaseException | None = error.__cause__ or error.__context__
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if current.__class__.__name__ in _SDK_EXCEPTION_NAMES or current.__class__.__module__.startswith(
            _SDK_EXCEPTION_MODULE_PREFIXES
        ):
            sanitize_sdk_error(current)
        current = current.__cause__ or current.__context__
    message = str(error or "")
    sanitized = _URL_PATTERN.sub(_REDACTED_URL, message)
    if sanitized != message and len(error.args) == 1 and isinstance(error.args[0], str):
        error.args = (sanitized,)
    return sanitized


__all__ = ["sanitize_sdk_error"]
