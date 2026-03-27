"""Observability — trace logging, audit, session management."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class TraceTag:
    REQ = "[REQ]"
    RES = "[RES]"
    ACT = "[ACT]"
    TOOL = "[TOOL]"
    SYS = "[SYS]"
    SUB = "[SUB]"


class SessionManager:
    """Manages session lifecycle and directories."""

    def __init__(self, project_dir: Path) -> None:
        self.gitlog_dir = project_dir / ".gitlog"
        self.gitlog_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    def create_session(self, parent_session: str | None = None) -> Session:
        """Create a new session with a unique ID."""
        now = datetime.now(timezone.utc)
        self._counter += 1
        base_id = now.strftime("%Y%m%d-%H%M%S")

        if parent_session:
            session_id = f"{parent_session}_{base_id}-{self._counter}"
        else:
            session_id = f"{base_id}-{self._counter}"

        session_dir = self.gitlog_dir / f"session_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        return Session(session_id=session_id, session_dir=session_dir)

    def get_session(self, session_id: str) -> Session | None:
        """Load an existing session."""
        session_dir = self.gitlog_dir / f"session_{session_id}"
        if not session_dir.exists():
            return None
        return Session(session_id=session_id, session_dir=session_dir)

    def list_sessions(self, last_n: int | None = None) -> list[Session]:
        """List sessions, optionally the last N."""
        sessions = []
        if not self.gitlog_dir.exists():
            return sessions
        for d in sorted(self.gitlog_dir.iterdir()):
            if d.is_dir() and d.name.startswith("session_"):
                sid = d.name[len("session_"):]
                sessions.append(Session(session_id=sid, session_dir=d))
        if last_n:
            sessions = sessions[-last_n:]
        return sessions


class Session:
    """A single session with its artifacts."""

    def __init__(self, session_id: str, session_dir: Path) -> None:
        self.session_id = session_id
        self.session_dir = session_dir
        self.trace_path = session_dir / "trace.log"
        self.audit_path = session_dir / "audit.md"
        self.reasoning_path = session_dir / "reasoning.log"
        self.metrics_path = session_dir / "metrics.json"
        self.meta_prompt_path = session_dir / "meta_prompt.txt"

    def log_trace(self, tag: str, message: str) -> None:
        """Append a trace entry."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        entry = f"{ts} {tag} {message}\n"
        with open(self.trace_path, "a") as f:
            f.write(entry)

    def init_audit(self, role: str, provider: str, model: str = "") -> None:
        """Initialize incremental audit.md."""
        header = (
            f"# Session Audit: {self.session_id}\n\n"
            f"- **Role**: {role}\n"
            f"- **Provider**: {provider}\n"
            f"- **Model**: {model}\n"
            f"- **Started**: {datetime.now(timezone.utc).isoformat()}\n\n"
            f"## Events\n\n"
        )
        self.audit_path.write_text(header)

    def append_audit(self, event: str) -> None:
        """Append an event to the incremental audit."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with open(self.audit_path, "a") as f:
            f.write(f"- `{ts}` {event}\n")

    def finalize_audit(self, metrics: dict) -> None:
        """Finalize audit with summary metrics."""
        with open(self.audit_path, "a") as f:
            f.write("\n## Metrics\n\n")
            for k, v in metrics.items():
                f.write(f"- **{k}**: {v}\n")

        # Save metrics as JSON too
        with open(self.metrics_path, "w") as f:
            json.dump(metrics, f, indent=2)

    def save_meta_prompt(self, prompt: str) -> None:
        """Save the meta-prompt used for sub-agent execution."""
        self.meta_prompt_path.write_text(prompt)

    def get_env(self) -> dict[str, str]:
        """Environment variables for this session."""
        return {
            "AI_HATS_SESSION_ID": self.session_id,
            "TRACE_LOG_PATH": str(self.trace_path),
        }
