"""Session ``config.toml``: the person's file with the grok session policy on top."""

from __future__ import annotations

import logging
import tomllib
from datetime import date, datetime, time
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

logger = logging.getLogger(__name__)

_COMPAT_VENDORS = ("claude", "cursor", "codex")
_COMPAT_CELLS = ("skills", "rules", "agents", "mcps", "hooks", "sessions")
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


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
        mapped = _ESCAPES.get(char)
        if mapped is not None:
            escaped.append(mapped)
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


def _emit_bool(value: bool) -> str:
    return "true" if value else "false"


def _emit_int(value: int) -> str:
    return str(value)


def _emit_isoformat(value: date | datetime | time) -> str:
    return value.isoformat()


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


def _emit_list(value: list) -> str:
    return "[" + ", ".join(_emit_array_item(item) for item in value) + "]"


# Exact type: bool is an int and datetime is a date, so isinstance order would lie.
_SCALAR_HANDLERS = {
    bool: _emit_bool,
    int: _emit_int,
    float: _emit_float,
    str: _quote_string,
    datetime: _emit_isoformat,
    date: _emit_isoformat,
    time: _emit_isoformat,
    list: _emit_list,
}


def _emit_scalar(value: object) -> str:
    handler = _SCALAR_HANDLERS.get(type(value))
    if handler is None:
        raise TypeError(f"cannot emit {type(value).__name__} as TOML")
    return handler(value)


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


def asking_config(text: str) -> str:
    """ACP child prompts. The person's auto or yolo does not cross this launch."""
    document = _parsed_person(text, None) or {}
    ui = document.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        document["ui"] = ui
    ui["permission_mode"] = "default"
    ui["yolo"] = False
    return _emit_toml(document)


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


def session_config(layout: ProjectLayout, path: Path | None = None) -> str:
    """Person's config at ``path``, with session compat cells and skills.ignore forced on top."""
    source = "" if path is None else _read_person_config(path)
    document = _parsed_person(source, path)
    if document is None:
        document = _allowlist(layout)
    else:
        _apply_session_policy(document, layout, path)
    return _emit_toml(document)
