"""Post-session audit writer — surface-agnostic (HATS-948, T15).

``AuditWriter`` orchestrates the audit: it asks its injected ``TranscriptParser``
for a ``ParsedTranscript``, formats ``audit.md``, and enriches ``metrics.json``.
It holds ZERO provider parsing — every JSONL/trace assumption lives in the parser
(``ai_hats_observe.parsers``), so a new surface adds a parser, not a writer branch.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .artifacts import (
    FLAG_NO_STRUCTURED_TRANSCRIPT,
    FLAG_NO_TOKEN_TELEMETRY,
    TRANSCRIPT_JSONL,
    TRANSCRIPT_TXT,
    is_measured,
    session_start_dt,
)
from .parsers.claude import ClaudeParser
from .session import AUDIT_SCHEMA_VERSION, Session, _load_metrics_safe


if TYPE_CHECKING:
    from pathlib import Path

    from .parsers.base import ParsedTranscript, TranscriptParser, Turn

logger = logging.getLogger(__name__)


def _merge_flags(prior: object, new: list[str]) -> list[str]:
    """Union of a record's existing flags and this parse's, insertion-ordered."""
    merged = [f for f in prior if isinstance(f, str)] if isinstance(prior, list) else []
    for flag in new:
        if flag not in merged:
            merged.append(flag)
    return merged


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
        start = session_start_dt(session.session_id)
        if start is not None:
            secs = int((datetime.now(timezone.utc) - start).total_seconds())
            duration = f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"

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
                # HATS-1397: lossless, on the same grounds HATS-683 gave for
                # user_input — audit *size* is bounded at the delivery layer
                # (`_truncate_audit`), not by truncating the canonical record.
                # The 500-char cut hit 97% of replies, so only ~3% of the
                # agent's own prose reached the one artifact anyone reads.
                lines.append(f"👾 {turn.response}")
            lines.append("")

        # HATS-561: emit ALL metric keys (not just ``exit_code`` + ``turns``)
        # as bold list items, mirroring the pre-HATS-529 ``finalize_audit``
        # body. Keys already rendered in the header are skipped to avoid
        # duplication; ``composition`` is its own section above; ``models``
        # is folded into the dedicated ``## Model Usage`` block below.
        # This restores the ``**total_cost_usd**`` / ``**claude_session_id**``
        # markers the golden-path test asserts and the
        # `_finalize_sub_agent` extra_metrics keys (claude SDK telemetry).
        # HATS-1397: `build` rewrites metrics.json BEFORE calling us, so this
        # block renders that record rather than racing it. The five keys below
        # used to arrive twice — once as a fresh claim, once in the verbatim dump
        # of every non-header key — printing `measured: false` beside the
        # `turns: 42` it contradicts.
        lines.append("## Metrics")
        lines.append(f"- **exit_code**: {exit_code}")
        if "measured" in metrics:
            lines.append(f"- **measured**: {str(metrics['measured']).lower()}")
        if metrics.get("flags"):
            lines.append(f"- **flags**: {', '.join(str(f) for f in metrics['flags'])}")
        for counter in ("turns", "tool_calls", "tokens"):
            if counter in metrics:
                lines.append(f"- **{counter}**: {metrics[counter]}")
        _header_keys = {
            "role",
            "provider",
            "exit_code",
            "duration",
            "composition",
            "models",
            "measured",
            "flags",
            "turns",
            "tool_calls",
            "tokens",
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
        jsonl_path: Path | Iterable[Path] | None = None,
        transcript_verified: bool = False,
        keep_raw: bool = False,
    ) -> None:
        """Rebuild session audit.md from structured log or trace.log fallback."""
        parsed = self.parser.parse(jsonl_path, session.trace_path)
        turns = parsed.turns
        # Metrics first: `_format_audit` renders metrics.json, so reading it
        # before the rewrite printed the previous run's counters (HATS-1397).
        self._write_metrics(
            session, turns, parsed.model_stats, parsed.agg_usage, flags=parsed.flags
        )
        audit_content = self._format_audit(session, turns, model_stats=parsed.model_stats)
        if not turns:
            audit_content = self._with_transcript_fallback(session, audit_content)
        session.write_artifact_text(session.audit_path, audit_content)

        preserved = self._preserve_transcript(session, jsonl_path)
        droppable = self._may_drop_trace(parsed, preserved and transcript_verified)
        if not keep_raw and droppable and session.trace_path.exists():
            session.trace_path.unlink()  # safe-delete: ok raw-trace (source copied in)

    @staticmethod
    def _normalize_paths(jsonl_path: Path | Iterable[Path] | None) -> list[Path]:
        if jsonl_path is None:
            return []
        if isinstance(jsonl_path, (Path, str)):
            return [Path(jsonl_path)]
        return [Path(p) for p in jsonl_path]

    @staticmethod
    def _read_and_merge_records(existing: list[Path], min_ts: float) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen_fps: set[tuple[Any, ...]] = set()

        for p in existing:
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not isinstance(rec, dict):
                    continue

                ts_raw = rec.get("created_at") or ""
                try:
                    t = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError):
                    t = 0.0

                if min_ts > 0.0 and t > 0.0 and t < min_ts:
                    continue

                fp = (
                    rec.get("created_at"),
                    rec.get("type"),
                    rec.get("source"),
                    rec.get("step_index"),
                    str(rec.get("content")),
                    json.dumps(rec.get("tool_calls"), sort_keys=True)
                    if rec.get("tool_calls")
                    else "",
                )
                if fp not in seen_fps:
                    seen_fps.add(fp)
                    records.append(rec)

        def _ts_key(r: dict[str, Any]) -> tuple[float, int]:
            ts = r.get("created_at") or ""
            try:
                t = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                t = 0.0
            return (t, r.get("step_index") or 0)

        records.sort(key=_ts_key)
        return records

    @classmethod
    def _preserve_transcript(
        cls, session: Session, jsonl_path: Path | Iterable[Path] | None
    ) -> bool:
        """Copy or merge the provider's transcript(s) into the session dir; True once there (HATS-1400)."""
        paths = cls._normalize_paths(jsonl_path)
        existing = [p for p in paths if p.exists()]
        if not existing:
            return False

        dest = session.session_dir / TRANSCRIPT_JSONL
        try:
            if len(existing) == 1:
                session.copy_artifact(existing[0], dest)
            else:
                from .artifacts import session_start_dt

                s_dt = session_start_dt(session.session_id)
                min_ts = s_dt.timestamp() if s_dt else 0.0

                records = cls._read_and_merge_records(existing, min_ts)
                session.write_artifact_text(
                    dest,
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                    encoding="utf-8",
                )
        except OSError:
            logger.warning("could not copy/merge transcripts into %s", dest, exc_info=True)
            return False
        return True

    @staticmethod
    def _may_drop_trace(parsed: ParsedTranscript, preserved_and_verified: bool) -> bool:
        """Whether the session text survives the deletion of trace.log.

        HATS-1374: this used to be unconditional, which destroyed the only copy
        of 295 sessions' text. HATS-1397: the licence is a **verified** copy in
        the session dir. A guessed one is not enough — agy rotates its brain
        segment on a checkpoint, so the freshest transcript can be a 4-record
        tail of a 42-record conversation, and the trace held all of it.
        """
        if FLAG_NO_STRUCTURED_TRANSCRIPT in parsed.flags or "trace-used" in parsed.flags:
            return False

        return bool(preserved_and_verified and parsed.turns)

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
        flags: list[str] | None = None,
    ) -> None:
        """Enrich metrics.json with the parse, claiming a count only if measured.

        HATS-1374 (verdict B7): this used to write the counters unconditionally,
        so an unreachable transcript was recorded as a hard ``turns: 0`` /
        ``tokens: {0,...}`` — indistinguishable from a measured zero, and it
        overwrote SDK ground truth (``num_turns``/``total_cost_usd``) sitting in
        the same file. Now an unmeasured parse annotates instead of asserting,
        per ``rule_composition_value_contract §3``.
        """
        existing = _load_metrics_safe(session) or {}
        parse_flags = list(flags or [])
        existing["schema_version"] = AUDIT_SCHEMA_VERSION

        if FLAG_NO_STRUCTURED_TRANSCRIPT in parse_flags:
            existing["flags"] = _merge_flags(existing.get("flags"), parse_flags)
            # HATS-1397: ask the reader's question, so writer and reader cannot
            # disagree. Demanding `measured: true` erased genuine legacy counters
            # — no pre-HATS-1374 record has the key — while `is_measured` called
            # those same records measured, and the destructive side won.
            if not is_measured(existing):
                for counter in ("turns", "tokens", "models", "tool_calls"):
                    existing.pop(counter, None)
                existing["measured"] = False
        else:
            update = {
                "measured": True,
                # Replaces, not merges: the flags describe the current measurement,
                # so a stale "unmeasured" marker must not survive a good parse.
                "flags": parse_flags,
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
            }
            # HATS-1397: the parse measured turns and tools but the surface emits no
            # usage at all, so only the token counters are withheld — the same
            # "counter absent, flag says why" the unreachable-transcript branch uses.
            if FLAG_NO_TOKEN_TELEMETRY in parse_flags:
                update.pop("tokens")
                existing.pop("tokens", None)  # on this surface any prior value is fabricated
            existing.update(update)

        session.write_artifact_text(session.metrics_path, json.dumps(existing, indent=2))
