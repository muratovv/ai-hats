"""OpenCode adapter for ai-hats (HATS-1788).

Role context, skills and runtime hooks are materialized into the ai-hats
session cache and delivered through OpenCode's per-session ``OPENCODE_CONFIG``
config file: a composed primary agent carries the role prompt, and the
runtime-hook dispatcher registers through the config's ``plugin`` array as a
``file://`` entry. The project root is never written: no ``AGENTS.md``, no
``.opencode/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats.providers import Provider
from ai_hats.session_artifacts import BuiltArtifacts, RunMode, SessionPolicy

from .runtime_hooks import materialize_hook_manifest

if TYPE_CHECKING:
    from ai_hats.providers import CompositionResult

ENV_OPENCODE_CONFIG = "OPENCODE_CONFIG"

#: The single session agent opencode is launched with (`--agent`). One stable
#: name keeps launch argv free of role-derived values that change per project.
AGENT_NAME = "ai-hats"

_DANGEROUS_FLAGS = {"--auto"}

_SCHEMA = "https://opencode.ai/config.json"


class OpenCodeProvider(Provider):
    """The ``opencode`` entry-point surface, isolated to one ai-hats session."""

    @property
    def name(self) -> str:
        return "opencode"

    def detected_home_dirs(self) -> list[str]:
        return [".opencode"]

    def supports_session_command_wrappers(self) -> bool:
        # Consent middleware is PATH-based around the rack/wt binaries and
        # provider-agnostic (ADR-0030); opencode inherits launch env like codex.
        return True

    def provider_hints(self) -> list:
        from ai_hats.providers import ProviderHint

        return [
            ProviderHint(
                name="--model",
                values="<provider/model>",
                description="Override the model for this session.",
            ),
        ]

    def system_prompt_path(self, project_dir: Path) -> Path | None:
        """OpenCode receives ai-hats context via the session agent; no project file."""
        del project_dir
        return None

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: "CompositionResult") -> str:
        # The session-aware skill index is appended by _expanded_prompt, where
        # exact paths in this session's cache are available.
        return self._compose_sections(result, include_skills=False)

    # --- deterministic session paths -------------------------------------------------

    def session_config_path(self, project_dir: Path, session_id: str) -> Path:
        from ai_hats.paths import session_cache_dir

        return session_cache_dir(project_dir, session_id) / "opencode" / "opencode.json"

    def session_skills_root(self, project_dir: Path, session_id: str) -> Path:
        from ai_hats.paths import session_cache_dir

        return session_cache_dir(project_dir, session_id) / "opencode-home" / "skills"

    def _agent_description(self, result: "CompositionResult") -> str:
        return f"ai-hats composed role session ({result.name})"

    def _expanded_prompt(self, project_dir: Path, result, session_id: str) -> str:
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        prompt = self.build_system_prompt(result)
        index = self._skill_index(project_dir, result, session_id)
        if index:
            prompt = f"{prompt}\n\n{index}" if prompt else index
        prompt = expand_path_placeholders(prompt, project_dir)
        return expand_role_catalog(prompt, project_dir)

    @staticmethod
    def _skill_description(skill) -> str:
        from ai_hats.frontmatter import FrontmatterError, read_frontmatter

        try:
            metadata = read_frontmatter(skill.source_path / "SKILL.md")
        except (FrontmatterError, OSError):
            return skill.name
        description = metadata.get("description")
        return description if isinstance(description, str) and description else skill.name

    def _skill_index(self, project_dir: Path, result, session_id: str) -> str:
        if not result.skills:
            return ""
        skills_root = self.session_skills_root(project_dir, session_id)
        lines = [
            "## AVAILABLE SKILLS",
            "Use a skill when its description matches the task. Before using it, read the exact "
            "SKILL.md path below; resolve its relative references from that skill directory.",
        ]
        for skill in result.skills:
            skill_md = skills_root / skill.name / "SKILL.md"
            lines.append(f"- **{skill.name}** — {self._skill_description(skill)} (`{skill_md}`)")
        return "\n".join(lines)

    # --- session config accumulation -------------------------------------------------
    #
    # Category handlers run in enum order (context → skills → hooks) under one
    # BuiltArtifacts and only accumulate the desired config document on the
    # artifacts object; build_session_artifacts persists it exactly once after
    # the loop. One merge_json entry per build, never a torn intermediate file.

    @staticmethod
    def _config_doc(artifacts: BuiltArtifacts) -> dict:
        doc = getattr(artifacts, "_ai_hats_opencode_config", None)
        if doc is None:
            doc = {"$schema": _SCHEMA}
            setattr(artifacts, "_ai_hats_opencode_config", doc)
        return doc

    def build_session_artifacts(
        self,
        project_dir: Path,
        result: "CompositionResult",
        session_id: str,
        *,
        run_mode: RunMode | str = RunMode.HITL,
        policy: SessionPolicy | None = None,
        artifacts: BuiltArtifacts,
    ) -> BuiltArtifacts:
        built = super().build_session_artifacts(
            project_dir,
            result,
            session_id,
            run_mode=run_mode,
            policy=policy,
            artifacts=artifacts,
        )
        doc = getattr(built, "_ai_hats_opencode_config", None)
        if doc is not None:
            built.port.merge_json(self.session_config_path(project_dir, session_id), doc)
            built.materialized.append(self.session_config_path(project_dir, session_id))
        return built

    # --- context ---------------------------------------------------------------------

    def _build_context_hitl(self, project_dir, result, session_id, artifacts) -> None:
        prompt = self._expanded_prompt(project_dir, result, session_id)
        artifacts.full_content = prompt
        doc = self._config_doc(artifacts)
        doc.setdefault("agent", {})[AGENT_NAME] = {
            "description": self._agent_description(result),
            "mode": "primary",
            "prompt": prompt,
        }
        artifacts.cli_args.extend(["--agent", AGENT_NAME])
        artifacts.extra_env[ENV_OPENCODE_CONFIG] = str(
            self.session_config_path(project_dir, session_id)
        )

    def _build_context_automate(self, project_dir, result, session_id, artifacts) -> None:
        self._build_context_hitl(project_dir, result, session_id, artifacts)

    # --- skills ----------------------------------------------------------------------

    def _deliver_skills(self, project_dir, result, session_id, artifacts) -> None:
        from ai_hats.skills_dir import inject_skill_paths_to_env, materialize_skills_dir

        if not result.skills:
            return
        skills_root = self.session_skills_root(project_dir, session_id)
        materialize_skills_dir(skills_root, result.skills, project_dir, artifacts.port)
        inject_skill_paths_to_env(artifacts.extra_env, result.skills, skills_root)
        artifacts.materialized.append(skills_root)

    def _build_skills_hitl(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_skills(project_dir, result, session_id, artifacts)

    def _build_skills_automate(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_skills(project_dir, result, session_id, artifacts)

    # --- hooks -----------------------------------------------------------------------

    def _deliver_hooks(self, project_dir, result, session_id, artifacts) -> None:
        from ai_hats.hook_collection import collect_runtime_hooks

        # Hookless roles must not register an inert dispatcher plugin.
        if not collect_runtime_hooks(result):
            return
        manifest_path, plugin_path = materialize_hook_manifest(
            project_dir,
            result,
            session_id,
            artifacts,
            skills_dir=self.session_skills_root(project_dir, session_id),
        )
        del manifest_path
        doc = self._config_doc(artifacts)
        plugins = doc.setdefault("plugin", [])
        entry = f"file://{plugin_path}"
        if entry not in plugins:
            plugins.append(entry)

    def _build_hooks_hitl(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_hooks(project_dir, result, session_id, artifacts)

    def _build_hooks_automate(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_hooks(project_dir, result, session_id, artifacts)

    # --- launch ----------------------------------------------------------------------

    @staticmethod
    def _validate_passthrough(args: list[str]) -> None:
        for arg in args:
            token = arg.split("=", 1)[0].lower()
            if token in _DANGEROUS_FLAGS or token == "-p":
                raise ValueError(f"unsafe OpenCode option is not allowed by ai-hats: {arg}")
            if arg == "--agent":
                raise ValueError(
                    "unsafe OpenCode option is not allowed by ai-hats: --agent is owned "
                    f"by ai-hats ({AGENT_NAME})"
                )

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        extra = list(args or [])
        self._validate_passthrough(extra)
        return ["opencode", *extra]

    def get_run_command(self, cmd: list[str], meta_prompt: str) -> list[str]:
        """Build the headless `opencode run ...` command for Automate launches."""
        base = list(cmd or ["opencode"])
        return [base[0], "run", *base[1:], meta_prompt]

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        del session_dir
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR, ai_hats_dir

        return {
            ENV_AI_HATS_DIR: str(ai_hats_dir(project_dir)),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
        }
