from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

__all__ = ["binding_names", "defined_probe", "literal", "node_text", "syntax_error"]

_MAX_REGEXP_REPEAT = 100_000
_MAX_REGEXP_CAPTURE_GROUPS = 32_767


def node_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8")


def literal(node: Node) -> str | None:
    """Read a Ruby symbol or ordinary quoted string without evaluating it.

    Escapes, interpolation, heredocs and percent literals are deliberately
    omitted: decoding them would require additional Ruby semantics.
    """
    if node.type == "simple_symbol":
        return node_text(node)[1:]
    if node.type == "hash_key_symbol":
        return node_text(node)
    if node.type not in {"string", "delimited_symbol"}:
        return None
    raw = node_text(node)
    if node.type == "delimited_symbol":
        raw = raw[1:]
    if not raw.startswith(("'", '"')):
        return None
    if any(child.type != "string_content" for child in node.named_children):
        return None
    return raw[1:-1]


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


def _control_argument_prefix(node: Node) -> bool:
    field_name = (
        "left" if node.type == "binary" else "begin" if node.type == "range" else None
    )
    control = node.child_by_field_name(field_name) if field_name else None
    if control is None or control.type not in {"return", "break", "next"}:
        return False
    operator = node.child_by_field_name("operator")
    # Ruby reads these as a control's unary/splat/range argument, whereas
    # Tree-sitter represents the control as the left operand.
    return node.type == "range" or (
        operator is not None and operator.type in {"+", "-", "*", "**"}
    )


def _void_value(root: Node) -> bool:
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type in {
            "break",
            "next",
            "redo",
            "retry",
            "return",
        } or _control_argument_prefix(node):
            continue
        if node.type == "begin" and any(
            child.type in {"rescue", "else", "ensure"} for child in node.named_children
        ):
            return False
        if node.type in {
            "parenthesized_statements",
            "then",
            "else",
            "begin",
            "body_statement",
            "in",
        }:
            children = [
                child for child in node.named_children if child.type != "comment"
            ]
            if not children:
                return False
            pending.append(children[-1])
        elif node.type in {"if", "unless", "conditional"}:
            branches = [
                node.child_by_field_name(field)
                for field in ("consequence", "alternative")
            ]
            if any(branch is None for branch in branches):
                return False
            pending.extend(branch for branch in branches if branch is not None)
        else:
            return False
    return True


def _value_error(node: Node) -> str | None:
    fields = {
        "assignment": ("right",),
        "operator_assignment": ("right",),
        "optional_parameter": ("value",),
        "keyword_parameter": ("value",),
        "pair": ("key", "value"),
        "call": ("receiver",),
        "singleton_method": ("object",),
        "singleton_class": ("value",),
        "for": ("value",),
        "case": ("value",),
    }.get(node.type, ())
    values = [
        value
        for field in fields
        if (value := node.child_by_field_name(field)) is not None
    ]
    condition = node.child_by_field_name("condition")
    if condition is not None:
        values.append(condition)
    if node.type == "unary" and not defined_probe(node):
        operand = node.child_by_field_name("operand")
        if operand is not None:
            values.append(operand)
    if node.type == "binary":
        operator = node.child_by_field_name("operator")
        fields = () if _control_argument_prefix(node) else ("left",)
        if operator is None or operator.type not in {"&&", "||", "and", "or"}:
            fields += ("right",)
        values.extend(
            value
            for field in fields
            if (value := node.child_by_field_name(field)) is not None
        )
    if node.type == "range":
        fields = ("end",) if _control_argument_prefix(node) else ("begin", "end")
        values.extend(
            value
            for field in fields
            if (value := node.child_by_field_name(field)) is not None
        )
    if node.type in {
        "argument_list",
        "array",
        "element_reference",
        "right_assignment_list",
        "splat_argument",
        "hash_splat_argument",
        "block_argument",
        "superclass",
    }:
        values.extend(child for child in node.named_children if child.type != "comment")
    return (
        "void value expression" if any(_void_value(value) for value in values) else None
    )


def _control_permissions(
    node: Node, child: Node, *, in_rescue: bool, in_iteration: bool
) -> tuple[bool, bool]:
    new_scopes = {"method", "singleton_method", "class", "singleton_class", "module"}
    blocks = {"lambda", "block", "do_block", "end_block"}
    loops = {"while", "until", "for", "while_modifier", "until_modifier"}
    if node.type in new_scopes:
        headers = {
            node.child_by_field_name(field)
            for field in ("object", "value", "name", "superclass")
        }
        if child not in headers:
            in_rescue = in_iteration = False
    if node.type in blocks or node.type == "ensure":
        in_rescue = False
    body = node.child_by_field_name("body")
    if (node.type == "rescue" and child == body) or (
        node.type == "rescue_modifier" and child == node.child_by_field_name("handler")
    ):
        in_rescue = True
    if node.type == "end_block" or (node.type in blocks | loops and child == body):
        in_iteration = True
    if node.type in loops - {"for"} and child == node.child_by_field_name("condition"):
        in_iteration = True
    return in_rescue, in_iteration


def defined_probe(node: Node) -> bool:
    operator = node.child_by_field_name("operator")
    return node.type == "unary" and operator is not None and operator.type == "defined?"


def _within_defined(node: Node, boundaries: set[str]) -> bool:
    # A probe does not execute its operand, but separately compiled bodies
    # still enforce their own control placement.
    child = node
    parent = child.parent
    while parent is not None:
        if defined_probe(parent):
            return True
        if parent.type in boundaries and child not in {
            parent.child_by_field_name(field)
            for field in ("object", "value", "name", "superclass")
        }:
            return False
        child, parent = parent, parent.parent
    return False


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
    loops = {"while", "until", "for", "while_modifier", "until_modifier"}
    while pending:
        node, in_rescue, in_iteration, in_return_scope = pending.pop()
        condition = node.child_by_field_name("condition")
        loop_condition = condition if node.type in loops - {"for"} else None
        if loop_condition is not None and _void_value(loop_condition):
            return "void value in loop condition"
        value_error = _value_error(node)
        if value_error is not None:
            return value_error
        if node.type == "return" and in_return_scope is False:
            return "return in class or module body"
        if (
            node.type == "retry"
            and not in_rescue
            and not _within_defined(
                node,
                {"method", "singleton_method", "class", "module", "singleton_class"},
            )
        ):
            return "retry outside rescue"
        if (
            node.type in {"break", "next", "redo"}
            and not in_iteration
            and not _within_defined(
                node, {"method", "singleton_method", "singleton_class"}
            )
        ):
            return f"{node.type} outside block or loop"
        pending.extend(
            (
                child,
                *_control_permissions(
                    node, child, in_rescue=in_rescue, in_iteration=in_iteration
                ),
                _return_scope(node, child, inherited=in_return_scope),
            )
            for child in node.named_children
        )
    return None


def _assignment_error(node: Node, *, in_method: bool) -> str | None:
    field_name = {
        "assignment": "left",
        "operator_assignment": "left",
        "for": "pattern",
        "rescue": "variable",
    }.get(node.type)
    target = node.child_by_field_name(field_name) if field_name else None
    pending = [target] if target is not None else []
    while pending:
        target = pending.pop()
        if target.type in {"nil", "true", "false", "self"}:
            return "nonassignable target"
        if target.type == "global_variable" and re.fullmatch(
            r"\$(?:[1-9][0-9]*|[&`'+])", node_text(target)
        ):
            return "readonly match global"
        if in_method and target.type in {"constant", "scope_resolution"}:
            return "constant assignment inside method"
        if target.type in {
            "left_assignment_list",
            "destructured_left_assignment",
            "rest_assignment",
            "exception_variable",
        }:
            pending.extend(target.named_children)
    return None


def _begin_at_top_level(node: Node) -> bool:
    parent = node.parent
    modifiers = {
        "if_modifier",
        "unless_modifier",
        "while_modifier",
        "until_modifier",
        "rescue_modifier",
    }
    while parent is not None and parent.type in modifiers:
        parent = parent.parent
    return parent is not None and parent.type in {"program", "begin_block"}


def _method_context(node: Node, child: Node, *, inherited: bool) -> bool:
    if node.type in {"method", "singleton_method"}:
        # A singleton receiver is evaluated in the enclosing scope.
        return inherited if child == node.child_by_field_name("object") else True
    if node.type in {"class", "module", "singleton_class"}:
        return inherited if child != node.child_by_field_name("body") else False
    return inherited


def binding_names(node: Node) -> Iterator[str]:
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
            yield node_text(binding)
        elif binding.type in containers:
            pending.extend(reversed(binding.named_children))
        elif binding.type == "keyword_pattern":
            value = binding.child_by_field_name("value")
            key = binding.child_by_field_name("key")
            if value is not None:
                pending.append(value)
            elif key is not None:
                name = literal(key)
                if name is not None:
                    yield name
        elif binding.type != "variable_reference_pattern":
            name_node = binding.child_by_field_name("name")
            if name_node is not None:
                pending.append(name_node)


def _binding_error(node: Node, *, pattern: bool = False) -> str | None:
    names: set[str] = set()
    for name in binding_names(node):
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
    if node.type == "alternative_pattern" and any(
        not name.startswith("_") for name in binding_names(node)
    ):
        return "variable binding in alternative pattern"
    if node.type in {"method_parameters", "lambda_parameters", "block_parameters"}:
        return _binding_error(node)
    if node.type in {"in_clause", "match_pattern", "test_pattern"}:
        pattern = node.child_by_field_name("pattern")
        if pattern is not None:
            return _binding_error(pattern, pattern=True)
    return None


def _endless_setter(node: Node) -> bool:
    if node.type not in {"method", "singleton_method"}:
        return False
    name = node.child_by_field_name("name")
    return (
        name is not None
        and (name.type == "setter" or node_text(name) == "[]=")
        and any(child.type == "=" for child in node.children)
    )


def _alias_error(node: Node) -> str | None:
    if node.type != "alias":
        return None
    target = node.child_by_field_name("name")
    source = node.child_by_field_name("alias")
    if target is None or source is None:
        return None
    if (target.type == "global_variable") != (source.type == "global_variable"):
        return "mixed global and method alias"
    if source.type == "global_variable" and re.fullmatch(
        r"\$[1-9][0-9]*", node_text(source)
    ):
        return "numbered match alias source"
    return None


def _regexp_escape(pattern: str, index: int) -> tuple[str, int] | None:
    if index + 1 >= len(pattern):
        return None
    escaped = pattern[index + 1]
    if escaped == "x":
        digits = pattern[index + 2 : index + 4]
        if re.fullmatch(r"[0-7][0-9a-fA-F]", digits) is None:
            return None
        return r"\x" + digits, index + 4
    translations = {"z": "Z", "G": "A", "e": "x1b", "h": "d", "H": "D", "R": "n"}
    if escaped.isdecimal() or (
        escaped.isalpha()
        and escaped not in "aAbBdDefGhHnRrSsTtVvwWzZ"  # pragma: allowlist secret
    ):
        return None
    return "\\" + translations.get(escaped, escaped), index + 2


def _regexp_token_supported(pattern: str, index: int, *, in_class: bool) -> bool:
    if not in_class and pattern[index : index + 2] == "(?":
        return pattern[index : index + 3] in {"(?:", "(?=", "(?!"}
    if pattern[index] == "[":
        return (
            not in_class
            and pattern[index + 1 : index + 2] != "]"
            and pattern[index + 1 : index + 3] != "^]"
        )
    return not in_class or pattern[index : index + 2] not in {"&&", "--", "||", "~~"}


def _regexp_pattern(pattern: str, *, extended: bool) -> str | None:
    # Compile only the shared ASCII Ruby/Python regexp subset. Encoding-sensitive
    # escapes, named groups, lookbehinds, and set expressions remain unsupported.
    if not pattern.isascii():
        return None
    converted: list[str] = []
    index = 0
    in_class = False
    while index < len(pattern):
        character = pattern[index]
        if extended and not in_class and character == "#":
            newline = pattern.find("\n", index)
            if newline == -1:
                break
            index = newline
            continue
        if character == "\\":
            if in_class and pattern[index + 1 : index + 2] == "R":
                return None
            escape = _regexp_escape(pattern, index)
            if escape is None:
                return None
            value, index = escape
            converted.append(value)
            continue
        if not _regexp_token_supported(pattern, index, in_class=in_class):
            return None
        in_class = character == "[" or (in_class and character != "]")
        converted.append(character)
        index += 1
    return "".join(converted)


def _regexp_error(node: Node) -> str | None:
    options = node_text(node.children[-1])[1:]
    if set(options) - set("imxounes"):
        return "invalid Ruby regexp options"
    if any(child.type == "interpolation" for child in node.named_children):
        # MRI constructs interpolated patterns at runtime rather than compiling
        # their regexp contents while loading the file.
        return None
    pattern = _regexp_pattern(
        "".join(node_text(child) for child in node.named_children),
        extended="x" in options,
    )
    if pattern is None:
        return "unsupported Ruby regexp"
    # Onigmo limits explicit repeats to 100000, unlike Python's regexp engine.
    repeat_limit = str(_MAX_REGEXP_REPEAT)
    for repeat in re.finditer(r"\{([0-9]+)(?:,([0-9]*))?\}", pattern):
        for number in repeat.groups():
            digits = (number or "").lstrip("0")
            if len(digits) > len(repeat_limit) or (
                len(digits) == len(repeat_limit) and digits > repeat_limit
            ):
                return "invalid or unsupported Ruby regexp repeat"
    try:
        compiled = re.compile(pattern, re.VERBOSE if "x" in options else 0)
    except (re.error, ValueError, OverflowError, RecursionError) as error:
        return f"invalid or unsupported Ruby regexp: {error}"
    return (
        "invalid Ruby regexp capture count"
        if compiled.groups > _MAX_REGEXP_CAPTURE_GROUPS
        else None
    )


def _control_argument_valid(control: str, argument: Node) -> bool:
    if argument.type in {"block_argument", "forward_argument"}:
        return False
    if control == "yield":
        return True
    if argument.type in {"pair", "hash_splat_argument"}:
        return False
    return argument.type != "splat_argument" or any(
        child.type != "comment" for child in argument.named_children
    )


def _control_parentheses_valid(control: str, arguments: Node) -> bool:
    if control == "yield" or arguments.children[0].type != "(":
        return True
    values = [child for child in arguments.named_children if child.type != "comment"]
    return len(values) <= 1 and all(value.type != "splat_argument" for value in values)


def _control_arguments_error(node: Node) -> str | None:
    if node.type not in {"yield", "return", "break", "next", "redo", "retry"}:
        return None
    arguments = next(
        (child for child in node.named_children if child.type == "argument_list"), None
    )
    if arguments is None:
        return None
    if not _control_parentheses_valid(node.type, arguments):
        return f"invalid {node.type} arguments"
    if node.type in {"redo", "retry"} or any(
        not _control_argument_valid(node.type, argument)
        for argument in arguments.named_children
        if argument.type != "comment"
    ):
        return f"invalid {node.type} arguments"
    return None


def _method_context_error(root: Node) -> str | None:
    # Blocks retain their enclosing method scope; class bodies start a new one.
    # These compile-time restrictions apply even inside undiscovered bodies.
    pending = [(root, False)]
    while pending:
        node, in_method = pending.pop()
        if _endless_setter(node):
            return "endless setter definition"
        error = (
            _node_binding_error(node)
            or _assignment_error(node, in_method=in_method)
            or _alias_error(node)
            or _control_arguments_error(node)
            or (_regexp_error(node) if node.type == "regex" else None)
        )
        if error is not None:
            return error
        if node.type in {"class", "module"} and in_method:
            return "class or module definition inside method"
        if node.type == "begin_block" and not _begin_at_top_level(node):
            return "BEGIN outside top level"
        if node.type == "yield" and not in_method and not _within_defined(node, set()):
            return "yield outside method"
        pending.extend(
            (child, _method_context(node, child, inherited=in_method))
            for child in node.named_children
        )
    return None


@dataclass(slots=True)
class _LocalScope:
    parent: _LocalScope | None = None
    names: set[str] = field(default_factory=set)
    implicit_names: set[str] = field(default_factory=set)
    forwarding: dict[str, bool] = field(default_factory=dict)
    block: bool = False
    explicit: bool = False
    numbered: bool = False
    inner_numbered: bool = False
    implicit_it: bool = False

    def contains(self, name: str, *, include_implicit: bool = True) -> bool:
        current: _LocalScope | None = self
        while current is not None:
            if name in current.names or (
                include_implicit and name in current.implicit_names
            ):
                return True
            current = current.parent
        return False

    def use_numbered(self, name: str) -> str | None:
        if not self.block:
            return None
        if self.explicit:
            return "numbered parameter with explicit block parameters"
        if self.implicit_it:
            return "numbered parameter with implicit it"
        if self.inner_numbered:
            return "numbered parameter already used in inner block"
        outer = self.parent
        while outer is not None:
            if outer.numbered:
                return "numbered parameter already used in outer block"
            outer.inner_numbered = True
            outer = outer.parent
        self.numbered = True
        self.implicit_names.update(
            f"_{number}" for number in range(1, int(name[1]) + 1)
        )
        return None

    def use_it(self) -> str | None:
        if not self.block or self.contains("it", include_implicit=False):
            return None
        if self.explicit:
            return "implicit it with explicit block parameters"
        if self.numbered:
            return "implicit it with numbered parameter"
        self.implicit_it = True
        self.implicit_names.add("it")
        return None


def _binding_steps(node: Node) -> list[tuple[Node, bool]]:
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
        "left_assignment_list",
        "destructured_left_assignment",
        "rest_assignment",
        "exception_variable",
    }
    if node.type in containers:
        return [(child, True) for child in node.named_children]
    if node.type == "variable_reference_pattern":
        return [(node, False)]
    if node.type == "keyword_pattern":
        value = node.child_by_field_name("value")
        return [(value, True)] if value is not None else []
    name = node.child_by_field_name("name")
    if name is not None:
        return [(child, child == name) for child in node.named_children]
    return [(child, False) for child in node.named_children]


def _scope_steps(
    node: Node, scope: _LocalScope
) -> list[tuple[Node, _LocalScope, bool]]:
    if node.type in {"method", "singleton_method"}:
        local = _LocalScope()
        return [
            (
                child,
                scope if child == node.child_by_field_name("object") else local,
                child == node.child_by_field_name("parameters"),
            )
            for child in node.named_children
            if child != node.child_by_field_name("name")
        ]
    if node.type in {"class", "module", "singleton_class"}:
        local = _LocalScope()
        return [
            (
                child,
                local if child == node.child_by_field_name("body") else scope,
                False,
            )
            for child in node.named_children
        ]
    if node.type in {"block", "do_block", "lambda"}:
        # Tree-sitter gives lambdas a separate block node for their body.
        if node.parent is None or node.parent.type != "lambda":
            scope = _LocalScope(
                parent=scope,
                block=True,
                explicit=node.child_by_field_name("parameters") is not None,
            )
        return [
            (child, scope, child == node.child_by_field_name("parameters"))
            for child in node.named_children
        ]
    target_field = {
        "assignment": "left",
        "operator_assignment": "left",
        "for": "pattern",
        "in_clause": "pattern",
        "match_pattern": "pattern",
        "test_pattern": "pattern",
        "rescue": "variable",
    }.get(node.type)
    target = node.child_by_field_name(target_field) if target_field else None
    return [(child, scope, child == target) for child in node.named_children]


def _implicit_reference(node: Node) -> bool:
    if node.type != "identifier" or re.fullmatch(r"_[1-9]|it", node_text(node)) is None:
        return False
    parent = node.parent
    return parent is None or (
        node != parent.child_by_field_name("method")
        and parent.type not in {"alias", "undef"}
    )


def _register_forwarding(node: Node, scope: _LocalScope) -> None:
    argument = {
        "forward_parameter": "forward_argument",
        "splat_parameter": "splat_argument",
        "hash_splat_parameter": "hash_splat_argument",
        "block_parameter": "block_argument",
    }.get(node.type)
    if argument is not None and node.child_by_field_name("name") is None:
        # Methods grant forwarding; an anonymous block parameter makes the
        # corresponding enclosing-method forwarding ambiguous.
        scope.forwarding[argument] = not scope.block


def _call_error(node: Node) -> str | None:
    if node.type != "call" or node.child_by_field_name("block") is None:
        return None
    arguments = node.child_by_field_name("arguments")
    if arguments is not None and any(
        argument.type in {"block_argument", "forward_argument"}
        for argument in arguments.named_children
    ):
        return "both block argument and literal block"
    return None


def _forward_error(node: Node, scope: _LocalScope) -> str | None:
    if node.type not in {
        "forward_argument",
        "splat_argument",
        "hash_splat_argument",
        "block_argument",
    }:
        return None
    if any(child.type != "comment" for child in node.named_children):
        return None
    current: _LocalScope | None = scope
    while current is not None:
        permission = current.forwarding.get(node.type)
        if permission is not None:
            return (
                None
                if permission
                else "argument forwarding conflicts with anonymous block parameter"
            )
        current = current.parent
    return "argument forwarding without matching method parameter"


def _register_binding(node: Node, scope: _LocalScope) -> str | None:
    _register_forwarding(node, scope)
    name = None
    if node.type == "identifier":
        name = node_text(node)
    elif node.type == "keyword_pattern" and node.child_by_field_name("value") is None:
        key = node.child_by_field_name("key")
        name = literal(key) if key is not None else None
    if name is not None:
        if re.fullmatch(r"_[1-9]", name):
            return "reserved numbered parameter"
        scope.names.add(name)
    return None


def _implicit_error(node: Node, scope: _LocalScope) -> str | None:
    if not _implicit_reference(node):
        return None
    name = node_text(node)
    return scope.use_it() if name == "it" else scope.use_numbered(name)


def _pin_error(node: Node, scope: _LocalScope) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is None or name_node.type != "identifier":
        return None
    error = _implicit_error(name_node, scope)
    if error is not None:
        return error
    name = node_text(name_node)
    return None if scope.contains(name) else f"{name}: no such local variable"


def _lexical_scope_error(root: Node) -> str | None:
    # Visit source order: assignment targets bind before their RHS, while a
    # pattern pin sees only preceding bindings. Blocks inherit locals; method
    # and class bodies reset them. All traversal remains iterative.
    pending = [(root, _LocalScope(), False)]
    while pending:
        node, scope, binding = pending.pop()
        if binding:
            error = _register_binding(node, scope)
            if error is not None:
                return error
            pending.extend(
                (child, scope, bind) for child, bind in reversed(_binding_steps(node))
            )
            continue
        if node.type == "variable_reference_pattern":
            error = _pin_error(node, scope)
            if error is not None:
                return error
            continue
        error = (
            _implicit_error(node, scope)
            or _forward_error(node, scope)
            or _call_error(node)
        )
        if error is not None:
            return error
        pending.extend(reversed(_scope_steps(node, scope)))
    return None


def syntax_error(root: Node) -> str | None:
    return (
        _control_flow_error(root)
        or _method_context_error(root)
        or _lexical_scope_error(root)
    )
