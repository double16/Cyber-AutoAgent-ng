"""Explicit operation-support judgments used by comparison evaluator fixtures."""


def operation_support(outcome, evidence_refs):
    return {
        "status": "established" if outcome == "supported" else "unestablished",
        "basis": "observed_intended_behavior" if outcome == "supported" else "unestablished",
        "evidence_refs": evidence_refs,
    }
