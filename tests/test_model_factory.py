import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.config.models import factory as mod
from modules.config.types import SDKConfig


class FakeModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.config = kwargs
        self.model_id = kwargs.get("model_id")
        self.client_args = kwargs.get("client_args", kwargs.get("ollama_client_args", {}))

    def update_config(self, **kwargs):
        self.config.update(kwargs)

    def get_config(self):
        return self.config

    @property
    def context_window_limit(self):
        return self.config.get("context_window_limit")


class FakeConfigManager:
    def __init__(self):
        self.sdk_config = SDKConfig()
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

    def get_sdk_config(self, _provider):
        return self.sdk_config

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
    standard = mod.create_bedrock_model("standard", "us-east-1", role="plan_creator", effort="medium")
    assert standard.model_id == "standard"
    assert standard.kwargs["additional_request_fields"]["output_config"]["effort"] == "medium"
    assert standard.kwargs["streaming"] is False
    assert standard._output_tokens == 8192
    assert "top_p" not in standard.kwargs

    thinking = mod.create_bedrock_model("thinking", "us-east-1", role="plan_creator")
    assert thinking.kwargs["max_tokens"] == 8192
    assert thinking.kwargs["streaming"] is False
    assert "existing" in thinking.kwargs["additional_request_fields"]["anthropic_beta"]


def test_create_bedrock_model_passes_configured_profile_top_p(monkeypatch, config_manager):
    import strands.models

    import modules.config.models.agent_profiles as profiles

    registry = profiles.AgentSettingsRegistry(
        custom_defaults={
            "plan_creator": profiles.AgentModelSettings(
                temperature=0.2,
                reasoning_level=profiles.ReasoningLevel.MEDIUM,
                top_p=0.8,
                max_tokens=8192,
            )
        }
    )
    monkeypatch.setattr(profiles, "get_agent_settings_registry", lambda: registry)
    monkeypatch.setattr(strands.models, "BedrockModel", FakeModel)

    model = mod.create_bedrock_model("standard", "us-east-1", role="plan_creator")

    assert model.kwargs["top_p"] == 0.8


def test_create_ollama_litellm_and_gemini_models(monkeypatch, config_manager):
    import modules.config.models as models_pkg
    models_pkg.reset_agent_settings_registry()
    import strands.models.gemini as gemini_mod
    import strands.models.litellm as litellm_mod

    import modules.config.models.ollama as ollama_mod
    from modules.agents import patches

    monkeypatch.setattr(ollama_mod, "OllamaModel", FakeModel)
    monkeypatch.setattr(models_pkg, "get_capabilities", lambda *_args: fake_capabilities())
    monkeypatch.setattr(patches, "patch_ollama_model_json_toolcalls", Mock())
    ollama_model = mod.create_ollama_model("llama3", role="plan_creator")
    assert ollama_model.kwargs["additional_args"]["think"] == "medium"
    assert ollama_model.kwargs["stream"] is False
    assert ollama_model._output_tokens == 8192

    fake_litellm = SimpleNamespace(get_max_tokens=Mock(return_value=500), context_window_fallbacks=None)
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    monkeypatch.setattr(litellm_mod, "LiteLLMModel", FakeModel)
    monkeypatch.setenv("CYBER_CONTEXT_WINDOW_FALLBACKS", "bedrock/model:bedrock/fallback")
    litellm_model = mod.create_litellm_model("bedrock/model", "us-east-1", role="plan_creator")
    assert litellm_model.kwargs["client_args"]["aws_region_name"] == "us-east-1"
    assert litellm_model.kwargs["client_args"]["aws_profile_name"] == "profile"
    assert litellm_model.kwargs["params"]["max_tokens"] == 500
    assert litellm_model.kwargs["stream"] is False
    assert "thinking" in litellm_model.kwargs["client_args"]

    monkeypatch.setattr(gemini_mod, "GeminiModel", FakeModel)
    gemini_model = mod.create_gemini_model("gemini/gemini-pro", "us-east-1", role="plan_creator")
    assert gemini_model.model_id == "gemini-pro"
    assert gemini_model.kwargs["params"]["max_output_tokens"] == 8192

    config_manager.env.pop("GEMINI_API_KEY")
    with pytest.raises(ValueError):
        mod.create_gemini_model("gemini/gemini-pro", "us-east-1")


def test_create_ollama_model_passes_max_string_reasoning(monkeypatch, config_manager):
    import modules.config.models as models_pkg
    import modules.config.models.agent_profiles as profiles
    import modules.config.models.ollama as ollama_mod
    from modules.agents import patches

    registry = profiles.AgentSettingsRegistry(
        custom_defaults={
            "plan_creator": profiles.AgentModelSettings(
                reasoning_level=profiles.ReasoningLevel.MAX,
                max_tokens=8192,
            )
        }
    )
    monkeypatch.setattr(profiles, "get_agent_settings_registry", lambda: registry)
    monkeypatch.setattr(ollama_mod, "OllamaModel", FakeModel)
    monkeypatch.setattr(models_pkg, "get_capabilities", lambda *_args: fake_capabilities())
    monkeypatch.setattr(patches, "patch_ollama_model_json_toolcalls", Mock())

    model = mod.create_ollama_model("any-thinking-model", role="plan_creator")

    assert model.kwargs["additional_args"]["think"] == "max"


def test_create_ollama_model_passes_xhigh_string_reasoning(monkeypatch, config_manager):
    import modules.config.models as models_pkg
    import modules.config.models.agent_profiles as profiles
    import modules.config.models.ollama as ollama_mod
    from modules.agents import patches

    registry = profiles.AgentSettingsRegistry(
        custom_defaults={
            "plan_creator": profiles.AgentModelSettings(
                reasoning_level=profiles.ReasoningLevel.XHIGH,
                max_tokens=8192,
            )
        }
    )
    monkeypatch.setattr(profiles, "get_agent_settings_registry", lambda: registry)
    monkeypatch.setattr(ollama_mod, "OllamaModel", FakeModel)
    monkeypatch.setattr(models_pkg, "get_capabilities", lambda *_args: fake_capabilities())
    monkeypatch.setattr(patches, "patch_ollama_model_json_toolcalls", Mock())

    model = mod.create_ollama_model("any-thinking-model", role="plan_creator")

    assert model.kwargs["additional_args"]["think"] == "xhigh"


def test_prompt_limit_helper_uses_context_window_registry_and_output_fallbacks(monkeypatch):
    context_calls = []
    fake_litellm = SimpleNamespace(
        get_context_window=lambda candidate: context_calls.append(candidate) or (4096 if candidate == "model" else 0),
        model_cost={},
        get_max_tokens=lambda _candidate: 0,
    )
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    assert mod._get_prompt_limit_from_model("openrouter/vendor/model") == 4096
    assert context_calls == ["vendor/model", "model"]

    fake_litellm.get_context_window = None
    fake_litellm.model_cost = {"model": {"max_input_tokens": 8192}}
    assert mod._get_prompt_limit_from_model("model") == 8192

    fake_litellm.model_cost = {"model": {"context_window": 2048}}
    assert mod._get_prompt_limit_from_model("model") == 2048

    fake_litellm.model_cost = {}
    fake_litellm.get_max_tokens = lambda _candidate: 1024
    assert mod._get_prompt_limit_from_model("model") == 1024


def test_resolve_prompt_limit_covers_force_static_litellm_and_provider_fallbacks(monkeypatch):
    monkeypatch.setenv("CYBER_PROMPT_LIMIT_FORCE", "1234")
    mod._resolve_prompt_token_limit.cache_clear()
    assert mod._resolve_prompt_token_limit("bedrock", "model") == 1234

    monkeypatch.setenv("CYBER_PROMPT_LIMIT_FORCE", "invalid")
    monkeypatch.setattr(mod, "PROMPT_TOKEN_FALLBACK_LIMIT", 0)
    monkeypatch.setattr(mod, "get_model_input_limit", lambda _model: 4096)
    mod._resolve_prompt_token_limit.cache_clear()
    assert mod._resolve_prompt_token_limit("bedrock", "model") == 4096

    monkeypatch.setattr(mod, "get_model_input_limit", lambda _model: None)
    monkeypatch.setattr(mod, "_get_prompt_limit_from_model", lambda _model: 2048)
    mod._resolve_prompt_token_limit.cache_clear()
    assert mod._resolve_prompt_token_limit("litellm", "model") == 2048

    monkeypatch.setattr(mod, "_get_prompt_limit_from_model", lambda _model: None)
    monkeypatch.setattr(mod, "get_provider_default_limit", lambda provider: 32000 if provider == "ollama" else None)
    mod._resolve_prompt_token_limit.cache_clear()
    assert mod._resolve_prompt_token_limit("ollama", None) == 32000


def test_resolve_prompt_limit_uses_ollama_environment_show_and_process_metadata(monkeypatch):
    monkeypatch.delenv("CYBER_PROMPT_LIMIT_FORCE", raising=False)
    monkeypatch.setattr(mod, "PROMPT_TOKEN_FALLBACK_LIMIT", 0)
    monkeypatch.setattr(mod, "get_model_input_limit", lambda _model: None)
    monkeypatch.setattr(mod, "get_provider_default_limit", lambda _provider: None)

    class Reader:
        def __init__(self, value):
            self.value = value

        def get_int(self, _name, _default):
            return self.value

        def get(self, _name, default=None):
            return default

    monkeypatch.setattr(mod, "EnvironmentReader", lambda: Reader(4096))
    mod._resolve_prompt_token_limit.cache_clear()
    assert mod._resolve_prompt_token_limit("ollama", "model") == 4096

    show_response = SimpleNamespace(parameters="num_ctx 8192")
    client = SimpleNamespace(show=lambda **_kwargs: show_response)
    monkeypatch.setattr(mod, "EnvironmentReader", lambda: Reader(0))
    monkeypatch.setattr(mod.ollama, "Client", lambda **_kwargs: client)
    mod._resolve_prompt_token_limit.cache_clear()
    assert mod._resolve_prompt_token_limit("ollama", "model") == 8192

    process = SimpleNamespace(model="model", context_length=16384)
    client.show = lambda **_kwargs: SimpleNamespace(parameters="")
    client.generate = lambda **_kwargs: None
    client.ps = lambda: SimpleNamespace(models=[process])
    mod._resolve_prompt_token_limit.cache_clear()
    assert mod._resolve_prompt_token_limit("ollama", "model") == 16384


def test_create_ollama_model_reuses_learned_think_fallback(monkeypatch, config_manager):
    import modules.config.models as models_pkg
    import modules.config.models.agent_profiles as profiles
    import modules.config.models.ollama as ollama_mod
    from modules.agents import patches

    registry = profiles.AgentSettingsRegistry()
    registry.record_parameter_fallback("ollama", "any-thinking-model", "think", True)
    monkeypatch.setattr(profiles, "get_agent_settings_registry", lambda: registry)
    monkeypatch.setattr(ollama_mod, "OllamaModel", FakeModel)
    monkeypatch.setattr(models_pkg, "get_capabilities", lambda *_args: fake_capabilities())
    monkeypatch.setattr(patches, "patch_ollama_model_json_toolcalls", Mock())

    model = mod.create_ollama_model("any-thinking-model", role="plan_creator")

    assert model.kwargs["additional_args"]["think"] is True


def test_provider_models_receive_plan_critic_profile(monkeypatch, config_manager):
    import strands.models
    import strands.models.gemini as gemini_mod
    import strands.models.litellm as litellm_mod

    import modules.config.models as models_pkg
    import modules.config.models.ollama as ollama_mod
    from modules.agents import patches

    models_pkg.reset_agent_settings_registry()
    monkeypatch.setattr(strands.models, "BedrockModel", FakeModel)
    monkeypatch.setattr(ollama_mod, "OllamaModel", FakeModel)
    monkeypatch.setattr(gemini_mod, "GeminiModel", FakeModel)
    monkeypatch.setattr(litellm_mod, "LiteLLMModel", FakeModel)
    monkeypatch.setattr(patches, "patch_ollama_model_json_toolcalls", Mock())
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(get_max_tokens=Mock(return_value=None)))

    bedrock = mod.create_bedrock_model("standard", "us-east-1", role="plan_critic")
    ollama = mod.create_ollama_model("llama3", role="plan_critic")
    litellm = mod.create_litellm_model("openai/model", "us-east-1", role="plan_critic")
    gemini = mod.create_gemini_model("gemini/gemini-pro", "us-east-1", role="plan_critic")

    assert bedrock.kwargs["temperature"] == 0.0
    assert ollama.kwargs["temperature"] == 0.0
    assert litellm.kwargs["params"]["temperature"] == 0.0
    assert gemini.kwargs["params"]["temperature"] == 0.0
    assert {model._cyber_llm_role for model in (bedrock, ollama, litellm, gemini)} == {"plan_critic"}


def test_create_models_propagate_enabled_sdk_streaming(monkeypatch, config_manager):
    import strands.models
    import strands.models.litellm as litellm_mod

    import modules.config.models as models_pkg
    import modules.config.models.ollama as ollama_mod
    from modules.agents import patches

    config_manager.sdk_config = SDKConfig(enable_streaming=True)
    monkeypatch.setattr(strands.models, "BedrockModel", FakeModel)
    monkeypatch.setattr(ollama_mod, "OllamaModel", FakeModel)
    monkeypatch.setattr(models_pkg, "get_capabilities", lambda *_args: fake_capabilities())
    monkeypatch.setattr(patches, "patch_ollama_model_json_toolcalls", Mock())
    fake_litellm = SimpleNamespace(get_max_tokens=Mock(return_value=500), context_window_fallbacks=None)
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    monkeypatch.setattr(litellm_mod, "LiteLLMModel", FakeModel)

    bedrock_model = mod.create_bedrock_model("standard", "us-east-1", role="plan_creator")
    ollama_model = mod.create_ollama_model("llama3", role="plan_creator")
    litellm_model = mod.create_litellm_model("bedrock/model", "us-east-1", role="plan_creator")

    assert bedrock_model.kwargs["streaming"] is True
    assert ollama_model.kwargs["stream"] is True
    assert litellm_model.kwargs["stream"] is True


def test_create_strands_dispatch_all_providers_and_rate_limits(monkeypatch, config_manager):
    monkeypatch.setattr(mod, "create_bedrock_model", Mock(return_value=FakeModel(model_id="bedrock")))
    monkeypatch.setattr(mod, "create_litellm_model", Mock(return_value=FakeModel(model_id="litellm")))
    monkeypatch.setattr(mod, "create_gemini_model", Mock(return_value=FakeModel(model_id="gemini")))
    monkeypatch.setattr(mod, "_resolve_prompt_token_limit", lambda _provider, _model_id: 48000)
    monkeypatch.setattr(mod, "print_status", Mock())

    assert mod.create_strands_model("bedrock", "m").context_window_limit == 48000
    assert mod.create_strands_model("litellm", "m").context_window_limit == 48000
    assert mod.create_strands_model("gemini", "m").context_window_limit == 48000
    with pytest.raises(ValueError):
        mod.create_strands_model("bad", "m")


def test_require_prompt_token_limit_rejects_unresolved_context(monkeypatch):
    monkeypatch.setattr(mod, "_resolve_prompt_token_limit", lambda _provider, _model_id: None)

    with pytest.raises(ValueError, match="CYBER_CONTEXT_LIMIT"):
        mod.require_prompt_token_limit("unknown", "unlisted-model")


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


def test_get_parameters_by_role_preserves_profile_when_server_config_lookup_fails(monkeypatch):
    monkeypatch.setattr(mod, "_get_config_manager", Mock(side_effect=RuntimeError("missing config")))

    params = mod._get_parameters_by_role(
        "litellm",
        "model",
        "plan_creator",
        {"temperature": 0.2, "max_tokens": 123},
    )

    assert params.llm_temp == 0.2
    assert params.llm_max == 8192
    assert params.role == "plan_creator"


def test_get_parameters_by_role_keeps_profile_temperature_over_generic_config(config_manager):
    params = mod._get_parameters_by_role(
        "litellm",
        "model",
        "plan_critic",
        {"temperature": 0.9, "max_tokens": 10000},
    )

    assert params.role == "plan_critic"
    assert params.llm_temp == 0.0
    assert params.llm_max == 4096


def test_get_parameters_by_role_uses_swarm_agent_output_ceiling(config_manager):
    params = mod._get_parameters_by_role(
        "litellm",
        "model",
        "swarm_agent",
        {"max_tokens": 999},
    )

    assert params.role == "swarm_agent"
    assert params.llm_max == 8192


def test_get_parameters_by_role_applies_explicit_output_ceiling(config_manager):
    params = mod._get_parameters_by_role(
        "ollama",
        "model",
        "plan_creator",
        {"max_tokens": 600, "max_tokens_ceiling": 600},
    )

    assert params.profile_max_tokens == 8192
    assert params.llm_max == 600


def test_thinking_model_disables_thinking_for_non_reasoning_profile(monkeypatch, config_manager):
    import strands.models

    config_manager.get_thinking_model_config = lambda model_id, region_name: {
        "model_id": model_id,
        "region_name": region_name,
        "temperature": 0.1,
        "max_tokens": 999,
        "additional_request_fields": {"thinking": {"type": "enabled", "budget_tokens": 700}},
    }
    monkeypatch.setattr(strands.models, "BedrockModel", FakeModel)

    model = mod.create_bedrock_model("thinking", "us-east-1", role="task_evaluator")

    assert model.kwargs["additional_request_fields"] == {'thinking': {'type': 'enabled', 'budget_tokens': 2048}}


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
    model = FakeModel(model_id="ollama-model", options={})
    monkeypatch.setattr(mod, "create_ollama_model", Mock(return_value=model))
    monkeypatch.setattr(mod, "_resolve_prompt_token_limit", lambda _provider, _model_id: 48000)
    monkeypatch.setattr(mod, "print_status", Mock())

    assert mod.create_strands_model() is model
    assert model.context_window_limit == 48000
    assert model.config["options"]["num_ctx"] == 48000

    monkeypatch.setattr(mod, "create_ollama_model", Mock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(mod, "_handle_model_creation_error", Mock())

    with pytest.raises(RuntimeError):
        mod.create_strands_model("ollama", "llama")

    mod._handle_model_creation_error.assert_called_once()


def test_prompt_limit_helpers_cover_model_cost_invalid_values_and_ollama_metadata(monkeypatch):
    fake_litellm = SimpleNamespace(
        get_context_window=lambda _model: None,
        get_max_tokens=lambda _model: "not-a-number",
        model_cost={"vendor/model": {"max_input_tokens": 1234}},
    )
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    assert mod._get_prompt_limit_from_model("vendor/model") == 1234
    assert mod._get_prompt_limit_from_model(None) is None

    class FakeEnvironment:
        def get_int(self, name, default):
            return 4096 if name == "OLLAMA_CONTEXT_LENGTH" else default

    monkeypatch.setattr(mod, "EnvironmentReader", FakeEnvironment)
    monkeypatch.setattr(mod, "PROMPT_TOKEN_FALLBACK_LIMIT", 3000)
    mod._resolve_prompt_token_limit.cache_clear()
    assert mod._resolve_prompt_token_limit("ollama", "local") == 3000


def test_prompt_limit_ollama_client_fallbacks_and_bad_forced_value(monkeypatch):
    class FakeEnvironment:
        def get_int(self, _name, default):
            return default

        def get(self, _name, default=None):
            return default

    class FakeClient:
        def show(self, **_kwargs):
            return SimpleNamespace(parameters="num_ctx 2048")

    monkeypatch.delenv("CYBER_PROMPT_LIMIT_FORCE", raising=False)
    monkeypatch.setattr(mod, "EnvironmentReader", FakeEnvironment)
    monkeypatch.setattr(mod.ollama, "Client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(mod, "PROMPT_TOKEN_FALLBACK_LIMIT", 0)
    mod._resolve_prompt_token_limit.cache_clear()
    assert mod._resolve_prompt_token_limit("ollama", "local") == 2048

    monkeypatch.setenv("CYBER_PROMPT_LIMIT_FORCE", "invalid")
    monkeypatch.setattr(mod, "get_model_input_limit", lambda _model: None)
    monkeypatch.setattr(mod, "get_provider_default_limit", lambda _provider: 777)
    mod._resolve_prompt_token_limit.cache_clear()
    assert mod._resolve_prompt_token_limit("unknown", "model") == 777


@pytest.mark.parametrize(
    ("provider", "integration_module", "chat_class_name", "model_module", "model_class_name"),
    [
        ("ollama", "langchain_ollama", "ChatOllama", "modules.config.models.ollama", "OllamaModel"),
        ("bedrock", "langchain_aws", "ChatBedrock", "strands.models", "BedrockModel"),
        ("litellm", "langchain_litellm", "ChatLiteLLM", "strands.models.litellm", "LiteLLMModel"),
        ("gemini", "langchain_google_genai", "ChatGoogleGenerativeAI", "strands.models.gemini", "GeminiModel"),
    ],
)
def test_configure_model_rate_limits_installs_provider_specific_patches(
    monkeypatch,
    provider,
    integration_module,
    chat_class_name,
    model_module,
    model_class_name,
):
    import importlib

    from modules.rate_limit import rate_limit as rate_limit_module

    class FakeLimiter:
        def __init__(self, config):
            self.config = config

    calls = []
    monkeypatch.setattr(mod, "_get_config_manager", lambda: SimpleNamespace(
        get_provider=lambda: provider,
        get_rate_limit_config=lambda requested_provider: {"provider": requested_provider},
    ))
    monkeypatch.setattr(rate_limit_module, "ThreadSafeRateLimiter", FakeLimiter)
    monkeypatch.setattr(
        rate_limit_module,
        "patch_model_provider_class",
        lambda model_class, limiter: calls.append(("model", model_class, limiter.config)),
    )
    monkeypatch.setattr(
        rate_limit_module,
        "patch_langchain_chat_class_generate",
        lambda chat_class, limiter: calls.append(("chat", chat_class, limiter.config)),
    )
    chat_class = type(chat_class_name, (), {})
    monkeypatch.setitem(sys.modules, integration_module, SimpleNamespace(**{chat_class_name: chat_class}))
    target_module = importlib.import_module(model_module)
    model_class = type(model_class_name, (), {})
    monkeypatch.setattr(target_module, model_class_name, model_class)

    mod.configure_model_rate_limits(provider)

    assert calls == [
        ("model", model_class, {"provider": provider}),
        ("chat", chat_class, {"provider": provider}),
    ]


def test_configure_model_rate_limits_skips_missing_config_and_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(mod, "_get_config_manager", lambda: SimpleNamespace(
        get_provider=lambda: "ollama",
        get_rate_limit_config=lambda _provider: None,
    ))
    assert mod.configure_model_rate_limits() is None

    monkeypatch.setattr(mod, "_get_config_manager", lambda: SimpleNamespace(
        get_provider=lambda: "unknown",
        get_rate_limit_config=lambda _provider: {},
    ))
    with pytest.raises(ValueError, match="Unsupported provider"):
        mod.configure_model_rate_limits()


def test_litellm_model_applies_sagemaker_reasoning_and_azure_response_options(monkeypatch, config_manager):
    import strands.models.litellm as litellm_module

    from modules.config.models.agent_profiles import ReasoningLevel

    config_manager.env.update({
        "LITELLM_TIMEOUT": "12",
        "LITELLM_NUM_RETRIES": "0",
        "MAX_COMPLETION_TOKENS": "333",
    })
    parameters = SimpleNamespace(
        llm_temp=0.2,
        llm_max=500,
        top_k=12,
        top_p=0.7,
        reasoning_level=ReasoningLevel.HIGH,
        role=SimpleNamespace(value="task_executor"),
    )
    monkeypatch.setattr(mod, "_get_parameters_by_role", lambda *_args: parameters)
    monkeypatch.setattr(mod, "get_capabilities", lambda *_args: fake_capabilities())
    monkeypatch.setattr(litellm_module, "LiteLLMModel", FakeModel)
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(get_max_tokens=lambda _model: 400))
    monkeypatch.setattr(mod, "_apply_context_window_fallbacks", Mock())

    sagemaker = mod.create_litellm_model("sagemaker/model", "us-east-1", role="task_executor")

    assert sagemaker.kwargs["client_args"] == {
        "aws_region_name": "us-east-1",
        "aws_profile_name": "profile",
        "aws_role_name": "role",
        "aws_session_name": "session",
        "aws_sts_endpoint": "https://sts",
        "aws_external_id": "external",
        "sagemaker_base_url": "https://sage",
        "timeout": 12,
        "num_retries": 0,
        "max_retries": 0,
        "reasoning_effort": "high",
        "thinking": {"type": "enabled", "budget_tokens": 320},
    }
    assert sagemaker.kwargs["params"] == {
        "max_tokens": 400,
        "temperature": 0.2,
        "top_p": 0.7,
        "top_k": 12,
        "max_completion_tokens": 333,
    }

    azure = mod.create_litellm_model("azure/responses/model", "us-east-1", role="task_executor")
    assert azure.kwargs["params"]["text"] == {
        "format": {"type": "text"},
        "verbosity": "low",
    }

    config_manager.env.update({"LITELLM_TIMEOUT": "0", "LITELLM_NUM_RETRIES": "-1", "MAX_COMPLETION_TOKENS": "0"})
    monkeypatch.setattr(mod, "get_capabilities", lambda *_args: fake_capabilities(supports_temperature=False))
    no_transport_options = mod.create_litellm_model("openai/model", "us-east-1", role="task_executor")
    assert "temperature" not in no_transport_options.kwargs["params"]
    assert "timeout" not in no_transport_options.kwargs["client_args"]
    assert "num_retries" not in no_transport_options.kwargs["client_args"]


def test_get_model_timeout_reads_ollama_client_timeout(monkeypatch):
    import modules.config.models.ollama as ollama_module

    class FakeOllamaModel:
        def __init__(self, client_args):
            self.client_args = client_args

    monkeypatch.setattr(ollama_module, "OllamaModel", FakeOllamaModel)

    assert mod.get_model_timeout(FakeOllamaModel({"timeout": "9.5"}), default_timeout=7) == 9.5
    assert mod.get_model_timeout(FakeOllamaModel({}), default_timeout=7) == 7


def test_context_fallbacks_use_config_and_handle_litellm_import_failure(monkeypatch):
    monkeypatch.delenv("CYBER_CONTEXT_WINDOW_FALLBACKS", raising=False)
    manager = SimpleNamespace(
        get_context_window_fallbacks=lambda _provider: [{"source": ("one", "two")}]
    )
    monkeypatch.setattr(mod, "_get_config_manager", lambda: manager)
    assert mod._parse_context_window_fallbacks() == [{"source": ["one", "two"]}]

    monkeypatch.setattr(mod, "_parse_context_window_fallbacks", lambda: [{"a": ["b"]}])
    monkeypatch.setitem(sys.modules, "litellm", None)
    client_args = {"context_window_fallbacks": [{"existing": ["value"]}]}
    mod._apply_context_window_fallbacks(client_args)
    assert client_args["context_window_fallbacks"] == [{"existing": ["value"]}]


def test_create_strands_model_dispatches_bedrock_litellm_and_gemini(monkeypatch):
    manager = SimpleNamespace(
        get_default_region=lambda: "us-west-2",
        get_provider=lambda: "bedrock",
        get_llm_config=lambda _provider: SimpleNamespace(model_id="default"),
    )
    monkeypatch.setattr(mod, "_get_config_manager", lambda: manager)
    monkeypatch.setattr(mod, "apply_model_context_window", lambda model, *_args: 1)
    monkeypatch.setattr(mod, "print_status", lambda *_args: None)
    monkeypatch.setattr(
        "modules.config.models.capabilities.wrap_model_with_fallback", lambda model, *_args: model
    )
    for provider, creator_name in (
        ("bedrock", "create_bedrock_model"),
        ("litellm", "create_litellm_model"),
        ("gemini", "create_gemini_model"),
    ):
        model = FakeModel(model_id=provider)
        monkeypatch.setattr(mod, creator_name, lambda *_args, result=model, **_kwargs: result)
        assert mod.create_strands_model(provider, "model") is model


def test_model_helpers_cover_unavailable_ids_timeouts_and_no_rate_limit(monkeypatch):
    assert mod.get_model_id_from_model(SimpleNamespace(config={})) == ""
    assert mod.get_model_id_from_agent(SimpleNamespace(model=None)) == ""
    assert mod.get_provider_from_agent(SimpleNamespace(model=type("Other", (), {})())) is None

    import modules.config.models.ollama as ollama_mod

    class FakeOllama(ollama_mod.OllamaModel):
        @property
        def client_args(self):
            return {"timeout": "12.5"}

    assert mod.get_model_timeout(FakeOllama.__new__(FakeOllama), 5) == 12.5
    monkeypatch.setattr(mod, "_get_config_manager", lambda: SimpleNamespace(
        get_provider=lambda: "ollama", get_rate_limit_config=lambda _provider: None
    ))
    assert mod.configure_model_rate_limits() is None
