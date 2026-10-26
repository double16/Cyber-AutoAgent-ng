"""Operation-independent Qdrant and embedding construction shared with technique CLI."""

import os
import threading
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

QDRANT_WRITE_LOCK = threading.RLock()


def build_embeddings(config: dict, constructors: dict | None = None) -> Any:
    if config.get("embeddings") is not None:
        return config["embeddings"]
    if constructors is None:
        from modules.tools.memory import (
            BedrockEmbeddings,
            GoogleGenerativeAIEmbeddings,
            OllamaEmbeddings,
            _LiteLLMEmbeddings,
        )

        constructors = {
            "bedrock": BedrockEmbeddings,
            "gemini": GoogleGenerativeAIEmbeddings,
            "ollama": OllamaEmbeddings,
            "litellm": _LiteLLMEmbeddings,
        }
    provider = str(config.get("embedding_provider") or os.getenv("CYBER_AGENT_PROVIDER", "bedrock")).lower()
    model = str(config.get("embedding_model") or os.getenv("CYBER_AGENT_EMBEDDING_MODEL", ""))
    if provider == "ollama":
        return constructors[provider](
            model=model,
            base_url=str(config.get("ollama_base_url") or os.getenv("OLLAMA_HOST", "http://localhost:11434")),
            client_kwargs={"trust_env": False},
        )
    if provider == "gemini":
        return constructors[provider](model=model, client_args={"trust_env": False})
    if provider == "litellm":
        return constructors[provider](model)
    from botocore.config import Config

    return constructors["bedrock"](
        model_id=model.removeprefix("bedrock/"),
        region_name=str(config.get("aws_region") or os.getenv("AWS_REGION", "us-east-1")),
        config=Config(proxies={}),
    )


def open_qdrant(output_dir: str) -> QdrantClient:
    url = os.getenv("QDRANT_URL", "").strip()
    if url:
        return QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY") or None)
    path = Path(output_dir) / "qdrant"
    path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(path))
