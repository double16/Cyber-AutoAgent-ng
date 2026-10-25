"""Bounded syntactic route/component/call relationships; no JavaScript is executed."""

import hashlib
import json
from bisect import bisect_right
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

ROUTE_EXTRACTOR_VERSION = 2
_FUNCTIONS = {"function_declaration", "function_expression", "arrow_function", "generator_function"}


def saved_route_index(content: bytes, cache_directory: Path) -> dict:
    """Derive old mapping context on demand, keyed by source bytes and extractor version."""
    from modules.tools.client_request_extraction import extract_client_requests

    digest = hashlib.sha256(content).hexdigest()
    path = cache_directory / f"{digest}-v{ROUTE_EXTRACTOR_VERSION}.json"
    try:
        cached = json.loads(path.read_text())
        if cached["source_sha256"] == digest and cached["extractor_version"] == ROUTE_EXTRACTOR_VERSION:
            return cached
    except (OSError, ValueError, KeyError):
        pass
    extracted = extract_client_requests(content.decode("utf-8", errors="replace"))
    result = {"source_sha256": digest, "extractor_version": ROUTE_EXTRACTOR_VERSION,
              "route_links": extracted.get("route_links", []),
              "literal_links": extracted.get("literal_links", []),
              "route_analysis_status": extracted.get("route_analysis_status", extracted["request_analysis_status"])}
    cache_directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(result, sort_keys=True))
    temporary.replace(path)
    return result


def extract_route_links(
    source: bytes, nodes: list, resolve: Callable, lookup: Callable, bindings: dict, operations: list,
    unresolved_requests: list,
) -> dict[str, Any]:
    """Reuse request extraction's lexical bindings rather than guessing from component names."""
    starts = [0, *(index + 1 for index, byte in enumerate(source) if byte == 10)]

    def text(node):
        return source[node.start_byte:node.end_byte].decode() if node else ""

    def location(node):
        return {"start_line": bisect_right(starts, node.start_byte), "end_line": bisect_right(starts, node.end_byte),
                "start_byte": node.start_byte, "end_byte": node.end_byte}

    def children(node):
        stack = [node]
        while stack:
            current = stack.pop()
            yield current
            stack.extend(reversed(current.named_children))

    def properties(node):
        if node.type in {"jsx_opening_element", "jsx_self_closing_element"}:
            return {text(child.named_children[0]): child.named_children[-1]
                    for child in node.named_children if child.type == "jsx_attribute" and len(child.named_children) > 1}
        if node.type == "call_expression":
            args = node.child_by_field_name("arguments")
            args = args.named_children if args else []
            if len(args) > 1 and args[1].type == "object":
                return {text(child.child_by_field_name("key")).strip("\"'"): child.child_by_field_name("value")
                        for child in args[1].named_children if child.type == "pair"}
        return {}

    def unwrap(node):
        while node and node.type in {"jsx_expression", "parenthesized_expression"}:
            node = node.named_children[0] if node.named_children else None
        return node

    def definition(node, seen=frozenset()):
        if node is None or node.id in seen or len(seen) > 16:
            return None
        values = bindings.get(lookup(node), [])
        if len(values) != 1 or values[0] is None:
            return None
        value = unwrap(values[0])
        if value.type in _FUNCTIONS:
            return value
        if value.type == "identifier":
            return definition(value, seen | {node.id})
        return None

    literal_links = []
    for node in nodes:
        if node.type == "pair":
            key, value = node.child_by_field_name("key"), node.child_by_field_name("value")
        elif node.type == "jsx_attribute" and len(node.named_children) > 1:
            key, value = node.named_children[0], node.named_children[-1]
        else:
            continue
        if text(key).strip("\"'") not in {"url", "href"} or value is None or value.type != "string":
            continue
        url = resolve(value)
        if url and url.startswith(("/", "http://", "https://")):
            literal_links.append({"url": url, "source_location": location(value)})
    links = []
    for node in nodes:
        props = properties(node)
        if not {"path", "element"} <= props.keys():
            continue
        path = resolve(unwrap(props["path"]))
        if not path or not path.startswith("/") or "__CLIENT_DYNAMIC__" in path:
            continue
        element = props["element"]
        components = []
        for candidate in children(element):
            if candidate.type in {"jsx_opening_element", "jsx_self_closing_element"}:
                components.append(candidate.child_by_field_name("name"))
            elif candidate.type == "call_expression":
                args = candidate.child_by_field_name("arguments")
                if args and args.named_children:
                    components.append(args.named_children[0])
        pending = [(component, 0, ()) for component in components if component is not None
                   and component.type in {"identifier", "member_expression"}]
        definitions, unresolved, reached = [], [], set()
        while pending:
            symbol, depth, ancestry = pending.pop(0)
            function = definition(symbol)
            if function is None:
                unresolved.append({"reason": "unresolved_definition", "symbol": text(symbol),
                                   "source_location": location(symbol)})
                continue
            if function.id in ancestry:
                unresolved.append({"reason": "cycle", "source_location": location(symbol)})
                continue
            if function.id in reached:
                continue
            if depth > 4 or len(reached) >= 32:
                unresolved.append({"reason": "traversal_limit", "source_location": location(symbol)})
                continue
            reached.add(function.id)
            call_ranges = set()
            stack = list(function.named_children)
            while stack:
                current = stack.pop()
                if current.type in _FUNCTIONS and current.parent.type != "arguments":
                    continue  # A nested declaration is not a reachable call merely because it exists.
                if current.type == "call_expression":
                    call_ranges.add((current.start_byte, current.end_byte))
                    callee = current.child_by_field_name("function")
                    if callee and callee.type in {"identifier", "member_expression"}:
                        pending.append((callee, depth + 1, (*ancestry, function.id)))
                stack.extend(current.named_children)
            declarations = [operation for operation in operations
                            if (operation["source_location"]["start_byte"], operation["source_location"]["end_byte"])
                            in call_ranges]
            definitions.append({"symbol": text(symbol), "source_location": location(function),
                                "depth": depth, "request_declarations": declarations,
                                "unresolved_request_declarations": [request for request in unresolved_requests
                                    if (request["source_location"]["start_byte"], request["source_location"]["end_byte"])
                                    in call_ranges]})
        links.append({"path": path, "source_location": location(node),
                      "element_source_location": location(element), "definitions": definitions,
                      "unresolved_links": unresolved, "relationship": "client_route_calls",
                      "runtime_execution": "unestablished"})
    return {"route_links": links, "literal_links": literal_links, "route_analysis_status": "complete"}
