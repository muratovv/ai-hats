"""Session lifecycle + the trace/audit writer surface (HATS-948, T15).

``SessionManager`` (create/get/list) and ``Session`` (per-session artifacts:
trace/audit/metrics writers). Depends only on ``ai_hats_core`` (the ``recovery``
DI seam + ``atomic_write_text``) and observe's own vocab leaves — no integrator.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ai_hats_core import atomic_write_text
from ai_hats_core.recovery import NoOpRecovery, RecoveryProtocol

from .artifacts import (
    AUDIT_MD,
    META_PROMPT_TXT,
    METRICS_JSON,
    PTY_RAW_LOG,
    REASONING_LOG,
    TRACE_LOG,
    USAGE_JSON,
    session_dirname,
)
from .trace import ENV_SESSION_ID, TraceTag

# HATS-948: the metrics.json (machine-readable audit) schema tag — observe's
# first versioned surface (mirrors usage/v1). Bumped by the migration seam.
AUDIT_SCHEMA_VERSION = "audit/v1"


class SessionManager:
    """Manages session lifecycle and directories."""

    def __init__(
        self,
        project_dir: Path | None = None,
        *,
        runs_dir: Path,
        recovery: RecoveryProtocol | None = None,
    ) -> None:
        # HATS-864: the runs root is injected integrator policy (paths.runs_dir).
        # `gitlog_dir` name kept for backwards source compat; it's the runs root.
        self.gitlog_dir = runs_dir
        self.gitlog_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self.project_dir = project_dir
        # HATS-948: package-pure default = no-op recovery; the integrator injects
        # the real EnvironmentRecovery at the run-path seam (make_session_manager).
        # HATS-649: recovery runs at this create_session chokepoint on every run.
        self._recovery: RecoveryProtocol = recovery or NoOpRecovery()

    def create_session(self, parent_session: str | None = None) -> Session:
        """Create a new session with a unique ID."""
        # HATS-649: converge crash-recovery (ref write + cache/version sweeps +
        # orphan-version reclaim) on every run, before allocating the session.
        self._recovery.run()
        now = datetime.now(timezone.utc)
        self._counter += 1
        base_id = now.strftime("%Y%m%d-%H%M%S")

        if parent_session:
            session_id = f"{parent_session}_{base_id}-{self._counter}"
        else:
            session_id = f"{base_id}-{self._counter}"

        session_dir = self.gitlog_dir / session_dirname(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        return Session(session_id=session_id, session_dir=session_dir)

    def get_session(self, session_id: str) -> Session | None:
        """Load an existing session."""
        session_dir = self.gitlog_dir / session_dirname(session_id)
        if not session_dir.exists():
            return None
        return Session(session_id=session_id, session_dir=session_dir)

    def list_sessions(
        self,
        last_n: int | None = None,
        productive_only: bool = False,
        *,
        role_eq: str | None = None,
        tag_filters: dict[str, str] | None = None,
        since_date: str | None = None,
    ) -> list[Session]:
        """List sessions, optionally filtered.

        Filters (AND-combined):

        - ``productive_only`` — skip sessions with 0 turns or 0 tool_calls.
        - ``role_eq`` — exact match on ``metrics["role"]``.
        - ``tag_filters`` — all k=v pairs must match ``metrics["tags"]``.
        - ``since_date`` — ``YYYY-MM-DD``; session-id prefix (first 8 chars)
          must be ``>=`` the given date.

        Sessions without a readable ``metrics.json`` are skipped whenever any
        metric-dependent filter is active (role/tag/productive_only), so
        crashed sessions never produce phantom query hits.
        """
        sessions = []
        if not self.gitlog_dir.exists():
            return sessions

        since_prefix = since_date.replace("-", "") if since_date else None
        metric_filters_active = (
            productive_only or role_eq is not None or tag_filters
        )

        for d in sorted(self.gitlog_dir.iterdir()):
            if not (d.is_dir() and d.name.startswith("session_")):
                continue
            sid = d.name[len("session_"):]

            if since_prefix is not None and sid[:8] < since_prefix:
                continue

            s = Session(session_id=sid, session_dir=d)

            if metric_filters_active:
                metrics = _load_metrics_safe(s)
                if metrics is None:
                    continue
                if productive_only and not s.is_productive():
                    continue
                if role_eq is not None and metrics.get("role") != role_eq:
                    continue
                if tag_filters:
                    session_tags = metrics.get("tags") or {}
                    if any(session_tags.get(k) != v for k, v in tag_filters.items()):
                        continue

            sessions.append(s)

        if last_n:
            sessions = sessions[-last_n:]
        return sessions


def _load_metrics_safe(session: "Session") -> dict | None:
    """Return parsed metrics.json, or None if missing/corrupt."""
    if not session.metrics_path.exists():
        return None
    try:
        return json.loads(session.metrics_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


class Session:
    """A single session with its artifacts."""

    def __init__(self, session_id: str, session_dir: Path) -> None:
        self.session_id = session_id
        self.session_dir = session_dir
        self.trace_path = session_dir / TRACE_LOG
        self.audit_path = session_dir / AUDIT_MD
        self.reasoning_path = session_dir / REASONING_LOG
        self.metrics_path = session_dir / METRICS_JSON
        # HATS-664 producer / HATS-734 consumer: machine-readable usage/v1
        # report (measured always-on, aggregates, timeline). Read by
        # ``session show``'s Usage section.
        self.usage_path = session_dir / USAGE_JSON
        self.meta_prompt_path = session_dir / META_PROMPT_TXT
        # HATS-220: pre-strip raw byte dump from PTY (master + stdin). Captures
        # CSI escapes (kitty-keyboard push/pop, DEC modes) that strip_ansi
        # erases from trace.log — needed to diagnose terminal-mode regressions
        # like the Enter-as-newline bug. Created lazily on first write.
        self.pty_raw_path = session_dir / PTY_RAW_LOG

    def is_productive(self) -> bool:
        """Return True if this session had meaningful work (turns > 0 and tool_calls > 0)."""
        metrics = _load_metrics_safe(self)
        if metrics is None:
            return False
        turns = metrics.get("turns")
        tool_calls = metrics.get("tool_calls")
        if turns is None or tool_calls is None:
            return False
        return turns > 0 and tool_calls > 0

    def log_trace(self, tag: str, message: str) -> None:
        """Append a trace entry."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        entry = f"{ts} {tag} {message}\n"
        with open(self.trace_path, "a") as f:
            f.write(entry)

    # HATS-948: semantic tag methods keep `TraceTag` private to observe — runtime
    # bricks write traces through the injected session, importing no observe symbol.
    def log_sys(self, message: str) -> None:
        self.log_trace(TraceTag.SYS, message)

    def log_sub(self, message: str) -> None:
        self.log_trace(TraceTag.SUB, message)

    def log_res(self, message: str) -> None:
        self.log_trace(TraceTag.RES, message)

    def init_audit(
        self,
        role: str,
        provider: str,
        model: str = "",
        composition: dict | None = None,
    ) -> None:
        """Initialize incremental audit.md.

        HATS-442: ``composition`` is an optional snapshot of the effective
        role composition with per-component source layers
        (``built-in``/``global``/``project``) — captured at session start
        so post-session reviewers can cite exactly what loaded.

        Expected shape::

            {
                "traits":   ["name", ...],          # effective order
                "rules":    ["name", ...],
                "skills":   ["name", ...],
                "provenance": {
                    "traits": {"name": "built-in" | "global" | "project"},
                    "rules":  {...},
                    "skills": {...},
                },
            }

        Persisted in audit.md (human-readable) and surfaced again in
        metrics.json via ``finalize_audit`` (machine-readable).
        """
        self._composition = composition  # latched for finalize_audit
        header = (
            f"# Session Audit: {self.session_id}\n\n"
            f"- **Role**: {role}\n"
            f"- **Provider**: {provider}\n"
            f"- **Model**: {model}\n"
            f"- **Started**: {datetime.now(timezone.utc).isoformat()}\n\n"
        )
        if composition:
            header += self._render_composition_md(composition) + "\n"
        header += "## Events\n\n"
        self.audit_path.write_text(header)

    @staticmethod
    def _render_composition_md(composition: dict) -> str:
        """Render the composition snapshot as a markdown section."""
        prov = composition.get("provenance", {}) or {}

        def _line(name: str, layer_map: dict) -> str:
            layer = layer_map.get(name, "built-in")
            return f"{name} ({layer})"

        lines = ["## Composition\n"]
        traits = composition.get("traits", []) or []
        if traits:
            lines.append(
                "- **Traits**: "
                + ", ".join(_line(t, prov.get("traits", {})) for t in traits)
            )
        rules = composition.get("rules", []) or []
        if rules:
            lines.append(
                "- **Rules**: "
                + ", ".join(_line(r, prov.get("rules", {})) for r in rules)
            )
        skills = composition.get("skills", []) or []
        if skills:
            lines.append(
                "- **Skills**: "
                + ", ".join(_line(s, prov.get("skills", {})) for s in skills)
            )
        return "\n".join(lines) + "\n"

    def append_audit(self, event: str) -> None:
        """Append an event to the incremental audit."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with open(self.audit_path, "a") as f:
            f.write(f"- `{ts}` {event}\n")

    def finalize_audit(self, metrics: dict) -> None:
        """Finalize audit with summary metrics.

        HATS-442: composition snapshot captured at ``init_audit`` is
        embedded into metrics.json as the ``composition`` field so
        post-session reviewers can read it without parsing markdown.
        """
        with open(self.audit_path, "a") as f:
            f.write("\n## Metrics\n\n")
            for k, v in metrics.items():
                f.write(f"- **{k}**: {v}\n")

        # Save metrics as JSON too — fold in the composition snapshot
        # if init_audit was called with one.
        out = {"schema_version": AUDIT_SCHEMA_VERSION, **metrics}
        composition = getattr(self, "_composition", None)
        if composition is not None:
            out["composition"] = composition
        atomic_write_text(self.metrics_path, json.dumps(out, indent=2))

    def save_meta_prompt(self, prompt: str) -> None:
        """Save the meta-prompt used for sub-agent execution."""
        self.meta_prompt_path.write_text(prompt)

    def get_env(self) -> dict[str, str]:
        """Environment variables for this session."""
        return {
            ENV_SESSION_ID: self.session_id,
            "TRACE_LOG_PATH": str(self.trace_path),
        }
