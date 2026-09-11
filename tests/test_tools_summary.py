#!/usr/bin/env python3
"""
Tests for tools summary formatting.
- Accepts both list (with duplicates) and dict(name -> count)
- Deterministic sort and proper pluralization
"""

from modules.prompts.factory import format_tools_summary


def test_format_tools_summary_from_list_is_unique_and_filters_bookkeeping():
    tools = ["shell", "shell", "store_observation", "python_repl", "shell"]
    summary = format_tools_summary(tools)
    lines = summary.splitlines()
    assert lines == ["- shell", "- python_repl"]
    assert "store_observation" not in summary


def test_format_tools_summary_from_dict_uses_unique_keys():
    tools = {"python_repl": 2, "shell": 5, "store_finding": 2}
    summary = format_tools_summary(tools)
    lines = summary.splitlines()
    assert lines == ["- python_repl", "- shell"]


def test_format_tools_summary_is_one_unified_unique_list():
    summary = format_tools_summary(["shell", "record_task_acceptance", "http_request", "curl", "curl"])

    assert summary.splitlines() == ["- shell", "- http_request", "- curl"]
    assert "record_task_acceptance" not in summary
