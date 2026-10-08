import pytest

from nur.core.discovery import discover
from nur.core.providers.rake import RakeProvider, parse_rakefile


@pytest.mark.parametrize(
    "declaration",
    [
        "task :build",
        'task "build"',
        "task 'build'",
        'task :"build"',
        "task build: :test",
        "task :build => [:test, :lint]",
        'task "build" => "test"',
        "task({build: :test})",
        "task(:build)",
        "task((:build))",
        "task :build, [:mode] => :test",
        "task :build, computed_dependencies",
        "multitask :build => [:test, :lint]",
        "self.task :build",
        "(self).multitask :build",
    ],
)
def test_literal_task_candidates(declaration):
    tasks = parse_rakefile(declaration + ' do; sh "not executed"; end')
    assert len(tasks) == 1
    task = tasks[0]
    assert task.qualified_name == "rake:build"
    assert task.argv_base == ("rake", "build")
    assert task.run_argv(["--trace"]) == ["rake", "build", "--trace"]
    assert task.description is None
    assert task.definition == ""
    assert task.source_file == "Rakefile"
    assert not task.run_in_shell
    assert task.passthrough_prefix == ()


def test_nested_namespaces_and_opaque_task_bodies():
    source = (
        'desc "Run tests"; task :test { namespace :hidden { task :inside } }; '
        'namespace :db do; desc "Migrate"; task :migrate; '
        'self.namespace("schema") { self.task :dump }; task :reset; end; '
        "task :default => [:test]"
    )
    tasks = parse_rakefile(source)
    assert [task.name for task in tasks] == [
        "test",
        "db:migrate",
        "db:schema:dump",
        "db:reset",
        "default",
    ]
    assert [task.description for task in tasks] == [
        "Run tests",
        "Migrate",
        None,
        None,
        None,
    ]
    assert tasks[2].argv_base == ("rake", "db:schema:dump")


@pytest.mark.parametrize("wrapper", ["({body})", "begin; {body}; end"])
def test_transparent_wrappers_preserve_structural_scope(wrapper):
    source = wrapper.format(body='desc "Build"; task :build')
    assert [(task.name, task.description) for task in parse_rakefile(source)] == [
        ("build", "Build")
    ]


def test_comments_and_strings_are_not_declarations():
    source = (
        "# task :comment\n=begin\ntask :block_comment\n=end\n"
        'text = "task :string\\nnamespace :fake do"\n'
        "text = <<~RUBY\n task :heredoc\nRUBY\n"
        "text = %q{task :percent}\n"
        'desc "Hash # and end" # comment\n\n# another comment\ntask :real\n'
    )
    assert [(task.name, task.description) for task in parse_rakefile(source)] == [
        ("real", "Hash # and end")
    ]


@pytest.mark.parametrize(
    "wrapper",
    [
        "if true; task :hidden; end",
        "unless false; task :hidden; end",
        "task :hidden if enabled",
        "while true; task :hidden; end",
        "[1].each { task :hidden }",
        "def helper; task :hidden; end",
        "class Helper; task :hidden; end",
        "module Helper; task :hidden; end",
        "class << self; task :hidden; end",
        "BEGIN { task :hidden }",
        "END { task :hidden }",
        "namespace computed do; task :hidden; end",
        "namespace(()) { task :hidden }",
        'namespace "#{computed}" do; task :hidden; end',
        "namespace :db do |scope|; task :hidden; end",
        "namespace :db do; task :hidden; rescue; task :rescued; end",
        "namespace :db do; task :hidden; ensure; nil; end",
        "namespace(:db, &->(_) { task :hidden })",
        "file 'output' { task :hidden }",
        "rule '.o' { task :hidden }",
        "catch(:stop) { task :hidden }",
        "begin; task :hidden; rescue; nil; end",
        "other.namespace(:db) { task :hidden }",
    ],
)
def test_unsupported_scopes_are_opaque(wrapper):
    tasks = parse_rakefile(wrapper + "; namespace :db { task :real }")
    assert [task.name for task in tasks] == ["db:real"]


@pytest.mark.parametrize(
    "declaration",
    [
        "task name",
        'task "#{name}"',
        "task :build.to_s",
        'task "build" + suffix',
        "task *names",
        "task **options",
        "task",
        "task()",
        "task(())",
        "task({})",
        "task({build: :test, other: :lint})",
        "task build: :test, other: :lint",
        "task({**options})",
        "task computed => :test",
        'task ""',
        'task "--help"',
        'task "name=value"',
        'task "build[argument]"',
        'task "two words"',
        'task "bad\\nname"',
        'task "build\\x31"',
        'task :"build\\x31"',
        "task %q{build}",
        "task <<~NAME\nbuild\nNAME\n",
        'task "build:"',
        'task "rake:build"',
        "other.task :hidden",
        "(other).task :hidden",
        "().task(:hidden)",
        "(other; self).task(:hidden)",
    ],
)
def test_dynamic_ambiguous_and_unsafe_names_are_omitted(declaration):
    assert parse_rakefile(declaration) == []


@pytest.mark.parametrize(
    "barrier",
    [
        "nil",
        "file 'output'",
        "rule '.o'",
        "task computed",
        "if true; task :hidden; end",
        "namespace computed { task :hidden }",
        "def helper; end",
        "()",
        "begin; end",
    ],
)
def test_description_adjacency_is_cleared_by_other_statements(barrier):
    tasks = parse_rakefile(f'desc "Hidden"; {barrier}; task :shown')
    assert [task.name for task in tasks] == ["shown"]
    assert tasks[0].description is None


def test_descriptions_do_not_cross_namespace_boundaries():
    tasks = parse_rakefile(
        'desc "Outer"; namespace :db { task :inside; desc "Unused" }; task :root'
    )
    assert [(task.name, task.description) for task in tasks] == [
        ("db:inside", None),
        ("root", None),
    ]


@pytest.mark.parametrize(
    "description",
    ["computed", ":Build", '"#{compute()}"', '"escaped\\n"', '""', '" "', '"x", "y"'],
)
def test_nonliteral_or_empty_descriptions_are_optional(description):
    tasks = parse_rakefile(f"desc {description}; task :build")
    assert [task.name for task in tasks] == ["build"]
    assert tasks[0].description is None


def test_first_description_line_and_duplicate_candidates():
    tasks = parse_rakefile(
        'desc "First"; task :build; desc " Second\nDetails "; task :build; '
        "task :build; namespace :db { task :build }"
    )
    assert [(task.name, task.description) for task in tasks] == [
        ("build", "Second"),
        ("db:build", None),
    ]


@pytest.mark.parametrize(
    "ruby",
    [
        'raise "boom"',
        "exit 0",
        "catch(:x)",
        "return",
        "undef task",
        "def self.task(*); end",
        "$? = nil",
        "while true; []; end",
        "begin; raise foo: 1; rescue TypeError; end",
        'begin; raise "handled"; rescue Object; end',
        "class Helper; def self.task(...); end; task; end",
        'if ENV["NO_RAISE"]; def self.raise(*); end; end; raise "boom"',
        "class Helper; extend Rake::DSL, 1; task :inside; end",
        "value = /#{pattern}/",
    ],
)
def test_runtime_behavior_is_outside_candidate_discovery_contract(ruby):
    tasks = parse_rakefile(ruby + "; task :candidate")
    assert [task.name for task in tasks] == ["candidate"]


def test_reserved_namespace_and_qualified_lookup_prefix():
    assert parse_rakefile("namespace :rake { task :hidden }") == []
    task = parse_rakefile('namespace :db { task "rake:build" }')[0]
    assert task.argv_base == ("rake", "db:rake:build")


def test_empty_source_unicode_and_custom_source_label():
    assert parse_rakefile("") == []
    tasks = parse_rakefile('desc "Construire le café"; task "café"', "custom.rb")
    assert tasks[0].name == "café"
    assert tasks[0].description == "Construire le café"
    assert tasks[0].source_file == "custom.rb"


def test_detection_and_discovery_never_execute(tmp_path, monkeypatch):
    provider = RakeProvider()
    assert not provider.detect(tmp_path)
    marker = tmp_path / "executed"
    (tmp_path / "Rakefile").write_text(
        f'File.write({str(marker)!r}, "bad"); require "missing"; import "other.rake"; '
        'namespace :db { task :migrate { raise "not executed" } }',
        encoding="utf-8",
    )
    (tmp_path / "other.rake").write_text("task :imported")
    (tmp_path / "rakelib").mkdir()
    (tmp_path / "rakelib" / "hidden.rake").write_text("task :hidden")

    def forbid_process(*args, **kwargs):
        pytest.fail("discovery must never launch a process")

    monkeypatch.setattr("subprocess.Popen", forbid_process)
    assert provider.detect(tmp_path)
    assert [task.name for task in provider.discover(tmp_path)] == ["db:migrate"]
    assert discover(tmp_path).resolve("rake:db:migrate").argv_base == (
        "rake",
        "db:migrate",
    )
    assert not marker.exists()


@pytest.mark.parametrize(
    "contents",
    [
        b"task :broken do\n",
        b"\xff",
        b'task "unterminated\n',
        b"# encoding: unsupported\ntask :build",
    ],
)
def test_unparsable_or_unreadable_sources_are_skipped(tmp_path, caplog, contents):
    (tmp_path / "Rakefile").write_bytes(contents)
    assert RakeProvider().discover(tmp_path) == []
    assert "skipping Rakefile" in caplog.text


def test_missing_source_and_current_directory_only(tmp_path, caplog):
    provider = RakeProvider()
    assert provider.discover(tmp_path) == []
    assert "skipping Rakefile" in caplog.text
    (tmp_path / "Rakefile").write_text("task :build")
    child = tmp_path / "child"
    child.mkdir()
    assert not provider.detect(child)
    (child / "Rakefile").mkdir()
    assert not provider.detect(child)


@pytest.mark.parametrize(
    "directive", ["encoding: ISO-8859-1", "coding=iso-8859-1", "coding: ISO-8859-1"]
)
def test_source_encoding_preserves_metadata_and_safe_names(tmp_path, directive):
    source = f'# {directive}\ndesc "Café"; task :ascii; task "café"'
    (tmp_path / "Rakefile").write_bytes(source.encode("latin-1"))
    tasks = RakeProvider().discover(tmp_path)
    assert [(task.name, task.description) for task in tasks] == [("ascii", "Café")]


@pytest.mark.parametrize(
    "header",
    [b"\xef\xbb\xbf", b"# encoding: utf8\n", b"#!/usr/bin/ruby\n# coding: UTF-8\n"],
)
def test_utf8_source_headers_keep_unicode_candidates(tmp_path, header):
    (tmp_path / "Rakefile").write_bytes(header + 'task "café"'.encode())
    assert [task.name for task in RakeProvider().discover(tmp_path)] == ["café"]


def test_non_ruby_encoding_directive_is_not_used(tmp_path):
    (tmp_path / "Rakefile").write_bytes(
        b"# fileencoding: ISO-8859-1\n# \xe9\ntask :build"
    )
    assert RakeProvider().discover(tmp_path) == []


def test_utf8_mac_source_keeps_only_portable_ascii_names(tmp_path):
    source = '# encoding: UTF-8-MAC\ntask :ascii; task "café"'
    (tmp_path / "Rakefile").write_bytes(source.encode())
    assert [task.name for task in RakeProvider().discover(tmp_path)] == ["ascii"]
