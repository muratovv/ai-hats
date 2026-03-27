"""Assembly engine — backup, clean, copy, update, rollback, verify."""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .composer import Composer, CompositionResult
from .library import LibraryResolver
from .models import ProfileConfig, ProjectConfig
from .providers import Provider, get_provider


AGENT_DIR = ".agent"
GITLOG_DIR = ".gitlog"
PROJECT_CONFIG = "ai-hats.yaml"
PROFILE_FILE = "profile.json"


class Assembler:
    """Manages the lifecycle of role assembly in a project directory."""

    def __init__(self, project_dir: Path, library_paths: list[Path] | None = None) -> None:
        self.project_dir = project_dir
        self.agent_dir = project_dir / AGENT_DIR
        self.gitlog_dir = project_dir / GITLOG_DIR
        self.config_path = project_dir / PROJECT_CONFIG
        self.profile_path = project_dir / PROFILE_FILE
        self.project_config = ProjectConfig.from_yaml(self.config_path)

        # Build library paths: built-in + project config + explicit
        self.library_paths = self._build_library_paths(library_paths or [])
        self.resolver = LibraryResolver(self.library_paths)
        self.composer = Composer(self.resolver)

    def _build_library_paths(self, extra: list[Path]) -> list[Path]:
        """Build ordered library paths (earlier = lower priority)."""
        paths: list[Path] = []

        # Built-in libraries (shipped with ai-hats)
        builtin = Path(__file__).parent.parent.parent / "libraries"
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

        # Create profile.json
        if not self.profile_path.exists():
            ProfileConfig().save(self.profile_path)

        # Create STATE.md
        state_md = self.agent_dir / "STATE.md"
        if not state_md.exists():
            state_md.write_text("# Task State\n\nNo active tasks.\n")

        # Apply role if specified
        if role:
            self.set_role(role)

    def set_role(self, role_name: str, provider_name: str | None = None) -> CompositionResult:
        """Apply a role to the project. Full assembly cycle."""
        provider = get_provider(provider_name or self.project_config.provider)
        result = self.composer.compose(role_name)

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

            # 4. Update system prompt
            prompt_content = provider.build_system_prompt(result)
            provider.update_system_prompt(self.project_dir, prompt_content)

            # 5. Verify
            self._verify(result, provider)

            # 6. Update profile
            profile = ProfileConfig(active_role=role_name, provider=provider.name)
            profile.save(self.profile_path)

            # 7. Save backup reference
            self._save_backup_ref(backup_path)

        except Exception:
            # Rollback on failure
            if backup_path:
                self._restore_backup(backup_path)
            raise

        return result

    def rollback(self) -> bool:
        """Rollback to the last backup."""
        ref_path = self._backup_ref_path()
        if not ref_path.exists():
            return False
        backup_path = Path(ref_path.read_text().strip())
        if not backup_path.exists():
            return False
        self._restore_backup(backup_path)
        return True

    def clean(self) -> None:
        """Clean all active directories."""
        self._clean(preserve_local=False)

    def status(self) -> dict:
        """Get current status: role, dependency tree, health."""
        profile = ProfileConfig.load(self.profile_path)
        status = {
            "role": profile.active_role,
            "provider": profile.provider or self.project_config.provider,
            "project_dir": str(self.project_dir),
            "library_paths": [str(p) for p in self.library_paths],
            "health": {},
            "tree": None,
        }

        if profile.active_role:
            result = self.composer.compose(profile.active_role)
            status["tree"] = self._build_tree(result)
            status["health"] = self._check_health(result)
            status["errors"] = result.errors

        return status

    def bump(self) -> CompositionResult | None:
        """Re-apply current role (update to latest)."""
        profile = ProfileConfig.load(self.profile_path)
        if not profile.active_role:
            return None
        return self.set_role(profile.active_role, profile.provider or None)

    def whoami(self) -> dict:
        """Diagnostic info about current session."""
        profile = ProfileConfig.load(self.profile_path)
        return {
            "role": profile.active_role,
            "provider": profile.provider or self.project_config.provider,
            "project_dir": str(self.project_dir),
            "schema_version": self.project_config.schema_version,
        }

    # -- Internal methods --

    def _backup(self) -> Path | None:
        """Backup .agent/ to $TMPDIR."""
        if not self.agent_dir.exists():
            return None
        backup_dir = Path(tempfile.mkdtemp(prefix="ai-hats-backup-"))
        shutil.copytree(self.agent_dir, backup_dir / AGENT_DIR)
        # Also backup system prompt files
        for name in ("GEMINI.md", "CLAUDE.md"):
            src = self.project_dir / name
            if src.exists():
                shutil.copy2(src, backup_dir / name)
        # Backup profile
        if self.profile_path.exists():
            shutil.copy2(self.profile_path, backup_dir / PROFILE_FILE)
        return backup_dir

    def _restore_backup(self, backup_path: Path) -> None:
        """Restore from backup."""
        agent_backup = backup_path / AGENT_DIR
        if agent_backup.exists():
            if self.agent_dir.exists():
                shutil.rmtree(self.agent_dir)
            shutil.copytree(agent_backup, self.agent_dir)

        for name in ("GEMINI.md", "CLAUDE.md"):
            src = backup_path / name
            if src.exists():
                shutil.copy2(src, self.project_dir / name)

        profile_backup = backup_path / PROFILE_FILE
        if profile_backup.exists():
            shutil.copy2(profile_backup, self.profile_path)

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
            library_rules = set(marker_file.read_text().strip().split("\n"))
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


class AssemblyError(Exception):
    pass
