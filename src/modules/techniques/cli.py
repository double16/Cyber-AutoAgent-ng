"""Find or build a global technique and print its complete Markdown to stdout."""

import argparse
import contextlib
import os
from pathlib import Path
from types import SimpleNamespace


def build_service(provider: str, model: str | None, output_dir: str, module: str, *, diagnostics=None):
    from modules.config import get_config_manager
    from modules.prompts.factory import get_module_loader
    from modules.storage.qdrant import build_embeddings, open_qdrant
    from modules.techniques.repository import TechniqueRepository
    from modules.techniques.research import WebResearch, default_search_tool
    from modules.techniques.roles import TechniqueRoleRunner
    from modules.techniques.service import TechniqueService
    from modules.techniques.storage import TechniqueStore

    manager = get_config_manager()
    config = manager.get_qdrant_memory_config(provider)
    model = manager.get_model_id(provider, model)
    qdrant = open_qdrant(output_dir)
    try:
        repository = TechniqueRepository(str(Path(output_dir) / "cyber_autoagent.db"))
        store = TechniqueStore(qdrant, build_embeddings(config), config, repository)
        runner = TechniqueRoleRunner(provider, model, diagnostics=diagnostics)
        backend = SimpleNamespace(
            qdrant=qdrant,
            embeddings=store.embeddings,
            embedding_dimensions=store.dimensions,
            config=config,
            qdrant_url=os.getenv("QDRANT_URL", ""),
        )
        service = TechniqueService(store, runner, WebResearch(runner, default_search_tool(backend)))
        if diagnostics is not None:
            service.events.callback = diagnostics.lifecycle
        service.synchronize_module(get_module_loader(), module)
    except BaseException:
        qdrant.close()
        raise
    return service


def main(argv: list[str] | None = None) -> int:
    from modules.techniques.context import LookupContext
    from modules.techniques.documents import CONTEXTS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--module", default="web")
    parser.add_argument("--provider", choices=["bedrock", "ollama", "litellm", "gemini"])
    parser.add_argument("--model")
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--phase", action="append", choices=CONTEXTS, default=[],
        help="Phase context for lookup and generation; repeat for multiple phases (default: unknown)",
    )
    parser.add_argument(
        "--constraint", action="append", default=[],
        help="Task restriction such as read-only or no live execution; repeat for multiple restrictions",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", action="store_true", help="Show model/tool timing and diagnostic identifiers")
    verbosity.add_argument("--quiet", action="store_true", help="Show only warnings and errors on stderr")
    parser.add_argument("--log-file", help="Override the automatic per-run JSONL log path; must not already exist")
    args = parser.parse_args(argv)
    constraints = list(dict.fromkeys(value.strip() for value in args.constraint))
    if "" in constraints:
        parser.error("--constraint must contain non-whitespace text")
    context = LookupContext(
        phase_contexts=list(dict.fromkeys(args.phase)), purpose=args.request, constraints=constraints,
    ) if args.phase or constraints else None
    from modules.techniques.diagnostics import DiagnosticSession

    diagnostics = DiagnosticSession(verbose=args.verbose, quiet=args.quiet)
    service = None
    try:
        with diagnostics.capture_warnings(), contextlib.redirect_stdout(diagnostics.output):
            from modules.config import get_config_manager

            manager = get_config_manager()
            provider = args.provider or manager.get_provider()
            model = manager.get_model_id(provider, args.model)
            output_dir = args.output_dir or str(manager.get_output_config(provider).base_dir)
            diagnostics.open(output_dir, args.log_file)
            diagnostics.emit("startup", message="Initializing technique service", provider=provider,
                             model_id=model, module=args.module, phase_contexts=args.phase)
            service = build_service(provider, model, output_dir, args.module, diagnostics=diagnostics)
            bundle = service.resolve_bundle(args.request, context=context)
            markdown = bundle.render()
            diagnostics.emit(
                "bundle_completed", level="warning" if bundle.unresolved else "info",
                status="partial" if bundle.unresolved else "completed",
                delivered_count=len(bundle.delivered), unresolved_count=len(bundle.unresolved),
            )
        print(markdown, end="" if markdown.endswith("\n") else "\n")
        return 0
    except Exception as error:
        diagnostics.failure("lookup_failed", error, level="error")
        return 1
    finally:
        if service is not None:
            try:
                with diagnostics.capture_warnings(), contextlib.redirect_stdout(diagnostics.output):
                    service.store.qdrant.close()
            except Exception as error:
                diagnostics.failure("cleanup_failed", error, level="warning")
        diagnostics.output.flush()
        diagnostics.close()


if __name__ == "__main__":
    raise SystemExit(main())
