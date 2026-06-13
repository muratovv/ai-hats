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
            # ``role`` enables the optional static always-on cross-check; absent
            # (e.g. SubAgent paths that don't thread it) → measured-only report.
            optional=frozenset({"role"}),
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
        **_: Any,
    ) -> dict[str, Any]:
        from ...runtime import _claude_jsonl_path, _discover_claude_jsonl
        from ...usage import parse_session_usage

        usage_path = session_dir / "usage.json"
        try:
            jsonl_path = _claude_jsonl_path(project_dir, claude_session_id)
            if jsonl_path is None or not jsonl_path.exists():
                # HATS-734: discovery keys off the ai-hats ``session_id``
                # (``YYYYMMDD-HHMMSS-N``), NOT ``claude_session_id``. In
                # resume/continue mode the uuid never reached Claude, so its
                # path is missing AND it cannot drive the mtime-window start —
                # ``_discover_claude_jsonl`` parses ``session_id[:15]`` with
                # strptime, which a uuid fails → None. Sibling ``make_audit``
                # already passes ``session_id``; converge on that.
                jsonl_path = _discover_claude_jsonl(project_dir, session_id)
            if jsonl_path is None or not jsonl_path.exists():
                logger.debug("compute_usage: no JSONL for %s", claude_session_id)
                return {}

            report = parse_session_usage(jsonl_path)
            self._attach_session_meta(report, session_dir, role)
            if report.get("role"):
                self._enrich_static(report, project_dir, report["role"])

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
        metrics_path = session_dir / "metrics.json"
        if metrics_path.exists():
            try:
                meta = json.loads(metrics_path.read_text())
            except (OSError, json.JSONDecodeError):
                meta = {}
        report["role"] = funnel_role or meta.get("role")
        report["provider"] = meta.get("provider")
        report["exit_code"] = meta.get("exit_code")

    @staticmethod
    def _enrich_static(report: dict[str, Any], project_dir: Path, role: str) -> None:
        """Attach a static ``costs.py`` always-on breakdown for cross-check.

        Best-effort and live-only — historical transcripts lack the role and
        skip this. The measured proxy (first cache_creation) stays authoritative;
        the static figure is the per-component breakdown the comparison sibling
        diffs against. On any failure, leave ``always_on["static"]`` = None
        (absent, not a fake zero).
        """
        try:
            from ...assembler import Assembler
            from ...composer import Composer
            from ...costs import analyze_composition

            composer = Composer(Assembler(project_dir).resolver)
            breakdown = analyze_composition(composer, role, exact=False)
            ao = report.get("always_on")
            if ao is None:
                ao = {}
                report["always_on"] = ao
            ao["static"] = {
                "role": role,
                "total_tokens": breakdown.total_tokens,
                "exact": breakdown.exact,
                "components": [
                    {"name": c.name, "category": c.category, "tokens": c.tokens}
                    for c in breakdown.components
                ],
            }
        except Exception:
            logger.debug("compute_usage: static enrich skipped", exc_info=True)
