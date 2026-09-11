#!/usr/bin/env python3

import atexit
import contextlib
import copy
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

from modules.config.system.logger import (
    configure_provider_diagnostic_logging,
    get_logger,
    initialize_logger_factory,
    unsafe_diagnostic_logging_enabled,
)
from modules.config.types import get_default_base_dir
from modules.handlers.utils import print_status
from modules.utils.redaction import redact, redact_text


def clean_operation_memory(operation_id: str, target_values: list[str] | None = None):
    """Delete semantic-memory points for one operation without removing the database."""
    logger = get_logger("Config.Environment")
    raw_targets = [target_values] if isinstance(target_values, str) else (target_values or [])
    resolved_targets = [
        str(value).strip()
        for value in raw_targets
        if str(value).strip()
    ]
    logger.debug(
        "Cleaning Qdrant memory for operation_id=%s target_values=%s",
        operation_id,
        resolved_targets,
    )
    if not operation_id:
        logger.warning("No operation ID provided, skipping memory cleanup")
        return
    if not resolved_targets:
        logger.warning("No target values provided, skipping memory cleanup")
        return
    try:
        url = str(os.getenv("QDRANT_URL", "")).strip()
        client = (
            QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY") or None)
            if url
            else QdrantClient(path=os.path.join(get_default_base_dir(), "qdrant"))
        )
        collection = os.getenv("QDRANT_COLLECTION", "cyber_autoagent_memories")
        if not client.collection_exists(collection):
            logger.debug("Qdrant collection %s does not exist; no memory to clean", collection)
            return
        selector = qdrant_models.FilterSelector(
            filter=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="target_values",
                        match=qdrant_models.MatchAny(any=resolved_targets),
                    ),
                    qdrant_models.FieldCondition(
                        key="operation_id",
                        match=qdrant_models.MatchValue(value=operation_id),
                    )
                ]
            )
        )
        client.delete(collection_name=collection, points_selector=selector, wait=True)
        logger.info("Cleaned Qdrant memory for operation %s", operation_id)
    except Exception as error:
        logger.error("Failed to clean Qdrant memory for %s: %s", operation_id, error)


_STARTUP_FAILURE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:module|package)notfounderror\b",
        r"\bimporterror\b",
        r"\bno module named\b",
        r"\berror while loading shared libraries\b",
        r"\b(?:shared object|dynamic library) .* (?:not found|cannot open)\b",
        r"\btraceback \(most recent call last\)\b",
    )
)

_SECLISTS_ENV_VAR = "CYBER_SECLISTS_DIR"
_SECLISTS_ROOT_CANDIDATES = (
    Path("/usr/share/seclists"),
    Path("/usr/share/SecLists"),
    Path("/opt/seclists"),
    Path("/opt/SecLists"),
    Path.home() / "seclists",
    Path.home() / "SecLists",
    Path.home() / "wordlists" / "seclists",
    Path.home() / "wordlists" / "SecLists",
)
_SECLISTS_ROOT_MARKERS = ("Discovery", "Fuzzing", "Passwords", "Usernames", "Miscellaneous")


def _is_seclists_root(path: Path) -> bool:
    """Return whether ``path`` has the expected SecLists root structure."""

    try:
        return path.is_dir() and any((path / marker).is_dir() for marker in _SECLISTS_ROOT_MARKERS)
    except OSError:
        return False


def resolve_seclists_root() -> str | None:
    """Resolve the local SecLists root without performing a filesystem-wide search."""

    configured_root = os.getenv(_SECLISTS_ENV_VAR, "").strip()
    candidate_roots = [Path(configured_root)] if configured_root else []
    candidate_roots.extend(_SECLISTS_ROOT_CANDIDATES)

    for candidate in candidate_roots:
        if _is_seclists_root(candidate):
            try:
                return str(candidate.resolve())
            except OSError:
                return str(candidate.absolute())
    return None


@dataclass(frozen=True)
class ToolHealth:
    """Result of deterministic executable discovery and optional startup verification."""

    state: str
    path: str | None
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.state in {"available_verified", "available_unverified"}


def _bounded_probe_reason(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _probe_config(canary: Any) -> tuple[list[str], int, set[int]]:
    if not isinstance(canary, dict):
        raise ValueError("canary must be an object")
    args = canary.get("args")
    if not isinstance(args, list) or not all(isinstance(arg, str) and arg for arg in args):
        raise ValueError("canary.args must be a list of non-empty strings")
    timeout_seconds = canary.get("timeout_seconds", 5)
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 60:
        raise ValueError("canary.timeout_seconds must be an integer from 1 through 60")
    exit_codes = canary.get("accepted_exit_codes", [0])
    if not isinstance(exit_codes, list) or not exit_codes or not all(
        isinstance(code, int) and not isinstance(code, bool) for code in exit_codes
    ):
        raise ValueError("canary.accepted_exit_codes must be a non-empty list of integers")
    return args, timeout_seconds, set(exit_codes)


def check_shell_command(command: str, canary: Any = None) -> ToolHealth:
    """Resolve a command and optionally verify that it starts without generic dependency failures."""

    tool_path = shutil.which(command)
    if not tool_path:
        return ToolHealth("missing", None, "executable not found in PATH")
    if canary is None:
        return ToolHealth("available_unverified", tool_path)
    try:
        args, timeout_seconds, accepted_exit_codes = _probe_config(canary)
        result = subprocess.run(
            [tool_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, ValueError) as error:
        return ToolHealth("broken", tool_path, _bounded_probe_reason(error))
    except subprocess.TimeoutExpired:
        return ToolHealth("broken", tool_path, "canary timed out")

    output = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
    startup_failure = next((pattern.pattern for pattern in _STARTUP_FAILURE_PATTERNS if pattern.search(output)), "")
    if startup_failure:
        return ToolHealth("broken", tool_path, _bounded_probe_reason(output))
    if result.returncode not in accepted_exit_codes:
        reason = output or f"canary exited with code {result.returncode}"
        return ToolHealth("broken", tool_path, _bounded_probe_reason(reason))
    return ToolHealth("available_verified", tool_path)


def _get_shell_command_path(command: str, canary: Any = None) -> str | None:
    """Return the path for an available command, preserving the historical helper contract."""

    health = check_shell_command(command, canary)
    return health.path if health.available else None


def auto_setup() -> list[str]:
    """Setup directories and discover available cyber tools"""
    # Disable RAGAS evaluator tracking
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

    # Create necessary directories in proper locations
    try:
        tools_path = Path("tools")
        if tools_path.exists():
            if not tools_path.is_dir():
                # If 'tools' exists but is not a directory, remove it and create directory
                print_status(
                    "Removing existing 'tools' file to create directory", "WARNING"
                )
                tools_path.unlink()
                tools_path.mkdir(exist_ok=True)
        else:
            tools_path.mkdir(exist_ok=True)  # Local tools directory for custom tools
    except PermissionError:
        # If we can't access or create the tools directory, continue without it
        print_status(
            "Cannot create/access 'tools' directory - continuing without custom tools",
            "WARNING",
        )
    except Exception as e:
        # Log any other issues but continue
        print_status(f"Issue with tools directory: {e} - continuing", "WARNING")

    # httpx has two packages: python and projectdiscovery, we want projectdiscovery
    try:
        httpx_path = shutil.which("httpx")
        if httpx_path and os.access(httpx_path, os.R_OK | os.W_OK) and os.stat(httpx_path).st_size > 10:
            httpx_is_python = False
            with open(httpx_path, "rb") as f:
                magic = f.read(4)
                if magic.startswith(b"#!/"):
                    httpx_is_python = True
            if httpx_is_python:
                os.remove(httpx_path)
    except Exception:
        pass

    print_status("Discovering cyber security tools...", "INFO")

    # Emit structured event for React UI
    tool_discovery_event = {
        "type": "tool_discovery_start",
        "timestamp": datetime.now().isoformat(),
        "message": "Starting cybersecurity tool discovery",
    }
    print(f"__CYBER_EVENT__{json.dumps(tool_discovery_event)}__CYBER_EVENT_END__")

    # Load tools from environment.yaml in the same directory as this file
    env_path = Path(__file__).with_name("environment.yaml")
    with env_path.open("r", encoding="utf-8") as f:
        env_config = yaml.safe_load(f) or {}

    cyber_tools = env_config.get("cyber_tools", {})

    available_tools = []

    # Check existing tools using shutil.which
    for tool_name, tool_info in cyber_tools.items():
        description = tool_info.get("description", "")
        binary = tool_info.get("command", tool_name)

        health = check_shell_command(binary, tool_info.get("canary"))
        tool_path = health.path
        is_available = health.available

        if is_available:
            available_tools.append(tool_name)
            print_status(f"✓ {tool_name:<12} - {description}", "SUCCESS")

            # Emit structured event for React UI
            tool_event = {
                "type": "tool_available",
                "timestamp": datetime.now().isoformat(),
                "tool_name": tool_name,
                "description": description,
                "status": "available",
                "health": health.state,
                "binary": binary,
                "path": tool_path,
            }
            print(f"__CYBER_EVENT__{json.dumps(tool_event)}__CYBER_EVENT_END__")
        else:
            print_status(
                f"○ {tool_name:<12} - {description} ({health.state}: {health.reason})", "WARNING"
            )

            # Emit structured event for React UI
            tool_event = {
                "type": "tool_unavailable",
                "timestamp": datetime.now().isoformat(),
                "tool_name": tool_name,
                "description": description,
                "status": "unavailable",
                "health": health.state,
                "reason": health.reason,
                "binary": binary,
                "path": None,
            }
            print(f"__CYBER_EVENT__{json.dumps(tool_event)}__CYBER_EVENT_END__")

    print_status(
        f"Environment ready. {len(available_tools)} cyber tools available.", "SUCCESS"
    )

    # Emit environment ready event
    env_ready_event = {
        "type": "environment_ready",
        "timestamp": datetime.now().isoformat(),
        "available_tools": available_tools,
        "tool_count": len(available_tools),
        "message": f"Environment ready with {len(available_tools)} cybersecurity tools",
    }
    print(f"__CYBER_EVENT__{json.dumps(env_ready_event)}__CYBER_EVENT_END__")
    return available_tools


class TeeOutput:
    """Thread-safe output duplicator to both terminal and log file"""

    def __init__(self, stream, log_file):
        self.terminal = stream
        self.log = open(log_file, "a", encoding="utf-8", buffering=1)
        self.lock = threading.Lock()
        self.line_buffer = ""  # Buffer for incomplete lines
        self._closed = False

    def write(self, message):
        with self.lock:
            # Write to terminal as-is, ensuring proper flushing
            self.terminal.write(message)
            # Force immediate flush to prevent buffering issues
            if hasattr(self.terminal, "flush"):
                self.terminal.flush()

            # Clean message for log file
            try:
                # Remove ANSI escape sequences for log file
                ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
                clean_message = ansi_escape.sub("", message)

                # Handle carriage returns properly
                # If message contains \r without \n, it's likely overwriting the same line
                if "\r" in clean_message and "\r\n" not in clean_message:
                    # Split by \r and take the last part (what would be visible on screen)
                    parts = clean_message.split("\r")
                    # Keep only the last part after all overwrites
                    clean_message = parts[-1]
                    # If we had buffered content, clear it as it's being overwritten
                    self.line_buffer = ""

                # Add to line buffer
                self.line_buffer += clean_message

                # Write complete lines to log
                if "\n" in self.line_buffer:
                    lines = self.line_buffer.split("\n")
                    # Write all complete lines
                    for line in lines[:-1]:
                        # Don't strip leading spaces - preserve formatting
                        self.log.write(redact_text(line) + "\n")
                    # Keep the incomplete line in buffer
                    self.line_buffer = lines[-1]
                    self.log.flush()

            except (ValueError, OSError):
                # Handle closed file gracefully
                pass

    def flush(self):
        with self.lock:
            self.terminal.flush()
            with contextlib.suppress(ValueError, OSError):
                self.log.flush()

    def close(self):
        with self.lock:
            if self._closed:
                return
            self._closed = True
            try:
                # Flush any remaining buffered content
                if self.line_buffer:
                    self.log.write(redact_text(self.line_buffer))
                    self.line_buffer = ""
                    self.log.flush()
                self.log.close()
            except (OSError, AttributeError):
                pass

    # Additional methods to fully mimic file objects
    def fileno(self):
        return self.terminal.fileno()

    def isatty(self):
        return self.terminal.isatty()


def setup_logging(log_file: str = "cyber_operations.log", verbose: bool = False):
    """Configure unified logging for all operations with complete terminal capture"""
    # Ensure the directory exists
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # Create header in log file
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("\n" + "=" * 80 + "\n")
        f.write(
            f"CYBER-AUTOAGENT SESSION STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        f.write("=" * 80 + "\n\n")

    # Set up stdout and stderr redirection to capture ALL terminal output
    sys.stdout = TeeOutput(sys.stdout, log_file)
    sys.stderr = TeeOutput(sys.stderr, log_file)

    # Register cleanup handler to ensure log files are properly closed
    def cleanup_tee_outputs():
        if isinstance(sys.stdout, TeeOutput):
            sys.stdout.close()
            sys.stdout = sys.__stdout__
        if isinstance(sys.stderr, TeeOutput):
            sys.stderr.close()
            sys.stderr = sys.__stderr__

    atexit.register(cleanup_tee_outputs)

    # Initialize the logger factory with configuration
    initialize_logger_factory(log_file=log_file, verbose=verbose)

    # Traditional logger setup for structured logging
    class RedactingFormatter(logging.Formatter):
        """Render log records without secret values while preserving diagnostic context."""

        def format(self, record: logging.LogRecord) -> str:
            safe_record = copy.copy(record)
            if isinstance(safe_record.args, tuple):
                safe_record.args = tuple(redact(value) for value in safe_record.args)
            elif safe_record.args:
                safe_record.args = redact(safe_record.args)
            safe_record.msg = redact(safe_record.msg)
            return redact_text(super().format(safe_record))

    formatter = RedactingFormatter(
        fmt="%(asctime)s - [%(name)s] - %(levelname)s - [%(threadName)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    operation_file_level = (
        logging.DEBUG
        if verbose and unsafe_diagnostic_logging_enabled()
        else logging.INFO
    )

    # Operation logs keep structured events plus INFO-and-above Python records.
    file_handler = logging.FileHandler(log_file, mode="a")
    file_handler.setLevel(operation_file_level)
    file_handler.setFormatter(formatter)

    # Console handler - only show warnings and above unless verbose
    console_handler = logging.StreamHandler(sys.__stdout__)  # Use original stdout
    console_handler.setLevel(logging.INFO if verbose else logging.WARNING)
    console_handler.setFormatter(formatter)

    # Configure the logger specifically
    cyber_logger = logging.getLogger("CyberAutoAgent")
    cyber_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    cyber_logger.addHandler(file_handler)
    if verbose:
        cyber_logger.addHandler(console_handler)
    cyber_logger.propagate = False  # Don't propagate to root logger

    # Suppress Strands framework error logging for expected step limit termination
    strands_event_loop_logger = logging.getLogger("strands.event_loop.event_loop")
    strands_event_loop_logger.setLevel(
        logging.CRITICAL
    )  # Only show critical errors, not our expected StopIteration

    # Capture all other loggers at INFO level to file. Verbose mode may still
    # enable component diagnostics elsewhere, but does not expand operation logs.
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    root_file_handler = logging.FileHandler(log_file, mode="a")
    root_file_handler.setLevel(operation_file_level)
    root_file_handler.setFormatter(formatter)
    root_logger.addHandler(root_file_handler)

    # Suppress verbose AWS credential detection messages
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    configure_provider_diagnostic_logging(enable_debug=verbose)

    return cyber_logger
