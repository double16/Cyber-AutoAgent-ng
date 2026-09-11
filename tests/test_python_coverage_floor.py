import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_coverage_checker():
    path = Path(__file__).parents[1] / ".github" / "scripts" / "check-python-coverage-floor.py"
    spec = importlib.util.spec_from_file_location("python_coverage_floor", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


checker = _load_coverage_checker()


def _coverage_data(*, branch_coverage=True, files=None):
    return {
        "meta": {"branch_coverage": branch_coverage},
        "files": files
        or {
            "src/example.py": {
                "summary": {
                    "covered_lines": 8,
                    "num_statements": 10,
                    "covered_branches": 4,
                    "num_branches": 5,
                }
            }
        },
    }


def test_evaluate_coverage_accepts_line_and_branch_thresholds():
    assert checker.evaluate_coverage(_coverage_data(), 80, 80) == []


def test_evaluate_coverage_reports_line_and_branch_failures():
    data = _coverage_data(
        files={
            "src/example.py": {
                "summary": {
                    "covered_lines": 7,
                    "num_statements": 10,
                    "covered_branches": 3,
                    "num_branches": 5,
                }
            }
        }
    )

    assert checker.evaluate_coverage(data, 80, 80) == ["src/example.py: line 70.0%, branch 60.0%"]


def test_evaluate_coverage_accepts_files_without_executable_branches():
    data = _coverage_data(
        files={
            "src/example.py": {
                "summary": {
                    "covered_lines": 8,
                    "num_statements": 10,
                    "covered_branches": 0,
                    "num_branches": 0,
                }
            }
        }
    )

    assert checker.evaluate_coverage(data, 80, 80) == []


def test_evaluate_coverage_rejects_missing_branch_instrumentation():
    with pytest.raises(ValueError, match="branch coverage"):
        checker.evaluate_coverage(_coverage_data(branch_coverage=False), 80, 80)


def test_evaluate_coverage_rejects_empty_coverage_reports():
    with pytest.raises(ValueError, match="no measured Python files"):
        checker.evaluate_coverage({"meta": {"branch_coverage": True}, "files": {}}, 80, 80)


def test_evaluate_coverage_reports_changed_file_absent_from_report():
    assert checker.evaluate_coverage(_coverage_data(), 80, 80, {"src/new_module.py"}) == [
        "src/new_module.py: absent from the coverage report"
    ]


def test_changed_python_files_filters_to_source_python_files(monkeypatch, tmp_path):
    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="src/changed.py\nsrc/changed.ts\ntests/test_changed.py\nsrc/new_module.py\n",
            stderr="",
        ),
    )

    assert checker.changed_python_files("base-sha", tmp_path) == {"src/changed.py", "src/new_module.py"}


def test_changed_python_files_rejects_invalid_base_revision(monkeypatch, tmp_path):
    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=128, stdout="", stderr="bad revision"),
    )

    with pytest.raises(ValueError, match="Unable to determine"):
        checker.changed_python_files("missing", tmp_path)
