from __future__ import annotations

import codecs
import re

__all__ = ["decode_source"]

_MAGIC_ENCODING = re.compile(
    rb"^[ \t]*#.*?\b(?:coding|encoding|fileencoding)[ \t]*"
    rb"(?::|=(?=[ \t]))[ \t]*[\"']?([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_ENCODINGS = {
    "utf-8": "utf-8",
    "utf-8-mac": "utf-8",
    "ascii": "ascii",
    "us-ascii": "ascii",
    "ascii-8bit": "latin-1",
    "binary": "latin-1",
    "windows-31j": "cp932",
    "shift-jis": "shift_jis",
    "sjis": "shift_jis",
    "euc-jp": "euc_jp",
    "euc-kr": "euc_kr",
    "gb18030": "gb18030",
    "gbk": "gbk",
    "gb2312": "gb2312",
    "big5": "big5",
    "big5-hkscs": "big5hkscs",
    "koi8-r": "koi8_r",
    "koi8-u": "koi8_u",
}


class _UnsupportedEncodingError(ValueError):
    def __init__(self, encoding: str) -> None:
        super().__init__(f"unsupported Ruby source encoding: {encoding}")


def decode_source(source: bytes) -> tuple[str, str]:
    source = source.removeprefix(codecs.BOM_UTF8)
    lines = source.split(b"\n", 2)
    header = lines[1] if lines[0].startswith(b"#!") and len(lines) > 1 else lines[0]
    match = _MAGIC_ENCODING.search(header)
    if match is None:
        return source.decode("utf-8"), "utf-8"
    encoding = match[1].decode("ascii").lower().replace("_", "-")
    codec = _ENCODINGS.get(encoding)
    if codec is None and re.fullmatch(
        r"iso-?8859-[0-9]+|(?:windows-|cp)(?:125[0-8]|874|932|949|950)", encoding
    ):
        codec = encoding
    if codec is None:
        raise _UnsupportedEncodingError(encoding)
    return source.decode(codec), codec
