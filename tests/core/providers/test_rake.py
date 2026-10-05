import pytest

from nur.core.discovery import discover
from nur.core.providers.rake import RakeProvider, parse_rakefile


@pytest.mark.parametrize(
    "declaration",
    [
        "task :build",
        'task "build"',
        "task 'build'",
        "task build: :test",
        "task :build => [:test, :lint]",
        'task "build" => "test"',
        "task(:build)",
        "task(:build, [:mode] => :test)",
        "task :build, [:mode]",
        "multitask :build => [:test, :lint]",
        'task :"build"',
    ],
)
def test_literal_task_forms(declaration):
    tasks = parse_rakefile(f'{declaration} do\n sh "do not execute"\nend\n')
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
    tasks = parse_rakefile(
        'desc "Run tests"\ntask :test do\n'
        " namespace :hidden do\n  task :inside\n end\n"
        ' if true\n  puts "end"\n end\nend\n'
        'namespace :db do\n desc "Migrate"\n task :migrate do\n'
        "  [1, 2].each do |n|\n   puts n\n  end\n end\n"
        ' namespace "schema" do\n  task :dump\n end\n'
        " task :reset\nend\ntask :default => [:test]\n"
    )
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


def test_braces_parentheses_and_multiple_statements():
    tasks = parse_rakefile(
        'namespace("db") { desc("Migrate"); task(:migrate) { puts "hello" } }; '
        "task :root\n"
    )
    assert [(task.name, task.description) for task in tasks] == [
        ("db:migrate", "Migrate"),
        ("root", None),
    ]


def test_comments_and_strings_are_not_declarations():
    tasks = parse_rakefile(
        "# task :comment\n=begin\ntask :block_comment\n=end\n"
        'text = "task :string\\nnamespace :fake do"\n'
        "text = <<~RUBY\n task :heredoc\n namespace :fake do\nRUBY\n"
        "text = %q{task :percent}\n"
        'desc "Hash # and end" # comment\n\n# another comment\ntask :real\n'
    )
    assert [(task.name, task.description) for task in tasks] == [
        ("real", "Hash # and end")
    ]


@pytest.mark.parametrize(
    "wrapper",
    [
        "namespace computed do\n task :hidden\nend",
        'namespace "#{computed}" do\n task :hidden\nend',
        "if enabled\n task :hidden\nend",
        "unless enabled\n task :hidden\nend",
        "[1, 2].each do |n|\n task :hidden\nend",
        "def helper\n task :hidden\nend",
        "class Helper\n task :hidden\nend",
        "module Helper\n task :hidden\nend",
        "task :hidden if enabled",
        "file 'output' do\n task :hidden\nend",
        "rule '.o' do\n task :hidden\nend",
    ],
)
def test_unsupported_scopes_are_skipped(wrapper):
    tasks = parse_rakefile(f"{wrapper}\nnamespace :db do\n task :real\nend\n")
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
        'task ""',
        'task "--help"',
        'task "name=value"',
        'task "build[argument]"',
        'task "two words"',
        'task "bad\\nname"',
        "other.task :hidden",
    ],
)
def test_dynamic_and_unsafe_names_are_skipped(declaration):
    assert parse_rakefile(f"{declaration}\n") == []


def test_nonliteral_descriptions_do_not_hide_tasks():
    tasks = parse_rakefile(
        'desc "#{compute()}"\ntask :build\ndesc computed\ntask :test\n'
    )
    assert [task.name for task in tasks] == ["build", "test"]
    assert all(task.description is None for task in tasks)


def test_description_does_not_leak_past_other_declarations():
    tasks = parse_rakefile(
        'desc "For file"\nfile "output"\ntask :build\n'
        'desc "For skipped"\ntask computed\ntask :test\n'
        'namespace :db do\n desc "Unused"\nend\ntask :root\n'
    )
    assert [task.name for task in tasks] == ["build", "test", "root"]
    assert all(task.description is None for task in tasks)


def test_duplicate_declarations_are_merged():
    tasks = parse_rakefile(
        'task :build\ndesc "Build everything"\ntask :build do\nend\n'
        "task :build => :test\nnamespace :db do\n task :build\nend\n"
    )
    assert [(task.name, task.description) for task in tasks] == [
        ("build", "Build everything"),
        ("db:build", None),
    ]


def test_detection_and_discovery_never_execute(tmp_path, monkeypatch):
    provider = RakeProvider()
    assert not provider.detect(tmp_path)
    marker = tmp_path / "executed"
    (tmp_path / "Rakefile").write_text(
        f'File.write({str(marker)!r}, "bad")\nrequire "missing"\n'
        'import "other.rake"\nnamespace :db do\n task :migrate do\n'
        '  raise "do not run"\n end\nend\n',
        encoding="utf-8",
    )
    (tmp_path / "rakelib").mkdir()
    (tmp_path / "rakelib" / "hidden.rake").write_text("task :hidden\n")
    (tmp_path / "other.rake").write_text("task :imported\n")

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
    "contents", [b"task :broken do\n", b"\xff", b'task "unterminated\n']
)
def test_invalid_files_are_skipped(tmp_path, caplog, contents):
    (tmp_path / "Rakefile").write_bytes(contents)
    assert RakeProvider().discover(tmp_path) == []
    assert "skipping Rakefile" in caplog.text


def test_missing_file_is_skipped(tmp_path, caplog):
    assert RakeProvider().discover(tmp_path) == []
    assert "skipping Rakefile" in caplog.text


def test_detection_is_current_directory_only(tmp_path):
    (tmp_path / "Rakefile").write_text("task :build\n")
    child = tmp_path / "child"
    child.mkdir()
    assert not RakeProvider().detect(child)
    (child / "Rakefile").mkdir()
    assert not RakeProvider().detect(child)


def test_reserved_rake_namespace_is_skipped():
    assert (
        parse_rakefile('task "rake:build"\nnamespace :rake do\n task :build\nend\n')
        == []
    )


def test_namespace_parameters_and_exception_handlers_are_skipped():
    tasks = parse_rakefile(
        "namespace :db do |task|\n task :hidden\nend\n"
        "namespace :unsafe do\n task :hidden\nrescue\n task :rescued\nend\n"
        "namespace :safe do\n task :real\nend\n"
    )
    assert [task.name for task in tasks] == ["safe:real"]


def test_description_and_names_with_unicode():
    tasks = parse_rakefile('desc "Construire le café"\ntask "café"\n')
    assert tasks[0].name == "café"
    assert tasks[0].description == "Construire le café"


def test_empty_file_and_custom_source():
    assert parse_rakefile("") == []
    assert parse_rakefile("task :build", "custom.rb")[0].source_file == "custom.rb"
