"""Deterministic provenance extraction for Bash-compatible shell commands."""

from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Iterable

from tree_sitter import Language, Parser
import tree_sitter_bash


@dataclass(frozen=True)
class ShellExecutionProvenance:
    """Static command and literal output paths observed in one shell invocation."""

    executables: tuple[str, ...]
    output_paths: tuple[str, ...]
    parsed: bool


_PARSER = Parser(Language(tree_sitter_bash.language()))
_OUTPUT_OPTIONS = frozenset({"-o", "--output", "--output-file"})
_WRAPPER_COMMANDS = frozenset({"command", "env", "exec", "nice", "stdbuf", "sudo", "timeout"})


def shell_execution_provenance(command: str, initial_directory: str = "") -> ShellExecutionProvenance:
    """Parse a command without executing it and collect literal command/output values.

    Syntax errors deliberately produce no inferred provenance. Tree-sitter still
    exposes partial trees for invalid input, but acceptance receipts must never
    be built from partial parsing.
    """

    source = str(command or "")
    if not source.strip():
        return ShellExecutionProvenance((), (), False)
    tree = _PARSER.parse(source.encode())
    if tree.root_node.has_error:
        return ShellExecutionProvenance((), (), False)

    executables = []
    output_paths = []
    working_directory = Path(initial_directory) if initial_directory else None
    for node in _walk(tree.root_node):
        if node.type != "command":
            continue
        children = list(node.named_children)
        command_name = next((child for child in children if child.type == "command_name"), None)
        if command_name is None:
            continue
        executable = _node_text(source, command_name).strip()
        if executable:
            executables.append(PurePath(executable).name.lower())
        arguments = [child for child in children if child != command_name]
        executables.extend(
            _wrapped_executables(PurePath(executable).name.lower(), [_node_text(source, item) for item in arguments])
        )
        if PurePath(executable).name == "cd" and arguments:
            destination = _literal_path(_node_text(source, arguments[0]))
            if destination:
                candidate = Path(destination)
                working_directory = candidate if candidate.is_absolute() else (working_directory / candidate if working_directory else None)
            continue
        for output_path in _command_output_paths(source, arguments):
            candidate = Path(output_path)
            output_paths.append(str(candidate if candidate.is_absolute() else working_directory / candidate) if working_directory else output_path)

    for redirect in _walk(tree.root_node):
        if redirect.type != "file_redirect" or ">" not in _node_text(source, redirect):
            continue
        destination = redirect.child_by_field_name("destination")
        if destination is None:
            continue
        output_path = _literal_path(_node_text(source, destination))
        if not output_path:
            continue
        candidate = Path(output_path)
        output_paths.append(str(candidate if candidate.is_absolute() else working_directory / candidate) if working_directory else output_path)

    return ShellExecutionProvenance(
        tuple(dict.fromkeys(executables)),
        tuple(dict.fromkeys(output_paths)),
        True,
    )


def _wrapped_executables(executable: str, arguments: list[str]) -> list[str]:
    """Return literal executable chain behind well-known shell wrappers."""

    if executable not in _WRAPPER_COMMANDS:
        return []
    values = [value.strip() for value in arguments]
    if executable == "timeout":
        values = _skip_options(values)
        if values:
            values = values[1:]
    elif executable == "env":
        values = _skip_options(values)
        while values and "=" in values[0] and not values[0].startswith("="):
            values.pop(0)
    elif executable in {"nice", "stdbuf", "sudo"}:
        values = _skip_options_with_values(values)
    else:
        values = _skip_options(values)
    if not values:
        return []
    candidate = _literal_path(values[0])
    if not candidate:
        return []
    nested = PurePath(candidate).name.lower()
    return [nested, *_wrapped_executables(nested, values[1:])]


def _skip_options(values: list[str]) -> list[str]:
    while values and values[0].startswith("-"):
        values.pop(0)
    return values


def _skip_options_with_values(values: list[str]) -> list[str]:
    options_with_values = frozenset({"-c", "-n", "-o", "-p", "-u", "-g"})
    while values and values[0].startswith("-"):
        option = values.pop(0)
        if option in options_with_values and values:
            values.pop(0)
    return values


def _walk(node) -> Iterable:
    yield node
    for child in node.children:
        yield from _walk(child)


def _node_text(source: str, node) -> str:
    return source[node.start_byte : node.end_byte]


def _command_output_paths(source: str, children: list) -> list[str]:
    """Extract literal output targets from standard flags and shell redirects."""

    values = []
    for index, child in enumerate(children[:-1]):
        if _node_text(source, child).strip() in _OUTPUT_OPTIONS:
            candidate = _literal_path(_node_text(source, children[index + 1]))
            if candidate:
                values.append(candidate)
    return values


def _literal_path(value: str) -> str:
    """Return an unquoted static path, rejecting shell expansion and dynamic text."""

    candidate = value.strip().strip("'\"")
    if not candidate or any(token in candidate for token in ("$", "`", "*", "?", "[", "]")):
        return ""
    return candidate
