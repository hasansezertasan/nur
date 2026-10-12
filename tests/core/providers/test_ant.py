from __future__ import annotations

import codecs

from nur.core.discovery import discover
from nur.core.providers.ant import AntProvider, parse_build_xml

_BUILD_XML = """\
<project name="demo" default="build" basedir=".">
  <target name="clean" description="remove build artifacts">
    <delete dir="build"/>
  </target>
  <target name="compile" depends="clean" description="compile sources">
    <javac srcdir="src" destdir="build"/>
  </target>
  <target name="build" depends="compile" description="package the jar">
    <jar destfile="app.jar" basedir="build"/>
  </target>
</project>
"""


def test_parse_build_xml_extracts_target_names_in_order() -> None:
    assert [t.name for t in parse_build_xml(_BUILD_XML)] == [
        "clean",
        "compile",
        "build",
    ]


def test_parse_build_xml_sets_argv_prefix_and_source_file() -> None:
    tasks = {t.name: t for t in parse_build_xml(_BUILD_XML)}
    assert tasks["compile"].argv_base == ("ant", "compile")
    assert tasks["compile"].prefix == "ant"
    assert tasks["compile"].qualified_name == "ant:compile"
    assert tasks["compile"].run_argv() == ["ant", "compile"]
    assert tasks["compile"].source_file == "build.xml"
    assert tasks["compile"].definition == ""


def test_parse_build_xml_reads_description_attribute() -> None:
    tasks = {t.name: t for t in parse_build_xml(_BUILD_XML)}
    assert tasks["clean"].description == "remove build artifacts"


def test_parse_build_xml_missing_description_is_none() -> None:
    tasks = {
        t.name: t for t in parse_build_xml("<project><target name='a'/></project>")
    }
    assert tasks["a"].description is None


def test_parse_build_xml_blank_description_is_none() -> None:
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='a' description='  '/></project>"
        )
    }
    assert tasks["a"].description is None


def test_parse_build_xml_lists_undescribed_targets() -> None:
    # Unlike `ant -p`, internal (description-less) targets stay discoverable.
    names = {
        t.name
        for t in parse_build_xml(
            "<project><target name='a'/><target name='b' description='B'/></project>"
        )
    }
    assert names == {"a", "b"}


def test_parse_build_xml_default_target_is_a_regular_task() -> None:
    tasks = {t.name: t for t in parse_build_xml(_BUILD_XML)}
    assert tasks["build"].argv_base == ("ant", "build")


def test_parse_build_xml_ignores_runtime_attributes() -> None:
    # `depends` ordering and `if`/`unless` conditions are runtime concerns;
    # the target is still statically named and runnable.
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='cond' depends='clean' if='flag' unless='skip'/>"
            "</project>"
        )
    }
    assert list(tasks) == ["cond"]
    assert tasks["cond"].definition == ""


def test_parse_build_xml_leaves_property_references_literal() -> None:
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='${target.name}' description='${target.desc}'/>"
            "</project>"
        )
    }
    assert tasks["${target.name}"].description == "${target.desc}"


def test_parse_build_xml_skips_targets_without_usable_name() -> None:
    # Ant rejects a missing or actually empty name; anything else is kept.
    assert (
        parse_build_xml(
            "<project><target description='no name'/><target name=''/></project>"
        )
        == []
    )


def test_parse_build_xml_keeps_whitespace_only_names() -> None:
    # Ant only rejects an actually empty name, so a whitespace-only name is a
    # runnable entry and stays listed verbatim as a single argv token.
    tasks = {
        t.name: t for t in parse_build_xml("<project><target name='  '/></project>")
    }
    assert list(tasks) == ["  "]
    assert tasks["  "].argv_base == ("ant", "  ")


def test_parse_build_xml_preserves_padded_names() -> None:
    # Ant permits spaces in target names and `argv_base` passes the name as a
    # single token, so the exact XML name is kept, never normalized.
    tasks = {
        t.name: t
        for t in parse_build_xml("<project><target name='  clean  '/></project>")
    }
    assert list(tasks) == ["  clean  "]
    assert tasks["  clean  "].argv_base == ("ant", "  clean  ")


def test_parse_build_xml_treats_padded_variants_as_distinct() -> None:
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='a'/><target name=' a '/></project>"
        )
    }
    assert list(tasks) == ["a", " a "]


def test_parse_build_xml_skips_hyphen_prefixed_targets() -> None:
    # Ant parses leading-hyphen arguments as options, so `-init` can never run
    # as `ant -init` (the conventional internal-target form).
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='-init'/><target name='-'/>"
            "<target name='build'/></project>"
        )
    }
    assert list(tasks) == ["build"]


def test_parse_build_xml_lists_extension_points_like_targets() -> None:
    # Extension-points are target-like states runnable as `ant <name>`.
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='build'/>"
            "<extension-point name='ready' description='ready to build'/>"
            "<extension-point name='-hidden'/>"
            "<extension-point name='plain'/></project>"
        )
    }
    assert [t.name for t in tasks.values()] == ["build", "ready", "plain"]
    assert tasks["ready"].argv_base == ("ant", "ready")
    assert tasks["ready"].prefix == "ant"
    assert tasks["ready"].description == "ready to build"
    assert tasks["ready"].source_file == "build.xml"


def test_parse_build_xml_deduplicates_keeping_first_description() -> None:
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='a' description='First'/>"
            "<target name='a' description='Second'/></project>"
        )
    }
    assert list(tasks) == ["a"]
    assert tasks["a"].description == "First"


def test_parse_build_xml_later_description_fills_missing() -> None:
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='a'/><target name='a' description='Late'/></project>"
        )
    }
    assert tasks["a"].description == "Late"


def test_parse_build_xml_ignores_commented_out_targets() -> None:
    tasks = parse_build_xml(
        "<project><!-- <target name='hidden'/> --><target name='real'/></project>"
    )
    assert [t.name for t in tasks] == ["real"]


def test_parse_build_xml_character_reference_newline_in_description() -> None:
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='a' description='First   &#10;Second'/></project>"
        )
    }
    assert tasks["a"].description == "First"


def test_parse_build_xml_unescapes_entities() -> None:
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='a&amp;b' description='x &lt;y&gt;'/></project>"
        )
    }
    assert tasks["a&b"].description == "x <y>"


def test_parse_build_xml_supports_core_namespaced_targets() -> None:
    tasks = parse_build_xml(
        '<project xmlns:ant="antlib:org.apache.tools.ant">'
        '<ant:target name="x"/></project>'
    )
    assert [t.name for t in tasks] == ["x"]


def test_parse_build_xml_ignores_foreign_namespaced_targets() -> None:
    # Other namespaces are ordinary task/type uses, not Ant entry points.
    assert (
        parse_build_xml(
            '<project xmlns:plugin="urn:plugin">'
            '<plugin:target name="x"/>'
            '<plugin:extension-point name="y"/></project>'
        )
        == []
    )


def test_parse_build_xml_ignores_macrodef_bodies() -> None:
    # `macrodef` defines a reusable task type, not a runnable target.
    assert (
        parse_build_xml(
            "<project><macrodef name='helper'><sequential>"
            "<echo message='hi'/></sequential></macrodef></project>"
        )
        == []
    )


def test_parse_build_xml_honours_source_file() -> None:
    tasks = parse_build_xml(_BUILD_XML, source_file="other.xml")
    assert tasks
    assert all(t.source_file == "other.xml" for t in tasks)


def test_parse_build_xml_accepts_bytes_preserving_declared_encoding() -> None:
    source = (
        b'<?xml version="1.0" encoding="ISO-8859-1"?>'
        b"<project><target name='gr\xfc\xdfe'/></project>"
    )
    assert [t.name for t in parse_build_xml(source)] == ["grüße"]


def test_parse_build_xml_transcodes_shift_jis() -> None:
    # pyexpat rejects multibyte encodings outright, but Ant's JAXP reader
    # accepts them, so transcode to UTF-8 instead of skipping the file.
    source = (
        '<?xml version="1.0" encoding="Shift_JIS"?>'
        "<project><target name='build' description='日本語'/></project>"
    ).encode("shift_jis")
    tasks = {t.name: t for t in parse_build_xml(source)}
    assert tasks["build"].description == "日本語"


def test_parse_build_xml_transcoded_shift_jis_still_rejects_doctype(caplog) -> None:
    source = (
        '<?xml version="1.0" encoding="Shift_JIS"?>'
        '<!DOCTYPE project [<!ENTITY x "value">]>'
        "<project><target name='a' description='&x;'/></project>"
    ).encode("shift_jis")
    assert parse_build_xml(source) == []
    assert any("build.xml" in r.message for r in caplog.records)


def test_parse_build_xml_decodes_utf32() -> None:
    body = (
        '<?xml version="1.0" encoding="UTF-32"?>'
        "<project><target name='build'/></project>"
    )
    variants = {
        "utf-32-le BOM": codecs.BOM_UTF32_LE + body.encode("utf-32-le"),
        "utf-32-be BOM": codecs.BOM_UTF32_BE + body.encode("utf-32-be"),
        "utf-32-le": body.encode("utf-32-le"),
        "utf-32-be": body.encode("utf-32-be"),
    }
    for label, source in variants.items():
        assert [t.name for t in parse_build_xml(source)] == ["build"], label


def test_parse_build_xml_decodes_ebcdic_cp037() -> None:
    source = (
        '<?xml version="1.0" encoding="CP037"?>'
        "<project><target name='build' description='hello'/></project>"
    ).encode("cp037")
    tasks = {t.name: t for t in parse_build_xml(source)}
    assert tasks["build"].description == "hello"


def test_parse_build_xml_malformed_returns_empty(caplog) -> None:
    assert parse_build_xml("<project><target") == []
    assert any("build.xml" in r.message for r in caplog.records)


def test_parse_build_xml_unknown_encoding_returns_empty(caplog) -> None:
    source = (
        b'<?xml version="1.0" encoding="unknown-enc"?>'
        b"<project><target name='a'/></project>"
    )
    assert parse_build_xml(source) == []
    assert any("build.xml" in r.message for r in caplog.records)


def test_parse_build_xml_empty_returns_empty() -> None:
    assert parse_build_xml("") == []


def test_parse_build_xml_non_xml_returns_empty(caplog) -> None:
    assert parse_build_xml("just some text") == []
    assert any("build.xml" in r.message for r in caplog.records)


def test_detect(tmp_path) -> None:
    (tmp_path / "build.xml").write_text(_BUILD_XML)
    assert AntProvider().detect(tmp_path)
    assert not AntProvider().detect(tmp_path / "nope")
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "build.xml").mkdir()
    assert not AntProvider().detect(tmp_path / "dir")


def test_discover_parses_file(tmp_path) -> None:
    (tmp_path / "build.xml").write_text(_BUILD_XML)
    tasks = {t.name: t for t in AntProvider().discover(tmp_path)}
    assert tasks["clean"].description == "remove build artifacts"
    assert tasks["build"].argv_base == ("ant", "build")
    assert tasks["build"].source_file == "build.xml"


def test_discover_includes_ant_tasks(tmp_path) -> None:
    (tmp_path / "build.xml").write_text(_BUILD_XML)
    registry = discover(tmp_path)
    assert "ant:build" in {t.qualified_name for t in registry.all()}
    assert registry.resolve("ant:build").argv_base == ("ant", "build")


def test_discover_missing_file_returns_empty(tmp_path) -> None:
    assert AntProvider().discover(tmp_path) == []


def test_discover_malformed_file_returns_empty(tmp_path, caplog) -> None:
    (tmp_path / "build.xml").write_text("<project><target")
    assert AntProvider().discover(tmp_path) == []
    assert any("build.xml" in r.message for r in caplog.records)


def test_discover_undecodable_file_returns_empty(tmp_path, caplog) -> None:
    (tmp_path / "build.xml").write_bytes(b"<project><target name='\xff'/></project>")
    assert AntProvider().discover(tmp_path) == []
    assert any("skipping build.xml" in r.message for r in caplog.records)


def test_discover_unreadable_file_returns_empty(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "build.xml").write_text(_BUILD_XML)

    def boom(*_a: object, **_k: object) -> bytes:
        msg = "nope"
        raise OSError(msg)

    monkeypatch.setattr("pathlib.Path.read_bytes", boom)
    assert AntProvider().discover(tmp_path) == []
    assert any("skipping build.xml" in r.message for r in caplog.records)


def test_parse_build_xml_skips_doctype_declaration(caplog) -> None:
    source = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE project [<!ENTITY greeting "hello">]>'
        '<project><target name="a" description="&greeting;"/></project>'
    )
    assert parse_build_xml(source) == []
    assert any("build.xml" in r.message for r in caplog.records)


def test_parse_build_xml_skips_doctype_bytes(caplog) -> None:
    source = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE project [<!ENTITY a "x">]>'
        b"<project><target name='a'/></project>"
    )
    assert parse_build_xml(source) == []
    assert any("build.xml" in r.message for r in caplog.records)


def test_parse_build_xml_ignores_doctype_text_in_comment() -> None:
    tasks = parse_build_xml(
        "<project><!-- <!DOCTYPE html> --><target name='real'/></project>"
    )
    assert [t.name for t in tasks] == ["real"]


def test_parse_build_xml_ignores_doctype_text_in_cdata() -> None:
    tasks = parse_build_xml(
        "<project><target name='real'>"
        "<echo><![CDATA[<!DOCTYPE html>]]></echo>"
        "</target></project>"
    )
    assert [t.name for t in tasks] == ["real"]


def test_parse_build_xml_ignores_doctype_text_in_pi() -> None:
    tasks = parse_build_xml(
        "<?meta <!DOCTYPE html>?><project><target name='real'/></project>"
    )
    assert [t.name for t in tasks] == ["real"]


def test_parse_build_xml_detects_doctype_after_tricky_comment(caplog) -> None:
    # The comment holds a PI opener and a later real PI supplies the first
    # `?>`; a regex sanitizer could erase the genuine declaration along with
    # the pseudo-instruction, but the prolog walk still sees it.
    source = (
        "<!-- <?fake -->"
        '<!DOCTYPE project [<!ENTITY x "value">]>'
        "<?real?>"
        "<project><target name='a' description='&x;'/></project>"
    )
    assert parse_build_xml(source) == []
    assert any("build.xml" in r.message for r in caplog.records)


def test_parse_build_xml_skips_utf16_doctype(caplog) -> None:
    source = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE project [<!ENTITY a "x">]>'
        "<project><target name='a'/></project>"
    ).encode("utf-16")
    assert parse_build_xml(source) == []
    assert any("build.xml" in r.message for r in caplog.records)


def test_parse_build_xml_keeps_padded_hyphen_prefixed_targets() -> None:
    # The argv token is passed verbatim and starts with a space, so Ant does
    # not parse it as an option; only position-zero hyphens are skipped.
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='  -init '/><target name='build'/></project>"
        )
    }
    assert list(tasks) == ["  -init ", "build"]
    assert tasks["  -init "].argv_base == ("ant", "  -init ")


def test_parse_build_xml_ignores_nested_targets() -> None:
    tasks = parse_build_xml(
        "<project><macrodef name='helper'><target name='nested'/>"
        "</macrodef><target name='real'/></project>"
    )
    assert [t.name for t in tasks] == ["real"]


def test_parse_build_xml_skips_non_project_root(caplog) -> None:
    assert parse_build_xml("<config><target name='deploy'/></config>") == []
    assert any("build.xml" in r.message for r in caplog.records)


def test_parse_build_xml_skips_foreign_namespaced_root() -> None:
    assert (
        parse_build_xml(
            '<plugin:project xmlns:plugin="urn:plugin">'
            '<target name="deploy"/></plugin:project>'
        )
        == []
    )


def test_parse_build_xml_accepts_core_namespaced_root() -> None:
    tasks = parse_build_xml(
        '<ant:project xmlns:ant="antlib:org.apache.tools.ant">'
        '<ant:target name="x"/></ant:project>'
    )
    assert [t.name for t in tasks] == ["x"]


def test_parse_build_xml_reads_core_namespaced_attributes() -> None:
    tasks = {
        t.name: t
        for t in parse_build_xml(
            '<project xmlns:ant="antlib:org.apache.tools.ant">'
            '<ant:target ant:name="build" ant:description="Build"/>'
            "</project>"
        )
    }
    assert list(tasks) == ["build"]
    assert tasks["build"].description == "Build"
    assert tasks["build"].argv_base == ("ant", "build")


def test_parse_build_xml_ignores_core_qualified_attrs_on_plain_target() -> None:
    # Ant ignores attributes whose namespace differs from the element's own
    # (empty) namespace, leaving the target nameless, so nothing is listed.
    assert (
        parse_build_xml(
            '<project xmlns:ant="antlib:org.apache.tools.ant">'
            '<target ant:name="build"/></project>'
        )
        == []
    )


def test_parse_build_xml_later_duplicate_attr_wins() -> None:
    # Like Ant, which assigns while iterating attributes, the later of the
    # accepted duplicate forms wins.
    first = {
        t.name: t
        for t in parse_build_xml(
            '<project xmlns:ant="antlib:org.apache.tools.ant">'
            '<ant:target name="old" ant:name="new"/>'
            "</project>"
        )
    }
    assert list(first) == ["new"]
    second = {
        t.name: t
        for t in parse_build_xml(
            '<project xmlns:ant="antlib:org.apache.tools.ant">'
            '<ant:target ant:name="new" name="old"/>'
            "</project>"
        )
    }
    assert list(second) == ["old"]
