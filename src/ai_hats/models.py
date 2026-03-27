"""Core data models for ai-hats components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class ComponentType(str, Enum):
    RULE = "rule"
    SKILL = "skill"
    TRAIT = "trait"
    ROLE = "role"


class TaskState(str, Enum):
    BRAINSTORM = "brainstorm"
    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"

    @staticmethod
    def valid_transitions() -> dict[TaskState, list[TaskState]]:
        return {
            TaskState.BRAINSTORM: [TaskState.PLAN, TaskState.BLOCKED],
            TaskState.PLAN: [TaskState.EXECUTE, TaskState.BLOCKED],
            TaskState.EXECUTE: [TaskState.REVIEW, TaskState.BLOCKED, TaskState.FAILED],
            TaskState.REVIEW: [TaskState.DONE, TaskState.FAILED],
            TaskState.BLOCKED: [TaskState.BRAINSTORM, TaskState.PLAN, TaskState.EXECUTE],
            TaskState.FAILED: [TaskState.BRAINSTORM],
            TaskState.DONE: [],
        }

    def can_transition_to(self, target: TaskState) -> bool:
        return target in self.valid_transitions()[self]


class LifecycleEvent(str, Enum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    ERROR = "error"


@dataclass
class MCPServerConfig:
    name: str
    config: str  # path to config file


@dataclass
class HooksConfig:
    session_start: list[str] = field(default_factory=list)
    session_end: list[str] = field(default_factory=list)
    task_start: list[str] = field(default_factory=list)
    task_complete: list[str] = field(default_factory=list)
    task_failed: list[str] = field(default_factory=list)
    error: list[str] = field(default_factory=list)

    def get_scripts(self, event: LifecycleEvent) -> list[str]:
        return getattr(self, event.value, [])

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HooksConfig:
        if not data:
            return cls()
        return cls(
            session_start=data.get("session_start", []),
            session_end=data.get("session_end", []),
            task_start=data.get("task_start", []),
            task_complete=data.get("task_complete", []),
            task_failed=data.get("task_failed", []),
            error=data.get("error", []),
        )


@dataclass
class Composition:
    traits: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    mcp: list[MCPServerConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Composition:
        if not data:
            return cls()
        mcp_list = []
        for m in data.get("mcp", []):
            if isinstance(m, dict):
                mcp_list.append(MCPServerConfig(name=m["name"], config=m.get("config", "")))
        return cls(
            traits=data.get("traits", []),
            rules=data.get("rules", []),
            skills=data.get("skills", []),
            hooks=HooksConfig.from_dict(data.get("hooks")),
            mcp=mcp_list,
        )


@dataclass
class ComponentConfig:
    """Parsed config.yaml for a trait or role."""

    name: str
    composition: Composition = field(default_factory=Composition)
    injection: str = ""
    priorities: list[str] = field(default_factory=list)
    source_path: Path | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> ComponentConfig:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(
            name=data.get("name", path.parent.name),
            composition=Composition.from_dict(data.get("composition")),
            injection=data.get("injection", ""),
            priorities=data.get("priorities", []),
            source_path=path,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_path: Path | None = None) -> ComponentConfig:
        return cls(
            name=data.get("name", ""),
            composition=Composition.from_dict(data.get("composition")),
            injection=data.get("injection", ""),
            priorities=data.get("priorities", []),
            source_path=source_path,
        )


@dataclass
class RuleMetadata:
    name: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> RuleMetadata:
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            author=data.get("author", ""),
            tags=data.get("tags", []),
        )


@dataclass
class ProjectConfig:
    """ai-hats.yaml project configuration."""

    provider: str = "gemini"
    default_role: str = ""
    schema_version: int = 1
    library_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> ProjectConfig:
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(
            provider=data.get("provider", "gemini"),
            default_role=data.get("default_role", ""),
            schema_version=data.get("schema_version", 1),
            library_paths=data.get("library_paths", []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "default_role": self.default_role,
            "schema_version": self.schema_version,
            "library_paths": self.library_paths,
        }

    def save(self, path: Path) -> None:
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)


@dataclass
class ProfileConfig:
    """profile.json — active role tracking."""

    active_role: str = ""
    provider: str = ""

    @classmethod
    def load(cls, path: Path) -> ProfileConfig:
        if not path.exists():
            return cls()
        import json

        with open(path) as f:
            data = json.load(f)
        return cls(
            active_role=data.get("active_role", ""),
            provider=data.get("provider", ""),
        )

    def save(self, path: Path) -> None:
        import json

        with open(path, "w") as f:
            json.dump({"active_role": self.active_role, "provider": self.provider}, f, indent=2)


@dataclass
class WorkLogEntry:
    """Single work log entry with timestamp and session tracking."""

    timestamp: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"timestamp": self.timestamp, "message": self.message}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkLogEntry:
        return cls(timestamp=data.get("timestamp", ""), message=data.get("message", ""))


@dataclass
class TaskCard:
    """YAML task card for state machine."""

    id: str
    title: str
    state: TaskState = TaskState.BRAINSTORM
    description: str = ""
    priority: str = "medium"
    assignee: str = ""
    reviewer: str = "user"
    role: str = ""
    parent_task: str = ""
    subtasks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    work_log: list[WorkLogEntry] = field(default_factory=list)
    final_state: str = ""
    created: str = ""
    updated: str = ""
    completed_at: str = ""

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
        if self.completed_at:
            d["completed_at"] = self.completed_at
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskCard:
        work_log = [
            WorkLogEntry.from_dict(e) if isinstance(e, dict) else WorkLogEntry(timestamp="", message=str(e))
            for e in data.get("work_log", [])
        ]
        return cls(
            id=data["id"],
            title=data["title"],
            state=TaskState(data.get("state", "brainstorm")),
            description=data.get("description", ""),
            priority=data.get("priority", "medium"),
            assignee=data.get("assignee", ""),
            reviewer=data.get("reviewer", "user"),
            role=data.get("role", ""),
            parent_task=data.get("parent_task", ""),
            subtasks=data.get("subtasks", []),
            tags=data.get("tags", []),
            work_log=work_log,
            final_state=data.get("final_state", ""),
            created=data.get("created", ""),
            updated=data.get("updated", ""),
            completed_at=data.get("completed_at", ""),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> TaskCard:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def resolve_namespace(name: str) -> str:
    """Convert namespace notation (dev::python) to filesystem path (dev/python)."""
    return name.replace("::", "/")
