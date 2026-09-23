from __future__ import annotations

import os
import re
import shlex

__all__ = ["quote", "quote_for_cmd"]


_CMD_PLAIN = re.compile(r'[^\s"&|<>^()%!,;=]+')


def _double_quote(argument: str) -> str:
    escaped = re.sub(r'(\\*)"', r'\1\1\\"', argument)
    escaped = re.sub(r"(\\+)$", r"\1\1", escaped)
    return f'"{escaped}"'


def quote_for_cmd(argument: str) -> str:
    """Quote one argument for cmd.exe and the MSVC argv parser, only if needed.

    Plain tokens stay bare, since cmd.exe built-ins such as ``echo`` print
    quotes verbatim. Anything with whitespace, quotes, or cmd.exe operators
    such as ``&`` or ``>`` is double-quoted so it stays one literal token.
    cmd.exe expands ``%NAME%`` even inside quotes, so each ``%`` is emitted as
    ``^%`` between quoted pieces: the caret keeps it literal, and the quote
    characters around it stop the text from naming a real variable.
    """
    if _CMD_PLAIN.fullmatch(argument):
        return argument
    return "^%".join(map(_double_quote, argument.split("%")))


# Quote for the shell that runs shell tasks: cmd.exe, or a POSIX $SHELL.
quote = quote_for_cmd if os.name == "nt" else shlex.quote
