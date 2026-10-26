"""Find or build a global technique and print its complete Markdown to stdout."""

import argparse
import contextlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace


def build_service(provider: str, model: str | None, output_dir: str, module: str):
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
    model = model or manager.get_llm_config(provider).model_id
    qdrant = open_qdrant(output_dir)
    try:
        repository = TechniqueRepository(str(Path(output_dir) / "cyber_autoagent.db"))
        store = TechniqueStore(qdrant, build_embeddings(config), config, repository)
        runner = TechniqueRoleRunner(provider, model)
        backend = SimpleNamespace(
            qdrant=qdrant,
            embeddings=store.embeddings,
            embedding_dimensions=store.dimensions,
            config=config,
            qdrant_url=os.getenv("QDRANT_URL", ""),
        )
        service = TechniqueService(store, runner, WebResearch(runner, default_search_tool(backend)))
        service.synchronize_module(get_module_loader(), module)
    except BaseException:
        qdrant.close()
        raise
    return service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--module", default="web")
    parser.add_argument("--provider", choices=["bedrock", "ollama", "litellm", "gemini"])
    parser.add_argument("--model")
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)
    service = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from modules.config import get_config_manager

            manager = get_config_manager()
            provider = args.provider or manager.get_provider()
            output_dir = args.output_dir or str(manager.get_output_config(provider).base_dir)
            service = build_service(provider, args.model, output_dir, args.module)
            _, markdown = service.get(args.request)
        print(markdown, end="" if markdown.endswith("\n") else "\n")
        return 0
    except Exception as error:
        print(f"Technique lookup failed: {error}", file=sys.stderr)
        return 1
    finally:
        if service is not None:
            service.store.qdrant.close()


if __name__ == "__main__":
    raise SystemExit(main())
