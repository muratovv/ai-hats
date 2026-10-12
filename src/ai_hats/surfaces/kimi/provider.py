"""Kimi Code surface adapter — maps the `kimi` CLI to the ai-hats `Surface`.

Hook protocol is Claude-Code-compatible, skills ride `--skills-dir` (which
replaces kimi's discovery, so the composed set is exact), the HITL role lands
in the managed `AGENTS.md` block, and headless runs go
`kimi --auto --output-format=stream-json -p <prompt>` — `-p` last with its
value: `-p --flag` misparses.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING, Mapping

from ai_hats.materialization import describe_mkdir
from ai_hats.surfaces import Surface
from ai_hats.session_artifacts import RunMode, SessionPolicy

from ..mirror import mirror_entries, path_dirs
from ..plan import CompositionPlan, Host, Launch, MaterializationPlan

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult
    from ai_hats_observe.canonical.signals import Signal

    from ai_hats.surfaces import SurfaceHint

KIMI_HOME_ENV = "KIMI_CODE_HOME"
READINESS_SOURCE = "kimi/readiness"


def kimi_home() -> Path:
    """``$KIMI_CODE_HOME`` or ``~/.kimi-code`` — kimi's own relocation rule."""
    from ai_hats.paths import tool_home

    return tool_home("kimi-code", KIMI_HOME_ENV)


def _readiness_findings(
    environ: Mapping[str, str], *, which=shutil.which, home: Path | None = None
) -> "list[Signal]":
    from ai_hats_observe.canonical.signals import (
        Notice,
        PersonActionRequired,
        PersonMustAct,
        WorthRecording,
    )
    from ai_hats_observe.canonical.types import now

    if not which("kimi", path=environ.get("PATH")):
        return [
            PersonActionRequired(
                reason=PersonMustAct.INSTALL,
                detail="Kimi Code CLI is not on PATH. Install it, then run `kimi --version` and `kimi login`.",
                source=READINESS_SOURCE,
                ts=now(),
            )
        ]
    kimi_home_dir = home if home is not None else kimi_home()
    credentials = kimi_home_dir / "credentials"
    if not credentials.is_dir() or not any(credentials.glob("*.json")):
        return [
            PersonActionRequired(
                reason=PersonMustAct.REAUTHENTICATE,
                detail="No Kimi OAuth credentials found. Run `kimi login` (international accounts need KIMI_OAUTH_HOST=https://auth.kimi.ai).",
                source=READINESS_SOURCE,
                ts=now(),
            )
        ]
    if not (kimi_home_dir / "config.toml").is_file():
        return [
            Notice(
                reason=WorthRecording.SURFACE_WARNING,
                detail="Kimi config.toml missing; kimi will create defaults on first run.",
                source=READINESS_SOURCE,
                ts=now(),
            )
        ]
    return []


class KimiSurface(Surface):
    """`kimi` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "kimi"

    def detected_home_dirs(self) -> list[str]:
        return [".kimi-code"]

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
                description="Auto-approve regular tool calls; the agent may still ask questions.",
            ),
        ]

    def readiness_findings(self, environ: Mapping[str, str]) -> "list[Signal]":
        """Probe CLI/credential readiness without reading or changing kimi config."""
        return _readiness_findings(environ)

    def system_prompt_path(self, layout: ProjectLayout) -> Path | None:
        """Kimi reads the project AGENTS.md natively — the managed block lands there."""
        return layout.root / "AGENTS.md"

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: "CompositionResult") -> str:
        return self._compose_sections(result)

    def session_skills_root(self, layout: ProjectLayout, session_id: str) -> Path:
        return layout.cache.session(session_id) / "skills"

    def resolve_transcript(
        self,
        cwd: Path,
        session_id: str,
        *,
        provider_session_id: str | None = None,
        end_ts: float | None = None,
    ) -> list[Path]:
        from ai_hats.paths import resolve_transcript

        del cwd  # kimi keys sessions by working directory, ours is identified by id
        sessions_dir = kimi_home() / "sessions"
        exact = None
        if provider_session_id:
            matches = sorted(sessions_dir.glob(f"*/{provider_session_id}/agents/main/wire.jsonl"))
            exact = matches[0] if matches else None
        return resolve_transcript(
            sessions_dir,
            "*/agents/main/wire.jsonl",
            session_id,
            exact_path=exact,
            end_ts=end_ts,
        )

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
        skills_dir = root / "skills"
        prompt = composition.prompt  # kimi adds no block of its own
        entries = [describe_mkdir(root), describe_mkdir(skills_dir)]
        entries += mirror_entries(composition, skills_dir)
        # --skills-dir REPLACES kimi's skill discovery, so the mirrored set is
        # exactly the composed one — nothing user-global leaks into the session.
        args = ["--skills-dir", str(skills_dir)]
        env: dict[str, str] = {}
        if dirs := path_dirs(composition, skills_dir):
            env["PATH"] = os.pathsep.join([*(str(d) for d in dirs), host.path])
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

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["kimi"]
        if args:
            cmd.extend(args)
        return cmd

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
    ) -> list[str]:
        # Flags first, `-p` last with its value: kimi misparses `-p --flag`.
        kept = [a for a in (cmd or ["kimi"]) if a not in ("--yolo", "-y")]
        return [*kept, "--auto", "--output-format=stream-json", "-p", meta_prompt]

    def get_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR

        return {
            ENV_AI_HATS_DIR: str(layout.base),
            AI_HATS_PROJECT_DIR_ENV: str(layout.root),
        }
