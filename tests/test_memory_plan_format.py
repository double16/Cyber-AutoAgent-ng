#!/usr/bin/env python3
"""Tests for memory tool plan formatting."""
from modules.tools.memory import OperationPlan, PlanPhase


def test_format_plan_as_toon_generates_compact_rows():
    plan = {
        "objective": "Assess api.example.com",
        "current_phase": 2,
        "total_phases": 3,
        "phases": [
            {"id": 1, "title": "Recon", "status": "done", "criteria": "map ports", "produces_hypotheses": False},
            {
                "id": 2,
                "title": "Testing",
                "status": "active",
                "criteria": "validate vulns",
                "produces_hypotheses": False,
            },
            {
                "id": 3,
                "title": "Exploit",
                "status": "pending",
                "criteria": "extract flag",
                "produces_hypotheses": False,
            },
        ],
    }

    toon = OperationPlan.from_obj(plan).to_toon()

    assert "plan_overview" in toon
    assert "plan_phases[3]" in toon
    assert "2,Testing,active,validate vulns" in toon


def test_plan_phase_to_toon():
    phase = {
        "id": 2,
        "title": "Testing",
        "status": "active",
        "criteria": "validate vulns",
        "produces_hypotheses": False,
    }
    toon = PlanPhase.from_obj(phase).to_toon()
    assert "plan_phases[1]" in toon
    assert "  2,Testing,active,validate vulns" in toon
