import json
import os
from unittest.mock import patch, MagicMock
import pytest

from modules.tools.memory import mem0_list, mem0_retrieve, clear_memory_client

@pytest.fixture(autouse=True)
def memory_client_clear():
    clear_memory_client()
    if "MEM0_LIST_LIMIT" in os.environ:
        del os.environ["MEM0_LIST_LIMIT"]
    if "MEMORY_ISOLATION" in os.environ:
        del os.environ["MEMORY_ISOLATION"]
    yield
    clear_memory_client()

@patch("modules.tools.memory._ensure_memory_client")
@patch("modules.tools.memory._user_id")
@patch("modules.tools.memory._operation_id")
class TestMem0List:
    def test_mem0_list_success(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        
        mock_client.list_memories.return_value = [
            {"id": "1", "memory": "test memory 1", "created_at": "2024-01-01T00:00:00"},
            {"id": "2", "memory": "test memory 2", "created_at": "2024-01-01T00:00:01"}
        ]
        
        result = mem0_list()
        
        # Verify result is a JSON string of pruned memories
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["id"] == "2" # Sorted by create time reverse
        assert data[0]["memory"] == "test memory 2"
        assert data[1]["id"] == "1"
        assert data[1]["memory"] == "test memory 1"
        
        mock_client.list_memories.assert_called_once_with(
            "test_user", None, limit=100, run_id="test_op"
        )

    def test_mem0_list_with_agent_id(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        mock_client.list_memories.return_value = []
        
        mem0_list(agent_id="test_agent")
        
        mock_client.list_memories.assert_called_once_with(
            "test_user", "test_agent", limit=100, run_id="test_op"
        )

    def test_mem0_list_empty(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_client.list_memories.return_value = []
        
        result = mem0_list()
        assert result == ""

    def test_mem0_list_exception(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_ensure_client.side_effect = Exception("Initialization failed")
        
        result = mem0_list()
        assert result == "Error: Initialization failed"

    def test_mem0_list_custom_limit(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        mock_client.list_memories.return_value = []
        
        with patch.dict(os.environ, {"MEM0_LIST_LIMIT": "50"}):
            mem0_list()
        
        mock_client.list_memories.assert_called_once_with(
            "test_user", None, limit=50, run_id="test_op"
        )

    def test_mem0_list_cross_operation(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        mock_client.list_memories.return_value = []
        
        with patch.dict(os.environ, {"MEMORY_ISOLATION": "shared"}):
            mem0_list()
        
        mock_client.list_memories.assert_called_once_with(
            "test_user", None, limit=100, run_id=None
        )

@patch("modules.tools.memory._ensure_memory_client")
@patch("modules.tools.memory._user_id")
@patch("modules.tools.memory._operation_id")
class TestMem0Retrieve:
    def test_mem0_retrieve_success(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        
        mock_client.search.return_value = [
            {"id": "1", "memory": "relevant memory", "metadata": {"category": "finding"}, "created_at": "2024-01-01T00:00:00"}
        ]
        
        result = mem0_retrieve(query="test query")
        
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["memory"] == "relevant memory"
        
        mock_client.search.assert_called_once_with(
            query="test query",
            filters=None,
            limit=100,
            user_id="test_user",
            agent_id=None,
            run_id="test_op"
        )

    def test_mem0_retrieve_with_filters(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_user_id.return_value = "test_user"
        mock_op_id.return_value = "test_op"
        mock_client.search.return_value = []
        
        filters = {"category": "finding"}
        mem0_retrieve(query="test query", metadata=filters, agent_id="test_agent")
        
        mock_client.search.assert_called_once_with(
            query="test query",
            filters=filters,
            limit=100,
            user_id="test_user",
            agent_id="test_agent",
            run_id="test_op"
        )

    def test_mem0_retrieve_missing_query(self, mock_op_id, mock_user_id, mock_ensure_client):
        # mem0_retrieve checks for query truthiness
        result = mem0_retrieve(query="")
        assert result == "Error: query is required"

    def test_mem0_retrieve_no_results(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_client = MagicMock()
        mock_ensure_client.return_value = mock_client
        mock_client.search.return_value = []
        
        result = mem0_retrieve(query="test query")
        assert result == "[]"

    def test_mem0_retrieve_exception(self, mock_op_id, mock_user_id, mock_ensure_client):
        mock_ensure_client.side_effect = Exception("Search failed")
        
        result = mem0_retrieve(query="test query")
        assert result == "Error: Search failed"
