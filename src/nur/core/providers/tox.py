from __future__ import annotations

import configparser
import logging
import re
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nur.core.models import Task

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

__all__ = ["ToxProvider"]


log = logging.getLogger("nur")

_CONFIG_NAMES = ("tox.ini", "tox.toml", "pyproject.toml", "setup.cfg")
_RANGE = re.compile(r"^(\d+)-(\d+)$")
_PROVISIONING_ENVS = frozenset({".pkg", ".tox"})


@dataclass(frozen=True, slots=True)
class _Config:
    path: Path
    kind: str
    data: object


def _split_envlist(value: str) -> list[str]:
    """Split an INI envlist without splitting commas inside braces."""
    items: list[str] = []
    current: list[str] = []
    depth = 0
    for char in value:
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        if (char == "," or char.isspace()) and not depth:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
        else:
            current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def _expand_env_name(value: str) -> list[str]:
    """Expand tox's simple brace alternatives and inclusive numeric ranges."""
    start = value.find("{")
    if start < 0:
        return [value]
    end = value.find("}", start)
    if end < 0:
        return [value]
    contents = value[start + 1 : end]
    match = _RANGE.fullmatch(contents)
    if match:
        first, last = (int(part) for part in match.groups())
        width = max(len(part) for part in match.groups())
        step = 1 if first <= last else -1
        options = [
            str(number).zfill(width) for number in range(first, last + step, step)
        ]
    else:
        options = contents.split(",")
    expanded: list[str] = []
    for option in options:
        expanded.extend(_expand_env_name(value[:start] + option + value[end + 1 :]))
    return expanded


def _command_argument(value: object) -> str:  # pylint: disable=too-many-return-statements  # noqa: PLR0911
    """Render a TOML command argument in tox's familiar INI notation."""
    if not isinstance(value, dict):
        return str(value)
    replacement = value.get("replace")
    default = value.get("default")
    default_text = (
        " ".join(_command_argument(argument) for argument in default)
        if isinstance(default, list)
        else _command_argument(default)
        if default is not None
        else ""
    )
    if replacement == "posargs":
        return f"{{posargs:{default_text}}}" if default_text else "{posargs}"
    if replacement == "glob":
        pattern = value.get("pattern")
        suffix = f":{default_text}" if default_text else ""
        return f"{{glob:{pattern}{suffix}}}"
    if replacement == "env":
        name = value.get("name")
        suffix = f":{default_text}" if default_text else ""
        return f"{{env:{name}{suffix}}}"
    if replacement == "ref":
        if isinstance(value.get("env"), str) and isinstance(value.get("key"), str):
            environment = value["env"]
            key = value["key"]
            return f"{{[{environment}]{key}}}"
        source = value.get("of")
        if isinstance(source, list):
            return "{ref:" + ".".join(str(part) for part in source) + "}"
    return "{" + str(replacement or "...") + "}"


def _command_strings(command: object) -> list[str]:
    if isinstance(command, str):
        return [command]
    if isinstance(command, list):
        return [" ".join(_command_argument(argument) for argument in command)]
    if not isinstance(command, dict):
        return []
    default = command.get("default")
    if command.get("replace") != "posargs" or not isinstance(default, list):
        return [_command_argument(command)]
    return [
        " ".join(_command_argument(argument) for argument in default_command)
        if isinstance(default_command, list)
        else default_command
        for default_command in default
        if isinstance(default_command, (list, str))
    ]


def _command_definition(value: object) -> str:
    """Render tox's command forms without trying to evaluate substitutions."""
    if isinstance(value, str):
        return " && ".join(line.strip() for line in value.splitlines() if line.strip())
    if not isinstance(value, list):
        return ""
    return " && ".join(
        rendered for command in value for rendered in _command_strings(command)
    )


def _toml_tox_table(path: Path) -> dict[str, Any] | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("nur: skipping %s (%s)", path.name, exc)
        return None
    if path.name == "pyproject.toml":
        tool = data.get("tool")
        tox = tool.get("tox") if isinstance(tool, dict) else None
    else:
        tox = data
    return tox if isinstance(tox, dict) else None


def _ini_config(path: Path) -> configparser.ConfigParser | None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, configparser.Error) as exc:
        log.warning("nur: skipping %s (%s)", path.name, exc)
        return None
    return parser


def _find_config(cwd: Path) -> _Config | None:
    for name in _CONFIG_NAMES:
        path = cwd / name
        if not path.is_file():
            continue
        if name in {"tox.ini", "setup.cfg"}:
            parser = _ini_config(path)
            if parser is None:
                return None
            if name == "setup.cfg" and not parser.has_section("tox:tox"):
                continue
            return _Config(path, "ini", parser)
        table = _toml_tox_table(path)
        if table is not None:
            return _Config(path, "toml", table)
    return None


def _without_provisioning(names: Iterable[str]) -> list[str]:
    """Remove tox's internal packaging/provisioning environments."""
    return list(
        dict.fromkeys(name for name in names if name and name not in _PROVISIONING_ENVS)
    )


def _ini_tasks(config: _Config) -> list[Task]:
    parser = config.data
    if not isinstance(parser, configparser.ConfigParser):
        return []
    tox_section = "tox:tox" if config.path.name == "setup.cfg" else "tox"
    envlist = (
        parser.get(
            tox_section,
            "envlist",
            fallback=parser.get(tox_section, "env_list", fallback=""),
        )
        if parser.has_section(tox_section)
        else ""
    )
    names = [
        name for item in _split_envlist(envlist) for name in _expand_env_name(item)
    ]
    generative_sections: dict[str, configparser.SectionProxy] = {}
    explicit: list[str] = []
    for section_name in parser.sections():
        if not section_name.startswith("testenv:"):
            continue
        for name in _expand_env_name(section_name.removeprefix("testenv:")):
            explicit.append(name)
            generative_sections.setdefault(name, parser[section_name])
    names = _without_provisioning([*names, *explicit])
    base = parser["testenv"] if parser.has_section("testenv") else {}
    tasks: list[Task] = []
    for name in names:
        exact_section = f"testenv:{name}"
        section = (
            parser[exact_section]
            if parser.has_section(exact_section)
            else generative_sections.get(name)
        )
        description = (section.get("description") if section else None) or base.get(
            "description"
        )
        commands = (section.get("commands") if section else None) or base.get(
            "commands"
        )
        tasks.append(
            Task(
                name=name,
                prefix="tox",
                argv_base=("tox", "-e", name),
                description=description,
                definition=_command_definition(commands),
                source_file=config.path.name,
            )
        )
    return tasks


def _toml_tasks(config: _Config) -> list[Task]:
    tox = config.data
    if not isinstance(tox, dict):
        return []
    env_list = tox.get("env_list", tox.get("envlist", []))
    names = (
        [name for name in env_list if isinstance(name, str)]
        if isinstance(env_list, list)
        else []
    )
    envs = tox.get("env")
    envs = envs if isinstance(envs, dict) else {}
    explicit = [
        name
        for name, value in envs.items()
        if isinstance(name, str) and isinstance(value, dict)
    ]
    names = _without_provisioning([*names, *explicit])
    base = tox.get("env_run_base")
    base = base if isinstance(base, dict) else {}
    tasks: list[Task] = []
    for name in names:
        section = envs.get(name)
        section = section if isinstance(section, dict) else {}
        description = section.get("description") or base.get("description")
        description = description if isinstance(description, str) else None
        commands = section.get("commands") or base.get("commands")
        tasks.append(
            Task(
                name=name,
                prefix="tox",
                argv_base=("tox", "-e", name),
                description=description,
                definition=_command_definition(commands),
                source_file=config.path.name,
            )
        )
    return tasks


class ToxProvider:
    prefix = "tox"

    def detect(self, cwd: Path) -> bool:
        return _find_config(cwd) is not None

    def discover(self, cwd: Path) -> list[Task]:
        config = _find_config(cwd)
        if config is None:
            return []
        return _ini_tasks(config) if config.kind == "ini" else _toml_tasks(config)
