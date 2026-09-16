"""Cline surface adapter — maps the `cline` CLI to the ai-hats `Surface`.

cline's session is planned (ADR-0036) on the clean-root invariant — skills
mirror into the per-session cache and reach cline via ``--config`` (a spike);
nothing lands in the project root. Native ``--hooks-dir`` entrypoints deliver
the composed per-tool runtime-hook chain from the same session cache.
"""

from __future__ import annotations

import os
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING

from ai_hats.materialization import describe_mkdir
from ai_hats.surfaces import Surface
from ai_hats.session_artifacts import RunMode, SessionPolicy

from ..mirror import mirror_entries, path_dirs
from ..plan import CompositionPlan, Host, Launch, MaterializationPlan

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult
    from ai_hats_observe.parsers.base import TranscriptParser

    from ai_hats.surfaces import SurfaceHint


class ClineSurface(Surface):
    """`cline` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "cline"

    def supports_session_command_wrappers(self) -> bool:
        return True

    def surface_hints(self) -> list["SurfaceHint"]:
        from ai_hats.surfaces import SurfaceHint

        return [
            SurfaceHint(
                name="--model",
                values="<model>",
                description="Override the model for this session.",
            ),
            SurfaceHint(
                name="--yolo",
                values="N/A",
                description="Run cline in fully autonomous mode without asking for permission.",
            ),
        ]

    def transcript_parser(self) -> TranscriptParser:
        # Lazy import: provider discovery must not eager-load observe parsers.
        from .parser import ClineParser

        return ClineParser()

    def resolve_transcript(
        self,
        cwd: Path,
        session_id: str,
        *,
        provider_session_id: str | None = None,
        end_ts: float | None = None,
    ) -> list[Path]:
        from ai_hats.paths import resolve_transcript, tool_home

        del cwd  # cline keys its sessions by id, not by where it ran
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

    def system_prompt_path(self, layout: ProjectLayout) -> Path | None:
        # Inline-only surface — no root file managed.
        del layout
        return None

    def update_system_prompt(self, layout: ProjectLayout, content: str) -> Path | None:
        # Inline-only surface: set_role must not write a CLINE.md cline would ignore.
        del layout, content
        return None

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        # Skills reach cline natively via <cache>/skills/ — a text index duplicates them.
        return self._compose_sections(result)

    # -- the plan (ADR-0036 D2): entries, env and launch from the composition half --

    def plan(
        self,
        composition: CompositionPlan,
        *,
        run_mode: RunMode,
        policy: SessionPolicy,
        root: Path,
        layout: ProjectLayout,
        host: Host,
    ) -> MaterializationPlan:
        from .runtime_hooks import plan_hooks

        mode = RunMode(run_mode)
        skills_dir = root / "skills"
        prompt = composition.prompt  # cline adds no block of its own
        entries = [describe_mkdir(root)]
        args: list[str] = []
        env: dict[str, str] = {}
        # HITL takes the role inline; a sub-agent takes the same text in its
        # prompt token, so no flag and nothing on disk carry it.
        if policy.context and mode is RunMode.HITL:
            args += ["-s", prompt.text]
        # cline scans <T()>/skills and --config sets T() to the root (a spike),
        # so the mirror dir and the flag are there with or without skills.
        entries.append(describe_mkdir(skills_dir))
        entries += mirror_entries(composition, skills_dir)
        args += ["--config", str(root)]
        if dirs := path_dirs(composition, skills_dir):
            env["PATH"] = os.pathsep.join([*(str(d) for d in dirs), host.path])
        if policy.hooks and (
            hooked := plan_hooks(composition, root, host, layout=layout, skills_dir=skills_dir)
        ):
            hook_entries, hook_env = hooked
            entries += hook_entries
            env.update(hook_env)
            args += ["--hooks-dir", str(root / "hooks")]
        env.update(self.get_env(root, layout))
        return MaterializationPlan(
            composition=composition,
            prompt=prompt,
            surface=self.name,
            run_mode=mode,
            policy=policy,
            root=root,
            entries=tuple(entries),
            env=env,
            launch=Launch(args=tuple(args), sdk_options=None),
        )

    def get_cli_launch_args(
        self, base_cmd: list[str], session_id: str, is_resume: bool
    ) -> list[str]:
        cmd = list(base_cmd)
        if "-i" not in cmd and "--yolo" not in cmd:
            cmd.insert(1, "-i")
        return super().get_cli_launch_args(cmd, session_id, is_resume)

    def session_skills_root(self, layout: ProjectLayout, session_id: str) -> Path:
        """What a bound check resolves its script from in-session."""

        return layout.cache.session(session_id) / "skills"

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

    def get_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        """Pure: the hub port reads as the launch's to pick."""
        from ai_hats.session_artifacts import AT_LAUNCH

        return self._env(layout, hub_port=AT_LAUNCH)

    def claim_launch_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        """Bind the hub port. Only a real launch may take one."""
        del session_dir
        return {"CLINE_HUB_PORT": str(self._allocate_hub_port())}

    def _env(self, layout: ProjectLayout, *, hub_port: str) -> dict[str, str]:
        """One key list for both modes, so the report cannot name a different set."""
        project_dir = layout.root
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
        from ai_hats.paths import tool_home

        return {
            ENV_AI_HATS_DIR: str(layout.base),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
            # Per-session hub port — parallel sessions EADDRINUSE on the default.
            "CLINE_HUB_PORT": hub_port,
            # --config relocates cline's base dir; pin data (auth /
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
