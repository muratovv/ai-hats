"""``make_audit`` step — derive structured ``audit.md`` from claude JSONL.

Canonical post-spawn audit derivation, shared by both HITL (``human.yaml``)
and Automate (``execute.yaml``) pipelines (HATS-535). Reads claude's
per-session JSONL at ``~/.claude/projects/<key>/<claude_session_id>.jsonl``
and rewrites ``<session_dir>/audit.md`` with structured turn markers
(``👤``/``👾``/🔧/💭) plus token aggregation in ``metrics.json``.

Before HATS-535, this logic lived inside ``runtime._finalize_session`` and
fired ONLY on the HITL path — the SubAgent path produced a meta-only
``audit.md`` despite claude SDK persisting the same JSONL. Lifting the
call into its own step closes that asymmetry (mirror of HATS-523, which
brought ``meta_prompt.txt`` to HITL parity with SubAgent).

``failure_policy = "continue"``: audit derivation is best-effort. If the
JSONL is missing (claude never started, project_key encoding mismatch,
etc.) ``AuditWriter`` falls back to the trace-log branch which produces
degraded but non-empty output. A hard exception here would orphan the
session-end print and update banner — neither acceptable.

``KeyboardInterrupt`` is swallowed internally for the same reason a second
Ctrl+C must not kill cleanup partway (HATS-086 invariant, inherited from
the pre-refactor ``_finalize_session`` discipline).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from ...observe import AuditWriter, Session, TraceTag
from ..step import Step, StepIO

logger = logging.getLogger(__name__)


class MakeAudit(Step):
    failure_policy = "continue"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="make_audit",
            requires=frozenset({
                "session_id", "session_dir", "claude_session_id",
                "project_dir", "exit_code",
            }),
            produces=frozenset({"audit_path"}),
        )

    def run(
        self,
        *,
        session_id: str,
        session_dir: Path,
        claude_session_id: str,
        project_dir: Path,
        exit_code: int,
        **_: Any,
    ) -> dict[str, Any]:
        # Import inside run() to avoid steps→runtime circular at module
        # load time. ``runtime`` already imports from ``observe`` which
        # imports from ``pipeline`` indirectly via Session integration.
        from ...runtime import _claude_jsonl_path, _discover_claude_jsonl

        del exit_code  # contract-required key; AuditWriter reads metrics.json instead

        session = Session(session_id=session_id, session_dir=session_dir)

        try:
            jsonl_path = _claude_jsonl_path(project_dir, claude_session_id)
            if jsonl_path is None or not jsonl_path.exists():
                discovered = _discover_claude_jsonl(project_dir, session_id)
                if discovered is not None:
                    session.log_trace(
                        TraceTag.SYS,
                        f"JSONL discovered via mtime fallback: {discovered.name}",
                    )
                    jsonl_path = discovered
            AuditWriter().build(session, jsonl_path=jsonl_path)
        except (Exception, KeyboardInterrupt):
            logger.warning("audit writer failed", exc_info=True)

        return {"audit_path": session.audit_path}
