#!/usr/bin/env python3
"""Enforce per-file Python statement and branch coverage floors."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _normalise_path(filename: str, repository_root: Path) -> str:
    """Return a coverage-report path relative to the repository root."""
    path = Path(filename)
    if path.is_absolute():
        try:
            path = path.relative_to(repository_root)
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def changed_python_files(base_ref: str, repository_root: Path) -> set[str]:
    """Return added, copied, modified, and renamed Python files under ``src``."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", base_ref, "HEAD", "--", "src"],
        cwd=repository_root,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        raise ValueError(f"Unable to determine changed Python files from {base_ref!r}: {result.stderr.strip()}")

    return {
        filename
        for filename in result.stdout.splitlines()
        if filename.startswith("src/") and filename.endswith(".py")
    }


def _percentage(covered: object, total: object, metric_name: str, filename: str) -> float:
    """Calculate one coverage percentage and reject malformed report data."""
    if not isinstance(covered, int) or not isinstance(total, int) or covered < 0 or total < 0:
        raise ValueError(f"Invalid {metric_name} coverage data for {filename}")
    return 100.0 if total == 0 else (covered / total) * 100.0


def evaluate_coverage(
    data: dict[str, object], line_minimum: float, branch_minimum: float, selected_files: set[str] | None = None
) -> list[str]:
    """Return coverage failures for every selected source file."""
    meta = data.get("meta")
    if not isinstance(meta, dict) or meta.get("branch_coverage") is not True:
        raise ValueError("Coverage report does not contain branch coverage data. Run pytest with --cov-branch.")

    files = data.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Coverage report contains no measured Python files.")

    failures = []
    measured_files = set(files)
    filenames = sorted(selected_files if selected_files is not None else measured_files)
    for filename in filenames:
        file_data = files.get(filename)
        if not isinstance(file_data, dict):
            failures.append(f"{filename}: absent from the coverage report")
            continue

        summary = file_data.get("summary")
        if not isinstance(summary, dict):
            raise ValueError(f"Coverage report has no summary for {filename}")

        line_percent = _percentage(summary.get("covered_lines"), summary.get("num_statements"), "line", filename)
        branch_percent = _percentage(
            summary.get("covered_branches"), summary.get("num_branches"), "branch", filename
        )
        deficiencies = []
        if line_percent < line_minimum:
            deficiencies.append(f"line {line_percent:.1f}%")
        if branch_percent < branch_minimum:
            deficiencies.append(f"branch {branch_percent:.1f}%")
        if deficiencies:
            failures.append(f"{filename}: {', '.join(deficiencies)}")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--min", dest="line_minimum", type=float, default=80.0)
    parser.add_argument("--branch-min", type=float, default=80.0)
    parser.add_argument("--changed-from", help="Git base revision used to select changed src Python files")
    args = parser.parse_args()

    try:
        data = json.loads(args.coverage_json.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Coverage report must contain a JSON object.")
        repository_root = Path.cwd().resolve()
        selected_files = None
        if args.changed_from:
            changed_files = changed_python_files(args.changed_from, repository_root)
            files = data.get("files")
            if not isinstance(files, dict):
                raise ValueError("Coverage report contains no measured Python files.")
            coverage_files = {
                _normalise_path(filename, repository_root): filename for filename in files
            }
            selected_files = {coverage_files.get(filename, filename) for filename in changed_files}
        failures = evaluate_coverage(data, args.line_minimum, args.branch_min, selected_files)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Python coverage check failed: {error}")
        return 1

    if not failures:
        print(
            "All selected Python files have at least "
            f"{args.line_minimum:.1f}% line and {args.branch_min:.1f}% branch coverage."
        )
        return 0

    print(
        "Python files below the coverage floor "
        f"(line {args.line_minimum:.1f}%, branch {args.branch_min:.1f}%):"
    )
    for failure in failures:
        print(f"  {failure}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
