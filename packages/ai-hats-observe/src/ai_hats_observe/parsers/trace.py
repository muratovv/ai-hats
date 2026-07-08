"""Trace-log fallback parser (HATS-948, T15).

Reconstructs turns from observe's ``trace.log`` when no structured provider log
is available. The line format (``HH:MM:SS.mmm [TAG] content``) is observe's own
schema; the ``⏺``/spinner chrome inside ``[RES]`` content is Claude-TUI stdout
captured by the sidecar — this is the historical JSONL-missing fallback that the
Gemini surface also rides. Distinct from ``ai_hats_observe.trace`` (the tag
vocab); this module is the *parser*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .base import ParsedTranscript, Turn


@dataclass
class TraceEntry:
    timestamp: str
    tag: str
    content: str


class TraceParser:
    """Parse ``trace.log`` (Claude-TUI chrome) into a ``ParsedTranscript``.

    Implements the ``TranscriptParser`` contract: ``jsonl_path`` is ignored —
    this parser reads only the trace. Token telemetry is unavailable on this
    surface, so ``model_stats``/``agg_usage`` come back empty.
    """

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
        m = TraceParser._LINE_RE.match(line.strip())
        if not m:
            return None
        return TraceEntry(timestamp=m.group(1), tag=m.group(2), content=m.group(3))

    @staticmethod
    def _is_noise(text: str) -> bool:
        if len(text) <= 3:
            return True
        if text[0] in TraceParser._SPINNER_CHARS:
            return True
        if "(thinking with high effort)" in text:
            return True
        for w in TraceParser._THINKING_WORDS:
            if w in text:
                return True
        if any(c in TraceParser._UI_CHARS for c in text[:3]):
            return True
        for phrase in TraceParser._UI_PHRASES:
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
        after = TraceParser._UI_TRIM.sub("", after).strip()
        after = TraceParser._OSC8_REMNANT.sub("", after).strip()
        after = TraceParser._RESPONSE_TAIL_NOISE.sub("", after).strip()
        after = TraceParser._TIP_NOISE.sub("", after).strip()
        return after if after else None

    @staticmethod
    def _extract_tool(text: str) -> str | None:
        content = TraceParser._extract_pio_content(text)
        if content is None:
            return None
        for pattern, fmt in TraceParser._TOOL_PATTERNS:
            m = pattern.search(content)
            if m:
                return fmt.format(*m.groups())
        return None

    @staticmethod
    def _is_thinking(text: str) -> bool:
        if "(thinking with high effort)" in text:
            return True
        stripped = text.lstrip("✢✳✶✻*· ")
        return stripped in TraceParser._THINKING_WORDS

    @staticmethod
    def _thinking_duration(entries: list[TraceEntry]) -> int:
        thinking = [e for e in entries if TraceParser._is_thinking(e.content)]
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

    def parse(
        self, jsonl_path: Path | None, trace_path: Path
    ) -> ParsedTranscript:
        entries = self._parse_trace(trace_path)
        return ParsedTranscript(turns=self._extract_turns(entries))
