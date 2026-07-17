import os
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Union

from strands import tool
from strands_tools.shell import shell as shell_original

#
# We are overriding the shell tool because models aren't very good at following input schemas.
#


def _parallel_command_result(
        command: Union[str, Dict[str, Any]],
        default_timeout: int,
        default_work_dir: str,
) -> Dict[str, Any]:
    """Run one parallel command without a PTY so macOS never calls forkpty from a worker thread."""
    if isinstance(command, dict):
        command_text = str(command.get("command", ""))
        command_timeout = command.get("timeout", default_timeout)
        command_work_dir = command.get("work_dir", default_work_dir)
    else:
        command_text = str(command)
        command_timeout = default_timeout
        command_work_dir = default_work_dir

    try:
        completed = subprocess.run(
            command_text,
            cwd=command_work_dir,
            shell=True,
            capture_output=True,
            text=True,
            timeout=command_timeout,
            check=False,
        )
        return {
            "command": command_text,
            "exit_code": completed.returncode,
            "output": completed.stdout,
            "error": completed.stderr,
            "status": "success" if completed.returncode == 0 else "error",
        }
    except subprocess.TimeoutExpired as error:
        return {
            "command": command_text,
            "exit_code": 124,
            "output": error.stdout or "",
            "error": f"Command timed out after {command_timeout} seconds",
            "status": "error",
        }
    except (OSError, ValueError) as error:
        return {
            "command": command_text,
            "exit_code": 1,
            "output": "",
            "error": str(error),
            "status": "error",
        }


def _run_non_interactive_parallel(
        commands: List[Union[str, Dict[str, Any]]],
        timeout: Optional[int],
        work_dir: Optional[str],
) -> Dict[str, Any]:
    """Execute independent commands concurrently without the dependency's thread-unsafe PTY path."""
    default_timeout = timeout if timeout is not None else int(os.environ.get("SHELL_DEFAULT_TIMEOUT", "900"))
    default_work_dir = work_dir or os.getcwd()
    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        results = list(
            executor.map(
                lambda item: _parallel_command_result(item, default_timeout, default_work_dir),
                commands,
            )
        )

    success_count = sum(result["status"] == "success" for result in results)
    error_count = len(results) - success_count
    content = [
        {
            "text": (
                "Execution Summary:\n"
                f"Total commands: {len(results)}\n"
                f"Successful: {success_count}\n"
                f"Failed: {error_count}"
            )
        }
    ]
    content.extend(
        {
            "text": (
                f"Command: {result['command']}\n"
                f"Status: {result['status']}\n"
                f"Exit Code: {result['exit_code']}\n"
                f"Output: {result['output']}\n"
                f"Error: {result['error']}"
            )
        }
        for result in results
    )
    status = "success" if error_count == 0 else "error"
    return {"status": status, "content": content}


@tool
def shell(
        command: Union[str, List[Union[str, Dict[str, Any]]]],
        parallel: bool = False,
        timeout: Optional[int] = None,
        work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Non-interactive shell for real-time command execution and interaction. Features:

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

    non_interactive = os.environ.get("STRANDS_NON_INTERACTIVE", "").lower() == "true"
    if parallel and non_interactive and isinstance(command, list) and len(command) > 1:
        return _run_non_interactive_parallel(command, timeout, work_dir)

    return shell_original(
        command=command,
        parallel=parallel,
        ignore_errors=False,
        timeout=timeout,
        work_dir=work_dir,
    )
