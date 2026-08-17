"""Filesystem-backed result caching for expensive web operation tools."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


RESULT_CACHE_TTL_SECONDS = 30 * 60
RESULT_CACHE_DIR = Path.home() / ".cache" / "cyber-autoagent" / "tool-results"


def build_result_cache_key(**values: Any) -> str:
    """Return a deterministic, opaque cache key for normalized tool inputs."""
    serialized = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def get_cached_result(namespace: str, cache_key: str) -> str | None:
    """Return a still-valid cached JSON result, ignoring unusable cache entries."""
    cache_file = RESULT_CACHE_DIR / namespace / f"{cache_key}.json"
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("expires_at", 0) <= time.time():
            return None
        result = payload.get("result")
        return result if isinstance(result, str) else None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def cache_result(namespace: str, cache_key: str, result: str) -> None:
    """Atomically persist a JSON result for the fixed result-cache TTL."""
    cache_dir = RESULT_CACHE_DIR / namespace
    temporary_path: str | None = None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "expires_at": time.time() + RESULT_CACHE_TTL_SECONDS,
            "result": result,
        }
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=cache_dir, delete=False) as temporary:
            json.dump(payload, temporary, ensure_ascii=False, separators=(",", ":"))
            temporary_path = temporary.name
        os.replace(temporary_path, cache_dir / f"{cache_key}.json")
    except OSError:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
