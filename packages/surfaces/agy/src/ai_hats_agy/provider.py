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
from ai_hats.providers import Provider
from ai_hats.session_artifacts import BuiltArtifacts, RunMode


def agy_user_settings_json() -> Path:
    from ai_hats.paths._discovery import tool_home
    return tool_home("gemini", "GEMINI_CONFIG_DIR") / "antigravity-cli" / "settings.json"

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

    def _cache_dir(self, project_dir: Path, session_id: str, artifacts: BuiltArtifacts) -> Path:
        cache_dir = session_cache_dir(project_dir, session_id)
        artifacts.port.mkdir(cache_dir)
        return cache_dir

    # -- context ---------------------------------------------------------------

    def _build_context_hitl(self, project_dir, result, session_id, artifacts) -> None:
        """Rules dir on disk, handed over with --add-dir."""
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        cache_dir = self._cache_dir(project_dir, session_id, artifacts)
        prompt_content = self.build_system_prompt(result)
        prompt_content = expand_path_placeholders(prompt_content, project_dir)
        prompt_content = expand_role_catalog(prompt_content, project_dir)

        artifacts.full_content = prompt_content
        rules_dir = cache_dir / "rules"
        artifacts.port.mkdir(rules_dir)
        session_md = rules_dir / GEMINI_MD_FILENAME
        artifacts.port.write_text(session_md, prompt_content)
        artifacts.materialized.append(session_md)
        artifacts.cli_args.extend(["--add-dir", str(rules_dir)])

    def _build_context_automate(self, project_dir, result, session_id, artifacts) -> None:
        """Role sections inline in the meta-prompt — nothing on disk, no flag."""
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        prompt_content = self.build_system_prompt(result)
        prompt_content = expand_path_placeholders(prompt_content, project_dir)
        prompt_content = expand_role_catalog(prompt_content, project_dir)
        artifacts.full_content = prompt_content

    # -- skills ----------------------------------------------------------------

    def _materialize_skills(self, project_dir, result, session_id, artifacts) -> Path:
        # Not via materialize_runtime_skills: that is a published extension point
        # and cannot take the port (HATS-1211 / HATS-1207 R4).
        from ai_hats.skills_dir import inject_skill_paths_to_env, materialize_skills_dir

        self._cache_dir(project_dir, session_id, artifacts)
        skills_dir = self._session_skills_dir(project_dir, session_id)
        materialize_skills_dir(
            skills_dir, result.skills, project_dir, session_id, artifacts.port
        )
        inject_skill_paths_to_env(artifacts.extra_env, result.skills, skills_dir)
        artifacts.materialized.append(skills_dir)
        return skills_dir

    def _build_skills_hitl(self, project_dir, result, session_id, artifacts) -> None:
        self._materialize_skills(project_dir, result, session_id, artifacts)

    def _build_skills_automate(self, project_dir, result, session_id, artifacts) -> None:
        self._materialize_skills(project_dir, result, session_id, artifacts)

    # -- hooks -----------------------------------------------------------------

    def _hooks_manifest(self, project_dir: Path, result, session_id: str) -> dict[str, list[dict]]:
        from ai_hats.hook_collection import collect_runtime_hooks

        skills_dir = self._session_skills_dir(project_dir, session_id)
        manifest: dict[str, list[dict]] = {}
        for event, entries in collect_runtime_hooks(result).items():
            event_list = manifest.setdefault(event, [])
            for skill_name, hook in entries:
                matcher = getattr(hook, "matcher", "")
                if "Edit" in matcher or "Write" in matcher:
                    matcher = AGY_FILE_MUTATION_MATCHER
                script = getattr(hook, "script", "")
                event_list.append({
                    "matcher": matcher,
                    "command": str(skills_dir / skill_name / script),
                    "tag": f"ai-hats:{skill_name}:{event}:{matcher}",
                })
        return manifest

    def _build_hooks_hitl(self, project_dir, result, session_id, artifacts) -> None:
        """Global dispatcher registration (HATS-1166) plus the session manifest it reads."""
        cache_dir = self._cache_dir(project_dir, session_id, artifacts)
        ensure_global_dispatcher_hook(agy_user_settings_json(), artifacts.port)

        manifest = self._hooks_manifest(project_dir, result, session_id)
        hooks_json = cache_dir / "hooks.json"
        artifacts.port.write_text(hooks_json, json.dumps(manifest, indent=2) + "\n")
        artifacts.materialized.append(hooks_json)

    def _build_hooks_automate(self, project_dir, result, session_id, artifacts) -> None:
        """Global dispatcher registration plus the session manifest it reads (HATS-1223)."""
        self._build_hooks_hitl(project_dir, result, session_id, artifacts)

    def materialize_runtime_skills(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> list[str]:
        """Mirror the role's skills into the session's ``rules/.agents/skills/``."""
        from ai_hats.materialization import ApplyMaterializer
        from ai_hats.skills_dir import materialize_skills_dir

        # A published extension point cannot carry the port, so this path always
        # writes — it is one of the builder bypasses HATS-1207 removes.
        materialize_skills_dir(
            self._session_skills_dir(project_dir, session_id),
            result.skills,
            project_dir,
            session_id,
            ApplyMaterializer(),
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
            project_dir, result, session_id, run_mode=RunMode.HITL,
            artifacts=BuiltArtifacts(),
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
        import sys
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
        from ai_hats.paths import ai_hats_dir

        return {
            ENV_AI_HATS_DIR: str(ai_hats_dir(project_dir)),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
            "AI_HATS_PYTHON": sys.executable,
        }
