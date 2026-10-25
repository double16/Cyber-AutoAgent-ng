"""Recover bounded, source-backed HTTP request descriptions without executing JavaScript."""

import json
import re
from bisect import bisect_right
from collections import defaultdict
from urllib.parse import parse_qsl, urljoin, urlsplit

import tree_sitter_javascript
from tree_sitter import Language, Parser

from modules.tools.client_route_extraction import extract_route_links
from modules.tools.inventory_provenance import (
    OPERATION_PROVENANCE_FIELD,
    operation_provenance,
    set_operation_provenance,
)

_MAX_BYTES = 16_000_000
_MAX_NODES = 500_000
_MAX_REQUESTS = 2_000
_UNKNOWN = "__CLIENT_DYNAMIC__"
_FUNCTIONS = {"function_declaration", "function_expression", "arrow_function", "generator_function"}


def extract_client_requests(text: str) -> dict:
    """Return concrete operations and focused unresolved source locations from one saved bundle."""

    source = text.encode("utf-8")
    if len(source) > _MAX_BYTES:
        return {"operations": [], "unresolved_requests": [], "request_analysis_status": "size_limit"}
    language = Language(tree_sitter_javascript.language())
    parser = Parser(language)
    tree = parser.parse(source)
    root = tree.root_node
    nodes = []
    stack = [root]
    while stack and len(nodes) < _MAX_NODES:
        node = stack.pop()
        nodes.append(node)
        stack.extend(reversed(node.named_children))
    if stack:
        return {"operations": [], "unresolved_requests": [], "request_analysis_status": "node_limit"}

    def raw(node):
        return source[node.start_byte : node.end_byte].decode("utf-8") if node else ""

    def unwrap(node):
        while node and node.type == "parenthesized_expression":
            node = node.named_children[0]
        return node

    def scope(node):
        parent = node.parent
        while parent and parent.type not in _FUNCTIONS | {"program", "statement_block"}:
            parent = parent.parent
        return parent or root

    def outer_scopes(node):
        current = scope(node)
        while True:
            yield current.start_byte, current.end_byte
            if current == root:
                break
            current = scope(current)

    bindings = defaultdict(list)
    constants = set()

    def bind(name, value, at):
        bindings[(*next(outer_scopes(at)), name)].append(value)

    def fields(node):
        if not node or node.type != "object":
            return {}
        result = {}
        for child in node.named_children:
            if child.type == "pair":
                key = child.child_by_field_name("key")
                if key.type in {"property_identifier", "string", "number"}:
                    result[raw(key).strip("\"'")] = child.child_by_field_name("value")
            elif child.type == "shorthand_property_identifier":
                result[raw(child)] = child
        return result

    def complete_object(node):
        return bool(
            node
            and node.type == "object"
            and not any(
                child.type in {"spread_element", "method_definition"}
                or (child.type == "pair" and child.child_by_field_name("key").type == "computed_property_name")
                for child in node.named_children
            )
        )

    for node in nodes:
        if node.type == "variable_declarator":
            name = raw(node.child_by_field_name("name"))
            bind(name, node.child_by_field_name("value"), node)
            if node.parent.children[0].type == "const":
                constants.add((*next(outer_scopes(node)), name))
        if node.type in _FUNCTIONS:
            if node.type == "function_declaration":
                bind(raw(node.child_by_field_name("name")), node, node)
            parameters = node.child_by_field_name("parameters")
            parameter = node.child_by_field_name("parameter")
            for child in parameters.named_children if parameters else ([parameter] if parameter else []):
                bindings[(node.start_byte, node.end_byte, raw(child))].append(None)
    for node in nodes:
        if node.type not in {"assignment_expression", "augmented_assignment_expression", "update_expression"}:
            continue
        left = node.named_children[0] if node.type == "update_expression" else node.child_by_field_name("left")
        name = raw(left)
        value = node.child_by_field_name("right") if node.type == "assignment_expression" else None
        destination = next(outer_scopes(node))
        for start, end in outer_scopes(node):
            if (start, end, name) in bindings:
                destination = (start, end)
                break
        bindings[(*destination, name)].append(value)

    # Transpiled enum IIFEs expose member assignments through their returned parameter.
    for node in nodes:
        if node.type != "variable_declarator":
            continue
        call = unwrap(node.child_by_field_name("value"))
        if not call or call.type != "call_expression":
            continue
        function = unwrap(call.child_by_field_name("function"))
        if not function or function.type not in _FUNCTIONS:
            continue
        params = function.child_by_field_name("parameters")
        parameter = function.child_by_field_name("parameter")
        names = params.named_children if params else ([parameter] if parameter else [])
        if len(names) != 1:
            continue
        parameter_name = raw(names[0])
        body = function.child_by_field_name("body")
        statements = body.named_children if body else []
        returns = [s for s in statements if s.type == "return_statement"]
        if len(returns) != 1 or not returns[0].named_children or raw(returns[0].named_children[0]) != parameter_name:
            continue
        for statement in statements:
            if statement.type != "expression_statement":
                continue
            assignment = statement.named_children[0]
            if assignment.type != "assignment_expression":
                continue
            member = raw(assignment.child_by_field_name("left"))
            if member.startswith(parameter_name + "."):
                bind(
                    raw(node.child_by_field_name("name")) + member[len(parameter_name) :],
                    assignment.child_by_field_name("right"),
                    node,
                )

    def lookup(node):
        for start, end in outer_scopes(node):
            key = (start, end, raw(node))
            if key in bindings:
                return key
        return None

    def root_binding(node, depth=0, seen=frozenset()):
        node = unwrap(node)
        if node is None or depth > 16 or node.id in seen:
            return None
        if node.type in {"member_expression", "subscript_expression"}:
            return root_binding(node.child_by_field_name("object"), depth + 1, seen | {node.id})
        if node.type not in {"identifier", "shorthand_property_identifier"}:
            return None
        key = lookup(node)
        values = bindings.get(key, [])
        value = unwrap(values[0]) if len(values) == 1 else None
        if value is not None and value.type == "identifier":
            return root_binding(value, depth + 1, seen | {node.id})
        return key

    def transport(name):
        return (
            name in {"fetch", "window.fetch", "globalThis.fetch", "axios", "request"}
            or name.endswith(".request")
            or name in {f"axios.{method}" for method in ("get", "head", "post", "put", "patch", "delete", "options")}
        )

    def direct_transport(function):
        return transport(raw(function)) and (function.type != "identifier" or lookup(function) is None)

    def function_key(function):
        declaration = function if function.type == "function_declaration" else function.parent
        if declaration.type not in {"function_declaration", "variable_declarator"}:
            return None
        return (*next(outer_scopes(declaration)), raw(declaration.child_by_field_name("name")))

    wrapper_candidates = set()
    for node in nodes:
        if node.type != "call_expression" or not direct_transport(node.child_by_field_name("function")):
            continue
        parent = node.parent
        while parent and parent.type not in _FUNCTIONS:
            parent = parent.parent
        if parent is not None:
            key = function_key(parent)
            if key is not None:
                wrapper_candidates.add(key)

    wrappers = set()

    def forwarded_value(value, aliases):
        value = unwrap(value)
        if value is None:
            return False
        if value.type == "identifier":
            return raw(value) in aliases
        if value.type != "ternary_expression":
            return False
        condition = raw(value.child_by_field_name("condition"))
        alternative = unwrap(value.child_by_field_name("alternative"))
        consequence = value.child_by_field_name("consequence")
        if alternative is None or raw(alternative) not in aliases or not complete_object(consequence):
            return False
        properties = fields(consequence)
        return (
            set(properties) == {"url"} and raw(properties["url"]) == raw(alternative)
            and re.fullmatch(r"typeof\s+" + re.escape(raw(alternative)) + r"\s*={2,3}\s*['\"]string['\"]",
                             condition) is not None
        )

    for node in nodes:
        if node.type not in _FUNCTIONS:
            continue
        parameters = node.child_by_field_name("parameters")
        parameter = node.child_by_field_name("parameter")
        names = parameters.named_children if parameters else ([parameter] if parameter else [])
        if len(names) != 1 or names[0].type != "identifier":
            continue
        body = node.child_by_field_name("body")
        call = unwrap(body)
        aliases = {raw(names[0])}
        if body is not None and body.type == "statement_block":
            statements = body.named_children
            if not statements or statements[-1].type != "return_statement":
                continue
            valid = True
            for statement in statements[:-1]:
                if statement.type != "lexical_declaration" or statement.children[0].type != "const":
                    valid = False
                    break
                for declaration in statement.named_children:
                    name = declaration.child_by_field_name("name")
                    if (name is None or name.type != "identifier" or raw(name) in aliases
                            or not forwarded_value(declaration.child_by_field_name("value"), aliases)):
                        valid = False
                        break
                    aliases.add(raw(name))
            if not valid:
                continue
            call = unwrap(statements[-1].named_children[0]) if statements[-1].named_children else None
        while call is not None and call.type == "call_expression":
            member = call.child_by_field_name("function")
            if member.type != "member_expression" or raw(member.child_by_field_name("property")) not in {
                "then", "catch", "finally",
            }:
                break
            callbacks = call.child_by_field_name("arguments").named_children
            # Only response callbacks independent of configuration are safe to ignore.
            pending = list(callbacks)
            valid = all(callback.type in _FUNCTIONS for callback in callbacks)
            while pending and valid:
                child = pending.pop()
                if child.type in {"identifier", "shorthand_property_identifier"} and raw(child) in aliases:
                    valid = False
                    break
                pending.extend(child.named_children)
            if not valid:
                call = None
                break
            call = unwrap(member.child_by_field_name("object"))
        if call is None or call.type != "call_expression":
            continue
        function = call.child_by_field_name("function")
        function_name = raw(function)
        arguments = call.child_by_field_name("arguments").named_children
        if (
            not direct_transport(function) or function_name in {"fetch", "window.fetch", "globalThis.fetch"}
            or len(arguments) != 1 or raw(arguments[0]) not in aliases
        ):
            continue
        key = function_key(node)
        if key is not None and len(bindings[key]) == 1:
            wrappers.add(key)

    invalidated = set()
    for node in nodes:
        if node.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"}:
            left = node.named_children[0] if node.type == "update_expression" else node.child_by_field_name("left")
            if left.type in {"member_expression", "subscript_expression"}:
                invalidated.add(root_binding(left))
        elif node.type == "unary_expression" and raw(node.child_by_field_name("operator")) == "delete":
            invalidated.add(root_binding(node.named_children[-1]))
        elif node.type == "call_expression":
            function = node.child_by_field_name("function")
            if direct_transport(function) or lookup(function) in wrappers:
                continue
            arguments = node.child_by_field_name("arguments")
            for argument in arguments.named_children if arguments else []:
                invalidated.add(root_binding(argument))
            if function.type == "member_expression":
                invalidated.add(root_binding(function.child_by_field_name("object")))
    invalidated.discard(None)
    # A write through a reassigned alias may affect any of its possible original objects.
    aliases = defaultdict(set)
    for key, values in bindings.items():
        for value in values:
            value = unwrap(value)
            if value is not None and value.type == "identifier":
                other = lookup(value)
                if other is not None:
                    aliases[key].add(other)
                    aliases[other].add(key)
    pending = list(invalidated)
    while pending:
        for alias in aliases[pending.pop()] - invalidated:
            invalidated.add(alias)
            pending.append(alias)

    def resolve_object(node, depth=0, seen=frozenset()):
        node = unwrap(node)
        if node is None or depth > 16 or node.id in seen:
            return None
        if complete_object(node):
            return node
        if node.type not in {"identifier", "shorthand_property_identifier"}:
            return None
        key = lookup(node)
        if key not in constants or len(bindings[key]) != 1 or root_binding(node) in invalidated:
            return None
        return resolve_object(bindings[key][0], depth + 1, seen | {node.id})

    def resolve(node, depth=0, seen=frozenset()):
        node = unwrap(node)
        if node is None or depth > 16 or node.id in seen:
            return None
        seen = seen | {node.id}
        if node.type == "string":
            value = raw(node)
            try:
                if value.startswith('"'):
                    return json.loads(value)
                literal = value[1:-1].replace("\\'", "'")
                return None if "\\" in literal else literal
            except ValueError:
                return None
        if node.type in {"identifier", "member_expression"}:
            if root_binding(node) in invalidated:
                return None
            name = raw(node)
            obj = node.child_by_field_name("object") if node.type == "member_expression" else None
            for start, end in outer_scopes(node):
                base_values = bindings.get((start, end, raw(obj))) if obj else None
                if base_values and len(base_values) != 1:
                    return None
                values = bindings.get((start, end, name))
                if values:
                    return resolve(values[0], depth + 1, seen) if len(values) == 1 else None
                if base_values:
                    base = unwrap(base_values[0])
                    if not complete_object(base):
                        return None
                    target = fields(base).get(raw(node.child_by_field_name("property")))
                    return resolve(target, depth + 1, seen)
            return None
        if node.type == "binary_expression" and raw(node.child_by_field_name("operator")) == "+":
            left = resolve(node.child_by_field_name("left"), depth + 1, seen)
            right = resolve(node.child_by_field_name("right"), depth + 1, seen)
            if left is None and right is None:
                return None
            return (left if left is not None else _UNKNOWN) + (right if right is not None else _UNKNOWN)
        if node.type == "template_string":
            parts = []
            for child in node.named_children:
                if child.type == "string_fragment":
                    parts.append(raw(child))
                elif child.type == "template_substitution":
                    part = resolve(child.named_children[0], depth + 1, seen)
                    parts.append(part if part is not None else _UNKNOWN)
                else:
                    return None
            return "".join(parts)
        return None

    line_starts = [0, *(index + 1 for index, byte in enumerate(source) if byte == 10)]
    operations = []
    unresolved = []
    candidate_count = 0
    for node in nodes:
        if node.type != "call_expression":
            continue
        arguments = node.child_by_field_name("arguments")
        args = arguments.named_children if arguments else []
        function = node.child_by_field_name("function")
        function_name = raw(function)
        is_fetch = function_name in {"fetch", "window.fetch", "globalThis.fetch"}
        config_argument = args[1] if is_fetch and len(args) > 1 else (None if is_fetch or not args else args[0])
        config_node = resolve_object(config_argument)
        config = fields(config_node)
        config_complete = complete_object(config_node)
        url_node = args[0] if is_fetch and args else config.get("url")
        # A URL plus request-specific configuration is a candidate, not proof of execution.
        key = lookup(function)
        known_transport = direct_transport(function) or key in wrappers
        if (
            not known_transport and key not in wrapper_candidates
            and (url_node is None or not ({"method", "headers", "data"} & config.keys()))
        ):
            continue
        candidate_count += 1
        location = {
            "start_line": bisect_right(line_starts, node.start_byte),
            "end_line": bisect_right(line_starts, node.end_byte),
            "start_byte": node.start_byte,
            "end_byte": node.end_byte,
        }
        unresolved_wrapper = None
        if not known_transport:
            unresolved_wrapper = {"source_location": location, "reason": "unrecognized_request_wrapper",
                                  "callee": function_name}
            definitions = bindings.get(key, [])
            if len(definitions) == 1 and definitions[0] is not None and definitions[0].type in _FUNCTIONS:
                definition = definitions[0]
                unresolved_wrapper["wrapper_location"] = {
                    "start_line": bisect_right(line_starts, definition.start_byte),
                    "end_line": bisect_right(line_starts, definition.end_byte),
                    "start_byte": definition.start_byte, "end_byte": definition.end_byte,
                }
            unresolved.append(unresolved_wrapper)
        value = resolve(url_node) if is_fetch or config_complete else None
        method = (
            resolve(config.get("method"))
            if config_complete and "method" in config
            else ("GET" if is_fetch and (len(args) == 1 or config_complete) and "method" not in config else None)
        )
        try:
            parsed = urlsplit(value or "")
        except ValueError:
            value = None
            parsed = urlsplit("")
        if (
            not value
            or _UNKNOWN in parsed.path
            or _UNKNOWN in parsed.netloc
            or not (value.startswith("/") or parsed.scheme in {"http", "https"})
            or (method is not None and not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", method))
        ):
            if known_transport:
                unresolved.append({"source_location": location, "reason": "unresolved_url_or_method"})
        else:
            if method is None and known_transport:
                unresolved.append({"source_location": location, "reason": "unresolved_method"})
            inputs = [
                {"name": name, "location": "query"}
                for name, _ in parse_qsl(parsed.query, keep_blank_values=True)
                if _UNKNOWN not in name
            ]
            headers = config.get("headers")
            inputs.extend({"name": name, "location": "header"}
                          for name in fields(resolve_object(headers) or headers))
            body = config.get("data") or config.get("body")
            if body:
                body_fields = fields(resolve_object(body) or body)
                inputs.extend({"name": name, "location": "body"} for name in body_fields)
                if not body_fields:
                    inputs.append({"name": "body", "location": "body", "type": "opaque"})
            operation = {
                    "url": value.split("?", 1)[0].split("#", 1)[0],
                    "method": method.upper() if method else None,
                    "inputs": inputs,
                    "source_location": location,
                    "evidence_kind": "client_source",
                }
            if known_transport:
                operations.append(operation)
            else:
                operation["evidence_kind"] = "unverified_caller_declaration"
                unresolved_wrapper["candidate"] = operation
        if candidate_count >= _MAX_REQUESTS:
            break
    status = "request_limit" if candidate_count >= _MAX_REQUESTS else ("parse_errors" if root.has_error else "complete")
    return {"operations": operations, "unresolved_requests": unresolved, "request_analysis_status": status,
            **extract_route_links(source, nodes, resolve, lookup, bindings, operations, unresolved),
            "route_analysis_status": "parse_errors" if root.has_error else "complete"}


def attach_request_operations(
    manifest: dict, payload: dict, *, target: str | None = None, target_id: str | None = None
) -> None:
    """Merge source operations into matching endpoints without claiming live execution."""

    from modules.tools.recon_inventory_manifest import _canonical_url, _in_scope, _stable_id

    source_target = payload.get("target", "")
    target = target or source_target
    target_id = target_id or payload.get("target_id", "target-1")
    if not _in_scope(source_target, target):
        return
    for operation in payload.get("operations", []):
        url = _canonical_url(urljoin(source_target, operation["url"]))
        if not url or not _in_scope(url, target):
            continue
        endpoint = next(
            (
                item
                for item in manifest["items"]
                if item["kind"] == "endpoint" and item["value"].split("?", 1)[0] == url
            ),
            None,
        )
        if endpoint is None:
            endpoint = {
                "id": _stable_id("endpoint", target_id, url),
                "kind": "endpoint",
                "target_id": target_id,
                "value": url,
                "attributes": {},
            }
            manifest["items"].append(endpoint)
        interaction = endpoint["attributes"].setdefault(
            "interaction",
            {
                "interface": "http",
                "operations": [],
                "operation_provenance": "inferred",
                "inputs": [],
                "success_signals": [],
                "failure_signals": [],
                "evidence_refs": [],
            },
        )
        reference = payload.get("analysis_artifact")
        if isinstance(reference, str) and reference.startswith("artifact:"):
            if reference not in interaction["evidence_refs"]:
                interaction["evidence_refs"].append(reference)
        extensions = interaction.setdefault("extensions", {})
        details = extensions.setdefault("client_source_operations", [])
        detail = {**operation, "source_artifact": payload.get("analysis_artifact")}
        if detail not in details:
            details.append(detail)
        # Source-declared methods are candidates until network observation validates them.
        methods = [entry["method"] for entry in details if entry.get("method")]
        _attach_source_methods(interaction, methods)
        for value in operation["inputs"]:
            if value not in interaction["inputs"]:
                interaction["inputs"].append(value)
            if value.get("type") == "opaque":
                continue
            suffix = value["name"] if value["location"] == "query" else f"{value['location']}:{value['name']}"
            parameter_id = _stable_id("parameter", target_id, f"{endpoint['id']}:{suffix}")
            parameter = next((item for item in manifest["items"] if item["id"] == parameter_id), None)
            if parameter is None:
                parameter = {
                    "id": parameter_id,
                    "kind": "parameter",
                    "target_id": target_id,
                    "value": value["name"],
                    "attributes": {
                        "endpoint_id": endpoint["id"],
                        "interaction": {
                            "interface": "http",
                            "operations": [],
                            "operation_provenance": "inferred",
                            "inputs": [value],
                            "success_signals": [],
                            "failure_signals": [],
                            "evidence_refs": [],
                        },
                    },
                }
                manifest["items"].append(parameter)
            parameter_interaction = parameter["attributes"]["interaction"]
            if (
                isinstance(reference, str)
                and reference.startswith("artifact:")
                and reference not in parameter_interaction["evidence_refs"]
            ):
                parameter_interaction["evidence_refs"].append(reference)
            parameter_details = parameter_interaction.setdefault("extensions", {}).setdefault(
                "client_source_operations", []
            )
            if detail not in parameter_details:
                parameter_details.append(detail)
            _attach_source_methods(parameter_interaction, [
                entry["method"] for entry in parameter_details if entry.get("method")
            ])
    if "extraction" in manifest:
        manifest["extraction"]["added_count"] = len(manifest["items"])
    for entry in payload.get("unresolved_requests", []):
        gap = {
            **entry,
            "reason": "unresolved_client_request",
            "detail": entry.get("reason"),
            "source_artifact": payload.get("analysis_artifact"),
        }
        if gap not in manifest.setdefault("unassessed_gaps", []):
            manifest["unassessed_gaps"].append(gap)
    status = payload.get("request_analysis_status", "complete")
    if status != "complete":
        gap = {
            "reason": "incomplete_client_request_analysis",
            "status": status,
            "source_artifact": payload.get("analysis_artifact"),
        }
        if gap not in manifest.setdefault("unassessed_gaps", []):
            manifest["unassessed_gaps"].append(gap)


def _attach_source_methods(interaction: dict, methods: list[str]) -> None:
    """Replace legacy guessed methods, preserving explicit and observed method evidence."""

    provenance = {
        method: status for method, status in operation_provenance(interaction).items()
        if status != "inferred" or OPERATION_PROVENANCE_FIELD in interaction
    }
    for method in methods:
        if provenance.get(method, "unknown") == "unknown":
            provenance[method] = "inferred"
    set_operation_provenance(interaction, provenance)
