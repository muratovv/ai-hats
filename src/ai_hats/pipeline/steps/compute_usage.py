"""``compute_usage`` step — derive ``usage.json`` from claude JSONL.

HATS-664. Sibling of ``make_audit`` (``pipeline/steps/make_audit.py``): same
post-session JSONL source (``~/.claude/projects/<key>/<id>.jsonl``), same
fail-soft contract, wired into the same ``finalize-hitl`` / ``finalize-subagent``
pipelines — but emits a SEPARATE artifact. ``make_audit`` owns the
human-readable ``audit.md`` (turn markers) + flat ``metrics.json``; this step
owns the machine-readable ``usage.json`` (``usage/v1``: measured always-on,
ordered timeline, aggregates, sidechain linkage). Keeping them as two steps and
two artifacts means the rich timeline never bloats the flat ``metrics.json`` that
``session list --json`` reads, and each has its own failure surface (HATS-664
supervisor decision).

The heavy lifting is the pure ``usage.parse_session_usage`` — this step is the
thin live-session driver: locate the JSONL, run the parser, optionally enrich
with a static ``costs.py`` always-on cross-check WHEN ``role`` is in the funnel
(live sessions only; historical sweeps call the parser directly), then persist.

``failure_policy = "continue"``: usage derivation is best-effort. A missing JSONL
(claude never started, project_key mismatch) or any parser hiccup must not orphan
the rest of finalization — mirror of ``make_audit``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

from ai_hats_observe.artifacts import METRICS_JSON, TRACE_LOG, USAGE_JSON

from ..step import Step, StepIO

logger = logging.getLogger(__name__)


class ComputeUsage(Step):
    failure_policy = "continue"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="compute_usage",
            requires=frozenset({
                "session_id", "session_dir", "claude_session_id", "project_dir",
            }),
            # Runner-threaded carve-outs, absent on paths that don't inject them:
            # role + static_cost_analyzer (HATS-865) drive the static cross-check;
            # audit_writer_factory (HATS-953) carries the surface parser (.parser).
            optional=frozenset({
                "role", "static_cost_analyzer", "audit_writer_factory",
                "transcript_resolver",
            }),
            produces=frozenset({"usage_path"}),
        )

    def run(
        self,
        *,
        session_id: str,
        session_dir: Path,
        claude_session_id: str,
        project_dir: Path,
        role: str | None = None,
        static_cost_analyzer=None,
        audit_writer_factory=None,
        transcript_resolver=None,
        **_: Any,
    ) -> dict[str, Any]:
        from ai_hats_observe.parsers.claude import ClaudeParser

        # usage/v1 rides the surface's transcript parser (HATS-953); the seam
        # injects it via audit_writer_factory, standalone defaults to Claude.
        parser = (
            audit_writer_factory().parser
            if audit_writer_factory is not None
            else ClaudeParser()
        )

        usage_path = session_dir / USAGE_JSON
        try:
            # HATS-1087: provider owns discovery; no resolver → empty.
            jsonl_path = (
                transcript_resolver(
                    project_dir, session_id,
                    provider_session_id=claude_session_id or None,
                )
                if transcript_resolver is not None
                else None
            )
            if jsonl_path is None or not jsonl_path.exists():
                logger.debug("compute_usage: no transcript for %s", claude_session_id)
                return {}

            report = parser.parse_usage(jsonl_path, session_dir / TRACE_LOG)
            self._attach_session_meta(report, session_dir, role)
            if report.get("role") and static_cost_analyzer is not None:
                self._enrich_static(report, static_cost_analyzer, report["role"])

            usage_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
            )
        except (Exception, KeyboardInterrupt):
            logger.warning("compute_usage failed", exc_info=True)
            return {}

        return {"usage_path": usage_path}

    @staticmethod
    def _attach_session_meta(
        report: dict[str, Any], session_dir: Path, funnel_role: str | None,
    ) -> None:
        """Fill the report's ai-hats session metadata (role / provider / exit_code).

        The transcript carries none of these — they are ai-hats concepts. Source
        of truth is the session's ``metrics.json`` (written at session start by
        ``init_audit``, refreshed by the upstream ``make_audit`` step, so it
        exists by the time this step runs). A live ``role`` from the pipeline
        funnel, when present, wins over the persisted value. Best-effort: on any
        read error the placeholders stay ``None`` (absent, not a fake value).
        """
        meta: dict[str, Any] = {}
        metrics_path = session_dir / METRICS_JSON
        if metrics_path.exists():
            try:
                meta = json.loads(metrics_path.read_text())
            except (OSError, json.JSONDecodeError):
                meta = {}
        report["role"] = funnel_role or meta.get("role")
        report["provider"] = meta.get("provider")
        report["exit_code"] = meta.get("exit_code")

    @staticmethod
    def _enrich_static(report: dict[str, Any], analyzer, role: str) -> None:
        """Attach the static always-on breakdown via the threaded ``analyzer``.

        Best-effort and live-only — historical transcripts lack the role and
        skip this. The measured proxy (first cache_creation) stays authoritative;
        the static figure is the per-component breakdown the comparison sibling
        diffs against. On any failure, leave ``always_on["static"]`` = None
        (absent, not a fake zero). HATS-865: the composition-layer walk lives in
        the analyzer callable (built at the compose seam), not here.
        """
        try:
            static = analyzer(role)
            if static is None:
                return
            ao = report.get("always_on")
            if ao is None:
                ao = {}
                report["always_on"] = ao
            ao["static"] = static
        except Exception:
            logger.debug("compute_usage: static enrich skipped", exc_info=True)
