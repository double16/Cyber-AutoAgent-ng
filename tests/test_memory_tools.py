import os
from unittest.mock import patch, MagicMock
import pytest

from modules.tools.memory import memory_list, memory_retrieve, clear_memory_client

@pytest.fixture(autouse=True)
def memory_client_clear():
    clear_memory_client()
    if "MEMORY_LIST_LIMIT" in os.environ:
        del os.environ["MEMORY_LIST_LIMIT"]
    yield
    clear_memory_client()

@patch("modules.tools.memory._ensure_memory_client")
@patch("modules.tools.memory._user_id")
@patch("modules.tools.memory._operation_id")
class TestMemoryList:
    def test_memory_list_success(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        
        mock_client.list_memories.return_value = [
            {"id": "1", "memory": "test memory 1", "created_at": "2024-01-01T00:00:00"},
            {"id": "2", "memory": "test memory 2", "created_at": "2024-01-01T00:00:01"}
        ]
        
        result = memory_list()

        assert len(result.splitlines()) == 2
        assert "- test memory 1\n" in result
        assert "- test memory 2\n" in result

        mock_client.list_memories.assert_called_once_with(
            "test_user", None, limit=100, run_id="test_op"
        )

    def test_memory_list_with_agent_id(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        mock_client.list_memories.return_value = []
        
        memory_list()
        
        mock_client.list_memories.assert_called_once_with(
            "test_user", None, limit=100, run_id="test_op"
        )

    def test_memory_list_empty(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_client.list_memories.return_value = []
        
        result = memory_list()
        assert result == ""

    def test_memory_list_exception(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_ensure_client.side_effect = Exception("Initialization failed")
        
        result = memory_list()
        assert result == "Error: Initialization failed"

    def test_memory_list_custom_limit(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        mock_client.list_memories.return_value = []
        
        with patch.dict(os.environ, {"MEMORY_LIST_LIMIT": "50"}):
            memory_list()
        
        mock_client.list_memories.assert_called_once_with(
            "test_user", None, limit=50, run_id="test_op"
        )

    def test_memory_list_cross_operation(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        mock_client.list_memories.return_value = []
        
        with patch("modules.tools.memory.memory_is_cross_operation", return_value=True):
            memory_list()
        
        mock_client.list_memories.assert_called_once_with(
            "test_user", None, limit=100, run_id=None
        )

@patch("modules.tools.memory._ensure_memory_client")
@patch("modules.tools.memory._user_id")
@patch("modules.tools.memory._operation_id")
class TestMemoryRetrieve:
    def test_memory_retrieve_success(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        
        mock_client.search.return_value = [
            {"id": "1", "memory": "relevant memory", "metadata": {"category": "finding"}, "created_at": "2024-01-01T00:00:00"}
        ]
        
        result = memory_retrieve(query="test query")

        assert len(result.splitlines()) == 1
        assert "- relevant memory" in result

        mock_client.search.assert_called_once_with(
            query="test query",
            filters=None,
            limit=100,
            user_id="test_user",
            agent_id=None,
            run_id="test_op"
        )

    def test_memory_retrieve_with_filters(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        mock_client.search.return_value = []
        
        filters = {"category": "finding"}
        memory_retrieve(query="test query", metadata=filters)
        
        mock_client.search.assert_called_once_with(
            query="test query",
            filters=filters,
            limit=100,
            user_id="test_user",
            agent_id=None,
            run_id="test_op"
        )

    def test_memory_retrieve_missing_query(self, mock_op_id, mock_user_id, mock_ensure_client):
        # The retrieval tool checks for query truthiness.
        result = memory_retrieve(query="")
        assert result == "Error: query is required"

    def test_memory_retrieve_no_results(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_client.search.return_value = []
        
        result = memory_retrieve(query="test query")
        assert result == ""

    def test_memory_retrieve_exception(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_ensure_client.side_effect = Exception("Search failed")
        
        result = memory_retrieve(query="test query")
        assert result == "Error: Search failed"
