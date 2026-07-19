"""Tracker domain schema (HATS-863, ex ``ai_hats.models``) — task cards + FSM
states. Lifted into the ``ai-hats-tracker`` package by T16a (HATS-933).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import Field, field_validator, model_validator

from ai_hats_core import YamlModel as _YamlModel
from ai_hats_core import atomic_write_text


class TaskState(str, Enum):
    BRAINSTORM = "brainstorm"
    PLAN = "plan"
    EXECUTE = "execute"
    DOCUMENT = "document"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @staticmethod
    def valid_transitions() -> dict[TaskState, list[TaskState]]:
        # CANCELLED is reachable from every non-terminal state — it's the
        # administrative-close exit (won't-fix / duplicate / obsolete) so the
        # task doesn't have to walk the full lifecycle just to be closed.
        return {
            TaskState.BRAINSTORM: [TaskState.PLAN, TaskState.BLOCKED, TaskState.CANCELLED],
            TaskState.PLAN: [TaskState.EXECUTE, TaskState.BLOCKED, TaskState.CANCELLED],
            TaskState.EXECUTE: [
                # HATS-955: reclaim self-loop — a second agent re-enters execute
                # to take over a task its dead owner left mid-flight. Gated by the
                # ownership claim in state.transition, not by the FSM.
                TaskState.EXECUTE,
                TaskState.DOCUMENT,
                TaskState.BLOCKED,
                TaskState.FAILED,
                TaskState.CANCELLED,
            ],
            TaskState.DOCUMENT: [TaskState.REVIEW, TaskState.BLOCKED, TaskState.CANCELLED],
            TaskState.REVIEW: [
                # HATS-1052: rework loop — review WITH comments goes back to execute
                # (address → document → review again). Fires NO worktree merge (unlike
                # review → done); the tree survives — edges-into-execute route to setup.
                TaskState.EXECUTE,
                TaskState.DONE,
                TaskState.FAILED,
                TaskState.CANCELLED,
            ],
            TaskState.BLOCKED: [
                TaskState.BRAINSTORM,
                TaskState.PLAN,
                TaskState.EXECUTE,
                TaskState.DOCUMENT,
                TaskState.CANCELLED,
            ],
            TaskState.FAILED: [TaskState.BRAINSTORM, TaskState.CANCELLED],
            # DONE → EXECUTE: reopen path for epic close-out / forgotten scope
            # (HATS-328). Side effects (clear completed_at, log reopen) live in
            # state.StateManager.transition.
            TaskState.DONE: [TaskState.EXECUTE],
            TaskState.CANCELLED: [],
        }

    def can_transition_to(self, target: TaskState) -> bool:
        return target in self.valid_transitions()[self]


class WorkLogEntry(_YamlModel):
    """Single work log entry with timestamp and session tracking."""

    timestamp: str = ""
    message: str = ""


_DIGEST_LEN = 12
_DIGEST_RE = re.compile(rf"^[0-9a-f]{{{_DIGEST_LEN}}}$")


class Attachment(_YamlModel):
    """Manifest entry for a file under tasks/<ID>/attachments/.

    The on-disk blob lives in the task's ``attachments/`` directory; this
    record carries the metadata. ``digest`` is the first 12 hex chars of the
    blob's SHA-256 — full hash would balloon ``task.yaml`` and tax every
    agent that loads the card. 48 bits gives a birthday-collision bound
    around 2^24 attachments, vastly beyond any realistic per-task scale.
    """

    name: str = ""
    digest: str = ""
    added: str = ""
    note: str = ""

    @field_validator("digest")
    @classmethod
    def _check_digest(cls, value: str) -> str:
        if value and not _DIGEST_RE.match(value):
            raise ValueError(
                f"digest must be {_DIGEST_LEN} lowercase hex chars (got {value!r})"
            )
        return value


class TaskCard(_YamlModel):
    """YAML task card for state machine.

    Unknown YAML keys are captured into ``extras`` and round-tripped verbatim
    on save. This guards against silent data loss when callers add new fields
    (e.g. ``acceptance_criteria``) that aren't part of the typed schema.
    """

    #: typed fields recognized by from_dict / to_dict; everything else → extras
    _KNOWN_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "title",
            "state",
            "description",
            "priority",
            "assignee",
            "reviewer",
            "role",
            "parent_task",
            "subtasks",
            "depends_on",
            "related",
            "see_also",
            "folded_into",
            "tags",
            "work_log",
            "attachments",
            "final_state",
            "resolution",
            "created",
            "updated",
            "completed_at",
        }
    )

    id: str
    title: str
    state: TaskState = TaskState.BRAINSTORM
    description: str = ""
    priority: str = "medium"
    assignee: str = ""
    reviewer: str = "user"
    role: str = ""
    parent_task: str = ""
    subtasks: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    see_also: list[str] = Field(default_factory=list)
    folded_into: str = ""
    tags: list[str] = Field(default_factory=list)
    work_log: list[WorkLogEntry] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    final_state: str = ""
    resolution: str = ""
    created: str = ""
    updated: str = ""
    completed_at: str = ""
    extras: dict[str, Any] = Field(default_factory=dict)

    @field_validator("work_log", mode="before")
    @classmethod
    def _coerce_work_log(cls, value: Any) -> Any:
        """Older task cards wrote work_log as bare strings (``"date: msg"``).

        Coerce those to WorkLogEntry shape so historical cards still load.
        """
        if not isinstance(value, list):
            return value
        return [
            v if isinstance(v, (dict, WorkLogEntry)) else {"timestamp": "", "message": str(v)}
            for v in value
        ]

    @field_validator("created", "updated", "completed_at", mode="before")
    @classmethod
    def _stringify_timestamp(cls, value: Any) -> Any:
        """YAML literals like ``2026-04-06`` (no quotes) parse as ``datetime.date``.

        Historical cards rely on this; keep the field type as ``str`` but
        stringify non-str inputs on load.
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    @model_validator(mode="before")
    @classmethod
    def _capture_extras(cls, data: Any) -> Any:
        """Move non-schema keys into ``extras`` before field parsing."""
        if not isinstance(data, dict):
            return data
        captured = {k: v for k, v in data.items() if k not in cls._KNOWN_FIELDS and k != "extras"}
        if captured:
            # Merge with any explicit extras passed in (explicit wins).
            merged = {**captured, **(data.get("extras") or {})}
            data = {k: v for k, v in data.items() if k in cls._KNOWN_FIELDS or k == "extras"}
            data["extras"] = merged
        return data

    def transition_to(self, new_state: TaskState) -> None:
        if not self.state.can_transition_to(new_state):
            raise ValueError(
                f"Invalid transition: {self.state.value} → {new_state.value}. "
                f"Valid: {[s.value for s in self.state.valid_transitions()[self.state]]}"
            )
        self.state = new_state

    def log_work(self, message: str, session_id: str = "") -> None:
        """Append a work log entry."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if session_id:
            message = f"[Session {session_id}] {message}"
        self.work_log.append(WorkLogEntry(timestamp=ts, message=message))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "state": self.state.value,
            "description": self.description,
            "priority": self.priority,
            "assignee": self.assignee,
            "reviewer": self.reviewer,
            "role": self.role,
            "parent_task": self.parent_task,
            "subtasks": self.subtasks,
            "tags": self.tags,
            "work_log": [e.to_dict() for e in self.work_log],
            "created": self.created,
            "updated": self.updated,
        }
        if self.final_state:
            d["final_state"] = self.final_state
        if self.resolution:
            d["resolution"] = self.resolution
        if self.completed_at:
            d["completed_at"] = self.completed_at
        # Only emit depends_on when non-empty: keeps existing pre-HATS-198
        # YAML files byte-clean on first save (no spurious `depends_on: []`
        # noise in diffs). Cards with real blockers still serialize as expected.
        if self.depends_on:
            d["depends_on"] = self.depends_on
        # Same byte-clean rule for the HATS-371 link fields — only emit when set.
        if self.related:
            d["related"] = self.related
        if self.see_also:
            d["see_also"] = self.see_also
        if self.folded_into:
            d["folded_into"] = self.folded_into
        if self.attachments:
            d["attachments"] = [a.to_dict() for a in self.attachments]
        # Round-trip unknown fields verbatim. Known fields take precedence in
        # case of accidental collision (extras should never contain known keys
        # since _capture_extras filters them out, but we defend against direct
        # mutation of task.extras).
        for k, v in self.extras.items():
            if k not in self._KNOWN_FIELDS:
                d[k] = v
        return d

    @classmethod
    def from_yaml(cls, path: Path) -> TaskCard:
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})

    @classmethod
    def load_header(cls, path: Path) -> dict[str, str]:
        """Cheap header read for STATE.md rendering.

        Extracts the seven scalar fields needed by ``_update_state_md`` via a
        single regex pass — ~60× faster than ``from_yaml`` on large cards
        because ``description``, ``work_log``, and ``acceptance_criteria`` are
        never decoded.

        Falls back to a full ``from_yaml`` for any card where the regex
        cannot find both ``id`` and ``state`` (e.g. multi-line block scalars,
        unusual layouts) — guarantees parity with the slow path.
        """
        text = path.read_text()
        fields: dict[str, str] = {}
        for m in _TASK_HEADER_RE.finditer(text):
            fields[m.group("key")] = _unquote_yaml_scalar(m.group("val"))
        if "id" not in fields or "state" not in fields:
            full = cls.from_yaml(path)
            return {
                "id": full.id,
                "title": full.title,
                "state": full.state.value,
                "priority": full.priority,
                "assignee": full.assignee,
                "reviewer": full.reviewer,
                "role": full.role,
            }
        fields.setdefault("title", "")
        fields.setdefault("priority", "medium")
        fields.setdefault("assignee", "")
        fields.setdefault("reviewer", "user")
        fields.setdefault("role", "")
        return fields

    def save(self, path: Path) -> None:
        atomic_write_text(
            path,
            yaml.dump(
                self.to_dict(), default_flow_style=False, allow_unicode=True, sort_keys=False
            ),
        )


_TASK_HEADER_RE = re.compile(
    r"^(?P<key>id|title|state|priority|assignee|reviewer|role):[ \t]*(?P<val>.*)$",
    re.MULTILINE,
)


def _unquote_yaml_scalar(value: str) -> str:
    """Strip outer YAML quotes and unescape doubled single quotes."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        inner = v[1:-1]
        if v[0] == "'":
            inner = inner.replace("''", "'")
        return inner
    return v
