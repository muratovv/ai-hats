"""The project's geometry as one value — resolved once, passed down (HATS-1606).

The ONE walk-up in the ai-hats + ai-hats-core cone. rack keeps its sanctioned
copy (its import-hygiene pin forbids this package); parity between the two is
held by a conformance test, not shared code.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

ENV_PROJECT_DIR = "AI_HATS_PROJECT_DIR"
ENV_AI_HATS_DIR = "AI_HATS_DIR"
CONFIG_NAME = "ai-hats.yaml"


def _is_onboarded(candidate: Path) -> bool:
    # The ONE marker table. The four historical resolvers each kept their own —
    # the integrator's never accepted ai-hats.yaml, rack's did: same cd, two roots.
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
    if hop is not None and _is_onboarded(hop):
        root = hop
    else:
        root = next((c for c in (resolved, *resolved.parents) if _is_onboarded(c)), None)
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
class ProjectLayout:
    """The project's geometry: (root, base) plus per-consumer sub-layouts.

    Knowledge is split by consumer class (the taxonomy paths/_dirs.py already
    names): a tracker consumer takes ``.tracker``, a session consumer takes
    ``.sessions`` — neither sees the other's tree. Carries no config and reads
    nothing at access time.
    """

    root: Path  # the project checkout
    base: Path  # <root>/.agent/ai-hats, or its sanctioned override

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
                return cls(root=root, base=Path(override).expanduser())
        if ai_hats_dir:
            return cls(root=root, base=root / ai_hats_dir)
        return cls(root=root, base=root / ".agent" / "ai-hats")

    @classmethod
    def at(cls, root: Path) -> ProjectLayout:
        """Deliberate anchor, no resolution: ``init`` on a bare directory, tests."""
        return cls(root=root, base=root / ".agent" / "ai-hats")

    # -- per-consumer views ---------------------------------------------------

    @property
    def tracker(self) -> TrackerLayout:
        return TrackerLayout(self.base / "tracker")

    @property
    def sessions(self) -> SessionsLayout:
        return SessionsLayout(self.base / "sessions")

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
    def versions(self) -> Path:  # blue-green versioned venvs
        return self.base / "versions"
