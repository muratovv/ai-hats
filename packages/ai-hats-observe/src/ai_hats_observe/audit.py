"""Post-session audit writer — surface-agnostic (HATS-948, T15).

``AuditWriter`` orchestrates the audit: it asks its injected ``TranscriptParser``
for a ``ParsedTranscript``, formats ``audit.md``, and enriches ``metrics.json``.
It holds ZERO provider parsing — every JSONL/trace assumption lives in the parser
(``ai_hats_observe.parsers``), so a new surface adds a parser, not a writer branch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ai_hats_core import atomic_write_text

from .artifacts import TRANSCRIPT_TXT
from .parsers.claude import ClaudeParser
from .session import AUDIT_SCHEMA_VERSION, Session, _load_metrics_safe

if TYPE_CHECKING:
    from pathlib import Path

    from .parsers.base import TranscriptParser, Turn


class AuditWriter:
    """Post-processes a session record into enriched audit.md after it ends.

    The parser is injected (default ``ClaudeParser`` for standalone/back-compat);
    the integrator seam supplies ``provider.transcript_parser()``.
    """

    def __init__(self, parser: TranscriptParser | None = None) -> None:
        self.parser: TranscriptParser = parser or ClaudeParser()

    def _format_audit(
        self,
        session: Session,
        turns: list[Turn],
        model_stats: dict[str, dict] | None = None,
    ) -> str:
        metrics = _load_metrics_safe(session) or {}

        role = metrics.get("role", "unknown")
        provider = metrics.get("provider", "unknown")
        exit_code = metrics.get("exit_code", "?")

        # Duration from session_id (UTC)
        duration = "?"
        try:
            start = datetime.strptime(session.session_id[:15], "%Y%m%d-%H%M%S").replace(
                tzinfo=timezone.utc
            )
            secs = int((datetime.now(timezone.utc) - start).total_seconds())
            duration = f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"
        except Exception:
            pass

        total_in = sum(s["in"] for s in (model_stats or {}).values())
        total_out = sum(s["out"] for s in (model_stats or {}).values())

        # HATS-561: emit the header as a list-item block matching the
        # pre-HATS-529 ``init_audit`` + ``finalize_audit`` shape that
        # downstream tooling (golden-path e2e, retro readers, humans
        # scanning the doc) expects. The previous pipe-separated form
        # ``Role: X | Provider: Y | Duration: Zs`` was harder to grep
        # and dropped during the Path-A removal without an explicit
        # replacement contract.
        lines = [
            f"# Session Audit: {session.session_id}",
            "",
            f"- **Role**: {role}",
            f"- **Provider**: {provider}",
            f"- **Duration**: {duration}",
        ]
        if total_in or total_out:
            lines.append(f"- **Tokens**: {total_in:,} in / {total_out:,} out")
        lines.append("")

        # HATS-442: preserve composition snapshot through the post-session
        # audit rebuild. The init_audit path wrote a `## Composition` section
        # in the live audit.md and a `composition` field in metrics.json; the
        # AuditWriter then rebuilds audit.md from JSONL/trace and would
        # clobber it. Pull the snapshot back from metrics.json (whose existing
        # keys survive via `_write_metrics`' existing.update) and re-emit.
        composition = metrics.get("composition")
        if isinstance(composition, dict) and composition:
            lines.append(Session._render_composition_md(composition).rstrip())
            lines.append("")

        for i, turn in enumerate(turns, 1):
            # Support both trace format "17:32:34.581" and ISO "2026-03-27T18:15:00"
            ts_display = turn.timestamp
            if "T" in ts_display:
                ts_display = ts_display.split("T")[1][:8]
            else:
                ts_display = ts_display[:8]
            lines.append(f"## Turn {i} ({ts_display})")
            if turn.user_input:
                # HATS-683: lossless — render user_input in full. Audit *size* is
                # managed at the delivery layer (`_truncate_audit`, HATS-684), not
                # by truncating the canonical record. Pure-noise skill bodies are
                # already dropped upstream in `_extract_user_text` (HATS-666).
                lines.append(f"👤 {turn.user_input}")
            lines.append("")
            if turn.thinking_secs:
                lines.append(f"💭 Thinking {turn.thinking_secs}s")
            for tool in turn.tools:
                lines.append(f"🔧 {tool}")
            if turn.response:
                resp = turn.response
                if len(resp) > 500:
                    resp = resp[:500] + "…"
                lines.append(f"👾 {resp}")
            lines.append("")

        # HATS-561: emit ALL metric keys (not just ``exit_code`` + ``turns``)
        # as bold list items, mirroring the pre-HATS-529 ``finalize_audit``
        # body. Keys already rendered in the header are skipped to avoid
        # duplication; ``composition`` is its own section above; ``models``
        # is folded into the dedicated ``## Model Usage`` block below.
        # This restores the ``**total_cost_usd**`` / ``**claude_session_id**``
        # markers the golden-path test asserts and the
        # `_finalize_sub_agent` extra_metrics keys (claude SDK telemetry).
        lines.append("## Metrics")
        lines.append(f"- **exit_code**: {exit_code}")
        lines.append(f"- **turns**: {len(turns)}")
        _header_keys = {
            "role", "provider", "exit_code", "duration",
            "composition", "models",
            "schema_version",  # machine-only tag (metrics.json), not human MD
        }
        for k, v in metrics.items():
            if k in _header_keys:
                continue
            lines.append(f"- **{k}**: {v}")

        if model_stats:
            lines.append("")
            lines.append("## Model Usage")
            for model, stats in model_stats.items():
                lines.append(
                    f"- **{model}**: {stats['calls']} calls, "
                    f"{stats['in']:,} in / {stats['out']:,} out"
                )

        return "\n".join(lines)

    def build(
        self,
        session: Session,
        jsonl_path: Path | None = None,
        keep_raw: bool = False,
    ) -> None:
        """Build enriched audit.md + metrics.json via the injected parser.

        Deletes trace.log after successful audit unless keep_raw=True.
        """
        parsed = self.parser.parse(jsonl_path, session.trace_path)
        turns = parsed.turns
        audit_content = self._format_audit(session, turns, model_stats=parsed.model_stats)
        self._write_metrics(session, turns, parsed.model_stats, parsed.agg_usage)
        if not turns:
            audit_content = self._with_transcript_fallback(session, audit_content)
        session.audit_path.write_text(audit_content)

        # Clean up raw trace — redundant after audit is written. Whitelist.
        if not keep_raw and session.trace_path.exists():
            session.trace_path.unlink()  # safe-delete: ok raw-trace (audit superseded)

    @staticmethod
    def _with_transcript_fallback(session: Session, audit_content: str) -> str:
        """HATS-682: surface ``transcript.txt`` when no structured turns parsed.

        SDK sub-agents (e.g. ``isolation=discard`` hypothesis-intake) leave a
        non-empty ``transcript.txt`` (the LLM's final stdout) but no reachable
        claude JSONL (tmp-worktree project_key mismatch) and no ``trace.log``
        (SDK path doesn't write one). ``build()`` then parses zero turns and the
        audit body is an empty ``turns:0`` stub — real work the reviewer needs to
        cite is lost. Fold the already-captured transcript into the body.

        Only invoked by ``build()`` when ``not turns`` — so it never duplicates
        content already rendered as 👤/👾/🔧 turns. ``metrics.json`` counters stay
        honest (no synthesized turns). ``reasoning.log`` is intentionally excluded
        (noisy / large — would re-introduce the audit bloat HATS-684/666 fixed).
        Oversize transcripts are still bounded downstream by
        ``SessionReviewRunner._truncate_audit``.
        """
        transcript = session.session_dir / TRANSCRIPT_TXT
        if not transcript.exists():
            return audit_content
        text = transcript.read_text().strip()
        if not text:
            return audit_content
        return (
            audit_content.rstrip()
            + "\n\n## Transcript (raw — structured turns unavailable)\n\n"
            + text
            + "\n"
        )

    def _write_metrics(
        self,
        session: Session,
        turns: list[Turn],
        model_stats: dict[str, dict],
        agg_usage: dict,
    ) -> None:
        """Overwrite metrics.json with enriched data from the parse."""
        existing = _load_metrics_safe(session) or {}

        existing.update({
            "schema_version": AUDIT_SCHEMA_VERSION,
            "turns": len(turns),
            "tokens": {
                "input": agg_usage.get("input_tokens", 0),
                "output": agg_usage.get("output_tokens", 0),
                "cache_read": agg_usage.get("cache_read_input_tokens", 0),
                "cache_creation": agg_usage.get("cache_creation_input_tokens", 0),
            },
            "models": {
                model: {
                    "calls": stats["calls"],
                    "input_tokens": stats["in"],
                    "output_tokens": stats["out"],
                }
                for model, stats in model_stats.items()
            },
            "tool_calls": sum(len(t.tools) for t in turns),
        })

        atomic_write_text(session.metrics_path, json.dumps(existing, indent=2))
