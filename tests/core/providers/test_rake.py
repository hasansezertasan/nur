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


@pytest.mark.parametrize("control", ["next", "break"])
def test_namespace_control_flow_stops_discovery(control):
    tasks = parse_rakefile(
        f"namespace :db do\n task :before\n {control}\n task :hidden\nend\ntask :root\n"
    )
    assert [task.name for task in tasks] == ["db:before", "root"]


def test_return_stops_file_discovery():
    tasks = parse_rakefile(
        "task :before\nnamespace :db do\n return\n task :hidden\nend\ntask :after\n"
    )
    assert [task.name for task in tasks] == ["before"]


def test_task_names_with_trailing_colons_are_skipped():
    assert parse_rakefile('task "build:"\nnamespace :db do\n task "test:"\nend') == []


def test_reserved_prefix_inside_an_ordinary_namespace_is_literal():
    tasks = parse_rakefile('namespace :db do\n task "rake:build"\nend')
    assert tasks[0].argv_base == ("rake", "db:rake:build")


@pytest.mark.parametrize(
    "replacement",
    [
        "def self.task(*args); end",
        "def (self).task(*args); end",
        "def ((self)).task(*args); end",
        "class << self; def task(*args); end; end",
        "class << ((self)); def task(*args); end; end",
    ],
)
def test_redefined_task_method_is_not_discovered(replacement):
    tasks = parse_rakefile(f"task :before\n{replacement}\ntask :ghost\n")
    assert [task.name for task in tasks] == ["before"]


def test_redefined_namespace_is_not_traversed():
    tasks = parse_rakefile(
        "def self.namespace(*args); end\nnamespace :db do\n task :ghost\nend\n"
        "task :root\n"
    )
    assert [task.name for task in tasks] == ["root"]


def test_redefinition_inside_namespace_affects_later_calls():
    tasks = parse_rakefile(
        "namespace :db do\n def self.task(*args); end\n task :ghost\nend\ntask :root\n"
    )
    assert tasks == []


def test_redefinition_in_task_body_does_not_run_during_loading():
    tasks = parse_rakefile(
        "task :build do\n def self.task(*args); end\nend\ntask :test\n"
    )
    assert [task.name for task in tasks] == ["build", "test"]


@pytest.mark.parametrize("control", ["redo"])
def test_restart_control_flow_stops_file_discovery(control):
    tasks = parse_rakefile(
        f"namespace :db do\n task :before\n {control}\n task :hidden\nend\ntask :root\n"
    )
    assert [task.name for task in tasks] == ["db:before"]


@pytest.mark.parametrize(
    "text",
    [
        "task :before\nretry\ntask :after\n",
        "task :before\nnamespace :db do\n task :inside\n retry\nend\n",
        "task :before\nif false\n retry\nend\n",
        "task :build do\n retry\nend\n",
        "begin\nrescue\n [1].each do\n  retry\n end\nend\ntask :build\n",
        "begin\nrescue\n def helper\n  retry\n end\nend\ntask :build\n",
        "begin\nrescue\n begin\n ensure\n  retry\n end\nend\ntask :build\n",
    ],
)
def test_retry_outside_rescue_rejects_whole_file(text, caplog):
    assert parse_rakefile(text) == []
    assert "retry outside rescue" in caplog.text


def test_retry_inside_rescue_does_not_invalidate_file():
    tasks = parse_rakefile(
        "task :before\nnamespace :db do\n task :conditional\nrescue\n retry\nend\n"
        "task :after\n"
    )
    assert [task.name for task in tasks] == ["before", "after"]


def test_retry_in_task_rescue_is_valid():
    tasks = parse_rakefile(
        "task :build do\n begin\n  sh 'false'\n rescue\n  retry\n end\nend\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("control", ["break", "next", "redo"])
def test_control_outside_block_or_loop_rejects_whole_file(control, caplog):
    assert parse_rakefile(f"task :before\nif false\n {control}\nend\n") == []
    assert f"{control} outside block or loop" in caplog.text


@pytest.mark.parametrize("control", ["break", "next", "redo"])
def test_loop_control_in_opaque_task_body_is_valid(control):
    tasks = parse_rakefile(f"task :build do\n while true\n  {control}\n end\nend\n")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("scope", ["class", "module", "class <<"])
def test_return_in_class_or_module_rejects_whole_file(scope, caplog):
    declaration = "class << self" if scope == "class <<" else f"{scope} Helper"
    text = f"task :before\n{declaration}\n return\nend\ntask :after\n"
    assert parse_rakefile(text) == []
    assert "return in class or module body" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "class Helper; def helper; return; end; end",
        "class Helper; [1].each { return }; end",
        "module Helper; -> { return }; end",
        "class Helper; class << self; def helper; return; end; end; end",
        "def helper; class << self; return; end; end",
    ],
)
def test_returns_in_method_and_block_scopes_are_valid(body):
    tasks = parse_rakefile(f"{body}\ntask :build\n")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("method", ["task", "namespace", "desc"])
def test_ordinary_methods_do_not_override_rake_singleton_dsl(method):
    tasks = parse_rakefile(
        f"def {method}(*args); end\n"
        'namespace :db do\n desc "Real task"\n task :build\nend\n'
    )
    assert [(task.name, task.description) for task in tasks] == [
        ("db:build", "Real task")
    ]


@pytest.mark.parametrize(
    "body",
    [
        "yield",
        "if false; yield; end",
        "task :hidden do; yield; end",
        "class Helper; yield; end",
        "module Helper; -> { yield }; end",
        "def helper; class << self; yield; end; end",
        "def (yield).helper; end",
    ],
)
def test_yield_outside_method_rejects_whole_file(body, caplog):
    assert parse_rakefile(f"task :before\n{body}\ntask :after\n") == []
    assert "yield outside method" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "def helper; yield; end",
        "def self.helper; [1].each { yield }; end",
        "def helper; -> { yield }; end",
        "class Helper; def helper; yield; end; end",
        "def helper; def (yield).nested; end; end",
    ],
)
def test_yield_inside_method_is_valid(body):
    tasks = parse_rakefile(f"{body}\ntask :build\n")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "assignment",
    [
        "CONST = 1",
        "::CONST = 1",
        "Helper::CONST = 1",
        "a, CONST = 1, 2",
        "a, *CONST = []",
        "(a, CONST), b = [[1, 2], 3]",
        "a, (b, CONST) = []",
        "for CONST in []; end",
        "for a, CONST in []; end",
        "CONST ||= 1",
        "-> { CONST = 1 }",
        "if false; CONST = 1; end",
    ],
)
def test_constant_assignment_in_method_rejects_whole_file(assignment, caplog):
    text = f"task :before\ndef helper; {assignment}; end\ntask :after\n"
    assert parse_rakefile(text) == []
    assert "constant assignment inside method" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "CONST = 1",
        "class Helper; CONST = 1; end",
        "task :hidden do; CONST = 1; end",
        "def helper; class << self; CONST = 1; end; end",
        "def helper; CONST[0] = 1; end",
        "def helper; Thing::value = 1; end",
        "def (CONST = Object.new).helper; end",
    ],
)
def test_constant_assignments_in_valid_scopes_and_receivers(body):
    tasks = parse_rakefile(f"{body}\ntask :build\n")
    assert tasks[-1].name == "build"


def test_constant_assignment_in_method_default_rejects_whole_file(caplog):
    assert parse_rakefile("def helper(x = (CONST = 1)); end\ntask :build\n") == []
    assert "constant assignment inside method" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "def helper; class Helper; end; end",
        "def self.helper; module Helper; end; end",
        "def helper; -> { class Helper; end }; end",
        "def helper; if false; module Helper; end; end; end",
    ],
)
def test_class_or_module_inside_method_rejects_whole_file(body, caplog):
    assert parse_rakefile(f"task :before\n{body}\ntask :after\n") == []
    assert "class or module definition inside method" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "task :hidden do; BEGIN {}; end",
        "def helper; BEGIN {}; end",
        "class Helper; BEGIN {}; end",
        "if false; BEGIN {}; end",
        "begin; BEGIN {}; end",
        "END { BEGIN {} }",
    ],
)
def test_begin_outside_top_level_rejects_whole_file(body, caplog):
    assert parse_rakefile(f"task :before\n{body}\ntask :after\n") == []
    assert "BEGIN outside top level" in caplog.text


def test_nested_top_level_begin_blocks_are_valid():
    tasks = parse_rakefile("BEGIN { BEGIN {} }\ntask :build\n")
    assert [task.name for task in tasks] == ["build"]


def test_singleton_receiver_return_keeps_enclosing_file_scope():
    tasks = parse_rakefile("task :before\nclass << (return; self); end\n")
    assert [task.name for task in tasks] == ["before"]


@pytest.mark.parametrize(
    "body",
    [
        "def helper(arg, arg); end",
        "def self.helper(arg, arg = 1); end",
        "def helper(arg, *arg); end",
        "def helper(arg, arg:); end",
        "def helper(arg, arg: 1); end",
        "def helper(arg, **arg); end",
        "def helper(arg, &arg); end",
        "def helper((arg, other), arg); end",
        "task :hidden do |arg, arg|; end",
        "task :hidden do |arg; arg|; end",
        "task :hidden do |; arg, arg|; end",
        "->(arg, arg) {}",
        "if false; def helper(arg, arg); end; end",
    ],
)
def test_duplicate_parameter_names_reject_whole_file(body, caplog):
    assert parse_rakefile(f"task :before\n{body}\ntask :after\n") == []
    assert "duplicated argument name" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "def helper(_arg, _arg); end",
        "def helper(arg = call(arg), other: arg); end",
        "def helper(arg, *rest, key: 1, **options, &block); end",
        "def helper(*, **, &); end",
        "def first(arg); end; def second(arg); end",
        "task :build do |arg|; call { |arg| }; end",
        "task :build do |arg; other|; end",
        "def helper((arg, (other, *rest))); end",
    ],
)
def test_valid_parameter_bindings_preserve_discovery(body):
    tasks = parse_rakefile(f"{body}\ntask :after\n")
    assert tasks[-1].name == "after"


@pytest.mark.parametrize(
    "replacement",
    [
        "def ((other)).task(*args); end",
        "class << (self; other); def task(*args); end; end",
        "class << (); end",
    ],
)
def test_other_singleton_receivers_preserve_discovery(replacement):
    tasks = parse_rakefile(f"{replacement}\ntask :build\n")
    assert [task.name for task in tasks] == ["build"]


def test_parenthesized_self_with_comments_overrides_dsl():
    tasks = parse_rakefile("class << ( # receiver\n self); end\ntask :ghost\n")
    assert tasks == []
