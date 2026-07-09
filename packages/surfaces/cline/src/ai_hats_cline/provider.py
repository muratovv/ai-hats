"""Cline surface adapter — maps the `cline` CLI to the ai-hats `Provider`.

Inline-`-s` surface registered via the `ai_hats.providers` entry point (HATS-870).
Verified cline-v3.0.3 flag facts + the CLINE_DATA_DIR/auth rationale: HATS-956.
Skill materialization into `.cline/skills/` native registry: HATS-963.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats.providers import Provider

if TYPE_CHECKING:
    # Re-exported by the integrator; import here keeps the plugin's only
    # first-party root `ai_hats` (workspace-boundary Rule 1, HATS-869).
    from ai_hats.providers import CompositionResult

# Marker file tracking which skill dirs under `.cline/skills/` are ai-hats-owned
# (so user-authored skills are preserved on re-materialization). Mirrors the
# `.ai-hats-managed` convention from plugin_dir.py (HATS-901).
_MANAGED_MARKER = ".ai-hats-managed"


class ClineProvider(Provider):
    """`cline` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "cline"

    def system_prompt_path(self, project_dir: Path) -> Path:
        # Vestigial: cline takes the role inline via `-s`, so this path is never
        # read or written (update_system_prompt is a no-op). The ABC requires it.
        return project_dir / "CLINE.md"

    def update_system_prompt(self, project_dir: Path, content: str) -> None:
        # Inline-only surface: the role reaches cline through `-s`
        # (build_session_prompt), never a static file — so `set_role` must not
        # write a CLINE.md that cline would ignore.
        del project_dir, content

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        # HATS-963: skills now reach cline via the native `.cline/skills/`
        # registry (materialize_runtime_skills), so suppress the text index
        # to avoid the duplicate (~1.5k tok/session). Claude precedent:
        # providers.py:420-424 (include_skills=False).
        return self._compose_sections(result, include_skills=False)

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["cline"]
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
        # Headless one-shot. Strip only the interactive flags so a HITL `-i`
        # never collides with `--yolo`, while other passthrough (e.g. future
        # skill args) survives. Task prompt is positional (last).
        kept = [a for a in (cmd or ["cline"]) if a not in ("-i", "--tui")]
        extra = ["--model", model] if model else []
        return [*kept, "--yolo", "--json", *extra, meta_prompt]

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        # MVP (HATS-956 R10): do not isolate CLINE_DATA_DIR — keep the machine's
        # cline auth; ai-hats-wt already provides worktree isolation.
        del session_dir, project_dir
        return {}

    def materialize_runtime_skills(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> list[str]:
        """Materialize the composed role's skills into `.cline/skills/`.

        HATS-963: cline discovers skills by convention from `<project>/.cline/
        skills/` (docs.cline.bot/features/skills). No `--skills-dir` flag —
        returns ``[]`` (discovery is purely directory-based).

        Idempotent: re-materialization sweeps stale ai-hats-managed skills
        (role changed: skill A → B → A removed) while preserving user-authored
        skill dirs (tracked via the ``.ai-hats-managed`` marker, mirroring
        plugin_dir.py's HATS-901 convention).
        """
        from ai_hats.placeholders import expand_path_placeholders

        del session_id  # project-scoped, not session-scoped
        skills_dir = project_dir / ".cline" / "skills"
        marker = skills_dir / _MANAGED_MARKER

        skills_dir.mkdir(parents=True, exist_ok=True)

        # Read previously managed skill names (to sweep stale ones).
        prev_managed: set[str] = set()
        if marker.is_file():
            prev_managed = {
                line.strip()
                for line in marker.read_text().splitlines()
                if line.strip()
            }

        # Compose the desired set from the role's skills.
        desired = {s.name for s in result.skills if s.source_path.is_dir()}

        # Sweep stale managed skills (were managed, no longer desired).
        for name in prev_managed - desired:
            stale = skills_dir / name
            if stale.is_dir():
                shutil.rmtree(stale)

        # Materialize each desired skill (copytree + placeholder expansion).
        for skill in result.skills:
            if not skill.source_path.is_dir():
                continue
            dest = skills_dir / skill.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(skill.source_path, dest)
            # HATS-380 parity: expand <ai_hats_dir> in SKILL.md before cline
            # reads it. Other assets (hooks, fixtures) are copied verbatim.
            skill_md = dest / "SKILL.md"
            if skill_md.exists():
                original = skill_md.read_text()
                expanded = expand_path_placeholders(original, project_dir)
                if expanded != original:
                    skill_md.write_text(expanded)

        # Write the marker so the next run knows which dirs ai-hats owns.
        marker.write_text("\n".join(sorted(desired)) + "\n" if desired else "")

        return []  # no CLI flag — cline discovers .cline/skills/ by convention

    def build_session_prompt(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        """HITL (WrapRunner-only): compose the role, expand placeholders, and
        hand cline the interactive TUI (`-i`) plus the role inline (`-s`).

        `-i` is safe here: `build_session_prompt` is HITL-exclusive (the automate
        path uses `get_run_command` with `--yolo`), so the TUI flag never meets
        the mutually-exclusive `--yolo`. The third return element is the exact
        meta-prompt bytes WrapRunner persists to `meta_prompt.txt` (HATS-523).
        """
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        prompt_content = self.build_system_prompt(result)
        prompt_content = expand_path_placeholders(prompt_content, project_dir)
        prompt_content = expand_role_catalog(prompt_content, project_dir)

        # HATS-963: materialize the role's skills into .cline/skills/ before
        # the TUI launches, so /skills discovers them. Mirrors Claude's
        # build_session_prompt calling materialize_runtime_skills (providers.py:492).
        self.materialize_runtime_skills(project_dir, result, session_id)

        return ["-i", "-s", prompt_content], {}, prompt_content
