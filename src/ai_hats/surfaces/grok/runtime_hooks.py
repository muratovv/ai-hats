"""Session-scoped home delivery for the grok surface.

Grok reads global hooks from ``$GROK_HOME/hooks/*.json`` and user rules from
``$GROK_HOME/rules/*.md``. Both are the person's own files, so a per-session
chain and role need a per-session home. ``sessions/`` and ``logs/`` write
through into the real home: the parent resolver does not inherit the child's
``GROK_HOME``, and the transcript has to outlive the session-cache sweep.
Shims stay beside the manifest, not in ``hooks/`` — grok loads every ``*.json``
there, and a script in that directory is not a hook file.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import tomllib
from datetime import date, datetime, time
from importlib.resources import files
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_SESSION_CACHE_DIR
from ai_hats.materialization import (
    MaterializationEntry,
    describe_mkdir,
    describe_symlink,
    describe_write_executable,
    describe_write_text,
    render_json,
)

from ..hook_channel import BINDABLE_EVENTS, surface_timeout
from ..mirror import manifest_rows
from ..plan import CompositionPlan, Host
from .home import GROK_HOME_ENV, grok_home

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
SESSION_HOME_SUBPATH = "grok-home"
HOOK_SHIMS: tuple[str, ...] = BINDABLE_EVENTS
_COMPAT_VENDORS = ("claude", "cursor", "codex")
_COMPAT_CELLS = ("skills", "rules", "agents", "mcps", "hooks", "sessions")


def shim_source(name: str) -> str:
    """The packaged shim's bytes — the planner's own code, read like a constant."""
    return files("ai_hats.surfaces.grok").joinpath("hooks", name).read_text(encoding="utf-8")


def _grok_hooks(hooks_dir: Path, events: tuple[str, ...]) -> str:
    timeout = surface_timeout()
    document = {
        "hooks": {
            event: [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(hooks_dir / event),
                            "timeout": timeout,
                        }
                    ]
                }
            ]
            for event in events
        }
    }
    return render_json(document)


def _ignore_paths(layout: ProjectLayout) -> tuple[str, ...]:
    """Project ``.grok/skills`` and ``.grok/commands`` at the checkout and the cwd."""
    found: list[str] = []
    for base in (layout.root, layout.cwd):
        resolved = Path(base).resolve()
        for name in ("skills", "commands"):
            text = str(resolved / ".grok" / name)
            if text not in found:
                found.append(text)
    return tuple(found)


def _bare_key(key: str) -> bool:
    return bool(key) and all(char.isascii() and (char.isalnum() or char in "_-") for char in key)


def _quote_string(value: str) -> str:
    escaped = ['"']
    for char in value:
        if char == "\\":
            escaped.append("\\\\")
        elif char == '"':
            escaped.append('\\"')
        elif char == "\b":
            escaped.append("\\b")
        elif char == "\t":
            escaped.append("\\t")
        elif char == "\n":
            escaped.append("\\n")
        elif char == "\f":
            escaped.append("\\f")
        elif char == "\r":
            escaped.append("\\r")
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(char)
    escaped.append('"')
    return "".join(escaped)


def _quote_key(key: str) -> str:
    return key if _bare_key(key) else _quote_string(key)


def _emit_float(value: float) -> str:
    rendered = repr(value)
    if rendered in {"nan", "-nan", "inf", "-inf"}:
        raise TypeError(f"TOML has no {rendered}")
    return rendered


def _emit_inline_table(table: dict) -> str:
    if not table:
        return "{}"
    parts: list[str] = []
    for key, value in table.items():
        rendered = _emit_inline_table(value) if isinstance(value, dict) else _emit_scalar(value)
        parts.append(f"{_quote_key(key)} = {rendered}")
    return "{ " + ", ".join(parts) + " }"


def _emit_array_item(value: object) -> str:
    if isinstance(value, dict):
        return _emit_inline_table(value)
    return _emit_scalar(value)


def _emit_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _emit_float(value)
    if isinstance(value, str):
        return _quote_string(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_emit_array_item(item) for item in value) + "]"
    raise TypeError(f"cannot emit {type(value).__name__} as TOML")


def _array_of_tables(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def _dotted(path: tuple[str, ...]) -> str:
    return ".".join(_quote_key(part) for part in path)


def _emit_body(lines: list[str], table: dict, path: tuple[str, ...]) -> None:
    for key, value in table.items():
        if isinstance(value, dict) or _array_of_tables(value):
            continue
        lines.append(f"{_quote_key(key)} = {_emit_scalar(value)}")
    for key, value in table.items():
        if not isinstance(value, dict):
            continue
        child = path + (key,)
        lines.append("")
        lines.append(f"[{_dotted(child)}]")
        _emit_body(lines, value, child)
    for key, value in table.items():
        if not _array_of_tables(value):
            continue
        child = path + (key,)
        for item in value:
            lines.append("")
            lines.append(f"[[{_dotted(child)}]]")
            _emit_body(lines, item, child)


def _emit_toml(document: dict) -> str:
    lines: list[str] = []
    _emit_body(lines, document, ())
    return "\n".join(lines).strip("\n") + "\n"


def _allowlist(layout: ProjectLayout) -> dict:
    return {
        "compat": {vendor: dict.fromkeys(_COMPAT_CELLS, False) for vendor in _COMPAT_VENDORS},
        "skills": {"ignore": list(_ignore_paths(layout))},
    }


def _read_person_config(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        logger.warning("grok: cannot read %s: %s", path, exc)
        return ""


def _parsed_person(source: str, source_path: Path | None) -> dict | None:
    if source == "":
        return None
    try:
        return tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        logger.warning("grok: cannot parse %s: %s", source_path, exc)
        return None


def _merge_ignore(user: list[str], session_paths: tuple[str, ...]) -> list[str]:
    merged: list[str] = []
    for item in [*user, *session_paths]:
        if item not in merged:
            merged.append(item)
    return merged


def _apply_session_policy(document: dict, layout: ProjectLayout, source_path: Path | None) -> None:
    where = source_path if source_path is not None else "config.toml"
    compat = document.get("compat")
    if not isinstance(compat, dict):
        if compat is not None:
            logger.warning(
                "grok: %s [compat] is %s, not a table; rewriting the session cells",
                where,
                type(compat).__name__,
            )
        compat = {}
        document["compat"] = compat
    for vendor in _COMPAT_VENDORS:
        table = compat.get(vendor)
        if not isinstance(table, dict):
            if table is not None:
                logger.warning(
                    "grok: %s [compat.%s] is %s, not a table; rewriting the session cells",
                    where,
                    vendor,
                    type(table).__name__,
                )
            table = {}
            compat[vendor] = table
        for cell in _COMPAT_CELLS:
            table[cell] = False
    skills = document.get("skills")
    session_paths = _ignore_paths(layout)
    if not isinstance(skills, dict):
        if skills is not None:
            logger.warning(
                "grok: %s [skills] is %s, not a table; using the session ignore list",
                where,
                type(skills).__name__,
            )
        document["skills"] = {"ignore": list(session_paths)}
        return
    raw = skills.get("ignore")
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        skills["ignore"] = _merge_ignore(raw, session_paths)
        return
    if raw is not None:
        logger.warning(
            "grok: %s [skills].ignore is not a list of strings; using the session paths",
            where,
        )
    skills["ignore"] = list(session_paths)


def _session_config(
    layout: ProjectLayout,
    source: str = "",
    source_path: Path | None = None,
) -> str:
    """Person's config with session compat cells and skills.ignore forced on top."""
    document = _parsed_person(source, source_path)
    if document is None:
        document = _allowlist(layout)
    else:
        _apply_session_policy(document, layout, source_path)
    return _emit_toml(document)


def plan_session_home(
    composition: CompositionPlan,
    root: Path,
    host: Host,
    *,
    layout: ProjectLayout,
    skills_dir: Path,
    hooks_enabled: bool,
) -> tuple[list[MaterializationEntry], dict[str, str], Path]:
    """The per-session grok home. The role file is the planner's context write.

    Hooks join when the composition carries runtime rows and the session policy
    enables them. Returns entries, env, home.
    """
    rows = manifest_rows(composition, skills_dir) if hooks_enabled else {}
    hooks = {event: rows[event] for event in BINDABLE_EVENTS if event in rows}
    session_home = root / SESSION_HOME_SUBPATH
    hooks_dir = root / "hooks"
    global_home = grok_home()

    entries: list[MaterializationEntry] = [
        describe_mkdir(session_home),
        describe_mkdir(session_home / "rules"),
        describe_symlink(skills_dir, session_home / "skills"),
    ]
    auth = global_home / "auth.json"
    if auth.is_file():
        entries.append(describe_symlink(auth, session_home / "auth.json"))
    trust = global_home / "trusted_folders.toml"
    if trust.is_file():
        entries.append(describe_symlink(trust, session_home / "trusted_folders.toml"))
    for sub in ("sessions", "logs"):
        global_sub = global_home / sub
        entries.append(dataclasses.replace(describe_mkdir(global_sub), escape=True))
        entries.append(describe_symlink(global_sub, session_home / sub))
    person_config = global_home / "config.toml"
    entries.append(
        describe_write_text(
            session_home / "config.toml",
            _session_config(layout, _read_person_config(person_config), person_config),
        )
    )
    if hooks:
        entries += [
            describe_write_executable(hooks_dir / name, shim_source(name)) for name in HOOK_SHIMS
        ]
        entries.append(
            describe_write_text(
                root / "hooks.json",
                json.dumps(
                    {
                        "version": MANIFEST_VERSION,
                        "session": {"id": root.name, "ai_hats_dir": str(layout.base)},
                        "hooks": hooks,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
        )
        entries.append(describe_mkdir(session_home / "hooks"))
        entries.append(
            describe_write_text(
                session_home / "hooks" / "ai-hats.json",
                _grok_hooks(hooks_dir, HOOK_SHIMS),
            )
        )
    env = {
        GROK_HOME_ENV: str(session_home),
        ENV_SESSION_CACHE_DIR: str(root),
        ENV_AI_HATS_PYTHON: str(host.python),
    }
    return entries, env, session_home


__all__ = [
    "HOOK_SHIMS",
    "MANIFEST_VERSION",
    "SESSION_HOME_SUBPATH",
    "plan_session_home",
    "shim_source",
]
