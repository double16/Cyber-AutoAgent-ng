#!/usr/bin/env python3
"""
Tests for tools summary formatting.
- Accepts both list (with duplicates) and dict(name -> count)
- Deterministic sort and proper pluralization
"""

from modules.prompts.factory import format_tools_summary


def test_format_tools_summary_from_list_counts_and_sorts():
    tools = ["shell", "shell", "store_knowledge", "python_repl", "shell"]
    summary = format_tools_summary(tools)
    lines = summary.splitlines()
    # shell appears first with 3 uses
    assert lines[0] == "- shell: 3 uses"
    # then python_repl and store_knowledge with 1 use each (alphabetical)
    assert "- store_knowledge: 1 use" in summary
    assert "- python_repl: 1 use" in summary


def test_format_tools_summary_from_dict_counts_and_sorts():
    tools = {"python_repl": 2, "shell": 5, "store_knowledge": 2}
    summary = format_tools_summary(tools)
    lines = summary.splitlines()
    assert lines[0] == "- shell: 5 uses"
    # 2-use tools are sorted by name
    two_use_lines = [line for line in lines if line.endswith("2 uses")]
    assert two_use_lines == ["- python_repl: 2 uses", "- store_knowledge: 2 uses"]
