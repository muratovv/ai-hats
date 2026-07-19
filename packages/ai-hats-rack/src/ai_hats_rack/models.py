"""Task-card anchor model — on-disk format compatible with ai-hats-tracker.

The rack reads/writes the same ``task.yaml`` as the production tracker (epic
HATS-1014 §4: cutover is a code swap, not a data migration). Old cards load
with defaults; unknown keys (including fields owned by other K-children, e.g.
``attachments``) round-trip verbatim through ``extras``.

``state`` is a plain string validated against the loaded topology by the
kernel — the topology file is the SSOT, an enum here would be a second one.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, get_origin

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import RackConfigError


#: task.yaml fields holding link ids in dedicated storage: a registry kind whose
#: NAME is one of these reads/writes that field (HATS-1032: kind name == storage
#: field); any other kind lands in the generic `links:` map.
LINK_STORAGE_FIELDS: frozenset[str] = frozenset(
    {"parent_task", "subtasks", "depends_on", "related", "see_also", "folded_into"}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_text(path: Path, text: str) -> None:
    """tmp-file + rename in the target dir — no torn task.yaml on crash."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)  # safe-delete: ok ephemeral tmp-file (failed atomic write)
        except OSError:
            pass
        raise


class DeltaFieldError(RackConfigError):
    """A ``Delta.fields`` op violates its target field's type (HATS-1043 thin
    validation): Append onto a non-list field, or Set of a mismatched type. A
    subscriber returned a malformed op — a structural invariant, routed to the
    internal marker like the rest of the RackConfigError subtree."""

    def __init__(self, name: str, message: str) -> None:
        self.field_name = name
        super().__init__(f"field {name!r}: {message}")


class WorkLogEntry(BaseModel):
    timestamp: str = ""
    message: str = ""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _stringify(cls, value: Any) -> Any:
        # Unquoted YAML timestamps parse as datetime; the field stays str.
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    def to_dict(self) -> dict[str, str]:
        return {"timestamp": self.timestamp, "message": self.message}


class TaskCard(BaseModel):
    """YAML task card (anchor: ``state``, ``work_log``, metadata)."""

    model_config = ConfigDict(validate_assignment=False)

    #: typed fields recognized on load; everything else is captured into
    #: ``extras`` and re-emitted verbatim on save (no silent data loss).
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
            "links",
            "tags",
            "work_log",
            "final_state",
            "resolution",
            "created",
            "updated",
            "completed_at",
        }
    )

    id: str
    title: str = ""
    state: str = "brainstorm"
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
    #: generic, kind-blind edge storage (HATS-1028): new kinds land here; legacy
    #: scalar/list fields above stay their own storage (no migration).
    links: dict[str, list[str]] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    work_log: list[WorkLogEntry] = Field(default_factory=list)
    final_state: str = ""
    resolution: str = ""
    created: str = ""
    updated: str = ""
    completed_at: str = ""
    extras: dict[str, Any] = Field(default_factory=dict)

    @field_validator("work_log", mode="before")
    @classmethod
    def _coerce_work_log(cls, value: Any) -> Any:
        """Historical cards wrote work_log entries as bare strings."""
        if not isinstance(value, list):
            return value
        return [
            v if isinstance(v, (dict, WorkLogEntry)) else {"timestamp": "", "message": str(v)}
            for v in value
        ]

    @field_validator("state", mode="before")
    @classmethod
    def _stringify_state(cls, value: Any) -> Any:
        return value if isinstance(value, str) else str(value)

    @field_validator("created", "updated", "completed_at", mode="before")
    @classmethod
    def _stringify_timestamp(cls, value: Any) -> Any:
        """Unquoted YAML dates parse as ``datetime.date``; keep the field str."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    @model_validator(mode="before")
    @classmethod
    def _capture_extras(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        captured = {k: v for k, v in data.items() if k not in cls._KNOWN_FIELDS and k != "extras"}
        if captured:
            merged = {**captured, **(data.get("extras") or {})}
            data = {k: v for k, v in data.items() if k in cls._KNOWN_FIELDS or k == "extras"}
            data["extras"] = merged
        return data

    def log_work(self, message: str, actor: str = "") -> None:
        if actor:
            message = f"[{actor}] {message}"
        self.work_log.append(WorkLogEntry(timestamp=utc_now(), message=message))

    @classmethod
    def _field_type(cls, name: str) -> Any:
        """The declared field's python type: a container origin (list/dict) or
        the scalar annotation itself (str/int)."""
        annotation = cls.model_fields[name].annotation
        origin = get_origin(annotation)
        return origin if origin is not None else annotation

    def set_field(self, name: str, value: Any) -> None:
        """Apply a Delta ``Set`` op: replace a typed field (validated against its
        type) or an unknown key (extras passthrough — today's policy, HATS-1043)."""
        if name not in self._KNOWN_FIELDS:
            self.extras[name] = value
            return
        expected = self._field_type(name)
        if isinstance(expected, type) and not isinstance(value, expected):
            raise DeltaFieldError(
                name, f"Set expects {expected.__name__}, got {type(value).__name__}"
            )
        setattr(self, name, value)

    def append_field(self, name: str, entry: Any) -> None:
        """Apply a Delta ``Append`` op: append to a typed list field (Append
        requires a list field) or an unknown key's extras list (HATS-1043)."""
        if name not in self._KNOWN_FIELDS:
            bucket = self.extras.setdefault(name, [])
            if not isinstance(bucket, list):
                raise DeltaFieldError(name, "Append onto a non-list extras value")
            bucket.append(entry)
            return
        if self._field_type(name) is not list:
            raise DeltaFieldError(name, "Append requires a list field")
        getattr(self, name).append(entry)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "state": self.state,
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
        # Emit-only-when-set keeps existing cards byte-clean on first save
        # (mirrors the tracker's serialization rules for these link fields).
        if self.depends_on:
            d["depends_on"] = self.depends_on
        if self.related:
            d["related"] = self.related
        if self.see_also:
            d["see_also"] = self.see_also
        if self.folded_into:
            d["folded_into"] = self.folded_into
        # Emit only non-empty kinds — an empty `links:` mapping is diff-noise,
        # and the old tracker round-trips this key verbatim through its extras.
        links = {kind: ids for kind, ids in self.links.items() if ids}
        if links:
            d["links"] = links
        for k, v in self.extras.items():
            if k not in self._KNOWN_FIELDS:
                d[k] = v
        return d

    @classmethod
    def from_yaml(cls, path: Path) -> TaskCard:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def save(self, path: Path) -> None:
        atomic_write_text(
            path,
            yaml.dump(
                self.to_dict(), default_flow_style=False, allow_unicode=True, sort_keys=False
            ),
        )
