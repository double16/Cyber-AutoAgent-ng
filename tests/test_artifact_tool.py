import ast
import asyncio
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from modules.handlers.tool_router import ToolRouterHook
from modules.tools.artifact import (
    ARTIFACT_DIRECTORY_LISTING_LIMIT,
    ARTIFACT_MAX_BYTES_PER_READ,
    ARTIFACT_MIN_BYTES_PER_READ,
    ARTIFACT_READ_OVERLAP_GUARD_MARKER,
    ARTIFACT_READ_POLICY_VIOLATION_MARKER,
    ARTIFACT_READ_REPEAT_GUARD_MARKER,
    ARTIFACT_READ_SIZE_LIMIT_REACHED_MARKER,
    artifact_max_bytes_for_context_window,
    create_artifact_reader,
    create_bounded_artifact_reader,
    resolve_tool_result_max_chars,
)

READ_ARTIFACT = create_artifact_reader(48_000)


def test_artifact_page_budget_scales_with_context_window_and_clamps():
    assert artifact_max_bytes_for_context_window(1) == ARTIFACT_MIN_BYTES_PER_READ
    assert artifact_max_bytes_for_context_window(48_000) == 19_200
    assert artifact_max_bytes_for_context_window(1_000_000) == ARTIFACT_MAX_BYTES_PER_READ


def test_tool_result_max_chars_converts_context_tokens_to_characters():
    assert resolve_tool_result_max_chars(40_000) == 16_000
    assert resolve_tool_result_max_chars(100_000) == 30_000
    assert resolve_tool_result_max_chars(40_000, "12000") == 12_000
    assert resolve_tool_result_max_chars(40_000, "invalid") == 16_000
    assert resolve_tool_result_max_chars(40_000, "0") == 16_000


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


def test_read_artifact_serialized_line_page_stays_below_router_limit(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    lines = [f"line-{index}: {'x' * 40}" for index in range(1, 301)]
    artifact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reader = create_artifact_reader(40_000, max_output_chars=4_000)

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        first = ast.literal_eval(reader("evidence.txt", max_lines=200))
        second = ast.literal_eval(reader("evidence.txt", start_line=first["end_line"] + 1, max_lines=200))

    assert len(str(first)) <= 4_000
    assert first["end_line"] < 200
    assert second["start_line"] == first["end_line"] + 1
    assert second["content"].startswith(lines[first["end_line"]])


def test_router_does_not_truncate_reader_result_within_its_ceiling(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("x" * 10_270, encoding="utf-8")
    reader = create_artifact_reader(40_000, max_output_chars=16_000)

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        output = reader("evidence.txt", max_bytes=10_270)

    result = {"content": [{"text": output}]}
    event = types.SimpleNamespace(result=result, tool_use={"name": "read_artifact"})
    hook = ToolRouterHook(max_result_chars=16_000, artifact_threshold=16_000)
    asyncio.run(hook._truncate_large_results_async(event))

    assert len(output) <= 16_000
    assert event.result is result


def test_read_artifact_serialized_byte_page_stays_below_router_limit(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("x" * 10_000, encoding="utf-8")
    reader = create_artifact_reader(40_000, max_output_chars=1_000)

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        first = ast.literal_eval(reader("evidence.txt", start_byte=0, max_bytes=10_000))
        second = ast.literal_eval(
            reader("evidence.txt", start_byte=first["next_start_byte"], max_bytes=10_000 - first["next_start_byte"])
        )

    assert len(str(first)) <= 1_000
    assert first["next_start_byte"] is not None
    assert second["start_byte"] == first["next_start_byte"]
    assert second["content"]


def test_artifact_reader_uses_its_context_window_not_legacy_byte_override(monkeypatch, tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setenv("CYBER_WORKFLOW_ARTIFACT_MAX_BYTES_PER_READ", "64")
    reader = create_artifact_reader(48_000)

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        assert "x" * 100 in reader("evidence.txt")


def test_read_artifact_rejects_oversized_minified_page_without_returning_content(tmp_path: Path):
    artifact = tmp_path / "minified.js"
    oversized_content = "x" * 19_201
    artifact.write_text(oversized_content, encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError, match=ARTIFACT_READ_SIZE_LIMIT_REACHED_MARKER) as error,
    ):
        READ_ARTIFACT("minified.js")

    assert oversized_content not in str(error.value)
    assert "max_lines" in str(error.value)
    assert "start_byte" in str(error.value)
    assert "max_bytes" in str(error.value)


def test_read_artifact_reads_oversized_minified_content_by_byte_page(tmp_path: Path):
    artifact = tmp_path / "minified.js"
    artifact.write_text("x" * 19_201, encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = READ_ARTIFACT("minified.js", start_byte=19_100, max_bytes=101)

    assert "'start_byte': 19100" in result
    assert "'end_byte': 19201" in result
    assert "'eof': True" in result


def test_read_artifact_defaults_byte_page_to_zero_offset(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("abcdef", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = READ_ARTIFACT("evidence.txt", max_bytes=3)

    assert "'start_byte': 0" in result
    assert "'end_byte': 3" in result
    assert "'content': 'abc'" in result


def test_read_artifact_combines_byte_and_line_limits_with_contiguous_pages(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    lines = [f"line-{index}" for index in range(1, 36)]
    content = "\n".join(lines) + "\n"
    artifact.write_text(content, encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        first = ast.literal_eval(
            READ_ARTIFACT("evidence.txt", start_byte=0, max_bytes=16_000, max_lines=30)
        )
        second = ast.literal_eval(
            READ_ARTIFACT(
                "evidence.txt",
                start_byte=first["next_start_byte"],
                max_bytes=16_000,
                max_lines=30,
            )
        )

    assert first["content"] == "\n".join(lines[:30]) + "\n"
    assert first["next_start_byte"] == len(first["content"].encode("utf-8"))
    assert second["content"] == "\n".join(lines[30:]) + "\n"
    assert first["content"] + second["content"] == content


def test_read_artifact_allows_start_line_only_at_zero_byte_offset(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("one\ntwo\nthree\n", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(ValueError, match="start_line requires"),
    ):
        READ_ARTIFACT("evidence.txt", start_line=2, start_byte=1, max_bytes=8)

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = ast.literal_eval(READ_ARTIFACT("evidence.txt", start_line=2, max_bytes=8, max_lines=2))

    assert result["start_byte"] == 0
    assert result["content"] == "two\n"
    assert result["next_start_byte"] == len(b"one\ntwo\n")


def test_read_artifact_counts_utf8_bytes_not_characters(tmp_path: Path):
    artifact = tmp_path / "unicode.txt"
    artifact.write_text("é" * 9_601, encoding="utf-8")

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
        limit = ast.literal_eval(reader(str(artifact), start_line=2))

    assert limit["status"] == "not_directly_readable"
    assert limit["reason"] == "evaluator_read_budget_exhausted"


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

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        line_boundary_reader = create_bounded_artifact_reader(
            max_reads=1,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/evidence.txt"],
        )
        line_limited_result = line_boundary_reader(
            "artifact:artifacts/evidence.txt",
            start_byte=0,
            max_bytes=3,
            max_lines=1,
        )

    assert "'content': 'abc'" in line_limited_result
    with pytest.raises(RuntimeError, match=ARTIFACT_READ_POLICY_VIOLATION_MARKER):
        line_boundary_reader(
            "artifact:artifacts/evidence.txt",
            start_line=1,
            start_byte=1,
            max_bytes=3,
            max_lines=1,
        )


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
        page_limit = ast.literal_eval(reader("artifact:artifacts/evidence.txt", start_line=9, max_lines=2))
        assert page_limit["status"] == "not_directly_readable"
        assert page_limit["reason"] == "artifact_page_budget_exhausted"
        assert page_limit["content"] == ""

        duplicate_reader = create_bounded_artifact_reader(
            max_reads=4,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/evidence.txt"],
            max_reads_per_artifact=4,
            max_lines_per_read=2,
        )
        duplicate_reader("artifact:artifacts/evidence.txt", max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_OVERLAP_GUARD_MARKER):
            duplicate_reader("artifact:artifacts/evidence.txt", max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_OVERLAP_GUARD_MARKER):
            duplicate_reader("artifact:artifacts/evidence.txt", max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_POLICY_VIOLATION_MARKER):
            duplicate_reader("artifact:artifacts/other.txt", max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_POLICY_VIOLATION_MARKER):
            duplicate_reader("artifact:artifacts/missing.txt", max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_OVERLAP_GUARD_MARKER):
            duplicate_reader("artifact:artifacts/evidence.txt", start_line=0, max_lines=2)


def test_bounded_reader_allows_distinct_byte_pages_for_large_artifact(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    artifact = artifacts / "minified.js"
    artifact.write_text("x" * 19_201, encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        reader = create_bounded_artifact_reader(
            max_reads=2,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/minified.js"],
            omitted_large_artifact_sizes={"artifact:artifacts/minified.js": 19_201},
            max_reads_per_artifact=2,
        )
        first = reader("artifact:artifacts/minified.js", max_bytes=19_200)
        second = reader("artifact:artifacts/minified.js", start_byte=19_200, max_bytes=19_200)

    assert "'start_byte': 0" in first
    assert "'next_start_byte': 19200" in first
    assert "'start_byte': 19200" in second
    assert "'eof': True" in second


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

        assert "one" in reader("artifact:artifacts/first.txt", max_lines=1)
        page_limit = ast.literal_eval(reader("artifact:artifacts/first.txt", start_line=2))
        assert page_limit["reason"] == "artifact_page_budget_exhausted"
        assert "three" in reader("artifact:artifacts/second.txt", max_lines=1)
        total_limit = ast.literal_eval(reader("artifact:artifacts/second.txt", start_line=2))
        assert total_limit["reason"] == "evaluator_read_budget_exhausted"


def test_bounded_reader_rejects_overlapping_line_and_byte_pages(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    lines = artifacts / "lines.txt"
    lines.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    bytes_artifact = artifacts / "bytes.txt"
    bytes_artifact.write_text("abcdefgh", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        line_reader = create_bounded_artifact_reader(
            max_reads=2,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/lines.txt"],
        )
        assert "one" in line_reader("artifact:artifacts/lines.txt", max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_OVERLAP_GUARD_MARKER):
            line_reader("artifact:artifacts/lines.txt", start_line=2, max_lines=2)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_OVERLAP_GUARD_MARKER):
            line_reader("artifact:artifacts/lines.txt", start_line=3, max_lines=2)

        byte_reader = create_bounded_artifact_reader(
            max_reads=2,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/bytes.txt"],
        )
        assert "abcd" in byte_reader("artifact:artifacts/bytes.txt", start_byte=0, max_bytes=4)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_OVERLAP_GUARD_MARKER):
            byte_reader("artifact:artifacts/bytes.txt", start_byte=2, max_bytes=4)
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_OVERLAP_GUARD_MARKER):
            byte_reader("artifact:artifacts/bytes.txt", start_byte=4, max_bytes=4)


def test_bounded_reader_replays_overlapping_page_once_after_context_reduction(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    artifact = artifacts / "evidence.txt"
    artifact.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    reduction_state = {"epoch": 0}

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        reader = create_bounded_artifact_reader(
            max_reads=1,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/evidence.txt"],
            context_reduction_state=reduction_state,
        )
        initial = ast.literal_eval(reader("artifact:artifacts/evidence.txt", max_lines=120))
        reduction_state["epoch"] = 1
        replay = ast.literal_eval(reader("artifact:artifacts/evidence.txt", start_line=4, max_lines=120))

    assert initial["content"] == "one\ntwo\nthree\nfour"
    assert replay["content"] == initial["content"]
    assert replay["compression_recovery"] is True
    assert replay["compression_recovery_epoch"] == 1

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
        pytest.raises(RuntimeError, match=ARTIFACT_READ_OVERLAP_GUARD_MARKER),
    ):
        reader("artifact:artifacts/evidence.txt", start_line=3, max_lines=120)


def test_bounded_reader_terminally_guards_nearby_byte_pages(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    artifact = artifacts / "bytes.txt"
    artifact.write_text("x" * 1_000, encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        reader = create_bounded_artifact_reader(
            max_reads=3,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/bytes.txt"],
        )
        assert "'end_byte': 100" in reader("artifact:artifacts/bytes.txt", start_byte=0, max_bytes=100)
        nearby = ast.literal_eval(
            reader("artifact:artifacts/bytes.txt", start_byte=356, max_bytes=100)
        )
        assert nearby["status"] == "not_directly_readable"
        assert nearby["reason"] == "nearby_page"
        guarded = ast.literal_eval(
            reader("artifact:artifacts/bytes.txt", start_byte=100, max_bytes=100)
        )
        assert guarded["reason"] == "artifact_byte_page_guarded"
        with pytest.raises(RuntimeError, match=ARTIFACT_READ_REPEAT_GUARD_MARKER):
            reader("artifact:artifacts/bytes.txt", start_byte=100, max_bytes=100)


def test_bounded_reader_rejects_oversized_page_without_consuming_successful_read_budget(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    oversized = artifacts / "minified.js"
    oversized.write_text("x" * 19_201, encoding="utf-8")
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


def test_bounded_reader_requires_explicit_byte_page_for_large_artifact(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    oversized = artifacts / "minified.js"
    oversized.write_text("x" * 19_201, encoding="utf-8")
    small = artifacts / "evidence.txt"
    small.write_text("proof", encoding="utf-8")

    with (
        patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)),
        patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
    ):
        reader = create_bounded_artifact_reader(
            max_reads=2,
            context_window_tokens=48_000,
            allowed_artifact_refs=["artifact:artifacts/minified.js", "artifact:artifacts/evidence.txt"],
            omitted_large_artifact_sizes={"artifact:artifacts/minified.js": 19_201},
        )

        with pytest.raises(RuntimeError, match=r"Artifact is 19201 bytes") as error:
            reader("artifact:artifacts/minified.js")
        assert "requires explicit byte paging" in str(error.value)
        assert "start_byte" in str(error.value)
        assert "max_bytes" in str(error.value)
        assert "max_lines" in str(error.value)
        assert "'start_byte': 0" in reader("artifact:artifacts/minified.js", max_bytes=19_200)
        assert "proof" in reader("artifact:artifacts/evidence.txt")
