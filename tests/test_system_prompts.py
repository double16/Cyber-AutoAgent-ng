#!/usr/bin/env python3

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from modules.config.types import BudgetConfig
from modules.prompts import get_system_prompt, load_prompt_template
from modules.prompts.factory import get_reflection_snapshot

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

        assert "test.com" in prompt
        assert "test objective" not in prompt
        assert "OP_20240101_120000" in prompt
        assert "CRITICAL FIRST ACTION" in prompt

    def test_get_system_prompt_with_memory_path(self):
        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            has_memory_path=True,
        )

        assert "CRITICAL FIRST ACTIONS**\n  1. Load all memories" in prompt
        assert "mem0_list(" in prompt
        assert "Memory Intake Pass" in prompt

    def test_get_system_prompt_with_existing_memories(self):
        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            has_existing_memories=True,
        )

        assert "CRITICAL FIRST ACTIONS**\n  1. Load all memories" in prompt
        assert "mem0_list(" in prompt
        assert "Memory Intake Pass" in prompt

    def test_get_system_prompt_with_both_memory_flags(self):
        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            has_memory_path=True,
            has_existing_memories=True,
        )

        assert "CRITICAL FIRST ACTIONS**\n  1. Load all memories" in prompt
        assert "mem0_list(" in prompt
        assert "Memory Intake Pass" in prompt

    def test_get_system_prompt_no_memory_flags(self):
        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            has_memory_path=False,
            has_existing_memories=False,
        )

        assert "CRITICAL FIRST ACTION" in prompt
        assert "Create a strategic plan via" in prompt

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
        assert "test.com" in prompt
        assert "CRITICAL FIRST ACTION" in prompt

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

        assert "test.com" in prompt_local
        assert "test.com" in prompt_remote
        assert "test objective" not in prompt_local
        assert "test objective" not in prompt_remote


class TestMemoryInstructions:
    def test_memory_instruction_priority(self):
        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            has_memory_path=True,
            has_existing_memories=False,
        )

        assert "CRITICAL FIRST ACTIONS**\n  1. Load all memories" in prompt

    def test_memory_instruction_existing_only(self):
        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            has_memory_path=False,
            has_existing_memories=True,
        )

        assert "CRITICAL FIRST ACTIONS**\n  1. Load all memories" in prompt

    def test_memory_instruction_fresh_operation(self):
        prompt = get_system_prompt(
            target="test.com",
            objective="test objective",
            operation_id="OP_20240101_120000",
            budget=_budget(),
            has_memory_path=False,
            has_existing_memories=False,
        )

        assert "CRITICAL FIRST ACTION" in prompt
        assert "Create a strategic plan" in prompt


class TestReflectionSnapshot:
    @pytest.mark.parametrize("progress", [0, 10])
    def test_before_first_checkpoint(self, progress):
        snapshot = get_reflection_snapshot(progress, _budget(), None)
        assert f"Budget Used: {progress}%" in snapshot
        assert "duration cap 60m" in snapshot
        assert "token cap 100000" in snapshot
        assert "cost cap 10.0" in snapshot
        assert "Next Checkpoint: 20% budget use" in snapshot
        assert "\nCurrent Phase:" not in snapshot

    @pytest.mark.parametrize("progress, checkpoint, action", [
        (20, 20, "Evaluate: What capabilities gained? Phase 1 criteria met?"),
        (40, 40, "Confidence trend rising/flat/falling? Flat = pivot NOW."),
        (60, 60, "If stuck (no findings), deploy swarm with different approach classes."),
        (80, 80, "Focus ONLY on highest-confidence path. No new exploration."),
    ])
    def test_checkpoints(self, progress, checkpoint, action):
        snapshot = get_reflection_snapshot(progress, _budget(), None)
        assert f"Budget Used: {progress}%" in snapshot
        assert f"**CHECKPOINT {checkpoint}% REACHED**" in snapshot
        assert action in snapshot

    def test_current_phase_and_urgency(self):
        snapshot = get_reflection_snapshot(95, _budget(), 4)
        assert "Budget Used: 95%" in snapshot
        assert "Current Phase: 4" in snapshot
        assert "FINAL: Budget >90%" in snapshot


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
