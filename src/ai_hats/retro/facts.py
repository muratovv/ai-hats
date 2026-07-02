"""Pure-Python session-fact extractor (HATS-252).

Computes factual fields for SessionReviewV1 without an LLM:
metrics, artifacts (files_changed/commits/tasks_closed), links, role, project,
date, session window. Extracted from the legacy SessionRetroBuilder so the
factual layer survives the merge into a single LLM call.

The window invariants (HATS-212) — files_changed and tasks_closed are scoped
to ``[session_start, session_end]`` — are preserved here.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ai_hats.git_env import scrubbed_git_env

from .common import SessionArtifacts, SessionLinks, SessionMetrics
from .window import (
    SESSION_PREFIX,
    compute_session_end,
    parse_session_start,
    tasks_closed_in_window,
)


@dataclass
class SessionFacts:
    """Factual snapshot of one session — feeds SessionReviewV1 directly."""

    session_id: str
    project: str
    role: str
    date: date
    metrics: SessionMetrics
    artifacts: SessionArtifacts
    links: SessionLinks
    session_start: datetime
    session_end: datetime
    # HATS-442: effective composition snapshot captured at session start.
    # ``None`` for sessions written before this field landed (backwards compat).
    composition: dict | None = None


def compute_facts(project_dir: Path, session_id: str) -> SessionFacts:
    """Compute SessionFacts from <runs_dir>/<session> + git log + backlog.

    Raises FileNotFoundError if the session directory does not exist.
    """
    from ..paths import runs_dir

    sid = _normalize(session_id)
    session_dir = runs_dir(project_dir) / f"{SESSION_PREFIX}{sid}"
    if not session_dir.exists():
        raise FileNotFoundError(f"Session not found: {sid}")

    metrics = _parse_metrics(session_dir)
    session_start = parse_session_start(sid)
    session_end = compute_session_end(session_start, session_dir, sid)
    role = _parse_role(session_dir)
    project_name = project_dir.resolve().name or "project"

    artifacts = SessionArtifacts(
        files_changed=_files_changed(project_dir, session_start, session_end),
        commits=_commits_in_window(project_dir, session_start, session_end),
        tasks_closed=tasks_closed_in_window(project_dir, session_start, session_end),
    )

    # Retros live at <ai_hats_dir>/sessions/retros/<...>.md and runs at
    # <ai_hats_dir>/sessions/runs/session_<sid>/ — one ".." up gets us
    # back to the sessions/ root.
    links = SessionLinks(
        audit=f"../runs/{SESSION_PREFIX}{sid}/audit.md",
        metrics=(
            f"../runs/{SESSION_PREFIX}{sid}/metrics.json"
            if (session_dir / "metrics.json").exists()
            else None
        ),
    )

    return SessionFacts(
        session_id=sid,
        project=project_name,
        role=role,
        date=session_start.date(),
        metrics=metrics,
        artifacts=artifacts,
        links=links,
        session_start=session_start,
        session_end=session_end,
        composition=_parse_composition(session_dir),
    )


def _parse_composition(session_dir: Path) -> dict | None:
    """Read composition snapshot from metrics.json — HATS-442.

    Returns ``None`` for old sessions that lack the field (pre-HATS-442)
    or any session whose metrics.json is missing/unparsable.
    """
    metrics_path = session_dir / "metrics.json"
    if not metrics_path.exists():
        return None
    try:
        data = json.loads(metrics_path.read_text())
    except json.JSONDecodeError:
        return None
    composition = data.get("composition")
    if isinstance(composition, dict):
        return composition
    return None


def _normalize(session_id: str) -> str:
    if session_id.startswith(SESSION_PREFIX):
        return session_id[len(SESSION_PREFIX):]
    return session_id


def _parse_metrics(session_dir: Path) -> SessionMetrics:
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


def _parse_role(session_dir: Path) -> str:
    """Read role from metrics.json; fall back to audit.md header; finally 'unknown'."""
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


def _git(project_dir: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=scrubbed_git_env(),
        )
        return proc.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _files_changed(
    project_dir: Path, since: datetime, until: datetime
) -> list[str]:
    out = _git(project_dir, [
        "log",
        f"--since={since.isoformat()}",
        f"--until={until.isoformat()}",
        "--name-only",
        "--pretty=format:",
    ])
    return sorted({line for line in out.splitlines() if line.strip()})


def _commits_in_window(
    project_dir: Path, since: datetime, until: datetime
) -> list[str]:
    out = _git(project_dir, [
        "log",
        f"--since={since.isoformat()}",
        f"--until={until.isoformat()}",
        "--pretty=format:%h %s",
    ])
    return [line for line in out.splitlines() if line.strip()]


__all__ = ["SessionFacts", "compute_facts"]
