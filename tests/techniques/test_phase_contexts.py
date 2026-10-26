from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.agents.multi_agent_workflow import MultiAgentWorkflowController
from modules.agents.structured_outputs import PlanPhaseOutput
from modules.operation_plugins.planning_contracts import _parse_metadata_contract
from modules.tools.memory import PlanPhase


def test_phase_contexts_survive_wire_and_structured_output_round_trip():
    phase = PlanPhase(id=1, title="Arbitrary title", status="active", technique_contexts=("hypothesis_generation",))
    assert PlanPhase.from_obj(phase.to_dict()).technique_contexts == ("hypothesis_generation",)
    output = PlanPhaseOutput(
        id=1, title="Arbitrary title", produces_hypotheses=True, technique_contexts=["hypothesis_generation"]
    )
    assert PlanPhase.from_obj(output.model_dump()).technique_contexts == phase.technique_contexts
    assert (
        PlanPhase.from_obj(
            {"id": 1, "title": "Legacy", "status": "pending", "produces_hypotheses": False}
        ).technique_contexts
        == ()
    )


@pytest.mark.parametrize("contexts", ["hypothesis_generation", [""], [None], [42]])
def test_invalid_phase_context_metadata_is_rejected(contexts):
    with pytest.raises(ValueError):
        PlanPhase.from_obj(
            {
                "id": 1,
                "title": "Arbitrary",
                "status": "pending",
                "produces_hypotheses": False,
                "technique_contexts": contexts,
            }
        )
    with pytest.raises(ValueError):
        _parse_metadata_contract(
            "fixture", {"phase_id": 1, "task_creation_mode": "standard", "technique_contexts": contexts}
        )


def test_module_declared_phase_contexts_override_generated_metadata_without_title_inference(monkeypatch):
    contract = _parse_metadata_contract(
        "fixture",
        {
            "phase_id": 1,
            "task_creation_mode": "standard",
            "technique_contexts": ["reconnaissance"],
        },
    )
    monkeypatch.setattr(
        "modules.operation_plugins.planning_contracts.load_phase_metadata_contracts", lambda module: {1: contract}
    )
    controller = object.__new__(MultiAgentWorkflowController)
    controller.runtime = SimpleNamespace(config=SimpleNamespace(module="fixture"))
    controller._emit_workflow_event = Mock()
    data = {"phases": [{"id": 1, "title": "Completely unrelated title", "task_creation_mode": "standard"}]}
    normalized = controller._normalize_module_phase_metadata(data)
    assert normalized["phases"][0]["technique_contexts"] == ["reconnaissance"]
    assert "technique_contexts" not in data["phases"][0]
