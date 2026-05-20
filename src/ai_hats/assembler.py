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
from pathlib import Path

from .composer import Composer, CompositionResult
from .resolver import LibraryResolver
from .models import (
    ComponentType,
    ProjectConfig,
    SkillMetadata,
)
from .paths import (
    hooks_dir as _lib_hooks_dir,
    legacy_paths_by_class,
    rules_dir as _lib_rules_dir,
    skills_dir as _lib_skills_dir,
)
from .placeholders import expand_path_placeholders
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


class Assembler:
    """Manages the lifecycle of role assembly in a project directory."""

    def __init__(self, project_dir: Path, library_paths: list[Path] | None = None) -> None:
        self.project_dir = project_dir
        self.agent_dir = project_dir / AGENT_DIR
        self.config_path = project_dir / PROJECT_CONFIG
        self.project_config = ProjectConfig.from_yaml(self.config_path)

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

        # Global user libraries
        global_lib = Path.home() / ".ai-hats"
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
        prompt_path.write_bytes(template.read_bytes())

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
            prompt_path.write_text(new_content)

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
        for rel in managed:
            if rel.startswith("skills/"):
                continue
            target = claude_dir / rel
            target.unlink(missing_ok=True)

        # Well-known publish artefacts as belt-and-suspenders.
        for rel in ("CLAUDE.md", "priorities.md", "role.md", "skills_index.md"):
            (claude_dir / rel).unlink(missing_ok=True)
        for sub in ("traits", "rules"):
            sub_dir = claude_dir / sub
            if sub_dir.is_dir():
                shutil.rmtree(sub_dir, ignore_errors=True)

        manifest.unlink(missing_ok=True)

        # Best-effort empty-dir cleanup of `.claude/` itself if `skills/`
        # is also absent — but never delete `.claude/skills/` content.
        try:
            if not any(claude_dir.iterdir()):
                claude_dir.rmdir()
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
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
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
            # restrict rmtree to absolute paths whose basename carries
            # that prefix — a corrupt or hand-edited pointer cannot
            # redirect cleanup at the user's project tree.
            if backup_ref.is_file():
                try:
                    tmp_target = Path(backup_ref.read_text().strip())
                    if (
                        tmp_target.is_absolute()
                        and tmp_target.exists()
                        and tmp_target.name.startswith("ai-hats-backup-")
                    ):
                        shutil.rmtree(tmp_target, ignore_errors=True)
                except (OSError, ValueError):
                    pass
                backup_ref.unlink(missing_ok=True)
            elif backup_ref.is_dir():
                shutil.rmtree(backup_ref, ignore_errors=True)
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

        # Create/update ai-hats.yaml
        save_config = False
        if not self.config_path.exists():
            self.project_config.provider = provider or "gemini"
            if role:
                self.project_config.default_role = role
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

        # Write provider scaffold (./CLAUDE.md etc.) if missing — HATS-284.
        # Must run before set_role so update_system_prompt sees the lowercase
        # scaffold markers and skips writing the legacy uppercase block.
        active_provider = get_provider(self.project_config.provider)
        self._ensure_scaffold(active_provider)

        # HATS-285: strip legacy uppercase AI-HATS block from existing
        # ./CLAUDE.md so old projects upgrade in place.
        self._migrate_claude_md_to_v3(active_provider)

        # HATS-317: one-shot .gitignore entry. Idempotent; no managed block.
        if self.project_config.manage_gitignore:
            self._ensure_gitignore_entry()

        # HATS-407: write the user-rules aggregator at init time so direct
        # ``claude`` in project_dir has a stable entry-point even before the
        # first ai-hats session. Sweeps stale v0.6 framework files via the
        # manifest. No role-content materialization (per-session compose
        # handles that — HATS-294).
        self.write_canonical()

        # Apply role if specified — HATS-407: persists default_role in yaml
        # and installs HATS-088 git hooks for the chosen role. NO heavy
        # set_role chain (backup/clean/copy/verify is gone post-294).
        if role:
            self.project_config.default_role = role
            self.project_config.save(self.config_path)
            # Install git hooks only when a git repo is present; otherwise
            # `.git/hooks/` does not exist and `_install_git_hooks` would
            # need a no-op guard. Cheap pre-check keeps the path tidy.
            if (self.project_dir / ".git").exists():
                result = self.composer.compose(role, overlay=self._get_overlay(role))
                self._install_git_hooks(result)

    def _get_overlay(self, role_name: str):
        """Get overlay for a role from project config, or None."""
        overlay = self.project_config.customizations.get(role_name)
        if overlay and overlay.is_empty:
            return None
        return overlay

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
        result = self.composer.compose(role_name, overlay=self._get_overlay(role_name))

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
        result = self.composer.compose(role_name, overlay=self._get_overlay(role_name))

        # Non-fatal compose errors (e.g. missing optional rule) are surfaced
        # via result.errors; do not abort.

        # 1. Bring ./CLAUDE.md to the v3 scaffold layout if a legacy
        # uppercase AI-HATS block or v2 inline injection is present. The
        # migrator also runs in ``bump`` and ``init``; keeping the call
        # here lets first-session bootstrap heal legacy projects without
        # forcing the user to run bump explicitly. Idempotent.
        self._migrate_claude_md_to_v3(provider)
        # 2. Ensure provider scaffold exists (./CLAUDE.md for Claude). Cheap
        # and idempotent — no-op for providers without a scaffold template
        # (Gemini), and no-op when the file already exists.
        self._ensure_scaffold(provider)

        # 3. Skill-contributed git hooks (HATS-088).
        self._install_git_hooks(result)

        # 4. Canonical user-rules aggregator (HATS-294).
        self.write_canonical()

        # 5. Provider inline system prompt — Gemini-only path.
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

        # 6. Persist active_role + provider.
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
            result = self.composer.compose(
                effective_role,
                overlay=self._get_overlay(effective_role),
            )
            status["tree"] = self._build_tree(result)
            status["health"] = self._check_health(result)
            status["errors"] = result.errors

        return status

    def bump(self) -> CompositionResult | None:
        """Refresh on-disk state: migrations, scaffold, canonical, git hooks.

        HATS-407: no longer triggers a full role-compose-and-materialize
        cycle (per-session compose handles framework content in memory —
        HATS-294). bump is now strictly a migration + refresh command:

        1. ``_cleanup_obsolete_files`` — drop files retired by prior releases.
        2. ``heal_external_refs`` (HATS-397) — fix legacy-path refs in user
           docs BEFORE the scaffold migrator runs so its edits do not look
           like user-dirty content to the inventory fallback.
        3. ``_migrate_claude_md_to_v3`` — bring ``./CLAUDE.md`` to the v3
           scaffold layout.
        4. ``_migrate_layout_v4_*`` — relocate legacy `.agent/...` paths
           into ``<ai_hats_dir>/sessions/`` / ``tracker/`` / ``library/``.
        5. ``_note_empty_legacy_agent_dir`` — surface a NOTE when only
           the managed ``.agent/ai-hats/`` subtree remains.
        6. ``_ensure_scaffold`` — re-create the provider scaffold if the
           user-owned prompt file is missing.
        7. ``write_canonical`` — regenerate the user-rules aggregator and
           sweep stale framework files via the manifest.
        8. ``_install_git_hooks`` — re-install skill-contributed hooks for
           the active role (HATS-088).

        Returns the CompositionResult for the active role (or ``None`` when
        the project has no active_role yet — legacy bare-bump path).

        HATS-337: the HATS-330 mixed-install gate was removed — venv-first
        launcher architecture makes mixed installs impossible by
        construction.
        """
        self._cleanup_obsolete_files(self.project_dir)
        provider = get_provider(self.project_config.provider)
        # HATS-397: heal stale legacy-path refs FIRST, while user files are
        # still clean in git. Running after `_migrate_claude_md_to_v3` would
        # see ai-hats-induced edits as user-dirty and force inventory fallback.
        from .migration_healer import heal_external_refs

        heal_external_refs(self.project_dir)
        self._migrate_claude_md_to_v3(provider)
        self._migrate_layout_v4_sessions()
        self._migrate_layout_v4_tracker()
        self._migrate_layout_v4_library()
        self._note_empty_legacy_agent_dir()

        # Scaffold + canonical aggregator. Run unconditionally — these are
        # disk artefacts every project needs, regardless of active_role.
        self._ensure_scaffold(provider)
        self.write_canonical()

        # Git hooks live in `.git/hooks/` and reflect the skills composed
        # for the active role. Without an active_role we have nothing to
        # install — return early.
        role = self.project_config.active_role or self.project_config.default_role
        if not role:
            return None
        result = self.composer.compose(role, overlay=self._get_overlay(role))
        self._install_git_hooks(result)
        return result

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

    def _migrate_layout_v4_library(self) -> None:
        """One-shot migration of library-mirror artefacts (HATS-314).

        Moves `.agent/{rules,skills,hooks}/` → `<ai_hats_dir>/library/...`.
        `.claude/skills/` and `.githooks/` are NOT touched — they stay as
        copy-publish targets owned by external tooling.
        """
        for old_abs, new_abs in legacy_paths_by_class(self.project_dir, "library"):
            self._idempotent_move(old_abs, new_abs)

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
                    orphan.unlink()
                except OSError:
                    pass

    @staticmethod
    def _idempotent_move(old_abs: Path, new_abs: Path) -> None:
        """Move `old_abs` to `new_abs`, merging into existing dirs when needed.

        - new_abs missing → simple shutil.move (parent created).
        - new_abs is a dir → copy items missing on the new side, then drop old.
        - new_abs is a file → assume already migrated; remove the stale source.
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
            # New side wins on collisions: drop the rest of old to keep the
            # migration deterministic. rmtree is best-effort so a concurrent
            # cleanup or read-only entry is benign.
            shutil.rmtree(old_abs, ignore_errors=True)
            return
        # File-vs-file or type mismatch: trust new, drop old.
        try:
            if old_abs.is_dir():
                shutil.rmtree(old_abs)
            else:
                old_abs.unlink()
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
                shutil.rmtree(rules_dir)
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
                    shutil.rmtree(rule_path)
            marker_file.unlink()

    @staticmethod
    def _clean_managed_entries(target: Path) -> None:
        """Remove only entries listed in the target's `.ai-hats-managed` manifest.

        Without a manifest the directory is assumed to hold only user content,
        so we leave it alone.
        """
        marker = target / MANAGED_SKILLS_MARKER
        if not marker.exists():
            return
        managed = {n for n in marker.read_text().splitlines() if n.strip()}
        for name in managed:
            entry = target / name
            if entry.is_dir():
                shutil.rmtree(entry)
            elif entry.exists():
                entry.unlink()
        marker.unlink()

    @staticmethod
    def _write_managed_manifest(target: Path, names: list[str]) -> None:
        """Write/remove `.ai-hats-managed` listing entries ai-hats owns in `target`."""
        marker = target / MANAGED_SKILLS_MARKER
        if names:
            marker.write_text("\n".join(names) + "\n")
        elif marker.exists():
            marker.unlink()

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
                target.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        # Remove empty <event>.d/ subdirs.
        for child in githooks_dir.iterdir():
            if child.is_dir() and child.name.endswith(".d") and not any(child.iterdir()):
                child.rmdir()

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

    def _build_tree(self, result: CompositionResult) -> dict:
        """Build a dependency tree representation."""
        return {
            "name": result.name,
            "priorities": result.priorities,
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
            target.unlink(missing_ok=True)
            # Best-effort cleanup of empty parent dirs (stop at canonical root)
            parent = target.parent
            while parent != canonical and parent.is_dir():
                try:
                    parent.rmdir()
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
            gitignore.write_text(line + "\n")
            return
        existing = gitignore.read_text()
        existing_lines = {ln.strip() for ln in existing.splitlines()}
        if line in existing_lines:
            return
        sep = "" if existing.endswith("\n") else "\n"
        gitignore.write_text(existing + sep + line + "\n")

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
            gitignore.write_text(new_line + "\n")
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
        gitignore.write_text(body)
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
                shutil.rmtree(old_venv)
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
                old_abs.rmdir()
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
