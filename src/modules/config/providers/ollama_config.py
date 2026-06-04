#!/usr/bin/env python3
"""
Ollama provider configuration helpers.

This module provides configuration utilities specific to Ollama local models,
including host detection and connectivity checks.
"""

import os
from typing import Dict, Any

import requests

from modules.config.system.env_reader import EnvironmentReader
from modules.config.system.logger import get_logger

logger = get_logger("Config.OllamaProvider")


def get_ollama_host(env_reader: EnvironmentReader) -> str:
    """Determine the appropriate Ollama host based on environment.

    Tries the following in order:
    1. OLLAMA_HOST environment variable
    2. If in Docker (/app exists), try localhost and host.docker.internal
    3. Default to localhost for native execution

    Args:
        env_reader: Environment variable reader

    Returns:
        Ollama host URL (e.g., "http://localhost:11434")
    """
    env_host = env_reader.get("OLLAMA_HOST")
    if env_host:
        return env_host

    # Check if running in Docker
    if os.path.exists("/app"):
        candidates = ["http://localhost:11434", "http://host.docker.internal:11434"]
        for host in candidates:
            try:
                response = requests.get(f"{host}/api/version", timeout=2)
                if response.status_code == 200:
                    logger.debug("Found Ollama at %s", host)
                    return host
            except (requests.exceptions.RequestException, ConnectionError):
                pass
        # Fallback to host.docker.internal if no connection works
        logger.debug(
            "No Ollama connection found, falling back to host.docker.internal"
        )
        return "http://host.docker.internal:11434"
    # Native execution - use localhost
    return "http://localhost:11434"


def get_ollama_timeout(env_reader: EnvironmentReader) -> float:
    """Determine the appropriate Ollama timeout based on environment.

    Tries the following in order:
    1. OLLAMA_TIMEOUT environment variable
    2. Default to 120

    Args:
        env_reader: Environment variable reader

    Returns:
        Ollama timeout in seconds (120)
    """
    env_timeout = env_reader.get("OLLAMA_TIMEOUT")
    if env_timeout:
        try:
            return float(env_timeout)
        except ValueError:
            logger.warning(
                "Ollama timeout not a float, falling back to 120"
            )
    return 120


def get_ollama_keep_alive(env_reader: EnvironmentReader) -> str:
    """Determine appropriate Ollama keep alive based on the environment.

    Tries the following in order:
    1. OLLAMA_KEEP_ALIVE environment variable
    2. Default to 30m

    Args:
        env_reader: Environment variable reader

    Returns:
        Ollama keep_alive
    """
    return env_reader.get("OLLAMA_KEEP_ALIVE", "30m")


def get_ollama_options(env_reader: EnvironmentReader) -> Dict[str, Any]:
    options = dict()
    env_context_length = env_reader.get("OLLAMA_CONTEXT_LENGTH")
    if env_context_length and env_context_length.strip():
        try:
            num_ctx = int(env_context_length)
            if num_ctx >= 2048:
                options["num_ctx"] = num_ctx
        except ValueError:
            logger.warning(
                "OLLAMA_CONTEXT_LENGTH should be an int, ignoring"
            )
    return options
