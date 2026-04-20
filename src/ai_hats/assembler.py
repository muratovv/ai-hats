"""Assembly engine — backup, clean, copy, update, rollback, verify."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .composer import Composer, CompositionResult
from .library import LibraryResolver
from .models import ProjectConfig, SkillMetadata
from .providers import Provider, get_provider


AGENT_DIR = ".agent"
GITLOG_DIR = ".gitlog"
PROJECT_CONFIG = "ai-hats.yaml"
PROFILE_FILE = "profile.json"  # legacy, used only for backup compat
GITHOOKS_DIR = ".githooks"
GITHOOKS_MANIFEST = ".ai-hats-manifest"
GITHOOKS_DISPATCHER_MARKER = "AI-HATS-DISPATCHER-MARKER"
GITHOOKS_DISPATCHER_TEMPLATE = (
    Path(__file__).parent / "templates" / "githooks" / "dispatcher.sh"
)
GITIGNORE_FILE = ".gitignore"
GITIGNORE_START = "# AI-HATS:START — managed by ai-hats, do not edit"
GITIGNORE_END = "# AI-HATS:END"
LIBRARY_RULES_MARKER = ".library_rules"
MANAGED_SKILLS_MARKER = ".ai-hats-managed"


class Assembler:
    """Manages the lifecycle of role assembly in a project directory."""

    def __init__(self, project_dir: Path, library_paths: list[Path] | None = None) -> None:
        self.project_dir = project_dir
        self.agent_dir = project_dir / AGENT_DIR
        self.gitlog_dir = project_dir / GITLOG_DIR
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

    def init(self, role: str | None = None, provider: str | None = None) -> None:
        """Initialize project structure. Idempotent."""
        # Create .agent/ subdirectories
        for subdir in ("rules", "skills", "hooks", "mcp", "backlog/tasks"):
            (self.agent_dir / subdir).mkdir(parents=True, exist_ok=True)

        # Create .gitlog/
        self.gitlog_dir.mkdir(parents=True, exist_ok=True)

        # Create/update ai-hats.yaml
        if not self.config_path.exists():
            self.project_config.provider = provider or "gemini"
            if role:
                self.project_config.default_role = role
            self.project_config.save(self.config_path)
        elif provider:
            self.project_config.provider = provider
            self.project_config.save(self.config_path)

        # Create STATE.md
        state_md = self.agent_dir / "STATE.md"
        if not state_md.exists():
            state_md.write_text("# Task State\n\nNo active tasks.\n")

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
        """Apply a role to the project. Full assembly cycle."""
        provider = get_provider(provider_name or self.project_config.provider)
        result = self.composer.compose(role_name, overlay=self._get_overlay(role_name))

        if result.errors:
            # Still proceed with what we have, but report errors
            pass

        # 1. Backup
        backup_path = self._backup()

        try:
            # 2. Clean active directories (preserve project-local rules)
            self._clean(preserve_local=True)

            # 3. Copy components
            self._copy_components(result)

            # 3b. Install skill-contributed git hooks (HATS-088)
            self._install_git_hooks(result)

            # 4. Export skills to provider-native directory
            provider.export_skills(self.project_dir, result.skills)

            # 5. Update system prompt
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

            # 9. Update .gitignore managed block (HATS-141)
            self._update_gitignore()

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
                cfg.active_role, overlay=self._get_overlay(cfg.active_role),
            )
            status["tree"] = self._build_tree(result)
            status["health"] = self._check_health(result)
            status["errors"] = result.errors

        return status

    def bump(self) -> CompositionResult | None:
        """Re-apply current role (update to latest)."""
        if not self.project_config.active_role:
            return None
        return self.set_role(self.project_config.active_role, self.project_config.provider or None)

    def whoami(self) -> dict:
        """Diagnostic info about current session."""
        return {
            "role": self.project_config.active_role,
            "provider": self.project_config.provider,
            "project_dir": str(self.project_dir),
            "schema_version": self.project_config.schema_version,
        }

    # -- Internal methods --

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
        # Backup provider-native skills
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

        # Restore provider-native skills
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
        return self.agent_dir / ".last_backup"

    def _clean(self, *, preserve_local: bool = False) -> None:
        """Clean active directories."""
        for subdir in ("rules", "skills", "hooks", "mcp"):
            target = self.agent_dir / subdir
            if not target.exists():
                continue

            if preserve_local and subdir == "rules":
                # Keep project-local rules (not from library)
                self._clean_non_local(target)
            else:
                shutil.rmtree(target)
                target.mkdir(parents=True, exist_ok=True)

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

    def _copy_components(self, result: CompositionResult) -> None:
        """Copy resolved components into .agent/."""
        # Copy rules
        rules_dir = self.agent_dir / "rules"
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
        skills_dir = self.agent_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        for skill in result.skills:
            dest = skills_dir / skill.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(skill.source_path, dest)

        # Copy hook scripts
        hooks_dir = self.agent_dir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        for event_name in (
            "session_start", "session_end", "task_start",
            "task_complete", "task_failed", "error",
        ):
            scripts = getattr(result.hooks, event_name)
            for script in scripts:
                src = self._find_hook_script(script)
                if src and src.exists():
                    dest = hooks_dir / src.name
                    shutil.copy2(src, dest)

        # Copy MCP configs
        mcp_dir = self.agent_dir / "mcp"
        mcp_dir.mkdir(parents=True, exist_ok=True)
        for mcp_config in result.mcp:
            src = self._find_mcp_config(mcp_config.config)
            if src and src.exists():
                shutil.copy2(src, mcp_dir / src.name)

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
        self, result: CompositionResult,
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
                collected.setdefault(event, []).extend(
                    (skill.name, script) for script in scripts
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
            warnings.append(
                f"failed to set core.hooksPath: {e.stderr.strip() or e}"
            )

    def _verify(self, result: CompositionResult, provider: Provider) -> None:
        """Verify assembly correctness."""
        # Check all rules copied
        for rule in result.rules:
            dest = self.agent_dir / "rules" / rule.name
            if not dest.exists():
                raise AssemblyError(f"Rule '{rule.name}' not copied to {dest}")

        # Check all skills copied
        for skill in result.skills:
            dest = self.agent_dir / "skills" / skill.name
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
                    "session_start", "session_end", "task_start",
                    "task_complete", "task_failed", "error",
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
            dest = self.agent_dir / "rules" / rule.name
            health[f"rule:{rule.name}"] = "OK" if dest.exists() else "Missing"
        for skill in result.skills:
            dest = self.agent_dir / "skills" / skill.name
            health[f"skill:{skill.name}"] = "OK" if dest.exists() else "Missing"
        prompt_ok = any(
            (self.project_dir / f).exists()
            for f in ("GEMINI.md", "CLAUDE.md")
        )
        health["system_prompt"] = "OK" if prompt_ok else "Missing"
        return health

    # ----- .gitignore management (HATS-141) -----

    def _update_gitignore(self) -> None:
        """Write or remove the AI-HATS managed block in .gitignore.

        Managed paths are derived entirely from marker files written by other
        steps (.library_rules, .ai-hats-managed, .ai-hats-manifest), so the
        block self-heals across role switches — stale entries drop out when
        a component leaves the composition.
        """
        gitignore = self.project_dir / GITIGNORE_FILE
        if not self.project_config.manage_gitignore:
            self._remove_gitignore_block(gitignore)
            return
        paths = self._collect_managed_paths()
        self._write_gitignore_block(gitignore, paths)

    def _collect_managed_paths(self) -> list[str]:
        """Enumerate ai-hats-owned file paths, sorted for stable diffs."""
        paths: set[str] = {
            f"{AGENT_DIR}/.last_backup",
            f"{AGENT_DIR}/hooks/",
            f"{AGENT_DIR}/mcp/",
            f"{AGENT_DIR}/skills/",
        }

        lib_rules = self.agent_dir / "rules" / LIBRARY_RULES_MARKER
        if lib_rules.exists():
            paths.add(f"{AGENT_DIR}/rules/{LIBRARY_RULES_MARKER}")
            for name in lib_rules.read_text().splitlines():
                name = name.strip()
                if name:
                    paths.add(f"{AGENT_DIR}/rules/{name}/")

        for prov_dir in (".claude/skills", ".gemini/skills"):
            marker = self.project_dir / prov_dir / MANAGED_SKILLS_MARKER
            if marker.exists():
                paths.add(f"{prov_dir}/{MANAGED_SKILLS_MARKER}")
                for name in marker.read_text().splitlines():
                    name = name.strip()
                    if name:
                        paths.add(f"{prov_dir}/{name}/")

        manifest = self.project_dir / GITHOOKS_DIR / GITHOOKS_MANIFEST
        if manifest.exists():
            paths.add(f"{GITHOOKS_DIR}/{GITHOOKS_MANIFEST}")
            for entry in manifest.read_text().splitlines():
                entry = entry.strip()
                if entry:
                    paths.add(f"{GITHOOKS_DIR}/{entry}")

        return sorted(paths)

    @staticmethod
    def _render_block(paths: list[str]) -> str:
        body = "\n".join(paths)
        return f"{GITIGNORE_START}\n{body}\n{GITIGNORE_END}\n"

    def _write_gitignore_block(self, gitignore: Path, paths: list[str]) -> None:
        """Create/update the managed block, preserving user-authored content."""
        block = self._render_block(paths)
        if not gitignore.exists():
            gitignore.write_text(block)
            return
        existing = gitignore.read_text()
        if GITIGNORE_START in existing and GITIGNORE_END in existing:
            start = existing.index(GITIGNORE_START)
            end = existing.index(GITIGNORE_END) + len(GITIGNORE_END)
            # Consume trailing newline after the end marker so we don't
            # accumulate blanks across re-runs.
            if end < len(existing) and existing[end] == "\n":
                end += 1
            new_content = existing[:start] + block + existing[end:]
            if new_content != existing:
                gitignore.write_text(new_content)
            return
        # Markers absent → append with a separating blank line if needed.
        sep = "" if existing.endswith("\n") else "\n"
        gitignore.write_text(existing + sep + "\n" + block)

    def _remove_gitignore_block(self, gitignore: Path) -> None:
        """Strip the managed block if present. Leaves user content untouched."""
        if not gitignore.exists():
            return
        existing = gitignore.read_text()
        if GITIGNORE_START not in existing or GITIGNORE_END not in existing:
            return
        start = existing.index(GITIGNORE_START)
        end = existing.index(GITIGNORE_END) + len(GITIGNORE_END)
        if end < len(existing) and existing[end] == "\n":
            end += 1
        new_content = existing[:start] + existing[end:]
        gitignore.write_text(new_content)


class AssemblyError(Exception):
    pass
