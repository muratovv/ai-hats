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

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from .composer import Composer, CompositionResult
from .materialize import compose_for_role
from .resolver import LibraryResolver
from .models import (
    ComponentType,
    OverlayConfig,
    ProjectConfig,
    RuntimeHook,
    SkillMetadata,
    UserConfig,
)
from .paths import (
    hooks_dir as _lib_hooks_dir,
    legacy_paths_by_class,
    managed_runtime_hook_filename as _managed_runtime_hook_filename,
    rules_dir as _lib_rules_dir,
    skills_dir as _lib_skills_dir,
    user_home,
    user_hooks_dir as _user_hooks_dir,
)
from .placeholders import expand_path_placeholders
from .safe_delete import discard as _safe_discard
from .safe_delete import replace as _safe_replace
from .providers import (
    INJECTION_END,
    INJECTION_START,
    PROVIDERS,
    PUBLISH_AGGREGATOR_END,
    PUBLISH_AGGREGATOR_START,
    Provider,
    get_provider,
)


AGENT_DIR = ".agent"
PROJECT_CONFIG = "ai-hats.yaml"
GITHOOKS_DIR = ".githooks"
GITHOOKS_MANIFEST = ".ai-hats-manifest"
GITHOOKS_DISPATCHER_MARKER = "AI-HATS-DISPATCHER-MARKER"
GITHOOKS_DISPATCHER_TEMPLATE = Path(__file__).parent / "templates" / "githooks" / "dispatcher.sh"
GITIGNORE_FILE = ".gitignore"
LIBRARY_RULES_MARKER = ".library_rules"
MANAGED_SKILLS_MARKER = ".ai-hats-managed"

# HATS-282 — canonical layered layer
CANONICAL_DIR = "ai-hats"
CANONICAL_MANIFEST = "MANAGED"
USER_RULES_SUBDIR = "user-rules"


def _ai_hats_owned_hook_basenames() -> frozenset[str]:
    """Return basenames of hooks shipped inside the ai-hats package.

    Sourced from ``importlib.resources.files("ai_hats.library") /
    "hooks"`` so the whitelist tracks package contents automatically
    when new managed hooks are added. The result is cached (functools
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
    from importlib.resources import files

    try:
        source_root = files("ai_hats.library") / "hooks"
        root_path = Path(str(source_root))
        if not root_path.is_dir():
            return frozenset()
        return frozenset(
            entry.name for entry in root_path.iterdir() if entry.is_file()
        )
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        return frozenset()


def _builtin_library_layers() -> list[Path]:
    """Resolve built-in library layers via `importlib.resources`.

    Returns `[core, usage]` in priority order (core first = lowest, usage on
    top). Both layers ship inside the `ai_hats.library` sub-package. Falls
    back to a relative path when the package is not installed (sdist
    inspection in CI).
    """
    from importlib.resources import files

    try:
        root = files("ai_hats.library")
    except (ModuleNotFoundError, FileNotFoundError):
        return []
    out: list[Path] = []
    for layer in ("core", "usage"):
        p = Path(str(root / layer))
        if p.is_dir():
            out.append(p)
    return out


@dataclass(frozen=True)
class HookSyncResult:
    """Outcome of :meth:`Assembler.sync_hooks` (HATS-593).

    status:
        ``"synced"``        — managed git hooks were drifted and re-materialized.
        ``"in-sync"``       — already consistent with the composed source; no-op.
        ``"skipped"``       — nothing to do (not a git repo / no active role).
        ``"version-skew"``  — the installed ``ai-hats`` binary is strictly
            behind upstream master (failure-mode #5). Materializing from a
            stale binary could write hooks that don't match the merged source,
            so we refuse and recommend ``ai-hats self update`` instead of
            healing blind.
    """

    status: str
    detail: str = ""


class Assembler:
    """Manages the lifecycle of role assembly in a project directory."""

    def __init__(self, project_dir: Path, library_paths: list[Path] | None = None) -> None:
        self.project_dir = project_dir
        self.agent_dir = project_dir / AGENT_DIR
        self.config_path = project_dir / PROJECT_CONFIG
        self.project_config = ProjectConfig.from_yaml(self.config_path)

        # HATS-421: user-level customizations layer. Loaded lazily-eagerly here
        # so the global overlay applies to every composer invocation through
        # this assembler. Missing file → empty (silent default); malformed
        # raises UserConfigError up-front, before any composition runs.
        self.user_config = UserConfig.from_yaml(UserConfig.default_path())

        # Build library paths: built-in + project config + explicit
        self.library_paths = self._build_library_paths(library_paths or [])
        self.resolver = LibraryResolver(self.library_paths)
        self.composer = Composer(self.resolver)

    def _build_library_paths(self, extra: list[Path]) -> list[Path]:
        """Build ordered library paths (earlier = lower priority).

        Built-in shipping: `library/core` (engine fundament) and
        `library/usage` (curated content), both packaged under
        `ai_hats.library`. Override points (user-global, project-config,
        project-local) layer on top via last-wins.
        """
        paths: list[Path] = []

        # Built-in: core + usage (shipped with ai-hats package)
        for layer in _builtin_library_layers():
            paths.append(layer)

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

        # Project-local libraries
        local_lib = self.project_dir / "libraries"
        if local_lib.is_dir():
            paths.append(local_lib)

        # Explicit extra paths (highest priority)
        paths.extend(extra)

        return paths

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

        - No-op if the provider declares no scaffold (e.g. Gemini).
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
            prompt_path, template.read_bytes(),
            reason="scaffold", project_dir=self.project_dir,
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

        Provider must declare a scaffold template (Gemini is a no-op via
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
                prompt_path, new_content.encode("utf-8"),
                reason="claude-md-migrate", project_dir=self.project_dir,
            )

        # 3. Drop legacy `.claude/` publish artefacts.
        self._cleanup_legacy_claude_publish()

    def _cleanup_legacy_claude_publish(self) -> None:
        """Remove `.claude/` publish artefacts replaced by the canonical aggregator (HATS-289).

        Idempotent. `.claude/skills/` and any user-authored files are left
        alone — only the previously-managed publish set is targeted via
        the legacy `.claude/.ai-hats-managed` manifest. As a safety net
        we also remove the well-known publish-only files (CLAUDE.md,
        priorities/role/skills_index, traits/, rules/).
        """
        claude_dir = self.project_dir / ".claude"
        if not claude_dir.is_dir():
            return

        manifest = claude_dir / ".ai-hats-managed"
        managed: set[str] = set()
        if manifest.exists():
            for line in manifest.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    managed.add(line)

        # Manifest-listed files (excluding skills/, never managed by publish).
        # HATS-470: routed via safe_delete so the .claude/role.md etc.
        # belt-and-suspenders sweep is recoverable from trash.
        for rel in managed:
            if rel.startswith("skills/"):
                continue
            target = claude_dir / rel
            _safe_discard(
                target, reason="claude-legacy-publish",
                project_dir=self.project_dir,
            )

        # Well-known publish artefacts as belt-and-suspenders.
        for rel in ("CLAUDE.md", "priorities.md", "role.md", "skills_index.md"):
            _safe_discard(
                claude_dir / rel, reason="claude-legacy-publish",
                project_dir=self.project_dir,
            )
        for sub in ("traits", "rules"):
            sub_dir = claude_dir / sub
            if sub_dir.is_dir():
                # Preserve permissive "ignore_errors=True" semantics —
                # original code chose best-effort cleanup for these dirs.
                try:
                    _safe_discard(
                        sub_dir, reason="claude-legacy-publish",
                        project_dir=self.project_dir,
                    )
                except OSError:
                    pass

        _safe_discard(
            manifest, reason="claude-legacy-manifest",
            project_dir=self.project_dir,
        )

        # Best-effort empty-dir cleanup of `.claude/` itself if `skills/`
        # is also absent — but never delete `.claude/skills/` content.
        try:
            if not any(claude_dir.iterdir()):
                claude_dir.rmdir()  # safe-delete: ok empty-dir
        except OSError:
            pass

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
                target, reason="obsolete-file", project_dir=project_dir,
            )
            actions.append(reason)

        # HATS-407: sweep stale .last_backup pointer + referenced /tmp dir.
        # Local import avoids a top-level cycle with paths.py at module load.
        from .paths import last_backup_path as _last_backup_path

        for backup_ref in (
            project_dir / ".agent" / ".last_backup",  # pre-v4 location
            _last_backup_path(project_dir),           # v4 location
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
                                tmp_target, reason="obsolete-backup-tmp",
                                project_dir=project_dir,
                            )
                        except OSError:
                            pass
                except (OSError, ValueError):
                    pass
                _safe_discard(
                    backup_ref, reason="obsolete-backup-pointer",
                    project_dir=project_dir,
                )
            elif backup_ref.is_dir():
                try:
                    _safe_discard(
                        backup_ref, reason="obsolete-backup-dir",
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

        # Apply path overrides to the in-memory config BEFORE resolving any
        # paths, so runs_dir / tasks_dir see the new location.
        save_config_early = False
        if ai_hats_dir is not None:
            existing_dir = self.project_config.ai_hats_dir
            if self.config_path.exists() and existing_dir != ai_hats_dir:
                raise ValueError(
                    f"ai_hats_dir conflict: ai-hats.yaml has {existing_dir!r}, "
                    f"init called with {ai_hats_dir!r}. Relocating an existing "
                    "framework directory is not automated — move the directory "
                    "manually and edit the yaml."
                )
            self.project_config.ai_hats_dir = ai_hats_dir
            save_config_early = True
        if venv_path is not None:
            self.project_config.venv_path = venv_path
            save_config_early = True
        if manage_gitignore is not None:
            self.project_config.manage_gitignore = manage_gitignore
            save_config_early = True

        # Persist path overrides NOW so subsequent path resolution
        # (runs_dir / tasks_dir) reads the new ai_hats_dir from yaml.
        if save_config_early:
            # Provider must be set before the very first save (yaml is
            # rejected without it). Pick the requested value, fall back
            # to whatever's already on the config, finally gemini.
            if not self.config_path.exists() and not self.project_config.provider:
                self.project_config.provider = provider or "gemini"
            self.project_config.save(self.config_path)

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

        # Create/update ai-hats.yaml
        save_config = False
        if greenfield:
            self.project_config.provider = provider or "gemini"
            if role:
                self.project_config.default_role = role
            # HATS-471: greenfield projects start at the latest migration
            # step. No registry entry needs to run — the directory is fresh.
            from .migrations import latest_step

            self.project_config.migration_step = latest_step()
            save_config = True
        elif provider:
            self.project_config.provider = provider
            save_config = True
        if task_prefix is not None:
            self.project_config.task_prefix = task_prefix
            save_config = True
        if save_config:
            self.project_config.save(self.config_path)

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
            self.project_config.default_role = role
            self.project_config.save(self.config_path)
        cfg = self.project_config
        effective_role = role or cfg.active_role or cfg.default_role
        result: CompositionResult | None = (
            compose_for_role(self, effective_role) if effective_role else None
        )

        # HATS-469: single entry-point for all heal/install work.
        # install_time=True → registry fires (gated by migration_step;
        # greenfield no-ops via the R2 seed above).
        self._refresh(install_time=True, result=result)

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

        cfg.default_role = role_name
        cfg.provider = new_provider
        cfg.save(self.config_path)
        return result

    def set_role(self, role_name: str, provider_name: str | None = None) -> CompositionResult:
        """Runtime-bootstrap: sync ``active_role`` + materialize per-session deps.

        Called by :class:`Runtime` on the first session of a fresh project (or
        on provider switch) to bring on-disk state into a usable shape: the
        canonical user-rules aggregator and skill-contributed git hooks
        (HATS-088). NOT invoked by the CLI surface — use
        :meth:`set_default_role` for that.

        HATS-469: delegated to :meth:`_refresh` (install_time=False — runtime
        bootstrap does not re-run migrations; init/bump already did). The
        Gemini inline-prompt path and ``active_role``/``provider`` persist
        stay here as set_role-only concerns.

        HATS-407: backup/clean/copy_components/verify side-effects were
        removed; per-session compose (HATS-294) means framework content is
        never materialized into the canonical tree. The Gemini inline-prompt
        path is retained as a known asymmetry (no scaffold-template
        equivalent for bare-gemini in project_dir).

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
        # AND build_system_prompt for Gemini scaffold-less branch (below).
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
        # _ensure_scaffold, write_canonical, ensure_runtime_hooks,
        # _materialize_pretooluse_hooks, _install_git_hooks. Diagnostics
        # are NOT called from here — runtime auto-trigger stays silent
        # (HATS-469 R3).
        self._refresh(install_time=False, result=result)

        # Provider inline system prompt — Gemini-only path.
        # Claude declares a scaffold template (HATS-284); ./CLAUDE.md is
        # owned by the scaffold + canonical aggregator. Gemini has no
        # scaffold mechanism, so bare-gemini in project_dir relies on
        # ./GEMINI.md inline-block injection. Documented asymmetry with
        # Claude Fork B (HATS-294); separate cleanup task tracks
        # symmetric drop later.
        if provider.scaffold_template_relpath() is None:
            prompt_content = provider.build_system_prompt(result)
            prompt_content = expand_path_placeholders(prompt_content, self.project_dir)
            provider.update_system_prompt(self.project_dir, prompt_content)

        # Persist active_role + provider.
        self.project_config.active_role = role_name
        self.project_config.provider = provider.name
        self.project_config.save(self.config_path)

        return result

    def clean(self) -> None:
        """Clean all active directories."""
        self._clean(preserve_local=False)

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
    ) -> None:
        """Single idempotent entry-point for on-disk state pull-up.

        Replaces the historical init/set_role/bump triple-dispatch
        (HATS-469). One method, called from every public entry-point that
        needs to bring the project tree to a consistent shape.

        Parameters:
            install_time: ``True`` for ``init`` / ``do_bump`` paths — runs
                the migration registry (``migrations.run_pending``).
                ``False`` for ``set_role`` (runtime first-session
                bootstrap) — skip migrations, they have already replayed
                via init or a prior bump.
            result: composition for the active role, or ``None`` when
                no role is active (legacy bare-bump path / init without
                ``-r``). When provided AND ``.git/`` exists, role-specific
                git hooks (HATS-088) are installed.

        Concurrency contract: extends migrations.py:29-33 — under N
        parallel ``init`` / ``set_role`` / ``bump`` processes against the
        same project, every method invoked here MUST be idempotent (this
        was already the case for the bump-only world; we explicitly
        extend the surface). The registry guarantees at-most-once across
        **sequential** invocations; concurrent ones may replay one step
        per process (by design, documented in migrations.py).

        Diagnostics (``_warn_orphan_*`` / ``_note_empty_*``) are NOT part
        of refresh — they live in :meth:`_run_diagnostics` and fire only
        on user-initiated paths (``do_bump``, init re-init). Runtime
        set_role stays silent (HATS-469 R3: per-session orphan-warning
        spam = bad UX).
        """
        # 1. Migration registry — install_time only (HATS-471).
        if install_time:
            from .migrations import run_pending
            run_pending(self)

        # 2. Heal — always.
        provider = get_provider(self.project_config.provider)
        self._ensure_scaffold(provider)
        self.write_canonical()

        # 3. Provider-level hooks — always (HATS-469 D1).
        # ``ensure_runtime_hooks`` writes ``.claude/settings.json``
        # PreToolUse entry; ``_materialize_pretooluse_hooks`` copies
        # hook bodies from package data. Both are idempotent and
        # REQUIRED on set_role first-session bootstrap (without them,
        # Claude fires PreToolUse against a non-existent script on
        # the very first Bash call).
        provider.ensure_runtime_hooks(self.project_dir)
        self._materialize_pretooluse_hooks(result)

        # 4. Role-specific git hooks — only if role active AND .git/
        # exists. ``.git/`` guard lives here (was inline in init); other
        # call sites benefit too — non-git project dirs skip silently.
        if result is not None and (self.project_dir / ".git").exists():
            self._install_git_hooks(result)

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
        self._note_empty_legacy_agent_dir()

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
        """HATS-471: unified v3→v4 layout migration entry-point.

        Consolidates the three historical splits — sessions / tracker / library —
        into a single call site so the migration registry has one entry per
        logical migration (not three for the same v4 layout move).

        The three sub-methods stay as private helpers (they remain
        independently testable and the split is convenient for narrow log
        diagnostics), but no other caller invokes them directly.
        """
        self._migrate_layout_v4_sessions()
        self._migrate_layout_v4_tracker()
        self._migrate_layout_v4_library()

    def _migrate_layout_v4_library(self) -> None:
        """One-shot migration of library-mirror artefacts (HATS-314).

        Moves `.agent/{rules,skills,hooks}/` → `<ai_hats_dir>/library/...`.
        `.claude/skills/` and `.githooks/` are NOT touched — they stay as
        copy-publish targets owned by external tooling.

        HATS-549 Phase 4: the ``.agent/hooks/`` entry is partitioned
        before the generic move — managed files (basename in the
        ai-hats-owned whitelist) head to ``<ai_hats_dir>/library/hooks/``
        as before; foreign files (anything else, including subdirs)
        head to ``<ai_hats_dir>/user-hooks/``. Keeps user-owned content
        out of the managed namespace where future sweep passes could
        delete it.
        """
        self._migrate_layout_v4_hooks_partition()
        for old_abs, new_abs in legacy_paths_by_class(self.project_dir, "library"):
            # The hooks pair was handled by the partition step; skip
            # so ``_idempotent_move`` doesn't run on the now-empty
            # ``.agent/hooks/`` directory (the partition leaves it
            # cleaned up).
            if old_abs.name == "hooks" and old_abs.parent.name == AGENT_DIR:
                continue
            self._idempotent_move(old_abs, new_abs)

    def _migrate_layout_v4_hooks_partition(self) -> None:
        """HATS-549 Phase 4: partition legacy ``.agent/hooks/`` contents
        AND reconcile pre-Phase-4 stuck states.

        Two passes:

        1. **Legacy partition** — walks ``.agent/hooks/``, routes each
           entry by basename whitelist:

           - basename in :func:`_ai_hats_owned_hook_basenames` →
             moved to ``<ai_hats_dir>/library/hooks/<name>``.
           - everything else (including subdirs like ``tests/``,
             arbitrary ``.py`` / ``.yaml`` / ``.log`` files) →
             moved to ``<ai_hats_dir>/user-hooks/<name>``.

        2. **Managed-namespace reconciliation** — walks
           ``<ai_hats_dir>/library/hooks/`` for foreign files left
           there by a pre-HATS-549 bump that auto-healed
           ``.agent/hooks/X`` → ``.agent/ai-hats/library/hooks/X``
           for user-owned content. Anything NOT in the whitelist
           (and not framework bookkeeping like ``.manifest``) is
           relocated to ``user-hooks/`` — getting user content out of
           the managed namespace before any future sweep could touch
           it. Combined with the healer Phase 4 pre-pass (which now
           also recognises the post-heal path prefix), the next bump
           cleanly heals stuck states from prior versions.

        Per-entry move preserves mode (``shutil.move`` is rename-based
        on the same filesystem, copytree-based across filesystems).
        Idempotent: a fully-partitioned project is a no-op on re-entry.
        Collisions on the destination side route through
        ``_safe_discard`` so the user can recover from trash. Discard
        failures emit a stderr WARN — silence here would mask a
        partial-state limbo (HATS-549 review S.4).

        On a fully-partitioned state, ``.agent/hooks/`` is empty;
        ``_safe_discard`` drops the empty directory so the legacy
        namespace doesn't linger.
        """
        managed_dst = _lib_hooks_dir(self.project_dir)
        user_dst = _user_hooks_dir(self.project_dir)
        whitelist = _ai_hats_owned_hook_basenames()

        # --- Pass 1: legacy partition ---
        legacy = self.project_dir / AGENT_DIR / "hooks"
        if legacy.is_dir():
            managed_dst.mkdir(parents=True, exist_ok=True)
            try:
                entries = list(legacy.iterdir())
            except OSError:
                entries = []

            for entry in entries:
                if entry.name in whitelist:
                    target = managed_dst / entry.name
                else:
                    user_dst.mkdir(parents=True, exist_ok=True)
                    target = user_dst / entry.name
                if target.exists():
                    self._safe_discard_with_warn(
                        entry, reason="hooks-partition-collision",
                    )
                    continue
                shutil.move(str(entry), str(target))

            try:
                if not any(legacy.iterdir()):
                    _safe_discard(
                        legacy, reason="hooks-partition-cleanup",
                        project_dir=self.project_dir,
                    )
            except OSError as e:
                print(
                    f"[ai-hats] WARN: hooks-partition: could not clean up "
                    f"empty {legacy}: {e}", file=sys.stderr,
                )

        # --- Pass 2: managed-namespace reconciliation ---
        # If a previous-version bump auto-healed settings.json to point
        # at .agent/ai-hats/library/hooks/<x> AND moved the file there,
        # the file is currently sitting in the managed namespace where
        # any future framework-side sweep could mistake it for managed
        # content and discard it. Move it out NOW, while we're already
        # in a "rearrange hooks" frame.
        if managed_dst.is_dir():
            try:
                managed_entries = list(managed_dst.iterdir())
            except OSError:
                managed_entries = []
            for entry in managed_entries:
                # Skip framework bookkeeping and whitelisted basenames.
                if entry.name == ".manifest":
                    continue
                if entry.name in whitelist:
                    continue
                user_dst.mkdir(parents=True, exist_ok=True)
                target = user_dst / entry.name
                if target.exists():
                    self._safe_discard_with_warn(
                        entry, reason="hooks-reconcile-collision",
                    )
                    continue
                shutil.move(str(entry), str(target))

    def _safe_discard_with_warn(self, path: Path, *, reason: str) -> None:
        """Wrap :func:`_safe_discard` with a stderr WARN on failure.

        HATS-549 review S.4: on a read-only filesystem (some CI gates)
        ``_safe_discard`` fails silently, leaving the caller's flow
        in partial-state limbo. The WARN ensures the user sees the
        problem instead of triaging mysterious downstream errors.
        """
        try:
            _safe_discard(
                path, reason=reason, project_dir=self.project_dir,
            )
        except OSError as e:
            try:
                rel = path.relative_to(self.project_dir).as_posix()
            except ValueError:
                rel = str(path)
            print(
                f"[ai-hats] WARN: {reason}: could not safe-discard {rel}: "
                f"{e}", file=sys.stderr,
            )

    @staticmethod
    def _ai_hats_owned_hook_basenames() -> frozenset[str]:
        """Set of hook basenames the framework itself ships.

        Sourced from ``importlib.resources.files("ai_hats.library") /
        "hooks"`` at import time — same surface as
        :meth:`_materialize_pretooluse_hooks`. Anything not in this set
        is treated as user-owned content by the v4 hooks-partition step
        (HATS-549 Phase 4).

        Exposed as a public-ish static so :mod:`ai_hats.migration_healer`
        can read the same whitelist when deciding whether to auto-disable
        vs. heal a settings.json hook entry.
        """
        return _ai_hats_owned_hook_basenames()

    def _migrate_layout_v4_tracker(self) -> None:
        """One-shot migration of tracker + root-class artefacts (HATS-313).

        Moves backlog/, hypotheses/, decisions/, STATE.md, and .last_backup
        from their legacy .agent/ locations to <ai_hats_dir>/tracker/* (and
        the framework-root entries STATE.md / .last_backup directly under
        <ai_hats_dir>/). Idempotent on a re-run after success.
        """
        for class_ in ("tracker", "root"):
            for old_abs, new_abs in legacy_paths_by_class(self.project_dir, class_):
                self._idempotent_move(old_abs, new_abs)

    def _migrate_layout_v4_sessions(self) -> None:
        """One-shot migration of session-class artefacts to <ai_hats_dir>/sessions/.

        Moves seven legacy locations (pipeline_runs, retrospectives, audits,
        handoffs, experiments, worktrees, worktree.json) plus an orphan
        handoff file. Idempotent: a no-op once every legacy path is gone.
        See ADR `2026-05-13-hats-316-ai-hats-dir-layout.md`.
        """
        for old_abs, new_abs in legacy_paths_by_class(self.project_dir, "sessions"):
            self._idempotent_move(old_abs, new_abs)
        # Pick up the orphan handoff file lingering at .agent/ root.
        orphan = self.project_dir / AGENT_DIR / "handoff-2026-04-09-hats-061.md"
        if orphan.exists():
            from .paths import handoffs_dir

            dest_dir = handoffs_dir(self.project_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / orphan.name
            if not dest.exists():
                shutil.move(str(orphan), str(dest))
            else:
                try:
                    _safe_discard(
                        orphan, reason="layout-v4-orphan",
                        project_dir=self.project_dir,
                    )
                except OSError:
                    pass

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
                    old_abs, reason="move-collision",
                    project_dir=self.project_dir,
                )
            except OSError:
                pass
            return
        # File-vs-file or type mismatch: trust new, drop old.
        try:
            _safe_discard(
                old_abs, reason="move-collision",
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
        if provider_name in PROVIDERS:
            return
        raise ValueError(
            f"Unknown provider: {provider_name}. Available: {sorted(PROVIDERS.keys())}"
        )

    def _clean(self, *, preserve_local: bool = False) -> None:
        """Clean active directories.

        Each subdir keeps a manifest of ai-hats-managed entries so that
        user-authored files placed alongside survive re-assembly.
        """
        rules_dir = _lib_rules_dir(self.project_dir)
        if rules_dir.exists():
            if preserve_local:
                self._clean_non_local(rules_dir)
            else:
                _safe_discard(
                    rules_dir, reason="clean-rules",
                    project_dir=self.project_dir,
                )
                rules_dir.mkdir(parents=True, exist_ok=True)

        for subdir in ("skills", "hooks"):
            target = self.agent_dir / subdir
            if target.exists():
                self._clean_managed_entries(target)

    def _clean_non_local(self, rules_dir: Path) -> None:
        """Remove only library-sourced rules, keep project-local ones."""
        marker_file = rules_dir / ".library_rules"
        if marker_file.exists():
            library_rules = {r for r in marker_file.read_text().strip().split("\n") if r}
            for rule_name in library_rules:
                rule_path = rules_dir / rule_name
                if rule_path.exists():
                    _safe_discard(
                        rule_path, reason="clean-rules",
                        project_dir=self.project_dir,
                    )
            _safe_discard(
                marker_file, reason="clean-marker",
                project_dir=self.project_dir,
            )

    def _clean_managed_entries(self, target: Path) -> None:
        """Remove only entries listed in the target's `.ai-hats-managed` manifest.

        Without a manifest the directory is assumed to hold only user content,
        so we leave it alone.

        HATS-470: converted from ``@staticmethod`` so trash recording
        gets ``self.project_dir`` for relpath preservation.
        """
        marker = target / MANAGED_SKILLS_MARKER
        if not marker.exists():
            return
        managed = {n for n in marker.read_text().splitlines() if n.strip()}
        for name in managed:
            entry = target / name
            if entry.exists():
                _safe_discard(
                    entry, reason="clean-managed",
                    project_dir=self.project_dir,
                )
        _safe_discard(
            marker, reason="clean-managed-marker",
            project_dir=self.project_dir,
        )

    @staticmethod
    def _write_managed_manifest(target: Path, names: list[str]) -> None:
        """Write/remove `.ai-hats-managed` listing entries ai-hats owns in `target`."""
        marker = target / MANAGED_SKILLS_MARKER
        if names:
            marker.write_text("\n".join(names) + "\n")
        elif marker.exists():
            # Manifest is framework bookkeeping — empty-state cleanup of
            # our own marker, no user content. Whitelisted.
            marker.unlink()  # safe-delete: ok framework-manifest

    def _find_hook_script(self, script_ref: str) -> Path | None:
        """Find a hook script across library paths."""
        script_path = Path(script_ref)
        if script_path.is_absolute() and script_path.exists():
            return script_path
        for lib_path in reversed(self.library_paths):
            candidate = lib_path / script_ref
            if candidate.exists():
                return candidate
        return None

    # ----- Skill-contributed git hooks (HATS-088) -----

    def _materialize_pretooluse_hooks(
        self, result: "CompositionResult | None" = None
    ) -> None:
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
        from importlib.resources import files

        try:
            source_root = files("ai_hats.library") / "hooks"
        except (ModuleNotFoundError, FileNotFoundError) as e:
            raise AssemblyError(
                "ai_hats.library.hooks not found in package data — "
                "broken install"
            ) from e

        source_root_path = Path(str(source_root))
        if not source_root_path.is_dir():
            raise AssemblyError(
                f"Hook source dir missing: {source_root_path}"
            )

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

        body = (
            "# ai-hats managed — do not edit\n"
            + "\n".join(sorted(new_names))
            + "\n"
        )
        _safe_replace(
            manifest_path,
            body.encode(),
            reason="materialize-pretooluse-manifest",
            project_dir=self.project_dir,
        )

    def _install_git_hooks(self, result: CompositionResult) -> None:
        """Install git hooks declared by composed skills.

        Skills declare hooks in their `metadata.yaml` under `git_hooks:`.
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
        declared = self._collect_skill_git_hooks(result)
        if not declared:
            # No skill declares git hooks. Don't touch user's repo.
            # Still clean up our previously-installed managed files (in case the
            # user removed all skills with git_hooks) so stale entries don't linger.
            self._cleanup_managed_git_hooks()
            return

        githooks_dir = self.project_dir / GITHOOKS_DIR
        githooks_dir.mkdir(exist_ok=True)

        # Remove anything we previously owned, then re-install fresh.
        self._cleanup_managed_git_hooks()

        new_manifest: list[str] = []
        warnings: list[str] = []

        for event, entries in declared.items():
            if not entries:
                continue
            event_d = githooks_dir / f"{event}.d"
            event_d.mkdir(exist_ok=True)

            for skill_name, script_path in entries:
                src = self._resolve_skill_script(skill_name, script_path, result)
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
            installed = self._install_dispatcher(dispatcher_path)
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
        self._configure_hooks_path(warnings)

        for w in warnings:
            print(f"[ai-hats] WARNING: {w}")

    def _collect_skill_git_hooks(
        self,
        result: CompositionResult,
    ) -> dict[str, list[tuple[str, str]]]:
        """Walk composed skills and collect their declared git hooks.

        Returns: {event_name: [(skill_name, script_path), ...]}
        """
        collected: dict[str, list[tuple[str, str]]] = {}
        for skill in result.skills:
            metadata_path = skill.source_path / "metadata.yaml"
            metadata = SkillMetadata.from_yaml(metadata_path)
            if not metadata.git_hooks:
                continue
            for event, scripts in metadata.git_hooks.items():
                collected.setdefault(event, []).extend((skill.name, script) for script in scripts)
        return collected

    def _collect_skill_runtime_hooks(
        self,
        result: CompositionResult,
    ) -> dict[str, list[tuple[str, RuntimeHook]]]:
        """Walk composed skills and collect their declared runtime hooks (HATS-597).

        Mirrors :meth:`_collect_skill_git_hooks` but for provider runtime
        hooks (PreToolUse / PostToolUse). Validation (unknown event, malformed
        row) already happened at ``SkillMetadata.from_yaml`` time and fails
        loud there.

        Returns: {event_name: [(skill_name, RuntimeHook), ...]}
        """
        collected: dict[str, list[tuple[str, RuntimeHook]]] = {}
        for skill in result.skills:
            metadata_path = skill.source_path / "metadata.yaml"
            metadata = SkillMetadata.from_yaml(metadata_path)
            if not metadata.runtime_hooks:
                continue
            for event, hooks in metadata.runtime_hooks.items():
                collected.setdefault(event, []).extend(
                    (skill.name, hook) for hook in hooks
                )
        return collected

    @staticmethod
    def _resolve_skill_script(
        skill_name: str,
        script_path: str,
        result: CompositionResult,
    ) -> Path | None:
        """Resolve a script path declared in a skill's metadata to an absolute path."""
        for skill in result.skills:
            if skill.name != skill_name:
                continue
            candidate = (skill.source_path / script_path).resolve()
            if candidate.exists():
                return candidate
        return None

    def _install_dispatcher(self, dispatcher_path: Path) -> bool:
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

    def _cleanup_managed_git_hooks(self) -> None:
        """Remove files listed in our manifest. Idempotent."""
        githooks_dir = self.project_dir / GITHOOKS_DIR
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
                    target, reason="githook-dispatcher",
                    project_dir=self.project_dir,
                )
        # Manifest itself is framework bookkeeping — whitelist.
        manifest_path.unlink(missing_ok=True)  # safe-delete: ok framework-manifest
        # Remove empty <event>.d/ subdirs.
        for child in githooks_dir.iterdir():
            if child.is_dir() and child.name.endswith(".d") and not any(child.iterdir()):
                child.rmdir()  # safe-delete: ok empty-dir

    def _configure_hooks_path(self, warnings: list[str]) -> None:
        """Set git config core.hooksPath = .githooks if safe to do so."""
        try:
            current = subprocess.run(
                ["git", "config", "--get", "core.hooksPath"],
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, FileNotFoundError):
            warnings.append("git not found — cannot configure core.hooksPath")
            return

        existing = current.stdout.strip() if current.returncode == 0 else ""
        target = GITHOOKS_DIR

        if existing == target:
            return  # Already correct.
        if existing and existing != target:
            warnings.append(
                f"core.hooksPath is already set to '{existing}' — not "
                f"overwriting. To enable ai-hats hooks, run: "
                f"git config core.hooksPath {target}  (or merge dispatchers manually)"
            )
            return

        try:
            subprocess.run(
                ["git", "config", "core.hooksPath", target],
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            warnings.append(f"failed to set core.hooksPath: {e.stderr.strip() or e}")

    # ----- HATS-593: drift-detecting hook re-materialization -----

    def sync_hooks(self) -> HookSyncResult:
        """Re-materialize ONLY the git-hook surface if it has drifted.

        Idempotent no-op when on-disk hooks already match the composed source.
        Unlike ``init`` / ``_refresh`` this runs NO migrations, scaffold,
        provider hooks, or prompt recomposition — just the
        :meth:`_install_git_hooks` surface. Invoked by the post-merge /
        post-checkout git hooks and the ``session_start`` lifecycle hook so
        ``.githooks/`` never goes stale after a merge / pull / checkout.
        """
        if not (self.project_dir / ".git").exists():
            return HookSyncResult(status="skipped", detail="not a git repo")
        cfg = self.project_config
        effective_role = cfg.active_role or cfg.default_role
        if not effective_role:
            return HookSyncResult(status="skipped", detail="no active role")
        result = compose_for_role(self, effective_role)
        if not self._git_hooks_drift(result):
            return HookSyncResult(status="in-sync")
        # Failure-mode #5: refuse to heal from a stale binary. If the installed
        # ai-hats is strictly behind upstream master (reuse of the update-banner
        # drift signal), the composed-source the hooks would be derived from may
        # not match what the merged repo actually expects — recommend
        # ``self update`` rather than materialize blind.
        if self._binary_behind_source():
            return HookSyncResult(
                status="version-skew",
                detail="installed ai-hats is behind upstream — run 'ai-hats self update'",
            )
        self._install_git_hooks(result)
        return HookSyncResult(status="synced")

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

    def _expected_git_hook_files(self, result: CompositionResult) -> dict[str, bytes]:
        """Managed ``.githooks/`` relpath -> expected bytes for ``result``.

        Mirrors :meth:`_install_git_hooks` WITHOUT writing, so drift can be
        detected cheaply. Keys: ``<event>.d/<skill>-<basename>`` per declared
        script, plus ``<event>`` per dispatcher.
        """
        declared = self._collect_skill_git_hooks(result)
        expected: dict[str, bytes] = {}
        for event, entries in declared.items():
            has_entry = False
            for skill_name, script_path in entries:
                src = self._resolve_skill_script(skill_name, script_path, result)
                if src is None:
                    continue
                expected[f"{event}.d/{skill_name}-{src.name}"] = src.read_bytes()
                has_entry = True
            if has_entry and GITHOOKS_DISPATCHER_TEMPLATE.exists():
                expected[event] = GITHOOKS_DISPATCHER_TEMPLATE.read_bytes()
        return expected

    def _git_hooks_drift(self, result: CompositionResult) -> bool:
        """True if managed git hooks on disk diverge from ``result``.

        Compares the expected managed file set (content + exec-bit + presence)
        and the manifest against disk. A foreign (non-marker) dispatcher is
        left to the install policy and is never counted as drift here.
        """
        githooks_dir = self.project_dir / GITHOOKS_DIR
        manifest_path = githooks_dir / GITHOOKS_MANIFEST
        expected = self._expected_git_hook_files(result)
        if not expected:
            # Nothing should be installed → drift iff a stale manifest lingers.
            return self._read_canonical_manifest(manifest_path) != set()
        if self._read_canonical_manifest(manifest_path) != set(expected):
            return True
        for rel, content in expected.items():
            target = githooks_dir / rel
            if not target.is_file():
                return True
            # Top-level dispatcher with a foreign body → leave alone (not drift).
            if "/" not in rel:
                try:
                    if GITHOOKS_DISPATCHER_MARKER not in target.read_text():
                        continue
                except OSError:
                    return True
            try:
                if target.read_bytes() != content:
                    return True
            except OSError:
                return True
            if not target.stat().st_mode & 0o100:  # owner-exec bit lost
                return True
        return False

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
            "hooks": {
                k: getattr(result.hooks, k)
                for k in (
                    "session_start",
                    "session_end",
                    "task_start",
                    "task_complete",
                    "task_failed",
                    "error",
                )
                if getattr(result.hooks, k)
            },
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
        - Provider system prompt (``./CLAUDE.md`` / ``./GEMINI.md``).
        """
        del result  # composition is checked in-memory via composer.compose
        health: dict[str, str] = {}
        imports_md = self._canonical_dir / "imports.md"
        health["imports.md"] = "OK" if imports_md.exists() else "Missing"
        prompt_ok = any((self.project_dir / f).exists() for f in ("GEMINI.md", "CLAUDE.md"))
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
        aggregator = expand_path_placeholders(
            aggregator.decode(), self.project_dir
        ).encode()
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
                target, reason="canonical-stale",
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
        paths = sorted(
            f"@./{USER_RULES_SUBDIR}/{md.name}"
            for md in user_rules_dir.glob("*.md")
        )
        if not paths:
            return ""
        return "\n".join(paths) + "\n"

    @staticmethod
    def _atomic_write_if_changed(path: Path, content: bytes) -> bool:
        """Write `content` to `path` only if it would change. Returns True on write."""
        if path.exists() and path.read_bytes() == content:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)
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
                source_lookup = self._build_v07_tier2_source_lookup()
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
            warns = check_branches_modify_paths(
                self.project_dir, report.paths_to_delete
            )
            if warns:
                print(
                    "WARN: local branches modify v0.7-migration paths slated "
                    "for deletion:",
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
            raise AssemblyError(render_user_edits_refusal(
                report.user_edits, self.project_dir
            ))

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

        execute_deletions(
            report, self._canonical_dir, project_dir=self.project_dir
        )

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

    def _build_v07_tier2_source_lookup(self) -> dict[str, Path]:
        """Map mirror-dir name → source root for every rule/skill the composer resolved.

        Used by :func:`ai_hats.migration_v07.plan_migration` so Tier-2 dirs
        whose source we can locate get a diff baseline (safe-to-delete when
        content matches).
        """
        from .models import resolve_namespace

        resolver = self.composer.resolver
        effective = self.project_config.active_role or self.project_config.default_role
        if not effective:
            return {}
        try:
            comp = compose_for_role(self, effective)
        except Exception:  # noqa: BLE001 — defensive: any compose failure → empty lookup
            return {}
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
            self.project_config.save(self.config_path)

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
        self.project_config.save(self.config_path)

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
        """One-shot: ensure `.agent/ai-hats/` (or current `<ai_hats_dir>/`) is in .gitignore.

        HATS-317 removed the dynamic managed-block generator. The new policy
        is a single static line written once at ``init`` time. ``set_role``
        and ``bump`` do not touch .gitignore — the user owns the file.
        Idempotent: re-running ``init`` is a no-op if the line is present.
        """
        from .paths import _read_ai_hats_dir_from_yaml

        gitignore = self.project_dir / GITIGNORE_FILE
        ai_hats_rel = _read_ai_hats_dir_from_yaml(self.project_dir) or ".agent/ai-hats"
        # Normalize: trailing slash so directories are matched explicitly.
        line = ai_hats_rel.rstrip("/") + "/"

        if not gitignore.exists():
            _safe_replace(
                gitignore, (line + "\n").encode("utf-8"),
                reason="gitignore-init", project_dir=self.project_dir,
            )
            return
        existing = gitignore.read_text()
        existing_lines = {ln.strip() for ln in existing.splitlines()}
        if line in existing_lines:
            return
        sep = "" if existing.endswith("\n") else "\n"
        _safe_replace(
            gitignore, (existing + sep + line + "\n").encode("utf-8"),
            reason="gitignore-append", project_dir=self.project_dir,
        )

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
            gitignore, new_text.encode("utf-8"),
            reason="gitignore-strip", project_dir=self.project_dir,
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
        marker = Path.home() / ".claude" / "skills" / ".ai-hats-managed"
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

    def _gitignore_swap_entry(self, old_rel: str, new_rel: str) -> bool:
        """Replace .gitignore line `old_rel/` with `new_rel/`.

        Returns True if the file was changed. Idempotent: if the old line is
        missing, just ensures the new line is present. No-op when both lines
        already match the desired post-state.
        """
        gitignore = self.project_dir / GITIGNORE_FILE
        old_line = old_rel.rstrip("/") + "/"
        new_line = new_rel.rstrip("/") + "/"

        if not gitignore.exists():
            _safe_replace(
                gitignore, (new_line + "\n").encode("utf-8"),
                reason="gitignore-swap", project_dir=self.project_dir,
            )
            return True

        text = gitignore.read_text()
        lines = text.splitlines()
        seen_new = any(ln.strip() == new_line for ln in lines)
        out: list[str] = []
        swapped = False
        for ln in lines:
            stripped = ln.strip()
            if stripped == old_line:
                if not seen_new and not swapped:
                    out.append(new_line)
                    swapped = True
                # else: drop duplicate old entry
                continue
            out.append(ln)
        if not swapped and not seen_new:
            out.append(new_line)
        body = "\n".join(out)
        if text.endswith("\n"):
            body += "\n"
        if body == text:
            return False
        _safe_replace(
            gitignore, body.encode("utf-8"),
            reason="gitignore-swap", project_dir=self.project_dir,
        )
        return True

    # ----- Relocation (HATS-366) -----

    # Top-level entries under <ai_hats_dir>/ that relocate() moves to the
    # new location. Order: directories first (cheap renames), files last.
    # `.venv` is intentionally NOT here — managed venvs are deleted (their
    # internal absolute paths break on move) and recreated on next session.
    _RELOCATE_ENTRIES: tuple[str, ...] = (
        "library",
        "tracker",
        "sessions",
        "traces",
        "pipeline_steps",
        "STATE.md",
        ".last_backup",
    )

    def relocate(self, new_dir: str) -> "RelocationResult":
        """Move framework directory from current ``ai_hats_dir`` to ``new_dir``.

        Steps (idempotent — partial-failure re-run completes what's missing):
          1. Validate ``new_dir`` via :func:`normalize_ai_hats_dir`.
          2. Move ``library / tracker / sessions / traces / pipeline_steps /
             STATE.md / .last_backup`` to the new location.
          3. If venv is managed (``venv_path is None``) and ``<old>/.venv``
             exists — delete it. The bash launcher recreates the venv on
             next session at the new location.
          4. Persist ``ai_hats_dir = new_dir`` in ``ai-hats.yaml``.
          5. If ``manage_gitignore=true`` — swap the old/ entry for new/.
          6. Remove ``<old>/`` if empty.

        Raises:
            ValueError: ``new_dir`` invalid OR destination already exists and
              contains conflicting entries.
        """
        from .paths import normalize_ai_hats_dir

        new_rel = normalize_ai_hats_dir(new_dir)
        old_rel = self.project_config.ai_hats_dir
        if old_rel == new_rel:
            return RelocationResult(old=old_rel, new=new_rel, changed=False)

        old_abs = self.project_dir / old_rel
        new_abs = self.project_dir / new_rel

        # Refuse if destination has any entry that would collide with what
        # we're about to move. An EMPTY destination (or one containing only
        # leftovers from a partial previous run) is fine.
        if new_abs.exists():
            for name in self._RELOCATE_ENTRIES:
                src = old_abs / name
                dst = new_abs / name
                if src.exists() and dst.exists():
                    raise ValueError(
                        f"relocate: destination collision at {new_rel}/{name} "
                        "— refusing to overwrite. Remove the existing entry "
                        "or pick a different ai_hats_dir."
                    )

        new_abs.mkdir(parents=True, exist_ok=True)

        moved: list[str] = []
        for name in self._RELOCATE_ENTRIES:
            src = old_abs / name
            dst = new_abs / name
            if not src.exists():
                continue
            if dst.exists():
                # Idempotent: previous run already moved this entry.
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append(name)

        venv_removed = False
        if self.project_config.venv_path is None:
            old_venv = old_abs / ".venv"
            if old_venv.exists():
                _safe_discard(
                    old_venv, reason="venv-relocate",
                    project_dir=self.project_dir,
                )
                venv_removed = True

        self.project_config.ai_hats_dir = new_rel
        self.project_config.save(self.config_path)

        gitignore_updated = False
        if self.project_config.manage_gitignore:
            gitignore_updated = self._gitignore_swap_entry(old_rel, new_rel)

        # Best-effort cleanup of an empty old dir. Leave it alone if the
        # user has unrelated files there.
        if old_abs.exists() and old_abs.is_dir():
            try:
                old_abs.rmdir()  # safe-delete: ok empty-dir
            except OSError:
                pass

        return RelocationResult(
            old=old_rel,
            new=new_rel,
            changed=True,
            moved=moved,
            venv_removed=venv_removed,
            gitignore_updated=gitignore_updated,
        )


class RelocationResult:
    """Outcome of :meth:`Assembler.relocate`. Diagnostic-only; CLI prints it."""

    __slots__ = ("old", "new", "changed", "moved", "venv_removed", "gitignore_updated")

    def __init__(
        self,
        *,
        old: str,
        new: str,
        changed: bool,
        moved: list[str] | None = None,
        venv_removed: bool = False,
        gitignore_updated: bool = False,
    ) -> None:
        self.old = old
        self.new = new
        self.changed = changed
        self.moved = moved or []
        self.venv_removed = venv_removed
        self.gitignore_updated = gitignore_updated


class AssemblyError(Exception):
    pass
