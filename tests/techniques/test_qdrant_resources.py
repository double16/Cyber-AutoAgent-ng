from unittest.mock import Mock

import pytest

from modules.storage import qdrant


@pytest.mark.parametrize("provider", ["ollama", "gemini", "litellm", "bedrock"])
def test_shared_embedding_factory_preserves_provider_configuration(provider):
    constructors = {name: Mock(return_value=name) for name in ["ollama", "gemini", "litellm", "bedrock"]}
    config = {
        "embedding_provider": provider,
        "embedding_model": "bedrock/model" if provider == "bedrock" else "model",
        "ollama_base_url": "http://custom.invalid:11434",
        "aws_region": "us-west-2",
    }
    assert qdrant.build_embeddings(config, constructors) == provider
    selected = constructors[provider]
    if provider == "ollama":
        assert selected.call_args.kwargs["base_url"] == config["ollama_base_url"]
    if provider == "bedrock":
        assert selected.call_args.kwargs["model_id"] == "model"
        assert selected.call_args.kwargs["region_name"] == "us-west-2"


def test_embedding_injection_and_default_constructor_loading(monkeypatch):
    embedded = Mock()
    assert qdrant.build_embeddings({"embeddings": embedded}) is embedded
    constructor = Mock(return_value=embedded)
    monkeypatch.setattr("modules.tools.memory._LiteLLMEmbeddings", constructor)
    monkeypatch.setenv("CYBER_AGENT_PROVIDER", "litellm")
    monkeypatch.setenv("CYBER_AGENT_EMBEDDING_MODEL", "configured-model")
    assert qdrant.build_embeddings({}) is embedded
    constructor.assert_called_once_with("configured-model")


def test_local_and_service_connections_share_existing_configuration(monkeypatch, tmp_path):
    constructor = Mock()
    monkeypatch.setattr(qdrant, "QdrantClient", constructor)
    monkeypatch.delenv("QDRANT_URL", raising=False)
    qdrant.open_qdrant(str(tmp_path))
    assert (tmp_path / "qdrant").is_dir()
    constructor.assert_called_once_with(path=str(tmp_path / "qdrant"))
    constructor.reset_mock()
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.invalid")
    monkeypatch.setenv("QDRANT_API_KEY", "synthetic-key")
    qdrant.open_qdrant(str(tmp_path))
    constructor.assert_called_once_with(url="https://qdrant.invalid", api_key="synthetic-key")
