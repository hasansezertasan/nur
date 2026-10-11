import json

from nur.core.providers.turbo import TurboProvider, parse_turbo


def test_detect_true_when_turbo_json_present(tmp_path) -> None:
    (tmp_path / "turbo.json").write_text("{}")
    assert TurboProvider().detect(tmp_path)


def test_detect_false_when_absent(tmp_path) -> None:
    assert not TurboProvider().detect(tmp_path)


def test_discover_builds_tasks(tmp_path) -> None:
    (tmp_path / "turbo.json").write_text(
        json.dumps({
            "$schema": "https://turborepo.dev/schema.json",
            "tasks": {
                "build": {"dependsOn": ["^build"], "outputs": ["dist/**"]},
                "test": {"dependsOn": ["build"]},
                "dev": {"cache": False, "persistent": True},
            },
        })
    )
    tasks = {t.name: t for t in TurboProvider().discover(tmp_path)}
    assert set(tasks) == {"build", "test", "dev"}
    assert tasks["build"].argv_base == ("turbo", "run", "build")
    assert tasks["build"].qualified_name == "turbo:build"
    assert tasks["build"].source_file == "turbo.json"
    assert tasks["build"].definition == ""
    assert tasks["test"].run_argv() == ["turbo", "run", "test"]


def test_discover_reads_legacy_pipeline_key(tmp_path) -> None:
    (tmp_path / "turbo.json").write_text(json.dumps({"pipeline": {"build": {}}}))
    assert [t.name for t in TurboProvider().discover(tmp_path)] == ["build"]


def test_discover_prefers_tasks_over_pipeline(tmp_path) -> None:
    (tmp_path / "turbo.json").write_text(
        json.dumps({"tasks": {"build": {}}, "pipeline": {"legacy": {}}})
    )
    assert [t.name for t in TurboProvider().discover(tmp_path)] == ["build"]


def test_discover_strips_root_task_prefix(tmp_path) -> None:
    (tmp_path / "turbo.json").write_text(
        json.dumps({"tasks": {"//#lint:root": {}, "lint": {}}})
    )
    tasks = {t.name: t for t in TurboProvider().discover(tmp_path)}
    assert set(tasks) == {"lint:root", "lint"}
    assert tasks["lint:root"].argv_base == ("turbo", "run", "lint:root")


def test_discover_tolerates_jsonc(tmp_path) -> None:
    (tmp_path / "turbo.json").write_text(
        '// workspace pipeline\n{\n"tasks": {\n"build": {}, // cached\n},\n}\n'
    )
    assert [t.name for t in TurboProvider().discover(tmp_path)] == ["build"]


def test_discover_skips_non_object_entries() -> None:
    tasks = parse_turbo(json.dumps({"tasks": {"build": {}, "oops": ["x"]}}))
    assert [t.name for t in tasks] == ["build"]
    # A bare `//#` root marker strips to an empty name and is skipped.
    assert parse_turbo(json.dumps({"tasks": {"//#": {}}})) == []
    assert parse_turbo(json.dumps({"tasks": ["build"]})) == []


def test_discover_empty_or_missing_tasks_returns_empty(tmp_path) -> None:
    assert parse_turbo("{}") == []
    assert parse_turbo("[]") == []
    (tmp_path / "turbo.json").write_text(json.dumps({"globalEnv": ["CI"]}))
    assert TurboProvider().discover(tmp_path) == []


def test_discover_malformed_json_returns_empty(tmp_path, caplog) -> None:
    (tmp_path / "turbo.json").write_text("{ not json")
    assert TurboProvider().discover(tmp_path) == []
    assert any("turbo.json" in r.message for r in caplog.records)


def test_discover_unreadable_file_returns_empty(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "turbo.json").write_text("{}")

    def boom(*_a: object, **_k: object) -> str:
        msg = "nope"
        raise OSError(msg)

    monkeypatch.setattr("pathlib.Path.read_text", boom)
    assert TurboProvider().discover(tmp_path) == []
    assert any("turbo.json" in r.message for r in caplog.records)
