"""Managed-hook materialization + drift-sync (HATS-837 extract from Assembler).

Owns the cohesive hook cluster the :class:`~ai_hats.assembler.Assembler` used to
carry inline: runtime-hook scripts (``library/hooks/``) and their
``.claude/settings.json`` wiring, worktree-hook scripts (``library/wt-hooks/``),
and skill-declared git hooks (``.githooks/``) — plus the HATS-833 per-surface
drift detectors and the session-start :meth:`HooksManager.sync_hooks` orchestrator.

Narrow DI (HATS-837): the manager holds ``project_dir`` (every materializer writes
under it), a *live* ``project_config`` reference (``sync_hooks`` reads
``active_role`` / ``default_role`` / ``provider``; the owning ``Assembler`` mutates
the same object on ``set_role``), and a ``compose`` callable — the single
back-coupling into the assembler's composition layer, injected so this module
never imports ``Assembler`` at load time (the ``AssemblyError`` raise stays a
local import).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .composer import (
    CompositionResult,
    collect_runtime_hooks as _collect_runtime_hooks,
    collect_worktree_hooks as _collect_worktree_hooks,
    resolve_skill_script as _resolve_runtime_script,
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


@dataclass(frozen=True)
class HookChange:
    """One managed-hook surface change detected/healed by :meth:`HooksManager.sync_hooks`.

    surface: ``"runtime"`` | ``"wt"`` | ``"git"``.
    name:    the hook's display name (script name for runtime/wt, git event for git).
    kind:    ``"missing"`` (absent → materialized), ``"content"`` (bytes drifted →
             updated), ``"wiring"`` (settings.json managed entry (re)written), or
             ``"stale"`` (no longer composed → swept).
    """

    surface: str
    name: str
    kind: str


@dataclass(frozen=True)
class HookSyncResult:
    """Outcome of :meth:`HooksManager.sync_hooks` (HATS-593, generalized HATS-833).

    status:
        ``"synced"``        — managed hooks were drifted and re-materialized.
        ``"in-sync"``       — already consistent with the composed source; no-op.
        ``"skipped"``       — nothing to do (not a git repo / no active role).
        ``"version-skew"``  — the installed ``ai-hats`` binary is strictly
            behind upstream master (failure-mode #5). Materializing from a
            stale binary could write hooks that don't match the merged source,
            so we refuse and recommend ``ai-hats self update`` instead of
            healing blind. ``changes`` still lists the detected drift so the
            caller can name what was left unhealed.

    changes: per-surface/per-hook list of what drifted (and, on ``synced``, was
        healed). Empty on ``in-sync`` / ``skipped``. Drives the session-start
        heal note (HATS-833 req-5).
    """

    status: str
    detail: str = ""
    changes: tuple[HookChange, ...] = ()


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

    @staticmethod
    def _read_manifest(path: Path) -> set[str]:
        """Read a manifest file into a set of managed names.

        Duplicated from :meth:`Assembler._read_canonical_manifest` (HATS-837):
        8 lines, kept local rather than lifted into a shared util until a third
        consumer appears.
        """
        if not path.exists():
            return set()
        out: set[str] = set()
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.add(line)
        return out

    # ----- Skill-contributed git hooks (HATS-088) -----

    @staticmethod
    def _resolve_skill_script(
        skill_name: str,
        script_path: str,
        result: CompositionResult,
    ) -> Path | None:
        """Resolve a script path declared in a skill's metadata to an absolute path."""
        return _resolve_runtime_script(result, skill_name, script_path)

    def materialize_runtime_hooks(self, result: "CompositionResult | None" = None) -> None:
        """Materialize runtime-hook scripts to ``<ai_hats_dir>/library/hooks/``.

        Two sources, one managed namespace + manifest:

        * **Package-data guards** (HATS-467 / HATS-437): the hard-coded
          ``library/hooks/*.sh`` set restores the shared-state-guard safety
          net. ``.claude/settings.json``'s PreToolUse entry (written by
          :meth:`ClaudeProvider.ensure_runtime_hooks`) expects a real script
          at ``<ai_hats_dir>/library/hooks/<name>.sh``. Post-HATS-294
          framework content is composed in memory and not materialized on
          disk — but PreToolUse hooks are the exception: Claude Code's hook
          channel calls ``/bin/sh <path>`` and so the file must exist.
        * **Skill-declared runtime hooks** (HATS-597): each script a composed
          skill declares under ``runtime_hooks:`` is materialized to the
          collision-free :func:`managed_runtime_hook_filename` path — the same
          path the provider writes as the settings.json ``command``. ``result``
          is ``None`` on the legacy bare-bump path (no active role); then only
          the package-data guards are materialized.

        Idempotent. Uses :func:`safe_delete.replace` (bytes-compare
        no-op, ``mode=0o755`` set atomically) for writes and
        :func:`safe_delete.discard` for sweep of files no longer managed.
        Manifest at ``<target>/.manifest`` tracks managed names across runs
        — skill scripts ride the same manifest, so a skill leaving the
        composition sweeps its script on the next pass.

        Fails loudly (``AssemblyError``) if the package data hook root
        cannot be resolved — a broken install is not a state we'd want
        to silently paper over with an empty hooks dir.
        """
        from .assembler import AssemblyError

        source_root_path = _builtin_library_hooks()
        if source_root_path is None:
            raise AssemblyError("ai_hats.library.hooks not found in package data — broken install")

        target_dir = _lib_hooks_dir(self.project_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = target_dir / ".manifest"

        previous: set[str] = set()
        if manifest_path.exists():
            previous = {
                line.strip()
                for line in manifest_path.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            }

        new_names: set[str] = set()
        for src in sorted(source_root_path.iterdir()):
            if not src.is_file() or src.suffix != ".sh":
                continue
            new_names.add(src.name)
            _safe_replace(
                target_dir / src.name,
                src.read_bytes(),
                reason="materialize-pretooluse",
                project_dir=self.project_dir,
                mode=0o755,
            )

        # Skill-declared runtime-hook scripts (HATS-597). Same managed dir +
        # manifest as the package guards, so the sweep below removes a skill's
        # script once the skill leaves the composition.
        if result is not None:
            for _event, entries in self._collect_skill_runtime_hooks(result).items():
                for skill_name, hook in entries:
                    src = self._resolve_skill_script(skill_name, hook.script, result)
                    if src is None:
                        continue
                    dest_name = _managed_runtime_hook_filename(skill_name, hook.script)
                    new_names.add(dest_name)
                    _safe_replace(
                        target_dir / dest_name,
                        src.read_bytes(),
                        reason="materialize-runtime-hook",
                        project_dir=self.project_dir,
                        mode=0o755,
                    )

        # Sweep entries managed previously but no longer in source.
        for stale in previous - new_names:
            _safe_discard(
                target_dir / stale,
                reason="materialize-pretooluse-sweep",
                project_dir=self.project_dir,
            )

        body = "# ai-hats managed — do not edit\n" + "\n".join(sorted(new_names)) + "\n"
        _safe_replace(
            manifest_path,
            body.encode(),
            reason="materialize-pretooluse-manifest",
            project_dir=self.project_dir,
        )

    def install_git_hooks(self, result: CompositionResult) -> None:
        """Install git hooks declared by composed skills (delegates to githooks)."""
        from .githooks import install_git_hooks

        install_git_hooks(self.project_dir, result)

    def _collect_skill_runtime_hooks(
        self,
        result: CompositionResult,
    ) -> dict[str, list[tuple[str, RuntimeHook]]]:
        """Walk composed skills and collect their declared runtime hooks (HATS-597).

        Thin delegate to :func:`composer.collect_runtime_hooks` — the shared
        derivation the provider also consumes (see that module's note on why it
        lives in composer, not here).
        """
        return _collect_runtime_hooks(result)

    def _collect_skill_worktree_hooks(self, result: CompositionResult):
        """Collect skill-declared worktree hooks by kind (HATS-823).

        Thin delegate to :func:`composer.collect_worktree_hooks`.
        """
        return _collect_worktree_hooks(result)

    def materialize_worktree_hooks(self, result: "CompositionResult | None" = None) -> None:
        """Materialize skill-declared wt_in/wt_out scripts to
        ``<ai_hats_dir>/library/wt-hooks/`` (HATS-823).

        Mirrors :meth:`_materialize_pretooluse_hooks` (managed dir + manifest +
        sweep), minus the package-data guards. No-op (no dir) when nothing is or
        was declared, so projects without worktree hooks stay clean.
        """
        target_dir = _wt_hooks_dir(self.project_dir)
        manifest_path = target_dir / ".manifest"

        previous: set[str] = set()
        if manifest_path.exists():
            previous = {
                line.strip()
                for line in manifest_path.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            }

        collected = self._collect_skill_worktree_hooks(result) if result is not None else {}
        new_names: set[str] = set()
        pending: list[tuple[str, Path]] = []
        for entries in collected.values():
            for skill_name, hook in entries:
                src = self._resolve_skill_script(skill_name, hook.script, result)
                if src is None:
                    continue
                dest_name = _managed_wt_hook_filename(skill_name, hook.script)
                new_names.add(dest_name)
                pending.append((dest_name, src))

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
        for stale in previous - new_names:
            _safe_discard(
                target_dir / stale,
                reason="materialize-worktree-hook-sweep",
                project_dir=self.project_dir,
            )
        body = "# ai-hats managed — do not edit\n" + "\n".join(sorted(new_names)) + "\n"
        _safe_replace(
            manifest_path,
            body.encode(),
            reason="materialize-worktree-hook-manifest",
            project_dir=self.project_dir,
        )

    # ----- HATS-593: drift-detecting hook re-materialization -----

    def sync_hooks(self, result: CompositionResult | None = None) -> HookSyncResult:
        """Re-materialize ANY drifted managed-hook surface (HATS-593 → HATS-833).

        Generalizes the HATS-593 git-only drift net to ALL three managed-hook
        surfaces that ``_refresh`` materializes but a plain interactive launch
        skips (the ``needs_assembly`` gate): runtime-hook scripts
        (``library/hooks/``) and their ``.claude/settings.json`` wiring,
        worktree-hook scripts (``library/wt-hooks/``), and git hooks
        (``.githooks/``). Drift-gated + idempotent: a clean no-op when every
        surface is already in sync. Runs NO migrations / scaffold / prompt
        recomposition — only the four materializers.

        Fail-open by contract (the caller wraps it). The SOLE trigger is the
        session-start path (:meth:`WrapRunner._resync_managed_hooks`): there is no
        standalone ``ai-hats self sync-hooks`` command and no post-merge /
        post-checkout git-event trigger (HATS-833 Q2 — healing consolidated to
        session start). ``result`` may be supplied to reuse the composition the
        session already built; composed here when ``None``.
        """
        cfg = self.project_config
        effective_role = cfg.active_role or cfg.default_role
        if not effective_role:
            return HookSyncResult(status="skipped", detail="no active role")
        if result is None:
            result = self.compose(effective_role)
        provider = get_provider(cfg.provider)

        changes: list[HookChange] = []
        changes.extend(self._runtime_hooks_changes(result, provider))
        changes.extend(self._wt_hooks_changes(result))
        if (self.project_dir / ".git").exists():
            changes.extend(self._git_hooks_changes(result))

        if not changes:
            return HookSyncResult(status="in-sync")

        # Failure-mode #5: refuse to heal from a stale binary. If the installed
        # ai-hats is strictly behind upstream master (reuse of the update-banner
        # drift signal), the composed source the hooks would be derived from may
        # not match what the merged repo expects — recommend ``self update``
        # rather than materialize blind. Carry ``changes`` so the caller can NAME
        # the drift left unhealed (HATS-833 req-7: never silently skip).
        if self._binary_behind_source():
            return HookSyncResult(
                status="version-skew",
                detail="installed ai-hats is behind upstream — run 'ai-hats self update'",
                changes=tuple(changes),
            )

        # Heal only the surfaces that drifted, dispatched by surface name (each
        # materializer is idempotent; ensure_runtime_hooks / _safe_replace no-op
        # when already correct). An unknown surface warns rather than silently
        # skipping a real drift.
        def _heal_runtime() -> None:
            provider.ensure_runtime_hooks(self.project_dir, result)
            self.materialize_runtime_hooks(result)

        healers = {
            "runtime": _heal_runtime,
            "wt": lambda: self.materialize_worktree_hooks(result),
            "git": lambda: self.install_git_hooks(result),
        }
        for surface in {c.surface for c in changes}:
            healer = healers.get(surface)
            if healer is None:
                logger.warning("sync_hooks: no healer for surface %r — left undrifted", surface)
                continue
            healer()
        return HookSyncResult(status="synced", changes=tuple(changes))

    def _runtime_hooks_changes(
        self, result: "CompositionResult | None", provider
    ) -> list[HookChange]:
        """Runtime-hook drift (HATS-833): script bytes (``library/hooks/``) +
        settings.json wiring. Bytes and wiring operate on DIFFERENT sets — the
        ``shared_state_classifier.sh`` helper is materialized but not wired — so
        the two detectors are kept separate (review pt-1/pt-2).
        """
        return [
            HookChange(surface="runtime", name=name, kind=kind)
            for name, kind in (
                *self._runtime_bytes_changes(result),
                *provider.runtime_wiring_changes(self.project_dir, result),
            )
        ]

    def _runtime_bytes_changes(self, result: "CompositionResult | None") -> list[tuple[str, str]]:
        """Drift of materialized ``library/hooks/`` bytes vs the composed source.

        Mirrors :meth:`_materialize_pretooluse_hooks`'s DUAL source so the
        detector cannot false-report in-sync: package-data guards (every ``*.sh``
        under ``ai_hats.library/hooks`` — incl. the ``shared_state_classifier.sh``
        helper) PLUS each composed skill's resolved ``runtime_hooks`` script.
        """
        expected: dict[str, bytes] = {}
        try:
            src_root = _builtin_library_hooks()  # worktree-aware builtin resolver (HATS-831)
            if src_root is not None and src_root.is_dir():
                for src in src_root.iterdir():
                    if src.is_file() and src.suffix == ".sh":
                        expected[src.name] = src.read_bytes()
        except OSError:
            return []  # broken install — let _refresh's loud path own it
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
            HookChange(surface="wt", name=name, kind=kind)
            for name, kind in self._bytes_surface_changes(_wt_hooks_dir(self.project_dir), expected)
        ]

    @staticmethod
    def _bytes_surface_changes(
        target_dir: Path, expected: dict[str, bytes]
    ) -> list[tuple[str, str]]:
        """Diff a managed bytes-surface dir against ``{name: bytes}`` using its
        ``.manifest`` as the record of managed names (mirrors the materializers).
        Returns ``[(name, kind)]`` with kind ``missing`` / ``content`` / ``stale``.
        """
        manifest_path = target_dir / ".manifest"
        managed: set[str] = set()
        if manifest_path.exists():
            managed = {
                line.strip()
                for line in manifest_path.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            }
        out: list[tuple[str, str]] = []
        for name in sorted(managed - set(expected)):
            out.append((name, "stale"))
        for name in sorted(expected):
            p = target_dir / name
            if name not in managed or not p.is_file():
                out.append((name, "missing"))
                continue
            try:
                if p.read_bytes() != expected[name]:
                    out.append((name, "content"))
            except OSError:
                out.append((name, "missing"))
        return out

    def _git_hooks_changes(self, result: CompositionResult) -> list[HookChange]:
        """Git-hook drift as :class:`HookChange` list, deduped to the git EVENT
        for a one-glance note (two scripts in one ``pre-push.d`` collapse to one
        ``pre-push`` line per kind).
        """
        from .githooks import GITHOOKS_DIR, GITHOOKS_MANIFEST, git_hooks_changes

        manifest = self._read_manifest(self.project_dir / GITHOOKS_DIR / GITHOOKS_MANIFEST)
        seen: set[tuple[str, str]] = set()
        out: list[HookChange] = []
        for rel, kind in git_hooks_changes(self.project_dir, result, manifest):
            event = rel.split(".d/", 1)[0] if ".d/" in rel else rel
            key = (event, kind)
            if key in seen:
                continue
            seen.add(key)
            out.append(HookChange(surface="git", name=event, kind=kind))
        return out

    def _binary_behind_source(self) -> bool:
        """True if the installed ai-hats binary is strictly behind upstream.

        Reuses the existing update-check cache (the same signal that drives the
        update banner): ``has_update`` is True only when the installed SHA is
        strictly *behind* the cached upstream master and not ahead/diverged.
        Best-effort — a missing/corrupt cache or any read error means "unknown",
        which we treat as "not behind" so a healthy heal is never blocked by a
        cold cache (cases #1/#6: bootstrap stays fail-open).
        """
        try:
            from .update_check import read_cache

            entry = read_cache(self.project_dir)
        except Exception:  # noqa: BLE001 — version-skew detection is best-effort
            return False
        return bool(entry is not None and entry.has_update)

    def _git_hooks_drift(self, result: CompositionResult) -> bool:
        """True if managed git hooks on disk diverge from ``result`` (via githooks)."""
        from .githooks import GITHOOKS_DIR, GITHOOKS_MANIFEST, git_hooks_drift

        manifest = self._read_manifest(self.project_dir / GITHOOKS_DIR / GITHOOKS_MANIFEST)
        return git_hooks_drift(self.project_dir, result, manifest)
