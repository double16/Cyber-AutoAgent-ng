from pathlib import Path
from unittest.mock import patch

import pytest

from modules.tools.artifact import (
    ARTIFACT_DIRECTORY_LISTING_LIMIT,
    ARTIFACT_MAX_BYTES_PER_READ,
    ARTIFACT_MIN_BYTES_PER_READ,
    ARTIFACT_PAGE_LIMIT_REACHED_MARKER,
    ARTIFACT_READ_POLICY_VIOLATION_MARKER,
    ARTIFACT_READ_SIZE_LIMIT_REACHED_MARKER,
    ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER,
    artifact_max_bytes_for_context_window,
    create_artifact_reader,
    create_bounded_artifact_reader,
)

READ_ARTIFACT = create_artifact_reader(48_000)


def test_artifact_page_budget_scales_with_context_window_and_clamps():
    assert artifact_max_bytes_for_context_window(1) == ARTIFACT_MIN_BYTES_PER_READ
    assert artifact_max_bytes_for_context_window(48_000) == 9_600
    assert artifact_max_bytes_for_context_window(1_000_000) == ARTIFACT_MAX_BYTES_PER_READ


@pytest.mark.parametrize(
    ("context_window_tokens", "error_type"),
    [(0, ValueError), (-1, ValueError), (True, TypeError), ("48000", TypeError)],
)
def test_artifact_page_budget_rejects_invalid_context_window(context_window_tokens, error_type):
    with pytest.raises(error_type, match="positive integer"):
        artifact_max_bytes_for_context_window(context_window_tokens)


def test_read_artifact_returns_bounded_lines(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        result = READ_ARTIFACT("evidence.txt", start_line=2, max_lines=2)

    assert "'content': 'two\\nthree'" in result
    assert "'total_lines': 4" in result


def test_artifact_reader_uses_its_context_window_not_legacy_byte_override(monkeypatch, tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setenv("CYBER_WORKFLOW_ARTIFACT_MAX_BYTES_PER_READ", "64")
    reader = create_artifact_reader(48_000)

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        assert "x" * 100 in reader("evidence.txt")


def test_read_artifact_rejects_oversized_minified_page_without_returning_content(tmp_path: Path):
    artifact = tmp_path / "minified.js"
    oversized_content = "x" * 9_601
    artifact.write_text(oversized_content, encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError, match=ARTIFACT_READ_SIZE_LIMIT_REACHED_MARKER) as error,
    ):
        READ_ARTIFACT("minified.js")

    assert oversized_content not in str(error.value)


def test_read_artifact_reads_oversized_minified_content_by_byte_page(tmp_path: Path):
    artifact = tmp_path / "minified.js"
    artifact.write_text("x" * 9_601, encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = READ_ARTIFACT("minified.js", start_byte=9_500, max_bytes=101)

    assert "'start_byte': 9500" in result
    assert "'end_byte': 9601" in result
    assert "'eof': True" in result


def test_read_artifact_defaults_byte_page_to_zero_offset(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("abcdef", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = READ_ARTIFACT("evidence.txt", max_bytes=3)

    assert "'start_byte': 0" in result
    assert "'end_byte': 3" in result
    assert "'content': 'abc'" in result


def test_read_artifact_rejects_mixed_line_and_byte_pages(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("one", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError, match="byte paging"),
    ):
        READ_ARTIFACT("evidence.txt", start_line=2, start_byte=0, max_bytes=8)


def test_read_artifact_counts_utf8_bytes_not_characters(tmp_path: Path):
    artifact = tmp_path / "unicode.txt"
    artifact.write_text("é" * 4_801, encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError, match=ARTIFACT_READ_SIZE_LIMIT_REACHED_MARKER),
    ):
        READ_ARTIFACT("unicode.txt")


def test_read_artifact_prefers_artifact_directory_for_relative_paths(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evidence.txt").write_text("artifact copy", encoding="utf-8")
    (tmp_path / "evidence.txt").write_text("root copy", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        result = READ_ARTIFACT("evidence.txt")

    assert "artifact copy" in result
    assert "artifact:artifacts/evidence.txt" in result


def test_read_artifact_falls_back_to_operation_root_for_relative_paths(tmp_path: Path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "evidence.txt").write_text("root file", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = READ_ARTIFACT("tools/evidence.txt")

    assert "root file" in result
    assert "artifact:tools/evidence.txt" in result


def test_read_artifact_lists_immediate_files_when_given_a_directory(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "zeta.txt").write_text("zeta", encoding="utf-8")
    (artifacts / "alpha.txt").write_text("alpha", encoding="utf-8")
    nested = artifacts / "nested"
    nested.mkdir()
    (nested / "hidden.txt").write_text("hidden", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = READ_ARTIFACT("artifacts")

    assert "'status': 'directory'" in result
    assert "requires one file" in result
    assert "artifact:artifacts/alpha.txt" in result
    assert "artifact:artifacts/zeta.txt" in result
    assert result.index("artifact:artifacts/alpha.txt") < result.index("artifact:artifacts/zeta.txt")
    assert "hidden.txt" not in result


def test_read_artifact_directory_listing_is_bounded(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for index in range(ARTIFACT_DIRECTORY_LISTING_LIMIT + 1):
        (artifacts / f"evidence-{index:02}.txt").write_text(str(index), encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = READ_ARTIFACT("artifacts")

    assert result.count("artifact:artifacts/evidence-") == ARTIFACT_DIRECTORY_LISTING_LIMIT
    assert "'omitted_file_count': 1" in result


def test_read_artifact_directory_listing_handles_empty_directory(tmp_path: Path):
    (tmp_path / "artifacts").mkdir()

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = READ_ARTIFACT("artifacts")

    assert "'artifact_refs': []" in result
    assert "Retry with one listed artifact_ref" in result


def test_read_artifact_rejects_traversal_and_missing_files(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        with pytest.raises(ValueError, match="outside"):
            READ_ARTIFACT(str(outside))
        with pytest.raises(ValueError, match="does not exist"):
            READ_ARTIFACT("missing.txt")
        with pytest.raises(ValueError, match="outside"):
            READ_ARTIFACT("../../outside.txt")


def test_bounded_reader_rejects_directory_without_consuming_a_read(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    evidence = artifacts / "evidence.txt"
    evidence.write_text("proof", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        reader = create_bounded_artifact_reader(
            max_reads=1,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/evidence.txt"],
        )
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_POLICY_VIOLATION_MARKER):
            reader("artifacts")
        assert "proof" in reader("artifact:artifacts/evidence.txt")


@pytest.mark.parametrize("start_line,max_lines", [(0, 10), (1, 0), (1, 501)])
def test_read_artifact_validates_bounds(tmp_path: Path, start_line: int, max_lines: int):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("one", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError),
    ):
        READ_ARTIFACT(str(artifact), start_line=start_line, max_lines=max_lines)


def test_bounded_reader_enforces_per_agent_limit(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("one", encoding="utf-8")
    reader = create_bounded_artifact_reader(max_reads=1, context_window_tokens=48_000)

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        assert "one" in reader(str(artifact))
        with pytest.raises(RuntimeError, match="read limit"):
            reader(str(artifact))


def test_bounded_reader_defaults_byte_page_to_zero_offset(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    artifact = artifacts / "evidence.txt"
    artifact.write_text("abcdef", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        reader = create_bounded_artifact_reader(
            max_reads=1,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/evidence.txt"],
        )
        result = reader("artifact:artifacts/evidence.txt", max_bytes=3)

    assert "'start_byte': 0" in result
    assert "'end_byte': 3" in result
    assert "'content': 'abc'" in result


def test_bounded_reader_allows_paginated_authorized_artifact_reads(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    evidence = artifacts / "evidence.txt"
    evidence.write_text("\n".join(str(index) for index in range(1, 13)), encoding="utf-8")
    other = artifacts / "other.txt"
    other.write_text("not authorized", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        reader = create_bounded_artifact_reader(
            max_reads=8,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/evidence.txt"],
            max_reads_per_artifact=4,
            max_lines_per_read=2,
        )

        assert "'content': '1\\n2'" in reader("artifact:artifacts/evidence.txt", max_lines=200)
        assert "'content': '3\\n4'" in reader("artifact:artifacts/evidence.txt", start_line=3, max_lines=2)
        assert "'content': '5\\n6'" in reader("artifact:artifacts/evidence.txt", start_line=5, max_lines=2)
        assert "'content': '7\\n8'" in reader("artifact:artifacts/evidence.txt", start_line=7, max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_PAGE_LIMIT_REACHED_MARKER):
            reader("artifact:artifacts/evidence.txt", start_line=9, max_lines=2)

        duplicate_reader = create_bounded_artifact_reader(
            max_reads=4,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/evidence.txt"],
            max_reads_per_artifact=4,
            max_lines_per_read=2,
        )
        duplicate_reader("artifact:artifacts/evidence.txt", max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_POLICY_VIOLATION_MARKER):
            duplicate_reader("artifact:artifacts/evidence.txt", max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_POLICY_VIOLATION_MARKER):
            duplicate_reader("artifact:artifacts/other.txt", max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_POLICY_VIOLATION_MARKER):
            duplicate_reader("artifact:artifacts/missing.txt", max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_POLICY_VIOLATION_MARKER):
            duplicate_reader("artifact:artifacts/evidence.txt", start_line=0, max_lines=2)


def test_bounded_reader_preserves_other_artifacts_after_one_reaches_its_page_limit(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    first = artifacts / "first.txt"
    first.write_text("one\ntwo", encoding="utf-8")
    second = artifacts / "second.txt"
    second.write_text("three\nfour", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        reader = create_bounded_artifact_reader(
            max_reads=2,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/first.txt", "artifact:artifacts/second.txt"],
            max_reads_per_artifact=1,
        )

        assert "one" in reader("artifact:artifacts/first.txt")
        with pytest.raises(RuntimeError, match=ARTIFACT_PAGE_LIMIT_REACHED_MARKER):
            reader("artifact:artifacts/first.txt", start_line=2)
        assert "three" in reader("artifact:artifacts/second.txt")
        with pytest.raises(RuntimeError, match=ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER):
            reader("artifact:artifacts/second.txt", start_line=2)


def test_bounded_reader_rejects_oversized_page_without_consuming_successful_read_budget(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    oversized = artifacts / "minified.js"
    oversized.write_text("x" * 9_601, encoding="utf-8")
    small = artifacts / "evidence.txt"
    small.write_text("proof", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        reader = create_bounded_artifact_reader(
            max_reads=1,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/minified.js", "artifact:artifacts/evidence.txt"],
        )

        with pytest.raises(RuntimeError, match=ARTIFACT_READ_SIZE_LIMIT_REACHED_MARKER):
            reader("artifact:artifacts/minified.js")
        assert "proof" in reader("artifact:artifacts/evidence.txt")


def test_bounded_reader_returns_large_artifact_digest_without_consuming_read_budget(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    oversized = artifacts / "minified.js"
    oversized.write_text("x" * 9_601, encoding="utf-8")
    small = artifacts / "evidence.txt"
    small.write_text("proof", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        reader = create_bounded_artifact_reader(
            max_reads=1,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/evidence.txt"],
            omitted_large_artifact_sizes={"artifact:artifacts/minified.js": 9_601},
        )

        with pytest.raises(RuntimeError, match=r"Artifact is 9601 bytes") as error:
            reader("artifact:artifacts/minified.js")
        assert "acceptance summary and review digest" in str(error.value)
        assert "proof" in reader("artifact:artifacts/evidence.txt")
