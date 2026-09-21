from __future__ import annotations

import configparser
import logging
import os
import re
import textwrap
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nur.core.models import Task

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

__all__ = ["ToxProvider"]


log = logging.getLogger("nur")

_CONFIG_NAMES = ("tox.ini", "setup.cfg", "pyproject.toml", "tox.toml")
_RANGE = re.compile(r"^(\d+)-(\d+)$")
_PROVISIONING_ENVS = frozenset({".pkg", ".tox"})
_FACTOR_LINE = re.compile(r"^([a-zA-Z0-9_!{},.-]+):\s*(.*)$")
_MISSING = object()


@dataclass(frozen=True, slots=True)
class _Config:
    """Holds parsed tox configuration details and source metadata."""

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


def _resolve_substitution(
    contents: str, parser: configparser.ConfigParser | None
) -> str | None:
    """Resolve an env or section reference substitution inside braces."""
    if contents.startswith("env:"):
        var_name, has_default, default_val = contents[4:].partition(":")
        var_name = var_name.strip()
        env_val = os.environ.get(var_name)
        if env_val is not None:
            return env_val
        return default_val if has_default else None

    if contents.startswith("[") and "]" in contents:
        sec, _, key = contents[1:].partition("]")
        sec, key = sec.strip(), key.strip()
        if parser is not None and parser.has_section(sec) and key in parser[sec]:
            return parser.get(sec, key)
    return None


def _find_matching_brace(value: str, start: int) -> int:
    """Find the index of the closing brace matching the opening brace at start."""
    depth = 0
    for i in range(start, len(value)):
        if value[i] == "{":
            depth += 1
        elif value[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _expand_brace_options(contents: str) -> list[str]:
    """Expand numeric ranges or comma-separated alternatives inside braces."""
    match = _RANGE.fullmatch(contents)
    if match:
        first, last = (int(part) for part in match.groups())
        width = max(len(part) for part in match.groups())
        step = 1 if first <= last else -1
        return [str(number).zfill(width) for number in range(first, last + step, step)]
    return [option.strip() for option in _split_envlist(contents)]


def _expand_env_name(
    value: str, parser: configparser.ConfigParser | None = None
) -> list[str]:
    """Expand tox's simple brace alternatives, numeric ranges, and substitutions."""
    start = value.find("{")
    if start < 0:
        return [value]
    end = _find_matching_brace(value, start)
    if end < 0:
        return [value]

    contents = value[start + 1 : end]
    if contents.startswith("env:") or (contents.startswith("[") and "]" in contents):
        resolved = _resolve_substitution(contents, parser)
        if not resolved or not resolved.strip():
            return []
        return _expand_env_name(value[:start] + resolved + value[end + 1 :], parser)

    expanded: list[str] = []
    for option in _expand_brace_options(contents):
        expanded.extend(
            _expand_env_name(value[:start] + option + value[end + 1 :], parser)
        )
    return expanded


def _expand_simple_factors(expr: str) -> list[str]:
    """Expand simple comma and brace groups in factor conditions."""
    start = expr.find("{")
    if start >= 0:
        end = _find_matching_brace(expr, start)
        if end >= 0:
            opts = [o.strip() for o in expr[start + 1 : end].split(",")]
            res: list[str] = []
            for o in opts:
                res.extend(_expand_simple_factors(expr[:start] + o + expr[end + 1 :]))
            return res
    return [o.strip() for o in expr.split(",")]


def _factor_group_matches(group: str, env_factors: set[str]) -> bool:
    """Determine whether all conditions in a factor group match the environment."""
    factors = [f.strip() for f in group.split("-") if f.strip()]
    for factor in factors:
        if factor.startswith("!"):
            if factor[1:] in env_factors:
                return False
        elif factor not in env_factors:
            return False
    return True


def _filter_ini_commands(value: object, env_name: str) -> str:
    """Filter INI command lines matching the current environment's factors."""
    if not isinstance(value, str):
        return ""
    env_factors = set(env_name.split("-"))
    kept: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _FACTOR_LINE.match(line)
        if not match:
            kept.append(line)
            continue
        factor_expr, cmd = match.groups()
        if (
            any(
                _factor_group_matches(grp, env_factors)
                for grp in _expand_simple_factors(factor_expr)
            )
            and cmd.strip()
        ):
            kept.append(cmd.strip())
    return "\n".join(kept)


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
    """Convert a single command structure into rendered command string segments."""
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
        rendered
        for command in value
        for rendered in _command_strings(command)
        if rendered
    )


def _render_command_groups(*groups: object) -> str:
    """Render and combine pre-, main, and post-command groups in order."""
    rendered = [_command_definition(group) for group in groups]
    return " && ".join(part for part in rendered if part)


def _toml_tox_table(path: Path) -> dict[str, Any] | None:
    """Parse and return the tox configuration table from a TOML file."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        log.warning("nur: skipping %s (%s)", path.name, exc)
        return None
    if path.name == "pyproject.toml":
        tool = data.get("tool")
        tox = tool.get("tox") if isinstance(tool, dict) else None
    else:
        tox = data
    return tox if isinstance(tox, dict) else None


def _ini_config(path: Path) -> configparser.ConfigParser | None:
    """Parse and return a ConfigParser instance from an INI file."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeDecodeError, configparser.Error) as exc:
        log.warning("nur: skipping %s (%s)", path.name, exc)
        return None
    return parser


def _find_config(cwd: Path) -> _Config | None:
    """Locate and load the first matching tox configuration in discovery order."""
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
            if name == "pyproject.toml" and isinstance(
                table.get("legacy_tox_ini"), str
            ):
                parser = configparser.ConfigParser(interpolation=None)
                try:
                    parser.read_string(textwrap.dedent(table["legacy_tox_ini"]))
                except configparser.Error as exc:
                    log.warning("nur: skipping %s (%s)", path.name, exc)
                    return None
                return _Config(path, "ini", parser)
            return _Config(path, "toml", table)
    return None


def _configured_internal_envs_ini(
    parser: configparser.ConfigParser, tox_section: str
) -> set[str]:
    """Collect default and configured internal tox environment names from INI."""
    internal = set(_PROVISIONING_ENVS)
    if parser.has_section(tox_section):
        sec = parser[tox_section]
        for key in ("provision_tox_env", "package_env", "isolated_build_env"):
            val = sec.get(key)
            if val and val.strip():
                internal.add(val.strip())
    for s_name in parser.sections():
        s = parser[s_name]
        for key in ("package_env", "isolated_build_env"):
            val = s.get(key)
            if val and val.strip():
                internal.add(val.strip())
    return internal


def _collect_envs_from_mapping(
    mapping: dict[str, object], keys: tuple[str, ...]
) -> list[str]:
    """Extract trimmed environment names from mapping keys."""
    names: list[str] = []
    for key in keys:
        val = mapping.get(key)
        if isinstance(val, str) and val.strip():
            names.append(val.strip())
    return names


def _configured_internal_envs_toml(tox: dict[str, object]) -> set[str]:
    """Collect default and configured internal tox environment names from TOML."""
    internal = set(_PROVISIONING_ENVS)
    keys = ("provision_tox_env", "package_env", "isolated_build_env")
    internal.update(_collect_envs_from_mapping(tox, keys))
    base = tox.get("env_run_base")
    if isinstance(base, dict):
        internal.update(_collect_envs_from_mapping(base, keys[1:]))
    envs = tox.get("env")
    if isinstance(envs, dict):
        for sec in envs.values():
            if isinstance(sec, dict):
                internal.update(_collect_envs_from_mapping(sec, keys[1:]))
    return internal


def _without_provisioning(
    names: Iterable[str], internal_envs: set[str] | frozenset[str] = _PROVISIONING_ENVS
) -> list[str]:
    """Remove tox's internal packaging/provisioning environments."""
    return list(
        dict.fromkeys(name for name in names if name and name not in internal_envs)
    )


def _resolve_ini_setting(
    sec_name: str,
    section: configparser.SectionProxy | None,
    key: str,
    parser: configparser.ConfigParser,
    seen: set[str],
) -> str | None:
    """Recursively resolve a setting through an INI section's declared base chain."""
    if section is not None and key in section:
        return section[key]
    seen.add(sec_name)
    if section is not None and "base" in section:
        base_val = section["base"].strip()
        bases = (
            [b.strip() for b in base_val.split(",") if b.strip()] if base_val else []
        )
    elif sec_name != "testenv" and parser.has_section("testenv"):
        bases = ["testenv"]
    else:
        bases = []

    for base_name in bases:
        if base_name in seen:
            continue
        base_sec = (
            parser[base_name]
            if parser.has_section(base_name)
            else parser[f"testenv:{base_name}"]
            if parser.has_section(f"testenv:{base_name}")
            else None
        )
        if base_sec is not None:
            val = _resolve_ini_setting(base_name, base_sec, key, parser, seen)
            if val is not None:
                return val
    return None


def _resolve_toml_setting(
    sec_name: str,
    sec_data: dict[str, object],
    key: str,
    tox: dict[str, object],
    seen: set[str],
) -> object:
    """Recursively resolve a setting through an environment's TOML base chain."""
    if key in sec_data:
        return sec_data[key]
    seen.add(sec_name)
    if "base" in sec_data:
        raw_base = sec_data["base"]
        if isinstance(raw_base, str):
            bases = [raw_base]
        elif isinstance(raw_base, list):
            bases = [b for b in raw_base if isinstance(b, str)]
        else:
            bases = []
    elif sec_name != "env_run_base":
        bases = ["env_run_base"]
    else:
        bases = []

    envs = tox.get("env")
    for base_name in bases:
        if base_name in seen:
            continue
        base_data = (
            tox.get("env_run_base")
            if base_name == "env_run_base"
            else envs.get(base_name)
            if isinstance(envs, dict) and base_name in envs
            else tox.get(base_name)
        )
        if isinstance(base_data, dict):
            val = _resolve_toml_setting(base_name, base_data, key, tox, seen)
            if val is not _MISSING:
                return val
    return _MISSING


def _toml_env_setting(
    name: str, section: dict[str, object], key: str, tox: dict[str, object]
) -> object | None:
    """Look up a TOML setting for an environment, falling back to its base chain."""
    val = _resolve_toml_setting(name, section, key, tox, set())
    return val if val is not _MISSING else None


def _ini_tasks(config: _Config) -> list[Task]:
    """Build tasks from an INI-based tox configuration file."""
    parser = config.data
    if not isinstance(parser, configparser.ConfigParser):
        return []
    tox_section = "tox:tox" if config.path.name == "setup.cfg" else "tox"
    has_envlist = parser.has_section(tox_section) and (
        "envlist" in parser[tox_section] or "env_list" in parser[tox_section]
    )
    if has_envlist:
        envlist = parser.get(
            tox_section,
            "envlist",
            fallback=parser.get(tox_section, "env_list", fallback=""),
        )
        names = [
            name
            for item in _split_envlist(envlist)
            for name in _expand_env_name(item, parser)
        ]
    else:
        names = ["py"]
    generative_sections: dict[str, configparser.SectionProxy] = {}
    explicit: list[str] = []
    for section_name in parser.sections():
        if not section_name.startswith("testenv:"):
            continue
        for name in _expand_env_name(section_name.removeprefix("testenv:"), parser):
            explicit.append(name)
            generative_sections.setdefault(name, parser[section_name])
    internal_envs = _configured_internal_envs_ini(parser, tox_section)
    names = _without_provisioning([*names, *explicit], internal_envs)
    tasks: list[Task] = []
    for name in names:
        exact_section = f"testenv:{name}"
        section = (
            parser[exact_section]
            if parser.has_section(exact_section)
            else generative_sections.get(name)
        )
        description = _resolve_ini_setting(
            exact_section, section, "description", parser, set()
        )
        commands_pre = _resolve_ini_setting(
            exact_section, section, "commands_pre", parser, set()
        )
        commands = _resolve_ini_setting(
            exact_section, section, "commands", parser, set()
        )
        commands_post = _resolve_ini_setting(
            exact_section, section, "commands_post", parser, set()
        )
        tasks.append(
            Task(
                name=name,
                prefix="tox",
                argv_base=("tox", "-e", name),
                passthrough_prefix=("--",),
                description=description,
                definition=_render_command_groups(
                    _filter_ini_commands(commands_pre, name),
                    _filter_ini_commands(commands, name),
                    _filter_ini_commands(commands_post, name),
                ),
                source_file=config.path.name,
            )
        )
    return tasks


def _toml_tasks(config: _Config) -> list[Task]:
    """Build tasks from a native TOML tox configuration table."""
    tox = config.data
    if not isinstance(tox, dict):
        return []
    if "env_list" in tox:
        raw_env_list = tox["env_list"]
    elif "envlist" in tox:
        raw_env_list = tox["envlist"]
    else:
        raw_env_list = ["py"]

    names = (
        [name for name in raw_env_list if isinstance(name, str)]
        if isinstance(raw_env_list, list)
        else [
            name
            for item in _split_envlist(raw_env_list)
            for name in _expand_env_name(item)
        ]
        if isinstance(raw_env_list, str)
        else []
    )
    envs = tox.get("env")
    envs = envs if isinstance(envs, dict) else {}
    explicit = [
        name
        for name, value in envs.items()
        if isinstance(name, str) and isinstance(value, dict)
    ]
    internal_envs = _configured_internal_envs_toml(tox)
    names = _without_provisioning([*names, *explicit], internal_envs)
    tasks: list[Task] = []
    for name in names:
        section = envs.get(name)
        section = section if isinstance(section, dict) else {}
        desc = _toml_env_setting(name, section, "description", tox)
        tasks.append(
            Task(
                name=name,
                prefix="tox",
                argv_base=("tox", "-e", name),
                passthrough_prefix=("--",),
                description=desc if isinstance(desc, str) else None,
                definition=_render_command_groups(
                    _toml_env_setting(name, section, "commands_pre", tox),
                    _toml_env_setting(name, section, "commands", tox),
                    _toml_env_setting(name, section, "commands_post", tox),
                ),
                source_file=config.path.name,
            )
        )
    return tasks


class ToxProvider:
    """Task provider for tox test automation configurations."""

    prefix = "tox"

    def detect(self, cwd: Path) -> bool:
        """Check whether a supported tox configuration exists in the directory."""
        return _find_config(cwd) is not None

    def discover(self, cwd: Path) -> list[Task]:
        """Discover runnable tox environments as tasks from configuration."""
        config = _find_config(cwd)
        if config is None:
            return []
        return _ini_tasks(config) if config.kind == "ini" else _toml_tasks(config)
