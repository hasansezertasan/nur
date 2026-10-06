from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_overrides import TERMINATING_METHODS, receiver_name
from nur.core.providers._rake_syntax import is_self, literal, node_text

if TYPE_CHECKING:
    from tree_sitter import Node

__all__ = ["handled_error", "load_raise_error", "record_catch"]

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


def _raised_kind(node: Node) -> str:
    arguments = _arguments(node)
    if not arguments or arguments[0].type == "string":
        return "RuntimeError"
    first = arguments[0]
    if first.type in {"constant", "scope_resolution"}:
        return receiver_name(first)
    if (
        first.type == "call"
        and (method := first.child_by_field_name("method")) is not None
        and node_text(method) == "new"
    ):
        return receiver_name(first.child_by_field_name("receiver"))
    return (
        "TypeError"
        if first.type in {"nil", "true", "false", "integer", "float", "array", "hash"}
        else "Exception"
    )


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
    }
    invalid.update(
        {"integer", "float", "true", "false"} if name == "abort" else {"string"}
    )
    return "TypeError" if arguments[0].type in invalid else "SystemExit"


def _ancestors(kind: str) -> set[str]:
    result = {kind, "Exception"}
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


def _caught_throw(node: Node, catch_calls: set[int]) -> bool:
    arguments = _arguments(node)
    if not arguments or len(arguments) > _THROW_MAX_ARGS:
        return False
    tag = arguments[0]
    if tag.type not in {"simple_symbol", "delimited_symbol"}:
        return False
    parent = node.parent
    while parent is not None:
        if parent.type in {"block", "do_block"} and parent.parent is not None:
            owner = parent.parent
            if _catch_matches(owner, tag, catch_calls):
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
    tags = _arguments(owner)
    return (
        owner.id in catch_calls
        and len(tags) == 1
        and tags[0].type in {"simple_symbol", "delimited_symbol"}
        and literal(tags[0]) == literal(tag)
    )


def _termination_kind(node: Node, name: str) -> str:
    if name in {"exit", "exit!", "abort"}:
        return _exit_kind(node, name)
    if name == "throw":
        count = len(_arguments(node))
        return "UncaughtThrowError" if 0 < count <= _THROW_MAX_ARGS else "ArgumentError"
    return _raised_kind(node)


def load_raise_error(
    node: Node, disabled: set[str], bare_raises: set[int], catch_calls: set[int]
) -> str | None:
    method = node.child_by_field_name("method")
    name = (
        node_text(method)
        if method is not None
        else node_text(node)
        if node.id in bare_raises
        else ""
    )
    if name not in TERMINATING_METHODS:
        return None
    receiver = node.child_by_field_name("receiver")
    key = (
        name
        if receiver is None or is_self(receiver)
        else f"{receiver_name(receiver)}.{name}"
    )
    known = TERMINATING_METHODS | {f"Kernel.{method}" for method in TERMINATING_METHODS}
    if key not in known or key in disabled:
        return None
    exit_call = name in {"exit", "exit!", "abort"}
    kind = _termination_kind(node, name)
    if name == "throw" and _caught_throw(node, catch_calls):
        return None
    if (name != "exit!" or kind != "SystemExit") and handled_error(node, kind):
        return None
    return (
        "unhandled process exit during loading"
        if exit_call
        else "uncaught raise during loading"
    )
