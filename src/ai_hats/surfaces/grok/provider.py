"""Grok surface adapter — maps the `grok` CLI to the ai-hats `Surface`.

The HITL role lands in ``$GROK_HOME/rules/role.md`` of the per-session home
(grok loads every file there). It is not also written as a project AGENTS.md:
grok reads that file too, and so do claude and codex. Headless runs go
``grok --output-format streaming-messages-json --always-approve -p <prompt>``
with ``-p`` last. ``--always-approve`` stays: without it a gated tool is
refused and the process still exits 0, so a gate would look like success.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING, Callable, Mapping, Sequence

from ai_hats.materialization import describe_mkdir, describe_write_text
from ai_hats.session_artifacts import RunMode, SessionPolicy, working_directory_section
from ai_hats.surfaces import Surface

from .. import MetricsSink, SubagentEngine, SurfaceRunResult
from ..mirror import mirror_entries, path_dirs
from ..plan import CompositionPlan, Host, Launch, Launched, MaterializationPlan, context_text
from .home import GROK_HOME_ENV, grok_home

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult
    from ai_hats_observe.canonical.signals import Signal
    from ai_hats_observe.parsers.base import TranscriptParser

    from ai_hats.debt import SessionId
    from ai_hats.surfaces import SurfaceHint
    from ai_hats.surfaces.plan import LaunchFlags

READINESS_SOURCE = "grok/readiness"
_RESUME_FLAGS = ("--resume", "-r", "--continue", "-c")


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def _is_resume(args: Sequence[str]) -> bool:
    return any(str(arg).partition("=")[0] in _RESUME_FLAGS for arg in args)


def _readiness_findings(
    environ: Mapping[str, str], *, which=shutil.which, home: Path | None = None
) -> "list[Signal]":
    from ai_hats_observe.canonical.signals import PersonActionRequired, PersonMustAct
    from ai_hats_observe.canonical.types import now

    if not which("grok", path=environ.get("PATH")):
        return [
            PersonActionRequired(
                reason=PersonMustAct.INSTALL,
                detail=(
                    "grok CLI is not on PATH. Install it, then run `grok --version`. "
                    "A session token in ~/.grok/auth.json or XAI_API_KEY authenticates it."
                ),
                source=READINESS_SOURCE,
                ts=now(),
            )
        ]
    grok_home_dir = home if home is not None else grok_home()
    if (grok_home_dir / "auth.json").is_file() or environ.get("XAI_API_KEY"):
        return []
    return [
        PersonActionRequired(
            reason=PersonMustAct.REAUTHENTICATE,
            detail=("No grok credentials found. Run `grok` once to sign in, or set XAI_API_KEY."),
            source=READINESS_SOURCE,
            ts=now(),
        )
    ]


class GrokSurface(Surface):
    """`grok` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "grok"

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
        ]

    def transcript_parser(self) -> "TranscriptParser":
        from .parser import GrokParser

        return GrokParser()

    def readiness_findings(self, environ: Mapping[str, str]) -> "list[Signal]":
        """Probe the real grok home, not the per-session one this surface builds."""
        return _readiness_findings(environ)

    def system_prompt_path(self, layout: ProjectLayout) -> Path | None:
        """The role rides the session home's rules file. A project AGENTS.md
        would deliver it twice and leak into surfaces that read that file."""
        del layout
        return None

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: "CompositionResult") -> str:
        return self._compose_sections(result)

    def session_skills_root(self, layout: ProjectLayout, session_id: str) -> Path:
        return layout.cache.session(session_id) / "skills"

    def retain_transcript(self, session_home: Path, cwd: Path, provider_session_id: str) -> None:
        """Copy the session directory into the real grok home before the cache goes."""
        if not provider_session_id:
            return
        from urllib.parse import quote

        from .runtime_hooks import _scope_root

        matches = [
            path
            for path in (session_home / "sessions").glob(f"*/{provider_session_id}/updates.jsonl")
            if path.is_file()
        ]
        if not matches:
            return
        source = matches[0].parent
        remote = (
            grok_home() / "sessions" / quote(str(_scope_root(cwd)), safe="") / provider_session_id
        )
        if source.resolve() == remote.resolve():
            return
        _copy_missing(source, remote)

    def resolve_transcript(
        self,
        cwd: Path,
        session_id: str,
        *,
        provider_session_id: str | None = None,
        end_ts: float | None = None,
    ) -> list[Path]:
        from ai_hats.paths import resolve_transcript

        # The transcript directory is published under the real home. The parent
        # does not inherit the child's GROK_HOME.
        del cwd
        sessions_dir = grok_home() / "sessions"
        exact = None
        if provider_session_id:
            matches = sorted(sessions_dir.glob(f"*/{provider_session_id}/updates.jsonl"))
            exact = matches[0] if matches else None
        return resolve_transcript(
            sessions_dir,
            "*/*/updates.jsonl",
            session_id,
            exact_path=exact,
            end_ts=end_ts,
        )

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
        prompt = composition.prompt
        entries = [describe_mkdir(root), describe_mkdir(skills_dir)]
        entries += mirror_entries(composition, skills_dir)
        env: dict[str, str] = {}
        if dirs := path_dirs(composition, skills_dir):
            env["PATH"] = os.pathsep.join([*(str(d) for d in dirs), host.path])
        home_entries, home_env, session_home = plan_session_home(
            composition,
            root,
            host,
            layout=layout,
            skills_dir=skills_dir,
            hooks_enabled=policy.hooks,
        )
        entries += home_entries
        env.update(home_env)
        context = None
        if policy.context and mode is RunMode.HITL:
            context = session_home / "rules" / "role.md"
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
            launch=Launch(args=(), sdk_options=None),
            context=context,
        )

    def automate_launch(
        self,
        plan: MaterializationPlan,
        flags: "LaunchFlags",
        env: Mapping[str, str],
        *,
        layout: ProjectLayout,
    ) -> Launched:
        """The plan's argv, the minted session id, then one prompt token.

        The default launch never puts the id on the command line, and a grok
        transcript is filed under the id grok was given. Without it the runner
        records an id the resolver cannot find.
        """
        prompt = "\n\n".join(
            part
            for part in (
                context_text(plan),
                working_directory_section(layout),
                flags.brief or "",
            )
            if part
        )
        model = self.model_flags(flags.model) if flags.model else []
        cmd = self.get_cli_command() + list(plan.launch.args or ()) + model
        cmd = self.get_cli_launch_args(
            cmd, flags.provider_session_id or "", _is_resume(flags.extra_args)
        )
        return Launched(
            args=tuple(self.get_run_command(cmd, prompt)),
            sdk_options=None,
            env=env,
            prompt=prompt,
        )

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["grok"]
        if args:
            cmd.extend(args)
        return cmd

    def prepare_launch(
        self,
        plan: MaterializationPlan,
        flags: "LaunchFlags",
        *,
        layout: ProjectLayout,
    ) -> None:
        """Link transcripts already in the real home. A fresh directory needs a uuid."""
        from .runtime_hooks import bind_session_store

        home = plan.env.get(GROK_HOME_ENV)
        if not home:
            return
        session_id = flags.provider_session_id or ""
        known = _is_uuid(session_id)
        resume = _is_resume(flags.extra_args)
        # "<assigned at launch>" is not a session. An empty id is ACP: link what exists.
        if not known and not resume and session_id:
            return
        cwd = flags.work_dir if flags.work_dir is not None else layout.cwd
        bind_session_store(
            Path(home),
            Path(cwd),
            session_id if known else None,
            fresh=known and not resume,
            create_remote=known or resume,
        )

    def get_cli_launch_args(
        self, base_cmd: list[str], session_id: str, is_resume: bool
    ) -> list[str]:
        # Fresh UUID only, before the positional: usage is [OPTIONS] [PROMPT].
        # Resume already names a session; pairing --session-id with it is an error.
        if is_resume or not _is_uuid(session_id):
            return base_cmd
        return [base_cmd[0], "--session-id", session_id, *base_cmd[1:]]

    def get_run_command(self, cmd: list[str], meta_prompt: str) -> list[str]:
        # Flags first, ``-p`` last: a value that looks like a flag is the prompt.
        kept = list(cmd or ["grok"])
        return [
            *kept,
            "--output-format",
            "streaming-messages-json",
            "--always-approve",
            "-p",
            meta_prompt,
        ]

    def get_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR

        del session_dir
        env = {
            ENV_AI_HATS_DIR: str(layout.base),
            AI_HATS_PROJECT_DIR_ENV: str(layout.root),
        }
        key = os.environ.get("XAI_API_KEY")
        if key:
            env["XAI_API_KEY"] = key
        return env

    def engine(self) -> "SubagentEngine | None":
        return GrokSubagentEngine()


class GrokSubagentEngine(SubagentEngine):
    """Drives ``grok -p --output-format streaming-messages-json`` as a subprocess.

    The runner mints the id and ``automate_launch`` puts it on ``--session-id``.
    The stream's own ``session_id`` wins when grok reports one: that is the
    directory the transcript was written under.
    """

    def __init__(self, *, popen=subprocess.Popen) -> None:
        self._popen = popen

    def run(
        self,
        *,
        layout: ProjectLayout,
        work_dir: Path,
        session_id: "SessionId",
        env: dict[str, str],
        model: str | None,
        timeout_s: int,
        metrics: MetricsSink,
        launched: Launched,
        brief: str | None,
        provider_session_id: str | None = None,
        event_log: Path | None = None,
        on_spawn: Callable[[int], None] | None = None,
    ) -> SurfaceRunResult:
        del layout, session_id, model, brief, event_log
        cmd = list(launched.args or ())
        try:
            proc = self._popen(
                cmd,
                cwd=work_dir,
                env={**os.environ, **env},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            return SurfaceRunResult(
                exit_code=127, stdout="", stderr="", timed_out=False, error=str(exc)
            )
        if on_spawn is not None:
            on_spawn(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            stream = _stream_lines(stdout or "")
            _record_stream_metrics(
                metrics, stream, stop_reason="timeout", fallback=provider_session_id
            )
            return SurfaceRunResult(
                exit_code=124,
                stdout=_assistant_text(stream),
                stderr=stderr or "",
                timed_out=True,
                error=f"grok -p timed out after {timeout_s}s",
            )

        stream = _stream_lines(stdout or "")
        _record_stream_metrics(
            metrics,
            stream,
            stop_reason="completed" if proc.returncode == 0 else "error",
            fallback=provider_session_id,
        )
        return SurfaceRunResult(
            exit_code=proc.returncode,
            stdout=_assistant_text(stream),
            stderr=stderr or "",
            timed_out=False,
            error=None if proc.returncode == 0 else f"grok -p exited {proc.returncode}",
        )


def _stream_lines(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("{") and text.endswith("}") and "\n" not in text:
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            return [obj]
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


def _assistant_text(stream: list[dict]) -> str:
    parts: list[str] = []
    for obj in stream:
        if obj.get("type") != "assistant":
            continue
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
    if parts:
        return "".join(parts)
    for obj in stream:
        if isinstance(obj.get("result"), str):
            return str(obj["result"])
        if isinstance(obj.get("text"), str):
            return str(obj["text"])
    return ""


def _copy_missing(source: Path, remote: Path) -> None:
    """Copy files the real home does not have yet. An existing file stays."""
    for path in source.rglob("*"):
        target = remote / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            target.symlink_to(path.readlink())
            continue
        shutil.copy2(path, target)


def _session_id(stream: list[dict], fallback: str | None) -> str:
    for obj in reversed(stream):
        for key in ("session_id", "sessionId"):
            if obj.get(key):
                return str(obj[key])
    return fallback or ""


def _record_stream_metrics(
    metrics: MetricsSink,
    stream: list[dict],
    *,
    stop_reason: str,
    fallback: str | None,
) -> None:
    found = _session_id(stream, fallback)
    result = next((obj for obj in stream if obj.get("type") == "result"), None)
    turns = result.get("num_turns") if isinstance(result, dict) else None
    if not isinstance(turns, int):
        turns = sum(1 for obj in stream if obj.get("type") == "assistant")
    reason = stop_reason
    if isinstance(result, dict) and result.get("stop_reason"):
        reason = str(result["stop_reason"])
    metrics.record(
        {
            "provider_session_id": found,
            "grok_session_id": found,
            "num_turns": turns,
            "stop_reason": reason,
        }
    )
