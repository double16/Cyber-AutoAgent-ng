#!/usr/bin/env python3

import os
import re
import shutil
import subprocess
import sys

import yaml


STARTUP_FAILURE_PATTERNS = tuple(
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


def probe_command(tool_path, canary):
    """Return whether an optional, declarative executable canary succeeds."""

    if canary is None:
        return True
    if not isinstance(canary, dict):
        return False
    args = canary.get("args")
    timeout_seconds = canary.get("timeout_seconds", 5)
    accepted_exit_codes = canary.get("accepted_exit_codes", [0])
    if not isinstance(args, list) or not all(isinstance(arg, str) and arg for arg in args):
        return False
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 60:
        return False
    if not isinstance(accepted_exit_codes, list) or not accepted_exit_codes or not all(
        isinstance(code, int) and not isinstance(code, bool) for code in accepted_exit_codes
    ):
        return False
    try:
        result = subprocess.run(
            [tool_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    return result.returncode in set(accepted_exit_codes) and not any(
        pattern.search(output) for pattern in STARTUP_FAILURE_PATTERNS
    )


def main():
    fail_all = False
    if len(sys.argv) > 1:
        if sys.argv[1] == "--all":
            fail_all = True
        elif sys.argv[1] == "--help" or sys.argv[1] == "-h":
            print(f"Usage: {sys.argv[0]} [--all]")
            sys.exit(0)
        else:
            print(f"Usage: {sys.argv[0]} [--all]", file=sys.stderr)
            sys.exit(1)

    env_file = "src/modules/config/system/environment.yaml"
    paths_to_try = [
        env_file,
        os.path.join("/app", env_file),
        "/tmp/environment.yaml",
    ]

    actual_env_file = None
    for p in paths_to_try:
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            actual_env_file = p
            break

    if not actual_env_file:
        print(f"environment.yaml not found: checked {','.join(paths_to_try)}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(actual_env_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"Error parsing {actual_env_file}: {e}", file=sys.stderr)
        sys.exit(1)

    cyber_tools = data.get("cyber_tools") or {}
    if not cyber_tools:
        print("No tools found", file=sys.stderr)
        sys.exit(1)

    missing = []
    broken = []
    missing_fallback = []
    broken_fallback = []
    count = 0

    for tool_name, info in cyber_tools.items():
        if info is None:
            info = {}

        count += 1
        cmd = info.get("command") or tool_name
        preference = info.get("preference") or ""
        canary = info.get("canary")

        is_fallback = (preference == "fallback") and not fail_all

        # Check if command exists
        tool_path = shutil.which(cmd)
        if not tool_path:
            if is_fallback:
                missing_fallback.append(tool_name)
            else:
                missing.append(tool_name)
            continue

        if not probe_command(tool_path, canary):
            if is_fallback:
                broken_fallback.append(tool_name)
            else:
                broken.append(tool_name)

    if missing:
        print("Missing tools:", file=sys.stderr)
        for t in missing:
            print(f"  {t}", file=sys.stderr)

    if broken:
        print("Broken tools:", file=sys.stderr)
        for t in broken:
            print(f"  {t}", file=sys.stderr)

    if missing_fallback:
        print("Missing fallback tools:", file=sys.stderr)
        for t in missing_fallback:
            print(f"  {t}", file=sys.stderr)

    if broken_fallback:
        print("Broken fallback tools:", file=sys.stderr)
        for t in broken_fallback:
            print(f"  {t}", file=sys.stderr)

    if missing or broken:
        sys.exit(1)

    print(f"{count} tools in {actual_env_file} found.")


if __name__ == "__main__":
    main()
