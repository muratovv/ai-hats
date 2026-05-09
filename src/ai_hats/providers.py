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

# HATS-283 — provider publish (canonical → derived)
PUBLISH_AGGREGATOR_START = "<!-- ai-hats:start -->"
PUBLISH_AGGREGATOR_END = "<!-- ai-hats:end -->"
PUBLISH_MANIFEST = ".ai-hats-managed"  # at .claude/ root; distinct from skills/.ai-hats-managed
CANONICAL_MANIFEST_NAME = "MANAGED"  # mirrors assembler.CANONICAL_MANIFEST

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

    def publish(self, canonical_dir: Path, project_dir: Path) -> None:
        """Publish canonical layered output to provider-discoverable namespace.

        Default: no-op. Subclasses (Claude) override to materialize an
        aggregator + per-file mirror. Gemini's env-var override path remains
        unchanged (non-goal of HATS-276).
        """
        return

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

    # ----- HATS-283: canonical → .claude/ publish -----

    def publish(self, canonical_dir: Path, project_dir: Path) -> None:
        """Mirror `.agent/ai-hats/` into `.claude/` and write the aggregator.

        - `.claude/<path>` mirrors each file listed in canonical MANAGED.
        - `.agent/ai-hats/user-rules/*.md` are copied to `.claude/rules/`
          alongside framework rules (user wins on name collision).
        - `.claude/CLAUDE.md` aggregator @-imports the mirrored files in
          deterministic order.
        - `.claude/.ai-hats-managed` tracks every file we own; cleanup
          removes paths absent from the new run.
        - `.claude/skills/` is managed separately (export_skills) and is
          never touched by publish.
        """
        target_dir = project_dir / ".claude"
        target_dir.mkdir(parents=True, exist_ok=True)

        canonical_paths = self._read_canonical_manifest(canonical_dir / CANONICAL_MANIFEST_NAME)
        if not canonical_paths:
            # Empty / missing canonical → nothing to publish, but still cleanup
            # if a previous publish left files behind.
            self._cleanup_publish(target_dir, new_paths=set())
            return

        targets: dict[str, bytes] = {}

        # Mirror canonical files (skip user-rules/ — handled separately so
        # they land in .claude/rules/ not .claude/user-rules/).
        for relpath in canonical_paths:
            if relpath.startswith("user-rules/"):
                continue
            src = canonical_dir / relpath
            if src.is_file():
                targets[relpath] = src.read_bytes()

        # User-rules → .claude/rules/<name>.md (last-write wins on name collision).
        user_rules_dir = canonical_dir / "user-rules"
        if user_rules_dir.is_dir():
            for md in sorted(user_rules_dir.glob("*.md")):
                targets[f"rules/{md.name}"] = md.read_bytes()

        # Aggregator on top of mirrored files.
        targets["CLAUDE.md"] = self._render_aggregator(targets).encode()

        # Idempotent write.
        for relpath, content in targets.items():
            self._atomic_write_if_changed(target_dir / relpath, content)

        # Stale cleanup.
        self._cleanup_publish(target_dir, new_paths=set(targets.keys()))

        # Write new manifest.
        self._write_publish_manifest(target_dir / PUBLISH_MANIFEST, sorted(targets))

    @staticmethod
    def _render_aggregator(targets: dict[str, bytes]) -> str:
        """Build `.claude/CLAUDE.md` body with @import directives.

        Order: priorities → traits → role → rules → skills_index. Within each
        bucket, paths are sorted alphabetically so diffs stay deterministic.
        Aggregator is dormant until T5 wires `./CLAUDE.md` to @-import it.
        """
        lines = [
            PUBLISH_AGGREGATOR_START,
            "# ai-hats canonical view (auto-generated; do not edit)",
        ]

        def _section(predicate) -> None:
            paths = sorted(p for p in targets if predicate(p))
            if not paths:
                return
            lines.append("")
            for p in paths:
                lines.append(f"@./{p}")

        _section(lambda p: p == "priorities.md")
        _section(lambda p: p.startswith("traits/"))
        _section(lambda p: p == "role.md")
        _section(lambda p: p.startswith("rules/"))
        _section(lambda p: p == "skills_index.md")

        lines.append(PUBLISH_AGGREGATOR_END)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _read_canonical_manifest(path: Path) -> list[str]:
        if not path.exists():
            return []
        out: list[str] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
        return out

    @staticmethod
    def _atomic_write_if_changed(path: Path, content: bytes) -> bool:
        if path.exists() and path.read_bytes() == content:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)
        return True

    @staticmethod
    def _read_publish_manifest(path: Path) -> set[str]:
        if not path.exists():
            return set()
        out: set[str] = set()
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.add(line)
        return out

    @staticmethod
    def _write_publish_manifest(path: Path, names: list[str]) -> None:
        body = "# ai-hats published files manifest. Do not edit.\n"
        body += "\n".join(names) + "\n"
        ClaudeProvider._atomic_write_if_changed(path, body.encode())

    def _cleanup_publish(self, target_dir: Path, *, new_paths: set[str]) -> None:
        """Remove paths from previous manifest that are absent from new_paths.

        `skills/**` is excluded (managed separately by export_skills).
        """
        previous = self._read_publish_manifest(target_dir / PUBLISH_MANIFEST)
        for stale in previous - new_paths:
            if stale.startswith("skills/"):
                continue
            target = target_dir / stale
            target.unlink(missing_ok=True)
            # Best-effort empty-dir cleanup, stopping at .claude/.
            parent = target.parent
            while parent != target_dir and parent.is_dir():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent


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
