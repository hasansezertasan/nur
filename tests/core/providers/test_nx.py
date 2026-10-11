import json

from nur.core.providers.nx import NxProvider, parse_nx


def test_detect_true_when_nx_json_present(tmp_path) -> None:
    (tmp_path / "nx.json").write_text("{}")
    assert NxProvider().detect(tmp_path)


def test_detect_false_when_absent(tmp_path) -> None:
    assert not NxProvider().detect(tmp_path)


def test_discover_builds_tasks(tmp_path) -> None:
    (tmp_path / "nx.json").write_text(
        json.dumps({
            "$schema": "./node_modules/nx/schemas/nx-schema.json",
            "targetDefaults": {
                "build": {"dependsOn": ["^build"]},
                "test": {"inputs": ["default"]},
            },
        })
    )
    tasks = {t.name: t for t in NxProvider().discover(tmp_path)}
    assert set(tasks) == {"build", "test"}
    assert tasks["build"].argv_base == ("nx", "run-many", "-t", "build")
    assert tasks["build"].qualified_name == "nx:build"
    assert tasks["build"].source_file == "nx.json"
    assert tasks["build"].definition == ""
    assert tasks["test"].run_argv() == ["nx", "run-many", "-t", "test"]


def test_discover_tolerates_jsonc(tmp_path) -> None:
    (tmp_path / "nx.json").write_text(
        '{\n// shared targets\n"targetDefaults": {\n"build": {},\n},\n}\n'
    )
    assert [t.name for t in NxProvider().discover(tmp_path)] == ["build"]


def test_discover_skips_non_object_entries() -> None:
    tasks = parse_nx(json.dumps({"targetDefaults": {"build": {}, "oops": True}}))
    assert [t.name for t in tasks] == ["build"]
    assert parse_nx(json.dumps({"targetDefaults": {"": {}}})) == []
    assert parse_nx(json.dumps({"targetDefaults": ["build"]})) == []


def test_discover_empty_or_missing_targets_returns_empty(tmp_path) -> None:
    assert parse_nx("{}") == []
    assert parse_nx("[]") == []
    (tmp_path / "nx.json").write_text(json.dumps({"namedInputs": {"a": ["b"]}}))
    assert NxProvider().discover(tmp_path) == []


def test_discover_malformed_json_returns_empty(tmp_path, caplog) -> None:
    (tmp_path / "nx.json").write_text("{ not json")
    assert NxProvider().discover(tmp_path) == []
    assert any("nx.json" in r.message for r in caplog.records)


def test_discover_unreadable_file_returns_empty(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "nx.json").write_text("{}")

    def boom(*_a: object, **_k: object) -> str:
        msg = "nope"
        raise OSError(msg)

    monkeypatch.setattr("pathlib.Path.read_text", boom)
    assert NxProvider().discover(tmp_path) == []
    assert any("nx.json" in r.message for r in caplog.records)
