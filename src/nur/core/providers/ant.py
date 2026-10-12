"""Discover Apache Ant targets by parsing build.xml, without running Ant."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import replace
from typing import TYPE_CHECKING

from nur.core.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["AntProvider", "parse_build_xml"]

log = logging.getLogger("nur")
_SOURCE_FILE = "build.xml"


def _local(tag: str) -> str:
    """Return the namespace-free element name (``{ns}target`` -> ``target``)."""
    return tag.rsplit("}", 1)[-1]


def _description(element: ET.Element) -> str | None:
    value = element.get("description")
    if value is None:
        return None
    # XML attribute normalization already folds literal newlines to spaces, so
    # only `&#10;` character references still split lines; strip the first one.
    lines = value.splitlines()
    first = lines[0].strip() if lines else ""
    return first or None


def parse_build_xml(text: str | bytes, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Extract target names from ``build.xml`` content without executing anything.

    Deliberately does NOT shell out to ``ant -p``: the file is declarative XML,
    so ``<target name=...>`` attributes can be read directly and a task body
    (a sequence of XML tasks, not shell commands) never runs during discovery.
    Each target runs as ``ant <name>``. Descriptions come from the ``description``
    attribute; every target is listed, including ones without a description
    (which ``ant -p`` hides as internal). Task bodies stay opaque, so
    ``definition`` is empty. Imported/included build files, ``macrodef``-defined
    behaviour, ``if``/``unless`` conditions, ``depends`` ordering, and
    ``${property}`` interpolation are not resolved; names are left literal.
    Parser errors skip the file. Results are syntactic task candidates, not a
    promise that Ant will accept or successfully execute each target.

    Byte input is preferred: ElementTree honours the XML encoding declaration
    only for bytes; a `str` carrying one is skipped as unparsable.
    """
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, LookupError, ValueError, UnicodeError) as exc:
        log.warning("nur: skipping %s (%s)", source_file, exc)
        return []
    tasks: dict[str, Task] = {}
    for element in root.iter():
        if _local(element.tag) != "target":
            continue
        raw = element.get("name")
        # Ant accepts most characters in a name and `argv_base` passes it as a
        # single token, so only surrounding padding is stripped, never rejected.
        name = raw.strip() if raw is not None else ""
        if not name:
            continue
        declared = Task(
            name=name,
            prefix="ant",
            argv_base=("ant", name),
            description=_description(element),
            source_file=source_file,
        )
        existing = tasks.get(name)
        if existing is not None:
            if existing.description is None and declared.description is not None:
                tasks[name] = replace(existing, description=declared.description)
            continue
        tasks[name] = declared
    return list(tasks.values())


class AntProvider:
    prefix = "ant"

    def detect(self, cwd: Path) -> bool:
        return (cwd / _SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        # Bytes (not text): ElementTree honours the XML encoding declaration
        # only for byte input; decoding first would misread non-UTF-8 files
        # and reject declarations outright.
        try:
            source = (cwd / _SOURCE_FILE).read_bytes()
        except OSError as exc:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
            return []
        return parse_build_xml(source)
