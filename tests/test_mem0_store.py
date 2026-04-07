import json
import logging
import pytest
from unittest.mock import MagicMock, patch
from src.modules.tools.memory import mem0_store


@pytest.fixture
def mock_memory_client():
    with patch("src.modules.tools.memory._ensure_memory_client") as mock_ensure:
        mock_client = MagicMock()
        mock_ensure.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_user_op_ids():
    with patch("src.modules.tools.memory._user_id", return_value="test_user"), \
            patch("src.modules.tools.memory._operation_id", return_value="test_op"):
        yield


def test_mem0_store_basic_success(mock_memory_client, mock_user_op_ids):
    # Setup
    content = "Found a vulnerability"
    metadata = {"category": "finding", "severity": "HIGH"}
    mock_memory_client.mem0.search.return_value = {"results": []}
    mock_memory_client.store_memory.return_value = [{"id": "mem_1", "memory": content}]

    # Execute
    result_json = mem0_store(content, metadata)
    result = json.loads(result_json)

    # Verify
    assert len(result) == 1
    assert result[0]["id"] == "mem_1"
    mock_memory_client.store_memory.assert_called_once()
    args, kwargs = mock_memory_client.store_memory.call_args
    assert args[0] == content
    assert args[1] == "test_user"
    assert args[3]["category"] == "finding"
    assert args[3]["operation_id"] == "test_op"


def test_mem0_store_content_cleaning(mock_memory_client, mock_user_op_ids):
    # Setup
    content = "Line 1\nLine 2\tTabbed\x00Null  Multiple   Spaces"
    metadata = {"category": "observation"}
    mock_memory_client.mem0.search.return_value = {"results": []}
    mock_memory_client.store_memory.return_value = []

    # Execute
    mem0_store(content, metadata)

    # Verify
    # Null byte is replaced with "", not " "
    cleaned_content = "Line 1 Line 2 TabbedNull Multiple Spaces"
    mock_memory_client.store_memory.assert_called_once()
    args, kwargs = mock_memory_client.store_memory.call_args
    assert args[0] == cleaned_content
    assert args[1] == "test_user"
    assert args[2] == None


def test_mem0_store_metadata_cleaning(mock_memory_client, mock_user_op_ids):
    # Setup
    content = "Some content"
    metadata = {"category": "observation", "note": "Multi\nline\tmetadata"}
    mock_memory_client.mem0.search.return_value = {"results": []}
    mock_memory_client.store_memory.return_value = []

    # Execute
    mem0_store(content, metadata)

    # Verify
    cleaned_metadata = {
        "category": "observation",
        "note": "Multi line metadata",
        "operation_id": "test_op"
    }
    mock_memory_client.store_memory.assert_called_once_with(
        "Some content", "test_user", None, cleaned_metadata
    )


def test_mem0_store_validation_errors(mock_memory_client, mock_user_op_ids):
    # Empty content
    with pytest.raises(ValueError, match="content is required"):
        mem0_store("", {"category": "finding"})

    # Content empty after cleaning
    with pytest.raises(ValueError, match="Content is empty after cleaning"):
        mem0_store("   \n\t  ", {"category": "finding"})

    # Missing category
    with pytest.raises(ValueError, match="MISSING CATEGORY"):
        mem0_store("Content", {})

    # Invalid category - should NOT raise error but log warning and default to observation
    content = "Some content"
    metadata = {"category": "invalid"}
    mock_memory_client.mem0.search.return_value = {"results": []}
    mock_memory_client.store_memory.return_value = []

    mem0_store(content, metadata)
    args, kwargs = mock_memory_client.store_memory.call_args
    assert args[3]["category"] == "observation"


def test_mem0_store_auto_categorization(mock_memory_client, mock_user_op_ids):
    # observation with severity != INFO should become finding
    content = "Something important"
    metadata = {"category": "observation", "severity": "HIGH"}
    mock_memory_client.mem0.search.return_value = {"results": []}
    mock_memory_client.store_memory.return_value = []

    mem0_store(content, metadata)

    args, kwargs = mock_memory_client.store_memory.call_args
    assert args[3]["category"] == "finding"


def test_mem0_store_confidence_normalization(mock_memory_client, mock_user_op_ids):
    content = "Discovered something with pattern match"
    # Need category='finding' for confidence normalization logic to run
    metadata = {"category": "finding", "confidence": "80%", "evidence_type": "pattern_match"}
    mock_memory_client.mem0.search.return_value = {"results": []}
    mock_memory_client.store_memory.return_value = []

    mem0_store(content, metadata)

    args, kwargs = mock_memory_client.store_memory.call_args
    # Capped to 40.0 for pattern_match
    assert args[3]["confidence"] == "40.0%"


def test_mem0_store_status_consistency(mock_memory_client, mock_user_op_ids):
    # status='verified' but validation_status='hypothesis' -> fix validation_status
    content = "Verified finding"
    # Need category='finding'
    metadata = {"category": "finding", "status": "verified", "validation_status": "hypothesis"}
    mock_memory_client.mem0.search.return_value = {"results": []}
    mock_memory_client.store_memory.return_value = []

    mem0_store(content, metadata)

    args, kwargs = mock_memory_client.store_memory.call_args
    assert args[3]["status"] == "verified"
    assert args[3]["validation_status"] == "verified"


def test_mem0_store_status_consistency_part2(mock_memory_client, mock_user_op_ids):
    # validation_status='verified' but status!='verified' -> fix status
    content = "Accepted submission"
    # Need severity=MEDIUM so validation_status isn't overwritten by _has_valid_proof_pack logic for HIGH/CRITICAL
    metadata = {"category": "finding", "status": "hypothesis", "validation_status": "verified", "severity": "MEDIUM"}
    mock_memory_client.mem0.search.return_value = {"results": []}
    mock_memory_client.store_memory.return_value = []

    mem0_store(content, metadata)
    args, kwargs = mock_memory_client.store_memory.call_args
    assert args[3]["status"] == "verified"
    assert args[3]["validation_status"] == "verified"


def test_mem0_store_duplicate_detection_standard(mock_memory_client, mock_user_op_ids):
    content = "Found vulnerability"
    metadata = {"category": "finding"}

    # Mock search to find a duplicate
    mock_memory_client.mem0.search.return_value = {
        "results": [
            {
                "id": "mem_dup",
                "memory": content,
                "score": 0.05,
                "metadata": {"category": "finding"}
            }
        ]
    }

    result_json = mem0_store(content, metadata)
    result = json.loads(result_json)

    assert len(result) == 1
    assert result[0]["event"] == "DUPLICATE"
    assert result[0]["id"] == "mem_dup"
    mock_memory_client.store_memory.assert_not_called()


def test_mem0_store_duplicate_detection_sensitive_patterns(mock_memory_client, mock_user_op_ids):
    content1 = "Found vulnerability at http://example.com/api"
    metadata = {"category": "finding"}

    # Mock search to find a match with high similarity but DIFFERENT URL
    mock_memory_client.mem0.search.return_value = {
        "results": [
            {
                "id": "mem_1",
                "memory": "Found vulnerability at http://other-domain.com/api",
                "score": 0.05,
                "metadata": {"category": "finding"}
            }
        ]
    }
    mock_memory_client.store_memory.return_value = [{"id": "mem_new"}]

    # Should NOT be treated as duplicate
    result_json = mem0_store(content1, metadata)
    result = json.loads(result_json)
    assert result[0]["id"] == "mem_new"
    mock_memory_client.store_memory.assert_called_once()

    # Now test with SAME URL
    mock_memory_client.store_memory.reset_mock()
    mock_memory_client.mem0.search.return_value = {
        "results": [
            {
                "id": "mem_1",
                "memory": content1,
                "score": 0.05,
                "metadata": {"category": "finding"}
            }
        ]
    }

    result_json = mem0_store(content1, metadata)
    result = json.loads(result_json)
    assert result[0]["event"] == "DUPLICATE"
    mock_memory_client.store_memory.assert_not_called()


def test_mem0_store_error_recovery_json(mock_memory_client, mock_user_op_ids):
    content = 'Content with "quotes" and {braces}'
    metadata = {"category": "finding"}
    mock_memory_client.mem0.search.return_value = {"results": []}

    # First call fails with JSON error, second (retry) succeeds
    mock_memory_client.store_memory.side_effect = [
        Exception("Extra data: line 1 column 10 (char 9)"),
        [{"id": "mem_recovered"}]
    ]

    result_json = mem0_store(content, metadata)
    result = json.loads(result_json)

    assert result[0]["id"] == "mem_recovered"
    assert mock_memory_client.store_memory.call_count == 2

    # Verify second call had escaped content
    args, kwargs = mock_memory_client.store_memory.call_args
    assert '\\"' in args[0]


def test_mem0_store_unrecoverable_error(mock_memory_client, mock_user_op_ids):
    content = "Some content"
    metadata = {"category": "finding"}
    mock_memory_client.mem0.search.return_value = {"results": []}

    mock_memory_client.store_memory.side_effect = RuntimeError("Backend down")

    with pytest.raises(RuntimeError, match="Backend down"):
        mem0_store(content, metadata)
