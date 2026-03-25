import pytest
import os
import json
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime
from modules.handlers.report_generator import generate_security_report, _extract_text_from_result, build_report_sections
from modules.tools.memory import clear_memory_client


def test_extract_text_from_result():
    # Test normal extraction
    mock_result = MagicMock()
    mock_result.message = {
        "content": [
            {"text": "  # Heading 1\n"},
            {"text": "\t## Heading 2\n"},
            {"text": "Some normal text\n"},
            {"text": "    ### Heading 3 with spaces\n"}
        ]
    }
    
    extracted = _extract_text_from_result(mock_result)
    assert "# Heading 1\n" in extracted
    assert "## Heading 2\n" in extracted
    assert "### Heading 3 with spaces\n" in extracted
    assert "Some normal text\n" in extracted
    
    # Verify no leading spaces before headings
    lines = extracted.splitlines()
    assert lines[0] == "# Heading 1"
    assert lines[1] == "## Heading 2"
    assert lines[2] == "Some normal text"
    assert lines[3] == "### Heading 3 with spaces"

def test_extract_text_from_result_empty():
    assert _extract_text_from_result(None) == ""
    
    mock_result = MagicMock()
    mock_result.message = {}
    assert _extract_text_from_result(mock_result) == ""


@pytest.fixture(autouse=True)
def memory_client_clear():
    clear_memory_client()


@patch("modules.handlers.report_generator.get_memory_client")
@pytest.mark.skip(reason="Not sure we want to downgrade findings in this way")
def test_report_builder_downgrade_logic(mock_get_client, tmp_path):
    op_id = "OP_DOWNGRADE_TEST"
    output_dir = tmp_path / "outputs"
    os.environ["CYBER_AGENT_OUTPUT_DIR"] = str(output_dir)

    # Mock list_memories to return findings with various validation statuses
    mock_client = mock_get_client.return_value
    mock_client.list_memories.return_value = [
        {
            "id": "1",
            "memory": "[VULNERABILITY] Verified with Proof [WHERE] /a [EVIDENCE] proof exists",
            "metadata": {
                "category": "finding",
                "operation_id": op_id,
                "severity": "CRITICAL",
                "validation_status": "verified",
                "proof_pack": {"artifacts": [str(tmp_path / "proof.txt")]}
            },
        },
        {
            "id": "2",
            "memory": "[VULNERABILITY] Unverified but HAS Proof [WHERE] /c",
            "metadata": {
                "category": "finding",
                "operation_id": op_id,
                "severity": "MEDIUM",
                "validation_status": "unverified",
                "proof_pack": {"artifacts": [str(tmp_path / "proof.txt")]}
            },
        },
        {
            "id": "3",
            "memory": "[VULNERABILITY] Hypothesis [WHERE] /d",
            "metadata": {
                "category": "finding",
                "operation_id": op_id,
                "severity": "LOW",
                "validation_status": "hypothesis"
            },
        },
    ]

    # Create the proof file
    proof_file = tmp_path / "proof.txt"
    proof_file.write_text("proof")

    # Run build_report_sections
    sections = build_report_sections(op_id, "example.com", "Test Objective")

    evidence = sections.get("raw_evidence", [])

    # Check item 1: Should remain a finding
    item1 = next(e for e in evidence if e["id"] == "1")
    assert item1["category"] == "finding", "Item 1 should remain a finding"

    # Check item 2: Should be downgraded to observation (unverified)
    item3 = next(e for e in evidence if e["id"] == "2")
    assert item3["category"] == "observation", "Item 2 should be downgraded to observation (unverified)"

    # Check item 3: Should be downgraded to observation (hypothesis)
    item4 = next(e for e in evidence if e["id"] == "3")
    assert item4["category"] == "observation", "Item 3 should be downgraded to observation (hypothesis)"

if __name__ == "__main__":
    pytest.main([__file__])
