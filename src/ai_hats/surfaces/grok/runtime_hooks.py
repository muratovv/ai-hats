"""Session-scoped home delivery for the grok surface.

Grok reads hooks and rules from ``$GROK_HOME``, so a session needs its own home.
``logs/`` writes through. ``sessions/`` does not: the grant file sits beside the
transcript, and only that directory is created in the real home — the session
cache is removed at the end — then linked into the session home.
Shims stay beside the manifest, not in ``hooks/``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import subprocess
import tomllib
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote

from ai_hats_core.git_env import scrubbed_git_env
from ai_hats_core.layout import ProjectLayout

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_SESSION_CACHE_DIR
from ai_hats.materialization import (
    MaterializationEntry,
    describe_mkdir,
    describe_private_text,
    describe_symlink,
    describe_write_executable,
    describe_write_text,
    render_json,
)

from ..hook_channel import BINDABLE_EVENTS, surface_timeout
from ..mirror import manifest_rows
from ..plan import CompositionPlan, Host
from .home import GROK_HOME_ENV, grok_home
from .session_config import _emit_toml, session_config

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
SESSION_HOME_SUBPATH = "grok-home"
HOOK_SHIMS: tuple[str, ...] = BINDABLE_EVENTS


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


def _scope_root(cwd: Path) -> Path:
    """Git toplevel of ``cwd``; the resolved cwd when this directory is not a repo."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=scrubbed_git_env(),
        )
    except subprocess.CalledProcessError:
        return Path(cwd).resolve()
    text = completed.stdout.strip()
    return Path(text).resolve() if text else Path(cwd).resolve()


def bind_session_store(
    session_home: Path,
    cwd: Path,
    session_id: str | None,
    *,
    fresh: bool,
) -> None:
    """Link prior transcripts in. A fresh transcript dir is created in the real home."""
    encoded = quote(str(_scope_root(cwd)), safe="")
    local = session_home / "sessions" / encoded
    remote = grok_home() / "sessions" / encoded
    if local.resolve() == remote.resolve():
        return
    local.mkdir(parents=True, exist_ok=True)
    remote.mkdir(parents=True, exist_ok=True)
    skip = session_id if fresh else None
    for child in remote.iterdir():
        if child.name.startswith("permission") or child.name == skip:
            continue
        dest = local / child.name
        if dest.exists() or dest.is_symlink():
            continue
        dest.symlink_to(child, target_is_directory=child.is_dir())
    if fresh and session_id:
        _publish_transcript(local, remote, session_id)


def _publish_transcript(local: Path, remote: Path, session_id: str) -> None:
    """The real directory lives in the person's home; the session home only links it."""
    durable = remote / session_id
    visible = local / session_id
    if durable.is_symlink() or visible.exists() or visible.is_symlink():
        return
    durable.mkdir(exist_ok=True)
    visible.symlink_to(durable, target_is_directory=True)


def _trust_copies(session_root: Path) -> list[Path]:
    """Regular files only. A symlink is the person's store, not a saved decision."""
    sessions = session_root.parent
    homes = [sessions]
    if sessions.name == "sessions":
        cache = sessions.parent.parent
        if cache.is_dir():
            homes = [path for path in cache.glob("*/sessions") if path.is_dir()]
    found: list[Path] = []
    for home in homes:
        for path in home.glob("*/grok-home/trusted_folders.toml"):
            if path.is_symlink() or not path.is_file():
                continue
            found.append(path)
    found.sort(key=lambda path: path.stat().st_mtime)
    return found


def _read_trust(path: Path) -> dict | None:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        logger.warning("grok: cannot read folder trust %s: %s", path, exc)
        return None
    if not isinstance(document, dict):
        logger.warning("grok: %s is not a trust table", path)
        return None
    return document


def _folder_rows(document: dict, path: Path) -> dict[str, dict]:
    folders = document.get("folders")
    if folders is None:
        return {}
    if not isinstance(folders, dict):
        logger.warning("grok: %s [folders] is not a table", path)
        return {}
    rows: dict[str, dict] = {}
    for folder, row in folders.items():
        trusted = row.get("trusted") if isinstance(row, dict) else None
        if isinstance(folder, str) and isinstance(trusted, bool):
            rows[folder] = row
            continue
        logger.warning("grok: %s folder %s has no boolean trusted", path, folder)
    return rows


def _decided_at(row: dict) -> int:
    value = row.get("decided_at")
    return value if isinstance(value, int) else 0


def folded_trust_text(global_path: Path, session_root: Path) -> str | None:
    """Text for the person's trust file when a session copy is ahead of it."""
    current = (
        _read_trust(global_path)
        if global_path.is_file() and not global_path.is_symlink()
        else None
    )
    merged: dict[str, dict] = {}
    if current is not None:
        merged.update(_folder_rows(current, global_path))
    for path in _trust_copies(session_root):
        document = _read_trust(path)
        if document is None:
            continue
        for folder, row in _folder_rows(document, path).items():
            held = merged.get(folder)
            if held is None or _decided_at(row) >= _decided_at(held):
                merged[folder] = row
    if current is not None and _folder_rows(current, global_path) == merged:
        return None
    if not merged and current is None:
        return None
    base = {key: value for key, value in (current or {}).items() if key != "folders"}
    if merged:
        base["folders"] = merged
    return _emit_toml(base)


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
    # Grok replaces this file on save, so the symlink never updates the home.
    folded = folded_trust_text(trust, root)
    if folded is not None:
        entries.append(dataclasses.replace(describe_private_text(trust, folded), escape=True))
    if trust.is_file() or folded is not None:
        entries.append(describe_symlink(trust, session_home / "trusted_folders.toml"))
    entries.append(describe_mkdir(session_home / "sessions"))
    global_logs = global_home / "logs"
    entries.append(dataclasses.replace(describe_mkdir(global_logs), escape=True))
    entries.append(describe_symlink(global_logs, session_home / "logs"))
    person_config = global_home / "config.toml"
    entries.append(
        describe_write_text(
            session_home / "config.toml",
            session_config(layout, person_config),
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
    "bind_session_store",
    "plan_session_home",
    "shim_source",
]
