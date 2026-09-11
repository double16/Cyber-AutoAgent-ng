import asyncio
import hashlib
import json
import random
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from ddgs import DDGS
from ddgs.exceptions import RatelimitException, TimeoutException
from pydantic import BaseModel, Field
from qdrant_client.http import models as qdrant_models
from strands import tool

from modules.config.system.logger import get_logger
from modules.tools import memory as memory_tools

logger = get_logger("Tools.WebSearch")

SENSITIVE_QUERY_BLOCKED_MESSAGE = "Web search query blocked by sensitive-data policy."
WEB_SEARCH_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
WEB_SEARCH_CACHE_SIMILARITY_THRESHOLD = 0.92
WEB_SEARCH_CACHE_COLLECTION_PREFIX = "cyber_autoagent_web_search_cache"
WEB_SEARCH_CACHE_CLEANUP_INTERVAL_SECONDS = 60 * 60
_WEB_SEARCH_CACHE_NAMESPACE = uuid.UUID("d4a6c017-e41a-42d3-8c9b-7d458563eff4")
_SENSITIVE_QUERY_PATTERNS = (
    re.compile(r"(?i)-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:authorization\s*[:=]\s*(?:bearer|basic)\s+|bearer\s+)[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token|access[_-]?key)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)https?://[^\s/@:]+:[^\s/@]+@"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)
_CARD_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


class SensitiveWebSearchQueryError(ValueError):
    """Raised when a web-search query would disclose sensitive data to a provider."""


def _passes_luhn(value: str) -> bool:
    """Return whether a digit-only payment-card candidate passes the Luhn checksum."""

    total = 0
    for index, digit in enumerate(reversed(value)):
        number = int(digit)
        if index % 2:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def contains_sensitive_web_search_data(query: str) -> bool:
    """Detect high-confidence secret, payment, and PII values in an outbound search query."""

    if any(pattern.search(query) for pattern in _SENSITIVE_QUERY_PATTERNS):
        return True
    return any(
        _passes_luhn(re.sub(r"[ -]", "", candidate.group()))
        for candidate in _CARD_CANDIDATE_PATTERN.finditer(query)
    )


class WebSearchHit(BaseModel):
    title: str = Field(description="Result title")
    url: str = Field(description="Result url")
    snippet: str = Field(description="Result snippet")


class SemanticWebSearchCache:
    """Reuse sufficiently similar successful web-search responses from Qdrant."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_cleanup: dict[str, float] = {}

    @staticmethod
    def _result_count(result: Any) -> int:
        if isinstance(result, list):
            return len(result)
        if isinstance(result, dict) and isinstance(result.get("results"), list):
            return len(result["results"])
        return 0

    @staticmethod
    def _limit_result(result: Any, limit: int) -> Any:
        if isinstance(result, list):
            return result[:limit]
        if isinstance(result, dict) and isinstance(result.get("results"), list):
            limited_result = dict(result)
            limited_result["results"] = result["results"][:limit]
            return limited_result
        return result

    @staticmethod
    def _json_copy(value: Any) -> Any | None:
        try:
            return json.loads(json.dumps(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _model_namespace(memory_client: Any) -> str:
        config = memory_client.config
        model = str(config.get("embedding_model") or "").strip()
        provider = str(config.get("embedding_provider") or "").strip()
        dimensions = int(memory_client.embedding_dimensions)
        identity = f"{provider}:{model}:{dimensions}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return f"{WEB_SEARCH_CACHE_COLLECTION_PREFIX}_{dimensions}_{digest}"

    @staticmethod
    def _point_id(query: str) -> str:
        return str(uuid.uuid5(_WEB_SEARCH_CACHE_NAMESPACE, query))

    @staticmethod
    def _memory_client() -> Any | None:
        """Return the already-initialized operation memory client without auto-initializing it."""

        return memory_tools._MEMORY_CLIENT

    def _ensure_collection(self, memory_client: Any, collection_name: str) -> None:
        qdrant = memory_client.qdrant
        if not qdrant.collection_exists(collection_name):
            qdrant.create_collection(
                collection_name=collection_name,
                vectors_config=qdrant_models.VectorParams(
                    size=memory_client.embedding_dimensions,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
        if memory_client.qdrant_url:
            try:
                qdrant.create_payload_index(
                    collection_name=collection_name,
                    field_name="expires_at",
                    field_schema=qdrant_models.PayloadSchemaType.FLOAT,
                    wait=True,
                )
            except Exception:
                logger.debug("Web-search cache expiry index was not created", exc_info=True)

    def _purge_expired(self, memory_client: Any, collection_name: str, now: float) -> None:
        with self._lock:
            last_cleanup = self._last_cleanup.get(collection_name, 0.0)
            if now - last_cleanup < WEB_SEARCH_CACHE_CLEANUP_INTERVAL_SECONDS:
                return
            memory_client.qdrant.delete(
                collection_name=collection_name,
                points_selector=qdrant_models.FilterSelector(
                    filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="expires_at",
                                range=qdrant_models.Range(lt=now),
                            )
                        ]
                    )
                ),
                wait=True,
            )
            self._last_cleanup[collection_name] = now

    def lookup(self, query: str, limit: int) -> Any | None:
        """Return an eligible semantic hit, or ``None`` when a live search is required."""

        memory_client = self._memory_client()
        if memory_client is None:
            logger.debug("Web-search cache unavailable because memory is not initialized")
            return None
        collection_name = self._model_namespace(memory_client)
        now = time.time()
        self._ensure_collection(memory_client, collection_name)
        self._purge_expired(memory_client, collection_name, now)
        vector = memory_client.embeddings.embed_query(query)
        result = memory_client.qdrant.query_points(
            collection_name=collection_name,
            query=vector,
            query_filter=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="expires_at",
                        range=qdrant_models.Range(gt=now),
                    )
                ]
            ),
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        if not result.points:
            logger.info("Web-search cache miss query_digest=%s", self._point_id(query)[:12])
            return None
        point = result.points[0]
        score = float(getattr(point, "score", 0.0) or 0.0)
        payload = dict(point.payload or {})
        result_count = int(payload.get("result_count", 0) or 0)
        if score < WEB_SEARCH_CACHE_SIMILARITY_THRESHOLD:
            logger.info(
                "Web-search cache similarity rejection query_digest=%s score=%.4f threshold=%.2f",
                self._point_id(query)[:12],
                score,
                WEB_SEARCH_CACHE_SIMILARITY_THRESHOLD,
            )
            return None
        if result_count < limit:
            logger.info(
                "Web-search cache result-count rejection query_digest=%s score=%.4f cached=%d requested=%d",
                self._point_id(query)[:12],
                score,
                result_count,
                limit,
            )
            return None
        cached_result = self._json_copy(payload.get("result"))
        if cached_result is None:
            logger.info("Web-search cache invalid payload query_digest=%s", self._point_id(query)[:12])
            return None
        logger.info(
            "Web-search cache hit query_digest=%s score=%.4f result_count=%d",
            self._point_id(query)[:12],
            score,
            result_count,
        )
        return self._limit_result(cached_result, limit)

    def store(self, query: str, result: Any) -> None:
        """Store a JSON-safe successful result for cross-operation semantic retrieval."""

        response = self._json_copy(result)
        result_count = self._result_count(response)
        if response is None or result_count <= 0:
            logger.info("Web-search cache write skipped query_digest=%s", self._point_id(query)[:12])
            return
        memory_client = self._memory_client()
        if memory_client is None:
            return
        collection_name = self._model_namespace(memory_client)
        now = time.time()
        self._ensure_collection(memory_client, collection_name)
        vector = memory_client.embeddings.embed_query(query)
        memory_client.qdrant.upsert(
            collection_name=collection_name,
            points=[
                qdrant_models.PointStruct(
                    id=self._point_id(query),
                    vector=vector,
                    payload={
                        "query": query,
                        "result": response,
                        "result_count": result_count,
                        "created_at": now,
                        "expires_at": now + WEB_SEARCH_CACHE_TTL_SECONDS,
                    },
                )
            ],
            wait=True,
        )
        logger.info(
            "Web-search cache write query_digest=%s result_count=%d",
            self._point_id(query)[:12],
            result_count,
        )


_semantic_web_search_cache = SemanticWebSearchCache()


# 9.10.0 has backend: brave, duckduckgo, google, grokipedia, mojeek, wikipedia, yahoo, yandex
def search_duckduckgo(query: str, num: int) -> list[WebSearchHit]:
    with DDGS() as ddg:
        results = ddg.text(query, backend="brave,duckduckgo", max_results=num)
        return [WebSearchHit(title=r["title"], url=r["href"], snippet=r["body"])
                for r in results]


def with_backoff(fn: Callable[..., list[WebSearchHit]],
                 retries: int = 4,
                 base: float = 1.5,
                 jitter: float = 0.3):
    """Decorate *fn* so it retries with exponential back-off on 429/5xx."""

    async def wrapper(*args, **kwargs):
        delay = base
        last_exc = None
        for attempt in range(retries + 1):
            try:
                async with asyncio.timeout(90):
                    hits = await asyncio.to_thread(fn, *args, **kwargs)
                if len(hits) == 0:
                    raise TimeoutException("no results")
                return hits
            except Exception as exc:
                last_exc = exc
                if attempt >= retries:
                    raise
                # Only retry obvious transient problems
                if isinstance(exc, (RatelimitException, TimeoutException)):
                    pass
                else:
                    msg = str(exc).lower()
                    if "429" not in msg and "rate" not in msg and "timeout" not in msg \
                            and "temporarily unavailable" not in msg:
                        raise
                sleep_for = delay * (1 + random.uniform(-jitter, jitter))
                await asyncio.sleep(max(0.1, sleep_for))
                delay *= base
        raise last_exc

    return wrapper


async def search_duckduckgo_with_backoff(query: str, limit: int) -> list[dict[str, str]]:
    """Search DDGS and normalize the provider response for the wrapper."""

    search_fn = with_backoff(search_duckduckgo)
    hits = await search_fn(query, limit)
    return [dict(hit) for hit in hits]


WebSearchProvider = Callable[[str, int], Awaitable[Any]]


def create_web_search_tool(
    search_provider: WebSearchProvider = search_duckduckgo_with_backoff,
    cache: SemanticWebSearchCache | None = None,
):
    """Create the single agent-facing web-search wrapper around a selected provider."""

    @tool
    async def web_search(
            query: str,
            limit: int = 20,
    ) -> Any:
        """
        Searches the web with the provided query.

        Invoke this tool when the user needs to find general information on vulnerabilities, CVEs, published exploits,
        and instructions for using tools.

        Sensitive credentials, payment-card data, and personally identifiable information are blocked before a search
        provider is called.

        Args:
            query:
            | Example | Result |
            | ------- | ------ |
            | cats dogs | Results about cats or dogs |
            | "cats and dogs" | Results for exact term "cats and dogs". If no or few results are found, we'll try to show related results. |
            | ~"cats and dogs" | Experimental syntax: more results that are semantically similar to "cats and dogs", like "cats & dogs" and "dogs and cats" in addition to "cats and dogs". |
            | cats -dogs | Fewer dogs in results |
            | cats +dogs | More dogs in results |
            | cats filetype:pdf | PDFs about cats. Supported file types: pdf, doc(x), xls(x), ppt(x), html |
            | dogs site:example.com | Pages about dogs from example.com |
            | cats -site:example.com | Pages about cats, excluding example.com |
            | intitle:dogs | Page title includes the word "dogs" |
            | inurl:cats | Page URL includes the word "cats" |

            limit: The maximum number of results to return, defaults to 20
        Return:
            Provider search results.
        """
        if contains_sensitive_web_search_data(query):
            raise SensitiveWebSearchQueryError(SENSITIVE_QUERY_BLOCKED_MESSAGE)

        effective_limit = max(1, min(50, limit))
        resolved_cache = cache or _semantic_web_search_cache
        try:
            cached_result = await asyncio.to_thread(resolved_cache.lookup, query, effective_limit)
            if cached_result is not None:
                return cached_result
        except Exception:
            logger.warning("Web-search cache lookup failed; using live provider", exc_info=True)

        result = await search_provider(query, effective_limit)
        try:
            await asyncio.to_thread(resolved_cache.store, query, result)
        except Exception:
            logger.warning("Web-search cache write failed; returning live provider result", exc_info=True)
        return result

    return web_search


web_search = create_web_search_tool()
