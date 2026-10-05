from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import tree_sitter_ruby
from tree_sitter import Language, Node, Parser

from nur.core.models import Task

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = ["RakeProvider", "parse_rakefile"]


log = logging.getLogger("nur")
_SOURCE_FILE = "Rakefile"
_LANGUAGE = Language(tree_sitter_ruby.language())
# Rake interprets leading '-' as an option, '=' as an environment assignment,
# and brackets as task arguments. Accept a conservative runnable-name subset.
_NAME = re.compile(r"[\w][\w:./-]*\Z")


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8")


def _literal(node: Node) -> str | None:
    """Read a Ruby symbol or ordinary quoted string without evaluating it.

    Escapes, interpolation, heredocs and percent literals are deliberately
    omitted: decoding them would require additional Ruby semantics.
    """
    if node.type == "simple_symbol":
        return _text(node)[1:]
    if node.type == "hash_key_symbol":
        return _text(node)
    if node.type not in {"string", "delimited_symbol"}:
        return None
    raw = _text(node)
    if node.type == "delimited_symbol":
        raw = raw[1:]
    if not raw.startswith(("'", '"')):
        return None
    if any(child.type != "string_content" for child in node.named_children):
        return None
    return raw[1:-1]


def _arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [child for child in arguments.named_children if child.type != "comment"]


def _is_self(node: Node | None) -> bool:
    while node is not None and node.type == "parenthesized_statements":
        children = [child for child in node.named_children if child.type != "comment"]
        if len(children) != 1:
            return False
        node = children[0]
    return node is not None and node.type == "self"


def _method(node: Node, disabled: set[str]) -> str | None:
    # Direct singleton definitions replace the methods Rake extends main with.
    # A singleton-class body can replace any of them; do not guess its effects.
    if node.type == "singleton_class":
        value = node.child_by_field_name("value")
        if _is_self(value):
            disabled.update({"task", "multitask", "namespace", "desc"})
    if node.type == "singleton_method":
        owner = node.child_by_field_name("object")
        name_node = node.child_by_field_name("name")
        if _is_self(owner) and name_node is not None:
            disabled.add(_text(name_node))
    if node.type != "call" or node.child_by_field_name("receiver") is not None:
        return None
    method = node.child_by_field_name("method")
    name = _text(method) if method is not None else None
    return name if name not in disabled else None


def _task_name(arguments: list[Node]) -> str | None:
    if not arguments:
        return None
    first = arguments[0]
    if first.type == "pair":
        # Rake expects one task-name/prerequisites pair, not multiple names.
        if len(arguments) != 1:
            return None
        key = first.child_by_field_name("key")
        if key is None:
            return None
        first = key
    name = _literal(first)
    # Rake strips trailing colons from string task names; omit this ambiguous form.
    return (
        name
        if name is not None and _NAME.fullmatch(name) and not name.endswith(":")
        else None
    )


def _namespace_body(
    node: Node, arguments: list[Node], namespace: str
) -> tuple[str, Node] | None:
    if len(arguments) != 1:
        return None
    name = _literal(arguments[0])
    block = node.child_by_field_name("block")
    if name is None or not _NAME.fullmatch(name) or block is None:
        return None
    # Parameters may shadow the Rake DSL. Rescue/ensure bodies are conditional;
    # skip the whole namespace rather than claiming its tasks are unconditional.
    if block.child_by_field_name("parameters") is not None:
        return None
    body = block.child_by_field_name("body")
    if body is None or any(
        child.type in {"rescue", "else", "ensure"} for child in body.named_children
    ):
        return None
    qualified = f"{namespace}:{name}" if namespace else name
    if qualified == "rake" or qualified.startswith("rake:"):
        return None
    return qualified, body


def _make_task(
    descriptions: dict[str, list[str]],
    arguments: list[Node],
    namespace: str,
    description: str | None,
    source_file: str,
) -> Task | None:
    name = _task_name(arguments)
    if name is None:
        return None
    qualified = f"{namespace}:{name}" if namespace else name
    # Rake strips this special lookup prefix instead of invoking that literal name.
    if qualified.startswith("rake:"):
        return None
    comments = descriptions.setdefault(qualified, [])
    if description is not None:
        comment = description.strip()
        if comment and comment not in comments:
            comments.append(comment)
    summary = " / ".join(
        re.split(r"(?<=\w)(\.|!)[ \t]|(\.$|!)|\n", comment, flags=re.ASCII)[0]
        for comment in comments
    )
    return Task(
        name=qualified,
        prefix="rake",
        argv_base=("rake", qualified),
        description=summary if comments else None,
        source_file=source_file,
    )


def _description(arguments: list[Node]) -> str | None:
    return _literal(arguments[0]) if len(arguments) == 1 else None


def _return_scope(node: Node, child: Node, *, inherited: bool | None) -> bool | None:
    if node.type in {"class", "module"}:
        return False if child == node.child_by_field_name("body") else inherited
    if node.type == "singleton_class":
        return (
            inherited is True
            if child == node.child_by_field_name("body")
            else inherited
        )
    if node.type == "singleton_method" and child == node.child_by_field_name("object"):
        return inherited
    if node.type in {"method", "singleton_method", "lambda", "block", "do_block"}:
        return True
    return inherited


def _control_flow_error(root: Node) -> str | None:
    """Validate Ruby control placement beyond the grammar's syntax shapes.

    Invalid placement prevents the whole file loading, even in opaque task
    bodies. Retry needs a rescue; break/next/redo need a block or loop. New
    method/class scopes reset both permissions; ensure resets retry only.
    Class/module returns are invalid. Singleton-class returns need an enclosing
    method or block in current Ruby.
    """
    # None denotes file scope: return is valid there, but not in a nested
    # singleton class unless an enclosing method or block permits it.
    pending: list[tuple[Node, bool, bool, bool | None]] = [(root, False, False, None)]
    new_scopes = {"method", "singleton_method", "class", "singleton_class", "module"}
    blocks = {"lambda", "block", "do_block"}
    loops = {"while", "until", "for", "while_modifier", "until_modifier"}
    while pending:
        node, in_rescue, in_iteration, in_return_scope = pending.pop()
        if node.type == "return" and in_return_scope is False:
            return "return in class or module body"
        if node.type == "retry" and not in_rescue:
            return "retry outside rescue"
        if node.type in {"break", "next", "redo"} and not in_iteration:
            return f"{node.type} outside block or loop"
        if node.type in new_scopes:
            in_rescue = in_iteration = False
        if node.type in blocks or node.type == "ensure":
            in_rescue = False
        body = node.child_by_field_name("body")
        rescue_body = body if node.type == "rescue" else None
        iteration_body = body if node.type in blocks | loops else None
        pending.extend(
            (
                child,
                in_rescue or child == rescue_body,
                in_iteration or child == iteration_body,
                _return_scope(node, child, inherited=in_return_scope),
            )
            for child in node.named_children
        )
    return None


def _constant_target(node: Node | None) -> bool:
    pending = [node] if node is not None else []
    while pending:
        target = pending.pop()
        if target.type in {"constant", "scope_resolution"}:
            return True
        if target.type in {
            "left_assignment_list",
            "destructured_left_assignment",
            "rest_assignment",
        }:
            pending.extend(target.named_children)
    return False


def _method_context(node: Node, child: Node, *, inherited: bool) -> bool:
    if node.type in {"method", "singleton_method"}:
        # A singleton receiver is evaluated in the enclosing scope.
        return inherited if child == node.child_by_field_name("object") else True
    if node.type in {"class", "module", "singleton_class"}:
        return inherited if child != node.child_by_field_name("body") else False
    return inherited


def _binding_names(node: Node) -> Iterator[str]:
    """Read binding targets, omitting expressions and pinned references.

    Yields:
        Names introduced by a parameter list or a single pattern.
    """
    pending = [node]
    containers = {
        "method_parameters",
        "lambda_parameters",
        "block_parameters",
        "destructured_parameter",
        "array_pattern",
        "hash_pattern",
        "find_pattern",
        "as_pattern",
        "alternative_pattern",
        "parenthesized_pattern",
    }
    while pending:
        binding = pending.pop()
        if binding.type == "identifier":
            yield _text(binding)
        elif binding.type in containers:
            pending.extend(reversed(binding.named_children))
        elif binding.type == "keyword_pattern":
            value = binding.child_by_field_name("value")
            key = binding.child_by_field_name("key")
            if value is not None:
                pending.append(value)
            elif key is not None:
                name = _literal(key)
                if name is not None:
                    yield name
        elif binding.type != "variable_reference_pattern":
            name_node = binding.child_by_field_name("name")
            if name_node is not None:
                pending.append(name_node)


def _binding_error(node: Node, *, pattern: bool = False) -> str | None:
    names: set[str] = set()
    for name in _binding_names(node):
        if re.fullmatch(r"_[1-9]", name):
            return "reserved numbered parameter"
        # Ruby deliberately permits repeated ordinary underscore bindings.
        if name.startswith("_"):
            continue
        if name in names:
            return (
                "duplicated pattern variable" if pattern else "duplicated argument name"
            )
        names.add(name)
    return None


def _node_binding_error(node: Node) -> str | None:
    if node.type in {"method_parameters", "lambda_parameters", "block_parameters"}:
        return _binding_error(node)
    if node.type in {"in_clause", "match_pattern", "test_pattern"}:
        pattern = node.child_by_field_name("pattern")
        if pattern is not None:
            return _binding_error(pattern, pattern=True)
    return None


def _method_context_error(root: Node) -> str | None:
    # Blocks retain their enclosing method scope; class bodies start a new one.
    # These compile-time restrictions apply even inside undiscovered bodies.
    pending = [(root, False)]
    while pending:
        node, in_method = pending.pop()
        error = _node_binding_error(node)
        if error is not None:
            return error
        if node.type in {"class", "module"} and in_method:
            return "class or module definition inside method"
        if (
            node.type == "begin_block"
            and node.parent is not None
            and node.parent.type not in {"program", "begin_block"}
        ):
            return "BEGIN outside top level"
        if node.type == "yield" and not in_method:
            return "yield outside method"
        if (
            in_method
            and node.type in {"assignment", "operator_assignment", "for"}
            and _constant_target(
                node.child_by_field_name("pattern" if node.type == "for" else "left")
            )
        ):
            return "constant assignment inside method"
        pending.extend(
            (child, _method_context(node, child, inherited=in_method))
            for child in node.named_children
        )
    return None


def _declarations(root: Node) -> Iterator[tuple[Node, str, str | None]]:
    # Each frame owns its pending description; it cannot leak out of a scope.
    # An explicit stack avoids Python recursion on deeply nested namespaces.
    scopes: list[tuple[Iterator[Node], str, str | None]] = [
        (iter(root.named_children), "", None)
    ]
    disabled: set[str] = set()
    while scopes:
        statements, namespace, description = scopes.pop()
        node = next(statements, None)
        if node is None:
            continue
        if node.type in {"return", "redo"}:
            return
        if node.type in {"break", "next"}:
            continue
        method = _method(node, disabled)
        arguments = _arguments(node)
        if node.type == "comment":
            scopes.append((statements, namespace, description))
            continue
        if method == "desc":
            scopes.append((statements, namespace, _description(arguments)))
            continue
        scopes.append((statements, namespace, None))
        if method in {"task", "multitask"}:
            yield node, namespace, description
        elif method == "namespace":
            nested = _namespace_body(node, arguments, namespace)
            if nested is not None:
                qualified, body = nested
                scopes.append((iter(body.named_children), qualified, None))


def parse_rakefile(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Discover literal Rake tasks at file scope or inside literal namespaces.

    Only direct ``task``/``multitask`` calls and adjacent literal ``desc`` calls
    are read. Task bodies, conditional/generated declarations, file tasks,
    rules and imported files are opaque. No Ruby code or runner is executed.
    """
    root = Parser(_LANGUAGE).parse(text.encode("utf-8")).root_node
    if root.has_error:
        log.warning("nur: skipping %s (invalid Ruby syntax)", source_file)
        return []
    control_error = _control_flow_error(root) or _method_context_error(root)
    if control_error is not None:
        log.warning("nur: skipping %s (%s)", source_file, control_error)
        return []
    tasks: dict[str, Task] = {}
    descriptions: dict[str, list[str]] = {}
    for node, namespace, description in _declarations(root):
        task = _make_task(
            descriptions, _arguments(node), namespace, description, source_file
        )
        if task is not None:
            tasks[task.name] = task
    return list(tasks.values())


class RakeProvider:
    prefix = "rake"

    def detect(self, cwd: Path) -> bool:
        return (cwd / _SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            text = (cwd / _SOURCE_FILE).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
            return []
        return parse_rakefile(text)
