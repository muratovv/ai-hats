"""Assembly engine — backup, clean, copy, update, rollback, verify."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .composer import Composer, CompositionResult, ResolvedComponent
from .library import LibraryResolver
from .models import (
    IMPORTS_ORDER_PRESETS,
    ComponentType,
    ProjectConfig,
    SkillMetadata,
)
from .paths import (
    hooks_dir as _lib_hooks_dir,
    legacy_paths_by_class,
    mcp_dir as _lib_mcp_dir,
    rules_dir as _lib_rules_dir,
    skills_dir as _lib_skills_dir,
)
from .providers import (
    INJECTION_END,
    INJECTION_START,
    PROVIDERS,
    PUBLISH_AGGREGATOR_END,
    PUBLISH_AGGREGATOR_START,
    Provider,
    _extract_frontmatter_description,
    get_provider,
)


AGENT_DIR = ".agent"
PROJECT_CONFIG = "ai-hats.yaml"
PROFILE_FILE = "profile.json"  # legacy, used only for backup compat
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


def _md_table_escape(text: str) -> str:
    """Escape pipe characters and collapse newlines for a single markdown table cell."""
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


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
        """Build ordered library paths (earlier = lower priority)."""
        paths: list[Path] = []

        # Built-in libraries (shipped with ai-hats package)
        builtin = Path(__file__).parent / "libraries"
        if builtin.is_dir():
            paths.append(builtin)

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
        return actions

    def init(
        self,
        role: str | None = None,
        provider: str | None = None,
        task_prefix: str | None = None,
    ) -> None:
        """Initialize project structure. Idempotent.

        Validates `role`, `provider`, and `task_prefix` before touching disk —
        unknown values raise ValueError with a helpful message, and no
        files/dirs are created. Re-running init with a `task_prefix` that
        conflicts with the value already in `ai-hats.yaml` is rejected.
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

        # HATS-312 / HATS-313 / HATS-314: all framework roots live under
        # <ai_hats_dir>/. .agent/ itself is no longer populated by ai-hats.
        from .paths import runs_dir, tasks_dir

        runs_dir(self.project_dir).mkdir(parents=True, exist_ok=True)
        tasks_dir(self.project_dir).mkdir(parents=True, exist_ok=True)
        for subdir_fn in (_lib_rules_dir, _lib_skills_dir, _lib_hooks_dir, _lib_mcp_dir):
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

        # Apply role if specified
        if role:
            self.set_role(role)

    def _get_overlay(self, role_name: str):
        """Get overlay for a role from project config, or None."""
        overlay = self.project_config.customizations.get(role_name)
        if overlay and overlay.is_empty:
            return None
        return overlay

    def set_role(self, role_name: str, provider_name: str | None = None) -> CompositionResult:
        """Apply a role to the project. Full assembly cycle.

        Fails loudly on unknown role/provider: the project must not end up in
        a half-applied state where the config is saved but no components are
        materialized.
        """
        # Validate before doing any work so we fail fast with a clear message.
        self._validate_role(role_name)
        if provider_name is not None:
            self._validate_provider(provider_name)

        provider = get_provider(provider_name or self.project_config.provider)
        result = self.composer.compose(role_name, overlay=self._get_overlay(role_name))

        if result.errors:
            # Non-fatal errors (e.g. missing optional rule) are still surfaced
            # to the caller via result.errors and do not abort assembly.
            pass

        # 0. HATS-285: legacy ./CLAUDE.md migration runs before backup.
        # No rollback needed if it fails — backup hasn't happened yet.
        self._migrate_claude_md_to_v3(provider)

        # 1. Backup
        backup_path = self._backup()

        try:
            # 2. Clean active directories (preserve project-local rules)
            self._clean(preserve_local=True)

            # 3. Copy components
            self._copy_components(result)

            # 3b. Install skill-contributed git hooks (HATS-088)
            self._install_git_hooks(result)

            # 3c. Write canonical layered layer (HATS-282)
            self.write_canonical(result)

            # 4. Export skills to provider-native directory
            provider.export_skills(self.project_dir, result.skills)

            # 4b. HATS-291 legacy cleanup — remove stale routing.md mirrors
            # left by HATS-264's lazy-publish path (now collapsed into
            # skills_index.md). Idempotent; can drop once everyone has
            # bumped past HATS-291.
            for prov_root in (".claude", ".gemini"):
                (self.project_dir / prov_root / "routing.md").unlink(missing_ok=True)

            # 5. Update system prompt — legacy inline block path. Providers
            # that declare a scaffold template (Claude — HATS-284) own
            # ./CLAUDE.md via canonical+aggregator (HATS-282/289); Gemini
            # still uses the inline path (non-goal of HATS-276).
            if provider.scaffold_template_relpath() is None:
                prompt_content = provider.build_system_prompt(result)
                provider.update_system_prompt(self.project_dir, prompt_content)

            # 6. Verify
            self._verify(result, provider)

            # 7. Update config (active_role + provider)
            self.project_config.active_role = role_name
            self.project_config.provider = provider.name
            self.project_config.save(self.config_path)

            # 8. Save backup reference
            self._save_backup_ref(backup_path)

            # HATS-317: gitignore handling moved to one-shot init.
            # set_role / bump never touch .gitignore.

        except Exception:
            # Rollback on failure
            if backup_path:
                self._restore_backup(backup_path)
            raise

        return result

    def rollback(self) -> bool:
        """Rollback to the last backup. Cleans up temp backup dir after restore."""
        ref_path = self._backup_ref_path()
        if not ref_path.exists():
            return False
        backup_path = Path(ref_path.read_text().strip())
        if not backup_path.exists():
            return False
        self._restore_backup(backup_path)
        shutil.rmtree(backup_path, ignore_errors=True)
        ref_path.unlink(missing_ok=True)
        return True

    def clean(self, provider_name: str | None = None) -> None:
        """Clean all active directories."""
        self._clean(preserve_local=False)
        # Clean provider-native skills
        pname = provider_name or self.project_config.provider
        provider = get_provider(pname)
        provider.cleanup_skills(self.project_dir)

    def status(self) -> dict:
        """Get current status: role, dependency tree, health."""
        cfg = self.project_config
        status = {
            "role": cfg.active_role,
            "provider": cfg.provider,
            "project_dir": str(self.project_dir),
            "library_paths": [str(p) for p in self.library_paths],
            "health": {},
            "tree": None,
        }

        if cfg.active_role:
            result = self.composer.compose(
                cfg.active_role,
                overlay=self._get_overlay(cfg.active_role),
            )
            status["tree"] = self._build_tree(result)
            status["health"] = self._check_health(result)
            status["errors"] = result.errors

        return status

    def bump(self) -> CompositionResult | None:
        """Re-apply current role (update to latest).

        HATS-285: cleanup obsolete files. HATS-289: also run scaffold/cleanup
        migration even when there's no active role, so legacy projects with
        a populated `./CLAUDE.md` but no `active_role` still get fixed up by
        a plain `ai-hats self bump`. HATS-312: also runs the v4 sessions-class
        layout migration so legacy paths (`.gitlog/pipeline_runs/`,
        `.agent/retrospectives/`, etc.) land under `<ai_hats_dir>/sessions/`.
        """
        self._cleanup_obsolete_files(self.project_dir)
        provider = get_provider(self.project_config.provider)
        self._migrate_claude_md_to_v3(provider)
        self._migrate_layout_v4_sessions()
        self._migrate_layout_v4_tracker()
        self._migrate_layout_v4_library()
        self._note_empty_legacy_agent_dir()
        if not self.project_config.active_role:
            return None
        return self.set_role(self.project_config.active_role, self.project_config.provider or None)

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
        import sys

        print(
            "NOTE: .agent/ holds only the managed ai-hats/ namespace; "
            "legacy top-level artefacts (rules/, skills/, hooks/, backlog/, "
            "STATE.md, ...) have migrated to <ai_hats_dir>/. If nothing "
            "else of yours lives in .agent/, the wrapper is no longer "
            "required — ai-hats will not remove it automatically.",
            file=sys.stderr,
        )

    def _migrate_layout_v4_library(self) -> None:
        """One-shot migration of library-mirror artefacts (HATS-314).

        Moves `.agent/{rules,skills,hooks,mcp}/` → `<ai_hats_dir>/library/...`.
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

    def _backup(self) -> Path | None:
        """Backup .agent/ to $TMPDIR."""
        if not self.agent_dir.exists():
            return None
        backup_dir = Path(tempfile.mkdtemp(prefix="ai-hats-backup-"))
        # symlinks=True copies links as links instead of dereferencing them.
        # Why: user-managed provider dirs (.gemini/skills, .claude/skills) can
        # contain self-referential or circular symlinks; dereferencing loops
        # until the OS raises ELOOP ("Too many levels of symbolic links").
        shutil.copytree(self.agent_dir, backup_dir / AGENT_DIR, symlinks=True)
        # Also backup system prompt files
        for name in ("GEMINI.md", "CLAUDE.md"):
            src = self.project_dir / name
            if src.exists():
                shutil.copy2(src, backup_dir / name)
        # Backup provider-native skills dirs only. HATS-289 collapsed the
        # canonical publish into `.agent/ai-hats/` (which is included via
        # the AGENT_DIR copytree above), so `.claude/` outside `skills/` is
        # no longer ai-hats-managed.
        for provider_dir in (".claude/skills", ".gemini/skills"):
            src = self.project_dir / provider_dir
            if src.exists():
                shutil.copytree(src, backup_dir / provider_dir, symlinks=True)
        # Backup ai-hats.yaml
        if self.config_path.exists():
            shutil.copy2(self.config_path, backup_dir / PROJECT_CONFIG)
        return backup_dir

    def _restore_backup(self, backup_path: Path) -> None:
        """Restore from backup."""
        agent_backup = backup_path / AGENT_DIR
        if agent_backup.exists():
            if self.agent_dir.exists():
                shutil.rmtree(self.agent_dir)
            shutil.copytree(agent_backup, self.agent_dir, symlinks=True)

        for name in ("GEMINI.md", "CLAUDE.md"):
            src = backup_path / name
            if src.exists():
                shutil.copy2(src, self.project_dir / name)

        # Restore provider-native skills dirs only (HATS-289).
        for provider_dir in (".claude/skills", ".gemini/skills"):
            src = backup_path / provider_dir
            dest = self.project_dir / provider_dir
            if src.exists():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest, symlinks=True)

        config_backup = backup_path / PROJECT_CONFIG
        if config_backup.exists():
            shutil.copy2(config_backup, self.config_path)
            self.project_config = ProjectConfig.from_yaml(self.config_path)

    def _save_backup_ref(self, backup_path: Path | None) -> None:
        ref = self._backup_ref_path()
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(str(backup_path) if backup_path else "")

    def _backup_ref_path(self) -> Path:
        from .paths import last_backup_path

        return last_backup_path(self.project_dir)

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

        for subdir in ("skills", "hooks", "mcp"):
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

    def _copy_components(self, result: CompositionResult) -> None:
        """Copy resolved components into .agent/."""
        # Copy rules
        rules_dir = _lib_rules_dir(self.project_dir)
        rules_dir.mkdir(parents=True, exist_ok=True)
        library_rule_names = []
        for rule in result.rules:
            dest = rules_dir / rule.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(rule.source_path, dest)
            library_rule_names.append(rule.name)

        # Track which rules came from library
        marker = rules_dir / ".library_rules"
        marker.write_text("\n".join(library_rule_names))

        # Copy skills
        skills_dir = _lib_skills_dir(self.project_dir)
        skills_dir.mkdir(parents=True, exist_ok=True)
        managed_skills: list[str] = []
        for skill in result.skills:
            dest = skills_dir / skill.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(skill.source_path, dest)
            managed_skills.append(skill.name)
        self._write_managed_manifest(skills_dir, managed_skills)

        # Copy hook scripts
        hooks_dir = _lib_hooks_dir(self.project_dir)
        hooks_dir.mkdir(parents=True, exist_ok=True)
        managed_hooks: list[str] = []
        for event_name in (
            "session_start",
            "session_end",
            "task_start",
            "task_complete",
            "task_failed",
            "error",
        ):
            scripts = getattr(result.hooks, event_name)
            for script in scripts:
                src = self._find_hook_script(script)
                if src and src.exists():
                    dest = hooks_dir / src.name
                    shutil.copy2(src, dest)
                    if src.name not in managed_hooks:
                        managed_hooks.append(src.name)
        self._write_managed_manifest(hooks_dir, managed_hooks)

        # Copy MCP configs
        mcp_dir = _lib_mcp_dir(self.project_dir)
        mcp_dir.mkdir(parents=True, exist_ok=True)
        managed_mcp: list[str] = []
        for mcp_config in result.mcp:
            src = self._find_mcp_config(mcp_config.config)
            if src and src.exists():
                shutil.copy2(src, mcp_dir / src.name)
                if src.name not in managed_mcp:
                    managed_mcp.append(src.name)
        self._write_managed_manifest(mcp_dir, managed_mcp)

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

    def _find_mcp_config(self, config_ref: str) -> Path | None:
        """Find an MCP config file across library paths."""
        config_path = Path(config_ref)
        if config_path.is_absolute() and config_path.exists():
            return config_path
        for lib_path in reversed(self.library_paths):
            candidate = lib_path / config_ref
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

    def _verify(self, result: CompositionResult, provider: Provider) -> None:
        """Verify assembly correctness."""
        # Check all rules copied
        for rule in result.rules:
            dest = _lib_rules_dir(self.project_dir) / rule.name
            if not dest.exists():
                raise AssemblyError(f"Rule '{rule.name}' not copied to {dest}")

        # Check all skills copied
        for skill in result.skills:
            dest = _lib_skills_dir(self.project_dir) / skill.name
            if not dest.exists():
                raise AssemblyError(f"Skill '{skill.name}' not copied to {dest}")

        # Check system prompt exists and has content
        prompt_path = provider.system_prompt_path(self.project_dir)
        if not prompt_path.exists():
            raise AssemblyError(f"System prompt not created at {prompt_path}")
        if not prompt_path.read_text().strip():
            raise AssemblyError("System prompt is empty")

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
            "mcp": [m.name for m in result.mcp],
            "injections_count": len(result.injections),
        }

    def _check_health(self, result: CompositionResult) -> dict[str, str]:
        """Check health of all components."""
        health: dict[str, str] = {}
        for rule in result.rules:
            dest = _lib_rules_dir(self.project_dir) / rule.name
            health[f"rule:{rule.name}"] = "OK" if dest.exists() else "Missing"
        for skill in result.skills:
            dest = _lib_skills_dir(self.project_dir) / skill.name
            health[f"skill:{skill.name}"] = "OK" if dest.exists() else "Missing"
        prompt_ok = any((self.project_dir / f).exists() for f in ("GEMINI.md", "CLAUDE.md"))
        health["system_prompt"] = "OK" if prompt_ok else "Missing"
        return health

    # ----- Canonical layered layer (HATS-282) -----

    @property
    def _canonical_dir(self) -> Path:
        return self.agent_dir / CANONICAL_DIR

    def write_canonical(self, result: CompositionResult) -> None:
        """Write the layered canonical view of `result` under .agent/ai-hats/.

        Idempotent: per-file bytes-compare avoids spurious mtime updates.
        Manifest-driven cleanup removes stale files from previous compositions
        but never touches `user-rules/`.
        """
        canonical = self._canonical_dir
        canonical.mkdir(parents=True, exist_ok=True)
        (canonical / USER_RULES_SUBDIR).mkdir(exist_ok=True)

        targets: dict[str, bytes] = {}

        priorities = self._render_priorities(result.priorities)
        if priorities is not None:
            targets["priorities.md"] = priorities.encode()

        role_doc = self._render_role(result.role_injection, result.overlay_injection)
        if role_doc is not None:
            targets["role.md"] = role_doc.encode()

        for trait_name, text in result.trait_injections.items():
            if not text:
                continue
            targets[f"traits/{trait_name}.md"] = self._ensure_trailing_newline(text).encode()

        for rule in result.rules:
            if not rule.injection:
                continue
            targets[f"rules/{rule.name}.md"] = self._ensure_trailing_newline(
                rule.injection
            ).encode()

        # HATS-264 + HATS-291: skills_index.md is the single always-on file
        # for skill discovery. It now embeds the trigger→skill routing table
        # (and optional skip table) generated from each skill's
        # metadata.yaml — so trigger phrases are visible to the agent in the
        # imports.md aggregator without a separate lazy-loaded artefact.
        skills_index = self._render_skills_index(result.skills)
        if skills_index is not None:
            targets["skills_index.md"] = skills_index.encode()

        # HATS-289: aggregator-in-canonical. Imports every other file in
        # deterministic order so `./CLAUDE.md` can `@import` a single stable
        # entry-point. Computed before write so the aggregator can include
        # user-rules/ files (which are NOT in `targets` — they live outside
        # the manifest by design).
        # Pass the ordered keys (insertion order = composition order from the
        # composer) so the aggregator preserves general-to-specific ordering
        # rather than re-sorting alphabetically.
        targets["imports.md"] = self._render_canonical_aggregator(
            canonical,
            list(targets.keys()),
            order=self._resolve_imports_order(self.project_config.imports_order),
        ).encode()

        # Idempotent write
        for relpath, content in targets.items():
            self._atomic_write_if_changed(canonical / relpath, content)

        # Stale cleanup: remove previous-managed files no longer present.
        previous = self._read_canonical_manifest(canonical / CANONICAL_MANIFEST)
        new_paths = set(targets.keys())
        for stale in previous - new_paths:
            if stale.startswith(f"{USER_RULES_SUBDIR}/") or stale == USER_RULES_SUBDIR:
                continue  # never touch user-rules/
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

        # Write the new manifest.
        self._write_canonical_manifest(canonical / CANONICAL_MANIFEST, sorted(new_paths))

    @staticmethod
    def _render_priorities(priorities: list[str]) -> str | None:
        if not priorities:
            return None
        body = "\n".join(f"{i}. {p}" for i, p in enumerate(priorities, start=1))
        return f"# Priorities\n\n{body}\n"

    @staticmethod
    def _render_role(role_text: str, overlay_text: str) -> str | None:
        parts = [t for t in (role_text, overlay_text) if t]
        if not parts:
            return None
        return Assembler._ensure_trailing_newline("\n\n".join(parts))

    # HATS-290: outer-section bucket predicates for imports.md.
    # Maps each canonical section name to a path-predicate; user-rules is
    # handled separately because its files live outside the manifest.
    _SECTION_BUCKETS: dict[str, "callable[[str], bool]"] = {
        "priorities":   lambda p: p == "priorities.md",
        "traits":       lambda p: p.startswith("traits/"),
        "role":         lambda p: p == "role.md",
        "rules":        lambda p: p.startswith("rules/"),
        "skills_index": lambda p: p == "skills_index.md",
    }

    @staticmethod
    def _resolve_imports_order(
        config_value: str | list[str] | None,
    ) -> list[str]:
        """Resolve `imports_order` config to a concrete section ordering.

        None / "default" → the default preset. A preset name → the preset's list.
        A list[str] → returned as-is (already validated to be a permutation of
        IMPORTS_SECTION_NAMES at config-load time).
        """
        if config_value is None:
            return list(IMPORTS_ORDER_PRESETS["default"])
        if isinstance(config_value, str):
            return list(IMPORTS_ORDER_PRESETS[config_value])
        return list(config_value)

    @staticmethod
    def _render_canonical_aggregator(
        canonical_dir: Path,
        framework_paths: list[str],
        order: list[str] | None = None,
    ) -> str:
        """Build the `imports.md` aggregator in canonical (HATS-289 + HATS-290).

        Pure `@import` list, no markers, no headings. Outer order is driven by
        `order` — a list of section names from IMPORTS_SECTION_NAMES. When
        `order` is None, the "default" preset is used (preserves the original
        priorities → traits → role → rules → user-rules → skills_index).

        Within each section, paths preserve their **insertion order** in
        `framework_paths` (= composition / resolution order from the composer
        — general traits before specific ones, depth-first across the
        dependency tree). Alphabetical sort would scramble that.

        `framework_paths` is the ordered list of relpaths the canonical writer
        will own; `user-rules/*.md` is enumerated separately because user-rules
        live outside the manifest (composer never writes them).
        """
        if order is None:
            order = list(IMPORTS_ORDER_PRESETS["default"])

        sections: list[list[str]] = []
        for name in order:
            if name == "user-rules":
                user_rules_dir = canonical_dir / USER_RULES_SUBDIR
                if user_rules_dir.is_dir():
                    user_paths = sorted(
                        f"@./{USER_RULES_SUBDIR}/{md.name}"
                        for md in user_rules_dir.glob("*.md")
                    )
                    if user_paths:
                        sections.append(user_paths)
                continue
            predicate = Assembler._SECTION_BUCKETS[name]
            picked = [p for p in framework_paths if predicate(p)]
            if picked:
                sections.append([f"@./{p}" for p in picked])

        body = "\n\n".join("\n".join(lines) for lines in sections)
        return body + ("\n" if body else "")

    @staticmethod
    def _render_skills_index(skills: list[ResolvedComponent]) -> str | None:
        """Render `skills_index.md` — the single always-on skill-discovery file.

        Top section: one-liner per skill (name + frontmatter description).
        Bottom sections (HATS-264 + HATS-291): a `## Routing` table mapping
        trigger phrases to skills, and an optional `## Skip` table — both
        sourced from each skill's `metadata.yaml` (`triggers:` / `skip:`).
        Skills with empty triggers are absent from the routing table but
        still appear in the top index.

        The routing tables live here (rather than in a separate lazy file)
        so the agent sees activation hints directly in the always-on
        imports.md aggregator — closing the chicken-and-egg gap that an
        on-demand routing.md left open.
        """
        if not skills:
            return None
        lines = ["# Skills Index", ""]
        for skill in skills:
            desc = _extract_frontmatter_description(skill)
            # Fall back to empty when description equals the name (no frontmatter).
            if desc == skill.name:
                lines.append(f"- **{skill.name}**")
            else:
                lines.append(f"- **{skill.name}** — {desc}")

        trigger_rows: list[tuple[str, str]] = []  # (trigger, skill_name)
        skip_rows: list[tuple[str, str]] = []  # (skill_name, skip_phrase)
        for skill in skills:
            metadata = SkillMetadata.from_yaml(skill.source_path / "metadata.yaml")
            for trigger in metadata.triggers:
                phrase = str(trigger).strip()
                if phrase:
                    trigger_rows.append((phrase, skill.name))
            for skip in metadata.skip:
                phrase = str(skip).strip()
                if phrase:
                    skip_rows.append((skill.name, phrase))

        if trigger_rows:
            lines.append("")
            lines.append("## Routing")
            lines.append("")
            lines.append("| Trigger | Skill |")
            lines.append("|---------|-------|")
            for trigger, skill_name in trigger_rows:
                lines.append(f"| {_md_table_escape(trigger)} | {skill_name} |")
        if skip_rows:
            lines.append("")
            lines.append("## Skip")
            lines.append("")
            lines.append("| Skill | Skip when |")
            lines.append("|-------|-----------|")
            for skill_name, phrase in skip_rows:
                lines.append(f"| {skill_name} | {_md_table_escape(phrase)} |")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _ensure_trailing_newline(text: str) -> str:
        return text if text.endswith("\n") else text + "\n"

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


class AssemblyError(Exception):
    pass
