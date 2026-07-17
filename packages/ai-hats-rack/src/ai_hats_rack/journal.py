"""JSONL audit journal — the K7 consumer of the K1 ``JournalSink`` seam (HATS-1025).

Per-task ``tasks/<ID>/audit.jsonl`` + rotated ``audit-NNN.jsonl`` segments:
append-only, lossless (PROP-004), nothing deleted or shortened. Write failures
are loud on stderr but never break the transaction — the journal is a
post-persist observer. Records carry a verifiable identity block (PROP-080/076);
full rationale: HATS-1025 plan.md.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dispatch import DispatchRecord

SCHEMA_VERSION = 1

#: same env contract as the tracker and ``cli._actor`` (PROP-080 identity).
ENV_SESSION_ID = "AI_HATS_SESSION_ID"
ENV_ROOT_PID = "AI_HATS_ROOT_PID"

ACTIVE_NAME = "audit.jsonl"
_SEGMENT_RE = re.compile(r"^audit-(\d+)\.jsonl$")

#: rollover threshold per segment; completeness is kept — a segment is only
#: renamed aside, never truncated or removed.
DEFAULT_MAX_BYTES = 1 << 20


def _segments(task_dir: Path) -> list[tuple[int, Path]]:
    """Rotated segments of a task's journal, ``(number, path)``, oldest first."""
    try:
        names = os.listdir(task_dir)
    except FileNotFoundError:
        return []
    found = [(int(m.group(1)), task_dir / n) for n in names if (m := _SEGMENT_RE.match(n))]
    return sorted(found)


def journal_files(tasks_dir: Path, task_id: str) -> list[Path]:
    """All journal files of a task in read order: segments, then the active file."""
    task_dir = Path(tasks_dir) / task_id
    files = [path for _, path in _segments(task_dir)]
    active = task_dir / ACTIVE_NAME
    if active.exists():
        files.append(active)
    return files


@dataclass(frozen=True)
class CorruptLine:
    """A journal line that failed to parse (torn write): reported, not dropped."""

    file: str
    line_no: int
    raw: str

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line_no": self.line_no, "raw": self.raw}


def read_journal(tasks_dir: Path, task_id: str) -> tuple[list[dict[str, Any]], list[CorruptLine]]:
    """Parse a task's whole journal, oldest record first.

    Unparseable lines (a crash can tear the last append) are returned raw in
    the second element — the reader never crashes on them and never hides
    them (PROP-004: a silently skipped record is the truncation class).
    """
    records: list[dict[str, Any]] = []
    corrupt: list[CorruptLine] = []
    for path in journal_files(tasks_dir, task_id):
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                corrupt.append(CorruptLine(str(path), line_no, line))
    return records, corrupt


def _ownership_holder(tasks_dir: Path, task_id: str) -> str:
    """Session id holding ``task_id`` per the ownership registry, or ``""``.

    Layout parity with the tracker: ``ownership.json`` sits next to the tasks
    dir. No registry (bare kernel, no ownership extension) → no cross-check.
    """
    path = Path(tasks_dir).parent / "ownership.json"
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
        return str(registry["owners"][task_id].get("session_id", ""))
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return ""


def build_identity(actor: str, tasks_dir: Path, task_id: str) -> dict[str, Any]:
    """Verifiable identity block for one record (PROP-080/076).

    ``session_id``/``root_pid`` describe the *writing process* (env contract),
    independently of the claimed ``actor``. The verdict says whether the claim
    could be checked: ``verified`` / ``mismatch`` / ``unverified`` (no env
    identity — an explicitly marked blind zone, never a silent one).
    """
    session = os.environ.get(ENV_SESSION_ID, "")
    try:
        root_pid = int(os.environ.get(ENV_ROOT_PID, "") or 0)
    except ValueError:
        root_pid = 0
    identity: dict[str, Any] = {"session_id": session, "root_pid": root_pid}
    if not session:
        identity["verdict"] = "unverified"
        identity["note"] = f"no {ENV_SESSION_ID} in the environment to verify the claim"
    elif actor == f"session:{session}":
        identity["verdict"] = "verified"
    else:
        identity["verdict"] = "mismatch"
        identity["note"] = f"claimed '{actor}' but the environment says 'session:{session}'"

    holder = _ownership_holder(tasks_dir, task_id)
    if holder:
        identity["holder"] = holder
        claimed_session = actor.removeprefix("session:") if actor.startswith("session:") else ""
        if claimed_session != holder:
            identity["holder_mismatch"] = True
    return identity


class JsonlJournalSink:
    """``JournalSink`` writing one JSON line per dispatch to the task's journal."""

    def __init__(self, tasks_dir: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.tasks_dir = Path(tasks_dir)
        self.max_bytes = max_bytes

    def record(self, record: DispatchRecord) -> None:
        try:
            self._write(record)
        except OSError as exc:
            # Loud but non-fatal (module docstring): the operation already
            # persisted; only fs-level failures are softened — anything else
            # is a programming error and propagates.
            sys.stderr.write(
                f"rack: AUDIT JOURNAL WRITE FAILED for {record.task_id} "
                f"({record.event_key}): {exc!r} — the operation itself went "
                f"through, but the audit trail now has a hole. Fix "
                f"{self._active_path(record.task_id)} before trusting "
                f"`rack audit {record.task_id}`.\n"
            )

    # ----- internals --------------------------------------------------------

    def _active_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / ACTIVE_NAME

    def _write(self, record: DispatchRecord) -> None:
        path = self._active_path(record.task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._maybe_rotate(path)
        data = (self._line(record) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)  # one O_APPEND write: concurrent lines don't interleave
        finally:
            os.close(fd)

    def _line(self, record: DispatchRecord) -> str:
        entry: dict[str, Any] = {
            "v": SCHEMA_VERSION,
            "ts": record.started_at,
            "event": record.event_key,
            "task_id": record.task_id,
        }
        if record.detail:
            entry["detail"] = dict(record.detail)
        entry.update(
            {
                "actor": record.actor,
                "force": record.force,
                "reason": record.reason,
                "result": record.result,
                "outcomes": [outcome.to_dict() for outcome in record.outcomes],
                "identity": build_identity(record.actor, self.tasks_dir, record.task_id),
            }
        )
        return json.dumps(entry, ensure_ascii=False)

    def _maybe_rotate(self, path: Path) -> None:
        """Roll a full active file into the next numbered segment.

        Rotation happens between whole lines and only renames — no record is
        ever split, shortened, or removed. Losing the rename race to a
        concurrent writer just means the file was already rotated.
        """
        try:
            if path.stat().st_size < self.max_bytes:
                return
        except FileNotFoundError:
            return
        next_number = max((number for number, _ in _segments(path.parent)), default=0) + 1
        try:
            os.rename(path, path.parent / f"audit-{next_number:03d}.jsonl")
        except FileNotFoundError:
            pass
