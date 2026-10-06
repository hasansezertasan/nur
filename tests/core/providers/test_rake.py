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


def test_pending_descriptions_are_consumed_by_tasks_and_files():
    tasks = parse_rakefile(
        'desc "For file"\nfile "output"\ntask :build\n'
        'desc "For skipped"\ntask computed\ntask :test\n'
        'namespace :db do\n desc "Unused"\nend\ntask :root\n'
    )
    assert [task.name for task in tasks] == ["build", "test", "root"]
    assert [task.description for task in tasks] == [None, None, "Unused"]


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


@pytest.mark.parametrize("name", [f"_{number}" for number in range(1, 10)])
@pytest.mark.parametrize(
    "binding",
    [
        "{name}",
        "{name} = 1",
        "*{name}",
        "{name}:",
        "**{name}",
        "&{name}",
        "({name}, other)",
    ],
)
def test_reserved_numbered_parameters_reject_whole_file(name, binding, caplog):
    parameters = binding.format(name=name)
    assert parse_rakefile(f"task :before; def helper({parameters}); end") == []
    assert "reserved numbered parameter" in caplog.text


@pytest.mark.parametrize(
    "pattern",
    [
        "[value, value]",
        "([value, value])",
        "Array[value, value]",
        '{"value":, other: value}',
        "[value, *value]",
        "[*value, other, *value]",
        "[value, [value]]",
        "[value => value]",
        "[value, other] => value",
        "{value:, other: value}",
        "{key: value, **value}",
    ],
)
@pytest.mark.parametrize(
    "form", ["case []; in {pattern}; end", "[] => {pattern}", "[] in {pattern}"]
)
def test_duplicate_pattern_bindings_reject_whole_file(pattern, form, caplog):
    body = form.format(pattern=pattern)
    assert parse_rakefile(f"task :before; {body}") == []
    assert "duplicated pattern variable" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "case []; in [value, other]; end",
        "case []; in [_value, _value]; end",
        "case []; in [value]; in [value]; end",
        "value = 1; case []; in [value, ^value]; end",
        "value = 1; case []; in [value, ^(value + 1)]; end",
        "case {}; in {key: value, other: other}; end",
        "[] => [value, other]",
        "[] in [value, other]",
        "case []; in [value] if check(value); end",
        "def helper(_0, _10, _arg, _arg); end",
        "task :build do; _1; end",
    ],
)
def test_valid_pattern_and_numbered_parameter_usage_preserves_discovery(body):
    tasks = parse_rakefile(f"{body}; task :after")
    assert tasks[-1].name == "after"


@pytest.mark.parametrize(
    ("descriptions", "expected"),
    [
        (["First", "Second", "First"], "First / Second"),
        ([" First ", "First", "", "Second"], "First / Second"),
        (["First. Details", "Second! Details"], "First / Second"),
        (["First.\nDetails", "Second"], "First / Second"),
        (["Version 1.2", "Second"], "Version 1.2 / Second"),
        (["Same. One", "Same. Two"], "Same / Same"),
        (["", "   "], None),
    ],
)
def test_duplicate_task_descriptions_match_rake_listing(descriptions, expected):
    source = "\n".join(
        f'desc "{description}"; task :build' for description in descriptions
    )
    tasks = parse_rakefile(source)
    assert tasks[0].description == expected


@pytest.mark.parametrize(
    "body",
    [
        "[1].each { |arg| _1 }",
        "_1 = 1",
        "[1].each { _1 = 1 }",
        "[1].each { || _9 }",
        "[1].each { |; local| _1 }",
        "->(arg) { _1 }",
        "->() { _1 }",
        "task :hidden do |arg|; _1; end",
        "[1].each { _1; [1].each { _1 } }",
        "[1].each { [1].each { _1 }; _1 }",
    ],
)
def test_invalid_numbered_references_reject_whole_file(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "numbered parameter" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "case 1; in ^missing; end",
        "1 => ^missing",
        "1 in ^missing",
        "case []; in [^value, value]; end",
        "case 1; in ^value; end; value = 1",
        "call { value = 1 }; case 1; in ^value; end",
        "value = 1; def helper; case 1; in ^value; end; end",
        "value = 1; class Helper; case 1; in ^value; end; end",
        "value = 1; module Helper; case 1; in ^value; end; end",
        "value = 1; class << self; case 1; in ^value; end; end",
        "def helper(arg = (1 in ^other), other = 1); end",
    ],
)
def test_undefined_pins_reject_whole_file(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "no such local variable" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "value = 1; case 1; in ^value; end",
        "case {}; in {value:}; end; 1 in ^value",
        'case {}; in {"value":}; end; 1 in ^value',
        "case 1; in ^@value; end",
        "case 1; in ^(computed); end",
        "value = 1; call { 1 in ^value }",
        "call { |value| 1 in ^value }",
        "call { |; value| 1 in ^value }",
        "value = (1 in ^value)",
        "left, value = 1, (1 in ^value)",
        "for value in []; 1 in ^value; end",
        "def helper(value = (1 in ^value)); end",
        "case []; in [value, ^value]; end",
        "case 1; in value; in ^value; end",
        "[] => [value]; 1 in ^value",
        "begin; call; rescue => value; 1 in ^value; end",
        "call { |value| call { _1 } }",
        "call { _2; 1 in ^_1 }",
        "call { |value| other._1; _1() }",
        "def helper; _1; end",
        "call { |value| def helper; _1; end }",
        "value = 1; def ((1 in ^value)).helper; end",
    ],
)
def test_valid_lexical_scopes_preserve_discovery(body):
    tasks = parse_rakefile(f"{body}; task :after")
    assert tasks[-1].name == "after"


@pytest.mark.parametrize("target", ["nil", "true", "false", "self"])
@pytest.mark.parametrize(
    "form",
    [
        "{target} = 1",
        "({target}, other) = 1, 2",
        "for {target} in []; end",
        "begin; rescue => {target}; end",
    ],
)
def test_nonassignable_targets_reject_whole_file(target, form, caplog):
    assert (
        parse_rakefile(f"task :before; {form.format(target=target)}; task :after") == []
    )
    assert "nonassignable target" in caplog.text


@pytest.mark.parametrize("target", ["CONST", "Helper::CONST", "::CONST"])
def test_rescue_constant_targets_in_methods_reject_whole_file(target, caplog):
    assert (
        parse_rakefile(f"task :before; def helper; begin; rescue => {target}; end; end")
        == []
    )
    assert "constant assignment inside method" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "call { |arg| it }",
        "call { it; other { |arg| it } }",
        "call { it; other { _1; it } }",
        "call { |arg| 1 in ^it }",
        "call { _1; 1 in ^it }",
        "call { it; 1 in ^_1 }",
        "call { || it }",
        "->(arg) { it }",
        "call { it; _1 }",
        "call { _1; it }",
    ],
)
def test_invalid_implicit_it_rejects_whole_file(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "implicit it" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "call { it }",
        "call { 1 in ^it }",
        "call { 1 in ^_1 }",
        "call { it; other { 1 in ^it } }",
        "call { it; it = 1; other { |arg| it } }",
        "call { |it| it }",
        "call { |; it| it }",
        "it = 1; call { |arg| it }",
        "it = 1; call { _1; it }",
        "call { it = 1; _1; it }",
        "call { |arg| it = 1; it }",
        "call { it; it = 1 }",
        "call { it; other { _1 } }",
        "call { _1; other { it } }",
        "call { |arg| self.it; it() }",
        "begin; rescue => CONST; end",
        "def helper; begin; rescue => array[0]; end; end",
        "case 1; in nil; in true; in false; end",
        "def helper; it; end",
    ],
)
def test_valid_it_and_assignment_scopes_preserve_discovery(body):
    tasks = parse_rakefile(f"{body}; task :after")
    assert tasks[-1].name == "after"


@pytest.mark.parametrize(
    "modifier",
    [
        "if false",
        "unless true",
        "while false",
        "until true",
        "rescue nil",
        "if false while false",
    ],
)
def test_top_level_begin_modifiers_are_valid(modifier):
    tasks = parse_rakefile(f"task :before; BEGIN {{}} {modifier}; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


def test_begin_modifier_inside_block_still_rejects_file(caplog):
    assert parse_rakefile("task :before; call { BEGIN {} if false }") == []
    assert "BEGIN outside top level" in caplog.text


@pytest.mark.parametrize(
    "escape",
    [
        "return if ENV['STOP']",
        "return unless enabled",
        "if enabled; return; end",
        "case mode; when :stop; return; end",
        "enabled ? return : nil",
        "while enabled; return; end",
    ],
)
def test_conditional_return_omits_later_file_tasks(escape):
    tasks = parse_rakefile(f"task :before; {escape}; task :after")
    assert [task.name for task in tasks] == ["before"]


@pytest.mark.parametrize(
    "escape",
    [
        "next if enabled",
        "break unless enabled",
        "if enabled; next; end",
        "begin; next if enabled; end",
    ],
)
def test_conditional_namespace_escape_keeps_parent_scope(escape):
    tasks = parse_rakefile(
        f"namespace :db do; task :before; {escape}; task :after; end; task :root"
    )
    assert [task.name for task in tasks] == ["db:before", "root"]


@pytest.mark.parametrize(
    "body",
    [
        "while enabled; next; end",
        "for value in []; break; end",
        "call { next if enabled }",
        "def helper; return if enabled; end",
        "task :build do; return if enabled; end",
    ],
)
def test_local_controls_do_not_escape_discovery_scope(body):
    tasks = parse_rakefile(f"{body}; task :after")
    assert tasks[-1].name == "after"


@pytest.mark.parametrize(
    "body",
    [
        "call(...)",
        "def helper; call(...); end",
        "def helper(...); def nested; call(...); end; end",
        "def helper(...); class << self; call(...); end; end",
        "def helper(arg = call(...), ...); end",
        "def helper(*args); call(*); end",
        "def helper(**kw); call(**); end",
        "def helper(&block); call(&); end",
        "call(*)",
        "call(**)",
        "call(&)",
        "def helper(...); call(*); end",
        "def helper(...); call(**); end",
        "def helper(...); call(&); end",
        "def helper(*); call { |*| target(*) }; end",
        "def helper(**); call { |**| target(**) }; end",
        "def helper(&); call { |&| target(&) }; end",
        "call { |*| target(*) }",
    ],
)
def test_invalid_argument_forwarding_rejects_whole_file(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "argument forwarding" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "def helper(...); call(...); end",
        "def self.helper(first, ...); call(...); end",
        "def helper(...); call { target(...) }; end",
        "def helper(...); -> { target(...) }; end",
        "def helper(*); call(*); end",
        "def helper(**); call(**); end",
        "def helper(&); call(&); end",
        "def helper(*, **, &); call(*, **, &); end",
        "def helper(*); call { |*args| target(*) }; end",
        "def helper(...); def (call(...)).nested; end; end",
        "def helper(*args, **kw, &block); call(*args, **kw, &block); end",
    ],
)
def test_valid_argument_forwarding_preserves_discovery(body):
    tasks = parse_rakefile(f"{body}; task :after")
    assert tasks[-1].name == "after"


@pytest.mark.parametrize("loop", ["while", "until"])
@pytest.mark.parametrize("control", ["break", "next", "redo"])
def test_loop_condition_controls_are_valid(loop, control):
    tasks = parse_rakefile(f"task :before; {loop} ({control}; false); end; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "condition", ["enabled && break", "if enabled; break; else; false; end"]
)
def test_loop_condition_can_conditionally_exit(condition):
    tasks = parse_rakefile(f"task :before; while ({condition}); end; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "condition",
    [
        "break",
        "next",
        "redo",
        "return",
        "if enabled; break; else; next; end",
        "enabled ? break : next",
    ],
)
def test_void_loop_conditions_reject_whole_file(condition, caplog):
    assert parse_rakefile(f"task :before; while ({condition}); end; task :after") == []
    assert "void value" in caplog.text


@pytest.mark.parametrize("target", ["$1", "$10", "$&", "$+", "$'", "$`"])
@pytest.mark.parametrize(
    "form",
    [
        "{target} = 1",
        "{target} += 1",
        "other, {target} = 1, 2",
        "for {target} in []; end",
        "begin; rescue => {target}; end",
    ],
)
def test_readonly_match_globals_reject_whole_file(target, form, caplog):
    assert (
        parse_rakefile(f"task :before; {form.format(target=target)}; task :after") == []
    )
    assert "readonly match global" in caplog.text


@pytest.mark.parametrize("target", ["$~", "$_", "$named"])
def test_writable_globals_preserve_discovery(target):
    tasks = parse_rakefile(f"{target} = nil; task :build")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("name", ["helper=", "[]="])
@pytest.mark.parametrize("receiver", ["", "self."])
def test_endless_setters_reject_whole_file(name, receiver, caplog):
    assert (
        parse_rakefile(
            f"task :before; def {receiver}{name}(value) = value; task :after"
        )
        == []
    )
    assert "endless setter" in caplog.text


@pytest.mark.parametrize(
    "name", ["helper", "==", "===", "!=", "<=", ">=", "=~", "<=>", "[]"]
)
def test_endless_ordinary_and_operator_methods_are_valid(name):
    tasks = parse_rakefile(f"def {name}(value) = value; task :build")
    assert [task.name for task in tasks] == ["build"]


def test_normal_setter_methods_are_valid():
    tasks = parse_rakefile("def helper=(value); value; end; task :build")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "body",
    [
        "helper(&callback) {}",
        "helper(&callback) do; end",
        "helper(&nil) {}",
        "task(:after, &callback) {}",
        "def helper(&); target(&) {}; end",
        "def helper(...); target(...) {}; end",
        "def helper(...); target(...) do; end; end",
    ],
)
def test_calls_with_two_blocks_reject_whole_file(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "both block" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "helper(&callback)",
        "helper(nested(&callback)) {}",
        "def helper(...); target(...); end",
        "helper { nested(&callback) }",
    ],
)
def test_calls_with_one_block_preserve_discovery(body):
    tasks = parse_rakefile(f"{body}; task :after")
    assert tasks[-1].name == "after"


@pytest.mark.parametrize(
    "body",
    [
        "begin; rescue; class << (retry if false; self); end; end",
        "call { class << (break if false; self); end }",
        "call { class << (next if false; self); end }",
        "call { class << (redo if false; self); end }",
        "begin; rescue; def ((retry if false; self)).helper; end; end",
    ],
)
def test_singleton_headers_keep_enclosing_control_scope(body):
    tasks = parse_rakefile(f"{body}; task :build")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("control", ["retry", "break", "next", "redo"])
def test_singleton_bodies_reset_enclosing_control_scope(control, caplog):
    assert (
        parse_rakefile(
            "task :before; begin; rescue; call { "
            f"class << self; {control}; end }}; end"
        )
        == []
    )
    assert "outside" in caplog.text


@pytest.mark.parametrize("source", ["$1", "$10", "$999"])
def test_numbered_match_alias_sources_reject_whole_file(source, caplog):
    assert parse_rakefile(f"task :before; alias $copy {source}; task :after") == []
    assert "numbered match alias" in caplog.text


@pytest.mark.parametrize("body", ["alias $copy helper", "alias helper $copy"])
def test_mixed_global_and_method_aliases_reject_whole_file(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "mixed global and method alias" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "alias $copy $&",
        "alias $copy $0",
        "alias $1 $copy",
        "alias helper other",
        "alias :helper :other",
    ],
)
def test_valid_aliases_preserve_discovery(body):
    tasks = parse_rakefile(f"{body}; task :build")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "parameters", ["||", "|scope|", "|scope; scratch|", "|*scopes|", "|**options|"]
)
def test_harmless_namespace_parameters_preserve_discovery(parameters):
    tasks = parse_rakefile(
        f"namespace :db do {parameters}; desc 'Migrate'; task :migrate; end; task :root"
    )
    assert [task.name for task in tasks] == ["db:migrate", "root"]
    assert tasks[0].description == "Migrate"


@pytest.mark.parametrize(
    "body",
    [
        "defined?(break)",
        "defined?(next)",
        "defined?(redo)",
        "defined?(retry)",
        "defined?(return)",
        "defined?(yield)",
        "defined? break",
        "defined?(begin; break; end)",
        "defined?(class C; break; end)",
        "defined?(class C; next; end)",
        "defined?(class C; redo; end)",
        "defined?(class C; yield; end)",
        "defined?(class << self; yield; end)",
        "defined?(foo { retry })",
        "defined?(-> { retry })",
        "defined?(def foo; defined?(break); end)",
    ],
)
def test_defined_control_probes_preserve_discovery(body):
    tasks = parse_rakefile(f"task :before; {body}; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "body",
    [
        "class C; defined?(return); end",
        "defined?(while (break); end)",
        "defined?(def foo(a,a); end)",
        "defined?(def foo; break; end)",
        "defined?(def self.foo; break; end)",
        "defined?(def foo; retry; end)",
        "defined?(class C; retry; end)",
        "defined?(class << self; break; end)",
    ],
)
def test_defined_probes_retain_compile_time_restrictions(body):
    assert parse_rakefile(f"task :before; {body}; task :after") == []


@pytest.mark.parametrize("control", ["next", "break", "redo", "return"])
def test_end_controls_are_deferred_and_preserve_discovery(control):
    tasks = parse_rakefile(
        f"task :before; END {{ {control}; task :hidden }}; desc 'After'; task :after"
    )
    assert [task.name for task in tasks] == ["before", "after"]
    assert tasks[1].description == "After"


def test_end_retry_does_not_inherit_enclosing_rescue(caplog):
    assert parse_rakefile("task :before; begin; rescue; END { retry }; end") == []
    assert "retry outside rescue" in caplog.text


def test_end_return_still_rejects_class_context(caplog):
    assert parse_rakefile("class Helper; END { return }; end; task :after") == []
    assert "return in class" in caplog.text


@pytest.mark.parametrize(
    "override",
    [
        "BEGIN { def self.task(*args); end }",
        "BEGIN { BEGIN { def self.task(*args); end } }",
        "BEGIN { def (self).task(*args); end }",
        "BEGIN { class << self; def task(*args); end; end }",
        "BEGIN { def self.task(*args); end } if true",
        "BEGIN { def self.task(*args); end } unless false",
        "BEGIN { def self.task(*args); end } while false",
        "BEGIN { def self.task(*args); end } until true",
    ],
)
def test_begin_overrides_apply_before_earlier_declarations(override):
    assert parse_rakefile(f"desc 'Ghost'; task :ghost; {override}; task :later") == []


def test_begin_desc_override_preserves_tasks_without_descriptions():
    tasks = parse_rakefile(
        "desc 'Ghost'; task :build; BEGIN { def self.desc(*args); end }"
    )
    assert [task.name for task in tasks] == ["build"]
    assert tasks[0].description is None


@pytest.mark.parametrize(
    "body",
    [
        "BEGIN { END { def self.task(*args); end } }",
        "BEGIN { def helper; def self.task(*args); end; end }",
        "BEGIN { call { def self.task(*args); end } }",
        "BEGIN { class Helper; def self.task(*args); end; end }",
    ],
)
def test_begin_nested_opaque_overrides_preserve_discovery(body):
    tasks = parse_rakefile(f"task :before; {body}; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize("modifier", ["if false", "if nil", "unless true"])
def test_inactive_begin_overrides_preserve_discovery(modifier):
    tasks = parse_rakefile(
        f"task :before; BEGIN {{ def self.task(*args); end }} {modifier}; task :after"
    )
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "pattern",
    [
        "{a:} | {b:}",
        "[a] | [b]",
        "{key: a} | {other: b}",
        "[*a] | [*b]",
        "{**a} | {**b}",
        "([1] => a) | [2]",
    ],
)
def test_alternative_pattern_bindings_reject_whole_file(pattern, caplog):
    assert (
        parse_rakefile(f"task :before; case value; in {pattern}; end; task :after")
        == []
    )
    assert "alternative pattern" in caplog.text


@pytest.mark.parametrize(
    "pattern",
    [
        "{a: _a} | {b: _b}",
        "[_a] | [_b]",
        "[*_a] | [*_b]",
        "{**_a} | {**_b}",
        "[1] | [2] => whole",
        "[^a] | [^b]",
    ],
)
def test_valid_alternative_patterns_preserve_discovery(pattern):
    tasks = parse_rakefile(f"a = 1; b = 2; case value; in {pattern}; end; task :build")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "body",
    [
        "if false; def self.task(*args); end; end",
        "unless true; def self.task(*args); end; end",
        "if nil; def self.task(*args); end; end",
        "if (false); def self.task(*args); end; end",
        "if true; nil; else; def self.task(*args); end; end",
        "unless false; nil; else; def self.task(*args); end; end",
        (
            "if false; def self.task(*args); end; "
            "elsif false; def self.task(*args); end; end"
        ),
        "while false; def self.task(*args); end; end",
        "until true; def self.task(*args); end; end",
        "defined?(def self.task(*args); end)",
    ],
)
def test_inactive_begin_branches_preserve_discovery(body):
    tasks = parse_rakefile(f"task :before; BEGIN {{ {body} }}; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "body",
    [
        "if true; def self.task(*args); end; end",
        "unless false; def self.task(*args); end; end",
        "if false; nil; else; def self.task(*args); end; end",
        "unless true; nil; else; def self.task(*args); end; end",
        "if false; nil; elsif true; def self.task(*args); end; end",
        "if false; nil; elsif false; nil; else; def self.task(*args); end; end",
    ],
)
def test_active_begin_branches_override_earlier_declarations(body):
    assert parse_rakefile(f"task :before; BEGIN {{ {body} }}; task :after") == []


@pytest.mark.parametrize(
    "arguments",
    [
        ":build, bar: :baz",
        ":build, {bar: :baz}",
        ":build, {}",
        ":build, [:mode] => :test, [:other] => :lint",
        ":build, :mode, order_only: :test",
        ":build, 'mode' => :test",
        ":build, [:first], [:second]",
        ":build, [nil]",
        ":build, 123",
        ":build, nil",
    ],
)
def test_malformed_task_argument_tails_reject_whole_file(arguments):
    assert parse_rakefile(f"task({arguments}); task :safe") == []


@pytest.mark.parametrize(
    "arguments",
    [
        ":build, :first, :second",
        ":build, ['first', :second]",
        ":build, []",
        ":build, [:mode] => :test",
        ":build, {[:mode] => :test}",
        ":build, [:mode] => :test, order_only: :prepare",
        ":build, order_only: :prepare",
        ":build, [:mode], order_only: :prepare",
        ":build, nil => :test",
        ":build, [:mode], nil => :test",
        ":build, nil, order_only: :prepare",
    ],
)
def test_valid_task_argument_tails_preserve_discovery(arguments):
    tasks = parse_rakefile(f"task({arguments})")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "declaration", ["task order_only: :prepare", "task :order_only => :prepare"]
)
def test_order_only_hash_without_task_name_is_skipped(declaration):
    assert parse_rakefile(declaration) == []


@pytest.mark.parametrize(
    "declaration", ["task :order_only", "task 'order_only' => :prepare"]
)
def test_order_only_literal_task_names_remain_supported(declaration):
    assert [task.name for task in parse_rakefile(declaration)] == ["order_only"]


@pytest.mark.parametrize(
    "body",
    [
        "def helper; value = return; end",
        "while true; value = break; end",
        "begin; rescue; value = retry; end",
        "call { value = next }",
        "call { value = redo }",
        "def helper; value ||= return; end",
        "def helper; value += return; end",
        "def helper; foo(return); end",
        "def helper; [return]; end",
        "def helper; {a: return}; end",
        "def helper; !return; end",
        "def helper; if return; end; end",
        "def helper; return if return; end",
        "def helper; case return; when 1; end; end",
        "def helper; for x in return; end; end",
        "def helper; obj[return]; end",
        "def helper; (return).foo; end",
        "def helper; value = (return || foo); end",
        "defined?(value = return)",
        "def helper; value = (begin; return; end); end",
        "def helper; def foo(x = return); end; end",
        "def helper; return return; end",
        "def helper; value = (true ? return : return); end",
        "def helper; (return) + 1; end",
        "def helper; (return)..1; end",
    ],
)
def test_void_value_positions_reject_whole_file(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "void value" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "def helper; value = (foo || return); end",
        "def helper; value = (foo && return); end",
        "def helper; value = if cond; return; else; 1; end; end",
        "def helper; value = if cond; return; end; end",
        "def helper; value = (return if cond); end",
        "def helper; value = (return; 1); end",
        "def helper; value = (begin; return; rescue; nil; end); end",
        "def helper; value = (return rescue nil); end",
        "def helper; return + 1; end",
        "def helper; return - 1; end",
        "def helper; return * 1; end",
        "def helper; return ** 1; end",
        "def helper; return..1; end",
        'def helper; "#{return}"; end',
    ],
)
def test_valid_control_value_expressions_preserve_discovery(body):
    tasks = parse_rakefile(f"task :before; {body}; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "body",
    [
        "begin; def self.task(*args); end; end",
        "begin; begin; def (self).task(*args); end; end; end",
        "begin; class << self; def task(*args); end; end; end",
    ],
)
def test_plain_begin_overrides_apply_to_later_declarations(body):
    tasks = parse_rakefile(f"task :before; {body}; task :after")
    assert [task.name for task in tasks] == ["before"]


def test_plain_begin_desc_override_preserves_earlier_description():
    tasks = parse_rakefile(
        "desc 'Before'; task :before; begin; def self.desc(*args); end; end; "
        "desc 'After'; task :after"
    )
    assert [(task.name, task.description) for task in tasks] == [
        ("before", "Before"),
        ("after", None),
    ]


@pytest.mark.parametrize(
    "body",
    [
        "begin; if false; def self.task(*args); end; end; end",
        "begin; def helper; def self.task(*args); end; end; end",
        "begin; END { def self.task(*args); end }; end",
    ],
)
def test_plain_begin_inactive_and_opaque_overrides_preserve_discovery(body):
    tasks = parse_rakefile(f"{body}; task :build")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "body",
    [
        "begin; task :wrapped; end",
        "begin; begin; task :wrapped; end; end",
        "namespace :db do; begin; task :wrapped; end; end",
        "begin; namespace :db do; task :wrapped; end; end",
    ],
)
def test_plain_begin_declarations_are_discovered(body):
    tasks = parse_rakefile(body)
    assert len(tasks) == 1
    assert tasks[0].name == ("db:wrapped" if "namespace" in body else "wrapped")


@pytest.mark.parametrize(
    "body",
    [
        "desc 'Wrapped'; begin; task :wrapped; end",
        "begin; desc 'Wrapped'; task :wrapped; end",
        "begin; desc 'Wrapped'; end; task :wrapped",
        "begin; begin; desc 'Wrapped'; end; end; task :wrapped",
    ],
)
def test_plain_begin_descriptions_share_enclosing_scope(body):
    tasks = parse_rakefile(body)
    assert [(task.name, task.description) for task in tasks] == [("wrapped", "Wrapped")]


def test_plain_begin_overrides_preserve_declaration_order_inside_wrapper():
    tasks = parse_rakefile(
        "begin; task :before; def self.task(*args); end; task :after; end; task :root"
    )
    assert [task.name for task in tasks] == ["before"]


@pytest.mark.parametrize("handler", ["rescue", "ensure", "rescue; nil; else"])
def test_plain_begin_exception_handlers_remain_opaque(handler):
    tasks = parse_rakefile(
        f"begin; task :hidden; {handler}; task :also_hidden; end; task :root"
    )
    assert [task.name for task in tasks] == ["root"]


def test_plain_begin_control_preserves_preceding_declarations():
    tasks = parse_rakefile(
        "begin; task :before; return; task :hidden; end; task :after"
    )
    assert [task.name for task in tasks] == ["before"]


@pytest.mark.parametrize(
    "body",
    [
        "if true; def self.task(*args); end; end",
        "unless false; def self.task(*args); end; end",
        "if false; nil; else; def self.task(*args); end; end",
    ],
)
def test_plain_begin_conditional_overrides_preserve_source_order(body):
    tasks = parse_rakefile(
        f"task :root_before; begin; task :inner_before; {body}; "
        "task :hidden; end; task :root_after"
    )
    assert [task.name for task in tasks] == ["root_before", "inner_before"]


@pytest.mark.parametrize(
    "body",
    [
        "def helper; foo rescue retry; end",
        "foo rescue retry",
        "def helper; (foo rescue retry) rescue nil; end",
        "foo rescue (retry if enabled)",
        "foo rescue (retry; nil)",
    ],
)
def test_rescue_modifier_handlers_allow_retry(body):
    tasks = parse_rakefile(f"task :before; {body}; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "body",
    [
        "retry rescue nil",
        "(retry rescue nil) rescue retry",
        "def helper; foo rescue call { retry }; end",
        "foo rescue (class C; retry; end)",
    ],
)
def test_rescue_modifier_retry_permissions_do_not_leak(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "retry outside rescue" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "BEGIN { return }",
        "BEGIN { return } if true",
        "BEGIN { return } unless false",
        "BEGIN { return } while false",
        "BEGIN { return } until true",
        "BEGIN { if true; return; end }",
        "BEGIN { unless false; return; end }",
        "BEGIN { BEGIN { return } }",
        "BEGIN { while true; return; end }",
    ],
)
def test_active_begin_return_prevents_all_declarations(body):
    assert parse_rakefile(f"task :before; {body}; task :after") == []


@pytest.mark.parametrize(
    "body",
    [
        "BEGIN { return } if false",
        "BEGIN { return } unless true",
        "BEGIN { if false; return; end }",
        "BEGIN { unless true; return; end }",
        "BEGIN { while false; return; end }",
        "BEGIN { until true; return; end }",
        "BEGIN { def helper; return; end }",
        "BEGIN { END { return } }",
        "BEGIN { defined?(return) }",
    ],
)
def test_inactive_and_deferred_begin_returns_preserve_discovery(body):
    tasks = parse_rakefile(f"task :before; {body}; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "body",
    [
        "(task :build)",
        "((task :build))",
        "(desc 'Build'; task :build)",
        "(desc 'Build'); (task :build)",
        "(begin; task :build; end)",
    ],
)
def test_parenthesized_declarations_are_transparent(body):
    tasks = parse_rakefile(body)
    assert [task.name for task in tasks] == ["build"]
    assert tasks[0].description == ("Build" if "desc" in body else None)


def test_parenthesized_namespaces_are_discovered():
    tasks = parse_rakefile("(namespace :db do; (task :migrate); end)")
    assert [task.name for task in tasks] == ["db:migrate"]


def test_parenthesized_overrides_preserve_source_order():
    tasks = parse_rakefile(
        "(task :before; def self.task(*args); end; task :hidden); task :after"
    )
    assert [task.name for task in tasks] == ["before"]


def test_parentheses_inside_opaque_bodies_remain_opaque():
    tasks = parse_rakefile(
        "task :outer do; (task :hidden); end; result = (task :also_hidden)"
    )
    assert [task.name for task in tasks] == ["outer"]


@pytest.mark.parametrize(
    "expression",
    [
        "/(/",
        "/[abc/",
        "/[z-a]/",
        "/a{3,2}/",
        "/a{100001}/",
        "/a{999999999}/",
        r"/\1/",
        r"/\xFF/",
        r"/\u{bogus}/",
        r"/\p{bogus}/",
        "/abc/q",
        "/#{value}/q",
        "%r{(}",
    ],
)
def test_invalid_regexp_literals_reject_whole_file(expression, caplog):
    assert parse_rakefile(f"task :before; {expression}; task :after") == []
    assert "skipping Rakefile" in caplog.text


@pytest.mark.parametrize(
    "expression",
    [
        "/abc/",
        "/a(b|c)+/",
        "/[a-z_0-9]+/",
        r"/\d+\s*\w?/",
        "/^begin.*end$/im",
        r"/\Afoo\z/",
        r"/\Gfoo/",
        r"/a\e/",
        r"/\x20/",
        r"/\h+\H?\R/",
        "/a{1,3}/",
        "/a{100000}/",
        "%r{foo/bar}i",
        "%r(a(?:b|c))",
        "/abc/iimx",
        "/abc/nu",
        "/a # ) ignored\nb/x",
        "/(?=a)a/",
        "/(?!a)b/",
        "/#{value}(/",
    ],
)
def test_supported_regexp_literals_preserve_discovery(expression):
    tasks = parse_rakefile(f"task :before; task :after do; {expression}; end")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "expression", [r"/\p{L}/", r"/(?<name>\w+)/", "/(?<=a)b/", "/[a&&b]/"]
)
def test_regexp_forms_outside_validated_subset_are_skipped(expression, caplog):
    assert parse_rakefile(f"task :before; {expression}") == []
    assert "unsupported Ruby regexp" in caplog.text


def test_regexp_capture_count_respects_ruby_limit(caplog):
    pattern = "()" * 32768
    assert parse_rakefile(f"task :before; /{pattern}/; task :after") == []
    assert "regexp capture count" in caplog.text


def test_regexp_capture_count_at_ruby_limit_is_supported():
    pattern = "()" * 32767
    assert [task.name for task in parse_rakefile(f"/{pattern}/; task :build")] == [
        "build"
    ]


@pytest.mark.parametrize(
    "initializer",
    [
        "BEGIN { task :bootstrap }",
        "BEGIN { task :bootstrap } if true",
        "BEGIN { task :bootstrap } unless false",
        "BEGIN { task :bootstrap } while false",
        "BEGIN { task :bootstrap } until true",
        "BEGIN { task :bootstrap } rescue nil",
    ],
)
def test_active_begin_tasks_precede_ordinary_declarations(initializer):
    tasks = parse_rakefile(f"task :ordinary; {initializer}")
    assert [task.name for task in tasks] == ["bootstrap", "ordinary"]


def test_nested_begin_tasks_follow_initializer_execution_order():
    tasks = parse_rakefile(
        "BEGIN { task :outer; BEGIN { task :inner } }; "
        "BEGIN { task :second; BEGIN { task :second_inner } }; task :ordinary"
    )
    assert [task.name for task in tasks] == [
        "inner",
        "outer",
        "second_inner",
        "second",
        "ordinary",
    ]


def test_begin_descriptions_share_ordinary_scope():
    tasks = parse_rakefile('task :build; BEGIN { desc "Build" }')
    assert [(task.name, task.description) for task in tasks] == [("build", "Build")]


def test_begin_tasks_and_namespaces_read_descriptions():
    tasks = parse_rakefile(
        'BEGIN { desc "Boot"; task :bootstrap; '
        'namespace :db do; desc "Migrate"; task :migrate; end }'
    )
    assert [(task.name, task.description) for task in tasks] == [
        ("bootstrap", "Boot"),
        ("db:migrate", "Migrate"),
    ]


def test_begin_overrides_preserve_earlier_initializer_tasks():
    tasks = parse_rakefile(
        "task :ordinary; BEGIN { task :bootstrap; def self.task(*args); end }; "
        "BEGIN { task :hidden }"
    )
    assert [task.name for task in tasks] == ["bootstrap"]


@pytest.mark.parametrize("modifier", ["if false", "unless true", "if condition"])
def test_inactive_or_dynamic_begin_tasks_remain_opaque(modifier):
    tasks = parse_rakefile(f"BEGIN {{ task :hidden }} {modifier}; task :ordinary")
    assert [task.name for task in tasks] == ["ordinary"]


def test_dynamic_begin_overrides_disable_later_initializer_tasks():
    tasks = parse_rakefile(
        "BEGIN { task :bootstrap }; "
        "BEGIN { def self.task(*args); end } if condition; "
        "BEGIN { task :hidden }; task :ordinary"
    )
    assert [task.name for task in tasks] == ["bootstrap"]


@pytest.mark.parametrize("method", ["task", "multitask"])
@pytest.mark.parametrize(
    "arguments",
    [
        "{build: :test}",
        "{:build => [:test]}",
        '{"build" => :test}',
        "{build: :test, order_only: :prepare}",
        "build: :test, order_only: :prepare",
    ],
)
def test_literal_task_name_hashes_are_discovered(method, arguments):
    tasks = parse_rakefile(f"{method}({arguments})")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("method", ["task", "multitask"])
@pytest.mark.parametrize(
    "description",
    [":Build", ':"Build"', "123", "1.5", "true", "[]", "{}", "/Build/", "( :Build )"],
)
def test_known_invalid_descriptions_reject_whole_file(method, description):
    assert parse_rakefile(f"desc({description}); {method} :hidden; task :safe") == []


@pytest.mark.parametrize("description", ["nil", "false", '"Build"', '("Build")'])
def test_valid_description_types_preserve_tasks(description):
    tasks = parse_rakefile(f"desc {description}; task :build")
    assert [(task.name, task.description) for task in tasks] == [
        ("build", "Build" if "Build" in description else None)
    ]


@pytest.mark.parametrize(
    "name",
    ["task", "multitask", "namespace", "desc", ":task", ':"task"', ":task, :desc"],
)
@pytest.mark.parametrize(
    "wrapper",
    ["{}", "( {} )", "begin; {}; end", "BEGIN {{ {} }}", "namespace :db do; {}; end"],
)
def test_direct_undef_of_rake_dsl_rejects_file(name, wrapper, caplog):
    body = wrapper.format(f"undef {name}")
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "undef of Rake DSL" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "def helper; undef task; end",
        "class C; undef task; end",
        "proc { undef task }",
        "END { undef task }",
    ],
)
def test_deferred_or_opaque_undef_does_not_hide_tasks(body):
    assert [t.name for t in parse_rakefile(f"{body}; task :build")] == ["build"]


@pytest.mark.parametrize(
    "body",
    [
        "def helper; yield(&callback); end",
        "def helper; defined?(yield(&callback)); end",
        "def helper(&); yield(&); end",
        "def helper(...); yield(...); end",
        "proc { redo() }",
        "proc { redo(1) }",
        "begin; rescue; retry(); end",
        "begin; rescue; retry(1); end",
        "defined?(redo(1))",
        "defined?(retry())",
        "def helper; return(&callback); end",
        "proc { next(&callback) }",
        "while true; break(&callback); end",
        "def helper; return(**options); end",
        "proc { next(**options) }",
        "while true; break(**options); end",
        "def helper(...); return(...); end",
        "def helper(*); return(*); end",
        "def helper(**); return(**); end",
        "def helper(*); proc { next(*) }; end",
        "def helper(*); while true; break(*); end; end",
        "def helper; return(foo: 1); end",
        "proc { next(foo: 1) }",
        "while true; break(foo: 1); end",
        "def helper; return(*values); end",
        "proc { next(*values) }",
        "while true; break(*values); end",
        "def helper; return(1, 2); end",
        "proc { next(1, 2) }",
        "while true; break(1, 2); end",
    ],
)
def test_invalid_control_arguments_reject_whole_file(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "arguments" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "def helper; return(); end",
        "proc { next() }",
        "while true; break(); end",
        "def helper; yield(); end",
        "def helper; yield({a: 1}); end",
        "def helper; yield **options; end",
        "def helper(*); yield(*); end",
        "def helper(**); yield(**); end",
        "def helper; return({a: 1}); end",
        "proc { next({a: 1}) }",
        "while true; break({a: 1}); end",
        "def helper; return *values; end",
        "proc { next *values }",
        "while true; break *values; end",
        "def helper; return 1, 2; end",
        "proc { next 1, 2 }",
        "while true; break 1, 2; end",
    ],
)
def test_valid_control_arguments_preserve_discovery(body):
    assert [t.name for t in parse_rakefile(f"{body}; task :build")] == ["build"]


@pytest.mark.parametrize("method", ["task", "multitask"])
@pytest.mark.parametrize(
    "arguments", [":bad, bar: :baz", ":bad, {}", ":bad, 123", "{one: :dep, two: :dep}"]
)
def test_statically_invalid_task_calls_reject_file(method, arguments, caplog):
    assert parse_rakefile(f"task :before; {method}({arguments}); task :after") == []
    assert "invalid Rake task" in caplog.text


@pytest.mark.parametrize(
    "declaration",
    [
        "namespace :broken",
        "namespace(:broken)",
        "namespace(123) {}",
        "namespace(true) {}",
        "namespace(false) {}",
        "namespace(:one, :two) {}",
        "namespace(:broken, &nil)",
    ],
)
def test_statically_invalid_namespaces_reject_file(declaration, caplog):
    assert parse_rakefile(f"task :before; {declaration}; task :after") == []
    assert "invalid Rake namespace" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        'desc "Good"; nil; task :build',
        'desc "Good"; namespace :db do; task :build; end',
        'namespace :db do; desc "Good"; end; task :build',
        'desc "Good"; rule ".o" => ".c"; task :build',
    ],
)
def test_pending_descriptions_follow_rake_global_metadata(body):
    tasks = parse_rakefile(body)
    assert [(task.name, task.description) for task in tasks] == [
        ("db:build" if "task :build; end" in body else "build", "Good")
    ]


@pytest.mark.parametrize(
    "body",
    [
        "desc :Bad; nil; task :build",
        "desc :Bad; namespace :db do; task :build; end",
        "namespace :db do; desc :Bad; end; task :build",
        'desc :Bad; file "output"; task :build',
    ],
)
def test_pending_invalid_descriptions_reject_file(body, caplog):
    assert parse_rakefile(f"task :before; {body}") == []
    assert "invalid Rake description" in caplog.text


def test_dynamic_task_argument_tail_remains_opaque():
    tasks = parse_rakefile("task(:hidden, **options); task :safe")
    assert [task.name for task in tasks] == ["safe"]


@pytest.mark.parametrize(
    "declaration",
    [
        "task(:hidden, options => :dep)",
        "task({**options})",
        "task(:hidden, first => :dep, second => :dep)",
        "task(:hidden, first => :dep, second => :dep, third => :dep)",
        "task()",
        "namespace {}",
        "namespace(:db, &callback)",
        "namespace(*names) {}",
    ],
)
def test_unknown_declaration_shapes_are_opaque(declaration):
    tasks = parse_rakefile(f"{declaration}; task :safe")
    assert [task.name for task in tasks] == ["safe"]


@pytest.mark.parametrize(
    "declaration", ["desc()", 'desc("First", "Second")', "desc {}"]
)
def test_statically_wrong_description_arity_rejects_file(declaration, caplog):
    assert parse_rakefile(f"task :before; {declaration}; task :after") == []
    assert "invalid Rake description arguments" in caplog.text


@pytest.mark.parametrize("condition", ["(true)", "((true))"])
def test_parenthesized_active_initializer_conditions_preserve_declarations(condition):
    tasks = parse_rakefile(
        f"BEGIN {{ task :bootstrap }} if {condition}; task :ordinary"
    )
    assert [task.name for task in tasks] == ["bootstrap", "ordinary"]


@pytest.mark.parametrize("condition", ["(false)", "(flag)", "(true; flag)"])
def test_parenthesized_inactive_or_dynamic_initializer_conditions_are_opaque(condition):
    tasks = parse_rakefile(f"BEGIN {{ task :hidden }} if {condition}; task :ordinary")
    assert [task.name for task in tasks] == ["ordinary"]


@pytest.mark.parametrize(
    "arguments",
    [
        "{build: :first, build: :second}",
        "build: :first, build: :second, build: :third",
        ":build, {[:mode] => :first, [:mode] => :second}",
        ":build, [:mode] => :first, [:mode] => :second",
    ],
)
def test_duplicate_dependency_keys_follow_ruby_hash_semantics(arguments):
    assert [task.name for task in parse_rakefile(f"task({arguments})")] == ["build"]


@pytest.mark.parametrize(
    "conditional",
    [
        "if true; def self.task(*args); end; end",
        "unless false; def self.task(*args); end; end",
        "def self.task(*args); end if true",
        "if condition; def self.task(*args); end; end",
    ],
)
def test_top_level_conditional_overrides_preserve_source_order(conditional):
    tasks = parse_rakefile(f"task :before; {conditional}; task :hidden")
    assert [task.name for task in tasks] == ["before"]


@pytest.mark.parametrize(
    "conditional",
    [
        "if false; def self.task(*args); end; end",
        "unless true; def self.task(*args); end; end",
        "def self.task(*args); end if false",
    ],
)
def test_inactive_top_level_conditional_overrides_preserve_tasks(conditional):
    tasks = parse_rakefile(f"task :before; {conditional}; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize("control", ["break", "next", "redo", "return"])
@pytest.mark.parametrize(
    "wrapper",
    ["{} if false", "if false; {}; end", "{} unless true", "while false; {}; end"],
)
def test_inactive_namespace_controls_preserve_later_tasks(control, wrapper):
    body = wrapper.format(control)
    tasks = parse_rakefile(
        f"namespace :db do; task :before; {body}; task :after; end; task :root"
    )
    assert [task.name for task in tasks] == ["db:before", "db:after", "root"]


@pytest.mark.parametrize("control", ["break", "next"])
def test_active_namespace_controls_still_stop_namespace(control):
    tasks = parse_rakefile(
        f"namespace :db do; task :before; {control} if true; "
        "task :hidden; end; task :root"
    )
    assert [task.name for task in tasks] == ["db:before", "root"]


@pytest.mark.parametrize(
    "expression",
    [
        "false && return",
        "nil && return",
        "false and return",
        "true || return",
        "true or return",
        "0 || return",
        '"" || return',
        "[] || return",
        "(false) && return",
        "(true) || return",
    ],
)
def test_literal_short_circuits_preserve_later_tasks(expression):
    tasks = parse_rakefile(f"task :before; {expression}; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "expression",
    [
        "false && (def self.task(*args); end)",
        "false and (def self.task(*args); end)",
        "true || (def self.task(*args); end)",
        "true or (def self.task(*args); end)",
        '"" || (def self.task(*args); end)',
    ],
)
def test_literal_short_circuits_do_not_install_unreachable_overrides(expression):
    tasks = parse_rakefile(f"task :before; {expression}; task :after")
    assert [task.name for task in tasks] == ["before", "after"]


@pytest.mark.parametrize(
    "expression", ["true && return", "false || return", "condition && return"]
)
def test_active_or_dynamic_short_circuit_controls_remain_conservative(expression):
    tasks = parse_rakefile(f"task :before; {expression}; task :hidden")
    assert [task.name for task in tasks] == ["before"]


def test_short_circuit_left_operand_effects_are_still_scanned():
    tasks = parse_rakefile(
        "task :before; [def self.task(*args); end] || return; task :hidden"
    )
    assert [task.name for task in tasks] == ["before"]


@pytest.mark.parametrize("expression", ["false && return", "true || return"])
def test_unreachable_initializer_short_circuit_controls_preserve_tasks(expression):
    tasks = parse_rakefile(f"BEGIN {{ {expression} }}; task :build")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "body",
    [
        "def helper(options); return **options; end",
        "proc { next **options }",
        "while true; break **options; end",
        "def helper(**); return **; end",
        "def helper(**); while true; break **; end; end",
        "def helper(*); return *; end",
    ],
)
def test_unparenthesized_control_splats_are_valid(body):
    assert [task.name for task in parse_rakefile(f"{body}; task :build")] == ["build"]


@pytest.mark.parametrize(
    "body",
    [
        "class << (def self.task(*args); end; self); end",
        "class C < (def self.task(*args); end; Object); end",
    ],
)
def test_load_time_class_and_method_headers_install_dsl_overrides(body):
    tasks = parse_rakefile(f"task :before; {body}; task :hidden")
    assert [task.name for task in tasks] == ["before"]


@pytest.mark.parametrize(
    "body",
    [
        "value = /#{pattern}/",
        "namespace :db do; value = /#{pattern}/; end",
        "class C; value = /#{pattern}/; end",
        "BEGIN { value = /#{pattern}/ }",
        "[1].each { /#{pattern}/ }",
    ],
)
def test_load_time_interpolated_regexps_are_conservatively_rejected(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "interpolated regexp during loading" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "def helper; value = /#{pattern}/; end",
        "task :hidden do; value = /#{pattern}/; end",
        "callback = -> { /#{pattern}/ }",
        "callback = proc { /#{pattern}/ }",
        "END { /#{pattern}/ }",
        "if false; value = /#{pattern}/; end",
        "false && /#{pattern}/",
        "defined?(/#{pattern}/)",
    ],
)
def test_deferred_or_inactive_interpolated_regexps_preserve_tasks(body):
    tasks = parse_rakefile(f"{body}; task :build")
    assert [task.name for task in tasks] == (
        ["hidden", "build"] if "task :hidden" in body else ["build"]
    )


@pytest.mark.parametrize(
    "body",
    [
        "def helper(**); proc { next ** }; end",
        "def helper(*); proc { next * }; end",
        "def (def self.task(*args); end; self).helper; end",
    ],
)
def test_unrecognized_ruby_grammar_remains_conservatively_skipped(body, caplog):
    assert parse_rakefile(f"{body}; task :build") == []
    assert "invalid Ruby syntax" in caplog.text


@pytest.mark.parametrize("receiver", ["self", "(self)", "((self))"])
def test_explicit_self_rake_dsl_calls_are_discovered(receiver):
    tasks = parse_rakefile(
        f'{receiver}.desc "Build"; {receiver}.task :build; '
        f"{receiver}.namespace :db do; {receiver}.multitask :test; end"
    )
    assert [(task.name, task.description) for task in tasks] == [
        ("build", "Build"),
        ("db:test", None),
    ]


def test_explicit_self_calls_respect_dsl_overrides():
    tasks = parse_rakefile(
        "self.task :before; def self.task(*args); end; self.task :hidden"
    )
    assert [task.name for task in tasks] == ["before"]


def test_explicit_self_task_blocks_keep_interpolated_regexps_deferred():
    tasks = parse_rakefile("self.task :build do; /#{pattern}/; end")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "value", ["1", "1.5", "-1", "true", "false", '"text"', "[]", "/pattern/", "1..2"]
)
def test_non_coercible_block_arguments_reject_file(value, caplog):
    assert parse_rakefile(f"task :before; task(:bad, &{value}); task :after") == []
    assert "block argument" in caplog.text


@pytest.mark.parametrize("value", ["nil", ":to_s", "{}", "-> {}", "callback"])
def test_coercible_or_unknown_task_blocks_preserve_names(value):
    tasks = parse_rakefile(f"task(:build, &{value}); task :after")
    assert [task.name for task in tasks] == ["build", "after"]


@pytest.mark.parametrize(
    "arguments", ["foo: :bar", "foo: :bar, other: :value", "{foo: :bar}"]
)
def test_keyword_hash_namespace_names_reject_file(arguments, caplog):
    assert (
        parse_rakefile(f"task :before; namespace({arguments}) {{}}; task :after") == []
    )
    assert "invalid Rake namespace" in caplog.text


@pytest.mark.parametrize("arguments", ["foo: :bar", "foo: :bar, other: :value"])
def test_keyword_hash_descriptions_fail_only_when_consumed(arguments, caplog):
    assert [
        task.name for task in parse_rakefile(f"task :before; desc({arguments})")
    ] == ["before"]
    assert parse_rakefile(f"task :before; desc({arguments}); task :after") == []
    assert "invalid Rake description type" in caplog.text


@pytest.mark.parametrize("method", ["directory", "file_create"])
@pytest.mark.parametrize("receiver", ["", "self."])
def test_file_creation_blocks_keep_runtime_checks_deferred(method, receiver):
    tasks = parse_rakefile(
        f'{receiver}{method} "out" do; /#{{pattern}}/; helper(&1); end; task :safe'
    )
    assert [task.name for task in tasks] == ["safe"]


@pytest.mark.parametrize("method", ["directory", "file_create"])
def test_file_creation_helpers_consume_pending_descriptions(method):
    tasks = parse_rakefile(f'desc "Directory"; {method} "out"; task :safe')
    assert [(task.name, task.description) for task in tasks] == [("safe", None)]


@pytest.mark.parametrize("method", ["directory", "file_create"])
def test_file_creation_helpers_reject_invalid_pending_descriptions(method, caplog):
    assert parse_rakefile(f'task :before; desc :Bad; {method} "out"') == []
    assert "invalid Rake description type" in caplog.text


def test_rules_preserve_invalid_pending_descriptions_until_next_task(caplog):
    assert parse_rakefile('desc :Bad; rule ".o" => ".c"; task :safe') == []
    assert "invalid Rake description type" in caplog.text


@pytest.mark.parametrize(
    "path", ["", "nil", ":out", "1", "true", "[]", ":out => []", "{out: []}"]
)
def test_invalid_directory_paths_abort_loading(path, caplog):
    assert parse_rakefile(f"task :before; directory({path}); task :safe") == []
    assert "invalid Rake task arguments" in caplog.text


@pytest.mark.parametrize(
    "path", ['"out"', '""', '"out" => []', '{"out" => []}', "path", "*paths"]
)
def test_valid_or_unknown_directory_paths_preserve_tasks(path):
    assert [task.name for task in parse_rakefile(f"directory({path}); task :safe")] == [
        "safe"
    ]


@pytest.mark.parametrize(
    "method",
    ["task", "multitask", "file", "file_create", "directory", "rule", "proc", "lambda"],
)
@pytest.mark.parametrize("receiver", ["", "self."])
def test_overridden_deferred_helpers_validate_their_blocks(method, receiver, caplog):
    source = (
        f"task :before; def self.{method}(*); yield; end; "
        f"{receiver}{method} {{ /#{{pattern}}/ }}"
    )
    assert parse_rakefile(source) == []
    assert "unsupported interpolated regexp during loading" in caplog.text


def test_later_override_does_not_execute_earlier_task_block():
    tasks = parse_rakefile(
        "task :safe do; /#{pattern}/; end; def self.task(*); yield; end"
    )
    assert [task.name for task in tasks] == ["safe"]


def test_begin_override_makes_earlier_source_block_load_time():
    assert (
        parse_rakefile(
            "task :safe do; /#{pattern}/; end; BEGIN { def self.task(*); yield; end }"
        )
        == []
    )


def test_deferred_task_block_does_not_install_override():
    tasks = parse_rakefile(
        "task :safe do; def self.task(*); yield; end; end; "
        "task :next do; /#{pattern}/; end"
    )
    assert [task.name for task in tasks] == ["safe", "next"]


@pytest.mark.parametrize(
    "body",
    [
        'value = /#{")"}/',
        'task :hidden do; /#{")"}/; end',
        'def helper; /#{")"}/; end',
        'callback = -> { /#{")"}/ }',
        'false && /#{")"}/',
    ],
)
def test_constant_string_interpolated_regexps_are_compile_time_errors(body, caplog):
    assert parse_rakefile(f"{body}; task :safe") == []
    assert "invalid or unsupported Ruby regexp" in caplog.text


def test_valid_constant_string_interpolation_inside_deferred_task():
    assert [task.name for task in parse_rakefile('task :safe do; /#{"ok"}/; end')] == [
        "safe"
    ]


@pytest.mark.parametrize(
    "body",
    [
        'if false; /#{")"}/; end',
        'unless true; /#{")"}/; end',
        '/#{")"}/ if false',
        'true ? /ok/ : /#{")"}/',
    ],
)
def test_discarded_constant_interpolated_regexps_preserve_tasks(body):
    assert [task.name for task in parse_rakefile(f"{body}; task :safe")] == ["safe"]


@pytest.mark.parametrize(
    "callback",
    [
        "-> {}",
        "->(a,b) {}",
        "->(a:) {}",
        "->(**kw) {}",
        "lambda {}",
        "lambda { |a,b| }",
        "Kernel.lambda {}",
    ],
)
def test_namespace_callbacks_reject_known_strict_arity(callback, caplog):
    assert parse_rakefile(f"namespace(:broken, &{callback}); task :after") == []
    assert "invalid Rake namespace call" in caplog.text


@pytest.mark.parametrize(
    "callback",
    [
        "->(n) {}",
        "->(n=1) {}",
        "->(*args) {}",
        "->(n, **kw) {}",
        "-> { _1 }",
        "lambda { |n| }",
        "proc {}",
        "callback",
    ],
)
def test_namespace_callbacks_accept_valid_or_unknown_arity(callback):
    assert [
        task.name
        for task in parse_rakefile(f"namespace(:ok, &{callback}); task :after")
    ] == ["after"]


@pytest.mark.parametrize(
    "constructor",
    [
        "Kernel.proc",
        "::Kernel.proc",
        "Kernel.lambda",
        "::Kernel.lambda",
        "Proc.new",
        "::Proc.new",
    ],
)
def test_canonical_proc_constructors_defer_blocks(constructor):
    tasks = parse_rakefile(
        f"{constructor} {{ /#{{pattern}}/; helper(&1) }}; task :safe"
    )
    assert [task.name for task in tasks] == ["safe"]


@pytest.mark.parametrize("constructor", ["Kernel.proc", "Kernel.lambda", "Proc.new"])
def test_overridden_proc_constructors_validate_blocks(constructor):
    assert (
        parse_rakefile(
            f"task :before; def {constructor}(*); yield; end; "
            f"{constructor} {{ /#{{pattern}}/ }}"
        )
        == []
    )


@pytest.mark.parametrize("callback", ["-> { _2 }", "-> { proc { _1 } }"])
def test_namespace_callbacks_reject_incompatible_implicit_arity(callback):
    assert parse_rakefile(f"namespace(:broken, &{callback}); task :after") == []


@pytest.mark.parametrize(
    "callback", ["lambda { |arg; temp| }", "->((a,b)) {}", "->(a=1,b=2) {}"]
)
def test_namespace_lambda_arity_ignores_block_locals_and_accepts_destructuring(
    callback,
):
    assert [
        task.name
        for task in parse_rakefile(f"namespace(:ok, &{callback}); task :after")
    ] == ["after"]


def test_overridden_lambda_constructor_has_unknown_callback_arity():
    source = (
        "def self.lambda(&block); proc { |arg| }; end; "
        "namespace(:ok, &lambda {}); task :after"
    )
    assert [task.name for task in parse_rakefile(source)] == ["after"]


@pytest.mark.parametrize(
    "wrapper",
    [
        "if true; {call}; end",
        "unless false; {call}; end",
        "{call} if true",
        "true && ({call})",
        "namespace :db do; if true; {call}; end; end",
    ],
)
@pytest.mark.parametrize(
    "call", ["namespace(foo: :bar) {}", "task :bad, [1]", "directory nil", "desc 1, 2"]
)
def test_active_branches_validate_dsl_declarations(wrapper, call, caplog):
    assert (
        parse_rakefile(f"task :before; {wrapper.format(call=call)}; task :after") == []
    )
    assert "invalid Rake" in caplog.text


@pytest.mark.parametrize(
    "wrapper",
    [
        "if false; {call}; end",
        "unless true; {call}; end",
        "{call} if false",
        "false && ({call})",
    ],
)
def test_inactive_branches_do_not_validate_dsl_calls(wrapper):
    source = wrapper.format(call="namespace(foo: :bar) {}")
    assert [task.name for task in parse_rakefile(f"{source}; task :after")] == ["after"]


def test_overridden_dsl_in_active_branch_is_not_validated():
    source = (
        "def self.namespace(*); end; if true; namespace(foo: :bar) {}; end; task :after"
    )
    assert [task.name for task in parse_rakefile(source)] == ["after"]


def test_class_body_dsl_method_has_unknown_implementation():
    source = (
        "class Example; def self.namespace(*); end; "
        "namespace(foo: :bar) {}; end; task :after"
    )
    assert [task.name for task in parse_rakefile(source)] == ["after"]


@pytest.mark.parametrize("receiver", ["", "self."])
@pytest.mark.parametrize("name", [":task", '"task"'])
def test_literal_singleton_method_definitions_override_dsl(receiver, name):
    source = (
        f"task :before; {receiver}define_singleton_method({name}) {{ |*| }}; "
        "task :ghost"
    )
    assert [task.name for task in parse_rakefile(source)] == ["before"]


@pytest.mark.parametrize(
    "constructor",
    [
        "define_method",
        "self.define_method",
        "define_singleton_method",
        "self.define_singleton_method",
    ],
)
def test_method_definition_blocks_are_deferred(constructor):
    tasks = parse_rakefile(
        f"{constructor}(:helper) {{ /#{{pattern}}/; helper(&1) }}; task :safe"
    )
    assert [task.name for task in tasks] == ["safe"]


def test_singleton_method_override_in_begin_applies_before_tasks():
    assert (
        parse_rakefile("task :ghost; BEGIN { define_singleton_method(:task) { |*| } }")
        == []
    )


@pytest.mark.parametrize(
    "source",
    [
        "case {}; in {a: x, a: y}; end",
        'case {}; in {"a": x, a: y}; end',
        "{} => {a: x, a: y}",
        "{} in {a: x, a: y}",
    ],
)
def test_duplicate_hash_pattern_keys_are_compile_time_errors(source, caplog):
    assert parse_rakefile(f"{source}; task :safe") == []
    assert "duplicated hash pattern key" in caplog.text


def test_nested_hash_patterns_validate_keys_independently():
    assert [
        task.name for task in parse_rakefile("case {}; in {a: {a: x}}; end; task :safe")
    ] == ["safe"]


def test_defining_method_constructor_itself_keeps_definition_block_deferred():
    tasks = parse_rakefile(
        "define_singleton_method(:define_singleton_method) { /#{pattern}/ }; task :safe"
    )
    assert [task.name for task in tasks] == ["safe"]


def test_overridden_method_constructor_validates_later_blocks():
    assert (
        parse_rakefile(
            "task :before; def self.define_singleton_method(*); yield; end; "
            "define_singleton_method(:helper) { /#{pattern}/ }"
        )
        == []
    )


@pytest.mark.parametrize(
    "call",
    [
        "proc",
        "lambda",
        "Proc.new",
        "Kernel.proc(1) {}",
        "proc(&nil)",
        "lambda(1) {}",
        "Proc.new(1) {}",
        "define_method() {}",
        "define_singleton_method(:helper)",
        "define_method(:helper, nil)",
        "define_singleton_method(1) {}",
        "define_singleton_method(:helper, :body)",
    ],
)
def test_invalid_deferred_constructor_calls_abort_loading(call, caplog):
    assert parse_rakefile(f"task :before; {call}; task :after") == []
    assert "invalid" in caplog.text


@pytest.mark.parametrize(
    "call",
    [
        "proc {}",
        "lambda {}",
        "Proc.new {}",
        "proc(&callback)",
        "proc(*args) {}",
        "define_method(:helper, -> {})",
        "define_singleton_method(:helper, method(:existing))",
        "define_method(*args) {}",
        "define_singleton_method(name) {}",
    ],
)
def test_valid_or_unknown_deferred_constructor_calls_preserve_tasks(call):
    assert [task.name for task in parse_rakefile(f"{call}; task :safe")] == ["safe"]


def test_overridden_proc_constructor_signature_is_unknown():
    source = "def self.proc(*); end; proc(1); task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "source",
    [
        "proc = 1; proc",
        "lambda = 1; lambda",
        "namespace :db do |proc|; proc; end",
        "task :later do; proc; end",
        "def self.proc; 1; end; proc",
    ],
)
def test_bare_constructor_names_respect_bindings_and_deferred_scopes(source):
    assert [task.name for task in parse_rakefile(f"{source}; task :safe")][-1] == "safe"


@pytest.mark.parametrize("name", ["file", "directory", "file_create", "rule"])
def test_undefining_other_rake_helpers_aborts_loading(name, caplog):
    assert parse_rakefile(f"task :before; undef {name}; task :after") == []
    assert "undef of Rake DSL method" in caplog.text


@pytest.mark.parametrize("name", ["proc", "lambda", "define_singleton_method"])
def test_ordinary_kernel_constructor_overrides_validate_blocks(name):
    source = f"task :before; def {name}(*); yield; end; {name} {{ /#{{pattern}}/ }}"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "body",
    [
        "class Example; def proc; yield; end; end",
        "module Example; def lambda; yield; end; end",
        "task :later do; def proc; yield; end; end",
    ],
)
def test_opaque_ordinary_constructor_definitions_do_not_override_main(body):
    assert [
        task.name
        for task in parse_rakefile(f"{body}; proc {{ /#{{pattern}}/ }}; task :safe")
    ][-1] == "safe"


def test_description_blocks_remain_deferred():
    tasks = parse_rakefile('desc("Safe") { /#{pattern}/; helper(&1) }; task :safe')
    assert [(task.name, task.description) for task in tasks] == [("safe", "Safe")]


def test_overridden_description_method_validates_its_block():
    assert (
        parse_rakefile(
            'task :before; def self.desc(*); yield; end; desc("Safe") { /#{pattern}/ }'
        )
        == []
    )


@pytest.mark.parametrize(
    ("body", "call"),
    [
        ("class Proc; def self.new(*); yield; end; end", "Proc.new"),
        ("class << Proc; def new(*); yield; end; end", "Proc.new"),
        ("module Kernel; def self.proc(*); yield; end; end", "Kernel.proc"),
        ("module Kernel; def self.lambda(*); yield; end; end", "Kernel.lambda"),
        (
            "class Proc; define_singleton_method(:new) { |&block| block.call }; end",
            "Proc.new",
        ),
    ],
)
def test_qualified_constructor_self_overrides_validate_blocks(body, call):
    assert parse_rakefile(f"task :before; {body}; {call} {{ /#{{pattern}}/ }}") == []


def test_kernel_singleton_override_does_not_replace_bare_proc():
    source = (
        "module Kernel; def self.proc(*); yield; end; end; "
        "proc { /#{pattern}/ }; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "body",
    ["super", "super(1)", "namespace :db do; super; end", "class Example; super; end"],
)
def test_load_time_super_aborts_discovery(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "super outside method" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "defined?(super)",
        "task :deferred do; super; end",
        "def helper; super; end",
        "if false; super; end",
    ],
)
def test_deferred_or_unreachable_super_preserves_tasks(body):
    assert [task.name for task in parse_rakefile(f"{body}; task :safe")][-1] == "safe"


@pytest.mark.parametrize("method", ["proc", "lambda", "define_singleton_method"])
def test_kernel_instance_constructor_overrides_validate_blocks(method):
    source = (
        f"task :before; module Kernel; def {method}(*); yield; end; end; "
        f"{method} {{ /#{{pattern}}/ }}"
    )
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "body",
    [
        "class Helper; class << self; end; end",
        "class Helper; def self.task(*); end; end",
        "class Helper; define_singleton_method(:task) { |*| }; end",
    ],
)
def test_nested_self_definitions_do_not_disable_main_dsl(body):
    assert [
        task.name
        for task in parse_rakefile(f"{body}; task(:safe) {{ /#{{pattern}}/ }}")
    ] == ["safe"]


def test_kernel_nested_singleton_scope_disables_qualified_constructor():
    source = (
        "module Kernel; class << self; def proc(*); yield; end; end; end; "
        "Kernel.proc { /#{pattern}/ }"
    )
    assert parse_rakefile(f"task :before; {source}") == []


@pytest.mark.parametrize(
    ("body", "call"),
    [
        ("module Kernel; define_method(:proc) { |&block| block.call }; end", "proc"),
        (
            "module Kernel; define_method(:lambda) { |&block| block.call }; end",
            "lambda",
        ),
        ("class << Proc; define_method(:new) { |&block| block.call }; end", "Proc.new"),
        ("define_method(:proc) { |&block| block.call }", "proc"),
    ],
)
def test_dynamic_instance_constructor_overrides_validate_blocks(body, call):
    assert parse_rakefile(f"task :before; {body}; {call} {{ /#{{pattern}}/ }}") == []


@pytest.mark.parametrize("name", ["proc", "lambda", "define_singleton_method"])
def test_undefined_constructors_reject_later_direct_calls(name):
    assert parse_rakefile(f"task :before; undef {name}; {name} {{}}; task :safe") == []


@pytest.mark.parametrize("name", ["proc", "lambda"])
def test_undefined_constructors_reject_later_bare_calls(name):
    assert parse_rakefile(f"undef {name}; {name}; task :safe") == []


@pytest.mark.parametrize(
    "restore",
    [
        "def proc(*); end",
        "def self.proc(*); end",
        "define_method(:proc) { |*| }",
        "define_singleton_method(:proc) { |*| }",
    ],
)
def test_direct_definition_restores_undefined_constructor(restore):
    assert [
        task.name
        for task in parse_rakefile(f"undef proc; {restore}; proc {{}}; task :safe")
    ] == ["safe"]


def test_undefined_qualified_constructor_rejects_later_call():
    assert (
        parse_rakefile("class << Proc; undef new; end; Proc.new {}; task :safe") == []
    )


@pytest.mark.parametrize(
    "header",
    [
        "# encoding: ISO-8859-1",
        "# coding= ISO8859-1",
        "# -*- coding: ISO-8859-1 -*-",
        '# encoding: "ISO-8859-1"',
        "#!/usr/bin/ruby\n# encoding: ISO-8859-1",
    ],
)
def test_source_encoding_declarations_decode_descriptions(tmp_path, header):
    (tmp_path / "Rakefile").write_bytes(
        f'{header}\ndesc "café"; task :build\n'.encode("latin-1")
    )
    assert [
        (task.name, task.description) for task in RakeProvider().discover(tmp_path)
    ] == [("build", "café")]


@pytest.mark.parametrize(
    ("encoding", "codec", "description"),
    [
        ("Windows-1252", "cp1252", "€ café"),
        ("Windows-31J", "cp932", "日本語"),
        ("Shift_JIS", "shift_jis", "日本語"),
        ("ASCII-8BIT", "latin-1", "café"),
    ],
)
def test_common_ascii_compatible_ruby_encodings(tmp_path, encoding, codec, description):
    source = f'# encoding: {encoding}\ndesc "{description}"; task :build\n'
    (tmp_path / "Rakefile").write_bytes(source.encode(codec))
    assert [
        (task.name, task.description) for task in RakeProvider().discover(tmp_path)
    ] == [("build", description)]


@pytest.mark.parametrize(
    "encoding",
    [
        "unknown-codec",
        "UTF-16",
        "UTF-7",
        "ISO-2022-JP",
        "latin1",
        "utf8",
        "ISO-8859-99",
    ],
)
def test_unsupported_source_encodings_skip_file(tmp_path, caplog, encoding):
    (tmp_path / "Rakefile").write_bytes(
        f"# encoding: {encoding}\ntask :safe\n".encode()
    )
    assert RakeProvider().discover(tmp_path) == []
    assert "skipping Rakefile" in caplog.text


def test_second_line_encoding_requires_shebang(tmp_path, caplog):
    (tmp_path / "Rakefile").write_bytes(
        b'# ordinary comment\n# encoding: ISO-8859-1\ndesc "calf\xe9"; task :build\n'
    )
    assert RakeProvider().discover(tmp_path) == []
    assert "skipping Rakefile" in caplog.text


def test_utf8_bom_is_removed_before_parsing(tmp_path):
    (tmp_path / "Rakefile").write_bytes(b"\xef\xbb\xbftask :safe\n")
    assert [task.name for task in RakeProvider().discover(tmp_path)] == ["safe"]


def test_equals_encoding_comment_without_separator_is_ignored(tmp_path, caplog):
    (tmp_path / "Rakefile").write_bytes(
        b'# coding=ISO-8859-1\ndesc "calf\xe9"; task :build\n'
    )
    assert RakeProvider().discover(tmp_path) == []
    assert "skipping Rakefile" in caplog.text


@pytest.mark.parametrize(
    "receiver", ["singleton_class", "self.singleton_class", "(singleton_class)"]
)
def test_singleton_class_literal_method_definition_overrides_dsl(receiver):
    source = f"task :before; {receiver}.define_method(:task) {{ |*| }}; task :ghost"
    assert [task.name for task in parse_rakefile(source)] == ["before"]


def test_singleton_class_definition_blocks_are_deferred():
    tasks = parse_rakefile(
        "singleton_class.define_method(:helper) { /#{pattern}/ }; task :safe"
    )
    assert [task.name for task in tasks] == ["safe"]


def test_local_singleton_class_receiver_does_not_override_main():
    source = (
        "singleton_class = other; singleton_class.define_method(:task) { |*| }; "
        "task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "source", ['task "é"; task :safe', 'namespace "é" do; task :build; end; task :safe']
)
def test_non_utf8_non_ascii_task_names_are_omitted(tmp_path, caplog, source):
    (tmp_path / "Rakefile").write_bytes(
        ("# encoding: ISO-8859-1\n" + source).encode("latin-1")
    )
    assert [task.name for task in RakeProvider().discover(tmp_path)] == ["safe"]
    assert "cannot be reproduced safely" in caplog.text


def test_utf8_non_ascii_task_names_are_preserved(tmp_path):
    (tmp_path / "Rakefile").write_bytes('# encoding: UTF-8\ntask "é"'.encode())
    assert [task.name for task in RakeProvider().discover(tmp_path)] == ["é"]


def test_overridden_singleton_class_receiver_is_not_assumed_to_be_main():
    source = (
        "def singleton_class; Module.new; end; "
        "singleton_class.define_method(:task) { |*| }; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_fileencoding_colon_does_not_declare_source_encoding(tmp_path, caplog):
    (tmp_path / "Rakefile").write_bytes(
        '# fileencoding: ISO-8859-1\ndesc "café"; task :safe'.encode("latin-1")
    )
    assert RakeProvider().discover(tmp_path) == []
    assert "skipping Rakefile" in caplog.text


def test_fileencoding_equals_declaration_decodes_source(tmp_path):
    (tmp_path / "Rakefile").write_bytes(
        '# fileencoding= ISO-8859-1\ndesc "café"; task :safe'.encode("latin-1")
    )
    assert [
        (task.name, task.description) for task in RakeProvider().discover(tmp_path)
    ] == [("safe", "café")]


@pytest.mark.parametrize(
    "receiver",
    ["self.singleton_class()", "singleton_class()", "(self.singleton_class())"],
)
def test_empty_argument_singleton_class_receivers_override_dsl(receiver):
    source = f"task :before; {receiver}.define_method(:task) {{ |*| }}; task :ghost"
    assert [task.name for task in parse_rakefile(source)] == ["before"]


@pytest.mark.parametrize("receiver", ["self.singleton_class()", "singleton_class()"])
def test_empty_argument_singleton_class_method_bodies_are_deferred(receiver):
    source = f"{receiver}.define_method(:helper) {{ /#{{pattern}}/ }}; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


_READONLY_GLOBALS = [
    "$?",
    "$!",
    "$:",
    "$<",
    '$"',
    "$$",
    "$*",
    "$LOAD_PATH",
    "$LOADED_FEATURES",
    "$FILENAME",
    "$-I",
    "$-W",
    "$-a",
    "$-l",
    "$-p",
]


@pytest.mark.parametrize("target", _READONLY_GLOBALS)
@pytest.mark.parametrize(
    "form", ["{target} = nil", "{target} += 1", "other, {target} = 1, nil"]
)
def test_readonly_special_global_assignments_reject_loading(target, form, caplog):
    source = f"task :before; {form.format(target=target)}; task :after"
    assert parse_rakefile(source) == []
    assert "readonly global" in caplog.text


@pytest.mark.parametrize("target", _READONLY_GLOBALS)
@pytest.mark.parametrize(
    "context",
    [
        "task :safe do; {body}; end",
        "def helper; {body}; end; task :safe",
        "if false; {body}; end; task :safe",
        "defined?({body}); task :safe",
    ],
)
def test_readonly_runtime_globals_in_deferred_or_inactive_code(target, context):
    source = context.format(body=f"{target} = nil")
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "assignment",
    ["@@value = 1", "@@value += 1", "@@value ||= 1", "other, @@value = 1, 2"],
)
@pytest.mark.parametrize(
    "context",
    ["{body}", "namespace :group do; {body}; end", "class << self; {body}; end"],
)
def test_top_level_class_variable_assignments_reject_loading(
    assignment, context, caplog
):
    source = f"task :before; {context.format(body=assignment)}; task :after"
    assert parse_rakefile(source) == []
    assert "class variable access from toplevel" in caplog.text


@pytest.mark.parametrize(
    "context",
    [
        "class Example; {body}; end",
        "module Example; {body}; end",
        "class Example; class << self; {body}; end; end",
        "task :safe do; {body}; end",
        "def helper; {body}; end",
        "if false; {body}; end",
        "defined?({body})",
    ],
)
def test_class_variable_assignments_in_valid_or_deferred_contexts(context):
    source = context.format(body="@@value = 1") + "; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "target",
    [
        target
        for target in _READONLY_GLOBALS
        if target not in {"$?", "$!", "$-a", "$-l", "$-p"}
    ],
)
def test_readonly_truthy_globals_do_not_assign_with_or_equals(target):
    assert [task.name for task in parse_rakefile(f"{target} ||= nil; task :safe")] == [
        "safe"
    ]


@pytest.mark.parametrize(
    "receiver",
    [
        "singleton_class",
        "self.singleton_class",
        "singleton_class()",
        "self.singleton_class()",
    ],
)
@pytest.mark.parametrize("name", [":task", '"task"'])
def test_singleton_class_alias_method_replaces_dsl(receiver, name):
    source = f"task :before; {receiver}.alias_method({name}, :desc); task :ghost"
    assert [task.name for task in parse_rakefile(source)] == ["before"]


def test_singleton_class_alias_of_unrelated_method_keeps_dsl():
    source = "singleton_class.alias_method(:helper, :desc); task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("name", ["EUCJP", "eucJP", "euc-jp"])
def test_ruby_eucjp_source_encoding_alias_decodes_description(tmp_path, name):
    (tmp_path / "Rakefile").write_bytes(
        (f'# encoding: {name}\ndesc "構築"; task :build').encode("euc_jp")
    )
    assert [
        (task.name, task.description) for task in RakeProvider().discover(tmp_path)
    ] == [("build", "構築")]


@pytest.mark.parametrize("encoding", ["EUC_JP", "UTF_8", "ASCII_8BIT", "ISO_8859_1"])
def test_invalid_underscore_encoding_aliases_skip_file(tmp_path, caplog, encoding):
    (tmp_path / "Rakefile").write_text(f"# encoding: {encoding}\ntask :ghost")
    assert RakeProvider().discover(tmp_path) == []
    assert "unsupported Ruby source encoding" in caplog.text


@pytest.mark.parametrize("receiver", ["singleton_class", "self.singleton_class()"])
@pytest.mark.parametrize(
    "arguments",
    [
        "",
        ":task",
        ":task, :desc, :puts",
        "nil, :desc",
        ":task, 1",
        "[], :desc",
        ":task, false",
        ":task, -> {}",
    ],
)
def test_invalid_singleton_alias_method_arguments_reject_loading(
    receiver, arguments, caplog
):
    source = f"task :before; {receiver}.alias_method({arguments}); task :after"
    assert parse_rakefile(source) == []
    assert "invalid method alias" in caplog.text


def test_singleton_alias_method_ignores_block_body():
    source = "singleton_class.alias_method(:helper, :desc) { /#{pattern}/ }; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("target", ["$?", "$LOAD_PATH", "@@value"])
@pytest.mark.parametrize(
    "statement", ["for {target} in [1]; end", "begin; raise; rescue => {target}; end"]
)
def test_invalid_runtime_loop_and_rescue_targets_reject_loading(target, statement):
    source = "task :before; " + statement.format(target=target) + "; task :after"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize("target", ["$?", "$LOAD_PATH", "@@value"])
def test_empty_loop_does_not_assign_readonly_targets(target):
    source = f"for {target} in []; /#{{pattern}}/; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("target", ["$?", "$LOAD_PATH", "@@value"])
def test_empty_rescue_body_does_not_assign_readonly_targets(target):
    source = f"begin; rescue => {target}; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("receiver", ["singleton_class", "self.singleton_class()"])
@pytest.mark.parametrize("name", [":task", '"task"', ":desc"])
def test_singleton_undef_method_rejects_later_dsl_calls(receiver, name):
    method = name.strip(':"')
    source = f"task :before; {receiver}.undef_method({name}); {method} :ghost"
    assert parse_rakefile(source) == []


def test_singleton_undef_method_preserves_preceding_tasks_without_later_calls():
    source = "task :safe; singleton_class.undef_method(:task)"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_singleton_undef_method_accepts_no_arguments():
    source = "singleton_class.undef_method(); task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("value", ["nil", "1", "[]"])
def test_singleton_undef_method_rejects_invalid_names(value):
    source = f"task :before; singleton_class.undef_method({value}); task :after"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "body", ["p @@value", "other = @@value", "@@value", "class << self; p @@value; end"]
)
def test_reachable_top_level_class_variable_reads_reject_loading(body, caplog):
    assert parse_rakefile(f"task :before; {body}; task :after") == []
    assert "class variable access from toplevel" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "task :safe do; p @@value; end",
        "def helper; p @@value; end",
        "if false; p @@value; end",
        "defined?(@@value)",
        "class Example; @@value = 1; p @@value; end",
    ],
)
def test_valid_or_deferred_class_variable_reads_preserve_discovery(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize(
    "name",
    [
        "task",
        "multitask",
        "file",
        "file_create",
        "directory",
        "rule",
        "desc",
        "namespace",
    ],
)
@pytest.mark.parametrize(
    "context", ["if true; {body}; end", "{body} unless false", "true && ({body})"]
)
def test_reachable_nested_undef_of_dsl_rejects_loading(name, context, caplog):
    source = "task :before; " + context.format(body=f"undef {name}") + "; task :after"
    assert parse_rakefile(source) == []
    assert "undef of Rake DSL method" in caplog.text


@pytest.mark.parametrize(
    "context",
    [
        "if false; {body}; end",
        "{body} if false",
        "false && ({body})",
        "def helper; {body}; end",
        "task :safe do; {body}; end",
    ],
)
def test_inactive_or_deferred_nested_undef_keeps_discovery(context):
    source = context.format(body="undef task") + "; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("header", ["# coding=iso-8859-1", "# encoding=iso-8859-1"])
def test_no_separator_equals_encoding_is_not_a_ruby_declaration(
    tmp_path, caplog, header
):
    (tmp_path / "Rakefile").write_bytes(
        (header + '\ndesc "café"; task :ghost').encode("latin-1")
    )
    assert RakeProvider().discover(tmp_path) == []
    assert "skipping Rakefile" in caplog.text


@pytest.mark.parametrize("constructor", ["proc", "lambda"])
@pytest.mark.parametrize(
    "alias_form", ["alias {name} then", "alias :{name} :then", 'alias :"{name}" :then']
)
def test_literal_alias_constructor_overrides_execute_blocks(
    constructor, alias_form, caplog
):
    source = (
        alias_form.format(name=constructor)
        + f'; pattern = ")"; {constructor} {{ /#{{pattern}}/ }}; task :ghost'
    )
    assert parse_rakefile(source) == []
    assert "interpolated regexp during loading" in caplog.text


@pytest.mark.parametrize("constructor", ["proc", "lambda"])
@pytest.mark.parametrize(
    "scope", ["if true; {body}; end", "module Kernel; {body}; end"]
)
def test_reachable_alias_constructor_overrides_are_tracked(constructor, scope):
    source = (
        scope.format(body=f"alias {constructor} then")
        + f'; pattern = ")"; {constructor} {{ /#{{pattern}}/ }}; task :ghost'
    )
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "context",
    [
        "class Other; {body}; end",
        "if false; {body}; end",
        "def helper; {body}; end",
        "proc {{ {body} }}",
    ],
)
def test_alias_constructor_in_unrelated_or_deferred_scope_keeps_canonical(context):
    source = (
        context.format(body="alias proc then") + "; proc { /#{pattern}/ }; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("macro", ["attr_reader", "attr_accessor"])
@pytest.mark.parametrize("receiver", ["singleton_class", "self.singleton_class()"])
def test_singleton_attribute_reader_rejects_dsl_arguments(macro, receiver):
    source = f"task :before; {receiver}.{macro}(:task); task :ghost"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize("macro", ["attr_reader", "attr_accessor"])
def test_singleton_attribute_reader_preserves_preceding_tasks(macro):
    source = f"task :safe; singleton_class.{macro}(:task)"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_singleton_attribute_reader_zero_argument_call_ignores_block():
    source = (
        "singleton_class.attr_reader(:task); task { /#{pattern}/ }; multitask :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("macro", ["attr_reader", "attr_accessor"])
def test_singleton_attribute_macros_validate_known_invalid_names(macro):
    assert (
        parse_rakefile(f"task :before; singleton_class.{macro}(nil); task :after") == []
    )


def test_redefined_singleton_attribute_reader_does_not_keep_old_arity():
    source = (
        "singleton_class.attr_reader(:task); def self.task(*); end; "
        "task :ignored; multitask :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "receiver", ["(Kernel)", "((Kernel))", "(::Kernel)", "(Proc)", "((::Proc))"]
)
def test_parenthesized_canonical_constructor_receivers_defer_blocks(receiver):
    method = "new" if "Proc" in receiver else "proc"
    source = f'pattern = ")"; {receiver}.{method} {{ /#{{pattern}}/ }}; task :safe'
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_parenthesized_constructor_receiver_preserves_override_tracking():
    source = (
        'def Kernel.proc; yield; end; pattern = ")"; '
        "(Kernel).proc { /#{pattern}/ }; task :ghost"
    )
    assert parse_rakefile(source) == []


def test_singleton_class_own_singleton_definition_keeps_main_dsl():
    source = "singleton_class.define_singleton_method(:task) { |*| }; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "source",
    [
        "singleton_class.attr_reader(:proc); proc(); task :safe",
        (
            "module Kernel; singleton_class.attr_reader(:proc); end; "
            "Kernel.proc(); task :safe"
        ),
    ],
)
def test_attribute_readers_replacing_constructors_use_reader_arity(source):
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "override",
    [
        "def self.task(*); end",
        "singleton_class.define_method(:task) { |*| }",
        "singleton_class.attr_reader(:task)",
        "singleton_class.alias_method(:task, :desc)",
    ],
)
@pytest.mark.parametrize("receiver", ["singleton_class", "self.singleton_class()"])
def test_removing_temporary_singleton_override_restores_rake_dsl(override, receiver):
    source = f"{override}; {receiver}.remove_method(:task); task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_removing_singleton_constructor_override_preserves_inherited_override():
    source = (
        "def proc; yield; end; def self.proc; end; "
        'singleton_class.remove_method(:proc); pattern = ")"; '
        "proc { /#{pattern}/ }; task :ghost"
    )
    assert parse_rakefile(source) == []


def test_removing_singleton_constructor_override_restores_inherited_constructor():
    source = (
        "def self.proc; yield; end; singleton_class.remove_method(:proc); "
        "proc { /#{pattern}/ }; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_removing_overridden_main_define_method_does_not_restore_missing_wrapper():
    source = (
        "def self.define_method(*); end; "
        "singleton_class.remove_method(:define_method); "
        "define_method(:helper) {}; task :ghost"
    )
    assert parse_rakefile(source) == []


def test_singleton_remove_method_accepts_empty_arguments():
    assert [
        task.name
        for task in parse_rakefile("singleton_class.remove_method(); task :safe")
    ] == ["safe"]


@pytest.mark.parametrize("value", ["nil", "1", "[]"])
def test_singleton_remove_method_rejects_invalid_names(value):
    assert (
        parse_rakefile(
            f"task :before; singleton_class.remove_method({value}); task :after"
        )
        == []
    )


@pytest.mark.parametrize(
    "source",
    [
        (
            "class Other; def self.helper; end; "
            "singleton_class.remove_method(:helper); end; task :safe"
        ),
        "class Other; singleton_class.attr_reader(:task); end; task :safe",
        (
            "def self.helper; end; name = :helper; "
            "singleton_class.remove_method(name); task :safe"
        ),
        "singleton_class.remove_method(:to_s); task :safe",
    ],
)
def test_unrelated_or_dynamic_singleton_mutations_keep_rake_dsl(source):
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "arguments", [":task", ":task, true", ":task, false", ":task, :helper"]
)
def test_singleton_legacy_attr_macro_replaces_dsl_reader(arguments):
    assert (
        parse_rakefile(f"task :before; singleton_class.attr({arguments}); task :ghost")
        == []
    )


@pytest.mark.parametrize("arguments", [":task", ":task, true", ":task, false", ""])
def test_singleton_legacy_attr_preserves_tasks_without_invalid_reader_calls(arguments):
    source = f"task :safe; singleton_class.attr({arguments})"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("arguments", ["nil", ":task, nil", ":task, true, :helper"])
def test_singleton_legacy_attr_invalid_names_reject_loading(arguments):
    assert (
        parse_rakefile(f"task :before; singleton_class.attr({arguments}); task :after")
        == []
    )


@pytest.mark.parametrize(
    "name",
    [
        "task",
        "multitask",
        "file",
        "file_create",
        "directory",
        "rule",
        "desc",
        "namespace",
    ],
)
@pytest.mark.parametrize("receiver", ["singleton_class", "self.singleton_class()"])
def test_removing_inherited_dsl_without_local_override_rejects_loading(name, receiver):
    source = f"task :before; {receiver}.remove_method(:{name}); task :after"
    assert parse_rakefile(source) == []


def test_removing_same_singleton_override_twice_rejects_loading():
    source = (
        "task :before; def self.task(*); end; "
        "singleton_class.remove_method(:task); "
        "singleton_class.remove_method(:task); task :after"
    )
    assert parse_rakefile(source) == []


def test_inactive_invalid_removal_keeps_tasks():
    source = "if false; singleton_class.remove_method(:task); end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "expression", ['raise "stop"', 'fail "stop"', "raise", 'Kernel.raise "stop"']
)
@pytest.mark.parametrize(
    "context", ["{body}", "if true; {body}; end", "namespace :group do; {body}; end"]
)
def test_reachable_uncaught_raise_rejects_loading(expression, context, caplog):
    source = "task :before; " + context.format(body=expression) + "; task :after"
    assert parse_rakefile(source) == []
    assert "uncaught raise during loading" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        'begin; raise "stop"; rescue; end',
        'begin; raise "stop"; rescue RuntimeError; end',
        'begin; raise "stop"; rescue StandardError; end',
        'raise "stop" rescue nil',
        'if false; raise "stop"; end',
        'task :safe do; raise "stop"; end',
        'def helper; raise "stop"; end',
        'def raise(*); end; raise "stop"',
        'def self.raise(*); end; raise "stop"',
    ],
)
def test_rescued_inactive_or_overridden_raise_keeps_discovery(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize(
    "body",
    [
        'begin; raise "stop"; rescue ArgumentError; end',
        'begin; raise "stop"; rescue; raise "again"; end',
        'begin; nil; rescue; nil; ensure; raise "stop"; end',
        'begin; nil; rescue; nil; else; raise "stop"; end',
        "begin; raise SystemExit; rescue; end",
    ],
)
def test_rescue_that_does_not_handle_raise_rejects_loading(body):
    assert parse_rakefile("task :before; " + body + "; task :after") == []


@pytest.mark.parametrize(
    "method", ["class_eval", "class_exec", "module_eval", "module_exec"]
)
@pytest.mark.parametrize("receiver", ["singleton_class", "self.singleton_class()"])
def test_singleton_class_evaluation_replaces_main_dsl(method, receiver):
    source = (
        f"task :before; {receiver}.{method} {{ define_method(:task) {{ |*| }} }}; "
        "task :ghost"
    )
    assert [task.name for task in parse_rakefile(source)] == ["before"]


@pytest.mark.parametrize(
    "method", ["class_eval", "class_exec", "module_eval", "module_exec"]
)
def test_singleton_class_evaluation_method_bodies_are_deferred(method):
    source = (
        f"singleton_class.{method} {{ define_method(:helper) {{ /#{{pattern}}/ }} }}; "
        "task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_singleton_class_evaluation_self_definition_does_not_replace_main():
    source = "singleton_class.class_eval { def self.task(*); end }; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_unrelated_class_evaluation_does_not_replace_main():
    source = (
        "class Other; singleton_class.class_eval { define_method(:task) { |*| } }; "
        "end; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "expression", ["exit 0", 'abort "stop"', "exit", "abort", "Kernel.exit(0)"]
)
@pytest.mark.parametrize(
    "context", ["{body}", "if true; {body}; end", "namespace :group do; {body}; end"]
)
def test_unhandled_load_time_process_exits_reject_loading(expression, context, caplog):
    source = "task :before; " + context.format(body=expression) + "; task :after"
    assert parse_rakefile(source) == []
    assert "unhandled process exit during loading" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "begin; exit 0; rescue Exception; end",
        'begin; abort "stop"; rescue SystemExit; end',
        "if false; exit; end",
        "task :safe do; exit; end",
        "def exit(*); end; exit 0",
        "def self.abort(*); end; abort",
    ],
)
def test_handled_or_deferred_process_exits_keep_discovery(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


def test_default_rescue_does_not_handle_process_exit():
    assert parse_rakefile("task :before; begin; exit; rescue; end; task :after") == []


@pytest.mark.parametrize(
    "expression", ['exit "invalid"', "exit 1, 2", "abort nil", "abort 1, 2"]
)
def test_rescued_invalid_process_exit_arguments_keep_discovery(expression):
    source = f"begin; {expression}; rescue StandardError; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_handled_raise_does_not_execute_ignored_block():
    source = 'begin; raise("stop") { /#{pattern}/ }; rescue; end; task :safe'
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "kind",
    [
        "RangeError",
        "FloatDomainError",
        "IOError",
        "EOFError",
        "IndexError",
        "KeyError",
        "StopIteration",
        "ThreadError",
        "FiberError",
        "EncodingError",
        "Encoding::CompatibilityError",
        "UncaughtThrowError",
        "NoMatchingPatternError",
        "FrozenError",
        "SystemCallError",
        "Errno::ENOENT",
    ],
)
def test_builtin_standard_error_subclasses_are_rescued(kind):
    source = f"begin; raise {kind}; rescue; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    ("kind", "handler"),
    [
        ("EOFError", "IOError"),
        ("FrozenError", "RuntimeError"),
        ("FloatDomainError", "RangeError"),
    ],
)
def test_builtin_error_intermediate_parent_handlers_are_respected(kind, handler):
    source = f"begin; raise {kind}; rescue {handler}; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "source", ["alias helper task", "alias :helper :desc", 'alias :helper :"namespace"']
)
def test_lexical_alias_cannot_read_singleton_only_rake_dsl(source):
    assert parse_rakefile("task :before; " + source + "; task :after") == []


def test_lexical_alias_of_preexisting_object_method_is_valid():
    source = "def task(*); end; alias helper task; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("target", ["$0", "$PROGRAM_NAME", "$stdout", "$stderr", "$>"])
@pytest.mark.parametrize(
    "value", ["nil", "1", ":invalid", "[]", "{}", "false", "-1", "(nil)", "!false"]
)
def test_invalid_literal_constrained_globals_reject_loading(target, value):
    assert parse_rakefile(f"task :before; {target} = {value}; task :after") == []


@pytest.mark.parametrize("target", ["$0", "$PROGRAM_NAME"])
def test_string_program_name_assignment_preserves_discovery(target):
    assert [
        task.name for task in parse_rakefile(f'{target} = "valid"; task :safe')
    ] == ["safe"]


def test_rescued_invalid_program_name_assignment_preserves_discovery():
    source = "begin; $PROGRAM_NAME = nil; rescue TypeError; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "source",
    [
        "$stdout = STDOUT; task :safe",
        '$0 = +"valid"; task :safe',
        '$0 = (value = "valid"; value); task :safe',
        "begin; alias helper task; rescue NameError; end; task :safe",
        (
            "begin; singleton_class.remove_method(:task); "
            "rescue NameError; end; task :safe"
        ),
    ],
)
def test_valid_or_handled_constrained_globals_and_mutations_keep_tasks(source):
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("target", ["$~", "$/", "$-F"])
def test_invalid_literal_optional_globals_reject_loading(target):
    assert parse_rakefile(f"task :before; {target} = 1; task :after") == []


@pytest.mark.parametrize("receiver", ["singleton_class", "self.singleton_class"])
@pytest.mark.parametrize("mutation", ["remove_method", "undef_method"])
def test_singleton_alias_of_deleted_helper_rejects_loading(receiver, mutation):
    source = (
        f"task :before; {receiver}.define_method(:helper) {{}}; "
        f"{receiver}.{mutation}(:helper); "
        f"{receiver}.alias_method(:copy, :helper); task :after"
    )
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "body",
    [
        (
            "singleton_class.define_method(:helper) {}; "
            "singleton_class.remove_method(:helper); "
            "begin; singleton_class.alias_method(:copy, :helper); rescue NameError; end"
        ),
        (
            "singleton_class.define_method(:helper) {}; "
            "singleton_class.remove_method(:helper); "
            "singleton_class.define_method(:helper) {}; "
            "singleton_class.alias_method(:copy, :helper)"
        ),
        (
            "singleton_class.define_method(:task) {}; "
            "singleton_class.remove_method(:task); "
            "singleton_class.alias_method(:copy, :task)"
        ),
        (
            "def helper; end; singleton_class.define_method(:helper) {}; "
            "singleton_class.remove_method(:helper); "
            "singleton_class.alias_method(:copy, :helper)"
        ),
    ],
)
def test_rescued_or_restored_singleton_alias_source_keeps_tasks(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize("expression", ["exit! 7", "exit!", "Kernel.exit!(7)"])
@pytest.mark.parametrize("context", ["{body}", "begin; {body}; rescue Exception; end"])
def test_immediate_process_exit_cannot_be_rescued(expression, context):
    source = "task :before; " + context.format(body=expression) + "; task :after"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "body",
    [
        "if false; exit!; end",
        "task :safe do; exit!; end",
        "def exit!(*); end; exit! 7",
        "def self.exit!(*); end; exit! 7",
        "def Kernel.exit!(*); end; Kernel.exit!(7)",
        'begin; exit! "invalid"; rescue TypeError; end',
        "begin; exit! 1, 2; rescue ArgumentError; end",
    ],
)
def test_deferred_overridden_or_invalid_immediate_exit_keeps_tasks(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize(
    ("expression", "handler"),
    [
        ("RangeError.new('stop')", "StandardError"),
        ("1", "TypeError"),
        ("nil", "TypeError"),
        ("[]", "TypeError"),
        ("error", "Exception"),
        ("RuntimeError.exception('stop')", "Exception"),
    ],
)
def test_rescued_exception_values_preserve_task_discovery(expression, handler):
    source = (
        "error = RuntimeError.new('stop'); "
        f"begin; raise {expression}; rescue {handler}; end; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "body",
    [
        "source = :desc; singleton_class.alias_method(:copy, source)",
        "arguments = [:copy, :desc]; singleton_class.alias_method(*arguments)",
        "begin; exit(*[0]); rescue SystemExit; end",
    ],
)
def test_dynamic_alias_and_handled_splatted_exit_preserve_discovery(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize(
    "expression", ["throw :stop", "Kernel.throw(:stop)", "throw(:stop, 1)"]
)
def test_uncaught_load_time_throw_rejects_discovery(expression):
    assert parse_rakefile(f"task :before; {expression}; task :after") == []


@pytest.mark.parametrize(
    "body",
    [
        "begin; throw :stop; rescue UncaughtThrowError; end",
        "begin; throw :stop; rescue StandardError; end",
        "catch(:stop) { throw :stop }",
        "catch(:outer) { catch(:stop) { throw :stop } }",
        "if false; throw :stop; end",
        "task :safe do; throw :stop; end",
        "def throw(*); end; throw :stop",
        "def self.throw(*); end; throw :stop",
        "def Kernel.throw(*); end; Kernel.throw(:stop)",
    ],
)
def test_handled_deferred_or_overridden_throw_keeps_tasks(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


def test_mismatched_catch_does_not_handle_throw():
    assert (
        parse_rakefile("task :before; catch(:other) { throw :stop }; task :after") == []
    )


@pytest.mark.parametrize(
    "body",
    [
        "begin; throw; rescue ArgumentError; end",
        "begin; throw :stop, 1, 2; rescue ArgumentError; end",
        "catch(:stop) { throw :stop, 1 }",
        "Kernel.catch(:stop) { Kernel.throw(:stop) }",
        "catch(:other) { begin; throw 1; rescue UncaughtThrowError; end }",
    ],
)
def test_throw_arguments_and_qualified_catch_keep_valid_tasks(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize(
    "body",
    [
        "def catch(*); yield; end; catch(:stop) { throw :stop }",
        "other.catch(:stop) { throw :stop }",
        "catch('stop') { throw :stop }",
        "catch { throw :stop }",
    ],
)
def test_nonmatching_or_overridden_catch_cannot_handle_throw(body):
    assert parse_rakefile("task :before; " + body + "; task :after") == []


@pytest.mark.parametrize(
    "value", ["nil", "true", "false", "1", "1.0", ":safe", "'safe'"]
)
def test_nonraising_literal_body_skips_rescue_handler(value):
    source = f'begin; {value}; rescue; raise "never"; end; task :safe'
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "source",
    [
        "catch(:stop) { def catch(*); end; throw :stop }; task :safe",
        "Kernel.catch(:stop) { def Kernel.catch(*); end; throw :stop }; task :safe",
        "catch(:stop) { def self.catch(*); end; Kernel.throw(:stop) }; task :safe",
    ],
)
def test_catch_binding_survives_redefinition_inside_its_block(source):
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_nonraising_body_skips_each_rescue_handler():
    source = (
        'begin; nil; rescue NameError; raise "never"; '
        "rescue StandardError; exit!; end; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_nonraising_body_still_executes_ensure():
    assert (
        parse_rakefile("begin; nil; rescue; nil; ensure; exit!; end; task :after") == []
    )


def test_unknown_protected_body_keeps_rescue_handler_validation():
    source = 'begin; operation; rescue; raise "stop"; end; task :after'
    assert parse_rakefile(source) == []


def test_bare_reraise_preserves_active_system_exit():
    source = (
        "begin; raise SystemExit.new(7); rescue Exception; "
        "begin; raise; rescue RuntimeError; end; end; task :safe"
    )
    assert parse_rakefile(source) == []


@pytest.mark.parametrize("value", [":oops", "/a/", "1..2", "-> {}", ':"oops"'])
def test_nonexception_raise_literals_are_rescuable_type_errors(value):
    source = f"begin; raise {value}; rescue TypeError; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "body",
    [
        (
            "begin; raise SystemExit.new(7); rescue Exception; "
            "begin; raise; rescue SystemExit; end; end"
        ),
        (
            "begin; raise TypeError; rescue StandardError; "
            "begin; raise; rescue TypeError; end; end"
        ),
        'begin; raise "stop"; rescue; begin; raise; rescue RuntimeError; end; end',
        (
            "begin; operation; rescue TypeError; "
            "begin; raise; rescue TypeError; end; end"
        ),
    ],
)
def test_bare_reraise_is_handled_by_the_active_exception_handler(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize(
    "body",
    [
        "begin; operation; rescue; begin; raise; rescue StandardError; end; end",
        (
            "begin; operation; rescue RuntimeError, SystemExit; "
            "begin; raise; rescue Exception; end; end"
        ),
        (
            "begin; nil; raise TypeError; rescue; "
            "begin; raise(); rescue TypeError; end; end"
        ),
    ],
)
def test_reraise_uses_handler_bounds_when_original_error_is_unknown(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize(
    ("expression", "handler"),
    [
        ('raise "x", "bad"', "TypeError"),
        ('raise RuntimeError, "x", 1', "TypeError"),
        ('raise RuntimeError, "x", [1]', "TypeError"),
        ('raise RuntimeError, "x", [], 4', "ArgumentError"),
    ],
)
def test_raise_argument_errors_use_the_actual_handler(expression, handler):
    assert (
        parse_rakefile(f"begin; {expression}; rescue RuntimeError; end; task :bad")
        == []
    )
    source = f"begin; {expression}; rescue {handler}; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("method", ["exit", "exit!", "abort"])
def test_lambda_exit_arguments_are_rescuable_type_errors(method):
    assert (
        parse_rakefile(f"begin; {method} -> {{}}; rescue SystemExit; end; task :bad")
        == []
    )
    source = f"begin; {method} -> {{}}; rescue TypeError; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("method", ["raise", "fail", "exit", "exit!", "abort", "throw"])
def test_lexical_aliases_preserve_termination(method):
    source = f"alias stop {method}; task :before; stop; task :after"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "body",
    [
        'alias stop raise; begin; stop "x"; rescue RuntimeError; end',
        "alias stop throw; catch(:stop) { stop :stop }",
        'alias stop raise; alias halt stop; begin; halt "x"; rescue RuntimeError; end',
        'alias stop raise; def stop(*); end; stop "x"',
        'def raise(*); end; alias stop raise; stop "x"',
        'alias stop raise; def self.stop(*); end; stop "x"',
        'alias stop raise; if false; stop "x"; end',
        'alias stop raise; task :safe do; stop "x"; end',
        'alias stop raise; begin; stop("x") { /#{pattern}/ }; rescue; end',
    ],
)
def test_handled_deferred_and_overridden_termination_aliases_keep_tasks(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize("trace", ["nil", "[]", "['line']", "'line'", "(nil)"])
def test_valid_raise_backtraces_preserve_original_exception_type(trace):
    source = (
        f'begin; raise RuntimeError, "x", {trace}; rescue RuntimeError; end; task :safe'
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_alias_to_another_termination_method_replaces_its_behavior():
    source = "alias raise exit; begin; raise 0; rescue RuntimeError; end; task :after"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize("cause", ["nil", "RuntimeError.new('cause')"])
def test_raise_with_valid_cause_preserves_the_exception_type(cause):
    source = f'begin; raise "x", cause: {cause}; rescue RuntimeError; end; task :safe'
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_invalid_literal_raise_cause_is_a_type_error():
    source = 'begin; raise "x", cause: 1; rescue TypeError; end; task :safe'
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("method", ["raise", "fail", "exit", "exit!", "abort", "throw"])
def test_singleton_aliases_preserve_termination(method):
    source = (
        f"singleton_class.alias_method(:stop, :{method}); "
        "task :before; stop; task :after"
    )
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "body",
    [
        (
            "singleton_class.alias_method(:stop, :raise); "
            'begin; stop "x"; rescue RuntimeError; end'
        ),
        "singleton_class.alias_method(:stop, :throw); catch(:stop) { stop :stop }",
        (
            "singleton_class.alias_method(:stop, :raise); "
            "singleton_class.alias_method(:halt, :stop); "
            'begin; halt "x"; rescue RuntimeError; end'
        ),
        'singleton_class.alias_method(:stop, :raise); def self.stop(*); end; stop "x"',
        'def self.raise(*); end; singleton_class.alias_method(:stop, :raise); stop "x"',
        (
            "singleton_class.class_eval { alias_method(:stop, :raise) }; "
            'begin; stop "x"; rescue RuntimeError; end'
        ),
        'singleton_class.alias_method(:stop, :raise); task :safe do; stop "x"; end',
        (
            "singleton_class.alias_method(:stop, :raise); "
            'begin; stop("x") { /#{pattern}/ }; rescue; end'
        ),
        (
            "alias stop raise; def self.stop(*); end; "
            "singleton_class.remove_method(:stop); "
            'begin; stop "x"; rescue RuntimeError; end'
        ),
    ],
)
def test_handled_deferred_or_overridden_singleton_termination_aliases_keep_tasks(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize(
    "callback",
    [
        '->(_) { raise "boom" }',
        'lambda { |_| raise "boom" }',
        'proc { |_| raise "boom" }',
    ],
)
def test_namespace_executes_inline_callback_during_loading(callback):
    source = f"task :before; namespace(:db, &{callback}); task :after"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "body",
    [
        'callback = ->(_) { raise "boom" }',
        'task(:safe, &->(_) { raise "boom" })',
        'def self.namespace(*); end; namespace(:db, &->(_) { raise "boom" })',
        'begin; namespace(:db, &->(_) { raise "boom" }); rescue RuntimeError; end',
        "namespace(:db, &->(_) { nil })",
    ],
)
def test_deferred_overridden_or_handled_namespace_callbacks_keep_tasks(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize("method", ["exit", "exit!", "abort"])
def test_process_termination_methods_reject_loading(method):
    assert parse_rakefile(f"task :before; Process.{method}; task :after") == []


@pytest.mark.parametrize(
    "body",
    [
        "begin; Process.exit(0); rescue SystemExit; end",
        "def Process.exit(*); end; Process.exit(0)",
        "module Process; class << self; def exit(*); end; end; end; Process.exit(0)",
        "task :safe do; Process.exit(0); end",
        "begin; Process.exit(0) { /#{pattern}/ }; rescue SystemExit; end",
        "begin; (::Process).exit(0); rescue SystemExit; end",
    ],
)
def test_overridden_deferred_or_handled_process_exit_keeps_tasks(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


@pytest.mark.parametrize(
    "callback",
    [
        'Kernel.proc { |_| raise "boom" }',
        'Proc.new { |_| raise "boom" }',
        '(->(_) { raise "boom" })',
        'Kernel.lambda { |_| raise "boom" }',
    ],
)
def test_namespace_executes_qualified_and_parenthesized_inline_callbacks(callback):
    source = f"task :before; namespace(:db, &{callback}); task :after"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize("visibility", ["private", "public", "protected"])
@pytest.mark.parametrize("name", [":task", ':"namespace"', '"desc"'])
def test_visibility_cannot_target_singleton_only_rake_dsl(visibility, name):
    source = f"task :before; {visibility} {name}; task :after"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "body",
    [
        "begin; private :task; rescue NameError; end",
        "def task(*); end; private :task",
        "def self.private(*); end; private :task",
        "def self.public(*); end; public :task",
        "private; public",
        "if false; protected :task; end",
        "task :safe do; private :task; end",
        "def helper; end; private :helper",
    ],
)
def test_valid_rescued_overridden_or_deferred_visibility_keeps_tasks(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


def test_visibility_checks_every_literal_target():
    source = "def helper; end; task :before; private :helper, :task; task :after"
    assert parse_rakefile(source) == []


@pytest.mark.parametrize(
    "tag",
    [
        "1",
        "0",
        "nil",
        "true",
        "false",
        "-1",
        "4611686018427387903",
        "-4611686018427387904",
    ],
)
def test_matching_immediate_catch_tags_keep_task_discovery(tag):
    source = f"catch({tag}) {{ throw({tag}) }}; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    ("tag", "equivalent"),
    [
        ("1", "0x1"),
        ("10", "012"),
        ("10", "0d10"),
        ("2", "0b10"),
        ("8", "0o10"),
        ("1000", "1_000"),
    ],
)
def test_equivalent_integer_tag_spellings_match(tag, equivalent):
    source = f"catch({tag}) {{ throw({equivalent}) }}; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "body",
    [
        "catch(1) { throw 2 }",
        "catch(true) { throw 1 }",
        'catch("same") { throw "same" }',
        "catch(4611686018427387904) { throw 4611686018427387904 }",
    ],
)
def test_distinct_or_heap_allocated_literal_tags_do_not_match(body):
    assert parse_rakefile("task :before; " + body + "; task :after") == []


@pytest.mark.parametrize(
    "expression", ["raise TypeError", 'raise "stop"', "exit 0", "throw :stop"]
)
def test_handled_protected_exception_skips_rescue_else(expression):
    source = (
        f"begin; {expression}; rescue Exception; nil; "
        'else; raise "never"; end; task :safe'
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_redefined_raise_in_handler_does_not_make_else_reachable():
    source = (
        "begin; raise TypeError; rescue; def self.raise(*); end; "
        "else; exit!; end; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "body",
    [
        'begin; nil; rescue; nil; else; raise "reachable"; end',
        "def self.raise(*); end; begin; raise TypeError; rescue; nil; else; exit!; end",
        "begin; raise TypeError; rescue; nil; else; nil; ensure; exit!; end",
    ],
)
def test_reachable_else_and_ensure_calls_still_reject_loading(body):
    assert parse_rakefile("task :before; " + body + "; task :after") == []


@pytest.mark.parametrize(
    "constant",
    [
        "String",
        "Array",
        "Hash",
        "Integer",
        "Object",
        "Kernel",
        "::String",
        "Process::Status",
    ],
)
def test_known_nonexception_core_constants_raise_type_error(constant):
    assert (
        parse_rakefile(f"begin; raise {constant}; rescue {constant}; end; task :bad")
        == []
    )
    source = f"begin; raise {constant}; rescue TypeError; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("method", ["exit", "exit!", "abort"])
@pytest.mark.parametrize(
    "constant", ["String", "Array", "RuntimeError", "Exception", "::String"]
)
def test_known_class_exit_arguments_are_type_errors(method, constant):
    source = f"begin; {method} {constant}; rescue TypeError; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]
    assert (
        parse_rakefile(f"begin; {method} {constant}; rescue SystemExit; end; task :bad")
        == []
    )


@pytest.mark.parametrize("tag", ["1.5", "0.0", "-1.5", "1.5e0", "1_000.5"])
def test_matching_immediate_float_tags_keep_tasks(tag):
    source = f"catch({tag}) {{ throw({tag}) }}; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("tag", ["-0.0", "1e100", "1e-100"])
def test_heap_allocated_float_tags_do_not_match(tag):
    assert parse_rakefile(f"catch({tag}) {{ throw({tag}) }}; task :after") == []


@pytest.mark.parametrize(
    ("tag", "equivalent"), [("1.5", "(1.5)"), ("1.5", "1.50e0"), ("1.5", "+1.5")]
)
def test_equivalent_immediate_float_tags_match(tag, equivalent):
    source = f"catch({tag}) {{ throw({equivalent}) }}; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_distinct_unary_catch_tag_does_not_match_integer():
    assert parse_rakefile("catch(1) { throw !false }; task :after") == []


@pytest.mark.parametrize(
    "constant",
    [
        "RUBY_VERSION",
        "RUBY_ENGINE",
        "RUBY_PLATFORM",
        "::RUBY_DESCRIPTION",
        "ARGV",
        "ENV",
        "STDOUT",
        "TOPLEVEL_BINDING",
    ],
)
@pytest.mark.parametrize("method", ["exit", "exit!"])
def test_invalid_core_value_exit_arguments_are_type_errors(constant, method):
    source = f"begin; {method} {constant}; rescue TypeError; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]
    invalid = f"begin; {method} {constant}; rescue SystemExit; end; task :bad"
    assert parse_rakefile(invalid) == []


@pytest.mark.parametrize(
    "body",
    [
        "begin; exit RUBY_PATCHLEVEL; rescue SystemExit; end",
        "begin; abort RUBY_VERSION; rescue SystemExit; end",
        "begin; raise RUBY_VERSION; rescue RuntimeError; end",
        "begin; raise ARGV; rescue TypeError; end",
        "begin; abort RUBY_PATCHLEVEL; rescue TypeError; end",
    ],
)
def test_core_value_constants_preserve_their_actual_argument_kinds(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


def test_nonmatching_rescue_handlers_are_unreachable_for_known_exception():
    source = (
        'begin; raise TypeError; rescue ArgumentError; raise "never"; '
        "rescue TypeError; nil; rescue StandardError; exit!; end; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_only_first_matching_rescue_handler_executes():
    source = (
        "begin; raise TypeError; rescue; nil; rescue TypeError; exit!; end; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("target", ["$LOAD_PATH", "$?", "$LOADED_FEATURES"])
@pytest.mark.parametrize("handler", ["NameError", "StandardError"])
def test_rescued_readonly_global_assignment_preserves_tasks(target, handler):
    source = f"begin; {target} = []; rescue {handler}; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize(
    "body",
    [
        "begin; desc(); rescue ArgumentError; end",
        'begin; desc("one", "two"); rescue ArgumentError; end',
        "begin; namespace(1) {}; rescue ArgumentError; end",
        "begin; namespace(:db); rescue LocalJumpError; end",
        "begin; namespace(:db, &false); rescue TypeError; end",
    ],
)
def test_rescued_invalid_dsl_declaration_keeps_tasks(body):
    assert [task.name for task in parse_rakefile(body + "; task :safe")] == ["safe"]


def test_wrong_handler_does_not_rescue_invalid_dsl_call():
    assert parse_rakefile("begin; desc(); rescue NameError; end; task :after") == []


def test_known_assignment_error_skips_nonmatching_handler_and_else():
    source = (
        'begin; $LOAD_PATH = []; rescue ArgumentError; raise "never"; '
        "rescue NameError; nil; else; exit!; end; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_known_constrained_global_error_skips_nonmatching_handler():
    source = (
        "begin; $PROGRAM_NAME = nil; rescue NameError; exit!; "
        "rescue TypeError; nil; end; task :safe"
    )
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_rescued_invalid_dsl_call_skips_else():
    source = "begin; desc(); rescue ArgumentError; nil; else; exit!; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


@pytest.mark.parametrize("control", ["return", "begin; return; end", "return if true"])
def test_load_validation_stops_after_file_return(control):
    source = f'task :safe; {control}; raise "unreachable"'
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_file_return_still_validates_ensure():
    source = 'task :safe; begin; return; ensure; raise "executed"; end'
    assert parse_rakefile(source) == []


@pytest.mark.parametrize("target", ["$stdout", "$stderr", "$>", "$0", "$PROGRAM_NAME"])
def test_guaranteed_compound_constrained_global_write_rejects_loading(target):
    assert parse_rakefile(f"task :before; {target} &&= nil; task :after") == []


@pytest.mark.parametrize("target", ["$stdout", "$stderr", "$>", "$0", "$PROGRAM_NAME"])
def test_skipped_compound_constrained_global_write_keeps_tasks(target):
    source = f"{target} ||= nil; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_rescued_compound_constrained_global_write_keeps_tasks():
    source = "begin; $stdout &&= nil; rescue TypeError; end; task :safe"
    assert [task.name for task in parse_rakefile(source)] == ["safe"]


def test_return_inside_ensure_does_not_reexecute_ensure():
    source = 'task :safe; begin; nil; ensure; return; raise "unreachable"; end'
    assert [task.name for task in parse_rakefile(source)] == ["safe"]
