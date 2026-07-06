"""Parse plan.md and surface subtask candidates (HATS-231).

Deterministic. Looks at three structures in priority order:
  1. ``## Subtasks`` block with bullet items.
  2. ``## Steps`` checklist (``- [ ] ...`` / ``- [x] ...``).
  3. Numbered headings: ``### 1. ...`` / ``### Phase N: ...`` / ``### Step N: ...``.

Lines already containing ``<!-- HATS-`` are skipped (idempotency marker).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TITLE = 80
MARKER_RE = re.compile(r"<!--\s*[A-Za-z]+-\d+\s*-->")

_HEADING_NUMBERED_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
_HEADING_PHASE_RE = re.compile(
    r"^\s*Phase\s+\d+\s*[:\.\s-]\s*(.+?)\s*$", re.IGNORECASE
)
_HEADING_STEP_RE = re.compile(
    r"^\s*Step\s+\d+\s*[:\.\s-]\s*(.+?)\s*$", re.IGNORECASE
)
_BULLET_RE = re.compile(r"^\s*-\s+(.+?)\s*$")
_CHECKLIST_RE = re.compile(r"^\s*-\s+\[[ xX]\]\s+(.+?)\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")


@dataclass(frozen=True)
class Candidate:
    line_no: int
    title: str
    raw_line: str
    kind: str  # "subtasks" | "steps" | "phase"


def _truncate(s: str) -> str:
    if len(s) <= MAX_TITLE:
        return s
    cut = s[:MAX_TITLE].rsplit(" ", 1)[0]
    return f"{cut}…" if cut else s[:MAX_TITLE] + "…"


def _strip_inline_markdown(text: str) -> str:
    """Strip light markdown decoration (`**bold**`, `` `code` ``) for cleaner titles."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip()


def _is_marked(line: str) -> bool:
    return bool(MARKER_RE.search(line))


def _block_after(lines: list[str], start_idx: int) -> tuple[int, int]:
    """Return (block_start, block_end) line indices after a heading at start_idx."""
    block_start = start_idx + 1
    i = block_start
    while i < len(lines):
        if _H2_RE.match(lines[i]):
            break
        i += 1
    return block_start, i


def _try_subtasks_block(lines: list[str]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for i, line in enumerate(lines):
        m = _H2_RE.match(line)
        if not m:
            continue
        if m.group(1).strip().lower() != "subtasks":
            continue
        block_start, block_end = _block_after(lines, i)
        for j in range(block_start, block_end):
            row = lines[j]
            if _is_marked(row):
                continue
            # Checklist takes precedence over plain bullet (subset).
            cm = _CHECKLIST_RE.match(row) or _BULLET_RE.match(row)
            if cm:
                title = _truncate(_strip_inline_markdown(cm.group(1)))
                if title:
                    candidates.append(
                        Candidate(line_no=j, title=title, raw_line=row, kind="subtasks")
                    )
        # First Subtasks heading wins; later ones ignored.
        return candidates
    return candidates


def _try_steps_block(lines: list[str]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for i, line in enumerate(lines):
        m = _H2_RE.match(line)
        if not m:
            continue
        if m.group(1).strip().lower() != "steps":
            continue
        block_start, block_end = _block_after(lines, i)
        for j in range(block_start, block_end):
            row = lines[j]
            if _is_marked(row):
                continue
            cm = _CHECKLIST_RE.match(row)
            if cm:
                title = _truncate(_strip_inline_markdown(cm.group(1)))
                if title:
                    candidates.append(
                        Candidate(line_no=j, title=title, raw_line=row, kind="steps")
                    )
        return candidates
    return candidates


def _try_numbered_headings(lines: list[str]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for j, row in enumerate(lines):
        m = _H3_RE.match(row)
        if not m:
            continue
        if _is_marked(row):
            continue
        head = m.group(1).strip()
        for pattern in (_HEADING_NUMBERED_RE, _HEADING_PHASE_RE, _HEADING_STEP_RE):
            cm = pattern.match(head)
            if cm:
                title = _truncate(_strip_inline_markdown(cm.groups()[-1]))
                if title:
                    candidates.append(
                        Candidate(line_no=j, title=title, raw_line=row, kind="phase")
                    )
                break
    return candidates


def extract_candidates(plan_text: str) -> list[Candidate]:
    """Return subtask candidates by scanning the plan with priority Subtasks > Steps > Phase."""
    lines = plan_text.splitlines()
    for finder in (_try_subtasks_block, _try_steps_block, _try_numbered_headings):
        found = finder(lines)
        if found:
            return found
    return []


def mark_extracted(plan_text: str, line_no: int, child_id: str) -> str:
    """Append ``<!-- child_id -->`` to the given line; idempotent if already marked."""
    lines = plan_text.splitlines(keepends=True)
    if line_no < 0 or line_no >= len(lines):
        raise IndexError(f"line_no out of range: {line_no}")
    raw = lines[line_no]
    if MARKER_RE.search(raw):
        return plan_text
    newline = ""
    if raw.endswith("\r\n"):
        newline = "\r\n"
        body = raw[:-2]
    elif raw.endswith("\n"):
        newline = "\n"
        body = raw[:-1]
    else:
        body = raw
    lines[line_no] = f"{body.rstrip()} <!-- {child_id} -->{newline}"
    return "".join(lines)
