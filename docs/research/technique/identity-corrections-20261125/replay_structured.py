"""Structured reasoning outputs applied through the real correction controller's bound tools."""

import importlib.util
import json
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from pydantic import BaseModel, Field

from modules.techniques.roles import DeferredReviewPrompt, RolePrompt, TechniqueRoleRunner
from modules.tools.hypotheses import Hypothesis

SPEC = importlib.util.spec_from_file_location("controller_replay", Path(__file__).with_name("replay.py"))
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


class Revision(BaseModel):
    replacement: Hypothesis | list[Hypothesis] | None = Field(
        description="Complete corrected future proposal, independent atomic split proposals, or null to withdraw.",
    )
    reason: str = Field(min_length=1)


class Acceptance(BaseModel):
    record_acceptance: Literal[True]


class StructuredControllerRunner:
    """Keep model reasoning typed and mutation/provenance bookkeeping deterministic."""

    require_inspection = False

    def __init__(self, provider, model_id):
        self.runner = TechniqueRoleRunner(provider, model_id)

    def __call__(self, role, prompt, tools):
        terminals = [item for item in tools if replay.workflow.get_tool_name(item) in {
            "revise_hypothesis", "record_task_acceptance",
        }]
        if len(terminals) != 1:
            raise ValueError("Controller dispatch must bind exactly one terminal mutation")
        terminal = terminals[0]
        revision = replay.workflow.get_tool_name(terminal) == "revise_hypothesis"
        inspection = [item for item in tools if item is not terminal]
        instructions = (
            "Reason about the assigned correction using retained observations. Do not execute future tests. "
            "Use the supplied read-only inspection tools when necessary. Return the structured final output. "
            "Python applies that output through the bound terminal tool; references in the task packet to "
            "calling the revision or acceptance tool describe Python's finalization step. Do not serialize "
            "a replacement as a JSON string. Unknown policy remains conditional."
        )
        packet = RolePrompt(instructions, prompt.data)
        if revision and self.require_inspection:
            packet = DeferredReviewPrompt(
                "Inspect the explicitly linked retained source with the supplied read-only tools. "
                "Do not execute future tests or revise records during inspection. End with concise source "
                "observations.\n" + prompt.data,
                instructions + "\n" + prompt.data,
            )
        result = self.runner.structured(role, packet, Revision if revision else Acceptance, inspection)
        if revision:
            value = result.model_dump(exclude={"replacement": {"hypothesis_id"}})
            replacement = result.replacement
            if isinstance(replacement, list):
                value["replacement"] = [item.model_dump(exclude={"hypothesis_id"}) for item in replacement]
            elif replacement is not None:
                value["replacement"] = replacement.model_dump(exclude={"hypothesis_id"})
            terminal(**value)
        else:
            terminal()
        return result.model_dump_json()


def main():
    repository = Path("outputs/technique_review_trials")
    prior = [repository / "identity-corrections-20261125", repository / "identity-corrections-connected-20261125",
             repository / "identity-corrections-structured-20261125"]
    consumed = sum(json.loads((path / "requests.json").read_text())["requests"] for path in prior)
    replay.ROOT = (repository / "identity-corrections-final-20261125").resolve()
    replay.REQUEST_LIMIT = 96 - consumed
    # Freeze this driver as well as the dependency implementing the real dispatcher and persistence.
    replay.__file__ = __file__
    original_trial = replay.trial

    def trial(name, *args):
        # Frozen fixture metadata: only this case withholds a necessary linked consumer.
        with patch.object(StructuredControllerRunner, "require_inspection", name == "linked-dom"):
            return original_trial(name, *args)

    with patch.object(replay, "TechniqueRoleRunner", StructuredControllerRunner), patch.object(replay, "trial", trial):
        replay.main()
    (replay.ROOT / "dependency_replay_snapshot.py").write_bytes(Path(__file__).with_name("replay.py").read_bytes())
    (replay.ROOT / "prior-attempts.json").write_text(json.dumps({
        "prior_requests": consumed, "overall_limit": 96, "prior_roots": [str(path) for path in prior],
    }, indent=2))


if __name__ == "__main__":
    main()
