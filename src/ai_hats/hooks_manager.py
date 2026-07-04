"""Managed-hook materialization + drift-sync (HATS-837 extract from Assembler).

Owns the managed-hook cluster: runtime-hook scripts (``library/hooks/``) and
their ``.claude/settings.json`` wiring, worktree-hook scripts
(``library/wt-hooks/``), and skill-declared git hooks (``.githooks/``), plus the
HATS-833 drift detectors and the session-start :meth:`HooksManager.sync_hooks`.

Narrow DI: ``project_dir`` + a live ``project_config`` reference + a ``compose``
callable (carve-out #2, HATS-865: result-less resync edges only) + a
``resolve_provider`` callable. This module never imports ``Assembler`` or the
composition layer — the dependency runs the other way.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from .hook_collection import (
    collect_runtime_hooks as _collect_runtime_hooks,
    collect_worktree_hooks as _collect_worktree_hooks,
    resolve_skill_script as _resolve_runtime_script,
)
from ai_hats_core import CompositionResult, scrubbed_git_env
from .models import SkillMetadata
from .paths import (
    builtin_library_hooks as _builtin_library_hooks,
    hooks_dir as _lib_hooks_dir,
    managed_runtime_hook_filename as _managed_runtime_hook_filename,
    managed_wt_hook_filename as _managed_wt_hook_filename,
    wt_hooks_dir as _wt_hooks_dir,
)
from ai_hats_core.safe_delete import discard as _safe_discard
from ai_hats_core.safe_delete import replace as _safe_replace

if TYPE_CHECKING:
    from .models import ProjectConfig, RuntimeHook
    from .providers import Provider

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
        resolve_provider: "Callable[[str], Provider]",
    ) -> None:
        # HATS-865: provider lookup is DI'd (like ``compose``) so this brick
        # never imports the composition layer.
        self.project_dir = project_dir
        self.project_config = project_config
        self.compose = compose
        self.resolve_provider = resolve_provider

    # ----- materialization (the init/materialization-phase facade) -----

    def materialize(self, result: "CompositionResult | None") -> None:
        """Bring every managed-hook surface on disk in sync with ``result``.

        Single facade for the materialization phase (HATS-837 review): provider
        settings.json wiring + the three surfaces. The git-repo guard lives here,
        not in the caller — a non-git project dir simply skips git hooks.
        """
        provider = self.resolve_provider(self.project_config.provider)
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
                src = _resolve_skill_script(skill_name, hook.script, result)
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

    # HATS-865: ``result`` is required — the runtime callers always have one.
    def materialize_worktree_hooks(self, result: "CompositionResult | None") -> None:
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
                src = _resolve_skill_script(skill_name, hook.script, result)
                if src is None:
                    continue
                pending.append((_managed_wt_hook_filename(skill_name, hook.script), src))
        return pending

    def install_git_hooks(self, result: CompositionResult) -> None:
        """Install skill-declared git hooks (mechanics are the module functions
        below — HATS-837 merged the former ``githooks`` module in)."""
        install_git_hooks(self.project_dir, result)

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

    # ----- HATS-593/833: drift-detecting re-materialization -----

    # HATS-865: ``result`` required-explicit; ``None`` marks the genuinely
    # result-less resync edge where the compose carve-out (#2) fires.
    def sync_hooks(self, result: CompositionResult | None) -> HookSyncResult:
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
        provider = self.resolve_provider(cfg.provider)

        changes = self._detect_changes(result, provider)
        if not changes:
            return HookSyncResult(status=HookSyncStatus.IN_SYNC)

        # Failure-mode #5: a binary strictly behind upstream may derive hooks that
        # don't match the merged repo — name the drift but refuse to heal blind
        # (HATS-833 req-7: never silently skip). Excludes LOCAL channel and a cache
        # about a different build — see `upstream_update` (HATS-846).
        if self.binary_behind_source():
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
                    src = _resolve_skill_script(skill_name, hook.script, result)
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
                    src = _resolve_skill_script(skill_name, hook.script, result)
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
        for rel, kind in git_hooks_changes(self.project_dir, result, manifest):
            event = rel.split(".d/", 1)[0] if ".d/" in rel else rel
            key = (event, kind)
            if key in seen:
                continue
            seen.add(key)
            out.append(HookChange(surface=HookSurface.GIT, name=event, kind=kind))
        return out

    def _git_hooks_drift(self, result: CompositionResult) -> bool:
        """True if managed git hooks on disk diverge from ``result``."""
        manifest = _read_manifest(self.project_dir / GITHOOKS_DIR / GITHOOKS_MANIFEST)
        return git_hooks_drift(self.project_dir, result, manifest)

    def binary_behind_source(self) -> bool:
        """True if the installed ai-hats binary is strictly behind upstream.
        Public since HATS-907: the skills-mirror heal shares this gate.

        Routes through the canonical ``update_check.upstream_update`` predicate
        (HATS-846) — honours LOCAL channel + running-SHA match identically to
        the update banner (on LOCAL always False: the working tree IS the
        merged source). Best-effort — any error means "unknown", treated as
        "not behind" so a healthy heal is never blocked by a cold cache.
        """
        try:
            from .update_check import upstream_update

            return upstream_update(self.project_dir) is not None
        except Exception:  # noqa: BLE001 — version-skew detection is best-effort
            return False


# ----- git-hook mechanics (HATS-837: merged from the former githooks.py) -----
# Pure functions over (project_dir, CompositionResult); the HooksManager methods
# above are the OOP seam onto them.

GITHOOKS_DIR = ".githooks"
GITHOOKS_MANIFEST = ".ai-hats-manifest"
GITHOOKS_DISPATCHER_MARKER = "AI-HATS-DISPATCHER-MARKER"
GITHOOKS_DISPATCHER_TEMPLATE = Path(__file__).parent / "templates" / "githooks" / "dispatcher.sh"


def install_git_hooks(project_dir: Path, result: CompositionResult) -> None:
    """Install git hooks declared by composed skills.

    Skills declare hooks in their `SKILL.md` frontmatter under the top-level
    `ai_hats.git_hooks:` key (HATS-814).
    Each declared script is copied into `.githooks/<event>.d/<skill>-<basename>`,
    a dispatcher script is generated at `.githooks/<event>`, and
    `core.hooksPath` is set to `.githooks` (idempotently).

    Conflict policy:
    - If `.githooks/<event>` exists WITHOUT our marker → leave alone, warn.
    - If `core.hooksPath` is set to a non-`.githooks` value → leave alone, warn.
    - Files inside `<event>.d/` from previous installs are tracked via a
      manifest at `.githooks/.ai-hats-manifest` and removed before re-install,
      so stale hooks from removed skills don't linger.
    """
    declared = _collect_skill_git_hooks(result)
    if not declared:
        # No skill declares git hooks. Don't touch user's repo.
        # Still clean up our previously-installed managed files (in case the
        # user removed all skills with git_hooks) so stale entries don't linger.
        _cleanup_managed_git_hooks(project_dir)
        return

    githooks_dir = project_dir / GITHOOKS_DIR
    githooks_dir.mkdir(exist_ok=True)

    # Remove anything we previously owned, then re-install fresh.
    _cleanup_managed_git_hooks(project_dir)

    new_manifest: list[str] = []
    warnings: list[str] = []

    for event, entries in declared.items():
        if not entries:
            continue
        event_d = githooks_dir / f"{event}.d"
        event_d.mkdir(exist_ok=True)

        for skill_name, script_path in entries:
            src = _resolve_skill_script(skill_name, script_path, result)
            if src is None:
                warnings.append(
                    f"git_hooks: skill '{skill_name}' declares script "
                    f"'{script_path}' but file not found"
                )
                continue
            dest_basename = f"{skill_name}-{src.name}"
            dest = event_d / dest_basename
            shutil.copy2(src, dest)
            dest.chmod(0o755)
            new_manifest.append(f"{event}.d/{dest_basename}")

        # Generate dispatcher (or warn on conflict).
        dispatcher_path = githooks_dir / event
        installed = _install_dispatcher(dispatcher_path)
        if installed:
            new_manifest.append(event)
        else:
            warnings.append(
                f"git_hooks: existing {dispatcher_path} is not managed by "
                f"ai-hats — left in place. Hooks for '{event}' will not run "
                f"unless you wire {event}.d/* into it manually."
            )

    # Persist manifest of files we own.
    manifest_path = githooks_dir / GITHOOKS_MANIFEST
    manifest_path.write_text("\n".join(new_manifest) + "\n")

    # Configure core.hooksPath (idempotent + safe).
    _configure_hooks_path(project_dir, warnings)

    for w in warnings:
        print(f"[ai-hats] WARNING: {w}")


def _collect_skill_git_hooks(
    result: CompositionResult,
) -> dict[str, list[tuple[str, str]]]:
    """Walk composed skills and collect their declared git hooks.

    Returns: {event_name: [(skill_name, script_path), ...]}
    """
    collected: dict[str, list[tuple[str, str]]] = {}
    for skill in result.skills:
        metadata = SkillMetadata.from_skill_dir(skill.source_path)
        if not metadata.git_hooks:
            continue
        for event, scripts in metadata.git_hooks.items():
            collected.setdefault(event, []).extend((skill.name, script) for script in scripts)
    return collected


def _resolve_skill_script(
    skill_name: str,
    script_path: str,
    result: CompositionResult,
) -> Path | None:
    """Resolve a script path declared in a skill's metadata to an absolute path."""
    return _resolve_runtime_script(result, skill_name, script_path)


def _install_dispatcher(dispatcher_path: Path) -> bool:
    """Write the dispatcher script. Returns True if installed/updated, False on conflict."""
    if dispatcher_path.exists():
        try:
            existing = dispatcher_path.read_text()
        except OSError:
            return False
        if GITHOOKS_DISPATCHER_MARKER not in existing:
            return False  # Foreign file, leave it alone.
    if not GITHOOKS_DISPATCHER_TEMPLATE.exists():
        # Should never happen with package-data set, but defend against it.
        return False
    shutil.copy2(GITHOOKS_DISPATCHER_TEMPLATE, dispatcher_path)
    dispatcher_path.chmod(0o755)
    return True


def _cleanup_managed_git_hooks(project_dir: Path) -> None:
    """Remove files listed in our manifest. Idempotent."""
    githooks_dir = project_dir / GITHOOKS_DIR
    manifest_path = githooks_dir / GITHOOKS_MANIFEST
    if not manifest_path.exists():
        return
    try:
        entries = manifest_path.read_text().splitlines()
    except OSError:
        return
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        target = githooks_dir / entry
        if target.is_file():
            # For dispatcher files, only remove if the marker is still ours.
            if "/" not in entry:
                try:
                    if GITHOOKS_DISPATCHER_MARKER not in target.read_text():
                        continue
                except OSError:
                    continue
            _safe_discard(
                target,
                reason="githook-dispatcher",
                project_dir=project_dir,
            )
    # Manifest itself is framework bookkeeping — whitelist.
    manifest_path.unlink(missing_ok=True)  # safe-delete: ok framework-manifest
    # Remove empty <event>.d/ subdirs.
    for child in githooks_dir.iterdir():
        if child.is_dir() and child.name.endswith(".d") and not any(child.iterdir()):
            child.rmdir()  # safe-delete: ok empty-dir


def _configure_hooks_path(project_dir: Path, warnings: list[str]) -> None:
    """Set git config core.hooksPath = .githooks if safe to do so."""
    try:
        current = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
            env=scrubbed_git_env(),
        )
    except (OSError, FileNotFoundError):
        warnings.append("git not found — cannot configure core.hooksPath")
        return

    existing = current.stdout.strip() if current.returncode == 0 else ""
    target = GITHOOKS_DIR

    if existing == target:
        return  # Already correct.
    if existing:
        warnings.append(
            f"core.hooksPath is already set to '{existing}' — not "
            f"overwriting. To enable ai-hats hooks, run: "
            f"git config core.hooksPath {target}  (or merge dispatchers manually)"
        )
        return

    try:
        subprocess.run(
            ["git", "config", "core.hooksPath", target],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=True,
            env=scrubbed_git_env(),
        )
    except subprocess.CalledProcessError as e:
        warnings.append(f"failed to set core.hooksPath: {e.stderr.strip() or e}")


def expected_git_hook_files(project_dir: Path, result: CompositionResult) -> dict[str, bytes]:
    """Managed ``.githooks/`` relpath -> expected bytes for ``result``.

    Mirrors :meth:`_install_git_hooks` WITHOUT writing, so drift can be
    detected cheaply. Keys: ``<event>.d/<skill>-<basename>`` per declared
    script, plus ``<event>`` per dispatcher.
    """
    declared = _collect_skill_git_hooks(result)
    expected: dict[str, bytes] = {}
    for event, entries in declared.items():
        has_entry = False
        for skill_name, script_path in entries:
            src = _resolve_skill_script(skill_name, script_path, result)
            if src is None:
                continue
            expected[f"{event}.d/{skill_name}-{src.name}"] = src.read_bytes()
            has_entry = True
        if has_entry and GITHOOKS_DISPATCHER_TEMPLATE.exists():
            expected[event] = GITHOOKS_DISPATCHER_TEMPLATE.read_bytes()
    return expected


def git_hooks_changes(
    project_dir: Path, result: CompositionResult, manifest_set: set[str]
) -> list[tuple[str, str]]:
    """Managed git-hook drift as ``[(relpath, kind), ...]`` (HATS-833).

    Same comparison as :func:`git_hooks_drift` (content + exec-bit + presence +
    manifest), but reports WHICH files drifted and HOW so the session-start net
    can name them. ``kind`` is ``"missing"`` (absent / exec-bit lost / unreadable),
    ``"content"`` (bytes differ), or ``"stale"`` (managed in the manifest but no
    longer expected). A foreign (non-marker) top-level dispatcher is never
    counted as drift (left to the install policy).
    """
    githooks_dir = project_dir / GITHOOKS_DIR
    expected = expected_git_hook_files(project_dir, result)
    changes: list[tuple[str, str]] = []
    # Stale: managed previously (manifest) but no longer expected.
    for rel in sorted(manifest_set - set(expected)):
        changes.append((rel, "stale"))
    for rel, content in sorted(expected.items()):
        target = githooks_dir / rel
        # Top-level dispatcher with a foreign body on disk → install policy
        # leaves it alone, so it is NEVER our drift — even when it's absent from
        # our manifest (a user-owned dispatcher we never installed). Check this
        # BEFORE the manifest/presence test, else a foreign dispatcher would be
        # flagged "missing" every launch and never heal (perpetual false note).
        if "/" not in rel and target.is_file():
            try:
                if GITHOOKS_DISPATCHER_MARKER not in target.read_text():
                    continue
            except OSError:
                pass
        if rel not in manifest_set or not target.is_file():
            changes.append((rel, "missing"))
            continue
        try:
            if target.read_bytes() != content:
                changes.append((rel, "content"))
                continue
        except OSError:
            changes.append((rel, "missing"))
            continue
        if not target.stat().st_mode & 0o100:  # owner-exec bit lost
            changes.append((rel, "missing"))
    return changes


def git_hooks_drift(project_dir: Path, result: CompositionResult, manifest_set: set[str]) -> bool:
    """True if managed git hooks on disk diverge from ``result``.

    Thin wrapper over :func:`git_hooks_changes` (HATS-833) — preserves the
    original boolean contract for callers that only need yes/no.
    """
    return bool(git_hooks_changes(project_dir, result, manifest_set))
