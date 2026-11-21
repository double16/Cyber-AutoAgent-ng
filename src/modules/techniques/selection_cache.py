"""Task-scoped complete-document judgments, never lifecycle or delivery authority."""

import hashlib
import json

from modules.techniques.selection_review import SelectionReview
from modules.techniques.versioning import TECHNIQUE_GENERATION_VERSION


def cache_key(service, fingerprint):
    scope = service.events.current_context()
    if not scope.get("operation_id") or not scope.get("task_uid"):
        return None
    return hashlib.sha256(json.dumps([
        TECHNIQUE_GENERATION_VERSION, scope["operation_id"], scope["task_uid"],
        fingerprint, service.module_technique_policy,
    ], sort_keys=True).encode()).hexdigest()


def cached_review(service, key):
    if key is None:
        return None
    if key in service._selection_reviews:
        service.events.emit("technique_retrieval", stage="selection", status="cache_hit")
        return service._selection_reviews[key]
    if service.exports is not None:
        path = service.exports.directory / "selection_cache" / f"{key}.json"
        try:
            review = SelectionReview.model_validate_json(path.read_text())
        except (OSError, ValueError):
            return None
        service._selection_reviews[key] = review
        service.events.emit("technique_retrieval", stage="selection", status="cache_hit")
        return review
    return None


def save_review(service, key, review):
    if key is None:
        return
    service._selection_reviews[key] = review
    if service.exports is not None:
        path = service.exports.directory / "selection_cache" / f"{key}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(review.model_dump_json())
            temporary.replace(path)
        except OSError:
            service.events.emit("technique_warning", stage="selection_cache",
                                diagnostic="Selection cache persistence unavailable", retry_pending=False)
