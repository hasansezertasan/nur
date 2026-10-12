from __future__ import annotations

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
    assert (
        parse_build_xml(
            "<project><target description='no name'/><target name='  '/></project>"
        )
        == []
    )


def test_parse_build_xml_strips_padded_names() -> None:
    tasks = {
        t.name: t
        for t in parse_build_xml("<project><target name='  clean  '/></project>")
    }
    assert list(tasks) == ["clean"]
    assert tasks["clean"].argv_base == ("ant", "clean")


def test_parse_build_xml_deduplicates_padded_variants() -> None:
    tasks = {
        t.name: t
        for t in parse_build_xml(
            "<project><target name='a'/><target name=' a '/></project>"
        )
    }
    assert list(tasks) == ["a"]


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


def test_parse_build_xml_supports_namespaced_targets() -> None:
    tasks = parse_build_xml(
        '<project xmlns:ant="urn:ant"><ant:target name="x"/></project>'
    )
    assert [t.name for t in tasks] == ["x"]


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
