"""Provider abstraction — adapters for Gemini and Claude CLI."""

from __future__ import annotations

import abc
import shutil
from pathlib import Path

from .composer import CompositionResult, ResolvedComponent


INJECTION_START = "<!-- AI-HATS:START -->"
INJECTION_END = "<!-- AI-HATS:END -->"


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

    @abc.abstractmethod
    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        """Get environment variables needed for the provider."""

    def update_system_prompt(self, project_dir: Path, content: str) -> None:
        """Write or update system prompt between markers in the prompt file."""
        prompt_path = self.system_prompt_path(project_dir)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)

        if prompt_path.exists():
            existing = prompt_path.read_text()
            if INJECTION_START in existing and INJECTION_END in existing:
                before = existing[: existing.index(INJECTION_START)]
                after = existing[existing.index(INJECTION_END) + len(INJECTION_END) :]
                new_content = f"{before}{INJECTION_START}\n{content}\n{INJECTION_END}{after}"
                prompt_path.write_text(new_content)
                return

        # Fresh write with markers
        prompt_path.write_text(f"{INJECTION_START}\n{content}\n{INJECTION_END}\n")


class GeminiProvider(Provider):
    @property
    def name(self) -> str:
        return "gemini"

    def system_prompt_path(self, project_dir: Path) -> Path:
        return project_dir / "GEMINI.md"

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        sections = []

        if result.priorities:
            sections.append("## PRIORITIES\n" + "\n".join(
                f"{i+1}. {p}" for i, p in enumerate(result.priorities)
            ))

        if result.merged_injection:
            sections.append(result.merged_injection)

        if result.rules:
            rules_section = "## RULES\n"
            for rule in result.rules:
                if rule.injection:
                    rules_section += f"\n### {rule.name}\n{rule.injection}\n"
            sections.append(rules_section)

        if result.skills:
            skills_section = "## SKILLS\n"
            for skill in result.skills:
                if skill.injection:
                    skills_section += f"\n### {skill.name}\n{skill.injection}\n"
            sections.append(skills_section)

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

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["gemini"]
        if args:
            cmd.extend(args)
        return cmd

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

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        sections = []

        if result.priorities:
            sections.append("## PRIORITIES\n" + "\n".join(
                f"{i+1}. {p}" for i, p in enumerate(result.priorities)
            ))

        if result.merged_injection:
            sections.append(result.merged_injection)

        if result.rules:
            rules_section = "## RULES\n"
            for rule in result.rules:
                if rule.injection:
                    rules_section += f"\n### {rule.name}\n{rule.injection}\n"
            sections.append(rules_section)

        if result.skills:
            skills_section = "## SKILLS\n"
            for skill in result.skills:
                if skill.injection:
                    skills_section += f"\n### {skill.name}\n{skill.injection}\n"
            sections.append(skills_section)

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

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["claude"]
        if args:
            cmd.extend(args)
        return cmd

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
