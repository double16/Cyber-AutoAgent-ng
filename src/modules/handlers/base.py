"""
Base classes and constants for the handlers module.

This module contains shared constants, type definitions, and base functionality
used across different handler components.
"""

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def is_docker():
    """Check if running inside a Docker container."""
    return os.path.exists("/.dockerenv") or os.environ.get("CONTAINER") == "docker" or os.path.exists("/app")


# Use langfuse-web:3000 when in Docker, localhost:3000 otherwise
DEFAULT_LANGFUSE_HOST = (
    "http://langfuse-web:3000" if is_docker() else "http://localhost:3000"
)
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", DEFAULT_LANGFUSE_HOST)
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-placeholder")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-placeholder")

# Display configuration
CONTENT_PREVIEW_LENGTH = 200
MAX_CONTENT_DISPLAY_LENGTH = 500


class HandlerError(Exception):
    """Base exception for handler-related errors."""


class BudgetLimitReached(HandlerError):
    """Raised when an execution budget limit is reached."""
