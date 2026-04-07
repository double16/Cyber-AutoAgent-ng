import shlex
from typing import Union, List, Dict, Any, Optional
import os

from strands import tool
from strands_tools.shell import shell as shell_original

#
# We are overriding the shell tool because models aren't very good at following input schemas.
#

@tool
def shell(
        command: Union[str, List[Union[str, Dict[str, Any]]]],
        parallel: bool = False,
        ignore_errors: bool = False,
        timeout: Optional[int] = None,
        work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Non-interactive shell for real-time command execution and interaction. Features:

    1. Command Formats:
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

    2. Execution Modes:
       • Sequential (default): Commands run in order
       • Parallel: Multiple commands execute simultaneously
       • Error Handling: Stop on error or continue with ignore_errors

    3. Best Practices:
       • Use arrays for multiple commands
       • Set appropriate timeouts
       • Specify work_dir when needed
       • Enable ignore_errors for resilient scripts
       • Use parallel execution for independent commands

    Example Usage:
    1. Simple command:
       {"command": "ls -la"}

    2. Multiple commands:
       {"command": ["mkdir test", "cd test", "touch file.txt"]}

    3. Parallel execution:
       {"command": ["task1", "task2"], "parallel": true}

    4. With error handling:
       {"command": ["risky-command"], "ignore_errors": true}

    5. Custom directory:
       {"command": "npm install", "work_dir": "/app/path"}

    Args:
        command: The shell command(s) to execute interactively. Can be a single command string or array of commands
        parallel: Whether to execute multiple commands in parallel (default: False)
        ignore_errors: Continue execution even if some commands fail (default: False)
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

    return shell_original(
        command=command,
        parallel=parallel,
        ignore_errors=ignore_errors,
        timeout=timeout,
        work_dir=work_dir,
        non_interactive=True
    )
