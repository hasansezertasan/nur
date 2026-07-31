import pytest

from nur.models import Task
from nur.registry import AmbiguousTaskError, Registry, UnknownTaskError


def _t(name, prefix):
    return Task(name=name, prefix=prefix, argv_base=(prefix, name))


def test_resolve_bare_unique() -> None:
    reg = Registry([_t("build", "npm"), _t("test", "make")])
    assert reg.resolve("build").prefix == "npm"


def test_resolve_prefixed() -> None:
    reg = Registry([_t("test", "npm"), _t("test", "make")])
    assert reg.resolve("make:test").prefix == "make"


def test_resolve_ambiguous_bare_raises_with_candidates() -> None:
    reg = Registry([_t("test", "npm"), _t("test", "make")])
    with pytest.raises(AmbiguousTaskError) as exc:
        reg.resolve("test")
    assert sorted(exc.value.candidates) == ["make:test", "npm:test"]


def test_resolve_unknown_raises_with_suggestions() -> None:
    reg = Registry([_t("build", "npm")])
    with pytest.raises(UnknownTaskError) as exc:
        reg.resolve("biuld")
    assert "npm:build" in exc.value.suggestions or "build" in exc.value.suggestions


def test_groups_preserve_insertion_order() -> None:
    reg = Registry([_t("a", "npm"), _t("b", "make"), _t("c", "npm")])
    assert list(reg.groups().keys()) == ["npm", "make"]
    assert [t.name for t in reg.groups()["npm"]] == ["a", "c"]


def test_is_empty() -> None:
    assert Registry([]).is_empty()
    assert not Registry([_t("a", "npm")]).is_empty()


def test_resolve_bare_name_with_colon() -> None:
    reg = Registry([_t("test:watch", "npm")])
    assert reg.resolve("test:watch").prefix == "npm"


def test_resolve_qualified_name_with_colon_in_name() -> None:
    reg = Registry([_t("test:watch", "npm")])
    task = reg.resolve("npm:test:watch")
    assert task.prefix == "npm"
    assert task.name == "test:watch"


def test_resolve_bare_colon_name_unique_still_resolves() -> None:
    reg = Registry([_t("build:prod", "npm"), _t("test", "make")])
    assert reg.resolve("build:prod").prefix == "npm"


def test_resolve_unknown_prefix_with_colon_raises() -> None:
    reg = Registry([_t("build", "npm")])
    with pytest.raises(UnknownTaskError):
        reg.resolve("foo:bar")


def test_resolve_known_prefix_unknown_name_raises_unknown() -> None:
    import pytest

    from nur.registry import UnknownTaskError

    reg = Registry([_t("build", "make")])
    with pytest.raises(UnknownTaskError):
        reg.resolve("make:nope")
