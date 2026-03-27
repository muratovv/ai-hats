"""Feedback loops — retrospective, judge, long-cycle analysis."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from .observe import Session, SessionManager


RETRO_TEMPLATE = """\
# Retrospective: {session_id}

## What was done
{what_done}

## What went wrong
{what_wrong}

### Root cause analysis
{root_cause}

## Time spent on problems
{time_spent}

## Attempts before resolution
{attempts}

## Who helped
{who_helped}

## Process improvements
{improvements}
"""

JUDGE_TEMPLATE = """\
# Judge Verdict: {session_id}

## Session Info
- **Role**: {role}
- **Provider**: {provider}
- **Date**: {date}

## Quality Assessment
{assessment}

## Issues Found
{issues}

## Recommendations
{recommendations}

## Score
{score}/10
"""


class RetroGenerator:
    """Generates retrospective documents for sessions."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.session_mgr = SessionManager(project_dir)

    def generate(self, session_id: str | None = None) -> Path:
        """Generate a retrospective for a session.

        Returns the path to the generated retro.md.
        If session_id is None, uses the most recent session.
        """
        if session_id:
            session = self.session_mgr.get_session(session_id)
        else:
            sessions = self.session_mgr.list_sessions(last_n=1)
            session = sessions[0] if sessions else None

        if session is None:
            raise ValueError("No session found for retrospective")

        # Collect session data
        trace_data = self._read_trace(session)
        audit_data = self._read_audit(session)

        # Generate retro content (placeholder — in production, this would use an LLM)
        retro_content = RETRO_TEMPLATE.format(
            session_id=session.session_id,
            what_done=audit_data.get("events", "No audit data available"),
            what_wrong="To be filled by analysis agent",
            root_cause="To be filled by analysis agent",
            time_spent="To be calculated from trace timestamps",
            attempts="To be extracted from trace",
            who_helped="To be determined from trace",
            improvements="To be suggested by analysis agent",
        )

        # Save retro — try to attach to task if possible
        retro_path = self._find_retro_target(session)
        retro_path.write_text(retro_content)
        return retro_path

    def _read_trace(self, session: Session) -> str:
        if session.trace_path.exists():
            return session.trace_path.read_text()
        return ""

    def _read_audit(self, session: Session) -> dict:
        if session.audit_path.exists():
            return {"events": session.audit_path.read_text()}
        return {}

    def _find_retro_target(self, session: Session) -> Path:
        """Find where to save the retro — in task dir if task context exists, else session dir."""
        # Check if session has a task association
        env_file = session.session_dir / "env.yaml"
        if env_file.exists():
            env_data = yaml.safe_load(env_file.read_text()) or {}
            task_id = env_data.get("task_id")
            if task_id:
                task_retro = (
                    self.project_dir / ".agent" / "backlog" / "tasks" / task_id / "retro.md"
                )
                task_retro.parent.mkdir(parents=True, exist_ok=True)
                return task_retro

        # Default: save in session dir
        return session.session_dir / "retro.md"


class JudgeRunner:
    """Evaluates session quality using trace and audit data."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.session_mgr = SessionManager(project_dir)

    def judge_session(self, session_id: str) -> Path:
        """Judge a specific session. Returns path to verdict."""
        session = self.session_mgr.get_session(session_id)
        if session is None:
            raise ValueError(f"Session '{session_id}' not found")
        return self._evaluate(session)

    def judge_last(self, n: int = 1) -> list[Path]:
        """Judge the last N sessions."""
        sessions = self.session_mgr.list_sessions(last_n=n)
        return [self._evaluate(s) for s in sessions]

    def _evaluate(self, session: Session) -> Path:
        """Evaluate a session and produce a verdict."""
        audit_content = ""
        if session.audit_path.exists():
            audit_content = session.audit_path.read_text()

        # Extract basic metrics
        metrics = {}
        if session.metrics_path.exists():
            import json
            metrics = json.loads(session.metrics_path.read_text())

        # Generate verdict (placeholder — in production, this invokes a judge sub-agent)
        verdict = JUDGE_TEMPLATE.format(
            session_id=session.session_id,
            role=metrics.get("role", "unknown"),
            provider=metrics.get("provider", "unknown"),
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            assessment="To be filled by judge agent",
            issues="To be filled by judge agent",
            recommendations="To be filled by judge agent",
            score="?",
        )

        verdict_path = session.session_dir / "verdict.md"
        verdict_path.write_text(verdict)
        return verdict_path
