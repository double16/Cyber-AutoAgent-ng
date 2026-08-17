from pathlib import Path
from unittest.mock import patch

import pytest

from modules.tools.artifact import create_bounded_artifact_reader, read_artifact


def test_read_artifact_returns_bounded_lines(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = read_artifact("evidence.txt", start_line=2, max_lines=2)

    assert "'content': 'two\\nthree'" in result
    assert "'total_lines': 4" in result


def test_read_artifact_prefers_artifact_directory_for_relative_paths(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evidence.txt").write_text("artifact copy", encoding="utf-8")
    (tmp_path / "evidence.txt").write_text("root copy", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = read_artifact("evidence.txt")

    assert "artifact copy" in result
    assert "artifact:artifacts/evidence.txt" in result


def test_read_artifact_falls_back_to_operation_root_for_relative_paths(tmp_path: Path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "evidence.txt").write_text("root file", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        result = read_artifact("tools/evidence.txt")

    assert "root file" in result
    assert "artifact:tools/evidence.txt" in result


def test_read_artifact_rejects_traversal_and_missing_files(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        with pytest.raises(ValueError, match="outside"):
            read_artifact(str(outside))
        with pytest.raises(ValueError, match="does not exist"):
            read_artifact("missing.txt")
        with pytest.raises(ValueError, match="outside"):
            read_artifact("../../outside.txt")


@pytest.mark.parametrize("start_line,max_lines", [(0, 10), (1, 0), (1, 501)])
def test_read_artifact_validates_bounds(tmp_path: Path, start_line: int, max_lines: int):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("one", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        with pytest.raises(ValueError):
            read_artifact(str(artifact), start_line=start_line, max_lines=max_lines)


def test_bounded_reader_enforces_per_agent_limit(tmp_path: Path):
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("one", encoding="utf-8")
    reader = create_bounded_artifact_reader(max_reads=1)

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        assert "one" in reader(str(artifact))
        with pytest.raises(RuntimeError, match="read limit"):
            reader(str(artifact))
