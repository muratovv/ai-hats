"""Post-session audit writer — trace.log/JSONL → enriched audit.md (HATS-948, T15).

``AuditWriter`` reconstructs turns (from the ``claude`` JSONL when present, else
the trace-chrome fallback), formats ``audit.md`` and enriches ``metrics.json``.
Depends only on ``ai_hats_core`` + observe's ``Session``/vocab — no integrator.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ai_hats_core import atomic_write_text

from .artifacts import TRANSCRIPT_TXT
from .session import AUDIT_SCHEMA_VERSION, Session, _load_metrics_safe

logger = logging.getLogger(__name__)


@dataclass
class TraceEntry:
    timestamp: str
    tag: str
    content: str


@dataclass
class Turn:
    timestamp: str
    user_input: str | None = None
    tools: list[str] = field(default_factory=list)
    response: str = ""
    thinking_secs: int = 0


class AuditWriter:
    """Post-processes trace.log into enriched audit.md after session ends."""

    _LINE_RE = re.compile(r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s+\[(\w+)\]\s+(.*)$")
    _SPINNER_CHARS = set("✢✳✶✻*·⠐⠂⠁⠈⠌⠘⠠⠤⠸")
    _THINKING_WORDS = {"Pondering…", "Fermenting…", "Reticulating…", "Effecting…"}
    _UI_CHARS = set("╭╮╰╯│─━┃┏┓┗┛┣┫")
    _TOOL_PATTERNS = [
        (re.compile(r"Searching for (\d+) pattern"), "Search: {0} pattern"),
        (re.compile(r"Read\((.+?)\)"), "Read: {0}"),
        (re.compile(r"read (\d+) file"), "Read: {0} files"),
        (re.compile(r"Bash\((.+?)\)"), "Bash: {0}"),
        (re.compile(r"Edit\((.+?)\)"), "Edit: {0}"),
        (re.compile(r"Write\((.+?)\)"), "Write: {0}"),
        (re.compile(r"Glob\((.+?)\)"), "Glob: {0}"),
        (re.compile(r"Grep\((.+?)\)"), "Grep: {0}"),
    ]
    _UI_PHRASES = {"? for shortcuts", "esc to interrupt", "● high", "ctrl+o to expand"}
    _ZELLIJ_NOISE = re.compile(r"(?:[>|]+Zellij\(\d+\))+[a-z]{0,2}")

    @staticmethod
    def _parse_line(line: str) -> TraceEntry | None:
        m = AuditWriter._LINE_RE.match(line.strip())
        if not m:
            return None
        return TraceEntry(timestamp=m.group(1), tag=m.group(2), content=m.group(3))

    @staticmethod
    def _is_noise(text: str) -> bool:
        if len(text) <= 3:
            return True
        if text[0] in AuditWriter._SPINNER_CHARS:
            return True
        if "(thinking with high effort)" in text:
            return True
        for w in AuditWriter._THINKING_WORDS:
            if w in text:
                return True
        if any(c in AuditWriter._UI_CHARS for c in text[:3]):
            return True
        for phrase in AuditWriter._UI_PHRASES:
            if phrase in text:
                return True
        if re.match(r"^\d+;", text):
            return True
        return False

    _UI_TRIM = re.compile(r"[─╭╮╰╯│━❯┃┏┓┗┛┣┫].*$")
    _OSC8_REMNANT = re.compile(r"8;(?:id=[^;]*;)?(?:file://)?[^;]*8;;")
    _RESPONSE_TAIL_NOISE = re.compile(r"\s*[✢✳✶✻*·⏵⏸]\w+….*$")
    _TIP_NOISE = re.compile(r"\s*⎿\s+Tip:.*$")

    @staticmethod
    def _extract_pio_content(text: str) -> str | None:
        """Extract text after ⏺, trimming TUI chrome and noise. Returns None if no ⏺ found.

        Trace-fallback path only — used by ``_extract_turns`` when the
        canonical ``claude`` JSONL session log is not available and we
        have to reconstruct turns from ``trace.log``. Unrelated to the
        removed live-PTY ⏺-marker accumulator (Path A, HATS-529).
        """
        if "⏺" not in text:
            return None
        idx = text.index("⏺")
        after = text[idx + 1:].strip()
        after = AuditWriter._UI_TRIM.sub("", after).strip()
        after = AuditWriter._OSC8_REMNANT.sub("", after).strip()
        after = AuditWriter._RESPONSE_TAIL_NOISE.sub("", after).strip()
        after = AuditWriter._TIP_NOISE.sub("", after).strip()
        return after if after else None

    @staticmethod
    def _extract_tool(text: str) -> str | None:
        content = AuditWriter._extract_pio_content(text)
        if content is None:
            return None
        for pattern, fmt in AuditWriter._TOOL_PATTERNS:
            m = pattern.search(content)
            if m:
                return fmt.format(*m.groups())
        return None

    @staticmethod
    def _is_thinking(text: str) -> bool:
        if "(thinking with high effort)" in text:
            return True
        stripped = text.lstrip("✢✳✶✻*· ")
        return stripped in AuditWriter._THINKING_WORDS

    @staticmethod
    def _thinking_duration(entries: list[TraceEntry]) -> int:
        thinking = [e for e in entries if AuditWriter._is_thinking(e.content)]
        if len(thinking) < 2:
            return len(thinking)
        t0 = thinking[0].timestamp
        t1 = thinking[-1].timestamp
        try:
            fmt = "%H:%M:%S.%f"
            d0 = datetime.strptime(t0, fmt)
            d1 = datetime.strptime(t1, fmt)
            return max(1, int((d1 - d0).total_seconds()))
        except Exception:
            return len(thinking)

    def _parse_trace(self, trace_path: Path) -> list[TraceEntry]:
        if not trace_path.exists():
            return []
        entries = []
        for line in trace_path.read_text().splitlines():
            entry = self._parse_line(line)
            if entry:
                entries.append(entry)
            elif entries and line.strip():
                # Orphan line (continuation of multi-line PTY output) — append to previous
                entries[-1].content += " " + line.strip()
        return entries

    def _clean_req(self, text: str) -> str | None:
        cleaned = self._ZELLIJ_NOISE.sub("", text).strip()
        cleaned = re.sub(r"[\t\x7f\x00-\x1f]", "", cleaned)
        # Deduplicate repeated chars (кк → к)
        cleaned = re.sub(r"(.)\1{2,}", r"\1", cleaned)
        if len(cleaned) < 3:
            return None
        return cleaned

    def _extract_turns(self, entries: list[TraceEntry]) -> list[Turn]:
        turns: list[Turn] = []
        current: Turn | None = None
        thinking_entries: list[TraceEntry] = []

        for entry in entries:
            if entry.tag == "REQ":
                # Flush previous turn
                if current:
                    current.thinking_secs = self._thinking_duration(thinking_entries)
                    turns.append(current)
                    thinking_entries = []
                user_input = self._clean_req(entry.content)
                current = Turn(timestamp=entry.timestamp, user_input=user_input)

            elif entry.tag == "RES" and current:
                if "⏺" in entry.content:
                    tool = self._extract_tool(entry.content)
                    if tool:
                        current.tools.append(tool)
                    else:
                        pio = self._extract_pio_content(entry.content)
                        if pio:
                            current.response = pio  # last wins
                elif self._is_thinking(entry.content):
                    thinking_entries.append(entry)

        # Flush last turn
        if current:
            current.thinking_secs = self._thinking_duration(thinking_entries)
            turns.append(current)

        # Dedup tools per turn
        for turn in turns:
            seen = []
            for t in turn.tools:
                if t not in seen:
                    seen.append(t)
            turn.tools = seen

        return turns

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
        """Build enriched audit.md + metrics.json. Uses JSONL if available.

        Deletes trace.log after successful audit unless keep_raw=True.
        """
        if jsonl_path and jsonl_path.exists():
            turns, model_stats, agg_usage = self._parse_jsonl(jsonl_path)
            audit_content = self._format_audit(session, turns, model_stats=model_stats)
            self._write_metrics(session, turns, model_stats, agg_usage)
        else:
            if jsonl_path:
                logger.debug("JSONL not found at %s — falling back to trace", jsonl_path)
            entries = self._parse_trace(session.trace_path)
            turns = self._extract_turns(entries)
            audit_content = self._format_audit(session, turns)
            # Write partial metrics from trace (no token data available)
            self._write_metrics(
                session, turns, model_stats={},
                agg_usage={"input_tokens": 0, "output_tokens": 0,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            )
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
        """Overwrite metrics.json with enriched data from JSONL."""
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

    def _parse_jsonl(self, jsonl_path: Path) -> tuple[list[Turn], dict[str, dict], dict]:
        """Parse Claude Code JSONL → (turns, per-model stats, aggregated usage)."""
        turns: list[Turn] = []
        current: Turn | None = None
        model_stats: dict[str, dict] = {}
        agg_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        prev_model: str | None = None

        for line in jsonl_path.read_text().splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type")
            ts = obj.get("timestamp", "")[:19]
            message = obj.get("message", {})
            content = message.get("content", [])

            if msg_type == "user":
                user_text = self._extract_user_text(content)
                if user_text:
                    current = Turn(timestamp=ts, user_input=user_text)
                    turns.append(current)

            elif msg_type == "assistant" and current is not None:
                model = message.get("model", "unknown")
                usage = message.get("usage", {})
                tok_in = usage.get("input_tokens", 0)
                tok_out = usage.get("output_tokens", 0)

                if model not in model_stats:
                    model_stats[model] = {"in": 0, "out": 0, "calls": 0}
                model_stats[model]["in"] += tok_in
                model_stats[model]["out"] += tok_out
                model_stats[model]["calls"] += 1

                agg_usage["input_tokens"] += tok_in
                agg_usage["output_tokens"] += tok_out
                agg_usage["cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0)
                agg_usage["cache_creation_input_tokens"] += usage.get("cache_creation_input_tokens", 0)

                # Track model switches within turns
                if prev_model and model != prev_model:
                    current.tools.append(f"⚙️ Model: {model}")
                prev_model = model

                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type")
                        if bt == "thinking":
                            thinking = block.get("thinking", "")
                            if thinking:
                                current.thinking_secs = max(1, len(thinking) // 200)
                            else:
                                current.thinking_secs = max(current.thinking_secs, 1)
                        elif bt == "tool_use":
                            name = block.get("name", "?")
                            inp = block.get("input", {})
                            summary = self._summarize_tool_input(name, inp)
                            current.tools.append(f"{name}: {summary}")
                        elif bt == "text":
                            text = block.get("text", "").strip()
                            if text:
                                current.response = text

        return turns, model_stats, agg_usage

    @staticmethod
    def _extract_user_text(content) -> str | None:
        """Extract user text from message content, filtering system/command messages."""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            has_tool_result = any(
                isinstance(c, dict) and c.get("type") == "tool_result" for c in content
            )
            if has_tool_result:
                return None
            parts = [
                c["text"] for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            text = " ".join(parts).strip()
        else:
            return None

        if not text:
            return None
        # Filter Claude Code system messages
        if text.startswith(("<", "/")):
            return None
        # HATS-666: a Skill invocation re-injects the full SKILL.md as a user
        # text message ("Base directory for this skill: <path>"). That body is
        # 100% redundant with the `🔧 Skill: <name>` tool line the audit already
        # renders — filter it like a tool_result so it never becomes a 👤 turn.
        if text.startswith("Base directory for this skill:"):
            return None
        return text

    @staticmethod
    def _summarize_tool_input(name: str, inp: dict) -> str:
        """Summarize tool input to a short string."""
        if name == "Bash":
            return inp.get("command", inp.get("description", ""))[:100]
        if name in ("Read", "Write", "Edit"):
            return inp.get("file_path", "")
        if name in ("Grep", "Glob"):
            return inp.get("pattern", "")
        if name == "Agent":
            return inp.get("description", inp.get("prompt", ""))[:80]
        # Generic: show first string value
        for v in inp.values():
            if isinstance(v, str) and v:
                return v[:80]
        return str(inp)[:80]
