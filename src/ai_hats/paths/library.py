"""Builtin library SOURCE resolution — worktree-aware (HATS-826 / HATS-831).

THE single home for "where is the builtin ``library/`` the engine composes
from?". This is the SHIPPED source (``core``/``usage``/``hooks``/``core/pipelines``),
distinct from :func:`ai_hats.paths.library_dir` (the materialized
``<.agent>/library/`` mirror).

``importlib.resources.files(LIBRARY_PKG)`` hard-pins the editable install
to the MAIN repo regardless of cwd, so library edits made inside a linked
worktree are otherwise invisible to composition (HATS-826). Routing EVERY
consumer (layers, hooks, core pipelines) through these helpers makes
worktree-awareness uniform — and a guard test
(``test_builtin_library_resolver_single_home``) bans the ``files(LIBRARY_PKG)``
call anywhere else, so the resolution cannot silently diverge again (HATS-831).
HATS-876/T18: builtin library = the standalone ``ai_hats_library`` package; the
installed tier routes through ``as_file`` to survive a data-only wheel (P1 #14).
"""

from __future__ import annotations

import atexit
import functools
import warnings
from contextlib import ExitStack
from importlib.resources import as_file, files
from pathlib import Path

from .. import env
from .constants import (
    HOOKS_DIRNAME,
    LIBRARY_LAYERS,
    REQUIRED_LIBRARY_LAYERS,
    LIBRARY_PKG,
    PIPELINES_SUBPATH,
)
from .validation import _validated_library_root, is_library_root


# Layer-root subpaths (from a checkout root) holding the ai_hats_library layers:
# the monorepo/worktree home, then a standalone (git-split) ai-hats-library checkout.
_SOURCE_LIBRARY_SUBPATHS = (
    ("packages", "ai-hats-library", "src", "ai_hats_library"),
    ("src", "ai_hats_library"),
)


def _detect_source_library_root(start: Path) -> Path | None:
    """Walk up from ``start`` for an ai-hats-library *source* checkout; return its root.

    A source checkout holds an ``ai_hats_library`` package serving the full manifest
    (:func:`is_library_root`) — inside the monorepo/worktree
    (``packages/ai-hats-library/src/…``) or a
    standalone git-split checkout (``src/ai_hats_library``). HATS-876 dropped the
    former ``src/ai_hats`` co-requirement so a **library-only checkout** resolves too
    (ADR-0014 §6); a downstream project has neither layout and stays on the installed
    package. Returns the layer-root dir or ``None``.
    """
    for d in (start, *start.parents):
        for parts in _SOURCE_LIBRARY_SUBPATHS:
            root = d.joinpath(*parts)
            if is_library_root(root):
                return root
    return None


_LIB_EXITSTACK = ExitStack()
atexit.register(_LIB_EXITSTACK.close)


@functools.lru_cache(maxsize=1)
def _importlib_library_root() -> Path | None:
    """The installed ``ai_hats_library`` package dir as a real path, or ``None``.

    Routes through ``importlib.resources.as_file`` (review P1 #14) so it survives a
    data-only / zipimported wheel: for the real-dir installs we ship, ``files``
    returns a ``pathlib.Path`` and ``as_file`` yields it unchanged (a no-op
    passthrough — valid on 3.11); a zipimported package extracts once to a temp dir
    held open for the process via a module-level ``ExitStack``. Cached (``lru_cache``)
    so the materialisation happens at most once.
    """
    try:
        res = files(LIBRARY_PKG)
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    try:
        root = _LIB_EXITSTACK.enter_context(as_file(res))
    except (FileNotFoundError, OSError):
        return None
    return root if root.is_dir() else None


@functools.lru_cache(maxsize=None)
def _git_common_dir(start: Path) -> Path | None:
    """The git dir shared by a checkout and every worktree linked to it.

    A linked worktree's ``.git`` is a file pointing at
    ``<main>/.git/worktrees/<name>``; trimming at ``worktrees`` yields the same
    dir the main checkout reports, which is what makes "same repo" cheap to
    decide without spawning git.
    """
    for d in (start, *start.parents):
        dot = d / ".git"
        if dot.is_dir():
            return dot.resolve()
        if not dot.is_file():
            continue
        try:
            text = dot.read_text().strip()
        except OSError as exc:
            warnings.warn(f"unreadable gitlink at {dot}: {exc}", stacklevel=1)
            return None
        if not text.startswith("gitdir:"):
            return None
        gitdir = Path(text.split(":", 1)[1].strip())
        parts = gitdir.parts
        if "worktrees" in parts:
            gitdir = Path(*parts[: parts.index("worktrees")])
        return gitdir.resolve()
    return None


def _is_surprising_divergence(
    cwd_root: Path, pinned_root: Path | None, project_named: bool
) -> bool:
    """True when cwd's library shadows a project that meant a different one.

    Silent by design in the two everyday cases: no project named at all (cwd is
    then the only signal — the HATS-826 fallback), and worktrees of one repo,
    which diverge by construction. What remains is a checkout shadowing an
    unrelated project — the surprise worth a line (HATS-1501).
    """
    if not project_named:
        return False
    if pinned_root is None:
        return True
    common = _git_common_dir(cwd_root)
    return common is None or common != _git_common_dir(pinned_root)


@functools.lru_cache(maxsize=None)
def _warn_library_divergence(cwd_root: Path, pinned_root: Path | None, used: bool) -> None:
    """Report a surprising cwd/project split once per process."""
    verb = "resolved from" if used else "available in"
    warnings.warn(
        f"builtin library {verb} cwd ({cwd_root}) differs from the project's "
        f"({pinned_root or 'installed package'}) — HATS-1501. Set "
        f"AI_HATS_LIBRARY_ROOT to choose explicitly.",
        stacklevel=1,
    )


def _pinned_source_library_root(project_dir: Path | None) -> Path | None:
    """The source library the PROJECT points at — ``project_dir``, else the env pin."""
    if project_dir is not None:
        return _detect_source_library_root(project_dir)
    env_proj = env.project_dir_pin()
    if env_proj:
        return _detect_source_library_root(Path(env_proj))
    return None


def builtin_library_root(
    project_dir: Path | None = None, *, prefer_cwd: bool = False, cwd: Path | None = None
) -> Path | None:
    """Resolve the builtin ``library/`` source root.

    Resolution order (HATS-826 / HATS-1127 / HATS-1501), highest precedence first:

    1. ``AI_HATS_LIBRARY_ROOT`` env override — explicit, greppable seam
       (tests, power users), validated against the full manifest or rejected.
    2. cwd auto-detection of an ai-hats source checkout — **only under
       ``prefer_cwd``** (read-only composition; see below).
    3. ``project_dir`` or ``AI_HATS_PROJECT_DIR`` source auto-detection.
    4. ``importlib.resources`` — the installed package (downstream / default).

    ``cwd`` names the directory whose checkout counts as "here" (default:
    the process cwd) — injected so callers, and tests, need not chdir.

    ``prefer_cwd`` splits two questions that need opposite answers. Composing
    to WRITE must key off ``project_dir``: composing checkout A's library while
    materializing into project B's ``.agent`` is how HATS-1123 shipped one
    worktree's hook bytes into another project, and HATS-1127 closed it by
    pinning both to ``project_dir`` — that is the default here, unchanged.
    Composing to READ (``config show-prompt`` and friends) must key off cwd, or
    a library edit inside a linked worktree is invisible: ``_project_dir`` hops
    a worktree to the MAIN checkout by design so tracker ops reach the one live
    backlog (HATS-524), and reusing that answer rendered master's text at exit 0
    while you edited the worktree's (HATS-1501). Nothing is written on that
    path, so cwd cannot contaminate a target.

    Returns the root dir whose children are ``core``/``usage``/``hooks``/… or
    ``None`` on a broken install. All builtin-library subpaths derive from here.
    """  # comment-length: allow — the read/write split IS the contract
    root = _validated_library_root(env.library_root_override())
    if root is not None:
        return root

    pinned_root = _pinned_source_library_root(project_dir)
    cwd_root = _detect_source_library_root(Path.cwd() if cwd is None else cwd)
    project_named = project_dir is not None or bool(env.project_dir_pin())

    if cwd_root is not None and cwd_root != pinned_root:
        if _is_surprising_divergence(cwd_root, pinned_root, project_named):
            _warn_library_divergence(cwd_root, pinned_root, prefer_cwd)

    # cwd is the fallback when the project names no source library (HATS-826);
    # prefer_cwd promotes it above one that does (HATS-1501).
    order = (cwd_root, pinned_root) if prefer_cwd else (pinned_root, cwd_root)
    for root in order:
        if root is not None and is_library_root(root):
            return root
    return _importlib_library_root()


def _importlib_library_layers() -> list[Path]:
    """Resolve the present ``LIBRARY_LAYERS`` from the installed library package.

    Falls back to an empty list when the package data is missing (sdist
    inspection in CI / broken install) — callers degrade gracefully.
    """
    root = _importlib_library_root()
    if root is None:
        return []
    return [root / layer for layer in LIBRARY_LAYERS if (root / layer).is_dir()]


def builtin_library_layers(
    project_dir: Path | None = None, *, prefer_cwd: bool = False, cwd: Path | None = None
) -> list[Path]:
    """The builtin layers present under the root, lowest priority first.

    Derived from :func:`builtin_library_root` (see it for ``prefer_cwd`` and
    ``cwd``). Every REQUIRED layer must exist under the resolved root, else we
    fall through to the installed package (never a partial builtin); an optional
    layer is included when present and skipped when not (HATS-1834).
    """
    root = (
        builtin_library_root(project_dir, prefer_cwd=prefer_cwd, cwd=cwd)
        if project_dir is not None
        else builtin_library_root(prefer_cwd=prefer_cwd, cwd=cwd)
    )
    if root is None:
        return []
    if all((root / layer).is_dir() for layer in REQUIRED_LIBRARY_LAYERS):
        return [root / layer for layer in LIBRARY_LAYERS if (root / layer).is_dir()]
    return _importlib_library_layers()


def builtin_library_hooks(project_dir: Path | None = None) -> Path | None:
    """The builtin ``library/hooks/`` source dir, or ``None`` if unresolved.

    Callers decide on ``None``: the managed-hook whitelist degrades to empty;
    materialization raises (a broken install is not a state to paper over).
    """
    root = builtin_library_root(project_dir) if project_dir is not None else builtin_library_root()
    if root is None:
        return None
    hooks = root / HOOKS_DIRNAME
    return hooks if hooks.is_dir() else None


def core_pipeline_path(name: str, project_dir: Path | None = None) -> Path | None:
    """Filesystem path to a builtin core pipeline YAML, or ``None`` if unresolved.

    Returns a plain ``Path`` — the root is already materialised to a real dir by
    the :func:`_importlib_library_root` ``as_file`` seam, so no per-call wrapper.
    """
    root = builtin_library_root(project_dir) if project_dir is not None else builtin_library_root()
    if root is None:
        return None
    return root.joinpath(*PIPELINES_SUBPATH, f"{name}.yaml")


__all__ = [
    "_detect_source_library_root",
    "builtin_library_root",
    "builtin_library_layers",
    "builtin_library_hooks",
    "core_pipeline_path",
]
