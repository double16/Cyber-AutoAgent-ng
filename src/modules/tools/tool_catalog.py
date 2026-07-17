import json
import subprocess
import re
from functools import lru_cache
from pathlib import Path
from typing import List, Any, Dict, Optional

import yaml
from strands import tool, Agent

from modules.config.system import environment, get_logger

logger = get_logger("Tools.Catalog")


@lru_cache()
def _get_cyber_tools() -> Dict[str, Any]:
    env_path = Path(environment.__file__).with_name("environment.yaml")
    with env_path.open("r", encoding="utf-8") as f:
        env_config = yaml.safe_load(f) or {}

    return env_config.get("cyber_tools", {})


def get_cyber_tools_by_caps(available: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Returns command line tools:
        capability -> preferred|fallback -> tools (list[str])
    """
    result = {}
    cyber_tools = _get_cyber_tools()
    for tool_name in cyber_tools:
        if tool_name not in available:
            continue
        tool_cfg = cyber_tools.get(tool_name) or {}
        real_command = tool_cfg.get("command", tool_name)

        pref_raw = tool_cfg.get("preference") or "fallback"
        pref_raw = str(pref_raw).strip().lower()
        pref = "preferred" if pref_raw.startswith("p") else "fallback"

        caps = tool_cfg.get("caps") or []
        if isinstance(caps, str):
            caps = [caps]
        for cap in caps:
            if cap not in result:
                result[cap] = {}
            cap_dict = result.get(cap)
            if pref not in cap_dict:
                cap_dict[pref] = []
            pref_list = cap_dict.get(pref)
            pref_list.append(real_command)
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
        preference = str(tool_cfg.get("preference") or "fallback").strip().lower()
        preference = "preferred" if preference.startswith("p") else "fallback"
        specs.append(
            {
                "command": command,
                "description": str(tool_cfg.get("description") or ""),
                "capabilities": [str(capability) for capability in capabilities],
                "shell_preference": preference,
            }
        )
        seen_commands.add(command)
    return specs


@lru_cache(maxsize=200)
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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.stdout is None and result.stderr is None:
                continue
            result_str = str(result.stdout) + str(result.stderr)
            if len(result_str) > 30:
                return result_str
    except Exception as e:
        logger.warning(f"Getting help text for {command}", exc_info=e)
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
        - `shell_preference` ranks command-line programs only against other command-line programs.

        Args:
            keywords:
                - None/empty: return full catalog.
                - 2–6 terms: capability + task (e.g., `idor validate`, `jwt decode`, `web_crawling`, `xss_testing`).
                - 1 term: tool/command name.
        """
        separator = "=" * 80
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
{separator}
name: {tool_name}

{tool_desc}

{separator}
"""

        found_cyber_tools = []
        if shell_found and shell_commands and (not specific_tool or keywords[0] in shell_commands):
            catalog += """
# COMMAND LINE PROGRAMS

These are command-line programs invoked through the **shell** tool.
"""
            cyber_tools = _get_cyber_tools()
            for shell_command in shell_commands:
                if specific_tool and shell_command != keywords[0]:
                    continue
                tool_cfg = (cyber_tools.get(shell_command) or {})
                real_command = tool_cfg.get("command", shell_command)
                help_commands = tool_cfg.get("help", [])
                if not isinstance(help_commands, list):
                    help_commands = [help_commands]
                description = tool_cfg.get("description", "")
                preference = tool_cfg.get("preference", "")
                caps = tool_cfg.get("caps") or []
                if isinstance(caps, str):
                    caps = [caps]
                if keywords and not specific_tool:
                    desc_l = str(description).lower()
                    caps_l = [str(cap).lower() for cap in caps]
                    if not any(
                            [w in shell_command.lower() or w in real_command.lower() or w in desc_l or w in caps_l for w in keywords]):
                        continue
                found_cyber_tools.append(real_command)

                catalog += f"""
{separator}
command: {real_command}
capabilities: {", ".join(caps)}
shell_preference: {preference}

{description}

{_get_shell_command_help(real_command, json.dumps(help_commands))}

{separator}
"""
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
