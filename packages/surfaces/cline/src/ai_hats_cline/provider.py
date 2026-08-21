"""Cline surface adapter — maps the `cline` CLI to the ai-hats `Provider`.

HATS-1171: cline runs through the unified artifact-builder (ADR-0018) on the
clean-root invariant — skills materialize into the per-session cache and reach
cline via ``--config`` (spike HATS-1191); nothing lands in the project root.
HATS-1775: native ``--hooks-dir`` entrypoints deliver the composed per-tool
runtime-hook chain from the same session cache.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats.providers import Provider
from ai_hats.session_artifacts import BuiltArtifacts, RunMode

if TYPE_CHECKING:
    # Workspace-boundary Rule 1 (HATS-869): only first-party root is `ai_hats`.
    from ai_hats.providers import CompositionResult, ProviderHint
    from ai_hats_observe.parsers.base import TranscriptParser


class ClineProvider(Provider):
    """`cline` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "cline"

    def supports_session_command_wrappers(self) -> bool:
        return True

    def provider_hints(self) -> list["ProviderHint"]:
        from ai_hats.providers import ProviderHint

        return [
            ProviderHint(
                name="--yolo",
                values="N/A",
                description="Run cline in fully autonomous mode without asking for permission.",
            ),
        ]

    def transcript_parser(self) -> TranscriptParser:
        # Lazy import: provider discovery must not eager-load observe parsers.
        from ai_hats_cline.parser import ClineParser

        return ClineParser()

    def resolve_transcript(
        self,
        project_dir: Path,
        session_id: str,
        *,
        provider_session_id: str | None = None,
        end_ts: float | None = None,
    ) -> list[Path]:
        from ai_hats.paths import resolve_transcript, tool_home

        sessions_dir = tool_home("cline", "CLINE_DATA_DIR") / "data" / "sessions"
        exact = (
            sessions_dir / provider_session_id / f"{provider_session_id}.messages.json"
            if provider_session_id
            else None
        )
        return resolve_transcript(
            sessions_dir,
            "*/*.messages.json",
            session_id,
            exact_path=exact,
            end_ts=end_ts,
        )

    def system_prompt_path(self, project_dir: Path) -> Path | None:
        # HATS-1238: Inline-only surface — no root file managed.
        del project_dir
        return None

    def update_system_prompt(self, project_dir: Path, content: str) -> Path | None:
        # Inline-only surface: set_role must not write a CLINE.md cline would ignore.
        del project_dir, content
        return None

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        # Skills reach cline natively via <cache>/skills/ — a text index duplicates them.
        return self._compose_sections(result, include_skills=False)

    # ----- HATS-1171: unified artifact-builder (ADR-0018) -----

    # SETTINGS has no Cline-native artifact. HOOKS use --hooks-dir below.

    def get_cli_launch_args(
        self, base_cmd: list[str], session_id: str, is_resume: bool
    ) -> list[str]:
        cmd = list(base_cmd)
        if "-i" not in cmd and "--yolo" not in cmd:
            cmd.insert(1, "-i")
        return super().get_cli_launch_args(cmd, session_id, is_resume)

    def _cache_dir(self, project_dir: Path, session_id: str, artifacts: BuiltArtifacts) -> Path:
        from ai_hats.paths import session_cache_dir

        cache_dir = session_cache_dir(project_dir, session_id)
        artifacts.port.mkdir(cache_dir)
        return cache_dir

    # -- context ---------------------------------------------------------------

    def _build_context_hitl(self, project_dir, result, session_id, artifacts) -> None:
        """Role inline via -s. The TUI flag is launch mode, not context — see
        ``get_cli_launch_args`` (HATS-1207: gating CONTEXT must not drop -i)."""
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        self._cache_dir(project_dir, session_id, artifacts)
        prompt = self.build_system_prompt(result)
        prompt = expand_path_placeholders(prompt, project_dir)
        prompt = expand_role_catalog(prompt, project_dir)
        artifacts.full_content = prompt
        artifacts.cli_args.extend(["-s", prompt])

    def _build_context_automate(self, project_dir, result, session_id, artifacts) -> None:
        """Role sections inline in the meta-prompt — no flag."""
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        self._cache_dir(project_dir, session_id, artifacts)
        prompt = self.build_system_prompt(result)
        prompt = expand_path_placeholders(prompt, project_dir)
        prompt = expand_role_catalog(prompt, project_dir)
        artifacts.full_content = prompt

    # -- skills ----------------------------------------------------------------

    def session_skills_root(self, project_dir: Path, session_id: str) -> Path:
        """HATS-1540: what a bound check resolves its script from in-session."""
        from ai_hats.paths import session_cache_dir

        return session_cache_dir(project_dir, session_id) / "skills"

    def _deliver_skills(self, project_dir, result, session_id, artifacts) -> None:
        from ai_hats.skills_dir import inject_skill_paths_to_env, materialize_skills_dir

        cache_dir = self._cache_dir(project_dir, session_id, artifacts)
        skills_dir = self.session_skills_root(project_dir, session_id)
        # Shared with agy (HATS-1271): a private copy drifted and lost the
        # {{backlog_fsm_edges}} expansion the shared one has done since HATS-1051.
        materialize_skills_dir(skills_dir, result.skills, project_dir, artifacts.port)
        # cline scans <T()>/skills; --config sets T()=cache_dir (spike HATS-1191).
        # CLINE_DATA_DIR (get_env) keeps auth/state off this ephemeral base.
        artifacts.cli_args.extend(["--config", str(cache_dir)])
        inject_skill_paths_to_env(artifacts.extra_env, result.skills, skills_dir)
        artifacts.materialized.append(skills_dir)

    def _build_skills_hitl(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_skills(project_dir, result, session_id, artifacts)

    def _build_skills_automate(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_skills(project_dir, result, session_id, artifacts)

    # -- hooks -----------------------------------------------------------------

    def _deliver_hooks(self, project_dir, result, session_id, artifacts) -> None:
        from ai_hats_cline.runtime_hooks import materialize_runtime_hooks

        hooks_dir = materialize_runtime_hooks(
            project_dir,
            result,
            session_id,
            artifacts,
            skills_dir=self.session_skills_root(project_dir, session_id),
        )
        if hooks_dir is not None:
            artifacts.cli_args.extend(["--hooks-dir", str(hooks_dir)])

    def _build_hooks_hitl(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_hooks(project_dir, result, session_id, artifacts)

    def _build_hooks_automate(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_hooks(project_dir, result, session_id, artifacts)

    def build_session_prompt(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        """HITL entry (WrapRunner): thin delegate over the artifact-builder.

        Returns ``(cli_args, extra_env, meta_prompt)``; the third element is the
        exact bytes WrapRunner persists to ``meta_prompt.txt`` (HATS-523).
        """
        artifacts = self.build_session_artifacts(
            project_dir,
            result,
            session_id,
            run_mode=RunMode.HITL,
            artifacts=BuiltArtifacts(),
        )
        return (artifacts.cli_args, artifacts.extra_env, artifacts.full_content or "")

    def materialize_runtime_skills(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> list[str]:
        """Automate entry (SubAgentRunner): thin delegate over the artifact-builder.

        Materializes skills into the per-session cache and returns the CLI args
        (``["--config", <cache>]``) the runner threads onto the cline command —
        so the headless path lands skills in the cache too (no project-root leak).
        """
        artifacts = self.build_session_artifacts(
            project_dir,
            result,
            session_id,
            run_mode=RunMode.AUTOMATE,
            artifacts=BuiltArtifacts(),
        )
        return artifacts.cli_args

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["cline"]
        if args:
            cmd.extend(args)
        return cmd

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
    ) -> list[str]:
        # Strip interactive flags so HITL -i never meets --yolo; other passthrough survives.
        kept = [a for a in (cmd or ["cline"]) if a not in ("-i", "--tui")]
        return [*kept, "--yolo", "--json", meta_prompt]

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        """Pure: the hub port reads as the launch's to pick (HATS-1554)."""
        from ai_hats.session_artifacts import AT_LAUNCH

        return self._env(project_dir, hub_port=AT_LAUNCH)

    def claim_launch_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        """Bind the hub port. Only a real launch may take one."""
        del session_dir
        return {"CLINE_HUB_PORT": str(self._allocate_hub_port())}

    def _env(self, project_dir: Path, *, hub_port: str) -> dict[str, str]:
        """One key list for both modes, so the report cannot name a different set."""
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
        from ai_hats.paths import ai_hats_dir, tool_home

        return {
            ENV_AI_HATS_DIR: str(ai_hats_dir(project_dir)),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
            # Per-session hub port — parallel sessions EADDRINUSE on the default (HATS-973).
            "CLINE_HUB_PORT": hub_port,
            # HATS-1171: --config relocates cline's base dir; pin data (auth /
            # sessions / db) back to the real cline home so auth survives and
            # resolve_transcript still finds the transcript.
            "CLINE_DATA_DIR": str(tool_home("cline", "CLINE_DATA_DIR") / "data"),
        }

    @staticmethod
    def _allocate_hub_port() -> int:
        """Bind :0 and return the assigned port — free at allocation; cline rebinds ms later."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
