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
# Both run as `ant <name>`: extension-points are target-like entry points with
# a name, a depends list, and a description, but no task body of their own.
_RUNNABLE = frozenset({"target", "extension-point"})


def _has_doctype(text: str | bytes) -> bool:
    """Return whether *text* contains a ``<!DOCTYPE`` declaration.

    The declaration is the only carrier of inline ``<!ENTITY>`` definitions,
    whose expansion ElementTree performs without bounds (billion laughs), and
    of external entity references (which abort parsing and skip the file).
    Genuine Ant build files never need a DOCTYPE, so reject it up front and
    keep the stdlib parser without adding defusedxml.
    """
    if isinstance(text, bytes):
        return b"<!doctype" in text.lower()
    return "<!doctype" in text.lower()


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
    """Extract runnable entry names from ``build.xml`` without executing anything.

    Deliberately does NOT shell out to ``ant -p``: the file is declarative XML,
    so ``<target name=...>`` attributes can be read directly and a task body
    (a sequence of XML tasks, not shell commands) never runs during discovery.
    ``<extension-point name=...>`` entries are listed the same way: they are
    target-like states with a name and an optional description, each running
    as ``ant <name>``. Descriptions come from the ``description``
    attribute; every entry is listed, including ones without a description
    (which ``ant -p`` hides as internal). Names are kept verbatim — Ant permits
    spaces in target names and ``argv_base`` passes the name as a single token,
    so normalizing would change the target's identity. Leading-hyphen names
    are skipped: Ant parses them as options, so they cannot run via
    ``ant <name>`` (the conventional internal-target form). Task bodies stay
    opaque, so ``definition`` is empty. Imported/included build files,
    ``macrodef``-defined
    behaviour, ``if``/``unless`` conditions, ``depends`` ordering, and
    ``${property}`` interpolation are not resolved; names are left literal.
    Parser errors skip the file. Files with a ``<!DOCTYPE>`` declaration are
    skipped as well: it is the only carrier of ``<!ENTITY>`` definitions,
    whose expansion is unbounded, and genuine Ant build files never need one.
    Results are syntactic task candidates, not a
    promise that Ant will accept or successfully execute each target.

    Byte input is preferred: ElementTree honours the XML encoding declaration
    only for bytes; a `str` carrying one is skipped as unparsable.
    """
    if _has_doctype(text):
        log.warning(
            "nur: skipping %s (document type declaration not allowed)", source_file
        )
        return []
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, LookupError, ValueError, UnicodeError) as exc:
        log.warning("nur: skipping %s (%s)", source_file, exc)
        return []
    tasks: dict[str, Task] = {}
    # Only direct children of the project root: Ant only accepts <target> and
    # <extension-point> there, so nested elements of the same name (e.g. inside
    # <macrodef>) are not runnable entry points.
    for element in root:
        if _local(element.tag) not in _RUNNABLE:
            continue
        raw = element.get("name")
        if raw is None or not raw.strip():
            continue
        if raw.strip().startswith("-"):
            # Ant parses leading-hyphen arguments as options, so `-init` and
            # friends can never run as `ant <name>`; make skips `.`-prefixed
            # names for the same reason. The check uses the stripped name so
            # padded variants (`  -init `) are skipped too, while the kept
            # name stays verbatim.
            continue
        name = raw
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
