"""Discover Apache Ant targets by parsing build.xml, without running Ant."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
import xml.parsers.expat
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
# Ant installs <target>/<extension-point> only from the empty namespace or its
# core antlib URI (ProjectHelper.ANT_CORE_URI); elements of the same local
# name in any other namespace are ordinary task/type uses, not entry points.
_ANT_CORE_URI = "antlib:org.apache.tools.ant"


class _DoctypeFoundError(Exception):
    """Control flow: the pre-scan spotted a document type declaration."""


def _reject_doctype(*_args: object) -> None:
    raise _DoctypeFoundError


def _has_doctype(text: str | bytes) -> bool:
    """Return whether *text* carries a ``<!DOCTYPE`` declaration.

    The declaration is the only carrier of inline ``<!ENTITY>`` definitions,
    whose expansion ElementTree performs without bounds (billion laughs), and
    of external entity references. Genuine Ant build files never need a
    DOCTYPE, so reject it up front and keep the stdlib parser without adding
    defusedxml.

    Detection itself runs on expat with a handler that aborts the parse the
    moment a declaration starts, so no entity is ever expanded and encodings,
    comments, processing instructions, and CDATA sections are all handled by
    a real parser rather than pattern matching. Anything expat rejects is
    left for the ElementTree parse below, which skips the file the same way.
    """
    parser = xml.parsers.expat.ParserCreate()
    parser.StartDoctypeDeclHandler = _reject_doctype
    try:
        # pyexpat's Parse takes no keyword arguments, hence the positional flag.
        parser.Parse(text, True)  # noqa: FBT003
    except _DoctypeFoundError:
        return True
    except xml.parsers.expat.ExpatError, LookupError, ValueError, UnicodeError:
        # Unparseable input (unknown encoding, malformed XML, ...): no
        # declaration was seen; the ElementTree parse below skips the file.
        return False
    return False


def _runnable(tag: str) -> bool:
    """Whether *tag* is an Ant core target-like element.

    ElementTree reports namespaced tags as ``{uri}local``; only the empty
    namespace and Ant's core URI count (``<plugin:target>`` is a task use).
    """
    if tag.startswith("{"):
        uri, _, local = tag[1:].partition("}")
        return local in _RUNNABLE and uri == _ANT_CORE_URI
    return tag in _RUNNABLE


def _is_project_root(tag: str) -> bool:
    """Whether *tag* is an Ant ``<project>`` root element.

    Ant's root handler only accepts ``project`` in the empty namespace or its
    core URI and rejects anything else before installing any target.
    """
    if tag.startswith("{"):
        uri, _, local = tag[1:].partition("}")
        return local == "project" and uri == _ANT_CORE_URI
    return tag == "project"


def _attr(element: ET.Element, local: str) -> str | None:
    """Return the attribute with local name *local*, Ant-style.

    Ant interprets attributes by local name when unqualified or qualified with
    the element's own namespace, ignores other namespaces, and assigns while
    iterating so the later duplicate wins. ElementTree keys a namespaced
    attribute as ``{uri}local`` but preserves document order in
    ``element.attrib``, so the last accepted form is returned.
    """
    core = f"{{{_ANT_CORE_URI}}}{local}"
    namespaced = element.tag.startswith(f"{{{_ANT_CORE_URI}}}")
    value: str | None = None
    for key, val in element.attrib.items():
        if key == local or (namespaced and key == core):
            value = val
    return value


def _description(element: ET.Element) -> str | None:
    value = _attr(element, "description")
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
    so normalizing would change the target's identity. Only a missing name or
    an actually empty one is skipped (Ant rejects the latter); anything else,
    including whitespace-only names, stays listed exactly as Ant accepts it.
    Names beginning with a
    hyphen are skipped: Ant parses such arguments as options, so they cannot
    run via ``ant <name>`` (the conventional internal-target form); the check
    mirrors Ant's own ``startsWith("-")`` on the same verbatim token, so
    padded variants (``  -init ``) stay listed. Only core-namespace
    ``<target>``/``<extension-point>`` elements count: other namespaces are
    ordinary task/type uses, not entry points. Documents whose root is not an
    Ant ``<project>`` element are skipped: Ant rejects them before installing
    any target. Task bodies stay
    opaque, so ``definition`` is empty. Imported/included build files,
    ``macrodef``-defined
    behaviour, ``if``/``unless`` conditions, ``depends`` ordering, and
    ``${property}`` interpolation are not resolved; names are left literal.
    Parser errors skip the file. Files with a ``<!DOCTYPE>`` declaration are
    skipped as well: it is the only carrier of ``<!ENTITY>`` definitions,
    whose expansion is unbounded, and genuine Ant build files never need one.
    Detection is an expat pre-scan that aborts at the first declaration, so no
    entity is ever expanded during discovery.
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
    if not _is_project_root(root.tag):
        log.warning("nur: skipping %s (not an Ant project file)", source_file)
        return []
    tasks: dict[str, Task] = {}
    # Only direct children of the project root: Ant only accepts <target> and
    # <extension-point> there, so nested elements of the same name (e.g. inside
    # <macrodef>) are not runnable entry points.
    for element in root:
        if not _runnable(element.tag):
            continue
        raw = _attr(element, "name")
        if not raw:
            continue
        if raw.startswith("-"):
            # Ant checks the original command-line token with startsWith("-"),
            # so only position-zero hyphens read as options; padded variants
            # (`  -init `) stay runnable as single-token target names, exactly
            # as Ant itself treats them. Make skips `.`-prefixed names for the
            # same reason.
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
