"""Agy surface adapter — maps the `agy` (Antigravity) CLI to the ai-hats `Provider`.

Materialization contract (driven by ``build_session_artifacts`` / ADR-0018):

- **Role / system prompt** — ``build_system_prompt`` composes PRIORITIES + the
  merged role/trait injection + always-on RULES. Written to
  ``.cache/sessions/<sid>/rules/GEMINI.md`` and passed via ``--add-dir <rules_dir>``.
  Root ``GEMINI.md`` is untouched and native-by-default.
- **Skills** — ``materialize_runtime_skills`` mirrors composed skills into
  ``.cache/sessions/<sid>/rules/.agents/skills/``.
- **Hooks** — ``ensure_global_dispatcher_hook`` idempotently ensures the global
  dispatcher (``ai-hats-hook-dispatcher``) is registered in ``~/.gemini/antigravity-cli/settings.json``.
  Active session hooks are written to ``.cache/sessions/<sid>/hooks.json``.
  Zero files created in project root.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator

from .global_hook import ensure_global_dispatcher_hook

from ai_hats.paths import (
    GEMINI_MD_FILENAME,
    gemini_md,
    session_cache_dir,
)

def agy_user_settings_json() -> Path:
    from ai_hats.paths._discovery import tool_home
    return tool_home("gemini", "GEMINI_CONFIG_DIR") / "antigravity-cli" / "settings.json"
from ai_hats.providers import Provider
from ai_hats.session_artifacts import ArtifactCategory, BuiltArtifacts, RunMode

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult
    from ai_hats.providers import ProviderHint


AGY_FILE_MUTATION_MATCHER = (
    "Create|Edit|Write|MultiEdit|write_to_file|replace_file_content|multi_replace_file_content"
)


class AgyProvider(Provider):
    """`agy` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "agy"

    def detected_home_dirs(self) -> list[str]:
        return [".gemini", ".agy"]

    def provider_hints(self) -> list["ProviderHint"]:
        from ai_hats.providers import ProviderHint
        return [
            ProviderHint(
                name="--model",
                values="gemini-2.5-pro, ...",
                description="Overrides the model to use for the session.",
            ),
            ProviderHint(
                name="--headless",
                values="N/A",
                description="Run agy in headless mode without TUI.",
            ),
        ]

    def system_prompt_path(self, project_dir: Path) -> Path:
        return gemini_md(project_dir)

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    @contextmanager
    def execution_context(self, project_dir: Path) -> Generator[None, None, None]:
        """Clean execution context — HATS-1166: file-hiding hacks retired (native-by-default)."""
        yield

    def build_system_prompt(self, result: CompositionResult) -> str:
        # HATS-993: skills reach agy via the native .agy/skills/ registry
        return self._compose_sections(result, include_skills=False)

    def _session_skills_dir(self, project_dir: Path, session_id: str) -> Path:
        return session_cache_dir(project_dir, session_id) / "rules" / ".agents" / "skills"

    def build_category_artifact(
        self,
        category: ArtifactCategory,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
        *,
        run_mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        """Materialize a single category of session artifacts for AgyProvider (ADR-0018)."""
        cache_dir = session_cache_dir(project_dir, session_id)
        cache_dir.mkdir(parents=True, exist_ok=True)

        if category == ArtifactCategory.CONTEXT:
            self._build_context_artifact(project_dir, result, cache_dir, run_mode, artifacts)
        elif category == ArtifactCategory.SKILLS:
            self._build_skills_artifact(project_dir, result, session_id, cache_dir, run_mode, artifacts)
        elif category == ArtifactCategory.HOOKS:
            self._build_hooks_artifact(project_dir, result, session_id, cache_dir, run_mode, artifacts)
        elif category == ArtifactCategory.SETTINGS:
            self._build_settings_artifact(project_dir, result, cache_dir, run_mode, artifacts)

    def _build_context_artifact(
        self,
        project_dir: Path,
        result: CompositionResult,
        cache_dir: Path,
        mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        prompt_content = self.build_system_prompt(result)
        prompt_content = expand_path_placeholders(prompt_content, project_dir)
        prompt_content = expand_role_catalog(prompt_content, project_dir)

        artifacts.full_content = prompt_content
        rules_dir = cache_dir / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        session_md = rules_dir / GEMINI_MD_FILENAME
        session_md.write_text(prompt_content)
        artifacts.materialized.append(session_md)

        artifacts.cli_args.extend(["--add-dir", str(rules_dir)])

    def _build_skills_artifact(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
        cache_dir: Path,
        mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        self.materialize_runtime_skills(project_dir, result, session_id)
        skills_dir = self._session_skills_dir(project_dir, session_id)
        from ai_hats.skills_dir import inject_skill_paths_to_env
        inject_skill_paths_to_env(artifacts.extra_env, result.skills, skills_dir)
        artifacts.materialized.append(skills_dir)

    def _build_hooks_artifact(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
        cache_dir: Path,
        mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        from ai_hats.hook_collection import collect_runtime_hooks

        # HATS-1166: Idempotent global hook registration at session start
        user_settings = agy_user_settings_json()
        ensure_global_dispatcher_hook(user_settings)

        # Build session hooks manifest in session cache dir
        collected = collect_runtime_hooks(result)
        skills_dir = self._session_skills_dir(project_dir, session_id)

        manifest: dict[str, list[dict]] = {}
        for event, entries in collected.items():
            event_list = manifest.setdefault(event, [])
            for skill_name, hook in entries:
                matcher = getattr(hook, "matcher", "")
                if "Edit" in matcher or "Write" in matcher:
                    matcher = AGY_FILE_MUTATION_MATCHER
                script = getattr(hook, "script", "")
                command = str(skills_dir / skill_name / script)
                event_list.append({
                    "matcher": matcher,
                    "command": command,
                    "tag": f"ai-hats:{skill_name}:{event}:{matcher}",
                })

        hooks_json = cache_dir / "hooks.json"
        hooks_json.write_text(json.dumps(manifest, indent=2) + "\n")
        artifacts.materialized.append(hooks_json)

    def _build_settings_artifact(
        self,
        project_dir: Path,
        result: CompositionResult,
        cache_dir: Path,
        mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        """Dormant settings category for AgyProvider."""
        pass

    def materialize_runtime_skills(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> list[str]:
        """Mirror the role's skills into the session's ``rules/.agents/skills/``."""
        from ai_hats.skills_dir import materialize_skills_dir

        materialize_skills_dir(
            self._session_skills_dir(project_dir, session_id),
            result.skills,
            project_dir,
            session_id,
            gitignore_entry=None,
        )
        return []

    def ensure_runtime_hooks(
        self, project_dir: Path, result: CompositionResult | None = None, **kwargs
    ) -> None:
        """HATS-1166: Runtime hooks write to session cache hooks.json via build_session_artifacts."""
        pass

    def build_session_prompt(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        """Write composed prompt & session artifacts via build_session_artifacts (ADR-0018)."""
        artifacts = self.build_session_artifacts(
            project_dir, result, session_id, run_mode=RunMode.HITL
        )
        return (artifacts.cli_args, artifacts.extra_env, artifacts.full_content or "")

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["agy"]
        if args:
            cmd.extend(args)
        return cmd

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
    ) -> list[str]:
        return cmd + ["-p", meta_prompt]

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
        from ai_hats.paths import ai_hats_dir

        return {
            ENV_AI_HATS_DIR: str(ai_hats_dir(project_dir)),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
        }
