"""Agy surface adapter — maps the `agy` (Antigravity) CLI to the ai-hats `Provider`.

Materialization contract (all driven by ``build_session_prompt`` at session start,
per session id, into gitignored per-session homes):

- **Role / system prompt** — ``build_system_prompt`` composes PRIORITIES + the
  merged role/trait injection + always-on RULES (rules are *inlined* here; agy has
  no separate rules channel). Path placeholders and ``<available_roles>`` are
  expanded, then the bytes are written to ``.cache/sessions/<sid>/rules/GEMINI.md``.
- **Skills (and their hooks)** — ``materialize_runtime_skills`` mirrors the role's
  *composed* skills (``result.skills``) into ``.agy/skills/`` — agy's native
  workspace skill registry, ref-counted per session, gitignored. Each skill dir
  carries its own hooks. Skills are NOT dropped: this native registry is their
  delivery channel (HATS-993), which is exactly why ``build_system_prompt`` passes
  ``include_skills=False`` — the text index would only duplicate the registry.
- **Session / startup prompt** — the composed ``GEMINI.md`` is threaded to agy via
  ``--add-dir <session-rules-dir>``; agy loads ``GEMINI.md`` from added directories
  at start, so the project's active role reaches the agent WITHOUT touching the
  repo's own ``./GEMINI.md``. The third return value is the exact prompt bytes
  (persisted by ``WrapRunner`` for audit, HATS-523).
- **Params passed to agy** — interactive (HITL): ``get_cli_command`` → ``["agy",
  --add-dir <dir>]``. Headless (Automate): ``get_run_command`` → ``agy [--model
  <m>] -p <meta_prompt>`` (no ``--skip-trust`` — not a valid agy flag; headless
  ``agy -p`` needs no trust grant).
- **Env dependencies** — ``get_env`` exports ``AI_HATS_DIR`` + ``AI_HATS_PROJECT_DIR``
  so materialized hooks/tools resolve the project root at runtime.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator

from ai_hats.paths import GEMINI_MD_FILENAME, agy_skills_dir, gemini_md
from ai_hats.providers import Provider

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult


class AgyProvider(Provider):
    """`agy` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "agy"

    def system_prompt_path(self, project_dir: Path) -> Path:
        return gemini_md(project_dir)

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    @contextmanager
    def execution_context(self, project_dir: Path) -> Generator[None, None, None]:
        """Temporarily move workspace root rule files (GEMINI.md, AGENTS.md) out of the way during agy execution.

        agy automatically scans and merges root GEMINI.md / AGENTS.md files in addition to --add-dir rules.
        Hiding them during execution prevents project-level rules from leaking into session roles.
        """
        targets = [
            project_dir / GEMINI_MD_FILENAME,
            project_dir / "AGENTS.md",
        ]
        moved: list[tuple[Path, Path]] = []
        try:
            for target in targets:
                if target.exists() or target.is_symlink():
                    tmp_path = target.with_name(f".{target.name}.ai_hats_bak_{id(self)}")
                    target.rename(tmp_path)
                    moved.append((target, tmp_path))
            yield
        finally:
            for target, tmp_path in moved:
                if tmp_path.exists() or tmp_path.is_symlink():
                    if target.exists() or target.is_symlink():
                        target.unlink()
                    tmp_path.rename(target)

    def build_system_prompt(self, result: CompositionResult) -> str:
        # HATS-993: skills reach agy via the native .agy/skills/ registry
        return self._compose_sections(result, include_skills=False)


    def materialize_runtime_skills(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> list[str]:
        """Mirror the role's skills into ``.agy/skills/``."""
        from ai_hats.skills_dir import materialize_skills_dir

        materialize_skills_dir(
            agy_skills_dir(project_dir),
            result.skills,
            project_dir,
            session_id,
            gitignore_entry=".agy/skills/",
        )
        return []

    def build_session_prompt(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        """Session-scoped role via ``--add-dir`` memory.

        agy loads ``GEMINI.md`` files from workspace added-directories at
        session start; a per-session dir under ``.cache/sessions/<sid>/rules/``
        carries the composed prompt.
        """
        from ai_hats.paths import session_cache_dir
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        prompt_content = self.build_system_prompt(result)
        prompt_content = expand_path_placeholders(prompt_content, project_dir)
        prompt_content = expand_role_catalog(prompt_content, project_dir)

        cache_dir = session_cache_dir(project_dir, session_id)
        rules_dir = cache_dir / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / GEMINI_MD_FILENAME).write_text(prompt_content)

        self.materialize_runtime_skills(project_dir, result, session_id)

        return ["--add-dir", str(rules_dir)], {}, prompt_content

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["agy"]
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
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
        from ai_hats.paths import ai_hats_dir

        return {
            ENV_AI_HATS_DIR: str(ai_hats_dir(project_dir)),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
        }
