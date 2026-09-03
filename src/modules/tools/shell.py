
#
# We are overriding the shell tool because models aren't very good at following input schemas.
#

import json
import logging
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

from strands import tool

logger = logging.getLogger(__name__)

ShellCommandValidator = Callable[[list[str | dict]], str | None]
_shell_command_validator: ContextVar[ShellCommandValidator | None] = ContextVar(
    "shell_command_validator",
    default=None,
)


@contextmanager
def scoped_shell_command_validator(validator: ShellCommandValidator) -> Iterator[None]:
    """Apply a controller-owned command validator for the current execution scope."""

    token = _shell_command_validator.set(validator)
    try:
        yield
    finally:
        _shell_command_validator.reset(token)


def _safe_text(value: Any) -> str:
    """Normalize subprocess output values to text."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("latin-1", errors="ignore")
    return str(value)


def validate_command(command: str | dict) -> tuple[str, dict]:
    """Validate and normalize command input."""
    if isinstance(command, str):
        return command, {}
    elif isinstance(command, dict):
        cmd = command.get("command")
        if not cmd or not isinstance(cmd, str):
            raise ValueError("Command object must contain a 'command' string")
        return cmd, command
    else:
        raise ValueError("Command must be string or dict")


_SHELL_CONTROL_OPERATOR_PATTERN = re.compile(r"(?:&&|\|\||[;<>]|`|\$\()")


def _is_empty_read_only_search(command: str, exit_code: int, error: str) -> bool:
    """Return whether grep/rg exit status one unambiguously means no matches."""

    if (
        exit_code != 1
        or error.strip()
        or _SHELL_CONTROL_OPERATOR_PATTERN.search(command)
    ):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    executable = os.path.basename(tokens[0]).lower()
    if executable not in {"grep", "rg"}:
        return False
    return "--pre" not in tokens and "--pre-glob" not in tokens


class CommandExecutor:
    """Handles execution of shell commands with timeout."""

    def __init__(self, timeout: int | None = None) -> None:
        self.timeout = int(os.environ.get("SHELL_DEFAULT_TIMEOUT", "900")) if timeout is None else timeout

    def execute(self, command: str, cwd: str) -> tuple[int, str, str]:
        """Execute command with timeout support."""
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            timeout_error = f"Command timed out after {self.timeout} seconds"
            stderr = _safe_text(error.stderr)
            if stderr:
                timeout_error = f"{stderr}\n{timeout_error}"
            return 124, _safe_text(error.stdout or error.output), timeout_error
        return completed.returncode, completed.stdout, completed.stderr


def execute_single_command(
        command: str | dict, work_dir: str, timeout: int
) -> dict[str, Any]:
    """Execute a single command and return its results."""
    cmd_str = str(command)

    try:
        cmd_str, cmd_opts = validate_command(command)
        executor = CommandExecutor(timeout=timeout)
        exit_code, output, error = executor.execute(cmd_str, work_dir)

        result = {
            "command": cmd_str,
            "exit_code": exit_code,
            "output": output,
            "error": error,
            "status": "success" if exit_code == 0 else "error",
        }
        if _is_empty_read_only_search(cmd_str, exit_code, error):
            result["status"] = "success"
            result["no_matches"] = True

        if cmd_opts:
            result["options"] = cmd_opts

        return result

    except Exception as e:
        return {
            "command": cmd_str,
            "exit_code": 1,
            "output": "",
            "error": str(e),
            "status": "error",
        }


class CommandContext:
    """Maintains command execution context including working directory."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = os.path.abspath(base_dir)
        self.current_dir = self.base_dir
        self._dir_stack: list[str] = []

    def push_dir(self) -> None:
        """Save current directory to stack."""
        self._dir_stack.append(self.current_dir)

    def pop_dir(self) -> None:
        """Restore previous directory from stack."""
        if self._dir_stack:
            self.current_dir = self._dir_stack.pop()

    def update_dir(self, command: str) -> None:
        """Update current directory based on cd command."""
        if command.strip().startswith("cd "):
            new_dir = command.split("cd ", 1)[1].strip()
            if new_dir.startswith("/"):
                # Absolute path
                self.current_dir = os.path.abspath(new_dir)
            else:
                # Relative path
                self.current_dir = os.path.abspath(os.path.join(self.current_dir, new_dir))


def execute_commands(
        commands: list[str | dict],
        parallel: bool,
        ignore_errors: bool,
        work_dir: str,
        timeout: int,
) -> list[dict[str, Any]]:
    """Execute multiple commands either sequentially or in parallel."""
    results = []
    context = CommandContext(work_dir)

    if parallel:
        # For parallel execution, use the initial work_dir for all commands
        with ThreadPoolExecutor() as executor:
            future_to_index = {
                executor.submit(execute_single_command, cmd, work_dir, timeout): index
                for index, cmd in enumerate(commands)
            }
            ordered_results: list[dict[str, Any] | None] = [None] * len(future_to_index)

            for future in as_completed(future_to_index):
                index = future_to_index[future]
                result = future.result()
                ordered_results[index] = result
            results.extend(result for result in ordered_results if result is not None)
    else:
        # For sequential execution, maintain directory context
        for cmd in commands:
            cmd_str = cmd if isinstance(cmd, str) else cmd.get("command", "")

            # Execute in current context directory
            result = execute_single_command(cmd, context.current_dir, timeout)
            results.append(result)

            # Update context if command was successful
            if result["status"] == "success":
                context.update_dir(cmd_str)

            if not ignore_errors and result["status"] == "error":
                break

    return results


def normalize_commands(
        command: str | list[str | dict[Any, Any]] | dict[Any, Any],
) -> list[str | dict]:
    """Convert command input into a normalized list of commands."""
    if isinstance(command, list):
        return command
    return [command]


@tool
def shell(
        command: str | list[str | dict[str, Any]],
        parallel: bool = False,
        timeout: int | None = None,
        work_dir: str | None = None,
) -> dict[str, Any]:
    """Non-interactive shell for command execution. Features:

    1. Selection Rules:
      • Purpose-built tool when scanning/enumerating many targets or endpoints.
      • `curl` supports single requests, reproductions, crafted edge-cases, and independent HTTP validation.
        For presence or accessibility checks, capture status explicitly, for example:
        `curl -sS -o /dev/null -w "%{http_code} %{url_effective}\n" <url>` or
        `curl -sS -D - -o /dev/null <url>`. Avoid bare `curl -s <url>` as evidence because it may emit no useful
        output.
      • `grep/sed/awk/jq` only for small transformations after purpose-built tools produce raw output.

    2. Command Formats:
       • Single Command (string):
         command: "ls -la"

       • Multiple Commands (array):
         command: ["cd /path", "git status"]

       • Detailed Command Objects:
         command: [{
           "command": "git clone repo",
           "timeout": 60,
           "work_dir": "/specific/path"
         }]

    3. Execution Modes:
       • Sequential (default): Commands run in order
       • Parallel: Multiple commands execute simultaneously
       • Error Handling: Sequential lists stop on failure; parallel lists fail if any command fails

    4. Best Practices:
       • Use command string for a single command, arrays for multiple commands
       • Set appropriate timeouts
       • Specify work_dir when needed
       • Use parallel execution for independent commands
       • Default timeout: 300s, heavy operations ≥600s.
       • On timeout → reduce scope, break into smaller operations
       • Large outputs (>10KB expected):
         • Pipe to file: `sqlmap ... 2>&1 | tee <artifacts_path>/sqlmap_output.txt`
         • Extract relevant: `grep -E "password|hash|Database:" <artifacts_path>/sqlmap_output.txt`
       • Install missing tools: `apt install tool` or `pip install package` (no sudo needed in container)

    Example Usage:
    1. Simple command:
       {"command": "ls -la"}

    2. Multiple commands:
       {"command": ["mkdir test", "cd test", "touch file.txt"]}

    3. Parallel execution:
       {"command": ["task1", "task2"], "parallel": true}

    4. Custom directory:
       {"command": "npm install", "work_dir": "/app/path"}

    Args:
        command: The shell command(s) to execute interactively. Can be a single command string or array of commands
        parallel: Whether to execute multiple commands in parallel (default: False)
        timeout: Timeout in seconds for each command (default: 600)
        work_dir: Working directory for command execution (default: current)

    Returns:
        Dict containing status and response content
    """

    # Models may use an array for each argument. Use a heuristic to determine if command is a single string or array.
    if isinstance(command, list) and len(command) > 1:
        if all(isinstance(cmd, str) for cmd in command):
            first_cmd = str(command[0])
            if ' ' not in first_cmd:
                is_first_cmd_known = os.system(f"which {first_cmd} >/dev/null 2>&1") == 0
                if is_first_cmd_known:
                    second_cmd = str(command[1])
                    if ' ' in second_cmd or os.path.isdir(second_cmd) or (os.path.isfile(second_cmd) and not os.access(second_cmd, os.X_OK)):
                        is_second_cmd_known = False
                    else:
                        is_second_cmd_known = os.system(f"which {second_cmd} >/dev/null 2>&1") == 0
                    if not is_second_cmd_known:
                        command = " ".join(map(shlex.quote, command))

    if timeout is not None:
        # make sure timeout is sane
        while timeout > 2000:
            # probably not using seconds as units
            timeout = timeout // 1000
        timeout = min(900, max(timeout, 30))

    # Validate command parameter
    if command is None:
        return {
            "status": "error",
            "content": [{"text": "Command is required"}],
        }

    # Fix for array input: if the command is a string that looks like JSON array, parse it
    if isinstance(command, str) and command.strip().startswith("[") and command.strip().endswith("]"):
        try:
            command = json.loads(command)
        except json.JSONDecodeError:
            # If it fails to parse, keep it as a string
            pass

    commands = normalize_commands(command)

    validator = _shell_command_validator.get()
    if validator is not None:
        error = validator(commands)
        if error:
            return {
                "status": "error",
                "content": [{"text": error}],
            }

    # Set defaults for parameters
    if timeout is None:
        timeout = int(os.environ.get("SHELL_DEFAULT_TIMEOUT", "900"))
    if work_dir is None:
        # TODO: look for operation output path in env vars
        work_dir = os.getcwd()

    # Development mode check
    STRANDS_BYPASS_TOOL_CONSENT = os.environ.get("BYPASS_TOOL_CONSENT", "").lower() == "true"

    if not STRANDS_BYPASS_TOOL_CONSENT:
        # TODO: Implement HITL tool consent
        pass

    try:
        results = execute_commands(commands, parallel, False, work_dir, timeout)

        # Process results for tool output
        success_count = sum(1 for r in results if r["status"] == "success")
        error_count = len(results) - success_count

        content = []
        for result in results:
            no_matches = "\nNo Matches: true" if result.get("no_matches") else ""
            content.append(
                {
                    "text": f"Command: {result['command']}\n"
                            f"Status: {result['status']}\n"
                            f"Exit Code: {result['exit_code']}\n"
                            f"Output: {result['output']}\n"
                            f"Error: {result['error']}{no_matches}"
                }
            )

        content.insert(
            0,
            {
                "text": f"Execution Summary:\n"
                        f"Total commands: {len(results)}\n"
                        f"Successful: {success_count}\n"
                        f"Failed: {error_count}"
            },
        )

        status: Literal["success", "error"] = "success" if error_count == 0 else "error"

        return {"status": status, "content": content}

    except Exception as e:
        return {
            "status": "error",
            "content": [{"text": f"Shell error: {e!s}"}],
        }
