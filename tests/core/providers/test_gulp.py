from nur.core.discovery import discover
from nur.core.providers.gulp import GulpProvider, parse_gulpfile

_GULPFILE = """const { series, task } = require('gulp');
function clean(cb) { cb(); }
function build(cb) { cb(); }
exports.build = build;
exports.default = series(clean, build);
exports['dash-name'] = build;
gulp.task('legacy', function(cb) { cb(); });
task('destructured', function(cb) { cb(); });
task(build);
clean.description = 'Remove files.';
"""


def test_detect_true_for_filename_variants(tmp_path) -> None:
    for filename in ("gulpfile.js", "Gulpfile.js", "gulpfile.mjs", "gulpfile.cjs"):
        variant = tmp_path / filename
        variant.write_text("exports.a = () => {};\n")
        assert GulpProvider().detect(tmp_path), filename
        variant.unlink()


def test_detect_false_when_absent(tmp_path) -> None:
    assert not GulpProvider().detect(tmp_path)


def test_discover_registers_literal_tasks(tmp_path) -> None:
    (tmp_path / "gulpfile.js").write_text(_GULPFILE)
    tasks = {t.name: t for t in GulpProvider().discover(tmp_path)}
    assert set(tasks) == {"build", "default", "dash-name", "legacy", "destructured"}
    assert tasks["build"].argv_base == ("gulp", "build")
    assert tasks["build"].qualified_name == "gulp:build"
    assert tasks["build"].source_file == "gulpfile.js"
    assert tasks["build"].description is None
    # `clean` is private (never exported), so its description is ignored...
    assert "clean" not in tasks
    # ...but the function-reference form `task(build)` is also skipped.
    assert tasks["default"].run_argv() == ["gulp", "default"]


def test_discover_prefers_first_filename_variant(tmp_path) -> None:
    # Note: `gulpfile.js` vs `Gulpfile.js` cannot share a directory on
    # case-insensitive filesystems, so precedence is shown with extensions.
    (tmp_path / "gulpfile.js").write_text("exports.a = () => {};\n")
    (tmp_path / "gulpfile.mjs").write_text("exports.b = () => {};\n")
    tasks = {t.name: t for t in GulpProvider().discover(tmp_path)}
    assert set(tasks) == {"a"}
    assert tasks["a"].source_file == "gulpfile.js"


def test_discover_esm_forms() -> None:
    source = (
        "export function styles() {}\n"
        "export const scripts = () => {};\n"
        "export default build;\n"
        "export { watchFiles as watch };\n"
        "export { plain };\n"
        "export * from './other.js';\n"
        "export class Helper {}\n"
        "export default function() {}\n"
        "export const { destructure } = parts;\n"
        "export { quoted as 'with space' };\n"
    )
    tasks = {t.name: t for t in parse_gulpfile(source, "gulpfile.mjs")}
    assert set(tasks) == {"styles", "scripts", "default", "watch", "plain"}
    assert all(t.source_file == "gulpfile.mjs" for t in tasks.values())


def test_discover_duplicate_exports_keep_first() -> None:
    source = "export function a() {}\n export { a };\n"
    assert [t.name for t in parse_gulpfile(source)] == ["a"]


def test_discover_comment_in_lexical_declaration() -> None:
    assert [t.name for t in parse_gulpfile("export const /*c*/ a = 1;\n")] == ["a"]
    assert [t.name for t in parse_gulpfile("export { /*c*/ a };\n")] == ["a"]


def test_discover_module_exports_forms() -> None:
    source = "module.exports.extra = build;\nmodule.exports = { skipped: build };\n"
    assert [t.name for t in parse_gulpfile(source)] == ["extra"]


def test_dynamic_names_are_skipped() -> None:
    source = (
        "const n = 'dyn';\n"
        "gulp.task(n, () => {});\n"
        "gulp.task('a' + 'b', () => {});\n"
        "gulp.task(`tpl`, () => {});\n"
        "gulp.#task('priv', () => {});\n"
        "exports[n] = () => {};\n"
        "exports['with space'] = () => {};\n"
        "other['x'] = () => {};\n"
        "other.task('a', () => {});\n"
        "gulp.series('b');\n"
        "gulp.task();\n"
        "unrelated = 1;\n"
        "(a.b).description = 'x';\n"
        "a['description'] = 'x';\n"
    )
    assert parse_gulpfile(source) == []


def test_guarded_declarations_are_listed() -> None:
    # Conditional guards are not evaluated: syntactic candidates are listed.
    source = "if (process.env.CI) {\n  exports.ci = () => {};\n}\n"
    assert [t.name for t in parse_gulpfile(source)] == ["ci"]


def test_duplicates_keep_first_with_latest_description() -> None:
    tasks = {
        t.name: t
        for t in parse_gulpfile(
            "exports.a = one;\ngulp.task('a', two);\na.description = 'Kept.';\n"
        )
    }
    source = "exports.a = one;\ngulp.task('a', two);\n"
    assert [t.name for t in parse_gulpfile(source)] == ["a"]
    assert tasks["a"].description == "Kept."


def test_empty_descriptions_are_ignored() -> None:
    tasks = {
        t.name: t for t in parse_gulpfile("exports.a = one;\na.description = '  ';\n")
    }
    assert tasks["a"].description is None


def test_parse_error_skips_file(caplog) -> None:
    assert parse_gulpfile("exports.a = ", "gulpfile.js") == []
    assert any("gulpfile.js" in r.message for r in caplog.records)


def test_discovery_does_not_execute_gulpfile(tmp_path) -> None:
    """Security: discovering tasks must never run project JavaScript."""
    sentinel = tmp_path / "PWNED"
    (tmp_path / "gulpfile.js").write_text(
        "require('fs').writeFileSync("
        f"{str(sentinel)!r}, 'bad');\n"
        "exports.safe = () => {};\n"
    )
    assert [t.name for t in GulpProvider().discover(tmp_path)] == ["safe"]
    assert not sentinel.exists()


def test_discover_includes_gulp_tasks(tmp_path) -> None:
    (tmp_path / "gulpfile.js").write_text("exports.build = () => {};\n")
    registry = discover(tmp_path)
    assert "gulp:build" in {t.qualified_name for t in registry.all()}
    assert registry.resolve("gulp:build").argv_base == ("gulp", "build")


def test_discover_missing_file_returns_empty(tmp_path) -> None:
    assert GulpProvider().discover(tmp_path) == []


def test_discover_unreadable_file_returns_empty(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "gulpfile.js").write_text("exports.a = () => {};\n")

    def boom(*_a: object, **_k: object) -> str:
        msg = "nope"
        raise OSError(msg)

    monkeypatch.setattr("pathlib.Path.read_text", boom)
    assert GulpProvider().discover(tmp_path) == []
    assert any("gulpfile.js" in r.message for r in caplog.records)


class _Stub:
    """Minimal stand-in for tree_sitter.Node to pin defensive guards."""

    text = None

    def __init__(self, type_, fields=None) -> None:
        self.type = type_
        self._fields = fields or {}

    def child_by_field_name(self, name):
        return self._fields.get(name)


def test_matchers_tolerate_partial_nodes() -> None:
    from nur.core.providers._javascript import parse
    from nur.core.providers.gulp import (
        _arguments,
        _call_task_name,
        _description_assignment,
        _exports_name,
        _exports_object,
        _module_exports,
    )

    call = _Stub("call_expression", {})
    assert _call_task_name(call) is None
    assert _arguments(call) == []
    member = _Stub("member_expression", {})
    assert _call_task_name(_Stub("call_expression", {"function": member})) is None
    assert _module_exports(member) is False
    assert _exports_name(_Stub("assignment_expression", {})) is None
    assert _description_assignment(_Stub("assignment_expression", {})) is None
    assert (
        _description_assignment(
            _Stub("assignment_expression", {"left": member, "right": member})
        )
        is None
    )
    assert _exports_object(None) is False
    root = parse("1;\n", "gulpfile.js")
    assert root is not None
    assert _exports_object(root) is False

    from nur.core.providers.gulp import _export_names

    anonymous = _Stub(
        "export_statement", {"declaration": _Stub("function_declaration", {})}
    )
    assert list(_export_names(anonymous)) == []
