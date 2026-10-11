from nur.core.discovery import discover
from nur.core.providers.grunt import GruntProvider, parse_gruntfile

_GRUNTFILE = """module.exports = function(grunt) {
  grunt.initConfig({ pkg: 'demo' });
  grunt.loadNpmTasks('grunt-contrib-jshint');
  grunt.registerTask('test', ['jshint', 'qunit']);
  grunt.registerTask('default', 'Default task.', ['jshint', 'test']);
  grunt.registerMultiTask('log', 'Log stuff.', function() {});
  grunt.task.registerTask('alias', ['test']);
};
"""


def test_detect_true_when_gruntfile_present(tmp_path) -> None:
    (tmp_path / "Gruntfile.js").write_text(_GRUNTFILE)
    assert GruntProvider().detect(tmp_path)


def test_detect_false_when_absent(tmp_path) -> None:
    assert not GruntProvider().detect(tmp_path)


def test_discover_registers_literal_tasks(tmp_path) -> None:
    (tmp_path / "Gruntfile.js").write_text(_GRUNTFILE)
    tasks = {t.name: t for t in GruntProvider().discover(tmp_path)}
    assert set(tasks) == {"test", "default", "log", "alias"}
    assert tasks["test"].argv_base == ("grunt", "test")
    assert tasks["test"].qualified_name == "grunt:test"
    assert tasks["test"].source_file == "Gruntfile.js"
    assert tasks["test"].description is None
    assert tasks["default"].description == "Default task."
    assert tasks["log"].description == "Log stuff."
    assert tasks["alias"].run_argv() == ["grunt", "alias"]


def test_description_only_from_three_arg_form() -> None:
    tasks = {t.name: t for t in parse_gruntfile("grunt.registerTask('x', 'y');")}
    assert tasks["x"].description is None
    source = "grunt.registerTask('a', 5, []);\ngrunt.registerTask('b', '  ', []);\n"
    tasks = {t.name: t for t in parse_gruntfile(source)}
    assert tasks["a"].description is None
    assert tasks["b"].description is None


def test_dynamic_names_are_skipped() -> None:
    source = (
        "const n = 'dyn';\n"
        "grunt.registerTask(n, []);\n"
        "grunt.registerTask('a' + 'b', []);\n"
        "grunt.registerTask(`tpl`, []);\n"
        "grunt.registerTask(`interp-${n}`, []);\n"
        "grunt.registerTask('esc\\'aped', []);\n"
        "grunt.registerTask('with space', []);\n"
        "grunt.registerTask('with:colon', []);\n"
        "grunt.registerTask('', []);\n"
        "grunt.registerTask();\n"
        "grunt.registerTask(...args);\n"
    )
    assert parse_gruntfile(source) == []


def test_non_grunt_receivers_are_skipped() -> None:
    source = (
        "other.registerTask('a', []);\n"
        "grunt.other.registerTask('b', []);\n"
        "grunt.tasks.registerTask('c', []);\n"
        "registerTask('d', []);\n"
        "super.registerTask('e', []);\n"
        "grunt.#priv('f', []);\n"
        "(grunt.registerTask)('g', []);\n"
        "frobnicate.task.registerTask('h', []);\n"
        "grunt.registerTask('e', []);\n"
    )
    assert [t.name for t in parse_gruntfile(source)] == ["e"]


def test_guarded_declarations_are_listed() -> None:
    # Conditional guards are not evaluated: syntactic candidates are listed.
    source = "if (process.env.CI) {\n  grunt.registerTask('ci', []);\n}\n"
    assert [t.name for t in parse_gruntfile(source)] == ["ci"]


def test_duplicates_keep_first_with_latest_description() -> None:
    tasks = {
        t.name: t
        for t in parse_gruntfile(
            "grunt.registerTask('a', []);\ngrunt.registerTask('a', 'Kept.', []);\n"
        )
    }
    assert tasks["a"].description == "Kept."
    tasks = {
        t.name: t
        for t in parse_gruntfile(
            "grunt.registerTask('b', 'First.', []);\ngrunt.registerTask('b', []);\n"
        )
    }
    assert tasks["b"].description == "First."


def test_parse_error_skips_file(caplog) -> None:
    assert parse_gruntfile("grunt.registerTask('oops', ", "Gruntfile.js") == []
    assert any("Gruntfile.js" in r.message for r in caplog.records)


def test_discovery_does_not_execute_gruntfile(tmp_path) -> None:
    """Security: discovering tasks must never run project JavaScript."""
    sentinel = tmp_path / "PWNED"
    (tmp_path / "Gruntfile.js").write_text(
        "require('fs').writeFileSync("
        f"{str(sentinel)!r}, 'bad');\n"
        "grunt.registerTask('safe', []);\n"
    )
    assert [t.name for t in GruntProvider().discover(tmp_path)] == ["safe"]
    assert not sentinel.exists()
    assert "loadNpmTasks" not in {
        t.name for t in parse_gruntfile("grunt.loadNpmTasks('x');")
    }


def test_discover_includes_grunt_tasks(tmp_path) -> None:
    (tmp_path / "Gruntfile.js").write_text("grunt.registerTask('build', []);\n")
    registry = discover(tmp_path)
    assert "grunt:build" in {t.qualified_name for t in registry.all()}
    assert registry.resolve("grunt:build").argv_base == ("grunt", "build")


def test_discover_missing_file_returns_empty(tmp_path, caplog) -> None:
    assert GruntProvider().discover(tmp_path) == []
    assert any("Gruntfile.js" in r.message for r in caplog.records)


class _Stub:
    """Minimal stand-in for tree_sitter.Node to pin defensive guards."""

    def __init__(self, type_, fields=None, text=None) -> None:
        self.type = type_
        self._fields = fields or {}
        self.text = text

    def child_by_field_name(self, name):
        return self._fields.get(name)


def test_matchers_tolerate_partial_nodes() -> None:
    from nur.core.providers.grunt import _arguments, _register_kind

    call = _Stub("call_expression", {})
    assert _register_kind(call) is None
    assert _arguments(call) == []
    member = _Stub("member_expression", {})
    bare = _Stub("call_expression", {"function": member, "arguments": member})
    assert _register_kind(bare) is None
    inner = _Stub("member_expression", {"property": _Stub("property", {})})
    outer = _Stub(
        "member_expression",
        {
            "object": inner,
            "property": _Stub("property_identifier", text=b"registerTask"),
        },
    )
    assert _register_kind(_Stub("call_expression", {"function": outer})) is None
