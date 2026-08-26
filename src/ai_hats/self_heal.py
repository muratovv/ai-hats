"""Self-heal a stale editable that breaks an installed workspace member (HATS-966).

Detects ``packages/*`` members whose editable ``.pth`` target was deleted (e.g. a
torn-down worktree) and re-points them to their canonical repo dir. Signal = the
module fails ``find_spec`` (not the ``direct_url`` project path — that disagrees
with the real ``.pth`` target ``<proj>/src`` when only ``src`` moves).

Re-pointing an editable is all this module does — it never installs a
distribution on the user's behalf (HATS-1826).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path

from .provider_entry_points import PROVIDER_ENTRY_POINT_GROUP, _provider_entry_points

# Repo layout: workspace members live at ``<repo>/packages/<name>``.
WORKSPACE_SUBDIR = "packages"


def is_broken_install_exception(exc: Exception) -> bool:
    """Return True if exc represents a broken install (ImportError or module AttributeError)."""
    if isinstance(exc, ImportError):
        return True
    if isinstance(exc, AttributeError):
        obj = getattr(exc, "obj", None)
        if isinstance(obj, types.ModuleType):
            return True
        msg = str(exc)
        if msg.startswith("module ") or msg.startswith("partially initialized module "):
            return True
    return False


@dataclass(frozen=True)
class BrokenProvider:
    """An entry point or workspace member whose module does not resolve in this venv."""

    ep_name: str  # entry-point name or member dir, e.g. "ai-hats-wt"
    module: str  # top-level import module, e.g. "ai_hats_wt"


@dataclass(frozen=True)
class Healed:
    provider: BrokenProvider
    canonical: Path


@dataclass(frozen=True)
class Warned:
    provider: BrokenProvider
    reason: str
    fix: str


@dataclass(frozen=True)
class HealResult:
    healed: list[Healed]
    warned: list[Warned]

    def is_noop(self) -> bool:
        return not self.healed and not self.warned


def _ep_module(value: str) -> str:
    """Top-level module from an entry-point value: ``pkg.sub:Obj`` -> ``pkg``."""
    return value.split(":", 1)[0].split(".", 1)[0].strip()


def _module_resolves(module: str) -> bool:
    """True iff ``module`` is importable in THIS interpreter (no code executed)."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        # A dangling path entry / missing parent surfaces here — treat as broken.
        return False


def find_broken_surface_providers() -> list[BrokenProvider]:
    """Surface entry points whose top-level module can't be located in this venv."""
    broken: list[BrokenProvider] = []
    for ep in _provider_entry_points():
        module = _ep_module(ep.value)
        if not _module_resolves(module):
            broken.append(BrokenProvider(ep_name=ep.name, module=module))
    return broken


def workspace_editable_map(repo_root: Path) -> dict[str, Path]:
    """Map top-level module -> canonical ``packages/*`` workspace member dir.

    Keyed by module (globbed ``<member>/src/*/__init__.py``, mirroring the
    launcher's member probe) rather than dist name, so it never depends on
    ``EntryPoint.dist`` being populated (HATS-966, HATS-1367).
    """
    out: dict[str, Path] = {}
    packages = repo_root / WORKSPACE_SUBDIR
    if not packages.is_dir():
        return out
    for member in sorted(p for p in packages.iterdir() if p.is_dir()):
        for init in sorted(member.glob("src/*/__init__.py")):
            out[init.parent.name] = member
    return out


def find_broken_workspace_members(repo_root: Path) -> list[BrokenProvider]:
    """Workspace members present in the tree whose module doesn't import here."""
    return [
        BrokenProvider(ep_name=member.name, module=module)
        for module, member in workspace_editable_map(repo_root).items()
        if not _module_resolves(module)
    ]


def find_broken_editables(repo_root: Path) -> list[BrokenProvider]:
    """Every re-pointable editable that doesn't resolve — provider plugins and workspace."""
    broken = find_broken_surface_providers()
    seen = {b.module for b in broken}
    broken.extend(m for m in find_broken_workspace_members(repo_root) if m.module not in seen)
    return broken


def _uv_reinstall_editable(package_dir: Path) -> None:
    """Re-point a stale editable to ``package_dir`` in THIS venv.

    ``--python sys.executable`` is mandatory — bare ``uv pip install`` targets the
    nearest cwd venv, not this interpreter (mirrors ``maintenance._build_install_cmd``).
    ``--no-deps`` — only the ``.pth`` is stale; deps are unchanged.
    """
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--python",
            sys.executable,
            "-e",
            str(package_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _module_imports_in_subprocess(module: str) -> bool:
    """Verify ``module`` imports in a FRESH interpreter (this process's sys.path
    was frozen at startup with the stale ``.pth``, so an in-process check would
    still report broken right after a re-point). Guarded on a valid identifier so
    a hostile entry-point value can't reach the ``-c`` snippet."""
    if not module.isidentifier():
        return False
    return (
        subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
        ).returncode
        == 0
    )


def heal_surface_editables(
    repo_root: Path,
    *,
    broken: list[BrokenProvider] | None = None,
    mapping: dict[str, Path] | None = None,
    installer=_uv_reinstall_editable,
    verifier=_module_imports_in_subprocess,
) -> HealResult:
    """Re-point broken editables to their canonical repo dir.

    Pure control flow with ``installer`` / ``verifier`` injected for tests. A
    broken module that maps to a ``packages/*`` member is re-pointed then
    verified; an unmapped one (arbitrary out-of-tree ``-e``) is warned, never
    touched (HATS-966 R3). Idempotent: no broken providers -> empty result.
    """
    broken = find_broken_surface_providers() if broken is None else broken
    mapping = workspace_editable_map(repo_root) if mapping is None else mapping
    healed: list[Healed] = []
    warned: list[Warned] = []
    for bp in broken:
        canonical = mapping.get(bp.module)
        if canonical is None:
            warned.append(
                Warned(
                    bp,
                    reason=f"module {bp.module!r} has no packages/* member",
                    fix=f"reinstall it from its source: uv pip install -e <path-to-{bp.module}>",
                )
            )
            continue
        try:
            installer(canonical)
        except Exception as exc:  # noqa: BLE001 - surface any installer failure as a warning
            warned.append(
                Warned(
                    bp,
                    reason=f"re-point failed: {exc}",
                    fix=f"uv pip install --no-deps -e {canonical}",
                )
            )
            continue
        if verifier(bp.module):
            healed.append(Healed(bp, canonical))
        else:
            warned.append(
                Warned(
                    bp,
                    reason=f"{bp.module!r} still unimportable after re-point",
                    fix=f"uv pip install -e {canonical}  # (retry with deps)",
                )
            )
    return HealResult(healed=healed, warned=warned)


def _default_lock_path() -> Path:
    """Venv-scoped lock file — serializes heals across concurrent launches/updates."""
    return Path(sys.executable).resolve().parent.parent / ".ai-hats-heal.lock"


def run_editable_heal(
    repo_root: Path | None = None,
    *,
    lock_path: Path | None = None,
    lock_timeout: float = 120,
) -> HealResult | None:
    """Detect + re-point stale editables — provider plugins and workspace members.

    Business logic only — the caller renders the result. Returns ``None`` when
    there is nothing to do: not an editable dev checkout, or nothing broken
    (fast path, no lock taken). Otherwise serialized behind a venv-scoped filelock
    so concurrent launches/updates never race on ``uv pip install``; a held lock
    is best-effort skipped (a peer is already healing).
    """
    from filelock import FileLock, Timeout

    from .paths import editable_install_root

    if repo_root is None:
        repo_root = editable_install_root("ai-hats")
    if repo_root is None or not (repo_root / WORKSPACE_SUBDIR).is_dir():
        return None
    broken = find_broken_editables(repo_root)
    if not broken:
        return None
    mapping = workspace_editable_map(repo_root)
    try:
        with FileLock(str(lock_path or _default_lock_path()), timeout=lock_timeout):
            return heal_surface_editables(repo_root, broken=broken, mapping=mapping)
    except Timeout:
        return None


__all__ = [
    "PROVIDER_ENTRY_POINT_GROUP",
    "BrokenProvider",
    "HealResult",
    "Healed",
    "Warned",
    "find_broken_surface_providers",
    "heal_surface_editables",
    "is_broken_install_exception",
    "run_editable_heal",
]
