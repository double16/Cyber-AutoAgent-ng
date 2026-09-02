import os
from types import SimpleNamespace

import pytest

from modules.config.system import validation


class Env:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key):
        return self.values.get(key, "")


def test_validate_provider_dispatches_and_rejects_unknown_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(validation, "validate_ollama_requirements", lambda *args: calls.append("ollama"))
    monkeypatch.setattr(validation, "validate_aws_requirements", lambda *args: calls.append("bedrock"))
    monkeypatch.setattr(validation, "validate_litellm_requirements", lambda *args: calls.append("litellm"))
    monkeypatch.setattr(validation, "validate_gemini_requirements", lambda *args: calls.append("gemini"))

    for provider in ("ollama", "bedrock", "litellm", "gemini"):
        validation.validate_provider(provider, Env(), "host", "region")

    assert calls == ["ollama", "bedrock", "litellm", "gemini"]
    with pytest.raises(ValueError, match="Unsupported provider"):
        validation.validate_provider("unknown", Env())


def test_validate_ollama_requirements_checks_host_connectivity_and_models(monkeypatch):
    with pytest.raises(ValueError, match="ollama_host is required"):
        validation.validate_ollama_requirements(Env())

    monkeypatch.setattr(validation.requests, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=503))
    with pytest.raises(ConnectionError, match="not accessible"):
        validation.validate_ollama_requirements(Env(), "http://ollama.test")

    monkeypatch.setattr(validation.requests, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=200))
    monkeypatch.setattr(validation.ollama, "Client", lambda **_kwargs: SimpleNamespace(list=lambda: {"models": []}))
    with pytest.raises(ValueError, match="No Ollama models"):
        validation.validate_ollama_requirements(Env(), "http://ollama.test")


def test_validate_ollama_requirements_validates_configured_models_and_wraps_client_errors(monkeypatch):
    monkeypatch.setattr(validation.requests, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=200))
    server_config = SimpleNamespace(
        llm=SimpleNamespace(model_id="required-llm"),
        embedding=SimpleNamespace(model_id="required-embed"),
    )
    monkeypatch.setattr(
        validation.ollama,
        "Client",
        lambda **_kwargs: SimpleNamespace(list=lambda: {"models": [{"name": "other-model"}]}),
    )
    with pytest.raises(ValueError, match="Required models not found"):
        validation.validate_ollama_requirements(Env(), "http://ollama.test", server_config)

    monkeypatch.setattr(validation.ollama, "Client", lambda **_kwargs: SimpleNamespace(list=lambda: (_ for _ in ()).throw(RuntimeError("down"))))
    with pytest.raises(ConnectionError, match="Could not verify"):
        validation.validate_ollama_requirements(Env(), "http://ollama.test")


def test_validate_aws_requirements_selects_bearer_or_standard_credentials(monkeypatch):
    monkeypatch.setattr(validation, "validate_bedrock_model_access", lambda region: None)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)

    with pytest.raises(OSError, match="AWS region"):
        validation.validate_aws_requirements(Env({"AWS_BEARER_TOKEN_BEDROCK": "token"}))
    with pytest.raises(OSError, match="credentials not configured"):
        validation.validate_aws_requirements(Env(), "us-east-1")

    validation.validate_aws_requirements(Env({"AWS_BEARER_TOKEN_BEDROCK": "token"}), "us-east-1")
    assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == "token"

    validation.validate_aws_requirements(
        Env({"AWS_BEARER_TOKEN_BEDROCK": "token", "AWS_ACCESS_KEY_ID": "access"}), "us-east-1"
    )
    assert "AWS_BEARER_TOKEN_BEDROCK" not in os.environ


@pytest.mark.parametrize(
    ("model_id", "key", "value"),
    [
        ("bedrock/model", "AWS_PROFILE", "profile"),
        ("openai/model", "OPENAI_API_KEY", "key"),
        ("anthropic/model", "ANTHROPIC_API_KEY", "key"),
        ("cohere/model", "COHERE_API_KEY", "key"),
        ("azure/model", "AZURE_API_KEY", "key"),
        ("gemini/model", "GEMINI_API_KEY", "key"),
    ],
)
def test_validate_litellm_requirements_requires_each_provider_credential(model_id, key, value):
    with pytest.raises(OSError):
        validation.validate_litellm_requirements(Env(), model_id)

    validation.validate_litellm_requirements(Env({key: value}), model_id)


def test_validate_litellm_sagemaker_and_gemini_requirements():
    with pytest.raises(OSError, match="credentials"):
        validation.validate_litellm_requirements(Env(), "sagemaker/model")
    with pytest.raises(OSError, match="region"):
        validation.validate_litellm_requirements(
            Env({"AWS_ACCESS_KEY_ID": "access", "AWS_SECRET_ACCESS_KEY": "secret"}), "sagemaker/model"
        )
    validation.validate_litellm_requirements(
        Env({"AWS_PROFILE": "profile", "AWS_REGION": "us-east-1"}), "sagemaker/model"
    )
    validation.validate_litellm_requirements(Env(), "")
    validation.validate_litellm_requirements(Env(), "custom-model")

    with pytest.raises(OSError, match="GEMINI_API_KEY"):
        validation.validate_gemini_requirements(Env())
    validation.validate_gemini_requirements(Env({"GEMINI_API_KEY": "key"}))


def test_validate_bedrock_model_access_requires_region_and_ignores_client_creation_errors(monkeypatch):
    with pytest.raises(OSError, match="AWS region"):
        validation.validate_bedrock_model_access("")

    monkeypatch.setattr(validation.boto3, "client", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no creds")))
    validation.validate_bedrock_model_access("us-east-1")
