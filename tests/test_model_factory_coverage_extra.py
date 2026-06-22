import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.config.models import factory as mod


def test_prompt_limit_from_litellm_candidates(monkeypatch):
    fake_litellm = SimpleNamespace(
        get_context_window=Mock(side_effect=[None, 12345]),
        model_cost={},
        get_max_tokens=Mock(return_value=None),
    )
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    assert mod._get_prompt_limit_from_model("openrouter/vendor/model-name") == 12345


def test_resolve_prompt_token_limit_env_static_litellm_and_default(monkeypatch):
    mod._resolve_prompt_token_limit.cache_clear()
    monkeypatch.setenv("CYBER_PROMPT_LIMIT_FORCE", "999")
    assert mod._resolve_prompt_token_limit("unknown", "model") == 999

    mod._resolve_prompt_token_limit.cache_clear()
    monkeypatch.delenv("CYBER_PROMPT_LIMIT_FORCE", raising=False)
    monkeypatch.setattr(mod, "get_model_input_limit", lambda model_id: 222 if model_id == "known" else None)
    assert mod._resolve_prompt_token_limit("bedrock", "known") == 222

    mod._resolve_prompt_token_limit.cache_clear()
    monkeypatch.setattr(mod, "_get_prompt_limit_from_model", lambda _model_id: 333)
    assert mod._resolve_prompt_token_limit("litellm", "unknown") == 333

    mod._resolve_prompt_token_limit.cache_clear()
    monkeypatch.setattr(mod, "PROMPT_TOKEN_FALLBACK_LIMIT", 0)
    monkeypatch.setattr(mod, "get_provider_default_limit", lambda provider: 444 if provider == "gemini" else None)
    assert mod._resolve_prompt_token_limit("gemini", "unknown") == 444
    assert mod._resolve_prompt_token_limit("none", "unknown") is None


def test_parse_and_apply_context_window_fallbacks(monkeypatch):
    monkeypatch.setenv("CYBER_CONTEXT_WINDOW_FALLBACKS", "a:b,c; broken ; d:e")
    assert mod._parse_context_window_fallbacks() == [{"a": ["b", "c"]}, {"d": ["e"]}]

    fake_litellm = SimpleNamespace(context_window_fallbacks=None)
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    args = {}
    mod._apply_context_window_fallbacks(args)

    assert args["context_window_fallbacks"] == [{"a": ["b", "c"]}, {"d": ["e"]}]
    assert fake_litellm.context_window_fallbacks == args["context_window_fallbacks"]


def test_get_parameters_by_role_fallback_and_config_override(monkeypatch):
    monkeypatch.setattr(mod, "_get_config_manager", Mock(side_effect=RuntimeError("missing config")))

    params = mod._get_parameters_by_role(
        "litellm",
        "model",
        "swarm",
        {"temperature": 0.2, "max_tokens": 123},
    )

    assert params.llm_temp == 0.2
    assert params.llm_max == 123
    assert params.role == "unknown"


def test_get_model_and_provider_helpers():
    assert mod.get_model_id_from_agent(SimpleNamespace(model=SimpleNamespace(model_id="m"))) == "m"
    assert mod.get_model_id_from_model(SimpleNamespace(config={"model": "cfg-model"})) == "cfg-model"
    assert mod.get_model_id_from_model(SimpleNamespace(config=SimpleNamespace(model_id="obj-model"))) == "obj-model"
    assert mod.get_provider_from_model(type("OllamaThing", (), {})()) == "ollama"
    assert mod.get_provider_from_model(type("LiteLLMThing", (), {})()) == "litellm"
    assert mod.get_provider_from_model(type("BedrockThing", (), {})()) == "bedrock"
    assert mod.get_provider_from_model(type("GeminiThing", (), {})()) == "gemini"
    assert mod.get_provider_from_model(type("OtherThing", (), {})()) is None
    assert mod.get_provider_from_agent(SimpleNamespace(model=type("BedrockThing", (), {})())) == "bedrock"
    assert mod.get_provider_from_agent(SimpleNamespace(model=None)) == ""
    assert mod.get_model_timeout(None, default_timeout=7) == 7


def test_handle_model_creation_error_prints_guidance(monkeypatch):
    messages = []
    monkeypatch.setattr(mod, "print_status", lambda message, status: messages.append((status, message)))

    mod._handle_model_creation_error("ollama", RuntimeError("down"))
    mod._handle_model_creation_error("unknown", RuntimeError("bad"))

    assert ("ERROR", "Ollama model creation failed: down") in messages
    assert any("Start Ollama" in message for _status, message in messages)
    assert ("ERROR", "Unknown model creation failed: bad") in messages


def test_create_strands_model_dispatch_and_error(monkeypatch):
    config_manager = SimpleNamespace(
        get_provider=Mock(return_value="ollama"),
        get_llm_config=Mock(return_value=SimpleNamespace(model_id="llama")),
        get_default_region=Mock(return_value="us-east-1"),
    )
    monkeypatch.setattr(mod, "_get_config_manager", lambda: config_manager)
    monkeypatch.setattr(mod, "create_ollama_model", Mock(return_value="ollama-model"))
    monkeypatch.setattr(mod, "print_status", Mock())

    assert mod.create_strands_model() == "ollama-model"

    monkeypatch.setattr(mod, "create_ollama_model", Mock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(mod, "_handle_model_creation_error", Mock())

    with pytest.raises(RuntimeError):
        mod.create_strands_model("ollama", "llama")

    mod._handle_model_creation_error.assert_called_once()
