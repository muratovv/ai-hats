"""Core data models for ai-hats components (Pydantic v2)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

logger = logging.getLogger(__name__)


# ----- Enums -----


class ComponentType(str, Enum):
    RULE = "rule"
    SKILL = "skill"
    TRAIT = "trait"
    ROLE = "role"


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
            TaskState.EXECUTE: [TaskState.DOCUMENT, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED],
            TaskState.DOCUMENT: [TaskState.REVIEW, TaskState.BLOCKED, TaskState.CANCELLED],
            TaskState.REVIEW: [TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED],
            TaskState.BLOCKED: [TaskState.BRAINSTORM, TaskState.PLAN, TaskState.EXECUTE, TaskState.DOCUMENT, TaskState.CANCELLED],
            TaskState.FAILED: [TaskState.BRAINSTORM, TaskState.CANCELLED],
            TaskState.DONE: [],
            TaskState.CANCELLED: [],
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


class FeedbackPolicy(str, Enum):
    OFF = "off"
    ALWAYS = "always"
    SMART = "smart"
    HINT = "hint"


class JudgePolicy(str, Enum):
    OFF = "off"
    MANUAL = "manual"


# ----- Base -----


class _YamlModel(BaseModel):
    """Common base for YAML-round-trippable models.

    Defaults to ``extra="ignore"`` (silently drop unknown keys). Subclasses
    override when needed (e.g. TaskCard needs ``extras`` round-trip).
    Serialization uses ``mode="json"`` via ``to_dict()`` to coerce enums/Paths
    to primitives suitable for ``yaml.safe_dump``.
    """

    model_config = ConfigDict(extra="ignore")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None):  # pragma: no cover - trivial
        return cls.model_validate(data or {})


# ----- Composition + components -----


class MCPServerConfig(_YamlModel):
    name: str
    config: str = ""  # path to config file


class HooksConfig(_YamlModel):
    session_start: list[str] = Field(default_factory=list)
    session_end: list[str] = Field(default_factory=list)
    task_start: list[str] = Field(default_factory=list)
    task_complete: list[str] = Field(default_factory=list)
    task_failed: list[str] = Field(default_factory=list)
    error: list[str] = Field(default_factory=list)

    def get_scripts(self, event: LifecycleEvent) -> list[str]:
        return getattr(self, event.value, [])


class Composition(_YamlModel):
    traits: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    mcp: list[MCPServerConfig] = Field(default_factory=list)


class ComponentConfig(_YamlModel):
    """Parsed config.yaml for a trait or role."""

    name: str = ""
    composition: Composition = Field(default_factory=Composition)
    injection: str = ""
    priorities: list[str] = Field(default_factory=list)
    source_path: Path | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> ComponentConfig:
        data = yaml.safe_load(path.read_text()) or {}
        return cls.model_validate({**data, "source_path": path, "name": data.get("name") or path.parent.name})


class RuleMetadata(_YamlModel):
    name: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> RuleMetadata:
        if not path.exists():
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})


# Git hook events recognized by the framework. Skills declare their hooks
# under one of these keys in metadata.yaml's `git_hooks:` block. The keys
# match git's actual hook filenames so the dispatcher path is unambiguous.
GIT_HOOK_EVENTS: tuple[str, ...] = (
    "pre-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-push",
    "pre-rebase",
)


class SkillMetadata(_YamlModel):
    """Parsed metadata.yaml for a skill.

    `git_hooks` lets a skill declare scripts that should be installed into
    the project's `.githooks/<event>.d/` during composition. Keys are git
    hook event names (see GIT_HOOK_EVENTS); values are lists of script
    paths relative to the skill directory.
    """

    name: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    pattern: str = ""
    git_hooks: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_git_hooks(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = data.get("git_hooks") or {}
        if not isinstance(raw, dict):
            data["git_hooks"] = {}
            return data
        normalized: dict[str, list[str]] = {}
        for ev, scripts in raw.items():
            if not isinstance(scripts, list):
                continue
            key = str(ev).replace("_", "-")
            if key in GIT_HOOK_EVENTS:
                normalized[key] = [str(s) for s in scripts]
            # Unknown events silently skipped — surfaces upstream via tests.
        data["git_hooks"] = normalized
        return data

    @classmethod
    def from_yaml(cls, path: Path) -> SkillMetadata:
        if not path.exists():
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})


# ----- Overlays + feedback config -----


class OverlayConfig(_YamlModel):
    """Per-role customization overlay (add/remove components).

    Wire format nests add/remove sections (``add: {traits: [...], ...}``) while
    the in-memory shape is flat. ``from_dict`` / ``to_dict`` bridge the two.
    """

    add_traits: list[str] = Field(default_factory=list)
    add_rules: list[str] = Field(default_factory=list)
    add_skills: list[str] = Field(default_factory=list)
    remove_traits: list[str] = Field(default_factory=list)
    remove_rules: list[str] = Field(default_factory=list)
    remove_skills: list[str] = Field(default_factory=list)
    injection_append: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> OverlayConfig:
        if not data:
            return cls()
        add = data.get("add") or {}
        remove = data.get("remove") or {}
        return cls(
            add_traits=add.get("traits", []),
            add_rules=add.get("rules", []),
            add_skills=add.get("skills", []),
            remove_traits=remove.get("traits", []),
            remove_rules=remove.get("rules", []),
            remove_skills=remove.get("skills", []),
            injection_append=data.get("injection_append", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        add = {k: v for k, v in (
            ("traits", self.add_traits),
            ("rules", self.add_rules),
            ("skills", self.add_skills),
        ) if v}
        if add:
            d["add"] = add
        remove = {k: v for k, v in (
            ("traits", self.remove_traits),
            ("rules", self.remove_rules),
            ("skills", self.remove_skills),
        ) if v}
        if remove:
            d["remove"] = remove
        if self.injection_append:
            d["injection_append"] = self.injection_append
        return d

    @computed_field
    @property
    def is_empty(self) -> bool:
        return not any([
            self.add_traits, self.add_rules, self.add_skills,
            self.remove_traits, self.remove_rules, self.remove_skills,
            self.injection_append,
        ])


class SmartThreshold(_YamlModel):
    min_turns: int = 5
    min_tool_calls: int = 10


class ReminderConfig(_YamlModel):
    enabled: bool = True
    max_skipped: int = 5
    window_days: int = 14


class SessionRetroConfig(_YamlModel):
    policy: FeedbackPolicy = FeedbackPolicy.SMART
    smart_threshold: SmartThreshold = Field(default_factory=SmartThreshold)
    background: bool = True
    mode: str = "programmatic"
    reminder: ReminderConfig = Field(default_factory=ReminderConfig)


class JudgeConfig(_YamlModel):
    policy: JudgePolicy = JudgePolicy.MANUAL


class FeedbackConfig(_YamlModel):
    session_retro: SessionRetroConfig = Field(default_factory=SessionRetroConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)

    @property
    def is_default(self) -> bool:
        return self == FeedbackConfig()


# ----- ProjectConfig -----


class ProjectConfig(_YamlModel):
    """ai-hats.yaml — unified project configuration.

    Sections:
      - Project: provider, library_paths
      - Role: active_role, default_role, customizations
      - Feedback: session_retro, judge
      - Meta: schema_version (2 = current)
    """

    provider: str = "gemini"
    default_role: str = ""
    active_role: str = ""
    schema_version: int = 2
    library_paths: list[str] = Field(default_factory=list)
    customizations: dict[str, OverlayConfig] = Field(default_factory=dict)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)
    manage_gitignore: bool = True
    task_prefix: str = "TASK"

    @model_validator(mode="before")
    @classmethod
    def _coerce_customizations(cls, data: Any) -> Any:
        """Customizations arrive as nested dicts; route each through OverlayConfig.from_dict."""
        if isinstance(data, dict) and data.get("customizations"):
            data["customizations"] = {
                role: OverlayConfig.from_dict(overlay) if isinstance(overlay, dict) else overlay
                for role, overlay in data["customizations"].items()
            }
        return data

    @classmethod
    def from_yaml(cls, path: Path) -> ProjectConfig:
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        if data.get("schema_version", 1) < 2:
            data = _migrate_v1_to_v2(path, data)
        return cls.model_validate(data)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": 2,
            "provider": self.provider,
            "library_paths": self.library_paths,
            "active_role": self.active_role,
            "default_role": self.default_role,
        }
        live_customs = {
            name: overlay.to_dict()
            for name, overlay in self.customizations.items()
            if not overlay.is_empty
        }
        if live_customs:
            d["customizations"] = live_customs
        if not self.feedback.is_default:
            d["feedback"] = self.feedback.to_dict()
        if not self.manage_gitignore:
            d["manage_gitignore"] = False
        if self.task_prefix != "TASK":
            d["task_prefix"] = self.task_prefix
        return d

    def save(self, path: Path) -> None:
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)

    @staticmethod
    def validate_task_prefix(prefix: str) -> str:
        """Normalize and validate a task-id prefix. Raises ValueError if invalid."""
        import re as _re

        if not isinstance(prefix, str):
            raise ValueError("task_prefix must be a string")
        cleaned = prefix.strip()
        if not _re.fullmatch(r"[A-Z][A-Z0-9]*", cleaned):
            raise ValueError(
                f"Invalid task_prefix: {prefix!r}. "
                "Must match [A-Z][A-Z0-9]* (uppercase letter/digit, starts with letter)."
            )
        return cleaned

    @classmethod
    def resolve_task_prefix(cls, project_dir: Path, config_path: Path) -> str:
        """Return the task-id prefix for `project_dir`, persisting an auto-detected
        value for legacy projects so we only pay the detection cost once.

        Precedence:
          1. Explicit `task_prefix` in ai-hats.yaml.
          2. Auto-detect from existing `.agent/backlog/tasks/<PREFIX>-NNN/` dirs —
             persisted to yaml if yaml exists, so subsequent runs are O(1).
          3. Default "TASK" for greenfield projects.
        """
        raw: dict[str, Any] = {}
        if config_path.exists():
            raw = yaml.safe_load(config_path.read_text()) or {}
            if isinstance(raw.get("task_prefix"), str) and raw["task_prefix"].strip():
                return raw["task_prefix"].strip()

        detected = cls._detect_prefix_from_tasks(project_dir)
        if detected and config_path.exists():
            # Persist the detected prefix so legacy repos don't re-detect every call.
            raw["task_prefix"] = detected
            with open(config_path, "w") as f:
                yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
            return detected
        if detected:
            return detected
        return "TASK"

    @staticmethod
    def _detect_prefix_from_tasks(project_dir: Path) -> str | None:
        """Return the common prefix of existing task dirs, or None if ambiguous/empty."""
        import re as _re

        tasks_dir = project_dir / ".agent" / "backlog" / "tasks"
        if not tasks_dir.is_dir():
            return None
        prefixes: set[str] = set()
        for d in tasks_dir.iterdir():
            if not d.is_dir():
                continue
            m = _re.match(r"^([A-Z][A-Z0-9]*)-\d+$", d.name)
            if m:
                prefixes.add(m.group(1))
        if len(prefixes) == 1:
            return prefixes.pop()
        return None


def _migrate_v1_to_v2(yaml_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Auto-migrate schema v1 → v2: merge profile.json into ai-hats.yaml.

    Runs once when a v1 ai-hats.yaml is loaded. Merges active_role, provider,
    and feedback from adjacent profile.json (if present), writes the unified
    YAML, and renames profile.json to profile.json.bak.
    """
    profile_path = yaml_path.parent / "profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text())
            if profile.get("provider"):
                data["provider"] = profile["provider"]
            data["active_role"] = profile.get("active_role", "")
            if "feedback" in profile:
                data["feedback"] = profile["feedback"]
            profile_path.rename(profile_path.with_suffix(".json.bak"))
        except (json.JSONDecodeError, OSError):
            pass  # corrupt profile.json — skip, use YAML as-is

    data["schema_version"] = 2
    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    logger.info("Migrated profile.json → ai-hats.yaml (schema v2)")
    return data


# ----- Task cards -----


class WorkLogEntry(_YamlModel):
    """Single work log entry with timestamp and session tracking."""

    timestamp: str = ""
    message: str = ""


class TaskCard(_YamlModel):
    """YAML task card for state machine.

    Unknown YAML keys are captured into ``extras`` and round-tripped verbatim
    on save. This guards against silent data loss when callers add new fields
    (e.g. ``acceptance_criteria``) that aren't part of the typed schema.
    """

    #: typed fields recognized by from_dict / to_dict; everything else → extras
    _KNOWN_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "id", "title", "state", "description", "priority",
        "assignee", "reviewer", "role", "parent_task", "subtasks",
        "tags", "work_log", "final_state", "resolution",
        "created", "updated", "completed_at",
    })

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

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def resolve_namespace(name: str) -> str:
    """Convert namespace notation (dev::python) to filesystem path (dev/python)."""
    return name.replace("::", "/")
