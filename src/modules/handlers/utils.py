#!/usr/bin/env python3
"""
Utility functions for the handlers module.

This module contains general utility functions for file operations,
output formatting, and message analysis.
"""
import base64
import json
import os
import pathlib
import re
import shutil
import sys
import threading
import tomllib
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Union


@dataclass
class LatestOutputPointerResult:
    """Result from updating the per-target latest operation pointer."""

    success: bool
    mode: str
    pointer_path: str
    operation_path: str
    message: str = ""


def get_terminal_width(default=80):
    """Get terminal width with fallback to default."""
    try:
        # Try to get actual terminal size
        size = shutil.get_terminal_size((default, 24))
        # Return a slightly smaller width to account for edge cases
        return max(40, min(size.columns - 2, default))
    except (OSError, ValueError):
        return default


def print_separator(char="─", color_start="", color_end=""):
    """Print a separator line that fits the terminal width."""
    width = get_terminal_width()
    if color_start and color_end:
        print(f"{color_start}{char * width}{color_end}")
    else:
        print(char * width)


def get_output_path(
    target_name: str,
    operation_id: str,
    subdir: str = "",
    base_dir: Optional[str] = None,
) -> str:
    """Get path for unified output directory structure.

    Args:
        target_name: Sanitized target name for organization
        operation_id: Unique operation identifier (should include OP_ prefix)
        subdir: Optional subdirectory within the operation directory
        base_dir: Optional base directory override (defaults to ./outputs)

    Returns:
        Full path in format: {base_dir}/{target_name}/{operation_id}/{subdir}
    """
    from modules.config.types import get_default_base_dir
    if base_dir is None:
        base_dir = get_default_base_dir()

    operation_dir = os.path.join(base_dir, target_name)
    if operation_id:
        operation_dir = os.path.join(operation_dir, operation_id)
    return os.path.join(operation_dir, subdir) if subdir else operation_dir


@lru_cache
def sanitize_target_name(target: str) -> str:
    """Sanitize target string for safe filesystem usage.

    Args:
        target: Raw target string (URL, IP, domain, etc.)

    Returns:
        Sanitized string safe for filesystem usage
    """
    # Remove protocol prefixes
    sanitized = re.sub(r"^https?://", "", target)
    sanitized = re.sub(r"^ftp://", "", sanitized)

    # Remove path components (keep only domain/host part)
    sanitized = sanitized.split("/")[0]

    # Remove query parameters
    sanitized = sanitized.split("?")[0]

    # Extract port if present for special handling
    port = None
    port_match = re.search(r":(\d+)$", sanitized)
    if port_match:
        port = port_match.group(1)
        # Remove port temporarily for processing
        sanitized = re.sub(r":\d+$", "", sanitized)

    # Replace unsafe characters with underscores
    sanitized = re.sub(r"[^\w\-.]", "_", sanitized)

    # Remove consecutive underscores
    sanitized = re.sub(r"_+", "_", sanitized)

    # Enforce maximum length
    sanitized = sanitized[:100]

    # Remove leading/trailing underscores and dots
    sanitized = sanitized.strip("_.")

    # Re-append port if it was present (using underscore separator for filesystem safety)
    if port:
        sanitized = f"{sanitized}_{port}"

    # Ensure non-empty result
    if not sanitized:
        sanitized = "unknown_target"

    return sanitized


def validate_output_path(path: str, base_dir: str) -> bool:
    """Validate that a path is within the allowed output directory.

    Args:
        path: Path to validate
        base_dir: Base directory that should contain the path

    Returns:
        True if path is safe and within base_dir, False otherwise
    """
    try:
        # Resolve both paths to absolute paths
        abs_path = os.path.abspath(path)
        abs_base = os.path.abspath(base_dir)

        # Check if path is within base directory
        common_path = os.path.commonpath([abs_path, abs_base])
        return common_path == abs_base
    except (ValueError, OSError):
        return False


def create_output_directory(path: str) -> bool:
    """Create an output directory if it doesn't exist.

    Args:
        path: Directory path to create

    Returns:
        True if a directory was created or already exists, False on error
    """
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


def update_latest_output_pointer(
    target_name: str,
    operation_id: str,
    base_dir: Optional[str] = None,
) -> LatestOutputPointerResult:
    """Update {base_dir}/{target_name}/latest to point at the current operation.

    The preferred pointer is a relative symlink named "latest". When symlinks
    are unavailable, fall back to a regular text file containing the absolute
    operation directory path. Existing directories are preserved.
    """
    target_dir = get_output_path(target_name, "", "", base_dir)
    operation_dir = get_output_path(target_name, operation_id, "", base_dir)
    latest_path = os.path.join(target_dir, "latest")
    operation_path = os.path.abspath(operation_dir)

    try:
        os.makedirs(operation_dir, exist_ok=True)
    except OSError as exc:
        return LatestOutputPointerResult(
            success=False,
            mode="failed",
            pointer_path=latest_path,
            operation_path=operation_path,
            message=f"Could not create operation directory: {exc}",
        )

    try:
        if os.path.lexists(latest_path):
            if os.path.islink(latest_path) or os.path.isfile(latest_path):
                os.unlink(latest_path)
            else:
                return LatestOutputPointerResult(
                    success=False,
                    mode="skipped",
                    pointer_path=latest_path,
                    operation_path=operation_path,
                    message="latest exists and is not a symlink or regular file",
                )

        os.symlink(operation_id, latest_path)
        return LatestOutputPointerResult(
            success=True,
            mode="symlink",
            pointer_path=latest_path,
            operation_path=operation_path,
            message=f"latest -> {operation_id}",
        )
    except OSError as symlink_exc:
        try:
            with open(latest_path, "w", encoding="utf-8") as latest_file:
                latest_file.write(operation_path + "\n")
            return LatestOutputPointerResult(
                success=True,
                mode="file",
                pointer_path=latest_path,
                operation_path=operation_path,
                message=f"latest fallback file written after symlink failure: {symlink_exc}",
            )
        except OSError as file_exc:
            return LatestOutputPointerResult(
                success=False,
                mode="failed",
                pointer_path=latest_path,
                operation_path=operation_path,
                message=f"Could not update latest pointer: {file_exc}",
            )


# ANSI color codes for terminal output
class Colors:
    """ANSI color codes for terminal output formatting."""

    # Check if output is to a terminal (not redirected), Docker pseudo-TTY, or if colors are forced
    # Docker allocates a pseudo-TTY when -t flag is used, which makes isatty() return True
    # We also respect FORCE_COLOR env var which is set in docker-compose.yml
    _force_color = os.environ.get("FORCE_COLOR", "").lower() in ("1", "true", "yes")
    _is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    _is_terminal = _is_tty or _force_color

    # Define colors only if outputting to terminal or colors are forced
    BLUE = "\033[94m" if _is_terminal else ""
    GREEN = "\033[92m" if _is_terminal else ""
    YELLOW = "\033[93m" if _is_terminal else ""
    RED = "\033[91m" if _is_terminal else ""
    CYAN = "\033[96m" if _is_terminal else ""
    MAGENTA = "\033[95m" if _is_terminal else ""
    BOLD = "\033[1m" if _is_terminal else ""
    DIM = "\033[2m" if _is_terminal else ""
    RESET = "\033[0m" if _is_terminal else ""


def print_banner():
    """Display operation banner with neon cyberpunk gradient colors."""
    if (
            os.getenv("CYBERAGENT_NO_BANNER", "").lower() in ("1", "true", "yes")
            or os.getenv("CYBER_UI_MODE", "cli").lower() == "react"
    ):
        return

    banner_lines = [
        r" ██████╗██╗   ██╗██████╗ ███████╗██████╗ ",
        r"██╔════╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗",
        r"██║      ╚████╔╝ ██████╔╝█████╗  ██████╔╝",
        r"██║       ╚██╔╝  ██╔══██╗██╔══╝  ██╔══██╗",
        r"╚██████╗   ██║   ██████╔╝███████╗██║  ██║",
        r" ╚═════╝   ╚═╝   ╚═════╝ ╚══════╝╚═╝  ╚═╝",
        r"",
        r"█████╗ ██╗   ██╗████████╗ ██████╗  █████╗  ██████╗ ███████╗███╗   ██╗████████╗",
        r"██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝",
        r"███████║██║   ██║   ██║   ██║   ██║███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   ",
        r"██╔══██║██║   ██║   ██║   ██║   ██║██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   ",
        r"██║  ██║╚██████╔╝   ██║   ╚██████╔╝██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ",
        r"╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ",
    ]

    try:
        with open(pathlib.Path(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "pyproject.toml"), "rb") as f:
            version = tomllib.load(f).get("project", {}).get("version", "???")
    except IOError:
        version = "???"

    subtitle = "Full Spectrum Cyber Operations"

    # Terminal Pro gradient colors (24-bit RGB ANSI codes)
    # Matrix green → Cyan → Apple blue (Gemini-inspired multi-color gradient)
    gradient_colors = [
        "\033[38;2;0;255;65m",  # Bright matrix green (#00FF41)
        "\033[38;2;0;247;92m",  # Green-emerald blend
        "\033[38;2;0;239;120m",  # Emerald blend
        "\033[38;2;0;229;160m",  # Emerald green (#00E5A0)
        "\033[38;2;0;223;192m",  # Emerald-cyan blend
        "\033[38;2;0;217;224m",  # Cyan blend
        "\033[38;2;0;217;255m",  # Bright cyan (#00D9FF)
        "\033[38;2;25;205;255m",  # Cyan-blue blend
        "\033[38;2;51;184;255m",  # Sky blue (#33B8FF)
        "\033[38;2;40;164;255m",  # Sky-blue blend
        "\033[38;2;28;148;255m",  # Blue blend
        "\033[38;2;10;132;255m",  # Apple blue (#0A84FF)
    ]

    banner_art_width = 0
    if banner_lines:
        banner_art_width = max(len(line.rstrip()) for line in banner_lines)

    # Print banner with gradient - one color per line
    print()  # Empty line before banner
    for i, line in enumerate(banner_lines):
        if i < len(gradient_colors):
            print(f"{gradient_colors[i]}{line}{Colors.RESET}")
        else:
            print(f"{gradient_colors[-1]}{line}{Colors.RESET}")

    # Print subtitle with Apple blue and version with bright matrix green
    padding_length = (
        banner_art_width - len(subtitle) - len(version) - 3
    ) // 2  # 3 for spacing
    centered_line = (
        (" " * max(0, padding_length))
        + subtitle
        + " "
        + "\033[38;2;0;255;65m"
        + version
        + Colors.RESET
    )
    print(f"\033[38;2;10;132;255m{centered_line}{Colors.RESET}")
    print()  # Empty line after banner


def print_section(title, content, color=Colors.BLUE, emoji=""):
    """Print formatted section with optional emoji."""
    if (
            os.getenv("CYBERAGENT_NO_BANNER", "").lower() in ("1", "true", "yes")
            or os.getenv("CYBER_UI_MODE", "cli").lower() == "react"
    ):
        return

    # Print section for CLI mode
    print("\n%s" % ("─" * 60))
    print("%s %s%s%s%s" % (emoji, color, Colors.BOLD, title, Colors.RESET))
    print("%s" % ("─" * 60))
    print(content)


def print_status(message, status="INFO"):
    """Print status message with color coding and emojis."""
    if (
            os.getenv("CYBERAGENT_NO_BANNER", "").lower() in ("1", "true", "yes")
            or os.getenv("CYBER_UI_MODE", "cli").lower() == "react"
    ):
        return

    # Print status for CLI mode - professional symbols only
    status_config = {
        "INFO": (Colors.BLUE, "•"),
        "SUCCESS": (Colors.GREEN, "✓"),
        "WARNING": (Colors.YELLOW, "!"),
        "ERROR": (Colors.RED, "✗"),
        "THINKING": (Colors.MAGENTA, "→"),
        "EXECUTING": (Colors.CYAN, "*"),
        "FOUND": (Colors.GREEN, "+"),
    }
    color, prefix = status_config.get(status, (Colors.BLUE, "[INFO]"))
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(
        "%s[%s]%s %s %s%s %s"
        % (
            Colors.DIM,
            timestamp,
            Colors.RESET,
            prefix,
            color,
            Colors.RESET,
            message,
        )
    )


@dataclass
class CyberEvent:
    """Structured event for terminal output."""

    type: str  # 'step_start', 'command', 'command_array', 'output', 'error', 'status', 'complete'
    content: Union[str, List[str]]
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_json(self) -> str:
        """Convert event to JSON with special markers for parsing."""
        return f"__CYBER_EVENT__{json.dumps(asdict(self), separators=(',', ':'))}__CYBER_EVENT_END__"


def emit_event(event_type: str, content: Union[str, List[str]], **metadata) -> None:
    """Emit a structured event to stdout for React parsing.

    This replaces direct print() calls to prevent garbled output.
    Events are wrapped in special markers for reliable parsing.

    Args:
        event_type: Type of event (step_start, command, output, etc.)
        content: Event content (string or list of strings)
        **metadata: Additional metadata (step number, tool name, etc.)
    """
    event = CyberEvent(type=event_type, content=content, metadata=metadata)
    # Use print with flush to ensure immediate output
    print(event.to_json(), flush=True)


def emit_step_start(step: int, total_steps: int, tool_name: str) -> None:
    """Emit a step start event."""
    emit_event("step_start", tool_name, step=step, total_steps=total_steps)


def emit_command(command: Union[str, List[str]]) -> None:
    """Emit a command execution event."""
    if isinstance(command, list):
        emit_event("command_array", command)
    else:
        emit_event("command", command)


def emit_output(output: str) -> None:
    """Emit tool output event."""
    # Emit the entire output as a single event
    # The UI will handle formatting and display
    if output.strip():
        emit_event("output", output.strip())


def emit_error(error: str) -> None:
    """Emit an error event."""
    emit_event("error", error, level="error")


def emit_status(message: str, level: str = "info") -> None:
    """Emit a status message event."""
    emit_event("status", message, level=level)


def dumpstacks(signal, frame):
    id2name = dict([(th.ident, th.name) for th in threading.enumerate()])
    trace = []
    for threadId, stack in sys._current_frames().items():
        trace.append("\n# Thread: %s(%d)" % (id2name.get(threadId, ""), threadId))
        for filename, lineno, name, line in traceback.extract_stack(stack):
            trace.append('File: "%s", line %d, in %s' % (filename, lineno, name))
            if line:
                trace.append("  %s" % (line.strip()))
    print("\n".join(trace), file=sys.stderr)
    try:
        from guppy import hpy
        h = hpy()
        print(str((h.heap())), file=sys.stderr)
    except ImportError:
        pass


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def get_tool_spec(tool) -> Optional[Dict[str, Any]]:
    if hasattr(tool, "tool_spec"):
        return getattr(tool, "tool_spec")
    if hasattr(tool, "TOOL_SPEC"):
        return getattr(tool, "TOOL_SPEC")
    return None


def get_tool_name(tool) -> str:
    tool_spec = get_tool_spec(tool)
    if tool_spec and tool_spec.get("name"):
        return str(tool_spec["name"])
    try:
        tool_name = tool.tool_name
    except AttributeError:
        tool_name = getattr(tool, "__name__", tool.__class__.__name__).split(".")[-1]
    return str(tool_name)


def get_tool_description(tool) -> str:
    tool_spec = get_tool_spec(tool)
    if tool_spec and tool_spec.get("description"):
        return str(tool_spec["description"])
    description = getattr(tool, "description", None)
    if description:
        return str(description)
    description = getattr(tool, "__doc__", None)
    if description:
        return str(description)
    description = getattr(tool.__class__, "__doc__", None)
    return str(description or "")


def tool_rename(tool, new_name: str):
    if hasattr(tool, "tool_name"):
        setattr(tool, "tool_name", new_name)
    tool_spec = get_tool_spec(tool)
    if tool_spec:
        tool_spec["name"] = new_name


def tool_append_description(tool, description: str):
    tool_spec = get_tool_spec(tool)
    if tool_spec:
        tool_description = tool_spec.get("description", "")
        tool_spec["description"] = tool_description + "\n\n" + description


def duration_max(*values):
    parsed = []
    for value in values:
        if not value:
            continue
        nums = [
            float(m.group(0))
            for token in re.split(r"[\s:]+", value.strip())
            if token
            for m in [re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", token)]
            if m
        ]
        parsed.append((value, nums))

    width = max((len(nums) for _, nums in parsed), default=0)
    return max(parsed, key=lambda x: [0.0] * (width - len(x[1])) + x[1])[0] if parsed else None


def filter_none_values(d: dict) -> dict:
    """
    Returns a new dict with None values removed.
    """
    return {
        key: value
        for key, value in d.items()
        if value is not None
    }


def sanitize_toon_value(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace(",", ";")
