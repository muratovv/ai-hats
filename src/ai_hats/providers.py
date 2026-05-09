"""Provider abstraction — adapters for Gemini and Claude CLI."""

from __future__ import annotations

import abc
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .composer import CompositionResult, ResolvedComponent

if TYPE_CHECKING:
    from .observe import SessionManager


INJECTION_START = "<!-- AI-HATS:START -->"
INJECTION_END = "<!-- AI-HATS:END -->"

# HATS-284: lowercase scaffold markers — used in `./CLAUDE.md` to delimit the
# user-owned ai-hats block. PUBLISH_AGGREGATOR_* names kept for backwards
# compatibility of imports across the codebase; functionally these are the
# scaffold markers.
PUBLISH_AGGREGATOR_START = "<!-- ai-hats:start -->"
PUBLISH_AGGREGATOR_END = "<!-- ai-hats:end -->"

# Always-on rules that stay in prompt (safety-critical)
ALWAYS_ON_RULES = {
    "global_rule_destructive_actions",
    "global_rule_resource_hygiene",
    "dev_rule_secure_coding",
    "dev_rule_tool_call_hygiene",
}


def _extract_frontmatter_description(skill: ResolvedComponent) -> str:
    """Extract description from SKILL.md YAML frontmatter."""
    skill_md = skill.source_path / "SKILL.md"
    if not skill_md.exists():
        return skill.name
    text = skill_md.read_text()
    if not text.startswith("---"):
        return skill.name
    end = text.find("---", 3)
    if end == -1:
        return skill.name
    for line in text[3:end].splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    return skill.name


class Provider(abc.ABC):
    """Abstract provider interface."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    def system_prompt_path(self, project_dir: Path) -> Path:
        """Path to the system prompt file for this provider."""

    @abc.abstractmethod
    def rules_dir(self, session_dir: Path) -> Path:
        """Directory where rules files should be placed."""

    @abc.abstractmethod
    def build_system_prompt(self, result: CompositionResult) -> str:
        """Build the complete system prompt from composition result."""

    @abc.abstractmethod
    def inject_rules(self, session_dir: Path, rules: list[ResolvedComponent]) -> None:
        """Copy rule files into the provider's expected location."""

    @abc.abstractmethod
    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        """Get the CLI command to launch this provider."""

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
        *,
        model: str | None = None,
    ) -> list[str]:
        """Build a non-interactive command that runs ``meta_prompt`` through this provider.

        Default: return ``cmd`` unchanged. Subclasses tailor the invocation
        to their CLI (e.g. Claude needs ``--print -p``, Gemini needs ``-p``).
        ``model`` is an optional explicit model name; when None, the provider
        CLI's default applies (back-compat).
        """
        return cmd

    @abc.abstractmethod
    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        """Get environment variables needed for the provider."""

    def build_override(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_mgr: "SessionManager",
    ) -> tuple[list[str], dict[str, str]]:
        """Build CLI args and env vars for a temporary role override.

        Returns (extra_args, extra_env). Must NOT modify project files.
        Default: no-op (subclasses override).
        """
        return [], {}

    @abc.abstractmethod
    def skills_export_dir(self, project_dir: Path) -> Path:
        """Provider-native skills directory."""

    _MANAGED_MARKER = ".ai-hats-managed"

    def export_skills(self, project_dir: Path, skills: list[ResolvedComponent]) -> None:
        """Copy skills to provider-native directory for /skills discovery."""
        target_dir = self.skills_export_dir(project_dir)
        self._clean_managed_skills(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        managed_names: list[str] = []
        for skill in skills:
            if skill.source_path.is_dir():
                dest = target_dir / skill.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(skill.source_path, dest)
                managed_names.append(skill.name)

        # Track which skills we own
        (target_dir / self._MANAGED_MARKER).write_text("\n".join(managed_names) + "\n")

    def cleanup_skills(self, project_dir: Path) -> None:
        """Remove only ai-hats-managed skills, keep user-created ones."""
        target_dir = self.skills_export_dir(project_dir)
        self._clean_managed_skills(target_dir)

    def routing_export_path(self, project_dir: Path) -> Path:
        """Path where the lazy-loaded `routing.md` is published (HATS-264)."""
        return self.skills_export_dir(project_dir).parent / "routing.md"

    def export_routing(self, project_dir: Path, canonical_dir: Path) -> None:
        """Mirror canonical `routing.md` into the provider directory.

        Lazy-loaded artefact (HATS-264): published next to `skills/` so a
        provider-native agent can read it on demand. When the canonical
        source is absent (no skill carries `triggers:`), the target file is
        removed to keep state consistent.
        """
        target = self.routing_export_path(project_dir)
        source = canonical_dir / "routing.md"
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        else:
            target.unlink(missing_ok=True)

    def cleanup_routing(self, project_dir: Path) -> None:
        """Remove the published `routing.md`, if present."""
        self.routing_export_path(project_dir).unlink(missing_ok=True)

    def _clean_managed_skills(self, target_dir: Path) -> None:
        """Remove skills tracked by the managed marker."""
        marker = target_dir / self._MANAGED_MARKER
        if not marker.exists():
            return
        for name in marker.read_text().strip().splitlines():
            skill_path = target_dir / name
            if skill_path.exists():
                shutil.rmtree(skill_path)
        marker.unlink()

    def scaffold_template_relpath(self) -> str | None:
        """Library-relative path to the provider's prompt-file scaffold template.

        Default: None — provider has no scaffold (e.g. Gemini per HATS-276).
        Subclasses point at a markdown asset under
        `libraries/templates/<provider>/...`.
        """
        return None

    def update_system_prompt(self, project_dir: Path, content: str) -> None:
        """Write or update the inline system prompt block.

        Used by providers without a scaffold (e.g. Gemini) to maintain the
        AI-HATS-managed section of `./GEMINI.md` between `INJECTION_START` /
        `INJECTION_END` markers. For providers that declare a scaffold
        (Claude — HATS-284), this method is dormant: `Assembler.set_role`
        skips the call entirely (HATS-286), and the lowercase-marker early
        return below provides a defense-in-depth no-op if it is invoked
        anyway.
        """
        prompt_path = self.system_prompt_path(project_dir)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)

        if prompt_path.exists():
            existing = prompt_path.read_text()
            # HATS-284: lowercase scaffold markers signal the project is on
            # the canonical-publish layout — `./CLAUDE.md` is user-owned and
            # the framework injection lives in `.claude/CLAUDE.md`.
            if PUBLISH_AGGREGATOR_START in existing and PUBLISH_AGGREGATOR_END in existing:
                return
            if INJECTION_START in existing and INJECTION_END in existing:
                # Update between markers, preserve everything outside
                before = existing[: existing.index(INJECTION_START)]
                after = existing[existing.index(INJECTION_END) + len(INJECTION_END) :]
                new_content = f"{before}{INJECTION_START}\n{content}\n{INJECTION_END}{after}"
                prompt_path.write_text(new_content)
                return
            if existing.strip():
                # Existing file without markers — preserve as project context
                prompt_path.write_text(
                    f"{INJECTION_START}\n{content}\n{INJECTION_END}\n\n{existing}"
                )
                return

        # Fresh write with markers
        prompt_path.write_text(f"{INJECTION_START}\n{content}\n{INJECTION_END}\n")


class GeminiProvider(Provider):
    @property
    def name(self) -> str:
        return "gemini"

    def system_prompt_path(self, project_dir: Path) -> Path:
        return project_dir / "GEMINI.md"

    def skills_export_dir(self, project_dir: Path) -> Path:
        return project_dir / ".gemini" / "skills"

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        sections = []

        if result.priorities:
            sections.append(
                "## PRIORITIES\n"
                + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(result.priorities))
            )

        if result.merged_injection:
            sections.append(result.merged_injection)

        # Only always-on rules in prompt
        always_on = [r for r in result.rules if r.name in ALWAYS_ON_RULES]
        if always_on:
            rules_section = "## RULES\n"
            for rule in always_on:
                if rule.injection:
                    rules_section += f"\n### {rule.name}\n{rule.injection}\n"
            sections.append(rules_section)

        # Skills: index only (body loaded on demand via native provider)
        if result.skills:
            lines = ["## AVAILABLE SKILLS\n"]
            for skill in result.skills:
                desc = _extract_frontmatter_description(skill)
                lines.append(f"- **{skill.name}** — {desc}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def inject_rules(self, session_dir: Path, rules: list[ResolvedComponent]) -> None:
        rules_dir = self.rules_dir(session_dir)
        rules_dir.mkdir(parents=True, exist_ok=True)
        for rule in rules:
            if rule.source_path.is_dir():
                dest = rules_dir / rule.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(rule.source_path, dest)

    def build_override(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_mgr: "SessionManager",
    ) -> tuple[list[str], dict[str, str]]:
        """Create session-scoped rules dir with override prompt.

        Uses GEMINI_CLI_PROJECT_RULES_PATH to inject without touching GEMINI.md.
        """
        prompt_content = self.build_system_prompt(result)

        # Create isolated rules dir in temp
        rules_dir = Path(tempfile.mkdtemp(prefix="ai-hats-override-rules-"))

        # Copy existing project rules
        project_rules = project_dir / ".agent" / "rules"
        if project_rules.exists():
            for item in project_rules.iterdir():
                if item.is_dir():
                    shutil.copytree(item, rules_dir / item.name)

        # Write mandatory role override (00_ prefix = highest priority)
        (rules_dir / "00_MANDATORY_ROLE.md").write_text(prompt_content)

        return [], {"GEMINI_CLI_PROJECT_RULES_PATH": str(rules_dir)}

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["gemini"]
        if args:
            cmd.extend(args)
        return cmd

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
        *,
        model: str | None = None,
    ) -> list[str]:
        extra = ["--model", model] if model else []
        return cmd + extra + ["-p", meta_prompt]

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        return {
            "GEMINI_CLI_PROJECT_RULES_PATH": str(self.rules_dir(session_dir)),
        }


class ClaudeProvider(Provider):
    @property
    def name(self) -> str:
        return "claude"

    def system_prompt_path(self, project_dir: Path) -> Path:
        return project_dir / "CLAUDE.md"

    def skills_export_dir(self, project_dir: Path) -> Path:
        return project_dir / ".claude" / "skills"

    def scaffold_template_relpath(self) -> str | None:
        return "templates/claude/CLAUDE.md.template"

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        sections = []

        if result.priorities:
            sections.append(
                "## PRIORITIES\n"
                + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(result.priorities))
            )

        if result.merged_injection:
            sections.append(result.merged_injection)

        # Only always-on rules in prompt
        always_on = [r for r in result.rules if r.name in ALWAYS_ON_RULES]
        if always_on:
            rules_section = "## RULES\n"
            for rule in always_on:
                if rule.injection:
                    rules_section += f"\n### {rule.name}\n{rule.injection}\n"
            sections.append(rules_section)

        # Skills: index only (body loaded on demand via native provider)
        if result.skills:
            lines = ["## AVAILABLE SKILLS\n"]
            for skill in result.skills:
                desc = _extract_frontmatter_description(skill)
                lines.append(f"- **{skill.name}** — {desc}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def inject_rules(self, session_dir: Path, rules: list[ResolvedComponent]) -> None:
        rules_dir = self.rules_dir(session_dir)
        rules_dir.mkdir(parents=True, exist_ok=True)
        for rule in rules:
            if rule.source_path.is_dir():
                dest = rules_dir / rule.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(rule.source_path, dest)

    def build_override(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_mgr: "SessionManager",
    ) -> tuple[list[str], dict[str, str]]:
        """Write override prompt to temp file, pass via --system-prompt-file.

        Preserves project-local content outside AI-HATS markers.
        """
        prompt_content = self.build_system_prompt(result)

        # Build full file content preserving project-local sections.
        # HATS-285: handle both legacy uppercase markers and the new lowercase
        # scaffold (which contains an @-import line that we replace with inline
        # override content for the duration of the session).
        existing_path = self.system_prompt_path(project_dir)
        if existing_path.exists():
            existing = existing_path.read_text()
            if PUBLISH_AGGREGATOR_START in existing and PUBLISH_AGGREGATOR_END in existing:
                before = existing[: existing.index(PUBLISH_AGGREGATOR_START)]
                after = existing[
                    existing.index(PUBLISH_AGGREGATOR_END) + len(PUBLISH_AGGREGATOR_END) :
                ]
                full_content = (
                    f"{before}{INJECTION_START}\n{prompt_content}\n{INJECTION_END}{after}"
                )
            elif INJECTION_START in existing and INJECTION_END in existing:
                before = existing[: existing.index(INJECTION_START)]
                after = existing[existing.index(INJECTION_END) + len(INJECTION_END) :]
                full_content = (
                    f"{before}{INJECTION_START}\n{prompt_content}\n{INJECTION_END}{after}"
                )
            else:
                full_content = f"{INJECTION_START}\n{prompt_content}\n{INJECTION_END}\n"
        else:
            full_content = f"{INJECTION_START}\n{prompt_content}\n{INJECTION_END}\n"

        # Write to temp file (survives sigkill — just orphaned, no harm)
        override_file = Path(tempfile.mktemp(prefix="ai-hats-override-", suffix=".md"))
        override_file.write_text(full_content)

        return ["--system-prompt-file", str(override_file)], {}

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["claude"]
        if args:
            cmd.extend(args)
        return cmd

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
        *,
        model: str | None = None,
    ) -> list[str]:
        extra = ["--model", model] if model else []
        return cmd + extra + ["--print", "-p", meta_prompt]

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        return {}


PROVIDERS: dict[str, type[Provider]] = {
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
}


def get_provider(name: str) -> Provider:
    """Get a provider instance by name."""
    cls = PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown provider: {name}. Available: {list(PROVIDERS.keys())}")
    return cls()
