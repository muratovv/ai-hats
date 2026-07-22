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
            # HATS-867: Session/AuditWriter arrive injected — no observe import.
            requires=frozenset({
                "session_id", "session_dir", "claude_session_id",
                "project_dir", "exit_code",
                "session_factory", "audit_writer_factory",
            }),
            optional=frozenset({"transcript_resolver"}),
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
        session_factory: Any,
        audit_writer_factory: Any,
        transcript_resolver: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        del exit_code  # contract-required key; AuditWriter reads metrics.json instead

        session = session_factory(session_id=session_id, session_dir=session_dir)

        # HATS-1087: provider owns discovery; no resolver → trace.log fallback.
        try:
            jsonl_path = (
                transcript_resolver(
                    project_dir, session_id,
                    provider_session_id=claude_session_id or None,
                )
                if transcript_resolver is not None
                else None
            )
            audit_writer_factory().build(session, jsonl_path=jsonl_path)
        except (Exception, KeyboardInterrupt):
            logger.warning("audit writer failed", exc_info=True)

        return {"audit_path": session.audit_path}
