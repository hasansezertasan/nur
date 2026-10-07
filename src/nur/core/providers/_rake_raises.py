from __future__ import annotations

import re
import struct
from typing import TYPE_CHECKING

from nur.core.providers._rake_catch_tags import generated_tag_matches
from nur.core.providers._rake_overrides import (
    TERMINATING_METHODS,
    receiver_name,
    termination_method,
)
from nur.core.providers._rake_syntax import is_self, literal, node_text

if TYPE_CHECKING:
    from tree_sitter import Node

__all__ = [
    "handled_error",
    "handled_load_error",
    "inactive_handler",
    "load_raise_error",
    "record_catch",
]

_CORE_VALUES = {
    "ARGV": "array",
    "ENV": "object",
    "RUBY_COPYRIGHT": "string",
    "RUBY_DESCRIPTION": "string",
    "RUBY_ENGINE": "string",
    "RUBY_ENGINE_VERSION": "string",
    "RUBY_PATCHLEVEL": "integer",
    "RUBY_PLATFORM": "string",
    "RUBY_RELEASE_DATE": "string",
    "RUBY_REVISION": "string",
    "RUBY_VERSION": "string",
    "STDERR": "object",
    "STDIN": "object",
    "STDOUT": "object",
    "TOPLEVEL_BINDING": "object",
}

_CORE_NONEXCEPTIONS = {
    "Array",
    "BasicObject",
    "Binding",
    "Class",
    "Comparable",
    "Complex",
    "Data",
    "DidYouMean",
    "Dir",
    "Encoding",
    "Encoding::Converter",
    "Enumerable",
    "Enumerator",
    "Enumerator::ArithmeticSequence",
    "Enumerator::Chain",
    "Enumerator::Generator",
    "Enumerator::Lazy",
    "Enumerator::Producer",
    "Enumerator::Product",
    "Enumerator::Yielder",
    "Errno",
    "ErrorHighlight",
    "FalseClass",
    "Fiber",
    "File",
    "File::Constants",
    "File::Stat",
    "FileTest",
    "Float",
    "GC",
    "GC::Profiler",
    "Gem",
    "Hash",
    "IO",
    "IO::Buffer",
    "IO::WaitReadable",
    "IO::WaitWritable",
    "Integer",
    "Kernel",
    "Marshal",
    "MatchData",
    "Math",
    "Method",
    "Module",
    "NilClass",
    "Numeric",
    "Object",
    "ObjectSpace",
    "ObjectSpace::WeakKeyMap",
    "ObjectSpace::WeakMap",
    "Proc",
    "Process",
    "Process::GID",
    "Process::Status",
    "Process::Sys",
    "Process::Tms",
    "Process::UID",
    "Process::Waiter",
    "Ractor",
    "Ractor::MovedObject",
    "Random",
    "Random::Base",
    "Random::Formatter",
    "Range",
    "Rational",
    "Refinement",
    "Regexp",
    "RubyVM",
    "RubyVM::AbstractSyntaxTree",
    "RubyVM::AbstractSyntaxTree::Location",
    "RubyVM::AbstractSyntaxTree::Node",
    "RubyVM::InstructionSequence",
    "RubyVM::RJIT",
    "Signal",
    "String",
    "Struct",
    "Symbol",
    "SyntaxSuggest",
    "Thread",
    "Thread::Backtrace",
    "Thread::Backtrace::Location",
    "Thread::ConditionVariable",
    "Thread::Mutex",
    "Thread::Queue",
    "Thread::SizedQueue",
    "ThreadGroup",
    "Time",
    "TracePoint",
    "TrueClass",
    "UnboundMethod",
    "UnicodeNormalize",
    "Warning",
}

_ERROR_PARENTS = {
    "ArgumentError": "StandardError",
    "ClosedQueueError": "StopIteration",
    "EOFError": "IOError",
    "Encoding::CompatibilityError": "EncodingError",
    "Encoding::ConverterNotFoundError": "EncodingError",
    "Encoding::InvalidByteSequenceError": "EncodingError",
    "Encoding::UndefinedConversionError": "EncodingError",
    "EncodingError": "StandardError",
    "Errno::E2BIG": "SystemCallError",
    "Errno::EACCES": "SystemCallError",
    "Errno::EADDRINUSE": "SystemCallError",
    "Errno::EADDRNOTAVAIL": "SystemCallError",
    "Errno::EAFNOSUPPORT": "SystemCallError",
    "Errno::EAGAIN": "SystemCallError",
    "Errno::EALREADY": "SystemCallError",
    "Errno::EAUTH": "SystemCallError",
    "Errno::EBADARCH": "SystemCallError",
    "Errno::EBADEXEC": "SystemCallError",
    "Errno::EBADF": "SystemCallError",
    "Errno::EBADMACHO": "SystemCallError",
    "Errno::EBADMSG": "SystemCallError",
    "Errno::EBADRPC": "SystemCallError",
    "Errno::EBUSY": "SystemCallError",
    "Errno::ECANCELED": "SystemCallError",
    "Errno::ECHILD": "SystemCallError",
    "Errno::ECONNABORTED": "SystemCallError",
    "Errno::ECONNREFUSED": "SystemCallError",
    "Errno::ECONNRESET": "SystemCallError",
    "Errno::EDEADLK": "SystemCallError",
    "Errno::EDESTADDRREQ": "SystemCallError",
    "Errno::EDEVERR": "SystemCallError",
    "Errno::EDOM": "SystemCallError",
    "Errno::EDQUOT": "SystemCallError",
    "Errno::EEXIST": "SystemCallError",
    "Errno::EFAULT": "SystemCallError",
    "Errno::EFBIG": "SystemCallError",
    "Errno::EFTYPE": "SystemCallError",
    "Errno::EHOSTDOWN": "SystemCallError",
    "Errno::EHOSTUNREACH": "SystemCallError",
    "Errno::EIDRM": "SystemCallError",
    "Errno::EILSEQ": "SystemCallError",
    "Errno::EINPROGRESS": "SystemCallError",
    "Errno::EINTR": "SystemCallError",
    "Errno::EINVAL": "SystemCallError",
    "Errno::EIO": "SystemCallError",
    "Errno::EISCONN": "SystemCallError",
    "Errno::EISDIR": "SystemCallError",
    "Errno::ELOOP": "SystemCallError",
    "Errno::EMFILE": "SystemCallError",
    "Errno::EMLINK": "SystemCallError",
    "Errno::EMSGSIZE": "SystemCallError",
    "Errno::EMULTIHOP": "SystemCallError",
    "Errno::ENAMETOOLONG": "SystemCallError",
    "Errno::ENEEDAUTH": "SystemCallError",
    "Errno::ENETDOWN": "SystemCallError",
    "Errno::ENETRESET": "SystemCallError",
    "Errno::ENETUNREACH": "SystemCallError",
    "Errno::ENFILE": "SystemCallError",
    "Errno::ENOATTR": "SystemCallError",
    "Errno::ENOBUFS": "SystemCallError",
    "Errno::ENODATA": "SystemCallError",
    "Errno::ENODEV": "SystemCallError",
    "Errno::ENOENT": "SystemCallError",
    "Errno::ENOEXEC": "SystemCallError",
    "Errno::ENOLCK": "SystemCallError",
    "Errno::ENOLINK": "SystemCallError",
    "Errno::ENOMEM": "SystemCallError",
    "Errno::ENOMSG": "SystemCallError",
    "Errno::ENOPOLICY": "SystemCallError",
    "Errno::ENOPROTOOPT": "SystemCallError",
    "Errno::ENOSPC": "SystemCallError",
    "Errno::ENOSR": "SystemCallError",
    "Errno::ENOSTR": "SystemCallError",
    "Errno::ENOSYS": "SystemCallError",
    "Errno::ENOTBLK": "SystemCallError",
    "Errno::ENOTCAPABLE": "SystemCallError",
    "Errno::ENOTCONN": "SystemCallError",
    "Errno::ENOTDIR": "SystemCallError",
    "Errno::ENOTEMPTY": "SystemCallError",
    "Errno::ENOTRECOVERABLE": "SystemCallError",
    "Errno::ENOTSOCK": "SystemCallError",
    "Errno::ENOTSUP": "SystemCallError",
    "Errno::ENOTTY": "SystemCallError",
    "Errno::ENXIO": "SystemCallError",
    "Errno::EOPNOTSUPP": "SystemCallError",
    "Errno::EOVERFLOW": "SystemCallError",
    "Errno::EOWNERDEAD": "SystemCallError",
    "Errno::EPERM": "SystemCallError",
    "Errno::EPFNOSUPPORT": "SystemCallError",
    "Errno::EPIPE": "SystemCallError",
    "Errno::EPROCLIM": "SystemCallError",
    "Errno::EPROCUNAVAIL": "SystemCallError",
    "Errno::EPROGMISMATCH": "SystemCallError",
    "Errno::EPROGUNAVAIL": "SystemCallError",
    "Errno::EPROTO": "SystemCallError",
    "Errno::EPROTONOSUPPORT": "SystemCallError",
    "Errno::EPROTOTYPE": "SystemCallError",
    "Errno::EPWROFF": "SystemCallError",
    "Errno::EQFULL": "SystemCallError",
    "Errno::ERANGE": "SystemCallError",
    "Errno::EREMOTE": "SystemCallError",
    "Errno::EROFS": "SystemCallError",
    "Errno::ERPCMISMATCH": "SystemCallError",
    "Errno::ESHLIBVERS": "SystemCallError",
    "Errno::ESHUTDOWN": "SystemCallError",
    "Errno::ESOCKTNOSUPPORT": "SystemCallError",
    "Errno::ESPIPE": "SystemCallError",
    "Errno::ESRCH": "SystemCallError",
    "Errno::ESTALE": "SystemCallError",
    "Errno::ETIME": "SystemCallError",
    "Errno::ETIMEDOUT": "SystemCallError",
    "Errno::ETOOMANYREFS": "SystemCallError",
    "Errno::ETXTBSY": "SystemCallError",
    "Errno::EUSERS": "SystemCallError",
    "Errno::EXDEV": "SystemCallError",
    "Errno::NOERROR": "SystemCallError",
    "FiberError": "StandardError",
    "FloatDomainError": "RangeError",
    "FrozenError": "RuntimeError",
    "IO::Buffer::AccessError": "RuntimeError",
    "IO::Buffer::AllocationError": "RuntimeError",
    "IO::Buffer::InvalidatedError": "RuntimeError",
    "IO::Buffer::LockedError": "RuntimeError",
    "IO::Buffer::MaskError": "ArgumentError",
    "IO::EAGAINWaitReadable": "Errno::EAGAIN",
    "IO::EAGAINWaitWritable": "Errno::EAGAIN",
    "IO::EINPROGRESSWaitReadable": "Errno::EINPROGRESS",
    "IO::EINPROGRESSWaitWritable": "Errno::EINPROGRESS",
    "IO::TimeoutError": "IOError",
    "IOError": "StandardError",
    "IndexError": "StandardError",
    "Interrupt": "SignalException",
    "KeyError": "IndexError",
    "LoadError": "ScriptError",
    "LocalJumpError": "StandardError",
    "Math::DomainError": "StandardError",
    "NameError": "StandardError",
    "NoMatchingPatternError": "StandardError",
    "NoMatchingPatternKeyError": "NoMatchingPatternError",
    "NoMemoryError": "Exception",
    "NoMethodError": "NameError",
    "NotImplementedError": "ScriptError",
    "Ractor::ClosedError": "StopIteration",
    "Ractor::Error": "RuntimeError",
    "Ractor::IsolationError": "Ractor::Error",
    "Ractor::MovedError": "Ractor::Error",
    "Ractor::RemoteError": "Ractor::Error",
    "Ractor::UnsafeError": "Ractor::Error",
    "RangeError": "StandardError",
    "Regexp::TimeoutError": "RegexpError",
    "RegexpError": "StandardError",
    "RuntimeError": "StandardError",
    "ScriptError": "Exception",
    "SecurityError": "Exception",
    "SignalException": "Exception",
    "StandardError": "Exception",
    "StopIteration": "IndexError",
    "SyntaxError": "ScriptError",
    "SystemCallError": "StandardError",
    "SystemExit": "Exception",
    "SystemStackError": "Exception",
    "ThreadError": "StandardError",
    "TypeError": "StandardError",
    "UncaughtThrowError": "ArgumentError",
    "ZeroDivisionError": "StandardError",
}


def _arguments(node: Node) -> list[Node]:
    argument_list = node.child_by_field_name("arguments")
    return (
        [
            child
            for child in argument_list.named_children
            if child.type not in {"comment", "block_argument"}
        ]
        if argument_list is not None
        else []
    )


_NON_EXCEPTION_LITERALS = {
    "nil",
    "true",
    "false",
    "integer",
    "float",
    "array",
    "hash",
    "simple_symbol",
    "delimited_symbol",
    "regex",
    "range",
    "lambda",
}

_RAISE_MAX_ARGS = 3


def _raise_argument_error(arguments: list[Node]) -> str | None:
    if len(arguments) > _RAISE_MAX_ARGS:
        return "ArgumentError"
    if len(arguments) != _RAISE_MAX_ARGS:
        return None
    trace = arguments[-1]
    if trace.type == "array":
        return (
            "TypeError"
            if any(
                child.type in _NON_EXCEPTION_LITERALS for child in trace.named_children
            )
            else None
        )
    return (
        "TypeError"
        if trace.type in _NON_EXCEPTION_LITERALS - {"nil", "array"}
        else None
    )


def _cause_argument(node: Node) -> bool:
    key = node.child_by_field_name("key") if node.type == "pair" else None
    return key is not None and literal(key) == "cause"


def _invalid_literal_cause(arguments: list[Node]) -> bool:
    return any(
        _cause_argument(argument)
        and (value := argument.child_by_field_name("value")) is not None
        and value.type in (_NON_EXCEPTION_LITERALS - {"nil"}) | {"string"}
        for argument in arguments
    )


def _raised_kind(node: Node) -> str:
    raw_arguments = _arguments(node)
    arguments = [
        argument for argument in raw_arguments if not _cause_argument(argument)
    ]
    error = (
        "TypeError"
        if _invalid_literal_cause(raw_arguments)
        else _raise_argument_error(arguments)
    )
    if error is not None:
        return error
    if not arguments:
        return _active_exception(node)
    if arguments[0].type == "string":
        return "RuntimeError" if len(arguments) == 1 else "TypeError"
    first = arguments[0]
    if first.type in {"constant", "scope_resolution"}:
        return _constant_raise_kind(first, len(arguments))
    if (
        first.type == "call"
        and (method := first.child_by_field_name("method")) is not None
        and node_text(method) == "new"
    ):
        return _constructed_raise_kind(first, len(arguments))
    return "TypeError" if first.type in _NON_EXCEPTION_LITERALS else "Exception"


def _constructed_raise_kind(node: Node, arity: int) -> str:
    name = receiver_name(node.child_by_field_name("receiver"))
    values = _arguments(node)
    positional = [value for value in values if value.type != "pair"]
    specialized = {
        "NameError",
        "NoMethodError",
        "SystemExit",
        "SignalException",
        "Interrupt",
        "SystemCallError",
    }
    known_exception = name in _ERROR_PARENTS or name == "Exception"
    if (
        known_exception
        and name not in specialized
        and not name.startswith("Errno::")
        and len(positional) > 1
        and not any(
            value.type in {"splat_argument", "hash_splat_argument"} for value in values
        )
    ):
        return "ArgumentError"
    if name == "String" and (
        not values or (len(values) == 1 and values[0].type == "string")
    ):
        return "RuntimeError" if arity == 1 else "TypeError"
    return name


def _constant_raise_kind(node: Node, arity: int) -> str:
    name = receiver_name(node)
    if name in _CORE_VALUES:
        return (
            "RuntimeError"
            if _CORE_VALUES[name] == "string" and arity == 1
            else "TypeError"
        )
    return "TypeError" if name in _CORE_NONEXCEPTIONS else name


def _active_exception(node: Node) -> str:
    parent = node.parent
    while parent is not None:
        if parent.type == "rescue":
            return _rescued_kind(parent)
        parent = parent.parent
    return "RuntimeError"


def _protected_exception(handler: Node) -> str | None:
    if handler.parent is None:
        return None
    for child in handler.parent.named_children:
        if child.type == "rescue":
            break
        method = child.child_by_field_name("method")
        receiver = child.child_by_field_name("receiver")
        if (
            method is not None
            and node_text(method) in TERMINATING_METHODS
            and (
                receiver is None
                or is_self(receiver)
                or receiver_name(receiver) == "Kernel"
            )
        ):
            return _termination_kind(child, node_text(method))
        if (
            child.type not in {"comment", "nil", "true", "false", "integer", "float"}
            and literal(child) is None
        ):
            break
    return None


def _rescued_kind(handler: Node) -> str:
    if (kind := _protected_exception(handler)) is not None:
        return kind
    exceptions = handler.child_by_field_name("exceptions")
    if exceptions is None:
        return "StandardError"
    names = [
        receiver_name(child)
        for child in exceptions.named_children
        if child.type in {"constant", "scope_resolution"}
    ]
    return names[0] if len(names) == 1 else "Exception"


def _exit_kind(node: Node, name: str) -> str:
    arguments = _arguments(node)
    if any(
        argument.type in {"splat_argument", "hash_splat_argument", "forward_argument"}
        for argument in arguments
    ):
        return "SystemExit"
    if len(arguments) > 1:
        return "ArgumentError"
    if not arguments:
        return "SystemExit"
    invalid = {
        "nil",
        "array",
        "hash",
        "pair",
        "simple_symbol",
        "delimited_symbol",
        "regex",
        "range",
        "lambda",
        "object",
    }
    invalid.update(
        {"integer", "float", "true", "false"} if name == "abort" else {"string"}
    )
    known_class = arguments[0].type in {
        "constant",
        "scope_resolution",
    } and receiver_name(arguments[0]) in _CORE_NONEXCEPTIONS | _ERROR_PARENTS.keys() | {
        "Exception"
    }
    value_kind = _CORE_VALUES.get(receiver_name(arguments[0]), arguments[0].type)
    return "TypeError" if value_kind in invalid or known_class else "SystemExit"


def _ancestors(kind: str) -> set[str]:
    result = {kind, "Exception"}
    if kind.startswith("Errno::"):
        kind = "SystemCallError"
        result.add(kind)
    while kind in _ERROR_PARENTS:
        kind = _ERROR_PARENTS[kind]
        result.add(kind)
    return result


def _rescue_matches(node: Node, kind: str) -> bool:
    exceptions = node.child_by_field_name("exceptions")
    names = (
        {"StandardError"}
        if exceptions is None
        else {
            receiver_name(child)
            for child in exceptions.named_children
            if child.type in {"constant", "scope_resolution"}
        }
    )
    return bool(names & _ancestors(kind))


def handled_error(node: Node, kind: str) -> bool:
    child, parent = node, node.parent
    while parent is not None:
        if (
            parent.type == "rescue_modifier"
            and child == parent.child_by_field_name("body")
            and "StandardError" in _ancestors(kind)
        ):
            return True
        if parent.type in {"begin", "body_statement"} and child.type not in {
            "rescue",
            "ensure",
            "else",
        }:
            following = False
            for sibling in parent.named_children:
                if (
                    following
                    and sibling.type == "rescue"
                    and _rescue_matches(sibling, kind)
                ):
                    return True
                following = following or sibling == child
        child, parent = parent, parent.parent
    return False


_THROW_MAX_ARGS = 2


_FIXNUM_LIMIT = 1 << 62


def _integer_tag(node: Node) -> tuple[str, int] | None:
    raw = node_text(node).replace("_", "").replace(" ", "")
    sign = -1 if raw.startswith("-") else 1
    raw = raw.removeprefix("-").removeprefix("+").lower()
    bases = {"0x": 16, "0b": 2, "0o": 8, "0d": 10}
    if raw[:2] in bases:
        base = bases[raw[:2]]
        raw = raw[2:]
    else:
        base = 8 if raw.startswith("0") else 10
    try:
        value = sign * int(raw, base)
    except ValueError:
        return None
    return ("integer", value) if -_FIXNUM_LIMIT <= value < _FIXNUM_LIMIT else None


_FLONUM_EXCLUDED_BITS = 0x3000000000000000


def _float_tag(node: Node) -> tuple[str, int] | None:
    raw = node_text(node).replace("_", "").replace(" ", "")
    try:
        bits = struct.unpack(">Q", struct.pack(">d", float(raw)))[0]
    except ValueError:
        return None
    immediate = bits == 0 or ((bits >> 60) & 7) in {3, 4}
    return ("float", bits) if immediate and bits != _FLONUM_EXCLUDED_BITS else None


def _numeric_tag(node: Node) -> tuple[str, int] | None:
    operand = node.child_by_field_name("operand")
    floating = node.type == "float" or (operand is not None and operand.type == "float")
    return _float_tag(node) if floating else _integer_tag(node)


def _frozen_strings(node: Node) -> bool:
    while (parent := node.parent) is not None:
        node = parent
    lines = node_text(node).splitlines()
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    return bool(
        lines
        and lines[0].lstrip().startswith("#")
        and re.search(r"\bfrozen_string_literal\s*:\s*true\b", lines[0])
    )


def _tag_identity(node: Node) -> tuple[str, str | int | None] | None:
    while node.type == "parenthesized_statements" and len(node.named_children) == 1:
        node = node.named_children[0]
    if node.type in {"nil", "true", "false"}:
        return (node.type, None)
    if node.type == "string" and _frozen_strings(node):
        value = literal(node)
        return ("string", value) if value is not None else None
    if node.type in {"simple_symbol", "delimited_symbol"}:
        value = literal(node)
        return ("symbol", value) if value is not None else None
    if node.type in {"integer", "float"}:
        return _numeric_tag(node)
    if node.type == "unary":
        operand = node.child_by_field_name("operand")
        operator = node.child_by_field_name("operator")
        if (
            operand is not None
            and operand.type in {"integer", "float"}
            and operator is not None
            and operator.type in {"+", "-"}
        ):
            return _numeric_tag(node)
    return None


def _caught_throw(node: Node, catch_calls: set[int]) -> bool:
    arguments = _arguments(node)
    if not arguments or len(arguments) > _THROW_MAX_ARGS:
        return False
    tag = arguments[0]
    if _tag_identity(tag) is None and tag.type != "identifier":
        return False
    parent = node.parent
    while parent is not None:
        if parent.type in {"block", "do_block"} and parent.parent is not None:
            owner = parent.parent
            if _catch_matches(owner, tag, catch_calls):
                return True
        if parent.type == "block_argument" and parent.parent is not None:
            callback_owner = parent.parent.parent
            if callback_owner is not None and _catch_matches(
                callback_owner, tag, catch_calls
            ):
                return True
        parent = parent.parent
    return False


def record_catch(node: Node, disabled: set[str], catch_calls: set[int]) -> None:
    method = node.child_by_field_name("method")
    if method is None or node_text(method) != "catch":
        return
    receiver = node.child_by_field_name("receiver")
    key = (
        "catch"
        if receiver is None or is_self(receiver)
        else f"{receiver_name(receiver)}.catch"
    )
    if key in {"catch", "Kernel.catch"} - disabled:
        catch_calls.add(node.id)


def _catch_matches(owner: Node, tag: Node, catch_calls: set[int]) -> bool:
    if owner.id not in catch_calls:
        return False
    if tag.type == "identifier":
        return generated_tag_matches(owner, tag)
    tags = _arguments(owner)
    return len(tags) == 1 and _tag_identity(tags[0]) == _tag_identity(tag)


def _termination_kind(node: Node, name: str) -> str:
    if name in {"exit", "exit!", "abort"}:
        return _exit_kind(node, name)
    if name == "throw":
        count = len(_arguments(node))
        return "UncaughtThrowError" if 0 < count <= _THROW_MAX_ARGS else "ArgumentError"
    return _raised_kind(node)


def inactive_handler(node: Node, raised_scopes: dict[int, str | None]) -> bool:
    if node.parent is None or node.parent.id not in raised_scopes:
        return False
    if node.type == "else":
        return True
    kind = raised_scopes[node.parent.id]
    if node.type != "rescue" or kind is None:
        return False
    for handler in node.parent.named_children:
        if handler.type == "rescue" and _rescue_matches(handler, kind):
            return handler != node
    return True


def _record_scope_error(
    node: Node, kind: str | None, scopes: dict[int, str | None]
) -> None:
    if node.parent is not None and node.parent.type in {"begin", "body_statement"}:
        scopes.setdefault(node.parent.id, kind)


def handled_load_error(
    node: Node, error: str | None, kind: str | None, scopes: dict[int, str | None]
) -> str | None:
    if error is None or kind is None:
        return error
    _record_scope_error(node, kind, scopes)
    return None if handled_error(node, kind) else error


def _scope_exception_kind(node: Node, name: str, kind: str) -> str | None:
    if kind == "Exception":
        return None
    if name in {"raise", "fail"} and not _arguments(node):
        parent = node.parent
        while parent is not None:
            if parent.type == "rescue":
                return kind if _protected_exception(parent) is not None else None
            parent = parent.parent
    return kind


def load_raise_error(
    node: Node,
    disabled: set[str],
    bare_raises: set[int],
    catch_calls: set[int],
    raised_scopes: dict[int, str | None],
) -> str | None:
    method = node.child_by_field_name("method")
    name = (
        node_text(method)
        if method is not None
        else node_text(node)
        if node.id in bare_raises
        else ""
    )
    receiver = node.child_by_field_name("receiver")
    key = (
        name
        if receiver is None or is_self(receiver)
        else f"{receiver_name(receiver)}.{name}"
    )
    if name and f"undef:{key}" in disabled:
        return handled_load_error(
            node,
            "undefined terminating method during loading",
            "NoMethodError",
            raised_scopes,
        )
    canonical = termination_method(key, disabled)
    if canonical is None:
        return None
    name = canonical
    exit_call = name in {"exit", "exit!", "abort"}
    kind = _termination_kind(node, name)
    if name == "throw" and _caught_throw(node, catch_calls):
        return None
    _record_scope_error(node, _scope_exception_kind(node, name, kind), raised_scopes)
    if (name != "exit!" or kind != "SystemExit") and handled_error(node, kind):
        return None
    return (
        "unhandled process exit during loading"
        if exit_call
        else "uncaught raise during loading"
    )
