"""Kimi Code surface adapter — maps the `kimi` CLI to the ai-hats `Surface`.

Hook protocol is Claude-Code-compatible, skills ride `--skills-dir` (which
replaces kimi's discovery, so the composed set is exact), the HITL role lands
in `$KIMI_CODE_HOME/AGENTS.md` of the per-session home (kimi injects it), and
headless runs go `kimi --output-format=stream-json -p <prompt>` — `-p` last
with its value (`-p --flag` misparses), and no `--auto`: print mode implies it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING, Mapping

from ai_hats.materialization import describe_mkdir, describe_write_text
from ai_hats.surfaces import Surface
from ai_hats.session_artifacts import RunMode, SessionPolicy

from .. import MetricsSink, SubagentEngine, SurfaceRunResult
from ..mirror import mirror_entries, path_dirs
from ..plan import CompositionPlan, Host, Launch, Launched, MaterializationPlan
from .home import kimi_home

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult
    from ai_hats_observe.canonical.signals import Signal
    from ai_hats_observe.parsers.base import TranscriptParser

    from ai_hats.surfaces import SurfaceHint
    from ai_hats.debt import SessionId

READINESS_SOURCE = "kimi/readiness"


def subprocess_run(*args, **kwargs):
    return subprocess.run(*args, **kwargs)  # noqa: S603 — argv from the plan, not a shell


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

    def transcript_parser(self) -> TranscriptParser:
        # Lazy import: provider discovery must not eager-load observe parsers.
        from .parser import KimiParser

        return KimiParser()

    def readiness_findings(self, environ: Mapping[str, str]) -> "list[Signal]":
        """Probe CLI/credential readiness without reading or changing kimi config."""
        return _readiness_findings(environ)

    def system_prompt_path(self, layout: ProjectLayout) -> Path | None:
        """Kimi receives the role via the session home's AGENTS.md; no project
        file is managed. Managing `<project>/AGENTS.md` too would deliver the
        role twice (kimi injects both files) and leak the managed block into
        claude/codex sessions, which read AGENTS.md natively as well."""
        del layout
        return None

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

        # The session home's sessions/ is a symlink into the global home, so
        # every wire.jsonl — hooked session or not — lands in one place.
        del cwd
        sessions_dir = kimi_home() / "sessions"
        exact = None
        if provider_session_id:
            matches = sorted(sessions_dir.glob(f"*/{provider_session_id}/agents/main/wire.jsonl"))
            exact = matches[0] if matches else None
        return resolve_transcript(
            sessions_dir,
            "*/*/agents/main/wire.jsonl",
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
        from .runtime_hooks import plan_session_home

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
        home_entries, home_env, session_home = plan_session_home(
            composition, root, host, layout=layout, skills_dir=skills_dir
        )
        entries += home_entries
        env.update(home_env)
        context = None
        if policy.context and mode is RunMode.HITL:
            # Kimi injects $KIMI_CODE_HOME/AGENTS.md into every session's
            # prompt — the composed role rides it (verified against the live CLI).
            context = session_home / "AGENTS.md"
            entries.append(describe_write_text(context, prompt.text))
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
            context=context,
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
        # `-p` already runs with auto permissions; an explicit `--auto`/`--yolo`
        # is a startup error in print mode.
        kept = [a for a in (cmd or ["kimi"]) if a not in ("--yolo", "-y", "--auto")]
        return [*kept, "--output-format=stream-json", "-p", meta_prompt]

    def get_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR

        return {
            ENV_AI_HATS_DIR: str(layout.base),
            AI_HATS_PROJECT_DIR_ENV: str(layout.root),
        }

    def engine(self) -> "SubagentEngine | None":
        return KimiSubagentEngine()


class KimiSubagentEngine(SubagentEngine):
    """Drives `kimi -p --output-format=stream-json` as a subprocess.

    Kimi mints its own session id (no flag to pass one), so the runner-minted
    id is ignored per the contract and the real one is reported back from the
    stream's ``session.resume_hint`` meta line.
    """

    def __init__(self, *, run=subprocess_run) -> None:
        # The subprocess call is a seam: a test drives the engine by handing in
        # its own, instead of patching the module under test.
        self._run = run

    def run(
        self,
        *,
        layout: ProjectLayout,
        work_dir: Path,
        session_id: SessionId,
        env: dict[str, str],
        model: str | None,
        timeout_s: int,
        metrics: MetricsSink,
        launched: Launched,
        brief: str | None,
        provider_session_id: str | None = None,
        event_log: Path | None = None,
    ) -> SurfaceRunResult:
        del layout, session_id, model, brief, provider_session_id, event_log
        cmd = list(launched.args or ())
        try:
            proc = self._run(
                cmd,
                cwd=work_dir,
                # `env` is ai-hats' ADDITIONS, not a whole environment — merge
                # over the inherited one or the child has no PATH at all.
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            return SurfaceRunResult(
                exit_code=124,
                # TimeoutExpired carries BYTES even under text=True on POSIX.
                stdout=_stream_text(_as_text(exc.stdout)),
                stderr=_as_text(exc.stderr),
                timed_out=True,
                error=f"kimi -p timed out after {timeout_s}s",
            )
        except OSError as exc:
            return SurfaceRunResult(
                exit_code=127, stdout="", stderr="", timed_out=False, error=str(exc)
            )

        stream = _stream_lines(proc.stdout or "")
        found = next(
            (
                str(obj["session_id"])
                for obj in stream
                if obj.get("role") == "meta"
                and obj.get("type") == "session.resume_hint"
                and obj.get("session_id")
            ),
            None,
        )
        metrics.record(
            {
                # the generic key the finalize plumbing reads for the resolver
                "provider_session_id": found or "",
                "kimi_session_id": found or "",
                "num_turns": sum(1 for obj in stream if obj.get("role") == "assistant"),
                "stop_reason": "completed" if proc.returncode == 0 else "error",
            }
        )
        return SurfaceRunResult(
            exit_code=proc.returncode,
            stdout="".join(
                str(obj.get("content") or "")
                for obj in stream
                if obj.get("role") == "assistant" and isinstance(obj.get("content"), str)
            ),
            stderr=proc.stderr or "",
            timed_out=False,
            error=None if proc.returncode == 0 else f"kimi -p exited {proc.returncode}",
        )


def _as_text(raw: str | bytes | None) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw or ""


def _stream_lines(raw: str) -> list[dict]:
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _stream_text(raw: str) -> str:
    """The assistant text of a stream-json run — what the transcript records."""
    return "".join(
        str(obj.get("content") or "")
        for obj in _stream_lines(raw)
        if obj.get("role") == "assistant" and isinstance(obj.get("content"), str)
    )
