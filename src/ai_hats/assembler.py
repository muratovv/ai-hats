"""Assembly engine — yaml-only role mutations + per-session compose.

HATS-407 trimmed the legacy backup / clean / copy_components / verify
side-effects: with HATS-294's per-session compose, framework content
never lands in the canonical tree, so the heavy assembly cycle became
dead-work. ``set_role`` is now a session-bootstrap helper for runtime;
the CLI surface uses :meth:`Assembler.set_default_role` which mutates
only ``ai-hats.yaml``. ``Assembler.rollback`` and its backup helpers
were removed — git owns user recovery.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

import yaml

from ai_hats_core import CompositionResult, atomic_write_bytes
from .composer import Composer
from .hooks_manager import HooksManager
from .materialize import compose_for_role
from .resolver import LibraryResolver
from .models import (
    ComponentType,
    HarnessConfig,
    OverlayConfig,
    ProjectConfig,
    UserConfig,
)
from .paths import (
    AI_HATS_MANAGED_MARKER,
    builtin_library_hooks as _builtin_library_hooks,
    builtin_library_layers as _builtin_library_layers,
    claude_md,
    claude_skills_dir,
    gemini_md,
    hooks_dir as _lib_hooks_dir,
    rules_dir as _lib_rules_dir,
    skills_dir as _lib_skills_dir,
    user_home,
)
from .paths.constants import LIBRARIES_DIRNAME, PROJECT_CONFIG
from .placeholders import expand_path_placeholders
from .plugin_dir import drop_legacy_claude_publish, drop_legacy_skills_mirror
from ai_hats_core.safe_delete import discard as _safe_discard
from ai_hats_core.safe_delete import replace as _safe_replace
from .providers import (
    INJECTION_END,
    INJECTION_START,
    PUBLISH_AGGREGATOR_END,
    provider_names,
    PUBLISH_AGGREGATOR_START,
    Provider,
    get_provider,
)
from .constants import (
    AGENT_DIR,
    CANONICAL_DIR,
    CANONICAL_MANIFEST,
    GITIGNORE_FILE,
    USER_RULES_SUBDIR,
    PROVIDER_AGY,
)

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .relocation import RelocationResult

logger = logging.getLogger(__name__)


def _ai_hats_owned_hook_basenames() -> frozenset[str]:
    """Return basenames of hooks shipped inside the ai-hats package.

    Sourced from :func:`paths.builtin_library_hooks` (the worktree-aware
    builtin ``library/hooks/`` resolver) so the whitelist tracks package
    contents automatically when new managed hooks are added. Re-walked on
    every call (functools
    not used to keep import-time imports minimal — re-walking the
    package data is cheap on every call).

    Used by:

    - :meth:`Assembler._migrate_layout_v4_hooks_partition` — entries
      in this set move to ``library/hooks/``; everything else goes
      to ``user-hooks/``.
    - :mod:`ai_hats.migration_healer` — Stage A1 disable-vs-rewrite
      decision: user-owned hooks (basename NOT in whitelist) have
      their settings.json entry removed (HATS-549 Phase 4); managed
      hooks pass through to the normal rewrite path.

    On a broken install (package data missing) returns an empty set —
    callers degrade gracefully, treating every legacy hook as
    user-owned (worst case: a managed file ends up under user-hooks/,
    which the next clean install can re-materialize under library/hooks/).
    """
    try:
        hooks = _builtin_library_hooks()
        if hooks is None:
            return frozenset()
        return frozenset(entry.name for entry in hooks.iterdir() if entry.is_file())
    except OSError:
        return frozenset()


class Assembler:
    """Manages the lifecycle of role assembly in a project directory."""

    def __init__(
        self,
        project_dir: Path,
        library_paths: list[Path] | None = None,
        hooks: "HooksManager | None" = None,
    ) -> None:
        self.project_dir = project_dir
        self.agent_dir = project_dir / AGENT_DIR
        self.config_path = project_dir / PROJECT_CONFIG
        self.project_config = ProjectConfig.from_yaml(self.config_path)
        # Read-path net for a hand-edited ai-hats.yaml: the schema no longer
        # validates ``provider`` (HATS-863 severed schema→providers).
        if self.project_config.provider:
            self._validate_provider(self.project_config.provider)

        # HATS-421: user-level customizations layer. Loaded lazily-eagerly here
        # so the global overlay applies to every composer invocation through
        # this assembler. Missing file → empty (silent default); malformed
        # raises UserConfigError up-front, before any composition runs.
        self.user_config = UserConfig.from_yaml(UserConfig.default_path())

        # Build library paths: built-in + project config + explicit
        self.library_paths = self._build_library_paths(library_paths or [])
        self.resolver = LibraryResolver(self.library_paths)
        self.composer = Composer(self.resolver)

        # HATS-837: managed-hook materialize + drift-sync. Injectable for tests.
        self.hooks = hooks if hooks is not None else HooksManager(
            self.project_dir,
            self.project_config,
            compose=lambda role: compose_for_role(self, role),
            resolve_provider=get_provider,  # HATS-865: DI so the brick never imports providers
            library_paths=self.library_paths,  # HATS-1023: consumer lifecycle union scope
        )

    def _build_library_paths(self, extra: list[Path]) -> list[Path]:
        """Build ordered library paths (earlier = lower priority).

        Built-in shipping: `core` (engine fundament) and `usage` (curated
        content), resolved from the `ai_hats_library` package. Override points
        (user-global, project-config, project-local) layer on top via last-wins.
        """
        from ai_hats.library_schema import check_library_schema
        from ai_hats.paths import builtin_library_root

        paths: list[Path] = []

        # Built-in: core + usage. Fail loud FIRST if the pinned library declares a
        # format-schema newer than this ai-hats understands (T18; built-in only).
        check_library_schema(builtin_library_root())
        for layer in _builtin_library_layers():
            paths.append(layer)

        # HATS-871 / ADR-0016: out-of-tree packages contribute their skills/ via
        # the ``ai_hats.skills`` entry-point (open registry). Shipped tier — ranks
        # above the builtins, below the user/config/project overrides that follow.
        from ai_hats.skill_sources import skill_source_roots

        paths.extend(skill_source_roots())

        # Global user libraries — ``user_home()`` honours
        # ``AI_HATS_USER_HOME`` (HATS-532) for e2e isolation.
        global_lib = user_home() / ".ai-hats"
        if global_lib.is_dir():
            paths.append(global_lib)

        # Config-specified paths
        for p in self.project_config.library_paths:
            expanded = Path(p).expanduser()
            if expanded.is_dir():
                paths.append(expanded)

        # Project-local libraries — re-pointed to the worktree when composing
        # inside one (HATS-831); see :meth:`_worktree_local_libraries`.
        local_lib = self._worktree_local_libraries() or self.project_dir / LIBRARIES_DIRNAME
        if local_lib.is_dir():
            paths.append(local_lib)

        # Explicit extra paths (highest priority)
        paths.extend(extra)

        return paths

    def _worktree_local_libraries(self) -> Path | None:
        """Project-local ``libraries/`` re-pointed to the linked worktree, or ``None``.

        Inside a linked worktree ``_project_dir`` hopped to MAIN (HATS-524), so the
        git-tracked ``libraries/`` would resolve to MAIN — invisible to worktree
        edits. Re-point only when cwd is in a worktree whose main checkout IS
        ``project_dir``. The ``is_relative_to`` pre-gate skips the git probe on the
        common main-checkout path (and under subprocess-mocking tests).
        """
        cwd = Path.cwd()
        try:
            if cwd.resolve().is_relative_to(self.project_dir.resolve()):
                return None
        except (OSError, ValueError):
            return None

        from ai_hats_wt import WorktreeManager

        main_root = WorktreeManager.main_worktree_root(cwd)
        if main_root is None or main_root.resolve() != self.project_dir.resolve():
            return None
        wt_top = WorktreeManager.worktree_toplevel(cwd)
        return (wt_top / LIBRARIES_DIRNAME) if wt_top is not None else None

    # ----- Scaffold-as-asset (HATS-284) -----

    def _resolve_scaffold_template(self, relpath: str) -> Path | None:
        """Find a scaffold template asset across `library_paths` (last-wins)."""
        result: Path | None = None
        for lib in self.library_paths:
            candidate = lib / relpath
            if candidate.is_file():
                result = candidate
        return result

    def _ensure_scaffold(self, provider: Provider) -> None:
        """Write the provider's prompt-file scaffold from a library template.

        - No-op if the provider declares no scaffold (e.g. Agy).
        - No-op if the prompt file already exists (user owns).
        - Soft no-op if the template asset is missing from library paths.
        """
        rel = provider.scaffold_template_relpath()
        if rel is None:
            return
        prompt_path = provider.system_prompt_path(self.project_dir)
        if prompt_path.exists():
            return
        template = self._resolve_scaffold_template(rel)
        if template is None:
            return
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        # exists-check above means this is always a fresh write — replace()
        # routes through atomic write without taking a snapshot (no-op for
        # missing files).
        _safe_replace(
            prompt_path,
            template.read_bytes(),
            reason="scaffold",
            project_dir=self.project_dir,
        )

    def _migrate_claude_md_to_v3(self, provider: Provider) -> None:
        """Bring `./CLAUDE.md` to the current v3 scaffold layout (HATS-285/289).

        Three idempotent fix-ups, applied in order:

        1. Strip the legacy uppercase AI-HATS block (HATS-285) and replace
           it with the lowercase scaffold from the library template.
        2. If the scaffold's import line still points at the deprecated
           `.claude/CLAUDE.md` aggregator (HATS-283), rewrite it to the
           canonical `imports.md` (HATS-289).
        3. Delete legacy publish artefacts under `.claude/` (HATS-289)
           that the canonical aggregator now replaces — keeps `skills/`
           since that one is auto-discovered by Claude Code.

        Provider must declare a scaffold template (Agy is a no-op via
        that contract).
        """
        rel = provider.scaffold_template_relpath()
        if rel is None:
            return
        prompt_path = provider.system_prompt_path(self.project_dir)
        if not prompt_path.exists():
            self._ensure_scaffold(provider)
            self._cleanup_legacy_claude_publish()
            return

        existing = prompt_path.read_text()
        template = self._resolve_scaffold_template(rel)
        scaffold_body = template.read_text() if template is not None else None

        new_content = existing
        # 1. Already-on-v3-scaffold OR legacy-uppercase-block path.
        if PUBLISH_AGGREGATOR_START in new_content and PUBLISH_AGGREGATOR_END in new_content:
            pass  # scaffold already present
        elif scaffold_body is None:
            # Cannot compose without a template. Skip silently.
            return
        elif INJECTION_START in new_content and INJECTION_END in new_content:
            before = new_content[: new_content.index(INJECTION_START)].rstrip("\n")
            after = new_content[new_content.index(INJECTION_END) + len(INJECTION_END) :]
            after = after.lstrip("\n")
            parts = []
            if before:
                parts.append(before)
            parts.append(scaffold_body.rstrip("\n"))
            if after:
                parts.append(after.rstrip("\n"))
            new_content = "\n\n".join(parts) + "\n"
        else:
            # No markers at all — user-owned file. Prepend scaffold so the
            # canonical aggregator gets imported; preserve user content below.
            new_content = scaffold_body.rstrip("\n") + "\n\n" + new_content.lstrip("\n")

        # 2. Rewrite deprecated import line to the canonical aggregator.
        new_content = new_content.replace(
            "@./.claude/CLAUDE.md",
            "@./.agent/ai-hats/imports.md",
        )

        if new_content != existing:
            # HATS-470: snapshot the pre-migrate content so users can
            # recover if the v3 scaffold rewrite mangles their file
            # (especially the no-markers branch above which prepends
            # blindly).
            _safe_replace(
                prompt_path,
                new_content.encode("utf-8"),
                reason="claude-md-migrate",
                project_dir=self.project_dir,
            )

        # 3. Drop legacy `.claude/` publish artefacts.
        self._cleanup_legacy_claude_publish()

    def _cleanup_legacy_claude_publish(self) -> None:
        """Thin seam over the shared legacy sweeps (HATS-905): the generic
        unclaimed-marker sweeper runs the same procedures at bump; this path
        keeps them firing on every refresh as before."""
        drop_legacy_skills_mirror(self.project_dir)
        drop_legacy_claude_publish(self.project_dir)

    @staticmethod
    def _cleanup_obsolete_files(project_dir: Path) -> list[str]:
        """Delete files retired by previous releases (HATS-285 — moved from CLI).

        Each entry: (relative path, human reason). Idempotent — missing
        files are skipped silently.

        HATS-407: sweeps stale ``.last_backup`` pointer files (and the
        ``/tmp/ai-hats-backup-*`` dirs they reference) left behind by the
        retired ``Assembler._backup()`` chain. Both the v3 legacy
        location (``.agent/.last_backup``) and the v4 location
        (``<ai_hats_dir>/.last_backup``) are swept so upgrades from any
        prior version land in a clean state.
        """
        obsolete = [
            (".agent/backlog.md", "removed legacy backlog.md (unified into STATE.md)"),
        ]
        actions: list[str] = []
        for rel, reason in obsolete:
            target = project_dir / rel
            if not target.exists():
                continue
            _safe_discard(
                target,
                reason="obsolete-file",
                project_dir=project_dir,
            )
            actions.append(reason)

        # HATS-407: sweep stale .last_backup pointer + referenced /tmp dir.
        # Local import avoids a top-level cycle with paths.py at module load.
        from .paths import last_backup_path as _last_backup_path

        for backup_ref in (
            project_dir / ".agent" / ".last_backup",  # pre-v4 location
            _last_backup_path(project_dir),  # v4 location
        ):
            if not backup_ref.exists():
                continue
            # Pointer-file form: text content names a /tmp backup dir
            # created by the retired _backup() helper, which used
            # ``tempfile.mkdtemp(prefix="ai-hats-backup-")``. Defensively
            # restrict cleanup to absolute paths whose basename carries
            # that prefix — a corrupt or hand-edited pointer cannot
            # redirect cleanup at the user's project tree. HATS-470:
            # the /tmp target lands in safe_delete's well-known-prefix
            # shortcut (_is_under_tmp), so it's hard-deleted directly
            # rather than copied into trash session.
            if backup_ref.is_file():
                try:
                    tmp_target = Path(backup_ref.read_text().strip())
                    if (
                        tmp_target.is_absolute()
                        and tmp_target.exists()
                        and tmp_target.name.startswith("ai-hats-backup-")
                    ):
                        try:
                            _safe_discard(
                                tmp_target,
                                reason="obsolete-backup-tmp",
                                project_dir=project_dir,
                            )
                        except OSError:
                            pass
                except (OSError, ValueError):
                    pass
                _safe_discard(
                    backup_ref,
                    reason="obsolete-backup-pointer",
                    project_dir=project_dir,
                )
            elif backup_ref.is_dir():
                try:
                    _safe_discard(
                        backup_ref,
                        reason="obsolete-backup-dir",
                        project_dir=project_dir,
                    )
                except OSError:
                    pass
            actions.append(f"swept stale {backup_ref.relative_to(project_dir)} (HATS-407)")
        return actions

    def init(
        self,
        role: str | None = None,
        provider: str | None = None,
        task_prefix: str | None = None,
        ai_hats_dir: str | None = None,
        venv_path: str | None = None,
        manage_gitignore: bool | None = None,
        channel: str | None = None,
        harness_path: str | None = None,
    ) -> None:
        """Initialize project structure. Idempotent.

        Validates `role`, `provider`, `task_prefix`, and the workspace
        path overrides before touching disk — unknown values raise
        ValueError with a helpful message, and no files/dirs are created.
        Re-running init with a `task_prefix` that conflicts with the
        value already in `ai-hats.yaml` is rejected.

        Bootstrap-time-only knobs (HATS-347 wizard):
        - ``ai_hats_dir`` — relocate the framework directory before any
          dirs are created. Default is ``.agent/ai-hats``.
        - ``venv_path`` — point ai-hats at a user-owned venv instead of
          the managed default ``<ai_hats_dir>/.venv``. Written to yaml;
          the bash launcher reads it on next invocation.
        - ``manage_gitignore`` — opt out of the one-shot `.gitignore`
          entry by passing ``False``.
        """
        # Validate inputs BEFORE creating any filesystem artifacts.
        if provider is not None:
            self._validate_provider(provider)
        if role is not None:
            self._validate_role(role)
        if task_prefix is not None:
            task_prefix = ProjectConfig.validate_task_prefix(task_prefix)
            existing = self.project_config.task_prefix
            if self.config_path.exists() and existing and existing != task_prefix:
                raise ValueError(
                    f"task_prefix conflict: ai-hats.yaml has {existing!r}, "
                    f"init called with {task_prefix!r}. Edit the yaml manually "
                    f"if you really want to change it."
                )
        # Pydantic v2 field validators don't fire on direct attribute
        # assignment (no validate_assignment in _YamlModel) — normalize
        # path overrides eagerly so values written to yaml are canonical.
        from .paths import normalize_ai_hats_dir, normalize_venv_path

        if ai_hats_dir is not None:
            ai_hats_dir = normalize_ai_hats_dir(ai_hats_dir)
        if venv_path is not None:
            venv_path = normalize_venv_path(venv_path)

        # Collect path overrides as a delta; save_config applies them to a
        # fresh on-disk read AND refreshes the in-memory config (HATS-526).
        early_delta: dict[str, Any] = {}
        if ai_hats_dir is not None:
            existing_dir = self.project_config.ai_hats_dir
            if self.config_path.exists() and existing_dir != ai_hats_dir:
                raise ValueError(
                    f"ai_hats_dir conflict: ai-hats.yaml has {existing_dir!r}, "
                    f"init called with {ai_hats_dir!r}. Relocating an existing "
                    "framework directory is not automated — move the directory "
                    "manually and edit the yaml."
                )
            early_delta["ai_hats_dir"] = ai_hats_dir
        if venv_path is not None:
            early_delta["venv_path"] = venv_path
        if manage_gitignore is not None:
            early_delta["manage_gitignore"] = manage_gitignore

        # Persist path overrides NOW so subsequent path resolution
        # (runs_dir / tasks_dir) reads the new ai_hats_dir from yaml.
        if early_delta:
            # Provider must be set before the very first save (yaml is
            # rejected without it). Pick the requested value, fall back
            # to whatever's already on the config, finally agy.
            if not self.config_path.exists() and not self.project_config.provider:
                early_delta["provider"] = provider or PROVIDER_AGY
            self.save_config(**early_delta)

        # HATS-312 / HATS-313 / HATS-314: all framework roots live under
        # <ai_hats_dir>/. .agent/ itself is no longer populated by ai-hats.
        from .paths import runs_dir, tasks_dir

        runs_dir(self.project_dir).mkdir(parents=True, exist_ok=True)
        tasks_dir(self.project_dir).mkdir(parents=True, exist_ok=True)
        for subdir_fn in (_lib_rules_dir, _lib_skills_dir, _lib_hooks_dir):
            subdir_fn(self.project_dir).mkdir(parents=True, exist_ok=True)

        # HATS-469 R2: capture greenfield state BEFORE the ai-hats.yaml
        # save block below consumes ``config_path.exists()`` as a signal.
        # Used downstream for:
        #   - migration_step=latest seeding (greenfield only)
        #   - greenfield seed-invariant assertion before _refresh
        #   - skipping _run_v07_migration on greenfield (nothing to heal)
        greenfield = not self.config_path.exists()

        # Create/update ai-hats.yaml (delta-write, HATS-526)
        delta: dict[str, Any] = {}
        if greenfield:
            delta["provider"] = provider or PROVIDER_AGY
            if role:
                delta["default_role"] = role
            # HATS-471: greenfield projects start at the latest migration
            # step. No registry entry needs to run — the directory is fresh.
            from .migrations import latest_step

            delta["migration_step"] = latest_step()
        elif provider:
            delta["provider"] = provider
        if task_prefix is not None:
            delta["task_prefix"] = task_prefix
        if delta:
            self.save_config(**delta)

        # HATS-938: seed harness.channel:local when the host ai-hats is editable,
        # so a fresh project heals editable-from-local, not the remote release.
        # Auto-detect is greenfield-only; an explicit --channel always applies.
        harness_seed = self._resolve_init_harness(channel, harness_path, greenfield)
        if harness_seed is not None:
            self.save_config(harness=harness_seed)

        # Create STATE.md
        from .paths import state_md_path

        state_md = state_md_path(self.project_dir)
        state_md.parent.mkdir(parents=True, exist_ok=True)
        if not state_md.exists():
            state_md.write_text("# Task State\n\nNo active tasks.\n")

        active_provider = get_provider(self.project_config.provider)

        # HATS-469 R1: direct heal of ./CLAUDE.md on re-init.
        # ``_migrate_claude_md_to_v3`` is also a registry entry (step 5)
        # but registry is gated by ``migration_step``; a re-init on a
        # fully-migrated project (migration_step=latest) would skip it.
        # The direct call preserves the legacy contract "init re-run
        # always normalises CLAUDE.md" — covers the edge case where the
        # user manually re-introduces a legacy uppercase block.
        # Idempotent; on greenfield ./CLAUDE.md does not exist yet so
        # this is a no-op.
        self._migrate_claude_md_to_v3(active_provider)

        # HATS-469 R2: greenfield invariant — migration_step MUST be
        # seeded to latest BEFORE _refresh. Otherwise run_pending would
        # replay every migration on empty files
        # (``_strip_legacy_managed_block`` on a non-existent ``.gitignore``,
        # ``_migrate_layout_v4`` on absent directories). Cheap guard
        # against future refactors that might reorder the seed.
        # Explicit ``raise`` (not ``assert``) so the invariant holds
        # under ``python -O`` — assertions get stripped by the optimiser
        # and we'd lose this safety net silently in production.
        if greenfield:
            from .migrations import latest_step

            if self.project_config.migration_step != latest_step():
                raise RuntimeError(
                    "HATS-469 R2: greenfield init must seed migration_step "
                    "to latest BEFORE _refresh; otherwise registry replays "
                    "on empty files. Current value: "
                    f"{self.project_config.migration_step!r}, "
                    f"expected: {latest_step()!r}."
                )

        # HATS-469 R6: re-init on a v0.6 layout must heal BEFORE _refresh
        # fires the registry (run_pending step 6 = ``_migrate_layout_v4``
        # would otherwise run against a still-v0.6 tree). Defaults
        # (force=False, check_branches=False) match the previous auto-bump
        # behaviour from cli/assembly.py:245-259 (now removed by R6).
        # User-edits trip ``AssemblyError`` with per-file guidance; user
        # re-runs with ``ai-hats self bump --migrate-force`` to override.
        # Greenfield: skip — nothing to migrate.
        if not greenfield:
            self._run_v07_migration(force=False, check_branches=False)

        # HATS-317: one-shot .gitignore entry. Idempotent; no managed block.
        if self.project_config.manage_gitignore:
            self._ensure_gitignore_entry()

        # Apply role to yaml first so _refresh sees the new default_role
        # (and the post-init session reads the right value). Compose result
        # is passed through to _refresh which installs role git hooks.
        #
        # HATS-469 R8 (audit): on re-init WITHOUT CLI ``-r`` we still
        # compose for the effective role (``active_role`` falls back to
        # ``default_role``). The pre-HATS-469 auto-bump path did this
        # implicitly via ``asm.bump()`` (which read
        # ``active_role or default_role``); without it, re-init on an
        # existing project with a saved role would silently SKIP
        # role-git-hooks re-installation — regression vs prior behaviour
        # whenever a user wipes ``.githooks/`` and re-runs ``self init``.
        if role:
            self.save_config(default_role=role)
        cfg = self.project_config
        effective_role = role or cfg.active_role or cfg.default_role
        result: CompositionResult | None = (
            compose_for_role(self, effective_role) if effective_role else None
        )

        # HATS-469: single entry-point for all heal/install work.
        # install_time=True → registry fires (gated by migration_step;
        # greenfield no-ops via the R2 seed above).
        self._refresh(install_time=True, result=result)

    def _resolve_init_harness(
        self, channel: str | None, harness_path: str | None, greenfield: bool
    ) -> "HarnessConfig | None":
        """Harness to seed at init, or None to leave the config default (STABLE).

        Precedence: explicit ``--channel`` (always) → editable-source auto-detect
        (greenfield only). Re-init never clobbers an existing harness on auto.
        """
        from .models import Channel

        if channel is not None:
            ch = Channel(channel)
            path = harness_path
            if ch is Channel.LOCAL and path is None:
                path = self._detect_editable_src()
            return HarnessConfig(channel=ch, path=path)
        if not greenfield:
            return None
        src = self._detect_editable_src()
        return HarnessConfig(channel=Channel.LOCAL, path=src) if src else None

    def _detect_editable_src(self) -> str | None:
        """Absolute path of the editable ai-hats source, or None if not editable.

        Launcher-exported ``AI_HATS_INIT_SRC`` wins (robust to venv-bootstrap
        ordering); else the running interpreter's PEP 610 ``file://`` editable url.
        """
        import os

        from .constants import ENV_AI_HATS_INIT_SRC

        env_src = (os.environ.get(ENV_AI_HATS_INIT_SRC) or "").strip()
        if env_src:
            return env_src
        from .cli.maintenance import _is_editable_install

        editable, url = _is_editable_install()
        if editable and url and url.startswith("file://"):
            return url.removeprefix("file://")
        return None

    def _get_overlay(self, role_name: str) -> OverlayConfig | None:
        """Get the **project** overlay for a role, or ``None`` if absent/empty.

        Kept for code that still wants only the project layer (mostly
        introspection in CLI ``--show --project``). For composition use
        :meth:`_get_overlays`, which returns the layered list including
        the user-level customizations.
        """
        overlay = self.project_config.customizations.get(role_name)
        if overlay is None or overlay.is_empty:
            return None
        return overlay

    def _get_global_overlay(self, role_name: str) -> OverlayConfig | None:
        """Get the user-level overlay for a role, or ``None`` if absent/empty.

        Reads from ``~/.ai-hats/customizations.yaml`` (loaded at assembler
        construction time as ``self.user_config``).
        """
        return self.user_config.overlay_for(role_name)

    def _get_overlays(self, role_name: str) -> list[OverlayConfig]:
        """Return the ordered list of overlay layers for a role (HATS-421).

        Order: ``[global, project]`` — global applied first, project
        applied last. ``compose`` runs ``_apply_overlay`` per layer in this
        order so project-level edits win on conflict, and each layer's own
        ``remove`` + ``add`` is honoured for in-layer reorder.

        Empty layers are omitted from the returned list so the composer
        never wastes work applying a no-op overlay.
        """
        layers: list[OverlayConfig] = []
        gl = self._get_global_overlay(role_name)
        if gl is not None:
            layers.append(gl)
        pr = self._get_overlay(role_name)
        if pr is not None:
            layers.append(pr)
        return layers

    def _get_overlay_provenance(self, role_name: str) -> dict[str, dict[str, str]]:
        """Return a ``{component_type: {name: layer}}`` provenance map for a role.

        ``component_type`` ∈ ``{"traits", "rules", "skills"}``. ``layer`` ∈
        ``{"built-in", "global", "project"}``. Used by ``config status`` to
        annotate the dependency tree with a source-tag per node.

        Walked in the same global-then-project order used by ``_get_overlays``
        so that a name added by global and re-added by project surfaces as
        ``project`` (last-wins), matching the composer's final state.
        """
        provenance: dict[str, dict[str, str]] = {"traits": {}, "rules": {}, "skills": {}}
        # Seed from the resolved role's base composition.
        base_cfg = self.resolver.resolve_role_config(role_name)
        if base_cfg is not None:
            for name in base_cfg.composition.traits:
                provenance["traits"][name] = "built-in"
            for name in base_cfg.composition.rules:
                provenance["rules"][name] = "built-in"
            for name in base_cfg.composition.skills:
                provenance["skills"][name] = "built-in"
        # Apply layers in order: each `add` claims provenance, each `remove`
        # drops the entry so a later layer's add can re-claim it.
        for layer, label in (
            (self._get_global_overlay(role_name), "global"),
            (self._get_overlay(role_name), "project"),
        ):
            if layer is None:
                continue
            for name in layer.remove_traits:
                provenance["traits"].pop(name, None)
            for name in layer.remove_rules:
                provenance["rules"].pop(name, None)
            for name in layer.remove_skills:
                provenance["skills"].pop(name, None)
            for name in layer.add_traits:
                provenance["traits"][name] = label
            for name in layer.add_rules:
                provenance["rules"][name] = label
            for name in layer.add_skills:
                provenance["skills"][name] = label
        return provenance

    def set_default_role(
        self, role_name: str, provider_name: str | None = None
    ) -> CompositionResult:
        """CLI-surface: set the project's ``default_role`` in ``ai-hats.yaml``.

        HATS-407: ``ai-hats config set -r X`` reduces to a yaml field write.
        Validation is a dry-run compose (so unknown roles / missing components
        surface before the yaml is touched) but NO canonical materialization,
        backup, copy, or hook-install side-effects happen here.

        ``active_role`` is intentionally untouched — it is a runtime cache
        managed by :meth:`set_role` on the session-bootstrap path.

        Idempotent: when both target values already match the current yaml,
        no write occurs.

        Returns the CompositionResult so the caller (CLI) can display
        rule/skill counts to the user.
        """
        self._validate_role(role_name)
        if provider_name is not None:
            self._validate_provider(provider_name)

        # Dry-run compose to surface unknown components before yaml write.
        result = compose_for_role(self, role_name)

        cfg = self.project_config
        new_provider = provider_name or cfg.provider
        if cfg.default_role == role_name and cfg.provider == new_provider:
            return result  # idempotent no-op

        self.save_config(default_role=role_name, provider=new_provider)
        return result

    def set_role(
        self,
        role_name: str,
        provider_name: str | None = None,
        *,
        warnings_sink: list[str] | None = None,
    ) -> CompositionResult:
        """Runtime-bootstrap: sync ``active_role`` + materialize per-session deps.

        Called by :class:`Runtime` on the first session of a fresh project (or
        on provider switch) to bring on-disk state into a usable shape: the
        canonical user-rules aggregator and skill-contributed git hooks
        (HATS-088). NOT invoked by the CLI surface — use
        :meth:`set_default_role` for that.

        HATS-469: delegated to :meth:`_refresh` (install_time=False — runtime
        bootstrap does not re-run migrations; init/bump already did). The
        Agy inline-prompt path and ``active_role``/``provider`` persist
        stay here as set_role-only concerns.

        HATS-407: backup/clean/copy_components/verify side-effects were
        removed; per-session compose (HATS-294) means framework content is
        never materialized into the canonical tree. The Agy inline-prompt
        path is retained as a known asymmetry (no scaffold-template
        equivalent for bare-agy in project_dir).

        Fails loudly on unknown role/provider: the project must not end up
        in a half-applied state where active_role is saved but composition
        is broken.
        """
        # Validate before doing any work so we fail fast with a clear message.
        self._validate_role(role_name)
        if provider_name is not None:
            self._validate_provider(provider_name)

        provider = get_provider(provider_name or self.project_config.provider)
        # HATS-456: single derivation point — used for hooks install
        # AND build_system_prompt for Agy scaffold-less branch (below).
        result = compose_for_role(self, role_name)

        # Non-fatal compose errors (e.g. missing optional rule) are surfaced
        # via result.errors; do not abort.

        # HATS-285: bring ./CLAUDE.md to the v3 scaffold layout if a
        # legacy uppercase AI-HATS block or v2 inline injection is
        # present. Also a registry entry (step 5), but the runner is
        # gated by ``migration_step``; the direct call here is the
        # sole-direct-caller contract documented in migrations.py:38-43
        # — bootstrap path the registry cannot cover (first session may
        # predate any bump). Idempotent.
        self._migrate_claude_md_to_v3(provider)

        # HATS-469: single entry-point. install_time=False → skip registry
        # (migrations replay only via init/do_bump). _refresh handles
        # _ensure_scaffold, write_canonical, ensure_runtime_hooks, and the
        # HooksManager materializers (runtime / worktree / git). Diagnostics
        # are NOT called from here — runtime auto-trigger stays silent
        # (HATS-469 R3).
        self._refresh(install_time=False, result=result, warnings_sink=warnings_sink)

        # Provider inline system prompt — Agy-only path.
        # Claude declares a scaffold template (HATS-284); ./CLAUDE.md is
        # owned by the scaffold + canonical aggregator. Agy has no
        # scaffold mechanism, so bare-agy in project_dir relies on
        # ./AGY.md inline-block injection. Documented asymmetry with
        # Claude Fork B (HATS-294); separate cleanup task tracks
        # symmetric drop later.
        if provider.scaffold_template_relpath() is None:
            prompt_content = provider.build_system_prompt(result)
            prompt_content = expand_path_placeholders(prompt_content, self.project_dir)
            provider.update_system_prompt(self.project_dir, prompt_content)

        # Persist active_role + provider.
        self.save_config(active_role=role_name, provider=provider.name)

        return result

    def status(self) -> dict:
        """Get current status: role, dependency tree, health.

        HATS-407: surfaces both ``default_role`` (user-intent persisted by
        the CLI) and ``active_role`` (runtime cache written on session
        start). The composite ``role`` field resolves the effective role
        the next session would use, mirroring runtime resolution order.
        """
        cfg = self.project_config
        effective_role = cfg.active_role or cfg.default_role
        status = {
            "role": effective_role,
            "default_role": cfg.default_role,
            "active_role": cfg.active_role,
            "provider": cfg.provider,
            "project_dir": str(self.project_dir),
            "library_paths": [str(p) for p in self.library_paths],
            "health": {},
            "tree": None,
        }

        if effective_role:
            result = compose_for_role(self, effective_role)
            status["tree"] = self._build_tree(result)
            status["health"] = self._check_health(result)
            status["errors"] = result.errors

        return status

    def _refresh(
        self,
        *,
        install_time: bool,
        result: CompositionResult | None,
        warnings_sink: list[str] | None = None,
    ) -> None:
        """Single idempotent entry-point for on-disk state pull-up (HATS-469).

        One method, called from every public entry-point that brings the project
        tree to a consistent shape.

        Parameters:
            install_time: ``True`` for ``init`` / ``do_bump`` — runs the
                migration registry (``migrations.run_pending``). ``False`` for
                ``set_role`` (runtime bootstrap) — migrations already replayed
                via init or a prior bump.
            result: composition for the active role, or ``None`` (legacy
                bare-bump / init without ``-r``). When provided AND ``.git/``
                exists, role-specific git hooks (HATS-088) are installed.

        Concurrency: per the migrations.py migration contract, under N parallel
        ``init`` / ``set_role`` / ``bump`` processes every method invoked here
        MUST be idempotent. The registry guarantees at-most-once across
        sequential invocations; concurrent ones may replay one step per process.

        Diagnostics (``_warn_orphan_*`` / ``_note_empty_*``) are NOT part of
        refresh — they live in :meth:`_run_diagnostics`, firing only on
        user-initiated paths (HATS-469 R3: per-session orphan spam = bad UX).
        """
        # 1. Migration registry — install_time only (HATS-471).
        if install_time:
            from .migrations import run_pending

            run_pending(self)

        # 1b. Unclaimed-marker sweep — install_time only (HATS-905): dead
        # mechanisms' leftovers reclaimed on init/bump, never on set_role.
        if install_time:
            self._sweep_unclaimed_markers()

        # 2. Heal — always.
        provider = get_provider(self.project_config.provider)
        self._ensure_scaffold(provider)
        self.write_canonical()

        # 3. Managed hooks — provider settings.json wiring + the three surfaces,
        # delegated to the HooksManager facade (HATS-837). The .git guard lives
        # inside materialize(), so non-git project dirs skip git hooks silently.
        # All idempotent and REQUIRED on set_role first-session bootstrap.
        self.hooks.materialize(result, warnings_sink=warnings_sink)

    def _sweep_unclaimed_markers(self) -> None:
        """Thin seam onto :func:`sweeper.run_unclaimed_sweep` (HATS-905)."""
        from . import sweeper

        sweeper.run_unclaimed_sweep(
            self.project_dir, binary_behind=self.hooks.binary_behind_source()
        )

    def _run_diagnostics(self) -> None:
        """Surface state-condition diagnostics on user-initiated paths only.

        Conditional-print methods — emit only when the underlying state
        is true (orphan exists / ``.agent/`` is bare). Called from:

        - ``do_bump`` (cli) after ``_refresh`` — user explicitly asked
          for maintenance; expects a report.
        - ``cli/assembly.py self_init`` after ``_refresh`` IFF re-init
          existing project — user explicitly re-ran, wants the state.
        - ``cli/maintenance.py`` self-update path — same as do_bump.

        NOT called from:

        - :meth:`set_role` — runtime auto-trigger; must be silent
          (HATS-469 R3: orphan-warning every session = bad UX).
        - Greenfield ``init`` — nothing to diagnose on a fresh project.
        """
        self._warn_orphan_user_level_managed_skills()
        if self.project_config.provider:
            provider = get_provider(self.project_config.provider)
            self._warn_leaked_user_global_project_hooks(provider)
        self._note_empty_legacy_agent_dir()
        self._warn_leftover_hook_sidecars()

    def _note_empty_legacy_agent_dir(self) -> None:
        """HATS-317: print a NOTE if `.agent/` only holds the managed `ai-hats/`.

        After the v4 layout migration, every legacy artefact has moved under
        `<ai_hats_dir>/`. The wrapper directory `.agent/` (which holds the
        canonical layered context at `.agent/ai-hats/`) may otherwise be
        empty for users running the default layout. The note tells the user
        the legacy top-level dirs are gone and they can delete anything left
        over manually — we never rm it automatically.
        """
        if not self.agent_dir.is_dir():
            return
        entries = {p.name for p in self.agent_dir.iterdir()}
        # Only emit when `.agent/` is a pure ai-hats wrapper (i.e. nothing
        # else lives there). In the default layout `ai-hats/` itself sits
        # inside; with a custom ai_hats_dir outside .agent/, this branch
        # detects a fully empty `.agent/`.
        if entries and entries != {"ai-hats"}:
            return
        # Print on stderr so it doesn't pollute scripted JSON output. The
        # message is informational — never an error.

        print(
            "[Warning] ⚠️  .agent/ holds only the managed ai-hats/ namespace; "
            "legacy top-level artefacts (rules/, skills/, hooks/, backlog/, "
            "STATE.md, ...) have migrated to <ai_hats_dir>/. If nothing "
            "else of yours lives in .agent/, the wrapper is no longer "
            "required — ai-hats will not remove it automatically.",
            file=sys.stderr,
        )

    def _migrate_layout_v4(self) -> None:
        """v4-layout migration step (logic in migrations.py, HATS-715)."""
        from . import migrations

        migrations.migrate_layout_v4(self)

    def _migrate_layout_v4_library(self) -> None:
        """v4-layout migration step (logic in migrations.py, HATS-715)."""
        from . import migrations

        migrations.migrate_layout_v4_library(self)

    def _migrate_layout_v4_hooks_partition(self) -> None:
        """v4-layout migration step (logic in migrations.py, HATS-715)."""
        from . import migrations

        migrations.migrate_layout_v4_hooks_partition(self)

    def _safe_discard_with_warn(self, path: Path, *, reason: str) -> None:
        """Wrap :func:`_safe_discard` with a stderr WARN on failure.

        HATS-549 review S.4: on a read-only filesystem (some CI gates)
        ``_safe_discard`` fails silently, leaving the caller's flow
        in partial-state limbo. The WARN ensures the user sees the
        problem instead of triaging mysterious downstream errors.
        """
        try:
            _safe_discard(
                path,
                reason=reason,
                project_dir=self.project_dir,
            )
        except OSError as e:
            try:
                rel = path.relative_to(self.project_dir).as_posix()
            except ValueError:
                rel = str(path)
            print(
                f"[ai-hats] WARN: {reason}: could not safe-discard {rel}: {e}",
                file=sys.stderr,
            )

    @staticmethod
    def _ai_hats_owned_hook_basenames() -> frozenset[str]:
        """Set of hook basenames the framework itself ships.

        Sourced from :func:`paths.builtin_library_hooks` — same surface as
        :meth:`HooksManager.materialize_runtime_hooks`. Anything not in this set
        is treated as user-owned content by the v4 hooks-partition step
        (HATS-549 Phase 4).

        Exposed as a public-ish static so :mod:`ai_hats.migration_healer`
        can read the same whitelist when deciding whether to auto-disable
        vs. heal a settings.json hook entry.
        """
        return _ai_hats_owned_hook_basenames()

    def _migrate_layout_v4_tracker(self) -> None:
        """v4-layout migration step (logic in migrations.py, HATS-715)."""
        from . import migrations

        migrations.migrate_layout_v4_tracker(self)

    def _migrate_layout_v4_sessions(self) -> None:
        """v4-layout migration step (logic in migrations.py, HATS-715)."""
        from . import migrations

        migrations.migrate_layout_v4_sessions(self)

    def _idempotent_move(self, old_abs: Path, new_abs: Path) -> None:
        """Move `old_abs` to `new_abs`, merging into existing dirs when needed.

        - new_abs missing → simple shutil.move (parent created).
        - new_abs is a dir → copy items missing on the new side, then drop old.
        - new_abs is a file → assume already migrated; remove the stale source.

        HATS-470: collision-side drops (old beats new) route through
        safe_delete so the loser side is recoverable from trash.
        Converted from ``@staticmethod`` because trash recording wants
        ``self.project_dir`` for relpath preservation.
        """
        if not old_abs.exists():
            return
        new_abs.parent.mkdir(parents=True, exist_ok=True)
        if not new_abs.exists():
            shutil.move(str(old_abs), str(new_abs))
            return
        if old_abs.is_dir() and new_abs.is_dir():
            for entry in old_abs.iterdir():
                target = new_abs / entry.name
                if target.exists():
                    continue
                shutil.move(str(entry), str(target))
            # New side wins on collisions: drop the rest of old to keep
            # the migration deterministic. Best-effort — concurrent
            # cleanup or read-only entry is benign.
            try:
                _safe_discard(
                    old_abs,
                    reason="move-collision",
                    project_dir=self.project_dir,
                )
            except OSError:
                pass
            return
        # File-vs-file or type mismatch: trust new, drop old.
        try:
            _safe_discard(
                old_abs,
                reason="move-collision",
                project_dir=self.project_dir,
            )
        except OSError:
            pass

    # -- Internal methods --

    def _validate_role(self, role_name: str) -> None:
        """Raise ValueError if `role_name` is not resolvable in library paths."""
        if self.resolver.resolve_role_config(role_name) is not None:
            return
        available = self.resolver.list_components(ComponentType.ROLE)
        hint = ", ".join(available) if available else "(none found in library paths)"
        raise ValueError(f"Role '{role_name}' not found. Available roles: {hint}")

    @staticmethod
    def _validate_provider(provider_name: str) -> None:
        """Raise ValueError if `provider_name` is not a registered provider."""
        if provider_name in provider_names():
            return
        raise ValueError(
            f"Unknown provider: {provider_name}. Available: {sorted(provider_names())}"
        )

    def _build_tree(self, result: CompositionResult) -> dict:
        """Build a dependency tree representation.

        HATS-421: includes a ``provenance`` map ``{traits|rules|skills:
        {name: "built-in"|"global"|"project"}}`` so ``config status`` can
        annotate each node with which layer contributed it. The traits
        list is also surfaced here (it doesn't otherwise appear in the
        tree — composer flattens traits into rules/skills/injections).
        """
        provenance = self._get_overlay_provenance(result.name)
        # Effective trait order: base composition + overlay-added (overlay
        # removes are already applied by the composer for the composition
        # lists, but trait-level visibility is what `config status` cares
        # about). Walk layers in same order as provenance: base → global → project.
        base_cfg = self.resolver.resolve_role_config(result.name)
        effective_traits: list[str] = list(base_cfg.composition.traits) if base_cfg else []
        for layer in (
            self._get_global_overlay(result.name),
            self._get_overlay(result.name),
        ):
            if layer is None:
                continue
            for name in layer.remove_traits:
                if name in effective_traits:
                    effective_traits.remove(name)
            for name in layer.add_traits:
                if name not in effective_traits:
                    effective_traits.append(name)
        return {
            "name": result.name,
            "priorities": result.priorities,
            "traits": effective_traits,
            "rules": [r.name for r in result.rules],
            "skills": [s.name for s in result.skills],
            "injections_count": len(result.injections),
            "provenance": provenance,
        }

    def _check_health(self, result: CompositionResult) -> dict[str, str]:
        """Check health of disk-resident artefacts post-HATS-407.

        With per-session compose (HATS-294) and yaml-only set_role
        (HATS-407), framework rules/skills/hooks are not materialized into
        the canonical tree — composition resolves them via the library
        layers in memory. Only artefacts that DO live on disk are
        verifiable here:

        - ``<ai_hats_dir>/imports.md`` — canonical user-rules aggregator.
        - Provider system prompt (``./CLAUDE.md`` / ``./AGY.md``).
        """
        del result  # composition is checked in-memory via composer.compose
        health: dict[str, str] = {}
        imports_md = self._canonical_dir / "imports.md"
        health["imports.md"] = "OK" if imports_md.exists() else "Missing"
        prompt_ok = any(f(self.project_dir).exists() for f in (gemini_md, claude_md))
        health["system_prompt"] = "OK" if prompt_ok else "Missing"
        return health

    # ----- Canonical layered layer (HATS-282) -----

    @property
    def _canonical_dir(self) -> Path:
        return self.agent_dir / CANONICAL_DIR

    def write_canonical(self) -> None:
        """Write the canonical aggregator under .agent/ai-hats/ (HATS-294/407).

        Emits only ``imports.md`` — a list of ``@./user-rules/*.md`` imports.
        All framework content (priorities / role / traits / rules / skills_index)
        is composed in memory per-session by ``Provider.build_session_prompt``
        and never materialized on disk.

        Stale framework files from prior v0.6 layouts are swept by the
        manifest-driven cleanup below; ``user-rules/`` is never touched.

        Idempotent: per-file bytes-compare avoids spurious mtime updates.

        HATS-407: the ``result`` parameter was dropped — composition no longer
        feeds the canonical writer, and callers must not pretend to influence
        the emitted aggregator via the result.
        """
        canonical = self._canonical_dir
        canonical.mkdir(parents=True, exist_ok=True)
        (canonical / USER_RULES_SUBDIR).mkdir(exist_ok=True)

        aggregator = self._render_canonical_aggregator(canonical).encode()
        # HATS-380: expand `<ai_hats_dir>` placeholder before write so the
        # agent never sees the literal token (it would otherwise create a
        # bogus `./<ai_hats_dir>/` directory in the project root).
        aggregator = expand_path_placeholders(aggregator.decode(), self.project_dir).encode()
        self._atomic_write_if_changed(canonical / "imports.md", aggregator)

        # Stale cleanup: remove previous-managed files no longer present.
        # On v0.6→v0.7 upgrade this sweeps priorities.md, role.md, traits/*,
        # rules/*, skills_index.md. ``user-rules/`` is always preserved.
        # HATS-408 layers user-edit detection on top of this cleanup before
        # release; HATS-294 alone is not user-shippable.
        new_paths = {"imports.md"}
        previous = self._read_canonical_manifest(canonical / CANONICAL_MANIFEST)
        for stale in previous - new_paths:
            if stale.startswith(f"{USER_RULES_SUBDIR}/") or stale == USER_RULES_SUBDIR:
                continue
            target = canonical / stale
            _safe_discard(
                target,
                reason="canonical-stale",
                project_dir=self.project_dir,
            )
            # Best-effort cleanup of empty parent dirs (stop at canonical root)
            parent = target.parent
            while parent != canonical and parent.is_dir():
                try:
                    parent.rmdir()  # safe-delete: ok empty-dir
                except OSError:
                    break
                parent = parent.parent

        self._write_canonical_manifest(canonical / CANONICAL_MANIFEST, sorted(new_paths))

    @staticmethod
    def _render_canonical_aggregator(canonical_dir: Path) -> str:
        """Build ``imports.md`` — a sorted list of ``@./user-rules/*.md`` imports.

        HATS-294: framework content (priorities / role / traits / rules /
        skills_index) is composed in memory per session and never written to
        disk; the aggregator therefore lists only user-rules. ``./CLAUDE.md``
        still imports this single file as its stable entry-point.
        """
        user_rules_dir = canonical_dir / USER_RULES_SUBDIR
        if not user_rules_dir.is_dir():
            return ""
        paths = sorted(f"@./{USER_RULES_SUBDIR}/{md.name}" for md in user_rules_dir.glob("*.md"))
        if not paths:
            return ""
        return "\n".join(paths) + "\n"

    @staticmethod
    def _atomic_write_if_changed(path: Path, content: bytes) -> bool:
        """Write `content` to `path` only if it would change. Returns True on write."""
        if path.exists() and path.read_bytes() == content:
            return False
        atomic_write_bytes(path, content)  # HATS-716: canonical atomic write
        return True

    def _run_v07_migration(self, *, force: bool, check_branches: bool) -> None:
        """HATS-415 inline v0.6 → v0.7 migration. Called from ``bump()``.

        Replaces the naive HATS-408 ``_refuse_on_v06_layout`` gate with a
        real diff-against-baseline classifier from
        :mod:`ai_hats.migration_v07`.

        Behaviour:

        * **Safe-to-delete findings** (bytes match composition baseline) are
          swept inline — no commit, no user prompt. ``self update`` heals
          v0.6 layouts transparently for the common case.
        * **User-edit findings** raise ``AssemblyError`` with per-file
          guidance pointing at the v0.7 home (``user-rules/`` or
          ``library/usage/...``). Same surface as the old ``migrate-v07``
          refusal — relocated, not re-engineered.
        * **--migrate-force** (``force=True``) bypasses the refusal,
          overwrites with one stderr ``WARN`` line per file.
        * **--check-branches** (``check_branches=True``) runs a local-branch
          scanner and emits stderr ``WARN`` lines if any branch modifies a
          path we're about to delete. Best-effort: never blocks.

        No-op on already-migrated projects (``has_work=False``) — idempotent.
        """
        from .migration_v07 import (
            check_branches_modify_paths,
            empty_composition,
            execute_deletions,
            plan_migration,
            render_user_edits_refusal,
        )

        cfg = self.project_config
        effective_role = cfg.active_role or cfg.default_role
        if effective_role:
            try:
                composition = compose_for_role(self, effective_role)
                source_lookup = self._build_v07_tier2_source_lookup(composition)
            except Exception:  # noqa: BLE001 — defensive fallback for compose failure
                composition = empty_composition()
                source_lookup = {}
        else:
            composition = empty_composition()
            source_lookup = {}
        hook_source_dirs = self._collect_v07_hook_source_dirs()

        report = plan_migration(
            self._canonical_dir,
            composition,
            source_lookup,
            project_dir=self.project_dir,
            tier2_hook_source_dirs=hook_source_dirs,
        )

        # HATS-415 trigger contract: only run the inline migration when
        # Tier-1 canonical files are present on disk (priorities/role/
        # traits/rules/skills_index). Those are framework-owned files that
        # CANNOT legitimately exist on a v0.7 project — their presence is
        # the strong v0.6 signal.
        #
        # Without this gate, every bump on a v0.7 project that happens to
        # have a user-authored flat hook under ``library/hooks/<x>.sh``
        # (a legitimate v0.7 use case) would refuse — Tier-2 findings
        # alone do not justify a migration. They are only swept when we
        # already know the project is v0.6.
        has_tier1 = any(f.tier == 1 for f in report.findings)
        if not has_tier1:
            return

        if check_branches and report.paths_to_delete:
            warns = check_branches_modify_paths(self.project_dir, report.paths_to_delete)
            if warns:
                print(
                    "WARN: local branches modify v0.7-migration paths slated for deletion:",
                    file=sys.stderr,
                )
                for branch, paths in warns:
                    print(f"  {branch}", file=sys.stderr)
                    for p in paths:
                        print(f"    {p}", file=sys.stderr)
                print(
                    "  Merge / cherry-pick first or those edits will be lost.",
                    file=sys.stderr,
                )

        if report.user_edits and not force:
            raise AssemblyError(render_user_edits_refusal(report.user_edits, self.project_dir))

        if force and report.user_edits:
            for f in report.user_edits:
                try:
                    rel = f.path.relative_to(self.project_dir)
                except ValueError:
                    rel = f.path
                print(
                    f"WARN: v07-migrate: overwriting {rel} (user edit detected)",
                    file=sys.stderr,
                )

        execute_deletions(report, self._canonical_dir, project_dir=self.project_dir)

    def _collect_v07_hook_source_dirs(self) -> list[Path]:
        """Return library hook root dirs from every layer of ``self.library_paths``.

        HATS-408 C1: a v0.6 ``library/hooks/<basename>`` flat-file finding is
        classified as safe-to-delete when its bytes match a source library hook
        of the same basename. Scanning all library layers in order avoids the
        user having to point at a particular one.
        """
        out: list[Path] = []
        for lib in self.library_paths:
            hooks_root = Path(lib) / "hooks"
            if hooks_root.is_dir():
                out.append(hooks_root)
        return out

    def _build_v07_tier2_source_lookup(self, comp: CompositionResult) -> dict[str, Path]:
        """Map mirror-dir name → source root for every rule/skill in ``comp``.

        Used by :func:`ai_hats.migration_v07.plan_migration` so Tier-2 dirs
        whose source we can locate get a diff baseline (safe-to-delete when
        content matches).

        HATS-755: consumes the composition already computed by the sole
        caller (:meth:`_run_v07_migration`) instead of recomposing the same
        role — the two composes ran back-to-back on identical state, so they
        were guaranteed equal. The caller's ``try/except`` maps a compose
        failure to an empty ``composition`` + empty lookup.
        """
        from .models import resolve_namespace

        resolver = self.composer.resolver
        out: dict[str, Path] = {}
        for r in comp.rules:
            src = resolver.resolve_rule_dir(r.name)
            if src is not None:
                # Mirror-dir name on disk uses fs-namespace form (`a::b` → `a/b`),
                # but the directory baseline match uses the mirror's own basename
                # which is the leaf name. Index both for safety.
                out[r.name] = src
                out[resolve_namespace(r.name).rsplit("/", 1)[-1]] = src
        for s in comp.skills:
            src = resolver.resolve_skill_dir(s.name)
            if src is not None:
                out[s.name] = src
                out[resolve_namespace(s.name).rsplit("/", 1)[-1]] = src
        return out

    def _normalize_yaml(self) -> None:
        """Persist HATS-408 in-memory yaml healing to disk.

        ``ProjectConfig.from_yaml`` strips deprecated fields
        (``imports_order``) and heals ``default_role := active_role``
        in memory only — by design, so read-only commands like ``task
        show`` or ``status`` never surprise the user with a yaml
        rewrite. The cost is that the WARN fires on EVERY load until
        something explicitly persists. HATS-413: ``bump()`` (also the
        target of the ``self update`` auto-bump chain) now persists
        the heal once, so the WARN doesn't re-fire forever.

        Drift detection peeks at the raw yaml dict (pre-load shape) and
        compares to the in-memory ``self.project_config``:

        * Deprecated key present in raw → strip is pending.
        * Raw ``default_role`` empty while in-memory non-empty → heal
          was applied and needs persisting.

        Idempotent: no-op on already-normalized yaml.
        """
        if not self.config_path.exists():
            return
        try:
            raw = yaml.safe_load(self.config_path.read_text()) or {}
        except yaml.YAMLError:
            # Broken yaml — the next load surfaces the error loudly; we
            # don't try to "fix" what we can't parse.
            return
        from .models import _DEPRECATED_PROJECT_FIELDS

        has_deprecated = any(k in raw for k in _DEPRECATED_PROJECT_FIELDS)
        raw_default = raw.get("default_role") or ""
        heal_pending = bool(self.project_config.default_role) and not raw_default
        if has_deprecated or heal_pending:
            heal: dict[str, Any] = (
                {"default_role": self.project_config.default_role} if heal_pending else {}
            )
            self.save_config(**heal)

    def save_config(self, **fields: Any) -> None:
        """Locked delta-write of ai-hats.yaml; refreshes the in-memory config (HATS-526).

        Pass ONLY the fields this operation changes — the on-disk state is
        re-read under the lock, so concurrent writers' fields survive.
        """
        from .config.project import locked_update

        def _apply(cfg: ProjectConfig) -> None:
            for name, value in fields.items():
                setattr(cfg, name, value)

        merged = locked_update(self.config_path, _apply)
        # In-place refresh: callers alias project_config (e.g. the migration
        # runner) — replacing the object would strand their mutations.
        for name in type(merged).model_fields:
            setattr(self.project_config, name, getattr(merged, name))
        self.project_config._extra = merged._extra

    def _persist_migration_step(self, step: int) -> None:
        """HATS-471: persist ``ProjectConfig.migration_step`` to disk after
        each successful registry entry.

        Called by ``migrations.run_pending`` between entries so a partial
        failure (entry N raises) leaves yaml at ``migration_step = N-1``;
        the next ``bump`` resumes from entry N.

        The in-memory config has already been mutated by the runner — this
        helper just writes the whole config back through the standard
        ``ProjectConfig.save`` path so other persisted fields stay
        consistent. No-op if ``ai-hats.yaml`` does not exist (pre-init).
        """
        if not self.config_path.exists():
            return
        # Sanity: only persist when the in-memory value already reflects
        # the requested step. Catches a programmer error if the runner
        # contract ever drifts.
        if self.project_config.migration_step != step:  # pragma: no cover
            raise AssertionError(
                f"_persist_migration_step({step}) called while in-memory "
                f"config has migration_step={self.project_config.migration_step}"
            )
        self.save_config(migration_step=step)

    @staticmethod
    def _read_canonical_manifest(path: Path) -> set[str]:
        if not path.exists():
            return set()
        out: set[str] = set()
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.add(line)
        return out

    @staticmethod
    def _write_canonical_manifest(path: Path, names: list[str]) -> None:
        body = "# ai-hats canonical layer manifest. Do not edit.\n"
        body += "\n".join(names) + "\n"
        Assembler._atomic_write_if_changed(path, body.encode())

    # ----- .gitignore management (HATS-317) -----

    def _ensure_gitignore_entry(self) -> None:
        """Ensure <ai_hats_dir>/ is gitignored (logic in relocation.py, HATS-715)."""
        from . import relocation

        relocation.ensure_gitignore_entry(self.project_dir)

    def _strip_legacy_managed_block(self) -> bool:
        """One-shot: remove the pre-HATS-317 `# AI-HATS:START..END` block from `.gitignore`.

        Returns ``True`` when the file was modified.

        HATS-317 replaced the dynamic managed-block generator with a single
        static line (``<ai_hats_dir>/``) written once at ``init`` time, but
        did NOT include a one-shot cleanup for projects that already had the
        block written by the old generator. Result: every project that ran
        ``ai-hats self init`` before HATS-317 still carries 50-90 stale
        per-component lines like ``.agent/ai-hats/library/skills/X/``,
        ``.agent/ai-hats/rules/Y.md``, ``.agent/ai-hats/traits/Z.md``.

        After HATS-294 (v0.7 per-session compose) most of those files no
        longer exist on disk, so the block is doubly stale: the bare
        ``.agent/`` line already covers the subtree, AND the per-file
        entries point at vanished paths.

        Safe whole-block strip: the block was always machine-generated;
        the ``START`` marker explicitly says "managed by ai-hats, do not
        edit". No ``--force`` flag — if a user did edit lines inside, they
        contradicted the explicit contract.

        Called from ``bump()`` so existing users pick up the sweep on the
        next ``ai-hats self bump`` / ``self update`` (same delivery pattern
        as HATS-413 ``_normalize_yaml`` and HATS-415 v0.7 layout sweep).
        Skipped when ``manage_gitignore = False`` — opted-out projects own
        the whole file.
        """
        if not self.project_config.manage_gitignore:
            return False
        gitignore = self.project_dir / GITIGNORE_FILE
        if not gitignore.exists():
            return False

        text = gitignore.read_text()
        start_marker = "# AI-HATS:START"
        end_marker = "# AI-HATS:END"
        has_start = start_marker in text
        has_end = end_marker in text
        if not has_start and not has_end:
            return False  # common case — block was never written
        if has_start and not has_end:
            # Corrupted: START opened but never closed. Refuse to mangle
            # what might be user content past the opener. Silent no-op —
            # the .gitignore won't sweep, but nothing breaks either; user
            # can fix the marker by hand and re-run.
            return False
        if has_end and not has_start:
            return False  # unrelated line that happens to contain the END text

        lines = text.splitlines(keepends=True)
        start_idx: int | None = None
        end_idx: int | None = None
        for i, ln in enumerate(lines):
            if start_marker in ln and start_idx is None:
                start_idx = i
            elif end_marker in ln and start_idx is not None and end_idx is None:
                end_idx = i
                break
        # Defensive: parser should always find both since we verified above.
        if start_idx is None or end_idx is None:
            return False

        # Absorb one preceding blank line (visual separator) so we don't
        # leave a stranded blank line between unrelated user content.
        removal_start = start_idx
        if removal_start > 0 and lines[removal_start - 1].strip() == "":
            removal_start -= 1

        new_text = "".join(lines[:removal_start] + lines[end_idx + 1 :])
        if new_text == text:
            return False
        _safe_replace(
            gitignore,
            new_text.encode("utf-8"),
            reason="gitignore-strip",
            project_dir=self.project_dir,
        )
        return True

    def _warn_orphan_user_level_managed_skills(self) -> bool:
        """HATS-465: WARN when `~/.claude/skills/.ai-hats-managed` exists.

        ai-hats has never written to ``~/.claude/skills/``. Pre-HATS-294
        ``Provider.skills_export_dir`` for Claude pointed at the
        project-level ``<project>/.claude/skills`` mirror; HATS-294
        removed permanent export entirely in favor of the per-session
        plugin-dir under ``<ai_hats_dir>/.cache/sessions/<sid>/plugin/``.
        Yet some user environments carry
        ``~/.claude/skills/.ai-hats-managed`` from a manual
        ``cp -r .claude/skills/ ~/.claude/skills/`` (the marker tagged
        along). That marker claims ai-hats ownership, but no refresh
        path exists — the dir drifts from source-of-truth forever and
        Claude Code's user-level skill auto-discovery serves stale
        content to sub-agents.

        Print one WARN per bump with a safe-remove instruction. We do
        NOT delete the directory ourselves: ai-hats never wrote it, so
        treating it as ours to discard would violate
        ``global_rule_destructive_actions §1``. Idempotent and
        non-self-healing — re-fires on every bump until the user
        removes the directory.

        Returns ``True`` when a WARN was emitted (test seam).
        """
        marker = claude_skills_dir(Path.home()) / AI_HATS_MANAGED_MARKER
        if not marker.exists():
            return False
        print(
            "[Warning] ⚠️  Orphan ai-hats marker detected: "
            "~/.claude/skills/.ai-hats-managed\n"
            "  ai-hats does not manage user-level Claude skills — they "
            "are session-scoped via per-session plugin-dir since v0.7 "
            "(HATS-294).\n"
            "  The marker was likely created by a manual "
            "`cp -r .claude/skills/ ~/.claude/skills/`; the directory "
            "will drift from source-of-truth.\n"
            "  Safe to remove: `rm -rf ~/.claude/skills/`",
            file=sys.stderr,
        )
        return True

    def _warn_leaked_user_global_project_hooks(self, provider: Provider) -> bool:
        """HATS-961: WARN when the active surface leaked ai-hats project hooks into
        user-global config (double-fires + 404s off project-root). Detection is the
        provider's (Claude surface); this only reports. WARN only — never mutate.
        Returns ``True`` when a WARN was emitted (test seam)."""
        leaked = provider.leaked_user_global_project_hooks(Path.home())
        if not leaked:
            return False
        listing = "\n".join(f"    {cmd}" for cmd in leaked)
        print(
            "[Warning] ⚠️  Leaked ai-hats project hooks in user-global "
            "~/.claude/settings.json:\n"
            f"{listing}\n"
            "  ai-hats never writes user-global settings; these copies double-fire "
            "every hook and 404 off project-root.\n"
            "  These project hooks belong only in project `.claude/settings.json` "
            "— remove them from `~/.claude/settings.json`.",
            file=sys.stderr,
        )
        return True

    def _warn_leftover_hook_sidecars(self) -> bool:
        """HATS-815: WARN per skill still shipping a hook-bearing metadata.yaml.

        Proactive companion to the 814 compose-guard
        (:class:`~ai_hats.models.LeftoverSidecarHooksError`): scans every
        resolved library layer (``self.library_paths``) and names each skill
        whose ``metadata.yaml`` still carries ``git_hooks`` / ``runtime_hooks``,
        with the migrate-by-hand remedy. Where the guard hard-fails the FIRST
        such skill it composes mid-session, this lists ALL of them at bump time
        — including library skills the active role does not compose, which the
        guard never reaches.

        Detection only — never rewrites or deletes (supervisor: the user
        migrates by hand). It lives here, in :meth:`_run_diagnostics`, not the
        one-shot migration registry, so it re-fires on every user-initiated
        bump until fixed and stays silent on per-session ``set_role``.

        Returns ``True`` when a WARN was emitted (test seam).
        """
        from .skill_sidecar import (
            leftover_sidecar_remedy,
            scan_leftover_hook_sidecars,
        )

        findings = scan_leftover_hook_sidecars(self.library_paths)
        for finding in findings:
            print(
                f"[ai-hats] WARN: {leftover_sidecar_remedy(finding.name, finding.keys)}",
                file=sys.stderr,
            )
        return bool(findings)

    def relocate(self, new_dir: str) -> "RelocationResult":
        """Move the framework dir to ``new_dir`` (logic in relocation.py, HATS-715)."""
        from . import relocation

        return relocation.relocate(self, new_dir)


class AssemblyError(Exception):
    pass
