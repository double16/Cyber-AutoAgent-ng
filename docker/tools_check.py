#!/usr/bin/env python3

import os
import sys
import shutil
import subprocess
import yaml


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
        "/tmp/environment.yaml"
    ]

    actual_env_file = None
    for p in paths_to_try:
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            actual_env_file = p
            break

    if not actual_env_file:
        print(f"environment.yaml not found: checked {",".join(paths_to_try)}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(actual_env_file, 'r') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"Error parsing {actual_env_file}: {e}", file=sys.stderr)
        sys.exit(1)

    cyber_tools = data.get('cyber_tools') or {}
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
        cmd = info.get('command') or tool_name
        preference = info.get('preference') or ""
        canary = info.get('canary') or ""

        is_fallback = (preference == "fallback") and not fail_all

        # Check if command exists
        if not shutil.which(cmd):
            if is_fallback:
                missing_fallback.append(tool_name)
            else:
                missing.append(tool_name)
            continue

        # Run canary if provided
        if canary:
            canaries = canary if isinstance(canary, list) else [canary]
            success = False
            for c in canaries:
                try:
                    # Use bash explicitly as in the original script
                    result = subprocess.run(c, shell=True, executable='/bin/bash', stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
                    if result.returncode == 0:
                        success = True
                        break
                except Exception:
                    pass

            if not success:
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
