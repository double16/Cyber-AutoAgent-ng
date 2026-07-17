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


def test_read_artifact_rejects_traversal_and_missing_files(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path)):
        with pytest.raises(ValueError, match="outside"):
            read_artifact(str(outside))
        with pytest.raises(ValueError, match="does not exist"):
            read_artifact("missing.txt")


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
