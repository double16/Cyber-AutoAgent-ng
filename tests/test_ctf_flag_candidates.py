import json
from pathlib import Path
from unittest.mock import patch

import pytest

from modules.handlers.utils import get_tool_spec
from modules.operation_plugins.ctf.tools import flag_candidates as flags


def test_artifact_matches_supports_braced_and_hex_flag_shapes(tmp_path: Path):
    artifact = tmp_path / "response.txt"
    sha256 = "a" * 64
    sha512 = "b" * 128
    artifact.write_text(f"FLAG{{alpha}}\nHTB{{beta}}\n{sha256}\n{sha512}\n{'c' * 63}", encoding="utf-8")

    with patch.object(flags.memory_tools, "_artifact_path_from_ref", return_value=str(artifact)):
        matches = flags._artifact_matches(str(artifact))

    assert [(match["match_type"], match["line"]) for match in matches] == [
        ("braced", 1),
        ("braced", 2),
        ("hex", 3),
        ("hex", 4),
    ]
    assert {match["value"] for match in matches} == {"FLAG{alpha}", "HTB{beta}", sha256, sha512}


def test_discover_flag_candidates_returns_opaque_references(tmp_path: Path):
    artifact = tmp_path / "response.txt"
    artifact.write_text("command output: FLAG{artifact_only}", encoding="utf-8")
    registered = {
        "candidate_ref": "objective_candidate:candidate-1",
        "candidate_uid": "candidate-1",
        "verification_task_ref": "task:task-1",
        "verification_task_uid": "task-1",
        "status": "pending_validation",
    }
    with (
        patch.object(flags.memory_tools, "_validated_artifact_paths", return_value=[str(artifact)]),
        patch.object(flags.memory_tools, "_artifact_path_from_ref", return_value=str(artifact)),
        patch.object(
            flags.memory_tools, "store_objective_candidate", return_value=json.dumps(registered)
        ) as store_candidate,
    ):
        result = json.loads(flags.discover_flag_candidates([str(artifact)]))

    assert result["candidate_count"] == 1
    assert result["candidates"] == [
        {
            **registered,
            "match_type": "braced",
            "evidence_artifact": str(artifact),
            "artifact_line": 1,
        }
    ]
    assert "FLAG{artifact_only}" not in json.dumps(result)
    assert store_candidate.call_args.args[1] == "FLAG{artifact_only}"


def test_discover_flag_candidates_returns_no_candidates_for_non_flags(tmp_path: Path):
    artifact = tmp_path / "response.txt"
    artifact.write_text("command completed with status 200", encoding="utf-8")
    with (
        patch.object(flags.memory_tools, "_validated_artifact_paths", return_value=[str(artifact)]),
        patch.object(flags.memory_tools, "_artifact_path_from_ref", return_value=str(artifact)),
        patch.object(flags.memory_tools, "store_objective_candidate") as store_candidate,
    ):
        result = json.loads(flags.discover_flag_candidates([str(artifact)]))

    assert result == {"candidate_count": 0, "candidates": []}
    store_candidate.assert_not_called()


def test_discover_flag_candidates_honors_candidate_limit(tmp_path: Path):
    artifact = tmp_path / "response.txt"
    artifact.write_text("FLAG{first}\nHTB{second}", encoding="utf-8")
    registered = {
        "candidate_ref": "objective_candidate:candidate-1",
        "candidate_uid": "candidate-1",
        "verification_task_ref": "task:task-1",
        "verification_task_uid": "task-1",
        "status": "pending_validation",
    }
    with (
        patch.object(flags.memory_tools, "_validated_artifact_paths", return_value=[str(artifact)]),
        patch.object(flags.memory_tools, "_artifact_path_from_ref", return_value=str(artifact)),
        patch.object(
            flags.memory_tools, "store_objective_candidate", return_value=json.dumps(registered)
        ) as store_candidate,
    ):
        result = json.loads(flags.discover_flag_candidates([str(artifact)], max_candidates=1))

    assert result["candidate_count"] == 1
    assert store_candidate.call_count == 1
    assert store_candidate.call_args.args[1] == "FLAG{first}"


def test_discover_flag_candidates_rejects_invalid_maximum():
    with pytest.raises(ValueError, match="max_candidates"):
        flags.discover_flag_candidates(["artifact:response.txt"], 0)


def test_discover_flag_candidates_schema_is_bounded():
    schema = get_tool_spec(flags.discover_flag_candidates)["inputSchema"]["json"]

    assert schema["required"] == ["evidence_artifacts"]
    assert schema["properties"]["max_candidates"]["maximum"] == 20


def test_ctf_tool_module_does_not_reexport_core_tools():
    decorated_names = [
        name
        for name in dir(flags)
        if callable(getattr(flags, name)) and hasattr(getattr(flags, name), "__wrapped__")
    ]

    assert decorated_names == ["discover_flag_candidates"]
