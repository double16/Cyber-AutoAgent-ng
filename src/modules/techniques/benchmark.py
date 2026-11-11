"""Embedding-provider-independent evaluation of labeled Qdrant retrieval cases."""

from time import monotonic

from pydantic import BaseModel, ConfigDict, Field


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1)
    phases: list[str] = Field(min_length=1)
    relevant_lineages: list[str]
    observation: str = ""
    recommendation_lineages: list[str] = Field(default_factory=list)
    # Empty relevance labels intentionally test catalog misses.


def evaluate(store, cases: list[RetrievalCase], limits=(10, 20)) -> list[dict]:
    """Measure ranked retrieval, without claiming that similarity establishes applicability."""
    if not cases or any(limit < 1 for limit in limits):
        raise ValueError("Provide cases and positive retrieval limits")
    results = []
    for limit in limits:
        for case in cases:
            started = monotonic()
            rows = store.search(case.request, active=True, contexts=case.phases, limit=limit)
            elapsed = monotonic() - started
            ranked = [store.repository.get(row["revision_id"])["lineage"] for row in rows]
            relevant = set(case.relevant_lineages)
            hits = relevant.intersection(ranked)
            results.append({
                "request": case.request, "limit": limit, "ranked_lineages": ranked,
                "recall": len(hits) / len(relevant) if relevant else None,
                "precision": len(hits) / len(ranked) if ranked else 0.0,
                "reciprocal_rank": next(
                    (1 / rank for rank, lineage in enumerate(ranked, 1) if lineage in relevant), 0.0,
                ),
                "catalog_miss": not relevant,
                "elapsed_seconds": elapsed,
            })
    return results


def evaluate_workflow(service, cases: list[RetrievalCase], directory) -> list[dict]:
    """Run recommendation and actual lookup/generation, measuring decisions against explicit labels."""
    from pathlib import Path

    from modules.techniques.context import MethodologyContext
    from modules.techniques.triggers import RecommendationContext

    if not cases:
        raise ValueError("Provide labeled workflow cases")
    results = []
    for number, case in enumerate(cases):
        events = []
        previous = service.events.callback

        def capture(event):
            events.append(event)
            if previous is not None:
                previous(event)

        service.events.callback = capture
        started = monotonic()
        try:
            context = RecommendationContext(
                service, case.phases, [], Path(directory) / str(number) / "recommendations.json",
            )
            if case.observation:
                context.observe(case.observation, "benchmark:source", origin="source")
            # Use the actual public alignment, selection, confirmation and generation pipeline.
            revision, _ = service.get(case.request, MethodologyContext(phase_contexts=case.phases))
            selected = service.repository.get(revision)["lineage"]
            resolved = [event for event in events if event["type"] == "technique_resolved"]
            generated = any(event.get("outcome") == "generated" for event in resolved)
            recommended = {
                service.repository.get(row["revision_id"])["lineage"]
                for row in context.recommended() if row.get("revision_id")
            }
            expected = set(case.recommendation_lineages)
            results.append({
                "request": case.request, "selected_lineage": selected, "generated": generated,
                "selection_correct": selected in case.relevant_lineages if not generated else None,
                "unnecessary_generation": generated and bool(case.relevant_lineages),
                "recommendation_precision": len(recommended & expected) / len(recommended) if recommended else None,
                "recommendation_recall": len(recommended & expected) / len(expected) if expected else None,
                "false_recommendations": sorted(recommended - expected),
                "recommended_lineages": sorted(recommended),
                "model_calls": sum(event["type"] == "technique_model_call" for event in events),
                "elapsed_seconds": monotonic() - started,
            })
        finally:
            service.events.callback = previous
    return results


def compare(catalog, embeddings, config, cases, *, workflow_runner=None, research=None):
    """Compare representations in disposable Qdrant/SQLite stores, leaving operation state untouched."""
    from contextlib import closing
    from tempfile import TemporaryDirectory

    from qdrant_client import QdrantClient

    from modules.techniques.documents import TechniqueDocument
    from modules.techniques.repository import TechniqueRepository
    from modules.techniques.retrieval import RetrievalSettings
    from modules.techniques.service import TechniqueService
    from modules.techniques.storage import TechniqueStore

    labels = {lineage for lineage, _ in catalog}
    for case in cases:
        if not set([*case.relevant_lineages, *case.recommendation_lineages]).issubset(labels):
            raise ValueError("Benchmark labels reference techniques outside the catalog")
    output = []
    for representation in ("full", "summary"):
        for limit in (10, 20):
            with TemporaryDirectory() as directory, closing(QdrantClient(":memory:")) as client:
                store = TechniqueStore(
                    client, embeddings, config, TechniqueRepository(f"{directory}/benchmark.db"),
                    retrieval=RetrievalSettings(
                        representation=representation, document_limit=limit, trigger_limit=limit,
                    ),
                )
                for lineage, markdown in catalog:
                    store.publish(TechniqueDocument(markdown), lineage, "benchmark")
                row = {
                    "representation": representation, "limit": limit,
                    "catalog_size": len(catalog), "retrieval": evaluate(store, cases, limits=(limit,)),
                }
                if workflow_runner is not None:
                    row["workflow"] = []
                    for number, case in enumerate(cases):
                        with closing(QdrantClient(":memory:")) as workflow_client:
                            workflow_store = TechniqueStore(
                                workflow_client, embeddings, config,
                                TechniqueRepository(f"{directory}/case-{number}.db"),
                                retrieval=store.retrieval,
                            )
                            for lineage, markdown in catalog:
                                workflow_store.publish(TechniqueDocument(markdown), lineage, "benchmark")
                            service = TechniqueService(workflow_store, workflow_runner, research)
                            row["workflow"].extend(evaluate_workflow(
                                service, [case], f"{directory}/workflow-{number}",
                            ))
                output.append(row)
    return output


def main(argv=None):
    """Run against a disposable copy of module guidance using the configured embedding provider."""
    import argparse
    import hashlib
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    from dotenv import load_dotenv

    from modules.config import get_config_manager
    from modules.storage.qdrant import build_embeddings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path("src/modules/operation_plugins"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=["ollama", "bedrock", "litellm", "gemini"])
    parser.add_argument("--workflow", action="store_true", help="Also run LLM selection and research/generation")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output already exists; choose a new report path")
    load_dotenv()
    cases = [RetrievalCase.model_validate(row) for row in json.loads(args.cases.read_text())]
    catalog = []
    for module in sorted(args.catalog.iterdir()) if args.catalog.is_dir() else []:
        for path in sorted((module / "techniques").rglob("*.md")):
            catalog.append((f"module:{module.name}:{path.relative_to(module).as_posix()}", path.read_text()))
    if not catalog:
        parser.error("No module technique documents found")
    manager = get_config_manager()
    provider = args.provider or manager.get_provider()
    config = manager.get_qdrant_memory_config(provider)
    embeddings = build_embeddings(config)
    cache = {}
    requests = {case.request for case in cases}

    class CachedEmbeddings:
        def embed_query(self, text):
            if text in requests:
                return embeddings.embed_query(text)
            if text not in cache:
                cache[text] = embeddings.embed_query(text)
            return cache[text]

    runner, research = None, None
    if args.workflow:
        from modules.techniques.research import WebResearch, default_search_tool
        from modules.techniques.roles import TechniqueRoleRunner

        runner = TechniqueRoleRunner(provider, manager.get_model_id(provider, None))
        research = WebResearch(runner, default_search_tool())
    results = compare(catalog, CachedEmbeddings(), config, cases, workflow_runner=runner, research=research)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "catalog_sha256": hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest(),
        "cases_sha256": hashlib.sha256(
            json.dumps([case.model_dump() for case in cases], sort_keys=True).encode(),
        ).hexdigest(),
        "embedding_provider": config.get("embedding_provider"),
        "embedding_model": config.get("embedding_model"),
        "workflow": args.workflow, "query_embeddings_cached": False, "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    from modules.utils.redaction import redact

    with args.output.open("x") as stream:
        json.dump(redact(report), stream, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
