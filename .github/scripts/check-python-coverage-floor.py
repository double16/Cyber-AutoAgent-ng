#!/usr/bin/env python3
"""Fail when any measured Python source file is below a coverage floor."""

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--min", type=float, default=50.0)
    args = parser.parse_args()

    data = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    failures = []
    for filename, file_data in sorted(data.get("files", {}).items()):
        summary = file_data.get("summary", {})
        percent = float(summary.get("percent_covered", 100.0))
        if percent < args.min:
            failures.append((filename, percent))

    if not failures:
        print(f"All measured Python files have at least {args.min:.1f}% coverage.")
        return 0

    print(f"Python files below {args.min:.1f}% coverage:")
    for filename, percent in failures:
        print(f"  {filename}: {percent:.1f}%")
    return 1


if __name__ == "__main__":
    sys.exit(main())
