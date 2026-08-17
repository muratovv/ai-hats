"""Self-heal a stale editable that breaks a surface-plugin provider (HATS-966).

Detects ``packages/surfaces/*`` provider plugins whose editable ``.pth`` target
was deleted (e.g. a torn-down worktree) and re-points them to their canonical
repo dir. Signal = the entry-point module fails ``find_spec`` (not the
``direct_url`` project path — that disagrees with the real ``.pth`` target
``<proj>/src`` when only ``src`` moves). Design/scope: plan.md + work_log R1.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import site
import subprocess
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .provider_entry_points import PROVIDER_ENTRY_POINT_GROUP, _provider_entry_points

logger = logging.getLogger(__name__)

# Repo layout: surface-plugin members live at ``<repo>/packages/surfaces/<name>``.
SURFACES_SUBPATH = ("packages", "surfaces")
WORKSPACE_SUBDIR = SURFACES_SUBPATH[0]


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
    """A provider entry point whose module does not resolve in this venv."""

    ep_name: str  # entry-point name, e.g. "cline"
    module: str  # top-level import module, e.g. "ai_hats_cline"


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


def find_uninstalled_surface_members(repo_root: Path) -> list[BrokenProvider]:
    """In-tree packages/surfaces/* members whose provider entry point is missing in venv."""
    surfaces_dir = repo_root.joinpath(*SURFACES_SUBPATH)
    if not surfaces_dir.is_dir():
        return []
    installed_ep_names = {ep.name for ep in _provider_entry_points()}
    missing: list[BrokenProvider] = []
    for member in sorted(p for p in surfaces_dir.iterdir() if p.is_dir()):
        ep_name = member.name
        inits = sorted(member.glob("src/*/__init__.py"))
        module = inits[0].parent.name if inits else f"ai_hats_{member.name}"
        if ep_name not in installed_ep_names or not _module_resolves(module):
            missing.append(BrokenProvider(ep_name=ep_name, module=module))
    return missing


def find_broken_surface_providers(repo_root: Path | None = None) -> list[BrokenProvider]:
    """Provider entry points whose module can't be located, plus uninstalled in-tree surfaces."""
    broken: list[BrokenProvider] = []
    seen: set[str] = set()
    for ep in _provider_entry_points():
        module = _ep_module(ep.value)
        if not _module_resolves(module):
            broken.append(BrokenProvider(ep_name=ep.name, module=module))
            seen.add(ep.name)
    if repo_root is not None:
        for missing in find_uninstalled_surface_members(repo_root):
            if missing.ep_name not in seen:
                broken.append(missing)
                seen.add(missing.ep_name)
    return broken


def get_surface_remediation(provider_name: str, repo_root: Path | None = None) -> str | None:
    """Return a remediation string if provider_name is an in-tree or known surface plugin."""
    from .paths import editable_install_root
    from .surfaces_registry import get_surface_info

    info = get_surface_info(provider_name)
    root = repo_root or editable_install_root("ai-hats")
    if root is not None:
        member = root.joinpath(*SURFACES_SUBPATH, provider_name)
        if member.is_dir():
            return f"uv pip install -e packages/surfaces/{provider_name}"
    if info is not None and info.package_name:
        return f"pip install {info.package_name}  # (or: uv pip install -e packages/surfaces/{provider_name} in dev repo)"
    return None


def surface_editable_map(repo_root: Path) -> dict[str, Path]:
    """Map top-level module -> canonical ``packages/surfaces/*`` member dir.

    Keyed by module (globbed ``<member>/src/*/__init__.py``, mirroring the
    launcher's member probe) rather than dist name, so it never depends on
    ``EntryPoint.dist`` being populated.
    """
    out: dict[str, Path] = {}
    surfaces = repo_root.joinpath(*SURFACES_SUBPATH)
    if not surfaces.is_dir():
        return out
    for member in sorted(p for p in surfaces.iterdir() if p.is_dir()):
        for init in sorted(member.glob("src/*/__init__.py")):
            out[init.parent.name] = member
    return out


def workspace_editable_map(repo_root: Path) -> dict[str, Path]:
    """Map top-level module -> canonical ``packages/*`` workspace member dir.

    HATS-1367: the surface map covers plugins only, so a workspace member whose
    editable ``.pth`` went dangling (the ``ai-hats-tracker`` shape) had no
    canonical dir to be re-pointed at. ``packages/surfaces`` is the plugin
    category dir, not a member — :func:`surface_editable_map` owns what's inside.
    """
    out: dict[str, Path] = {}
    packages = repo_root / WORKSPACE_SUBDIR
    if not packages.is_dir():
        return out
    for member in sorted(p for p in packages.iterdir() if p.is_dir()):
        if member.name == SURFACES_SUBPATH[-1]:
            continue
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
    """Every re-pointable editable that doesn't resolve — surfaces and workspace."""
    broken = find_broken_surface_providers(repo_root=repo_root)
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
    """Re-point broken surface-plugin editables to their canonical repo dir.

    Pure control flow with ``installer`` / ``verifier`` injected for tests. A
    broken provider that maps to a ``packages/surfaces/*`` member is re-pointed
    then verified; an unmapped one (arbitrary out-of-tree ``-e``) is warned, never
    touched (HATS-966 R3). Idempotent: no broken providers -> empty result.
    """
    broken = find_broken_surface_providers(repo_root=repo_root) if broken is None else broken
    mapping = surface_editable_map(repo_root) if mapping is None else mapping
    healed: list[Healed] = []
    warned: list[Warned] = []
    for bp in broken:
        canonical = mapping.get(bp.module)
        if canonical is None:
            warned.append(
                Warned(
                    bp,
                    reason=f"module {bp.module!r} has no packages/surfaces/* member",
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
    """Detect + re-point stale editables — surface plugins and workspace members.

    Business logic only — the caller renders the result. Returns ``None`` when
    there is nothing to do: not an editable dev checkout, or nothing broken
    (fast path, no lock taken). Otherwise serialized behind a venv-scoped filelock
    so concurrent launches/updates never race on ``uv pip install``; a held lock
    is best-effort skipped (a peer is already healing).

    HATS-966 covered ``packages/surfaces/*``; HATS-1367 widened it to the rest of
    ``packages/*``, whose dangling ``.pth`` this channel used to walk straight past.
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
    mapping = {**surface_editable_map(repo_root), **workspace_editable_map(repo_root)}
    try:
        with FileLock(str(lock_path or _default_lock_path()), timeout=lock_timeout):
            return heal_surface_editables(repo_root, broken=broken, mapping=mapping)
    except Timeout:
        return None


def _uv_install_surface_package(package_name: str) -> None:
    """Install surface package via uv into THIS venv (HATS-1179)."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            package_name,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


class ProviderInstallationError(RuntimeError):
    """Raised when auto-installing a surface plugin package fails (HATS-1394)."""


def _is_surface_module_installed(provider_name: str) -> bool:
    """Return True iff provider_name entry point exists and its top-level module resolves."""
    for ep in _provider_entry_points():
        if ep.name == provider_name:
            module = _ep_module(ep.value)
            if _module_resolves(module):
                return True
    return False


def _refresh_editable(canonical: Path) -> None:
    """Expose a healed surface source to the already-running interpreter."""
    site.addsitedir(str(canonical / "src"))
    importlib.invalidate_caches()


def ensure_surface_plugin_installed(
    provider_name: str,
    repo_root: Path | None = None,
    installer: Callable[[str], None] = _uv_install_surface_package,
    *,
    healer: Callable[[Path | None], HealResult | None] | None = None,
    installed_checker: Callable[[str], bool] | None = None,
    refresher: Callable[[Path], None] | None = None,
) -> bool:
    """Ensure a surface plugin package is installed in venv (HATS-1179, HATS-1394).

    1. If provider_name is already installed & importable, returns True.
    2. Runs editable heal if in-tree checkout is present.
    3. If still uninstalled and known in KNOWN_SURFACES, attempts uv pip install.
    Raises ProviderInstallationError if installer fails.
    Returns True if provider is installed after these steps, False otherwise.
    """
    from .surfaces_registry import get_surface_info

    if healer is None:
        healer = run_editable_heal
    if installed_checker is None:
        installed_checker = _is_surface_module_installed
    if refresher is None:
        refresher = _refresh_editable

    if installed_checker(provider_name):
        return True

    # 1. Try editable heal (for in-tree packages/surfaces/* checkout)
    heal_result = healer(repo_root)
    targeted_heal = (
        next(
            (healed for healed in heal_result.healed if healed.provider.ep_name == provider_name),
            None,
        )
        if heal_result
        else None
    )
    if targeted_heal:
        refresher(targeted_heal.canonical)
    if installed_checker(provider_name):
        return True

    # 2. Try package installer if known surface package name is available
    info = get_surface_info(provider_name)
    if info and info.package_name:
        try:
            installer(info.package_name)
        except Exception as exc:
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                stderr = (
                    exc.stderr.decode(errors="replace")
                    if isinstance(exc.stderr, bytes)
                    else exc.stderr
                )
                detail = stderr.strip() or detail
            logger.warning("Failed to install surface package %s: %s", info.package_name, detail)
            raise ProviderInstallationError(
                f"Failed to auto-install surface plugin {provider_name!r} "
                f"({info.package_name}): {detail}"
            ) from exc

    return installed_checker(provider_name)


__all__ = [
    "PROVIDER_ENTRY_POINT_GROUP",
    "SURFACES_SUBPATH",
    "BrokenProvider",
    "HealResult",
    "Healed",
    "ProviderInstallationError",
    "Warned",
    "ensure_surface_plugin_installed",
    "find_broken_surface_providers",
    "find_uninstalled_surface_members",
    "get_surface_remediation",
    "heal_surface_editables",
    "is_broken_install_exception",
    "run_editable_heal",
    "surface_editable_map",
]
