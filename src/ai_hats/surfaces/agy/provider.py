"""Agy surface adapter — maps the `agy` (Antigravity) CLI to the ai-hats `Surface`.

The session is planned (ADR-0036) under the out-of-project per-session cache
``<sc>`` = ``<cache_root>/sessions/<sid>/``: the role prompt at
``<sc>/rules/GEMINI.md`` (passed via ``--add-dir``), the skill mirror at
``<sc>/rules/.agents/skills/``, the session hooks at ``<sc>/hooks.json`` and the
global dispatcher merged into ``~/.gemini/antigravity-cli/settings.json``. Root
``GEMINI.md`` is untouched; zero files are created in the project root.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING, Generator

from .global_hook import plan_global_hook

from ai_hats.env import ENV_AI_HATS_PYTHON, ENV_SESSION_CACHE_DIR
from ai_hats.materialization import describe_mkdir, describe_write_text
from ai_hats.paths import (
    AI_HATS_PROJECT_DIR_ENV,
    ENV_AI_HATS_DIR,
    GEMINI_MD_FILENAME,
    gemini_md,
)
from ai_hats.surfaces import Surface
from ai_hats.session_artifacts import RunMode, SessionPolicy

from ..mirror import manifest_rows, mirror_entries, path_dirs
from ..plan import CompositionPlan, Host, Launch, MaterializationPlan


def agy_user_settings_json() -> Path:
    from ai_hats.paths._discovery import tool_home

    return tool_home("gemini", "GEMINI_CONFIG_DIR") / "antigravity-cli" / "settings.json"


if TYPE_CHECKING:
    from ai_hats_core import CompositionResult
    from ai_hats.surfaces import SurfaceHint


class AgySurface(Surface):
    """`agy` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "agy"

    def detected_home_dirs(self) -> list[str]:
        return [".gemini", ".agy"]

    def surface_hints(self) -> list["SurfaceHint"]:
        from ai_hats.surfaces import SurfaceHint

        return [
            SurfaceHint(
                name="--model",
                values="gemini-2.5-pro, ...",
                description="Overrides the model to use for the session.",
            ),
            SurfaceHint(
                name="--headless",
                values="N/A",
                description="Run agy in headless mode without TUI.",
            ),
        ]

    def transcript_parser(self):
        from .parser import AgyParser

        return AgyParser()

    def resolve_transcript(
        self,
        cwd: Path,
        session_id: str,
        *,
        provider_session_id: str | None = None,
        end_ts: float | None = None,
    ) -> list[Path]:
        from ai_hats.paths import resolve_transcript, tool_home

        del cwd  # agy keys its brain by session, not by where it ran
        brain_dir = tool_home("gemini", "GEMINI_CONFIG_DIR") / "antigravity-cli" / "brain"
        exact_path = (
            brain_dir / provider_session_id / ".system_generated" / "logs" / "transcript.jsonl"
            if provider_session_id
            else None
        )
        return resolve_transcript(
            brain_dir,
            "*/.system_generated/logs/transcript.jsonl",
            session_id,
            exact_path=exact_path,
            end_ts=end_ts,
        )

    def system_prompt_path(self, layout: ProjectLayout) -> Path | None:
        project_dir = layout.root
        return gemini_md(project_dir)

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    @contextmanager
    def execution_context(self, layout: ProjectLayout) -> Generator[None, None, None]:
        """Clean execution context — file-hiding hacks retired (native-by-default)."""
        yield

    def build_system_prompt(self, result: CompositionResult) -> str:
        # Skills reach agy via the native .agy/skills/ registry
        return self._compose_sections(result)

    def _session_skills_dir(self, layout: ProjectLayout, session_id: str) -> Path:
        return self._session_skills_dir_under(layout.cache.session(session_id))

    def session_skills_root(self, layout: ProjectLayout, session_id: str) -> Path:
        """What a bound check resolves its script from in-session."""
        return self._session_skills_dir(layout, session_id)

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
        mode = RunMode(run_mode)
        rules_dir = root / "rules"
        skills_dir = self._session_skills_dir_under(root)
        prompt = composition.prompt  # agy adds no block of its own
        entries = [describe_mkdir(root)]
        args: list[str] = []
        env: dict[str, str] = {}
        context: Path | None = None
        # HITL reads the rules dir; a sub-agent takes the same text in its
        # prompt token, so nothing of it lands on disk.
        if policy.context and mode is RunMode.HITL:
            context = rules_dir / GEMINI_MD_FILENAME
            entries += [describe_mkdir(rules_dir), describe_write_text(context, prompt.text)]
            args += ["--add-dir", str(rules_dir)]
        if composition.skills:
            entries.append(describe_mkdir(skills_dir))
            entries += mirror_entries(composition, skills_dir)
            if dirs := path_dirs(composition, skills_dir):
                env["PATH"] = os.pathsep.join([*(str(d) for d in dirs), host.path])
        if policy.hooks:
            env[ENV_SESSION_CACHE_DIR] = str(root)
            entries.append(plan_global_hook(agy_user_settings_json()))
            entries.append(
                describe_write_text(
                    root / "hooks.json",
                    json.dumps(manifest_rows(composition, skills_dir), indent=2) + "\n",
                )
            )
        env.update(self._env(layout, python=str(host.python)))
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
            context=context,
        )

    @staticmethod
    def _session_skills_dir_under(root: Path) -> Path:
        return root / "rules" / ".agents" / "skills"

    def ensure_runtime_hooks(
        self, layout: ProjectLayout, result: CompositionResult | None = None, **kwargs
    ) -> None:
        """Runtime hooks ride the plan's session ``hooks.json``; nothing at the project."""
        pass

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["agy"]
        if args:
            cmd.extend(args)
        return cmd

    def get_cli_launch_args(
        self, base_cmd: list[str], session_id: str, is_resume: bool
    ) -> list[str]:
        """Convert positional prompt text in `base_cmd` into `-i <prompt>` for interactive agy sessions."""
        del session_id, is_resume
        if not base_cmd or len(base_cmd) <= 1:
            return base_cmd

        prompt_flags = {"-i", "--prompt-interactive", "-p", "--print", "--prompt"}
        if any(arg in prompt_flags for arg in base_cmd):
            return base_cmd

        flags_with_val = {
            "--add-dir",
            "--agent",
            "--effort",
            "--log-file",
            "--mode",
            "--model",
            "--print-timeout",
            "--project",
            "--conversation",
        }

        executable = base_cmd[0]
        args = base_cmd[1:]

        other_tokens: list[str] = []
        positional_prompt: list[str] = []

        i = 0
        while i < len(args):
            token = args[i]
            if token in flags_with_val:
                other_tokens.append(token)
                if i + 1 < len(args):
                    other_tokens.append(args[i + 1])
                    i += 1
            elif token.startswith("-"):
                other_tokens.append(token)
            else:
                positional_prompt.append(token)
            i += 1

        if not positional_prompt:
            return base_cmd

        prompt_str = " ".join(positional_prompt)
        return [executable, "-i", prompt_str, *other_tokens]

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
    ) -> list[str]:
        return cmd + ["--output-format", "json", "-p", meta_prompt]

    def get_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        import sys

        del session_dir
        return self._env(layout, python=sys.executable)

    @staticmethod
    def _env(layout: ProjectLayout, *, python: str) -> dict[str, str]:
        """One key list for the builder and the plan; the plan names the host's interpreter."""
        return {
            ENV_AI_HATS_DIR: str(layout.base),
            AI_HATS_PROJECT_DIR_ENV: str(layout.root),
            ENV_AI_HATS_PYTHON: python,
        }
