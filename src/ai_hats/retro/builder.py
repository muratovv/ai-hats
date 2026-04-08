"""SessionRetroBuilder — build SessionRetroV1 from raw session artifacts.

Two modes:
- programmatic: pure parser, no LLM calls (default for hook flow)
- llm: LLM call for narrative summary + factual observations

Output is written to `.agent/retrospectives/sessions/<mode>/<session_id>.md`
so the two modes do not overwrite each other.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .common import SessionArtifacts, SessionLinks, SessionMetrics
from .llm_caller import LLMCaller
from .loader import load
from .session_retro import SCHEMA_VERSION as SESSION_RETRO_VERSION
from .session_retro import SessionRetroV1
from .writer import dump

SESSION_PREFIX = "session_"


class BuilderMode(str, Enum):
    PROGRAMMATIC = "programmatic"
    LLM = "llm"


class SessionRetroBuilder:
    """Build a SessionRetroV1 from a session directory under .gitlog/."""

    def __init__(
        self,
        project_dir: Path,
        *,
        llm_caller: LLMCaller | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.gitlog_dir = project_dir / ".gitlog"
        self.retros_dir = project_dir / ".agent" / "retrospectives" / "sessions"
        self._llm_caller = llm_caller

    # --- public API ---

    def build(
        self,
        session_id: str,
        *,
        mode: BuilderMode = BuilderMode.PROGRAMMATIC,
    ) -> SessionRetroV1:
        """Build a SessionRetroV1 in the given mode."""
        sid = self._normalize(session_id)
        session_dir = self._session_dir(sid)
        if not session_dir.exists():
            raise FileNotFoundError(f"Session not found: {sid}")

        metrics = self._parse_metrics(session_dir)
        session_start = self._parse_session_start(sid)
        artifacts = self._parse_artifacts(session_start)
        role = self._parse_role(session_dir)
        session_date = session_start.date()

        if mode == BuilderMode.PROGRAMMATIC:
            summary = self._programmatic_summary(metrics, artifacts)
            observations: list[str] = []
        elif mode == BuilderMode.LLM:
            summary, observations = self._llm_summary_and_observations(session_dir, metrics)
        else:
            raise ValueError(f"Unknown mode: {mode}")

        # Paths in links are relative to sessions/<mode>/<id>.md → up 4 levels.
        links = SessionLinks(
            audit=f"../../../../.gitlog/{SESSION_PREFIX}{sid}/audit.md",
            metrics=(
                f"../../../../.gitlog/{SESSION_PREFIX}{sid}/metrics.json"
                if (session_dir / "metrics.json").exists()
                else None
            ),
        )

        return SessionRetroV1(
            schema=SESSION_RETRO_VERSION,
            session_id=sid,
            project=self._project_name(),
            role=role,
            date=session_date,
            metrics=metrics,
            summary=summary,
            artifacts=artifacts,
            observations=observations,
            links=links,
        )

    def build_and_save(
        self,
        session_id: str,
        *,
        mode: BuilderMode = BuilderMode.PROGRAMMATIC,
    ) -> Path:
        """Build, save to sessions/<mode>/<id>.md, validate via loader.

        Each mode writes to its own subdirectory so the two artifact streams
        do not overwrite each other.
        """
        retro = self.build(session_id, mode=mode)
        out_dir = self.retros_dir / mode.value
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{retro.session_id}.md"
        body = self._render_body(retro)
        dump(retro, path, body=body)
        # roundtrip validation
        load(path)
        return path

    # --- private helpers ---

    def _project_name(self) -> str:
        name = self.project_dir.resolve().name
        return name or "project"

    @staticmethod
    def _normalize(session_id: str) -> str:
        if session_id.startswith(SESSION_PREFIX):
            return session_id[len(SESSION_PREFIX):]
        return session_id

    def _session_dir(self, session_id: str) -> Path:
        return self.gitlog_dir / f"{SESSION_PREFIX}{session_id}"

    @staticmethod
    def _parse_session_start(session_id: str) -> datetime:
        """Parse session_id YYYYMMDD-HHMMSS-N → datetime (UTC)."""
        try:
            return datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError as e:
            raise ValueError(f"Cannot parse session start from {session_id!r}") from e

    def _parse_metrics(self, session_dir: Path) -> SessionMetrics:
        metrics_path = session_dir / "metrics.json"
        if not metrics_path.exists():
            return SessionMetrics(exit_code=0, turns=0, tool_calls=0)
        try:
            data = json.loads(metrics_path.read_text())
        except json.JSONDecodeError:
            return SessionMetrics(exit_code=0, turns=0, tool_calls=0)
        tokens = data.get("tokens") or {}
        return SessionMetrics(
            exit_code=int(data.get("exit_code", 0)),
            turns=int(data.get("turns", 0)),
            tool_calls=int(data.get("tool_calls", 0)),
            tokens_in=int(tokens.get("input", 0)),
            tokens_out=int(tokens.get("output", 0)),
            cache_read=int(tokens.get("cache_read", 0)),
            cache_creation=int(tokens.get("cache_creation", 0)),
        )

    def _parse_role(self, session_dir: Path) -> str:
        """Read role from metrics.json or fall back to audit.md header."""
        metrics_path = session_dir / "metrics.json"
        if metrics_path.exists():
            try:
                data = json.loads(metrics_path.read_text())
                role = data.get("role")
                if role:
                    return str(role)
            except json.JSONDecodeError:
                pass
        audit_path = session_dir / "audit.md"
        if audit_path.exists():
            for line in audit_path.read_text().splitlines()[:10]:
                m = re.search(r"\*\*Role\*\*:\s*(\S+)", line)
                if m:
                    return m.group(1)
                m = re.search(r"Role:\s*(\S+)", line)
                if m:
                    return m.group(1)
        return "unknown"

    def _parse_artifacts(self, session_start: datetime) -> SessionArtifacts:
        return SessionArtifacts(
            files_changed=self._files_changed(session_start),
            commits=self._commits_since(session_start),
            tasks_closed=self._tasks_closed_since(session_start),
        )

    def _git(self, args: list[str]) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return proc.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    def _files_changed(self, since: datetime) -> list[str]:
        out = self._git([
            "log",
            f"--since={since.isoformat()}",
            "--name-only",
            "--pretty=format:",
        ])
        files = sorted({line for line in out.splitlines() if line.strip()})
        return files

    def _commits_since(self, since: datetime) -> list[str]:
        out = self._git([
            "log",
            f"--since={since.isoformat()}",
            "--pretty=format:%h %s",
        ])
        return [line for line in out.splitlines() if line.strip()]

    def _tasks_closed_since(self, since: datetime) -> list[str]:
        tasks_dir = self.project_dir / ".agent" / "backlog" / "tasks"
        if not tasks_dir.exists():
            return []
        try:
            from ..models import TaskState
            from ..state import TaskManager
        except ImportError:
            return []
        try:
            tm = TaskManager(self.project_dir)
            done_tasks = tm.list_tasks(state=TaskState.DONE)
        except Exception:
            return []
        closed: list[str] = []
        for task in done_tasks:
            updated = self._parse_task_timestamp(task.updated)
            if updated and updated >= since:
                closed.append(task.id)
        return sorted(closed)

    @staticmethod
    def _parse_task_timestamp(value: str) -> datetime | None:
        if not value:
            return None
        # Accept ISO with or without trailing Z
        try:
            cleaned = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
        # Accept date-only (e.g. "2026-04-08")
        try:
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _programmatic_summary(
        self, metrics: SessionMetrics, artifacts: SessionArtifacts
    ) -> str:
        parts: list[str] = []
        parts.append(
            f"Session of {metrics.turns} turn(s), {metrics.tool_calls} tool call(s)"
        )
        if metrics.exit_code != 0:
            parts.append(f"exit code {metrics.exit_code}")
        if artifacts.files_changed:
            preview = ", ".join(artifacts.files_changed[:5])
            more = (
                f" (+{len(artifacts.files_changed) - 5} more)"
                if len(artifacts.files_changed) > 5
                else ""
            )
            parts.append(f"touched files: {preview}{more}")
        if artifacts.commits:
            parts.append(f"{len(artifacts.commits)} commit(s)")
        if artifacts.tasks_closed:
            parts.append(f"closed tasks: {', '.join(artifacts.tasks_closed)}")
        return ". ".join(parts) + "."

    # --- LLM-mode helpers (Step 5) ---

    def _llm_summary_and_observations(
        self, session_dir: Path, metrics: SessionMetrics
    ) -> tuple[str, list[str]]:
        """Call the LLM caller with SUMMARY_PROMPT and parse the response.

        Implemented in Step 5; raises NotImplementedError until then.
        """
        if self._llm_caller is None:
            raise RuntimeError("llm/hybrid modes require an llm_caller")
        from .prompts import SUMMARY_PROMPT, parse_summary_response

        audit_text = ""
        audit_path = session_dir / "audit.md"
        if audit_path.exists():
            audit_text = audit_path.read_text()
        metrics_text = json.dumps(
            metrics.model_dump(mode="json"), indent=2, sort_keys=True
        )
        prompt = SUMMARY_PROMPT.format(
            audit_text=audit_text or "(no audit)",
            metrics_json=metrics_text,
        )
        if os.environ.get("AI_HATS_NO_LLM") == "1":
            raise RuntimeError("LLM calls disabled by AI_HATS_NO_LLM=1")
        response = self._llm_caller(prompt)
        return parse_summary_response(response)

    @staticmethod
    def _render_body(retro: SessionRetroV1) -> str:
        """Render a minimal markdown body for the retro file."""
        lines: list[str] = [
            f"# Session Retro: {retro.session_id}",
            "",
            f"**Role:** {retro.role}  ",
            f"**Date:** {retro.date.isoformat()}  ",
            f"**Project:** {retro.project}",
            "",
            "## Summary",
            "",
            retro.summary,
            "",
        ]
        if retro.observations:
            lines.append("## Observations")
            lines.append("")
            for obs in retro.observations:
                lines.append(f"- {obs}")
            lines.append("")
        if retro.artifacts.files_changed:
            lines.append("## Files Changed")
            lines.append("")
            for f in retro.artifacts.files_changed:
                lines.append(f"- {f}")
            lines.append("")
        if retro.artifacts.commits:
            lines.append("## Commits")
            lines.append("")
            for c in retro.artifacts.commits:
                lines.append(f"- {c}")
            lines.append("")
        return "\n".join(lines)
