#!/usr/bin/env python3
"""Tests for logger factory."""

import logging

from modules.config.system import logger as logger_module
from modules.config.system.logger import (
    configure_provider_diagnostic_logging,
    configure_sdk_logging,
    get_logger,
    initialize_logger_factory,
    reset_logger_factory,
    unsafe_diagnostic_logging_enabled,
)


def test_get_logger_creates_logger():
    """Test that get_logger creates a logger."""
    reset_logger_factory()
    logger = get_logger("Test.Component")
    assert logger is not None
    assert isinstance(logger, logging.Logger)
    assert logger.name == "Test.Component"


def test_get_logger_caches_loggers():
    """Test that get_logger caches and reuses loggers."""
    reset_logger_factory()
    logger1 = get_logger("Test.Component")
    logger2 = get_logger("Test.Component")
    assert logger1 is logger2


def test_different_components_get_different_loggers():
    """Test that different components get different loggers."""
    reset_logger_factory()
    logger1 = get_logger("Agents.CyberAutoAgent")
    logger2 = get_logger("Tools.Memory")
    assert logger1 is not logger2
    assert logger1.name == "Agents.CyberAutoAgent"
    assert logger2.name == "Tools.Memory"


def test_reset_clears_registry():
    """Test that reset_logger_factory clears the registry."""
    reset_logger_factory()
    get_logger("Test.Component1")
    get_logger("Test.Component2")
    from modules.config.system.logger import _logger_registry

    assert len(_logger_registry) == 2
    reset_logger_factory()
    assert len(_logger_registry) == 0


def test_diagnostic_logging_requires_explicit_unsafe_environment(monkeypatch):
    monkeypatch.delenv("CYBER_UNSAFE_DIAGNOSTIC_LOGGING", raising=False)
    assert unsafe_diagnostic_logging_enabled() is False

    monkeypatch.setenv("CYBER_UNSAFE_DIAGNOSTIC_LOGGING", " TRUE ")
    assert unsafe_diagnostic_logging_enabled() is True

    configure_provider_diagnostic_logging(enable_debug=True)
    assert all(logging.getLogger(name).level == logging.DEBUG for name in logger_module._RAW_DIAGNOSTIC_LOGGERS)

    monkeypatch.setenv("CYBER_UNSAFE_DIAGNOSTIC_LOGGING", "false")
    configure_provider_diagnostic_logging(enable_debug=True)
    assert all(logging.getLogger(name).level == logging.WARNING for name in logger_module._RAW_DIAGNOSTIC_LOGGERS)


def test_sdk_logging_configures_component_levels_and_compatibility_initializer(monkeypatch):
    monkeypatch.setenv("CYBER_UNSAFE_DIAGNOSTIC_LOGGING", "true")
    reset_logger_factory()

    initialize_logger_factory(log_file="ignored.log", verbose=True)
    configure_sdk_logging(enable_debug=True)

    debug_loggers = (
        "strands",
        "strands.multiagent",
        "strands.tools.registry",
        "strands_tools.swarm",
        "modules.handlers",
        "modules.handlers.react",
    )
    assert all(logging.getLogger(name).level == logging.DEBUG for name in debug_loggers)
    assert all(logging.getLogger(name).level == logging.DEBUG for name in logger_module._RAW_DIAGNOSTIC_LOGGERS)
    assert get_logger("Config.SDK").name == "Config.SDK"

    configure_sdk_logging(enable_debug=False)
    assert logging.getLogger("strands").level == logging.INFO
    assert logging.getLogger("openai").level == logging.WARNING
