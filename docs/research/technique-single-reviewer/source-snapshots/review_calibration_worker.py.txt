"""One isolated arm execution; invoked by the coordinator using the selected arm's src tree."""

import json
import os
import runpy
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace


def run_job(job, *, runner_factory=None, transport=None):
    # These imports intentionally resolve from the arm-specific PYTHONPATH in a fresh process.
    from modules.techniques import roles
    from modules.techniques.coverage import Requirement
    from modules.techniques.critique import ReviewSnapshot, assess

    mode = job.get("review_mode", "legacy")
    if mode not in {"legacy", "single"}:
        raise ValueError("Unknown review_mode")
    if mode == "single":
        from modules.techniques.publication_review import assess
    from modules.techniques.diagnostics import DiagnosticSession
    from modules.techniques.documents import TechniqueDocument
    from modules.techniques.roles import TechniqueRoleRunner
    from modules.techniques.service import TechniqueService

    helpers = runpy.run_path(job["budget_module"])
    budget = helpers["CalibrationBudget"](job["budget"])
    root = Path(job["root"])
    case = job["case"]
    snapshot = ReviewSnapshot(case["draft"])
    required = [json.dumps({"reference": key, "path": snapshot.paths[key], "value": value}, ensure_ascii=False)
                for key, value in snapshot.values.items() if snapshot.paths[key] != "metadata.source_coverage"]

    def verify_request(request):
        texts = [message.get("content", "") for message in request.get("messages", [])
                 if message.get("role") == "user" and isinstance(message.get("content"), str)]
        if any(not any(row in text for text in texts) for row in required):
            raise RuntimeError("Provider request lost authored document content; semantic review is invalid")

    with ExitStack() as stack:
        offline_model = None
        if job.get("offline"):
            offline = runpy.run_path(job["offline_module"])
            reference = next(key for key in snapshot.body_references)
            offline_model = stack.enter_context(offline["configure_offline_roles"](
                roles, required, reference, job.get("wire_mode", "native"),
            ))
        if transport is None:
            import ollama

            transport = ollama.AsyncClient
        session = DiagnosticSession(quiet=True, verbose=True)
        session.open(str(root))
        make_runner = runner_factory or TechniqueRoleRunner
        runner = make_runner("ollama", job["model"], diagnostics=session)

        class BoundedRunner:
            def structured(self, *args, **kwargs):
                budget.reserve("roles")
                return runner.structured(*args, **kwargs)

        service = TechniqueService(SimpleNamespace(repository=None), BoundedRunner(), None)
        service.events.callback = session.lifecycle
        try:
            with budget.instrument(transport, "chat", root, validate_request=verify_request):
                result = assess(service, case["request"], case["draft"],
                                [Requirement(**row) for row in case["requirements"]], case["sources"],
                                [row.url for row in TechniqueDocument(case["draft"]).metadata.sources])
            outcome = {"status": "offline_contract_passed" if offline_model is not None else "completed",
                       "review": result.model_dump()}
            if offline_model is not None:
                outcome["scripted_model_dispatches"] = offline_model.calls
                outcome["semantic_judgment"] = "not_performed"
        except Exception as error:
            outcome = {"status": "unresolved", "error_type": type(error).__name__, "error": str(error)}
        finally:
            session.close()
        outcome["counts"] = budget.counts()
        (root / "outcome.json").write_text(json.dumps(outcome, indent=2))
        return outcome


def main(argv=None):
    path = Path((sys.argv[1:] if argv is None else argv)[0])
    # Honor injected provider metadata without reading or modifying an operation's configuration.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    run_job(json.loads(path.read_text()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
