"""Managed-hook materialization + drift-sync (HATS-837 extract from Assembler).

Owns the managed-hook cluster: runtime-hook scripts (``library/hooks/``) and
their ``.claude/settings.json`` wiring, worktree-hook scripts
(``library/wt-hooks/``), and skill-declared git hooks (``.githooks/``), plus the
HATS-833 drift detectors and the session-start :meth:`HooksManager.sync_hooks`.

Narrow DI: ``project_dir`` + a live ``project_config`` reference + a ``compose``
callable (the only back-coupling into the assembler's composition layer). This
module never imports ``Assembler`` — the dependency runs the other way.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from .composer import (
    CompositionResult,
    collect_runtime_hooks as _collect_runtime_hooks,
    collect_worktree_hooks as _collect_worktree_hooks,
    resolve_skill_script as _resolve_runtime_script,
)
from .githooks import (
    GITHOOKS_DIR,
    GITHOOKS_MANIFEST,
    git_hooks_changes as _git_hooks_changes_fn,
    git_hooks_drift as _git_hooks_drift_fn,
    install_git_hooks as _install_git_hooks_fn,
)
from .paths import (
    builtin_library_hooks as _builtin_library_hooks,
    hooks_dir as _lib_hooks_dir,
    managed_runtime_hook_filename as _managed_runtime_hook_filename,
    managed_wt_hook_filename as _managed_wt_hook_filename,
    wt_hooks_dir as _wt_hooks_dir,
)
from .providers import get_provider
from .safe_delete import discard as _safe_discard
from .safe_delete import replace as _safe_replace

if TYPE_CHECKING:
    from .models import ProjectConfig, RuntimeHook

logger = logging.getLogger(__name__)

_MANAGED_HEADER = "# ai-hats managed — do not edit"


class HookError(Exception):
    """Unrecoverable managed-hook materialization fault (e.g. broken package data).

    Hook-local so this low-level module never imports from the higher-level
    ``assembler`` package (which imports *us*, not the reverse).
    """


class HookSurface(StrEnum):
    """The three managed-hook surfaces a :class:`HookChange` can belong to."""

    RUNTIME = "runtime"
    WT = "wt"
    GIT = "git"


class HookChangeKind(StrEnum):
    """What changed on a managed hook."""

    MISSING = "missing"  # absent → materialized
    CONTENT = "content"  # bytes drifted → updated
    WIRING = "wiring"  # settings.json managed entry (re)written
    STALE = "stale"  # no longer composed → swept


class HookSyncStatus(StrEnum):
    """Outcome status of :meth:`HooksManager.sync_hooks`."""

    SYNCED = "synced"  # drifted surfaces were re-materialized
    IN_SYNC = "in-sync"  # already consistent; no-op
    SKIPPED = "skipped"  # nothing to do (not a git repo / no active role)
    VERSION_SKEW = "version-skew"  # binary behind upstream — refuse to heal blind


def _read_manifest(path: Path) -> set[str]:
    """Managed names recorded in a ``.manifest`` (one per line, ``#`` comments skipped)."""
    if not path.exists():
        return set()
    return {
        ln.strip()
        for ln in path.read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }


def _write_manifest(path: Path, names: set[str], *, reason: str, project_dir: Path) -> None:
    """Write the managed-names manifest (sorted, do-not-edit header)."""
    body = _MANAGED_HEADER + "\n" + "\n".join(sorted(names)) + "\n"
    _safe_replace(path, body.encode(), reason=reason, project_dir=project_dir)


@dataclass(frozen=True)
class HookChange:
    """One managed-hook surface change detected/healed by :meth:`HooksManager.sync_hooks`.

    ``name`` is the script name (runtime/wt) or git event (git). ``surface`` and
    ``kind`` are coerced+validated at construction — a bad value raises ``ValueError``.
    """

    surface: HookSurface
    name: str
    kind: HookChangeKind

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", HookSurface(self.surface))
        object.__setattr__(self, "kind", HookChangeKind(self.kind))


@dataclass(frozen=True)
class HookSyncResult:
    """Outcome of :meth:`HooksManager.sync_hooks`.

    ``changes`` lists per-hook drift (and, on ``synced``, what was healed); empty
    on ``in-sync`` / ``skipped``. Drives the session-start heal note (HATS-833).
    """

    status: HookSyncStatus
    detail: str = ""
    changes: tuple[HookChange, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", HookSyncStatus(self.status))


class HooksManager:
    """Materialize + drift-sync all managed-hook surfaces (HATS-837).

    See module docstring for the narrow-DI contract.
    """

    def __init__(
        self,
        project_dir: Path,
        project_config: "ProjectConfig",
        *,
        compose: Callable[[str], CompositionResult],
    ) -> None:
        self.project_dir = project_dir
        self.project_config = project_config
        self.compose = compose

    # ----- materialization (the init/materialization-phase facade) -----

    def materialize(self, result: "CompositionResult | None") -> None:
        """Bring every managed-hook surface on disk in sync with ``result``.

        Single facade for the materialization phase (HATS-837 review): provider
        settings.json wiring + the three surfaces. The git-repo guard lives here,
        not in the caller — a non-git project dir simply skips git hooks.
        """
        provider = get_provider(self.project_config.provider)
        provider.ensure_runtime_hooks(self.project_dir, result)
        self.materialize_runtime_hooks(result)
        self.materialize_worktree_hooks(result)
        if result is not None and (self.project_dir / ".git").exists():
            self.install_git_hooks(result)

    def materialize_runtime_hooks(self, result: "CompositionResult | None" = None) -> None:
        """Materialize runtime-hook scripts to ``<ai_hats_dir>/library/hooks/``.

        Two sources under one managed manifest: the package-data ``*.sh`` guards
        (the shared-state safety net, HATS-467/437 — must exist on disk because
        Claude Code's PreToolUse channel execs the file) and each composed skill's
        declared ``runtime_hooks`` script (HATS-597). ``result`` is ``None`` on the
        bare-bump path, leaving only the guards. Idempotent; raises
        :class:`HookError` on a broken install (package data missing).
        """
        source_root = _builtin_library_hooks()
        if source_root is None:
            raise HookError("ai_hats.library.hooks not found in package data — broken install")

        target_dir = _lib_hooks_dir(self.project_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = target_dir / ".manifest"

        previous = _read_manifest(manifest_path)
        new_names = self._write_runtime_guards(target_dir, source_root)
        new_names |= self._write_skill_runtime_scripts(target_dir, result)
        self._sweep_stale(target_dir, previous - new_names, reason="materialize-pretooluse-sweep")
        _write_manifest(
            manifest_path,
            new_names,
            reason="materialize-pretooluse-manifest",
            project_dir=self.project_dir,
        )

    def _write_runtime_guards(self, target_dir: Path, source_root: Path) -> set[str]:
        """Copy package-data ``*.sh`` guards into ``target_dir``; return their names."""
        names: set[str] = set()
        for src in sorted(source_root.iterdir()):
            if not src.is_file() or src.suffix != ".sh":
                continue
            names.add(src.name)
            _safe_replace(
                target_dir / src.name,
                src.read_bytes(),
                reason="materialize-pretooluse",
                project_dir=self.project_dir,
                mode=0o755,
            )
        return names

    def _write_skill_runtime_scripts(
        self, target_dir: Path, result: "CompositionResult | None"
    ) -> set[str]:
        """Copy composed skills' ``runtime_hooks`` scripts into ``target_dir`` (HATS-597)."""
        names: set[str] = set()
        if result is None:
            return names
        for _event, entries in self._collect_skill_runtime_hooks(result).items():
            for skill_name, hook in entries:
                src = self._resolve_skill_script(skill_name, hook.script, result)
                if src is None:
                    continue
                dest_name = _managed_runtime_hook_filename(skill_name, hook.script)
                names.add(dest_name)
                _safe_replace(
                    target_dir / dest_name,
                    src.read_bytes(),
                    reason="materialize-runtime-hook",
                    project_dir=self.project_dir,
                    mode=0o755,
                )
        return names

    def materialize_worktree_hooks(self, result: "CompositionResult | None" = None) -> None:
        """Materialize skill ``wt_in`` / ``wt_out`` scripts to ``library/wt-hooks/`` (HATS-823).

        Mirrors :meth:`materialize_runtime_hooks` (managed dir + manifest + sweep),
        minus the package guards. No dir/manifest when nothing is or was declared,
        so projects without worktree hooks stay clean.
        """
        target_dir = _wt_hooks_dir(self.project_dir)
        manifest_path = target_dir / ".manifest"

        previous = _read_manifest(manifest_path)
        pending = self._collect_worktree_pending(result)
        new_names = {dest for dest, _src in pending}

        if not new_names and not previous:
            return  # nothing now or before → no dir/manifest

        target_dir.mkdir(parents=True, exist_ok=True)
        for dest_name, src in pending:
            _safe_replace(
                target_dir / dest_name,
                src.read_bytes(),
                reason="materialize-worktree-hook",
                project_dir=self.project_dir,
                mode=0o755,
            )
        self._sweep_stale(
            target_dir, previous - new_names, reason="materialize-worktree-hook-sweep"
        )
        _write_manifest(
            manifest_path,
            new_names,
            reason="materialize-worktree-hook-manifest",
            project_dir=self.project_dir,
        )

    def _collect_worktree_pending(
        self, result: "CompositionResult | None"
    ) -> list[tuple[str, Path]]:
        """Resolve composed wt-hook scripts to ``(dest_name, src_path)`` pairs."""
        if result is None:
            return []
        pending: list[tuple[str, Path]] = []
        for entries in self._collect_skill_worktree_hooks(result).values():
            for skill_name, hook in entries:
                src = self._resolve_skill_script(skill_name, hook.script, result)
                if src is None:
                    continue
                pending.append((_managed_wt_hook_filename(skill_name, hook.script), src))
        return pending

    def install_git_hooks(self, result: CompositionResult) -> None:
        """Install skill-declared git hooks.

        The ``.githooks/`` mechanics (dispatcher, manifest, ``core.hooksPath``) stay
        a pure-function module (:mod:`ai_hats.githooks`) with its own tests; this
        method is the manager's entry point onto that surface.
        """
        _install_git_hooks_fn(self.project_dir, result)

    def _sweep_stale(self, target_dir: Path, stale_names: set[str], *, reason: str) -> None:
        """Discard managed files no longer in the composition."""
        for stale in stale_names:
            _safe_discard(target_dir / stale, reason=reason, project_dir=self.project_dir)

    # ----- composition derivations (thin delegates to composer) -----

    def _collect_skill_runtime_hooks(
        self, result: CompositionResult
    ) -> dict[str, list[tuple[str, RuntimeHook]]]:
        """Composed skills' declared runtime hooks (HATS-597). Delegates to composer."""
        return _collect_runtime_hooks(result)

    def _collect_skill_worktree_hooks(self, result: CompositionResult):
        """Composed skills' worktree hooks by kind (HATS-823). Delegates to composer."""
        return _collect_worktree_hooks(result)

    @staticmethod
    def _resolve_skill_script(
        skill_name: str, script_path: str, result: CompositionResult
    ) -> Path | None:
        """Resolve a skill-declared script path to an absolute path."""
        return _resolve_runtime_script(result, skill_name, script_path)

    # ----- HATS-593/833: drift-detecting re-materialization -----

    def sync_hooks(self, result: CompositionResult | None = None) -> HookSyncResult:
        """Re-materialize ANY drifted managed-hook surface (HATS-593 → HATS-833).

        Generalizes the git-only drift net to all three surfaces that
        :meth:`materialize` writes but a plain interactive launch skips. Drift-gated
        + idempotent: a clean no-op when every surface is in sync. The sole trigger
        is session start (:meth:`WrapRunner._resync_managed_hooks`); ``result`` may
        be supplied to reuse the session's composition, composed here when ``None``.

        Refuses to heal from a stale binary (``version-skew``): materializing from
        an out-of-date ai-hats could write hooks that don't match the merged source.
        """
        cfg = self.project_config
        effective_role = cfg.active_role or cfg.default_role
        if not effective_role:
            return HookSyncResult(status=HookSyncStatus.SKIPPED, detail="no active role")
        if result is None:
            result = self.compose(effective_role)
        provider = get_provider(cfg.provider)

        changes = self._detect_changes(result, provider)
        if not changes:
            return HookSyncResult(status=HookSyncStatus.IN_SYNC)

        # Failure-mode #5: a binary strictly behind upstream may derive hooks that
        # don't match the merged repo — name the drift but refuse to heal blind
        # (HATS-833 req-7: never silently skip).
        if self._binary_behind_source():
            return HookSyncResult(
                status=HookSyncStatus.VERSION_SKEW,
                detail="installed ai-hats is behind upstream — run 'ai-hats self update'",
                changes=tuple(changes),
            )

        self._heal_surfaces({c.surface for c in changes}, result, provider)
        return HookSyncResult(status=HookSyncStatus.SYNCED, changes=tuple(changes))

    def _detect_changes(self, result: CompositionResult, provider) -> list[HookChange]:
        """All drifted managed-hook changes across the three surfaces."""
        changes: list[HookChange] = []
        changes.extend(self._runtime_hooks_changes(result, provider))
        changes.extend(self._wt_hooks_changes(result))
        if (self.project_dir / ".git").exists():
            changes.extend(self._git_hooks_changes(result))
        return changes

    def _heal_surfaces(
        self, surfaces: set[HookSurface], result: CompositionResult, provider
    ) -> None:
        """Re-materialize only the drifted surfaces (each materializer is idempotent)."""

        def _heal_runtime() -> None:
            provider.ensure_runtime_hooks(self.project_dir, result)
            self.materialize_runtime_hooks(result)

        healers = {
            HookSurface.RUNTIME: _heal_runtime,
            HookSurface.WT: lambda: self.materialize_worktree_hooks(result),
            HookSurface.GIT: lambda: self.install_git_hooks(result),
        }
        for surface in surfaces:
            healer = healers.get(surface)
            if healer is None:
                logger.warning("sync_hooks: no healer for surface %r — left undrifted", surface)
                continue
            healer()

    def _runtime_hooks_changes(
        self, result: "CompositionResult | None", provider
    ) -> list[HookChange]:
        """Runtime-hook drift: script bytes (``library/hooks/``) + settings.json wiring.

        Bytes and wiring operate on DIFFERENT sets (the ``shared_state_classifier.sh``
        helper is materialized but not wired), so the two detectors stay separate.
        """
        return [
            HookChange(surface=HookSurface.RUNTIME, name=name, kind=kind)
            for name, kind in (
                *self._runtime_bytes_changes(result),
                *provider.runtime_wiring_changes(self.project_dir, result),
            )
        ]

    def _runtime_bytes_changes(self, result: "CompositionResult | None") -> list[tuple[str, str]]:
        """Drift of materialized ``library/hooks/`` bytes vs the composed source.

        Mirrors :meth:`materialize_runtime_hooks`'s dual source (package guards +
        skill scripts) so the detector cannot false-report in-sync.
        """
        expected: dict[str, bytes] = {}
        try:
            src_root = _builtin_library_hooks()  # worktree-aware builtin resolver (HATS-831)
            if src_root is not None and src_root.is_dir():
                for src in src_root.iterdir():
                    if src.is_file() and src.suffix == ".sh":
                        expected[src.name] = src.read_bytes()
        except OSError:
            return []  # broken install — let the loud materialize path own it
        if result is not None:
            for _event, entries in self._collect_skill_runtime_hooks(result).items():
                for skill_name, hook in entries:
                    src = self._resolve_skill_script(skill_name, hook.script, result)
                    if src is None:
                        continue
                    expected[_managed_runtime_hook_filename(skill_name, hook.script)] = (
                        src.read_bytes()
                    )
        return self._bytes_surface_changes(_lib_hooks_dir(self.project_dir), expected)

    def _wt_hooks_changes(self, result: "CompositionResult | None") -> list[HookChange]:
        """Drift of materialized ``library/wt-hooks/`` bytes vs composed source."""
        expected: dict[str, bytes] = {}
        if result is not None:
            for entries in self._collect_skill_worktree_hooks(result).values():
                for skill_name, hook in entries:
                    src = self._resolve_skill_script(skill_name, hook.script, result)
                    if src is None:
                        continue
                    expected[_managed_wt_hook_filename(skill_name, hook.script)] = src.read_bytes()
        return [
            HookChange(surface=HookSurface.WT, name=name, kind=kind)
            for name, kind in self._bytes_surface_changes(_wt_hooks_dir(self.project_dir), expected)
        ]

    @staticmethod
    def _bytes_surface_changes(
        target_dir: Path, expected: dict[str, bytes]
    ) -> list[tuple[str, HookChangeKind]]:
        """Diff a managed bytes-surface dir against ``{name: bytes}`` via its ``.manifest``.

        Returns ``[(name, kind)]`` with kind ``stale`` / ``missing`` / ``content``.
        """
        managed = _read_manifest(target_dir / ".manifest")
        out: list[tuple[str, HookChangeKind]] = []
        for name in sorted(managed - set(expected)):
            out.append((name, HookChangeKind.STALE))
        for name in sorted(expected):
            p = target_dir / name
            if name not in managed or not p.is_file():
                out.append((name, HookChangeKind.MISSING))
                continue
            try:
                if p.read_bytes() != expected[name]:
                    out.append((name, HookChangeKind.CONTENT))
            except OSError:
                out.append((name, HookChangeKind.MISSING))
        return out

    def _git_hooks_changes(self, result: CompositionResult) -> list[HookChange]:
        """Git-hook drift as :class:`HookChange` list, deduped to the git EVENT.

        Two scripts in one ``pre-push.d`` collapse to one ``pre-push`` line per kind.
        """
        manifest = _read_manifest(self.project_dir / GITHOOKS_DIR / GITHOOKS_MANIFEST)
        seen: set[tuple[str, str]] = set()
        out: list[HookChange] = []
        for rel, kind in _git_hooks_changes_fn(self.project_dir, result, manifest):
            event = rel.split(".d/", 1)[0] if ".d/" in rel else rel
            key = (event, kind)
            if key in seen:
                continue
            seen.add(key)
            out.append(HookChange(surface=HookSurface.GIT, name=event, kind=kind))
        return out

    def _git_hooks_drift(self, result: CompositionResult) -> bool:
        """True if managed git hooks on disk diverge from ``result`` (via githooks)."""
        manifest = _read_manifest(self.project_dir / GITHOOKS_DIR / GITHOOKS_MANIFEST)
        return _git_hooks_drift_fn(self.project_dir, result, manifest)

    def _binary_behind_source(self) -> bool:
        """True if the installed ai-hats binary is strictly behind upstream.

        Reuses the update-check cache (the update-banner signal). Best-effort — any
        read error means "unknown", treated as "not behind" so a healthy heal is
        never blocked by a cold cache.
        """
        try:
            from .update_check import read_cache

            entry = read_cache(self.project_dir)
        except Exception:  # noqa: BLE001 — version-skew detection is best-effort
            return False
        return bool(entry is not None and entry.has_update)
