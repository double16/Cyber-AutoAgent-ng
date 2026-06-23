import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.config.models import factory as mod


class FakeModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.config = kwargs
        self.model_id = kwargs.get("model_id")
        self.client_args = kwargs.get("client_args", kwargs.get("ollama_client_args", {}))


class FakeConfigManager:
    def __init__(self):
        self.standard = {
            "model_id": "model-x",
            "region_name": "us-east-1",
            "temperature": 0.4,
            "max_tokens": 777,
            "top_p": 0.9,
            "additional_request_fields": {"extra": ["one"]},
        }
        self.local = {
            "host": "http://ollama",
            "model_id": "llama3",
            "temperature": 0.2,
            "max_tokens": 512,
            "timeout": 33,
            "keep_alive": "5m",
            "options": {"num_ctx": 4096},
        }
        self.env = {
            "AWS_PROFILE": "profile",
            "AWS_ROLE_ARN": "role",
            "AWS_ROLE_SESSION_NAME": "session",
            "AWS_STS_ENDPOINT": "https://sts",
            "AWS_EXTERNAL_ID": "external",
            "SAGEMAKER_BASE_URL": "https://sage",
            "GEMINI_API_KEY": "gem-key",
            "REASONING_EFFORT": "high",
            "REASONING_VERBOSITY": "low",
        }

    def get_standard_model_config(self, model_id, region_name, provider):
        config = dict(self.standard)
        config["model_id"] = model_id
        config["region_name"] = region_name
        return config

    def get_local_model_config(self, model_id, _provider):
        config = dict(self.local)
        config["model_id"] = model_id
        return config

    def get_thinking_model_config(self, model_id, region_name):
        return {
            "model_id": model_id,
            "region_name": region_name,
            "temperature": 0.1,
            "max_tokens": 999,
            "additional_request_fields": {"anthropic_beta": ["existing"]},
        }

    def get_server_config(self, _provider):
        return SimpleNamespace(
            llm=SimpleNamespace(model_id="primary", temperature=0.3, max_tokens=600),
            swarm=SimpleNamespace(llm=SimpleNamespace(model_id="swarm", temperature=0.6, max_tokens=700)),
        )

    def is_thinking_model(self, _provider, model_id):
        return model_id == "thinking"

    def getenv(self, name, default=None):
        return self.env.get(name, default)

    def getenv_int(self, name, default=0):
        return int(self.env.get(name, default))

    def get_provider(self):
        return "ollama"

    def get_llm_config(self, _provider):
        return SimpleNamespace(model_id="llama3")

    def get_default_region(self):
        return "us-east-1"

    def get_rate_limit_config(self, _provider):
        return SimpleNamespace(rate=1)


def fake_capabilities(**overrides):
    defaults = {
        "supports_temperature": True,
        "supports_reasoning": True,
        "pass_reasoning_effort": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def config_manager(monkeypatch):
    manager = FakeConfigManager()
    monkeypatch.setattr(mod, "_get_config_manager", lambda: manager)
    monkeypatch.setattr(mod, "get_capabilities", lambda *_args: fake_capabilities())
    return manager


def test_create_bedrock_model_standard_and_thinking(monkeypatch, config_manager):
    import strands.models

    monkeypatch.setattr(strands.models, "BedrockModel", FakeModel)
    standard = mod.create_bedrock_model("standard", "us-east-1", role="primary", effort="medium")
    assert standard.model_id == "standard"
    assert standard.kwargs["additional_request_fields"]["output_config"]["effort"] == "medium"
    assert standard._output_tokens == 600

    thinking = mod.create_bedrock_model("thinking", "us-east-1", role="primary")
    assert thinking.kwargs["max_tokens"] == 999
    assert "existing" in thinking.kwargs["additional_request_fields"]["anthropic_beta"]


def test_create_ollama_litellm_and_gemini_models(monkeypatch, config_manager):
    import modules.config.models as models_pkg
    import modules.config.models.ollama as ollama_mod
    import modules.agents.patches as patches
    import strands.models.gemini as gemini_mod
    import strands.models.litellm as litellm_mod

    monkeypatch.setattr(ollama_mod, "OllamaModel", FakeModel)
    monkeypatch.setattr(models_pkg, "get_capabilities", lambda *_args: fake_capabilities())
    monkeypatch.setattr(patches, "patch_ollama_model_json_toolcalls", Mock())
    ollama_model = mod.create_ollama_model("llama3", role="primary")
    assert ollama_model.kwargs["additional_args"]["think"] == "medium"
    assert ollama_model._output_tokens == 512

    fake_litellm = SimpleNamespace(get_max_tokens=Mock(return_value=500), context_window_fallbacks=None)
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    monkeypatch.setattr(litellm_mod, "LiteLLMModel", FakeModel)
    monkeypatch.setenv("CYBER_CONTEXT_WINDOW_FALLBACKS", "bedrock/model:bedrock/fallback")
    litellm_model = mod.create_litellm_model("bedrock/model", "us-east-1", role="primary")
    assert litellm_model.kwargs["client_args"]["aws_region_name"] == "us-east-1"
    assert litellm_model.kwargs["client_args"]["aws_profile_name"] == "profile"
    assert litellm_model.kwargs["params"]["max_tokens"] == 500
    assert "thinking" in litellm_model.kwargs["client_args"]

    monkeypatch.setattr(gemini_mod, "GeminiModel", FakeModel)
    gemini_model = mod.create_gemini_model("gemini/gemini-pro", "us-east-1", role="primary")
    assert gemini_model.model_id == "gemini-pro"
    assert gemini_model.kwargs["params"]["max_output_tokens"] == 600

    config_manager.env.pop("GEMINI_API_KEY")
    with pytest.raises(ValueError):
        mod.create_gemini_model("gemini/gemini-pro", "us-east-1")


def test_create_strands_dispatch_all_providers_and_rate_limits(monkeypatch, config_manager):
    monkeypatch.setattr(mod, "create_bedrock_model", Mock(return_value="bedrock"))
    monkeypatch.setattr(mod, "create_litellm_model", Mock(return_value="litellm"))
    monkeypatch.setattr(mod, "create_gemini_model", Mock(return_value="gemini"))
    monkeypatch.setattr(mod, "print_status", Mock())

    assert mod.create_strands_model("bedrock", "m") == "bedrock"
    assert mod.create_strands_model("litellm", "m") == "litellm"
    assert mod.create_strands_model("gemini", "m") == "gemini"
    with pytest.raises(ValueError):
        mod.create_strands_model("bad", "m")


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
