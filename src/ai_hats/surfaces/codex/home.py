"""The person's Codex home as planning sees it (ADR-0036 D2): enumerated once
before the plan by ``probe_home`` and handed in as ``Host.home``, then projected
into a session home by ``plan_session_home`` — the planner reads nothing itself."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ai_hats_core.layout import cache_home

from ai_hats.materialization import (
    MaterializationEntry,
    describe_mkdir,
    describe_symlink,
    describe_write_text,
)

from ..mirror import mirror_entries
from ..plan import CompositionPlan, Digested, mirror_name
from .session_auth import auth_digest, plan_auth
from .session_home import (
    SESSION_HOME_MANIFEST,
    SessionHomeMetadata,
    render_session_home_metadata,
)

ENV_CODEX_BASE_HOME = "AI_HATS_CODEX_BASE_HOME"
# Codex's own two names: honoured on the way in, rewritten on the way out.
ENV_CODEX_HOME = "CODEX_HOME"
ENV_CODEX_SQLITE_HOME = "CODEX_SQLITE_HOME"
AI_HATS_HOME_DIR = ".ai-hats"
SESSION_HOMES_DIR = "session-homes"
#: What a session home never links: it mirrors its own skills, stages its own
#: copy of the credential, and the sqlite state stays where Codex keeps it.
_UNPROJECTED = frozenset({"skills", "auth.json", AI_HATS_HOME_DIR})
SQLITE_ARTIFACT_SUFFIXES = (".sqlite", ".sqlite-shm", ".sqlite-wal", ".sqlite-journal")


@dataclass(frozen=True)
class CodexHome(Digested):
    """The facts of the person's home a session home is projected from."""

    base_home: Path
    sqlite_home: Path
    #: Names in the base home a session home links, in name order.
    entries: tuple[str, ...]
    #: Names under ``<base_home>/skills``, in name order; empty without that directory.
    skills: tuple[str, ...]
    #: sha256 of ``<base_home>/auth.json``; ``None`` where the person is not logged in by file.
    auth_digest: str | None


def managed_root(base_home: Path) -> Path:
    return base_home / AI_HATS_HOME_DIR / SESSION_HOMES_DIR


def session_home_of(base_home: Path, project_key: str, session_id: str) -> Path:
    return managed_root(base_home) / project_key / session_id


def configured_base_home(environ: Mapping[str, str]) -> Path:
    configured = environ.get(ENV_CODEX_BASE_HOME) or environ.get(ENV_CODEX_HOME)
    if configured:
        candidate = Path(configured).expanduser()
    else:
        home = environ.get("HOME")
        candidate = (Path(home) if home else Path.home()) / ".codex"
    if not candidate.is_absolute() or not candidate.is_dir():
        raise RuntimeError("Codex base home must be an existing absolute directory")
    base_home = candidate.resolve()
    resolved_cache_home = cache_home(environ).resolve()
    if (
        base_home == resolved_cache_home
        or base_home in resolved_cache_home.parents
        or resolved_cache_home in base_home.parents
    ):
        raise RuntimeError("Codex base home must be disjoint from the ai-hats cache home")
    return base_home


def validate_sqlite_home(sqlite_home: Path, base_home: Path, environ: Mapping[str, str]) -> Path:
    resolved_cache_home = cache_home(environ).resolve()
    if sqlite_home == resolved_cache_home or resolved_cache_home in sqlite_home.parents:
        raise RuntimeError("Codex SQLite home must be outside the ai-hats cache home")
    root = managed_root(base_home)
    if sqlite_home == root or root in sqlite_home.parents:
        raise RuntimeError("Codex SQLite home must be outside managed session homes")
    return sqlite_home


def configured_sqlite_home(environ: Mapping[str, str], base_home: Path) -> Path:
    configured = environ.get(ENV_CODEX_SQLITE_HOME)
    sqlite_home = Path(configured).expanduser() if configured else base_home
    if not sqlite_home.is_absolute():
        raise RuntimeError("Codex SQLite home must be an absolute directory")
    return validate_sqlite_home(sqlite_home.resolve(strict=False), base_home, environ)


def probe_home(environ: Mapping[str, str]) -> CodexHome:
    """The one read of the home a plan is built on: the two roots, what the
    session home links, the person's own skills, the credential's digest."""
    base_home = configured_base_home(environ)
    sqlite_home = configured_sqlite_home(environ, base_home)
    try:
        # `sessions` is linked whether or not it is there yet: the plan creates it.
        names = {path.name for path in base_home.iterdir()} | {"sessions"}
    except OSError:
        raise RuntimeError("Codex session home projection failed") from None
    entries = tuple(
        sorted(
            name
            for name in names
            if name not in _UNPROJECTED and not name.endswith(SQLITE_ARTIFACT_SUFFIXES)
        )
    )
    base_skills = base_home / "skills"
    skills: tuple[str, ...] = ()
    if base_skills.is_dir():
        try:
            skills = tuple(sorted(path.name for path in base_skills.iterdir()))
        except OSError:
            raise RuntimeError("Codex base skill projection failed") from None
    return CodexHome(
        base_home=base_home,
        sqlite_home=sqlite_home,
        entries=entries,
        skills=skills,
        auth_digest=auth_digest(base_home),
    )


def plan_session_home(
    composition: CompositionPlan, home: CodexHome, session_home: Path, *, project_key: str
) -> list[MaterializationEntry]:
    """The session home as entries, every one outside the session root and
    saying so: the manifest, the skill mirror, the links onto the person's
    home, the staged credential, the person's skills the role does not shadow."""
    skills_root = session_home / "skills"
    metadata = SessionHomeMetadata(
        base_home=home.base_home,
        sqlite_home=home.sqlite_home,
        project_key=project_key,
        session_id=session_home.name,
    )
    mirrored = {mirror_name(skill) for skill in composition.skills}
    entries = [
        describe_mkdir(session_home),
        describe_write_text(
            session_home / SESSION_HOME_MANIFEST, render_session_home_metadata(metadata)
        ),
        describe_mkdir(skills_root),
        *mirror_entries(composition, skills_root),
        describe_mkdir(home.base_home / "sessions"),
        *(describe_symlink(home.base_home / name, session_home / name) for name in home.entries),
        *plan_auth(home.base_home, session_home, home.auth_digest),
        *(
            describe_symlink(home.base_home / "skills" / name, skills_root / name)
            for name in home.skills
            if name not in mirrored
        ),
    ]
    return [dataclasses.replace(entry, escape=True) for entry in entries]


def session_home_env(home: CodexHome, session_home: Path) -> dict[str, str]:
    """Where the child finds its home, its state and the home it was projected from."""
    return {
        ENV_CODEX_HOME: str(session_home),
        ENV_CODEX_SQLITE_HOME: str(home.sqlite_home),
        ENV_CODEX_BASE_HOME: str(home.base_home),
    }


__all__ = [
    "AI_HATS_HOME_DIR",
    "ENV_CODEX_BASE_HOME",
    "ENV_CODEX_HOME",
    "ENV_CODEX_SQLITE_HOME",
    "SESSION_HOMES_DIR",
    "SQLITE_ARTIFACT_SUFFIXES",
    "CodexHome",
    "configured_base_home",
    "configured_sqlite_home",
    "managed_root",
    "plan_session_home",
    "probe_home",
    "session_home_env",
    "session_home_of",
    "validate_sqlite_home",
]
