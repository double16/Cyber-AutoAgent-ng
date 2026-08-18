import json
import re
import shlex
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import List, Any, Dict, Optional

import yaml
from strands import tool, Agent

from modules.config.system import environment, get_logger

logger = get_logger("Tools.Catalog")
_DIAGNOSTIC_EXECUTABLES = {"command", "find", "ls", "stat", "test", "type", "which"}
_SEPARATOR = "=" * 80
_EXECUTION_CAPABILITIES = frozenset({"analyze", "compare", "crawl", "enumerate", "execute", "request"})


@lru_cache()
def _get_cyber_tools() -> Dict[str, Any]:
    env_path = Path(environment.__file__).with_name("environment.yaml")
    with env_path.open("r", encoding="utf-8") as f:
        env_config = yaml.safe_load(f) or {}

    return env_config.get("cyber_tools", {})


def get_cyber_tools_by_caps(available: List[str]) -> Dict[str, List[str]]:
    """
    Returns command line tools:
        capability -> tools (list[str])
    """
    result = {}
    cyber_tools = _get_cyber_tools()
    for tool_name in cyber_tools:
        if tool_name not in available:
            continue
        tool_cfg = cyber_tools.get(tool_name) or {}
        real_command = tool_cfg.get("command", tool_name)

        caps = tool_cfg.get("caps") or []
        if isinstance(caps, str):
            caps = [caps]
        for cap in caps:
            result.setdefault(cap, []).append(real_command)
    return result


def get_shell_command_specs(available: List[str]) -> List[Dict[str, Any]]:
    """Return compact metadata for installed command-line programs."""

    cyber_tools = _get_cyber_tools()
    specs = []
    seen_commands = set()
    for tool_name in available:
        tool_cfg = cyber_tools.get(tool_name) or {}
        command = str(tool_cfg.get("command") or tool_name).strip()
        if not command or command in seen_commands:
            continue
        capabilities = tool_cfg.get("caps") or []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        specs.append(
            {
                "command": command,
                "description": str(tool_cfg.get("description") or ""),
                "capabilities": [str(capability) for capability in capabilities],
            }
        )
        seen_commands.add(command)
    return specs


def get_shell_command_execution_capabilities(executable: str) -> frozenset[str]:
    """Return canonical receipt capabilities for a configured shell executable.

    Matches either the YAML tool key or its command override. Selection-oriented
    ``caps`` deliberately remain separate from controller receipt metadata.
    """

    normalized = str(executable or "").strip().casefold()
    if not normalized:
        return frozenset()
    for tool_name, raw_config in _get_cyber_tools().items():
        config = raw_config if isinstance(raw_config, dict) else {}
        command = str(config.get("command") or tool_name).strip()
        if normalized not in {str(tool_name).casefold(), command.casefold()}:
            continue
        capabilities = config.get("execution_caps") or []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        return frozenset(
            str(capability).strip().casefold()
            for capability in capabilities
            if str(capability).strip().casefold() in _EXECUTION_CAPABILITIES
        )
    return frozenset()


def remove_shell_command(available: List[str], executable: str) -> List[str]:
    """Remove configured tool names that resolve to an unavailable executable."""

    executable = str(executable or "").strip()
    removed = []
    for tool_name in list(available):
        real_command, _, _ = _shell_command_config(str(tool_name))
        if executable not in {str(tool_name), real_command}:
            continue
        available.remove(tool_name)
        removed.append(str(tool_name))
    return removed


def get_shell_command_alternatives(executable: str, available: List[str]) -> List[str]:
    """Return available commands sharing at least one declared capability."""

    executable = str(executable or "").strip()
    specs = get_shell_command_specs(available)
    failed = next((spec for spec in specs if spec["command"] == executable), None)
    if failed is None:
        cyber_tools = _get_cyber_tools()
        for tool_name, config in cyber_tools.items():
            if str((config or {}).get("command") or tool_name) == executable:
                caps = (config or {}).get("caps") or []
                failed = {"capabilities": [caps] if isinstance(caps, str) else caps}
                break
    failed_capabilities = set((failed or {}).get("capabilities") or [])
    if not failed_capabilities:
        return []
    return [
        str(spec["command"])
        for spec in specs
        if spec["command"] != executable
        and failed_capabilities.intersection(spec.get("capabilities") or [])
    ]


@lru_cache(maxsize=1000)
def _get_shell_command_help(command: str, help_commands_json: str) -> str:
    """
    Get the command help text by attempting `--help` or `-h`.
    :param command: The name of the command
    :param help_commands_json: JSON array of full command(s) that provide help. A string to allow lru_cache to be used.
    :return:
    """
    try:
        help_commands = json.loads(help_commands_json)
        for cmd in [
            *help_commands,
            f"{command} --help",
            f"{command} -h",
            command,
        ]:
            if not cmd:
                continue
            argv = shlex.split(str(cmd))
            if not argv:
                continue
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            if result.stdout is None and result.stderr is None:
                continue
            result_str = str(result.stdout) + str(result.stderr)
            if len(result_str) > 30:
                return result_str
    except Exception as e:
        logger.warning(f"Getting help text for {command}", exc_info=e)
    return ""


def _shell_command_config(shell_command: str) -> tuple[str, Dict[str, Any], List[str]]:
    cyber_tools = _get_cyber_tools()
    tool_cfg = cyber_tools.get(shell_command) or {}
    real_command = str(tool_cfg.get("command") or shell_command).strip()
    help_commands = tool_cfg.get("help", [])
    if not isinstance(help_commands, list):
        help_commands = [help_commands]
    return real_command, tool_cfg, [str(item) for item in help_commands if str(item).strip()]


def _render_shell_command_help(shell_command: str, *, require_help: bool) -> str:
    real_command, tool_cfg, help_commands = _shell_command_config(shell_command)
    if not real_command:
        return ""
    description = str(tool_cfg.get("description", ""))
    caps = tool_cfg.get("caps") or []
    if isinstance(caps, str):
        caps = [caps]
    help_text = _get_shell_command_help(real_command, json.dumps(help_commands))
    if require_help and not help_text.strip():
        return ""
    return f"""
{_SEPARATOR}
command: {real_command}
capabilities: {", ".join(str(capability) for capability in caps)}

{description}

{help_text}

{_SEPARATOR}
"""


def get_shell_command_help_context(command: str, available: List[str]) -> str:
    """Return full tool-catalog command help for one available shell executable."""

    command = str(command or "").strip()
    if not command or command in _DIAGNOSTIC_EXECUTABLES:
        return ""
    for shell_command in available:
        shell_command_text = str(shell_command or "").strip()
        real_command, _, _ = _shell_command_config(shell_command_text)
        if command in {shell_command_text, real_command}:
            return _render_shell_command_help(shell_command_text, require_help=True)
    return ""


def tool_catalog_wrapper(agent: Agent, shell_commands: List[str]):
    """
    Create a full catalog of all available tools.
    :param agent: agent from which tools will be gathered
    :param shell_commands: available shell commands
    :return: tool
    """

    @tool(name="tool_catalog")
    def tool_catalog(keywords: Optional[str] = None) -> str:
        """
        List available tools to pick the best next tool.

        Call when:
        - Unsure which tool fits (confidence <80%).
        - About to use `shell`, `http_request`, or `python_repl` for recon/fuzz/scan/validate/crack/crawl/parse.
        - Need a tool’s args/schema.
        - User asks “what tool can do X?”.

        How:
        - Search by keywords and prefer native agent tools over command-line programs for overlapping capabilities.
        - Use a command-line program only for a required additional capability or a concrete native-tool limitation.
        Args:
            keywords:
                - None/empty: return full catalog.
                - 2–6 terms: capability + task (e.g., `idor validate`, `jwt decode`, `web_crawling`, `xss_testing`).
                - 1 term: tool/command name.
        """
        parts = re.split(r"[\s,;]+", (keywords or ""))
        keywords = [w.strip().lower() for w in parts if w.strip()]
        found_tools = []
        catalog = ""
        all_tools = agent.tool_registry.get_all_tools_config()
        specific_tool = len(keywords) == 1 and (keywords[0] in all_tools or keywords[0] in shell_commands)
        shell_found = False
        for tool_name, tool_spec in all_tools.items():
            if tool_name == "shell":
                shell_found = True
            if specific_tool and tool_name != keywords[0]:
                continue
            if keywords:
                if not any([w in tool_name.lower() or w in tool_spec.get("description", "").lower() for w in keywords]):
                    continue
            found_tools.append(tool_name)

            tool_desc = tool_spec.get("description", "")
            if len(tool_desc) > 200:
                tool_desc = tool_desc[:200] + " ..."
            catalog += f"""
{_SEPARATOR}
name: {tool_name}

{tool_desc}

{_SEPARATOR}
"""

        found_cyber_tools = []
        if shell_found and shell_commands and (not specific_tool or keywords[0] in shell_commands):
            catalog += """
# COMMAND LINE PROGRAMS

These are command-line programs invoked through the **shell** tool.
"""
            for shell_command in shell_commands:
                if specific_tool and shell_command != keywords[0]:
                    continue
                real_command, tool_cfg, _ = _shell_command_config(shell_command)
                description = str(tool_cfg.get("description", ""))
                caps = tool_cfg.get("caps") or []
                if isinstance(caps, str):
                    caps = [caps]
                if keywords and not specific_tool:
                    desc_l = description.lower()
                    caps_l = [str(cap).lower() for cap in caps]
                    if not any(
                        [
                            w in shell_command.lower()
                            or w in real_command.lower()
                            or w in desc_l
                            or w in caps_l
                            for w in keywords
                        ]
                    ):
                        continue
                found_cyber_tools.append(real_command)

                catalog += _render_shell_command_help(shell_command, require_help=False)
        if len(found_tools) + len(found_cyber_tools) == 0:
            return f"**NO RESULTS**\nkeywords: {' '.join(keywords)}"

        prologue = """
# TOOL CATALOG

"""
        if len(found_tools) + len(found_cyber_tools) > 1:
            if len(found_tools) > 0:
                prologue += f"""**Tools found**: {','.join(found_tools)}\n"""
            if len(found_cyber_tools) > 0:
                prologue += f"""**Command line tools found**: {','.join(found_cyber_tools)}\n"""

        return prologue + catalog

    return tool_catalog
