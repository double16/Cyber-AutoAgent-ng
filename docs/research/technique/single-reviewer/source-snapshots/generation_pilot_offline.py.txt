"""Scripted SDK worker for exact generation inputs; never a semantic evaluation."""

import json
from pathlib import Path

from modules.techniques import roles
from modules.techniques.generation_pilot import run_generation
from modules.techniques.review_calibration_offline import OfflineReviewModel, configure_offline_roles


class GenerationContractModel(OfflineReviewModel):
    def __init__(self, case, mode):
        super().__init__((), "unused", mode)
        self.case = case
        self.role = ""

    def payload(self, properties):
        reason = "Scripted SDK contract check; no semantic judgment"
        urls = [source["url"] for source in self.case["sources"]]
        role = self.role
        if role == "technique_judge":
            if "generic_request" in properties:
                return {"generic_request": self.case["request"], "applicability": "conditional",
                        "unmet_prerequisites": ["Establish declared prerequisites"], "reason": reason}
            return {"selected_id": None, "reason": reason}
        if role == "technique_request_aligner":
            return {"adjusted_request": self.case["request"], "reason": reason}
        if role == "technique_request_reviewer":
            return {"approved": True, "reason": reason, "subject_preserved": True,
                    "mechanisms_preserved": True, "deliverables_preserved": True,
                    "additions": [], "omissions": [], "references": ["original_request"]}
        if role == "technique_source_assessor":
            return {"selected_urls": urls, "attributions": [{"url": url, "reason": reason} for url in urls],
                    "reason": reason, "authoritative_sufficient": True}
        if role in {"technique_requirement_assessor", "technique_requirement_reviewer"}:
            if len(self.case["requirements"]) != 1:
                raise ValueError("Scripted pilot worker expects one declared requirement per fixture")
            return {"requirement_id": self.case["requirements"][0]["id"], "source_urls": urls,
                    "explanation": reason, "authority_sufficient": True, "gaps": [],
                    "claim_support": [{"claim": reason, "source_urls": urls, "authority_basis": reason}]}
        if role == "technique_author":
            return {"metadata": {"title": "Scripted generation contract specimen",
                    "purpose": self.case["request"], "applicability": ["SDK preflight only"],
                    "phases": ["hypothesis_generation"], "triggers": [{"phases": ["hypothesis_generation"],
                    "when": "Scripted preflight", "request": self.case["request"], "signals_any": []}]},
                    "body": "# SCRIPTED SDK SPECIMEN\nNo semantic judgment or usable security method is asserted.\n"}
        if role == "technique_critic":
            return {"summary": reason, "issues": [], "suggestions": []}
        if role == "technique_critique_assessor":
            return {"critic_supported": True, "reason": reason, "findings": [], "interpretation_reviews": []}
        raise ValueError(f"Unexpected scripted generation role: {role}")


def run_offline_generation(case, directory, mode, budget):
    model = GenerationContractModel(case, mode)
    with configure_offline_roles(roles, (), "unused", mode):
        roles.create_strands_model = lambda *_args: model
        runner = roles.TechniqueRoleRunner("ollama", "scripted-generation-contract")

        class RoleRunner:
            def structured(self, role, *args, **kwargs):
                model.role = role
                return runner.structured(role, *args, **kwargs)

        result = run_generation(case, directory, RoleRunner(), budget)
    if result["status"] == "generated_pending_human_adjudication":
        result["status"] = "offline_contract_passed"
    result.update(scripted_model_dispatches=model.calls, semantic_judgment="not_performed")
    (Path(directory) / "outcome.json").write_text(json.dumps(result, indent=2))
    return result
