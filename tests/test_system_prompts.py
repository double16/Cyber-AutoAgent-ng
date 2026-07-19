#!/usr/bin/env python3

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from modules.config.types import BudgetConfig
from modules.prompts import get_system_prompt, load_prompt_template, get_task_capture_prompt

real_load_prompt_template = load_prompt_template


def _budget() -> BudgetConfig:
    return BudgetConfig(max_duration_minutes=60, max_tokens=100000, max_cost=10.0)


class TestGetSystemPrompt:
    def test_get_system_prompt_basic(self):
        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
        )

        assert "test objective" not in prompt
        assert "OP_20240101_120000" in prompt
        assert "Follow the selected module's" in prompt
        assert "Target infrastructure = remote endpoint" not in prompt
        assert "Never\n  infer authorization from available tools" in prompt

    @patch("modules.prompts.factory.load_prompt_template")
    def test_get_system_prompt_with_tools_context(self, mock_load_prompt_template):
        def side_effect(name: str, *args, **kwargs):
            real_template = real_load_prompt_template(name)
            if name == "system_prompt.md":
                real_template += " {{ environmental_context }} "
            return real_template

        mock_load_prompt_template.side_effect = side_effect
        tools_context = "## ENVIRONMENTAL CONTEXT\n\nTools: nmap, curl"

        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            tools_context=tools_context,
        )

        assert "ENVIRONMENTAL CONTEXT" in prompt
        assert "nmap, curl" in prompt

    def test_get_system_prompt_with_output_config(self):
        output_config = {
            "artifacts_path": "/custom/artifacts",
            "tools_path": "/custom/tools_path",
            "base_dir": "/custom/output",
            "target_name": "test_target",
            "enable_unified_output": True,
        }

        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            output_config=output_config,
        )

        assert "/custom/artifacts" in prompt
        assert "/custom/tools_path" in prompt

    def test_get_system_prompt_with_overlay_block(self, tmp_path):
        output_config = {"base_dir": str(tmp_path), "target_name": "test_target"}
        operation_id = "OP_20250101_000000"
        overlay_dir = tmp_path / "test_target" / operation_id
        overlay_dir.mkdir(parents=True, exist_ok=True)
        overlay_payload = {
            "version": 1,
            "origin": "agent_reflection",
            "budget_progress": 12,
            "payload": {"directives": ["Focus on consolidation"]},
        }
        (overlay_dir / "adaptive_prompt.json").write_text(json.dumps(overlay_payload), encoding="utf-8")

        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id=operation_id,
            budget=_budget(),
            output_config=output_config,
            progress_percent=20,
        )

        assert "## ADAPTIVE DIRECTIVES" in prompt
        assert "Focus on consolidation" in prompt
        assert "applied_progress=12%" in prompt

    def test_overlay_expires_after_progress(self, tmp_path):
        output_config = {"base_dir": str(tmp_path), "target_name": "test_target"}
        operation_id = "OP_20250101_000000"
        overlay_dir = tmp_path / "test_target" / operation_id
        overlay_dir.mkdir(parents=True, exist_ok=True)
        overlay_payload = {
            "version": 1,
            "origin": "agent_reflection",
            "budget_progress": 5,
            "expires_after_progress": 3,
            "payload": {"directives": ["Temporary directive"]},
        }
        overlay_file = overlay_dir / "adaptive_prompt.json"
        overlay_file.write_text(json.dumps(overlay_payload), encoding="utf-8")

        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id=operation_id,
            budget=_budget(),
            output_config=output_config,
            progress_percent=10,
        )

        assert "ADAPTIVE DIRECTIVES" not in prompt
        assert not overlay_file.exists()

    def test_get_system_prompt_different_servers(self):
        prompt_local = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            provider="ollama",
        )
        prompt_remote = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            provider="bedrock",
        )

        assert "test objective" not in prompt_local
        assert "test objective" not in prompt_remote

    def test_get_task_capture_basic(self):
        prompt = get_task_capture_prompt()

        assert "Task Capture Pass" in prompt

    def test_tools_guide_permits_overlapping_applicable_methods(self):
        prompt = load_prompt_template("tools_guide.md")

        assert "Use any native tool, optional tool, or shell command applicable to the task" in prompt
        assert "Overlap between native tools, optional tools" in prompt
        assert "required capability absent from native tools" not in prompt
        assert "Medium confidence (50-80%) → Parallel shell" not in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
