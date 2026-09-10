"""The project's geometry as one value — resolved once, passed down (HATS-1606).

The ONE walk-up in the ai-hats + ai-hats-core cone. rack keeps its sanctioned
copy (its import-hygiene pin forbids this package); parity between the two is
held by a conformance test, not shared code.
"""

from __future__ import annotations

import hashlib
import os
import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

ENV_PROJECT_DIR = "AI_HATS_PROJECT_DIR"
ENV_AI_HATS_DIR = "AI_HATS_DIR"
ENV_CACHE_HOME = "AI_HATS_CACHE_HOME"
ENV_XDG_CACHE_HOME = "XDG_CACHE_HOME"
ENV_USER_HOME = "AI_HATS_USER_HOME"
CONFIG_NAME = "ai-hats.yaml"


def is_onboarded(candidate: Path) -> bool:
    """The ONE marker table. The four historical resolvers each kept their own —
    the integrator's never accepted ai-hats.yaml, rack's did: same cd, two roots."""
    return (candidate / ".agent").is_dir() or (candidate / CONFIG_NAME).is_file()


def pin_is_foreign(pin: str | None, root: Path) -> bool:
    """The one trust comparison of ADR-0025 D3 — every consumer calls this,
    none re-derives it: same ~-expansion, same physical normalization."""
    return bool(pin) and Path(pin).expanduser().resolve() != root.expanduser().resolve()


class ForeignPinPolicy(Enum):
    """Reaction to a pin naming another project — the caller's parameter, not a hardcode (ADR-0025 D3)."""

    REFUSE = "refuse"  # a backlog op under a foreign pin has no safe continuation
    WARN_AND_IGNORE = "warn_and_ignore"  # warn once, answer with the structural root


class ProjectNotFoundError(Exception):
    """No marker between ``start`` and the filesystem root.

    Deliberately NOT a fallback to cwd: the silent fallback IS the
    stray-ancestor bug (a forgotten ``/private/tmp/.agent`` captures every run
    under /tmp). A caller that means "anchor right here" says so:
    ``ProjectLayout.at(cwd)``.
    """

    def __init__(self, start: Path) -> None:
        self.start = start
        super().__init__(
            f"no ai-hats project above {start}: no ancestor holds .agent/ or {CONFIG_NAME}"
        )


class ForeignProjectPinError(Exception):
    """The pin names another project and the caller chose REFUSE."""


def resolve_root(
    start: Path,
    environ: Mapping[str, str],
    *,
    on_foreign_pin: ForeignPinPolicy,
) -> Path:
    """The structural root for ``start``.

    The order is the contract:
      1. worktree hop FIRST — a linked worktree carries a tracked copy of the
         markers, so a marker walk from inside it answers the worktree, not the
         project; the hop is conditional on the MAIN checkout being onboarded;
      2. marker walk-up otherwise;
      3. pin check LAST (ADR-0025 D2) — after the hop, or a sub-agent whose pin
         legitimately names the main checkout gets called foreign. The pin
         never redirects the answer; it is a consistency check.
    """
    resolved = start.expanduser().resolve()

    hop = _main_worktree_root(resolved)
    if hop is not None and is_onboarded(hop):
        root = hop
    else:
        root = next((c for c in (resolved, *resolved.parents) if is_onboarded(c)), None)
        if root is None:
            raise ProjectNotFoundError(start)

    pin = environ.get(ENV_PROJECT_DIR)
    if pin_is_foreign(pin, root):
        if on_foreign_pin is ForeignPinPolicy.REFUSE:
            raise ForeignProjectPinError(
                f"{ENV_PROJECT_DIR}={pin!r} names another project — structural root is {root}"
            )
        warnings.warn(
            f"{ENV_PROJECT_DIR}={pin!r} belongs to another project — ignoring the leaked "
            f"session pin; answering with {root}.",
            stacklevel=2,
        )
    return root


def _main_worktree_root(start: Path) -> Path | None:
    """Pure-fs gitlink hop: a linked worktree (``.git`` FILE) resolves to the main
    checkout root; a ``.git`` DIRECTORY means we already stand in the main repo.

    No git subprocess — the metadata (``gitdir`` + ``commondir``) is read
    directly, exactly as rack's copy does.
    """
    for candidate in (start, *start.parents):
        git = candidate / ".git"
        if git.is_dir():
            return None
        if git.is_file():
            return _resolve_gitlink(git)
    return None


def _resolve_gitlink(git_file: Path) -> Path | None:
    """``<wt>/.git`` (``gitdir: <maindotgit>/worktrees/<name>``) → main root."""
    try:
        text = git_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text[len("gitdir:") :].strip())
    if not gitdir.is_absolute():
        gitdir = (git_file.parent / gitdir).resolve()
    try:
        common_rel = (gitdir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    common = (gitdir / common_rel).resolve()
    return common.parent if common.name == ".git" else None


@dataclass(frozen=True)
class TrackerLayout:
    """The tracker-class tree — what a backlog consumer needs, and nothing else."""

    base: Path  # <ai_hats_dir>/tracker

    @property
    def tasks_dir(self) -> Path:
        return self.base / "backlog" / "tasks"

    @property
    def proposals_dir(self) -> Path:
        return self.base / "backlog" / "proposals"

    @property
    def decisions_dir(self) -> Path:
        return self.base / "decisions"


@dataclass(frozen=True)
class SessionsLayout:
    """The session-class tree — what session/observe consumers need, and nothing else."""

    base: Path  # <ai_hats_dir>/sessions

    @property
    def runs(self) -> Path:
        return self.base / "runs"

    @property
    def retros(self) -> Path:
        return self.base / "retros"

    @property
    def handoffs(self) -> Path:
        return self.base / "handoffs"

    @property
    def audits(self) -> Path:
        return self.base / "audits"

    @property
    def worktrees(self) -> Path:
        return self.base / "worktrees"


@dataclass(frozen=True)
class LibraryLayout:
    """The materialized library mirror — the assembler's and the hook writers' tree."""

    root: Path  # <ai_hats_dir>/library

    @property
    def rules(self) -> Path:
        return self.root / "rules"

    @property
    def skills(self) -> Path:
        return self.root / "skills"

    @property
    def hooks(self) -> Path:
        return self.root / "hooks"


@dataclass(frozen=True)
class VersionsLayout:
    """The blue-green install tree — geometry only.

    Whether a version is complete or runnable is a filesystem question; the
    integrator's ``version_refs`` answers it against these paths.
    """

    root: Path  # <ai_hats_dir>/versions

    @property
    def current_pointer(self) -> Path:
        return self.root / "current"

    def dir(self, sha: str) -> Path:
        return self.root / sha

    def sentinel(self, sha: str) -> Path:
        return self.dir(sha) / ".complete"


@dataclass(frozen=True)
class CacheLayout:
    """The machine-local, regenerable tree — outside the checkout on purpose."""

    root: Path  # <cache home>/<project key>

    @property
    def sessions(self) -> Path:
        return self.root / "sessions"

    def session(self, session_id: str) -> Path:
        return self.sessions / session_id

    @property
    def worktree_checkouts(
        self,
    ) -> Path:  # the trees themselves; their metadata is sessions.worktrees
        return self.root / "worktrees"


def cache_home(environ: Mapping[str, str]) -> Path:
    """Cache-class base, outside any project: ``AI_HATS_CACHE_HOME`` →
    ``XDG_CACHE_HOME``/ai-hats → ``<user home>/.cache/ai-hats``.

    Both env vars name a BASE, never a final root — the layout always appends
    ``project_key``, so a leaked var cannot merge two projects' caches.
    """
    raw = environ.get(ENV_CACHE_HOME)
    if raw:
        return Path(raw).expanduser()
    xdg = environ.get(ENV_XDG_CACHE_HOME)
    if xdg:
        return Path(xdg).expanduser() / "ai-hats"
    home = environ.get(ENV_USER_HOME)
    return (Path(home).expanduser() if home else Path.home()) / ".cache" / "ai-hats"


def project_key(root: Path) -> str:
    """Stable per-project dir name: ``<slug>-<sha256(abs path)[:8]>``.

    The digest is what makes it unique (two checkouts sharing a basename get
    different keys); the slug is there so a human can read ``ls ~/.cache/ai-hats``.
    """
    resolved = root.expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:8]
    slug = "".join(c if (c.isalnum() or c in "._-") else "-" for c in resolved.name)
    slug = slug.strip("-.") or "project"
    return f"{slug}-{digest}"


@dataclass(frozen=True)
class ProjectLayout:
    """The project's geometry: (root, base, cache) plus per-consumer sub-layouts.

    Knowledge is split by consumer class: a tracker consumer takes ``.tracker``,
    a session consumer ``.sessions``, a hook writer ``.library`` — none sees the
    others' tree. Carries no config and reads nothing at access time: the cache
    root, the one value that comes from the environment, is settled when the
    layout is built.
    """

    root: Path  # the project checkout
    base: Path  # <root>/.agent/ai-hats, or its sanctioned override
    cache_root: Path | None = None  # <cache home>/<project key>; None only for a bare constructor

    @classmethod
    def compute(
        cls,
        root: Path,
        environ: Mapping[str, str],
        *,
        ai_hats_dir: str
        | None = None,  # the config's say, passed as DATA — layout never reads yaml
    ) -> ProjectLayout:
        """``base``: trusted AI_HATS_DIR env > ``ai_hats_dir`` > default.

        Env trust is scoped by the pin (ADR-0025 D3): no pin — env-wins, an
        explicit human override; pin agrees — honoured; pin names another
        project — a leaked session pin, dropped with a warn rather than allowed
        to redirect this project's writes.
        """
        cache = cache_home(environ) / project_key(root)
        override = environ.get(ENV_AI_HATS_DIR)
        if override:
            pin = environ.get(ENV_PROJECT_DIR)
            if pin_is_foreign(pin, root):
                warnings.warn(
                    f"{ENV_AI_HATS_DIR}={override!r} is pinned to project {pin!r} — foreign "
                    f"to {root}; ignoring the leaked session pin.",
                    stacklevel=2,
                )
            else:
                return cls(root=root, base=Path(override).expanduser(), cache_root=cache)
        if ai_hats_dir:
            return cls(root=root, base=root / ai_hats_dir, cache_root=cache)
        return cls(root=root, base=root / ".agent" / "ai-hats", cache_root=cache)

    @classmethod
    def at(cls, root: Path | str, environ: Mapping[str, str] | None = None) -> ProjectLayout:
        """Deliberate anchor, no resolution: ``init`` on a bare directory, tests.

        ``environ`` only names the cache home — the project is not resolved.
        """
        root = Path(root)
        env = os.environ if environ is None else environ
        return cls(
            root=root,
            base=root / ".agent" / "ai-hats",
            cache_root=cache_home(env) / project_key(root),
        )

    # -- per-consumer views ---------------------------------------------------

    @property
    def tracker(self) -> TrackerLayout:
        return TrackerLayout(self.base / "tracker")

    @property
    def sessions(self) -> SessionsLayout:
        return SessionsLayout(self.base / "sessions")

    @property
    def library(self) -> LibraryLayout:
        return LibraryLayout(self.base / "library")

    @property
    def cache(self) -> CacheLayout:
        if self.cache_root is None:
            raise LookupError("layout built without a cache root — build it with compute() or at()")
        return CacheLayout(self.cache_root)

    # -- base-level singles, each with its own single consumer ----------------

    @property
    def state_md(self) -> Path:
        return self.base / "STATE.md"

    @property
    def traces(self) -> Path:  # pipeline traces live beside sessions, not inside
        return self.base / "traces"

    @property
    def default_venv(self) -> Path:
        return self.base / ".venv"

    @property
    def versions(self) -> VersionsLayout:  # blue-green versioned venvs
        return VersionsLayout(self.base / "versions")

    @property
    def pipeline_steps(self) -> Path:  # user-authored steps, loaded by name before any YAML
        return self.base / "pipeline_steps"

    @property
    def user_hooks(self) -> Path:  # project-authored, outside the managed namespace
        return self.base / "user-hooks"

    @property
    def user_rules(self) -> Path:  # project-authored, read at compose time
        return self.base / "user-rules"

    @property
    def last_backup(self) -> Path:
        return self.base / ".last_backup"
