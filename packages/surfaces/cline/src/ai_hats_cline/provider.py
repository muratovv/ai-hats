"""Cline surface adapter — maps the `cline` CLI to the ai-hats `Provider`.

Inline-`-s` surface registered via the `ai_hats.providers` entry point (HATS-870).
Verified cline-v3.0.3 flag facts + the CLINE_DATA_DIR/auth rationale: HATS-956.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats.providers import Provider

if TYPE_CHECKING:
    # Re-exported by the integrator; import here keeps the plugin's only
    # first-party root `ai_hats` (workspace-boundary Rule 1, HATS-869).
    from ai_hats.providers import CompositionResult
    from ai_hats_observe.parsers.base import TranscriptParser


class ClineProvider(Provider):
    """`cline` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "cline"

    def transcript_parser(self) -> TranscriptParser:
        # HATS-960: cline emits a structured `.messages.json` → richer parse than
        # the default trace-only. Lazy import so entry-point discovery of the
        # provider class never eager-loads the observe parsers.
        from ai_hats_cline.parser import ClineParser

        return ClineParser()

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
        # No native cline skill registry (MVP) → keep the AVAILABLE SKILLS index
        # as the discovery channel, like Gemini (HATS-701).
        return self._compose_sections(result, include_skills=True)

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

        del session_id
        prompt_content = self.build_system_prompt(result)
        prompt_content = expand_path_placeholders(prompt_content, project_dir)
        prompt_content = expand_role_catalog(prompt_content, project_dir)
        return ["-i", "-s", prompt_content], {}, prompt_content
